import unittest
from datetime import date

from budget_tracker.db import connect
from budget_tracker.models import Transaction, TransactionType
from budget_tracker.repository import Repository
from budget_tracker.services import BudgetService


class TestTopSpendingDays(unittest.TestCase):
    def setUp(self):
        self.repo = Repository(connect(":memory:"))
        self.service = BudgetService(self.repo)
        self.account = self.repo.list_accounts()[0]
        self.category = self.repo.list_categories(TransactionType.EXPENSE)[0]

    def tearDown(self):
        self.repo.close()

    def spend(self, cents: int, day: date, *, type_=TransactionType.EXPENSE):
        self.repo.add_transaction(
            Transaction(
                cents,
                type_,
                self.category.id,
                day,
                "Test",
                account_id=self.account.id,
            )
        )

    def test_ranks_days_by_net_spending_and_caps_the_list(self):
        self.spend(1_000, date(2026, 11, 5))
        self.spend(2_000, date(2026, 11, 5))
        self.spend(5_000, date(2026, 11, 10))
        self.spend(3_000, date(2026, 11, 20))
        self.spend(1_000, date(2026, 11, 20), type_=TransactionType.REFUND)

        days = self.repo.top_spending_days("2026-11", limit=2)
        self.assertEqual(
            [(day.day, day.total_cents, day.transaction_count) for day in days],
            [(date(2026, 11, 10), 5_000, 1), (date(2026, 11, 5), 3_000, 2)],
        )

    def test_refunds_and_non_spending_types_are_excluded(self):
        other = self.repo.list_accounts()[1]
        self.spend(5_000, date(2026, 11, 10))
        self.spend(1_000, date(2026, 11, 10), type_=TransactionType.REFUND)
        self.repo.add_transaction(
            Transaction(
                9_999,
                TransactionType.TRANSFER,
                None,
                date(2026, 11, 11),
                "Transfer",
                account_id=self.account.id,
                transfer_account_id=other.id,
            )
        )
        self.repo.add_transaction(
            Transaction(
                8_888,
                TransactionType.ADJUSTMENT,
                None,
                date(2026, 11, 12),
                "Adjustment",
                account_id=self.account.id,
            )
        )
        self.spend(500, date(2026, 11, 13))
        self.spend(500, date(2026, 11, 13), type_=TransactionType.REFUND)

        days = self.repo.top_spending_days("2026-11", limit=5)
        self.assertEqual([(day.day, day.total_cents) for day in days], [(date(2026, 11, 10), 4_000)])

    def test_dashboard_exposes_the_top_days(self):
        self.spend(2_500, date(2026, 11, 14))
        data = self.service.dashboard("2026-11")
        self.assertEqual([(day.day, day.total_cents) for day in data.top_days], [(date(2026, 11, 14), 2_500)])

    def test_spending_days_card_lists_the_ranked_days(self):
        from budget_tracker.ui.views.dashboard import DashboardView

        self.spend(2_500, date(2026, 11, 14))
        self.spend(4_000, date(2026, 11, 15))
        data = self.service.dashboard("2026-11")
        card = DashboardView._top_days(data)
        texts = []

        def walk(control):
            for child in getattr(control, "controls", []) or []:
                walk(child)
            child = getattr(control, "content", None)
            if child is not None:
                walk(child)
            value = getattr(control, "value", None)
            if isinstance(value, str):
                texts.append(value)

        walk(card)
        self.assertIn("Highest spending days", texts)
        self.assertIn("Sun, Nov 15", texts)
        self.assertEqual(texts.count("$40.00"), 1)
        self.assertEqual(texts.count("$25.00"), 1)
        self.assertIn("1 entry", texts)

    def test_spending_days_card_has_an_empty_state(self):
        from budget_tracker.ui.views.dashboard import DashboardView

        data = self.service.dashboard("2026-11")
        card = DashboardView._top_days(data)
        texts = []

        def walk(control):
            for child in getattr(control, "controls", []) or []:
                walk(child)
            child = getattr(control, "content", None)
            if child is not None:
                walk(child)
            value = getattr(control, "value", None)
            if isinstance(value, str):
                texts.append(value)

        walk(card)
        self.assertIn("No spending yet", texts)
        self.assertIn("Days with expenses in this month will be ranked here.", texts)
