"""Portal adapters: one small interface per external system, a registry that picks the implementation, and sandboxes.

Everything that talks to GSTN, the IRP, the e-way bill portal, MCA, TRACES/Income Tax, EPFO, ESIC, an Account Aggregator or a
bank goes through one of these interfaces, and only ever from a worker job. Handlers never import a vendor SDK; they call
`adapters.get("gsp").file_return(...)`. Choosing the licensed GSP/ASP (issue #39) therefore means writing one class per
interface that fits the vendor's API and naming it in BIZ_ADAPTERS, e.g. `BIZ_ADAPTERS=gsp=acme,irp=acme,eway=acme`.

Error vocabulary (handlers and the worker rely on it):
  OtpRequired        the portal wants a one-time password; the job pauses in `waiting_for_otp` and resumes with it.
  PortalRejected     a terminal 4xx from the portal (bad data, wrong OTP). Not retried; the job fails with the portal's code.
  PortalUnavailable  a transient failure (timeout, 5xx, maintenance). Retried with backoff.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import hmac
import json
import os
import re
import pathlib
import uuid
from typing import Any, Protocol

from . import secretbox


# ================================================================== errors
class PortalError(Exception):
    code = "portal_error"

    def __init__(self, message: str, code: str | None = None):
        super().__init__(message)
        if code:
            self.code = code


class PortalRejected(PortalError):
    code = "portal_rejected"


class PortalUnavailable(PortalError):
    code = "portal_unavailable"


class SessionExpired(PortalError):
    code = "portal_session_expired"


class OtpRequired(Exception):
    def __init__(self, session_ref: str, channel: str = "mobile", message: str = "Enter the OTP sent to the registered mobile number"):
        super().__init__(message)
        self.session_ref, self.channel, self.message = session_ref, channel, message


# ================================================================== interfaces
class Irp(Protocol):
    def generate_irn(self, payload: dict) -> dict: ...
    def cancel_irn(self, irn: str, reason: str) -> dict: ...


class Gsp(Protocol):
    def fetch_inward(self, gstin: str, period: str, source: str) -> list[dict]: ...
    def file_return(self, gstin: str, return_type: str, period: str, payload: dict, *, signing: str = "evc_otp", otp: str | None = None) -> dict: ...


class Eway(Protocol):
    def generate(self, payload: dict) -> dict: ...
    def update_vehicle(self, ewb_no: str, vehicle_no: str, reason: str) -> dict: ...


class Mca(Protocol):
    def lookup(self, identifier: str, kind: str) -> dict | None: ...
    def file_form(self, form_code: str, payload: dict) -> dict: ...


class Traces(Protocol):
    def fetch_26as(self, pan: str, fy: str) -> list[dict]: ...
    def upload_return(self, token: str, form: str) -> dict: ...
    def pay_challan(self, payload: dict) -> dict: ...
    def fvu_validate(self, payload: dict) -> dict: ...


class Labour(Protocol):
    def upload_ecr(self, payload: dict, *, otp: str | None = None) -> dict: ...
    def upload_esic(self, payload: dict, *, otp: str | None = None) -> dict: ...


class Sessions(Protocol):
    def start(self, portal: str, credential_ref: str | None) -> str: ...                 # raises OtpRequired
    def complete(self, portal: str, session_ref: str, otp: str) -> dt.datetime: ...      # returns the session expiry


class AccountAggregator(Protocol):
    def create_consent(self, handle: str, accounts: list[str], valid_until: dt.datetime) -> dict: ...
    def consent_url(self, handle: str) -> str: ...
    def fetch(self, handle: str, account: str, start: dt.date, end: dt.date) -> list[dict]: ...


class Bank(Protocol):
    def send_payment(self, payload: dict) -> dict: ...
    def send_salaries(self, payload: dict) -> dict: ...


# ================================================================== sandboxes
def _h(*parts) -> str:
    return hashlib.sha256("|".join(str(p) for p in parts).encode()).hexdigest()


class SandboxIrp:
    """Deterministic IRN and QR. A document whose number contains 'REJECT' is refused, so failure paths can be exercised."""

    def generate_irn(self, payload):
        key = f"{payload['seller_gstin']}|{payload['fy']}|{payload['doc_type']}|{payload['number']}"
        if "REJECT" in payload["number"]:
            raise PortalRejected("2150: Duplicate IRN (sandbox rule)", "2150")
        return {"irn": _h(key), "ack_no": str(int(hashlib.md5(key.encode()).hexdigest()[:12], 16))[:15], "signed_qr": "sandbox-signed-qr:" + hashlib.sha1(key.encode()).hexdigest()}

    def cancel_irn(self, irn, reason):
        return {"cancelled": True, "cancel_date": dt.datetime.now(dt.timezone.utc).isoformat()}


class SandboxGsp:
    inward: dict[tuple[str, str, str], list[dict]] = {}          # tests and demos preload what "the portal" reports
    VALID_OTP = "123456"

    def fetch_inward(self, gstin, period, source):
        return list(self.inward.get((gstin, period, source), []))

    def file_return(self, gstin, return_type, period, payload, *, signing="evc_otp", otp=None):
        if signing == "evc_otp":
            if otp is None:
                raise OtpRequired(_h("gsp", gstin, return_type, period)[:16], "mobile", "Enter the EVC OTP sent to the authorised signatory's mobile")
            if otp != self.VALID_OTP:
                raise PortalRejected("RET11417: The OTP is incorrect", "RET11417")
        return {"arn": "AA" + uuid.uuid5(uuid.NAMESPACE_URL, f"{gstin}|{return_type}|{period}").hex[:13].upper()}


class SandboxEway:
    def generate(self, payload):
        if int(payload["distance_km"]) > 4000:
            raise PortalRejected("Distance cannot exceed 4000 km", "EWB-DIST")
        days = max(1, -(-int(payload["distance_km"]) // 200))                          # one day per 200 km, part days count
        no = str(int(_h("ewb", payload["seller_gstin"], payload["doc_no"], payload["to_pincode"])[:12], 16))[:12].zfill(12)
        return {"ewb_no": no, "valid_until": (dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=days)).isoformat()}

    def update_vehicle(self, ewb_no, vehicle_no, reason):
        return {"ewb_no": ewb_no, "vehicle_no": vehicle_no, "updated": True}


class SandboxMca:
    registry = {"U26510KA2019PTC123456": {
        "kind": "cin", "cin": "U26510KA2019PTC123456", "legal_name": "Nimbus Instruments Private Limited", "constitution": "private_limited", "incorporated_on": "2019-08-14", "registered_state": "29",
        "registered_address": "Peenya, Bengaluru, Karnataka", "paid_up_capital": "29500000.00", "nic_code": "26510", "last_agm_on": "2026-09-30",
        "directors": [{"name": "Ananya Rao", "din": "08123456"}, {"name": "Ravi Kumar", "din": "08234567"}, {"name": "Meera Iyer", "din": "08345678"}],
        "gstins": [{"gstin": "29AAHCN1234Q1Z8", "state_code": "29", "status": "active", "filing_frequency": "monthly", "label": "Karnataka principal place"},
                   {"gstin": "33AAHCN1234Q1Z4", "state_code": "33", "status": "active", "filing_frequency": "monthly", "label": "Tamil Nadu branch"}],
        "udyam": {"number": "UDYAM-KA-03-0054321", "class": "small"}},
        # a second company that exists in no demo workspace, so a developer can walk through onboarding on a seeded database
        "U72900MH2022PTC999999": {
        "kind": "cin", "cin": "U72900MH2022PTC999999", "legal_name": "Orchid Labs Private Limited", "constitution": "private_limited", "incorporated_on": "2022-06-01",
        "registered_state": "27", "registered_address": "Powai, Mumbai, Maharashtra", "paid_up_capital": "1000000.00", "nic_code": "62099", "last_agm_on": "2026-09-25",
        "directors": [{"name": "Kavya Nair", "din": "09123456"}],
        "gstins": [{"gstin": "27AAKCO5678M1Z2", "state_code": "27", "status": "active", "filing_frequency": "monthly", "label": "Maharashtra principal place"}],
        "udyam": None}}

    def lookup(self, identifier, kind):
        if identifier == "U99999KA2020PTC000000":
            raise PortalUnavailable("MCA is down for maintenance (sandbox rule)")
        if kind == "cin":
            return self.registry.get(identifier)
        for entry in self.registry.values():                       # a GSTIN or PAN resolves to the company that holds it
            for g in entry["gstins"]:
                if (kind == "gstin" and g["gstin"] == identifier) or (kind == "pan" and g["gstin"][2:12] == identifier):
                    return entry
        return None

    def file_form(self, form_code, payload):
        return {"srn": "T" + _h("mca", form_code, payload.get("cin"), payload.get("period"))[:8].upper()}


class SandboxTraces:
    entries: dict[tuple[str, str], list[dict]] = {}                # (pan, fy) -> Form 26AS lines

    def fetch_26as(self, pan, fy):
        return list(self.entries.get((pan, fy), []))

    def upload_return(self, token, form):
        return {"ack": "TRACES" + _h("tr", token, form)[:10].upper()}

    def pay_challan(self, payload):
        return {"challan_ref": "CIN" + _h(payload["challan_id"])[:14].upper(), "bsr_code": "0510308"}

    def fvu_validate(self, payload):
        return {"token": "TKN" + _h(payload["return_id"])[:12].upper()}


class SandboxLabour:
    VALID_OTP = "123456"

    def upload_ecr(self, payload, *, otp=None):
        if otp is None:
            raise OtpRequired(_h("epfo", payload["period"])[:16], "mobile", "Enter the OTP sent for the EPFO unified portal login")
        if otp != self.VALID_OTP:
            raise PortalRejected("The OTP is incorrect", "EPFO-OTP")
        return {"trrn": "TRRN" + _h("ecr", payload["establishment"], payload["period"])[:11].upper()}

    def upload_esic(self, payload, *, otp=None):
        return {"ack": "ESIC" + _h("esic", payload["employer_code"], payload["period"])[:10].upper()}


class SandboxSessions:
    VALID_OTP = "123456"
    TTL = {"gstn": dt.timedelta(hours=6), "income_tax": dt.timedelta(days=1), "mca": dt.timedelta(days=1), "traces": dt.timedelta(days=1),
           "epfo": dt.timedelta(hours=12), "esic": dt.timedelta(hours=12), "eway": dt.timedelta(hours=6), "irp": dt.timedelta(hours=6)}

    def start(self, portal, credential_ref):
        raise OtpRequired(_h("session", portal, credential_ref)[:16], "mobile")

    def complete(self, portal, session_ref, otp):
        if otp != self.VALID_OTP:
            raise PortalRejected("The OTP is incorrect", "OTP")
        return dt.datetime.now(dt.timezone.utc) + self.TTL.get(portal, dt.timedelta(hours=12))


class SandboxAa:
    data: dict[tuple[str, str], list[dict]] = {}               # (consent handle, account) -> bank lines

    def create_consent(self, handle, accounts, valid_until):
        return {"redirect_url": self.consent_url(handle)}

    def consent_url(self, handle):
        return f"https://aa.sandbox.biz360x.example/consent/{handle}"

    def fetch(self, handle, account, start, end):
        return [l for l in self.data.get((handle, account), []) if start <= dt.date.fromisoformat(l["txn_date"]) <= end]


class SandboxBank:
    def send_payment(self, payload):
        return {"utr": "SBX" + uuid.uuid5(uuid.NAMESPACE_URL, payload["payment_no"]).hex[:12].upper(), "status": "accepted"}

    def send_salaries(self, payload):
        return {"batch_ref": "SAL" + uuid.uuid5(uuid.NAMESPACE_URL, payload["run_id"]).hex[:10].upper(), "status": "accepted"}


class SandboxSamadhaan:
    """MSME Samadhaan, where a micro or small supplier files a claim for delayed payment. A real adapter calls the portal."""
    def file_claim(self, payload):
        if not payload.get("invoices"):
            raise PortalRejected("A claim needs at least one invoice", "SAM001")
        return {"case_no": "SAM/" + _h("samadhaan", payload["claim_id"])[:10].upper(), "forum": "msme_samadhaan"}


# ================================================================== copilot
def _inr(v) -> str:
    """Rupees in the Indian grouping, no decimals unless there are paise: 1234567.5 -> Rs 12,34,567.50"""
    from decimal import Decimal
    d = Decimal(str(v)).quantize(Decimal("0.01"))
    sign, d = ("-", -d) if d < 0 else ("", d)
    whole, frac = str(d).split(".")
    head, tail = whole[:-3], whole[-3:]
    groups = []
    while len(head) > 2:
        groups.insert(0, head[-2:])
        head = head[:-2]
    if head:
        groups.insert(0, head)
    return f"{sign}Rs {','.join(groups + [tail])}" + ("" if frac == "00" else "." + frac)


def _day(iso: str) -> str:
    """"2026-10-07" -> "7 Oct"."""
    y, m, d = iso[:10].split("-")
    return f"{int(d)} {['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'][int(m) - 1]}"


class SandboxCopilot:
    """A rule-based stand-in for a language model, so the copilot works without a vendor key.

    It picks what to look up from the words in the question, reads only through the `tools` it is handed (the server runs those under the
    caller's own permissions), and writes an answer in which every fact carries a [n] marker pointing at the record it came from. It can
    propose actions; it can never perform one. A real model replaces this class and keeps the same two things: tools in, a reply out.

    reply(question, tools) -> {"content": str, "citations": [{"subject", "subject_id", "label"}], "actions": [{"kind", "payload"}], "module": str | None}
    """
    name = "sandbox"
    # Whole words only, so "overdue" does not count as "due" and "GST returns" does not count as a question about supplier credit.
    TOPICS = (
        ("itc", {"itc", "credit", "2a", "2b", "supplier", "suppliers", "vendor", "vendors", "reminder", "reminders"}, ("input tax",)),
        ("receivables", {"owe", "owes", "owed", "owing", "money", "receivable", "receivables", "collect", "customer", "customers", "unpaid", "debtor", "debtors", "invoice", "invoices"}, ()),
        ("filings", {"due", "deadline", "deadlines", "filing", "filings", "file", "compliance", "return", "returns", "upcoming"}, ()),
    )

    def reply(self, question, tools):
        from decimal import Decimal
        q = question.lower()
        words = set(re.findall(r"[a-z0-9]+", q))
        topics = [t for t, ws, phrases in self.TOPICS if words & ws or any(ph in q for ph in phrases)]
        parts: list[str] = []
        citations: list[dict] = []
        actions: list[dict] = []

        def cite(subject, subject_id, label) -> str:
            citations.append({"subject": subject, "subject_id": subject_id, "label": label})
            return f"[{len(citations)}]"

        if "itc" in topics:
            gaps = tools.itc_gaps()
            if not gaps:
                parts.append("I found no purchase invoices that are missing from your GST portal view, so there is no supplier to chase.")
            else:
                total = sum(Decimal(g["tax"]) for g in gaps)
                lines = [f"{g['supplier']}: {g['invoices']} {'invoice' if g['invoices'] == 1 else 'invoices'}, {_inr(g['tax'])} of tax "
                         f"{cite('itc_matches', g['match_ids'][0], 'Purchase register vs GST portal · ' + g['supplier'])}" for g in gaps[:6]]
                parts.append(f"{len(gaps)} {'supplier has' if len(gaps) == 1 else 'suppliers have'} invoiced you for {_inr(total)} of input tax that does not show on the GST portal yet. "
                             "You can claim it only once the supplier files and it appears in your GSTR-2B.\n" + "\n".join("- " + x for x in lines))
                actions.append({"kind": "send_supplier_reminders", "payload": {"party_ids": [g["party_id"] for g in gaps],
                                                                               "suppliers": [{"name": g["supplier"], "invoices": g["invoices"], "tax": g["tax"]} for g in gaps]}})
        if "receivables" in topics:
            owed = tools.receivables()
            if not owed:
                parts.append("No customer invoice is unpaid right now.")
            else:
                total = sum(Decimal(r["balance_due"]) for r in owed)
                late = [r for r in owed if r["days_overdue"] > 0]
                head = f"Customers owe you {_inr(total)} across {len(owed)} {'invoice' if len(owed) == 1 else 'invoices'}; {len(late)} {'is' if len(late) == 1 else 'are'} past the due date."
                lines = [f"{r['party']}: {r['number']} for {_inr(r['balance_due'])}, {r['days_overdue']} days late "
                         f"{cite('sales_documents', r['document_id'], 'Invoice ' + r['number'])}" for r in sorted(late, key=lambda r: -r["days_overdue"])[:5]]
                parts.append(head + ("\n" + "\n".join("- " + x for x in lines) if lines else ""))
                if late:
                    top = max(late, key=lambda r: r["days_overdue"])
                    actions.append({"kind": "create_task", "payload": {"title": f"Follow up with {top['party']} on {top['number']} ({_inr(top['balance_due'])}, {top['days_overdue']} days late)"}})
        if "filings" in topics:
            fl = tools.filings()
            if not fl:
                parts.append("Nothing is open on your compliance calendar.")
            else:
                late = [f for f in fl if f["days"] < 0]
                soon = [f for f in fl if 0 <= f["days"] <= 14]
                head = f"You have {len(fl)} open {'filing' if len(fl) == 1 else 'filings'}: {len(late)} overdue and {len(soon)} due in the next 14 days."
                shown = (late + soon)[:6] or fl[:3]
                lines = [f"{f['label']}, {'was due ' + _day(f['due']) + ' (' + str(-f['days']) + ' days late)' if f['days'] < 0 else 'due ' + _day(f['due'])} "
                         f"{cite('obligations', f['id'], f['label'])}" for f in shown]
                parts.append(head + "\n" + "\n".join("- " + x for x in lines))
                if late:
                    actions.append({"kind": "create_task", "payload": {"title": f"Clear {len(late)} overdue {'filing' if len(late) == 1 else 'filings'}, starting with {late[0]['label']}"}})
        if not parts:
            return {"content": "I answer from your own records. Try asking what is due this week, which suppliers are holding up your GST credit, or who owes you money.",
                    "citations": [], "actions": [], "module": None}
        module = {"itc": "gst", "receivables": "sales", "filings": "compliance"}[topics[0]] if len(topics) == 1 else None
        return {"content": "\n\n".join(parts), "citations": citations, "actions": actions, "module": module}


# ================================================================== registry
IMPLEMENTATIONS: dict[str, dict[str, type]] = {
    "irp": {"sandbox": SandboxIrp}, "gsp": {"sandbox": SandboxGsp}, "eway": {"sandbox": SandboxEway}, "mca": {"sandbox": SandboxMca},
    "traces": {"sandbox": SandboxTraces}, "labour": {"sandbox": SandboxLabour}, "sessions": {"sandbox": SandboxSessions},
    "aa": {"sandbox": SandboxAa}, "bank": {"sandbox": SandboxBank}, "copilot": {"sandbox": SandboxCopilot}, "samadhaan": {"sandbox": SandboxSamadhaan},
}
_active: dict[str, Any] = {}


def register(family: str, name: str, cls: type) -> None:
    """Make a vendor implementation selectable, e.g. register('gsp', 'acme', AcmeGsp)."""
    IMPLEMENTATIONS[family][name] = cls


def _choice(family: str) -> str:
    cfg = dict(p.split("=", 1) for p in os.environ.get("BIZ_ADAPTERS", "").split(",") if "=" in p)
    name = cfg.get(family, "sandbox")
    if name not in IMPLEMENTATIONS[family]:
        raise RuntimeError(f"BIZ_ADAPTERS selects '{name}' for {family}, but only {sorted(IMPLEMENTATIONS[family])} are installed")
    if name == "sandbox" and os.environ.get("BIZ_ENV", "dev") not in ("dev", "test"):
        raise RuntimeError(f"The sandbox {family} adapter cannot run outside development; set BIZ_ADAPTERS")
    return name


def get(family: str):
    if family not in _active:
        _active[family] = IMPLEMENTATIONS[family][_choice(family)]()
    return _active[family]


def use(family: str, impl) -> None:
    """Replace an adapter (tests, or a vendor object built with credentials)."""
    _active[family] = impl


def reset() -> None:
    _active.clear()


def status() -> dict[str, str]:
    return {f: type(get(f)).__name__ for f in IMPLEMENTATIONS}


class proxy:
    """A module-level name that always forwards to the adapter currently selected for `family`."""

    def __init__(self, family: str):
        self._family = family

    def __getattr__(self, name):
        return getattr(get(self._family), name)


# ================================================================== credentials
class LocalSecretStore:
    """Development secret store: one encrypted JSON file per reference under BIZ_SECRETS_DIR. Production uses the
    cloud secret manager through the same three methods; only the *reference* is kept in the database."""

    def __init__(self, root: str | None = None):
        self._root = root

    def _path(self, ref: str) -> pathlib.Path:
        root = pathlib.Path(self._root or os.environ.get("BIZ_SECRETS_DIR") or pathlib.Path(__file__).resolve().parents[2] / "var" / "secrets")
        p = (root / (hashlib.sha256(ref.encode()).hexdigest() + ".json.enc")).resolve()
        p.parent.mkdir(parents=True, exist_ok=True)
        return p

    def put(self, ref: str, secret: dict) -> None:
        self._path(ref).write_bytes(secretbox.encrypt(json.dumps(secret)))

    def get(self, ref: str) -> dict | None:
        p = self._path(ref)
        return json.loads(secretbox.decrypt(p.read_bytes())) if p.is_file() else None

    def delete(self, ref: str) -> None:
        p = self._path(ref)
        if p.is_file():
            p.unlink()


secrets = LocalSecretStore()


def webhook_signature(secret: str, body: bytes) -> str:
    return hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
