"""Category rules: review and delete the mappings learned from statement imports."""

from __future__ import annotations

import flet as ft

from ...models import Category, CategoryRule
from ..context import AppContext
from ..theme import EXPENSE_COLOR, GAP, fs


class CategoryRulesDialog:
    """Modal list of saved category rules with a delete action per rule."""

    def __init__(self, ctx: AppContext) -> None:
        self.ctx = ctx
        self.list_column = ft.Column(spacing=2, scroll=ft.ScrollMode.AUTO)
        self.dialog = ft.AlertDialog(
            modal=True,
            title=ft.Text("Category rules"),
            content=ft.Container(
                width=520,
                height=320,
                content=self.list_column,
            ),
            actions=[ft.TextButton(content="Close", on_click=lambda _: ctx.page.pop_dialog())],
            actions_alignment=ft.MainAxisAlignment.END,
        )
        self._render()

    def open(self) -> None:
        self._render()
        self.ctx.page.show_dialog(self.dialog)

    def _render(self) -> None:
        rules = self.ctx.repo.list_category_rules()
        if not rules:
            self.list_column.controls = [
                ft.Text(
                    "No rules yet — save one from the import preview.",
                    size=fs(13),
                    color=ft.Colors.ON_SURFACE_VARIANT,
                )
            ]
            return
        categories = {category.id: category for category in self.ctx.repo.list_categories()}
        self.list_column.controls = [self._row(rule, categories.get(rule.category_id)) for rule in rules]

    def _row(self, rule: CategoryRule, category: Category | None) -> ft.Container:
        category_name = category.name if category else "Unknown category"
        return ft.Container(
            content=ft.Row(
                [
                    ft.Icon(ft.Icons.RULE, size=fs(20), color=ft.Colors.ON_SURFACE_VARIANT),
                    ft.Text(
                        f'"{rule.match_text}" → {category_name}',
                        size=fs(14),
                        expand=True,
                        max_lines=1,
                        overflow=ft.TextOverflow.ELLIPSIS,
                    ),
                    ft.IconButton(
                        ft.Icons.DELETE_OUTLINE,
                        tooltip="Delete rule",
                        icon_size=20,
                        icon_color=EXPENSE_COLOR,
                        on_click=lambda _, r=rule: self._delete(r),
                    ),
                ],
                spacing=GAP,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
            padding=ft.Padding.symmetric(horizontal=8, vertical=6),
        )

    def _delete(self, rule: CategoryRule) -> None:
        try:
            self.ctx.repo.delete_category_rule(rule.id)
        except Exception as exc:  # noqa: BLE001 - surface storage failures instead of hiding them
            self.ctx.notify(str(exc), error=True)
            return
        self._render()
        self.ctx.notify(f'Rule "{rule.match_text}" deleted')
        self.ctx.data_changed()
