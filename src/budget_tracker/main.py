"""Application entry point: builds the shell (navigation + content) and wires views."""

from __future__ import annotations

import logging
import os
import sys
from collections.abc import Callable
from pathlib import Path

import flet as ft

from . import __version__
from .config import APP_NAME, db_path
from .db import connect
from .formatting import format_money
from .repository import Repository
from .services import BudgetService
from .ui.context import AppContext
from .ui.theme import EXPENSE_COLOR, SEED_COLOR, WARNING_COLOR, build_theme, fs, set_dark, set_text_scale
from .ui.views.accounts import AccountsView
from .ui.views.bills import BillsView, bill_avatar, status_chip
from .ui.views.budgets import BudgetsView
from .ui.views.categories import CategoriesView
from .ui.views.dashboard import DashboardView
from .ui.views.debts import DebtsView
from .ui.views.goals import GoalsView
from .ui.views.transactions import TransactionsView
from .ui.views.year import YearView

log = logging.getLogger(__name__)

THEME_SETTING = "theme_mode"
TEXT_SIZE_SETTING = "text_scale"

NAV_ITEMS = [
    ("Dashboard", ft.Icons.DASHBOARD_OUTLINED, ft.Icons.DASHBOARD),
    ("Year", ft.Icons.CALENDAR_MONTH_OUTLINED, ft.Icons.CALENDAR_MONTH),
    ("Transactions", ft.Icons.RECEIPT_LONG_OUTLINED, ft.Icons.RECEIPT_LONG),
    ("Accounts", ft.Icons.ACCOUNT_BALANCE_OUTLINED, ft.Icons.ACCOUNT_BALANCE),
    ("Goals", ft.Icons.SAVINGS_OUTLINED, ft.Icons.SAVINGS),
    ("Debts", ft.Icons.CREDIT_SCORE_OUTLINED, ft.Icons.CREDIT_SCORE),
    ("Bills", ft.Icons.EVENT_NOTE_OUTLINED, ft.Icons.EVENT_NOTE),
    ("Budgets", ft.Icons.TRACK_CHANGES_OUTLINED, ft.Icons.TRACK_CHANGES),
    ("Categories", ft.Icons.CATEGORY_OUTLINED, ft.Icons.CATEGORY),
]
TRANSACTIONS_INDEX = 2
BILLS_INDEX = 6
ALERT_MAX_ROWS = 6


def _icon_path() -> Path:
    """Absolute path to the runtime ``.ico``.

    Flet ignores a relative ``assets/...`` icon path on Windows; the title-bar icon only
    appears when this is an absolute ``.ico`` path.
    """
    return Path(__file__).resolve().parent.parent / "assets" / "icon.ico"


def _pinned_page(control: ft.Control) -> ft.Control:
    """Pin a view's page header and let everything below it scroll.

    Every view's control is a Column whose first child is its ``page_header``. Splitting that
    Column keeps the title and its actions fixed while the body scrolls beneath them.
    """
    if not isinstance(control, ft.Column) or len(control.controls) < 2:
        return control
    header, *body = control.controls
    return ft.Column(
        [
            header,
            ft.Column(
                body,
                spacing=control.spacing,
                expand=True,
                scroll=ft.ScrollMode.AUTO,
            ),
        ],
        spacing=control.spacing,
        expand=True,
    )


