from datetime import datetime
from pathlib import Path

from fpdf import FPDF

from budget_tracker.statements.model import Word

# Generic-layout fixtures draw one token at a time at these column positions. The parser centres
# each header token and matches row values to the nearest column, so the header shares the values'
# x positions.
_DATE_X = 40
_DESCRIPTION_X = 130
_DEBIT_X = 400
_CREDIT_X = 470
_BALANCE_X = 540
_FONT_SIZE = 8
# pdfplumber splits words on gaps wider than 3 pt, so draw tokens at least this far apart or the
# extractor merges them into one word.
_WORD_GAP = 4.0


def make_statement_pdf(path: Path) -> None:
    pdf = FPDF(unit="pt", format="letter")
    pdf.set_creation_date(datetime(2026, 1, 1, 12, 0, 0))
    pdf.add_page()
    pdf.set_font("Helvetica", size=8)
    lines = [
        (150, 15, "Regions Bank"), (200, 237, "62+ LIFEGREEN CHECKING"),
        (200, 252, "September 16, 2026 through October 15, 2026"),
        (33, 141, "ACCOUNT #"), (90, 159, "060"),
        (220, 324, "$1,000.00"), (220, 336, "$500.00"), (220, 348, "$300.00"),
        (220, 360, "$10.00"), (220, 381, "$0.00"), (220, 405, "$0.00"), (220, 417, "$1,190.00"),
        (250, 465, "DEPOSITS & CREDITS"),
        (75, 480, "09/20 Card Purchase Merchant 200.00"),
        (240, 480, "10/02 Payroll 300.00"),
        (260, 579, "WITHDRAWALS"),
        (75, 591, "09/25 Rent 300.00"),
        (280, 620, "FEES"),
        (75, 632, "09/30 Service Fee 10.00"),
    ]
    for x, y, text in lines:
        pdf.text(x, y, text)
    pdf.output(str(path))


def make_multipage_pdf(path: Path) -> None:
    pdf = FPDF(unit="pt", format="letter")
    pdf.set_creation_date(datetime(2026, 1, 1, 12, 0, 0))
    pdf.set_font("Helvetica", size=8)
    pdf.add_page()
    page1 = [
        (150, 15, "Regions Bank"), (200, 237, "62+ LIFEGREEN CHECKING"),
        (200, 252, "September 16, 2026 through October 15, 2026"),
        (220, 324, "$1,000.00"), (220, 336, "$500.00"), (220, 348, "$300.00"),
        (220, 360, "$10.00"), (220, 381, "$0.00"), (220, 405, "$0.00"), (220, 417, "$1,190.00"),
        (250, 465, "DEPOSITS & CREDITS"),
        (75, 480, "09/20 Card Purchase Merchant 200.00"),
        (240, 480, "10/02 Payroll 300.00"),
        (260, 579, "WITHDRAWALS"),
        (75, 591, "09/25 Rent 100.00"),
    ]
    for x, y, text in page1:
        pdf.text(x, y, text)
    pdf.add_page()
    page2 = [
        (260, 500, "WITHDRAWALS (CONTINUED)"),
        (75, 520, "09/26 Other 200.00"),
        (280, 560, "FEES"),
        (75, 580, "09/30 Service Fee 10.00"),
    ]
    for x, y, text in page2:
        pdf.text(x, y, text)
    pdf.output(str(path))


def _spread(x: float, y: float, text: str) -> list[tuple[float, float, str]]:
    """Position each whitespace-separated token of ``text`` left-to-right from ``x``."""
    pdf = FPDF(unit="pt", format="letter")
    pdf.set_font("Helvetica", size=_FONT_SIZE)
    tokens: list[tuple[float, float, str]] = []
    cursor = x
    for index, token in enumerate(text.split()):
        if index:
            cursor += _WORD_GAP
        tokens.append((cursor, y, token))
        cursor += pdf.get_string_width(token)
    return tokens


def _expand(entries: list[tuple[float, float, str]]) -> list[tuple[float, float, str]]:
    """Expand every multi-word entry into one positioned token per word."""
    return [token for x, y, text in entries for token in _spread(x, y, text)]


def _table_row(
    y: float,
    date_text: str,
    description: str,
    *,
    debit: str | None = None,
    credit: str | None = None,
    balance: str | None = None,
) -> list[tuple[float, float, str]]:
    entries = [(_DATE_X, y, date_text), *_spread(_DESCRIPTION_X, y, description)]
    if debit is not None:
        entries.append((_DEBIT_X, y, debit))
    if credit is not None:
        entries.append((_CREDIT_X, y, credit))
    if balance is not None:
        entries.append((_BALANCE_X, y, balance))
    return entries


