"""UI behaviour for receipt capture: row actions, the import dialog, and its wiring."""

import asyncio
import os
import tempfile
import unittest
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import flet as ft

from budget_tracker.db import connect
from budget_tracker.models import Transaction, TransactionType
from budget_tracker.receipts import storage
from budget_tracker.repository import Repository, RepositoryError
from budget_tracker.services import BudgetService


class _PageStub:
    """Minimal stand-in for ``ft.Page`` so views can be built without a running app."""

    def __init__(self) -> None:
        self.overlay: list = []

    def show_dialog(self, control) -> None:
        if isinstance(control, ft.AlertDialog):
            control.open = True
        self.overlay.append(control)

    def pop_dialog(self):
        for control in reversed(self.overlay):
            if isinstance(control, ft.AlertDialog):
                control.open = False
                self.overlay.remove(control)
                return control
        return None

    def update(self, *_) -> None:
        pass

    @property
    def open_dialogs(self) -> list:
        return [c for c in self.overlay if isinstance(c, ft.AlertDialog)]

    @property
    def errors(self) -> list[str]:
        from budget_tracker.ui.theme import INCOME_COLOR

        return [
            c.content.value
            for c in self.overlay
            if isinstance(c, ft.SnackBar) and c.bgcolor != INCOME_COLOR
        ]


def _context():
    from budget_tracker.ui.context import AppContext

    repo = Repository(connect(":memory:"))
    page = _PageStub()
    return AppContext(page=page, repo=repo, service=BudgetService(repo)), repo, page


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
        self.ctx, self.repo, self.page = _context()
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


class ReceiptDialogTestCase(unittest.TestCase):
    """Shared setup: an in-memory database, a stub page, and a data folder for stored receipts."""

    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmpdir.cleanup)
        self.tmp = Path(self._tmpdir.name)
        env = mock.patch.dict(os.environ, {"BUDGET_TRACKER_HOME": str(self.tmp)})
        env.start()
        self.addCleanup(env.stop)

        self.ctx, self.repo, self.page = _context()
        self.addCleanup(self.repo.close)
        self.checking = next(a for a in self.repo.list_accounts() if a.name == "Checking")
        self.groceries = next(
            c for c in self.repo.list_categories(TransactionType.EXPENSE) if c.name == "Groceries"
        )
        self.png = self.tmp / "receipt.png"
        self.png.write_bytes(b"\x89PNG\r\n\x1a\n" + b"receipt-bytes")

    def _dialog(self, **kwargs):
        from budget_tracker.ui.views.receipts import ReceiptImportDialog

        return ReceiptImportDialog(self.ctx, **kwargs)

    def _draft(self, **overrides):
        from budget_tracker.receipts.parser import ReceiptDraft

        values = dict(merchant="Corner Market", occurred_on=date(2026, 10, 28), total_cents=647,
                      card_last4=None, paid_cash=False, raw_text="TOTAL 6.47",
                      missing=frozenset())
        values.update(overrides)
        return ReceiptDraft(**values)


