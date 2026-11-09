"""MSME track: buyers who pay late and the interest owed under the MSMED Act, claims for delayed payment (filed on MSME Samadhaan as a job),
TReDS invoice discounting, the Udyam classification tests, and recording the company facts those tests and the schemes read.

Interest and classification limits are a starter library (see `msme.py`) and need review by a CA or CS. Interest rights under Sec 16 belong
to micro and small suppliers only, so for anyone else the interest is not calculated.
"""
from __future__ import annotations

import datetime as dt
import json
import uuid
from decimal import Decimal
from typing import Literal

from fastapi import APIRouter, Header, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from . import adapters, engine, msme
from .core import ApiError, Auth, Principal, audit, emit, idempotency_begin, idempotency_finish, jsonable, require, tx
from .routes_orders import js, offset_of, page

router = APIRouter()
E = "/entities/{entity_id}"
RIGHTS = ("micro", "small")


def _rates(cur) -> list[tuple[dt.date, Decimal]]:
    cur.execute("SELECT effective_from, rate_pct FROM ref_rbi_bank_rate ORDER BY effective_from")
    return [(r["effective_from"], r["rate_pct"]) for r in cur.fetchall()]


def _supplier_class(cur, entity_id) -> str | None:
    cur.execute("SELECT udyam_class::text AS c FROM entities WHERE id = %s", (entity_id,))
    return cur.fetchone()["c"]


def _late_invoices(cur, entity_id, as_of: dt.date, rates, with_interest: bool, party_id=None, ids=None) -> list[dict]:
    cur.execute("""SELECT document_id, party_id, legal_name, number, doc_date, due_date, balance_due FROM v_receivables_ageing
                    WHERE entity_id = %s AND (%s::uuid IS NULL OR party_id = %s) AND (%s::uuid[] IS NULL OR document_id = ANY(%s::uuid[]))
                    ORDER BY doc_date, number""", (entity_id, party_id, party_id, ids, ids))
    out = []
    for r in cur.fetchall():
        appointed = msme.appointed_day(r["doc_date"], r["due_date"])
        if as_of <= appointed or r["balance_due"] <= 0:
            continue
        interest = msme.interest(r["balance_due"], appointed, as_of, rates) if with_interest and rates else None
        out.append({**r, "appointed_on": appointed, "days_late": (as_of - appointed).days, "interest": interest})
    return out


# ================================================================== buyers past the appointed day
@router.get(E + "/msme/late-buyers", tags=["Schemes & credit"])
def late_buyers(entity_id: uuid.UUID, p: Principal = Auth, as_of: dt.date | None = None):
    today = as_of or dt.date.today()
    with tx(p.user_id) as cur:
        require(cur, entity_id, "sales:read")
        cls = _supplier_class(cur, entity_id)
        applies = cls in RIGHTS
        rates = _rates(cur)
        invoices = _late_invoices(cur, entity_id, today, rates, applies)
        cur.execute("SELECT DISTINCT sales_document_id FROM treds_invoices WHERE entity_id = %s AND status <> 'rejected'", (entity_id,))
        on_treds = {r["sales_document_id"] for r in cur.fetchall()}
        cur.execute("""SELECT DISTINCT ON (c.buyer_party_id) c.buyer_party_id, c.id, c.status FROM delayed_payment_claims c
                        WHERE c.entity_id = %s AND c.status <> 'closed' ORDER BY c.buyer_party_id, c.id""", (entity_id,))
        claims = {r["buyer_party_id"]: {"id": r["id"], "status": r["status"]} for r in cur.fetchall()}
        rate = None
        if rates:
            try:
                start, pct = msme.bank_rate_on(rates, today)
                rate = {"bank_rate_pct": str(pct), "effective_from": start, "annual_pct": str(msme.RATE_MULTIPLE * pct)}
            except msme.MsmeError:
                rate = None
    buyers: dict = {}
    for inv in invoices:
        b = buyers.setdefault(inv["party_id"], {"party_id": inv["party_id"], "name": inv["legal_name"], "invoices": [], "principal": Decimal(0), "interest": Decimal(0) if applies else None,
                                                  "days_late": 0, "on_treds": False, "claim": claims.get(inv["party_id"])})
        b["invoices"].append({"document_id": inv["document_id"], "number": inv["number"], "balance_due": inv["balance_due"], "appointed_on": inv["appointed_on"],
                              "days_late": inv["days_late"], "interest": inv["interest"], "on_treds": inv["document_id"] in on_treds})
        b["principal"] += inv["balance_due"]
        if applies and inv["interest"] is not None:
            b["interest"] += inv["interest"]
        b["days_late"] = max(b["days_late"], inv["days_late"])
        b["on_treds"] = b["on_treds"] or inv["document_id"] in on_treds
    rows = sorted(buyers.values(), key=lambda b: (-b["days_late"], -b["principal"]))
    total_interest = sum((b["interest"] for b in rows), Decimal(0)) if applies else None
    return js({"as_of": today, "supplier_class": cls, "interest_applies": applies, "rate": rate, "buyers": rows,
               "totals": {"buyers": len(rows), "invoices": len(invoices), "principal": sum((b["principal"] for b in rows), Decimal(0)), "interest": total_interest}})


