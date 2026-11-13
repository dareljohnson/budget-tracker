import tempfile
import unittest
from pathlib import Path

from PIL import Image

from budget_tracker.statements import extract
from budget_tracker.statements.extract import sniff_kind
from budget_tracker.statements.model import StatementReadError


class TestEffectiveDpi(unittest.TestCase):
    def test_applies_the_resize_factor_before_the_floor(self):
        dpi = extract._effective_dpi(600, 6600, 4000)
        self.assertAlmostEqual(dpi, 600 * 4000 / 6600, places=6)
        # A 4000 px long edge at the effective DPI must be page-like (~792 pt), not ~480 pt.
        self.assertAlmostEqual(4000 * 72 / dpi, 792.0, places=6)

    def test_resized_scan_keeps_page_like_points(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "scan.png"
            Image.new("L", (6600, 5100), 255).save(path, dpi=(600, 600))
            pages = extract.load_image_pages(path)
            image = pages[0]
            try:
                self.assertEqual(max(image.size), extract._MAX_LONG_EDGE)
                dpi = extract.read_dpi(image.info)
                self.assertAlmostEqual(max(image.size) * 72 / dpi, 792.0, places=1)
            finally:
                image.close()


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
