import os
import sqlite3
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest import mock

from budget_tracker.db import MIGRATIONS, connect, migrate, schema_version
from budget_tracker.models import (
    Bill,
    BillFrequency,
    Statement,
    Transaction,
    TransactionType,
)
from budget_tracker.receipts.parser import ReceiptDraft
from budget_tracker.repository import Repository, RepositoryError
from budget_tracker.services import BudgetService
from budget_tracker.statements.model import ImportPreview, ImportRow

INCOME = TransactionType.INCOME
EXPENSE = TransactionType.EXPENSE
TRANSFER = TransactionType.TRANSFER


class Base(unittest.TestCase):
    def setUp(self) -> None:
        self.repo = Repository(connect(":memory:"))
        self.service = BudgetService(self.repo)
        self.cats = {c.name: c for c in self.repo.list_categories()}
        self.accts = {a.name: a for a in self.repo.list_accounts()}

    def tearDown(self) -> None:
        self.repo.close()

    def add(
        self,
        cents: int,
        cat: str,
        day: date,
        desc: str = "",
        *,
        account: str = "Checking",
        type_: TransactionType | None = None,
        source: str = "manual",
        receipt_path: str | None = None,
    ) -> Transaction:
        category = self.cats[cat]
        return self.repo.add_transaction(
            Transaction(
                cents,
                type_ or category.type,
                category.id,
                day,
                desc,
                account_id=self.accts[account].id,
                source=source,
                receipt_path=receipt_path,
            )
        )

    def transfer(self, cents: int, src: str, dst: str, day: date, desc: str = "") -> Transaction:
        return self.repo.add_transaction(
            Transaction(
                cents,
                TRANSFER,
                None,
                day,
                desc,
                account_id=self.accts[src].id,
                transfer_account_id=self.accts[dst].id,
            )
        )

    def transaction_count(self) -> int:
        return self.repo.conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0]


