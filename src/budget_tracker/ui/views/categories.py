"""Categories: manage income and expense categories with colors and icons."""

from __future__ import annotations

import flet as ft

from ...models import Category, TransactionType
from ...repository import RepositoryError
from ..components import card, category_avatar, confirm_dialog, empty_state, page_header, section_title
from ..context import AppContext
from ..theme import EXPENSE_COLOR, GAP, ICON_CHOICES, PALETTE, icon_for
from .category_rules import CategoryRulesDialog


class CategoriesView:
    def __init__(self, ctx: AppContext) -> None:
        self.ctx = ctx
        self.income_list = ft.Column(spacing=2)
        self.expense_list = ft.Column(spacing=2)
        self.control = ft.Column(
            [
                page_header(
                    "Categories",
                    "Organize your money with custom categories",
                    [
                        ft.OutlinedButton(
                            content="Rules",
                            icon=ft.Icons.RULE,
                            tooltip="Categorization rules learned from imports",
                            on_click=lambda _: CategoryRulesDialog(ctx).open(),
                        ),
                        ft.FilledButton(content="Add Category", icon=ft.Icons.ADD, on_click=lambda _: CategoryEditor(ctx, None).open()),
                    ],
                ),
                ft.ResponsiveRow(
                    [
                        card(ft.Column([section_title("Expense Categories"), self.expense_list], spacing=8), col={"xs": 12, "md": 6}),
                        card(ft.Column([section_title("Income Categories"), self.income_list], spacing=8), col={"xs": 12, "md": 6}),
                    ],
                    spacing=GAP,
                    run_spacing=GAP,
                    vertical_alignment=ft.CrossAxisAlignment.START,
                ),
            ],
            spacing=GAP,
            scroll=ft.ScrollMode.AUTO,
            expand=True,
        )
        ctx.on_data_changed(self.refresh)
        self.refresh()

    def refresh(self) -> None:
        categories = self.ctx.repo.list_categories()
        for type_, column in ((TransactionType.INCOME, self.income_list), (TransactionType.EXPENSE, self.expense_list)):
            items = [c for c in categories if c.type == type_]
            column.controls = [self._row(c) for c in items] or [
                empty_state(ft.Icons.CATEGORY, "No categories", f"Add an {type_.value} category to get started.")
            ]

    def _row(self, category: Category) -> ft.Container:
        usage = self.ctx.repo.category_usage(category.id)
        return ft.Container(
            content=ft.Row(
                [
                    category_avatar(category.icon, category.color),
                    ft.Column(
                        [
                            ft.Text(category.name, weight=ft.FontWeight.W_500),
                            ft.Text(f"{usage} transaction(s)", size=12, color=ft.Colors.ON_SURFACE_VARIANT),
                        ],
                        spacing=2,
                        expand=True,
                    ),
                    ft.IconButton(ft.Icons.EDIT_OUTLINED, tooltip="Edit", icon_size=20, on_click=lambda _: CategoryEditor(self.ctx, category).open()),
                    ft.IconButton(
                        ft.Icons.DELETE_OUTLINE,
                        tooltip="Delete" if not usage else "In use - cannot delete",
                        icon_size=20,
                        icon_color=EXPENSE_COLOR,
                        disabled=bool(usage),
                        on_click=lambda _: self._confirm_delete(category),
                    ),
                ],
                spacing=GAP,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
            padding=ft.Padding.symmetric(horizontal=8, vertical=6),
        )

    def _confirm_delete(self, category: Category) -> None:
        def delete() -> None:
            try:
                self.ctx.repo.delete_category(category.id)
            except RepositoryError as exc:
                self.ctx.notify(str(exc), error=True)
                return
            self.ctx.notify(f"Category '{category.name}' deleted")
            self.ctx.data_changed()

        confirm_dialog(self.ctx.page, "Delete category?", f"'{category.name}' and any budgets for it will be removed.", delete)


class CategoryEditor:
    def __init__(self, ctx: AppContext, category: Category | None) -> None:
        self.ctx = ctx
        self.category = category
        self.color = category.color if category else PALETTE[5]
        self.icon = category.icon if category else ICON_CHOICES[0]
        self.type = category.type if category else TransactionType.EXPENSE

        self.name = ft.TextField(label="Name", value=category.name if category else "", max_length=40, autofocus=True, on_submit=self._save)
        self.type_toggle = ft.SegmentedButton(
            selected=[self.type.value],
            segments=[
                ft.Segment(value=TransactionType.EXPENSE.value, label=ft.Text("Expense")),
                ft.Segment(value=TransactionType.INCOME.value, label=ft.Text("Income")),
            ],
            on_change=self._type_changed,
        )
        self.preview = category_avatar(self.icon, self.color, size=56)
        self.color_grid = ft.Row(wrap=True, spacing=8, run_spacing=8)
        self.icon_grid = ft.Row(wrap=True, spacing=6, run_spacing=6)
        self._render_pickers()

        self.dialog = ft.AlertDialog(
            modal=True,
            title=ft.Text("Edit Category" if category else "New Category"),
            content=ft.Container(
                width=460,
                content=ft.Column(
                    [
                        ft.Row([self.preview, ft.Column([self.name], expand=True)], spacing=GAP, vertical_alignment=ft.CrossAxisAlignment.START),
                        self.type_toggle,
                        ft.Text("Color", weight=ft.FontWeight.W_600),
                        self.color_grid,
                        ft.Text("Icon", weight=ft.FontWeight.W_600),
                        self.icon_grid,
                    ],
                    spacing=12,
                    tight=True,
                    scroll=ft.ScrollMode.AUTO,
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

    def _render_pickers(self) -> None:
        self.color_grid.controls = [
            ft.Container(
                width=30,
                height=30,
                border_radius=15,
                bgcolor=c,
                border=ft.Border.all(3, ft.Colors.ON_SURFACE) if c == self.color else None,
                on_click=lambda _, c=c: self._select(color=c),
                tooltip=c,
            )
            for c in PALETTE
        ]
        self.icon_grid.controls = [
            ft.Container(
                content=ft.Icon(icon_for(name), size=20, color=self.color if name == self.icon else ft.Colors.ON_SURFACE_VARIANT),
                width=38,
                height=38,
                border_radius=10,
                alignment=ft.Alignment.CENTER,
                bgcolor=ft.Colors.with_opacity(0.15, self.color) if name == self.icon else None,
                on_click=lambda _, n=name: self._select(icon=n),
                tooltip=name.replace("_", " ").title(),
            )
            for name in ICON_CHOICES
        ]
        avatar = category_avatar(self.icon, self.color, size=56)
        self.preview.content = avatar.content
        self.preview.bgcolor = avatar.bgcolor

    def _select(self, *, color: str | None = None, icon: str | None = None) -> None:
        self.color = color or self.color
        self.icon = icon or self.icon
        self._render_pickers()
        self.dialog.update()

    def _type_changed(self, e: ft.Event[ft.SegmentedButton]) -> None:
        self.type = TransactionType(e.control.selected[0])

    def _save(self, _: ft.Event) -> None:
        name = (self.name.value or "").strip()
        if not name:
            self.name.error = "Name is required"
            self.dialog.update()
            return
        category = Category(id=self.category.id if self.category else None, name=name, type=self.type, color=self.color, icon=self.icon)
        try:
            if category.id:
                self.ctx.repo.update_category(category)
            else:
                self.ctx.repo.add_category(category)
        except RepositoryError as exc:
            self.name.error = str(exc)
            self.dialog.update()
            return
        self.ctx.page.pop_dialog()
        self.ctx.notify(f"Category '{name}' saved")
        self.ctx.data_changed()
