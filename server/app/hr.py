"""HR calculations: a CTC split by the company's policy, leave earned and taken, working days, and the unpaid days a month's payroll
must deduct. Pure functions, no database.

The defaults below (basic at half the CTC, HRA at 40% of basic, the leave quotas, the checklist) are a **starter policy**: the company
sets its own, and a CA must review anything that drives a real payroll. The statutory figures (PF ceiling and rate, ESI limit and
rate) come from payroll.py and carry the same caveat.
"""
from __future__ import annotations

import datetime as dt
from decimal import ROUND_HALF_UP, Decimal

from . import payroll as pr

D = Decimal
ZERO = D(0)
HALF = D("0.5")
GRATUITY_PCT = D("4.81")                     # 15 days' wages for every 26 working days of a year: 15 / 26 / 12

DEFAULT_SALARY = {"basic_pct_of_ctc": "50", "hra_pct_of_basic": "40", "employer_pf_in_ctc": True, "pf_on_full_basic": False,
                  "employer_esi_in_ctc": True, "gratuity_in_ctc": False}
DEFAULT_LEAVE = {"leave_year": "financial", "week_off": [5, 6], "lop_when_exhausted": True}      # weekdays as Python counts them: Monday is 0, Sunday is 6
DEFAULT_PAYROLL = {"inputs_cutoff_day": 25, "pay_day": 30, "probation_months": 6, "notice_days": 30}
DEFAULT_CHECKLIST = [
    {"key": "offer", "title": "Offer letter signed", "required": True, "auto": None},
    {"key": "pan", "title": "PAN on file", "required": True, "auto": "pan"},
    {"key": "id_proof", "title": "Identity and address proof collected", "required": True, "auto": None},
    {"key": "bank", "title": "Bank account for salary", "required": True, "auto": "bank"},
    {"key": "salary", "title": "Salary set from the CTC", "required": True, "auto": "salary"},
    {"key": "pf_form", "title": "PF declaration (Form 11) and UAN", "required": False, "auto": None},
    {"key": "tax_regime", "title": "Tax regime chosen by the employee", "required": False, "auto": None},
    {"key": "handbook", "title": "Company policies acknowledged", "required": False, "auto": "handbook"},
]
DEFAULT_ONBOARDING = {"checklist": DEFAULT_CHECKLIST}
# code, name, days a year, accrual, paid, carry forward, encashable
DEFAULT_LEAVE_TYPES = [("EL", "Earned leave", D(15), "monthly", True, D(30), True), ("CL", "Casual leave", D(7), "yearly", True, ZERO, False),
                       ("SL", "Sick leave", D(7), "yearly", True, ZERO, False), ("LOP", "Leave without pay", None, "yearly", False, ZERO, False)]


def default_policy() -> dict:
    return {"salary": dict(DEFAULT_SALARY), "leave": dict(DEFAULT_LEAVE), "payroll": dict(DEFAULT_PAYROLL), "onboarding": {"checklist": [dict(c) for c in DEFAULT_CHECKLIST]}}


def r2(x) -> D:
    return D(x).quantize(D("0.01"), rounding=ROUND_HALF_UP)


def half_step(x) -> D:
    """Leave is counted in half days."""
    return (D(x) * 2).quantize(D(1), rounding=ROUND_HALF_UP) / 2


# ------------------------------------------------------------------ CTC -> salary structure
class PolicyError(ValueError):
    pass


