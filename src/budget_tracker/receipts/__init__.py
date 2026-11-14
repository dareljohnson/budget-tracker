"""Receipt capture: local OCR plus a pure parser for receipt words.

The public entry point :func:`read_receipt` reads one scanned or photographed receipt
entirely on-device. Only page 0 is recognised, so a multi-page PDF does not spend time on
pages that cannot belong to one receipt.
"""

from datetime import date
from pathlib import Path

from budget_tracker.receipts.errors import (
    ReceiptError,
    ReceiptOcrUnavailable,
    ReceiptReadError,
    ReceiptScanError,
)
from budget_tracker.receipts.parser import ReceiptDraft, parse_receipt
from budget_tracker.statements.extract import (
    load_image_pages,
    render_pdf_page,
    sniff_kind,
)
from budget_tracker.statements.model import StatementReadError
from budget_tracker.statements.ocr import ocr_available, ocr_images

__all__ = [
    "ReceiptDraft",
    "ReceiptError",
    "ReceiptOcrUnavailable",
    "ReceiptReadError",
    "ReceiptScanError",
    "parse_receipt",
    "read_receipt",
]


def read_receipt(path: Path, *, today: date | None = None) -> ReceiptDraft:
    """OCR the first page of a receipt file and turn its words into a reviewable draft.

    ``today`` anchors a receipt that does not print its own date; it defaults to the current
    day. A missing OCR stack raises :class:`ReceiptOcrUnavailable`, and a file that cannot be
    sniffed, rendered or recognised raises :class:`ReceiptReadError`, so the caller can keep
    the dialog open for manual entry.
    """
    if not ocr_available():
        raise ReceiptOcrUnavailable("Text recognition isn't available on this computer")
    try:
        if sniff_kind(path) == "pdf":
            image = render_pdf_page(path, 0)
        else:
            image = load_image_pages(path)[0]
    except (StatementReadError, OSError, IndexError, ValueError) as exc:
        raise ReceiptReadError("This file could not be read as a receipt image") from exc
    try:
        words = ocr_images([image])
    except Exception as exc:  # noqa: BLE001 - engine failures are surfaced as a user-facing message
        raise ReceiptReadError("Text recognition failed for this image") from exc
    return parse_receipt(words, today=today or date.today())
