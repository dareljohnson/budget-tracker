import tempfile
import unittest
from datetime import date
from pathlib import Path

from fpdf import FPDF

from statement_fixture import (
    generic_statement_words,
    make_generic_statement_pdf,
    make_multipage_generic_statement_pdf,
    make_multipage_pdf,
    make_statement_pdf,
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

    def test_rejects_non_statement_pdf(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "other.pdf"
            pdf = FPDF(unit="pt", format="letter"); pdf.add_page(); pdf.set_font("Helvetica", size=8)
            pdf.text(50, 50, "Hello world")
            pdf.output(str(path))
            with self.assertRaises(UnsupportedStatementError):
                parse_pdf(path)