class TestSchemaV11(Base):
    def test_schema_version_and_source_column(self):
        self.assertEqual(len(MIGRATIONS), 11)
        self.assertEqual(schema_version(self.repo.conn), 11)
        columns = {
            row["name"]: row
            for row in self.repo.conn.execute("PRAGMA table_info(transactions)")
        }
        self.assertIn("source", columns)
        self.assertEqual(columns["source"]["dflt_value"], "'manual'")
        self.assertEqual(
            self.repo.conn.execute(
                "SELECT COUNT(*) FROM sqlite_master WHERE type='index' AND name='idx_transactions_match'"
            ).fetchone()[0],
            1,
        )

    def test_backfill_import_wins_then_receipt_then_manual(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        try:
            for version, script in enumerate(MIGRATIONS[:9], start=1):
                conn.executescript(f"BEGIN;{script}PRAGMA user_version = {version};COMMIT;")
            conn.execute("INSERT INTO categories (name, type) VALUES ('Food', 'expense')")
            conn.execute(
                "INSERT INTO statements (account_id, institution, account_label, period_start, period_end, "
                "beginning_cents, deposits_cents, withdrawals_cents, fees_cents, ending_cents, file_sha256) "
                "VALUES (1, 'Bank', 'Checking', '2026-09-01', '2026-09-30', 0, 0, 100, 0, -100, 'sha')"
            )
            conn.execute(
                "INSERT INTO transactions (amount_cents, type, account_id, category_id, occurred_on, "
                "statement_id, receipt_path) VALUES (100, 'expense', 1, 1, '2026-09-01', 1, 'receipts/a.png')"
            )
            conn.execute(
                "INSERT INTO transactions (amount_cents, type, account_id, category_id, occurred_on, "
                "receipt_path) VALUES (200, 'expense', 1, 1, '2026-09-02', 'receipts/b.png')"
            )
            conn.execute(
                "INSERT INTO transactions (amount_cents, type, account_id, category_id, occurred_on) "
                "VALUES (300, 'expense', 1, 1, '2026-09-03')"
            )
            conn.commit()

            migrate(conn)

            self.assertEqual(schema_version(conn), 11)
            sources = [
                row["source"]
                for row in conn.execute("SELECT source FROM transactions ORDER BY id")
            ]
            self.assertEqual(sources, ["import", "receipt", "manual"])
        finally:
            conn.close()


class TestProvenance(Base):
    def test_add_transaction_stores_source_and_update_preserves_it(self):
        tx = self.add(3485, "Groceries", date(2026, 9, 19), "FEL LINIS PIZZA", source="receipt")
        self.assertEqual(self.repo.get_transaction(tx.id).source, "receipt")
        tx.description = "FEL LINIS PIZZA 2"
        tx.amount_cents = 3500
        self.repo.update_transaction(tx)
        stored = self.repo.get_transaction(tx.id)
        self.assertEqual((stored.amount_cents, stored.source), (3500, "receipt"))

    def test_new_transactions_default_to_manual(self):
        tx = self.add(1000, "Groceries", date(2026, 9, 19))
        self.assertEqual(tx.source, "manual")
        self.assertEqual(self.repo.get_transaction(tx.id).source, "manual")

    def test_bill_payment_inserted_by_mark_paid_is_bill(self):
        bill = self.repo.add_bill(
            Bill(
                name="Rent",
                amount_cents=150000,
                frequency=BillFrequency.MONTHLY,
                next_due=date(2026, 10, 1),
                account_id=self.accts["Checking"].id,
                category_id=self.cats["Housing"].id,
            )
        )
        paid = self.repo.mark_bill_paid(bill.id, paid_on=date(2026, 10, 1), amount_cents=150000)
        self.assertEqual(paid.source, "bill")
        self.assertEqual(self.repo.get_transaction(paid.id).source, "bill")

    def test_import_statement_inserts_source_import(self):
        statement = Statement(
            account_id=self.accts["Checking"].id,
            institution="Bank",
            account_label="Checking",
            period_start=date(2026, 9, 1),
            period_end=date(2026, 9, 30),
            beginning_cents=0,
            deposits_cents=0,
            withdrawals_cents=3485,
            fees_cents=0,
            ending_cents=-3485,
            file_sha256="import-sha",
        )
        tx = Transaction(
            3485,
            EXPENSE,
            self.cats["Groceries"].id,
            date(2026, 9, 19),
            "FEL LINIS PIZZA",
            account_id=self.accts["Checking"].id,
        )
        self.repo.import_statement(statement, [ImportRow(transaction=tx)])
        self.assertEqual(self.repo.get_transaction(tx.id).source, "import")


class TestReceiptProvenance(Base):
    def setUp(self) -> None:
        super().setUp()
        self._tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmpdir.cleanup)
        self.tmp = self._tmpdir.name
        env = mock.patch.dict(os.environ, {"BUDGET_TRACKER_HOME": self.tmp})
        env.start()
        self.addCleanup(env.stop)
        self.png = Path(self.tmp) / "photo.png"
        self.png.write_bytes(b"\x89PNG\r\n\x1a\n" + b"data")

    def test_create_receipt_transaction_inserts_source_receipt(self):
        tx = self.service.create_receipt_transaction(
            amount_cents=3485,
            occurred_on=date(2026, 9, 19),
            description="FEL LINIS PIZZA",
            account_id=self.accts["Checking"].id,
            category_id=self.cats["Groceries"].id,
            source_path=self.png,
        )
        self.assertEqual(tx.source, "receipt")
        self.assertEqual(self.repo.get_transaction(tx.id).source, "receipt")

    def test_attaching_a_receipt_does_not_change_an_existing_source(self):
        imported = self.add(1000, "Groceries", date(2026, 9, 19), "ROW", source="import")
        self.service.attach_receipt(imported.id, self.png)
        self.assertEqual(self.repo.get_transaction(imported.id).source, "import")

        bill = self.add(2000, "Housing", date(2026, 9, 20), "BILL", source="bill")
        self.repo.set_transaction_receipt(bill.id, "receipts/fake.png")
        self.assertEqual(self.repo.get_transaction(bill.id).source, "bill")

    def test_invalid_source_raises_integrity_error(self):
        with self.assertRaises(sqlite3.IntegrityError):
            self.add(1000, "Groceries", date(2026, 9, 19), "BAD SOURCE", source="other")