def _generic_statement_tokens() -> list[tuple[float, float, str]]:
    """The BNI-style statement's words as ``(x, y, text)`` tokens."""
    entries: list[tuple[float, float, str]] = [
        (40, 40, "BNI"),
        (40, 52, "BNI CHECKING ACCOUNT"),
        (40, 64, "Account # 1234"),
        (40, 90, "Opening Balance"), (400, 90, "$10,895.92"),
        (40, 102, "Total Credit"), (400, 102, "$22,032.72"),
        (40, 114, "Total Debit"), (400, 114, "$1,249.95"),
        (40, 126, "Closing Balance"), (400, 126, "$31,678.69"),
        (40, 160, "DATE"), (130, 160, "DESCRIPTION"), (200, 160, "REFERENCE"),
        (270, 160, "TRANSACTION"), (400, 160, "DEBIT"), (470, 160, "CREDIT"),
        (540, 160, "BALANCE"),
    ]
    rows = [
        ("09/20/2026", "Account Interest", "19.95", None, "10,875.97"),
        ("09/21/2026", "Branch Deposit", None, "20,000.00", "30,875.97"),
        ("09/22/2026", "Branch Deposit", None, "600.00", "31,475.97"),
        ("09/23/2026", "Branch Cheque - MC Kean & Park", None, "1,430.00", "32,905.97"),
        ("09/24/2026", "Cost of Bank Cheque", "100.00", None, "32,805.97"),
        ("09/25/2026", "Branch Deposit", None, "2.72", "32,808.69"),
        ("09/26/2026", "FED Bank Account Debits Tax", "1,130.00", None, "31,678.69"),
    ]
    for index, (date_text, description, debit, credit, balance) in enumerate(rows):
        entries.extend(
            _table_row(
                184 + index * 16,
                date_text,
                description,
                debit=debit,
                credit=credit,
                balance=balance,
            )
        )
    return _expand(entries)


def make_generic_statement_pdf(path: Path) -> None:
    """A BNI-style statement with explicit credit, debit and running-balance columns."""
    pdf = FPDF(unit="pt", format="letter")
    pdf.set_creation_date(datetime(2026, 1, 1, 12, 0, 0))
    pdf.add_page()
    pdf.set_font("Helvetica", size=_FONT_SIZE)
    for x, y, text in _generic_statement_tokens():
        pdf.text(x, y, text)
    pdf.output(str(path))


def generic_statement_words() -> list[Word]:
    """The exact words :func:`make_generic_statement_pdf` draws, for a fake OCR result."""
    return [Word(text, x, y) for x, y, text in _generic_statement_tokens()]


def _multipage_generic_tokens() -> list[list[tuple[float, float, str]]]:
    """A BB&T-style statement split across two pages, with the column header repeated."""
    header = [
        (_DATE_X, 0, "DATE"),
        (_DESCRIPTION_X, 0, "DESCRIPTION"),
        (_DEBIT_X, 0, "DEBIT"),
        (_CREDIT_X, 0, "CREDIT"),
    ]
    page1: list[tuple[float, float, str]] = [
        (40, 40, "BB&T"),
        (40, 60, "Your previous balance as of 05/18/2020"), (400, 60, "$2,074.21"),
        (40, 72, "Checks"), (400, 72, "$701.39"),
        (40, 84, "Other withdrawals, debits and service charges"), (400, 84, "$6,242.34"),
        (40, 96, "Deposits, credits and interest"), (400, 96, "$5,834.32"),
        (40, 108, "Your new balance as of 06/17/2020"), (400, 108, "$964.80"),
        *[(x, 140, text) for x, _, text in header],
        *_table_row(160, "05/28/2026", "Check 1401", debit="450.00"),
        *_table_row(176, "06/03/2026", "Check 965025", debit="101.39"),
        *_table_row(192, "06/04/2026", "Check 1500", debit="150.00"),
    ]
    page2: list[tuple[float, float, str]] = [
        *[(x, 40, text) for x, _, text in header],
        *_table_row(60, "06/05/2026", "Withdrawal ATM", debit="6,242.34"),
        *_table_row(76, "06/06/2026", "Deposit Payroll", credit="4,834.32"),
        *_table_row(92, "06/07/2026", "Deposit Interest", credit="1,000.00"),
    ]
    return [_expand(page1), _expand(page2)]


def make_multipage_generic_statement_pdf(path: Path) -> None:
    """A BB&T-style decomposed-summary statement whose column header repeats on page two."""
    pdf = FPDF(unit="pt", format="letter")
    pdf.set_creation_date(datetime(2026, 1, 1, 12, 0, 0))
    pdf.set_font("Helvetica", size=_FONT_SIZE)
    for tokens in _multipage_generic_tokens():
        pdf.add_page()
        for x, y, text in tokens:
            pdf.text(x, y, text)
    pdf.output(str(path))


def make_unparsable_text_pdf(path: Path) -> None:
    """A PDF with a real text layer that no statement parser recognizes."""
    pdf = FPDF(unit="pt", format="letter")
    pdf.set_creation_date(datetime(2026, 1, 1, 12, 0, 0))
    pdf.add_page()
    pdf.set_font("Helvetica", size=12)
    pdf.text(50, 50, "Lorem ipsum dolor sit amet consectetur adipiscing elit sed do")
    pdf.output(str(path))

