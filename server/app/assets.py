"""Fixed assets: category defaults and the depreciation schedule. Pure functions, no database, so they are unit-tested on their own.

Depreciation is the Companies Act kind (books), charged monthly:
  * straight line (slm): (cost - residual) / life in months;
  * written down value (wdv): the book value at the start of the financial year x the yearly rate / 12.
The first month is charged for the days from the date the asset was put to use, and the month of a sale for the days up to the sale.
The category lives are a starter library from Schedule II as commonly read; they must be reviewed by a CA, and a company may choose a
different life with a reason."""
from __future__ import annotations

import calendar
import datetime as dt
from decimal import Decimal

from .tax import r2

ZERO = Decimal("0")

# category -> (label, useful life in years under Schedule II, income-tax block rate in % for reference). The tax rate is only shown, not used.
CATEGORIES: dict[str, tuple[str, int, int]] = {
    "building": ("Building", 60, 10),
    "plant_machinery": ("Plant and machinery", 15, 15),
    "furniture": ("Furniture and fittings", 10, 10),
    "office_equipment": ("Office equipment", 5, 15),
    "computers": ("Computers and laptops", 3, 40),
    "servers_networks": ("Servers and networks", 6, 40),
    "vehicles": ("Motor vehicles", 8, 15),
    "electrical": ("Electrical installations", 10, 10),
    "software": ("Software and other intangibles", 5, 25),
    "other": ("Other", 5, 15),
}
DEFAULT_RESIDUAL_PCT = Decimal("5")          # Schedule II: residual value of up to 5% of cost


def default_life_months(category: str) -> int:
    return CATEGORIES[category][1] * 12


def month_start(d: dt.date) -> dt.date:
    return d.replace(day=1)


def next_month(m: dt.date) -> dt.date:
    return dt.date(m.year + (m.month == 12), m.month % 12 + 1, 1)


def month_end(m: dt.date) -> dt.date:
    return dt.date(m.year, m.month, calendar.monthrange(m.year, m.month)[1])


def fy_start(m: dt.date) -> dt.date:
    """The first day of the financial year (1 April) the month falls in."""
    return dt.date(m.year if m.month >= 4 else m.year - 1, 4, 1)


def first_month(a: dict) -> dt.date:
    """The first month this register charges: where the asset was put to use, or the month after the opening balance."""
    if a["booking"] == "opening":
        return next_month(month_start(a["opening_through"]))
    return month_start(a["put_to_use_on"])


def _fraction(a: dict, month: dt.date) -> Decimal:
    """The part of the month that is charged: from the put-to-use day in the first month, up to the sale day in the last."""
    dim = calendar.monthrange(month.year, month.month)[1]
    start_day, end_day = 1, dim
    if a["booking"] != "opening" and month_start(a["put_to_use_on"]) == month:
        start_day = a["put_to_use_on"].day
    if a.get("disposed_on") and month_start(a["disposed_on"]) == month:
        end_day = a["disposed_on"].day
    return Decimal(max(0, end_day - start_day + 1)) / Decimal(dim)


def schedule(a: dict, posted: dict[dt.date, Decimal], through: dt.date) -> list[dict]:
    """Every month from the first charged month to `through` (a month, any day), or to the month of the sale. Months already posted keep the
    amount that was posted; the rest are worked out. Each row: month, amount, book_value_after, posted."""
    last = month_start(through)
    if a.get("disposed_on"):
        last = min(last, month_start(a["disposed_on"]))
    cost, residual = a["cost"], a["residual_value"]
    book = cost - (a["opening_accumulated"] if a["booking"] == "opening" else ZERO)
    m = first_month(a)
    out: list[dict] = []
    fy_open = book
    fy_seen = fy_start(m)
    while m <= last:
        if fy_start(m) != fy_seen:
            fy_seen, fy_open = fy_start(m), book
        if m in posted:
            amt, was = posted[m], True
        else:
            f = _fraction(a, m)
            if a["method"] == "slm":
                raw = (cost - residual) / a["life_months"] * f
            else:
                raw = fy_open * a["rate_pct"] / Decimal(100) / Decimal(12) * f
            amt, was = max(ZERO, min(r2(raw), book - residual)), False
            if amt == 0:                                                  # down to the residual value: nothing more to charge
                break
        book -= amt
        out.append({"month": m, "amount": amt, "book_value_after": book, "posted": was})
        m = next_month(m)
    return out


def accumulated(a: dict, posted: dict[dt.date, Decimal]) -> Decimal:
    return (a["opening_accumulated"] if a["booking"] == "opening" else ZERO) + sum(posted.values(), ZERO)


def disposal_result(*, cost: Decimal, accumulated_before: Decimal, last_month_charge: Decimal, proceeds: Decimal) -> dict:
    """The numbers of a sale: book value at the sale, and the gain (positive) or loss (negative)."""
    book = cost - accumulated_before - last_month_charge
    return {"book_value": book, "gain": proceeds - book}
