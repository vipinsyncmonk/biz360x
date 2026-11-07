"""Legal and secretarial: contracts and their terms, templates, board meetings and resolutions, signature requests, notices.

Document generation (a draft notice, a filled template, a reply skeleton) writes to the vault through `store_document`.
Term extraction is pattern-based until the copilot arrives (M9) and says so in `extracted_by` / `confidence`.
"""
from __future__ import annotations

import datetime as dt
import json
import re
import uuid
from decimal import Decimal
from typing import Literal

from fastapi import APIRouter, Header, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from . import storage
from .core import ApiError, Auth, Principal, audit, emit, idempotency_begin, idempotency_finish, jsonable, money, require, tx
from .routes_orders import js, offset_of, page
from .routes_vault import store_document

router = APIRouter()
E = "/entities/{entity_id}"
KINDS = ("customer", "vendor", "property", "employment", "nda", "consultant", "licence", "loan", "shareholder", "other")
STATUSES = ("draft", "negotiating", "out_for_signature", "active", "expired", "terminated", "renewed")


# ================================================================== pure helpers
def contract_alerts(c: dict, udyam_class: str | None, today: dt.date) -> list[dict]:
    """What a person should look at on a contract today."""
    out: list[dict] = []

    def alert(code, level, title, detail):
        out.append({"code": code, "level": level, "title": title, "detail": detail})

    if c["status"] == "active" and c["auto_renews"] and c.get("notice_deadline"):
        days = (c["notice_deadline"] - today).days
        months = c.get("renewal_months") or 12
        if 0 <= days <= 30:
            alert("notice_window", "warn", f"Notice window closes in {days} day(s)",
                  f"The contract renews for {months} months unless notice is given by {c['notice_deadline']:%d %b %Y}.")
        elif days < 0 and c.get("end_date") and c["end_date"] >= today:
            alert("notice_window_passed", "info", "Notice window has passed", f"It renews on {c['end_date']:%d %b %Y}; the next chance to act is the following term.")
    if c["kind"] == "customer" and (c.get("payment_terms_days") or 0) > 45 and udyam_class in ("micro", "small"):
        alert("msme_terms", "warn", "Payment terms are longer than 45 days",
              "As a micro or small enterprise, buyers must pay within 45 days of acceptance whatever the contract says (MSMED Act s.15).")
    if c["status"] == "active" and not c["auto_renews"] and c.get("end_date") and 0 <= (c["end_date"] - today).days <= 90:
        alert("expiring", "info", "Expires soon", f"Ends on {c['end_date']:%d %b %Y} and does not renew by itself.")
    if c["status"] == "active" and not c.get("signed_document_id"):
        alert("no_signed_copy", "warn", "No signed copy on file", "Upload the signed document so terms can be read and the record is complete.")
    if c["kind"] in ("property", "loan") and c.get("stamp_duty_paid") is None and c["status"] in ("active", "out_for_signature"):
        alert("stamp_duty", "info", "Stamp duty not recorded", "Record the stamp duty paid, or confirm that the instrument was exempt.")
    return out


PLACEHOLDER = re.compile(r"\{\{\s*([a-z_]+)\s*\}\}")
AUTO_FIELDS = {"counterparty_name", "company_name", "today"}

TEMPLATE_BODIES = {
    "Mutual NDA": """DRAFT - NOT REVIEWED BY COUNSEL
MUTUAL NON-DISCLOSURE AGREEMENT

This agreement is made on {{today}} between {{company_name}} and {{counterparty_name}}.

1. Each party may disclose confidential information to the other for {{purpose}}.
2. The receiving party shall keep it confidential and use it only for that purpose.
3. This agreement lasts {{term_years}} years; confidentiality survives for {{survival_years}} years after it ends.
4. This agreement is governed by the laws of India. Disputes go to arbitration in {{arbitration_seat}}.
""",
    "Master services agreement": """DRAFT - NOT REVIEWED BY COUNSEL
MASTER SERVICES AGREEMENT

Between {{company_name}} (the Supplier) and {{counterparty_name}} (the Customer), dated {{today}}.

1. The Supplier shall provide the services in each Statement of Work.
2. Fees are as stated in each Statement of Work. The Customer shall pay within {{payment_terms_days}} days of invoice.
3. This agreement starts on {{start_date}} and continues until {{end_date}}, and renews for {{renewal_months}} months unless either party gives {{notice_days}} days' written notice.
4. Liability is capped at {{liability_cap}}.
5. This agreement is governed by the laws of India. Disputes go to arbitration in {{arbitration_seat}}.
""",
    "Supply agreement": """DRAFT - NOT REVIEWED BY COUNSEL
SUPPLY AGREEMENT

Between {{company_name}} (the Supplier) and {{counterparty_name}} (the Buyer), dated {{today}}.

1. The Supplier shall supply the goods in the price list attached, at the prices stated there.
2. Payment is due within {{payment_terms_days}} days of acceptance of the goods. Where the Supplier is a micro or small enterprise, payment shall in no case be made later than 45 days from the day of acceptance (MSMED Act 2006, s.15).
3. This agreement starts on {{start_date}} and ends on {{end_date}}. It renews for {{renewal_months}} months unless either party gives {{notice_days}} days' written notice.
4. This agreement is governed by the laws of India. Disputes go to arbitration in {{arbitration_seat}}.
""",
    "Offer letter and employment": """DRAFT - NOT REVIEWED BY COUNSEL
OFFER OF EMPLOYMENT

{{today}}

Dear {{counterparty_name}},

{{company_name}} is pleased to offer you the position of {{designation}}, starting on {{start_date}}, at an annual cost to company of {{ctc_annual}}.

Your employment is subject to the terms of the Company's employment agreement and policies, including confidentiality and assignment of inventions made in the course of your work.
""",
    "Consultant agreement": """DRAFT - NOT REVIEWED BY COUNSEL
CONSULTANT AGREEMENT

Between {{company_name}} and {{counterparty_name}} (the Consultant), dated {{today}}.

1. The Consultant shall provide: {{scope}}.
2. Fees: {{fee}}, payable within {{payment_terms_days}} days of invoice, subject to deduction of tax at source.
3. Work product, including all intellectual property in it, belongs to {{company_name}} on payment.
4. Either party may end this agreement on {{notice_days}} days' written notice.
5. This agreement is governed by the laws of India. Disputes go to arbitration in {{arbitration_seat}}.
""",
}


def render_template(body: str, values: dict[str, str]) -> tuple[str, list[str]]:
    names = PLACEHOLDER.findall(body)
    missing = sorted({n for n in names if not str(values.get(n, "")).strip()})
    return PLACEHOLDER.sub(lambda m: str(values.get(m.group(1), "")), body), missing


TERM_PATTERNS = [
    ("payment", re.compile(r"[^.]*\b(?:payment|pay|invoice)\b[^.]{0,80}?\b(\d{1,3})\s*days[^.]*\.", re.I)),
    ("notice", re.compile(r"[^.]*\b(\d{1,3})\s*days['’]?\s*(?:prior\s+)?(?:written\s+)?notice[^.]*\.", re.I)),
    ("renewal", re.compile(r"[^.]*\b(?:auto[- ]?renew\w*|renews?)\b[^.]{0,80}?\b(\d{1,2})\s*months[^.]*\.", re.I)),
    ("governing_law", re.compile(r"[^.]*\bgoverned by the laws? of ([A-Za-z ]{3,30})[^.]*\.", re.I)),
    ("liability_cap", re.compile(r"[^.]*\bliability\b[^.]{0,60}?\bcapped\b[^.]*\.", re.I)),
]


