"""Debts: track what is owed, link credit cards and plan the payoff."""

from __future__ import annotations

from datetime import date

import flet as ft

from ...formatting import (
    format_money,
    format_percent,
    month_label,
    parse_date,
    parse_money,
    parse_percent_bps,
    parse_signed_money,
)
from ...models import AccountKind, Debt, DebtKind, TransactionType
from ...repository import RepositoryError
from ...services import DebtSummary
from ..components import card, confirm_dialog, date_field, empty_state, page_header
from ..context import AppContext
from ..theme import EXPENSE_COLOR, GAP, SEED_COLOR


class DebtsView:
    def __init__(self, ctx: AppContext) -> None:
        self.ctx = ctx
        # Plain alignment, not CrossAxisAlignment.STRETCH: on Flet 1.1.2 a ResponsiveRow using
        # STRETCH without an explicit height collapses to zero height in this view's scrollable
        # body, so the cards (and the empty state) never appear. Cards size to their content.
        self.grid = ft.ResponsiveRow(spacing=GAP, run_spacing=GAP)
        self.plan_panel = ft.Column(spacing=GAP)
        self.control = ft.Column(
            [
                page_header(
                    "Debts",
                    "Pay down what you owe",
                    [ft.FilledButton(content="Add debt", icon=ft.Icons.ADD,
                                     on_click=lambda _: DebtEditor(ctx, None).open())],
                ),
                self.plan_panel,
                self.grid,
            ],
            spacing=GAP,
            scroll=ft.ScrollMode.AUTO,
            expand=True,
        )
        ctx.on_data_changed(self.refresh)
        self.refresh()

    def refresh(self) -> None:
        summaries = self.ctx.service.list_debt_summaries()
        self.grid.controls = [self._debt_card(s) for s in summaries] or [
            card(
                empty_state(
                    ft.Icons.CREDIT_SCORE_OUTLINED,
                    "No debts tracked",
                    "Add a debt to see how fast you can pay it down.",
                    ft.FilledButton(content="Add debt", icon=ft.Icons.ADD,
                                    on_click=lambda _: DebtEditor(self.ctx, None).open()),
                ),
                col=12,
            )
        ]

    def _debt_card(self, summary: DebtSummary, payoff_month: int | None = None) -> ft.Container:
        debt = summary.debt
        details = [
            ft.Text(f"{format_money(summary.balance_cents)} owed", size=20, weight=ft.FontWeight.W_700),
            ft.Text(f"APR {format_percent(debt.apr_bps)}", size=12, color=ft.Colors.ON_SURFACE_VARIANT),
            ft.Text(
                f"Minimum {format_money(summary.effective_minimum_cents)}"
                + (f" · {month_label(summary.statement_month)} statement"
                   if summary.minimum_from_statement else " · set manually"),
                size=12,
                color=ft.Colors.ON_SURFACE_VARIANT,
            ),
        ]
        if summary.account_name:
            details.append(ft.Text(f"Linked to {summary.account_name}", size=12, color=SEED_COLOR))
        if debt.original_balance_cents:
            paid = min(1.0, max(0.0, 1 - summary.balance_cents / debt.original_balance_cents))
            details.append(ft.ProgressBar(value=paid, bar_height=8, border_radius=4))
            details.append(ft.Text(
                f"{round(paid * 100)}% paid down from {format_money(debt.original_balance_cents)}",
                size=11, color=ft.Colors.ON_SURFACE_VARIANT,
            ))
        if summary.minimum_below_interest:
            details.append(ft.Text("The minimum does not cover monthly interest", size=12, color=EXPENSE_COLOR))
        actions = [
            ft.FilledTonalButton(content="Log payment", icon=ft.Icons.PAYMENT,
                                 on_click=lambda _: DebtPaymentDialog(self.ctx, summary).open()),
            ft.OutlinedButton(content="History", icon=ft.Icons.HISTORY,
                              on_click=lambda _: DebtHistoryDialog(self.ctx, debt).open()),
            ft.OutlinedButton(content="Edit", icon=ft.Icons.EDIT_OUTLINED,
                              on_click=lambda _: DebtEditor(self.ctx, debt).open()),
            ft.IconButton(ft.Icons.DELETE_OUTLINE, tooltip="Delete", icon_color=EXPENSE_COLOR,
                          on_click=lambda _: self._confirm_delete(debt)),
        ]
        return card(
            ft.Column([ft.Text(debt.name, size=16, weight=ft.FontWeight.W_600), *details,
                       ft.Row(actions, alignment=ft.MainAxisAlignment.END, spacing=4, wrap=True)],
                      spacing=8),
            col={"xs": 12, "md": 6, "xl": 4},
        )

    def _confirm_delete(self, debt: Debt) -> None:
        def delete() -> None:
            try:
                self.ctx.service.delete_debt(debt.id)
            except RepositoryError as exc:
                self.ctx.notify(str(exc), error=True)
                return
            self.ctx.notify(f"Debt '{debt.name}' deleted")
            self.ctx.data_changed()

        confirm_dialog(
            self.ctx.page,
            "Delete debt?",
            f"'{debt.name}' will be removed. Its payments stay in the ledger.",
            delete,
        )


