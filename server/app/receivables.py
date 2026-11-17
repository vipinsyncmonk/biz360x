"""Dunning sequences and recurring invoices: the logic the worker runs, and the API for configuring both."""
from __future__ import annotations

import datetime as dt
import uuid
from typing import Literal

from fastapi import APIRouter, Query
from pydantic import BaseModel, Field

from .core import ApiError, Auth, Principal, audit, require, tx
from .engine import _add_months_date
from .routes_orders import js, offset_of, page
from .routes_sales import LineIn, SalesDocIn, create_draft, issue_core

router = APIRouter()
E = "/entities/{entity_id}"
MONTHS = {"monthly": 1, "quarterly": 3, "half_yearly": 6, "annual": 12}


# ================================================================== worker logic
def run_dunning(cur, entity_id, today: dt.date | None = None) -> dict:
    """For each open invoice, perform the steps of its customer's (or the default) sequence that fall due today.
    A step runs at most once per invoice (dunning_events is the memory)."""
    today = today or dt.date.today()
    cur.execute("""SELECT d.id, d.number, d.due_date, d.party_id, coalesce(pd.sequence_id, ds.id) AS sequence_id, pd.paused_until
                     FROM sales_documents d
                     LEFT JOIN party_dunning pd ON pd.party_id = d.party_id
                     LEFT JOIN dunning_sequences ds ON ds.entity_id = d.entity_id AND ds.is_default
                    WHERE d.entity_id = %s AND d.status = 'issued' AND d.doc_type = 'tax_invoice' AND d.balance_due > 0 AND d.due_date IS NOT NULL""",
                (entity_id,))
    sent = tasks = 0
    for d in cur.fetchall():
        if not d["sequence_id"] or (d["paused_until"] and d["paused_until"] >= today):
            continue
        cur.execute("""SELECT s.* FROM dunning_steps s WHERE s.sequence_id = %s AND s.offset_days = %s
                          AND NOT EXISTS (SELECT 1 FROM dunning_events e WHERE e.sales_document_id = %s AND e.step_id = s.id)""",
                    (d["sequence_id"], (today - d["due_date"]).days, d["id"]))
        for s in cur.fetchall():
            if s["action"] == "message":
                for ch in s["channels"]:
                    cur.execute("INSERT INTO dunning_events (sales_document_id, step_id, channel, delivery_status) VALUES (%s, %s, %s, 'queued')",
                                (d["id"], s["id"], ch))
                    sent += 1
            else:
                label = {"task": "Follow up", "escalate": "Escalate", "hold_orders": "Hold new orders"}[s["action"]]
                cur.execute("INSERT INTO tasks (entity_id, title, subject, subject_id, due_on, source) VALUES (%s, %s, 'sales_document', %s, %s, 'dunning') RETURNING id",
                            (entity_id, f"{label}: invoice {d['number']} is {(today - d['due_date']).days} days from due", d["id"], today))
                cur.execute("INSERT INTO dunning_events (sales_document_id, step_id, channel, task_id) VALUES (%s, %s, 'task', %s)",
                            (d["id"], s["id"], cur.fetchone()["id"]))
                tasks += 1
    if sent or tasks:
        audit(cur, entity_id, "dunning.run", "dunning_sequences", None, actor_kind="system", after={"messages": sent, "tasks": tasks})
    return {"messages_queued": sent, "tasks_created": tasks}


