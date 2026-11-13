"""Fuzzy recognition of statement summary labels, institutions and rejection markers.

Only labels and institution names are matched fuzzily. Amounts and dates stay exact and live in
``numbers``; this module merely removes them before comparing label text.
"""

import difflib
import re

from budget_tracker.statements.numbers import parse_amount

DEFAULT_THRESHOLD = 0.82

_PUNCTUATION_RE = re.compile(r"[&/,.:;()'\"\-]")
_WHITESPACE_RE = re.compile(r"\s+")

_DATE_TOKEN_RES = (
    re.compile(r"\b\d{1,2}/\d{1,2}(?:/\d{2,4})?\b"),
    re.compile(
        r"\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)[A-Za-z]*\.?\s+\d{1,2},?\s+\d{4}\b",
        re.IGNORECASE,
    ),
)

_FAMILIES: dict[str, tuple[str, ...]] = {
    "beginning": (
        "beginning balance",
        "opening balance",
        "previous balance",
        "previous statement balance",
        "your previous balance",
        "balance forward",
        "balance brought forward",
        "starting balance",
        "balance last statement",
    ),
    "deposits": (
        "total deposits",
        "deposits and credits",
        "deposits & credits",
        "total credits",
        "credits",
        "deposits/credits",
        "additions",
        "payments in",
    ),
    "withdrawals": (
        "total withdrawals",
        "withdrawals and debits",
        "withdrawals & debits",
        "total debits",
        "debits",
        "withdrawals/debits",
        "subtractions",
        "payments out",
    ),
    "fees": (
        "total fees",
        "service charges",
        "fees and charges",
        "bank charges",
        "total service charges",
    ),
    "ending": (
        "ending balance",
        "closing balance",
        "new balance",
        "your new balance",
        "current balance",
        "total ending balance",
        "balance on",
    ),
}

_NON_FLOW_LABELS = (
    "average",
    "daily balance",
    "number of",
    "days in",
    "interest rate",
    "apr",
    "minimum payment",
    "credit limit",
    "available balance",
)

_DETAIL_NON_FLOW_LABELS = _NON_FLOW_LABELS + (
    "beginning",
    "ending",
    "opening",
    "closing",
    "previous",
    "new balance",
)

_DETAIL_DEPOSIT_KEYWORDS = (
    "deposit",
    "deposits",
    "credit",
    "credits",
    "interest",
    "addition",
    "additions",
    "payment in",
    "payments in",
    "transfer in",
    "transfers in",
)

_DETAIL_WITHDRAWAL_KEYWORDS = (
    "check",
    "checks",
    "withdrawal",
    "withdrawals",
    "debit",
    "debits",
    "charge",
    "charges",
    "fee",
    "fees",
    "payment",
    "payments",
    "purchase",
    "purchases",
    "atm",
    "transfer out",
    "transfers out",
)

_INSTITUTIONS = (
    ("Regions Bank", "Regions Bank"),
    ("Chase", "Chase"),
    ("JPMorgan Chase", "Chase"),
    ("Truist", "Truist"),
    ("BB&T", "BB&T"),
    ("Bank of America", "Bank of America"),
    ("Wells Fargo", "Wells Fargo"),
    ("Citibank", "Citibank"),
    ("Capital One", "Capital One"),
    ("US Bank", "US Bank"),
    ("PNC", "PNC"),
    ("TD Bank", "TD Bank"),
    ("Ally", "Ally"),
    ("Georgia's Own", "Georgia's Own"),
    ("BNI", "BNI"),
)

_CREDIT_CARD_MARKERS = (
    "credit card",
    "minimum payment due",
    "credit limit",
    "visa",
    "mastercard",
    "american express",
)

_TRANSACTION_TOKENS = ("pos", "debit", "purchase", "pymt")

_NON_USD_SYMBOLS = ("£", "€", "C$", "A$")
_NON_USD_CODES = ("GBP", "EUR", "CAD", "AUD")


def normalize_label(text: str) -> str:
    """Uppercase label text and collapse punctuation and whitespace."""
    return _WHITESPACE_RE.sub(" ", _PUNCTUATION_RE.sub(" ", text.upper())).strip()


