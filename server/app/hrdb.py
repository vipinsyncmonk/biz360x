"""HR helpers that read and write the database inside the caller's transaction: the policy in force, leave types, holidays, leave
balances, salary structures built from a CTC, and the unpaid leave a payroll run must deduct. The maths is in hr.py."""
from __future__ import annotations

import datetime as dt
import json
from decimal import Decimal

from . import hr, payroll as pr
from .core import ApiError

ZERO = Decimal(0)
OPEN_RUN = ("inputs_open", "inputs_locked", "computed")


def current_policy(cur, entity_id, as_of: dt.date | None = None) -> dict:
    """The policy in force on a date, with the starter values for anything the company has not set. `effective_from` is None when the
    company has never saved one."""
    cur.execute("""SELECT effective_from, salary, leave, payroll, onboarding FROM hr_policies WHERE entity_id = %s AND effective_from <= %s
                    ORDER BY effective_from DESC LIMIT 1""", (entity_id, as_of or dt.date.today()))
    row = cur.fetchone()
    out = hr.default_policy()
    out["effective_from"] = None
    if row:
        for k in ("salary", "leave", "payroll"):
            out[k].update(row[k] or {})
        if (row["onboarding"] or {}).get("checklist"):
            out["onboarding"] = row["onboarding"]
        out["effective_from"] = row["effective_from"]
    return out


def registrations(cur, entity_id) -> tuple[bool, bool]:
    """(registered for PF, registered for ESI)."""
    cur.execute("SELECT kind::text AS kind FROM entity_registrations WHERE entity_id = %s AND kind IN ('epf', 'esic') AND status = 'active'", (entity_id,))
    kinds = {r["kind"] for r in cur.fetchall()}
    return "epf" in kinds, "esic" in kinds


def ensure_leave_types(cur, entity_id) -> None:
    """A company that has set none gets the starter leave types the first time leave is used."""
    cur.execute("SELECT 1 FROM leave_types WHERE entity_id = %s LIMIT 1", (entity_id,))
    if cur.fetchone():
        return
    for code, name, quota, accrual, paid, carry, encash in hr.DEFAULT_LEAVE_TYPES:
        cur.execute("""INSERT INTO leave_types (entity_id, code, name, annual_quota, accrual, paid, carry_forward_max, encashable) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
                       ON CONFLICT (entity_id, code) DO NOTHING""", (entity_id, code, name, quota, accrual, paid, carry, encash))


def leave_types(cur, entity_id, *, active_only: bool = True) -> list[dict]:
    ensure_leave_types(cur, entity_id)
    cur.execute("""SELECT id, code, name, annual_quota, accrual, paid, carry_forward_max, encashable, is_active FROM leave_types
                    WHERE entity_id = %s AND (is_active OR NOT %s) ORDER BY paid DESC, code""", (entity_id, active_only))
    return cur.fetchall()


def holiday_set(cur, entity_id, start: dt.date, end: dt.date) -> set[dt.date]:
    cur.execute("SELECT holiday_date FROM holidays WHERE entity_id = %s AND holiday_date BETWEEN %s AND %s", (entity_id, start, end))
    return {r["holiday_date"] for r in cur.fetchall()}


def balances(cur, entity_id, employee: dict, as_of: dt.date, policy: dict, types: list[dict] | None = None) -> list[dict]:
    """One row per leave type for a person, for the leave year `as_of` falls in."""
    start = hr.leave_year_start(as_of, policy["leave"]["leave_year"])
    end = hr.leave_year_end(start)
    types = types if types is not None else leave_types(cur, entity_id)
    cur.execute("SELECT leave_type_id, coalesce(sum(days), 0) AS d FROM leave_adjustments WHERE employee_id = %s AND leave_year_start = %s GROUP BY leave_type_id",
                (employee["id"], start))
    adj = {r["leave_type_id"]: r["d"] for r in cur.fetchall()}
    cur.execute("""SELECT leave_type_id, status, coalesce(sum(days - lop_days), 0) AS paid, coalesce(sum(lop_days), 0) AS lop FROM leave_requests
                    WHERE employee_id = %s AND status IN ('pending', 'approved') AND lower(dates) BETWEEN %s AND %s GROUP BY leave_type_id, status""",
                (employee["id"], start, end))
    used: dict = {}
    for r in cur.fetchall():
        used[(r["leave_type_id"], r["status"])] = (r["paid"], r["lop"])
    out = []
    for t in types:
        taken, lop_a = used.get((t["id"], "approved"), (ZERO, ZERO))
        pending, lop_p = used.get((t["id"], "pending"), (ZERO, ZERO))
        b = hr.balance(quota=t["annual_quota"], accrual=t["accrual"], joined=employee["date_of_joining"], year_start=start, as_of=as_of,
                       adjustments=adj.get(t["id"], ZERO), taken=taken, pending=pending)
        out.append({"leave_type_id": t["id"], "code": t["code"], "name": t["name"], "paid": t["paid"], "annual_quota": t["annual_quota"], "accrual": t["accrual"],
                    "unpaid_taken": lop_a + lop_p, **b})
    return out


