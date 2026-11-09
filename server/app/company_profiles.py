"""Company profiles: register your own company with the sandbox MCA registry, in development only.

The sandbox registry knows two made-up companies. To walk the real onboarding flow with a real company (your CIN, your GSTINs), put
its details in a JSON file and point BIZ_COMPANY_PROFILES at it. The profile is merged into SandboxMca.registry, so a lookup by CIN,
LLPIN, GSTIN or PAN finds it, and onboarding then creates the workspace exactly as it does for any other company.

This is not a registry client. Nothing is fetched from MCA or GSTN; the file is what you typed, checked for the shapes the database
enforces so a typo fails here with a clear message and not half-way through onboarding. The sandbox adapters are refused outside
development (adapters.get), so a profile can never be loaded in production. Keep the real file out of git (see docs/DEV_SETUP.md).
"""
from __future__ import annotations

import datetime as dt
import json
import os
import re

CIN = re.compile(r"^[LU][0-9]{5}[A-Z]{2}[0-9]{4}[A-Z]{3}[0-9]{6}$")
LLPIN = re.compile(r"^[A-Z]{3}-[0-9]{4}$")
GSTIN = re.compile(r"^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][1-9A-Z]Z[0-9A-Z]$")
PAN = re.compile(r"^[A-Z]{5}[0-9]{4}[A-Z]$")
TAN = re.compile(r"^[A-Z]{4}[0-9]{5}[A-Z]$")
UDYAM = re.compile(r"^UDYAM-[A-Z]{2}-[0-9]{2}-[0-9]{7}$")
DIN = re.compile(r"^[0-9]{8}$")
MONEY = re.compile(r"^[0-9]+(\.[0-9]{1,2})?$")

CONSTITUTIONS = ("private_limited", "public_limited", "opc", "section8", "llp", "partnership", "proprietorship", "trust", "society", "huf")
COMPANY_KINDS = ("private_limited", "public_limited", "opc", "section8")
GST_STATUS = ("active", "cancelled", "suspended")
FILING = ("monthly", "quarterly")
UDYAM_CLASS = ("micro", "small", "medium")


class ProfileError(ValueError):
    """Carries every problem found, one line each, so a person can fix the file in one pass."""

    def __init__(self, problems: list[str]):
        super().__init__("; ".join(problems))
        self.problems = problems


def _date(v, label: str, problems: list[str], today: dt.date, required: bool = False) -> str | None:
    if v in (None, ""):
        if required:
            problems.append(f"{label} is required (YYYY-MM-DD)")
        return None
    try:
        d = dt.date.fromisoformat(str(v))
    except ValueError:
        problems.append(f"{label} must be a date as YYYY-MM-DD, got {v!r}")
        return None
    if d > today:
        problems.append(f"{label} {v} is in the future")
    return d.isoformat()


