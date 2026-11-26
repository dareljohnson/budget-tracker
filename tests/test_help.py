"""In-app Help & Guide."""

import unittest

try:
    import flet as ft
except ImportError:  # pragma: no cover - UI tests need Flet installed
    ft = None

from budget_tracker.db import connect
from budget_tracker.repository import Repository
from budget_tracker.services import BudgetService


class _PageStub:
    def __init__(self) -> None:
        self.overlay: list = []

    def update(self, *_) -> None:
        pass


def _texts(control) -> list[str]:
    if isinstance(control, (list, tuple)):
        return [text for child in control for text in _texts(child)]
    texts: list[str] = []
    for attribute in ("value", "title", "subtitle", "content"):
        child = getattr(control, attribute, None)
        if isinstance(child, str):
            texts.append(child)
        elif child is not None:
            texts.extend(_texts(child))
    for child in getattr(control, "controls", None) or []:
        texts.extend(_texts(child))
    return texts


@unittest.skipIf(ft is None, "flet is not installed")
class TestHelpGuide(unittest.TestCase):
    def setUp(self) -> None:
        from budget_tracker.ui.context import AppContext
        from budget_tracker.ui.views.help import HelpView

        self.repo = Repository(connect(":memory:"))
        self.addCleanup(self.repo.close)
        self.ctx = AppContext(
            page=_PageStub(),
            repo=self.repo,
            service=BudgetService(self.repo),
        )
        self.view = HelpView(self.ctx)

    def test_guide_renders_sections_and_topics(self):
        texts = _texts(self.view.control)
        for section in ("Quick start", "Accounts", "Transactions", "Receipts", "Debts", "Help & Guide"):
            self.assertIn(section, texts)
        self.assertIn("Scan with your phone", texts)

    def test_search_filters_to_matching_topics(self):
        self.view.search.value = "firewall"
        self.view.refresh()
        texts = _texts(self.view.body.controls)
        self.assertIn("Troubleshooting", texts)
        self.assertIn("The phone cannot connect", texts)
        self.assertNotIn("Quick start", texts)

    def test_cloud_sync_topic_is_searchable(self):
        self.view.search.value = "icloud"
        self.view.refresh()
        texts = _texts(self.view.body.controls)
        self.assertIn("Cloud sync to iCloud Drive, Google Drive or OneDrive", texts)

    def test_empty_search_restores_every_section(self):
        self.view.search.value = "zzzz-no-match"
        self.view.refresh()
        self.assertIn("No guide topics match", _texts(self.view.body.controls))
        self.view.search.value = ""
        self.view.refresh()
        self.assertIn("Quick start", _texts(self.view.body.controls))

    def test_help_is_the_last_navigation_item(self):
        from budget_tracker import main as shell

        self.assertEqual(shell.NAV_ITEMS[-1][0], "Help")
        self.assertEqual(shell.HELP_INDEX, len(shell.NAV_ITEMS) - 1)
