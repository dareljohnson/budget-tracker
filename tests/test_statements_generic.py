# tests/test_statements_generic.py
from budget_tracker.statements.generic import find_summary
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
