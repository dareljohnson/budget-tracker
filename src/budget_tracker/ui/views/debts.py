"""Debts: track what is owed, link credit cards and plan the payoff."""

from __future__ import annotations

import base64
from datetime import date

import flet as ft

from ...debt_planner import (
    DebtPlan,
    DebtStrategy,
    escrow_from_amortization,
    monthly_interest_cents,
)
from ...formatting import (
    format_money,
    format_percent,
    month_key,
    month_label,
    parse_date,
    parse_money,
    parse_percent_bps,
    parse_signed_money,
    shift_month,
)
from ...models import AccountKind, Debt, DebtKind, TransactionType
from ...repository import RepositoryError
from ...services import DebtSummary
from ..charts import (
    DEBT_OTHER_COLOR_DARK,
    DEBT_OTHER_COLOR_LIGHT,
    DEBT_SERIES_COLORS_DARK,
    DEBT_SERIES_COLORS_LIGHT,
    ChartColors,
    DebtSeries,
    debt_stack_chart_png,
)
from ..components import card, confirm_dialog, date_field, empty_state, page_header, section_title
from ..context import AppContext
from ..theme import (
    EXPENSE_COLOR,
    GAP,
    INCOME_COLOR,
    RADIUS,
    SEED_COLOR,
    THEME_PRESET_SETTING,
    build_theme,
    fs,
    text_scale,
)
from .loan_payment import LoanSplitFields
from .velocity import VelocityPanel

EXTRA_PRESETS = [0, 5_000, 10_000, 20_000]
CHART_HORIZONS = (("12", "1y"), ("36", "3y"), ("60", "5y"), ("all", "All"))
CHART_HOVER_COLUMNS = 120
DEBT_CHART_HEIGHT = 280
DEBT_CHART_MIN_WIDTH = 420
DEBT_CHART_MAX_NAMED = 8
DEBT_LAYOUT_BREAKPOINT = 768
DEBT_LAYOUT_WIDE_BREAKPOINT = 992
DEFAULT_PAGE_WIDTH = 1320
RAIL_WIDTH = 120
PAGE_PADDING = 64
DIVIDER_WIDTH = 1
CARD_PADDING = 40


def comparison_amounts(custom_text: str = "") -> list[int]:
    amounts = list(EXTRA_PRESETS)
    text = (custom_text or "").strip()
    if text:
        try:
            custom = parse_money(text)
        except ValueError:
            return amounts
        if custom not in amounts:
            amounts.append(custom)
    return amounts


def savings_cents(first: DebtPlan, second: DebtPlan) -> int | None:
    if not first.finished or not second.finished:
        return None
    difference = abs(first.total_interest_cents - second.total_interest_cents)
    return difference or None


def plans_coincide(first: DebtPlan, second: DebtPlan) -> bool:
    """True when both strategies produce the same order and totals."""
    if (first.finished, first.months, first.total_interest_cents) != (
        second.finished,
        second.months,
        second.total_interest_cents,
    ):
        return False
    return [debt.id for debt, _ in first.payoff_order] == [
        debt.id for debt, _ in second.payoff_order
    ]