def extract_terms(text: str) -> list[dict]:
    out = []
    for key, rx in TERM_PATTERNS:
        m = rx.search(text)
        if m:
            sentence = re.sub(r"\s+", " ", m.group(0)).strip()
            out.append({"term_key": key, "value_text": sentence, "value_json": {"match": m.group(1)} if m.groups() else None})
    return out


# ================================================================== contracts
class ContractIn(BaseModel):
    party_id: uuid.UUID | None = None
    counterparty_name: str = Field(min_length=1, max_length=200)
    title: str = Field(min_length=1, max_length=200)
    kind: Literal["customer", "vendor", "property", "employment", "nda", "consultant", "licence", "loan", "shareholder", "other"]
    template_id: uuid.UUID | None = None
    start_date: dt.date | None = None
    end_date: dt.date | None = None
    auto_renews: bool = False
    renewal_months: int | None = Field(default=None, ge=1, le=120)
    notice_days: int | None = Field(default=None, ge=0, le=365)
    value_amount: Decimal | None = Field(default=None, ge=0)
    value_period: Literal["one_time", "monthly", "annual", "rate_card"] | None = None
    payment_terms_days: int | None = Field(default=None, ge=0, le=365)
    governing_law: str | None = None
    dispute_forum: str | None = None
    stamp_state: str | None = Field(default=None, pattern=r"^[0-9]{2}$")
    stamp_duty_paid: Decimal | None = Field(default=None, ge=0)
    registered_on: dt.date | None = None
    status: Literal["draft", "negotiating", "out_for_signature", "active", "expired", "terminated", "renewed"] = "draft"
    signed_document_id: uuid.UUID | None = None


CONTRACT_COLS = ("party_id", "counterparty_name", "title", "kind", "template_id", "start_date", "end_date", "auto_renews", "renewal_months", "notice_days",
                 "value_amount", "value_period", "payment_terms_days", "governing_law", "dispute_forum", "stamp_state", "stamp_duty_paid", "registered_on",
                 "status", "signed_document_id")


def load_contract(cur, entity_id, cid, today: dt.date | None = None) -> dict:
    cur.execute(f"SELECT id, {', '.join(CONTRACT_COLS)}, notice_deadline FROM contracts WHERE id = %s AND entity_id = %s", (cid, entity_id))
    c = cur.fetchone()
    if not c:
        raise ApiError(404, "not_found", "Not found")
    cur.execute("SELECT term_key, value_text, value_json, source_page, extracted_by, confidence, verified_by IS NOT NULL AS verified FROM contract_terms WHERE contract_id = %s ORDER BY term_key", (cid,))
    c["terms"] = [{**t, "confidence": float(t["confidence"]) if t["confidence"] is not None else None} for t in cur.fetchall()]
    cur.execute("SELECT udyam_class::text FROM entities WHERE id = %s", (entity_id,))
    c["alerts"] = contract_alerts(c, cur.fetchone()["udyam_class"], today or dt.date.today())
    return js(c)


def _check_refs(cur, entity_id, body: ContractIn) -> None:
    if body.party_id:
        cur.execute("SELECT 1 FROM parties WHERE id = %s AND entity_id = %s", (body.party_id, entity_id))
        if not cur.fetchone():
            raise ApiError(400, "validation_failed", "party_id is not a party of this entity")
    if body.signed_document_id:
        cur.execute("SELECT 1 FROM documents WHERE id = %s AND entity_id = %s", (body.signed_document_id, entity_id))
        if not cur.fetchone():
            raise ApiError(400, "validation_failed", "signed_document_id is not a document of this entity")
    if body.start_date and body.end_date and body.end_date < body.start_date:
        raise ApiError(400, "validation_failed", "end_date is before start_date")
    if body.auto_renews and (body.notice_days is None or body.end_date is None):
        raise ApiError(400, "validation_failed", "An auto-renewing contract needs an end date and a notice period")


def _insert_contract(cur, entity_id, body: ContractIn) -> uuid.UUID:
    _check_refs(cur, entity_id, body)
    cols = ", ".join(CONTRACT_COLS)
    cur.execute(f"INSERT INTO contracts (entity_id, {cols}, owner_user_id) VALUES (%s, {', '.join(['%s'] * len(CONTRACT_COLS))}, app.current_user_id()) RETURNING id",
                (entity_id, *[getattr(body, c) for c in CONTRACT_COLS]))
    return cur.fetchone()["id"]


@router.post(E + "/contracts", status_code=201, tags=["Legal & board"])
def create_contract(entity_id: uuid.UUID, body: ContractIn, p: Principal = Auth):
    with tx(p.user_id) as cur:
        require(cur, entity_id, "legal:write")
        cid = _insert_contract(cur, entity_id, body)
        audit(cur, entity_id, "contract.created", "contracts", cid, after={"title": body.title})
        return load_contract(cur, entity_id, cid)


@router.get(E + "/contracts", tags=["Legal & board"])
def list_contracts(entity_id: uuid.UUID, p: Principal = Auth, kind: str | None = None, status: str | None = None,
                   notice_within_days: int | None = Query(default=None, ge=0, le=730), limit: int = Query(default=50, ge=1, le=200), cursor: str | None = None):
    off = offset_of(cursor)
    with tx(p.user_id) as cur:
        require(cur, entity_id, "legal:read")
        cur.execute("""SELECT id FROM contracts WHERE entity_id = %s AND (%s::text IS NULL OR kind = %s) AND (%s::text IS NULL OR status = %s)
                          AND (%s::int IS NULL OR (status = 'active' AND auto_renews AND notice_deadline BETWEEN current_date AND current_date + %s::int))
                        ORDER BY coalesce(notice_deadline, end_date), title, id LIMIT %s OFFSET %s""",
                    (entity_id, kind, kind, status, status, notice_within_days, notice_within_days, limit + 1, off))
        return page([load_contract(cur, entity_id, r["id"]) for r in cur.fetchall()], limit, off)


@router.get(E + "/contracts/{contract_id}", tags=["Legal & board"])
def get_contract(entity_id: uuid.UUID, contract_id: uuid.UUID, p: Principal = Auth):
    with tx(p.user_id) as cur:
        require(cur, entity_id, "legal:read")
        return load_contract(cur, entity_id, contract_id)


@router.patch(E + "/contracts/{contract_id}", tags=["Legal & board"])
def update_contract(entity_id: uuid.UUID, contract_id: uuid.UUID, body: dict, p: Principal = Auth):
    with tx(p.user_id) as cur:
        require(cur, entity_id, "legal:write")
        cur.execute(f"SELECT {', '.join(CONTRACT_COLS)} FROM contracts WHERE id = %s AND entity_id = %s FOR UPDATE", (contract_id, entity_id))
        c = cur.fetchone()
        if not c:
            raise ApiError(404, "not_found", "Not found")
        try:
            new = ContractIn(**{**{k: (str(v) if isinstance(v, (uuid.UUID,)) else v) for k, v in c.items()}, **{k: v for k, v in body.items() if k in CONTRACT_COLS}})
        except Exception as e:
            raise ApiError(400, "validation_failed", "Invalid contract", str(e))
        _check_refs(cur, entity_id, new)
        cur.execute(f"UPDATE contracts SET {', '.join(f'{k} = %s' for k in CONTRACT_COLS)} WHERE id = %s", (*[getattr(new, k) for k in CONTRACT_COLS], contract_id))
        audit(cur, entity_id, "contract.updated", "contracts", contract_id, after={k: v for k, v in body.items() if k in CONTRACT_COLS})
        return load_contract(cur, entity_id, contract_id)


class TermIn(BaseModel):
    value_text: str = Field(min_length=1, max_length=2000)
    value_json: dict | None = None
    source_page: int | None = Field(default=None, ge=1)
    extracted_by: Literal["user", "copilot"] = "user"
    confidence: float | None = Field(default=None, ge=0, le=1)
    verified: bool = False


