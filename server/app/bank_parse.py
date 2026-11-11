"""Read a bank narration into parts a rule can use: how the money moved, who it went to, the remark the sender typed and the bank's reference.

Banks write narrations in their own shapes, and several shapes exist per bank. Each recogniser below covers one family; anything else falls back to
"other" with the words kept, so rules can still match on a keyword. Nothing here guesses an accounting treatment: that is `bank_rules`.

  mode:        upi, neft, rtgs, imps, auto_debit, transfer, cheque, card, cash, charges, interest, other
  key:         what identifies the counterparty for a rule: a UPI handle, a beneficiary name, a card or deposit number; lower case, or None
  name:        what to show for it
  remark:      the text the sender typed (SalaryMarch26, Rent, invoice numbers)
  reference:   the UTR, RRN or transaction id
"""
from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass
class Parsed:
    mode: str = "other"
    key: str | None = None
    name: str | None = None
    remark: str | None = None
    reference: str | None = None
    ifsc: str | None = None


_IFSC = re.compile(r"^[A-Z]{4}0[A-Z0-9]{6}$")
_VPA = re.compile(r"^[\w.\-]{2,}@[A-Za-z][\w.\-]*$")
_SPACE = re.compile(r"\s+")


def _clean(s: str | None) -> str | None:
    s = _SPACE.sub(" ", (s or "").strip())
    return s or None


def words(text: str | None) -> list[str]:
    """Lower-case words with camelCase and letter/digit joins split: 'March26Rent' -> march 26 rent, 'SalaryFY2526' -> salary fy 2526."""
    s = re.sub(r"([a-z])([A-Z])", r"\1 \2", text or "")
    s = re.sub(r"([A-Za-z])(\d)", r"\1 \2", s)
    s = re.sub(r"(\d)([A-Za-z])", r"\1 \2", s)
    return [w for w in re.split(r"[^A-Za-z0-9]+", s.lower()) if w]


def _key(s: str | None) -> str | None:
    s = _clean(s)
    return s.lower() if s else None


