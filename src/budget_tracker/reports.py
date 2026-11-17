"""Report assembly for the monthly, year, and net-worth reports.

A report is a read-only snapshot built from the same repository and service calls as the
pages it summarizes. Nothing here renders or writes: the PDF layer consumes these objects,
and the Reports page previews the generated PDF.
"""

from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import date
from enum import StrEnum
from typing import TYPE_CHECKING

from .debt_planner import DebtInput, DebtPlan, DebtStrategy, plan_payoff
from .formatting import month_key, month_label, shift_month
from .models import (
    AccountBalance,
    BudgetProgress,
    CategoryTotal,
    MonthTotal,
    Summary,
    Transaction,
    TransactionType,
)

if TYPE_CHECKING:
    from .services import BudgetService, SavingsGoalProgress, YearMonth, YearOverview

# The extra monthly amount used for the payoff comparison on a net-worth statement.
NET_WORTH_EXTRA_CENTS = 20_000
RANKED_CATEGORY_LIMIT = 8


class ReportKind(StrEnum):
    MONTHLY = "monthly"
    YEAR = "year"
    NET_WORTH = "net_worth"

    @property
    def label(self) -> str:
        return {
            ReportKind.MONTHLY: "Monthly summary",
            ReportKind.YEAR: "Year in review",
            ReportKind.NET_WORTH: "Net worth statement",
        }[self]

    @property
    def blurb(self) -> str:
        return {
            ReportKind.MONTHLY: "One page for the month, with budgets and top spending",
            ReportKind.YEAR: "Twelve-month totals, pacing, and last-year comparison",
            ReportKind.NET_WORTH: "A balance sheet: assets, debts, goals, and trend",
        }[self]


@dataclass(slots=True, frozen=True)
class CategorySlice:
    """One ranked category row, with its share of positive spending."""

    name: str
    amount_cents: int
    share: float
    color: str = "#757575"
    muted: bool = False


@dataclass(slots=True, frozen=True)
class BudgetRow:
    name: str
    limit_cents: int
    spent_cents: int
    ratio: float
    status: str  # over | at_limit | near | ok
    remaining_cents: int


@dataclass(slots=True, frozen=True)
class DebtRow:
    name: str
    kind_label: str
    balance_cents: int
    apr_bps: int
    minimum_cents: int
    interest_cents: int
    payoff_month: str | None
    minimum_below_interest: bool
    account_name: str | None = None


@dataclass(slots=True, frozen=True)
class NetWorthPoint:
    month: str  # YYYY-MM
    label: str
    net_worth_cents: int


@dataclass(slots=True, frozen=True)
class MonthlyReport:
    month: str
    generated_on: date
    summary: Summary
    budgets: list[BudgetRow]
    categories: list[CategorySlice]
    trend: list[MonthTotal]
    largest: list[Transaction]
    accounts: list[AccountBalance]
    unbudgeted_spending_cents: int = 0

    @property
    def title(self) -> str:
        return "Monthly Summary"

    @property
    def period_label(self) -> str:
        return month_label(self.month)

    @property
    def period_detail(self) -> str:
        year, month = map(int, self.month.split("-"))
        last = calendar.monthrange(year, month)[1]
        return f"{calendar.month_name[month]} 1 - {calendar.month_name[month]} {last}, {year}"

    @property
    def balances_as_of(self) -> date:
        """Month end for a past month; the generation date for the current or a future month."""
        return min(self.generated_on, _month_end(self.month))

    @property
    def filename(self) -> str:
        return f"budget_tracker_monthly_{self.month}.pdf"

    @property
    def income_cents(self) -> int:
        return self.summary.income_cents

    @property
    def spending_cents(self) -> int:
        return self.summary.expense_cents

    @property
    def net_cents(self) -> int:
        return self.summary.net_cents

    @property
    def savings_rate(self) -> float:
        return self.summary.savings_rate

    @property
    def budget_cents(self) -> int:
        return sum(row.limit_cents for row in self.budgets)

    @property
    def budget_spent_cents(self) -> int:
        return sum(row.spent_cents for row in self.budgets)

    @property
    def budget_difference_cents(self) -> int:
        return self.budget_cents - self.budget_spent_cents

    @property
    def budget_ratio(self) -> float:
        return self.budget_spent_cents / self.budget_cents if self.budget_cents else 0.0

    @property
    def has_unbudgeted_spending(self) -> bool:
        """Whether some of this month's spending is outside every category with a budget."""
        return self.unbudgeted_spending_cents > 0

    @property
    def net_worth_cents(self) -> int:
        return sum(balance.balance_cents for balance in self.accounts)


