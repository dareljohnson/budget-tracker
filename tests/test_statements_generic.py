# tests/test_statements_generic.py
from datetime import date

import pytest

from budget_tracker.statements.generic import find_summary, find_transactions
from budget_tracker.statements.model import StatementValidationError, Word

def summary_words(rows, top=100):
    words = []
    for i, (label, amount) in enumerate(rows):
        y = top + i * 12
        x = 40
        for token in label.split():
            words.append(Word(token, x, y)); x += 7 * len(token) + 3
        words.append(Word(amount, 500, y))
    return words

def test_simple_summary():
    figures = find_summary(summary_words([
        ("Beginning Balance", "$1,000.00"), ("Total Deposits", "$500.00"),
        ("Total Withdrawals", "$40.00"), ("Ending Balance", "$1,460.00"),
    ]), 500)
    assert (figures.beginning_cents, figures.deposits_cents, figures.withdrawals_cents,
            figures.fees_cents, figures.ending_cents) == (100_000, 50_000, 4_000, None, 146_000)

def test_decomposed_summary_sums_detail_lines():
    figures = find_summary(summary_words([
        ("Your previous balance as of 05/18/2020", "$2,074.21"),
        ("Checks", "$701.39"),
        ("Other withdrawals, debits and service charges", "$6,242.34"),
        ("Deposits, credits and interest", "$5,834.32"),
        ("Your new balance as of 06/17/2020", "$964.80"),
    ]), 500)
    assert (figures.beginning_cents, figures.deposits_cents,
            figures.withdrawals_cents, figures.ending_cents) == (207_421, 583_432, 694_373, 96_480)

def test_non_flow_figure_not_summed_into_withdrawals():
    figures = find_summary(summary_words([
        ("Beginning Balance", "$1,000.00"), ("Total Withdrawals", "$40.00"),
        ("Average Posted Balance in Statement Cycle", "$1,048.00"),
        ("Ending Balance", "$1,460.00"),
    ]), 500)
    assert figures.withdrawals_cents == 4_000

def test_next_line_amount_only_continuation():
    words = [Word("Beginning", 40, 100), Word("Balance", 90, 100),
             Word("$1,000.00", 500, 112)]
    assert find_summary(words, 500).beginning_cents == 100_000

def test_next_line_with_label_is_not_stolen():
    words = [Word("Total", 40, 100), Word("Deposits", 80, 100),
             Word("Total", 40, 112), Word("Withdrawals", 80, 112), Word("$40.00", 500, 112)]
    figures = find_summary(words, 500)
    assert figures.deposits_cents is None
    assert figures.withdrawals_cents == 4_000

def test_wrapped_detail_label_counts_once():
    words = [Word("Other", 40, 100), Word("withdrawals,", 80, 100),
             Word("debits", 160, 100), Word("and", 200, 100),
             Word("service", 40, 112), Word("charges", 90, 112), Word("$6,242.34", 500, 112)]
    assert find_summary(words, 500).withdrawals_cents == 624_234

COLUMN_X = {"date": 40, "description": 130, "amount": 400, "debit": 400,
            "credit": 470, "balance": 540}
HEADER_ROLE = {"DATE": "date", "DESCRIPTION": "description", "AMOUNT": "amount",
               "DEBIT": "debit", "CREDIT": "credit", "BALANCE": "balance"}

def table_words(header, rows, top=200):
    words = [Word(name, COLUMN_X[HEADER_ROLE[name]], top) for name in header]
    for i, row in enumerate(rows):
        y = top + 20 + i * 14
        words.append(Word(row["date"], COLUMN_X["date"], y))
        dx = COLUMN_X["description"]
        for token in row["description"].split():
            words.append(Word(token, dx, y)); dx += 7 * len(token) + 3
        for column in ("amount", "debit", "credit", "balance"):
            if column in row:
                words.append(Word(row[column], COLUMN_X[column], y))
    return words

def test_debit_credit_columns():
    rows = find_transactions(table_words(
        ["DATE", "DESCRIPTION", "DEBIT", "CREDIT", "BALANCE"],
        [{"date": "09/20", "description": "Grocery", "debit": "40.00", "balance": "960.00"},
         {"date": "09/25", "description": "Payroll", "credit": "500.00", "balance": "1,460.00"}],
    ), anchor=date(2026, 9, 16), anchor_on_end=False)
    assert [(r.occurred_on, r.amount_cents, r.direction, r.balance_cents) for r in rows] == [
        (date(2026, 9, 20), 4_000, "expense", 96_000),
        (date(2026, 9, 25), 50_000, "income", 146_000),
    ]

