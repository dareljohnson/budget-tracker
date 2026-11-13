"""Extract a statement's own summary figures and transaction table.

Summary lines are classified by their label alone; amounts stay exact. An explicit total wins over
the decomposed detail lines some statements print instead, and fees are kept apart from withdrawals.
Transaction rows are reconstructed from column geometry: a date starts a row, the remaining tokens
are placed in the nearest amount column, and direction is taken from the columns, the amount's own
sign, the running balance, or the enclosing section heading -- never guessed.
"""

import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date

from budget_tracker.statements.labels import detail_family, family_of, label_text, normalize_label
from budget_tracker.statements.lines import collapsed, group_lines
from budget_tracker.statements.model import StatementValidationError, Word
from budget_tracker.statements.numbers import parse_amount, parse_date

_NEXT_LINE_TOLERANCE = 12.0

_TOTAL_RE = re.compile(r"\b(?:sub)?totals?\b", re.IGNORECASE)

COLUMN_TOLERANCE = 25.0

_HEADER_ROLES = {
    "DATE": "date",
    "DESCRIPTION": "description",
    "AMOUNT": "amount",
    "DEBIT": "debit",
    "CREDIT": "credit",
    "BALANCE": "balance",
}
_AMOUNT_ROLES = ("amount", "debit", "credit", "balance")
_SECTION_FAMILIES = ("deposits", "withdrawals", "fees")
_SECTION_HEADINGS = {
    "DEPOSITS": "deposits",
    "DEPOSITS CREDITS": "deposits",
    "DEPOSITS AND CREDITS": "deposits",
    "CREDITS": "deposits",
    "WITHDRAWALS": "withdrawals",
    "WITHDRAWALS DEBITS": "withdrawals",
    "WITHDRAWALS AND DEBITS": "withdrawals",
    "DEBITS": "withdrawals",
    "FEES": "fees",
    "SERVICE CHARGES": "fees",
}

_FOOTER_RE = re.compile(r"\bpage\s+\d+\s+of\s+\d+\b|\bcontinued\b", re.IGNORECASE)

# A date is usable without a statement anchor only when it carries its own year.
_YEARED_DATE_RE = re.compile(
    r"^(?:\d{1,2}/\d{1,2}/(?:\d{2}|\d{4})|"
    r"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)[A-Za-z]*\.?\s+\d{1,2},\s+\d{4})$",
    re.IGNORECASE,
)
_SENTINEL_ANCHOR = date(2000, 1, 1)


@dataclass(frozen=True, slots=True)
class SummaryFigures:
    beginning_cents: int | None = None
    deposits_cents: int | None = None
    withdrawals_cents: int | None = None
    fees_cents: int | None = None
    ending_cents: int | None = None


def find_summary(words: Sequence[Word], block_end_top: float) -> SummaryFigures:
    """Return the summary figures printed above ``block_end_top``.

    Each line that looks like a summary label contributes the nearest amount on its own line, or
    else the nearest amount on the next line within 12 points when that next line is amount-only
    (it carries no label text). A next line with label words is classified on its own; borrowing
    its amount would double-count a wrapped label or steal a neighbouring summary figure. A label
    with no amount nearby is ignored. An explicit total for a family overrides that family's detail
    lines; otherwise the detail lines are summed.
    """
    lines = [line for line in group_lines(words) if line[0].top < block_end_top]
    totals: dict[str, int] = {}
    details: dict[str, int] = {}
    for index, line in enumerate(lines):
        label = label_text(collapsed(line))
        label_words = [word for word in line if _amount_cents(word) is None]
        if not label or not label_words:
            continue
        cents = _nearest_amount(line, _label_end(label_words))
        if cents is None and index + 1 < len(lines):
            following = lines[index + 1]
            if (
                following[0].top - line[0].top <= _NEXT_LINE_TOLERANCE
                and not label_text(collapsed(following))
            ):
                cents = _nearest_amount(following, _label_end(label_words))
        if cents is None:
            continue
        family = _classify(label)
        if family is None:
            continue
        if _is_explicit_total(label):
            totals.setdefault(family, cents)
        else:
            details[family] = details.get(family, 0) + cents
    return SummaryFigures(
        beginning_cents=_figure(totals, details, "beginning"),
        deposits_cents=_figure(totals, details, "deposits"),
        withdrawals_cents=_figure(totals, details, "withdrawals"),
        fees_cents=_figure(totals, details, "fees"),
        ending_cents=_figure(totals, details, "ending"),
    )


