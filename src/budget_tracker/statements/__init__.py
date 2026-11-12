"""PDF adapter for bank statements."""

from pathlib import Path

import pdfplumber

from budget_tracker.statements.model import ParsedStatement, Word
from budget_tracker.statements.regions_checking import parse_statement


def parse_pdf(path: Path) -> ParsedStatement:
    with pdfplumber.open(path) as pdf:
        words = [
            Word(w["text"], float(w["x0"]), float(w["top"]) + page_index * 10_000)
            for page_index, page in enumerate(pdf.pages)
            for w in page.extract_words()
        ]
    return parse_statement(words)
