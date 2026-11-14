import sqlite3
import unittest
from datetime import date

from budget_tracker.db import MIGRATIONS, _seed, connect, migrate, schema_version
from budget_tracker.models import Account, AccountKind, Transaction, TransactionType
from budget_tracker.repository import Repository


class TestLoanAccountKind(unittest.TestCase):
    def test_loan_is_a_liability(self):
        self.assertTrue(AccountKind.LOAN.is_liability)
        self.assertEqual(AccountKind.LOAN.label, "Loan")
        self.assertEqual(AccountKind.LOAN.icon, "REQUEST_QUOTE")


class TestMigrationV8(unittest.TestCase):
    def test_fresh_schema_accepts_a_loan_account(self):
        repo = Repository(connect(":memory:"))
        self.addCleanup(repo.close)
        self.assertEqual(schema_version(repo.conn), len(MIGRATIONS))
        with repo.conn:
            repo.conn.execute(
                "INSERT INTO accounts (name, kind, opening_balance_cents) VALUES ('Mortgage', 'loan', -100000)"
            )
        row = repo.conn.execute("SELECT kind, opening_balance_cents FROM accounts WHERE name = 'Mortgage'").fetchone()
        self.assertEqual((row["kind"], row["opening_balance_cents"]), ("loan", -100000))

    def test_upgrade_from_v7_preserves_accounts_and_references(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        for version, script in enumerate(MIGRATIONS[:7], start=1):
            conn.executescript(f"BEGIN;\n{script}\nPRAGMA user_version = {version};\nCOMMIT;")
        _seed(conn)
        conn.execute("INSERT INTO accounts (name, kind) VALUES ('Savings', 'savings')")
        savings_id = conn.execute("SELECT id FROM accounts WHERE name = 'Savings'").fetchone()[0]
        checking_id = conn.execute("SELECT id FROM accounts WHERE name = 'Checking'").fetchone()[0]
        card_id = conn.execute("SELECT id FROM accounts WHERE name = 'Credit Card'").fetchone()[0]
        category_id = conn.execute("SELECT id FROM categories WHERE name = 'Other'").fetchone()[0]
        conn.execute("UPDATE accounts SET number_last4 = '1234' WHERE id = ?", (card_id,))
        card_row = tuple(
            conn.execute(
                "SELECT id, name, kind, opening_balance_cents, color, number_last4 FROM accounts WHERE id = ?",
                (card_id,),
            ).fetchone()
        )
        conn.execute(
            "INSERT INTO transactions (amount_cents, type, account_id, category_id, occurred_on) "
            "VALUES (500, 'expense', ?, ?, '2026-01-02')",
            (checking_id, category_id),
        )
        conn.execute(
            "INSERT INTO transactions (amount_cents, type, account_id, transfer_account_id, occurred_on) "
            "VALUES (100, 'transfer', ?, ?, '2026-01-03')",
            (checking_id, card_id),
        )
        conn.execute(
            "INSERT INTO bills (name, amount_cents, frequency, next_due, anchor_day, account_id, category_id) "
            "VALUES ('Bill', 1000, 'monthly', '2026-12-01', 1, ?, ?)",
            (checking_id, category_id),
        )
        conn.execute(
            "INSERT INTO bills (name, amount_cents, frequency, next_due, anchor_day, account_id, pay_to_account_id) "
            "VALUES ('Card payment', 100, 'monthly', '2026-12-15', 15, ?, ?)",
            (checking_id, card_id),
        )
        conn.execute(
            "INSERT INTO savings_goals (name, target_cents, target_date, start_date, account_id) "
            "VALUES ('Trip', 1000, '2027-01-01', '2026-01-01', ?)",
            (savings_id,),
        )
        conn.execute(
            "INSERT INTO debts (name, kind, balance_cents, apr_bps, minimum_cents, account_id) "
            "VALUES ('Visa', 'credit_card', 1000, 1200, 100, ?)",
            (card_id,),
        )
        conn.execute(
            "INSERT INTO minimum_payments (account_id, month, amount_cents) VALUES (?, '2026-11', 100)",
            (card_id,),
        )
        conn.execute(
            "INSERT INTO statements (account_id, institution, account_label, period_start, period_end, "
            "beginning_cents, deposits_cents, withdrawals_cents, fees_cents, ending_cents, file_sha256) "
            "VALUES (?, 'Bank', 'Checking', '2026-01-01', '2026-01-31', 0, 0, 500, 0, -500, 'sha-1')",
            (checking_id,),
        )
        statement_id = conn.execute("SELECT id FROM statements WHERE file_sha256 = 'sha-1'").fetchone()[0]
        conn.execute(
            "INSERT INTO reconciliations (account_id, statement_id, as_of, stated_cents, computed_cents, difference_cents) "
            "VALUES (?, ?, '2026-01-31', -500, -500, 0)",
            (checking_id, statement_id),
        )
        conn.commit()

        migrate(conn)

        self.assertEqual(schema_version(conn), len(MIGRATIONS))
        self.assertEqual(conn.execute("PRAGMA foreign_key_check").fetchall(), [])
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0], 2)
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM bills").fetchone()[0], 2)
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM savings_goals").fetchone()[0], 1)
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM debts").fetchone()[0], 1)
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM minimum_payments").fetchone()[0], 1)
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM statements").fetchone()[0], 1)
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM reconciliations").fetchone()[0], 1)
        self.assertEqual(card_row[5], "1234")
        self.assertEqual(
            tuple(
                conn.execute(
                    "SELECT id, name, kind, opening_balance_cents, color, number_last4 FROM accounts WHERE id = ?",
                    (card_id,),
                ).fetchone()
            ),
            card_row,
        )
        self.assertEqual(
            tuple(conn.execute("SELECT account_id, transfer_account_id FROM transactions WHERE type = 'transfer'").fetchone()),
            (checking_id, card_id),
        )
        self.assertEqual(
            tuple(conn.execute("SELECT account_id, pay_to_account_id FROM bills WHERE name = 'Card payment'").fetchone()),
            (checking_id, card_id),
        )
        self.assertEqual(
            tuple(
                conn.execute(
                    "SELECT account_id, statement_id FROM reconciliations"
                ).fetchone()
            ),
            (checking_id, statement_id),
        )
        with conn:
            conn.execute("INSERT INTO accounts (name, kind) VALUES ('Auto Loan', 'loan')")
        self.assertEqual(
            conn.execute("SELECT kind FROM accounts WHERE name = 'Auto Loan'").fetchone()[0], "loan"
        )
        conn.close()


class TestLoanBalances(unittest.TestCase):
    def setUp(self) -> None:
        self.repo = Repository(connect(":memory:"))
        self.accounts = {a.name: a for a in self.repo.list_accounts()}

    def tearDown(self) -> None:
        self.repo.close()

    def test_loan_owed_balance_and_payment(self):
        loan = self.repo.add_account(Account("Mortgage", AccountKind.LOAN, opening_balance_cents=-100_000))
        self.repo.add_transaction(Transaction(
            20_000, TransactionType.TRANSFER, None, date(2026, 11, 10), "",
            account_id=self.accounts["Checking"].id, transfer_account_id=loan.id,
        ))
        balance = next(b for b in self.repo.account_balances() if b.account.id == loan.id)
        self.assertEqual((balance.balance_cents, balance.owed_cents), (-80_000, 80_000))

    def test_loans_sort_with_liabilities(self):
        self.repo.add_account(Account("Mortgage", AccountKind.LOAN, opening_balance_cents=-100_000))
        names = [a.name for a in self.repo.list_accounts()]
        self.assertEqual(names[-2:], ["Credit Card", "Mortgage"])
