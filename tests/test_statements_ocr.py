import tempfile
import unittest
from pathlib import Path
from unittest import mock

from fpdf import FPDF
from PIL import Image

from statement_fixture import (
    generic_statement_words,
    make_generic_statement_pdf,
    make_multipage_generic_statement_pdf,
)

from budget_tracker.statements import load_statement
from budget_tracker.statements.extract import PAGE_OFFSET, render_pdf_pages
from budget_tracker.statements.model import (
    StatementValidationError,
    UnsupportedStatementError,
    Word,
)
from budget_tracker.statements.numbers import parse_amount
from budget_tracker.statements.ocr import normalize_amount_token, ocr_available, ocr_document


class TestNormalizeAmountToken(unittest.TestCase):
    """Separator repair runs without the engine, so these tests always run."""

    def test_repairs_thousands_separator_read_as_decimal(self):
        self.assertEqual(normalize_amount_token("2.074.21"), "2074.21")

    def test_repairs_comma_grouped_amount(self):
        self.assertEqual(normalize_amount_token("3.240,000,000.00"), "3240000000.00")

    def test_removes_spaces_around_separators(self):
        self.assertEqual(normalize_amount_token("2 . 074 . 21"), "2074.21")

    def test_never_invents_a_missing_decimal(self):
        self.assertEqual(normalize_amount_token("70139"), "70139")
        self.assertIsNone(parse_amount(normalize_amount_token("70139")))

    def test_keeps_sign_and_currency_symbol(self):
        self.assertEqual(normalize_amount_token("-$6.242.34"), "-$6242.34")

    def test_drops_a_redundant_leading_plus(self):
        self.assertEqual(normalize_amount_token("+5.834.32"), "5834.32")

    def test_leaves_descriptive_text_alone(self):
        self.assertEqual(normalize_amount_token("Branch Deposit"), "Branch Deposit")


class TestOcrDocument(unittest.TestCase):
    """Page assembly is engine-free when the per-page recogniser is stubbed."""

    def test_later_pages_are_offset_into_reading_order(self):
        with tempfile.TemporaryDirectory() as tmp:
            pdf = Path(tmp) / "multi.pdf"; make_multipage_generic_statement_pdf(pdf)
            with mock.patch(
                "budget_tracker.statements.ocr.ocr_images",
                return_value=[Word("probe", 1.0, 5.0, 9.0)],
            ):
                words = ocr_document(pdf)
        self.assertEqual([word.top for word in words], [5.0, 5.0 + PAGE_OFFSET])

    def test_image_only_pdf_page_is_replaced_with_ocr(self):
        with tempfile.TemporaryDirectory() as tmp:
            pdf = Path(tmp) / "sparse.pdf"
            document = FPDF(unit="pt", format="letter"); document.add_page()
            document.set_font("Helvetica", size=12); document.text(50, 50, "Hello")
            document.output(str(pdf))
            with (
                mock.patch("budget_tracker.statements.ocr_available", return_value=True),
                mock.patch(
                    "budget_tracker.statements.render_pdf_pages",
                    return_value=[Image.new("L", (10, 10))],
                ),
                mock.patch(
                    "budget_tracker.statements.ocr_images",
                    return_value=generic_statement_words(),
                ),
            ):
                stmt = load_statement(pdf)
        self.assertEqual(stmt.source, "ocr")


@unittest.skipUnless(ocr_available(), "OCR engine not installed")
class TestOcrPath(unittest.TestCase):
    def test_rendered_mockup_statement_imports(self):
        with tempfile.TemporaryDirectory() as tmp:
            pdf = Path(tmp) / "stmt.pdf"; make_generic_statement_pdf(pdf)
            png = Path(tmp) / "stmt.png"
            render_pdf_pages(pdf)[0].save(png)
            stmt = load_statement(png)
        self.assertEqual(stmt.source, "ocr")
        self.assertEqual(stmt.ending_cents, 3_167_869)

    def test_corrupted_image_is_refused_not_imported(self):
        with tempfile.TemporaryDirectory() as tmp:
            png = Path(tmp) / "noise.png"
            Image.new("L", (800, 1000), 255).save(png)  # blank page
            with self.assertRaises((UnsupportedStatementError, StatementValidationError)):
                load_statement(png)
