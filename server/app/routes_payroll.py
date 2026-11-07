"""Payroll & HR: employees, salary structures, payroll runs, payslips, wage-code check, labour compliance."""
from __future__ import annotations

import datetime as dt
import json
import uuid
from decimal import Decimal
from typing import Literal, Protocol

from fastapi import APIRouter, Header, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from . import adapters, ledger, payroll as pr, secretbox
from .core import ApiError, Auth, Principal, audit, emit, idempotency_begin, idempotency_finish, jsonable, money, require, tx
from .routes_orders import js, offset_of, page

router = APIRouter()
E = "/entities/{entity_id}"
ZERO = Decimal(0)


def _month_end(d: dt.date) -> dt.date:
    return dt.date(d.year, d.month, pr.days_in(d))


# ================================================================== employees
class BankDetails(BaseModel):
    account_no: str = Field(pattern=r"^[0-9]{6,18}$")
    ifsc: str = Field(pattern=r"^[A-Z]{4}0[A-Z0-9]{6}$")


class EmployeeIn(BaseModel):
    employee_code: str = Field(min_length=1, max_length=20)
    full_name: str
    pan: str | None = Field(default=None, pattern=r"^[A-Z]{5}[0-9]{4}[A-Z]$")
    uan: str | None = Field(default=None, pattern=r"^[0-9]{12}$")
    esic_ip_no: str | None = Field(default=None, pattern=r"^[0-9]{10}$")
    email: str | None = None
    date_of_joining: dt.date
    date_of_leaving: dt.date | None = None
    designation: str | None = None
    department: str | None = None
    work_state: str = Field(pattern=r"^[0-9]{2}$")
    employment_type: Literal["permanent", "fixed_term", "intern", "apprentice", "contract"] = "permanent"
    tax_regime: Literal["new", "old"] = "new"
    bank_account: BankDetails | None = None
    status: Literal["onboarding", "active", "notice", "exited"] = "active"


def structure_out(cur, sid) -> dict:
    cur.execute("SELECT id, lower(effective) AS effective_from, upper(effective) AS effective_to, ctc_annual FROM salary_structures WHERE id = %s", (sid,))
    s = cur.fetchone()
    cur.execute("SELECT code, monthly_amount, kind, in_wages, taxable FROM salary_components WHERE structure_id = %s ORDER BY id", (sid,))
    s["components"] = cur.fetchall()
    return js(s)


def load_employee(cur, entity_id, eid) -> dict:
    cur.execute("""SELECT id, employee_code, full_name, pan, uan, esic_ip_no, email, date_of_joining, date_of_leaving, designation, department, work_state,
                          employment_type, tax_regime, bank_account_enc, bank_ifsc, status FROM employees WHERE id = %s AND entity_id = %s""", (eid, entity_id))
    e = cur.fetchone()
    if not e:
        raise ApiError(404, "not_found", "Not found")
    acct = secretbox.decrypt(e.pop("bank_account_enc"))
    e["bank_account"] = {"account_last4": acct[-4:], "ifsc": e["bank_ifsc"]} if acct else None
    e.pop("bank_ifsc")
    cur.execute("SELECT id FROM salary_structures WHERE employee_id = %s AND effective @> current_date", (eid,))
    s = cur.fetchone()
    e["current_structure"] = structure_out(cur, s["id"]) if s else None
    return js(e)


@router.post(E + "/employees", status_code=201, tags=["Payroll & HR"])
def create_employee(entity_id: uuid.UUID, body: EmployeeIn, p: Principal = Auth):
    with tx(p.user_id) as cur:
        require(cur, entity_id, "payroll:write")
        cur.execute("""INSERT INTO employees (entity_id, employee_code, full_name, pan, uan, esic_ip_no, email, date_of_joining, date_of_leaving, designation,
                              department, work_state, employment_type, tax_regime, bank_account_enc, bank_ifsc, status)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id""",
                    (entity_id, body.employee_code, body.full_name, body.pan, body.uan, body.esic_ip_no, body.email, body.date_of_joining, body.date_of_leaving,
                     body.designation, body.department, body.work_state, body.employment_type, body.tax_regime,
                     secretbox.encrypt(body.bank_account.account_no) if body.bank_account else None, body.bank_account.ifsc if body.bank_account else None, body.status))
        eid = cur.fetchone()["id"]
        audit(cur, entity_id, "employee.created", "employees", eid, after={"code": body.employee_code})
        return load_employee(cur, entity_id, eid)


@router.get(E + "/employees", tags=["Payroll & HR"])
def list_employees(entity_id: uuid.UUID, p: Principal = Auth, status: str | None = None, q: str | None = None, esi_covered: bool | None = None,
                   wage_code_flag: bool | None = None, limit: int = Query(default=50, ge=1, le=200), cursor: str | None = None):
    off = offset_of(cursor)
    with tx(p.user_id) as cur:
        require(cur, entity_id, "payroll:read")
        cur.execute("""SELECT e.id FROM employees e
                        LEFT JOIN v_wage_code_check w ON w.employee_id = e.id
                       WHERE e.entity_id = %s AND (%s::text IS NULL OR e.status = %s)
                         AND (%s::text IS NULL OR e.full_name ILIKE '%%' || %s || '%%' OR e.employee_code ILIKE %s)
                         AND (%s::boolean IS NULL OR (coalesce(w.gross_monthly, 0) BETWEEN 1 AND %s) = %s)
                         AND (%s::boolean IS NULL OR (coalesce(w.wage_pct, 100) < 50) = %s)
                       ORDER BY e.employee_code LIMIT %s OFFSET %s""",
                    (entity_id, status, status, q, q, q, esi_covered, pr.ESI_GROSS_LIMIT, esi_covered, wage_code_flag, wage_code_flag, limit + 1, off))
        return page([load_employee(cur, entity_id, r["id"]) for r in cur.fetchall()], limit, off)