class DebtEditor:
    def __init__(self, ctx: AppContext, debt: Debt | None) -> None:
        self.ctx = ctx
        self.debt = debt
        linked = {
            d.account_id
            for d in ctx.repo.list_debts()
            if d.account_id is not None and (debt is None or d.id != debt.id)
        }
        cards = [
            a for a in ctx.repo.list_accounts()
            if a.kind is AccountKind.CREDIT_CARD and a.id not in linked
        ]
        self.name = ft.TextField(label="Name", value=debt.name if debt else "", autofocus=True)
        self.kind = ft.Dropdown(
            label="Kind",
            options=[ft.DropdownOption(key=k.value, text=k.label) for k in DebtKind],
            value=(debt.kind.value if debt else DebtKind.CREDIT_CARD.value),
        )
        self.balance = ft.TextField(label="Balance owed", prefix_icon=ft.Icons.ATTACH_MONEY,
                                    value=f"{debt.balance_cents / 100:.2f}" if debt else "")
        self.apr = ft.TextField(label="APR (%)", value=format_percent(debt.apr_bps).rstrip("%") if debt else "")
        self.minimum = ft.TextField(label="Minimum / month", prefix_icon=ft.Icons.ATTACH_MONEY,
                                    value=f"{debt.minimum_cents / 100:.2f}" if debt and debt.minimum_cents else "")
        self.original = ft.TextField(label="Original balance", hint_text="Optional",
                                     prefix_icon=ft.Icons.ATTACH_MONEY,
                                     value=(f"{debt.original_balance_cents / 100:.2f}"
                                            if debt and debt.original_balance_cents is not None else ""))
        self.link = ft.Dropdown(
            label="Link to credit-card account",
            options=[ft.DropdownOption(key="none", text="None")]
            + [ft.DropdownOption(key=str(a.id), text=a.name) for a in cards],
            value=str(debt.account_id) if debt and debt.account_id else "none",
        )
        self.link.on_select = lambda _: self._link_changed()
        self.error = ft.Text("", size=12, color=EXPENSE_COLOR, visible=False)
        self.dialog = ft.AlertDialog(
            modal=True,
            title=ft.Text("Edit Debt" if debt else "New Debt"),
            content=ft.Container(width=460, content=ft.Column(
                [self.name, self.kind, self.balance, self.apr, self.minimum, self.original,
                 self.link, self.error],
                spacing=14, tight=True, horizontal_alignment=ft.CrossAxisAlignment.STRETCH)),
            actions=[
                ft.TextButton(content="Cancel", on_click=lambda _: ctx.page.pop_dialog()),
                ft.FilledButton(content="Save", icon=ft.Icons.CHECK, on_click=self._save),
            ],
            actions_alignment=ft.MainAxisAlignment.END,
        )
        self._link_changed()

    def _link_changed(self) -> None:
        account_id = int(self.link.value) if self.link.value not in (None, "", "none") else None
        self.balance.disabled = account_id is not None
        self.kind.disabled = account_id is not None
        if account_id is not None:
            owed = next(
                (b.owed_cents for b in self.ctx.repo.account_balances() if b.account.id == account_id), 0
            )
            self.balance.value = f"{owed / 100:.2f}"
            self.kind.value = DebtKind.CREDIT_CARD.value
            status = self.ctx.service.current_minimum_payment(account_id)
            if status is not None:
                self.minimum.value = f"{status.amount_cents / 100:.2f}"
                self.minimum.disabled = True
            else:
                self.minimum.disabled = False
        else:
            self.minimum.disabled = False

    def open(self) -> None:
        self.ctx.page.show_dialog(self.dialog)

    def _refresh_dialog(self) -> None:
        """Repaint the page (and its dialog overlay) after setting validation errors.

        Updating the page rather than the dialog itself works before the dialog has been
        attached, which is how the editor's tests drive ``_save`` directly.
        """
        self.ctx.page.update()

    def _save(self, _: ft.Event) -> None:
        for field in (self.name, self.balance, self.apr, self.minimum, self.original):
            field.error = None
        self.error.visible = False
        valid = True
        name = (self.name.value or "").strip()
        if not name:
            self.name.error = "Name is required"
            valid = False
        try:
            balance = parse_signed_money(self.balance.value)
            if balance < 0:
                raise ValueError("Balance cannot be negative")
        except ValueError as exc:
            self.balance.error = str(exc)
            valid = False
        try:
            apr = parse_percent_bps(self.apr.value)
        except ValueError as exc:
            self.apr.error = str(exc)
            valid = False
        try:
            minimum = parse_signed_money(self.minimum.value) if (self.minimum.value or "").strip() else 0
            if minimum < 0:
                raise ValueError("Minimum payment cannot be negative")
        except ValueError as exc:
            self.minimum.error = str(exc)
            valid = False
        try:
            original = (
                parse_signed_money(self.original.value)
                if (self.original.value or "").strip()
                else None
            )
            if original is not None and original < 0:
                raise ValueError("Original balance cannot be negative")
        except ValueError as exc:
            self.original.error = str(exc)
            valid = False
        link = int(self.link.value) if self.link.value not in (None, "", "none") else None
        kind = DebtKind.CREDIT_CARD if link is not None else DebtKind(self.kind.value)
        if not valid:
            self._refresh_dialog()
            return
        try:
            if self.debt is not None:
                self.ctx.service.update_debt(
                    self.debt.id, name=name, kind=kind, balance_cents=balance, apr_bps=apr,
                    minimum_cents=minimum, original_balance_cents=original, account_id=link,
                )
            else:
                self.ctx.service.create_debt(
                    name, kind, balance, apr, minimum,
                    original_balance_cents=original, account_id=link,
                )
        except RepositoryError as exc:
            self.error.value = str(exc)
            self.error.visible = True
            self._refresh_dialog()
            return
        self.ctx.page.pop_dialog()
        self.ctx.notify(f"Debt '{name}' saved")
        self.ctx.data_changed()


