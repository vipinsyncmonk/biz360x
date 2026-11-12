"""HR: the company's policy, onboarding a new hire from a CTC, leave and holidays, the handbook, payslips for a person, and what a
payroll run still needs before it can be computed.

The idea is that a person states the policy once and enters a new hire once (name, joining date, CTC). The salary structure, the
checklist and the leave balances follow from the policy, approved unpaid leave reaches the month's payroll by itself, and the
readiness check says what is still missing, so a month's run is: start, look at the readiness list, compute, send for approval.
Everything runs under the payroll permission; the policy, leave types, the handbook and leave decisions need payroll:approve."""
from __future__ import annotations

import datetime as dt
import json
import re
import uuid
from decimal import Decimal
from typing import Literal

from fastapi import APIRouter, Query, Response
from pydantic import BaseModel, Field

from . import hr, hrdb, payroll as pr, secretbox
from .core import ApiError, Auth, Principal, audit, emit, money, require, tx
from .routes_orders import js, offset_of, page
from .routes_payroll import BankDetails, _month_end, _run_for_update, load_employee, structure_out

router = APIRouter()
E = "/entities/{entity_id}"
ZERO = Decimal(0)
APPROVED_RUN = ("approved", "bank_file_sent", "paid", "statutory_done")


def _bad(field: str, message: str):
    return ApiError(400, "validation_failed", message, errors=[{"field": field, "message": message}])


def d1(v) -> str | None:
    """Leave days as text: 2, 0.5, 7.5."""
    return None if v is None else f"{Decimal(v).normalize():f}"


# ================================================================== policy
class SalaryPolicy(BaseModel):
    basic_pct_of_ctc: Decimal = Field(gt=0, le=100, max_digits=5, decimal_places=2)
    hra_pct_of_basic: Decimal = Field(ge=0, le=100, max_digits=5, decimal_places=2)
    employer_pf_in_ctc: bool = True
    pf_on_full_basic: bool = False
    employer_esi_in_ctc: bool = True
    gratuity_in_ctc: bool = False


class LeavePolicy(BaseModel):
    leave_year: Literal["financial", "calendar"] = "financial"
    week_off: list[int] = Field(default=[5, 6], max_length=6)
    lop_when_exhausted: bool = True


class PayrollPolicy(BaseModel):
    inputs_cutoff_day: int = Field(default=25, ge=1, le=31)
    pay_day: int = Field(default=30, ge=1, le=31)
    probation_months: int = Field(default=6, ge=0, le=24)
    notice_days: int = Field(default=30, ge=0, le=180)


class ChecklistItem(BaseModel):
    key: str = Field(pattern=r"^[a-z0-9_]{1,30}$")
    title: str = Field(min_length=1, max_length=120)
    required: bool = True
    auto: Literal["pan", "bank", "salary", "handbook"] | None = None


class OnboardingPolicy(BaseModel):
    checklist: list[ChecklistItem] = Field(min_length=1, max_length=30)


class PolicyIn(BaseModel):
    effective_from: dt.date
    salary: SalaryPolicy
    leave: LeavePolicy
    payroll: PayrollPolicy
    onboarding: OnboardingPolicy


def _type_out(t: dict) -> dict:
    return {"id": str(t["id"]), "code": t["code"], "name": t["name"], "annual_quota": d1(t["annual_quota"]), "accrual": t["accrual"], "paid": t["paid"],
            "carry_forward_max": d1(t["carry_forward_max"]), "encashable": t["encashable"], "is_active": t["is_active"]}


def _policy_out(cur, entity_id) -> dict:
    pol = hrdb.current_policy(cur, entity_id)
    cur.execute("SELECT effective_from FROM hr_policies WHERE entity_id = %s AND effective_from > current_date ORDER BY effective_from LIMIT 1", (entity_id,))
    nxt = cur.fetchone()
    return {"effective_from": pol["effective_from"].isoformat() if pol["effective_from"] else None, "is_default": pol["effective_from"] is None,
            "next_change_on": nxt["effective_from"].isoformat() if nxt else None,
            "salary": {**pol["salary"], "basic_pct_of_ctc": str(pol["salary"]["basic_pct_of_ctc"]), "hra_pct_of_basic": str(pol["salary"]["hra_pct_of_basic"])},
            "leave": pol["leave"], "payroll": pol["payroll"], "onboarding": pol["onboarding"],
            "leave_types": [_type_out(t) for t in hrdb.leave_types(cur, entity_id, active_only=False)]}


@router.get(E + "/hr/policy", tags=["Payroll & HR"])
def get_policy(entity_id: uuid.UUID, p: Principal = Auth):
    """The policy in force today. A company that has saved none sees the starter policy (`is_default`)."""
    with tx(p.user_id) as cur:
        require(cur, entity_id, "payroll:read")
        return _policy_out(cur, entity_id)


@router.put(E + "/hr/policy", tags=["Payroll & HR"])
def put_policy(entity_id: uuid.UUID, body: PolicyIn, p: Principal = Auth):
    """Save the policy from a date. A policy is never changed for the past: salaries already set and leave already counted keep the
    policy they were made under."""
    if body.effective_from < dt.date.today():
        raise _bad("effective_from", "A policy takes effect from today or later")
    if len(set(body.leave.week_off)) != len(body.leave.week_off) or any(d < 0 or d > 6 for d in body.leave.week_off):
        raise _bad("leave.week_off", "The weekly off is a list of weekdays, 0 for Monday to 6 for Sunday")
    keys = [c.key for c in body.onboarding.checklist]
    if len(set(keys)) != len(keys):
        raise _bad("onboarding.checklist", "Each checklist item needs its own key")
    with tx(p.user_id) as cur:
        require(cur, entity_id, "payroll:approve")
        before = _policy_out(cur, entity_id)
        d = body.model_dump(mode="json")
        cur.execute("""INSERT INTO hr_policies (entity_id, effective_from, salary, leave, payroll, onboarding, created_by)
                       VALUES (%s, %s, %s::jsonb, %s::jsonb, %s::jsonb, %s::jsonb, app.current_user_id())
                       ON CONFLICT (entity_id, effective_from) DO UPDATE SET salary = EXCLUDED.salary, leave = EXCLUDED.leave, payroll = EXCLUDED.payroll,
                                   onboarding = EXCLUDED.onboarding, created_by = EXCLUDED.created_by, created_at = now() RETURNING id""",
                    (entity_id, body.effective_from, *(json.dumps(d[k]) for k in ("salary", "leave", "payroll", "onboarding"))))
        pid = cur.fetchone()["id"]
        out = _policy_out(cur, entity_id)
        audit(cur, entity_id, "hr_policy.saved", "hr_policies", pid, before={k: before[k] for k in ("salary", "leave", "payroll")}, after=d)
        emit(cur, entity_id, "hr_policy.changed", "hr_policies", pid, {"effective_from": body.effective_from.isoformat()})
        return out


