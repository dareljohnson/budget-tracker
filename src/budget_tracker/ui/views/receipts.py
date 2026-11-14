"""Capture a receipt: scan or choose a file, review the OCR draft, and save it as an expense."""

from __future__ import annotations

import asyncio
import base64
import shutil
import tempfile
from collections.abc import Callable
from datetime import date
from pathlib import Path

import flet as ft

from ...formatting import parse_date, parse_money
from ...models import Transaction, TransactionType
from ...receipts import (
    ReceiptDraft,
    ReceiptError,
    ReceiptOcrUnavailable,
    ReceiptReadError,
    ReceiptScanError,
    read_receipt,
)
from ...receipts.scanner import scan_receipt
from ...receipts.storage import resolve_receipt
from ...repository import RepositoryError
from ..components import date_field
from ..context import AppContext
from ..theme import EXPENSE_COLOR, GAP, fs

_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
_JPEG_MAGIC = b"\xff\xd8\xff"
_PDF_MAGIC = b"%PDF"
_MAX_PREVIEW_BYTES = 8 * 1024 * 1024
_EMPTY_PREVIEW = "Scan or choose a receipt to see its preview"
_UNREADABLE_NOTE = "That file could not be read. Choose another file."
_MISSING_CATEGORY_NOTE = "Create an expense category first."
_ATTACHED_STATUS = "A receipt is attached."
_NO_RECEIPT_STATUS = "No receipt attached."
_NO_PREVIEW = "No receipt attached"
_BROKEN_PREVIEW = "The saved receipt file could not be found."


def _file_row(icon: ft.IconData, path: Path) -> ft.Row:
    return ft.Row(
        [ft.Icon(icon), ft.Text(path.name)],
        spacing=8,
        alignment=ft.MainAxisAlignment.CENTER,
    )


def preview_for(path: Path) -> ft.Control:
    """A visual preview for a receipt file: an inline image, or an icon and file name.

    Only files up to 8 MB are base64-encoded, so a large phone photo cannot freeze the dialog.
    """
    path = Path(path)
    try:
        size = path.stat().st_size
        with path.open("rb") as handle:
            header = handle.read(8)
    except OSError:
        return _file_row(ft.Icons.BROKEN_IMAGE, path)
    if header.startswith(_PDF_MAGIC):
        return _file_row(ft.Icons.PICTURE_AS_PDF, path)
    if size > _MAX_PREVIEW_BYTES:
        return _file_row(ft.Icons.IMAGE, path)
    try:
        data = path.read_bytes()
    except OSError:
        return _file_row(ft.Icons.BROKEN_IMAGE, path)
    mime = "image/jpeg" if data.startswith(_JPEG_MAGIC) else "image/png"
    encoded = base64.b64encode(data).decode("ascii")
    return ft.Image(src=f"data:{mime};base64,{encoded}", fit=ft.BoxFit.CONTAIN)


