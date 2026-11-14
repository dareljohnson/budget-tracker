"""Pure scoring for possible duplicate transactions.

The repository supplies existing transactions; this module applies the hard filters (amount,
account, type and date window) and ranks whatever survives by description similarity. It never
hides a hard-filter-eligible candidate and it never decides a match on its own.
"""

from __future__ import annotations

import difflib
import re
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date

from .models import Transaction, TransactionType

MAX_RESULTS = 5
WINDOW_DAYS = 7
STRONG_SCORE = 85

_GENERIC_WORDS = frozenset({"POS", "DEBIT", "CARD", "PURCHASE", "PAYMENT", "ACH", "ONLINE"})
_CARD_MASK = re.compile(r"\b[X*]{2,}\d*\b")
_DIGIT_RUN = re.compile(r"\d{4,}")
_NON_ALNUM = re.compile(r"[^A-Z0-9]+")

_SOURCE_LABELS = {
    "manual": "Added manually",
    "import": "Imported",
    "receipt": "Receipt",
    "bill": "Bill payment",
}


@dataclass(frozen=True, slots=True)
class MatchCandidate:
    transaction: Transaction
    score: int  # 0-100
    reasons: tuple[str, ...]
    date_delta: int  # absolute days between the candidate and the incoming date
    description_ratio: float  # 0-1


def source_label(source: str) -> str:
    """Human-readable provenance; unknown values pass through unchanged."""
    return _SOURCE_LABELS.get(source, source)


def normalize_description(text: str) -> str:
    """Uppercase, strip card masks, long digit runs, punctuation and generic bank words."""
    upper = (text or "").upper()
    upper = _CARD_MASK.sub(" ", upper)
    words = _NON_ALNUM.sub(" ", upper).split()
    kept = [word for word in words if word not in _GENERIC_WORDS and not _DIGIT_RUN.fullmatch(word)]
    return " ".join(kept)


def description_similarity(left: str, right: str) -> float:
    """The larger of token-set Jaccard and SequenceMatcher over normalised text."""
    a = normalize_description(left)
    b = normalize_description(right)
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    tokens_a, tokens_b = set(a.split()), set(b.split())
    union = tokens_a | tokens_b
    jaccard = len(tokens_a & tokens_b) / len(union) if union else 1.0
    ratio = difflib.SequenceMatcher(None, a, b).ratio()
    return max(jaccard, ratio)


def _eligible(
    tx: Transaction,
    *,
    account_id: int,
    amount_cents: int,
    type_: TransactionType,
    transfer_account_id: int | None,
) -> bool:
    if tx.amount_cents != amount_cents or amount_cents <= 0:
        return False
    if tx.type != type_:
        return False
    if tx.account_id == account_id:
        pass
    elif tx.type == TransactionType.TRANSFER and tx.transfer_account_id == account_id:
        pass
    else:
        return False
    if transfer_account_id is not None and tx.transfer_account_id != transfer_account_id:
        return False
    return True


def match_candidates(
    transactions: Iterable[Transaction],
    *,
    account_id: int,
    amount_cents: int,
    occurred_on: date,
    type_: TransactionType,
    transfer_account_id: int | None = None,
    window_days: int = WINDOW_DAYS,
    description: str | None = None,
) -> list[MatchCandidate]:
    """Rank the transactions that pass the hard filters, strongest first.

    ``description`` is the incoming text (receipt merchant, bill name or statement description).
    When it is omitted the first supplied transaction's description is used as the reference, so
    callers that only have a list of rows still get a deterministic ranking.
    """
    rows = list(transactions)
    reference = description if description is not None else (rows[0].description if rows else "")
    ranked: list[MatchCandidate] = []
    for tx in rows:
        if not _eligible(
            tx,
            account_id=account_id,
            amount_cents=amount_cents,
            type_=type_,
            transfer_account_id=transfer_account_id,
        ):
            continue
        delta = abs((tx.occurred_on - occurred_on).days)
        if delta > window_days:
            continue
        similarity = description_similarity(reference, tx.description)
        date_points = 20 if delta == 0 else 15 - 2 * delta
        score = 60 + date_points + round(20 * similarity)
        reasons = ["same amount", "same date" if delta == 0 else f"{delta} day{'s' if delta != 1 else ''} apart"]
        if similarity >= 0.4:
            reasons.append("similar merchant")
        ranked.append(
            MatchCandidate(
                transaction=tx,
                score=score,
                reasons=tuple(reasons),
                date_delta=delta,
                description_ratio=similarity,
            )
        )
    ranked.sort(key=lambda candidate: (
        -candidate.score,
        candidate.date_delta,
        candidate.transaction.id if candidate.transaction.id is not None else 0,
    ))
    return ranked[:MAX_RESULTS]
