"""Record and review the minimum payment printed on each credit card statement."""

from __future__ import annotations

from datetime import date

import flet as ft

from ...formatting import format_money, month_label, parse_money
from ...models import Account, MinimumPaymentStatus
from ...repository import RepositoryError
from ..context import AppContext
from ..theme import EXPENSE_COLOR, GAP, INCOME_COLOR, WARNING_COLOR

RECENT_MONTHS = 18


def recent_months(count: int = RECENT_MONTHS) -> list[str]:
    """The current month and the ones before it, newest first."""
    today = date.today()
    year, month = today.year, today.month
    months: list[str] = []
    for _ in range(count):
        months.append(f"{year:04d}-{month:02d}")
        month -= 1
        if month == 0:
            month = 12
            year -= 1
    return months


def minimum_status_chip(status: MinimumPaymentStatus | None) -> ft.Control:
    if status is None:
        return ft.Text("Not recorded", size=12, color=ft.Colors.ON_SURFACE_VARIANT)
    if status.state == "paid":
        text, color = "Paid", INCOME_COLOR
    elif status.state == "partial":
        text = f"Partial · {format_money(status.paid_cents)} of {format_money(status.amount_cents)}"
        color = WARNING_COLOR
    else:
        text, color = "Unpaid", EXPENSE_COLOR
    return ft.Text(text, size=12, color=color, weight=ft.FontWeight.W_600)


class MinimumPaymentDialog:
    """Record one card's statement minimum by month, with the matching status history."""

    def __init__(self, ctx: AppContext, account: Account) -> None:
        self.ctx = ctx
        self.account = account
        self.months = recent_months()
        self.month = ft.Dropdown(
            label="Statement month",
            width=210,
            options=[ft.DropdownOption(key=month, text=month_label(month)) for month in self.months],
            value=self.months[0],
        )
        self.amount = ft.TextField(
            label="Minimum payment",
            prefix_icon=ft.Icons.PAYMENT,
            keyboard_type=ft.KeyboardType.NUMBER,
            hint_text="Amount printed on the statement",
            on_submit=self._save,
        )
        self.history = ft.Column(spacing=2, tight=True)
        self.dialog = ft.AlertDialog(
            modal=True,
            title=ft.Text(f"Minimum payments — {account.name}"),
            content=ft.Container(
                width=540,
                height=390,
                content=ft.Column(
                    [
                        ft.Text(
                            "Record the minimum printed each month. Payments into this card are matched "
                            "oldest-unpaid first, so a payment in the following month still counts.",
                            size=12,
                            color=ft.Colors.ON_SURFACE_VARIANT,
                        ),
                        ft.Row([self.month, self.amount], spacing=GAP),
                        ft.Row(
                            [
                                ft.FilledButton(content="Save", icon=ft.Icons.CHECK, on_click=self._save),
                                ft.TextButton(content="Delete this month", on_click=self._delete),
                            ],
                            spacing=GAP,
                        ),
                        ft.Divider(height=1),
                        ft.Text("History", size=14, weight=ft.FontWeight.W_600),
                        self.history,
                    ],
                    spacing=10,
                    tight=True,
                    scroll=ft.ScrollMode.AUTO,
                ),
            ),
            actions=[ft.TextButton(content="Close", on_click=lambda _: ctx.page.pop_dialog())],
            actions_alignment=ft.MainAxisAlignment.END,
        )
        self._render_history()

    def open(self) -> None:
        self._render_history()
        self.ctx.page.show_dialog(self.dialog)

    def _render_history(self) -> None:
        statuses = self.ctx.service.minimum_payment_statuses(self.account.id)
        if not statuses:
            self.history.controls = [
                ft.Text("No minimum payments recorded yet.", size=12, color=ft.Colors.ON_SURFACE_VARIANT)
            ]
            return
        rows: list[ft.Control] = []
        for status in statuses:
            rows.append(
                ft.Row(
                    [
                        ft.Text(month_label(status.month), size=12, width=130),
                        ft.Text(
                            format_money(status.amount_cents),
                            size=12,
                            width=86,
                            text_align=ft.TextAlign.RIGHT,
                        ),
                        ft.Container(content=minimum_status_chip(status), expand=True),
                        ft.IconButton(
                            ft.Icons.EDIT_OUTLINED,
                            tooltip="Edit",
                            icon_size=18,
                            on_click=lambda _, current=status: self._edit(current),
                        ),
                    ],
                    spacing=6,
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                )
            )
        self.history.controls = rows

    def _edit(self, status: MinimumPaymentStatus) -> None:
        if status.month not in self.months:
            self.months.append(status.month)
            self.month.options.append(
                ft.DropdownOption(key=status.month, text=month_label(status.month))
            )
        self.month.value = status.month
        self.amount.value = f"{status.amount_cents / 100:.2f}"
        self.amount.error = None
        self.ctx.page.update()

    def _save(self, _: ft.Event) -> None:
        self.amount.error = None
        try:
            amount = parse_money((self.amount.value or "").strip())
        except ValueError as exc:
            self.amount.error = str(exc)
            self.ctx.page.update()
            return
        try:
            self.ctx.service.record_minimum_payment(self.account.id, self.month.value, amount)
        except RepositoryError as exc:
            self.ctx.notify(str(exc), error=True)
            return
        self.amount.value = ""
        self._render_history()
        self.ctx.page.update()
        self.ctx.data_changed()

    def _delete(self, _: ft.Event) -> None:
        try:
            self.ctx.service.delete_minimum_payment(self.account.id, self.month.value)
        except RepositoryError as exc:
            self.ctx.notify(str(exc), error=True)
            return
        self.amount.value = ""
        self._render_history()
        self.ctx.page.update()
        self.ctx.data_changed()
