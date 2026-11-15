"""UI behaviour for the Year cash-flow tab: KPIs, chart, events, and the year switch."""

import unittest
from datetime import date, timedelta
from types import SimpleNamespace
from unittest import mock

import flet as ft

from budget_tracker.cashflow import CashEvent, CashFlowForecast, CashFlowPoint
from budget_tracker.db import connect
from budget_tracker.repository import Repository
from budget_tracker.services import BudgetService
from budget_tracker.ui.context import AppContext
from budget_tracker.ui.theme import GAP, set_text_scale

TODAY = date(2026, 11, 14)


class _PageStub:
    """Minimal stand-in for ``ft.Page`` so views can be built without a running app."""

    def __init__(self) -> None:
        self.overlay: list = []
        self.theme_mode = ft.ThemeMode.LIGHT
        self.updates = 0

    def show_dialog(self, control) -> None:
        if isinstance(control, ft.AlertDialog):
            control.open = True
        self.overlay.append(control)

    def pop_dialog(self):
        for control in reversed(self.overlay):
            if isinstance(control, ft.AlertDialog):
                control.open = False
                self.overlay.remove(control)
                return control
        return None

    def update(self, *_) -> None:
        self.updates += 1

    @property
    def snackbars(self) -> list:
        return [control for control in self.overlay if isinstance(control, ft.SnackBar)]


def _walk(control):
    """Yield a control and everything nested below it (rows, columns, containers, segments)."""
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


def _event_row_texts(row: ft.Row) -> list[str]:
    found: list[str] = []
    for node in _walk(row):
        if isinstance(node, ft.Text) and node.value:
            found.append(node.value)
    return found


class _FakeService:
    """Returns a prepared forecast per horizon and records the requested horizons."""

    def __init__(self, forecasts: dict[int, CashFlowForecast]) -> None:
        self.forecasts = forecasts
        self.calls: list[int] = []

    def cash_flow(self, today=None, horizon_days: int = 90) -> CashFlowForecast:
        self.calls.append(horizon_days)
        return self.forecasts.get(horizon_days, self.forecasts[90])


def _forecast(
    horizon: int = 90,
    *,
    checking_id: int,
    cash_id: int,
    events: list[CashEvent] | None = None,
    points: list[CashFlowPoint] | None = None,
    endings: dict | None = None,
    starting_cents: int = 100_000,
    lowest_cents: int | None = None,
    lowest_date: date | None = None,
    has_income: bool = True,
) -> CashFlowForecast:
    if points is None:
        points = [
            CashFlowPoint(TODAY + timedelta(days=offset), starting_cents - offset * 100)
            for offset in range(horizon + 1)
        ]
    if lowest_cents is None:
        if points:
            lowest = min(points, key=lambda point: (point.balance_cents, point.date))
            lowest_cents, lowest_date = lowest.balance_cents, lowest.date
        else:
            lowest_cents, lowest_date = starting_cents, TODAY
    if endings is None:
        endings = {
            checking_id: (60_000, 50_000, 40_000),
            cash_id: (20_000, 20_000, 20_000),
        }
    return CashFlowForecast(
        points=points,
        events=list(events or []),
        starting_cents=starting_cents,
        lowest_date=lowest_date or TODAY,
        lowest_cents=lowest_cents,
        at_30=110_000,
        at_60=120_000,
        at_90=130_000,
        endings_by_account=endings,
        has_income=has_income,
    )


class _Base(unittest.TestCase):
    def setUp(self) -> None:
        self.repo = Repository(connect(":memory:"))
        self.page = _PageStub()
        self.accounts = {account.name: account for account in self.repo.list_accounts()}
        self.checking_id = self.accounts["Checking"].id
        self.cash_id = self.accounts["Cash"].id
        set_text_scale(1.0)

    def tearDown(self) -> None:
        set_text_scale(1.0)
        self.repo.close()

    def _context(self, service=None) -> AppContext:
        return AppContext(page=self.page, repo=self.repo, service=service or BudgetService(self.repo))

    def _panel(self, forecast: CashFlowForecast, *, on_income=None):
        from budget_tracker.ui.views.cash_flow import CashFlowPanel

        panel = CashFlowPanel(self._context(_FakeService({90: forecast})), on_income=on_income)
        panel.refresh()
        return panel


