import sqlite3
import tempfile
import unittest
from datetime import date
from pathlib import Path

from budget_tracker.db import MIGRATIONS, connect, migrate, schema_version
from budget_tracker.models import Account, AccountKind, Transaction, TransactionType
from budget_tracker.repository import Repository, RepositoryError
from budget_tracker.services import BudgetService

ADJUSTMENT = TransactionType.ADJUSTMENT


class Base(unittest.TestCase):
    def setUp(self) -> None:
        self.repo = Repository(connect(":memory:"))
        self.accts = {a.name: a for a in self.repo.list_accounts()}
        self.cats = {c.name: c for c in self.repo.list_categories()}

    def tearDown(self) -> None:
        self.repo.close()

    def adjust(self, cents: int, desc: str = "Balance adjustment") -> Transaction:
        return self.repo.add_transaction(
            Transaction(cents, ADJUSTMENT, None, date(2026, 10, 1), desc, account_id=self.accts["Checking"].id)
        )


class TestAdjustment(Base):
    def test_adjustment_changes_balance_only(self):
        self.adjust(-1500)
        self.assertEqual({b.account.name: b.balance_cents for b in self.repo.account_balances()}["Checking"], -1500)
        self.assertEqual(self.repo.summary("2026-10").income_cents, 0)
        self.assertEqual(self.repo.summary("2026-10").expense_cents, 0)

    def test_adjustment_rejects_zero_and_category(self):
        with self.assertRaises(RepositoryError):
            self.adjust(0)
        with self.assertRaises(RepositoryError):
            self.repo.add_transaction(
                Transaction(-500, ADJUSTMENT, self.cats["Groceries"].id, date(2026, 10, 1),
                            account_id=self.accts["Checking"].id)
            )

    def test_csv_export_keeps_adjustment_sign(self):
        self.adjust(-1500)
        with tempfile.TemporaryDirectory() as tmp:
            path = BudgetService(self.repo).export_csv(Path(tmp) / "out.csv", self.repo.list_transactions())
            rows = [line.split(",") for line in path.read_text(encoding="utf-8").splitlines()]
        self.assertEqual(rows[1][1], "Adjustment")
        self.assertEqual(rows[1][-1], "-15.00")


class TestMigrationV4(Base):
    def test_upgrade_from_v3_preserves_rows_and_adds_schema(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        for version, script in enumerate(MIGRATIONS[:3], start=1):
            conn.executescript(f"BEGIN;{script}PRAGMA user_version = {version};COMMIT;")
        conn.execute("INSERT INTO categories (name, type) VALUES ('Food', 'expense')")
        conn.execute("INSERT INTO accounts (name, kind) VALUES ('Savings', 'savings')")
        conn.execute(
            "INSERT INTO transactions (amount_cents, type, account_id, category_id, occurred_on) "
            "VALUES (500, 'expense', 1, 1, '2026-10-01')"
        )
        conn.commit()

        migrate(conn)

        self.assertEqual(schema_version(conn), len(MIGRATIONS))
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0], 1)
        self.assertIsNone(conn.execute("SELECT number_last4 FROM accounts WHERE id = 1").fetchone()[0])
        for table in ("statements", "reconciliations", "category_rules"):
            self.assertEqual(conn.execute(
                "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name=?", (table,)
            ).fetchone()[0], 1)
        conn.execute(
            "INSERT INTO transactions (amount_cents, type, account_id, occurred_on) "
            "VALUES (-1500, 'adjustment', 1, '2026-10-02')"
        )
        conn.commit()


class TestAccountNumber(Base):
    def test_round_trips_last_digits(self):
        acct = self.repo.add_account(Account("Joint", AccountKind.CHECKING, number_last4="3060"))
        self.assertEqual(self.repo.get_account(acct.id).number_last4, "3060")
        self.assertEqual({a.name: a.number_last4 for a in self.repo.list_accounts()}["Joint"], "3060")
        self.repo.set_account_number(acct.id, "060")
        self.assertEqual(self.repo.get_account(acct.id).number_last4, "060")


class TestCategoryRules(Base):
    def test_add_category_rule_is_idempotent(self):
        cat = self.cats["Groceries"]
        first = self.repo.add_category_rule("Kroger", cat.id)
        second = self.repo.add_category_rule("kroger", cat.id)
        self.assertEqual(first.id, second.id)
        self.assertEqual(len(self.repo.list_category_rules()), 1)


class TestBalanceCheck(Base):
    def test_check_balance_computes_as_of_difference(self):
        self.repo.add_transaction(Transaction(5000, TransactionType.INCOME, self.cats["Salary"].id,
                                              date(2026, 10, 1), "pay", account_id=self.accts["Checking"].id))
        self.repo.add_transaction(Transaction(2000, TransactionType.EXPENSE, self.cats["Groceries"].id,
                                              date(2026, 10, 10), "later", account_id=self.accts["Checking"].id))
        rec = BudgetService(self.repo).check_balance(self.accts["Checking"].id, date(2026, 10, 5), 4500)
        self.assertEqual((rec.computed_cents, rec.difference_cents), (5000, 500))
        self.assertEqual(BudgetService(self.repo).last_reconciliation(self.accts["Checking"].id).id, rec.id)

    def test_adjustment_resolves_difference_without_spending(self):
        svc = BudgetService(self.repo)
        svc.adjust_balance(self.accts["Checking"].id, date(2026, 10, 5), -500, "counted cash")
        self.assertEqual({b.account.name: b.balance_cents for b in self.repo.account_balances()}["Checking"], -500)
        self.assertEqual(self.repo.summary("2026-10").expense_cents, 0)

    def test_follow_up_check_after_adjustment_is_reconciled(self):
        svc = BudgetService(self.repo)
        svc.adjust_balance(self.accts["Checking"].id, date(2026, 10, 5), -500, "fix")
        rec = svc.check_balance(self.accts["Checking"].id, date(2026, 10, 5), -500)
        self.assertEqual(rec.difference_cents, 0)
        self.assertEqual(len(self.repo.list_reconciliations(self.accts["Checking"].id)), 1)
