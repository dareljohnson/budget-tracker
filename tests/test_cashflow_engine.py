"""Pure cash-flow engine tests: hand-built models only, no database or UI (task 2)."""

import unittest
from datetime import date, timedelta

from budget_tracker.cashflow import (
    CardMinimum,
    CashFlowForecast,
    ForecastBill,
    forecast,
    income_dates,
)
from budget_tracker.models import (
    Account,
    AccountKind,
    Bill,
    BillFrequency,
    IncomeFrequency,
    IncomeSchedule,
    Transaction,
    TransactionType,
)

TODAY = date(2026, 11, 14)

CHECKING = Account("Checking", AccountKind.CHECKING, id=1)
SAVINGS = Account("Savings", AccountKind.SAVINGS, id=2)
CASH = Account("Cash", AccountKind.CASH, id=3)
CARD = Account("Credit Card", AccountKind.CREDIT_CARD, id=4)
LOAN = Account("Loan", AccountKind.LOAN, id=5)
ACCOUNTS = [CHECKING, SAVINGS, CASH, CARD, LOAN]


def schedule(
    *,
    name="Paycheck",
    amount_cents=100_000,
    frequency=IncomeFrequency.BIWEEKLY,
    next_date=TODAY,
    anchor_day=None,
    second_day=None,
    account_id=CHECKING.id,
    active=True,
):
    return IncomeSchedule(
        name=name,
        amount_cents=amount_cents,
        frequency=frequency,
        next_date=next_date,
        anchor_day=anchor_day if anchor_day is not None else next_date.day,
        account_id=account_id,
        second_day=second_day,
        active=active,
    )


def bill(
    *,
    name="Bill",
    amount_cents=10_000,
    frequency=BillFrequency.MONTHLY,
    next_due=TODAY,
    account_id=CHECKING.id,
    anchor_day=None,
    pay_to_account_id=None,
    active=True,
):
    return Bill(
        name=name,
        amount_cents=amount_cents,
        frequency=frequency,
        next_due=next_due,
        account_id=account_id,
        anchor_day=anchor_day if anchor_day is not None else next_due.day,
        pay_to_account_id=pay_to_account_id,
        active=active,
    )


def transaction(
    *,
    amount_cents,
    type_,
    occurred_on,
    account_id=CHECKING.id,
    transfer_account_id=None,
    description="",
):
    return Transaction(
        amount_cents=amount_cents,
        type=type_,
        category_id=None,
        occurred_on=occurred_on,
        description=description,
        account_id=account_id,
        transfer_account_id=transfer_account_id,
    )


def run(
    *,
    today=TODAY,
    horizon_days=90,
    accounts=ACCOUNTS,
    balances=None,
    bills=(),
    card_owed=None,
    card_minimums=(),
    incomes=(),
    transactions=(),
):
    return forecast(
        today=today,
        horizon_days=horizon_days,
        accounts=accounts,
        balances=balances if balances is not None else {CHECKING.id: 100_000},
        bills=bills,
        card_owed=card_owed or {},
        card_minimums=card_minimums,
        incomes=incomes,
        scheduled_transactions=transactions,
    )


