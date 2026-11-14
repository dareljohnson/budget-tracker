import unittest

from budget_tracker.debt_planner import (
    MAX_MONTHS,
    DebtInput,
    DebtPayoff,
    DebtPlan,
    DebtStrategy,
    monthly_interest_cents,
    plan_payoff,
)
from budget_tracker.formatting import shift_month
from budget_tracker.models import Debt, DebtKind


def entry(name: str, balance: int, apr: int, minimum: int) -> DebtInput:
    debt = Debt(name=name, kind=DebtKind.CREDIT_CARD, balance_cents=balance, apr_bps=apr,
                minimum_cents=minimum)
    return DebtInput(debt, balance, minimum)


class TestInterest(unittest.TestCase):
    def test_monthly_interest_rounds_half_up(self):
        self.assertEqual(monthly_interest_cents(100_000, 2400), 2_000)
        self.assertEqual(monthly_interest_cents(100_000, 3600), 3_000)
        self.assertEqual(monthly_interest_cents(100_000, 0), 0)


class TestPayoff(unittest.TestCase):
    def test_single_debt_payoff(self):
        a = entry("A", 100_000, 1200, 10_000)
        plan = plan_payoff([a], extra_cents=0, strategy=DebtStrategy.AVALANCHE, start_month="2026-11")
        self.assertTrue(plan.finished)
        self.assertEqual(plan.months, 11)
        self.assertEqual(plan.debt_free_month, "2027-10")
        self.assertEqual(plan.total_interest_cents, 5_898)
        self.assertEqual(plan.total_paid_cents, 105_898)
        self.assertEqual([(d.debt.name, month) for d, month in plan.payoff_order], [("A", 11)])
        self.assertEqual(plan.schedule[0], DebtPayoff(1, 91_000, 1_000, 10_000))
        self.assertEqual(plan.schedule[-1], DebtPayoff(11, 0, 58, 5_898))
        self.assertEqual(plan.schedule[-1].month, plan.months)

    def test_avalanche_and_snowball_differ(self):
        c = entry("C", 50_000, 2400, 2_500)
        d = entry("D", 40_000, 1200, 2_000)
        avalanche = plan_payoff([c, d], extra_cents=5_000, strategy=DebtStrategy.AVALANCHE, start_month="2026-11")
        snowball = plan_payoff([c, d], extra_cents=5_000, strategy=DebtStrategy.SNOWBALL, start_month="2026-11")
        self.assertEqual((avalanche.months, avalanche.total_interest_cents), (11, 7_304))
        self.assertEqual((snowball.months, snowball.total_interest_cents), (11, 9_151))
        self.assertEqual([(x.debt.name, m) for x, m in avalanche.payoff_order], [("C", 8), ("D", 11)])
        self.assertEqual([(x.debt.name, m) for x, m in snowball.payoff_order], [("D", 6), ("C", 11)])

    def test_a_cleared_minimum_rolls_into_the_next_debt(self):
        e = entry("E", 10_000, 0, 2_500)
        f = entry("F", 100_000, 0, 5_000)
        plan = plan_payoff([e, f], extra_cents=0, strategy=DebtStrategy.AVALANCHE, start_month="2026-11")
        self.assertEqual((plan.months, plan.total_paid_cents, plan.total_interest_cents), (15, 110_000, 0))
        self.assertEqual([(x.debt.name, m) for x, m in plan.payoff_order], [("E", 4), ("F", 15)])

    def test_zero_minimum_with_extra_pays_off(self):
        h = entry("H", 10_000, 0, 0)
        plan = plan_payoff([h], extra_cents=2_000, strategy=DebtStrategy.AVALANCHE, start_month="2026-11")
        self.assertEqual((plan.months, plan.total_paid_cents), (5, 10_000))

    def test_zero_minimum_without_extra_never_finishes(self):
        h = entry("H", 10_000, 0, 0)
        plan = plan_payoff([h], extra_cents=0, strategy=DebtStrategy.AVALANCHE, start_month="2026-11")
        self.assertFalse(plan.finished)
        self.assertIsNone(plan.debt_free_month)
        self.assertEqual(plan.months, MAX_MONTHS)
        self.assertEqual(len(plan.schedule), MAX_MONTHS)
        self.assertEqual(plan.total_paid_cents, 0)

    def test_minimum_below_interest_never_finishes(self):
        g = entry("G", 100_000, 3600, 2_000)
        plan = plan_payoff([g], extra_cents=0, strategy=DebtStrategy.AVALANCHE, start_month="2026-11")
        self.assertFalse(plan.finished)
        self.assertIsNone(plan.debt_free_month)
        self.assertEqual(plan.months, MAX_MONTHS)
        self.assertGreater(plan.total_interest_cents, 0)

    def test_balance_below_the_minimum_finishes_in_one_month(self):
        small = entry("Small", 1_500, 0, 2_500)
        plan = plan_payoff([small], extra_cents=0, strategy=DebtStrategy.AVALANCHE, start_month="2026-11")
        self.assertEqual((plan.months, plan.total_paid_cents), (1, 1_500))

    def test_no_debts(self):
        plan = plan_payoff([], extra_cents=0, strategy=DebtStrategy.AVALANCHE, start_month="2026-11")
        self.assertTrue(plan.finished)
        self.assertEqual(plan.months, 0)
        self.assertEqual(plan.debt_free_month, "2026-11")
        self.assertEqual(plan.schedule, [])
        self.assertIsNone(plan.first_target)

    def test_start_month_and_last_row_agree(self):
        a = entry("A", 100_000, 1200, 10_000)
        plan = plan_payoff([a], extra_cents=0, strategy=DebtStrategy.AVALANCHE, start_month="2026-11")
        self.assertEqual(plan.debt_free_month, shift_month("2026-11", plan.months))
