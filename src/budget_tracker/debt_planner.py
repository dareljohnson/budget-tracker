"""Pure debt payoff simulation: no database or UI imports."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
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
    payoff_order: list[tuple[DebtInput, int]]
    first_target: Debt | None


def monthly_interest_cents(balance_cents: int, apr_bps: int) -> int:
    return (balance_cents * apr_bps + 60_000) // 120_000


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
    budget = sum(d.minimum_cents for d in entries) + extra_cents
    month = 0
    total_interest = total_paid = 0
    schedule: list[DebtPayoff] = []
    payoff_order: list[tuple[DebtInput, int]] = []
    while any(b > 0 for b in balances) and month < max_months:
        month += 1
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
                amount = min(balances[i], entries[i].minimum_cents)
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
            if balances[i] == 0 and not any(entry is entries[i] for entry, _ in payoff_order):
                payoff_order.append((entries[i], month))
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