class ReceiptImportDialog:
    """Review one scanned or chosen receipt and turn it into an expense transaction."""

    def __init__(self, ctx: AppContext, *, on_close: Callable[[], None] | None = None) -> None:
        self.ctx = ctx
        self.on_close = on_close
        self._closed_notified = False
        self._busy = False
        self._saving = False
        self._source_path: Path | None = None
        self._temp_dir: Path | None = None
        self._file_picker = ft.FilePicker()
        accounts = ctx.repo.list_accounts()
        categories = ctx.repo.list_categories(TransactionType.EXPENSE)

        self.scan_button = ft.FilledTonalButton(
            content="Scan with scanner",
            icon=ft.Icons.DOCUMENT_SCANNER,
            on_click=self._scan,
        )
        self.choose_button = ft.OutlinedButton(
            content="Choose photo or scan file",
            icon=ft.Icons.FOLDER_OPEN,
            on_click=self._choose_file,
        )
        self.progress = ft.ProgressRing(visible=False, width=fs(18), height=fs(18), stroke_width=2)
        self.note = ft.Text("", size=fs(13), color=ft.Colors.ON_SURFACE_VARIANT)
        self.preview_holder = ft.Container(
            height=220,
            border=ft.Border.all(1, ft.Colors.OUTLINE_VARIANT),
            border_radius=10,
            alignment=ft.Alignment.CENTER,
            content=ft.Text(
                _EMPTY_PREVIEW,
                size=fs(13),
                color=ft.Colors.ON_SURFACE_VARIANT,
            ),
        )

        self.merchant = ft.TextField(label="Merchant", max_length=120, on_change=lambda _: self._refresh_save())
        self.amount = ft.TextField(
            label="Amount",
            prefix_icon=ft.Icons.ATTACH_MONEY,
            keyboard_type=ft.KeyboardType.NUMBER,
            on_change=lambda _: self._refresh_save(),
            on_submit=self._save,
        )
        self.date = date_field(
            ctx.page, "Date", date.today(), on_change=lambda _: self._refresh_save()
        )
        self.account = ft.Dropdown(
            label="Account",
            options=[ft.DropdownOption(key=str(a.id), text=f"{a.name} ({a.kind.label})") for a in accounts],
            on_select=self._account_changed,
        )
        self.category = ft.Dropdown(
            label="Category",
            options=[ft.DropdownOption(key=str(c.id), text=c.name) for c in categories],
            on_select=self._category_changed,
        )
        self.raw_text = ft.ExpansionTile(
            title=ft.Text("Recognized text"),
            controls=[ft.Text("", selectable=True, size=fs(12))],
        )
        self.save_button = ft.FilledButton(
            content="Save", icon=ft.Icons.CHECK, disabled=True, on_click=self._save
        )

        self.dialog = ft.AlertDialog(
            modal=True,
            title=ft.Text("Scan receipt"),
            content=ft.Container(
                width=720,
                height=600,
                content=ft.Column(
                    [
                        ft.Row(
                            [self.scan_button, self.choose_button, self.progress, self.note],
                            spacing=12,
                            wrap=True,
                            vertical_alignment=ft.CrossAxisAlignment.CENTER,
                        ),
                        self.preview_holder,
                        self.merchant,
                        ft.Row([self.amount, self.date], spacing=12),
                        ft.Row([self.account, self.category], spacing=12),
                        self.raw_text,
                    ],
                    spacing=GAP,
                    scroll=ft.ScrollMode.AUTO,
                ),
            ),
            actions=[
                ft.TextButton(content="Cancel", on_click=lambda _: self._close()),
                self.save_button,
            ],
            actions_alignment=ft.MainAxisAlignment.END,
        )

    # ---------- opening and closing ----------
    async def open(self) -> None:
        if not self.ctx.repo.list_accounts():
            self.ctx.notify("Create an account first, then scan a receipt", error=True)
            self._finish()
            return
        self.ctx.page.show_dialog(self.dialog)

    def _close(self) -> None:
        """Close this dialog even when a SnackBar is stacked on top of it."""
        while self.dialog.open:
            if self.ctx.page.pop_dialog() is None:
                break
        self._finish()

    def _finish(self) -> None:
        """Release the opener and any scanner temporary files exactly once."""
        if self._closed_notified:
            return
        self._closed_notified = True
        self._cleanup_temp()
        if self.on_close is not None:
            self.on_close()

    def _cleanup_temp(self) -> None:
        """Remove the scanner's temporary folder; the user's own source file is never touched."""
        if self._temp_dir is not None:
            shutil.rmtree(self._temp_dir, ignore_errors=True)
            self._temp_dir = None

    # ---------- sources ----------
    async def _choose_file(self, _: ft.Event | None = None) -> None:
        if self._busy:
            return
        try:
            files = await self._file_picker.pick_files(
                dialog_title="Choose a receipt",
                file_type=ft.FilePickerFileType.CUSTOM,
                allowed_extensions=["pdf", "png", "jpg", "jpeg"],
            )
        except Exception as exc:  # noqa: BLE001 - native picker can be unavailable
            self.ctx.notify(f"Could not open the file picker: {exc}", error=True)
            return
        chosen = next((f for f in files or [] if f.path), None)
        if chosen is None:
            return
        await self._load(Path(chosen.path))

    async def _scan(self, _: ft.Event | None = None) -> None:
        if self._busy:
            return
        previous_temp = self._temp_dir
        self._cleanup_temp()
        self._drop_source_in(previous_temp)
        self._set_busy(True)
        try:
            self._temp_dir = Path(tempfile.mkdtemp(prefix="budget-tracker-scan-"))
            destination = self._temp_dir / "scan.png"
            await asyncio.to_thread(scan_receipt, destination)
        except ReceiptScanError as exc:
            self._cleanup_temp()
            if not self._closed_notified:
                self.ctx.notify(str(exc), error=True)
            self._set_busy(False)
            return
        except OSError as exc:
            self._cleanup_temp()
            if not self._closed_notified:
                self.ctx.notify(f"Could not scan a receipt: {exc}", error=True)
            self._set_busy(False)
            return
        if self._closed_notified:
            self._cleanup_temp()
            return
        self._set_busy(False)
        await self._load(destination)

    def _drop_source_in(self, temp_dir: Path | None) -> None:
        """Forget a source that lived in a temporary folder we just deleted.

        The draft that came from that file would otherwise stay on screen and Save would
        stay enabled against a file that no longer exists.
        """
        if temp_dir is None or self._source_path is None:
            return
        try:
            inside = Path(self._source_path).is_relative_to(temp_dir)
        except (OSError, ValueError):
            inside = False
        if not inside:
            return
        self._source_path = None
        self._clear_preview()
        self.note.value = ""
        self.note.color = ft.Colors.ON_SURFACE_VARIANT

    async def _load(self, path: Path) -> None:
        """Preview the file immediately, then install its OCR draft (or a blank one)."""
        if self._busy:
            return
        self._saving = False
        self._source_path = Path(path)
        self._set_preview(self._source_path)
        self._set_busy(True)
        try:
            draft = await asyncio.to_thread(read_receipt, self._source_path)
        except ReceiptOcrUnavailable:
            if self._closed_notified:
                return
            self._install(
                ReceiptDraft(
                    merchant="",
                    occurred_on=date.today(),
                    total_cents=None,
                    card_last4=None,
                    paid_cash=False,
                    raw_text="",
                    missing=frozenset({"total", "merchant"}),
                )
            )
            if not self.note.value:
                self.note.value = "Text recognition isn't available. Enter the details below."
                self.note.color = ft.Colors.ON_SURFACE_VARIANT
        except ReceiptReadError as exc:
            # An unreadable image produces no transaction; the dialog stays usable for
            # choosing another file or entering details by hand.
            if self._closed_notified:
                return
            self._source_path = None
            self._clear_preview()
            self.note.value = _UNREADABLE_NOTE
            self.note.color = EXPENSE_COLOR
            self.ctx.notify(str(exc), error=True)
        else:
            if self._closed_notified:
                return
            self._install(draft)
        finally:
            self._set_busy(False)
            self._refresh_ui()

    def _set_preview(self, path: Path) -> None:
        self.preview_holder.content = preview_for(path)
        self.preview_holder.alignment = ft.Alignment.CENTER

    def _clear_preview(self) -> None:
        self.preview_holder.content = ft.Text(
            _EMPTY_PREVIEW,
            size=fs(13),
            color=ft.Colors.ON_SURFACE_VARIANT,
        )
        self.preview_holder.alignment = ft.Alignment.CENTER

    # ---------- fields ----------
    def _install(self, draft: ReceiptDraft) -> None:
        self.merchant.value = draft.merchant
        self.amount.value = "" if draft.total_cents is None else f"{draft.total_cents / 100:.2f}"
        self.amount.error = None
        self.date.value = draft.occurred_on.isoformat()
        self.date.error = None
        self.raw_text.controls[0].value = draft.raw_text
        self.note.value = ""
        self.note.color = ft.Colors.ON_SURFACE_VARIANT

        account = self.ctx.service.receipt_account(draft)
        self.account.error_text = None
        self.account.value = str(account.id) if account is not None else None
        if draft.card_last4 and account is None:
            self.account.error_text = (
                f"No account matches the card ending {draft.card_last4}. Choose one."
            )
        self.category.error_text = None
        try:
            category_id = self.ctx.service.receipt_category_id(draft)
        except RepositoryError:
            # All expense categories were deleted; let the owner create one instead of crashing.
            self.category.value = None
            self.note.value = _MISSING_CATEGORY_NOTE
            self.note.color = EXPENSE_COLOR
        else:
            self.category.value = str(category_id)
        self._refresh_save()

    def _account_changed(self, _: ft.Event | None = None) -> None:
        self.account.error_text = None
        self._refresh_save()

    def _category_changed(self, _: ft.Event | None = None) -> None:
        self.category.error_text = None
        self._refresh_save()

    def _refresh_save(self) -> None:
        source_exists = self._source_path is not None and Path(self._source_path).is_file()
        valid = not self._busy and not self._saving and source_exists
        try:
            amount = parse_money(self.amount.value)
            valid = valid and amount > 0
        except ValueError:
            valid = False
        try:
            parse_date(self.date.value)
        except ValueError:
            valid = False
        if not self.account.value or not self.category.value:
            valid = False
        self.save_button.disabled = not valid
        self._refresh_ui()

    def _set_busy(self, busy: bool) -> None:
        self._busy = busy
        self.progress.visible = busy
        self.scan_button.disabled = busy
        self.choose_button.disabled = busy
        self._refresh_save()

    def _is_mounted(self) -> bool:
        control: ft.Control | None = self.dialog
        while control is not None:
            if isinstance(control, ft.Page):
                return True
            control = control.parent
        return False

    def _refresh_ui(self) -> None:
        """Refresh the dialog when it is mounted; direct tests build it without a page."""
        if self._is_mounted():
            self.dialog.update()

    # ---------- saving ----------
    def _save(self, _: ft.Event | None = None) -> None:
        if self._busy or self._saving or self._closed_notified:
            return
        valid = True
        try:
            amount = parse_money(self.amount.value)
            self.amount.error = None
        except ValueError as exc:
            self.amount.error = str(exc)
            valid = False
        try:
            occurred = parse_date(self.date.value)
            self.date.error = None
        except ValueError as exc:
            self.date.error = str(exc)
            valid = False
        if not self.account.value:
            self.account.error_text = "Select an account (create one on the Accounts page)"
            valid = False
        if not self.category.value:
            self.category.error_text = "Select a category (create one on the Categories page)"
            valid = False
        source_exists = self._source_path is not None and Path(self._source_path).is_file()
        if not source_exists:
            self.ctx.notify("Choose or scan a receipt first", error=True)
            valid = False
        if not valid:
            self._refresh_ui()
            return

        self._saving = True
        self.save_button.disabled = True
        self._refresh_ui()
        try:
            self.ctx.service.create_receipt_transaction(
                amount_cents=amount,
                occurred_on=occurred,
                description=(self.merchant.value or "").strip(),
                account_id=int(self.account.value),
                category_id=int(self.category.value),
                source_path=self._source_path,
            )
        except (RepositoryError, ReceiptError, OSError) as exc:
            self._saving = False
            self.ctx.notify(str(exc), error=True)
            self._refresh_save()
            return

        self._close()
        self.ctx.notify("Receipt saved as a transaction")
        self.ctx.data_changed()


