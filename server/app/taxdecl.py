"""Year-end tax declarations and Form 16 Part B (roles phase 5, migration 42).

An employee declares a regime and deductions for a financial year; HR verifies each figure; payroll deducts TDS on the verified ones. Only
verified amounts ever reach a payslip. The section limits come from `tax_deduction_sections` (versioned data, a starter library for a CA to
review). Not modelled: HRA exemption, senior-citizen limits, employer NPS, 80G, other income, surcharge, income from a previous employer.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from decimal import Decimal as D

from . import payroll as pr

ZERO = D(0)
PF_SECTION = "80C"                                   # the employee's own PF counts under 80C


def fy_of(d: dt.date) -> str:
    start = d.year if d.month >= 4 else d.year - 1
    return f"{start}-{(start + 1) % 100:02d}"


def fy_bounds(fy: str) -> tuple[dt.date, dt.date]:
    start = int(fy[:4])
    return dt.date(start, 4, 1), dt.date(start + 1, 3, 31)


@dataclass
class Declared:
    """What payroll needs for one employee and one financial year."""
    regime: str | None                 # the regime they chose, when they have made a declaration
    claimed_80c: D = ZERO              # verified 80C items, before adding the employee's PF and applying the cap
    other: D = ZERO                    # verified items of the other sections, each already held to its own cap
    cap_80c: D = pr.SEC_80C_CAP


def totals_by_section(items: list[dict], caps: dict[str, D | None]) -> dict[str, D]:
    """The verified amount per section, each section held to its cap. 80C is left uncapped here because the employee's PF joins it first."""
    out: dict[str, D] = {}
    for it in items:
        if it["status"] != "verified" or it["verified_amount"] is None:
            continue
        out[it["section"]] = out.get(it["section"], ZERO) + D(it["verified_amount"])
    for code, amount in list(out.items()):
        cap = caps.get(code)
        if code != PF_SECTION and cap is not None:
            out[code] = min(amount, D(cap))
    return out


def declared_for_payroll(regime: str | None, status: str | None, items: list[dict], caps: dict[str, D | None]) -> Declared:
    """A submitted or verified declaration sets the regime; only a verified one adds deductions, and only under the old regime."""
    if status not in ("submitted", "verified"):
        return Declared(None)
    if status != "verified" or regime != "old":
        return Declared(regime)
    t = totals_by_section(items, caps)
    cap80 = caps.get(PF_SECTION)
    return Declared(regime, t.get(PF_SECTION, ZERO), sum((v for k, v in t.items() if k != PF_SECTION), ZERO), D(cap80) if cap80 is not None else D(10) ** 12)


def declared_view(items: list[dict], caps: dict[str, D | None], *, use_verified: bool) -> list[dict]:
    """Each section with what was declared, what is verified, and what the cap lets count (80C without the PF)."""
    by: dict[str, dict] = {}
    for it in items:
        s = by.setdefault(it["section"], {"section": it["section"], "declared": ZERO, "verified": ZERO})
        s["declared"] += D(it["declared_amount"])
        if it["status"] == "verified" and it["verified_amount"] is not None:
            s["verified"] += D(it["verified_amount"])
    out = []
    for s in by.values():
        cap = caps.get(s["section"])
        base = s["verified"] if use_verified else s["declared"]
        out.append({**s, "cap": cap, "counts": min(base, D(cap)) if cap is not None and s["section"] != PF_SECTION else base})
    return sorted(out, key=lambda r: r["section"])


def part_b(*, fy: str, regime: str, slips: list[dict], items: list[dict], caps: dict[str, D | None], verified: bool) -> dict:
    """Form 16 Part B for one employee: salary, deductions, tax on the total income, and what was deducted. Money as Decimals.

    `slips` are the year's approved payslips with `gross`, `reimbursements`, `pf_employee` and `tds`. Salary components marked not taxable are not
    separated out (reimbursements are), so the gross here can be higher than what TDS was worked on. Deductions count only when verified."""
    gross = sum((D(s["gross"]) - D(s["reimbursements"]) for s in slips), ZERO)
    std = min(pr.STANDARD_DEDUCTION[regime], gross)
    pf = sum((D(s["pf_employee"]) for s in slips), ZERO)
    lines: list[dict] = []
    via = ZERO
    if regime == "old":
        t = totals_by_section(items, caps) if verified else {}
        cap80 = caps.get(PF_SECTION)
        c80 = t.get(PF_SECTION, ZERO) + pf
        c80 = min(c80, D(cap80)) if cap80 is not None else c80
        if c80:
            lines.append({"section": PF_SECTION, "label": "Section 80C (including your provident fund)", "amount": c80})
        for code, amount in sorted(t.items()):
            if code != PF_SECTION and amount:
                lines.append({"section": code, "label": f"Section {code}", "amount": amount})
        via = sum((ln["amount"] for ln in lines), ZERO)
    taxable = max(gross - std - via, ZERO)
    tax = pr.annual_tax(taxable, regime)
    deducted = sum((D(s["tds"]) for s in slips), ZERO)
    return {"fy": fy, "assessment_year": f"{int(fy[:4]) + 1}-{(int(fy[:4]) + 2) % 100:02d}", "regime": regime, "months": len(slips), "gross_salary": pr.paise(gross),
            "standard_deduction": pr.paise(std), "chapter_vi_a": [{**ln, "amount": pr.paise(ln["amount"])} for ln in lines], "chapter_vi_a_total": pr.paise(via),
            "taxable_income": pr.paise(taxable), "tax_with_cess": tax, "tds_deducted": pr.paise(deducted), "difference": pr.paise(tax - deducted),
            "deductions_verified": verified}
