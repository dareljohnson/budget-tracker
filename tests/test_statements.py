import unittest
from datetime import date

from budget_tracker.statements.model import UnsupportedStatementError, Word
from budget_tracker.statements.regions_checking import parse_statement


def W(text, x, y):
    return Word(text, x, y)


def sample_words(period=("September", "16,", "2026", "October", "15,", "2026"), withdrawal="300.00"):
    words = [
        W("Regions", 150, 15), W("Bank", 200, 15),
        W("62+", 200, 237), W("LIFEGREEN", 215, 237), W("CHECKING", 270, 237),
        W(period[0], 200, 252), W(period[1], 235, 252), W(period[2], 262, 252),
        W("through", 300, 252),
        W(period[3], 335, 252), W(period[4], 372, 252), W(period[5], 398, 252),
        W("ACCOUNT", 33, 141), W("#", 73, 141), W("060", 90, 159),
        W("SUMMARY", 250, 300),
        W("$1,000.00", 220, 324), W("$500.00", 220, 336), W("$300.00", 220, 348),
        W("$10.00", 220, 360), W("$0.00", 220, 381), W("$0.00", 220, 405),
        W("$1,190.00", 220, 417),
        W("Beginning", 33, 342), W("Balance", 80, 342),
        W("Deposits", 33, 354), W("&", 70, 354), W("Credits", 79, 354),
        W("Withdrawals", 33, 366), W("Fees", 33, 378),
        W("Ending", 33, 399), W("Balance", 60, 399),
        W("DEPOSITS", 250, 465), W("&", 285, 465), W("CREDITS", 296, 465),
        W("09/20", 75, 480), W("Card", 110, 480), W("Purchase", 135, 480),
        W("Merchant", 185, 480), W("200.00", 222, 480),
        W("10/02", 240, 480), W("Payroll", 275, 480), W("300.00", 320, 480),
        W("WITHDRAWALS", 260, 579),
        W("09/25", 75, 591), W("Rent", 110, 591), W(withdrawal, 540, 591),
        W("FEES", 280, 620),
        W("09/30", 75, 632), W("Service", 110, 632), W("Fee", 145, 632), W("10.00", 540, 632),
        W("DAILY", 260, 660), W("BALANCE", 292, 660),
        W("09/20", 75, 672), W("1,000.00", 540, 672),
    ]
    return words


class TestRegionsParser(unittest.TestCase):
    def test_parses_metadata_summary_and_transactions(self):
        stmt = parse_statement(sample_words())
        self.assertEqual(stmt.institution, "Regions Bank")
        self.assertEqual(stmt.account_label, "62+ LIFEGREEN CHECKING")
        self.assertEqual(stmt.account_last4, "060")
        self.assertEqual((stmt.period_start, stmt.period_end), (date(2026, 9, 16), date(2026, 10, 15)))
        self.assertEqual(
            (stmt.beginning_cents, stmt.deposits_cents, stmt.withdrawals_cents, stmt.fees_cents, stmt.ending_cents),
            (100_000, 50_000, 30_000, 1_000, 119_000),
        )
        self.assertEqual(
            [(t.occurred_on, t.amount_cents, t.direction) for t in stmt.transactions],
            [(date(2026, 9, 20), 20_000, "income"), (date(2026, 10, 2), 30_000, "income"),
             (date(2026, 9, 25), 30_000, "expense"), (date(2026, 9, 30), 1_000, "expense")],
        )

    def test_wrapped_description_keeps_one_transaction(self):
        words = [w for w in sample_words() if not (w.text in ("Merchant", "10/02", "Payroll", "200.00", "300.00") and w.top == 480)]
        words += [W("Merchant", 185, 491), W("200.00", 222, 491)]
        words += [W("10/02", 240, 500), W("Payroll", 275, 500), W("300.00", 320, 500)]
        stmt = parse_statement(words)
        self.assertEqual(stmt.transactions[0].description, "Card Purchase Merchant")
        self.assertEqual(stmt.transactions[0].amount_cents, 20_000)

    def test_december_transaction_uses_start_year(self):
        words = sample_words(period=("December", "16,", "2025", "January", "15,", "2026"))
        words = [W("12/20", w.x0, w.top) if w.text == "09/25" else w for w in words]
        self.assertEqual(parse_statement(words).transactions[2].occurred_on, date(2025, 12, 20))

    def test_january_transaction_uses_next_year(self):
        words = sample_words(period=("December", "16,", "2025", "January", "15,", "2026"))
        words = [W("01/03", w.x0, w.top) if w.text == "10/02" else w for w in words]
        self.assertEqual(parse_statement(words).transactions[1].occurred_on, date(2026, 1, 3))

    def test_validation_rejects_total_mismatch(self):
        from budget_tracker.statements.model import StatementValidationError
        with self.assertRaises(StatementValidationError) as ctx:
            parse_statement(sample_words(withdrawal="250.00"))
        self.assertTrue(any("Withdrawals" in e for e in ctx.exception.errors))

    def test_rejects_unrecognized_statement(self):
        with self.assertRaises(UnsupportedStatementError):
            parse_statement([W("Hello", 10, 10), W("world", 40, 10)])
