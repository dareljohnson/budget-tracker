"""Statement import: review a parsed PDF, confirm it, and undo an import later."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path

import flet as ft

from ...formatting import format_money
from ...models import Account, Category, Reconciliation, Statement, TransactionType
from ...repository import RepositoryError
from ...statements.model import (
    ImportPreview,
    ImportRow,
    StatementReadError,
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
RULE_WIDTH = 40
DUPLICATE_WIDTH = 76


_UNSUPPORTED_MESSAGES = {
    # generic._refuse_out_of_scope raises "Credit card statements are not supported".
    "credit card": "Credit card statements aren't supported. Nothing was imported.",
    # generic._refuse_out_of_scope raises "Only US dollar statements are supported".
    "us dollar": "Only US dollar statements are supported. Nothing was imported.",
    # statements.__init__._load_image raises "Image statements require the local OCR engine".
    "ocr": "Could not read this image because local OCR isn't available. Nothing was imported.",
}
_LAYOUT_MESSAGE = (
    "This statement doesn't match a supported checking or savings layout. Nothing was imported."
)


def _notify_parse_error(ctx: AppContext, exc: Exception) -> None:
    """Turn a parse failure into a clear message; nothing has been written at this point."""
    if isinstance(exc, StatementValidationError):
        details = "\n".join(f"• {error}" for error in exc.errors)
        ctx.notify(f"Statement did not validate. Nothing was imported.\n{details}", error=True)
    elif isinstance(exc, UnsupportedStatementError):
        detail = str(exc).lower()
        message = next(
            (text for marker, text in _UNSUPPORTED_MESSAGES.items() if marker in detail),
            _LAYOUT_MESSAGE,
        )
        ctx.notify(message, error=True)
    elif isinstance(exc, StatementReadError):
        ctx.notify("Could not read this file. Nothing was imported.", error=True)
    elif isinstance(exc, OSError):
        ctx.notify(f"Could not read this file: {exc.strerror or exc}. Nothing was imported.", error=True)
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
    save_rule: ft.IconButton


class SaveCategoryRuleDialog:
    """Remember one row's description -> category mapping for future imports."""

    def __init__(self, ctx: AppContext, match_text: str, category: Category) -> None:
        self.ctx = ctx
        self.category = category
        self.match_text = ft.TextField(
            label="Match text",
            value=match_text,
            hint_text="Text to look for in imported descriptions",
            autofocus=True,
            on_submit=self._save,
        )
        self.dialog = ft.AlertDialog(
            modal=True,
            title=ft.Text("Save category rule"),
            content=ft.Container(
                width=420,
                content=ft.Column(
                    [
                        ft.Text(
                            "Future imports whose description contains this text use the category below.",
                            size=12,
                            color=ft.Colors.ON_SURFACE_VARIANT,
                        ),
                        self.match_text,
                        ft.Row(
                            [
                                ft.Text("Category", size=12, color=ft.Colors.ON_SURFACE_VARIANT),
                                ft.Text(category.name, weight=ft.FontWeight.W_500),
                            ],
                            spacing=8,
                        ),
                    ],
                    spacing=12,
                    tight=True,
                ),
            ),
            actions=[
                ft.TextButton(content="Cancel", on_click=lambda _: ctx.page.pop_dialog()),
                ft.FilledButton(content="Save rule", icon=ft.Icons.CHECK, on_click=self._save),
            ],
            actions_alignment=ft.MainAxisAlignment.END,
        )

    def open(self) -> None:
        self.ctx.page.show_dialog(self.dialog)

    def _save(self, _: ft.Event) -> None:
        text = (self.match_text.value or "").strip()
        if not text:
            self.match_text.error = "Rule text is required"
            self.dialog.update()
            return
        try:
            self.ctx.repo.add_category_rule(text, self.category.id)
        except RepositoryError as exc:
            self.ctx.notify(str(exc), error=True)
            return
        self.ctx.page.pop_dialog()
        self.ctx.notify("Rule saved")