@router.get(E + "/employees/{employee_id}", tags=["Payroll & HR"])
def get_employee(entity_id: uuid.UUID, employee_id: uuid.UUID, p: Principal = Auth):
    with tx(p.user_id) as cur:
        require(cur, entity_id, "payroll:read")
        return load_employee(cur, entity_id, employee_id)


@router.patch(E + "/employees/{employee_id}", tags=["Payroll & HR"])
def update_employee(entity_id: uuid.UUID, employee_id: uuid.UUID, body: dict, p: Principal = Auth):
    allowed = {"full_name", "pan", "uan", "esic_ip_no", "email", "date_of_leaving", "designation", "department", "work_state", "employment_type",
               "tax_regime", "status", "bank_account"}
    with tx(p.user_id) as cur:
        require(cur, entity_id, "payroll:write")
        cur.execute("SELECT * FROM employees WHERE id = %s AND entity_id = %s FOR UPDATE", (employee_id, entity_id))
        e = cur.fetchone()
        if not e:
            raise ApiError(404, "not_found", "Not found")
        merged = {k: e[k] for k in ("employee_code", "full_name", "pan", "uan", "esic_ip_no", "email", "date_of_joining", "date_of_leaving", "designation",
                                     "department", "work_state", "employment_type", "tax_regime", "status")}
        merged.update({k: v for k, v in body.items() if k in allowed})
        try:
            new = EmployeeIn(**{k: (v.strip() if isinstance(v, str) else v) for k, v in merged.items()})
            bank = BankDetails(**body["bank_account"]) if body.get("bank_account") else None
        except Exception as ex:
            raise ApiError(400, "validation_failed", "Invalid employee", str(ex))
        cur.execute("""UPDATE employees SET full_name = %s, pan = %s, uan = %s, esic_ip_no = %s, email = %s, date_of_leaving = %s, designation = %s, department = %s,
                              work_state = %s, employment_type = %s, tax_regime = %s, status = %s,
                              bank_account_enc = coalesce(%s, bank_account_enc), bank_ifsc = coalesce(%s, bank_ifsc) WHERE id = %s""",
                    (new.full_name, new.pan, new.uan, new.esic_ip_no, new.email, new.date_of_leaving, new.designation, new.department, new.work_state,
                     new.employment_type, new.tax_regime, new.status, secretbox.encrypt(bank.account_no) if bank else None, bank.ifsc if bank else None, employee_id))
        audit(cur, entity_id, "employee.updated", "employees", employee_id, after={k: v for k, v in body.items() if k != "bank_account"})
        return load_employee(cur, entity_id, employee_id)


class ComponentIn(BaseModel):
    code: str
    monthly_amount: Decimal = Field(ge=0)
    kind: Literal["earning", "employer_contribution", "reimbursement"]
    in_wages: bool
    taxable: bool = True


class StructureIn(BaseModel):
    effective_from: dt.date
    ctc_annual: Decimal = Field(gt=0)
    components: list[ComponentIn] = Field(min_length=1)


@router.post(E + "/employees/{employee_id}/salary-structures", status_code=201, tags=["Payroll & HR"])
def create_structure(entity_id: uuid.UUID, employee_id: uuid.UUID, body: StructureIn, p: Principal = Auth):
    with tx(p.user_id) as cur:
        require(cur, entity_id, "payroll:write")
        cur.execute("SELECT date_of_joining FROM employees WHERE id = %s AND entity_id = %s FOR UPDATE", (employee_id, entity_id))
        e = cur.fetchone()
        if not e:
            raise ApiError(404, "not_found", "Not found")
        if len({c.code for c in body.components}) != len(body.components):
            raise ApiError(400, "validation_failed", "Duplicate component codes")
        if not any(c.kind == "earning" and c.in_wages for c in body.components):
            raise ApiError(400, "validation_failed", "At least one earning must count as wages (basic pay)")
        monthly = sum((c.monthly_amount for c in body.components), ZERO)
        if abs(monthly * 12 - body.ctc_annual) > 12:
            raise ApiError(400, "validation_failed", f"Components add up to {monthly * 12} a year, not the stated CTC {body.ctc_annual}")
        cur.execute("SELECT id, lower(effective) AS lo FROM salary_structures WHERE employee_id = %s AND upper(effective) IS NULL FOR UPDATE", (employee_id,))
        prev = cur.fetchone()
        if prev:
            if body.effective_from <= prev["lo"]:
                raise ApiError(409, "conflict", f"A structure effective from {prev['lo']} already exists; the revision must start after it")
            cur.execute("UPDATE salary_structures SET effective = daterange(lower(effective), %s) WHERE id = %s", (body.effective_from, prev["id"]))
        cur.execute("""INSERT INTO salary_structures (employee_id, effective, ctc_annual, approved_by) VALUES (%s, daterange(%s, NULL), %s, app.current_user_id())
                       RETURNING id""", (employee_id, body.effective_from, body.ctc_annual))
        sid = cur.fetchone()["id"]
        for c in body.components:
            cur.execute("INSERT INTO salary_components (structure_id, code, monthly_amount, kind, in_wages, taxable) VALUES (%s, %s, %s, %s, %s, %s)",
                        (sid, c.code, c.monthly_amount, c.kind, c.in_wages, c.taxable))
        audit(cur, entity_id, "salary_structure.created", "employees", employee_id, after={"ctc": body.ctc_annual, "from": body.effective_from})
        return structure_out(cur, sid)


