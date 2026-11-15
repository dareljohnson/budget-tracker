"""Service assembly tests for cash-flow forecasts and income-schedule CRUD (task 3)."""

import os
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

from budget_tracker.db import connect
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
from budget_tracker.repository import Repository, RepositoryError
from budget_tracker.services import BudgetService

TODAY = date(2026, 11, 14)


class Base(unittest.TestCase):
    def setUp(self) -> None:
        # Never touch the owner's real data directory.
        self.home = tempfile.TemporaryDirectory()
        self.addCleanup(self.home.cleanup)
        self.previous_home = os.environ.get("BUDGET_TRACKER_HOME")
        os.environ["BUDGET_TRACKER_HOME"] = self.home.name
        self.addCleanup(self._restore_home)

        self.repo = Repository(connect(Path(self.home.name) / "budget_tracker.db"))
        self.addCleanup(self.repo.close)
        self.service = BudgetService(self.repo)

        accounts = {account.name: account for account in self.repo.list_accounts()}
        self.checking = accounts["Checking"]
        self.card = accounts["Credit Card"]
        self.cash = accounts["Cash"]
        self.categories = {category.name: category for category in self.repo.list_categories()}

    def _restore_home(self) -> None:
        if self.previous_home is None:
            os.environ.pop("BUDGET_TRACKER_HOME", None)
        else:
            os.environ["BUDGET_TRACKER_HOME"] = self.previous_home

    def add_account(self, name: str, kind: AccountKind) -> Account:
        return self.repo.add_account(Account(name, kind))

    def add_transaction(
        self,
        cents: int,
        type_: TransactionType,
        day: date,
        *,
        account: Account | None = None,
        category: str | None = None,
        transfer_account: Account | None = None,
        description: str = "",
    ) -> Transaction:
        return self.repo.add_transaction(
            Transaction(
                amount_cents=cents,
                type=type_,
                category_id=self.categories[category].id if category else None,
                occurred_on=day,
                description=description,
                account_id=(account or self.checking).id,
                transfer_account_id=transfer_account.id if transfer_account else None,
            )
        )

    def add_bill(
        self,
        name: str,
        cents: int,
        *,
        day: date = TODAY,
        frequency: BillFrequency = BillFrequency.ONCE,
        account: Account | None = None,
        pay_to: Account | None = None,
        active: bool = True,
    ) -> Bill:
        category_id = None if pay_to is not None else self.categories["Housing"].id
        return self.repo.add_bill(
            Bill(
                name=name,
                amount_cents=cents,
                frequency=frequency,
                next_due=day,
                account_id=(account or self.checking).id,
                category_id=category_id,
                pay_to_account_id=pay_to.id if pay_to else None,
                anchor_day=day.day,
                active=active,
            )
        )

    def add_income(
        self,
        *,
        name: str = "Paycheck",
        cents: int = 100_000,
        frequency: IncomeFrequency = IncomeFrequency.BIWEEKLY,
        next_date: date = TODAY + timedelta(days=2),
        anchor_day: int | None = None,
        second_day: int | None = None,
        account: Account | None = None,
        active: bool = True,
    ) -> IncomeSchedule:
        return self.service.add_income_schedule(
            IncomeSchedule(
                name=name,
                amount_cents=cents,
                frequency=frequency,
                next_date=next_date,
                anchor_day=anchor_day if anchor_day is not None else next_date.day,
                second_day=second_day,
                account_id=(account or self.checking).id,
                active=active,
            )
        )

    def add_card_balance(self, cents: int = 20_000, day: date = TODAY - timedelta(days=10)) -> None:
        self.add_transaction(cents, TransactionType.EXPENSE, day, account=self.card, category="Groceries")


