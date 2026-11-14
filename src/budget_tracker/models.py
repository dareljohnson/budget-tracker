"""Domain models. Monetary values are stored as integer cents to avoid float errors."""

from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import date, timedelta
from enum import StrEnum


class TransactionType(StrEnum):
    INCOME = "income"
    EXPENSE = "expense"
    REFUND = "refund"  # money back for an earlier expense; reduces spending in its category
    TRANSFER = "transfer"  # moves money between accounts, e.g. paying off a credit card
    ADJUSTMENT = "adjustment"  # signed balance correction; excluded from income, spending and budgets

    @property
    def label(self) -> str:
        return self.value.title()

    @property
    def category_type(self) -> TransactionType | None:
        """The kind of category this transaction type uses (None for transfers)."""
        return {
            TransactionType.INCOME: TransactionType.INCOME,
            TransactionType.EXPENSE: TransactionType.EXPENSE,
            TransactionType.REFUND: TransactionType.EXPENSE,
        }.get(self)


class AccountKind(StrEnum):
    CHECKING = "checking"
    SAVINGS = "savings"
    CREDIT_CARD = "credit_card"
    LOAN = "loan"
    CASH = "cash"
    OTHER = "other"

    @property
    def label(self) -> str:
        return self.value.replace("_", " ").title()

    @property
    def is_liability(self) -> bool:
        return self in (AccountKind.CREDIT_CARD, AccountKind.LOAN)

    @property
    def icon(self) -> str:
        return {
            AccountKind.CHECKING: "ACCOUNT_BALANCE",
            AccountKind.SAVINGS: "SAVINGS",
            AccountKind.CREDIT_CARD: "CREDIT_CARD",
            AccountKind.LOAN: "REQUEST_QUOTE",
            AccountKind.CASH: "PAYMENTS",
            AccountKind.OTHER: "ACCOUNT_BALANCE_WALLET",
        }[self]


class DebtKind(StrEnum):
    CREDIT_CARD = "credit_card"
    AUTO = "auto"
    STUDENT = "student"
    PERSONAL = "personal"
    MORTGAGE = "mortgage"
    OTHER = "other"

    @property
    def label(self) -> str:
        return {
            DebtKind.CREDIT_CARD: "Credit card",
            DebtKind.AUTO: "Auto loan",
            DebtKind.STUDENT: "Student loan",
            DebtKind.PERSONAL: "Personal loan",
            DebtKind.MORTGAGE: "Mortgage",
            DebtKind.OTHER: "Other",
        }[self]


@dataclass(slots=True)
class Account:
    name: str
    kind: AccountKind
    opening_balance_cents: int = 0  # negative for money owed (e.g. an existing card balance)
    color: str = "#3949AB"
    id: int | None = None
    number_last4: str | None = None


@dataclass(slots=True, frozen=True)
class AccountBalance:
    account: Account
    balance_cents: int

    @property
    def owed_cents(self) -> int:
        """Amount owed on a liability account (0 if it is in credit)."""
        return max(0, -self.balance_cents)


@dataclass(slots=True)
class Category:
    name: str
    type: TransactionType
    color: str = "#607D8B"
    icon: str = "CATEGORY"
    id: int | None = None


@dataclass(slots=True)
class Transaction:
    amount_cents: int
    type: TransactionType
    category_id: int | None
    occurred_on: date
    description: str = ""
    id: int | None = None
    account_id: int | None = None
    transfer_account_id: int | None = None  # destination account, transfers only
    category_name: str | None = None
    category_color: str | None = None
    category_icon: str | None = None
    account_name: str | None = None
    transfer_account_name: str | None = None
    statement_id: int | None = None  # statement this row was imported from
    import_ref: str | None = None  # source reference within the imported statement
    goal_id: int | None = None  # savings goal this transfer contributes to
    debt_id: int | None = None  # debt this payment is applied to

    @property
    def flow_sign(self) -> int:
        """+1 money in, -1 money out, 0 for transfers (net-neutral overall)."""
        return {TransactionType.INCOME: 1, TransactionType.REFUND: 1, TransactionType.EXPENSE: -1}.get(self.type, 0)


@dataclass(slots=True)
class Debt:
    name: str
    kind: DebtKind
    balance_cents: int
    apr_bps: int
    minimum_cents: int = 0
    original_balance_cents: int | None = None
    account_id: int | None = None  # set only for a debt linked to a credit card
    id: int | None = None


@dataclass(slots=True)
class SavingsGoal:
    name: str
    target_cents: int
    target_date: date
    start_date: date
    account_id: int  # account the saved money lives in
    initial_cents: int = 0  # already saved when the goal was created
    id: int | None = None


