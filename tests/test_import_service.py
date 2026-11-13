import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest import mock

from statement_fixture import make_generic_statement_pdf, make_statement_pdf

from budget_tracker.db import connect
from budget_tracker.models import Account, AccountKind, Statement, Transaction, TransactionType
from budget_tracker.repository import Repository, RepositoryError
from budget_tracker.services import BudgetService


class Base(unittest.TestCase):
    def setUp(self) -> None:
        self.repo = Repository(connect(":memory:"))
        self.accts = {a.name: a for a in self.repo.list_accounts()}
        self.cats = {c.name: c for c in self.repo.list_categories()}

    def tearDown(self) -> None:
        self.repo.close()


class TestImport(Base):
    def prepare(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "statement.pdf"
            make_statement_pdf(path)
            svc = BudgetService(self.repo)
            preview = svc.prepare_import(path, self.accts["Checking"].id)
        for row in preview.rows:
            cat = self.cats["Groceries"] if row.transaction.type is TransactionType.EXPENSE else self.cats["Salary"]
            row.transaction.category_id = cat.id
        return svc, preview

    def preview(self, set_opening=False):
        svc, preview = self.prepare()
        self.assertEqual(len(preview.rows), 4)
        svc.commit_import(preview, preview.rows, set_opening_balance=set_opening)
        return preview

    def test_commit_creates_statement_transactions_and_reconciliation(self):
        preview = self.preview(set_opening=True)
        self.assertEqual(len(self.repo.list_statements()), 1)
        self.assertEqual(self.repo.account_balance_as_of(self.accts["Checking"].id, date(2026, 10, 15)), 119_000)
        rec = self.repo.list_reconciliations(self.accts["Checking"].id)[0]
        self.assertEqual((rec.stated_cents, rec.computed_cents, rec.difference_cents), (119_000, 119_000, 0))

    def test_reimport_same_file_is_refused(self):
        self.preview()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "statement.pdf"
            make_statement_pdf(path)
            with self.assertRaises(RepositoryError):
                BudgetService(self.repo).prepare_import(path, self.accts["Checking"].id)

    def test_existing_matching_row_is_flagged_not_dropped(self):
        self.repo.add_transaction(Transaction(20_000, TransactionType.EXPENSE, self.cats["Groceries"].id,
                                              date(2026, 9, 20), "Card Purchase Merchant",
                                              account_id=self.accts["Checking"].id))
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "statement.pdf"
            make_statement_pdf(path)
            preview = BudgetService(self.repo).prepare_import(path, self.accts["Checking"].id)
        self.assertTrue(preview.rows[0].duplicate)
        self.assertTrue(preview.rows[0].include)

    def test_opening_balance_is_set_only_when_requested(self):
        self.preview(set_opening=True)
        self.assertEqual(self.repo.get_account(self.accts["Checking"].id).opening_balance_cents, 100_000)

    def test_opening_balance_is_left_alone_when_not_requested(self):
        self.preview()
        self.assertEqual(self.repo.get_account(self.accts["Checking"].id).opening_balance_cents, 0)

    def test_prior_transactions_block_opening_balance(self):
        self.repo.add_transaction(Transaction(500, TransactionType.INCOME, self.cats["Salary"].id,
                                              date(2026, 9, 1), "Earlier deposit",
                                              account_id=self.accts["Checking"].id))
        self.preview(set_opening=True)
        self.assertEqual(self.repo.get_account(self.accts["Checking"].id).opening_balance_cents, 0)

    def test_opening_balance_precondition_is_checked_before_import(self):
        svc, preview = self.prepare()
        # Two imported rows now fall before the period start; the check must use the state before import.
        preview.statement.period_start = date(2026, 10, 1)
        svc.commit_import(preview, preview.rows, set_opening_balance=True)
        self.assertEqual(self.repo.get_account(self.accts["Checking"].id).opening_balance_cents, 100_000)

    def test_import_rolls_back_completely_when_reconciliation_fails(self):
        svc, preview = self.prepare()
        checking = self.accts["Checking"].id
        with mock.patch.object(self.repo, "account_balance_as_of", side_effect=RuntimeError("forced failure")):
            with self.assertRaises(RuntimeError):
                svc.commit_import(preview, preview.rows, set_opening_balance=True)
        self.assertEqual(self.repo.list_statements(), [])
        self.assertEqual(self.repo.list_transactions(account_id=checking), [])
        self.assertEqual(self.repo.list_reconciliations(checking), [])
        self.assertEqual(self.repo.get_account(checking).opening_balance_cents, 0)

    def test_transfer_without_destination_is_rejected(self):
        svc, preview = self.prepare()
        row = preview.rows[0]
        row.transaction.type = TransactionType.TRANSFER
        row.transaction.category_id = None
        row.transaction.transfer_account_id = None
        with self.assertRaises(RepositoryError):
            svc.commit_import(preview, preview.rows)
        self.assertEqual(self.repo.list_statements(), [])
        self.assertEqual(self.repo.list_transactions(account_id=self.accts["Checking"].id), [])

    def test_excluded_rows_are_not_written(self):
        svc, preview = self.prepare()
        preview.rows[0].include = False
        svc.commit_import(preview, preview.rows)
        self.assertEqual(len(self.repo.list_transactions(account_id=self.accts["Checking"].id)), 3)

    def test_account_number_mismatch_blocks_commit(self):
        self.repo.set_account_number(self.accts["Checking"].id, "9999")
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "statement.pdf"
            make_statement_pdf(path)
            preview = BudgetService(self.repo).prepare_import(path, self.accts["Checking"].id)
            with self.assertRaises(RepositoryError):
                BudgetService(self.repo).commit_import(preview, preview.rows)

    def test_prepare_defaults_categories_by_type(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "statement.pdf"
            make_statement_pdf(path)
            preview = BudgetService(self.repo).prepare_import(path, self.accts["Checking"].id)
        for row in preview.rows:
            expected = "Other Income" if row.transaction.type is TransactionType.INCOME else "Other"
            self.assertEqual(row.transaction.category_id, self.cats[expected].id)

    def test_undo_import_removes_rows_and_releases_hash(self):
        preview = self.preview()
        sid = preview.statement.id
        self.repo.delete_statement(sid)
        self.assertEqual(self.repo.list_statements(), [])
        self.assertEqual(self.repo.list_transactions(account_id=self.accts["Checking"].id), [])
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "statement.pdf"
            make_statement_pdf(path)
            again = BudgetService(self.repo).prepare_import(path, self.accts["Checking"].id)
        self.assertEqual(len(again.rows), 4)

    def test_overlapping_generic_import_flags_duplicates(self):
        self.repo.add_transaction(Transaction(1_995, TransactionType.EXPENSE, self.cats["Groceries"].id,
                                              date(2026, 9, 20), "Account Interest",
                                              account_id=self.accts["Checking"].id))
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "generic.pdf"
            make_generic_statement_pdf(path)
            preview = BudgetService(self.repo).prepare_import(path, self.accts["Checking"].id)
        self.assertTrue(any(row.duplicate for row in preview.rows))

    def test_saved_rule_suggests_and_can_be_deleted(self):
        self.repo.add_category_rule("Kroger", self.cats["Groceries"].id)
        self.assertEqual(
            self.repo.suggest_category_id("Card Purchase Kroger #443", TransactionType.EXPENSE),
            self.cats["Groceries"].id,
        )
        self.repo.delete_category_rule(self.repo.list_category_rules()[0].id)
        self.assertIsNone(self.repo.suggest_category_id("Card Purchase Kroger #443", TransactionType.EXPENSE))

    def test_editing_a_transaction_preserves_import_metadata(self):
        self.preview()
        imported = self.repo.list_transactions(account_id=self.accts["Checking"].id)[0]
        self.assertIsNotNone(imported.statement_id)
        self.assertIsNotNone(imported.import_ref)
        edited = Transaction(imported.amount_cents, imported.type, imported.category_id,
                             imported.occurred_on, "edited description",
                             id=imported.id, account_id=imported.account_id)
        self.repo.update_transaction(edited)
        again = next(t for t in self.repo.list_transactions(account_id=self.accts["Checking"].id)
                     if t.id == imported.id)
        self.assertEqual((again.statement_id, again.import_ref),
                         (imported.statement_id, imported.import_ref))

    def test_deposit_can_be_retyped_as_refund(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "statement.pdf"
            make_statement_pdf(path)
            svc = BudgetService(self.repo)
            preview = svc.prepare_import(path, self.accts["Checking"].id)
        deposit = next(r for r in preview.rows if r.transaction.type is TransactionType.INCOME)
        deposit.transaction.type = TransactionType.REFUND
        deposit.transaction.category_id = self.cats["Groceries"].id
        svc.commit_import(preview, preview.rows)
        refunds = [t for t in self.repo.list_transactions(account_id=self.accts["Checking"].id)
                   if t.type is TransactionType.REFUND]
        self.assertEqual([(t.amount_cents, t.category_name) for t in refunds], [(20_000, "Groceries")])


class TestStatementOrdering(Base):
    def statement(self, sha256: str) -> Statement:
        return self.repo.add_statement(Statement(
            account_id=self.accts["Checking"].id,
            institution="Regions Bank",
            account_label="62+ LIFEGREEN CHECKING",
            period_start=date(2026, 9, 16),
            period_end=date(2026, 10, 15),
            beginning_cents=0,
            deposits_cents=0,
            withdrawals_cents=0,
            fees_cents=0,
            ending_cents=0,
            file_sha256=sha256,
        ))

    def test_list_statements_newest_first(self):
        first = self.statement("a" * 64)
        second = self.statement("b" * 64)
        self.assertEqual([s.id for s in self.repo.list_statements()], [second.id, first.id])


class TestMatchAccount(Base):
    def test_matches_suffix_in_both_directions(self):
        acct = self.repo.add_account(Account("Joint", AccountKind.CHECKING, number_last4="060"))
        svc = BudgetService(self.repo)
        self.assertEqual(svc.match_account("3060").id, acct.id)
        self.assertEqual(svc.match_account("60").id, acct.id)

    def test_returns_none_when_missing_or_ambiguous(self):
        svc = BudgetService(self.repo)
        self.assertIsNone(svc.match_account(None))
        self.assertIsNone(svc.match_account("9999"))
        self.repo.add_account(Account("One", AccountKind.CHECKING, number_last4="060"))
        self.repo.add_account(Account("Two", AccountKind.SAVINGS, number_last4="3060"))
        self.assertIsNone(svc.match_account("3060"))