@dataclass(slots=True, frozen=True)
class YearReport:
    year: int
    generated_on: date
    overview: YearOverview
    categories: list[CategorySlice]
    cumulative_budget: list[int]
    cumulative_spending: list[int]
    actual_months: int
    prior_income_cents: int | None
    prior_spending_cents: int | None

    @property
    def title(self) -> str:
        return "Year in Review"

    @property
    def in_progress_month(self) -> str | None:
        """The current month when this report was generated before that month ended.

        ``elapsed_months`` counts the current month even on its first day, so a December report
        generated on the 15th used to look like a completed year. This keeps the partial month
        distinct from a finished one.
        """
        if self.generated_on.year != self.year:
            return None
        last_day = calendar.monthrange(self.year, self.generated_on.month)[1]
        if self.generated_on.day >= last_day:
            return None
        return f"{self.year:04d}-{self.generated_on.month:02d}"

    @property
    def period_label(self) -> str:
        if self.in_progress_month is not None:
            return f"{self.year} to date"
        if self.actual_months <= 0 or self.actual_months >= 12:
            return str(self.year)
        last_month = self.overview.months[self.actual_months - 1].month
        return f"January - {month_label(last_month)}"

    @property
    def period_detail(self) -> str:
        if self.in_progress_month is not None:
            return f"January 1 - {_long_date(self.generated_on)}"
        if self.actual_months >= 12:
            return f"January 1 - December 31, {self.year}"
        if self.actual_months <= 0:
            return f"January 1 - December 31, {self.year}"
        last_month = self.overview.months[self.actual_months - 1].month
        if self.actual_months == self.generated_on.month and self.generated_on.year == self.year:
            end = self.generated_on
        else:
            end = _month_end(last_month)
        return f"January 1 - {_long_date(end)}"

    @property
    def filename(self) -> str:
        return f"budget_tracker_year_{self.year}.pdf"

    @property
    def income_cents(self) -> int:
        return self.overview.ytd_income_cents

    @property
    def spending_cents(self) -> int:
        return self.overview.ytd_spending_cents

    @property
    def net_cents(self) -> int:
        return self.income_cents - self.spending_cents

    @property
    def savings_rate(self) -> float:
        return self.net_cents / self.income_cents if self.income_cents else 0.0

    @property
    def budget_cents(self) -> int:
        return self.overview.ytd_budget_cents

    @property
    def budget_spending_cents(self) -> int:
        """Spending in the elapsed months, the figure the YTD budget is compared with."""
        return self.overview.ytd_spending_cents

    @property
    def budget_difference_cents(self) -> int:
        return self.budget_cents - self.budget_spending_cents

    @property
    def full_year_budget_cents(self) -> int:
        return sum(month.budget_cents for month in self.overview.months)

    @property
    def projected_year_end_cents(self) -> int | None:
        return self.overview.forecast_spending_cents

    @property
    def projected_difference_cents(self) -> int | None:
        forecast = self.projected_year_end_cents
        return None if forecast is None else self.full_year_budget_cents - forecast

    @property
    def has_projection(self) -> bool:
        return 0 < self.actual_months < 12 and self.projected_year_end_cents is not None

    @property
    def prior_savings_rate(self) -> float | None:
        if self.prior_income_cents in (None, 0) or self.prior_spending_cents is None:
            return None
        return (self.prior_income_cents - self.prior_spending_cents) / self.prior_income_cents

    @property
    def income_change(self) -> float | None:
        if self.prior_income_cents in (None, 0):
            return None
        return (self.income_cents - self.prior_income_cents) / self.prior_income_cents

    @property
    def spending_change(self) -> float | None:
        if self.prior_spending_cents in (None, 0):
            return None
        return (self.spending_cents - self.prior_spending_cents) / self.prior_spending_cents

    @property
    def savings_rate_change(self) -> float | None:
        prior = self.prior_savings_rate
        return None if prior is None else self.savings_rate - prior


