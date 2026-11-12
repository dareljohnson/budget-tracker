import csv
import sqlite3
import tempfile
import unittest
import xml.etree.ElementTree as ET
import zipfile
from datetime import date
from pathlib import Path

from budget_tracker.db import DEFAULT_CATEGORIES, MIGRATIONS, connect, migrate, schema_version
from budget_tracker.formatting import format_money, parse_money, parse_signed_money, shift_month
from budget_tracker.models import Account, AccountKind, Bill, BillFrequency, Budget, Category, Transaction, TransactionType, next_due_date
from budget_tracker.reminders import check_and_notify
from budget_tracker.repository import Repository, RepositoryError
from budget_tracker.services import BudgetService
from budget_tracker.xlsx import Sheet

INCOME = TransactionType.INCOME
EXPENSE = TransactionType.EXPENSE
REFUND = TransactionType.REFUND
TRANSFER = TransactionType.TRANSFER


class RepoTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.repo = Repository(connect(":memory:"))
        self.cats = {c.name: c for c in self.repo.list_categories()}
        self.accts = {a.name: a for a in self.repo.list_accounts()}

    def tearDown(self) -> None:
        self.repo.close()

    def add(self, cents: int, cat: str, day: date, desc: str = "", account: str = "Checking", type_=None) -> Transaction:
        c = self.cats[cat]
        return self.repo.add_transaction(
            Transaction(cents, type_ or c.type, c.id, day, desc, account_id=self.accts[account].id)
        )

    def transfer(self, cents: int, src: str, dst: str, day: date) -> Transaction:
        return self.repo.add_transaction(
            Transaction(cents, TRANSFER, None, day, account_id=self.accts[src].id, transfer_account_id=self.accts[dst].id)
        )

    def balances(self) -> dict[str, int]:
        return {b.account.name: b.balance_cents for b in self.repo.account_balances()}