def _one(c: dict, i: int, today: dt.date, problems: list[str]) -> dict | None:
    start = len(problems)
    name = str(c.get("legal_name") or "").strip()
    where = f"companies[{i}]" + (f" ({name})" if name else "")

    def bad(msg):
        problems.append(f"{where}: {msg}")

    if not name:
        bad("legal_name is required")
    constitution = c.get("constitution")
    if constitution not in CONSTITUTIONS:
        bad(f"constitution must be one of {', '.join(CONSTITUTIONS)}")
    cin, llpin = (c.get("cin") or "").strip().upper(), (c.get("llpin") or "").strip().upper()
    if constitution in COMPANY_KINDS and not CIN.match(cin):
        bad("cin is required for a company and must look like U26510KA2019PTC123456")
    elif constitution == "llp" and not LLPIN.match(llpin):
        bad("llpin is required for an LLP and must look like AAA-1234")
    elif constitution not in COMPANY_KINDS and constitution != "llp" and not (cin or llpin):
        bad("give a cin or an llpin; the lookup is by that number")
    elif cin and not CIN.match(cin):
        bad("cin has the wrong shape")
    elif llpin and not LLPIN.match(llpin):
        bad("llpin has the wrong shape")
    ident = cin or llpin

    state = str(c.get("registered_state") or "").strip()
    if not re.match(r"^[0-9]{2}$", state):
        bad("registered_state is the two-digit GST state code, for example 29 for Karnataka")
    pan = (c.get("pan") or "").strip().upper()
    if pan and not PAN.match(pan):
        bad("pan has the wrong shape (AAAAA9999A)")
    tan = (c.get("tan") or "").strip().upper()
    if tan and not TAN.match(tan):
        bad("tan has the wrong shape (AAAA99999A)")
    incorporated = _date(c.get("incorporated_on"), f"{where}: incorporated_on", problems, today, required=True)
    agm = _date(c.get("last_agm_on"), f"{where}: last_agm_on", problems, today)
    cap = c.get("paid_up_capital")
    if cap not in (None, "") and not MONEY.match(str(cap)):
        bad("paid_up_capital must be an amount like 1000000.00, with no commas")

    directors = []
    for j, d in enumerate(c.get("directors") or []):
        dn, din = str(d.get("name") or "").strip(), str(d.get("din") or "").strip()
        if not dn:
            bad(f"directors[{j}] needs a name")
        if din and not DIN.match(din):
            bad(f"directors[{j}] DIN must be eight digits")
        directors.append({"name": dn, "din": din})

    gstins, seen = [], set()
    for j, g in enumerate(c.get("gstins") or []):
        num = str(g.get("gstin") or "").strip().upper()
        if not GSTIN.match(num):
            bad(f"gstins[{j}] {num or '(empty)'} is not a valid GSTIN shape")
            continue
        if num in seen:
            bad(f"gstins[{j}] {num} appears twice")
        seen.add(num)
        if pan and num[2:12] != pan:
            bad(f"gstins[{j}] {num} embeds PAN {num[2:12]}, not {pan}")
        status = g.get("status", "active")
        if status not in GST_STATUS:
            bad(f"gstins[{j}] status must be one of {', '.join(GST_STATUS)}")
        freq = g.get("filing_frequency", "monthly")
        if freq not in FILING:
            bad(f"gstins[{j}] filing_frequency must be monthly or quarterly")
        gstins.append({"gstin": num, "state_code": num[:2], "status": status, "filing_frequency": freq,
                       "label": str(g.get("label") or f"State {num[:2]}")})
    if not pan and gstins:
        pan = gstins[0]["gstin"][2:12]

    udyam = c.get("udyam")
    if udyam:
        un, uc = str(udyam.get("number") or "").strip().upper(), udyam.get("class")
        if not UDYAM.match(un):
            bad("udyam.number must look like UDYAM-KA-03-0054321")
        if uc not in UDYAM_CLASS:
            bad("udyam.class must be micro, small or medium")
        udyam = {"number": un, "class": uc}
    else:
        udyam = None

    if len(problems) > start:
        return None
    return {"kind": "cin" if cin else "llpin", "cin": cin or None, "llpin": llpin or None, "legal_name": name, "constitution": constitution,
            "incorporated_on": incorporated, "registered_state": state, "registered_address": str(c.get("registered_address") or ""),
            "paid_up_capital": f"{float(cap):.2f}" if cap not in (None, "") else None, "nic_code": str(c.get("nic_code") or "") or None,
            "last_agm_on": agm, "pan": pan or None, "tan": tan or None, "directors": directors, "gstins": gstins, "udyam": udyam,
            "_key": ident}


def validate(data, today: dt.date | None = None) -> dict[str, dict]:
    """Return {identifier: registry entry}. Raise ProfileError listing every problem."""
    today = today or dt.date.today()
    problems: list[str] = []
    companies = data.get("companies") if isinstance(data, dict) else None
    if not isinstance(companies, list) or not companies:
        raise ProfileError(['the file must be {"companies": [ {...}, ... ]} with at least one company'])
    out: dict[str, dict] = {}
    held: dict[str, str] = {}
    for i, c in enumerate(companies):
        if not isinstance(c, dict):
            problems.append(f"companies[{i}] must be an object")
            continue
        e = _one(c, i, today, problems)
        if not e:
            continue
        key = e.pop("_key")
        if key in out:
            problems.append(f"companies[{i}]: {key} is listed twice")
            continue
        for g in e["gstins"]:
            if g["gstin"] in held:
                problems.append(f"companies[{i}]: GSTIN {g['gstin']} is already listed under {held[g['gstin']]}")
            held[g["gstin"]] = e["legal_name"]
        out[key] = e
    if problems:
        raise ProfileError(problems)
    return out


def load_file(path: str, today: dt.date | None = None) -> dict[str, dict]:
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        raise ProfileError([f"{path} does not exist"])
    except json.JSONDecodeError as e:
        raise ProfileError([f"{path} is not valid JSON: {e}"])
    return validate(data, today)


def configured() -> dict[str, dict]:
    """Entries from the file named by BIZ_COMPANY_PROFILES (empty when unset). A bad file raises, so a typo is loud at start-up."""
    path = os.environ.get("BIZ_COMPANY_PROFILES", "").strip()
    return load_file(path) if path else {}


def summary(entries: dict[str, dict]) -> list[str]:
    lines = []
    for key, e in entries.items():
        extra = []
        if e["pan"]:
            extra.append(f"PAN {e['pan']}")
        if e["tan"]:
            extra.append(f"TAN {e['tan']}")
        extra.append(f"{len(e['gstins'])} GSTIN" + ("" if len(e["gstins"]) == 1 else "s"))
        extra.append(f"{len(e['directors'])} director" + ("" if len(e["directors"]) == 1 else "s"))
        if e["udyam"]:
            extra.append(f"Udyam {e['udyam']['class']}")
        lines.append(f"{key}  {e['legal_name']}  ({e['constitution']}; " + ", ".join(extra) + ")")
    return lines
