"""Extract a statement's own summary figures and transaction table.

Summary lines are classified by their label alone; amounts stay exact. An explicit total wins over
the decomposed detail lines some statements print instead, and fees are kept apart from withdrawals.
Transaction rows are reconstructed from column geometry: a date starts a row, the remaining tokens
are placed in the nearest amount column, and direction is taken from the columns, the amount's own
sign, the running balance, or the enclosing section heading -- never guessed. The assembled parser
refuses credit-card, non-USD and unrecognizable documents, and imports a statement only when its
rows reconcile to the figures the statement itself prints.
"""

import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from typing import cast

from budget_tracker.statements.labels import (
    detail_family,
    family_of,
    has_non_usd_marker,
    institution_of,
    is_credit_card_marker,
    label_text,
    normalize_label,
)
from budget_tracker.statements.lines import collapsed, group_lines
from budget_tracker.statements.model import (
    ParsedStatement,
    ParsedTransaction,
    StatementValidationError,
    UnsupportedStatementError,
    Word,
)
from budget_tracker.statements.numbers import parse_amount, parse_date

_NEXT_LINE_TOLERANCE = 12.0

_TOTAL_RE = re.compile(r"\b(?:sub)?totals?\b", re.IGNORECASE)
_ENDING_BALANCE_RE = re.compile(r"\b(?:ending|closing)\s+balance\b", re.IGNORECASE)
_NON_FLOW_RE = re.compile(
    r"\b(?:average|daily\s+balance|interest\s+rate|number\s+of|days\s+in)\b", re.IGNORECASE
)
_TABLE_END_RE = re.compile(
    r"\b(?:daily\s+balance|average\s+balance|end\s+of\s+statement)\b", re.IGNORECASE
)

COLUMN_TOLERANCE = 25.0

