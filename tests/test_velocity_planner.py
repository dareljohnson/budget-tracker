import unittest

from budget_tracker.velocity_planner import (
    VelocityInput,
    VelocityTarget,
    compare_velocity,
    rate_scenarios,
    run_baseline,
    run_velocity_plan,
)


def plan(**overrides) -> VelocityInput:
    values = {
        "target_balance_cents": 900_000,
        "target_apr_bps": 2499,
        "target_minimum_cents": 25_000,
        "loc_limit_cents": 1_200_000,
        "loc_apr_bps": 399,
        "loc_balance_cents": 0,
        "chunk_cents": 300_000,
        "sweep_cents": 30_000,
        "reserve_cents": 100_000,
    }
    values.update(overrides)
    return VelocityInput(**values)


class TestVelocityPlan(unittest.TestCase):
    def test_cycles_clear_the_target_and_the_line(self):
        result = run_velocity_plan(plan())

        self.assertTrue(result.finished, result)
        self.assertEqual(result.cycles, 2)
        self.assertEqual(result.target_payoff_month, 17)
        self.assertEqual(result.line_clear_month, 19)
        self.assertEqual(result.schedule[-1].target_balance_cents, 0)
        self.assertEqual(result.schedule[-1].loc_balance_cents, 0)
        self.assertLess(result.total_interest_cents, run_baseline(plan()).total_interest_cents)

    def test_chunk_is_capped_by_available_credit_and_reserve(self):
        result = run_velocity_plan(
            plan(chunk_cents=2_000_000, loc_limit_cents=500_000, reserve_cents=50_000)
        )

        self.assertEqual(result.schedule[0].chunk_cents, 450_000)
        self.assertLessEqual(result.max_loc_balance_cents, 500_000)

    def test_an_existing_line_balance_is_paid_before_a_new_chunk(self):
        result = run_velocity_plan(plan(loc_balance_cents=60_000))

        self.assertEqual(result.schedule[0].chunk_cents, 0)
        self.assertGreater(result.schedule[0].loc_balance_cents, 0)
        self.assertTrue(result.finished)

    def test_no_sweep_and_no_minimum_never_finishes(self):
        result = run_velocity_plan(
            plan(sweep_cents=0, target_minimum_cents=0, chunk_cents=300_000)
        )

        self.assertFalse(result.finished)
        self.assertEqual(result.months, 600)
        self.assertGreater(result.schedule[-1].target_balance_cents, 0)
        self.assertGreater(result.schedule[-1].loc_balance_cents, 0)

    def test_comparison_and_break_even(self):
        comparison = compare_velocity(plan())

        self.assertIsNotNone(comparison.net_interest_saved_cents)
        self.assertGreater(comparison.net_interest_saved_cents, 0)
        self.assertEqual(comparison.baseline.finished, True)
        self.assertEqual(comparison.baseline.payoff_month, 21)
        self.assertIsNotNone(comparison.break_even_loc_apr_bps)
        self.assertGreater(comparison.break_even_loc_apr_bps, 399)

    def test_rate_scenarios_run_at_the_base_and_stress_rates(self):
        scenarios = rate_scenarios(plan())

        self.assertEqual([rate for rate, _ in scenarios], [399, 599, 799])
        self.assertTrue(all(result.finished for _, result in scenarios))
        self.assertLess(scenarios[0][1].total_interest_cents, scenarios[-1][1].total_interest_cents)

    def test_zero_target_finishes_immediately(self):
        result = run_velocity_plan(plan(target_balance_cents=0))
        self.assertTrue(result.finished)
        self.assertEqual(result.months, 0)
        self.assertEqual(result.schedule, [])

    def test_negative_values_are_treated_as_zero(self):
        result = run_velocity_plan(
            plan(
                target_balance_cents=-1,
                loc_limit_cents=-1,
                loc_balance_cents=-1,
                chunk_cents=-1,
                sweep_cents=-1,
                reserve_cents=-1,
            )
        )
        self.assertTrue(result.finished)
        self.assertEqual(result.months, 0)


class TestMultiTargetVelocityPlan(unittest.TestCase):
    def multi(self, **overrides) -> VelocityInput:
        targets = (
            VelocityTarget(1, 900_000, 2499, 25_000),
            VelocityTarget(2, 1_000_000, 690, 30_000),
            VelocityTarget(3, 500_000, 1250, 20_000),
        )
        values = {
            "loc_limit_cents": 1_200_000,
            "loc_apr_bps": 399,
            "chunk_cents": 400_000,
            "sweep_cents": 40_000,
            "reserve_cents": 100_000,
            "targets": targets,
        }
        values.update(overrides)
        return VelocityInput.for_targets(**values)

    def test_all_targets_get_a_payoff_and_interest_result(self):
        result = run_velocity_plan(self.multi())

        self.assertTrue(result.finished)
        self.assertEqual(len(result.target_results), 3)
        self.assertEqual({row.target_id for row in result.target_results}, {1, 2, 3})
        self.assertTrue(all(row.payoff_month is not None for row in result.target_results))
        self.assertEqual(result.target_payoff_month, max(
            row.payoff_month for row in result.target_results if row.payoff_month is not None
        ))

    def test_chunks_go_to_the_highest_apr_first(self):
        result = run_velocity_plan(self.multi())

        first = result.schedule[0]
        self.assertGreater(first.chunk_cents, 0)
        first_result = next(row for row in result.target_results if row.target_id == 1)
        second_result = next(row for row in result.target_results if row.target_id == 2)
        self.assertLess(first_result.payoff_month, second_result.payoff_month)

    def test_baseline_also_prioritizes_the_highest_apr(self):
        baseline = run_baseline(self.multi())
        self.assertTrue(baseline.finished)
        self.assertEqual(len(baseline.target_results), 3)
        self.assertIsNotNone(baseline.payoff_month)

    def test_more_targets_can_still_beat_the_baseline(self):
        comparison = compare_velocity(self.multi())
        self.assertIsNotNone(comparison.net_interest_saved_cents)
        self.assertGreater(comparison.net_interest_saved_cents, 0)

    def test_zero_balance_targets_are_ignored(self):
        result = run_velocity_plan(self.multi(targets=(
            VelocityTarget(1, 0, 2499, 25_000),
            VelocityTarget(2, 500_000, 690, 30_000),
        )))
        self.assertEqual(len(result.target_results), 1)
        self.assertEqual(result.target_results[0].target_id, 2)
