"""Employee self-service: a person's own payslips, leave, handbook and joining checklist.

An employee is not a member of the company. Requests under /self run as the database role `biz_self`, which can read no table at all:
it can only call the functions in the schema `self`, and each of those takes the employee record it is about and refuses unless that
record belongs to the signed-in person (migration 41). So nothing here can reach the books, another employee or another company, even
by mistake. The two /me routes (the invitation and accepting it) run as the ordinary role, because the person is not linked yet.

The leave arithmetic (working days, balances, what is unpaid) is hr.py and hrdb.balance_rows, the same as when HR records a request.
"""
from __future__ import annotations

import datetime as dt
import re
import uuid

from fastapi import APIRouter, Query, Response
from pydantic import BaseModel, Field

from . import hr, hrdb, secretbox
from .core import ApiError, Auth, Principal, tx
from .routes_hr import _balance_out, _slip_out, d1
from .routes_payroll import BankDetails
from .routes_orders import js

router = APIRouter()
S = "/self/{employee_id}"
ROLE = "biz_self"


# ================================================================== being invited, and accepting (as the ordinary role)
@router.get("/me/employments", tags=["Employee self-service"])
def my_employments(p: Principal = Auth):
    """The companies where the signed-in person is an employee with their own sign-in, and the invitations waiting for them."""
    with tx(p.user_id) as cur:
        cur.execute("SELECT employee_id, entity_id, legal_name, employee_code, full_name, designation, status FROM app.my_employments()")
        mine = cur.fetchall()
        cur.execute("SELECT employee_id, entity_id, legal_name, employee_code, full_name, designation, invited_at FROM app.my_employment_invites()")
        return {"employments": js(mine), "invitations": js(cur.fetchall())}


@router.post("/me/employments/{employee_id}/accept", tags=["Employee self-service"])
def accept_employment(employee_id: uuid.UUID, p: Principal = Auth):
    """Accept the invitation. It must be addressed to the email the person signed in with; anything else is a 404."""
    with tx(p.user_id) as cur:
        cur.execute("SELECT app.accept_employment(%s) AS entity_id", (employee_id,))
        return {"employee_id": str(employee_id), "entity_id": str(cur.fetchone()["entity_id"])}


# ================================================================== the door
def _me(cur, employee_id) -> dict:
    """The person's own record. Someone else's, or one that does not exist, is a 404 (the function refuses and tx maps it)."""
    cur.execute("SELECT * FROM self.profile(%s)", (employee_id,))
    e = cur.fetchone()
    if not e:
        raise ApiError(404, "not_found", "Not found")
    return e


def _leave_policy(cur, employee_id, as_of: dt.date) -> dict:
    out = dict(hr.default_policy()["leave"])
    cur.execute("SELECT self.leave_policy(%s, %s) AS leave", (employee_id, as_of))
    out.update(cur.fetchone()["leave"] or {})
    return out


def _types(cur, employee_id) -> list[dict]:
    cur.execute("SELECT * FROM self.leave_types(%s)", (employee_id,))
    return cur.fetchall()


def _balances(cur, e: dict, as_of: dt.date, leave_policy: dict, types: list[dict]) -> list[dict]:
    start = hr.leave_year_start(as_of, leave_policy["leave_year"])
    cur.execute("SELECT * FROM self.leave_adjustments(%s, %s)", (e["employee_id"], start))
    adj = cur.fetchall()
    cur.execute("SELECT * FROM self.leave_used(%s, %s, %s)", (e["employee_id"], start, hr.leave_year_end(start)))
    return hrdb.balance_rows(types, adj, cur.fetchall(), e["date_of_joining"], start, as_of)


def _request_out(r: dict) -> dict:
    return {"id": str(r["id"]), "leave_type": r["leave_type"], "leave_type_name": r["leave_type_name"], "from": r["d_from"].isoformat(), "to": r["d_to"].isoformat(),
            "days": d1(r["days"]), "lop_days": d1(r["lop_days"]), "half_day": r["half_day"], "reason": r["reason"], "status": r["status"],
            "requested_at": r["requested_at"].isoformat(), "decided_at": r["decided_at"].isoformat() if r["decided_at"] else None, "decision_note": r["decision_note"],
            "can_cancel": r["status"] == "pending"}