# ================================================================== claims for delayed payment
class ClaimIn(BaseModel):
    buyer_party_id: uuid.UUID
    invoice_ids: list[uuid.UUID] = Field(min_length=1, max_length=100)


def _claim_out(cur, entity_id, claim_id) -> dict:
    cur.execute("""SELECT c.id, c.buyer_party_id, p.legal_name AS buyer_name, c.principal, c.interest_claimed, c.forum, c.status, c.case_no, c.filed_on
                     FROM delayed_payment_claims c JOIN parties p ON p.id = c.buyer_party_id WHERE c.id = %s AND c.entity_id = %s""", (claim_id, entity_id))
    c = cur.fetchone()
    if not c:
        raise ApiError(404, "not_found", "Not found")
    cur.execute("""SELECT d.id, d.number, d.doc_date, d.balance_due FROM delayed_payment_claim_invoices i JOIN sales_documents d ON d.id = i.sales_document_id
                    WHERE i.claim_id = %s ORDER BY d.doc_date, d.number""", (claim_id,))
    invs = cur.fetchall()
    c["invoice_ids"] = [i["id"] for i in invs]
    c["invoices"] = [{"number": i["number"], "doc_date": i["doc_date"], "balance_due": i["balance_due"]} for i in invs]
    return js(c)


@router.post(E + "/delayed-payment-claims", status_code=201, tags=["Schemes & credit"])
def create_claim(entity_id: uuid.UUID, body: ClaimIn, p: Principal = Auth):
    with tx(p.user_id) as cur:
        require(cur, entity_id, "schemes:write")
        cls = _supplier_class(cur, entity_id)
        if cls not in RIGHTS:
            raise ApiError(409, "conflict", "Only micro and small enterprises can claim delayed-payment interest", "Set the company's Udyam class on its profile if it is registered.")
        rates = _rates(cur)
        if not rates:
            raise ApiError(409, "conflict", "No RBI bank rate is on record, so the interest cannot be worked out")
        ids = list(dict.fromkeys(body.invoice_ids))
        late = _late_invoices(cur, entity_id, dt.date.today(), rates, True, party_id=body.buyer_party_id, ids=ids)
        found = {x["document_id"] for x in late}
        missing = [i for i in ids if i not in found]
        if missing:
            raise ApiError(400, "validation_failed", "Some invoices are not unpaid invoices to this buyer that are past their due day",
                           errors=[{"field": "invoice_ids", "message": f"{m} cannot be claimed"} for m in missing])
        cur.execute("""SELECT 1 FROM delayed_payment_claim_invoices i JOIN delayed_payment_claims c ON c.id = i.claim_id
                        WHERE c.entity_id = %s AND c.status <> 'closed' AND i.sales_document_id = ANY(%s::uuid[]) LIMIT 1""", (entity_id, ids))
        if cur.fetchone():
            raise ApiError(409, "conflict", "An invoice is already in a claim")
        principal = sum((x["balance_due"] for x in late), Decimal(0))
        interest = sum((x["interest"] for x in late), Decimal(0))
        cur.execute("INSERT INTO delayed_payment_claims (entity_id, buyer_party_id, principal, interest_claimed) VALUES (%s, %s, %s, %s) RETURNING id", (entity_id, body.buyer_party_id, principal, interest))
        cid = cur.fetchone()["id"]
        for i in ids:
            cur.execute("INSERT INTO delayed_payment_claim_invoices (claim_id, sales_document_id) VALUES (%s, %s)", (cid, i))
        audit(cur, entity_id, "delayed_payment_claim.drafted", "delayed_payment_claims", cid, after={"principal": principal, "interest": interest, "invoices": len(ids)})
        return _claim_out(cur, entity_id, cid)