class ReceiptAttachmentDialog:
    """Attach, replace, or remove the receipt of an already-saved transaction.

    Attaching and removing happen immediately; closing the dialog changes nothing else.
    """

    def __init__(
        self,
        ctx: AppContext,
        tx: Transaction,
        *,
        on_changed: Callable[[str | None], None] | None = None,
        on_close: Callable[[], None] | None = None,
    ) -> None:
        self.ctx = ctx
        self.tx = tx
        self.on_changed = on_changed
        self.on_close = on_close
        self._closed_notified = False
        self._busy = False
        self._temp_dir: Path | None = None
        self._file_picker = ft.FilePicker()

        self.status = ft.Text(
            _ATTACHED_STATUS if tx.receipt_path else _NO_RECEIPT_STATUS,
            size=fs(13),
            color=ft.Colors.ON_SURFACE_VARIANT,
        )
        self.progress = ft.ProgressRing(visible=False, width=fs(18), height=fs(18), stroke_width=2)
        self.choose_button = ft.OutlinedButton(
            content="Choose file",
            icon=ft.Icons.FOLDER_OPEN,
            on_click=self._choose_file,
        )
        self.scan_button = ft.FilledTonalButton(
            content="Scan with scanner",
            icon=ft.Icons.DOCUMENT_SCANNER,
            on_click=self._scan,
        )
        self.remove_button = ft.OutlinedButton(
            content="Remove receipt",
            icon=ft.Icons.DELETE_OUTLINE,
            tooltip="Removes the link and the stored copy",
            visible=bool(tx.receipt_path),
            on_click=self._remove,
        )
        self.preview_holder = ft.Container(
            height=220,
            border=ft.Border.all(1, ft.Colors.OUTLINE_VARIANT),
            border_radius=10,
            alignment=ft.Alignment.CENTER,
        )
        self._refresh_preview()

        self.dialog = ft.AlertDialog(
            modal=True,
            title=ft.Text("Receipt"),
            content=ft.Container(
                width=560,
                height=420,
                content=ft.Column(
                    [
                        ft.Row(
                            [self.choose_button, self.scan_button, self.progress],
                            spacing=12,
                            wrap=True,
                            vertical_alignment=ft.CrossAxisAlignment.CENTER,
                        ),
                        self.preview_holder,
                        self.status,
                        ft.Row([self.remove_button], spacing=8, wrap=True),
                    ],
                    spacing=GAP,
                    scroll=ft.ScrollMode.AUTO,
                ),
            ),
            actions=[ft.TextButton(content="Close", on_click=lambda _: self._close())],
            actions_alignment=ft.MainAxisAlignment.END,
        )

    # ---------- opening and closing ----------
    def open(self) -> None:
        self.ctx.page.show_dialog(self.dialog)

    def _close(self) -> None:
        """Close this dialog even when a SnackBar is stacked on top of it."""
        while self.dialog.open:
            if self.ctx.page.pop_dialog() is None:
                break
        self._finish()

    def _finish(self) -> None:
        """Release the opener and any scanner temporary files exactly once."""
        if self._closed_notified:
            return
        self._closed_notified = True
        self._cleanup_temp()
        if self.on_close is not None:
            self.on_close()

    def _cleanup_temp(self) -> None:
        """Remove the scanner's temporary folder; the user's own source file is never touched.

        The path is kept until a removal actually succeeds, so a failed attempt (for
        example while the scanner still holds the file) can be retried when the dialog closes.
        """
        if self._temp_dir is None:
            return
        try:
            shutil.rmtree(self._temp_dir)
        except FileNotFoundError:
            self._temp_dir = None
        except OSError:
            return
        else:
            self._temp_dir = None

    # ---------- sources ----------
    async def _choose_file(self, _: ft.Event | None = None) -> None:
        if self._busy:
            return
        self._set_busy(True)
        try:
            files = await self._file_picker.pick_files(
                dialog_title="Choose a receipt",
                file_type=ft.FilePickerFileType.CUSTOM,
                allowed_extensions=["pdf", "png", "jpg", "jpeg"],
            )
        except Exception as exc:  # noqa: BLE001 - native picker can be unavailable
            if not self._closed_notified:
                self.ctx.notify(f"Could not open the file picker: {exc}", error=True)
            return
        finally:
            self._set_busy(False)
        if self._closed_notified:
            return
        chosen = next((f for f in files or [] if f.path), None)
        if chosen is None:
            return
        self._attach(Path(chosen.path))

    async def _scan(self, _: ft.Event | None = None) -> None:
        if self._busy:
            return
        self._cleanup_temp()
        self._set_busy(True)
        try:
            self._temp_dir = Path(tempfile.mkdtemp(prefix="budget-tracker-scan-"))
            destination = self._temp_dir / "scan.png"
            await asyncio.to_thread(scan_receipt, destination)
        except ReceiptScanError as exc:
            self._cleanup_temp()
            if not self._closed_notified:
                self.ctx.notify(str(exc), error=True)
            return
        except OSError as exc:
            self._cleanup_temp()
            if not self._closed_notified:
                self.ctx.notify(f"Could not scan a receipt: {exc}", error=True)
            return
        finally:
            self._set_busy(False)
        try:
            if self._closed_notified:
                return
            # The service copies the scan synchronously, so the temp folder can go now.
            self._attach(destination)
        finally:
            self._cleanup_temp()

    # ---------- attach and remove ----------
    def _attach(self, path: Path) -> None:
        if self._busy or self._closed_notified or self.tx.id is None:
            return
        self._set_busy(True)
        try:
            stored = self.ctx.service.attach_receipt(self.tx.id, Path(path))
        except (RepositoryError, ReceiptError, OSError) as exc:
            self.ctx.notify(str(exc), error=True)
            return
        finally:
            self._set_busy(False)
        self.tx.receipt_path = stored
        self._refresh()
        self.ctx.data_changed()
        if self.on_changed is not None:
            self.on_changed(stored)
        self.ctx.notify("Receipt attached")

    def _remove(self, _: ft.Event | None = None) -> None:
        if self._busy or self.tx.id is None:
            return
        self._set_busy(True)
        try:
            self.ctx.service.remove_receipt(self.tx.id)
        except (RepositoryError, ReceiptError, OSError) as exc:
            self.ctx.notify(str(exc), error=True)
            return
        finally:
            self._set_busy(False)
        self.tx.receipt_path = None
        self._refresh()
        self.ctx.data_changed()
        if self.on_changed is not None:
            self.on_changed(None)
        # Close before notifying so _close cannot pop the "Receipt removed" SnackBar.
        self._close()
        self.ctx.notify("Receipt removed")

    # ---------- presentation ----------
    def _refresh_preview(self) -> None:
        self.preview_holder.alignment = ft.Alignment.CENTER
        if not self.tx.receipt_path:
            self.preview_holder.content = ft.Text(
                _NO_PREVIEW,
                size=fs(13),
                color=ft.Colors.ON_SURFACE_VARIANT,
            )
            return
        try:
            path = resolve_receipt(self.tx.receipt_path)
        except ReceiptReadError:
            self.preview_holder.content = ft.Text(
                _BROKEN_PREVIEW,
                size=fs(13),
                color=EXPENSE_COLOR,
            )
            return
        self.preview_holder.content = preview_for(path)

    def _refresh(self) -> None:
        attached = bool(self.tx.receipt_path)
        self.status.value = _ATTACHED_STATUS if attached else _NO_RECEIPT_STATUS
        self.remove_button.visible = attached
        self._refresh_preview()
        self._refresh_ui()

    def _set_busy(self, busy: bool) -> None:
        self._busy = busy
        self.progress.visible = busy
        self.choose_button.disabled = busy
        self.scan_button.disabled = busy
        self.remove_button.disabled = busy
        self._refresh_ui()

    def _is_mounted(self) -> bool:
        control: ft.Control | None = self.dialog
        while control is not None:
            if isinstance(control, ft.Page):
                return True
            control = control.parent
        return False

    def _refresh_ui(self) -> None:
        """Refresh the dialog when it is mounted; direct tests build it without a page."""
        if self._is_mounted():
            self.dialog.update()
