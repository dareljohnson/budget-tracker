"""Manual balance checks: compare an account with a bank-stated balance and adjust it."""

from __future__ import annotations

from datetime import date

import flet as ft

from ...formatting import format_money, month_key, parse_date, parse_money, parse_signed_money
from ...models import Account, AccountKind, Reconciliation
from ...repository import RepositoryError
from ..components import date_field
from ..context import AppContext
from ..theme import EXPENSE_COLOR, GAP, INCOME_COLOR, WARNING_COLOR


class BalanceCheckDialog:
    """Check one account against a stated balance, record an adjustment and show its history.

    The ledger stores credit cards as negative balances (money owed), while banks show the amount
    owed as a positive figure. For liability accounts the stated input is therefore negated before
    it reaches :meth:`BudgetService.check_balance`, and every reconciliation value is negated again
    for display.
    """

    def __init__(self, ctx: AppContext, account: Account) -> None:
        self.ctx = ctx
        self.account = account
        self.is_liability = account.kind.is_liability
        self.last_check: Reconciliation | None = None
        self._stated_cents: int | None = None
        self._adjusted_check: Reconciliation | None = None

        self.status = ft.Text(size=12, color=ft.Colors.ON_SURFACE_VARIANT)
        self.date = date_field(ctx.page, "As of", date.today())
        self.stated = ft.TextField(
            label="Amount owed" if self.is_liability else "Stated balance",
            prefix_icon=ft.Icons.ATTACH_MONEY,
            hint_text="Positive amount the bank shows" if self.is_liability else "Balance the bank shows",
            keyboard_type=ft.KeyboardType.NUMBER,
            autofocus=True,
            on_submit=self._save,
        )
        self.note = ft.TextField(label="Note (optional)", max_length=140, on_submit=self._save)
        self.minimum = ft.TextField(
            label="Statement minimum payment (optional)",
            prefix_icon=ft.Icons.PAYMENT,
            keyboard_type=ft.KeyboardType.NUMBER,
            hint_text="Minimum printed on this statement",
            visible=account.kind is AccountKind.CREDIT_CARD,
        )

        self.computed_value = ft.Text(size=15, weight=ft.FontWeight.W_600)
        self.stated_value = ft.Text(size=15, weight=ft.FontWeight.W_600)
        self.difference_value = ft.Text(size=15, weight=ft.FontWeight.W_600)
        self.result = ft.Column(
            [
                self._amount_row("Computed", self.computed_value),
                self._amount_row("Stated", self.stated_value),
                self._amount_row("Difference (ledger - statement)", self.difference_value),
            ],
            spacing=4,
            tight=True,
            visible=False,
        )

        self.adjustment = ft.TextField(
            label="Correction to ledger",
            prefix_icon=ft.Icons.TUNE,
            keyboard_type=ft.KeyboardType.NUMBER,
            hint_text="Added to the ledger balance (positive raises it)",
            expand=True,
            on_submit=self._adjust,
        )
        self.adjust_button = ft.FilledTonalButton(
            content="Record adjustment",
            icon=ft.Icons.TUNE,
            on_click=self._adjust,
        )
        self.adjust_row = ft.Column(
            [
                ft.Text(
                    "The ledger differs from the stated balance. Record an adjustment to reconcile it.",
                    size=12,
                    color=WARNING_COLOR,
                ),
                ft.Row(
                    [self.adjustment, self.adjust_button],
                    spacing=GAP,
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                ),
            ],
            spacing=8,
            tight=True,
            visible=False,
        )

        self.history = ft.Column(spacing=2, tight=True)
        self.dialog = ft.AlertDialog(
            modal=True,
            title=ft.Text(f"Check balance — {account.name}"),
            content=ft.Container(
                width=560,
                height=500,
                content=ft.Column(
                    [
                        self.status,
                        self.date,
                        self.stated,
                        self.minimum,
                        self.note,
                        self.result,
                        self.adjust_row,
                        ft.Divider(height=1),
                        ft.Text("History", size=14, weight=ft.FontWeight.W_600),
                        self.history,
                    ],
                    spacing=12,
                    tight=True,
                    scroll=ft.ScrollMode.AUTO,
                ),
            ),
            actions=[
                ft.TextButton(content="Close", on_click=lambda _: ctx.page.pop_dialog()),
                ft.FilledButton(
                    content="Check balance",
                    icon=ft.Icons.FACT_CHECK_OUTLINED,
                    on_click=self._save,
                ),
            ],
            actions_alignment=ft.MainAxisAlignment.END,
        )
        self._refresh_status()
        self._render_history()

    def open(self) -> None:
        self.ctx.page.show_dialog(self.dialog)

    @staticmethod
    def _amount_row(label: str, value: ft.Text) -> ft.Row:
        return ft.Row(
            [ft.Text(label, size=13, color=ft.Colors.ON_SURFACE_VARIANT, expand=True), value],
            spacing=GAP,
        )

    # ---------- rendering ----------
    def _display(self, cents: int) -> int:
        """Flip the sign for liability accounts so owed amounts read positive."""
        return -cents if self.is_liability else cents

    def _refresh_status(self) -> None:
        last = self.ctx.service.last_reconciliation(self.account.id)
        if last is None:
            self.status.value = "No balance checks yet."
            self.status.color = ft.Colors.ON_SURFACE_VARIANT
            return
        reconciled = last.difference_cents == 0
        state = "reconciled" if reconciled else "not reconciled"
        self.status.value = f"Last checked {last.as_of:%b %d, %Y}: {state}"
        self.status.color = INCOME_COLOR if reconciled else EXPENSE_COLOR

    def _render_history(self) -> None:
        records = self.ctx.repo.list_reconciliations(self.account.id)
        if not records:
            self.history.controls = [
                ft.Text("No balance checks yet.", size=12, color=ft.Colors.ON_SURFACE_VARIANT)
            ]
            return
        header = ft.Row(
            [
                ft.Text("Date", size=11, width=96, color=ft.Colors.ON_SURFACE_VARIANT),
                ft.Text("Stated", size=11, width=82, color=ft.Colors.ON_SURFACE_VARIANT, text_align=ft.TextAlign.RIGHT),
                ft.Text("Computed", size=11, width=82, color=ft.Colors.ON_SURFACE_VARIANT, text_align=ft.TextAlign.RIGHT),
                ft.Text("Difference", size=11, width=82, color=ft.Colors.ON_SURFACE_VARIANT, text_align=ft.TextAlign.RIGHT),
                ft.Text("Note", size=11, expand=True, color=ft.Colors.ON_SURFACE_VARIANT),
            ],
            spacing=6,
        )
        rows: list[ft.Control] = [header]
        for record in records:
            reconciled = record.difference_cents == 0
            rows.append(
                ft.Row(
                    [
                        ft.Text(f"{record.as_of:%b %d, %Y}", size=12, width=96),
                        ft.Text(format_money(self._display(record.stated_cents)), size=12, width=82, text_align=ft.TextAlign.RIGHT),
                        ft.Text(format_money(self._display(record.computed_cents)), size=12, width=82, text_align=ft.TextAlign.RIGHT),
                        ft.Text(
                            format_money(self._display(record.difference_cents)),
                            size=12,
                            width=82,
                            text_align=ft.TextAlign.RIGHT,
                            color=INCOME_COLOR if reconciled else EXPENSE_COLOR,
                            weight=ft.FontWeight.W_600,
                        ),
                        ft.Text(
                            record.note or ("Reconciled" if reconciled else "Unresolved"),
                            size=12,
                            expand=True,
                            max_lines=1,
                            overflow=ft.TextOverflow.ELLIPSIS,
                            color=ft.Colors.ON_SURFACE_VARIANT if record.note else (INCOME_COLOR if reconciled else EXPENSE_COLOR),
                        ),
                    ],
                    spacing=6,
                )
            )
        self.history.controls = rows

    def _show_result(self, reconciliation: Reconciliation) -> None:
        self.last_check = reconciliation
        self._stated_cents = reconciliation.stated_cents
        self.computed_value.value = format_money(self._display(reconciliation.computed_cents))
        self.stated_value.value = format_money(self._display(reconciliation.stated_cents))
        self.difference_value.value = format_money(self._display(reconciliation.difference_cents))
        resolved = reconciliation.difference_cents == 0
        self.difference_value.color = INCOME_COLOR if resolved else EXPENSE_COLOR
        self.result.visible = True
        self.adjust_button.disabled = False
        self.adjust_row.visible = not resolved
        if not resolved:
            # The signed ledger correction that moves computed to the stated balance.
            needed = reconciliation.stated_cents - reconciliation.computed_cents
            self.adjustment.value = f"{needed / 100:.2f}"
            self.adjustment.error = None

    # ---------- actions ----------
    def _save(self, _: ft.Event) -> None:
        valid = True
        self.date.error = None
        self.stated.error = None
        try:
            as_of = parse_date(self.date.value)
        except ValueError as exc:
            self.date.error = str(exc)
            valid = False
        try:
            raw_stated = (self.stated.value or "").strip()
            if not raw_stated:
                raise ValueError("Enter the stated balance")
            entered = parse_signed_money(raw_stated)
            if self.is_liability and entered < 0:
                raise ValueError("Enter the amount owed as a positive number")
        except ValueError as exc:
            self.stated.error = str(exc)
            valid = False
        if not valid:
            self.dialog.update()
            return
        stated_cents = -entered if self.is_liability else entered
        note = (self.note.value or "").strip()
        try:
            reconciliation = self.ctx.service.check_balance(self.account.id, as_of, stated_cents, note)
        except RepositoryError as exc:
            self.ctx.notify(str(exc), error=True)
            return
        self._show_result(reconciliation)
        if self.is_liability:
            raw_minimum = (self.minimum.value or "").strip()
            if raw_minimum:
                try:
                    self.ctx.service.record_minimum_payment(
                        self.account.id, month_key(as_of), parse_money(raw_minimum)
                    )
                except (ValueError, RepositoryError) as exc:
                    # The balance check itself is already recorded; surface this separately.
                    self.ctx.notify(
                        f"Balance checked, but the minimum payment was not recorded: {exc}",
                        error=True,
                    )
        self._refresh_status()
        self._render_history()
        self.ctx.data_changed()

    def _adjust(self, _: ft.Event) -> None:
        if self.last_check is None or self._stated_cents is None:
            self.ctx.notify("Run a balance check first", error=True)
            return
        if self.last_check is self._adjusted_check:
            # The correction is already in the ledger; never post it twice for the same check.
            self.ctx.notify("An adjustment was already recorded for this balance check", error=True)
            return
        self.adjustment.error = None
        try:
            amount = parse_signed_money(self.adjustment.value)
        except ValueError as exc:
            self.adjustment.error = str(exc)
            self.dialog.update()
            return
        if amount == 0:
            self.adjustment.error = "Enter a nonzero amount"
            self.dialog.update()
            return
        note = (self.note.value or "").strip()
        as_of = self.last_check.as_of
        recorded = False
        try:
            self.ctx.service.adjust_balance(self.account.id, as_of, amount, note)
            recorded = True
            # Disable the action before the follow-up so a failed recheck cannot invite a duplicate.
            self._adjusted_check = self.last_check
            self.adjust_button.disabled = True
            follow_up = self.ctx.service.check_balance(self.account.id, as_of, self._stated_cents, note)
        except RepositoryError as exc:
            if recorded:
                # The correction is already in the ledger; refresh even though the follow-up failed.
                self._refresh_status()
                self._render_history()
                self.ctx.data_changed()
                self.ctx.notify(
                    f"The adjustment was recorded, but the follow-up balance check failed: {exc}",
                    error=True,
                )
            else:
                self.ctx.notify(str(exc), error=True)
            return
        self._show_result(follow_up)
        self._refresh_status()
        self._render_history()
        self.ctx.data_changed()
