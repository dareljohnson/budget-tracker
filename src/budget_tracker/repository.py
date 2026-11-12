"""Data access layer. All SQL lives here and is fully parameterized."""

from __future__ import annotations

import sqlite3
from collections.abc import Sequence
from datetime import date
from typing import TYPE_CHECKING

from .models import (
    Account,
    AccountBalance,
    AccountKind,
    Bill,
    BillFrequency,
    Budget,
    BudgetProgress,
    Category,
    CategoryRule,
    CategoryTotal,
    DueBill,
    MonthTotal,
    Reconciliation,
    Statement,
    Summary,
    Transaction,
    TransactionType,
    next_due_date,
)

if TYPE_CHECKING:
    from .statements.model import ImportRow

REMINDER_DAYS_SETTING = "reminder_days"
DEFAULT_REMINDER_DAYS = 3

# Net spending: expenses minus refunds. Transfers are neither income nor spending.
_SPEND_SQL = "CASE {t}type WHEN 'expense' THEN {t}amount_cents WHEN 'refund' THEN -{t}amount_cents ELSE 0 END"
_INCOME_SQL = "CASE {t}type WHEN 'income' THEN {t}amount_cents ELSE 0 END"


class RepositoryError(Exception):
    """Raised for user-facing data integrity errors."""


def _month_bounds(month: str) -> tuple[str, str]:
    """Return [start, end) ISO dates for a YYYY-MM month."""
    year, mon = map(int, month.split("-"))
    nxt = (year + 1, 1) if mon == 12 else (year, mon + 1)
    return f"{year:04d}-{mon:02d}-01", f"{nxt[0]:04d}-{nxt[1]:02d}-01"


