"""Data-layer tests for income schedules and cash-flow support queries (task 1)."""

import os
import sqlite3
import tempfile
import unittest
from datetime import date
from pathlib import Path

from budget_tracker.db import MIGRATIONS, connect, schema_version
from budget_tracker.models import (
    Account,
    AccountKind,
    IncomeFrequency,
    IncomeSchedule,
    Transaction,
    TransactionType,
)
from budget_tracker.repository import Repository, RepositoryError
from budget_tracker.services import BudgetService


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

    def schedule(self, **overrides) -> IncomeSchedule:
        values = {
            "name": "Paycheck",
            "amount_cents": 250_000,
            "frequency": IncomeFrequency.BIWEEKLY,
            "next_date": date(2026, 11, 20),
            "anchor_day": 20,
            "account_id": self.checking.id,
        }
        values.update(overrides)
        return self.repo.add_income_schedule(IncomeSchedule(**values))

    def add_transaction(
        self,
        cents: int,
        type_: TransactionType,
        day: date,
        *,
        account: Account | None = None,
        category: str | None = None,
        transfer_account: Account | None = None,
    ) -> Transaction:
        return self.repo.add_transaction(
            Transaction(
                cents,
                type_,
                self.categories[category].id if category else None,
                day,
                account_id=(account or self.checking).id,
                transfer_account_id=transfer_account.id if transfer_account else None,
            )
        )


class TestIncomeScheduleSchema(Base):
    def test_schema_version_is_current(self):
        self.assertEqual(schema_version(self.repo.conn), len(MIGRATIONS))
        self.assertEqual(len(MIGRATIONS), 12)

    def test_table_columns_and_index(self):
        columns = [row[1] for row in self.repo.conn.execute("PRAGMA table_info(income_schedules)")]
        self.assertEqual(
            columns,
            [
                "id",
                "name",
                "amount_cents",
                "frequency",
                "next_date",
                "anchor_day",
                "second_day",
                "account_id",
                "active",
            ],
        )
        indexes = {row[1] for row in self.repo.conn.execute("PRAGMA index_list(income_schedules)")}
        self.assertIn("idx_income_schedules_active", indexes)

    def raw_insert(self, **overrides) -> None:
        values = {
            "name": "Paycheck",
            "amount_cents": 250_000,
            "frequency": "biweekly",
            "next_date": "2026-11-20",
            "anchor_day": 20,
            "second_day": None,
            "account_id": self.checking.id,
            "active": 1,
        }
        values.update(overrides)
        with self.assertRaises(sqlite3.IntegrityError):
            with self.repo.conn:
                self.repo.conn.execute(
                    "INSERT INTO income_schedules "
                    "(name, amount_cents, frequency, next_date, anchor_day, second_day, account_id, active) "
                    "VALUES (:name, :amount_cents, :frequency, :next_date, :anchor_day, :second_day, "
                    ":account_id, :active)",
                    values,
                )

    def test_checks_reject_invalid_rows(self):
        self.raw_insert(amount_cents=0)
        self.raw_insert(amount_cents=-100)
        self.raw_insert(frequency="daily")
        self.raw_insert(anchor_day=0)
        self.raw_insert(anchor_day=32)
        self.raw_insert(second_day=32)
        self.raw_insert(account_id=999_999)

    def test_frequency_labels(self):
        self.assertEqual(
            {frequency: frequency.label for frequency in IncomeFrequency},
            {
                IncomeFrequency.WEEKLY: "Weekly",
                IncomeFrequency.BIWEEKLY: "Every 2 weeks",
                IncomeFrequency.MONTHLY: "Monthly",
                IncomeFrequency.SEMIMONTHLY: "Twice a month",
            },
        )


