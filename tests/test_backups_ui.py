"""UI and startup wiring for backups: the dialog, the rail button, and the auto-backup task."""

import asyncio
import os
import tempfile
import threading
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import flet as ft

from budget_tracker import backups
from budget_tracker.config import db_path
from budget_tracker.db import connect
from budget_tracker.repository import Repository


class _PageStub:
    """Minimal stand-in for ``ft.Page`` so dialogs and the shell can be built headless."""

    def __init__(self) -> None:
        self.overlay: list = []
        self.added: list[tuple] = []
        self.window = SimpleNamespace(close=mock.Mock())
        self.run_task = mock.Mock(return_value=mock.Mock())

    def show_dialog(self, control) -> None:
        if isinstance(control, ft.AlertDialog):
            control.open = True
        self.overlay.append(control)

    def pop_dialog(self):
        for control in reversed(self.overlay):
            if isinstance(control, ft.AlertDialog):
                control.open = False
                self.overlay.remove(control)
                return control
        return None

    def add(self, *controls) -> None:
        self.added.append(controls)

    def update(self, *_) -> None:
        pass

    @property
    def open_dialogs(self) -> list:
        return [c for c in self.overlay if isinstance(c, ft.AlertDialog)]

    @property
    def snackbars(self) -> list:
        return [c for c in self.overlay if isinstance(c, ft.SnackBar)]

    @property
    def errors(self) -> list[str]:
        from budget_tracker.ui.theme import INCOME_COLOR

        return [
            c.content.value
            for c in self.snackbars
            if c.bgcolor != INCOME_COLOR
        ]


def _texts(control) -> list[str]:
    """Collect every string under a Flet control tree (text values, labels, actions)."""
    if control is None:
        return []
    found: list[str] = []
    value = getattr(control, "value", None)
    if isinstance(value, str):
        found.append(value)
    for attr in ("content", "title", "label"):
        child = getattr(control, attr, None)
        if isinstance(child, str):
            found.append(child)
        elif child is not None:
            found.extend(_texts(child))
    children = list(getattr(control, "controls", None) or [])
    children += list(getattr(control, "actions", None) or [])
    for child in children:
        found.extend(_texts(child))
    return found


_BUTTONS = (ft.TextButton, ft.FilledButton, ft.OutlinedButton, ft.FilledTonalButton)


def _find_button(control, label: str):
    """Depth-first search for a button whose visible label matches."""
    if control is None:
        return None
    if isinstance(control, _BUTTONS) and getattr(control, "content", None) == label:
        return control
    for attr in ("content", "title"):
        child = getattr(control, attr, None)
        if child is not None and not isinstance(child, str):
            found = _find_button(child, label)
            if found is not None:
                return found
    children = list(getattr(control, "controls", None) or [])
    children += list(getattr(control, "actions", None) or [])
    for child in children:
        found = _find_button(child, label)
        if found is not None:
            return found
    return None


class _BackupTestCase(unittest.TestCase):
    """A temp data folder, a real database file, and a stub page."""

    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmpdir.cleanup)
        self.home = Path(self._tmpdir.name)
        env = mock.patch.dict(os.environ, {"BUDGET_TRACKER_HOME": str(self.home)})
        env.start()
        self.addCleanup(env.stop)

        from budget_tracker.services import BudgetService
        from budget_tracker.ui.context import AppContext

        self.repo = Repository(connect(db_path()))
        self.addCleanup(self.repo.close)
        self.page = _PageStub()
        self.ctx = AppContext(page=self.page, repo=self.repo, service=BudgetService(self.repo))

    def _backup(self, *, kind: str = "manual", reason: str = "unit test") -> Path:
        return backups.create_backup(kind=kind, reason=reason)

    def _dialog(self, **kwargs):
        from budget_tracker.ui.views.backups import BackupsDialog

        return BackupsDialog(self.ctx, **kwargs)


