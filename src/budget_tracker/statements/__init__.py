"""PDF adapter for bank statements."""

from pathlib import Path

import pdfplumber

from budget_tracker.statements.model import ParsedStatement, StatementReadError, Word
from budget_tracker.statements.regions_checking import parse_statement


def parse_pdf(path: Path) -> ParsedStatement:
    """Read a PDF into positioned words and parse it as a statement.

    Library-level PDF failures (corrupt data, encryption, unsupported structure) are translated
    into :class:`StatementReadError` so callers never have to depend on pdfplumber/pdfminer.
    """
    try:
        with pdfplumber.open(path) as pdf:
            words = [
                Word(w["text"], float(w["x0"]), float(w["top"]) + page_index * 10_000)
                for page_index, page in enumerate(pdf.pages)
                for w in page.extract_words()
            ]
    except OSError:
        # Missing/unreadable files are reported by the caller as file errors.
        raise
    except Exception as exc:  # noqa: BLE001 - pdfplumber/pdfminer raise library-specific types
        raise StatementReadError(str(exc)) from exc
    return parse_statement(words)
