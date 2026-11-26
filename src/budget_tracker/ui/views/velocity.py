"""Velocity banking: a read-only cycled personal line-of-credit plan."""

from __future__ import annotations

import base64

import flet as ft

from ...formatting import (
    format_money,
    format_percent,
    month_label,
    parse_money,
    parse_percent_bps,
    parse_signed_money,
    shift_month,
)
from ...models import AccountKind, DebtKind, LoanType, TransactionType
from ...repository import RepositoryError
from ...services import DebtSummary, VelocitySettings
from ...velocity_planner import VelocityComparison, VelocityResult
from ..charts import (
    DEBT_SERIES_COLORS_DARK,
    DEBT_SERIES_COLORS_LIGHT,
    ChartColors,
    velocity_chart_png,
)
from ..components import card, confirm_dialog, section_title
from ..context import AppContext
from .transactions import TransactionEditor
from ..theme import (
    EXPENSE_COLOR,
    GAP,
    INCOME_COLOR,
    THEME_PRESET_SETTING,
    build_theme,
    fs,
    text_scale,
)

VELOCITY_CHART_HEIGHT = 260
VELOCITY_CHART_MIN_WIDTH = 420
CHART_HOVER_COLUMNS = 120
DEFAULT_PAGE_WIDTH = 1320
RAIL_WIDTH = 120
PAGE_PADDING = 64
DIVIDER_WIDTH = 1
CARD_PADDING = 40