class LeaveTypeIn(BaseModel):
    code: str = Field(pattern=r"^[A-Za-z]{2,6}$")
    name: str = Field(min_length=1, max_length=60)
    annual_quota: Decimal | None = Field(default=None, ge=0, le=365, max_digits=5, decimal_places=1)
    accrual: Literal["yearly", "monthly"] = "yearly"
    paid: bool = True
    carry_forward_max: Decimal = Field(default=ZERO, ge=0, le=365, max_digits=5, decimal_places=1)
    encashable: bool = False
    is_active: bool = True


@router.get(E + "/leave-types", tags=["Payroll & HR"])
def list_leave_types(entity_id: uuid.UUID, p: Principal = Auth):
    with tx(p.user_id) as cur:
        require(cur, entity_id, "payroll:read")
        return {"data": [_type_out(t) for t in hrdb.leave_types(cur, entity_id, active_only=False)]}


@router.post(E + "/leave-types", tags=["Payroll & HR"])
def save_leave_type(entity_id: uuid.UUID, body: LeaveTypeIn, p: Principal = Auth):
    """Add a leave type, or change the one with the same code. A paid type needs a yearly quota; an unpaid one has none."""
    if body.paid and body.annual_quota is None:
        raise _bad("annual_quota", "A paid leave type needs the days a year")
    with tx(p.user_id) as cur:
        require(cur, entity_id, "payroll:approve")
        hrdb.ensure_leave_types(cur, entity_id)
        cur.execute("""INSERT INTO leave_types (entity_id, code, name, annual_quota, accrual, paid, carry_forward_max, encashable, is_active) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
                       ON CONFLICT (entity_id, code) DO UPDATE SET name = EXCLUDED.name, annual_quota = EXCLUDED.annual_quota, accrual = EXCLUDED.accrual, paid = EXCLUDED.paid,
                                   carry_forward_max = EXCLUDED.carry_forward_max, encashable = EXCLUDED.encashable, is_active = EXCLUDED.is_active
                       RETURNING id, code, name, annual_quota, accrual, paid, carry_forward_max, encashable, is_active""",
                    (entity_id, body.code.upper(), body.name.strip(), body.annual_quota if body.paid else None, body.accrual, body.paid, body.carry_forward_max,
                     body.encashable, body.is_active))
        out = _type_out(cur.fetchone())
        audit(cur, entity_id, "leave_type.saved", "leave_types", out["id"], after=out)
        return out


# ================================================================== CTC
def _breakup_out(comps: list[dict], ctc: Decimal) -> dict:
    s = hr.breakup_summary(comps)
    return {"ctc_annual": money(ctc), "components": [{**c, "monthly_amount": money(c["monthly_amount"]), "annual_amount": money(c["monthly_amount"] * 12)} for c in comps],
            "monthly_ctc": money(s["monthly_ctc"]), "monthly_gross": money(s["monthly_gross"]), "employer_contributions": money(s["employer_contributions"]),
            "employee_pf": money(s["employee_pf"]), "employee_esi": money(s["employee_esi"]), "take_home_before_tax": money(s["take_home_before_tax"]),
            "wage_pct": float(s["wage_pct"]) if s["wage_pct"] is not None else None}


@router.get(E + "/payroll/ctc-breakup", tags=["Payroll & HR"])
def ctc_breakup(entity_id: uuid.UUID, ctc_annual: Decimal = Query(gt=0, le=Decimal("999999999")), as_of: dt.date | None = None, p: Principal = Auth):
    """What a yearly CTC becomes under the company's policy: the monthly components, the gross, and a take-home before income tax.
    Saves nothing."""
    with tx(p.user_id) as cur:
        require(cur, entity_id, "payroll:read")
        pf_on, esi_on = hrdb.registrations(cur, entity_id)
        try:
            comps = hr.ctc_breakup(ctc_annual, hrdb.current_policy(cur, entity_id, as_of)["salary"], pf_registered=pf_on, esi_registered=esi_on)
        except hr.PolicyError as e:
            raise _bad("ctc_annual", str(e))
    return _breakup_out(comps, ctc_annual)


# ================================================================== onboarding
class OnboardIn(BaseModel):
    full_name: str = Field(min_length=1, max_length=120)
    employee_code: str | None = Field(default=None, min_length=1, max_length=20)
    date_of_joining: dt.date
    work_state: str = Field(pattern=r"^[0-9]{2}$")
    ctc_annual: Decimal = Field(gt=0, max_digits=14, decimal_places=2)
    designation: str | None = Field(default=None, max_length=80)
    department: str | None = Field(default=None, max_length=80)
    email: str | None = Field(default=None, max_length=160)
    pan: str | None = Field(default=None, pattern=r"^[A-Z]{5}[0-9]{4}[A-Z]$")
    uan: str | None = Field(default=None, pattern=r"^[0-9]{12}$")
    employment_type: Literal["permanent", "fixed_term", "intern", "apprentice", "contract"] = "permanent"
    tax_regime: Literal["new", "old"] = "new"
    bank_account: BankDetails | None = None


class TaskIn(BaseModel):
    done: bool


def _employee_row(cur, entity_id, employee_id, *, lock=False) -> dict:
    cur.execute("SELECT * FROM employees WHERE id = %s AND entity_id = %s" + (" FOR UPDATE" if lock else ""), (employee_id, entity_id))
    e = cur.fetchone()
    if not e:
        raise ApiError(404, "not_found", "Not found")
    return e


