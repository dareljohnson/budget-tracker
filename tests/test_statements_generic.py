# tests/test_statements_generic.py
from budget_tracker.statements.generic import find_summary
from budget_tracker.statements.lines import group_lines
from budget_tracker.statements.model import Word

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
