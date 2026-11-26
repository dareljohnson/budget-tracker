"""Send text messages through a carrier's email-to-SMS gateway.

Carriers expose a phone number as an email address (for example
``5551234567@txt.att.net``). Budget Tracker sends to that address through an SMTP
account the owner configures. This module holds the carrier list, settings
persistence, phone-number handling and the SMTP delivery itself, so the UI and
the background reminder command share one implementation.
"""

from __future__ import annotations

import re
import smtplib
import ssl
from dataclasses import dataclass
from datetime import date
from email.message import EmailMessage
from typing import TYPE_CHECKING

from .config import APP_NAME
from .formatting import format_money
from .models import DueBill

if TYPE_CHECKING:
    from .repository import Repository


class SmsError(Exception):
    """Raised when a text message cannot be built or sent."""


@dataclass(frozen=True, slots=True)
class Carrier:
    """A carrier's email-to-SMS gateway addresses.

    ``sms_gateway`` is preferred because a plain text message is all this app
    sends. Carriers that publish only an MMS gateway fall back to it.
    """

    key: str
    label: str
    sms_gateway: str | None = None
    mms_gateway: str | None = None

    @property
    def gateway(self) -> str | None:
        return self.sms_gateway or self.mms_gateway


CARRIERS: tuple[Carrier, ...] = (
    Carrier("att", "AT&T", "txt.att.net", "mms.att.net"),
    Carrier("tmobile", "T-Mobile", "tmomail.net", "tmomail.net"),
    Carrier("verizon", "Verizon", "vtext.com", "vzwpix.com"),
    Carrier("sprint", "Sprint", "messaging.sprintpcs.com", "pm.sprint.com"),
    Carrier("xfinity", "Xfinity Mobile", "vtext.com", "mypixmessages.com"),
    Carrier("virgin", "Virgin Mobile", "vmobl.com", "vmpix.com"),
    Carrier("tracfone", "Tracfone", None, "mmst5.tracfone.com"),
    Carrier("metro", "Metro PCS", "mymetropcs.com", "mymetropcs.com"),
    Carrier("boost", "Boost Mobile", "sms.myboostmobile.com", "myboostmobile.com"),
    Carrier("cricket", "Cricket", "sms.cricketwireless.net", "mms.cricketwireless.net"),
    Carrier("republic", "Republic Wireless", "text.republicwireless.com"),
    Carrier("straighttalk", "StraightTalk", "vtext.com", "mypixmessages.com"),
    Carrier("googlefi", "Google Fi (Project Fi)", "msg.fi.google.com", "msg.fi.google.com"),
    Carrier("uscellular", "U.S. Cellular", "email.uscc.net", "mms.uscc.net"),
    Carrier("ting", "Ting", "message.ting.com"),
    Carrier("consumer", "Consumer Cellular", "mailmymobile.net"),
    Carrier("cspire", "C-Spire", "cspire1.com"),
    Carrier("pageplus", "Page Plus", "vtext.com"),
)
CARRIERS_BY_KEY: dict[str, Carrier] = {carrier.key: carrier for carrier in CARRIERS}

CARRIER_SETTING = "sms_carrier"
NUMBER_SETTING = "sms_number"
NOTIFY_DUE_SETTING = "sms_notify_due"
NOTIFY_VELOCITY_SETTING = "sms_notify_velocity"
LAST_DUE_SENT_SETTING = "sms_last_due_sent"
LAST_VELOCITY_SMS_SETTING = "sms_last_velocity_sent"
SMTP_HOST_SETTING = "sms_smtp_host"
SMTP_PORT_SETTING = "sms_smtp_port"
SMTP_SECURITY_SETTING = "sms_smtp_security"
SMTP_USERNAME_SETTING = "sms_smtp_username"
SMTP_PASSWORD_SETTING = "sms_smtp_password"
SMTP_SENDER_SETTING = "sms_smtp_sender"