def _handbook_done(cur, entity_id, employee_id) -> bool:
    cur.execute("""SELECT count(*) AS docs, count(a.employee_id) AS acked FROM hr_policy_documents d
                     LEFT JOIN hr_policy_acks a ON a.document_id = d.id AND a.employee_id = %s
                    WHERE d.entity_id = %s AND d.is_current AND d.requires_ack""", (employee_id, entity_id))
    r = cur.fetchone()
    return r["docs"] > 0 and r["docs"] == r["acked"]


def _onboarding(cur, entity_id, e: dict) -> dict:
    cur.execute("SELECT key, title, required, auto, done_at FROM onboarding_tasks WHERE employee_id = %s ORDER BY sort, key", (e["id"],))
    tasks = cur.fetchall()
    cur.execute("SELECT 1 FROM salary_structures WHERE employee_id = %s LIMIT 1", (e["id"],))
    has_salary = bool(cur.fetchone())
    state, blockers = hr.checklist_state(tasks, has_pan=bool(e["pan"]), has_bank=e["bank_account_enc"] is not None, has_salary=has_salary,
                                         handbook_done=_handbook_done(cur, entity_id, e["id"]))
    return {"employee": load_employee(cur, entity_id, e["id"]), "checklist": state, "open_required": blockers,
            "ready": e["status"] == "onboarding" and not blockers, "done": sum(1 for t in state if t["done"]), "total": len(state)}


@router.post(E + "/employees/onboard", status_code=201, tags=["Payroll & HR"])
def onboard(entity_id: uuid.UUID, body: OnboardIn, p: Principal = Auth):
    """Enter a new hire once: the person, the joining date and the CTC. The salary structure is worked out from the policy, the
    checklist is copied from it, and the person stays `onboarding` (outside payroll) until they are activated."""
    with tx(p.user_id) as cur:
        require(cur, entity_id, "payroll:write")
        code = body.employee_code
        if not code:
            cur.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (f"employees:{entity_id}",))
            cur.execute("SELECT employee_code FROM employees WHERE entity_id = %s", (entity_id,))
            code = hr.next_employee_code([r["employee_code"] for r in cur.fetchall()])
        cur.execute("""INSERT INTO employees (entity_id, employee_code, full_name, pan, uan, email, date_of_joining, designation, department, work_state, employment_type,
                              tax_regime, bank_account_enc, bank_ifsc, status)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'onboarding') RETURNING id""",
                    (entity_id, code.strip(), body.full_name.strip(), body.pan, body.uan, body.email, body.date_of_joining, body.designation, body.department, body.work_state,
                     body.employment_type, body.tax_regime, secretbox.encrypt(body.bank_account.account_no) if body.bank_account else None,
                     body.bank_account.ifsc if body.bank_account else None))
        eid = cur.fetchone()["id"]
        hrdb.insert_structure(cur, entity_id, eid, body.date_of_joining, body.ctc_annual, None)
        for i, c in enumerate(hrdb.current_policy(cur, entity_id)["onboarding"]["checklist"]):
            cur.execute("INSERT INTO onboarding_tasks (entity_id, employee_id, key, title, required, auto, sort) VALUES (%s,%s,%s,%s,%s,%s,%s)",
                        (entity_id, eid, c["key"], c["title"], c.get("required", True), c.get("auto"), i))
        out = _onboarding(cur, entity_id, _employee_row(cur, entity_id, eid))
        audit(cur, entity_id, "employee.onboarding_started", "employees", eid, after={"code": code, "ctc": body.ctc_annual, "joining": body.date_of_joining})
        emit(cur, entity_id, "employee.onboarding_started", "employees", eid, {"code": code, "name": body.full_name.strip()})
        return out


@router.get(E + "/employees/{employee_id}/onboarding", tags=["Payroll & HR"])
def get_onboarding(entity_id: uuid.UUID, employee_id: uuid.UUID, p: Principal = Auth):
    with tx(p.user_id) as cur:
        require(cur, entity_id, "payroll:read")
        return _onboarding(cur, entity_id, _employee_row(cur, entity_id, employee_id))


@router.put(E + "/employees/{employee_id}/onboarding/{key}", tags=["Payroll & HR"])
def tick_task(entity_id: uuid.UUID, employee_id: uuid.UUID, key: str, body: TaskIn, p: Principal = Auth):
    """Tick or untick a checklist item. Items read from the record (PAN, bank account, salary, handbook) cannot be ticked by hand."""
    with tx(p.user_id) as cur:
        require(cur, entity_id, "payroll:write")
        e = _employee_row(cur, entity_id, employee_id, lock=True)
        cur.execute("SELECT id, auto FROM onboarding_tasks WHERE employee_id = %s AND key = %s", (employee_id, key))
        t = cur.fetchone()
        if not t:
            raise ApiError(404, "not_found", "Not found")
        if t["auto"]:
            raise ApiError(409, "conflict", "This item is read from the employee's record", "Add the PAN, the bank account or the acknowledgement and it ticks itself.")
        cur.execute("UPDATE onboarding_tasks SET done_at = CASE WHEN %s THEN now() END, done_by = CASE WHEN %s THEN app.current_user_id() END WHERE id = %s", (body.done, body.done, t["id"]))
        audit(cur, entity_id, "employee.onboarding_task", "employees", employee_id, after={"key": key, "done": body.done})
        return _onboarding(cur, entity_id, e)


@router.post(E + "/employees/{employee_id}/activate", tags=["Payroll & HR"])
def activate(entity_id: uuid.UUID, employee_id: uuid.UUID, p: Principal = Auth):
    """Finish onboarding: every required item must be done. The person becomes active and is paid from the joining date."""
    with tx(p.user_id) as cur:
        require(cur, entity_id, "payroll:write")
        e = _employee_row(cur, entity_id, employee_id, lock=True)
        if e["status"] != "onboarding":
            raise ApiError(409, "conflict", f"This employee is already {e['status']}")
        state = _onboarding(cur, entity_id, e)
        if state["open_required"]:
            raise ApiError(409, "onboarding_incomplete", "Onboarding is not finished", "Still to do: " + "; ".join(state["open_required"]),
                           errors=[{"field": "checklist", "message": t} for t in state["open_required"]])
        cur.execute("UPDATE employees SET status = 'active' WHERE id = %s", (employee_id,))
        out = _onboarding(cur, entity_id, _employee_row(cur, entity_id, employee_id))
        audit(cur, entity_id, "employee.activated", "employees", employee_id, after={"code": e["employee_code"]})
        emit(cur, entity_id, "employee.activated", "employees", employee_id, {"code": e["employee_code"], "name": e["full_name"]})
        return out