@dataclass(slots=True, frozen=True)
class NetWorthReport:
    as_of: date
    generated_on: date
    accounts: list[AccountBalance]
    trend: list[NetWorthPoint]
    year_start_net_worth_cents: int
    debts: list[DebtRow]
    minimum_plan: DebtPlan | None
    extra_plan: DebtPlan | None
    extra_cents: int
    goals: list[SavingsGoalProgress]

    @property
    def title(self) -> str:
        return "Net Worth Statement"

    @property
    def period_label(self) -> str:
        return f"As of {_long_date(self.as_of)}"

    @property
    def period_detail(self) -> str:
        return f"As of {_long_date(self.as_of)}"

    @property
    def filename(self) -> str:
        return f"budget_tracker_net_worth_{self.as_of.isoformat()}.pdf"

    @property
    def assets(self) -> list[AccountBalance]:
        return sorted(
            (balance for balance in self.accounts if not balance.account.kind.is_liability),
            key=lambda balance: (
                not bool(balance.account.liquid),
                -balance.balance_cents,
                balance.account.name.casefold(),
            ),
        )

    @property
    def liquid_assets(self) -> list[AccountBalance]:
        return [balance for balance in self.assets if balance.account.liquid]

    @property
    def other_assets(self) -> list[AccountBalance]:
        return [balance for balance in self.assets if not balance.account.liquid]

    @property
    def liquid_assets_cents(self) -> int:
        return sum(balance.balance_cents for balance in self.liquid_assets)

    @property
    def other_assets_cents(self) -> int:
        return sum(balance.balance_cents for balance in self.other_assets)

    @property
    def liabilities(self) -> list[AccountBalance]:
        rows = [balance for balance in self.accounts if balance.account.kind.is_liability]
        return sorted(rows, key=lambda balance: (balance.balance_cents, balance.account.name.casefold()))

    @property
    def assets_cents(self) -> int:
        return sum(balance.balance_cents for balance in self.assets)

    @property
    def liabilities_cents(self) -> int:
        """Signed amount owed: positive for a balance, negative when a card is in credit."""
        return -sum(balance.balance_cents for balance in self.liabilities)

    @property
    def net_worth_cents(self) -> int:
        return self.assets_cents - self.liabilities_cents

    @property
    def change_month_cents(self) -> int | None:
        if len(self.trend) < 2:
            return None
        return self.trend[-1].net_worth_cents - self.trend[-2].net_worth_cents

    @property
    def change_ytd_cents(self) -> int:
        return self.net_worth_cents - self.year_start_net_worth_cents

    @property
    def debt_balance_cents(self) -> int:
        return sum(row.balance_cents for row in self.debts)

    @property
    def interest_saved_cents(self) -> int | None:
        if (
            self.minimum_plan is None
            or self.extra_plan is None
            or not self.minimum_plan.finished
            or not self.extra_plan.finished
        ):
            return None
        return self.minimum_plan.total_interest_cents - self.extra_plan.total_interest_cents


def budget_status(row: BudgetProgress) -> str:
    """The report label for one budget, keeping a fully used budget separate from an exceeded one."""
    if row.ratio > 1:
        return "over"
    if row.limit_cents and row.spent_cents >= row.limit_cents:
        return "at_limit"
    if row.ratio >= 0.8:
        return "near"
    return "ok"


def budget_status_label(status: str) -> str:
    return {"over": "Over", "at_limit": "At limit", "near": "Near", "ok": "On track"}[status]


def month_status(month: YearMonth, *, in_progress: bool = False) -> str:
    """A month's budget status. A partial current month is never called under or over."""
    if in_progress:
        return "In progress"
    if not month.has_budget:
        return "No budget"
    if month.spending_cents > month.budget_cents:
        return "Over"
    if month.spending_cents == month.budget_cents:
        return "On budget"
    return "Under"


def _long_date(value: date) -> str:
    return f"{calendar.month_name[value.month]} {value.day}, {value.year}"


def _month_end(month: str) -> date:
    year, mon = map(int, month.split("-"))
    return date(year, mon, calendar.monthrange(year, mon)[1])