_HEADER_ROLES = {
    "DATE": "date",
    "DESCRIPTION": "description",
    "DETAILS": "description",
    "TRANSACTION": "description",
    "REFERENCE": "description",
    "MEMO": "description",
    "AMOUNT": "amount",
    "DEBIT": "debit",
    "WITHDRAWAL": "debit",
    "WITHDRAWALS": "debit",
    "CHARGES": "debit",
    "PAYMENTS": "debit",
    "CREDIT": "credit",
    "DEPOSIT": "credit",
    "DEPOSITS": "credit",
    "BALANCE": "balance",
}
# Two adjacent header words can name one role (`MONEY OUT`, `MONEY IN`).
_HEADER_PAIRS = {
    ("MONEY", "OUT"): "debit",
    ("MONEY", "IN"): "credit",
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

_UNKNOWN_INSTITUTION = "Unknown bank"
_DEFAULT_ACCOUNT_LABEL = "Statement"
_ACCOUNT_TYPE_RE = re.compile(
    r"\b(?:CHECKING|SAVINGS|CURRENT|SHARE|MONEY\s+MARKET)\b", re.IGNORECASE
)
_ACCOUNT_RE = re.compile(r"\bACCOUNT\b", re.IGNORECASE)
_CARD_RE = re.compile(r"\bCARD\b", re.IGNORECASE)
_DIGIT_RUN_RE = re.compile(r"\d{4,}")
_NON_USD_CODE_RE = re.compile(r"\b(?:GBP|EUR|CAD|AUD)\b", re.IGNORECASE)
_PERIOD_DATE = r"(?:[A-Za-z]+\s+\d{1,2},\s+\d{4}|\d{1,2}/\d{1,2}/\d{2,4})"
_PERIOD_RE = re.compile(
    rf"({_PERIOD_DATE})\s*(?:through|to|[-–—])\s*({_PERIOD_DATE})", re.IGNORECASE
)
_PERIOD_END_RE = re.compile(rf"\bfor\s+({_PERIOD_DATE})\b", re.IGNORECASE)


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

    Explicit totals use family matching only. A bare ``Fee``/``Fees`` summary line is the printed
    fee figure. Every other line is tried with the detail keywords first, so a decomposed
    withdrawal line that names service charges is not read as fees.
    """
    if _is_explicit_total(label):
        return family_of(label)
    if normalize_label(label) in ("FEE", "FEES"):
        return "fees"
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


@dataclass(frozen=True, slots=True)
class _DocumentFacts:
    institution: str
    account_label: str
    account_last4: str | None
    period_start: date | None
    period_end: date | None
    period_read: bool  # both bounds were read from the document
    end_date_read: bool  # only the end date was read


def find_transactions(
    words: Sequence[Word],
    *,
    anchor: date | None,
    anchor_on_end: bool,
    beginning_cents: int | None = None,
) -> list[TableRow]:
    """Reconstruct transaction rows from the statement's column geometry.

    The first header line (a ``DATE`` token plus a description token plus an amount token) supplies
    the column anchors. Each later line whose date-column token sequence parses as a date starts a
    row; the date may span adjacent tokens (``Jan 05``, ``January 5, 2026``), so tokens within the
    date column's tolerance are joined before parsing. A
    repeated header, page footer, totals row, or non-flow figure is skipped before section-heading
    detection, so it never opens a section or extends a description; a wrapped description word at
    the description column is not a heading. A trailing non-flow block (``DAILY BALANCE``,
    ``AVERAGE BALANCE``, ``END OF STATEMENT``) ends parsing. Other lines extend the current row's
    description. Tokens are assigned to the nearest amount column within
    :data:`COLUMN_TOLERANCE`. A row's direction comes from its debit/credit column, the sign
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
    description_anchor = anchors["description"]
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
            date_words, occurred_on = found
            current = {"occurred_on": occurred_on, "description": [], "columns": {}}
            _collect_row_tokens(line, current, date_words, amount_anchors)
            continue
        if _TABLE_END_RE.search(text):
            finalize()
            break
        if _FOOTER_RE.search(text) or _non_transaction_line(text):
            continue
        heading = _heading_family(line, description_anchor)
        if heading is not None:
            finalize()
            section = heading
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
    """Return the column anchors of a header line, or ``None`` for a non-header line.

    Adjacent tokens are consumed as a pair when the pair names a role (`MONEY OUT`, `MONEY IN`);
    the pair's anchor is the centre of the span it covers.
    """
    anchors: dict[str, float] = {}
    index = 0
    while index < len(line):
        word = line[index]
        if index + 1 < len(line):
            pair = (word.text.upper(), line[index + 1].text.upper())
            pair_role = _HEADER_PAIRS.get(pair)
            if pair_role is not None:
                if pair_role not in anchors:
                    anchors[pair_role] = _pair_centre(word, line[index + 1])
                index += 2
                continue
        role = _HEADER_ROLES.get(word.text.upper())
        if role is not None and role not in anchors:
            anchors[role] = _centre(word)
        index += 1
    if (
        "date" in anchors
        and "description" in anchors
        and any(role in anchors for role in _AMOUNT_ROLES)
    ):
        return anchors
    return None


def _pair_centre(first: Word, second: Word) -> float:
    return (first.x0 + (second.x1 if second.x1 is not None else second.x0)) / 2


def _row_date(
    line: Sequence[Word], date_anchor: float, anchor: date | None, anchor_on_end: bool
) -> tuple[list[Word], date] | None:
    """Return the date-column tokens that form a row's date, and the parsed date.

    Tokens whose centre lies within :data:`COLUMN_TOLERANCE` of the date column are joined before
    parsing, so a month-name date split into separate words (``Jan 05``, ``January 5, 2026``)
    is recognised. The tokens are returned as well so they are not appended to the description.
    """
    candidates = [
        word for word in line if abs(_centre(word) - date_anchor) <= COLUMN_TOLERANCE
    ]
    for start in range(len(candidates)):
        for end in range(len(candidates), start, -1):
            tokens = candidates[start:end]
            parsed = _parse_row_date(
                " ".join(word.text for word in tokens), anchor, anchor_on_end
            )
            if parsed is not None:
                return tokens, parsed
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
    date_words: Sequence[Word],
    amount_anchors: dict[str, float],
) -> None:
    date_ids = {id(word) for word in date_words}
    for word in line:
        if id(word) in date_ids:
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


def _heading_family(line: Sequence[Word], description_anchor: float) -> str | None:
    """Return the section a heading line opens, or ``None`` for an ordinary line.

    A heading is a narrow class: it carries no amount, is at most six words, matches a section
    family, and starts outside the description column's tolerance. A wrapped description word sits
    at the description column, so it extends the current row instead of opening a section.
    """
    if any(parse_amount(word.text) is not None for word in line) or len(line) > 6:
        return None
    if abs(_centre(line[0]) - description_anchor) <= COLUMN_TOLERANCE:
        return None
    text = collapsed(line)
    family = family_of(text)
    if family in _SECTION_FAMILIES:
        return family
    normalized = normalize_label(text)
    if normalized.endswith(" CONTINUED"):
        normalized = normalized[: -len(" CONTINUED")].strip()
    return _SECTION_HEADINGS.get(normalized)


def _non_transaction_line(text: str) -> bool:
    return any(pattern.search(text) for pattern in (_TOTAL_RE, _ENDING_BALANCE_RE, _NON_FLOW_RE))


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