# ================================================================== holidays
class HolidayIn(BaseModel):
    date: dt.date
    name: str = Field(min_length=1, max_length=80)


@router.get(E + "/holidays", tags=["Payroll & HR"])
def list_holidays(entity_id: uuid.UUID, year: int | None = Query(default=None, ge=2000, le=2100), p: Principal = Auth):
    with tx(p.user_id) as cur:
        require(cur, entity_id, "payroll:read")
        cur.execute("""SELECT id, holiday_date AS date, name FROM holidays WHERE entity_id = %s AND (%s::int IS NULL OR extract(year FROM holiday_date) = %s)
                        ORDER BY holiday_date""", (entity_id, year, year))
        return {"data": js(cur.fetchall())}


@router.post(E + "/holidays", status_code=201, tags=["Payroll & HR"])
def add_holiday(entity_id: uuid.UUID, body: HolidayIn, p: Principal = Auth):
    with tx(p.user_id) as cur:
        require(cur, entity_id, "payroll:write")
        cur.execute("INSERT INTO holidays (entity_id, holiday_date, name) VALUES (%s, %s, %s) RETURNING id, holiday_date AS date, name", (entity_id, body.date, body.name.strip()))
        out = js(cur.fetchone())
        audit(cur, entity_id, "holiday.added", "holidays", out["id"], after=out)
        return out


@router.delete(E + "/holidays/{holiday_id}", status_code=204, tags=["Payroll & HR"])
def delete_holiday(entity_id: uuid.UUID, holiday_id: uuid.UUID, p: Principal = Auth):
    with tx(p.user_id) as cur:
        require(cur, entity_id, "payroll:write")
        cur.execute("DELETE FROM holidays WHERE id = %s AND entity_id = %s RETURNING holiday_date, name", (holiday_id, entity_id))
        h = cur.fetchone()
        if not h:
            raise ApiError(404, "not_found", "Not found")
        audit(cur, entity_id, "holiday.removed", "holidays", holiday_id, before=js(h))
    return Response(status_code=204)


# ================================================================== leave
class LeaveIn(BaseModel):
    employee_id: uuid.UUID
    leave_type: str = Field(pattern=r"^[A-Za-z]{2,6}$")
    from_: dt.date = Field(alias="from")
    to: dt.date
    half_day: bool = False
    reason: str | None = Field(default=None, max_length=300)


class DecisionIn(BaseModel):
    note: str | None = Field(default=None, max_length=300)


class AdjustmentIn(BaseModel):
    employee_id: uuid.UUID
    leave_type: str = Field(pattern=r"^[A-Za-z]{2,6}$")
    days: Decimal = Field(ge=-365, le=365, max_digits=5, decimal_places=1)
    kind: Literal["opening", "carry_forward", "correction"] = "opening"
    as_of: dt.date | None = None
    note: str | None = Field(default=None, max_length=300)


LEAVE_SQL = """SELECT r.id, r.employee_id, e.full_name AS employee_name, e.employee_code, t.code AS leave_type, t.name AS leave_type_name, lower(r.dates) AS d_from,
                      upper(r.dates) - 1 AS d_to, r.days, r.lop_days, r.half_day, r.reason, r.status, r.requested_at, r.decided_at, r.decision_note
                 FROM leave_requests r JOIN employees e ON e.id = r.employee_id JOIN leave_types t ON t.id = r.leave_type_id"""


def _leave_out(r: dict) -> dict:
    return {"id": str(r["id"]), "employee_id": str(r["employee_id"]), "employee_name": r["employee_name"], "employee_code": r["employee_code"], "leave_type": r["leave_type"],
            "leave_type_name": r["leave_type_name"], "from": r["d_from"].isoformat(), "to": r["d_to"].isoformat(), "days": d1(r["days"]), "lop_days": d1(r["lop_days"]),
            "half_day": r["half_day"], "reason": r["reason"], "status": r["status"], "requested_at": r["requested_at"].isoformat(),
            "decided_at": r["decided_at"].isoformat() if r["decided_at"] else None, "decision_note": r["decision_note"]}


def _load_leave(cur, entity_id, rid, *, lock=False) -> dict:
    cur.execute(LEAVE_SQL + " WHERE r.id = %s AND e.entity_id = %s" + (" FOR UPDATE OF r" if lock else ""), (rid, entity_id))
    r = cur.fetchone()
    if not r:
        raise ApiError(404, "not_found", "Not found")
    return r


def _balance_out(b: dict) -> dict:
    return {"leave_type": b["code"], "name": b["name"], "paid": b["paid"], "annual_quota": d1(b["annual_quota"]), "accrual": b["accrual"], "earned": d1(b["earned"]),
            "adjustments": d1(b["adjustments"]), "taken": d1(b["taken"]), "pending": d1(b["pending"]), "unpaid_taken": d1(b["unpaid_taken"]), "available": d1(b["available"])}


def _type_by_code(cur, entity_id, code: str) -> dict:
    t = next((t for t in hrdb.leave_types(cur, entity_id) if t["code"] == code.upper()), None)
    if not t:
        raise _bad("leave_type", f"{code.upper()} is not one of this company's leave types")
    return t


@router.get(E + "/leave-balances", tags=["Payroll & HR"])
def leave_balances(entity_id: uuid.UUID, employee_id: uuid.UUID | None = None, as_of: dt.date | None = None, p: Principal = Auth):
    """What each person has earned, taken and can still take, for the leave year the date falls in."""
    as_of = as_of or dt.date.today()
    with tx(p.user_id) as cur:
        require(cur, entity_id, "payroll:read")
        policy = hrdb.current_policy(cur, entity_id, as_of)
        types = hrdb.leave_types(cur, entity_id)
        cur.execute("""SELECT id, employee_code, full_name, date_of_joining, status FROM employees WHERE entity_id = %s AND status <> 'exited'
                          AND (%s::uuid IS NULL OR id = %s) ORDER BY employee_code""", (entity_id, employee_id, employee_id))
        emps = cur.fetchall()
        if employee_id and not emps:
            raise ApiError(404, "not_found", "Not found")
        start = hr.leave_year_start(as_of, policy["leave"]["leave_year"])
        return {"as_of": as_of.isoformat(), "leave_year_start": start.isoformat(), "leave_year_end": hr.leave_year_end(start).isoformat(),
                "data": [{"employee_id": str(e["id"]), "employee_code": e["employee_code"], "employee_name": e["full_name"],
                          "balances": [_balance_out(b) for b in hrdb.balances(cur, entity_id, e, as_of, policy, types)]} for e in emps]}


