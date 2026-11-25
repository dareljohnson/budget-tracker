"""Accounts: checking, savings, credit cards and cash with live balances and card payments."""

from __future__ import annotations

from datetime import date

import flet as ft

from ...formatting import format_money, month_label, parse_signed_money
from ...models import Account, AccountBalance, AccountKind, TransactionType
from ...repository import RepositoryError
from ..components import account_avatar, balance_text, card, confirm_dialog, empty_state, page_header, stat_card
from ..context import AppContext
from ..theme import EXPENSE_COLOR, GAP, INCOME_COLOR, PALETTE, SEED_COLOR, fs
from .minimum_payments import MinimumPaymentDialog, minimum_status_chip
from .reconcile import BalanceCheckDialog
from .transactions import TransactionEditor

# Every account card gets the same height so the grid stays on a single rhythm.
# Scaled with the text-size setting so larger text cannot clip the content.
ACCOUNT_CARD_HEIGHT = 250


def _equity_metric(label: str, value: str, *, emphasize: bool = False) -> ft.Column:
    """One compact label-over-value metric in a real-estate card's equity strip."""
    return ft.Column(
        [
            ft.Text(label, size=fs(11), color=ft.Colors.ON_SURFACE_VARIANT),
            ft.Text(
                value,
                size=fs(13),
                weight=ft.FontWeight.W_600 if emphasize else ft.FontWeight.W_500,
                color=SEED_COLOR if emphasize else None,
                max_lines=1,
                overflow=ft.TextOverflow.ELLIPSIS,
            ),
        ],
        spacing=0,
        expand=True,
    )


