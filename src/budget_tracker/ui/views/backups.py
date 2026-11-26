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
    BACKUP_SYNC_SETTING,
    SYNC_FOLDER_NAME,
    BackupError,
    BackupInfo,
    backups_dir,
    create_backup,
    list_backups,
    read_status,
    read_sync_status,
    stage_restore,
    suggest_sync_dirs,
    sync_all_backups,
    sync_backup,
    sync_dir_from_setting,
)
from ..components import empty_state
from ..context import AppContext
from ..theme import EXPENSE_COLOR, GAP, INCOME_COLOR, WARNING_COLOR, fs

RESTART_INSTRUCTION = "Restore will apply when Budget Tracker restarts."
RETENTION_COPY = (
    "Keeps 14 automatic backups. Manual backups and the copy taken before a "
    "restore are never pruned automatically; synced copies are also left in place. "
    "Restore asks for confirmation and applies after a restart."
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
    """Return the copy and color for the last backup or restore recorded by the core."""
    status = read_status()
    if not status:
        return "No backup has run yet. Back up now to protect your data.", None
    when = _date_label(str(status.get("at") or ""))
    kind = str(status.get("kind") or "")
    label = "Last restore" if kind == "restore" else "Last backup"
    detail = _kind_label(kind)
    prefix = f"{label} {when} · {detail}" if when else f"{label} · {detail}"
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
            selectable=True,
        )
        self.retention_text = ft.Text(
            RETENTION_COPY, size=fs(12), color=ft.Colors.ON_SURFACE_VARIANT
        )
        self.encryption_text = ft.Text(
            "Backups and synced copies aren't encrypted. Protect this PC with disk "
            "encryption such as BitLocker (Windows) or FileVault (macOS); a cloud provider "
            "sees the unencrypted ZIP files in the sync folder.",
            size=fs(12),
            color=ft.Colors.ON_SURFACE_VARIANT,
        )
        self.sync_folder_text = ft.Text(
            "", size=fs(12), color=ft.Colors.ON_SURFACE_VARIANT, selectable=True
        )
        self.sync_status_text = ft.Text(
            "", size=fs(12), color=ft.Colors.ON_SURFACE_VARIANT
        )
        self.sync_choose_button = ft.OutlinedButton(
            content="Choose sync folder…",
            icon=ft.Icons.CLOUD_UPLOAD,
            on_click=self._choose_sync_folder,
        )
        self.sync_now_button = ft.OutlinedButton(
            content="Sync now",
            icon=ft.Icons.SYNC,
            visible=False,
            on_click=self._sync_now,
        )
        self.sync_stop_button = ft.TextButton(
            content="Stop syncing",
            visible=False,
            on_click=self._stop_sync,
        )
        self.open_sync_folder_button = ft.OutlinedButton(
            content="Open sync folder",
            icon=ft.Icons.FOLDER_OPEN,
            visible=False,
            on_click=self._open_sync_folder,
        )
        self.sync_shortcuts = ft.Row(spacing=8, wrap=True)
        self.sync_block = ft.Column(
            [
                ft.Text("Cloud sync (optional)", weight=ft.FontWeight.W_600),
                self.sync_folder_text,
                self.sync_status_text,
                ft.Row(
                    [
                        self.sync_choose_button,
                        self.sync_now_button,
                        self.open_sync_folder_button,
                        self.sync_stop_button,
                    ],
                    spacing=8,
                    wrap=True,
                ),
                self.sync_shortcuts,
                ft.Text(
                    "The app copies backups into the iCloud Drive, Google Drive or OneDrive "
                    "folder on this computer; your normal sync client uploads them. If they "
                    "do not appear online, check that the provider's app is signed in, the "
                    "sync folder is on, and the account has free space.",
                    size=fs(11),
                    color=ft.Colors.ON_SURFACE_VARIANT,
                ),
            ],
            spacing=6,
        )

        self.dialog = ft.AlertDialog(
            modal=True,
            title=ft.Text("Backups"),
            content=ft.Container(
                width=760,
                height=600,
                content=ft.Column(
                    [
                        ft.Text(
                            "Database and receipts · stored locally, optionally synced",
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
                        self.sync_block,
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
        self._refresh_sync()
        self._refresh_ui()

    def _refresh_sync(self) -> None:
        try:
            sync_dir = sync_dir_from_setting(self.ctx.repo)
        except Exception:  # noqa: BLE001 - a settings read must not break the dialog
            sync_dir = None
        suggested = suggest_sync_dirs()
        self.sync_shortcuts.controls = [
            ft.TextButton(
                content=f"Use {name}",
                icon=ft.Icons.CLOUD,
                disabled=self._busy,
                on_click=lambda _, root=root: self._use_sync_root(root),
            )
            for name, root in suggested.items()
            if sync_dir is None
            or not (
                sync_dir == root / SYNC_FOLDER_NAME
                or sync_dir.is_relative_to(root)
            )
        ]
        if sync_dir is None:
            self.sync_folder_text.value = (
                "Backups are stored locally. Choose a folder inside iCloud Drive, "
                "Google Drive or OneDrive to sync copies."
            )
            self.sync_choose_button.content = "Choose sync folder…"
            self.sync_now_button.visible = False
            self.sync_stop_button.visible = False
            self.open_sync_folder_button.visible = False
            self.sync_status_text.value = ""
            return
        self.sync_folder_text.value = f"Copying backups to {sync_dir}"
        self.sync_choose_button.content = "Change sync folder…"
        self.sync_now_button.visible = True
        self.sync_stop_button.visible = True
        self.open_sync_folder_button.visible = sync_dir.is_dir()
        status = read_sync_status()
        if not sync_dir.is_dir():
            self.sync_status_text.value = "The sync folder is not available right now."
            self.sync_status_text.color = WARNING_COLOR
        elif not status:
            self.sync_status_text.value = "No sync has run yet."
            self.sync_status_text.color = ft.Colors.ON_SURFACE_VARIANT
        else:
            when = _date_label(str(status.get("at") or ""))
            if status.get("ok"):
                message = str(status.get('message') or 'Copied').replace(
                    'Synced ', 'Copied '
                )
                self.sync_status_text.value = f"Last copy {when} · {message}"
                self.sync_status_text.color = INCOME_COLOR
            else:
                self.sync_status_text.value = (
                    f"Last copy {when} failed · {status.get('message') or 'Unknown error'}"
                )
                self.sync_status_text.color = EXPENSE_COLOR

    def _use_sync_root(self, root: Path) -> None:
        self._set_sync_dir(root / SYNC_FOLDER_NAME)

    def _set_sync_dir(self, path: Path) -> None:
        try:
            folder = Path(path).expanduser()
            folder.mkdir(parents=True, exist_ok=True)
            local = backups_dir().resolve()
            target = folder.resolve()
            if target == local or target.is_relative_to(local):
                raise ValueError("Choose a folder outside the local backups folder")
        except (OSError, ValueError) as exc:
            self.ctx.notify(f"Could not use that sync folder: {exc}", error=True)
            return
        self.ctx.repo.set_setting(BACKUP_SYNC_SETTING, str(folder))
        self._refresh()
        self.ctx.notify(f"Backups will sync to {folder}")

    async def _choose_sync_folder(self, _: ft.Event | None = None) -> None:
        if self._busy:
            return
        try:
            chosen = await self._file_picker.get_directory_path(
                dialog_title="Choose a folder for synced backups"
            )
        except Exception as exc:  # noqa: BLE001 - the native picker can be unavailable
            if not self._closed_notified:
                self.ctx.notify(f"Could not open the folder picker: {exc}", error=True)
            return
        if chosen and not self._closed_notified:
            self._set_sync_dir(Path(chosen))

    def _stop_sync(self, _: ft.Event | None = None) -> None:
        self.ctx.repo.set_setting(BACKUP_SYNC_SETTING, "")
        self._refresh()
        self.ctx.notify("Cloud backup sync is off")

    async def _sync_now(self, _: ft.Event | None = None) -> None:
        if self._busy:
            return
        sync_dir = sync_dir_from_setting(self.ctx.repo)
        if sync_dir is None:
            return
        self._set_busy(True)
        try:
            copied, errors = await asyncio.to_thread(sync_all_backups, sync_dir)
        except Exception as exc:  # noqa: BLE001 - sync failures leave local backups intact
            if not self._closed_notified:
                self.ctx.notify(f"Could not sync backups: {exc}", error=True)
        else:
            if not self._closed_notified:
                if errors:
                    self.ctx.notify(
                        f"Synced {copied} backup(s); {len(errors)} failed: {errors[0]}",
                        error=True,
                    )
                else:
                    self.ctx.notify(f"Synced {copied} backup(s) to {sync_dir}")
        finally:
            self._set_busy(False)
        if not self._closed_notified:
            self._refresh()

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
        self.sync_choose_button.disabled = busy
        self.sync_now_button.disabled = busy
        self.open_sync_folder_button.disabled = busy
        self.sync_stop_button.disabled = busy
        for control in self.sync_shortcuts.controls:
            control.disabled = busy
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
            sync_dir = sync_dir_from_setting(self.ctx.repo)
            synced = False
            sync_error = None
            if sync_dir is not None:
                try:
                    await asyncio.to_thread(sync_backup, path, sync_dir)
                except BackupError as exc:
                    sync_error = str(exc)
                except Exception as exc:  # noqa: BLE001 - the local backup is still valid
                    sync_error = str(exc)
                else:
                    synced = True
            if not self._closed_notified:
                message = f"Backup saved to {Path(path).name}"
                if synced:
                    message += f" and synced to {sync_dir}"
                elif sync_error:
                    message += f"; sync failed: {sync_error}"
                self.ctx.notify(message, error=bool(sync_error))
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
        async def close_and_restart(_: ft.Event) -> None:
            self.ctx.page.pop_dialog()
            self._close()
            close = getattr(getattr(self.ctx.page, "window", None), "close", None)
            if callable(close):
                await close()

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
        self._open_path(backups_dir())

    def _open_sync_folder(self, _: ft.Event | None = None) -> None:
        folder = sync_dir_from_setting(self.ctx.repo)
        if folder is not None:
            self._open_path(folder)

    def _open_path(self, folder: Path) -> None:
        try:
            if os.name == "nt":
                os.startfile(str(folder))  # type: ignore[attr-defined]  # Windows-only
            elif sys.platform == "darwin":
                subprocess.Popen(["open", str(folder)])
            else:
                subprocess.Popen(["xdg-open", str(folder)])
        except OSError as exc:
            self.ctx.notify(f"Could not open {folder}: {exc}", error=True)
