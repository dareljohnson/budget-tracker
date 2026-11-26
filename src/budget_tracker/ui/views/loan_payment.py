"""Shared principal/interest/escrow fields for loan payments.

Only the principal portion reduces the loan. Interest and escrow are expenses, so they
lower net worth without pretending the unpaid principal fell by the whole payment.
"""

from __future__ import annotations

from collections.abc import Callable

import flet as ft

from ...formatting import format_money, parse_money, parse_signed_money
from ...models import Category, TransactionType
from ..theme import EXPENSE_COLOR, GAP, SEED_COLOR, fs

INTEREST_CATEGORY_NAMES = ("Mortgage interest", "Interest", "Housing", "Debt payments")
ESCROW_CATEGORY_NAMES = ("Mortgage escrow", "Escrow", "Property tax", "Taxes", "Housing")


def default_category_id(categories: list[Category], names: tuple[str, ...]) -> int | None:
    """The first preferred expense category that exists, else the first expense category."""
    expense = [category for category in categories if category.type is TransactionType.EXPENSE]
    by_name = {category.name.casefold(): category.id for category in expense}
    for name in names:
        found = by_name.get(name.casefold())
        if found is not None:
            return found
    return expense[0].id if expense else None


class LoanSplitFields:
    """Interest, escrow and a live principal preview for a loan payment."""

    def __init__(
        self,
        categories: list[Category],
        total_getter: Callable[[], str | None],
        on_change: Callable[[], None] | None = None,
        interest_estimator: Callable[[], int | None] | None = None,
        escrow_estimator: Callable[[int], int | None] | None = None,
        initial_extra_cents: int = 0,
    ) -> None:
        self._total_getter = total_getter
        self._on_change = on_change
        self._interest_estimator = interest_estimator
        self._escrow_estimator = escrow_estimator
        self._escrow_manual = False
        expense = [category for category in categories if category.type is TransactionType.EXPENSE]
        options = [ft.DropdownOption(key=str(category.id), text=category.name) for category in expense]
        interest_default = default_category_id(categories, INTEREST_CATEGORY_NAMES)
        escrow_default = default_category_id(categories, ESCROW_CATEGORY_NAMES)
        self.interest = ft.TextField(
            label="Interest",
            prefix_icon=ft.Icons.ATTACH_MONEY,
            keyboard_type=ft.KeyboardType.NUMBER,
            on_change=lambda _: self._field_changed(),
        )
        self.escrow = ft.TextField(
            label="Escrow",
            prefix_icon=ft.Icons.ATTACH_MONEY,
            keyboard_type=ft.KeyboardType.NUMBER,
            on_change=lambda _: self._escrow_changed(),
        )
        self.extra = ft.TextField(
            label="Extra principal",
            prefix_icon=ft.Icons.ATTACH_MONEY,
            keyboard_type=ft.KeyboardType.NUMBER,
            value=f"{initial_extra_cents / 100:.2f}" if initial_extra_cents else "",
            on_change=lambda _: self._field_changed(),
        )
        self.interest_category = ft.Dropdown(
            label="Interest category",
            options=options,
            value=str(interest_default) if interest_default else None,
            on_select=lambda _: self._field_changed(),
        )
        self.escrow_category = ft.Dropdown(
            label="Escrow category",
            options=options,
            value=str(escrow_default) if escrow_default else None,
            on_select=lambda _: self._field_changed(),
        )
        self.principal = ft.Text("Principal —", size=fs(13), weight=ft.FontWeight.W_600)
        self.total_cash = ft.Text(
            "Total cash —", size=fs(12), color=ft.Colors.ON_SURFACE_VARIANT
        )
        self.estimate_button = ft.TextButton(
            content="Estimate interest",
            icon=ft.Icons.CALCULATE,
            visible=interest_estimator is not None,
            on_click=lambda _: self.estimate(),
            tooltip="Current balance × APR ÷ 12; replace it with the statement figure",
        )
        self.auto_escrow_button = ft.TextButton(
            content="Auto-fill escrow",
            icon=ft.Icons.AUTO_FIX_HIGH,
            visible=escrow_estimator is not None,
            on_click=lambda _: self.auto_fill_escrow(),
            tooltip="Total payment minus the scheduled principal and interest",
        )
        self.control = ft.Column(
            [
                self.interest,
                ft.Row([self.estimate_button, self.auto_escrow_button], spacing=8, wrap=True),
                self.escrow,
                self.extra,
                self.principal,
                self.total_cash,
                ft.Row([self.interest_category, self.escrow_category], spacing=GAP),
                ft.Text(
                    "Only the principal reduces the loan; interest and escrow are expenses. "
                    "The interest estimate is a guide, not the servicer's exact figure. Escrow "
                    "is prefilled from the total payment minus the scheduled principal and "
                    "interest when a maturity date is set.",
                    size=fs(11),
                    color=ft.Colors.ON_SURFACE_VARIANT,
                ),
            ],
            spacing=10,
        )
        self.refresh()
        if interest_estimator is not None:
            estimate = interest_estimator()
            if estimate is not None:
                # Prefill without notifying the parent: this runs before the parent's
                # remaining controls exist.
                self.interest.value = f"{estimate / 100:.2f}"
                self.refresh()

    def estimate(self) -> None:
        """Fill the interest field from the balance × APR ÷ 12 estimate."""
        if self._interest_estimator is None:
            return
        estimate = self._interest_estimator()
        if estimate is None:
            return
        self.interest.value = f"{estimate / 100:.2f}"
        self.refresh()
        if self._on_change is not None:
            self._on_change()

    def auto_fill_escrow(self) -> None:
        """Re-enable and recompute the schedule-derived escrow."""
        self._escrow_manual = False
        self.refresh()
        if self._on_change is not None:
            self._on_change()

    def _field_changed(self) -> None:
        self.refresh()
        if self._on_change is not None:
            self._on_change()

    def _escrow_changed(self) -> None:
        # Once the owner types an escrow figure, stop replacing it from the schedule.
        self._escrow_manual = True
        self._field_changed()

    def refresh(self) -> None:
        """Update the principal preview and enable only the categories that are in use."""
        try:
            total = parse_money(self._total_getter())
            interest = parse_signed_money(self.interest.value)
            escrow = parse_signed_money(self.escrow.value)
            extra = parse_signed_money(self.extra.value)
        except ValueError:
            self.principal.value = "Principal —"
            self.principal.color = ft.Colors.ON_SURFACE_VARIANT
            self.total_cash.value = "Total cash —"
            return
        if self._escrow_estimator is not None and not self._escrow_manual:
            estimate = self._escrow_estimator(total)
            if estimate is not None:
                self.escrow.value = f"{estimate / 100:.2f}"
                escrow = estimate
        self.interest_category.disabled = interest <= 0
        self.escrow_category.disabled = escrow <= 0
        principal = total - interest - escrow + extra
        if principal < 0:
            self.principal.value = "Interest + escrow exceed the total"
            self.principal.color = EXPENSE_COLOR
        else:
            suffix = f" (incl. {format_money(extra)} extra)" if extra else ""
            self.principal.value = f"Principal {format_money(principal)}{suffix}"
            self.principal.color = SEED_COLOR
        self.total_cash.value = f"Total cash {format_money(total + extra)}"

    def amounts(self, total_cents: int) -> tuple[int, int, int]:
        """Return (interest, escrow, extra principal) or raise ``ValueError``."""
        interest = parse_signed_money(self.interest.value)
        escrow = parse_signed_money(self.escrow.value)
        extra = parse_signed_money(self.extra.value)
        if interest < 0 or escrow < 0 or extra < 0:
            raise ValueError("Interest, escrow and extra principal cannot be negative")
        if total_cents - interest - escrow < 0:
            raise ValueError("Interest and escrow cannot exceed the total payment")
        return interest, escrow, extra

    def has_extra(self) -> bool:
        try:
            return parse_signed_money(self.extra.value) > 0
        except ValueError:
            return False

    def category_ids(self) -> tuple[int | None, int | None]:
        """The chosen interest and escrow category ids."""
        interest = int(self.interest_category.value) if self.interest_category.value else None
        escrow = int(self.escrow_category.value) if self.escrow_category.value else None
        return interest, escrow

    def non_principal_cents(self) -> int:
        """Interest + escrow when both parse cleanly; 0 otherwise (used to hide match candidates)."""
        try:
            interest = parse_signed_money(self.interest.value)
            escrow = parse_signed_money(self.escrow.value)
        except ValueError:
            return 0
        return max(0, interest + escrow)
