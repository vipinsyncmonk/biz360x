"""People files (migration 44): an employee's address and emergency contact, the proofs they upload, tasks HR hands them, the manager who
approves their team's leave, and the emailed invitation to their own page.

The employee's side runs as `biz_self` (see routes_self.py); HR's side is the ordinary role under the hr permission. Files are kept in the
object store under their content hash and only ever sent back to the person they belong to, or to HR with `hr:read`.
"""
from __future__ import annotations

import base64
import binascii
import json
import re
import uuid
from typing import Literal

from fastapi import APIRouter, Response
from pydantic import BaseModel, Field

from . import mailer, storage
from .core import ApiError, Auth, Principal, audit, emit, require, tx
from .routes_hr import E, _bad, _employee_row, d1, queue_invitation
from .routes_orders import js
from .routes_self import ROLE, S, _me

router = APIRouter()
STATE = r"^[0-9]{2}$"
MOBILE = r"^\+[1-9][0-9]{7,14}$"


# ================================================================== address and emergency contact
class ContactIn(BaseModel):
    address_line1: str | None = Field(default=None, max_length=120)
    address_line2: str | None = Field(default=None, max_length=120)
    city: str | None = Field(default=None, max_length=60)
    state_code: str | None = Field(default=None, pattern=STATE, description="GST state code, like 29 for Karnataka")
    pincode: str | None = Field(default=None, pattern=r"^[1-9][0-9]{5}$")
    emergency_name: str | None = Field(default=None, max_length=80)
    emergency_relation: str | None = Field(default=None, max_length=40)
    emergency_mobile: str | None = Field(default=None, pattern=MOBILE, description="With the country code, like +919812345678")


ADDRESS = ("address_line1", "city", "state_code", "pincode")
EMERGENCY = ("emergency_name", "emergency_relation", "emergency_mobile")


def _clean(body: ContactIn) -> dict:
    d = {k: (v.strip() if isinstance(v, str) else v) or None for k, v in body.model_dump().items()}
    for group, what in ((ADDRESS, "address"), (EMERGENCY, "emergency contact")):
        have = [k for k in group if d[k]]
        if have and len(have) != len(group):
            missing = next(k for k in group if not d[k])
            raise _bad(missing, f"Give the whole {what}, or leave it empty")
    if d["address_line2"] and not d["address_line1"]:
        raise _bad("address_line1", "Give the whole address, or leave it empty")
    return d


def _contact_out(c: dict | None) -> dict:
    keys = ("address_line1", "address_line2", "city", "state_code", "pincode", "emergency_name", "emergency_relation", "emergency_mobile")
    out = {k: (c[k] if c else None) for k in keys}
    out["updated_at"] = c["updated_at"].isoformat() if c else None
    return out


@router.get(S + "/contact", tags=["Employee self-service"])
def my_contact(employee_id: uuid.UUID, p: Principal = Auth):
    """My address and my emergency contact. Empty until I give them."""
    with tx(p.user_id, role=ROLE) as cur:
        _me(cur, employee_id)
        cur.execute("SELECT * FROM self.contact(%s)", (employee_id,))
        return _contact_out(cur.fetchone())