class TestLiquidBalances(Base):
    def test_starting_balance_is_liquid_only_and_as_of_today(self):
        savings = self.add_account("Savings", AccountKind.SAVINGS)
        self.add_transaction(100_000, TransactionType.INCOME, TODAY - timedelta(days=1), category="Salary")
        self.add_transaction(20_000, TransactionType.EXPENSE, TODAY, category="Groceries")
        self.add_transaction(5_000, TransactionType.EXPENSE, TODAY - timedelta(days=2), account=savings,
                             category="Groceries")
        self.add_transaction(30_000, TransactionType.EXPENSE, TODAY - timedelta(days=1), account=self.card,
                             category="Groceries")
        self.add_transaction(9_999, TransactionType.EXPENSE, TODAY + timedelta(days=5), category="Groceries")

        expected = (
            self.repo.account_balance_as_of(self.checking.id, TODAY)
            + self.repo.account_balance_as_of(savings.id, TODAY)
            + self.repo.account_balance_as_of(self.cash.id, TODAY)
        )
        self.assertEqual(expected, 75_000)

        result = self.service.cash_flow(TODAY)

        self.assertEqual(result.starting_cents, 75_000)
        self.assertEqual(
            set(result.endings_by_account),
            {self.checking.id, savings.id, self.cash.id},
        )

    def test_no_liquid_accounts_returns_an_empty_forecast(self):
        for account in self.repo.list_accounts():
            if not account.kind.is_liability:
                self.repo.delete_account(account.id)

        result = self.service.cash_flow(TODAY)

        self.assertEqual(result.starting_cents, 0)
        self.assertEqual(result.points, [])
        self.assertEqual(result.events, [])
        self.assertEqual(result.endings_by_account, {})
        self.assertEqual((result.at_30, result.at_60, result.at_90), (0, 0, 0))
        self.assertEqual(result.lowest_cents, 0)
        self.assertFalse(result.has_income)


class TestBills(Base):
    def test_active_regular_bill_becomes_a_forecast_event(self):
        self.add_bill("Rent", 12_000)

        result = self.service.cash_flow(TODAY)

        self.assertEqual(
            [(event.date, event.description, event.kind, event.amount_cents) for event in result.events],
            [(TODAY, "Rent", "bill", -12_000)],
        )

    def test_inactive_bills_are_excluded(self):
        self.add_bill("Rent", 12_000, active=False)

        result = self.service.cash_flow(TODAY)

        self.assertEqual(result.events, [])

    def test_open_card_bill_projects_once_net_of_transfers_before_its_due_date(self):
        self.add_card_balance(50_000, TODAY - timedelta(days=2))
        self.add_transaction(
            20_000,
            TransactionType.TRANSFER,
            TODAY + timedelta(days=5),
            transfer_account=self.card,
            description="Card payment",
        )
        self.add_bill(
            "Pay card",
            0,
            day=TODAY + timedelta(days=10),
            frequency=BillFrequency.MONTHLY,
            pay_to=self.card,
        )

        result = self.service.cash_flow(TODAY)

        card_events = [event for event in result.events if event.kind == "card_payment"]
        self.assertEqual(len(card_events), 1)
        event = card_events[0]
        self.assertEqual(event.date, TODAY + timedelta(days=10))
        self.assertEqual(event.account_id, self.checking.id)
        self.assertEqual(event.counter_account_id, self.card.id)
        self.assertEqual(event.amount_cents, -30_000)

    def test_open_card_bill_ignores_transfers_after_its_due_date(self):
        self.add_card_balance(50_000, TODAY - timedelta(days=2))
        self.add_transaction(
            20_000,
            TransactionType.TRANSFER,
            TODAY + timedelta(days=40),
            transfer_account=self.card,
            description="Later card payment",
        )
        self.add_bill(
            "Pay card",
            0,
            day=TODAY + timedelta(days=10),
            frequency=BillFrequency.MONTHLY,
            pay_to=self.card,
        )

        result = self.service.cash_flow(TODAY)

        # The later transfer is applied on its own date and must not shrink the open bill, which
        # only nets transfers made before its due date.
        self.assertEqual(
            [(event.date, event.amount_cents) for event in result.events if event.kind == "card_payment"],
            [(TODAY + timedelta(days=10), -50_000)],
        )
        self.assertEqual(
            [(event.date, event.amount_cents) for event in result.events if event.kind == "transfer_out"],
            [(TODAY + timedelta(days=40), -20_000)],
        )

    def test_open_card_bill_with_nothing_owed_is_skipped(self):
        self.add_bill("Pay card", 0, pay_to=self.card)

        result = self.service.cash_flow(TODAY)

        self.assertEqual(result.events, [])

    def test_two_open_card_bills_project_the_balance_only_once(self):
        self.add_card_balance(50_000, TODAY - timedelta(days=2))
        self.add_bill(
            "Pay card first",
            0,
            day=TODAY + timedelta(days=5),
            frequency=BillFrequency.MONTHLY,
            pay_to=self.card,
        )
        self.add_bill(
            "Pay card second",
            0,
            day=TODAY + timedelta(days=20),
            frequency=BillFrequency.MONTHLY,
            pay_to=self.card,
        )

        result = self.service.cash_flow(TODAY)

        card_events = [event for event in result.events if event.kind == "card_payment"]
        self.assertEqual(len(card_events), 1)
        self.assertEqual(card_events[0].date, TODAY + timedelta(days=5))
        self.assertEqual(card_events[0].amount_cents, -50_000)

    def test_fixed_card_payment_is_capped_at_today_owed(self):
        self.add_card_balance(10_000, TODAY - timedelta(days=1))
        self.add_bill("Card payment", 15_000, frequency=BillFrequency.MONTHLY, pay_to=self.card)

        result = self.service.cash_flow(TODAY)

        card_events = [event for event in result.events if event.kind == "card_payment"]
        self.assertEqual([event.amount_cents for event in card_events], [-10_000])

    def test_fixed_card_payment_on_a_zero_owed_card_is_skipped(self):
        self.add_bill("Card payment", 15_000, pay_to=self.card)

        result = self.service.cash_flow(TODAY)

        self.assertEqual([event for event in result.events if event.kind == "card_payment"], [])


