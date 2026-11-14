import unittest
from datetime import date

from budget_tracker.db import MIGRATIONS, connect, schema_version
from budget_tracker.models import (
    Account, AccountKind, SavingsGoal, Transaction, TransactionType,
)
from budget_tracker.repository import Repository, RepositoryError
from budget_tracker.services import BudgetService


class Base(unittest.TestCase):
    def setUp(self) -> None:
        self.repo = Repository(connect(":memory:"))
        self.service = BudgetService(self.repo)
        self.accounts = {a.name: a for a in self.repo.list_accounts()}
        self.categories = {c.name: c for c in self.repo.list_categories()}
        self.savings = self.repo.add_account(Account("Savings", AccountKind.SAVINGS))

    def tearDown(self) -> None:
        self.repo.close()

    def goal(self, **overrides) -> SavingsGoal:
        values = {
            "name": "Trip",
            "target_cents": 100_000,
            "target_date": date(2027, 1, 31),
            "start_date": date(2026, 11, 1),
            "account_id": self.savings.id,
            "initial_cents": 0,
        }
        values.update(overrides)
        return self.repo.add_savings_goal(SavingsGoal(**values))

    def contribute(self, goal: SavingsGoal, cents: int, day: date) -> Transaction:
        return self.repo.add_transaction(Transaction(
            cents, TransactionType.TRANSFER, None, day, "Savings",
            account_id=self.accounts["Checking"].id,
            transfer_account_id=self.savings.id,
            goal_id=goal.id,
        ))


