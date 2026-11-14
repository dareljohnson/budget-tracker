import sqlite3
import unittest

from budget_tracker.db import MIGRATIONS, _seed, connect, migrate, schema_version
from budget_tracker.models import (
    Account,
    AccountKind,
    Debt,
    DebtKind,
    Transaction,
    TransactionType,
)
from budget_tracker.repository import Repository, RepositoryError


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