def run_recurring(cur, entity_id, today: dt.date | None = None) -> dict:
    """Draft (and optionally issue) every recurring invoice that is due, then move next_run_on forward."""
    today = today or dt.date.today()
    cur.execute("""SELECT * FROM recurring_invoices WHERE entity_id = %s AND is_active AND next_run_on <= %s
                      AND (ends_on IS NULL OR next_run_on <= ends_on) ORDER BY next_run_on, id FOR UPDATE""", (entity_id, today))
    made, issued = [], 0
    for r in cur.fetchall():
        cur.execute("SELECT * FROM sales_documents WHERE id = %s", (r["template_document_id"],))
        t = cur.fetchone()
        cur.execute("SELECT * FROM sales_document_lines WHERE document_id = %s ORDER BY line_no", (t["id"],))
        lines = [LineIn(item_id=l["item_id"], description=l["description"], hsn_sac=l["hsn_sac"], qty=l["qty"], rate=l["rate"],
                        gst_rate=l["gst_rate"], discount=l["discount"], uqc=l["uqc"]) for l in cur.fetchall()]
        run_on = r["next_run_on"]
        for _ in range(12):                                   # catch up missed periods, but never run away
            if run_on > today or (r["ends_on"] and run_on > r["ends_on"]):
                break
            doc_id = create_draft(cur, entity_id, SalesDocIn(
                doc_type="tax_invoice", doc_date=run_on, registration_id=r["registration_id"], party_id=r["party_id"],
                party_gst_id=t["party_gst_id"], place_of_supply=t["place_of_supply"], supply_type=t["supply_type"],
                payment_terms_days=t["payment_terms_days"], lines=lines))
            made.append(doc_id)
            if r["auto_issue"]:
                cur.execute("SAVEPOINT recurring_issue")
                try:
                    issue_core(cur, entity_id, doc_id)
                    issued += 1
                except ApiError as e:
                    if e.code != "insufficient_stock":
                        raise
                    cur.execute("ROLLBACK TO SAVEPOINT recurring_issue")        # not enough stock: the invoice stays a draft for a person to issue
                cur.execute("RELEASE SAVEPOINT recurring_issue")
            run_on = _add_months_date(run_on, MONTHS[r["frequency"]])
        cur.execute("UPDATE recurring_invoices SET next_run_on = %s, is_active = %s WHERE id = %s",
                    (run_on, not (r["ends_on"] and run_on > r["ends_on"]), r["id"]))
    if made:
        audit(cur, entity_id, "recurring.run", "recurring_invoices", None, actor_kind="system", after={"drafted": len(made), "issued": issued})
    return {"drafted": len(made), "issued": issued, "document_ids": [str(x) for x in made]}


# ================================================================== API: dunning sequences
class StepIn(BaseModel):
    offset_days: int = Field(ge=-60, le=365)
    action: Literal["message", "task", "escalate", "hold_orders"]
    channels: list[Literal["email", "whatsapp", "sms"]] = ["email"]
    template_key: str | None = None
    attach_statement: bool = False


class SequenceIn(BaseModel):
    name: str
    is_default: bool = False
    steps: list[StepIn] = Field(min_length=1)


def _sequence(cur, entity_id, sid) -> dict:
    cur.execute("SELECT id, name, is_default FROM dunning_sequences WHERE id = %s AND entity_id = %s", (sid, entity_id))
    s = cur.fetchone()
    if not s:
        raise ApiError(404, "not_found", "Not found")
    cur.execute("""SELECT offset_days, action, channels, template_key, attach_statement FROM dunning_steps WHERE sequence_id = %s
                    ORDER BY offset_days, action""", (sid,))
    s["steps"] = cur.fetchall()
    return js(s)


def _write_sequence(cur, entity_id, sid, body: SequenceIn):
    if body.is_default:
        cur.execute("UPDATE dunning_sequences SET is_default = false WHERE entity_id = %s AND id <> %s", (entity_id, sid))
    cur.execute("UPDATE dunning_sequences SET name = %s, is_default = %s WHERE id = %s", (body.name, body.is_default, sid))
    cur.execute("DELETE FROM dunning_steps WHERE sequence_id = %s", (sid,))
    for st in body.steps:
        cur.execute("""INSERT INTO dunning_steps (sequence_id, offset_days, action, channels, template_key, attach_statement)
                       VALUES (%s, %s, %s, %s, %s, %s)""", (sid, st.offset_days, st.action, st.channels, st.template_key, st.attach_statement))


@router.post(E + "/dunning-sequences", status_code=201, tags=["Sales & billing"])
def create_sequence(entity_id: uuid.UUID, body: SequenceIn, p: Principal = Auth):
    with tx(p.user_id) as cur:
        require(cur, entity_id, "sales:write")
        cur.execute("INSERT INTO dunning_sequences (entity_id, name) VALUES (%s, %s) RETURNING id", (entity_id, body.name))
        sid = cur.fetchone()["id"]
        _write_sequence(cur, entity_id, sid, body)
        audit(cur, entity_id, "dunning_sequence.created", "dunning_sequences", sid)
        return _sequence(cur, entity_id, sid)


