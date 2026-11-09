"""Export and import rules that need no database: the time allowed to realise export proceeds, where a shipping bill stands, and the financial year.

The nine months allowed to bring export proceeds home (FEMA and the RBI directions on export of goods), the way the clock starts from the
date of export, and the financial-year boundaries are a starter library for a CA or CS to review. Longer periods exist for some exports
(for example goods sent to a warehouse abroad) and an authorised dealer can extend the period; neither is modelled.
"""
from __future__ import annotations

import calendar
import datetime as dt
from decimal import Decimal

REALISATION_MONTHS = 9
LUT_WARNING_DAYS = 30
TOLERANCE = Decimal("0.01")          # a cent: a bank charge or rounding should not leave a bill outstanding


def add_months(d: dt.date, n: int) -> dt.date:
    m = d.month - 1 + n
    y, m = d.year + m // 12, m % 12 + 1
    return dt.date(y, m, min(d.day, calendar.monthrange(y, m)[1]))


def realisation_due(exported_on: dt.date | None) -> dt.date | None:
    """The last day to realise the proceeds: nine calendar months from the date of export."""
    return add_months(exported_on, REALISATION_MONTHS) if exported_on else None


def realisation_state(fob_foreign: Decimal, realised: Decimal, exported_on: dt.date | None, today: dt.date) -> dict:
    """awaiting_export (no date of export yet), pending, overdue or realised, with the days left (negative when overdue) and what is outstanding."""
    outstanding = max(Decimal(fob_foreign) - Decimal(realised), Decimal(0))
    due = realisation_due(exported_on)
    if exported_on is None:
        state = "awaiting_export"
    elif outstanding <= TOLERANCE:
        state = "realised"
    elif today > due:
        state = "overdue"
    else:
        state = "pending"
    return {"state": state, "due_on": due, "days_left": (due - today).days if due and state in ("pending", "overdue") else None,
            "outstanding_foreign": outstanding if outstanding > TOLERANCE else Decimal(0)}


def fy_start(today: dt.date) -> dt.date:
    return dt.date(today.year if today.month >= 4 else today.year - 1, 4, 1)


def lut_state(valid_until: dt.date | None, today: dt.date, have_one: bool) -> dict:
    """Whether the Letter of Undertaking covers exports today: valid, expiring within 30 days, expired, or none on record."""
    if not have_one:
        return {"state": "none", "valid_until": None, "days_left": None}
    if valid_until is None:
        return {"state": "valid", "valid_until": None, "days_left": None}
    left = (valid_until - today).days
    return {"state": "expired" if left < 0 else "expiring" if left <= LUT_WARNING_DAYS else "valid", "valid_until": valid_until, "days_left": left}


def irp_export_details(doc: dict, bills: list[dict]) -> dict | None:
    """What the e-invoice portal wants on an export invoice that an ordinary one does not carry: the supply type (EXPWOP under LUT, EXPWP with payment of IGST),
    the unregistered-person buyer (URP) with place of supply 96, the foreign currency and the buyer's country, and the shipping bill and port once customs has issued them.
    None for a domestic document."""
    if not str(doc.get("supply_type") or "").startswith("export"):
        return None
    filed = next((b for b in bills if b.get("sb_number") and b.get("sb_date")), None)
    out = {"supply_type": "EXPWOP" if doc["supply_type"] == "export_under_lut" else "EXPWP", "buyer_gstin": "URP", "place_of_supply": "96",
           "foreign_currency": doc.get("currency"), "buyer_country": doc.get("country")}
    if filed:
        out["shipping_bill_no"] = filed["sb_number"]
        out["shipping_bill_date"] = filed["sb_date"].isoformat() if hasattr(filed["sb_date"], "isoformat") else str(filed["sb_date"])
        out["port_code"] = filed["port_code"]
    return out