class TestCardMinimums(Base):
    def test_minimum_is_due_at_the_end_of_the_following_month(self):
        self.add_card_balance()
        self.service.record_minimum_payment(self.card.id, "2026-11", 5_000)

        result = self.service.cash_flow(TODAY)

        minimums = [event for event in result.events if event.kind == "minimum"]
        self.assertEqual(len(minimums), 1)
        event = minimums[0]
        self.assertEqual(event.date, date(2026, 12, 31))
        self.assertEqual(event.account_id, self.checking.id)
        self.assertEqual(event.counter_account_id, self.card.id)
        self.assertEqual(event.amount_cents, -5_000)

    def test_every_unpaid_month_is_projected_including_past_due(self):
        self.add_card_balance()
        self.service.record_minimum_payment(self.card.id, "2026-09", 3_000)
        self.service.record_minimum_payment(self.card.id, "2026-10", 4_000)

        result = self.service.cash_flow(TODAY)

        minimums = [(event.date, event.amount_cents) for event in result.events if event.kind == "minimum"]
        self.assertEqual(minimums, [(TODAY, -3_000), (date(2026, 11, 30), -4_000)])

    def test_minimum_uses_todays_status_not_a_future_payment(self):
        self.add_card_balance()
        self.service.record_minimum_payment(self.card.id, "2026-09", 3_000)
        self.add_transaction(3_000, TransactionType.TRANSFER, TODAY + timedelta(days=2),
                             transfer_account=self.card)

        result = self.service.cash_flow(TODAY)

        minimums = [event for event in result.events if event.kind == "minimum"]
        self.assertEqual([(event.date, event.amount_cents) for event in minimums], [(TODAY, -3_000)])

    def test_minimum_is_paid_from_the_first_checking_account(self):
        self.add_account("A Cash", AccountKind.CASH)
        first_checking = self.add_account("A Checking", AccountKind.CHECKING)
        # "A Cash" sorts before "A Checking", so only the checking preference picks the right one.
        self.assertEqual(self.repo.list_accounts()[0].name, "A Cash")
        self.add_card_balance()
        self.service.record_minimum_payment(self.card.id, "2026-11", 5_000)

        result = self.service.cash_flow(TODAY)

        minimums = [event for event in result.events if event.kind == "minimum"]
        self.assertEqual([event.account_id for event in minimums], [first_checking.id])

    def test_minimum_falls_back_to_the_first_liquid_account(self):
        self.repo.delete_account(self.checking.id)
        self.add_card_balance()
        self.service.record_minimum_payment(self.card.id, "2026-11", 5_000)

        result = self.service.cash_flow(TODAY)

        minimums = [event for event in result.events if event.kind == "minimum"]
        self.assertEqual([event.account_id for event in minimums], [self.cash.id])

    def test_card_payment_bill_suppresses_all_of_a_cards_minimums(self):
        self.add_card_balance()
        self.service.record_minimum_payment(self.card.id, "2026-09", 3_000)
        self.service.record_minimum_payment(self.card.id, "2026-10", 4_000)
        self.add_bill(
            "Card payment",
            5_000,
            day=TODAY + timedelta(days=5),
            frequency=BillFrequency.MONTHLY,
            pay_to=self.card,
        )

        result = self.service.cash_flow(TODAY)

        self.assertEqual([event for event in result.events if event.kind == "minimum"], [])
        card_payments = [event.amount_cents for event in result.events if event.kind == "card_payment"]
        self.assertTrue(card_payments)
        self.assertTrue(all(amount == -5_000 for amount in card_payments))

    def test_card_payment_bill_from_a_non_liquid_account_still_suppresses_minimums(self):
        loan = self.add_account("Loan", AccountKind.LOAN)
        self.add_card_balance()
        self.service.record_minimum_payment(self.card.id, "2026-11", 5_000)
        self.add_bill(
            "Card payment",
            5_000,
            day=TODAY + timedelta(days=5),
            frequency=BillFrequency.MONTHLY,
            account=loan,
            pay_to=self.card,
        )

        result = self.service.cash_flow(TODAY)

        self.assertEqual([event for event in result.events if event.kind == "minimum"], [])
        self.assertEqual([event for event in result.events if event.kind == "card_payment"], [])

    def test_card_payment_bill_outside_the_horizon_does_not_suppress_minimums(self):
        self.add_card_balance()
        self.service.record_minimum_payment(self.card.id, "2026-11", 5_000)
        self.add_bill("Card payment", 5_000, day=TODAY + timedelta(days=100), pay_to=self.card)

        result = self.service.cash_flow(TODAY)

        self.assertEqual(
            [event.amount_cents for event in result.events if event.kind == "minimum"],
            [-5_000],
        )
        self.assertEqual([event for event in result.events if event.kind == "card_payment"], [])

    def test_no_minimums_when_the_card_owes_nothing(self):
        self.service.record_minimum_payment(self.card.id, "2026-11", 5_000)

        result = self.service.cash_flow(TODAY)

        self.assertEqual([event for event in result.events if event.kind == "minimum"], [])


