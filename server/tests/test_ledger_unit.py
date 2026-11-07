"""Ledger posting templates: pure functions, no database."""
import uuid
from decimal import Decimal as D

import pytest

from app import ledger

PARTY = uuid.uuid4()


def doc(**kw):
    base = {"doc_type": "tax_invoice", "party_id": PARTY, "number": "X/1", "taxable_total": D("10000.00"), "cgst_total": D(0), "sgst_total": D(0),
            "igst_total": D("1800.00"), "cess_total": D(0), "round_off": D(0), "grand_total": D("11800.00")}
    return {**base, **kw}


def by_key(lines):
    return {l.key: (l.debit, l.credit) for l in lines}


def test_inter_state_invoice():
    lines = ledger.sales_document_lines(doc())
    assert ledger.balanced(lines)
    assert by_key(lines) == {"receivable": (D("11800.00"), 0), "sales": (0, D("10000.00")), "gst_output_igst": (0, D("1800.00"))}
    assert lines[0].party_id == PARTY


def test_intra_state_invoice_splits_cgst_sgst():
    lines = ledger.sales_document_lines(doc(igst_total=D(0), cgst_total=D("900.00"), sgst_total=D("900.00")))
    k = by_key(lines)
    assert k["gst_output_cgst"] == (0, D("900.00")) and k["gst_output_sgst"] == (0, D("900.00")) and "gst_output_igst" not in k


@pytest.mark.parametrize("round_off,side", [(D("0.01"), "credit"), (D("-0.40"), "debit")])
def test_round_off_lands_on_the_correct_side(round_off, side):
    taxable, tax = D("999.99"), D("180.00")
    d = doc(taxable_total=taxable, igst_total=tax, round_off=round_off, grand_total=taxable + tax + round_off)
    lines = ledger.sales_document_lines(d)
    assert ledger.balanced(lines)
    ro = next(l for l in lines if l.key == "round_off")
    assert (ro.credit if side == "credit" else ro.debit) == abs(round_off)


def test_credit_note_mirrors_the_invoice():
    inv = by_key(ledger.sales_document_lines(doc()))
    cn = by_key(ledger.sales_document_lines(doc(doc_type="credit_note", taxable_total=D("2000.00"), igst_total=D("360.00"), grand_total=D("2360.00"))))
    assert cn == {"receivable": (0, D("2360.00")), "sales_returns": (D("2000.00"), 0), "gst_output_igst": (D("360.00"), 0)}
    assert set(inv) - {"sales"} == set(cn) - {"sales_returns"}


def test_commercial_credit_note_has_no_tax_lines():
    cn = ledger.sales_document_lines(doc(doc_type="credit_note", taxable_total=D("500.00"), igst_total=D(0), grand_total=D("500.00")))
    assert by_key(cn) == {"receivable": (0, D("500.00")), "sales_returns": (D("500.00"), 0)}


def test_credit_note_round_off_reverses():
    cn = ledger.sales_document_lines(doc(doc_type="credit_note", taxable_total=D("999.99"), igst_total=D("180.00"), round_off=D("0.01"), grand_total=D("1180.00")))
    assert ledger.balanced(cn) and next(l for l in cn if l.key == "round_off").debit == D("0.01")


def test_a_document_that_does_not_add_up_is_refused():
    with pytest.raises(ledger.PostingError):
        ledger.sales_document_lines(doc(grand_total=D("11800.01")))


def test_receipt_splits_allocated_and_advance():
    lines = ledger.receipt_lines(amount=D("5000"), allocated=D("3000"), party_id=PARTY, method="neft")
    assert ledger.balanced(lines)
    assert by_key(lines) == {"bank": (D("5000"), 0), "receivable": (0, D("3000")), "advances_received": (0, D("2000"))}


def test_receipt_variants():
    assert by_key(ledger.receipt_lines(amount=D("100"), allocated=D("100"), party_id=PARTY, method="cash")) == {"cash": (D("100"), 0), "receivable": (0, D("100"))}
    assert by_key(ledger.receipt_lines(amount=D("100"), allocated=D(0), party_id=PARTY, method="upi")) == {"bank": (D("100"), 0), "advances_received": (0, D("100"))}
    k = ledger.receipt_lines(amount=D("100"), allocated=D("100"), party_id=PARTY, method="neft", bank_account_key="account:" + str(PARTY))
    assert k[0].key == "account:" + str(PARTY)
    with pytest.raises(ledger.PostingError):
        ledger.receipt_lines(amount=D("100"), allocated=D("101"), party_id=PARTY, method="neft")


def test_balanced_rejects_empty_and_lopsided():
    assert not ledger.balanced([])
    assert not ledger.balanced([ledger.Line("bank", debit=D(1)), ledger.Line("sales", credit=D(2))])


def test_system_accounts_have_unique_codes_and_subtypes():
    codes = [v[0] for v in ledger.SYSTEM_ACCOUNTS.values()]
    subs = [v[3] for v in ledger.SYSTEM_ACCOUNTS.values()]
    assert len(set(codes)) == len(codes) and len(set(subs)) == len(subs)
    assert {l.key for doc_ in (doc(),) for l in ledger.sales_document_lines(doc_)} <= set(ledger.SYSTEM_ACCOUNTS)
