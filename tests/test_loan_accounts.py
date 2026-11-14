import sqlite3
import unittest

from budget_tracker.db import MIGRATIONS, _seed, connect, migrate, schema_version
from budget_tracker.models import AccountKind
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
        conn.execute(
            "INSERT INTO transactions (amount_cents, type, account_id, category_id, occurred_on) "
            "VALUES (500, 'expense', ?, ?, '2026-01-02')",
            (checking_id, category_id),
        )
        conn.execute(
            "INSERT INTO bills (name, amount_cents, frequency, next_due, anchor_day, account_id, category_id) "
            "VALUES ('Bill', 1000, 'monthly', '2026-12-01', 1, ?, ?)",
            (checking_id, category_id),
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
        conn.commit()

        migrate(conn)

        self.assertEqual(schema_version(conn), len(MIGRATIONS))
        self.assertEqual(conn.execute("PRAGMA foreign_key_check").fetchall(), [])
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0], 1)
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM bills").fetchone()[0], 1)
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM savings_goals").fetchone()[0], 1)
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM debts").fetchone()[0], 1)
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM minimum_payments").fetchone()[0], 1)
        with conn:
            conn.execute("INSERT INTO accounts (name, kind) VALUES ('Auto Loan', 'loan')")
        self.assertEqual(
            conn.execute("SELECT kind FROM accounts WHERE name = 'Auto Loan'").fetchone()[0], "loan"
        )
        conn.close()
