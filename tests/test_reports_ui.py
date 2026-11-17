"""UI behaviour for the Reports page: report switching, preview, and PDF export."""

import asyncio
import tempfile
import unittest
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import flet as ft

from budget_tracker.db import connect
from budget_tracker.models import Budget, Transaction, TransactionType
from budget_tracker.reports import ReportKind
from budget_tracker.repository import Repository
from budget_tracker.services import BudgetService
from budget_tracker.ui.context import AppContext
from budget_tracker.ui.views.reports import ReportsView


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


class _Picker:
    def __init__(self, path: Path) -> None:
        self.path = path

    async def save_file(self, **_: object) -> str:
        return str(self.path)


def _walk(control):
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


class ReportsViewTest(unittest.TestCase):
    def setUp(self) -> None:
        self.repo = Repository(connect(":memory:"))
        categories = {category.name: category for category in self.repo.list_categories()}
        accounts = {account.name: account for account in self.repo.list_accounts()}
        self.repo.add_transaction(
            Transaction(
                500_000,
                TransactionType.INCOME,
                categories["Salary"].id,
                date.today(),
                "Pay",
                account_id=accounts["Checking"].id,
            )
        )
        self.repo.add_transaction(
            Transaction(
                75_000,
                TransactionType.EXPENSE,
                categories["Groceries"].id,
                date.today(),
                "Groceries",
                account_id=accounts["Checking"].id,
            )
        )
        self.repo.set_budget(Budget(categories["Groceries"].id, date.today().strftime("%Y-%m"), 100_000))
        self.page = _PageStub()
        self.ctx = AppContext(page=self.page, repo=self.repo, service=BudgetService(self.repo))
        self.view = ReportsView(self.ctx)

    def tearDown(self) -> None:
        self.repo.close()

    def test_page_renders_a_pdf_preview_and_the_report_info(self) -> None:
        self.assertIn("Reports", _texts(self.view.control))
        self.assertIsNotNone(self.view._pdf_bytes)
        self.assertGreaterEqual(self.view._page_count, 1)
        self.assertTrue(self.view.preview_image.image.src.startswith("data:image/png;base64,"))
        self.assertEqual(self.view.page_label.value, f"Page 1 of {self.view._page_count}")
        self.assertIn("Monthly summary", _texts(self.view.control))
        self.assertEqual(self.view.month_dropdown.value, date.today().strftime("%Y-%m"))
        self.assertEqual(len(self.view.kind_switch.segments), len(ReportKind))
        self.assertFalse(self.view.export_button.disabled)

    def test_switching_reports_updates_the_period_control_and_info(self) -> None:
        self.view._switch_kind(SimpleNamespace(control=SimpleNamespace(selected={"year"})))
        self.assertEqual(self.view.kind, ReportKind.YEAR)
        self.assertIs(self.view.period_slot.content, self.view.year_dropdown)
        self.assertEqual(self.view.info_title.value, "Year in review")
        self.assertIsNotNone(self.view._pdf_bytes)

        self.view._switch_kind(SimpleNamespace(control=SimpleNamespace(selected={"net_worth"})))
        self.assertEqual(self.view.kind, ReportKind.NET_WORTH)
        self.assertIsInstance(self.view.period_slot.content, ft.Row)
        self.assertEqual(self.view.info_title.value, "Net worth statement")
        self.assertIn("As of", " ".join(_texts(self.view.period_slot.content)))

    def test_page_navigation_moves_through_the_preview(self) -> None:
        if self.view._page_count < 2:
            self.skipTest("The sample report has only one page")
        self.assertTrue(self.view.prev_page.disabled)
        self.assertFalse(self.view.next_page.disabled)
        self.view._show_page(1)
        self.assertFalse(self.view.prev_page.disabled)
        self.assertEqual(self.view.page_label.value, f"Page 2 of {self.view._page_count}")

    def test_export_writes_the_previewed_pdf(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "monthly.pdf"
            self.view._file_picker = _Picker(target)
            asyncio.run(self.view._export(None))
            self.assertTrue(target.exists())
            self.assertTrue(target.read_bytes().startswith(b"%PDF"))
        self.assertTrue(self.page.snackbars)
        self.assertIn("Exported report", self.page.snackbars[-1].content.value)
