"""Copilot: conversations answered from the entity's own records, with citations, and actions that only run after a person approves them.

The model never touches the database. It is handed `Tools` that read through the caller's own session (same row-level security as any
request) and it returns an answer plus *proposed* actions. A proposed action does nothing until someone other than the requester
approves it in the approvals inbox; the database refuses an 'approved' or 'executed' action without an approval request. Which model
answers is the `copilot` adapter (`BIZ_ADAPTERS=copilot=...`); the sandbox one is rule-based and refused outside development.
"""
from __future__ import annotations

import datetime as dt
import json
import uuid
from typing import Any

from fastapi import APIRouter, Header, Query, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

from . import adapters
from .core import ApiError, Auth, Principal, audit, emit, idempotency_begin, idempotency_finish, jsonable, require, tx
from .routes_orders import js, offset_of, page

router = APIRouter()
E = "/entities/{entity_id}"
DEFAULT_TITLE = "New conversation"


# ================================================================== what the model may read
class Tools:
    """Read-only lookups for one entity, run on the caller's cursor. Each returns plain JSON-safe data."""

    def __init__(self, cur, entity_id):
        self.cur, self.entity_id = cur, entity_id

    def filings(self) -> list[dict]:
        self.cur.execute("""SELECT o.id, r.title, o.period_label, coalesce(o.extended_due_date, o.due_date) AS due, o.status::text AS status,
                                   coalesce(o.extended_due_date, o.due_date) - current_date AS days
                              FROM obligations o JOIN compliance_rules r ON r.id = o.rule_id
                             WHERE o.entity_id = %s AND o.status NOT IN ('filed', 'waived', 'not_applicable')
                             ORDER BY coalesce(o.extended_due_date, o.due_date), r.title LIMIT 40""", (self.entity_id,))
        return [{"id": str(r["id"]), "label": f"{r['title']} · {r['period_label']}", "due": r["due"].isoformat(), "days": r["days"], "status": r["status"]} for r in self.cur.fetchall()]

    def itc_gaps(self) -> list[dict]:
        self.cur.execute("""SELECT b.party_id, p.legal_name, count(*) AS invoices, sum(b.cgst_total + b.sgst_total + b.igst_total) AS tax, array_agg(m.id) AS ids
                              FROM itc_matches m JOIN purchase_bills b ON b.id = m.purchase_bill_id JOIN parties p ON p.id = b.party_id
                             WHERE m.entity_id = %s AND m.status = 'not_in_portal' AND m.action_taken_at IS NULL
                             GROUP BY b.party_id, p.legal_name ORDER BY sum(b.cgst_total + b.sgst_total + b.igst_total) DESC""", (self.entity_id,))
        return [{"party_id": str(r["party_id"]), "supplier": r["legal_name"], "invoices": r["invoices"], "tax": str(r["tax"]), "match_ids": [str(i) for i in r["ids"]]} for r in self.cur.fetchall()]

    def receivables(self) -> list[dict]:
        self.cur.execute("""SELECT document_id, number, legal_name, balance_due, days_overdue FROM v_receivables_ageing
                             WHERE entity_id = %s ORDER BY days_overdue DESC, balance_due DESC LIMIT 60""", (self.entity_id,))
        return [{"document_id": str(r["document_id"]), "number": r["number"], "party": r["legal_name"], "balance_due": str(r["balance_due"]), "days_overdue": r["days_overdue"]} for r in self.cur.fetchall()]


# ================================================================== threads and messages
class ThreadIn(BaseModel):
    title: str | None = Field(default=None, max_length=120)
    module: str | None = Field(default=None, max_length=40)


class AskIn(BaseModel):
    content: str = Field(min_length=1, max_length=4000)


def _own_thread(cur, entity_id, thread_id) -> dict:
    cur.execute("SELECT id, title, module, created_at FROM copilot_threads WHERE id = %s AND entity_id = %s AND user_id = app.current_user_id()", (thread_id, entity_id))
    t = cur.fetchone()
    if not t:
        raise ApiError(404, "not_found", "Not found")
    return t


