"""Opening position from the company's earlier books: checks on the rows a person uploads, with no database access.

Three kinds of rows: an opening trial balance, the sales invoices still open on the cut-off date, and the purchase bills still open on it.
Every function returns the cleaned rows and a list of problems ({row, field, message}); nothing is saved until the list is empty.
Money is Decimal throughout and leaves as a two-decimal string.
"""
from __future__ import annotations

import datetime as dt
import re
from decimal import Decimal, InvalidOperation

ZERO = Decimal("0.00")
TYPES = ("asset", "liability", "equity", "income", "expense")
GSTIN = re.compile(r"^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][1-9A-Z]Z[0-9A-Z]$")
STATE = re.compile(r"^[0-9]{2}$")
Problem = dict


def d2(v) -> Decimal | None:
    """A money cell as Decimal with two places, None when empty; raises ValueError on junk or more than two decimals."""
    if v is None or str(v).strip() == "":
        return None
    s = str(v).strip().replace(",", "")
    try:
        x = Decimal(s)
    except InvalidOperation:
        raise ValueError(f"{v!r} is not an amount")
    if x != x.quantize(ZERO):
        raise ValueError(f"{v!r} has more than two decimals")
    return x.quantize(ZERO)


def money(x: Decimal) -> str:
    return f"{x:.2f}"


class _Rows:
    def __init__(self):
        self.problems: list[Problem] = []

    def bad(self, n: int, field: str | None, message: str):
        self.problems.append({"row": n, "field": field, "message": message})

    def text(self, n, row, field, *, required=False, maxlen=200) -> str | None:
        v = str(row.get(field) or "").strip()
        if not v:
            if required:
                self.bad(n, field, f"{field} is required")
            return None
        if len(v) > maxlen:
            self.bad(n, field, f"{field} is longer than {maxlen} characters")
        return v

    def amount(self, n, row, field, *, required=False, default=None, negative=False) -> Decimal | None:
        try:
            v = d2(row.get(field))
        except ValueError as e:
            self.bad(n, field, f"{field}: {e}")
            return None
        if v is None:
            if required:
                self.bad(n, field, f"{field} is required")
            return default
        if v < 0 and not negative:
            self.bad(n, field, f"{field} cannot be negative")
        return v

    def date(self, n, row, field, *, required=False, today: dt.date) -> dt.date | None:
        v = str(row.get(field) or "").strip()
        if not v:
            if required:
                self.bad(n, field, f"{field} is required (YYYY-MM-DD)")
            return None
        try:
            x = dt.date.fromisoformat(v)
        except ValueError:
            self.bad(n, field, f"{field} must be a date as YYYY-MM-DD, got {v!r}")
            return None
        if x > today:
            self.bad(n, field, f"{field} {v} is in the future")
        return x

    def gstin(self, n, row, field) -> str | None:
        v = str(row.get(field) or "").strip().upper()
        if not v:
            return None
        if not GSTIN.match(v):
            self.bad(n, field, f"{field} {v} is not a valid GSTIN shape")
            return None
        return v

    def state(self, n, row, field, default: str | None) -> str | None:
        v = str(row.get(field) or "").strip()
        if not v:
            return default
        if not STATE.match(v):
            self.bad(n, field, f"{field} must be the two-digit GST state code, for example 29")
            return default
        return v


def _tax(r: _Rows, n, row, inter_state: bool | None):
    """taxable, the three taxes and the total, checked against each other. Returns the figures or None."""
    taxable = r.amount(n, row, "taxable", required=True)
    cgst = r.amount(n, row, "cgst", default=ZERO)
    sgst = r.amount(n, row, "sgst", default=ZERO)
    igst = r.amount(n, row, "igst", default=ZERO)
    total = r.amount(n, row, "total", required=True)
    if None in (taxable, cgst, sgst, igst, total):
        return None
    if inter_state is True and (cgst or sgst):
        r.bad(n, "cgst", "This is an inter-state supply, so tax must be IGST, not CGST and SGST")
    if inter_state is False and igst:
        r.bad(n, "igst", "This is an intra-state supply, so tax must be CGST and SGST, not IGST")
    if cgst != sgst:
        r.bad(n, "sgst", "CGST and SGST must be equal")
    round_off = total - (taxable + cgst + sgst + igst)
    if abs(round_off) >= 1:
        r.bad(n, "total", f"total {money(total)} does not equal taxable plus taxes {money(taxable + cgst + sgst + igst)} (a rounding difference under 1 is allowed)")
        return None
    return taxable, cgst, sgst, igst, total, round_off


