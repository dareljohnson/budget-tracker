"""Bills: recurring payments with due dates, reminders and one-click 'Mark paid'."""

from __future__ import annotations

from datetime import date

import flet as ft

from ...formatting import format_money, parse_date, parse_money, parse_signed_money
from ...models import AccountKind, Bill, BillFrequency, DueBill, TransactionType
from ...repository import RepositoryError
from ..components import card, category_avatar, confirm_dialog, date_field, empty_state, page_header, stat_card
from ..context import AppContext
from ..theme import EXPENSE_COLOR, GAP, INCOME_COLOR, SEED_COLOR, WARNING_COLOR

REMINDER_CHOICES = (0, 1, 2, 3, 5, 7, 14)
STATUS_COLORS = {"overdue": EXPENSE_COLOR, "today": WARNING_COLOR, "soon": WARNING_COLOR}
BILL_KIND, CARD_KIND = "bill", "card"


def monthly_equivalent(bill: Bill) -> int:
    """Approximate monthly cost of a recurring bill, in cents (one-time bills count as 0)."""
    return {
        BillFrequency.WEEKLY: round(bill.amount_cents * 52 / 12),
        BillFrequency.MONTHLY: bill.amount_cents,
        BillFrequency.YEARLY: round(bill.amount_cents / 12),
    }.get(bill.frequency, 0)


def status_chip(due: DueBill) -> ft.Container:
    color = STATUS_COLORS[due.status]
    return ft.Container(
        content=ft.Text(due.due_text, size=11, weight=ft.FontWeight.W_600, color=color),
        bgcolor=ft.Colors.with_opacity(0.12, color),
        border_radius=10,
        padding=ft.Padding.symmetric(horizontal=8, vertical=3),
    )


def bill_avatar(bill: Bill, size: int = 40) -> ft.Container:
    if bill.is_card_payment:
        return category_avatar("CREDIT_CARD", SEED_COLOR, size)
    return category_avatar(bill.category_icon, bill.category_color, size)


