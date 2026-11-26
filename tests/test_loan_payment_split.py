"""Loan payments split into principal (liability), interest and escrow (expenses)."""

import unittest
from datetime import date

from budget_tracker.db import connect
from budget_tracker.debt_planner import (
    DebtInput,
    DebtStrategy,
    escrow_from_amortization,
    next_month_extra_allocation,
    remaining_payments,
    scheduled_pi_payment_cents,
)
from budget_tracker.models import (
    Account,
    AccountKind,
    Bill,
    BillFrequency,
    Category,
    Debt,
    DebtKind,
    TransactionType,
)
from budget_tracker.repository import Repository, RepositoryError
from budget_tracker.services import BudgetService

TODAY = date(2026, 11, 20)
TOTAL = 105_549
INTEREST = 35_549
ESCROW = 5_000
PRINCIPAL = TOTAL - INTEREST - ESCROW


class LoanPaymentBase(unittest.TestCase):
    def setUp(self) -> None:
        self.repo = Repository(connect(":memory:"))
        self.service = BudgetService(self.repo)
        self.addCleanup(self.repo.close)
        self.checking = next(a for a in self.repo.list_accounts() if a.kind is AccountKind.CHECKING)
        self.loan = self.repo.add_account(Account("Mortgage", AccountKind.LOAN, -10_000_000))
        self.interest_category = self.repo.add_category(
            Category("Mortgage interest", TransactionType.EXPENSE)
        )
        self.escrow_category = self.repo.add_category(
            Category("Escrow", TransactionType.EXPENSE)
        )
        self.debt = self.service.create_debt(
            "Mortgage",
            DebtKind.MORTGAGE,
            0,
            365,
            105_549,
            original_balance_cents=12_000_000,
            account_id=self.loan.id,
        )

    def balances(self) -> dict[int, int]:
        return {balance.account.id: balance.balance_cents for balance in self.repo.account_balances()}

    def net_worth(self) -> int:
        return sum(self.balances().values())


class TestLoanPaymentSplit(LoanPaymentBase):
    def test_split_records_principal_transfer_and_two_expenses(self):
        before = self.balances()
        before_net_worth = self.net_worth()

        rows = self.service.record_loan_payment(
            self.debt.id,
            self.checking.id,
            TOTAL,
            INTEREST,
            ESCROW,
            TODAY,
            self.interest_category.id,
            self.escrow_category.id,
            today=TODAY,
        )

        self.assertEqual(len(rows), 3)
        principal = next(row for row in rows if row.type is TransactionType.TRANSFER)
        interest = next(row for row in rows if row.category_id == self.interest_category.id)
        escrow = next(row for row in rows if row.category_id == self.escrow_category.id)
        self.assertEqual(principal.amount_cents, PRINCIPAL)
        self.assertEqual(principal.transfer_account_id, self.loan.id)
        self.assertEqual(principal.debt_id, self.debt.id)
        self.assertEqual((interest.type, interest.amount_cents), (TransactionType.EXPENSE, INTEREST))
        self.assertEqual((escrow.type, escrow.amount_cents), (TransactionType.EXPENSE, ESCROW))

        after = self.balances()
        self.assertEqual(after[self.checking.id], before[self.checking.id] - TOTAL)
        self.assertEqual(after[self.loan.id], before[self.loan.id] + PRINCIPAL)
        self.assertEqual(self.service.debt_summary(self.debt.id).balance_cents, 10_000_000 - PRINCIPAL)
        self.assertEqual(self.net_worth(), before_net_worth - INTEREST - ESCROW)

    def test_maturity_and_pi_are_stored_and_exposed(self):
        self.service.update_debt(
            self.debt.id,
            name=self.debt.name,
            kind=DebtKind.MORTGAGE,
            balance_cents=0,
            apr_bps=365,
            minimum_cents=105_549,
            original_balance_cents=12_000_000,
            account_id=self.loan.id,
            maturity_date=date(2046, 4, 1),
            pi_payment_cents=78_849,
        )
        stored = self.repo.get_debt(self.debt.id)
        self.assertEqual(
            (stored.maturity_date, stored.pi_payment_cents),
            (date(2046, 4, 1), 78_849),
        )
        summary = self.service.debt_summary(self.debt.id)
        self.assertEqual(
            (summary.maturity_date, summary.pi_payment_cents),
            (date(2046, 4, 1), 78_849),
        )

    def test_debt_history_lists_only_the_principal_portion(self):
        self.service.record_loan_payment(
            self.debt.id,
            self.checking.id,
            TOTAL,
            INTEREST,
            ESCROW,
            TODAY,
            self.interest_category.id,
            self.escrow_category.id,
            today=TODAY,
        )
        payments = self.repo.list_debt_payments(self.debt.id)
        self.assertEqual([payment.amount_cents for payment in payments], [PRINCIPAL])
        self.assertEqual(payments[0].type, TransactionType.TRANSFER)

    def test_extra_principal_increases_the_transfer_and_cash_out(self):
        before = self.balances()
        rows = self.service.record_loan_payment(
            self.debt.id,
            self.checking.id,
            TOTAL,
            INTEREST,
            ESCROW,
            TODAY,
            self.interest_category.id,
            self.escrow_category.id,
            today=TODAY,
            extra_principal_cents=10_000,
        )
        principal = next(row for row in rows if row.type is TransactionType.TRANSFER)
        self.assertEqual(principal.amount_cents, PRINCIPAL + 10_000)
        after = self.balances()
        self.assertEqual(
            after[self.checking.id], before[self.checking.id] - TOTAL - 10_000
        )
        self.assertEqual(
            after[self.loan.id], before[self.loan.id] + PRINCIPAL + 10_000
        )

    def test_zero_interest_and_escrow_still_records_a_single_transfer(self):
        rows = self.service.record_loan_payment(
            self.debt.id, self.checking.id, TOTAL, 0, 0, TODAY, today=TODAY
        )
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].amount_cents, TOTAL)
        self.assertEqual(rows[0].type, TransactionType.TRANSFER)

    def test_interest_and_escrow_cannot_exceed_the_total(self):
        with self.assertRaisesRegex(RepositoryError, "cannot exceed the total"):
            self.service.record_loan_payment(
                self.debt.id,
                self.checking.id,
                TOTAL,
                TOTAL,
                1,
                TODAY,
                self.interest_category.id,
                self.escrow_category.id,
                today=TODAY,
            )
        self.assertEqual(self.repo.list_transactions(), [])

    def test_each_non_zero_portion_needs_a_category(self):
        with self.assertRaisesRegex(RepositoryError, "interest portion"):
            self.service.record_loan_payment(
                self.debt.id, self.checking.id, TOTAL, INTEREST, 0, TODAY, None, None, today=TODAY
            )
        with self.assertRaisesRegex(RepositoryError, "escrow portion"):
            self.service.record_loan_payment(
                self.debt.id, self.checking.id, TOTAL, 0, ESCROW, TODAY, None, None, today=TODAY
            )
        self.assertEqual(self.repo.list_transactions(), [])

    def test_a_non_loan_debt_is_rejected(self):
        standalone = self.service.create_debt("Personal", DebtKind.PERSONAL, 100_000, 1200, 2_000)
        with self.assertRaisesRegex(RepositoryError, "Link this debt"):
            self.service.record_loan_payment(
                standalone.id, self.checking.id, TOTAL, 0, 0, TODAY, today=TODAY
            )

    def test_funding_cannot_be_a_liability_or_the_loan_itself(self):
        with self.assertRaisesRegex(RepositoryError, "checking, savings or cash"):
            self.service.record_loan_payment(
                self.debt.id, self.loan.id, TOTAL, 0, 0, TODAY, today=TODAY
            )


