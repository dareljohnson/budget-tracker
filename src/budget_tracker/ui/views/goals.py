"""Savings goals: set money aside, plan monthly contributions and track history."""

from __future__ import annotations

import calendar
from datetime import date

import flet as ft

from ...formatting import format_money, month_key, month_label, parse_date, parse_money, parse_signed_money, shift_month
from ...models import AccountKind, SavingsGoal
from ...repository import RepositoryError
from ...services import GoalScheduleMonth, SavingsGoalProgress
from ..components import card, confirm_dialog, date_field, empty_state, page_header, section_title
from ..context import AppContext
from ..theme import EXPENSE_COLOR, GAP, INCOME_COLOR, SEED_COLOR, fs

STATE_COLORS = {"active": SEED_COLOR, "reached": INCOME_COLOR, "overdue": EXPENSE_COLOR}


def _six_months_from_today() -> date:
    today = date.today()
    year, month = map(int, shift_month(month_key(today), 6).split("-"))
    return date(year, month, min(today.day, calendar.monthrange(year, month)[1]))


def _state_text(progress: SavingsGoalProgress) -> str:
    if progress.state == "reached":
        return "Reached"
    if progress.state == "overdue":
        return "Overdue"
    months = progress.months_left
    return f"{months} month{'s' if months != 1 else ''} left"


def goal_state_chip(progress: SavingsGoalProgress) -> ft.Container:
    color = STATE_COLORS[progress.state]
    return ft.Container(
        content=ft.Text(_state_text(progress), size=fs(12), weight=ft.FontWeight.W_600, color=color),
        bgcolor=ft.Colors.with_opacity(0.12, color),
        border_radius=10,
        padding=ft.Padding.symmetric(horizontal=8, vertical=3),
    )


def _progress_ratio(progress: SavingsGoalProgress) -> float:
    target = progress.goal.target_cents
    return min(1.0, progress.progress_cents / target) if target > 0 else 0.0


