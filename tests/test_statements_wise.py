"""Wise USD statement adapter: geometry, running balances and validation."""

from __future__ import annotations

import unittest
from datetime import date

from budget_tracker.statements import _parse_adapters
from budget_tracker.statements.model import (
    StatementValidationError,
    UnsupportedStatementError,
    Word,
)
from budget_tracker.statements.wise import parse_wise


def W(text: str, x: float, y: float) -> Word:
    return Word(text, x, y)


def wise_words(
    *,
    currency: str = "USD",
    ending: str = "975.00",
    second_balance: str = "500.00",
) -> list[Word]:
    """A synthetic Wise page in the same geometry as the real PDF."""
    words = [
        W("Wise US Inc.", 42, 10),
        W(f"{currency} statement", 42, 22),
        W("October 1, 2026 [GMT-04:00] - October 31, 2026 [GMT-05:00]", 42, 34),
        W("Account Holder", 42, 50),
        W("Account number", 303, 50),
        W("Routing number", 431, 50),
        W("Test User", 42, 62),
        W("123456789012", 303, 62),
        W("026073150", 431, 62),
        W("USD on October 31, 2026 [GMT-05:00]", 42, 80),
        W(ending, 500, 80),
        W("USD", 535, 80),
        W("Description", 42, 100),
        W("Incoming", 386, 100),
        W("Outgoing", 454, 100),
        W("Amount", 527, 100),
    ]
    rows = [
        ("Card transaction of 25.00 USD issued by Sample Store", None, "-25.00", "975.00", "October 30, 2026"),
        ("Topped up account", "500.00", None, "1,000.00", "October 20, 2026"),
        ("Card transaction of 100.00 USD issued by Another Shop", None, "-100.00", second_balance, "October 10, 2026"),
        ("Card transaction of 25.00 USD issued by Test Merchant", None, "-25.00", "600.00", "October 1, 2026"),
    ]
    top = 120
    for description, incoming, outgoing, balance, when in rows:
        words.append(W(description, 42, top))
        if incoming is not None:
            words.append(W(incoming, 386, top))
        if outgoing is not None:
            words.append(W(outgoing, 454, top))
        words.append(W(balance, 527, top))
        month, day, year = when.split()
        words.extend([W(month, 42, top + 12), W(day, 80, top + 12), W(year, 95, top + 12)])
        words.append(W("| Transaction: TEST", 120, top + 12))
        top += 30
    words.append(W("Fees Summary", 42, top))
    return words


class TestWiseParser(unittest.TestCase):
    def test_parses_metadata_summary_and_transactions(self):
        stmt = parse_wise(wise_words())

        self.assertEqual(stmt.institution, "Wise US Inc.")
        self.assertEqual(stmt.account_label, "Wise USD")
        self.assertEqual(stmt.account_last4, "9012")
        self.assertEqual((stmt.period_start, stmt.period_end), (date(2026, 10, 1), date(2026, 10, 31)))
        self.assertEqual(
            (stmt.beginning_cents, stmt.deposits_cents, stmt.withdrawals_cents, stmt.fees_cents, stmt.ending_cents),
            (62_500, 50_000, 15_000, 0, 97_500),
        )
        self.assertEqual(
            [(t.occurred_on, t.description, t.amount_cents, t.direction) for t in stmt.transactions],
            [
                (date(2026, 10, 1), "Test Merchant", 2_500, "expense"),
                (date(2026, 10, 10), "Another Shop", 10_000, "expense"),
                (date(2026, 10, 20), "Topped up account", 50_000, "income"),
                (date(2026, 10, 30), "Sample Store", 2_500, "expense"),
            ],
        )

    def test_wrapped_description_is_kept(self):
        words = wise_words()
        top = 120
        words.append(W("continued merchant text", 42, top + 6))
        stmt = parse_wise(words)
        self.assertIn("continued merchant text", stmt.transactions[-1].description)

    def test_non_usd_wise_statement_is_refused(self):
        with self.assertRaisesRegex(UnsupportedStatementError, "Only US dollar"):
            parse_wise(wise_words(currency="EUR"))

    def test_a_non_wise_document_is_refused(self):
        with self.assertRaisesRegex(UnsupportedStatementError, "not a Wise"):
            parse_wise([W("Some Other Bank", 42, 10)])

    def test_end_balance_mismatch_is_rejected(self):
        with self.assertRaisesRegex(StatementValidationError, "Ending balance"):
            parse_wise(wise_words(ending="999.00"))

    def test_running_balance_chain_mismatch_is_rejected(self):
        with self.assertRaisesRegex(StatementValidationError, "Running balance"):
            parse_wise(wise_words(second_balance="501.00"))

    def test_adapter_chain_detects_wise(self):
        parsed = _parse_adapters(wise_words())
        self.assertEqual(parsed.institution, "Wise US Inc.")
        self.assertEqual(len(parsed.transactions), 4)


if __name__ == "__main__":
    unittest.main()