# ================================================================== wage-code check
@router.get(E + "/payroll/wage-code-check", tags=["Payroll & HR"])
def wage_code_check(entity_id: uuid.UUID, p: Principal = Auth):
    with tx(p.user_id) as cur:
        require(cur, entity_id, "payroll:read")
        cur.execute("""SELECT employee_id, full_name, gross_monthly, wages_monthly, wage_pct, statutory_wage FROM v_wage_code_check
                        WHERE entity_id = %s AND wage_pct < 50 ORDER BY wage_pct, full_name""", (entity_id,))
        rows = cur.fetchall()
        for r in rows:
            r["pf_wage_should_be"] = pr.pf_wage(r["statutory_wage"])
            r["wage_pct"] = float(r["wage_pct"])
        return js(rows)


# ================================================================== payroll runs
class RunIn(BaseModel):
    period_month: dt.date


def statutory_dues(cur, entity_id, run: dict) -> list[dict]:
    if run["status"] not in ("approved", "bank_file_sent", "paid", "statutory_done"):
        return []
    cur.execute("""SELECT coalesce(sum(tds), 0) AS tds, coalesce(sum(pf_employee + pf_employer_epf + pf_employer_eps), 0) AS pf,
                          coalesce(sum(esi_employee + esi_employer), 0) AS esi, coalesce(sum(professional_tax), 0) AS pt FROM payslips WHERE run_id = %s""", (run["id"],))
    t = cur.fetchone()
    nxt = dt.date(run["period_month"].year + (run["period_month"].month == 12), run["period_month"].month % 12 + 1, 1)
    items = [("TDS on salary", t["tds"], "TDS.DEPOSIT.MONTHLY", 7), ("PF", t["pf"], "EPF.ECR.MONTHLY", 15), ("ESI", t["esi"], "ESI.MONTHLY", 15),
             ("Professional tax", t["pt"], None, 20)]
    out = []
    for kind, amount, rule, day in items:
        if not amount:
            continue
        cur.execute("""SELECT o.id FROM obligations o JOIN compliance_rules r ON r.id = o.rule_id WHERE o.entity_id = %s AND o.period_start = %s
                          AND (r.code = %s OR (%s::text IS NULL AND r.code LIKE 'PT.%%.MONTHLY')) ORDER BY o.id LIMIT 1""", (entity_id, run["period_month"], rule, rule))
        o = cur.fetchone()
        out.append({"kind": kind, "amount": amount, "due_date": dt.date(nxt.year, nxt.month, day), "obligation_id": o["id"] if o else None})
    return out


def load_run(cur, entity_id, rid) -> dict:
    cur.execute("""SELECT id, period_month, status, headcount, gross_total, net_total, employer_cost_total, approval_request_id, paid_on
                     FROM payroll_runs WHERE id = %s AND entity_id = %s""", (rid, entity_id))
    r = cur.fetchone()
    if not r:
        raise ApiError(404, "not_found", "Not found")
    r["statutory"] = statutory_dues(cur, entity_id, r)
    for k in ("headcount", "gross_total", "net_total", "employer_cost_total"):
        r[k] = r[k] if r[k] is not None else (0 if k == "headcount" else ZERO)
    return js(r)


@router.post(E + "/payroll-runs", status_code=201, tags=["Payroll & HR"])
def create_run(entity_id: uuid.UUID, body: RunIn, p: Principal = Auth):
    if body.period_month.day != 1:
        raise ApiError(400, "validation_failed", "period_month must be the first day of the month")
    with tx(p.user_id) as cur:
        require(cur, entity_id, "payroll:write")
        cur.execute("""SELECT count(*) AS n FROM employees WHERE entity_id = %s AND status <> 'onboarding' AND date_of_joining <= %s
                          AND (date_of_leaving IS NULL OR date_of_leaving >= %s)""", (entity_id, _month_end(body.period_month), body.period_month))
        cur.execute("INSERT INTO payroll_runs (entity_id, period_month, headcount) VALUES (%s, %s, %s) RETURNING id", (entity_id, body.period_month, cur.fetchone()["n"]))
        rid = cur.fetchone()["id"]
        audit(cur, entity_id, "payroll_run.created", "payroll_runs", rid, after={"month": body.period_month})
        return load_run(cur, entity_id, rid)


@router.get(E + "/payroll-runs", tags=["Payroll & HR"])
def list_runs(entity_id: uuid.UUID, p: Principal = Auth, fy: str | None = Query(default=None, pattern=r"^[0-9]{4}-[0-9]{2}$"),
              limit: int = Query(default=50, ge=1, le=200), cursor: str | None = None):
    off = offset_of(cursor)
    with tx(p.user_id) as cur:
        require(cur, entity_id, "payroll:read")
        cur.execute("""SELECT id FROM payroll_runs WHERE entity_id = %s AND (%s::text IS NULL OR app.fy_of(period_month) = %s)
                        ORDER BY period_month DESC LIMIT %s OFFSET %s""", (entity_id, fy, fy, limit + 1, off))
        return page([load_run(cur, entity_id, r["id"]) for r in cur.fetchall()], limit, off)


@router.get(E + "/payroll-runs/{run_id}", tags=["Payroll & HR"])
def get_run(entity_id: uuid.UUID, run_id: uuid.UUID, p: Principal = Auth):
    with tx(p.user_id) as cur:
        require(cur, entity_id, "payroll:read")
        return load_run(cur, entity_id, run_id)


class InputIn(BaseModel):
    employee_id: uuid.UUID
    lop_days: Decimal = Field(default=ZERO, ge=0, le=31)
    overtime: Decimal = Field(default=ZERO, ge=0)
    bonus: Decimal = Field(default=ZERO, ge=0)
    reimbursements: Decimal = Field(default=ZERO, ge=0)
    other_deductions: Decimal = Field(default=ZERO, ge=0)


