"""Statement parsing: file sniffing, adapter dispatch and the public entry points."""

from collections.abc import Callable
from dataclasses import replace
from pathlib import Path

from budget_tracker.statements.extract import (
    PAGE_OFFSET,
    pdf_word_pages,
    render_pdf_page,
    sniff_kind,
)
from budget_tracker.statements.generic import parse_generic
from budget_tracker.statements.lines import collapsed, group_lines
from budget_tracker.statements.model import (
    ParsedStatement,
    StatementReadError,
    StatementValidationError,
    UnsupportedStatementError,
    Word,
)
from budget_tracker.statements.ocr import (
    normalize_amount_token,
    ocr_available,
    ocr_document,
    ocr_images,
)
from budget_tracker.statements.regions_checking import parse_statement

# OCR returns flat positioned words, exactly like the PDF text extractor does.
OcrPages = Callable[[Path], list[Word]]

# A page with fewer extracted words than this has no usable text layer and must be OCRed.
_MIN_PAGE_WORDS = 8
# OCR is seconds per page, so a huge document is not retried through the engine.
_MAX_OCR_PAGES = 20


def _parse_adapters(words: list[Word], *, source: str = "text") -> ParsedStatement:
    """Parse with the Regions adapter first, then the generic parser.

    The generic parser also validates the Regions parser's own totals, so a document whose
    Regions template fails validation gets a second chance from the generic layout. When both
    refuse, the Regions error is the more useful one only when the document actually claimed to
    be Regions; otherwise the generic error explains why the document is unsupported.

    OCR text has its separator damage repaired before any amount is parsed.
    """
    if source == "ocr":
        words = [replace(word, text=normalize_amount_token(word.text)) for word in words]
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

    Images are always read through OCR. PDFs use their text layer; a page with fewer than
    :data:`_MIN_PAGE_WORDS` words is replaced by OCR of that page, and a document whose text path
    is unsupported or fails validation is retried once through OCR when it is at most
    :data:`_MAX_OCR_PAGES` pages. If the retry also fails, the text-path error is the one raised.
    ``ocr`` defaults to the local engine and exists so tests can inject a fake.
    """
    if sniff_kind(path) == "image":
        return _load_image(path, ocr)

    pages = pdf_word_pages(path)
    words, used_ocr = _pdf_words(path, pages)
    try:
        return _parse_adapters(words, source="ocr" if used_ocr else "text")
    except (UnsupportedStatementError, StatementValidationError) as text_error:
        ocr_fn = ocr if ocr is not None else _default_ocr()
        if ocr_fn is None or len(pages) > _MAX_OCR_PAGES:
            raise
        try:
            return _parse_adapters(ocr_fn(path), source="ocr")
        except Exception:  # noqa: BLE001 - any retry failure yields the text-path error
            raise text_error from None


def _load_image(path: Path, ocr: OcrPages | None) -> ParsedStatement:
    ocr_fn = ocr if ocr is not None else _default_ocr()
    if ocr_fn is None:
        raise UnsupportedStatementError("Image statements require the local OCR engine")
    return _parse_adapters(ocr_fn(path), source="ocr")


def _pdf_words(path: Path, pages: list[list[Word]]) -> tuple[list[Word], bool]:
    """Return the document's words, replacing image-only pages with OCR and flagging it.

    Sparse-page OCR is skipped entirely above :data:`_MAX_OCR_PAGES` pages, and each sparse page
    is rendered and recognised one at a time so a long document is never rasterised at once. A
    render or recogniser failure becomes :class:`StatementReadError`; a file-level
    :class:`OSError` still propagates.
    """
    flat = [word for page in pages for word in page]
    if len(pages) > _MAX_OCR_PAGES:
        return flat, False
    sparse = [index for index, page in enumerate(pages) if len(page) < _MIN_PAGE_WORDS]
    if not sparse or _default_ocr() is None:
        return flat, False
    try:
        for index in sparse:
            image = render_pdf_page(path, index)
            try:
                page_words = ocr_images([image])
            finally:
                image.close()
            offset = index * PAGE_OFFSET
            pages[index] = [replace(word, top=word.top + offset) for word in page_words]
    except (StatementReadError, OSError):
        raise
    except Exception as exc:  # noqa: BLE001 - render/OCR library failures are read errors
        raise StatementReadError(str(exc)) from exc
    return [word for page in pages for word in page], True


def _default_ocr() -> OcrPages | None:
    return ocr_document if ocr_available() else None
