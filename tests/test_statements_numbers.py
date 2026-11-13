from datetime import date
from budget_tracker.statements.numbers import parse_amount, parse_date
from budget_tracker.statements.regions_checking import _cents

def test_amounts():
    assert parse_amount("$1,234.56").cents == 123_456
    assert parse_amount("(1,234.56)").cents == -123_456
    assert parse_amount("1,234.56-").cents == -123_456
    assert parse_amount("500.00 CR").marker == "CR"
    assert parse_amount("500.00 DR").marker == "DR"
    assert parse_amount("Page 1 of 5") is None

def test_leading_minus_amount():
    assert parse_amount("-500.00").cents == -50_000
    assert parse_amount("$-500.00").cents == -50_000

def test_regions_cents_accepts_leading_minus():
    assert _cents("-500.00") == -50_000

def test_dates_anchor_on_start():
    assert parse_date("09/20", date(2026, 9, 16)) == date(2026, 9, 20)
    assert parse_date("01/03", date(2025, 12, 16)) == date(2026, 1, 3)
    assert parse_date("09/20/2024", date(2026, 9, 16)) == date(2024, 9, 20)
    assert parse_date("Sep 20", date(2026, 9, 16)) == date(2026, 9, 20)
    assert parse_date("September 20, 2026", date(2026, 9, 16)) == date(2026, 9, 20)

def test_dates_anchor_on_end():
    assert parse_date("01/03", date(2026, 1, 15), anchor_on_end=True) == date(2026, 1, 3)
    assert parse_date("12/28", date(2026, 1, 15), anchor_on_end=True) == date(2025, 12, 28)