def ctc_breakup(ctc_annual: D, salary: dict, *, pf_registered: bool, esi_registered: bool) -> list[dict]:
    """Split a yearly CTC into monthly components the way the policy says. The components add up to CTC / 12 to the paisa.

    Basic is a share of the CTC and HRA a share of basic. What the employer pays on top of the pay (PF, ESI, gratuity) is taken out of
    the CTC when the policy says the CTC includes it. The special allowance is what is left, so it absorbs every rounding."""
    monthly = r2(D(ctc_annual) / 12)
    basic = r2(monthly * D(salary["basic_pct_of_ctc"]) / 100)
    hra = r2(basic * D(salary["hra_pct_of_basic"]) / 100)
    pf = ZERO
    if pf_registered and salary.get("employer_pf_in_ctc"):
        base = basic if salary.get("pf_on_full_basic") else min(basic, pr.PF_WAGE_CEILING)
        pf = D(pr.rupee(base * pr.EPF_EMPLOYEE_PCT / 100))
    gratuity = r2(basic * GRATUITY_PCT / 100) if salary.get("gratuity_in_ctc") else ZERO
    esi = ZERO
    if esi_registered and salary.get("employer_esi_in_ctc"):
        gross = (monthly - pf - gratuity) / (1 + pr.ESI_EMPLOYER_PCT / 100)               # the pay on which the employer's ESI is itself charged
        if gross <= pr.ESI_GROSS_LIMIT:
            esi = D(pr.rupee_up(gross * pr.ESI_EMPLOYER_PCT / 100))
    special = monthly - basic - hra - pf - gratuity - esi
    if special < 0:
        raise PolicyError("The policy's basic pay, HRA and employer contributions come to more than this CTC. Lower the basic or HRA share.")
    out = [{"code": "BASIC", "monthly_amount": basic, "kind": "earning", "in_wages": True, "taxable": True},
           {"code": "HRA", "monthly_amount": hra, "kind": "earning", "in_wages": False, "taxable": True},
           {"code": "SPECIAL", "monthly_amount": special, "kind": "earning", "in_wages": False, "taxable": True},
           {"code": "EMPLOYER_PF", "monthly_amount": pf, "kind": "employer_contribution", "in_wages": False, "taxable": False},
           {"code": "EMPLOYER_ESI", "monthly_amount": esi, "kind": "employer_contribution", "in_wages": False, "taxable": False},
           {"code": "GRATUITY", "monthly_amount": gratuity, "kind": "employer_contribution", "in_wages": False, "taxable": False}]
    return [c for c in out if c["monthly_amount"] > 0 or c["code"] == "BASIC"]


def breakup_summary(components: list[dict]) -> dict:
    """Monthly gross, a rough take-home before income tax, and the wage share the Code on Wages looks at."""
    gross = sum((c["monthly_amount"] for c in components if c["kind"] == "earning"), ZERO)
    wages = sum((c["monthly_amount"] for c in components if c["kind"] == "earning" and c["in_wages"]), ZERO)
    employer = sum((c["monthly_amount"] for c in components if c["kind"] == "employer_contribution"), ZERO)
    has_pf = any(c["code"] == "EMPLOYER_PF" for c in components)
    has_esi = any(c["code"] == "EMPLOYER_ESI" for c in components)
    pf_emp = pr.pf(pr.statutory_wage(gross, wages), applicable=has_pf)["employee"]
    esi_emp = pr.esi(gross, applicable=has_esi)["employee"]
    return {"monthly_ctc": gross + employer, "monthly_gross": gross, "employer_contributions": employer, "employee_pf": D(pf_emp), "employee_esi": D(esi_emp),
            "take_home_before_tax": gross - D(pf_emp) - D(esi_emp), "wage_pct": pr.wage_pct(gross, wages)}


# ------------------------------------------------------------------ leave
def leave_year_start(d: dt.date, kind: str = "financial") -> dt.date:
    if kind == "calendar":
        return dt.date(d.year, 1, 1)
    return dt.date(d.year if d.month >= 4 else d.year - 1, 4, 1)


def leave_year_end(start: dt.date) -> dt.date:
    return dt.date(start.year + 1, start.month, 1) - dt.timedelta(days=1)


def _months_between(a: dt.date, b: dt.date) -> int:
    """Whole calendar months from the month of `a` up to and including the month of `b`."""
    return max((b.year - a.year) * 12 + b.month - a.month + 1, 0)


def entitlement(quota, accrual: str, joined: dt.date, year_start: dt.date, as_of: dt.date) -> D | None:
    """Days of a leave type a person has earned in the leave year by `as_of`. None for a type with no quota (unpaid leave).

    Yearly: the whole quota on the first day, or for someone who joined during the year the share for the months from the joining
    month on. Monthly: a twelfth of the quota for each month started, from the later of the year's start and the joining month.
    Both are rounded to half a day."""
    if quota is None:
        return None
    year_end = leave_year_end(year_start)
    if joined > year_end or as_of < year_start or as_of < joined:
        return ZERO
    first = max(year_start, joined.replace(day=1))
    if accrual == "monthly":
        return half_step(D(quota) * _months_between(first, min(as_of, year_end)) / 12)
    return half_step(D(quota) * _months_between(first, year_end) / 12)


