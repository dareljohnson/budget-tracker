"""Application services that sit between the UI and the repository."""

from __future__ import annotations

import csv
import hashlib
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

from .debt_planner import DebtInput, DebtPlan, DebtStrategy, monthly_interest_cents, plan_payoff
from .formatting import month_key, shift_month
from .models import (
    Account,
    AccountBalance,
    AccountKind,
    BudgetProgress,
    CategoryTotal,
    Debt,
    DebtKind,
    DueBill,
    MonthTotal,
    MinimumPayment,
    MinimumPaymentStatus,
    Reconciliation,
    Statement,
    Summary,
    SavingsGoal,
    Transaction,
    TransactionType,
)
from .repository import Repository, RepositoryError
from .statements import load_statement
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


@dataclass(slots=True, frozen=True)
class YearMonth:
    month: str
    budget_cents: int
    has_budget: bool
    income_cents: int
    spending_cents: int

    @property
    def difference_cents(self) -> int:
        return self.budget_cents - self.spending_cents

    @property
    def net_cents(self) -> int:
        return self.income_cents - self.spending_cents


@dataclass(slots=True, frozen=True)
class YearOverview:
    year: int
    months: list[YearMonth]
    elapsed_months: int
    ytd_budget_cents: int
    ytd_spending_cents: int
    ytd_income_cents: int
    budgeted_months: int
    forecast_spending_cents: int | None
    prior_spending_cents: int | None
    prior_income_cents: int | None

    @property
    def ytd_difference_cents(self) -> int:
        return self.ytd_budget_cents - self.ytd_spending_cents

    @property
    def has_data(self) -> bool:
        return any(month.income_cents or month.spending_cents or month.has_budget for month in self.months)


@dataclass(slots=True, frozen=True)
class GoalScheduleMonth:
    """One month of a goal's original fixed plan."""

    month: str  # YYYY-MM
    required_cents: int
    cumulative_cents: int


@dataclass(slots=True, frozen=True)
class SavingsGoalProgress:
    goal: SavingsGoal
    account_name: str
    contributed_cents: int
    progress_cents: int
    remaining_cents: int
    required_monthly_cents: int
    months_left: int
    contributed_this_month_cents: int
    state: str  # active | reached | overdue
    plan: list[GoalScheduleMonth]


