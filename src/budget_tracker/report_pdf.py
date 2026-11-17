"""Print-ready PDF reports.

The renderer is deliberately UI-free: it consumes the report dataclasses and writes on a local
Letter page with the app's report palette. Charts are Pillow PNGs embedded in the PDF, so the
whole report is generated offline. Page images for the in-app preview come from the same bytes
via pypdfium2.
"""

from __future__ import annotations

import calendar
import os
import tempfile
from collections.abc import Sequence
from datetime import date
from io import BytesIO
from pathlib import Path
from typing import TYPE_CHECKING

import pypdfium2
from fpdf import FPDF
from fpdf.enums import Align, TableBordersLayout, XPos, YPos
from fpdf.fonts import FontFace
from PIL import Image

from .formatting import format_money
from .report_charts import cumulative_chart_png, monthly_chart_png, net_worth_chart_png
from .reports import (
    MonthlyReport,
    NetWorthReport,
    YearReport,
    budget_status_label,
    month_status,
)

if TYPE_CHECKING:
    from .services import SavingsGoalProgress

PAGE_WIDTH_MM = 215.9
MARGIN_MM = 15
CONTENT_WIDTH_MM = PAGE_WIDTH_MM - 2 * MARGIN_MM

ACCENT = (63, 81, 181)
INK = (27, 27, 31)
MUTED = (107, 113, 128)
HAIRLINE = (225, 227, 235)
PANEL = (247, 248, 252)
POSITIVE = (11, 107, 47)
NEGATIVE = (165, 40, 40)
INCOME = (42, 120, 214)
SPENDING = (235, 104, 52)

_TRANSLATIONS = str.maketrans(
    {
        "–": "-",
        "—": "-",
        "‘": "'",
        "’": "'",
        "“": '"',
        "”": '"',
        "…": "...",
        "·": "-",
        "•": "-",
    }
)


def _pdf_text(value: object) -> str:
    """Fold typographic punctuation and replace characters the built-in PDF font cannot draw."""
    text = str(value or "").translate(_TRANSLATIONS)
    return text.encode("latin-1", "replace").decode("latin-1")


def _cell_text(value: object, *, limit: int = 80) -> str:
    """Keep a table cell short enough that a legacy long name cannot create a giant row."""
    text = _pdf_text(value)
    if len(text) <= limit:
        return text
    return text[: limit - 3].rstrip() + "..."


def _money(cents: int, *, signed: bool = False) -> str:
    return _pdf_text(format_money(cents, signed=signed))


def _date_text(value: date) -> str:
    return f"{calendar.month_name[value.month]} {value.day}, {value.year}"


def _short_month(month: str) -> str:
    year, mon = map(int, month.split("-"))
    return f"{calendar.month_abbr[mon]} {year}"


def _difference_color(cents: int) -> tuple[int, int, int]:
    return POSITIVE if cents >= 0 else NEGATIVE


