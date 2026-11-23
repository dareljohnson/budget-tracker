"""JMD → USD converter dialog with a live rate, manual override and USD result."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from datetime import datetime, timezone

import flet as ft

from ... import currency
from ...formatting import format_money, parse_money
from ..context import AppContext
from ..theme import EXPENSE_COLOR, GAP, INCOME_COLOR, SEED_COLOR, WARNING_COLOR, fs

log = logging.getLogger(__name__)


class CurrencyConverterDialog:
    """Convert JMD to USD and optionally hand the USD cents to a transaction editor."""

    def __init__(
        self,
        ctx: AppContext,
        *,
        on_use: Callable[[int], None] | None = None,
    ) -> None:
        self.ctx = ctx
        self.on_use = on_use
        self._fetching = False
        self._converted: tuple[int, int, float] | None = None
        self.clipboard = ft.Clipboard()
        self._cached = currency.load_cached_rate(ctx.repo)
        self._rate_source = self._cached.source if self._cached else currency.RATE_PROVIDER

        self.rate = ft.TextField(
            label="JMD per USD",
            hint_text="e.g. 158.38",
            value=f"{self._cached.rate:.4f}" if self._cached else "",
            keyboard_type=ft.KeyboardType.NUMBER,
            width=190,
            on_change=self._recalculate,
        )
        self.refresh_button = ft.IconButton(
            ft.Icons.REFRESH,
            tooltip="Fetch the current JMD/USD rate",
            on_click=self._refresh,
        )
        self.status = ft.Text("", size=fs(12), color=ft.Colors.ON_SURFACE_VARIANT)
        self.jmd = ft.TextField(
            label="Amount in JMD",
            hint_text="e.g. 10000",
            prefix_icon=ft.Icons.ATTACH_MONEY,
            keyboard_type=ft.KeyboardType.NUMBER,
            autofocus=True,
            on_change=self._recalculate,
        )
        self.usd = ft.Text("$0.00", size=fs(26), weight=ft.FontWeight.BOLD, color=SEED_COLOR)
        self.detail = ft.Text(
            "Enter a JMD amount and a rate.",
            size=fs(13),
            color=ft.Colors.ON_SURFACE_VARIANT,
        )
        self.use_button = ft.FilledButton(
            content="Use this amount",
            icon=ft.Icons.CHECK,
            disabled=True,
            on_click=self._use,
            visible=on_use is not None,
        )
        self.copy_button = ft.TextButton(
            content="Copy USD",
            icon=ft.Icons.CONTENT_COPY,
            disabled=True,
            on_click=self._copy,
        )

        actions: list[ft.Control] = [
            ft.TextButton(content="Close", on_click=lambda _: self._close()),
            self.copy_button,
            self.use_button,
        ]
        self.dialog = ft.AlertDialog(
            modal=True,
            title=ft.Text("JMD → USD"),
            content=ft.Container(
                width=480,
                content=ft.Column(
                    [
                        ft.Text(
                            "Convert Jamaican dollars to US dollars at the current daily rate. "
                            "The last rate is cached for 12 hours.",
                            size=fs(13),
                            color=ft.Colors.ON_SURFACE_VARIANT,
                        ),
                        ft.Row(
                            [self.rate, self.refresh_button],
                            spacing=8,
                            vertical_alignment=ft.CrossAxisAlignment.CENTER,
                        ),
                        self.status,
                        ft.Divider(height=1, color=ft.Colors.OUTLINE_VARIANT),
                        self.jmd,
                        ft.Container(
                            content=ft.Column([self.usd, self.detail], spacing=4),
                            padding=14,
                            border_radius=12,
                            bgcolor=ft.Colors.with_opacity(0.06, SEED_COLOR),
                        ),
                        ft.Text(
                            "Rate by exchangerate-api.com · the request contains only the USD base currency.",
                            size=fs(11),
                            color=ft.Colors.ON_SURFACE_VARIANT,
                        ),
                    ],
                    spacing=GAP,
                    tight=True,
                    horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
                ),
            ),
            actions=actions,
            actions_alignment=ft.MainAxisAlignment.END,
        )
        self._set_initial_status()
        self._recalculate()

    def open(self) -> None:
        self.ctx.page.show_dialog(self.dialog)
        if self._cached is None or not currency.is_fresh(self._cached):
            self.ctx.page.run_task(self._refresh)

    # ---------- rate ----------
    def _set_initial_status(self) -> None:
        if self._cached is None:
            self.status.value = "Fetching the current rate…"
            return
        label = f"{self._cached.as_of_label} · {self._cached.source}"
        if currency.is_fresh(self._cached):
            self.status.value = f"Saved rate · {label}"
        else:
            self.status.value = f"Saved rate from {label}; refreshing…"

    async def _refresh(self, _: ft.Event | None = None) -> None:
        if self._fetching:
            return
        self._fetching = True
        self.refresh_button.disabled = True
        self.status.value = "Fetching the current rate…"
        self.status.color = ft.Colors.ON_SURFACE_VARIANT
        self._refresh_ui()
        try:
            fetched = await asyncio.to_thread(currency.fetch_jmd_per_usd)
        except currency.RateError as exc:
            if self._cached is not None:
                self.status.value = (
                    f"Could not refresh ({exc}). Using the saved rate from "
                    f"{self._cached.as_of_label}."
                )
            else:
                self.status.value = f"Could not fetch a rate ({exc}). Enter one manually."
            self.status.color = WARNING_COLOR
        else:
            currency.save_rate(self.ctx.repo, fetched)
            self._cached = fetched
            self._rate_source = fetched.source
            self.rate.value = f"{fetched.rate:.4f}"
            self.status.value = f"Live rate · {fetched.as_of_label} · {fetched.source}"
            self.status.color = INCOME_COLOR
            self._recalculate()
        finally:
            self._fetching = False
            self.refresh_button.disabled = False
            self._refresh_ui()

    def _current_rate(self) -> float | None:
        try:
            return currency.parse_rate(self.rate.value)
        except ValueError:
            return None

    def _current_jmd_cents(self) -> int | None:
        cleaned = (self.jmd.value or "").strip().replace("J$", "").strip()
        if not cleaned:
            return None
        try:
            return parse_money(cleaned)
        except ValueError:
            return None

    def _recalculate(self, _: ft.Event | None = None) -> None:
        rate = self._current_rate()
        jmd_cents = self._current_jmd_cents()
        if rate is None:
            self._converted = None
            self.usd.value = "$0.00"
            self.detail.value = "Enter a valid JMD-per-USD rate."
            self.detail.color = EXPENSE_COLOR
            self.use_button.disabled = True
            self.copy_button.disabled = True
            self._refresh_ui()
            return
        if jmd_cents is None:
            self._converted = None
            self.usd.value = "$0.00"
            self.detail.value = "Enter a JMD amount."
            self.detail.color = ft.Colors.ON_SURFACE_VARIANT
            self.use_button.disabled = True
            self.copy_button.disabled = True
            self._refresh_ui()
            return

        usd_cents = currency.to_usd_cents(jmd_cents, rate)
        self._converted = (usd_cents, jmd_cents, rate)
        self.usd.value = format_money(usd_cents)
        self.detail.value = (
            f"{currency.format_jmd(jmd_cents)} ÷ {rate:,.4f} JMD/USD "
            f"= {format_money(usd_cents)} USD"
        )
        self.detail.color = ft.Colors.ON_SURFACE_VARIANT
        self.use_button.disabled = False
        self.copy_button.disabled = False
        self.use_button.content = f"Use {format_money(usd_cents)}"
        self._refresh_ui()

    # ---------- actions ----------
    def _remember_rate(self, rate: float) -> None:
        """Keep a manually entered rate as the offline fallback without refreshing it."""
        if self._cached is not None and abs(rate - self._cached.rate) < 1e-9:
            return
        currency.save_rate(
            self.ctx.repo,
            currency.ExchangeRate(rate, datetime.now(timezone.utc), "manual"),
        )

    def _use(self, _: ft.Event | None = None) -> None:
        if self._converted is None or self.on_use is None:
            return
        usd_cents, jmd_cents, rate = self._converted
        self._remember_rate(rate)
        self._close()
        self.on_use(usd_cents)
        self.ctx.notify(
            f"Converted {currency.format_jmd(jmd_cents)} to {format_money(usd_cents)}"
        )

    def _close(self) -> None:
        """Close this dialog even when a SnackBar or another dialog is stacked above it."""
        while self.dialog.open:
            if self.ctx.page.pop_dialog() is None:
                break

    async def _copy(self, _: ft.Event | None = None) -> None:
        if self._converted is None:
            return
        usd_cents, _, rate = self._converted
        self._remember_rate(rate)
        await self.clipboard.set(f"{usd_cents / 100:.2f}")
        self.ctx.notify(f"Copied {format_money(usd_cents)}")

    # ---------- mounted helpers ----------
    def _is_mounted(self) -> bool:
        control: ft.Control | None = self.dialog
        while control is not None:
            if isinstance(control, ft.Page):
                return True
            control = control.parent
        return False

    def _refresh_ui(self) -> None:
        if self._is_mounted():
            self.dialog.update()