class DebtPaymentDialog:
    """Record a real payment against a debt, moving money from a funding account."""

    def __init__(self, ctx: AppContext, summary: DebtSummary) -> None:
        self.ctx = ctx
        self.summary = summary
        debt = summary.debt
        self.amount = ft.TextField(
            label="Amount",
            prefix_icon=ft.Icons.ATTACH_MONEY,
            keyboard_type=ft.KeyboardType.NUMBER,
            autofocus=True,
            on_submit=self._save,
        )
        funding = [
            a for a in ctx.repo.list_accounts()
            if not a.kind.is_liability and a.id != debt.account_id
        ]
        default_funding = next(
            (a for a in funding if a.kind is AccountKind.CHECKING),
            funding[0] if funding else None,
        )
        self.funding = ft.Dropdown(
            label="From account",
            options=[ft.DropdownOption(key=str(a.id), text=f"{a.name} ({a.kind.label})") for a in funding],
            value=str(default_funding.id) if default_funding else None,
        )
        self.date = date_field(ctx.page, "Payment date", date.today())
        self.note = ft.TextField(label="Note", hint_text="Optional")
        categories = [c for c in ctx.repo.list_categories() if c.type is TransactionType.EXPENSE]
        preferred = next(
            (c for c in categories if c.name == "Debt payments"),
            categories[0] if categories else None,
        )
        self.category = ft.Dropdown(
            label="Category",
            options=[ft.DropdownOption(key=str(c.id), text=c.name) for c in categories],
            value=str(preferred.id) if preferred else None,
            visible=debt.account_id is None,
        )
        self.error = ft.Text("", size=12, color=EXPENSE_COLOR, visible=False)

        self.dialog = ft.AlertDialog(
            modal=True,
            title=ft.Text(f"Log payment — {debt.name}"),
            content=ft.Container(width=420, content=ft.Column(
                [
                    ft.Text(
                        f"{format_money(summary.balance_cents)} owed.",
                        size=12,
                        color=ft.Colors.ON_SURFACE_VARIANT,
                    ),
                    self.amount,
                    self.funding,
                    self.date,
                    self.note,
                    self.category,
                    self.error,
                ],
                spacing=14,
                tight=True,
                horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
            )),
            actions=[
                ft.TextButton(content="Cancel", on_click=lambda _: ctx.page.pop_dialog()),
                ft.FilledButton(content="Record payment", icon=ft.Icons.PAYMENT, on_click=self._save),
            ],
            actions_alignment=ft.MainAxisAlignment.END,
        )

    def open(self) -> None:
        self.ctx.page.show_dialog(self.dialog)

    def _save(self, _: ft.Event) -> None:
        self.amount.error = None
        self.date.error = None
        self.funding.error_text = None
        self.error.visible = False
        valid = True
        try:
            amount = parse_money(self.amount.value)
        except ValueError as exc:
            self.amount.error = str(exc)
            valid = False
        try:
            on_date = parse_date(self.date.value)
        except ValueError as exc:
            self.date.error = str(exc)
            valid = False
        if not self.funding.value:
            self.funding.error_text = "Select a funding account"
            valid = False
        if not valid:
            # Repaint through the page so validation works even before the dialog is attached.
            self.ctx.page.update()
            return

        debt = self.summary.debt
        category_id = (
            int(self.category.value)
            if debt.account_id is None and self.category.value
            else None
        )
        try:
            self.ctx.service.record_debt_payment(
                debt.id,
                int(self.funding.value),
                amount,
                on_date,
                category_id,
                (self.note.value or "").strip(),
            )
        except RepositoryError as exc:
            self.error.value = str(exc)
            self.error.visible = True
            self.ctx.page.update()
            return
        self.ctx.page.pop_dialog()
        self.ctx.notify(f"Payment of {format_money(amount)} recorded for '{debt.name}'")
        self.ctx.data_changed()