def _message_out(cur, m: dict) -> dict:
    cur.execute("SELECT ordinal, subject, subject_id, label FROM copilot_citations WHERE message_id = %s ORDER BY ordinal", (m["id"],))
    m["citations"] = cur.fetchall()
    cur.execute("SELECT id, kind, payload, status, approval_request_id FROM copilot_actions WHERE message_id = %s ORDER BY kind, id", (m["id"],))
    m["actions"] = cur.fetchall()
    return m


@router.post(E + "/copilot/threads", status_code=201, tags=["Copilot"])
def create_thread(entity_id: uuid.UUID, body: ThreadIn, p: Principal = Auth):
    with tx(p.user_id) as cur:
        require(cur, entity_id, "copilot:use")
        cur.execute("INSERT INTO copilot_threads (entity_id, user_id, title, module) VALUES (%s, app.current_user_id(), %s, %s) RETURNING id, title, module, created_at",
                    (entity_id, (body.title or DEFAULT_TITLE).strip() or DEFAULT_TITLE, body.module))
        t = cur.fetchone()
        t["messages"] = []
        return js(t)


@router.get(E + "/copilot/threads", tags=["Copilot"])
def list_threads(entity_id: uuid.UUID, p: Principal = Auth, limit: int = Query(default=50, ge=1, le=200), cursor: str | None = None):
    off = offset_of(cursor)
    with tx(p.user_id) as cur:
        require(cur, entity_id, "copilot:use")
        cur.execute("""SELECT id, title, module, created_at FROM copilot_threads WHERE entity_id = %s AND user_id = app.current_user_id()
                        ORDER BY created_at DESC, id LIMIT %s OFFSET %s""", (entity_id, limit + 1, off))
        return page(js(cur.fetchall()), limit, off)


@router.get(E + "/copilot/threads/{thread_id}", tags=["Copilot"])
def get_thread(entity_id: uuid.UUID, thread_id: uuid.UUID, p: Principal = Auth):
    with tx(p.user_id) as cur:
        require(cur, entity_id, "copilot:use")
        t = _own_thread(cur, entity_id, thread_id)
        cur.execute("SELECT id, role, content, created_at FROM copilot_messages WHERE thread_id = %s AND role IN ('user', 'assistant') ORDER BY created_at, id", (thread_id,))
        t["messages"] = [_message_out(cur, m) for m in cur.fetchall()]
        return js(t)


def _sse(event: str, data: Any) -> str:
    return f"event: {event}\ndata: {json.dumps(data, default=jsonable)}\n\n"


