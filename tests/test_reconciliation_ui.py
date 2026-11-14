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
from budget_tracker.models import (
    Account,
    AccountKind,
    Bill,
    BillFrequency,
    Transaction,
    TransactionType,
)
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


class BillMatchTestCase(MatchPanelTestCase):
    """Shared helpers for the bill 'Mark paid' dialog."""

    def _bill(
        self,
        *,
        name: str = "Rent",
        amount_cents: int = 1500,
        next_due: date = date(2026, 11, 1),
        account_id: int | None = None,
        pay_to_account_id: int | None = None,
    ) -> Bill:
        return self.repo.add_bill(
            Bill(
                name=name,
                amount_cents=amount_cents,
                frequency=BillFrequency.MONTHLY,
                next_due=next_due,
                account_id=(account_id or self.checking.id),
                category_id=None if pay_to_account_id else self.groceries.id,
                pay_to_account_id=pay_to_account_id,
            )
        )

    def _bill_dialog(self, bill: Bill, today: date = date(2026, 11, 1)):
        from budget_tracker.ui.views.bills import MarkPaidDialog

        return MarkPaidDialog(self.ctx, bill, today)

    def _transfer(
        self,
        amount_cents: int,
        source: Account,
        destination: Account,
        occurred_on: date,
        description: str,
    ) -> Transaction:
        return self.repo.add_transaction(
            Transaction(
                amount_cents=amount_cents,
                type=TransactionType.TRANSFER,
                category_id=None,
                occurred_on=occurred_on,
                description=description,
                account_id=source.id,
                transfer_account_id=destination.id,
            )
        )


class TestBillStrongCandidate(BillMatchTestCase):
    def test_a_single_strong_candidate_is_preselected_and_the_footer_uses_it(self):
        bill = self._bill()
        candidate = self._add_expense(description="Rent", amount_cents=1500, occurred_on=date(2026, 11, 1))
        dialog = self._bill_dialog(bill)
        dialog.open()
        before = self.repo.list_transactions()

        self.assertTrue(dialog.match_panel.visible)
        self.assertEqual(dialog.match_group.value, str(candidate.id))
        self.assertIsNotNone(dialog._selected_match)
        self.assertEqual(dialog._selected_match.transaction.id, candidate.id)
        self.assertTrue(dialog._use_existing)

        with mock.patch.object(
            self.repo, "mark_bill_paid", wraps=self.repo.mark_bill_paid
        ) as mark_paid:
            dialog._save(None)

        mark_paid.assert_called_once_with(
            bill.id,
            paid_on=date(2026, 11, 1),
            amount_cents=1500,
            account_id=self.checking.id,
            existing_transaction_id=candidate.id,
        )
        self.assertEqual(len(self.repo.list_transactions()), len(before))
        kept = self.repo.get_transaction(candidate.id)
        self.assertEqual(kept.amount_cents, 1500)
        self.assertEqual(kept.description, "Rent")
        self.assertEqual(kept.occurred_on, date(2026, 11, 1))
        self.assertEqual(kept.category_id, self.groceries.id)
        self.assertEqual(kept.account_id, self.checking.id)
        self.assertEqual(kept.source, "manual")
        self.assertEqual(self.repo.get_bill(bill.id).next_due, date(2026, 12, 1))
        self.assertEqual(self.page.open_dialogs, [])
        self.assertEqual(self.page.errors, [])

    def test_record_a_new_payment_switches_modes_and_inserts_a_transaction(self):
        bill = self._bill()
        self._add_expense(description="Rent", amount_cents=1500, occurred_on=date(2026, 11, 1))
        dialog = self._bill_dialog(bill)
        dialog.open()
        self.assertTrue(dialog._use_existing)

        dialog.record_new_button.on_click(None)

        self.assertFalse(dialog._use_existing)
        self.assertIsNone(dialog._selected_match)
        self.assertIsNone(dialog.match_group.value)

        dialog._save(None)

        transactions = self.repo.list_transactions()
        self.assertEqual(len(transactions), 2)
        created = next(t for t in transactions if t.source == "bill")
        self.assertEqual(created.amount_cents, 1500)
        self.assertEqual(created.type, TransactionType.EXPENSE)
        self.assertEqual(created.account_id, self.checking.id)
        self.assertEqual(created.occurred_on, date(2026, 11, 1))
        self.assertEqual(self.page.open_dialogs, [])

    def test_double_click_advances_the_due_date_once_when_reusing_a_transaction(self):
        bill = self._bill()
        self._add_expense(description="Rent", amount_cents=1500, occurred_on=date(2026, 11, 1))
        dialog = self._bill_dialog(bill)
        dialog.open()
        self.assertTrue(dialog._use_existing)

        with mock.patch.object(
            self.repo, "mark_bill_paid", wraps=self.repo.mark_bill_paid
        ) as mark_paid:
            dialog._save(None)
            dialog._save(None)

        mark_paid.assert_called_once()
        self.assertEqual(self.repo.get_bill(bill.id).next_due, date(2026, 12, 1))
        self.assertEqual(len(self.repo.list_transactions()), 1)
        self.assertEqual(self.page.open_dialogs, [])

    def test_double_click_inserts_at_most_one_new_payment(self):
        bill = self._bill()
        self._add_expense(description="Rent", amount_cents=1500, occurred_on=date(2026, 11, 1))
        dialog = self._bill_dialog(bill)
        dialog.open()
        dialog.record_new_button.on_click(None)

        with mock.patch.object(
            self.repo, "mark_bill_paid", wraps=self.repo.mark_bill_paid
        ) as mark_paid:
            dialog._save(None)
            dialog._save(None)

        mark_paid.assert_called_once()
        self.assertEqual(len(self.repo.list_transactions()), 2)
        self.assertEqual(self.repo.get_bill(bill.id).next_due, date(2026, 12, 1))
        self.assertEqual(self.page.open_dialogs, [])


