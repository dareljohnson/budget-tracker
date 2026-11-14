"""SQLite connection management and schema migrations."""

from __future__ import annotations

import sqlite3
from pathlib import Path

DEFAULT_CATEGORIES: list[tuple[str, str, str, str]] = [
    ("Salary", "income", "#2E7D32", "WORK"),
    ("Freelance", "income", "#00897B", "LAPTOP_MAC"),
    ("Investments", "income", "#3949AB", "TRENDING_UP"),
    ("Other Income", "income", "#6D4C41", "ATTACH_MONEY"),
    ("Housing", "expense", "#5E35B1", "HOME"),
    ("Groceries", "expense", "#43A047", "SHOPPING_CART"),
    ("Dining Out", "expense", "#FB8C00", "RESTAURANT"),
    ("Transportation", "expense", "#1E88E5", "DIRECTIONS_CAR"),
    ("Utilities", "expense", "#F9A825", "BOLT"),
    ("Health", "expense", "#E53935", "LOCAL_HOSPITAL"),
    ("Entertainment", "expense", "#D81B60", "MOVIE"),
    ("Shopping", "expense", "#8E24AA", "SHOPPING_BAG"),
    ("Other", "expense", "#757575", "CATEGORY"),
]

# Each entry upgrades the schema by one version (tracked in PRAGMA user_version).
MIGRATIONS: list[str] = [
    """
    CREATE TABLE categories (
        id      INTEGER PRIMARY KEY,
        name    TEXT NOT NULL COLLATE NOCASE,
        type    TEXT NOT NULL CHECK (type IN ('income', 'expense')),
        color   TEXT NOT NULL DEFAULT '#607D8B',
        icon    TEXT NOT NULL DEFAULT 'CATEGORY',
        UNIQUE (name, type)
    );

    CREATE TABLE transactions (
        id           INTEGER PRIMARY KEY,
        amount_cents INTEGER NOT NULL CHECK (amount_cents > 0),
        type         TEXT NOT NULL CHECK (type IN ('income', 'expense')),
        category_id  INTEGER NOT NULL REFERENCES categories(id) ON DELETE RESTRICT,
        occurred_on  TEXT NOT NULL,
        description  TEXT NOT NULL DEFAULT '',
        created_at   TEXT NOT NULL DEFAULT (datetime('now'))
    );
    CREATE INDEX ix_transactions_occurred_on ON transactions(occurred_on);
    CREATE INDEX ix_transactions_category ON transactions(category_id);

    CREATE TABLE budgets (
        id          INTEGER PRIMARY KEY,
        category_id INTEGER NOT NULL REFERENCES categories(id) ON DELETE CASCADE,
        month       TEXT NOT NULL,
        limit_cents INTEGER NOT NULL CHECK (limit_cents > 0),
        UNIQUE (category_id, month)
    );

    CREATE TABLE settings (
        key   TEXT PRIMARY KEY,
        value TEXT NOT NULL
    );
    """,
    # v2: accounts (checking, credit card, ...), transfers between accounts and refunds.
    # Existing transactions are assigned to a default "Checking" account.
    """
    CREATE TABLE accounts (
        id                    INTEGER PRIMARY KEY,
        name                  TEXT NOT NULL UNIQUE COLLATE NOCASE,
        kind                  TEXT NOT NULL CHECK (kind IN ('checking', 'savings', 'credit_card', 'cash', 'other')),
        opening_balance_cents INTEGER NOT NULL DEFAULT 0,
        color                 TEXT NOT NULL DEFAULT '#3949AB',
        created_at            TEXT NOT NULL DEFAULT (datetime('now'))
    );
    INSERT INTO accounts (name, kind, color) VALUES ('Checking', 'checking', '#1E88E5');

    CREATE TABLE transactions_new (
        id                  INTEGER PRIMARY KEY,
        amount_cents        INTEGER NOT NULL CHECK (amount_cents > 0),
        type                TEXT NOT NULL CHECK (type IN ('income', 'expense', 'refund', 'transfer')),
        account_id          INTEGER NOT NULL REFERENCES accounts(id) ON DELETE RESTRICT,
        category_id         INTEGER REFERENCES categories(id) ON DELETE RESTRICT,
        transfer_account_id INTEGER REFERENCES accounts(id) ON DELETE RESTRICT,
        occurred_on         TEXT NOT NULL,
        description         TEXT NOT NULL DEFAULT '',
        created_at          TEXT NOT NULL DEFAULT (datetime('now')),
        CHECK ((type = 'transfer') = (category_id IS NULL)),
        CHECK ((type = 'transfer') = (transfer_account_id IS NOT NULL)),
        CHECK (transfer_account_id IS NULL OR transfer_account_id <> account_id)
    );
    INSERT INTO transactions_new (id, amount_cents, type, account_id, category_id, occurred_on, description, created_at)
        SELECT id, amount_cents, type, (SELECT MIN(id) FROM accounts), category_id, occurred_on, description, created_at
        FROM transactions;
    DROP TABLE transactions;
    ALTER TABLE transactions_new RENAME TO transactions;
    CREATE INDEX ix_transactions_occurred_on ON transactions(occurred_on);
    CREATE INDEX ix_transactions_category ON transactions(category_id);
    CREATE INDEX ix_transactions_account ON transactions(account_id);
    CREATE INDEX ix_transactions_transfer_account ON transactions(transfer_account_id);
    """,
    # v3: bills and credit card payments with due dates and reminders.
    # A bill either has an expense category (paid as an expense) or a pay_to account (paid as a transfer,
    # e.g. a credit card payment). amount_cents = 0 on a card payment means "pay the full amount owed".
    """
    CREATE TABLE bills (
        id                INTEGER PRIMARY KEY,
        name              TEXT NOT NULL,
        amount_cents      INTEGER NOT NULL CHECK (amount_cents >= 0),
        frequency         TEXT NOT NULL CHECK (frequency IN ('once', 'weekly', 'monthly', 'yearly')),
        next_due          TEXT NOT NULL,
        anchor_day        INTEGER NOT NULL CHECK (anchor_day BETWEEN 1 AND 31),
        account_id        INTEGER NOT NULL REFERENCES accounts(id) ON DELETE RESTRICT,
        category_id       INTEGER REFERENCES categories(id) ON DELETE RESTRICT,
        pay_to_account_id INTEGER REFERENCES accounts(id) ON DELETE RESTRICT,
        active            INTEGER NOT NULL DEFAULT 1,
        created_at        TEXT NOT NULL DEFAULT (datetime('now')),
        CHECK ((category_id IS NULL) <> (pay_to_account_id IS NULL)),
        CHECK (pay_to_account_id IS NULL OR pay_to_account_id <> account_id),
        CHECK (category_id IS NULL OR amount_cents > 0)
    );
    CREATE INDEX ix_bills_next_due ON bills(next_due) WHERE active = 1;
    """,
    # v4: bank statement import and reconciliation. Adjustments are signed balance corrections
    # that are neither income nor spending. Statements and reconciliations are per account;
    # category rules map imported descriptions to categories.
    """
    ALTER TABLE accounts ADD COLUMN number_last4 TEXT;

    CREATE TABLE statements (
        id                INTEGER PRIMARY KEY,
        account_id        INTEGER NOT NULL REFERENCES accounts(id) ON DELETE RESTRICT,
        institution       TEXT NOT NULL,
        account_label     TEXT NOT NULL,
        account_last4     TEXT,
        period_start      TEXT NOT NULL,
        period_end        TEXT NOT NULL,
        beginning_cents   INTEGER NOT NULL,
        deposits_cents    INTEGER NOT NULL,
        withdrawals_cents INTEGER NOT NULL,
        fees_cents        INTEGER NOT NULL,
        ending_cents      INTEGER NOT NULL,
        file_sha256       TEXT NOT NULL UNIQUE,
        imported_at       TEXT NOT NULL DEFAULT (datetime('now'))
    );
    CREATE INDEX ix_statements_account ON statements(account_id);

    CREATE TABLE reconciliations (
        id               INTEGER PRIMARY KEY,
        account_id       INTEGER NOT NULL REFERENCES accounts(id) ON DELETE RESTRICT,
        statement_id     INTEGER REFERENCES statements(id) ON DELETE SET NULL,
        as_of            TEXT NOT NULL,
        stated_cents     INTEGER NOT NULL,
        computed_cents   INTEGER NOT NULL,
        difference_cents INTEGER NOT NULL,
        note             TEXT NOT NULL DEFAULT '',
        created_at       TEXT NOT NULL DEFAULT (datetime('now'))
    );
    CREATE INDEX ix_reconciliations_account ON reconciliations(account_id, as_of DESC);

    CREATE TABLE category_rules (
        id          INTEGER PRIMARY KEY,
        match_text  TEXT NOT NULL COLLATE NOCASE,
        category_id INTEGER NOT NULL REFERENCES categories(id) ON DELETE CASCADE,
        priority    INTEGER NOT NULL DEFAULT 0
    );

    CREATE TABLE transactions_new (
        id                  INTEGER PRIMARY KEY,
        amount_cents        INTEGER NOT NULL,
        type                TEXT NOT NULL CHECK (type IN ('income','expense','refund','transfer','adjustment')),
        account_id          INTEGER NOT NULL REFERENCES accounts(id) ON DELETE RESTRICT,
        category_id         INTEGER REFERENCES categories(id) ON DELETE RESTRICT,
        transfer_account_id INTEGER REFERENCES accounts(id) ON DELETE RESTRICT,
        occurred_on         TEXT NOT NULL,
        description         TEXT NOT NULL DEFAULT '',
        statement_id        INTEGER REFERENCES statements(id) ON DELETE SET NULL,
        import_ref          TEXT,
        created_at          TEXT NOT NULL DEFAULT (datetime('now')),
        CHECK ((type = 'adjustment' AND amount_cents <> 0) OR (type <> 'adjustment' AND amount_cents > 0)),
        CHECK ((type IN ('transfer','adjustment')) = (category_id IS NULL)),
        CHECK ((type = 'transfer') = (transfer_account_id IS NOT NULL)),
        CHECK (transfer_account_id IS NULL OR transfer_account_id <> account_id)
    );
    INSERT INTO transactions_new (id, amount_cents, type, account_id, category_id,
        transfer_account_id, occurred_on, description, created_at)
    SELECT id, amount_cents, type, account_id, category_id,
        transfer_account_id, occurred_on, description, created_at FROM transactions;
    DROP TABLE transactions;
    ALTER TABLE transactions_new RENAME TO transactions;
    CREATE INDEX ix_transactions_occurred_on ON transactions(occurred_on);
    CREATE INDEX ix_transactions_category ON transactions(category_id);
    CREATE INDEX ix_transactions_account ON transactions(account_id);
    CREATE INDEX ix_transactions_transfer_account ON transactions(transfer_account_id);
    CREATE INDEX ix_transactions_statement ON transactions(statement_id);
    """,
    # v5: the minimum payment printed on each credit card statement, one row per card and month.
    # Paid status is derived from transfers into the card, not stored here.
    """
    CREATE TABLE minimum_payments (
        id           INTEGER PRIMARY KEY,
        account_id   INTEGER NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
        month        TEXT NOT NULL CHECK (month GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]'),
        amount_cents INTEGER NOT NULL CHECK (amount_cents > 0),
        UNIQUE (account_id, month)
    );
    CREATE INDEX ix_minimum_payments_account ON minimum_payments(account_id, month DESC);
    """,
    # v6: savings goals and the transfer tag that records a contribution to one.
    # Deleting a goal clears the tag on its transfers but keeps the transactions.
    """
    CREATE TABLE savings_goals (
        id            INTEGER PRIMARY KEY,
        name          TEXT NOT NULL,
        target_cents  INTEGER NOT NULL CHECK (target_cents > 0),
        target_date   TEXT NOT NULL,
        start_date    TEXT NOT NULL,
        account_id    INTEGER NOT NULL REFERENCES accounts(id) ON DELETE RESTRICT,
        initial_cents INTEGER NOT NULL DEFAULT 0 CHECK (initial_cents >= 0),
        created_at    TEXT NOT NULL DEFAULT (datetime('now'))
    );
    CREATE INDEX ix_savings_goals_account ON savings_goals(account_id);

    ALTER TABLE transactions ADD COLUMN goal_id INTEGER REFERENCES savings_goals(id) ON DELETE SET NULL;
    CREATE INDEX ix_transactions_goal ON transactions(goal_id);
    """,
    # v7: debts and the transaction tag that records a payment to one. A debt linked to a credit
    # card account tracks that card's balance; the unique partial index allows at most one debt
    # per linked account. Payments are ordinary transactions carrying debt_id.
    """
    CREATE TABLE debts (
        id                     INTEGER PRIMARY KEY,
        name                   TEXT NOT NULL,
        kind                   TEXT NOT NULL CHECK (kind IN ('credit_card','auto','student','personal','mortgage','other')),
        balance_cents          INTEGER NOT NULL DEFAULT 0 CHECK (balance_cents >= 0),
        apr_bps                INTEGER NOT NULL CHECK (apr_bps BETWEEN 0 AND 10000),
        minimum_cents          INTEGER NOT NULL DEFAULT 0 CHECK (minimum_cents >= 0),
        original_balance_cents INTEGER CHECK (original_balance_cents IS NULL OR original_balance_cents >= 0),
        account_id             INTEGER REFERENCES accounts(id) ON DELETE SET NULL,
        created_at             TEXT NOT NULL DEFAULT (datetime('now'))
    );
    CREATE UNIQUE INDEX ux_debts_account ON debts(account_id) WHERE account_id IS NOT NULL;

    ALTER TABLE transactions ADD COLUMN debt_id INTEGER REFERENCES debts(id) ON DELETE SET NULL;
    CREATE INDEX ix_transactions_debt ON transactions(debt_id);

    INSERT OR IGNORE INTO categories (name, type, color, icon)
    VALUES ('Debt payments', 'expense', '#546E7A', 'PAYMENTS');
    """,
    # v8: loan accounts. SQLite cannot alter a CHECK constraint in place, so the accounts table is
    # rebuilt. Foreign keys must be off while accounts is dropped; the runner re-enables them and
    # runs PRAGMA foreign_key_check afterwards.
    """
    CREATE TABLE accounts_new (
        id                    INTEGER PRIMARY KEY,
        name                  TEXT NOT NULL UNIQUE COLLATE NOCASE,
        kind                  TEXT NOT NULL CHECK (kind IN ('checking','savings','credit_card','cash','other','loan')),
        opening_balance_cents INTEGER NOT NULL DEFAULT 0,
        color                 TEXT NOT NULL DEFAULT '#3949AB',
        created_at            TEXT NOT NULL DEFAULT (datetime('now')),
        number_last4          TEXT
    );
    INSERT INTO accounts_new (id, name, kind, opening_balance_cents, color, created_at, number_last4)
        SELECT id, name, kind, opening_balance_cents, color, created_at, number_last4 FROM accounts;
    DROP TABLE accounts;
    ALTER TABLE accounts_new RENAME TO accounts;
    """,
]