@router.post(E + "/copilot/threads/{thread_id}/messages", status_code=201, tags=["Copilot"])
def ask(entity_id: uuid.UUID, thread_id: uuid.UUID, body: AskIn, request: Request, p: Principal = Auth):
    question = body.content.strip()
    if not question:
        raise ApiError(400, "validation_failed", "Ask a question")
    with tx(p.user_id) as cur:
        require(cur, entity_id, "copilot:use")
        t = _own_thread(cur, entity_id, thread_id)
        cur.execute("INSERT INTO copilot_messages (thread_id, role, content, created_at) VALUES (%s, 'user', %s, clock_timestamp())", (thread_id, question))
        brain = adapters.get("copilot")
        try:
            r = brain.reply(question, Tools(cur, entity_id))
        except adapters.PortalUnavailable as e:
            raise ApiError(503, "unavailable", "The copilot is not available right now", str(e)) from e
        cur.execute("INSERT INTO copilot_messages (thread_id, role, content, model, created_at) VALUES (%s, 'assistant', %s, %s, clock_timestamp()) RETURNING id, role, content, created_at",
                    (thread_id, r["content"], getattr(brain, "name", type(brain).__name__)))
        msg = cur.fetchone()
        for i, c in enumerate(r["citations"], start=1):
            cur.execute("INSERT INTO copilot_citations (message_id, ordinal, subject, subject_id, label) VALUES (%s, %s, %s, %s, %s)", (msg["id"], i, c["subject"], c["subject_id"], c["label"]))
        for a in r["actions"]:
            cur.execute("INSERT INTO copilot_actions (message_id, entity_id, kind, payload) VALUES (%s, %s, %s, %s)", (msg["id"], entity_id, a["kind"], json.dumps(a["payload"])))
        if t["title"] == DEFAULT_TITLE:
            cur.execute("UPDATE copilot_threads SET title = %s, module = coalesce(module, %s) WHERE id = %s", (question[:60], r.get("module"), thread_id))
        audit(cur, entity_id, "copilot.answered", "copilot_threads", thread_id, after={"citations": len(r["citations"]), "proposed_actions": len(r["actions"])}, actor_kind="copilot")
        out = js(_message_out(cur, msg))
    if "text/event-stream" not in request.headers.get("accept", ""):
        return JSONResponse(out, status_code=201)

    def stream():
        words = out["content"].split(" ")
        for i, w in enumerate(words):
            yield _sse("token", {"text": w + (" " if i < len(words) - 1 else "")})
        for c in out["citations"]:
            yield _sse("citation", c)
        for a in out["actions"]:
            yield _sse("action", a)
        yield _sse("done", {"message_id": out["id"]})
    return StreamingResponse(stream(), media_type="text/event-stream", status_code=201)


# ================================================================== proposed actions
ACTION_KINDS = {"send_supplier_reminders", "create_task"}


def _summary(kind: str, payload: dict) -> str:
    if kind == "send_supplier_reminders":
        n = len(payload.get("party_ids", []))
        return f"Copilot: remind {n} {'supplier' if n == 1 else 'suppliers'} about invoices missing from GSTR-2A"
    return f"Copilot: {str(payload.get('title', 'create a follow-up task'))[:180]}"


class ActionApprovalIn(BaseModel):
    assigned_to: uuid.UUID | None = None
    payload_overrides: dict = {}


@router.post(E + "/copilot/actions/{action_id}/request-approval", status_code=201, tags=["Copilot"])
def request_approval(entity_id: uuid.UUID, action_id: uuid.UUID, body: ActionApprovalIn, p: Principal = Auth,
                     idempotency_key: str | None = Header(default=None, alias="Idempotency-Key")):
    with tx(p.user_id) as cur:
        require(cur, entity_id, "copilot:use")
        replay = idempotency_begin(cur, idempotency_key, "POST", f"/entities/{entity_id}/copilot/actions/{action_id}/request-approval", body.model_dump(mode="json"))
        if replay:
            return JSONResponse(replay["body"], status_code=replay["status"])
        cur.execute("""SELECT a.id, a.kind, a.payload, a.status, a.approval_request_id FROM copilot_actions a
                         JOIN copilot_messages m ON m.id = a.message_id JOIN copilot_threads t ON t.id = m.thread_id
                        WHERE a.id = %s AND a.entity_id = %s AND t.user_id = app.current_user_id() FOR UPDATE OF a""", (action_id, entity_id))
        a = cur.fetchone()
        if not a:
            raise ApiError(404, "not_found", "Not found")
        if a["kind"] not in ACTION_KINDS:
            raise ApiError(409, "conflict", f"This copilot cannot carry out '{a['kind']}' yet")
        if a["status"] != "proposed" or a["approval_request_id"]:
            raise ApiError(409, "conflict", "This action has already been sent for approval")
        payload = {**a["payload"]}
        for k, v in body.payload_overrides.items():
            if k not in payload:
                raise ApiError(400, "validation_failed", f"'{k}' is not part of this action", errors=[{"field": "payload_overrides", "message": f"unknown key {k}"}])
            payload[k] = v
        if body.assigned_to:
            cur.execute("""SELECT 1 FROM memberships WHERE entity_id = %s AND user_id = %s AND revoked_at IS NULL AND accepted_at IS NOT NULL
                              AND role IN ('owner', 'approver')""", (entity_id, body.assigned_to))
            if not cur.fetchone():
                raise ApiError(400, "validation_failed", "The person you chose cannot approve", errors=[{"field": "assigned_to", "message": "choose an owner or approver of this company"}])
        cur.execute("""INSERT INTO approval_requests (entity_id, subject, subject_id, summary, requested_by, assigned_to)
                       VALUES (%s, 'copilot_action', %s, %s, app.current_user_id(), %s) RETURNING *""", (entity_id, action_id, _summary(a["kind"], payload), body.assigned_to))
        ar = cur.fetchone()
        cur.execute("UPDATE copilot_actions SET approval_request_id = %s, payload = %s WHERE id = %s", (ar["id"], json.dumps(payload), action_id))
        audit(cur, entity_id, "copilot_action.approval_requested", "copilot_actions", action_id, after={"kind": a["kind"], "approval_request_id": ar["id"]})
        emit(cur, entity_id, "approval.requested", "approval_requests", ar["id"], {"subject": "copilot_action"})
        out = json.loads(json.dumps(ar, default=jsonable))
        idempotency_finish(cur, idempotency_key, 201, out)
    return JSONResponse(out, status_code=201)