@router.get(E + "/delayed-payment-claims", tags=["Schemes & credit"])
def list_claims(entity_id: uuid.UUID, p: Principal = Auth, limit: int = Query(default=50, ge=1, le=200), cursor: str | None = None):
    off = offset_of(cursor)
    with tx(p.user_id) as cur:
        require(cur, entity_id, "schemes:read")
        cur.execute("SELECT id FROM delayed_payment_claims WHERE entity_id = %s ORDER BY (status = 'closed'), filed_on DESC NULLS FIRST, id LIMIT %s OFFSET %s", (entity_id, limit + 1, off))
        return page([_claim_out(cur, entity_id, r["id"]) for r in cur.fetchall()], limit, off)


@router.get(E + "/delayed-payment-claims/{claim_id}", tags=["Schemes & credit"])
def get_claim(entity_id: uuid.UUID, claim_id: uuid.UUID, p: Principal = Auth):
    with tx(p.user_id) as cur:
        require(cur, entity_id, "schemes:read")
        return _claim_out(cur, entity_id, claim_id)


@router.post(E + "/delayed-payment-claims/{claim_id}/file", status_code=202, tags=["Schemes & credit"])
def file_claim(entity_id: uuid.UUID, claim_id: uuid.UUID, p: Principal = Auth, idempotency_key: str | None = Header(default=None, alias="Idempotency-Key")):
    with tx(p.user_id) as cur:
        require(cur, entity_id, "schemes:file")
        replay = idempotency_begin(cur, idempotency_key, "POST", f"/entities/{entity_id}/delayed-payment-claims/{claim_id}/file", {})
        if replay:
            return JSONResponse(replay["body"], status_code=replay["status"] or 202)
        cur.execute("SELECT status FROM delayed_payment_claims WHERE id = %s AND entity_id = %s FOR UPDATE", (claim_id, entity_id))
        c = cur.fetchone()
        if not c:
            raise ApiError(404, "not_found", "Not found")
        if c["status"] != "draft":
            raise ApiError(409, "conflict", f"The claim is already {c['status']}")
        cur.execute("""INSERT INTO jobs (entity_id, kind, subject, subject_id, payload, created_by) VALUES (%s, 'msme.file_claim', 'delayed_payment_claims', %s, '{}', app.current_user_id())
                       RETURNING id, kind, status, progress, subject, subject_id, result, created_at, finished_at""", (entity_id, claim_id))
        job = js(cur.fetchone())
        audit(cur, entity_id, "delayed_payment_claim.filing_requested", "delayed_payment_claims", claim_id)
        idempotency_finish(cur, idempotency_key, 202, job)
    return JSONResponse(job, status_code=202, headers={"Location": f"/v1/jobs/{job['id']}"})


def handle_file_claim(cur, job: dict) -> dict:
    """Worker: file the claim on MSME Samadhaan and record the case number."""
    entity_id = job["entity_id"]
    cur.execute("SELECT * FROM delayed_payment_claims WHERE id = %s FOR UPDATE", (job["subject_id"],))
    c = cur.fetchone()
    if c["status"] != "draft":
        return {"case_no": c["case_no"]}
    claim = _claim_out(cur, entity_id, c["id"])
    res = adapters.get("samadhaan").file_claim({"claim_id": str(c["id"]), "buyer": claim["buyer_name"], "principal": str(c["principal"]), "interest": str(c["interest_claimed"]),
                                                "invoices": [i["number"] for i in claim["invoices"]]})
    cur.execute("UPDATE delayed_payment_claims SET status = 'filed', filed_on = current_date, case_no = %s, forum = %s WHERE id = %s", (res["case_no"], res.get("forum", "msme_samadhaan"), c["id"]))
    audit(cur, entity_id, "delayed_payment_claim.filed", "delayed_payment_claims", c["id"], after={"case_no": res["case_no"]}, actor_kind="integration")
    emit(cur, entity_id, "delayed_payment_claim.filed", "delayed_payment_claims", c["id"], {"case_no": res["case_no"]})
    return {"case_no": res["case_no"]}