class TestReceiptImportDialog(ReceiptDialogTestCase):
    def test_dialog_prefills_and_saves_with_receipt(self):
        dialog = self._dialog()
        asyncio.run(dialog.open())
        dialog._source_path = self.png
        dialog._install(self._draft())
        self.assertEqual(dialog.merchant.value, "Corner Market")
        self.assertEqual(dialog.amount.value, "6.47")
        self.assertEqual(dialog.account.value, str(self.checking.id))
        self.assertEqual(dialog.category.value, str(self.groceries.id))
        self.assertFalse(dialog.save_button.disabled)
        dialog._save(None)
        tx = self.repo.list_transactions()[0]
        self.assertEqual(tx.description, "Corner Market")
        self.assertEqual(tx.amount_cents, 647)
        self.assertTrue(tx.receipt_path.startswith("receipts/"))
        self.assertEqual(self.page.open_dialogs, [])
        self.assertEqual(self.page.errors, [])
        self.assertEqual(len(list(storage.receipts_dir().iterdir())), 1)

    def test_save_is_not_reentrant(self):
        dialog = self._dialog()
        asyncio.run(dialog.open())
        dialog._source_path = self.png
        dialog._install(self._draft())
        dialog._save(None)
        dialog._save(None)
        self.assertEqual(len(self.repo.list_transactions()), 1)
        self.assertEqual(len(list(storage.receipts_dir().iterdir())), 1)

    def test_save_copies_the_source_file_once_and_keeps_it(self):
        before = self.png.read_bytes()
        dialog = self._dialog()
        dialog._source_path = self.png
        dialog._install(self._draft())
        with mock.patch(
            "budget_tracker.services.store_receipt", wraps=storage.store_receipt
        ) as store:
            dialog._save(None)
        store.assert_called_once_with(self.png)
        self.assertEqual(self.png.read_bytes(), before)
        self.assertEqual(len(list((self.tmp / "receipts").iterdir())), 1)

    def test_dialog_blocks_missing_total(self):
        dialog = self._dialog()
        dialog._install(self._draft(total_cents=None, missing=frozenset({"total"})))
        self.assertTrue(dialog.save_button.disabled)
        self.assertEqual(dialog.amount.value, "")

    def test_unmatched_card_leaves_account_blank_and_blocks_save(self):
        dialog = self._dialog()
        dialog._install(self._draft(card_last4="9999"))
        self.assertIsNone(dialog.account.value)
        self.assertIn("9999", dialog.account.error_text)
        self.assertTrue(dialog.save_button.disabled)
        self.assertEqual(dialog.raw_text.title.value, "Recognized text")

    def test_choosing_an_account_after_unmatched_card_enables_save(self):
        dialog = self._dialog()
        dialog._source_path = self.png
        dialog._install(self._draft(card_last4="9999"))
        self.assertTrue(dialog.save_button.disabled)
        dialog.account.value = str(self.checking.id)
        dialog.account.error_text = None
        dialog._refresh_save()
        self.assertFalse(dialog.save_button.disabled)

    def test_save_requires_a_source_file(self):
        dialog = self._dialog()
        dialog._install(self._draft())
        self.assertTrue(dialog.save_button.disabled)
        dialog._source_path = self.png
        dialog._refresh_save()
        self.assertFalse(dialog.save_button.disabled)

    def test_no_expense_categories_keeps_dialog_usable(self):
        for category in self.repo.list_categories(TransactionType.EXPENSE):
            self.repo.delete_category(category.id)
        dialog = self._dialog()
        dialog._source_path = self.png
        dialog._install(self._draft())
        self.assertIsNone(dialog.category.value)
        self.assertTrue(dialog.save_button.disabled)
        self.assertIn("expense category", dialog.note.value)

    def test_double_scan_click_starts_one_scan(self):
        dialog = self._dialog()
        dialog._busy = True
        with mock.patch("budget_tracker.ui.views.receipts.scan_receipt") as scan:
            asyncio.run(dialog._scan())
        scan.assert_not_called()

    def test_save_is_ignored_while_loading(self):
        dialog = self._dialog()
        asyncio.run(dialog.open())
        dialog._source_path = self.png
        dialog._install(self._draft())
        dialog._busy = True
        dialog._save(None)
        self.assertEqual(self.repo.list_transactions(), [])
        self.assertTrue(dialog.dialog.open)

    def test_save_failure_reenables_save_and_can_retry(self):
        dialog = self._dialog()
        asyncio.run(dialog.open())
        dialog._source_path = self.png
        dialog._install(self._draft())
        with mock.patch.object(
            self.ctx.service,
            "create_receipt_transaction",
            side_effect=RepositoryError("disk problem"),
        ):
            dialog._save(None)
        self.assertFalse(dialog._saving)
        self.assertFalse(dialog.save_button.disabled)
        self.assertTrue(dialog.dialog.open)
        dialog._save(None)
        self.assertEqual(len(self.repo.list_transactions()), 1)

    def test_repeated_scan_cleans_the_previous_temp_dir(self):
        dialog = self._dialog()
        first = Path(tempfile.mkdtemp(prefix="budget-tracker-scan-test-"))
        second = Path(tempfile.mkdtemp(prefix="budget-tracker-scan-test-"))
        self.addCleanup(lambda: second.exists() and second.rmdir())
        dialog._temp_dir = first
        with mock.patch(
            "budget_tracker.ui.views.receipts.tempfile.mkdtemp", return_value=str(second)
        ), mock.patch(
            "budget_tracker.ui.views.receipts.scan_receipt",
            return_value=second / "scan.png",
        ), mock.patch.object(dialog, "_load", new=mock.AsyncMock()):
            asyncio.run(dialog._scan())
        self.assertFalse(first.exists())

    def test_no_accounts_blocks_capture(self):
        closed = []
        for account in self.repo.list_accounts():
            self.repo.delete_account(account.id)
        dialog = self._dialog(on_close=lambda: closed.append(True))
        asyncio.run(dialog.open())
        self.assertIn("Create an account", self.page.errors[0])
        self.assertEqual(self.page.open_dialogs, [])
        self.assertEqual(closed, [True])

    def test_save_stays_disabled_until_every_field_is_valid(self):
        dialog = self._dialog()
        dialog._source_path = self.png
        dialog._install(self._draft())
        original = (dialog.amount.value, dialog.date.value, dialog.account.value,
                    dialog.category.value)

        for change in (
            lambda: setattr(dialog.account, "value", None),
            lambda: setattr(dialog.category, "value", None),
            lambda: setattr(dialog.amount, "value", "0"),
            lambda: setattr(dialog.date, "value", "not-a-date"),
        ):
            dialog.amount.value, dialog.date.value = original[0], original[1]
            dialog.account.value, dialog.category.value = original[2], original[3]
            change()
            dialog._refresh_save()
            self.assertTrue(dialog.save_button.disabled)

    def test_cancel_removes_scanner_temp_dir_but_keeps_source(self):
        closed = []
        dialog = self._dialog(on_close=lambda: closed.append(True))
        temp_dir = Path(tempfile.mkdtemp(prefix="budget-tracker-scan-test-"))
        self.addCleanup(lambda: temp_dir.exists() and temp_dir.rmdir())
        (temp_dir / "scan.png").write_bytes(b"scanned")
        dialog._temp_dir = temp_dir
        dialog._source_path = self.png

        dialog._close()
        dialog._close()

        self.assertFalse(temp_dir.exists())
        self.assertTrue(self.png.exists())
        self.assertEqual(closed, [True])

    def test_ocr_unavailable_allows_manual_entry_with_the_image_attached(self):
        from budget_tracker.receipts.errors import ReceiptOcrUnavailable

        dialog = self._dialog()
        with mock.patch(
            "budget_tracker.ui.views.receipts.read_receipt",
            side_effect=ReceiptOcrUnavailable("Text recognition isn't available"),
        ):
            asyncio.run(dialog._load(self.png))

        self.assertEqual(dialog._source_path, self.png)
        self.assertEqual(dialog.amount.value, "")
        self.assertTrue(dialog.save_button.disabled)
        self.assertIn("Text recognition isn't available", dialog.note.value)
        self.assertIsInstance(dialog.preview_holder.content, ft.Image)

        dialog.amount.value = "12.34"
        dialog._refresh_save()
        self.assertFalse(dialog.save_button.disabled)
        dialog._save(None)
        [tx] = self.repo.list_transactions()
        self.assertEqual((tx.amount_cents, tx.description), (1234, ""))
        self.assertTrue(tx.receipt_path.startswith("receipts/"))

    def test_unreadable_file_clears_the_source_and_keeps_the_dialog_open(self):
        from budget_tracker.receipts.errors import ReceiptReadError

        dialog = self._dialog()
        with mock.patch(
            "budget_tracker.ui.views.receipts.read_receipt",
            side_effect=ReceiptReadError("This file could not be read as a receipt image"),
        ):
            asyncio.run(dialog._load(self.png))

        self.assertIsNone(dialog._source_path)
        self.assertTrue(dialog.save_button.disabled)
        self.assertIn("could not be read", dialog.note.value)
        self.assertNotIsInstance(dialog.preview_holder.content, ft.Image)
        self.assertEqual(self.page.errors, ["This file could not be read as a receipt image"])

    def test_scan_success_loads_the_scanned_image(self):
        scan_dir = self.tmp / "scan-run"
        scan_dir.mkdir()

        def fake_scan(destination):
            Path(destination).write_bytes(b"\x89PNG\r\n\x1a\n" + b"scanned")
            return Path(destination)

        dialog = self._dialog()
        with mock.patch(
            "budget_tracker.ui.views.receipts.tempfile.mkdtemp", return_value=str(scan_dir)
        ), mock.patch(
            "budget_tracker.ui.views.receipts.scan_receipt", side_effect=fake_scan
        ), mock.patch(
            "budget_tracker.ui.views.receipts.read_receipt", return_value=self._draft()
        ):
            asyncio.run(dialog._scan(None))

        self.assertEqual(dialog._source_path, scan_dir / "scan.png")
        self.assertEqual(dialog.amount.value, "6.47")
        self.assertIsInstance(dialog.preview_holder.content, ft.Image)

    def test_scan_failure_notifies_and_removes_the_temp_dir(self):
        from budget_tracker.receipts.errors import ReceiptScanError

        scan_dir = self.tmp / "scan-failed"
        scan_dir.mkdir()
        dialog = self._dialog()
        with mock.patch(
            "budget_tracker.ui.views.receipts.tempfile.mkdtemp", return_value=str(scan_dir)
        ), mock.patch(
            "budget_tracker.ui.views.receipts.scan_receipt",
            side_effect=ReceiptScanError("The scanner did not return an image."),
        ):
            asyncio.run(dialog._scan(None))

        self.assertEqual(self.page.errors, ["The scanner did not return an image."])
        self.assertIsNone(dialog._temp_dir)
        self.assertFalse(scan_dir.exists())

    def test_choose_file_loads_the_picked_path(self):
        dialog = self._dialog()
        files = [SimpleNamespace(path=str(self.png))]
        with mock.patch.object(
            dialog._file_picker, "pick_files", new=mock.AsyncMock(return_value=files)
        ), mock.patch(
            "budget_tracker.ui.views.receipts.read_receipt", return_value=self._draft()
        ) as reader:
            asyncio.run(dialog._choose_file(None))

        self.assertEqual(dialog._source_path, self.png)
        reader.assert_called_once_with(self.png)
        self.assertEqual(dialog.merchant.value, "Corner Market")

    def test_choose_file_picker_failure_notifies(self):
        dialog = self._dialog()
        with mock.patch.object(
            dialog._file_picker, "pick_files", new=mock.AsyncMock(side_effect=RuntimeError("no picker"))
        ):
            asyncio.run(dialog._choose_file(None))
        self.assertIn("Could not open the file picker", self.page.errors[0])

    def test_preview_for_uses_magic_bytes(self):
        from budget_tracker.ui.views.receipts import preview_for

        image = preview_for(self.png)
        self.assertIsInstance(image, ft.Image)
        self.assertTrue(image.src.startswith("data:image/png;base64,"))

        jpg = self.tmp / "photo.jpg"
        jpg.write_bytes(b"\xff\xd8\xff" + b"data")
        self.assertTrue(preview_for(jpg).src.startswith("data:image/jpeg;base64,"))

        pdf = self.tmp / "receipt.pdf"
        pdf.write_bytes(b"%PDF-1.4\n")
        pdf_preview = preview_for(pdf)
        self.assertIsInstance(pdf_preview, ft.Row)
        self.assertIsInstance(pdf_preview.controls[0], ft.Icon)
        self.assertEqual(pdf_preview.controls[1].value, "receipt.pdf")

    def test_preview_skips_base64_for_files_over_eight_megabytes(self):
        from budget_tracker.ui.views.receipts import preview_for

        large = self.tmp / "large.png"
        with large.open("wb") as handle:
            handle.write(b"\x89PNG\r\n\x1a\n")
            handle.truncate(8 * 1024 * 1024 + 1)
        preview = preview_for(large)
        self.assertIsInstance(preview, ft.Row)
        self.assertIsInstance(preview.controls[0], ft.Icon)
        self.assertEqual(preview.controls[1].value, "large.png")