class Repository:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def close(self) -> None:
        self.conn.close()

    # ---------- categories ----------
    @staticmethod
    def _row_to_category(row: sqlite3.Row) -> Category:
        return Category(
            id=row["id"],
            name=row["name"],
            type=TransactionType(row["type"]),
            color=row["color"],
            icon=row["icon"],
        )

    def list_categories(self, type_: TransactionType | None = None) -> list[Category]:
        sql = "SELECT id, name, type, color, icon FROM categories"
        params: tuple = ()
        if type_ is not None:
            sql += " WHERE type = ?"
            params = (type_.value,)
        sql += " ORDER BY type DESC, name"
        return [self._row_to_category(r) for r in self.conn.execute(sql, params)]

    def get_category(self, category_id: int) -> Category | None:
        row = self.conn.execute(
            "SELECT id, name, type, color, icon FROM categories WHERE id = ?", (category_id,)
        ).fetchone()
        return self._row_to_category(row) if row else None

    def add_category(self, category: Category) -> Category:
        try:
            with self.conn:
                cur = self.conn.execute(
                    "INSERT INTO categories (name, type, color, icon) VALUES (?, ?, ?, ?)",
                    (category.name.strip(), category.type.value, category.color, category.icon),
                )
        except sqlite3.IntegrityError as exc:
            raise RepositoryError(f"A {category.type.value} category named '{category.name}' already exists") from exc
        category.id = cur.lastrowid
        return category

    def update_category(self, category: Category) -> None:
        if category.id is None:
            raise ValueError("Category id is required")
        if self.category_usage(category.id) and (existing := self.get_category(category.id)):
            if existing.type != category.type:
                raise RepositoryError("Cannot change the type of a category that has transactions")
        try:
            with self.conn:
                self.conn.execute(
                    "UPDATE categories SET name = ?, type = ?, color = ?, icon = ? WHERE id = ?",
                    (category.name.strip(), category.type.value, category.color, category.icon, category.id),
                )
        except sqlite3.IntegrityError as exc:
            raise RepositoryError(f"A {category.type.value} category named '{category.name}' already exists") from exc

    def category_usage(self, category_id: int) -> int:
        return self.conn.execute(
            "SELECT COUNT(*) FROM transactions WHERE category_id = ?", (category_id,)
        ).fetchone()[0]

    def delete_category(self, category_id: int) -> None:
        if count := self.category_usage(category_id):
            raise RepositoryError(f"Category is used by {count} transaction(s); reassign or delete them first")
        if self.conn.execute("SELECT 1 FROM bills WHERE category_id = ?", (category_id,)).fetchone():
            raise RepositoryError("Category is used by a bill; change or delete the bill first")
        with self.conn:
            self.conn.execute("DELETE FROM categories WHERE id = ?", (category_id,))

    # ---------- accounts ----------
    @staticmethod
    def _row_to_account(row: sqlite3.Row) -> Account:
        return Account(
            id=row["id"],
            name=row["name"],
            kind=AccountKind(row["kind"]),
            opening_balance_cents=row["opening_balance_cents"],
            color=row["color"],
            number_last4=row["number_last4"],
        )

    def list_accounts(self) -> list[Account]:
        rows = self.conn.execute(
            "SELECT id, name, kind, opening_balance_cents, color, number_last4 FROM accounts "
            "ORDER BY kind = 'credit_card', name"
        )
        return [self._row_to_account(r) for r in rows]

    def get_account(self, account_id: int) -> Account | None:
        row = self.conn.execute(
            "SELECT id, name, kind, opening_balance_cents, color, number_last4 FROM accounts WHERE id = ?",
            (account_id,),
        ).fetchone()
        return self._row_to_account(row) if row else None

    def add_account(self, account: Account) -> Account:
        name = account.name.strip()
        if not name:
            raise RepositoryError("Account name is required")
        try:
            with self.conn:
                cur = self.conn.execute(
                    "INSERT INTO accounts (name, kind, opening_balance_cents, color, number_last4) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (name, account.kind.value, account.opening_balance_cents, account.color, account.number_last4),
                )
        except sqlite3.IntegrityError as exc:
            raise RepositoryError(f"An account named '{name}' already exists") from exc
        account.id = cur.lastrowid
        return account

    def update_account(self, account: Account) -> None:
        if account.id is None:
            raise ValueError("Account id is required")
        name = account.name.strip()
        if not name:
            raise RepositoryError("Account name is required")
        try:
            with self.conn:
                self.conn.execute(
                    "UPDATE accounts SET name = ?, kind = ?, opening_balance_cents = ?, color = ?, number_last4 = ? "
                    "WHERE id = ?",
                    (name, account.kind.value, account.opening_balance_cents, account.color, account.number_last4,
                     account.id),
                )
        except sqlite3.IntegrityError as exc:
            raise RepositoryError(f"An account named '{name}' already exists") from exc

    def set_account_number(self, account_id: int, digits: str | None) -> None:
        with self.conn:
            self.conn.execute("UPDATE accounts SET number_last4 = ? WHERE id = ?", (digits, account_id))

    def account_usage(self, account_id: int) -> int:
        return self.conn.execute(
            "SELECT COUNT(*) FROM transactions WHERE account_id = ? OR transfer_account_id = ?",
            (account_id, account_id),
        ).fetchone()[0]

    def delete_account(self, account_id: int) -> None:
        if count := self.account_usage(account_id):
            raise RepositoryError(f"Account is used by {count} transaction(s); reassign or delete them first")
        if self.conn.execute(
            "SELECT 1 FROM bills WHERE account_id = ? OR pay_to_account_id = ?", (account_id, account_id)
        ).fetchone():
            raise RepositoryError("Account is used by a bill; change or delete the bill first")
        with self.conn:
            self.conn.execute("DELETE FROM accounts WHERE id = ?", (account_id,))

    def account_balances(self) -> list[AccountBalance]:
        """Current balance of every account. Credit cards go negative as you spend on them."""
        rows = self.conn.execute(
            "SELECT a.id, a.name, a.kind, a.opening_balance_cents, a.color, a.number_last4, "
            "a.opening_balance_cents "
            "+ COALESCE((SELECT SUM(CASE t.type WHEN 'expense' THEN -t.amount_cents WHEN 'transfer' THEN -t.amount_cents "
            "WHEN 'adjustment' THEN t.amount_cents ELSE t.amount_cents END) FROM transactions t WHERE t.account_id = a.id), 0) "
            "+ COALESCE((SELECT SUM(t.amount_cents) FROM transactions t WHERE t.transfer_account_id = a.id), 0) AS balance "
            "FROM accounts a ORDER BY a.kind = 'credit_card', a.name"
        )
        return [AccountBalance(self._row_to_account(r), r["balance"]) for r in rows]

    # ---------- transactions ----------
    @staticmethod
    def _row_to_transaction(row: sqlite3.Row) -> Transaction:
        return Transaction(
            id=row["id"],
            amount_cents=row["amount_cents"],
            type=TransactionType(row["type"]),
            category_id=row["category_id"],
            occurred_on=date.fromisoformat(row["occurred_on"]),
            description=row["description"],
            account_id=row["account_id"],
            transfer_account_id=row["transfer_account_id"],
            category_name=row["category_name"],
            category_color=row["category_color"],
            category_icon=row["category_icon"],
            account_name=row["account_name"],
            transfer_account_name=row["transfer_account_name"],
            statement_id=row["statement_id"],
            import_ref=row["import_ref"],
        )

    def _validate_transaction(self, tx: Transaction) -> None:
        if tx.type == TransactionType.ADJUSTMENT:
            if tx.amount_cents == 0:
                raise RepositoryError("Adjustment amount cannot be zero")
            if tx.account_id is None or self.get_account(tx.account_id) is None:
                raise RepositoryError("Select an account")
            if tx.category_id is not None or tx.transfer_account_id is not None:
                raise RepositoryError("Adjustments cannot have a category")
            return
        if tx.amount_cents <= 0:
            raise RepositoryError("Amount must be greater than zero")
        if tx.account_id is None or self.get_account(tx.account_id) is None:
            raise RepositoryError("Select an account")
        if tx.type == TransactionType.TRANSFER:
            if tx.transfer_account_id is None or self.get_account(tx.transfer_account_id) is None:
                raise RepositoryError("Select the account the money goes to")
            if tx.transfer_account_id == tx.account_id:
                raise RepositoryError("Choose two different accounts for a transfer")
            tx.category_id = None
            return
        tx.transfer_account_id = None
        category = self.get_category(tx.category_id) if tx.category_id is not None else None
        if category is None:
            raise RepositoryError("Selected category does not exist")
        if category.type != tx.type.category_type:
            raise RepositoryError("Category type does not match transaction type")

    def _tx_params(self, tx: Transaction) -> tuple:
        return (
            tx.amount_cents,
            tx.type.value,
            tx.account_id,
            tx.category_id,
            tx.transfer_account_id,
            tx.occurred_on.isoformat(),
            tx.description.strip(),
        )

    def add_transaction(
        self,
        tx: Transaction,
        *,
        statement_id: int | None = None,
        import_ref: str | None = None,
    ) -> Transaction:
        self._validate_transaction(tx)
        if statement_id is not None:
            tx.statement_id = statement_id
        if import_ref is not None:
            tx.import_ref = import_ref
        with self.conn:
            cur = self.conn.execute(
                "INSERT INTO transactions (amount_cents, type, account_id, category_id, transfer_account_id, "
                "occurred_on, description, statement_id, import_ref) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (*self._tx_params(tx), tx.statement_id, tx.import_ref),
            )
        tx.id = cur.lastrowid
        return tx

    def update_transaction(self, tx: Transaction) -> None:
        if tx.id is None:
            raise ValueError("Transaction id is required")
        self._validate_transaction(tx)
        with self.conn:
            self.conn.execute(
                "UPDATE transactions SET amount_cents = ?, type = ?, account_id = ?, category_id = ?, "
                "transfer_account_id = ?, occurred_on = ?, description = ? WHERE id = ?",
                (*self._tx_params(tx), tx.id),
            )

    def delete_transaction(self, tx_id: int) -> None:
        with self.conn:
            self.conn.execute("DELETE FROM transactions WHERE id = ?", (tx_id,))

    def list_transactions(
        self,
        *,
        month: str | None = None,
        type_: TransactionType | None = None,
        category_id: int | None = None,
        account_id: int | None = None,
        search: str | None = None,
        limit: int | None = None,
    ) -> list[Transaction]:
        clauses: list[str] = []
        params: list = []
        if month:
            start, end = _month_bounds(month)
            clauses.append("t.occurred_on >= ? AND t.occurred_on < ?")
            params += [start, end]
        if type_ is not None:
            clauses.append("t.type = ?")
            params.append(type_.value)
        if category_id is not None:
            clauses.append("t.category_id = ?")
            params.append(category_id)
        if account_id is not None:
            clauses.append("(t.account_id = ? OR t.transfer_account_id = ?)")
            params += [account_id, account_id]
        if search:
            escaped = search.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            clauses.append(
                "(t.description LIKE ? ESCAPE '\\' OR c.name LIKE ? ESCAPE '\\' "
                "OR a.name LIKE ? ESCAPE '\\' OR ta.name LIKE ? ESCAPE '\\')"
            )
            params += [f"%{escaped}%"] * 4
        sql = (
            "SELECT t.id, t.amount_cents, t.type, t.category_id, t.account_id, t.transfer_account_id, "
            "t.occurred_on, t.description, t.statement_id, t.import_ref, c.name AS category_name, "
            "c.color AS category_color, c.icon AS category_icon, a.name AS account_name, "
            "ta.name AS transfer_account_name "
            "FROM transactions t "
            "JOIN accounts a ON a.id = t.account_id "
            "LEFT JOIN categories c ON c.id = t.category_id "
            "LEFT JOIN accounts ta ON ta.id = t.transfer_account_id"
        )
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY t.occurred_on DESC, t.id DESC"
        if limit:
            sql += " LIMIT ?"
            params.append(limit)
        return [self._row_to_transaction(r) for r in self.conn.execute(sql, params)]

    def transaction_months(self) -> list[str]:
        rows = self.conn.execute(
            "SELECT DISTINCT substr(occurred_on, 1, 7) AS m FROM transactions ORDER BY m DESC"
        )
        return [r["m"] for r in rows]

    _FIND_SQL = (
        "SELECT t.id, t.amount_cents, t.type, t.category_id, t.account_id, t.transfer_account_id, "
        "t.occurred_on, t.description, t.statement_id, t.import_ref, c.name AS category_name, "
        "c.color AS category_color, c.icon AS category_icon, a.name AS account_name, "
        "ta.name AS transfer_account_name "
        "FROM transactions t JOIN accounts a ON a.id = t.account_id "
        "LEFT JOIN categories c ON c.id = t.category_id "
        "LEFT JOIN accounts ta ON ta.id = t.transfer_account_id"
    )

    def find_transaction(
        self, account_id: int, occurred_on: date, amount_cents: int, description: str
    ) -> Transaction | None:
        """An existing transaction matching an imported row (same account, date, amount and description)."""
        normalized = " ".join(description.upper().split())
        row = self.conn.execute(
            self._FIND_SQL
            + " WHERE t.account_id = ? AND t.occurred_on = ? AND t.amount_cents = ? "
            "AND UPPER(TRIM(t.description)) = ? ORDER BY t.id LIMIT 1",
            (account_id, occurred_on.isoformat(), amount_cents, normalized),
        ).fetchone()
        return self._row_to_transaction(row) if row else None

    def has_transactions_before(self, account_id: int, before: date) -> bool:
        """Whether the account already has activity before ``before`` (used before setting an opening balance)."""
        row = self.conn.execute(
            "SELECT 1 FROM transactions WHERE (account_id = ? OR transfer_account_id = ?) "
            "AND occurred_on < ? LIMIT 1",
            (account_id, account_id, before.isoformat()),
        ).fetchone()
        return row is not None

    # ---------- statements, reconciliations & rules ----------
    @staticmethod
    def _row_to_statement(row: sqlite3.Row) -> Statement:
        return Statement(
            id=row["id"],
            account_id=row["account_id"],
            institution=row["institution"],
            account_label=row["account_label"],
            account_last4=row["account_last4"],
            period_start=date.fromisoformat(row["period_start"]),
            period_end=date.fromisoformat(row["period_end"]),
            beginning_cents=row["beginning_cents"],
            deposits_cents=row["deposits_cents"],
            withdrawals_cents=row["withdrawals_cents"],
            fees_cents=row["fees_cents"],
            ending_cents=row["ending_cents"],
            file_sha256=row["file_sha256"],
        )

    _STATEMENT_SELECT = (
        "SELECT id, account_id, institution, account_label, account_last4, period_start, period_end, "
        "beginning_cents, deposits_cents, withdrawals_cents, fees_cents, ending_cents, file_sha256 "
        "FROM statements"
    )

    @staticmethod
    def _statement_params(statement: Statement) -> tuple:
        return (
            statement.account_id,
            statement.institution,
            statement.account_label,
            statement.account_last4,
            statement.period_start.isoformat(),
            statement.period_end.isoformat(),
            statement.beginning_cents,
            statement.deposits_cents,
            statement.withdrawals_cents,
            statement.fees_cents,
            statement.ending_cents,
            statement.file_sha256,
        )

    def add_statement(self, statement: Statement) -> Statement:
        try:
            with self.conn:
                cur = self.conn.execute(
                    "INSERT INTO statements (account_id, institution, account_label, account_last4, period_start, "
                    "period_end, beginning_cents, deposits_cents, withdrawals_cents, fees_cents, ending_cents, "
                    "file_sha256) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    self._statement_params(statement),
                )
        except sqlite3.IntegrityError as exc:
            raise RepositoryError("This statement was already imported") from exc
        statement.id = cur.lastrowid
        return statement

    def statement_by_sha256(self, file_sha256: str) -> Statement | None:
        row = self.conn.execute(
            self._STATEMENT_SELECT + " WHERE file_sha256 = ?", (file_sha256,)
        ).fetchone()
        return self._row_to_statement(row) if row else None

    def list_statements(self) -> list[Statement]:
        """All imported statements, newest first."""
        rows = self.conn.execute(self._STATEMENT_SELECT + " ORDER BY id DESC")
        return [self._row_to_statement(r) for r in rows]

    def delete_statement(self, statement_id: int) -> None:
        """Undo an import: drop its transactions, reconciliations and the statement row atomically."""
        with self.conn:
            self.conn.execute("DELETE FROM transactions WHERE statement_id = ?", (statement_id,))
            self.conn.execute("DELETE FROM reconciliations WHERE statement_id = ?", (statement_id,))
            self.conn.execute("DELETE FROM statements WHERE id = ?", (statement_id,))

    def import_statement(
        self,
        statement: Statement,
        rows: Sequence[ImportRow],
        *,
        opening_balance_cents: int | None = None,
        reconciliation: Reconciliation | None = None,
    ) -> Statement:
        """Import a statement as one unit.

        The statement row, every included transaction, the optional opening-balance update and the
        optional reconciliation are written inside a single transaction; any failure rolls all of
        them back. When ``reconciliation`` is supplied its computed/additional fields are filled in
        from the state written by this transaction.
        """
        statement.id = None
        try:
            with self.conn:
                if self.conn.execute(
                    "SELECT 1 FROM statements WHERE file_sha256 = ?", (statement.file_sha256,)
                ).fetchone():
                    raise RepositoryError("This statement was already imported")
                cur = self.conn.execute(
                    "INSERT INTO statements (account_id, institution, account_label, account_last4, period_start, "
                    "period_end, beginning_cents, deposits_cents, withdrawals_cents, fees_cents, ending_cents, "
                    "file_sha256) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    self._statement_params(statement),
                )
                statement.id = cur.lastrowid
                for index, row in enumerate(rows, start=1):
                    if not row.include:
                        continue
                    tx = row.transaction
                    self._validate_transaction(tx)
                    tx.statement_id = statement.id
                    tx.import_ref = f"{statement.file_sha256}:{index}"
                    tx_cur = self.conn.execute(
                        "INSERT INTO transactions (amount_cents, type, account_id, category_id, "
                        "transfer_account_id, occurred_on, description, statement_id, import_ref) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (*self._tx_params(tx), tx.statement_id, tx.import_ref),
                    )
                    tx.id = tx_cur.lastrowid
                if opening_balance_cents is not None:
                    updated = self.conn.execute(
                        "UPDATE accounts SET opening_balance_cents = ? WHERE id = ?",
                        (opening_balance_cents, statement.account_id),
                    )
                    if updated.rowcount != 1:
                        raise RepositoryError("Select an account")
                if reconciliation is not None:
                    reconciliation.statement_id = statement.id
                    reconciliation.computed_cents = self.account_balance_as_of(
                        statement.account_id, reconciliation.as_of
                    )
                    reconciliation.difference_cents = (
                        reconciliation.computed_cents - reconciliation.stated_cents
                    )
                    rec_cur = self.conn.execute(
                        "INSERT INTO reconciliations (account_id, statement_id, as_of, stated_cents, "
                        "computed_cents, difference_cents, note) VALUES (?, ?, ?, ?, ?, ?, ?)",
                        (
                            reconciliation.account_id,
                            reconciliation.statement_id,
                            reconciliation.as_of.isoformat(),
                            reconciliation.stated_cents,
                            reconciliation.computed_cents,
                            reconciliation.difference_cents,
                            reconciliation.note,
                        ),
                    )
                    reconciliation.id = rec_cur.lastrowid
                    reconciliation.created_at = self.conn.execute(
                        "SELECT created_at FROM reconciliations WHERE id = ?", (reconciliation.id,)
                    ).fetchone()["created_at"]
                return statement
        except RepositoryError:
            statement.id = None
            raise
        except sqlite3.IntegrityError as exc:
            statement.id = None
            if "file_sha256" in str(exc):
                raise RepositoryError("This statement was already imported") from exc
            raise RepositoryError("Could not save the imported statement") from exc

    @staticmethod
    def _row_to_reconciliation(row: sqlite3.Row) -> Reconciliation:
        return Reconciliation(
            id=row["id"],
            account_id=row["account_id"],
            statement_id=row["statement_id"],
            as_of=date.fromisoformat(row["as_of"]),
            stated_cents=row["stated_cents"],
            computed_cents=row["computed_cents"],
            difference_cents=row["difference_cents"],
            note=row["note"],
            created_at=row["created_at"],
        )

    def add_reconciliation(self, reconciliation: Reconciliation) -> Reconciliation:
        with self.conn:
            cur = self.conn.execute(
                "INSERT INTO reconciliations (account_id, statement_id, as_of, stated_cents, computed_cents, "
                "difference_cents, note) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    reconciliation.account_id,
                    reconciliation.statement_id,
                    reconciliation.as_of.isoformat(),
                    reconciliation.stated_cents,
                    reconciliation.computed_cents,
                    reconciliation.difference_cents,
                    reconciliation.note,
                ),
            )
        reconciliation.id = cur.lastrowid
        row = self.conn.execute(
            "SELECT created_at FROM reconciliations WHERE id = ?", (reconciliation.id,)
        ).fetchone()
        reconciliation.created_at = row["created_at"]
        return reconciliation

    def list_reconciliations(self, account_id: int) -> list[Reconciliation]:
        rows = self.conn.execute(
            "SELECT id, account_id, statement_id, as_of, stated_cents, computed_cents, difference_cents, note, "
            "created_at FROM reconciliations WHERE account_id = ? ORDER BY as_of DESC, id DESC",
            (account_id,),
        )
        return [self._row_to_reconciliation(r) for r in rows]

    def account_balance_as_of(self, account_id: int, as_of: date) -> int:
        """Ledger balance of one account through ``as_of``, inclusive."""
        row = self.conn.execute(
            "SELECT a.opening_balance_cents "
            "+ COALESCE((SELECT SUM(CASE t.type WHEN 'expense' THEN -t.amount_cents "
            "WHEN 'transfer' THEN -t.amount_cents WHEN 'adjustment' THEN t.amount_cents "
            "ELSE t.amount_cents END) FROM transactions t "
            "WHERE t.account_id = a.id AND t.occurred_on <= ?), 0) "
            "+ COALESCE((SELECT SUM(t.amount_cents) FROM transactions t "
            "WHERE t.transfer_account_id = a.id AND t.occurred_on <= ?), 0) "
            "FROM accounts a WHERE a.id = ?",
            (as_of.isoformat(), as_of.isoformat(), account_id),
        ).fetchone()
        if row is None:
            raise RepositoryError("Account not found")
        return row[0]

    @staticmethod
    def _row_to_category_rule(row: sqlite3.Row) -> CategoryRule:
        return CategoryRule(
            id=row["id"], match_text=row["match_text"], category_id=row["category_id"], priority=row["priority"]
        )

    def list_category_rules(self) -> list[CategoryRule]:
        rows = self.conn.execute(
            "SELECT id, match_text, category_id, priority FROM category_rules "
            "ORDER BY priority DESC, length(match_text) DESC, id"
        )
        return [self._row_to_category_rule(r) for r in rows]

    def add_category_rule(self, match_text: str, category_id: int, priority: int = 0) -> CategoryRule:
        match_text = match_text.strip()
        if not match_text:
            raise RepositoryError("Rule text is required")
        if self.get_category(category_id) is None:
            raise RepositoryError("Selected category does not exist")
        # Saving the same mapping twice must not create a duplicate rule. Compare with
        # casefold so "Kroger" and "kroger" are treated as the same rule, matching the
        # case-insensitive lookup used by suggest_category_id.
        wanted = match_text.casefold()
        for row in self.conn.execute(
            "SELECT id, match_text, category_id, priority FROM category_rules WHERE category_id = ?",
            (category_id,),
        ):
            if row["match_text"].casefold() == wanted:
                return self._row_to_category_rule(row)
        with self.conn:
            cur = self.conn.execute(
                "INSERT INTO category_rules (match_text, category_id, priority) VALUES (?, ?, ?)",
                (match_text, category_id, priority),
            )
        return CategoryRule(
            id=cur.lastrowid, match_text=match_text, category_id=category_id, priority=priority
        )

    def delete_category_rule(self, rule_id: int) -> None:
        with self.conn:
            self.conn.execute("DELETE FROM category_rules WHERE id = ?", (rule_id,))

    def suggest_category_id(self, description: str, type_: TransactionType) -> int | None:
        """Best saved rule for a description: highest priority, then longest match text."""
        wanted = type_.category_type
        if wanted is None:
            return None
        rows = self.conn.execute(
            "SELECT r.category_id, r.match_text FROM category_rules r "
            "JOIN categories c ON c.id = r.category_id WHERE c.type = ? "
            "ORDER BY r.priority DESC, length(r.match_text) DESC, r.id",
            (wanted.value,),
        )
        needle = description.casefold()
        for row in rows:
            if row["match_text"].casefold() in needle:
                return row["category_id"]
        return None

    # ---------- aggregates ----------
    def summary(self, month: str | None = None) -> Summary:
        sql = (
            f"SELECT COALESCE(SUM({_INCOME_SQL.format(t='')}), 0) AS income, "
            f"COALESCE(SUM({_SPEND_SQL.format(t='')}), 0) AS expense FROM transactions"
        )
        params: tuple = ()
        if month:
            sql += " WHERE occurred_on >= ? AND occurred_on < ?"
            params = _month_bounds(month)
        row = self.conn.execute(sql, params).fetchone()
        return Summary(income_cents=row["income"], expense_cents=row["expense"])

    def totals_by_category(self, month: str, type_: TransactionType = TransactionType.EXPENSE) -> list[CategoryTotal]:
        """Per-category totals for a month. Expense totals are net of refunds."""
        start, end = _month_bounds(month)
        amount = _SPEND_SQL if type_ == TransactionType.EXPENSE else _INCOME_SQL
        rows = self.conn.execute(
            f"SELECT c.id, c.name, c.color, SUM({amount.format(t='t.')}) AS total FROM transactions t "
            "JOIN categories c ON c.id = t.category_id "
            "WHERE c.type = ? AND t.occurred_on >= ? AND t.occurred_on < ? "
            "GROUP BY c.id HAVING total > 0 ORDER BY total DESC",
            (type_.value, start, end),
        )
        return [CategoryTotal(r["id"], r["name"], r["color"], r["total"]) for r in rows]

    def monthly_totals(self, months: list[str]) -> list[MonthTotal]:
        if not months:
            return []
        start, _ = _month_bounds(min(months))
        _, end = _month_bounds(max(months))
        rows = self.conn.execute(
            "SELECT substr(occurred_on, 1, 7) AS m, "
            f"COALESCE(SUM({_INCOME_SQL.format(t='')}), 0) AS income, "
            f"COALESCE(SUM({_SPEND_SQL.format(t='')}), 0) AS expense "
            "FROM transactions WHERE occurred_on >= ? AND occurred_on < ? GROUP BY m",
            (start, end),
        )
        by_month = {r["m"]: (r["income"], r["expense"]) for r in rows}
        return [MonthTotal(m, *by_month.get(m, (0, 0))) for m in months]

    # ---------- budgets ----------
    def set_budget(self, budget: Budget) -> Budget:
        if budget.limit_cents <= 0:
            raise RepositoryError("Budget limit must be greater than zero")
        category = self.get_category(budget.category_id)
        if category is None or category.type != TransactionType.EXPENSE:
            raise RepositoryError("Budgets can only be set for expense categories")
        with self.conn:
            self.conn.execute(
                "INSERT INTO budgets (category_id, month, limit_cents) VALUES (?, ?, ?) "
                "ON CONFLICT (category_id, month) DO UPDATE SET limit_cents = excluded.limit_cents",
                (budget.category_id, budget.month, budget.limit_cents),
            )
        budget.id = self.conn.execute(
            "SELECT id FROM budgets WHERE category_id = ? AND month = ?", (budget.category_id, budget.month)
        ).fetchone()["id"]
        return budget

    def delete_budget(self, budget_id: int) -> None:
        with self.conn:
            self.conn.execute("DELETE FROM budgets WHERE id = ?", (budget_id,))

    def list_budgets(self, month: str) -> list[Budget]:
        rows = self.conn.execute(
            "SELECT id, category_id, month, limit_cents FROM budgets WHERE month = ?", (month,)
        )
        return [Budget(id=r["id"], category_id=r["category_id"], month=r["month"], limit_cents=r["limit_cents"]) for r in rows]

    def copy_budgets(self, from_month: str, to_month: str) -> int:
        with self.conn:
            cur = self.conn.execute(
                "INSERT OR IGNORE INTO budgets (category_id, month, limit_cents) "
                "SELECT category_id, ?, limit_cents FROM budgets WHERE month = ?",
                (to_month, from_month),
            )
        return cur.rowcount

    def budget_months(self) -> list[str]:
        rows = self.conn.execute("SELECT DISTINCT month FROM budgets ORDER BY month DESC")
        return [r["month"] for r in rows]

    def budget_progress(self, month: str) -> list[BudgetProgress]:
        start, end = _month_bounds(month)
        rows = self.conn.execute(
            "SELECT b.id, b.category_id, c.name, c.color, c.icon, b.limit_cents, "
            f"COALESCE((SELECT SUM({_SPEND_SQL.format(t='t.')}) FROM transactions t WHERE t.category_id = b.category_id "
            "AND t.occurred_on >= ? AND t.occurred_on < ?), 0) AS spent "
            "FROM budgets b JOIN categories c ON c.id = b.category_id WHERE b.month = ? ORDER BY c.name",
            (start, end, month),
        )
        return [
            BudgetProgress(r["id"], r["category_id"], r["name"], r["color"], r["icon"], r["limit_cents"], r["spent"])
            for r in rows
        ]

    # ---------- bills & reminders ----------
    _BILL_SELECT = (
        "SELECT b.id, b.name, b.amount_cents, b.frequency, b.next_due, b.anchor_day, b.account_id, b.category_id, "
        "b.pay_to_account_id, b.active, a.name AS account_name, c.name AS category_name, c.color AS category_color, "
        "c.icon AS category_icon, pa.name AS pay_to_account_name "
        "FROM bills b JOIN accounts a ON a.id = b.account_id "
        "LEFT JOIN categories c ON c.id = b.category_id "
        "LEFT JOIN accounts pa ON pa.id = b.pay_to_account_id"
    )

    @staticmethod
    def _row_to_bill(row: sqlite3.Row) -> Bill:
        return Bill(
            id=row["id"],
            name=row["name"],
            amount_cents=row["amount_cents"],
            frequency=BillFrequency(row["frequency"]),
            next_due=date.fromisoformat(row["next_due"]),
            anchor_day=row["anchor_day"],
            account_id=row["account_id"],
            category_id=row["category_id"],
            pay_to_account_id=row["pay_to_account_id"],
            active=bool(row["active"]),
            account_name=row["account_name"],
            category_name=row["category_name"],
            category_color=row["category_color"],
            category_icon=row["category_icon"],
            pay_to_account_name=row["pay_to_account_name"],
        )

    def list_bills(self, *, include_inactive: bool = True) -> list[Bill]:
        sql = self._BILL_SELECT + ("" if include_inactive else " WHERE b.active = 1")
        sql += " ORDER BY b.active DESC, b.next_due, b.name"
        return [self._row_to_bill(r) for r in self.conn.execute(sql)]

    def get_bill(self, bill_id: int) -> Bill | None:
        row = self.conn.execute(self._BILL_SELECT + " WHERE b.id = ?", (bill_id,)).fetchone()
        return self._row_to_bill(row) if row else None

    def _validate_bill(self, bill: Bill) -> None:
        if not bill.name.strip():
            raise RepositoryError("Bill name is required")
        if bill.amount_cents < 0:
            raise RepositoryError("Amount cannot be negative")
        if self.get_account(bill.account_id) is None:
            raise RepositoryError("Select the account the payment comes from")
        if bill.is_card_payment:
            if self.get_account(bill.pay_to_account_id) is None:
                raise RepositoryError("Select the card or account being paid")
            if bill.pay_to_account_id == bill.account_id:
                raise RepositoryError("Pay from a different account than the one being paid")
            bill.category_id = None
            return
        if bill.amount_cents == 0:
            raise RepositoryError("Amount must be greater than zero")
        category = self.get_category(bill.category_id) if bill.category_id is not None else None
        if category is None or category.type != TransactionType.EXPENSE:
            raise RepositoryError("Select an expense category")

    def _bill_params(self, bill: Bill) -> tuple:
        if bill.anchor_day is None:
            bill.anchor_day = bill.next_due.day
        return (
            bill.name.strip(),
            bill.amount_cents,
            bill.frequency.value,
            bill.next_due.isoformat(),
            bill.anchor_day,
            bill.account_id,
            bill.category_id,
            bill.pay_to_account_id,
            int(bill.active),
        )

    def add_bill(self, bill: Bill) -> Bill:
        self._validate_bill(bill)
        with self.conn:
            cur = self.conn.execute(
                "INSERT INTO bills (name, amount_cents, frequency, next_due, anchor_day, account_id, category_id, "
                "pay_to_account_id, active) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                self._bill_params(bill),
            )
        bill.id = cur.lastrowid
        return bill

    def update_bill(self, bill: Bill) -> None:
        if bill.id is None:
            raise ValueError("Bill id is required")
        self._validate_bill(bill)
        with self.conn:
            self.conn.execute(
                "UPDATE bills SET name = ?, amount_cents = ?, frequency = ?, next_due = ?, anchor_day = ?, "
                "account_id = ?, category_id = ?, pay_to_account_id = ?, active = ? WHERE id = ?",
                (*self._bill_params(bill), bill.id),
            )

    def delete_bill(self, bill_id: int) -> None:
        with self.conn:
            self.conn.execute("DELETE FROM bills WHERE id = ?", (bill_id,))

    def amount_due(self, bill: Bill) -> int:
        """What to pay now: the fixed amount, or the full balance owed for an open-amount card payment."""
        if bill.amount_cents or not bill.is_card_payment:
            return bill.amount_cents
        owed = next((b.owed_cents for b in self.account_balances() if b.account.id == bill.pay_to_account_id), 0)
        return owed

    def due_bills(self, today: date, within_days: int) -> list[DueBill]:
        """Active bills that are overdue or due within ``within_days`` of ``today``, soonest first."""
        horizon = date.fromordinal(today.toordinal() + max(0, within_days)).isoformat()
        rows = self.conn.execute(
            self._BILL_SELECT + " WHERE b.active = 1 AND b.next_due <= ? ORDER BY b.next_due, b.name", (horizon,)
        )
        bills = [self._row_to_bill(r) for r in rows]
        return [DueBill(b, (b.next_due - today).days, self.amount_due(b)) for b in bills]

    def mark_bill_paid(self, bill_id: int, *, paid_on: date, amount_cents: int, account_id: int | None = None) -> Transaction:
        """Record the payment (expense or transfer) and roll the bill to its next due date, atomically."""
        bill = self.get_bill(bill_id)
        if bill is None or not bill.active:
            raise RepositoryError("This bill is no longer active")
        tx = Transaction(
            amount_cents=amount_cents,
            type=TransactionType.TRANSFER if bill.is_card_payment else TransactionType.EXPENSE,
            category_id=bill.category_id,
            occurred_on=paid_on,
            description=bill.name,
            account_id=account_id or bill.account_id,
            transfer_account_id=bill.pay_to_account_id,
        )
        self._validate_transaction(tx)
        following = next_due_date(bill.next_due, bill.frequency, bill.anchor_day or bill.next_due.day)
        with self.conn:
            cur = self.conn.execute(
                "INSERT INTO transactions (amount_cents, type, account_id, category_id, transfer_account_id, "
                "occurred_on, description) VALUES (?, ?, ?, ?, ?, ?, ?)",
                self._tx_params(tx),
            )
            if following is None:
                self.conn.execute("UPDATE bills SET active = 0 WHERE id = ?", (bill_id,))
            else:
                self.conn.execute("UPDATE bills SET next_due = ? WHERE id = ?", (following.isoformat(), bill_id))
        tx.id = cur.lastrowid
        return tx

    def skip_bill(self, bill_id: int) -> None:
        """Move a bill to its next due date without recording a payment."""
        bill = self.get_bill(bill_id)
        if bill is None:
            return
        following = next_due_date(bill.next_due, bill.frequency, bill.anchor_day or bill.next_due.day)
        with self.conn:
            if following is None:
                self.conn.execute("UPDATE bills SET active = 0 WHERE id = ?", (bill_id,))
            else:
                self.conn.execute("UPDATE bills SET next_due = ? WHERE id = ?", (following.isoformat(), bill_id))

    def reminder_days(self) -> int:
        try:
            return max(0, int(self.get_setting(REMINDER_DAYS_SETTING, str(DEFAULT_REMINDER_DAYS))))
        except ValueError:
            return DEFAULT_REMINDER_DAYS

    def set_reminder_days(self, days: int) -> None:
        self.set_setting(REMINDER_DAYS_SETTING, str(max(0, min(60, days))))

    # ---------- settings ----------
    def get_setting(self, key: str, default: str | None = None) -> str | None:
        row = self.conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else default

    def set_setting(self, key: str, value: str) -> None:
        with self.conn:
            self.conn.execute(
                "INSERT INTO settings (key, value) VALUES (?, ?) "
                "ON CONFLICT (key) DO UPDATE SET value = excluded.value",
                (key, value),
            )