class _ReportPDF(FPDF):
    def __init__(self, report_label: str, period_label: str) -> None:
        super().__init__(unit="mm", format="Letter")
        self.report_label = report_label
        self.period_label = period_label
        self.set_margins(MARGIN_MM, MARGIN_MM, MARGIN_MM)
        self.set_auto_page_break(auto=True, margin=18)
        self.alias_nb_pages()
        self.set_title(_pdf_text(f"Budget Tracker - {report_label}"))
        self.set_author("Budget Tracker")
        self.set_creator("Budget Tracker")

    def footer(self) -> None:
        self.set_y(-14)
        self.set_font("Helvetica", "", 7.5)
        self.set_text_color(*MUTED)
        self.cell(110, 5, _pdf_text(f"Budget Tracker  |  {self.report_label}  |  {self.period_label}"))
        self.set_x(PAGE_WIDTH_MM - MARGIN_MM - 70)
        self.cell(70, 5, f"Page {self.page_no()} of {{nb}}", align="R")

    def header_block(self, title: str, period: str, generated_on: date) -> None:
        self.add_page()
        self.set_fill_color(*ACCENT)
        self.rect(0, 0, PAGE_WIDTH_MM, 3.5, "F")
        self.set_y(MARGIN_MM)
        self.set_font("Helvetica", "B", 9)
        self.set_text_color(*INK)
        self.cell(70, 5, "Budget Tracker")
        self.set_font("Helvetica", "", 8)
        self.set_text_color(*MUTED)
        self.cell(
            0,
            5,
            _pdf_text(f"Generated {_date_text(generated_on)}"),
            align="R",
            new_x=XPos.LMARGIN,
            new_y=YPos.NEXT,
        )
        self.ln(6)
        self.set_font("Helvetica", "B", 22)
        self.set_text_color(*INK)
        self.cell(0, 10, _pdf_text(title), new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        self.set_font("Helvetica", "", 10)
        self.set_text_color(*MUTED)
        self.cell(0, 6, _pdf_text(period), new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        self.ln(4)
        self.set_draw_color(*HAIRLINE)
        self.set_line_width(0.3)
        self.line(MARGIN_MM, self.get_y(), PAGE_WIDTH_MM - MARGIN_MM, self.get_y())
        self.ln(5)

    def ensure(self, height: float) -> None:
        if self.will_page_break(height):
            self.add_page()

    def kpis(self, items: Sequence[tuple[str, str, str, tuple[int, int, int]]]) -> None:
        gap = 4
        width = (CONTENT_WIDTH_MM - gap * (len(items) - 1)) / len(items)
        y = self.get_y()
        x = MARGIN_MM
        for label, value, caption, color in items:
            self.set_fill_color(*PANEL)
            self.rect(x, y, width, 23, "F")
            self.set_text_color(*MUTED)
            self.set_font("Helvetica", "B", 7)
            self.set_xy(x + 3, y + 2.5)
            self.cell(width - 6, 4, _pdf_text(label.upper()))
            self.set_text_color(*color)
            size = 13 if len(value) <= 14 else 10.5
            self.set_font("Helvetica", "B", size)
            self.set_xy(x + 3, y + 7.5)
            self.cell(width - 6, 7, _pdf_text(value))
            self.set_text_color(*MUTED)
            self.set_font("Helvetica", "", 7)
            self.set_xy(x + 3, y + 15.5)
            self.cell(width - 6, 4, _pdf_text(caption))
            x += width + gap
        self.set_y(y + 27)

    def stats_row(self, items: Sequence[tuple[str, str, str]]) -> None:
        width = CONTENT_WIDTH_MM / len(items)
        y = self.get_y()
        self.set_draw_color(*HAIRLINE)
        self.set_line_width(0.3)
        self.line(MARGIN_MM, y, PAGE_WIDTH_MM - MARGIN_MM, y)
        for index, (label, value, note) in enumerate(items):
            x = MARGIN_MM + width * index
            self.set_xy(x, y + 2.5)
            self.set_text_color(*MUTED)
            self.set_font("Helvetica", "B", 7)
            self.cell(width - 4, 4, _pdf_text(label.upper()))
            self.set_xy(x, y + 7)
            self.set_text_color(*INK)
            self.set_font("Helvetica", "B", 11)
            self.cell(width - 4, 6, _pdf_text(value))
            if note:
                self.set_xy(x, y + 13.5)
                self.set_text_color(*MUTED)
                self.set_font("Helvetica", "", 7)
                self.cell(width - 4, 4, _pdf_text(note))
        self.set_y(y + 21)
        self.line(MARGIN_MM, self.get_y(), PAGE_WIDTH_MM - MARGIN_MM, self.get_y())
        self.ln(4)

    def strip(self, text: str, *, color: tuple[int, int, int] = INK) -> None:
        y = self.get_y()
        self.set_fill_color(*PANEL)
        self.rect(MARGIN_MM, y, CONTENT_WIDTH_MM, 8.5, "F")
        self.set_text_color(*color)
        self.set_font("Helvetica", "", 8.5)
        self.set_xy(MARGIN_MM + 3, y + 1.5)
        self.cell(CONTENT_WIDTH_MM - 6, 5.5, _pdf_text(text))
        self.set_y(y + 11.5)

    def section(self, title: str, note: str = "", *, reserve: float = 0.0) -> None:
        """Add a section heading, reserving room for the block that follows it.

        A heading on its own at the bottom of a page reads as an orphan. ``reserve`` is the
        approximate height of the chart or table that follows, so the heading moves with it.
        """
        self.ensure(18 + reserve)
        self.ln(3)
        self.set_font("Helvetica", "B", 12)
        self.set_text_color(*INK)
        self.cell(120, 6, _pdf_text(title))
        if note:
            self.set_font("Helvetica", "", 8)
            self.set_text_color(*MUTED)
            self.cell(0, 6, _pdf_text(note), align="R")
        self.ln(7)

    def note(self, text: str) -> None:
        self.set_font("Helvetica", "", 8)
        self.set_text_color(*MUTED)
        self.multi_cell(CONTENT_WIDTH_MM, 4.5, _pdf_text(text))
        self.ln(2)

    def report_table(
        self,
        headers: Sequence[str],
        rows: Sequence[Sequence[str]],
        widths: Sequence[float],
        aligns: Sequence[Align],
        *,
        font_size: float = 8.5,
    ) -> None:
        self.set_font("Helvetica", "", font_size)
        self.set_text_color(*INK)
        # fpdf2 seeds a table's initial style from the current fill color. A colored fill left
        # over from a header or strip would paint every cell, so reset it to the page color.
        self.set_fill_color(255, 255, 255)
        with self.table(
            col_widths=tuple(widths),
            text_align=tuple(aligns),
            line_height=5.3,
            padding=1.4,
            borders_layout=TableBordersLayout.HORIZONTAL_LINES,
            headings_style=FontFace(emphasis="BOLD", color=MUTED, size_pt=7.5),
        ) as table:
            heading = table.row()
            for value in headers:
                heading.cell(_cell_text(value))
            for row in rows:
                table_row = table.row()
                for value in row:
                    table_row.cell(_cell_text(value))
        self.ln(2)

    def chart(self, png: bytes, *, max_height_mm: float = 80) -> None:
        with Image.open(BytesIO(png)) as image:
            pixel_width, pixel_height = image.size
        height = min(max_height_mm, CONTENT_WIDTH_MM * pixel_height / max(1, pixel_width))
        self.ensure(height + 6)
        self.image(BytesIO(png), x=MARGIN_MM, y=self.get_y(), w=CONTENT_WIDTH_MM, h=height)
        self.set_y(self.get_y() + height + 4)

    def hero_net_worth(self, report: NetWorthReport) -> None:
        self.ln(2)
        self.set_font("Helvetica", "", 8)
        self.set_text_color(*MUTED)
        self.cell(0, 4, "NET WORTH", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        self.set_font("Helvetica", "B", 28)
        self.set_text_color(*INK)
        self.cell(0, 13, _money(report.net_worth_cents), new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        self.set_font("Helvetica", "", 9)
        self.set_text_color(*MUTED)
        self.cell(0, 5, _pdf_text(f"Assets {_money(report.assets_cents)}   |   Liabilities {_money(report.liabilities_cents)}"), new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        y = self.get_y() + 2
        self.set_font("Helvetica", "", 8.5)
        self.set_xy(MARGIN_MM, y)
        self.set_text_color(*MUTED)
        self.cell(52, 5, "Since last month")
        self.set_text_color(*_difference_color(report.change_month_cents or 0))
        self.cell(38, 5, _money(report.change_month_cents or 0, signed=True))
        self.set_text_color(*MUTED)
        self.cell(54, 5, "Since January 1")
        self.set_text_color(*_difference_color(report.change_ytd_cents))
        self.cell(0, 5, _money(report.change_ytd_cents, signed=True), new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        self.ln(3)
        self.set_draw_color(*HAIRLINE)
        self.line(MARGIN_MM, self.get_y(), PAGE_WIDTH_MM - MARGIN_MM, self.get_y())
        self.ln(4)

    def debt_callout(self, text: str) -> None:
        text = _pdf_text(text)
        self.set_font("Helvetica", "", 8.2)
        text_height = self.multi_cell(
            CONTENT_WIDTH_MM - 8,
            4.4,
            text,
            dry_run=True,
            output="HEIGHT",
        )
        height = text_height + 4
        self.ensure(height + 4)
        y = self.get_y()
        self.set_fill_color(*PANEL)
        self.rect(MARGIN_MM, y, CONTENT_WIDTH_MM, height, "F")
        self.set_fill_color(*ACCENT)
        self.rect(MARGIN_MM, y, 1.5, height, "F")
        self.set_xy(MARGIN_MM + 4, y + 2)
        self.set_text_color(*INK)
        self.multi_cell(CONTENT_WIDTH_MM - 8, 4.4, text)
        self.set_y(y + height + 4)


def _month_rows(report: YearReport) -> list[list[str]]:
    rows: list[list[str]] = []
    for month in report.overview.months[: report.actual_months]:
        label = calendar.month_name[int(month.month.split("-")[1])]
        in_progress = report.in_progress_month == month.month
        if in_progress:
            difference = "-"
        elif month.has_budget:
            difference = _money(month.difference_cents, signed=True)
        else:
            difference = "-"
        rows.append(
            [
                label,
                _money(month.budget_cents) if month.has_budget else "-",
                _money(month.spending_cents),
                difference,
                month_status(month, in_progress=in_progress),
            ]
        )
    return rows


def _category_rows(report: MonthlyReport | YearReport) -> list[list[str]]:
    return [
        [item.name, _money(item.amount_cents), f"{item.share:.1%}"]
        for item in report.categories
    ]


def _render_monthly(report: MonthlyReport) -> bytes:
    pdf = _ReportPDF(report.title, report.period_label)
    pdf.header_block(report.title, report.period_detail, report.generated_on)
    pdf.kpis(
        [
            ("Income", _money(report.income_cents), "Money received", INCOME),
            ("Spending", _money(report.spending_cents), "Net of refunds", SPENDING),
            ("Net", _money(report.net_cents, signed=True), "Income minus spending", _difference_color(report.net_cents)),
            ("Savings rate", f"{report.savings_rate:.1%}" if report.income_cents else "-", "Kept from income", INK),
        ]
    )
    if report.budgets:
        difference = report.budget_difference_cents
        state = "over" if difference < 0 else "left"
        pdf.strip(
            f"Budgeted spending {_money(report.budget_spent_cents)} of {_money(report.budget_cents)}  |  "
            f"{_money(abs(difference))} {state}",
            color=_difference_color(difference),
        )
    else:
        pdf.strip("No budgets set for this month.", color=MUTED)

    pdf.section("Income and spending", "Last six months", reserve=64)
    pdf.chart(monthly_chart_png(report.trend), max_height_mm=64)

    pdf.section("Spending by category", "Share of net spending", reserve=20)
    if report.categories:
        pdf.report_table(
            ("Category", "Amount", "Share"),
            _category_rows(report),
            widths=(110, 45.9, 30),
            aligns=(Align.L, Align.R, Align.R),
        )
        if any(item.amount_cents < 0 for item in report.categories):
            pdf.note("A negative amount is net refunds in that category; shares use net spending.")
    else:
        pdf.note("No spending recorded for this month.")

    pdf.section("Budget performance", f"{_money(report.budget_cents)} total", reserve=22)
    if report.budgets:
        pdf.report_table(
            ("Category", "Budget", "Spent", "Left", "Status"),
            [
                [
                    row.name,
                    _money(row.limit_cents),
                    _money(row.spent_cents),
                    _money(row.remaining_cents, signed=True),
                    budget_status_label(row.status),
                ]
                for row in report.budgets
            ],
            widths=(60, 33, 33, 32, 27.9),
            aligns=(Align.L, Align.R, Align.R, Align.R, Align.L),
        )
        if report.has_unbudgeted_spending:
            pdf.note(
                "Spending in categories without a budget is not in the budget table; "
                "the Spending figure at the top includes it."
            )
    else:
        pdf.note("Set monthly limits on the Budgets page to compare spending with a plan.")

    pdf.section("Largest expenses", "Top five by amount", reserve=20)
    if report.largest:
        pdf.report_table(
            ("Date", "Description", "Category", "Account", "Amount"),
            [
                [
                    f"{transaction.occurred_on:%b} {transaction.occurred_on.day}",
                    transaction.description or transaction.category_name or "Expense",
                    transaction.category_name or "",
                    transaction.account_name or "",
                    _money(transaction.amount_cents),
                ]
                for transaction in report.largest
            ],
            widths=(18, 67, 36, 42, 22.9),
            aligns=(Align.L, Align.L, Align.L, Align.L, Align.R),
        )
    else:
        pdf.note("No expenses recorded for this month.")

    pdf.section(
        "Balances",
        f"As of {calendar.month_abbr[report.generated_on.month]} {report.generated_on.day}, "
        f"{report.generated_on.year} | net worth {_money(report.net_worth_cents)}",
        reserve=22,
    )
    if report.accounts:
        ordered = sorted(
            report.accounts,
            key=lambda balance: (
                balance.account.kind.is_liability,
                not bool(balance.account.liquid),
                balance.account.name.casefold(),
            ),
        )
        pdf.report_table(
            ("Account", "Type", "Liquidity", "Balance"),
            [
                [
                    balance.account.name,
                    balance.account.kind.label,
                    "-" if balance.account.kind.is_liability else (
                        "Liquid" if balance.account.liquid else "Non-liquid"
                    ),
                    _money(balance.balance_cents),
                ]
                for balance in ordered
            ],
            widths=(62, 45, 30, 48.9),
            aligns=(Align.L, Align.L, Align.L, Align.R),
        )
    else:
        pdf.note("No accounts recorded.")
    return bytes(pdf.output())


def _render_year(report: YearReport) -> bytes:
    pdf = _ReportPDF(report.title, report.period_label)
    pdf.header_block(report.title, report.period_detail, report.generated_on)
    pdf.kpis(
        [
            (
                "Income",
                _money(report.income_cents),
                "Year to date" if report.in_progress_month else f"Across {report.actual_months} months",
                INCOME,
            ),
            ("Spending", _money(report.spending_cents), "Net of refunds", SPENDING),
            ("Net", _money(report.net_cents, signed=True), "Income minus spending", _difference_color(report.net_cents)),
            ("Savings rate", f"{report.savings_rate:.1%}" if report.income_cents else "-", "Kept from income", INK),
        ]
    )
    difference = report.budget_difference_cents
    pdf.strip(
        f"Budget {_money(report.budget_cents)}  |  Spent {_money(report.spending_cents)}  |  "
        f"{_money(abs(difference))} {'over' if difference < 0 else 'left'}",
        color=_difference_color(difference),
    )
    if not report.overview.has_data:
        pdf.note("No transactions or budgets were recorded for this period yet.")

    pdf.section("Budget and spending across the year", "Cumulative", reserve=76)
    labels = [calendar.month_abbr[int(month.month.split("-")[1])] for month in report.overview.months]
    pdf.chart(
        cumulative_chart_png(
            labels,
            report.cumulative_budget,
            report.cumulative_spending,
            report.actual_months,
        ),
        max_height_mm=76,
    )
    forecast = report.projected_year_end_cents
    budgeted = report.overview.budgeted_months
    if report.has_projection and forecast is not None:
        pace = ("Year-end pace", _money(forecast), "Estimate")
    else:
        pace = (
            "Year spending",
            _money(report.spending_cents),
            "Year to date" if report.in_progress_month else "Completed year",
        )
    pdf.stats_row(
        [
            (
                "Budget, year to date",
                _money(report.budget_cents),
                f"{budgeted} of {report.actual_months} months budgeted",
            ),
            ("Over budget" if difference < 0 else "Under budget", _money(abs(difference)), "Budget minus spending"),
            pace,
        ]
    )
    if report.actual_months and budgeted < 3:
        if budgeted == 0:
            coverage = "No months have a budget; the budget comparison is empty."
        elif budgeted == 1:
            coverage = (
                f"Only 1 of {report.actual_months} months has a budget; "
                "the budget comparison is partial."
            )
        else:
            coverage = (
                f"Only {budgeted} of {report.actual_months} months have a budget; "
                "the budget comparison is partial."
            )
        pdf.note(coverage)

    pdf.section("Month by month", "Actuals", reserve=22)
    if report.actual_months:
        rows = _month_rows(report)
        if report.actual_months:
            rows.append(
                [
                    "Year to date",
                    _money(report.budget_cents),
                    _money(report.spending_cents),
                    _money(difference, signed=True),
                    "Over" if difference < 0 else "Under",
                ]
            )
        pdf.report_table(
            ("Month", "Budget", "Spending", "Difference", "Status"),
            rows,
            widths=(42, 34, 38, 39, 32.9),
            aligns=(Align.L, Align.R, Align.R, Align.R, Align.L),
        )
    else:
        pdf.note("No months have been completed for this year yet.")

    pdf.section("Where the year went", "Share of net spending", reserve=20)
    if report.categories:
        pdf.report_table(
            ("Category", "Amount", "Share"),
            _category_rows(report),
            widths=(110, 45.9, 30),
            aligns=(Align.L, Align.R, Align.R),
        )
        if any(item.amount_cents < 0 for item in report.categories):
            pdf.note("A negative amount is net refunds in that category; shares use net spending.")
    else:
        pdf.note("No spending recorded for this period.")

    pdf.section("Against last year", "Same period", reserve=20)
    if report.prior_income_cents is not None and report.prior_spending_cents is not None:
        prior_rate = report.prior_savings_rate
        pdf.report_table(
            ("Measure", "This year", "Last year", "Change"),
            [
                [
                    "Income",
                    _money(report.income_cents),
                    _money(report.prior_income_cents),
                    f"{report.income_change:+.1%}" if report.income_change is not None else "-",
                ],
                [
                    "Spending",
                    _money(report.spending_cents),
                    _money(report.prior_spending_cents),
                    f"{report.spending_change:+.1%}" if report.spending_change is not None else "-",
                ],
                [
                    "Savings rate",
                    f"{report.savings_rate:.1%}" if report.income_cents else "-",
                    f"{prior_rate:.1%}" if prior_rate is not None else "-",
                    f"{report.savings_rate_change:+.1%}" if report.savings_rate_change is not None else "-",
                ],
            ],
            widths=(60, 45, 45, 35.9),
            aligns=(Align.L, Align.R, Align.R, Align.R),
        )
    else:
        pdf.note("No transactions were recorded for the same period last year.")
    return bytes(pdf.output())


def _goal_state(goal: SavingsGoalProgress) -> str:
    if goal.state == "reached":
        return "Reached"
    if goal.state == "overdue":
        return "Overdue"
    return "Active"


def _render_net_worth(report: NetWorthReport) -> bytes:
    pdf = _ReportPDF(report.title, report.period_label)
    pdf.header_block(report.title, report.period_detail, report.generated_on)
    pdf.hero_net_worth(report)

    pdf.section("Net worth over twelve months", "Month-end balances", reserve=58)
    pdf.chart(
        net_worth_chart_png(
            [point.label for point in report.trend],
            [point.net_worth_cents for point in report.trend],
        ),
        max_height_mm=58,
    )

    pdf.section(
        "Assets",
        f"Liquid {_money(report.liquid_assets_cents)}  |  Other {_money(report.other_assets_cents)}",
        reserve=20,
    )
    if report.assets:
        pdf.report_table(
            ("Asset", "Liquidity", "Balance"),
            [
                [
                    balance.account.name,
                    "Liquid" if balance.account.liquid else "Non-liquid",
                    _money(balance.balance_cents),
                ]
                for balance in report.assets
            ],
            widths=(85, 40, 60.9),
            aligns=(Align.L, Align.L, Align.R),
        )
    else:
        pdf.note("No asset accounts recorded.")

    pdf.section("Liabilities", f"{_money(report.liabilities_cents)} owed", reserve=20)
    if report.liabilities:
        pdf.report_table(
            ("Liability", "Type", "Owed"),
            [
                [
                    balance.account.name,
                    balance.account.kind.label,
                    _money(-balance.balance_cents),
                ]
                for balance in report.liabilities
            ],
            widths=(75, 55, 55.9),
            aligns=(Align.L, Align.L, Align.R),
        )
    else:
        pdf.note("No liability accounts recorded.")
    pdf.strip(
        f"Assets {_money(report.assets_cents)}  -  Liabilities {_money(report.liabilities_cents)}"
        f"  =  Net worth {_money(report.net_worth_cents)}"
    )

    pdf.section("Debt payoff", "Estimates at the current minimums", reserve=24)
    if report.debts:
        pdf.report_table(
            ("Debt", "Balance", "APR", "Minimum", "Interest / mo", "Payoff at minimum"),
            [
                [
                    row.name,
                    _money(row.balance_cents),
                    f"{row.apr_bps / 100:.2f}%",
                    _money(row.minimum_cents),
                    _money(row.interest_cents),
                    _short_month(row.payoff_month) if row.payoff_month else "Does not pay off",
                ]
                for row in report.debts
            ],
            widths=(48, 33, 20, 26, 28, 30.9),
            aligns=(Align.L, Align.R, Align.R, Align.R, Align.R, Align.R),
            font_size=8,
        )
        parts: list[str] = []
        minimum = report.minimum_plan
        extra = report.extra_plan
        if minimum is not None:
            if minimum.finished and minimum.debt_free_month:
                parts.append(
                    f"At the current minimums, the last debt clears in {_short_month(minimum.debt_free_month)}."
                )
            else:
                parts.append("At the current minimums, the debts are not paid off within 50 years.")
        if extra is not None:
            if extra.finished and extra.debt_free_month:
                sentence = (
                    f" An avalanche with {_money(report.extra_cents)} extra per month clears them "
                    f"around {_short_month(extra.debt_free_month)}"
                )
                saved = report.interest_saved_cents
                if saved is not None:
                    sentence += f" and saves about {_money(saved)} in interest"
                parts.append(sentence + ".")
            else:
                parts.append(
                    f" Even an avalanche with {_money(report.extra_cents)} extra per month does not "
                    "finish within 50 years."
                )
        if parts:
            pdf.debt_callout("".join(parts) + " These are estimates, not a payoff schedule.")
        if any(row.minimum_below_interest for row in report.debts):
            pdf.note("A minimum payment below the monthly interest will not reduce that balance.")
        if any(row.account_name is None for row in report.debts):
            pdf.note(
                "A standalone debt is listed here but is not linked to an account, so it is not "
                "in the account-based net worth above."
            )
    else:
        pdf.note("No debts are tracked. Add one on the Debts page to see a payoff estimate.")

    pdf.section("Savings goals", "Contributed to date", reserve=20)
    if report.goals:
        pdf.report_table(
            ("Goal", "Saved", "Target", "Progress", "Target date", "Monthly"),
            [
                [
                    goal.goal.name,
                    _money(goal.progress_cents),
                    _money(goal.goal.target_cents),
                    f"{goal.progress_cents / goal.goal.target_cents:.0%}" if goal.goal.target_cents else "-",
                    f"{calendar.month_abbr[goal.goal.target_date.month]} {goal.goal.target_date.year}",
                    _money(goal.required_monthly_cents) if goal.state == "active" else _goal_state(goal),
                ]
                for goal in report.goals
            ],
            widths=(48, 31, 31, 22, 27, 26.9),
            aligns=(Align.L, Align.R, Align.R, Align.R, Align.L, Align.R),
            font_size=8,
        )
    else:
        pdf.note("No savings goals are tracked.")
    return bytes(pdf.output())


def render_report_pdf(report: MonthlyReport | YearReport | NetWorthReport) -> bytes:
    """Return the complete PDF for one report."""
    if isinstance(report, MonthlyReport):
        return _render_monthly(report)
    if isinstance(report, YearReport):
        return _render_year(report)
    if isinstance(report, NetWorthReport):
        return _render_net_worth(report)
    raise TypeError(f"Unsupported report: {type(report).__name__}")


def write_report_pdf(path: Path | str, data: bytes) -> Path:
    """Write the PDF atomically so a failed export cannot leave a half-written file."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(prefix=f"{target.stem}-", suffix=".pdf", dir=target.parent)
    try:
        with os.fdopen(handle, "wb") as stream:
            stream.write(data)
        os.replace(temporary, target)
    except Exception:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise
    return target


def export_report_pdf(report: MonthlyReport | YearReport | NetWorthReport, path: Path | str) -> Path:
    return write_report_pdf(path, render_report_pdf(report))


def page_count(pdf_bytes: bytes) -> int:
    document = pypdfium2.PdfDocument(pdf_bytes)
    try:
        return len(document)
    finally:
        document.close()


def page_png(pdf_bytes: bytes, page_index: int, *, scale: float = 1.6) -> bytes:
    """Render one PDF page for the Reports page preview."""
    document = pypdfium2.PdfDocument(pdf_bytes)
    try:
        index = max(0, min(int(page_index), len(document) - 1))
        page = document[index]
        image = page.render(scale=scale).to_pil()
        buffer = BytesIO()
        image.save(buffer, format="PNG", optimize=True)
        return buffer.getvalue()
    finally:
        document.close()
