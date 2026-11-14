"""Parsing and formatting helpers for money and dates."""

from __future__ import annotations

from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from .config import CURRENCY_SYMBOL


def format_money(cents: int, *, signed: bool = False) -> str:
    sign = "-" if cents < 0 else ("+" if signed and cents > 0 else "")
    return f"{sign}{CURRENCY_SYMBOL}{abs(cents) / 100:,.2f}"


def parse_money(text: str) -> int:
    """Parse user input like '1,234.56' or '$12' into positive cents."""
    cleaned = (text or "").strip().replace(",", "").replace(CURRENCY_SYMBOL, "")
    try:
        value = Decimal(cleaned)
    except InvalidOperation as exc:
        raise ValueError("Enter a valid amount, e.g. 42.50") from exc
    if not value.is_finite() or value <= 0:
        raise ValueError("Amount must be greater than zero")
    if value >= Decimal("1000000000"):
        raise ValueError("Amount is too large")
    cents = int((value * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    if cents <= 0:
        raise ValueError("Amount must be at least 0.01")
    return cents


def parse_signed_money(text: str) -> int:
    """Parse an amount that may be zero or negative (e.g. opening balances) into cents."""
    cleaned = (text or "").strip().replace(",", "").replace(CURRENCY_SYMBOL, "") or "0"
    try:
        value = Decimal(cleaned)
    except InvalidOperation as exc:
        raise ValueError("Enter a valid amount, e.g. 42.50") from exc
    if not value.is_finite():
        raise ValueError("Enter a valid amount, e.g. 42.50")
    if abs(value) >= Decimal("1000000000"):
        raise ValueError("Amount is too large")
    return int((value * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def parse_percent_bps(text: str) -> int:
    """Parse '24.99' or '24.99%' into basis points."""
    cleaned = (text or "").strip().replace("%", "")
    try:
        value = Decimal(cleaned)
    except InvalidOperation as exc:
        raise ValueError("Enter a rate like 24.99") from exc
    if not value.is_finite() or value < 0 or value > 100:
        raise ValueError("Enter a rate between 0 and 100")
    return int((value * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def format_percent(bps: int) -> str:
    return f"{bps / 100:.2f}%"


def parse_date(text: str) -> date:
    try:
        return datetime.strptime((text or "").strip(), "%Y-%m-%d").date()
    except ValueError as exc:
        raise ValueError("Use the format YYYY-MM-DD") from exc


def month_key(d: date) -> str:
    return d.strftime("%Y-%m")


def month_label(key: str) -> str:
    return datetime.strptime(key, "%Y-%m").strftime("%B %Y")


def short_month_label(key: str) -> str:
    return datetime.strptime(key, "%Y-%m").strftime("%b")


def shift_month(key: str, delta: int) -> str:
    year, month = map(int, key.split("-"))
    index = year * 12 + (month - 1) + delta
    return f"{index // 12:04d}-{index % 12 + 1:02d}"
