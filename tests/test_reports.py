"""Report assembly and PDF output: monthly, year, and net-worth reports."""

import io
import tempfile
import types
import unittest
from datetime import date
from pathlib import Path

import pdfplumber
import pypdfium2

from budget_tracker.db import connect
from budget_tracker.models import (
    Account,
    AccountKind,
    Budget,
    Debt,
    DebtKind,
    SavingsGoal,
    Transaction,
    TransactionType,
)
from budget_tracker.report_pdf import (
    export_report_pdf,
    page_count,
    page_png,
    render_report_pdf,
    write_report_pdf,
)
from budget_tracker.reports import (
    ReportKind,
    ReportService,
    budget_status_label,
    month_status,
)
from budget_tracker.repository import Repository
from budget_tracker.services import BudgetService

TODAY = date(2026, 11, 16)


class Base(unittest.TestCase):
    def setUp(self) -> None:
        self.repo = Repository(connect(":memory:"))
        self.service = BudgetService(self.repo)
        self.reports = ReportService(self.service)
        self.cats = {category.name: category for category in self.repo.list_categories()}
        self.accts = {account.name: account for account in self.repo.list_accounts()}

    def tearDown(self) -> None:
        self.repo.close()

    def add(
        self,
        cents: int,
        category: str,
        day: date,
        *,
        account: str = "Checking",
        type_: TransactionType | None = None,
        description: str = "",
        transfer_to: str | None = None,
    ) -> Transaction:
        category_row = self.cats[category]
        return self.repo.add_transaction(
            Transaction(
                cents,
                type_ or category_row.type,
                category_row.id,
                day,
                description,
                account_id=self.accts[account].id,
                transfer_account_id=self.accts[transfer_to].id if transfer_to else None,
            )
        )

    def income(self, cents: int, day: date, *, account: str = "Checking") -> Transaction:
        return self.add(cents, "Salary", day, account=account, description="Pay")

    def spend(
        self,
        cents: int,
        category: str,
        day: date,
        *,
        account: str = "Checking",
        description: str = "Purchase",
    ) -> Transaction:
        return self.add(cents, category, day, account=account, description=description)

    def refund(self, cents: int, category: str, day: date, *, account: str = "Checking") -> Transaction:
        return self.add(
            cents,
            category,
            day,
            account=account,
            type_=TransactionType.REFUND,
            description="Refund",
        )

    def transfer(self, cents: int, source: str, destination: str, day: date) -> Transaction:
        return self.repo.add_transaction(
            Transaction(
                cents,
                TransactionType.TRANSFER,
                None,
                day,
                "Transfer",
                account_id=self.accts[source].id,
                transfer_account_id=self.accts[destination].id,
            )
        )

    def budget(self, month: str, cents: int, category: str = "Groceries") -> None:
        self.repo.set_budget(Budget(self.cats[category].id, month, cents))

    def savings_account(self, opening_cents: int = 0) -> Account:
        account = self.repo.add_account(
            Account("High-Yield Savings", AccountKind.SAVINGS, opening_cents, "#00897B")
        )
        self.accts[account.name] = account
        return account


