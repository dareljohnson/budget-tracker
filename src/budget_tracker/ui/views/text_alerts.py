"""The Text Alerts dialog: phone, carrier and the email account used to send texts."""

from __future__ import annotations

import asyncio
import logging

import flet as ft

from ... import sms
from ..context import AppContext
from ..theme import EXPENSE_COLOR, GAP, INCOME_COLOR, fs

log = logging.getLogger(__name__)


class TextAlertsDialog:
    """Configure an email-to-SMS gateway and optionally text due-bill reminders."""

    def __init__(self, ctx: AppContext) -> None:
        self.ctx = ctx
        self.config = sms.load_config(ctx.repo)

        self.carrier = ft.Dropdown(
            label="Carrier",
            options=[ft.DropdownOption(key=c.key, text=c.label) for c in sms.CARRIERS],
            value=self.config.carrier if self.config.carrier in sms.CARRIERS_BY_KEY else None,
            on_select=self._carrier_changed,
            expand=True,
        )
        self.number = ft.TextField(
            label="Mobile number",
            hint_text="10 digits, e.g. 5551234567",
            value=self.config.number,
            keyboard_type=ft.KeyboardType.PHONE,
            on_change=self._number_changed,
            expand=True,
        )
        self.gateway_hint = ft.Text("", size=fs(12), color=ft.Colors.ON_SURFACE_VARIANT)
        self.notify = ft.Switch(
            label="Text me when payments are due",
            value=self.config.notify_due,
        )
        self.notify_help = ft.Text(
            "Sends at most one summary a day. It also runs from the scheduled reminder command.",
            size=fs(12),
            color=ft.Colors.ON_SURFACE_VARIANT,
        )
        self.notify_velocity = ft.Switch(
            label="Text me about PLOC draw and sweep reminders",
            value=self.config.notify_velocity,
        )
        self.notify_velocity_help = ft.Text(
            "Uses the same phone and email account. Sends at most one PLOC reminder a month.",
            size=fs(12),
            color=ft.Colors.ON_SURFACE_VARIANT,
        )

        self.host = ft.TextField(
            label="Email server",
            hint_text="smtp.gmail.com",
            value=self.config.host,
            autofocus=False,
        )
        self.port = ft.TextField(
            label="Port",
            value=str(self.config.port),
            keyboard_type=ft.KeyboardType.NUMBER,
            width=120,
            on_change=self._connection_changed,
        )
        self.security = ft.Dropdown(
            label="Security",
            options=[
                ft.DropdownOption(key=key, text=sms.SMTP_SECURITY_LABELS[key])
                for key in sms.SMTP_SECURITY
            ],
            value=self.config.security
            if self.config.security in sms.SMTP_SECURITY
            else "starttls",
            width=220,
            on_select=self._connection_changed,
        )
        self.connection_hint = ft.Text("", size=fs(12), color=ft.Colors.ON_SURFACE_VARIANT)
        self.username = ft.TextField(
            label="Email address",
            hint_text="you@example.com",
            value=self.config.username,
        )
        self.password = ft.TextField(
            label="Email password",
            hint_text="Use an app password when your provider offers one",
            value=self.config.password,
            password=True,
            can_reveal_password=True,
        )
        self.sender = ft.TextField(
            label="From address (optional)",
            hint_text="Defaults to your email address",
            value=self.config.sender,
        )
        self.status = ft.Text("", size=fs(13))
        self.test_button = ft.OutlinedButton(
            content="Send test text",
            icon=ft.Icons.SEND,
            on_click=self._send_test,
        )
        self.save_button = ft.FilledButton(
            content="Save",
            icon=ft.Icons.CHECK,
            on_click=self._save,
        )

        self.dialog = ft.AlertDialog(
            modal=True,
            title=ft.Text("Text alerts"),
            content=ft.Container(
                width=560,
                height=500,
                content=ft.Column(
                    [
                        ft.Text(
                            "Budget Tracker sends texts through your carrier's email-to-SMS "
                            "gateway, using an email account you already have.",
                            size=fs(13),
                            color=ft.Colors.ON_SURFACE_VARIANT,
                        ),
                        ft.Text("Your phone", size=fs(14), weight=ft.FontWeight.W_600),
                        ft.Row(
                            [self.carrier, self.number],
                            spacing=12,
                            vertical_alignment=ft.CrossAxisAlignment.START,
                        ),
                        self.gateway_hint,
                        self.notify,
                        self.notify_help,
                        self.notify_velocity,
                        self.notify_velocity_help,
                        ft.Divider(height=1, color=ft.Colors.OUTLINE_VARIANT),
                        ft.Text("Email account used to send", size=fs(14), weight=ft.FontWeight.W_600),
                        ft.Text(
                            "Use an app password, not your main email password. It is stored "
                            "locally in Budget Tracker's database.",
                            size=fs(12),
                            color=ft.Colors.ON_SURFACE_VARIANT,
                        ),
                        self.host,
                        ft.Row(
                            [self.port, self.security],
                            spacing=12,
                            vertical_alignment=ft.CrossAxisAlignment.START,
                        ),
                        self.connection_hint,
                        self.username,
                        self.password,
                        self.sender,
                        ft.Text(
                            "Carrier gateways are best-effort and may delay, split or block "
                            "messages. Send a test before relying on them.",
                            size=fs(12),
                            color=ft.Colors.ON_SURFACE_VARIANT,
                        ),
                        self.status,
                    ],
                    spacing=GAP,
                    tight=True,
                    horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
                    scroll=ft.ScrollMode.AUTO,
                ),
            ),
            actions=[
                ft.TextButton(content="Cancel", on_click=lambda _: self.ctx.page.pop_dialog()),
                self.test_button,
                self.save_button,
            ],
            actions_alignment=ft.MainAxisAlignment.END,
        )
        self._update_gateway_hint()
        self._update_connection_hint()

    def open(self) -> None:
        self.ctx.page.show_dialog(self.dialog)

    # ---------- field feedback ----------
    def _carrier_changed(self, _: ft.Event | None = None) -> None:
        self.carrier.error_text = None
        self._update_gateway_hint()

    def _number_changed(self, _: ft.Event | None = None) -> None:
        self.number.error = None
        self._update_gateway_hint()

    def _update_gateway_hint(self) -> None:
        if not self.carrier.value:
            self.gateway_hint.value = "Choose your carrier to see the gateway address."
            return
        carrier = sms.CARRIERS_BY_KEY.get(self.carrier.value)
        if carrier is None or carrier.gateway is None:
            self.gateway_hint.value = "Choose a supported carrier."
            return
        try:
            address = sms.gateway_address(self.carrier.value, self.number.value)
        except ValueError:
            self.gateway_hint.value = f"This carrier uses {carrier.gateway}."
        else:
            self.gateway_hint.value = f"Sends to {address}."

    def _port_number(self) -> int:
        try:
            return int((self.port.value or "").strip())
        except ValueError:
            return 0

    def _connection_changed(self, _: ft.Event | None = None) -> None:
        self.port.error = None
        self.security.error_text = None
        self._update_connection_hint()

    def _update_connection_hint(self) -> None:
        port = self._port_number()
        security = self.security.value
        if port == sms.TLS_PORT and security != "ssl":
            self.connection_hint.value = (
                "Port 465 uses SSL/TLS. Choose SSL/TLS, or use port 587 with STARTTLS."
            )
        elif port in sms.STARTTLS_PORTS and security == "ssl":
            self.connection_hint.value = (
                f"Port {port} uses STARTTLS. Choose STARTTLS, or use port 465 with SSL/TLS."
            )
        elif port == sms.TLS_PORT:
            self.connection_hint.value = "Port 465 uses SSL/TLS."
        elif port in sms.STARTTLS_PORTS:
            self.connection_hint.value = f"Port {port} usually uses STARTTLS."
        else:
            self.connection_hint.value = "Use port 465 with SSL/TLS, or port 587 with STARTTLS."

    def _build_config(self) -> sms.SmsConfig:
        port = self._port_number()
        try:
            number = sms.normalize_phone(self.number.value)
        except ValueError:
            # Keep the typed value so the field can explain what is wrong with it.
            number = self.number.value or ""
        return sms.SmsConfig(
            carrier=self.carrier.value or "",
            number=number,
            notify_due=bool(self.notify.value),
            notify_velocity=bool(self.notify_velocity.value),
            host=self.host.value or "",
            port=port,
            security=self.security.value or "starttls",
            username=self.username.value or "",
            password=self.password.value or "",
            sender=self.sender.value or "",
        )

    def _clear_errors(self) -> None:
        self.carrier.error_text = None
        self.number.error = None
        self.host.error = None
        self.port.error = None
        self.security.error_text = None
        self.username.error = None
        self.password.error = None

    def _show_errors(self, errors: dict[str, str]) -> None:
        self.carrier.error_text = errors.get("carrier")
        self.number.error = errors.get("number")
        self.host.error = errors.get("host")
        self.port.error = errors.get("port")
        self.security.error_text = errors.get("security")
        self.username.error = errors.get("username")
        self.password.error = errors.get("password")
        self._refresh_ui()

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

    # ---------- actions ----------
    def _save(self, _: ft.Event | None = None) -> None:
        self._clear_errors()
        config = self._build_config()
        errors = sms.config_errors(
            config, require_smtp=config.notify_due or config.notify_velocity
        )
        if errors:
            self._show_errors(errors)
            return
        try:
            sms.save_config(self.ctx.repo, config)
        except ValueError as exc:
            self.status.value = str(exc)
            self.status.color = EXPENSE_COLOR
            self._refresh_ui()
            return
        self.ctx.page.pop_dialog()
        self.ctx.notify("Text alert settings saved")
        if config.notify_due:
            # The next startup/date tick would handle this, but a due bill should not wait.
            self.ctx.page.run_task(self._send_due_now)

    async def _send_due_now(self) -> None:
        try:
            sent = await asyncio.to_thread(sms.send_due_sms_for_today)
        except sms.SmsError as exc:
            log.warning("Could not send a due-bill text: %s", exc)
            self.ctx.notify(f"Text not sent: {exc}", error=True)
            return
        except Exception:  # noqa: BLE001 - a background text must not break the app
            log.exception("Could not send a due-bill text")
            return
        if sent:
            self.ctx.notify("Due-bill text sent")

    async def _send_test(self, _: ft.Event | None = None) -> None:
        self._clear_errors()
        config = self._build_config()
        errors = sms.config_errors(config, require_smtp=True)
        if errors:
            self._show_errors(errors)
            return
        self.test_button.disabled = True
        self.status.value = "Sending…"
        self.status.color = ft.Colors.ON_SURFACE_VARIANT
        self._refresh_ui()
        try:
            await asyncio.to_thread(sms.send_sms, config, sms.TEST_MESSAGE)
        except sms.SmsError as exc:
            self.status.value = str(exc)
            self.status.color = EXPENSE_COLOR
        else:
            self.status.value = "Test text sent. Click Save to keep these settings."
            self.status.color = INCOME_COLOR
        finally:
            self.test_button.disabled = False
            self._refresh_ui()