_INSTITUTION_ALIASES = tuple(
    sorted(
        ((normalize_label(alias), display) for alias, display in _INSTITUTIONS),
        key=lambda item: len(item[0]),
        reverse=True,
    )
)
_NORMALIZED_FAMILIES = {
    family: tuple(normalize_label(alias) for alias in aliases) for family, aliases in _FAMILIES.items()
}


def label_text(raw_line: str) -> str:
    """Return ``raw_line`` with every amount-shaped and date-shaped token removed."""
    text = " ".join(token for token in raw_line.split() if parse_amount(token) is None)
    for pattern in _DATE_TOKEN_RES:
        text = pattern.sub(" ", text)
    return _WHITESPACE_RE.sub(" ", text).strip()


def family_of(raw_line: str, *, threshold: float = DEFAULT_THRESHOLD) -> str | None:
    """Classify a summary label as ``beginning``, ``deposits``, ``withdrawals``, ``fees`` or ``ending``."""
    normalized = normalize_label(label_text(raw_line))
    if not normalized or _contains_any(normalized, _NON_FLOW_LABELS):
        return None
    return _match_aliases(normalized, threshold)


def detail_family(raw_line: str, *, threshold: float = DEFAULT_THRESHOLD) -> str | None:
    """Classify a decomposed summary line as ``deposits`` or ``withdrawals``.

    Totals, subtotals and non-flow figures are explicit summary lines rather than detail lines, so
    they are rejected here and left for :func:`family_of`.
    """
    normalized = normalize_label(label_text(raw_line))
    if not normalized or _contains_any(normalized, _DETAIL_NON_FLOW_LABELS):
        return None
    if _contains_any(normalized, ("total", "totals", "subtotal", "subtotals")):
        return None
    deposits = _contains_any(normalized, _DETAIL_DEPOSIT_KEYWORDS)
    withdrawals = _contains_any(normalized, _DETAIL_WITHDRAWAL_KEYWORDS)
    if deposits == withdrawals:
        return None
    return "deposits" if deposits else "withdrawals"


def institution_of(raw_line: str) -> str | None:
    """Return the display name for a recognized institution in ``raw_line``."""
    normalized = normalize_label(raw_line)
    for alias, display in _INSTITUTION_ALIASES:
        if _contains_phrase(normalized, alias):
            return display
    return None


def is_credit_card_marker(text: str) -> bool:
    """Return whether ``text`` is a summary-level credit-card marker.

    Transaction-like descriptions (``POS DEBIT VISA 1234``) are never treated as markers even though
    they contain a card brand.
    """
    normalized = normalize_label(text)
    if _contains_any(normalized, _TRANSACTION_TOKENS):
        return False
    return _contains_any(normalized, _CREDIT_CARD_MARKERS)


def has_non_usd_marker(text: str) -> bool:
    """Return whether a currency declaration names a non-USD currency.

    ISO codes are only meaningful in a declaration (``Statement currency: EUR``); a foreign purchase
    inside a transaction description (``PURCHASE 25.00 EUR``) is not a document-level signal.
    """
    normalized = normalize_label(text)
    if not _contains_phrase(normalized, "currency"):
        return False
    if any(symbol in text.upper() for symbol in _NON_USD_SYMBOLS):
        return True
    return any(_contains_phrase(normalized, code) for code in _NON_USD_CODES)


def _match_aliases(normalized: str, threshold: float) -> str | None:
    for family, aliases in _NORMALIZED_FAMILIES.items():
        if normalized in aliases:
            return family
    for family, aliases in _NORMALIZED_FAMILIES.items():
        if any(_contains_phrase(normalized, alias) for alias in aliases):
            return family

    best_family: str | None = None
    best_ratio = 0.0
    for family, aliases in _NORMALIZED_FAMILIES.items():
        for alias in aliases:
            ratio = difflib.SequenceMatcher(None, normalized, alias).ratio()
            if ratio > best_ratio:
                best_family, best_ratio = family, ratio
    return best_family if best_ratio >= threshold else None


def _contains_any(text: str, phrases: tuple[str, ...]) -> bool:
    return any(_contains_phrase(text, phrase) for phrase in phrases)


def _contains_phrase(text: str, phrase: str) -> bool:
    return re.search(rf"(?<!\w){re.escape(phrase)}(?!\w)", text, re.IGNORECASE) is not None
