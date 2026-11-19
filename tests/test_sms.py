"""Text alerts: carrier gateways, phone handling, SMTP delivery and settings."""

from __future__ import annotations

import asyncio
import ssl
import unittest
from datetime import date
from types import SimpleNamespace
from unittest import mock

from budget_tracker import sms
from budget_tracker.db import connect
from budget_tracker.models import Bill, BillFrequency, DueBill, TransactionType
from budget_tracker.repository import Repository


def _bill(name: str = "Rent", cents: int = 120_000, days: int = 0) -> DueBill:
    return DueBill(
        Bill(name, cents, BillFrequency.MONTHLY, date(2026, 3, 10), account_id=1),
        days,
        cents,
    )


class _FakeSMTP:
    instances: list["_FakeSMTP"] = []
    fail = False

    def __init__(self, host, port, timeout=None, context=None):
        self.host = host
        self.port = port
        self.timeout = timeout
        self.context = context
        self.ehlo_calls = 0
        self.starttls_called = False
        self.credentials: tuple[str, str] | None = None
        self.message = None
        self.closed = False
        _FakeSMTP.instances.append(self)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.closed = True

    def ehlo(self):
        self.ehlo_calls += 1

    def starttls(self, context=None):
        self.starttls_called = True
        self.context = context

    def login(self, username, password):
        self.credentials = (username, password)

    def send_message(self, message):
        if _FakeSMTP.fail:
            raise sms.smtplib.SMTPException("relay refused")
        self.message = message


def _config(**overrides) -> sms.SmsConfig:
    values = dict(
        carrier="att",
        number="5551234567",
        notify_due=True,
        host="smtp.example.com",
        port=587,
        security="starttls",
        username="owner@example.com",
        password="app-password",
    )
    values.update(overrides)
    return sms.SmsConfig(**values)