def main(page: ft.Page) -> None:
    repo = Repository(connect(db_path()))
    ctx = AppContext(page=page, repo=repo, service=BudgetService(repo))

    page.title = APP_NAME
    page.theme = build_theme()
    page.dark_theme = build_theme(dark=True)
    page.theme_mode = ft.ThemeMode.DARK if repo.get_setting(THEME_SETTING) == "dark" else ft.ThemeMode.LIGHT
    set_dark(page.theme_mode == ft.ThemeMode.DARK)
    set_text_scale(1.15 if repo.get_setting(TEXT_SIZE_SETTING) == "large" else 1.0)
    page.padding = 0
    page.window.width = 1320
    page.window.height = 860
    page.window.min_width = 900
    page.window.min_height = 640
    icon = _icon_path()
    page.window.icon = str(icon) if icon.is_file() else "assets/icon.ico"

    def show_view(index: int) -> None:
        rail.selected_index = index
        content.content = _pinned_page(views[index].control)
        page.update()

    def build_views() -> list:
        return [
            DashboardView(ctx, on_view_bills=lambda: show_view(BILLS_INDEX)),
            YearView(ctx),
            TransactionsView(ctx),
            AccountsView(ctx),
            GoalsView(ctx),
            DebtsView(ctx),
            BillsView(ctx),
            BudgetsView(ctx),
            CategoriesView(ctx),
        ]

    views = build_views()
    content = ft.Container(
        content=_pinned_page(views[0].control),
        padding=ft.Padding.symmetric(horizontal=32, vertical=24),
        expand=True,
    )

    def navigate(e: ft.Event[ft.NavigationRail]) -> None:
        content.content = _pinned_page(views[e.control.selected_index].control)

    def toggle_theme(_: ft.Event) -> None:
        dark = page.theme_mode != ft.ThemeMode.DARK
        page.theme_mode = ft.ThemeMode.DARK if dark else ft.ThemeMode.LIGHT
        set_dark(dark)
        theme_button.icon = ft.Icons.LIGHT_MODE if dark else ft.Icons.DARK_MODE
        repo.set_setting(THEME_SETTING, "dark" if dark else "light")
        ctx.data_changed()

    def quick_add(_: ft.Event) -> None:
        rail.selected_index = TRANSACTIONS_INDEX
        content.content = _pinned_page(views[TRANSACTIONS_INDEX].control)
        views[TRANSACTIONS_INDEX].open_editor()

    theme_button = ft.IconButton(
        icon=ft.Icons.LIGHT_MODE if page.theme_mode == ft.ThemeMode.DARK else ft.Icons.DARK_MODE,
        tooltip="Toggle light/dark theme",
        on_click=toggle_theme,
    )
    text_button = ft.IconButton(
        icon=ft.Icons.FORMAT_SIZE,
        tooltip="Text size",
        on_click=lambda event: choose_text_size(event),
    )

    rail = ft.NavigationRail(
        selected_index=0,
        label_type=ft.NavigationRailLabelType.ALL,
        min_width=96,
        group_alignment=-0.85,
        indicator_color=ft.Colors.PRIMARY_CONTAINER,
        selected_label_text_style=ft.TextStyle(
            color=ft.Colors.ON_PRIMARY_CONTAINER, weight=ft.FontWeight.BOLD
        ),
        leading=ft.Column(
            [
                ft.Container(
                    content=ft.Icon(ft.Icons.ACCOUNT_BALANCE_WALLET, color=ft.Colors.ON_PRIMARY, size=fs(26)),
                    width=48,
                    height=48,
                    border_radius=14,
                    bgcolor=SEED_COLOR,
                    alignment=ft.Alignment.CENTER,
                ),
                ft.Text("Budget", size=fs(13), weight=ft.FontWeight.BOLD),
                ft.Container(height=8),
                ft.FloatingActionButton(icon=ft.Icons.ADD, tooltip="Add transaction", on_click=quick_add, mini=True),
            ],
            horizontal_alignment=ft.CrossAxisAlignment.CENTER,
            spacing=4,
        ),
        trailing=ft.Column(
            [
                ft.Row(
                    [theme_button, text_button],
                    alignment=ft.MainAxisAlignment.CENTER,
                    spacing=0,
                ),
                ft.Text(f"v{__version__}", size=fs(11), color=ft.Colors.ON_SURFACE_VARIANT),
            ],
            horizontal_alignment=ft.CrossAxisAlignment.CENTER,
            spacing=2,
        ),
        destinations=[
            ft.NavigationRailDestination(
                icon=icon,
                selected_icon=ft.Icon(selected, color=ft.Colors.ON_PRIMARY_CONTAINER),
                label=label,
            )
            for label, icon, selected in NAV_ITEMS
        ],
        on_change=navigate,
    )

    def rebuild_views() -> None:
        nonlocal views
        ctx.reset_listeners()
        views = build_views()
        rail.selected_index = min(rail.selected_index, len(views) - 1)
        content.content = _pinned_page(views[rail.selected_index].control)
        page.update()

    def apply_text_scale(scale: float, label: str) -> None:
        set_text_scale(scale)
        repo.set_setting(TEXT_SIZE_SETTING, label)
        page.pop_dialog()
        rebuild_views()

    def choose_text_size(_: ft.Event) -> None:
        page.show_dialog(
            ft.AlertDialog(
                modal=True,
                title=ft.Text("Text size"),
                content=ft.Column(
                    [
                        ft.Text(
                            "Choose a larger size if small text is hard to read.",
                            size=fs(13),
                        ),
                        ft.FilledButton(
                            content="Normal",
                            on_click=lambda _: apply_text_scale(1.0, "normal"),
                        ),
                        ft.FilledButton(
                            content="Large",
                            on_click=lambda _: apply_text_scale(1.15, "large"),
                        ),
                    ],
                    spacing=12,
                    tight=True,
                ),
                actions=[
                    ft.TextButton(content="Cancel", on_click=lambda _: page.pop_dialog())
                ],
                actions_alignment=ft.MainAxisAlignment.END,
            )
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
        rows.append(ft.Text(f"and {len(due) - ALERT_MAX_ROWS} more…", size=fs(13), color=ft.Colors.ON_SURFACE_VARIANT))
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


def _assets_dir() -> str:
    """Return the assets folder for a source run; bundled builds keep the default.

    ``flet run`` resolves ``assets`` relative to the app path, but running from the project root
    would miss ``src/assets``. Prefer the checkout's folder when it exists, otherwise leave the
    default string so ``flet build`` behaves exactly as before.
    """
    if Path("assets").is_dir():
        return "assets"
    candidate = Path(__file__).resolve().parent.parent / "assets"
    return str(candidate) if candidate.is_dir() else "assets"


def _taskbar_identity() -> dict[str, str]:
    """Return the Windows taskbar AppUserModel/relaunch values for a source run.

    A source run launches the shared ``flet.exe`` desktop client, so Windows resolves the
    taskbar button from that executable. Packaged builds already set
    ``FLET_APP_USER_MODEL_ID`` in their PyInstaller runtime hook; for source runs, these
    values give the client window Budget Tracker's name, icon and relaunch target.
    ``page.window.icon`` alone only changes the window icon, not the taskbar identity.
    """
    if sys.platform != "win32" or os.environ.get("FLET_APP_USER_MODEL_ID"):
        return {}
    root = Path(__file__).resolve().parents[2]
    icon = root / "src" / "assets" / "icon.ico"
    if not icon.is_file():
        return {}
    launcher = root / "packaging" / "launch.pyw"
    command = (
        f'"{sys.executable}" "{launcher}"'
        if launcher.is_file()
        else f'"{sys.executable}" -m budget_tracker'
    )
    return {
        "FLET_APP_USER_MODEL_ID": "com.budgettracker.budget_tracker",
        "FLET_APP_RELAUNCH_DISPLAY_NAME": APP_NAME,
        "FLET_APP_RELAUNCH_ICON": f"{icon},0",
        "FLET_APP_RELAUNCH_COMMAND": command,
    }


def _apply_taskbar_identity() -> None:
    """Publish the source-run taskbar identity before the Flet client starts."""
    os.environ.update(_taskbar_identity())


def run() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    log.info("Using database at %s", db_path())
    _apply_taskbar_identity()
    ft.run(main, assets_dir=_assets_dir())


if __name__ == "__main__":
    run()
