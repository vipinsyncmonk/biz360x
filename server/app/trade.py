"""Export and import rules that need no database: the time allowed to realise export proceeds, where a shipping bill stands, and the financial year.

The nine months allowed to bring export proceeds home (FEMA and the RBI directions on export of goods), the way the clock starts from the
date of export, and the financial-year boundaries are a starter library for a CA or CS to review. Longer periods exist for some exports
(for example goods sent to a warehouse abroad) and an authorised dealer can extend the period; neither is modelled.
"""
from __future__ import annotations

import calendar
import datetime as dt
import re
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


# ---- matching a bank credit to export proceeds. The statement shows the rupees *after* the bank took its charges and converted at its own rate,
# ---- often as "inward remittance" with no invoice number, so nothing here needs an exact amount: a person confirms the dollars and the charges.
CENT = Decimal("0.01")
MATCH_LOW, MATCH_HIGH = Decimal("0.90"), Decimal("1.10")      # the credit is "about right" within 10% of the invoice rate (charges and rate movement)
MATCH_FLOOR = Decimal("0.50")                                 # below this nothing is suggested
REMITTANCE = re.compile(r"INWARD|REMITT|\bFIRC\b|\bFIRA\b|SWIFT|\bIRM\b|\bFCY\b|FOREIGN|\bFX\b|\bTT\b|\bIFT\b|\b(?:USD|EUR|GBP|AED|SGD|AUD|CAD|JPY|CHF)\b")
NAME_NOISE = {"inc", "ltd", "llc", "limited", "corp", "corporation", "gmbh", "pte", "pvt", "private", "company", "the", "and", "plc", "llp", "bv", "sa", "ag"}


def looks_like_remittance(text: str) -> bool:
    return bool(REMITTANCE.search((text or "").upper()))


def _tokens(s: str) -> set[str]:
    return {t for t in re.findall(r"[a-z0-9]{3,}", (s or "").lower()) if t not in NAME_NOISE}


def name_overlap(party_name: str, text: str) -> Decimal:
    """The share of the customer's name that appears in the narration (0 to 1)."""
    want = _tokens(party_name)
    return Decimal(len(want & _tokens(text))) / Decimal(len(want)) if want else Decimal(0)


def narrated_amount(text: str, currency: str) -> Decimal | None:
    """A foreign amount written in the narration next to the invoice currency (USD 800.00 or 800.00 USD), when there is one."""
    c = re.escape(currency.upper())
    m = re.search(rf"\b{c}\s*([0-9][0-9,]*(?:\.[0-9]{{1,2}})?)", text.upper()) or re.search(rf"([0-9][0-9,]*(?:\.[0-9]{{1,2}})?)\s*{c}\b", text.upper())
    return Decimal(m.group(1).replace(",", "")).quantize(CENT) if m else None


def rank_proceeds(credit: Decimal, narration: str, remitter: str | None, candidates: list[dict], limit: int = 3) -> list[dict]:
    """Shipping bills a bank credit may be the proceeds of, best first. Each candidate has sb_id, sb_number, number (the invoice), legal_name, currency, rate,
    remaining (foreign still to realise on the bill) and balance_due (rupees still owed on the invoice).
    Evidence: the narration reads like a foreign remittance, it names the customer, it quotes the invoice or shipping bill number, the credit is within
    10% of what the rest of the invoice is worth at its own rate, and it quotes the foreign amount. Never 1.0: the dollars are the person's to confirm."""
    text = f"{remitter or ''} {narration or ''}"
    remit = looks_like_remittance(text)
    out = []
    for c in candidates:
        rate = Decimal(c["rate"])
        remaining = min(Decimal(c["remaining"]), (Decimal(c["balance_due"]) / rate).quantize(CENT))
        if remaining <= 0:
            continue
        expected = (remaining * rate).quantize(CENT)
        named = name_overlap(c["legal_name"], text) >= Decimal("0.5")
        quoted = any(x and len(x) >= 5 and x.upper() in text.upper() for x in (c.get("number"), c.get("sb_number")))
        fits = MATCH_LOW <= Decimal(credit) / expected <= MATCH_HIGH
        said = narrated_amount(text, c["currency"])
        said = said if said is not None and said <= remaining else None
        if not (named or quoted or (remit and fits)):
            continue
        conf = min(Decimal("0.95"), Decimal("0.25") * remit + Decimal("0.35") * named + Decimal("0.50") * quoted + Decimal("0.20") * fits + Decimal("0.15") * (said is not None))
        if conf < MATCH_FLOOR:
            continue
        proposed = said if said is not None else remaining if fits else min(remaining, (Decimal(credit) / rate).quantize(CENT))
        out.append({"shipping_bill_id": c["sb_id"], "confidence": conf, "proposed_foreign": proposed, "expected_inr": (proposed * rate).quantize(CENT)})
    return sorted(out, key=lambda x: -x["confidence"])[:limit]