class TestAmortizationEscrow(unittest.TestCase):
    def test_remaining_payments_counts_the_maturity_month(self):
        self.assertEqual(
            remaining_payments(date(2026, 12, 1), date(2046, 4, 1)), 233
        )
        self.assertEqual(remaining_payments(date(2046, 4, 1), date(2046, 4, 1)), 1)
        self.assertEqual(remaining_payments(date(2046, 5, 1), date(2046, 4, 1)), 0)
        self.assertEqual(remaining_payments(date(2026, 12, 1), None), 0)

    def test_scheduled_pi_payment_amortizes_the_balance(self):
        self.assertEqual(scheduled_pi_payment_cents(10_000_000, 365, 360), 45_746)
        self.assertEqual(scheduled_pi_payment_cents(10_000_000, 0, 10), 1_000_000)
        self.assertIsNone(scheduled_pi_payment_cents(10_000_000, 365, 0))

    def test_escrow_is_total_minus_scheduled_pi(self):
        escrow = escrow_from_amortization(
            105_549,
            12_053_185,
            365,
            date(2046, 4, 1),
            date(2026, 12, 1),
        )
        self.assertEqual(escrow, 33_265)

    def test_pi_override_wins(self):
        self.assertEqual(
            escrow_from_amortization(
                105_549,
                12_053_185,
                365,
                date(2046, 4, 1),
                date(2026, 12, 1),
                pi_payment_cents=78_849,
            ),
            26_700,
        )

    def test_no_maturity_or_pi_means_no_escrow(self):
        self.assertIsNone(
            escrow_from_amortization(105_549, 12_053_185, 365, None, date(2026, 12, 1))
        )

    def test_total_below_pi_means_no_escrow(self):
        self.assertIsNone(
            escrow_from_amortization(
                50_000,
                12_053_185,
                365,
                date(2046, 4, 1),
                date(2026, 12, 1),
                pi_payment_cents=78_849,
            )
        )