class GoalsView:
    def __init__(self, ctx: AppContext) -> None:
        self.ctx = ctx
        # Plain alignment, not CrossAxisAlignment.STRETCH: on Flet 1.1.2 a ResponsiveRow using
        # STRETCH without an explicit height collapses to zero height in this view's scrollable
        # body, so the cards (and the empty state) never appear. Cards size to their content.
        self.grid = ft.ResponsiveRow(
            spacing=GAP,
            run_spacing=GAP,
        )
        self.control = ft.Column(
            [
                page_header(
                    "Goals",
                    "Set money aside for what matters",
                    [ft.FilledButton(content="Add goal", icon=ft.Icons.ADD, on_click=lambda _: GoalEditor(ctx, None).open())],
                ),
                self.grid,
            ],
            spacing=GAP,
            scroll=ft.ScrollMode.AUTO,
            expand=True,
        )
        ctx.on_data_changed(self.refresh)
        self.refresh()

    def refresh(self) -> None:
        progress = self.ctx.service.list_goal_progress()
        self.grid.controls = [self._goal_card(p) for p in progress] or [
            card(
                empty_state(
                    ft.Icons.SAVINGS_OUTLINED,
                    "No goals yet",
                    "Set a target and a date, then track how much you have saved towards it.",
                    ft.FilledButton(content="Add goal", icon=ft.Icons.ADD, on_click=lambda _: GoalEditor(self.ctx, None).open()),
                ),
                col=12,
            )
        ]

    def _goal_card(self, progress: SavingsGoalProgress) -> ft.Container:
        goal = progress.goal
        color = STATE_COLORS[progress.state]
        actions: list[ft.Control] = []
        contribute = ft.FilledTonalButton(
            content="Catch up" if progress.state == "overdue" else "Contribute",
            icon=ft.Icons.SAVINGS,
            on_click=lambda _: ContributeDialog(self.ctx, progress).open(),
            visible=progress.state != "reached",
        )
        actions += [
            contribute,
            ft.OutlinedButton(
                content="Schedule & history",
                icon=ft.Icons.HISTORY,
                on_click=lambda _: GoalDetailDialog(self.ctx, goal.id).open(),
            ),
            ft.IconButton(ft.Icons.EDIT_OUTLINED, tooltip="Edit", icon_size=20, on_click=lambda _: GoalEditor(self.ctx, goal).open()),
            ft.IconButton(
                ft.Icons.DELETE_OUTLINE,
                tooltip="Delete",
                icon_size=20,
                icon_color=EXPENSE_COLOR,
                on_click=lambda _: self._confirm_delete(goal),
            ),
        ]
        return card(
            ft.Column(
                [
                    ft.Row(
                        [
                            ft.Container(
                                content=ft.Icon(ft.Icons.SAVINGS, color=color, size=fs(22)),
                                width=44,
                                height=44,
                                border_radius=14,
                                bgcolor=ft.Colors.with_opacity(0.12, color),
                                alignment=ft.Alignment.CENTER,
                            ),
                            ft.Column(
                                [
                                    ft.Text(goal.name, size=fs(16), weight=ft.FontWeight.W_600),
                                    ft.Text(f"Savings · {progress.account_name}", size=fs(13), color=ft.Colors.ON_SURFACE_VARIANT),
                                ],
                                spacing=2,
                                expand=True,
                            ),
                            goal_state_chip(progress),
                        ],
                        spacing=GAP,
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    ),
                    ft.Column(
                        [
                            ft.Text(
                                f"{format_money(progress.progress_cents)} of {format_money(goal.target_cents)}",
                                size=fs(15),
                                weight=ft.FontWeight.W_600,
                            ),
                            ft.ProgressBar(
                                value=_progress_ratio(progress),
                                color=color,
                                bgcolor=ft.Colors.with_opacity(0.15, color),
                                bar_height=10,
                                border_radius=5,
                            ),
                        ],
                        spacing=6,
                    ),
                    self._goal_details(progress),
                    ft.Row(actions, alignment=ft.MainAxisAlignment.END, spacing=4, wrap=True),
                ],
                spacing=12,
            ),
            col={"xs": 12, "md": 6, "xl": 4},
        )

    def _goal_details(self, progress: SavingsGoalProgress) -> ft.Control:
        if progress.state == "reached":
            return ft.Text("Target met — nothing left to save.", size=fs(13), color=INCOME_COLOR)
        if progress.state == "overdue":
            return ft.Text(
                f"Due now {format_money(progress.remaining_cents)}",
                size=fs(13),
                weight=ft.FontWeight.W_600,
                color=EXPENSE_COLOR,
            )
        return ft.Column(
            [
                ft.Text(f"{format_money(progress.remaining_cents)} remaining", size=fs(13), weight=ft.FontWeight.W_500),
                ft.Row(
                    [
                        ft.Text(
                            f"Required {format_money(progress.required_monthly_cents)}/month",
                            size=fs(13),
                            color=ft.Colors.ON_SURFACE_VARIANT,
                        ),
                        ft.Text(
                            f"This month: {format_money(progress.contributed_this_month_cents)} of "
                            f"{format_money(progress.required_monthly_cents)}",
                            size=fs(13),
                            color=ft.Colors.ON_SURFACE_VARIANT,
                        ),
                    ],
                    spacing=GAP,
                    wrap=True,
                ),
            ],
            spacing=4,
        )

    def _confirm_delete(self, goal: SavingsGoal) -> None:
        def delete() -> None:
            try:
                self.ctx.service.delete_savings_goal(goal.id)
            except RepositoryError as exc:
                self.ctx.notify(str(exc), error=True)
                return
            self.ctx.notify(f"Goal '{goal.name}' deleted")
            self.ctx.data_changed()

        confirm_dialog(
            self.ctx.page,
            "Delete goal?",
            f"'{goal.name}' will be removed. Its contributions stay in the ledger and the money stays in the savings "
            "account; only the goal link is removed.",
            delete,
        )


