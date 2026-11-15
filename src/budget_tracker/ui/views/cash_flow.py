"""Year cash-flow tab: a 30/60/90-day projected liquid balance.

The panel consumes ``BudgetService.cash_flow`` and renders the forecast as KPI tiles, a locally
drawn Pillow chart, an upcoming-events list, per-account ending balances and an assumptions footer.
It never writes to the ledger; income planning opens ``IncomePlanDialog`` by default (or delegates
to an injected ``on_income`` callback).
"""

from __future__ import annotations

import base64
from collections.abc import Callable
from datetime import date, timedelta

import flet as ft

from ...cashflow import (
    CashEvent,
    CashFlowForecast,
    CashFlowPoint,
    clamped_day,
    income_dates,
)
from ...formatting import format_money, parse_date, parse_money
from ...models import AccountKind, IncomeFrequency, IncomeSchedule
from ...repository import LIQUID_ACCOUNT_KINDS, RepositoryError
from ..charts import ChartColors, line_chart_png
from ..components import card, confirm_dialog, date_field, empty_state, section_title
from ..context import AppContext
from ..theme import (
    EXPENSE_COLOR,
    GAP,
    INCOME_COLOR,
    RADIUS,
    SEED_COLOR,
    THEME_PRESET_SETTING,
    WARNING_COLOR,
    build_theme,
    fs,
    text_scale,
)

CHART_WIDTH = 900
CHART_HEIGHT = 260
EVENT_LIMIT = 8
HORIZONS = (30, 60, 90)
INCOME_DIALOG_WIDTH = 520
_INFLOW_KINDS = frozenset({"income", "refund", "transfer_in"})

ASSUMPTIONS = (
    "Card payments use today's owed amount; open-amount card bills project once and are netted "
    "against future card payments. Card minimums are due at the following month-end and are "
    "replaced by a card-payment bill for that card. Income starts after today. Budgets and "
    "variable spending are not included."
)
NO_INCOME_CALLOUT = "Add income to make this forecast realistic"
NO_EVENTS_CALLOUT = "Add income or a bill to see a forecast."
NO_LIQUID_ACCOUNTS = "Add a checking, savings, or cash account to forecast cash flow"
PREVIEW_NOTE = "Forecast only; record the real deposit when it arrives."
_PREVIEW_HORIZON = 400  # days: enough to show three occurrences of any supported rule


def _event_sort_key(event: CashEvent) -> tuple[date, int, str, int]:
    """Sort by date, inflows before outflows, then description and amount."""
    return (
        event.date,
        0 if event.kind in _INFLOW_KINDS else 1,
        event.description,
        event.amount_cents,
    )


def _date_label(day: date) -> str:
    return f"{day:%b} {day.day}"


def _ordinal(value: int) -> str:
    if 10 <= value % 100 <= 20:
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(value % 10, "th")
    return f"{value}{suffix}"