def _classify(label: str) -> str | None:
    """Classify a summary label.

    Explicit totals use family matching only. Every other line is tried with the detail keywords
    first, so a decomposed withdrawal line that names service charges is not read as fees.
    """
    if _is_explicit_total(label):
        return family_of(label)
    return detail_family(label) or family_of(label)


def _is_explicit_total(label: str) -> bool:
    return _TOTAL_RE.search(label) is not None


def _figure(totals: dict[str, int], details: dict[str, int], family: str) -> int | None:
    if family in totals:
        return totals[family]
    return details.get(family)


def _nearest_amount(line: Sequence[Word], label_end: float) -> int | None:
    """Return the amount on ``line`` whose start is nearest ``label_end``, if any."""
    candidates = [
        (abs(word.x0 - label_end), cents)
        for word in line
        if (cents := _amount_cents(word)) is not None
    ]
    if not candidates:
        return None
    return min(candidates, key=lambda candidate: candidate[0])[1]


def _amount_cents(word: Word) -> int | None:
    amount = parse_amount(word.text)
    return None if amount is None else amount.cents


def _label_end(label_words: Sequence[Word]) -> float:
    return max(word.x1 if word.x1 is not None else word.x0 for word in label_words)


@dataclass(frozen=True, slots=True)
class TableRow:
    occurred_on: date
    description: str
    amount_cents: int
    direction: str
    balance_cents: int | None


def find_transactions(
    words: Sequence[Word],
    *,
    anchor: date | None,
    anchor_on_end: bool,
    beginning_cents: int | None = None,
) -> list[TableRow]:
    """Reconstruct transaction rows from the statement's column geometry.

    The first header line (a ``DATE`` token plus a description token plus an amount token) supplies
    the column anchors. Each later line whose date-column token parses as a date starts a row; other
    lines extend the current row's description unless they are a repeated header, a page footer, a
    totals/summary line, or a section heading. Tokens are assigned to the nearest amount column
    within :data:`COLUMN_TOLERANCE`. A row's direction comes from its debit/credit column, the sign
    or marker of its single amount, the change in its running balance, or its enclosing section --
    in that order; when none applies the direction is ``""``. Rows with no amount are not rows.

    A yearless date is resolved against ``anchor``; when ``anchor`` is ``None`` it is rejected
    rather than guessed.
    """
    lines = group_lines(words)
    header = _find_header(lines)
    if header is None:
        return []
    header_index, anchors = header
    date_anchor = anchors["date"]
    amount_anchors = {role: x for role, x in anchors.items() if role in _AMOUNT_ROLES}

    rows: list[TableRow] = []
    current: dict | None = None
    section: str | None = None
    running_balance = beginning_cents

    def finalize() -> None:
        nonlocal current, running_balance
        if current is None:
            return
        row = _build_row(current, running_balance, section)
        if row is not None:
            rows.append(row)
            if row.balance_cents is not None:
                running_balance = row.balance_cents
        current = None

    for line in lines[header_index + 1 :]:
        text = collapsed(line)
        if _header_anchors(line) is not None:
            continue
        found = _row_date(line, date_anchor, anchor, anchor_on_end)
        if found is not None:
            finalize()
            date_word, occurred_on = found
            current = {"occurred_on": occurred_on, "description": [], "columns": {}}
            _collect_row_tokens(line, current, date_word, amount_anchors)
            continue
        heading = _heading_family(text)
        if heading is not None:
            finalize()
            section = heading
            continue
        if _FOOTER_RE.search(text):
            continue
        if _non_transaction_line(text):
            continue
        if current is not None:
            for word in line:
                if parse_amount(word.text) is None:
                    current["description"].append(word.text)

    finalize()
    return rows


def _find_header(lines: Sequence[Sequence[Word]]) -> tuple[int, dict[str, float]] | None:
    for index, line in enumerate(lines):
        anchors = _header_anchors(line)
        if anchors is not None:
            return index, anchors
    return None