class TestTransactionEditorReceipts(ReceiptDialogTestCase):
    def _editor(self, tx):
        from budget_tracker.ui.views.transactions import TransactionEditor

        return TransactionEditor(self.ctx, tx)

    def _saved_transaction(self, receipt_path: str | None = None) -> Transaction:
        return self.repo.add_transaction(Transaction(
            500, TransactionType.EXPENSE, self.groceries.id, date(2026, 11, 14), "Shop",
            account_id=self.checking.id, receipt_path=receipt_path,
        ))

    def test_editor_shows_receipt_actions_only_for_saved_transactions(self):
        new_editor = self._editor(None)
        self.assertFalse(new_editor.receipt_section.visible)

        stored = storage.store_receipt(self.png)
        saved = self._saved_transaction(receipt_path=stored)
        editor = self._editor(saved)
        self.assertTrue(editor.receipt_section.visible)
        self.assertEqual(editor.receipt_status.value, "Receipt attached")
        self.assertTrue(editor.remove_receipt_button.visible)

        plain = self._saved_transaction()
        plain_editor = self._editor(plain)
        self.assertFalse(plain_editor.remove_receipt_button.visible)
        self.assertEqual(plain_editor.receipt_status.value, "No receipt attached")

    def test_editing_a_receipt_transaction_preserves_the_link(self):
        stored = storage.store_receipt(self.png)
        saved = self._saved_transaction(receipt_path=stored)
        editor = self._editor(saved)
        editor.description.value = "Changed description"

        editor._save(None)

        updated = self.repo.get_transaction(saved.id)
        self.assertEqual(updated.description, "Changed description")
        self.assertEqual(updated.receipt_path, stored)

    def test_editor_receipt_section_wiring(self):
        saved = self._saved_transaction()
        editor = self._editor(saved)
        opened = []

        class FakeAttachmentDialog:
            def __init__(self, ctx, tx, *, on_changed=None, on_close=None):
                opened.append((ctx, tx, on_changed))

            def open(self) -> None:
                pass

        with mock.patch(
            "budget_tracker.ui.views.transactions.ReceiptAttachmentDialog",
            FakeAttachmentDialog,
        ):
            editor._attach_receipt(None)
        self.assertEqual(len(opened), 1)
        self.assertIs(opened[0][1], saved)

        editor._receipt_changed("receipts/x.png")
        self.assertEqual(editor.receipt_status.value, "Receipt attached")
        self.assertEqual(editor.attach_receipt_button.content, "Replace receipt")
        self.assertTrue(editor.remove_receipt_button.visible)

        # Inline removal clears the DB link and restores the attach state.
        self.repo.set_transaction_receipt(saved.id, "receipts/x.png")
        editor._remove_receipt()

        self.assertEqual(editor.receipt_status.value, "No receipt attached")
        self.assertEqual(editor.attach_receipt_button.content, "Attach receipt")
        self.assertFalse(editor.remove_receipt_button.visible)
        self.assertIsNone(self.repo.get_transaction(saved.id).receipt_path)


