import unittest
from datetime import date

from budget_tracker.db import MIGRATIONS, connect, schema_version
from budget_tracker.formatting import shift_month
from budget_tracker.models import MinimumPaymentStatus, Transaction, TransactionType
from budget_tracker.repository import Repository, RepositoryError
from budget_tracker.services import BudgetService


class Base(unittest.TestCase):
    def setUp(self) -> None:
        self.repo = Repository(connect(":memory:"))
        self.service = BudgetService(self.repo)
        accounts = {account.name: account for account in self.repo.list_accounts()}
        self.card = accounts["Credit Card"]
        self.checking = accounts["Checking"]

    def tearDown(self) -> None:
        self.repo.close()

    def pay(self, cents: int, day: date) -> Transaction:
        return self.repo.add_transaction(
            Transaction(
                cents,
                TransactionType.TRANSFER,
                None,
                day,
                "Card payment",
                account_id=self.checking.id,
                transfer_account_id=self.card.id,
            )
        )

    def statuses(self) -> dict[str, MinimumPaymentStatus]:
        return {status.month: status for status in self.service.minimum_payment_statuses(self.card.id)}


class TestMinimumPaymentStorage(Base):
    def test_schema_has_the_table(self):
        self.assertEqual(schema_version(self.repo.conn), len(MIGRATIONS))
        row = self.repo.conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'minimum_payments'"
        ).fetchone()
        self.assertIsNotNone(row)

    def test_record_and_update_a_month(self):
        self.service.record_minimum_payment(self.card.id, "2026-11", 3500)
        self.service.record_minimum_payment(self.card.id, "2026-11", 4000)
        self.assertEqual(
            [(payment.month, payment.amount_cents) for payment in self.repo.list_minimum_payments(self.card.id)],
            [("2026-11", 4000)],
        )

    def test_only_credit_cards_accept_a_minimum(self):
        with self.assertRaises(RepositoryError):
            self.service.record_minimum_payment(self.checking.id, "2026-11", 3500)

    def test_invalid_month_and_amount_are_rejected(self):
        with self.assertRaises(RepositoryError):
            self.service.record_minimum_payment(self.card.id, "2026-13", 3500)
        with self.assertRaises(RepositoryError):
            self.service.record_minimum_payment(self.card.id, "2026-11", 0)

    def test_delete_a_month(self):
        self.service.record_minimum_payment(self.card.id, "2026-11", 3500)
        self.service.delete_minimum_payment(self.card.id, "2026-11")
        self.assertEqual(self.repo.list_minimum_payments(self.card.id), [])

    def test_deleting_the_card_removes_its_minimums(self):
        self.service.record_minimum_payment(self.card.id, "2026-11", 3500)
        self.repo.delete_account(self.card.id)
        count = self.repo.conn.execute("SELECT COUNT(*) FROM minimum_payments").fetchone()[0]
        self.assertEqual(count, 0)


class TestMinimumPaymentStatus(Base):
    def test_payment_in_the_same_month_marks_it_paid(self):
        self.service.record_minimum_payment(self.card.id, "2026-11", 3500)
        self.pay(3500, date(2026, 11, 10))
        status = self.statuses()["2026-11"]
        self.assertEqual((status.paid_cents, status.state), (3500, "paid"))

    def test_payment_in_the_following_month_still_counts(self):
        self.service.record_minimum_payment(self.card.id, "2026-11", 3500)
        self.pay(3500, date(2026, 12, 2))
        self.assertEqual(self.statuses()["2026-11"].state, "paid")

    def test_partial_payment(self):
        self.service.record_minimum_payment(self.card.id, "2026-11", 3500)
        self.pay(2000, date(2026, 11, 10))
        status = self.statuses()["2026-11"]
        self.assertEqual((status.paid_cents, status.state), (2000, "partial"))

    def test_overpayment_covers_two_months_without_double_counting(self):
        self.service.record_minimum_payment(self.card.id, "2026-10", 3000)
        self.service.record_minimum_payment(self.card.id, "2026-11", 4000)
        self.pay(7000, date(2026, 11, 5))
        statuses = self.statuses()
        self.assertEqual(statuses["2026-10"].paid_cents, 3000)
        self.assertEqual(statuses["2026-11"].paid_cents, 4000)
        self.assertEqual({status.state for status in statuses.values()}, {"paid"})

    def test_payment_before_the_first_minimum_is_ignored(self):
        self.service.record_minimum_payment(self.card.id, "2026-11", 3500)
        self.pay(3500, date(2026, 10, 20))
        self.assertEqual(self.statuses()["2026-11"].state, "unpaid")

    def test_transfer_out_of_the_card_does_not_count(self):
        self.service.record_minimum_payment(self.card.id, "2026-11", 3500)
        self.repo.add_transaction(
            Transaction(
                3500,
                TransactionType.TRANSFER,
                None,
                date(2026, 11, 10),
                "Cash advance",
                account_id=self.card.id,
                transfer_account_id=self.checking.id,
            )
        )
        self.assertEqual(self.statuses()["2026-11"].state, "unpaid")

    def test_current_minimum_prefers_the_current_month(self):
        current = self.service.current_month()
        previous = shift_month(current, -1)
        self.service.record_minimum_payment(self.card.id, previous, 3000)
        self.service.record_minimum_payment(self.card.id, current, 4000)
        self.assertEqual(self.service.current_minimum_payment(self.card.id).month, current)
        self.service.delete_minimum_payment(self.card.id, current)
        self.assertEqual(self.service.current_minimum_payment(self.card.id).month, previous)

    def test_no_minimums_has_no_current_status(self):
        self.assertIsNone(self.service.current_minimum_payment(self.card.id))
