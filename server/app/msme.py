"""MSME maths: interest on delayed payments (MSMED Act Sec 15 and 16) and the Udyam classification tests. Pure functions: no database, no clock.

The figures here (the 45-day and 15-day limits, three times the RBI bank rate, compounding with monthly rests, and the Udyam investment
and turnover limits) are a starter library. They must be reviewed by a CA or CS before anyone relies on them; the bank-rate history in
`ref_rbi_bank_rate` (migration 23) needs the same review and has to be kept up to date.
"""
from __future__ import annotations

import calendar
import datetime as dt
from decimal import ROUND_HALF_UP, Decimal

ZERO = Decimal(0)
CENT = Decimal("0.01")
MAX_CREDIT_DAYS = 45          # Sec 15: payment within the agreed time, never more than 45 days from acceptance
DEFAULT_CREDIT_DAYS = 15      # Sec 15: with no written agreement, 15 days from acceptance
RATE_MULTIPLE = Decimal(3)    # Sec 16: three times the bank rate notified by RBI


class MsmeError(ValueError):
    pass


def appointed_day(doc_date: dt.date, due_date: dt.date | None) -> dt.date:
    """The day payment was due under the Act. The invoice date stands in for the date of acceptance, and the agreed terms are capped at 45 days."""
    terms = (due_date - doc_date).days if due_date else 0
    days = DEFAULT_CREDIT_DAYS if terms <= 0 else min(terms, MAX_CREDIT_DAYS)
    return doc_date + dt.timedelta(days=days)


def bank_rate_on(rates: list[tuple[dt.date, Decimal]], on: dt.date) -> tuple[dt.date, Decimal]:
    """The RBI bank rate in force on a date: the latest entry that started on or before it."""
    best = None
    for start, pct in sorted(rates):
        if start <= on:
            best = (start, Decimal(pct))
    if best is None:
        raise MsmeError(f"There is no bank rate on record for {on}")
    return best


def _add_month(d: dt.date) -> dt.date:
    y, m = (d.year + d.month // 12, d.month % 12 + 1)
    return dt.date(y, m, min(d.day, calendar.monthrange(y, m)[1]))


def interest(principal: Decimal, appointed: dt.date, as_of: dt.date, rates: list[tuple[dt.date, Decimal]]) -> Decimal:
    """Compound interest with monthly rests at three times the bank rate, from the day after the appointed day up to and including `as_of`.

    Whole months compound: each month's interest is added to the balance before the next month starts, at the rate in force when that month began.
    A part month at the end earns its share of a month, by days."""
    if principal <= 0 or as_of <= appointed:
        return ZERO
    balance = Decimal(principal)
    start = appointed + dt.timedelta(days=1)
    end_excl = as_of + dt.timedelta(days=1)
    while True:
        nxt = _add_month(start)
        _, pct = bank_rate_on(rates, start)
        monthly = RATE_MULTIPLE * pct / Decimal(100) / Decimal(12)
        if nxt <= end_excl:
            balance += balance * monthly
            start = nxt
            if start == end_excl:
                break
            continue
        part = Decimal((end_excl - start).days) / Decimal((nxt - start).days)
        balance += balance * monthly * part
        break
    return (balance - Decimal(principal)).quantize(CENT, rounding=ROUND_HALF_UP)


# ---- Udyam classification
CRORE = Decimal(10_000_000)
LIMITS = {"micro": (Decimal("2.5") * CRORE, Decimal(10) * CRORE), "small": (Decimal(25) * CRORE, Decimal(100) * CRORE), "medium": (Decimal(125) * CRORE, Decimal(500) * CRORE)}
ORDER = ("micro", "small", "medium")


def classify(investment: Decimal | None, turnover: Decimal | None) -> str | None:
    """The smallest class whose investment and turnover limits are both met, or 'above medium'; None when either figure is unknown."""
    if investment is None or turnover is None:
        return None
    for name in ORDER:
        inv, turn = LIMITS[name]
        if investment <= inv and turnover <= turn:
            return name
    return "above_medium"


def tests(declared: str | None, investment: Decimal | None, turnover: Decimal | None) -> list[dict]:
    """How much of the declared class's two limits is used. Both must stay within their limit."""
    if declared not in LIMITS:
        return []
    inv_lim, turn_lim = LIMITS[declared]
    out = []
    for key, label, value, limit in (("investment", "Investment in plant and machinery", investment, inv_lim), ("turnover", "Annual turnover", turnover, turn_lim)):
        out.append({"key": key, "label": label, "value": str(value.quantize(CENT)) if value is not None else None, "limit": str(limit.quantize(CENT)),
                    "pct": float((value / limit * 100).quantize(Decimal("0.1"))) if value is not None else None})
    return out
