"""Copy a receipt into the app data folder and resolve stored receipts for display.

Stored paths are relative to :func:`budget_tracker.config.data_dir`, so a database row stays
valid when the user's home folder moves. The original file is never modified; the copy gets a
UUID name whose extension comes from the file's magic bytes, not from its own name.
"""

import os
import shutil
import subprocess
import sys
from pathlib import Path
from uuid import uuid4

from budget_tracker.config import data_dir
from budget_tracker.receipts.errors import ReceiptReadError
from budget_tracker.statements.extract import sniff_kind
from budget_tracker.statements.model import StatementReadError

IS_WINDOWS = os.name == "nt"

_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
_JPEG_MAGIC = b"\xff\xd8\xff"


def receipts_dir() -> Path:
    """Return the folder that holds stored receipt files, creating it when needed."""
    path = data_dir() / "receipts"
    path.mkdir(parents=True, exist_ok=True)
    return path


def store_receipt(source: Path) -> str:
    """Copy ``source`` into the receipts folder and return its data-relative path.

    The copy is named with a fresh UUID and given an extension from the detected kind, so two
    receipts with the same original name (or a misleading one) never collide. Files whose
    contents are neither PDF nor a supported image raise :class:`ReceiptReadError`.
    """
    source = Path(source)
    try:
        kind = sniff_kind(source)
    except StatementReadError as exc:
        raise ReceiptReadError(str(exc)) from exc
    if kind == "pdf":
        extension = ".pdf"
    else:
        with source.open("rb") as handle:
            header = handle.read(8)
        extension = ".jpg" if header.startswith(_JPEG_MAGIC) else ".png"
    target = receipts_dir() / f"{uuid4().hex}{extension}"
    try:
        shutil.copyfile(source, target)
    except BaseException:
        try:
            target.unlink(missing_ok=True)
        except OSError:
            pass
        raise
    return f"receipts/{target.name}"


def resolve_receipt(stored: str) -> Path:
    """Return the absolute path for a stored receipt.

    Absolute paths, non-empty drives, rooted paths and any path containing a ``..`` part are
    rejected, and the resolved join must stay inside the receipts folder. Without that
    containment check a tampered row such as ``budget_tracker.db`` would resolve to the live
    SQLite file, so the app could read or delete something outside its data folder.
    """
    path = Path(stored)
    if path.is_absolute() or path.drive or path.root or ".." in path.parts:
        raise ReceiptReadError("Invalid receipt location")
    try:
        resolved = (data_dir() / path).resolve()
        inside_receipts = resolved.is_relative_to(receipts_dir().resolve())
    except (OSError, ValueError, TypeError) as exc:
        raise ReceiptReadError("Invalid receipt location") from exc
    if not inside_receipts:
        raise ReceiptReadError("Invalid receipt location")
    return data_dir() / path


def delete_receipt_file(stored: str | None) -> None:
    """Delete a stored receipt, ignoring missing files and invalid paths.

    Cleanup must never break the caller's save, so this is best-effort by design.
    """
    if stored is None:
        return
    try:
        resolve_receipt(stored).unlink(missing_ok=True)
    except (ReceiptReadError, OSError, ValueError, TypeError):
        pass


def open_receipt(stored: str) -> None:
    """Open a stored receipt with the platform's default viewer."""
    path = resolve_receipt(stored)
    if not path.is_file():
        raise FileNotFoundError(stored)
    try:
        if IS_WINDOWS:
            os.startfile(str(path))  # type: ignore[attr-defined]  # Windows-only attribute
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(path)])
        else:
            subprocess.Popen(["xdg-open", str(path)])
    except FileNotFoundError as exc:
        # The receipt exists; the launcher (open/xdg-open) is what could not be started.
        raise ReceiptReadError("Could not start the default application") from exc
