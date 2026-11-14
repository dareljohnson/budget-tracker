import sqlite3
import unittest

from budget_tracker.db import MIGRATIONS, _seed, connect, migrate, schema_version
from budget_tracker.models import Debt, DebtKind, Transaction, TransactionType
from budget_tracker.repository import Repository


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
