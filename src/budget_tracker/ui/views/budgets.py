"""Budgets: monthly spending limits per expense category with progress tracking."""

from __future__ import annotations

import flet as ft

from ...formatting import format_money, month_label, parse_money, shift_month
from ...models import Budget, BudgetProgress, TransactionType
from ...repository import RepositoryError
from ..components import MonthSelector, card, category_avatar, confirm_dialog, empty_state, page_header, section_title
from ..context import AppContext
from ..theme import EXPENSE_COLOR, GAP, INCOME_COLOR, WARNING_COLOR

STATUS_COLORS = {"ok": INCOME_COLOR, "warning": WARNING_COLOR, "over": EXPENSE_COLOR}
STATUS_LABELS = {"ok": "On track", "warning": "Near limit", "over": "Over budget"}


class BudgetsView:
    def __init__(self, ctx: AppContext) -> None:
        self.ctx = ctx
        self.month_selector = MonthSelector(ctx.service.current_month(), lambda _: self.refresh())
        self.summary = ft.Container()
        self.grid = ft.ResponsiveRow(spacing=GAP, run_spacing=GAP)
        self.control = ft.Column(
            [
                page_header(
                    "Budgets",
                    "Set monthly limits and stay on top of your spending",
                    [
                        self.month_selector.control,
                        ft.OutlinedButton(content="Copy last month", icon=ft.Icons.CONTENT_COPY, on_click=self._copy_previous),
                        ft.FilledButton(content="Set Budget", icon=ft.Icons.ADD, on_click=lambda _: self._open_editor()),
                    ],
                ),
                self.summary,
                self.grid,
            ],
            spacing=GAP,
            scroll=ft.ScrollMode.AUTO,
            expand=True,
        )
        ctx.on_data_changed(self.refresh)
        self.refresh()

    @property
    def month(self) -> str:
        return self.month_selector.month

    def refresh(self) -> None:
        progress = self.ctx.repo.budget_progress(self.month)
        total_limit = sum(b.limit_cents for b in progress)
        total_spent = sum(b.spent_cents for b in progress)
        ratio = total_spent / total_limit if total_limit else 0
        status = "over" if ratio > 1 else "warning" if ratio >= 0.8 else "ok"

        self.summary.content = card(
            ft.Column(
                [
                    section_title(
                        f"Overall budget — {month_label(self.month)}",
                        ft.Text(f"{format_money(total_spent)} of {format_money(total_limit)}", weight=ft.FontWeight.W_600),
                    ),
                    ft.ProgressBar(
                        value=min(ratio, 1.0),
                        color=STATUS_COLORS[status],
                        bgcolor=ft.Colors.with_opacity(0.15, STATUS_COLORS[status]),
                        bar_height=12,
                        border_radius=6,
                    ),
                    ft.Text(
                        f"{format_money(total_limit - total_spent)} remaining" if total_limit >= total_spent else f"{format_money(total_spent - total_limit)} over budget",
                        size=13,
                        color=STATUS_COLORS[status],
                    ),
                ],
                spacing=10,
            )
        )
        self.summary.visible = bool(progress)

        if progress:
            self.grid.controls = [self._budget_card(b) for b in progress]
        else:
            self.grid.controls = [
                card(
                    empty_state(
                        ft.Icons.TRACK_CHANGES,
                        f"No budgets for {month_label(self.month)}",
                        "Set a spending limit for a category, or copy last month's budgets.",
                        ft.FilledTonalButton(content="Set Budget", icon=ft.Icons.ADD, on_click=lambda _: self._open_editor()),
                    ),
                    col=12,
                )
            ]

    def _budget_card(self, b: BudgetProgress) -> ft.Container:
        color = STATUS_COLORS[b.status]
        return card(
            ft.Column(
                [
                    ft.Row(
                        [
                            category_avatar(b.category_icon, b.category_color),
                            ft.Column(
                                [
                                    ft.Text(b.category_name, weight=ft.FontWeight.W_600),
                                    ft.Container(
                                        content=ft.Text(STATUS_LABELS[b.status], size=11, color=color, weight=ft.FontWeight.W_600),
                                        bgcolor=ft.Colors.with_opacity(0.12, color),
                                        padding=ft.Padding.symmetric(horizontal=8, vertical=2),
                                        border_radius=8,
                                    ),
                                ],
                                spacing=4,
                                expand=True,
                            ),
                            ft.IconButton(ft.Icons.EDIT_OUTLINED, tooltip="Edit", icon_size=20, on_click=lambda _: self._open_editor(b)),
                            ft.IconButton(ft.Icons.DELETE_OUTLINE, tooltip="Remove", icon_size=20, icon_color=EXPENSE_COLOR, on_click=lambda _: self._confirm_delete(b)),
                        ],
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    ),
                    ft.ProgressBar(value=min(b.ratio, 1.0), color=color, bgcolor=ft.Colors.with_opacity(0.15, color), bar_height=10, border_radius=5),
                    ft.Row(
                        [
                            ft.Text(f"{format_money(b.spent_cents)} spent", size=13, expand=True),
                            ft.Text(f"{b.ratio:.0%} of {format_money(b.limit_cents)}", size=13, color=ft.Colors.ON_SURFACE_VARIANT),
                        ]
                    ),
                    ft.Text(
                        f"{format_money(b.remaining_cents)} left" if b.remaining_cents >= 0 else f"{format_money(-b.remaining_cents)} over",
                        size=13,
                        weight=ft.FontWeight.W_600,
                        color=color,
                    ),
                ],
                spacing=10,
            ),
            col={"xs": 12, "md": 6, "xl": 4},
        )

    def _copy_previous(self, _: ft.Event) -> None:
        previous = shift_month(self.month, -1)
        copied = self.ctx.repo.copy_budgets(previous, self.month)
        if copied:
            self.ctx.notify(f"Copied {copied} budget(s) from {month_label(previous)}")
            self.ctx.data_changed()
        else:
            self.ctx.notify(f"No new budgets to copy from {month_label(previous)}", error=True)

    def _confirm_delete(self, b: BudgetProgress) -> None:
        def delete() -> None:
            self.ctx.repo.delete_budget(b.budget_id)
            self.ctx.notify(f"Budget for {b.category_name} removed")
            self.ctx.data_changed()

        confirm_dialog(self.ctx.page, "Remove budget?", f"Remove the {month_label(self.month)} budget for {b.category_name}?", delete, "Remove")

    def _open_editor(self, existing: BudgetProgress | None = None) -> None:
        categories = self.ctx.repo.list_categories(TransactionType.EXPENSE)
        if not categories:
            self.ctx.notify("Create an expense category first", error=True)
            return
        page = self.ctx.page
        category = ft.Dropdown(
            label="Category",
            options=[ft.DropdownOption(key=str(c.id), text=c.name) for c in categories],
            value=str(existing.category_id if existing else categories[0].id),
            disabled=existing is not None,
            expand=True,
        )
        amount = ft.TextField(
            label="Monthly limit",
            prefix_icon=ft.Icons.ATTACH_MONEY,
            value=f"{existing.limit_cents / 100:.2f}" if existing else "",
            keyboard_type=ft.KeyboardType.NUMBER,
            autofocus=True,
        )

        def save(_: ft.Event) -> None:
            try:
                limit = parse_money(amount.value)
                self.ctx.repo.set_budget(Budget(category_id=int(category.value), month=self.month, limit_cents=limit))
            except (ValueError, RepositoryError) as exc:
                amount.error = str(exc)
                dialog.update()
                return
            page.pop_dialog()
            self.ctx.notify("Budget saved")
            self.ctx.data_changed()

        amount.on_submit = save
        dialog = ft.AlertDialog(
            modal=True,
            title=ft.Text(f"{'Edit' if existing else 'Set'} Budget — {month_label(self.month)}"),
            content=ft.Container(width=400, content=ft.Column([category, amount], spacing=14, tight=True)),
            actions=[
                ft.TextButton(content="Cancel", on_click=lambda _: page.pop_dialog()),
                ft.FilledButton(content="Save", icon=ft.Icons.CHECK, on_click=save),
            ],
            actions_alignment=ft.MainAxisAlignment.END,
        )
        page.show_dialog(dialog)
