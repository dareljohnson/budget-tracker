import unittest
from datetime import date, timedelta

from budget_tracker.matching import match_candidates, source_label
from budget_tracker.models import Transaction, TransactionType

ACCOUNT = 1
TARGET = date(2026, 9, 19)


def tx(
    amount,
    day,
    description,
    *,
    type_=TransactionType.EXPENSE,
    account=ACCOUNT,
    transfer=None,
    source="manual",
    tx_id=1,
):
    return Transaction(
        amount,
        type_,
        1,
        TARGET + timedelta(days=day),
        description,
        id=tx_id,
        account_id=account,
        transfer_account_id=transfer,
        source=source,
    )


class TestMatchCandidates(unittest.TestCase):
    def test_exact_amount_and_date_is_strong(self):
        [candidate] = match_candidates(
            [tx(3485, 0, "FEL LINIS PIZZA")],
            account_id=ACCOUNT,
            amount_cents=3485,
            occurred_on=TARGET,
            type_=TransactionType.EXPENSE,
            description="FEL LINIS PIZZA",
        )
        self.assertGreaterEqual(candidate.score, 85)
        self.assertEqual(candidate.date_delta, 0)
        self.assertIn("same amount", candidate.reasons)
        self.assertIn("same date", candidate.reasons)

    def test_description_changes_rank_but_do_not_hide(self):
        candidates = match_candidates(
            [tx(3485, 0, "FEL LINIS PIZZA", tx_id=1), tx(3485, 0, "TOTALLY UNRELATED", tx_id=2)],
            account_id=ACCOUNT,
            amount_cents=3485,
            occurred_on=TARGET,
            type_=TransactionType.EXPENSE,
            description="FEL LINIS PIZZA",
        )
        self.assertEqual(len(candidates), 2)
        self.assertGreater(candidates[0].score, candidates[1].score)

    def test_supplied_description_ranks_the_matching_merchant_first(self):
        candidates = match_candidates(
            [tx(3485, 0, "TOTALLY UNRELATED", tx_id=1), tx(3485, 0, "FEL LINIS PIZZA", tx_id=2)],
            account_id=ACCOUNT,
            amount_cents=3485,
            occurred_on=TARGET,
            type_=TransactionType.EXPENSE,
            description="FEL LINIS PIZZA",
        )
        self.assertEqual(candidates[0].transaction.id, 2)
        self.assertGreater(candidates[0].score, candidates[1].score)
        self.assertIn("similar merchant", candidates[0].reasons)

    def test_equal_scores_break_by_date_delta_then_id(self):
        closer = tx(3485, 0, "FEL LIN", tx_id=1)
        farther = tx(3485, 1, "FEL LINIS PIZZA", tx_id=2)
        candidates = match_candidates(
            [farther, closer],
            account_id=ACCOUNT,
            amount_cents=3485,
            occurred_on=TARGET,
            type_=TransactionType.EXPENSE,
            description="FEL LINIS PIZZA",
        )
        self.assertEqual(candidates[0].score, candidates[1].score)
        self.assertEqual([candidate.transaction for candidate in candidates], [closer, farther])

        tied_ids = match_candidates(
            [tx(3485, 0, "FEL LINIS PIZZA", tx_id=2), tx(3485, 0, "FEL LINIS PIZZA", tx_id=1)],
            account_id=ACCOUNT,
            amount_cents=3485,
            occurred_on=TARGET,
            type_=TransactionType.EXPENSE,
            description="FEL LINIS PIZZA",
        )
        self.assertEqual([candidate.transaction.id for candidate in tied_ids], [1, 2])

    def test_amount_account_type_and_window_are_hard_filters(self):
        rows = [
            tx(3484, 0, "ONE CENT OFF"),
            tx(3485, 0, "OTHER ACCOUNT", account=2),
            tx(3485, 0, "WRONG TYPE", type_=TransactionType.INCOME),
            tx(3485, 8, "TOO LATE"),
        ]
        self.assertEqual(
            match_candidates(
                rows,
                account_id=ACCOUNT,
                amount_cents=3485,
                occurred_on=TARGET,
                type_=TransactionType.EXPENSE,
                description="FEL LINIS PIZZA",
            ),
            [],
        )

    def test_transfer_matches_from_either_side_and_destination(self):
        outgoing = tx(5000, 0, "PAY CARD", type_=TransactionType.TRANSFER, account=1, transfer=2)
        incoming = tx(5000, 0, "CARD PAYMENT", type_=TransactionType.TRANSFER, account=2, transfer=1)
        self.assertEqual(
            len(
                match_candidates(
                    [outgoing, incoming],
                    account_id=1,
                    amount_cents=5000,
                    occurred_on=TARGET,
                    type_=TransactionType.TRANSFER,
                    description="PAY CARD",
                )
            ),
            2,
        )
        card = match_candidates(
            [outgoing, incoming],
            account_id=1,
            amount_cents=5000,
            occurred_on=TARGET,
            type_=TransactionType.TRANSFER,
            description="PAY CARD",
            transfer_account_id=2,
        )
        self.assertEqual([candidate.transaction for candidate in card], [outgoing])

    def test_results_are_capped_at_five(self):
        rows = [tx(3485, 0, f"MERCHANT {index}", tx_id=index) for index in range(1, 8)]
        candidates = match_candidates(
            rows,
            account_id=ACCOUNT,
            amount_cents=3485,
            occurred_on=TARGET,
            type_=TransactionType.EXPENSE,
            description="MERCHANT",
        )
        self.assertEqual(len(candidates), 5)


class TestSourceLabels(unittest.TestCase):
    def test_source_labels(self):
        self.assertEqual(source_label("manual"), "Added manually")
        self.assertEqual(source_label("import"), "Imported")
        self.assertEqual(source_label("receipt"), "Receipt")
        self.assertEqual(source_label("bill"), "Bill payment")

    def test_unknown_source_returns_the_raw_value(self):
        self.assertEqual(source_label("other"), "other")


if __name__ == "__main__":
    unittest.main()