DEFAULT_PORT = 587
SMTP_SECURITY = ("starttls", "ssl", "none")
SMTP_SECURITY_LABELS = {
    "starttls": "STARTTLS (port 587)",
    "ssl": "SSL/TLS (port 465)",
    "none": "None (not recommended)",
}
TLS_PORT = 465
STARTTLS_PORTS = (25, 587)
TEST_MESSAGE = f"{APP_NAME}: test message. Text alerts are working."


def normalize_phone(number: str) -> str:
    """Return a 10-digit US number, accepting common punctuation and a leading 1."""
    digits = re.sub(r"\D", "", number or "")
    if not (digits.isascii() and digits.isdigit()):
        raise ValueError("Enter a 10-digit phone number")
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    if len(digits) != 10:
        raise ValueError("Enter a 10-digit phone number")
    return digits


def gateway_address(carrier_key: str, number: str) -> str:
    """Build the carrier's email address for a phone number."""
    carrier = CARRIERS_BY_KEY.get(carrier_key)
    if carrier is None or carrier.gateway is None:
        raise SmsError("Choose the mobile carrier")
    return f"{normalize_phone(number)}@{carrier.gateway}"


@dataclass(slots=True)
class SmsConfig:
    """Everything needed to address and send a text message."""

    carrier: str = ""
    number: str = ""
    notify_due: bool = False
    notify_velocity: bool = False
    host: str = ""
    port: int = DEFAULT_PORT
    security: str = "starttls"
    username: str = ""
    password: str = ""
    sender: str = ""

    @property
    def gateway(self) -> str | None:
        carrier = CARRIERS_BY_KEY.get(self.carrier)
        return carrier.gateway if carrier else None

    @property
    def from_address(self) -> str:
        return self.sender.strip() or self.username.strip() or "budget-tracker@localhost"

    @property
    def configured(self) -> bool:
        """Whether a text alert can be sent with the saved values."""
        try:
            normalize_phone(self.number)
        except ValueError:
            return False
        return bool(
            self.gateway
            and self.host.strip()
            and 1 <= self.port <= 65535
            and self.username.strip()
            and self.password
        )


def load_config(repo: Repository) -> SmsConfig:
    """Read text-alert settings from the app database."""
    return SmsConfig(
        carrier=repo.get_setting(CARRIER_SETTING, "") or "",
        number=repo.get_setting(NUMBER_SETTING, "") or "",
        notify_due=repo.get_setting(NOTIFY_DUE_SETTING, "0") == "1",
        notify_velocity=repo.get_setting(NOTIFY_VELOCITY_SETTING, "0") == "1",
        host=repo.get_setting(SMTP_HOST_SETTING, "") or "",
        port=_port_setting(repo.get_setting(SMTP_PORT_SETTING)),
        security=repo.get_setting(SMTP_SECURITY_SETTING, "starttls") or "starttls",
        username=repo.get_setting(SMTP_USERNAME_SETTING, "") or "",
        password=repo.get_setting(SMTP_PASSWORD_SETTING, "") or "",
        sender=repo.get_setting(SMTP_SENDER_SETTING, "") or "",
    )


def save_config(repo: Repository, config: SmsConfig) -> None:
    """Validate and persist text-alert settings."""
    errors = config_errors(
        config, require_smtp=config.notify_due or config.notify_velocity
    )
    if errors:
        raise ValueError(next(iter(errors.values())))
    repo.set_setting(CARRIER_SETTING, config.carrier)
    repo.set_setting(
        NUMBER_SETTING, normalize_phone(config.number) if config.number.strip() else ""
    )
    repo.set_setting(NOTIFY_DUE_SETTING, "1" if config.notify_due else "0")
    repo.set_setting(
        NOTIFY_VELOCITY_SETTING, "1" if config.notify_velocity else "0"
    )
    repo.set_setting(SMTP_HOST_SETTING, config.host.strip())
    repo.set_setting(SMTP_PORT_SETTING, str(config.port))
    repo.set_setting(SMTP_SECURITY_SETTING, config.security)
    repo.set_setting(SMTP_USERNAME_SETTING, config.username.strip())
    repo.set_setting(SMTP_PASSWORD_SETTING, config.password)
    repo.set_setting(SMTP_SENDER_SETTING, config.sender.strip())