@dataclass(slots=True, frozen=True)
class DebtSummary:
    debt: Debt
    balance_cents: int
    effective_minimum_cents: int
    minimum_from_statement: bool
    statement_month: str | None
    monthly_interest_cents: int
    minimum_below_interest: bool
    account_name: str | None


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

    def available_years(self) -> list[int]:
        return self.repo.data_years()

    def year_overview(self, year: int, today: date | None = None) -> YearOverview:
        """Monthly budget-vs-spending and cash flow for one year.

        The YTD figures cover January through the chosen month for the current year, all twelve
        months for a past year, and nothing for a future year. The year-end projection is a simple
        run-rate: YTD spending divided by the months elapsed, times twelve.
        """
        today = today or date.today()
        if year < today.year:
            elapsed = 12
        elif year == today.year:
            elapsed = today.month
        else:
            elapsed = 0
        months = [f"{year:04d}-{month:02d}" for month in range(1, 13)]
        totals = {total.month: total for total in self.repo.monthly_totals(months)}
        budgets = self.repo.budget_totals(months)
        year_months: list[YearMonth] = []
        for month in months:
            total = totals.get(month)
            limit, count = budgets.get(month, (0, 0))
            year_months.append(
                YearMonth(
                    month=month,
                    budget_cents=limit,
                    has_budget=count > 0,
                    income_cents=total.income_cents if total else 0,
                    spending_cents=total.expense_cents if total else 0,
                )
            )
        elapsed_months = year_months[:elapsed]
        ytd_budget = sum(month.budget_cents for month in elapsed_months)
        ytd_spending = sum(month.spending_cents for month in elapsed_months)
        ytd_income = sum(month.income_cents for month in elapsed_months)
        budgeted_months = sum(1 for month in elapsed_months if month.has_budget)
        forecast = None
        if elapsed and year == today.year and (ytd_budget or ytd_spending or ytd_income):
            forecast = round(ytd_spending / elapsed * 12)
        prior_spending = prior_income = None
        if elapsed:
            prior_months = [f"{year - 1:04d}-{month:02d}" for month in range(1, elapsed + 1)]
            prior = self.repo.monthly_totals(prior_months)
            if any(month.income_cents or month.expense_cents for month in prior):
                prior_spending = sum(month.expense_cents for month in prior)
                prior_income = sum(month.income_cents for month in prior)
        return YearOverview(
            year=year,
            months=year_months,
            elapsed_months=elapsed,
            ytd_budget_cents=ytd_budget,
            ytd_spending_cents=ytd_spending,
            ytd_income_cents=ytd_income,
            budgeted_months=budgeted_months,
            forecast_spending_cents=forecast,
            prior_spending_cents=prior_spending,
            prior_income_cents=prior_income,
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

    # ---------- credit card minimum payments ----------
    def record_minimum_payment(self, account_id: int, month: str, amount_cents: int) -> MinimumPayment:
        """Record or update the minimum printed on one credit card statement."""
        account = self.repo.get_account(account_id)
        if account is None:
            raise RepositoryError("Account not found")
        if account.kind is not AccountKind.CREDIT_CARD:
            raise RepositoryError("Minimum payments apply to credit cards only")
        try:
            datetime.strptime(month, "%Y-%m")
        except ValueError as exc:
            raise RepositoryError("Choose a valid month") from exc
        if amount_cents <= 0:
            raise RepositoryError("Minimum payment must be greater than zero")
        return self.repo.set_minimum_payment(account_id, month, amount_cents)

    def delete_minimum_payment(self, account_id: int, month: str) -> None:
        self.repo.delete_minimum_payment(account_id, month)

    def minimum_payment_statuses(self, account_id: int) -> list[MinimumPaymentStatus]:
        """Every recorded month, newest first, with payments applied oldest-unpaid first.

        A payment in the month after the statement still counts toward that statement's minimum,
        a payment above a minimum rolls into the next recorded month, and no payment is counted
        twice. Only transfers into the card count; cash advances (transfers out) do not.
        """
        minimums = self.repo.list_minimum_payments(account_id)
        if not minimums:
            return []
        first_month = minimums[0].month
        payments = [
            (day, cents)
            for day, cents in self.repo.card_payments(account_id)
            if day.strftime("%Y-%m") >= first_month
        ]
        paid = {minimum.month: 0 for minimum in minimums}
        index = 0
        carried = 0
        for _day, cents in payments:
            available = cents + carried
            while index < len(minimums) and available:
                minimum = minimums[index]
                outstanding = minimum.amount_cents - paid[minimum.month]
                applied = min(available, outstanding)
                paid[minimum.month] += applied
                available -= applied
                if paid[minimum.month] >= minimum.amount_cents:
                    index += 1
                else:
                    break
            carried = available
        return [
            MinimumPaymentStatus(
                month=minimum.month,
                amount_cents=minimum.amount_cents,
                paid_cents=paid[minimum.month],
            )
            for minimum in reversed(minimums)
        ]

    def current_minimum_payment(self, account_id: int) -> MinimumPaymentStatus | None:
        """The current month's minimum when recorded, otherwise the most recent one."""
        statuses = self.minimum_payment_statuses(account_id)
        if not statuses:
            return None
        current = self.current_month()
        return next((status for status in statuses if status.month == current), statuses[0])

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

    def prepare_import(self, statement_path: Path, account_id: int) -> ImportPreview:
        """Parse a statement and build an editable preview without writing anything."""
        if self.repo.get_account(account_id) is None:
            raise RepositoryError("Select an account")
        parsed = load_statement(statement_path)
        file_sha256 = hashlib.sha256(statement_path.read_bytes()).hexdigest()
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
                    direction=item.direction,
                )
            )
        return ImportPreview(statement=statement, account_id=account_id, rows=rows, source=parsed.source)

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

    # ---------- debts ----------
    def _validate_debt_record(
        self,
        name: str,
        kind: DebtKind,
        balance_cents: int,
        apr_bps: int,
        minimum_cents: int,
        original_balance_cents: int | None,
        account_id: int | None,
        *,
        debt_id: int | None = None,
    ) -> None:
        if not (name or "").strip():
            raise RepositoryError("Debt name is required")
        if not isinstance(kind, DebtKind):
            raise RepositoryError("Choose a debt kind")
        if balance_cents < 0:
            raise RepositoryError("Balance cannot be negative")
        if not (0 <= apr_bps <= 10_000):
            raise RepositoryError("APR must be between 0 and 100")
        if minimum_cents < 0:
            raise RepositoryError("Minimum payment cannot be negative")
        if original_balance_cents is not None and original_balance_cents < 0:
            raise RepositoryError("Original balance cannot be negative")
        if account_id is not None:
            account = self.repo.get_account(account_id)
            if account is None or not account.kind.is_liability:
                raise RepositoryError("Link a debt only to a credit-card or loan account")
            if account.kind is AccountKind.CREDIT_CARD and kind is not DebtKind.CREDIT_CARD:
                raise RepositoryError("A linked card debt must be a credit card")
            if account.kind is AccountKind.LOAN and kind is DebtKind.CREDIT_CARD:
                raise RepositoryError("Choose a loan kind for a loan account")
            linked = [d for d in self.repo.list_debts() if d.account_id == account_id and d.id != debt_id]
            if linked:
                raise RepositoryError("This account is already linked to a debt")

    def _card_owed(self, account_id: int) -> int:
        for balance in self.repo.account_balances():
            if balance.account.id == account_id:
                return balance.owed_cents
        return 0

    def debt_summary(self, debt_id: int) -> DebtSummary:
        debt = self.repo.get_debt(debt_id)
        if debt is None:
            raise RepositoryError("Debt not found")
        balance = self._card_owed(debt.account_id) if debt.account_id is not None else debt.balance_cents
        account_name = None
        minimum = debt.minimum_cents
        from_statement = False
        statement_month = None
        if debt.account_id is not None:
            account = self.repo.get_account(debt.account_id)
            account_name = account.name if account else None
            if account is not None and account.kind is AccountKind.CREDIT_CARD:
                status = self.current_minimum_payment(debt.account_id)
                if status is not None:
                    minimum, from_statement, statement_month = status.amount_cents, True, status.month
        interest = monthly_interest_cents(balance, debt.apr_bps)
        return DebtSummary(
            debt=debt,
            balance_cents=balance,
            effective_minimum_cents=minimum,
            minimum_from_statement=from_statement,
            statement_month=statement_month,
            monthly_interest_cents=interest,
            minimum_below_interest=balance > 0 and minimum < interest,
            account_name=account_name,
        )

    def list_debt_summaries(self) -> list[DebtSummary]:
        return [self.debt_summary(debt.id) for debt in self.repo.list_debts()]

    def create_debt(
        self,
        name: str,
        kind: DebtKind,
        balance_cents: int,
        apr_bps: int,
        minimum_cents: int = 0,
        *,
        original_balance_cents: int | None = None,
        account_id: int | None = None,
    ) -> Debt:
        balance = self._card_owed(account_id) if account_id is not None else balance_cents
        self._validate_debt_record(name, kind, balance, apr_bps, minimum_cents,
                                   original_balance_cents, account_id)
        return self.repo.add_debt(Debt(
            name=name.strip(), kind=kind, balance_cents=balance, apr_bps=apr_bps,
            minimum_cents=minimum_cents, original_balance_cents=original_balance_cents,
            account_id=account_id,
        ))

    def update_debt(
        self,
        debt_id: int,
        *,
        name: str,
        kind: DebtKind,
        balance_cents: int,
        apr_bps: int,
        minimum_cents: int,
        original_balance_cents: int | None,
        account_id: int | None,
    ) -> None:
        existing = self.repo.get_debt(debt_id)
        if existing is None:
            raise RepositoryError("Debt not found")
        if account_id is not None:
            balance = self._card_owed(account_id)
        elif existing.account_id is not None:
            balance = self._card_owed(existing.account_id)
        else:
            balance = balance_cents
        self._validate_debt_record(name, kind, balance, apr_bps, minimum_cents,
                                   original_balance_cents, account_id, debt_id=debt_id)
        if existing.account_id is None and account_id is not None:
            # Pre-link payments are superseded by the card's live balance snapshot.
            self.repo.clear_debt_payments(debt_id)
        self.repo.update_debt(Debt(
            id=debt_id, name=name.strip(), kind=kind, balance_cents=balance, apr_bps=apr_bps,
            minimum_cents=minimum_cents, original_balance_cents=original_balance_cents,
            account_id=account_id,
        ))

    def delete_debt(self, debt_id: int) -> None:
        self.repo.delete_debt(debt_id)

    def _debt_payment_category(self, category_id: int | None) -> int:
        if category_id is not None:
            category = self.repo.get_category(category_id)
            if category is None or category.type is not TransactionType.EXPENSE:
                raise RepositoryError("Choose an expense category for the payment")
            return category.id
        preferred = next(
            (c for c in self.repo.list_categories() if c.name == "Debt payments" and c.type is TransactionType.EXPENSE),
            None,
        )
        if preferred is None:
            preferred = next((c for c in self.repo.list_categories() if c.type is TransactionType.EXPENSE), None)
        if preferred is None:
            raise RepositoryError("Add an expense category before recording a payment")
        return preferred.id

    def record_debt_payment(
        self,
        debt_id: int,
        funding_account_id: int,
        amount_cents: int,
        on_date: date,
        category_id: int | None = None,
        note: str = "",
        *,
        today: date | None = None,
    ) -> Transaction:
        today = today or date.today()
        debt = self.repo.get_debt(debt_id)
        if debt is None:
            raise RepositoryError("Debt not found")
        funding = self.repo.get_account(funding_account_id)
        if funding is None:
            raise RepositoryError("Select a funding account")
        if funding.kind.is_liability:
            raise RepositoryError("Pay from a checking, savings or cash account")
        if amount_cents <= 0:
            raise RepositoryError("Amount must be greater than zero")
        if on_date > today:
            raise RepositoryError("Payment date cannot be in the future")
        if debt.account_id is not None and funding.id == debt.account_id:
            raise RepositoryError("Choose a different account to pay from")
        description = note.strip() or f"Payment: {debt.name}"
        if debt.account_id is None:
            if amount_cents > debt.balance_cents:
                raise RepositoryError("Payment is more than the remaining balance")
            return self._describe_transaction(self.repo.add_transaction(Transaction(
                amount_cents, TransactionType.EXPENSE, self._debt_payment_category(category_id),
                on_date, description, account_id=funding.id, debt_id=debt.id,
            )))
        return self._describe_transaction(self.repo.add_transaction(Transaction(
            amount_cents, TransactionType.TRANSFER, None, on_date, description,
            account_id=funding.id, transfer_account_id=debt.account_id, debt_id=debt.id,
        )))

    def _describe_transaction(self, tx: Transaction) -> Transaction:
        """Fill the display names a freshly inserted transaction does not carry yet."""
        if tx.category_id is not None:
            category = self.repo.get_category(tx.category_id)
            tx.category_name = category.name if category else None
        if tx.account_id is not None:
            account = self.repo.get_account(tx.account_id)
            tx.account_name = account.name if account else None
        if tx.transfer_account_id is not None:
            account = self.repo.get_account(tx.transfer_account_id)
            tx.transfer_account_name = account.name if account else None
        return tx

    def debt_payments(self, debt_id: int) -> list[Transaction]:
        return self.repo.list_debt_payments(debt_id)

    def debt_plan(
        self,
        extra_cents: int,
        strategy: DebtStrategy,
        *,
        today: date | None = None,
    ) -> DebtPlan:
        if extra_cents < 0:
            raise RepositoryError("Extra payment cannot be negative")
        inputs = [
            DebtInput(summary.debt, summary.balance_cents, summary.effective_minimum_cents)
            for summary in self.list_debt_summaries()
            if summary.balance_cents > 0
        ]
        return plan_payoff(
            inputs, extra_cents=extra_cents, strategy=strategy, start_month=self.current_month(today)
        )

    def debt_plans(self, extra_cents: int, *, today: date | None = None) -> dict[DebtStrategy, DebtPlan]:
        return {strategy: self.debt_plan(extra_cents, strategy, today=today) for strategy in DebtStrategy}

    def debt_extra_comparison(
        self, strategy: DebtStrategy, amounts: list[int], *, today: date | None = None
    ) -> list[DebtPlan]:
        return [self.debt_plan(amount, strategy, today=today) for amount in amounts]

    # ---------- savings goals ----------
    def _validate_goal(
        self,
        name: str,
        target_cents: int,
        target_date: date,
        account_id: int,
        initial_cents: int,
    ) -> None:
        """Reject every value the schema would reject, so callers never see a raw sqlite error."""
        if not (name or "").strip():
            raise RepositoryError("Goal name is required")
        if target_cents <= 0:
            raise RepositoryError("Target amount must be greater than zero")
        if initial_cents < 0:
            raise RepositoryError("Amount already saved cannot be negative")
        if initial_cents > target_cents:
            raise RepositoryError("Amount already saved cannot exceed the target")
        if isinstance(target_date, datetime) or not isinstance(target_date, date):
            raise RepositoryError("Choose a valid target date")
        account = self.repo.get_account(account_id)
        if account is None:
            raise RepositoryError("Select a destination account")
        if account.kind is not AccountKind.SAVINGS:
            raise RepositoryError("Choose a savings account as the destination")

    @staticmethod
    def _goal_plan(goal: SavingsGoal) -> tuple[int, list[GoalScheduleMonth]]:
        """The fixed monthly amount and the month-by-month plan, both computed from the goal's terms.

        The plan does not change when contributions are recorded: it is the original schedule.
        """
        total = goal.target_cents - goal.initial_cents
        if total <= 0:
            return 0, []
        start_month = month_key(goal.start_date)
        target_month = month_key(goal.target_date)
        start_year, start_index = map(int, start_month.split("-"))
        target_year, target_index = map(int, target_month.split("-"))
        plan_months = max(1, (target_year - start_year) * 12 + (target_index - start_index) + 1)
        monthly = max(1, (total + plan_months - 1) // plan_months)
        plan: list[GoalScheduleMonth] = []
        remaining = total
        cumulative = goal.initial_cents
        month = start_month
        while remaining > 0:
            required = min(monthly, remaining)
            remaining -= required
            cumulative += required
            plan.append(GoalScheduleMonth(month, required, cumulative))
            month = shift_month(month, 1)
        return monthly, plan

    def list_goal_progress(self, today: date | None = None) -> list[SavingsGoalProgress]:
        return [self.goal_progress(goal.id, today) for goal in self.repo.list_savings_goals()]

    def goal_progress(self, goal_id: int, today: date | None = None) -> SavingsGoalProgress:
        today = today or date.today()
        goal = self.repo.get_savings_goal(goal_id)
        if goal is None:
            raise RepositoryError("Goal not found")
        contributed = self.repo.goal_contributed_cents(goal_id, through=today)
        progress = goal.initial_cents + contributed
        remaining = max(0, goal.target_cents - progress)
        account = self.repo.get_account(goal.account_id)
        monthly, plan = self._goal_plan(goal)
        if progress >= goal.target_cents:
            state, required, plan, months_left = "reached", 0, [], 0
        elif goal.target_date < today:
            state, required, plan, months_left = "overdue", remaining, [], 0
        else:
            state, required = "active", monthly
            months_left = (goal.target_date.year - today.year) * 12 + (goal.target_date.month - today.month) + 1
        return SavingsGoalProgress(
            goal=goal,
            account_name=account.name if account else "",
            contributed_cents=contributed,
            progress_cents=progress,
            remaining_cents=remaining,
            required_monthly_cents=required,
            months_left=months_left,
            contributed_this_month_cents=self.repo.goal_contributed_in_month(
                goal_id, month_key(today), through=today
            ),
            state=state,
            plan=plan,
        )

    def goal_schedule(self, goal_id: int, today: date | None = None) -> list[GoalScheduleMonth]:
        return self.goal_progress(goal_id, today).plan

    def create_savings_goal(
        self,
        name: str,
        target_cents: int,
        target_date: date,
        account_id: int,
        initial_cents: int = 0,
    ) -> SavingsGoal:
        self._validate_goal(name, target_cents, target_date, account_id, initial_cents)
        return self.repo.add_savings_goal(
            SavingsGoal(
                name=name.strip(),
                target_cents=target_cents,
                target_date=target_date,
                start_date=date.today(),
                account_id=account_id,
                initial_cents=initial_cents,
            )
        )

    def update_savings_goal(
        self,
        goal_id: int,
        *,
        name: str,
        target_cents: int,
        target_date: date,
        account_id: int,
        initial_cents: int,
    ) -> SavingsGoal:
        goal = self.repo.get_savings_goal(goal_id)
        if goal is None:
            raise RepositoryError("Goal not found")
        self._validate_goal(name, target_cents, target_date, account_id, initial_cents)
        if account_id != goal.account_id and self.repo.goal_contribution_count(goal_id) > 0:
            raise RepositoryError(
                "This goal already has contributions; remove them or create a new goal for that account"
            )
        goal.name = name.strip()
        goal.target_cents = target_cents
        goal.target_date = target_date
        goal.account_id = account_id
        goal.initial_cents = initial_cents
        self.repo.update_savings_goal(goal)
        return goal

    def delete_savings_goal(self, goal_id: int) -> None:
        self.repo.delete_savings_goal(goal_id)

    def contribute_to_goal(
        self,
        goal_id: int,
        funding_account_id: int,
        amount_cents: int,
        on_date: date,
        *,
        today: date | None = None,
    ) -> Transaction:
        today = today or date.today()
        goal = self.repo.get_savings_goal(goal_id)
        if goal is None:
            raise RepositoryError("Goal not found")
        funding = self.repo.get_account(funding_account_id)
        if funding is None:
            raise RepositoryError("Select a funding account")
        if funding.kind.is_liability:
            raise RepositoryError("Choose a non-credit account to fund this goal")
        if funding.id == goal.account_id:
            raise RepositoryError("Choose a different account to fund this goal")
        if amount_cents <= 0:
            raise RepositoryError("Amount must be greater than zero")
        if isinstance(on_date, datetime) or not isinstance(on_date, date):
            raise RepositoryError("Choose a valid contribution date")
        if on_date > today:
            raise RepositoryError("Contribution date cannot be in the future")
        return self.repo.add_transaction(
            Transaction(
                amount_cents=amount_cents,
                type=TransactionType.TRANSFER,
                category_id=None,
                occurred_on=on_date,
                description=f"Savings: {goal.name}",
                account_id=funding.id,
                transfer_account_id=goal.account_id,
                goal_id=goal.id,
            )
        )
