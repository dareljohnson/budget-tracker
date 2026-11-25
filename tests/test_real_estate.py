"""Real estate: a property asset account linked to the mortgage that secures it."""

import sqlite3
import unittest
from datetime import date
from types import SimpleNamespace

from budget_tracker.db import MIGRATIONS, _seed, connect, migrate, schema_version
from budget_tracker.models import Account, AccountKind, Debt, DebtKind
from budget_tracker.reports import ReportService
from budget_tracker.repository import Repository, RepositoryError
from budget_tracker.services import BudgetService

TODAY = date(2026, 11, 16)
MARKET_VALUE = 45_000_000  # $450,000
UPB = 31_200_000  # $312,000 unpaid principal
ORIGINAL = 36_000_000  # $360,000 original principal


class RealEstateBase(unittest.TestCase):
    def setUp(self) -> None:
        self.repo = Repository(connect(":memory:"))
        self.service = BudgetService(self.repo)
        self.addCleanup(self.repo.close)
        self.checking = next(a for a in self.repo.list_accounts() if a.kind is AccountKind.CHECKING)

    def add_property_and_mortgage(self) -> tuple[Account, Account, Debt]:
        home = self.repo.add_account(Account("Home", AccountKind.REAL_ESTATE, MARKET_VALUE))
        loan = self.repo.add_account(Account("JP Morgan Chase", AccountKind.LOAN, -UPB))
        debt = self.service.create_debt(
            "JP Morgan Chase",
            DebtKind.MORTGAGE,
            0,
            620,
            220_000,
            original_balance_cents=ORIGINAL,
            account_id=loan.id,
            property_account_id=home.id,
        )
        return home, loan, debt


class TestRealEstateKind(unittest.TestCase):
    def test_real_estate_is_a_non_liquid_asset(self):
        self.assertFalse(AccountKind.REAL_ESTATE.is_liability)
        self.assertTrue(AccountKind.REAL_ESTATE.is_real_estate)
        self.assertFalse(AccountKind.REAL_ESTATE.default_liquid)
        self.assertEqual(AccountKind.REAL_ESTATE.label, "Real Estate")
        self.assertEqual(AccountKind.REAL_ESTATE.icon, "HOME")
        self.assertFalse(AccountKind.OTHER.is_real_estate)

    def test_fresh_schema_accepts_a_real_estate_account(self):
        repo = Repository(connect(":memory:"))
        self.addCleanup(repo.close)
        home = repo.add_account(Account("Home", AccountKind.REAL_ESTATE, MARKET_VALUE))
        row = repo.conn.execute(
            "SELECT kind, opening_balance_cents, liquid FROM accounts WHERE id = ?",
            (home.id,),
        ).fetchone()
        self.assertEqual(tuple(row), ("real_estate", MARKET_VALUE, 0))


class TestMigrationV14(unittest.TestCase):
    def _v13_database(self) -> sqlite3.Connection:
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        for version, script in enumerate(MIGRATIONS[:13], start=1):
            conn.executescript(f"BEGIN;\n{script}\nPRAGMA user_version = {version};\nCOMMIT;")
        _seed(conn)
        return conn

    def test_upgrade_preserves_accounts_and_debts(self):
        conn = self._v13_database()
        self.addCleanup(conn.close)
        conn.execute(
            "INSERT INTO accounts (name, kind, opening_balance_cents, liquid) "
            "VALUES ('Condo', 'other', 25000000, 0)"
        )
        condo_id = conn.execute("SELECT id FROM accounts WHERE name = 'Condo'").fetchone()[0]
        conn.execute(
            "INSERT INTO debts (name, kind, balance_cents, apr_bps, minimum_cents, account_id) "
            "VALUES ('Condo loan', 'mortgage', 10000000, 620, 100000, ?)",
            (condo_id,),
        )
        conn.commit()

        migrate(conn)

        self.assertEqual(schema_version(conn), 14)
        self.assertEqual(conn.execute("PRAGMA foreign_key_check").fetchall(), [])
        self.assertEqual(conn.execute("PRAGMA foreign_keys").fetchone()[0], 1)
        self.assertEqual(
            tuple(
                conn.execute(
                    "SELECT kind, opening_balance_cents, liquid FROM accounts WHERE id = ?",
                    (condo_id,),
                ).fetchone()
            ),
            ("other", 25_000_000, 0),
        )
        debts = conn.execute("SELECT name, property_account_id FROM debts").fetchall()
        self.assertEqual([tuple(row) for row in debts], [("Condo loan", None)])
        conn.execute(
            "INSERT INTO accounts (name, kind, opening_balance_cents) "
            "VALUES ('Vacation home', 'real_estate', 30000000)"
        )


