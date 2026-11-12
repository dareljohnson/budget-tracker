"""Parser for the Regions Bank checking-statement template.

The parser is pure: it consumes positioned words (as produced by a PDF text
extractor) and returns a validated :class:`~budget_tracker.statements.model.ParsedStatement`.
"""

import re
from collections.abc import Sequence
from datetime import date, datetime

from budget_tracker.statements.model import (
    ParsedStatement,
    ParsedTransaction,
    StatementValidationError,
    UnsupportedStatementError,
    Word,
)

_AMOUNT_RE = re.compile(r"^\$?-?\d{1,3}(?:,\d{3})*\.\d{2}$")
_DATE_RE = re.compile(r"^(\d{2})/(\d{2})$")
_PERIOD_RE = re.compile(r"(\w+ \d{1,2}, \d{4}) through (\w+ \d{1,2}, \d{4})")
_ACCOUNT_NUMBER_RE = re.compile(r"^\d{2,}$")

_HEADINGS = {
    "DEPOSITS & CREDITS": "deposits",
    "WITHDRAWALS": "withdrawals",
    "WITHDRAWALS (CONTINUED)": "withdrawals",
    "FEES": "fees",
    "FEES (CONTINUED)": "fees",
    "DAILY BALANCE": "stop",
}

_LINE_TOLERANCE = 2.0


def _collapsed(line: Sequence[Word]) -> str:
    return " ".join(" ".join(word.text for word in line).split())


def _group_lines(words: Sequence[Word]) -> list[list[Word]]:
    """Group words into visual lines by ``top`` (tolerance 2 pt), each sorted by ``x0``."""
    lines: list[list[Word]] = []
    for word in sorted(words, key=lambda item: item.top):
        if not lines or abs(word.top - lines[-1][0].top) > _LINE_TOLERANCE:
            lines.append([word])
        else:
            lines[-1].append(word)
    for line in lines:
        line.sort(key=lambda item: item.x0)
    return lines


def _heading(line: Sequence[Word]) -> str | None:
    """Return the section a heading line opens, or ``None`` for ordinary lines."""
    if not line or line[0].x0 < 150:
        return None
    return _HEADINGS.get(_collapsed(line).upper())


def _cents(text: str) -> int:
    raw = text.replace("$", "").replace(",", "")
    negative = raw.startswith("-")
    if negative:
        raw = raw[1:]
    whole, fraction = raw.split(".")
    value = int(whole) * 100 + int(fraction)
    return -value if negative else value


def _find_institution(lines: Sequence[Sequence[Word]]) -> str:
    for line in lines:
        if "REGIONS BANK" in _collapsed(line).upper():
            return "Regions Bank"
    raise UnsupportedStatementError("Not a Regions Bank statement")


def _find_account_label(lines: Sequence[Sequence[Word]]) -> str:
    for line in lines:
        if any(word.text.upper() == "CHECKING" for word in line):
            return _collapsed(line)
    raise UnsupportedStatementError("Account label not found")


def _find_period(lines: Sequence[Sequence[Word]]) -> tuple[date, date]:
    for line in lines:
        match = _PERIOD_RE.search(_collapsed(line))
        if match is None:
            continue
        try:
            start = datetime.strptime(match.group(1), "%B %d, %Y").date()
            end = datetime.strptime(match.group(2), "%B %d, %Y").date()
        except ValueError as exc:
            raise UnsupportedStatementError("Unrecognized statement period") from exc
        return start, end
    raise UnsupportedStatementError("Statement period not found")


def _find_account_last4(lines: Sequence[Sequence[Word]]) -> str | None:
    all_words = [word for line in lines for word in line]
    for line in lines:
        texts = {word.text.upper() for word in line}
        if "ACCOUNT" not in texts or "#" not in texts:
            continue
        anchor = line[0].top
        candidates = sorted(
            (
                word
                for word in all_words
                if anchor < word.top <= anchor + 25 and _ACCOUNT_NUMBER_RE.match(word.text)
            ),
            key=lambda word: (word.top, word.x0),
        )
        if candidates:
            return candidates[0].text[-4:]
        return None
    return None


def _summary_amounts(lines: Sequence[Sequence[Word]], first_heading_top: float) -> list[int]:
    amounts = [
        word
        for line in lines
        if line[0].top < first_heading_top
        for word in line
        if 180 <= word.x0 <= 300 and _AMOUNT_RE.match(word.text)
    ]
    amounts.sort(key=lambda word: (word.top, word.x0))
    if len(amounts) != 7:
        raise UnsupportedStatementError("Seven summary amounts not found")
    return [_cents(word.text) for word in amounts]


