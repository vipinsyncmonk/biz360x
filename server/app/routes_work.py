"""Work: who does what in a company (the responsibility matrix), a person's own queue across every company they can see, their
calendar, and tasks one person gives another.

The matrix is the owner's to set. The routing it drives is in database triggers (migration 38): a new obligation takes its owner and
approver from it, and a request for approval goes to the approver it names. The queue only reads; row-level security decides which
companies a person sees, and what is theirs within them is decided here."""
from __future__ import annotations

import datetime as dt
import uuid
from typing import Literal

from fastapi import APIRouter, Query
from pydantic import BaseModel, Field

from . import roles, routing
from .core import ApiError, Auth, Principal, allowed, audit, emit, require, require_owner, tx
from .routes_orders import js

router = APIRouter()
E = "/entities/{entity_id}"
OPEN_OBLIGATION = "('filed', 'not_applicable', 'waived')"


def _bad(field: str, message: str):
    return ApiError(400, "validation_failed", message, errors=[{"field": field, "message": message}])


# ================================================================== the matrix
class SlotIn(BaseModel):
    work_kind: str
    step: Literal["prepare", "approve"]
    user_id: uuid.UUID


class MatrixIn(BaseModel):
    data: list[SlotIn] = Field(max_length=40)


def _people(cur, entity_id) -> list[dict]:
    cur.execute("""SELECT m.user_id, u.full_name, m.title, m.scopes FROM memberships m JOIN users u ON u.id = m.user_id
                    WHERE m.entity_id = %s AND m.revoked_at IS NULL AND u.auth_provider <> 'service' ORDER BY u.full_name""", (entity_id,))
    return [{"user_id": str(r["user_id"]), "name": r["full_name"], "title": r["title"],
             "approves": [k for k in routing.KINDS if roles.allows(r["scopes"], routing.approve_module(k), "approve")]} for r in cur.fetchall()]


def _matrix(cur, entity_id) -> dict:
    cur.execute("""SELECT r.work_kind, r.step, r.user_id, u.full_name AS name FROM responsibilities r JOIN users u ON u.id = r.user_id
                    WHERE r.entity_id = %s ORDER BY r.work_kind, r.step""", (entity_id,))
    return {"kinds": routing.kinds_out(), "data": js(cur.fetchall()), "people": _people(cur, entity_id)}


@router.get(E + "/responsibilities", tags=["Entities"])
def get_responsibilities(entity_id: uuid.UUID, p: Principal = Auth):
    """Who prepares and who approves each kind of work, the kinds there are, and the people who can be named. Anyone in the company can
    read it: knowing who does what is not a setting."""
    with tx(p.user_id) as cur:
        require(cur, entity_id, "tasks:read")
        return _matrix(cur, entity_id)


def _slots(cur, entity_id) -> dict:
    cur.execute("SELECT work_kind, step, user_id FROM responsibilities WHERE entity_id = %s", (entity_id,))
    return {(r["work_kind"], r["step"]): r["user_id"] for r in cur.fetchall()}


def reassign_open(cur, entity_id, before: dict) -> int:
    """Bring open obligations in line with the matrix. An obligation nobody owns, or one still held by whoever the matrix named before
    (`before`), takes the person it names now. An owner a person chose by hand is left alone."""
    now, moved = _slots(cur, entity_id), 0
    for (kind, step) in set(before) | set(now):
        old, new = before.get((kind, step)), now.get((kind, step))
        if old == new:
            continue
        col = "owner_user_id" if step == "prepare" else "approver_user_id"
        cur.execute(f"""UPDATE obligations o SET {col} = %s FROM compliance_rules r
                         WHERE r.id = o.rule_id AND o.entity_id = %s AND o.status NOT IN {OPEN_OBLIGATION} AND app.work_kind_of_rule(r.code, r.authority_code) = %s
                           AND (o.{col} IS NULL OR o.{col} = %s) AND o.{col} IS DISTINCT FROM %s""", (new, entity_id, kind, old, new))
        moved += cur.rowcount
    return moved


def set_slots(cur, entity_id, slots: list[tuple[str, str, object]]) -> None:
    for kind, step, user_id in slots:
        cur.execute("""INSERT INTO responsibilities (entity_id, work_kind, step, user_id, set_by) VALUES (%s, %s, %s, %s, app.current_user_id())
                       ON CONFLICT (entity_id, work_kind, step) DO UPDATE SET user_id = EXCLUDED.user_id, set_by = EXCLUDED.set_by, created_at = now()""",
                    (entity_id, kind, step, user_id))