def parse_generic(words: Sequence[Word], *, source: str = "text") -> ParsedStatement:
    """Parse a US checking or savings statement into a validated statement.

    Document facts are read from the summary region, the statement's own summary figures and
    transaction rows are extracted, and the rows must reconcile to those figures before anything is
    returned. Credit-card, non-USD and unrecognizable layouts are refused outright; every gate
    failure is reported together in one :class:`StatementValidationError`.
    """
    lines = group_lines(words)
    header = _find_header(lines)
    block_end_top = lines[header[0]][0].top if header is not None else float("inf")

    facts = _document_facts(lines, block_end_top)
    if header is None:
        raise UnsupportedStatementError("Unrecognized statement layout")

    summary = find_summary(words, block_end_top)
    if facts.period_read:
        anchor, anchor_on_end = facts.period_start, False
    elif facts.end_date_read:
        anchor, anchor_on_end = facts.period_end, True
    else:
        anchor, anchor_on_end = None, False

    rows = find_transactions(
        words,
        anchor=anchor,
        anchor_on_end=anchor_on_end,
        beginning_cents=summary.beginning_cents,
    )
    period_start, period_end = _resolved_period(facts, rows)

    errors = _validate(rows, summary, facts, period_start, period_end, "balance" in header[1])
    if errors:
        raise StatementValidationError(errors)

    # The gate above refuses a document with no transactions and no readable period, so both
    # bounds are present for every statement that reaches here.
    if period_start is None or period_end is None:
        raise UnsupportedStatementError("Unrecognized statement layout")
    return ParsedStatement(
        institution=facts.institution,
        account_label=facts.account_label,
        account_last4=facts.account_last4,
        period_start=period_start,
        period_end=period_end,
        beginning_cents=cast(int, summary.beginning_cents),
        deposits_cents=cast(int, summary.deposits_cents),
        withdrawals_cents=cast(int, summary.withdrawals_cents),
        fees_cents=summary.fees_cents if summary.fees_cents is not None else 0,
        ending_cents=cast(int, summary.ending_cents),
        transactions=[
            ParsedTransaction(
                occurred_on=row.occurred_on,
                description=row.description,
                amount_cents=row.amount_cents,
                direction=row.direction,
            )
            for row in rows
        ],
        source=source,
    )


def _document_facts(lines: Sequence[Sequence[Word]], block_end_top: float) -> _DocumentFacts:
    """Read header facts and refuse documents outside the importer's scope.

    Only the summary region above ``block_end_top`` is inspected, so a checking statement whose
    transaction rows mention a card brand or a foreign currency is not misclassified.
    """
    region = [line for line in lines if line[0].top < block_end_top]
    _refuse_out_of_scope(region)
    institution = next(
        (name for line in region if (name := institution_of(collapsed(line))) is not None),
        _UNKNOWN_INSTITUTION,
    )
    account_label = next(
        (collapsed(line) for line in region if _ACCOUNT_TYPE_RE.search(collapsed(line))),
        _DEFAULT_ACCOUNT_LABEL,
    )
    period_start, period_end, period_read, end_date_read = _read_period(region)
    return _DocumentFacts(
        institution=institution,
        account_label=account_label,
        account_last4=_account_last4(region),
        period_start=period_start,
        period_end=period_end,
        period_read=period_read,
        end_date_read=end_date_read,
    )


def _refuse_out_of_scope(lines: Sequence[Sequence[Word]]) -> None:
    """Raise for credit-card and non-USD documents.

    A standalone ISO code is a document-level declaration even when the header does not print the
    word ``currency``; ``has_non_usd_marker`` only accepts codes in that declarative form, so any
    code token anywhere in the region is refused here instead.
    """
    for line in lines:
        text = collapsed(line)
        if is_credit_card_marker(text):
            raise UnsupportedStatementError("Credit card statements are not supported")
        if has_non_usd_marker(text) or _NON_USD_CODE_RE.search(text):
            raise UnsupportedStatementError("Only US dollar statements are supported")


def _read_period(
    lines: Sequence[Sequence[Word]],
) -> tuple[date | None, date | None, bool, bool]:
    """Return ``(start, end, full_period_read, end_date_read)`` from the summary region."""
    for line in lines:
        match = _PERIOD_RE.search(collapsed(line))
        if match is None:
            continue
        start = _parse_period_date(match.group(1))
        end = _parse_period_date(match.group(2))
        if start is not None and end is not None:
            return start, end, True, False
    for line in lines:
        match = _PERIOD_END_RE.search(collapsed(line))
        if match is None:
            continue
        end = _parse_period_date(match.group(1))
        if end is not None:
            return None, end, False, True
    return None, None, False, False


def _parse_period_date(text: str) -> date | None:
    return parse_date(" ".join(text.split()), _SENTINEL_ANCHOR)


def _account_last4(lines: Sequence[Sequence[Word]]) -> str | None:
    """Return the last four digits of an account number, never of a card number."""
    for line in lines:
        text = collapsed(line)
        if _CARD_RE.search(text) or _ACCOUNT_RE.search(text) is None:
            continue
        match = _DIGIT_RUN_RE.search(text)
        if match is not None:
            return match.group()[-4:]
    return None


