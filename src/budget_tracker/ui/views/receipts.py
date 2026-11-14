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
from ...models import TransactionType
from ...receipts import (
    ReceiptDraft,
    ReceiptError,
    ReceiptOcrUnavailable,
    ReceiptReadError,
    ReceiptScanError,
    read_receipt,
)
from ...receipts.scanner import scan_receipt
from ...repository import RepositoryError
from ..components import date_field
from ..context import AppContext
from ..theme import GAP, fs

_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
_JPEG_MAGIC = b"\xff\xd8\xff"
_PDF_MAGIC = b"%PDF"


def preview_for(path: Path) -> ft.Control:
    """A visual preview for a receipt file: an inline image, or a PDF icon and file name."""
    path = Path(path)
    try:
        data = path.read_bytes()
    except OSError:
        return ft.Row(
            [ft.Icon(ft.Icons.BROKEN_IMAGE), ft.Text(path.name)],
            spacing=8,
            alignment=ft.MainAxisAlignment.CENTER,
        )
    if data.startswith(_PDF_MAGIC):
        return ft.Row(
            [ft.Icon(ft.Icons.PICTURE_AS_PDF), ft.Text(path.name)],
            spacing=8,
            alignment=ft.MainAxisAlignment.CENTER,
        )
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
                "Scan or choose a receipt to see its preview",
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
        self._set_busy(True)
        try:
            self._temp_dir = Path(tempfile.mkdtemp(prefix="budget-tracker-scan-"))
            destination = self._temp_dir / "scan.png"
            await asyncio.to_thread(scan_receipt, destination)
        except ReceiptScanError as exc:
            self._cleanup_temp()
            self.ctx.notify(str(exc), error=True)
            self._set_busy(False)
            return
        except OSError as exc:
            self._cleanup_temp()
            self.ctx.notify(f"Could not scan a receipt: {exc}", error=True)
            self._set_busy(False)
            return
        await self._load(destination)

    async def _load(self, path: Path) -> None:
        """Preview the file immediately, then install its OCR draft (or a blank one)."""
        self._source_path = Path(path)
        self._set_preview(self._source_path)
        self._set_busy(True)
        try:
            draft = await asyncio.to_thread(read_receipt, self._source_path)
        except ReceiptOcrUnavailable:
            self.note.value = "Text recognition isn't available. Enter the details below."
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
        except ReceiptReadError as exc:
            self._source_path = None
            self.ctx.notify(str(exc), error=True)
        else:
            self.note.value = ""
            self._install(draft)
        finally:
            self._set_busy(False)
            self._refresh_ui()

    def _set_preview(self, path: Path) -> None:
        self.preview_holder.content = preview_for(path)
        self.preview_holder.alignment = ft.Alignment.CENTER

    # ---------- fields ----------
    def _install(self, draft: ReceiptDraft) -> None:
        self.merchant.value = draft.merchant
        self.amount.value = "" if draft.total_cents is None else f"{draft.total_cents / 100:.2f}"
        self.date.value = draft.occurred_on.isoformat()
        self.date.error = None
        self.raw_text.controls[0].value = draft.raw_text

        account = self.ctx.service.receipt_account(draft)
        self.account.error_text = None
        self.account.value = str(account.id) if account is not None else None
        if draft.card_last4 and account is None:
            self.account.error_text = (
                f"No account matches the card ending {draft.card_last4}. Choose one."
            )
        self.category.error_text = None
        self.category.value = str(self.ctx.service.receipt_category_id(draft))
        self._refresh_save()

    def _account_changed(self, _: ft.Event | None = None) -> None:
        self.account.error_text = None
        self._refresh_save()

    def _category_changed(self, _: ft.Event | None = None) -> None:
        self.category.error_text = None
        self._refresh_save()

    def _refresh_save(self) -> None:
        valid = not self._busy and self._source_path is not None
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

    def _refresh_ui(self) -> None:
        """Refresh the dialog when it is mounted; direct tests build it without a page."""
        try:
            self.dialog.update()
        except RuntimeError:
            pass

    # ---------- saving ----------
    def _save(self, _: ft.Event | None = None) -> None:
        if self._busy:
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
        if self._source_path is None:
            self.ctx.notify("Choose or scan a receipt first", error=True)
            valid = False
        if not valid:
            self._refresh_ui()
            return

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
            self.ctx.notify(str(exc), error=True)
            return

        self._close()
        self.ctx.notify("Receipt saved as a transaction")
        self.ctx.data_changed()