def fill_from_preset(cur, entity_id, user_id, preset: str | None, scopes: dict) -> int:
    """A person invited with a preset takes the work that preset usually does, where nobody holds it yet."""
    cur.execute("SELECT work_kind, step FROM responsibilities WHERE entity_id = %s", (entity_id,))
    taken = {(r["work_kind"], r["step"]) for r in cur.fetchall()}
    slots = [(k, s, user_id) for k, s in routing.preset_slots(preset, taken) if s == "prepare" or roles.allows(scopes, routing.approve_module(k), "approve")]
    before = _slots(cur, entity_id)
    set_slots(cur, entity_id, slots)
    if slots:
        reassign_open(cur, entity_id, before)
    return len(slots)


@router.put(E + "/responsibilities", tags=["Entities"])
def put_responsibilities(entity_id: uuid.UUID, body: MatrixIn, p: Principal = Auth):
    """Replace the matrix. Only an owner can. An approver must hold an approving level; open filings nobody chose an owner for follow the
    new matrix at once."""
    seen = set()
    for i, s in enumerate(body.data):
        if not routing.valid_slot(s.work_kind, s.step):
            raise _bad(f"data.{i}.work_kind", f"'{s.work_kind}' has no '{s.step}' step")
        if (s.work_kind, s.step) in seen:
            raise _bad(f"data.{i}.step", "Each step of a kind of work has one person")
        seen.add((s.work_kind, s.step))
    with tx(p.user_id) as cur:
        require_owner(cur, entity_id)
        people = {x["user_id"]: x for x in _people(cur, entity_id)}
        for i, s in enumerate(body.data):
            who = people.get(str(s.user_id))
            if not who:
                raise _bad(f"data.{i}.user_id", "That person is not a member of this company")
            if s.step == "approve" and s.work_kind not in who["approves"]:
                where = roles.MODULE_LABELS.get(routing.approve_module(s.work_kind), "any module")
                raise _bad(f"data.{i}.user_id", f"{who['name']} cannot approve this: give them an approving level on {where} first")
        before, held = _matrix(cur, entity_id)["data"], _slots(cur, entity_id)
        cur.execute("DELETE FROM responsibilities WHERE entity_id = %s", (entity_id,))
        set_slots(cur, entity_id, [(s.work_kind, s.step, s.user_id) for s in body.data])
        moved = reassign_open(cur, entity_id, held)
        out = _matrix(cur, entity_id)
        audit(cur, entity_id, "responsibilities.set", "entities", entity_id, before={"data": before}, after={"data": out["data"], "filings_reassigned": moved})
        emit(cur, entity_id, "responsibilities.changed", "entities", entity_id, {"slots": len(body.data)})
        return {**out, "filings_reassigned": moved}


# ================================================================== my work
WORK_SQL = f"""
WITH mine AS (SELECT m.entity_id, m.role::text AS role, m.scopes FROM memberships m WHERE m.user_id = app.current_user_id() AND m.revoked_at IS NULL)
SELECT x.kind, x.id, x.entity_id, e.legal_name AS entity_name, x.title, x.detail, x.due_on FROM (
  SELECT 'task' AS kind, t.id, t.entity_id, t.title, t.description AS detail, t.due_on
    FROM tasks t WHERE t.status = 'open' AND t.assignee_id = app.current_user_id()
  UNION ALL
  SELECT 'filing', o.id, o.entity_id, r.title || ' · ' || o.period_label, replace(o.status::text, '_', ' '), coalesce(o.extended_due_date, o.due_date)
    FROM obligations o JOIN compliance_rules r ON r.id = o.rule_id
   WHERE o.owner_user_id = app.current_user_id() AND o.status NOT IN {OPEN_OBLIGATION}
  UNION ALL
  SELECT 'approval', a.id, a.entity_id, a.summary, replace(a.subject::text, '_', ' '), a.due_on
    FROM approval_requests a LEFT JOIN mine ON mine.entity_id = a.entity_id
   WHERE a.status = 'pending' AND (a.assigned_to = app.current_user_id()
          OR (a.assigned_to IS NULL AND a.requested_by <> app.current_user_id() AND mine.role IN ('owner', 'approver')))
  UNION ALL
  SELECT 'leave', lr.id, emp.entity_id, emp.full_name || ': ' || lt.name, trim(to_char(lr.days, 'FM990.0')) || ' days from ' || to_char(lower(lr.dates), 'DD Mon'), lower(lr.dates)
    FROM leave_requests lr JOIN employees emp ON emp.id = lr.employee_id JOIN leave_types lt ON lt.id = lr.leave_type_id JOIN mine ON mine.entity_id = emp.entity_id
   WHERE lr.status = 'pending' AND (app.responsible(emp.entity_id, 'leave', 'approve') = app.current_user_id()
          OR (app.responsible(emp.entity_id, 'leave', 'approve') IS NULL AND coalesce(mine.scopes ->> 'hr', mine.scopes ->> '*') IN ('approve', 'file')))
  UNION ALL
  SELECT 'document_request', d.id, d.entity_id, d.title, 'Asked for by ' || d.requested_by_name, d.due_on
    FROM document_requests d JOIN mine ON mine.entity_id = d.entity_id
   WHERE d.status IN ('open', 'partially_fulfilled') AND coalesce(mine.scopes ->> 'documents', mine.scopes ->> '*') IN ('write', 'approve', 'file')
) x JOIN entities e ON e.id = x.entity_id
WHERE (%s::uuid IS NULL OR x.entity_id = %s)"""
HREF = {"task": "/", "filing": "/compliance", "approval": "/", "leave": "/payroll/leave", "document_request": "/documents"}