class CashFlowPanel:
    """Cash-flow view hosted by the Year page. Call ``refresh`` after mounting."""

    def __init__(self, ctx: AppContext, *, on_income: Callable[[], None] | None = None) -> None:
        self.ctx = ctx
        # Default to the income dialog; callers may inject their own handler.
        self.on_income = on_income if on_income is not None else self._open_income_dialog
        self.income_dialog: IncomePlanDialog | None = None
        self.horizon = 90
        self._show_all = False
        self._forecast: CashFlowForecast | None = None
        self._points: list[CashFlowPoint] = []
        self._events: list[CashEvent] = []
        self._event_rows: list[ft.Row] = []
        self._account_names: dict[int, str] = {}
        self._today = date.today()
        self._lowest_cents = 0
        self._lowest_date = date.today()
        self._has_liquid = False
        self._has_income = False

        self._subtitle = ft.Text(
            self._subtitle_text(self._today),
            size=fs(13),
            color=ft.Colors.ON_SURFACE_VARIANT,
        )
        self._horizon_control = ft.SegmentedButton(
            segments=[
                ft.Segment(value=str(days), label=ft.Text(f"{days} days")) for days in HORIZONS
            ],
            selected=["90"],
            allow_empty_selection=False,
            allow_multiple_selection=False,
            on_change=self._on_horizon,
        )
        self._income_button = ft.OutlinedButton(
            "Income plan",
            icon=ft.Icons.PAYMENTS_OUTLINED,
            on_click=lambda _: self._open_income(),
        )
        self._show_all_button = ft.TextButton("Show all", on_click=lambda _: self._toggle_show_all())

        header = ft.Row(
            [
                ft.Column(
                    [
                        ft.Text("Cash flow", size=fs(18), weight=ft.FontWeight.W_600),
                        self._subtitle,
                    ],
                    spacing=2,
                    expand=True,
                ),
                ft.Row(
                    [self._horizon_control, self._income_button],
                    spacing=8,
                    wrap=True,
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                ),
            ],
            spacing=GAP,
            wrap=True,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        )
        self._content = ft.Column(spacing=GAP)
        self.control = ft.Column([header, self._content], spacing=GAP)

    # ---------- public refresh ----------
    def refresh(self) -> None:
        """Reload the forecast for the selected horizon and rebuild the view.

        The page is not updated here: the Year view updates once the panel is mounted.
        """
        result = self.ctx.service.cash_flow(horizon_days=self.horizon)
        self._forecast = result
        self._account_names = self._load_account_names()
        self._today = self._forecast_today(result)
        horizon_end = self._today + timedelta(days=self.horizon)

        points = [
            point for point in (result.points or []) if point.date <= horizon_end
        ][: self.horizon + 1]
        events = sorted(
            [event for event in (result.events or []) if event.date <= horizon_end],
            key=_event_sort_key,
        )
        self._points = points
        self._events = events
        if points:
            lowest = min(points, key=lambda point: (point.balance_cents, point.date))
            self._lowest_cents, self._lowest_date = lowest.balance_cents, lowest.date
        else:
            self._lowest_cents = result.lowest_cents
            self._lowest_date = result.lowest_date
        self._has_liquid = bool(result.endings_by_account) or bool(points)
        self._has_income = bool(result.has_income)
        self._subtitle.value = self._subtitle_text(self._today)
        self._render()

    # ---------- render ----------
    def _render(self) -> None:
        if not self._has_liquid:
            self._content.controls = [
                card(
                    empty_state(
                        ft.Icons.ACCOUNT_BALANCE_OUTLINED,
                        "No liquid accounts",
                        NO_LIQUID_ACCOUNTS,
                    ),
                    col=12,
                )
            ]
            return

        controls: list[ft.Control] = [
            self._kpi_row_control(),
            card(self._chart_section(), col={"xs": 12}),
        ]
        callout = self._callout_control()
        if callout is not None:
            controls.append(callout)
        self._events_card_container = card(self._events_card(), col={"xs": 12, "lg": 7})
        self._ending_card_container = card(self._ending_card(), col={"xs": 12, "lg": 5})
        controls.append(
            ft.ResponsiveRow(
                [self._events_card_container, self._ending_card_container],
                spacing=GAP,
                run_spacing=GAP,
            )
        )
        controls.append(self._assumptions())
        self._content.controls = controls

    def _kpi_row_control(self) -> ft.ResponsiveRow:
        tiles = [
            self._kpi_tile(
                "Today",
                format_money(self._forecast.starting_cents),
                self._liquid_accounts_caption(),
            )
        ]
        for days in HORIZONS:
            if days > self.horizon:
                break
            tiles.append(
                self._kpi_tile(
                    f"In {days} days",
                    format_money(getattr(self._forecast, f"at_{days}")),
                    _date_label(self._today + timedelta(days=days)),
                )
            )
        lowest_color = EXPENSE_COLOR if self._lowest_cents < 0 else WARNING_COLOR
        tiles.append(
            self._kpi_tile(
                "Lowest point",
                format_money(self._lowest_cents),
                _date_label(self._lowest_date),
                color=lowest_color,
            )
        )
        self._kpi_row = ft.ResponsiveRow(tiles, spacing=GAP, run_spacing=GAP)
        return self._kpi_row

    def _kpi_tile(
        self,
        title: str,
        value: str,
        caption: str,
        *,
        color: str | None = None,
    ) -> ft.Container:
        return card(
            ft.Column(
                [
                    ft.Text(
                        title,
                        size=fs(12),
                        color=ft.Colors.ON_SURFACE_VARIANT,
                        weight=ft.FontWeight.W_500,
                        max_lines=2,
                    ),
                    ft.Text(
                        value,
                        size=fs(20),
                        weight=ft.FontWeight.BOLD,
                        color=color,
                    ),
                    ft.Text(
                        caption,
                        size=fs(11),
                        color=ft.Colors.ON_SURFACE_VARIANT,
                        max_lines=2,
                    ),
                ],
                spacing=2,
            ),
            padding=12,
            col={"xs": 6, "sm": 4, "lg": 2},
        )

    def _chart_section(self) -> ft.Column:
        colors = self._chart_colors()
        image = ft.Image(
            src=self._png_source(
                line_chart_png(self._points, colors=colors, text_scale=text_scale())
            ),
            width=CHART_WIDTH,
            height=CHART_HEIGHT,
            fit=ft.BoxFit.CONTAIN,
        )
        self._chart_image = image
        labels = [self._today] + [
            self._today + timedelta(days=days) for days in HORIZONS if days <= self.horizon
        ]
        lowest_color = EXPENSE_COLOR if self._lowest_cents < 0 else WARNING_COLOR
        return ft.Column(
            [
                section_title(
                    "Projected liquid balance",
                    ft.Text(
                        "Daily · no variable spending",
                        size=fs(12),
                        color=ft.Colors.ON_SURFACE_VARIANT,
                    ),
                ),
                ft.Container(content=image, alignment=ft.Alignment.CENTER),
                ft.Row(
                    [
                        ft.Text(_date_label(day), size=fs(12), color=ft.Colors.ON_SURFACE_VARIANT)
                        for day in labels
                    ],
                    alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                ),
                ft.Text(
                    f"Lowest point: {format_money(self._lowest_cents)} on {_date_label(self._lowest_date)}",
                    size=fs(12),
                    color=lowest_color,
                ),
            ],
            spacing=8,
        )

    def _callout_control(self) -> ft.Control | None:
        if not self._events:
            icon, message, color = ft.Icons.ADD_CHART, NO_EVENTS_CALLOUT, SEED_COLOR
        elif not self._has_income:
            icon, message, color = ft.Icons.PAYMENTS_OUTLINED, NO_INCOME_CALLOUT, WARNING_COLOR
        else:
            return None
        return card(self._callout(icon, message, color), col={"xs": 12})

    def _callout(self, icon: ft.IconData, message: str, color: str) -> ft.Container:
        return ft.Container(
            content=ft.Row(
                [
                    ft.Icon(icon, color=color, size=fs(20)),
                    ft.Text(message, size=fs(13), expand=True),
                ],
                spacing=10,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
            padding=12,
            border_radius=RADIUS,
            bgcolor=ft.Colors.with_opacity(0.08, color),
        )

    def _events_card(self) -> ft.Column:
        self._event_rows = self._build_event_rows()
        self._events_list = ft.Column(spacing=0)
        self._render_events()
        return ft.Column(
            [
                section_title(
                    "Upcoming",
                    ft.Text(
                        self._events_caption(),
                        size=fs(12),
                        color=ft.Colors.ON_SURFACE_VARIANT,
                    ),
                ),
                self._events_list,
            ],
            spacing=8,
        )

    def _build_event_rows(self) -> list[ft.Row]:
        rows: list[ft.Row] = []
        running = self._forecast.starting_cents if self._forecast else 0
        for event in self._events:
            running += event.amount_cents
            rows.append(self._event_row(event, running))
        return rows

    def _event_row(self, event: CashEvent, running: int) -> ft.Row:
        account = self._account_names.get(event.account_id, "Account")
        amount_color = INCOME_COLOR if event.amount_cents >= 0 else EXPENSE_COLOR
        return ft.Row(
            [
                ft.Text(
                    _date_label(event.date),
                    size=fs(12),
                    width=56,
                    color=ft.Colors.ON_SURFACE_VARIANT,
                ),
                ft.Column(
                    [
                        ft.Text(event.description, size=fs(13), max_lines=1, overflow=ft.TextOverflow.ELLIPSIS),
                        ft.Text(account, size=fs(11), color=ft.Colors.ON_SURFACE_VARIANT, max_lines=1),
                    ],
                    spacing=0,
                    expand=True,
                ),
                ft.Text(
                    format_money(event.amount_cents, signed=True),
                    size=fs(13),
                    width=88,
                    text_align=ft.TextAlign.RIGHT,
                    color=amount_color,
                    weight=ft.FontWeight.W_600,
                ),
                ft.Text(
                    format_money(running),
                    size=fs(12),
                    width=92,
                    text_align=ft.TextAlign.RIGHT,
                    color=ft.Colors.ON_SURFACE_VARIANT,
                ),
            ],
            spacing=8,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        )

    def _render_events(self) -> None:
        visible = self._event_rows if self._show_all else self._event_rows[:EVENT_LIMIT]
        controls: list[ft.Control] = list(visible)
        if len(self._event_rows) > EVENT_LIMIT:
            self._show_all_button.content = "Show fewer" if self._show_all else "Show all"
            controls.append(self._show_all_button)
        else:
            self._show_all_button.content = "Show all"
        self._events_list.controls = controls

    def _events_caption(self) -> str:
        total = len(self._event_rows)
        if not total:
            return "No scheduled events"
        visible = total if self._show_all else min(EVENT_LIMIT, total)
        return f"All {total}" if visible == total else f"Next {visible} of {total}"

    def _ending_card(self) -> ft.Column:
        header = ft.Row(
            [
                ft.Text("Account", size=fs(12), width=80, color=ft.Colors.ON_SURFACE_VARIANT),
                *[
                    ft.Text(
                        f"{days}d",
                        size=fs(12),
                        width=68,
                        text_align=ft.TextAlign.RIGHT,
                        color=ft.Colors.ON_SURFACE_VARIANT,
                    )
                    for days in HORIZONS
                ],
            ],
            spacing=8,
        )
        rows: list[ft.Row] = [header]
        endings = self._forecast.endings_by_account if self._forecast else {}
        for account_id, balances in sorted(
            endings.items(), key=lambda item: self._account_names.get(item[0], "")
        ):
            rows.append(
                ft.Row(
                    [
                        ft.Text(
                            self._account_names.get(account_id, "Account"),
                            size=fs(13),
                            width=80,
                            max_lines=1,
                            overflow=ft.TextOverflow.ELLIPSIS,
                        ),
                        *[
                            ft.Text(
                                format_money(balance),
                                size=fs(12),
                                width=68,
                                text_align=ft.TextAlign.RIGHT,
                            )
                            for balance in balances
                        ],
                    ],
                    spacing=8,
                )
            )
        self._ending_rows = rows
        return ft.Column(
            [
                section_title(
                    "Ending balance",
                    ft.Text("By account", size=fs(12), color=ft.Colors.ON_SURFACE_VARIANT),
                ),
                *rows,
            ],
            spacing=6,
        )

    def _assumptions(self) -> ft.Container:
        return ft.Container(
            content=ft.Row(
                [
                    ft.Container(
                        width=2,
                        height=44,
                        bgcolor=SEED_COLOR,
                        border_radius=1,
                    ),
                    ft.Text(
                        ASSUMPTIONS,
                        size=fs(12),
                        color=ft.Colors.ON_SURFACE_VARIANT,
                        expand=True,
                    ),
                ],
                spacing=10,
            ),
            padding=ft.Padding.symmetric(horizontal=12, vertical=10),
            border_radius=RADIUS,
            bgcolor=ft.Colors.with_opacity(0.04, SEED_COLOR),
        )

    # ---------- interactions ----------
    def _on_horizon(self, event: ft.Event[ft.SegmentedButton]) -> None:
        value = self._selected_value(event)
        try:
            horizon = int(value)
        except (TypeError, ValueError):
            return
        if horizon not in HORIZONS or horizon == self.horizon:
            return
        self.horizon = horizon
        self._show_all = False
        self.refresh()
        self.ctx.page.update()

    def _toggle_show_all(self) -> None:
        self._show_all = not self._show_all
        # The caption lives beside the list, so rebuild the whole card.
        self._events_card_container.content = self._events_card()
        self.ctx.page.update()

    def _open_income(self) -> None:
        self.on_income()

    def _open_income_dialog(self) -> None:
        # The dialog calls ``ctx.data_changed()`` after a successful change; the Year view
        # refreshes this panel when it is the visible mode.
        self.income_dialog = IncomePlanDialog(self.ctx)
        self.income_dialog.open()

    # ---------- helpers ----------
    @staticmethod
    def _selected_value(event) -> str | None:
        control = getattr(event, "control", event)
        selected = getattr(control, "selected", None)
        if isinstance(selected, str):
            return selected
        if selected:
            try:
                return next(iter(selected))
            except TypeError:
                return None
        return None

    @staticmethod
    def _forecast_today(forecast: CashFlowForecast) -> date:
        if forecast.points:
            return forecast.points[0].date
        return date.today()

    @staticmethod
    def _subtitle_text(today: date) -> str:
        return f"Checking, savings, and cash · starting {_date_label(today)}, {today.year}."

    def _liquid_accounts_caption(self) -> str:
        count = len(self._forecast.endings_by_account) if self._forecast else 0
        return f"{count} liquid account" if count == 1 else f"{count} liquid accounts"

    def _load_account_names(self) -> dict[int, str]:
        try:
            accounts = self.ctx.repo.list_accounts()
        except AttributeError:  # pragma: no cover - defensive for minimal contexts
            return {}
        return {
            account.id: account.name for account in accounts if account.id is not None
        }

    def _chart_colors(self) -> ChartColors:
        mode = getattr(self.ctx.page, "theme_mode", None)
        dark = mode.lower().endswith("dark") if isinstance(mode, str) else mode == ft.ThemeMode.DARK
        get_setting = getattr(self.ctx.repo, "get_setting", None)
        preset = "indigo"
        if get_setting is not None:
            try:
                preset = get_setting(THEME_PRESET_SETTING) or "indigo"
            except Exception:  # pragma: no cover - a missing setting must not break rendering
                preset = "indigo"
        scheme = build_theme(dark=dark, preset=preset).color_scheme
        return ChartColors(
            primary=scheme.primary,
            grid=scheme.outline_variant,
            text=scheme.on_surface,
            zero=scheme.outline,
            lowest=scheme.secondary,
            negative=scheme.error,
        )

    @staticmethod
    def _png_source(png: bytes) -> str:
        return "data:image/png;base64," + base64.b64encode(png).decode("ascii")


class IncomePlanDialog:
    """Combined income schedule list and inline editor, per the cash-flow mockup.

    A schedule is a forecast: saving or deleting one never writes to the ledger. The dialog
    owns its own list, so it reads the service on construction and closes after a successful
    change; ``on_close`` fires exactly once so the cash-flow panel can refresh.
    """

    def __init__(
        self,
        ctx: AppContext,
        *,
        on_close: Callable[[], None] | None = None,
    ) -> None:
        self.ctx = ctx
        self.on_close = on_close
        self.today = date.today()
        self._closed = False
        self._saving = False
        self._editing: IncomeSchedule | None = None
        self._anchor_auto = True
        self.preview_dates: list[date] = []
        self.accounts = [
            account
            for account in ctx.repo.list_accounts()
            if account.kind in LIQUID_ACCOUNT_KINDS
        ]
        self._account_by_id = {
            account.id: account for account in self.accounts if account.id is not None
        }
        self.schedules = ctx.service.list_income_schedules()
        default_id = self._default_account_id()

        # ---------- list ----------
        self.add_button = ft.FilledButton(
            content="Add income", icon=ft.Icons.ADD, on_click=self._start_add
        )
        self.list_column = ft.Column(spacing=4)
        self._render_list()

        # ---------- inline editor ----------
        self.editor_title = ft.Text("New income", size=fs(14), weight=ft.FontWeight.W_600)
        self.name = ft.TextField(
            label="Name",
            hint_text="e.g. Payroll",
            max_length=60,
            expand=True,
        )
        self.amount = ft.TextField(
            label="Amount",
            prefix_icon=ft.Icons.ATTACH_MONEY,
            hint_text="0.00",
            keyboard_type=ft.KeyboardType.NUMBER,
            expand=True,
        )
        self.frequency = ft.Dropdown(
            label="Frequency",
            options=[
                ft.DropdownOption(key=frequency.value, text=frequency.label)
                for frequency in IncomeFrequency
            ],
            value=IncomeFrequency.MONTHLY.value,
            on_select=self._frequency_changed,
            expand=True,
        )
        self.next_date = date_field(
            ctx.page,
            "Next deposit",
            self.today,
            on_change=self._next_date_changed,
            expand=True,
        )
        self.anchor = ft.TextField(
            label="Anchor day",
            hint_text="1–31",
            keyboard_type=ft.KeyboardType.NUMBER,
            on_change=self._anchor_changed,
            expand=True,
        )
        self.second_day = ft.TextField(
            label="Second day",
            hint_text="1–31",
            keyboard_type=ft.KeyboardType.NUMBER,
            on_change=self._second_day_changed,
            expand=True,
        )
        self.account = ft.Dropdown(
            label="Deposit to",
            options=[
                ft.DropdownOption(
                    key=str(account.id), text=f"{account.name} ({account.kind.label})"
                )
                for account in self.accounts
            ],
            value=str(default_id) if default_id is not None else None,
            on_select=self._account_changed,
        )
        self.active = ft.Switch(label="Active · include in forecast", value=True)
        self.delete_button = ft.TextButton(
            content="Delete",
            icon=ft.Icons.DELETE_OUTLINE,
            icon_color=EXPENSE_COLOR,
            on_click=self._confirm_delete,
            visible=False,
        )
        self.error = ft.Text("", size=fs(12), color=EXPENSE_COLOR, visible=False)
        self.preview_dates_text = ft.Text("", size=fs(12), color=ft.Colors.ON_SURFACE_VARIANT)
        self.preview_note = ft.Text(
            "", size=fs(11), color=ft.Colors.ON_SURFACE_VARIANT
        )
        self.preview = ft.Container(
            content=ft.Column(
                [self.preview_dates_text, self.preview_note], spacing=2
            ),
            padding=ft.Padding.symmetric(horizontal=10, vertical=8),
            border_radius=RADIUS,
            bgcolor=ft.Colors.with_opacity(0.08, WARNING_COLOR),
        )
        self.editor = ft.Column(
            [
                self.editor_title,
                ft.Row([self.name, self.amount], spacing=12),
                ft.Row([self.frequency, self.next_date], spacing=12),
                ft.Row([self.anchor, self.second_day], spacing=12),
                self.account,
                ft.Row(
                    [self.active, self.delete_button],
                    alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                ),
                self.preview,
                self.error,
            ],
            spacing=12,
            visible=False,
        )

        self.save_button = ft.FilledButton(
            content="Save income",
            icon=ft.Icons.CHECK,
            on_click=self._save,
            visible=False,
        )
        self.dialog = ft.AlertDialog(
            modal=True,
            scrollable=True,
            title=ft.Row(
                [
                    ft.Column(
                        [
                            ft.Text("Income plan", size=fs(18), weight=ft.FontWeight.W_600),
                            ft.Text(
                                "Planned deposits · used only for forecasts",
                                size=fs(12),
                                color=ft.Colors.ON_SURFACE_VARIANT,
                            ),
                        ],
                        spacing=2,
                        expand=True,
                    ),
                    self.add_button,
                ],
                spacing=8,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
            content=ft.Container(
                width=INCOME_DIALOG_WIDTH,
                content=ft.Column(
                    [
                        self.list_column,
                        ft.Divider(height=1),
                        self.editor,
                    ],
                    spacing=12,
                    tight=True,
                    scroll=ft.ScrollMode.AUTO,
                    horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
                ),
            ),
            actions=[
                ft.TextButton(content="Cancel", on_click=self._cancel),
                self.save_button,
            ],
            actions_alignment=ft.MainAxisAlignment.END,
        )
        self._update_visibility()

    # ---------- list ----------
    def _render_list(self) -> None:
        if not self.schedules:
            self.list_column.controls = [
                ft.Text(
                    "No income planned yet. Add income to make this forecast realistic.",
                    size=fs(13),
                    color=ft.Colors.ON_SURFACE_VARIANT,
                )
            ]
            return
        self.list_column.controls = [
            self._schedule_row(schedule) for schedule in self.schedules
        ]

    def _schedule_row(self, schedule: IncomeSchedule) -> ft.Row:
        parts = [schedule.frequency.label]
        if schedule.frequency in (
            IncomeFrequency.MONTHLY,
            IncomeFrequency.SEMIMONTHLY,
        ):
            parts.append(f"on the {_ordinal(schedule.anchor_day)}")
            if (
                schedule.frequency is IncomeFrequency.SEMIMONTHLY
                and schedule.second_day is not None
            ):
                parts.append(f"and the {_ordinal(schedule.second_day)}")
        account = self._account_by_id.get(schedule.account_id)
        if account is not None:
            parts.append(f"into {account.name}")
        next_day = self._next_occurrence(schedule)
        parts.append(
            f"next {_date_label(next_day)}" if next_day is not None else "ended"
        )

        chip_color = INCOME_COLOR if schedule.active else ft.Colors.ON_SURFACE_VARIANT
        chip = ft.Container(
            content=ft.Text(
                "Active" if schedule.active else "Inactive",
                size=fs(11),
                weight=ft.FontWeight.W_600,
                color=chip_color,
            ),
            bgcolor=ft.Colors.with_opacity(0.12, chip_color),
            border_radius=10,
            padding=ft.Padding.symmetric(horizontal=8, vertical=2),
        )
        return ft.Row(
            [
                ft.Column(
                    [
                        ft.Text(
                            schedule.name,
                            size=fs(14),
                            weight=ft.FontWeight.W_600,
                            max_lines=1,
                            overflow=ft.TextOverflow.ELLIPSIS,
                        ),
                        ft.Text(
                            " · ".join(parts),
                            size=fs(11),
                            color=ft.Colors.ON_SURFACE_VARIANT,
                            max_lines=1,
                            overflow=ft.TextOverflow.ELLIPSIS,
                        ),
                    ],
                    spacing=0,
                    expand=True,
                ),
                ft.Text(format_money(schedule.amount_cents), size=fs(13)),
                chip,
                ft.TextButton(
                    content="Edit",
                    on_click=lambda _, current=schedule: self._start_edit(current),
                ),
            ],
            spacing=8,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        )

    def _next_occurrence(self, schedule: IncomeSchedule) -> date | None:
        start = self.today + timedelta(days=1)
        end = max(start, schedule.next_date) + timedelta(days=_PREVIEW_HORIZON)
        dates = income_dates(schedule, start, end)
        return dates[0] if dates else None

    # ---------- editor lifecycle ----------
    def _start_add(self, _: ft.Event | None = None) -> None:
        self._editing = None
        self.editor_title.value = "New income"
        self.name.value = ""
        self.amount.value = ""
        self.frequency.value = IncomeFrequency.MONTHLY.value
        self.next_date.value = self.today.isoformat()
        self.anchor.value = str(self.today.day)
        self.second_day.value = ""
        default_id = self._default_account_id()
        self.account.value = str(default_id) if default_id is not None else None
        self.active.value = True
        self.delete_button.visible = False
        self._anchor_auto = True
        self._clear_errors()
        self._update_visibility()
        self._update_preview()
        self.editor.visible = True
        self.save_button.visible = True
        self._update_dialog()

    def _start_edit(self, schedule: IncomeSchedule) -> None:
        self._editing = schedule
        self.editor_title.value = f"Edit {schedule.name}"
        self.name.value = schedule.name
        self.amount.value = f"{schedule.amount_cents / 100:.2f}"
        self.frequency.value = schedule.frequency.value
        self.next_date.value = schedule.next_date.isoformat()
        self.anchor.value = str(schedule.anchor_day)
        self.second_day.value = (
            str(schedule.second_day) if schedule.second_day is not None else ""
        )
        self.account.value = (
            str(schedule.account_id) if schedule.account_id in self._account_by_id else None
        )
        self.active.value = schedule.active
        self.delete_button.visible = True
        self._anchor_auto = False
        self._clear_errors()
        self._update_visibility()
        self._update_preview()
        self.editor.visible = True
        self.save_button.visible = True
        self._update_dialog()

    # ---------- draft / preview ----------
    def _frequency_value(self) -> IncomeFrequency | None:
        try:
            return IncomeFrequency(self.frequency.value)
        except (TypeError, ValueError):
            return None

    def _parsed_next_date(self) -> date | None:
        try:
            return parse_date(self.next_date.value)
        except ValueError:
            return None

    @staticmethod
    def _parse_int(value) -> int | None:
        try:
            return int(str(value).strip())
        except (TypeError, ValueError):
            return None

    @classmethod
    def _parse_day(cls, value) -> int | None:
        day = cls._parse_int(value)
        return day if day is not None and 1 <= day <= 31 else None

    def _draft_for_preview(self) -> IncomeSchedule | None:
        frequency = self._frequency_value()
        next_date = self._parsed_next_date()
        if frequency is None or next_date is None:
            return None
        anchor = self._parse_day(self.anchor.value) if self.anchor.visible else next_date.day
        if anchor is None:
            return None
        second = None
        if frequency is IncomeFrequency.SEMIMONTHLY:
            second = self._parse_day(self.second_day.value)
            if second is None or second == anchor:
                return None
        return IncomeSchedule(
            name="",
            amount_cents=1,
            frequency=frequency,
            next_date=next_date,
            anchor_day=anchor,
            account_id=0,
            second_day=second,
        )

    def _update_preview(self) -> None:
        draft = self._draft_for_preview()
        self.preview_dates = []
        if draft is None:
            self.preview_dates_text.value = "Next dates · complete the schedule to preview."
        else:
            start = self.today + timedelta(days=1)
            end = max(start, draft.next_date) + timedelta(days=_PREVIEW_HORIZON)
            self.preview_dates = income_dates(draft, start, end)[:3]
            if self.preview_dates:
                labels = " · ".join(_date_label(day) for day in self.preview_dates)
                self.preview_dates_text.value = f"Next dates · {labels}"
            else:
                self.preview_dates_text.value = "Next dates · no upcoming deposits"
        account = self._account_by_id.get(self._parse_int(self.account.value))
        account_name = account.name if account is not None else "the selected account"
        self.preview_note.value = f"Deposits into {account_name}. {PREVIEW_NOTE}"

    def _update_visibility(self) -> None:
        frequency = self._frequency_value()
        self.anchor.visible = frequency in (
            IncomeFrequency.MONTHLY,
            IncomeFrequency.SEMIMONTHLY,
        )
        self.second_day.visible = frequency is IncomeFrequency.SEMIMONTHLY

    # ---------- events ----------
    def _frequency_changed(self, _: ft.Event | None = None) -> None:
        self._update_visibility()
        if self._anchor_auto:
            next_date = self._parsed_next_date()
            if next_date is not None:
                self.anchor.value = str(next_date.day)
        self._update_preview()
        self._update_dialog()

    def _next_date_changed(self, _: ft.Event | None = None) -> None:
        if self._anchor_auto:
            next_date = self._parsed_next_date()
            if next_date is not None:
                self.anchor.value = str(next_date.day)
        self._update_preview()
        self._update_dialog()

    def _anchor_changed(self, _: ft.Event | None = None) -> None:
        self._anchor_auto = False
        self._update_preview()
        self._update_dialog()

    def _second_day_changed(self, _: ft.Event | None = None) -> None:
        self._update_preview()
        self._update_dialog()

    def _account_changed(self, _: ft.Event | None = None) -> None:
        self._update_preview()
        self._update_dialog()

    def _clear_errors(self) -> None:
        for field in (self.name, self.amount, self.anchor, self.second_day, self.next_date):
            field.error = None
        for field in (self.frequency, self.account):
            field.error_text = None
        self.error.value = ""
        self.error.visible = False

    def _validate(self) -> IncomeSchedule | None:
        self._clear_errors()
        valid = True

        name = (self.name.value or "").strip()
        if not name:
            self.name.error = "Name is required"
            valid = False
        elif len(name) > 60:
            self.name.error = "Name must be 60 characters or fewer"
            valid = False

        try:
            amount = parse_money(self.amount.value)
        except ValueError as exc:
            self.amount.error = str(exc)
            valid = False
            amount = 0

        frequency = self._frequency_value()
        if frequency is None:
            self.frequency.error_text = "Choose a valid income frequency"
            valid = False

        try:
            next_date = parse_date(self.next_date.value)
        except ValueError as exc:
            self.next_date.error = str(exc)
            valid = False
            next_date = None

        anchor = self._parse_day(self.anchor.value)
        if self.anchor.visible:
            if anchor is None:
                self.anchor.error = "Enter a day between 1 and 31"
                valid = False
        elif next_date is not None:
            # Weekly/biweekly schedules still store an anchor; the next date supplies it.
            anchor = next_date.day
        else:
            anchor = self.today.day

        second = None
        if frequency is IncomeFrequency.SEMIMONTHLY:
            second = self._parse_day(self.second_day.value)
            if second is None:
                self.second_day.error = "Enter a day between 1 and 31"
                valid = False
            elif anchor is not None and second == anchor:
                self.second_day.error = "The two income days must be different"
                valid = False

        account_id = self._parse_int(self.account.value)
        if account_id is None:
            self.account.error_text = "Select a deposit account"
            valid = False
        elif account_id not in self._account_by_id:
            self.account.error_text = (
                "Income must be deposited into a checking, savings or cash account"
            )
            valid = False

        if frequency is not None and next_date is not None and anchor is not None:
            if frequency is IncomeFrequency.MONTHLY and next_date.day != clamped_day(next_date, anchor):
                self.next_date.error = "The next date must fall on the anchor day"
                valid = False
            elif frequency is IncomeFrequency.SEMIMONTHLY:
                allowed = {clamped_day(next_date, anchor)}
                if second is not None:
                    allowed.add(clamped_day(next_date, second))
                if next_date.day not in allowed:
                    self.next_date.error = "The next date must fall on one of the income days"
                    valid = False

        if not valid:
            self._update_dialog()
            return None
        return IncomeSchedule(
            name=name,
            amount_cents=amount,
            frequency=frequency,
            next_date=next_date,
            anchor_day=anchor,
            account_id=account_id,
            second_day=second if frequency is IncomeFrequency.SEMIMONTHLY else None,
            active=bool(self.active.value),
            id=self._editing.id if self._editing is not None else None,
        )

    # ---------- persistence ----------
    def _save(self, _: ft.Event | None = None) -> None:
        # A hidden editor has nothing to save; a second click must not insert a duplicate, and
        # a closed dialog must never write again.
        if self._closed or self._saving or not self.editor.visible:
            return
        schedule = self._validate()
        if schedule is None:
            return
        self._saving = True
        self.save_button.disabled = True
        failure: str | None = None
        try:
            if self._editing is not None and self._editing.id is not None:
                self.ctx.service.update_income_schedule(schedule)
            else:
                self.ctx.service.add_income_schedule(schedule)
        except RepositoryError as exc:
            failure = str(exc)
        finally:
            self._saving = False
            self.save_button.disabled = False
        if failure is not None:
            self.error.value = failure
            self.error.visible = True
            self._update_dialog()
            return
        self.close(changed=True)

    def _confirm_delete(self, _: ft.Event | None = None) -> None:
        schedule = self._editing
        if schedule is None or schedule.id is None:
            return
        confirm_dialog(
            self.ctx.page,
            "Delete income",
            f"Delete '{schedule.name}'? Recorded deposits are not affected.",
            lambda: self._delete(schedule),
        )

    def _delete(self, schedule: IncomeSchedule) -> None:
        try:
            self.ctx.service.delete_income_schedule(schedule.id)
        except RepositoryError as exc:
            self.error.value = str(exc)
            self.error.visible = True
            self._update_dialog()
            return
        self.close(changed=True)

    # ---------- lifecycle ----------
    def open(self) -> None:
        self.ctx.page.show_dialog(self.dialog)

    def _cancel(self, _: ft.Event | None = None) -> None:
        self.close()

    def close(self, *, changed: bool = False) -> None:
        """Dismiss the dialog once and, after a successful change, notify every view.

        ``data_changed`` refreshes the accounts list (which names the in-use schedule) and the
        cash-flow panel, and updates the page exactly once.
        """
        if self._closed:
            return
        self._closed = True
        self.ctx.page.pop_dialog()
        if self.on_close is not None:
            self.on_close()
        if changed:
            self.ctx.data_changed()
        else:
            self.ctx.page.update()

    def _default_account_id(self) -> int | None:
        checking = next(
            (account for account in self.accounts if account.kind is AccountKind.CHECKING),
            None,
        )
        chosen = checking or (self.accounts[0] if self.accounts else None)
        return chosen.id if chosen is not None else None

    def _update_dialog(self) -> None:
        try:
            self.dialog.update()
        except RuntimeError:
            # Direct tests build the dialog without a mounted page; the values still apply.
            pass