class TestBillCandidateSearch(BillMatchTestCase):
    def test_changing_the_amount_refreshes_and_clears_an_incompatible_selection(self):
        bill = self._bill()
        self._add_expense(description="Rent", amount_cents=1500, occurred_on=date(2026, 11, 1))
        dialog = self._bill_dialog(bill)
        self.assertIsNotNone(dialog._selected_match)

        dialog.amount.value = "20.00"
        dialog.amount.on_change(None)

        self.assertIsNone(dialog._selected_match)
        self.assertFalse(dialog._use_existing)
        self.assertFalse(dialog.match_panel.visible)
        self.assertEqual(dialog.match_group.content.controls, [])

    def test_changing_the_date_refreshes_and_clears_an_incompatible_selection(self):
        bill = self._bill()
        self._add_expense(description="Rent", amount_cents=1500, occurred_on=date(2026, 11, 1))
        dialog = self._bill_dialog(bill)
        self.assertIsNotNone(dialog._selected_match)

        dialog.date.value = "2026-11-20"
        dialog.date.on_change(None)

        self.assertIsNone(dialog._selected_match)
        self.assertFalse(dialog._use_existing)
        self.assertFalse(dialog.match_panel.visible)

    def test_changing_the_account_recomputes_and_clears_an_incompatible_selection(self):
        bill = self._bill()
        on_checking = self._add_expense(
            description="Rent", amount_cents=1500, occurred_on=date(2026, 11, 1)
        )
        on_cash = self._add_expense(
            description="Rent",
            amount_cents=1500,
            occurred_on=date(2026, 11, 1),
            account=self.cash,
        )
        empty = self.repo.add_account(Account(name="Empty Wallet", kind=AccountKind.SAVINGS))
        dialog = self._bill_dialog(bill)
        self.assertEqual(dialog._selected_match.transaction.id, on_checking.id)

        dialog.account.value = str(self.cash.id)
        dialog.account.on_select(None)

        self.assertEqual([t.transaction.id for t in dialog._candidates], [on_cash.id])
        self.assertEqual(dialog._selected_match.transaction.id, on_cash.id)
        self.assertEqual(dialog.match_group.value, str(on_cash.id))
        self.assertTrue(dialog._use_existing)

        dialog.account.value = str(empty.id)
        dialog.account.on_select(None)

        self.assertIsNone(dialog._selected_match)
        self.assertFalse(dialog._use_existing)
        self.assertFalse(dialog.match_panel.visible)

    def test_multiple_candidates_show_radios_without_a_preselection(self):
        bill = self._bill()
        first = self._add_expense(
            description="Rent", amount_cents=1500, occurred_on=date(2026, 11, 1)
        )
        second = self._add_expense(
            description="Landlord", amount_cents=1500, occurred_on=date(2026, 11, 1)
        )

        dialog = self._bill_dialog(bill)

        self.assertTrue(dialog.match_panel.visible)
        self.assertEqual(
            set(self._radio_values(dialog)), {str(first.id), str(second.id)}
        )
        self.assertIsNone(dialog.match_group.value)
        self.assertIsNone(dialog._selected_match)
        self.assertFalse(dialog._use_existing)

        dialog.match_group.value = str(second.id)
        dialog.match_group.on_change(None)

        self.assertEqual(dialog._selected_match.transaction.id, second.id)
        self.assertTrue(dialog._use_existing)

    def test_use_this_transaction_without_a_selection_asks_for_one(self):
        bill = self._bill()
        self._add_expense(description="Rent", amount_cents=1500, occurred_on=date(2026, 11, 1))
        dialog = self._bill_dialog(bill)
        dialog.record_new_button.on_click(None)
        self.assertIsNone(dialog.match_group.value)

        dialog.use_existing_button.on_click(None)

        self.assertFalse(dialog._use_existing)
        self.assertTrue(dialog.match_note.visible)
        self.assertEqual(
            dialog.match_note.value, "Select a transaction above to reuse it."
        )

    def test_a_card_bill_only_offers_transfers_to_its_destination_account(self):
        card = next(a for a in self.repo.list_accounts() if a.name == "Credit Card")
        bill = self._bill(
            name="Visa payment",
            amount_cents=2500,
            account_id=self.checking.id,
            pay_to_account_id=card.id,
        )
        outgoing = self._transfer(
            2500, self.checking, card, date(2026, 11, 1), "Visa payment"
        )
        self._transfer(2500, self.checking, self.cash, date(2026, 11, 1), "Cash")
        self._transfer(2500, card, self.checking, date(2026, 11, 1), "Refund")

        dialog = self._bill_dialog(bill)

        self.assertTrue(dialog.match_panel.visible)
        self.assertEqual(self._radio_values(dialog), [str(outgoing.id)])
        self.assertEqual(dialog._selected_match.transaction.id, outgoing.id)


