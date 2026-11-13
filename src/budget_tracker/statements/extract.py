"""Sniff a statement file's kind and extract its positioned words.

PDF parsing lives here so the parser package's public dispatch stays free of pdfplumber details:
library-level failures become :class:`StatementReadError`, and page positions are flattened into a
single coordinate space that preserves reading order across pages.
"""

from pathlib import Path

import pdfplumber

from budget_tracker.statements.model import StatementReadError, Word


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

    Each page's ``top`` is offset by ``page_index * 10_000`` so word order across pages matches
    reading order. Library-level PDF failures (corrupt data, encryption, unsupported structure)
    are translated into :class:`StatementReadError` so callers never depend on pdfplumber.
    """
    try:
        with pdfplumber.open(path) as pdf:
            return [
                [
                    Word(
                        word["text"],
                        float(word["x0"]),
                        float(word["top"]) + page_index * 10_000,
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