def config_errors(config: SmsConfig, *, require_smtp: bool = False) -> dict[str, str]:
    """Field-keyed validation errors, so a dialog can place each message.

    A phone number is optional when nothing is configured yet, but choosing a
    carrier without a number (or the reverse) is always an error. SMTP details
    are required for a test message and for automatic due-bill texts.
    """
    errors: dict[str, str] = {}
    number = config.number.strip()
    if number:
        try:
            normalize_phone(number)
        except ValueError as exc:
            errors["number"] = str(exc)
        if config.carrier not in CARRIERS_BY_KEY:
            errors["carrier"] = "Choose your mobile carrier"
    elif config.carrier:
        errors["number"] = "Enter your 10-digit mobile number"
    if config.notify_due or config.notify_velocity:
        if not number:
            errors.setdefault("number", "Enter your 10-digit mobile number")
        if config.carrier not in CARRIERS_BY_KEY:
            errors.setdefault("carrier", "Choose your mobile carrier")

    needs_smtp = require_smtp or config.notify_due or config.notify_velocity or bool(
        config.host.strip() or config.username.strip() or config.password
    )
    if needs_smtp:
        if not config.host.strip():
            errors["host"] = "Enter the email server"
        if not 1 <= config.port <= 65535:
            errors["port"] = "Enter a port between 1 and 65535"
        if config.security not in SMTP_SECURITY:
            errors["security"] = "Choose a security option"
        elif config.security == "ssl" and config.port in STARTTLS_PORTS:
            errors["security"] = (
                f"Port {config.port} uses STARTTLS; choose STARTTLS or use port 465 with SSL/TLS"
            )
        elif config.security == "starttls" and config.port == TLS_PORT:
            errors["security"] = "Port 465 uses SSL/TLS; choose SSL/TLS or use port 587 with STARTTLS"
        elif config.security == "none" and config.port == TLS_PORT:
            errors["security"] = "Port 465 requires SSL/TLS"
        if not config.username.strip():
            errors["username"] = "Enter the email address"
        elif not config.password:
            errors["password"] = "Enter the email password"
    return errors


def send_sms(config: SmsConfig, body: str, *, subject: str | None = None) -> None:
    """Send one text message. Raises :class:`SmsError` on any delivery failure."""
    if not config.configured:
        raise SmsError("Text alerts are not configured yet")
    address = gateway_address(config.carrier, config.number)
    message = EmailMessage()
    message["From"] = config.from_address
    message["To"] = address
    message["Subject"] = subject or APP_NAME
    message.set_content(body)

    try:
        if config.security == "ssl":
            with smtplib.SMTP_SSL(
                config.host.strip(),
                config.port,
                timeout=20,
                context=ssl.create_default_context(),
            ) as smtp:
                _deliver(smtp, config, message)
        elif config.security in ("starttls", "none"):
            with smtplib.SMTP(config.host.strip(), config.port, timeout=20) as smtp:
                smtp.ehlo()
                if config.security == "starttls":
                    smtp.starttls(context=ssl.create_default_context())
                    smtp.ehlo()
                _deliver(smtp, config, message)
        else:
            raise SmsError("Choose a valid email security option")
    except SmsError:
        raise
    except (OSError, smtplib.SMTPException) as exc:
        raise SmsError(_delivery_error(config, exc)) from exc


