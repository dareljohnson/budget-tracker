"""UI behaviour for receipt actions on transaction rows."""

import unittest
from datetime import date
from unittest import mock

import flet as ft

from budget_tracker.db import connect
from budget_tracker.models import Transaction, TransactionType
from budget_tracker.repository import Repository
from budget_tracker.services import BudgetService


class _PageStub:
    """Minimal stand-in for ``ft.Page`` so views can be built without a running app."""

    def __init__(self) -> None:
        self.overlay: list = []

    def show_dialog(self, control) -> None:
        self.overlay.append(control)

    def pop_dialog(self) -> None:
        pass

    def update(self, *_) -> None:
        pass


class TestReceiptRow(unittest.TestCase):
    def test_row_shows_receipt_button_only_with_callback_and_path(self):
        from budget_tracker.ui.components import transaction_row
        tx = Transaction(500, TransactionType.EXPENSE, 1, date(2026, 11, 14),
                         "Shop", receipt_path="receipts/x.png")
        seen = []
        row = transaction_row(tx, on_open_receipt=lambda t: seen.append(t))
        buttons = [c for c in row.content.controls if isinstance(c, ft.IconButton)]
        receipt = next(b for b in buttons if b.tooltip == "Open receipt")
        receipt.on_click(None)
        self.assertEqual(seen, [tx])

    def test_row_hides_receipt_button_without_path(self):
        from budget_tracker.ui.components import transaction_row
        tx = Transaction(500, TransactionType.EXPENSE, 1, date(2026, 11, 14), "Shop")
        row = transaction_row(tx, on_open_receipt=lambda t: None)
        self.assertFalse([c for c in row.content.controls
                          if isinstance(c, ft.IconButton) and c.tooltip == "Open receipt"])

    def test_row_hides_receipt_button_without_callback(self):
        from budget_tracker.ui.components import transaction_row
        tx = Transaction(500, TransactionType.EXPENSE, 1, date(2026, 11, 14),
                         "Shop", receipt_path="receipts/x.png")
        row = transaction_row(tx)
        self.assertFalse([c for c in row.content.controls
                          if isinstance(c, ft.IconButton) and c.tooltip == "Open receipt"])


class TestOpenReceipt(unittest.TestCase):
    def setUp(self) -> None:
        from budget_tracker.ui.context import AppContext

        self.repo = Repository(connect(":memory:"))
        self.page = _PageStub()
        self.ctx = AppContext(page=self.page, repo=self.repo, service=BudgetService(self.repo))
        self.tx = Transaction(500, TransactionType.EXPENSE, 1, date(2026, 11, 14),
                              "Shop", receipt_path="receipts/x.png")

    def tearDown(self) -> None:
        self.repo.close()

    def _view(self):
        from budget_tracker.ui.views.transactions import TransactionsView

        return TransactionsView(self.ctx)

    def test_open_receipt_opens_the_stored_path(self):
        with mock.patch("budget_tracker.ui.views.transactions.open_receipt") as opener:
            self._view()._open_receipt(self.tx)
        opener.assert_called_once_with("receipts/x.png")

    def test_open_receipt_notifies_when_file_is_missing(self):
        with mock.patch(
            "budget_tracker.ui.views.transactions.open_receipt",
            side_effect=FileNotFoundError("receipts/x.png"),
        ), mock.patch.object(self.ctx, "notify") as notify:
            self._view()._open_receipt(self.tx)
        notify.assert_called_once_with("The receipt file is missing", error=True)

    def test_open_receipt_notifies_on_os_error(self):
        with mock.patch(
            "budget_tracker.ui.views.transactions.open_receipt",
            side_effect=OSError("no viewer"),
        ), mock.patch.object(self.ctx, "notify") as notify:
            self._view()._open_receipt(self.tx)
        notify.assert_called_once_with("Could not open the receipt: no viewer", error=True)

    def test_open_receipt_notifies_on_an_invalid_stored_path(self):
        from budget_tracker.receipts.errors import ReceiptReadError

        with mock.patch(
            "budget_tracker.ui.views.transactions.open_receipt",
            side_effect=ReceiptReadError("Invalid receipt location"),
        ), mock.patch.object(self.ctx, "notify") as notify:
            self._view()._open_receipt(self.tx)
        notify.assert_called_once_with("Could not open the receipt: Invalid receipt location", error=True)

    def test_open_receipt_does_nothing_without_a_path(self):
        tx = Transaction(500, TransactionType.EXPENSE, 1, date(2026, 11, 14), "Shop")
        with mock.patch("budget_tracker.ui.views.transactions.open_receipt") as opener:
            self._view()._open_receipt(tx)
        opener.assert_not_called()

    def test_refresh_wires_a_receipt_row_to_the_view_opener(self):
        category = self.repo.list_categories(TransactionType.EXPENSE)[0]
        account = self.repo.list_accounts()[0]
        self.repo.add_transaction(Transaction(
            500, TransactionType.EXPENSE, category.id, date(2026, 11, 14), "Shop",
            account_id=account.id, receipt_path="receipts/x.png",
        ))
        view = self._view()
        [row] = view.list_column.controls
        receipt = next(
            c for c in row.content.controls
            if isinstance(c, ft.IconButton) and c.tooltip == "Open receipt"
        )
        with mock.patch("budget_tracker.ui.views.transactions.open_receipt") as opener:
            receipt.on_click(None)
        opener.assert_called_once_with("receipts/x.png")


if __name__ == "__main__":
    unittest.main()