def _run_for_update(cur, entity_id, rid, allowed: tuple[str, ...]):
    cur.execute("SELECT * FROM payroll_runs WHERE id = %s AND entity_id = %s FOR UPDATE", (rid, entity_id))
    r = cur.fetchone()
    if not r:
        raise ApiError(404, "not_found", "Not found")
    if r["status"] not in allowed:
        raise ApiError(409, "conflict", f"Run is {r['status']}; this needs one of: {', '.join(allowed)}")
    return r


@router.put(E + "/payroll-runs/{run_id}/inputs", tags=["Payroll & HR"])
def put_inputs(entity_id: uuid.UUID, run_id: uuid.UUID, body: list[InputIn], p: Principal = Auth):
    with tx(p.user_id) as cur:
        require(cur, entity_id, "payroll:write")
        run = _run_for_update(cur, entity_id, run_id, ("inputs_open", "computed"))
        for i in body:
            cur.execute("SELECT 1 FROM employees WHERE id = %s AND entity_id = %s", (i.employee_id, entity_id))
            if not cur.fetchone():
                raise ApiError(400, "validation_failed", f"employee {i.employee_id} is not in this entity")
        # inputs are one-off values for this month, kept as one JSON document per run
        merged = {str(i.employee_id): {k: str(v) for k, v in i.model_dump(mode="json").items() if k != "employee_id"} for i in body}
        cur.execute("UPDATE payroll_runs SET status = 'inputs_open' WHERE id = %s", (run_id,))
        cur.execute("""INSERT INTO payroll_run_inputs (run_id, inputs) VALUES (%s, %s) ON CONFLICT (run_id) DO UPDATE SET inputs = EXCLUDED.inputs""",
                    (run_id, json.dumps(merged)))
        audit(cur, entity_id, "payroll_run.inputs_set", "payroll_runs", run_id, after={"employees": len(body)})
        return load_run(cur, entity_id, run_id)


@router.get(E + "/payroll-runs/{run_id}/inputs", tags=["Payroll & HR"])
def get_inputs(entity_id: uuid.UUID, run_id: uuid.UUID, p: Principal = Auth):
    """What was last set for the month: one row per employee that has any attendance or one-off figure. Money as two-decimal strings."""
    with tx(p.user_id) as cur:
        require(cur, entity_id, "payroll:read")
        cur.execute("SELECT 1 FROM payroll_runs WHERE id = %s AND entity_id = %s", (run_id, entity_id))
        if not cur.fetchone():
            raise ApiError(404, "not_found", "Not found")
        cur.execute("SELECT inputs FROM payroll_run_inputs WHERE run_id = %s", (run_id,))
        stored = (cur.fetchone() or {}).get("inputs") or {}
    out = []
    for emp_id, v in stored.items():
        row = {"employee_id": emp_id, "lop_days": str(Decimal(v.get("lop_days", "0")).normalize() if Decimal(v.get("lop_days", "0")) else "0")}
        for k in ("overtime", "bonus", "reimbursements", "other_deductions"):
            row[k] = money(Decimal(v.get(k, "0")))
        out.append(row)
    return sorted(out, key=lambda r: r["employee_id"])


def _history(cur, entity_id, employee_id, month_start: dt.date) -> dict:
    cur.execute("SELECT app.fy_of(%s::date) AS fy", (month_start,))
    fy = cur.fetchone()["fy"]
    cur.execute("""SELECT count(*) AS n, coalesce(sum(s.gross - coalesce(l.reimb, 0)), 0) AS gross, coalesce(sum(s.tds), 0) AS tds
                     FROM payslips s JOIN payroll_runs r ON r.id = s.run_id
                     LEFT JOIN LATERAL (SELECT sum(amount) AS reimb FROM payslip_lines WHERE payslip_id = s.id AND code = 'REIMBURSEMENT') l ON true
                    WHERE s.employee_id = %s AND r.entity_id = %s AND app.fy_of(r.period_month) = %s AND r.period_month < %s
                      AND r.status IN ('approved', 'bank_file_sent', 'paid', 'statutory_done')""", (employee_id, entity_id, fy, month_start))
    h = cur.fetchone()
    return {"prior_payslips": h["n"], "prior_gross": h["gross"], "tds_so_far": h["tds"]}