def _work(cur, entity_id, today: dt.date) -> list[dict]:
    cur.execute(WORK_SQL, (entity_id, entity_id))
    return routing.sort_work(cur.fetchall(), today)


def _item_out(i: dict) -> dict:
    return {"kind": i["kind"], "id": str(i["id"]), "entity_id": str(i["entity_id"]), "entity_name": i["entity_name"], "title": i["title"], "detail": i["detail"],
            "due_on": i["due_on"].isoformat() if i["due_on"] else None, "overdue": i["overdue"],
            "href": HREF[i["kind"]] + (f"?o={i['id']}" if i["kind"] == "filing" else "")}


@router.get("/me/work", tags=["Me"])
def my_work(p: Principal = Auth, entity_id: uuid.UUID | None = None, limit: int = Query(default=100, ge=1, le=200)):
    """Everything waiting on the caller, across every company they can see, in the order to work it: tasks given to them, filings they
    own, approvals for them to decide, leave for them to decide and document requests they can answer."""
    today = dt.date.today()
    with tx(p.user_id) as cur:
        items = _work(cur, entity_id, today)
    counts: dict = {}
    for i in items:
        counts[i["kind"]] = counts.get(i["kind"], 0) + 1
    return {"data": [_item_out(i) for i in items[:limit]], "total": len(items), "overdue": sum(1 for i in items if i["overdue"]), "counts": counts}


@router.get("/me/calendar", tags=["Me"])
def my_calendar(p: Principal = Auth, from_: dt.date | None = Query(default=None, alias="from"), to: dt.date | None = None, entity_id: uuid.UUID | None = None):
    """The caller's dated work and their companies' holidays, day by day. Five weeks from today when no range is given."""
    today = dt.date.today()
    start = from_ or today
    end = to or start + dt.timedelta(days=34)
    if end < start or (end - start).days > 366:
        raise _bad("to", "Choose a range of up to a year, ending on or after it starts")
    with tx(p.user_id) as cur:
        items = [i for i in _work(cur, entity_id, today) if i["due_on"] and start <= i["due_on"] <= end]
        cur.execute("""SELECT h.holiday_date, h.name, h.entity_id, e.legal_name AS entity_name FROM holidays h JOIN entities e ON e.id = h.entity_id
                         JOIN memberships m ON m.entity_id = h.entity_id AND m.user_id = app.current_user_id() AND m.revoked_at IS NULL
                        WHERE h.holiday_date BETWEEN %s AND %s AND (%s::uuid IS NULL OR h.entity_id = %s) ORDER BY h.holiday_date""", (start, end, entity_id, entity_id))
        holidays = cur.fetchall()
    days: dict = {}
    for i in items:
        days.setdefault(i["due_on"], []).append(_item_out(i))
    for h in holidays:
        days.setdefault(h["holiday_date"], []).append({"kind": "holiday", "id": None, "entity_id": str(h["entity_id"]), "entity_name": h["entity_name"], "title": h["name"],
                                                         "detail": "Company holiday", "due_on": h["holiday_date"].isoformat(), "overdue": False, "href": "/payroll/leave"})
    return {"from": start.isoformat(), "to": end.isoformat(), "days": [{"date": d.isoformat(), "items": days[d]} for d in sorted(days)]}


# ================================================================== tasks
class TaskIn(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=2000)
    assignee_id: uuid.UUID | None = None
    due_on: dt.date | None = None


class TaskPatch(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=2000)
    assignee_id: uuid.UUID | None = None
    due_on: dt.date | None = None
    status: Literal["open", "done", "cancelled"] | None = None


TASK_SQL = """SELECT t.id, t.title, t.description, t.assignee_id, a.full_name AS assignee_name, t.due_on, t.status, t.source, t.created_by, c.full_name AS created_by_name,
                     t.created_at, t.completed_at FROM tasks t LEFT JOIN users a ON a.id = t.assignee_id LEFT JOIN users c ON c.id = t.created_by"""


def _task(cur, entity_id, task_id, *, lock=False) -> dict:
    cur.execute(TASK_SQL + " WHERE t.id = %s AND t.entity_id = %s" + (" FOR UPDATE OF t" if lock else ""), (task_id, entity_id))
    t = cur.fetchone()
    if not t:
        raise ApiError(404, "not_found", "Not found")
    return t