def _header_anchors(line: Sequence[Word]) -> dict[str, float] | None:
    """Return the column anchors of a header line, or ``None`` for a non-header line."""
    anchors: dict[str, float] = {}
    for word in line:
        role = _HEADER_ROLES.get(word.text.upper())
        if role is not None and role not in anchors:
            anchors[role] = _centre(word)
    if (
        "date" in anchors
        and "description" in anchors
        and any(role in anchors for role in _AMOUNT_ROLES)
    ):
        return anchors
    return None


def _row_date(
    line: Sequence[Word], date_anchor: float, anchor: date | None, anchor_on_end: bool
) -> tuple[Word, date] | None:
    for word in line:
        if abs(_centre(word) - date_anchor) > COLUMN_TOLERANCE:
            continue
        parsed = _parse_row_date(word.text, anchor, anchor_on_end)
        if parsed is not None:
            return word, parsed
    return None


def _parse_row_date(text: str, anchor: date | None, anchor_on_end: bool) -> date | None:
    probe = anchor if anchor is not None else _SENTINEL_ANCHOR
    parsed = parse_date(text, probe, anchor_on_end=anchor_on_end)
    if parsed is None:
        return None
    if anchor is None and _YEARED_DATE_RE.match(text.strip()) is None:
        raise StatementValidationError([f"Transaction date {text!r} has no year"])
    return parsed


def _collect_row_tokens(
    line: Sequence[Word],
    current: dict,
    date_word: Word,
    amount_anchors: dict[str, float],
) -> None:
    for word in line:
        if word is date_word:
            continue
        amount = parse_amount(word.text)
        if amount is not None:
            role = _nearest_role(_centre(word), amount_anchors)
            if role is not None:
                current["columns"][role] = amount
                continue
        current["description"].append(word.text)


def _centre(word: Word) -> float:
    return (word.x0 + word.x1) / 2 if word.x1 is not None else word.x0


def _nearest_role(centre: float, anchors: dict[str, float]) -> str | None:
    if not anchors:
        return None
    role, x = min(anchors.items(), key=lambda item: abs(item[1] - centre))
    return role if abs(x - centre) <= COLUMN_TOLERANCE else None


def _heading_family(text: str) -> str | None:
    """Return the section a heading line opens, or ``None`` for an ordinary line."""
    family = family_of(text)
    if family in _SECTION_FAMILIES:
        return family
    normalized = normalize_label(text)
    if normalized.endswith(" CONTINUED"):
        normalized = normalized[: -len(" CONTINUED")].strip()
    return _SECTION_HEADINGS.get(normalized)


def _non_transaction_line(text: str) -> bool:
    if _TOTAL_RE.search(text):
        return True
    return family_of(text) is not None


def _build_row(current: dict, previous_balance: int | None, section: str | None) -> TableRow | None:
    columns = current["columns"]
    balance = columns.get("balance")
    # A balance keeps its sign: an overdrawn account is a real running-balance step.
    balance_cents = balance.cents if balance is not None else None
    resolved = _direction(columns, balance_cents, previous_balance, section)
    if resolved is None:
        return None
    amount_cents, direction = resolved
    return TableRow(
        occurred_on=current["occurred_on"],
        description=" ".join(current["description"]),
        amount_cents=amount_cents,
        direction=direction,
        balance_cents=balance_cents,
    )


def _direction(
    columns: dict,
    balance_cents: int | None,
    previous_balance: int | None,
    section: str | None,
) -> tuple[int, str] | None:
    debit = columns.get("debit")
    credit = columns.get("credit")
    if debit is not None and credit is None:
        return abs(debit.cents), "expense"
    if credit is not None and debit is None:
        return abs(credit.cents), "income"
    if debit is not None and credit is not None:
        return abs(debit.cents) + abs(credit.cents), ""

    amount = columns.get("amount")
    if amount is None:
        return None
    magnitude = abs(amount.cents)
    if amount.marker == "CR":
        return magnitude, "income"
    if amount.marker == "DR" or amount.cents < 0:
        return magnitude, "expense"
    if balance_cents is not None and previous_balance is not None:
        delta = balance_cents - previous_balance
        if delta < 0:
            return magnitude, "expense"
        if delta > 0:
            return magnitude, "income"
    if section == "deposits":
        return magnitude, "income"
    if section in ("withdrawals", "fees"):
        return magnitude, "expense"
    return magnitude, ""