class TestCarrierGateways(unittest.TestCase):
    def test_known_carriers_use_their_email_gateways(self):
        self.assertEqual(sms.gateway_address("att", "5551234567"), "5551234567@txt.att.net")
        self.assertEqual(sms.gateway_address("tmobile", "5551234567"), "5551234567@tmomail.net")
        self.assertEqual(sms.gateway_address("verizon", "5551234567"), "5551234567@vtext.com")
        self.assertEqual(sms.gateway_address("googlefi", "5551234567"), "5551234567@msg.fi.google.com")
        # Tracfone publishes only an MMS gateway; text delivery still uses it.
        self.assertEqual(sms.gateway_address("tracfone", "5551234567"), "5551234567@mmst5.tracfone.com")

    def test_every_listed_carrier_has_a_gateway_and_label(self):
        self.assertGreaterEqual(len(sms.CARRIERS), 18)
        for carrier in sms.CARRIERS:
            self.assertTrue(carrier.gateway)
            self.assertTrue(carrier.label)
            self.assertNotIn(carrier.key, {c.key for c in sms.CARRIERS if c is not carrier})

    def test_a_ten_digit_number_accepts_common_formatting(self):
        for value in ("5551234567", "(555) 123-4567", "+1 555-123-4567", "1 (555) 123 4567"):
            self.assertEqual(sms.normalize_phone(value), "5551234567")

    def test_an_incomplete_or_foreign_number_is_rejected(self):
        for value in ("", "123", "555-123-4567 ext 9", "+44 20 7946 0958", "2 555 123 4567", "５５５１２３４５６７"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                sms.normalize_phone(value)

    def test_an_unknown_carrier_is_rejected(self):
        with self.assertRaises(sms.SmsError):
            sms.gateway_address("nope", "5551234567")


class TestSmsConfig(unittest.TestCase):
    def setUp(self) -> None:
        self.repo = Repository(connect(":memory:"))

    def tearDown(self) -> None:
        self.repo.close()

    def test_settings_round_trip_through_the_database(self):
        sms.save_config(self.repo, _config(number="(555) 123-4567", sender="alerts@example.com"))

        loaded = sms.load_config(self.repo)

        self.assertEqual(loaded.carrier, "att")
        self.assertEqual(loaded.number, "5551234567")
        self.assertTrue(loaded.notify_due)
        self.assertEqual((loaded.host, loaded.port, loaded.security), ("smtp.example.com", 587, "starttls"))
        self.assertEqual((loaded.username, loaded.password), ("owner@example.com", "app-password"))
        self.assertEqual(loaded.sender, "alerts@example.com")

    def test_an_empty_config_is_allowed_until_a_phone_is_added(self):
        self.assertEqual(sms.config_errors(sms.SmsConfig()), {})
        sms.save_config(self.repo, sms.SmsConfig())
        self.assertEqual(sms.load_config(self.repo).number, "")

    def test_validation_names_the_field_that_needs_attention(self):
        self.assertEqual(sms.config_errors(_config(number="123"))["number"], "Enter a 10-digit phone number")
        self.assertEqual(sms.config_errors(_config(carrier=""))["carrier"], "Choose your mobile carrier")
        self.assertEqual(
            sms.config_errors(_config(host="", notify_due=True, port=0))["host"],
            "Enter the email server",
        )
        self.assertEqual(sms.config_errors(_config(port=0))["port"], "Enter a port between 1 and 65535")
        self.assertEqual(sms.config_errors(_config(username=""))["username"], "Enter the email address")
        self.assertEqual(sms.config_errors(_config(password=""))["password"], "Enter the email password")
        automatic = sms.config_errors(_config(carrier="", number="", notify_due=True))
        self.assertEqual(automatic["number"], "Enter your 10-digit mobile number")
        self.assertEqual(automatic["carrier"], "Choose your mobile carrier")

    def test_port_and_security_mismatches_are_explained_before_connecting(self):
        ssl_on_587 = sms.config_errors(_config(port=587, security="ssl"))["security"]
        self.assertIn("Port 587 uses STARTTLS", ssl_on_587)
        self.assertIn("port 465", ssl_on_587)

        starttls_on_465 = sms.config_errors(_config(port=465, security="starttls"))["security"]
        self.assertIn("Port 465 uses SSL/TLS", starttls_on_465)

        unencrypted_on_465 = sms.config_errors(_config(port=465, security="none"))["security"]
        self.assertEqual(unencrypted_on_465, "Port 465 requires SSL/TLS")

    def test_saving_an_invalid_config_raises(self):
        with self.assertRaises(ValueError):
            sms.save_config(self.repo, _config(number="555"))

    def test_configured_requires_a_phone_gateway_server_and_login(self):
        self.assertTrue(_config().configured)
        self.assertFalse(_config(number="").configured)
        self.assertFalse(_config(host="").configured)
        self.assertFalse(_config(carrier="unknown").configured)
        self.assertFalse(_config(username="").configured)
        self.assertFalse(_config(password="").configured)


class TestSending(unittest.TestCase):
    def setUp(self) -> None:
        _FakeSMTP.instances.clear()
        _FakeSMTP.fail = False
        patcher = mock.patch.object(sms.smtplib, "SMTP", _FakeSMTP)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_starttls_login_and_gateway_address(self):
        sms.send_sms(_config(), "Hello from Budget Tracker")

        [connection] = _FakeSMTP.instances
        self.assertEqual((connection.host, connection.port, connection.timeout), ("smtp.example.com", 587, 20))
        self.assertTrue(connection.starttls_called)
        self.assertEqual(connection.credentials, ("owner@example.com", "app-password"))
        self.assertEqual(connection.message["To"], "5551234567@txt.att.net")
        self.assertEqual(connection.message["From"], "owner@example.com")
        self.assertEqual(connection.message["Subject"], sms.APP_NAME)
        self.assertEqual(connection.message.get_content().strip(), "Hello from Budget Tracker")
        self.assertTrue(connection.closed)

    def test_ssl_uses_smtp_ssl_without_starttls(self):
        with mock.patch.object(sms.smtplib, "SMTP_SSL", _FakeSMTP):
            sms.send_sms(_config(security="ssl", port=465), "via SSL")

        [connection] = _FakeSMTP.instances
        self.assertFalse(connection.starttls_called)
        self.assertEqual(connection.credentials, ("owner@example.com", "app-password"))

    def test_no_security_skips_starttls(self):
        sms.send_sms(_config(security="none"), "local relay")

        [connection] = _FakeSMTP.instances
        self.assertFalse(connection.starttls_called)

    def test_a_relay_failure_becomes_a_readable_sms_error(self):
        _FakeSMTP.fail = True
        with self.assertRaisesRegex(sms.SmsError, "relay refused"):
            sms.send_sms(_config(), "will fail")

    def test_an_ssl_version_error_points_at_the_security_setting(self):
        message = sms._delivery_error(
            _config(port=587, security="ssl"),
            ssl.SSLError("[SSL: WRONG_VERSION_NUMBER] wrong version number"),        )
        self.assertIn("Port 587 and port 25 usually use STARTTLS", message)
        self.assertIn("port 465 uses SSL/TLS", message)

    def test_an_authentication_error_points_at_app_passwords(self):
        message = sms._delivery_error(
            _config(),
            sms.smtplib.SMTPAuthenticationError(535, b"bad credentials"),
        )
        self.assertIn("rejected the sign-in", message)
        self.assertIn("app password", message)

    def test_a_rejected_recipient_points_at_the_gateway_address(self):
        message = sms._delivery_error(
            _config(),
            sms.smtplib.SMTPRecipientsRefused({"5551234567@txt.att.net": (550, b"unknown")}),
        )
        self.assertIn("rejected the message address", message)
        self.assertIn("mobile number", message)

    def test_unconfigured_settings_are_refused_before_connecting(self):
        with self.assertRaisesRegex(sms.SmsError, "not configured"):
            sms.send_sms(sms.SmsConfig(), "nothing")
        self.assertEqual(_FakeSMTP.instances, [])

    def test_due_message_is_short_and_lists_the_first_bills(self):
        due = [_bill("Rent", 120_000, 0), _bill("Internet", 6_000, 2), _bill("Gym", 4_000, -1), _bill("Water", 5_000, 1)]
        message = sms.due_message(due)
        self.assertTrue(message.startswith(f"{sms.APP_NAME}: 1 overdue, 3 due soon."))
        self.assertIn("Rent $1,200.00 (due today)", message)
        self.assertIn("Internet $60.00 (due in 2 days)", message)
        self.assertIn("Gym $40.00 (overdue by 1 day)", message)
        self.assertIn("+1 more", message)
        self.assertNotIn("Water", message)

    def test_due_message_handles_a_single_payment_and_a_full_balance(self):
        self.assertEqual(
            sms.due_message([_bill("Card", 0, 3)]),
            f"{sms.APP_NAME}: 1 payment due soon. Card amount TBD (due in 3 days)",
        )
        self.assertEqual(sms.due_message([]), "")


class TestDueSms(unittest.TestCase):
    def setUp(self) -> None:
        self.repo = Repository(connect(":memory:"))
        self.sent: list[tuple[sms.SmsConfig, str]] = []
        sms.save_config(self.repo, _config())

    def tearDown(self) -> None:
        self.repo.close()

    def _send(self, due, *, today=None) -> bool:
        return sms.send_due_sms(
            self.repo,
            due,
            today=today,
            sender=lambda config, body: self.sent.append((config, body)),
        )

    def test_sends_once_per_day_when_enabled_and_due(self):
        day = date(2026, 3, 10)
        self.assertTrue(self._send([_bill()], today=day))
        self.assertEqual(len(self.sent), 1)
        self.assertIn("Rent $1,200.00", self.sent[0][1])
        self.assertFalse(self._send([_bill()], today=day))
        self.assertEqual(len(self.sent), 1)
        self.assertTrue(self._send([_bill()], today=date(2026, 3, 11)))
        self.assertEqual(len(self.sent), 2)

    def test_nothing_is_sent_when_there_are_no_due_bills(self):
        self.assertFalse(self._send([], today=date(2026, 3, 10)))
        self.assertEqual(self.sent, [])

    def test_the_switch_off_prevents_automatic_texts(self):
        sms.save_config(self.repo, _config(notify_due=False))
        self.assertFalse(self._send([_bill()], today=date(2026, 3, 10)))
        self.assertEqual(self.sent, [])

    def test_an_incomplete_config_prevents_automatic_texts(self):
        sms.save_config(self.repo, _config(carrier="", number="", notify_due=False))
        self.repo.set_setting(sms.NOTIFY_DUE_SETTING, "1")
        self.assertFalse(self._send([_bill()], today=date(2026, 3, 10)))
        self.assertEqual(self.sent, [])

    def test_a_failed_send_does_not_consume_the_day(self):
        day = date(2026, 3, 10)

        def fail(config, body):
            raise sms.SmsError("offline")

        with self.assertRaises(sms.SmsError):
            sms.send_due_sms(self.repo, [_bill()], today=day, sender=fail)
        self.assertIsNone(self.repo.get_setting(sms.LAST_DUE_SENT_SETTING))
        self.assertTrue(self._send([_bill()], today=day))
        self.assertEqual(len(self.sent), 1)


class TestReminderCommand(unittest.TestCase):
    def setUp(self) -> None:
        from budget_tracker import reminders

        self.reminders = reminders
        self.repo = Repository(connect(":memory:"))
        category = self.repo.list_categories(TransactionType.EXPENSE)[0]
        account = self.repo.list_accounts()[0]
        self.repo.add_bill(
            Bill(
                "Rent",
                120_000,
                BillFrequency.MONTHLY,
                date.today(),
                account.id,
                category_id=category.id,
            )
        )
        sms.save_config(self.repo, _config())

    def tearDown(self) -> None:
        self.repo.close()

    def test_the_scheduled_command_sends_the_sms_alongside_the_toast(self):
        with mock.patch.object(self.reminders, "connect", return_value=self.repo.conn), \
                mock.patch.object(self.reminders, "show_windows_toast"), \
                mock.patch.object(self.reminders, "send_due_sms", return_value=True) as send:
            result = self.reminders.main([])

        self.assertEqual(result, 0)
        send.assert_called_once()
        self.assertTrue(send.call_args.args[1])

    def test_a_toast_failure_does_not_block_the_text(self):
        with mock.patch.object(self.reminders, "connect", return_value=self.repo.conn), \
                mock.patch.object(self.reminders, "show_windows_toast", side_effect=OSError("no toast host")), \
                mock.patch.object(self.reminders, "send_due_sms", return_value=True) as send:
            result = self.reminders.main([])

        self.assertEqual(result, 0)
        send.assert_called_once()

    def test_print_mode_does_not_send_a_text(self):
        with mock.patch.object(self.reminders, "connect", return_value=self.repo.conn), \
                mock.patch.object(self.reminders, "send_due_sms") as send, \
                mock.patch("builtins.print"):
            result = self.reminders.main(["--print"])

        self.assertEqual(result, 0)
        send.assert_not_called()


class _PageStub:
    def __init__(self) -> None:
        self.overlay: list = []
        self.run_task = mock.Mock(return_value=mock.Mock())

    def show_dialog(self, control) -> None:
        if hasattr(control, "open"):
            control.open = True
        self.overlay.append(control)

    def pop_dialog(self) -> None:
        for control in reversed(self.overlay):
            if hasattr(control, "open") and hasattr(control, "actions"):
                control.open = False
                self.overlay.remove(control)
                return control
        return None

    def update(self, *_) -> None:
        pass

    @property
    def open_dialogs(self) -> list:
        import flet as ft

        return [c for c in self.overlay if isinstance(c, ft.AlertDialog)]


@unittest.skipIf(__import__("importlib").util.find_spec("flet") is None, "flet is not installed")
class TestTextAlertsDialog(unittest.TestCase):
    def setUp(self) -> None:
        from budget_tracker.services import BudgetService
        from budget_tracker.ui.context import AppContext

        self.repo = Repository(connect(":memory:"))
        self.page = _PageStub()
        self.ctx = AppContext(page=self.page, repo=self.repo, service=BudgetService(self.repo))
        self.addCleanup(self.repo.close)

    def _dialog(self):
        from budget_tracker.ui.views.text_alerts import TextAlertsDialog

        return TextAlertsDialog(self.ctx)

    def _fill(self, dialog):
        dialog.carrier.value = "att"
        dialog.number.value = "(555) 123-4567"
        dialog.notify.value = True
        dialog.host.value = "smtp.example.com"
        dialog.port.value = "587"
        dialog.security.value = "starttls"
        dialog.username.value = "owner@example.com"
        dialog.password.value = "app-password"

    def test_carrier_list_and_gateway_hint(self):
        dialog = self._dialog()
        self.assertEqual(len(dialog.carrier.options), len(sms.CARRIERS))
        dialog.carrier.value = "att"
        dialog.number.value = "5551234567"
        dialog._update_gateway_hint()
        self.assertIn("5551234567@txt.att.net", dialog.gateway_hint.value)

    def test_save_persists_the_config_and_closes(self):
        dialog = self._dialog()
        dialog.open()
        self._fill(dialog)

        dialog._save(None)

        self.assertEqual(self.page.open_dialogs, [])
        loaded = sms.load_config(self.repo)
        self.assertEqual((loaded.carrier, loaded.number, loaded.notify_due), ("att", "5551234567", True))
        self.assertEqual(loaded.username, "owner@example.com")
        self.assertTrue(any("saved" in bar.content.value.lower() for bar in self.page.overlay if hasattr(bar, "content")))

    def test_invalid_phone_and_missing_server_are_reported_on_their_fields(self):
        dialog = self._dialog()
        dialog.open()
        self._fill(dialog)
        dialog.number.value = "123"
        dialog.host.value = ""

        dialog._save(None)

        self.assertEqual(dialog.number.error, "Enter a 10-digit phone number")
        self.assertEqual(dialog.host.error, "Enter the email server")
        self.assertEqual(len(self.page.open_dialogs), 1)

    def test_an_ssl_port_mismatch_is_explained_before_a_test_send(self):
        dialog = self._dialog()
        self._fill(dialog)
        dialog.port.value = "587"
        dialog.security.value = "ssl"
        dialog._update_connection_hint()
        self.assertIn("Port 587 uses STARTTLS", dialog.connection_hint.value)

        with mock.patch.object(sms, "send_sms") as send:
            asyncio.run(dialog._send_test(None))

        send.assert_not_called()
        self.assertIn("Port 587 uses STARTTLS", dialog.security.error_text)

    def test_send_test_reports_success_and_uses_the_test_message(self):
        dialog = self._dialog()
        self._fill(dialog)

        with mock.patch.object(sms, "send_sms") as send:
            asyncio.run(dialog._send_test(None))

        send.assert_called_once()
        config, body = send.call_args.args
        self.assertEqual((config.carrier, config.number), ("att", "5551234567"))
        self.assertEqual(body, sms.TEST_MESSAGE)
        self.assertIn("Test text sent", dialog.status.value)
        self.assertFalse(dialog.test_button.disabled)

    def test_send_test_reports_a_delivery_error_without_saving(self):
        dialog = self._dialog()
        self._fill(dialog)

        with mock.patch.object(sms, "send_sms", side_effect=sms.SmsError("relay refused")):
            asyncio.run(dialog._send_test(None))

        self.assertEqual(dialog.status.value, "relay refused")
        self.assertFalse(dialog.test_button.disabled)
        self.assertEqual(self.repo.get_setting(sms.CARRIER_SETTING), None)


if __name__ == "__main__":
    unittest.main()
