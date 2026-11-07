"""Payroll calculations: wage-code 50% rule, PF, ESI, professional tax, TDS on salary, gratuity and bonus.

Pure functions, no database. Every rate, ceiling and slab below is a **starter library**: a CA/CS must review it
before it drives a real payroll (tracked in the launch-readiness milestone). Tax figures are for the new and old
regimes as announced for FY 2025-26 and are kept as data tables so a change is a data edit, not a rewrite.
"""
from __future__ import annotations

import calendar
import datetime as dt
from dataclasses import dataclass, field
from decimal import ROUND_CEILING, ROUND_HALF_UP, Decimal

D = Decimal
ZERO = D(0)

PF_WAGE_CEILING = D(15000)
EPF_EMPLOYEE_PCT = D("12")
EPS_PCT = D("8.33")
ESI_GROSS_LIMIT = D(21000)
ESI_EMPLOYEE_PCT = D("0.75")
ESI_EMPLOYER_PCT = D("3.25")
BONUS_ELIGIBILITY_WAGE = D(21000)
BONUS_CALC_CEILING = D(7000)
BONUS_MIN_PCT = D("8.33")
GRATUITY_EMPLOYEE_THRESHOLD = 10
POSH_EMPLOYEE_THRESHOLD = 10


def rupee(x: D) -> D:
    """Round half up to a whole rupee (PF, TDS)."""
    return D(x).quantize(D(1), rounding=ROUND_HALF_UP)


def rupee_up(x: D) -> D:
    """ESI contributions are rounded up to the next rupee."""
    return D(x).quantize(D(1), rounding=ROUND_CEILING)


def paise(x: D) -> D:
    return D(x).quantize(D("0.01"), rounding=ROUND_HALF_UP)


# ----------------------------------------------------------------------- wage-code 50% rule
def statutory_wage(gross_earnings: D, wages_in: D) -> D:
    """Code on Wages: wages are at least 50% of total remuneration; allowances above half are added back."""
    return max(D(gross_earnings) * D("0.5"), D(wages_in))


def wage_pct(gross_earnings: D, wages_in: D) -> D | None:
    return None if not gross_earnings else (D(wages_in) * 100 / D(gross_earnings)).quantize(D("0.1"), rounding=ROUND_HALF_UP)


def pf_wage(stat_wage: D) -> D:
    return min(D(stat_wage), PF_WAGE_CEILING)


# --------------------------------------------------------------------------------------- PF
def pf(stat_wage: D, *, applicable: bool = True) -> dict:
    """Employee 12% of the capped wage; employer 12% split into EPS (8.33% of the capped wage) and EPF (the rest)."""
    base = pf_wage(stat_wage) if applicable else ZERO
    emp = rupee(base * EPF_EMPLOYEE_PCT / 100)
    eps = rupee(base * EPS_PCT / 100)
    return {"pf_wage": base, "employee": emp, "employer_eps": eps, "employer_epf": emp - eps}


# -------------------------------------------------------------------------------------- ESI
def esi(gross: D, *, applicable: bool = True) -> dict:
    """Covered while monthly gross is at most 21,000. 0.75% employee + 3.25% employer, each rounded up."""
    covered = applicable and D(gross) <= ESI_GROSS_LIMIT
    if not covered:
        return {"applicable": False, "employee": ZERO, "employer": ZERO}
    return {"applicable": True, "employee": rupee_up(D(gross) * ESI_EMPLOYEE_PCT / 100), "employer": rupee_up(D(gross) * ESI_EMPLOYER_PCT / 100)}


# -------------------------------------------------------------------- professional tax (state slabs)
# state gst code -> list of (gross_from_inclusive, monthly_tax, february_tax)
PT_SLABS = {
    "29": [(D(25000), D(200), D(300))],                                            # Karnataka
    "27": [(D(7501), D(175), D(175)), (D(10001), D(200), D(300))],                 # Maharashtra (the rate for men; women are exempt up to 25,000)
}


def professional_tax(state: str, gross: D, month: int) -> tuple[D, bool]:
    """Returns (amount, supported). A state with no slabs here returns (0, False) so the payslip can say so."""
    slabs = PT_SLABS.get(state)
    if slabs is None:
        return ZERO, False
    tax = ZERO
    for floor, monthly, feb in slabs:
        if D(gross) >= floor:
            tax = feb if month == 2 else monthly
    return tax, True


