"""UI behaviour for the Year cash-flow tab: KPIs, chart, events, and the year switch."""

import unittest
from datetime import date, timedelta
from types import SimpleNamespace
from unittest import mock

import flet as ft

from budget_tracker.cashflow import CashEvent, CashFlowForecast, CashFlowPoint, income_dates
from budget_tracker.db import connect
from budget_tracker.models import IncomeFrequency, IncomeSchedule
from budget_tracker.repository import Repository, RepositoryError
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
    def test_header_uses_responsive_row_so_it_does_not_render_blank(self):
        panel = self._panel(_forecast(checking_id=self.checking_id, cash_id=self.cash_id))
        header = panel.control.controls[0]
        self.assertIsInstance(header, ft.ResponsiveRow)
        self.assertFalse(getattr(header.controls[0], "expand", False))

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

    def test_income_plan_without_a_callback_opens_the_income_dialog(self):
        from budget_tracker.ui.views.cash_flow import CashFlowPanel

        panel = CashFlowPanel(self._context())
        panel.refresh()
        panel._income_button.on_click(None)
        dialogs = [control for control in self.page.overlay if isinstance(control, ft.AlertDialog)]
        self.assertEqual(len(dialogs), 1)
        self.assertTrue(dialogs[0].open)
        self.assertEqual(self.page.snackbars, [])

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

    def test_data_change_refreshes_the_cash_panel_only_once(self):
        from budget_tracker.ui.views.year import YearView

        ctx = self._context()
        view = YearView(ctx)
        switch = self._switch(view)
        switch.selected = ["cash"]
        switch.on_change(_event(selected=["cash"]))

        with mock.patch.object(
            view.cash_panel, "refresh", wraps=view.cash_panel.refresh
        ) as refresh:
            ctx.data_changed()

        self.assertEqual(refresh.call_count, 1)

    def test_data_change_in_year_mode_does_not_rebuild_the_hidden_cash_panel(self):
        from budget_tracker.ui.views.year import YearView

        ctx = self._context()
        view = YearView(ctx)
        self.assertEqual(view.mode, "year")

        with mock.patch.object(
            view.cash_panel, "refresh", wraps=view.cash_panel.refresh
        ) as refresh:
            ctx.data_changed()

        self.assertEqual(refresh.call_count, 0)
        self.assertEqual(view.mode, "year")


