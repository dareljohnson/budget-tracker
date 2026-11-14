import json
import os
import sqlite3
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock
from uuid import uuid4

from budget_tracker.config import db_path
from budget_tracker.db import MIGRATIONS, connect, schema_version

CHECKING = "(SELECT id FROM accounts WHERE name = 'Checking')"
GROCERIES = "(SELECT id FROM categories WHERE name = 'Groceries')"


def insert_transaction(conn, description: str, amount: int = 1234):
    conn.execute(
        "INSERT INTO transactions (amount_cents, type, account_id, category_id, occurred_on, description, source) "
        f"VALUES (?, 'expense', {CHECKING}, {GROCERIES}, '2026-01-02', ?, 'manual')",
        (amount, description),
    )
    conn.commit()


class BackupTestCase(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmpdir.cleanup)
        self.home = Path(self._tmpdir.name)
        env = mock.patch.dict(os.environ, {"BUDGET_TRACKER_HOME": str(self.home)})
        env.start()
        self.addCleanup(env.stop)
        self.data = self.home
        self.backups = self.data / "backups"

    # -- helpers ---------------------------------------------------------

    def make_live_db(self, description: str = "current row", amount: int = 1234):
        conn = connect(db_path())
        insert_transaction(conn, description, amount)
        conn.close()

    def make_receipt(self, name: str = "receipt.png", data: bytes = b"\x89PNG\r\n\x1a\ndata"):
        folder = self.data / "receipts"
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / name
        path.write_bytes(data)
        return path

    def make_db_bytes(self, description: str = "restored row", amount: int = 4321) -> bytes:
        path = self.data / f"fixture-{uuid4().hex}.db"
        conn = connect(path)
        insert_transaction(conn, description, amount)
        conn.close()
        payload = path.read_bytes()
        path.unlink()
        return payload

    def make_db_bytes_with_version(self, version: int, description: str = "restored row") -> bytes:
        path = self.data / f"fixture-{uuid4().hex}.db"
        conn = connect(path)
        insert_transaction(conn, description)
        conn.execute(f"PRAGMA user_version = {version}")
        conn.commit()
        conn.close()
        payload = path.read_bytes()
        path.unlink()
        return payload

    def manifest_bytes(self, **overrides) -> bytes:
        data = {
            "format": 1,
            "created_at": "2026-01-02T03:04:05",
            "reason": "manual",
            "app_version": "1.5.1",
            "schema_version": len(MIGRATIONS),
            "transaction_count": 1,
            "receipt_count": 1,
        }
        data.update(overrides)
        return json.dumps(data).encode("utf-8")

    def make_zip(self, members: dict[str, bytes], name: str | None = None) -> Path:
        path = self.data / (name or f"upload-{uuid4().hex}.zip")
        with zipfile.ZipFile(path, "w") as archive:
            for member, payload in members.items():
                archive.writestr(member, payload)
        return path

    def valid_restore_zip(self, description: str = "restored row", extra: dict | None = None) -> Path:
        members = {
            "manifest.json": self.manifest_bytes(),
            "budget_tracker.db": self.make_db_bytes(description),
            "receipts/new.png": b"\x89PNG\r\n\x1a\nnew",
        }
        members.update(extra or {})
        return self.make_zip(members)

    def extract_db(self, archive_path: Path, member: str = "budget_tracker.db") -> sqlite3.Connection:
        target = self.data / f"extract-{uuid4().hex}.db"
        with zipfile.ZipFile(archive_path) as archive:
            target.write_bytes(archive.read(member))
        return sqlite3.connect(target)