def _category_slices(
    totals: list[CategoryTotal],
    *,
    total_cents: int,
    limit: int = RANKED_CATEGORY_LIMIT,
) -> list[CategorySlice]:
    """Rank category totals and fold the tail into one labelled row.

    Negative totals stay in the table: a category whose refunds exceed its spending is part of
    the period's net spending. Shares use that same net total, so the table reconciles with the
    Spending KPI instead of omitting a refund-heavy category.
    """
    nonzero = sorted(
        (total for total in totals if total.total_cents != 0),
        key=lambda total: (-total.total_cents, total.name.casefold()),
    )
    if not nonzero:
        return []
    denominator = total_cents if total_cents else 0
    if len(nonzero) <= limit + 1:
        selected = nonzero
        tail: list[CategoryTotal] = []
    else:
        selected = nonzero[:limit]
        tail = nonzero[limit:]
    slices = [
        CategorySlice(
            name=total.name,
            amount_cents=total.total_cents,
            share=total.total_cents / denominator if denominator else 0.0,
            color=total.color,
        )
        for total in selected
    ]
    if tail:
        tail_total = sum(total.total_cents for total in tail)
        slices.append(
            CategorySlice(
                name=f"Other ({len(tail)} categories)",
                amount_cents=tail_total,
                share=tail_total / denominator if denominator else 0.0,
                color="#9AA0AD",
                muted=True,
            )
        )
    return slices