def _delivery_error(config: SmsConfig, exc: Exception) -> str:
    """Turn an SMTP/SSL failure into a message that points at the setting to change."""
    where = f"{config.host.strip()}:{config.port}"
    if isinstance(exc, ssl.SSLError):
        return (
            f"Could not establish a secure connection to {where} with "
            f"{SMTP_SECURITY_LABELS.get(config.security, config.security)}. "
            "Port 587 and port 25 usually use STARTTLS; port 465 uses SSL/TLS. "
            "Choose the security option that matches the port."
        )
    if isinstance(exc, smtplib.SMTPAuthenticationError):
        return (
            f"The email server rejected the sign-in for {config.username.strip()}. "
            "Use an app password if your provider requires one."
        )
    if isinstance(exc, (smtplib.SMTPRecipientsRefused, smtplib.SMTPSenderRefused)):
        return (
            "The email server rejected the message address. Check the mobile number, "
            "carrier and From address."
        )
    return f"Could not send the text: {exc}"


def _deliver(smtp: smtplib.SMTP, config: SmsConfig, message: EmailMessage) -> None:
    if config.username.strip():
        smtp.login(config.username.strip(), config.password)
    smtp.send_message(message)


def due_message(due: list[DueBill], *, limit: int = 3) -> str:
    """A compact message for the due-bill text.

    Carrier gateways are best-effort and often cap or split long messages, so
    keep the summary short and list only the first few bills.
    """
    if not due:
        return ""
    overdue = sum(1 for bill in due if bill.status == "overdue")
    count = len(due)
    noun = "payment" if count == 1 else "payments"
    if overdue and overdue == count:
        summary = f"{count} {noun} overdue"
    elif overdue:
        summary = f"{overdue} overdue, {count - overdue} due soon"
    else:
        summary = f"{count} {noun} due soon"
    parts = []
    for item in due[:limit]:
        amount = format_money(item.amount_cents) if item.amount_cents else "amount TBD"
        parts.append(f"{item.bill.name} {amount} ({item.due_text.lower()})")
    if count > limit:
        parts.append(f"+{count - limit} more")
    return f"{APP_NAME}: {summary}. " + "; ".join(parts)


def send_due_sms(
    repo: Repository,
    due: list[DueBill],
    *,
    today: date | None = None,
    sender=send_sms,
) -> bool:
    """Text a due-bill summary at most once per day.

    Returns True when a message was sent. The date is recorded only after a
    successful send, so a transient SMTP failure can be retried by the next run.
    """
    if not due:
        return False
    config = load_config(repo)
    if not config.notify_due or not config.configured:
        return False
    day = today or date.today()
    if repo.get_setting(LAST_DUE_SENT_SETTING) == day.isoformat():
        return False
    sender(config, due_message(due))
    repo.set_setting(LAST_DUE_SENT_SETTING, day.isoformat())
    return True


def send_due_sms_for_today(
    repo: Repository | None = None, *, today: date | None = None
) -> bool:
    """Open (or use) a repository and text today's due bills, if configured."""
    owned = repo is None
    if repo is None:
        from .config import db_path
        from .db import connect
        from .repository import Repository

        repo = Repository(connect(db_path()))
    try:
        day = today or date.today()
        due = repo.due_bills(day, repo.reminder_days())
        return send_due_sms(repo, due, today=day)
    finally:
        if owned:
            repo.close()


def send_velocity_sms(
    repo: Repository,
    reminder,
    *,
    today: date | None = None,
    sender=send_sms,
) -> bool:
    """Text one PLOC draw/sweep reminder at most once per month."""
    config = load_config(repo)
    if not config.notify_velocity or not config.configured:
        return False
    day = today or date.today()
    month = f"{day.year:04d}-{day.month:02d}"
    if repo.get_setting(LAST_VELOCITY_SMS_SETTING) == month:
        return False
    sender(config, f"{APP_NAME}: {reminder.title}. {reminder.body}")
    repo.set_setting(LAST_VELOCITY_SMS_SETTING, month)
    return True


def _port_setting(value: str | None) -> int:
    try:
        return int(value) if value is not None else DEFAULT_PORT
    except (TypeError, ValueError):
        return DEFAULT_PORT
