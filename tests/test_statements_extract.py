import tempfile
import unittest
from pathlib import Path

from budget_tracker.statements.extract import sniff_kind
from budget_tracker.statements.model import StatementReadError


class TestSniffKind(unittest.TestCase):
    def test_sniff_kind_by_magic_bytes(self):
        with tempfile.TemporaryDirectory() as tmp:
            pdf = Path(tmp) / "renamed.jpg"; pdf.write_bytes(b"%PDF-1.7\n...")
            self.assertEqual(sniff_kind(pdf), "pdf")
            png = Path(tmp) / "x.pdf"; png.write_bytes(b"\x89PNG\r\n\x1a\n")
            self.assertEqual(sniff_kind(png), "image")
            bad = Path(tmp) / "x.txt"; bad.write_bytes(b"hello")
            with self.assertRaises(StatementReadError):
                sniff_kind(bad)
