"""Group extracted words into visual text lines."""

from collections.abc import Sequence

from budget_tracker.statements.model import Word

LINE_TOLERANCE = 2.0


def collapsed(line: Sequence[Word]) -> str:
    """Join a line's words into whitespace-normalized text."""
    return " ".join(" ".join(word.text for word in line).split())


def group_lines(words: Sequence[Word]) -> list[list[Word]]:
    """Group words into visual lines by ``top`` (tolerance 2 pt), each sorted by ``x0``."""
    lines: list[list[Word]] = []
    for word in sorted(words, key=lambda item: item.top):
        if not lines or abs(word.top - lines[-1][0].top) > LINE_TOLERANCE:
            lines.append([word])
        else:
            lines[-1].append(word)
    for line in lines:
        line.sort(key=lambda item: item.x0)
    return lines
