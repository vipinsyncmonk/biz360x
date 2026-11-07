"""Pure tax calculations for M3: TDS rates and interest, GST ITC utilisation, advance-tax plan, and the
GSTR-2A/2B matcher. No database access, so every rule is unit-tested on its own.

All rates and rules are a starter library: a CA must review them before they drive a filing.
"""
from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

ZERO = Decimal(0)
TWO = Decimal("0.01")


def r2(x: Decimal) -> Decimal:
    return Decimal(x).quantize(TWO, rounding=ROUND_HALF_UP)


# --------------------------------------------------------------------------- TDS
# section -> rate in percent (resident payees; payee has a PAN)
TDS_RATES = {"194C": Decimal("1"), "194H": Decimal("5"), "194I": Decimal("10"), "194J": Decimal("10"), "194Q": Decimal("0.1"),
             "194A": Decimal("10"), "194-O": Decimal("0.1")}
NO_PAN_RATE = Decimal("20")                                  # Sec 206AA: at least 20% when the payee gives no PAN


def tds_rate(section: str, has_pan: bool) -> Decimal:
    if section not in TDS_RATES:
        raise ValueError(f"unknown TDS section {section!r}")
    return max(TDS_RATES[section], NO_PAN_RATE) if not has_pan else TDS_RATES[section]


def tds_amount(base: Decimal, section: str, has_pan: bool) -> tuple[Decimal, Decimal]:
    rate = tds_rate(section, has_pan)
    return r2(Decimal(base) * rate / 100), rate


def months_late(due: dt.date, paid: dt.date) -> int:
    """Interest under 201(1A) runs per month or part of a month from the date of deduction/due date."""
    if paid <= due:
        return 0
    m = (paid.year - due.year) * 12 + paid.month - due.month
    return m + (1 if paid.day > due.day else 0)


def tds_late_interest(amount: Decimal, due: dt.date, paid: dt.date, pct_per_month: Decimal = Decimal("1.5")) -> Decimal:
    return r2(Decimal(amount) * pct_per_month / 100 * months_late(due, paid))


# ----------------------------------------------------------------------- advance tax
ADVANCE_TAX_SCHEDULE = [(1, (6, 15), Decimal("15")), (2, (9, 15), Decimal("45")), (3, (12, 15), Decimal("75")), (4, (3, 15), Decimal("100"))]


def advance_tax_plan(estimated_tax: Decimal, fy_start_year: int) -> list[dict]:
    out = []
    for no, (m, d), pct in ADVANCE_TAX_SCHEDULE:
        year = fy_start_year + (1 if m < 4 else 0)
        out.append({"instalment_no": no, "due_date": dt.date(year, m, d), "cumulative_pct": pct,
                    "cumulative_due": r2(Decimal(estimated_tax) * pct / 100)})
    return out


# ----------------------------------------------------------------------------- GST ITC utilisation
HEADS = ("igst", "cgst", "sgst")


def utilise(output: dict[str, Decimal], itc: dict[str, Decimal]) -> dict:
    """Set ITC off against output tax in the order the law requires (Sec 49A/49B, Rule 88A):
    IGST credit -> IGST, then CGST, then SGST; CGST credit -> CGST, then IGST; SGST credit -> SGST, then IGST.
    Returns {"cash": {head: payable}, "left": {head: unused credit}}."""
    out = {h: Decimal(output.get(h, 0)) for h in HEADS}
    cr = {h: Decimal(itc.get(h, 0)) for h in HEADS}
    order = {"igst": ("igst", "cgst", "sgst"), "cgst": ("cgst", "igst"), "sgst": ("sgst", "igst")}

    def spend(credit_head: str, targets) -> None:
        for t in targets:
            use = min(cr[credit_head], out[t])
            cr[credit_head] -= use
            out[t] -= use

    # Rule 88A: IGST credit is exhausted first, then CGST and SGST credits against what is left
    spend("igst", order["igst"])
    spend("cgst", order["cgst"])
    spend("sgst", order["sgst"])
    return {"cash": {h: r2(out[h]) for h in HEADS}, "left": {h: r2(cr[h]) for h in HEADS}}


# --------------------------------------------------------------------------- 2A / 2B matching
def norm_doc_no(s: str) -> str:
    """'INV/0012', 'inv-12' and 'INV 12' are the same invoice: upper-case, drop punctuation and leading zeros per run of digits."""
    s = re.sub(r"[^A-Z0-9]", "", (s or "").upper())
    return re.sub(r"(?<![0-9])0+(?=[0-9])", "", s)


@dataclass
class Book:
    id: str
    gstin: str
    number: str
    tax: Decimal
    party_id: str | None = None
    name: str | None = None


@dataclass
class Portal:
    id: str
    gstin: str
    number: str
    tax: Decimal
    name: str | None = None


def match_inward(books: list[Book], portal: list[Portal], tolerance: Decimal = Decimal("1.00")) -> list[dict]:
    """Pair purchase bills with supplier-reported documents by (GSTIN, normalised number).
    Returns one dict per pair/orphan with status matched | amount_differs | not_in_portal | not_in_books."""
    index = {(p.gstin, norm_doc_no(p.number)): p for p in portal}
    used: set[str] = set()
    out = []
    for b in books:
        p = index.get((b.gstin, norm_doc_no(b.number))) if b.gstin else None
        if p and p.id not in used:
            used.add(p.id)
            diff = r2(b.tax - p.tax)
            out.append({"status": "matched" if abs(diff) <= tolerance else "amount_differs", "book": b, "portal": p, "tax_difference": diff})
        else:
            out.append({"status": "not_in_portal", "book": b, "portal": None, "tax_difference": r2(b.tax)})
    for p in portal:
        if p.id not in used:
            out.append({"status": "not_in_books", "book": None, "portal": p, "tax_difference": r2(-p.tax)})
    return out


FINDINGS = {
    "matched": ("Matches the supplier's filing.", "claim"),
    "amount_differs": ("Tax differs from what the supplier reported.", "claim_lower"),
    "not_in_portal": ("The supplier has not reported this invoice.", "remind_supplier"),
    "not_in_books": ("The supplier reported an invoice you have not booked.", "book_bill"),
}
ALLOWED_ACTIONS = {
    "matched": {"claim", "defer"},
    "amount_differs": {"claim_lower", "ask_to_amend", "defer", "investigate"},
    "not_in_portal": {"remind_supplier", "defer", "reject"},
    "not_in_books": {"book_bill", "investigate", "reject"},
}