@dataclass(slots=True)
class Statement:
    account_id: int
    institution: str
    account_label: str
    period_start: date
    period_end: date
    beginning_cents: int
    deposits_cents: int
    withdrawals_cents: int
    fees_cents: int
    ending_cents: int
    file_sha256: str
    account_last4: str | None = None
    id: int | None = None


@dataclass(slots=True)
class Reconciliation:
    account_id: int
    as_of: date
    stated_cents: int
    computed_cents: int
    difference_cents: int
    note: str = ""
    statement_id: int | None = None
    id: int | None = None
    created_at: str | None = None


@dataclass(slots=True, frozen=True)
class MinimumPayment:
    """The minimum payment printed on one credit card statement."""

    account_id: int
    month: str  # 'YYYY-MM'
    amount_cents: int
    id: int | None = None


@dataclass(slots=True, frozen=True)
class MinimumPaymentStatus:
    """One month's minimum plus how much of it has been paid."""

    month: str
    amount_cents: int
    paid_cents: int = 0

    @property
    def state(self) -> str:
        if self.paid_cents >= self.amount_cents:
            return "paid"
        return "partial" if self.paid_cents else "unpaid"


@dataclass(slots=True, frozen=True)
class CategoryRule:
    match_text: str
    category_id: int
    priority: int = 0
    id: int | None = None


@dataclass(slots=True)
class Budget:
    category_id: int
    month: str  # YYYY-MM
    limit_cents: int
    id: int | None = None


@dataclass(slots=True, frozen=True)
class BudgetProgress:
    budget_id: int
    category_id: int
    category_name: str
    category_color: str
    category_icon: str
    limit_cents: int
    spent_cents: int

    @property
    def ratio(self) -> float:
        return self.spent_cents / self.limit_cents if self.limit_cents else 0.0

    @property
    def remaining_cents(self) -> int:
        return self.limit_cents - self.spent_cents

    @property
    def status(self) -> str:
        if self.ratio > 1:
            return "over"
        if self.ratio >= 0.8:
            return "warning"
        return "ok"


@dataclass(slots=True, frozen=True)
class Summary:
    income_cents: int
    expense_cents: int

    @property
    def net_cents(self) -> int:
        return self.income_cents - self.expense_cents

    @property
    def savings_rate(self) -> float:
        return self.net_cents / self.income_cents if self.income_cents else 0.0


@dataclass(slots=True, frozen=True)
class CategoryTotal:
    category_id: int
    name: str
    color: str
    total_cents: int


@dataclass(slots=True, frozen=True)
class MonthTotal:
    month: str
    income_cents: int
    expense_cents: int


class BillFrequency(StrEnum):
    ONCE = "once"
    WEEKLY = "weekly"
    MONTHLY = "monthly"
    YEARLY = "yearly"

    @property
    def label(self) -> str:
        return {"once": "One time"}.get(self.value, self.value.title())


def next_due_date(current: date, frequency: BillFrequency, anchor_day: int) -> date | None:
    """The due date after ``current``. Monthly/yearly bills keep their day (e.g. the 31st), clamped to month end."""
    if frequency is BillFrequency.ONCE:
        return None
    if frequency is BillFrequency.WEEKLY:
        return current + timedelta(weeks=1)
    months = 1 if frequency is BillFrequency.MONTHLY else 12
    index = current.year * 12 + current.month - 1 + months
    year, month = divmod(index, 12)
    month += 1
    return date(year, month, min(anchor_day, calendar.monthrange(year, month)[1]))


@dataclass(slots=True)
class Bill:
    name: str
    amount_cents: int  # 0 on a card payment means "pay the full amount owed"
    frequency: BillFrequency
    next_due: date
    account_id: int  # account the payment comes from
    category_id: int | None = None  # set for regular bills (paid as an expense)
    pay_to_account_id: int | None = None  # set for card payments (paid as a transfer)
    anchor_day: int | None = None
    active: bool = True
    id: int | None = None
    account_name: str | None = None
    category_name: str | None = None
    category_color: str | None = None
    category_icon: str | None = None
    pay_to_account_name: str | None = None

    @property
    def is_card_payment(self) -> bool:
        return self.pay_to_account_id is not None


@dataclass(slots=True, frozen=True)
class DueBill:
    bill: Bill
    days_until: int  # negative when overdue
    amount_cents: int  # amount to pay now (card payments resolve to the current amount owed)

    @property
    def status(self) -> str:
        if self.days_until < 0:
            return "overdue"
        if self.days_until == 0:
            return "today"
        return "soon"

    @property
    def due_text(self) -> str:
        d = self.days_until
        if d < 0:
            return f"Overdue by {-d} day{'s' if d != -1 else ''}"
        if d == 0:
            return "Due today"
        return "Due tomorrow" if d == 1 else f"Due in {d} days"