class TestIncomePlanDialog(_Base):
    """The combined income schedule list and inline editor."""

    def setUp(self) -> None:
        super().setUp()
        self.ctx = self._context()
        self.today = date.today()

    def _dialog(self, *, on_close=None):
        from budget_tracker.ui.views.cash_flow import IncomePlanDialog

        dialog = IncomePlanDialog(self.ctx, on_close=on_close)
        dialog.open()
        return dialog

    def _schedule(self, **overrides) -> IncomeSchedule:
        values = {
            "name": "Payroll",
            "amount_cents": 241_000,
            "frequency": IncomeFrequency.BIWEEKLY,
            "next_date": self.today + timedelta(days=1),
            "anchor_day": (self.today + timedelta(days=1)).day,
            "account_id": self.checking_id,
            "active": True,
        }
        values.update(overrides)
        return self.ctx.service.add_income_schedule(IncomeSchedule(**values))

    def _fill_valid(self, dialog, *, name: str = "Salary", amount: str = "100") -> None:
        day = self.today.day
        dialog.name.value = name
        dialog.amount.value = amount
        dialog.frequency.value = IncomeFrequency.MONTHLY.value
        dialog.next_date.value = self.today.isoformat()
        dialog.anchor.value = str(day)
        dialog._frequency_changed(None)

    def _edit_row(self, dialog):
        row = dialog.list_column.controls[0]
        return row.controls[-1]

    # ---------- list ----------
    def test_list_shows_amount_active_state_and_the_computed_next_occurrence(self):
        past = self.today - timedelta(days=1)
        payroll = self._schedule(next_date=past, anchor_day=past.day)
        self._schedule(
            name="Old contract",
            amount_cents=60_000,
            frequency=IncomeFrequency.MONTHLY,
            next_date=date(2025, 1, 1),
            anchor_day=1,
            active=False,
        )

        dialog = self._dialog()

        self.assertEqual(dialog.dialog.content.width, 520)
        self.assertTrue(dialog.dialog.scrollable)
        texts = _texts(dialog.list_column)
        self.assertIn("Payroll", texts)
        self.assertIn("Old contract", texts)
        self.assertIn("$2,410.00", texts)
        self.assertIn("$600.00", texts)
        self.assertIn("Active", texts)
        self.assertIn("Inactive", texts)

        computed = income_dates(payroll, self.today + timedelta(days=1), self.today + timedelta(days=365))[0]
        self.assertGreater(computed, self.today)
        joined = " ".join(texts)
        self.assertIn("into Checking", joined)
        self.assertIn(f"next {computed:%b} {computed.day}", joined)
        self.assertNotIn(f"next {past:%b} {past.day}", joined)

    # ---------- defaults and visibility ----------
    def test_add_income_resets_the_editor_to_the_documented_defaults(self):
        dialog = self._dialog()
        self.assertFalse(dialog.editor.visible)
        self.assertFalse(dialog.save_button.visible)

        dialog.add_button.on_click(None)

        self.assertTrue(dialog.editor.visible)
        self.assertTrue(dialog.save_button.visible)
        self.assertFalse(dialog.delete_button.visible)
        self.assertEqual(dialog.frequency.value, IncomeFrequency.MONTHLY.value)
        self.assertEqual(dialog.anchor.value, str(self.today.day))
        self.assertEqual(dialog.account.value, str(self.checking_id))
        self.assertTrue(dialog.active.value)

    def test_default_deposit_account_is_first_checking_then_first_liquid(self):
        dialog = self._dialog()
        dialog.add_button.on_click(None)
        self.assertEqual(dialog.account.value, str(self.checking_id))

        cash = self.accounts["Cash"]
        dialog.accounts = [cash]
        dialog._account_by_id = {cash.id: cash}
        dialog._start_add(None)

        self.assertEqual(dialog.account.value, str(cash.id))

    def test_frequency_drives_anchor_and_second_day_visibility(self):
        dialog = self._dialog()
        dialog.add_button.on_click(None)

        dialog.frequency.value = IncomeFrequency.WEEKLY.value
        dialog._frequency_changed(None)
        self.assertFalse(dialog.anchor.visible)
        self.assertFalse(dialog.second_day.visible)

        dialog.frequency.value = IncomeFrequency.MONTHLY.value
        dialog._frequency_changed(None)
        self.assertTrue(dialog.anchor.visible)
        self.assertFalse(dialog.second_day.visible)

        dialog.frequency.value = IncomeFrequency.SEMIMONTHLY.value
        dialog._frequency_changed(None)
        self.assertTrue(dialog.anchor.visible)
        self.assertTrue(dialog.second_day.visible)

    # ---------- add + validation ----------
    def test_add_saves_a_valid_schedule_and_closes_the_dialog(self):
        dialog = self._dialog()
        dialog.add_button.on_click(None)
        self._fill_valid(dialog, name="Salary", amount="1,200.50")

        dialog.save_button.on_click(None)

        [saved] = self.ctx.service.list_income_schedules()
        self.assertEqual((saved.name, saved.amount_cents), ("Salary", 120_050))
        self.assertEqual(saved.frequency, IncomeFrequency.MONTHLY)
        self.assertEqual(saved.account_id, self.checking_id)
        self.assertTrue(saved.active)
        self.assertEqual(self.page.overlay, [])

    def test_save_is_inert_while_the_editor_is_hidden(self):
        dialog = self._dialog()
        self.assertFalse(dialog.save_button.visible)

        dialog.save_button.on_click(None)

        self.assertEqual(self.ctx.service.list_income_schedules(), [])
        self.assertIn(dialog.dialog, self.page.overlay)
        self.assertFalse(dialog.name.error)
        self.assertFalse(dialog.amount.error)
        self.assertFalse(dialog.error.visible)

    def test_double_click_save_inserts_exactly_one_schedule(self):
        dialog = self._dialog()
        dialog.add_button.on_click(None)
        self._fill_valid(dialog)
        calls: list[IncomeSchedule] = []
        original = self.ctx.service.add_income_schedule

        def record(schedule: IncomeSchedule) -> IncomeSchedule:
            calls.append(schedule)
            return original(schedule)

        with mock.patch.object(
            self.ctx.service, "add_income_schedule", side_effect=record
        ):
            dialog.save_button.on_click(None)
            dialog.save_button.on_click(None)

        self.assertEqual(len(calls), 1)
        self.assertEqual(len(self.ctx.service.list_income_schedules()), 1)

    def test_add_validates_each_field_inline_and_keeps_the_dialog_open(self):
        dialog = self._dialog()
        dialog.add_button.on_click(None)
        card_id = self.accounts["Credit Card"].id

        dialog.name.value = "   "
        dialog._save(None)
        self.assertIn("Name is required", dialog.name.error)
        self.assertIn(dialog.dialog, self.page.overlay)

        dialog.name.value = "x" * 61
        dialog._save(None)
        self.assertIn("60", dialog.name.error)

        dialog.name.value = "Salary"
        dialog.amount.value = "0"
        dialog._save(None)
        self.assertTrue(dialog.amount.error)

        dialog.amount.value = "100"
        dialog.frequency.value = "hourly"
        dialog._save(None)
        self.assertTrue(dialog.frequency.error_text)

        dialog.frequency.value = IncomeFrequency.MONTHLY.value
        dialog.anchor.value = "32"
        dialog._save(None)
        self.assertTrue(dialog.anchor.error)

        dialog.anchor.value = str(self.today.day)
        dialog.next_date.value = "not-a-date"
        dialog._save(None)
        self.assertTrue(dialog.next_date.error)

        dialog.next_date.value = self.today.isoformat()
        dialog.account.value = str(card_id)
        dialog._save(None)
        self.assertIn("checking, savings or cash", dialog.account.error_text)

        self.assertEqual(self.ctx.service.list_income_schedules(), [])
        self.assertIn(dialog.dialog, self.page.overlay)

    def test_monthly_next_date_must_fall_on_the_anchor_day(self):
        dialog = self._dialog()
        dialog.add_button.on_click(None)
        dialog.name.value = "Salary"
        dialog.amount.value = "100"
        dialog.frequency.value = IncomeFrequency.MONTHLY.value
        dialog._frequency_changed(None)
        dialog.next_date.value = date(self.today.year, self.today.month, 14).isoformat()
        dialog._next_date_changed(None)
        dialog.anchor.value = "15"
        dialog._anchor_changed(None)

        dialog._save(None)

        self.assertIn("anchor day", dialog.next_date.error)
        self.assertEqual(self.ctx.service.list_income_schedules(), [])
        self.assertIn(dialog.dialog, self.page.overlay)

        dialog.next_date.value = date(self.today.year, self.today.month, 15).isoformat()
        dialog._save(None)
        [saved] = self.ctx.service.list_income_schedules()
        self.assertEqual((saved.next_date.day, saved.anchor_day), (15, 15))

    def test_monthly_next_date_accepts_a_month_clamped_anchor_day(self):
        dialog = self._dialog()
        dialog.add_button.on_click(None)
        dialog.name.value = "Salary"
        dialog.amount.value = "100"
        dialog.frequency.value = IncomeFrequency.MONTHLY.value
        dialog._frequency_changed(None)
        dialog.anchor.value = "31"
        dialog._anchor_changed(None)

        for next_date in (date(2026, 11, 30), date(2027, 2, 28)):
            with self.subTest(next_date=next_date):
                dialog.next_date.value = next_date.isoformat()
                dialog._next_date_changed(None)
                schedule = dialog._validate()
                self.assertIsNotNone(schedule)
                self.assertFalse(dialog.next_date.error)
                self.assertEqual((schedule.next_date, schedule.anchor_day), (next_date, 31))

    def test_semimonthly_next_date_accepts_a_month_clamped_income_day(self):
        dialog = self._dialog()
        dialog.add_button.on_click(None)
        dialog.name.value = "Consulting"
        dialog.amount.value = "600"
        dialog.frequency.value = IncomeFrequency.SEMIMONTHLY.value
        dialog._frequency_changed(None)
        dialog.anchor.value = "15"
        dialog._anchor_changed(None)
        dialog.second_day.value = "31"
        dialog._second_day_changed(None)

        for next_date in (date(2026, 11, 30), date(2027, 2, 28)):
            with self.subTest(next_date=next_date):
                dialog.next_date.value = next_date.isoformat()
                dialog._next_date_changed(None)
                schedule = dialog._validate()
                self.assertIsNotNone(schedule)
                self.assertFalse(dialog.next_date.error)
                self.assertEqual(
                    (schedule.next_date, schedule.anchor_day, schedule.second_day),
                    (next_date, 15, 31),
                )

    def test_semimonthly_requires_a_distinct_second_day_and_a_matching_next_date(self):
        dialog = self._dialog()
        dialog.add_button.on_click(None)
        dialog.name.value = "Consulting"
        dialog.amount.value = "600"
        dialog.frequency.value = IncomeFrequency.SEMIMONTHLY.value
        dialog._frequency_changed(None)
        dialog.next_date.value = date(self.today.year, self.today.month, 15).isoformat()
        dialog._next_date_changed(None)

        dialog.anchor.value = "15"
        dialog.second_day.value = ""
        dialog._anchor_changed(None)
        dialog._save(None)
        self.assertTrue(dialog.second_day.error)

        dialog.second_day.value = "15"
        dialog._save(None)
        self.assertIn("different", dialog.second_day.error)

        dialog.second_day.value = "1"
        dialog.next_date.value = date(self.today.year, self.today.month, 14).isoformat()
        dialog._save(None)
        self.assertIn("income days", dialog.next_date.error)

        dialog.next_date.value = date(self.today.year, self.today.month, 1).isoformat()
        dialog._save(None)
        [saved] = self.ctx.service.list_income_schedules()
        self.assertEqual((saved.anchor_day, saved.second_day), (15, 1))

    # ---------- edit / delete ----------
    def test_edit_fills_the_editor_and_updates_the_active_toggle(self):
        schedule = self._schedule(
            name="Payroll",
            frequency=IncomeFrequency.MONTHLY,
            next_date=date(self.today.year, self.today.month, 15),
            anchor_day=15,
        )
        dialog = self._dialog()

        self._edit_row(dialog).on_click(None)

        self.assertTrue(dialog.editor.visible)
        self.assertTrue(dialog.delete_button.visible)
        self.assertEqual(dialog.name.value, "Payroll")
        self.assertEqual(dialog.amount.value, "2410.00")
        self.assertEqual(dialog.frequency.value, IncomeFrequency.MONTHLY.value)
        self.assertEqual(dialog.anchor.value, "15")

        dialog.name.value = "Payroll (renamed)"
        dialog.active.value = False
        dialog._save(None)

        saved = self.ctx.service.get_income_schedule(schedule.id)
        self.assertEqual(saved.name, "Payroll (renamed)")
        self.assertFalse(saved.active)

    def test_delete_confirms_then_removes_the_schedule(self):
        schedule = self._schedule()
        dialog = self._dialog()
        self._edit_row(dialog).on_click(None)

        dialog.delete_button.on_click(None)
        [confirm] = [
            c
            for c in self.page.overlay
            if isinstance(c, ft.AlertDialog) and c is not dialog.dialog
        ]
        self.assertEqual(confirm.title.value, "Delete income")
        confirm.actions[0].on_click(None)
        self.assertIsNotNone(self.ctx.service.get_income_schedule(schedule.id))
        self.assertIn(dialog.dialog, self.page.overlay)

        dialog.delete_button.on_click(None)
        confirm = [c for c in self.page.overlay if isinstance(c, ft.AlertDialog)][-1]
        confirm.actions[-1].on_click(None)

        self.assertIsNone(self.ctx.service.get_income_schedule(schedule.id))
        self.assertEqual(self.page.overlay, [])

    def test_delete_confirmation_does_not_touch_recorded_transactions(self):
        from budget_tracker.models import Transaction, TransactionType

        schedule = self._schedule()
        self.repo.add_transaction(
            Transaction(
                5_000,
                TransactionType.INCOME,
                self.repo.list_categories(TransactionType.INCOME)[0].id,
                self.today,
                "Real deposit",
                account_id=self.checking_id,
            )
        )
        dialog = self._dialog()
        self._edit_row(dialog).on_click(None)
        dialog.delete_button.on_click(None)
        confirm = [c for c in self.page.overlay if isinstance(c, ft.AlertDialog)][-1]
        confirm.actions[-1].on_click(None)

        self.assertIsNone(self.ctx.service.get_income_schedule(schedule.id))
        transactions = self.repo.list_transactions()
        self.assertEqual([tx.description for tx in transactions], ["Real deposit"])

    # ---------- preview ----------
    def test_preview_shows_exactly_three_dates_after_today(self):
        dialog = self._dialog()
        dialog.add_button.on_click(None)

        dialog.frequency.value = IncomeFrequency.WEEKLY.value
        dialog._frequency_changed(None)
        dialog.next_date.value = self.today.isoformat()
        dialog._next_date_changed(None)

        expected = [
            self.today + timedelta(days=7),
            self.today + timedelta(days=14),
            self.today + timedelta(days=21),
        ]
        self.assertEqual(dialog.preview_dates, expected)
        self.assertNotIn(self.today, dialog.preview_dates)
        preview_text = " ".join(_texts(dialog.preview))
        for day in expected:
            self.assertIn(f"{day:%b} {day.day}", preview_text)
        self.assertIn("Forecast only", preview_text)

    def test_preview_follows_a_semimonthly_rule(self):
        dialog = self._dialog()
        dialog.add_button.on_click(None)
        dialog.frequency.value = IncomeFrequency.SEMIMONTHLY.value
        dialog._frequency_changed(None)
        dialog.next_date.value = date(self.today.year, self.today.month, 15).isoformat()
        dialog._next_date_changed(None)
        dialog.anchor.value = "1"
        dialog.second_day.value = "15"
        dialog._anchor_changed(None)

        self.assertEqual(len(dialog.preview_dates), 3)
        self.assertTrue(all(day > self.today for day in dialog.preview_dates))
        self.assertTrue(all(day.day in (1, 15) for day in dialog.preview_dates))

    def test_calendar_pick_refreshes_the_preview_and_the_dialog(self):
        dialog = self._dialog()
        dialog.add_button.on_click(None)
        dialog.frequency.value = IncomeFrequency.MONTHLY.value
        dialog._frequency_changed(None)
        picked = self.today + timedelta(days=5)
        dialog.next_date.value = picked.isoformat()

        with mock.patch.object(dialog, "_update_dialog") as update:
            dialog._next_date_changed(None)

        update.assert_called_once_with()
        self.assertEqual(dialog.anchor.value, str(picked.day))
        self.assertEqual(dialog.preview_dates[0], picked)

        with mock.patch.object(dialog, "_update_dialog") as update:
            dialog._anchor_changed(None)
            dialog._second_day_changed(None)
            dialog._account_changed(None)

        self.assertEqual(update.call_count, 3)

    # ---------- wiring and errors ----------
    def test_service_errors_keep_the_dialog_open_and_show_the_message(self):
        dialog = self._dialog()
        dialog.add_button.on_click(None)
        self._fill_valid(dialog)

        with mock.patch.object(
            self.ctx.service, "add_income_schedule", side_effect=RepositoryError("boom")
        ):
            dialog._save(None)

        self.assertIn(dialog.dialog, self.page.overlay)
        self.assertTrue(dialog.error.visible)
        self.assertEqual(dialog.error.value, "boom")
        self.assertFalse(dialog.save_button.disabled)
        self.assertTrue(dialog.save_button.visible)

    def test_on_close_fires_once(self):
        calls: list[bool] = []
        dialog = self._dialog(on_close=lambda: calls.append(True))
        dialog.add_button.on_click(None)
        self._fill_valid(dialog)

        dialog._save(None)
        self.assertEqual(calls, [True])

        dialog._cancel(None)
        self.assertEqual(calls, [True])

    def test_successful_add_edit_and_delete_notify_data_changed_once(self):
        existing = self._schedule(name="Existing")
        with mock.patch.object(
            self.ctx, "data_changed", wraps=self.ctx.data_changed
        ) as changed:
            dialog = self._dialog()
            dialog.add_button.on_click(None)
            self._fill_valid(dialog, name="Added")
            dialog._save(None)
            self.assertEqual(changed.call_count, 1)

            dialog = self._dialog()
            self._edit_row(dialog).on_click(None)
            dialog.name.value = "Edited"
            dialog._save(None)
            self.assertEqual(changed.call_count, 2)

            dialog = self._dialog()
            self._edit_row(dialog).on_click(None)
            dialog.delete_button.on_click(None)
            confirm = [c for c in self.page.overlay if isinstance(c, ft.AlertDialog)][-1]
            confirm.actions[-1].on_click(None)
            self.assertEqual(changed.call_count, 3)

        self.assertEqual(
            [schedule.name for schedule in self.ctx.service.list_income_schedules()],
            [existing.name],
        )

    def test_default_income_button_opens_the_dialog_and_refreshes_the_panel_on_save_and_delete(self):
        from budget_tracker.ui.views.year import YearView

        view = YearView(self.ctx)
        switch = _find(
            view.control,
            lambda node: isinstance(node, ft.SegmentedButton)
            and {segment.value for segment in node.segments} == {"year", "cash"},
        )
        switch.selected = ["cash"]
        switch.on_change(_event(selected=["cash"]))
        panel = view.cash_panel

        with mock.patch.object(panel, "refresh", wraps=panel.refresh) as refresh:
            panel._income_button.on_click(None)
            dialog = panel.income_dialog
            dialog.add_button.on_click(None)
            self._fill_valid(dialog)
            dialog._save(None)
            self.assertEqual(refresh.call_count, 1)

            panel._income_button.on_click(None)
            dialog = panel.income_dialog
            self._edit_row(dialog).on_click(None)
            dialog.delete_button.on_click(None)
            confirm = [c for c in self.page.overlay if isinstance(c, ft.AlertDialog)][-1]
            confirm.actions[-1].on_click(None)

        self.assertEqual(refresh.call_count, 2)
        self.assertEqual(self.ctx.service.list_income_schedules(), [])


if __name__ == "__main__":
    unittest.main()