@router.put(E + "/contracts/{contract_id}/terms/{term_key}", tags=["Legal & board"])
def put_term(entity_id: uuid.UUID, contract_id: uuid.UUID, term_key: str, body: TermIn, p: Principal = Auth):
    if not re.fullmatch(r"[a-z][a-z0-9_]{1,39}", term_key):
        raise ApiError(400, "validation_failed", "term_key must be lower-case letters, digits and underscores")
    with tx(p.user_id) as cur:
        require(cur, entity_id, "legal:write")
        load_contract(cur, entity_id, contract_id)
        cur.execute("""INSERT INTO contract_terms (contract_id, term_key, value_text, value_json, source_page, extracted_by, confidence, verified_by)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, CASE WHEN %s THEN app.current_user_id() END)
                       ON CONFLICT (contract_id, term_key) DO UPDATE SET value_text = EXCLUDED.value_text, value_json = EXCLUDED.value_json,
                              source_page = EXCLUDED.source_page, extracted_by = EXCLUDED.extracted_by, confidence = EXCLUDED.confidence, verified_by = EXCLUDED.verified_by""",
                    (contract_id, term_key, body.value_text, json.dumps(body.value_json) if body.value_json is not None else None, body.source_page,
                     "user" if body.verified else body.extracted_by, body.confidence, body.verified))
        audit(cur, entity_id, "contract_term.set", "contracts", contract_id, after={"term": term_key, "verified": body.verified})
        return next(t for t in load_contract(cur, entity_id, contract_id)["terms"] if t["term_key"] == term_key)


def _queue(cur, entity_id, kind: str, subject: str, subject_id, payload: dict | None = None) -> JSONResponse:
    cur.execute("""INSERT INTO jobs (entity_id, kind, subject, subject_id, payload, created_by) VALUES (%s, %s, %s, %s, %s, app.current_user_id())
                   RETURNING id, kind, status, progress, subject, subject_id, result, created_at, finished_at""",
                (entity_id, kind, subject, subject_id, json.dumps(payload or {}, default=jsonable)))
    job = js(cur.fetchone())
    return JSONResponse(job, status_code=202, headers={"Location": f"/v1/jobs/{job['id']}"})


@router.post(E + "/contracts/{contract_id}/extract-terms", status_code=202, tags=["Legal & board"])
def extract_terms_endpoint(entity_id: uuid.UUID, contract_id: uuid.UUID, p: Principal = Auth):
    with tx(p.user_id) as cur:
        require(cur, entity_id, "legal:write")
        c = load_contract(cur, entity_id, contract_id)
        if not c["signed_document_id"]:
            raise ApiError(409, "conflict", "There is no signed copy to read; upload it and set signed_document_id first")
        return _queue(cur, entity_id, "legal.extract_terms", "contracts", contract_id)


def handle_extract_terms(cur, job: dict) -> dict:
    cur.execute("SELECT c.id, d.extracted_text FROM contracts c JOIN documents d ON d.id = c.signed_document_id WHERE c.id = %s", (job["subject_id"],))
    r = cur.fetchone()
    if not r or not r["extracted_text"]:
        return {"terms": 0, "note": "The signed copy has no text layer yet (needs OCR), so nothing was extracted"}
    found = extract_terms(r["extracted_text"])
    for t in found:
        cur.execute("""INSERT INTO contract_terms (contract_id, term_key, value_text, value_json, extracted_by, confidence) VALUES (%s, %s, %s, %s, 'copilot', 0.5)
                       ON CONFLICT (contract_id, term_key) DO UPDATE SET value_text = EXCLUDED.value_text, value_json = EXCLUDED.value_json
                        WHERE contract_terms.verified_by IS NULL""", (r["id"], t["term_key"], t["value_text"], json.dumps(t["value_json"]) if t["value_json"] else None))
    audit(cur, job["entity_id"], "contract.terms_extracted", "contracts", r["id"], after={"terms": [t["term_key"] for t in found]}, actor_kind="engine")
    return {"terms": len(found), "keys": [t["term_key"] for t in found], "method": "pattern matching (confidence 0.5); verify each term"}


class NoticeRequest(BaseModel):
    kind: Literal["non_renewal", "renegotiation", "termination"]


@router.post(E + "/contracts/{contract_id}/notices", status_code=202, tags=["Legal & board"])
def draft_contract_notice(entity_id: uuid.UUID, contract_id: uuid.UUID, body: NoticeRequest, p: Principal = Auth):
    with tx(p.user_id) as cur:
        require(cur, entity_id, "legal:write")
        c = load_contract(cur, entity_id, contract_id)
        if c["status"] not in ("active", "negotiating"):
            raise ApiError(409, "conflict", f"A notice cannot be drafted for a {c['status']} contract")
        return _queue(cur, entity_id, "legal.draft_notice", "contracts", contract_id, {"kind": body.kind})


NOTICE_TEXT = {
    "non_renewal": "We write to give notice that {company} does not wish to renew the agreement \"{title}\" with {counterparty}. It will end on {end} and will not renew.",
    "renegotiation": "We write regarding the agreement \"{title}\" with {counterparty}, which is due to renew on {end}. Before it does, we would like to renegotiate its terms, in particular payment terms and pricing.",
    "termination": "We write to give notice of termination of the agreement \"{title}\" with {counterparty} in accordance with its terms.",
}


def handle_draft_notice(cur, job: dict) -> dict:
    entity_id = job["entity_id"]
    c = load_contract(cur, entity_id, job["subject_id"])
    cur.execute("SELECT legal_name FROM entities WHERE id = %s", (entity_id,))
    company = cur.fetchone()["legal_name"]
    kind = job["payload"]["kind"]
    body = NOTICE_TEXT[kind].format(company=company, title=c["title"], counterparty=c["counterparty_name"], end=c["end_date"] or "its end date")
    deadline = f"The notice must reach {c['counterparty_name']} by {c['notice_deadline']}." if c["notice_deadline"] else ""
    text = (f"DRAFT - REVIEW BEFORE SENDING\n\n{dt.date.today():%d %B %Y}\n\nTo: {c['counterparty_name']}\nFrom: {company}\n\n"
            f"Subject: {kind.replace('_', ' ').title()} - {c['title']}\n\n{body}\n{deadline}\n\nYours faithfully,\n{company}\n")
    doc = store_document(cur, entity_id, folder="contracts", title=f"Draft {kind.replace('_', '-')} notice - {c['title']}", file_name=f"notice-{kind}.txt",
                         mime="text/plain", data=text.encode(), links=[("contracts", job["subject_id"], "draft")])
    cur.execute("INSERT INTO tasks (entity_id, title, subject, subject_id, due_on, source) VALUES (%s, %s, 'contract', %s, %s, 'engine')",
                (entity_id, f"Review and send the {kind.replace('_', ' ')} notice to {c['counterparty_name']}", job["subject_id"], c["notice_deadline"]))
    return {"document_id": str(doc)}


# ================================================================== templates
@router.get(E + "/contract-templates", tags=["Legal & board"])
def list_templates(entity_id: uuid.UUID, p: Principal = Auth):
    with tx(p.user_id) as cur:
        require(cur, entity_id, "legal:read")
        cur.execute("""SELECT id, name, kind, version, reviewed_by, reviewed_on, (entity_id IS NULL) AS global FROM contract_templates
                        WHERE is_active AND (entity_id IS NULL OR entity_id = %s) ORDER BY name, version DESC""", (entity_id,))
        return js(cur.fetchall())


class TemplateFill(BaseModel):
    party_id: uuid.UUID
    fields: dict[str, str | int | float] = {}


