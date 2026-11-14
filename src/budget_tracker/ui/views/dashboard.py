"""Dashboard: KPIs, spending breakdown, monthly trend, budget alerts and recent activity."""

from __future__ import annotations

from collections.abc import Callable

import flet as ft

from ...formatting import format_money, month_label, short_month_label
from ...models import BudgetProgress, CategoryTotal, MonthTotal
from ...services import DashboardData
from ..components import (
    MonthSelector,
    account_avatar,
    balance_text,
    card,
    empty_state,
    page_header,
    section_title,
    stat_card,
    transaction_row,
)
from ..context import AppContext
from ..theme import EXPENSE_COLOR, GAP, INCOME_COLOR, WARNING_COLOR, readable, fs
from .bills import bill_avatar, status_chip

TREND_HEIGHT = 170
CHART_CARD_HEIGHT = 300


class DashboardView:
    def __init__(self, ctx: AppContext, on_view_bills: Callable[[], None] | None = None) -> None:
        self.ctx = ctx
        self.on_view_bills = on_view_bills
        self.month_selector = MonthSelector(ctx.service.current_month(), lambda _: self.refresh())
        self.body = ft.Column(spacing=GAP)
        self.control = ft.Column(
            [
                page_header("Dashboard", "Your financial overview at a glance", [self.month_selector.control]),
                self.body,
            ],
            spacing=GAP + 8,
            scroll=ft.ScrollMode.AUTO,
            expand=True,
        )
        ctx.on_data_changed(self.refresh)
        self.refresh()

    def refresh(self) -> None:
        data = self.ctx.service.dashboard(self.month_selector.month)
        self.body.controls = [
            self._kpis(data),
            ft.ResponsiveRow(
                [
                    card(self._breakdown(data.expense_by_category), col={"xs": 12, "lg": 6}, height=CHART_CARD_HEIGHT),
                    card(self._trend(data.trend), col={"xs": 12, "lg": 6}, height=CHART_CARD_HEIGHT),
                ],
                spacing=GAP,
                run_spacing=GAP,
            ),
            ft.ResponsiveRow(
                [
                    ft.Column(
                        [card(self._upcoming(data)), card(self._accounts(data)), card(self._budget_alerts(data.budgets))],
                        spacing=GAP,
                        col={"xs": 12, "lg": 6},
                    ),
                    card(self._recent(data), col={"xs": 12, "lg": 6}),
                ],
                spacing=GAP,
                run_spacing=GAP,
                vertical_alignment=ft.CrossAxisAlignment.START,
            ),
        ]

    def _kpis(self, data: DashboardData) -> ft.ResponsiveRow:
        month = data.month_summary
        net = month.net_cents
        return ft.ResponsiveRow(
            [
                stat_card("Net Worth", format_money(data.net_worth_cents), ft.Icons.ACCOUNT_BALANCE_WALLET, "#3949AB", "All accounts, cards included"),
                stat_card("Income", format_money(month.income_cents), ft.Icons.ARROW_DOWNWARD, INCOME_COLOR, month_label(data.month)),
                stat_card("Spending", format_money(month.expense_cents), ft.Icons.ARROW_UPWARD, EXPENSE_COLOR, f"{month_label(data.month)}, net of refunds"),
                stat_card(
                    "Savings Rate",
                    f"{month.savings_rate:.0%}" if month.income_cents else "—",
                    ft.Icons.SAVINGS,
                    INCOME_COLOR if net >= 0 else EXPENSE_COLOR,
                    f"Net {format_money(net, signed=True)}",
                ),
            ],
            spacing=GAP,
            run_spacing=GAP,
        )

    @staticmethod
    def _breakdown(totals: list[CategoryTotal]) -> ft.Column:
        header = section_title("Spending by Category")
        if not totals:
            return ft.Column([header, empty_state(ft.Icons.PIE_CHART_OUTLINE, "No spending yet", "Expenses for this month will appear here.")])

        denominator = sum(t.total_cents for t in totals if t.total_cents > 0)
        stacked = None
        if denominator:
            stacked = ft.Container(
                content=ft.Row(
                    [ft.Container(bgcolor=readable(t.color), expand=max(1, round(t.total_cents / denominator * 1000)), tooltip=t.name) for t in totals],
                    spacing=2,
                ),
                height=14,
                border_radius=7,
                clip_behavior=ft.ClipBehavior.ANTI_ALIAS,
            )
        legend = [
            ft.Row(
                [
                    ft.Container(width=10, height=10, border_radius=5, bgcolor=readable(t.color)),
                    ft.Text(t.name, expand=True, size=fs(13)),
                    ft.Text(f"{t.total_cents / denominator:.0%}" if denominator else "—", size=fs(13), color=ft.Colors.ON_SURFACE_VARIANT, width=44, text_align=ft.TextAlign.RIGHT),
                    ft.Text(format_money(t.total_cents), size=fs(13), weight=ft.FontWeight.W_600, width=100, text_align=ft.TextAlign.RIGHT),
                ],
                spacing=10,
            )
            for t in totals[:6]
        ]
        controls = [header]
        if stacked is not None:
            controls.append(stacked)
        controls += [ft.Container(height=4), *legend]
        return ft.Column(controls, spacing=10)

    @staticmethod
    def _trend(trend: list[MonthTotal]) -> ft.Column:
        peak = max([max(m.income_cents, m.expense_cents) for m in trend] + [1])

        def bar(value: int, color: str, label: str) -> ft.Container:
            return ft.Container(
                width=16,
                height=max(3, value / peak * TREND_HEIGHT),
                bgcolor=color,
                border_radius=ft.BorderRadius.only(top_left=4, top_right=4),
                tooltip=f"{label}: {format_money(value)}",
            )

        columns = [
            ft.Column(
                [
                    ft.Row(
                        [bar(m.income_cents, INCOME_COLOR, "Income"), bar(m.expense_cents, EXPENSE_COLOR, "Expenses")],
                        spacing=4,
                        vertical_alignment=ft.CrossAxisAlignment.END,
                    ),
                    ft.Text(short_month_label(m.month), size=fs(13), color=ft.Colors.ON_SURFACE_VARIANT),
                ],
                horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                alignment=ft.MainAxisAlignment.END,
                spacing=6,
            )
            for m in trend
        ]

        def legend(color: str, text: str) -> ft.Row:
            return ft.Row([ft.Container(width=10, height=10, border_radius=2, bgcolor=color), ft.Text(text, size=fs(13))], spacing=6)

        return ft.Column(
            [
                section_title("Income vs Expenses", ft.Row([legend(INCOME_COLOR, "Income"), legend(EXPENSE_COLOR, "Expenses")], spacing=12)),
                ft.Container(
                    content=ft.Row(columns, alignment=ft.MainAxisAlignment.SPACE_AROUND, vertical_alignment=ft.CrossAxisAlignment.END),
                    height=TREND_HEIGHT + 36,
                    alignment=ft.Alignment.BOTTOM_CENTER,
                ),
            ],
            spacing=12,
        )

    def _upcoming(self, data: DashboardData) -> ft.Column:
        view_all = ft.TextButton(content="View bills", on_click=lambda _: self.on_view_bills()) if self.on_view_bills else None
        if not data.upcoming_bills:
            return ft.Column(
                [
                    section_title("Upcoming Payments", view_all),
                    ft.Row(
                        [ft.Icon(ft.Icons.CHECK_CIRCLE_OUTLINE, color=INCOME_COLOR, size=fs(18)), ft.Text("Nothing due soon", size=fs(13))],
                        spacing=8,
                    ),
                ],
                spacing=10,
            )
        rows = [
            ft.Row(
                [
                    bill_avatar(d.bill, 32),
                    ft.Column([ft.Text(d.bill.name, size=fs(13), weight=ft.FontWeight.W_500), status_chip(d)], spacing=4, expand=True),
                    ft.Text(format_money(d.amount_cents), size=fs(14), weight=ft.FontWeight.W_600),
                ],
                spacing=10,
            )
            for d in data.upcoming_bills[:6]
        ]
        return ft.Column([section_title("Upcoming Payments", view_all), *rows], spacing=10)

    @staticmethod
    def _accounts(data: DashboardData) -> ft.Column:
        header = section_title("Accounts", ft.Text(format_money(data.net_worth_cents), weight=ft.FontWeight.BOLD))
        if not data.accounts:
            return ft.Column([header, empty_state(ft.Icons.ACCOUNT_BALANCE, "No accounts", "Add accounts on the Accounts page.")])
        rows = [
            ft.Row(
                [
                    account_avatar(b.account, 32),
                    ft.Column(
                        [
                            ft.Text(b.account.name, size=fs(13), weight=ft.FontWeight.W_500),
                            ft.Text(
                                "Owed" if b.account.kind.is_liability and b.owed_cents else b.account.kind.label,
                                size=fs(12),
                                color=ft.Colors.ON_SURFACE_VARIANT,
                            ),
                        ],
                        spacing=0,
                        expand=True,
                    ),
                    balance_text(b, size=fs(14)),
                ],
                spacing=10,
            )
            for b in data.accounts
        ]
        return ft.Column([header, *rows], spacing=10)

    @staticmethod
    def _budget_alerts(budgets: list[BudgetProgress]) -> ft.Column:
        header = section_title("Budget Health")
        if not budgets:
            return ft.Column([header, empty_state(ft.Icons.TRACK_CHANGES, "No budgets set", "Create budgets on the Budgets page to track your limits.")])
        rows: list[ft.Control] = []
        for b in sorted(budgets, key=lambda x: x.ratio, reverse=True)[:6]:
            color = {"over": EXPENSE_COLOR, "warning": WARNING_COLOR}.get(b.status, INCOME_COLOR)
            rows.append(
                ft.Column(
                    [
                        ft.Row(
                            [
                                ft.Text(b.category_name, size=fs(13), weight=ft.FontWeight.W_500, expand=True),
                                ft.Text(f"{format_money(b.spent_cents)} / {format_money(b.limit_cents)}", size=fs(13), color=ft.Colors.ON_SURFACE_VARIANT),
                            ]
                        ),
                        ft.ProgressBar(value=min(b.ratio, 1.0), color=color, bgcolor=ft.Colors.with_opacity(0.15, color), bar_height=8, border_radius=4),
                    ],
                    spacing=6,
                )
            )
        over = sum(1 for b in budgets if b.status == "over")
        status = (
            ft.Text(f"{over} budget(s) exceeded", color=EXPENSE_COLOR, size=fs(13), weight=ft.FontWeight.W_600)
            if over
            else ft.Text("All budgets on track", color=INCOME_COLOR, size=fs(13), weight=ft.FontWeight.W_600)
        )
        return ft.Column([section_title("Budget Health", status), *rows], spacing=14)

    @staticmethod
    def _recent(data: DashboardData) -> ft.Column:
        header = section_title("Recent Transactions")
        if not data.recent:
            return ft.Column([header, empty_state(ft.Icons.RECEIPT_LONG, "No transactions", "Add your first transaction to get started.")])
        return ft.Column([header, *[transaction_row(t) for t in data.recent]], spacing=2)
