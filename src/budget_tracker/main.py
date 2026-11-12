"""Application entry point: builds the shell (navigation + content) and wires views."""

from __future__ import annotations

import logging
from collections.abc import Callable

import flet as ft

from . import __version__
from .config import APP_NAME, db_path
from .db import connect
from .formatting import format_money
from .repository import Repository
from .services import BudgetService
from .ui.context import AppContext
from .ui.theme import EXPENSE_COLOR, SEED_COLOR, WARNING_COLOR, build_theme
from .ui.views.accounts import AccountsView
from .ui.views.bills import BillsView, bill_avatar, status_chip
from .ui.views.budgets import BudgetsView
from .ui.views.categories import CategoriesView
from .ui.views.dashboard import DashboardView
from .ui.views.transactions import TransactionsView

log = logging.getLogger(__name__)

THEME_SETTING = "theme_mode"

NAV_ITEMS = [
    ("Dashboard", ft.Icons.DASHBOARD_OUTLINED, ft.Icons.DASHBOARD),
    ("Transactions", ft.Icons.RECEIPT_LONG_OUTLINED, ft.Icons.RECEIPT_LONG),
    ("Accounts", ft.Icons.ACCOUNT_BALANCE_OUTLINED, ft.Icons.ACCOUNT_BALANCE),
    ("Bills", ft.Icons.EVENT_NOTE_OUTLINED, ft.Icons.EVENT_NOTE),
    ("Budgets", ft.Icons.TRACK_CHANGES_OUTLINED, ft.Icons.TRACK_CHANGES),
    ("Categories", ft.Icons.CATEGORY_OUTLINED, ft.Icons.CATEGORY),
]
TRANSACTIONS_INDEX = 1
BILLS_INDEX = 3
ALERT_MAX_ROWS = 6


def main(page: ft.Page) -> None:
    repo = Repository(connect(db_path()))
    ctx = AppContext(page=page, repo=repo, service=BudgetService(repo))

    page.title = APP_NAME
    page.theme = build_theme()
    page.dark_theme = build_theme()
    page.theme_mode = ft.ThemeMode.DARK if repo.get_setting(THEME_SETTING) == "dark" else ft.ThemeMode.LIGHT
    page.padding = 0
    page.window.width = 1320
    page.window.height = 860
    page.window.min_width = 900
    page.window.min_height = 640

    def show_view(index: int) -> None:
        rail.selected_index = index
        content.content = views[index].control
        page.update()

    views = [
        DashboardView(ctx, on_view_bills=lambda: show_view(BILLS_INDEX)),
        TransactionsView(ctx),
        AccountsView(ctx),
        BillsView(ctx),
        BudgetsView(ctx),
        CategoriesView(ctx),
    ]
    content = ft.Container(content=views[0].control, padding=ft.Padding.symmetric(horizontal=32, vertical=24), expand=True)

    def navigate(e: ft.Event[ft.NavigationRail]) -> None:
        content.content = views[e.control.selected_index].control

    def toggle_theme(_: ft.Event) -> None:
        dark = page.theme_mode != ft.ThemeMode.DARK
        page.theme_mode = ft.ThemeMode.DARK if dark else ft.ThemeMode.LIGHT
        theme_button.icon = ft.Icons.LIGHT_MODE if dark else ft.Icons.DARK_MODE
        repo.set_setting(THEME_SETTING, "dark" if dark else "light")

    def quick_add(_: ft.Event) -> None:
        rail.selected_index = TRANSACTIONS_INDEX
        content.content = views[TRANSACTIONS_INDEX].control
        views[TRANSACTIONS_INDEX].open_editor()

    theme_button = ft.IconButton(
        icon=ft.Icons.LIGHT_MODE if page.theme_mode == ft.ThemeMode.DARK else ft.Icons.DARK_MODE,
        tooltip="Toggle light/dark theme",
        on_click=toggle_theme,
    )

    rail = ft.NavigationRail(
        selected_index=0,
        label_type=ft.NavigationRailLabelType.ALL,
        min_width=96,
        group_alignment=-0.85,
        leading=ft.Column(
            [
                ft.Container(
                    content=ft.Icon(ft.Icons.ACCOUNT_BALANCE_WALLET, color=ft.Colors.WHITE, size=26),
                    width=48,
                    height=48,
                    border_radius=14,
                    bgcolor=SEED_COLOR,
                    alignment=ft.Alignment.CENTER,
                ),
                ft.Text("Budget", size=12, weight=ft.FontWeight.BOLD),
                ft.Container(height=8),
                ft.FloatingActionButton(icon=ft.Icons.ADD, tooltip="Add transaction", on_click=quick_add, mini=True),
            ],
            horizontal_alignment=ft.CrossAxisAlignment.CENTER,
            spacing=4,
        ),
        trailing=ft.Column(
            [theme_button, ft.Text(f"v{__version__}", size=10, color=ft.Colors.ON_SURFACE_VARIANT)],
            horizontal_alignment=ft.CrossAxisAlignment.CENTER,
            spacing=2,
        ),
        destinations=[ft.NavigationRailDestination(icon=icon, selected_icon=selected, label=label) for label, icon, selected in NAV_ITEMS],
        on_change=navigate,
    )

    def on_disconnect(_: ft.Event) -> None:
        repo.close()

    page.on_disconnect = on_disconnect
    page.add(ft.Row([rail, ft.VerticalDivider(width=1), content], expand=True, spacing=0))
    show_due_bills_alert(ctx, lambda: show_view(BILLS_INDEX))


def show_due_bills_alert(ctx: AppContext, on_view_bills: Callable[[], None]) -> bool:
    """On startup, pop up a summary of overdue and soon-due bills. Returns True if shown."""
    due = ctx.service.due_bills()
    if not due:
        return False
    page = ctx.page

    def view_bills(_: ft.Event) -> None:
        page.pop_dialog()
        on_view_bills()

    overdue = sum(1 for d in due if d.status == "overdue")
    rows: list[ft.Control] = [
        ft.Row(
            [
                bill_avatar(d.bill, 32),
                ft.Column([ft.Text(d.bill.name, weight=ft.FontWeight.W_500), status_chip(d)], spacing=4, expand=True),
                ft.Text(format_money(d.amount_cents), weight=ft.FontWeight.W_600),
            ],
            spacing=10,
        )
        for d in due[:ALERT_MAX_ROWS]
    ]
    if len(due) > ALERT_MAX_ROWS:
        rows.append(ft.Text(f"and {len(due) - ALERT_MAX_ROWS} more…", size=12, color=ft.Colors.ON_SURFACE_VARIANT))
    page.show_dialog(
        ft.AlertDialog(
            modal=True,
            icon=ft.Icon(ft.Icons.NOTIFICATIONS_ACTIVE, color=EXPENSE_COLOR if overdue else WARNING_COLOR),
            title=ft.Text("Payments due"),
            content=ft.Container(width=420, content=ft.Column(rows, spacing=12, tight=True)),
            actions=[
                ft.TextButton(content="Dismiss", on_click=lambda _: page.pop_dialog()),
                ft.FilledButton(content="View bills", icon=ft.Icons.EVENT_NOTE, on_click=view_bills),
            ],
            actions_alignment=ft.MainAxisAlignment.END,
        )
    )
    return True


def run() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    log.info("Using database at %s", db_path())
    ft.run(main)


if __name__ == "__main__":
    run()