class TestBillMismatch(BillMatchTestCase):
    def test_a_mismatched_existing_transaction_keeps_the_dialog_open_with_a_notification(self):
        bill = self._bill()
        candidate = self._add_expense(
            description="Rent", amount_cents=1500, occurred_on=date(2026, 11, 1)
        )
        dialog = self._bill_dialog(bill)
        dialog.open()
        self.assertTrue(dialog._use_existing)

        # Change the row after it was offered so the repository rejects the match.
        self.repo.conn.execute(
            "UPDATE transactions SET amount_cents = 9999 WHERE id = ?", (candidate.id,)
        )
        self.repo.conn.commit()

        dialog._save(None)

        self.assertEqual(self.page.open_dialogs, [dialog.dialog])
        self.assertEqual(self.page.errors, ["That transaction does not match this bill"])
        self.assertEqual(len(self.repo.list_transactions()), 1)
        self.assertEqual(self.repo.get_bill(bill.id).next_due, date(2026, 11, 1))
        self.assertFalse(dialog.save_button.disabled)
        self.assertFalse(dialog._saving)

        # The dialog is still usable once the stale row matches again.
        self.repo.conn.execute(
            "UPDATE transactions SET amount_cents = 1500 WHERE id = ?", (candidate.id,)
        )
        self.repo.conn.commit()
        dialog._save(None)

        self.assertEqual(self.page.open_dialogs, [])
        self.assertEqual(self.repo.get_bill(bill.id).next_due, date(2026, 12, 1))
        self.assertEqual(len(self.repo.list_transactions()), 1)


def _texts(control) -> list[str]:
    """Collect every string value under a Flet control tree (Text values and button labels)."""
    if control is None:
        return []
    found: list[str] = []
    value = getattr(control, "value", None)
    if isinstance(value, str):
        found.append(value)
    content = getattr(control, "content", None)
    if isinstance(content, str):
        found.append(content)
    elif content is not None:
        found.extend(_texts(content))
    for child in getattr(control, "controls", None) or []:
        found.extend(_texts(child))
    return found


def _find_button(control, label: str):
    """Depth-first search for a button whose visible label matches."""
    if control is None:
        return None
    if getattr(control, "content", None) == label and isinstance(
        control,
        (ft.TextButton, ft.FilledButton, ft.OutlinedButton, ft.FilledTonalButton),
    ):
        return control
    for attr in ("content",):
        child = getattr(control, attr, None)
        if child is not None and not isinstance(child, str):
            found = _find_button(child, label)
            if found is not None:
                return found
    for child in getattr(control, "controls", None) or []:
        found = _find_button(child, label)
        if found is not None:
            return found
    return None