def _checklist(cur, e: dict) -> dict:
    cur.execute("SELECT * FROM self.onboarding_tasks(%s)", (e["employee_id"],))
    tasks = cur.fetchall()
    cur.execute("SELECT count(*) FILTER (WHERE requires_ack) AS docs, count(*) FILTER (WHERE requires_ack AND acknowledged_on IS NOT NULL) AS acked FROM self.policy_documents(%s)",
                (e["employee_id"],))
    h = cur.fetchone()
    state, blockers = hr.checklist_state(tasks, has_pan=bool(e["pan_last4"]), has_bank=e["bank_on_file"], has_salary=e["has_salary"],
                                         handbook_done=h["docs"] > 0 and h["docs"] == h["acked"])
    return {"checklist": state, "open_required": blockers, "done": sum(1 for t in state if t["done"]), "total": len(state),
            "to_acknowledge": h["docs"] - h["acked"]}


@router.get(S, tags=["Employee self-service"])
def my_record(employee_id: uuid.UUID, p: Principal = Auth):
    """Who I am at this company, and where my joining checklist stands. The PAN shows its last four characters and the bank account
    only whether one is on file."""
    with tx(p.user_id, role=ROLE) as cur:
        e = _me(cur, employee_id)
        return {"employee": js(e), "onboarding": _checklist(cur, e)}


# ------------------------------------------------------------------ leave
@router.get(S + "/leave", tags=["Employee self-service"])
def my_leave(employee_id: uuid.UUID, p: Principal = Auth, as_of: dt.date | None = None):
    """My balances for the leave year the date falls in, my requests, the leave types I can apply for and the holidays of that year."""
    as_of = as_of or dt.date.today()
    with tx(p.user_id, role=ROLE) as cur:
        e = _me(cur, employee_id)
        policy = _leave_policy(cur, employee_id, as_of)
        types = _types(cur, employee_id)
        start = hr.leave_year_start(as_of, policy["leave_year"])
        end = hr.leave_year_end(start)
        balances = _balances(cur, e, as_of, policy, types)
        cur.execute("SELECT * FROM self.leave_requests(%s)", (employee_id,))
        requests = cur.fetchall()
        cur.execute("SELECT holiday_date, name FROM self.holidays(%s, %s, %s)", (employee_id, start, end))
        return {"as_of": as_of.isoformat(), "leave_year_start": start.isoformat(), "leave_year_end": end.isoformat(), "week_off": policy["week_off"],
                "unpaid_when_exhausted": policy["lop_when_exhausted"], "can_apply": e["status"] != "exited" and bool(types),
                "balances": [_balance_out(b) for b in balances], "requests": [_request_out(r) for r in requests], "holidays": js(cur.fetchall())}


class MyLeaveIn(BaseModel):
    leave_type: str = Field(pattern=r"^[A-Za-z]{2,6}$")
    from_: dt.date = Field(alias="from")
    to: dt.date
    half_day: bool = False
    reason: str | None = Field(default=None, max_length=300)


def _bad(field: str, message: str):
    return ApiError(400, "validation_failed", "Validation failed", errors=[{"field": field, "message": message}])


