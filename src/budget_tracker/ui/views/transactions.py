"""Transactions: searchable, filterable ledger with add/edit/delete and CSV export."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import flet as ft

from ...config import export_dir
from ...formatting import format_money, month_label, parse_date, parse_money, parse_signed_money
from ...models import Transaction, TransactionType
from ...receipts.storage import open_receipt
from ...repository import RepositoryError
from ..components import card, confirm_dialog, date_field, empty_state, page_header, transaction_row
from ..context import AppContext
from ..theme import EXPENSE_COLOR, GAP, INCOME_COLOR, fs
from .statement_import import StatementImportDialog, open_imported_statements

ALL = "all"


class TransactionsView:
    def __init__(self, ctx: AppContext) -> None:
        self.ctx = ctx
        self._file_picker = ft.FilePicker()
        self.search = ft.TextField(
            hint_text="Search description, category or account",
            prefix_icon=ft.Icons.SEARCH,
            on_change=lambda _: self.refresh(),
            dense=True,
            border_radius=10,
            col={"xs": 12, "lg": 4},
        )
        filter_col = {"xs": 6, "md": 3, "lg": 2}
        filter_style = {"dense": True, "expand": True, "text_size": 14, "col": filter_col}
        self.type_filter = ft.Dropdown(
            label="Type",
            value=ALL,
            options=[ft.DropdownOption(key=ALL, text="All types")]
            + [ft.DropdownOption(key=t.value, text=t.label) for t in TransactionType],
            on_select=lambda _: self.refresh(),
            **filter_style,
        )
        self.account_filter = ft.Dropdown(label="Account", value=ALL, on_select=lambda _: self.refresh(), **filter_style)
        self.category_filter = ft.Dropdown(label="Category", value=ALL, on_select=lambda _: self.refresh(), **filter_style)
        self.month_filter = ft.Dropdown(
            label="Month", value=ctx.service.current_month(), on_select=lambda _: self.refresh(), **filter_style
        )
        self.list_column = ft.Column(spacing=2)
        self.footer = ft.Row(spacing=24)
        self.import_button = ft.FilledTonalButton(
            content="Import statement",
            icon=ft.Icons.UPLOAD_FILE,
            tooltip="Review a statement before importing it",
            on_click=self._import_statement,
        )

        self.control = ft.Column(
            [
                page_header(
                    "Transactions",
                    "Track every dollar coming in and going out",
                    [
                        self.import_button,
                        ft.OutlinedButton(
                            content="Imported statements…",
                            icon=ft.Icons.HISTORY,
                            tooltip="Undo a statement import",
                            on_click=lambda _: open_imported_statements(self.ctx),
                        ),
                        ft.OutlinedButton(
                            content="Export CSV",
                            icon=ft.Icons.DOWNLOAD,
                            tooltip="Filtered transactions as CSV",
                            on_click=self._export,
                        ),
                        ft.OutlinedButton(
                            content="Export Excel",
                            icon=ft.Icons.TABLE_CHART_OUTLINED,
                            tooltip="Full workbook with formulas: summary, transactions, accounts, budgets, bills",
                            on_click=self._export_excel,
                        ),
                        ft.FilledButton(content="Add Transaction", icon=ft.Icons.ADD, on_click=lambda _: self.open_editor()),
                    ],
                ),
                card(
                    ft.ResponsiveRow(
                        [self.search, self.type_filter, self.account_filter, self.category_filter, self.month_filter],
                        spacing=12,
                        run_spacing=12,
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    ),
                    padding=14,
                ),
                card(ft.Column([self.list_column, ft.Divider(height=1), self.footer], spacing=12), padding=12),
            ],
            spacing=GAP,
            scroll=ft.ScrollMode.AUTO,
            expand=True,
        )
        ctx.on_data_changed(self.refresh)
        self.refresh()

    # ---------- data ----------
    def _filters(self) -> dict:
        type_ = None if self.type_filter.value in (None, ALL) else TransactionType(self.type_filter.value)
        category = None if self.category_filter.value in (None, ALL) else int(self.category_filter.value)
        account = None if self.account_filter.value in (None, ALL) else int(self.account_filter.value)
        month = None if self.month_filter.value in (None, ALL) else self.month_filter.value
        return {
            "type_": type_,
            "category_id": category,
            "account_id": account,
            "month": month,
            "search": self.search.value or None,
        }

    def _current_transactions(self) -> list[Transaction]:
        return self.ctx.repo.list_transactions(**self._filters())

    def _sync_filter_options(self) -> None:
        accounts = self.ctx.repo.list_accounts()
        self.account_filter.options = [ft.DropdownOption(key=ALL, text="All accounts")] + [
            ft.DropdownOption(key=str(a.id), text=a.name) for a in accounts
        ]
        if self.account_filter.value != ALL and self.account_filter.value not in {str(a.id) for a in accounts}:
            self.account_filter.value = ALL

        categories = self.ctx.repo.list_categories()
        self.category_filter.options = [ft.DropdownOption(key=ALL, text="All categories")] + [
            ft.DropdownOption(key=str(c.id), text=c.name) for c in categories
        ]
        if self.category_filter.value != ALL and self.category_filter.value not in {str(c.id) for c in categories}:
            self.category_filter.value = ALL

        months = set(self.ctx.repo.transaction_months()) | {self.ctx.service.current_month()}
        if self.month_filter.value and self.month_filter.value != ALL:
            months.add(self.month_filter.value)
        self.month_filter.options = [ft.DropdownOption(key=ALL, text="All time")] + [
            ft.DropdownOption(key=m, text=month_label(m)) for m in sorted(months, reverse=True)
        ]

    def refresh(self) -> None:
        self._sync_filter_options()
        transactions = self._current_transactions()
        if transactions:
            self.list_column.controls = [
                transaction_row(t, self.open_editor, self._confirm_delete, on_open_receipt=self._open_receipt)
                for t in transactions
            ]
        else:
            self.list_column.controls = [
                empty_state(
                    ft.Icons.RECEIPT_LONG,
                    "No transactions found",
                    "Try adjusting your filters, or add a new transaction.",
                    ft.FilledTonalButton(content="Add Transaction", icon=ft.Icons.ADD, on_click=lambda _: self.open_editor()),
                )
            ]
        income = sum(t.amount_cents for t in transactions if t.type == TransactionType.INCOME)
        spending = sum(t.amount_cents * -t.flow_sign for t in transactions if t.type in (TransactionType.EXPENSE, TransactionType.REFUND))
        self.footer.controls = [
            ft.Text(f"{len(transactions)} transaction(s)", color=ft.Colors.ON_SURFACE_VARIANT, expand=True),
            ft.Text(f"Income {format_money(income)}", color=INCOME_COLOR, weight=ft.FontWeight.W_600),
            ft.Text(
                f"Spending {format_money(spending)}",
                color=EXPENSE_COLOR,
                weight=ft.FontWeight.W_600,
                tooltip="Expenses minus refunds. Transfers (e.g. card payments) are excluded.",
            ),
            ft.Text(f"Net {format_money(income - spending, signed=True)}", weight=ft.FontWeight.BOLD),
        ]

    # ---------- actions ----------
    async def _import_statement(self, _: ft.Event) -> None:
        """Open the import review; the button is re-enabled when that dialog closes."""
        self.import_button.disabled = True
        self.import_button.update()
        dialog = StatementImportDialog(self.ctx, on_close=self._import_closed)
        await dialog.open()

    def _import_closed(self) -> None:
        self.import_button.disabled = False
        self.import_button.update()

    def _open_receipt(self, tx: Transaction) -> None:
        try:
            open_receipt(tx.receipt_path)
        except FileNotFoundError:
            self.ctx.notify("The receipt file is missing", error=True)
        except OSError as exc:
            self.ctx.notify(f"Could not open the receipt: {exc}", error=True)

    def _confirm_delete(self, tx: Transaction) -> None:
        def delete() -> None:
            self.ctx.repo.delete_transaction(tx.id)
            self.ctx.notify("Transaction deleted")
            self.ctx.data_changed()

        name = tx.description or tx.category_name or (
            "Adjustment" if tx.type == TransactionType.ADJUSTMENT else f"Transfer to {tx.transfer_account_name}"
        )
        confirm_dialog(
            self.ctx.page,
            "Delete transaction?",
            f"{name} ({format_money(tx.amount_cents)}) will be permanently removed.",
            delete,
        )

    async def _export(self, _: ft.Event) -> None:
        transactions = self._current_transactions()
        if not transactions:
            self.ctx.notify("Nothing to export with the current filters", error=True)
            return
        chosen = await self._choose_path("Export transactions", self.ctx.service.export_filename(), "csv")
        if not chosen:
            return
        try:
            path = self.ctx.service.export_csv(chosen, transactions)
        except OSError as exc:
            self.ctx.notify(f"Export failed: {exc.strerror or exc}", error=True)
            return
        self.ctx.notify(f"Exported {len(transactions)} transaction(s) to {path}")

    async def _export_excel(self, _: ft.Event) -> None:
        chosen = await self._choose_path("Export Excel workbook", self.ctx.service.export_xlsx_filename(), "xlsx")
        if not chosen:
            return
        try:
            path = self.ctx.service.export_xlsx(chosen)
        except OSError as exc:
            self.ctx.notify(f"Export failed (is the file open in another program?): {exc.strerror or exc}", error=True)
            return
        self.ctx.notify(f"Exported workbook to {path}")

    async def _choose_path(self, title: str, filename: str, extension: str) -> Path | None:
        try:
            chosen = await self._file_picker.save_file(
                dialog_title=title,
                file_name=filename,
                initial_directory=str(export_dir()),
                file_type=ft.FilePickerFileType.CUSTOM,
                allowed_extensions=[extension],
            )
        except Exception:  # noqa: BLE001 - fall back to the default folder if the native dialog is unavailable
            chosen = str(export_dir() / filename)
        if not chosen:
            return None
        path = Path(chosen)
        return path if path.suffix.lower() == f".{extension}" else path.with_name(f"{path.name}.{extension}")

    def open_editor(
        self,
        tx: Transaction | None = None,
        *,
        preset_type: TransactionType | None = None,
        preset_account_id: int | None = None,
        preset_to_account_id: int | None = None,
        preset_amount_cents: int | None = None,
    ) -> None:
        TransactionEditor(
            self.ctx,
            tx,
            preset_type=preset_type,
            preset_account_id=preset_account_id,
            preset_to_account_id=preset_to_account_id,
            preset_amount_cents=preset_amount_cents,
        ).open()


TYPE_HINTS = {
    TransactionType.EXPENSE: "Money spent. Pick the account you paid with, e.g. a credit card.",
    TransactionType.INCOME: "Money received into an account.",
    TransactionType.REFUND: "Money returned for a purchase. Reduces spending in the category.",
    TransactionType.TRANSFER: "Moves money between your accounts, e.g. paying a credit card bill. Not counted as spending.",
}


class TransactionEditor:
    """Modal dialog for creating or editing a transaction."""

    def __init__(
        self,
        ctx: AppContext,
        tx: Transaction | None,
        *,
        preset_type: TransactionType | None = None,
        preset_account_id: int | None = None,
        preset_to_account_id: int | None = None,
        preset_amount_cents: int | None = None,
    ) -> None:
        self.ctx = ctx
        self.tx = tx
        self.type = tx.type if tx else (preset_type or TransactionType.EXPENSE)
        self.is_adjustment = self.type == TransactionType.ADJUSTMENT
        accounts = ctx.repo.list_accounts()
        amount_cents = tx.amount_cents if tx else preset_amount_cents

        self.type_toggle = ft.SegmentedButton(
            selected=[self.type.value],
            segments=[
                ft.Segment(value=TransactionType.EXPENSE.value, label=ft.Text("Expense"), icon=ft.Icon(ft.Icons.ARROW_UPWARD)),
                ft.Segment(value=TransactionType.INCOME.value, label=ft.Text("Income"), icon=ft.Icon(ft.Icons.ARROW_DOWNWARD)),
                ft.Segment(value=TransactionType.REFUND.value, label=ft.Text("Refund"), icon=ft.Icon(ft.Icons.UNDO)),
                ft.Segment(value=TransactionType.TRANSFER.value, label=ft.Text("Transfer"), icon=ft.Icon(ft.Icons.SWAP_HORIZ)),
            ],
            on_change=self._type_changed,
        )
        self.adjustment_label = ft.Text("Adjustment", weight=ft.FontWeight.W_600, visible=self.is_adjustment)
        self.hint = ft.Text(size=fs(13), color=ft.Colors.ON_SURFACE_VARIANT)
        self.amount = ft.TextField(
            label="Amount",
            prefix_icon=ft.Icons.ATTACH_MONEY,
            value=f"{amount_cents / 100:.2f}" if amount_cents else "",
            keyboard_type=ft.KeyboardType.NUMBER,
            autofocus=True,
            on_submit=self._save,
        )
        account_options = [ft.DropdownOption(key=str(a.id), text=f"{a.name} ({a.kind.label})") for a in accounts]
        self.account = ft.Dropdown(
            label="Account",
            expand=True,
            options=account_options,
            value=self._pick(account_options, tx.account_id if tx else preset_account_id),
        )
        self.to_account = ft.Dropdown(
            label="To account",
            expand=True,
            options=list(account_options),
            value=self._pick(
                account_options,
                tx.transfer_account_id if tx else preset_to_account_id,
                fallback=account_options[1].key if len(account_options) > 1 else None,
            ),
        )
        self.category = ft.Dropdown(label="Category", expand=True)
        self.date = date_field(ctx.page, "Date", tx.occurred_on if tx else date.today())
        self.description = ft.TextField(label="Description (optional)", value=tx.description if tx else "", max_length=120, on_submit=self._save)
        self.account_row = ft.Row(spacing=12)
        if self.is_adjustment:
            self._setup_adjustment()
        else:
            self._load_categories(selected=tx.category_id if tx else None)
            self._apply_type()

        self.dialog = ft.AlertDialog(
            modal=True,
            title=ft.Text("Edit Transaction" if tx else "New Transaction"),
            content=ft.Container(
                width=520,
                content=ft.Column(
                    [
                        self.adjustment_label if self.is_adjustment else self.type_toggle,
                        self.hint,
                        self.amount,
                        self.account_row,
                        self.category,
                        self.date,
                        self.description,
                    ],
                    spacing=14,
                    tight=True,
                    horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
                ),
            ),
            actions=[
                ft.TextButton(content="Cancel", on_click=lambda _: ctx.page.pop_dialog()),
                ft.FilledButton(content="Save", icon=ft.Icons.CHECK, on_click=self._save),
            ],
            actions_alignment=ft.MainAxisAlignment.END,
        )

    @staticmethod
    def _pick(options: list[ft.DropdownOption], wanted: int | None, fallback: str | None = None) -> str | None:
        keys = [o.key for o in options]
        if wanted is not None and str(wanted) in keys:
            return str(wanted)
        return fallback if fallback is not None else (keys[0] if keys else None)

    def open(self) -> None:
        self.ctx.page.show_dialog(self.dialog)

    def _setup_adjustment(self) -> None:
        """A signed balance correction: no type toggle or category, just account/date/amount."""
        self.type_toggle.visible = False
        self.adjustment_label.visible = True
        self.hint.visible = False
        self.category.visible = False
        self.account_row.controls = [self.account]
        self.account.label = "Account"
        self.amount.keyboard_type = ft.KeyboardType.TEXT

    def _load_categories(self, selected: int | None = None) -> None:
        category_type = self.type.category_type
        categories = self.ctx.repo.list_categories(category_type) if category_type else []
        self.category.options = [ft.DropdownOption(key=str(c.id), text=c.name) for c in categories]
        self.category.value = self._pick(self.category.options, selected)

    def _apply_type(self) -> None:
        is_transfer = self.type == TransactionType.TRANSFER
        self.category.visible = not is_transfer
        self.to_account.visible = is_transfer
        self.account_row.controls = [self.account, self.to_account] if is_transfer else [self.account]
        self.account.label = {
            TransactionType.EXPENSE: "Paid with",
            TransactionType.INCOME: "Deposited to",
            TransactionType.REFUND: "Refunded to",
            TransactionType.TRANSFER: "From account",
        }[self.type]
        self.hint.value = TYPE_HINTS[self.type]

    def _type_changed(self, e: ft.Event[ft.SegmentedButton]) -> None:
        self.type = TransactionType(e.control.selected[0])
        self._load_categories()
        self._apply_type()

    def _save(self, _: ft.Event) -> None:
        valid = True
        try:
            amount = parse_signed_money(self.amount.value) if self.is_adjustment else parse_money(self.amount.value)
            self.amount.error = None
        except ValueError as exc:
            self.amount.error = str(exc)
            valid = False
        try:
            occurred = parse_date(self.date.value)
            self.date.error = None
        except ValueError as exc:
            self.date.error = str(exc)
            valid = False
        is_transfer = self.type == TransactionType.TRANSFER
        is_adjustment = self.type == TransactionType.ADJUSTMENT
        if not self.account.value:
            self.account.error_text = "Select an account (create one on the Accounts page)"
            valid = False
        else:
            self.account.error_text = None
        self.to_account.error_text = None
        if is_adjustment:
            self.category.error_text = None
        elif is_transfer:
            if not self.to_account.value or self.to_account.value == self.account.value:
                self.to_account.error_text = "Choose a different account"
                valid = False
        elif not self.category.value:
            self.category.error_text = "Select a category (create one on the Categories page)"
            valid = False
        else:
            self.category.error_text = None
        if not valid:
            self.dialog.update()
            return

        tx = Transaction(
            id=self.tx.id if self.tx else None,
            amount_cents=amount,
            type=self.type,
            category_id=None if (is_transfer or is_adjustment) else int(self.category.value),
            occurred_on=occurred,
            description=self.description.value or "",
            account_id=int(self.account.value),
            transfer_account_id=int(self.to_account.value) if is_transfer else None,
            goal_id=self.tx.goal_id if self.tx else None,
            debt_id=self.tx.debt_id if self.tx else None,
        )
        try:
            if tx.id:
                self.ctx.repo.update_transaction(tx)
            else:
                self.ctx.repo.add_transaction(tx)
        except RepositoryError as exc:
            self.ctx.notify(str(exc), error=True)
            return
        self.ctx.page.pop_dialog()
        self.ctx.notify("Transaction updated" if self.tx else "Transaction added")
        self.ctx.data_changed()