@router.post(E + "/contract-templates/{template_id}/generate", status_code=201, tags=["Legal & board"])
def generate_from_template(entity_id: uuid.UUID, template_id: uuid.UUID, body: TemplateFill, p: Principal = Auth):
    with tx(p.user_id) as cur:
        require(cur, entity_id, "legal:write")
        cur.execute("SELECT * FROM contract_templates WHERE id = %s AND is_active AND (entity_id IS NULL OR entity_id = %s)", (template_id, entity_id))
        t = cur.fetchone()
        if not t:
            raise ApiError(404, "not_found", "Not found")
        text_body = TEMPLATE_BODIES.get(t["name"])
        if text_body is None:
            raise ApiError(409, "conflict", f"The template '{t['name']}' has no text on file")
        cur.execute("SELECT legal_name FROM parties WHERE id = %s AND entity_id = %s", (body.party_id, entity_id))
        party = cur.fetchone()
        cur.execute("SELECT legal_name FROM entities WHERE id = %s", (entity_id,))
        company = cur.fetchone()["legal_name"]
        if not party:
            raise ApiError(400, "validation_failed", "party_id is not a party of this entity")
        values = {**{k: str(v) for k, v in body.fields.items()}, "counterparty_name": party["legal_name"], "company_name": company, "today": f"{dt.date.today():%d %B %Y}"}
        text, missing = render_template(text_body, values)
        if missing:
            raise ApiError(400, "validation_failed", "Fill in every field of the template", errors=[{"field": f"fields.{m}", "message": "required"} for m in missing])

        def date_of(k):
            try:
                return dt.date.fromisoformat(values[k]) if k in values else None
            except ValueError:
                raise ApiError(400, "validation_failed", f"{k} must be YYYY-MM-DD", errors=[{"field": f"fields.{k}", "message": "bad date"}])

        def intval(k):
            return int(float(values[k])) if k in values and re.fullmatch(r"\d+(\.0+)?", values[k]) else None

        kind = t["kind"] if t["kind"] in KINDS else "other"
        notice_days, months = intval("notice_days"), intval("renewal_months")
        doc_id = store_document(cur, entity_id, folder="contracts", title=f"{t['name']} - {party['legal_name']}", file_name=f"{t['name'].lower().replace(' ', '-')}.txt",
                                mime="text/plain", data=text.encode())
        cid = _insert_contract(cur, entity_id, ContractIn(
            party_id=body.party_id, counterparty_name=party["legal_name"], title=f"{t['name']} - {party['legal_name']}", kind=kind, template_id=template_id,
            start_date=date_of("start_date"), end_date=date_of("end_date"), auto_renews=bool(months and notice_days and date_of("end_date")),
            renewal_months=months, notice_days=notice_days, payment_terms_days=intval("payment_terms_days"), governing_law="India", status="draft"))
        from .routes_vault import link_document
        link_document(cur, doc_id, "contracts", cid, "draft")
        audit(cur, entity_id, "contract.generated", "contracts", cid, after={"template": t["name"], "version": t["version"], "document": doc_id})
        return load_contract(cur, entity_id, cid)


# ================================================================== meetings
class MeetingIn(BaseModel):
    kind: Literal["board", "agm", "egm", "committee", "partners"]
    scheduled_for: dt.datetime
    held_on: dt.date | None = None
    notice_sent_on: dt.date | None = None
    quorum_met: bool | None = None
    minutes_document_id: uuid.UUID | None = None


def load_meeting(cur, entity_id, mid) -> dict:
    cur.execute("""SELECT id, kind, scheduled_for, held_on, notice_sent_on, quorum_met, minutes_document_id FROM meetings WHERE id = %s AND entity_id = %s""", (mid, entity_id))
    m = cur.fetchone()
    if not m:
        raise ApiError(404, "not_found", "Not found")
    cur.execute("SELECT id, meeting_id, number, title, kind, passed_on, needs_mgt14, status, signature_request_id FROM resolutions WHERE meeting_id = %s ORDER BY number", (mid,))
    m["resolutions"] = cur.fetchall()
    m["days_since_last_board"] = None
    if m["kind"] == "board":
        when = m["held_on"] or m["scheduled_for"].date()
        cur.execute("SELECT %s::date - max(held_on) AS gap FROM meetings WHERE entity_id = %s AND kind = 'board' AND held_on < %s::date AND id <> %s",
                    (when, entity_id, when, mid))
        m["days_since_last_board"] = cur.fetchone()["gap"]
    return js(m)


def _check_meeting(cur, entity_id, body: MeetingIn) -> None:
    if body.held_on and body.held_on > dt.date.today():
        raise ApiError(400, "validation_failed", "held_on cannot be in the future")
    if body.quorum_met is not None and not body.held_on:
        raise ApiError(400, "validation_failed", "Quorum can only be recorded for a meeting that was held")
    if body.notice_sent_on and body.notice_sent_on > (body.held_on or body.scheduled_for.date()):
        raise ApiError(400, "validation_failed", "notice_sent_on is after the meeting")
    if body.minutes_document_id:
        cur.execute("SELECT 1 FROM documents WHERE id = %s AND entity_id = %s", (body.minutes_document_id, entity_id))
        if not cur.fetchone():
            raise ApiError(400, "validation_failed", "minutes_document_id is not a document of this entity")


@router.post(E + "/meetings", status_code=201, tags=["Legal & board"])
def create_meeting(entity_id: uuid.UUID, body: MeetingIn, p: Principal = Auth):
    with tx(p.user_id) as cur:
        require(cur, entity_id, "legal:write")
        _check_meeting(cur, entity_id, body)
        cur.execute("""INSERT INTO meetings (entity_id, kind, scheduled_for, held_on, notice_sent_on, quorum_met, minutes_document_id) VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING id""",
                    (entity_id, body.kind, body.scheduled_for, body.held_on, body.notice_sent_on, body.quorum_met, body.minutes_document_id))
        mid = cur.fetchone()["id"]
        audit(cur, entity_id, "meeting.created", "meetings", mid, after={"kind": body.kind})
        return load_meeting(cur, entity_id, mid)


@router.get(E + "/meetings", tags=["Legal & board"])
def list_meetings(entity_id: uuid.UUID, p: Principal = Auth, kind: str | None = None, from_: dt.date | None = Query(default=None, alias="from"), to: dt.date | None = None,
                  limit: int = Query(default=50, ge=1, le=200), cursor: str | None = None):
    off = offset_of(cursor)
    with tx(p.user_id) as cur:
        require(cur, entity_id, "legal:read")
        cur.execute("""SELECT id FROM meetings WHERE entity_id = %s AND (%s::text IS NULL OR kind = %s) AND (%s::date IS NULL OR scheduled_for::date >= %s)
                          AND (%s::date IS NULL OR scheduled_for::date <= %s) ORDER BY scheduled_for DESC, id LIMIT %s OFFSET %s""",
                    (entity_id, kind, kind, from_, from_, to, to, limit + 1, off))
        return page([load_meeting(cur, entity_id, r["id"]) for r in cur.fetchall()], limit, off)


@router.get(E + "/meetings/{meeting_id}", tags=["Legal & board"])
def get_meeting(entity_id: uuid.UUID, meeting_id: uuid.UUID, p: Principal = Auth):
    with tx(p.user_id) as cur:
        require(cur, entity_id, "legal:read")
        return load_meeting(cur, entity_id, meeting_id)


