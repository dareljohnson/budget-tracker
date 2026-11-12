"""Application services that sit between the UI and the repository."""

from __future__ import annotations

import csv
import hashlib
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

from .formatting import month_key, shift_month
from .models import (
    Account,
    AccountBalance,
    BudgetProgress,
    CategoryTotal,
    DueBill,
    MonthTotal,
    Reconciliation,
    Statement,
    Summary,
    Transaction,
    TransactionType,
)
from .repository import Repository, RepositoryError
from .statements import parse_pdf
from .statements.model import ImportPreview, ImportRow
from .workbook_export import export_workbook


def _csv_safe(value: str) -> str:
    """Neutralize spreadsheet formula injection in user-provided text."""
    return f"'{value}" if value[:1] in ("=", "+", "-", "@", "\t", "\r") else value


@dataclass(slots=True, frozen=True)
class DashboardData:
    month: str
    month_summary: Summary
    accounts: list[AccountBalance]
    expense_by_category: list[CategoryTotal]
    trend: list[MonthTotal]
    recent: list[Transaction]
    budgets: list[BudgetProgress]
    upcoming_bills: list[DueBill]

    @property
    def net_worth_cents(self) -> int:
        return sum(a.balance_cents for a in self.accounts)

    @property
    def alerts(self) -> list[BudgetProgress]:
        return [b for b in self.budgets if b.status != "ok"]