class TestCreateBackup(BackupTestCase):
    def test_backup_contains_manifest_database_and_receipt(self):
        from budget_tracker import backups

        self.make_live_db("kept row")
        self.make_receipt("abc.png")

        path = backups.create_backup(kind="manual", reason="unit test")

        self.assertTrue(path.is_file())
        self.assertEqual(path.suffix, ".zip")
        self.assertTrue(path.name.startswith("manual-"))
        with zipfile.ZipFile(path) as archive:
            names = set(archive.namelist())
        self.assertIn("manifest.json", names)
        self.assertIn("budget_tracker.db", names)
        self.assertIn("receipts/abc.png", names)

        copied = self.extract_db(path)
        try:
            rows = copied.execute("SELECT description FROM transactions").fetchall()
        finally:
            copied.close()
        self.assertEqual([row[0] for row in rows], ["kept row"])

        with zipfile.ZipFile(path) as archive:
            manifest = json.loads(archive.read("manifest.json"))
        self.assertEqual(manifest["format"], backups.BACKUP_FORMAT)
        self.assertEqual(manifest["reason"], "unit test")
        self.assertEqual(manifest["schema_version"], len(MIGRATIONS))
        self.assertEqual(manifest["transaction_count"], 1)
        self.assertEqual(manifest["receipt_count"], 1)

        status = backups.read_status()
        self.assertTrue(status["ok"])

    def test_backup_includes_committed_wal_content(self):
        from budget_tracker import backups

        conn = connect(db_path())
        self.addCleanup(conn.close)
        insert_transaction(conn, "wal row")

        path = backups.create_backup(kind="manual", reason="wal")

        copied = self.extract_db(path)
        try:
            rows = copied.execute("SELECT description FROM transactions").fetchall()
        finally:
            copied.close()
        self.assertEqual([row[0] for row in rows], ["wal row"])

    def test_create_backup_if_due_is_once_per_day(self):
        from budget_tracker import backups

        self.make_live_db()

        first = backups.create_backup_if_due()
        self.assertIsNotNone(first)
        self.assertTrue(first.name.startswith("auto-"))
        self.assertIsNone(backups.create_backup_if_due())

        # An automatic snapshot older than 24 hours is due again.
        old = 1_000_000
        os.utime(first, (old, old))
        self.assertIsNotNone(backups.create_backup_if_due())

    def test_prune_retention(self):
        from budget_tracker import backups

        folder = backups.backups_dir()
        folder.mkdir(parents=True, exist_ok=True)

        def touch(path: Path, stamp: int):
            path.write_bytes(b"x")
            os.utime(path, (stamp, stamp))

        base = 1_700_000_000
        autos = []
        for index in range(16):
            path = folder / f"auto-202601{index:02d}-000000.zip"
            touch(path, base + index)
            autos.append(path)
        migrations = []
        for index in range(5):
            path = folder / f"pre-migration-v9-202601{index:02d}-000000.db"
            touch(path, base + index)
            migrations.append(path)
        replaced = []
        for index in range(4):
            path = folder / f"replaced-202601{index:02d}-000000"
            path.mkdir()
            (path / "budget_tracker.db").write_bytes(b"x")
            os.utime(path, (base + index, base + index))
            replaced.append(path)
        manuals = []
        for index in range(2):
            path = folder / f"manual-202601{index:02d}-000000.zip"
            touch(path, base + index)
            manuals.append(path)
        pre_restores = []
        for index in range(2):
            path = folder / f"pre-restore-202601{index:02d}-000000.zip"
            touch(path, base + index)
            pre_restores.append(path)

        backups.prune_backups()

        remaining_autos = sorted(p.name for p in folder.glob("auto-*.zip"))
        self.assertEqual(len(remaining_autos), 14)
        self.assertEqual(
            remaining_autos,
            sorted(path.name for path in autos[-14:]),
        )
        remaining_migrations = sorted(p.name for p in folder.glob("pre-migration-*.db"))
        self.assertEqual(remaining_migrations, sorted(path.name for path in migrations[-3:]))
        remaining_replaced = sorted(p.name for p in folder.glob("replaced-*") if p.is_dir())
        self.assertEqual(remaining_replaced, sorted(path.name for path in replaced[-2:]))
        for path in manuals + pre_restores:
            self.assertTrue(path.exists(), path.name)

    def test_unreadable_copied_database_fails_backup(self):
        from budget_tracker import backups

        self.make_live_db()
        folder = backups.backups_dir()

        def write_garbage(target):
            Path(target).write_bytes(b"this is not a database")

        with mock.patch.object(backups, "_copy_database", side_effect=write_garbage):
            with self.assertRaises(backups.BackupError):
                backups.create_backup(kind="manual", reason="broken copy")

        self.assertEqual(list(folder.glob("*.zip")), [])
        self.assertEqual(list(folder.rglob("*.tmp")), [])
        self.assertFalse(backups.read_status()["ok"])

    def test_due_is_rechecked_inside_the_create_path(self):
        from budget_tracker import backups

        self.make_live_db()
        folder = backups.backups_dir()

        # The outer check passes, but the re-check under the lock says another caller
        # has just taken the automatic snapshot.
        with mock.patch.object(backups, "_auto_due", side_effect=[True, False]):
            self.assertIsNone(backups.create_backup_if_due())

        self.assertEqual(list(folder.glob("auto-*.zip")), [])

    def test_failed_copy_leaves_no_partial_and_records_status(self):
        from budget_tracker import backups

        self.make_live_db()
        folder = backups.backups_dir()

        with mock.patch.object(
            backups, "_copy_database", side_effect=sqlite3.DatabaseError("copy failed")
        ):
            with self.assertRaises(backups.BackupError):
                backups.create_backup(kind="manual", reason="broken")

        self.assertEqual(list(folder.glob("*.zip")), [])
        self.assertEqual(list(folder.rglob("*.tmp")), [])
        status = backups.read_status()
        self.assertFalse(status["ok"])
        self.assertEqual(status["kind"], "manual")
        self.assertIn("copy failed", status["message"])


