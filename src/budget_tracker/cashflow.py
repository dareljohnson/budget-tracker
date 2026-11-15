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
OUTFLOW_KINDS = frozenset({"bill", "card_payment", "minimum", "transfer_out"})

_ONE_DAY = timedelta(days=1)
_RECHECK_DAYS = (30, 60, 90)
_MAX_DAY_SPAN = 1


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
    """
    days = max(0, horizon_days)
    end = today + timedelta(days=days)
    liquid_ids = [account.id for account in accounts if account.id is not None and account.kind in LIQUID_KINDS]
    liquid_set = set(liquid_ids)
    account_names = {account.id: account.name for account in accounts if account.id is not None}

    start_balances = {account_id: int(balances.get(account_id, 0)) for account_id in liquid_ids}
    starting_cents = sum(start_balances.values())

    scheduled = _scheduled_events(
        today=today,
        end=end,
        bills=bills,
        card_owed=card_owed,
        card_minimums=card_minimums,
        incomes=incomes,
        liquid_set=liquid_set,
        account_names=account_names,
    )

    recorded = [
        transaction
        for transaction in scheduled_transactions
        if transaction.occurred_on >= today and transaction.type is not TransactionType.ADJUSTMENT
    ]
    consumed = _deduplicate(scheduled, recorded, liquid_set)
    events = [event for index, event in enumerate(scheduled) if index not in consumed]
    events.extend(_transaction_events(scheduled_transactions, today, end, liquid_set))
    events.sort(key=_event_sort_key)

    by_date: dict[date, list[CashEvent]] = {}
    for event in events:
        if today <= event.date <= end:
            by_date.setdefault(event.date, []).append(event)

    balances_now = dict(start_balances)
    points: list[CashFlowPoint] = []
    snapshots: dict[int, dict[int, int]] = {}
    for offset in range(days + 1):
        day = today + timedelta(days=offset)
        for event in by_date.get(day, ()):  # order does not change the day-end total
            balances_now[event.account_id] = balances_now.get(event.account_id, 0) + event.amount_cents
        points.append(CashFlowPoint(day, sum(balances_now.values())))
        if offset in _RECHECK_DAYS:
            snapshots[offset] = dict(balances_now)

    if points:
        lowest = min(points, key=lambda point: (point.balance_cents, point.date))
        lowest_date, lowest_cents = lowest.date, lowest.balance_cents
    else:
        lowest_date, lowest_cents = today, starting_cents

    def total_at(offset: int) -> int:
        snapshot = snapshots.get(offset)
        if snapshot is not None:
            return sum(snapshot.values())
        return points[-1].balance_cents if points else starting_cents

    endings_by_account: dict[int, tuple[int, int, int]] = {}
    final_balances = snapshots.get(days, balances_now) if points else start_balances
    for account_id in liquid_ids:
        values: list[int] = []
        for offset in _RECHECK_DAYS:
            snapshot = snapshots.get(offset)
            values.append((snapshot if snapshot is not None else final_balances).get(account_id, 0))
        endings_by_account[account_id] = (values[0], values[1], values[2])

    return CashFlowForecast(
        points=points,
        events=events,
        starting_cents=starting_cents,
        lowest_date=lowest_date,
        lowest_cents=lowest_cents,
        at_30=total_at(30),
        at_60=total_at(60),
        at_90=total_at(90),
        endings_by_account=endings_by_account,
        has_income=any(event.kind == "income" for event in events),
    )


def _scheduled_events(
    *,
    today: date,
    end: date,
    bills: Sequence[ForecastBill],
    card_owed: Mapping[int, int],
    card_minimums: Sequence[CardMinimum],
    incomes: Sequence[IncomeSchedule],
    liquid_set: set[int],
    account_names: Mapping[int, str],
) -> list[CashEvent]:
    events: list[CashEvent] = []
    remaining_owed = {int(account_id): int(cents) for account_id, cents in card_owed.items()}
    for entry in bills:
        events.extend(_bill_events(entry, today, end, liquid_set, remaining_owed))
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
    events.sort(key=_event_sort_key)
    return events


def _bill_events(
    entry: ForecastBill,
    today: date,
    end: date,
    liquid_set: set[int],
    remaining_owed: dict[int, int],
) -> list[CashEvent]:
    bill = entry.bill
    if not bill.active or bill.account_id not in liquid_set:
        return []
    occurrences = _bill_dates(bill, today, end)
    if not occurrences:
        return []
    if not bill.is_card_payment:
        return [
            CashEvent(
                date=occurrence,
                description=bill.name,
                account_id=bill.account_id,
                amount_cents=-abs(entry.amount_cents),
                kind="bill",
            )
            for occurrence in occurrences
            if entry.amount_cents > 0
        ]

    target = bill.pay_to_account_id
    if target is None:  # defensive: is_card_payment implies a target
        return []
    remaining = remaining_owed.get(target)
    if bill.amount_cents == 0:
        # Pay-the-full-balance bills are projected once; recurrence is deliberately ignored.
        available = entry.amount_cents if remaining is None else min(entry.amount_cents, remaining)
        if available <= 0:
            return []
        if remaining is not None:
            remaining_owed[target] = max(0, remaining - available)
        return [
            CashEvent(
                date=occurrences[0],
                description=bill.name,
                account_id=bill.account_id,
                amount_cents=-available,
                kind="card_payment",
            )
        ]

    if remaining is None or remaining <= 0:
        return []
    events: list[CashEvent] = []
    for occurrence in occurrences:
        if remaining <= 0:
            break
        amount = min(abs(entry.amount_cents), remaining)
        if amount <= 0:
            break
        events.append(
            CashEvent(
                date=occurrence,
                description=bill.name,
                account_id=bill.account_id,
                amount_cents=-amount,
                kind="card_payment",
            )
        )
        remaining -= amount
    remaining_owed[target] = remaining
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
) -> set[int]:
    """Return indexes of scheduled events cancelled one-to-one by recorded transactions."""
    consumed: set[int] = set()
    for transaction in recorded:
        amount = abs(transaction.amount_cents)
        if amount == 0:
            continue
        for account_id, inflow in _match_sides(transaction):
            if account_id is None or account_id not in liquid_set:
                continue
            match = _find_match(scheduled, consumed, transaction.occurred_on, account_id, inflow, amount)
            if match is not None:
                consumed.add(match)
                break  # a transaction cancels at most one scheduled event
    return consumed


def _match_sides(transaction: Transaction) -> list[tuple[int | None, bool]]:
    """Account/direction pairs a recorded transaction can cancel, most specific first."""
    if transaction.type in (TransactionType.INCOME, TransactionType.REFUND):
        return [(transaction.account_id, True)]
    if transaction.type is TransactionType.EXPENSE:
        return [(transaction.account_id, False)]
    if transaction.type is TransactionType.TRANSFER:
        return [
            (transaction.account_id, False),
            (transaction.transfer_account_id, True),
        ]
    return []


def _find_match(
    scheduled: Sequence[CashEvent],
    consumed: set[int],
    occurred_on: date,
    account_id: int,
    inflow: bool,
    amount: int,
) -> int | None:
    wanted_kinds = INFLOW_KINDS if inflow else OUTFLOW_KINDS
    for index, event in enumerate(scheduled):
        if index in consumed:
            continue
        if event.account_id != account_id or event.kind not in wanted_kinds:
            continue
        if (event.amount_cents > 0) != inflow or abs(event.amount_cents) != amount:
            continue
        if abs((event.date - occurred_on).days) > _MAX_DAY_SPAN:
            continue
        return index
    return None


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