class AccountsView:
    def __init__(self, ctx: AppContext) -> None:
        self.ctx = ctx
        self.stats = ft.ResponsiveRow(spacing=GAP, run_spacing=GAP)
        # ResponsiveRow treats STRETCH like START when its rows wrap, so equal card
        # heights come from ACCOUNT_CARD_HEIGHT on each card instead.
        self.grid = ft.ResponsiveRow(spacing=GAP, run_spacing=GAP)
        self.control = ft.Column(
            [
                page_header(
                    "Accounts",
                    "Bank accounts, cash, investments, property and debts in one place",
                    [
                        ft.OutlinedButton(
                            content="Transfer",
                            icon=ft.Icons.SWAP_HORIZ,
                            on_click=lambda _: TransactionEditor(ctx, None, preset_type=TransactionType.TRANSFER).open(),
                        ),
                        ft.FilledButton(content="Add Account", icon=ft.Icons.ADD, on_click=lambda _: AccountEditor(ctx, None).open()),
                    ],
                ),
                self.stats,
                self.grid,
            ],
            spacing=GAP,
            scroll=ft.ScrollMode.AUTO,
            expand=True,
        )
        ctx.on_data_changed(self.refresh)
        self.refresh()

    def refresh(self) -> None:
        balances = self.ctx.repo.account_balances()
        net_worth = sum(b.balance_cents for b in balances)
        assets = sum(b.balance_cents for b in balances if b.balance_cents > 0)
        liquid_assets = sum(
            b.balance_cents
            for b in balances
            if b.balance_cents > 0 and b.account.liquid and not b.account.kind.is_liability
        )
        other_assets = assets - liquid_assets
        # Only liabilities count as debts here; an overdrawn checking or savings account is not a
        # debt (it still lowers net worth above and shows on its own card).
        debts = sum(
            -b.balance_cents
            for b in balances
            if b.account.kind.is_liability and b.balance_cents < 0
        )
        self.stats.controls = [
            stat_card("Net Worth", format_money(net_worth), ft.Icons.ACCOUNT_BALANCE_WALLET, SEED_COLOR, "Assets minus debts"),
            stat_card("Liquid Assets", format_money(liquid_assets), ft.Icons.PAYMENTS, INCOME_COLOR, "Checking, savings and cash"),
            stat_card("Other Assets", format_money(other_assets), ft.Icons.ACCOUNT_BALANCE, SEED_COLOR, "Investments, vehicles and other"),
            stat_card("Debts", format_money(debts), ft.Icons.CREDIT_CARD, EXPENSE_COLOR, "Credit card and loan balances owed"),
        ]
        for control in self.stats.controls:
            control.col = {"xs": 12, "sm": 6, "xl": 3}
        self.grid.controls = [self._account_card(b) for b in balances] or [
            card(
                empty_state(
                    ft.Icons.ACCOUNT_BALANCE,
                    "No accounts",
                    "Add checking, savings, cash, a Real Estate account for property, "
                    "or an Other account for investments and vehicles.",
                ),
                col=12,
            )
        ]

    def _account_card(self, balance: AccountBalance) -> ft.Container:
        account = balance.account
        usage = self.ctx.repo.account_usage(account.id)
        transactions = self.ctx.repo.transaction_count(account.id)
        linked_debts = self.ctx.repo.debt_usage(account.id)
        is_liability = account.kind.is_liability
        is_credit_card = account.kind is AccountKind.CREDIT_CARD
        is_real_estate = account.kind.is_real_estate
        actions: list[ft.Control] = []
        if is_liability:
            actions.append(
                ft.FilledTonalButton(
                    content="Pay card" if is_credit_card else "Pay loan",
                    icon=ft.Icons.PAYMENT,
                    on_click=lambda _: self._pay_card(balance),
                    tooltip="Record a payment from another account (a transfer, not an expense)",
                )
            )
        actions += [
            ft.IconButton(
                ft.Icons.FACT_CHECK_OUTLINED,
                tooltip="Check balance",
                icon_size=20,
                on_click=lambda _: BalanceCheckDialog(self.ctx, account).open(),
            ),
            ft.IconButton(ft.Icons.EDIT_OUTLINED, tooltip="Edit", icon_size=20, on_click=lambda _: AccountEditor(self.ctx, account).open()),
            ft.IconButton(
                ft.Icons.DELETE_OUTLINE,
                tooltip="Delete" if not usage else "In use - cannot delete",
                icon_size=20,
                icon_color=EXPENSE_COLOR,
                disabled=bool(usage),
                on_click=lambda _: self._confirm_delete(account),
            ),
        ]
        caption = (
            "Market value"
            if is_real_estate
            else ("Owed" if is_liability and balance.owed_cents else "Balance")
        )
        mode = "Liability" if is_liability else ("Liquid" if account.liquid else "Non-liquid")
        if transactions:
            count = f"{transactions} transaction" + ("" if transactions == 1 else "s")
            details = f"{account.kind.label} • {mode} • {count}"
        elif linked_debts:
            details = f"{account.kind.label} • {mode} • linked to a debt"
        elif usage:
            details = f"{account.kind.label} • {mode} • in use by an income schedule"
        else:
            details = f"{account.kind.label} • {mode} • 0 transactions"
        if account.number_last4:
            details += f" • •••• {account.number_last4}"
        latest = self.ctx.service.last_reconciliation(account.id)
        if latest is not None and latest.difference_cents == 0:
            details += f" • reconciled {latest.as_of:%Y-%m-%d}"
        heading: list[ft.Control] = [
            ft.Text(account.name, size=fs(16), weight=ft.FontWeight.W_600, max_lines=1, overflow=ft.TextOverflow.ELLIPSIS),
            ft.Text(
                details,
                size=fs(12),
                color=ft.Colors.ON_SURFACE_VARIANT,
                max_lines=1,
                overflow=ft.TextOverflow.ELLIPSIS,
                tooltip=details,
            ),
        ]
        minimum = (
            self.ctx.service.current_minimum_payment(account.id, as_of=date.today())
            if is_credit_card
            else None
        )
        header = ft.Row(
            [
                account_avatar(account, 44),
                ft.Column(heading, spacing=2, expand=True),
            ],
            spacing=GAP,
        )
        body: list[ft.Control] = [
            ft.Column(
                [ft.Text(caption, size=fs(13), color=ft.Colors.ON_SURFACE_VARIANT), balance_text(balance, size=fs(26))],
                spacing=0,
            )
        ]
        if is_real_estate:
            property_debt = self.ctx.repo.debt_for_property(account.id)
            if property_debt is not None:
                summary = self.ctx.service.debt_summary(property_debt.id)
                equity = balance.balance_cents - summary.balance_cents
                equity_strip = ft.Column(
                    [
                        ft.Row(
                            [
                                _equity_metric("Mortgage owed", format_money(summary.balance_cents)),
                                _equity_metric("Equity", format_money(equity), emphasize=True),
                            ],
                            spacing=GAP,
                            vertical_alignment=ft.CrossAxisAlignment.START,
                        )
                    ],
                    spacing=0,
                )
                if property_debt.original_balance_cents:
                    equity_strip.controls.append(
                        ft.Text(
                            f"Original principal {format_money(property_debt.original_balance_cents)}",
                            size=fs(11),
                            color=ft.Colors.ON_SURFACE_VARIANT,
                            max_lines=1,
                            overflow=ft.TextOverflow.ELLIPSIS,
                        )
                    )
                body.append(equity_strip)
        if is_credit_card:
            minimum_controls: list[ft.Control] = [
                ft.Text("Minimum", size=fs(13), color=ft.Colors.ON_SURFACE_VARIANT)
            ]
            if minimum:
                minimum_controls.append(
                    ft.Text(
                        f"{format_money(minimum.amount_cents)} · {month_label(minimum.month)}",
                        size=fs(13),
                        weight=ft.FontWeight.W_600,
                    )
                )
            minimum_controls.append(minimum_status_chip(minimum))
            minimum_controls.append(
                ft.IconButton(
                    ft.Icons.EDIT_OUTLINED,
                    tooltip="Record minimum payment",
                    icon_size=18,
                    on_click=lambda _: MinimumPaymentDialog(self.ctx, account).open(),
                )
            )
            body.append(
                ft.Row(
                    minimum_controls,
                    spacing=8,
                    wrap=True,
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                )
            )
        footer = ft.Row(actions, alignment=ft.MainAxisAlignment.END, spacing=4)
        return card(
            ft.Column(
                [
                    header,
                    ft.Column(body, spacing=12),
                    # Fixed height plus a flexible spacer pins the actions to the bottom
                    # while keeping the name and balance aligned across every card.
                    ft.Container(expand=True),
                    footer,
                ],
                spacing=12,
                expand=True,
            ),
            col={"xs": 12, "md": 6, "xl": 4},
            height=fs(ACCOUNT_CARD_HEIGHT),
        )

    def _pay_card(self, balance: AccountBalance) -> None:
        candidates = [a for a in self.ctx.repo.list_accounts() if not a.kind.is_liability]
        source = next((a for a in candidates if a.kind == AccountKind.CHECKING), candidates[0] if candidates else None)
        if source is None:
            self.ctx.notify("Add a checking or cash account to pay from", error=True)
            return
        TransactionEditor(
            self.ctx,
            None,
            preset_type=TransactionType.TRANSFER,
            preset_account_id=source.id,
            preset_to_account_id=balance.account.id,
            preset_amount_cents=balance.owed_cents or None,
        ).open()

    def _confirm_delete(self, account: Account) -> None:
        def delete() -> None:
            try:
                self.ctx.repo.delete_account(account.id)
            except RepositoryError as exc:
                self.ctx.notify(str(exc), error=True)
                return
            self.ctx.notify(f"Account '{account.name}' deleted")
            self.ctx.data_changed()

        confirm_dialog(self.ctx.page, "Delete account?", f"'{account.name}' will be permanently removed.", delete)


