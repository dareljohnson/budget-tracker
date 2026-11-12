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
from budget_tracker.models import (
    Account,
    AccountKind,
    Bill,
    BillFrequency,
    CategoryTotal,
    Transaction,
    TransactionType,
)
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

    def test_account_editor_preserves_number_last4_when_renaming(self):
        """Regression: editing an account must never drop its stored account suffix."""
        from budget_tracker.ui.views.accounts import AccountEditor

        account = self.repo.add_account(Account("Joint", AccountKind.CHECKING, number_last4="3060"))
        editor = AccountEditor(self.ctx, self.repo.get_account(account.id))
        editor.open()
        self.assertEqual(editor.number_last4.value, "3060")

        editor.name.value = "Joint (renamed)"
        editor._save(None)

        self.assertEqual(self.page.errors, [])
        self.assertEqual(self.page.open_dialogs, [])
        saved = self.repo.get_account(account.id)
        self.assertEqual((saved.name, saved.number_last4), ("Joint (renamed)", "3060"))

    def test_account_editor_validates_and_saves_last_digits(self):
        from budget_tracker.ui.views.accounts import AccountEditor

        editor = AccountEditor(self.ctx, None)
        editor.open()
        editor.name.value = "Visa"
        editor.number_last4.value = "12a4"
        with unittest.mock.patch.object(type(editor.dialog), "update"):
            editor._save(None)
        self.assertEqual(editor.number_last4.error, "Digits only")
        self.assertIsNone(next((a for a in self.repo.list_accounts() if a.name == "Visa"), None))

        editor.number_last4.value = "060"
        editor._save(None)
        self.assertEqual(self.page.errors, [])
        self.assertEqual(next((a for a in self.repo.list_accounts() if a.name == "Visa"), None).number_last4, "060")

    def test_balance_check_adjustment_resolves_and_records_history(self):
        from budget_tracker.ui.views.reconcile import BalanceCheckDialog

        self.repo.add_transaction(
            Transaction(5000, TransactionType.INCOME, self.repo.list_categories(TransactionType.INCOME)[0].id,
                        date(2026, 10, 1), "pay", account_id=self.accts["Checking"].id)
        )
        dialog = BalanceCheckDialog(self.ctx, self.accts["Checking"])
        dialog.open()
        dialog.date.value = "2026-10-05"
        dialog.stated.value = "45"
        dialog.note.value = "bank app"
        dialog._save(None)

        self.assertEqual((self.page.errors, self.page.open_dialogs), ([], [dialog.dialog]))
        self.assertTrue(dialog.result.visible)
        self.assertEqual(
            (dialog.computed_value.value, dialog.stated_value.value, dialog.difference_value.value),
            ("$50.00", "$45.00", "$5.00"),
        )
        self.assertTrue(dialog.adjust_row.visible)
        self.assertEqual(dialog.adjustment.value, "-5.00")
        self.assertEqual(dialog.adjustment.label, "Correction to ledger")
        self.assertIn("added to the ledger balance", dialog.adjustment.hint_text.lower())
        self.assertEqual(dialog.result.controls[2].controls[0].value, "Difference (ledger - statement)")

        dialog._adjust(None)
        self.assertFalse(dialog.adjust_row.visible)
        self.assertEqual((dialog.computed_value.value, dialog.difference_value.value), ("$45.00", "$0.00"))
        records = self.repo.list_reconciliations(self.accts["Checking"].id)
        self.assertEqual([r.difference_cents for r in records], [0, 500])
        self.assertEqual({b.account.name: b.balance_cents for b in self.repo.account_balances()}["Checking"], 4500)
        self.assertEqual(self.repo.summary("2026-10").expense_cents, 0)

    def test_balance_check_liability_uses_positive_amount_owed(self):
        from budget_tracker.ui.views.reconcile import BalanceCheckDialog

        self.repo.add_transaction(
            Transaction(25000, TransactionType.EXPENSE, self.repo.list_categories(TransactionType.EXPENSE)[0].id,
                        date(2026, 10, 1), "shopping", account_id=self.accts["Credit Card"].id)
        )
        dialog = BalanceCheckDialog(self.ctx, self.accts["Credit Card"])
        dialog.open()
        self.assertEqual(dialog.stated.label, "Amount owed")
        dialog.date.value = "2026-10-05"
        dialog.stated.value = "240"
        dialog._save(None)

        self.assertEqual(
            (dialog.computed_value.value, dialog.stated_value.value, dialog.difference_value.value),
            ("$250.00", "$240.00", "$10.00"),
        )
        self.assertEqual(dialog.adjustment.value, "10.00")
        self.assertEqual(self.repo.list_reconciliations(self.accts["Credit Card"].id)[0].stated_cents, -24000)

        dialog._adjust(None)
        self.assertFalse(dialog.adjust_row.visible)
        self.assertEqual({b.account.name: b.balance_cents for b in self.repo.account_balances()}["Credit Card"], -24000)
        newest = self.repo.list_reconciliations(self.accts["Credit Card"].id)[0]
        self.assertEqual((newest.stated_cents, newest.computed_cents, newest.difference_cents), (-24000, -24000, 0))

    def test_balance_check_rejects_blank_stated_and_negative_owed(self):
        from budget_tracker.ui.views.reconcile import BalanceCheckDialog

        dialog = BalanceCheckDialog(self.ctx, self.accts["Credit Card"])
        dialog.open()
        dialog.date.value = "2026-10-05"
        with unittest.mock.patch.object(type(dialog.dialog), "update"):
            dialog.stated.value = ""
            dialog._save(None)
            self.assertEqual(dialog.stated.error, "Enter the stated balance")
            self.assertEqual(self.repo.list_reconciliations(self.accts["Credit Card"].id), [])

            dialog.stated.value = "-10"
            dialog._save(None)
        self.assertEqual(dialog.stated.error, "Enter the amount owed as a positive number")
        self.assertEqual(self.repo.list_reconciliations(self.accts["Credit Card"].id), [])

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

    def test_adjustment_editor_saves_signed_amount_and_keeps_type(self):
        from budget_tracker.ui.components import _transaction_subtitle, amount_text, transaction_row
        from budget_tracker.ui.theme import EXPENSE_COLOR, INCOME_COLOR

        tx = self.repo.add_transaction(
            Transaction(
                -1500, TransactionType.ADJUSTMENT, None, date(2026, 11, 3), "bank correction",
                account_id=self.accts["Checking"].id,
            )
        )
        editor = self._editor(tx=tx)
        self.assertIs(editor.type, TransactionType.ADJUSTMENT)
        self.assertFalse(editor.type_toggle.visible)
        self.assertFalse(editor.category.visible)
        self.assertTrue(editor.adjustment_label.visible)
        self.assertEqual(editor.amount.value, "-15.00")
        self.assertEqual(editor.account.value, str(self.accts["Checking"].id))

        editor.amount.value = "-20.50"
        editor.date.value = "2026-11-04"
        editor.description.value = "corrected"
        editor._save(None)

        self.assertEqual((self.page.errors, self.page.open_dialogs), ([], []))
        [saved] = self.repo.list_transactions(type_=TransactionType.ADJUSTMENT)
        self.assertEqual(
            (saved.type, saved.amount_cents, saved.category_id),
            (TransactionType.ADJUSTMENT, -2050, None),
        )
        self.assertEqual((saved.occurred_on, saved.description), (date(2026, 11, 4), "corrected"))
        summary = self.repo.summary("2026-11")
        self.assertEqual((summary.income_cents, summary.expense_cents), (0, 0))

        negative = amount_text(saved)
        self.assertEqual((negative.value, negative.color), ("-$20.50", EXPENSE_COLOR))
        row = transaction_row(saved)
        self.assertEqual(row.content.controls[1].controls[0].value, "corrected")
        self.assertEqual(_transaction_subtitle(saved), "Adjustment  •  Checking  •  Nov 04, 2026")

        self.repo.add_transaction(
            Transaction(
                500, TransactionType.ADJUSTMENT, None, date(2026, 11, 5), "",
                account_id=self.accts["Checking"].id,
            )
        )
        positive = next(t for t in self.repo.list_transactions(type_=TransactionType.ADJUSTMENT) if t.amount_cents > 0)
        self.assertEqual((amount_text(positive).value, amount_text(positive).color), ("+$5.00", INCOME_COLOR))

        editor.amount.value = "0"
        editor._save(None)
        self.assertEqual(self.page.errors, ["Adjustment amount cannot be zero"])

    def test_category_rules_dialog_lists_and_deletes_rules(self):
        from budget_tracker.ui.views.categories import CategoriesView
        from budget_tracker.ui.views.category_rules import CategoryRulesDialog

        groceries = self.repo.list_categories(TransactionType.EXPENSE)[0]
        income = self.repo.list_categories(TransactionType.INCOME)[0]
        self.repo.add_category_rule("Kroger", groceries.id)
        self.repo.add_category_rule("PAYROLL", income.id)

        view = CategoriesView(self.ctx)
        actions = view.control.controls[0].controls[1].controls
        rules_button = next(button for button in actions if getattr(button, "content", None) == "Rules")
        rules_button.on_click(None)
        [opened] = self.page.open_dialogs
        self.assertIn("Kroger", " ".join(text.value for text in _dialog_rule_texts(opened)))

        dialog = CategoryRulesDialog(self.ctx)
        dialog.open()
        self.assertEqual(len(dialog.list_column.controls), 2)
        kroger = next(row for row in dialog.list_column.controls if "Kroger" in row.content.controls[1].value)
        self.assertEqual(kroger.content.controls[1].value, f'"Kroger" → {groceries.name}')
        kroger.content.controls[-1].on_click(None)
        self.assertEqual([rule.match_text for rule in self.repo.list_category_rules()], ["PAYROLL"])
        self.assertEqual(len(dialog.list_column.controls), 1)

        dialog._delete(self.repo.list_category_rules()[0])
        self.assertEqual(dialog.list_column.controls[0].value, "No rules yet — save one from the import preview.")
        self.assertEqual(self.repo.list_category_rules(), [])
        self.assertIsNone(self.repo.suggest_category_id("Card Purchase Kroger #443", TransactionType.EXPENSE))


def _dialog_rule_texts(dialog) -> list:
    return [row.content.controls[1] for row in dialog.content.content.controls]


if __name__ == "__main__":
    unittest.main()