class GoalEditor:
    """Create or edit a goal. The destination is fixed once contributions exist."""

    def __init__(self, ctx: AppContext, goal: SavingsGoal | None) -> None:
        self.ctx = ctx
        self.goal = goal
        self.contribution_count = ctx.repo.goal_contribution_count(goal.id) if goal else 0
        savings = [a for a in ctx.repo.list_accounts() if a.kind is AccountKind.SAVINGS]

        self.name = ft.TextField(label="Name", value=goal.name if goal else "", max_length=60, autofocus=True, on_submit=self._save)
        self.target = ft.TextField(
            label="Target amount",
            prefix_icon=ft.Icons.ATTACH_MONEY,
            value=f"{goal.target_cents / 100:.2f}" if goal else "",
            keyboard_type=ft.KeyboardType.NUMBER,
            on_submit=self._save,
        )
        self.target_date = date_field(ctx.page, "Target date", goal.target_date if goal else _six_months_from_today())
        self.destination = ft.Dropdown(
            label="Destination account",
            options=[ft.DropdownOption(key=str(a.id), text=f"{a.name} ({a.kind.label})") for a in savings],
            value=str(goal.account_id) if goal else (str(savings[0].id) if savings else None),
            disabled=self.contribution_count > 0,
        )
        initial = goal.initial_cents if goal else 0
        self.initial = ft.TextField(
            label="Already saved",
            prefix_icon=ft.Icons.ATTACH_MONEY,
            hint_text="Optional",
            value=f"{initial / 100:.2f}" if initial else "",
            keyboard_type=ft.KeyboardType.NUMBER,
            on_submit=self._save,
        )
        self.destination_note = ft.Text(
            "The destination can't change while contributions are linked to this goal.",
            size=fs(13),
            color=ft.Colors.ON_SURFACE_VARIANT,
            visible=self.contribution_count > 0,
        )
        self.no_savings_note = ft.Text(
            "Add a savings account on the Accounts page first.",
            size=fs(13),
            color=EXPENSE_COLOR,
            visible=not savings,
        )
        self.error = ft.Text("", size=fs(13), color=EXPENSE_COLOR, visible=False)

        self.dialog = ft.AlertDialog(
            modal=True,
            title=ft.Text("Edit Goal" if goal else "New Goal"),
            content=ft.Container(
                width=460,
                content=ft.Column(
                    [
                        self.name,
                        self.target,
                        self.target_date,
                        self.destination,
                        self.destination_note,
                        self.no_savings_note,
                        self.initial,
                        self.error,
                    ],
                    spacing=14,
                    tight=True,
                    horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
                ),
            ),
            actions=[
                ft.TextButton(content="Cancel", on_click=lambda _: ctx.page.pop_dialog()),
                ft.FilledButton(content="Save", icon=ft.Icons.CHECK, on_click=self._save),
            ],
            actions_alignment=ft.MainAxisAlignment.END,
        )

    def open(self) -> None:
        self.ctx.page.show_dialog(self.dialog)

    def _save(self, _: ft.Event) -> None:
        valid = True
        for field in (self.name, self.target, self.target_date, self.initial):
            field.error = None
        self.destination.error_text = None
        self.error.visible = False

        name = (self.name.value or "").strip()
        if not name:
            self.name.error = "Name is required"
            valid = False
        try:
            target = parse_money(self.target.value)
        except ValueError as exc:
            self.target.error = str(exc)
            valid = False
        try:
            target_date = parse_date(self.target_date.value)
        except ValueError as exc:
            self.target_date.error = str(exc)
            valid = False
        try:
            initial = parse_signed_money(self.initial.value)
            if initial < 0:
                raise ValueError("Amount already saved cannot be negative")
        except ValueError as exc:
            self.initial.error = str(exc)
            valid = False
        if not self.destination.value:
            self.destination.error_text = "Select a savings account"
            valid = False
        if not valid:
            self.dialog.update()
            return

        try:
            if self.goal:
                self.ctx.service.update_savings_goal(
                    self.goal.id,
                    name=name,
                    target_cents=target,
                    target_date=target_date,
                    account_id=int(self.destination.value),
                    initial_cents=initial,
                )
            else:
                self.ctx.service.create_savings_goal(name, target, target_date, int(self.destination.value), initial)
        except RepositoryError as exc:
            self.error.value = str(exc)
            self.error.visible = True
            self.dialog.update()
            return
        self.ctx.page.pop_dialog()
        self.ctx.notify(f"Goal '{name}' saved")
        self.ctx.data_changed()


