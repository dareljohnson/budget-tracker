"""Debts: track what is owed, link credit cards and plan the payoff."""

from __future__ import annotations

from datetime import date

import flet as ft

from ...debt_planner import DebtPlan, DebtStrategy
from ...formatting import (
    format_money,
    format_percent,
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
from ..components import card, confirm_dialog, date_field, empty_state, page_header, section_title
from ..context import AppContext
from ..theme import EXPENSE_COLOR, GAP, INCOME_COLOR, RADIUS, SEED_COLOR

EXTRA_PRESETS = [0, 5_000, 10_000, 20_000]


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


class DebtsView:
    def __init__(self, ctx: AppContext) -> None:
        self.ctx = ctx
        # Plain alignment, not CrossAxisAlignment.STRETCH: on Flet 1.1.2 a ResponsiveRow using
        # STRETCH without an explicit height collapses to zero height in this view's scrollable
        # body, so the cards (and the empty state) never appear. Cards size to their content.
        self.grid = ft.ResponsiveRow(spacing=GAP, run_spacing=GAP)
        self.plan_panel = ft.Column(spacing=GAP)
        self.extra_cents = 0
        self.custom_text = ""
        self.strategy = DebtStrategy.AVALANCHE
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
        summaries = self.ctx.service.list_debt_summaries()
        self.grid.controls = [
            self._debt_card(s, self._payoff_months.get(s.debt.id)) for s in summaries
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
        self.refresh()
        self.ctx.page.update()

    def _set_extra(self, cents: int) -> None:
        self.extra_cents = max(0, int(cents))
        self.schedule_limit = 24
        self.refresh()
        self.ctx.page.update()

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
        self.schedule_limit = 24
        self.refresh()
        self.ctx.page.update()

    def _show_more(self, _: ft.Event | None) -> None:
        self.schedule_limit += 24
        self.refresh()
        self.ctx.page.update()

    def _render_plan(self) -> None:
        summaries = self.ctx.service.list_debt_summaries()
        if not any(summary.balance_cents > 0 for summary in summaries):
            self._payoff_months = {}
            self.plan_panel.controls = [card(ft.Text("No debt to pay off."), col=12)]
            return

        self._start_month = self.ctx.service.current_month()
        self.last_start_month = self._start_month
        plans = self.ctx.service.debt_plans(self.extra_cents)
        current = plans[self.strategy]
        self._payoff_months = {debt.id: month for debt, month in current.payoff_order}
        included = [summary for summary in summaries if summary.balance_cents > 0]

        controls: list[ft.Control] = [section_title("Payoff plan")]
        warnings = [
            ft.Text(
                f"{summary.debt.name}: the minimum does not cover monthly interest",
                size=12,
                color=EXPENSE_COLOR,
            )
            for summary in summaries
            if summary.minimum_below_interest
        ]
        if not all(plan.finished for plan in plans.values()):
            warnings.append(
                ft.Text("Not paid off within 50 years at this payment.", size=12, color=EXPENSE_COLOR)
            )
        if self.extra_cents == 0 and all(
            summary.effective_minimum_cents == 0
            for summary in summaries
            if summary.balance_cents > 0
        ):
            warnings.append(ft.Text("No payment is being made.", size=12, color=EXPENSE_COLOR))
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
        controls.append(section_title("What extra buys"))
        controls.append(self._comparison_table())
        controls.append(self._included_table(included))
        controls.append(self._payoff_table(included))
        controls.append(section_title("Combined monthly schedule"))
        controls.append(ft.Text(
            "Payments and balances below are the total across every debt listed above.",
            size=12,
            color=ft.Colors.ON_SURFACE_VARIANT,
        ))
        controls.append(self._yearly_table(current))
        controls.append(self._monthly_table(current))
        if self.schedule_limit < len(current.schedule):
            controls.append(ft.TextButton(content="Show all months", on_click=self._show_more))
        self.plan_panel.controls = [card(ft.Column(controls, spacing=GAP), col=12)]

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
            ft.Text(plan.strategy.label, size=15, weight=ft.FontWeight.W_600),
            ft.Text(
                debt_free,
                size=18,
                weight=ft.FontWeight.W_700,
                color=SEED_COLOR if plan.finished else EXPENSE_COLOR,
            ),
            ft.Text(f"{plan.months} months", size=12, color=ft.Colors.ON_SURFACE_VARIANT),
            ft.Text(
                f"Covers {debt_count} debt{'s' if debt_count != 1 else ''}",
                size=12,
                color=ft.Colors.ON_SURFACE_VARIANT,
            ),
            ft.Text(f"Interest {format_money(plan.total_interest_cents)}", size=12),
            ft.Text(f"Paid {format_money(plan.total_paid_cents)}", size=12),
            ft.Text(
                f"First target: {plan.first_target.name if plan.first_target else '—'}",
                size=12,
                color=ft.Colors.ON_SURFACE_VARIANT,
            ),
        ]
        if savings:
            lines.append(ft.Text(
                f"Saves {format_money(savings)} in interest",
                size=12,
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
                size=12,
                width=width,
                weight=ft.FontWeight.W_600 if header else None,
                color=ft.Colors.ON_SURFACE_VARIANT if header else None,
                text_align=ft.TextAlign.RIGHT if index >= numeric_from else ft.TextAlign.LEFT,
            )
            for index, (cell, width) in enumerate(zip(cells, widths))
        ]
        return ft.Container(
            content=ft.Row(texts, spacing=GAP),
            padding=ft.Padding.symmetric(horizontal=8, vertical=4),
            border_radius=8,
            bgcolor=ft.Colors.with_opacity(0.08, SEED_COLOR) if highlight else None,
        )

    def _yearly_table(self, plan: DebtPlan) -> ft.Column:
        widths = [70, 130, 110, 110, 130]
        rows = [
            self._table_row(
                ["Year", "Starting balance", "Interest", "Paid", "Ending balance"],
                widths,
                header=True,
            )
        ]
        year: str | None = None
        starting = interest = paid = ending = 0
        previous_ending: int | None = None
        for row in plan.schedule:
            row_year = shift_month(self._start_month, row.month)[:4]
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
            self._table_row(["Month", "Payment", "Interest", "Balance"], widths, header=True)
        ]
        for row in plan.schedule[: self.schedule_limit]:
            rows.append(self._table_row(
                [
                    month_label(shift_month(self._start_month, row.month)),
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
                    format_percent(summary.debt.apr_bps),
                    format_money(summary.effective_minimum_cents),
                ],
                widths,
            ))
        return ft.Column(
            [
                section_title("Included in this plan"),
                ft.Text(
                    "The payoff order and combined schedule below cover every debt in this table.",
                    size=12,
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
            payoff = month_label(shift_month(self._start_month, month)) if month else "—"
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
                size=11,
                color=ft.Colors.ON_SURFACE_VARIANT,
            ))
        return ft.Column([section_title("Debt payoff order"), *rows], spacing=4)

    def _debt_card(self, summary: DebtSummary, payoff_month: int | None = None) -> ft.Container:
        debt = summary.debt
        details = [
            ft.Text(f"{format_money(summary.balance_cents)} owed", size=20, weight=ft.FontWeight.W_700),
            ft.Text(f"APR {format_percent(debt.apr_bps)}", size=12, color=ft.Colors.ON_SURFACE_VARIANT),
            ft.Text(
                f"Minimum {format_money(summary.effective_minimum_cents)}"
                + (f" · {month_label(summary.statement_month)} statement"
                   if summary.minimum_from_statement else " · set manually"),
                size=12,
                color=ft.Colors.ON_SURFACE_VARIANT,
            ),
        ]
        if summary.account_name:
            details.append(ft.Text(f"Linked to {summary.account_name}", size=12, color=SEED_COLOR))
        if payoff_month is not None:
            details.append(ft.Text(
                f"Payoff: {month_label(shift_month(self._start_month, payoff_month))} at this plan",
                size=12,
                color=SEED_COLOR,
            ))
        if debt.original_balance_cents:
            paid = min(1.0, max(0.0, 1 - summary.balance_cents / debt.original_balance_cents))
            details.append(ft.ProgressBar(value=paid, bar_height=8, border_radius=4))
            details.append(ft.Text(
                f"{round(paid * 100)}% paid down from {format_money(debt.original_balance_cents)}",
                size=11, color=ft.Colors.ON_SURFACE_VARIANT,
            ))
        if summary.minimum_below_interest:
            details.append(ft.Text("The minimum does not cover monthly interest", size=12, color=EXPENSE_COLOR))
        actions = [
            ft.FilledTonalButton(content="Log payment", icon=ft.Icons.PAYMENT,
                                 on_click=lambda _: DebtPaymentDialog(self.ctx, summary).open()),
            ft.OutlinedButton(content="History", icon=ft.Icons.HISTORY,
                              on_click=lambda _: DebtHistoryDialog(self.ctx, debt).open()),
            ft.OutlinedButton(content="Edit", icon=ft.Icons.EDIT_OUTLINED,
                              on_click=lambda _: DebtEditor(self.ctx, debt).open()),
            ft.IconButton(ft.Icons.DELETE_OUTLINE, tooltip="Delete", icon_color=EXPENSE_COLOR,
                          on_click=lambda _: self._confirm_delete(debt)),
        ]
        return card(
            ft.Column([ft.Text(debt.name, size=16, weight=ft.FontWeight.W_600),
                       ft.Text(debt.kind.label, size=12, color=ft.Colors.ON_SURFACE_VARIANT),
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
        self.name = ft.TextField(label="Name", value=debt.name if debt else "", autofocus=True)
        self.kind = ft.Dropdown(
            label="Kind",
            options=[ft.DropdownOption(key=k.value, text=k.label) for k in DebtKind],
            value=(debt.kind.value if debt else DebtKind.CREDIT_CARD.value),
        )
        self.balance = ft.TextField(label="Balance owed", prefix_icon=ft.Icons.ATTACH_MONEY,
                                    value=f"{debt.balance_cents / 100:.2f}" if debt else "")
        self.apr = ft.TextField(label="APR (%)", value=format_percent(debt.apr_bps).rstrip("%") if debt else "")
        self.minimum = ft.TextField(label="Minimum / month", prefix_icon=ft.Icons.ATTACH_MONEY,
                                    value=f"{debt.minimum_cents / 100:.2f}" if debt and debt.minimum_cents else "")
        self.minimum_note = ft.Text("", size=11, color=ft.Colors.ON_SURFACE_VARIANT, visible=False)
        self.original = ft.TextField(label="Original balance", hint_text="Optional",
                                     prefix_icon=ft.Icons.ATTACH_MONEY,
                                     value=(f"{debt.original_balance_cents / 100:.2f}"
                                            if debt and debt.original_balance_cents is not None else ""))
        self.link = ft.Dropdown(
            label="Link to credit-card or loan account",
            options=[ft.DropdownOption(key="none", text="None")]
            + [ft.DropdownOption(key=str(a.id), text=a.name) for a in linkable],
            value=str(debt.account_id) if debt and debt.account_id else "none",
        )
        self.link.on_select = lambda _: self._link_changed()
        self.error = ft.Text("", size=12, color=EXPENSE_COLOR, visible=False)
        self.dialog = ft.AlertDialog(
            modal=True,
            title=ft.Text("Edit Debt" if debt else "New Debt"),
            content=ft.Container(width=460, content=ft.Column(
                [self.name, self.kind, self.balance, self.apr, self.minimum, self.minimum_note,
                 self.original, self.link, self.error],
                spacing=14, tight=True, horizontal_alignment=ft.CrossAxisAlignment.STRETCH)),
            actions=[
                ft.TextButton(content="Cancel", on_click=lambda _: ctx.page.pop_dialog()),
                ft.FilledButton(content="Save", icon=ft.Icons.CHECK, on_click=self._save),
            ],
            actions_alignment=ft.MainAxisAlignment.END,
        )
        self._link_changed()

    def _link_changed(self) -> None:
        account_id = int(self.link.value) if self.link.value not in (None, "", "none") else None
        account = self.ctx.repo.get_account(account_id) if account_id is not None else None
        is_loan = account is not None and account.kind is AccountKind.LOAN
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
                # A loan link uses the stored minimum; there is no statement minimum to drive it.
                self.minimum.disabled = False
                self.minimum_note.visible = False
            else:
                self.kind.value = DebtKind.CREDIT_CARD.value
                status = self.ctx.service.current_minimum_payment(account_id)
                if status is not None:
                    self.minimum.value = f"{status.amount_cents / 100:.2f}"
                    self.minimum.disabled = True
                    self.minimum_note.value = f"Driven by the {month_label(status.month)} statement minimum"
                    self.minimum_note.visible = True
                else:
                    self.minimum.disabled = False
                    self.minimum_note.visible = False
        else:
            self.minimum.disabled = False
            self.minimum_note.visible = False

    def open(self) -> None:
        self.ctx.page.show_dialog(self.dialog)

    def _refresh_dialog(self) -> None:
        """Repaint the page (and its dialog overlay) after setting validation errors.

        Updating the page rather than the dialog itself works before the dialog has been
        attached, which is how the editor's tests drive ``_save`` directly.
        """
        self.ctx.page.update()

    def _save(self, _: ft.Event) -> None:
        for field in (self.name, self.balance, self.apr, self.minimum, self.original):
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
        if not valid:
            self._refresh_dialog()
            return
        try:
            if self.debt is not None:
                self.ctx.service.update_debt(
                    self.debt.id, name=name, kind=kind, balance_cents=balance, apr_bps=apr,
                    minimum_cents=minimum, original_balance_cents=original, account_id=link,
                )
            else:
                self.ctx.service.create_debt(
                    name, kind, balance, apr, minimum,
                    original_balance_cents=original, account_id=link,
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

    def __init__(self, ctx: AppContext, summary: DebtSummary) -> None:
        self.ctx = ctx
        self.summary = summary
        debt = summary.debt
        self.amount = ft.TextField(
            label="Amount",
            prefix_icon=ft.Icons.ATTACH_MONEY,
            keyboard_type=ft.KeyboardType.NUMBER,
            autofocus=True,
            on_submit=self._save,
        )
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
        self.date = date_field(ctx.page, "Payment date", date.today())
        self.note = ft.TextField(label="Note", hint_text="Optional")
        categories = [c for c in ctx.repo.list_categories() if c.type is TransactionType.EXPENSE]
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
        self.error = ft.Text("", size=12, color=EXPENSE_COLOR, visible=False)

        self.dialog = ft.AlertDialog(
            modal=True,
            title=ft.Text(f"Log payment — {debt.name}"),
            content=ft.Container(width=420, content=ft.Column(
                [
                    ft.Text(
                        f"{format_money(summary.balance_cents)} owed.",
                        size=12,
                        color=ft.Colors.ON_SURFACE_VARIANT,
                    ),
                    self.amount,
                    self.funding,
                    self.date,
                    self.note,
                    self.category,
                    self.error,
                ],
                spacing=14,
                tight=True,
                horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
            )),
            actions=[
                ft.TextButton(content="Cancel", on_click=lambda _: ctx.page.pop_dialog()),
                ft.FilledButton(content="Record payment", icon=ft.Icons.PAYMENT, on_click=self._save),
            ],
            actions_alignment=ft.MainAxisAlignment.END,
        )

    def open(self) -> None:
        self.ctx.page.show_dialog(self.dialog)

    def _save(self, _: ft.Event) -> None:
        self.amount.error = None
        self.date.error = None
        self.funding.error_text = None
        self.error.visible = False
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
                (self.note.value or "").strip(),
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
                    ft.Text(f"{tx.occurred_on:%b %d, %Y}", size=12, width=110),
                    ft.Text(format_money(tx.amount_cents), size=12, width=90,
                            text_align=ft.TextAlign.RIGHT),
                    ft.Text(tx.account_name or "", size=12, width=120),
                    ft.Text(source, size=12, expand=True),
                ],
                spacing=GAP,
            ))
        body: list[ft.Control] = [
            ft.Column(rows, spacing=6, tight=True)
            if payments
            else ft.Text("No payments recorded yet.", size=12, color=ft.Colors.ON_SURFACE_VARIANT)
        ]
        if debt.account_id is not None:
            body.append(ft.Text(
                "Payments made outside the Log payment dialog still change the balance but are not listed here.",
                size=11,
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
