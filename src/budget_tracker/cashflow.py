"""Pure cash-flow projection engine.

This module turns starting liquid balances and dated events into a daily forecast. It deliberately
imports only the domain models and the standard library: no database, Flet, or Pillow.
"""

from __future__ import annotations

import calendar
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, timedelta

from .models import (
    Account,
    AccountKind,
    Bill,
    BillFrequency,
    IncomeFrequency,
    IncomeSchedule,
    Transaction,
    TransactionType,
    next_due_date,
)

LIQUID_KINDS = frozenset({AccountKind.CHECKING, AccountKind.SAVINGS, AccountKind.CASH})
INFLOW_KINDS = frozenset({"income", "refund", "transfer_in"})

# De-duplication is by transaction type and, for transfers, by the target account. A transfer is
# not a bill, a refund is not income, and an expense is not a card payment. Card-payment and
# minimum events carry ``counter_account_id`` so a transfer only cancels the debt it actually pays.
_INCOME_KINDS = frozenset({"income"})
_EXPENSE_KINDS = frozenset({"bill"})
# A card_payment amount is already net of recorded transfers, so a transfer never cancels one.
# Minimum amounts are net of payments through today only, so a future transfer can.
_TRANSFER_KINDS = frozenset({"minimum"})

_ONE_DAY = timedelta(days=1)
_RECHECK_DAYS = (30, 60, 90)
_MAX_DAY_SPAN = 1
_FULL_HORIZON = 90


@dataclass(frozen=True, slots=True)
class ForecastBill:
    bill: Bill
    amount_cents: int


@dataclass(frozen=True, slots=True)
class CardMinimum:
    account_id: int
    month: str
    remaining_cents: int
    source_account_id: int


@dataclass(frozen=True, slots=True)
class CashEvent:
    date: date
    description: str
    account_id: int
    amount_cents: int  # signed: in positive, out negative
    kind: str  # income|refund|transfer_in|transfer_out|bill|card_payment|minimum
    counter_account_id: int | None = None  # card/loan paid by a card_payment or minimum


@dataclass(frozen=True, slots=True)
class CashFlowPoint:
    date: date
    balance_cents: int


@dataclass(frozen=True, slots=True)
class CashFlowForecast:
    points: list[CashFlowPoint]  # one point per day, first = end of today
    events: list[CashEvent]
    starting_cents: int
    lowest_date: date
    lowest_cents: int
    at_30: int
    at_60: int
    at_90: int
    endings_by_account: dict[int, tuple[int, int, int]]
    has_income: bool


@dataclass(frozen=True, slots=True)
class _CardOccurrence:
    """A card-payment candidate before the per-card cap is allocated."""

    date: date
    description: str
    account_id: int  # paying (liquid) account
    target_id: int  # card/loan being paid
    nominal_cents: int
    is_open: bool
    order: int


def income_dates(schedule: IncomeSchedule, start: date, end: date) -> list[date]:
    """Occurrence dates in ``[start, end]`` inclusive, driven by the schedule's rule and anchor.

    ``next_date`` is only the first candidate: dates before it are never emitted, and a past
    ``next_date`` advances to the first occurrence on or after ``start``. Monthly and semimonthly
    dates clamp to the month end without losing the anchor day for later months.
    """
    if end < start:
        return []
    if schedule.frequency in (IncomeFrequency.WEEKLY, IncomeFrequency.BIWEEKLY):
        return _fixed_interval_dates(schedule.next_date, start, end, schedule.frequency)
    if schedule.frequency is IncomeFrequency.MONTHLY:
        return _monthly_dates(schedule, start, end)
    if schedule.frequency is IncomeFrequency.SEMIMONTHLY:
        return _semimonthly_dates(schedule, start, end)
    return []