def _check_assignee(cur, entity_id, user_id) -> None:
    if user_id is None:
        return
    cur.execute("SELECT 1 FROM memberships WHERE entity_id = %s AND user_id = %s AND revoked_at IS NULL", (entity_id, user_id))
    if not cur.fetchone():
        raise _bad("assignee_id", "That person is not a member of this company")


@router.get(E + "/tasks", tags=["Approvals"])
def list_tasks(entity_id: uuid.UUID, p: Principal = Auth, assignee: str | None = None, status: Literal["open", "done", "cancelled"] | None = "open",
               limit: int = Query(default=100, ge=1, le=200)):
    """Tasks of the company. `assignee=me` for the caller's own, `assignee=none` for the ones nobody has, or a person's id."""
    who = None
    if assignee and assignee not in ("me", "none"):
        try:
            who = uuid.UUID(assignee)
        except ValueError:
            raise _bad("assignee", "Use me, none or a person's id")
    with tx(p.user_id) as cur:
        require(cur, entity_id, "tasks:read")
        cur.execute(TASK_SQL + """ WHERE t.entity_id = %s AND (%s::text IS NULL OR t.status = %s)
                                   AND (%s::text IS NULL OR (%s = 'me' AND t.assignee_id = app.current_user_id()) OR (%s = 'none' AND t.assignee_id IS NULL) OR t.assignee_id = %s)
                                 ORDER BY t.due_on NULLS LAST, t.created_at LIMIT %s""", (entity_id, status, status, assignee, assignee, assignee, who, limit))
        return {"data": js(cur.fetchall()), "next_cursor": None}


@router.post(E + "/tasks", status_code=201, tags=["Approvals"])
def create_task(entity_id: uuid.UUID, body: TaskIn, p: Principal = Auth):
    """Give a task to a person, or leave it unassigned for someone to pick up."""
    with tx(p.user_id) as cur:
        require(cur, entity_id, "tasks:write")
        _check_assignee(cur, entity_id, body.assignee_id)
        cur.execute("""INSERT INTO tasks (entity_id, title, description, assignee_id, due_on, source, created_by) VALUES (%s, %s, %s, %s, %s, 'user', app.current_user_id())
                       RETURNING id""", (entity_id, body.title.strip(), body.description, body.assignee_id, body.due_on))
        tid = cur.fetchone()["id"]
        out = js(_task(cur, entity_id, tid))
        audit(cur, entity_id, "task.created", "tasks", tid, after={"title": out["title"], "assignee_id": out["assignee_id"]})
        if body.assignee_id:
            emit(cur, entity_id, "task.assigned", "tasks", tid, {"title": out["title"], "assignee_id": out["assignee_id"]})
        return out


@router.patch(E + "/tasks/{task_id}", tags=["Approvals"])
def update_task(entity_id: uuid.UUID, task_id: uuid.UUID, body: TaskPatch, p: Principal = Auth):
    """Change a task: finish it, hand it to someone else, move its date. The person it is assigned to can always finish it."""
    changes = body.model_dump(exclude_unset=True)
    if not changes:
        raise ApiError(400, "validation_failed", "Nothing to change")
    with tx(p.user_id) as cur:
        require(cur, entity_id, "tasks:read")
        t = _task(cur, entity_id, task_id, lock=True)
        cur.execute("SELECT app.current_user_id() AS me")
        me = cur.fetchone()["me"]
        own_status_only = t["assignee_id"] == me and set(changes) == {"status"}
        if not own_status_only and not allowed(cur, entity_id, "tasks:write"):
            raise ApiError(403, "permission_denied", "Missing permission tasks:write")
        if "title" in changes and not changes["title"]:
            raise _bad("title", "A task needs a title")
        if "assignee_id" in changes:
            _check_assignee(cur, entity_id, changes["assignee_id"])
        if "status" in changes:
            if changes["status"] is None:
                raise _bad("status", "Choose open, done or cancelled")
            changes["completed_at"] = dt.datetime.now(dt.timezone.utc) if changes["status"] == "done" else None
        sets = ", ".join(f"{k} = %s" for k in changes)
        cur.execute(f"UPDATE tasks SET {sets} WHERE id = %s", (*changes.values(), task_id))
        out = js(_task(cur, entity_id, task_id))
        audit(cur, entity_id, "task.updated", "tasks", task_id, before={"status": t["status"], "assignee_id": str(t["assignee_id"]) if t["assignee_id"] else None},
              after={"status": out["status"], "assignee_id": out["assignee_id"]})
        if changes.get("assignee_id") and changes["assignee_id"] != t["assignee_id"]:
            emit(cur, entity_id, "task.assigned", "tasks", task_id, {"title": out["title"], "assignee_id": out["assignee_id"]})
        return out