@router.patch(E + "/meetings/{meeting_id}", tags=["Legal & board"])
def update_meeting(entity_id: uuid.UUID, meeting_id: uuid.UUID, body: dict, p: Principal = Auth):
    with tx(p.user_id) as cur:
        require(cur, entity_id, "legal:write")
        cur.execute("SELECT kind, scheduled_for, held_on, notice_sent_on, quorum_met, minutes_document_id FROM meetings WHERE id = %s AND entity_id = %s FOR UPDATE", (meeting_id, entity_id))
        m = cur.fetchone()
        if not m:
            raise ApiError(404, "not_found", "Not found")
        try:
            new = MeetingIn(**{**{k: (str(v) if isinstance(v, uuid.UUID) else v) for k, v in m.items()},
                               **{k: v for k, v in body.items() if k in m}})
        except Exception as e:
            raise ApiError(400, "validation_failed", "Invalid meeting", str(e))
        _check_meeting(cur, entity_id, new)
        cur.execute("UPDATE meetings SET kind = %s, scheduled_for = %s, held_on = %s, notice_sent_on = %s, quorum_met = %s, minutes_document_id = %s WHERE id = %s",
                    (new.kind, new.scheduled_for, new.held_on, new.notice_sent_on, new.quorum_met, new.minutes_document_id, meeting_id))
        audit(cur, entity_id, "meeting.updated", "meetings", meeting_id, after={k: v for k, v in body.items() if k in m})
        return load_meeting(cur, entity_id, meeting_id)


# ================================================================== resolutions
class ResolutionIn(BaseModel):
    meeting_id: uuid.UUID | None = None
    number: str = Field(min_length=1, max_length=40)
    title: str = Field(min_length=1, max_length=300)
    kind: Literal["board", "ordinary", "special", "circular"]
    passed_on: dt.date | None = None
    status: Literal["draft", "circulated", "passed", "failed", "withdrawn"] = "draft"
    signature_request_id: uuid.UUID | None = None


RES_COLS = "id, meeting_id, number, title, kind, passed_on, needs_mgt14, mgt14_obligation_id, status, signature_request_id"
TRANSITIONS = {"draft": {"circulated", "passed", "withdrawn"}, "circulated": {"passed", "failed", "withdrawn"}, "passed": set(), "failed": set(), "withdrawn": set()}


def load_resolution(cur, entity_id, rid) -> dict:
    cur.execute(f"SELECT {RES_COLS} FROM resolutions WHERE id = %s AND entity_id = %s", (rid, entity_id))
    r = cur.fetchone()
    if not r:
        raise ApiError(404, "not_found", "Not found")
    return js(r)


def _check_resolution_refs(cur, entity_id, body: ResolutionIn):
    if body.meeting_id:
        cur.execute("SELECT 1 FROM meetings WHERE id = %s AND entity_id = %s", (body.meeting_id, entity_id))
        if not cur.fetchone():
            raise ApiError(400, "validation_failed", "meeting_id is not a meeting of this entity")
    if body.signature_request_id:
        cur.execute("SELECT 1 FROM signature_requests WHERE id = %s AND entity_id = %s", (body.signature_request_id, entity_id))
        if not cur.fetchone():
            raise ApiError(400, "validation_failed", "signature_request_id is not a request of this entity")


@router.post(E + "/resolutions", status_code=201, tags=["Legal & board"])
def create_resolution(entity_id: uuid.UUID, body: ResolutionIn, p: Principal = Auth):
    if body.status != "draft":
        raise ApiError(400, "validation_failed", "A resolution starts as a draft")
    with tx(p.user_id) as cur:
        require(cur, entity_id, "legal:write")
        _check_resolution_refs(cur, entity_id, body)
        cur.execute("INSERT INTO resolutions (entity_id, meeting_id, number, title, kind, needs_mgt14, signature_request_id) VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING id",
                    (entity_id, body.meeting_id, body.number, body.title, body.kind, body.kind == "special", body.signature_request_id))
        rid = cur.fetchone()["id"]
        audit(cur, entity_id, "resolution.drafted", "resolutions", rid, after={"number": body.number})
        return load_resolution(cur, entity_id, rid)


@router.get(E + "/resolutions", tags=["Legal & board"])
def list_resolutions(entity_id: uuid.UUID, p: Principal = Auth, status: str | None = None, limit: int = Query(default=50, ge=1, le=200), cursor: str | None = None):
    off = offset_of(cursor)
    with tx(p.user_id) as cur:
        require(cur, entity_id, "legal:read")
        cur.execute(f"SELECT {RES_COLS} FROM resolutions WHERE entity_id = %s AND (%s::text IS NULL OR status = %s) ORDER BY number LIMIT %s OFFSET %s",
                    (entity_id, status, status, limit + 1, off))
        return page(js(cur.fetchall()), limit, off)


@router.get(E + "/resolutions/{resolution_id}", tags=["Legal & board"])
def get_resolution(entity_id: uuid.UUID, resolution_id: uuid.UUID, p: Principal = Auth):
    with tx(p.user_id) as cur:
        require(cur, entity_id, "legal:read")
        return load_resolution(cur, entity_id, resolution_id)


def _pass_resolution(cur, entity_id, r: dict, passed_on: dt.date) -> uuid.UUID | None:
    """Conditions for passing, then the MGT-14 obligation for resolutions that must be filed."""
    if r["meeting_id"]:
        cur.execute("SELECT kind, held_on, quorum_met FROM meetings WHERE id = %s", (r["meeting_id"],))
        m = cur.fetchone()
        if not m["held_on"] or m["held_on"] > passed_on:
            raise ApiError(409, "conflict", "The meeting has not been held on or before the date the resolution is passed")
        if not m["quorum_met"]:
            raise ApiError(409, "conflict", "Quorum was not recorded for the meeting, so the resolution cannot be passed")
    elif r["kind"] == "circular" or r["signature_request_id"]:
        if not r["signature_request_id"]:
            raise ApiError(409, "conflict", "A resolution by circulation needs a completed signature request")
        cur.execute("SELECT status FROM signature_requests WHERE id = %s", (r["signature_request_id"],))
        if cur.fetchone()["status"] != "completed":
            raise ApiError(409, "conflict", "Every director must sign before a circular resolution is passed")
    if not r["needs_mgt14"]:
        return None
    cur.execute("SELECT id, version FROM compliance_rules WHERE code = 'MCA.MGT14' AND effective @> %s::date", (passed_on,))
    rule = cur.fetchone()
    if not rule:
        return None
    cur.execute("SELECT app.fy_of(%s::date) AS fy", (passed_on,))
    fy = cur.fetchone()["fy"]
    cur.execute("""INSERT INTO obligations (entity_id, rule_id, fy, period_start, period_end, period_label, due_date, status, reason_snapshot)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, 'upcoming', %s)
                   ON CONFLICT (entity_id, rule_id, coalesce(registration_id, '00000000-0000-0000-0000-000000000000'::uuid), period_start) DO NOTHING RETURNING id""",
                (entity_id, rule["id"], fy, passed_on, passed_on, f"Resolution {r['number']}", passed_on + dt.timedelta(days=30),
                 json.dumps({"rule": {"code": "MCA.MGT14", "version": rule["version"]}, "event": "resolution", "resolution": r["number"], "passed_on": passed_on}, default=jsonable)))
    created = cur.fetchone()
    if created:
        emit(cur, entity_id, "obligation.created", "obligations", created["id"], {"rule": "MCA.MGT14", "period": f"Resolution {r['number']}"})
        return created["id"]
    cur.execute("SELECT id FROM obligations WHERE entity_id = %s AND rule_id = %s AND period_start = %s", (entity_id, rule["id"], passed_on))
    return cur.fetchone()["id"]