@router.get(E + "/dunning-sequences", tags=["Sales & billing"])
def list_sequences(entity_id: uuid.UUID, p: Principal = Auth, limit: int = Query(default=50, ge=1, le=200), cursor: str | None = None):
    off = offset_of(cursor)
    with tx(p.user_id) as cur:
        require(cur, entity_id, "sales:read")
        cur.execute("SELECT id FROM dunning_sequences WHERE entity_id = %s ORDER BY name LIMIT %s OFFSET %s", (entity_id, limit + 1, off))
        return page([_sequence(cur, entity_id, r["id"]) for r in cur.fetchall()], limit, off)


@router.get(E + "/dunning-sequences/{seq_id}", tags=["Sales & billing"])
def get_sequence(entity_id: uuid.UUID, seq_id: uuid.UUID, p: Principal = Auth):
    with tx(p.user_id) as cur:
        require(cur, entity_id, "sales:read")
        return _sequence(cur, entity_id, seq_id)


@router.patch(E + "/dunning-sequences/{seq_id}", tags=["Sales & billing"])
def update_sequence(entity_id: uuid.UUID, seq_id: uuid.UUID, body: dict, p: Principal = Auth):
    with tx(p.user_id) as cur:
        require(cur, entity_id, "sales:write")
        cur_seq = _sequence(cur, entity_id, seq_id)
        merged = {**cur_seq, **{k: v for k, v in body.items() if k in ("name", "is_default", "steps")}}
        try:
            seq = SequenceIn(**{k: merged[k] for k in ("name", "is_default", "steps")})
        except Exception as e:
            raise ApiError(400, "validation_failed", "Invalid sequence", str(e))
        _write_sequence(cur, entity_id, seq_id, seq)
        audit(cur, entity_id, "dunning_sequence.updated", "dunning_sequences", seq_id)
        return _sequence(cur, entity_id, seq_id)


class PauseIn(BaseModel):
    until: dt.date
    reason: str | None = None


@router.post(E + "/parties/{party_id}/pause-dunning", status_code=204, tags=["Sales & billing"])
def pause_dunning(entity_id: uuid.UUID, party_id: uuid.UUID, body: PauseIn, p: Principal = Auth):
    with tx(p.user_id) as cur:
        require(cur, entity_id, "sales:write")
        cur.execute("SELECT 1 FROM parties WHERE id = %s AND entity_id = %s", (party_id, entity_id))
        if not cur.fetchone():
            raise ApiError(404, "not_found", "Not found")
        cur.execute("""INSERT INTO party_dunning (party_id, paused_until, pause_reason) VALUES (%s, %s, %s)
                       ON CONFLICT (party_id) DO UPDATE SET paused_until = EXCLUDED.paused_until, pause_reason = EXCLUDED.pause_reason""",
                    (party_id, body.until, body.reason))
        audit(cur, entity_id, "dunning.paused", "parties", party_id, after={"until": body.until, "reason": body.reason})


# ================================================================== API: recurring invoices
class RecurringIn(BaseModel):
    name: str
    party_id: uuid.UUID
    registration_id: uuid.UUID
    template_document_id: uuid.UUID
    frequency: Literal["monthly", "quarterly", "half_yearly", "annual"]
    bill_in_advance: bool = True
    next_run_on: dt.date
    ends_on: dt.date | None = None
    auto_issue: bool = False
    is_active: bool = True


RECURRING_COLS = "id, name, party_id, registration_id, template_document_id, frequency, bill_in_advance, next_run_on, ends_on, auto_issue, is_active"