class TestIncomeDates(unittest.TestCase):
    def test_weekly(self):
        s = schedule(frequency=IncomeFrequency.WEEKLY, next_date=date(2026, 11, 2))
        self.assertEqual(
            income_dates(s, date(2026, 11, 1), date(2026, 11, 23)),
            [date(2026, 11, 2), date(2026, 11, 9), date(2026, 11, 16), date(2026, 11, 23)],
        )

    def test_weekly_skips_occurrences_before_start(self):
        s = schedule(frequency=IncomeFrequency.WEEKLY, next_date=date(2026, 11, 2))
        self.assertEqual(
            income_dates(s, date(2026, 11, 5), date(2026, 11, 23)),
            [date(2026, 11, 9), date(2026, 11, 16), date(2026, 11, 23)],
        )

    def test_biweekly(self):
        s = schedule(frequency=IncomeFrequency.BIWEEKLY, next_date=date(2026, 11, 3))
        self.assertEqual(
            income_dates(s, date(2026, 11, 1), date(2026, 12, 1)),
            [date(2026, 11, 3), date(2026, 11, 17), date(2026, 12, 1)],
        )

    def test_monthly_anchor_31_clamps_through_february_and_returns_to_31(self):
        s = schedule(
            frequency=IncomeFrequency.MONTHLY,
            next_date=date(2027, 1, 31),
            anchor_day=31,
        )
        self.assertEqual(
            income_dates(s, date(2027, 1, 1), date(2027, 4, 30)),
            [date(2027, 1, 31), date(2027, 2, 28), date(2027, 3, 31), date(2027, 4, 30)],
        )

    def test_monthly_past_next_date_starts_after_start(self):
        s = schedule(
            frequency=IncomeFrequency.MONTHLY,
            next_date=date(2026, 1, 31),
            anchor_day=31,
        )
        self.assertEqual(
            income_dates(s, date(2026, 3, 1), date(2026, 4, 30)),
            [date(2026, 3, 31), date(2026, 4, 30)],
        )

    def test_monthly_future_next_date_is_not_back_dated(self):
        s = schedule(
            frequency=IncomeFrequency.MONTHLY,
            next_date=date(2026, 11, 20),
            anchor_day=20,
        )
        self.assertEqual(
            income_dates(s, date(2026, 11, 1), date(2026, 12, 31)),
            [date(2026, 11, 20), date(2026, 12, 20)],
        )

    def test_semimonthly(self):
        s = schedule(
            frequency=IncomeFrequency.SEMIMONTHLY,
            next_date=date(2026, 11, 1),
            anchor_day=15,
            second_day=1,
        )
        self.assertEqual(
            income_dates(s, date(2026, 11, 1), date(2026, 12, 31)),
            [date(2026, 11, 1), date(2026, 11, 15), date(2026, 12, 1), date(2026, 12, 15)],
        )

    def test_semimonthly_same_day_deduplicates(self):
        s = schedule(
            frequency=IncomeFrequency.SEMIMONTHLY,
            next_date=date(2027, 2, 28),
            anchor_day=31,
            second_day=30,
        )
        self.assertEqual(
            income_dates(s, date(2027, 2, 1), date(2027, 2, 28)),
            [date(2027, 2, 28)],
        )

    def test_boundaries_are_inclusive(self):
        s = schedule(frequency=IncomeFrequency.WEEKLY, next_date=date(2026, 11, 1))
        self.assertEqual(
            income_dates(s, date(2026, 11, 1), date(2026, 11, 1)),
            [date(2026, 11, 1)],
        )
        self.assertEqual(income_dates(s, date(2026, 11, 2), date(2026, 11, 7)), [])