def test_unsigned_single_amount_uses_balance_delta():
    rows = find_transactions(table_words(
        ["DATE", "DESCRIPTION", "AMOUNT", "BALANCE"],
        [{"date": "09/20", "description": "Grocery", "amount": "40.00", "balance": "960.00"},
         {"date": "09/25", "description": "Payroll", "amount": "500.00", "balance": "1,460.00"}],
    ), anchor=date(2026, 9, 16), anchor_on_end=False, beginning_cents=100_000)
    assert [r.direction for r in rows] == ["expense", "income"]

def test_continuation_line_and_repeated_header():
    words = table_words(
        ["DATE", "DESCRIPTION", "DEBIT", "CREDIT", "BALANCE"],
        [{"date": "09/20", "description": "Card Purchase", "debit": "40.00", "balance": "960.00"}],
    )
    words += [Word("Merchant Name", 130, 236)]                # continuation
    words += [Word("DATE", 40, 260), Word("DESCRIPTION", 130, 260),
              Word("DEBIT", 400, 260), Word("CREDIT", 470, 260), Word("BALANCE", 540, 260)]
    words += [Word("09/21", 40, 280), Word("Rent", 130, 280), Word("100.00", 400, 280),
              Word("860.00", 540, 280)]
    rows = find_transactions(words, anchor=date(2026, 9, 20), anchor_on_end=False)
    assert [r.description for r in rows] == ["Card Purchase Merchant Name", "Rent"]
    assert [r.amount_cents for r in rows] == [4_000, 10_000]

def test_parenthesized_amount_and_credit_marker():
    rows = find_transactions(table_words(
        ["DATE", "DESCRIPTION", "AMOUNT"],
        [{"date": "09/20", "description": "Fee", "amount": "(5.00)"},
         {"date": "09/21", "description": "Refund", "amount": "5.00 CR"}],
    ), anchor=date(2026, 9, 20), anchor_on_end=False)
    assert [(r.amount_cents, r.direction) for r in rows] == [(500, "expense"), (500, "income")]

def test_page_footer_and_totals_row_are_not_rows():
    words = table_words(
        ["DATE", "DESCRIPTION", "AMOUNT", "BALANCE"],
        [{"date": "09/20", "description": "Grocery", "amount": "40.00", "balance": "960.00"}],
    )
    words += [Word("Page", 40, 240), Word("1", 75, 240), Word("of", 90, 240), Word("2", 110, 240)]
    words += [Word("Total", 130, 260), Word("Withdrawals", 170, 260), Word("40.00", 400, 260)]
    words += [Word("09/21", 40, 280), Word("Rent", 130, 280), Word("100.00", 400, 280),
              Word("860.00", 540, 280)]
    rows = find_transactions(words, anchor=date(2026, 9, 20), anchor_on_end=False,
                             beginning_cents=100_000)
    assert [r.description for r in rows] == ["Grocery", "Rent"]

def test_section_heading_supplies_direction():
    words = [Word("DATE", 40, 200), Word("DESCRIPTION", 130, 200), Word("AMOUNT", 400, 200)]
    words += [Word("DEPOSITS", 130, 220)]
    words += [Word("09/20", 40, 240), Word("Refund", 130, 240), Word("25.00", 400, 240)]
    words += [Word("WITHDRAWALS", 130, 260)]
    words += [Word("09/21", 40, 280), Word("Rent", 130, 280), Word("100.00", 400, 280)]
    rows = find_transactions(words, anchor=date(2026, 9, 20), anchor_on_end=False)
    assert [r.direction for r in rows] == ["income", "expense"]

def test_yearless_date_without_anchor_is_rejected():
    words = table_words(
        ["DATE", "DESCRIPTION", "AMOUNT"],
        [{"date": "09/20", "description": "Fee", "amount": "5.00"}],
    )
    with pytest.raises(StatementValidationError, match="09/20"):
        find_transactions(words, anchor=None, anchor_on_end=False)
