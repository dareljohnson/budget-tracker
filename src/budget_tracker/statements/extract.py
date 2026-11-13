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

# Resource bounds: an image is decoded only below this pixel count, a rendered page's long edge is
# clamped to this many pixels, and a document is never rasterised wholesale above this page count.
MAX_IMAGE_PIXELS = 120_000_000
MAX_RENDER_LONG_EDGE = 4_000
_MAX_RENDER_PAGES = 20


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


def pdf_page_count(path: Path) -> int:
    """Return a PDF's page count without rasterising anything.

    Library-level failures become :class:`StatementReadError`; a missing file keeps its
    :class:`OSError`.
    """
    try:
        document = pdfium.PdfDocument(str(path))
    except OSError:
        raise
    except Exception as exc:  # noqa: BLE001 - pypdfium2 raises pdfium-specific types
        raise StatementReadError(str(exc)) from exc
    try:
        return len(document)
    finally:
        document.close()


def _render_scale(page_width_pt: float, page_height_pt: float, dpi: int) -> float:
    """Return the pixels-per-point scale that keeps the page's long edge bounded.

    ``dpi`` is honoured unless it would make the long edge exceed
    :data:`MAX_RENDER_LONG_EDGE`, in which case the scale is clamped.
    """
    scale = dpi / 72
    long_edge = max(float(page_width_pt), float(page_height_pt))
    if long_edge > 0 and long_edge * scale > MAX_RENDER_LONG_EDGE:
        scale = MAX_RENDER_LONG_EDGE / long_edge
    return scale


def render_pdf_page(path: Path, index: int, *, dpi: int = 300) -> Image.Image:
    """Rasterise one PDF page, clamped so its long edge stays within the render budget.

    The image records its *actual* effective DPI (``72 * scale``) in ``info["dpi"]``, which may be
    lower than ``dpi`` when the page was clamped, so OCR maps pixel boxes to the right points.
    Library-level failures become :class:`StatementReadError`.
    """
    try:
        document = pdfium.PdfDocument(str(path))
    except OSError:
        raise
    except Exception as exc:  # noqa: BLE001 - pypdfium2 raises pdfium-specific types
        raise StatementReadError(str(exc)) from exc
    try:
        try:
            page = document[index]
            scale = _render_scale(page.get_width(), page.get_height(), dpi)
            image = page.render(scale=scale).to_pil()
        except Exception as exc:  # noqa: BLE001 - pypdfium2 raises pdfium-specific types
            raise StatementReadError(str(exc)) from exc
    finally:
        document.close()
    effective_dpi = 72.0 * scale
    image.info["dpi"] = (effective_dpi, effective_dpi)
    return image


def render_pdf_pages(path: Path, *, dpi: int = 300) -> list[Image.Image]:
    """Rasterise every page of a small PDF through :func:`render_pdf_page`.

    Documents above :data:`_MAX_RENDER_PAGES` are refused rather than materialised in full; callers
    that need a larger document should render page by page. Library-level failures become
    :class:`StatementReadError`.
    """
    count = pdf_page_count(path)
    if count > _MAX_RENDER_PAGES:
        raise StatementReadError(
            f"PDF has {count} pages; at most {_MAX_RENDER_PAGES} can be rasterised at once"
        )
    return [render_pdf_page(path, index, dpi=dpi) for index in range(count)]


def _check_image_pixels(width: int, height: int) -> None:
    """Refuse an image whose decoded pixel count exceeds :data:`MAX_IMAGE_PIXELS`."""
    if width * height > MAX_IMAGE_PIXELS:
        raise StatementReadError(
            f"Image is too large to process ({width}x{height}); "
            f"limit is {MAX_IMAGE_PIXELS} pixels"
        )


def _resize_factor(width: int, height: int) -> float:
    """Return the scale that grows the short edge and bounds the long edge."""
    short_edge, long_edge = sorted((width, height))
    factor = 1.0
    if short_edge < _MIN_SHORT_EDGE:
        factor = _MIN_SHORT_EDGE / short_edge
    if long_edge * factor > _MAX_LONG_EDGE:
        factor = _MAX_LONG_EDGE / long_edge
    return factor


def load_image_pages(path: Path) -> list[Image.Image]:
    """Load an image statement as a grayscale, contrast-normalised page.

    The pixel budget is checked from the header before decoding, so a decompression bomb is
    refused without allocating its pixels. JPEGs are decoded at a reduced scale where the decoder
    supports it. EXIF orientation is honoured, the short edge is grown to at least
    :data:`_MIN_SHORT_EDGE` pixels so small print survives recognition, and the long edge is
    capped at :data:`_MAX_LONG_EDGE`.

    ``info["dpi"]`` records the *effective* DPI: ``max(recorded_dpi, 72 * long_edge / 1000)``.
    Camera/phone images carry 72 or 96 DPI metadata, which is not scan density; the floor maps
    their pixels into the same page-like point coordinates as a rendered PDF page, so the parser's
    line and column tolerances keep their intended physical meaning.
    """
    try:
        opened = Image.open(path)
    except Image.DecompressionBombError as exc:
        raise StatementReadError(str(exc)) from exc
    except OSError:
        raise
    with opened:
        recorded_dpi = read_dpi(opened.info)
        _check_image_pixels(*opened.size)
        width, height = opened.size
        factor = _resize_factor(width, height)
        if factor != 1.0:
            opened.draft("L", (max(1, round(width * factor)), max(1, round(height * factor))))
        image = ImageOps.exif_transpose(opened).convert("L")
    image = ImageOps.autocontrast(image)

    width, height = image.size
    factor = _resize_factor(width, height)
    if factor != 1.0:
        image = image.resize(
            (max(1, round(width * factor)), max(1, round(height * factor))),
            Image.Resampling.LANCZOS,
        )
    effective_dpi = max(recorded_dpi, 72.0 * max(image.size) / 1000.0)
    image.info["dpi"] = (effective_dpi, effective_dpi)
    return [image]


def read_dpi(info: dict) -> float:
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
