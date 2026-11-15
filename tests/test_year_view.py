import unittest
from datetime import date
from types import SimpleNamespace

try:
    import flet as ft
except ImportError:  # pragma: no cover - UI tests need Flet installed
    ft = None

from budget_tracker.db import connect
from budget_tracker.models import Budget, Transaction, TransactionType
from budget_tracker.repository import Repository
from budget_tracker.services import BudgetService
from budget_tracker.ui.context import AppContext
from budget_tracker.ui.theme import set_text_scale


class _PageStub:
    """Minimal stand-in for ``ft.Page`` so views can be built without a running app."""

    def __init__(self) -> None:
        self.overlay: list = []
        self.theme_mode = ft.ThemeMode.LIGHT
        self.updates = 0

    def show_dialog(self, control) -> None:
        self.overlay.append(control)

    def pop_dialog(self) -> None:
        if self.overlay:
            self.overlay.pop()

    def update(self, *_) -> None:
        self.updates += 1


def _walk(control):
    """Yield a control and everything nested below it."""
    yield control
    for attr in ("controls", "content", "actions", "segments", "label"):
        value = getattr(control, attr, None)
        if isinstance(value, list):
            for child in value:
                if isinstance(child, ft.Control):
                    yield from _walk(child)
        elif isinstance(value, ft.Control):
            yield from _walk(value)


def _texts(control) -> list[str]:
    return [node.value for node in _walk(control) if isinstance(node, ft.Text) and node.value]


def _find(control, predicate):
    return next((node for node in _walk(control) if predicate(node)), None)


def _event(*, selected):
    return SimpleNamespace(control=SimpleNamespace(selected=selected))


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