class TestForecastBasics(unittest.TestCase):
    def test_starting_total_and_today_point(self):
        result = run(balances={CHECKING.id: 100_000, SAVINGS.id: 50_000, CARD.id: -500_000})

        self.assertIsInstance(result, CashFlowForecast)
        self.assertEqual(result.starting_cents, 150_000)
        self.assertEqual(len(result.points), 91)
        self.assertEqual(result.points[0].date, TODAY)
        self.assertEqual(result.points[0].balance_cents, 150_000)
        self.assertEqual(result.points[-1].date, TODAY + timedelta(days=90))
        self.assertEqual(result.lowest_date, TODAY)
        self.assertEqual(result.lowest_cents, 150_000)
        self.assertEqual((result.at_30, result.at_60, result.at_90), (150_000, 150_000, 150_000))
        self.assertEqual(
            result.endings_by_account,
            {
                CHECKING.id: (100_000, 100_000, 100_000),
                SAVINGS.id: (50_000, 50_000, 50_000),
                CASH.id: (0, 0, 0),
            },
        )
        self.assertFalse(result.has_income)
        self.assertEqual(result.events, [])

    def test_a_point_exists_for_every_day(self):
        result = run(horizon_days=30)
        self.assertEqual(len(result.points), 31)
        self.assertEqual(
            [point.date for point in result.points],
            [TODAY + timedelta(days=offset) for offset in range(31)],
        )

    def test_bill_recurs(self):
        b = bill(amount_cents=30_000, next_due=TODAY, anchor_day=14)
        result = run(bills=[ForecastBill(b, 30_000)])

        self.assertEqual(
            [event.date for event in result.events],
            [TODAY, date(2026, 12, 14), date(2027, 1, 14)],
        )
        self.assertTrue(all(event.kind == "bill" for event in result.events))
        self.assertTrue(all(event.amount_cents == -30_000 for event in result.events))
        self.assertEqual(result.points[0].balance_cents, 70_000)
        self.assertEqual(result.at_30, 40_000)  # Dec 14 event lands on day 30
        self.assertEqual(result.at_60, 40_000)  # Jan 13, before the Jan 14 bill
        self.assertEqual(result.at_90, 10_000)
        self.assertEqual(result.lowest_cents, 10_000)
        self.assertEqual(result.lowest_date, date(2027, 1, 14))

    def test_overdue_bill_collapses_to_today_once(self):
        b = bill(amount_cents=10_000, next_due=date(2026, 10, 1), anchor_day=1)
        result = run(bills=[ForecastBill(b, 10_000)], horizon_days=40)

        self.assertEqual([event.date for event in result.events], [TODAY, date(2026, 12, 1)])
        self.assertEqual(result.points[0].balance_cents, 90_000)

    def test_overdue_one_time_bill_emits_once_at_today(self):
        b = bill(frequency=BillFrequency.ONCE, next_due=date(2026, 10, 1))
        result = run(bills=[ForecastBill(b, 10_000)])
        self.assertEqual([event.date for event in result.events], [TODAY])

    def test_non_liquid_bill_source_is_skipped(self):
        b = bill(account_id=CARD.id)
        result = run(bills=[ForecastBill(b, 10_000)])
        self.assertEqual(result.events, [])

    def test_inactive_bill_is_skipped(self):
        b = bill(active=False)
        result = run(bills=[ForecastBill(b, 10_000)])
        self.assertEqual(result.events, [])

    def test_event_on_last_horizon_day_is_included_and_the_next_is_not(self):
        included = bill(name="Included", frequency=BillFrequency.ONCE, next_due=TODAY + timedelta(days=90))
        excluded = bill(name="Excluded", frequency=BillFrequency.ONCE, next_due=TODAY + timedelta(days=91))
        result = run(bills=[ForecastBill(included, 1_000), ForecastBill(excluded, 2_000)])
        self.assertEqual([event.description for event in result.events], ["Included"])


class TestCardBills(unittest.TestCase):
    def test_open_amount_bill_projects_once(self):
        b = bill(amount_cents=0, next_due=TODAY, pay_to_account_id=CARD.id)
        result = run(
            bills=[ForecastBill(b, 12_345)],
            card_owed={CARD.id: 50_000},
        )

        self.assertEqual(len(result.events), 1)
        event = result.events[0]
        self.assertEqual((event.date, event.account_id, event.kind), (TODAY, CHECKING.id, "card_payment"))
        self.assertEqual(event.amount_cents, -12_345)
        self.assertEqual(result.points[0].balance_cents, 87_655)

    def test_open_amount_bill_with_nothing_owed_is_skipped(self):
        b = bill(amount_cents=0, next_due=TODAY, pay_to_account_id=CARD.id)
        result = run(bills=[ForecastBill(b, 0)], card_owed={CARD.id: 0})
        self.assertEqual(result.events, [])

    def test_open_amount_overdue_bill_emits_once_at_today(self):
        b = bill(amount_cents=0, next_due=date(2026, 10, 1), pay_to_account_id=CARD.id)
        result = run(bills=[ForecastBill(b, 7_000)], card_owed={CARD.id: 7_000})
        self.assertEqual([(event.date, event.amount_cents) for event in result.events], [(TODAY, -7_000)])

    def test_fixed_card_payments_are_capped_across_occurrences(self):
        b = bill(amount_cents=15_000, next_due=TODAY, anchor_day=14, pay_to_account_id=CARD.id)
        result = run(
            bills=[ForecastBill(b, 15_000)],
            card_owed={CARD.id: 20_000},
            horizon_days=70,
        )

        self.assertEqual([event.amount_cents for event in result.events], [-15_000, -5_000])
        self.assertEqual([event.kind for event in result.events], ["card_payment", "card_payment"])

    def test_fixed_card_payment_on_a_card_owing_nothing_is_skipped(self):
        b = bill(amount_cents=15_000, pay_to_account_id=CARD.id)
        result = run(bills=[ForecastBill(b, 15_000)], card_owed={CARD.id: 0})
        self.assertEqual(result.events, [])

    def test_open_bill_reduces_the_remaining_owed_for_fixed_bills(self):
        open_bill = bill(
            name="Pay card",
            amount_cents=0,
            next_due=TODAY,
            pay_to_account_id=CARD.id,
        )
        fixed = bill(
            name="Extra card payment",
            amount_cents=10_000,
            next_due=TODAY + timedelta(days=1),
            frequency=BillFrequency.ONCE,
            pay_to_account_id=CARD.id,
        )
        result = run(
            bills=[ForecastBill(open_bill, 8_000), ForecastBill(fixed, 10_000)],
            card_owed={CARD.id: 8_000},
        )
        self.assertEqual([event.amount_cents for event in result.events], [-8_000])