def check_opening(rows: list[dict], as_of: dt.date, today: dt.date) -> tuple[list[dict], list[Problem], dict, list[str]]:
    r, clean, seen, warnings = _Rows(), [], set(), []
    debit_total = credit_total = ZERO
    for i, row in enumerate(rows, 1):
        code = r.text(i, row, "account_code", required=True, maxlen=20)
        name = r.text(i, row, "account_name", required=True)
        typ = str(row.get("type") or "").strip().lower()
        if typ not in TYPES:
            r.bad(i, "type", f"type must be one of {', '.join(TYPES)}")
        subtype = r.text(i, row, "subtype", maxlen=40)
        dr = r.amount(i, row, "debit", default=ZERO)
        cr = r.amount(i, row, "credit", default=ZERO)
        if dr is None or cr is None:
            continue
        if (dr > 0) == (cr > 0):
            r.bad(i, "debit", "Each line needs a debit or a credit, not both and not neither")
            continue
        if code and code in seen:
            r.bad(i, "account_code", f"account {code} appears twice; combine the lines")
        seen.add(code)
        if typ in ("income", "expense"):
            warnings.append(f"Row {i}: {name} is a {typ} account. An opening balance sheet normally carries only assets, liabilities and equity; the year's profit sits in equity.")
        if code and name and typ in TYPES:
            clean.append({"account_code": code, "account_name": name, "type": typ, "subtype": subtype, "debit": dr, "credit": cr})
            debit_total += dr
            credit_total += cr
    if not r.problems and debit_total != credit_total:
        r.bad(0, None, f"The trial balance does not tie: debits {money(debit_total)}, credits {money(credit_total)}, difference {money(abs(debit_total - credit_total))}")
    return clean, r.problems, {"debits": money(debit_total), "credits": money(credit_total), "lines": len(clean)}, warnings


def check_invoices(rows: list[dict], as_of: dt.date, today: dt.date, our_gstins: list[dict]) -> tuple[list[dict], list[Problem], dict, list[str]]:
    r, clean, seen, warnings = _Rows(), [], set(), []
    by_gstin = {g["number"]: g for g in our_gstins}
    outstanding_total = billed_total = ZERO
    for i, row in enumerate(rows, 1):
        number = r.text(i, row, "number", required=True, maxlen=16)
        date = r.date(i, row, "date", required=True, today=today)
        if date and date > as_of:
            r.bad(i, "date", f"date {date} is after the cut-off {as_of}")
        customer = r.text(i, row, "customer_name", required=True)
        cgstin = r.gstin(i, row, "customer_gstin")
        ours = r.gstin(i, row, "supplier_gstin")
        if ours and ours not in by_gstin:
            r.bad(i, "supplier_gstin", f"{ours} is not one of this company's GSTINs")
            continue
        reg = by_gstin[ours] if ours else our_gstins[0]
        pos = r.state(i, row, "place_of_supply", cgstin[:2] if cgstin else None)
        if not pos:
            r.bad(i, "place_of_supply", "place_of_supply is required when there is no customer GSTIN")
        figures = _tax(r, i, row, (reg["state_code"] != pos) if pos else None)
        outstanding = r.amount(i, row, "outstanding")
        due = r.date(i, row, "due_date", today=dt.date(2100, 1, 1))
        if not figures or not (number and date and customer and pos):
            continue
        taxable, cgst, sgst, igst, total, round_off = figures
        if outstanding is None:
            outstanding = total
        if outstanding > total:
            r.bad(i, "outstanding", f"outstanding {money(outstanding)} is more than the invoice total {money(total)}")
            continue
        if outstanding == 0:
            warnings.append(f"Row {i}: invoice {number} has nothing outstanding; it is imported as paid.")
        key = (reg["number"], number, date.year if date.month >= 4 else date.year - 1)
        if key in seen:
            r.bad(i, "number", f"invoice {number} appears twice in the same financial year")
        seen.add(key)
        clean.append({"number": number, "date": date, "customer_name": customer, "customer_gstin": cgstin, "registration_id": reg["id"], "supplier_state": reg["state_code"],
                      "place_of_supply": pos, "taxable": taxable, "cgst": cgst, "sgst": sgst, "igst": igst, "total": total, "round_off": round_off,
                      "outstanding": outstanding, "due_date": due or date + dt.timedelta(days=30)})
        billed_total += total
        outstanding_total += outstanding
    return clean, r.problems, {"invoices": len(clean), "billed": money(billed_total), "outstanding": money(outstanding_total), "settled": money(billed_total - outstanding_total)}, warnings