class TestTransactionCandidates(Base):
    def setUp(self) -> None:
        super().setUp()
        self.checking = self.accts["Checking"].id
        self.credit = self.accts["Credit Card"].id
        self.cash = self.accts["Cash"].id
        self.target = self.add(3485, "Groceries", date(2026, 9, 19), "FEL LINIS PIZZA")
        self.other_amount = self.add(3484, "Groceries", date(2026, 9, 19), "ONE CENT OFF")
        self.other_account = self.add(
            3485, "Groceries", date(2026, 9, 19), "OTHER ACCOUNT", account="Credit Card"
        )
        self.wrong_type = self.add(
            3485, "Salary", date(2026, 9, 19), "WRONG TYPE", type_=INCOME
        )
        self.outside = self.add(3485, "Groceries", date(2026, 9, 28), "TOO LATE")
        self.receipted = self.add(
            3485,
            "Groceries",
            date(2026, 9, 19),
            "ALREADY HAS RECEIPT",
            source="receipt",
            receipt_path="receipts/x.png",
        )
        self.transfer_out = self.transfer(5000, "Checking", "Credit Card", date(2026, 9, 19), "PAY CARD")
        self.transfer_in = self.transfer(5000, "Credit Card", "Checking", date(2026, 9, 19), "CARD PAYMENT")
        self.unrelated_transfer = self.transfer(5000, "Credit Card", "Cash", date(2026, 9, 19))

    def ids(self, rows: list[Transaction]) -> set[int]:
        return {row.id for row in rows}

    def test_amount_account_date_and_type_are_filtered_in_sql(self):
        rows = self.repo.transaction_candidates(
            self.checking, 3485, date(2026, 9, 12), date(2026, 9, 26), EXPENSE
        )
        found = self.ids(rows)
        self.assertIn(self.target.id, found)
        self.assertIn(self.receipted.id, found)
        self.assertNotIn(self.other_amount.id, found)
        self.assertNotIn(self.other_account.id, found)
        self.assertNotIn(self.wrong_type.id, found)
        self.assertNotIn(self.outside.id, found)
        self.assertEqual([row.id for row in rows], sorted(row.id for row in rows))

    def test_date_bounds_are_inclusive(self):
        rows = self.repo.transaction_candidates(
            self.checking, 3485, date(2026, 9, 19), date(2026, 9, 19), EXPENSE
        )
        self.assertIn(self.target.id, self.ids(rows))
        self.assertNotIn(self.outside.id, self.ids(rows))

    def test_receiptless_only_drops_rows_that_already_have_one(self):
        rows = self.repo.transaction_candidates(
            self.checking,
            3485,
            date(2026, 9, 12),
            date(2026, 9, 26),
            EXPENSE,
            receiptless_only=True,
        )
        found = self.ids(rows)
        self.assertIn(self.target.id, found)
        self.assertNotIn(self.receipted.id, found)

    def test_transfers_match_from_either_side(self):
        rows = self.repo.transaction_candidates(
            self.checking, 5000, date(2026, 9, 12), date(2026, 9, 26), TRANSFER
        )
        self.assertEqual(self.ids(rows), {self.transfer_out.id, self.transfer_in.id})

    def test_transfer_destination_filter(self):
        rows = self.repo.transaction_candidates(
            self.checking,
            5000,
            date(2026, 9, 12),
            date(2026, 9, 26),
            TRANSFER,
            transfer_account_id=self.credit,
        )
        self.assertEqual(self.ids(rows), {self.transfer_out.id})

    def test_exclude_ids(self):
        rows = self.repo.transaction_candidates(
            self.checking,
            3485,
            date(2026, 9, 12),
            date(2026, 9, 26),
            EXPENSE,
            exclude_ids=[self.target.id],
        )
        self.assertNotIn(self.target.id, self.ids(rows))
        self.assertIn(self.receipted.id, self.ids(rows))