class TestCardMinimums(unittest.TestCase):
    def test_minimum_is_due_at_the_end_of_the_following_month(self):
        minimum = CardMinimum(
            account_id=CARD.id,
            month="2026-11",
            remaining_cents=5_000,
            source_account_id=CHECKING.id,
        )
        result = run(today=date(2026, 12, 15), card_minimums=[minimum], horizon_days=30)

        self.assertEqual(len(result.events), 1)
        event = result.events[0]
        self.assertEqual((event.date, event.account_id, event.kind), (date(2026, 12, 31), CHECKING.id, "minimum"))
        self.assertEqual(event.amount_cents, -5_000)

    def test_past_due_minimum_is_projected_today(self):
        minimum = CardMinimum(
            account_id=CARD.id,
            month="2026-09",
            remaining_cents=4_000,
            source_account_id=SAVINGS.id,
        )
        result = run(today=date(2026, 12, 15), card_minimums=[minimum])

        self.assertEqual([(event.date, event.account_id, event.amount_cents) for event in result.events],
                         [(date(2026, 12, 15), SAVINGS.id, -4_000)])

    def test_minimum_from_a_non_liquid_account_is_skipped(self):
        minimum = CardMinimum(
            account_id=CARD.id,
            month="2026-11",
            remaining_cents=5_000,
            source_account_id=CARD.id,
        )
        result = run(today=date(2026, 12, 15), card_minimums=[minimum])
        self.assertEqual(result.events, [])


class TestIncomeForecast(unittest.TestCase):
    def test_income_starts_strictly_after_today(self):
        s = schedule(
            frequency=IncomeFrequency.BIWEEKLY,
            next_date=TODAY,
            amount_cents=100_000,
        )
        result = run(incomes=[s])

        self.assertTrue(result.has_income)
        self.assertEqual(
            [event.date for event in result.events],
            [date(2026, 11, 28), date(2026, 12, 12), date(2026, 12, 26), date(2027, 1, 9), date(2027, 1, 23), date(2027, 2, 6)],
        )
        self.assertTrue(all(event.kind == "income" and event.amount_cents == 100_000 for event in result.events))

    def test_monthly_income_uses_anchor_day(self):
        s = schedule(
            frequency=IncomeFrequency.MONTHLY,
            next_date=date(2026, 11, 30),
            anchor_day=31,
        )
        result = run(incomes=[s])
        self.assertEqual(
            [event.date for event in result.events],
            [date(2026, 11, 30), date(2026, 12, 31), date(2027, 1, 31)],
        )

    def test_inactive_income_is_skipped(self):
        result = run(incomes=[schedule(active=False)])
        self.assertFalse(result.has_income)
        self.assertEqual(result.events, [])

    def test_income_on_a_non_liquid_account_is_skipped(self):
        result = run(incomes=[schedule(account_id=CARD.id)])
        self.assertEqual(result.events, [])


