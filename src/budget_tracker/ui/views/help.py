"""In-app Help & Guide: searchable sections for every part of Budget Tracker.

Keep this file current whenever a feature or user-visible workflow changes.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

import flet as ft

from ..components import card, empty_state, page_header
from ..context import AppContext
from ..theme import GAP, SEED_COLOR, fs


@dataclass(frozen=True)
class HelpTable:
    columns: tuple[str, ...]
    rows: tuple[tuple[str, ...], ...]


@dataclass(frozen=True)
class HelpBlock:
    heading: str = ""
    body: str = ""
    bullets: tuple[str, ...] = ()
    table: HelpTable | None = None


@dataclass(frozen=True)
class HelpTopic:
    title: str
    body: str
    keywords: tuple[str, ...] = ()
    blocks: tuple[HelpBlock, ...] = ()


@dataclass(frozen=True)
class HelpSection:
    title: str
    icon: ft.IconData
    summary: str
    topics: tuple[HelpTopic, ...]


GUIDE: tuple[HelpSection, ...] = (
    HelpSection(
        "Quick start",
        ft.Icons.PLAY_CIRCLE_OUTLINE,
        "The five-minute path from an empty app to a working budget.",
        (
            HelpTopic(
                "Add your accounts",
                "Open Accounts → Add Account. Enter the current balance as the opening balance. "
                "A credit card or loan asks for the amount currently owed instead. "
                "Cash accounts can record where the money is kept.",
                ("start", "account", "opening balance", "setup"),
            ),
            HelpTopic(
                "Record a transaction",
                "Open Transactions → Add Transaction, or use the + button on the navigation rail. "
                "Choose Expense, Income, Refund, Transfer or Adjustment. Transfers move money "
                "between your own accounts and are not spending.",
                ("transaction", "expense", "income", "transfer"),
            ),
            HelpTopic(
                "Set up repeating bills",
                "Open Bills → Add Bill. Choose the amount, how often it repeats, the account it is "
                "paid from, and either an expense category or a card/loan account it pays. "
                "Mark paid advances it to the next due date.",
                ("bill", "recurring", "reminder", "due"),
            ),
            HelpTopic(
                "Scan a receipt",
                "Open Transactions → Scan receipt. Use Scan with phone for a phone on the same "
                "Wi-Fi, Scan with scanner for a Windows flatbed scanner, or Choose photo or scan "
                "file. Review the recognised merchant, amount and date before saving.",
                ("receipt", "scan", "phone", "camera", "ocr"),
            ),
            HelpTopic(
                "Settings and appearance",
                "The gear at the bottom of the navigation rail opens Settings: light or dark "
                "mode, the color theme, text size, Backups and this Help guide.",
                ("settings", "appearance", "dark", "light", "text size", "theme", "backups", "help"),
            ),
            HelpTopic(
                "Review the Dashboard",
                "The Dashboard shows net worth, this month's income and spending, the savings "
                "rate, spending by category, upcoming bills, account balances and recent activity.",
                ("dashboard", "overview", "net worth"),
            ),
        ),
    ),
    HelpSection(
        "Accounts",
        ft.Icons.ACCOUNT_BALANCE,
        "Bank accounts, cash, cards, loans, property and investments.",
        (
            HelpTopic(
                "Account types",
                "Checking, Savings, Credit Card, Loan, Cash, Real Estate and Other. Checking, "
                "savings and cash are liquid by default. Credit cards and loans are liabilities; "
                "their balance is the amount owed.",
                ("account type", "checking", "savings", "cash", "other", "loan", "credit card"),
            ),
            HelpTopic(
                "Where is the cash stored?",
                "Cash accounts can record a location such as a home safe, wallet or safe deposit "
                "box. The location appears on the account card. Change the account type away "
                "from Cash and the location is cleared.",
                ("cash location", "safe", "wallet", "safe deposit box"),
            ),
            HelpTopic(
                "APR, loan type and promos",
                "Credit-card and loan accounts record an APR and a loan type: Revolving, "
                "Installment, Same as cash or Other. Same as cash adds a promo APR and end date. "
                "A liability can also record a credit limit; its account card then shows the "
                "limit and available credit. A linked debt uses the account's rate for its "
                "payoff plan.",
                ("apr", "interest rate", "revolving", "installment", "same as cash", "promo",
                 "credit limit", "available credit"),
            ),
            HelpTopic(
                "Real estate and mortgages",
                "A Real Estate account's balance is the property's market value. The mortgage is "
                "a separate Loan account liability, and the mortgage debt can be linked to the "
                "property. The card then shows market value, mortgage owed, equity and original "
                "principal.",
                ("property", "house", "home", "mortgage", "equity", "market value"),
            ),
            HelpTopic(
                "Liquid and non-liquid",
                "The cash-flow forecast counts only liquid accounts. Reports group assets into "
                "Liquid, Real estate and Other non-liquid. A house or car still counts in net "
                "worth even though it cannot be spent quickly.",
                ("liquid", "non-liquid", "forecast", "net worth"),
            ),
        ),
    ),
    HelpSection(
        "Transactions",
        ft.Icons.RECEIPT_LONG,
        "The five entry types, transfers, adjustments and receipts.",
        (
            HelpTopic(
                "The five transaction types",
                "Expense: money spent. Income: money received. Refund: money returned for an "
                "earlier purchase; it reduces that category's spending. Transfer: money moved "
                "between your own accounts. Adjustment: a signed balance correction excluded "
                "from income, spending and budgets.",
                ("expense", "income", "refund", "transfer", "adjustment"),
            ),
            HelpTopic(
                "Transfers",
                "A transfer needs a source and a destination account. Credit-card and loan "
                "payments are transfers, so they lower the balance without counting as spending "
                "a second time.",
                ("transfer", "card payment", "loan payment"),
            ),
            HelpTopic(
                "Editing and deleting",
                "Open a transaction to edit it. Deleting removes it from the ledger; a saved "
                "receipt image stays on disk. Adjustments are used for balance checks and other "
                "corrections.",
                ("edit", "delete", "adjustment"),
            ),
        ),
    ),
    HelpSection(
        "Receipts",
        ft.Icons.PHOTO_CAMERA,
        "Turn a paper receipt into an expense, with the image attached.",
        (
            HelpTopic(
                "Scan with your phone",
                "Click Scan with phone in the receipt dialog. The PC shows a QR code and a local "
                "address. On a phone on the same Wi-Fi, scan the code and take or choose a photo. "
                "The photo uploads directly to the PC; no cloud service is used.",
                ("phone", "qr", "wifi", "iphone", "android", "camera", "network"),
            ),
            HelpTopic(
                "Scanner or file",
                "Scan with scanner uses the first flatbed/WIA scanner on Windows. Choose photo or "
                "scan file accepts a PDF, PNG or JPEG on Windows and macOS. Only page one of a "
                "PDF is read.",
                ("scanner", "flatbed", "pdf", "image", "mac"),
            ),
            HelpTopic(
                "Review and match",
                "The OCR draft fills merchant, amount, date, account and category. Save stays "
                "disabled until the amount, date, account and category are valid. If an existing "
                "transaction looks like the same purchase, the dialog can attach the receipt to "
                "it instead of creating a duplicate.",
                ("ocr", "review", "match", "duplicate", "attach"),
            ),
            HelpTopic(
                "Where receipts are stored",
                "Saved receipts are copied to the app's data folder under receipts/. The original "
                "file is never changed. Deleting a transaction leaves its stored receipt.",
                ("storage", "file", "image", "delete"),
            ),
        ),
    ),
    HelpSection(
        "Bills",
        ft.Icons.EVENT_NOTE,
        "Recurring payments, due dates, reminders and temporary amounts.",
        (
            HelpTopic(
                "Add and pay a bill",
                "Bills → Add Bill. Choose the amount, repeat frequency and pay-from account. A "
                "regular bill uses an expense category; a card or loan payment uses a target "
                "account. Mark paid records the payment and advances the due date. Skip advances "
                "without recording a payment.",
                ("bill", "mark paid", "skip", "due date", "confirmation"),
            ),
            HelpTopic(
                "Temporary reduced amount",
                "A bill can have a lower amount until a date and a later amount. Set Reduced "
                "until and Then amount. The bill, payment prefill and Bills row use the correct "
                "amount for the due date.",
                ("temporary", "reduced", "step", "2028", "payment change"),
            ),
            HelpTopic(
                "Reminders and text alerts",
                "The reminder window controls the startup pop-up and Windows notifications. SMS "
                "alerts can be configured on the Bills page; they use the saved provider settings "
                "and are sent by the reminder process. The Text alerts dialog can also text PLOC "
                "draw and sweep reminders once a month.",
                ("reminder", "notification", "sms", "text", "alert", "ploc"),
            ),
        ),
    ),
    HelpSection(
        "Debts",
        ft.Icons.CREDIT_SCORE,
        "What you owe, payment splits, the reduction chart, velocity banking and payoff planning.",
        (
            HelpTopic(
                "Add or link a debt",
                "A standalone debt keeps its own balance. A linked debt follows the live amount "
                "owed on a credit-card or loan account. An account can be linked to one debt.",
                ("debt", "standalone", "linked", "card", "loan"),
            ),
            HelpTopic(
                "Principal, interest and escrow",
                "For a linked loan, Log payment and a loan bill's Mark paid split the payment. "
                "Principal is a transfer that reduces the loan; interest and escrow are expenses. "
                "Only principal reduces the unpaid balance, and net worth falls by interest plus "
                "escrow.",
                ("principal", "interest", "escrow", "split", "mortgage", "p&i"),
            ),
            HelpTopic(
                "Interest and escrow estimates",
                "Interest starts at current balance × APR ÷ 12. Escrow is derived as the total "
                "payment minus the scheduled principal-and-interest payment when a maturity date "
                "or P&I payment is set. Replace either with the statement figure for an exact "
                "match.",
                ("estimate", "interest", "escrow", "maturity", "p&i", "amortization"),
            ),
            HelpTopic(
                "Extra principal",
                "Extra principal is additional money applied to the loan and is neither interest "
                "nor escrow. The Debts payoff plan prefills each debt with its share of the "
                "first-month extra; when a target is nearly paid off, the unused extra rolls to "
                "the next debt. The plan's extra and strategy are remembered.",
                ("extra", "principal", "payoff", "snowball", "avalanche", "split"),
            ),
            HelpTopic(
                "Debt reduction chart",
                "The chart above the payoff plan stacks every debt's balance from today to the end "
                "of the current plan. Use 1y, 3y, 5y or All to change the time horizon. The side "
                "panel lists each debt's balance, APR and payoff month; hover a month for that "
                "month's balances. The chart uses the selected strategy and extra payment, and "
                "groups extras as Other when there are more than eight debts. Escrow is not "
                "included; the yearly and monthly tables below are the full data view.",
                ("chart", "graph", "stacked", "horizon", "1y", "3y", "5y", "debt reduction"),
            ),
            HelpTopic(
                "Velocity banking (personal line of credit)",
                "Use a personal line of credit in repeating cycles against selected debts.",
                ("velocity", "line of credit", "ploc", "chunk", "sweep", "cycle", "variable",
                 "multiple targets", "personal loan", "payoff plan"),
                (
                    HelpBlock(
                        heading="How a cycle works",
                        bullets=(
                            "Choose one or more credit cards, auto loans or personal loans.",
                            "The plan draws a chunk. The chunk goes to the highest-APR target "
                            "first; any leftover goes to the next target.",
                            "Every selected debt's minimum is still paid. The monthly sweep "
                            "pays the PLOC.",
                            "The line must reach zero before another chunk starts.",
                            "The plan compares the result with paying the same cash flow "
                            "directly to the selected targets.",
                        ),
                    ),
                    HelpBlock(
                        heading="Velocity or Payoff",
                        body="A debt selected for Velocity is marked Velocity on its card and "
                        "removed from the normal Payoff plan. Every other debt is marked Payoff. "
                        "Clearing the velocity plan returns its targets to Payoff.",
                    ),
                    HelpBlock(
                        heading="Variable rate and what is real",
                        bullets=(
                            "The PLOC rate is treated as variable; scenarios show +2 and +4 "
                            "points.",
                            "Hover each setup field for a short explanation.",
                            "The plan is read-only: it does not move money. Link a Revolving "
                            "Loan account in setup to read its live APR, amount owed and credit "
                            "limit when set; draws and sweeps then update the plan automatically.",
                            "Set a monthly Reminder day to get a PLOC sweep reminder, or a "
                            "new-chunk reminder when the linked line reaches zero.",
                            "If no account is linked, enter the APR and current amount owed "
                            "manually; update them after a draw or sweep.",
                        ),
                    ),
                ),
            ),
            HelpTopic(
                "Velocity banking setup examples",
                "Example 1 — credit card; Example 2 — auto loan; Example 3 — existing PLOC "
                "balance; Example 4 — multiple targets. These are illustrative, not "
                "recommendations.",
                ("velocity example", "ploc example", "chunk example", "sweep example",
                 "credit card velocity", "auto loan velocity", "multiple targets example"),
                (
                    HelpBlock(
                        heading="Common setups",
                        table=HelpTable(
                            columns=("Field", "Credit card", "Auto loan", "Existing PLOC"),
                            rows=(
                                ("Target debt",
                                 "Visa, $4,800 at 24.99%, min $150",
                                 "Auto loan, $9,000 at 6.90%, min $250",
                                 "Visa, $3,000 at 22.00%, min $100"),
                                ("PLOC limit", "$12,000", "$12,000", "$12,000"),
                                ("PLOC APR", "3.99% variable", "3.99% variable", "3.99% variable"),
                                ("Current owed", "$0", "$0", "$1,500"),
                                ("Chunk", "$3,000", "$2,000", "$1,000"),
                                ("Monthly sweep", "$350", "$300", "$250"),
                                ("Credit reserve", "$2,000", "$4,000", "$1,000"),
                            ),
                        ),
                    ),
                    HelpBlock(
                        heading="Multiple targets",
                        body="With Visa $4,800 at 24.99% and an auto loan $9,000 at 6.90%, "
                        "use the same fields with a $12,000 PLOC at 3.99%, $2,000 reserve, "
                        "$3,000 chunk and $500 sweep. Chunks go to Visa first; after Visa is "
                        "paid, remaining chunks go to the auto loan.",
                    ),
                    HelpBlock(
                        heading="Avoid",
                        bullets=(
                            "A target APR below the PLOC APR.",
                            "A chunk above limit minus current balance minus reserve.",
                            "A sweep that does not cover line interest.",
                        ),
                    ),
                ),
            ),
            HelpTopic(
                "Tracking PLOC draws, sweeps and payments",
                "The Velocity Banking card is a simulation; it does not create a PLOC account "
                "or move money. Use normal accounts and transfers to track the real line.",
                ("track ploc", "ploc sweep", "draw", "transfer", "log payment", "personal loc"),
                (
                    HelpBlock(
                        heading="Track the real PLOC",
                        bullets=(
                            "Add a Loan account such as Personal LOC with the amount owed, APR, "
                            "Revolving loan type and, optionally, the credit limit.",
                            "Link that account in Velocity setup. Its APR, current amount owed "
                            "and credit limit (when set) become the source of truth.",
                            "Record a draw as a Transfer from the PLOC to Checking.",
                            "Record a sweep as a Transfer from Checking to the PLOC.",
                            "The Velocity card's Record draw and Record sweep buttons prefill "
                            "the transfer and amount; review and save the transaction.",
                            "A linked account updates the plan automatically. If no account is "
                            "linked, update Current PLOC amount owed after each draw or sweep.",
                        ),
                    ),
                    HelpBlock(
                        heading="What Log payment does",
                        bullets=(
                            "Linked credit card: a transfer from Checking to the card; the "
                            "card's live balance drops.",
                            "Linked auto or personal loan: principal, interest and escrow split; "
                            "principal reduces the loan.",
                            "Standalone debt: an expense that lowers the debt balance.",
                            "It does not draw or sweep the PLOC. To use PLOC money, first "
                            "transfer from the PLOC to Checking, then log the payment from "
                            "Checking.",
                        ),
                    ),
                ),
            ),
            HelpTopic(
                "PLOC draw and sweep reminders",
                "Set a Reminder day of the month (1–31) in Velocity setup to get one monthly "
                "nudge. This is a calendar day, not a number of days before; 15 means the 15th "
                "of each month. On or "
                "after that day, if the linked PLOC still has a balance, the app says PLOC sweep "
                "due. If the PLOC is at zero and a target remains, it says the next chunk is "
                "ready. The reminder appears at startup, and the scheduled reminder command can "
                "show a Windows notification. If PLOC reminder texts are enabled in Text alerts, "
                "the startup and scheduled checks can also send the reminder by SMS. A sweep "
                "reminder is suppressed when a transfer into the PLOC is already recorded that "
                "month. A reminder never creates a transaction; "
                "use Record draw or Record sweep on the Velocity card to prefill it for "
                "confirmation.",
                ("reminder", "alert", "sweep reminder", "draw reminder", "notification"),
            ),
            HelpTopic(
                "PLOC sweep and payment walkthrough",
                "Example only: Visa $4,800 at 24.99%, minimum $150; Personal LOC $12,000 "
                "limit at 3.99% variable, current owed $0; reserve $2,000, chunk $3,000, "
                "sweep $350; Checking starts at $2,000. The Record draw and Record sweep "
                "buttons can prefill the transfers in steps 1 and 4.",
                ("ploc walkthrough", "payment walkthrough", "sweep example", "log payment",
                 "draw example", "velocity demo", "ploc demo"),
                (
                    HelpBlock(
                        heading="Step by step",
                        table=HelpTable(
                            columns=("Step", "Action", "Checking", "PLOC owed", "Visa"),
                            rows=(
                                ("Start", "—", "$2,000", "$0", "$4,800"),
                                ("1", "Transfer PLOC → Checking, $3,000", "$5,000", "$3,000", "$4,800"),
                                ("2", "Log payment Visa, $3,000 from Checking", "$2,000", "$3,000", "$1,800"),
                                ("3", "Log Visa minimum, $150", "$1,850", "$3,000", "~$1,687"),
                                ("4", "Transfer Checking → PLOC sweep, $350", "$1,500", "~$2,660", "~$1,687"),
                                ("5", "Repeat steps 3–4 monthly", "↓", "↓", "↓"),
                                ("6", "PLOC zero; next chunk", "—", "$0", "~$598"),
                                ("7", "Draw $598; Log payment Visa $598", "—", "$598", "$0"),
                                ("8", "Final Checking → PLOC sweep", "↓", "$0", "$0"),
                            ),
                        ),
                    ),
                    HelpBlock(
                        heading="What updates automatically",
                        bullets=(
                            "Log payment updates the target balance; the Velocity target date, "
                            "chunks and interest recalculate.",
                            "If the setup is linked to the Personal LOC Loan account, the draw "
                            "or sweep changes its balance and the plan follows automatically. If "
                            "it is not linked, update Current PLOC amount owed manually.",
                            "When a target reaches zero it drops out of the active Velocity "
                            "targets. Edit or clear the plan and choose the next target.",
                        ),
                    ),
                    HelpBlock(
                        heading="End state",
                        body="The sample estimates Visa paid around September 2027, PLOC "
                        "cleared around October 2027, and about $312 net interest saved versus "
                        "paying the same $500 per month directly to Visa. These figures are "
                        "illustrative, not a statement forecast.",
                    ),
                ),
            ),
            HelpTopic(
                "Temporary higher payment",
                "A mortgage can have a current minimum and a higher payment starting on a date. "
                "The payoff plan steps up automatically. The combined schedule shows a payment "
                "change note for both the debt payment (P&I) and the linked bill's cash payment.",
                ("temporary", "higher", "step", "2029", "reduced", "payment change"),
            ),
        ),
    ),
    HelpSection(
        "Goals",
        ft.Icons.SAVINGS,
        "Savings targets, monthly plans and contributions.",
        (
            HelpTopic(
                "Create a goal",
                "Goals → Add goal. Enter a name, target amount, target date and destination "
                "savings account. Already saved is optional and changes the monthly plan.",
                ("goal", "savings", "target"),
            ),
            HelpTopic(
                "Contribute and track",
                "Contribute moves money from a funding account into the goal's savings account as "
                "a transfer. Schedule & history shows the plan and every contribution. Only "
                "contributions recorded from the goal count toward its progress.",
                ("contribute", "progress", "history", "plan"),
            ),
        ),
    ),
    HelpSection(
        "Budgets and categories",
        ft.Icons.TRACK_CHANGES,
        "Monthly limits and the categories spending is grouped into.",
        (
            HelpTopic(
                "Budgets",
                "Budgets → Add budget sets a monthly limit for an expense category. The card "
                "shows spent, remaining and status. Refunds reduce spending in their category.",
                ("budget", "limit", "spent", "remaining"),
            ),
            HelpTopic(
                "Categories and rules",
                "Categories are income or expense. Category rules map text found in descriptions "
                "to a category and are used to suggest categories during receipt and statement "
                "review.",
                ("category", "rule", "suggest", "income", "expense"),
            ),
        ),
    ),
    HelpSection(
        "Year and cash flow",
        ft.Icons.CALENDAR_MONTH,
        "Year-to-date performance, forecasts and the income plan.",
        (
            HelpTopic(
                "Year view",
                "Year shows monthly budget versus actual spending, year-to-date totals, the same "
                "period last year, and a run-rate year-end projection.",
                ("year", "ytd", "projection", "comparison"),
            ),
            HelpTopic(
                "Cash-flow forecast",
                "The forecast projects liquid balances for the next 30, 60 or 90 days using bills, "
                "scheduled income and recent spending. Only liquid accounts count. The income plan "
                "lists scheduled income and where it lands.",
                ("cash flow", "forecast", "income plan", "liquid", "30 60 90"),
            ),
        ),
    ),
    HelpSection(
        "Reports",
        ft.Icons.DESCRIPTION,
        "Printable monthly, year and net-worth statements.",
        (
            HelpTopic(
                "The three reports",
                "Monthly summary: income, spending, budgets, categories and balances for the "
                "month. Year in review: year-to-date totals, pacing and comparison. Net worth "
                "statement: assets, liabilities, debt payoff estimates and goals.",
                ("report", "pdf", "monthly", "year", "net worth"),
            ),
            HelpTopic(
                "What the reports assume",
                "Net worth is account-based, matching the Dashboard. Assets are grouped into "
                "Liquid, Real estate and Other non-liquid. A standalone debt appears in the debt "
                "table but is not part of account net worth. PDFs are local and unencrypted.",
                ("assumption", "net worth", "liquidity", "pdf", "unencrypted"),
            ),
            HelpTopic(
                "Excel workbook",
                "Reports → Excel workbook saves the full data workbook with live SUMIFS formulas "
                "for Summary, Transactions, Accounts, Budgets and Bills.",
                ("excel", "workbook", "xlsx", "export"),
            ),
        ),
    ),
    HelpSection(
        "Statement import",
        ft.Icons.UPLOAD_FILE,
        "Turn a bank statement into ledger rows.",
        (
            HelpTopic(
                "Supported statements",
                "The importer reads US checking and savings statements and Wise USD account "
                "statements as PDF, PNG or JPEG. Text is taken from the PDF when available; scans "
                "use the local OCR engine.",
                ("statement", "import", "pdf", "png", "jpeg", "wise", "ocr"),
            ),
            HelpTopic(
                "Review and match",
                "The importer checks the statement's own totals or running balance, then shows a "
                "review. Rows with an existing matching transaction can be marked already "
                "recorded. Importing also records a reconciliation at the ending balance.",
                ("statement", "review", "duplicate", "match", "reconciliation"),
            ),
        ),
    ),
    HelpSection(
        "Reconciliation",
        ft.Icons.FACT_CHECK,
        "Check an account against the bank and correct the ledger.",
        (
            HelpTopic(
                "Check balance",
                "Accounts → Check balance. Enter the balance the bank shows and the date. The app "
                "compares it with the ledger and shows the difference.",
                ("reconcile", "check balance", "difference", "bank"),
            ),
            HelpTopic(
                "Corrections",
                "If the balances differ, record a correction. It changes the account balance only "
                "and is excluded from income, spending and budgets. Previous checks are kept in "
                "the dialog's history.",
                ("correction", "adjustment", "history", "difference"),
            ),
        ),
    ),
    HelpSection(
        "Currency conversion",
        ft.Icons.CURRENCY_EXCHANGE,
        "Convert JMD to USD with a cached rate.",
        (
            HelpTopic(
                "JMD → USD",
                "Open Transactions → JMD → USD, or use the JMD button while editing a "
                "transaction. The app fetches a live USD/JMD rate and caches it, so the converter "
                "still works offline. Enter a JMD amount to see the USD value.",
                ("jmd", "usd", "currency", "rate", "converter"),
            ),
        ),
    ),
    HelpSection(
        "Backups and data",
        ft.Icons.BACKUP,
        "Where the database lives, backups, restore and privacy.",
        (
            HelpTopic(
                "Data folder",
                "The database lives in %LOCALAPPDATA%\\BudgetTracker on Windows or "
                "~/.local/share/budget_tracker on macOS and Linux (or $XDG_DATA_HOME/"
                "budget_tracker); BUDGET_TRACKER_HOME overrides it. The backups/ and receipts/ "
                "folders sit beside it. With a Microsoft Store Python, Windows redirects the "
                "folder into %LOCALAPPDATA%\\Packages\\PythonSoftwareFoundation.Python.*\\"
                "LocalCache\\Local\\BudgetTracker; the Backups dialog shows that real path. "
                "The app makes a pre-migration copy before a schema upgrade.",
                ("data folder", "database", "sqlite", "location", "migration", "mac", "linux", "store python", "redirect"),
            ),
            HelpTopic(
                "Backups and restore",
                "Open Settings (the gear at the bottom of the navigation rail) → Backups to "
                "create a manual snapshot and list earlier ones. Restore "
                "replaces the live database with a chosen backup. Automatic backups run on a "
                "schedule while the app is open. Add a cloud sync folder for off-PC copies.",
                ("backup", "restore", "snapshot", "automatic"),
            ),
            HelpTopic(
                "Cloud sync to iCloud Drive, Google Drive or OneDrive",
                "In Settings → Backups → Cloud sync, choose a folder inside iCloud Drive, Google Drive or "
                "OneDrive (the desktop sync client). Each new automatic or manual backup is copied there, "
                "and Sync now copies existing snapshots. The app does not connect to the cloud "
                "account directly; your normal sync client uploads the files. Copied backups "
                "are unencrypted and are not pruned by the app.",
                ("icloud", "google drive", "onedrive", "cloud", "sync", "backup", "offsite"),
            ),
            HelpTopic(
                "Privacy",
                "OCR, statement parsing, reports and the phone receipt hand-off run locally. The "
                "phone upload uses your local network and a one-time token. Exported PDFs and the "
                "Excel workbook are not encrypted.",
                ("privacy", "local", "cloud", "security", "phone"),
            ),
        ),
    ),
    HelpSection(
        "Troubleshooting",
        ft.Icons.HELP_OUTLINE,
        "Common reasons something does not look right.",
        (
            HelpTopic(
                "The phone cannot connect",
                "Make sure the phone and PC are on the same Wi-Fi. Allow Budget Tracker through "
                "the Windows or macOS firewall. A VPN can make the QR address unreachable; use "
                "the Wi-Fi address or disconnect the VPN. iPhone photos are converted to JPEG in "
                "the browser; if one is rejected, use Camera → Formats → Most Compatible.",
                ("phone", "wifi", "firewall", "vpn", "iphone", "heic", "connect"),
            ),
            HelpTopic(
                "Net worth does not match",
                "Net worth sums account balances. A standalone debt is not included; link it to a "
                "card or loan account. A property needs a Real Estate account for its market "
                "value. For a mortgage, only principal reduces the loan; escrow is not debt.",
                ("net worth", "difference", "standalone", "property", "mortgage", "escrow"),
            ),
            HelpTopic(
                "A payment amount changed unexpectedly",
                "The Debts page schedule shows debt payments (principal + interest), not the cash "
                "bill. Escrow is separate. A temporary reduced payment shows a payment-change "
                "note with both the debt and bill steps.",
                ("payment", "schedule", "changed", "temporary", "escrow", "p&i"),
            ),
            HelpTopic(
                "The backup folder path does not exist",
                "If the Backups dialog shows a path under AppData\\Local\\BudgetTracker but "
                "Explorer cannot open it, the app is running under the Microsoft Store Python. "
                "Windows redirects that folder into %LOCALAPPDATA%\\Packages\\"
                "PythonSoftwareFoundation.Python.*\\LocalCache\\Local\\BudgetTracker. "
                "The dialog now shows the real path; use Open backup folder. Your backups are safe.",
                ("backup", "folder", "path", "missing", "does not exist", "store python", "redirect"),
            ),
            HelpTopic(
                "Text recognition is unavailable",
                "The local OCR engine may be missing on this platform. You can still enter the "
                "merchant, amount and date by hand; the receipt image can still be attached.",
                ("ocr", "recognition", "unavailable", "manual"),
            ),
            HelpTopic(
                "Where is this guide updated?",
                "This guide is part of the app and is revised whenever a feature or workflow "
                "changes. Search for a feature name, or browse the sections above.",
                ("help", "guide", "update", "new feature"),
            ),
        ),
    ),
)


class HelpView:
    def __init__(self, ctx: AppContext) -> None:
        self.ctx = ctx
        self.search = ft.TextField(
            label="Search the guide",
            hint_text="Try 'phone', 'escrow', 'budget' or 'backup'",
            prefix_icon=ft.Icons.SEARCH,
            on_change=self._search_changed,
            on_submit=self._search_changed,
        )
        self.body = ft.Column(spacing=GAP)
        self.control = ft.Column(
            [
                page_header(
                    "Help & Guide",
                    "How Budget Tracker works, and where each feature lives",
                ),
                ft.Text(
                    "Search for a feature, or expand a section. The guide is updated with each release.",
                    size=fs(13),
                    color=ft.Colors.ON_SURFACE_VARIANT,
                ),
                self.search,
                self.body,
            ],
            spacing=GAP,
            scroll=ft.ScrollMode.AUTO,
            expand=True,
        )
        self.refresh()

    def _search_changed(self, _: ft.Event | None = None) -> None:
        self.refresh()
        self.ctx.page.update()

    def _matching_sections(self) -> list[HelpSection]:
        query = (self.search.value or "").strip().casefold()
        if not query:
            return list(GUIDE)
        matches: list[HelpSection] = []
        for section in GUIDE:
            if query in section.title.casefold() or query in section.summary.casefold():
                matches.append(section)
                continue
            topics = tuple(
                topic
                for topic in section.topics
                if query in self._topic_text(topic).casefold()
                or any(query in keyword.casefold() for keyword in topic.keywords)
            )
            if topics:
                matches.append(replace(section, topics=topics))
        return matches

    @staticmethod
    def _topic_text(topic: HelpTopic) -> str:
        parts = [topic.title, topic.body]
        for block in topic.blocks:
            parts.extend((block.heading, block.body, *block.bullets))
            if block.table is not None:
                parts.extend(block.table.columns)
                for row in block.table.rows:
                    parts.extend(row)
        return " ".join(part for part in parts if part)

    @staticmethod
    def _block_control(block: HelpBlock) -> ft.Control:
        controls: list[ft.Control] = []
        if block.heading:
            controls.append(ft.Text(
                block.heading,
                size=fs(13),
                weight=ft.FontWeight.W_600,
                color=ft.Colors.ON_SURFACE,
            ))
        if block.body:
            controls.append(ft.Text(
                block.body,
                size=fs(13),
                color=ft.Colors.ON_SURFACE_VARIANT,
                selectable=True,
            ))
        for bullet in block.bullets:
            controls.append(ft.Row(
                [
                    ft.Text("•", size=fs(13), color=SEED_COLOR),
                    ft.Text(
                        bullet,
                        size=fs(13),
                        color=ft.Colors.ON_SURFACE_VARIANT,
                        selectable=True,
                        expand=True,
                    ),
                ],
                spacing=6,
                vertical_alignment=ft.CrossAxisAlignment.START,
            ))
        if block.table is not None:
            table = ft.DataTable(
                columns=[
                    ft.DataColumn(ft.Text(column, size=fs(12), weight=ft.FontWeight.W_600))
                    for column in block.table.columns
                ],
                rows=[
                    ft.DataRow(
                        cells=[
                            ft.DataCell(ft.Text(cell, size=fs(12)))
                            for cell in row
                        ]
                    )
                    for row in block.table.rows
                ],
                column_spacing=24,
            )
            controls.append(ft.Row([table], scroll=ft.ScrollMode.AUTO))
        return ft.Column(controls, spacing=6)

    def _topic_control(self, topic: HelpTopic) -> ft.Control:
        controls: list[ft.Control] = [
            ft.Text(
                topic.title,
                size=fs(14),
                weight=ft.FontWeight.W_600,
                color=ft.Colors.ON_SURFACE,
            ),
            ft.Text(
                topic.body,
                size=fs(13),
                color=ft.Colors.ON_SURFACE_VARIANT,
                selectable=True,
            ),
        ]
        controls.extend(self._block_control(block) for block in topic.blocks)
        return ft.Column(controls, spacing=6)

    def refresh(self) -> None:
        query = (self.search.value or "").strip()
        sections = self._matching_sections()
        if not sections:
            self.body.controls = [
                card(
                    empty_state(
                        ft.Icons.SEARCH_OFF,
                        "No guide topics match",
                        "Try a different word, or clear the search to browse every section.",
                    )
                )
            ]
            return
        self.body.controls = [
            card(
                ft.ExpansionTile(
                    title=ft.Row(
                        [
                            ft.Icon(section.icon, size=fs(20), color=SEED_COLOR),
                            ft.Text(section.title, size=fs(16), weight=ft.FontWeight.W_600),
                        ],
                        spacing=10,
                    ),
                    subtitle=ft.Text(section.summary, size=fs(13), color=ft.Colors.ON_SURFACE_VARIANT),
                    controls=[
                        ft.Column(
                            [
                                self._topic_control(topic)
                                for topic in section.topics
                            ],
                            spacing=14,
                        )
                    ],
                    expanded=bool(query) or section is sections[0],
                    maintain_state=False,
                ),
                padding=4,
            )
            for section in sections
        ]
