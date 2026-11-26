"""APR, loan type and same-as-cash promotions live on liability accounts."""

import sqlite3
import unittest
from datetime import date

from budget_tracker.db import MIGRATIONS, _seed, connect, migrate, schema_version
from budget_tracker.models import Account, AccountKind, DebtKind, LoanType
from budget_tracker.repository import Repository
from budget_tracker.services import BudgetService


class LoanDetailsBase(unittest.TestCase):
    def setUp(self) -> None:
        self.repo = Repository(connect(":memory:"))
        self.service = BudgetService(self.repo)
        self.addCleanup(self.repo.close)


class TestAccountLoanDetails(LoanDetailsBase):
    def test_liability_kinds_get_a_default_loan_type(self):
        self.assertEqual(
            Account("Visa", AccountKind.CREDIT_CARD).loan_type, LoanType.REVOLVING
        )
        self.assertEqual(
            Account("Auto", AccountKind.LOAN).loan_type, LoanType.INSTALLMENT
        )
        self.assertIsNone(Account("Checking", AccountKind.CHECKING).loan_type)

    def test_apr_type_and_promo_round_trip(self):
        account = self.repo.add_account(Account(
            "Promo Card",
            AccountKind.CREDIT_CARD,
            0,
            apr_bps=2499,
            loan_type=LoanType.SAME_AS_CASH,
            promo_apr_bps=0,
            promo_end_date=date(2027, 3, 1),
        ))
        loaded = self.repo.get_account(account.id)
        self.assertEqual(
            (loaded.apr_bps, loaded.loan_type, loaded.promo_apr_bps, loaded.promo_end_date),
            (2499, LoanType.SAME_AS_CASH, 0, date(2027, 3, 1)),
        )
        [balance] = [b for b in self.repo.account_balances() if b.account.id == account.id]
        self.assertEqual(
            (balance.account.apr_bps, balance.account.promo_end_date),
            (2499, date(2027, 3, 1)),
        )

    def test_promo_fields_are_cleared_for_other_loan_types(self):
        account = self.repo.add_account(Account(
            "Visa",
            AccountKind.CREDIT_CARD,
            0,
            loan_type=LoanType.REVOLVING,
            promo_apr_bps=0,
            promo_end_date=date(2027, 3, 1),
        ))
        loaded = self.repo.get_account(account.id)
        self.assertIsNone(loaded.promo_apr_bps)
        self.assertIsNone(loaded.promo_end_date)

    def test_asset_accounts_do_not_store_loan_details(self):
        asset = self.repo.add_account(Account(
            "Brokerage",
            AccountKind.OTHER,
            100_000,
            apr_bps=2499,
            loan_type=LoanType.SAME_AS_CASH,
            promo_apr_bps=0,
            promo_end_date=date(2027, 3, 1),
        ))
        loaded = self.repo.get_account(asset.id)
        self.assertEqual(
            (loaded.apr_bps, loaded.loan_type, loaded.promo_apr_bps, loaded.promo_end_date),
            (None, None, None, None),
        )

    def test_linking_a_debt_copies_its_apr_onto_an_account_without_one(self):
        card = self.repo.add_account(Account("Visa", AccountKind.CREDIT_CARD, 0))
        debt = self.service.create_debt(
            "Visa", DebtKind.CREDIT_CARD, 0, 2499, 1_000, account_id=card.id
        )
        self.assertEqual(self.repo.get_account(card.id).apr_bps, 2499)
        self.assertEqual(self.repo.get_debt(debt.id).apr_bps, 2499)

    def test_account_apr_is_the_source_of_truth_for_a_linked_debt(self):
        card = self.repo.add_account(Account("Visa", AccountKind.CREDIT_CARD, 0, apr_bps=2499))
        debt = self.service.create_debt(
            "Visa", DebtKind.CREDIT_CARD, 0, 999, 1_000, account_id=card.id
        )
        self.assertEqual(self.repo.get_debt(debt.id).apr_bps, 2499)

        card.apr_bps = 1999
        self.repo.update_account(card)
        self.assertEqual(self.repo.get_debt(debt.id).apr_bps, 1999)

        self.service.update_debt(
            debt.id,
            name="Visa",
            kind=DebtKind.CREDIT_CARD,
            balance_cents=0,
            apr_bps=500,
            minimum_cents=1_000,
            original_balance_cents=None,
            account_id=card.id,
        )
        self.assertEqual(self.repo.get_debt(debt.id).apr_bps, 1999)

    def test_debt_summary_uses_the_account_rate_and_type(self):
        card = self.repo.add_account(Account("Visa", AccountKind.CREDIT_CARD, 0, apr_bps=1999))
        debt = self.service.create_debt(
            "Visa", DebtKind.CREDIT_CARD, 0, 2499, 1_000, account_id=card.id
        )
        summary = self.service.debt_summary(debt.id)
        self.assertEqual((summary.apr_bps, summary.loan_type), (1999, LoanType.REVOLVING))


class TestMigrationV15(unittest.TestCase):
    def _v14_database(self) -> sqlite3.Connection:
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        for version, script in enumerate(MIGRATIONS[:14], start=1):
            conn.executescript(f"BEGIN;\n{script}\nPRAGMA user_version = {version};\nCOMMIT;")
        _seed(conn)
        return conn

    def test_upgrade_copies_linked_debt_rates_and_infers_loan_types(self):
        conn = self._v14_database()
        self.addCleanup(conn.close)
        card_id = conn.execute(
            "SELECT id FROM accounts WHERE kind = 'credit_card'"
        ).fetchone()[0]
        conn.execute(
            "INSERT INTO accounts (name, kind, opening_balance_cents, liquid) "
            "VALUES ('Auto loan', 'loan', -1000000, 0)"
        )
        loan_id = conn.execute("SELECT id FROM accounts WHERE name = 'Auto loan'").fetchone()[0]
        conn.execute(
            "INSERT INTO debts (name, kind, balance_cents, apr_bps, minimum_cents, account_id) "
            "VALUES ('Visa', 'credit_card', 0, 2499, 1000, ?)",
            (card_id,),
        )
        conn.execute(
            "INSERT INTO debts (name, kind, balance_cents, apr_bps, minimum_cents, account_id) "
            "VALUES ('Auto', 'auto', 1000000, 490, 20000, ?)",
            (loan_id,),
        )
        conn.commit()

        migrate(conn)

        self.assertEqual(schema_version(conn), len(MIGRATIONS))
        self.assertEqual(conn.execute("PRAGMA foreign_key_check").fetchall(), [])
        card = conn.execute(
            "SELECT apr_bps, loan_type FROM accounts WHERE id = ?", (card_id,)
        ).fetchone()
        loan = conn.execute(
            "SELECT apr_bps, loan_type FROM accounts WHERE id = ?", (loan_id,)
        ).fetchone()
        checking = conn.execute(
            "SELECT apr_bps, loan_type FROM accounts WHERE kind = 'checking' ORDER BY id LIMIT 1"
        ).fetchone()
        self.assertEqual(tuple(card), (2499, "revolving"))
        self.assertEqual(tuple(loan), (490, "installment"))
        self.assertEqual(tuple(checking), (None, None))