class AccountEditor:
    """Create or edit an account. Credit card opening balances are entered as the amount owed."""

    def __init__(self, ctx: AppContext, account: Account | None) -> None:
        self.ctx = ctx
        self.account = account
        self.kind = account.kind if account else AccountKind.CHECKING
        self.color = account.color if account else PALETTE[5]

        self.name = ft.TextField(label="Name", value=account.name if account else "", max_length=40, autofocus=True, on_submit=self._save)
        self.kind_dropdown = ft.Dropdown(
            label="Type",
            value=self.kind.value,
            options=[ft.DropdownOption(key=k.value, text=k.label) for k in AccountKind],
            on_select=self._kind_changed,
        )
        self.liquid = account.liquid if account is not None else self.kind.default_liquid
        self.liquid_switch = ft.Switch(
            label="Liquid",
            value=bool(self.liquid),
            on_change=self._liquid_changed,
        )
        self.liquid_help = ft.Text(
            "Count this account as liquid in the cash-flow forecast and reports.",
            size=fs(12),
            color=ft.Colors.ON_SURFACE_VARIANT,
        )
        self.liquid_block = ft.Column(
            [self.liquid_switch, self.liquid_help], spacing=0
        )
        self.number_last4 = ft.TextField(
            label="Last 4 digits (optional)",
            value=(account.number_last4 or "") if account else "",
            max_length=4,
            keyboard_type=ft.KeyboardType.NUMBER,
            on_submit=self._save,
        )
        opening = account.opening_balance_cents if account else 0
        if self.kind.is_liability:
            opening = -opening
        self.opening = ft.TextField(
            prefix_icon=ft.Icons.ATTACH_MONEY,
            value=f"{opening / 100:.2f}" if opening else "",
            keyboard_type=ft.KeyboardType.NUMBER,
            on_submit=self._save,
        )
        self.preview = account_avatar(Account(name="", kind=self.kind, color=self.color), size=fs(56))
        self.color_grid = ft.Row(wrap=True, spacing=8, run_spacing=8)
        self._render()

        self.dialog = ft.AlertDialog(
            modal=True,
            title=ft.Text("Edit Account" if account else "New Account"),
            content=ft.Container(
                width=460,
                content=ft.Column(
                    [
                        ft.Row([self.preview, ft.Column([self.name], expand=True)], spacing=GAP, vertical_alignment=ft.CrossAxisAlignment.START),
                        self.kind_dropdown,
                        self.liquid_block,
                        self.number_last4,
                        self.opening,
                        ft.Text("Color", weight=ft.FontWeight.W_600),
                        self.color_grid,
                    ],
                    spacing=12,
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

    def _render(self) -> None:
        self.liquid_block.visible = not self.kind.is_liability
        self.liquid_switch.value = bool(self.liquid)
        if self.kind.is_liability:
            self.opening.label = "Amount currently owed"
            self.opening.hint_text = "Balance before your first tracked transaction"
        elif self.kind.is_real_estate:
            self.opening.label = "Market value"
            self.opening.hint_text = "Current market value of the property"
        else:
            self.opening.label = "Opening balance"
            self.opening.hint_text = "Balance before your first tracked transaction"
        self.color_grid.controls = [
            ft.Container(
                width=30,
                height=30,
                border_radius=15,
                bgcolor=c,
                border=ft.Border.all(3, ft.Colors.ON_SURFACE) if c == self.color else None,
                on_click=lambda _, c=c: self._select_color(c),
                tooltip=c,
            )
            for c in PALETTE
        ]
        avatar = account_avatar(Account(name="", kind=self.kind, color=self.color), size=fs(56))
        self.preview.content = avatar.content
        self.preview.bgcolor = avatar.bgcolor

    def _select_color(self, color: str) -> None:
        self.color = color
        self._render()
        self.dialog.update()

    def _liquid_changed(self, e: ft.Event[ft.Switch]) -> None:
        self.liquid = bool(e.control.value)

    def _kind_changed(self, e: ft.Event[ft.Dropdown]) -> None:
        self.kind = AccountKind(e.control.value)
        self.liquid = self.kind.default_liquid
        self._render()

    def _save(self, _: ft.Event) -> None:
        name = (self.name.value or "").strip()
        last4 = (self.number_last4.value or "").strip()
        valid = True
        self.name.error = None
        self.opening.error = None
        self.number_last4.error = None
        if not name:
            self.name.error = "Name is required"
            valid = False
        if last4 and not (last4.isascii() and last4.isdigit() and len(last4) <= 4):
            self.number_last4.error = "Digits only"
            valid = False
        try:
            opening = parse_signed_money(self.opening.value)
        except ValueError as exc:
            self.opening.error = str(exc)
            valid = False
        if not valid:
            self.dialog.update()
            return
        if self.kind.is_liability:
            opening = -opening
        account = Account(
            id=self.account.id if self.account else None,
            name=name,
            kind=self.kind,
            opening_balance_cents=opening,
            color=self.color,
            number_last4=last4 or None,
            liquid=self.liquid,
        )
        try:
            if account.id:
                self.ctx.repo.update_account(account)
            else:
                self.ctx.repo.add_account(account)
        except RepositoryError as exc:
            self.name.error = str(exc)
            self.dialog.update()
            return
        self.ctx.page.pop_dialog()
        self.ctx.notify(f"Account '{name}' saved")
        self.ctx.data_changed()
