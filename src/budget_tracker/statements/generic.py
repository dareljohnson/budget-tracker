"""Extract a statement's own summary figures by label.

Summary lines are classified by their label alone; amounts stay exact. An explicit total wins over
the decomposed detail lines some statements print instead, and fees are kept apart from withdrawals.
"""

import re
from collections.abc import Sequence
from dataclasses import dataclass

from budget_tracker.statements.labels import detail_family, family_of, label_text
from budget_tracker.statements.lines import collapsed, group_lines
from budget_tracker.statements.model import Word
from budget_tracker.statements.numbers import parse_amount

_NEXT_LINE_TOLERANCE = 12.0

_TOTAL_RE = re.compile(r"\b(?:sub)?totals?\b", re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class SummaryFigures:
    beginning_cents: int | None = None
    deposits_cents: int | None = None
    withdrawals_cents: int | None = None
    fees_cents: int | None = None
    ending_cents: int | None = None


def find_summary(words: Sequence[Word], block_end_top: float) -> SummaryFigures:
    """Return the summary figures printed above ``block_end_top``.

    Each line that looks like a summary label contributes the nearest amount on its own line, or
    else the nearest amount on the next line within 12 points. A label with
    no amount nearby is ignored. An explicit total for a family overrides that family's detail
    lines; otherwise the detail lines are summed.
    """
    lines = [line for line in group_lines(words) if line[0].top < block_end_top]
    totals: dict[str, int] = {}
    details: dict[str, int] = {}
    for index, line in enumerate(lines):
        label = label_text(collapsed(line))
        label_words = [word for word in line if _amount_cents(word) is None]
        if not label or not label_words:
            continue
        cents = _nearest_amount(line, _label_end(label_words))
        if cents is None and index + 1 < len(lines):
            following = lines[index + 1]
            if following[0].top - line[0].top <= _NEXT_LINE_TOLERANCE:
                cents = _nearest_amount(following, _label_end(label_words))
        if cents is None:
            continue
        family = _classify(label)
        if family is None:
            continue
        if _is_explicit_total(label):
            totals.setdefault(family, cents)
        else:
            details[family] = details.get(family, 0) + cents
    return SummaryFigures(
        beginning_cents=_figure(totals, details, "beginning"),
        deposits_cents=_figure(totals, details, "deposits"),
        withdrawals_cents=_figure(totals, details, "withdrawals"),
        fees_cents=_figure(totals, details, "fees"),
        ending_cents=_figure(totals, details, "ending"),
    )


def _classify(label: str) -> str | None:
    """Classify a summary label.

    Explicit totals use family matching only. Every other line is tried with the detail keywords
    first, so a decomposed withdrawal line that names service charges is not read as fees.
    """
    if _is_explicit_total(label):
        return family_of(label)
    return detail_family(label) or family_of(label)


def _is_explicit_total(label: str) -> bool:
    return _TOTAL_RE.search(label) is not None


def _figure(totals: dict[str, int], details: dict[str, int], family: str) -> int | None:
    if family in totals:
        return totals[family]
    return details.get(family)


def _nearest_amount(line: Sequence[Word], label_end: float) -> int | None:
    """Return the amount on ``line`` whose start is nearest ``label_end``, if any."""
    candidates = [
        (abs(word.x0 - label_end), cents)
        for word in line
        if (cents := _amount_cents(word)) is not None
    ]
    if not candidates:
        return None
    return min(candidates, key=lambda candidate: candidate[0])[1]


def _amount_cents(word: Word) -> int | None:
    amount = parse_amount(word.text)
    return None if amount is None else amount.cents


def _label_end(label_words: Sequence[Word]) -> float:
    return max(word.x1 if word.x1 is not None else word.x0 for word in label_words)
