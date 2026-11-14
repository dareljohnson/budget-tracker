import os
import sqlite3
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest import mock

from budget_tracker.db import MIGRATIONS, connect, migrate, schema_version
from budget_tracker.models import Transaction, TransactionType
from budget_tracker.receipts import storage
from budget_tracker.repository import Repository, RepositoryError


class TestReceiptPath(unittest.TestCase):
    def setUp(self):
        self.repo = Repository(connect(":memory:"))
        self.account = self.repo.list_accounts()[0]
        self.category = self.repo.list_categories(TransactionType.EXPENSE)[0]

    def tearDown(self):
        self.repo.close()

    def _tx(self, **overrides):
        values = dict(
            amount_cents=1234,
            type=TransactionType.EXPENSE,
            category_id=self.category.id,
            occurred_on=date(2026, 11, 14),
            description="Corner Market",
            account_id=self.account.id,
        )
        values.update(overrides)
        return Transaction(**values)

    def test_schema_has_receipt_path(self):
        self.assertEqual(schema_version(self.repo.conn), len(MIGRATIONS))
        columns = {r["name"] for r in self.repo.conn.execute("PRAGMA table_info(transactions)")}
        self.assertIn("receipt_path", columns)

    def test_insert_and_read_round_trip(self):
        saved = self.repo.add_transaction(self._tx(receipt_path="receipts/abc.png"))
        self.assertEqual(self.repo.get_transaction(saved.id).receipt_path, "receipts/abc.png")

    def test_ordinary_update_preserves_receipt_path(self):
        saved = self.repo.add_transaction(self._tx(receipt_path="receipts/abc.png"))
        edited = self._tx(id=saved.id, amount_cents=999, description="Edited")
        self.repo.update_transaction(edited)
        self.assertEqual(self.repo.get_transaction(saved.id).receipt_path, "receipts/abc.png")

    def test_set_and_clear_receipt(self):
        saved = self.repo.add_transaction(self._tx())
        self.repo.set_transaction_receipt(saved.id, "receipts/new.jpg")
        self.assertEqual(self.repo.get_transaction(saved.id).receipt_path, "receipts/new.jpg")
        self.repo.set_transaction_receipt(saved.id, None)
        self.assertIsNone(self.repo.get_transaction(saved.id).receipt_path)

    def test_set_receipt_rejects_missing_transaction(self):
        with self.assertRaises(RepositoryError):
            self.repo.set_transaction_receipt(9999, "receipts/x.png")

    def test_get_transaction_missing_returns_none(self):
        self.assertIsNone(self.repo.get_transaction(9999))

    def test_deleting_a_transaction_leaves_the_receipt_file(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            env = mock.patch.dict(os.environ, {"BUDGET_TRACKER_HOME": temp_dir})
            env.start()
            self.addCleanup(env.stop)
            source = Path(temp_dir) / "source.png"
            source.write_bytes(b"\x89PNG\r\n\x1a\n" + b"image")
            stored = storage.store_receipt(source)
            saved = self.repo.add_transaction(self._tx(receipt_path=stored))
            self.assertTrue(storage.resolve_receipt(stored).is_file())

            self.repo.delete_transaction(saved.id)

            self.assertIsNone(self.repo.get_transaction(saved.id))
            self.assertTrue(storage.resolve_receipt(stored).is_file())


class TestUpgradeFromV8(unittest.TestCase):
    def test_upgrade_from_v8_keeps_transactions(self):
        conn = sqlite3.connect(":memory:")
        self.addCleanup(conn.close)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        for version, script in enumerate(MIGRATIONS[:8], start=1):
            conn.executescript(f"BEGIN;\n{script}\nPRAGMA user_version = {version};\nCOMMIT;")
        conn.execute("INSERT INTO accounts (name, kind) VALUES ('Savings', 'savings')")
        conn.execute("INSERT INTO categories (name, type) VALUES ('Other', 'expense')")
        conn.execute(
            "INSERT INTO transactions (amount_cents, type, account_id, category_id, occurred_on) "
            "VALUES (500, 'expense', (SELECT id FROM accounts WHERE name = 'Savings'), "
            "(SELECT id FROM categories WHERE name = 'Other'), '2026-01-02')"
        )
        conn.commit()
        tx_id = conn.execute("SELECT id FROM transactions").fetchone()[0]

        migrate(conn)

        self.assertEqual(schema_version(conn), len(MIGRATIONS))
        repo = Repository(conn)
        kept = repo.get_transaction(tx_id)
        self.assertEqual(kept.amount_cents, 500)
        self.assertIsNone(kept.receipt_path)
        repo.set_transaction_receipt(tx_id, "receipts/v8.png")
        self.assertEqual(repo.get_transaction(tx_id).receipt_path, "receipts/v8.png")
