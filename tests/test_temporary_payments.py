"""Temporary bill amounts and stepped-up debt minimums."""

import unittest
from datetime import date

from budget_tracker.db import connect
from budget_tracker.debt_planner import DebtInput, DebtStrategy, plan_payoff
from budget_tracker.models import (
    Account,
    AccountKind,
    Bill,
    BillFrequency,
    Debt,
    DebtKind,
    TransactionType,
)
from budget_tracker.repository import Repository
from budget_tracker.services import BudgetService


class TemporaryPaymentBase(unittest.TestCase):
    def setUp(self) -> None:
        self.repo = Repository(connect(":memory:"))
        self.service = BudgetService(self.repo)
        self.addCleanup(self.repo.close)
        self.checking = next(a for a in self.repo.list_accounts() if a.kind is AccountKind.CHECKING)

    def test_bill_uses_the_reduced_amount_through_the_end_date(self):
        bill = self.repo.add_bill(Bill(
            "Mortgage - Chase",
            105_549,
            BillFrequency.MONTHLY,
            date(2028, 12, 1),
            self.checking.id,
            pay_to_account_id=self.repo.add_account(
                Account("Mortgage", AccountKind.LOAN, -1_000_000)
            ).id,
            anchor_day=1,
            amount_after_cents=124_454,
            reduced_until=date(2028, 12, 31),
        ))
        self.assertEqual(self.repo.amount_due(bill, date(2028, 12, 1)), 105_549)
        self.assertEqual(self.repo.amount_due(bill, date(2029, 1, 1)), 124_454)

    def test_bill_requires_both_step_fields(self):
        from budget_tracker.repository import RepositoryError

        with self.assertRaisesRegex(RepositoryError, "Set both"):
            self.repo.add_bill(Bill(
                "Mortgage - Chase",
                105_549,
                BillFrequency.MONTHLY,
                date(2028, 12, 1),
                self.checking.id,
                category_id=next(c.id for c in self.repo.list_categories() if c.type is TransactionType.EXPENSE),
                amount_after_cents=124_454,
            ))

    def test_debt_stores_a_later_minimum(self):
        debt = self.service.create_debt(
            "Mortgage",
            DebtKind.MORTGAGE,
            100_000,
            0,
            10_000,
            minimum_after_cents=50_000,
            minimum_after_date=date(2026, 12, 1),
        )
        stored = self.repo.get_debt(debt.id)
        self.assertEqual(
            (stored.minimum_after_cents, stored.minimum_after_date),
            (50_000, date(2026, 12, 1)),
        )
        summary = self.service.debt_summary(debt.id)
        self.assertEqual(
            (summary.minimum_after_cents, summary.minimum_after_date),
            (50_000, date(2026, 12, 1)),
        )

    def test_plan_uses_the_higher_minimum_from_its_start_month(self):
        debt = self.repo.add_debt(Debt(
            name="Loan",
            kind=DebtKind.PERSONAL,
            balance_cents=100_000,
            apr_bps=0,
            minimum_cents=10_000,
            minimum_after_cents=50_000,
            minimum_after_date=date(2026, 12, 1),
        ))
        plan = plan_payoff(
            [
                DebtInput(
                    debt,
                    100_000,
                    10_000,
                    minimum_after_cents=50_000,
                    minimum_after_month="2026-12",
                )
            ],
            extra_cents=0,
            strategy=DebtStrategy.AVALANCHE,
            start_month="2026-11",
        )
        self.assertEqual(plan.months, 3)
        self.assertEqual(plan.total_paid_cents, 100_000)

    def test_service_plan_uses_the_stored_step_up(self):
        self.service.create_debt(
            "Loan",
            DebtKind.PERSONAL,
            100_000,
            0,
            10_000,
            minimum_after_cents=50_000,
            minimum_after_date=date(2026, 12, 1),
        )
        plan = self.service.debt_plan(
            0, DebtStrategy.AVALANCHE, today=date(2026, 11, 15)
        )
        self.assertEqual(plan.months, 3)
        self.assertEqual(plan.total_paid_cents, 100_000)
