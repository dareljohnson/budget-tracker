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

from budget_tracker.statements.extract import PAGE_OFFSET, load_image_pages, render_pdf_pages, sniff_kind
from budget_tracker.statements.model import Word

_DEFAULT_DPI = 300.0
_ENGINE = None

# An amount candidate consists only of digits, separators, spaces around them, and the usual
# sign/currency/parenthesis/marker decorations. Text and dates are left alone.
_AMOUNT_CANDIDATE_RE = re.compile(
    r"^[+(\-]?\s*\$?\s*\d(?:[\d.,\s])*\d\s*[)\-]?\s*(?:CR|DR)?$",
    re.IGNORECASE,
)
_SEPARATOR_SPACING_RE = re.compile(r"\s*([.,])\s*")
_CORE_RE = re.compile(r"\d[\d.,]*")
_DECIMAL_TAIL_RE = re.compile(r"^(.*)[.,](\d{2})$")


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

    The engine's boxes are pixels; they are scaled by ``72 / dpi`` using the image's recorded DPI
    (300 when absent, which is what :func:`extract.render_pdf_pages` uses). Empty text is dropped.
    The caller is responsible for adding a page offset when combining several pages.
    """
    engine = _engine()
    words: list[Word] = []
    for image in images:
        scale = 72.0 / _image_dpi(image)
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
    """OCR a whole statement file, flattening its pages into one reading-order word list."""
    images = render_pdf_pages(path) if sniff_kind(path) == "pdf" else load_image_pages(path)
    words: list[Word] = []
    for index, image in enumerate(images):
        offset = index * PAGE_OFFSET
        words.extend(replace(word, top=word.top + offset) for word in ocr_images([image]))
    return words


def normalize_amount_token(text: str) -> str:
    """Repair separator damage in an OCR'd amount token without inventing a decimal point.

    Thousands separators are removed. When the token ends in a separator plus exactly two digits,
    that final separator is the decimal point (``2.074.21`` -> ``2074.21``,
    ``3.240,000,000.00`` -> ``3240000000.00``); every earlier separator is grouping. A token with
    no decimal point is returned without one (``70139`` stays ``70139``), so :func:`parse_amount`
    refuses it rather than booking a 100x-wrong value. Non-amount text is returned unchanged.
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
    digits_only = re.sub(r"[.,]", "", core)
    match = _DECIMAL_TAIL_RE.match(core)
    if match is None:
        return digits_only
    whole = re.sub(r"[.,]", "", match.group(1))
    if not whole:
        return digits_only
    return f"{whole}.{match.group(2)}"


def _image_dpi(image: Image.Image) -> float:
    dpi = image.info.get("dpi")
    if isinstance(dpi, (tuple, list)) and dpi:
        try:
            value = float(dpi[0])
        except (TypeError, ValueError):
            return _DEFAULT_DPI
        if value > 0:
            return value
    return _DEFAULT_DPI


def _engine():
    global _ENGINE
    if _ENGINE is None:
        from rapidocr_onnxruntime import RapidOCR

        _ENGINE = RapidOCR()
    return _ENGINE