class TestReceiptAttachmentDialog(ReceiptDialogTestCase):
    def _saved_transaction(self) -> Transaction:
        return self.repo.add_transaction(Transaction(
            500, TransactionType.EXPENSE, self.groceries.id, date(2026, 11, 14), "Shop",
            account_id=self.checking.id,
        ))

    def test_attachment_dialog_attaches_and_removes(self):
        from budget_tracker.ui.views.receipts import ReceiptAttachmentDialog

        saved = self._saved_transaction()
        seen: list[str | None] = []
        dialog = ReceiptAttachmentDialog(self.ctx, saved, on_changed=seen.append)

        dialog._attach(self.png)

        stored = self.repo.get_transaction(saved.id).receipt_path
        self.assertTrue(stored.startswith("receipts/"))
        self.assertEqual(seen, [stored])
        self.assertEqual(dialog.status.value, "A receipt is attached.")
        self.assertTrue(dialog.remove_button.visible)
        stored_file = self.tmp / stored
        self.assertTrue(stored_file.exists())
        self.assertTrue(self.png.exists())

        dialog._remove()

        self.assertIsNone(self.repo.get_transaction(saved.id).receipt_path)
        self.assertFalse(stored_file.exists())
        self.assertEqual(seen[-1], None)
        self.assertEqual(dialog.status.value, "No receipt attached.")
        self.assertFalse(dialog.remove_button.visible)

    def test_attachment_after_close_does_not_attach(self):
        from budget_tracker.ui.views.receipts import ReceiptAttachmentDialog

        stored = storage.store_receipt(self.png)
        saved = self._saved_transaction()
        self.repo.set_transaction_receipt(saved.id, stored)
        saved.receipt_path = stored
        seen: list[str | None] = []
        dialog = ReceiptAttachmentDialog(self.ctx, saved, on_changed=seen.append)
        dialog._closed_notified = True

        dialog._attach(self.png)

        self.assertEqual(self.repo.get_transaction(saved.id).receipt_path, stored)
        self.assertTrue((self.tmp / stored).exists())
        self.assertEqual(seen, [])

    def test_attachment_failure_keeps_the_db_link_and_allows_retry(self):
        from budget_tracker.ui.views.receipts import ReceiptAttachmentDialog

        saved = self._saved_transaction()
        seen: list[str | None] = []
        dialog = ReceiptAttachmentDialog(self.ctx, saved, on_changed=seen.append)
        dialog._attach(self.png)
        first = self.repo.get_transaction(saved.id).receipt_path
        first_file = self.tmp / first

        with mock.patch.object(
            self.ctx.service, "attach_receipt", side_effect=RepositoryError("disk problem")
        ):
            dialog._attach(self.png)

        self.assertEqual(self.repo.get_transaction(saved.id).receipt_path, first)
        self.assertTrue(first_file.exists())
        self.assertFalse(dialog._busy)
        self.assertEqual(seen, [first])
        self.assertIn("disk problem", self.page.errors[0])

        dialog._attach(self.png)
        second = self.repo.get_transaction(saved.id).receipt_path
        self.assertNotEqual(second, first)
        self.assertEqual(seen, [first, second])
        self.assertTrue((self.tmp / second).exists())


