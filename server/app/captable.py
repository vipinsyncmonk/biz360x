"""Cap table, ESOP vesting and funding-round steps. Pure functions: no database, no clock (the caller passes `today`).

Share counts are whole numbers and money is Decimal; nothing here uses floats. The statutory timelines mentioned in the round
steps (Companies Act Sec 42 / 62, MGT-14, PAS-3, FEMA FC-GPR) are a starter library and need review by a CA or CS.
"""
from __future__ import annotations

import calendar
import datetime as dt
from decimal import ROUND_DOWN, ROUND_HALF_UP, Decimal

ZERO = Decimal(0)


class CapTableError(ValueError):
    pass


def percent(part: Decimal, whole: Decimal) -> Decimal:
    return (Decimal(100) * part / whole).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP) if whole else ZERO


def post_round(rows: list[dict], target: Decimal, pre_money: Decimal, conversion_ratio: Decimal, label: str, class_name: str) -> dict:
    """The fully diluted table after a round is issued at the pre-money valuation.

    price per share = pre-money / fully diluted shares today; new shares = target / price, rounded down to whole shares.
    Each row has `fd_shares` (Decimal); returns the new rows plus what the round does."""
    total = sum((Decimal(r["fd_shares"]) for r in rows), ZERO)
    if total <= 0:
        raise CapTableError("There is no cap table yet: record the existing allotments first")
    if target <= 0 or pre_money <= 0:
        raise CapTableError("The round needs a target amount and a pre-money valuation")
    price = (pre_money / total).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)
    if price <= 0:
        raise CapTableError("The valuation is too low for the number of shares")
    new_shares = int((target / price).to_integral_value(rounding=ROUND_DOWN))
    fd_new = (Decimal(new_shares) * conversion_ratio).to_integral_value(rounding=ROUND_DOWN)
    new_total = total + fd_new
    out = [{**r, "fd_shares": Decimal(r["fd_shares"]), "fd_pct": percent(Decimal(r["fd_shares"]), new_total)} for r in rows]
    out.append({"holder": f"New investors · {label}", "class_name": class_name, "fd_shares": fd_new, "fd_pct": percent(fd_new, new_total)})
    return {"rows": out, "total_fd_shares": new_total, "new_shares": new_shares, "price_per_share": price, "post_money": pre_money + new_shares * price}


def _add_months(d: dt.date, n: int) -> dt.date:
    m = d.month - 1 + n
    y, m = d.year + m // 12, m % 12 + 1
    return dt.date(y, m, min(d.day, calendar.monthrange(y, m)[1]))


FREQ_MONTHS = {"monthly": 1, "quarterly": 3, "annual": 12}


def vested_on(options: int, granted_on: dt.date, cliff_months: int, vesting_months: int, frequency: str, on: dt.date) -> int:
    """Options vested by `on`. Nothing vests before the cliff; from then options vest at the end of each period (monthly, quarterly or
    yearly) in proportion to the months passed, rounded down, and all of them by the end of `vesting_months`."""
    step = FREQ_MONTHS[frequency]
    if options <= 0 or vesting_months <= 0 or on < _add_months(granted_on, cliff_months):
        return 0
    months = 0
    while months + step <= vesting_months and _add_months(granted_on, months + step) <= on:
        months += step
    if _add_months(granted_on, vesting_months) <= on:
        return options
    return options * months // vesting_months


def vesting(options: int, granted_on: dt.date, cliff_months: int, vesting_months: int, frequency: str, today: dt.date) -> tuple[int, dict | None]:
    """(options vested by `today`, the next vesting event as {"date", "options"} or None when everything has vested)."""
    step = FREQ_MONTHS[frequency]
    now = vested_on(options, granted_on, cliff_months, vesting_months, frequency, today)
    if now >= options:
        return now, None
    candidates = {_add_months(granted_on, cliff_months), _add_months(granted_on, vesting_months)}
    candidates.update(_add_months(granted_on, m) for m in range(step, vesting_months + 1, step))
    for when in sorted(c for c in candidates if c > today):
        later = vested_on(options, granted_on, cliff_months, vesting_months, frequency, when)
        if later > now:
            return now, {"date": when, "options": later - now}
    return now, None


ROUND_ORDER = ["planning", "term_sheet", "diligence", "documentation", "allotment", "closed", "abandoned"]


def round_steps(rnd: dict, has_foreign_investor: bool, allotted: bool) -> list[dict]:
    """The six steps of a private placement with their state, from the round's recorded status and dates.

    `rnd` needs status, term_sheet_signed_on, shareholder_approval_on, closed_on. The first step not done is in progress; the rest are next."""
    idx = ROUND_ORDER.index(rnd["status"]) if rnd["status"] in ROUND_ORDER else 0
    closed = rnd["status"] == "closed"

    def day(d):
        return f"{d.day} {calendar.month_abbr[d.month]}" if d else None

    spec = [
        ("term_sheet", "Term sheet", bool(rnd.get("term_sheet_signed_on")) or idx >= 2,
         f"Signed {day(rnd['term_sheet_signed_on'])}" if rnd.get("term_sheet_signed_on") else "Not signed yet"),
        ("diligence", "Due diligence", idx >= 3 or closed, "Data room and investor questions"),
        ("approvals", "Board and shareholder approvals", bool(rnd.get("shareholder_approval_on")) or closed,
         f"Approved {day(rnd['shareholder_approval_on'])}" if rnd.get("shareholder_approval_on") else "Special resolution under Sec 42 and 62; file MGT-14 within 30 days"),
        ("documents", "SSA, SHA and PAS-4", idx >= 4 or closed, "Offer letter to identified investors"),
        ("allotment", "Allotment and PAS-3", allotted or closed, "Allot within 60 days of receiving the money; file the return in 15 days"),
    ]
    if has_foreign_investor:
        spec.append(("fcgpr", "FC-GPR on FIRMS", closed, "Within 30 days of allotment to a foreign investor"))
    steps, seen_open = [], False
    for key, title, done, detail in spec:
        if done:
            status = "done"
        elif not seen_open:
            status, seen_open = "in_progress", True
        else:
            status = "next"
        steps.append({"key": key, "title": title, "status": status, "detail": detail, "obligation_id": None})
    return steps


def benefit_reason(clauses: list[dict], eligible: bool) -> str:
    """Why a scheme does or does not fit, from the engine's evaluated tests."""
    labels = {"age_years": "Company age in years", "turnover_max_any_year": "Highest yearly turnover", "dpiit_recognised": "DPIIT recognised", "udyam_class": "Udyam class",
              "udyam_registered": "Registered on Udyam", "is_new_unit": "New unit"}
    ops = {"=": "is", "!=": "is not", ">=": "at least", ">": "more than", "<=": "at most", "<": "less than", "in": "one of"}

    def val(v):
        return "yes" if v is True else "no" if v is False else ", ".join(map(str, v)) if isinstance(v, list) else str(v)

    parts = []
    for c in clauses:
        label = labels.get(c["fact"], c["fact"].replace("_", " ").capitalize())
        want = f"{label} {ops.get(c['op'], c['op'])} {val(c['value'])}"
        if c.get("missing"):
            parts.append(f"{want}: not known yet")
        elif c["result"]:
            parts.append(f"{want}: met")
        else:
            parts.append(f"{want}: not met (yours: {val(c['actual'])})")
    return ("You qualify. " if eligible else "Not yet. ") + "; ".join(parts) + "."
