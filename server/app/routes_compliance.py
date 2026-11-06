"""Obligations, calendar and approvals."""
from __future__ import annotations

import base64
import datetime as dt
import json
import uuid

from fastapi import APIRouter, Header, Query
from pydantic import BaseModel

from .core import (ApiError, Auth, Principal, audit, emit, idempotency_begin, idempotency_finish, jsonable,
                   require, tx)

router = APIRouter()
E = "/entities/{entity_id}"


def encode_cursor(row) -> str:
    return base64.urlsafe_b64encode(json.dumps([str(row["due_on"]), str(row["id"])]).encode()).decode()


def decode_cursor(c: str):
    try:
        due, oid = json.loads(base64.urlsafe_b64decode(c.encode()))
        return dt.date.fromisoformat(due), uuid.UUID(oid)
    except Exception:
        raise ApiError(400, "validation_failed", "Bad cursor")


@router.get(E + "/obligations", tags=["Compliance"])
def list_obligations(entity_id: uuid.UUID, p: Principal = Auth,
                     status: list[str] | None = Query(default=None), authority: str | None = None,
                     due_before: dt.date | None = None, limit: int = Query(default=50, ge=1, le=200), cursor: str | None = None):
    where, args = ["o.entity_id = %s"], [entity_id]
    if status:
        where.append("o.status::text = ANY(%s)"); args.append(status)
    if authority:
        where.append("r.authority_code = %s"); args.append(authority)
    if due_before:
        where.append("coalesce(o.extended_due_date, o.due_date) <= %s"); args.append(due_before)
    if cursor:
        due, oid = decode_cursor(cursor)
        where.append("(coalesce(o.extended_due_date, o.due_date), o.id) > (%s, %s)"); args += [due, oid]
    with tx(p.user_id) as cur:
        require(cur, entity_id, "compliance:read")
        cur.execute(f"""
            SELECT o.id, o.fy, o.period_label, o.period_start, o.period_end, o.due_date, o.extended_due_date,
                   coalesce(o.extended_due_date, o.due_date) AS due_on,
                   coalesce(o.extended_due_date, o.due_date) - current_date AS days_left,
                   o.status::text, o.owner_user_id, o.approver_user_id, o.late_fee_accrued, o.interest_accrued,
                   o.reason_snapshot AS reason, o.filed_at, o.acknowledgement,
                   json_build_object('id', r.id, 'code', r.code, 'title', r.title, 'form_code', r.form_code,
                                     'authority_code', r.authority_code, 'statute_ref', r.statute_ref) AS rule,
                   CASE WHEN er.id IS NULL THEN NULL ELSE json_build_object('id', er.id, 'kind', er.kind, 'number', er.number, 'label', er.label) END AS registration
              FROM obligations o JOIN compliance_rules r ON r.id = o.rule_id
              LEFT JOIN entity_registrations er ON er.id = o.registration_id
             WHERE {' AND '.join(where)}
             ORDER BY due_on, o.id LIMIT %s""", args + [limit + 1])
        rows = cur.fetchall()
    more = len(rows) > limit
    rows = rows[:limit]
    return {"data": rows, "next_cursor": encode_cursor(rows[-1]) if more else None}


class SubmitForApproval(BaseModel):
    assigned_to: uuid.UUID | None = None
    note: str | None = None