class TestSchema(RepoTestCase):
    def test_seeds_defaults_and_sets_version(self):
        self.assertEqual(len(self.cats), len(DEFAULT_CATEGORIES))
        self.assertEqual(set(self.accts), {"Checking", "Credit Card", "Cash"})
        self.assertEqual(schema_version(self.repo.conn), len(MIGRATIONS))

    def test_upgrade_from_v1_moves_transactions_to_checking(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.executescript(f"BEGIN;{MIGRATIONS[0]}PRAGMA user_version = 1;COMMIT;")
        conn.execute("INSERT INTO categories (name, type) VALUES ('Food', 'expense')")
        conn.execute("INSERT INTO transactions (amount_cents, type, category_id, occurred_on) VALUES (500, 'expense', 1, '2026-01-02')")
        conn.commit()
        migrate(conn)
        repo = Repository(conn)
        [tx] = repo.list_transactions()
        self.assertEqual((tx.account_name, tx.category_name, tx.amount_cents), ("Checking", "Food", 500))
        self.assertEqual([a.name for a in repo.list_accounts()], ["Checking"])
        self.assertEqual(schema_version(conn), len(MIGRATIONS))
        conn.close()

    def test_reopening_file_db_does_not_reseed(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "t.db"
            conn = connect(path)
            conn.execute("DELETE FROM categories WHERE name = 'Other'")
            conn.commit()
            conn.close()
            conn = connect(path)
            count = conn.execute("SELECT COUNT(*) FROM categories").fetchone()[0]
            conn.close()
            self.assertEqual(count, len(DEFAULT_CATEGORIES) - 1)


class TestTransactions(RepoTestCase):
    def test_rejects_category_type_mismatch(self):
        with self.assertRaises(RepositoryError):
            self.repo.add_transaction(
                Transaction(100, INCOME, self.cats["Groceries"].id, date(2026, 1, 1), account_id=self.accts["Checking"].id)
            )

    def test_requires_account(self):
        with self.assertRaises(RepositoryError):
            self.repo.add_transaction(Transaction(100, EXPENSE, self.cats["Groceries"].id, date(2026, 1, 1)))

    def test_filters_by_month_type_and_search(self):
        self.add(5000, "Groceries", date(2026, 3, 31), "Weekly shop")
        self.add(200000, "Salary", date(2026, 3, 1), "March pay")
        self.add(1500, "Dining Out", date(2026, 4, 1), "100% pizza")

        march = self.repo.list_transactions(month="2026-03")
        self.assertEqual([t.amount_cents for t in march], [5000, 200000])
        self.assertEqual(len(self.repo.list_transactions(type_=INCOME)), 1)
        self.assertEqual(self.repo.list_transactions(search="weekly")[0].category_name, "Groceries")
        self.assertEqual(len(self.repo.list_transactions(search="dining")), 1)
        self.assertEqual(len(self.repo.list_transactions(search="0%")), 1)

    def test_update_and_delete(self):
        tx = self.add(1000, "Groceries", date(2026, 1, 5))
        tx.amount_cents = 2500
        self.repo.update_transaction(tx)
        self.assertEqual(self.repo.list_transactions()[0].amount_cents, 2500)
        self.repo.delete_transaction(tx.id)
        self.assertEqual(self.repo.list_transactions(), [])


class TestCategories(RepoTestCase):
    def test_duplicate_name_rejected_case_insensitive(self):
        with self.assertRaises(RepositoryError):
            self.repo.add_category(Category("groceries", EXPENSE))

    def test_cannot_delete_category_in_use(self):
        self.add(1000, "Groceries", date(2026, 1, 5))
        with self.assertRaises(RepositoryError):
            self.repo.delete_category(self.cats["Groceries"].id)
        self.repo.delete_category(self.cats["Shopping"].id)
        self.assertIsNone(self.repo.get_category(self.cats["Shopping"].id))

    def test_cannot_change_type_when_in_use(self):
        self.add(1000, "Groceries", date(2026, 1, 5))
        cat = self.cats["Groceries"]
        cat.type = INCOME
        with self.assertRaises(RepositoryError):
            self.repo.update_category(cat)


class TestBudgetsAndAggregates(RepoTestCase):
    def test_budget_upsert_progress_and_status(self):
        gid = self.cats["Groceries"].id
        self.repo.set_budget(Budget(gid, "2026-03", 10000))
        self.repo.set_budget(Budget(gid, "2026-03", 20000))
        self.add(17000, "Groceries", date(2026, 3, 10))
        self.add(99999, "Groceries", date(2026, 4, 1))
        [progress] = self.repo.budget_progress("2026-03")
        self.assertEqual((progress.limit_cents, progress.spent_cents, progress.status), (20000, 17000, "warning"))

    def test_budget_only_for_expense_categories(self):
        with self.assertRaises(RepositoryError):
            self.repo.set_budget(Budget(self.cats["Salary"].id, "2026-03", 100))

    def test_copy_budgets_skips_existing(self):
        g, d = self.cats["Groceries"].id, self.cats["Dining Out"].id
        self.repo.set_budget(Budget(g, "2026-03", 100))
        self.repo.set_budget(Budget(d, "2026-03", 200))
        self.repo.set_budget(Budget(g, "2026-04", 999))
        self.assertEqual(self.repo.copy_budgets("2026-03", "2026-04"), 1)
        limits = {b.category_id: b.limit_cents for b in self.repo.list_budgets("2026-04")}
        self.assertEqual(limits, {g: 999, d: 200})

    def test_dashboard_aggregates(self):
        self.add(300000, "Salary", date(2026, 3, 1))
        self.add(5000, "Groceries", date(2026, 3, 2))
        self.add(7000, "Groceries", date(2026, 3, 9))
        self.add(3000, "Dining Out", date(2026, 3, 3))
        self.add(1000, "Dining Out", date(2026, 2, 3))
        data = BudgetService(self.repo).dashboard("2026-03", trend_months=3)
        self.assertEqual(data.month_summary.net_cents, 285000)
        self.assertEqual(data.net_worth_cents, 300000 - 16000)
        self.assertEqual([(c.name, c.total_cents) for c in data.expense_by_category], [("Groceries", 12000), ("Dining Out", 3000)])
        self.assertEqual([(m.month, m.expense_cents) for m in data.trend], [("2026-01", 0), ("2026-02", 1000), ("2026-03", 15000)])


class TestAccountsTransfersRefunds(RepoTestCase):
    def test_card_purchase_and_payment_are_not_double_counted(self):
        self.add(300000, "Salary", date(2026, 3, 1))
        self.add(12000, "Groceries", date(2026, 3, 5), account="Credit Card")
        self.transfer(12000, "Checking", "Credit Card", date(2026, 3, 25))

        summary = self.repo.summary("2026-03")
        self.assertEqual((summary.income_cents, summary.expense_cents), (300000, 12000))
        self.assertEqual(self.balances(), {"Cash": 0, "Checking": 288000, "Credit Card": 0})

    def test_card_balance_goes_negative_with_opening_debt(self):
        card = self.accts["Credit Card"]
        card.opening_balance_cents = -50000
        self.repo.update_account(card)
        self.add(2500, "Dining Out", date(2026, 3, 5), account="Credit Card")
        [card_balance] = [b for b in self.repo.account_balances() if b.account.kind == AccountKind.CREDIT_CARD]
        self.assertEqual((card_balance.balance_cents, card_balance.owed_cents), (-52500, 52500))

    def test_refund_reduces_spending_category_totals_and_budget(self):
        gid = self.cats["Groceries"].id
        self.repo.set_budget(Budget(gid, "2026-03", 10000))
        self.add(9000, "Groceries", date(2026, 3, 2), account="Credit Card")
        self.add(3000, "Groceries", date(2026, 3, 8), "Returned item", account="Credit Card", type_=REFUND)
        self.add(500, "Dining Out", date(2026, 3, 9), type_=REFUND)

        self.assertEqual(self.repo.summary("2026-03").expense_cents, 5500)
        self.assertEqual([(c.name, c.total_cents) for c in self.repo.totals_by_category("2026-03")], [("Groceries", 6000)])
        [progress] = self.repo.budget_progress("2026-03")
        self.assertEqual(progress.spent_cents, 6000)
        self.assertEqual(self.balances()["Credit Card"], -6000)
        self.assertEqual(self.repo.monthly_totals(["2026-03"])[0].expense_cents, 5500)

    def test_refund_requires_expense_category(self):
        with self.assertRaises(RepositoryError):
            self.add(100, "Salary", date(2026, 3, 1), type_=REFUND)

    def test_transfer_validation(self):
        with self.assertRaises(RepositoryError):
            self.transfer(100, "Checking", "Checking", date(2026, 3, 1))
        with self.assertRaises(RepositoryError):
            self.repo.add_transaction(Transaction(100, TRANSFER, None, date(2026, 3, 1), account_id=self.accts["Checking"].id))
        tx = self.repo.add_transaction(
            Transaction(
                100, TRANSFER, self.cats["Groceries"].id, date(2026, 3, 1),
                account_id=self.accts["Checking"].id, transfer_account_id=self.accts["Cash"].id,
            )
        )
        self.assertIsNone(tx.category_id)
        [stored] = self.repo.list_transactions()
        self.assertEqual((stored.account_name, stored.transfer_account_name, stored.category_name), ("Checking", "Cash", None))

    def test_account_filter_matches_both_sides_of_transfer(self):
        self.add(1000, "Groceries", date(2026, 3, 1), account="Cash")
        self.add(2000, "Groceries", date(2026, 3, 2))
        self.transfer(5000, "Checking", "Cash", date(2026, 3, 3))
        cash = self.repo.list_transactions(account_id=self.accts["Cash"].id)
        self.assertEqual([t.amount_cents for t in cash], [5000, 1000])
        self.assertEqual(len(self.repo.list_transactions(search="cash")), 2)

    def test_account_crud_rules(self):
        with self.assertRaises(RepositoryError):
            self.repo.add_account(Account("checking", AccountKind.SAVINGS))
        savings = self.repo.add_account(Account("Savings", AccountKind.SAVINGS, 100000))
        self.accts["Savings"] = savings
        self.transfer(500, "Checking", "Savings", date(2026, 3, 1))
        with self.assertRaises(RepositoryError):
            self.repo.delete_account(savings.id)
        self.repo.delete_account(self.accts["Cash"].id)
        self.assertIsNone(self.repo.get_account(self.accts["Cash"].id))
        self.assertEqual(self.balances()["Savings"], 100500)


class TestBillsAndReminders(RepoTestCase):
    def bill(self, name="Rent", cents=150000, due=date(2026, 3, 31), freq=BillFrequency.MONTHLY, **kw) -> Bill:
        kw.setdefault("account_id", self.accts["Checking"].id)
        if "pay_to_account_id" not in kw:
            kw.setdefault("category_id", self.cats["Housing"].id)
        return self.repo.add_bill(Bill(name, cents, freq, due, **kw))

    def test_next_due_date_keeps_anchor_day(self):
        self.assertEqual(next_due_date(date(2026, 1, 31), BillFrequency.MONTHLY, 31), date(2026, 2, 28))
        self.assertEqual(next_due_date(date(2026, 2, 28), BillFrequency.MONTHLY, 31), date(2026, 3, 31))
        self.assertEqual(next_due_date(date(2026, 12, 15), BillFrequency.MONTHLY, 15), date(2027, 1, 15))
        self.assertEqual(next_due_date(date(2024, 2, 29), BillFrequency.YEARLY, 29), date(2025, 2, 28))
        self.assertEqual(next_due_date(date(2026, 3, 1), BillFrequency.WEEKLY, 1), date(2026, 3, 8))
        self.assertIsNone(next_due_date(date(2026, 3, 1), BillFrequency.ONCE, 1))

    def test_due_bills_window_and_status(self):
        self.bill("Rent", due=date(2026, 3, 9))
        self.bill("Gym", 4000, due=date(2026, 3, 10))
        self.bill("Phone", 6000, due=date(2026, 3, 13))
        self.bill("Later", 100, due=date(2026, 3, 14))
        due = self.repo.due_bills(date(2026, 3, 10), 3)
        self.assertEqual([(d.bill.name, d.days_until, d.status) for d in due],
                         [("Rent", -1, "overdue"), ("Gym", 0, "today"), ("Phone", 3, "soon")])
        self.assertEqual(due[0].due_text, "Overdue by 1 day")

    def test_mark_paid_records_expense_and_rolls_forward(self):
        bill = self.bill(due=date(2026, 1, 31))
        self.repo.mark_bill_paid(bill.id, paid_on=date(2026, 1, 30), amount_cents=150000)
        [tx] = self.repo.list_transactions()
        self.assertEqual((tx.type, tx.category_name, tx.description, tx.account_name), (EXPENSE, "Housing", "Rent", "Checking"))
        self.assertEqual(self.repo.get_bill(bill.id).next_due, date(2026, 2, 28))
        self.repo.mark_bill_paid(bill.id, paid_on=date(2026, 2, 27), amount_cents=150000)
        self.assertEqual(self.repo.get_bill(bill.id).next_due, date(2026, 3, 31))

    def test_card_payment_bill_pays_full_balance_as_transfer(self):
        card = self.accts["Credit Card"]
        self.add(8000, "Groceries", date(2026, 3, 2), account="Credit Card")
        bill = self.bill("Card payment", 0, date(2026, 3, 20), BillFrequency.ONCE, pay_to_account_id=card.id)
        [due] = self.repo.due_bills(date(2026, 3, 18), 3)
        self.assertEqual(due.amount_cents, 8000)
        self.repo.mark_bill_paid(bill.id, paid_on=date(2026, 3, 18), amount_cents=due.amount_cents)
        self.assertEqual(self.balances()["Credit Card"], 0)
        self.assertEqual(self.repo.summary("2026-03").expense_cents, 8000)
        self.assertFalse(self.repo.get_bill(bill.id).active)
        self.assertEqual(self.repo.due_bills(date(2026, 3, 18), 30), [])

    def test_bill_validation_and_delete_guards(self):
        with self.assertRaises(RepositoryError):
            self.bill(cents=0)
        with self.assertRaises(RepositoryError):
            self.bill(category_id=self.cats["Salary"].id)
        with self.assertRaises(RepositoryError):
            self.bill(pay_to_account_id=self.accts["Checking"].id)
        self.bill()
        with self.assertRaises(RepositoryError):
            self.repo.delete_category(self.cats["Housing"].id)
        with self.assertRaises(RepositoryError):
            self.repo.delete_account(self.accts["Checking"].id)

    def test_skip_and_reminder_days_setting(self):
        bill = self.bill(due=date(2026, 3, 5))
        self.repo.skip_bill(bill.id)
        self.assertEqual(self.repo.get_bill(bill.id).next_due, date(2026, 4, 5))
        self.assertEqual(self.repo.reminder_days(), 3)
        self.repo.set_reminder_days(7)
        self.assertEqual(BudgetService(self.repo).due_bills(date(2026, 3, 29))[0].days_until, 7)

    def test_reminder_notification(self):
        self.bill("Rent", due=date(2026, 3, 9))
        self.bill("Gym", 4000, due=date(2026, 3, 11))
        sent = []
        due = check_and_notify(self.repo, today=date(2026, 3, 10), notifier=lambda t, b: sent.append((t, b)))
        self.assertEqual(len(due), 2)
        self.assertEqual(sent, [("1 overdue, 1 due soon", "Rent: $1,500.00 - overdue by 1 day\nGym: $40.00 - due tomorrow")])
        sent.clear()
        check_and_notify(self.repo, today=date(2026, 1, 1), notifier=lambda t, b: sent.append((t, b)))
        self.assertEqual(sent, [])


class TestExportAndFormatting(RepoTestCase):
    def test_csv_export_signs_amounts_and_escapes_formulas(self):
        self.add(1234, "Groceries", date(2026, 3, 2), "=HYPERLINK(evil)")
        self.add(5000, "Salary", date(2026, 3, 1))
        with tempfile.TemporaryDirectory() as tmp:
            path = BudgetService(self.repo).export_csv(Path(tmp) / "out.csv", self.repo.list_transactions())
            with path.open(encoding="utf-8") as fh:
                rows = list(csv.reader(fh))
        self.assertEqual(rows[0], ["Date", "Type", "Account", "Category", "To Account", "Description", "Amount"])
        self.assertEqual(rows[1][5:], ["'=HYPERLINK(evil)", "-12.34"])
        self.assertEqual(rows[2][6], "50.00")

    def _read_xlsx(self, path: Path) -> dict[str, dict[str, tuple[str | None, str | None]]]:
        """Map sheet name -> cell ref -> (formula, value) by parsing the package XML."""
        ns = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
        with zipfile.ZipFile(path) as zf:
            self.assertIsNone(zf.testzip())
            names = [s.get("name") for s in ET.fromstring(zf.read("xl/workbook.xml")).iter(f"{{{ns['m']}}}sheet")]
            sheets = {}
            for i, name in enumerate(names, 1):
                cells = {}
                for c in ET.fromstring(zf.read(f"xl/worksheets/sheet{i}.xml")).iter(f"{{{ns['m']}}}c"):
                    f, v, t = c.find("m:f", ns), c.find("m:v", ns), c.find("m:is/m:t", ns)
                    cells[c.get("r")] = (f.text if f is not None else None, (v if v is not None else t).text if (v is not None or t is not None) else None)
                sheets[name] = cells
        return sheets

    def test_xlsx_adjustment_cash_flow_is_signed_and_spending_zero(self):
        self.repo.add_transaction(
            Transaction(-1500, TransactionType.ADJUSTMENT, None, date(2026, 3, 5),
                        "balance adjustment", account_id=self.accts["Checking"].id)
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = BudgetService(self.repo).export_xlsx(Path(tmp) / "out.xlsx", today=date(2026, 3, 10))
            sheets = self._read_xlsx(path)
        self.assertEqual(float(sheets["Transactions"]["J2"][1]), -15.0)
        self.assertEqual(float(sheets["Transactions"]["K2"][1]), 0.0)
        self.assertEqual(float(sheets["Transactions"]["L2"][1]), 0.0)
        self.assertIn("Adjustment", sheets["Accounts"]["F2"][0])

    def test_xlsx_export_has_formulas_lookups_and_cached_values(self):
        self.repo.set_budget(Budget(self.cats["Groceries"].id, "2026-03", 10000))
        self.add(5000, "Salary", date(2026, 3, 1))
        self.add(4000, "Groceries", date(2026, 3, 2), "=HYPERLINK(evil)")
        self.add(500, "Groceries", date(2026, 3, 3), type_=TransactionType.REFUND)
        self.transfer(1000, "Checking", "Credit Card", date(2026, 3, 4))
        with tempfile.TemporaryDirectory() as tmp:
            path = BudgetService(self.repo).export_xlsx(Path(tmp) / "out.xlsx", today=date(2026, 3, 10))
            sheets = self._read_xlsx(path)

        self.assertEqual(list(sheets), ["Summary", "Transactions", "Accounts", "Budgets", "Bills", "Lookups"])
        tx = sheets["Transactions"]
        evil = next(ref for ref, (_, v) in tx.items() if v == "=HYPERLINK(evil)")
        self.assertIsNone(tx[evil][0], "user text must never become a formula")
        self.assertIn("INDEX(Accounts!", tx["E2"][0])
        self.assertEqual(tx["E2"][1], self.accts["Checking"].kind.label)

        accounts = sheets["Accounts"]
        row = next(ref[1:] for ref, (_, v) in accounts.items() if ref[0] == "A" and v == "Checking")
        self.assertTrue(accounts[f"F{row}"][0])
        self.assertAlmostEqual(float(accounts[f"F{row}"][1]), self.balances()["Checking"] / 100)

        budgets = sheets["Budgets"]
        self.assertIn("SUMIFS(Transactions!", budgets["D2"][0])
        self.assertAlmostEqual(float(budgets["D2"][1]), 35.0)  # expense minus refund
        self.assertEqual(budgets["G2"][1], "On track")

    def test_xlsx_rejects_invalid_sheet_names(self):
        for bad in ("", "a/b", "x" * 32):
            with self.assertRaises(ValueError, msg=bad):
                Sheet(bad)

    def test_money_parsing_and_formatting(self):
        self.assertEqual(parse_money("$1,234.565"), 123457)
        for bad in ("", "abc", "0", "-5", "0.001", "nan"):
            with self.assertRaises(ValueError, msg=bad):
                parse_money(bad)
        self.assertEqual(format_money(-123456), "-$1,234.56")
        self.assertEqual(format_money(500, signed=True), "+$5.00")

    def test_signed_money_allows_zero_and_negative(self):
        self.assertEqual(parse_signed_money(""), 0)
        self.assertEqual(parse_signed_money("-$1,250.5"), -125050)
        for bad in ("abc", "inf", "-1e10"):
            with self.assertRaises(ValueError, msg=bad):
                parse_signed_money(bad)

    def test_shift_month_wraps_years(self):
        self.assertEqual(shift_month("2026-01", -1), "2025-12")
        self.assertEqual(shift_month("2025-12", 1), "2026-01")


if __name__ == "__main__":
    unittest.main()
