import tempfile
import unittest
from datetime import date
from pathlib import Path

from fpdf import FPDF

from statement_fixture import make_multipage_pdf, make_statement_pdf

from budget_tracker.statements import parse_pdf
from budget_tracker.statements.model import UnsupportedStatementError


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

    def test_rejects_non_statement_pdf(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "other.pdf"
            pdf = FPDF(unit="pt", format="letter"); pdf.add_page(); pdf.set_font("Helvetica", size=8)
            pdf.text(50, 50, "Hello world")
            pdf.output(str(path))
            with self.assertRaises(UnsupportedStatementError):
                parse_pdf(path)