def insert_structure(cur, entity_id, employee_id, effective_from: dt.date, ctc_annual: Decimal, components: list[dict] | None) -> tuple:
    """Close the open salary structure and add one from `effective_from`. With no components they are worked out from the CTC by the
    company's policy. Returns (structure id, whether the policy built it)."""
    from_policy = components is None
    if from_policy:
        pf_on, esi_on = registrations(cur, entity_id)
        try:
            # a salary entered today for an earlier date (existing staff being brought in) uses today's policy
            policy = current_policy(cur, entity_id, max(effective_from, dt.date.today()))
            components = hr.ctc_breakup(ctc_annual, policy["salary"], pf_registered=pf_on, esi_registered=esi_on)
        except hr.PolicyError as e:
            raise ApiError(400, "validation_failed", str(e), errors=[{"field": "ctc_annual", "message": str(e)}])
    cur.execute("SELECT id, lower(effective) AS lo FROM salary_structures WHERE employee_id = %s AND upper(effective) IS NULL FOR UPDATE", (employee_id,))
    prev = cur.fetchone()
    if prev:
        if effective_from <= prev["lo"]:
            raise ApiError(409, "conflict", f"A structure effective from {prev['lo']} already exists; the revision must start after it")
        cur.execute("UPDATE salary_structures SET effective = daterange(lower(effective), %s) WHERE id = %s", (effective_from, prev["id"]))
    cur.execute("""INSERT INTO salary_structures (employee_id, effective, ctc_annual, approved_by) VALUES (%s, daterange(%s, NULL), %s, app.current_user_id())
                   RETURNING id""", (employee_id, effective_from, ctc_annual))
    sid = cur.fetchone()["id"]
    for c in components:
        cur.execute("INSERT INTO salary_components (structure_id, code, monthly_amount, kind, in_wages, taxable) VALUES (%s, %s, %s, %s, %s, %s)",
                    (sid, c["code"], c["monthly_amount"], c["kind"], c["in_wages"], c["taxable"]))
    return sid, from_policy


def leave_lop_for_month(cur, entity_id, month: dt.date) -> dict:
    """Unpaid leave days per employee in a month, from the approved leave requests that touch it: {employee id: days}."""
    month_end = dt.date(month.year, month.month, pr.days_in(month))
    cur.execute("""SELECT r.employee_id, lower(r.dates) AS d_from, upper(r.dates) - 1 AS d_to, r.days, r.lop_days
                     FROM leave_requests r JOIN employees e ON e.id = r.employee_id
                    WHERE e.entity_id = %s AND r.status = 'approved' AND r.lop_days > 0 AND r.dates && daterange(%s, %s, '[]')""", (entity_id, month, month_end))
    rows = cur.fetchall()
    if not rows:
        return {}
    week_off = current_policy(cur, entity_id, month_end)["leave"]["week_off"]
    hol = holiday_set(cur, entity_id, min(r["d_from"] for r in rows), max(r["d_to"] for r in rows))
    out: dict = {}
    for r in rows:
        share = hr.lop_by_month(r["d_from"], r["d_to"], r["days"], r["lop_days"], week_off, hol).get(month, ZERO)
        if share:
            out[r["employee_id"]] = out.get(r["employee_id"], ZERO) + share
    return out


def pull_leave_lop(cur, entity_id, run: dict) -> list[dict]:
    """Write the month's unpaid leave into the run's inputs. A figure that came from leave is replaced when the leave changes; other
    figures a person typed for the month are left alone. A computed run goes back to open, because its payslips are now out of date."""
    lop = leave_lop_for_month(cur, entity_id, run["period_month"])
    cur.execute("SELECT inputs FROM payroll_run_inputs WHERE run_id = %s", (run["id"],))
    stored = (cur.fetchone() or {}).get("inputs") or {}
    before = json.dumps(stored, sort_keys=True)
    for emp_id in list(stored):
        if "lop_from_leave" in stored[emp_id] and emp_id not in {str(k) for k in lop}:
            stored[emp_id].pop("lop_from_leave")
            stored[emp_id]["lop_days"] = "0"
    for emp_id, days in lop.items():
        row = stored.setdefault(str(emp_id), {})
        row["lop_days"] = str(days)
        row["lop_from_leave"] = str(days)
    if json.dumps(stored, sort_keys=True) != before:
        cur.execute("INSERT INTO payroll_run_inputs (run_id, inputs) VALUES (%s, %s) ON CONFLICT (run_id) DO UPDATE SET inputs = EXCLUDED.inputs", (run["id"], json.dumps(stored)))
        if run["status"] == "computed":
            cur.execute("UPDATE payroll_runs SET status = 'inputs_open' WHERE id = %s", (run["id"],))
    return [{"employee_id": str(k), "days": v} for k, v in lop.items()]


def refresh_open_runs(cur, entity_id, d_from: dt.date, d_to: dt.date) -> str | None:
    """After leave changes: bring the open payroll runs of the months it touches up to date. Returns a note when a month's payroll is
    already approved, because that month's pay will not change by itself."""
    cur.execute("""SELECT id, period_month, status FROM payroll_runs WHERE entity_id = %s AND period_month BETWEEN %s AND %s ORDER BY period_month FOR UPDATE""",
                (entity_id, d_from.replace(day=1), d_to.replace(day=1)))
    closed = []
    for run in cur.fetchall():
        if run["status"] in OPEN_RUN:
            pull_leave_lop(cur, entity_id, run)
        else:
            closed.append(f"{run['period_month']:%B %Y}")
    return f"Payroll for {', '.join(closed)} is already approved: adjust the unpaid days in the next run." if closed else None
