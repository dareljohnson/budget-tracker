# tests/test_statements_generic.py
from datetime import date

import pytest

from budget_tracker.statements.generic import find_summary, find_transactions, parse_generic
from budget_tracker.statements.model import (
    StatementValidationError,
    UnsupportedStatementError,
    Word,
)

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
HEADER_ROLE = {"DATE": "date", "DESCRIPTION": "description", "DETAILS": "description",
               "TRANSACTION": "description", "REFERENCE": "description", "MEMO": "description",
               "AMOUNT": "amount", "DEBIT": "debit", "CREDIT": "credit", "BALANCE": "balance",
               "WITHDRAWAL": "debit", "WITHDRAWALS": "debit", "CHARGES": "debit",
               "PAYMENTS": "debit", "DEPOSIT": "credit", "DEPOSITS": "credit"}

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
    words += [Word("DEPOSITS", 20, 220)]
    words += [Word("09/20", 40, 240), Word("Refund", 130, 240), Word("25.00", 400, 240)]
    words += [Word("WITHDRAWALS", 20, 260)]
    words += [Word("09/21", 40, 280), Word("Rent", 130, 280), Word("100.00", 400, 280)]
    rows = find_transactions(words, anchor=date(2026, 9, 20), anchor_on_end=False)
    assert [r.direction for r in rows] == ["income", "expense"]

def test_totals_line_does_not_clear_the_section():
    words = [Word("DATE", 40, 200), Word("DESCRIPTION", 130, 200), Word("AMOUNT", 400, 200)]
    words += [Word("WITHDRAWALS", 20, 220)]
    words += [Word("09/20", 40, 240), Word("Rent", 130, 240), Word("100.00", 400, 240)]
    words += [Word("Total", 40, 260), Word("Deposits", 80, 260), Word("100.00", 400, 260)]
    words += [Word("09/21", 40, 280), Word("Rent", 130, 280), Word("100.00", 400, 280)]
    rows = find_transactions(words, anchor=date(2026, 9, 20), anchor_on_end=False)
    assert [r.direction for r in rows] == ["expense", "expense"]

def test_totals_line_does_not_set_the_section():
    words = table_words(["DATE", "DESCRIPTION", "AMOUNT"], [
        {"date": "09/20", "description": "Card Purchase", "amount": "40.00"},
    ])
    words += [Word("Total", 40, 250), Word("Withdrawals", 80, 250), Word("40.00", 400, 250)]
    words += [Word("09/21", 40, 270), Word("Another", 130, 270), Word("40.00", 400, 270)]
    rows = find_transactions(words, anchor=date(2026, 9, 20), anchor_on_end=False)
    assert [r.direction for r in rows] == ["", ""]

def test_wrapped_description_word_is_not_a_heading():
    words = table_words(["DATE", "DESCRIPTION", "AMOUNT"], [
        {"date": "09/20", "description": "Card Purchase", "amount": "40.00"},
        {"date": "09/21", "description": "Rent", "amount": "100.00"},
    ])
    words += [Word("WITHDRAWALS", 130, 227)]  # continuation at the description column
    rows = find_transactions(words, anchor=date(2026, 9, 20), anchor_on_end=False)
    assert rows[0].description == "Card Purchase WITHDRAWALS"
    assert [r.description for r in rows] == ["Card Purchase WITHDRAWALS", "Rent"]

def test_trailing_summary_block_is_not_appended():
    words = table_words(["DATE", "DESCRIPTION", "AMOUNT"], [
        {"date": "09/20", "description": "Card Purchase", "amount": "40.00"},
    ])
    words += [Word("DAILY", 130, 260), Word("BALANCE", 180, 260), Word("SUMMARY", 240, 260)]
    words += [Word("09/20", 40, 280), Word("1,000.00", 400, 280)]
    rows = find_transactions(words, anchor=date(2026, 9, 20), anchor_on_end=False)
    assert len(rows) == 1
    assert rows[0].description == "Card Purchase"

def test_yearless_date_without_anchor_is_rejected():
    words = table_words(
        ["DATE", "DESCRIPTION", "AMOUNT"],
        [{"date": "09/20", "description": "Fee", "amount": "5.00"}],
    )
    with pytest.raises(StatementValidationError, match="09/20"):
        find_transactions(words, anchor=None, anchor_on_end=False)

def test_full_statement_passes_and_parses():
    words = summary_words([
        ("Beginning Balance", "$1,000.00"), ("Total Deposits", "$500.00"),
        ("Total Withdrawals", "$40.00"), ("Ending Balance", "$1,460.00"),
    ]) + table_words(["DATE", "DESCRIPTION", "DEBIT", "CREDIT", "BALANCE"],
                     [{"date": "09/20/2026", "description": "Grocery", "debit": "40.00", "balance": "960.00"},
                      {"date": "09/25/2026", "description": "Payroll", "credit": "500.00", "balance": "1,460.00"}])
    stmt = parse_generic(words)
    assert stmt.institution == "Unknown bank"
    assert stmt.period_start == date(2026, 9, 20)
    assert stmt.ending_cents == 146_000
    assert [t.direction for t in stmt.transactions] == ["expense", "income"]