def _transaction_date(text: str, period_start: date) -> date:
    match = _DATE_RE.match(text)
    assert match is not None
    month, day = int(match.group(1)), int(match.group(2))
    year = period_start.year if month >= period_start.month else period_start.year + 1
    try:
        return date(year, month, day)
    except ValueError as exc:
        raise StatementValidationError([f"Invalid transaction date {text!r}"]) from exc


def _parse_transactions(
    lines: Sequence[Sequence[Word]], period_start: date
) -> tuple[list[tuple[str, ParsedTransaction]], list[str]]:
    """Walk the transaction area and return ``(section, transaction)`` pairs plus parse errors."""
    section: str | None = None
    current: dict | None = None
    parsed: list[tuple[str, ParsedTransaction]] = []
    errors: list[str] = []

    def finalize() -> None:
        nonlocal current
        if current is None:
            return
        if current["amount_cents"] is None:
            errors.append(f"Transaction dated {current['date_text']} has no amount")
        else:
            parsed.append(
                (
                    current["section"],
                    ParsedTransaction(
                        occurred_on=current["occurred_on"],
                        description=" ".join(current["description"]),
                        amount_cents=current["amount_cents"],
                        direction="income" if current["section"] == "deposits" else "expense",
                    ),
                )
            )
        current = None

    for line in lines:
        heading = _heading(line)
        if heading == "stop":
            finalize()
            break
        if heading is not None:
            finalize()
            section = heading
            continue
        if section is None:
            continue
        for word in line:
            if _DATE_RE.match(word.text):
                finalize()
                current = {
                    "date_text": word.text,
                    "occurred_on": _transaction_date(word.text, period_start),
                    "section": section,
                    "description": [],
                    "amount_cents": None,
                }
            elif _AMOUNT_RE.match(word.text):
                if current is not None and current["amount_cents"] is None:
                    current["amount_cents"] = _cents(word.text)
            elif current is not None and current["amount_cents"] is None:
                current["description"].append(word.text)

    finalize()
    return parsed, errors


def parse_statement(words: Sequence[Word]) -> ParsedStatement:
    """Parse positioned words from a Regions checking statement.

    Raises :class:`UnsupportedStatementError` for another statement template and
    :class:`StatementValidationError` when the parsed transactions do not match the
    statement's own summary box.
    """
    lines = _group_lines(words)

    institution = _find_institution(lines)
    account_label = _find_account_label(lines)
    period_start, period_end = _find_period(lines)
    account_last4 = _find_account_last4(lines)

    if not any(_heading(line) is not None for line in lines):
        raise UnsupportedStatementError("No statement sections found")
    first_heading_top = next(line[0].top for line in lines if _heading(line) is not None)

    (
        beginning_cents,
        deposits_cents,
        withdrawals_cents,
        fees_cents,
        _automatic_transfers_cents,
        _checks_cents,
        ending_cents,
    ) = _summary_amounts(lines, first_heading_top)

    parsed, errors = _parse_transactions(lines, period_start)
    if errors:
        raise StatementValidationError(errors)

    parsed_deposits = sum(transaction.amount_cents for section, transaction in parsed if section == "deposits")
    parsed_withdrawals = sum(transaction.amount_cents for section, transaction in parsed if section == "withdrawals")
    parsed_fees = sum(transaction.amount_cents for section, transaction in parsed if section == "fees")

    if parsed_deposits != deposits_cents:
        errors.append(f"Deposits: parsed {parsed_deposits} != statement {deposits_cents}")
    if parsed_withdrawals != withdrawals_cents:
        errors.append(f"Withdrawals: parsed {parsed_withdrawals} != statement {withdrawals_cents}")
    if parsed_fees != fees_cents:
        errors.append(f"Fees: parsed {parsed_fees} != statement {fees_cents}")
    computed_ending = beginning_cents + deposits_cents - withdrawals_cents - fees_cents
    if computed_ending != ending_cents:
        errors.append(
            f"Ending balance: {beginning_cents} + {deposits_cents} - {withdrawals_cents} - {fees_cents} "
            f"= {computed_ending} != statement {ending_cents}"
        )
    if errors:
        raise StatementValidationError(errors)

    return ParsedStatement(
        institution=institution,
        account_label=account_label,
        account_last4=account_last4,
        period_start=period_start,
        period_end=period_end,
        beginning_cents=beginning_cents,
        deposits_cents=deposits_cents,
        withdrawals_cents=withdrawals_cents,
        fees_cents=fees_cents,
        ending_cents=ending_cents,
        transactions=[transaction for _section, transaction in parsed],
    )