@router.get(E + "/leave-requests", tags=["Payroll & HR"])
def list_leave(entity_id: uuid.UUID, p: Principal = Auth, status: Literal["pending", "approved", "rejected", "cancelled"] | None = None, employee_id: uuid.UUID | None = None,
               from_: dt.date | None = Query(default=None, alias="from"), to: dt.date | None = None, limit: int = Query(default=50, ge=1, le=200), cursor: str | None = None):
    off = offset_of(cursor)
    with tx(p.user_id) as cur:
        require(cur, entity_id, "payroll:read")
        cur.execute(LEAVE_SQL + """ WHERE e.entity_id = %s AND (%s::text IS NULL OR r.status = %s) AND (%s::uuid IS NULL OR r.employee_id = %s)
                                     AND (%s::date IS NULL OR upper(r.dates) > %s) AND (%s::date IS NULL OR lower(r.dates) <= %s)
                                   ORDER BY (r.status = 'pending') DESC, lower(r.dates) DESC, r.id LIMIT %s OFFSET %s""",
                    (entity_id, status, status, employee_id, employee_id, from_, from_, to, to, limit + 1, off))
        return page([_leave_out(r) for r in cur.fetchall()], limit, off)


@router.post(E + "/leave-requests", status_code=201, tags=["Payroll & HR"])
def request_leave(entity_id: uuid.UUID, body: LeaveIn, p: Principal = Auth):
    """Record a leave request. The days are the working days in the range (not the weekly off, not a holiday). A paid type is paid up
    to the balance; when the policy allows it the rest is loss of pay, otherwise the request is refused (`leave_balance_short`)."""
    if body.to < body.from_:
        raise _bad("to", "The last day cannot be before the first")
    if (body.to - body.from_).days > 180:
        raise _bad("to", "A single request covers at most six months")
    with tx(p.user_id) as cur:
        require(cur, entity_id, "payroll:write")
        e = _employee_row(cur, entity_id, body.employee_id, lock=True)
        if e["status"] == "exited" or body.from_ < e["date_of_joining"] or (e["date_of_leaving"] and body.to > e["date_of_leaving"]):
            raise _bad("from", "The leave falls outside this person's time with the company")
        t = _type_by_code(cur, entity_id, body.leave_type)
        policy = hrdb.current_policy(cur, entity_id, body.from_)
        try:
            days = hr.leave_days(body.from_, body.to, body.half_day, policy["leave"]["week_off"], hrdb.holiday_set(cur, entity_id, body.from_, body.to))
        except hr.PolicyError as x:
            raise _bad("half_day", str(x))
        if days <= 0:
            raise _bad("from", "There are no working days in that range: it is all weekly off or holidays")
        year_end = hr.leave_year_end(hr.leave_year_start(body.from_, policy["leave"]["leave_year"]))
        bal = hrdb.balances(cur, entity_id, e, min(body.to, year_end), policy, [t])[0]            # what will have been earned by the last day, in the year the leave starts
        try:
            _, lop = hr.split_paid_unpaid(days, bal["available"], paid_type=t["paid"], lop_when_exhausted=policy["leave"]["lop_when_exhausted"])
        except hr.PolicyError as x:
            raise ApiError(409, "leave_balance_short", "Not enough leave left", f"{t['name']}: {x}. The policy does not allow unpaid leave when the balance runs out.")
        cur.execute("""INSERT INTO leave_requests (employee_id, leave_type_id, dates, days, lop_days, half_day, reason, requested_by)
                       VALUES (%s, %s, daterange(%s, %s, '[]'), %s, %s, %s, %s, app.current_user_id()) RETURNING id""",
                    (body.employee_id, t["id"], body.from_, body.to, days, lop, body.half_day, body.reason))
        rid = cur.fetchone()["id"]
        out = _leave_out(_load_leave(cur, entity_id, rid))
        audit(cur, entity_id, "leave.requested", "leave_requests", rid, after=out)
        emit(cur, entity_id, "leave.requested", "leave_requests", rid, {"employee": e["full_name"], "days": out["days"]})
        return out


def _decide(entity_id, request_id, p: Principal, to_status: str, note: str | None):
    with tx(p.user_id) as cur:
        require(cur, entity_id, "payroll:approve" if to_status in ("approved", "rejected") else "payroll:write")
        r = _load_leave(cur, entity_id, request_id, lock=True)
        allowed = ("pending",) if to_status in ("approved", "rejected") else ("pending", "approved")
        if r["status"] not in allowed:
            raise ApiError(409, "conflict", f"This request is already {r['status']}")
        if to_status == "cancelled" and r["status"] == "approved" and r["lop_days"] > 0:
            cur.execute("""SELECT 1 FROM payroll_runs WHERE entity_id = %s AND period_month BETWEEN %s AND %s AND status = ANY(%s)""",
                        (entity_id, r["d_from"].replace(day=1), r["d_to"].replace(day=1), list(APPROVED_RUN)))
            if cur.fetchone():
                raise ApiError(409, "conflict", "Payroll for that month is already approved", "The unpaid days were deducted. Correct the pay in the next run instead of cancelling the leave.")
        was_approved = r["status"] == "approved"
        cur.execute("""UPDATE leave_requests SET status = %s, decided_at = now(), decision_note = %s, approved_by = CASE WHEN %s = 'approved' THEN app.current_user_id() ELSE approved_by END
                        WHERE id = %s""", (to_status, note, to_status, request_id))
        out = _leave_out(_load_leave(cur, entity_id, request_id))
        if (to_status == "approved" or was_approved) and r["lop_days"] > 0:
            out["payroll_note"] = hrdb.refresh_open_runs(cur, entity_id, r["d_from"], r["d_to"])
        audit(cur, entity_id, f"leave.{to_status}", "leave_requests", request_id, before={"status": r["status"]}, after={"status": to_status, "note": note})
        emit(cur, entity_id, "leave.decided", "leave_requests", request_id, {"employee": r["employee_name"], "status": to_status})
        return out