class TestTransactionsAndIncome(Base):
    def test_future_transactions_apply_and_adjustments_are_excluded(self):
        self.add_transaction(100_000, TransactionType.INCOME, TODAY - timedelta(days=1), category="Salary")
        self.add_transaction(5_000, TransactionType.EXPENSE, TODAY + timedelta(days=3), category="Groceries",
                             description="Groceries")
        self.add_transaction(7_000, TransactionType.INCOME, TODAY + timedelta(days=4), category="Freelance",
                             description="Side job")
        self.add_transaction(-1_000, TransactionType.ADJUSTMENT, TODAY + timedelta(days=2))
        self.add_transaction(2_000, TransactionType.EXPENSE, TODAY, category="Groceries")

        result = self.service.cash_flow(TODAY)

        self.assertEqual(
            [(event.date, event.kind, event.amount_cents) for event in result.events],
            [
                (TODAY + timedelta(days=3), "bill", -5_000),
                (TODAY + timedelta(days=4), "income", 7_000),
            ],
        )
        self.assertEqual(result.starting_cents, 98_000)

    def test_active_income_schedules_are_included_and_inactive_ones_are_not(self):
        self.add_income(name="Paycheck")
        self.add_income(name="Old job", active=False)

        result = self.service.cash_flow(TODAY)

        self.assertEqual(
            [(event.date, event.description, event.kind) for event in result.events],
            [
                (TODAY + timedelta(days=2), "Paycheck", "income"),
                (TODAY + timedelta(days=16), "Paycheck", "income"),
                (TODAY + timedelta(days=30), "Paycheck", "income"),
                (TODAY + timedelta(days=44), "Paycheck", "income"),
                (TODAY + timedelta(days=58), "Paycheck", "income"),
                (TODAY + timedelta(days=72), "Paycheck", "income"),
                (TODAY + timedelta(days=86), "Paycheck", "income"),
            ],
        )
        self.assertTrue(result.has_income)