class TestTransactions(unittest.TestCase):
    def test_future_expense_and_refund(self):
        expense = transaction(
            amount_cents=1_000,
            type_=TransactionType.EXPENSE,
            occurred_on=TODAY + timedelta(days=5),
            description="Groceries",
        )
        refund = transaction(
            amount_cents=400,
            type_=TransactionType.REFUND,
            occurred_on=TODAY + timedelta(days=6),
            description="Return",
        )
        result = run(transactions=[expense, refund])

        self.assertEqual(
            [(event.date, event.kind, event.amount_cents) for event in result.events],
            [(TODAY + timedelta(days=5), "bill", -1_000), (TODAY + timedelta(days=6), "refund", 400)],
        )

    def test_todays_transactions_are_not_applied(self):
        today_expense = transaction(
            amount_cents=1_000,
            type_=TransactionType.EXPENSE,
            occurred_on=TODAY,
        )
        result = run(transactions=[today_expense])
        self.assertEqual(result.events, [])
        self.assertEqual(result.points[0].balance_cents, result.starting_cents)

    def test_adjustments_are_skipped(self):
        adjustment = transaction(
            amount_cents=-5_000,
            type_=TransactionType.ADJUSTMENT,
            occurred_on=TODAY + timedelta(days=2),
        )
        result = run(transactions=[adjustment])
        self.assertEqual(result.events, [])

    def test_transactions_after_the_horizon_are_skipped(self):
        later = transaction(
            amount_cents=1_000,
            type_=TransactionType.EXPENSE,
            occurred_on=TODAY + timedelta(days=91),
        )
        result = run(transactions=[later])
        self.assertEqual(result.events, [])

    def test_transfer_is_expanded_both_sides(self):
        transfer = transaction(
            amount_cents=4_000,
            type_=TransactionType.TRANSFER,
            occurred_on=TODAY + timedelta(days=3),
            account_id=CHECKING.id,
            transfer_account_id=SAVINGS.id,
            description="Move to savings",
        )
        result = run(transactions=[transfer])

        self.assertEqual(
            [(event.account_id, event.kind, event.amount_cents) for event in result.events],
            [(SAVINGS.id, "transfer_in", 4_000), (CHECKING.id, "transfer_out", -4_000)],
        )
        self.assertEqual(result.points[-1].balance_cents, result.starting_cents)
        self.assertEqual(result.endings_by_account[CHECKING.id], (96_000, 96_000, 96_000))
        self.assertEqual(result.endings_by_account[SAVINGS.id], (4_000, 4_000, 4_000))

    def test_transfer_into_a_card_only_applies_the_liquid_side(self):
        transfer = transaction(
            amount_cents=3_000,
            type_=TransactionType.TRANSFER,
            occurred_on=TODAY + timedelta(days=3),
            account_id=CHECKING.id,
            transfer_account_id=CARD.id,
        )
        result = run(transactions=[transfer])
        self.assertEqual(
            [(event.account_id, event.kind, event.amount_cents) for event in result.events],
            [(CHECKING.id, "transfer_out", -3_000)],
        )


