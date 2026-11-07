"""M3 pure logic: TDS, advance tax, ITC utilisation, 2A/2B matching, purchase-bill journals. No database."""
import datetime as dt
import uuid
from decimal import Decimal as D

import pytest

from app import ledger, taxcalc as t

d = dt.date


# ------------------------------------------------------------------ TDS
@pytest.mark.parametrize("section,pan,rate", [("194C", True, 1), ("194J", True, 10), ("194Q", True, D("0.1")), ("194C", False, 20), ("194Q", False, 20)])
def test_tds_rate_and_no_pan_floor(section, pan, rate):
    assert t.tds_rate(section, pan) == D(rate)


def test_tds_amount_rounds_to_paise_and_unknown_section_raises():
    assert t.tds_amount(D("10000"), "194C", True) == (D("100.00"), D("1"))
    assert t.tds_amount(D("333.33"), "194J", True)[0] == D("33.33")
    with pytest.raises(ValueError):
        t.tds_rate("194ZZ", True)


@pytest.mark.parametrize("due,paid,months", [
    (d(2026, 11, 7), d(2026, 11, 7), 0), (d(2026, 11, 7), d(2026, 11, 1), 0), (d(2026, 11, 7), d(2026, 11, 8), 1),
    (d(2026, 11, 7), d(2026, 12, 7), 1), (d(2026, 11, 7), d(2026, 12, 8), 2), (d(2026, 11, 7), d(2027, 2, 20), 4),
    (d(2026, 12, 7), d(2027, 1, 6), 1)])
def test_months_late_counts_part_months(due, paid, months):
    assert t.months_late(due, paid) == months


def test_tds_interest():
    assert t.tds_late_interest(D("10000"), d(2026, 11, 7), d(2026, 12, 20)) == D("300.00")     # 2 months x 1.5% of 10 000
    assert t.tds_late_interest(D("10000"), d(2026, 11, 7), d(2026, 11, 7)) == 0


# ------------------------------------------------------------------ advance tax
def test_advance_tax_plan():
    plan = t.advance_tax_plan(D("120000"), 2026)
    assert [(p["instalment_no"], p["due_date"], p["cumulative_due"]) for p in plan] == [
        (1, d(2026, 6, 15), D("18000.00")), (2, d(2026, 9, 15), D("54000.00")), (3, d(2026, 12, 15), D("90000.00")), (4, d(2027, 3, 15), D("120000.00"))]
    assert t.advance_tax_plan(D(0), 2026)[3]["cumulative_due"] == 0


# ------------------------------------------------------------------ ITC utilisation
def util(out, itc):
    return t.utilise({"igst": D(out[0]), "cgst": D(out[1]), "sgst": D(out[2])}, {"igst": D(itc[0]), "cgst": D(itc[1]), "sgst": D(itc[2])})


def test_utilise_pays_the_balance_in_cash():
    u = util((1000, 500, 500), (700, 300, 100))
    assert u["cash"] == {"igst": D("300.00"), "cgst": D("200.00"), "sgst": D("400.00")} and u["left"] == {"igst": 0, "cgst": 0, "sgst": 0}


def test_igst_credit_spills_into_cgst_then_sgst():
    u = util((100, 200, 100), (500, 0, 0))
    assert u["cash"] == {"igst": 0, "cgst": 0, "sgst": 0} and u["left"]["igst"] == D("100.00")


def test_cgst_and_sgst_credits_never_cross_each_other():
    u = util((0, 0, 500), (0, 800, 0))
    assert u["cash"]["sgst"] == D("500.00") and u["left"]["cgst"] == D("800.00")              # CGST credit cannot pay SGST
    u = util((300, 0, 0), (0, 200, 200))
    assert u["cash"] == {"igst": 0, "cgst": 0, "sgst": 0} and u["left"] == {"igst": 0, "cgst": 0, "sgst": D("100.00")}      # both credits can pay IGST


def test_utilise_conserves_the_total():
    out, itc = (1234, 567, 890), (400, 700, 100)
    u = util(out, itc)
    assert sum(u["cash"].values()) + (sum(D(x) for x in itc) - sum(u["left"].values())) == sum(D(x) for x in out)


# ------------------------------------------------------------------ doc numbers and matching
@pytest.mark.parametrize("a,b", [("INV/0012", "inv-12"), ("INV 12", "INV12"), ("00045", "45"), ("A-007/26", "A7/26")])
def test_norm_doc_no_treats_formatting_as_noise(a, b):
    assert t.norm_doc_no(a) == t.norm_doc_no(b)


