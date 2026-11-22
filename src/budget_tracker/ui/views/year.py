"""Year view: monthly budget performance, cash flow and a year-end pace projection."""

from __future__ import annotations

import base64
from collections.abc import Callable
from datetime import date

import flet as ft

from ...formatting import format_money, month_label, short_month_label
from ...services import YearMonth, YearOverview
from ..charts import ChartColors, CumulativeMonth, cumulative_chart_png
from ..components import card, empty_state, section_title, summary_tile
from ..context import AppContext
from ..theme import (
    EXPENSE_COLOR,
    GAP,
    INCOME_COLOR,
    SEED_COLOR,
    THEME_PRESET_SETTING,
    WARNING_COLOR,
    build_theme,
    fs,
    readable,
    text_scale,
)
from .cash_flow import CashFlowPanel

CHART_HEIGHT = 260
COVERAGE_MIN_MONTHS = 3
FOCUS_CATEGORY_LIMIT = 3

# The chart is rendered at the card's logical width so BoxFit.CONTAIN neither stretches nor
# letterboxes the baked labels. Flet resolves ``lg`` from the *page* width (992px), so the span
# switch follows the page width even though the card lives inside the narrower content area. The
# page content width is the window minus the navigation rail, the divider and the page padding.
DEFAULT_PAGE_WIDTH = 1320
RAIL_WIDTH = 120
PAGE_PADDING = 64
DIVIDER_WIDTH = 1
CARD_PADDING = 40
LG_BREAKPOINT = 992
CHART_WIDE_COLUMNS = 8
MIN_CHART_WIDTH = 420


def _months_label(count: int) -> str:
    return "1 month" if count == 1 else f"{count} months"


def _elapsed_label(value: float) -> str:
    """Describe a fractional month count without turning a whole month into ``1.0 months``."""
    if abs(value - round(value)) < 0.05:
        return _months_label(round(value))
    return f"{value:.1f} months"