class DebtHistoryDialog:
    """Every payment tagged to a debt, newest first."""

    def __init__(self, ctx: AppContext, debt: Debt) -> None:
        self.ctx = ctx
        self.debt = debt
        payments = ctx.service.debt_payments(debt.id)
        rows: list[ft.Control] = []
        for tx in payments:
            if tx.category_name:
                source = tx.category_name
            elif tx.transfer_account_name:
                source = f"Transfer to {tx.transfer_account_name}"
            else:
                source = "Transfer"
            rows.append(ft.Row(
                [
                    ft.Text(f"{tx.occurred_on:%b %d, %Y}", size=12, width=110),
                    ft.Text(format_money(tx.amount_cents), size=12, width=90,
                            text_align=ft.TextAlign.RIGHT),
                    ft.Text(tx.account_name or "", size=12, width=120),
                    ft.Text(source, size=12, expand=True),
                ],
                spacing=GAP,
            ))
        body: list[ft.Control] = [
            ft.Column(rows, spacing=6, tight=True)
            if payments
            else ft.Text("No payments recorded yet.", size=12, color=ft.Colors.ON_SURFACE_VARIANT)
        ]
        if debt.account_id is not None:
            body.append(ft.Text(
                "Payments made outside the Log payment dialog still change the balance but are not listed here.",
                size=11,
                color=ft.Colors.ON_SURFACE_VARIANT,
            ))
        self.dialog = ft.AlertDialog(
            modal=True,
            title=ft.Text(f"{debt.name} — payments"),
            content=ft.Container(
                width=560,
                content=ft.Column(body, spacing=10, tight=True, scroll=ft.ScrollMode.AUTO),
            ),
            actions=[ft.TextButton(content="Close", on_click=lambda _: ctx.page.pop_dialog())],
            actions_alignment=ft.MainAxisAlignment.END,
        )

    def open(self) -> None:
        self.ctx.page.show_dialog(self.dialog)