def _resolved_period(
    facts: _DocumentFacts, rows: Sequence[TableRow]
) -> tuple[date | None, date | None]:
    """Resolve the statement period, deriving bounds from the rows when the document omits them."""
    dates = [row.occurred_on for row in rows]
    if facts.period_read:
        return facts.period_start, facts.period_end
    if facts.end_date_read:
        return (min(dates) if dates else facts.period_end), facts.period_end
    if dates:
        return min(dates), max(dates)
    return None, None


def _validate(
    rows: Sequence[TableRow],
    summary: SummaryFigures,
    facts: _DocumentFacts,
    period_start: date | None,
    period_end: date | None,
    balance_column: bool,
) -> list[str]:
    """Return one message per failed gate rule, in the documented order."""
    errors: list[str] = []

    if not rows:
        errors.append("No transactions parsed")

    for row in rows:
        if row.amount_cents <= 0:
            errors.append(f"Transaction dated {row.occurred_on.isoformat()} has no positive amount")
        if row.direction not in ("income", "expense"):
            errors.append(
                f"Transaction dated {row.occurred_on.isoformat()} has no determined direction"
            )

    for value, name in (
        (summary.beginning_cents, "beginning balance"),
        (summary.deposits_cents, "total deposits"),
        (summary.withdrawals_cents, "total withdrawals"),
        (summary.ending_cents, "ending balance"),
    ):
        if value is None:
            errors.append(f"Statement {name} not found")

    parsed_deposits = sum(row.amount_cents for row in rows if row.direction == "income")
    parsed_withdrawals = sum(row.amount_cents for row in rows if row.direction == "expense")
    if summary.deposits_cents is not None and parsed_deposits != summary.deposits_cents:
        errors.append(f"Deposits: parsed {parsed_deposits} != statement {summary.deposits_cents}")
    if summary.withdrawals_cents is not None and parsed_withdrawals != summary.withdrawals_cents:
        errors.append(
            f"Withdrawals: parsed {parsed_withdrawals} != statement {summary.withdrawals_cents}"
        )

    if all(
        value is not None
        for value in (
            summary.beginning_cents,
            summary.deposits_cents,
            summary.withdrawals_cents,
            summary.ending_cents,
        )
    ):
        beginning = cast(int, summary.beginning_cents)
        deposits = cast(int, summary.deposits_cents)
        withdrawals = cast(int, summary.withdrawals_cents)
        ending = cast(int, summary.ending_cents)
        plain = beginning + deposits - withdrawals
        # Fees are either a memo already inside withdrawals or a separate subtraction; accept
        # whichever the document itself demonstrates.
        fees_separate = summary.fees_cents is not None and plain - summary.fees_cents == ending
        if plain != ending and not fees_separate:
            errors.append(
                f"Ending balance: {beginning} + {deposits} - {withdrawals} = {plain} "
                f"!= statement {ending}"
            )

    errors.extend(_period_errors(rows, facts, period_start, period_end))
    if balance_column:
        errors.extend(_balance_errors(rows, summary.beginning_cents))
    return errors


def _period_errors(
    rows: Sequence[TableRow],
    facts: _DocumentFacts,
    period_start: date | None,
    period_end: date | None,
) -> list[str]:
    if facts.period_read and period_start is not None and period_end is not None:
        return [
            f"Transaction dated {row.occurred_on.isoformat()} is outside the statement period "
            f"{period_start.isoformat()}..{period_end.isoformat()}"
            for row in rows
            if not period_start <= row.occurred_on <= period_end
        ]
    if facts.end_date_read and period_end is not None:
        return [
            f"Transaction dated {row.occurred_on.isoformat()} is after the statement end "
            f"{period_end.isoformat()}"
            for row in rows
            if row.occurred_on > period_end
        ]
    return []


def _balance_errors(rows: Sequence[TableRow], beginning_cents: int | None) -> list[str]:
    """Check the running-balance chain when a balance column was parsed."""
    if beginning_cents is None or not rows:
        return []
    errors: list[str] = []
    previous = beginning_cents
    for row in rows:
        if row.balance_cents is None:
            errors.append(f"Transaction dated {row.occurred_on.isoformat()} has no running balance")
            previous = None
            continue
        if row.direction in ("income", "expense") and previous is not None:
            expected = previous + (row.amount_cents if row.direction == "income" else -row.amount_cents)
            if row.balance_cents != expected:
                errors.append(
                    f"Running balance for transaction dated {row.occurred_on.isoformat()}: "
                    f"expected {expected}, parsed {row.balance_cents}"
                )
        previous = row.balance_cents
    return errors