class ReportService:
    """Build the three report snapshots from one BudgetService."""

    def __init__(self, service: BudgetService) -> None:
        self.service = service
        self.repo = service.repo

    def available_months(self, today: date | None = None) -> list[str]:
        months = set(self.repo.transaction_months()) | set(self.repo.budget_months())
        months.add(month_key(today or date.today()))
        return sorted(months, reverse=True)

    def available_years(self) -> list[int]:
        return self.service.available_years()

    def build(
        self,
        kind: ReportKind,
        *,
        month: str | None = None,
        year: int | None = None,
        today: date | None = None,
    ) -> MonthlyReport | YearReport | NetWorthReport:
        today = today or date.today()
        if kind is ReportKind.MONTHLY:
            return self.monthly(month or month_key(today), today=today)
        if kind is ReportKind.YEAR:
            return self.year(year or today.year, today=today)
        return self.net_worth(today=today)

    def monthly(self, month: str, *, today: date | None = None) -> MonthlyReport:
        today = today or date.today()
        cutoff = min(today, _month_end(month))
        trend_months = [shift_month(month, offset) for offset in range(-5, 1)]
        summary = self.repo.summary(month, through=cutoff)
        # A monthly report is a snapshot of its period. Month-end for a past month; today for
        # the current month. account_balances() would count anything dated later.
        accounts = [
            AccountBalance(account, self.repo.account_balance_as_of(account.id, cutoff))
            for account in self.repo.list_accounts()
        ]
        progress = self.repo.budget_progress(month, through=cutoff)
        budgets = [
            BudgetRow(
                name=budget.category_name,
                limit_cents=budget.limit_cents,
                spent_cents=budget.spent_cents,
                ratio=budget.ratio,
                status=budget_status(budget),
                remaining_cents=budget.remaining_cents,
            )
            for budget in progress
        ]
        budgets.sort(key=lambda row: (-row.ratio, row.name.casefold()))
        categories = self.repo.totals_by_category(month, TransactionType.EXPENSE, through=cutoff)
        budgeted_ids = {budget.category_id for budget in progress}
        unbudgeted = sum(
            total.total_cents
            for total in categories
            if total.category_id not in budgeted_ids and total.total_cents > 0
        )
        largest = sorted(
            (
                transaction
                for transaction in self.repo.list_transactions(month=month, through=cutoff)
                if transaction.type is TransactionType.EXPENSE
            ),
            key=lambda transaction: (
                transaction.amount_cents,
                transaction.occurred_on,
                transaction.id or 0,
            ),
            reverse=True,
        )[:5]
        return MonthlyReport(
            month=month,
            generated_on=today,
            summary=summary,
            budgets=budgets,
            categories=_category_slices(categories, total_cents=summary.expense_cents),
            trend=self.repo.monthly_totals(trend_months, through=cutoff),
            largest=largest,
            accounts=accounts,
            unbudgeted_spending_cents=unbudgeted,
        )

    def year(self, year: int, *, today: date | None = None) -> YearReport:
        today = today or date.today()
        overview = self.service.year_overview(year, today=today, through=today)
        actual_months = overview.elapsed_months
        totals: dict[int, CategoryTotal] = {}
        for month in overview.months[:actual_months]:
            for total in self.repo.totals_by_category(
                month.month,
                TransactionType.EXPENSE,
                through=today,
            ):
                previous = totals.get(total.category_id)
                if previous is None:
                    totals[total.category_id] = total
                else:
                    totals[total.category_id] = CategoryTotal(
                        previous.category_id,
                        previous.name,
                        previous.color,
                        previous.total_cents + total.total_cents,
                    )
        cumulative_budget: list[int] = []
        cumulative_spending: list[int] = []
        budget_run = spending_run = 0
        for month in overview.months:
            budget_run += month.budget_cents
            spending_run += month.spending_cents
            cumulative_budget.append(budget_run)
            cumulative_spending.append(spending_run)
        if 0 < actual_months < 12 and overview.forecast_spending_cents is not None:
            remaining = 12 - actual_months
            actual_total = cumulative_spending[actual_months - 1]
            to_add = overview.forecast_spending_cents - actual_total
            for step in range(1, remaining + 1):
                cumulative_spending[actual_months - 1 + step] = actual_total + round(
                    to_add * step / remaining
                )
        return YearReport(
            year=year,
            generated_on=today,
            overview=overview,
            categories=_category_slices(
                list(totals.values()),
                total_cents=overview.ytd_spending_cents,
            ),
            cumulative_budget=cumulative_budget,
            cumulative_spending=cumulative_spending,
            actual_months=actual_months,
            prior_income_cents=overview.prior_income_cents,
            prior_spending_cents=overview.prior_spending_cents,
        )

    def net_worth(self, *, today: date | None = None) -> NetWorthReport:
        today = today or date.today()
        accounts = [
            AccountBalance(account, self.repo.account_balance_as_of(account.id, today))
            for account in self.repo.list_accounts()
        ]
        current_month = month_key(today)
        trend: list[NetWorthPoint] = []
        for offset in range(11, -1, -1):
            month = shift_month(current_month, -offset)
            point_date = today if month == current_month else _month_end(month)
            total = sum(
                self.repo.account_balance_as_of(balance.account.id, point_date)
                for balance in accounts
            )
            trend.append(NetWorthPoint(month, month_label(month).split()[0][:3], total))
        year_start = date(today.year - 1, 12, 31)
        year_start_total = sum(
            self.repo.account_balance_as_of(balance.account.id, year_start)
            for balance in accounts
        )
        summaries = self.service.list_debt_summaries(as_of=today)
        debts: list[DebtRow] = []
        for summary in summaries:
            plan = plan_payoff(
                [
                    DebtInput(
                        summary.debt,
                        summary.balance_cents,
                        summary.effective_minimum_cents,
                    )
                ],
                extra_cents=0,
                strategy=DebtStrategy.AVALANCHE,
                start_month=current_month,
            )
            debts.append(
                DebtRow(
                    name=summary.debt.name,
                    kind_label=summary.debt.kind.label,
                    balance_cents=summary.balance_cents,
                    apr_bps=summary.debt.apr_bps,
                    minimum_cents=summary.effective_minimum_cents,
                    interest_cents=summary.monthly_interest_cents,
                    payoff_month=plan.debt_free_month if plan.finished else None,
                    minimum_below_interest=summary.minimum_below_interest,
                    account_name=summary.account_name,
                )
            )
        minimum_plan = (
            self.service.debt_plan(0, DebtStrategy.AVALANCHE, today=today, as_of=today)
            if summaries
            else None
        )
        extra_plan = (
            self.service.debt_plan(
                NET_WORTH_EXTRA_CENTS,
                DebtStrategy.AVALANCHE,
                today=today,
                as_of=today,
            )
            if summaries
            else None
        )
        return NetWorthReport(
            as_of=today,
            generated_on=today,
            accounts=accounts,
            trend=trend,
            year_start_net_worth_cents=year_start_total,
            debts=debts,
            minimum_plan=minimum_plan,
            extra_plan=extra_plan,
            extra_cents=NET_WORTH_EXTRA_CENTS,
            goals=self.service.list_goal_progress(today),
        )