def test_norm_doc_no_keeps_real_differences():
    assert t.norm_doc_no("INV100") != t.norm_doc_no("INV10")
    assert t.norm_doc_no("INV-1") != t.norm_doc_no("INV-2")
    assert t.norm_doc_no("100") == "100" and t.norm_doc_no("0") == "0"


def book(i, gstin, no, tax):
    return t.Book(i, gstin, no, D(tax))


def portal(i, gstin, no, tax):
    return t.Portal(i, gstin, no, D(tax))


def test_matching_statuses():
    books = [book("b1", "G1", "INV/001", "180"), book("b2", "G1", "INV/002", "180"), book("b3", "G2", "X9", "50"), book("b4", "", "NOGST", "10")]
    port = [portal("p1", "G1", "inv-1", "180"), portal("p2", "G1", "INV-2", "150"), portal("p3", "G3", "ZZ", "99")]
    got = {(m["book"].id if m["book"] else None, m["portal"].id if m["portal"] else None): (m["status"], m["tax_difference"]) for m in t.match_inward(books, port)}
    assert got[("b1", "p1")] == ("matched", D("0.00"))
    assert got[("b2", "p2")] == ("amount_differs", D("30.00"))
    assert got[("b3", None)] == ("not_in_portal", D("50.00")) and got[("b4", None)][0] == "not_in_portal"
    assert got[(None, "p3")] == ("not_in_books", D("-99.00"))


def test_matching_tolerates_rounding_and_pairs_once():
    assert t.match_inward([book("b", "G", "1", "100.00")], [portal("p", "G", "1", "100.60")])[0]["status"] == "matched"
    assert t.match_inward([book("b", "G", "1", "100.00")], [portal("p", "G", "1", "102.00")])[0]["status"] == "amount_differs"
    res = t.match_inward([book("b1", "G", "1", "10"), book("b2", "G", "01", "10")], [portal("p", "G", "1", "10")])
    assert sorted(m["status"] for m in res) == ["matched", "not_in_portal"]                # one portal document cannot pay two bills


def test_every_status_has_a_finding_and_allowed_actions():
    assert set(t.FINDINGS) == set(t.ALLOWED_ACTIONS) == {"matched", "amount_differs", "not_in_portal", "not_in_books"}
    assert all(t.FINDINGS[s][1] in t.ALLOWED_ACTIONS[s] for s in t.FINDINGS)


# ------------------------------------------------------------------ purchase bill journals
V = uuid.uuid4()


def bill(**kw):
    base = {"bill_no": "B1", "party_id": V, "itc_eligibility": "eligible", "taxable_total": D("10000"), "cgst_total": D("900"), "sgst_total": D("900"),
            "igst_total": D(0), "grand_total": D("11800"), "tds_amount": D(0)}
    return {**base, **kw}


def keyed(lines):
    return {l.key: (l.debit, l.credit) for l in lines}


def test_eligible_bill_claims_input_gst():
    lines = ledger.purchase_bill_lines(bill())
    assert ledger.balanced(lines)
    assert keyed(lines) == {"purchases": (D("10000"), 0), "gst_input_cgst": (D("900"), 0), "gst_input_sgst": (D("900"), 0), "payable": (0, D("11800"))}


def test_ineligible_bill_costs_the_gst():
    lines = ledger.purchase_bill_lines(bill(itc_eligibility="blocked_17_5", cgst_total=D(0), sgst_total=D(0), igst_total=D("1800")))
    assert keyed(lines) == {"purchases": (D("11800"), 0), "payable": (0, D("11800"))}


def test_tds_is_withheld_from_the_payable():
    lines = ledger.purchase_bill_lines(bill(tds_amount=D("100")))
    assert ledger.balanced(lines)
    k = keyed(lines)
    assert k["payable"] == (0, D("11700")) and k["tds_payable"] == (0, D("100"))


def test_expense_split_and_mismatch():
    lines = ledger.purchase_bill_lines(bill(), [("account:" + str(V), D("6000")), ("purchases", D("4000"))])
    assert ledger.balanced(lines) and len([l for l in lines if l.debit and l.key.startswith("account:")]) == 1
    with pytest.raises(ledger.PostingError):
        ledger.purchase_bill_lines(bill(), [("purchases", D("9999"))])
    with pytest.raises(ledger.PostingError):
        ledger.purchase_bill_lines(bill(grand_total=D("11801")))


def test_vendor_payment_template():
    lines = ledger.vendor_payment_lines(amount=D("500"), party_id=V, bank_account_key="account:x")
    assert ledger.balanced(lines) and keyed(lines) == {"payable": (D("500"), 0), "account:x": (0, D("500"))}
