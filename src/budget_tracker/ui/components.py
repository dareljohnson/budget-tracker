"""Reusable presentational components."""

from __future__ import annotations

from collections.abc import Callable
from datetime import date, datetime

import flet as ft

from ..formatting import format_money, month_label, parse_date, shift_month
from ..models import Account, AccountBalance, Transaction, TransactionType
from .theme import EXPENSE_COLOR, GAP, INCOME_COLOR, RADIUS, SEED_COLOR, icon_for


def card(
    content: ft.Control,
    *,
    padding: int = 20,
    expand: bool | int = False,
    col: dict | int | None = None,
    height: float | None = None,
) -> ft.Container:
    container = ft.Container(
        content=content,
        padding=padding,
        expand=expand,
        height=height,
        bgcolor=ft.Colors.SURFACE_CONTAINER_LOW,
        border_radius=RADIUS,
        border=ft.Border.all(1, ft.Colors.OUTLINE_VARIANT),
    )
    if col is not None:
        container.col = col
    return container


def page_header(title: str, subtitle: str, actions: list[ft.Control] | None = None) -> ft.Row:
    return ft.Row(
        [
            ft.Column(
                [
                    ft.Text(title, size=28, weight=ft.FontWeight.BOLD),
                    ft.Text(subtitle, size=14, color=ft.Colors.ON_SURFACE_VARIANT),
                ],
                spacing=2,
                expand=True,
            ),
            ft.Row(actions or [], spacing=8, wrap=True, vertical_alignment=ft.CrossAxisAlignment.CENTER),
        ],
        vertical_alignment=ft.CrossAxisAlignment.CENTER,
    )


def section_title(text: str, trailing: ft.Control | None = None) -> ft.Row:
    controls: list[ft.Control] = [ft.Text(text, size=16, weight=ft.FontWeight.W_600, expand=True)]
    if trailing:
        controls.append(trailing)
    return ft.Row(controls, vertical_alignment=ft.CrossAxisAlignment.CENTER)


def category_avatar(icon: str | None, color: str | None, size: int = 40) -> ft.Container:
    color = color or ft.Colors.PRIMARY
    return ft.Container(
        content=ft.Icon(icon_for(icon), color=color, size=size * 0.5),
        width=size,
        height=size,
        border_radius=size / 2,
        bgcolor=ft.Colors.with_opacity(0.15, color),
        alignment=ft.Alignment.CENTER,
    )


def stat_card(title: str, value: str, icon: ft.IconData, color: str, caption: str = "") -> ft.Container:
    return card(
        ft.Row(
            [
                ft.Column(
                    [
                        ft.Text(title, size=13, color=ft.Colors.ON_SURFACE_VARIANT, weight=ft.FontWeight.W_500),
                        ft.Text(value, size=24, weight=ft.FontWeight.BOLD),
                        ft.Text(caption, size=12, color=ft.Colors.ON_SURFACE_VARIANT, visible=bool(caption)),
                    ],
                    spacing=4,
                    expand=True,
                ),
                ft.Container(
                    content=ft.Icon(icon, color=color, size=26),
                    width=52,
                    height=52,
                    border_radius=14,
                    bgcolor=ft.Colors.with_opacity(0.12, color),
                    alignment=ft.Alignment.CENTER,
                ),
            ],
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        ),
        col={"xs": 12, "sm": 6, "xl": 3},
    )


def empty_state(icon: ft.IconData, title: str, message: str, action: ft.Control | None = None) -> ft.Container:
    controls: list[ft.Control] = [
        ft.Icon(icon, size=48, color=ft.Colors.OUTLINE),
        ft.Text(title, size=16, weight=ft.FontWeight.W_600),
        ft.Text(message, size=13, color=ft.Colors.ON_SURFACE_VARIANT, text_align=ft.TextAlign.CENTER),
    ]
    if action:
        controls.append(action)
    return ft.Container(
        content=ft.Column(controls, horizontal_alignment=ft.CrossAxisAlignment.CENTER, spacing=8),
        padding=32,
        alignment=ft.Alignment.CENTER,
    )


def amount_text(tx: Transaction, size: int = 15) -> ft.Text:
    sign = tx.flow_sign
    color = INCOME_COLOR if sign > 0 else EXPENSE_COLOR if sign < 0 else ft.Colors.ON_SURFACE_VARIANT
    return ft.Text(
        format_money(tx.amount_cents * sign, signed=True) if sign else format_money(tx.amount_cents),
        size=size,
        weight=ft.FontWeight.W_600,
        color=color,
    )


def account_avatar(account: Account, size: int = 40) -> ft.Container:
    return category_avatar(account.kind.icon, account.color, size)


def balance_text(balance: AccountBalance, size: int = 15) -> ft.Text:
    """Show credit cards as an amount owed; other accounts as a signed balance."""
    if balance.account.kind.is_liability:
        owed = balance.owed_cents
        value = format_money(owed) if owed else format_money(balance.balance_cents)
        color = EXPENSE_COLOR if owed else INCOME_COLOR
    else:
        value = format_money(balance.balance_cents)
        color = EXPENSE_COLOR if balance.balance_cents < 0 else None
    return ft.Text(value, size=size, weight=ft.FontWeight.W_600, color=color)