@router.put(S + "/contact", tags=["Employee self-service"])
def save_my_contact(employee_id: uuid.UUID, body: ContactIn, p: Principal = Auth):
    """Save my address and emergency contact. Both are mine to keep (no pay depends on them), so they take effect at once; a part left empty is cleared."""
    d = _clean(body)
    with tx(p.user_id, role=ROLE) as cur:
        _me(cur, employee_id)
        cur.execute("SELECT self.save_contact(%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                    (employee_id, d["address_line1"], d["address_line2"], d["city"], d["state_code"], d["pincode"], d["emergency_name"], d["emergency_relation"], d["emergency_mobile"]))
        cur.execute("SELECT * FROM self.contact(%s)", (employee_id,))
        return _contact_out(cur.fetchone())


@router.get(E + "/employees/{employee_id}/contact", tags=["Payroll & HR"])
def employee_contact(entity_id: uuid.UUID, employee_id: uuid.UUID, p: Principal = Auth):
    """A person's address and emergency contact, as they gave it."""
    with tx(p.user_id) as cur:
        require(cur, entity_id, "hr:read")
        _employee_row(cur, entity_id, employee_id)
        cur.execute("SELECT * FROM employee_contacts WHERE employee_id = %s", (employee_id,))
        return _contact_out(cur.fetchone())


@router.put(E + "/employees/{employee_id}/contact", tags=["Payroll & HR"])
def correct_employee_contact(entity_id: uuid.UUID, employee_id: uuid.UUID, body: ContactIn, p: Principal = Auth):
    """HR corrects a person's address or emergency contact. The audit log records that it was changed, not the values."""
    d = _clean(body)
    with tx(p.user_id) as cur:
        require(cur, entity_id, "hr:write")
        e = _employee_row(cur, entity_id, employee_id, lock=True)
        cur.execute("""INSERT INTO employee_contacts (employee_id, entity_id, address_line1, address_line2, city, state_code, pincode, emergency_name, emergency_relation, emergency_mobile, updated_by)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s, app.current_user_id())
                       ON CONFLICT (employee_id) DO UPDATE SET address_line1 = EXCLUDED.address_line1, address_line2 = EXCLUDED.address_line2, city = EXCLUDED.city,
                         state_code = EXCLUDED.state_code, pincode = EXCLUDED.pincode, emergency_name = EXCLUDED.emergency_name, emergency_relation = EXCLUDED.emergency_relation,
                         emergency_mobile = EXCLUDED.emergency_mobile, updated_at = now(), updated_by = EXCLUDED.updated_by
                       RETURNING *""", (employee_id, entity_id, *(d[k] for k in ("address_line1", "address_line2", "city", "state_code", "pincode", "emergency_name", "emergency_relation", "emergency_mobile"))))
        out = _contact_out(cur.fetchone())
        audit(cur, entity_id, "employee_contact.corrected", "employees", employee_id, after={"address": bool(d["address_line1"]), "emergency": bool(d["emergency_name"]), "by": "hr"})
        emit(cur, entity_id, "employee_contact.corrected", "employees", employee_id, {"employee": e["full_name"]})
        return out


# ================================================================== proofs: files the employee uploads and HR accepts
KINDS = Literal["identity", "address", "education", "bank", "tax", "other"]
MAX_PROOF = 5 * 1024 * 1024
MAGIC = ((b"%PDF", "application/pdf"), (b"\xff\xd8\xff", "image/jpeg"), (b"\x89PNG\r\n\x1a\n", "image/png"))


class ProofIn(BaseModel):
    kind: KINDS
    label: str = Field(min_length=1, max_length=80, description="What it is, like 'Aadhaar card' or 'Rent receipts, April to June'")
    file_name: str = Field(min_length=1, max_length=200)
    content_base64: str = Field(max_length=int(MAX_PROOF * 4 / 3) + 16)


def _proof_out(r: dict) -> dict:
    return {"id": str(r["id"]), "kind": r["kind"], "label": r["label"], "file_name": r["file_name"], "mime_type": r["mime_type"], "size_bytes": r["size_bytes"], "status": r["status"],
            "uploaded_at": r["uploaded_at"].isoformat(), "decided_at": r["decided_at"].isoformat() if r["decided_at"] else None, "hr_note": r["hr_note"]}


def _file_response(key: str, name: str, mime: str, download: bool) -> Response:
    data = storage.get(key)
    if data is None:
        raise ApiError(404, "not_found", "Not found")
    safe = re.sub(r"[^A-Za-z0-9._-]", "_", name)[:120] or "proof"
    return Response(content=data, media_type=mime, headers={"Content-Disposition": f'{"attachment" if download else "inline"}; filename="{safe}"', "Cache-Control": "private, no-store",
                                                            "X-Content-Type-Options": "nosniff"})


@router.get(S + "/proofs", tags=["Employee self-service"])
def my_proofs(employee_id: uuid.UUID, p: Principal = Auth):
    """The files I have uploaded, newest first, and what HR decided about each."""
    with tx(p.user_id, role=ROLE) as cur:
        _me(cur, employee_id)
        cur.execute("SELECT * FROM self.proofs(%s)", (employee_id,))
        return {"data": [_proof_out(r) for r in cur.fetchall()]}


@router.post(S + "/proofs", status_code=201, tags=["Employee self-service"])
def upload_my_proof(employee_id: uuid.UUID, body: ProofIn, p: Principal = Auth):
    """Upload a PDF, JPEG or PNG of up to 5 MB. HR accepts or refuses it. The kind of file is read from the bytes, not from the name."""
    try:
        data = base64.b64decode(body.content_base64, validate=True)
    except (binascii.Error, ValueError):
        raise ApiError(400, "validation_failed", "The file was not sent correctly", errors=[{"field": "content_base64", "message": "not valid base64"}])
    if not data:
        raise _bad("content_base64", "The file is empty")
    if len(data) > MAX_PROOF:
        raise _bad("content_base64", "Files are limited to 5 MB")
    mime = next((m for sig, m in MAGIC if data.startswith(sig)), None)
    if not mime:
        raise ApiError(422, "unsupported_file", "Upload a PDF, JPEG or PNG", "Other file types cannot be kept as proof.")
    sha = storage.sha256_of(data)
    with tx(p.user_id, role=ROLE) as cur:
        e = _me(cur, employee_id)
        key = f"{e['entity_id']}/proofs/{sha}"
        storage.put(key, data)
        cur.execute("SELECT self.add_proof(%s,%s,%s,%s,%s,%s,%s,%s) AS id", (employee_id, body.kind, body.label.strip(), body.file_name.strip(), mime, len(data), sha, key))
        pid = cur.fetchone()["id"]
        cur.execute("SELECT * FROM self.proofs(%s)", (employee_id,))
        return _proof_out(next(r for r in cur.fetchall() if r["id"] == pid))


@router.delete(S + "/proofs/{proof_id}", tags=["Employee self-service"])
def remove_my_proof(employee_id: uuid.UUID, proof_id: uuid.UUID, p: Principal = Auth):
    """Take back a file nobody has decided on yet. Accepted and refused files stay."""
    with tx(p.user_id, role=ROLE) as cur:
        _me(cur, employee_id)
        cur.execute("SELECT self.remove_proof(%s, %s) AS removed", (employee_id, proof_id))
        if not cur.fetchone()["removed"]:
            raise ApiError(409, "conflict", "This file cannot be removed", "HR has already decided on it, or it is not yours.")
        cur.execute("SELECT * FROM self.proofs(%s)", (employee_id,))
        return {"data": [_proof_out(r) for r in cur.fetchall()]}


@router.get(S + "/proofs/{proof_id}/file", tags=["Employee self-service"])
def my_proof_file(employee_id: uuid.UUID, proof_id: uuid.UUID, download: bool = False, p: Principal = Auth):
    with tx(p.user_id, role=ROLE) as cur:
        _me(cur, employee_id)
        cur.execute("SELECT * FROM self.proof_file(%s, %s)", (employee_id, proof_id))
        f = cur.fetchone()
    if not f:
        raise ApiError(404, "not_found", "Not found")
    return _file_response(f["storage_key"], f["file_name"], f["mime_type"], download)


PROOF_SQL = "SELECT * FROM employee_proofs WHERE employee_id = %s AND entity_id = %s"


@router.get(E + "/employees/{employee_id}/proofs", tags=["Payroll & HR"])
def employee_proofs(entity_id: uuid.UUID, employee_id: uuid.UUID, p: Principal = Auth):
    """The files a person has uploaded, with what was decided about each."""
    with tx(p.user_id) as cur:
        require(cur, entity_id, "hr:read")
        _employee_row(cur, entity_id, employee_id)
        cur.execute(PROOF_SQL + " ORDER BY uploaded_at DESC", (employee_id, entity_id))
        return {"data": [_proof_out(r) for r in cur.fetchall()]}


@router.get(E + "/employees/{employee_id}/proofs/{proof_id}/file", tags=["Payroll & HR"])
def employee_proof_file(entity_id: uuid.UUID, employee_id: uuid.UUID, proof_id: uuid.UUID, download: bool = False, p: Principal = Auth):
    with tx(p.user_id) as cur:
        require(cur, entity_id, "hr:read")
        cur.execute(PROOF_SQL + " AND id = %s", (employee_id, entity_id, proof_id))
        f = cur.fetchone()
        if not f:
            raise ApiError(404, "not_found", "Not found")
        audit(cur, entity_id, "employee_proof.viewed", "employee_proofs", proof_id, after={"employee": str(employee_id)})
    return _file_response(f["storage_key"], f["file_name"], f["mime_type"], download)


class ProofDecision(BaseModel):
    note: str | None = Field(default=None, max_length=300)


def _decide_proof(entity_id, employee_id, proof_id, p: Principal, accept: bool, note: str | None):
    with tx(p.user_id) as cur:
        require(cur, entity_id, "hr:write")
        cur.execute(PROOF_SQL + " AND id = %s FOR UPDATE", (employee_id, entity_id, proof_id))
        r = cur.fetchone()
        if not r:
            raise ApiError(404, "not_found", "Not found")
        if r["status"] != "pending":
            raise ApiError(409, "conflict", "This file has already been decided", f"It is {r['status']}.")
        if not accept and not note:
            raise _bad("note", "Say why the file is not accepted; the person will see it")
        status = "accepted" if accept else "rejected"
        cur.execute("UPDATE employee_proofs SET status = %s, decided_by = app.current_user_id(), decided_at = now(), hr_note = %s WHERE id = %s RETURNING *", (status, note, proof_id))
        out = _proof_out(cur.fetchone())
        audit(cur, entity_id, f"employee_proof.{status}", "employee_proofs", proof_id, after={"kind": r["kind"], "label": r["label"], "note": note})
        emit(cur, entity_id, f"employee_proof.{status}", "employee_proofs", proof_id, {"label": r["label"]})
        return out


@router.post(E + "/employees/{employee_id}/proofs/{proof_id}/accept", tags=["Payroll & HR"])
def accept_proof(entity_id: uuid.UUID, employee_id: uuid.UUID, proof_id: uuid.UUID, body: ProofDecision | None = None, p: Principal = Auth):
    return _decide_proof(entity_id, employee_id, proof_id, p, True, body.note if body else None)


@router.post(E + "/employees/{employee_id}/proofs/{proof_id}/reject", tags=["Payroll & HR"])
def reject_proof(entity_id: uuid.UUID, employee_id: uuid.UUID, proof_id: uuid.UUID, body: ProofDecision, p: Principal = Auth):
    return _decide_proof(entity_id, employee_id, proof_id, p, False, body.note)


# ================================================================== tasks for a person
class TaskIn(BaseModel):
    employee_id: uuid.UUID
    title: str = Field(min_length=1, max_length=120)
    detail: str | None = Field(default=None, max_length=600)
    due_on: str | None = Field(default=None, pattern=r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")


class DoneIn(BaseModel):
    done: bool


def _task_out(t: dict) -> dict:
    out = {"id": str(t["id"]), "title": t["title"], "detail": t["detail"], "due_on": t["due_on"].isoformat() if t["due_on"] else None, "status": t["status"],
           "created_at": t["created_at"].isoformat(), "done_at": t["done_at"].isoformat() if t["done_at"] else None}
    if "employee_name" in t:
        out["employee_id"] = str(t["employee_id"])
        out["employee_name"] = t["employee_name"]
        out["employee_code"] = t["employee_code"]
    return out


@router.get(S + "/tasks", tags=["Employee self-service"])
def my_tasks(employee_id: uuid.UUID, p: Principal = Auth):
    """What HR has asked me to do: open first, by due date."""
    with tx(p.user_id, role=ROLE) as cur:
        _me(cur, employee_id)
        cur.execute("SELECT * FROM self.tasks(%s)", (employee_id,))
        rows = cur.fetchall()
        return {"data": [_task_out(t) for t in rows], "open": sum(1 for t in rows if t["status"] == "open")}


@router.put(S + "/tasks/{task_id}", tags=["Employee self-service"])
def set_my_task(employee_id: uuid.UUID, task_id: uuid.UUID, body: DoneIn, p: Principal = Auth):
    """Mark a task done, or open it again. Doing it twice changes nothing."""
    with tx(p.user_id, role=ROLE) as cur:
        _me(cur, employee_id)
        cur.execute("SELECT self.set_task_done(%s, %s, %s)", (employee_id, task_id, body.done))
        cur.execute("SELECT * FROM self.tasks(%s)", (employee_id,))
        rows = cur.fetchall()
        return {"data": [_task_out(t) for t in rows], "open": sum(1 for t in rows if t["status"] == "open")}


TASK_SQL = """SELECT t.*, e.full_name AS employee_name, e.employee_code FROM employee_tasks t JOIN employees e ON e.id = t.employee_id WHERE t.entity_id = %s"""


@router.get(E + "/employee-tasks", tags=["Payroll & HR"])
def list_employee_tasks(entity_id: uuid.UUID, p: Principal = Auth, status: Literal["open", "done", "cancelled"] | None = None, employee_id: uuid.UUID | None = None):
    """Tasks HR has handed to people, with who they are for and whether they are done."""
    with tx(p.user_id) as cur:
        require(cur, entity_id, "hr:read")
        cur.execute(TASK_SQL + " AND (%s::text IS NULL OR t.status = %s) AND (%s::uuid IS NULL OR t.employee_id = %s) ORDER BY (t.status <> 'open'), t.due_on NULLS LAST, t.created_at",
                    (entity_id, status, status, employee_id, employee_id))
        return {"data": [_task_out(t) for t in cur.fetchall()]}


@router.post(E + "/employee-tasks", status_code=201, tags=["Payroll & HR"])
def add_employee_task(entity_id: uuid.UUID, body: TaskIn, p: Principal = Auth):
    """Ask a person to do something (send a document, finish a form). They see it on their own page and mark it done."""
    with tx(p.user_id) as cur:
        require(cur, entity_id, "hr:write")
        e = _employee_row(cur, entity_id, body.employee_id)
        if e["status"] == "exited":
            raise ApiError(409, "conflict", "This person has left the company")
        cur.execute("""INSERT INTO employee_tasks (entity_id, employee_id, title, detail, due_on, created_by) VALUES (%s,%s,%s,%s,%s, app.current_user_id()) RETURNING id""",
                    (entity_id, body.employee_id, body.title.strip(), (body.detail or "").strip() or None, body.due_on))
        tid = cur.fetchone()["id"]
        audit(cur, entity_id, "employee_task.assigned", "employee_tasks", tid, after={"employee": str(body.employee_id), "title": body.title})
        emit(cur, entity_id, "employee_task.assigned", "employee_tasks", tid, {"employee": e["full_name"], "title": body.title})
        cur.execute(TASK_SQL + " AND t.id = %s", (entity_id, tid))
        return _task_out(cur.fetchone())


@router.post(E + "/employee-tasks/{task_id}/cancel", tags=["Payroll & HR"])
def cancel_employee_task(entity_id: uuid.UUID, task_id: uuid.UUID, p: Principal = Auth):
    """Withdraw a task that is still open."""
    with tx(p.user_id) as cur:
        require(cur, entity_id, "hr:write")
        cur.execute(TASK_SQL + " AND t.id = %s FOR UPDATE OF t", (entity_id, task_id))
        t = cur.fetchone()
        if not t:
            raise ApiError(404, "not_found", "Not found")
        if t["status"] != "open":
            raise ApiError(409, "conflict", "Only an open task can be withdrawn", f"It is {t['status']}.")
        cur.execute("UPDATE employee_tasks SET status = 'cancelled' WHERE id = %s", (task_id,))
        audit(cur, entity_id, "employee_task.cancelled", "employee_tasks", task_id, before={"status": "open"}, after={"status": "cancelled"})
        cur.execute(TASK_SQL + " AND t.id = %s", (entity_id, task_id))
        return _task_out(cur.fetchone())


# ================================================================== the manager, and the team's leave
class ManagerIn(BaseModel):
    manager_id: uuid.UUID | None = None


@router.put(E + "/employees/{employee_id}/manager", tags=["Payroll & HR"])
def set_manager(entity_id: uuid.UUID, employee_id: uuid.UUID, body: ManagerIn, p: Principal = Auth):
    """Name the person this one reports to, or none. The manager decides their team's leave from their own page (leave with no unpaid days;
    anything that touches pay stays with HR). A manager must be someone at the company, and the line of managers cannot loop."""
    with tx(p.user_id) as cur:
        require(cur, entity_id, "hr:write")
        e = _employee_row(cur, entity_id, employee_id, lock=True)
        if body.manager_id:
            m = _employee_row(cur, entity_id, body.manager_id)
            if m["status"] == "exited":
                raise ApiError(409, "conflict", "This person has left the company")
            seen, at = {employee_id}, body.manager_id
            while at:
                if at in seen:
                    raise ApiError(409, "manager_cycle", "That would make a loop of managers", "A person cannot report to someone who reports to them.")
                seen.add(at)
                cur.execute("SELECT manager_id FROM employees WHERE id = %s", (at,))
                at = cur.fetchone()["manager_id"]
        cur.execute("UPDATE employees SET manager_id = %s WHERE id = %s", (body.manager_id, employee_id))
        audit(cur, entity_id, "employee.manager_set", "employees", employee_id, before={"manager_id": str(e["manager_id"]) if e["manager_id"] else None},
              after={"manager_id": str(body.manager_id) if body.manager_id else None})
        emit(cur, entity_id, "employee.manager_set", "employees", employee_id, {"employee": e["full_name"]})
        cur.execute("SELECT m.id, m.full_name FROM employees m WHERE m.id = %s", (body.manager_id,))
        m = cur.fetchone()
        return {"employee_id": str(employee_id), "manager_id": str(m["id"]) if m else None, "manager_name": m["full_name"] if m else None}


def _team_leave_out(r: dict) -> dict:
    return {"id": str(r["id"]), "employee_id": str(r["employee_id"]), "employee_name": r["employee_name"], "leave_type": r["leave_type"], "leave_type_name": r["leave_type_name"],
            "from": r["d_from"].isoformat(), "to": r["d_to"].isoformat(), "days": d1(r["days"]), "lop_days": d1(r["lop_days"]), "half_day": r["half_day"], "reason": r["reason"],
            "status": r["status"], "requested_at": r["requested_at"].isoformat(), "decided_at": r["decided_at"].isoformat() if r["decided_at"] else None,
            "decision_note": r["decision_note"], "decided_by_me": bool(r["by_me"]), "mine_to_approve": r["status"] == "pending" and r["lop_days"] == 0}


def _team(cur, employee_id) -> dict:
    cur.execute("SELECT * FROM self.manager(%s)", (employee_id,))
    m = cur.fetchone()
    cur.execute("SELECT * FROM self.reports(%s)", (employee_id,))
    reports = cur.fetchall()
    leave = []
    if reports:
        cur.execute("SELECT * FROM self.team_leave(%s)", (employee_id,))
        leave = [_team_leave_out(r) for r in cur.fetchall()]
    return {"manager": js(m) if m else None, "reports": js(reports), "leave": leave, "pending": sum(1 for r in leave if r["status"] == "pending")}


@router.get(S + "/team", tags=["Employee self-service"])
def my_team(employee_id: uuid.UUID, p: Principal = Auth):
    """Who I report to, who reports to me, and their leave requests. Empty for someone with no team."""
    with tx(p.user_id, role=ROLE) as cur:
        _me(cur, employee_id)
        return _team(cur, employee_id)


class TeamDecision(BaseModel):
    note: str | None = Field(default=None, max_length=300)


def _team_decide(employee_id, request_id, p: Principal, approve: bool, note: str | None):
    with tx(p.user_id, role=ROLE) as cur:
        _me(cur, employee_id)
        cur.execute("SELECT self.decide_team_leave(%s, %s, %s, %s)", (employee_id, request_id, approve, note))
        return _team(cur, employee_id)


@router.post(S + "/team/leave/{request_id}/approve", tags=["Employee self-service"])
def approve_team_leave(employee_id: uuid.UUID, request_id: uuid.UUID, body: TeamDecision | None = None, p: Principal = Auth):
    """Approve a request from someone who reports to me. Leave with unpaid days is refused (`leave_unpaid_is_hr`): it changes pay, so HR decides it."""
    return _team_decide(employee_id, request_id, p, True, body.note if body else None)


@router.post(S + "/team/leave/{request_id}/reject", tags=["Employee self-service"])
def reject_team_leave(employee_id: uuid.UUID, request_id: uuid.UUID, body: TeamDecision | None = None, p: Principal = Auth):
    return _team_decide(employee_id, request_id, p, False, body.note if body else None)


# ================================================================== the emailed invitation
@router.post(E + "/employees/{employee_id}/self-service/invitation", status_code=202, tags=["Payroll & HR"])
def resend_invitation(entity_id: uuid.UUID, employee_id: uuid.UUID, p: Principal = Auth):
    """Send the invitation email again to a person who has not accepted yet. Once every ten minutes at most. The email is sent by the worker."""
    with tx(p.user_id) as cur:
        require(cur, entity_id, "hr:write")
        e = _employee_row(cur, entity_id, employee_id, lock=True)
        if e["user_id"] or not e["self_invited_at"] or e["status"] == "exited":
            raise ApiError(409, "conflict", "There is no invitation waiting for this person", "Open their sign-in first, or they have already accepted.")
        cur.execute("SELECT 1 FROM notifications WHERE event_type = 'employee_invitation' AND subject_id = %s AND created_at > now() - interval '10 minutes'", (employee_id,))
        if cur.fetchone():
            raise ApiError(409, "invitation_recent", "An invitation was sent a moment ago", "Wait ten minutes before sending it again.")
        cur.execute("SELECT app.invite_employee(%s) AS user_id", (employee_id,))
        queue_invitation(cur, entity_id, e, cur.fetchone()["user_id"])
        audit(cur, entity_id, "employee.invitation_resent", "employees", employee_id, after={"email": e["email"]})
        return {"employee_id": str(employee_id), "queued": True, "email": e["email"]}


def handle_mail_send(cur, job: dict) -> dict:
    """Worker: send a queued email notification through the mail provider. A provider error is retried by the worker."""
    nid = job["payload"]["notification_id"]
    cur.execute("SELECT * FROM notifications WHERE id = %s FOR UPDATE", (nid,))
    n = cur.fetchone()
    if not n or n["status"] != "queued":
        return {"skipped": True}
    pl = n["payload"]
    mailer.mailer().send(pl["to"], pl["subject"], pl["body"])
    cur.execute("UPDATE notifications SET status = 'sent', sent_at = now() WHERE id = %s", (nid,))
    return {"sent_to": pl["to"]}


def mail_failed(cur, job: dict, exc: Exception) -> None:
    cur.execute("UPDATE notifications SET status = 'failed' WHERE id = %s AND status = 'queued'", (job["payload"].get("notification_id"),))