class TestBackupsDialogList(_BackupTestCase):
    def test_lists_backups_with_date_kind_size_and_restore_action(self):
        self._backup(kind="manual", reason="before upgrade")
        self._backup(kind="auto", reason="automatic")

        dialog = self._dialog()

        self.assertEqual(len(dialog._backups), 2)
        self.assertEqual(len(dialog.list_column.controls), 2)
        texts = _texts(dialog.list_column)
        joined = "\n".join(texts)
        self.assertIn("Manual", joined)
        self.assertIn("Automatic", joined)
        self.assertIn("before upgrade", joined)
        self.assertTrue(any(text.endswith(("KB", "MB", "B")) for text in texts))

        for info, row in zip(dialog._backups, dialog.list_column.controls):
            moment = datetime.fromisoformat(info.created_at)
            row_text = "\n".join(_texts(row))
            self.assertIn(f"{moment:%b} {moment.day}", row_text)
            self.assertIsNotNone(_find_button(row, "Restore"))

    def test_empty_state_suggests_back_up_now(self):
        dialog = self._dialog()

        self.assertTrue(dialog.empty_state.visible)
        self.assertFalse(dialog.list_column.visible)
        self.assertIn("Back up now", "\n".join(_texts(dialog.empty_state)))

    def test_status_line_reports_the_last_backup(self):
        self._backup(kind="manual", reason="unit test")

        dialog = self._dialog()

        self.assertIn("Last backup", dialog.status_line.value)
        self.assertIn("Manual", dialog.status_line.value)

    def test_status_line_reports_the_last_failure(self):
        backups.write_status("manual", False, "disk full")

        dialog = self._dialog()

        self.assertIn("disk full", dialog.status_line.value)

    def test_status_line_invites_a_first_backup(self):
        dialog = self._dialog()

        self.assertIn("No backup", dialog.status_line.value)

    def test_retention_location_and_encryption_copy_is_shown(self):
        dialog = self._dialog()

        joined = "\n".join(_texts(dialog.dialog.content))
        self.assertIn("14 automatic backups", joined)
        self.assertIn(str(backups.backups_dir()), joined)
        self.assertIn("BitLocker", joined)


class TestBackUpNow(_BackupTestCase):
    def test_back_up_now_runs_in_a_worker_shows_progress_and_refreshes(self):
        dialog = self._dialog()
        observed: dict = {}
        threads: list[str] = []

        def fake_create(**kwargs):
            observed["progress"] = dialog.progress.visible
            observed["disabled"] = dialog.backup_button.disabled
            threads.append(threading.current_thread().name)
            return backups.create_backup(**kwargs)

        with mock.patch(
            "budget_tracker.ui.views.backups.create_backup", side_effect=fake_create
        ) as create:
            asyncio.run(dialog._back_up_now(None))

        create.assert_called_once_with(kind="manual", reason="manual")
        self.assertEqual(observed, {"progress": True, "disabled": True})
        self.assertNotEqual(threads[0], threading.current_thread().name)
        self.assertFalse(dialog.progress.visible)
        self.assertFalse(dialog.backup_button.disabled)
        self.assertFalse(dialog.restore_file_button.disabled)
        self.assertEqual(len(dialog.list_column.controls), 1)
        self.assertFalse(dialog.empty_state.visible)
        self.assertTrue(dialog.list_column.visible)

    def test_failed_backup_notifies_and_leaves_the_dialog_usable(self):
        dialog = self._dialog()
        self._backup()

        with mock.patch(
            "budget_tracker.ui.views.backups.create_backup",
            side_effect=backups.BackupError("no room on disk"),
        ):
            asyncio.run(dialog._back_up_now(None))

        self.assertEqual(self.page.errors, ["no room on disk"])
        self.assertFalse(dialog.backup_button.disabled)
        self.assertFalse(dialog._busy)
        self.assertEqual(len(dialog.list_column.controls), 1)

        with mock.patch(
            "budget_tracker.ui.views.backups.create_backup",
            side_effect=lambda **kwargs: backups.create_backup(**kwargs),
        ):
            asyncio.run(dialog._back_up_now(None))
        self.assertEqual(len(dialog.list_column.controls), 2)

    def test_a_second_click_while_busy_does_nothing(self):
        dialog = self._dialog()
        dialog._busy = True

        with mock.patch("budget_tracker.ui.views.backups.create_backup") as create:
            asyncio.run(dialog._back_up_now(None))

        create.assert_not_called()


