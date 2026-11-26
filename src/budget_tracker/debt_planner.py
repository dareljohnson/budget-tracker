"""Pure debt payoff simulation: no database or UI imports."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from enum import StrEnum

from .formatting import shift_month
from .models import Debt

MAX_MONTHS = 600


class DebtStrategy(StrEnum):
    AVALANCHE = "avalanche"
    SNOWBALL = "snowball"

    @property
    def label(self) -> str:
        return self.value.title()


@dataclass(slots=True, frozen=True)
class DebtInput:
    debt: Debt
    balance_cents: int
    minimum_cents: int
    minimum_after_cents: int | None = None
    minimum_after_month: str | None = None  # YYYY-MM when the later minimum starts


@dataclass(slots=True, frozen=True)
class DebtPayoff:
    month: int  # 1-based month number
    balance_cents: int
    interest_cents: int
    paid_cents: int


@dataclass(slots=True, frozen=True)
class DebtPlan:
    strategy: DebtStrategy
    extra_cents: int
    months: int
    debt_free_month: str | None
    total_interest_cents: int
    total_paid_cents: int
    finished: bool
    schedule: list[DebtPayoff]
    payoff_order: list[tuple[Debt, int]]
    first_target: Debt | None


def monthly_interest_cents(balance_cents: int, apr_bps: int) -> int:
    return (balance_cents * apr_bps + 60_000) // 120_000


def remaining_payments(payment_date: date, maturity_date: date | None) -> int:
    """How many monthly payments are left, counting the maturity month."""
    if maturity_date is None or payment_date > maturity_date:
        return 0
    months = (
        (maturity_date.year - payment_date.year) * 12
        + (maturity_date.month - payment_date.month)
        + 1
    )
    return max(0, months)


def scheduled_pi_payment_cents(
    balance_cents: int, apr_bps: int, remaining: int
) -> int | None:
    """The principal-and-interest payment that amortizes ``balance`` over ``remaining`` months."""
    if balance_cents <= 0 or remaining <= 0:
        return None
    if apr_bps <= 0:
        return max(1, round(balance_cents / remaining))
    monthly_rate = apr_bps / 10_000 / 12
    factor = (1 + monthly_rate) ** remaining
    return max(1, round(balance_cents * monthly_rate * factor / (factor - 1)))


def escrow_from_amortization(
    total_cents: int,
    balance_cents: int,
    apr_bps: int,
    maturity_date: date | None,
    payment_date: date,
    *,
    pi_payment_cents: int | None = None,
) -> int | None:
    """The escrow part of a payment: total minus the scheduled principal-and-interest payment.

    Returns ``None`` when there is no maturity date or P&I payment to schedule from, or when
    the total payment is smaller than the scheduled P&I.
    """
    scheduled = pi_payment_cents
    if scheduled is None:
        scheduled = scheduled_pi_payment_cents(
            balance_cents, apr_bps, remaining_payments(payment_date, maturity_date)
        )
    if scheduled is None or scheduled > total_cents:
        return None
    return total_cents - scheduled


def next_month_extra_allocation(
    debts: Sequence[DebtInput],
    *,
    extra_cents: int,
    strategy: DebtStrategy,
    start_month: str | None = None,
) -> dict[int, int]:
    """The extra above minimums each debt receives in the plan's first month.

    The plan pays every minimum first, then applies the remaining budget in strategy order.
    A debt that is nearly paid off takes only what it needs; the rest rolls to the next debt.
    """
    entries = [d for d in debts if d.balance_cents > 0]
    if not entries or extra_cents <= 0:
        return {}
    if strategy == DebtStrategy.AVALANCHE:
        order = sorted(
            range(len(entries)),
            key=lambda i: (-entries[i].debt.apr_bps, entries[i].balance_cents),
        )
    else:
        order = sorted(
            range(len(entries)),
            key=lambda i: (entries[i].balance_cents, -entries[i].debt.apr_bps),
        )
    balances = [
        entry.balance_cents + monthly_interest_cents(entry.balance_cents, entry.debt.apr_bps)
        for entry in entries
    ]
    minimums = [
        (
            entry.minimum_after_cents
            if start_month
            and entry.minimum_after_cents is not None
            and entry.minimum_after_month is not None
            and start_month >= entry.minimum_after_month
            else entry.minimum_cents
        )
        for entry in entries
    ]
    budget = sum(minimums) + extra_cents
    paid = 0
    for i in order:
        if balances[i] > 0:
            amount = min(balances[i], minimums[i])
            balances[i] -= amount
            paid += amount
    remainder = budget - paid
    allocation: dict[int, int] = {}
    for i in order:
        if remainder <= 0:
            break
        if balances[i] > 0:
            amount = min(balances[i], remainder)
            balances[i] -= amount
            remainder -= amount
            if amount and entries[i].debt.id is not None:
                allocation[entries[i].debt.id] = amount
    return allocation


def plan_payoff(
    debts: Sequence[DebtInput],
    *,
    extra_cents: int,
    strategy: DebtStrategy,
    start_month: str,
    max_months: int = MAX_MONTHS,
) -> DebtPlan:
    """Simulate a fixed monthly budget: every minimum plus the extra, held until debt-free."""
    entries = [d for d in debts if d.balance_cents > 0]
    if not entries:
        return DebtPlan(strategy, extra_cents, 0, start_month, 0, 0, True, [], [], None)
    if strategy == DebtStrategy.AVALANCHE:
        order = sorted(range(len(entries)), key=lambda i: (-entries[i].debt.apr_bps, entries[i].balance_cents))
    else:
        order = sorted(range(len(entries)), key=lambda i: (entries[i].balance_cents, -entries[i].debt.apr_bps))
    balances = [d.balance_cents for d in entries]
    minimums = [d.minimum_cents for d in entries]
    month = 0
    total_interest = total_paid = 0
    schedule: list[DebtPayoff] = []
    payoff_order: list[tuple[Debt, int]] = []
    while any(b > 0 for b in balances) and month < max_months:
        month += 1
        current_month = shift_month(start_month, month - 1)
        for i, entry in enumerate(entries):
            if (
                entry.minimum_after_cents is not None
                and entry.minimum_after_month is not None
                and current_month >= entry.minimum_after_month
            ):
                minimums[i] = entry.minimum_after_cents
        budget = sum(minimums) + extra_cents
        interest_this_month = 0
        for i, d in enumerate(entries):
            if balances[i] > 0:
                interest = monthly_interest_cents(balances[i], d.debt.apr_bps)
                balances[i] += interest
                interest_this_month += interest
        total_interest += interest_this_month
        paid = 0
        for i in order:
            if balances[i] > 0:
                amount = min(balances[i], minimums[i])
                balances[i] -= amount
                paid += amount
        remainder = budget - paid
        for i in order:
            if remainder <= 0:
                break
            if balances[i] > 0:
                amount = min(balances[i], remainder)
                balances[i] -= amount
                paid += amount
                remainder -= amount
        total_paid += paid
        for i in order:
            if balances[i] == 0 and not any(debt is entries[i].debt for debt, _ in payoff_order):
                payoff_order.append((entries[i].debt, month))
        schedule.append(DebtPayoff(month, sum(balances), interest_this_month, paid))
    finished = all(balance == 0 for balance in balances)
    return DebtPlan(
        strategy=strategy,
        extra_cents=extra_cents,
        months=month,
        debt_free_month=shift_month(start_month, month) if finished else None,
        total_interest_cents=total_interest,
        total_paid_cents=total_paid,
        finished=finished,
        schedule=schedule,
        payoff_order=payoff_order,
        first_target=entries[order[0]].debt,
    )