class YearView:
    def __init__(
        self,
        ctx: AppContext,
        *,
        on_view_budgets: Callable[[], None] | None = None,
    ) -> None:
        self.ctx = ctx
        self.on_view_budgets = on_view_budgets
        self._show_all_months = False
        self._chart_image: ft.Container | None = None
        self._focus_card: ft.Container | None = None
        years = ctx.service.available_years()
        current = date.today().year
        self.year = ft.Dropdown(
            label="Year",
            width=130,
            dense=True,
            options=[ft.DropdownOption(key=str(year), text=str(year)) for year in reversed(years)],
            value=str(current if current in years else years[-1]),
            on_select=lambda _: self.refresh(),
        )
        self.mode = "year"
        self.mode_switch = ft.SegmentedButton(
            segments=[
                ft.Segment(value="year", label=ft.Text("This year")),
                ft.Segment(value="cash", label=ft.Text("Next 90 days")),
            ],
            selected=["year"],
            allow_empty_selection=False,
            allow_multiple_selection=False,
            on_change=self._switch_mode,
        )
        self.cash_panel = CashFlowPanel(ctx)
        self.body = ft.Column(spacing=GAP)
        self._title_block = ft.Column(
            [
                ft.Text("Year", size=fs(28), weight=ft.FontWeight.BOLD),
                ft.Text(
                    "Budget performance and cash flow across the year",
                    size=fs(14),
                    color=ft.Colors.ON_SURFACE_VARIANT,
                ),
            ],
            spacing=2,
        )
        self._toolbar = ft.Container(
            ft.Row(
                [self.year, self.mode_switch],
                spacing=8,
                wrap=True,
                alignment=ft.MainAxisAlignment.END,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
            col={"xs": 12, "sm": 12, "md": 12, "lg": 6},
        )
        self._header = ft.ResponsiveRow(
            [
                ft.Container(
                    self._title_block,
                    col={"xs": 12, "sm": 12, "md": 12, "lg": 6},
                ),
                self._toolbar,
            ],
            spacing=GAP,
            run_spacing=8,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        )
        self.control = ft.Column(
            [self._header, self.body],
            spacing=GAP,
            scroll=ft.ScrollMode.AUTO,
            expand=True,
        )
        ctx.on_data_changed(self.refresh)
        self.refresh()

    def _switch_mode(self, event: ft.Event[ft.SegmentedButton]) -> None:
        selected = event.control.selected
        if isinstance(selected, str):
            value = selected
        else:
            try:
                value = next(iter(selected or ()))
            except TypeError:
                value = None
        self.mode = "cash" if value == "cash" else "year"
        if self.mode == "cash":
            self.year.visible = False
            self.body.controls = [self.cash_panel.control]
            self.cash_panel.refresh()
            self.ctx.page.update()
            return
        self.refresh()

    def refresh(self) -> None:
        if self.mode == "cash":
            self.year.visible = False
            self.body.controls = [self.cash_panel.control]
            # Only the visible panel is rebuilt; while the yearly table is shown, a data change
            # must not run the forecast and Pillow chart for an unmounted panel.
            self.cash_panel.refresh()
            self.ctx.page.update()
            return
        self.year.visible = True
        overview = self.ctx.service.year_overview(int(self.year.value), through=date.today())
        if not overview.has_data:
            self.body.controls = [
                card(
                    empty_state(
                        ft.Icons.CALENDAR_MONTH_OUTLINED,
                        f"No data for {overview.year}",
                        "Add transactions and budgets, and this page will show monthly budget "
                        "performance, cash flow and a year-end projection.",
                    ),
                    col=12,
                )
            ]
        else:
            controls: list[ft.Control] = [self._kpis(overview)]
            banner = self._coverage_banner(overview)
            if banner is not None:
                controls.append(banner)
            self._focus_card = card(self._focus_card_content(overview), col={"xs": 12, "lg": 4})
            chart_card = card(self._chart(overview), col={"xs": 12, "lg": 8})
            controls.append(
                ft.ResponsiveRow(
                    [chart_card, self._focus_card],
                    spacing=GAP,
                    run_spacing=GAP,
                )
            )
            controls.append(card(self._table(overview), col={"xs": 12}))
            self.body.controls = controls
        self.ctx.page.update()

    # ---------- top row ----------
    def _kpis(self, overview: YearOverview) -> ft.ResponsiveRow:
        difference = overview.ytd_difference_cents
        under = difference >= 0
        if overview.prior_spending_cents is None:
            comparison = "No prior-year data"
        else:
            delta = overview.ytd_spending_cents - overview.prior_spending_cents
            percent = (
                f" ({delta / overview.prior_spending_cents:+.0%})"
                if overview.prior_spending_cents
                else ""
            )
            comparison = f"vs. last year {format_money(delta, signed=True)}{percent}"
        if overview.forecast_spending_cents is None:
            forecast_title = "Year total"
            forecast_value = format_money(overview.ytd_spending_cents)
            forecast_caption = "Full year"
        else:
            forecast_title = "Year-end pace"
            forecast_value = format_money(overview.forecast_spending_cents)
            forecast_caption = f"Estimate · {_elapsed_label(overview.elapsed_fraction)} elapsed"
        return ft.ResponsiveRow(
            [
                summary_tile(
                    "YTD budget",
                    format_money(overview.ytd_budget_cents),
                    f"{_months_label(overview.budgeted_months)} budgeted",
                    SEED_COLOR,
                ),
                summary_tile(
                    "YTD spending",
                    format_money(overview.ytd_spending_cents),
                    comparison,
                    EXPENSE_COLOR,
                ),
                summary_tile(
                    "Under budget" if under else "Over budget",
                    format_money(abs(difference)),
                    "YTD budget minus spending",
                    INCOME_COLOR if under else EXPENSE_COLOR,
                ),
                summary_tile(
                    forecast_title,
                    forecast_value,
                    forecast_caption,
                    SEED_COLOR,
                ),
            ],
            spacing=GAP,
            run_spacing=GAP,
        )

    # ---------- coverage ----------
    def _coverage_banner(self, overview: YearOverview) -> ft.Container | None:
        # Count the whole displayed year, not just the elapsed months: a future year can be
        # fully budgeted while its YTD figure is zero, and it must not claim "0 of 12".
        count = sum(1 for month in overview.months if month.has_budget)
        if count >= COVERAGE_MIN_MONTHS:
            return None
        if count == 0:
            message = "0 of 12 months have a budget."
        elif count == 1:
            message = "Only 1 of 12 months has a budget."
        else:
            message = f"Only {count} of 12 months have a budget."
        # Only the run-rate projection is an estimate; a completed or future year has none.
        if overview.forecast_spending_cents is not None:
            message += " The year-end pace is an estimate."
        banner = card(
            ft.Row(
                [
                    ft.Icon(ft.Icons.INFO_OUTLINE, color=WARNING_COLOR, size=fs(20)),
                    ft.Text(message, size=fs(13), expand=True),
                    ft.TextButton(
                        content="Review budgets",
                        icon=ft.Icons.TRACK_CHANGES_OUTLINED,
                        on_click=lambda _: self._review_budgets(),
                    ),
                ],
                spacing=10,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
            col={"xs": 12},
        )
        banner.data = "coverage-banner"
        return banner

    def _review_budgets(self) -> None:
        if self.on_view_budgets is not None:
            self.on_view_budgets()

    # ---------- chart ----------
    def _chart(self, overview: YearOverview) -> ft.Column:
        months = self._cumulative_months(overview)
        colors = self._chart_colors()
        # Match the renderer exactly: it uses the cumulative comparison, not each month's.
        over_budget = self._cumulative_over_budget(months)
        spending_color = colors.negative if over_budget else colors.lowest
        self._chart_image = ft.Container(
            height=CHART_HEIGHT,
            expand=True,
            image=ft.DecorationImage(
                src=self._png_source(
                    cumulative_chart_png(
                        months,
                        width=self._chart_width(),
                        height=CHART_HEIGHT,
                        colors=colors,
                        text_scale=text_scale(),
                    )
                ),
                fit=ft.BoxFit.CONTAIN,
            ),
            content=self._chart_hover_overlay(months),
        )

        def legend(color: str, text: str) -> ft.Row:
            return ft.Row(
                [
                    ft.Container(width=10, height=10, border_radius=2, bgcolor=color),
                    ft.Text(text, size=fs(12)),
                ],
                spacing=6,
            )

        return ft.Column(
            [
                section_title(
                    "Budget vs actual, cumulative",
                    ft.Row(
                        [legend(colors.primary, "Budget"), legend(spending_color, "Spending")],
                        spacing=12,
                    ),
                ),
                ft.Row([self._chart_image], spacing=0),
                ft.Text(
                    "Only months with activity change the line.",
                    size=fs(12),
                    color=ft.Colors.ON_SURFACE_VARIANT,
                ),
                ft.Text(
                    "Hover a month to see its cumulative budget and spending.",
                    size=fs(12),
                    color=ft.Colors.ON_SURFACE_VARIANT,
                ),
            ],
            spacing=8,
        )

    @staticmethod
    def _chart_hover_overlay(months: list[CumulativeMonth]) -> ft.Row:
        """Transparent month-wide bands whose tooltips carry the plotted cumulative values."""
        columns: list[ft.Control] = []
        running_budget = 0
        running_spending = 0
        for month in months:
            running_budget += month.budget_cents
            running_spending += month.spending_cents
            progress = " · in progress" if month.is_current else ""
            columns.append(
                ft.Container(
                    expand=True,
                    height=CHART_HEIGHT,
                    bgcolor=ft.Colors.TRANSPARENT,
                    tooltip=(
                        f"{month.label}{progress}\n"
                        f"Cumulative budget {format_money(running_budget)}\n"
                        f"Cumulative spending {format_money(running_spending)}"
                    ),
                )
            )
        return ft.Row(columns, spacing=0, expand=True)

    def _cumulative_months(self, overview: YearOverview) -> list[CumulativeMonth]:
        current = self.ctx.service.current_month()
        months: list[CumulativeMonth] = []
        for month in overview.months:
            # The renderer accumulates; pass the month's own values, not a running total.
            months.append(
                CumulativeMonth(
                    short_month_label(month.month),
                    month.budget_cents,
                    month.spending_cents,
                    month.month == current,
                )
            )
        return months

    @staticmethod
    def _cumulative_over_budget(months: list[CumulativeMonth]) -> bool:
        """The renderer's own rule: any cumulative spending point above cumulative budget."""
        running_budget = 0
        running_spending = 0
        for month in months:
            running_budget += month.budget_cents
            running_spending += month.spending_cents
            if running_spending > running_budget:
                return True
        return False

    # ---------- focus card ----------
    @staticmethod
    def _has_activity(month: YearMonth) -> bool:
        return bool(month.has_budget or month.spending_cents or month.income_cents)

    def _focus_month(self, overview: YearOverview) -> YearMonth | None:
        current_key = self.ctx.service.current_month()
        # Only months that have already happened (or are happening) can be "latest activity".
        eligible = [month for month in overview.months if month.month <= current_key]
        current = next((month for month in eligible if month.month == current_key), None)
        if current is not None and self._has_activity(current):
            return current
        # Real spending or income is activity; a budget-only month is only a last resort.
        for month in reversed(eligible):
            if month.spending_cents or month.income_cents:
                return month
        for month in reversed(eligible):
            if month.has_budget:
                return month
        return None

    def _focus_card_content(self, overview: YearOverview) -> ft.Column:
        month = self._focus_month(overview)
        if month is None:
            return ft.Column(
                [
                    section_title("This month"),
                    ft.Text(
                        "No activity yet in this year.",
                        size=fs(13),
                        color=ft.Colors.ON_SURFACE_VARIANT,
                    ),
                ],
                spacing=8,
            )

        current = self.ctx.service.current_month()
        if month.month == current:
            chip = "This month"
        elif month.spending_cents or month.income_cents:
            chip = "Latest activity"
        else:
            chip = "Latest budget"
        budget = month.budget_cents
        spent = month.spending_cents
        difference = month.difference_cents
        ratio = spent / budget if month.has_budget and budget else 0.0
        if not month.has_budget:
            state_text = "No budget set for this month."
            state_color = ft.Colors.ON_SURFACE_VARIANT
        elif difference >= 0:
            state_text = f"{format_money(difference)} under budget"
            state_color = INCOME_COLOR
        else:
            state_text = f"{format_money(-difference)} over budget"
            state_color = EXPENSE_COLOR

        summary = (
            f"Spent {format_money(spent)} of {format_money(budget)}"
            if month.has_budget
            else f"Spent {format_money(spent)}"
        )
        controls: list[ft.Control] = [
            ft.Row(
                [
                    ft.Text(month_label(month.month), size=fs(18), weight=ft.FontWeight.BOLD),
                    ft.Text(chip, size=fs(12), color=ft.Colors.ON_SURFACE_VARIANT),
                ],
                spacing=8,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
            ft.Text(summary, size=fs(13), color=ft.Colors.ON_SURFACE_VARIANT),
        ]
        if month.has_budget:
            controls.append(
                ft.ProgressBar(
                    value=min(max(ratio, 0.0), 1.0),
                    color=state_color,
                    bgcolor=ft.Colors.with_opacity(0.12, state_color),
                    bar_height=8,
                    border_radius=4,
                )
            )
        controls += [
            ft.Text(state_text, size=fs(13), color=state_color, weight=ft.FontWeight.W_600),
            ft.Divider(height=1),
            ft.Text("Top categories", size=fs(12), color=ft.Colors.ON_SURFACE_VARIANT),
        ]
        categories = self._focus_categories(month)
        if categories:
            controls.extend(self._category_row(*category) for category in categories)
        else:
            controls.append(
                ft.Text(
                    "No category activity yet.",
                    size=fs(13),
                    color=ft.Colors.ON_SURFACE_VARIANT,
                )
            )
        return ft.Column(controls, spacing=8)

    def _focus_categories(self, month: YearMonth) -> list[tuple[str, int, int | None, str]]:
        """Merge budgeted and unbudgeted spend, so the largest category always appears."""
        entries: dict[int, dict[str, object]] = {}
        for total in self.ctx.repo.totals_by_category(month.month, through=date.today()):
            entries[total.category_id] = {
                "name": total.name,
                "spent": total.total_cents,
                "limit": None,
                "color": total.color,
            }
        for progress in self.ctx.repo.budget_progress(month.month, through=date.today()):
            entry = entries.setdefault(
                progress.category_id,
                {
                    "name": progress.category_name,
                    "spent": progress.spent_cents,
                    "limit": progress.limit_cents,
                    "color": progress.category_color,
                },
            )
            entry.update(
                name=progress.category_name,
                spent=progress.spent_cents,
                limit=progress.limit_cents,
                color=progress.category_color,
            )
        ranked = sorted(entries.values(), key=lambda entry: entry["spent"], reverse=True)
        return [
            (
                str(entry["name"]),
                int(entry["spent"]),
                int(entry["limit"]) if entry["limit"] is not None else None,
                str(entry["color"]),
            )
            for entry in ranked[:FOCUS_CATEGORY_LIMIT]
        ]

    @staticmethod
    def _category_row(name: str, spent_cents: int, limit_cents: int | None, color: str) -> ft.Row:
        amount = (
            f"{format_money(spent_cents)} of {format_money(limit_cents)}"
            if limit_cents is not None
            else format_money(spent_cents)
        )
        return ft.Row(
            [
                ft.Container(
                    width=8,
                    height=8,
                    border_radius=4,
                    bgcolor=readable(color) or SEED_COLOR,
                ),
                ft.Text(
                    name,
                    size=fs(13),
                    expand=True,
                    max_lines=1,
                    overflow=ft.TextOverflow.ELLIPSIS,
                ),
                ft.Text(amount, size=fs(13), text_align=ft.TextAlign.RIGHT),
            ],
            spacing=8,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        )

    # ---------- table ----------
    def _visible_months(self, overview: YearOverview) -> list[YearMonth]:
        if self._show_all_months:
            return overview.months
        current = self.ctx.service.current_month()
        return [
            month
            for month in overview.months
            if month.has_budget
            or month.spending_cents
            or month.income_cents
            or month.month == current
        ]

    def _table(self, overview: YearOverview) -> ft.Column:
        header = ft.Row(
            [
                ft.Text("Month", size=fs(12), width=60, color=ft.Colors.ON_SURFACE_VARIANT),
                ft.Text("Budget", size=fs(12), width=92, text_align=ft.TextAlign.RIGHT, color=ft.Colors.ON_SURFACE_VARIANT),
                ft.Text("Spent", size=fs(12), width=92, text_align=ft.TextAlign.RIGHT, color=ft.Colors.ON_SURFACE_VARIANT),
                ft.Text("Difference", size=fs(12), width=100, text_align=ft.TextAlign.RIGHT, color=ft.Colors.ON_SURFACE_VARIANT),
                ft.Text("Income", size=fs(12), width=92, text_align=ft.TextAlign.RIGHT, color=ft.Colors.ON_SURFACE_VARIANT),
                ft.Text("Net", size=fs(12), width=92, text_align=ft.TextAlign.RIGHT, color=ft.Colors.ON_SURFACE_VARIANT),
            ],
            spacing=8,
        )
        rows: list[ft.Control] = [header]
        for month in self._visible_months(overview):
            rows.append(self._row(month))
        label = "Year total" if overview.year < date.today().year else "YTD"
        difference = overview.ytd_difference_cents
        rows.append(ft.Divider(height=1))
        rows.append(
            ft.Row(
                [
                    ft.Text(label, size=fs(13), width=60, weight=ft.FontWeight.W_600),
                    ft.Text(format_money(overview.ytd_budget_cents), size=fs(13), width=92, text_align=ft.TextAlign.RIGHT, weight=ft.FontWeight.W_600),
                    ft.Text(format_money(overview.ytd_spending_cents), size=fs(13), width=92, text_align=ft.TextAlign.RIGHT, weight=ft.FontWeight.W_600),
                    ft.Text(
                        format_money(difference, signed=True),
                        size=fs(13),
                        width=100,
                        text_align=ft.TextAlign.RIGHT,
                        weight=ft.FontWeight.W_600,
                        color=INCOME_COLOR if difference >= 0 else EXPENSE_COLOR,
                    ),
                    ft.Text(format_money(overview.ytd_income_cents), size=fs(13), width=92, text_align=ft.TextAlign.RIGHT, weight=ft.FontWeight.W_600),
                    ft.Text(
                        format_money(overview.ytd_income_cents - overview.ytd_spending_cents, signed=True),
                        size=fs(13),
                        width=92,
                        text_align=ft.TextAlign.RIGHT,
                        weight=ft.FontWeight.W_600,
                    ),
                ],
                spacing=8,
            )
        )
        self._month_toggle = ft.TextButton(
            "Show active months" if self._show_all_months else "Show all 12 months",
            on_click=lambda _: self._toggle_months(),
        )
        return ft.Column(
            [section_title("Monthly detail"), *rows, self._month_toggle],
            spacing=4,
        )

    def _toggle_months(self) -> None:
        self._show_all_months = not self._show_all_months
        self.refresh()

    @staticmethod
    def _row(month: YearMonth) -> ft.Row:
        budget = format_money(month.budget_cents) if month.has_budget else "—"
        difference = format_money(month.difference_cents, signed=True) if month.has_budget else "—"
        difference_color = (
            INCOME_COLOR if not month.has_budget or month.difference_cents >= 0 else EXPENSE_COLOR
        )
        return ft.Row(
            [
                ft.Text(short_month_label(month.month), size=fs(13), width=60),
                ft.Text(budget, size=fs(13), width=92, text_align=ft.TextAlign.RIGHT),
                ft.Text(format_money(month.spending_cents), size=fs(13), width=92, text_align=ft.TextAlign.RIGHT),
                ft.Text(difference, size=fs(13), width=100, text_align=ft.TextAlign.RIGHT, color=difference_color),
                ft.Text(format_money(month.income_cents), size=fs(13), width=92, text_align=ft.TextAlign.RIGHT),
                ft.Text(
                    format_money(month.net_cents, signed=True),
                    size=fs(13),
                    width=92,
                    text_align=ft.TextAlign.RIGHT,
                    color=INCOME_COLOR if month.net_cents >= 0 else EXPENSE_COLOR,
                ),
            ],
            spacing=8,
        )

    # ---------- helpers ----------
    def _page_width(self) -> float:
        page = self.ctx.page
        candidates = [getattr(page, "width", None)]
        window = getattr(page, "window", None)
        if window is not None:
            candidates.append(getattr(window, "width", None))
        for candidate in candidates:
            try:
                width = float(candidate)
            except (TypeError, ValueError):
                continue
            if width > 0:
                return width
        return DEFAULT_PAGE_WIDTH

    def _chart_width(self) -> int:
        """The chart card's inner width at the current page width, in logical pixels.

        Flet picks the responsive breakpoint from the page width, so ``lg`` starts at a 992px
        window even though the content area itself is only about 807px there.
        """
        page_width = self._page_width()
        content = max(1.0, page_width - RAIL_WIDTH - DIVIDER_WIDTH - PAGE_PADDING)
        if page_width >= LG_BREAKPOINT:
            card_width = (content - GAP) * CHART_WIDE_COLUMNS / 12
        else:
            card_width = content
        return max(MIN_CHART_WIDTH, round(card_width - CARD_PADDING))

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