# ---------------------------------------------------------------------------- TDS on salary (s.192)
NEW_SLABS = [(D(400000), D(0)), (D(800000), D(5)), (D(1200000), D(10)), (D(1600000), D(15)), (D(2000000), D(20)), (D(2400000), D(25)), (None, D(30))]
OLD_SLABS = [(D(250000), D(0)), (D(500000), D(5)), (D(1000000), D(20)), (None, D(30))]
STANDARD_DEDUCTION = {"new": D(75000), "old": D(50000)}
REBATE = {"new": (D(1200000), D(60000)), "old": (D(500000), D(12500))}        # (income limit, maximum rebate) under s.87A
CESS_PCT = D("4")
SEC_80C_CAP = D(150000)


def slab_tax(income: D, slabs) -> D:
    tax, lower = ZERO, ZERO
    for upper, pct in slabs:
        top = D(income) if upper is None else min(D(income), upper)
        if top > lower:
            tax += (top - lower) * pct / 100
        if upper is None or D(income) <= upper:
            break
        lower = upper
    return tax


def annual_tax(taxable_income: D, regime: str) -> D:
    """Tax plus 4% cess on taxable income, after the s.87A rebate and its marginal relief. Surcharge (income above 50 lakh) is not modelled."""
    income = max(D(taxable_income), ZERO)
    tax = slab_tax(income, NEW_SLABS if regime == "new" else OLD_SLABS)
    limit, cap = REBATE[regime]
    if income <= limit:
        tax = max(tax - cap, ZERO)
    elif regime == "new":
        tax = min(tax, income - limit)                                  # marginal relief just above the rebate limit
    return paise(tax * (1 + CESS_PCT / 100))


@dataclass
class TdsInput:
    regime: str
    gross_this_month: D                       # taxable earnings this month (excludes reimbursements)
    months_in_fy_before: int                  # months of the FY that ended before this payslip month
    prior_gross: D = ZERO                     # taxable earnings in earlier payslips of this FY
    prior_payslips: int = 0
    tds_so_far: D = ZERO
    employee_pf_this_month: D = ZERO


def tds_on_salary(i: TdsInput) -> D:
    """Spread the projected annual tax over the months left. Months before the employee's first payslip in this system are
    assumed to have paid the same salary with no tax deducted (there are no year-to-date opening balances yet), so the
    first payslip catches up over the remaining months."""
    remaining = 12 - i.months_in_fy_before
    if remaining <= 0:
        return ZERO
    unknown = i.months_in_fy_before - i.prior_payslips
    annual_income = i.prior_gross + i.gross_this_month * (remaining + unknown)
    deduction = STANDARD_DEDUCTION[i.regime]
    if i.regime == "old":                                               # no declarations yet: only the employee's PF counts under 80C
        deduction += min(i.employee_pf_this_month * 12, SEC_80C_CAP)
    tax = annual_tax(annual_income - deduction, i.regime)
    return rupee(max(tax - i.tds_so_far, ZERO) / remaining)


def months_before_in_fy(month_start: dt.date) -> int:
    """Months of the Indian FY (April-March) that ended before `month_start`."""
    return (month_start.month - 4) % 12


# ------------------------------------------------------------------------------ one payslip
@dataclass
class Component:
    code: str
    monthly: D
    kind: str                                 # earning | employer_contribution | reimbursement
    in_wages: bool
    taxable: bool = True


@dataclass
class Inputs:
    lop_days: D = ZERO
    overtime: D = ZERO
    bonus: D = ZERO
    reimbursements: D = ZERO
    other_deductions: D = ZERO


@dataclass
class Payslip:
    paid_days: D
    lop_days: D
    gross: D
    wages: D
    pf_wage: D
    pf_employee: D
    pf_employer_epf: D
    pf_employer_eps: D
    esi_applicable: bool
    esi_employee: D
    esi_employer: D
    professional_tax: D
    tds: D
    other_deductions: D
    net_pay: D
    flags: list[str] = field(default_factory=list)
    lines: list[tuple[str, str, str, D]] = field(default_factory=list)          # (code, label, kind, amount)


def days_in(month_start: dt.date) -> int:
    return calendar.monthrange(month_start.year, month_start.month)[1]


def service_days(month_start: dt.date, joined: dt.date, left: dt.date | None) -> int:
    end = dt.date(month_start.year, month_start.month, days_in(month_start))
    first = max(month_start, joined)
    last = min(end, left) if left else end
    return max((last - first).days + 1, 0)


