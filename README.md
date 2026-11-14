# Budget Tracker

A personal budget tracker desktop app built with **Python 3.13**, **[Flet](https://flet.dev) 1.x**, and **SQLite**.

## Features

- **Dashboard**: net worth across all accounts, monthly income and spending, savings rate, spending by category, a six-month income vs. spending trend, account balances, budget health, and recent activity.
- **Accounts**: checking, savings, credit card, cash, and other accounts, each with a live balance. Credit cards show the amount owed, and **Pay card** records a payment from your checking account.
- **Goals**: savings targets with progress, a fixed monthly plan, and contributions recorded as transfers.
- **Debts**: credit cards, auto, student, personal, mortgage and other debts, with a payoff planner
  comparing avalanche and snowball strategies at several extra monthly amounts.
- **Year**: monthly budget versus actual spending, year-to-date totals with the same period last year, a run-rate year-end projection, and cash flow across the year.
- **Balance checks and adjustments**: check any account against the balance your bank shows and see the difference. If they disagree, record a signed correction that changes the account balance only — adjustments are excluded from income, spending, and budgets. Earlier checks are kept in a history.
- **Bills and reminders**: recurring bills (one-time, weekly, monthly, yearly) and credit card payments, each with a due date. Overdue and soon-due bills show on the Bills page, on the Dashboard, and in a pop-up when the app starts. **Mark paid** records the payment and moves the bill to its next due date. Windows notifications can also remind you when the app is closed (see below).
- **Transactions**: five types, each tied to an account:
  - **Expense**: money spent, e.g. groceries paid with a credit card.
  - **Income**: money received into an account.
  - **Refund**: money returned for a purchase. It reduces spending (and budget usage) in its category.
  - **Transfer**: money moved between your own accounts, e.g. a credit card payment or moving money to savings. Transfers change balances but are **not** counted as income or spending, so card purchases aren't counted twice.
  - **Adjustment**: a signed correction recorded from a balance check. It changes the account balance only and is excluded from income, spending, and budgets.

  Search and filter by type, account, category, or month, and export the current view to CSV.
- **Statement import**: import a US checking or savings statement — a text PDF, a scanned PDF, or a PNG/JPEG photo — from the Transactions page. The file is parsed locally, with OCR when it has no usable text layer, and verified against the statement's own summary totals; nothing is written until you review it. When exactly one saved account number ending matches, that account is preselected, and a mismatch blocks the import. Each row has its own type, category, and (for transfers) destination account; a category can be applied to all selected rows at once, duplicate rows are flagged and left unchecked, and an opening balance can be set when the account has no earlier history. You can also remember the account's last four digits and save a category rule from a row. **Imported statements…** lets you undo an import. See [Importing bank statements](#importing-bank-statements) for the supported files and limits.
- **Excel export**: **Export Excel** on the Transactions screen saves a full `.xlsx` workbook with live formulas. It opens in Microsoft Excel, Apple Numbers, Google Sheets, and LibreOffice. See [Excel workbook](#excel-workbook).
- **Budgets**: set monthly limits per expense category. Progress bars turn amber at 80% and red when you go over. You can copy last month's budgets.
- **Categories**: income and expense categories with custom colors and icons. Default categories are created on first run. Category rules saved from statement imports are listed under **Categories → Rules**, where they can be reviewed and deleted.
- **Light/dark theme**: your choice is saved.

## Getting started

Requires **Python 3.13 or newer** (`requires-python` in `pyproject.toml`).

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"

budget-tracker            # or: python -m budget_tracker
```

To start the app from the Start menu or the desktop instead of a terminal, run `.\packaging\windows\Install-Shortcut.ps1`.

Copying an existing checkout to another PC? Don't copy its `.venv` — recreate it there. See [Moving the project to another PC](PACKAGING.md#moving-the-project-to-another-pc).

## Installers and launchers

See **[PACKAGING.md](PACKAGING.md)** for:
- launchers that run the app from this source folder on Windows, macOS and Linux;
- standalone installers that bundle Python, so users don't need it installed: a Windows `setup.exe` or `.zip`, a macOS `.dmg`, and a Linux `.tar.gz` or AppImage;
- a GitHub Actions workflow that builds all three;
- moving the project and its data to another PC.

The statement importer's libraries (`pdfplumber`, `pypdfium2`, `Pillow`, and RapidOCR on ONNX Runtime) are normal runtime dependencies, so the standalone installers bundle them automatically — no extra packaging step is needed. They add roughly 200 MB to the installed app.

## Running tests

```powershell
python -m pytest
```

## Data storage

Data is stored in SQLite at `%LOCALAPPDATA%\BudgetTracker\budget_tracker.db` on Windows (`~/.local/share/budget_tracker` on Linux and macOS). To use a different folder, set the `BUDGET_TRACKER_HOME` environment variable. With a Microsoft Store Python, Windows redirects the file into the package cache — see [Where data lives](PACKAGING.md#where-data-lives) for the exact paths.

Statement files (PDFs and images) are never copied into the database. Of the statement file itself, an import keeps only its SHA-256 hash and, if you choose to remember it, the last four account digits; the statement summary and its imported transactions are saved as normal local data.

## Project layout

```
src/budget_tracker/
  config.py        paths and constants
  models.py        dataclasses (amounts are integer cents)
  formatting.py    money/date parsing and formatting
  db.py            connection, versioned migrations, seed data
  repository.py    parameterized SQL data access
  services.py      dashboard aggregation, CSV/Excel export, statement import, balance checks
  statements/      statement models, PDF/image extraction, local OCR, Regions adapter + generic parser
  xlsx.py          dependency-free .xlsx writer (styles, formulas, filters)
  workbook_export.py  builds the Excel workbook sheets and formulas
  reminders.py     due-bill check + Windows toast (for Task Scheduler)
  main.py          Flet app shell (navigation rail, theme)
  ui/              theme, shared components, views
src/main.py        entry point for `flet build` (native installers)
src/assets/        app icon
packaging/         launchers, installer build scripts, icon generator (see PACKAGING.md)
tests/             data-layer and UI editor tests
```

## Tracking credit cards

1. Add the card on the **Accounts** page. If it already has a balance, enter that as **Amount currently owed**.
2. Record purchases as **Expenses** paid with the card. The card's balance owed goes up.
3. When you pay the bill, click **Pay card** (or add a **Transfer** from checking to the card). Checking goes down and the amount owed goes down. Your spending doesn't change, because you already counted it when you made the purchases.
4. Each month, record the **minimum payment** printed on the card's statement — from the card's **Minimum** row, or while using **Check balance**. The card shows whether that month's minimum has been paid: payments into the card are matched oldest-unpaid first, so a payment made the following month still counts, and anything above a minimum rolls into the next recorded month.

Existing databases are upgraded automatically: transactions recorded before accounts existed are assigned to a **Checking** account.

## Savings goals

Use the **Goals** page to set money aside for a target, such as a trip, a deposit, or an emergency fund.

1. Click **Add goal** and enter a name, a **Target amount**, and a **Target date**. If you have already saved part of the target, enter it in **Already saved** (optional); that amount changes the monthly plan.
2. Choose the **Destination account** — the savings account the money will live in. If you have no savings account yet, add one on the **Accounts** page first.
3. The card shows how much you have saved so far, how much remains, the fixed monthly amount planned for the goal, and how much you have contributed this month.
4. Click **Contribute** to move money into the goal. The contribution is recorded as a transfer from the account you choose into the goal's destination account.
5. Click **Schedule & history** to see the month-by-month plan and every contribution recorded for the goal.

The monthly amount is fixed when the goal is created: it spreads the amount still to save (the target minus any amount already saved) across the months from the start month through the target month, rounding up and making the final month smaller so the plan lands exactly on the target. Recording a contribution does not change it. Editing the target amount or target date recalculates the plan.

Limits:

- Only contributions recorded from the goal count toward its progress. Other activity in the savings account — deposits, withdrawals, interest, and transfers recorded elsewhere — does not.
- When two goals share one savings account, each counts only the transfers recorded from that goal. Money saved for one goal does not count toward another.
- A goal earmarks money; it does not lock it. You can still spend or move money out of the savings account, but progress is based on the goal's recorded contributions, not the account's current balance.
- Once a goal has contributions, its destination account cannot be changed. You can still edit its name, target amount, target date, and amount already saved; create a new goal for a different account instead.
- Deleting a goal removes only the goal. Its contribution transfers stay on the **Transactions** list and in the ledger, and the money stays in the savings account.

## Debts

Use the **Debts** page to track what you owe and see how different monthly payments would pay it down. Credit cards, auto loans, student loans, personal loans, mortgages, and other debts are all supported.

1. Click **Add debt** and enter a name, the **kind** (credit card, auto, student, personal, mortgage, or other), the current **balance**, the **APR**, and the **minimum monthly payment**. You can also enter the **original balance** to see a paid-down bar on the card.
2. A **standalone debt** keeps its balance in the app: update it yourself, or log payments to move it. A **linked debt** is tied to a credit-card or loan account you already track, and its balance is the account's live amount owed. An account can be linked to only one debt.
3. For a linked card, the plan minimum follows the **latest statement minimum** recorded for that card — the current month when recorded, otherwise the most recent. The minimum entered on the debt is only a fallback until a statement minimum exists.

### Loan accounts

To track a mortgage, auto, student, or personal loan alongside your other accounts, create an account with the **Loan** kind and enter the amount owed as its opening balance.

- Link a debt to the loan account to make the debt's balance follow the account's live amount owed, just like a linked credit card.
- Pay the loan with **Log payment** on the debt's card, **Pay loan** on the Accounts page, or a bill whose target is the loan account. Each payment is a transfer into the loan, so the owed balance drops without counting it as spending.
- Loan payments are tracked as a single amount: the app does not split them into principal and interest.

### Logging payments

Every payment moves real money and appears on the **Transactions** page.

- A **standalone debt** payment is an **expense** from the funding account you choose, categorized as **Debt payments** by default. It lowers the debt's balance, and the amount cannot exceed the current balance.
- A **linked card** payment is a **transfer** from the funding account into the card, so the card's amount owed goes down. Paying more than the balance is allowed and leaves a credit on the card, like any other card payment.
- Payments logged from a debt's **Log payment** dialog are tagged to that debt and listed in its **History**. Card payments made elsewhere (for example **Pay card** on the Accounts page) still change the card's balance but are not listed in the debt's history.
- The payment date cannot be in the future, and a liability account cannot fund a payment.

### The payoff plan

The plan panel compares two strategies, each with an **extra monthly amount on top of every minimum**:

- **Avalanche** puts the extra toward the highest-APR debt first, which usually costs the least interest.
- **Snowball** puts it toward the smallest balance first, clearing debts sooner.

For the selected extra amount, the panel shows each strategy's debt-free date, months, total interest, and total paid, and marks the strategy that saves interest when both plans finish. It also compares several extra amounts ($0, $50, $100, $200, and a custom amount) side by side, and shows the month-by-month schedule and the order debts are paid off. A cleared debt's minimum rolls into the next target, so the monthly budget stays fixed until you are debt-free.

A minimum may be **zero** (a deferred student loan, for example). A plan with only a zero minimum and no extra never pays the debt off; the plan says so instead of showing a date. The panel also warns when a minimum does not cover that month's interest.

The projection is an **estimate**: it assumes a fixed APR, monthly compounding, the minimums you record, and constant payments, and it ignores fees, escrow, and rate changes. A mortgage in particular may differ from its real amortization. A plan that cannot finish within **50 years (600 months)** is reported as not paid off rather than given a payoff date.

## Importing bank statements

On the **Transactions** page, choose **Import statement** and pick a US checking or savings statement: a PDF, PNG, or JPEG. The app reads it on your PC — from the PDF's text layer when there is one, and with the built-in local OCR engine for scans, photos, and PDFs with no usable text — checks the transactions against the statement's own summary totals, and shows a review before anything is written.

- The **account** is preselected when a single saved account number ending matches the statement's. If the statement's digits conflict with the selected account, the import is blocked until the right account is chosen.
- Each row has its own **type** (expense, income, refund, or transfer), **category**, and, for transfers, destination account. **Apply** sets one category for all selected rows, and **Select all** toggles every row.
- Rows that look like transactions you already have are flagged **Duplicate** and left unchecked. You can still include one deliberately.
- If the account has no transactions before the statement period, you can set its **opening balance** to the statement's beginning balance.
- You can remember the account's last four digits for future matching, and save a **category rule** from a row so later imports categorize that description automatically.

The import is rejected — and nothing is written — if the parsed rows don't add up to the statement's summary, or if a row falls outside the statement period. The same file can't be imported twice. **Imported statements…** lists past imports so you can delete one and import it again.

### Supported statements and limits

- Most US **checking or savings** statements — specifically, those that print a standard date / description / amount transaction table (a single amount column, a debit/credit pair, or a balance column): PDF (with or without a text layer), PNG, or JPEG.
- Scans and photos are read by the built-in local OCR engine, and so are PDFs with no usable text layer. Everything runs on your PC; nothing is uploaded.
- The import is strict: it is accepted only when the parsed rows reconcile exactly to the statement's printed summary and no row falls outside the statement period. Otherwise it is refused, the refusal explains the problem, and nothing is written.
- A layout without a recognizable transaction table — no date column, no description column, or no amount/debit/credit/balance column — is refused. It is not guessed at.
- **Credit card** statements and **non-USD** statements are out of scope and are refused.
- **One file per import.** A photographed multi-page statement must be combined into a single PDF first.
- No bank feeds, and no OFX, QIF, CAMT, or CSV import. CSV is export-only.
- Everything is parsed and stored locally. PDFs and images are never copied into the database: of the statement file itself, the app records only the SHA-256 hash (to detect repeat imports) and, at most, the last four account digits.

## Reconciling accounts

- A statement import records its own reconciliation at the statement's ending balance.
- To check any account at any time, use **Check balance** on the **Accounts** page. Enter the balance the bank shows — for a credit card, enter the positive **amount owed** — and the date. The app compares it with the ledger and shows the difference.
- If the amounts differ, you can record a **correction to the ledger**. It changes the account balance only; adjustments are excluded from income, spending, and budgets.
- Previous checks, their differences, and their notes are listed in the balance check dialog.

## Bills and payment reminders

1. On the **Bills** page, click **Add Bill**.
   - **Bill**: an expense with a category, e.g. rent, utilities, or subscriptions.
   - **Card payment**: a payment from one account to a credit card or loan. Enter an amount of `0` to pay the full balance owed when the bill is due.
2. Choose how far ahead you want to be reminded with **Remind me** (on the due date up to 14 days before; the default is 3 days).
3. When you've paid, click **Mark paid**. Bills are recorded as expenses, and card payments as transfers. The bill then moves to its next due date. Monthly bills keep their day of the month, so a bill due on the 31st is due on Feb 28 and then Mar 31. **Skip** moves to the next due date without recording a payment.

### Notifications when the app is closed (Windows)

`budget_tracker.reminders` checks your bills and shows a Windows toast notification if any are due. To run it every morning at 9:00, register a scheduled task yourself (replace the path with your project folder):

```powershell
$project = "C:\path\to\BudgetTracker"
schtasks /Create /SC DAILY /ST 09:00 /TN "BudgetTracker Reminders" `
  /TR "cmd /c cd /d `"$project`" && set PYTHONPATH=src && .venv\Scripts\pythonw.exe -m budget_tracker.reminders"
```

- If you installed the app with `pip install -e .`, you can run `.venv\Scripts\budget-tracker-remind.exe` instead.
- To test without showing a notification, run `python -m budget_tracker.reminders --print`.
- To remove the scheduled task, run `schtasks /Delete /TN "BudgetTracker Reminders"`.

## Excel workbook

**Export Excel** exports **all** of your data, not just the filtered view. The workbook has these sheets, which link to each other with formulas. If you edit or add a row in Transactions, the other sheets update.

| Sheet | Contents | Formulas and lookups |
|---|---|---|
| **Summary** | Net worth, amount owed on cards, monthly cost of bills, bills overdue, then one row per month (Income, Spending, Net, Savings Rate, Budgeted, Budget Used) and an all-time total | `SUMIFS` over Transactions and Budgets, `COUNTIF` over Bills, plus links to the Accounts and Bills totals |
| **Transactions** | Every transaction, oldest first: Date, Month, Type, Account, Account Type, Category, To Account, Description, Amount, Cash Flow, Income, Spending | Month = `DATE(YEAR(),MONTH(),1)`; Account Type = `INDEX/MATCH` into Accounts; Cash Flow, Income, and Spending use `IF` (refunds reduce spending) |
| **Accounts** | Opening Balance, Money In, Money Out, Balance, Amount Owed, transaction count, plus a Net worth total | `SUMIFS` by account and type (transfers count as money in for the receiving account); credit card owed = `MAX(0,-Balance)` |
| **Budgets** | Month, Category, Limit, Spent, Remaining, % Used, Status | Spent = `SUMIFS` of Transactions spending by category and month; Status = Over budget / Near limit (80%) / On track |
| **Bills** | Amount, Repeats, Next Due, Days Until, Status, Pay From, Amount Due, Monthly Cost, Pay-From Balance, Active | Days Until = `Next Due - TODAY()`; Status uses the reminder window from Lookups; Amount Due for an open card payment uses `INDEX/MATCH` to get the card's owed amount; Monthly Cost uses the payments-per-year lookup |
| **Lookups** | Category to Type, Frequency to payments per year, reminder window | Tables the other sheets look up |

Every sheet has a bold header row that stays visible when you scroll and has filter drop-downs. Money, date, month, and percent cells have number formats. Calculated values are saved in the file, so they appear right away, and Excel recalculates when the file opens.

## Design notes

- **Money is stored as integer cents** so amounts never pick up floating-point rounding errors.
- **The schema is versioned** with `PRAGMA user_version`. Foreign keys are enforced, and the database runs in WAL mode.
- **Layers stay separate**: UI → services → repository. All SQL lives in the repository, and every query is parameterized.
- **CSV export protects spreadsheets**: values that start with `=`, `+`, `-`, or `@` are escaped so they can't run as formulas.
- **Excel export protects spreadsheets too**: text you typed is always saved as plain text, never as a formula. Only formulas the app generates are formulas. The file is written to a temporary file first and then moved into place, so a failed export can't leave a half-written file.