class BudgetService:
    def __init__(self, repo: Repository) -> None:
        self.repo = repo

    @staticmethod
    def current_month(today: date | None = None) -> str:
        return month_key(today or date.today())

    def dashboard(self, month: str, *, trend_months: int = 6) -> DashboardData:
        months = [shift_month(month, -i) for i in range(trend_months - 1, -1, -1)]
        return DashboardData(
            month=month,
            month_summary=self.repo.summary(month),
            accounts=self.repo.account_balances(),
            expense_by_category=self.repo.totals_by_category(month, TransactionType.EXPENSE),
            trend=self.repo.monthly_totals(months),
            recent=self.repo.list_transactions(limit=6),
            budgets=self.repo.budget_progress(month),
            upcoming_bills=self.due_bills(),
        )

    def due_bills(self, today: date | None = None) -> list[DueBill]:
        """Bills that are overdue or due within the user's reminder window."""
        return self.repo.due_bills(today or date.today(), self.repo.reminder_days())

    def export_csv(self, path: Path, transactions: list[Transaction]) -> Path:
        """Write transactions to CSV. Amounts are signed by cash flow; transfers are unsigned."""
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", newline="", encoding="utf-8") as fh:
            writer = csv.writer(fh)
            writer.writerow(["Date", "Type", "Account", "Category", "To Account", "Description", "Amount"])
            for tx in transactions:
                amount = tx.amount_cents / 100 if tx.type is TransactionType.ADJUSTMENT else tx.amount_cents / 100 * (tx.flow_sign or 1)
                writer.writerow(
                    [
                        tx.occurred_on.isoformat(),
                        tx.type.label,
                        _csv_safe(tx.account_name or ""),
                        _csv_safe(tx.category_name or ""),
                        _csv_safe(tx.transfer_account_name or ""),
                        _csv_safe(tx.description),
                        f"{amount:.2f}",
                    ]
                )
        return path

    @staticmethod
    def export_filename(now: datetime | None = None) -> str:
        return f"transactions_{(now or datetime.now()):%Y%m%d_%H%M%S}.csv"

    # ---------- manual balance checks ----------
    def check_balance(self, account_id: int, as_of: date, stated_cents: int, note: str = "") -> Reconciliation:
        """Compare the ledger balance through ``as_of`` with a stated balance and record the check."""
        computed_cents = self.repo.account_balance_as_of(account_id, as_of)
        return self.repo.add_reconciliation(
            Reconciliation(
                account_id=account_id,
                as_of=as_of,
                stated_cents=stated_cents,
                computed_cents=computed_cents,
                difference_cents=computed_cents - stated_cents,
                note=note,
            )
        )

    def adjust_balance(self, account_id: int, as_of: date, amount_cents: int, note: str = "") -> Transaction:
        """Post a signed correction that moves the account balance only."""
        return self.repo.add_transaction(
            Transaction(
                amount_cents=amount_cents,
                type=TransactionType.ADJUSTMENT,
                category_id=None,
                occurred_on=as_of,
                description=note or "Balance adjustment",
                account_id=account_id,
            )
        )

    def last_reconciliation(self, account_id: int) -> Reconciliation | None:
        """The most recent balance check for an account, or ``None`` when it has never been checked."""
        reconciliations = self.repo.list_reconciliations(account_id)
        return reconciliations[0] if reconciliations else None

    # ---------- statement import ----------
    def _default_category_id(self, type_: TransactionType) -> int:
        """The seeded catch-all category for a transaction type (refunds share the expense default)."""
        wanted = type_.category_type or TransactionType.EXPENSE
        name = "Other Income" if wanted is TransactionType.INCOME else "Other"
        for category in self.repo.list_categories(wanted):
            if category.name == name:
                return category.id
        raise RepositoryError(f"The default '{name}' category is missing")

    def prepare_import(self, pdf_path: Path, account_id: int) -> ImportPreview:
        """Parse a statement and build an editable preview without writing anything."""
        if self.repo.get_account(account_id) is None:
            raise RepositoryError("Select an account")
        parsed = parse_pdf(pdf_path)
        file_sha256 = hashlib.sha256(pdf_path.read_bytes()).hexdigest()
        if self.repo.statement_by_sha256(file_sha256):
            raise RepositoryError("This statement was already imported")

        statement = Statement(
            account_id=account_id,
            institution=parsed.institution,
            account_label=parsed.account_label,
            account_last4=parsed.account_last4,
            period_start=parsed.period_start,
            period_end=parsed.period_end,
            beginning_cents=parsed.beginning_cents,
            deposits_cents=parsed.deposits_cents,
            withdrawals_cents=parsed.withdrawals_cents,
            fees_cents=parsed.fees_cents,
            ending_cents=parsed.ending_cents,
            file_sha256=file_sha256,
        )

        rows: list[ImportRow] = []
        for item in parsed.transactions:
            type_ = TransactionType.INCOME if item.direction == "income" else TransactionType.EXPENSE
            category_id = self.repo.suggest_category_id(item.description, type_)
            if category_id is None:
                category_id = self._default_category_id(type_)
            rows.append(
                ImportRow(
                    transaction=Transaction(
                        amount_cents=item.amount_cents,
                        type=type_,
                        category_id=category_id,
                        occurred_on=item.occurred_on,
                        description=item.description,
                        account_id=account_id,
                    ),
                    duplicate=self.repo.find_transaction(
                        account_id, item.occurred_on, item.amount_cents, item.description
                    ) is not None,
                )
            )
        return ImportPreview(statement=statement, account_id=account_id, rows=rows)

    def commit_import(
        self, preview: ImportPreview, rows: list[ImportRow], *, set_opening_balance: bool = False
    ) -> Statement:
        """Persist a previewed import, its opening balance (when requested) and its reconciliation."""
        if self.repo.statement_by_sha256(preview.statement.file_sha256):
            raise RepositoryError("This statement was already imported")

        account = self.repo.get_account(preview.account_id)
        if account is None:
            raise RepositoryError("Select an account")
        statement_last4 = preview.statement.account_last4
        if statement_last4 and account.number_last4:
            if not (statement_last4.endswith(account.number_last4)
                    or account.number_last4.endswith(statement_last4)):
                raise RepositoryError(
                    f"Statement account ending {statement_last4} does not match "
                    f"account '{account.name}' ending {account.number_last4}"
                )

        for row in rows:
            if not row.include:
                continue
            tx = row.transaction
            if tx.type in (TransactionType.INCOME, TransactionType.EXPENSE, TransactionType.REFUND):
                if tx.category_id is None:
                    raise RepositoryError("Choose a category for every imported transaction")
            elif tx.type is TransactionType.TRANSFER and tx.transfer_account_id is None:
                raise RepositoryError("Choose the destination account for every imported transfer")

        # Decide before writing: the imported rows themselves must not make this look like history.
        set_opening = set_opening_balance and not self.repo.has_transactions_before(
            preview.account_id, preview.statement.period_start
        )
        reconciliation = Reconciliation(
            account_id=preview.account_id,
            as_of=preview.statement.period_end,
            stated_cents=preview.statement.ending_cents,
            computed_cents=0,
            difference_cents=0,
            note="Statement import",
        )
        return self.repo.import_statement(
            preview.statement,
            rows,
            opening_balance_cents=preview.statement.beginning_cents if set_opening else None,
            reconciliation=reconciliation,
        )

    def match_account(self, account_last4: str | None) -> Account | None:
        """The single account whose saved suffix matches the statement's suffix (either direction)."""
        if not account_last4 or not (statement_last4 := account_last4.strip()):
            return None
        matches = [
            account
            for account in self.repo.list_accounts()
            if account.number_last4
            and (account.number_last4.endswith(statement_last4) or statement_last4.endswith(account.number_last4))
        ]
        return matches[0] if len(matches) == 1 else None

    def export_xlsx(self, path: Path, today: date | None = None) -> Path:
        """Write the full workbook (summary, transactions, accounts, budgets, bills) with live formulas."""
        return export_workbook(self.repo, path, today or date.today())

    @staticmethod
    def export_xlsx_filename(now: datetime | None = None) -> str:
        return f"budget_tracker_{(now or datetime.now()):%Y%m%d_%H%M%S}.xlsx"
