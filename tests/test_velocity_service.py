import unittest
from datetime import date

from budget_tracker.db import connect
from budget_tracker.debt_planner import DebtStrategy
from budget_tracker.models import Account, AccountKind, DebtKind, LoanType
from budget_tracker.repository import Repository
from budget_tracker.services import BudgetService, VELOCITY_SETTING, VelocitySettings


class TestVelocityService(unittest.TestCase):
    def setUp(self) -> None:
        self.repo = Repository(connect(":memory:"))
        self.addCleanup(self.repo.close)
        self.service = BudgetService(self.repo)
        self.card = self.service.create_debt(
            "Visa", DebtKind.CREDIT_CARD, 900_000, 2499, 25_000
        )
        self.settings = VelocitySettings(
            target_debt_ids=(self.card.id or 0,),
            loc_limit_cents=1_200_000,
            loc_apr_bps=399,
            loc_balance_cents=0,
            chunk_cents=300_000,
            sweep_cents=30_000,
            reserve_cents=100_000,
        )

    def test_settings_round_trip(self):
        self.assertIsNone(self.service.velocity_settings())
        self.service.set_velocity_settings(self.settings)
        self.assertEqual(self.service.velocity_settings(), self.settings)
        self.service.clear_velocity_settings()
        self.assertIsNone(self.service.velocity_settings())

    def test_malformed_settings_are_ignored(self):
        self.repo.set_setting(VELOCITY_SETTING, "{not json")
        self.assertIsNone(self.service.velocity_settings())
        self.repo.set_setting(VELOCITY_SETTING, '{"target_debt_id": 1}')
        self.assertIsNone(self.service.velocity_settings())

    def test_builds_a_comparison_for_a_card_target(self):
        comparison = self.service.velocity_comparison(self.settings)

        self.assertIsNotNone(comparison)
        self.assertTrue(comparison.velocity.finished)
        self.assertGreater(comparison.velocity.cycles, 0)
        self.assertIsNotNone(comparison.net_interest_saved_cents)

    def test_a_mortgage_target_is_not_eligible(self):
        mortgage = self.service.create_debt(
            "Mortgage", DebtKind.MORTGAGE, 10_000_000, 365, 100_000
        )
        settings = VelocitySettings(
            target_debt_ids=(mortgage.id or 0,),
            loc_limit_cents=1_200_000,
            loc_apr_bps=399,
            loc_balance_cents=0,
            chunk_cents=300_000,
            sweep_cents=30_000,
            reserve_cents=100_000,
        )
        self.assertIsNone(self.service.velocity_input(settings))
        self.assertIsNone(self.service.velocity_comparison(settings))

    def test_personal_loan_is_eligible(self):
        personal = self.service.create_debt(
            "Personal", DebtKind.PERSONAL, 400_000, 1250, 15_000
        )
        settings = VelocitySettings(
            target_debt_ids=(personal.id or 0,),
            loc_limit_cents=1_200_000,
            loc_apr_bps=399,
            loc_balance_cents=0,
            chunk_cents=200_000,
            sweep_cents=20_000,
            reserve_cents=100_000,
        )
        self.assertIsNotNone(self.service.velocity_comparison(settings))

    def test_multiple_targets_remain_in_the_velocity_plan_and_leave_payoff(self):
        auto = self.service.create_debt("Car", DebtKind.AUTO, 500_000, 690, 20_000)
        settings = VelocitySettings(
            target_debt_ids=(self.card.id or 0, auto.id or 0),
            loc_limit_cents=1_200_000,
            loc_apr_bps=399,
            loc_balance_cents=0,
            chunk_cents=300_000,
            sweep_cents=30_000,
            reserve_cents=100_000,
        )
        self.service.set_velocity_settings(settings)
        comparison = self.service.velocity_comparison(settings)
        self.assertEqual({row.target_id for row in comparison.velocity.target_results}, {
            self.card.id, auto.id
        })
        self.assertEqual(self.service.velocity_target_ids(), {self.card.id, auto.id})
        plan = self.service.debt_plan(
            0, DebtStrategy.AVALANCHE, today=date(2026, 11, 14)
        )
        self.assertEqual(plan.payoff_order, [])

    def test_linked_loc_account_supplies_apr_and_balance(self):
        account = self.repo.add_account(Account(
            "Personal LOC",
            AccountKind.LOAN,
            opening_balance_cents=-100_000,
            apr_bps=399,
            loan_type=LoanType.REVOLVING,
        ))
        settings = VelocitySettings(
            target_debt_ids=(self.card.id or 0,),
            loc_limit_cents=1_200_000,
            loc_apr_bps=0,
            loc_balance_cents=0,
            chunk_cents=300_000,
            sweep_cents=30_000,
            reserve_cents=100_000,
            loc_account_id=account.id,
        )
        self.assertEqual(
            self.service.velocity_line(settings),
            ("Personal LOC", 100_000, 399, 1_200_000),
        )
        inputs = self.service.velocity_input(settings)
        self.assertIsNotNone(inputs)
        self.assertEqual((inputs.loc_balance_cents, inputs.loc_apr_bps), (100_000, 399))
        self.assertIsNotNone(self.service.velocity_comparison(settings))

        account = self.repo.get_account(account.id)
        account.opening_balance_cents = -50_000
        self.repo.update_account(account)
        inputs = self.service.velocity_input(settings)
        self.assertEqual(inputs.loc_balance_cents, 50_000)

    def test_linked_account_credit_limit_overrides_the_plan_limit(self):
        account = self.repo.add_account(Account(
            "Personal LOC",
            AccountKind.LOAN,
            opening_balance_cents=0,
            apr_bps=399,
            loan_type=LoanType.REVOLVING,
            credit_limit_cents=900_000,
        ))
        settings = VelocitySettings(
            target_debt_ids=(self.card.id or 0,),
            loc_limit_cents=1_200_000,
            loc_apr_bps=399,
            loc_balance_cents=0,
            chunk_cents=300_000,
            sweep_cents=30_000,
            reserve_cents=100_000,
            loc_account_id=account.id,
        )
        inputs = self.service.velocity_input(settings)
        self.assertEqual(inputs.loc_limit_cents, 900_000)

    def test_linked_non_revolving_loan_is_rejected(self):
        account = self.repo.add_account(Account(
            "Car Loan",
            AccountKind.LOAN,
            opening_balance_cents=-100_000,
            apr_bps=690,
            loan_type=LoanType.INSTALLMENT,
        ))
        settings = VelocitySettings(
            target_debt_ids=(self.card.id or 0,),
            loc_limit_cents=1_200_000,
            loc_apr_bps=399,
            loc_balance_cents=0,
            chunk_cents=300_000,
            sweep_cents=30_000,
            reserve_cents=100_000,
            loc_account_id=account.id,
        )
        self.assertIsNone(self.service.velocity_line(settings))
        self.assertIsNone(self.service.velocity_input(settings))

    def test_old_single_target_json_still_loads(self):
        self.repo.set_setting(
            VELOCITY_SETTING,
            '{"target_debt_id": %d, "loc_limit_cents": 1200000, "loc_apr_bps": 399, '
            '"loc_balance_cents": 0, "chunk_cents": 300000, "sweep_cents": 30000, '
            '"reserve_cents": 100000}' % (self.card.id or 0),
        )
        settings = self.service.velocity_settings()
        self.assertEqual(settings.target_debt_ids, (self.card.id,))

    def test_auto_loan_is_eligible(self):
        auto = self.service.create_debt("Car", DebtKind.AUTO, 500_000, 690, 20_000)
        settings = VelocitySettings(
            target_debt_ids=(auto.id or 0,),
            loc_limit_cents=1_200_000,
            loc_apr_bps=399,
            loc_balance_cents=0,
            chunk_cents=200_000,
            sweep_cents=20_000,
            reserve_cents=100_000,
        )
        self.assertIsNotNone(self.service.velocity_comparison(settings))

    def test_rate_scenarios_use_the_saved_rate(self):
        scenarios = self.service.velocity_scenarios(self.settings)
        self.assertEqual([rate for rate, _ in scenarios], [399, 599, 799])

    def test_deleted_target_stops_the_comparison(self):
        self.repo.delete_debt(self.card.id or 0)
        self.assertIsNone(self.service.velocity_comparison(self.settings))