# ================================================================== TReDS
PLATFORMS = ("RXIL", "M1xchange", "Invoicemart", "C2FO")
TREDS_NEXT = {"uploaded": {"accepted", "rejected"}, "accepted": {"auctioned", "rejected"}, "auctioned": {"financed", "rejected"}, "financed": {"settled"}, "settled": set(), "rejected": set()}
TREDS_SQL = """SELECT t.id, t.sales_document_id, d.number AS invoice_number, p.legal_name AS buyer_name, d.grand_total AS invoice_total, t.platform, t.uploaded_on, t.buyer_accepted_on,
                      t.financed_on, t.discount_rate_pct, t.amount_received, t.status
                 FROM treds_invoices t JOIN sales_documents d ON d.id = t.sales_document_id JOIN parties p ON p.id = d.party_id"""


class TredsIn(BaseModel):
    sales_document_id: uuid.UUID
    platform: Literal["RXIL", "M1xchange", "Invoicemart", "C2FO"]


class TredsPatch(BaseModel):
    status: Literal["accepted", "auctioned", "financed", "settled", "rejected"]
    discount_rate_pct: Decimal | None = Field(default=None, ge=0, le=100, max_digits=6, decimal_places=3)
    amount_received: Decimal | None = Field(default=None, ge=0, max_digits=15, decimal_places=2)


def _treds_out(cur, entity_id, tid) -> dict:
    cur.execute(TREDS_SQL + " WHERE t.id = %s AND t.entity_id = %s", (tid, entity_id))
    r = cur.fetchone()
    if not r:
        raise ApiError(404, "not_found", "Not found")
    r["discount_rate_pct"] = str(r["discount_rate_pct"]) if r["discount_rate_pct"] is not None else None
    return js(r)


@router.post(E + "/treds-invoices", status_code=201, tags=["Schemes & credit"])
def upload_treds(entity_id: uuid.UUID, body: TredsIn, p: Principal = Auth, idempotency_key: str | None = Header(default=None, alias="Idempotency-Key")):
    with tx(p.user_id) as cur:
        require(cur, entity_id, "schemes:write")
        replay = idempotency_begin(cur, idempotency_key, "POST", f"/entities/{entity_id}/treds-invoices", body.model_dump(mode="json"))
        if replay:
            return JSONResponse(replay["body"], status_code=replay["status"] or 201)
        if _supplier_class(cur, entity_id) is None:
            raise ApiError(409, "conflict", "TReDS is for registered MSMEs", "Record the company's Udyam registration first.")
        cur.execute("SELECT status, payment_status, doc_type::text AS doc_type, balance_due FROM sales_documents WHERE id = %s AND entity_id = %s", (body.sales_document_id, entity_id))
        d = cur.fetchone()
        if not d:
            raise ApiError(400, "validation_failed", "Unknown invoice", errors=[{"field": "sales_document_id", "message": "not one of this company's invoices"}])
        if d["status"] != "issued" or d["payment_status"] not in ("unpaid", "partially_paid") or d["doc_type"] not in ("tax_invoice", "bill_of_supply", "export_invoice") or d["balance_due"] <= 0:
            raise ApiError(409, "conflict", "Only an issued invoice with an amount still due can go on TReDS")
        cur.execute("SELECT 1 FROM treds_invoices WHERE sales_document_id = %s AND status <> 'rejected'", (body.sales_document_id,))
        if cur.fetchone():
            raise ApiError(409, "conflict", "This invoice is already on TReDS")
        cur.execute("INSERT INTO treds_invoices (entity_id, sales_document_id, platform, uploaded_on) VALUES (%s, %s, %s, current_date) RETURNING id", (entity_id, body.sales_document_id, body.platform))
        tid = cur.fetchone()["id"]
        audit(cur, entity_id, "treds_invoice.uploaded", "treds_invoices", tid, after={"platform": body.platform})
        out = _treds_out(cur, entity_id, tid)
        idempotency_finish(cur, idempotency_key, 201, out)
    return JSONResponse(out, status_code=201)