class BillsView:
    def __init__(self, ctx: AppContext) -> None:
        self.ctx = ctx
        self.reminder = ft.Dropdown(
            label="Remind me",
            width=190,
            dense=True,
            text_size=14,
            value=str(ctx.repo.reminder_days()),
            options=[ft.DropdownOption(key=str(d), text="On the due date" if d == 0 else f"{d} day{'s' if d > 1 else ''} before") for d in REMINDER_CHOICES],
            on_select=self._reminder_changed,
        )
        self.stats = ft.ResponsiveRow(spacing=GAP, run_spacing=GAP)
        self.list = ft.Column(spacing=0)
        self.control = ft.Column(
            [
                page_header(
                    "Bills",
                    "Recurring payments and due-date reminders",
                    [self.reminder, ft.FilledButton(content="Add Bill", icon=ft.Icons.ADD, on_click=lambda _: BillEditor(ctx, None).open())],
                ),
                self.stats,
                card(self.list, padding=8),
            ],
            spacing=GAP,
            scroll=ft.ScrollMode.AUTO,
            expand=True,
        )
        ctx.on_data_changed(self.refresh)
        self.refresh()

    def refresh(self) -> None:
        today = date.today()
        bills = self.ctx.repo.list_bills()
        due = {d.bill.id: d for d in self.ctx.service.due_bills(today)}
        overdue = [d for d in due.values() if d.status == "overdue"]
        upcoming = [d for d in due.values() if d.status != "overdue"]
        active = [b for b in bills if b.active]
        self.stats.controls = [
            stat_card("Overdue", str(len(overdue)), ft.Icons.ERROR_OUTLINE, EXPENSE_COLOR, format_money(sum(d.amount_cents for d in overdue))),
            stat_card("Due Soon", str(len(upcoming)), ft.Icons.NOTIFICATIONS_ACTIVE, WARNING_COLOR, format_money(sum(d.amount_cents for d in upcoming))),
            stat_card(
                "Monthly Bills",
                format_money(sum(monthly_equivalent(b) for b in active)),
                ft.Icons.EVENT_REPEAT,
                SEED_COLOR,
                f"{len(active)} active bill(s)",
            ),
        ]
        for control in self.stats.controls:
            control.col = {"xs": 12, "sm": 6, "xl": 4}
        if not bills:
            self.list.controls = [
                empty_state(
                    ft.Icons.EVENT_NOTE,
                    "No bills yet",
                    "Add rent, utilities, subscriptions or credit card payments to get reminded before they're due.",
                    ft.FilledButton(content="Add Bill", icon=ft.Icons.ADD, on_click=lambda _: BillEditor(self.ctx, None).open()),
                )
            ]
            return
        rows: list[ft.Control] = []
        for i, bill in enumerate(bills):
            if i:
                rows.append(ft.Divider(height=1, color=ft.Colors.OUTLINE_VARIANT))
            rows.append(self._row(bill, due.get(bill.id), today))
        self.list.controls = rows

    def _row(self, bill: Bill, due: DueBill | None, today: date) -> ft.Container:
        if bill.is_card_payment:
            payment_kind = "Loan payment" if bill.pay_to_account_kind is AccountKind.LOAN else "Card payment"
            detail = f"{payment_kind}  •  {bill.account_name} → {bill.pay_to_account_name}"
        else:
            detail = f"{bill.category_name}  •  from {bill.account_name}"
        if bill.amount_cents:
            amount = format_money(bill.amount_cents)
        elif due:
            amount = format_money(due.amount_cents)
        else:
            amount = "Full balance"
        if not bill.active:
            when: ft.Control = ft.Text("Paid", size=12, color=INCOME_COLOR, weight=ft.FontWeight.W_600)
        elif due:
            when = status_chip(due)
        else:
            when = ft.Text(f"Due {bill.next_due:%b %d, %Y}", size=12, color=ft.Colors.ON_SURFACE_VARIANT)
        actions: list[ft.Control] = []
        if bill.active:
            actions += [
                ft.FilledTonalButton(content="Mark paid", icon=ft.Icons.CHECK, on_click=lambda _: MarkPaidDialog(self.ctx, bill, today).open()),
                ft.IconButton(ft.Icons.SKIP_NEXT, tooltip="Skip this payment", icon_size=20, on_click=lambda _: self._skip(bill)),
            ]
        actions += [
            ft.IconButton(ft.Icons.EDIT_OUTLINED, tooltip="Edit", icon_size=20, on_click=lambda _: BillEditor(self.ctx, bill).open()),
            ft.IconButton(ft.Icons.DELETE_OUTLINE, tooltip="Delete", icon_size=20, icon_color=EXPENSE_COLOR, on_click=lambda _: self._confirm_delete(bill)),
        ]
        return ft.Container(
            content=ft.Row(
                [
                    bill_avatar(bill),
                    ft.Column(
                        [
                            ft.Text(bill.name, size=15, weight=ft.FontWeight.W_600),
                            ft.Text(f"{bill.frequency.label}  •  {detail}", size=12, color=ft.Colors.ON_SURFACE_VARIANT),
                        ],
                        spacing=2,
                        expand=True,
                    ),
                    ft.Column(
                        [ft.Text(amount, size=15, weight=ft.FontWeight.W_600), when],
                        spacing=4,
                        horizontal_alignment=ft.CrossAxisAlignment.END,
                    ),
                    ft.Row(actions, spacing=4),
                ],
                spacing=GAP,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
            padding=ft.Padding.symmetric(horizontal=12, vertical=12),
            opacity=1.0 if bill.active else 0.6,
        )

    def _reminder_changed(self, e: ft.Event[ft.Dropdown]) -> None:
        self.ctx.repo.set_reminder_days(int(e.control.value))
        self.ctx.data_changed()

    def _skip(self, bill: Bill) -> None:
        self.ctx.repo.skip_bill(bill.id)
        self.ctx.notify(f"Skipped '{bill.name}'")
        self.ctx.data_changed()

    def _confirm_delete(self, bill: Bill) -> None:
        def delete() -> None:
            self.ctx.repo.delete_bill(bill.id)
            self.ctx.notify(f"Bill '{bill.name}' deleted")
            self.ctx.data_changed()

        confirm_dialog(self.ctx.page, "Delete bill?", f"'{bill.name}' will be removed. Past payments stay in your transactions.", delete)


class MarkPaidDialog:
    """Confirm the amount, date and account, then record the payment and advance the due date."""

    def __init__(self, ctx: AppContext, bill: Bill, today: date | None = None) -> None:
        self.ctx = ctx
        self.bill = bill
        sources = [a for a in ctx.repo.list_accounts() if a.id != bill.pay_to_account_id]
        amount = ctx.repo.amount_due(bill)
        self.amount = ft.TextField(
            label="Amount paid",
            prefix_icon=ft.Icons.ATTACH_MONEY,
            value=f"{amount / 100:.2f}" if amount else "",
            keyboard_type=ft.KeyboardType.NUMBER,
            autofocus=True,
            on_submit=self._save,
        )
        self.date = date_field(ctx.page, "Paid on", today or date.today())
        self.account = ft.Dropdown(
            label="Paid from",
            options=[ft.DropdownOption(key=str(a.id), text=f"{a.name} ({a.kind.label})") for a in sources],
            value=str(bill.account_id) if any(a.id == bill.account_id for a in sources) else None,
        )
        target = f"to {bill.pay_to_account_name} (transfer)" if bill.is_card_payment else f"as {bill.category_name} expense"
        self.dialog = ft.AlertDialog(
            modal=True,
            title=ft.Text(f"Pay {bill.name}"),
            content=ft.Container(
                width=420,
                content=ft.Column(
                    [
                        ft.Text(f"Due {bill.next_due:%b %d, %Y}. Records the payment {target}.", size=12, color=ft.Colors.ON_SURFACE_VARIANT),
                        self.amount,
                        self.date,
                        self.account,
                    ],
                    spacing=14,
                    tight=True,
                    horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
                ),
            ),
            actions=[
                ft.TextButton(content="Cancel", on_click=lambda _: ctx.page.pop_dialog()),
                ft.FilledButton(content="Mark paid", icon=ft.Icons.CHECK, on_click=self._save),
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
            paid_on = parse_date(self.date.value)
            self.date.error = None
        except ValueError as exc:
            self.date.error = str(exc)
            valid = False
        self.account.error_text = None if self.account.value else "Select an account"
        valid = valid and bool(self.account.value)
        if not valid:
            self.dialog.update()
            return
        try:
            self.ctx.repo.mark_bill_paid(self.bill.id, paid_on=paid_on, amount_cents=amount, account_id=int(self.account.value))
        except RepositoryError as exc:
            self.ctx.notify(str(exc), error=True)
            return
        self.ctx.page.pop_dialog()
        self.ctx.notify(f"'{self.bill.name}' marked as paid")
        self.ctx.data_changed()


class BillEditor:
    """Create or edit a bill: a regular expense bill or a payment to a credit card/loan account."""

    def __init__(self, ctx: AppContext, bill: Bill | None) -> None:
        self.ctx = ctx
        self.bill = bill
        self.kind = CARD_KIND if bill and bill.is_card_payment else BILL_KIND
        accounts = ctx.repo.list_accounts()
        account_options = [ft.DropdownOption(key=str(a.id), text=f"{a.name} ({a.kind.label})") for a in accounts]
        default_source = next((a for a in accounts if a.kind == AccountKind.CHECKING), next((a for a in accounts if not a.kind.is_liability), None))
        default_card = next((a for a in accounts if a.kind.is_liability), None)

        self.kind_toggle = ft.SegmentedButton(
            selected=[self.kind],
            segments=[
                ft.Segment(value=BILL_KIND, label=ft.Text("Bill"), icon=ft.Icon(ft.Icons.RECEIPT)),
                ft.Segment(value=CARD_KIND, label=ft.Text("Card or loan payment"), icon=ft.Icon(ft.Icons.CREDIT_CARD)),
            ],
            on_change=self._kind_changed,
        )
        self.hint = ft.Text(size=12, color=ft.Colors.ON_SURFACE_VARIANT)
        self.name = ft.TextField(label="Name", value=bill.name if bill else "", max_length=60, on_submit=self._save)
        self.amount = ft.TextField(
            label="Amount",
            prefix_icon=ft.Icons.ATTACH_MONEY,
            value=f"{bill.amount_cents / 100:.2f}" if bill and bill.amount_cents else "",
            keyboard_type=ft.KeyboardType.NUMBER,
            expand=True,
            on_submit=self._save,
        )
        self.frequency = ft.Dropdown(
            label="Repeats",
            value=(bill.frequency if bill else BillFrequency.MONTHLY).value,
            options=[ft.DropdownOption(key=f.value, text=f.label) for f in BillFrequency],
            expand=True,
        )
        self.next_due = date_field(ctx.page, "Next due date", bill.next_due if bill else date.today())
        self.account = ft.Dropdown(
            label="Pay from",
            options=account_options,
            value=str(bill.account_id) if bill else (str(default_source.id) if default_source else None),
            expand=True,
        )
        self.category = ft.Dropdown(
            label="Expense category",
            options=[ft.DropdownOption(key=str(c.id), text=c.name) for c in ctx.repo.list_categories(TransactionType.EXPENSE)],
            value=str(bill.category_id) if bill and bill.category_id else None,
            expand=True,
        )
        self.pay_to = ft.Dropdown(
            label="Card or account to pay",
            options=list(account_options),
            value=str(bill.pay_to_account_id) if bill and bill.pay_to_account_id else (str(default_card.id) if default_card else None),
            expand=True,
        )
        self.payment_row = ft.Row(spacing=12)
        self._apply_kind()

        self.dialog = ft.AlertDialog(
            modal=True,
            title=ft.Text("Edit Bill" if bill else "New Bill"),
            content=ft.Container(
                width=520,
                content=ft.Column(
                    [
                        self.kind_toggle,
                        self.hint,
                        self.name,
                        ft.Row([self.amount, self.frequency], spacing=12),
                        self.next_due,
                        self.payment_row,
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

    def _apply_kind(self) -> None:
        is_card = self.kind == CARD_KIND
        self.category.visible = not is_card
        self.pay_to.visible = is_card
        self.payment_row.controls = [self.account, self.pay_to if is_card else self.category]
        self.amount.hint_text = "0 = pay the full balance owed" if is_card else None
        self.hint.value = (
            "Paid as a transfer to a card or loan account."
            if is_card
            else "A recurring expense such as rent, utilities or a subscription."
        )

    def _kind_changed(self, e: ft.Event[ft.SegmentedButton]) -> None:
        self.kind = e.control.selected[0]
        self._apply_kind()

    def _save(self, _: ft.Event) -> None:
        is_card = self.kind == CARD_KIND
        valid = True
        for field in (self.name, self.amount, self.next_due):
            field.error = None
        for dropdown in (self.account, self.category, self.pay_to):
            dropdown.error_text = None
        name = (self.name.value or "").strip()
        if not name:
            self.name.error = "Name is required"
            valid = False
        try:
            amount = parse_signed_money(self.amount.value) if is_card else parse_money(self.amount.value)
            if amount < 0:
                raise ValueError("Amount cannot be negative")
        except ValueError as exc:
            self.amount.error = str(exc)
            valid = False
        try:
            next_due = parse_date(self.next_due.value)
        except ValueError as exc:
            self.next_due.error = str(exc)
            valid = False
        if not self.account.value:
            self.account.error_text = "Select an account"
            valid = False
        target = self.pay_to if is_card else self.category
        if not target.value:
            target.error_text = "Required"
            valid = False
        elif is_card and target.value == self.account.value:
            target.error_text = "Choose a different account"
            valid = False
        if not valid:
            self.dialog.update()
            return

        keep_anchor = self.bill is not None and self.bill.next_due == next_due
        bill = Bill(
            id=self.bill.id if self.bill else None,
            name=name,
            amount_cents=amount,
            frequency=BillFrequency(self.frequency.value),
            next_due=next_due,
            account_id=int(self.account.value),
            category_id=None if is_card else int(self.category.value),
            pay_to_account_id=int(self.pay_to.value) if is_card else None,
            anchor_day=self.bill.anchor_day if keep_anchor else None,
            active=self.bill.active if self.bill else True,
        )
        try:
            if bill.id:
                self.ctx.repo.update_bill(bill)
            else:
                self.ctx.repo.add_bill(bill)
        except RepositoryError as exc:
            self.ctx.notify(str(exc), error=True)
            return
        self.ctx.page.pop_dialog()
        self.ctx.notify(f"Bill '{name}' saved")
        self.ctx.data_changed()
