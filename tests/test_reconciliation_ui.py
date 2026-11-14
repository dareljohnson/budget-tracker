"""Receipt review match panel: candidates, mode switching, and attach-without-create."""

import asyncio
import os
import tempfile
import unittest
from datetime import date, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import flet as ft

from budget_tracker.db import connect
from budget_tracker.models import Account, AccountKind, Transaction, TransactionType
from budget_tracker.receipts import storage
from budget_tracker.repository import Repository
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


class MatchPanelTestCase(unittest.TestCase):
    """Shared setup: an in-memory database, a stub page, and a data folder for receipts."""

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
        self.cash = next(a for a in self.repo.list_accounts() if a.name == "Cash")
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

        values = dict(
            merchant="Corner Market",
            occurred_on=date(2026, 10, 28),
            total_cents=647,
            card_last4=None,
            paid_cash=False,
            raw_text="TOTAL 6.47",
            missing=frozenset(),
        )
        values.update(overrides)
        return ReceiptDraft(**values)

    def _add_expense(
        self,
        *,
        description: str = "Corner Market",
        amount_cents: int = 647,
        occurred_on: date = date(2026, 10, 28),
        account: Account | None = None,
        receipt_path: str | None = None,
    ) -> Transaction:
        return self.repo.add_transaction(
            Transaction(
                amount_cents=amount_cents,
                type=TransactionType.EXPENSE,
                category_id=self.groceries.id,
                occurred_on=occurred_on,
                description=description,
                account_id=(account or self.checking).id,
                receipt_path=receipt_path,
            )
        )

    def _radio_labels(self, dialog) -> list[str]:
        return [radio.label for radio in dialog.match_group.content.controls]

    def _radio_values(self, dialog) -> list[str]:
        return [radio.value for radio in dialog.match_group.content.controls]


class TestStrongSingleCandidate(MatchPanelTestCase):
    def test_install_preselects_a_strong_candidate_and_switches_to_attach(self):
        candidate = self._add_expense()
        dialog = self._dialog()
        dialog._source_path = self.png

        dialog._install(self._draft())

        self.assertTrue(dialog.match_panel.visible)
        self.assertEqual(dialog.match_group.value, str(candidate.id))
        self.assertIsNotNone(dialog._selected_match)
        self.assertEqual(dialog._selected_match.transaction.id, candidate.id)
        self.assertTrue(dialog._match_mode)
        self.assertTrue(dialog.amount.disabled)
        self.assertTrue(dialog.account.disabled)
        self.assertTrue(dialog.category.disabled)
        self.assertEqual(dialog.save_button.content, "Attach & save")
        self.assertFalse(dialog.save_button.disabled)

        labels = self._radio_labels(dialog)
        self.assertEqual(len(labels), 1)
        self.assertIn("Corner Market", labels[0])
        self.assertIn("Added manually", labels[0])
        self.assertIn("$6.47", labels[0])
        self.assertIn("2026-10-28", labels[0])

    def test_create_a_new_transaction_leaves_match_mode(self):
        candidate = self._add_expense()
        dialog = self._dialog()
        dialog._source_path = self.png
        dialog._install(self._draft())

        dialog.create_new_button.on_click(None)

        self.assertFalse(dialog._match_mode)
        self.assertFalse(dialog.amount.disabled)
        self.assertFalse(dialog.account.disabled)
        self.assertFalse(dialog.category.disabled)
        self.assertFalse(dialog.date.disabled)
        self.assertEqual(dialog.save_button.content, "Save")
        self.assertFalse(dialog.save_button.disabled)

        dialog._save(None)

        transactions = self.repo.list_transactions()
        self.assertEqual(len(transactions), 2)  # the match plus the new row
        created = next(tx for tx in transactions if tx.id != candidate.id)
        self.assertTrue(created.receipt_path.startswith("receipts/"))
        self.assertIsNone(self.repo.get_transaction(candidate.id).receipt_path)

    def test_attach_saves_no_transaction_and_calls_the_service_once(self):
        candidate = self._add_expense()
        dialog = self._dialog()
        asyncio.run(dialog.open())
        dialog._source_path = self.png
        dialog._install(self._draft())
        before = len(self.repo.list_transactions())

        # Attach mode never validates these fields.
        dialog.amount.value = "not money"
        dialog.category.value = None
        dialog._refresh_save()
        self.assertFalse(dialog.save_button.disabled)

        with mock.patch.object(
            self.ctx.service, "attach_receipt", wraps=self.ctx.service.attach_receipt
        ) as attach, mock.patch.object(self.ctx, "data_changed") as changed:
            dialog._save(None)

        attach.assert_called_once_with(candidate.id, self.png)
        changed.assert_called_once_with()
        self.assertEqual(len(self.repo.list_transactions()), before)
        stored = self.repo.get_transaction(candidate.id).receipt_path
        self.assertTrue(stored.startswith("receipts/"))
        self.assertEqual(len(list(storage.receipts_dir().iterdir())), 1)
        self.assertEqual(self.page.open_dialogs, [])
        self.assertEqual(self.page.errors, [])

    def test_double_save_attaches_only_once(self):
        candidate = self._add_expense()
        dialog = self._dialog()
        asyncio.run(dialog.open())
        dialog._source_path = self.png
        dialog._install(self._draft())

        with mock.patch.object(
            self.ctx.service, "attach_receipt", wraps=self.ctx.service.attach_receipt
        ) as attach:
            dialog._save(None)
            dialog._save(None)

        attach.assert_called_once_with(candidate.id, self.png)
        self.assertEqual(len(self.repo.list_transactions()), 1)
        self.assertEqual(len(list(storage.receipts_dir().iterdir())), 1)


