from pathlib import Path

from fpdf import FPDF


def make_statement_pdf(path: Path) -> None:
    pdf = FPDF(unit="pt", format="letter")
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