def _fixed_interval_dates(
    first_candidate: date,
    start: date,
    end: date,
    frequency: IncomeFrequency,
) -> list[date]:
    step = 7 if frequency is IncomeFrequency.WEEKLY else 14
    current = first_candidate
    if current < start:
        days = (start - current).days
        current += timedelta(days=((days + step - 1) // step) * step)
    dates: list[date] = []
    while current <= end:
        dates.append(current)
        current += timedelta(days=step)
    return dates


def _month_index(day: date) -> int:
    return day.year * 12 + day.month - 1


def clamped_day(day: date, desired_day: int) -> int:
    """``desired_day`` clamped to the last day of ``day``'s month.

    Monthly and semimonthly rules keep their anchor (for example, the 31st) while occurring on the
    month's last day in short months. Validation therefore compares the clamped day for the chosen
    month instead of the raw anchor.
    """
    return min(desired_day, calendar.monthrange(day.year, day.month)[1])


def _clamped_day(index: int, day: int) -> date:
    year, month_index = divmod(index, 12)
    month = month_index + 1
    return date(year, month, min(day, calendar.monthrange(year, month)[1]))


def _monthly_dates(schedule: IncomeSchedule, start: date, end: date) -> list[date]:
    anchor = schedule.anchor_day
    first_index = max(_month_index(schedule.next_date), _month_index(start))
    last_index = _month_index(end)
    dates: list[date] = []
    for index in range(first_index, last_index + 1):
        candidate = _clamped_day(index, anchor)
        if candidate < schedule.next_date or candidate < start or candidate > end:
            continue
        dates.append(candidate)
    return dates


def _semimonthly_dates(schedule: IncomeSchedule, start: date, end: date) -> list[date]:
    days = [schedule.anchor_day]
    if schedule.second_day is not None:
        days.append(schedule.second_day)
    first_index = max(_month_index(schedule.next_date), _month_index(start))
    last_index = _month_index(end)
    dates: set[date] = set()
    for index in range(first_index, last_index + 1):
        for day in days:
            candidate = _clamped_day(index, day)
            if candidate < schedule.next_date or candidate < start or candidate > end:
                continue
            dates.add(candidate)
    return sorted(dates)


def forecast(
    *,
    today: date,
    horizon_days: int,
    accounts: Sequence[Account],
    balances: Mapping[int, int],
    bills: Sequence[ForecastBill],
    card_owed: Mapping[int, int],
    card_minimums: Sequence[CardMinimum],
    incomes: Sequence[IncomeSchedule],
    scheduled_transactions: Sequence[Transaction],
) -> CashFlowForecast:
    """Project liquid balances from the end of today through ``today + horizon_days``.

    Balances are as of today and already include today's recorded transactions. Bills, card
    minimums, and income occurrences due today are applied; recorded transactions dated today are
    used only to de-duplicate scheduled events. Recorded transactions after today are applied
    normally, with transfers expanded per liquid account.

    ``at_30``/``at_60``/``at_90`` and ``endings_by_account`` are always taken from a full 90-day
    projection; ``points``, ``events``, and ``lowest`` are limited to the requested horizon.
    """
    days = max(0, horizon_days)
    end = today + timedelta(days=days)
    simulation_days = max(days, _FULL_HORIZON)
    simulation_end = today + timedelta(days=simulation_days)

    liquid_ids = [account.id for account in accounts if account.id is not None and account.kind in LIQUID_KINDS]
    liquid_set = set(liquid_ids)
    account_names = {account.id: account.name for account in accounts if account.id is not None}

    start_balances = {account_id: int(balances.get(account_id, 0)) for account_id in liquid_ids}
    starting_cents = sum(start_balances.values())

    scheduled, card_occurrences = _scheduled_events(
        today=today,
        end=simulation_end,
        bills=bills,
        card_minimums=card_minimums,
        incomes=incomes,
        liquid_set=liquid_set,
        account_names=account_names,
    )

    # Transactions through the full simulated horizon can cancel scheduled events; output events
    # remain limited to the requested horizon.
    recorded = [
        transaction
        for transaction in scheduled_transactions
        if today <= transaction.occurred_on <= simulation_end
        and transaction.type is not TransactionType.ADJUSTMENT
    ]
    consumed = _deduplicate(scheduled, recorded, liquid_set, today)
    card_indexes = set(card_occurrences)
    events = [
        event
        for index, event in enumerate(scheduled)
        if index not in consumed and index not in card_indexes
    ]
    events.extend(
        _allocate_card_payments(
            [occurrence for index, occurrence in card_occurrences.items() if index not in consumed],
            card_owed,
            scheduled_transactions,
            today,
        )
    )
    events.extend(_transaction_events(scheduled_transactions, today, simulation_end, liquid_set))
    events.sort(key=_event_sort_key)

    by_date: dict[date, list[CashEvent]] = {}
    for event in events:
        if today <= event.date <= simulation_end:
            by_date.setdefault(event.date, []).append(event)

    balances_now = dict(start_balances)
    points: list[CashFlowPoint] = []
    snapshots: dict[int, dict[int, int]] = {}
    for offset in range(simulation_days + 1):
        day = today + timedelta(days=offset)
        for event in by_date.get(day, ()):  # order does not change the day-end total
            balances_now[event.account_id] = balances_now.get(event.account_id, 0) + event.amount_cents
        if offset <= days:
            points.append(CashFlowPoint(day, sum(balances_now.values())))
        if offset in _RECHECK_DAYS:
            snapshots[offset] = dict(balances_now)

    if points:
        lowest = min(points, key=lambda point: (point.balance_cents, point.date))
        lowest_date, lowest_cents = lowest.date, lowest.balance_cents
    else:
        lowest_date, lowest_cents = today, starting_cents

    def total_at(offset: int) -> int:
        return sum(snapshots[offset].values())

    endings_by_account: dict[int, tuple[int, int, int]] = {
        account_id: (
            snapshots[30].get(account_id, 0),
            snapshots[60].get(account_id, 0),
            snapshots[90].get(account_id, 0),
        )
        for account_id in liquid_ids
    }
    horizon_events = [event for event in events if event.date <= end]

    return CashFlowForecast(
        points=points,
        events=horizon_events,
        starting_cents=starting_cents,
        lowest_date=lowest_date,
        lowest_cents=lowest_cents,
        at_30=total_at(30),
        at_60=total_at(60),
        at_90=total_at(90),
        endings_by_account=endings_by_account,
        has_income=any(event.kind == "income" for event in horizon_events),
    )


def _scheduled_events(
    *,
    today: date,
    end: date,
    bills: Sequence[ForecastBill],
    card_minimums: Sequence[CardMinimum],
    incomes: Sequence[IncomeSchedule],
    liquid_set: set[int],
    account_names: Mapping[int, str],
) -> tuple[list[CashEvent], dict[int, _CardOccurrence]]:
    """Build scheduled events and card-payment candidates, de-duplicating before cap allocation."""
    events: list[CashEvent] = []
    card_occurrences: dict[int, _CardOccurrence] = {}
    order = 0
    for entry in bills:
        bill = entry.bill
        if not bill.active or bill.account_id not in liquid_set:
            continue
        occurrences = _bill_dates(bill, today, end)
        if not occurrences:
            continue
        if not bill.is_card_payment:
            if entry.amount_cents <= 0:
                continue
            for occurrence in occurrences:
                events.append(
                    CashEvent(
                        date=occurrence,
                        description=bill.name,
                        account_id=bill.account_id,
                        amount_cents=-abs(entry.amount_cents),
                        kind="bill",
                    )
                )
            continue
        target = bill.pay_to_account_id
        if target is None:  # defensive: is_card_payment implies a target
            continue
        if bill.amount_cents == 0:
            # Pay-the-full-balance bills are projected once; recurrence is deliberately ignored.
            if entry.amount_cents <= 0:
                continue
            _add_card_occurrence(
                events,
                card_occurrences,
                _CardOccurrence(
                    date=occurrences[0],
                    description=bill.name,
                    account_id=bill.account_id,
                    target_id=target,
                    nominal_cents=entry.amount_cents,
                    is_open=True,
                    order=order,
                ),
            )
            order += 1
            continue
        nominal = abs(entry.amount_cents)
        if nominal <= 0:
            continue
        for occurrence in occurrences:
            _add_card_occurrence(
                events,
                card_occurrences,
                _CardOccurrence(
                    date=occurrence,
                    description=bill.name,
                    account_id=bill.account_id,
                    target_id=target,
                    nominal_cents=nominal,
                    is_open=False,
                    order=order,
                ),
            )
            order += 1

    events.extend(_minimum_events(card_minimums, today, end, liquid_set, account_names))
    for schedule in incomes:
        if not schedule.active or schedule.account_id not in liquid_set or schedule.amount_cents <= 0:
            continue
        for day in income_dates(schedule, today + _ONE_DAY, end):
            events.append(
                CashEvent(
                    date=day,
                    description=schedule.name,
                    account_id=schedule.account_id,
                    amount_cents=schedule.amount_cents,
                    kind="income",
                )
            )
    # Keep insertion order so card-occurrence indexes stay valid; output sorting happens later.
    return events, card_occurrences


def _add_card_occurrence(
    events: list[CashEvent],
    card_occurrences: dict[int, _CardOccurrence],
    occurrence: _CardOccurrence,
) -> None:
    card_occurrences[len(events)] = occurrence
    events.append(
        CashEvent(
            date=occurrence.date,
            description=occurrence.description,
            account_id=occurrence.account_id,
            amount_cents=-occurrence.nominal_cents,
            kind="card_payment",
            counter_account_id=occurrence.target_id,
        )
    )


def _allocate_card_payments(
    occurrences: Sequence[_CardOccurrence],
    card_owed: Mapping[int, int],
    transactions: Sequence[Transaction],
    today: date,
) -> list[CashEvent]:
    """Allocate the remaining owed across surviving card-payment occurrences chronologically.

    A recorded card transfer dated after today lowers the cap only from its own date onward, so a
    transfer made later never cancels a payment due before it. Today's transfers are already inside
    ``card_owed``.
    """
    remaining_owed = {int(account_id): int(cents) for account_id, cents in card_owed.items()}
    transfer_amounts: dict[int, list[tuple[date, int]]] = {}
    for transaction in transactions:
        if transaction.type is not TransactionType.TRANSFER:
            continue
        target = transaction.transfer_account_id
        if target is None or target not in remaining_owed or transaction.occurred_on <= today:
            continue
        transfer_amounts.setdefault(target, []).append(
            (transaction.occurred_on, abs(transaction.amount_cents))
        )
    for entries in transfer_amounts.values():
        entries.sort()

    applied: dict[int, int] = {}
    events: list[CashEvent] = []
    for occurrence in sorted(occurrences, key=lambda item: (item.date, item.order)):
        pending = transfer_amounts.get(occurrence.target_id, [])
        index = applied.get(occurrence.target_id, 0)
        while index < len(pending) and pending[index][0] <= occurrence.date:
            paid = pending[index][1]
            if occurrence.target_id in remaining_owed:
                remaining_owed[occurrence.target_id] = max(
                    0, remaining_owed[occurrence.target_id] - paid
                )
            index += 1
        applied[occurrence.target_id] = index
        remaining = remaining_owed.get(occurrence.target_id)
        if occurrence.is_open:
            amount = occurrence.nominal_cents if remaining is None else min(occurrence.nominal_cents, remaining)
        else:
            if remaining is None or remaining <= 0:
                continue
            amount = min(occurrence.nominal_cents, remaining)
        if amount <= 0:
            continue
        if remaining is not None:
            remaining_owed[occurrence.target_id] = remaining - amount
        events.append(
            CashEvent(
                date=occurrence.date,
                description=occurrence.description,
                account_id=occurrence.account_id,
                amount_cents=-amount,
                kind="card_payment",
                counter_account_id=occurrence.target_id,
            )
        )
    return events


def _bill_dates(bill: Bill, today: date, end: date) -> list[date]:
    anchor = bill.anchor_day or bill.next_due.day
    due = bill.next_due
    dates: list[date] = []
    if due < today:
        # One occurrence at today for the overdue amount, then normal future recurrence.
        dates.append(today)
        due = _advance_past(due, bill.frequency, anchor, today)
    while due is not None and due <= end:
        dates.append(due)
        due = next_due_date(due, bill.frequency, anchor)
    return dates


def _advance_past(current: date, frequency: BillFrequency, anchor: int, today: date) -> date | None:
    candidate = next_due_date(current, frequency, anchor)
    while candidate is not None and candidate <= today:
        candidate = next_due_date(candidate, frequency, anchor)
    return candidate


def _minimum_events(
    card_minimums: Sequence[CardMinimum],
    today: date,
    end: date,
    liquid_set: set[int],
    account_names: Mapping[int, str],
) -> list[CashEvent]:
    events: list[CashEvent] = []
    for minimum in card_minimums:
        if minimum.source_account_id not in liquid_set or minimum.remaining_cents <= 0:
            continue
        due = _minimum_due_date(minimum.month, today)
        if due > end:
            continue
        card_name = account_names.get(minimum.account_id, "Card")
        events.append(
            CashEvent(
                date=due,
                description=f"{card_name} minimum {minimum.month}",
                account_id=minimum.source_account_id,
                amount_cents=-minimum.remaining_cents,
                kind="minimum",
                counter_account_id=minimum.account_id,
            )
        )
    return events


def _minimum_due_date(month: str, today: date) -> date:
    """The last day of the month after the statement month, or today when that has passed."""
    year, month_number = (int(part) for part in month.split("-", 1))
    index = year * 12 + month_number - 1 + 1
    due = _clamped_day(index, 31)
    return max(due, today)


def _deduplicate(
    scheduled: Sequence[CashEvent],
    recorded: Sequence[Transaction],
    liquid_set: set[int],
    today: date,
) -> set[int]:
    """Return indexes of scheduled events cancelled one-to-one by recorded transactions.

    Matching is deliberately strict: income cancels only scheduled income, an expense cancels only
    a regular bill, and a transfer cancels only a card minimum for the account it pays. A refund
    cancels nothing scheduled. A transfer dated today is already inside the starting balances and
    inside the minimum's paid amount, so it never cancels anything again.
    """
    consumed: set[int] = set()
    for transaction in recorded:
        amount = abs(transaction.amount_cents)
        if amount == 0:
            continue
        if (
            transaction.type is TransactionType.TRANSFER
            and transaction.occurred_on <= today
        ):
            continue
        match = _find_transaction_match(scheduled, consumed, transaction, liquid_set, amount)
        if match is not None:
            consumed.add(match)  # a transaction cancels at most one scheduled event
    return consumed


def _find_transaction_match(
    scheduled: Sequence[CashEvent],
    consumed: set[int],
    transaction: Transaction,
    liquid_set: set[int],
    amount: int,
) -> int | None:
    if transaction.type is TransactionType.INCOME:
        return _find_match(
            scheduled,
            consumed,
            transaction.occurred_on,
            transaction.account_id,
            _INCOME_KINDS,
            amount,
            required_counter=None,
        )
    if transaction.type is TransactionType.EXPENSE:
        return _find_match(
            scheduled,
            consumed,
            transaction.occurred_on,
            transaction.account_id,
            _EXPENSE_KINDS,
            amount,
            required_counter=None,
        )
    if transaction.type is TransactionType.TRANSFER:
        source = transaction.account_id
        target = transaction.transfer_account_id
        if source is None or source not in liquid_set or target is None:
            return None
        return _find_match(
            scheduled,
            consumed,
            transaction.occurred_on,
            source,
            _TRANSFER_KINDS,
            amount,
            required_counter=target,
        )
    return None  # refunds and adjustments de-duplicate nothing scheduled


def _find_match(
    scheduled: Sequence[CashEvent],
    consumed: set[int],
    occurred_on: date,
    account_id: int | None,
    compatible_kinds: frozenset[str],
    amount: int,
    *,
    required_counter: int | None,
) -> int | None:
    best: int | None = None
    best_key: tuple[int, int] | None = None
    for index, event in enumerate(scheduled):
        if index in consumed:
            continue
        if event.account_id != account_id or event.kind not in compatible_kinds:
            continue
        if required_counter is not None and event.counter_account_id != required_counter:
            continue
        expected = amount if event.kind in INFLOW_KINDS else -amount
        if event.amount_cents != expected:
            continue
        span = abs((event.date - occurred_on).days)
        if span > _MAX_DAY_SPAN:
            continue
        # Prefer the occurrence on the transaction's own date over one a day away, so a transfer
        # never cancels the wrong neighbour.
        key = (span, index)
        if best_key is None or key < best_key:
            best, best_key = index, key
    return best


def _transaction_events(
    transactions: Sequence[Transaction],
    today: date,
    end: date,
    liquid_set: set[int],
) -> list[CashEvent]:
    events: list[CashEvent] = []
    for transaction in transactions:
        if transaction.occurred_on <= today or transaction.occurred_on > end:
            continue
        amount = abs(transaction.amount_cents)
        if amount == 0:
            continue
        description = transaction.description or transaction.type.label
        if transaction.type in (TransactionType.INCOME, TransactionType.REFUND):
            if transaction.account_id in liquid_set:
                events.append(
                    CashEvent(
                        date=transaction.occurred_on,
                        description=description,
                        account_id=transaction.account_id,
                        amount_cents=amount,
                        kind="income" if transaction.type is TransactionType.INCOME else "refund",
                    )
                )
        elif transaction.type is TransactionType.EXPENSE:
            if transaction.account_id in liquid_set:
                events.append(
                    CashEvent(
                        date=transaction.occurred_on,
                        description=description,
                        account_id=transaction.account_id,
                        amount_cents=-amount,
                        kind="bill",
                    )
                )
        elif transaction.type is TransactionType.TRANSFER:
            if transaction.account_id in liquid_set:
                events.append(
                    CashEvent(
                        date=transaction.occurred_on,
                        description=description,
                        account_id=transaction.account_id,
                        amount_cents=-amount,
                        kind="transfer_out",
                    )
                )
            if transaction.transfer_account_id in liquid_set:
                events.append(
                    CashEvent(
                        date=transaction.occurred_on,
                        description=description,
                        account_id=transaction.transfer_account_id,
                        amount_cents=amount,
                        kind="transfer_in",
                    )
                )
    return events


def _event_sort_key(event: CashEvent) -> tuple[date, int, str, int]:
    return (
        event.date,
        0 if event.kind in INFLOW_KINDS else 1,
        event.description,
        event.amount_cents,
    )