def _transaction_subtitle(tx: Transaction) -> str:
    when = f"{tx.occurred_on:%b %d, %Y}"
    if tx.type == TransactionType.TRANSFER:
        return f"Transfer  •  {tx.account_name} → {tx.transfer_account_name}  •  {when}"
    label = f"{tx.category_name} (refund)" if tx.type == TransactionType.REFUND else tx.category_name
    return f"{label}  •  {tx.account_name}  •  {when}"


def transaction_row(
    tx: Transaction,
    on_edit: Callable[[Transaction], None] | None = None,
    on_delete: Callable[[Transaction], None] | None = None,
) -> ft.Container:
    actions: list[ft.Control] = []
    if on_edit:
        actions.append(ft.IconButton(ft.Icons.EDIT_OUTLINED, tooltip="Edit", icon_size=20, on_click=lambda _: on_edit(tx)))
    if on_delete:
        actions.append(
            ft.IconButton(ft.Icons.DELETE_OUTLINE, tooltip="Delete", icon_size=20, icon_color=EXPENSE_COLOR, on_click=lambda _: on_delete(tx))
        )
    if tx.type == TransactionType.TRANSFER:
        avatar = category_avatar("SWAP_HORIZ", SEED_COLOR)
        title = tx.description or f"Transfer to {tx.transfer_account_name}"
    else:
        avatar = category_avatar("UNDO" if tx.type == TransactionType.REFUND else tx.category_icon, tx.category_color)
        title = tx.description or tx.category_name or ""
    return ft.Container(
        content=ft.Row(
            [
                avatar,
                ft.Column(
                    [
                        ft.Text(title, weight=ft.FontWeight.W_500, max_lines=1, overflow=ft.TextOverflow.ELLIPSIS),
                        ft.Text(_transaction_subtitle(tx), size=12, color=ft.Colors.ON_SURFACE_VARIANT),
                    ],
                    spacing=2,
                    expand=True,
                ),
                amount_text(tx),
                *actions,
            ],
            spacing=GAP,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        ),
        padding=ft.Padding.symmetric(horizontal=12, vertical=10),
        border_radius=10,
    )


class MonthSelector:
    """Previous / next month navigator. Use ``.control`` to place it in a layout."""

    def __init__(self, month: str, on_change: Callable[[str], None]) -> None:
        self.month = month
        self._on_change = on_change
        self._label = ft.Text(month_label(month), size=15, weight=ft.FontWeight.W_600, width=150, text_align=ft.TextAlign.CENTER)
        self.control = ft.Row(
            controls=[
                ft.IconButton(ft.Icons.CHEVRON_LEFT, tooltip="Previous month", on_click=lambda _: self._shift(-1)),
                self._label,
                ft.IconButton(ft.Icons.CHEVRON_RIGHT, tooltip="Next month", on_click=lambda _: self._shift(1)),
            ],
            spacing=0,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        )

    def _shift(self, delta: int) -> None:
        self.month = shift_month(self.month, delta)
        self._label.value = month_label(self.month)
        self._on_change(self.month)


def date_field(page: ft.Page, label: str, value: date, **kwargs) -> ft.TextField:
    """An ISO date text field with a calendar button that opens a DatePicker."""
    field = ft.TextField(label=label, hint_text="YYYY-MM-DD", value=value.isoformat(), **kwargs)

    def picked(e: ft.Event[ft.DatePicker]) -> None:
        if e.control.value:
            chosen = e.control.value
            field.value = (chosen.date() if isinstance(chosen, datetime) else chosen).isoformat()
            field.error = None
            field.update()

    def open_picker(_: ft.Event) -> None:
        try:
            current = parse_date(field.value)
        except ValueError:
            current = date.today()
        page.show_dialog(
            ft.DatePicker(
                value=datetime.combine(current, datetime.min.time()),
                first_date=datetime(2000, 1, 1),
                last_date=datetime(2100, 12, 31),
                on_change=picked,
            )
        )

    field.suffix = ft.IconButton(ft.Icons.CALENDAR_MONTH, tooltip="Pick a date", on_click=open_picker)
    return field


def confirm_dialog(page: ft.Page, title: str, message: str, on_confirm: Callable[[], None], confirm_text: str = "Delete") -> None:
    def confirm(_: ft.Event) -> None:
        page.pop_dialog()
        on_confirm()

    page.show_dialog(
        ft.AlertDialog(
            modal=True,
            icon=ft.Icon(ft.Icons.WARNING_AMBER_ROUNDED, color=EXPENSE_COLOR),
            title=ft.Text(title),
            content=ft.Text(message),
            actions=[
                ft.TextButton(content="Cancel", on_click=lambda _: page.pop_dialog()),
                ft.FilledButton(content=confirm_text, on_click=confirm, style=ft.ButtonStyle(bgcolor=EXPENSE_COLOR, color=ft.Colors.WHITE)),
            ],
            actions_alignment=ft.MainAxisAlignment.END,
        )
    )