class TestRestore(_BackupTestCase):
    def test_restore_confirmation_stages_and_shows_the_restart_instruction(self):
        path = self._backup()
        dialog = self._dialog()

        dialog._ask_restore(path)

        [confirm] = self.page.open_dialogs
        joined = " ".join(_texts(confirm)).lower()
        self.assertIn("database", joined)
        self.assertIn("receipt", joined)
        self.assertIn("restart", joined)

        asyncio.run(_find_button(confirm, "Restore").on_click(None))

        self.assertEqual(backups.pending_restore()["state"], "staged")
        restart = self.page.open_dialogs[-1]
        self.assertIn(
            "Restore will apply when Budget Tracker restarts.",
            _texts(restart),
        )
        self.assertIsNotNone(_find_button(restart, "Close and restart"))
        self.assertIsNotNone(_find_button(restart, "Later"))

    def test_restore_failure_shows_the_error_and_leaves_the_list_usable(self):
        path = self._backup()
        dialog = self._dialog()
        dialog.open()
        dialog._ask_restore(path)
        confirm = self.page.open_dialogs[-1]

        with mock.patch(
            "budget_tracker.ui.views.backups.stage_restore",
            side_effect=backups.BackupError("The backup is damaged"),
        ):
            asyncio.run(_find_button(confirm, "Restore").on_click(None))

        self.assertEqual(self.page.errors, ["The backup is damaged"])
        self.assertTrue(dialog.dialog.open)
        self.assertFalse(dialog._busy)
        self.assertFalse(dialog.backup_button.disabled)
        self.assertEqual(len(dialog.list_column.controls), 1)
        self.assertIsNone(backups.pending_restore())

    def test_restore_from_file_picks_zip_and_asks_for_confirmation(self):
        path = self._backup()
        dialog = self._dialog()
        files = [SimpleNamespace(path=str(path))]

        with mock.patch.object(
            dialog._file_picker,
            "pick_files",
            new=mock.AsyncMock(return_value=files),
        ) as pick:
            asyncio.run(dialog._choose_restore_file(None))

        pick.assert_awaited_once()
        self.assertEqual(pick.await_args.kwargs["allowed_extensions"], ["zip"])
        [confirm] = self.page.open_dialogs
        self.assertIn("database", " ".join(_texts(confirm)).lower())

    def test_restore_picker_failure_is_reported(self):
        dialog = self._dialog()

        with mock.patch.object(
            dialog._file_picker,
            "pick_files",
            new=mock.AsyncMock(side_effect=RuntimeError("no picker")),
        ):
            asyncio.run(dialog._choose_restore_file(None))

        self.assertEqual(self.page.errors, ["Could not open the file picker: no picker"])

    def test_close_and_restart_closes_the_window(self):
        path = self._backup()
        dialog = self._dialog()
        dialog.open()
        dialog._ask_restore(path)
        asyncio.run(_find_button(self.page.open_dialogs[-1], "Restore").on_click(None))

        restart = self.page.open_dialogs[-1]
        _find_button(restart, "Close and restart").on_click(None)

        self.page.window.close.assert_called_once()


class TestDialogFooter(_BackupTestCase):
    def test_close_fires_on_close_once(self):
        closed: list[bool] = []
        dialog = self._dialog(on_close=lambda: closed.append(True))
        dialog.open()

        dialog._close()
        dialog._close()

        self.assertEqual(closed, [True])
        self.assertFalse(dialog.dialog.open)