class TestCandidateSearch(MatchPanelTestCase):
    def test_receipt_without_a_total_has_no_match_panel(self):
        self._add_expense()
        dialog = self._dialog()
        dialog._source_path = self.png

        dialog._install(self._draft(total_cents=None, missing=frozenset({"total"})))

        self.assertFalse(dialog.match_panel.visible)
        self.assertFalse(dialog._match_mode)
        self.assertEqual(dialog.match_group.content.controls, [])
        self.assertIsNone(dialog._selected_match)

    def test_unmatched_card_searches_no_candidates(self):
        self._add_expense()
        dialog = self._dialog()

        dialog._install(self._draft(card_last4="9999"))

        self.assertIsNone(dialog.account.value)
        self.assertFalse(dialog.match_panel.visible)
        self.assertFalse(dialog._match_mode)
        self.assertEqual(dialog.match_group.content.controls, [])

    def test_no_eligible_candidate_leaves_no_panel(self):
        dialog = self._dialog()

        dialog._install(self._draft())

        self.assertFalse(dialog.match_panel.visible)
        self.assertFalse(dialog._match_mode)

    def test_candidate_with_a_receipt_is_not_offered(self):
        self._add_expense(description="Corner Market", receipt_path="receipts/taken.png")
        self._add_expense(description="Blue Bottle Coffee")

        dialog = self._dialog()
        dialog._install(self._draft())

        labels = self._radio_labels(dialog)
        self.assertEqual(len(labels), 1)
        self.assertIn("Blue Bottle Coffee", labels[0])
        self.assertNotIn("Corner Market", labels[0])

    def test_multiple_candidates_show_radios_without_a_preselection(self):
        first = self._add_expense(description="Blue Bottle Coffee")
        second = self._add_expense(description="Gas Station")
        dialog = self._dialog()
        dialog._source_path = self.png

        dialog._install(self._draft())

        self.assertTrue(dialog.match_panel.visible)
        self.assertEqual(len(self._radio_values(dialog)), 2)
        self.assertIn(str(first.id), self._radio_values(dialog))
        self.assertIn(str(second.id), self._radio_values(dialog))
        self.assertIsNone(dialog.match_group.value)
        self.assertIsNone(dialog._selected_match)
        self.assertTrue(dialog.save_button.disabled)

        dialog.match_group.value = str(second.id)
        dialog.match_group.on_change(None)

        self.assertEqual(dialog._selected_match.transaction.id, second.id)
        self.assertFalse(dialog.save_button.disabled)

    def test_changing_the_account_clears_an_incompatible_selection(self):
        on_checking = self._add_expense()
        on_cash = self._add_expense(account=self.cash)
        empty = self.repo.add_account(
            Account(name="Empty Wallet", kind=AccountKind.SAVINGS)
        )

        dialog = self._dialog()
        dialog._source_path = self.png
        dialog._install(self._draft())
        self.assertEqual(dialog._selected_match.transaction.id, on_checking.id)

        dialog.account.value = str(self.cash.id)
        dialog._account_changed(None)

        self.assertEqual(dialog._selected_match.transaction.id, on_cash.id)
        self.assertEqual(dialog.match_group.value, str(on_cash.id))

        dialog.account.value = str(empty.id)
        dialog._account_changed(None)

        self.assertIsNone(dialog._selected_match)
        self.assertFalse(dialog.match_panel.visible)
        self.assertFalse(dialog._match_mode)

    def test_editing_the_amount_in_create_mode_stays_in_create_mode(self):
        candidate = self._add_expense(amount_cents=1200, description="Corner Market")
        dialog = self._dialog()
        dialog._source_path = self.png
        dialog._install(self._draft())
        self.assertFalse(dialog.match_panel.visible)

        dialog.amount.value = "12.00"
        dialog.amount.on_change(None)  # type: ignore[operator]

        # The panel updates, but the owner is still creating: no surprise attach mode.
        self.assertTrue(dialog.match_panel.visible)
        self.assertIsNone(dialog._selected_match)
        self.assertIsNone(dialog.match_group.value)
        self.assertFalse(dialog._match_mode)
        self.assertFalse(dialog.amount.disabled)
        self.assertEqual(dialog.save_button.content, "Save")
        self.assertFalse(dialog.save_button.disabled)

    def test_create_new_then_editing_the_amount_stays_in_create_mode(self):
        self._add_expense()
        dialog = self._dialog()
        dialog._source_path = self.png
        dialog._install(self._draft())
        self.assertTrue(dialog._match_mode)

        dialog.create_new_button.on_click(None)
        dialog.amount.value = "6.47"
        dialog.amount.on_change(None)  # type: ignore[operator]

        self.assertTrue(dialog.match_panel.visible)
        self.assertIsNone(dialog._selected_match)
        self.assertFalse(dialog._match_mode)
        self.assertFalse(dialog.amount.disabled)
        self.assertEqual(dialog.save_button.content, "Save")
        self.assertFalse(dialog.save_button.disabled)

    def test_calendar_picked_date_refreshes_and_clears_a_stale_match(self):
        self._add_expense()
        dialog = self._dialog()
        dialog._source_path = self.png
        dialog._install(self._draft())
        self.assertIsNotNone(dialog._selected_match)

        dialog.date.suffix.on_click(None)  # type: ignore[union-attr]
        picker = next(c for c in self.page.overlay if isinstance(c, ft.DatePicker))
        picker.on_change(
            SimpleNamespace(control=SimpleNamespace(value=datetime(2026, 11, 20)))
        )

        self.assertEqual(dialog.date.value, "2026-11-20")
        self.assertIsNone(dialog._selected_match)
        self.assertFalse(dialog.match_panel.visible)
        self.assertFalse(dialog._match_mode)

    def test_match_panel_text_uses_explicit_sizes(self):
        from budget_tracker.ui.theme import fs

        self._add_expense()
        dialog = self._dialog()
        dialog._install(self._draft())

        self.assertEqual(dialog.match_title.size, fs(14))
        self.assertEqual(dialog.match_note.size, fs(12))
        [radio] = dialog.match_group.content.controls
        self.assertEqual(radio.label_style.size, fs(13))


if __name__ == "__main__":
    unittest.main()