class TestFindTransaction(Base):
    def test_incoming_transfer_is_a_duplicate_for_the_destination_account(self):
        self.transfer(5_000, "Cash", "Checking", date(2026, 9, 20), "TRANSFER FROM CASH")
        checking = self.accts["Checking"].id
        self.assertIsNotNone(
            self.repo.find_transaction(checking, date(2026, 9, 20), 5_000, "TRANSFER FROM CASH")
        )

    def test_outgoing_transfer_is_a_duplicate_for_the_source_account(self):
        self.transfer(5_000, "Checking", "Cash", date(2026, 9, 20), "TRANSFER TO CASH")
        checking = self.accts["Checking"].id
        self.assertIsNotNone(
            self.repo.find_transaction(checking, date(2026, 9, 20), 5_000, "TRANSFER TO CASH")
        )

    def test_description_whitespace_is_collapsed_on_both_sides(self):
        self.add(4_200, "Groceries", date(2026, 9, 4), "AMAZON   MKTPLACE")
        checking = self.accts["Checking"].id
        self.assertIsNotNone(
            self.repo.find_transaction(checking, date(2026, 9, 4), 4_200, "Amazon MKTPLACE")
        )

    def test_different_description_is_not_a_duplicate(self):
        self.add(4_200, "Groceries", date(2026, 9, 4), "AMAZON MKTPLACE")
        checking = self.accts["Checking"].id
        self.assertIsNone(
            self.repo.find_transaction(checking, date(2026, 9, 4), 4_200, "WHOLE FOODS")
        )


class TestMarkBillPaidExisting(Base):
    def setUp(self) -> None:
        super().setUp()
        self.checking = self.accts["Checking"].id
        self.credit = self.accts["Credit Card"].id
        self.cash = self.accts["Cash"].id
        self.regular = self.repo.add_bill(
            Bill(
                name="Rent",
                amount_cents=150000,
                frequency=BillFrequency.MONTHLY,
                next_due=date(2026, 10, 1),
                account_id=self.checking,
                category_id=self.cats["Housing"].id,
            )
        )

    def regular_payment(self, day: date = date(2026, 10, 1), cents: int = 150000) -> Transaction:
        return self.add(cents, "Housing", day, "RENT")

    def test_existing_payment_advances_the_bill_and_inserts_nothing(self):
        existing = self.regular_payment()
        before = self.transaction_count()
        result = self.repo.mark_bill_paid(
            self.regular.id,
            paid_on=date(2026, 10, 1),
            amount_cents=150000,
            existing_transaction_id=existing.id,
        )
        self.assertEqual(result.id, existing.id)
        self.assertEqual(self.transaction_count(), before)
        self.assertEqual(self.repo.get_bill(self.regular.id).next_due, date(2026, 11, 1))

    def test_existing_payment_keeps_its_source_and_fields(self):
        existing = self.regular_payment(day=date(2026, 10, 3))
        existing.source = "manual"
        self.repo.update_transaction(existing)
        self.repo.mark_bill_paid(
            self.regular.id,
            paid_on=date(2026, 10, 1),
            amount_cents=150000,
            existing_transaction_id=existing.id,
        )
        stored = self.repo.get_transaction(existing.id)
        self.assertEqual(stored.source, "manual")
        self.assertEqual(stored.description, "RENT")
        self.assertEqual(stored.occurred_on, date(2026, 10, 3))

    def test_mismatched_amount_account_type_or_date_raises(self):
        wrong_amount = self.regular_payment(cents=149999)
        wrong_account = self.add(150000, "Housing", date(2026, 10, 1), "RENT", account="Credit Card")
        wrong_type = self.add(
            150000, "Salary", date(2026, 10, 1), "PAYCHECK", type_=INCOME
        )
        too_late = self.regular_payment(day=date(2026, 10, 9))
        for candidate in (wrong_amount, wrong_account, wrong_type, too_late):
            with self.subTest(candidate=candidate.id):
                with self.assertRaisesRegex(RepositoryError, "does not match this bill"):
                    self.repo.mark_bill_paid(
                        self.regular.id,
                        paid_on=date(2026, 10, 1),
                        amount_cents=150000,
                        existing_transaction_id=candidate.id,
                    )

    def test_unknown_transaction_raises(self):
        with self.assertRaisesRegex(RepositoryError, "does not match this bill"):
            self.repo.mark_bill_paid(
                self.regular.id,
                paid_on=date(2026, 10, 1),
                amount_cents=150000,
                existing_transaction_id=9999,
            )

    def test_card_bill_requires_the_destination_account(self):
        card_bill = self.repo.add_bill(
            Bill(
                name="Card payment",
                amount_cents=25000,
                frequency=BillFrequency.MONTHLY,
                next_due=date(2026, 10, 5),
                account_id=self.checking,
                pay_to_account_id=self.credit,
            )
        )
        correct = self.transfer(25000, "Checking", "Credit Card", date(2026, 10, 5), "CARD")
        before = self.transaction_count()
        result = self.repo.mark_bill_paid(
            card_bill.id,
            paid_on=date(2026, 10, 5),
            amount_cents=25000,
            existing_transaction_id=correct.id,
        )
        self.assertEqual(result.id, correct.id)
        self.assertEqual(self.transaction_count(), before)
        self.assertEqual(self.repo.get_bill(card_bill.id).next_due, date(2026, 11, 5))

        wrong_destination = self.transfer(25000, "Checking", "Cash", date(2026, 10, 5), "CASH")
        with self.assertRaisesRegex(RepositoryError, "does not match this bill"):
            self.repo.mark_bill_paid(
                card_bill.id,
                paid_on=date(2026, 10, 5),
                amount_cents=25000,
                existing_transaction_id=wrong_destination.id,
            )

    def test_inactive_bill_raises(self):
        bill = self.repo.get_bill(self.regular.id)
        bill.active = False
        self.repo.update_bill(bill)
        existing = self.regular_payment()
        with self.assertRaisesRegex(RepositoryError, "no longer active"):
            self.repo.mark_bill_paid(
                bill.id,
                paid_on=date(2026, 10, 1),
                amount_cents=150000,
                existing_transaction_id=existing.id,
            )