def on_approval_decided(cur, entity_id, action_id, outcome: str) -> None:
    """Called inside the approval decision's transaction. Approved: carry the action out (and say so); rejected: close it."""
    cur.execute("SELECT id, kind, payload, status FROM copilot_actions WHERE id = %s AND entity_id = %s FOR UPDATE", (action_id, entity_id))
    a = cur.fetchone()
    if not a or a["status"] != "proposed":
        return
    if outcome != "approved":
        cur.execute("UPDATE copilot_actions SET status = 'rejected' WHERE id = %s", (action_id,))
        audit(cur, entity_id, "copilot_action.rejected", "copilot_actions", action_id)
        return
    cur.execute("UPDATE copilot_actions SET status = 'approved' WHERE id = %s", (action_id,))
    cur.execute("SAVEPOINT copilot_exec")
    try:
        result = _execute(cur, entity_id, a["kind"], a["payload"])
    except Exception as e:                                   # the approval stands; the action is marked failed and says why
        cur.execute("ROLLBACK TO SAVEPOINT copilot_exec")
        cur.execute("UPDATE copilot_actions SET status = 'failed', payload = payload || %s WHERE id = %s", (json.dumps({"error": str(e)[:300]}), action_id))
        audit(cur, entity_id, "copilot_action.failed", "copilot_actions", action_id, after={"error": str(e)[:300]}, actor_kind="copilot")
        return
    cur.execute("RELEASE SAVEPOINT copilot_exec")
    cur.execute("UPDATE copilot_actions SET status = 'executed', executed_at = now(), payload = payload || %s WHERE id = %s", (json.dumps({"result": result}), action_id))
    audit(cur, entity_id, "copilot_action.executed", "copilot_actions", action_id, after={"kind": a["kind"], "result": result}, actor_kind="copilot")
    emit(cur, entity_id, "copilot_action.executed", "copilot_actions", action_id, {"kind": a["kind"]})


def _execute(cur, entity_id, kind: str, payload: dict) -> dict:
    if kind == "send_supplier_reminders":
        from .routes_gst import queue_supplier_reminders
        return queue_supplier_reminders(cur, entity_id, [uuid.UUID(x) for x in payload.get("party_ids", [])], source="copilot")
    if kind == "create_task":
        due = payload.get("due_on")
        cur.execute("INSERT INTO tasks (entity_id, title, due_on, source) VALUES (%s, %s, %s, 'copilot') RETURNING id",
                    (entity_id, str(payload["title"])[:200], dt.date.fromisoformat(due) if due else None))
        return {"task_id": str(cur.fetchone()["id"])}
    raise ValueError(f"unknown action kind {kind}")