@router.patch(E + "/resolutions/{resolution_id}", tags=["Legal & board"])
def update_resolution(entity_id: uuid.UUID, resolution_id: uuid.UUID, body: dict, p: Principal = Auth):
    with tx(p.user_id) as cur:
        require(cur, entity_id, "legal:write")
        cur.execute(f"SELECT {RES_COLS} FROM resolutions WHERE id = %s AND entity_id = %s FOR UPDATE", (resolution_id, entity_id))
        r = cur.fetchone()
        if not r:
            raise ApiError(404, "not_found", "Not found")
        if r["status"] in ("passed", "failed", "withdrawn") and any(k in body for k in ("title", "kind", "meeting_id", "number", "status")):
            raise ApiError(409, "conflict", f"A {r['status']} resolution can no longer be changed")
        try:
            new = ResolutionIn(**{**{k: (str(v) if isinstance(v, uuid.UUID) else v) for k, v in r.items() if k in ResolutionIn.model_fields},
                                  **{k: v for k, v in body.items() if k in ResolutionIn.model_fields}})
        except Exception as e:
            raise ApiError(400, "validation_failed", "Invalid resolution", str(e))
        _check_resolution_refs(cur, entity_id, new)
        if new.status != r["status"] and new.status not in TRANSITIONS[r["status"]]:
            raise ApiError(409, "conflict", f"A {r['status']} resolution cannot become {new.status}")
        needs = new.kind == "special"
        passed_on, obligation = r["passed_on"], r["mgt14_obligation_id"]
        if new.status == "passed" and r["status"] != "passed":
            passed_on = new.passed_on or dt.date.today()
            obligation = _pass_resolution(cur, entity_id, {**r, "kind": new.kind, "needs_mgt14": needs, "meeting_id": new.meeting_id,
                                                           "signature_request_id": new.signature_request_id}, passed_on)
        cur.execute("""UPDATE resolutions SET meeting_id = %s, number = %s, title = %s, kind = %s, needs_mgt14 = %s, passed_on = %s, status = %s,
                              signature_request_id = %s, mgt14_obligation_id = %s WHERE id = %s""",
                    (new.meeting_id, new.number, new.title, new.kind, needs, passed_on, new.status, new.signature_request_id, obligation, resolution_id))
        audit(cur, entity_id, f"resolution.{new.status}" if new.status != r["status"] else "resolution.updated", "resolutions", resolution_id,
              after={"number": new.number, "mgt14_obligation": obligation})
        return load_resolution(cur, entity_id, resolution_id)


