import os
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest import mock

from budget_tracker.db import connect
from budget_tracker.models import Account, AccountKind, Transaction, TransactionType
from budget_tracker.receipts.parser import ReceiptDraft
from budget_tracker.receipts.storage import store_receipt
from budget_tracker.repository import Repository, RepositoryError
from budget_tracker.services import BudgetService


class TestReceiptServices(unittest.TestCase):
    def setUp(self):
        self.repo = Repository(connect(":memory:"))
        self.addCleanup(self.repo.close)
        self.service = BudgetService(self.repo)
        accounts = {a.name: a for a in self.repo.list_accounts()}
        categories = {c.name: c for c in self.repo.list_categories()}
        self.checking = accounts["Checking"]
        self.cash = accounts["Cash"]
        self.groceries = categories["Groceries"]
        self.other = categories["Other"]

        self._tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmpdir.cleanup)
        self.tmp = self._tmpdir.name
        env = mock.patch.dict(os.environ, {"BUDGET_TRACKER_HOME": self.tmp})
        env.start()
        self.addCleanup(env.stop)

        self.png = Path(self.tmp) / "photo.png"
        self.png.write_bytes(b"\x89PNG\r\n\x1a\n" + b"data")
        self.receipts = Path(self.tmp) / "receipts"

    def draft(self, **overrides):
        values = dict(
            merchant="Corner Market",
            occurred_on=date(2026, 10, 28),
            total_cents=647,
            card_last4=None,
            paid_cash=False,
            raw_text="",
            missing=frozenset(),
        )
        values.update(overrides)
        return ReceiptDraft(**values)

    def _stored(self, stored):
        return Path(self.tmp) / stored

    def _receipt_files(self):
        return list(self.receipts.iterdir()) if self.receipts.is_dir() else []

    # ---------- account matching ----------
    def test_card_last4_matches_cached_account(self):
        card = self.repo.add_account(Account("Visa", AccountKind.CREDIT_CARD, number_last4="1234"))
        self.assertEqual(self.service.receipt_account(self.draft(card_last4="1234")).id, card.id)

    def test_unmatched_card_does_not_fall_back_to_checking(self):
        self.assertIsNone(self.service.receipt_account(self.draft(card_last4="9999")))

    def test_no_payment_method_uses_first_checking_account(self):
        self.assertEqual(self.service.receipt_account(self.draft()).id, self.checking.id)

    def test_cash_prefers_cash_account(self):
        self.assertEqual(self.service.receipt_account(self.draft(paid_cash=True)).id, self.cash.id)

    def test_cash_falls_back_to_checking(self):
        self.repo.delete_account(self.cash.id)
        self.assertEqual(self.service.receipt_account(self.draft(paid_cash=True)).id, self.checking.id)

    def test_no_accounts_returns_none(self):
        for account in self.repo.list_accounts():
            self.repo.delete_account(account.id)
        self.assertIsNone(self.service.receipt_account(self.draft()))

    # ---------- category matching ----------
    def test_saved_rule_beats_keywords(self):
        self.repo.add_category_rule("KROGER", self.other.id)
        self.assertEqual(
            self.service.receipt_category_id(self.draft(merchant="KROGER #123")), self.other.id
        )

    def test_grocery_merchant_maps_to_groceries(self):
        self.assertEqual(
            self.service.receipt_category_id(self.draft(merchant="Corner Market")), self.groceries.id
        )

    def test_fuel_merchant_maps_to_transportation(self):
        transportation = next(c for c in self.repo.list_categories() if c.name == "Transportation")
        self.assertEqual(
            self.service.receipt_category_id(self.draft(merchant="SHELL FUEL")), transportation.id
        )

    def test_unknown_merchant_uses_other_category(self):
        self.assertEqual(
            self.service.receipt_category_id(self.draft(merchant="Mystery Bazaar")), self.other.id
        )

    # ---------- creation ----------
    def test_create_writes_expense_with_receipt_and_keeps_source(self):
        tx = self.service.create_receipt_transaction(
            amount_cents=647,
            occurred_on=date(2026, 10, 28),
            description="Corner Market",
            account_id=self.checking.id,
            category_id=self.groceries.id,
            source_path=self.png,
        )
        self.assertEqual(tx.type, TransactionType.EXPENSE)
        self.assertRegex(tx.receipt_path, r"^receipts/[0-9a-f]{32}\.png$")
        self.assertTrue((Path(self.tmp) / tx.receipt_path).is_file())
        self.assertTrue(self.png.is_file())
        self.assertEqual(self.repo.get_transaction(tx.id).receipt_path, tx.receipt_path)

    def test_zero_amount_is_rejected_before_storing(self):
        with self.assertRaises(RepositoryError):
            self.service.create_receipt_transaction(
                amount_cents=0,
                occurred_on=date(2026, 10, 28),
                description="Corner Market",
                account_id=self.checking.id,
                category_id=self.groceries.id,
                source_path=self.png,
            )
        self.assertEqual(self._receipt_files(), [])

    def test_save_failure_removes_the_stored_copy(self):
        with mock.patch.object(self.repo, "add_transaction", side_effect=RepositoryError("boom")):
            with self.assertRaises(RepositoryError):
                self.service.create_receipt_transaction(
                    amount_cents=647,
                    occurred_on=date(2026, 10, 28),
                    description="Corner Market",
                    account_id=self.checking.id,
                    category_id=self.groceries.id,
                    source_path=self.png,
                )
        self.assertEqual(self._receipt_files(), [])

    # ---------- attach and remove ----------
    def _expense(self):
        return self.repo.add_transaction(
            Transaction(
                647,
                TransactionType.EXPENSE,
                self.groceries.id,
                date(2026, 10, 28),
                "Corner Market",
                account_id=self.checking.id,
            )
        )

    def test_attach_replaces_old_file(self):
        tx = self._expense()
        old = store_receipt(self.png)
        self.repo.set_transaction_receipt(tx.id, old)

        attached = self.service.attach_receipt(tx.id, self.png)

        self.assertNotEqual(attached, old)
        self.assertTrue(self._stored(attached).is_file())
        self.assertFalse(self._stored(old).exists())
        self.assertEqual(self.repo.get_transaction(tx.id).receipt_path, attached)

    def test_attach_to_missing_transaction_raises(self):
        with self.assertRaises(RepositoryError):
            self.service.attach_receipt(9999, self.png)
        self.assertEqual(self._receipt_files(), [])

    def test_attach_failure_removes_new_copy(self):
        tx = self._expense()
        with mock.patch.object(
            self.repo, "set_transaction_receipt", side_effect=RepositoryError("boom")
        ):
            with self.assertRaises(RepositoryError):
                self.service.attach_receipt(tx.id, self.png)
        self.assertEqual(self._receipt_files(), [])

    def test_remove_clears_path_and_deletes_file(self):
        tx = self._expense()
        stored = store_receipt(self.png)
        self.repo.set_transaction_receipt(tx.id, stored)

        self.service.remove_receipt(tx.id)

        self.assertIsNone(self.repo.get_transaction(tx.id).receipt_path)
        self.assertFalse(self._stored(stored).exists())

    def test_remove_missing_transaction_raises(self):
        with self.assertRaises(RepositoryError):
            self.service.remove_receipt(9999)


if __name__ == "__main__":
    unittest.main()