class DebtsView:
    def __init__(self, ctx: AppContext) -> None:
        self.ctx = ctx
        # Plain alignment, not CrossAxisAlignment.STRETCH: on Flet 1.1.2 a ResponsiveRow using
        # STRETCH without an explicit height collapses to zero height in this view's scrollable
        # body, so the cards (and the empty state) never appear. Cards size to their content.
        self.grid = ft.ResponsiveRow(spacing=GAP, run_spacing=GAP)
        self.chart_panel = ft.Column(spacing=GAP)
        self.plan_panel = ft.Column(spacing=GAP)
        self.velocity_panel = VelocityPanel(ctx)
        self.extra_cents = ctx.service.debt_plan_extra()
        self.custom_text = ""
        self.strategy = ctx.service.debt_plan_strategy()
        self.chart_horizon = "60"
        self.schedule_limit = 24
        self._payoff_months: dict[int, int] = {}
        self._start_month = ctx.service.current_month()
        self.last_start_month = self._start_month
        self.custom_extra = ft.TextField(label="Custom extra", width=160)
        self.custom_calculate = ft.FilledButton(
            content="Calculate",
            icon=ft.Icons.CALCULATE,
            visible=False,
            on_click=lambda _: self._set_custom_extra(self.custom_extra.value),
        )
        self.control = ft.Column(
            [
                page_header(
                    "Debts",
                    "Pay down what you owe",
                    [ft.FilledButton(content="Add debt", icon=ft.Icons.ADD,
                                     on_click=lambda _: DebtEditor(ctx, None).open())],
                ),
                self.chart_panel,
                self.velocity_panel.control,
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
        self._render_plan()
        self.velocity_panel.refresh()
        summaries = self.ctx.service.list_debt_summaries()
        velocity_ids = self.ctx.service.velocity_target_ids()
        self.grid.controls = [
            self._debt_card(
                s,
                self._payoff_months.get(s.debt.id),
                velocity_target=s.debt.id in velocity_ids,
            )
            for s in summaries
        ] or [
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

    # ---------- payoff plan panel ----------

    def _set_custom_extra(self, text: str) -> None:
        self.custom_text = text or ""
        self.schedule_limit = 24
        try:
            self.extra_cents = parse_money(self.custom_text)
        except ValueError:
            pass
        self.ctx.service.set_debt_plan_extra(self.extra_cents)
        self.refresh()
        self.ctx.page.update()

    def _set_extra(self, cents: int) -> None:
        self.extra_cents = max(0, int(cents))
        self.ctx.service.set_debt_plan_extra(self.extra_cents)
        self.schedule_limit = 24
        self.refresh()
        self.ctx.page.update()

    def _plan_extra_for(self, debt_id: int) -> int:
        """This debt's share of the plan's first-month extra, including roll-over from a paid-off target."""
        if self.extra_cents <= 0:
            return 0
        return self.ctx.service.debt_extra_allocation(
            self.extra_cents, self.strategy
        ).get(debt_id, 0)

    def _extra_selected(self, event: ft.Event[ft.Dropdown]) -> None:
        value = event.control.value
        if value == "custom":
            self.custom_extra.visible = True
            self.custom_calculate.visible = True
            self.ctx.page.update()
            return
        self._set_extra(int(value))

    def _select_strategy(self, strategy: DebtStrategy) -> None:
        self.strategy = strategy
        self.ctx.service.set_debt_plan_strategy(strategy)
        self.schedule_limit = 24
        self.refresh()
        self.ctx.page.update()

    def _show_more(self, _: ft.Event | None) -> None:
        self.schedule_limit += 24
        self.refresh()
        self.ctx.page.update()

    def _render_plan(self) -> None:
        all_summaries = self.ctx.service.list_debt_summaries()
        velocity_ids = self.ctx.service.velocity_target_ids()
        summaries = [
            summary for summary in all_summaries if summary.debt.id not in velocity_ids
        ]
        if not any(summary.balance_cents > 0 for summary in summaries):
            self._payoff_months = {}
            self.chart_panel.controls = []
            message = (
                "All debts are assigned to Velocity Banking. Edit the velocity plan to move "
                "a target back to Payoff."
                if velocity_ids
                else "No debt to pay off."
            )
            self.plan_panel.controls = [card(ft.Text(message), col=12)]
            return

        self._start_month = self.ctx.service.current_month()
        self.last_start_month = self._start_month
        plans = self.ctx.service.debt_plans(self.extra_cents)
        current = plans[self.strategy]
        self._payoff_months = {debt.id: month for debt, month in current.payoff_order}
        included = [summary for summary in summaries if summary.balance_cents > 0]
        chart_card = self._debt_chart_card(current, included)
        self.chart_panel.controls = [chart_card] if chart_card is not None else []

        controls: list[ft.Control] = [section_title("Payoff plan")]
        warnings = [
            ft.Text(
                f"{summary.debt.name}: the minimum does not cover monthly interest",
                size=fs(13),
                color=EXPENSE_COLOR,
            )
            for summary in summaries
            if summary.minimum_below_interest
        ]
        if not all(plan.finished for plan in plans.values()):
            warnings.append(
                ft.Text("Not paid off within 50 years at this payment.", size=fs(13), color=EXPENSE_COLOR)
            )
        if self.extra_cents == 0 and all(
            summary.effective_minimum_cents == 0
            for summary in summaries
            if summary.balance_cents > 0
        ):
            warnings.append(ft.Text("No payment is being made.", size=fs(13), color=EXPENSE_COLOR))
        if warnings:
            controls.append(ft.Container(
                content=ft.Column(warnings, spacing=4),
                padding=ft.Padding.symmetric(horizontal=12, vertical=8),
                border_radius=10,
                bgcolor=ft.Colors.with_opacity(0.08, EXPENSE_COLOR),
            ))

        controls.append(ft.Row(
            [
                self._extra_selector(),
                self.custom_extra,
                self.custom_calculate,
            ],
            spacing=GAP,
            wrap=True,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        ))
        controls.append(self._strategy_cards(plans, len(included)))
        if plans_coincide(plans[DebtStrategy.AVALANCHE], plans[DebtStrategy.SNOWBALL]):
            controls.append(ft.Text(
                "Avalanche and snowball target the same debt here, so the plans match.",
                size=fs(13),
                color=ft.Colors.ON_SURFACE_VARIANT,
            ))
        controls.append(section_title("What extra buys"))
        controls.append(self._comparison_table())
        controls.append(self._included_table(included))
        controls.append(self._payoff_table(included))
        controls.append(section_title("Combined monthly schedule"))
        changes = self._payment_changes(summaries)
        if changes:
            controls.append(ft.Container(
                content=ft.Column(
                    [ft.Text(line, size=fs(13), color=SEED_COLOR) for line in changes],
                    spacing=4,
                ),
                padding=ft.Padding.symmetric(horizontal=12, vertical=8),
                border_radius=10,
                bgcolor=ft.Colors.with_opacity(0.06, SEED_COLOR),
            ))
        controls.append(ft.Text(
            "Debt payments (principal + interest) and balances below are the total across every debt "
            "listed above. Escrow is not part of debt payoff.",
            size=fs(13),
            color=ft.Colors.ON_SURFACE_VARIANT,
        ))
        controls.append(self._yearly_table(current))
        controls.append(self._monthly_table(current))
        if self.schedule_limit < len(current.schedule):
            controls.append(ft.TextButton(content="Show all months", on_click=self._show_more))
        self.plan_panel.controls = [card(ft.Column(controls, spacing=GAP), col=12)]

    def _set_horizon(self, value: str) -> None:
        if value not in {option for option, _ in CHART_HORIZONS}:
            return
        self.chart_horizon = value
        self.refresh()
        self.ctx.page.update()

    def _horizon_changed(self, event: ft.Event[ft.SegmentedButton] | None) -> None:
        selected = getattr(getattr(event, "control", None), "selected", None) or []
        if selected:
            self._set_horizon(selected[0])

    def _page_width(self) -> float:
        candidates = [getattr(self.ctx.page, "width", None)]
        window = getattr(self.ctx.page, "window", None)
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
        """The chart's inner width at the current page width, in logical pixels."""
        page_width = self._page_width()
        content = max(1.0, page_width - RAIL_WIDTH - DIVIDER_WIDTH - PAGE_PADDING)
        card_width = max(1.0, content - CARD_PADDING)
        if page_width >= DEBT_LAYOUT_WIDE_BREAKPOINT:
            chart_width = (card_width - GAP) * 9 / 12
        elif page_width >= DEBT_LAYOUT_BREAKPOINT:
            chart_width = (card_width - GAP) * 8 / 12
        else:
            chart_width = card_width
        return max(DEBT_CHART_MIN_WIDTH, round(chart_width))

    def _chart_colors(self) -> tuple[ChartColors, bool]:
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
        return (
            ChartColors(
                primary=scheme.primary,
                grid=scheme.outline_variant,
                text=scheme.on_surface,
                zero=scheme.outline,
                lowest=scheme.secondary,
                negative=scheme.error,
                surface=scheme.surface_container_low,
            ),
            dark,
        )

    def _chart_items(
        self, plan: DebtPlan, included: list[DebtSummary]
    ) -> list[dict[str, object]]:
        """The chart's debt bands, with extras grouped once there are more than eight debts."""
        _, dark = self._chart_colors()
        palette = DEBT_SERIES_COLORS_DARK if dark else DEBT_SERIES_COLORS_LIGHT
        other_color = DEBT_OTHER_COLOR_DARK if dark else DEBT_OTHER_COLOR_LIGHT
        by_id = {series.debt_id: series for series in plan.balance_series}
        records: list[tuple[DebtSummary, tuple[int, ...]]] = []
        ordered = sorted(
            included,
            key=lambda summary: (
                summary.debt.id is None,
                summary.debt.id or 0,
            ),
        )
        for summary in ordered:
            series = by_id.get(summary.debt.id)
            if series is not None:
                records.append((summary, tuple(series.balances_cents)))
        if not records:
            return []

        def item(summary: DebtSummary, balances: tuple[int, ...], color: str) -> dict[str, object]:
            return {
                "label": summary.debt.name,
                "balances": balances,
                "color": color,
                "balance_cents": summary.balance_cents,
                "apr_bps": summary.apr_bps,
                "payoff_month": self._payoff_months.get(summary.debt.id),
                "group_count": 1,
            }

        if len(records) <= DEBT_CHART_MAX_NAMED:
            return [
                item(summary, balances, palette[index % len(palette)])
                for index, (summary, balances) in enumerate(records)
            ]

        named = records[: DEBT_CHART_MAX_NAMED - 1]
        rest = records[DEBT_CHART_MAX_NAMED - 1 :]
        items = [
            item(summary, balances, palette[index])
            for index, (summary, balances) in enumerate(named)
        ]
        count = min(len(balances) for _, balances in rest)
        grouped = tuple(
            sum(balances[index] for _, balances in rest) for index in range(count)
        )
        payoffs = [
            self._payoff_months[summary.debt.id]
            for summary, _ in rest
            if summary.debt.id in self._payoff_months
        ]
        items.append(
            {
                "label": f"Other ({len(rest)} debts)",
                "balances": grouped,
                "color": other_color,
                "balance_cents": sum(summary.balance_cents for summary, _ in rest),
                "apr_bps": None,
                "payoff_month": max(payoffs) if len(payoffs) == len(rest) else None,
                "group_count": len(rest),
            }
        )
        return items

    def _debt_chart_card(
        self, plan: DebtPlan, included: list[DebtSummary]
    ) -> ft.Container | None:
        items = self._chart_items(plan, included)
        if not items:
            return None
        count = min(len(item["balances"]) for item in items)  # type: ignore[arg-type]
        keys = [shift_month(self._start_month, index) for index in range(count)]
        if self.chart_horizon == "all":
            limit = count
        else:
            limit = min(count, int(self.chart_horizon) + 1)
        view_keys = keys[:limit]
        chart_series = [
            DebtSeries(
                str(item["label"]),
                tuple(item["balances"])[:limit],  # type: ignore[arg-type]
                str(item["color"]),
            )
            for item in items
        ]
        colors, _ = self._chart_colors()
        png = debt_stack_chart_png(
            chart_series,
            view_keys,
            width=self._chart_width(),
            height=DEBT_CHART_HEIGHT,
            colors=colors,
            text_scale=text_scale(),
        )
        total_now = sum(int(item["balance_cents"]) for item in items)
        still_owed = sum(
            int(item["balances"][limit - 1]) for item in items  # type: ignore[index]
        )
        if self.chart_horizon == "all" or limit >= count:
            note = "Full plan · month-end balances · escrow excluded"
        else:
            label = {"12": "1-year", "36": "3-year", "60": "5-year"}.get(
                self.chart_horizon, "Selected"
            )
            note = (
                f"{label} view · {format_money(still_owed)} still owed then · escrow excluded"
                if still_owed
                else f"{label} view · debt-free by then · escrow excluded"
            )

        horizon = ft.SegmentedButton(
            segments=[
                ft.Segment(value=value, label=ft.Text(label, size=fs(12)))
                for value, label in CHART_HORIZONS
            ],
            selected=[self.chart_horizon],
            show_selected_icon=False,
            allow_multiple_selection=False,
            on_change=self._horizon_changed,
        )
        header = ft.ResponsiveRow(
            [
                ft.Column(
                    [
                        ft.Text("Debt reduction", size=fs(16), weight=ft.FontWeight.W_600),
                        ft.Text(
                            f"Payoff debts · {plan.strategy.label} · "
                            f"{format_money(self.extra_cents)} extra",
                            size=fs(12),
                            color=ft.Colors.ON_SURFACE_VARIANT,
                        ),
                    ],
                    spacing=2,
                    col={"xs": 12, "md": 8, "lg": 9},
                ),
                ft.Container(
                    content=ft.Row([horizon], alignment=ft.MainAxisAlignment.END),
                    col={"xs": 12, "md": 4, "lg": 3},
                ),
            ],
            spacing=GAP,
            run_spacing=8,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        )
        debt_free = (
            month_label(plan.debt_free_month)
            if plan.finished and plan.debt_free_month
            else "Not within 50 years"
        )
        summary = ft.Row(
            [
                ft.Container(
                    content=ft.Text(
                        f"Debt-free {debt_free}",
                        size=fs(12),
                        weight=ft.FontWeight.W_600,
                        color=INCOME_COLOR,
                    ),
                    padding=ft.Padding.symmetric(horizontal=10, vertical=4),
                    border_radius=999,
                    bgcolor=ft.Colors.with_opacity(0.12, INCOME_COLOR),
                ),
                ft.Text(
                    f"Est. interest {format_money(plan.total_interest_cents)} · "
                    f"{self._duration_label(plan.months)}",
                    size=fs(12),
                    color=ft.Colors.ON_SURFACE_VARIANT,
                ),
            ],
            spacing=10,
            wrap=True,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        )
        chart = ft.Container(
            height=DEBT_CHART_HEIGHT,
            expand=True,
            image=ft.DecorationImage(
                src=self._png_source(png),
                fit=ft.BoxFit.CONTAIN,
            ),
            content=self._chart_hover_overlay(items, view_keys),
        )
        chart_column = ft.Column(
            [
                ft.Row([chart], spacing=0),
                ft.Text(note, size=fs(11), color=ft.Colors.ON_SURFACE_VARIANT),
            ],
            spacing=6,
        )
        panel = ft.Container(
            content=self._chart_debt_panel(items, total_now),
            height=max(DEBT_CHART_HEIGHT, fs(40) * len(items) + fs(86)),
            padding=ft.Padding.only(left=GAP),
            border=ft.Border.only(
                left=ft.BorderSide(1, ft.Colors.OUTLINE_VARIANT)
            ),
        )
        body = ft.ResponsiveRow(
            [
                ft.Container(content=chart_column, col={"xs": 12, "md": 8, "lg": 9}),
                ft.Container(content=panel, col={"xs": 12, "md": 4, "lg": 3}),
            ],
            spacing=GAP,
            run_spacing=GAP,
        )
        return card(
            ft.Column([header, summary, body], spacing=12),
            col=12,
        )

    @staticmethod
    def _duration_label(months: int) -> str:
        years, remainder = divmod(max(0, months), 12)
        if years and remainder:
            return f"{years} yr {remainder} mo"
        if years:
            return f"{years} yr"
        return f"{remainder} mo"

    def _chart_hover_overlay(
        self, items: list[dict[str, object]], keys: list[str]
    ) -> ft.Row:
        columns: list[ft.Control] = []
        stride = max(1, (len(keys) + CHART_HOVER_COLUMNS - 1) // CHART_HOVER_COLUMNS)
        for index in range(0, len(keys), stride):
            key = keys[index]
            total = sum(int(item["balances"][index]) for item in items)  # type: ignore[index]
            lines = [f"{month_label(key)}", f"Total {format_money(total)}"]
            lines.extend(
                f"{item['label']}: {format_money(int(item['balances'][index]))}"  # type: ignore[index]
                for item in items
            )
            columns.append(
                ft.Container(
                    expand=True,
                    height=DEBT_CHART_HEIGHT,
                    bgcolor=ft.Colors.TRANSPARENT,
                    tooltip="\n".join(lines),
                )
            )
        return ft.Row(columns, spacing=0, expand=True)

    def _chart_debt_panel(
        self, items: list[dict[str, object]], total_now: int
    ) -> ft.Column:
        rows: list[ft.Control] = [
            ft.Text(
                "Debts in this plan",
                size=fs(11),
                weight=ft.FontWeight.W_600,
                color=ft.Colors.ON_SURFACE_VARIANT,
            )
        ]
        for item in items:
            payoff_month = item["payoff_month"]
            payoff = (
                month_label(shift_month(self._start_month, int(payoff_month)))
                if payoff_month is not None
                else "—"
            )
            if item["group_count"] == 1:
                detail = (
                    f"{format_money(int(item['balance_cents']))} · "
                    f"{format_percent(int(item['apr_bps']))} APR"
                )
            else:
                detail = (
                    f"{format_money(int(item['balance_cents']))} · "
                    f"{int(item['group_count'])} debts"
                )
            rows.append(
                ft.Row(
                    [
                        ft.Container(
                            width=9,
                            height=9,
                            border_radius=2,
                            bgcolor=str(item["color"]),
                        ),
                        ft.Column(
                            [
                                ft.Text(
                                    str(item["label"]),
                                    size=fs(13),
                                    weight=ft.FontWeight.W_600,
                                ),
                                ft.Text(
                                    detail,
                                    size=fs(11),
                                    color=ft.Colors.ON_SURFACE_VARIANT,
                                ),
                            ],
                            spacing=1,
                            expand=True,
                        ),
                        ft.Text(
                            f"Paid\n{payoff}",
                            size=fs(11),
                            color=ft.Colors.ON_SURFACE_VARIANT,
                            text_align=ft.TextAlign.RIGHT,
                        ),
                    ],
                    spacing=8,
                    vertical_alignment=ft.CrossAxisAlignment.START,
                )
            )
        rows.append(ft.Container(expand=True))
        rows.append(
            ft.Row(
                [
                    ft.Text(
                        "Total",
                        size=fs(12),
                        color=ft.Colors.ON_SURFACE_VARIANT,
                    ),
                    ft.Text(
                        format_money(total_now),
                        size=fs(13),
                        weight=ft.FontWeight.W_600,
                    ),
                ],
                alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
            )
        )
        return ft.Column(
            rows,
            spacing=10,
            expand=True,
            horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
        )

    @staticmethod
    def _png_source(png: bytes) -> str:
        return "data:image/png;base64," + base64.b64encode(png).decode("ascii")

    def _payment_changes(self, summaries: list[DebtSummary]) -> list[str]:
        """Future step-ups, so they are visible even beyond the 24-month table preview."""
        lines: list[str] = []
        for summary in summaries:
            if (
                summary.minimum_after_cents is not None
                and summary.minimum_after_date is not None
                and summary.minimum_after_cents != summary.effective_minimum_cents
            ):
                lines.append(
                    f"{summary.debt.name}: debt payment (P&I) changes to "
                    f"{format_money(summary.minimum_after_cents)} from "
                    f"{month_label(month_key(summary.minimum_after_date))}."
                )
            if summary.debt.account_id is None:
                continue
            bill = next(
                (
                    bill
                    for bill in self.ctx.repo.list_bills()
                    if bill.active
                    and bill.pay_to_account_id == summary.debt.account_id
                    and bill.amount_after_cents is not None
                    and bill.reduced_until is not None
                ),
                None,
            )
            if bill is not None:
                lines.append(
                    f"{bill.name}: cash payment changes to "
                    f"{format_money(bill.amount_after_cents)} from "
                    f"{month_label(shift_month(month_key(bill.reduced_until), 1))} "
                    f"(currently {format_money(bill.amount_cents)})."
                )
        return lines

    def _extra_selector(self) -> ft.Dropdown:
        keys = [str(amount) for amount in EXTRA_PRESETS]
        value = str(self.extra_cents) if self.extra_cents in EXTRA_PRESETS else "custom"
        self.custom_extra.value = self.custom_text
        self.custom_extra.visible = value == "custom"
        self.custom_extra.on_submit = lambda _: self._set_custom_extra(self.custom_extra.value)
        self.custom_calculate.visible = value == "custom"
        return ft.Dropdown(
            label="Extra per month (on top of minimums)",
            width=280,
            options=[
                ft.DropdownOption(key=key, text=f"${int(key) // 100}")
                for key in keys
            ] + [ft.DropdownOption(key="custom", text="Custom")],
            value=value,
            on_select=self._extra_selected,
        )

    def _strategy_cards(
        self, plans: dict[DebtStrategy, DebtPlan], debt_count: int
    ) -> ft.ResponsiveRow:
        savings = savings_cents(plans[DebtStrategy.AVALANCHE], plans[DebtStrategy.SNOWBALL])
        lower = min(plans.values(), key=lambda plan: plan.total_interest_cents)
        return ft.ResponsiveRow(
            [
                self._strategy_card(
                    plan,
                    savings if plan.strategy == lower.strategy else 0,
                    debt_count,
                )
                for plan in plans.values()
            ],
            spacing=GAP,
            run_spacing=GAP,
        )

    def _strategy_card(self, plan: DebtPlan, savings: int = 0, debt_count: int = 0) -> ft.Container:
        selected = plan.strategy == self.strategy
        debt_free = (
            month_label(plan.debt_free_month) if plan.debt_free_month else "Not within 50 years"
        )
        lines: list[ft.Control] = [
            ft.Text(plan.strategy.label, size=fs(15), weight=ft.FontWeight.W_600),
            ft.Text(
                debt_free,
                size=fs(18),
                weight=ft.FontWeight.W_700,
                color=SEED_COLOR if plan.finished else EXPENSE_COLOR,
            ),
            ft.Text(f"{plan.months} months", size=fs(13), color=ft.Colors.ON_SURFACE_VARIANT),
            ft.Text(
                f"Covers {debt_count} debt{'s' if debt_count != 1 else ''}",
                size=fs(13),
                color=ft.Colors.ON_SURFACE_VARIANT,
            ),
            ft.Text(f"Interest {format_money(plan.total_interest_cents)}", size=fs(13)),
            ft.Text(f"Paid {format_money(plan.total_paid_cents)}", size=fs(13)),
            ft.Text(
                f"First target: {plan.first_target.name if plan.first_target else '—'}",
                size=fs(13),
                color=ft.Colors.ON_SURFACE_VARIANT,
            ),
        ]
        if savings:
            lines.append(ft.Text(
                f"Saves {format_money(savings)} in interest",
                size=fs(13),
                weight=ft.FontWeight.W_600,
                color=INCOME_COLOR,
            ))
        return ft.Container(
            content=ft.Column(lines, spacing=4),
            padding=16,
            border_radius=RADIUS,
            bgcolor=ft.Colors.SURFACE_CONTAINER_LOW,
            border=(
                ft.Border.all(2, SEED_COLOR)
                if selected
                else ft.Border.all(1, ft.Colors.OUTLINE_VARIANT)
            ),
            on_click=lambda _, strategy=plan.strategy: self._select_strategy(strategy),
            col={"xs": 12, "md": 6},
        )

    def _comparison_table(self) -> ft.Column:
        widths = [110, 140, 90, 120, 120]
        rows = [
            self._table_row(
                ["Extra", "Debt-free", "Months", "Interest", "Paid"], widths, header=True
            )
        ]
        for plan in self.ctx.service.debt_extra_comparison(
            self.strategy, comparison_amounts(self.custom_text)
        ):
            debt_free = month_label(plan.debt_free_month) if plan.debt_free_month else "—"
            rows.append(self._table_row(
                [
                    format_money(plan.extra_cents),
                    debt_free,
                    str(plan.months),
                    format_money(plan.total_interest_cents),
                    format_money(plan.total_paid_cents),
                ],
                widths,
                highlight=plan.extra_cents == self.extra_cents,
            ))
        return ft.Column(rows, spacing=2)

    @staticmethod
    def _table_row(
        cells: list[str],
        widths: list[int],
        *,
        header: bool = False,
        highlight: bool = False,
        numeric_from: int = 1,
    ) -> ft.Container:
        texts = [
            ft.Text(
                cell,
                size=fs(13),
                width=width,
                weight=ft.FontWeight.W_600 if header else None,
                color=ft.Colors.ON_SURFACE_VARIANT if header else None,
                text_align=ft.TextAlign.RIGHT if index >= numeric_from else ft.TextAlign.LEFT,
            )
            for index, (cell, width) in enumerate(zip(cells, widths))
        ]
        return ft.Container(
            content=ft.Row(texts, spacing=GAP),
            padding=ft.Padding.symmetric(horizontal=8, vertical=6),
            border_radius=8,
            bgcolor=ft.Colors.with_opacity(0.08, SEED_COLOR) if highlight else None,
        )

    def _yearly_table(self, plan: DebtPlan) -> ft.Column:
        widths = [70, 130, 110, 110, 130]
        rows = [
            self._table_row(
                ["Year", "Starting balance", "Interest", "Debt paid", "Ending balance"],
                widths,
                header=True,
            )
        ]
        year: str | None = None
        starting = interest = paid = ending = 0
        previous_ending: int | None = None
        for row in plan.schedule:
            row_year = shift_month(self._start_month, row.month - 1)[:4]
            if row_year != year:
                if year is not None:
                    rows.append(self._year_row(year, starting, interest, paid, ending, widths))
                year = row_year
                starting = (
                    previous_ending
                    if previous_ending is not None
                    else row.balance_cents - row.interest_cents + row.paid_cents
                )
                interest = paid = 0
            interest += row.interest_cents
            paid += row.paid_cents
            ending = row.balance_cents
            previous_ending = ending
        if year is not None:
            rows.append(self._year_row(year, starting, interest, paid, ending, widths))
        return ft.Column(rows, spacing=2)

    def _year_row(
        self, year: str, starting: int, interest: int, paid: int, ending: int, widths: list[int]
    ) -> ft.Container:
        return self._table_row(
            [
                year,
                format_money(starting),
                format_money(interest),
                format_money(paid),
                format_money(ending),
            ],
            widths,
        )

    def _monthly_table(self, plan: DebtPlan) -> ft.Column:
        widths = [130, 110, 110, 130]
        rows = [
            self._table_row(["Month", "Debt payment", "Interest", "Balance"], widths, header=True)
        ]
        for row in plan.schedule[: self.schedule_limit]:
            rows.append(self._table_row(
                [
                    month_label(shift_month(self._start_month, row.month - 1)),
                    format_money(row.paid_cents),
                    format_money(row.interest_cents),
                    format_money(row.balance_cents),
                ],
                widths,
            ))
        return ft.Column(rows, spacing=2)

    def _included_table(self, summaries: list[DebtSummary]) -> ft.Column:
        widths = [200, 130, 90, 120]
        rows: list[ft.Control] = [
            self._table_row(["Debt", "Balance", "APR", "Minimum"], widths, header=True)
        ]
        for summary in summaries:
            rows.append(self._table_row(
                [
                    summary.debt.name,
                    format_money(summary.balance_cents),
                    format_percent(summary.apr_bps),
                    format_money(summary.effective_minimum_cents),
                ],
                widths,
            ))
        return ft.Column(
            [
                section_title("Included in this plan"),
                ft.Text(
                    "The payoff order and combined schedule below cover every debt in this table.",
                    size=fs(13),
                    color=ft.Colors.ON_SURFACE_VARIANT,
                ),
                *rows,
            ],
            spacing=4,
        )

    def _payoff_table(self, summaries: list[DebtSummary]) -> ft.Column:
        widths = [200, 130, 120, 150]
        rows: list[ft.Control] = [
            self._table_row(["Debt", "Balance", "Minimum", "Payoff"], widths, header=True)
        ]
        for summary in summaries:
            month = self._payoff_months.get(summary.debt.id)
            payoff = month_label(shift_month(self._start_month, month - 1)) if month else "—"
            rows.append(self._table_row(
                [
                    summary.debt.name,
                    format_money(summary.balance_cents),
                    format_money(summary.effective_minimum_cents),
                    payoff,
                ],
                widths,
            ))
        if len(self._payoff_months) < len(summaries):
            rows.append(ft.Text(
                "— means the plan does not pay this debt off within 50 years.",
                size=fs(12),
                color=ft.Colors.ON_SURFACE_VARIANT,
            ))
        return ft.Column([section_title("Debt payoff order"), *rows], spacing=4)

    def _debt_card(
        self,
        summary: DebtSummary,
        payoff_month: int | None = None,
        *,
        velocity_target: bool = False,
    ) -> ft.Container:
        debt = summary.debt
        rate = f"APR {format_percent(summary.apr_bps)}"
        if summary.loan_type is not None:
            rate = f"{summary.loan_type.label} • {rate}"
        details = [
            ft.Text(f"{format_money(summary.balance_cents)} owed", size=fs(20), weight=ft.FontWeight.W_700),
            ft.Text(rate, size=fs(13), color=ft.Colors.ON_SURFACE_VARIANT),
            ft.Text(
                f"Minimum {format_money(summary.effective_minimum_cents)}"
                + (f" · {month_label(summary.statement_month)} statement"
                   if summary.minimum_from_statement else " · set manually"),
                size=fs(13),
                color=ft.Colors.ON_SURFACE_VARIANT,
            ),
        ]
        if summary.account_name:
            details.append(ft.Text(f"Linked to {summary.account_name}", size=fs(13), color=SEED_COLOR))
        if summary.property_name:
            details.append(
                ft.Text(
                    f"Property: {summary.property_name}", size=fs(13), color=SEED_COLOR
                )
            )
            if summary.property_value_cents is not None:
                equity = summary.property_value_cents - summary.balance_cents
                details.append(
                    ft.Text(
                        f"Market value {format_money(summary.property_value_cents)}  ·  "
                        f"Equity {format_money(equity)}",
                        size=fs(13),
                        color=ft.Colors.ON_SURFACE_VARIANT,
                    )
                )
        if debt.is_secured_by_property and debt.account_id is None:
            details.append(
                ft.Text(
                    "Not linked to a loan account, so this balance is not in net worth",
                    size=fs(12),
                    color=EXPENSE_COLOR,
                )
            )
        if payoff_month is not None:
            details.append(ft.Text(
                f"Payoff: {month_label(shift_month(self._start_month, payoff_month - 1))} at this plan",
                size=fs(13),
                color=SEED_COLOR,
            ))
        if debt.original_balance_cents:
            paid = min(1.0, max(0.0, 1 - summary.balance_cents / debt.original_balance_cents))
            details.append(ft.ProgressBar(value=paid, bar_height=8, border_radius=4))
            details.append(ft.Text(
                f"{round(paid * 100)}% paid down from {format_money(debt.original_balance_cents)}",
                size=fs(12), color=ft.Colors.ON_SURFACE_VARIANT,
            ))
        if summary.minimum_below_interest:
            details.append(ft.Text("The minimum does not cover monthly interest", size=fs(13), color=EXPENSE_COLOR))
        actions = [
            ft.FilledTonalButton(content="Log payment", icon=ft.Icons.PAYMENT,
                                 on_click=lambda _: DebtPaymentDialog(
                                     self.ctx, summary, self._plan_extra_for(summary.debt.id)
                                 ).open()),
            ft.OutlinedButton(content="History", icon=ft.Icons.HISTORY,
                              on_click=lambda _: DebtHistoryDialog(self.ctx, debt).open()),
            ft.OutlinedButton(content="Edit", icon=ft.Icons.EDIT_OUTLINED,
                              on_click=lambda _: DebtEditor(self.ctx, debt).open()),
            ft.IconButton(ft.Icons.DELETE_OUTLINE, tooltip="Delete", icon_color=EXPENSE_COLOR,
                          on_click=lambda _: self._confirm_delete(debt)),
        ]
        plan_label = "Velocity" if velocity_target else "Payoff"
        plan_color = SEED_COLOR if velocity_target else INCOME_COLOR
        plan_badge = ft.Container(
            content=ft.Text(plan_label, size=fs(11), weight=ft.FontWeight.W_600, color=plan_color),
            padding=ft.Padding.symmetric(horizontal=8, vertical=2),
            border_radius=999,
            bgcolor=ft.Colors.with_opacity(0.12, plan_color),
        )
        return card(
            ft.Column([ft.Text(debt.name, size=fs(16), weight=ft.FontWeight.W_600),
                       ft.Row(
                           [
                               ft.Text(
                                   debt.kind.label,
                                   size=fs(13),
                                   color=ft.Colors.ON_SURFACE_VARIANT,
                               ),
                               plan_badge,
                           ],
                           spacing=8,
                           wrap=True,
                       ),
                       *details,
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
        linkable = [
            a for a in ctx.repo.list_accounts()
            if a.kind in (AccountKind.CREDIT_CARD, AccountKind.LOAN) and a.id not in linked
        ]
        properties = [a for a in ctx.repo.list_accounts() if a.kind.is_real_estate]
        linked_account = (
            ctx.repo.get_account(debt.account_id)
            if debt is not None and debt.account_id is not None
            else None
        )
        initial_apr = (
            linked_account.apr_bps
            if linked_account is not None and linked_account.apr_bps is not None
            else (debt.apr_bps if debt is not None else None)
        )
        self.name = ft.TextField(
            label="Name",
            value=debt.name if debt else "",
            max_length=80,
            autofocus=True,
        )
        self.kind = ft.Dropdown(
            label="Kind",
            options=[ft.DropdownOption(key=k.value, text=k.label) for k in DebtKind],
            value=(debt.kind.value if debt else DebtKind.CREDIT_CARD.value),
        )
        self.kind.on_select = lambda _: self._kind_selected()
        self.balance = ft.TextField(label="Balance owed", prefix_icon=ft.Icons.ATTACH_MONEY,
                                    value=f"{debt.balance_cents / 100:.2f}" if debt else "")
        self.apr = ft.TextField(
            label="APR (%)",
            value=format_percent(initial_apr).rstrip("%") if initial_apr is not None else "",
        )
        self.apr_note = ft.Text(
            "APR comes from the linked account.",
            size=fs(12),
            color=ft.Colors.ON_SURFACE_VARIANT,
            visible=False,
        )
        self.minimum = ft.TextField(label="Minimum / month", prefix_icon=ft.Icons.ATTACH_MONEY,
                                    value=f"{debt.minimum_cents / 100:.2f}" if debt and debt.minimum_cents else "")
        self.minimum_note = ft.Text("", size=fs(12), color=ft.Colors.ON_SURFACE_VARIANT, visible=False)
        self.original = ft.TextField(label="Original balance", hint_text="Optional",
                                     prefix_icon=ft.Icons.ATTACH_MONEY,
                                     value=(f"{debt.original_balance_cents / 100:.2f}"
                                            if debt and debt.original_balance_cents is not None else ""))
        self.maturity = ft.TextField(
            label="Maturity date (YYYY-MM-DD)",
            hint_text="For example 2046-04-01",
            value=(
                debt.maturity_date.isoformat()
                if debt and debt.maturity_date is not None
                else ""
            ),
        )
        self.pi_payment = ft.TextField(
            label="Principal & interest payment (optional)",
            hint_text="Leave blank to calculate from balance, APR and maturity",
            prefix_icon=ft.Icons.ATTACH_MONEY,
            value=(
                f"{debt.pi_payment_cents / 100:.2f}"
                if debt and debt.pi_payment_cents is not None
                else ""
            ),
        )
        self.minimum_after = ft.TextField(
            label="Higher payment / month (optional)",
            hint_text="The payment after a temporary reduction",
            prefix_icon=ft.Icons.ATTACH_MONEY,
            value=(
                f"{debt.minimum_after_cents / 100:.2f}"
                if debt and debt.minimum_after_cents is not None
                else ""
            ),
        )
        self.minimum_after_date = ft.TextField(
            label="Higher payment starts (YYYY-MM-DD)",
            hint_text="For example 2029-01-01",
            value=(
                debt.minimum_after_date.isoformat()
                if debt and debt.minimum_after_date is not None
                else ""
            ),
        )
        self.link = ft.Dropdown(
            label="Link to credit-card or loan account",
            options=[ft.DropdownOption(key="none", text="None")]
            + [ft.DropdownOption(key=str(a.id), text=a.name) for a in linkable],
            value=str(debt.account_id) if debt and debt.account_id else "none",
        )
        self.link.on_select = lambda _: self._link_selected()
        self.property = ft.Dropdown(
            label="Property (optional)",
            options=[ft.DropdownOption(key="none", text="None linked")]
            + [ft.DropdownOption(key=str(a.id), text=a.name) for a in properties],
            value=(
                str(debt.property_account_id)
                if debt and debt.property_account_id
                else "none"
            ),
        )
        self.property_note = ft.Text(
            "Link the Real Estate account this mortgage is secured by, so the property's "
            "market value and equity appear with it."
            if properties
            else "Add a Real Estate account on the Accounts page to link this property.",
            size=fs(12),
            color=ft.Colors.ON_SURFACE_VARIANT,
        )
        self.error = ft.Text("", size=fs(13), color=EXPENSE_COLOR, visible=False)
        self.dialog = ft.AlertDialog(
            modal=True,
            title=ft.Text("Edit Debt" if debt else "New Debt"),
            content=ft.Container(width=460, content=ft.Column(
                [self.name, self.kind, self.balance, self.apr, self.apr_note, self.minimum,
                 self.minimum_note, self.original, self.maturity, self.pi_payment,
                 self.minimum_after, self.minimum_after_date, self.link,
                 self.property, self.property_note, self.error],
                spacing=14, tight=True, horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
                scroll=ft.ScrollMode.AUTO)),
            actions=[
                ft.TextButton(content="Cancel", on_click=lambda _: ctx.page.pop_dialog()),
                ft.FilledButton(content="Save", icon=ft.Icons.CHECK, on_click=self._save),
            ],
            actions_alignment=ft.MainAxisAlignment.END,
        )
        self._link_changed()

    def _kind_selected(self) -> None:
        self._property_visibility()
        self.dialog.update()

    def _link_selected(self) -> None:
        self._link_changed()
        self.dialog.update()

    def _property_visibility(self) -> None:
        """Only a mortgage can point at the property that secures it."""
        visible = self.kind.value == DebtKind.MORTGAGE.value
        self.property.visible = visible
        self.property_note.visible = visible
        self.maturity.visible = visible
        self.pi_payment.visible = visible
        self.minimum_after.visible = visible
        self.minimum_after_date.visible = visible

    def _stored_minimum_value(self) -> str:
        if self.debt is not None and self.debt.minimum_cents:
            return f"{self.debt.minimum_cents / 100:.2f}"
        return ""

    def _link_changed(self) -> None:
        account_id = int(self.link.value) if self.link.value not in (None, "", "none") else None
        account = self.ctx.repo.get_account(account_id) if account_id is not None else None
        is_loan = account is not None and account.kind is AccountKind.LOAN
        was_statement_driven = self.minimum.disabled
        self.balance.disabled = account is not None
        # A card link fixes the kind to credit card; a loan link lets the user choose the loan kind.
        self.kind.disabled = account is not None and not is_loan
        if account is not None:
            owed = next(
                (b.owed_cents for b in self.ctx.repo.account_balances() if b.account.id == account_id), 0
            )
            self.balance.value = f"{owed / 100:.2f}"
            if is_loan:
                if self.kind.value == DebtKind.CREDIT_CARD.value:
                    self.kind.value = DebtKind.PERSONAL.value
                # Drop a card statement minimum, but keep a value the user typed for this debt.
                if was_statement_driven:
                    self.minimum.value = self._stored_minimum_value()
                self.minimum.disabled = False
                self.minimum_note.visible = False
            else:
                self.kind.value = DebtKind.CREDIT_CARD.value
                status = self.ctx.service.current_minimum_payment(account_id, as_of=date.today())
                if status is not None:
                    self.minimum.value = f"{status.amount_cents / 100:.2f}"
                    self.minimum.disabled = True
                    self.minimum_note.value = f"Driven by the {month_label(status.month)} statement minimum"
                    self.minimum_note.visible = True
                else:
                    if was_statement_driven:
                        self.minimum.value = self._stored_minimum_value()
                    self.minimum.disabled = False
                    self.minimum_note.visible = False
        else:
            if was_statement_driven:
                self.minimum.value = self._stored_minimum_value()
            self.minimum.disabled = False
            self.minimum_note.visible = False
        if account is not None and account.apr_bps is not None:
            self.apr.value = format_percent(account.apr_bps).rstrip("%")
            self.apr.disabled = True
            self.apr_note.visible = True
        else:
            self.apr.disabled = False
            self.apr_note.visible = False
        self._property_visibility()

    def open(self) -> None:
        self.ctx.page.show_dialog(self.dialog)

    def _refresh_dialog(self) -> None:
        """Repaint the page (and its dialog overlay) after setting validation errors.

        Updating the page rather than the dialog itself works before the dialog has been
        attached, which is how the editor's tests drive ``_save`` directly.
        """
        self.ctx.page.update()

    def _save(self, _: ft.Event) -> None:
        for field in (
            self.name,
            self.balance,
            self.apr,
            self.minimum,
            self.original,
            self.maturity,
            self.pi_payment,
            self.minimum_after,
            self.minimum_after_date,
        ):
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
        if self.debt is not None and self.minimum.disabled:
            # A statement minimum drives the plan; keep the stored fallback untouched.
            minimum = self.debt.minimum_cents
        link = int(self.link.value) if self.link.value not in (None, "", "none") else None
        linked_account = self.ctx.repo.get_account(link) if link is not None else None
        if linked_account is not None and linked_account.kind is AccountKind.CREDIT_CARD:
            kind = DebtKind.CREDIT_CARD
        else:
            # A loan link keeps the chosen loan kind; the service rejects a credit-card kind.
            kind = DebtKind(self.kind.value)
        property_link = (
            int(self.property.value)
            if kind is DebtKind.MORTGAGE and self.property.value not in (None, "", "none")
            else None
        )
        maturity = None
        pi_payment = None
        minimum_after = None
        minimum_after_date = None
        if kind is DebtKind.MORTGAGE:
            try:
                maturity = (
                    parse_date(self.maturity.value)
                    if (self.maturity.value or "").strip()
                    else None
                )
            except ValueError as exc:
                self.maturity.error = str(exc)
                valid = False
            try:
                pi_payment = (
                    parse_signed_money(self.pi_payment.value)
                    if (self.pi_payment.value or "").strip()
                    else None
                )
                if pi_payment is not None and pi_payment <= 0:
                    raise ValueError("Payment must be greater than zero")
            except ValueError as exc:
                self.pi_payment.error = str(exc)
                valid = False
            try:
                minimum_after = (
                    parse_signed_money(self.minimum_after.value)
                    if (self.minimum_after.value or "").strip()
                    else None
                )
                if minimum_after is not None and minimum_after <= 0:
                    raise ValueError("Payment must be greater than zero")
            except ValueError as exc:
                self.minimum_after.error = str(exc)
                valid = False
            try:
                minimum_after_date = (
                    parse_date(self.minimum_after_date.value)
                    if (self.minimum_after_date.value or "").strip()
                    else None
                )
            except ValueError as exc:
                self.minimum_after_date.error = str(exc)
                valid = False
            if (minimum_after is None) != (minimum_after_date is None):
                (self.minimum_after if minimum_after is None else self.minimum_after_date).error = (
                    "Set both the higher payment and the date it starts"
                )
                valid = False
        if not valid:
            self._refresh_dialog()
            return
        try:
            if self.debt is not None:
                self.ctx.service.update_debt(
                    self.debt.id, name=name, kind=kind, balance_cents=balance, apr_bps=apr,
                    minimum_cents=minimum, original_balance_cents=original, account_id=link,
                    property_account_id=property_link, maturity_date=maturity,
                    pi_payment_cents=pi_payment, minimum_after_cents=minimum_after,
                    minimum_after_date=minimum_after_date,
                )
            else:
                self.ctx.service.create_debt(
                    name, kind, balance, apr, minimum,
                    original_balance_cents=original, account_id=link,
                    property_account_id=property_link, maturity_date=maturity,
                    pi_payment_cents=pi_payment, minimum_after_cents=minimum_after,
                    minimum_after_date=minimum_after_date,
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

    def __init__(self, ctx: AppContext, summary: DebtSummary, extra_cents: int = 0) -> None:
        self.ctx = ctx
        self.summary = summary
        debt = summary.debt
        loan_account = ctx.repo.get_account(debt.account_id) if debt.account_id is not None else None
        self.is_loan_payment = loan_account is not None and loan_account.kind is AccountKind.LOAN
        self.amount = ft.TextField(
            label="Scheduled payment" if self.is_loan_payment else "Amount",
            prefix_icon=ft.Icons.ATTACH_MONEY,
            keyboard_type=ft.KeyboardType.NUMBER,
            autofocus=True,
            on_submit=self._save,
            on_change=lambda _: self._total_changed(),
        )
        if self.is_loan_payment:
            linked_bill = next(
                (
                    bill
                    for bill in ctx.repo.list_bills()
                    if bill.active
                    and bill.pay_to_account_id == loan_account.id
                    and bill.amount_cents
                ),
                None,
            )
            if linked_bill is not None:
                self.amount.value = (
                    f"{ctx.repo.amount_due(linked_bill, linked_bill.next_due) / 100:.2f}"
                )
            elif summary.effective_minimum_cents:
                self.amount.value = f"{summary.effective_minimum_cents / 100:.2f}"
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
        self.date = date_field(
            ctx.page,
            "Payment date",
            date.today(),
            on_change=lambda _: self._total_changed(),
        )
        self.note = ft.TextField(label="Note", hint_text="Optional")
        all_categories = ctx.repo.list_categories()
        categories = [c for c in all_categories if c.type is TransactionType.EXPENSE]
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
        self.split = (
            LoanSplitFields(
                all_categories,
                lambda: self.amount.value,
                on_change=self._split_changed,
                interest_estimator=lambda: monthly_interest_cents(
                    summary.balance_cents, summary.apr_bps
                ),
                escrow_estimator=self._escrow_estimate,
                initial_extra_cents=extra_cents,
            )
            if self.is_loan_payment
            else None
        )
        self.error = ft.Text("", size=fs(13), color=EXPENSE_COLOR, visible=False)

        content: list[ft.Control] = [
            ft.Text(
                f"{format_money(summary.balance_cents)} owed.",
                size=fs(13),
                color=ft.Colors.ON_SURFACE_VARIANT,
            ),
            self.amount,
        ]
        if self.split is not None:
            content.append(self.split.control)
        content += [self.funding, self.date, self.note, self.category, self.error]
        self.dialog = ft.AlertDialog(
            modal=True,
            title=ft.Text(f"Log payment — {debt.name}"),
            content=ft.Container(width=460, content=ft.Column(
                content,
                spacing=14,
                tight=True,
                horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
                scroll=ft.ScrollMode.AUTO,
            )),
            actions=[
                ft.TextButton(content="Cancel", on_click=lambda _: ctx.page.pop_dialog()),
                ft.FilledButton(content="Record payment", icon=ft.Icons.PAYMENT, on_click=self._save),
            ],
            actions_alignment=ft.MainAxisAlignment.END,
        )

    def _total_changed(self) -> None:
        if self.split is not None:
            self.split.refresh()

    def _split_changed(self) -> None:
        self.error.visible = False

    def _payment_date(self) -> date:
        try:
            return parse_date(self.date.value)
        except ValueError:
            return date.today()

    def _escrow_estimate(self, total_cents: int) -> int | None:
        payment_date = self._payment_date()
        scheduled = self.summary.pi_payment_cents
        if (
            self.summary.minimum_after_cents is not None
            and self.summary.minimum_after_date is not None
            and payment_date >= self.summary.minimum_after_date
        ):
            scheduled = self.summary.minimum_after_cents
        elif (
            scheduled is None
            and 0 < self.summary.effective_minimum_cents < total_cents
        ):
            # A loan minimum that is below the cash payment is the current P&I.
            scheduled = self.summary.effective_minimum_cents
        return escrow_from_amortization(
            total_cents,
            self.summary.balance_cents,
            self.summary.apr_bps,
            self.summary.maturity_date,
            payment_date,
            pi_payment_cents=scheduled,
        )

    def open(self) -> None:
        self.ctx.page.show_dialog(self.dialog)

    def _save(self, _: ft.Event) -> None:
        self.amount.error = None
        self.date.error = None
        self.funding.error_text = None
        self.error.visible = False
        if self.split is not None:
            self.split.interest_category.error_text = None
            self.split.escrow_category.error_text = None
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
        note = (self.note.value or "").strip()
        if self.split is not None:
            try:
                interest, escrow, extra = self.split.amounts(amount)
            except ValueError as exc:
                self.error.value = str(exc)
                self.error.visible = True
                self.ctx.page.update()
                return
            interest_category, escrow_category = self.split.category_ids()
            if interest and interest_category is None:
                self.split.interest_category.error_text = "Choose a category"
                self.ctx.page.update()
                return
            if escrow and escrow_category is None:
                self.split.escrow_category.error_text = "Choose a category"
                self.ctx.page.update()
                return
            try:
                self.ctx.service.record_loan_payment(
                    debt.id,
                    int(self.funding.value),
                    amount,
                    interest,
                    escrow,
                    on_date,
                    interest_category,
                    escrow_category,
                    note,
                    extra_principal_cents=extra,
                )
            except RepositoryError as exc:
                self.error.value = str(exc)
                self.error.visible = True
                self.ctx.page.update()
                return
        else:
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
                    note,
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
                    ft.Text(f"{tx.occurred_on:%b %d, %Y}", size=fs(13), width=110),
                    ft.Text(format_money(tx.amount_cents), size=fs(13), width=90,
                            text_align=ft.TextAlign.RIGHT),
                    ft.Text(tx.account_name or "", size=fs(13), width=120),
                    ft.Text(source, size=fs(13), expand=True),
                ],
                spacing=GAP,
            ))
        body: list[ft.Control] = [
            ft.Column(rows, spacing=6, tight=True)
            if payments
            else ft.Text("No payments recorded yet.", size=fs(13), color=ft.Colors.ON_SURFACE_VARIANT)
        ]
        if debt.account_id is not None:
            body.append(ft.Text(
                "Only the principal portion is listed here. Interest and escrow are recorded as "
                "separate expense transactions, and payments made outside this dialog still "
                "change the balance but are not listed here.",
                size=fs(12),
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