class TestMonthlyReport(Base):
    def setUp(self) -> None:
        super().setUp()
        self.savings_account()
        self.income(500_000, date(2026, 10, 1))
        self.spend(120_000, "Groceries", date(2026, 10, 5))
        self.spend(80_000, "Dining Out", date(2026, 10, 6), account="Credit Card")
        self.spend(50_000, "Shopping", date(2026, 10, 7), account="Credit Card")
        self.refund(10_000, "Groceries", date(2026, 10, 8))
        self.transfer(50_000, "Checking", "High-Yield Savings", date(2026, 10, 9))
        self.budget("2026-10", 150_000, "Groceries")
        self.budget("2026-10", 100_000, "Dining Out")
        self.budget("2026-10", 200_000, "Housing")

    def test_totals_budgets_categories_and_largest(self) -> None:
        report = self.reports.monthly("2026-10", today=date(2026, 10, 31))

        self.assertEqual(report.income_cents, 500_000)
        self.assertEqual(report.spending_cents, 240_000)
        self.assertEqual(report.net_cents, 260_000)
        self.assertAlmostEqual(report.savings_rate, 0.52)
        self.assertEqual(report.budget_cents, 450_000)
        self.assertEqual(report.budget_spent_cents, 190_000)
        self.assertEqual(report.budget_difference_cents, 260_000)
        self.assertAlmostEqual(report.budget_ratio, 190_000 / 450_000)
        self.assertTrue(report.has_unbudgeted_spending)
        self.assertEqual(report.unbudgeted_spending_cents, 50_000)

        self.assertEqual([row.name for row in report.budgets], ["Dining Out", "Groceries", "Housing"])
        self.assertEqual([row.status for row in report.budgets], ["near", "ok", "ok"])
        self.assertEqual(report.budgets[0].remaining_cents, 20_000)

        self.assertEqual([item.name for item in report.categories], ["Groceries", "Dining Out", "Shopping"])
        self.assertEqual([item.amount_cents for item in report.categories], [110_000, 80_000, 50_000])
        self.assertAlmostEqual(report.categories[0].share, 110_000 / 240_000)

        self.assertEqual([tx.amount_cents for tx in report.largest], [120_000, 80_000, 50_000])
        self.assertNotIn(10_000, [tx.amount_cents for tx in report.largest])

        self.assertEqual(len(report.trend), 6)
        self.assertEqual(report.trend[-1].month, "2026-10")
        self.assertEqual(report.trend[-1].income_cents, 500_000)
        self.assertEqual(report.trend[-1].expense_cents, 240_000)

        balances = {row.account.name: row.balance_cents for row in report.accounts}
        self.assertEqual(balances["Checking"], 340_000)
        self.assertEqual(balances["High-Yield Savings"], 50_000)
        self.assertEqual(balances["Credit Card"], -130_000)
        self.assertEqual(report.net_worth_cents, 260_000)
        self.assertEqual(report.period_detail, "October 1 - October 31, 2026")
        self.assertEqual(report.filename, "budget_tracker_monthly_2026-10.pdf")

    def test_balances_exclude_transactions_after_the_generation_date(self) -> None:
        self.income(999_000, date(2026, 12, 1))
        report = self.reports.monthly("2026-10", today=date(2026, 10, 31))

        balances = {row.account.name: row.balance_cents for row in report.accounts}
        self.assertEqual(balances["Checking"], 340_000)
        self.assertEqual(report.net_worth_cents, 260_000)

    def test_empty_month_is_a_valid_report(self) -> None:
        report = self.reports.monthly("2026-04", today=date(2026, 10, 31))

        self.assertEqual((report.income_cents, report.spending_cents, report.net_cents), (0, 0, 0))
        self.assertEqual(report.categories, [])
        self.assertEqual(report.budgets, [])
        self.assertEqual(report.largest, [])
        self.assertEqual(report.budget_ratio, 0.0)
        self.assertFalse(report.has_unbudgeted_spending)
        self.assertEqual(len(report.trend), 6)
        self.assertGreaterEqual(page_count(render_report_pdf(report)), 1)