class TestMainWiring(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmpdir.cleanup)
        self.home = Path(self._tmpdir.name)
        env = mock.patch.dict(os.environ, {"BUDGET_TRACKER_HOME": str(self.home)})
        env.start()
        self.addCleanup(env.stop)

    def _run_main(self, page, *, restore_message=None):
        from budget_tracker import main as shell

        order: list[str] = []

        def fake_apply():
            order.append("apply")
            return restore_message

        def fake_connect(_path):
            order.append("connect")
            return connect(":memory:")

        original_add = page.add
        original_show = page.show_dialog

        def recorded_add(*controls):
            order.append("add")
            original_add(*controls)

        def recorded_show(control):
            order.append("show")
            original_show(control)

        page.add = recorded_add
        page.show_dialog = recorded_show

        with mock.patch.object(shell, "apply_pending_restore", side_effect=fake_apply), \
                mock.patch.object(shell, "connect", side_effect=fake_connect):
            shell.main(page)
        return order

    def test_apply_pending_restore_runs_before_connect_and_notifies_after_add(self):
        page = _PageStub()
        message = "Restore applied; your restored data is in place."

        order = self._run_main(page, restore_message=message)

        self.assertEqual(order[:2], ["apply", "connect"])
        self.assertIn("add", order)
        self.assertIn(message, [bar.content.value for bar in page.snackbars])
        self.assertLess(order.index("add"), order.index("show"))

    def test_rail_trailing_row_includes_the_backup_button(self):
        page = _PageStub()

        self._run_main(page)

        [row] = page.added[0]
        rail = next(c for c in row.controls if isinstance(c, ft.NavigationRail))
        row = rail.trailing.controls[0]
        self.assertTrue(any(c.tooltip == "Backups" for c in row.controls))
        backup = next(c for c in row.controls if c.tooltip == "Backups")
        self.assertIsInstance(backup, ft.IconButton)
        self.assertEqual(backup.icon, ft.Icons.BACKUP)

    def test_main_starts_the_auto_backup_task(self):
        from budget_tracker import main as shell

        page = _PageStub()

        self._run_main(page)

        page.run_task.assert_called_once_with(shell.auto_backup_task)

    def test_disconnect_cancels_the_auto_backup_task_and_closes_the_repository(self):
        page = _PageStub()
        future = page.run_task.return_value

        with mock.patch.object(Repository, "close", autospec=True) as close:
            self._run_main(page)
            page.on_disconnect(None)

        future.cancel.assert_called_once()
        close.assert_called_once()


class TestAutoBackupTask(unittest.TestCase):
    def test_loop_creates_a_due_backup_then_sleeps_six_hours(self):
        from budget_tracker import main as shell

        with mock.patch.object(shell, "create_backup_if_due") as due, \
                mock.patch.object(
                    shell.asyncio,
                    "sleep",
                    new=mock.AsyncMock(side_effect=asyncio.CancelledError),
                ) as sleep:
            with self.assertRaises(asyncio.CancelledError):
                asyncio.run(shell.auto_backup_task())

        due.assert_called_once_with()
        sleep.assert_called_once_with(6 * 60 * 60)

    def test_a_failed_automatic_backup_is_swallowed_and_logged(self):
        from budget_tracker import main as shell

        with mock.patch.object(
            shell, "create_backup_if_due", side_effect=RuntimeError("disk full")
        ), mock.patch.object(
            shell.asyncio,
            "sleep",
            new=mock.AsyncMock(side_effect=asyncio.CancelledError),
        ), mock.patch.object(shell.log, "exception") as logged:
            with self.assertRaises(asyncio.CancelledError):
                asyncio.run(shell.auto_backup_task())

        logged.assert_called_once()


if __name__ == "__main__":
    unittest.main()
