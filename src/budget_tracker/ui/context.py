"""Shared application context passed to every view."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

import flet as ft

from ..repository import Repository
from ..services import BudgetService
from .theme import EXPENSE_COLOR, INCOME_COLOR


@dataclass
class AppContext:
    page: ft.Page
    repo: Repository
    service: BudgetService
    _listeners: list[Callable[[], None]] = field(default_factory=list)

    def on_data_changed(self, callback: Callable[[], None]) -> None:
        self._listeners.append(callback)

    def data_changed(self) -> None:
        """Notify all views that underlying data changed so they can re-render."""
        for callback in self._listeners:
            callback()
        self.page.update()

    def notify(self, message: str, *, error: bool = False) -> None:
        self.page.show_dialog(
            ft.SnackBar(
                content=ft.Text(message, color=ft.Colors.WHITE),
                bgcolor=EXPENSE_COLOR if error else INCOME_COLOR,
                behavior=ft.SnackBarBehavior.FLOATING,
                duration=ft.Duration(seconds=3),
                width=420,
            )
        )
