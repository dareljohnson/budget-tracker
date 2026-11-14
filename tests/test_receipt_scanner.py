import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from budget_tracker.receipts import scanner
from budget_tracker.receipts.errors import ReceiptScanError


class TestScanner(unittest.TestCase):
    def test_unavailable_off_windows(self):
        with mock.patch.object(scanner, "IS_WINDOWS", False):
            self.assertFalse(scanner.scanner_available())

    def test_unavailable_without_powershell(self):
        with mock.patch.object(scanner, "IS_WINDOWS", True), \
             mock.patch.object(scanner, "_powershell", return_value=None):
            self.assertFalse(scanner.scanner_available())

    def test_script_uses_first_wia_scanner_and_png(self):
        script = scanner._scan_script()
        self.assertIn("WIA.DeviceManager", script)
        self.assertIn("$info.Type -eq 1", script)
        self.assertIn("Transfer", script)
        self.assertIn("$Destination", script)

    def test_scan_runs_hidden_powershell_and_returns_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "scan.png"
            def fake_run(args, **kwargs):
                target.write_bytes(b"\x89PNG\r\n\x1a\n")
                fake_run.args, fake_run.kwargs = args, kwargs
                return mock.Mock(returncode=0, stdout="", stderr="")
            with mock.patch.object(scanner, "IS_WINDOWS", True), \
                 mock.patch.object(scanner, "_powershell", return_value="powershell"), \
                 mock.patch.object(scanner.subprocess, "run", side_effect=fake_run):
                self.assertEqual(scanner.scan_receipt(target), target)
                self.assertEqual(list(Path(tmp).glob("*.ps1")), [])
        self.assertIn("-NoProfile", fake_run.args)
        self.assertIn("-NonInteractive", fake_run.args)
        self.assertIn("-ExecutionPolicy", fake_run.args)
        self.assertIn("Bypass", fake_run.args)
        self.assertEqual(fake_run.args[-1], str(target))
        self.assertEqual(fake_run.kwargs.get("timeout"), 60)
        self.assertTrue(fake_run.kwargs.get("capture_output"))
        self.assertTrue(fake_run.kwargs.get("text"))
        self.assertNotEqual(fake_run.kwargs.get("creationflags", 0), 0)

    def test_scan_without_scanner_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "scan.png"
            failed = mock.Mock(returncode=1, stdout="", stderr="  + FullyQualifiedErrorId : No scanner is connected.")
            with mock.patch.object(scanner, "IS_WINDOWS", True), \
                 mock.patch.object(scanner, "_powershell", return_value="powershell"), \
                 mock.patch.object(scanner.subprocess, "run", return_value=failed):
                with self.assertRaises(ReceiptScanError) as caught:
                    scanner.scan_receipt(target)
        self.assertEqual(str(caught.exception), "No scanner is connected.")

    def test_success_without_a_file_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "scan.png"
            ok = mock.Mock(returncode=0, stdout="", stderr="")
            with mock.patch.object(scanner, "IS_WINDOWS", True), \
                 mock.patch.object(scanner, "_powershell", return_value="powershell"), \
                 mock.patch.object(scanner.subprocess, "run", return_value=ok):
                with self.assertRaises(ReceiptScanError):
                    scanner.scan_receipt(target)

    def test_scan_timeout_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "scan.png"
            with mock.patch.object(scanner, "IS_WINDOWS", True), \
                 mock.patch.object(scanner, "_powershell", return_value="powershell"), \
                 mock.patch.object(scanner.subprocess, "run", side_effect=subprocess.TimeoutExpired("ps", 60)):
                with self.assertRaises(ReceiptScanError):
                    scanner.scan_receipt(target)
