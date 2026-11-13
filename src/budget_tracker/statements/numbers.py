"""Exact parsing of statement amounts and dates.

Amounts and dates are parsed exactly: anything that does not match a known
shape is rejected rather than guessed at.
"""

import re
from dataclasses import dataclass
from datetime import date, datetime

_AMOUNT_TOKEN_RE = re.compile(r"^\d+(?:\.\d{2})$")
_MARKERS = ("CR", "DR")
_NUMERIC_DATE_RE = re.compile(r"^(\d{1,2})/(\d{1,2})(?:/(\d{2}|\d{4}))?$")
_MONTH_DAY_RE = re.compile(r"^([A-Za-z]+) (\d{1,2})$")
_MONTH_DAY_YEAR_RE = re.compile(r"^([A-Za-z]+) (\d{1,2}), (\d{4})$")


@dataclass(frozen=True, slots=True)
class Amount:
    cents: int
    marker: str | None = None


def parse_amount(text: str) -> Amount | None:
    """Parse ``$1,234.56``, ``-1,234.56``, ``(1,234.56)``, ``1,234.56-``, ``500.00 CR`` and ``500.00 DR``."""
    raw = text.replace("$", "").replace(",", "").strip()
    marker = next((candidate for candidate in _MARKERS if raw.endswith(candidate)), None)
    if marker is not None:
        raw = raw[: -len(marker)].strip()
    negative = False
    if raw.startswith("(") and raw.endswith(")"):
        negative = True
        raw = raw[1:-1].strip()
    elif raw.startswith("-"):
        negative = True
        raw = raw[1:].strip()
    elif raw.endswith("-"):
        negative = True
        raw = raw[:-1].strip()
    if _AMOUNT_TOKEN_RE.match(raw) is None:
        return None
    whole, fraction = raw.split(".")
    cents = int(whole) * 100 + int(fraction)
    return Amount(cents=-cents if negative else cents, marker=marker)


def parse_date(text: str, anchor: date, *, anchor_on_end: bool = False) -> date | None:
    """Parse a statement date, inferring a missing year from ``anchor``.

    With ``anchor_on_end=False`` a yearless month on or after the anchor's month
    belongs to the anchor's year, otherwise to the following year. With
    ``anchor_on_end=True`` the rule is reversed: a month on or before the anchor's
    month belongs to the anchor's year, otherwise to the previous year.
    """
    raw = text.strip()
    match = _NUMERIC_DATE_RE.match(raw)
    if match is not None:
        month, day = int(match.group(1)), int(match.group(2))
        year_text = match.group(3)
        year = None if year_text is None else int(year_text) + (2000 if len(year_text) == 2 else 0)
        return _build_date(year, month, day, anchor, anchor_on_end)

    match = _MONTH_DAY_RE.match(raw)
    if match is not None:
        month = _month_number(match.group(1), "%b")
        if month is None:
            return None
        return _build_date(None, month, int(match.group(2)), anchor, anchor_on_end)

    match = _MONTH_DAY_YEAR_RE.match(raw)
    if match is not None:
        month = _month_number(match.group(1), "%b", "%B")
        if month is None:
            return None
        return _build_date(int(match.group(3)), month, int(match.group(2)), anchor, anchor_on_end)
    return None


def _month_number(name: str, *formats: str) -> int | None:
    for fmt in formats:
        try:
            return datetime.strptime(name, fmt).month
        except ValueError:
            continue
    return None


def _build_date(year: int | None, month: int, day: int, anchor: date, anchor_on_end: bool) -> date | None:
    if year is None:
        if anchor_on_end:
            year = anchor.year if month <= anchor.month else anchor.year - 1
        else:
            year = anchor.year if month >= anchor.month else anchor.year + 1
    try:
        return date(year, month, day)
    except ValueError:
        return None