class TestSchemaAndGoalStorage(Base):
    def test_migration_adds_goals_and_goal_link(self):
        self.assertEqual(schema_version(self.repo.conn), len(MIGRATIONS))
        tables = {
            row[0]
            for row in self.repo.conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
        self.assertIn("savings_goals", tables)
        columns = {row[1] for row in self.repo.conn.execute("PRAGMA table_info(transactions)")}
        self.assertIn("goal_id", columns)

    def test_goal_crud(self):
        goal = self.goal()
        self.assertIsNotNone(goal.id)
        self.assertEqual(self.repo.get_savings_goal(goal.id).name, "Trip")
        goal.name = "Big trip"
        goal.target_cents = 150_000
        self.repo.update_savings_goal(goal)
        loaded = self.repo.get_savings_goal(goal.id)
        self.assertEqual((loaded.name, loaded.target_cents), ("Big trip", 150_000))
        self.repo.delete_savings_goal(goal.id)
        self.assertIsNone(self.repo.get_savings_goal(goal.id))

    def test_transaction_goal_tag_round_trips(self):
        goal = self.goal()
        tx = self.repo.add_transaction(Transaction(
            5_000, TransactionType.TRANSFER, None, date(2026, 11, 10), "Savings: Trip",
            account_id=self.accounts["Checking"].id,
            transfer_account_id=self.savings.id,
            goal_id=goal.id,
        ))
        loaded = self.repo.list_transactions(limit=1)[0]
        self.assertEqual((loaded.id, loaded.goal_id), (tx.id, goal.id))

    def test_editing_a_goal_transfer_keeps_the_tag(self):
        goal = self.goal()
        tx = self.contribute(goal, 5_000, date(2026, 11, 10))
        tx.amount_cents = 6_000
        self.repo.update_transaction(tx)
        loaded = self.repo.list_transactions(limit=1)[0]
        self.assertEqual((loaded.amount_cents, loaded.goal_id), (6_000, goal.id))

    def test_editing_a_goal_transfer_to_an_expense_clears_the_tag(self):
        goal = self.goal()
        tx = self.repo.add_transaction(Transaction(
            5_000, TransactionType.TRANSFER, None, date(2026, 11, 10), "Savings",
            account_id=self.accounts["Checking"].id,
            transfer_account_id=self.savings.id,
            goal_id=goal.id,
        ))
        tx.type = TransactionType.EXPENSE
        tx.category_id = self.categories["Groceries"].id
        tx.transfer_account_id = None
        self.repo.update_transaction(tx)
        self.assertIsNone(self.repo.list_transactions(limit=1)[0].goal_id)

    def test_a_transfer_to_another_account_drops_the_tag(self):
        goal = self.goal()
        other = self.repo.add_account(Account("Other savings", AccountKind.SAVINGS))
        tx = Transaction(
            5_000, TransactionType.TRANSFER, None, date(2026, 11, 10), "Savings",
            account_id=self.accounts["Checking"].id,
            transfer_account_id=other.id,
            goal_id=goal.id,
        )
        saved = self.repo.add_transaction(tx)
        self.assertIsNone(self.repo.list_transactions(limit=1)[0].goal_id)


class TestGoalQueries(Base):
    def test_goals_are_ordered_by_target_date_then_name(self):
        late = self.goal(name="Late", target_date=date(2027, 6, 1))
        early_b = self.goal(name="B early", target_date=date(2027, 1, 1))
        early_a = self.goal(name="A early", target_date=date(2027, 1, 1))
        self.assertEqual(
            [goal.id for goal in self.repo.list_savings_goals()],
            [early_a.id, early_b.id, late.id],
        )

    def test_contributions_count_only_tagged_transfers(self):
        goal = self.goal()
        self.contribute(goal, 5_000, date(2026, 11, 10))
        self.contribute(goal, 2_500, date(2026, 11, 20))
        self.repo.add_transaction(Transaction(
            9_000, TransactionType.TRANSFER, None, date(2026, 11, 15), "Untagged",
            account_id=self.accounts["Checking"].id,
            transfer_account_id=self.savings.id,
        ))
        self.assertEqual(self.repo.goal_contribution_count(goal.id), 2)
        self.assertEqual(self.repo.goal_contributed_cents(goal.id), 7_500)

    def test_contributions_honour_the_through_date(self):
        goal = self.goal()
        self.contribute(goal, 5_000, date(2026, 11, 10))
        self.contribute(goal, 2_500, date(2026, 11, 20))
        self.assertEqual(
            self.repo.goal_contributed_cents(goal.id, through=date(2026, 11, 10)), 5_000
        )

    def test_contributions_in_a_month(self):
        goal = self.goal()
        self.contribute(goal, 5_000, date(2026, 11, 10))
        self.contribute(goal, 1_000, date(2026, 11, 30))
        self.contribute(goal, 2_500, date(2026, 12, 1))
        self.assertEqual(self.repo.goal_contributed_in_month(goal.id, "2026-11"), 6_000)
        self.assertEqual(
            self.repo.goal_contributed_in_month(
                goal.id, "2026-11", through=date(2026, 11, 15)
            ),
            5_000,
        )

    def test_goal_contributions_are_newest_first(self):
        goal = self.goal()
        first = self.contribute(goal, 5_000, date(2026, 11, 10))
        second = self.contribute(goal, 2_500, date(2026, 11, 10))
        third = self.contribute(goal, 1_000, date(2026, 11, 20))
        self.assertEqual(
            [tx.id for tx in self.repo.list_goal_contributions(goal.id)],
            [third.id, second.id, first.id],
        )

    def test_deleting_a_goal_keeps_the_transactions_but_clears_the_tag(self):
        goal = self.goal()
        tx = self.contribute(goal, 5_000, date(2026, 11, 10))
        self.repo.delete_savings_goal(goal.id)
        loaded = self.repo.list_transactions(limit=1)[0]
        self.assertEqual(loaded.id, tx.id)
        self.assertIsNone(loaded.goal_id)


class TestGoalPlan(Base):
    def test_fixed_monthly_plan_and_schedule(self):
        goal = self.goal(target_cents=90_000, start_date=date(2026, 11, 1), target_date=date(2027, 1, 31))
        progress = self.service.goal_progress(goal.id, today=date(2026, 11, 15))
        self.assertEqual((progress.required_monthly_cents, progress.state), (30_000, "active"))
        self.assertEqual(
            [(row.month, row.required_cents, row.cumulative_cents) for row in progress.plan],
            [("2026-11", 30_000, 30_000), ("2026-12", 30_000, 60_000), ("2027-01", 30_000, 90_000)],
        )

    def test_final_row_is_smaller_and_lands_on_the_target(self):
        goal = self.goal(target_cents=100_000, start_date=date(2026, 11, 1), target_date=date(2027, 1, 31))
        progress = self.service.goal_progress(goal.id, today=date(2026, 11, 15))
        self.assertEqual(progress.required_monthly_cents, 33_334)
        self.assertEqual(progress.plan[-1].required_cents, 33_332)
        self.assertEqual(progress.plan[-1].cumulative_cents, 100_000)

    def test_a_contribution_does_not_change_the_monthly_amount(self):
        goal = self.goal(target_cents=90_000, start_date=date(2026, 11, 1), target_date=date(2027, 1, 31))
        self.service.contribute_to_goal(
            goal.id, self.accounts["Checking"].id, 30_000, date(2026, 11, 15),
            today=date(2026, 11, 15),
        )
        progress = self.service.goal_progress(goal.id, today=date(2026, 11, 15))
        self.assertEqual(progress.required_monthly_cents, 30_000)
        self.assertEqual(progress.contributed_this_month_cents, 30_000)
        self.assertEqual((progress.progress_cents, progress.remaining_cents), (30_000, 60_000))

    def test_reached_and_overdue_states(self):
        reached = self.goal(target_cents=30_000, start_date=date(2026, 11, 1), target_date=date(2027, 1, 31))
        self.service.contribute_to_goal(
            reached.id, self.accounts["Checking"].id, 30_000, date(2026, 11, 15),
            today=date(2026, 11, 15),
        )
        status = self.service.goal_progress(reached.id, today=date(2026, 11, 15))
        self.assertEqual((status.state, status.required_monthly_cents, status.plan), ("reached", 0, []))

        overdue = self.goal(
            name="Old goal", target_cents=10_000,
            start_date=date(2026, 8, 1), target_date=date(2026, 10, 31),
        )
        status = self.service.goal_progress(overdue.id, today=date(2026, 11, 15))
        self.assertEqual((status.state, status.required_monthly_cents, status.plan), ("overdue", 10_000, []))

    def test_tiny_target_does_not_emit_zero_rows(self):
        goal = self.goal(target_cents=1, start_date=date(2026, 11, 1), target_date=date(2027, 1, 31))
        progress = self.service.goal_progress(goal.id, today=date(2026, 11, 15))
        self.assertTrue(all(row.required_cents > 0 for row in progress.plan))
        self.assertEqual(progress.plan[-1].cumulative_cents, 1)

    def test_progress_counts_only_this_goals_tagged_transfers(self):
        first = self.goal(name="First")
        second = self.goal(name="Second")
        self.service.contribute_to_goal(first.id, self.accounts["Checking"].id, 5_000, date(2026, 11, 10))
        self.assertEqual(self.service.goal_progress(first.id, today=date(2026, 11, 15)).progress_cents, 5_000)
        self.assertEqual(self.service.goal_progress(second.id, today=date(2026, 11, 15)).progress_cents, 0)

    def test_untagged_transfers_do_not_count(self):
        goal = self.goal()
        self.repo.add_transaction(Transaction(
            20_000, TransactionType.TRANSFER, None, date(2026, 11, 10), "Other",
            account_id=self.accounts["Checking"].id, transfer_account_id=self.savings.id,
        ))
        self.assertEqual(self.service.goal_progress(goal.id, today=date(2026, 11, 15)).progress_cents, 0)

    def test_contribution_validation(self):
        goal = self.goal()
        with self.assertRaises(RepositoryError):
            self.service.contribute_to_goal(goal.id, self.savings.id, 1_000, date(2026, 11, 10))
        with self.assertRaises(RepositoryError):
            self.service.contribute_to_goal(goal.id, self.accounts["Credit Card"].id, 1_000, date(2026, 11, 10))
        with self.assertRaises(RepositoryError):
            self.service.contribute_to_goal(goal.id, self.accounts["Checking"].id, 1_000, date(2027, 1, 1), today=date(2026, 11, 15))

    def test_destination_cannot_change_with_a_contribution(self):
        goal = self.goal()
        other = self.repo.add_account(Account("Other savings", AccountKind.SAVINGS))
        self.service.contribute_to_goal(goal.id, self.accounts["Checking"].id, 5_000, date(2026, 11, 10))
        with self.assertRaises(RepositoryError):
            self.service.update_savings_goal(
                goal.id, name="Trip", target_cents=100_000, target_date=date(2027, 1, 31),
                account_id=other.id, initial_cents=0,
            )

    def test_create_requires_a_savings_destination(self):
        with self.assertRaises(RepositoryError):
            self.service.create_savings_goal(
                "Bad", 10_000, date(2027, 1, 31), self.accounts["Checking"].id,
            )