class TestIncomeScheduleCrud(Base):
    def test_add_read_update_delete_round_trip(self):
        savings = self.repo.add_account(Account("Savings", AccountKind.SAVINGS))
        schedule = self.schedule(
            name="  Paycheck  ",
            frequency=IncomeFrequency.SEMIMONTHLY,
            second_day=5,
            account_id=savings.id,
        )
        self.assertIsNotNone(schedule.id)
        self.assertEqual(self.repo.get_income_schedule(schedule.id).name, "Paycheck")

        schedule.name = "Main pay"
        schedule.amount_cents = 300_000
        schedule.frequency = IncomeFrequency.MONTHLY
        schedule.next_date = date(2026, 12, 31)
        schedule.anchor_day = 31
        schedule.second_day = None
        schedule.active = False
        self.repo.update_income_schedule(schedule)

        stored = self.repo.get_income_schedule(schedule.id)
        self.assertEqual(
            (
                stored.name,
                stored.amount_cents,
                stored.frequency,
                stored.next_date,
                stored.anchor_day,
                stored.second_day,
                stored.account_id,
                stored.active,
            ),
            ("Main pay", 300_000, IncomeFrequency.MONTHLY, date(2026, 12, 31), 31, None, savings.id, False),
        )

        self.repo.delete_income_schedule(schedule.id)
        self.assertIsNone(self.repo.get_income_schedule(schedule.id))

    def test_ordering_is_active_first_then_name_case_insensitive(self):
        self.schedule(name="Zebra")
        self.schedule(name="alpha")
        self.schedule(name="Beta")
        self.schedule(name="aardvark", active=False)

        self.assertEqual(
            [schedule.name for schedule in self.repo.list_income_schedules()],
            ["alpha", "Beta", "Zebra", "aardvark"],
        )
        self.assertEqual(
            [schedule.name for schedule in self.repo.list_income_schedules(active_only=True)],
            ["alpha", "Beta", "Zebra"],
        )

    def test_validation(self):
        savings = self.repo.add_account(Account("Savings", AccountKind.SAVINGS))
        loan = self.repo.add_account(Account("Loan", AccountKind.LOAN))

        invalid = [
            {"name": "   "},
            {"name": "x" * 61},
            {"amount_cents": 0},
            {"amount_cents": -1},
            {"frequency": "weekly"},
            {"anchor_day": 0},
            {"anchor_day": 32},
            {"account_id": self.card.id},
            {"account_id": loan.id},
            {"account_id": 999_999},
        ]
        for overrides in invalid:
            with self.subTest(overrides=overrides):
                with self.assertRaises(RepositoryError):
                    self.schedule(**overrides)

        # Liquid kinds are accepted.
        for account in (savings, self.cash):
            with self.subTest(account=account.name):
                created = self.schedule(name=f"{account.name} pay", account_id=account.id)
                self.assertEqual(created.account_id, account.id)

    def test_semimonthly_second_day_validation(self):
        with self.assertRaises(RepositoryError):
            self.schedule(frequency=IncomeFrequency.SEMIMONTHLY, second_day=None)
        with self.assertRaises(RepositoryError):
            self.schedule(frequency=IncomeFrequency.SEMIMONTHLY, anchor_day=20, second_day=20)
        with self.assertRaises(RepositoryError):
            self.schedule(frequency=IncomeFrequency.SEMIMONTHLY, second_day=0)
        with self.assertRaises(RepositoryError):
            self.schedule(frequency=IncomeFrequency.SEMIMONTHLY, second_day=32)

        created = self.schedule(frequency=IncomeFrequency.SEMIMONTHLY, second_day=5)
        self.assertEqual(created.second_day, 5)
        # A second day is meaningless for other frequencies; it is cleared rather than stored.
        weekly = self.schedule(name="Weekly pay", frequency=IncomeFrequency.WEEKLY, second_day=5)
        self.assertIsNone(self.repo.get_income_schedule(weekly.id).second_day)

    def test_account_deletion_is_blocked_and_usage_surfaces_it(self):
        savings = self.repo.add_account(Account("Savings", AccountKind.SAVINGS))
        created = self.schedule(name="Savings interest", account_id=savings.id)

        self.assertEqual(self.repo.account_usage(savings.id), 1)
        self.assertEqual(self.repo.transaction_count(savings.id), 0)
        with self.assertRaises(RepositoryError) as caught:
            self.repo.delete_account(savings.id)
        self.assertIn("Savings interest", str(caught.exception))

        self.repo.delete_income_schedule(created.id)
        self.assertEqual(self.repo.account_usage(savings.id), 0)
        self.assertEqual(self.repo.transaction_count(savings.id), 0)
        self.repo.delete_account(savings.id)


