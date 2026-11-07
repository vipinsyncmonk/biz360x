"""Portal connections, OTP-paused jobs, e-way bills, e-invoice cancellation, generic filing, 26AS, Account Aggregator, registry lookups.

Every call to an outside system is a job (`jobs` row + worker handler) that goes through `adapters`; this module only
validates, queues, and applies the result.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import re
import uuid
from decimal import Decimal
from typing import Literal

from fastapi import APIRouter, Header, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from . import adapters, ledger, secretbox
from .core import ApiError, Auth, Principal, audit, emit, idempotency_begin, idempotency_finish, jsonable, require, tx
from .routes_gst import _obligation_done
from .routes_orders import js, offset_of, page

router = APIRouter()
E = "/entities/{entity_id}"
ZERO = Decimal(0)
JOB_COLS = "id, kind, status, progress, subject, subject_id, result, error, created_at, finished_at"


def queue(cur, entity_id, kind: str, subject: str | None, subject_id, payload: dict | None = None) -> dict:
    cur.execute(f"""INSERT INTO jobs (entity_id, kind, subject, subject_id, payload, created_by) VALUES (%s, %s, %s, %s, %s, app.current_user_id())
                    RETURNING {JOB_COLS}""", (entity_id, kind, subject, subject_id, json.dumps(payload or {}, default=jsonable)))
    return js(cur.fetchone())


def accepted(job: dict) -> JSONResponse:
    return JSONResponse(job, status_code=202, headers={"Location": f"/v1/jobs/{job['id']}"})


# ================================================================== jobs: OTP and cancel
class OtpIn(BaseModel):
    otp: str = Field(pattern=r"^[0-9]{6}$")


def _job_for_update(cur, job_id):
    cur.execute("SELECT * FROM jobs WHERE id = %s FOR UPDATE", (job_id,))
    j = cur.fetchone()
    if not j:
        raise ApiError(404, "not_found", "Not found")
    return j


def _may_drive_job(cur, j, user_id) -> None:
    if j["created_by"] == user_id or j["entity_id"] is None:
        return
    require(cur, j["entity_id"], "settings:write")


@router.post("/jobs/{job_id}/otp", tags=["Portals & jobs"])
def submit_otp(job_id: uuid.UUID, body: OtpIn, p: Principal = Auth):
    with tx(p.user_id) as cur:
        j = _job_for_update(cur, job_id)
        _may_drive_job(cur, j, p.user_id)
        if j["status"] != "waiting_for_otp":
            raise ApiError(409, "conflict", f"The job is {j['status']}, not waiting for an OTP")
        cur.execute("""UPDATE jobs SET status = 'queued', run_after = now(), payload = (payload - 'otp_message') || jsonb_build_object('otp_enc', %s::text)
                        WHERE id = %s""", (secretbox.encrypt(body.otp).decode(), job_id))
        if j["entity_id"]:
            audit(cur, j["entity_id"], "job.otp_submitted", "jobs", job_id, after={"kind": j["kind"]})
        cur.execute(f"SELECT {JOB_COLS} FROM jobs WHERE id = %s", (job_id,))
        return js(cur.fetchone())


@router.post("/jobs/{job_id}/cancel", tags=["Portals & jobs"])
def cancel_job(job_id: uuid.UUID, p: Principal = Auth):
    with tx(p.user_id) as cur:
        j = _job_for_update(cur, job_id)
        _may_drive_job(cur, j, p.user_id)
        if j["status"] not in ("queued", "waiting_for_otp"):
            raise ApiError(409, "conflict", f"A {j['status']} job cannot be cancelled")
        cur.execute("""UPDATE jobs SET status = 'cancelled', finished_at = now(), locked_by = NULL, payload = payload - 'otp_enc' - 'otp_session' WHERE id = %s""", (job_id,))
        from .worker import _hook
        _hook(cur, j, RuntimeError("cancelled"))
        if j["entity_id"]:
            audit(cur, j["entity_id"], "job.cancelled", "jobs", job_id, after={"kind": j["kind"]})
        cur.execute(f"SELECT {JOB_COLS} FROM jobs WHERE id = %s", (job_id,))
        return js(cur.fetchone())


# ================================================================== portal connections
PORTALS = ("gstn", "irp", "eway", "mca", "income_tax", "traces", "epfo", "esic", "professional_tax", "udyam", "account_aggregator", "bank_api", "treds", "digilocker")
SESSION_FAMILY = {"gstn": "gstn", "irp": "irp", "eway": "eway", "mca": "mca", "income_tax": "income_tax", "traces": "traces", "epfo": "epfo", "esic": "esic"}


class ConnectionIn(BaseModel):
    portal: Literal["gstn", "irp", "eway", "mca", "income_tax", "traces", "epfo", "esic", "professional_tax", "udyam", "account_aggregator", "bank_api", "treds", "digilocker"]
    registration_id: uuid.UUID | None = None
    via: Literal["GSP", "ASP", "direct"] | None = None
    owner_user_id: uuid.UUID | None = None
    credentials: dict | None = None


def effective_status(c: dict, now: dt.datetime | None = None) -> str:
    now = now or dt.datetime.now(dt.timezone.utc)
    if c["status"] in ("revoked", "not_connected", "error"):
        return c["status"]
    exp = c["session_expires_at"]
    if exp is None:
        return c["status"]
    if exp <= now:
        return "expired"
    return "expiring" if exp - now <= dt.timedelta(hours=24) else "connected"


def load_connection(cur, entity_id, cid) -> dict:
    cur.execute("""SELECT id, portal::text, registration_id, via, owner_user_id, status, session_expires_at, last_sync_at, last_error
                     FROM portal_connections WHERE id = %s AND entity_id = %s""", (cid, entity_id))
    c = cur.fetchone()
    if not c:
        raise ApiError(404, "not_found", "Not found")
    c["status"] = effective_status(c)
    return js(c)


def _ref(entity_id, portal, registration_id) -> str:
    return f"portal/{entity_id}/{portal}/{registration_id or 'entity'}"


@router.post(E + "/portal-connections", status_code=201, tags=["Portals & jobs"])
def create_connection(entity_id: uuid.UUID, body: ConnectionIn, p: Principal = Auth):
    with tx(p.user_id) as cur:
        require(cur, entity_id, "settings:write")
        if body.registration_id:
            cur.execute("SELECT 1 FROM entity_registrations WHERE id = %s AND entity_id = %s", (body.registration_id, entity_id))
            if not cur.fetchone():
                raise ApiError(400, "validation_failed", "registration_id is not a registration of this entity")
        cur.execute("SELECT 1 FROM portal_connections WHERE entity_id = %s AND portal = %s AND registration_id IS NOT DISTINCT FROM %s", (entity_id, body.portal, body.registration_id))
        if cur.fetchone():
            raise ApiError(409, "conflict", "This portal is already connected for that registration")
        ref = _ref(entity_id, body.portal, body.registration_id) if body.credentials else None
        if ref:
            adapters.secrets.put(ref, body.credentials)                         # the secret goes to the secret store; only the reference is kept
        cur.execute("""INSERT INTO portal_connections (entity_id, portal, registration_id, via, owner_user_id, credential_ref, status)
                       VALUES (%s, %s::portal_kind, %s, %s, %s, %s, %s) RETURNING id""",
                    (entity_id, body.portal, body.registration_id, body.via, body.owner_user_id or p.user_id, ref, "connected" if ref else "not_connected"))
        cid = cur.fetchone()["id"]
        audit(cur, entity_id, "portal_connection.created", "portal_connections", cid, after={"portal": body.portal, "credentials": bool(ref)})
        return load_connection(cur, entity_id, cid)


@router.get(E + "/portal-connections", tags=["Portals & jobs"])
def list_connections(entity_id: uuid.UUID, p: Principal = Auth, limit: int = Query(default=50, ge=1, le=200), cursor: str | None = None):
    off = offset_of(cursor)
    with tx(p.user_id) as cur:
        require(cur, entity_id, "settings:read")
        cur.execute("SELECT id FROM portal_connections WHERE entity_id = %s ORDER BY portal, id LIMIT %s OFFSET %s", (entity_id, limit + 1, off))
        return page([load_connection(cur, entity_id, r["id"]) for r in cur.fetchall()], limit, off)


@router.get(E + "/portal-connections/{connection_id}", tags=["Portals & jobs"])
def get_connection(entity_id: uuid.UUID, connection_id: uuid.UUID, p: Principal = Auth):
    with tx(p.user_id) as cur:
        require(cur, entity_id, "settings:read")
        return load_connection(cur, entity_id, connection_id)


@router.patch(E + "/portal-connections/{connection_id}", tags=["Portals & jobs"])
def update_connection(entity_id: uuid.UUID, connection_id: uuid.UUID, body: dict, p: Principal = Auth):
    with tx(p.user_id) as cur:
        require(cur, entity_id, "settings:write")
        cur.execute("SELECT * FROM portal_connections WHERE id = %s AND entity_id = %s FOR UPDATE", (connection_id, entity_id))
        c = cur.fetchone()
        if not c:
            raise ApiError(404, "not_found", "Not found")
        if c["status"] == "revoked":
            raise ApiError(409, "conflict", "A revoked connection cannot be changed; create a new one")
        via = body.get("via", c["via"])
        if via not in (None, "GSP", "ASP", "direct"):
            raise ApiError(400, "validation_failed", "via must be GSP, ASP or direct")
        owner = body.get("owner_user_id", c["owner_user_id"])
        ref = c["credential_ref"]
        if body.get("credentials"):
            ref = ref or _ref(entity_id, c["portal"], c["registration_id"])
            adapters.secrets.put(ref, body["credentials"])
        cur.execute("UPDATE portal_connections SET via = %s, owner_user_id = %s, credential_ref = %s, status = CASE WHEN status = 'not_connected' AND %s::text IS NOT NULL THEN 'connected' ELSE status END WHERE id = %s",
                    (via, owner, ref, ref, connection_id))
        audit(cur, entity_id, "portal_connection.updated", "portal_connections", connection_id, after={"via": via, "credentials_rotated": bool(body.get("credentials"))})
        return load_connection(cur, entity_id, connection_id)


@router.delete(E + "/portal-connections/{connection_id}", status_code=204, tags=["Portals & jobs"])
def revoke_connection(entity_id: uuid.UUID, connection_id: uuid.UUID, p: Principal = Auth):
    with tx(p.user_id) as cur:
        require(cur, entity_id, "settings:write")
        cur.execute("SELECT credential_ref FROM portal_connections WHERE id = %s AND entity_id = %s FOR UPDATE", (connection_id, entity_id))
        c = cur.fetchone()
        if not c:
            raise ApiError(404, "not_found", "Not found")
        if c["credential_ref"]:
            adapters.secrets.delete(c["credential_ref"])
        cur.execute("UPDATE portal_connections SET status = 'revoked', credential_ref = NULL, session_expires_at = NULL WHERE id = %s", (connection_id,))
        audit(cur, entity_id, "portal_connection.revoked", "portal_connections", connection_id)


@router.post(E + "/portal-connections/{connection_id}/renew", status_code=202, tags=["Portals & jobs"])
def renew_session(entity_id: uuid.UUID, connection_id: uuid.UUID, p: Principal = Auth):
    with tx(p.user_id) as cur:
        require(cur, entity_id, "settings:write")
        c = load_connection(cur, entity_id, connection_id)
        if c["status"] in ("revoked", "not_connected"):
            raise ApiError(409, "conflict", f"A {c['status'].replace('_', ' ')} connection has no credentials to renew")
        if c["portal"] not in SESSION_FAMILY:
            raise ApiError(409, "conflict", f"{c['portal']} does not use OTP sessions")
        return accepted(queue(cur, entity_id, "portal.renew_session", "portal_connections", connection_id))


def handle_renew_session(cur, job: dict) -> dict:
    from .worker import job_otp
    cur.execute("SELECT * FROM portal_connections WHERE id = %s FOR UPDATE", (job["subject_id"],))
    c = cur.fetchone()
    sessions = adapters.get("sessions")
    otp = job_otp(job)
    if otp is None:
        sessions.start(c["portal"], c["credential_ref"])                      # raises OtpRequired: the job pauses
        raise adapters.PortalUnavailable("the portal did not ask for an OTP")  # an adapter that opens a session without one is unexpected
    expires = sessions.complete(c["portal"], (job["payload"] or {}).get("otp_session", ""), otp)
    cur.execute("UPDATE portal_connections SET status = 'connected', session_expires_at = %s, last_error = NULL WHERE id = %s", (expires, c["id"]))
    audit(cur, job["entity_id"], "portal_connection.renewed", "portal_connections", c["id"], after={"expires": expires}, actor_kind="integration")
    return {"session_expires_at": expires.isoformat()}


def hook_renew_session(cur, job: dict, exc: Exception) -> None:
    cur.execute("UPDATE portal_connections SET last_error = %s WHERE id = %s", (str(exc)[:300], job["subject_id"]))


def handle_check_sessions(cur, job: dict) -> dict:
    """Daily upkeep: expire sessions and e-way bills, and warn once when a session is about to lapse."""
    entity_id = job["entity_id"]
    cur.execute("""UPDATE portal_connections SET status = 'expired' WHERE entity_id = %s AND status IN ('connected', 'expiring') AND session_expires_at <= now() RETURNING id, portal::text AS portal""", (entity_id,))
    expired = cur.fetchall()
    cur.execute("""UPDATE portal_connections SET status = 'expiring' WHERE entity_id = %s AND status = 'connected' AND session_expires_at <= now() + interval '24 hours' RETURNING id, portal::text AS portal""", (entity_id,))
    expiring = cur.fetchall()
    for c, state in [(x, "expired") for x in expired] + [(x, "expiring") for x in expiring]:
        emit(cur, entity_id, "portal.session_expiring", "portal_connections", c["id"], {"portal": c["portal"], "state": state})
    cur.execute("UPDATE eway_bills SET status = 'expired' WHERE entity_id = %s AND status IN ('generated', 'extended') AND valid_until <= now()", (entity_id,))
    return {"expired": len(expired), "expiring": len(expiring), "eway_expired": cur.rowcount}


# ================================================================== e-way bills
EWAY_THRESHOLD = Decimal("50000")
EWAY_COLS = "id, sales_document_id, purpose, ewb_no, goods_value, from_pincode, to_pincode, distance_km, transport_mode, transporter_gstin, vehicle_no, valid_until, status"
VEHICLE = re.compile(r"^[A-Z]{2}[0-9]{1,2}[A-Z]{0,3}[0-9]{4}$")


class EwayIn(BaseModel):
    sales_document_id: uuid.UUID
    purpose: Literal["supply", "sales_return", "branch_transfer", "job_work", "export", "other"] = "supply"
    goods_value: Decimal | None = Field(default=None, gt=0)
    from_pincode: str = Field(pattern=r"^[1-9][0-9]{5}$")
    to_pincode: str = Field(pattern=r"^[1-9][0-9]{5}$")
    distance_km: int = Field(ge=1, le=4000)
    transport_mode: Literal["road", "rail", "air", "ship"] = "road"
    transporter_gstin: str | None = Field(default=None, pattern=r"^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][1-9A-Z]Z[0-9A-Z]$")
    vehicle_no: str | None = None


@router.post(E + "/eway-bills", status_code=201, tags=["Sales & billing"])
def create_eway(entity_id: uuid.UUID, body: EwayIn, p: Principal = Auth):
    vehicle = body.vehicle_no.replace(" ", "").upper() if body.vehicle_no else None
    if vehicle and not VEHICLE.match(vehicle):
        raise ApiError(400, "validation_failed", "vehicle_no is not a valid registration number (e.g. KA01AB1234)")
    with tx(p.user_id) as cur:
        require(cur, entity_id, "sales:write")
        cur.execute("SELECT status, number FROM sales_documents WHERE id = %s AND entity_id = %s", (body.sales_document_id, entity_id))
        d = cur.fetchone()
        if not d:
            raise ApiError(404, "not_found", "Not found")
        if d["status"] != "issued":
            raise ApiError(409, "conflict", "An e-way bill needs an issued document")
        cur.execute("""SELECT coalesce(sum(taxable_value + cgst_amount + sgst_amount + igst_amount), 0) AS goods FROM sales_document_lines WHERE document_id = %s AND NOT is_service""",
                    (body.sales_document_id,))
        goods = cur.fetchone()["goods"]
        value = body.goods_value or goods
        if value <= 0:
            raise ApiError(400, "validation_failed", "The document has no goods to move; give goods_value or choose a document with goods")
        if body.purpose == "supply" and value <= EWAY_THRESHOLD:
            raise ApiError(400, "validation_failed", f"An e-way bill is required only above ₹50,000; this consignment is ₹{value:,.2f}")
        cur.execute("SELECT 1 FROM eway_bills WHERE sales_document_id = %s AND status IN ('draft', 'generated', 'extended') AND purpose = %s", (body.sales_document_id, body.purpose))
        if cur.fetchone():
            raise ApiError(409, "conflict", "An e-way bill is already open for this document and purpose")
        cur.execute(f"""INSERT INTO eway_bills (entity_id, sales_document_id, purpose, goods_value, from_pincode, to_pincode, distance_km, transport_mode, transporter_gstin, vehicle_no)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING {EWAY_COLS}""",
                    (entity_id, body.sales_document_id, body.purpose, value, body.from_pincode, body.to_pincode, body.distance_km, body.transport_mode, body.transporter_gstin, vehicle))
        out = js(cur.fetchone())
        queue(cur, entity_id, "eway.generate", "eway_bills", out["id"])
        audit(cur, entity_id, "eway_bill.requested", "eway_bills", out["id"], after={"document": d["number"], "value": value})
        return out


@router.get(E + "/eway-bills", tags=["Sales & billing"])
def list_eway(entity_id: uuid.UUID, p: Principal = Auth, status: str | None = None, limit: int = Query(default=50, ge=1, le=200), cursor: str | None = None):
    off = offset_of(cursor)
    with tx(p.user_id) as cur:
        require(cur, entity_id, "sales:read")
        cur.execute(f"SELECT {EWAY_COLS} FROM eway_bills WHERE entity_id = %s AND (%s::text IS NULL OR status = %s) ORDER BY coalesce(generated_at, now()) DESC, id LIMIT %s OFFSET %s",
                    (entity_id, status, status, limit + 1, off))
        return page(js(cur.fetchall()), limit, off)


class VehicleIn(BaseModel):
    vehicle_no: str
    reason: str | None = None


@router.post(E + "/eway-bills/{eway_id}/vehicle", status_code=202, tags=["Sales & billing"])
def update_vehicle(entity_id: uuid.UUID, eway_id: uuid.UUID, body: VehicleIn, p: Principal = Auth, idempotency_key: str | None = Header(default=None, alias="Idempotency-Key")):
    vehicle = body.vehicle_no.replace(" ", "").upper()
    if not VEHICLE.match(vehicle):
        raise ApiError(400, "validation_failed", "vehicle_no is not a valid registration number (e.g. KA01AB1234)")
    with tx(p.user_id) as cur:
        require(cur, entity_id, "sales:write")
        replay = idempotency_begin(cur, idempotency_key, "POST", f"/entities/{entity_id}/eway-bills/{eway_id}/vehicle", body.model_dump())
        if replay:
            return JSONResponse(replay["body"], status_code=replay["status"])
        cur.execute("SELECT status FROM eway_bills WHERE id = %s AND entity_id = %s", (eway_id, entity_id))
        e = cur.fetchone()
        if not e:
            raise ApiError(404, "not_found", "Not found")
        if e["status"] not in ("generated", "extended"):
            raise ApiError(409, "conflict", f"Part B can only be set on a generated e-way bill (this one is {e['status']})")
        job = queue(cur, entity_id, "eway.update_vehicle", "eway_bills", eway_id, {"vehicle_no": vehicle, "reason": body.reason or "Change of vehicle"})
        idempotency_finish(cur, idempotency_key, 202, job)
    return accepted(job)


def handle_eway_generate(cur, job: dict) -> dict:
    cur.execute("""SELECT w.*, d.number AS doc_no, r.number AS seller_gstin FROM eway_bills w JOIN sales_documents d ON d.id = w.sales_document_id
                     JOIN entity_registrations r ON r.id = d.registration_id WHERE w.id = %s FOR UPDATE OF w""", (job["subject_id"],))
    w = cur.fetchone()
    if w["status"] != "draft":
        return {"ewb_no": w["ewb_no"], "note": f"already {w['status']}"}
    res = adapters.get("eway").generate({"seller_gstin": w["seller_gstin"], "doc_no": w["doc_no"], "goods_value": str(w["goods_value"]), "from_pincode": w["from_pincode"],
                                         "to_pincode": w["to_pincode"], "distance_km": w["distance_km"], "transport_mode": w["transport_mode"], "vehicle_no": w["vehicle_no"]})
    cur.execute("UPDATE eway_bills SET status = 'generated', ewb_no = %s, generated_at = now(), valid_until = %s WHERE id = %s", (res["ewb_no"], res["valid_until"], w["id"]))
    audit(cur, job["entity_id"], "eway_bill.generated", "eway_bills", w["id"], after={"ewb_no": res["ewb_no"]}, actor_kind="integration")
    return {"ewb_no": res["ewb_no"], "valid_until": res["valid_until"]}


def handle_eway_vehicle(cur, job: dict) -> dict:
    cur.execute("SELECT * FROM eway_bills WHERE id = %s FOR UPDATE", (job["subject_id"],))
    w = cur.fetchone()
    pl = job["payload"]
    adapters.get("eway").update_vehicle(w["ewb_no"], pl["vehicle_no"], pl["reason"])
    cur.execute("UPDATE eway_bills SET vehicle_no = %s WHERE id = %s", (pl["vehicle_no"], w["id"]))
    audit(cur, job["entity_id"], "eway_bill.vehicle_updated", "eway_bills", w["id"], after={"vehicle_no": pl["vehicle_no"]}, actor_kind="integration")
    return {"vehicle_no": pl["vehicle_no"]}


# ================================================================== e-invoice cancellation
class CancelIn(BaseModel):
    reason: Literal["duplicate", "data_entry_mistake", "order_cancelled", "other"]
    remark: str | None = Field(default=None, max_length=200)


@router.post(E + "/sales-documents/{doc_id}/cancel", status_code=202, tags=["Sales & billing"])
def cancel_document(entity_id: uuid.UUID, doc_id: uuid.UUID, body: CancelIn, p: Principal = Auth, idempotency_key: str | None = Header(default=None, alias="Idempotency-Key")):
    with tx(p.user_id) as cur:
        require(cur, entity_id, "sales:write")
        replay = idempotency_begin(cur, idempotency_key, "POST", f"/entities/{entity_id}/sales-documents/{doc_id}/cancel", body.model_dump())
        if replay:
            return JSONResponse(replay["body"], status_code=replay["status"])
        cur.execute("SELECT status, irn_status::text AS irn_status, irn_ack_at, amount_settled, doc_type::text AS doc_type FROM sales_documents WHERE id = %s AND entity_id = %s FOR UPDATE", (doc_id, entity_id))
        d = cur.fetchone()
        if not d:
            raise ApiError(404, "not_found", "Not found")
        if d["status"] != "issued":
            raise ApiError(409, "conflict", f"Only an issued document can be cancelled (this one is {d['status']})")
        if d["amount_settled"] > 0:
            raise ApiError(409, "conflict", "The document has receipts or credit notes against it; issue a credit note instead")
        if d["doc_type"] == "credit_note":
            raise ApiError(409, "conflict", "A credit note cannot be cancelled once it has reduced an invoice; issue a debit note")
        if d["irn_status"] == "generated" and dt.datetime.now(dt.timezone.utc) - d["irn_ack_at"] > dt.timedelta(hours=24):
            raise ApiError(409, "conflict", "An IRN can be cancelled only within 24 hours of generation; issue a credit note instead")
        if d["irn_status"] == "pending":
            raise ApiError(409, "conflict", "The IRN is still being generated; wait for it, then cancel")
        job = queue(cur, entity_id, "einvoice.cancel_irn", "sales_documents", doc_id, body.model_dump())
        idempotency_finish(cur, idempotency_key, 202, job)
    return accepted(job)


def handle_cancel_document(cur, job: dict) -> dict:
    entity_id = job["entity_id"]
    cur.execute("SELECT * FROM sales_documents WHERE id = %s FOR UPDATE", (job["subject_id"],))
    d = cur.fetchone()
    if d["status"] == "cancelled":
        return {"note": "already cancelled"}
    if d["irn_status"] == "generated":
        adapters.get("irp").cancel_irn(d["irn"], job["payload"]["reason"])
    cur.execute("UPDATE sales_documents SET status = 'cancelled', irn_status = CASE WHEN irn_status = 'generated' THEN 'cancelled' ELSE irn_status END, "
                "irn_cancelled_at = CASE WHEN irn_status = 'generated' THEN now() END WHERE id = %s", (d["id"],))
    if d["journal_entry_id"]:
        ledger.reverse_journal(cur, entity_id, d["journal_entry_id"], dt.date.today(), f"Cancellation of {d['number']}")
    audit(cur, entity_id, "sales_document.cancelled", "sales_documents", d["id"], after={"reason": job["payload"]["reason"], "number": d["number"]}, actor_kind="integration")
    return {"cancelled": d["number"]}


# ================================================================== filing an obligation with its portal
class FileIn(BaseModel):
    signing: Literal["dsc", "evc_otp", "none"] = "evc_otp"
    dsc_certificate_id: uuid.UUID | None = None


@router.post(E + "/obligations/{obligation_id}/file", status_code=202, tags=["Compliance"])
def file_obligation(entity_id: uuid.UUID, obligation_id: uuid.UUID, body: FileIn, p: Principal = Auth, idempotency_key: str | None = Header(default=None, alias="Idempotency-Key")):
    with tx(p.user_id) as cur:
        require(cur, entity_id, "compliance:file")
        replay = idempotency_begin(cur, idempotency_key, "POST", f"/entities/{entity_id}/obligations/{obligation_id}/file", body.model_dump(mode="json"))
        if replay:
            return JSONResponse(replay["body"], status_code=replay["status"])
        cur.execute("""SELECT o.status::text, r.code, r.authority_code FROM obligations o JOIN compliance_rules r ON r.id = o.rule_id WHERE o.id = %s AND o.entity_id = %s FOR UPDATE OF o""",
                    (obligation_id, entity_id))
        o = cur.fetchone()
        if not o:
            raise ApiError(404, "not_found", "Not found")
        if o["status"] == "filed":
            raise ApiError(409, "conflict", "Already filed")
        if o["code"].startswith("GST."):
            raise ApiError(409, "conflict", "GST returns are filed from the return: POST /gst-returns/{id}/file")
        if o["code"] not in FILERS:
            raise ApiError(409, "conflict", f"{o['code']} is filed through its payment or has no portal filing yet")
        if body.signing == "dsc" and not body.dsc_certificate_id:
            raise ApiError(400, "validation_failed", "Signing with a DSC needs dsc_certificate_id")
        if o["status"] != "ready_to_file":
            raise ApiError(409, "approval_required", "The obligation has not been approved for filing")
        job = queue(cur, entity_id, "obligation.file", "obligations", obligation_id, body.model_dump(mode="json"))
        audit(cur, entity_id, "obligation.filing_requested", "obligations", obligation_id, after={"code": o["code"]})
        idempotency_finish(cur, idempotency_key, 202, job)
    return accepted(job)


def _file_mca(cur, o, job) -> str:
    cur.execute("SELECT cin FROM entities WHERE id = %s", (o["entity_id"],))
    cin = cur.fetchone()["cin"]
    return adapters.get("mca").file_form(o["form_code"] or o["code"], {"cin": cin, "period": o["period_label"], "fy": o["fy"]})["srn"]


def _payroll_amount(cur, o, expr: str):
    cur.execute(f"""SELECT coalesce(sum({expr}), 0) AS amt FROM payroll_runs r JOIN payslips s ON s.run_id = r.id WHERE r.entity_id = %s AND r.period_month = %s
                       AND r.status IN ('approved', 'bank_file_sent', 'paid', 'statutory_done')""", (o["entity_id"], o["period_start"]))
    amt = cur.fetchone()["amt"]
    if amt <= 0:
        raise ApiError(409, "conflict", "There is no approved payroll for this period to file from")
    return amt


def _file_epf(cur, o, job) -> str:
    from .worker import job_otp
    amt = _payroll_amount(cur, o, "s.pf_employee + s.pf_employer_epf + s.pf_employer_eps")
    cur.execute("SELECT number FROM entity_registrations WHERE id = %s", (o["registration_id"],))
    return adapters.get("labour").upload_ecr({"establishment": cur.fetchone()["number"], "period": o["period_start"].strftime("%Y-%m"), "amount": str(amt)}, otp=job_otp(job))["trrn"]


def _file_esi(cur, o, job) -> str:
    amt = _payroll_amount(cur, o, "s.esi_employee + s.esi_employer")
    cur.execute("SELECT number FROM entity_registrations WHERE id = %s", (o["registration_id"],))
    return adapters.get("labour").upload_esic({"employer_code": cur.fetchone()["number"], "period": o["period_start"].strftime("%Y-%m"), "amount": str(amt)})["ack"]


def _file_tds_return(cur, o, job) -> str:
    cur.execute("SELECT id, form, token_no, status FROM tds_returns WHERE obligation_id = %s ORDER BY form", (o["id"],))
    rets = [r for r in cur.fetchall() if r["status"] != "drafting" or r["token_no"]]
    if not rets or any(r["status"] != "ready" for r in rets):
        raise ApiError(409, "conflict", "Validate the TDS returns first; every return with deductions must be ready")
    acks = []
    for r in rets:
        ack = adapters.get("traces").upload_return(r["token_no"], r["form"])["ack"]
        cur.execute("UPDATE tds_returns SET status = 'filed', filed_at = now() WHERE id = %s", (r["id"],))
        acks.append(f"{r['form']}:{ack}")
    return ",".join(acks)


FILERS = {"MCA.AOC4": _file_mca, "MCA.MGT7": _file_mca, "MCA.MSME1": _file_mca, "MCA.MGT14": _file_mca, "EPF.ECR.MONTHLY": _file_epf, "ESI.MONTHLY": _file_esi,
          "TDS.RETURN.QUARTERLY": _file_tds_return}


def handle_file_obligation(cur, job: dict) -> dict:
    cur.execute("""SELECT o.*, r.code, r.form_code FROM obligations o JOIN compliance_rules r ON r.id = o.rule_id WHERE o.id = %s FOR UPDATE OF o""", (job["subject_id"],))
    o = cur.fetchone()
    if o["status"] == "filed":
        return {"acknowledgement": o["acknowledgement"], "note": "already filed"}
    ack = FILERS[o["code"]](cur, o, job)
    _obligation_done(cur, job["entity_id"], o["id"], ack)
    audit(cur, job["entity_id"], "obligation.filed", "obligations", o["id"], after={"code": o["code"], "acknowledgement": ack}, actor_kind="integration")
    return {"acknowledgement": ack}


# ================================================================== Form 26AS
class SyncIn(BaseModel):
    fy: str = Field(pattern=r"^[0-9]{4}-[0-9]{2}$")


@router.post(E + "/tds-receivables/sync", status_code=202, tags=["GST & tax"])
def sync_26as(entity_id: uuid.UUID, body: SyncIn, p: Principal = Auth):
    with tx(p.user_id) as cur:
        require(cur, entity_id, "tds:write")
        cur.execute("SELECT pan FROM entities WHERE id = %s", (entity_id,))
        if not cur.fetchone()["pan"]:
            raise ApiError(409, "conflict", "The entity has no PAN on record to fetch Form 26AS with")
        return accepted(queue(cur, entity_id, "tds.fetch_26as", "entities", entity_id, body.model_dump()))


def handle_fetch_26as(cur, job: dict) -> dict:
    entity_id, fy = job["entity_id"], job["payload"]["fy"]
    cur.execute("SELECT pan FROM entities WHERE id = %s", (entity_id,))
    entries = adapters.get("traces").fetch_26as(cur.fetchone()["pan"], fy)
    matched = 0
    for e in entries:
        cur.execute("""UPDATE tds_receivables SET seen_in_26as_at = now() WHERE id = (
                           SELECT id FROM tds_receivables WHERE entity_id = %s AND fy = %s AND quarter = %s AND section = %s AND seen_in_26as_at IS NULL
                              AND abs(amount - %s::numeric) <= 1 ORDER BY abs(amount - %s::numeric), id LIMIT 1)""",
                    (entity_id, fy, e["quarter"], e["section"], e["amount"], e["amount"]))
        matched += cur.rowcount
    cur.execute("SELECT count(*) AS n FROM tds_receivables WHERE entity_id = %s AND fy = %s AND seen_in_26as_at IS NULL", (entity_id, fy))
    return {"entries": len(entries), "matched": matched, "still_unmatched": cur.fetchone()["n"]}


# ================================================================== Account Aggregator
class AaConsentIn(BaseModel):
    bank_account_ids: list[uuid.UUID] = Field(min_length=1)
    valid_until: dt.datetime


def load_consent(cur, entity_id, cid) -> dict:
    cur.execute("SELECT id, consent_handle, bank_account_ids, status, valid_until FROM aa_consents WHERE id = %s AND entity_id = %s", (cid, entity_id))
    c = cur.fetchone()
    if not c:
        raise ApiError(404, "not_found", "Not found")
    c["redirect_url"] = adapters.get("aa").consent_url(c.pop("consent_handle"))
    return js(c)


@router.post(E + "/aa-consents", status_code=201, tags=["Portals & jobs"])
def create_consent(entity_id: uuid.UUID, body: AaConsentIn, p: Principal = Auth):
    now = dt.datetime.now(dt.timezone.utc)
    if body.valid_until.tzinfo is None or body.valid_until <= now or body.valid_until > now + dt.timedelta(days=730):
        raise ApiError(400, "validation_failed", "valid_until must be a future timestamp with a time zone, within two years")
    with tx(p.user_id) as cur:
        require(cur, entity_id, "settings:write")
        cur.execute("SELECT count(*) AS n FROM bank_accounts WHERE entity_id = %s AND id = ANY(%s) AND is_active", (entity_id, body.bank_account_ids))
        if cur.fetchone()["n"] != len(set(body.bank_account_ids)):
            raise ApiError(400, "validation_failed", "bank_account_ids must all be active bank accounts of this entity")
        handle = "AAC-" + uuid.uuid4().hex[:16].upper()
        adapters.get("aa").create_consent(handle, [str(b) for b in body.bank_account_ids], body.valid_until)
        cur.execute("INSERT INTO aa_consents (entity_id, consent_handle, bank_account_ids, status, valid_until) VALUES (%s, %s, %s, 'pending', %s) RETURNING id",
                    (entity_id, handle, list(set(body.bank_account_ids)), body.valid_until))
        cid = cur.fetchone()["id"]
        audit(cur, entity_id, "aa_consent.requested", "aa_consents", cid, after={"accounts": len(body.bank_account_ids)})
        return load_consent(cur, entity_id, cid)


@router.get(E + "/aa-consents", tags=["Portals & jobs"])
def list_consents(entity_id: uuid.UUID, p: Principal = Auth, limit: int = Query(default=50, ge=1, le=200), cursor: str | None = None):
    off = offset_of(cursor)
    with tx(p.user_id) as cur:
        require(cur, entity_id, "settings:read")
        cur.execute("SELECT id FROM aa_consents WHERE entity_id = %s ORDER BY valid_until DESC, id LIMIT %s OFFSET %s", (entity_id, limit + 1, off))
        return page([load_consent(cur, entity_id, r["id"]) for r in cur.fetchall()], limit, off)


AA_STATUS = {"ACTIVE": "active", "PAUSED": "paused", "REVOKED": "revoked", "REJECTED": "revoked", "EXPIRED": "expired"}


def aa_secret() -> str:
    s = os.environ.get("AA_WEBHOOK_SECRET")
    if s:
        return s
    if os.environ.get("BIZ_ENV", "dev") != "dev":
        raise RuntimeError("AA_WEBHOOK_SECRET must be set outside development")
    return "dev-aa-webhook-secret"


@router.post("/integrations/aa/notify", status_code=204, tags=["Portals & jobs"])
async def aa_notify(request: Request, x_aa_signature: str = Header(default="")):
    raw = await request.body()
    if not x_aa_signature or not adapters.hmac.compare_digest(x_aa_signature, adapters.webhook_signature(aa_secret(), raw)):
        raise ApiError(401, "unauthorized", "Bad signature")
    try:
        doc = json.loads(raw)
        handle, status = doc["consent_handle"], AA_STATUS[doc["status"].upper()]
    except (ValueError, KeyError, AttributeError):
        raise ApiError(400, "validation_failed", "Expected {consent_handle, status}")
    with tx(None, role="biz_worker") as cur:
        cur.execute("UPDATE aa_consents SET status = %s WHERE consent_handle = %s RETURNING id, entity_id", (status, handle))
        c = cur.fetchone()
        if not c:
            raise ApiError(404, "not_found", "Unknown consent")
        audit(cur, c["entity_id"], f"aa_consent.{status}", "aa_consents", c["id"], actor_kind="integration")
        if status == "active":
            cur.execute("INSERT INTO jobs (entity_id, kind, subject, subject_id) VALUES (%s, 'aa.sync', 'aa_consents', %s)", (c["entity_id"], c["id"]))


def handle_aa_sync(cur, job: dict) -> dict:
    from .routes_finance import import_lines
    entity_id = job["entity_id"]
    only = job["subject_id"] if job["subject"] == "aa_consents" else None           # a callback syncs one consent; the daily job syncs them all
    cur.execute("SELECT * FROM aa_consents WHERE entity_id = %s AND status = 'active' AND valid_until > now() AND (%s::uuid IS NULL OR id = %s)", (entity_id, only, only))
    total_imported = total_suggested = accounts = 0
    for c in cur.fetchall():
        for bid in c["bank_account_ids"]:
            cur.execute("SELECT b.account_last4, coalesce((SELECT max(txn_date) FROM bank_transactions t WHERE t.bank_account_id = b.id), current_date - 30) AS since FROM bank_accounts b WHERE b.id = %s", (bid,))
            b = cur.fetchone()
            lines = adapters.get("aa").fetch(c["consent_handle"], b["account_last4"], b["since"] - dt.timedelta(days=1), dt.date.today())
            imported, suggested = import_lines(cur, entity_id, bid, lines)
            total_imported += imported
            total_suggested += suggested
            accounts += 1
    return {"accounts": accounts, "imported": total_imported, "suggested": total_suggested}


# ================================================================== registry lookups (onboarding)
KINDS = [("cin", re.compile(r"^[LU][0-9]{5}[A-Z]{2}[0-9]{4}[A-Z]{3}[0-9]{6}$")), ("llpin", re.compile(r"^[A-Z]{3}-[0-9]{4}$")),
         ("gstin", re.compile(r"^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][1-9A-Z]Z[0-9A-Z]$")), ("pan", re.compile(r"^[A-Z]{5}[0-9]{4}[A-Z]$"))]


class LookupIn(BaseModel):
    identifier: str = Field(min_length=3, max_length=30)


def lookup_out(row: dict) -> dict:
    res = row["result"] or {}
    return js({"id": row["id"], "identifier": row["identifier"], "identifier_kind": row["kind"], "status": row["status"], "entity": res.get("entity"),
               "gstins": res.get("gstins", []), "udyam": res.get("udyam")})


@router.post("/onboarding/lookups", status_code=201, tags=["Onboarding"])
def create_lookup(body: LookupIn, p: Principal = Auth):
    ident = body.identifier.strip().upper()
    kind = next((k for k, rx in KINDS if rx.match(ident)), None)
    if not kind:
        raise ApiError(400, "validation_failed", "Enter a CIN, LLPIN, GSTIN or PAN", errors=[{"field": "identifier", "message": "not a recognised format"}])
    with tx(p.user_id) as cur:
        cur.execute("SELECT status, result FROM app.cached_lookup(%s)", (ident,))
        cached = cur.fetchone()
        if cached:
            cur.execute("""INSERT INTO registry_lookups (user_id, identifier, kind, status, result, finished_at) VALUES (app.current_user_id(), %s, %s, %s, %s, now())
                           RETURNING id, identifier, kind, status, result""", (ident, kind, cached["status"], json.dumps(cached["result"]) if cached["result"] is not None else None))
            return lookup_out(cur.fetchone())
        cur.execute("INSERT INTO registry_lookups (user_id, identifier, kind) VALUES (app.current_user_id(), %s, %s) RETURNING id, identifier, kind, status, result", (ident, kind))
        row = cur.fetchone()
        queue(cur, None, "registry.lookup", "registry_lookups", row["id"], {"identifier": ident, "kind": kind})
        return lookup_out(row)


@router.get("/onboarding/lookups/{lookup_id}", tags=["Onboarding"])
def get_lookup(lookup_id: uuid.UUID, p: Principal = Auth):
    with tx(p.user_id) as cur:
        cur.execute("SELECT id, identifier, kind, status, result FROM registry_lookups WHERE id = %s", (lookup_id,))
        row = cur.fetchone()
        if not row:
            raise ApiError(404, "not_found", "Not found")
        return lookup_out(row)


def handle_registry_lookup(cur, job: dict) -> dict:
    pl = job["payload"]
    found = adapters.get("mca").lookup(pl["identifier"], pl["kind"])
    if found is None:
        cur.execute("UPDATE registry_lookups SET status = 'not_found', finished_at = now() WHERE id = %s", (job["subject_id"],))
        return {"status": "not_found"}
    body = {k: v for k, v in found.items() if k not in ("gstins", "udyam", "kind")}
    cur.execute("UPDATE registry_lookups SET status = 'found', result = %s, finished_at = now() WHERE id = %s",
                (json.dumps({"entity": body, "gstins": found.get("gstins", []), "udyam": found.get("udyam")}, default=jsonable), job["subject_id"]))
    return {"status": "found"}


def hook_registry_lookup(cur, job: dict, exc: Exception) -> None:
    cur.execute("UPDATE registry_lookups SET status = 'failed', error = %s, finished_at = now() WHERE id = %s AND status = 'running'", (str(exc)[:300], job["subject_id"]))
