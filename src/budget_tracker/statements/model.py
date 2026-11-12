"""Pure data model for parsed bank statements."""

from dataclasses import dataclass
from datetime import date

from budget_tracker.models import Statement, Transaction


@dataclass(frozen=True, slots=True)
class Word:
    text: str
    x0: float
    top: float


@dataclass(frozen=True, slots=True)
class ParsedTransaction:
    occurred_on: date
    description: str
    amount_cents: int
    direction: str  # "income" | "expense"


@dataclass(frozen=True, slots=True)
class ParsedStatement:
    institution: str
    account_label: str
    account_last4: str | None
    period_start: date
    period_end: date
    beginning_cents: int
    deposits_cents: int
    withdrawals_cents: int
    fees_cents: int
    ending_cents: int
    transactions: list[ParsedTransaction]


@dataclass(slots=True)
class ImportRow:
    transaction: Transaction
    duplicate: bool = False
    include: bool = True


@dataclass(slots=True)
class ImportPreview:
    statement: Statement
    account_id: int
    rows: list[ImportRow]


class StatementValidationError(Exception):
    errors: list[str]

    def __init__(self, errors: list[str]) -> None:
        self.errors = errors
        super().__init__("; ".join(errors))


class UnsupportedStatementError(Exception):
    pass
