"""JMD to USD conversion with a cached live rate and a manual fallback.

The app stores money in USD cents. This module fetches a daily JMD-per-USD rate
from ExchangeRate-API's free endpoint, caches it in the local settings table, and
converts entered Jamaican dollars to USD cents. Only the currency pair is sent;
no account, transaction, or personal data leaves the PC.
"""

from __future__ import annotations

import json
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal
from typing import TYPE_CHECKING

from . import __version__

if TYPE_CHECKING:
    from .repository import Repository


RATE_URL = "https://open.er-api.com/v6/latest/USD"
RATE_PROVIDER = "exchangerate-api.com"
RATE_SETTING = "jmd_per_usd_rate"
RATE_AT_SETTING = "jmd_per_usd_rate_at"
RATE_SOURCE_SETTING = "jmd_per_usd_rate_source"
CACHE_TTL = timedelta(hours=12)
MIN_RATE = 0.0001
MAX_RATE = 1_000_000
USER_AGENT = f"BudgetTracker/{__version__}"


class RateError(Exception):
    """Raised when no live JMD/USD rate can be obtained."""


@dataclass(frozen=True, slots=True)
class ExchangeRate:
    """JMD per USD, with the moment and source that produced it."""

    rate: float
    fetched_at: datetime
    source: str

    @property
    def as_of_label(self) -> str:
        moment = self.fetched_at.astimezone()
        return f"{moment:%b} {moment.day}, {moment:%Y %I:%M %p}"


def valid_rate(value: float) -> bool:
    return value == value and MIN_RATE <= value <= MAX_RATE


def fetch_jmd_per_usd(timeout: float = 15) -> ExchangeRate:
    """Fetch the current JMD-per-USD rate. Raises :class:`RateError` on failure."""
    request = urllib.request.Request(
        RATE_URL,
        headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.load(response)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise RateError("Could not reach the exchange-rate service") from exc

    if not isinstance(payload, dict) or payload.get("result") != "success":
        raise RateError("The exchange-rate service returned an unexpected response")
    rates = payload.get("rates")
    raw_rate = rates.get("JMD") if isinstance(rates, dict) else None
    try:
        rate = float(raw_rate)
    except (TypeError, ValueError) as exc:
        raise RateError("The exchange-rate service did not return a JMD rate") from exc
    if not valid_rate(rate):
        raise RateError("The exchange-rate service returned an invalid JMD rate")

    fetched_at = datetime.now(timezone.utc)
    if isinstance(payload.get("time_last_update_unix"), (int, float)):
        try:
            fetched_at = datetime.fromtimestamp(payload["time_last_update_unix"], timezone.utc)
        except (OverflowError, OSError, ValueError):
            pass
    return ExchangeRate(rate, fetched_at, RATE_PROVIDER)


def load_cached_rate(repo: Repository) -> ExchangeRate | None:
    """Read the last saved rate, if it is readable."""
    raw = repo.get_setting(RATE_SETTING)
    stamp = repo.get_setting(RATE_AT_SETTING)
    if not raw or not stamp:
        return None
    try:
        rate = float(raw)
        fetched_at = datetime.fromisoformat(stamp)
    except (TypeError, ValueError):
        return None
    if not valid_rate(rate):
        return None
    if fetched_at.tzinfo is None:
        fetched_at = fetched_at.replace(tzinfo=timezone.utc)
    return ExchangeRate(rate, fetched_at, repo.get_setting(RATE_SOURCE_SETTING, "saved") or "saved")


def save_rate(repo: Repository, rate: ExchangeRate) -> None:
    """Persist a rate for the next conversion and as the offline fallback."""
    if not valid_rate(rate.rate):
        raise ValueError("Enter a valid JMD-per-USD rate")
    repo.set_setting(RATE_SETTING, f"{rate.rate:.6f}")
    repo.set_setting(RATE_AT_SETTING, rate.fetched_at.astimezone(timezone.utc).isoformat(timespec="seconds"))
    repo.set_setting(RATE_SOURCE_SETTING, rate.source)


def is_fresh(rate: ExchangeRate, *, now: datetime | None = None) -> bool:
    if rate.source == "manual":
        return False
    now = now or datetime.now(timezone.utc)
    fetched = rate.fetched_at if rate.fetched_at.tzinfo else rate.fetched_at.replace(tzinfo=timezone.utc)
    return now - fetched < CACHE_TTL


def to_usd_cents(jmd_cents: int, rate: float) -> int:
    """Convert JMD cents to USD cents at ``rate`` JMD per USD."""
    if jmd_cents < 0:
        raise ValueError("Amount cannot be negative")
    if not valid_rate(rate):
        raise ValueError("Enter a valid JMD-per-USD rate")
    value = Decimal(jmd_cents) / Decimal(str(rate))
    return int(value.quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def parse_rate(text: str) -> float:
    """Parse a user-entered JMD-per-USD rate."""
    cleaned = (text or "").strip().replace(",", "")
    try:
        value = float(cleaned)
    except ValueError as exc:
        raise ValueError("Enter the rate as a number, e.g. 158.38") from exc
    if not valid_rate(value):
        raise ValueError("Enter a valid JMD-per-USD rate")
    return value


def format_jmd(cents: int) -> str:
    """A JMD amount with its own symbol, so it is not mistaken for USD."""
    sign = "-" if cents < 0 else ""
    return f"{sign}J${abs(cents) / 100:,.2f}"