@router.get(E + "/treds-invoices", tags=["Schemes & credit"])
def list_treds(entity_id: uuid.UUID, p: Principal = Auth, limit: int = Query(default=100, ge=1, le=200), cursor: str | None = None):
    off = offset_of(cursor)
    with tx(p.user_id) as cur:
        require(cur, entity_id, "schemes:read")
        cur.execute("SELECT id FROM treds_invoices WHERE entity_id = %s ORDER BY uploaded_on DESC, id LIMIT %s OFFSET %s", (entity_id, limit + 1, off))
        return page([_treds_out(cur, entity_id, r["id"]) for r in cur.fetchall()], limit, off)


@router.patch(E + "/treds-invoices/{treds_id}", tags=["Schemes & credit"])
def update_treds(entity_id: uuid.UUID, treds_id: uuid.UUID, body: TredsPatch, p: Principal = Auth):
    """Record how an upload is getting on at the platform: accepted by the buyer, auctioned, financed (with the discount rate and the money received), settled."""
    with tx(p.user_id) as cur:
        require(cur, entity_id, "schemes:write")
        cur.execute("SELECT status FROM treds_invoices WHERE id = %s AND entity_id = %s FOR UPDATE", (treds_id, entity_id))
        t = cur.fetchone()
        if not t:
            raise ApiError(404, "not_found", "Not found")
        if body.status not in TREDS_NEXT[t["status"]]:
            raise ApiError(409, "conflict", f"A {t['status']} upload cannot become {body.status}")
        if body.status == "financed" and (body.discount_rate_pct is None or body.amount_received is None):
            raise ApiError(400, "validation_failed", "Say the discount rate and the amount received",
                           errors=[{"field": "discount_rate_pct", "message": "required when financed"}, {"field": "amount_received", "message": "required when financed"}])
        cur.execute("""UPDATE treds_invoices SET status = %s, buyer_accepted_on = CASE WHEN %s = 'accepted' THEN current_date ELSE buyer_accepted_on END,
                              financed_on = CASE WHEN %s = 'financed' THEN current_date ELSE financed_on END,
                              discount_rate_pct = coalesce(%s, discount_rate_pct), amount_received = coalesce(%s, amount_received) WHERE id = %s""",
                    (body.status, body.status, body.status, body.discount_rate_pct, body.amount_received, treds_id))
        audit(cur, entity_id, "treds_invoice.updated", "treds_invoices", treds_id, before={"status": t["status"]}, after={"status": body.status})
        return _treds_out(cur, entity_id, treds_id)


# ================================================================== Udyam classification
@router.get(E + "/msme/classification", tags=["Schemes & credit"])
def classification(entity_id: uuid.UUID, p: Principal = Auth):
    with tx(p.user_id) as cur:
        require(cur, entity_id, "schemes:read")
        cur.execute("SELECT udyam_class::text AS c, udyam_number FROM entities WHERE id = %s", (entity_id,))
        e = cur.fetchone()
        facts = engine.load_context(cur, entity_id).facts
    inv = Decimal(str(facts["investment_plant_machinery"])) if facts.get("investment_plant_machinery") is not None else None
    turn_key = "aato" if facts.get("aato") is not None else "turnover_max_any_year"
    turn = Decimal(str(facts[turn_key])) if facts.get(turn_key) is not None else None
    return js({"declared": e["c"], "udyam_number": e["udyam_number"], "computed": msme.classify(inv, turn), "investment": inv, "turnover": turn, "tests": msme.tests(e["c"], inv, turn)})


# ================================================================== company facts
FACT_KEY = r"^[a-z][a-z0-9_]{1,59}$"


class FactIn(BaseModel):
    fact_key: str = Field(pattern=FACT_KEY)
    value: float | str | bool
    as_of: dt.date


def _fact_out(r: dict) -> dict:
    v = r["value_num"] if r["value_num"] is not None else r["value_text"] if r["value_text"] is not None else r["value_bool"]
    if isinstance(v, Decimal):
        v = int(v) if v == v.to_integral_value() else float(v)
    return {"fact_key": r["fact_key"], "value": v, "as_of": r["as_of"].isoformat(), "source": r["source"]}