@router.post(E + "/leave-requests/{request_id}/approve", tags=["Payroll & HR"])
def approve_leave(entity_id: uuid.UUID, request_id: uuid.UUID, body: DecisionIn | None = None, p: Principal = Auth):
    """Approve a request. Its unpaid days go into the open payroll run of each month it touches."""
    return _decide(entity_id, request_id, p, "approved", body.note if body else None)


@router.post(E + "/leave-requests/{request_id}/reject", tags=["Payroll & HR"])
def reject_leave(entity_id: uuid.UUID, request_id: uuid.UUID, body: DecisionIn | None = None, p: Principal = Auth):
    return _decide(entity_id, request_id, p, "rejected", body.note if body else None)


@router.post(E + "/leave-requests/{request_id}/cancel", tags=["Payroll & HR"])
def cancel_leave(entity_id: uuid.UUID, request_id: uuid.UUID, body: DecisionIn | None = None, p: Principal = Auth):
    """Withdraw a pending or approved request. Refused when its unpaid days were already deducted in an approved payroll."""
    return _decide(entity_id, request_id, p, "cancelled", body.note if body else None)


@router.post(E + "/leave-adjustments", status_code=201, tags=["Payroll & HR"])
def adjust_leave(entity_id: uuid.UUID, body: AdjustmentIn, p: Principal = Auth):
    """Add days to a person's balance or take them off: what they carried in when the company started here, a carry-forward, a correction."""
    if body.days == 0:
        raise _bad("days", "Give the days to add, or a negative number to take off")
    with tx(p.user_id) as cur:
        require(cur, entity_id, "payroll:approve")
        e = _employee_row(cur, entity_id, body.employee_id)
        t = _type_by_code(cur, entity_id, body.leave_type)
        if not t["paid"]:
            raise _bad("leave_type", "Unpaid leave has no balance to adjust")
        as_of = body.as_of or dt.date.today()
        policy = hrdb.current_policy(cur, entity_id, as_of)
        start = hr.leave_year_start(as_of, policy["leave"]["leave_year"])
        cur.execute("""INSERT INTO leave_adjustments (entity_id, employee_id, leave_type_id, leave_year_start, days, kind, note, created_by)
                       VALUES (%s,%s,%s,%s,%s,%s,%s, app.current_user_id()) RETURNING id""", (entity_id, body.employee_id, t["id"], start, body.days, body.kind, body.note))
        aid = cur.fetchone()["id"]
        bal = next(b for b in hrdb.balances(cur, entity_id, e, as_of, policy, [t]))
        audit(cur, entity_id, "leave.adjusted", "employees", body.employee_id, after={"type": t["code"], "days": d1(body.days), "kind": body.kind})
        return {"id": str(aid), "employee_id": str(body.employee_id), "leave_year_start": start.isoformat(), "balance": _balance_out(bal)}


# ================================================================== payroll run: readiness
@router.post(E + "/payroll-runs/{run_id}/inputs/from-leave", tags=["Payroll & HR"])
def inputs_from_leave(entity_id: uuid.UUID, run_id: uuid.UUID, p: Principal = Auth):
    """Bring the month's approved unpaid leave into the run's inputs again. Figures typed for the month are kept."""
    with tx(p.user_id) as cur:
        require(cur, entity_id, "payroll:write")
        run = _run_for_update(cur, entity_id, run_id, ("inputs_open", "computed"))
        rows = hrdb.pull_leave_lop(cur, entity_id, run)
        audit(cur, entity_id, "payroll_run.lop_from_leave", "payroll_runs", run_id, after={"employees": len(rows)})
        return {"data": [{"employee_id": r["employee_id"], "lop_days": d1(r["days"])} for r in rows]}


@router.get(E + "/payroll-runs/{run_id}/readiness", tags=["Payroll & HR"])
def readiness(entity_id: uuid.UUID, run_id: uuid.UUID, p: Principal = Auth):
    """What a run still needs. `blockers` stop it from being computed; `warnings` are worth a look before it is sent for approval."""
    with tx(p.user_id) as cur:
        require(cur, entity_id, "payroll:read")
        cur.execute("SELECT id, period_month, status FROM payroll_runs WHERE id = %s AND entity_id = %s", (run_id, entity_id))
        run = cur.fetchone()
        if not run:
            raise ApiError(404, "not_found", "Not found")
        month, end = run["period_month"], _month_end(run["period_month"])
        cur.execute("""SELECT e.id, e.employee_code, e.full_name, e.status, e.pan, e.date_of_joining, e.date_of_leaving, e.bank_account_enc IS NOT NULL AS has_bank,
                              EXISTS (SELECT 1 FROM salary_structures s WHERE s.employee_id = e.id AND s.effective @> least(%s::date, coalesce(e.date_of_leaving, %s::date))) AS has_salary
                         FROM employees e WHERE e.entity_id = %s AND e.date_of_joining <= %s AND (e.date_of_leaving IS NULL OR e.date_of_leaving >= %s)
                        ORDER BY e.employee_code""", (end, end, entity_id, end, month))
        emps = cur.fetchall()
        paid = [e for e in emps if e["status"] != "onboarding"]
        name = lambda e: {"employee_id": str(e["id"]), "employee_code": e["employee_code"], "name": e["full_name"]}
        cur.execute(LEAVE_SQL + " WHERE e.entity_id = %s AND r.status = 'pending' AND r.dates && daterange(%s, %s, '[]') ORDER BY lower(r.dates)", (entity_id, month, end))
        pending = cur.fetchall()
        lop = hrdb.leave_lop_for_month(cur, entity_id, month)
        cur.execute("SELECT inputs FROM payroll_run_inputs WHERE run_id = %s", (run_id,))
        stored = (cur.fetchone() or {}).get("inputs") or {}
        by_id = {e["id"]: e for e in emps}
        stale = [e for e in paid if Decimal(stored.get(str(e["id"]), {}).get("lop_from_leave", "0")) != lop.get(e["id"], ZERO)]
        blockers, warnings = [], []

        def add(to, code, title, people):
            if people:
                to.append({"code": code, "title": title, "employees": [name(e) for e in people]})
        add(blockers, "no_salary", "No salary is set for this month", [e for e in paid if not e["has_salary"]])
        add(warnings, "onboarding_open", "Joined, but onboarding is not finished: they are left out of this run", [e for e in emps if e["status"] == "onboarding"])
        add(warnings, "no_bank_account", "No bank account on file: the bank file cannot be sent", [e for e in paid if not e["has_bank"]])
        add(warnings, "no_pan", "No PAN on file", [e for e in paid if not e["pan"]])
        add(warnings, "leave_pending", "Leave in this month is waiting for a decision", [by_id[r["employee_id"]] for r in pending if r["employee_id"] in by_id])
        add(warnings, "lop_out_of_date", "Unpaid leave has changed since the inputs were filled", stale)
        return {"run_id": str(run_id), "period_month": month.isoformat(), "status": run["status"], "ready": not blockers and bool(paid), "headcount": len(paid),
                "blockers": blockers, "warnings": warnings,
                "joiners": [name(e) for e in paid if e["date_of_joining"] >= month], "leavers": [name(e) for e in paid if e["date_of_leaving"] and e["date_of_leaving"] <= end],
                "lop_from_leave": [{**name(by_id[k]), "lop_days": d1(v)} for k, v in lop.items() if k in by_id]}


