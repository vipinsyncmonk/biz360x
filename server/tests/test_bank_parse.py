"""Bank narrations read into parts. The shapes are the ones from real statements (an ICICI-style NEFT with the remark before the beneficiary, UPI to a bare
mobile-number handle, a credit-card auto-debit, a transfer to a fixed deposit); the rest cover the other families."""
from app.bank_parse import parse_narration as p, words


def test_an_outward_neft_gives_the_remark_and_the_beneficiary():
    a = p("INF/NEFT/IN42609256186997/UTIB0001888/SalaryFY2526   /Vipin")
    assert (a.mode, a.reference, a.ifsc, a.remark, a.name, a.key) == ("neft", "IN42609256186997", "UTIB0001888", "SalaryFY2526", "Vipin", "vipin")
    b = p("INF/NEFT/IN42609256190107/SBIN0010366/March26Rent   /SUNDARESAN")
    assert (b.remark, b.name, b.ifsc) == ("March26Rent", "SUNDARESAN", "SBIN0010366")
    c = p("INF/NEFT/IN42609256057825/UTIB0001888/DirectorPayment/Yamini")
    assert (c.remark, c.name) == ("DirectorPayment", "Yamini")


def test_upi_to_a_bare_handle_has_no_name_but_a_stable_key():
    u = p("UPI/110204743611/UPI Pay/8792003390@ibl//ICI8d18597f26b645cb84742310b7fb5163/")
    assert (u.mode, u.reference, u.key, u.remark) == ("upi", "110204743611", "8792003390@ibl", None)         # "UPI Pay" is the bank's filler, not a remark
    h = p("UPI-SWIGGY-swiggy@icici-ICIC0000001-123456789012-Dinner")
    assert (h.mode, h.key, h.name, h.reference, h.remark) == ("upi", "swiggy@icici", "SWIGGY", "123456789012", "Dinner")


def test_a_card_auto_debit_and_a_transfer_to_a_deposit_are_not_expenses_and_say_so():
    a = p("ATD/Auto Debit CC1xx6441")
    assert (a.mode, a.key, a.remark) == ("auto_debit", "card:cc1xx6441", "credit card")
    assert p("ATD/Auto Debit CC1xx6441TRF TO FD no. 317113010027").key == "card:cc1xx6441"                       # text run on after the card number is not part of it
    t = p("TRF TO FD no. 317113010027")
    assert (t.mode, t.key, t.name) == ("transfer", "fd:317113010027", "FD 317113010027")


def test_the_other_families_and_the_fallback():
    assert (p("NEFT-AXOIN12345678-ACME TRADERS-HDFC0000001-INV 17").mode, p("NEFT-AXOIN12345678-ACME TRADERS-HDFC0000001-INV 17").name) == ("neft", "ACME TRADERS")
    assert p("MMT/IMPS/123456789012/rent/JOHN/HDFC").mode == "imps"
    assert p("RTGS/SBINR52024040300456/VENDOR PVT LTD").mode == "rtgs"
    assert p("ACH D- BAJAJ FINANCE-123456").mode == "auto_debit" and p("NACH/LIC OF INDIA/9988").key == "lic of india"
    assert p("POS 411111XXXXXX1111 AMAZON PAY").mode == "card" and p("ATM WDL 1234").mode == "cash"
    assert p("CHQ PAID 000123 MICR").mode == "cheque" and p("INT.PD:01-07-2026 to 30-09-2026").mode == "interest"
    assert p("SMS CHARGES Q2").mode == "charges" and p("GST ON CHARGES").mode == "charges"
    odd = p("BESCOM ELECTRICITY BILL 8801")
    assert odd.mode == "other" and odd.key.startswith("bescom electricity bill") and p("").mode == "other" and p(None).key is None


def test_words_split_camel_case_and_digits_so_a_keyword_matches_a_run_together_remark():
    assert words("SalaryFY2526") == ["salary", "fy", "2526"] and words("March26Rent") == ["march", "26", "rent"]
    assert words("DirectorPayment") == ["director", "payment"] and "rent" not in words("Current account")
    assert words(None) == []
