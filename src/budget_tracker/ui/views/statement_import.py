"""Statement import: review a parsed PDF, confirm it, and undo an import later."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import flet as ft

from ...formatting import format_money
from ...models import Category, Statement, TransactionType
from ...repository import RepositoryError
from ...statements.model import (
    ImportPreview,
    ImportRow,
    StatementValidationError,
    UnsupportedStatementError,
)
from ..components import confirm_dialog, empty_state
from ..context import AppContext
from ..theme import EXPENSE_COLOR, GAP, INCOME_COLOR, WARNING_COLOR

INCLUDE_WIDTH = 36
DATE_WIDTH = 84
AMOUNT_WIDTH = 92
TYPE_WIDTH = 124
CATEGORY_WIDTH = 148
DESTINATION_WIDTH = 148
DUPLICATE_WIDTH = 76


def _notify_parse_error(ctx: AppContext, exc: Exception) -> None:
    """Turn a parse failure into a clear message; nothing has been written at this point."""
    if isinstance(exc, StatementValidationError):
        details = "\n".join(f"• {error}" for error in exc.errors)
        ctx.notify(f"Statement did not validate. Nothing was imported.\n{details}", error=True)
    elif isinstance(exc, UnsupportedStatementError):
        ctx.notify(
            "This doesn't look like a supported Regions checking statement. Nothing was imported.",
            error=True,
        )
    elif isinstance(exc, OSError):
        ctx.notify(f"Could not read this PDF: {exc.strerror or exc}. Nothing was imported.", error=True)
    else:
        ctx.notify(str(exc), error=True)


def _period(statement: Statement) -> str:
    return f"{statement.period_start:%b %d, %Y} – {statement.period_end:%b %d, %Y}"


@dataclass(slots=True)
class _RowWidgets:
    row: ImportRow
    include: ft.Checkbox
    amount: ft.Text
    type_dd: ft.Dropdown
    category_dd: ft.Dropdown
    destination_dd: ft.Dropdown


class StatementImportDialog:
    """Review dialog for one statement PDF. Nothing is written until Import is pressed."""

    def __init__(self, ctx: AppContext) -> None:
        self.ctx = ctx
        self._file_picker = ft.FilePicker()
        self._path: Path | None = None
        self.preview: ImportPreview | None = None
        self._row_widgets: list[_RowWidgets] = []
        self._accounts = ctx.repo.list_accounts()
        self._categories: dict[TransactionType, list[Category]] = {}

        self.account = ft.Dropdown(
            label="Account",
            hint_text="Choose the account this statement belongs to",
            width=340,
            options=[ft.DropdownOption(key=str(a.id), text=f"{a.name} ({a.kind.label})") for a in self._accounts],
            on_select=self._account_changed,
        )
        self.match_note = ft.Text("", size=12, color=INCOME_COLOR, weight=ft.FontWeight.W_600, visible=False)
        self.statement_meta = ft.Text("", size=12, color=ft.Colors.ON_SURFACE_VARIANT)
        self.summary_holder = ft.Row([], spacing=28, wrap=True, vertical_alignment=ft.CrossAxisAlignment.CENTER)
        self.rows_count = ft.Text("", size=12, color=ft.Colors.ON_SURFACE_VARIANT)
        self.rows_column = ft.Column(spacing=2, tight=True)

        self.select_all = ft.Checkbox(label="Select all", value=False, on_change=self._select_all_changed)
        self.apply_category = ft.Dropdown(
            label="Apply category to selected",
            width=250,
            dense=True,
            text_size=13,
            options=self._all_category_options(),
        )
        self.apply_button = ft.FilledTonalButton(
            content="Apply", icon=ft.Icons.CHECK, on_click=self._apply_category
        )
        self.opening_balance = ft.Checkbox(value=True, visible=False)
        self.remember = ft.Checkbox(value=True, visible=False)
        self.import_button = ft.FilledButton(
            content="Import", icon=ft.Icons.UPLOAD_FILE, disabled=True, on_click=self._commit
        )

        self.dialog = ft.AlertDialog(
            modal=True,
            title=ft.Text("Import statement"),
            content=ft.Container(
                width=1000,
                height=560,
                content=ft.Column(
                    [
                        ft.Row([self.account, self.match_note], spacing=16, vertical_alignment=ft.CrossAxisAlignment.CENTER),
                        self.statement_meta,
                        ft.Container(
                            content=self.summary_holder,
                            padding=14,
                            border_radius=12,
                            bgcolor=ft.Colors.SURFACE_CONTAINER_LOW,
                            border=ft.Border.all(1, ft.Colors.OUTLINE_VARIANT),
                        ),
                        ft.Row(
                            [self.select_all, self.apply_category, self.apply_button],
                            spacing=12,
                            vertical_alignment=ft.CrossAxisAlignment.CENTER,
                        ),
                        self.rows_count,
                        self.rows_column,
                        self.opening_balance,
                        self.remember,
                        ft.Text(
                            "PDFs are parsed locally. Nothing is written until you choose Import.",
                            size=11,
                            color=ft.Colors.ON_SURFACE_VARIANT,
                        ),
                    ],
                    spacing=GAP,
                    scroll=ft.ScrollMode.AUTO,
                ),
            ),
            actions=[
                ft.TextButton(content="Cancel", on_click=lambda _: self._close()),
                self.import_button,
            ],
            actions_alignment=ft.MainAxisAlignment.END,
        )

    def _close(self) -> None:
        """Close this dialog even when a SnackBar is stacked on top of it."""
        while self.dialog.open:
            if self.ctx.page.pop_dialog() is None:
                break

    # ---------- opening ----------
    async def open(self) -> None:
        if not self._accounts:
            self.ctx.notify("Create an account first, then import a statement", error=True)
            return
        try:
            files = await self._file_picker.pick_files(
                dialog_title="Import statement",
                file_type=ft.FilePickerFileType.CUSTOM,
                allowed_extensions=["pdf"],
            )
        except Exception as exc:  # noqa: BLE001 - native picker can be unavailable
            self.ctx.notify(f"Could not open the file picker: {exc}", error=True)
            return
        chosen = next((f for f in files or [] if f.path), None) if files else None
        if chosen is None:
            return
        self._path = Path(chosen.path)

        # Parsing needs an account for duplicate detection. Parsing once with a provisional
        # account reveals the statement's account digits, which then select the real account.
        try:
            preview = self.ctx.service.prepare_import(self._path, self._accounts[0].id)
        except (StatementValidationError, UnsupportedStatementError, OSError, RepositoryError) as exc:
            _notify_parse_error(self.ctx, exc)
            return

        matched = self.ctx.service.match_account(preview.statement.account_last4)
        if matched is not None:
            self.account.value = str(matched.id)
            if matched.id != preview.account_id:
                try:
                    preview = self.ctx.service.prepare_import(self._path, matched.id)
                except (StatementValidationError, UnsupportedStatementError, OSError, RepositoryError) as exc:
                    _notify_parse_error(self.ctx, exc)
                    return
        self._install_preview(preview)
        self.ctx.page.show_dialog(self.dialog)

    # ---------- preview ----------
    def _install_preview(self, preview: ImportPreview) -> None:
        self.preview = preview
        statement = preview.statement
        self.statement_meta.value = (
            f"{statement.institution}  •  {statement.account_label}  •  {_period(statement)}"
        )
        self.summary_holder.controls = [
            *self._summary_stats(statement),
            ft.Container(
                content=ft.Row(
                    [
                        ft.Icon(ft.Icons.VERIFIED, size=16, color=INCOME_COLOR),
                        ft.Text("Statement verified", size=12, weight=ft.FontWeight.W_600, color=INCOME_COLOR),
                    ],
                    spacing=4,
                ),
                padding=ft.Padding.symmetric(horizontal=10, vertical=5),
                border_radius=20,
                bgcolor=ft.Colors.with_opacity(0.12, INCOME_COLOR),
            ),
        ]

        # Duplicates are flagged and left unchecked; the owner may include one deliberately.
        for row in preview.rows:
            row.include = not row.duplicate
        self._row_widgets = []
        self.rows_column.controls = [self._header_row()]
        for row in preview.rows:
            container, widgets = self._build_row(row)
            self.rows_column.controls.append(container)
            self._row_widgets.append(widgets)
        duplicates = sum(1 for row in preview.rows if row.duplicate)
        count_text = f"{len(preview.rows)} transaction(s) parsed"
        if duplicates:
            count_text += f"  •  {duplicates} flagged as duplicate(s)"
        self.rows_count.value = count_text
        self.select_all.value = bool(preview.rows) and all(row.include for row in preview.rows)
        self._refresh_account_dependent()
        self.ctx.page.update()

    def _summary_stats(self, statement: Statement) -> list[ft.Control]:
        def stat(label: str, cents: int, color: str | None = None) -> ft.Control:
            return ft.Column(
                [
                    ft.Text(label, size=11, color=ft.Colors.ON_SURFACE_VARIANT),
                    ft.Text(format_money(cents), size=16, weight=ft.FontWeight.W_600, color=color),
                ],
                spacing=2,
                tight=True,
            )

        return [
            stat("Beginning balance", statement.beginning_cents),
            stat("Deposits & credits", statement.deposits_cents, INCOME_COLOR),
            stat("Withdrawals", statement.withdrawals_cents, EXPENSE_COLOR),
            stat("Fees", statement.fees_cents, EXPENSE_COLOR),
            stat("Ending balance", statement.ending_cents),
        ]

    def _header_row(self) -> ft.Control:
        header_style = {"size": 11, "weight": ft.FontWeight.W_600, "color": ft.Colors.ON_SURFACE_VARIANT}
        return ft.Container(
            content=ft.Row(
                [
                    ft.Text("", width=INCLUDE_WIDTH, **header_style),
                    ft.Text("Date", width=DATE_WIDTH, **header_style),
                    ft.Text("Description", expand=True, **header_style),
                    ft.Text("Amount", width=AMOUNT_WIDTH, text_align=ft.TextAlign.RIGHT, **header_style),
                    ft.Text("Type", width=TYPE_WIDTH, **header_style),
                    ft.Text("Category", width=CATEGORY_WIDTH, **header_style),
                    ft.Text("Destination", width=DESTINATION_WIDTH, **header_style),
                    ft.Text("", width=DUPLICATE_WIDTH, **header_style),
                ],
                spacing=8,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
            padding=ft.Padding.symmetric(horizontal=8, vertical=4),
        )

    def _build_row(self, row: ImportRow) -> tuple[ft.Control, _RowWidgets]:
        tx = row.transaction
        include = ft.Checkbox(value=row.include, width=INCLUDE_WIDTH)
        amount = ft.Text("", width=AMOUNT_WIDTH, text_align=ft.TextAlign.RIGHT, weight=ft.FontWeight.W_600)
        type_dd = ft.Dropdown(
            width=TYPE_WIDTH,
            dense=True,
            text_size=12,
            value=tx.type.value,
            options=[
                ft.DropdownOption(key=type_.value, text=type_.label)
                for type_ in (
                    TransactionType.EXPENSE,
                    TransactionType.INCOME,
                    TransactionType.REFUND,
                    TransactionType.TRANSFER,
                )
            ],
        )
        category_dd = ft.Dropdown(width=CATEGORY_WIDTH, dense=True, text_size=12, hint_text="Category")
        destination_dd = ft.Dropdown(
            width=DESTINATION_WIDTH, dense=True, text_size=12, hint_text="Destination account", visible=False
        )
        widgets = _RowWidgets(
            row=row,
            include=include,
            amount=amount,
            type_dd=type_dd,
            category_dd=category_dd,
            destination_dd=destination_dd,
        )
        self._sync_category(widgets)
        self._sync_destination(widgets)
        self._sync_amount(widgets)
        include.on_change = lambda _: self._row_toggled(widgets)
        type_dd.on_select = lambda _: self._type_changed(widgets)
        category_dd.on_select = lambda _: self._category_changed(widgets)
        destination_dd.on_select = lambda _: self._destination_changed(widgets)

        duplicate_badge: ft.Control
        if row.duplicate:
            duplicate_badge = ft.Container(
                content=ft.Text("Duplicate", size=10, weight=ft.FontWeight.W_600, color=WARNING_COLOR),
                width=DUPLICATE_WIDTH,
                padding=ft.Padding.symmetric(horizontal=6, vertical=3),
                border_radius=10,
                alignment=ft.Alignment.CENTER,
                bgcolor=ft.Colors.with_opacity(0.14, WARNING_COLOR),
                tooltip="A matching transaction already exists. Included only if you check it.",
            )
        else:
            duplicate_badge = ft.Container(width=DUPLICATE_WIDTH)

        container = ft.Container(
            content=ft.Row(
                [
                    include,
                    ft.Text(f"{tx.occurred_on:%Y-%m-%d}", width=DATE_WIDTH, size=12),
                    ft.Text(
                        tx.description or "(no description)",
                        expand=True,
                        max_lines=1,
                        overflow=ft.TextOverflow.ELLIPSIS,
                        tooltip=tx.description or None,
                    ),
                    amount,
                    type_dd,
                    category_dd,
                    destination_dd,
                    duplicate_badge,
                ],
                spacing=8,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
            padding=ft.Padding.symmetric(horizontal=8, vertical=4),
            border_radius=8,
            bgcolor=ft.Colors.with_opacity(0.05, WARNING_COLOR) if row.duplicate else None,
        )
        return container, widgets

    # ---------- row helpers ----------
    def _categories_for(self, category_type: TransactionType) -> list[Category]:
        if category_type not in self._categories:
            self._categories[category_type] = self.ctx.repo.list_categories(category_type)
        return self._categories[category_type]

    def _category_options(self, category_type: TransactionType) -> list[ft.DropdownOption]:
        return [ft.DropdownOption(key=str(c.id), text=c.name) for c in self._categories_for(category_type)]

    def _all_category_options(self) -> list[ft.DropdownOption]:
        return [
            ft.DropdownOption(key=str(c.id), text=f"{c.name} ({c.type.label})")
            for c in self.ctx.repo.list_categories()
            if c.type in (TransactionType.INCOME, TransactionType.EXPENSE)
        ]

    def _default_category_id(self, category_type: TransactionType) -> int | None:
        categories = self._categories_for(category_type)
        wanted = "Other Income" if category_type == TransactionType.INCOME else "Other"
        for category in categories:
            if category.name == wanted:
                return category.id
        return categories[0].id if categories else None

    def _sync_category(self, widgets: _RowWidgets) -> None:
        tx = widgets.row.transaction
        category_type = tx.type.category_type
        widgets.category_dd.visible = category_type is not None
        if category_type is None:
            tx.category_id = None
            widgets.category_dd.value = None
            return
        options = self._category_options(category_type)
        widgets.category_dd.options = options
        keys = {option.key for option in options}
        current = str(tx.category_id or "")
        if current in keys:
            widgets.category_dd.value = current
        else:
            tx.category_id = self._default_category_id(category_type)
            widgets.category_dd.value = str(tx.category_id) if tx.category_id is not None else None

    def _sync_destination(self, widgets: _RowWidgets) -> None:
        tx = widgets.row.transaction
        is_transfer = tx.type is TransactionType.TRANSFER
        widgets.destination_dd.visible = is_transfer
        if not is_transfer:
            tx.transfer_account_id = None
            widgets.destination_dd.value = None
            return
        options = [
            ft.DropdownOption(key=str(account.id), text=account.name)
            for account in self._accounts
            if str(account.id) != self.account.value
        ]
        widgets.destination_dd.options = options
        current = str(tx.transfer_account_id or "")
        if current in {option.key for option in options}:
            widgets.destination_dd.value = current
        else:
            tx.transfer_account_id = None
            widgets.destination_dd.value = None

    def _sync_amount(self, widgets: _RowWidgets) -> None:
        tx = widgets.row.transaction
        if tx.type is TransactionType.TRANSFER:
            widgets.amount.value = format_money(tx.amount_cents)
            widgets.amount.color = ft.Colors.ON_SURFACE_VARIANT
        elif tx.type is TransactionType.EXPENSE:
            widgets.amount.value = format_money(-tx.amount_cents)
            widgets.amount.color = EXPENSE_COLOR
        else:
            widgets.amount.value = format_money(tx.amount_cents, signed=True)
            widgets.amount.color = INCOME_COLOR

    def _row_toggled(self, widgets: _RowWidgets) -> None:
        widgets.row.include = bool(widgets.include.value)
        self.select_all.value = bool(self._row_widgets) and all(w.include.value for w in self._row_widgets)
        self.ctx.page.update()

    def _select_all_changed(self, _: ft.Event[ft.Checkbox]) -> None:
        value = bool(self.select_all.value)
        for widgets in self._row_widgets:
            widgets.row.include = value
            widgets.include.value = value
        self.ctx.page.update()

    def _type_changed(self, widgets: _RowWidgets) -> None:
        widgets.row.transaction.type = TransactionType(widgets.type_dd.value)
        self._sync_category(widgets)
        self._sync_destination(widgets)
        self._sync_amount(widgets)
        self.ctx.page.update()

    def _category_changed(self, widgets: _RowWidgets) -> None:
        widgets.row.transaction.category_id = (
            int(widgets.category_dd.value) if widgets.category_dd.value else None
        )

    def _destination_changed(self, widgets: _RowWidgets) -> None:
        widgets.row.transaction.transfer_account_id = (
            int(widgets.destination_dd.value) if widgets.destination_dd.value else None
        )

    def _apply_category(self, _: ft.Event) -> None:
        if not self.apply_category.value:
            self.ctx.notify("Choose a category to apply", error=True)
            return
        category = self.ctx.repo.get_category(int(self.apply_category.value))
        if category is None:
            self.ctx.notify("That category no longer exists", error=True)
            return
        included = [widgets for widgets in self._row_widgets if widgets.row.include]
        if not included:
            self.ctx.notify("Select at least one row first", error=True)
            return
        applied = skipped = 0
        for widgets in included:
            if widgets.row.transaction.type.category_type != category.type:
                skipped += 1
                continue
            widgets.row.transaction.category_id = category.id
            widgets.category_dd.value = str(category.id)
            applied += 1
        self.ctx.page.update()
        message = f"Applied {category.name} to {applied} selected row(s)"
        if skipped:
            message += f"; skipped {skipped} that cannot use that category"
        self.ctx.notify(message)

    # ---------- account binding ----------
    def _account_changed(self, _: ft.Event[ft.Dropdown]) -> None:
        if self.preview is None or self._path is None:
            return
        if not self.account.value:
            self._refresh_account_dependent()
            self.ctx.page.update()
            return
        account_id = int(self.account.value)
        if account_id == self.preview.account_id:
            # Reuse the existing preview, but no row may transfer to its own source account.
            for widgets in self._row_widgets:
                self._sync_destination(widgets)
            self._refresh_account_dependent()
            self.ctx.page.update()
            return
        try:
            preview = self.ctx.service.prepare_import(self._path, account_id)
        except (StatementValidationError, UnsupportedStatementError, OSError, RepositoryError) as exc:
            # Keep the old preview on screen but not importable under the new selection.
            self.import_button.disabled = True
            self.opening_balance.visible = False
            self.remember.visible = False
            _notify_parse_error(self.ctx, exc)
            return
        self._install_preview(preview)

    def _refresh_account_dependent(self) -> None:
        if self.preview is None or not self.account.value:
            self.import_button.disabled = True
            self.opening_balance.visible = False
            self.remember.visible = False
            self.match_note.visible = False
            return
        statement = self.preview.statement
        account = self.ctx.repo.get_account(int(self.account.value))
        if account is None:
            self.import_button.disabled = True
            self.opening_balance.visible = False
            self.remember.visible = False
            return
        self.import_button.disabled = False

        matched = self.ctx.service.match_account(statement.account_last4)
        show_match = matched is not None and matched.id == account.id
        self.match_note.value = (
            f"Matched by account number ending {statement.account_last4}" if show_match else ""
        )
        self.match_note.visible = show_match

        offer_opening = not self.ctx.repo.has_transactions_before(account.id, statement.period_start)
        self.opening_balance.visible = offer_opening
        self.opening_balance.value = True
        self.opening_balance.label = (
            f"Set opening balance to {format_money(statement.beginning_cents)} "
            "so this account matches the statement"
        )

        remember = bool(statement.account_last4) and not account.number_last4
        self.remember.visible = remember
        self.remember.value = True
        self.remember.label = f"Remember this account as ending {statement.account_last4}"

    # ---------- commit ----------
    def _commit(self, _: ft.Event) -> None:
        if self.preview is None or not self.account.value:
            return
        included = [row for row in self.preview.rows if row.include]
        if not included:
            self.ctx.notify("Select at least one transaction to import", error=True)
            return
        set_opening = bool(self.opening_balance.visible and self.opening_balance.value)
        remember = bool(self.remember.visible and self.remember.value)
        last4 = self.preview.statement.account_last4
        try:
            self.ctx.service.commit_import(
                self.preview, self.preview.rows, set_opening_balance=set_opening
            )
            if remember and last4:
                self.ctx.repo.set_account_number(self.preview.account_id, last4)
        except RepositoryError as exc:
            self.ctx.notify(str(exc), error=True)
            return
        except OSError as exc:
            self.ctx.notify(f"Import failed: {exc.strerror or exc}", error=True)
            return
        count = len(included)
        self._close()
        self.ctx.data_changed()
        self.ctx.notify(f"Imported {count} transaction(s)")


class ImportedStatementsDialog:
    """Undo path: list imports and delete one, releasing the statement hash."""

    def __init__(self, ctx: AppContext) -> None:
        self.ctx = ctx
        self._account_names = {account.id: account.name for account in ctx.repo.list_accounts()}
        self.list_column = ft.Column(spacing=8)
        self.dialog = ft.AlertDialog(
            modal=True,
            title=ft.Text("Imported statements"),
            content=ft.Container(
                width=680,
                content=ft.Column(
                    [
                        ft.Text(
                            "Deleting an import removes its transactions and reconciliation, "
                            "and frees the PDF to be imported again.",
                            size=12,
                            color=ft.Colors.ON_SURFACE_VARIANT,
                        ),
                        self.list_column,
                    ],
                    spacing=12,
                    scroll=ft.ScrollMode.AUTO,
                ),
            ),
            actions=[ft.TextButton(content="Close", on_click=lambda _: self._close())],
            actions_alignment=ft.MainAxisAlignment.END,
        )

    def _close(self) -> None:
        """Close this dialog even when a SnackBar is stacked on top of it."""
        while self.dialog.open:
            if self.ctx.page.pop_dialog() is None:
                break

    def open(self) -> None:
        self._refresh()
        self.ctx.page.show_dialog(self.dialog)

    def _refresh(self) -> None:
        statements = self.ctx.repo.list_statements()
        if not statements:
            self.list_column.controls = [
                empty_state(
                    ft.Icons.RECEIPT_LONG,
                    "No imported statements",
                    "Statements you confirm on the Transactions page appear here so you can undo them.",
                )
            ]
            return
        self.list_column.controls = [self._statement_row(statement) for statement in statements]

    def _statement_row(self, statement: Statement) -> ft.Control:
        account = self._account_names.get(statement.account_id, "Unknown account")
        return ft.Container(
            content=ft.Row(
                [
                    ft.Icon(ft.Icons.DESCRIPTION_OUTLINED, size=20, color=ft.Colors.ON_SURFACE_VARIANT),
                    ft.Column(
                        [
                            ft.Text(account, weight=ft.FontWeight.W_500),
                            ft.Text(
                                f"{_period(statement)}  •  ending {format_money(statement.ending_cents)}",
                                size=12,
                                color=ft.Colors.ON_SURFACE_VARIANT,
                            ),
                        ],
                        spacing=2,
                        expand=True,
                    ),
                    ft.TextButton(
                        content="Delete import",
                        icon=ft.Icons.DELETE_OUTLINE,
                        style=ft.ButtonStyle(color=EXPENSE_COLOR),
                        on_click=lambda _, statement=statement: self._confirm_delete(statement),
                    ),
                ],
                spacing=12,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
            padding=10,
            border_radius=10,
            bgcolor=ft.Colors.SURFACE_CONTAINER_LOW,
        )

    def _confirm_delete(self, statement: Statement) -> None:
        account = self._account_names.get(statement.account_id, "this account")
        confirm_dialog(
            self.ctx.page,
            "Delete this import?",
            f"Remove the {account} statement for {_period(statement)}? Its imported transactions "
            "and reconciliation will be deleted, and the PDF can be imported again.",
            lambda: self._delete(statement),
            confirm_text="Delete import",
        )

    def _delete(self, statement: Statement) -> None:
        try:
            self.ctx.repo.delete_statement(statement.id)
        except RepositoryError as exc:
            self.ctx.notify(str(exc), error=True)
            return
        self._refresh()
        self.ctx.data_changed()
        self.ctx.notify("Import deleted; the statement can be imported again")


def open_imported_statements(ctx: AppContext) -> None:
    ImportedStatementsDialog(ctx).open()
