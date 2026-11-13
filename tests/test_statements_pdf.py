import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest import mock

from fpdf import FPDF
from PIL import Image

from statement_fixture import (
    generic_statement_words,
    make_generic_statement_pdf,
    make_multipage_generic_statement_pdf,
    make_multipage_pdf,
    make_statement_pdf,
    make_statement_with_blank_page_pdf,
    make_unparsable_text_pdf,
)

from budget_tracker.statements import load_statement, parse_pdf
from budget_tracker.statements.model import (
    StatementReadError,
    StatementValidationError,
    UnsupportedStatementError,
)


class TestPdfAdapter(unittest.TestCase):
    def test_parses_generated_pdf(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "statement.pdf"
            make_statement_pdf(path)
            stmt = parse_pdf(path)
        self.assertEqual(stmt.ending_cents, 119_000)
        self.assertEqual(len(stmt.transactions), 4)
        self.assertEqual(stmt.transactions[0].occurred_on, date(2026, 9, 20))

    def test_multipage_statement_keeps_page_order(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "multi.pdf"
            make_multipage_pdf(path)
            stmt = parse_pdf(path)
        self.assertEqual(stmt.ending_cents, 119_000)
        self.assertEqual(len(stmt.transactions), 5)
        self.assertEqual(
            [t.occurred_on for t in stmt.transactions],
            [date(2026, 9, 20), date(2026, 10, 2), date(2026, 9, 25),
             date(2026, 9, 26), date(2026, 9, 30)],
        )

    def test_non_pdf_bytes_raise_statement_read_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "not-a-pdf.pdf"
            path.write_bytes(b"this is definitely not a pdf")
            with self.assertRaises(StatementReadError):
                parse_pdf(path)

    def test_generic_layout_imports_end_to_end(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "bni.pdf"; make_generic_statement_pdf(path)
            stmt = parse_pdf(path)
        assert stmt.institution == "BNI"
        assert (stmt.beginning_cents, stmt.deposits_cents, stmt.withdrawals_cents, stmt.ending_cents) == (
            1_089_592, 2_203_272, 124_995, 3_167_869)
        assert len(stmt.transactions) == 7

    def test_multipage_generic_layout_imports_each_row_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "bbt.pdf"; make_multipage_generic_statement_pdf(path)
            stmt = parse_pdf(path)
        assert len(stmt.transactions) == 6  # the fixture's rows, each once across the repeated header

    def test_bad_text_layer_retries_through_ocr(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "scanned.pdf"; make_unparsable_text_pdf(path)

            def fake(_path):
                return generic_statement_words()

            stmt = load_statement(path, ocr=fake)
        assert stmt.source == "ocr"
        assert stmt.ending_cents == 3_167_869

    def test_failed_ocr_retry_reraises_text_path_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "scanned.pdf"; make_unparsable_text_pdf(path)

            def fake(_path):
                raise StatementReadError("OCR read failed")

            with self.assertRaises((UnsupportedStatementError, StatementValidationError)):
                load_statement(path, ocr=fake)

    def test_sparse_page_render_failure_becomes_read_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "sparse.pdf"
            pdf = FPDF(unit="pt", format="letter"); pdf.add_page()
            pdf.set_font("Helvetica", size=12); pdf.text(50, 50, "Hello")
            pdf.output(str(path))
            with (
                mock.patch("budget_tracker.statements.ocr_available", return_value=True),
                mock.patch(
                    "budget_tracker.statements.render_pdf_page",
                    side_effect=RuntimeError("render exploded"),
                ),
            ):
                with self.assertRaises(StatementReadError):
                    load_statement(path)

    def test_document_over_twenty_pages_is_not_retried_through_ocr(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "long.pdf"
            pdf = FPDF(unit="pt", format="letter"); pdf.set_font("Helvetica", size=12)
            # More than eight words per page: the sparse-page OCR path must not run here.
            for _ in range(21):
                pdf.add_page()
                pdf.text(50, 50, "Lorem ipsum dolor sit amet consectetur adipiscing elit sed do eiusmod")
            pdf.output(str(path))
            calls = []

            def fake(_path):
                calls.append(_path)
                return generic_statement_words()

            with self.assertRaises((UnsupportedStatementError, StatementValidationError)):
                load_statement(path, ocr=fake)
        self.assertEqual(calls, [])

    def test_sparse_page_ocr_does_not_rewrite_text_layer_amounts(self):
        # Regression: the blank trailing page forces the sparse-page OCR path, but the Regions
        # adapter must still see page one's original grouped amounts (`$1,000.00`).
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "blank-last.pdf"
            make_statement_with_blank_page_pdf(path)
            with (
                mock.patch("budget_tracker.statements.ocr_available", return_value=True),
                mock.patch("budget_tracker.statements.ocr_images", return_value=[]),
            ):
                stmt = load_statement(path)
        self.assertEqual(stmt.source, "ocr")
        self.assertEqual(stmt.ending_cents, 119_000)
        self.assertEqual(len(stmt.transactions), 4)

    def test_engine_failure_on_image_becomes_read_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "photo.png"
            Image.new("L", (10, 10), 255).save(path)

            def fake(_path):
                raise RuntimeError("native engine exploded")

            with self.assertRaises(StatementReadError):
                load_statement(path, ocr=fake)

    def test_rejects_non_statement_pdf(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "other.pdf"
            pdf = FPDF(unit="pt", format="letter"); pdf.add_page(); pdf.set_font("Helvetica", size=8)
            pdf.text(50, 50, "Hello world")
            pdf.output(str(path))
            with self.assertRaises(UnsupportedStatementError):
                parse_pdf(path)