FOREIGN_KEYS_OFF_MIGRATIONS = {8}

DEFAULT_EXTRA_ACCOUNTS: list[tuple[str, str, str]] = [
    ("Credit Card", "credit_card", "#8E24AA"),
    ("Cash", "cash", "#43A047"),
]


def connect(path: str | Path = ":memory:") -> sqlite3.Connection:
    """Open a connection with sane defaults and an up-to-date schema."""
    conn = sqlite3.connect(str(path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    if str(path) != ":memory:":
        conn.execute("PRAGMA journal_mode = WAL")
    migrate(conn)
    return conn


def schema_version(conn: sqlite3.Connection) -> int:
    return conn.execute("PRAGMA user_version").fetchone()[0]


def migrate(conn: sqlite3.Connection) -> None:
    current = schema_version(conn)
    for version, script in enumerate(MIGRATIONS[current:], start=current + 1):
        fk_off = version in FOREIGN_KEYS_OFF_MIGRATIONS
        if fk_off:
            if conn.in_transaction:
                raise RuntimeError("Cannot migrate inside an open transaction")
            preexisting = conn.execute("PRAGMA foreign_key_check").fetchall()
            if preexisting:
                raise sqlite3.IntegrityError(
                    f"Pre-existing foreign key violations before migration {version}: {preexisting[:3]}"
                )
            conn.execute("PRAGMA foreign_keys = OFF")
        try:
            conn.executescript(f"BEGIN;\n{script}\nPRAGMA user_version = {version};\nCOMMIT;")
        except Exception:
            if conn.in_transaction:
                conn.rollback()
            raise
        else:
            if fk_off:
                violations = conn.execute("PRAGMA foreign_key_check").fetchall()
                if violations:
                    raise sqlite3.IntegrityError(
                        f"Foreign key violations after migration {version}: {violations[:3]}"
                    )
        finally:
            if fk_off:
                conn.execute("PRAGMA foreign_keys = ON")
    if current == 0:
        _seed(conn)


def _seed(conn: sqlite3.Connection) -> None:
    with conn:
        conn.executemany(
            "INSERT OR IGNORE INTO categories (name, type, color, icon) VALUES (?, ?, ?, ?)",
            DEFAULT_CATEGORIES,
        )
        conn.executemany(
            "INSERT OR IGNORE INTO accounts (name, kind, color) VALUES (?, ?, ?)",
            DEFAULT_EXTRA_ACCOUNTS,
        )