class ContributeDialog:
    """Record a transfer into the goal's savings account."""

    def __init__(self, ctx: AppContext, progress: SavingsGoalProgress) -> None:
        self.ctx = ctx
        self.progress = progress
        goal = progress.goal
        prefill = progress.remaining_cents if progress.state == "overdue" else progress.required_monthly_cents
        self.amount = ft.TextField(
            label="Amount",
            prefix_icon=ft.Icons.ATTACH_MONEY,
            value=f"{prefill / 100:.2f}" if prefill else "",
            keyboard_type=ft.KeyboardType.NUMBER,
            autofocus=True,
            on_submit=self._save,
        )
        funding = [a for a in ctx.repo.list_accounts() if not a.kind.is_liability and a.id != goal.account_id]
        default_funding = next((a for a in funding if a.kind is AccountKind.CHECKING), funding[0] if funding else None)
        self.funding = ft.Dropdown(
            label="From account",
            options=[ft.DropdownOption(key=str(a.id), text=f"{a.name} ({a.kind.label})") for a in funding],
            value=str(default_funding.id) if default_funding else None,
        )
        self.date = date_field(ctx.page, "Contribution date", date.today())

        self.dialog = ft.AlertDialog(
            modal=True,
            title=ft.Text(f"Contribute to {goal.name}"),
            content=ft.Container(
                width=420,
                content=ft.Column(
                    [
                        ft.Text(
                            f"{format_money(progress.remaining_cents)} remaining in '{progress.account_name}'.",
                            size=fs(13),
                            color=ft.Colors.ON_SURFACE_VARIANT,
                        ),
                        self.amount,
                        self.funding,
                        self.date,
                    ],
                    spacing=14,
                    tight=True,
                    horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
                ),
            ),
            actions=[
                ft.TextButton(content="Cancel", on_click=lambda _: ctx.page.pop_dialog()),
                ft.FilledButton(content="Contribute", icon=ft.Icons.SAVINGS, on_click=self._save),
            ],
            actions_alignment=ft.MainAxisAlignment.END,
        )

    def open(self) -> None:
        self.ctx.page.show_dialog(self.dialog)

    def _save(self, _: ft.Event) -> None:
        valid = True
        try:
            amount = parse_money(self.amount.value)
            self.amount.error = None
        except ValueError as exc:
            self.amount.error = str(exc)
            valid = False
        try:
            on_date = parse_date(self.date.value)
            self.date.error = None
        except ValueError as exc:
            self.date.error = str(exc)
            valid = False
        self.funding.error_text = None if self.funding.value else "Select a funding account"
        valid = valid and bool(self.funding.value)
        if not valid:
            self.dialog.update()
            return
        try:
            self.ctx.service.contribute_to_goal(
                self.progress.goal.id, int(self.funding.value), amount, on_date
            )
        except RepositoryError as exc:
            self.ctx.notify(str(exc), error=True)
            return
        self.ctx.page.pop_dialog()
        self.ctx.notify(f"{format_money(amount)} added to '{self.progress.goal.name}'")
        self.ctx.data_changed()