class TestCashFlowPanel(_Base):
    def test_chart_is_a_png_image_at_logical_size(self):
        panel = self._panel(_forecast(checking_id=self.checking_id, cash_id=self.cash_id))
        images = [node for node in _walk(panel.control) if isinstance(node, ft.Image)]
        self.assertEqual(len(images), 1)
        image = images[0]
        self.assertTrue(image.src.startswith("data:image/png;base64,"))
        self.assertEqual((image.width, image.height), (900, 260))

    def test_horizon_changes_kpis_chart_points_events_and_lowest(self):
        first = CashEvent(TODAY + timedelta(days=1), "Payroll", self.checking_id, 50_000, "income")
        bill = CashEvent(TODAY + timedelta(days=2), "Rent", self.checking_id, -30_000, "bill")
        many = [
            CashEvent(TODAY + timedelta(days=index + 1), f"Bill {index}", self.checking_id, -1_000, "bill")
            for index in range(12)
        ]
        forecasts = {
            30: _forecast(30, checking_id=self.checking_id, cash_id=self.cash_id, events=[first, bill], has_income=True),
            60: _forecast(60, checking_id=self.checking_id, cash_id=self.cash_id, events=[first, bill, *many[:3]], has_income=True),
            90: _forecast(90, checking_id=self.checking_id, cash_id=self.cash_id, events=many, has_income=True),
        }
        from budget_tracker.ui.views.cash_flow import CashFlowPanel

        service = _FakeService(forecasts)
        panel = CashFlowPanel(self._context(service))
        panel.refresh()

        self.assertEqual(panel.horizon, 90)
        self.assertEqual(len(panel._points), 91)
        self.assertEqual(len(panel._events), 12)
        labels = _texts(panel.control)
        for expected in ("In 30 days", "In 60 days", "In 90 days", "Today", "Lowest point", "Next 8 of 12"):
            self.assertIn(expected, labels)

        first_src = panel._chart_image.src
        panel._horizon_control.selected = ["30"]
        panel._horizon_control.on_change(_event(selected=["30"]))
        self.assertEqual(panel.horizon, 30)
        self.assertEqual(service.calls[-1], 30)
        self.assertEqual(len(panel._points), 31)
        self.assertEqual(len(panel._events), 2)
        labels = _texts(panel.control)
        self.assertIn("In 30 days", labels)
        self.assertNotIn("In 60 days", labels)
        self.assertNotIn("In 90 days", labels)
        self.assertIn("All 2", labels)
        self.assertEqual(panel._lowest_date, TODAY + timedelta(days=30))
        self.assertIn("$970.00", labels)  # 1000.00 - 30 * 1.00
        self.assertNotEqual(panel._chart_image.src, first_src)
        # Ending balances always keep the full 30/60/90 columns with their real values.
        for header in ("30d", "60d", "90d"):
            self.assertIn(header, labels)
        for value in ("$600.00", "$500.00", "$400.00"):
            self.assertEqual(labels.count(value), 1)

        panel._horizon_control.selected = ["60"]
        panel._horizon_control.on_change(_event(selected=["60"]))
        self.assertEqual(len(panel._points), 61)
        labels = _texts(panel.control)
        self.assertIn("In 60 days", labels)
        self.assertNotIn("In 90 days", labels)
        self.assertIn("All 5", labels)

    def test_kpi_row_wraps_and_does_not_ellipsize_values(self):
        panel = self._panel(_forecast(checking_id=self.checking_id, cash_id=self.cash_id))
        self.assertIsInstance(panel._kpi_row, ft.ResponsiveRow)
        self.assertEqual(len(panel._kpi_row.controls), 5)
        for tile in panel._kpi_row.controls:
            self.assertEqual(tile.col, {"xs": 6, "sm": 4, "lg": 2})
            values = [
                node
                for node in _walk(tile)
                if isinstance(node, ft.Text) and (node.value or "").startswith("$")
            ]
            self.assertEqual(len(values), 1)
            self.assertIsNone(values[0].max_lines)
            self.assertNotEqual(values[0].overflow, ft.TextOverflow.ELLIPSIS)

    @staticmethod
    def _card_inner_width(columns: int) -> float:
        # Smallest ResponsiveRow width that still uses the ``lg`` breakpoint, minus the card padding.
        return (992 - GAP) * columns / 12 - 40

    def test_ending_columns_fit_inside_the_lg_card(self):
        panel = self._panel(_forecast(checking_id=self.checking_id, cash_id=self.cash_id))
        widest = max(
            sum((control.width or 0) for control in row.controls) + 8 * (len(row.controls) - 1)
            for row in panel._ending_rows
        )
        self.assertLessEqual(widest, self._card_inner_width(5))

    def test_event_columns_fit_inside_the_lg_card(self):
        bill = CashEvent(TODAY + timedelta(days=1), "Rent", self.checking_id, -30_000, "bill")
        panel = self._panel(
            _forecast(checking_id=self.checking_id, cash_id=self.cash_id, events=[bill])
        )
        row = panel._event_rows[0]
        fixed = sum((control.width or 0) for control in row.controls if isinstance(control, ft.Text))
        fixed += 8 * (len(row.controls) - 1)
        self.assertLessEqual(fixed, self._card_inner_width(7))

    def test_kpi_row_has_today_lowest_and_only_in_horizon_checkpoints(self):
        panel = self._panel(_forecast(checking_id=self.checking_id, cash_id=self.cash_id))
        labels = _texts(panel._kpi_row)
        titles = [value for value in labels if value in {"Today", "In 30 days", "In 60 days", "In 90 days", "Lowest point"}]
        self.assertEqual(titles, ["Today", "In 30 days", "In 60 days", "In 90 days", "Lowest point"])
        self.assertIn("$1,000.00", labels)
        self.assertIn("$910.00", labels)  # 90-day low from the generated series

        panel._horizon_control.selected = ["30"]
        panel._horizon_control.on_change(_event(selected=["30"]))
        labels = _texts(panel._kpi_row)
        titles = [value for value in labels if value in {"Today", "In 30 days", "In 60 days", "In 90 days", "Lowest point"}]
        self.assertEqual(titles, ["Today", "In 30 days", "Lowest point"])
        self.assertIn("$970.00", labels)
        self.assertNotIn("In 60 days", labels)
        self.assertNotIn("In 90 days", labels)

    def test_events_cap_at_eight_and_show_all_reveals_the_rest(self):
        events = [
            CashEvent(TODAY + timedelta(days=index + 1), f"Bill {index}", self.checking_id, -1_000, "bill")
            for index in range(12)
        ]
        panel = self._panel(_forecast(checking_id=self.checking_id, cash_id=self.cash_id, events=events))
        self.assertEqual(len(panel._event_rows), 12)
        self.assertEqual(sum(isinstance(control, ft.Row) for control in panel._events_list.controls), 8)
        self.assertEqual(panel._show_all_button.content, "Show all")

        panel._show_all_button.on_click(None)

        self.assertEqual(sum(isinstance(control, ft.Row) for control in panel._events_list.controls), 12)
        self.assertEqual(panel._show_all_button.content, "Show fewer")

    def test_events_sort_inflows_before_outflows_on_the_same_day(self):
        events = [
            CashEvent(TODAY + timedelta(days=2), "Rent", self.checking_id, -30_000, "bill"),
            CashEvent(TODAY + timedelta(days=1), "Payroll", self.checking_id, 50_000, "income"),
            CashEvent(TODAY + timedelta(days=2), "Bonus", self.checking_id, 20_000, "income"),
        ]
        panel = self._panel(
            _forecast(checking_id=self.checking_id, cash_id=self.cash_id, events=events, has_income=True)
        )
        ordered = [_event_row_texts(row) for row in panel._event_rows]
        self.assertEqual(ordered[0][1], "Payroll")
        self.assertEqual(ordered[1][1], "Bonus")
        self.assertEqual(ordered[2][1], "Rent")
        # The running balance applies events in the displayed order.
        self.assertIn("$1,500.00", ordered[0])
        self.assertIn("$1,700.00", ordered[1])
        self.assertIn("$1,400.00", ordered[2])

    def test_income_plan_calls_the_injected_callback(self):
        calls = []
        panel = self._panel(
            _forecast(checking_id=self.checking_id, cash_id=self.cash_id),
            on_income=lambda: calls.append(True),
        )
        panel._income_button.on_click(None)
        self.assertEqual(calls, [True])
        self.assertEqual(self.page.snackbars, [])

    def test_income_plan_without_a_callback_notifies_that_it_is_unavailable(self):
        panel = self._panel(_forecast(checking_id=self.checking_id, cash_id=self.cash_id))
        panel._income_button.on_click(None)
        snackbars = self.page.snackbars
        self.assertEqual(len(snackbars), 1)
        self.assertIn("Income plan", snackbars[0].content.value)
        self.assertIn("not available", snackbars[0].content.value)

    def test_theme_and_text_size_refresh_regenerate_the_chart(self):
        panel = self._panel(_forecast(checking_id=self.checking_id, cash_id=self.cash_id))
        light_src = panel._chart_image.src

        self.page.theme_mode = ft.ThemeMode.DARK
        panel.refresh()
        dark_src = panel._chart_image.src
        self.assertNotEqual(dark_src, light_src)

        set_text_scale(1.15)
        panel.refresh()
        large_src = panel._chart_image.src
        self.assertNotEqual(large_src, dark_src)

    def test_preset_refresh_regenerates_the_chart(self):
        panel = self._panel(_forecast(checking_id=self.checking_id, cash_id=self.cash_id))
        indigo_src = panel._chart_image.src

        self.repo.set_setting("theme_preset", "ocean")
        panel.refresh()

        self.assertNotEqual(panel._chart_image.src, indigo_src)

    def test_no_liquid_accounts_shows_the_empty_state_without_a_chart(self):
        panel = self._panel(
            _forecast(
                checking_id=self.checking_id,
                cash_id=self.cash_id,
                points=[],
                endings={},
                starting_cents=0,
                lowest_cents=0,
                has_income=False,
            )
        )
        labels = _texts(panel.control)
        self.assertIn("Add a checking, savings, or cash account to forecast cash flow", labels)
        self.assertIsNone(_find(panel.control, lambda node: isinstance(node, ft.Image)))

    def test_liquid_accounts_without_events_show_a_flat_line_explanation(self):
        panel = self._panel(_forecast(checking_id=self.checking_id, cash_id=self.cash_id, events=[], has_income=False))
        labels = _texts(panel.control)
        self.assertIn("Add income or a bill to see a forecast.", labels)
        self.assertNotIn("Add income to make this forecast realistic", labels)
        self.assertIsNotNone(_find(panel.control, lambda node: isinstance(node, ft.Image)))

    def test_events_without_income_show_the_income_callout(self):
        bill = CashEvent(TODAY + timedelta(days=1), "Rent", self.checking_id, -30_000, "bill")
        panel = self._panel(
            _forecast(checking_id=self.checking_id, cash_id=self.cash_id, events=[bill], has_income=False)
        )
        labels = _texts(panel.control)
        self.assertIn("Add income to make this forecast realistic", labels)
        self.assertIsNotNone(_find(panel.control, lambda node: isinstance(node, ft.Image)))