def test_missing_summary_figures_are_refused():
    words = summary_words([("Beginning Balance", "$1,000.00")]) + table_words(
        ["DATE", "DESCRIPTION", "AMOUNT"], [{"date": "09/20/2026", "description": "X", "amount": "1.00 CR"}])
    with pytest.raises(StatementValidationError) as ctx:
        parse_generic(words)
    assert any("deposits" in e.lower() for e in ctx.value.errors)

def test_totals_mismatch_is_refused():
    words = summary_words([
        ("Beginning Balance", "$1,000.00"), ("Total Deposits", "$500.00"),
        ("Total Withdrawals", "$41.00"), ("Ending Balance", "$1,460.00"),
    ]) + table_words(["DATE", "DESCRIPTION", "DEBIT", "CREDIT"], [
        {"date": "09/20/2026", "description": "Grocery", "debit": "40.00"},
        {"date": "09/25/2026", "description": "Payroll", "credit": "500.00"}])
    with pytest.raises(StatementValidationError):
        parse_generic(words)

def test_fees_inside_withdrawals_and_separate_both_accepted():
    inside = summary_words([("Beginning Balance", "$1,000.00"), ("Total Deposits", "$0.00"),
                            ("Total Withdrawals", "$40.00"), ("Fees", "$5.00"),
                            ("Ending Balance", "$960.00")]) + table_words(
        ["DATE", "DESCRIPTION", "DEBIT"], [{"date": "09/20/2026", "description": "Grocery", "debit": "40.00"}])
    assert parse_generic(inside).ending_cents == 96_000
    separate = summary_words([("Beginning Balance", "$1,000.00"), ("Total Deposits", "$0.00"),
                              ("Total Withdrawals", "$40.00"), ("Fees", "$5.00"),
                              ("Ending Balance", "$955.00")]) + table_words(
        ["DATE", "DESCRIPTION", "DEBIT"], [{"date": "09/20/2026", "description": "Grocery", "debit": "40.00"}])
    assert parse_generic(separate).ending_cents == 95_500

def test_credit_card_and_non_usd_are_unsupported():
    with pytest.raises(UnsupportedStatementError):
        parse_generic([Word("CREDIT CARD", 40, 40), Word("Minimum Payment Due", 40, 52)])
    with pytest.raises(UnsupportedStatementError):
        parse_generic([Word("Statement currency: EUR", 40, 40)])

def test_iso_code_among_header_words_is_unsupported():
    with pytest.raises(UnsupportedStatementError):
        parse_generic([Word("Account", 40, 40), Word("Type:", 100, 40), Word("GBP", 150, 40)])
    with pytest.raises(UnsupportedStatementError):
        parse_generic([Word("GBP", 40, 40), Word("Statement", 80, 40)])

def test_iso_code_in_a_complete_statement_is_unsupported():
    words = [Word("Account", 40, 40), Word("Type:", 100, 40), Word("GBP", 150, 40)]
    words += summary_words([
        ("Beginning Balance", "$1,000.00"), ("Total Deposits", "$0.00"),
        ("Total Withdrawals", "$40.00"), ("Ending Balance", "$960.00"),
    ])
    words += table_words(["DATE", "DESCRIPTION", "DEBIT"], [
        {"date": "09/20/2026", "description": "Grocery", "debit": "40.00"},
    ])
    with pytest.raises(UnsupportedStatementError, match="US dollar"):
        parse_generic(words)

def test_ambiguous_direction_is_refused():
    words = summary_words([("Beginning Balance", "$1,000.00"), ("Total Deposits", "$0.00"),
                           ("Total Withdrawals", "$40.00"), ("Ending Balance", "$960.00")]) + table_words(
        ["DATE", "DESCRIPTION", "AMOUNT"], [{"date": "09/20/2026", "description": "X", "amount": "40.00"}])
    with pytest.raises(StatementValidationError):
        parse_generic(words)

def test_card_number_is_not_used_as_account_last4():
    words = [Word("CHECKING", 40, 40), Word("CARD NUMBER", 40, 60),
             Word("2547-96574-2541-4004", 40, 72)] + summary_words([
        ("Beginning Balance", "$1,000.00"), ("Total Deposits", "$0.00"),
        ("Total Withdrawals", "$40.00"), ("Ending Balance", "$960.00")]) + table_words(
        ["DATE", "DESCRIPTION", "DEBIT"], [{"date": "09/20/2026", "description": "X", "debit": "40.00"}])
    assert parse_generic(words).account_last4 is None

