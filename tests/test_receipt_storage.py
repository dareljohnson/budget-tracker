import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from budget_tracker.receipts import storage
from budget_tracker.receipts.errors import ReceiptReadError


class TestReceiptStorage(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmpdir.cleanup)
        self.tmp = self._tmpdir.name
        env = mock.patch.dict(os.environ, {"BUDGET_TRACKER_HOME": self.tmp})
        env.start()
        self.addCleanup(env.stop)

        self.png = Path(self.tmp) / "photo.png"
        self.png.write_bytes(b"\x89PNG\r\n\x1a\n" + b"data")
        self.jpg = Path(self.tmp) / "photo.jpg"
        self.jpg.write_bytes(b"\xff\xd8\xff" + b"data")
        self.pdf = Path(self.tmp) / "scan.pdf"
        self.pdf.write_bytes(b"%PDF-1.4\n")

    def _stored_path(self, stored):
        return Path(self.tmp) / stored

    def test_store_receipt_uses_uuid_and_detected_extension(self):
        stored = storage.store_receipt(self.png)
        self.assertRegex(stored, r"^receipts/[0-9a-f]{32}\.png$")
        self.assertEqual(self._stored_path(stored).read_bytes(), self.png.read_bytes())

    def test_store_jpeg_and_pdf_extensions(self):
        self.assertTrue(storage.store_receipt(self.jpg).endswith(".jpg"))
        self.assertTrue(storage.store_receipt(self.pdf).endswith(".pdf"))

    def test_source_is_not_modified(self):
        before = self.png.read_bytes()
        storage.store_receipt(self.png)
        self.assertEqual(self.png.read_bytes(), before)

    def test_extension_comes_from_magic_bytes_not_file_name(self):
        disguised = Path(self.tmp) / "invoice.pdf"
        disguised.write_bytes(b"\x89PNG\r\n\x1a\n" + b"data")
        self.assertTrue(storage.store_receipt(disguised).endswith(".png"))

    def test_store_rejects_unknown_file_type(self):
        unknown = Path(self.tmp) / "notes.txt"
        unknown.write_bytes(b"just some text")
        with self.assertRaises(ReceiptReadError):
            storage.store_receipt(unknown)

    def test_resolve_returns_path_under_data_dir(self):
        self.assertEqual(storage.resolve_receipt("receipts/x.png"), Path(self.tmp) / "receipts" / "x.png")

    def test_resolve_rejects_path_traversal(self):
        with self.assertRaises(ReceiptReadError):
            storage.resolve_receipt("../secrets.txt")
        with self.assertRaises(ReceiptReadError):
            storage.resolve_receipt(str(Path(self.tmp) / "receipts" / "x.png"))

    def test_resolve_rejects_relative_paths_outside_receipts(self):
        for bad in ("budget_tracker.db", "other/x.png", "/Windows/win.ini", "\\Windows\\win.ini"):
            with self.subTest(bad=bad), self.assertRaises(ReceiptReadError):
                storage.resolve_receipt(bad)

    def test_delete_receipt_file_is_safe_when_missing(self):
        storage.delete_receipt_file("receipts/missing.png")
        storage.delete_receipt_file(None)
        storage.delete_receipt_file("a\x00b")

    def test_store_failure_leaves_no_orphan(self):
        def failing_copy(source, target):
            Path(target).write_bytes(b"partial")
            raise OSError("disk full")

        with mock.patch.object(storage.shutil, "copyfile", side_effect=failing_copy):
            with self.assertRaises(OSError):
                storage.store_receipt(self.png)
        self.assertEqual(list(storage.receipts_dir().iterdir()), [])

    def test_delete_receipt_file_removes_existing_file(self):
        stored = storage.store_receipt(self.png)
        storage.delete_receipt_file(stored)
        self.assertFalse(self._stored_path(stored).exists())

    def test_open_receipt_uses_os_opener(self):
        stored = storage.store_receipt(self.png)
        with mock.patch.object(storage.os, "startfile", create=True) as start, \
             mock.patch.object(storage, "IS_WINDOWS", True):
            storage.open_receipt(stored)
        start.assert_called_once_with(str(self._stored_path(stored)))

    def test_open_receipt_rejects_missing_file(self):
        with self.assertRaises(FileNotFoundError):
            storage.open_receipt("receipts/missing.png")

    def test_open_receipt_reports_a_missing_launcher(self):
        stored = storage.store_receipt(self.png)
        with mock.patch.object(storage, "IS_WINDOWS", False), \
             mock.patch.object(storage.sys, "platform", "linux"), \
             mock.patch.object(
                 storage.subprocess, "Popen", side_effect=FileNotFoundError("xdg-open")
             ):
            with self.assertRaises(ReceiptReadError) as caught:
                storage.open_receipt(stored)
        self.assertEqual(str(caught.exception), "Could not start the default application")