class TestCashFlowQueries(Base):
    def test_transaction_count_counts_both_sides_of_a_transfer(self):
        self.add_transaction(1000, TransactionType.INCOME, date(2026, 1, 5), category="Salary")
        self.add_transaction(
            400, TransactionType.TRANSFER, date(2026, 1, 6), transfer_account=self.cash
        )

        self.assertEqual(self.repo.transaction_count(self.checking.id), 2)
        self.assertEqual(self.repo.transaction_count(self.cash.id), 1)
        self.assertEqual(self.repo.transaction_count(self.card.id), 0)

    def test_scheduled_transactions_bounds_and_adjustments(self):
        first = self.add_transaction(1000, TransactionType.INCOME, date(2026, 1, 1), category="Salary")
        middle = self.add_transaction(2500, TransactionType.EXPENSE, date(2026, 1, 15), category="Groceries")
        last = self.add_transaction(3000, TransactionType.INCOME, date(2026, 1, 31), category="Salary")
        self.add_transaction(4000, TransactionType.EXPENSE, date(2026, 2, 1), category="Groceries")
        self.add_transaction(-500, TransactionType.ADJUSTMENT, date(2026, 1, 20))

        scheduled = self.repo.scheduled_transactions(date(2026, 1, 1), date(2026, 1, 31))
        self.assertEqual([tx.id for tx in scheduled], [first.id, middle.id, last.id])
        self.assertEqual([tx.occurred_on for tx in scheduled], sorted(tx.occurred_on for tx in scheduled))

    def test_amount_owed_as_of_ignores_future_card_activity(self):
        self.add_transaction(5000, TransactionType.EXPENSE, date(2026, 1, 5), account=self.card, category="Groceries")
        self.add_transaction(2000, TransactionType.EXPENSE, date(2026, 2, 5), account=self.card, category="Groceries")

        self.assertEqual(self.repo.amount_owed_as_of(self.card.id, date(2026, 1, 31)), 5000)
        self.assertEqual(self.repo.amount_owed_as_of(self.card.id, date(2026, 2, 28)), 7000)

        # An asset account never owes money.
        self.add_transaction(1000, TransactionType.INCOME, date(2026, 1, 5), category="Salary")
        self.assertEqual(self.repo.amount_owed_as_of(self.checking.id, date(2026, 2, 28)), 0)

        # A card in credit also owes nothing.
        self.add_transaction(
            8000,
            TransactionType.TRANSFER,
            date(2026, 2, 10),
            transfer_account=self.card,
        )
        self.assertEqual(self.repo.amount_owed_as_of(self.card.id, date(2026, 2, 28)), 0)

    def test_card_payments_through_filters_later_payments(self):
        self.add_transaction(
            3000, TransactionType.TRANSFER, date(2026, 1, 10), transfer_account=self.card
        )
        self.add_transaction(
            2000, TransactionType.TRANSFER, date(2026, 2, 10), transfer_account=self.card
        )
        # A transfer out of the card is not a payment into it.
        self.add_transaction(
            500, TransactionType.TRANSFER, date(2026, 1, 15), account=self.card, transfer_account=self.cash
        )

        self.assertEqual(len(self.repo.card_payments(self.card.id)), 2)
        self.assertEqual(
            self.repo.card_payments(self.card.id, through=date(2026, 1, 31)),
            [(date(2026, 1, 10), 3000)],
        )
        self.assertEqual(self.repo.card_payments(self.card.id, through=date(2026, 1, 9)), [])

    def test_minimum_payment_statuses_as_of_ignores_future_payment(self):
        self.service.record_minimum_payment(self.card.id, "2026-11", 3500)
        self.add_transaction(
            3500, TransactionType.TRANSFER, date(2026, 12, 2), transfer_account=self.card
        )

        before = {
            status.month: status
            for status in self.service.minimum_payment_statuses(self.card.id, as_of=date(2026, 11, 30))
        }
        after = {
            status.month: status
            for status in self.service.minimum_payment_statuses(self.card.id, as_of=date(2026, 12, 31))
        }
        self.assertEqual(before["2026-11"].state, "unpaid")
        self.assertEqual(after["2026-11"].state, "paid")
        # Default behavior is unchanged.
        default = {status.month: status for status in self.service.minimum_payment_statuses(self.card.id)}
        self.assertEqual(default["2026-11"].state, "paid")

    def test_current_minimum_payment_uses_as_of_month(self):
        self.service.record_minimum_payment(self.card.id, "2026-11", 3500)
        self.service.record_minimum_payment(self.card.id, "2026-12", 4000)

        self.assertEqual(
            self.service.current_minimum_payment(self.card.id, as_of=date(2026, 11, 15)).month,
            "2026-11",
        )
        self.assertEqual(
            self.service.current_minimum_payment(self.card.id, as_of=date(2026, 12, 15)).month,
            "2026-12",
        )


if __name__ == "__main__":
    unittest.main()
