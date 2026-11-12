"""Export all budget data to a formatted Excel workbook with live formulas and lookups.

Sheets (in order): Summary, Transactions, Accounts, Budgets, Bills, Lookups. Raw data is
written as values; every derived column is an Excel formula that references the other sheets,
so edits made in the spreadsheet flow through. Each formula also carries the value the app
computed, so the file shows correct numbers even in viewers that don't recalculate.
Formulas use only functions available in Excel, Numbers, Google Sheets and LibreOffice.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date
from pathlib import Path

from .config import APP_NAME
from .models import Bill, BillFrequency, Transaction, TransactionType
from .repository import Repository
from .xlsx import Cell, Sheet, Style, write_workbook

H = Style.HEADER
TX = "Transactions"
PER_YEAR = {BillFrequency.ONCE: 0, BillFrequency.WEEKLY: 52, BillFrequency.MONTHLY: 12, BillFrequency.YEARLY: 1}
WARNING_RATIO = 0.8
REMINDER_CELL = "Lookups!$H$2"


def _money(cents: int) -> float:
    return round(cents / 100, 2)


def _month_start(month: str) -> date:
    year, mon = month.split("-")
    return date(int(year), int(mon), 1)


def _header(*titles: str) -> list[Cell]:
    return [Cell(t, H) for t in titles]


def _transactions_sheet(transactions: list[Transaction], account_kinds: dict[str, str]) -> Sheet:
    sheet = Sheet(TX, widths=[12, 11, 10, 20, 14, 20, 20, 36, 13, 13, 13, 13], freeze_rows=1, filter_header_row=1, tab_color="#3949AB")
    sheet.append(
        _header("Date", "Month", "Type", "Account", "Account Type", "Category", "To Account", "Description", "Amount", "Cash Flow", "Income", "Spending")
    )
    for tx in transactions:
        r = len(sheet.rows) + 1
        amount = _money(tx.amount_cents)
        kind = tx.type
        sheet.append(
            [
                tx.occurred_on,
                Cell(tx.occurred_on.replace(day=1), Style.MONTH, f"DATE(YEAR(A{r}),MONTH(A{r}),1)"),
                kind.label,
                tx.account_name or "",
                Cell(account_kinds.get(tx.account_name or "", ""), None, f'IFERROR(INDEX(Accounts!$B:$B,MATCH(D{r},Accounts!$A:$A,0)),"")'),
                tx.category_name or "",
                tx.transfer_account_name or "",
                tx.description,
                Cell(amount, Style.CURRENCY),
                Cell(
                    _money(tx.amount_cents) if kind is TransactionType.ADJUSTMENT else amount * tx.flow_sign,
                    Style.CURRENCY,
                    f'IF(C{r}="Adjustment",I{r},IF(OR(C{r}="Income",C{r}="Refund"),I{r},IF(C{r}="Expense",-I{r},0)))',
                ),
                Cell(amount if kind == TransactionType.INCOME else 0, Style.CURRENCY, f'IF(C{r}="Income",I{r},0)'),
                Cell(
                    {TransactionType.EXPENSE: amount, TransactionType.REFUND: -amount}.get(kind, 0),
                    Style.CURRENCY,
                    f'IF(C{r}="Expense",I{r},IF(C{r}="Refund",-I{r},0))',
                ),
            ]
        )
    return sheet


def _accounts_sheet(repo: Repository, transactions: list[Transaction]) -> tuple[Sheet, int]:
    sheet = Sheet("Accounts", widths=[22, 14, 16, 15, 15, 15, 15, 13], freeze_rows=1, filter_header_row=1, tab_color="#00897B")
    sheet.append(_header("Account", "Type", "Opening Balance", "Money In", "Money Out", "Balance", "Amount Owed", "Transactions"))
    money_in: dict[int, int] = defaultdict(int)
    money_out: dict[int, int] = defaultdict(int)
    counts: dict[int, int] = defaultdict(int)
    for tx in transactions:
        counts[tx.account_id] += 1
        if tx.type in (TransactionType.INCOME, TransactionType.REFUND):
            money_in[tx.account_id] += tx.amount_cents
        elif tx.type is not TransactionType.ADJUSTMENT:
            money_out[tx.account_id] += tx.amount_cents
        if tx.transfer_account_id is not None:
            money_in[tx.transfer_account_id] += tx.amount_cents
            counts[tx.transfer_account_id] += 1

    sumifs = f"SUMIFS({TX}!$I:$I,{TX}!$D:$D,A{{r}},{TX}!$C:$C,{{kind}})"
    for balance in repo.account_balances():
        acct = balance.account
        r = len(sheet.rows) + 1
        sheet.append(
            [
                acct.name,
                acct.kind.label,
                Cell(_money(acct.opening_balance_cents), Style.CURRENCY),
                Cell(
                    _money(money_in[acct.id]),
                    Style.CURRENCY,
                    sumifs.format(r=r, kind='"Income"') + "+" + sumifs.format(r=r, kind='"Refund"') + f"+SUMIFS({TX}!$I:$I,{TX}!$G:$G,A{r})",
                ),
                Cell(_money(money_out[acct.id]), Style.CURRENCY, sumifs.format(r=r, kind='"Expense"') + "+" + sumifs.format(r=r, kind='"Transfer"')),
                Cell(_money(balance.balance_cents), Style.CURRENCY, f'C{r}+D{r}-E{r}+SUMIFS({TX}!$I:$I,{TX}!$D:$D,A{r},{TX}!$C:$C,"Adjustment")'),
                Cell(_money(balance.owed_cents) if acct.kind.is_liability else 0, Style.CURRENCY, f'IF(B{r}="Credit Card",MAX(0,-F{r}),0)'),
                Cell(counts[acct.id], Style.INTEGER, f"COUNTIF({TX}!$D:$D,A{r})+COUNTIF({TX}!$G:$G,A{r})"),
            ]
        )
    last = len(sheet.rows)
    balances = repo.account_balances()
    total_row = [Cell("Net worth", Style.BOLD), None, None, None, None]
    if last > 1:
        total_row += [
            Cell(_money(sum(b.balance_cents for b in balances)), Style.BOLD_CURRENCY, f"SUM(F2:F{last})"),
            Cell(_money(sum(b.owed_cents for b in balances if b.account.kind.is_liability)), Style.BOLD_CURRENCY, f"SUM(G2:G{last})"),
        ]
    else:
        total_row += [Cell(0, Style.BOLD_CURRENCY), Cell(0, Style.BOLD_CURRENCY)]
    return sheet, sheet.append(total_row)


def _budgets_sheet(repo: Repository) -> Sheet:
    sheet = Sheet("Budgets", widths=[11, 22, 14, 14, 14, 10, 13], freeze_rows=1, filter_header_row=1, tab_color="#FB8C00")
    sheet.append(_header("Month", "Category", "Limit", "Spent", "Remaining", "% Used", "Status"))
    for month in repo.budget_months():
        for p in repo.budget_progress(month):
            r = len(sheet.rows) + 1
            status = "Over budget" if p.ratio > 1 else "Near limit" if p.ratio >= WARNING_RATIO else "On track"
            sheet.append(
                [
                    Cell(_month_start(month), Style.MONTH),
                    p.category_name,
                    Cell(_money(p.limit_cents), Style.CURRENCY),
                    Cell(_money(p.spent_cents), Style.CURRENCY, f"SUMIFS({TX}!$L:$L,{TX}!$F:$F,B{r},{TX}!$B:$B,A{r})"),
                    Cell(_money(p.remaining_cents), Style.CURRENCY, f"C{r}-D{r}"),
                    Cell(round(p.ratio, 6), Style.PERCENT, f"IF(C{r}=0,0,D{r}/C{r})"),
                    Cell(status, None, f'IF(F{r}>1,"Over budget",IF(F{r}>={WARNING_RATIO},"Near limit","On track"))'),
                ]
            )
    return sheet


def _bill_status(bill: Bill, days: int, reminder_days: int) -> str:
    if not bill.active:
        return "Paused"
    if days < 0:
        return "Overdue"
    if days == 0:
        return "Due today"
    return "Due soon" if days <= reminder_days else "Upcoming"


def _bills_sheet(repo: Repository, today: date) -> tuple[Sheet, int]:
    sheet = Sheet("Bills", widths=[28, 13, 12, 11, 12, 11, 11, 20, 22, 13, 14, 16, 8], freeze_rows=1, filter_header_row=1, tab_color="#E53935")
    sheet.append(
        _header(
            "Bill", "Kind", "Amount", "Repeats", "Next Due", "Days Until", "Status", "Pay From",
            "Category / Pays", "Amount Due", "Monthly Cost", "Pay-From Balance", "Active",
        )
    )
    reminder_days = repo.reminder_days()
    balances = {b.account.id: b.balance_cents for b in repo.account_balances()}
    monthly_total = 0.0
    for bill in repo.list_bills(include_inactive=True):
        r = len(sheet.rows) + 1
        days = (bill.next_due - today).days
        due = _money(repo.amount_due(bill))
        monthly = round(due * PER_YEAR[bill.frequency] / 12, 6)
        if bill.active:
            monthly_total += monthly
        sheet.append(
            [
                bill.name,
                "Card payment" if bill.is_card_payment else "Bill",
                Cell(_money(bill.amount_cents), Style.CURRENCY) if bill.amount_cents else None,
                bill.frequency.label,
                bill.next_due,
                Cell(days, Style.INTEGER, f"E{r}-TODAY()"),
                Cell(
                    _bill_status(bill, days, reminder_days),
                    None,
                    f'IF(M{r}="No","Paused",IF(F{r}<0,"Overdue",IF(F{r}=0,"Due today",IF(F{r}<={REMINDER_CELL},"Due soon","Upcoming"))))',
                ),
                bill.account_name or "",
                (bill.pay_to_account_name if bill.is_card_payment else bill.category_name) or "",
                Cell(due, Style.CURRENCY, f'IF(C{r}<>"",C{r},IFERROR(INDEX(Accounts!$G:$G,MATCH(I{r},Accounts!$A:$A,0)),0))'),
                Cell(monthly, Style.CURRENCY, f"J{r}*IFERROR(INDEX(Lookups!$E:$E,MATCH(D{r},Lookups!$D:$D,0)),0)/12"),
                Cell(_money(balances.get(bill.account_id, 0)), Style.CURRENCY, f"IFERROR(INDEX(Accounts!$F:$F,MATCH(H{r},Accounts!$A:$A,0)),0)"),
                "Yes" if bill.active else "No",
            ]
        )
    last = len(sheet.rows)
    total = Cell(round(monthly_total, 6), Style.BOLD_CURRENCY, f'SUMIFS(K2:K{last},M2:M{last},"Yes")' if last > 1 else None)
    return sheet, sheet.append([Cell("Monthly cost of active bills", Style.BOLD), *[None] * 9, total])


def _lookups_sheet(repo: Repository) -> Sheet:
    sheet = Sheet("Lookups", widths=[22, 10, 3, 12, 18, 3, 26, 10], tab_color="#757575")
    categories = repo.list_categories()
    frequencies = list(BillFrequency)
    settings = [("Reminder days before due", repo.reminder_days())]
    sheet.append([*_header("Category", "Type"), None, *_header("Frequency", "Payments per Year"), None, *_header("Setting", "Value")])
    for i in range(max(len(categories), len(frequencies), len(settings))):
        row: list = [None] * 8
        if i < len(categories):
            row[0], row[1] = categories[i].name, categories[i].type.label
        if i < len(frequencies):
            row[3], row[4] = frequencies[i].label, Cell(PER_YEAR[frequencies[i]], Style.INTEGER)
        if i < len(settings):
            row[6], row[7] = settings[i][0], Cell(settings[i][1], Style.INTEGER)
        sheet.append(row)
    return sheet


def _summary_sheet(repo: Repository, transactions: list[Transaction], today: date, accounts_total: int, bills: Sheet, bills_total: int) -> Sheet:
    sheet = Sheet("Summary", widths=[30, 15, 15, 15, 13, 15, 13], tab_color="#43A047")
    balances = repo.account_balances()
    sheet.append([Cell(f"{APP_NAME} Export", Style.TITLE)])
    sheet.append([Cell(f"Exported {today:%B %d, %Y}. Values update automatically when you edit the other sheets.", Style.MUTED)])
    sheet.append([])
    overdue = sum(1 for row in bills.rows[1 : bills_total - 1] if row[6].value == "Overdue")
    bills_monthly = bills.rows[bills_total - 1][10].value
    sheet.append([Cell("Net worth", Style.BOLD), Cell(_money(sum(b.balance_cents for b in balances)), Style.BOLD_CURRENCY, f"Accounts!F{accounts_total}")])
    sheet.append(["Owed on credit cards", Cell(_money(sum(b.owed_cents for b in balances if b.account.kind.is_liability)), Style.CURRENCY, f"Accounts!G{accounts_total}")])
    sheet.append(["Monthly cost of bills", Cell(bills_monthly, Style.CURRENCY, f"Bills!K{bills_total}")])
    sheet.append(["Bills overdue", Cell(overdue, Style.INTEGER, 'COUNTIF(Bills!$G:$G,"Overdue")')])
    sheet.append([])

    income: dict[str, int] = defaultdict(int)
    spending: dict[str, int] = defaultdict(int)
    for tx in transactions:
        m = tx.occurred_on.strftime("%Y-%m")
        if tx.type == TransactionType.INCOME:
            income[m] += tx.amount_cents
        elif tx.type == TransactionType.EXPENSE:
            spending[m] += tx.amount_cents
        elif tx.type == TransactionType.REFUND:
            spending[m] -= tx.amount_cents
    budgeted: dict[str, tuple[int, int]] = {}
    for m in repo.budget_months():
        progress = repo.budget_progress(m)
        budgeted[m] = (sum(p.limit_cents for p in progress), sum(p.spent_cents for p in progress))

    header_row = sheet.append(_header("Month", "Income", "Spending", "Net", "Savings Rate", "Budgeted", "Budget Used"))
    sheet.freeze_rows = header_row
    months = sorted(set(income) | set(spending) | set(budgeted) | {t.occurred_on.strftime("%Y-%m") for t in transactions}, reverse=True)
    for m in months:
        r = len(sheet.rows) + 1
        inc, spend = income[m], spending[m]
        limit, spent = budgeted.get(m, (0, 0))
        sheet.append(
            [
                Cell(_month_start(m), Style.MONTH),
                Cell(_money(inc), Style.CURRENCY, f"SUMIFS({TX}!$K:$K,{TX}!$B:$B,A{r})"),
                Cell(_money(spend), Style.CURRENCY, f"SUMIFS({TX}!$L:$L,{TX}!$B:$B,A{r})"),
                Cell(_money(inc - spend), Style.CURRENCY, f"B{r}-C{r}"),
                Cell(round((inc - spend) / inc, 6) if inc else 0, Style.PERCENT, f"IF(B{r}=0,0,D{r}/B{r})"),
                Cell(_money(limit), Style.CURRENCY, f"SUMIFS(Budgets!$C:$C,Budgets!$A:$A,A{r})"),
                Cell(round(spent / limit, 6) if limit else "", Style.PERCENT, f'IF(F{r}=0,"",SUMIFS(Budgets!$D:$D,Budgets!$A:$A,A{r})/F{r})'),
            ]
        )
    first, last = header_row + 1, len(sheet.rows)
    if last >= first:
        total_inc, total_spend = sum(income.values()), sum(spending.values())
        sheet.append(
            [
                Cell("All time", Style.BOLD),
                Cell(_money(total_inc), Style.BOLD_CURRENCY, f"SUM(B{first}:B{last})"),
                Cell(_money(total_spend), Style.BOLD_CURRENCY, f"SUM(C{first}:C{last})"),
                Cell(_money(total_inc - total_spend), Style.BOLD_CURRENCY, f"B{last + 1}-C{last + 1}"),
                Cell(round((total_inc - total_spend) / total_inc, 6) if total_inc else 0, Style.BOLD_PERCENT, f"IF(B{last + 1}=0,0,D{last + 1}/B{last + 1})"),
            ]
        )
    return sheet


def build_sheets(repo: Repository, today: date | None = None) -> list[Sheet]:
    today = today or date.today()
    transactions = sorted(repo.list_transactions(), key=lambda t: (t.occurred_on, t.id or 0))
    kinds = {a.name: a.kind.label for a in repo.list_accounts()}

    tx_sheet = _transactions_sheet(transactions, kinds)
    accounts, accounts_total = _accounts_sheet(repo, transactions)
    bills, bills_total = _bills_sheet(repo, today)
    summary = _summary_sheet(repo, transactions, today, accounts_total, bills, bills_total)
    return [summary, tx_sheet, accounts, _budgets_sheet(repo), bills, _lookups_sheet(repo)]


def export_workbook(repo: Repository, path: Path, today: date | None = None) -> Path:
    return write_workbook(path, build_sheets(repo, today), title=f"{APP_NAME} Export", app_name=APP_NAME)