@router.post(S + "/leave-requests", status_code=201, tags=["Employee self-service"])
def apply_for_leave(employee_id: uuid.UUID, body: MyLeaveIn, p: Principal = Auth):
    """Apply for leave for myself. The days are the working days in the range. A paid type is paid up to my balance; where the policy
    allows it the rest is unpaid, otherwise the request is refused (`leave_balance_short`). It waits for whoever approves leave."""
    if body.to < body.from_:
        raise _bad("to", "The last day cannot be before the first")
    if (body.to - body.from_).days > 180:
        raise _bad("to", "A single request covers at most six months")
    with tx(p.user_id, role=ROLE) as cur:
        e = _me(cur, employee_id)
        if e["status"] == "exited" or body.from_ < e["date_of_joining"] or (e["date_of_leaving"] and body.to > e["date_of_leaving"]):
            raise _bad("from", "The leave falls outside your time with the company")
        t = next((t for t in _types(cur, employee_id) if t["code"] == body.leave_type.upper()), None)
        if not t:
            raise _bad("leave_type", f"{body.leave_type.upper()} is not one of this company's leave types")
        policy = _leave_policy(cur, employee_id, body.from_)
        cur.execute("SELECT holiday_date FROM self.holidays(%s, %s, %s)", (employee_id, body.from_, body.to))
        try:
            days = hr.leave_days(body.from_, body.to, body.half_day, policy["week_off"], {r["holiday_date"] for r in cur.fetchall()})
        except hr.PolicyError as x:
            raise _bad("half_day", str(x))
        if days <= 0:
            raise _bad("from", "There are no working days in that range: it is all weekly off or holidays")
        year_end = hr.leave_year_end(hr.leave_year_start(body.from_, policy["leave_year"]))
        bal = _balances(cur, e, min(body.to, year_end), policy, [t])[0]
        try:
            _, lop = hr.split_paid_unpaid(days, bal["available"], paid_type=t["paid"], lop_when_exhausted=policy["lop_when_exhausted"])
        except hr.PolicyError as x:
            raise ApiError(409, "leave_balance_short", "Not enough leave left", f"{t['name']}: {x}. The policy does not allow unpaid leave when the balance runs out.")
        cur.execute("SELECT self.request_leave(%s, %s, %s, %s, %s, %s, %s, %s) AS id", (employee_id, t["id"], body.from_, body.to, days, lop, body.half_day, body.reason))
        rid = cur.fetchone()["id"]
        cur.execute("SELECT * FROM self.leave_requests(%s, %s)", (employee_id, rid))
        return _request_out(cur.fetchone())


@router.post(S + "/leave-requests/{request_id}/cancel", tags=["Employee self-service"])
def withdraw_my_leave(employee_id: uuid.UUID, request_id: uuid.UUID, p: Principal = Auth):
    """Withdraw my request while it is still waiting. Once approved, HR cancels it, because pay may already depend on it."""
    with tx(p.user_id, role=ROLE) as cur:
        _me(cur, employee_id)
        cur.execute("SELECT self.cancel_leave(%s, %s) AS was", (employee_id, request_id))
        was = cur.fetchone()["was"]
        if was != "pending":
            raise ApiError(409, "conflict", f"This request is already {was}", "Ask HR to cancel an approved leave." if was == "approved" else None)
        cur.execute("SELECT * FROM self.leave_requests(%s, %s)", (employee_id, request_id))
        return _request_out(cur.fetchone())


# ------------------------------------------------------------------ payslips
def _my_slip_out(employee_id, s: dict) -> dict:
    out = _slip_out(None, s)
    out["pdf_url"] = f"/v1/self/{employee_id}/payslips/{s['id']}/pdf"
    return out


def _my_slip(cur, employee_id, slip_id) -> dict:
    cur.execute("SELECT * FROM self.payslips(%s, %s)", (employee_id, slip_id))
    s = cur.fetchone()
    if not s:
        raise ApiError(404, "not_found", "Not found")
    out = _my_slip_out(employee_id, s)
    cur.execute("SELECT code, label, kind, amount FROM self.payslip_lines(%s, %s)", (employee_id, slip_id))
    out["lines"] = js(cur.fetchall())
    cur.execute("SELECT gross, tds, pf, net FROM self.payslip_year_to_date(%s, %s)", (employee_id, slip_id))
    out["year_to_date"] = js(cur.fetchone())
    return out


