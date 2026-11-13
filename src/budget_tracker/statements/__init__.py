"""Statement parsing: file sniffing, adapter dispatch and the public entry points."""

from collections.abc import Callable
from dataclasses import replace
from pathlib import Path

from budget_tracker.statements.extract import pdf_word_pages, sniff_kind
from budget_tracker.statements.generic import parse_generic
from budget_tracker.statements.lines import collapsed, group_lines
from budget_tracker.statements.model import (
    ParsedStatement,
    StatementValidationError,
    UnsupportedStatementError,
    Word,
)
from budget_tracker.statements.regions_checking import parse_statement

# OCR returns flat positioned words, exactly like the PDF text extractor does.
OcrPages = Callable[[Path], list[Word]]


def _parse_adapters(words: list[Word], *, source: str = "text") -> ParsedStatement:
    """Parse with the Regions adapter first, then the generic parser.

    The generic parser also validates the Regions parser's own totals, so a document whose
    Regions template fails validation gets a second chance from the generic layout. When both
    refuse, the Regions error is the more useful one only when the document actually claimed to
    be Regions; otherwise the generic error explains why the document is unsupported.
    """
    lines = group_lines(words)
    claimed_regions = any("REGIONS BANK" in collapsed(line).upper() for line in lines)
    try:
        return replace(parse_statement(words), source=source)
    except UnsupportedStatementError as adapter_error:
        try:
            return parse_generic(words, source=source)
        except (UnsupportedStatementError, StatementValidationError) as generic_error:
            raise (adapter_error if claimed_regions else generic_error) from None
    except StatementValidationError as adapter_error:
        try:
            return parse_generic(words, source=source)
        except (UnsupportedStatementError, StatementValidationError):
            raise adapter_error from None


def parse_pdf(path: Path) -> ParsedStatement:
    """Parse a PDF statement through the adapter chain (text path only, no OCR)."""
    return _parse_adapters([word for page in pdf_word_pages(path) for word in page])


def load_statement(path: Path, *, ocr: OcrPages | None = None) -> ParsedStatement:
    """Parse a statement file, dispatching on its contents rather than its name.

    PDFs use the text path. Images are not supported yet; Task 8 wires ``ocr`` into that branch.
    """
    if sniff_kind(path) == "pdf":
        return parse_pdf(path)
    raise UnsupportedStatementError(
        "Image statements are not available until the OCR task is complete"
    )
