"""Year view: monthly budget performance, cash flow and a year-end pace projection."""

from __future__ import annotations

from datetime import date

import flet as ft

from ...formatting import format_money, short_month_label
from ...services import YearMonth, YearOverview
from ..components import card, empty_state, page_header, section_title, stat_card
from ..context import AppContext
from ..theme import EXPENSE_COLOR, GAP, INCOME_COLOR, SEED_COLOR

CHART_HEIGHT = 150
COLUMN_WIDTH = 70


class YearView:
    def __init__(self, ctx: AppContext) -> None:
        self.ctx = ctx
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
        self.body = ft.Column(spacing=GAP)
        self.control = ft.Column(
            [
                page_header("Year", "Budget performance and cash flow across the year", [self.year]),
                self.body,
            ],
            spacing=GAP,
            scroll=ft.ScrollMode.AUTO,
            expand=True,
        )
        ctx.on_data_changed(self.refresh)
        self.refresh()

    def refresh(self) -> None:
        overview = self.ctx.service.year_overview(int(self.year.value))
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
            self.body.controls = [
                self._kpis(overview),
                card(self._chart(overview), col={"xs": 12}),
                card(self._table(overview), col={"xs": 12}),
            ]
        self.ctx.page.update()

    # ---------- top row ----------
    def _kpis(self, overview: YearOverview) -> ft.ResponsiveRow:
        difference = overview.ytd_difference_cents
        under = difference >= 0
        if overview.prior_spending_cents is None:
            comparison = "No prior-year data"
        else:
            delta = overview.ytd_spending_cents - overview.prior_spending_cents
            percent = f" ({delta / overview.prior_spending_cents:+.0%})" if overview.prior_spending_cents else ""
            comparison = f"vs. same period last year: {format_money(delta, signed=True)}{percent}"
        if overview.forecast_spending_cents is None:
            forecast_title = "Year total"
            forecast_value = format_money(overview.ytd_spending_cents)
            forecast_caption = "Full year"
        else:
            forecast_title = "Projected year-end"
            forecast_value = format_money(overview.forecast_spending_cents)
            forecast_caption = f"Pace from {overview.elapsed_months} month(s)"
        return ft.ResponsiveRow(
            [
                stat_card(
                    "YTD budget",
                    format_money(overview.ytd_budget_cents),
                    ft.Icons.SAVINGS_OUTLINED,
                    SEED_COLOR,
                    f"{overview.budgeted_months} month(s) budgeted",
                ),
                stat_card(
                    "YTD spending",
                    format_money(overview.ytd_spending_cents),
                    ft.Icons.ARROW_UPWARD,
                    EXPENSE_COLOR,
                    comparison,
                ),
                stat_card(
                    "Under budget" if under else "Over budget",
                    format_money(abs(difference)),
                    ft.Icons.BALANCE,
                    INCOME_COLOR if under else EXPENSE_COLOR,
                    "YTD budget minus spending",
                ),
                stat_card(
                    forecast_title,
                    forecast_value,
                    ft.Icons.TIMELINE,
                    SEED_COLOR,
                    forecast_caption,
                ),
            ],
            spacing=GAP,
            run_spacing=GAP,
        )

    # ---------- chart ----------
    def _chart(self, overview: YearOverview) -> ft.Column:
        peak = max([max(month.budget_cents, month.spending_cents) for month in overview.months] + [1])
        current = self.ctx.service.current_month()

        def bar(value: int, color: str, label: str) -> ft.Container:
            return ft.Container(
                width=13,
                height=max(3, value / peak * CHART_HEIGHT),
                bgcolor=color,
                border_radius=ft.BorderRadius.only(top_left=4, top_right=4),
                tooltip=f"{label}: {format_money(value)}",
            )

        columns = [
            ft.Column(
                [
                    ft.Row(
                        [
                            bar(month.budget_cents, SEED_COLOR, "Budget"),
                            bar(month.spending_cents, EXPENSE_COLOR, "Spending"),
                        ],
                        spacing=4,
                        vertical_alignment=ft.CrossAxisAlignment.END,
                    ),
                    ft.Text(
                        short_month_label(month.month),
                        size=12,
                        color=SEED_COLOR if month.month == current else ft.Colors.ON_SURFACE_VARIANT,
                        weight=ft.FontWeight.W_600 if month.month == current else ft.FontWeight.NORMAL,
                    ),
                ],
                width=COLUMN_WIDTH,
                horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                alignment=ft.MainAxisAlignment.END,
                spacing=6,
            )
            for month in overview.months
        ]

        def legend(color: str, text: str) -> ft.Row:
            return ft.Row(
                [ft.Container(width=10, height=10, border_radius=2, bgcolor=color), ft.Text(text, size=12)],
                spacing=6,
            )

        return ft.Column(
            [
                section_title(
                    "Budget vs. spending",
                    ft.Row([legend(SEED_COLOR, "Budget"), legend(EXPENSE_COLOR, "Spending")], spacing=12),
                ),
                ft.Container(
                    content=ft.Row(
                        columns,
                        alignment=ft.MainAxisAlignment.SPACE_AROUND,
                        vertical_alignment=ft.CrossAxisAlignment.END,
                    ),
                    height=CHART_HEIGHT + 40,
                    alignment=ft.Alignment.BOTTOM_CENTER,
                ),
            ],
            spacing=12,
        )

    # ---------- table ----------
    def _table(self, overview: YearOverview) -> ft.Column:
        header = ft.Row(
            [
                ft.Text("Month", size=11, width=60, color=ft.Colors.ON_SURFACE_VARIANT),
                ft.Text("Budget", size=11, width=92, text_align=ft.TextAlign.RIGHT, color=ft.Colors.ON_SURFACE_VARIANT),
                ft.Text("Spent", size=11, width=92, text_align=ft.TextAlign.RIGHT, color=ft.Colors.ON_SURFACE_VARIANT),
                ft.Text("Difference", size=11, width=100, text_align=ft.TextAlign.RIGHT, color=ft.Colors.ON_SURFACE_VARIANT),
                ft.Text("Income", size=11, width=92, text_align=ft.TextAlign.RIGHT, color=ft.Colors.ON_SURFACE_VARIANT),
                ft.Text("Net", size=11, width=92, text_align=ft.TextAlign.RIGHT, color=ft.Colors.ON_SURFACE_VARIANT),
            ],
            spacing=8,
        )
        rows: list[ft.Control] = [header]
        for month in overview.months:
            rows.append(self._row(month))
        label = "Year total" if overview.year < date.today().year else "YTD"
        difference = overview.ytd_difference_cents
        rows.append(ft.Divider(height=1))
        rows.append(
            ft.Row(
                [
                    ft.Text(label, size=12, width=60, weight=ft.FontWeight.W_600),
                    ft.Text(format_money(overview.ytd_budget_cents), size=12, width=92, text_align=ft.TextAlign.RIGHT, weight=ft.FontWeight.W_600),
                    ft.Text(format_money(overview.ytd_spending_cents), size=12, width=92, text_align=ft.TextAlign.RIGHT, weight=ft.FontWeight.W_600),
                    ft.Text(
                        format_money(difference, signed=True),
                        size=12,
                        width=100,
                        text_align=ft.TextAlign.RIGHT,
                        weight=ft.FontWeight.W_600,
                        color=INCOME_COLOR if difference >= 0 else EXPENSE_COLOR,
                    ),
                    ft.Text(format_money(overview.ytd_income_cents), size=12, width=92, text_align=ft.TextAlign.RIGHT, weight=ft.FontWeight.W_600),
                    ft.Text(
                        format_money(overview.ytd_income_cents - overview.ytd_spending_cents, signed=True),
                        size=12,
                        width=92,
                        text_align=ft.TextAlign.RIGHT,
                        weight=ft.FontWeight.W_600,
                    ),
                ],
                spacing=8,
            )
        )
        return ft.Column([section_title("Monthly detail"), *rows], spacing=4)

    @staticmethod
    def _row(month: YearMonth) -> ft.Row:
        budget = format_money(month.budget_cents) if month.has_budget else "—"
        difference = format_money(month.difference_cents, signed=True) if month.has_budget else "—"
        difference_color = (
            INCOME_COLOR if not month.has_budget or month.difference_cents >= 0 else EXPENSE_COLOR
        )
        return ft.Row(
            [
                ft.Text(short_month_label(month.month), size=12, width=60),
                ft.Text(budget, size=12, width=92, text_align=ft.TextAlign.RIGHT),
                ft.Text(format_money(month.spending_cents), size=12, width=92, text_align=ft.TextAlign.RIGHT),
                ft.Text(difference, size=12, width=100, text_align=ft.TextAlign.RIGHT, color=difference_color),
                ft.Text(format_money(month.income_cents), size=12, width=92, text_align=ft.TextAlign.RIGHT),
                ft.Text(
                    format_money(month.net_cents, signed=True),
                    size=12,
                    width=92,
                    text_align=ft.TextAlign.RIGHT,
                    color=INCOME_COLOR if month.net_cents >= 0 else EXPENSE_COLOR,
                ),
            ],
            spacing=8,
        )