def compute_run(cur, entity_id, run: dict) -> dict:
    month = run["period_month"]
    cur.execute("SELECT 1 FROM entity_registrations WHERE entity_id = %s AND kind = 'epf' AND status = 'active' LIMIT 1", (entity_id,))
    pf_on = bool(cur.fetchone())
    cur.execute("SELECT 1 FROM entity_registrations WHERE entity_id = %s AND kind = 'esic' AND status = 'active' LIMIT 1", (entity_id,))
    esi_on = bool(cur.fetchone())
    cur.execute("SELECT inputs FROM payroll_run_inputs WHERE run_id = %s", (run["id"],))
    stored = (cur.fetchone() or {}).get("inputs") or {}
    cur.execute("""SELECT e.* FROM employees e WHERE e.entity_id = %s AND e.status <> 'onboarding' AND e.date_of_joining <= %s
                      AND (e.date_of_leaving IS NULL OR e.date_of_leaving >= %s) ORDER BY e.employee_code""", (entity_id, _month_end(month), month))
    emps = cur.fetchall()
    missing = []
    slips = []
    for e in emps:
        cur.execute("SELECT id FROM salary_structures WHERE employee_id = %s AND effective @> %s::date", (e["id"], min(_month_end(month), e["date_of_leaving"] or _month_end(month))))
        st = cur.fetchone()
        if not st:
            missing.append(e["employee_code"])
            continue
        cur.execute("SELECT code, monthly_amount, kind, in_wages, taxable FROM salary_components WHERE structure_id = %s ORDER BY id", (st["id"],))
        comps = [pr.Component(c["code"], c["monthly_amount"], c["kind"], c["in_wages"], c["taxable"]) for c in cur.fetchall()]
        raw = stored.get(str(e["id"]), {})
        inp = pr.Inputs(**{k: Decimal(v) for k, v in raw.items() if k in ("lop_days", "overtime", "bonus", "reimbursements", "other_deductions")})
        slips.append((e, pr.compute_payslip(month_start=month, joined=e["date_of_joining"], left=e["date_of_leaving"], work_state=e["work_state"],
                                            regime=e["tax_regime"], components=comps, inputs=inp, pf_applicable=pf_on, esi_applicable=esi_on,
                                            tds_history=_history(cur, entity_id, e["id"], month))))
    if missing:
        raise ApiError(409, "conflict", "Some employees have no salary structure for this month", ", ".join(missing))
    cur.execute("DELETE FROM payslips WHERE run_id = %s", (run["id"],))
    gross = net = employer = ZERO
    for e, s in slips:
        cur.execute("""INSERT INTO payslips (run_id, employee_id, paid_days, lop_days, gross, wages, pf_wage, pf_employee, pf_employer_epf, pf_employer_eps,
                              esi_applicable, esi_employee, esi_employer, professional_tax, tds, other_deductions, net_pay)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id""",
                    (run["id"], e["id"], s.paid_days, s.lop_days, s.gross, s.wages, s.pf_wage, s.pf_employee, s.pf_employer_epf, s.pf_employer_eps,
                     s.esi_applicable, s.esi_employee, s.esi_employer, s.professional_tax, s.tds, s.other_deductions, s.net_pay))
        sid = cur.fetchone()["id"]
        for code, label, kind, amount in s.lines:
            cur.execute("INSERT INTO payslip_lines (payslip_id, code, label, kind, amount) VALUES (%s, %s, %s, %s, %s)", (sid, code, label, kind, amount))
        gross += s.gross
        net += s.net_pay
        employer += s.gross + s.pf_employer_epf + s.pf_employer_eps + s.esi_employer
    cur.execute("""UPDATE payroll_runs SET status = 'computed', headcount = %s, gross_total = %s, net_total = %s, employer_cost_total = %s WHERE id = %s""",
                (len(slips), gross, net, employer, run["id"]))
    audit(cur, entity_id, "payroll_run.computed", "payroll_runs", run["id"], after={"headcount": len(slips), "gross": gross, "net": net})
    return {"headcount": len(slips)}


@router.post(E + "/payroll-runs/{run_id}/compute", tags=["Payroll & HR"])
def compute_endpoint(entity_id: uuid.UUID, run_id: uuid.UUID, p: Principal = Auth):
    with tx(p.user_id) as cur:
        require(cur, entity_id, "payroll:write")
        run = _run_for_update(cur, entity_id, run_id, ("inputs_open", "inputs_locked", "computed"))
        compute_run(cur, entity_id, run)
        return load_run(cur, entity_id, run_id)


@router.post(E + "/payroll-runs/{run_id}/submit", status_code=201, tags=["Payroll & HR"])
def submit_run(entity_id: uuid.UUID, run_id: uuid.UUID, p: Principal = Auth):
    with tx(p.user_id) as cur:
        require(cur, entity_id, "payroll:write")
        run = _run_for_update(cur, entity_id, run_id, ("computed",))
        cur.execute("SELECT 1 FROM approval_requests WHERE subject = 'payroll_run' AND subject_id = %s AND status = 'pending'", (run_id,))
        if cur.fetchone():
            raise ApiError(409, "conflict", "This run is already waiting for approval")
        cur.execute("""INSERT INTO approval_requests (entity_id, subject, subject_id, summary, amount, requested_by) VALUES (%s, 'payroll_run', %s, %s, %s, app.current_user_id())
                       RETURNING *""", (entity_id, run_id, f"Approve payroll for {run['period_month']:%B %Y} ({run['headcount']} employees)", run["net_total"]))
        ar = cur.fetchone()
        cur.execute("UPDATE payroll_runs SET approval_request_id = %s WHERE id = %s", (ar["id"], run_id))
        audit(cur, entity_id, "payroll_run.submitted", "payroll_runs", run_id)
        emit(cur, entity_id, "approval.requested", "approval_requests", ar["id"], {"summary": ar["summary"]})
        return js(ar)


def on_run_approved(cur, entity_id, run_id) -> None:
    """Called by the approval decision: post the payroll journal and record the TDS withheld on salary."""
    cur.execute("SELECT * FROM payroll_runs WHERE id = %s FOR UPDATE", (run_id,))
    run = cur.fetchone()
    if run["status"] != "computed":
        return
    cur.execute("""SELECT coalesce(sum(gross), 0) AS gross, coalesce(sum(net_pay), 0) AS net, coalesce(sum(pf_employee), 0) AS pf_employee,
                          coalesce(sum(pf_employer_epf + pf_employer_eps), 0) AS pf_employer, coalesce(sum(esi_employee), 0) AS esi_employee,
                          coalesce(sum(esi_employer), 0) AS esi_employer, coalesce(sum(professional_tax), 0) AS pt, coalesce(sum(tds), 0) AS tds,
                          coalesce(sum(other_deductions), 0) AS other FROM payslips WHERE run_id = %s""", (run_id,))
    totals = cur.fetchone()
    jid = ledger.post_journal(cur, entity_id, _month_end(run["period_month"]), "payroll_run", run_id, f"Payroll {run['period_month']:%b %Y}", ledger.payroll_run_lines(totals))
    cur.execute("UPDATE payroll_runs SET status = 'approved', journal_entry_id = %s WHERE id = %s", (jid, run_id))
    cur.execute("""INSERT INTO tds_deductions (entity_id, deductee_employee_id, deductee_pan, section, deducted_on, base_amount, rate_pct, tds_amount, source_kind, source_id)
                   SELECT %s, s.employee_id, e.pan, '192', %s, s.gross, CASE WHEN s.gross > 0 THEN round(s.tds * 100 / s.gross, 4) ELSE 0 END, s.tds, 'payroll_run', %s
                     FROM payslips s JOIN employees e ON e.id = s.employee_id WHERE s.run_id = %s AND s.tds > 0""",
                (entity_id, _month_end(run["period_month"]), run_id, run_id))
    audit(cur, entity_id, "payroll_run.approved", "payroll_runs", run_id, after={"journal": jid})
    emit(cur, entity_id, "approval.decided", "payroll_runs", run_id, {"status": "approved"})