@router.post(E + "/obligations/{obligation_id}/submit", status_code=201, tags=["Compliance"])
def submit_obligation(entity_id: uuid.UUID, obligation_id: uuid.UUID, body: SubmitForApproval, p: Principal = Auth):
    with tx(p.user_id) as cur:
        require(cur, entity_id, "compliance:write")
        cur.execute("""SELECT o.id, o.status::text, r.title, o.period_label FROM obligations o JOIN compliance_rules r ON r.id = o.rule_id
                        WHERE o.id = %s AND o.entity_id = %s FOR UPDATE OF o""", (obligation_id, entity_id))
        o = cur.fetchone()
        if not o:
            raise ApiError(404, "not_found", "Not found")
        if o["status"] in ("filed", "not_applicable", "waived", "awaiting_approval"):
            raise ApiError(409, "conflict", f"Obligation is {o['status']}")
        cur.execute("""INSERT INTO approval_requests (entity_id, subject, subject_id, summary, requested_by, assigned_to)
                       VALUES (%s, 'obligation', %s, %s, app.current_user_id(), %s) RETURNING *""",
                    (entity_id, obligation_id, f"Approve {o['title']} · {o['period_label']}", body.assigned_to))
        ar = cur.fetchone()
        cur.execute("UPDATE obligations SET status = 'awaiting_approval' WHERE id = %s", (obligation_id,))
        cur.execute("INSERT INTO obligation_events (obligation_id, actor_user_id, kind, from_status, to_status, note) "
                    "VALUES (%s, app.current_user_id(), 'status_changed', %s::obligation_status, 'awaiting_approval', %s)",
                    (obligation_id, o["status"], body.note))
        audit(cur, entity_id, "approval.requested", "approval_requests", ar["id"], after={"subject": "obligation", "subject_id": obligation_id})
        emit(cur, entity_id, "approval.requested", "approval_requests", ar["id"], {"summary": ar["summary"]})
    return ar


@router.get(E + "/approvals", tags=["Approvals"])
def list_approvals(entity_id: uuid.UUID, p: Principal = Auth, status: str = "pending", assigned_to: str | None = None):
    with tx(p.user_id) as cur:
        require(cur, entity_id, "approvals:read")
        args = [entity_id, status]
        extra = ""
        if assigned_to == "me":
            extra = " AND (assigned_to = app.current_user_id() OR assigned_to IS NULL)"
        cur.execute("SELECT * FROM approval_requests WHERE entity_id = %s AND status = %s" + extra + " ORDER BY created_at", args)
        return {"data": cur.fetchall(), "next_cursor": None}


class Decision(BaseModel):
    note: str | None = None


def _decide(entity_id, approval_id, p, body, outcome, idem_key):
    with tx(p.user_id) as cur:
        require(cur, entity_id, "approvals:approve")
        replay = idempotency_begin(cur, idem_key, "POST", f"/approvals/{approval_id}/{outcome}", body.model_dump())
        if replay:
            return replay["body"]
        cur.execute("SELECT * FROM approval_requests WHERE id = %s AND entity_id = %s FOR UPDATE", (approval_id, entity_id))
        ar = cur.fetchone()
        if not ar:
            raise ApiError(404, "not_found", "Not found")
        if ar["status"] != "pending":
            raise ApiError(409, "conflict", f"Request is already {ar['status']}")
        cur.execute("""UPDATE approval_requests SET status = %s, decided_by = app.current_user_id(), decided_at = now(),
                              decision_note = %s WHERE id = %s RETURNING *""", (outcome, body.note, approval_id))
        ar = cur.fetchone()            # two_person_rule CHECK fires here → 403
        if ar["subject"] == "obligation":
            cur.execute("UPDATE obligations SET status = %s, approver_user_id = app.current_user_id() WHERE id = %s",
                        ("ready_to_file" if outcome == "approved" else "in_preparation", ar["subject_id"]))
        if ar["subject"] == "vendor_payment":
            cur.execute("UPDATE vendor_payments SET status = %s WHERE id = %s AND status = 'scheduled'",
                        ("approved" if outcome == "approved" else "cancelled", ar["subject_id"]))
        audit(cur, entity_id, f"approval.{outcome}", "approval_requests", approval_id, after={"note": body.note})
        emit(cur, entity_id, "approval.decided", "approval_requests", approval_id, {"status": outcome})
        out = json.loads(json.dumps(ar, default=jsonable))
        idempotency_finish(cur, idem_key, 200, out)
    return out


@router.post(E + "/approvals/{approval_id}/approve", tags=["Approvals"])
def approve(entity_id: uuid.UUID, approval_id: uuid.UUID, body: Decision, p: Principal = Auth,
            idempotency_key: str | None = Header(default=None, alias="Idempotency-Key")):
    return _decide(entity_id, approval_id, p, body, "approved", idempotency_key)


@router.post(E + "/approvals/{approval_id}/reject", tags=["Approvals"])
def reject(entity_id: uuid.UUID, approval_id: uuid.UUID, body: Decision, p: Principal = Auth,
           idempotency_key: str | None = Header(default=None, alias="Idempotency-Key")):
    return _decide(entity_id, approval_id, p, body, "rejected", idempotency_key)
