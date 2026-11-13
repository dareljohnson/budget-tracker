from budget_tracker.statements import labels


def test_families_by_exact_alias():
    assert labels.family_of("Beginning Balance $1,000.00") == "beginning"
    assert labels.family_of("Deposits & Credits $500.00") == "deposits"
    assert labels.family_of("Total Withdrawals $40.00") == "withdrawals"
    assert labels.family_of("Total service charges $5.00") == "fees"
    assert labels.family_of("Your new balance $1,460.00") == "ending"


def test_family_by_fuzzy_variant():
    assert labels.family_of("Your previous balance as of 05/18/2020 $2,074.21") == "beginning"
    assert labels.family_of("Depostis and credits $5,834.32") == "deposits"  # typo tolerated


def test_family_by_fuzzy_only_variant():
    assert labels.family_of("Begining balance $1,000.00") == "beginning"


def test_non_flow_labels_excluded():
    assert labels.family_of("Average Posted Balance in Statement Cycle $1,048.00") is None
    assert labels.family_of("Number of days in cycle 31") is None


def test_detail_classification():
    assert labels.detail_family("Checks $701.39") == "withdrawals"
    assert labels.detail_family("Other withdrawals, debits and service charges $6,242.34") == "withdrawals"
    assert labels.detail_family("Deposits, credits and interest $5,834.32") == "deposits"


def test_institution_aliases():
    assert labels.institution_of("JPMORGAN CHASE BANK, N.A.") == "Chase"
    assert labels.institution_of("Truist Bank, Member FDIC") == "Truist"
    assert labels.institution_of("Georgia's Own Credit Union") == "Georgia's Own"
    assert labels.institution_of("BNI") == "BNI"
    assert labels.institution_of("Acme Savings") is None


def test_rejection_markers():
    assert labels.is_credit_card_marker("Minimum payment due $25.00")
    assert not labels.is_credit_card_marker("POS DEBIT VISA 1234")
    assert labels.has_non_usd_marker("Statement currency: EUR")
    assert not labels.has_non_usd_marker("PURCHASE 25.00 EUR")


def test_label_text_strips_every_amount_and_date_token():
    assert labels.label_text("Your previous balance as of 05/18/2020 $2,074.21") == "Your previous balance as of"
