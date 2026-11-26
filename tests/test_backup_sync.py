"""Copying backups into an iCloud Drive or Google Drive folder."""

import os
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

from budget_tracker import backups, config
from budget_tracker.backups import (
    BACKUP_SYNC_SETTING,
    sync_all_backups,
    sync_backup,
    sync_dir_from_setting,
)
from budget_tracker.db import connect
from budget_tracker.repository import Repository


class TestBackupSync(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmpdir.cleanup)
        self.home = Path(self._tmpdir.name)
        env = mock.patch.dict(os.environ, {"BUDGET_TRACKER_HOME": str(self.home)})
        env.start()
        self.addCleanup(env.stop)

    def _zip(self, name: str) -> Path:
        path = backups.backups_dir() / name
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr("manifest.json", "{}")
            archive.writestr("budget_tracker.db", b"database")
        return path

    def test_sync_copies_a_backup(self):
        source = self._zip("manual-20260101-000000.zip")
        target_dir = self.home / "cloud"
        target_dir.mkdir()
        target = sync_backup(source, target_dir)
        self.assertEqual(target.read_bytes(), source.read_bytes())
        status = backups.read_sync_status()
        self.assertTrue(status["ok"])
        self.assertEqual(status["file"], source.name)

    def test_sync_requires_the_folder_to_exist(self):
        source = self._zip("manual-20260101-000000.zip")
        missing = self.home / "not-mounted"
        with self.assertRaisesRegex(backups.BackupError, "not available"):
            sync_backup(source, missing)
        self.assertFalse(missing.exists())

    def test_sync_refuses_the_local_backups_folder(self):
        source = self._zip("manual-20260101-000000.zip")
        with self.assertRaisesRegex(backups.BackupError, "outside the local backups"):
            sync_backup(source, backups.backups_dir())

    def test_sync_all_copies_every_snapshot(self):
        self._zip("manual-20260101-000000.zip")
        self._zip("auto-20260102-000000.zip")
        target_dir = self.home / "cloud"
        target_dir.mkdir()
        copied, errors = sync_all_backups(target_dir)
        self.assertEqual((copied, errors), (2, []))
        self.assertEqual(
            sorted(path.name for path in target_dir.glob("*.zip")),
            ["auto-20260102-000000.zip", "manual-20260101-000000.zip"],
        )

    def test_sync_dir_setting_round_trip(self):
        repo = Repository(connect(":memory:"))
        self.addCleanup(repo.close)
        self.assertIsNone(sync_dir_from_setting(repo))
        repo.set_setting(BACKUP_SYNC_SETTING, str(self.home / "cloud"))
        self.assertEqual(sync_dir_from_setting(repo), self.home / "cloud")

    def test_sync_status_round_trip(self):
        backups.write_sync_status(False, "disk full", self.home / "cloud", "manual.zip")
        status = backups.read_sync_status()
        self.assertFalse(status["ok"])
        self.assertEqual(status["message"], "disk full")
        self.assertEqual(status["file"], "manual.zip")

    def test_suggested_folders_is_a_mapping(self):
        self.assertIsInstance(backups.suggest_sync_dirs(), dict)

    def test_suggested_folders_detect_onedrive(self):
        one = self.home / "OneDrive"
        one.mkdir()
        with mock.patch.dict(os.environ, {"OneDrive": str(one)}, clear=False):
            found = backups.suggest_sync_dirs()
        self.assertEqual(found.get("OneDrive"), one)

    def test_actual_data_dir_honors_the_home_override(self):
        self.assertEqual(config.actual_data_dir(), config.data_dir())

    @unittest.skipUnless(os.name == "nt", "Microsoft Store redirect is Windows-only")
    def test_actual_data_dir_finds_the_store_package_path(self):
        local = self.home / "LocalAppData"
        family = "PythonSoftwareFoundation.Python.3.13_abc123"
        package = (
            local / "Packages" / family / "LocalCache" / "Local" / "BudgetTracker"
        )
        package.mkdir(parents=True)
        base = (
            self.home
            / "WindowsApps"
            / "PythonSoftwareFoundation.Python.3.13_3.13.0_x64__abc123"
        )
        with mock.patch.dict(
            os.environ,
            {"LOCALAPPDATA": str(local), "BUDGET_TRACKER_HOME": ""},
            clear=False,
        ), mock.patch.object(config.sys, "base_prefix", str(base)):
            self.assertEqual(config.actual_data_dir(), package)
