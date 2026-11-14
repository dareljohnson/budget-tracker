import unittest
from datetime import date

from budget_tracker.db import connect
from budget_tracker.models import Budget, Transaction, TransactionType
from budget_tracker.repository import Repository
from budget_tracker.services import BudgetService


class Base(unittest.TestCase):
    def setUp(self) -> None:
        self.repo = Repository(connect(":memory:"))
        self.service = BudgetService(self.repo)
        self.accounts = {account.name: account for account in self.repo.list_accounts()}
        self.categories = {category.name: category for category in self.repo.list_categories()}

    def tearDown(self) -> None:
        self.repo.close()

    def budget(self, month: str, cents: int, category: str = "Groceries") -> None:
        self.repo.set_budget(Budget(self.categories[category].id, month, cents))

    def spend(self, cents: int, day: date, category: str = "Groceries") -> None:
        self.repo.add_transaction(
            Transaction(
                cents,
                TransactionType.EXPENSE,
                self.categories[category].id,
                day,
                "Spending",
                account_id=self.accounts["Checking"].id,
            )
        )

    def income(self, cents: int, day: date) -> None:
        self.repo.add_transaction(
            Transaction(
                cents,
                TransactionType.INCOME,
                self.categories["Salary"].id,
                day,
                "Income",
                account_id=self.accounts["Checking"].id,
            )
        )


class TestYearOverview(Base):
    def test_sums_budgets_spending_and_cash_flow(self):
        self.budget("2026-01", 100_000)
        self.budget("2026-02", 120_000)
        self.spend(110_000, date(2026, 1, 10))
        self.spend(90_000, date(2026, 2, 10))
        self.income(400_000, date(2026, 1, 1))

        overview = self.service.year_overview(2026, today=date(2026, 2, 15))

        self.assertEqual(len(overview.months), 12)
        january = overview.months[0]
        self.assertEqual((january.budget_cents, january.has_budget, january.spending_cents), (100_000, True, 110_000))
        self.assertEqual(january.difference_cents, -10_000)
        self.assertEqual((overview.ytd_budget_cents, overview.ytd_spending_cents, overview.ytd_income_cents), (220_000, 200_000, 400_000))
        self.assertEqual(overview.ytd_difference_cents, 20_000)
        self.assertEqual(overview.budgeted_months, 2)
        self.assertEqual(overview.forecast_spending_cents, 1_200_000)
        self.assertIsNone(overview.prior_spending_cents)

    def test_compares_with_the_same_period_last_year(self):
        self.spend(50_000, date(2025, 1, 10))
        self.spend(40_000, date(2025, 2, 10))
        self.spend(70_000, date(2026, 1, 10))
        self.spend(90_000, date(2026, 2, 10))

        overview = self.service.year_overview(2026, today=date(2026, 2, 15))

        self.assertEqual(overview.prior_spending_cents, 90_000)
        self.assertEqual(overview.ytd_spending_cents, 160_000)

    def test_past_year_has_full_totals_and_no_projection(self):
        self.spend(10_000, date(2025, 6, 1))
        self.spend(20_000, date(2025, 12, 1))

        overview = self.service.year_overview(2025, today=date(2026, 2, 15))

        self.assertEqual(overview.elapsed_months, 12)
        self.assertEqual(overview.ytd_spending_cents, 30_000)
        self.assertIsNone(overview.forecast_spending_cents)

    def test_future_year_is_empty(self):
        overview = self.service.year_overview(2027, today=date(2026, 2, 15))
        self.assertEqual(overview.elapsed_months, 0)
        self.assertEqual(overview.ytd_spending_cents, 0)
        self.assertIsNone(overview.forecast_spending_cents)
        self.assertFalse(overview.has_data)

    def test_month_without_a_budget_still_shows_spending(self):
        self.spend(30_000, date(2026, 3, 5))
        overview = self.service.year_overview(2026, today=date(2026, 3, 15))
        march = overview.months[2]
        self.assertFalse(march.has_budget)
        self.assertEqual((march.budget_cents, march.spending_cents), (0, 30_000))
        self.assertEqual(overview.ytd_spending_cents, 30_000)
        self.assertEqual(overview.ytd_budget_cents, 0)

    def test_available_years_includes_data_and_the_current_year(self):
        self.spend(1_000, date(2024, 5, 1))
        self.budget("2025-01", 10_000)
        years = self.service.available_years()
        self.assertIn(2024, years)
        self.assertIn(2025, years)
        self.assertIn(date.today().year, years)
        self.assertEqual(years, sorted(years))

    def test_empty_year_has_no_data(self):
        overview = self.service.year_overview(2026, today=date(2026, 2, 15))
        self.assertFalse(overview.has_data)
        self.assertIsNone(overview.forecast_spending_cents)