# ================================================================== payslips for a person
SLIP_SQL = """SELECT s.id, s.run_id, r.period_month, r.status AS run_status, r.paid_on, s.employee_id, e.employee_code, e.full_name AS employee_name, e.designation, e.department,
                     e.pan, e.uan, e.date_of_joining, s.paid_days, s.lop_days, s.gross, s.wages, s.pf_wage, s.pf_employee, s.pf_employer_epf, s.pf_employer_eps, s.esi_applicable,
                     s.esi_employee, s.esi_employer, s.professional_tax, s.tds, s.other_deductions, s.net_pay
                FROM payslips s JOIN payroll_runs r ON r.id = s.run_id JOIN employees e ON e.id = s.employee_id"""


def _slip_out(entity_id, s: dict) -> dict:
    out = js({k: v for k, v in s.items() if k not in ("paid_days", "lop_days", "pan")})
    out.update({"paid_days": float(s["paid_days"]), "lop_days": float(s["lop_days"]), "final": s["run_status"] in APPROVED_RUN,
                "pan_last4": s["pan"][-4:] if s["pan"] else None, "pdf_url": f"/v1/entities/{entity_id}/payslips/{s['id']}/pdf"})
    return out


def _load_slip(cur, entity_id, slip_id) -> dict:
    cur.execute(SLIP_SQL + " WHERE s.id = %s AND r.entity_id = %s", (slip_id, entity_id))
    s = cur.fetchone()
    if not s:
        raise ApiError(404, "not_found", "Not found")
    cur.execute("SELECT code, label, kind, amount FROM payslip_lines WHERE payslip_id = %s ORDER BY id", (slip_id,))
    lines = cur.fetchall()
    cur.execute("""SELECT coalesce(sum(s.gross), 0) AS gross, coalesce(sum(s.tds), 0) AS tds, coalesce(sum(s.pf_employee), 0) AS pf, coalesce(sum(s.net_pay), 0) AS net
                     FROM payslips s JOIN payroll_runs r ON r.id = s.run_id
                    WHERE s.employee_id = %s AND r.entity_id = %s AND app.fy_of(r.period_month) = app.fy_of(%s::date) AND r.period_month <= %s AND r.status = ANY(%s)""",
                (s["employee_id"], entity_id, s["period_month"], s["period_month"], list(APPROVED_RUN)))
    out = _slip_out(entity_id, s)
    out["lines"] = js(lines)
    out["year_to_date"] = js(cur.fetchone())
    return out


@router.get(E + "/employees/{employee_id}/payslips", tags=["Payroll & HR"])
def employee_payslips(entity_id: uuid.UUID, employee_id: uuid.UUID, p: Principal = Auth, fy: str | None = Query(default=None, pattern=r"^[0-9]{4}-[0-9]{2}$")):
    """A person's payslips from approved payroll runs, newest first."""
    with tx(p.user_id) as cur:
        require(cur, entity_id, "payroll:read")
        _employee_row(cur, entity_id, employee_id)
        cur.execute(SLIP_SQL + """ WHERE s.employee_id = %s AND r.entity_id = %s AND r.status = ANY(%s) AND (%s::text IS NULL OR app.fy_of(r.period_month) = %s)
                                   ORDER BY r.period_month DESC""", (employee_id, entity_id, list(APPROVED_RUN), fy, fy))
        return {"data": [_slip_out(entity_id, s) for s in cur.fetchall()]}


@router.get(E + "/payslips/{payslip_id}", tags=["Payroll & HR"])
def get_payslip(entity_id: uuid.UUID, payslip_id: uuid.UUID, p: Principal = Auth):
    """One payslip with its lines and the year's totals so far."""
    with tx(p.user_id) as cur:
        require(cur, entity_id, "payroll:read")
        return _load_slip(cur, entity_id, payslip_id)


@router.get(E + "/payslips/{payslip_id}/pdf", tags=["Payroll & HR"])
def payslip_pdf(entity_id: uuid.UUID, payslip_id: uuid.UUID, download: bool = False, p: Principal = Auth):
    """The payslip as a PDF to hand to the employee. A payslip from a run that is not approved yet carries a DRAFT mark."""
    from .payslip_pdf import render_payslip
    with tx(p.user_id) as cur:
        require(cur, entity_id, "payroll:read")
        slip = _load_slip(cur, entity_id, payslip_id)
        cur.execute("SELECT legal_name, registered_address FROM entities WHERE id = %s", (entity_id,))
        employer = cur.fetchone()
    pdf = render_payslip(slip, employer)
    name = re.sub(r"[^A-Za-z0-9-]", "-", f"payslip-{slip['employee_code']}-{slip['period_month'][:7]}")
    return Response(content=pdf, media_type="application/pdf",
                    headers={"Content-Disposition": f'{"attachment" if download else "inline"}; filename="{name}.pdf"', "Cache-Control": "private, no-store"})