class TestRealEstateWorkflow(RealEstateBase):
    def test_property_market_value_and_mortgage_upb_set_net_worth(self):
        home, loan, debt = self.add_property_and_mortgage()

        stored = self.repo.get_debt(debt.id)
        self.assertEqual(stored.account_id, loan.id)
        self.assertEqual(stored.property_account_id, home.id)
        self.assertEqual(stored.balance_cents, UPB)
        self.assertEqual(stored.original_balance_cents, ORIGINAL)
        self.assertTrue(stored.is_secured_by_property)

        total = sum(balance.balance_cents for balance in self.repo.account_balances())
        self.assertEqual(total, MARKET_VALUE - UPB)

        summary = self.service.debt_summary(debt.id)
        self.assertEqual(summary.balance_cents, UPB)
        self.assertEqual(summary.property_name, "Home")
        self.assertEqual(summary.property_value_cents, MARKET_VALUE)
        self.assertEqual(summary.property_equity_cents, MARKET_VALUE - UPB)

    def test_net_worth_report_groups_real_estate_separately(self):
        self.add_property_and_mortgage()

        report = ReportService(self.service).net_worth(today=TODAY)

        self.assertEqual([b.account.name for b in report.real_estate_assets], ["Home"])
        self.assertEqual(report.real_estate_cents, MARKET_VALUE)
        self.assertEqual(report.other_assets_cents, 0)
        self.assertEqual(report.assets_cents, MARKET_VALUE)
        self.assertEqual(report.liabilities_cents, UPB)
        self.assertEqual(report.net_worth_cents, MARKET_VALUE - UPB)

    def test_property_must_be_a_real_estate_account(self):
        with self.assertRaisesRegex(RepositoryError, "Real Estate account"):
            self.service.create_debt(
                "Loan",
                DebtKind.MORTGAGE,
                10_000,
                500,
                0,
                property_account_id=self.checking.id,
            )

    def test_property_account_in_use_cannot_be_deleted(self):
        home, _loan, _debt = self.add_property_and_mortgage()
        with self.assertRaisesRegex(RepositoryError, "linked to the debt"):
            self.repo.delete_account(home.id)

    def test_property_account_kind_cannot_change_while_linked(self):
        home, _loan, _debt = self.add_property_and_mortgage()
        changed = Account(home.name, AccountKind.OTHER, home.opening_balance_cents, id=home.id)
        with self.assertRaisesRegex(RepositoryError, "unlink it before changing its type"):
            self.repo.update_account(changed)

    def test_unlinking_the_property_keeps_the_mortgage(self):
        _home, _loan, debt = self.add_property_and_mortgage()
        self.service.update_debt(
            debt.id,
            name=debt.name,
            kind=debt.kind,
            balance_cents=debt.balance_cents,
            apr_bps=debt.apr_bps,
            minimum_cents=debt.minimum_cents,
            original_balance_cents=debt.original_balance_cents,
            account_id=debt.account_id,
            property_account_id=None,
        )
        stored = self.repo.get_debt(debt.id)
        self.assertIsNone(stored.property_account_id)
        self.assertEqual(stored.account_id, debt.account_id)


try:
    import flet as ft
except ImportError:  # pragma: no cover - UI tests need Flet installed
    ft = None