class TestImportRowDefaults(unittest.TestCase):
    def test_candidates_and_match_default_to_empty(self):
        row = ImportRow(
            transaction=Transaction(
                100, EXPENSE, 1, date(2026, 9, 19), "ROW", account_id=1
            )
        )
        self.assertEqual(row.match_candidates, [])
        self.assertIsNone(row.matched)


class TestServiceMatching(Base):
    def setUp(self) -> None:
        super().setUp()
        self.checking = self.accts["Checking"].id
        self.credit = self.accts["Credit Card"].id

    def draft(self, **overrides) -> ReceiptDraft:
        values = dict(
            merchant="FEL LINIS PIZZA",
            occurred_on=date(2026, 9, 19),
            total_cents=3485,
            card_last4=None,
            paid_cash=False,
            raw_text="",
            missing=frozenset(),
        )
        values.update(overrides)
        return ReceiptDraft(**values)

    def test_receipt_without_total_does_not_search(self):
        existing = self.add(3485, "Groceries", date(2026, 9, 19), "FEL LINIS PIZZA")
        self.assertEqual(
            self.service.receipt_match_candidates(self.draft(total_cents=None), self.checking), []
        )
        self.assertIsNotNone(self.repo.get_transaction(existing.id))

    def test_receipt_candidates_are_receiptless_expenses_on_the_account(self):
        existing = self.add(3485, "Groceries", date(2026, 9, 19), "FEL LINIS PIZZA")
        self.add(
            3485,
            "Groceries",
            date(2026, 9, 19),
            "FEL LINIS PIZZA",
            source="receipt",
            receipt_path="receipts/y.png",
        )
        candidates = self.service.receipt_match_candidates(self.draft(), self.checking)
        self.assertEqual([candidate.transaction.id for candidate in candidates], [existing.id])

    def test_bill_candidates_follow_the_bill_type_and_destination(self):
        regular = self.repo.add_bill(
            Bill(
                name="Rent",
                amount_cents=150000,
                frequency=BillFrequency.MONTHLY,
                next_due=date(2026, 10, 1),
                account_id=self.checking,
                category_id=self.cats["Housing"].id,
            )
        )
        rent = self.add(150000, "Housing", date(2026, 10, 1), "RENT")
        candidates = self.service.bill_match_candidates(
            regular, 150000, date(2026, 10, 1), self.checking
        )
        self.assertEqual([candidate.transaction.id for candidate in candidates], [rent.id])

        card_bill = self.repo.add_bill(
            Bill(
                name="Card payment",
                amount_cents=25000,
                frequency=BillFrequency.MONTHLY,
                next_due=date(2026, 10, 5),
                account_id=self.checking,
                pay_to_account_id=self.credit,
            )
        )
        outgoing = self.transfer(25000, "Checking", "Credit Card", date(2026, 10, 5), "CARD")
        self.transfer(25000, "Credit Card", "Checking", date(2026, 10, 5), "REFUND CARD")
        candidates = self.service.bill_match_candidates(
            card_bill, 25000, date(2026, 10, 5), self.checking
        )
        self.assertEqual([candidate.transaction.id for candidate in candidates], [outgoing.id])

    def statement(self) -> Statement:
        return Statement(
            account_id=self.checking,
            institution="Bank",
            account_label="Checking",
            period_start=date(2026, 9, 1),
            period_end=date(2026, 9, 30),
            beginning_cents=0,
            deposits_cents=0,
            withdrawals_cents=3485,
            fees_cents=0,
            ending_cents=-3485,
            file_sha256="service-sha",
        )

    def import_row(self, description: str = "FEL LINIS PIZZA") -> ImportRow:
        return ImportRow(
            transaction=Transaction(
                3485,
                EXPENSE,
                self.cats["Groceries"].id,
                date(2026, 9, 19),
                description,
                account_id=self.checking,
            )
        )

    def test_match_import_preview_fills_sorted_candidates(self):
        existing = self.add(3485, "Groceries", date(2026, 9, 19), "FEL LINIS PIZZA")
        preview = ImportPreview(
            statement=self.statement(), account_id=self.checking, rows=[self.import_row()]
        )
        returned = self.service.match_import_preview(preview)
        self.assertIs(returned, preview)
        self.assertEqual([tx.id for tx in preview.rows[0].match_candidates], [existing.id])
        self.assertIsNone(preview.rows[0].matched)

    def test_import_candidates_respect_transfer_direction(self):
        outgoing = self.transfer(5_000, "Checking", "Credit Card", date(2026, 9, 19), "PAY CARD")
        incoming = self.transfer(5_000, "Credit Card", "Checking", date(2026, 9, 19), "PAYMENT IN")

        def transfer_row(direction: str, description: str) -> ImportRow:
            return ImportRow(
                transaction=Transaction(
                    5_000,
                    TRANSFER,
                    None,
                    date(2026, 9, 19),
                    description,
                    account_id=self.checking,
                    transfer_account_id=self.credit,
                ),
                direction=direction,
            )

        expense_row = transfer_row("expense", "PAY CARD")
        income_row = transfer_row("income", "PAYMENT IN")
        preview = ImportPreview(self.statement(), self.checking, [expense_row, income_row])

        outgoing_candidates = self.service.import_row_candidates(preview, expense_row)
        incoming_candidates = self.service.import_row_candidates(preview, income_row)
        self.assertEqual([candidate.transaction.id for candidate in outgoing_candidates], [outgoing.id])
        self.assertEqual([candidate.transaction.id for candidate in incoming_candidates], [incoming.id])

    def test_commit_import_refuses_a_liability_account(self):
        row = self.import_row()
        row.transaction.account_id = self.credit
        preview = ImportPreview(statement=self.statement(), account_id=self.credit, rows=[row])
        with self.assertRaises(RepositoryError):
            self.service.commit_import(preview, [row])

    def test_commit_import_skips_a_matched_row(self):
        existing = self.add(3485, "Groceries", date(2026, 9, 19), "FEL LINIS PIZZA")
        row = self.import_row()
        row.matched = existing
        preview = ImportPreview(statement=self.statement(), account_id=self.checking, rows=[row])
        before = self.transaction_count()
        self.service.commit_import(preview, [row])
        self.assertEqual(self.transaction_count(), before)
        self.assertIsNotNone(self.repo.get_transaction(existing.id))
        self.assertEqual(
            self.repo.conn.execute("SELECT COUNT(*) FROM statements").fetchone()[0], 1
        )


if __name__ == "__main__":
    unittest.main()