def check_bills(rows: list[dict], as_of: dt.date, today: dt.date, our_gstins: list[dict]) -> tuple[list[dict], list[Problem], dict, list[str]]:
    r, clean, seen, warnings = _Rows(), [], set(), []
    states = {g["state_code"]: g for g in our_gstins}
    outstanding_total = billed_total = ZERO
    for i, row in enumerate(rows, 1):
        bill_no = r.text(i, row, "bill_no", required=True, maxlen=60)
        date = r.date(i, row, "date", required=True, today=today)
        if date and date > as_of:
            r.bad(i, "date", f"date {date} is after the cut-off {as_of}")
        supplier = r.text(i, row, "supplier_name", required=True)
        sgstin = r.gstin(i, row, "supplier_gstin")
        sstate = r.state(i, row, "supplier_state", sgstin[:2] if sgstin else None)
        if not sstate:
            r.bad(i, "supplier_state", "supplier_state is required when there is no supplier GSTIN")
        pos = r.state(i, row, "place_of_supply", our_gstins[0]["state_code"])
        if pos and pos not in states:
            r.bad(i, "place_of_supply", f"This company has no GSTIN in state {pos}")
            continue
        figures = _tax(r, i, row, (sstate != pos) if sstate and pos else None)
        tds = r.amount(i, row, "tds", default=ZERO)
        outstanding = r.amount(i, row, "outstanding")
        due = r.date(i, row, "due_date", today=dt.date(2100, 1, 1))
        accepted = r.date(i, row, "accepted_on", today=today)
        if not figures or not (bill_no and date and supplier and sstate and pos) or tds is None:
            continue
        taxable, cgst, sgst, igst, total, _ = figures
        payable = total - tds
        if outstanding is None:
            outstanding = payable
        if outstanding > payable or payable < 0:
            r.bad(i, "outstanding", f"outstanding {money(outstanding)} is more than the bill total less TDS ({money(payable)})")
            continue
        if outstanding == 0:
            warnings.append(f"Row {i}: bill {bill_no} has nothing outstanding; it is imported as paid.")
        key = (supplier.lower(), bill_no, date.year if date.month >= 4 else date.year - 1)
        if key in seen:
            r.bad(i, "bill_no", f"bill {bill_no} from {supplier} appears twice in the same financial year")
        seen.add(key)
        clean.append({"bill_no": bill_no, "date": date, "supplier_name": supplier, "supplier_gstin": sgstin, "supplier_state": sstate, "place_of_supply": pos,
                      "registration_id": states[pos]["id"], "taxable": taxable, "cgst": cgst, "sgst": sgst, "igst": igst, "total": total, "tds": tds,
                      "outstanding": outstanding, "due_date": due or date + dt.timedelta(days=30), "accepted_on": accepted or date})
        billed_total += total
        outstanding_total += outstanding
    paid = billed_total - outstanding_total - sum((c["tds"] for c in clean), ZERO)
    return clean, r.problems, {"bills": len(clean), "billed": money(billed_total), "outstanding": money(outstanding_total), "settled": money(paid)}, warnings
