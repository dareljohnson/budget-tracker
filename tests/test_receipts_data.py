import unittest
from datetime import date

from budget_tracker.db import MIGRATIONS, connect, schema_version
from budget_tracker.models import Transaction, TransactionType
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