def compute_payslip(*, month_start: dt.date, joined: dt.date, left: dt.date | None, work_state: str, regime: str, components: list[Component],
                    inputs: Inputs, pf_applicable: bool, esi_applicable: bool, tds_history: dict | None = None) -> Payslip:
    month_days = days_in(month_start)
    employed = service_days(month_start, joined, left)
    if employed == 0:
        raise ValueError("employee was not in service in this month")
    lop = min(D(inputs.lop_days), D(employed))
    paid_days = D(employed) - lop
    factor = paid_days / D(month_days)
    flags: list[str] = []
    lines: list[tuple[str, str, str, D]] = []
    earn = [c for c in components if c.kind == "earning"]
    prorated = {c.code: paise(c.monthly * factor) for c in earn}
    base_gross = sum(prorated.values(), ZERO)
    wages_in = sum((prorated[c.code] for c in earn if c.in_wages), ZERO)
    reimb = sum((paise(c.monthly * factor) for c in components if c.kind == "reimbursement"), ZERO) + D(inputs.reimbursements)
    for c in earn:
        lines.append((c.code, c.code.title(), "earning", prorated[c.code]))
    for code, label, amt in (("OVERTIME", "Overtime", inputs.overtime), ("BONUS", "Bonus", inputs.bonus), ("REIMBURSEMENT", "Reimbursements", reimb)):
        if amt:
            lines.append((code, label, "earning", D(amt)))
    gross = base_gross + D(inputs.overtime) + D(inputs.bonus) + reimb
    non_taxable = sum((prorated[c.code] for c in earn if not c.taxable), ZERO)
    taxable_gross = base_gross - non_taxable + D(inputs.overtime) + D(inputs.bonus)
    stat = statutory_wage(base_gross, wages_in)
    pct = wage_pct(base_gross, wages_in)
    if pct is not None and pct < 50:
        flags.append("wage_below_50pct")
    p = pf(stat, applicable=pf_applicable)
    e = esi(gross, applicable=esi_applicable)
    pt, supported = professional_tax(work_state, gross, month_start.month)
    if not supported:
        flags.append("pt_state_not_configured")
    h = tds_history or {}
    tds = tds_on_salary(TdsInput(regime, taxable_gross, months_before_in_fy(month_start), D(h.get("prior_gross", 0)), int(h.get("prior_payslips", 0)),
                                 D(h.get("tds_so_far", 0)), p["employee"]))
    other = D(inputs.other_deductions)
    net = gross - p["employee"] - e["employee"] - pt - tds - other
    if net < 0:
        flags.append("negative_net_pay")
    for code, label, amt in (("PF_EMPLOYEE", "Provident fund (employee)", p["employee"]), ("ESI_EMPLOYEE", "ESI (employee)", e["employee"]),
                             ("PT", "Professional tax", pt), ("TDS", "TDS on salary", tds), ("OTHER_DED", "Other deductions", other)):
        if amt:
            lines.append((code, label, "deduction", D(amt)))
    for code, label, amt in (("PF_EMPLOYER_EPF", "Employer EPF", p["employer_epf"]), ("PF_EMPLOYER_EPS", "Employer EPS", p["employer_eps"]),
                             ("ESI_EMPLOYER", "Employer ESI", e["employer"])):
        if amt:
            lines.append((code, label, "employer_contribution", D(amt)))
    return Payslip(paid_days, lop, paise(gross), paise(stat), p["pf_wage"], p["employee"], p["employer_epf"], p["employer_eps"], e["applicable"],
                   e["employee"], e["employer"], pt, tds, paise(other), paise(net), flags, lines)


# ------------------------------------------------------------------------ gratuity and bonus
def completed_years(joined: dt.date, as_of: dt.date) -> int:
    """Gratuity counts a part of a year over six months as a full year."""
    months = (as_of.year - joined.year) * 12 + as_of.month - joined.month - (1 if as_of.day < joined.day else 0)
    return months // 12 + (1 if months % 12 >= 6 else 0)


def gratuity_provision(wages_monthly: D, joined: dt.date, as_of: dt.date) -> D:
    """15 days' wages per completed year: wages / 26 x 15 x years."""
    return paise(D(wages_monthly) / 26 * 15 * completed_years(joined, as_of))


def bonus_for_year(wages_monthly: D, months_worked: int, rate_pct: D = BONUS_MIN_PCT) -> D:
    """Payment of Bonus Act: eligible up to 21,000 wages; computed on the lower of wages and the 7,000 ceiling."""
    if D(wages_monthly) > BONUS_ELIGIBILITY_WAGE or months_worked <= 0:
        return ZERO
    return paise(min(D(wages_monthly), BONUS_CALC_CEILING) * min(months_worked, 12) * D(rate_pct) / 100)