class TestListBackups(BackupTestCase):
    def test_lists_newest_first_and_falls_back_on_damaged_zip(self):
        from budget_tracker import backups

        self.make_live_db()
        good = backups.create_backup(kind="manual", reason="unit test")
        folder = backups.backups_dir()
        damaged = folder / "manual-20200101-000000.zip"
        damaged.write_bytes(b"not a zip")
        old = 1_000_000
        os.utime(damaged, (old, old))

        listed = backups.list_backups()

        self.assertEqual(len(listed), 2)
        self.assertEqual(listed[0].path, good)
        self.assertEqual(listed[0].reason, "unit test")
        self.assertEqual(listed[0].schema_version, len(MIGRATIONS))
        self.assertEqual(listed[1].path, damaged)
        self.assertEqual(listed[1].kind, "manual")
        self.assertEqual(listed[1].schema_version, 0)
        self.assertGreater(listed[0].size_bytes, 0)


class TestStageRestore(BackupTestCase):
    def test_rejects_unsafe_members(self):
        from budget_tracker import backups

        database = self.make_db_bytes()
        manifest = self.manifest_bytes()
        for unsafe in ("../evil.txt", "/absolute.txt", "C:/evil.txt", "receipts\\..\\evil.txt"):
            with self.subTest(unsafe=unsafe):
                archive = self.make_zip(
                    {
                        "manifest.json": manifest,
                        "budget_tracker.db": database,
                        unsafe: b"evil",
                    }
                )
                with self.assertRaises(backups.BackupError):
                    backups.stage_restore(archive)
                self.assertFalse((self.data / "restore-pending.json").exists())

    def test_rejects_missing_database(self):
        from budget_tracker import backups

        archive = self.make_zip({"manifest.json": self.manifest_bytes()})
        with self.assertRaises(backups.BackupError):
            backups.stage_restore(archive)

    def test_validation_failure_records_a_failed_status(self):
        from budget_tracker import backups

        archive = self.make_zip({"manifest.json": self.manifest_bytes()})
        with self.assertRaises(backups.BackupError) as raised:
            backups.stage_restore(archive)

        status = backups.read_status()
        self.assertEqual(status["kind"], "restore")
        self.assertFalse(status["ok"])
        self.assertEqual(status["message"], str(raised.exception))
        self.assertTrue(status["message"])

    def test_rejects_missing_or_wrong_manifest(self):
        from budget_tracker import backups

        database = self.make_db_bytes()
        for members in (
            {"budget_tracker.db": database},
            {"manifest.json": b"not json", "budget_tracker.db": database},
            {"manifest.json": self.manifest_bytes(format=2), "budget_tracker.db": database},
        ):
            with self.subTest(members=sorted(members)):
                archive = self.make_zip(members)
                with self.assertRaises(backups.BackupError):
                    backups.stage_restore(archive)

    def test_rejects_newer_schema(self):
        from budget_tracker import backups

        archive = self.make_zip(
            {
                "manifest.json": self.manifest_bytes(schema_version=len(MIGRATIONS) + 1),
                "budget_tracker.db": self.make_db_bytes_with_version(len(MIGRATIONS) + 1),
            }
        )
        with self.assertRaises(backups.BackupError):
            backups.stage_restore(archive)

    def test_rejects_database_failing_integrity_check(self):
        from budget_tracker import backups

        archive = self.make_zip(
            {
                "manifest.json": self.manifest_bytes(),
                "budget_tracker.db": b"this is not a database",
            }
        )
        with self.assertRaises(backups.BackupError):
            backups.stage_restore(archive)

    def test_extracts_safely_and_writes_marker(self):
        from budget_tracker import backups

        archive = self.valid_restore_zip(
            extra={"notes.txt": b"ignored", "receipts\\backslash.png": b"backslash"}
        )

        staging = backups.stage_restore(archive)

        self.assertTrue(staging.is_dir())
        self.assertTrue((staging / "manifest.json").is_file())
        self.assertTrue((staging / "budget_tracker.db").is_file())
        self.assertTrue((staging / "receipts" / "new.png").is_file())
        self.assertTrue((staging / "receipts" / "backslash.png").is_file())
        self.assertFalse((staging / "notes.txt").exists())

        marker = backups.pending_restore()
        self.assertEqual(marker["state"], "staged")
        self.assertEqual(Path(marker["staging"]), staging)
        self.assertIn("created_at", marker)
        self.assertIn("step", marker)


