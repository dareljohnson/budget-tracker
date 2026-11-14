"""The Backups dialog: status, snapshots, an on-demand backup, and a staged restore.

A restore never replaces the live database while the app is running. The chosen ZIP is
validated and staged here; :mod:`budget_tracker.backups` applies it before the next start.
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

import flet as ft

from ...backups import (
    BackupError,
    BackupInfo,
    backups_dir,
    create_backup,
    list_backups,
    read_status,
    stage_restore,
)
from ..components import empty_state
from ..context import AppContext
from ..theme import EXPENSE_COLOR, GAP, INCOME_COLOR, WARNING_COLOR, fs

RESTART_INSTRUCTION = "Restore will apply when Budget Tracker restarts."
RETENTION_COPY = (
    "Keeps 14 automatic backups. Manual backups and the copy taken before a "
    "restore are never pruned automatically. Restore asks for confirmation and applies "
    "after a restart."
)
KIND_LABELS = {"auto": "Automatic", "manual": "Manual", "pre-restore": "Pre-restore"}


def _kind_label(kind: str) -> str:
    return KIND_LABELS.get(kind, kind.replace("-", " ").title())


def _date_label(value: str) -> str:
    """Format an ISO timestamp for the list and the status line."""
    try:
        moment = datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return value
    return f"{moment:%b} {moment.day}, {moment.year} · {moment:%I:%M %p}"


def _size_label(size_bytes: int) -> str:
    if size_bytes >= 1024 * 1024:
        return f"{size_bytes / (1024 * 1024):.1f} MB"
    if size_bytes >= 1024:
        return f"{size_bytes / 1024:.1f} KB"
    return f"{size_bytes} B"


def _status_line() -> tuple[str, str | None]:
    """Return the copy and color for the last backup/restore recorded by the core."""
    status = read_status()
    if not status:
        return "No backup has run yet. Back up now to protect your data.", None
    when = _date_label(str(status.get("at") or ""))
    kind = _kind_label(str(status.get("kind") or ""))
    prefix = f"Last backup {when} · {kind}" if when else f"Last backup · {kind}"
    if status.get("ok"):
        return prefix, INCOME_COLOR
    message = str(status.get("message") or "The last backup failed.")
    return f"{prefix} failed · {message}", EXPENSE_COLOR


class BackupsDialog:
    """Show local snapshots and let the owner take one or stage a restore."""

    def __init__(self, ctx: AppContext, *, on_close: Callable[[], None] | None = None) -> None:
        self.ctx = ctx
        self.on_close = on_close
        self._closed_notified = False
        self._busy = False
        self._backups: list[BackupInfo] = []
        self._file_picker = ft.FilePicker()

        self.status_line = ft.Text("", size=fs(13), color=ft.Colors.ON_SURFACE_VARIANT)
        self.progress = ft.ProgressRing(
            visible=False, width=fs(18), height=fs(18), stroke_width=2
        )
        self.backup_button = ft.FilledButton(
            content="Back up now",
            icon=ft.Icons.BACKUP,
            on_click=self._back_up_now,
        )
        self.restore_file_button = ft.OutlinedButton(
            content="Restore from file…",
            icon=ft.Icons.RESTORE,
            on_click=self._choose_restore_file,
        )
        self.open_folder_button = ft.OutlinedButton(
            content="Open backup folder",
            icon=ft.Icons.FOLDER_OPEN,
            on_click=self._open_folder,
        )
        self.list_column = ft.Column(spacing=2, tight=True, visible=False)
        self.empty_backup_button = ft.TextButton(
            content="Back up now", on_click=self._back_up_now
        )
        self.empty_state = empty_state(
            ft.Icons.BACKUP_OUTLINED,
            "No backups yet",
            "Choose Back up now to protect your database and receipts.",
            action=self.empty_backup_button,
        )
        self.location_text = ft.Text(
            f"Backups are stored in {backups_dir()}",
            size=fs(12),
            color=ft.Colors.ON_SURFACE_VARIANT,
        )
        self.retention_text = ft.Text(
            RETENTION_COPY, size=fs(12), color=ft.Colors.ON_SURFACE_VARIANT
        )
        self.encryption_text = ft.Text(
            "Backups aren't encrypted. Protect this PC with disk encryption such as "
            "BitLocker (Windows) or FileVault (macOS).",
            size=fs(12),
            color=ft.Colors.ON_SURFACE_VARIANT,
        )

        self.dialog = ft.AlertDialog(
            modal=True,
            title=ft.Text("Backups"),
            content=ft.Container(
                width=760,
                height=520,
                content=ft.Column(
                    [
                        ft.Text(
                            "Database and receipts · stored on this PC",
                            size=fs(13),
                            color=ft.Colors.ON_SURFACE_VARIANT,
                        ),
                        ft.Row(
                            [self.status_line, self.progress, self.backup_button],
                            spacing=12,
                            alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                            vertical_alignment=ft.CrossAxisAlignment.CENTER,
                            wrap=True,
                        ),
                        self.empty_state,
                        ft.Container(
                            content=self.list_column,
                            padding=ft.Padding.symmetric(vertical=2),
                        ),
                        self.location_text,
                        self.retention_text,
                        self.encryption_text,
                        ft.Row(
                            [self.restore_file_button, self.open_folder_button],
                            spacing=8,
                            wrap=True,
                        ),
                    ],
                    spacing=GAP,
                    scroll=ft.ScrollMode.AUTO,
                ),
            ),
            actions=[
                ft.TextButton(content="Close", on_click=lambda _: self._close()),
            ],
            actions_alignment=ft.MainAxisAlignment.END,
        )
        self._refresh()

    # ---------- opening and closing ----------
    def open(self) -> None:
        self._refresh()
        self.ctx.page.show_dialog(self.dialog)

    def _close(self) -> None:
        """Close this dialog even when a SnackBar or confirmation is stacked above it."""
        while self.dialog.open:
            if self.ctx.page.pop_dialog() is None:
                break
        self._finish()

    def _finish(self) -> None:
        """Release the opener exactly once, whether shown, cancelled, or failed."""
        if self._closed_notified:
            return
        self._closed_notified = True
        if self.on_close is not None:
            self.on_close()

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

    # ---------- listing ----------
    def _refresh(self) -> None:
        try:
            self._backups = list_backups()
        except Exception as exc:  # noqa: BLE001 - a listing failure must not break the dialog
            self._backups = []
            if not self._closed_notified:
                self.ctx.notify(f"Could not read the backup folder: {exc}", error=True)

        status, color = _status_line()
        self.status_line.value = status
        self.status_line.color = color or ft.Colors.ON_SURFACE_VARIANT

        self.list_column.controls = [self._row(info) for info in self._backups]
        self.list_column.visible = bool(self._backups)
        self.empty_state.visible = not self._backups
        self._refresh_ui()

    def _row(self, info: BackupInfo) -> ft.Container:
        kind = _kind_label(info.kind)
        detail = kind
        if info.reason and info.reason not in {info.kind, "automatic", "manual"}:
            detail = f"{kind} · {info.reason}"
        restore = ft.TextButton(
            content="Restore",
            disabled=self._busy,
            on_click=lambda _, path=info.path: self._ask_restore(path),
        )
        return ft.Container(
            content=ft.Row(
                [
                    ft.Column(
                        [
                            ft.Text(_date_label(info.created_at), weight=ft.FontWeight.W_500),
                            ft.Text(detail, size=fs(12), color=ft.Colors.ON_SURFACE_VARIANT),
                        ],
                        spacing=2,
                        expand=True,
                    ),
                    ft.Text(
                        _size_label(info.size_bytes),
                        size=fs(13),
                        color=ft.Colors.ON_SURFACE_VARIANT,
                    ),
                    restore,
                ],
                spacing=12,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
            padding=ft.Padding.symmetric(horizontal=12, vertical=8),
            border_radius=10,
            bgcolor=ft.Colors.SURFACE_CONTAINER_LOW,
        )

    # ---------- busy state ----------
    def _set_busy(self, busy: bool) -> None:
        self._busy = busy
        self.progress.visible = busy
        self.backup_button.disabled = busy
        self.empty_backup_button.disabled = busy
        self.restore_file_button.disabled = busy
        self.open_folder_button.disabled = busy
        self.list_column.controls = [self._row(info) for info in self._backups]
        self._refresh_ui()

    # ---------- manual backup ----------
    async def _back_up_now(self, _: ft.Event | None = None) -> None:
        """Create a manual snapshot in a worker so a large receipts folder cannot freeze the UI."""
        if self._busy:
            return
        self._set_busy(True)
        try:
            path = await asyncio.to_thread(create_backup, kind="manual", reason="manual")
        except BackupError as exc:
            if not self._closed_notified:
                self.ctx.notify(str(exc), error=True)
        except Exception as exc:  # noqa: BLE001 - worker failures must leave the dialog usable
            if not self._closed_notified:
                self.ctx.notify(f"Could not create a backup: {exc}", error=True)
        else:
            if not self._closed_notified:
                self.ctx.notify(f"Backup saved to {Path(path).name}")
        finally:
            self._set_busy(False)
        if not self._closed_notified:
            self._refresh()

    # ---------- restore ----------
    def _ask_restore(self, path: Path) -> None:
        """Confirm before staging; the live files are never touched while the app runs."""
        async def confirmed(_: ft.Event | None = None) -> None:
            self.ctx.page.pop_dialog()
            await self._stage_restore(path)

        self.ctx.page.show_dialog(
            ft.AlertDialog(
                modal=True,
                icon=ft.Icon(ft.Icons.RESTORE, color=WARNING_COLOR),
                title=ft.Text("Restore this backup?"),
                content=ft.Text(
                    "This replaces your database and receipt images with the snapshot "
                    f"{path.name}. Your current data is backed up first.\n\n"
                    f"{RESTART_INSTRUCTION}"
                ),
                actions=[
                    ft.TextButton(
                        content="Cancel",
                        on_click=lambda _: self.ctx.page.pop_dialog(),
                    ),
                    ft.FilledButton(
                        content="Restore",
                        on_click=confirmed,
                    ),
                ],
                actions_alignment=ft.MainAxisAlignment.END,
            )
        )

    async def _stage_restore(self, path: Path) -> None:
        if self._busy:
            return
        self._set_busy(True)
        try:
            await asyncio.to_thread(stage_restore, path)
        except BackupError as exc:
            if not self._closed_notified:
                self.ctx.notify(str(exc), error=True)
            return
        except Exception as exc:  # noqa: BLE001 - any staging failure leaves current data intact
            if not self._closed_notified:
                self.ctx.notify(f"Could not stage the restore: {exc}", error=True)
            return
        finally:
            self._set_busy(False)
        if not self._closed_notified:
            self._show_restart_prompt()

    def _show_restart_prompt(self) -> None:
        def close_and_restart(_: ft.Event) -> None:
            self.ctx.page.pop_dialog()
            self._close()
            window = getattr(self.ctx.page, "window", None)
            close = getattr(window, "close", None)
            if callable(close):
                close()

        self.ctx.page.show_dialog(
            ft.AlertDialog(
                modal=True,
                icon=ft.Icon(ft.Icons.RESTART_ALT, color=WARNING_COLOR),
                title=ft.Text("Restore ready"),
                content=ft.Text(RESTART_INSTRUCTION),
                actions=[
                    ft.TextButton(
                        content="Later",
                        on_click=lambda _: self.ctx.page.pop_dialog(),
                    ),
                    ft.FilledButton(
                        content="Close and restart",
                        on_click=close_and_restart,
                    ),
                ],
                actions_alignment=ft.MainAxisAlignment.END,
            )
        )

    async def _choose_restore_file(self, _: ft.Event | None = None) -> None:
        if self._busy:
            return
        try:
            files = await self._file_picker.pick_files(
                dialog_title="Restore from backup",
                file_type=ft.FilePickerFileType.CUSTOM,
                allowed_extensions=["zip"],
            )
        except Exception as exc:  # noqa: BLE001 - the native picker can be unavailable
            if not self._closed_notified:
                self.ctx.notify(f"Could not open the file picker: {exc}", error=True)
            return
        if self._closed_notified:
            return
        chosen = next((file for file in files or [] if file.path), None)
        if chosen is None:
            return
        self._ask_restore(Path(chosen.path))

    def _open_folder(self, _: ft.Event | None = None) -> None:
        folder = backups_dir()
        try:
            if os.name == "nt":
                os.startfile(str(folder))  # type: ignore[attr-defined]  # Windows-only
            elif sys.platform == "darwin":
                subprocess.Popen(["open", str(folder)])
            else:
                subprocess.Popen(["xdg-open", str(folder)])
        except OSError as exc:
            self.ctx.notify(f"Could not open the backup folder: {exc}", error=True)
