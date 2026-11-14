import sqlite3
import unittest
from datetime import date

from budget_tracker.db import MIGRATIONS, _seed, connect, migrate, schema_version
from budget_tracker.debt_planner import DebtStrategy
from budget_tracker.formatting import shift_month
from budget_tracker.models import (
    Account,
    AccountKind,
    Debt,
    DebtKind,
    Transaction,
    TransactionType,
)
from budget_tracker.repository import Repository, RepositoryError
from budget_tracker.services import BudgetService


class TestSchema(unittest.TestCase):
    def setUp(self) -> None:
        self.repo = Repository(connect(":memory:"))

    def tearDown(self) -> None:
        self.repo.close()

    def test_migration_adds_debts_and_payment_link(self):
        self.assertEqual(schema_version(self.repo.conn), len(MIGRATIONS))
        tables = {
            row[0]
            for row in self.repo.conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
        self.assertIn("debts", tables)
        columns = {row[1] for row in self.repo.conn.execute("PRAGMA table_info(transactions)")}
        self.assertIn("debt_id", columns)
        indexes = {row[1] for row in self.repo.conn.execute("PRAGMA index_list(debts)")}
        self.assertIn("ux_debts_account", indexes)

    def test_debt_payments_category_is_seeded(self):
        row = self.repo.conn.execute(
            "SELECT type, color, icon FROM categories WHERE name = 'Debt payments'"
        ).fetchone()
        self.assertEqual((row["type"], row["color"], row["icon"]), ("expense", "#546E7A", "PAYMENTS"))

    def test_upgrade_from_v6_keeps_transactions(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        for version, script in enumerate(MIGRATIONS[:6], start=1):
            conn.executescript(f"BEGIN;\n{script}\nPRAGMA user_version = {version};\nCOMMIT;")
        _seed(conn)
        conn.execute(
            "INSERT INTO transactions (amount_cents, type, account_id, category_id, occurred_on) "
            "VALUES (500, 'expense', (SELECT id FROM accounts WHERE name = 'Checking'), "
            "(SELECT id FROM categories WHERE name = 'Other'), '2026-01-02')"
        )
        conn.commit()
        migrate(conn)
        row = conn.execute("SELECT debt_id FROM transactions").fetchone()
        self.assertIsNone(row["debt_id"])
        self.assertEqual(schema_version(conn), len(MIGRATIONS))
        conn.close()


class DebtCase(unittest.TestCase):
    def setUp(self) -> None:
        self.repo = Repository(connect(":memory:"))
        self.accounts = {a.name: a for a in self.repo.list_accounts()}
        self.card = self.accounts["Credit Card"]
        self.checking = self.accounts["Checking"]
        self.savings = self.repo.add_account(Account("Savings", AccountKind.SAVINGS))

    def tearDown(self) -> None:
        self.repo.close()

    def debt(self, **overrides) -> Debt:
        values = {
            "name": "Visa",
            "kind": DebtKind.CREDIT_CARD,
            "balance_cents": 420_000,
            "apr_bps": 2499,
            "minimum_cents": 10_500,
        }
        values.update(overrides)
        return self.repo.add_debt(Debt(**values))


class TestDebtStorage(DebtCase):
    def test_add_get_list_update_delete(self):
        debt = self.debt()
        self.assertIsNotNone(debt.id)
        loaded = self.repo.get_debt(debt.id)
        self.assertEqual((loaded.name, loaded.balance_cents, loaded.apr_bps, loaded.minimum_cents),
                         ("Visa", 420_000, 2499, 10_500))
        debt.name = "Visa Signature"
        self.repo.update_debt(debt)
        self.assertEqual(self.repo.list_debts()[0].name, "Visa Signature")
        self.repo.delete_debt(debt.id)
        self.assertIsNone(self.repo.get_debt(debt.id))

    def test_one_debt_per_linked_card(self):
        self.debt(account_id=self.card.id)
        with self.assertRaises(RepositoryError):
            self.debt(name="Second", account_id=self.card.id)
        self.assertEqual(self.repo.debt_usage(self.card.id), 1)

    def test_standalone_debts_do_not_conflict(self):
        self.debt(name="Loan A")
        self.debt(name="Loan B")
        self.assertEqual(len(self.repo.list_debts()), 2)

    def test_linked_debt_requires_a_credit_card(self):
        with self.assertRaises(RepositoryError):
            self.debt(account_id=self.savings.id)

    def test_negative_values_are_rejected(self):
        for overrides in (
            {"balance_cents": -1},
            {"apr_bps": -1},
            {"apr_bps": 10_001},
            {"minimum_cents": -1},
            {"original_balance_cents": -1},
            {"name": "  "},
        ):
            with self.assertRaises(RepositoryError):
                self.debt(**overrides)

    def test_account_guards(self):
        debt = self.debt(account_id=self.card.id)
        with self.assertRaises(RepositoryError):
            self.repo.delete_account(self.card.id)
        self.card.kind = AccountKind.OTHER
        with self.assertRaises(RepositoryError):
            self.repo.update_account(self.card)
        self.card.kind = AccountKind.CREDIT_CARD
        self.repo.update_account(self.card)
        self.repo.delete_debt(debt.id)
        self.repo.delete_account(self.card.id)


class TestDebtPayments(DebtCase):
    def expense(self, debt, cents=20_000, day=date(2026, 11, 10), account=None, category=None):
        return self.repo.add_transaction(Transaction(
            cents,
            TransactionType.EXPENSE,
            category.id if category else self.category.id,
            day,
            "Payment",
            account_id=(account or self.checking).id,
            debt_id=debt.id,
        ))

    def setUp(self) -> None:
        super().setUp()
        self.category = next(c for c in self.repo.list_categories() if c.name == "Debt payments")

    def test_tagged_expense_lowers_the_standalone_balance(self):
        debt = self.debt()
        tx = self.expense(debt)
        self.assertEqual(self.repo.get_debt(debt.id).balance_cents, 400_000)
        self.assertEqual(self.repo.list_debt_payments(debt.id)[0].id, tx.id)

    def test_overpay_is_rejected_without_a_transaction(self):
        debt = self.debt()
        with self.assertRaises(RepositoryError):
            self.expense(debt, 500_000)
        self.assertEqual(self.repo.get_debt(debt.id).balance_cents, 420_000)
        self.assertEqual(self.repo.list_transactions(), [])

    def test_editing_a_payment_adjusts_the_balance(self):
        debt = self.debt()
        tx = self.expense(debt)
        tx.amount_cents = 30_000
        self.repo.update_transaction(tx)
        self.assertEqual(self.repo.get_debt(debt.id).balance_cents, 390_000)

    def test_editing_a_payment_into_a_transfer_restores_the_balance(self):
        debt = self.debt()
        tx = self.expense(debt)
        tx.type = TransactionType.TRANSFER
        tx.category_id = None
        tx.transfer_account_id = self.savings.id
        self.repo.update_transaction(tx)
        self.assertIsNone(self.repo.list_transactions()[0].debt_id)
        self.assertEqual(self.repo.get_debt(debt.id).balance_cents, 420_000)

    def test_editing_a_payment_above_the_balance_is_rejected(self):
        debt = self.debt()
        tx = self.expense(debt)
        tx.amount_cents = 500_000
        with self.assertRaises(RepositoryError):
            self.repo.update_transaction(tx)
        self.assertEqual(self.repo.get_debt(debt.id).balance_cents, 400_000)
        self.assertEqual(self.repo.list_transactions()[0].amount_cents, 20_000)

    def test_deleting_a_payment_restores_the_balance(self):
        debt = self.debt()
        tx = self.expense(debt)
        self.repo.delete_transaction(tx.id)
        self.assertEqual(self.repo.get_debt(debt.id).balance_cents, 420_000)

    def test_a_liability_funding_account_clears_the_tag(self):
        debt = self.debt()
        tx = self.expense(debt, account=self.card)
        self.assertIsNone(self.repo.list_transactions()[0].debt_id)
        self.assertEqual(self.repo.get_debt(debt.id).balance_cents, 420_000)

    def test_linked_payment_is_a_tagged_transfer_without_a_balance_change(self):
        debt = self.debt(account_id=self.card.id)
        tx = self.repo.add_transaction(Transaction(
            10_000, TransactionType.TRANSFER, None, date(2026, 11, 10), "",
            account_id=self.checking.id, transfer_account_id=self.card.id, debt_id=debt.id,
        ))
        self.assertEqual(tx.debt_id, debt.id)
        self.assertEqual(self.repo.get_debt(debt.id).balance_cents, 420_000)

    def test_deleting_the_debt_keeps_the_payment(self):
        debt = self.debt()
        self.expense(debt)
        self.repo.delete_debt(debt.id)
        [tx] = self.repo.list_transactions()
        self.assertIsNone(tx.debt_id)


class ServiceCase(DebtCase):
    def setUp(self) -> None:
        super().setUp()
        self.service = BudgetService(self.repo)
        self.payments_category = next(
            c for c in self.repo.list_categories() if c.name == "Debt payments"
        )

    def owe_card(self, cents: int = 50_000) -> None:
        self.repo.add_transaction(Transaction(
            cents, TransactionType.EXPENSE, self.payments_category.id, date(2026, 11, 1),
            "Card purchase", account_id=self.card.id,
        ))


class TestDebtService(ServiceCase):
    def test_standalone_summary_uses_stored_values(self):
        debt = self.service.create_debt("Loan", DebtKind.PERSONAL, 100_000, 1200, 0)
        summary = self.service.debt_summary(debt.id)
        self.assertEqual((summary.balance_cents, summary.effective_minimum_cents), (100_000, 0))
        self.assertFalse(summary.minimum_from_statement)
        self.assertTrue(summary.minimum_below_interest)

    def test_linked_summary_uses_the_card_balance(self):
        self.owe_card()
        debt = self.service.create_debt("Visa", DebtKind.CREDIT_CARD, 0, 2499, 10_500, account_id=self.card.id)
        summary = self.service.debt_summary(debt.id)
        self.assertEqual(summary.balance_cents, 50_000)
        self.assertEqual(summary.account_name, "Credit Card")
        self.assertEqual(self.repo.get_debt(debt.id).balance_cents, 50_000)

    def test_linked_minimum_follows_the_statement(self):
        self.owe_card()
        debt = self.service.create_debt("Visa", DebtKind.CREDIT_CARD, 0, 2499, 10_500, account_id=self.card.id)
        month = self.service.current_month()
        self.service.record_minimum_payment(self.card.id, month, 7_500)
        summary = self.service.debt_summary(debt.id)
        self.assertEqual(summary.effective_minimum_cents, 7_500)
        self.assertTrue(summary.minimum_from_statement)
        self.assertEqual(summary.statement_month, month)

    def test_linked_minimum_falls_back_when_no_statement_exists(self):
        self.owe_card()
        debt = self.service.create_debt("Visa", DebtKind.CREDIT_CARD, 0, 2499, 10_500, account_id=self.card.id)
        self.assertEqual(self.service.debt_summary(debt.id).effective_minimum_cents, 10_500)

    def test_unlinking_snapshots_the_current_owed_balance(self):
        self.owe_card()
        debt = self.service.create_debt("Visa", DebtKind.CREDIT_CARD, 0, 2499, 10_500, account_id=self.card.id)
        debt.account_id = None
        self.service.update_debt(debt.id, name="Visa", kind=DebtKind.CREDIT_CARD,
                                 balance_cents=0, apr_bps=2499, minimum_cents=10_500,
                                 original_balance_cents=None, account_id=None)
        self.assertEqual(self.repo.get_debt(debt.id).balance_cents, 50_000)

    def test_second_debt_for_a_card_is_rejected(self):
        self.owe_card()
        self.service.create_debt("Visa", DebtKind.CREDIT_CARD, 0, 2499, 10_500, account_id=self.card.id)
        with self.assertRaises(RepositoryError):
            self.service.create_debt("Other", DebtKind.CREDIT_CARD, 0, 1999, 5_000, account_id=self.card.id)

    def test_linked_card_in_credit_is_zero_owed(self):
        income = next(c for c in self.repo.list_categories() if c.name == "Salary")
        self.repo.add_transaction(Transaction(
            10_000, TransactionType.INCOME, income.id, date(2026, 11, 1),
            "Card credit", account_id=self.card.id,
        ))
        debt = self.service.create_debt("Visa", DebtKind.CREDIT_CARD, 0, 2499, 10_500, account_id=self.card.id)
        self.assertEqual(self.service.debt_summary(debt.id).balance_cents, 0)
        plan = self.service.debt_plan(0, DebtStrategy.AVALANCHE, today=date(2026, 11, 14))
        self.assertEqual(plan.months, 0)

    def test_standalone_payment_is_an_expense_and_moves_money(self):
        debt = self.service.create_debt("Loan", DebtKind.PERSONAL, 100_000, 1200, 2_000)
        tx = self.service.record_debt_payment(debt.id, self.checking.id, 20_000, date(2026, 11, 10),
                                              today=date(2026, 11, 14))
        self.assertEqual((tx.type, tx.category_name, tx.debt_id), (TransactionType.EXPENSE, "Debt payments", debt.id))
        self.assertEqual(self.repo.get_debt(debt.id).balance_cents, 80_000)
        self.assertEqual(
            next(b for b in self.repo.account_balances() if b.account.id == self.checking.id).balance_cents,
            -20_000,
        )

    def test_payment_rejections(self):
        debt = self.service.create_debt("Loan", DebtKind.PERSONAL, 10_000, 1200, 2_000)
        with self.assertRaises(RepositoryError):
            self.service.record_debt_payment(debt.id, self.checking.id, 20_000, date(2026, 11, 10),
                                             today=date(2026, 11, 14))
        with self.assertRaises(RepositoryError):
            self.service.record_debt_payment(debt.id, self.checking.id, 1_000, date(2026, 11, 20),
                                             today=date(2026, 11, 14))
        with self.assertRaises(RepositoryError):
            self.service.record_debt_payment(debt.id, self.card.id, 1_000, date(2026, 11, 10),
                                             today=date(2026, 11, 14))

    def test_linked_payment_is_a_transfer_into_the_card(self):
        self.owe_card()
        debt = self.service.create_debt("Visa", DebtKind.CREDIT_CARD, 0, 2499, 10_500, account_id=self.card.id)
        tx = self.service.record_debt_payment(debt.id, self.checking.id, 10_000, date(2026, 11, 10),
                                              today=date(2026, 11, 14))
        self.assertEqual((tx.type, tx.transfer_account_id), (TransactionType.TRANSFER, self.card.id))
        self.assertEqual(self.service.debt_summary(debt.id).balance_cents, 40_000)

    def test_linked_payment_may_exceed_the_card_balance(self):
        self.owe_card(10_000)
        debt = self.service.create_debt("Visa", DebtKind.CREDIT_CARD, 0, 2499, 10_500, account_id=self.card.id)
        self.service.record_debt_payment(debt.id, self.checking.id, 20_000, date(2026, 11, 10),
                                         today=date(2026, 11, 14))
        self.assertEqual(self.service.debt_summary(debt.id).balance_cents, 0)

    def test_plan_uses_live_balances_and_extra(self):
        self.owe_card(50_000)
        debt = self.service.create_debt("Visa", DebtKind.CREDIT_CARD, 0, 2400, 2_500, account_id=self.card.id)
        plan = self.service.debt_plan(5_000, DebtStrategy.AVALANCHE, today=date(2026, 11, 14))
        self.assertTrue(plan.finished)
        self.assertEqual(plan.debt_free_month, shift_month("2026-11", plan.months))
        self.assertEqual([(d.name, m) for d, m in plan.payoff_order], [("Visa", plan.months)])

    def test_extra_comparison_returns_plans(self):
        debt = self.service.create_debt("Loan", DebtKind.PERSONAL, 100_000, 0, 5_000)
        plans = self.service.debt_extra_comparison(DebtStrategy.AVALANCHE, [0, 5_000], today=date(2026, 11, 14))
        self.assertEqual([p.extra_cents for p in plans], [0, 5_000])
        self.assertLessEqual(plans[1].months, plans[0].months)