class TestApplyPendingRestore(BackupTestCase):
    def test_replaces_database_wal_shm_and_receipts(self):
        from budget_tracker import backups

        self.make_live_db("current row")
        self.make_receipt("old.png")
        db_file = db_path()

        archive = self.valid_restore_zip("restored row")
        backups.stage_restore(archive)

        # The pre-restore snapshot normally checkpoints and clears a stale WAL. Plant a
        # set just after it, simulating a WAL left by another process, so the move can be
        # observed as a set.
        original = backups._create_backup

        def create_then_leave_wal(*, kind, reason):
            result = original(kind=kind, reason=reason)
            Path(str(db_file) + "-wal").write_bytes(b"stale wal")
            Path(str(db_file) + "-shm").write_bytes(b"stale shm")
            return result

        with mock.patch.object(backups, "_create_backup", side_effect=create_then_leave_wal):
            message = backups.apply_pending_restore()

        self.assertIn("restored", message.lower())
        self.assertIsNone(backups.pending_restore())
        self.assertFalse((self.data / "restore-staging").exists())

        conn = sqlite3.connect(db_path())
        try:
            rows = conn.execute("SELECT description FROM transactions").fetchall()
        finally:
            conn.close()
        self.assertEqual([row[0] for row in rows], ["restored row"])

        receipts = self.data / "receipts"
        self.assertTrue((receipts / "new.png").is_file())
        self.assertFalse((receipts / "old.png").exists())

        replaced_dirs = list(backups.backups_dir().glob("replaced-*"))
        self.assertEqual(len(replaced_dirs), 1)
        replaced = replaced_dirs[0]
        self.assertTrue((replaced / "budget_tracker.db").is_file())
        self.assertTrue((replaced / "budget_tracker.db-wal").is_file())
        self.assertTrue((replaced / "budget_tracker.db-shm").is_file())
        self.assertTrue((replaced / "receipts" / "old.png").is_file())

        self.assertEqual(len(list(backups.backups_dir().glob("pre-restore-*.zip"))), 1)

    def test_resumes_crash_after_moving_current_files(self):
        from budget_tracker import backups

        folder = backups.backups_dir()
        replaced = folder / "replaced-20260101-000000"
        replaced.mkdir(parents=True)
        old_db = sqlite3.connect(replaced / "budget_tracker.db")
        try:
            old_db.execute("CREATE TABLE marker (value TEXT)")
            old_db.execute("INSERT INTO marker VALUES ('original')")
            old_db.commit()
        finally:
            old_db.close()

        staging = self.data / "restore-staging"
        (staging / "receipts").mkdir(parents=True)
        (staging / "budget_tracker.db").write_bytes(self.make_db_bytes("resumed row"))
        (staging / "receipts" / "new.png").write_bytes(b"\x89PNG\r\n\x1a\nnew")
        (staging / "manifest.json").write_bytes(self.manifest_bytes())
        marker = {
            "state": "applying",
            "step": "moved-current",
            "staging": str(staging),
            "replaced": str(replaced),
            "created_at": "2026-01-02T03:04:05",
        }
        (self.data / "restore-pending.json").write_text(json.dumps(marker), encoding="utf-8")

        message = backups.apply_pending_restore()

        self.assertIn("restored", message.lower())
        self.assertIsNone(backups.pending_restore())
        conn = sqlite3.connect(db_path())
        try:
            rows = conn.execute("SELECT description FROM transactions").fetchall()
        finally:
            conn.close()
        self.assertEqual([row[0] for row in rows], ["resumed row"])
        # The previously moved copy must still exist; neither copy was lost.
        self.assertTrue((replaced / "budget_tracker.db").is_file())

    def test_pre_restore_failure_keeps_current_data(self):
        from budget_tracker import backups

        self.make_live_db("current row")
        backups.stage_restore(self.valid_restore_zip("restored row"))

        with mock.patch.object(
            backups, "_create_backup", side_effect=backups.BackupError("no room")
        ):
            message = backups.apply_pending_restore()

        self.assertIn("no room", message)
        marker = backups.pending_restore()
        self.assertEqual(marker["state"], "failed")
        conn = sqlite3.connect(db_path())
        try:
            rows = conn.execute("SELECT description FROM transactions").fetchall()
        finally:
            conn.close()
        self.assertEqual([row[0] for row in rows], ["current row"])

    def test_move_failure_never_deletes_the_live_database(self):
        from budget_tracker import backups

        self.make_live_db("current row")
        self.make_receipt("old.png")
        backups.stage_restore(self.valid_restore_zip("restored row"))
        real_replace = os.replace

        def fail_first_move(source, target, *args, **kwargs):
            if Path(target).name == "budget_tracker.db" and "replaced-" in str(target):
                raise OSError("file is locked")
            return real_replace(source, target, *args, **kwargs)

        with mock.patch.object(backups.os, "replace", side_effect=fail_first_move):
            message = backups.apply_pending_restore()

        self.assertIn("locked", message)
        marker = backups.pending_restore()
        self.assertEqual(marker["state"], "failed")
        self.assertFalse((self.data / "restore-staging").exists())

        conn = sqlite3.connect(db_path())
        try:
            rows = conn.execute("SELECT description FROM transactions").fetchall()
        finally:
            conn.close()
        self.assertEqual([row[0] for row in rows], ["current row"])
        self.assertTrue((self.data / "receipts" / "old.png").is_file())

    def test_moving_current_without_recorded_folder_uses_a_fresh_folder(self):
        from budget_tracker import backups

        folder = backups.backups_dir()
        older = folder / "replaced-20200101-000000"
        older.mkdir()
        (older / "budget_tracker.db").write_bytes(b"older copy")

        self.make_live_db("current row")
        staging = self.data / "restore-staging"
        (staging / "receipts").mkdir(parents=True)
        (staging / "budget_tracker.db").write_bytes(self.make_db_bytes("restored row"))
        (staging / "manifest.json").write_bytes(self.manifest_bytes())
        (self.data / "restore-pending.json").write_text(
            json.dumps(
                {
                    "state": "applying",
                    "step": "moving-current",
                    "staging": str(staging),
                    "created_at": "2026-01-02T03:04:05",
                }
            ),
            encoding="utf-8",
        )

        message = backups.apply_pending_restore()

        self.assertIn("restored", message.lower())
        replaced_dirs = [path for path in folder.glob("replaced-*") if path.is_dir()]
        self.assertEqual(len(replaced_dirs), 2)
        self.assertIn(older, replaced_dirs)
        self.assertEqual((older / "budget_tracker.db").read_bytes(), b"older copy")
        fresh = next(path for path in replaced_dirs if path != older)
        self.assertTrue((fresh / "budget_tracker.db").is_file())
        conn = sqlite3.connect(db_path())
        try:
            rows = conn.execute("SELECT description FROM transactions").fetchall()
        finally:
            conn.close()
        self.assertEqual([row[0] for row in rows], ["restored row"])

    def test_missing_staging_while_applying_restores_moved_database(self):
        from budget_tracker import backups

        self.make_live_db("current row")
        replaced = backups.backups_dir() / "replaced-20260101-000000"
        replaced.mkdir()
        os.replace(db_path(), replaced / "budget_tracker.db")
        (self.data / "restore-pending.json").write_text(
            json.dumps(
                {
                    "state": "applying",
                    "step": "moved-current",
                    "staging": str(self.data / "gone"),
                    "replaced": str(replaced),
                    "created_at": "2026-01-02T03:04:05",
                }
            ),
            encoding="utf-8",
        )

        message = backups.apply_pending_restore()

        self.assertIn("restored", message.lower())
        self.assertIsNone(backups.pending_restore())
        self.assertTrue(db_path().is_file())
        conn = sqlite3.connect(db_path())
        try:
            rows = conn.execute("SELECT description FROM transactions").fetchall()
        finally:
            conn.close()
        self.assertEqual([row[0] for row in rows], ["current row"])

    def test_install_database_failure_rolls_back(self):
        from budget_tracker import backups

        self.make_live_db("current row")
        self.make_receipt("old.png")
        backups.stage_restore(self.valid_restore_zip("restored row"))

        with mock.patch.object(
            backups, "_install_database", side_effect=OSError("database install failed")
        ):
            message = backups.apply_pending_restore()

        self.assertIn("database install failed", message)
        self.assertEqual(backups.pending_restore()["state"], "failed")
        self.assertFalse((self.data / "restore-staging").exists())
        conn = sqlite3.connect(db_path())
        try:
            rows = conn.execute("SELECT description FROM transactions").fetchall()
        finally:
            conn.close()
        self.assertEqual([row[0] for row in rows], ["current row"])
        self.assertTrue((self.data / "receipts" / "old.png").is_file())

    def test_install_receipts_failure_rolls_back(self):
        from budget_tracker import backups

        self.make_live_db("current row")
        self.make_receipt("old.png")
        backups.stage_restore(self.valid_restore_zip("restored row"))

        with mock.patch.object(
            backups, "_install_receipts", side_effect=OSError("receipt install failed")
        ):
            message = backups.apply_pending_restore()

        self.assertIn("receipt install failed", message)
        self.assertEqual(backups.pending_restore()["state"], "failed")
        conn = sqlite3.connect(db_path())
        try:
            rows = conn.execute("SELECT description FROM transactions").fetchall()
        finally:
            conn.close()
        self.assertEqual([row[0] for row in rows], ["current row"])
        self.assertTrue((self.data / "receipts" / "old.png").is_file())
        self.assertFalse((self.data / "receipts" / "new.png").exists())

    def test_failed_marker_clears_and_keeps_current_data(self):
        from budget_tracker import backups

        self.make_live_db("current row")
        (self.data / "restore-pending.json").write_text(
            json.dumps({"state": "failed", "message": "disk on fire", "step": "moving-current"}),
            encoding="utf-8",
        )

        message = backups.apply_pending_restore()

        self.assertIn("disk on fire", message)
        self.assertIsNone(backups.pending_restore())
        conn = sqlite3.connect(db_path())
        try:
            rows = conn.execute("SELECT description FROM transactions").fetchall()
        finally:
            conn.close()
        self.assertEqual([row[0] for row in rows], ["current row"])

    def test_missing_staging_clears_marker(self):
        from budget_tracker import backups

        self.make_live_db("current row")
        (self.data / "restore-pending.json").write_text(
            json.dumps(
                {
                    "state": "staged",
                    "step": "staged",
                    "staging": str(self.data / "gone"),
                    "created_at": "2026-01-02T03:04:05",
                }
            ),
            encoding="utf-8",
        )

        message = backups.apply_pending_restore()

        self.assertIsNotNone(message)
        self.assertIsNone(backups.pending_restore())
        conn = sqlite3.connect(db_path())
        try:
            rows = conn.execute("SELECT description FROM transactions").fetchall()
        finally:
            conn.close()
        self.assertEqual([row[0] for row in rows], ["current row"])

    def test_rollback_keeps_the_saved_set_when_the_database_cannot_be_restored(self):
        from budget_tracker import backups

        self.make_live_db("current row")
        self.make_receipt("old.png")
        db_file = db_path()
        backups.stage_restore(self.valid_restore_zip("restored row"))

        original = backups._create_backup

        def create_then_leave_wal(*, kind, reason):
            result = original(kind=kind, reason=reason)
            Path(str(db_file) + "-wal").write_bytes(b"old wal")
            Path(str(db_file) + "-shm").write_bytes(b"old shm")
            return result

        real_replace = os.replace

        def fail_database_rollback(source, target, *args, **kwargs):
            if Path(target) == Path(db_file) and "replaced-" in str(source):
                raise OSError("database rollback failed")
            return real_replace(source, target, *args, **kwargs)

        with mock.patch.object(
            backups, "_create_backup", side_effect=create_then_leave_wal
        ), mock.patch.object(
            backups, "_install_receipts", side_effect=OSError("receipt install failed")
        ), mock.patch.object(backups.os, "replace", side_effect=fail_database_rollback):
            message = backups.apply_pending_restore()

        self.assertIn("receipt install failed", message)
        self.assertIn("previous database could not be restored", message)
        marker = backups.pending_restore()
        self.assertEqual(marker["state"], "failed")
        self.assertIn("previous database could not be restored", marker["message"])

        # The restored database is live and must not be paired with the old WAL/SHM.
        conn = sqlite3.connect(db_path())
        try:
            rows = conn.execute("SELECT description FROM transactions").fetchall()
        finally:
            conn.close()
        self.assertEqual([row[0] for row in rows], ["restored row"])
        self.assertFalse(Path(str(db_file) + "-wal").exists())
        self.assertFalse(Path(str(db_file) + "-shm").exists())

        # The entire saved set stays available for manual recovery.
        [replaced] = backups.backups_dir().glob("replaced-*")
        self.assertTrue((replaced / "budget_tracker.db").is_file())
        self.assertTrue((replaced / "budget_tracker.db-wal").is_file())
        self.assertTrue((replaced / "budget_tracker.db-shm").is_file())
        self.assertTrue((replaced / "receipts" / "old.png").is_file())

    def test_unwritable_marker_returns_a_failure_message_instead_of_crashing(self):
        from budget_tracker import backups

        self.make_live_db("current row")
        staging = self.data / "restore-staging"
        (staging / "receipts").mkdir(parents=True)
        (staging / "budget_tracker.db").write_bytes(self.make_db_bytes("restored row"))
        (self.data / "restore-pending.json").write_text(
            json.dumps(
                {
                    "state": "applying",
                    "step": "moving-current",
                    "staging": str(staging),
                    "created_at": "2026-01-02T03:04:05",
                }
            ),
            encoding="utf-8",
        )

        with mock.patch.object(
            backups, "_write_marker", side_effect=OSError("marker is read-only")
        ):
            message = backups.apply_pending_restore()

        self.assertIn("marker is read-only", message)
        conn = sqlite3.connect(db_path())
        try:
            rows = conn.execute("SELECT description FROM transactions").fetchall()
        finally:
            conn.close()
        self.assertEqual([row[0] for row in rows], ["current row"])
        self.assertFalse(staging.exists())


class TestPreMigrationCopy(BackupTestCase):
    def test_connect_copies_v9_before_migrating(self):
        path = self.data / "budget_tracker.db"
        conn = sqlite3.connect(path)
        for version, script in enumerate(MIGRATIONS[:9], start=1):
            conn.executescript(f"BEGIN;\n{script}\nPRAGMA user_version = {version};\nCOMMIT;")
        conn.close()

        migrated = connect(path)
        try:
            self.assertEqual(schema_version(migrated), len(MIGRATIONS))
        finally:
            migrated.close()

        copies = list((self.data / "backups").glob("pre-migration-v9-*.db"))
        self.assertEqual(len(copies), 1)
        copy = sqlite3.connect(copies[0])
        try:
            self.assertEqual(schema_version(copy), 9)
        finally:
            copy.close()
        status = json.loads((self.data / "backups" / "status.json").read_text(encoding="utf-8"))
        self.assertTrue(status["ok"])

    def test_memory_database_makes_no_copy(self):
        connect(":memory:").close()
        folder = self.data / "backups"
        self.assertEqual(list(folder.glob("pre-migration-*")) if folder.exists() else [], [])


if __name__ == "__main__":
    unittest.main()