class BankFileIn(BaseModel):
    bank_account_id: uuid.UUID
    format: Literal["h2h_api", "csv", "bank_specific"] = "csv"


@router.post(E + "/payroll-runs/{run_id}/bank-file", status_code=202, tags=["Payroll & HR"])
def bank_file(entity_id: uuid.UUID, run_id: uuid.UUID, body: BankFileIn, p: Principal = Auth,
              idempotency_key: str | None = Header(default=None, alias="Idempotency-Key")):
    with tx(p.user_id) as cur:
        require(cur, entity_id, "payroll:pay")
        replay = idempotency_begin(cur, idempotency_key, "POST", f"/entities/{entity_id}/payroll-runs/{run_id}/bank-file", body.model_dump(mode="json"))
        if replay:
            return JSONResponse(replay["body"], status_code=replay["status"])
        cur.execute("SELECT status FROM payroll_runs WHERE id = %s AND entity_id = %s FOR UPDATE", (run_id, entity_id))
        r = cur.fetchone()
        if not r:
            raise ApiError(404, "not_found", "Not found")
        if r["status"] in ("bank_file_sent", "paid", "statutory_done"):
            raise ApiError(409, "conflict", f"Salaries are already {r['status'].replace('_', ' ')}")
        if r["status"] != "approved":
            raise ApiError(409, "approval_required", "The payroll run has not been approved")
        cur.execute("SELECT 1 FROM bank_accounts WHERE id = %s AND entity_id = %s AND is_active", (body.bank_account_id, entity_id))
        if not cur.fetchone():
            raise ApiError(400, "validation_failed", "bank_account_id is not one of our accounts")
        cur.execute("""SELECT count(*) AS n FROM payslips s JOIN employees e ON e.id = s.employee_id WHERE s.run_id = %s AND e.bank_account_enc IS NULL""", (run_id,))
        if cur.fetchone()["n"]:
            raise ApiError(409, "conflict", "Some employees have no bank account on file")
        cur.execute("""INSERT INTO jobs (entity_id, kind, subject, subject_id, payload, created_by) VALUES (%s, 'payroll.bank_file', 'payroll_runs', %s, %s, app.current_user_id())
                       RETURNING id, kind, status, progress, subject, subject_id, result, created_at, finished_at""",
                    (entity_id, run_id, json.dumps(body.model_dump(mode="json"))))
        job = js(cur.fetchone())
        audit(cur, entity_id, "payroll_run.bank_file_requested", "payroll_runs", run_id)
        idempotency_finish(cur, idempotency_key, 202, job)
    return JSONResponse(job, status_code=202, headers={"Location": f"/v1/jobs/{job['id']}"})


BANK = adapters.proxy("bank")


def handle_bank_file(cur, job: dict) -> dict:
    entity_id, pl = job["entity_id"], job["payload"]
    cur.execute("SELECT * FROM payroll_runs WHERE id = %s FOR UPDATE", (job["subject_id"],))
    run = cur.fetchone()
    if run["status"] != "approved":
        raise RuntimeError(f"run is {run['status']}, expected approved")
    cur.execute("""SELECT e.employee_code, e.full_name, e.bank_account_enc, e.bank_ifsc, s.net_pay FROM payslips s JOIN employees e ON e.id = s.employee_id
                    WHERE s.run_id = %s ORDER BY e.employee_code""", (run["id"],))
    rows = cur.fetchall()
    csv = "employee_code,name,account_no,ifsc,amount\n" + "\n".join(
        f"{r['employee_code']},{r['full_name']},{secretbox.decrypt(r['bank_account_enc'])},{r['bank_ifsc']},{r['net_pay']}" for r in rows)
    res = BANK.send_salaries({"run_id": str(run["id"]), "csv": csv})
    paid_on = dt.date.today()
    cur.execute("UPDATE payroll_runs SET status = 'paid', paid_on = %s WHERE id = %s", (paid_on, run["id"]))
    cur.execute("SELECT ledger_account_id FROM bank_accounts WHERE id = %s", (pl["bank_account_id"],))
    ledger.post_journal(cur, entity_id, paid_on, "bank", run["id"], f"Salaries {run['period_month']:%b %Y} ({res['batch_ref']})",
                        [ledger.Line("salary_payable", debit=run["net_total"]), ledger.Line("account:" + str(cur.fetchone()["ledger_account_id"]), credit=run["net_total"])])
    audit(cur, entity_id, "payroll_run.paid", "payroll_runs", run["id"], after={"batch": res["batch_ref"], "employees": len(rows)}, actor_kind="integration")
    return {"batch_ref": res["batch_ref"], "employees": len(rows), "total": money(run["net_total"]), "format": pl.get("format", "csv")}