class TestYearReport(Base):
    def setUp(self) -> None:
        super().setUp()
        for year, amounts in (
            (2025, ((100_000, 50_000), (100_000, 60_000), (100_000, 70_000))),
            (2026, ((200_000, 110_000), (200_000, 120_000), (200_000, 130_000))),
        ):
            for index, (income, spending) in enumerate(amounts, start=1):
                self.income(income, date(year, index, 1))
                self.spend(spending, "Groceries", date(year, index, 10))
        for index in (1, 2, 3):
            self.budget(f"2026-{index:02d}", 150_000, "Groceries")

    def test_totals_projection_and_prior_year(self) -> None:
        report = self.reports.year(2026, today=date(2026, 3, 15))

        self.assertEqual(report.actual_months, 3)
        self.assertEqual(report.income_cents, 600_000)
        self.assertEqual(report.spending_cents, 360_000)
        self.assertEqual(report.net_cents, 240_000)
        self.assertAlmostEqual(report.savings_rate, 0.4)
        self.assertEqual(report.budget_cents, 450_000)
        self.assertEqual(report.budget_difference_cents, 90_000)
        self.assertEqual(report.projected_year_end_cents, 1_440_000)
        self.assertTrue(report.has_projection)
        self.assertEqual(report.full_year_budget_cents, 450_000)
        self.assertEqual(report.projected_difference_cents, 450_000 - 1_440_000)
        self.assertEqual(report.cumulative_spending[-1], 1_440_000)
        self.assertEqual(report.cumulative_budget[-1], 450_000)
        self.assertEqual([item.name for item in report.categories], ["Groceries"])
        self.assertEqual(report.categories[0].amount_cents, 360_000)
        self.assertAlmostEqual(report.categories[0].share, 1.0)

        self.assertEqual(report.prior_income_cents, 300_000)
        self.assertEqual(report.prior_spending_cents, 180_000)
        self.assertAlmostEqual(report.income_change, 1.0)
        self.assertAlmostEqual(report.spending_change, 1.0)
        self.assertAlmostEqual(report.prior_savings_rate, 0.4)
        self.assertAlmostEqual(report.savings_rate_change, 0.0)
        self.assertEqual(report.period_label, "2026 to date")
        self.assertEqual(report.period_detail, "January 1 - March 15, 2026")
        self.assertEqual(report.in_progress_month, "2026-03")
        self.assertEqual(report.filename, "budget_tracker_year_2026.pdf")
        self.assertEqual(month_status(report.overview.months[2], in_progress=True), "In progress")
        self.assertEqual(month_status(report.overview.months[0]), "Under")

    def test_december_partial_month_is_to_date(self) -> None:
        report = self.reports.year(2026, today=date(2026, 12, 15))

        self.assertEqual(report.in_progress_month, "2026-12")
        self.assertEqual(report.period_label, "2026 to date")
        self.assertEqual(report.period_detail, "January 1 - December 15, 2026")


class TestCategorySlices(Base):
    def test_refund_heavy_category_is_kept_and_shares_use_net_spending(self) -> None:
        self.spend(100_000, "Groceries", date(2026, 10, 1))
        self.refund(150_000, "Groceries", date(2026, 10, 2))
        self.spend(50_000, "Dining Out", date(2026, 10, 3))
        self.spend(100_000, "Shopping", date(2026, 10, 4))

        report = self.reports.monthly("2026-10", today=date(2026, 10, 31))

        amounts = {item.name: item.amount_cents for item in report.categories}
        self.assertEqual(amounts, {"Shopping": 100_000, "Dining Out": 50_000, "Groceries": -50_000})
        self.assertEqual(sum(amounts.values()), report.spending_cents)
        shares = {item.name: item.share for item in report.categories}
        self.assertAlmostEqual(shares["Shopping"], 1.0)
        self.assertAlmostEqual(shares["Dining Out"], 0.5)
        self.assertAlmostEqual(shares["Groceries"], -0.5)
        self.assertAlmostEqual(sum(shares.values()), 1.0)


