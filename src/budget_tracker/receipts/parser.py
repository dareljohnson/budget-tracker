"""Pure parser turning receipt OCR words into a reviewable draft.

The parser only sees positioned words. It does not read files, touch the database or
call the UI, so it can be exercised with hand-built word lists.
"""

import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date

from budget_tracker.statements.lines import collapsed, group_lines
from budget_tracker.statements.model import Word
from budget_tracker.statements.numbers import parse_amount, parse_date
from budget_tracker.statements.ocr import normalize_amount_token

_TOTAL_LINE = re.compile(r"\b(?:GRAND\s+TOTAL|TOTAL|AMOUNT\s+DUE|BALANCE\s+DUE)\b", re.IGNORECASE)
_ISO_DATE = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")
_CARD_MASKS = (
    re.compile(r"[*xX]{4,}\s*[- ]?\s*(\d{4})\b"),
    re.compile(r"\bending\s+in\s+(\d{4})\b", re.IGNORECASE),
    re.compile(r"\b(?:\d{4}[ -]?){3}(\d{4})\b"),
)
_CASH = re.compile(r"\bCASH\b", re.IGNORECASE)
_PHONE = re.compile(r"\(?\d{3}\)?[ -]\d{3}[ -]\d{4}")
_HEADER_NOISE = re.compile(r"\b(?:RECEIPT|INVOICE|STORE|PHONE|TEL|WWW|ORDER|THANK YOU)\b", re.IGNORECASE)
_WHOLE_DOLLARS = re.compile(r"^\$?\d+(?:,\d{3})*$")
_MERCHANT_CUTS = (
    _PHONE,
    re.compile(r"#\d+"),
    re.compile(r"\bSTORE\b", re.IGNORECASE),
    re.compile(r"\b\d{4}\b"),
)


@dataclass(frozen=True, slots=True)
class ReceiptDraft:
    merchant: str
    occurred_on: date
    total_cents: int | None
    card_last4: str | None
    paid_cash: bool
    raw_text: str
    missing: frozenset[str]  # contains "total" and/or "merchant"


def parse_receipt(words: Sequence[Word], *, today: date) -> ReceiptDraft:
    """Build a draft from OCR words, flagging fields the receipt did not provide."""
    text = _text(words)
    lines = text.split("\n") if text else []

    total_cents = _total_cents(lines)
    merchant = _merchant(lines)
    card_last4 = _card_last4(text)

    missing: list[str] = []
    if total_cents is None:
        missing.append("total")
    if merchant == "":
        missing.append("merchant")

    return ReceiptDraft(
        merchant=merchant,
        occurred_on=_receipt_date(lines, today),
        total_cents=total_cents,
        card_last4=card_last4,
        paid_cash=bool(_CASH.search(text)) and card_last4 is None,
        raw_text=text,
        missing=frozenset(missing),
    )


def _text(words: Sequence[Word]) -> str:
    """Join the visual lines of an OCR word list, in reading order."""
    return "\n".join(collapsed(line) for line in group_lines(words))


def _total_cents(lines: list[str]) -> int | None:
    """Return the last total-like line's largest positive amount.

    Later matching lines win, so a final "GRAND TOTAL" supersedes an earlier "TOTAL"
    line. A receipt without a total line yields ``None`` rather than a guess.
    """
    total: int | None = None
    for line in lines:
        if _TOTAL_LINE.search(line) is None:
            continue
        best: int | None = None
        for token in line.split():
            amount = _amount(token)
            if amount is None or amount <= 0:
                continue
            if best is None or amount > best:
                best = amount
        if best is not None:
            total = best
    return total


def _amount(token: str) -> int | None:
    """Parse one token as cents, accepting a bare whole-dollar amount as dollars."""
    normalized = normalize_amount_token(token)
    parsed = parse_amount(normalized)
    if parsed is not None:
        return parsed.cents
    if _WHOLE_DOLLARS.match(normalized):
        return int(normalized.replace("$", "").replace(",", "")) * 100
    return None


def _receipt_date(lines: list[str], today: date) -> date:
    """Return the first ISO date, else the first parseable date, else ``today``."""
    for match in _ISO_DATE.finditer("\n".join(lines)):
        try:
            return date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
        except ValueError:
            continue
    for line in lines:
        parsed = _date_in(line, today)
        if parsed is not None:
            return parsed
    return today


def _date_in(line: str, today: date) -> date | None:
    text = line.strip()
    if not text:
        return None
    parsed = parse_date(text, today)
    if parsed is not None:
        return parsed
    for token in text.split():
        parsed = parse_date(token, today)
        if parsed is not None:
            return parsed
    return None


def _merchant(lines: list[str]) -> str:
    """Pick the merchant from the first few printable lines of the receipt."""
    candidates = [line for line in lines if line.strip()][:6]
    for line in candidates:
        cleaned = _clean_merchant(line)
        if not cleaned:
            continue
        if _TOTAL_LINE.search(line) or _HEADER_NOISE.search(line):
            continue
        if "@" in line or "://" in line:
            continue
        stripped = line.strip()
        if stripped[:1].isdigit():
            continue
        letters = sum(char.isalpha() for char in stripped)
        digits = sum(char.isdigit() for char in stripped)
        if letters < 2 or digits > letters:
            continue
        return cleaned
    return ""


def _clean_merchant(line: str) -> str:
    """Trim address/phone/store noise appended to a merchant line."""
    text = " ".join(line.split())
    cut = len(text)
    for pattern in _MERCHANT_CUTS:
        match = pattern.search(text)
        if match is not None and match.start() < cut:
            cut = match.start()
    return text[:cut].strip(" .,;:-#")


def _card_last4(text: str) -> str | None:
    """Return the last four digits of a masked or printed card number, if any."""
    for pattern in _CARD_MASKS:
        match = pattern.search(text)
        if match is not None:
            return match.group(1)
    return None