@router.get(E + "/payroll-runs/{run_id}/payslips", tags=["Payroll & HR"])
def list_payslips(entity_id: uuid.UUID, run_id: uuid.UUID, p: Principal = Auth, flag: str | None = None, limit: int = Query(default=50, ge=1, le=200),
                  cursor: str | None = None):
    off = offset_of(cursor)
    with tx(p.user_id) as cur:
        require(cur, entity_id, "payroll:read")
        cur.execute("SELECT period_month FROM payroll_runs WHERE id = %s AND entity_id = %s", (run_id, entity_id))
        run = cur.fetchone()
        if not run:
            raise ApiError(404, "not_found", "Not found")
        cur.execute("""SELECT s.id, s.employee_id, e.full_name AS employee_name, s.paid_days, s.lop_days, s.gross, s.wages, s.pf_wage, s.pf_employee,
                              s.pf_employer_epf, s.pf_employer_eps, s.esi_applicable, s.esi_employee, s.esi_employer, s.professional_tax, s.tds,
                              s.other_deductions, s.net_pay,
                              (SELECT round(100.0 * sum(c.monthly_amount) FILTER (WHERE c.in_wages) / nullif(sum(c.monthly_amount), 0), 1)
                                 FROM salary_structures st JOIN salary_components c ON c.structure_id = st.id AND c.kind = 'earning'
                                WHERE st.employee_id = s.employee_id AND st.effective @> (%s::date + 27)) AS wage_pct
                         FROM payslips s JOIN employees e ON e.id = s.employee_id WHERE s.run_id = %s ORDER BY e.employee_code""", (run["period_month"], run_id))
        rows = []
        for r in cur.fetchall():
            pct = r.pop("wage_pct")
            r["flags"] = ["wage_below_50pct"] if pct is not None and pct < 50 else []
            r["paid_days"], r["lop_days"] = float(r["paid_days"]), float(r["lop_days"])
            r["pdf_url"] = None
            if flag is None or flag in r["flags"]:
                rows.append(r)
        return {"data": js(rows[off:off + limit]), "next_cursor": str(off + limit) if len(rows) > off + limit else None}


# ================================================================== labour compliance
class GratuityIn(BaseModel):
    as_of: dt.date
    liability: Decimal = Field(ge=0)
    funded_amount: Decimal = Field(default=ZERO, ge=0)
    actuary: str | None = None


@router.post(E + "/payroll/gratuity-valuations", status_code=201, tags=["Payroll & HR"])
def record_gratuity(entity_id: uuid.UUID, body: GratuityIn, p: Principal = Auth):
    with tx(p.user_id) as cur:
        require(cur, entity_id, "payroll:write")
        cur.execute("""INSERT INTO gratuity_valuations (entity_id, as_of, liability, funded_amount, actuary) VALUES (%s, %s, %s, %s, %s)
                       ON CONFLICT (entity_id, as_of) DO UPDATE SET liability = EXCLUDED.liability, funded_amount = EXCLUDED.funded_amount, actuary = coalesce(EXCLUDED.actuary, gratuity_valuations.actuary)
                       RETURNING id, as_of, liability, funded_amount, actuary""", (entity_id, body.as_of, body.liability, body.funded_amount, body.actuary))
        out = js(cur.fetchone())
        audit(cur, entity_id, "gratuity_valuation.recorded", "gratuity_valuations", out["id"], after={"as_of": body.as_of, "liability": body.liability})
        return out


class FyIn(BaseModel):
    fy: str = Field(pattern=r"^[0-9]{4}-[0-9]{2}$")


@router.post(E + "/payroll/statutory-bonus/accrue", tags=["Payroll & HR"])
def accrue_bonus(entity_id: uuid.UUID, body: FyIn, p: Principal = Auth):
    start_year = int(body.fy[:4])
    fy_start, fy_end = dt.date(start_year, 4, 1), dt.date(start_year + 1, 3, 31)
    pay_by = dt.date(start_year + 1, 11, 30)                                      # within 8 months of the year end
    with tx(p.user_id) as cur:
        require(cur, entity_id, "payroll:write")
        cur.execute("""SELECT e.id, e.date_of_joining, e.date_of_leaving,
                              (SELECT sum(c.monthly_amount) FILTER (WHERE c.in_wages AND c.kind = 'earning') FROM salary_components c
                                WHERE c.structure_id = (SELECT s.id FROM salary_structures s WHERE s.employee_id = e.id AND s.effective && daterange(%s, %s, '[]')
                                                         ORDER BY lower(s.effective) DESC LIMIT 1)) AS wages_monthly
                         FROM employees e WHERE e.entity_id = %s AND e.date_of_joining <= %s AND (e.date_of_leaving IS NULL OR e.date_of_leaving >= %s)""",
                    (fy_start, fy_end, entity_id, fy_end, fy_start))
        rows = [r for r in cur.fetchall() if r["wages_monthly"] is not None]
        accrued, total = 0, ZERO
        for e in rows:
            first, last = max(fy_start, e["date_of_joining"]), min(fy_end, e["date_of_leaving"] or fy_end)
            months = (last.year - first.year) * 12 + last.month - first.month + 1
            amount = pr.bonus_for_year(e["wages_monthly"], months)
            if amount <= 0:
                continue
            cur.execute("""INSERT INTO statutory_bonus (entity_id, employee_id, fy, rate_pct, amount, pay_by) VALUES (%s, %s, %s, %s, %s, %s)
                           ON CONFLICT (employee_id, fy) DO UPDATE SET amount = EXCLUDED.amount, pay_by = EXCLUDED.pay_by WHERE statutory_bonus.paid_on IS NULL""",
                        (entity_id, e["id"], body.fy, pr.BONUS_MIN_PCT, amount, pay_by))
            accrued += 1
            total += amount
        audit(cur, entity_id, "statutory_bonus.accrued", "statutory_bonus", None, after={"fy": body.fy, "employees": accrued, "total": total})
        return js({"fy": body.fy, "accrued": accrued, "total": total, "pay_by": pay_by})


class MemberIn(BaseModel):
    name: str
    role: Literal["presiding_officer", "member", "external_member"]
    employee_id: uuid.UUID | None = None


class CommitteeIn(BaseModel):
    kind: Literal["posh_internal", "audit", "nomination_remuneration", "csr", "grievance"]
    constituted_on: dt.date
    members: list[MemberIn] = Field(min_length=1)


