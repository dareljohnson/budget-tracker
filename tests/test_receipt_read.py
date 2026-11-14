from datetime import date
from pathlib import Path
from unittest import mock
import unittest

from budget_tracker import receipts
from budget_tracker.receipts.errors import ReceiptOcrUnavailable, ReceiptReadError
from budget_tracker.statements.model import Word


class TestReadReceipt(unittest.TestCase):
    def test_ocr_unavailable_raises(self):
        with mock.patch.object(receipts, "ocr_available", return_value=False):
            with self.assertRaises(ReceiptOcrUnavailable):
                receipts.read_receipt(Path("x.png"))

    def test_pdf_reads_page_zero_only(self):
        words = [Word("SHOP", 0, 0), Word("TOTAL", 0, 20), Word("5.00", 100, 20)]
        with mock.patch.object(receipts, "ocr_available", return_value=True), \
             mock.patch.object(receipts, "sniff_kind", return_value="pdf"), \
             mock.patch.object(receipts, "render_pdf_page") as render, \
             mock.patch.object(receipts, "ocr_images", return_value=words):
            result = receipts.read_receipt(Path("x.pdf"), today=date(2026, 11, 14))
        render.assert_called_once_with(Path("x.pdf"), 0)
        self.assertEqual(result.total_cents, 500)

    def test_image_path_uses_first_page(self):
        with mock.patch.object(receipts, "ocr_available", return_value=True), \
             mock.patch.object(receipts, "sniff_kind", return_value="image"), \
             mock.patch.object(receipts, "load_image_pages", return_value=["page0"]), \
             mock.patch.object(receipts, "ocr_images", return_value=[]) as ocr:
            receipts.read_receipt(Path("x.png"), today=date(2026, 11, 14))
        ocr.assert_called_once_with(["page0"])

    def test_unreadable_file_raises_read_error(self):
        with mock.patch.object(receipts, "ocr_available", return_value=True), \
             mock.patch.object(receipts, "sniff_kind", side_effect=ValueError("bad")):
            with self.assertRaises(ReceiptReadError):
                receipts.read_receipt(Path("x.png"))
