"""UI logic tests: drive the editors with real Flet controls against an in-memory page stub."""

import asyncio
import tempfile
import unittest
import unittest.mock
from datetime import date, timedelta
from pathlib import Path
from types import SimpleNamespace

from statement_fixture import make_statement_pdf

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
    Statement,
    Transaction,
    TransactionType,
)
from budget_tracker.repository import Repository, RepositoryError
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

    def _import_row(self, type_: TransactionType, cents: int, day: int, description: str,
                    *, duplicate: bool = False) -> "object":
        from budget_tracker.statements.model import ImportRow

        category_type = type_.category_type or TransactionType.EXPENSE
        category = self.repo.list_categories(category_type)[0]
        account = self.accts["Checking"]
        return ImportRow(
            transaction=Transaction(
                cents, type_, category.id, date(2026, 9, day), description,
                account_id=account.id,
            ),
            duplicate=duplicate,
            direction="income" if type_ is TransactionType.INCOME else "expense",
        )

    def _import_dialog(self, rows: list):
        from budget_tracker.statements.model import ImportPreview
        from budget_tracker.ui.views.statement_import import StatementImportDialog

        account = self.accts["Checking"]
        statement = Statement(
            account_id=account.id,
            institution="Regions Bank",
            account_label="62+ LIFEGREEN CHECKING",
            period_start=date(2026, 9, 16),
            period_end=date(2026, 10, 15),
            beginning_cents=100_000,
            deposits_cents=50_000,
            withdrawals_cents=30_000,
            fees_cents=1_000,
            ending_cents=119_000,
            file_sha256="c" * 64,
        )
        dialog = StatementImportDialog(self.ctx)
        dialog.account.value = str(account.id)
        dialog._install_preview(ImportPreview(statement=statement, account_id=account.id, rows=rows))
        return dialog

    def _widget_for(self, dialog, description: str):
        return next(
            widgets for widgets in dialog._row_widgets
            if widgets.row.transaction.description == description
        )

    def _set_opening(self, account_id: int, cents: int) -> None:
        account = self.repo.get_account(account_id)
        account.opening_balance_cents = cents
        self.repo.update_account(account)

    def test_statement_import_dialog_commits_only_included_rows(self):
        dialog = self._import_dialog([
            self._import_row(TransactionType.EXPENSE, 2_000, 20, "Groceries"),
            self._import_row(TransactionType.EXPENSE, 3_000, 25, "Rent"),
        ])
        groceries = self._widget_for(dialog, "Groceries")
        groceries.include.value = False
        dialog._row_toggled(groceries)

        with unittest.mock.patch.object(self.ctx.service, "commit_import") as commit:
            commit.return_value = dialog.preview.statement
            dialog._commit(None)

        payload = commit.call_args.args[1]
        self.assertEqual([row.transaction.description for row in payload], ["Rent"])
        self.assertEqual(len(self.repo.list_transactions()), 0)

    def test_statement_import_dialog_bulk_category_applies_to_selected_rows(self):
        dialog = self._import_dialog([
            self._import_row(TransactionType.EXPENSE, 2_000, 20, "Groceries"),
            self._import_row(TransactionType.EXPENSE, 3_000, 25, "Rent"),
            self._import_row(TransactionType.INCOME, 4_000, 2, "Payroll"),
        ])
        category = next(c for c in self.repo.list_categories(TransactionType.EXPENSE) if c.name == "Groceries")
        dialog.apply_category.value = str(category.id)
        dialog._apply_category(None)

        self.assertEqual(
            [self._widget_for(dialog, name).row.transaction.category_id for name in ("Groceries", "Rent")],
            [category.id, category.id],
        )
        self.assertNotEqual(self._widget_for(dialog, "Payroll").row.transaction.category_id, category.id)

    def test_statement_import_dialog_flags_duplicates_for_user_choice(self):
        dialog = self._import_dialog([
            self._import_row(TransactionType.EXPENSE, 2_000, 20, "Groceries", duplicate=True),
        ])
        duplicate = self._widget_for(dialog, "Groceries")
        self.assertFalse(duplicate.include.value)
        self.assertFalse(duplicate.row.include)
        row_container = dialog.rows_column.controls[1]
        self.assertIn("Duplicate", str(row_container.content.controls[-1].content.value))

        # The user may deliberately include a duplicate; only then is it part of the payload.
        duplicate.include.value = True
        dialog._row_toggled(duplicate)
        with unittest.mock.patch.object(self.ctx.service, "commit_import") as commit:
            commit.return_value = dialog.preview.statement
            dialog._commit(None)
        self.assertEqual([row.transaction.description for row in commit.call_args.args[1]], ["Groceries"])

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

    def test_account_editor_rejects_non_ascii_last_digits(self):
        from budget_tracker.ui.views.accounts import AccountEditor

        editor = AccountEditor(self.ctx, None)
        editor.open()
        editor.name.value = "Visa"
        editor.number_last4.value = "٠٦٠"
        with unittest.mock.patch.object(type(editor.dialog), "update"):
            editor._save(None)
        self.assertEqual(editor.number_last4.error, "Digits only")
        self.assertIsNone(next((a for a in self.repo.list_accounts() if a.name == "Visa"), None))

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

    def test_deposit_retyped_as_transfer_credits_statement_account(self):
        """A deposit retyped as a transfer must add money to the statement account.

        The ledger debits ``account_id`` and credits ``transfer_account_id``, so an income-origin
        transfer is oriented counterparty -> statement account, the opposite of an expense transfer.
        """
        from budget_tracker.ui.views.statement_import import StatementImportDialog

        checking = self.accts["Checking"]
        cash = self.accts["Cash"]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "statement.pdf"
            make_statement_pdf(path)
            preview = self.ctx.service.prepare_import(path, checking.id)
        dialog = StatementImportDialog(self.ctx)
        dialog._path = path
        dialog.account.value = str(checking.id)
        dialog._install_preview(preview)
        # Isolate the transfer: include only the $300 payroll deposit and do not set an opening balance.
        deposit = next(
            widgets
            for widgets in dialog._row_widgets
            if widgets.row.transaction.type is TransactionType.INCOME
            and widgets.row.transaction.amount_cents == 30_000
        )
        for widgets in dialog._row_widgets:
            widgets.include.value = widgets is deposit
            dialog._row_toggled(widgets)
        dialog.opening_balance.value = False

        deposit.type_dd.value = TransactionType.TRANSFER.value
        dialog._type_changed(deposit)
        self.assertEqual(deposit.destination_dd.label, "From account")
        deposit.destination_dd.value = str(cash.id)
        dialog._destination_changed(deposit)

        dialog._commit(None)
        self.assertEqual(self.page.errors, [])
        balances = {b.account.name: b.balance_cents for b in self.repo.account_balances()}
        self.assertEqual(balances["Checking"], 30_000)
        self.assertEqual(balances["Cash"], -30_000)
        [imported] = self.repo.list_transactions(account_id=cash.id)
        self.assertEqual(
            (imported.account_id, imported.transfer_account_id),
            (cash.id, checking.id),
        )

    def test_statement_import_dialog_confirms_account_switch_after_edits(self):
        dialog = self._import_dialog([self._import_row(TransactionType.EXPENSE, 2_000, 20, "Groceries")])
        dialog._path = Path("unused.pdf")
        widgets = self._widget_for(dialog, "Groceries")
        other = next(c for c in self.repo.list_categories(TransactionType.EXPENSE) if c.id != widgets.row.transaction.category_id)
        widgets.category_dd.value = str(other.id)
        dialog._category_changed(widgets)

        dialog.account.value = str(self.accts["Cash"].id)
        asyncio.run(dialog._account_changed(None))
        [confirm] = self.page.open_dialogs
        self.assertEqual(confirm.title.value, "Discard row changes?")
        confirm.actions[0].on_click(None)
        self.assertEqual(dialog.account.value, str(self.accts["Checking"].id))

        dialog.account.value = str(self.accts["Cash"].id)
        with unittest.mock.patch.object(dialog, "_load_for_account") as load:
            asyncio.run(dialog._account_changed(None))
            [confirm] = self.page.open_dialogs
            asyncio.run(confirm.actions[1].on_click(None))
        load.assert_called_once_with(self.accts["Cash"].id)

    def test_balance_check_adjustment_refreshes_when_follow_up_check_fails(self):
        from budget_tracker.ui.views.reconcile import BalanceCheckDialog

        dialog = BalanceCheckDialog(self.ctx, self.accts["Checking"])
        dialog.open()
        dialog.date.value = "2026-10-05"
        dialog.stated.value = "45"
        with unittest.mock.patch.object(type(dialog.dialog), "update"):
            dialog._save(None)
        dialog.adjustment.value = "-5.00"
        changes: list[bool] = []
        self.ctx.on_data_changed(lambda: changes.append(True))
        with unittest.mock.patch.object(
            self.ctx.service, "check_balance", side_effect=RepositoryError("follow-up failed")
        ):
            dialog._adjust(None)

        self.assertEqual(changes, [True])
        self.assertEqual(
            self.page.errors,
            ["The adjustment was recorded, but the follow-up balance check failed: follow-up failed"],
        )
        self.assertEqual(
            {b.account.name: b.balance_cents for b in self.repo.account_balances()}["Checking"], -500
        )
        self.assertTrue(dialog.adjust_button.disabled)

        # A failed recheck must not offer the same correction again.
        adjustments = self.repo.list_transactions(type_=TransactionType.ADJUSTMENT)
        self.assertEqual(len(adjustments), 1)
        dialog.adjustment.value = "-5.00"
        with unittest.mock.patch.object(self.ctx.service, "adjust_balance") as adjust:
            dialog._adjust(None)
        adjust.assert_not_called()
        self.assertEqual(len(self.repo.list_transactions(type_=TransactionType.ADJUSTMENT)), 1)
        self.assertEqual(
            self.page.errors[-1], "An adjustment was already recorded for this balance check"
        )

    def test_projection_uses_only_included_rows(self):
        self._set_opening(self.accts["Checking"].id, 100_000)
        dialog = self._import_dialog([
            self._import_row(TransactionType.INCOME, 50_000, 20, "Payroll"),
            self._import_row(TransactionType.EXPENSE, 30_000, 25, "Rent"),
            self._import_row(TransactionType.EXPENSE, 1_000, 30, "Fee"),
        ])
        self.assertEqual(dialog.projection_difference.value, "$0.00")

        payroll = self._widget_for(dialog, "Payroll")
        payroll.include.value = False
        dialog._row_toggled(payroll)
        self.assertEqual(dialog.projection_difference.value, "-$500.00")

    def test_projection_includes_activity_inside_the_period(self):
        self._set_opening(self.accts["Checking"].id, 100_000)
        groceries = next(c for c in self.repo.list_categories(TransactionType.EXPENSE) if c.name == "Groceries")
        self.repo.add_transaction(Transaction(
            5_000, TransactionType.EXPENSE, groceries.id, date(2026, 9, 18), "Manual",
            account_id=self.accts["Checking"].id,
        ))
        dialog = self._import_dialog([
            self._import_row(TransactionType.INCOME, 50_000, 20, "Payroll"),
            self._import_row(TransactionType.EXPENSE, 30_000, 25, "Rent"),
            self._import_row(TransactionType.EXPENSE, 1_000, 30, "Fee"),
        ])
        self.assertEqual(dialog.projection_difference.value, "-$50.00")

    def test_projection_credits_statement_account_for_income_origin_transfer(self):
        self._set_opening(self.accts["Checking"].id, 100_000)
        dialog = self._import_dialog([self._import_row(TransactionType.INCOME, 50_000, 20, "Payroll")])
        payroll = self._widget_for(dialog, "Payroll")
        payroll.type_dd.value = TransactionType.TRANSFER.value
        dialog._type_changed(payroll)
        payroll.destination_dd.value = str(self.accts["Cash"].id)
        dialog._destination_changed(payroll)

        # +50000 effect: the statement account is the credited side of the income-origin transfer.
        self.assertEqual(dialog.projection_difference.value, "+$310.00")

    def test_import_result_uses_the_imports_own_reconciliation(self):
        checking = self.accts["Checking"]
        newer = self.ctx.service.check_balance(checking.id, date(2026, 12, 1), 0)
        dialog = self._import_dialog([self._import_row(TransactionType.INCOME, 19_000, 20, "Payroll")])
        dialog.opening_balance.value = False
        with unittest.mock.patch(
            "budget_tracker.ui.views.statement_import.ImportResultDialog"
        ) as result_cls:
            dialog._commit(None)

        self.assertEqual(self.page.errors, [])
        self.assertIs(self.ctx.service.last_reconciliation(checking.id).id, newer.id)
        imported_id = dialog.preview.statement.id
        self.assertIsNotNone(imported_id)
        passed = result_cls.call_args.args[2]
        self.assertEqual(passed.statement_id, imported_id)
        self.assertEqual(passed.as_of, date(2026, 10, 15))
        self.assertEqual(self.repo.reconciliation_for_statement(imported_id).id, passed.id)

    def test_import_dialog_maps_read_error_to_message(self):
        from budget_tracker.statements.model import StatementReadError
        from budget_tracker.ui.views.statement_import import _notify_parse_error

        _notify_parse_error(self.ctx, StatementReadError("library detail"))
        self.assertEqual(self.page.errors, ["Could not read this file. Nothing was imported."])

    def test_parse_error_copy_is_generalized(self):
        from budget_tracker.statements.model import UnsupportedStatementError
        from budget_tracker.ui.views.statement_import import _notify_parse_error

        with unittest.mock.patch.object(self.ctx, "notify") as notify:
            _notify_parse_error(self.ctx, UnsupportedStatementError("no summary"))
        message = notify.call_args.args[0]
        self.assertNotIn("Regions", message)
        self.assertIn("statement", message.lower())

    def test_parse_error_copy_distinguishes_out_of_scope_statements(self):
        from budget_tracker.statements.model import UnsupportedStatementError
        from budget_tracker.ui.views.statement_import import _notify_parse_error

        with unittest.mock.patch.object(self.ctx, "notify") as notify:
            _notify_parse_error(
                self.ctx, UnsupportedStatementError("Credit card statements are not supported")
            )
        self.assertIn("credit card", notify.call_args.args[0].lower())

        with unittest.mock.patch.object(self.ctx, "notify") as notify:
            _notify_parse_error(
                self.ctx, UnsupportedStatementError("Only US dollar statements are supported")
            )
        message = notify.call_args.args[0]
        self.assertIn("US dollar", message)
        self.assertNotIn("Regions", message)

    def test_parse_error_copy_names_missing_ocr_engine(self):
        """Use the parser's real message so rewording it fails this test, not production."""
        from budget_tracker.statements import load_statement
        from budget_tracker.statements.model import UnsupportedStatementError
        from budget_tracker.ui.views.statement_import import _notify_parse_error

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "statement.png"
            path.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 16)
            with unittest.mock.patch(
                "budget_tracker.statements.ocr_available", return_value=False
            ):
                with self.assertRaises(UnsupportedStatementError) as raised:
                    load_statement(path)

        with unittest.mock.patch.object(self.ctx, "notify") as notify:
            _notify_parse_error(self.ctx, raised.exception)
        self.assertIn("OCR", notify.call_args.args[0])

    def test_ocr_preview_shows_badge(self):
        from budget_tracker.statements.model import ImportPreview

        rows = [self._import_row(TransactionType.EXPENSE, 2_000, 20, "Groceries")]
        dialog = self._import_dialog(rows)
        self.assertFalse(dialog.ocr_badge.visible)          # text preview: hidden
        dialog._install_preview(ImportPreview(
            statement=dialog.preview.statement, account_id=dialog.preview.account_id,
            rows=rows, source="ocr"))
        self.assertTrue(dialog.ocr_badge.visible)
        self.assertIn(dialog.ocr_badge, dialog.summary_holder.controls)

    def test_all_reparse_paths_are_async(self):
        import inspect

        from budget_tracker.ui.views.statement_import import StatementImportDialog

        self.assertTrue(inspect.iscoroutinefunction(StatementImportDialog._load_for_account))
        self.assertTrue(inspect.iscoroutinefunction(StatementImportDialog._account_changed))

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
            DashboardView._breakdown([CategoryTotal(1, "Groceries", "#43A047", 5000)])
        )

    def test_dashboard_breakdown_skips_stacked_bar_without_positive_totals(self):
        from budget_tracker.ui.views.dashboard import DashboardView
        totals = [
            CategoryTotal(1, "Groceries", "#43A047", 0),
            CategoryTotal(2, "Refunded", "#E53935", -500),
        ]
        controls = DashboardView._breakdown(totals).controls
        self.assertFalse(
            any(isinstance(control, ft.Container) and control.height == 14 for control in controls)
        )
        legend = controls[-1]
        self.assertEqual(legend.controls[2].value, "—")

    def test_adjustment_editor_saves_signed_amount_and_keeps_type(self):
        from budget_tracker.ui.components import _transaction_subtitle, amount_text, transaction_row
        from budget_tracker.ui.theme import EXPENSE_COLOR, INCOME_COLOR
        from budget_tracker.ui.views.transactions import TransactionsView

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

        TransactionsView(self.ctx)._confirm_delete(saved)
        self.assertIn("corrected (-$20.50) will be permanently removed", self.page.open_dialogs[-1].content.value)
        self.page.pop_dialog()

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

    def test_accounts_grid_stretches_cards_to_equal_height(self):
        from budget_tracker.ui.views.accounts import AccountsView

        view = AccountsView(self.ctx)
        self.assertEqual(view.grid.vertical_alignment, ft.CrossAxisAlignment.STRETCH)

    def test_year_view_builds_with_empty_data(self):
        from budget_tracker.ui.views.year import YearView

        view = YearView(self.ctx)
        self.assertEqual(view.year.value, str(date.today().year))
        self.assertTrue(view.body.controls)

    def test_year_view_shows_kpis_when_data_exists(self):
        from budget_tracker.models import Budget, Transaction, TransactionType
        from budget_tracker.ui.views.year import YearView

        groceries = next(
            category for category in self.repo.list_categories(TransactionType.EXPENSE)
            if category.name == "Groceries"
        )
        month = self.ctx.service.current_month()
        self.repo.set_budget(Budget(groceries.id, month, 50_000))
        self.repo.add_transaction(
            Transaction(
                60_000,
                TransactionType.EXPENSE,
                groceries.id,
                date.today(),
                "Test",
                account_id=self.accts["Checking"].id,
            )
        )
        view = YearView(self.ctx)
        self.assertGreaterEqual(len(view.body.controls), 3)

    def test_minimum_payment_dialog_records_and_lists(self):
        from budget_tracker.ui.views.minimum_payments import MinimumPaymentDialog

        card = self.accts["Credit Card"]
        dialog = MinimumPaymentDialog(self.ctx, card)
        dialog.month.value = "2026-11"
        dialog.amount.value = "35.00"
        dialog._save(None)
        self.assertEqual(
            [(payment.month, payment.amount_cents) for payment in self.repo.list_minimum_payments(card.id)],
            [("2026-11", 3500)],
        )
        self.assertEqual(dialog.history.controls[0].controls[0].value, "November 2026")

    def test_minimum_status_chip_states(self):
        from budget_tracker.models import MinimumPaymentStatus
        from budget_tracker.ui.views.minimum_payments import minimum_status_chip

        self.assertEqual(minimum_status_chip(MinimumPaymentStatus("2026-11", 3500, 3500)).value, "Paid")
        self.assertIn("Partial", minimum_status_chip(MinimumPaymentStatus("2026-11", 3500, 1000)).value)
        self.assertEqual(minimum_status_chip(MinimumPaymentStatus("2026-11", 3500, 0)).value, "Unpaid")
        self.assertEqual(minimum_status_chip(None).value, "Not recorded")

    def test_goals_view_empty_and_after_adding(self):
        from budget_tracker.models import Account, AccountKind
        from budget_tracker.ui.views.goals import GoalsView

        savings = self.repo.add_account(Account("Savings", AccountKind.SAVINGS))
        view = GoalsView(self.ctx)
        self.assertTrue(view.grid.controls)  # empty-state card
        self.ctx.service.create_savings_goal("Trip", 100_000, date.today() + timedelta(days=90), savings.id)
        view.refresh()
        self.assertEqual(len(view.grid.controls), 1)

    def test_goal_editor_save_replaces_the_empty_state_with_the_card(self):
        from budget_tracker.models import Account, AccountKind
        from budget_tracker.ui.views.goals import GoalEditor, GoalsView

        savings = self.repo.add_account(Account("Savings", AccountKind.SAVINGS))
        view = GoalsView(self.ctx)
        self.assertIn("No goals yet", self._texts(view.grid.controls[0]))

        editor = GoalEditor(self.ctx, None)
        editor.name.value = "Trip"
        editor.target.value = "500.00"
        editor.target_date.value = (date.today() + timedelta(days=90)).isoformat()
        editor.destination.value = str(savings.id)
        editor._save(None)

        self.assertEqual(len(view.grid.controls), 1)
        texts = self._texts(view.grid.controls[0])
        self.assertIn("Trip", texts)
        self.assertNotIn("No goals yet", texts)

    @staticmethod
    def _texts(control) -> list[str]:
        values = []
        value = getattr(control, "value", None)
        if isinstance(value, str):
            values.append(value)
        for child in getattr(control, "controls", None) or []:
            values.extend(TestEditors._texts(child))
        content = getattr(control, "content", None)
        if content is not None:
            values.extend(TestEditors._texts(content))
        return values

    def test_goals_grid_is_not_stretch_aligned(self):
        """Flet 1.1.2 collapses a STRETCH ResponsiveRow in the Goals scrollable body.

        The grid then renders no cards at all (not even the empty state), so keep it on the
        default alignment until the client handles STRETCH without an explicit height.
        """
        from budget_tracker.ui.views.goals import GoalsView

        view = GoalsView(self.ctx)
        self.assertNotEqual(view.grid.vertical_alignment, ft.CrossAxisAlignment.STRETCH)

    def test_contribute_dialog_prefills_and_saves(self):
        from budget_tracker.models import Account, AccountKind
        from budget_tracker.ui.views.goals import ContributeDialog, GoalsView

        savings = self.repo.add_account(Account("Savings", AccountKind.SAVINGS))
        goal = self.ctx.service.create_savings_goal("Trip", 90_000, date.today() + timedelta(days=90), savings.id)
        progress = self.ctx.service.goal_progress(goal.id)
        dialog = ContributeDialog(self.ctx, progress)
        self.assertEqual(dialog.amount.value, f"{progress.required_monthly_cents / 100:.2f}")
        dialog._save(None)
        self.assertEqual(self.ctx.service.goal_progress(goal.id).contributed_cents, progress.required_monthly_cents)

    def test_nav_indices_stay_in_sync_with_goals(self):
        from budget_tracker import main as shell
        self.assertEqual(shell.NAV_ITEMS[4][0], "Goals")
        self.assertEqual(shell.NAV_ITEMS[shell.BILLS_INDEX][0], "Bills")

    def test_editing_a_goal_contribution_preserves_the_tag(self):
        from budget_tracker.models import Account, AccountKind
        from budget_tracker.ui.views.transactions import TransactionEditor

        savings = self.repo.add_account(Account("Savings", AccountKind.SAVINGS))
        goal = self.ctx.service.create_savings_goal("Trip", 90_000, date.today() + timedelta(days=90), savings.id)
        self.ctx.service.contribute_to_goal(goal.id, self.accts["Checking"].id, 30_000, date.today())
        tx = self.repo.list_transactions(limit=1)[0]
        editor = TransactionEditor(self.ctx, tx)
        editor.amount.value = "60.00"
        editor._save(None)
        loaded = self.repo.list_transactions(limit=1)[0]
        self.assertEqual((loaded.amount_cents, loaded.goal_id), (6_000, goal.id))

    def test_debts_view_empty_and_after_adding(self):
        from budget_tracker.models import DebtKind
        from budget_tracker.ui.views.debts import DebtsView

        view = DebtsView(self.ctx)
        self.assertTrue(any("No debts" in text for text in self._texts(view.grid.controls[0])))
        debt = self.ctx.service.create_debt("Loan", DebtKind.PERSONAL, 100_000, 1200, 2_000)
        view.refresh()
        texts = self._texts(view.grid.controls[0])
        self.assertIn("Loan", texts)
        self.assertTrue(any("$1,000.00" in text for text in texts))

    def test_debts_grid_is_not_stretch_aligned(self):
        from budget_tracker.ui.views.debts import DebtsView

        view = DebtsView(self.ctx)
        self.assertNotEqual(view.grid.vertical_alignment, ft.CrossAxisAlignment.STRETCH)

    def test_debt_editor_creates_and_links(self):
        from budget_tracker.models import Account, AccountKind, DebtKind
        from budget_tracker.ui.views.debts import DebtEditor

        card = self.accts["Credit Card"]
        other = next(c for c in self.repo.list_categories() if c.name == "Other")
        self.repo.add_transaction(Transaction(
            40_000, TransactionType.EXPENSE, other.id, date(2026, 11, 1),
            "Purchase", account_id=card.id,
        ))
        editor = DebtEditor(self.ctx, None)
        editor.name.value = "Visa"
        editor.kind.value = DebtKind.CREDIT_CARD.value
        editor.apr.value = "24.99"
        editor.minimum.value = "105.00"
        editor.link.value = str(card.id)
        editor._save(None)
        debt = self.ctx.repo.list_debts()[0]
        self.assertEqual((debt.name, debt.balance_cents, debt.apr_bps), ("Visa", 40_000, 2499))
        self.assertEqual(debt.account_id, card.id)

    def test_debt_editor_rejects_a_bad_apr(self):
        from budget_tracker.models import DebtKind
        from budget_tracker.ui.views.debts import DebtEditor

        editor = DebtEditor(self.ctx, None)
        editor.name.value = "Loan"
        editor.kind.value = DebtKind.PERSONAL.value
        editor.balance.value = "500.00"
        editor.apr.value = "150"
        editor.minimum.value = "10.00"
        editor._save(None)
        self.assertEqual(self.ctx.repo.list_debts(), [])
        self.assertEqual(editor.apr.error, "Enter a rate between 0 and 100")

    def _statement_driven_debt(self):
        from budget_tracker.models import DebtKind

        card = self.accts["Credit Card"]
        other = next(c for c in self.repo.list_categories() if c.name == "Other")
        self.repo.add_transaction(Transaction(
            40_000, TransactionType.EXPENSE, other.id, date(2026, 11, 1),
            "Purchase", account_id=card.id,
        ))
        debt = self.ctx.service.create_debt("Visa", DebtKind.CREDIT_CARD, 0, 2499, 10_500,
                                            account_id=card.id)
        self.ctx.service.record_minimum_payment(card.id, self.ctx.service.current_month(), 7_500)
        return debt

    def test_debt_editor_keeps_the_stored_minimum_for_a_statement_driven_card(self):
        from budget_tracker.ui.views.debts import DebtEditor

        debt = self._statement_driven_debt()
        editor = DebtEditor(self.ctx, debt)
        self.assertEqual(editor.minimum.value, "75.00")
        editor.name.value = "Visa Signature"
        editor._save(None)
        stored = self.ctx.repo.get_debt(debt.id)
        self.assertEqual((stored.name, stored.minimum_cents), ("Visa Signature", 10_500))

    def test_debt_editor_disables_fields_driven_by_the_statement(self):
        from budget_tracker.ui.views.debts import DebtEditor

        debt = self._statement_driven_debt()
        editor = DebtEditor(self.ctx, debt)
        self.assertTrue(editor.balance.disabled)
        self.assertTrue(editor.minimum.disabled)
        self.assertEqual(editor.minimum.value, "75.00")
        self.assertTrue(editor.minimum_note.visible)
        self.assertIn("statement minimum", editor.minimum_note.value)

    def test_no_payment_warning_when_minimums_and_extra_are_zero(self):
        from budget_tracker.models import DebtKind
        from budget_tracker.ui.views.debts import DebtsView

        self.ctx.service.create_debt("Loan", DebtKind.PERSONAL, 100_000, 0, 0)
        view = DebtsView(self.ctx)
        self.assertIn("No payment is being made.", self._texts(view.plan_panel))

    def test_paid_off_debt_minimum_does_not_suppress_the_no_payment_warning(self):
        from budget_tracker.models import DebtKind
        from budget_tracker.ui.views.debts import DebtsView

        self.ctx.service.create_debt("Paid loan", DebtKind.PERSONAL, 0, 1200, 2_000)
        self.ctx.service.create_debt("Active loan", DebtKind.PERSONAL, 100_000, 0, 0)
        view = DebtsView(self.ctx)
        self.assertIn("No payment is being made.", self._texts(view.plan_panel))

    def test_nav_indices_stay_in_sync_with_debts(self):
        from budget_tracker import main as shell
        self.assertEqual(shell.NAV_ITEMS[5][0], "Debts")
        self.assertEqual(shell.NAV_ITEMS[shell.BILLS_INDEX][0], "Bills")

    def test_percent_parsing_and_formatting(self):
        from budget_tracker.formatting import format_percent, parse_percent_bps
        self.assertEqual(parse_percent_bps("24.99"), 2499)
        self.assertEqual(parse_percent_bps("0"), 0)
        self.assertEqual(parse_percent_bps("24.99%"), 2499)
        self.assertEqual(format_percent(2499), "24.99%")
        with self.assertRaises(ValueError):
            parse_percent_bps("101")

    def test_editing_a_debt_payment_preserves_the_tag(self):
        from budget_tracker.models import Debt, DebtKind
        from budget_tracker.ui.views.transactions import TransactionEditor

        debt = self.repo.add_debt(Debt("Loan", DebtKind.PERSONAL, 100_000, 1200, 2_000))
        category = next(c for c in self.repo.list_categories() if c.name == "Debt payments")
        tx = self.repo.add_transaction(Transaction(
            20_000, TransactionType.EXPENSE, category.id, date.today(), "Payment",
            account_id=self.accts["Checking"].id, debt_id=debt.id,
        ))
        editor = TransactionEditor(self.ctx, tx)
        editor.amount.value = "300.00"
        editor._save(None)

        loaded = self.repo.list_transactions(limit=1)[0]
        self.assertEqual((loaded.debt_id, loaded.amount_cents), (debt.id, 30_000))
        self.assertEqual(self.repo.get_debt(debt.id).balance_cents, 70_000)

    def test_balance_check_records_the_statement_minimum(self):
        from budget_tracker.ui.views.reconcile import BalanceCheckDialog

        card = self.accts["Credit Card"]
        dialog = BalanceCheckDialog(self.ctx, card)
        dialog.date.value = "2026-11-15"
        dialog.stated.value = "100.00"
        dialog.minimum.value = "35.00"
        dialog._save(None)
        self.assertEqual(
            [(payment.month, payment.amount_cents) for payment in self.repo.list_minimum_payments(card.id)],
            [("2026-11", 3500)],
        )

    def test_standalone_payment_dialog_moves_money(self):
        from budget_tracker.models import DebtKind
        from budget_tracker.ui.views.debts import DebtPaymentDialog

        debt = self.ctx.service.create_debt("Loan", DebtKind.PERSONAL, 100_000, 1200, 2_000)
        summary = self.ctx.service.debt_summary(debt.id)
        dialog = DebtPaymentDialog(self.ctx, summary)
        dialog.amount.value = "200.00"
        dialog.funding.value = str(self.accts["Checking"].id)
        dialog._save(None)
        self.assertEqual(self.ctx.repo.get_debt(debt.id).balance_cents, 80_000)
        [tx] = self.ctx.repo.list_debt_payments(debt.id)
        self.assertEqual((tx.type, tx.category_name), (TransactionType.EXPENSE, "Debt payments"))

    def test_linked_payment_dialog_creates_a_transfer(self):
        from budget_tracker.models import AccountKind, DebtKind, Transaction, TransactionType
        from budget_tracker.ui.views.debts import DebtPaymentDialog

        card = self.accts["Credit Card"]
        other = next(c for c in self.repo.list_categories() if c.name == "Other")
        self.repo.add_transaction(Transaction(
            40_000, TransactionType.EXPENSE, other.id, date(2026, 11, 1),
            "Purchase", account_id=card.id,
        ))
        debt = self.ctx.service.create_debt("Visa", DebtKind.CREDIT_CARD, 0, 2499, 10_500, account_id=card.id)
        dialog = DebtPaymentDialog(self.ctx, self.ctx.service.debt_summary(debt.id))
        dialog.amount.value = "100.00"
        dialog.funding.value = str(self.accts["Checking"].id)
        dialog._save(None)
        [tx] = self.ctx.repo.list_debt_payments(debt.id)
        self.assertEqual((tx.type, tx.transfer_account_id), (TransactionType.TRANSFER, card.id))

    def test_history_lists_payments_newest_first(self):
        from budget_tracker.models import DebtKind
        from budget_tracker.ui.views.debts import DebtHistoryDialog

        debt = self.ctx.service.create_debt("Loan", DebtKind.PERSONAL, 100_000, 1200, 2_000)
        self.ctx.service.record_debt_payment(debt.id, self.accts["Checking"].id, 10_000,
                                             date(2026, 11, 1), today=date(2026, 11, 14))
        self.ctx.service.record_debt_payment(debt.id, self.accts["Checking"].id, 20_000,
                                             date(2026, 11, 10), today=date(2026, 11, 14))
        dialog = DebtHistoryDialog(self.ctx, debt)
        texts = self._texts(dialog.dialog.content)
        self.assertLess(texts.index("$200.00"), texts.index("$100.00"))

    def test_comparison_amounts(self):
        from budget_tracker.ui.views.debts import comparison_amounts
        self.assertEqual(comparison_amounts(""), [0, 5_000, 10_000, 20_000])
        self.assertEqual(comparison_amounts("50"), [0, 5_000, 10_000, 20_000])
        self.assertEqual(comparison_amounts("75"), [0, 5_000, 10_000, 20_000, 7_500])
        self.assertEqual(comparison_amounts("nope"), [0, 5_000, 10_000, 20_000])

    def test_savings_label_requires_both_plans_to_finish(self):
        from budget_tracker.debt_planner import DebtStrategy, DebtPlan
        from budget_tracker.ui.views.debts import savings_cents

        def plan(finished, interest):
            return DebtPlan(DebtStrategy.AVALANCHE, 0, 1, "2026-12", interest, interest, finished, [], [], None)

        self.assertEqual(savings_cents(plan(True, 1_000), plan(True, 1_400)), 400)
        self.assertIsNone(savings_cents(plan(True, 1_000), plan(True, 1_000)))
        self.assertIsNone(savings_cents(plan(False, 1_000), plan(True, 1_400)))

    def test_plan_panel_shows_both_strategies_and_chunks_the_schedule(self):
        from budget_tracker.models import DebtKind
        from budget_tracker.ui.views.debts import DebtsView

        self.ctx.service.create_debt("Loan", DebtKind.PERSONAL, 100_000, 1200, 10_000)
        view = DebtsView(self.ctx)
        view.refresh()
        texts = self._texts(view.plan_panel)
        self.assertIn("Avalanche", texts)
        self.assertIn("Snowball", texts)
        self.assertEqual(view.schedule_limit, 24)
        view._show_more(None)
        self.assertEqual(view.schedule_limit, 48)

    def test_zero_minimum_and_unfinished_plans_warn(self):
        from budget_tracker.models import DebtKind
        from budget_tracker.ui.views.debts import DebtsView

        self.ctx.service.create_debt("Loan", DebtKind.PERSONAL, 100_000, 3600, 0)
        view = DebtsView(self.ctx)
        view.refresh()
        texts = " ".join(self._texts(view.plan_panel)).lower()
        self.assertIn("does not cover", texts)
        self.assertIn("not paid off within 50 years", texts)

    def test_custom_extra_updates_the_plan(self):
        from budget_tracker.models import DebtKind
        from budget_tracker.ui.views.debts import DebtsView

        self.ctx.service.create_debt("Loan", DebtKind.PERSONAL, 100_000, 0, 10_000)
        view = DebtsView(self.ctx)
        view._set_custom_extra("200")
        self.assertEqual(view.extra_cents, 20_000)


def _dialog_rule_texts(dialog) -> list:
    return [row.content.controls[1] for row in dialog.content.content.controls]


if __name__ == "__main__":
    unittest.main()