class TestNetWorthReport(Base):
    def setUp(self) -> None:
        super().setUp()
        self.savings = self.savings_account(opening_cents=100_000)
        self.income(200_000, date(2026, 10, 1))
        self.spend(50_000, "Groceries", date(2026, 10, 2))
        self.spend(30_000, "Shopping", date(2026, 10, 3), account="Credit Card")
        self.income(10_000, date(2026, 11, 10))
        self.service.create_debt("Visa", DebtKind.CREDIT_CARD, 0, 2_290, 5_000, account_id=self.accts["Credit Card"].id)
        self.service.create_debt("Auto Loan", DebtKind.AUTO, 500_000, 490, 20_000)
        self.goal = self.repo.add_savings_goal(
            SavingsGoal(
                "Emergency fund",
                1_000_000,
                date(2027, 12, 31),
                date(2026, 1, 1),
                self.savings.id,
                200_000,
            )
        )
        self.service.contribute_to_goal(
            self.goal.id,
            self.accts["Checking"].id,
            50_000,
            date(2026, 10, 15),
            today=TODAY,
        )

    def test_accounts_trend_debts_and_goals(self) -> None:
        report = self.reports.net_worth(today=TODAY)

        balances = {row.account.name: row.balance_cents for row in report.accounts}
        self.assertEqual(balances["Checking"], 110_000)
        self.assertEqual(balances["High-Yield Savings"], 150_000)
        self.assertEqual(balances["Credit Card"], -30_000)
        self.assertEqual(report.assets_cents, 260_000)
        self.assertEqual(report.liabilities_cents, 30_000)
        self.assertEqual(report.net_worth_cents, 230_000)
        self.assertEqual(report.assets[0].account.name, "High-Yield Savings")
        self.assertEqual(report.liquid_assets_cents, 260_000)
        self.assertEqual(report.other_assets_cents, 0)
        self.assertTrue(all(row.account.liquid for row in report.liquid_assets))
        self.assertEqual([row.account.name for row in report.liabilities], ["Credit Card"])

        self.assertEqual(len(report.trend), 12)
        self.assertEqual(report.trend[-1].month, "2026-11")
        self.assertEqual(report.trend[-1].net_worth_cents, 230_000)
        self.assertEqual(report.change_month_cents, 10_000)
        self.assertEqual(report.year_start_net_worth_cents, 100_000)
        self.assertEqual(report.change_ytd_cents, 130_000)

        debts = {row.name: row for row in report.debts}
        self.assertEqual(set(debts), {"Visa", "Auto Loan"})
        self.assertEqual(debts["Visa"].balance_cents, 30_000)
        self.assertEqual(debts["Visa"].minimum_cents, 5_000)
        self.assertTrue(debts["Visa"].minimum_below_interest is False)
        self.assertIsNotNone(debts["Visa"].payoff_month)
        self.assertEqual(debts["Auto Loan"].balance_cents, 500_000)

        self.assertIsNotNone(report.minimum_plan)
        self.assertIsNotNone(report.extra_plan)
        self.assertTrue(report.minimum_plan.finished)

        self.assertEqual([goal.goal.name for goal in report.goals], ["Emergency fund"])
        self.assertEqual(report.goals[0].progress_cents, 250_000)
        self.assertGreater(report.goals[0].required_monthly_cents, 0)
        self.assertEqual(report.period_label, "As of November 16, 2026")
        self.assertEqual(report.filename, "budget_tracker_net_worth_2026-11-16.pdf")

    def test_liquid_and_other_assets_are_separated(self) -> None:
        self.repo.add_account(Account("Home", AccountKind.OTHER, 500_000))
        self.repo.add_account(Account("Brokerage", AccountKind.OTHER, 75_000, liquid=True))

        report = self.reports.net_worth(today=TODAY)

        self.assertEqual(
            [row.account.name for row in report.liquid_assets],
            ["High-Yield Savings", "Checking", "Brokerage", "Cash"],
        )
        self.assertEqual([row.account.name for row in report.other_assets], ["Home"])
        self.assertEqual(report.liquid_assets_cents, 335_000)
        self.assertEqual(report.other_assets_cents, 500_000)
        self.assertEqual(report.assets_cents, 835_000)
        self.assertEqual(report.net_worth_cents, 805_000)