class GoalDetailDialog:
    """Show the fixed monthly plan and the contribution history for a goal."""

    def __init__(self, ctx: AppContext, goal_id: int) -> None:
        self.ctx = ctx
        self.goal_id = goal_id
        progress = ctx.service.goal_progress(goal_id)
        contributions = ctx.repo.list_goal_contributions(goal_id)
        goal = progress.goal
        color = STATE_COLORS[progress.state]

        summary: list[ft.Control] = [
            ft.Row(
                [
                    ft.Text(
                        f"{format_money(progress.progress_cents)} of {format_money(goal.target_cents)} saved",
                        size=fs(15),
                        weight=ft.FontWeight.W_600,
                        expand=True,
                    ),
                    goal_state_chip(progress),
                ],
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
            ft.ProgressBar(
                value=_progress_ratio(progress),
                color=color,
                bgcolor=ft.Colors.with_opacity(0.15, color),
                bar_height=10,
                border_radius=5,
            ),
        ]
        if progress.state == "reached":
            summary.append(ft.Text("Target met.", size=fs(13), color=INCOME_COLOR))
        elif progress.state == "overdue":
            summary.append(ft.Text(f"Due now {format_money(progress.remaining_cents)}", size=fs(13), color=EXPENSE_COLOR))
        else:
            summary.append(
                ft.Text(
                    f"{format_money(progress.remaining_cents)} remaining · "
                    f"{format_money(progress.required_monthly_cents)}/month required · "
                    f"This month {format_money(progress.contributed_this_month_cents)}",
                    size=fs(13),
                    color=ft.Colors.ON_SURFACE_VARIANT,
                )
            )

        plan_controls: list[ft.Control]
        if progress.plan:
            plan_controls = [_plan_table(progress.plan)]
        elif progress.state == "reached":
            plan_controls = [ft.Text("The target has been reached, so the monthly plan is complete.", size=fs(13), color=INCOME_COLOR)]
        else:
            plan_controls = [ft.Text("The target date has passed, so there is no schedule left to show.", size=fs(13), color=EXPENSE_COLOR)]

        if contributions:
            history: ft.Control = ft.DataTable(
                columns=[
                    ft.DataColumn(ft.Text("Date")),
                    ft.DataColumn(ft.Text("Amount"), numeric=True),
                    ft.DataColumn(ft.Text("From")),
                ],
                rows=[
                    ft.DataRow(
                        cells=[
                            ft.DataCell(ft.Text(f"{tx.occurred_on:%b %d, %Y}")),
                            ft.DataCell(ft.Text(format_money(tx.amount_cents))),
                            ft.DataCell(ft.Text(tx.account_name or "")),
                        ]
                    )
                    for tx in contributions
                ],
                column_spacing=24,
            )
        else:
            history = ft.Text("No contributions yet.", size=fs(13), color=ft.Colors.ON_SURFACE_VARIANT)

        self.dialog = ft.AlertDialog(
            modal=True,
            title=ft.Text(goal.name),
            content=ft.Container(
                width=560,
                content=ft.Column(
                    [
                        *summary,
                        section_title("Monthly plan"),
                        *plan_controls,
                        section_title("Contribution history"),
                        history,
                    ],
                    spacing=12,
                    tight=True,
                    scroll=ft.ScrollMode.AUTO,
                ),
                height=480,
            ),
            actions=[ft.TextButton(content="Close", on_click=lambda _: ctx.page.pop_dialog())],
            actions_alignment=ft.MainAxisAlignment.END,
        )

    def open(self) -> None:
        self.ctx.page.show_dialog(self.dialog)


def _plan_table(plan: list[GoalScheduleMonth]) -> ft.DataTable:
    return ft.DataTable(
        columns=[
            ft.DataColumn(ft.Text("Month")),
            ft.DataColumn(ft.Text("Required"), numeric=True),
            ft.DataColumn(ft.Text("Cumulative"), numeric=True),
        ],
        rows=[
            ft.DataRow(
                cells=[
                    ft.DataCell(ft.Text(month_label(row.month))),
                    ft.DataCell(ft.Text(format_money(row.required_cents))),
                    ft.DataCell(ft.Text(format_money(row.cumulative_cents))),
                ]
            )
            for row in plan
        ],
        column_spacing=24,
    )
