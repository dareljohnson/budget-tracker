"""UI logic tests: drive the editors with real Flet controls against an in-memory page stub."""

import unittest
import unittest.mock
from datetime import date
from types import SimpleNamespace

try:
    import flet as ft
except ImportError:  # pragma: no cover - UI tests need Flet installed
    ft = None

from budget_tracker.db import connect
from budget_tracker.models import Bill, BillFrequency, CategoryTotal, Transaction, TransactionType
from budget_tracker.repository import Repository
from budget_tracker.services import BudgetService


class _PageStub:
    def __init__(self) -> None:
        self.overlay: list = []

    def show_dialog(self, control) -> None:
        self.overlay.append(control)

    def pop_dialog(self) -> None:
        dialogs = [c for c in self.overlay if isinstance(c, ft.AlertDialog)]
        if dialogs:
            self.overlay.remove(dialogs[-1])

    def update(self, *_) -> None:
        pass

    @property
    def open_dialogs(self) -> list:
        return [c for c in self.overlay if isinstance(c, ft.AlertDialog)]

    @property
    def errors(self) -> list[str]:
        return [c.content.value for c in self.overlay if isinstance(c, ft.SnackBar) and c.bgcolor != "#2E7D32"]


def _event(**attrs) -> SimpleNamespace:
    return SimpleNamespace(control=SimpleNamespace(**attrs))


