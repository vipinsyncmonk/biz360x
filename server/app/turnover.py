"""Aggregate annual turnover (AATO) by financial year, and what it decides: whether e-invoicing applies.

E-invoicing (CGST Rules r.48(4)) applies from the financial year after the first year in which aggregate turnover went above the limit, and it
stays on. So a company that crosses ₹5 Cr during FY 2026-27 issues its first IRN on 1 April 2027, not in the month it crossed. The test for a
document dated in year Y is "was turnover above the limit in any year before Y", counting from 2017-18.

The figure for a year is what the person declared (an `aato` fact dated in that year, for example 31 March) or, when nothing is declared,
the taxable value of the sales documents issued in it: invoices, export invoices, bills of supply and debit notes, less credit notes. That is
a working figure: the legal AATO is for the whole PAN across all its GSTINs, and adds exempt and other outward supplies the books may not
hold. A CA should confirm it, and a declared figure always wins over the books.
"""
from __future__ import annotations

import datetime as dt
from decimal import Decimal

EINVOICE_LIMIT = Decimal("50000000")           # ₹5 Cr
FIRST_YEAR = 2017                              # the test looks back to 2017-18
WARN_AT = Decimal("0.80")                      # tell the person at 80% of the limit in the current year


def fy_start_year(d: dt.date) -> int:
    return d.year if d.month >= 4 else d.year - 1


def fy_label(start_year: int) -> str:
    return f"{start_year}-{(start_year + 1) % 100:02d}"


def fy_end(start_year: int) -> dt.date:
    return dt.date(start_year + 1, 3, 31)


def einvoice_basis(used: dict[int, Decimal], year: int) -> dict:
    """What decides e-invoicing for a document dated in the financial year starting in `year`. `used` is the turnover of each year that is known."""
    earlier = {y: v for y, v in used.items() if FIRST_YEAR <= y < year}
    peak = max(earlier.values()) if earlier else None
    crossed = min((y for y, v in used.items() if y >= FIRST_YEAR and v > EINVOICE_LIMIT), default=None)
    return {"applies": peak is not None and peak > EINVOICE_LIMIT, "basis": peak,
            "crossed_year": crossed, "applies_from": None if crossed is None else dt.date(crossed + 1, 4, 1)}


def turnover_by_year(cur, entity_id) -> dict[int, dict]:
    """{start_year: {"books": Decimal | None, "declared": Decimal | None, "used": Decimal}} for every year that has either."""
    cur.execute("""SELECT app.fy_of(doc_date) AS fy,
                          sum(CASE WHEN doc_type = 'credit_note' THEN -taxable_total ELSE taxable_total END) AS total
                     FROM sales_documents
                    WHERE entity_id = %s AND status = 'issued'
                      AND doc_type IN ('tax_invoice', 'export_invoice', 'bill_of_supply', 'debit_note', 'credit_note')
                    GROUP BY 1""", (entity_id,))
    out: dict[int, dict] = {int(r["fy"][:4]): {"books": Decimal(r["total"]), "declared": None} for r in cur.fetchall()}
    cur.execute("""SELECT DISTINCT ON (app.fy_of(as_of)) app.fy_of(as_of) AS fy, value_num
                     FROM entity_facts WHERE entity_id = %s AND fact_key = 'aato' AND value_num IS NOT NULL
                    ORDER BY app.fy_of(as_of), as_of DESC, recorded_at DESC""", (entity_id,))
    for r in cur.fetchall():
        out.setdefault(int(r["fy"][:4]), {"books": None, "declared": None})["declared"] = Decimal(r["value_num"])
    for v in out.values():
        v["used"] = v["declared"] if v["declared"] is not None else (v["books"] or Decimal(0))
    return out


def basis_for(cur, entity_id, doc_date: dt.date) -> Decimal | None:
    """The turnover that decides e-invoicing for a document on `doc_date`: the highest of the years before it, or None when none is known."""
    years = turnover_by_year(cur, entity_id)
    return einvoice_basis({y: v["used"] for y, v in years.items()}, fy_start_year(doc_date))["basis"]


def status(cur, entity_id, today: dt.date | None = None) -> dict:
    today = today or dt.date.today()
    years = turnover_by_year(cur, entity_id)
    year = fy_start_year(today)
    b = einvoice_basis({y: v["used"] for y, v in years.items()}, year)
    now = years.get(year, {}).get("used", Decimal(0))
    pct = (now / EINVOICE_LIMIT * 100).quantize(Decimal("0.1")) if EINVOICE_LIMIT else Decimal(0)
    if b["applies"]:
        level = "applies"
    elif now > EINVOICE_LIMIT:
        level = "crossed"                                       # crossed this year: it starts on 1 April
    elif now >= EINVOICE_LIMIT * WARN_AT:
        level = "approaching"
    else:
        level = "clear"
    return {"limit": EINVOICE_LIMIT, "financial_year": fy_label(year), "applies_today": b["applies"], "applies_from": b["applies_from"],
            "crossed_in": None if b["crossed_year"] is None else fy_label(b["crossed_year"]), "level": level,
            "this_year": {"turnover": now, "percent_of_limit": pct},
            "years": [{"financial_year": fy_label(y), "books": v["books"], "declared": v["declared"], "used": v["used"], "above_limit": v["used"] > EINVOICE_LIMIT}
                      for y, v in sorted(years.items(), reverse=True)]}


def warn_before_start(cur, entity_id, today: dt.date | None = None) -> list[str]:
    """One open task a year when turnover reaches 80% of the limit (or has crossed it) before e-invoicing applies, so the first IRN is not a surprise."""
    s = status(cur, entity_id, today)
    if s["level"] not in ("approaching", "crossed"):
        return []
    year = fy_start_year(today or dt.date.today())
    start = dt.date(year + 1, 4, 1)
    cur.execute("SELECT 1 FROM tasks WHERE entity_id = %s AND source = 'engine' AND title LIKE %s", (entity_id, f"E-invoicing: FY {fy_label(year)}%"))
    if cur.fetchone():
        return []
    pct = s["this_year"]["percent_of_limit"]
    what = ("Turnover has crossed ₹5 Cr" if s["level"] == "crossed" else f"Turnover is at {pct}% of ₹5 Cr")
    cur.execute("INSERT INTO tasks (entity_id, title, source) VALUES (%s, %s, 'engine')",
                (entity_id, f"E-invoicing: FY {fy_label(year)} {what}. B2B and export invoices need an IRN from {start:%d %b %Y}. Get an e-invoicing provider ready."))
    return ["GST.EINVOICE"]