def working_days(start: dt.date, end: dt.date, week_off: list[int], holidays: set[dt.date]) -> list[dt.date]:
    """The days of a leave that count: not the weekly off, not a company holiday."""
    out, d = [], start
    while d <= end:
        if d.weekday() not in week_off and d not in holidays:
            out.append(d)
        d += dt.timedelta(days=1)
    return out


def leave_days(start: dt.date, end: dt.date, half_day: bool, week_off: list[int], holidays: set[dt.date]) -> D:
    days = working_days(start, end, week_off, holidays)
    if half_day:
        if start != end:
            raise PolicyError("A half day is a single date")
        return HALF if days else ZERO
    return D(len(days))


def split_paid_unpaid(days: D, available: D | None, *, paid_type: bool, lop_when_exhausted: bool) -> tuple[D, D]:
    """(paid days, unpaid days) of a request. An unpaid leave type is unpaid throughout; a paid one is paid up to the balance and, when
    the policy allows it, the rest is loss of pay. Raises when the balance is short and the policy does not allow that."""
    if not paid_type:
        return ZERO, days
    have = max(available or ZERO, ZERO)
    if days <= have:
        return days, ZERO
    if not lop_when_exhausted:
        raise PolicyError(f"Only {have.normalize():f} days are left")
    return have, days - have


def lop_by_month(start: dt.date, end: dt.date, days: D, lop_days: D, week_off: list[int], holidays: set[dt.date]) -> dict[dt.date, D]:
    """Which months the unpaid days of a request fall in: {first of month: days}. The unpaid days are the last ones of the request,
    because the balance is used up from the first day."""
    if lop_days <= 0:
        return {}
    dates = working_days(start, end, week_off, holidays)
    if not dates:
        return {}
    weights = [HALF] if days == HALF else [D(1)] * len(dates)
    out: dict[dt.date, D] = {}
    left = D(lop_days)
    for d, w in zip(reversed(dates), reversed(weights)):
        if left <= 0:
            break
        take = min(w, left)
        m = d.replace(day=1)
        out[m] = out.get(m, ZERO) + take
        left -= take
    return out


def balance(*, quota, accrual: str, joined: dt.date, year_start: dt.date, as_of: dt.date, adjustments: D = ZERO, taken: D = ZERO, pending: D = ZERO) -> dict:
    """Where a person stands on one leave type: earned so far, added by hand, taken, waiting for a decision, and what can still be taken."""
    earned = entitlement(quota, accrual, joined, year_start, as_of)
    if earned is None:
        return {"earned": None, "adjustments": ZERO, "taken": taken, "pending": pending, "available": None}
    return {"earned": earned, "adjustments": adjustments, "taken": taken, "pending": pending, "available": earned + adjustments - taken - pending}


# ------------------------------------------------------------------ onboarding
def checklist_state(tasks: list[dict], *, has_pan: bool, has_bank: bool, has_salary: bool, handbook_done: bool) -> tuple[list[dict], list[str]]:
    """Each task with whether it is done, and the titles of the required ones still open. Tasks marked `auto` are read from the record."""
    facts = {"pan": has_pan, "bank": has_bank, "salary": has_salary, "handbook": handbook_done}
    out, open_required = [], []
    for t in tasks:
        done = facts[t["auto"]] if t.get("auto") else t.get("done_at") is not None
        out.append({"key": t["key"], "title": t["title"], "required": t["required"], "auto": t.get("auto"), "done": bool(done)})
        if t["required"] and not done:
            open_required.append(t["title"])
    return out, open_required


def next_employee_code(codes: list[str], prefix: str = "EMP-") -> str:
    """The next code after the highest numbered one with the prefix: EMP-0007 after EMP-6 or EMP-0006."""
    top = 0
    for c in codes:
        if c.upper().startswith(prefix) and c[len(prefix):].isdigit():
            top = max(top, int(c[len(prefix):]))
    return f"{prefix}{top + 1:04d}"
