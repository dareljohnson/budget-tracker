"""Local OCR for image statements and PDF pages without a usable text layer.

The engine is imported and constructed lazily, so importing this module (and the text-only parser)
works on an installation without the OCR stack. Boxes returned by the engine are in raster pixels;
:func:`ocr_images` converts them back to the point-like coordinate space the parser uses, using the
render DPI recorded by :mod:`budget_tracker.statements.extract`.
"""

import io
import re
from collections.abc import Sequence
from dataclasses import replace
from pathlib import Path

from PIL import Image

from budget_tracker.statements.extract import (
    PAGE_OFFSET,
    load_image_pages,
    pdf_page_count,
    read_dpi,
    render_pdf_page,
    sniff_kind,
)
from budget_tracker.statements.model import Word

_ENGINE = None

# An amount candidate consists only of digits, separators, spaces around them, and the usual
# sign/currency/parenthesis/marker decorations. Text and dates are left alone.
_AMOUNT_CANDIDATE_RE = re.compile(
    r"^[+(\-]?\s*\$?\s*\d(?:[\d.,\s])*\d\s*[)\-]?\s*(?:CR|DR)?$",
    re.IGNORECASE,
)
_SEPARATOR_SPACING_RE = re.compile(r"\s*([.,])\s*")
_CORE_RE = re.compile(r"\d[\d.,]*")
_SEPARATOR_SPLIT_RE = re.compile(r"[.,]")


def ocr_available() -> bool:
    """Return whether the local OCR stack can be imported."""
    try:
        import onnxruntime  # noqa: F401
        import rapidocr_onnxruntime  # noqa: F401
    except Exception:  # noqa: BLE001 - a broken native install is "unavailable", not fatal
        return False
    return True


def ocr_images(images: Sequence[Image.Image]) -> list[Word]:
    """OCR each page image and return its recognised words in point-like coordinates.

    The engine's boxes are pixels; they are scaled by ``72 / dpi`` using the image's recorded
    effective DPI (300 when absent). Empty text is dropped. The caller is responsible for adding a
    page offset when combining several pages.
    """
    engine = _engine()
    words: list[Word] = []
    for image in images:
        scale = 72.0 / read_dpi(image.info)
        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
        result, _ = engine(buffer.getvalue())
        for box, text, _score in result or []:
            cleaned = str(text).strip()
            if not cleaned:
                continue
            xs = [float(point[0]) for point in box]
            ys = [float(point[1]) for point in box]
            words.append(
                Word(cleaned, min(xs) * scale, min(ys) * scale, max(xs) * scale)
            )
    return words


def ocr_document(path: Path) -> list[Word]:
    """OCR a whole statement file, flattening its pages into one reading-order word list.

    PDF pages are rendered and recognised one at a time, and each page image is released before
    the next is opened, so a long document does not hold all of its rasters in memory.
    """
    words: list[Word] = []
    if sniff_kind(path) == "pdf":
        for index in range(pdf_page_count(path)):
            image = render_pdf_page(path, index)
            try:
                page_words = ocr_images([image])
            finally:
                image.close()
            offset = index * PAGE_OFFSET
            words.extend(replace(word, top=word.top + offset) for word in page_words)
        return words
    for image in load_image_pages(path):
        try:
            page_words = ocr_images([image])
        finally:
            image.close()
        words.extend(page_words)
    return words


def normalize_amount_token(text: str) -> str:
    """Repair separator damage in an OCR'd amount token without inventing a decimal point.

    A token is rewritten only when it splits on ``.``/``,`` into at least three groups whose last
    group is exactly two digits and whose earlier groups are one to three digits — the shape of
    thousands grouping (``2.074.21`` -> ``2074.21``, ``3.240,000,000.00`` ->
    ``3240000000.00``). Other shapes are left alone, so a dotted date (``2024.01.15``) is not
    mangled, and a token with no decimal point (``70139`` stays ``70139``) is left for
    :func:`parse_amount` to refuse rather than booking a 100x-wrong value. Non-amount text is
    returned unchanged.
    """
    stripped = text.strip()
    if _AMOUNT_CANDIDATE_RE.match(stripped) is None:
        return text
    repaired = _SEPARATOR_SPACING_RE.sub(r"\1", stripped)
    match = _CORE_RE.search(repaired)
    if match is None:
        return text
    result = f"{repaired[: match.start()]}{_normalize_core(match.group())}{repaired[match.end():]}"
    return result.removeprefix("+")


def _normalize_core(core: str) -> str:
    """Rewrite only digit groups that look like thousands grouping.

    At least three groups are required, the last must be exactly two digits, and every earlier
    group must be one to three digits. A dotted date (``2024.01.15``) has a four-digit first
    group, so it is left untouched rather than being mangled into ``202401.15``.
    """
    groups = _SEPARATOR_SPLIT_RE.split(core)
    if (
        len(groups) >= 3
        and len(groups[-1]) == 2
        and all(1 <= len(group) <= 3 for group in groups[:-1])
    ):
        return f"{''.join(groups[:-1])}.{groups[-1]}"
    return core


def _engine():
    global _ENGINE
    if _ENGINE is None:
        from rapidocr_onnxruntime import RapidOCR

        _ENGINE = RapidOCR()
    return _ENGINE