class TestHorizonSlicing(Base):
    def setUp(self) -> None:
        super().setUp()
        self.add_transaction(100_000, TransactionType.INCOME, TODAY - timedelta(days=1), category="Salary")
        self.add_income(
            cents=100_000,
            frequency=IncomeFrequency.MONTHLY,
            next_date=date(2026, 11, 20),
            anchor_day=20,
        )

    def test_thirty_days_slices_points_events_and_lowest_but_keeps_checkpoints(self):
        result = self.service.cash_flow(TODAY, horizon_days=30)

        self.assertEqual(len(result.points), 31)
        self.assertEqual(result.points[0].date, TODAY)
        self.assertEqual(result.points[-1].date, TODAY + timedelta(days=30))
        self.assertEqual([event.date for event in result.events], [date(2026, 11, 20)])
        self.assertEqual((result.lowest_cents, result.lowest_date), (100_000, TODAY))
        # Checkpoints and endings come from the full 90-day projection.
        self.assertEqual((result.at_30, result.at_60, result.at_90), (200_000, 300_000, 400_000))
        self.assertEqual(
            result.endings_by_account[self.checking.id],
            (200_000, 300_000, 400_000),
        )
        self.assertTrue(result.has_income)

    def test_lowest_point_is_recomputed_for_the_slice(self):
        self.repo.delete_income_schedule(self.service.list_income_schedules()[0].id)
        self.add_bill("Later", 10_000, day=TODAY + timedelta(days=40))

        short = self.service.cash_flow(TODAY, horizon_days=30)
        full = self.service.cash_flow(TODAY, horizon_days=90)

        self.assertEqual((short.lowest_cents, short.lowest_date), (100_000, TODAY))
        self.assertEqual(short.events, [])
        self.assertEqual((full.lowest_cents, full.lowest_date), (90_000, TODAY + timedelta(days=40)))
        self.assertEqual(short.at_30, 100_000)
        self.assertEqual(short.at_60, 90_000)

    def test_has_income_reflects_the_selected_horizon(self):
        self.repo.delete_income_schedule(self.service.list_income_schedules()[0].id)
        self.add_income(
            cents=50_000,
            frequency=IncomeFrequency.MONTHLY,
            next_date=date(2026, 12, 24),
            anchor_day=24,
        )

        self.assertFalse(self.service.cash_flow(TODAY, horizon_days=30).has_income)
        self.assertTrue(self.service.cash_flow(TODAY, horizon_days=60).has_income)

    def test_events_on_the_last_horizon_day_are_included_and_the_next_day_is_not(self):
        self.repo.delete_income_schedule(self.service.list_income_schedules()[0].id)
        self.add_bill("Last day", 1_000, day=TODAY + timedelta(days=30))
        self.add_bill("Next day", 2_000, day=TODAY + timedelta(days=31))

        result = self.service.cash_flow(TODAY, horizon_days=30)

        self.assertEqual([event.description for event in result.events], ["Last day"])