@router.post(E + "/recurring-invoices", status_code=201, tags=["Sales & billing"])
def create_recurring(entity_id: uuid.UUID, body: RecurringIn, p: Principal = Auth):
    with tx(p.user_id) as cur:
        require(cur, entity_id, "sales:write")
        cur.execute("SELECT party_id, registration_id FROM sales_documents WHERE id = %s AND entity_id = %s", (body.template_document_id, entity_id))
        t = cur.fetchone()
        if not t or t["party_id"] != body.party_id or t["registration_id"] != body.registration_id:
            raise ApiError(400, "validation_failed", "template_document_id must be a document of this customer and GSTIN")
        if body.ends_on and body.ends_on < body.next_run_on:
            raise ApiError(400, "validation_failed", "ends_on is before next_run_on")
        cur.execute(f"""INSERT INTO recurring_invoices (entity_id, party_id, registration_id, name, template_document_id, frequency,
                              bill_in_advance, next_run_on, ends_on, auto_issue, is_active) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        RETURNING {RECURRING_COLS}""",
                    (entity_id, body.party_id, body.registration_id, body.name, body.template_document_id, body.frequency, body.bill_in_advance,
                     body.next_run_on, body.ends_on, body.auto_issue, body.is_active))
        r = cur.fetchone()
        audit(cur, entity_id, "recurring_invoice.created", "recurring_invoices", r["id"])
        return js(r)


@router.get(E + "/recurring-invoices", tags=["Sales & billing"])
def list_recurring(entity_id: uuid.UUID, p: Principal = Auth, limit: int = Query(default=50, ge=1, le=200), cursor: str | None = None):
    off = offset_of(cursor)
    with tx(p.user_id) as cur:
        require(cur, entity_id, "sales:read")
        cur.execute(f"SELECT {RECURRING_COLS} FROM recurring_invoices WHERE entity_id = %s ORDER BY next_run_on, id LIMIT %s OFFSET %s", (entity_id, limit + 1, off))
        return page(js(cur.fetchall()), limit, off)


@router.get(E + "/recurring-invoices/{rid}", tags=["Sales & billing"])
def get_recurring(entity_id: uuid.UUID, rid: uuid.UUID, p: Principal = Auth):
    with tx(p.user_id) as cur:
        require(cur, entity_id, "sales:read")
        cur.execute(f"SELECT {RECURRING_COLS} FROM recurring_invoices WHERE id = %s AND entity_id = %s", (rid, entity_id))
        r = cur.fetchone()
        if not r:
            raise ApiError(404, "not_found", "Not found")
        return js(r)


@router.patch(E + "/recurring-invoices/{rid}", tags=["Sales & billing"])
def update_recurring(entity_id: uuid.UUID, rid: uuid.UUID, body: dict, p: Principal = Auth):
    allowed = ("name", "frequency", "bill_in_advance", "next_run_on", "ends_on", "auto_issue", "is_active")
    with tx(p.user_id) as cur:
        require(cur, entity_id, "sales:write")
        cur.execute(f"SELECT {RECURRING_COLS} FROM recurring_invoices WHERE id = %s AND entity_id = %s FOR UPDATE", (rid, entity_id))
        r = cur.fetchone()
        if not r:
            raise ApiError(404, "not_found", "Not found")
        try:
            new = RecurringIn(**{**js(r), **{k: v for k, v in body.items() if k in allowed}})
        except Exception as e:
            raise ApiError(400, "validation_failed", "Invalid recurring invoice", str(e))
        cur.execute(f"""UPDATE recurring_invoices SET name = %s, frequency = %s, bill_in_advance = %s, next_run_on = %s, ends_on = %s,
                               auto_issue = %s, is_active = %s WHERE id = %s RETURNING {RECURRING_COLS}""",
                    (new.name, new.frequency, new.bill_in_advance, new.next_run_on, new.ends_on, new.auto_issue, new.is_active, rid))
        out = cur.fetchone()
        audit(cur, entity_id, "recurring_invoice.updated", "recurring_invoices", rid)
        return js(out)


@router.delete(E + "/recurring-invoices/{rid}", status_code=204, tags=["Sales & billing"])
def delete_recurring(entity_id: uuid.UUID, rid: uuid.UUID, p: Principal = Auth):
    with tx(p.user_id) as cur:
        require(cur, entity_id, "sales:write")
        cur.execute("DELETE FROM recurring_invoices WHERE id = %s AND entity_id = %s", (rid, entity_id))
        if cur.rowcount == 0:
            raise ApiError(404, "not_found", "Not found")
        audit(cur, entity_id, "recurring_invoice.deleted", "recurring_invoices", rid)