class TestDeduplication(unittest.TestCase):
    def test_a_bill_due_today_is_cancelled_by_a_transaction_today(self):
        b = bill(amount_cents=50_000, frequency=BillFrequency.ONCE, next_due=TODAY)
        recorded = transaction(
            amount_cents=50_000,
            type_=TransactionType.EXPENSE,
            occurred_on=TODAY,
        )
        result = run(bills=[ForecastBill(b, 50_000)], transactions=[recorded])

        self.assertEqual(result.events, [])
        self.assertEqual(result.points[0].balance_cents, result.starting_cents)

    def test_one_transaction_cancels_only_one_matching_event(self):
        first = schedule(name="Pay A", frequency=IncomeFrequency.MONTHLY, next_date=TODAY + timedelta(days=1))
        second = schedule(name="Pay B", frequency=IncomeFrequency.MONTHLY, next_date=TODAY + timedelta(days=1))
        recorded = transaction(
            amount_cents=100_000,
            type_=TransactionType.INCOME,
            occurred_on=TODAY + timedelta(days=1),
            description="Deposit",
        )
        result = run(incomes=[first, second], transactions=[recorded], horizon_days=2)

        self.assertEqual(len(result.events), 2)
        self.assertTrue(all(event.kind == "income" for event in result.events))
        self.assertEqual(sum(event.amount_cents for event in result.events), 200_000)

    def test_matching_tolerates_one_day_and_requires_account_amount_and_direction(self):
        b = bill(amount_cents=10_000, frequency=BillFrequency.ONCE, next_due=TODAY + timedelta(days=2))
        near = transaction(
            amount_cents=10_000,
            type_=TransactionType.EXPENSE,
            occurred_on=TODAY + timedelta(days=1),
        )
        result = run(bills=[ForecastBill(b, 10_000)], transactions=[near])
        self.assertEqual([event.kind for event in result.events], ["bill"])

        wrong_amount = transaction(
            amount_cents=9_000,
            type_=TransactionType.EXPENSE,
            occurred_on=TODAY + timedelta(days=1),
        )
        result = run(bills=[ForecastBill(b, 10_000)], transactions=[wrong_amount])
        self.assertEqual(len(result.events), 2)

        wrong_account = transaction(
            amount_cents=10_000,
            type_=TransactionType.EXPENSE,
            occurred_on=TODAY + timedelta(days=1),
            account_id=SAVINGS.id,
        )
        result = run(bills=[ForecastBill(b, 10_000)], transactions=[wrong_account])
        self.assertEqual(len(result.events), 2)

        wrong_direction = transaction(
            amount_cents=10_000,
            type_=TransactionType.INCOME,
            occurred_on=TODAY + timedelta(days=1),
        )
        result = run(bills=[ForecastBill(b, 10_000)], transactions=[wrong_direction])
        self.assertEqual(len(result.events), 2)

    def test_far_apart_dates_do_not_match(self):
        b = bill(
            amount_cents=10_000,
            frequency=BillFrequency.ONCE,
            next_due=TODAY + timedelta(days=5),
        )
        recorded = transaction(
            amount_cents=10_000,
            type_=TransactionType.EXPENSE,
            occurred_on=TODAY + timedelta(days=2),
        )
        result = run(bills=[ForecastBill(b, 10_000)], transactions=[recorded])
        self.assertEqual(len(result.events), 2)

    def test_a_recorded_transfer_cancels_a_card_payment_bill(self):
        b = bill(amount_cents=5_000, next_due=TODAY + timedelta(days=2), pay_to_account_id=CARD.id)
        recorded = transaction(
            amount_cents=5_000,
            type_=TransactionType.TRANSFER,
            occurred_on=TODAY + timedelta(days=2),
            account_id=CHECKING.id,
            transfer_account_id=CARD.id,
        )
        result = run(
            bills=[ForecastBill(b, 5_000)],
            card_owed={CARD.id: 5_000},
            transactions=[recorded],
        )

        self.assertEqual(
            [(event.kind, event.amount_cents) for event in result.events],
            [("transfer_out", -5_000)],
        )


class TestOrderingAndSummary(unittest.TestCase):
    def test_inflows_sort_before_outflows_within_a_day(self):
        b = bill(name="Rent", amount_cents=50_000, next_due=TODAY + timedelta(days=1))
        s = schedule(
            name="Pay",
            frequency=IncomeFrequency.WEEKLY,
            next_date=TODAY + timedelta(days=1),
        )
        result = run(bills=[ForecastBill(b, 50_000)], incomes=[s])

        self.assertEqual([event.kind for event in result.events[:2]], ["income", "bill"])

    def test_lowest_point_is_negative_when_outflows_exceed_the_balance(self):
        b = bill(
            amount_cents=10_000,
            frequency=BillFrequency.ONCE,
            next_due=TODAY + timedelta(days=1),
        )
        result = run(balances={CHECKING.id: 5_000}, bills=[ForecastBill(b, 10_000)])

        self.assertEqual(result.lowest_cents, -5_000)
        self.assertEqual(result.lowest_date, TODAY + timedelta(days=1))

    def test_checkpoints_and_endings_come_from_the_full_horizon(self):
        b = bill(
            amount_cents=10_000,
            frequency=BillFrequency.ONCE,
            next_due=TODAY + timedelta(days=40),
        )
        result = run(
            balances={CHECKING.id: 100_000, SAVINGS.id: 20_000},
            bills=[ForecastBill(b, 10_000)],
        )

        self.assertEqual((result.at_30, result.at_60, result.at_90), (120_000, 110_000, 110_000))
        self.assertEqual(
            result.endings_by_account,
            {
                CHECKING.id: (100_000, 90_000, 90_000),
                SAVINGS.id: (20_000, 20_000, 20_000),
                CASH.id: (0, 0, 0),
            },
        )
        self.assertEqual(result.lowest_cents, 110_000)
        self.assertEqual(result.lowest_date, TODAY + timedelta(days=40))


if __name__ == "__main__":
    unittest.main()
