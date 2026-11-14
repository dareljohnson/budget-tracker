from datetime import date
import unittest

from budget_tracker.statements.model import Word
from budget_tracker.receipts.parser import parse_receipt

TODAY = date(2026, 11, 14)


def words(*lines: str) -> list[Word]:
    return [
        Word(token, x0=i * 40.0, top=line_no * 20.0)
        for line_no, line in enumerate(lines)
        for i, token in enumerate(line.split())
    ]


def draft(*lines: str):
    return parse_receipt(words(*lines), today=TODAY)


class TestReceiptParser(unittest.TestCase):
    def test_reads_total_merchant_and_date(self):
        result = draft(
            "CORNER MARKET",
            "123 MAIN ST",
            "10/28/2026",
            "MILK 3.49",
            "BREAD 2.50",
            "SUBTOTAL 5.99",
            "TAX 0.48",
            "TOTAL $6.47",
        )
        self.assertEqual(result.total_cents, 647)
        self.assertEqual(result.merchant, "CORNER MARKET")
        self.assertEqual(result.occurred_on, date(2026, 10, 28))
        self.assertEqual(result.missing, frozenset())

    def test_grand_total_wins_over_earlier_total_line(self):
        result = draft("SHOP", "TOTAL 5.00", "GRAND TOTAL 7.25")
        self.assertEqual(result.total_cents, 725)

    def test_whole_dollar_total_is_accepted(self):
        self.assertEqual(draft("SHOP", "TOTAL 20").total_cents, 2000)

    def test_missing_total_is_flagged_not_invented(self):
        result = draft("SHOP", "MILK 3.49", "BREAD 2.50")
        self.assertIsNone(result.total_cents)
        self.assertIn("total", result.missing)

    def test_month_name_date(self):
        self.assertEqual(draft("SHOP", "October 28, 2026", "TOTAL 1.00").occurred_on, date(2026, 10, 28))

    def test_iso_date(self):
        self.assertEqual(draft("SHOP", "2026-10-28", "TOTAL 1.00").occurred_on, date(2026, 10, 28))

    def test_date_defaults_to_today(self):
        self.assertEqual(draft("SHOP", "TOTAL 1.00").occurred_on, TODAY)

    def test_card_masks(self):
        self.assertEqual(draft("SHOP", "VISA **** 1234", "TOTAL 1.00").card_last4, "1234")
        self.assertEqual(draft("SHOP", "XXXX-4321", "TOTAL 1.00").card_last4, "4321")
        self.assertEqual(draft("SHOP", "ending in 9876", "TOTAL 1.00").card_last4, "9876")

    def test_full_card_number(self):
        result = draft("SHOP", "4111 1111 1111 1111", "TOTAL 1.00")
        self.assertEqual(result.card_last4, "1111")

    def test_cash_receipt(self):
        result = draft("SHOP", "CASH 6.47", "TOTAL 6.47")
        self.assertTrue(result.paid_cash)
        self.assertIsNone(result.card_last4)

    def test_merchant_skips_header_address_and_phone(self):
        result = draft("STORE #123", "123 MAIN ST", "PHONE 555-123-4567", "CORNER MARKET", "TOTAL 5.00")
        self.assertEqual(result.merchant, "CORNER MARKET")

    def test_empty_ocr_text(self):
        result = parse_receipt([], today=TODAY)
        self.assertEqual(result.merchant, "")
        self.assertIsNone(result.total_cents)
        self.assertEqual(result.occurred_on, TODAY)
        self.assertEqual(result.missing, frozenset({"merchant", "total"}))
        self.assertEqual(result.raw_text, "")