@router.get(S + "/payslips", tags=["Employee self-service"])
def my_payslips(employee_id: uuid.UUID, p: Principal = Auth, fy: str | None = Query(default=None, pattern=r"^[0-9]{4}-[0-9]{2}$")):
    """My payslips from approved payroll runs, newest first. A run that is not approved yet does not show."""
    with tx(p.user_id, role=ROLE) as cur:
        _me(cur, employee_id)
        cur.execute("SELECT * FROM self.payslips(%s, NULL, %s)", (employee_id, fy))
        return {"data": [_my_slip_out(employee_id, s) for s in cur.fetchall()]}


@router.get(S + "/payslips/{payslip_id}", tags=["Employee self-service"])
def my_payslip(employee_id: uuid.UUID, payslip_id: uuid.UUID, p: Principal = Auth):
    with tx(p.user_id, role=ROLE) as cur:
        _me(cur, employee_id)
        return _my_slip(cur, employee_id, payslip_id)


@router.get(S + "/payslips/{payslip_id}/pdf", tags=["Employee self-service"])
def my_payslip_pdf(employee_id: uuid.UUID, payslip_id: uuid.UUID, download: bool = False, p: Principal = Auth):
    from .payslip_pdf import render_payslip
    with tx(p.user_id, role=ROLE) as cur:
        e = _me(cur, employee_id)
        slip = _my_slip(cur, employee_id, payslip_id)
    pdf = render_payslip(slip, {"legal_name": e["legal_name"], "registered_address": e["registered_address"]})
    name = re.sub(r"[^A-Za-z0-9-]", "-", f"payslip-{slip['employee_code']}-{slip['period_month'][:7]}")
    return Response(content=pdf, media_type="application/pdf",
                    headers={"Content-Disposition": f'{"attachment" if download else "inline"}; filename="{name}.pdf"', "Cache-Control": "private, no-store"})


# ------------------------------------------------------------------ the handbook
def _doc_out(d: dict, *, full: bool) -> dict:
    out = {"id": str(d["id"]), "slug": d["slug"], "title": d["title"], "category": d["category"], "version": d["version"], "requires_ack": d["requires_ack"],
           "published_at": d["published_at"].isoformat(), "acknowledged_on": d["acknowledged_on"].isoformat() if d["acknowledged_on"] else None}
    if full:
        out["body"] = d["body"]
    return out


def _my_doc(cur, employee_id, document_id) -> dict:
    cur.execute("SELECT * FROM self.policy_documents(%s, %s)", (employee_id, document_id))
    d = cur.fetchone()
    if not d:
        raise ApiError(404, "not_found", "Not found")
    return _doc_out(d, full=True)


@router.get(S + "/policies", tags=["Employee self-service"])
def my_policies(employee_id: uuid.UUID, p: Principal = Auth):
    """The company's handbook as it stands, and which documents I have acknowledged."""
    with tx(p.user_id, role=ROLE) as cur:
        _me(cur, employee_id)
        cur.execute("SELECT * FROM self.policy_documents(%s)", (employee_id,))
        return {"data": [_doc_out(d, full=False) for d in cur.fetchall()]}


@router.get(S + "/policies/{document_id}", tags=["Employee self-service"])
def my_policy(employee_id: uuid.UUID, document_id: uuid.UUID, p: Principal = Auth):
    with tx(p.user_id, role=ROLE) as cur:
        _me(cur, employee_id)
        return _my_doc(cur, employee_id, document_id)


@router.post(S + "/policies/{document_id}/acknowledge", tags=["Employee self-service"])
def acknowledge_policy(employee_id: uuid.UUID, document_id: uuid.UUID, p: Principal = Auth):
    """Say that I have read and accept the current version. It is my own act, recorded with my name and the date. Twice changes nothing."""
    with tx(p.user_id, role=ROLE) as cur:
        _me(cur, employee_id)
        cur.execute("SELECT self.acknowledge(%s, %s)", (employee_id, document_id))
        return _my_doc(cur, employee_id, document_id)


# ------------------------------------------------------------------ my own checklist items
class TickIn(BaseModel):
    done: bool