class TestYearCashFlowSwitch(_Base):
    def _switch(self, view):
        return _find(
            view.control,
            lambda node: isinstance(node, ft.SegmentedButton)
            and {segment.value for segment in node.segments} == {"year", "cash"},
        )

    def test_switch_hides_the_year_dropdown_and_restores_it(self):
        from budget_tracker.ui.views.year import YearView

        view = YearView(self._context())
        self.assertEqual(view.mode, "year")
        self.assertTrue(view.year.visible)
        self.assertIn(f"No data for {date.today().year}", _texts(view.body))

        switch = self._switch(view)
        self.assertIsNotNone(switch)
        switch.selected = ["cash"]
        switch.on_change(_event(selected=["cash"]))

        self.assertEqual(view.mode, "cash")
        self.assertFalse(view.year.visible)
        self.assertIn("Today", _texts(view.body))
        self.assertIn("Next 90 days", _texts(view.control))
        self.assertIn("This year", _texts(view.control))

        switch.selected = ["year"]
        switch.on_change(_event(selected=["year"]))

        self.assertEqual(view.mode, "year")
        self.assertTrue(view.year.visible)
        self.assertIn(f"No data for {date.today().year}", _texts(view.body))

    def test_viewing_and_switching_never_writes_to_the_database(self):
        from budget_tracker.ui.views.year import YearView

        write_methods = (
            "add_transaction",
            "delete_transaction",
            "add_bill",
            "add_income_schedule",
            "update_income_schedule",
            "delete_income_schedule",
            "set_setting",
            "set_budget",
        )
        patches = {
            name: mock.patch.object(self.repo, name) for name in write_methods if hasattr(self.repo, name)
        }
        patched = {name: patcher.start() for name, patcher in patches.items()}
        try:
            view = YearView(self._context())
            switch = self._switch(view)
            switch.selected = ["cash"]
            switch.on_change(_event(selected=["cash"]))
            switch.selected = ["year"]
            switch.on_change(_event(selected=["year"]))
        finally:
            for patcher in patches.values():
                patcher.stop()

        for name, method in patched.items():
            method.assert_not_called()

    def test_data_change_while_viewing_cash_flow_refreshes_the_panel(self):
        from budget_tracker.ui.views.year import YearView

        ctx = self._context()
        view = YearView(ctx)
        switch = self._switch(view)
        switch.selected = ["cash"]
        switch.on_change(_event(selected=["cash"]))
        first_src = view.cash_panel._chart_image.src

        self.page.theme_mode = ft.ThemeMode.DARK
        ctx.data_changed()  # fires the listener registered by YearView

        self.assertNotEqual(view.cash_panel._chart_image.src, first_src)
        self.assertEqual(view.mode, "cash")


if __name__ == "__main__":
    unittest.main()