@router.get(E + "/facts", tags=["Entities"])
def list_facts(entity_id: uuid.UUID, p: Principal = Auth, history: bool = False):
    with tx(p.user_id) as cur:
        require(cur, entity_id, "compliance:read")
        table = "entity_facts" if history else "entity_facts_current"
        cur.execute(f"SELECT fact_key, value_num, value_text, value_bool, as_of, source FROM {table} WHERE entity_id = %s ORDER BY fact_key, as_of DESC", (entity_id,))
        return [_fact_out(r) for r in cur.fetchall()]


@router.post(E + "/facts", status_code=201, tags=["Entities"])
def record_facts(entity_id: uuid.UUID, body: list[FactIn], p: Principal = Auth):
    if not body or len(body) > 50:
        raise ApiError(400, "validation_failed", "Send between 1 and 50 facts")
    with tx(p.user_id) as cur:
        require(cur, entity_id, "compliance:write")
        today = dt.date.today()
        for f in body:
            if f.as_of > today:
                raise ApiError(400, "validation_failed", "A fact cannot be dated in the future", errors=[{"field": "as_of", "message": f"{f.fact_key} is dated {f.as_of}"}])
            num = Decimal(str(f.value)) if isinstance(f.value, (int, float)) and not isinstance(f.value, bool) else None
            txt = f.value if isinstance(f.value, str) else None
            flag = f.value if isinstance(f.value, bool) else None
            if num is not None and num < 0:
                raise ApiError(400, "validation_failed", "A number fact cannot be negative", errors=[{"field": "value", "message": f.fact_key}])
            cur.execute("INSERT INTO entity_facts (entity_id, fact_key, value_num, value_text, value_bool, as_of, source, recorded_by) VALUES (%s, %s, %s, %s, %s, %s, 'user', app.current_user_id())",
                        (entity_id, f.fact_key, num, txt, flag, f.as_of))
        audit(cur, entity_id, "facts.recorded", "entity_facts", None, after={"keys": [f.fact_key for f in body]})
        engine.run_engine(cur, entity_id, "fact_change")           # a changed fact can switch obligations on or off
        keys = [f.fact_key for f in body]
        cur.execute("SELECT fact_key, value_num, value_text, value_bool, as_of, source FROM entity_facts_current WHERE entity_id = %s AND fact_key = ANY(%s) ORDER BY fact_key", (entity_id, keys))
        return [_fact_out(r) for r in cur.fetchall()]


# ================================================================== the company's own Udyam registration and track
class EntityPatch(BaseModel):
    udyam_number: str | None = Field(default=None, pattern=r"^UDYAM-[A-Z]{2}-[0-9]{2}-[0-9]{7}$")
    udyam_class: Literal["micro", "small", "medium"] | None = None
    track: Literal["business", "startup", "msme"] | None = None


@router.patch(E, tags=["Entities"])
def update_entity(entity_id: uuid.UUID, body: EntityPatch, p: Principal = Auth):
    """Change the Udyam registration and the track. Other fields of the company come from the registries and are not edited here."""
    changes = body.model_dump(exclude_unset=True)
    if not changes:
        raise ApiError(400, "validation_failed", "Nothing to change")
    with tx(p.user_id) as cur:
        require(cur, entity_id, "settings:write")
        cur.execute("SELECT udyam_number, udyam_class::text AS udyam_class, track::text AS track FROM entities WHERE id = %s FOR UPDATE", (entity_id,))
        before = cur.fetchone()
        merged = {**before, **changes}
        if merged["udyam_class"] and not merged["udyam_number"]:
            raise ApiError(400, "validation_failed", "A Udyam class needs the Udyam registration number", errors=[{"field": "udyam_number", "message": "required with udyam_class"}])
        sets = ", ".join(f"{k} = %s" for k in changes)
        cur.execute(f"UPDATE entities SET {sets} WHERE id = %s", (*changes.values(), entity_id))
        audit(cur, entity_id, "entity.updated", "entities", entity_id, before=before, after=changes)
        emit(cur, entity_id, "entity.updated", "entities", entity_id, {"fields": sorted(changes)})
        cur.execute("SELECT id, legal_name, track::text AS track, udyam_number, udyam_class::text AS udyam_class FROM entities WHERE id = %s", (entity_id,))
        return js(cur.fetchone())