class StatementImportDialog:
    """Review dialog for one statement. Nothing is written until Import is pressed."""

    def __init__(self, ctx: AppContext, *, on_close: Callable[[], None] | None = None) -> None:
        self.ctx = ctx
        self.on_close = on_close
        self._closed_notified = False
        self._edited = False
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
        self.mismatch_note = ft.Text("", size=12, color=EXPENSE_COLOR, weight=ft.FontWeight.W_600, visible=False)
        self.ocr_badge = ft.Text("Read from image (OCR)", size=12, weight=ft.FontWeight.W_600, visible=False)
        self.statement_meta = ft.Text("", size=12, color=ft.Colors.ON_SURFACE_VARIANT)
        self.projection_statement = ft.Text("", weight=ft.FontWeight.W_600)
        self.projection_balance_label = ft.Text("", size=12, color=ft.Colors.ON_SURFACE_VARIANT)
        self.projection_balance = ft.Text("", weight=ft.FontWeight.W_600)
        self.projection_difference = ft.Text("", weight=ft.FontWeight.W_600)
        self.projection_note = ft.Text("", size=11, color=ft.Colors.ON_SURFACE_VARIANT, visible=False)
        self.projection_panel = ft.Container(
            content=ft.Column(
                [
                    ft.Text("Reconciliation preview", size=12, weight=ft.FontWeight.W_600),
                    ft.Row(
                        [ft.Text("Statement ending", size=12, color=ft.Colors.ON_SURFACE_VARIANT, expand=True),
                         self.projection_statement],
                        spacing=8,
                    ),
                    ft.Row([self.projection_balance_label, self.projection_balance], spacing=8),
                    ft.Row(
                        [ft.Text("Projected difference", size=12, color=ft.Colors.ON_SURFACE_VARIANT, expand=True),
                         self.projection_difference],
                        spacing=8,
                    ),
                    self.projection_note,
                ],
                spacing=4,
                tight=True,
            ),
            padding=ft.Padding.symmetric(horizontal=12, vertical=10),
            border_radius=10,
            bgcolor=ft.Colors.with_opacity(0.06, INCOME_COLOR),
            visible=False,
        )
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
        self.opening_balance = ft.Checkbox(value=True, visible=False, on_change=lambda _: self._refresh_projection())
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
                        ft.Row(
                            [self.account, self.match_note, self.mismatch_note],
                            spacing=16,
                            wrap=True,
                            vertical_alignment=ft.CrossAxisAlignment.CENTER,
                        ),
                        self.statement_meta,
                        self.projection_panel,
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
                            "Statements are parsed locally. Nothing is written until you choose Import.",
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
        self._finish()

    def _finish(self) -> None:
        """Release the opener exactly once, whether the dialog was shown, cancelled, or failed."""
        if self._closed_notified:
            return
        self._closed_notified = True
        if self.on_close is not None:
            self.on_close()

    # ---------- opening ----------
    async def open(self) -> None:
        if not self._accounts:
            self.ctx.notify("Create an account first, then import a statement", error=True)
            self._finish()
            return
        try:
            files = await self._file_picker.pick_files(
                dialog_title="Import statement",
                file_type=ft.FilePickerFileType.CUSTOM,
                allowed_extensions=["pdf", "png", "jpg", "jpeg"],
            )
        except Exception as exc:  # noqa: BLE001 - native picker can be unavailable
            self.ctx.notify(f"Could not open the file picker: {exc}", error=True)
            self._finish()
            return
        chosen = next((f for f in files or [] if f.path), None) if files else None
        if chosen is None:
            self._finish()
            return
        self._path = Path(chosen.path)

        # Parsing needs an account for duplicate detection. Parsing once with a provisional
        # account reveals the statement's account digits, which then select the real account.
        try:
            preview = await asyncio.to_thread(
                self.ctx.service.prepare_import, self._path, self._accounts[0].id
            )
        except (StatementValidationError, UnsupportedStatementError, StatementReadError, OSError,
                RepositoryError) as exc:
            _notify_parse_error(self.ctx, exc)
            self._finish()
            return

        matched = self.ctx.service.match_account(preview.statement.account_last4)
        if matched is not None:
            self.account.value = str(matched.id)
            if matched.id != preview.account_id:
                try:
                    preview = await asyncio.to_thread(
                        self.ctx.service.prepare_import, self._path, matched.id
                    )
                except (StatementValidationError, UnsupportedStatementError, StatementReadError, OSError,
                        RepositoryError) as exc:
                    _notify_parse_error(self.ctx, exc)
                    self._finish()
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
            ft.Row(
                [
                    ft.Container(
                        content=ft.Row(
                            [
                                ft.Icon(ft.Icons.VERIFIED, size=16, color=INCOME_COLOR),
                                ft.Text(
                                    "Statement verified",
                                    size=12,
                                    weight=ft.FontWeight.W_600,
                                    color=INCOME_COLOR,
                                ),
                            ],
                            spacing=4,
                        ),
                        padding=ft.Padding.symmetric(horizontal=10, vertical=5),
                        border_radius=20,
                        bgcolor=ft.Colors.with_opacity(0.12, INCOME_COLOR),
                    ),
                ],
                spacing=8,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
            self.ocr_badge,
        ]
        self.ocr_badge.visible = preview.source == "ocr"

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
        self._edited = False
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
                    ft.Text("", width=RULE_WIDTH, **header_style),
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
                for type_ in self._allowed_types(row)
            ],
        )
        category_dd = ft.Dropdown(width=CATEGORY_WIDTH, dense=True, text_size=12, hint_text="Category")
        destination_dd = ft.Dropdown(
            width=DESTINATION_WIDTH, dense=True, text_size=12, hint_text="Destination account", visible=False
        )
        save_rule = ft.IconButton(
            icon=ft.Icons.RULE,
            tooltip="Save a category rule for this description",
            icon_size=18,
            width=RULE_WIDTH,
            disabled=True,
        )
        widgets = _RowWidgets(
            row=row,
            include=include,
            amount=amount,
            type_dd=type_dd,
            category_dd=category_dd,
            destination_dd=destination_dd,
            save_rule=save_rule,
        )
        self._sync_category(widgets)
        self._sync_destination(widgets)
        self._sync_amount(widgets)
        include.on_change = lambda _: self._row_toggled(widgets)
        type_dd.on_select = lambda _: self._type_changed(widgets)
        category_dd.on_select = lambda _: self._category_changed(widgets)
        destination_dd.on_select = lambda _: self._destination_changed(widgets)
        save_rule.on_click = lambda _: self._save_rule(widgets)

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
                    save_rule,
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
    @staticmethod
    def _income_origin(row: ImportRow) -> bool:
        """Whether the statement printed this row in the deposits/credits section."""
        if row.direction is not None:
            return row.direction == "income"
        return row.transaction.type is TransactionType.INCOME

    def _allowed_types(self, row: ImportRow) -> tuple[TransactionType, ...]:
        """Conversions the spec allows, based on the row's parsed direction."""
        if self._income_origin(row):
            return (TransactionType.INCOME, TransactionType.REFUND, TransactionType.TRANSFER)
        return (TransactionType.EXPENSE, TransactionType.TRANSFER)

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
            widgets.save_rule.disabled = True
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
        widgets.save_rule.disabled = tx.category_id is None

    def _sync_destination(self, widgets: _RowWidgets) -> None:
        tx = widgets.row.transaction
        is_transfer = tx.type is TransactionType.TRANSFER
        widgets.destination_dd.visible = is_transfer
        if not is_transfer:
            tx.transfer_account_id = None
            widgets.destination_dd.value = None
            return
        # An income-origin transfer moves money from the counterparty into the statement account,
        # so the picker chooses the source; an expense-origin transfer chooses the destination.
        widgets.destination_dd.label = (
            "From account" if self._income_origin(widgets.row) else "Destination account"
        )
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
        self._edited = True
        self.select_all.value = bool(self._row_widgets) and all(w.include.value for w in self._row_widgets)
        self._refresh_projection()
        self.ctx.page.update()

    def _select_all_changed(self, _: ft.Event[ft.Checkbox]) -> None:
        value = bool(self.select_all.value)
        for widgets in self._row_widgets:
            widgets.row.include = value
            widgets.include.value = value
        self._edited = True
        self._refresh_projection()
        self.ctx.page.update()

    def _type_changed(self, widgets: _RowWidgets) -> None:
        chosen = TransactionType(widgets.type_dd.value)
        if chosen not in self._allowed_types(widgets.row):
            # Defensive: only offer expense->transfer, income->transfer, income->refund.
            widgets.type_dd.value = widgets.row.transaction.type.value
            return
        widgets.row.transaction.type = chosen
        self._edited = True
        self._sync_category(widgets)
        self._sync_destination(widgets)
        self._sync_amount(widgets)
        self._refresh_projection()
        self.ctx.page.update()

    def _category_changed(self, widgets: _RowWidgets) -> None:
        widgets.row.transaction.category_id = (
            int(widgets.category_dd.value) if widgets.category_dd.value else None
        )
        widgets.save_rule.disabled = widgets.row.transaction.category_id is None
        self._edited = True
        self._refresh_projection()
        self.ctx.page.update()

    def _save_rule(self, widgets: _RowWidgets) -> None:
        tx = widgets.row.transaction
        if tx.category_id is None:
            return
        category = self.ctx.repo.get_category(tx.category_id)
        if category is None:
            self.ctx.notify("That category no longer exists", error=True)
            return
        SaveCategoryRuleDialog(self.ctx, tx.description or "", category).open()

    def _destination_changed(self, widgets: _RowWidgets) -> None:
        widgets.row.transaction.transfer_account_id = (
            int(widgets.destination_dd.value) if widgets.destination_dd.value else None
        )
        self._edited = True
        self._refresh_projection()

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
            widgets.save_rule.disabled = False
            applied += 1
        self._edited = True
        self._refresh_projection()
        self.ctx.page.update()
        message = f"Applied {category.name} to {applied} selected row(s)"
        if skipped:
            message += f"; skipped {skipped} that cannot use that category"
        self.ctx.notify(message)

    # ---------- account binding ----------
    async def _account_changed(self, _: ft.Event[ft.Dropdown]) -> None:
        if self._path is None:
            return
        if not self.account.value:
            self._clear_preview_state("Choose an account to review this statement.")
            self.ctx.page.update()
            return
        account_id = int(self.account.value)
        if self.preview is not None and account_id == self.preview.account_id:
            # Reuse the existing preview, but no row may transfer to its own source account.
            for widgets in self._row_widgets:
                self._sync_destination(widgets)
            self._refresh_account_dependent()
            self.ctx.page.update()
            return
        if self.preview is not None and self._edited:
            self._confirm_account_switch(account_id)
            return
        await self._load_for_account(account_id)

    def _confirm_account_switch(self, account_id: int) -> None:
        """Switching accounts re-parses and discards row edits, so ask before doing it."""
        previous = str(self.preview.account_id) if self.preview is not None else None

        def cancel(_: ft.Event) -> None:
            self.ctx.page.pop_dialog()
            self.account.value = previous
            self.ctx.page.update()

        async def proceed(_: ft.Event) -> None:
            self.ctx.page.pop_dialog()
            await self._load_for_account(account_id)

        self.ctx.page.show_dialog(
            ft.AlertDialog(
                modal=True,
                icon=ft.Icon(ft.Icons.WARNING_AMBER_ROUNDED, color=WARNING_COLOR),
                title=ft.Text("Discard row changes?"),
                content=ft.Text(
                    "Changing the account re-reads the statement and discards the type, category, "
                    "destination and include choices you made in the preview."
                ),
                actions=[
                    ft.TextButton(content="Keep editing", on_click=cancel),
                    ft.FilledButton(content="Discard and switch", on_click=proceed),
                ],
                actions_alignment=ft.MainAxisAlignment.END,
            )
        )

    async def _load_for_account(self, account_id: int) -> None:
        try:
            preview = await asyncio.to_thread(
                self.ctx.service.prepare_import, self._path, account_id
            )
        except (StatementValidationError, UnsupportedStatementError, StatementReadError, OSError,
                RepositoryError) as exc:
            # Never leave another account's preview visible as if it belonged to this selection.
            self._clear_preview_state(
                "This statement could not be reviewed for that account. Choose another account."
            )
            _notify_parse_error(self.ctx, exc)
            self.ctx.page.update()
            return
        self._install_preview(preview)

    def _clear_preview_state(self, message: str) -> None:
        """Drop the current preview so another account's rows can never be committed."""
        self.preview = None
        self._edited = False
        self._row_widgets = []
        self.rows_column.controls = []
        self.rows_count.value = ""
        self.summary_holder.controls = []
        self.ocr_badge.visible = False
        self.statement_meta.value = message
        self.select_all.value = False
        self.match_note.value = ""
        self.match_note.visible = False
        self.mismatch_note.value = ""
        self.mismatch_note.visible = False
        self.opening_balance.visible = False
        self.remember.visible = False
        self.projection_panel.visible = False
        self.import_button.disabled = True

    def _refresh_account_dependent(self) -> None:
        self.projection_panel.visible = False
        if self.preview is None or not self.account.value:
            self.import_button.disabled = True
            self.opening_balance.visible = False
            self.remember.visible = False
            self.match_note.visible = False
            self.mismatch_note.visible = False
            return
        statement = self.preview.statement
        account = self.ctx.repo.get_account(int(self.account.value))
        if account is None:
            self.import_button.disabled = True
            self.opening_balance.visible = False
            self.remember.visible = False
            self.match_note.visible = False
            self.mismatch_note.visible = False
            return

        statement_last4 = statement.account_last4
        account_last4 = account.number_last4
        mismatched = bool(statement_last4 and account_last4) and not (
            statement_last4.endswith(account_last4) or account_last4.endswith(statement_last4)
        )
        self.mismatch_note.value = (
            f"Statement account ending {statement_last4} does not match {account.name} "
            f"ending {account_last4}"
            if mismatched
            else ""
        )
        self.mismatch_note.visible = mismatched
        if mismatched:
            # Wrong-account imports are an anti-goal: block until the right account is selected.
            self.import_button.disabled = True
            self.opening_balance.visible = False
            self.remember.visible = False
            self.match_note.visible = False
            return

        self.import_button.disabled = False
        matched = self.ctx.service.match_account(statement_last4)
        show_match = matched is not None and matched.id == account.id
        self.match_note.value = f"Matched by account number ending {statement_last4}" if show_match else ""
        self.match_note.visible = show_match

        offer_opening = not self.ctx.repo.has_transactions_before(account.id, statement.period_start)
        self.opening_balance.visible = offer_opening
        self.opening_balance.value = True
        self.opening_balance.label = (
            f"Set opening balance to {format_money(statement.beginning_cents)} "
            "so this account matches the statement"
        )

        remember = bool(statement_last4) and not account_last4
        self.remember.visible = remember
        self.remember.value = True
        self.remember.label = f"Remember this account as ending {statement_last4}"
        self._refresh_projection()

    def _included_effect(self, account_id: int) -> int:
        """Net change the included preview rows would make to ``account_id``'s balance."""
        effect = 0
        for row in self.preview.rows if self.preview is not None else []:
            if not row.include:
                continue
            tx = self._oriented(row, account_id).transaction
            if tx.type in (TransactionType.INCOME, TransactionType.REFUND):
                if tx.account_id == account_id:
                    effect += tx.amount_cents
            elif tx.type is TransactionType.EXPENSE:
                if tx.account_id == account_id:
                    effect -= tx.amount_cents
            elif tx.type is TransactionType.TRANSFER:
                if tx.account_id == account_id:
                    effect -= tx.amount_cents
                elif tx.transfer_account_id == account_id:
                    effect += tx.amount_cents
        return effect

    def _refresh_projection(self) -> None:
        """Show how the included rows would reconcile the account with the statement (spec §7.5/§8.1)."""
        self.projection_panel.visible = False
        if self.preview is None or not self.account.value:
            return
        statement = self.preview.statement
        account_id = int(self.account.value)
        account = self.ctx.repo.get_account(account_id)
        if account is None:
            return
        try:
            current_cents = self.ctx.repo.account_balance_as_of(account_id, statement.period_end)
        except RepositoryError:
            return
        effect_cents = self._included_effect(account_id)
        set_opening = bool(self.opening_balance.visible and self.opening_balance.value)
        if set_opening:
            # Replacing the opening balance changes only the starting point, not existing activity.
            projected_cents = (
                statement.beginning_cents
                + (current_cents - account.opening_balance_cents)
                + effect_cents
            )
        else:
            projected_cents = current_cents + effect_cents
        difference_cents = projected_cents - statement.ending_cents
        self.projection_statement.value = format_money(statement.ending_cents)
        self.projection_balance_label.value = f"Account balance at {statement.period_end:%Y-%m-%d}"
        self.projection_balance.value = format_money(current_cents)
        self.projection_difference.value = format_money(difference_cents, signed=True)
        self.projection_difference.color = INCOME_COLOR if difference_cents == 0 else EXPENSE_COLOR
        if difference_cents == 0:
            self.projection_note.value = "After importing, this account matches the statement's ending balance."
        else:
            self.projection_note.value = (
                "The statement and the included rows disagree by this amount. Importing keeps existing "
                "transactions; use Check balance afterwards to review or correct the difference."
            )
        self.projection_note.visible = True
        self.projection_panel.visible = True

    # ---------- commit ----------
    def _commit(self, _: ft.Event) -> None:
        if self.preview is None or not self.account.value:
            return
        if int(self.account.value) != self.preview.account_id:
            # Defensive: never commit a preview built for a different account than the dropdown shows.
            self.ctx.notify("Choose the account again so the preview can refresh", error=True)
            return
        included = [row for row in self.preview.rows if row.include]
        if not included:
            self.ctx.notify("Select at least one transaction to import", error=True)
            return
        for row in included:
            if row.transaction.type is TransactionType.TRANSFER and row.transaction.transfer_account_id is None:
                self.ctx.notify(
                    "Choose the From account for every imported deposit transfer"
                    if self._income_origin(row)
                    else "Choose the destination account for every imported transfer",
                    error=True,
                )
                return
        set_opening = bool(self.opening_balance.visible and self.opening_balance.value)
        remember = bool(self.remember.visible and self.remember.value)
        last4 = self.preview.statement.account_last4
        account_id = self.preview.account_id
        commit_rows = [self._oriented(row, account_id) for row in included]
        try:
            statement = self.ctx.service.commit_import(
                self.preview, commit_rows, set_opening_balance=set_opening
            )
        except RepositoryError as exc:
            self.ctx.notify(str(exc), error=True)
            return
        except OSError as exc:
            self.ctx.notify(f"Import failed: {exc.strerror or exc}", error=True)
            return

        # The import is committed; a failure to remember the digits must not mask that success.
        number_error: Exception | None = None
        if remember and last4:
            try:
                self.ctx.repo.set_account_number(account_id, last4)
            except Exception as exc:  # noqa: BLE001 - the import already succeeded
                number_error = exc

        count = len(included)
        # Use this import's own reconciliation; last_reconciliation is ordered by as_of and may
        # belong to a newer manual balance check.
        reconciliation = (
            self.ctx.repo.reconciliation_for_statement(statement.id)
            if statement.id is not None
            else None
        )
        account = self.ctx.repo.get_account(account_id)
        self._close()
        self.ctx.data_changed()
        if number_error is not None:
            self.ctx.notify(
                f"Imported {count} transaction(s), but the account ending digits could not be saved: "
                f"{number_error}",
                error=True,
            )
        if account is not None:
            ImportResultDialog(self.ctx, account, reconciliation, count).open()

    def _oriented(self, row: ImportRow, statement_account_id: int) -> ImportRow:
        """Orient a transfer so the ledger's debit/credit convention matches the parsed direction.

        The ledger debits ``account_id`` and credits ``transfer_account_id``. An expense-origin
        transfer moves money out of the statement account, while an income-origin (deposit) transfer
        moves money into it, so the statement account must be the credited side.
        """
        tx = row.transaction
        if tx.type is not TransactionType.TRANSFER:
            return row
        if self._income_origin(row):
            counterparty = tx.transfer_account_id
            if counterparty is None:
                return row  # commit_import reports the missing account
            return replace(row, transaction=replace(
                tx, account_id=counterparty, transfer_account_id=statement_account_id
            ))
        return replace(row, transaction=replace(
            tx, account_id=statement_account_id, transfer_account_id=tx.transfer_account_id
        ))