def load_committee(cur, entity_id, cid) -> dict:
    cur.execute("SELECT id, kind, constituted_on, dissolved_on FROM committees WHERE id = %s AND entity_id = %s", (cid, entity_id))
    c = cur.fetchone()
    cur.execute("SELECT name, role, employee_id FROM committee_members WHERE committee_id = %s ORDER BY name", (cid,))
    c["members"] = cur.fetchall()
    return js(c)


@router.post(E + "/committees", status_code=201, tags=["Payroll & HR"])
def create_committee(entity_id: uuid.UUID, body: CommitteeIn, p: Principal = Auth):
    with tx(p.user_id) as cur:
        require(cur, entity_id, "payroll:write")
        if body.kind == "posh_internal" and not any(m.role == "presiding_officer" for m in body.members):
            raise ApiError(400, "validation_failed", "An Internal Committee needs a presiding officer")
        cur.execute("INSERT INTO committees (entity_id, kind, constituted_on) VALUES (%s, %s, %s) RETURNING id", (entity_id, body.kind, body.constituted_on))
        cid = cur.fetchone()["id"]
        for m in body.members:
            cur.execute("INSERT INTO committee_members (committee_id, name, role, employee_id) VALUES (%s, %s, %s, %s)", (cid, m.name, m.role, m.employee_id))
        audit(cur, entity_id, "committee.constituted", "committees", cid, after={"kind": body.kind})
        return load_committee(cur, entity_id, cid)


@router.get(E + "/committees", tags=["Payroll & HR"])
def list_committees(entity_id: uuid.UUID, p: Principal = Auth, kind: str | None = None, limit: int = Query(default=50, ge=1, le=200), cursor: str | None = None):
    off = offset_of(cursor)
    with tx(p.user_id) as cur:
        require(cur, entity_id, "payroll:read")
        cur.execute("SELECT id FROM committees WHERE entity_id = %s AND (%s::text IS NULL OR kind = %s) ORDER BY constituted_on DESC, id LIMIT %s OFFSET %s",
                    (entity_id, kind, kind, limit + 1, off))
        return page([load_committee(cur, entity_id, r["id"]) for r in cur.fetchall()], limit, off)


@router.get(E + "/payroll/labour-compliance", tags=["Payroll & HR"])
def labour_compliance(entity_id: uuid.UUID, p: Principal = Auth, as_of: dt.date | None = None):
    today = as_of or dt.date.today()
    with tx(p.user_id) as cur:
        require(cur, entity_id, "payroll:read")
        cur.execute("""SELECT e.id, e.date_of_joining, w.wages_monthly, w.wage_pct FROM employees e
                         LEFT JOIN v_wage_code_check w ON w.employee_id = e.id
                        WHERE e.entity_id = %s AND e.status IN ('active', 'notice')""", (entity_id,))
        staff = cur.fetchall()
        n = len(staff)
        flagged = sum(1 for s in staff if s["wage_pct"] is not None and s["wage_pct"] < 50)
        applies = n >= pr.GRATUITY_EMPLOYEE_THRESHOLD
        estimate = sum((pr.gratuity_provision(s["wages_monthly"] or ZERO, s["date_of_joining"], today) for s in staff), ZERO)
        cur.execute("SELECT as_of, liability, funded_amount, actuary FROM gratuity_valuations WHERE entity_id = %s ORDER BY as_of DESC LIMIT 1", (entity_id,))
        latest = cur.fetchone()
        cur.execute("SELECT count(*) AS n, coalesce(sum(amount), 0) AS total, min(pay_by) AS pay_by FROM statutory_bonus WHERE entity_id = %s AND fy = app.fy_of(%s::date - 365)",
                    (entity_id, today))
        bonus = cur.fetchone()
        eligible = sum(1 for s in staff if s["wages_monthly"] is not None and s["wages_monthly"] <= pr.BONUS_ELIGIBILITY_WAGE)
        cur.execute("""SELECT c.id, count(m.*) AS members, bool_or(m.role = 'external_member') AS external FROM committees c LEFT JOIN committee_members m ON m.committee_id = c.id
                        WHERE c.entity_id = %s AND c.kind = 'posh_internal' AND c.dissolved_on IS NULL GROUP BY c.id ORDER BY c.constituted_on DESC LIMIT 1""", (entity_id,))
        ic = cur.fetchone()
        cur.execute("""SELECT valid_until FROM entity_registrations WHERE entity_id = %s AND kind = 'shops_establishment' AND status = 'active'
                        ORDER BY valid_until DESC NULLS FIRST LIMIT 1""", (entity_id,))
        shops = cur.fetchone()
        return js({
            "headcount": n,
            "wage_code": {"flagged": flagged, "employees": n, "status": "fix" if flagged else "ok"},
            "gratuity": {"applies": applies, "liability_estimate": estimate if applies else ZERO, "latest_valuation": latest,
                         "status": "not_applicable" if not applies else ("provided" if latest else "valuation_needed")},
            "statutory_bonus": {"eligible": eligible, "accrued_employees": bonus["n"], "accrued_total": bonus["total"], "pay_by": bonus["pay_by"]},
            "posh": {"required": n >= pr.POSH_EMPLOYEE_THRESHOLD, "constituted": ic is not None, "members": ic["members"] if ic else 0,
                     "has_external_member": bool(ic and ic["external"]),
                     "status": "not_applicable" if n < pr.POSH_EMPLOYEE_THRESHOLD else ("constituted" if ic and ic["external"] else "action_needed")},
            "shops_establishment": {"registered": shops is not None, "valid_until": shops["valid_until"] if shops else None,
                                    "status": "missing" if not shops else "expiring" if shops["valid_until"] and shops["valid_until"] < today + dt.timedelta(days=60) else "valid"},
        })
