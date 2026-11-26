"""Pure simulation for a cycled personal line-of-credit payoff plan.

The plan draws a chunk from a personal line of credit (PLOC), applies it to one or more
selected debt targets, then uses a monthly sweep to pay the line back down. A new chunk
starts only after the line is back to zero. Chunks and extra payments go to the highest-APR
target first. This is a read-only estimate: it never moves money or writes transactions.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from .debt_planner import MAX_MONTHS, monthly_interest_cents

# A PLOC plan that cannot finish within the same 50-year window as the payoff planner is
# reported as unfinished rather than given a misleading date.
VELOCITY_MAX_MONTHS = MAX_MONTHS


@dataclass(frozen=True, slots=True)
class VelocityTarget:
    target_id: int | None
    balance_cents: int
    apr_bps: int
    minimum_cents: int


@dataclass(frozen=True, slots=True)
class VelocityTargetResult:
    target_id: int | None
    payoff_month: int | None
    total_interest_cents: int


@dataclass(frozen=True, slots=True)
class VelocityInput:
    target_balance_cents: int = 0
    target_apr_bps: int = 0
    target_minimum_cents: int = 0
    loc_limit_cents: int = 0
    loc_apr_bps: int = 0
    loc_balance_cents: int = 0
    chunk_cents: int = 0
    sweep_cents: int = 0
    reserve_cents: int = 0
    max_months: int = VELOCITY_MAX_MONTHS
    targets: tuple[VelocityTarget, ...] = ()

    @classmethod
    def for_targets(
        cls,
        targets: tuple[VelocityTarget, ...],
        *,
        loc_limit_cents: int,
        loc_apr_bps: int,
        loc_balance_cents: int = 0,
        chunk_cents: int = 0,
        sweep_cents: int = 0,
        reserve_cents: int = 0,
    ) -> VelocityInput:
        return cls(
            loc_limit_cents=loc_limit_cents,
            loc_apr_bps=loc_apr_bps,
            loc_balance_cents=loc_balance_cents,
            chunk_cents=chunk_cents,
            sweep_cents=sweep_cents,
            reserve_cents=reserve_cents,
            targets=targets,
        )


@dataclass(frozen=True, slots=True)
class VelocityMonth:
    month: int  # 1-based; month 1 is the current month
    cycle: int
    chunk_cents: int
    target_balance_cents: int
    loc_balance_cents: int
    target_interest_cents: int
    loc_interest_cents: int
    target_paid_cents: int
    loc_paid_cents: int


@dataclass(frozen=True, slots=True)
class VelocityResult:
    months: int
    finished: bool
    target_payoff_month: int | None
    line_clear_month: int | None
    cycles: int
    total_target_interest_cents: int
    total_loc_interest_cents: int
    total_interest_cents: int
    total_target_paid_cents: int
    total_loc_paid_cents: int
    max_loc_balance_cents: int
    schedule: list[VelocityMonth]
    target_results: tuple[VelocityTargetResult, ...] = ()

    @property
    def average_loc_balance_cents(self) -> int:
        if not self.schedule:
            return 0
        return sum(row.loc_balance_cents for row in self.schedule) // len(self.schedule)


@dataclass(frozen=True, slots=True)
class BaselineResult:
    months: int
    finished: bool
    payoff_month: int | None
    total_interest_cents: int
    total_paid_cents: int
    target_results: tuple[VelocityTargetResult, ...] = ()


@dataclass(frozen=True, slots=True)
class VelocityComparison:
    velocity: VelocityResult
    baseline: BaselineResult
    net_interest_saved_cents: int | None
    break_even_loc_apr_bps: int | None


def _interest(balance_cents: int, apr_bps: int) -> int:
    return monthly_interest_cents(max(0, balance_cents), max(0, apr_bps))


def _input_targets(inputs: VelocityInput) -> list[VelocityTarget]:
    raw = inputs.targets or (
        VelocityTarget(
            None,
            inputs.target_balance_cents,
            inputs.target_apr_bps,
            inputs.target_minimum_cents,
        ),
    )
    targets: list[VelocityTarget] = []
    for target in raw:
        balance = max(0, int(target.balance_cents))
        if balance <= 0:
            continue
        targets.append(VelocityTarget(
            target.target_id,
            balance,
            max(0, int(target.apr_bps)),
            max(0, int(target.minimum_cents)),
        ))
    return targets


def _priority(targets: list[VelocityTarget], balances: list[int]) -> list[int]:
    """Highest APR first; ties go to the smaller balance, then the lower target id."""
    return sorted(
        range(len(targets)),
        key=lambda index: (
            -targets[index].apr_bps,
            balances[index],
            targets[index].target_id or 0,
        ),
    )


def _pay_targets(
    order: list[int],
    balances: list[int],
    paid: list[int],
    payoff: list[int | None],
    amount: int,
    month: int,
) -> int:
    """Apply ``amount`` to balances in priority order and return what was actually paid."""
    remaining = max(0, amount)
    for index in order:
        if remaining <= 0:
            break
        payment = min(balances[index], remaining)
        if payment <= 0:
            continue
        balances[index] -= payment
        paid[index] += payment
        remaining -= payment
        if balances[index] <= 0 and payoff[index] is None:
            payoff[index] = month
    return max(0, amount) - remaining


def run_velocity_plan(inputs: VelocityInput) -> VelocityResult:
    """Run the multi-target cycle simulation and return the month-by-month result.

    A draw happens only when the line is at zero and at least one target remains. Chunks and
    any extra budget go to the highest-APR target first. The monthly budget is the sum of the
    targets' initial minimums plus the sweep; if a target is paid off, its minimum rolls to the
    line or the remaining targets. The PLOC is separate from the target debts.
    """
    targets = _input_targets(inputs)
    if not targets:
        return VelocityResult(0, True, 0, 0, 0, 0, 0, 0, 0, 0, 0, [], ())

    balances = [target.balance_cents for target in targets]
    minimums = [target.minimum_cents for target in targets]
    target_interest = [0] * len(targets)
    target_paid = [0] * len(targets)
    target_payoff: list[int | None] = [None] * len(targets)
    order = _priority(targets, balances)
    budget = sum(minimums) + max(0, inputs.sweep_cents)

    line = max(0, inputs.loc_balance_cents)
    line_interest = line_paid = total_target_paid = 0
    max_line = line
    cycles = 0
    line_clear: int | None = None
    schedule: list[VelocityMonth] = []
    limit = max(0, inputs.loc_limit_cents)
    reserve = max(0, inputs.reserve_cents)
    chunk_size = max(0, inputs.chunk_cents)

    month = 0
    while month < max(1, inputs.max_months) and (any(balance > 0 for balance in balances) or line > 0):
        month += 1
        cycle = cycles
        chunk = 0
        paid_this_month = 0

        if line <= 0 and chunk_size > 0 and any(balance > 0 for balance in balances):
            available = max(0, limit - reserve - line)
            total_balance = sum(balances)
            chunk = min(chunk_size, available, total_balance)
            if chunk > 0:
                paid = _pay_targets(order, balances, target_paid, target_payoff, chunk, month)
                line += paid
                total_target_paid += paid
                paid_this_month += paid
                cycles += 1
                cycle = cycles
                max_line = max(max_line, line)

        target_interest_month = 0
        for index, target in enumerate(targets):
            accrued = _interest(balances[index], target.apr_bps)
            balances[index] += accrued
            target_interest[index] += accrued
            target_interest_month += accrued
        line_interest_month = _interest(line, inputs.loc_apr_bps)
        line += line_interest_month
        line_interest += line_interest_month
        max_line = max(max_line, line)

        remaining = budget
        for index in range(len(targets)):
            payment = min(balances[index], minimums[index], remaining)
            if payment <= 0:
                continue
            balances[index] -= payment
            target_paid[index] += payment
            total_target_paid += payment
            paid_this_month += payment
            remaining -= payment
            if balances[index] <= 0 and target_payoff[index] is None:
                target_payoff[index] = month

        line_payment = min(line, remaining)
        line -= line_payment
        remaining -= line_payment
        line_paid += line_payment

        if line <= 0 and remaining > 0:
            extra = _pay_targets(order, balances, target_paid, target_payoff, remaining, month)
            total_target_paid += extra
            paid_this_month += extra

        all_targets_paid = all(balance <= 0 for balance in balances)
        if all_targets_paid and line <= 0:
            line_clear = month
        schedule.append(
            VelocityMonth(
                month=month,
                cycle=cycle,
                chunk_cents=chunk,
                target_balance_cents=sum(balances),
                loc_balance_cents=line,
                target_interest_cents=target_interest_month,
                loc_interest_cents=line_interest_month,
                target_paid_cents=paid_this_month,
                loc_paid_cents=line_payment,
            )
        )

    finished = all(balance <= 0 for balance in balances) and line <= 0
    payoff_overall = (
        max(month for month in target_payoff if month is not None)
        if all(month is not None for month in target_payoff)
        else None
    )
    target_results = tuple(
        VelocityTargetResult(target.target_id, target_payoff[index], target_interest[index])
        for index, target in enumerate(targets)
    )
    return VelocityResult(
        months=month,
        finished=finished,
        target_payoff_month=payoff_overall,
        line_clear_month=line_clear,
        cycles=cycles,
        total_target_interest_cents=sum(target_interest),
        total_loc_interest_cents=line_interest,
        total_interest_cents=sum(target_interest) + line_interest,
        total_target_paid_cents=total_target_paid,
        total_loc_paid_cents=line_paid,
        max_loc_balance_cents=max_line,
        schedule=schedule,
        target_results=target_results,
    )


def run_baseline(inputs: VelocityInput) -> BaselineResult:
    """The same monthly cash flow paid directly to the targets, with no credit line."""
    targets = _input_targets(inputs)
    if not targets:
        return BaselineResult(0, True, 0, 0, 0, ())
    balances = [target.balance_cents for target in targets]
    minimums = [target.minimum_cents for target in targets]
    target_interest = [0] * len(targets)
    target_payoff: list[int | None] = [None] * len(targets)
    order = _priority(targets, balances)
    budget = sum(minimums) + max(0, inputs.sweep_cents)
    interest = paid = 0
    month = 0
    while month < max(1, inputs.max_months) and any(balance > 0 for balance in balances):
        month += 1
        for index, target in enumerate(targets):
            accrued = _interest(balances[index], target.apr_bps)
            balances[index] += accrued
            target_interest[index] += accrued
            interest += accrued
        remaining = budget
        for index in range(len(targets)):
            payment = min(balances[index], minimums[index], remaining)
            if payment <= 0:
                continue
            balances[index] -= payment
            paid += payment
            remaining -= payment
            if balances[index] <= 0 and target_payoff[index] is None:
                target_payoff[index] = month
        if remaining > 0:
            extra = _pay_targets(order, balances, [0] * len(targets), target_payoff, remaining, month)
            paid += extra
    payoff_overall = (
        max(month for month in target_payoff if month is not None)
        if all(month is not None for month in target_payoff)
        else None
    )
    return BaselineResult(
        months=month,
        finished=all(balance <= 0 for balance in balances),
        payoff_month=payoff_overall,
        total_interest_cents=interest,
        total_paid_cents=paid,
        target_results=tuple(
            VelocityTargetResult(target.target_id, target_payoff[index], target_interest[index])
            for index, target in enumerate(targets)
        ),
    )


def compare_velocity(inputs: VelocityInput) -> VelocityComparison:
    """Compare the PLOC cycles with paying the same budget directly to the targets."""
    velocity = run_velocity_plan(inputs)
    baseline = run_baseline(inputs)
    net_saved = None
    if velocity.finished and baseline.finished:
        net_saved = baseline.total_interest_cents - velocity.total_interest_cents
    break_even = (
        _break_even_apr(inputs, baseline.total_interest_cents)
        if velocity.finished and baseline.finished
        else None
    )
    return VelocityComparison(velocity, baseline, net_saved, break_even)


def rate_scenarios(
    inputs: VelocityInput, *, increases_bps: tuple[int, ...] = (0, 200, 400)
) -> list[tuple[int, VelocityResult]]:
    """Run the same plan at the base LOC rate and each stress increase."""
    results: list[tuple[int, VelocityResult]] = []
    for increase in increases_bps:
        apr = min(10_000, max(0, inputs.loc_apr_bps + increase))
        results.append((apr, run_velocity_plan(replace(inputs, loc_apr_bps=apr))))
    return results


def _break_even_apr(inputs: VelocityInput, baseline_interest_cents: int) -> int | None:
    """The LOC APR where the plan's total interest matches the no-line baseline.

    ``None`` means even a 100% LOC rate still beats the baseline, or the plan stopped
    finishing at the high end of the search.
    """

    def interest_at(apr_bps: int) -> int | None:
        result = run_velocity_plan(replace(inputs, loc_apr_bps=apr_bps))
        return result.total_interest_cents if result.finished else None

    at_zero = interest_at(0)
    if at_zero is None or at_zero >= baseline_interest_cents:
        return 0
    at_max = interest_at(10_000)
    if at_max is not None and at_max <= baseline_interest_cents:
        return None
    low, high = 0, 10_000
    while low < high:
        middle = (low + high) // 2
        result = interest_at(middle)
        if result is not None and result <= baseline_interest_cents:
            low = middle + 1
        else:
            high = middle
    return low
