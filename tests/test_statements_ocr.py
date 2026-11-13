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
                    "budget_tracker.statements.render_pdf_page",
                    return_value=Image.new("L", (10, 10)),
                ),
                mock.patch(
                    "budget_tracker.statements.ocr_images",
                    return_value=generic_statement_words(),
                ),
            ):
                stmt = load_statement(pdf)
        self.assertEqual(stmt.source, "ocr")


class TestOcrHelpers(unittest.TestCase):
    """Resource bounds and token repair are engine-free, so these tests always run."""

    def test_normalize_amount_token_leaves_dotted_dates(self):
        self.assertEqual(normalize_amount_token("2024.01.15"), "2024.01.15")

    def test_image_pixel_budget_rejects_huge_image(self):
        from budget_tracker.statements import extract

        with self.assertRaises(extract.StatementReadError):
            extract._check_image_pixels(20_000, 20_000)

    def test_render_scale_is_clamped(self):
        from budget_tracker.statements import extract

        scale = extract._render_scale(2_000, 10_000, 300)
        self.assertLessEqual(10_000 * scale, extract.MAX_RENDER_LONG_EDGE)

    def test_sparse_page_ocr_obeys_page_cap(self):
        from budget_tracker.statements import _pdf_words

        pages: list[list[Word]] = [[] for _ in range(21)]
        stmt, used = _pdf_words(Path("x.pdf"), pages)
        self.assertFalse(used)
        self.assertEqual(stmt, [])

    def test_render_pdf_pages_refuses_too_many_pages(self):
        from budget_tracker.statements import extract

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "long.pdf"
            document = FPDF(unit="pt", format="letter"); document.set_font("Helvetica", size=12)
            for _ in range(21):
                document.add_page(); document.text(50, 50, "x")
            document.output(str(path))
            with self.assertRaises(extract.StatementReadError):
                extract.render_pdf_pages(path)


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

    def test_photo_with_wrong_dpi_metadata_still_imports(self):
        with tempfile.TemporaryDirectory() as tmp:
            pdf = Path(tmp) / "stmt.pdf"; make_generic_statement_pdf(pdf)
            png = Path(tmp) / "photo.png"
            render_pdf_pages(pdf)[0].save(png, dpi=(72, 72))  # camera-style metadata
            stmt = load_statement(png)
        self.assertEqual(stmt.source, "ocr")
        self.assertEqual(stmt.ending_cents, 3_167_869)
