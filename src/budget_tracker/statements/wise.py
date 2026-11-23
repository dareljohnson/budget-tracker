"""Parse Wise USD account statements.

Wise prints a different layout from a US checking statement: the table has
``Description | Incoming | Outgoing | Amount`` columns, the date is on the line
*after* each transaction, and there is no beginning/deposits/withdrawals summary.
This adapter reconstructs the rows from column geometry and verifies them against
the printed ending balance and the running ``Amount`` column.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date

from .lines import collapsed, group_lines
from .model import (
    ParsedStatement,
    ParsedTransaction,
    StatementValidationError,
    UnsupportedStatementError,
    Word,
)
from .numbers import parse_amount, parse_date

WISE_MARKERS = ("WISE US INC", "WISE.COM")
COLUMN_TOLERANCE = 30.0
DATE_COLUMN_X = 130.0
_MAX_DATE_TOKENS = 3

_DATE = r"(?:[A-Za-z]+\s+\d{1,2},\s+\d{4}|\d{1,2}/\d{1,2}/\d{2,4})"
_PERIOD_RE = re.compile(
    rf"({_DATE})\s*(?:\[[^\]]+\])?\s*(?:-|–|—|through|to)\s*({_DATE})",
    re.IGNORECASE,
)
_CURRENCY_RE = re.compile(r"\b([A-Z]{3})\s+statement\b", re.IGNORECASE)
_ENDING_RE = re.compile(rf"\bUSD\s+on\b.*?(-?[\d,]+\.\d{{2}})\s+USD\b", re.IGNORECASE)
_CARD_PREFIX_RE = re.compile(r"^Card transaction of [\d,.]+ USD issued by\s+", re.IGNORECASE)
_DIGITS_RE = re.compile(r"\d{8,17}")
_STOP_RE = re.compile(r"^(?:Fees Summary|For any questions|Need help)", re.IGNORECASE)
_IGNORE_RE = re.compile(r"^(?:ref:|page\s+\d+\s+of\s+\d+)", re.IGNORECASE)
_HEADER_ROLES = {"DESCRIPTION", "INCOMING", "OUTGOING", "AMOUNT"}


def claims_wise(lines: Sequence[Sequence[Word]]) -> bool:
    return any(
        marker in collapsed(line).upper()
        for line in lines
        for marker in WISE_MARKERS
    )


@dataclass(slots=True)
class _WiseRow:
    occurred_on: date
    description: str
    amount_cents: int  # signed
    balance_cents: int
    direction: str


def parse_wise(words: Sequence[Word], *, source: str = "text") -> ParsedStatement:
    """Parse a Wise USD statement, or raise when the layout is not supported."""
    lines = group_lines(words)
    if not claims_wise(lines):
        raise UnsupportedStatementError("This is not a Wise statement")
    currency = _statement_currency(lines)
    if currency != "USD":
        raise UnsupportedStatementError("Only US dollar Wise statements are supported")

    header = _find_header(lines)
    if header is None:
        raise UnsupportedStatementError("Unrecognized Wise statement layout")
    header_index, anchors = header
    period_start, period_end = _read_period(lines)
    ending = _read_ending(lines)
    account_last4 = _read_account_last4(lines)
    rows = _read_rows(lines, header_index, anchors, period_start)

    if not rows:
        raise UnsupportedStatementError("No Wise transactions were found")
    # The statement prints newest first; reverse before a stable date sort so same-day
    # transactions stay in chronological running-balance order.
    rows.reverse()
    rows.sort(key=lambda row: row.occurred_on)
    _validate_rows(rows, ending)

    if period_start is None or period_end is None:
        period_start = rows[0].occurred_on
        period_end = rows[-1].occurred_on

    beginning = rows[0].balance_cents - rows[0].amount_cents
    deposits = sum(row.amount_cents for row in rows if row.direction == "income")
    withdrawals = sum(-row.amount_cents for row in rows if row.direction == "expense")
    return ParsedStatement(
        institution="Wise US Inc.",
        account_label="Wise USD",
        account_last4=account_last4,
        period_start=period_start,
        period_end=period_end,
        beginning_cents=beginning,
        deposits_cents=deposits,
        withdrawals_cents=withdrawals,
        fees_cents=0,
        ending_cents=rows[-1].balance_cents,
        transactions=[
            ParsedTransaction(
                occurred_on=row.occurred_on,
                description=row.description,
                amount_cents=abs(row.amount_cents),
                direction=row.direction,
            )
            for row in rows
        ],
        source=source,
    )


def _statement_currency(lines: Sequence[Sequence[Word]]) -> str | None:
    for line in lines:
        match = _CURRENCY_RE.search(collapsed(line))
        if match:
            return match.group(1).upper()
    return None


def _read_period(lines: Sequence[Sequence[Word]]) -> tuple[date | None, date | None]:
    for line in lines:
        match = _PERIOD_RE.search(collapsed(line))
        if match is None:
            continue
        start = parse_date(" ".join(match.group(1).split()), date(2000, 1, 1))
        end = parse_date(" ".join(match.group(2).split()), date(2000, 1, 1))
        if start is not None and end is not None:
            return start, end
    return None, None


def _read_ending(lines: Sequence[Sequence[Word]]) -> int | None:
    for line in lines:
        match = _ENDING_RE.search(collapsed(line))
        if match is not None:
            amount = parse_amount(match.group(1))
            if amount is not None:
                return amount.cents
    return None


def _read_account_last4(lines: Sequence[Sequence[Word]]) -> str | None:
    for index, line in enumerate(lines):
        if "ACCOUNT NUMBER" not in collapsed(line).upper():
            continue
        for candidate in lines[index + 1 : index + 3]:
            for word in candidate:
                if _DIGITS_RE.fullmatch(word.text):
                    return word.text[-4:]
    return None


def _find_header(lines: Sequence[Sequence[Word]]) -> tuple[int, dict[str, float]] | None:
    for index, line in enumerate(lines):
        anchors = {
            word.text.upper(): _centre(word)
            for word in line
            if word.text.upper() in _HEADER_ROLES
        }
        if len(anchors) == len(_HEADER_ROLES):
            return index, {role.lower(): x for role, x in anchors.items()}
    return None


def _read_rows(
    lines: Sequence[Sequence[Word]],
    header_index: int,
    anchors: dict[str, float],
    period_start: date | None,
) -> list[_WiseRow]:
    anchor = period_start or date(2000, 1, 1)
    rows: list[_WiseRow] = []
    pending: dict | None = None

    for line in lines[header_index + 1 :]:
        text = collapsed(line)
        if not text:
            continue
        if _STOP_RE.match(text):
            break
        if _IGNORE_RE.match(text):
            continue

        parsed_date = _line_date(line, anchor)
        if parsed_date is not None:
            if pending is not None:
                pending["occurred_on"] = parsed_date
                rows.append(_finish_row(pending))
                pending = None
            continue

        columns = _amount_columns(line, anchors)
        if columns:
            if pending is not None:
                raise StatementValidationError(["A Wise transaction is missing its date line"])
            pending = {
                "description": [word.text for word in line if _centre(word) < anchors["incoming"] - COLUMN_TOLERANCE],
                **columns,
                "occurred_on": None,
            }
        elif pending is not None:
            pending["description"].extend(
                word.text for word in line if _centre(word) < anchors["incoming"] - COLUMN_TOLERANCE
            )

    if pending is not None:
        raise StatementValidationError(["A Wise transaction is missing its date line"])
    return rows


def _line_date(line: Sequence[Word], anchor: date) -> date | None:
    if not line or line[0].x0 > DATE_COLUMN_X:
        return None
    tokens = line[:_MAX_DATE_TOKENS]
    return parse_date(" ".join(word.text for word in tokens), anchor)


def _amount_columns(line: Sequence[Word], anchors: dict[str, float]) -> dict | None:
    values: dict[str, int] = {}
    for word in line:
        centre = _centre(word)
        role = next(
            (role for role in ("incoming", "outgoing", "amount") if abs(centre - anchors[role]) <= COLUMN_TOLERANCE),
            None,
        )
        if role is None:
            continue
        amount = parse_amount(word.text)
        if amount is not None:
            values[role] = amount.cents
    if "amount" not in values:
        return None
    if ("incoming" in values) == ("outgoing" in values):
        raise StatementValidationError(["A Wise transaction has no single incoming or outgoing amount"])
    if "incoming" in values:
        return {"amount_cents": abs(values["incoming"]), "direction": "income", "balance_cents": values["amount"]}
    return {"amount_cents": -abs(values["outgoing"]), "direction": "expense", "balance_cents": values["amount"]}


def _finish_row(pending: dict) -> _WiseRow:
    if pending["occurred_on"] is None:
        raise StatementValidationError(["A Wise transaction is missing its date line"])
    description = _description(pending["description"])
    return _WiseRow(
        occurred_on=pending["occurred_on"],
        description=description,
        amount_cents=pending["amount_cents"],
        balance_cents=pending["balance_cents"],
        direction=pending["direction"],
    )


def _description(tokens: Sequence[str]) -> str:
    text = " ".join(tokens)
    text = _CARD_PREFIX_RE.sub("", text)
    text = " ".join(text.split())
    return text or "Transaction"


def _validate_rows(rows: list[_WiseRow], ending: int | None) -> None:
    previous = None
    for row in rows:
        if previous is not None and row.balance_cents != previous + row.amount_cents:
            raise StatementValidationError(
                [
                    f"Running balance does not match on {row.occurred_on.isoformat()}: "
                    f"{previous} + {row.amount_cents} != {row.balance_cents}"
                ]
            )
        previous = row.balance_cents
    if ending is not None and rows[-1].balance_cents != ending:
        raise StatementValidationError(
            [
                f"Ending balance: parsed {rows[-1].balance_cents} != statement {ending}"
            ]
        )


def _centre(word: Word) -> float:
    return (word.x0 + (word.x1 if word.x1 is not None else word.x0)) / 2