class StatementMatchTestCase(MatchPanelTestCase):
    """Shared helpers for the statement-import match column."""

    def _statement(self, *, sha: str = "a" * 64):
        from budget_tracker.models import Statement

        return Statement(
            account_id=self.checking.id,
            institution="Regions Bank",
            account_label="Checking",
            period_start=date(2026, 9, 1),
            period_end=date(2026, 9, 30),
            beginning_cents=0,
            deposits_cents=0,
            withdrawals_cents=3_485,
            fees_cents=0,
            ending_cents=-3_485,
            file_sha256=sha,
        )

    def _row(
        self,
        *,
        description: str = "FEL LINIS PIZZA",
        amount_cents: int = 3_485,
        occurred_on: date = date(2026, 9, 19),
        type_: TransactionType = TransactionType.EXPENSE,
    ):
        from budget_tracker.statements.model import ImportRow

        category_id = self.groceries.id if type_.category_type is not None else None
        return ImportRow(
            transaction=Transaction(
                amount_cents,
                type_,
                category_id,
                occurred_on,
                description,
                account_id=self.checking.id,
            ),
            direction="income" if type_ in (TransactionType.INCOME, TransactionType.REFUND) else "expense",
        )

    def _dialog(self, rows, *, sha: str = "a" * 64):
        from budget_tracker.statements.model import ImportPreview
        from budget_tracker.ui.views.statement_import import StatementImportDialog

        dialog = StatementImportDialog(self.ctx)
        dialog.account.value = str(self.checking.id)
        dialog._install_preview(
            ImportPreview(
                statement=self._statement(sha=sha),
                account_id=self.checking.id,
                rows=rows,
            )
        )
        return dialog