class TestReportPdf(Base):
    def setUp(self) -> None:
        super().setUp()
        self.savings_account()
        self.income(500_000, date(2026, 10, 1))
        self.spend(120_000, "Groceries", date(2026, 10, 5))
        self.budget("2026-10", 150_000, "Groceries")
        self.budget("2026-11", 150_000, "Groceries")
        self.income(200_000, date(2026, 11, 1))

    def _text(self, data: bytes) -> str:
        with pdfplumber.open(io.BytesIO(data)) as document:
            return "\n".join(page.extract_text() or "" for page in document.pages)

    def test_all_three_reports_render_expected_sections(self) -> None:
        monthly = self.reports.monthly("2026-10", today=TODAY)
        year = self.reports.year(2026, today=TODAY)
        net_worth = self.reports.net_worth(today=TODAY)

        for report, expected in (
            (monthly, ("Monthly Summary", "October 2026", "Budget performance", "Groceries")),
            (year, ("Year in Review", "Month by month", "Against last year")),
            (net_worth, ("Net Worth Statement", "Liabilities", "Debt payoff", "Savings goals")),
        ):
            data = render_report_pdf(report)
            self.assertTrue(data.startswith(b"%PDF"))
            self.assertGreaterEqual(page_count(data), 1)
            text = self._text(data)
            for phrase in expected:
                self.assertIn(phrase, text)

    def test_export_writes_pdf_and_page_image(self) -> None:
        report = self.reports.monthly("2026-10", today=TODAY)
        data = render_report_pdf(report)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / report.filename
            written = export_report_pdf(report, path)
            self.assertEqual(written, path)
            self.assertTrue(path.exists())
            self.assertTrue(path.read_bytes().startswith(b"%PDF"))
            png = page_png(data, 0)
            self.assertTrue(png.startswith(b"\x89PNG"))
            document = pypdfium2.PdfDocument(data)
            try:
                self.assertGreaterEqual(len(document), 1)
            finally:
                document.close()

    def test_atomic_write_replaces_an_existing_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "report.pdf"
            path.write_bytes(b"old")
            write_report_pdf(path, b"%PDF-new")
            self.assertEqual(path.read_bytes(), b"%PDF-new")

    def test_unicode_description_does_not_break_the_pdf(self) -> None:
        self.spend(99_999, "Groceries", date(2026, 10, 20), description="Café — 日本 • 100%")
        report = self.reports.monthly("2026-10", today=TODAY)
        data = render_report_pdf(report)
        self.assertGreaterEqual(page_count(data), 1)

    def test_december_partial_month_is_marked_in_progress(self) -> None:
        report = self.reports.year(2026, today=date(2026, 12, 15))
        text = self._text(render_report_pdf(report))
        self.assertIn("2026 to date", text)
        self.assertIn("January 1 - December 15, 2026", text)
        self.assertIn("In progress", text)
        self.assertIn("Year to date", text)

    def test_legacy_long_debt_name_does_not_break_the_pdf(self) -> None:
        self.repo.add_debt(
            Debt("x" * 1_200, DebtKind.AUTO, 100_000, 490, 5_000)
        )
        report = self.reports.net_worth(today=TODAY)
        data = render_report_pdf(report)
        self.assertGreaterEqual(page_count(data), 1)

    def test_empty_database_still_renders_each_report(self) -> None:
        empty = Repository(connect(":memory:"))
        try:
            reports = ReportService(BudgetService(empty))
            for report in (
                reports.monthly("2026-10", today=TODAY),
                reports.year(2026, today=TODAY),
                reports.net_worth(today=TODAY),
            ):
                data = render_report_pdf(report)
                self.assertTrue(data.startswith(b"%PDF"))
                self.assertGreaterEqual(page_count(data), 1)
        finally:
            empty.close()


class TestReportHelpers(Base):
    def test_available_periods_include_data_and_current_month(self) -> None:
        self.spend(1_000, "Groceries", date(2025, 12, 1))
        self.budget("2025-11", 10_000, "Groceries")

        months = self.reports.available_months(today=TODAY)

        self.assertEqual(months[0], "2026-11")
        self.assertIn("2025-12", months)
        self.assertIn("2025-11", months)
        self.assertEqual(months, sorted(months, reverse=True))
        self.assertIn(2026, self.reports.available_years())

    def test_status_labels_cover_the_four_budget_states(self) -> None:
        self.assertEqual(budget_status_label("over"), "Over")
        self.assertEqual(budget_status_label("at_limit"), "At limit")
        self.assertEqual(budget_status_label("near"), "Near")
        self.assertEqual(budget_status_label("ok"), "On track")
        self.assertEqual(month_status(types.SimpleNamespace(has_budget=False)), "No budget")

    def test_report_kind_labels(self) -> None:
        self.assertEqual(ReportKind.MONTHLY.label, "Monthly summary")
        self.assertEqual(ReportKind.YEAR.label, "Year in review")
        self.assertEqual(ReportKind.NET_WORTH.label, "Net worth statement")
        self.assertIn("budgets", ReportKind.MONTHLY.blurb)


if __name__ == "__main__":
    unittest.main()