class _PageStub:
    def __init__(self) -> None:
        self.overlay: list = []

    def show_dialog(self, control) -> None:
        self.overlay.append(control)

    def pop_dialog(self) -> None:
        dialogs = [c for c in self.overlay if isinstance(c, ft.AlertDialog)]
        if dialogs:
            self.overlay.remove(dialogs[-1])

    def update(self, *_) -> None:
        pass


def _texts(control) -> list[str]:
    if isinstance(control, (list, tuple)):
        return [text for child in control for text in _texts(child)]
    values: list[str] = []
    value = getattr(control, "value", None)
    if isinstance(value, str):
        values.append(value)
    content = getattr(control, "content", None)
    if content is not None:
        values.extend(_texts(content))
    for child in getattr(control, "controls", None) or []:
        values.extend(_texts(child))
    return values


@unittest.skipIf(ft is None, "flet is not installed")
class TestRealEstateEditors(RealEstateBase):
    def setUp(self) -> None:
        super().setUp()
        from budget_tracker.ui.context import AppContext

        self.page = _PageStub()
        self.ctx = AppContext(page=self.page, repo=self.repo, service=self.service)

    def test_account_editor_records_market_value(self):
        from budget_tracker.ui.views.accounts import AccountEditor

        editor = AccountEditor(self.ctx, None)
        editor.name.value = "Home"
        editor.kind = AccountKind.REAL_ESTATE
        editor.opening.value = "450000.00"
        editor._render()
        self.assertEqual(editor.opening.label, "Market value")
        editor._save(None)
        home = next(a for a in self.repo.list_accounts() if a.name == "Home")
        self.assertEqual(home.kind, AccountKind.REAL_ESTATE)
        self.assertEqual(home.opening_balance_cents, MARKET_VALUE)

    def test_debt_editor_links_a_mortgage_to_a_property(self):
        from budget_tracker.ui.views.debts import DebtEditor

        home = self.repo.add_account(Account("Home", AccountKind.REAL_ESTATE, MARKET_VALUE))
        loan = self.repo.add_account(Account("JP Morgan Chase", AccountKind.LOAN, -UPB))
        editor = DebtEditor(self.ctx, None)
        editor.name.value = "JP Morgan Chase"
        editor.apr.value = "6.20"
        editor.minimum.value = "2200.00"
        editor.original.value = "360000.00"
        editor.link.value = str(loan.id)
        editor._link_changed()
        editor.kind.value = DebtKind.MORTGAGE.value
        editor._property_visibility()
        editor.property.value = str(home.id)
        editor._save(None)

        debt = self.repo.list_debts()[0]
        self.assertEqual(debt.property_account_id, home.id)
        self.assertEqual(debt.account_id, loan.id)
        self.assertEqual(debt.balance_cents, UPB)
        self.assertEqual(debt.original_balance_cents, ORIGINAL)

    def test_debt_editor_hides_the_property_link_for_a_credit_card(self):
        from budget_tracker.ui.views.debts import DebtEditor

        editor = DebtEditor(self.ctx, None)
        self.assertFalse(editor.property.visible)
        editor.kind.value = DebtKind.MORTGAGE.value
        editor._property_visibility()
        self.assertTrue(editor.property.visible)

    def test_accounts_view_shows_market_value_and_equity(self):
        from budget_tracker.ui.views.accounts import AccountsView

        self.add_property_and_mortgage()
        view = AccountsView(self.ctx)
        texts = _texts(view.grid.controls)
        for expected in ("Home", "Market value", "Mortgage owed", "Equity", "Original principal"):
            self.assertIn(expected, texts)
        self.assertIn("$450,000.00", texts)
        self.assertIn("$312,000.00", texts)
        self.assertIn("$138,000.00", texts)

    def test_debts_view_shows_the_linked_property(self):
        from budget_tracker.ui.views.debts import DebtsView

        self.add_property_and_mortgage()
        view = DebtsView(self.ctx)
        texts = _texts(view.grid.controls)
        self.assertIn("Property: Home", texts)
        self.assertTrue(any("Equity $138,000.00" in text for text in texts))