class TestStatementMatchColumn(StatementMatchTestCase):
    def test_candidates_show_a_possible_match_chip_and_matching_excludes_the_row(self):
        existing = self._add_expense(
            description="FEL LINIS PIZZA", amount_cents=3_485, occurred_on=date(2026, 9, 19)
        )
        dialog = self._dialog([self._row()])
        [widgets] = dialog._row_widgets

        self.assertEqual([tx.id for tx in widgets.row.match_candidates], [existing.id])
        self.assertIn("Possible match", _texts(widgets.match_action))
        self.assertIn("Match…", _texts(widgets.match_action))

        dialog._apply_match(widgets, existing)

        self.assertIs(widgets.row.matched, existing)
        self.assertFalse(widgets.row.include)
        self.assertFalse(widgets.include.value)
        self.assertTrue(widgets.include.disabled)
        self.assertIn("Matched", _texts(widgets.match_action))
        self.assertTrue(any(text.startswith("Matched to") for text in _texts(widgets.match_detail)))
        self.assertIn("1 matched to existing", dialog.rows_count.value)

    def test_import_anyway_clears_the_match_and_re_enables_the_row(self):
        existing = self._add_expense(
            description="FEL LINIS PIZZA", amount_cents=3_485, occurred_on=date(2026, 9, 19)
        )
        dialog = self._dialog([self._row()])
        [widgets] = dialog._row_widgets
        dialog._apply_match(widgets, existing)

        button = _find_button(dialog.rows_column, "Import anyway")
        self.assertIsNotNone(button)
        button.on_click(None)

        self.assertIsNone(widgets.row.matched)
        self.assertTrue(widgets.row.include)
        self.assertTrue(widgets.include.value)
        self.assertFalse(widgets.include.disabled)
        self.assertIn("Possible match", _texts(widgets.match_action))

    def test_choosing_a_transaction_for_a_second_row_clears_the_first(self):
        existing = self._add_expense(
            description="FEL LINIS PIZZA", amount_cents=3_485, occurred_on=date(2026, 9, 19)
        )
        dialog = self._dialog([self._row(), self._row()])
        first, second = dialog._row_widgets
        self.assertIn(existing.id, [tx.id for tx in second.row.match_candidates])

        dialog._apply_match(first, existing)
        self.assertNotIn(existing.id, [tx.id for tx in second.row.match_candidates])

        # A stale UI can still ask for the same row; the other row must release it.
        dialog._apply_match(second, existing)

        self.assertIsNone(first.row.matched)
        self.assertTrue(first.row.include)
        self.assertFalse(first.include.disabled)
        self.assertIs(second.row.matched, existing)
        self.assertFalse(second.row.include)

    def test_included_effect_ignores_a_matched_row_even_when_forced_on(self):
        existing = self._add_expense(
            description="FEL LINIS PIZZA", amount_cents=3_485, occurred_on=date(2026, 9, 19)
        )
        dialog = self._dialog([
            self._row(description="FEL LINIS PIZZA", amount_cents=3_485),
            self._row(description="Rent", amount_cents=10_000),
        ])
        matched, other = dialog._row_widgets
        dialog._apply_match(matched, existing)

        # Force the checkbox on to prove the guard does not depend on the include flag.
        matched.row.include = True
        matched.include.value = True
        self.assertEqual(dialog._included_effect(self.checking.id), -10_000)

        other.row.include = False
        self.assertEqual(dialog._included_effect(self.checking.id), 0)

    def test_commit_passes_only_inserted_rows_and_reports_matched_separately(self):
        existing = self._add_expense(
            description="FEL LINIS PIZZA", amount_cents=3_485, occurred_on=date(2026, 9, 19)
        )
        dialog = self._dialog([
            self._row(description="FEL LINIS PIZZA", amount_cents=3_485),
            self._row(description="Rent", amount_cents=10_000),
        ])
        matched, inserted = dialog._row_widgets
        dialog._apply_match(matched, existing)

        with mock.patch.object(
            self.ctx.service, "commit_import", return_value=dialog.preview.statement
        ) as commit:
            dialog._commit(None)

        commit.assert_called_once()
        payload = commit.call_args.args[1]
        self.assertEqual([row.transaction.description for row in payload], ["Rent"])
        [result] = self.page.open_dialogs
        texts = _texts(result.content)
        self.assertIn("Imported 1 transaction(s) into Checking.", texts)
        self.assertTrue(any("1 matched row(s)" in text for text in texts))
        self.assertEqual(self.page.errors, [])

    def test_retyping_a_row_recomputes_candidates_and_clears_an_incompatible_match(self):
        expense = self._add_expense(
            description="FEL LINIS PIZZA", amount_cents=3_485, occurred_on=date(2026, 9, 19)
        )
        transfer = self.repo.add_transaction(
            Transaction(
                3_485,
                TransactionType.TRANSFER,
                None,
                date(2026, 9, 19),
                "TRANSFER TO CASH",
                account_id=self.checking.id,
                transfer_account_id=self.cash.id,
            )
        )
        dialog = self._dialog([self._row()])
        [widgets] = dialog._row_widgets
        dialog._apply_match(widgets, expense)

        widgets.type_dd.value = TransactionType.TRANSFER.value
        dialog._type_changed(widgets)

        self.assertEqual(widgets.row.transaction.type, TransactionType.TRANSFER)
        self.assertEqual([tx.id for tx in widgets.row.match_candidates], [transfer.id])
        self.assertIsNone(widgets.row.matched)
        self.assertTrue(widgets.row.include)
        self.assertFalse(widgets.include.disabled)

    def test_projection_counts_matched_rows_and_leaves_the_difference_alone(self):
        existing = self._add_expense(
            description="FEL LINIS PIZZA",
            amount_cents=3_485,
            occurred_on=date(2026, 9, 19),
        )
        dialog = self._dialog([self._row()])
        [widgets] = dialog._row_widgets
        dialog._apply_match(widgets, existing)

        self.assertTrue(dialog.projection_panel.visible)
        self.assertEqual(dialog.projection_matched.value, "1")
        self.assertIn("already in the ledger", dialog.projection_matched_note.value)
        self.assertEqual(dialog.projection_difference.value, "$0.00")

    def test_undo_leaves_the_matched_existing_transaction(self):
        existing = self._add_expense(
            description="FEL LINIS PIZZA",
            amount_cents=3_485,
            occurred_on=date(2026, 9, 19),
        )
        dialog = self._dialog([self._row()], sha="u" * 64)
        [widgets] = dialog._row_widgets
        dialog._apply_match(widgets, existing)

        dialog._commit(None)

        [statement] = self.repo.list_statements()
        kept = self.repo.get_transaction(existing.id)
        self.assertIsNotNone(kept)
        self.assertIsNone(kept.statement_id)

        self.repo.delete_statement(statement.id)

        kept = self.repo.get_transaction(existing.id)
        self.assertIsNotNone(kept)
        self.assertIsNone(kept.statement_id)
        self.assertEqual(kept.source, "manual")
        self.assertEqual(kept.description, "FEL LINIS PIZZA")


if __name__ == "__main__":
    unittest.main()