class TestReceiptHeaderButton(ReceiptDialogTestCase):
    def test_header_has_a_scan_receipt_button(self):
        from budget_tracker.ui.views.transactions import TransactionsView

        view = TransactionsView(self.ctx)
        self.assertIsInstance(view.receipt_button, ft.FilledTonalButton)
        self.assertIn("receipt", (view.receipt_button.tooltip or "").lower())

    def test_scan_receipt_disables_the_button_until_the_dialog_closes(self):
        from budget_tracker.ui.views.transactions import TransactionsView

        view = TransactionsView(self.ctx)
        opened = []

        class FakeDialog:
            def __init__(self, ctx, *, on_close=None):
                self.ctx = ctx
                self.on_close = on_close

            async def open(self):
                opened.append(self.on_close)

        with mock.patch(
            "budget_tracker.ui.views.transactions.ReceiptImportDialog", FakeDialog
        ), mock.patch.object(view.receipt_button, "update"):
            asyncio.run(view._scan_receipt(None))
            self.assertEqual(len(opened), 1)
            self.assertTrue(view.receipt_button.disabled)
            opened[0]()
            self.assertFalse(view.receipt_button.disabled)

    def test_scan_receipt_ignores_a_second_click_while_disabled(self):
        from budget_tracker.ui.views.transactions import TransactionsView

        view = TransactionsView(self.ctx)
        view.receipt_button.disabled = True
        with mock.patch(
            "budget_tracker.ui.views.transactions.ReceiptImportDialog"
        ) as dialog_cls:
            asyncio.run(view._scan_receipt(None))
        dialog_cls.assert_not_called()


if __name__ == "__main__":
    unittest.main()