# ================================================================== the handbook
class PolicyDocIn(BaseModel):
    slug: str | None = Field(default=None, pattern=r"^[a-z0-9-]{1,60}$")
    title: str = Field(min_length=1, max_length=120)
    category: Literal["general", "leave", "conduct", "posh", "payroll", "it_security", "travel", "exit"] = "general"
    body: str = Field(min_length=1, max_length=40000)
    requires_ack: bool = True


class AckIn(BaseModel):
    employee_id: uuid.UUID
    acknowledged_on: dt.date | None = None


def _doc_out(cur, entity_id, d: dict, *, full: bool) -> dict:
    cur.execute("SELECT count(*) AS n FROM employees WHERE entity_id = %s AND status IN ('onboarding', 'active', 'notice')", (entity_id,))
    people = cur.fetchone()["n"]
    cur.execute("""SELECT a.employee_id, e.full_name AS employee_name, e.employee_code, a.acknowledged_on FROM hr_policy_acks a JOIN employees e ON e.id = a.employee_id
                    WHERE a.document_id = %s AND e.status IN ('onboarding', 'active', 'notice') ORDER BY e.employee_code""", (d["id"],))
    acks = cur.fetchall()
    out = {"id": str(d["id"]), "slug": d["slug"], "title": d["title"], "category": d["category"], "version": d["version"], "requires_ack": d["requires_ack"],
           "is_current": d["is_current"], "published_at": d["published_at"].isoformat(), "acknowledged": len(acks), "people": people}
    if full:
        out["body"] = d["body"]
        out["acknowledgements"] = js(acks)
        cur.execute("""SELECT id AS employee_id, full_name AS employee_name, employee_code FROM employees WHERE entity_id = %s AND status IN ('onboarding', 'active', 'notice')
                          AND id <> ALL(%s) ORDER BY employee_code""", (entity_id, [a["employee_id"] for a in acks]))
        out["waiting"] = js(cur.fetchall()) if d["requires_ack"] else []
    return out


@router.get(E + "/hr/policy-documents", tags=["Payroll & HR"])
def list_policy_docs(entity_id: uuid.UUID, p: Principal = Auth):
    """The handbook: the current version of each document, with how many people have acknowledged it."""
    with tx(p.user_id) as cur:
        require(cur, entity_id, "payroll:read")
        cur.execute("SELECT * FROM hr_policy_documents WHERE entity_id = %s AND is_current ORDER BY category, title", (entity_id,))
        return {"data": [_doc_out(cur, entity_id, d, full=False) for d in cur.fetchall()]}


@router.post(E + "/hr/policy-documents", status_code=201, tags=["Payroll & HR"])
def publish_policy_doc(entity_id: uuid.UUID, body: PolicyDocIn, p: Principal = Auth):
    """Publish a document. Publishing with the slug of an existing one adds its next version; the earlier version is kept, and people
    acknowledge the new one afresh."""
    slug = body.slug or re.sub(r"[^a-z0-9]+", "-", body.title.lower()).strip("-")[:60] or "policy"
    with tx(p.user_id) as cur:
        require(cur, entity_id, "payroll:approve")
        cur.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (f"hr_policy_documents:{entity_id}:{slug}",))
        cur.execute("SELECT coalesce(max(version), 0) AS v FROM hr_policy_documents WHERE entity_id = %s AND slug = %s", (entity_id, slug))
        version = cur.fetchone()["v"] + 1
        cur.execute("UPDATE hr_policy_documents SET is_current = false WHERE entity_id = %s AND slug = %s AND is_current", (entity_id, slug))
        cur.execute("""INSERT INTO hr_policy_documents (entity_id, slug, title, category, body, version, requires_ack, published_by)
                       VALUES (%s,%s,%s,%s,%s,%s,%s, app.current_user_id()) RETURNING *""", (entity_id, slug, body.title.strip(), body.category, body.body, version, body.requires_ack))
        d = cur.fetchone()
        out = _doc_out(cur, entity_id, d, full=True)
        audit(cur, entity_id, "hr_policy_document.published", "hr_policy_documents", d["id"], after={"slug": slug, "version": version, "title": d["title"]})
        emit(cur, entity_id, "hr_policy.changed", "hr_policy_documents", d["id"], {"title": d["title"], "version": version})
        return out


@router.get(E + "/hr/policy-documents/{document_id}", tags=["Payroll & HR"])
def get_policy_doc(entity_id: uuid.UUID, document_id: uuid.UUID, p: Principal = Auth):
    with tx(p.user_id) as cur:
        require(cur, entity_id, "payroll:read")
        cur.execute("SELECT * FROM hr_policy_documents WHERE id = %s AND entity_id = %s", (document_id, entity_id))
        d = cur.fetchone()
        if not d:
            raise ApiError(404, "not_found", "Not found")
        return _doc_out(cur, entity_id, d, full=True)


@router.post(E + "/hr/policy-documents/{document_id}/acknowledgements", tags=["Payroll & HR"])
def acknowledge(entity_id: uuid.UUID, document_id: uuid.UUID, body: AckIn, p: Principal = Auth):
    """Record that a person has read and accepted the document. Recording it twice changes nothing."""
    on = body.acknowledged_on or dt.date.today()
    if on > dt.date.today():
        raise _bad("acknowledged_on", "An acknowledgement cannot be dated in the future")
    with tx(p.user_id) as cur:
        require(cur, entity_id, "payroll:write")
        cur.execute("SELECT * FROM hr_policy_documents WHERE id = %s AND entity_id = %s", (document_id, entity_id))
        d = cur.fetchone()
        if not d:
            raise ApiError(404, "not_found", "Not found")
        if not d["is_current"]:
            raise ApiError(409, "conflict", "A newer version of this document has been published", "Record the acknowledgement on the current version.")
        _employee_row(cur, entity_id, body.employee_id)
        cur.execute("""INSERT INTO hr_policy_acks (document_id, employee_id, entity_id, acknowledged_on, recorded_by) VALUES (%s,%s,%s,%s, app.current_user_id())
                       ON CONFLICT (document_id, employee_id) DO NOTHING""", (document_id, body.employee_id, entity_id, on))
        if cur.rowcount:
            audit(cur, entity_id, "hr_policy_document.acknowledged", "hr_policy_documents", document_id, after={"employee_id": str(body.employee_id), "on": on.isoformat()})
        return _doc_out(cur, entity_id, d, full=True)