@unittest.skipIf(ft is None, "flet is not installed")
class TestEditors(unittest.TestCase):
    def setUp(self) -> None:
        from budget_tracker.ui.context import AppContext

        self.repo = Repository(connect(":memory:"))
        self.page = _PageStub()
        self.ctx = AppContext(page=self.page, repo=self.repo, service=BudgetService(self.repo))
        self.accts = {a.name: a for a in self.repo.list_accounts()}

    def tearDown(self) -> None:
        self.repo.close()

    def _editor(self, **kwargs):
        from budget_tracker.ui.views.transactions import TransactionEditor

        editor = TransactionEditor(self.ctx, kwargs.pop("tx", None), **kwargs)
        editor.open()
        return editor

    def test_refund_on_card_then_pay_card_from_accounts_view(self):
        from budget_tracker.ui.views.accounts import AccountsView

        card_id = str(self.accts["Credit Card"].id)
        for type_, amount in (("expense", "120"), ("refund", "20")):
            editor = self._editor()
            editor._type_changed(_event(selected=[type_]))
            self.assertTrue(editor.category.visible)
            self.assertFalse(editor.to_account.visible)
            editor.amount.value = amount
            editor.account.value = card_id
            editor._save(None)
        self.assertEqual(self.page.errors, [])
        self.assertEqual(self.page.open_dialogs, [])

        view = AccountsView(self.ctx)
        [card] = [b for b in self.repo.account_balances() if b.account.kind.is_liability]
        view._pay_card(card)
        [dialog] = self.page.open_dialogs
        _, _, amount, account_row, category, *_ = dialog.content.content.controls
        from_account, to_account = account_row.controls
        self.assertEqual((amount.value, from_account.label), ("100.00", "From account"))
        self.assertEqual(to_account.value, str(card.account.id))
        self.assertTrue(to_account.visible)
        self.assertFalse(category.visible)
        dialog.actions[-1].on_click(None)
        self.assertEqual(self.page.errors, [])
        self.assertEqual(self.page.open_dialogs, [])

        balances = {b.account.name: b.balance_cents for b in self.repo.account_balances()}
        self.assertEqual((balances["Credit Card"], balances["Checking"]), (0, -10000))
        self.assertEqual(self.repo.summary(self.ctx.service.current_month()).expense_cents, 10000)

    def test_edit_transfer_restores_accounts(self):
        tx = self.repo.add_transaction(
            Transaction(
                500, TransactionType.TRANSFER, None, date.today(),
                account_id=self.accts["Checking"].id, transfer_account_id=self.accts["Cash"].id,
            )
        )
        editor = self._editor(tx=tx)
        self.assertEqual(editor.account.value, str(self.accts["Checking"].id))
        self.assertEqual(editor.to_account.value, str(self.accts["Cash"].id))
        self.assertTrue(editor.to_account.visible)

    def test_card_account_opening_balance_is_entered_as_amount_owed(self):
        from budget_tracker.ui.views.accounts import AccountEditor

        editor = AccountEditor(self.ctx, None)
        editor.open()
        editor.name.value = "Visa"
        editor._kind_changed(_event(value="credit_card"))
        self.assertEqual(editor.opening.label, "Amount currently owed")
        editor.opening.value = "250"
        editor._save(None)
        visa = next(a for a in self.repo.list_accounts() if a.name == "Visa")
        self.assertEqual(visa.opening_balance_cents, -25000)
        self.assertEqual(AccountEditor(self.ctx, visa).opening.value, "250.00")

    def test_views_render(self):
        from budget_tracker.ui.views.accounts import AccountsView
        from budget_tracker.ui.views.dashboard import DashboardView
        from budget_tracker.ui.views.transactions import TransactionsView

        self.repo.add_transaction(
            Transaction(
                500, TransactionType.TRANSFER, None, date.today(),
                account_id=self.accts["Checking"].id, transfer_account_id=self.accts["Cash"].id,
            )
        )
        for view_type in (DashboardView, TransactionsView, AccountsView):
            self.assertIsNotNone(view_type(self.ctx).control)

    def test_bill_editor_creates_card_payment_and_mark_paid_uses_balance_owed(self):
        from budget_tracker.ui.views.bills import BillEditor, BillsView, MarkPaidDialog

        self.repo.add_transaction(
            Transaction(4500, TransactionType.EXPENSE, self.repo.list_categories(TransactionType.EXPENSE)[0].id, date.today(),
                        account_id=self.accts["Credit Card"].id)
        )
        editor = BillEditor(self.ctx, None)
        editor.open()
        editor._kind_changed(_event(selected=["card"]))
        self.assertTrue(editor.pay_to.visible)
        self.assertFalse(editor.category.visible)
        self.assertEqual(editor.account.value, str(self.accts["Checking"].id))
        self.assertEqual(editor.pay_to.value, str(self.accts["Credit Card"].id))
        editor.name.value = "Visa payment"
        editor.amount.value = ""
        editor._save(None)
        self.assertEqual((self.page.errors, self.page.open_dialogs), ([], []))
        [bill] = self.repo.list_bills()
        self.assertTrue(bill.is_card_payment)
        self.assertEqual(bill.amount_cents, 0)

        view = BillsView(self.ctx)
        self.assertEqual(view.stats.controls[1].content.controls[0].controls[1].value, "1")
        dialog = MarkPaidDialog(self.ctx, bill)
        dialog.open()
        self.assertEqual(dialog.amount.value, "45.00")
        self.assertNotIn(str(self.accts["Credit Card"].id), [o.key for o in dialog.account.options])
        dialog._save(None)
        self.assertEqual((self.page.errors, self.page.open_dialogs), ([], []))
        balances = {b.account.name: b.balance_cents for b in self.repo.account_balances()}
        self.assertEqual((balances["Credit Card"], balances["Checking"]), (0, -4500))
        self.assertEqual(self.repo.get_bill(bill.id).next_due.month, (date.today().month % 12) + 1)

    def test_bill_editor_validation_and_startup_alert(self):
        from budget_tracker.main import show_due_bills_alert
        from budget_tracker.ui.views.bills import BillEditor
        from budget_tracker.ui.views.dashboard import DashboardView

        editor = BillEditor(self.ctx, None)
        editor.open()
        with unittest.mock.patch.object(type(editor.dialog), "update"):
            editor._save(None)
        self.assertEqual(editor.name.error, "Name is required")
        self.assertIsNotNone(editor.amount.error)
        self.assertEqual(editor.category.error_text, "Required")
        self.assertFalse(self.repo.list_bills())
        self.assertFalse(show_due_bills_alert(self.ctx, lambda: None))

        editor.name.value = "Internet"
        editor.amount.value = "60"
        editor.category.value = editor.category.options[0].key
        editor._save(None)
        self.assertEqual((self.page.errors, self.page.open_dialogs), ([], []))
        self.assertIsNotNone(DashboardView(self.ctx).control)

        opened = []
        self.assertTrue(show_due_bills_alert(self.ctx, lambda: opened.append(True)))
        [alert] = self.page.open_dialogs
        alert.actions[-1].on_click(None)
        self.assertEqual((opened, self.page.open_dialogs), ([True], []))

    def test_bill_editor_preserves_inactive_state(self):
        from budget_tracker.ui.views.bills import BillEditor
        self.repo.add_bill(Bill(
            name="Gym", amount_cents=4000, frequency=BillFrequency.ONCE,
            next_due=date(2026, 3, 1), account_id=self.accts["Checking"].id,
            category_id=self.repo.list_categories(TransactionType.EXPENSE)[0].id,
            active=False,
        ))
        bill = self.repo.list_bills()[0]
        editor = BillEditor(self.ctx, bill)
        editor.open()
        editor.name.value = "Gym (renamed)"
        editor._save(None)
        self.assertFalse(self.repo.get_bill(bill.id).active)

    def test_dashboard_breakdown_survives_zero_net_spending(self):
        from budget_tracker.ui.views.dashboard import DashboardView
        self.assertIsNotNone(
            DashboardView._breakdown([CategoryTotal(1, "Groceries", "#43A047", 5000)], 0)
        )


if __name__ == "__main__":
    unittest.main()