@router.put(S + "/checklist/{key}", tags=["Employee self-service"])
def tick_my_item(employee_id: uuid.UUID, key: str, body: TickIn, p: Principal = Auth):
    """Tick or untick an item of my joining checklist that is mine to do. Items HR does, and items read from the record, are a 409."""
    with tx(p.user_id, role=ROLE) as cur:
        e = _me(cur, employee_id)
        cur.execute("SELECT * FROM self.onboarding_tasks(%s)", (employee_id,))
        t = next((x for x in cur.fetchall() if x["key"] == key), None)
        if not t:
            raise ApiError(404, "not_found", "Not found")
        if not t["by_employee"] or t["auto"]:
            raise ApiError(409, "conflict", "This item is not yours to tick", "HR ticks it, or it ticks itself when the record has what it needs.")
        cur.execute("SELECT self.tick_task(%s, %s, %s)", (employee_id, key, body.done))
        return _checklist(cur, e)


# ------------------------------------------------------------------ my own details: proposed by me, applied by HR
class DetailsIn(BaseModel):
    pan: str | None = Field(default=None, pattern=r"^[A-Za-z]{5}[0-9]{4}[A-Za-z]$")
    bank_account: BankDetails | None = None
    mobile: str | None = Field(default=None, pattern=r"^\+[1-9][0-9]{7,14}$", description="With the country code, like +919812345678")


def last4(v: str | None) -> str | None:
    return v[-4:] if v else None


def _details_out(cur, e: dict) -> dict:
    cur.execute("SELECT * FROM self.detail_request(%s)", (e["employee_id"],))
    r = cur.fetchone()
    mine = {"pan_last4": e["pan_last4"], "bank_on_file": e["bank_on_file"], "bank_ifsc": e["bank_ifsc"], "mobile": e["mobile_e164"]}
    if not r:
        return {"status": "none", "current": mine, "request": None}
    req = {"id": str(r["id"]), "pan_last4": last4(r["pan"]), "bank_last4": last4(secretbox.decrypt(r["bank_account_enc"])), "bank_ifsc": r["bank_ifsc"], "mobile": r["mobile_e164"],
           "requested_at": r["requested_at"].isoformat(), "decided_at": r["decided_at"].isoformat() if r["decided_at"] else None, "hr_note": r["hr_note"]}
    return {"status": r["status"], "current": mine, "request": req}


@router.get(S + "/details", tags=["Employee self-service"])
def my_details(employee_id: uuid.UUID, p: Principal = Auth):
    """What is on my record (PAN's last four, whether a bank account is on file, my mobile) and the latest change I asked for."""
    with tx(p.user_id, role=ROLE) as cur:
        return _details_out(cur, _me(cur, employee_id))


@router.put(S + "/details", tags=["Employee self-service"])
def request_my_details(employee_id: uuid.UUID, body: DetailsIn, p: Principal = Auth):
    """Ask for my PAN, salary bank account or mobile number to be changed. Nothing on my record changes until HR confirms; asking again
    replaces a request that is still waiting. The bank account is stored encrypted."""
    if not (body.pan or body.bank_account or body.mobile):
        raise _bad("pan", "Give at least one thing to change")
    with tx(p.user_id, role=ROLE) as cur:
        e = _me(cur, employee_id)
        cur.execute("SELECT self.request_detail_change(%s, %s, %s, %s, %s)",
                    (employee_id, body.pan.upper() if body.pan else None, secretbox.encrypt(body.bank_account.account_no) if body.bank_account else None,
                     body.bank_account.ifsc if body.bank_account else None, body.mobile))
        return _details_out(cur, e)


@router.delete(S + "/details", tags=["Employee self-service"])
def withdraw_my_details(employee_id: uuid.UUID, p: Principal = Auth):
    """Take back a change I asked for while it is still waiting."""
    with tx(p.user_id, role=ROLE) as cur:
        e = _me(cur, employee_id)
        cur.execute("SELECT self.withdraw_detail_request(%s)", (employee_id,))
        return _details_out(cur, e)