class VelocityPanel:
    """The Debts-page velocity card, including its empty and configured states."""

    def __init__(self, ctx: AppContext) -> None:
        self.ctx = ctx
        self.control = ft.Column(spacing=GAP)
        self.refresh()

    def refresh(self) -> None:
        self.control.controls = self._render()

    def _render(self) -> list[ft.Control]:
        settings = self.ctx.service.velocity_settings()
        if settings is None:
            return [self._setup_card()]
        targets = self._selected_targets(settings)
        if not targets:
            return [self._setup_card(
                "None of the saved velocity targets are active credit cards, auto loans or "
                "personal loans. Choose another target."
            )]
        line = self.ctx.service.velocity_line(settings)
        if line is None:
            return [self._setup_card(
                "The linked PLOC account is missing or is not a Revolving Loan account. "
                "Edit the plan and link the account again."
            )]
        comparison = self.ctx.service.velocity_comparison(settings)
        if comparison is None:
            return [self._setup_card("That velocity plan could not be calculated.")]
        return [self._card(settings, targets, comparison, line)]

    def _selected_targets(self, settings: VelocitySettings) -> list[DebtSummary]:
        summaries = {
            summary.debt.id: summary for summary in self.ctx.service.list_debt_summaries()
        }
        targets: list[DebtSummary] = []
        for target_id in settings.target_debt_ids:
            summary = summaries.get(target_id)
            if (
                summary is not None
                and summary.balance_cents > 0
                and summary.debt.kind
                in (DebtKind.CREDIT_CARD, DebtKind.AUTO, DebtKind.PERSONAL)
            ):
                targets.append(summary)
        return targets

    @staticmethod
    def _targets(summaries: list[DebtSummary]) -> list[DebtSummary]:
        return [
            summary
            for summary in summaries
            if summary.balance_cents > 0
            and summary.debt.kind
            in (DebtKind.CREDIT_CARD, DebtKind.AUTO, DebtKind.PERSONAL)
        ]

    def _setup_card(self, message: str = "") -> ft.Container:
        targets = self._targets(self.ctx.service.list_debt_summaries())
        controls: list[ft.Control] = [
            ft.Text("Velocity banking", size=fs(16), weight=ft.FontWeight.W_600),
            ft.Text(
                "Use a personal line of credit in repeating chunks against one or more credit "
                "cards, auto loans or personal loans. The line returns to zero between cycles; "
                "this is a read-only estimate.",
                size=fs(13),
                color=ft.Colors.ON_SURFACE_VARIANT,
            ),
        ]
        if message:
            controls.append(ft.Text(message, size=fs(13), color=EXPENSE_COLOR))
        elif not targets:
            controls.append(ft.Text(
                "Add a credit card, auto loan or personal loan with a balance to set up a plan.",
                size=fs(13),
                color=ft.Colors.ON_SURFACE_VARIANT,
            ))
        actions: list[ft.Control] = []
        if targets:
            actions.append(ft.FilledTonalButton(
                content=ft.Text("Set up velocity banking"),
                icon=ft.Icons.ACCOUNT_BALANCE_WALLET,
                on_click=lambda _: self._open_dialog(),
            ))
        if message:
            actions.append(ft.TextButton(content="Clear plan", on_click=lambda _: self._clear()))
        if actions:
            controls.append(ft.Row(actions, spacing=8, wrap=True))
        return card(ft.Column(controls, spacing=10), col=12)

    def _open_dialog(self) -> None:
        VelocityPlanDialog(self.ctx, self.ctx.service.velocity_settings()).open()

    def _record_transfer(self, *, draw: bool) -> None:
        settings = self.ctx.service.velocity_settings()
        if settings is None or settings.loc_account_id is None:
            self.ctx.notify("Link the Personal LOC account in the velocity setup first", error=True)
            return
        account = self.ctx.repo.get_account(settings.loc_account_id)
        if account is None or account.kind is not AccountKind.LOAN:
            self.ctx.notify("The linked PLOC account is missing", error=True)
            return
        accounts = self.ctx.repo.list_accounts()
        checking = next(
            (candidate for candidate in accounts if candidate.kind is AccountKind.CHECKING),
            next((candidate for candidate in accounts if not candidate.kind.is_liability), None),
        )
        if checking is None:
            self.ctx.notify("Add a Checking or other cash account for the transfer", error=True)
            return
        amount = settings.chunk_cents
        if draw:
            comparison = self.ctx.service.velocity_comparison(settings)
            if comparison is not None:
                amount = next(
                    (
                        row.chunk_cents
                        for row in comparison.velocity.schedule
                        if row.chunk_cents > 0
                    ),
                    amount,
                )
            description = "PLOC draw"
            from_id, to_id = account.id, checking.id
        else:
            line = self.ctx.service.velocity_line(settings)
            if line is not None and line[1] > 0:
                amount = min(settings.sweep_cents, line[1])
            description = "PLOC sweep"
            from_id, to_id = checking.id, account.id
        TransactionEditor(
            self.ctx,
            None,
            preset_type=TransactionType.TRANSFER,
            preset_account_id=from_id,
            preset_to_account_id=to_id,
            preset_amount_cents=amount,
            preset_description=description,
        ).open()

    def _clear(self) -> None:
        def clear() -> None:
            self.ctx.service.clear_velocity_settings()
            self.refresh()
            self.ctx.page.update()
            self.ctx.notify("Velocity banking plan cleared")

        confirm_dialog(
            self.ctx.page,
            "Clear velocity plan?",
            "The saved line-of-credit setup will be removed. No account or transaction changes.",
            clear,
        )

    def _card(
        self,
        settings: VelocitySettings,
        targets: list[DebtSummary],
        comparison: VelocityComparison,
        line: tuple[str | None, int, int, int],
    ) -> ft.Container:
        velocity = comparison.velocity
        baseline = comparison.baseline
        net = comparison.net_interest_saved_cents
        line_name, line_balance, line_apr, line_limit = line
        warnings: list[str] = []
        if any(line_apr >= target.apr_bps for target in targets):
            warnings.append(
                "The PLOC rate is at or above at least one target APR; the plan relies on "
                "acceleration, not a rate arbitrage for that debt."
            )
        if any(target.minimum_below_interest for target in targets):
            warnings.append("At least one target minimum does not cover its monthly interest.")
        if not velocity.finished:
            warnings.append("Not paid off within 50 years with this chunk and sweep.")
        if velocity.max_loc_balance_cents > line_limit:
            warnings.append(
                "The PLOC balance exceeds its limit. Increase the sweep or reduce the chunk."
            )
        if velocity.cycles == 0:
            warnings.append(
                "No chunk was drawn. Check the credit limit, current PLOC balance and reserve."
            )
        if not baseline.finished:
            warnings.append("The same cash flow paid directly to the target does not finish.")

        net_color = (
            INCOME_COLOR
            if net is not None and net >= 0
            else EXPENSE_COLOR
            if net is not None
            else ft.Colors.ON_SURFACE_VARIANT
        )
        metrics = ft.ResponsiveRow(
            [
                self._metric(
                    "All targets paid" if len(targets) > 1 else "Target paid",
                    self._month(velocity.target_payoff_month),
                ),
                self._metric(
                    "Net interest saved",
                    format_money(net) if net is not None else "—",
                    net_color,
                ),
                self._metric("PLOC interest", format_money(velocity.total_loc_interest_cents)),
                self._metric(
                    "Cycles / peak line",
                    f"{velocity.cycles} · {format_money(velocity.max_loc_balance_cents)}",
                ),
            ],
            spacing=8,
            run_spacing=8,
        )

        header_actions: list[ft.Control] = []
        if line_name:
            header_actions.extend([
                ft.OutlinedButton(
                    content="Record draw",
                    icon=ft.Icons.ARROW_DOWNWARD,
                    tooltip="Prefill a transfer from the PLOC to Checking",
                    on_click=lambda _: self._record_transfer(draw=True),
                ),
                ft.OutlinedButton(
                    content="Record sweep",
                    icon=ft.Icons.ARROW_UPWARD,
                    tooltip="Prefill a transfer from Checking to the PLOC",
                    on_click=lambda _: self._record_transfer(draw=False),
                ),
            ])
        header_actions.extend([
            ft.TextButton(content="Clear", on_click=lambda _: self._clear()),
            ft.OutlinedButton(
                content="Edit",
                icon=ft.Icons.EDIT_OUTLINED,
                on_click=lambda _: self._open_dialog(),
            ),
        ])
        controls: list[ft.Control] = [
            ft.ResponsiveRow(
                [
                    ft.Column(
                        [
                            ft.Text("Velocity banking", size=fs(16), weight=ft.FontWeight.W_600),
                            ft.Text(
                                "Cycled personal line of credit · read-only estimate",
                                size=fs(12),
                                color=ft.Colors.ON_SURFACE_VARIANT,
                            ),
                        ],
                        spacing=2,
                        col={"xs": 12, "md": 8},
                    ),
                    ft.Container(
                        content=ft.Row(
                            header_actions,
                            spacing=8,
                            wrap=True,
                            alignment=ft.MainAxisAlignment.END,
                        ),
                        col={"xs": 12, "md": 4},
                    ),
                ],
                spacing=8,
                run_spacing=8,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
            ft.Text(
                (
                    f"Target {targets[0].debt.name}: {format_money(targets[0].balance_cents)} "
                    f"at {format_percent(targets[0].apr_bps)} APR"
                    if len(targets) == 1
                    else f"Targets ({len(targets)}): "
                    + ", ".join(target.debt.name for target in targets)
                ),
                size=fs(13),
            ),
            ft.Text(
                (
                    f"PLOC linked to {line_name}: {format_money(line_balance)} owed · "
                    f"{format_percent(line_apr)} APR · "
                    f"{format_money(line_limit)} limit · "
                    f"{format_money(settings.reserve_cents)} credit reserve"
                    if line_name
                    else f"PLOC {format_money(line_limit)} limit · "
                    f"{format_percent(line_apr)} variable · "
                    f"{format_money(line_balance)} drawn · "
                    f"{format_money(settings.reserve_cents)} credit reserve"
                ),
                size=fs(13),
                color=ft.Colors.ON_SURFACE_VARIANT,
            ),
            ft.Text(
                (
                    "APR, current owed and any account credit limit are read from the linked "
                    "account; draws and sweeps update the plan automatically. Use Record draw "
                    "or Record sweep to prefill the transfer, then confirm it in the "
                    "transaction dialog."
                    if line_name
                    else "Not linked to an account; update Current PLOC amount owed after a "
                    "draw or sweep."
                ),
                size=fs(11),
                color=ft.Colors.ON_SURFACE_VARIANT,
            ),
            ft.Text(
                f"Chunk {format_money(settings.chunk_cents)} per cycle · "
                f"sweep {format_money(settings.sweep_cents)} per month · "
                f"line cleared {self._month(velocity.line_clear_month)} · "
                f"{velocity.months} months",
                size=fs(12),
                color=ft.Colors.ON_SURFACE_VARIANT,
            ),
        ]
        if warnings:
            controls.append(ft.Container(
                content=ft.Column(
                    [ft.Text(warning, size=fs(12), color=EXPENSE_COLOR) for warning in warnings],
                    spacing=3,
                ),
                padding=ft.Padding.symmetric(horizontal=10, vertical=7),
                border_radius=9,
                bgcolor=ft.Colors.with_opacity(0.08, EXPENSE_COLOR),
            ))
        controls.extend([
            metrics,
            *self._target_rows(targets, velocity),
            self._chart(settings, targets, velocity),
            self._baseline_line(comparison),
            self._break_even_line(comparison),
            section_title("Variable-rate scenarios"),
            ft.Text(
                "Monthly estimate at the current rate, +2 points and +4 points. A variable "
                "PLOC can move outside these cases.",
                size=fs(12),
                color=ft.Colors.ON_SURFACE_VARIANT,
            ),
            self._scenarios_table(settings, baseline),
            ft.Text(
                "The line and target are simulation-only here; the plan does not create a PLOC "
                "account or record transactions. Escrow is not part of this plan.",
                size=fs(11),
                color=ft.Colors.ON_SURFACE_VARIANT,
            ),
        ])
        return card(ft.Column(controls, spacing=10), col=12)

    @staticmethod
    def _metric(label: str, value: str, color: str | None = None) -> ft.Container:
        return ft.Container(
            content=ft.Column(
                [
                    ft.Text(label, size=fs(11), color=ft.Colors.ON_SURFACE_VARIANT),
                    ft.Text(value, size=fs(16), weight=ft.FontWeight.W_600, color=color),
                ],
                spacing=2,
            ),
            padding=10,
            border_radius=10,
            bgcolor=ft.Colors.SURFACE_CONTAINER,
            col={"xs": 6, "md": 3},
        )

    def _month(self, month: int | None) -> str:
        if not month:
            return "—"
        return month_label(shift_month(self.ctx.service.current_month(), month))

    def _baseline_line(self, comparison: VelocityComparison) -> ft.Text:
        baseline = comparison.baseline
        net = comparison.net_interest_saved_cents
        if not baseline.finished:
            return ft.Text(
                "Direct-payment comparison: does not finish with this cash flow.",
                size=fs(12),
                color=EXPENSE_COLOR,
            )
        payoff = self._month(baseline.payoff_month)
        target_word = "targets" if len(comparison.velocity.target_results) > 1 else "target"
        if net is None:
            return ft.Text(
                f"Direct-payment comparison: {format_money(baseline.total_interest_cents)} "
                f"interest, {target_word} paid {payoff}.",
                size=fs(12),
                color=ft.Colors.ON_SURFACE_VARIANT,
            )
        if net >= 0:
            return ft.Text(
                f"Same cash flow without the line: {format_money(baseline.total_interest_cents)} "
                f"interest, {target_word} paid {payoff}. Net savings {format_money(net)}.",
                size=fs(12),
                color=INCOME_COLOR,
            )
        return ft.Text(
            f"Same cash flow without the line: {format_money(baseline.total_interest_cents)} "
            f"interest, {target_word} paid {payoff}. Net cost {format_money(-net)}.",
            size=fs(12),
            color=EXPENSE_COLOR,
        )

    def _target_rows(
        self, targets: list[DebtSummary], velocity: VelocityResult
    ) -> list[ft.Control]:
        if len(targets) <= 1:
            return []
        by_id = {row.target_id: row for row in velocity.target_results}
        widths = [160, 120, 100, 120]
        rows: list[ft.Control] = [
            section_title("Target payoff"),
            self._table_row(["Target", "Balance", "APR", "Payoff"], widths, header=True),
        ]
        for target in targets:
            result = by_id.get(target.debt.id)
            rows.append(self._table_row(
                [
                    target.debt.name,
                    format_money(target.balance_cents),
                    format_percent(target.apr_bps),
                    self._month(result.payoff_month if result else None),
                ],
                widths,
            ))
        return [ft.Column(rows, spacing=2)]

    @staticmethod
    def _break_even_line(comparison: VelocityComparison) -> ft.Text:
        if comparison.break_even_loc_apr_bps is None:
            return ft.Text(
                "Break-even PLOC rate: not reached within the 0–100% search range.",
                size=fs(12),
                color=ft.Colors.ON_SURFACE_VARIANT,
            )
        target_word = (
            "targets" if len(comparison.velocity.target_results) > 1 else "target"
        )
        return ft.Text(
            f"Break-even PLOC rate: {format_percent(comparison.break_even_loc_apr_bps)}. "
            f"Above this rate, paying the {target_word} directly is cheaper.",
            size=fs(12),
            color=ft.Colors.ON_SURFACE_VARIANT,
        )

    def _chart(
        self, settings: VelocitySettings, targets: list[DebtSummary], velocity: VelocityResult
    ) -> ft.Column:
        target_label = "Targets" if len(targets) > 1 else "Target"
        target_values = [sum(target.balance_cents for target in targets)] + [
            row.target_balance_cents for row in velocity.schedule
        ]
        line_values = [settings.loc_balance_cents] + [
            row.loc_balance_cents for row in velocity.schedule
        ]
        keys = [
            shift_month(self.ctx.service.current_month(), index)
            for index in range(len(target_values))
        ]
        colors, dark = self._chart_colors()
        palette = DEBT_SERIES_COLORS_DARK if dark else DEBT_SERIES_COLORS_LIGHT
        target_color, line_color = palette[0], palette[1]
        png = velocity_chart_png(
            target_values,
            line_values,
            keys,
            width=self._chart_width(),
            height=VELOCITY_CHART_HEIGHT,
            colors=colors,
            text_scale=text_scale(),
            target_color=target_color,
            line_color=line_color,
            target_label=target_label,
        )
        chart = ft.Container(
            height=VELOCITY_CHART_HEIGHT,
            width=self._chart_width(),
            image=ft.DecorationImage(src=self._png_source(png), fit=ft.BoxFit.CONTAIN),
            content=self._hover_overlay(target_values, line_values, keys),
        )
        legend = ft.Row(
            [
                ft.Row([
                    ft.Container(width=9, height=9, border_radius=2, bgcolor=target_color),
                    ft.Text(target_label, size=fs(12)),
                ], spacing=5),
                ft.Row([
                    ft.Container(width=9, height=9, border_radius=2, bgcolor=line_color),
                    ft.Text("PLOC", size=fs(12)),
                ], spacing=5),
            ],
            spacing=14,
        )
        return ft.Column([ft.Row([chart], spacing=0), legend], spacing=5)

    def _hover_overlay(
        self, target_values: list[int], line_values: list[int], keys: list[str]
    ) -> ft.Row:
        columns: list[ft.Control] = []
        count = min(len(target_values), len(line_values), len(keys))
        stride = max(1, (count + CHART_HOVER_COLUMNS - 1) // CHART_HOVER_COLUMNS)
        for index in range(0, count, stride):
            tip = (
                f"{month_label(keys[index])}\n"
                f"Target {format_money(target_values[index])}\n"
                f"PLOC {format_money(line_values[index])}"
            )
            columns.append(ft.Container(
                expand=True,
                height=VELOCITY_CHART_HEIGHT,
                bgcolor=ft.Colors.TRANSPARENT,
                tooltip=tip,
            ))
        return ft.Row(columns, spacing=0, expand=True)

    def _scenarios_table(self, settings: VelocitySettings, baseline) -> ft.Column:
        widths = [120, 140, 120, 130]
        rows: list[ft.Control] = [
            self._table_row(
                ["PLOC rate", "Target paid", "PLOC interest", "Net saved"],
                widths,
                header=True,
            )
        ]
        for apr, result in self.ctx.service.velocity_scenarios(settings):
            payoff = self._month(result.target_payoff_month)
            net = (
                baseline.total_interest_cents - result.total_interest_cents
                if result.finished and baseline.finished
                else None
            )
            rows.append(self._table_row(
                [
                    format_percent(apr),
                    payoff,
                    format_money(result.total_loc_interest_cents),
                    format_money(net) if net is not None else "—",
                ],
                widths,
                highlight=apr == settings.loc_apr_bps,
            ))
        return ft.Column(rows, spacing=2)

    @staticmethod
    def _table_row(
        cells: list[str],
        widths: list[int],
        *,
        header: bool = False,
        highlight: bool = False,
    ) -> ft.Container:
        texts = [
            ft.Text(
                cell,
                size=fs(12),
                width=width,
                weight=ft.FontWeight.W_600 if header else None,
                color=ft.Colors.ON_SURFACE_VARIANT if header else None,
                text_align=ft.TextAlign.RIGHT if index else ft.TextAlign.LEFT,
            )
            for index, (cell, width) in enumerate(zip(cells, widths))
        ]
        return ft.Container(
            content=ft.Row(texts, spacing=8),
            padding=ft.Padding.symmetric(horizontal=6, vertical=4),
            border_radius=8,
            bgcolor=ft.Colors.with_opacity(0.08, ft.Colors.PRIMARY) if highlight else None,
        )

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
        page_width = self._page_width()
        content = max(1.0, page_width - RAIL_WIDTH - DIVIDER_WIDTH - PAGE_PADDING)
        return max(VELOCITY_CHART_MIN_WIDTH, round(max(1.0, content - CARD_PADDING)))

    def _chart_colors(self) -> tuple[ChartColors, bool]:
        mode = getattr(self.ctx.page, "theme_mode", None)
        dark = mode.lower().endswith("dark") if isinstance(mode, str) else mode == ft.ThemeMode.DARK
        preset = "indigo"
        get_setting = getattr(self.ctx.repo, "get_setting", None)
        if get_setting is not None:
            try:
                preset = get_setting(THEME_PRESET_SETTING) or "indigo"
            except Exception:  # pragma: no cover
                preset = "indigo"
        scheme = build_theme(dark=dark, preset=preset).color_scheme
        return ChartColors(
            primary=scheme.primary,
            grid=scheme.outline_variant,
            text=scheme.on_surface,
            zero=scheme.outline,
            lowest=scheme.secondary,
            negative=scheme.error,
            surface=scheme.surface_container_low,
        ), dark

    @staticmethod
    def _png_source(png: bytes) -> str:
        return "data:image/png;base64," + base64.b64encode(png).decode("ascii")


class VelocityPlanDialog:
    def __init__(self, ctx: AppContext, settings: VelocitySettings | None) -> None:
        self.ctx = ctx
        self.settings = settings
        self.targets = VelocityPanel._targets(ctx.service.list_debt_summaries())
        selected_ids = set(settings.target_debt_ids) if settings is not None else set()
        if not selected_ids and self.targets:
            selected_ids = {self.targets[0].debt.id}
        self.target_checks: list[tuple[DebtSummary, ft.Checkbox]] = [
            (
                summary,
                ft.Checkbox(
                    label=(
                        f"{summary.debt.name} · {summary.debt.kind.label} · "
                        f"{format_money(summary.balance_cents)} · "
                        f"{format_percent(summary.apr_bps)}"
                    ),
                    value=summary.debt.id in selected_ids,
                    tooltip="Include this credit card, auto loan or personal loan in the velocity plan.",
                    on_change=lambda _: self._selection_changed(),
                ),
            )
            for summary in self.targets
        ]
        self.target_heading = ft.Text(
            "Target debts (choose one or more)",
            size=fs(13),
            weight=ft.FontWeight.W_600,
        )
        self.target_list = ft.Column(
            [checkbox for _, checkbox in self.target_checks],
            spacing=2,
        )
        balances = {balance.account.id: balance for balance in ctx.repo.account_balances()}
        self.loc_accounts = [
            account
            for account in ctx.repo.list_accounts()
            if account.kind is AccountKind.LOAN
            and account.loan_type in (None, LoanType.REVOLVING)
        ]
        self.loc_account = ft.Dropdown(
            label="PLOC account",
            tooltip="Link the Revolving Loan account that holds the PLOC balance. The plan "
            "then reads its APR and current amount owed.",
            options=[
                ft.DropdownOption(key="none", text="Not linked — enter APR and amount manually")
            ] + [
                ft.DropdownOption(
                    key=str(account.id),
                    text=(
                        f"{account.name} · "
                        f"{format_money(balances[account.id].owed_cents if account.id in balances else 0)} "
                        f"owed"
                        + (
                            f" · {format_percent(account.apr_bps)} APR"
                            if account.apr_bps is not None
                            else " · no APR set"
                        )
                    ),
                )
                for account in self.loc_accounts
            ],
            value=(
                str(settings.loc_account_id)
                if settings is not None
                and settings.loc_account_id is not None
                and any(account.id == settings.loc_account_id for account in self.loc_accounts)
                else "none"
            ),
            on_select=lambda _: self._loc_account_changed(),
        )
        self.loc_note = ft.Text(
            "Link a Revolving Loan account to read its live APR and amount owed.",
            size=fs(11),
            color=ft.Colors.ON_SURFACE_VARIANT,
        )
        self.limit = ft.TextField(
            label="PLOC credit limit",
            tooltip="The full PLOC credit limit. The chunk is capped by the limit minus the current balance and reserve.",
            prefix_icon=ft.Icons.ATTACH_MONEY,
            value=f"{settings.loc_limit_cents / 100:.2f}" if settings else "",
        )
        self.apr = ft.TextField(
            label="PLOC APR (%)",
            tooltip="Current variable APR. The plan also runs +2 and +4 point stress cases.",
            value=format_percent(settings.loc_apr_bps).rstrip("%") if settings else "",
            hint_text="For example 3.99",
        )
        self.loc_balance = ft.TextField(
            label="Current PLOC amount owed",
            tooltip="Amount already drawn. The plan pays this down before drawing a new chunk.",
            prefix_icon=ft.Icons.ATTACH_MONEY,
            value=f"{settings.loc_balance_cents / 100:.2f}" if settings else "0.00",
        )
        self.chunk = ft.TextField(
            label="Chunk per cycle",
            tooltip="Partial amount applied to the target each cycle. The next chunk starts only after the line reaches zero.",
            prefix_icon=ft.Icons.ATTACH_MONEY,
            value=f"{settings.chunk_cents / 100:.2f}" if settings else "",
        )
        self.sweep = ft.TextField(
            label="Monthly sweep to the PLOC",
            tooltip="Monthly cash paid to the PLOC. It must cover line interest and clear the line between chunks.",
            prefix_icon=ft.Icons.ATTACH_MONEY,
            value=f"{settings.sweep_cents / 100:.2f}" if settings else "",
        )
        self.reserve = ft.TextField(
            label="Keep this much credit unused",
            tooltip="Credit kept unused. The plan never draws the final reserve portion of the limit.",
            prefix_icon=ft.Icons.ATTACH_MONEY,
            value=f"{settings.reserve_cents / 100:.2f}" if settings else "0.00",
        )
        self.info = ft.Text(
            "The PLOC rate is treated as variable; the plan also shows +2 and +4 point cases.",
            size=fs(12),
            color=ft.Colors.ON_SURFACE_VARIANT,
        )
        self.error = ft.Text("", size=fs(13), color=EXPENSE_COLOR, visible=False)
        self.dialog = ft.AlertDialog(
            modal=True,
            title=ft.Text("Velocity banking setup"),
            content=ft.Container(
                width=480,
                content=ft.Column(
                    [
                        self.target_heading,
                        self.target_list,
                        self.loc_account,
                        self.loc_note,
                        self.limit,
                        self.apr,
                        self.loc_balance,
                        self.chunk,
                        self.sweep,
                        self.reserve,
                        self.info,
                        self.error,
                    ],
                    spacing=12,
                    tight=True,
                    horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
                    scroll=ft.ScrollMode.AUTO,
                ),
            ),
            actions=[
                ft.TextButton(content="Cancel", on_click=lambda _: ctx.page.pop_dialog()),
                ft.FilledButton(
                    content="Save plan",
                    icon=ft.Icons.CHECK,
                    on_click=self._save,
                ),
            ],
            actions_alignment=ft.MainAxisAlignment.END,
        )
        self._loc_account_changed()
        self._selection_changed()

    def _selected_loc_account(self):
        if self.loc_account.value in (None, "", "none"):
            return None
        return next(
            (
                account
                for account in self.loc_accounts
                if str(account.id) == str(self.loc_account.value)
            ),
            None,
        )

    def _loc_account_changed(self) -> None:
        account = self._selected_loc_account()
        if account is None:
            self.apr.disabled = False
            self.loc_balance.disabled = False
            self.loc_note.value = (
                "Not linked. Enter the PLOC APR and current amount owed manually."
            )
            return
        balance = next(
            (
                item.owed_cents
                for item in self.ctx.repo.account_balances()
                if item.account.id == account.id
            ),
            0,
        )
        self.apr.value = (
            format_percent(account.apr_bps).rstrip("%")
            if account.apr_bps is not None
            else ""
        )
        self.apr.disabled = True
        self.loc_balance.value = f"{balance / 100:.2f}"
        self.loc_balance.disabled = True
        if account.credit_limit_cents:
            self.limit.value = f"{account.credit_limit_cents / 100:.2f}"
            self.limit.disabled = True
        else:
            self.limit.disabled = False
        if account.apr_bps is None:
            self.loc_note.value = (
                f"Linked to {account.name}, but the account has no APR. Set the APR on "
                "Accounts first."
            )
        elif account.credit_limit_cents:
            self.loc_note.value = (
                f"Linked to {account.name}. APR, current owed and credit limit come from "
                "Accounts."
            )
        else:
            self.loc_note.value = (
                f"Linked to {account.name}. APR and current owed come from Accounts; the "
                "credit limit remains a plan input."
            )

    def _selection_changed(self) -> None:
        selected = [summary for summary, checkbox in self.target_checks if checkbox.value]
        if not selected:
            self.info.value = "Choose at least one target debt."
            return
        total = sum(summary.balance_cents for summary in selected)
        minimum = sum(summary.effective_minimum_cents for summary in selected)
        rates = ", ".join(format_percent(summary.apr_bps) for summary in selected)
        self.info.value = (
            f"{len(selected)} target{'s' if len(selected) != 1 else ''}: "
            f"{format_money(total)} total, {format_money(minimum)} total minimum, "
            f"APR {rates}. The PLOC rate is treated as variable; the plan also shows "
            "+2 and +4 point cases."
        )

    def _save(self, _: ft.Event) -> None:
        for field in (self.limit, self.apr, self.loc_balance, self.chunk, self.sweep, self.reserve):
            field.error = None
        self.error.visible = False
        selected = [summary for summary, checkbox in self.target_checks if checkbox.value]
        if not selected:
            self.error.value = "Choose at least one target debt."
            self.error.visible = True
            self.ctx.page.update()
            return
        target_ids = tuple(summary.debt.id for summary in selected)
        linked_account = self._selected_loc_account()
        valid = True
        try:
            limit = parse_money(self.limit.value)
            if limit <= 0:
                raise ValueError("Credit limit must be greater than zero")
        except ValueError as exc:
            self.limit.error = str(exc)
            valid = False
            limit = 0
        try:
            apr = parse_percent_bps(self.apr.value)
        except ValueError as exc:
            self.apr.error = str(exc)
            valid = False
            apr = 0
        try:
            current = parse_signed_money(self.loc_balance.value)
            if current < 0:
                raise ValueError("Current balance cannot be negative")
        except ValueError as exc:
            self.loc_balance.error = str(exc)
            valid = False
            current = 0
        try:
            chunk = parse_money(self.chunk.value)
            if chunk <= 0:
                raise ValueError("Chunk must be greater than zero")
        except ValueError as exc:
            self.chunk.error = str(exc)
            valid = False
            chunk = 0
        try:
            sweep = parse_money(self.sweep.value)
            if sweep <= 0:
                raise ValueError("Sweep must be greater than zero")
        except ValueError as exc:
            self.sweep.error = str(exc)
            valid = False
            sweep = 0
        try:
            reserve = parse_signed_money(self.reserve.value)
            if reserve < 0:
                raise ValueError("Reserve cannot be negative")
        except ValueError as exc:
            self.reserve.error = str(exc)
            valid = False
            reserve = 0
        if linked_account is not None:
            if linked_account.apr_bps is None:
                self.error.value = (
                    f"{linked_account.name} has no APR. Set its APR on Accounts first."
                )
                valid = False
            else:
                apr = linked_account.apr_bps
                current = next(
                    (
                        item.owed_cents
                        for item in self.ctx.repo.account_balances()
                        if item.account.id == linked_account.id
                    ),
                    0,
                )
                if linked_account.credit_limit_cents:
                    limit = linked_account.credit_limit_cents
                self.apr.error = None
                self.loc_balance.error = None
                self.limit.error = None
        if valid and chunk > max(0, limit - current - reserve):
            self.chunk.error = "Chunk cannot exceed available credit after the reserve"
            valid = False
        if not valid:
            self.error.visible = True
            self.error.value = "Check the highlighted fields."
            self.ctx.page.update()
            return
        settings = VelocitySettings(
            target_debt_ids=target_ids,
            loc_limit_cents=limit,
            loc_apr_bps=apr,
            loc_balance_cents=current,
            chunk_cents=chunk,
            sweep_cents=sweep,
            reserve_cents=reserve,
            loc_account_id=linked_account.id if linked_account is not None else None,
        )
        self.ctx.service.set_velocity_settings(settings)
        self.ctx.page.pop_dialog()
        self.ctx.notify("Velocity banking plan saved")
        self.ctx.data_changed()

    def open(self) -> None:
        if not self.targets:
            self.ctx.notify("Add a credit card, auto loan or personal loan first", error=True)
            return
        self.ctx.page.show_dialog(self.dialog)