class TestPlanExtraAllocation(unittest.TestCase):
    def test_extra_rolls_over_when_the_first_target_is_nearly_paid_off(self):
        first = Debt(
            name="Card",
            kind=DebtKind.PERSONAL,
            balance_cents=15_000,
            apr_bps=0,
            minimum_cents=10_000,
            id=1,
        )
        second = Debt(
            name="Loan",
            kind=DebtKind.PERSONAL,
            balance_cents=100_000,
            apr_bps=0,
            minimum_cents=10_000,
            id=2,
        )
        allocation = next_month_extra_allocation(
            [
                DebtInput(first, 15_000, 10_000),
                DebtInput(second, 100_000, 10_000),
            ],
            extra_cents=20_000,
            strategy=DebtStrategy.AVALANCHE,
        )
        self.assertEqual(allocation, {1: 5_000, 2: 15_000})

    def test_extra_stays_with_the_first_target_when_it_can_absorb_it(self):
        first = Debt(
            name="Card",
            kind=DebtKind.PERSONAL,
            balance_cents=100_000,
            apr_bps=0,
            minimum_cents=10_000,
            id=1,
        )
        second = Debt(
            name="Loan",
            kind=DebtKind.PERSONAL,
            balance_cents=200_000,
            apr_bps=0,
            minimum_cents=10_000,
            id=2,
        )
        allocation = next_month_extra_allocation(
            [
                DebtInput(first, 100_000, 10_000),
                DebtInput(second, 200_000, 10_000),
            ],
            extra_cents=20_000,
            strategy=DebtStrategy.AVALANCHE,
        )
        self.assertEqual(allocation, {1: 20_000})

    def test_plan_extra_and_strategy_are_persisted(self):
        repo = Repository(connect(":memory:"))
        self.addCleanup(repo.close)
        service = BudgetService(repo)
        self.assertEqual(service.debt_plan_extra(), 0)
        self.assertEqual(service.debt_plan_strategy(), DebtStrategy.AVALANCHE)
        service.set_debt_plan_extra(12_345)
        service.set_debt_plan_strategy(DebtStrategy.SNOWBALL)
        self.assertEqual(service.debt_plan_extra(), 12_345)
        self.assertEqual(service.debt_plan_strategy(), DebtStrategy.SNOWBALL)

    def test_service_exposes_the_split(self):
        repo = Repository(connect(":memory:"))
        self.addCleanup(repo.close)
        service = BudgetService(repo)
        first = service.create_debt("Card", DebtKind.PERSONAL, 15_000, 0, 10_000)
        second = service.create_debt("Loan", DebtKind.PERSONAL, 100_000, 0, 10_000)
        allocation = service.debt_extra_allocation(20_000, DebtStrategy.AVALANCHE)
        self.assertEqual(allocation, {first.id: 5_000, second.id: 15_000})


class TestBillSplitPayment(LoanPaymentBase):
    def add_loan_bill(self, amount: int = TOTAL) -> Bill:
        return self.repo.add_bill(Bill(
            "Mortgage - Chase",
            amount,
            BillFrequency.MONTHLY,
            date(2026, 11, 1),
            self.checking.id,
            pay_to_account_id=self.loan.id,
            anchor_day=1,
        ))

    def test_bill_split_advances_the_bill_and_records_three_rows(self):
        bill = self.add_loan_bill()
        before = self.balances()

        rows = self.repo.mark_bill_paid_split(
            bill.id,
            paid_on=date(2026, 11, 1),
            principal_cents=PRINCIPAL,
            interest_cents=INTEREST,
            escrow_cents=ESCROW,
            interest_category_id=self.interest_category.id,
            escrow_category_id=self.escrow_category.id,
            account_id=self.checking.id,
            confirmation_number="CONF-1",
        )

        self.assertEqual(len(rows), 3)
        self.assertTrue(all(row.confirmation_number == "CONF-1" for row in rows))
        principal = next(row for row in rows if row.type is TransactionType.TRANSFER)
        self.assertEqual(principal.debt_id, self.debt.id)
        after = self.balances()
        self.assertEqual(after[self.checking.id], before[self.checking.id] - TOTAL)
        self.assertEqual(after[self.loan.id], before[self.loan.id] + PRINCIPAL)
        self.assertEqual(self.repo.get_bill(bill.id).next_due, date(2026, 12, 1))

    def test_bill_split_rejects_a_non_loan_target(self):
        card = next(a for a in self.repo.list_accounts() if a.kind is AccountKind.CREDIT_CARD)
        card_bill = self.repo.add_bill(Bill(
            "Card payment",
            0,
            BillFrequency.MONTHLY,
            date(2026, 11, 1),
            self.checking.id,
            pay_to_account_id=card.id,
            anchor_day=1,
        ))
        with self.assertRaisesRegex(RepositoryError, "loan payments"):
            self.repo.mark_bill_paid_split(
                card_bill.id,
                paid_on=date(2026, 11, 1),
                principal_cents=1_000,
                interest_cents=0,
                escrow_cents=0,
                account_id=self.checking.id,
            )
