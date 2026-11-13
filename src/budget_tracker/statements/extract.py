"""Sniff a statement file's kind, extract positioned words, and rasterise pages for OCR.

PDF parsing lives here so the parser package's public dispatch stays free of pdfplumber details:
library-level failures become :class:`StatementReadError`, and page positions are flattened into a
single coordinate space that preserves reading order across pages.
"""

from pathlib import Path

import pdfplumber
import pypdfium2 as pdfium
from PIL import Image, ImageOps

from budget_tracker.statements.model import StatementReadError, Word

# Positions from later pages are shifted by this much so a flat word list keeps reading order.
PAGE_OFFSET = 10_000

# OCR needs enough pixels for small print; cap the long edge so a huge photo does not blow up the
# engine's memory use.
_MIN_SHORT_EDGE = 1_600
_MAX_LONG_EDGE = 4_000
_DEFAULT_IMAGE_DPI = 300.0


def sniff_kind(path: Path) -> str:
    """Return ``"pdf"`` or ``"image"`` from the file's magic bytes.

    The file's extension is ignored: a renamed file is classified by its contents. An unreadable
    file raises its own :class:`OSError`; bytes that are neither PDF nor a supported image raise
    :class:`StatementReadError`.
    """
    with path.open("rb") as handle:
        header = handle.read(8)
    if header.startswith(b"%PDF-"):
        return "pdf"
    if header.startswith(b"\xff\xd8\xff") or header.startswith(b"\x89PNG"):
        return "image"
    raise StatementReadError("Unrecognized file type; expected a PDF or an image")


def pdf_word_pages(path: Path) -> list[list[Word]]:
    """Read a PDF into one positioned-word list per page.

    Each page's ``top`` is offset by ``page_index * PAGE_OFFSET`` so word order across pages
    matches reading order. Library-level PDF failures (corrupt data, encryption, unsupported
    structure) are translated into :class:`StatementReadError` so callers never depend on
    pdfplumber.
    """
    try:
        with pdfplumber.open(path) as pdf:
            return [
                [
                    Word(
                        word["text"],
                        float(word["x0"]),
                        float(word["top"]) + page_index * PAGE_OFFSET,
                        float(word["x1"]),
                    )
                    for word in page.extract_words()
                ]
                for page_index, page in enumerate(pdf.pages)
            ]
    except OSError:
        # Missing/unreadable files are reported by the caller as file errors.
        raise
    except Exception as exc:  # noqa: BLE001 - pdfplumber/pdfminer raise library-specific types
        raise StatementReadError(str(exc)) from exc


def render_pdf_pages(path: Path, *, dpi: int = 300) -> list[Image.Image]:
    """Rasterise every PDF page to a Pillow image at ``dpi``.

    Each image records its render DPI in ``info["dpi"]`` so OCR can convert pixel boxes back to
    points. Library-level rendering failures become :class:`StatementReadError`.
    """
    scale = dpi / 72
    try:
        document = pdfium.PdfDocument(str(path))
    except OSError:
        raise
    except Exception as exc:  # noqa: BLE001 - pypdfium2 raises pdfium-specific types
        raise StatementReadError(str(exc)) from exc
    try:
        try:
            images = [document[index].render(scale=scale).to_pil() for index in range(len(document))]
        except Exception as exc:  # noqa: BLE001 - pypdfium2 raises pdfium-specific types
            raise StatementReadError(str(exc)) from exc
    finally:
        document.close()
    for image in images:
        image.info["dpi"] = (dpi, dpi)
    return images


def load_image_pages(path: Path) -> list[Image.Image]:
    """Load an image statement as a grayscale, contrast-normalised page.

    EXIF orientation is honoured, the short edge is grown to at least
    :data:`_MIN_SHORT_EDGE` pixels so small print survives recognition, and the long edge is
    capped at :data:`_MAX_LONG_EDGE`. The result's effective DPI is stored in ``info["dpi"]`` so
    OCR can map pixel boxes back to points.
    """
    with Image.open(path) as opened:
        dpi = _read_dpi(opened.info)
        image = ImageOps.exif_transpose(opened).convert("L")
    image = ImageOps.autocontrast(image)

    width, height = image.size
    short_edge, long_edge = sorted((width, height))
    factor = 1.0
    if short_edge < _MIN_SHORT_EDGE:
        factor = _MIN_SHORT_EDGE / short_edge
    if long_edge * factor > _MAX_LONG_EDGE:
        factor = _MAX_LONG_EDGE / long_edge
    if factor != 1.0:
        image = image.resize(
            (max(1, round(width * factor)), max(1, round(height * factor))),
            Image.Resampling.LANCZOS,
        )
    image.info["dpi"] = (dpi * factor, dpi * factor)
    return [image]


def _read_dpi(info: dict) -> float:
    """Return an image's horizontal DPI, defaulting to a page-scan equivalent."""
    dpi = info.get("dpi")
    if isinstance(dpi, (tuple, list)) and dpi:
        try:
            value = float(dpi[0])
        except (TypeError, ValueError):
            return _DEFAULT_IMAGE_DPI
        if value > 0:
            return value
    return _DEFAULT_IMAGE_DPI
