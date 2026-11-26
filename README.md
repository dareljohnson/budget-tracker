# Budget Tracker

A personal budget tracker desktop app built with **Python 3.13**, **[Flet](https://flet.dev) 1.x**, and **SQLite**.

## Features

- **Dashboard**: net worth across all accounts, monthly income and spending, savings rate, spending by category, a six-month income vs. spending trend, account balances, budget health, recent activity, and the five days in the selected month with the highest net spending.
- **Accounts**: checking, savings, credit card, cash, real estate, and other accounts, each with a live balance. A **Cash** account can record where the money is kept — for example a home safe, wallet, or safe deposit box — and the location appears on its card. A **Real Estate** account holds a property's market value; **Other** accounts hold investments, vehicles, and anything else you own. Each asset account is marked **Liquid** or **Non-liquid**; checking, savings, and cash are liquid by default, and an Other account can be marked liquid if it can be spent quickly. Credit cards show the amount owed, and **Pay card** records a payment from your checking account.
- **Goals**: savings targets with progress, a fixed monthly plan, and contributions recorded as transfers.
- **Debts**: credit cards, auto, student, personal, mortgage and other debts, with a payoff planner
  comparing avalanche and snowball strategies at several extra monthly amounts.
- **Year**: monthly budget versus actual spending, year-to-date totals with the same period last year, a run-rate year-end projection, and a daily cash-flow forecast for the next 30, 60, or 90 days with an income plan.
- **Reports**: preview and export a print-ready PDF of a **monthly summary**, a **year in review**, or a **net worth statement**, built from the same ledger as the rest of the app. See [Reports](#reports).
- **Help & Guide**: a searchable in-app guide to every page and workflow, kept current with each release.
- **Balance checks and adjustments**: check any account against the balance your bank shows and see the difference. If they disagree, record a signed correction that changes the account balance only — adjustments are excluded from income, spending, and budgets. Earlier checks are kept in a history.
- **Bills and reminders**: recurring bills (one-time, weekly, monthly, yearly) and credit card payments, each with a due date. Overdue and soon-due bills show on the Bills page, on the Dashboard, and in a pop-up when the app starts; the Bills page and Dashboard also refresh themselves when the date changes, so they do not need a restart. **Mark paid** records the payment and moves the bill to its next due date, with an optional confirmation number saved on that payment. Windows notifications and SMS text messages can also remind you when the app is closed (see below).
- **Transactions**: five types, each tied to an account:
  - **Expense**: money spent, e.g. groceries paid with a credit card.
  - **Income**: money received into an account.
  - **Refund**: money returned for a purchase. It reduces spending (and budget usage) in its category.
  - **Transfer**: money moved between your own accounts, e.g. a credit card payment or moving money to savings. Transfers change balances but are **not** counted as income or spending, so card purchases aren't counted twice.
  - **Adjustment**: a signed correction recorded from a balance check. It changes the account balance only and is excluded from income, spending, and budgets.

  Search and filter by type, account, category, or month, and export the current view to CSV.
- **JMD → USD conversion**: convert Jamaican dollars at the current daily rate from the Transactions page or beside a transaction's amount. The rate is cached for 12 hours, can be entered manually when offline, and only the converted USD amount is stored in the ledger. See [Converting JMD to USD](#converting-jmd-to-usd).
- **Statement import**: import a US checking or savings statement, or a Wise USD account statement — a text PDF, a scanned PDF, or a PNG/JPEG photo — from the Transactions page. The file is parsed locally, with OCR when it has no usable text layer, and verified against the statement's own summary totals or running balance; nothing is written until you review it. When exactly one saved account number ending matches, that account is preselected, and a mismatch blocks the import. Each row has its own type, category, and (for transfers) destination account; a category can be applied to all selected rows at once, duplicate rows are flagged and left unchecked, and an opening balance can be set when the account has no earlier history. You can also remember the account's last four digits and save a category rule from a row. **Imported statements…** lets you undo an import. See [Importing bank statements](#importing-bank-statements) for the supported files and limits.
- **Excel export**: **Export Excel** on the Transactions screen, or **Excel workbook** on the Reports page, saves a full `.xlsx` workbook with live formulas. It opens in Microsoft Excel, Apple Numbers, Google Sheets, and LibreOffice. See [Excel workbook](#excel-workbook).
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
- standalone installers that bundle Python, so users don't need it installed: a Windows `setup.exe` or `.zip`, a macOS `.dmg` for Apple Silicon, and a Linux `.tar.gz` or AppImage;
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

## Backups and restore

Budget Tracker keeps local snapshots of your data and lets you take and restore one yourself. Snapshots are ordinary ZIP files in the `backups` folder inside your data folder — `%LOCALAPPDATA%\BudgetTracker\backups` on Windows and `~/.local/share/budget_tracker/backups` elsewhere (with `BUDGET_TRACKER_HOME`, it follows that folder).

- **Automatic:** after startup and on a six-hour check, the app takes a snapshot when the newest automatic one is more than 24 hours old. That is at most one automatic backup a day, not a fixed time. The newest **14 automatic** snapshots are kept and older ones are deleted.
- **Manual:** click the backup icon in the navigation rail (next to the appearance and text-size buttons) and choose **Back up now**. Manual backups are never deleted automatically.
- **Before changes:** a copy is taken before a database migration and before a restore replaces data. The newest 3 pre-migration copies are kept, along with the pre-restore copy.
- **Contents:** each ZIP holds a consistent copy of the database, your receipt images, and a `manifest.json` describing the snapshot. Backups are **not encrypted** and are never uploaded — they stay on this PC. Use disk encryption such as BitLocker (Windows) or FileVault (macOS) if the folder needs protection.
- **Restore:** choose **Restore** on a snapshot, or **Restore from file…** to pick a ZIP. The app checks and stages the backup, then applies it when you close and restart; before replacing anything it copies your current data. The live database is never replaced while the app is open. If the restore cannot be applied, your current data is kept and the reason is shown.

Pre-migration `.db` copies are manual recovery files, not offered in the restore list.

## Project layout

```
src/budget_tracker/
  config.py        paths and constants
  models.py        dataclasses (amounts are integer cents)
  formatting.py    money/date parsing and formatting
  db.py            connection, versioned migrations, seed data
  repository.py    parameterized SQL data access
  services.py      dashboard aggregation, CSV/Excel export, statement import, balance checks
  statements/      statement models, PDF/image extraction, local OCR, Regions + Wise adapters + generic parser
  xlsx.py          dependency-free .xlsx writer (styles, formulas, filters)
  workbook_export.py  builds the Excel workbook sheets and formulas
  reports.py       monthly, year, and net-worth report snapshots
  report_pdf.py    PDF layout, page preview, and atomic export
  report_charts.py print-ready Pillow charts for reports
  reminders.py     due-bill check + Windows toast/SMS (for Task Scheduler)
  sms.py           carrier email-to-SMS gateways and SMTP delivery
  currency.py      JMD/USD live rate, cache, and conversion
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

A credit card or loan account also records its **APR** and **loan type**: Revolving, Installment, Same as cash, or Other. For **Same as cash**, enter the **promo APR** and **promo end date**; the account card shows the promo rate, its end date, and the standard rate afterward. The account is the source of truth: linking a debt copies the debt's APR onto an account that has none, and changing the account APR updates the linked debt's payoff rate.

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
- Pay the loan with **Log payment** on the debt's card, **Pay loan** on the Accounts page, or a bill whose target is the loan account.
- **Log payment** and a loan bill's **Mark paid** split a payment into **principal**, **interest**, and **escrow**. Principal is a transfer into the loan, so the unpaid principal drops by that part only; interest and escrow are expenses. Leave interest and escrow at zero to record a principal-only payment.
- **Extra principal** is separate from the scheduled payment. It reduces the loan in full and is neither interest nor escrow. The Debts page's payoff plan prefills each debt with its share of the plan's first-month extra; when the plan's first target is nearly paid off, the unused extra rolls to the next debt. The dialog shows the resulting **Principal** and **Total cash**.
- The plan's **extra** and **strategy** are remembered between sessions. A loan bill's **Mark paid** uses the same first-month split, so the Bills and Debts pages agree.
- A **temporary payment reduction** is recorded in two places: the bill's **Reduced amount until** and **Then amount**, and the mortgage's **Higher payment / month** and **Higher payment starts**. The bill and payment dialogs use the bill's cash amounts; the payoff plan uses the debt's current and later minimums, so it steps up automatically when the reduction ends.
- The **Interest** field starts with an estimate of one month's interest — current balance × APR ÷ 12. Use **Estimate interest** to recompute it; replace it with the figure on your statement for an exact match. The estimate does not include the servicer's day-count.
- A mortgage can also store its **Maturity date** in the debt editor (for example `2046-04-01`) and, optionally, its **Principal & interest payment**. The payment dialog then computes the scheduled P&I from the current balance, APR and remaining payments, and adds **Escrow = total payment − scheduled P&I**. With the interest estimate, that gives the scheduled principal. The escrow is filled automatically; typing your own escrow stops the automatic replacement, and **Auto-fill escrow** turns it back on.
- The split defaults to **Mortgage interest** for interest and **Escrow** for escrow when those categories exist; otherwise it uses **Housing** and **Taxes** (or the first expense category). You can choose another category in the dialog.

### Real estate and mortgages

A property is an account whose balance is its **market value**, and its mortgage is a Loan account liability. Net worth then counts both sides: market value minus the unpaid principal balance.

1. On **Accounts**, add an account with the **Real Estate** kind and enter the property's market value.
2. Track the mortgage as a **Loan** account with the unpaid principal as its opening balance, and create a **Mortgage** debt linked to it.
3. In the debt editor, set **Kind** to **Mortgage** and choose the property under **Property (optional)**.

The property's account card shows the market value, mortgage owed, equity, and — when you record it — the original principal. The debt card shows the same linked property and equity. If a mortgage has a property but is not linked to a Loan account, its balance is not part of account-based net worth, and the debt card says so.

Mortgage payments use the principal/interest/escrow split described under [Loan accounts](#loan-accounts), so the equity figure moves by the principal portion only.

### Logging payments

Every payment moves real money and appears on the **Transactions** page.

- A **standalone debt** payment is an **expense** from the funding account you choose, categorized as **Debt payments** by default. It lowers the debt's balance, and the amount cannot exceed the current balance.
- A **linked card** payment is a **transfer** from the funding account into the card, so the card's amount owed goes down. Paying more than the balance is allowed and leaves a credit on the card, like any other card payment.
- A **linked loan** payment is split into principal, interest, and escrow. The principal is a transfer into the loan and the other two are expenses; only the principal appears in the debt's **History**, while all three appear on the **Transactions** page. Net worth falls by interest plus escrow, not by the whole payment.
- Payments logged from a debt's **Log payment** dialog are tagged to that debt and listed in its **History**. Card payments made elsewhere (for example **Pay card** on the Accounts page) still change the card's balance but are not listed in the debt's history.
- The payment date cannot be in the future, and a liability account cannot fund a payment.

### The payoff plan

The plan panel compares two strategies, each with an **extra monthly amount on top of every minimum**:

- **Avalanche** puts the extra toward the highest-APR debt first, which usually costs the least interest.
- **Snowball** puts it toward the smallest balance first, clearing debts sooner.

For the selected extra amount, the panel shows each strategy's debt-free date, months, total interest, and total paid, and marks the strategy that saves interest when both plans finish. It also compares several extra amounts ($0, $50, $100, $200, and a custom amount) side by side, and shows the month-by-month schedule and the order debts are paid off. A cleared debt's minimum rolls into the next target, so the monthly budget stays fixed until you are debt-free.

A minimum may be **zero** (a deferred student loan, for example). A plan with only a zero minimum and no extra never pays the debt off; the plan says so instead of showing a date. The panel also warns when a minimum does not cover that month's interest.

The projection is an **estimate**: it assumes a fixed APR, monthly compounding, the minimums you record, and constant payments, and it ignores fees, escrow, and rate changes. A mortgage in particular may differ from its real amortization. A plan that cannot finish within **50 years (600 months)** is reported as not paid off rather than given a payoff date.

## Year and cash flow

The **Year** page has two tabs: **This year** shows the budget-versus-actual story and its year totals, and **Next 90 days** projects your day-to-day cash balance. The switch keeps the navigation rail and the existing Year content unchanged; the year picker is hidden while the forecast is open.

### This year

- **One toolbar:** the year picker and the This year / Next 90 days switch sit together, so they do not stack over the page.
- **Four compact summary tiles:** YTD budget, YTD spending, over/under budget, and year-end pace. YTD counts only transactions dated today or earlier, and the pace uses the fraction of the year elapsed rather than whole months.
- **Coverage banner:** when fewer than three months have a budget, the page says how many do, and labels the year-end pace as an estimate only when a pace projection applies.
- **Cumulative chart:** the budget and spending lines accumulate across the year, with a scale and month labels; the current month is emphasized. Hover over a month to see its cumulative budget and spending.
- **Current-month focus:** budget vs spent, the over/under amount, and the top categories for the active month.
- **Monthly detail:** only months with activity, the current month, and the YTD total are shown by default. **Show all 12 months** reveals the full year.


### Cash flow forecast

The forecast answers "will I make it to payday?" with a daily line for the next **30, 60, or 90 days**:

- **Liquid accounts only** — accounts marked **Liquid** on the Accounts page. Checking, savings, and cash are liquid by default; an Other account can be marked liquid. Starting balances are the balances as of today, so transactions dated today are already included.
- **Known outflows** — active bills (including card and loan payments), card minimums not already covered by a card-payment bill in the same horizon, and transactions dated after today.
- **What it shows** — today's starting total, the **lowest projected point** (in red when it goes negative), the balance in 30, 60, and 90 days, per-account ending balances, and the dated events with a running balance.
- **Local and read-only** — viewing the forecast writes nothing. The chart is drawn with Pillow, which the app already uses for statements and receipts.

### Income plan

A forecast is only useful if money comes in. Click **Income plan** on the cash-flow tab to add a schedule: a name, amount, frequency, next date, and the checking, savings, or cash account where the money is deposited.

- Frequencies are **weekly**, **every 2 weeks**, **monthly**, and **twice a month**.
- Schedules are **forecasts, not transactions**. The app never creates an income transaction for you; record the real deposit when it arrives.
- A schedule starts **strictly after today**, so an already-recorded deposit is not counted twice.
- **Monthly** schedules keep an **anchor day** — the permanent day of the month, such as the 31st. In a short month that day clamps to the last day (Feb 28 or 29), and the following month returns to the 31st. The anchor is stored separately from the next date, so changing the next date does not permanently move it; the editor shows the anchor and lets you change it explicitly.
- **Twice a month** uses the anchor day plus a second day, both clamped to month end; the two days must differ.
- The editor previews the next three dates for the rule.

### What the forecast assumes

- Card payments use the amount owed **today**. An open-amount ("pay the full balance", amount `0`) card bill is projected **once**, at its next due date, and is netted against future card payments so the same debt is not subtracted twice.
- Fixed card payments are **capped** at today's remaining owed across their occurrences, so they cannot overpay a card.
- Card minimums use the status **as of today**, are due at the end of the month **after** the statement month (or today if that has passed), and are paid from the first checking account, then the first liquid account. When a card-payment bill targets the same card inside the horizon, that bill replaces the card's minimums.
- An overdue bill is shown once at today, then continues on its normal schedule; there is no backlog of past occurrences.
- Scheduled bills and income are skipped when a matching transaction dated today or later already covers them (same account, type, amount, and a date within one day).
- **Budgets and variable spending are not included.** Budget limits are plans, not scheduled cash movements; adding them would double-count bills.

## Converting JMD to USD

Use **JMD → USD** on the Transactions page for a standalone conversion, or the **JMD** button beside a transaction's **Amount** to fill that amount.

- **Live rate:** the dialog fetches the daily JMD-per-USD rate from [exchangerate-api.com](https://www.exchangerate-api.com). The request contains only the USD base currency; no account, transaction, or personal data is sent.
- **Cached:** the last rate is kept locally for 12 hours. The dialog shows when it was fetched; the refresh button fetches again.
- **Manual fallback:** if the service is unreachable, the saved rate is used. You can also edit the **JMD per USD** field directly and convert with that rate.
- **Result:** enter an amount in JMD to see the USD result. **Use $X** fills the transaction amount; **Copy USD** copies the numeric value.
- **What is stored:** the ledger remains USD-only. The JMD amount and the rate are not saved with the transaction, so add them to the description if you need a record.
- Exchange rates are daily reference rates, not a bank's buy/sell rate; your bank or card may apply a different rate and fees.

## Reports

Choose **Reports** in the navigation rail to turn your ledger into a document you can keep, print, or share.

1. Choose **Monthly summary**, **Year in review**, or **Net worth statement**.
2. Pick the month or year. A net worth statement is always **as of today**.
3. The preview is the actual PDF. Use the arrows to turn its pages before exporting.
4. **Export PDF** saves it to your Downloads folder, or to the folder you choose. The **Excel workbook** button on the same page saves the full data workbook described under [Excel workbook](#excel-workbook).

### What each report includes

- **Monthly summary** — income, spending, net, savings rate, and budget use; a six-month income and spending chart; spending by category; every budget with its spent, remaining, and status; the five largest expenses; and account balances with net worth as of the end of the report month, or today while that month is still in progress. Category amounts are net of refunds, so a category where refunds exceed spending appears as a negative amount, and shares use net spending.
- **Year in review** — year-to-date income, spending, net, and savings rate; a cumulative budget-versus-spending chart with a year-end projection; month-by-month budget performance (the current month is marked **In progress**); annual category totals; a comparison with the same period last year when that data exists; and the year-end pace.
- **Net worth statement** — net worth with changes since last month and January 1; a twelve-month trend; **liquid, real estate, and other non-liquid assets** listed separately, followed by liabilities by account; each debt's balance, APR, minimum, monthly interest, and payoff at its current minimum, with an avalanche comparison at $200 extra per month; and progress on each savings goal.

### What the reports assume

- Reports read the same local SQLite data as the Dashboard, Year, Accounts, Debts, and Goals pages. They are generated on this PC and never uploaded. Like the Excel workbook and backups, exported PDFs are **not encrypted**.
- **Net worth is account-based**, matching the Dashboard. A debt linked to a card or loan is represented by that account. A standalone debt appears in the debt table but is not part of account net worth.
- **Liquidity is a property of each account.** The cash-flow forecast counts only accounts marked Liquid; the net-worth statement groups assets into Liquid, Real estate, and Other non-liquid using the same flag. A house or car remains part of net worth even though it is not spendable cash.
- **A property's balance is its market value.** A Real Estate account counts at that value; the mortgage on it is a separate Loan account whose owed balance is a liability. Linking the mortgage debt to the property only ties the two together for display — it does not change either balance.
- The year report includes the current month to date, and labels the period **to date** when that month is not complete. Future months in the chart use a simple run rate: spending year to date divided by the fraction of the year elapsed (the current month counts only its elapsed days), times twelve. Only transactions through today count as actual spending. It is an estimate, not a forecast of specific bills.
- The monthly report's balances and aggregates are as of the end of its month, or as of today while that month is still in progress. Transactions dated after the cutoff are excluded. A balance is signed: a negative card or loan balance is money owed.
- Debt payoff estimates assume a fixed APR, monthly compounding, the minimums currently recorded, and constant payments. A plan that cannot finish within 50 years is reported as **Does not pay off**; a minimum below the monthly interest will not reduce the balance.

## Importing bank statements

On the **Transactions** page, choose **Import statement** and pick a US checking or savings statement, or a Wise USD account statement: a PDF, PNG, or JPEG. The app reads it on your PC — from the PDF's text layer when there is one, and with the built-in local OCR engine for scans, photos, and PDFs with no usable text — checks the transactions against the statement's own summary totals or running balance, and shows a review before anything is written.

- The **account** is preselected when a single saved account number ending matches the statement's. If the statement's digits conflict with the selected account, the import is blocked until the right account is chosen. Statements can be imported into checking, savings, cash, or other asset accounts; credit cards and loans are refused, because a statement balance is an asset balance.
- When a statement prints a fee separately from its withdrawals total, the fee is added to the review as one editable **Statement fees** expense, so the imported ledger still matches the printed ending balance.
- Each row has its own **type** (expense, income, refund, or transfer), **category**, and, for transfers, destination account. **Apply** sets one category for all selected rows, and **Select all** toggles every row.
- Rows that look like transactions you already have are flagged **Duplicate** and left unchecked. You can still include one deliberately.
- If the account has no transactions before the statement period, you can set its **opening balance** to the statement's beginning balance.
- You can remember the account's last four digits for future matching, and save a **category rule** from a row so later imports categorize that description automatically.
- **Wise USD statements** use their own adapter. It reads the `Description / Incoming / Outgoing / Amount` table, pairs each transaction with its date line, and verifies the running `Amount` column against the printed ending balance before showing the review. Wise statements in other currencies are refused. Wise's separate **Fees Summary** is informational; only the listed transactions are imported, because they already reconcile to the ending balance.

The import is rejected — and nothing is written — if the parsed rows don't add up to the statement's summary or running balance, or if a row falls outside the statement period. The same file can't be imported twice. **Imported statements…** lists past imports so you can delete one and import it again.

### Supported statements and limits

- Most US **checking or savings** statements — specifically, those that print a standard date / description / amount transaction table (a single amount column, a debit/credit pair, or a balance column): PDF (with or without a text layer), PNG, or JPEG.
- Scans and photos are read by the built-in local OCR engine, and so are PDFs with no usable text layer. Everything runs on your PC; nothing is uploaded.
- The import is strict: it is accepted only when the parsed rows reconcile exactly to the statement's printed summary and no row falls outside the statement period. Otherwise it is refused, the refusal explains the problem, and nothing is written.
- A layout without a recognizable transaction table — no date column, no description column, or no amount/debit/credit/balance column — is refused. It is not guessed at.
- **Credit card** statements and **non-USD** statements are out of scope and are refused.
- **One file per import.** A photographed multi-page statement must be combined into a single PDF first.
- No bank feeds, and no OFX, QIF, CAMT, or CSV import. CSV is export-only.
- Everything is parsed and stored locally. PDFs and images are never copied into the database: of the statement file itself, the app records only the SHA-256 hash (to detect repeat imports) and, at most, the last four account digits.

## Scanning receipts

On the **Transactions** page, click **Scan receipt** to turn a paper receipt into an expense with its image attached. Choose **Scan with scanner** to scan one page with the first scanner connected to a Windows PC, **Scan with phone** to photograph it with a phone on the same Wi-Fi, or **Choose photo or scan file** to pick a PDF, PNG, or JPEG on any platform.

### Scanning with your phone

1. Click **Scan with phone**. The dialog shows a QR code and a local address.
2. Connect the phone to the same Wi-Fi as the PC, scan the QR code, and take or choose a photo of the receipt.
3. The photo uploads directly to the PC, then appears in the normal review form. One photo is received per link.

The phone page is served by the desktop app over your local network; no cloud service is involved. The link uses a one-time token, accepts JPEG and PNG up to 25 MB, and stops working after the upload or when the dialog closes. The phone hand-off works on Windows and macOS; only the flatbed **Scan with scanner** option is Windows-only. Windows or macOS may ask you to allow the app on private networks the first time. Camera photos are converted to JPEG in the browser before upload; if an iPhone still sends an unsupported format, set **Settings → Camera → Formats → Most Compatible**.

- Text recognition runs on your PC with the same local OCR engine the statement importer uses. The image is never uploaded.
- Review the merchant, amount, and date before saving. **Save** stays disabled until the amount, date, account, and category are filled in; a total the receipt does not print is left blank rather than guessed.
- When the receipt prints a card that matches no saved account, the account is left blank. Choose one, or add the card's last four digits on the **Accounts** page so future receipts match it.
- The image is copied to `<data folder>/receipts/` when you save. The original file is never changed.
- Only page 1 of a multi-page PDF is read.
- Rows with a receipt show a receipt icon; click it to open the image. The transaction editor can attach, replace, or remove a receipt later.
- Deleting a transaction leaves its stored receipt image on disk.

## Duplicate protection

A purchase can reach the app more than once — a receipt, a bill payment, and the bank statement may all describe it. Before writing anything, receipt capture, bill payments, and statement imports look for an existing transaction and offer it as a candidate. Nothing is merged automatically: you always confirm, and matching never edits an existing transaction.

- **Receipt matches.** Once a receipt has a recognised total, the app looks for an existing transaction on the same account with exactly that amount and a date within seven days. Pick one and **Attach & save** stores the image on that transaction and creates nothing; its date, amount, category, account, and description stay as they are. **Create a new transaction** switches to the normal form and records a separate expense. A receipt with no recognised total is not matched, because there is no amount to search with.
- **Bill matches.** When you click **Mark paid**, payments already recorded for the bill's amount, account, type, and date window appear under **Already recorded**. **Use this transaction** advances the bill to its next due date without adding or changing a transaction. **Record a new payment** records a new payment as usual.
- **Statement matches.** Each reviewed row is checked for an existing transaction with the same amount, account, and type within seven days. Rows with candidates show **Possible match**; choose **Match…** to mark the row as already recorded, which leaves it out of the import. **Import anyway** discards the match and imports the row as a new transaction.

The filters are exact amount in cents, the same account (for a transfer, either side counts; a statement row that pays out offers only outgoing transfers, and one that pays in offers only incoming ones), the same type, and a seven-day window. Description matching ignores case and extra spaces. Description similarity only orders the candidates; it never decides a match. Those are the facts that can be compared reliably, while a receipt's OCR text, a bill's name, and a bank's description often differ even for the same purchase. Nothing is auto-merged because only you can tell whether two similar entries are one event or two.

A matched statement row counts toward the statement's ending balance through its existing transaction, but it is not inserted and is not tagged with the new statement. Undoing the import leaves matched transactions untouched.

## Reconciling accounts

- A statement import records its own reconciliation at the statement's ending balance.
- To check any account at any time, use **Check balance** on the **Accounts** page. Enter the balance the bank shows — for a credit card, enter the positive **amount owed**, or a negative one when the card is in credit — and the date. The app compares it with the ledger and shows the difference.
- If the amounts differ, you can record a **correction to the ledger**. It changes the account balance only; adjustments are excluded from income, spending, and budgets.
- Previous checks, their differences, and their notes are listed in the balance check dialog.

## Bills and payment reminders

1. On the **Bills** page, click **Add Bill**.
   - **Bill**: an expense with a category, e.g. rent, utilities, or subscriptions.
   - **Card payment**: a payment from one account to a credit card or loan. Enter an amount of `0` to pay the full balance owed when the bill is due. The destination must be a card or loan; when two full-balance bills target the same card, only the soonest is shown as due so its balance is counted once.
2. Choose how far ahead you want to be reminded with **Remind me** (on the due date up to 14 days before; the default is 3 days).
3. When you've paid, click **Mark paid**. Bills are recorded as expenses, and card payments as transfers. A **Confirmation number** is optional: paste the reference the bank or payment site gave you, and it is saved with that payment, shown under it in Transactions, and included in the Excel export. The bill then moves to its next due date. Monthly bills keep their day of the month, so a bill due on the 31st is due on Feb 28 and then Mar 31. **Skip** moves to the next due date without recording a payment.

The due pop-up appears only when the app starts. The Dashboard and Bills page also compare the local date every minute and refresh after midnight, so statuses such as “due tomorrow” or “overdue” update without restarting the app.

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
- If text alerts are configured, the same command also sends the SMS summary. The `--print` option never sends a text.

### Text message alerts

Budget Tracker can text you a short due-bill summary through your carrier's email-to-SMS gateway. It does **not** include an email service: the messages are sent from an SMTP account you already control.

1. On the **Bills** page, click **Text alerts**.
2. Choose your **Carrier** and enter your **Mobile number** (10 digits; spaces, dashes and a leading `+1` are accepted). The dialog shows the gateway address it will use.
3. Enter the **Email server**, **Port**, **Security**, **Email address** and **Email password** for the account that sends the text. **From address** is optional and defaults to the email address. Match the security setting to the port: **SSL/TLS** for port **465**, or **STARTTLS** for port **587**. The dialog labels the options with those ports and warns about a mismatch.
4. Turn on **Text me when payments are due** and click **Save**. Use **Send test text** to verify the carrier and email settings.

The automatic summary is sent at most once a day. In the app it is sent at startup and on the first check after 8:00 AM local time, so leaving the app open overnight does not text you at midnight. The scheduled reminder command above sends at whatever time you register. The summary lists up to three bills with their amounts and due dates.

The carrier list includes AT&T, T-Mobile, Verizon, Sprint, Xfinity Mobile, Virgin Mobile, Tracfone, Metro PCS, Boost Mobile, Cricket, Republic Wireless, StraightTalk, Google Fi, U.S. Cellular, Ting, Consumer Cellular, C-Spire and Page Plus. When a carrier publishes both an SMS and an MMS gateway, Budget Tracker uses the SMS gateway; carriers with only an MMS gateway use that instead.

Notes:

- Use an **app password** if your email provider offers one (for example, Gmail, Yahoo and Outlook require one when two-step verification is on). Do not use your main email password. Typical settings are `smtp.gmail.com` on port 587 with STARTTLS, `smtp.mail.yahoo.com` on port 465 with SSL/TLS or port 587 with STARTTLS, or `smtp.office365.com` on port 587 with STARTTLS; check your provider's help page if its policy has changed.
- The mobile number, email address and app password are stored in the local SQLite database, which is not encrypted. Protect the PC with disk encryption such as BitLocker or FileVault.
- Carrier email-to-SMS gateways are best-effort: messages may be delayed, split, filtered as spam, or blocked, and carriers occasionally change the gateway address. Send a test after setting it up.
- The automatic text is sent only while the app is running, or when the scheduled reminder command runs. It is not a separate cloud service.

#### Text alert troubleshooting

- **`[SSL: WRONG_VERSION_NUMBER]`** — the port and security setting do not match. Use port **465** with **SSL/TLS**, or port **587** with **STARTTLS**. The dialog blocks a mismatch before connecting and its Security menu names the usual port for each option.
- **The email server rejected the sign-in** — use an **app password** from your email provider. Yahoo, Gmail and Outlook require one when two-step verification is on; a normal account password usually fails.
- **The email server rejected the message address** — check the mobile number, carrier and optional From address. Carrier gateways occasionally change.
- **A test arrives but no automatic text does** — automatic texts are sent only when there are due bills, at most once a day, and not before 8:00 AM in-app. The scheduled reminder command sends at the time you register it. Confirm **Text me when payments are due** is on and click **Save**.

## Excel workbook

**Export Excel** exports **all** of your data, not just the filtered view. The workbook has these sheets, which link to each other with formulas. If you edit or add a row in Transactions, the other sheets update.

| Sheet | Contents | Formulas and lookups |
|---|---|---|
| **Summary** | Net worth, amount owed on cards, monthly cost of bills, bills overdue, then one row per month (Income, Spending, Net, Savings Rate, Budgeted, Budget Used) and an all-time total | `SUMIFS` over Transactions and Budgets, `COUNTIF` over Bills, plus links to the Accounts and Bills totals |
| **Transactions** | Every transaction, oldest first: Date, Month, Type, Account, Account Type, Category, To Account, Description, Amount, Cash Flow, Income, Spending, Confirmation | Month = `DATE(YEAR(),MONTH(),1)`; Account Type = `INDEX/MATCH` into Accounts; Cash Flow, Income, and Spending use `IF` (refunds reduce spending) |
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
- **Reports stay local**: report data comes from the same services and repository calls as the screens, and the PDF is written to a temporary file before being moved into place. The preview is the exported PDF, rendered locally with the PDF engine.
- **The in-app Help & Guide lives in `src/budget_tracker/ui/views/help.py`.** Update the relevant section whenever a feature or user-visible workflow is added or changed, so the guide ships with the feature.