@unittest.skipIf(ft is None, "flet is not installed")
class TestYearViewComposition(Base):
    """The redesigned "This year" composition: toolbar, summary, chart, focus and table."""

    def setUp(self) -> None:
        super().setUp()
        self.page = _PageStub()
        self.ctx = AppContext(page=self.page, repo=self.repo, service=self.service)
        self.year = date.today().year
        self.current_month = self.service.current_month()
        set_text_scale(1.0)

    def tearDown(self) -> None:
        set_text_scale(1.0)
        super().tearDown()

    def view(self, **kwargs):
        from budget_tracker.ui.views.year import YearView

        return YearView(self.ctx, **kwargs)

    def seed_current_month(self) -> None:
        self.budget(self.current_month, 50_000)
        self.spend(60_000, date.today())

    # ---------- toolbar ----------
    def test_header_has_one_toolbar_row_holding_the_year_and_mode_switch(self):
        view = self.view()
        header = view.control.controls[0]

        self.assertIsInstance(header, ft.ResponsiveRow)
        self.assertEqual(len(header.controls), 2)
        toolbar = _find(
            header,
            lambda node: isinstance(node, ft.Container)
            and isinstance(node.content, ft.Row)
            and view.year in node.content.controls,
        )
        self.assertIsNotNone(toolbar)
        row = toolbar.content
        self.assertIn(view.mode_switch, row.controls)
        self.assertTrue(row.wrap)
        self.assertEqual(row.alignment, ft.MainAxisAlignment.END)

    def test_toolbar_keeps_the_year_and_switch_on_one_row_at_1024(self):
        from budget_tracker.ui.theme import GAP, fs

        view = self.view()
        self.assertEqual(view._toolbar.col, {"xs": 12, "md": 6, "lg": 5})

        # 1024px window minus the 120px rail, divider and 64px page padding, halved at md.
        available = (1024 - 120 - 1 - 64 - GAP) * 6 / 12
        # The dropdown plus two segment labels (six characters at the 14px size) and their
        # horizontal padding; deliberately conservative so this fails before the row wraps.
        required = view.year.width + 8 + 2 * (fs(14) * 6 + 24)
        self.assertGreaterEqual(available, required)

    # ---------- summary strip ----------
    def test_summary_strip_has_four_compact_tiles(self):
        self.seed_current_month()
        view = self.view()
        kpis = view.body.controls[0]

        self.assertIsInstance(kpis, ft.ResponsiveRow)
        self.assertEqual(len(kpis.controls), 4)
        for tile in kpis.controls:
            # Four across from ``md`` up, two columns below it: never a lone fourth tile.
            self.assertEqual(tile.col, {"xs": 6, "sm": 6, "md": 3, "lg": 3})
            self.assertIsNone(_find(tile, lambda node: isinstance(node, ft.Icon)))

        titles = [
            node.value
            for tile in kpis.controls
            for node in _walk(tile)
            if isinstance(node, ft.Text)
            and node.value in {"YTD budget", "YTD spending", "Under budget", "Over budget", "Year-end pace"}
        ]
        self.assertEqual(
            titles[:2] + titles[3:],
            ["YTD budget", "YTD spending", "Year-end pace"],
        )
        self.assertIn(titles[2], {"Under budget", "Over budget"})
        from budget_tracker.ui.views.year import YearView, _months_label

        self.assertIn(f"Estimate · {_months_label(date.today().month)} elapsed", _texts(kpis))

    def test_summary_tile_helper_is_compact(self):
        from budget_tracker.ui.components import summary_tile

        tile = summary_tile("YTD budget", "$100.00", "3 month(s) budgeted")
        self.assertEqual(tile.col, {"xs": 6, "sm": 6, "md": 3, "lg": 3})
        self.assertIsNone(_find(tile, lambda node: isinstance(node, ft.Icon)))
        self.assertEqual(_texts(tile), ["YTD budget", "$100.00", "3 month(s) budgeted"])

    # ---------- coverage banner ----------
    def test_coverage_banner_appears_under_three_budgeted_months_and_reviews_budgets(self):
        self.budget(self.current_month, 50_000)
        calls: list[bool] = []
        view = self.view(on_view_budgets=lambda: calls.append(True))

        texts = _texts(view.body)
        self.assertIn("Only 1 of 12 months has a budget. The year-end pace is an estimate.", texts)
        button = _find(
            view.body,
            lambda node: isinstance(node, ft.TextButton) and node.content == "Review budgets",
        )
        self.assertIsNotNone(button)
        button.on_click(None)
        self.assertEqual(calls, [True])
        self.assertEqual(view.body.controls[1].data, "coverage-banner")
        # The banner is a plain Row: a wrapping Row with an expanding child fails to paint in
        # Flet 1.1.2 and collapses the whole Year body to a gray card.
        banner_row = view.body.controls[1].content
        self.assertIsInstance(banner_row, ft.Row)
        self.assertFalse(banner_row.wrap)
        self.assertTrue(banner_row.controls[1].expand)

    def test_coverage_banner_is_absent_for_a_fully_budgeted_year(self):
        for month in range(1, 13):
            self.budget(f"{self.year}-{month:02d}", 50_000)
        view = self.view()

        self.assertIsNone(
            _find(
                view.body,
                lambda node: isinstance(node, ft.TextButton) and node.content == "Review budgets",
            )
        )
        self.assertFalse(any("year-end pace is an estimate" in text for text in _texts(view.body)))

    def test_coverage_banner_is_absent_with_three_budgeted_months(self):
        for month in ("01", "02", "03"):
            self.budget(f"{self.year}-{month}", 50_000)
        view = self.view()
        overview = self.service.year_overview(self.year)
        self.assertGreaterEqual(sum(1 for month in overview.months if month.has_budget), 3)
        self.assertIsNone(view._coverage_banner(overview))

    def test_fully_budgeted_future_year_has_no_coverage_banner(self):
        future = self.year + 1
        for month in range(1, 13):
            self.budget(f"{future}-{month:02d}", 10_000)
        view = self.view()
        view.year.value = str(future)
        view.refresh()

        overview = self.service.year_overview(future)
        self.assertEqual(overview.budgeted_months, 0)  # elapsed-scoped, and why the banner counts the year
        self.assertIsNone(view._coverage_banner(overview))
        self.assertFalse(any("of 12 months" in text for text in _texts(view.body)))

    def test_past_year_coverage_banner_omits_the_year_end_pace_estimate(self):
        past = self.year - 1
        self.budget(f"{past}-06", 10_000)
        view = self.view()
        view.year.value = str(past)
        view.refresh()

        self.assertIsNone(self.service.year_overview(past).forecast_spending_cents)
        texts = _texts(view.body)
        self.assertIn("Only 1 of 12 months has a budget.", texts)
        self.assertFalse(any("estimate" in text.lower() for text in texts))

    # ---------- cumulative chart ----------
    def test_chart_is_a_contained_decoration_image_inside_its_card(self):
        self.seed_current_month()
        view = self.view()

        chart = view._chart_image
        self.assertIsInstance(chart, ft.Container)
        self.assertIsInstance(chart.image, ft.DecorationImage)
        self.assertTrue(chart.image.src.startswith("data:image/png;base64,"))
        # CONTAIN keeps the baked-axis labels at their aspect ratio instead of stretching them.
        self.assertEqual(chart.image.fit, ft.BoxFit.CONTAIN)
        self.assertEqual(chart.height, 260)
        chart_card = view.body.controls[-2].controls[0]
        chart_row = chart_card.content.controls[1]
        self.assertIs(chart_row.controls[0], chart)

        texts = _texts(view.body)
        self.assertIn("Budget vs actual, cumulative", texts)
        self.assertIn("Budget", texts)
        self.assertIn("Spending", texts)
        self.assertIn("Only months with activity change the line.", texts)

    def test_cumulative_months_pass_monthly_values_to_the_renderer(self):
        self.budget(f"{self.year}-01", 100_000)
        self.budget(f"{self.year}-02", 50_000)
        self.spend(70_000, date(self.year, 1, 10))
        self.spend(40_000, date(self.year, 2, 10))
        view = self.view()
        overview = self.service.year_overview(self.year)

        months = view._cumulative_months(overview)
        self.assertEqual(len(months), 12)
        # The renderer owns the running total; these are the month's own values.
        self.assertEqual((months[0].budget_cents, months[0].spending_cents), (100_000, 70_000))
        self.assertEqual((months[1].budget_cents, months[1].spending_cents), (50_000, 40_000))
        self.assertEqual((months[-1].budget_cents, months[-1].spending_cents), (0, 0))
        self.assertTrue(months[int(self.current_month[-2:]) - 1].is_current)
        self.assertEqual(sum(month.is_current for month in months), 1)

    def test_chart_follows_theme_and_text_scale(self):
        self.seed_current_month()
        view = self.view()
        light_src = view._chart_image.image.src

        self.page.theme_mode = ft.ThemeMode.DARK
        view.refresh()
        dark_src = view._chart_image.image.src
        self.assertNotEqual(dark_src, light_src)

        set_text_scale(1.15)
        view.refresh()
        large_src = view._chart_image.image.src
        self.assertNotEqual(large_src, dark_src)

    # ---------- focus card ----------
    def test_focus_card_names_the_current_month_and_shows_budget_spent_and_categories(self):
        self.seed_current_month()
        view = self.view()

        texts = _texts(view.body)
        from budget_tracker.formatting import month_label

        self.assertIn(month_label(self.current_month), texts)
        self.assertIn("$600.00", texts)
        self.assertIn("$500.00", texts)
        self.assertIn("$100.00 over budget", texts)
        self.assertIn("Groceries", texts)

    def test_focus_card_falls_back_to_the_latest_month_with_data(self):
        from budget_tracker.formatting import month_label

        self.spend(30_000, date(self.year, 2, 10))
        view = self.view()

        texts = _texts(view.body)
        self.assertIn(f"February {self.year}", texts)
        if self.current_month != f"{self.year}-02":
            self.assertNotIn(month_label(self.current_month), texts)

    def test_focus_card_selects_the_current_month_with_only_income(self):
        from budget_tracker.formatting import month_label

        self.income(400_000, date.today())
        view = self.view()

        texts = _texts(view.body)
        self.assertIn(month_label(self.current_month), texts)
        self.assertIn("Spent $0.00", texts)
        self.assertIn("This month", texts)

    def test_focus_card_lists_at_most_three_categories(self):
        names = ("Groceries", "Dining Out", "Transportation", "Utilities")
        for index, name in enumerate(names):
            self.budget(self.current_month, 10_000, category=name)
            self.spend((index + 1) * 1_000, date.today(), category=name)
        view = self.view()

        focus = _find(
            view.body,
            lambda node: isinstance(node, ft.Column)
            and any(isinstance(child, ft.Text) and child.value == "Top categories" for child in node.controls),
        )
        self.assertIsNotNone(focus)
        listed = [name for name in names if name in _texts(focus)]
        self.assertEqual(len(listed), 3)
        self.assertIn("Utilities", listed)  # largest category is kept
        self.assertNotIn("Groceries", listed)  # smallest is dropped

    # ---------- table ----------
    def test_table_defaults_to_active_months_plus_the_current_month_and_ytd(self):
        self.budget(f"{self.year}-01", 50_000)
        self.spend(10_000, date(self.year, 2, 10))
        view = self.view()

        from budget_tracker.formatting import short_month_label

        texts = _texts(view.body)
        self.assertIn("Jan", texts)
        self.assertIn("Feb", texts)
        self.assertIn(short_month_label(self.current_month), texts)  # current month, even without activity
        self.assertNotIn("Mar", texts)
        self.assertIn("YTD", texts)
        self.assertIsNotNone(
            _find(
                view.body,
                lambda node: isinstance(node, ft.TextButton)
                and node.content == "Show all 12 months",
            )
        )

    def test_show_all_twelve_months_toggles_all_columns_and_collapses_again(self):
        self.budget(f"{self.year}-01", 50_000)
        view = self.view()
        self.assertNotIn("Dec", _texts(view.body))

        show_all = _find(
            view.body,
            lambda node: isinstance(node, ft.TextButton) and node.content == "Show all 12 months",
        )
        show_all.on_click(None)

        texts = _texts(view.body)
        for label in ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"):
            self.assertIn(label, texts)
        collapse = _find(
            view.body,
            lambda node: isinstance(node, ft.TextButton) and node.content == "Show active months",
        )
        self.assertIsNotNone(collapse)
        collapse.on_click(None)

        texts = _texts(view.body)
        self.assertNotIn("Dec", texts)
        self.assertIsNotNone(
            _find(
                view.body,
                lambda node: isinstance(node, ft.TextButton)
                and node.content == "Show all 12 months",
            )
        )
        self.assertTrue(view._show_all_months is False)

    def test_table_columns_fit_the_card_at_the_900px_minimum_window(self):
        self.seed_current_month()
        view = self.view()
        table_card = view.body.controls[-1]
        header = next(
            control
            for control in table_card.content.controls
            if isinstance(control, ft.Row) and len(control.controls) == 6
        )
        width = sum(control.width or 0 for control in header.controls) + 8 * (
            len(header.controls) - 1
        )
        # 900px window minus the 120px rail, divider, 64px page padding and 40px card padding.
        self.assertLessEqual(width, 900 - 120 - 1 - 64 - 40)

    def test_past_year_table_keeps_the_year_total_row(self):
        from budget_tracker.formatting import short_month_label

        self.spend(10_000, date(self.year - 1, 6, 1))
        view = self.view()
        view.year.value = str(self.year - 1)
        view.refresh()

        texts = _texts(view.body)
        self.assertIn("Year total", texts)
        self.assertIn("Jun", texts)
        self.assertNotIn(short_month_label(self.current_month), texts)  # current month is another year

    # ---------- empty and mode switching ----------
    def test_empty_year_keeps_the_existing_empty_state(self):
        view = self.view()
        texts = _texts(view.body)
        self.assertIn(f"No data for {self.year}", texts)
        self.assertIn(
            "Add transactions and budgets, and this page will show monthly budget "
            "performance, cash flow and a year-end projection.",
            texts,
        )

    def test_switching_to_cash_and_back_restores_the_year_content(self):
        self.seed_current_month()
        view = self.view()
        before = _texts(view.body)
        self.assertIn("Budget vs actual, cumulative", before)

        view.mode_switch.selected = ["cash"]
        view.mode_switch.on_change(_event(selected=["cash"]))
        self.assertEqual(view.mode, "cash")
        self.assertIs(view.body.controls[0], view.cash_panel.control)

        view.mode_switch.selected = ["year"]
        view.mode_switch.on_change(_event(selected=["year"]))
        self.assertEqual(view.mode, "year")
        self.assertIn("Budget vs actual, cumulative", _texts(view.body))
        self.assertEqual(len(view.body.controls), 4)