def test_foreign_text_in_description_does_not_reject():
    words = summary_words([("Beginning Balance", "$1,000.00"), ("Total Deposits", "$0.00"),
                           ("Total Withdrawals", "$40.00"), ("Ending Balance", "$960.00")]) + table_words(
        ["DATE", "DESCRIPTION", "DEBIT"],
        [{"date": "09/20/2026", "description": "PURCHASE 25.00 EUR", "debit": "40.00"}])
    assert parse_generic(words).transactions[0].amount_cents == 4_000


def test_withdrawals_and_deposits_headers_are_debit_and_credit_columns():
    rows = find_transactions(table_words(
        ["DATE", "DESCRIPTION", "WITHDRAWALS", "DEPOSITS", "BALANCE"],
        [{"date": "09/20", "description": "Grocery", "debit": "40.00", "balance": "960.00"},
         {"date": "09/25", "description": "Payroll", "credit": "500.00", "balance": "1,460.00"}],
    ), anchor=date(2026, 9, 16), anchor_on_end=False)
    assert [(r.amount_cents, r.direction) for r in rows] == [(4_000, "expense"), (50_000, "income")]


@pytest.mark.parametrize("header", ["DETAILS", "TRANSACTION", "REFERENCE", "MEMO"])
def test_description_header_aliases_are_recognized(header):
    rows = find_transactions(table_words(
        ["DATE", header, "AMOUNT"],
        [{"date": "09/20", "description": "Grocery", "amount": "40.00 CR"}],
    ), anchor=date(2026, 9, 20), anchor_on_end=False)
    assert [r.description for r in rows] == ["Grocery"]
    assert rows[0].direction == "income"


@pytest.mark.parametrize(
    ("debit_header", "credit_header"),
    [("WITHDRAWAL", "DEPOSIT"), ("CHARGES", "DEPOSITS"), ("PAYMENTS", "DEPOSIT")],
)
def test_debit_and_credit_header_aliases_are_recognized(debit_header, credit_header):
    rows = find_transactions(table_words(
        ["DATE", "DESCRIPTION", debit_header, credit_header],
        [{"date": "09/20", "description": "Grocery", "debit": "40.00"},
         {"date": "09/25", "description": "Payroll", "credit": "500.00"}],
    ), anchor=date(2026, 9, 16), anchor_on_end=False)
    assert [(r.amount_cents, r.direction) for r in rows] == [(4_000, "expense"), (50_000, "income")]


def test_money_out_and_money_in_header_pair_is_a_debit_credit_pair():
    words = [Word("DATE", 40, 200), Word("DESCRIPTION", 130, 200),
             Word("MONEY", 390, 200), Word("OUT", 425, 200),
             Word("MONEY", 475, 200), Word("IN", 510, 200)]
    words += [Word("09/20", 40, 220), Word("Grocery", 130, 220), Word("40.00", 408, 220)]
    words += [Word("09/25", 40, 234), Word("Payroll", 130, 234), Word("500.00", 493, 234)]
    rows = find_transactions(words, anchor=date(2026, 9, 16), anchor_on_end=False)
    assert [(r.amount_cents, r.direction) for r in rows] == [(4_000, "expense"), (50_000, "income")]


@pytest.mark.parametrize(
    ("anchor", "anchor_on_end"),
    [(date(2025, 12, 16), False), (date(2026, 1, 15), True)],
    ids=["start-anchor", "end-anchor"],
)
@pytest.mark.parametrize(
    "date_tokens",
    [[("Jan", 30), ("05", 55)], [("January", 16), ("5,", 45), ("2026", 58)]],
    ids=["month-abbrev", "month-day-year"],
)
def test_multi_word_month_dates_start_a_row(anchor, anchor_on_end, date_tokens):
    words = [Word("DATE", 40, 200), Word("DESCRIPTION", 130, 200), Word("AMOUNT", 400, 200)]
    words += [Word(text, x, 220) for text, x in date_tokens]
    words += [Word("Grocery", 130, 220), Word("40.00", 400, 220)]
    rows = find_transactions(words, anchor=anchor, anchor_on_end=anchor_on_end)
    assert [(r.occurred_on, r.amount_cents) for r in rows] == [(date(2026, 1, 5), 4_000)]
    assert rows[0].description == "Grocery"


def test_many_date_column_tokens_do_not_form_a_row():
    words = [Word("DATE", 40, 200), Word("DESCRIPTION", 130, 200), Word("AMOUNT", 400, 200)]
    # Hundreds of non-date tokens fill the date column; the only real date is far to the right and
    # must not be reached by an unbounded sub-slice search.
    words += [Word("zz", 16 + (index % 49), 220) for index in range(300)]
    words += [Word("01/05/2026", 40, 220), Word("Grocery", 130, 220), Word("40.00", 400, 220)]
    rows = find_transactions(words, anchor=date(2026, 1, 15), anchor_on_end=False)
    assert rows == []