def parse_narration(text: str | None) -> Parsed:
    raw = _clean(text) or ""
    up = raw.upper()

    # UPI/<rrn>/<note>/<handle>//<txn id>/  (ICICI)   UPI-<name>-<handle>-<ifsc>-<rrn>-<note>  (HDFC)   UPI/<rrn>/<name>/<note>/<bank>
    m = re.match(r"^UPI/(\d{9,14})/([^/]*)/([^/]*)/([^/]*)/*([^/]*)", raw, re.I)
    if m:
        rrn, note, third, fourth, fifth = (_clean(x) for x in m.groups())
        handle = next((x for x in (third, fourth, fifth) if x and _VPA.match(x)), None)
        note = None if (note or "").lower() in ("upi pay", "upi", "payment from phone", "paid via phonepe") else note
        name = None if handle else third
        return Parsed("upi", _key(handle or name), handle or name, note, rrn)
    m = re.match(r"^UPI-([^-]+)-([^-]+@[^-]+)-([A-Z]{4}0[A-Z0-9]{6})?-?(\d{9,14})?-?(.*)$", raw, re.I)
    if m:
        name, handle, ifsc, rrn, note = (_clean(x) for x in m.groups())
        return Parsed("upi", _key(handle), name or handle, note if (note or "").lower() not in ("upi", "payment from phone") else None, rrn, ifsc)

    # INF/NEFT/<utr>/<ifsc>/<remark>/<beneficiary>   NEFT/<utr>/<name>   NEFT-<utr>-<name>-<ifsc>-<remark>   (also RTGS, IMPS)
    m = re.match(r"^(?:INF/|INFT/|MMT/)?(NEFT|RTGS|IMPS)[/\- ]+([A-Z0-9]{8,30})[/\-](.*)$", raw, re.I)
    if m:
        mode, ref, rest = m.group(1).lower(), m.group(2), m.group(3)
        parts = [p for p in (_clean(x) for x in re.split(r"[/\-]", rest)) if p]
        ifsc = next((p.upper() for p in parts if _IFSC.match(p.upper())), None)
        others = [p for p in parts if p.upper() != ifsc]
        if m.group(0).upper().startswith("MMT/") and len(others) >= 2:
            remark, name = others[0], others[1]                                # IMPS through the bank: remark, beneficiary, then their bank
        elif m.group(0).upper().startswith(("INF/", "INFT/")) and len(others) >= 2:
            remark, name = others[0], others[-1]                               # outward, ICICI style: remark then beneficiary
        elif others:
            name, remark = others[0], (others[1] if len(others) > 1 else None)  # inward and other styles: the party first
        else:
            name = remark = None
        return Parsed(mode, _key(name), name, remark, ref, ifsc)

    # ATD/Auto Debit CC1xx6441    ACH D- <name>-<ref>    NACH/ECS debits
    m = re.match(r"^ATD/(?:AUTO DEBIT)?\s*(.*)$", raw, re.I)
    if m:
        card = re.match(r"^((?:CC|CARD|CRED[A-Z]*)[0-9xX*]*\d[0-9xX*]*)", m.group(1), re.I)      # the card's masked number; text run on after it is not part of it
        what = _clean(card.group(1)) if card else _clean(m.group(1))
        is_card = bool(card) or "CREDIT CARD" in up
        return Parsed("auto_debit", _key(("card:" + what) if is_card and what else what), what, "credit card" if is_card else None)
    m = re.match(r"^(?:ACH|NACH|ECS)[ /\-]*(?:D|C)?[ /\-]+([^/\-]+)[/\-]?(.*)$", raw, re.I)
    if m:
        return Parsed("auto_debit", _key(m.group(1)), _clean(m.group(1)), _clean(m.group(2)))

    # TRF TO FD no. 317113010027   (a transfer to or from a deposit of the company's own)
    m = re.search(r"\bTRF (TO|FROM)\s+(FD|RD|SWEEP|DEPOSIT)\b[^0-9]*(\d{6,})?", raw, re.I)
    if m:
        number = m.group(3)
        return Parsed("transfer", _key(f"{m.group(2).lower()}:{number}" if number else m.group(2)), f"{m.group(2).upper()} {number or ''}".strip(),
                      f"transfer {m.group(1).lower()} {m.group(2).lower()}")

    if re.search(r"\b(CHQ|CHEQUE|CLG|CLEARING)\b", up):
        return Parsed("cheque", None, None, _clean(raw), (re.search(r"\b(\d{6})\b", raw) or [None, None])[1])
    if re.match(r"^(POS|ECOM|ECOM PUR|PUR|DC |CC )", up) or "POS " in up[:8]:
        merchant = _clean(re.sub(r"^(POS|ECOM PUR|ECOM|PUR)[/\s\-]*\d*[/\s\-]*", "", raw, flags=re.I))
        return Parsed("card", _key(merchant), merchant, None)
    if re.match(r"^(ATM|NWD|CASH WDL|CASH DEP|CDM|BY CASH|CASH)", up):
        return Parsed("cash", None, None, _clean(raw))
    if re.search(r"\b(INT\.? ?PD|INTEREST|INT CR|INT\.?CREDIT|CR INT|QUARTERLY INT)\b", up):
        return Parsed("interest", None, None, _clean(raw))
    if re.search(r"\b(CHRG|CHRGS|CHARGES|CHGS|SMS ?CHG|AMC|ANNUAL FEE|MIN(IMUM)? BAL|PENAL|GST ON)\b", up) or up.startswith(("DR CARD", "SERVICE CHARGE")):
        return Parsed("charges", None, None, _clean(raw))

    # anything else: keep the text; the first run of letters is a fair stand-in for who it is
    first = re.search(r"[A-Za-z][A-Za-z .&']{3,}", raw)
    return Parsed("other", _key(first.group(0)) if first else None, _clean(first.group(0)) if first else None, raw or None)