class ImportResultDialog:
    """Post-import reconciliation summary, with a path into the balance-check dialog."""

    def __init__(
        self,
        ctx: AppContext,
        account: Account,
        reconciliation: Reconciliation | None,
        count: int,
    ) -> None:
        self.ctx = ctx
        self.account = account
        self.reconciliation = reconciliation
        self.is_liability = account.kind.is_liability

        controls: list[ft.Control] = [
            ft.Text(f"Imported {count} transaction(s) into {account.name}.", size=13),
        ]
        if reconciliation is None:
            controls.append(
                ft.Text("No reconciliation was recorded for this import.", size=12,
                        color=ft.Colors.ON_SURFACE_VARIANT)
            )
        else:
            resolved = reconciliation.difference_cents == 0
            controls += [
                ft.Row(
                    [ft.Text("Statement ending", size=12, color=ft.Colors.ON_SURFACE_VARIANT, expand=True),
                     ft.Text(format_money(self._display(reconciliation.stated_cents)), weight=ft.FontWeight.W_600)],
                    spacing=8,
                ),
                ft.Row(
                    [ft.Text("Computed balance", size=12, color=ft.Colors.ON_SURFACE_VARIANT, expand=True),
                     ft.Text(format_money(self._display(reconciliation.computed_cents)), weight=ft.FontWeight.W_600)],
                    spacing=8,
                ),
                ft.Row(
                    [ft.Text("Difference", size=12, color=ft.Colors.ON_SURFACE_VARIANT, expand=True),
                     ft.Text(
                         format_money(self._display(reconciliation.difference_cents)),
                         weight=ft.FontWeight.W_600,
                         color=INCOME_COLOR if resolved else EXPENSE_COLOR,
                     )],
                    spacing=8,
                ),
                ft.Text(
                    "The imported transactions match the statement's ending balance."
                    if resolved
                    else (
                        f"The ledger differs from the statement by "
                        f"{format_money(self._display(reconciliation.difference_cents))}. "
                        "Use Check balance to review it and record a correction if needed."
                    ),
                    size=12,
                    color=INCOME_COLOR if resolved else EXPENSE_COLOR,
                    weight=ft.FontWeight.W_500,
                ),
            ]

        self.dialog = ft.AlertDialog(
            modal=True,
            title=ft.Text("Import complete"),
            content=ft.Container(
                width=460,
                content=ft.Column(controls, spacing=10, tight=True),
            ),
            actions=[
                ft.TextButton(content="Done", on_click=lambda _: ctx.page.pop_dialog()),
                ft.FilledButton(
                    content="Check balance",
                    icon=ft.Icons.FACT_CHECK_OUTLINED,
                    on_click=self._check_balance,
                ),
            ],
            actions_alignment=ft.MainAxisAlignment.END,
        )

    def _display(self, cents: int) -> int:
        return -cents if self.is_liability else cents

    def _check_balance(self, _: ft.Event) -> None:
        from .reconcile import BalanceCheckDialog

        self.ctx.page.pop_dialog()
        BalanceCheckDialog(self.ctx, self.account).open()

    def open(self) -> None:
        self.ctx.page.show_dialog(self.dialog)


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
                            "and frees the file to be imported again.",
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
            "and reconciliation will be deleted, and the file can be imported again. If this import "
            "set the account's opening balance, that opening balance is not reverted.",
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