class TestIncomeScheduleCrud(Base):
    def test_add_list_get_update_delete_round_trip(self):
        savings = self.add_account("Savings", AccountKind.SAVINGS)
        created = self.add_income(
            name="  Paycheck  ",
            cents=250_000,
            frequency=IncomeFrequency.SEMIMONTHLY,
            next_date=date(2026, 11, 20),
            anchor_day=20,
            second_day=5,
            account=savings,
        )
        self.assertIsNotNone(created.id)
        self.assertEqual(created.name, "Paycheck")

        self.assertEqual([schedule.id for schedule in self.service.list_income_schedules()], [created.id])
        self.assertEqual(self.service.get_income_schedule(created.id).account_id, savings.id)
        self.assertIsNone(self.service.get_income_schedule(999_999))

        created.name = "Main pay"
        created.amount_cents = 300_000
        created.frequency = IncomeFrequency.MONTHLY
        created.next_date = date(2026, 12, 31)
        created.anchor_day = 31
        created.second_day = None
        self.service.update_income_schedule(created)

        stored = self.service.get_income_schedule(created.id)
        self.assertEqual((stored.name, stored.amount_cents, stored.anchor_day), ("Main pay", 300_000, 31))

        self.service.delete_income_schedule(created.id)
        self.assertIsNone(self.service.get_income_schedule(created.id))

    def test_validation_is_surfaced(self):
        invalid = [
            {"name": "   "},
            {"cents": 0},
            {"account": self.card},
            {"frequency": IncomeFrequency.MONTHLY, "next_date": date(2026, 11, 21), "anchor_day": 20},
        ]
        for overrides in invalid:
            with self.subTest(overrides=overrides):
                with self.assertRaises(RepositoryError):
                    self.add_income(**overrides)

        with self.assertRaises(RepositoryError):
            self.add_income(
                frequency=IncomeFrequency.SEMIMONTHLY,
                next_date=date(2026, 11, 21),
                anchor_day=20,
                second_day=5,
            )

    def test_monthly_next_date_accepts_a_month_clamped_anchor_day(self):
        for next_date in (date(2026, 11, 30), date(2027, 2, 28)):
            with self.subTest(next_date=next_date):
                created = self.add_income(
                    name=f"Month end {next_date:%Y-%m}",
                    frequency=IncomeFrequency.MONTHLY,
                    next_date=next_date,
                    anchor_day=31,
                )
                self.assertEqual((created.next_date, created.anchor_day), (next_date, 31))

    def test_semimonthly_next_date_accepts_a_month_clamped_income_day(self):
        for next_date in (date(2026, 11, 30), date(2027, 2, 28)):
            with self.subTest(next_date=next_date):
                created = self.add_income(
                    name=f"Month end {next_date:%Y-%m}",
                    frequency=IncomeFrequency.SEMIMONTHLY,
                    next_date=next_date,
                    anchor_day=15,
                    second_day=31,
                )
                self.assertEqual(
                    (created.next_date, created.anchor_day, created.second_day),
                    (next_date, 15, 31),
                )

    def test_crud_does_not_write_to_the_ledger(self):
        savings = self.add_account("Savings", AccountKind.SAVINGS)
        created = self.add_income(account=savings)

        self.assertEqual(self.repo.transaction_count(savings.id), 0)
        self.service.delete_income_schedule(created.id)
        self.assertEqual(self.repo.transaction_count(savings.id), 0)


if __name__ == "__main__":
    unittest.main()