# ================================================================== signature requests
class SignerIn(BaseModel):
    person_id: uuid.UUID | None = None
    external_name: str | None = None
    external_email: str | None = Field(default=None, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
    sign_order: int = Field(default=1, ge=1, le=20)


class SignatureIn(BaseModel):
    subject: Literal["contract", "board_resolution", "notice_reply", "quote", "sales_document", "payroll_run", "journal_entry", "obligation"]
    subject_id: uuid.UUID
    document_id: uuid.UUID
    method: Literal["dsc", "aadhaar_esign", "click_to_sign"]
    signers: list[SignerIn] = Field(min_length=1, max_length=20)


SUBJECT_TABLE = {"contract": "contracts", "board_resolution": "resolutions", "obligation": "obligations", "quote": "quotes", "sales_document": "sales_documents",
                 "payroll_run": "payroll_runs", "journal_entry": "journal_entries", "notice_reply": "notices"}


def load_signature(cur, entity_id, sid) -> dict:
    cur.execute("SELECT id, subject::text, subject_id, document_id, method, status, sent_at, completed_at FROM signature_requests WHERE id = %s AND entity_id = %s", (sid, entity_id))
    s = cur.fetchone()
    if not s:
        raise ApiError(404, "not_found", "Not found")
    cur.execute("""SELECT g.id, g.person_id, g.external_name, g.external_email, g.sign_order, g.status, g.signed_at, p.full_name AS person_name FROM signature_signers g
                     LEFT JOIN persons p ON p.id = g.person_id WHERE g.request_id = %s ORDER BY g.sign_order, g.id""", (sid,))
    s["signers"] = cur.fetchall()
    return js(s)


@router.post(E + "/signature-requests", status_code=201, tags=["Approvals"])
def create_signature(entity_id: uuid.UUID, body: SignatureIn, p: Principal = Auth, idempotency_key: str | None = Header(default=None, alias="Idempotency-Key")):
    with tx(p.user_id) as cur:
        require(cur, entity_id, "approvals:write")
        replay = idempotency_begin(cur, idempotency_key, "POST", f"/entities/{entity_id}/signature-requests", body.model_dump(mode="json"))
        if replay:
            return JSONResponse(replay["body"], status_code=replay["status"])
        for s in body.signers:
            if not s.person_id and not s.external_email:
                raise ApiError(400, "validation_failed", "Each signer needs a person_id or an external_email")
            if s.person_id:
                cur.execute("SELECT 1 FROM persons WHERE id = %s AND entity_id = %s", (s.person_id, entity_id))
                if not cur.fetchone():
                    raise ApiError(400, "validation_failed", f"person {s.person_id} is not a person of this entity")
        cur.execute("SELECT 1 FROM documents WHERE id = %s AND entity_id = %s AND uploaded_at IS NOT NULL", (body.document_id, entity_id))
        if not cur.fetchone():
            raise ApiError(400, "validation_failed", "document_id must be a document of this entity with its file uploaded")
        table = SUBJECT_TABLE[body.subject]
        cur.execute(f"SELECT 1 FROM {table} WHERE id = %s AND entity_id = %s", (body.subject_id, entity_id))
        if not cur.fetchone():
            raise ApiError(400, "validation_failed", f"{body.subject} {body.subject_id} does not exist")
        cur.execute("""INSERT INTO signature_requests (entity_id, subject, subject_id, document_id, method, status, sent_at) VALUES (%s, %s::subject_kind, %s, %s, %s, 'sent', now())
                       RETURNING id""", (entity_id, body.subject, body.subject_id, body.document_id, body.method))
        sid = cur.fetchone()["id"]
        first = min(s.sign_order for s in body.signers)
        for s in body.signers:
            cur.execute("""INSERT INTO signature_signers (request_id, sign_order, person_id, external_name, external_email, status) VALUES (%s, %s, %s, %s, %s, %s)""",
                        (sid, s.sign_order, s.person_id, s.external_name, s.external_email, "waiting" if s.sign_order == first else "queued"))
        if body.subject == "contract":
            cur.execute("UPDATE contracts SET status = 'out_for_signature' WHERE id = %s AND status IN ('draft', 'negotiating')", (body.subject_id,))
        elif body.subject == "board_resolution":
            cur.execute("UPDATE resolutions SET signature_request_id = %s, status = CASE WHEN status = 'draft' THEN 'circulated' ELSE status END WHERE id = %s", (sid, body.subject_id))
        audit(cur, entity_id, "signature_request.sent", "signature_requests", sid, after={"subject": body.subject, "signers": len(body.signers)})
        out = load_signature(cur, entity_id, sid)
        idempotency_finish(cur, idempotency_key, 201, out)
    return JSONResponse(out, status_code=201)


@router.get(E + "/signature-requests", tags=["Approvals"])
def list_signatures(entity_id: uuid.UUID, p: Principal = Auth, status: str | None = None, limit: int = Query(default=50, ge=1, le=200), cursor: str | None = None):
    off = offset_of(cursor)
    with tx(p.user_id) as cur:
        require(cur, entity_id, "approvals:read")
        cur.execute("SELECT id FROM signature_requests WHERE entity_id = %s AND (%s::text IS NULL OR status = %s) ORDER BY sent_at DESC NULLS LAST, id LIMIT %s OFFSET %s",
                    (entity_id, status, status, limit + 1, off))
        return page([load_signature(cur, entity_id, r["id"]) for r in cur.fetchall()], limit, off)


@router.get(E + "/signature-requests/{request_id}", tags=["Approvals"])
def get_signature(entity_id: uuid.UUID, request_id: uuid.UUID, p: Principal = Auth):
    with tx(p.user_id) as cur:
        require(cur, entity_id, "approvals:read")
        return load_signature(cur, entity_id, request_id)


def _signer_for_update(cur, entity_id, request_id, signer_id):
    cur.execute("SELECT * FROM signature_requests WHERE id = %s AND entity_id = %s FOR UPDATE", (request_id, entity_id))
    req = cur.fetchone()
    if not req:
        raise ApiError(404, "not_found", "Not found")
    cur.execute("""SELECT g.*, p.user_id AS person_user FROM signature_signers g LEFT JOIN persons p ON p.id = g.person_id WHERE g.id = %s AND g.request_id = %s FOR UPDATE OF g""",
                (signer_id, request_id))
    g = cur.fetchone()
    if not g:
        raise ApiError(404, "not_found", "Not found")
    if req["status"] in ("completed", "declined", "expired"):
        raise ApiError(409, "conflict", f"The request is already {req['status']}")
    if g["status"] != "waiting":
        raise ApiError(409, "conflict", "This signer is not up yet" if g["status"] == "queued" else f"This signer has already {g['status']}")
    return req, g


def _authorised_signer(cur, entity_id, g, user_id) -> None:
    """The signer signs for themselves; for an external signer, or one without a login, a user who can approve records it."""
    if g["person_user"] and g["person_user"] == user_id:
        return
    require(cur, entity_id, "approvals:approve")


class SignIn(BaseModel):
    evidence: str | None = Field(default=None, max_length=500)


@router.post(E + "/signature-requests/{request_id}/signers/{signer_id}/sign", tags=["Approvals"])
def sign(entity_id: uuid.UUID, request_id: uuid.UUID, signer_id: uuid.UUID, body: SignIn = SignIn(), p: Principal = Auth):
    with tx(p.user_id) as cur:
        require(cur, entity_id, "approvals:read")
        req, g = _signer_for_update(cur, entity_id, request_id, signer_id)
        _authorised_signer(cur, entity_id, g, p.user_id)
        cur.execute("UPDATE signature_signers SET status = 'signed', signed_at = now() WHERE id = %s", (signer_id,))
        cur.execute("SELECT count(*) FILTER (WHERE status = 'waiting') AS waiting, min(sign_order) FILTER (WHERE status = 'queued') AS next_order FROM signature_signers WHERE request_id = %s", (request_id,))
        s = cur.fetchone()
        if s["waiting"] == 0 and s["next_order"] is not None:
            cur.execute("UPDATE signature_signers SET status = 'waiting' WHERE request_id = %s AND status = 'queued' AND sign_order = %s", (request_id, s["next_order"]))
            cur.execute("UPDATE signature_requests SET status = 'partially_signed' WHERE id = %s", (request_id,))
        elif s["waiting"] == 0:
            cur.execute("UPDATE signature_requests SET status = 'completed', completed_at = now() WHERE id = %s", (request_id,))
            if req["subject"] == "contract":
                cur.execute("UPDATE contracts SET status = 'active', signed_document_id = %s WHERE id = %s", (req["document_id"], req["subject_id"]))
                cur.execute("INSERT INTO document_links (document_id, subject, subject_id, role) VALUES (%s, 'contracts', %s, 'signed_copy') ON CONFLICT DO NOTHING", (req["document_id"], req["subject_id"]))
        else:
            cur.execute("UPDATE signature_requests SET status = 'partially_signed' WHERE id = %s", (request_id,))
        audit(cur, entity_id, "signature.signed", "signature_requests", request_id, after={"signer": signer_id, "evidence": body.evidence})
        return load_signature(cur, entity_id, request_id)


class DeclineIn(BaseModel):
    reason: str | None = Field(default=None, max_length=500)


@router.post(E + "/signature-requests/{request_id}/signers/{signer_id}/decline", tags=["Approvals"])
def decline(entity_id: uuid.UUID, request_id: uuid.UUID, signer_id: uuid.UUID, body: DeclineIn = DeclineIn(), p: Principal = Auth):
    with tx(p.user_id) as cur:
        require(cur, entity_id, "approvals:read")
        req, g = _signer_for_update(cur, entity_id, request_id, signer_id)
        _authorised_signer(cur, entity_id, g, p.user_id)
        cur.execute("UPDATE signature_signers SET status = 'declined' WHERE id = %s", (signer_id,))
        cur.execute("UPDATE signature_requests SET status = 'declined' WHERE id = %s", (request_id,))
        if req["subject"] == "contract":
            cur.execute("UPDATE contracts SET status = 'negotiating' WHERE id = %s AND status = 'out_for_signature'", (req["subject_id"],))
        audit(cur, entity_id, "signature.declined", "signature_requests", request_id, after={"signer": signer_id, "reason": body.reason})
        return load_signature(cur, entity_id, request_id)


@router.post(E + "/signature-requests/{request_id}/remind", status_code=204, tags=["Approvals"])
def remind(entity_id: uuid.UUID, request_id: uuid.UUID, p: Principal = Auth):
    with tx(p.user_id) as cur:
        require(cur, entity_id, "approvals:read")
        s = load_signature(cur, entity_id, request_id)
        waiting = [g for g in s["signers"] if g["status"] == "waiting"]
        if s["status"] in ("completed", "declined", "expired") or not waiting:
            raise ApiError(409, "conflict", "Nobody is waiting to sign")
        audit(cur, entity_id, "signature.reminded", "signature_requests", request_id, after={"signers": [g["person_name"] or g["external_email"] for g in waiting]})


# ================================================================== notices
class NoticeIn(BaseModel):
    authority_code: str = Field(min_length=2, max_length=20)
    registration_id: uuid.UUID | None = None
    form_code: str = Field(min_length=1, max_length=40)
    reference_no: str | None = None
    period_label: str | None = None
    received_on: dt.date
    reply_due_on: dt.date | None = None
    hearing_on: dt.date | None = None
    demand_amount: Decimal | None = Field(default=None, ge=0)
    status: Literal["open", "draft_reply", "replied", "hearing", "closed", "appealed"] = "open"
    summary: str | None = None
    linked_obligation_id: uuid.UUID | None = None


NOTICE_COLS = ("authority_code", "registration_id", "form_code", "reference_no", "period_label", "received_on", "reply_due_on", "hearing_on", "demand_amount", "status",
               "summary", "linked_obligation_id")


def load_notice(cur, entity_id, nid) -> dict:
    cur.execute(f"SELECT id, {', '.join(NOTICE_COLS)} FROM notices WHERE id = %s AND entity_id = %s", (nid, entity_id))
    n = cur.fetchone()
    if not n:
        raise ApiError(404, "not_found", "Not found")
    cur.execute("SELECT document_id FROM document_links WHERE subject = 'notices' AND subject_id = %s ORDER BY document_id", (nid,))
    n["document_ids"] = [r["document_id"] for r in cur.fetchall()]
    return js(n)


def _check_notice(cur, entity_id, b: NoticeIn) -> None:
    cur.execute("SELECT 1 FROM ref_authorities WHERE code = %s", (b.authority_code,))
    if not cur.fetchone():
        raise ApiError(400, "validation_failed", f"Unknown authority {b.authority_code}")
    for d in (b.reply_due_on, b.hearing_on):
        if d and d < b.received_on:
            raise ApiError(400, "validation_failed", "reply_due_on and hearing_on cannot be before received_on")
    if b.registration_id:
        cur.execute("SELECT 1 FROM entity_registrations WHERE id = %s AND entity_id = %s", (b.registration_id, entity_id))
        if not cur.fetchone():
            raise ApiError(400, "validation_failed", "registration_id is not a registration of this entity")
    if b.linked_obligation_id:
        cur.execute("SELECT 1 FROM obligations WHERE id = %s AND entity_id = %s", (b.linked_obligation_id, entity_id))
        if not cur.fetchone():
            raise ApiError(400, "validation_failed", "linked_obligation_id is not an obligation of this entity")


@router.post(E + "/notices", status_code=201, tags=["Compliance"])
def create_notice(entity_id: uuid.UUID, body: NoticeIn, p: Principal = Auth):
    with tx(p.user_id) as cur:
        require(cur, entity_id, "compliance:write")
        _check_notice(cur, entity_id, body)
        cur.execute(f"INSERT INTO notices (entity_id, owner_user_id, {', '.join(NOTICE_COLS)}) VALUES (%s, app.current_user_id(), {', '.join(['%s'] * len(NOTICE_COLS))}) RETURNING id",
                    (entity_id, *[getattr(body, c) for c in NOTICE_COLS]))
        nid = cur.fetchone()["id"]
        if body.reply_due_on:
            cur.execute("INSERT INTO tasks (entity_id, title, subject, subject_id, due_on, source) VALUES (%s, %s, 'notice_reply', %s, %s, 'engine')",
                        (entity_id, f"Reply to {body.authority_code} notice {body.form_code}", nid, body.reply_due_on))
        if body.hearing_on:
            cur.execute("INSERT INTO tasks (entity_id, title, subject, subject_id, due_on, source) VALUES (%s, %s, 'notice_reply', %s, %s, 'engine')",
                        (entity_id, f"Hearing: {body.authority_code} {body.form_code}", nid, body.hearing_on))
        audit(cur, entity_id, "notice.received", "notices", nid, after={"authority": body.authority_code, "form": body.form_code})
        emit(cur, entity_id, "notice.received", "notices", nid, {"authority": body.authority_code, "form": body.form_code, "reply_due_on": body.reply_due_on})
        return load_notice(cur, entity_id, nid)


@router.get(E + "/notices", tags=["Compliance"])
def list_notices(entity_id: uuid.UUID, p: Principal = Auth, status: str | None = None, limit: int = Query(default=50, ge=1, le=200), cursor: str | None = None):
    off = offset_of(cursor)
    with tx(p.user_id) as cur:
        require(cur, entity_id, "compliance:read")
        cur.execute("SELECT id FROM notices WHERE entity_id = %s AND (%s::text IS NULL OR status = %s) ORDER BY (status = 'closed'), reply_due_on NULLS LAST, received_on DESC, id LIMIT %s OFFSET %s",
                    (entity_id, status, status, limit + 1, off))
        return page([load_notice(cur, entity_id, r["id"]) for r in cur.fetchall()], limit, off)


@router.get(E + "/notices/{notice_id}", tags=["Compliance"])
def get_notice(entity_id: uuid.UUID, notice_id: uuid.UUID, p: Principal = Auth):
    with tx(p.user_id) as cur:
        require(cur, entity_id, "compliance:read")
        return load_notice(cur, entity_id, notice_id)


@router.patch(E + "/notices/{notice_id}", tags=["Compliance"])
def update_notice(entity_id: uuid.UUID, notice_id: uuid.UUID, body: dict, p: Principal = Auth):
    with tx(p.user_id) as cur:
        require(cur, entity_id, "compliance:write")
        cur.execute(f"SELECT {', '.join(NOTICE_COLS)} FROM notices WHERE id = %s AND entity_id = %s FOR UPDATE", (notice_id, entity_id))
        n = cur.fetchone()
        if not n:
            raise ApiError(404, "not_found", "Not found")
        try:
            new = NoticeIn(**{**{k: (str(v) if isinstance(v, uuid.UUID) else v) for k, v in n.items()}, **{k: v for k, v in body.items() if k in NOTICE_COLS}})
        except Exception as e:
            raise ApiError(400, "validation_failed", "Invalid notice", str(e))
        _check_notice(cur, entity_id, new)
        cur.execute(f"UPDATE notices SET {', '.join(f'{k} = %s' for k in NOTICE_COLS)} WHERE id = %s", (*[getattr(new, k) for k in NOTICE_COLS], notice_id))
        if new.status in ("replied", "closed") and n["status"] not in ("replied", "closed"):
            cur.execute("UPDATE tasks SET status = 'done', completed_at = now() WHERE entity_id = %s AND subject = 'notice_reply' AND subject_id = %s AND status = 'open'", (entity_id, notice_id))
        audit(cur, entity_id, "notice.updated", "notices", notice_id, after={k: v for k, v in body.items() if k in NOTICE_COLS})
        return load_notice(cur, entity_id, notice_id)


@router.post(E + "/notices/{notice_id}/draft-reply", status_code=202, tags=["Compliance"])
def draft_notice_reply(entity_id: uuid.UUID, notice_id: uuid.UUID, p: Principal = Auth):
    with tx(p.user_id) as cur:
        require(cur, entity_id, "copilot:use")
        n = load_notice(cur, entity_id, notice_id)
        if n["status"] in ("closed",):
            raise ApiError(409, "conflict", "The notice is closed")
        return _queue(cur, entity_id, "notice.draft_reply", "notices", notice_id)


def handle_draft_reply(cur, job: dict) -> dict:
    entity_id = job["entity_id"]
    n = load_notice(cur, entity_id, job["subject_id"])
    cur.execute("SELECT legal_name FROM entities WHERE id = %s", (entity_id,))
    company = cur.fetchone()["legal_name"]
    cur.execute("""SELECT r.title, o.period_label, o.status::text AS status, o.acknowledgement FROM obligations o JOIN compliance_rules r ON r.id = o.rule_id
                    WHERE o.entity_id = %s AND (o.id = %s OR (%s::text IS NOT NULL AND o.period_label = %s)) ORDER BY o.period_start LIMIT 10""",
                (entity_id, n["linked_obligation_id"], n["period_label"], n["period_label"]))
    records = cur.fetchall()
    lines = "\n".join(f"  - {r['title']} - {r['period_label']}: {r['status']}" + (f", acknowledgement {r['acknowledgement']}" if r["acknowledgement"] else "") for r in records) \
        or "  - [Attach the returns and evidence for the period]"
    demand = f"The demand of Rs {n['demand_amount']} is disputed to the extent set out below.\n" if n["demand_amount"] else ""
    text = (f"DRAFT REPLY - REVIEW WITH YOUR ADVISER BEFORE FILING\n\nTo: {n['authority_code']}\nReference: {n['reference_no'] or '[reference]'}  Form: {n['form_code']}  Period: {n['period_label'] or '[period]'}\n"
            f"Received on: {n['received_on']}  Reply due: {n['reply_due_on'] or '[date]'}\n\nFrom: {company}\n\nSubject: Reply to {n['form_code']}\n\n"
            f"1. We acknowledge the notice. Summary of the notice: {n['summary'] or '[summary]'}\n2. {demand}3. Our records for the period show:\n{lines}\n"
            f"4. [Explain each point raised and attach evidence.]\n\nYours faithfully,\n{company}\n")
    folder = "tax" if n["authority_code"] in ("GSTN", "CBDT") else "mca" if n["authority_code"] == "MCA" else "other"
    doc = store_document(cur, entity_id, folder=folder, title=f"Draft reply - {n['authority_code']} {n['form_code']}", file_name="draft-reply.txt", mime="text/plain",
                         data=text.encode(), links=[("notices", job["subject_id"], "draft")])
    cur.execute("UPDATE notices SET status = 'draft_reply' WHERE id = %s AND status = 'open'", (job["subject_id"],))
    audit(cur, entity_id, "notice.reply_drafted", "notices", job["subject_id"], after={"document": doc}, actor_kind="engine")
    return {"document_id": str(doc), "records_matched": len(records)}
