"""A payslip as a PDF to hand to an employee: who and which month, the days paid, earnings beside deductions, the net pay in figures and
words, what the employer paid on top, and the year so far. Pure: it takes the payslip the API already loads and returns bytes.

The built-in PDF fonts have no rupee sign, so amounts are written "Rs." Money arrives as strings and is only formatted here.
"""
from __future__ import annotations

import io

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.platypus import SimpleDocTemplate, Spacer, Table, TableStyle

from . import tax
from .invoice_pdf import INK, LINE, _dmy, _p, _styles, inr, rs

MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"]


def month_label(iso: str) -> str:
    return f"{MONTHS[int(iso[5:7]) - 1]} {iso[:4]}"


def split_lines(lines: list[dict]) -> tuple[list[tuple[str, str]], list[tuple[str, str]], list[tuple[str, str]]]:
    """(earnings, deductions, employer contributions) as (label, amount) pairs, in the order they were computed."""
    pick = lambda kind: [(l["label"], l["amount"]) for l in lines if l["kind"] == kind]
    return pick("earning"), pick("deduction"), pick("employer_contribution")


def render_payslip(slip: dict, employer: dict) -> bytes:
    st = _styles()
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=16 * mm, rightMargin=16 * mm, topMargin=14 * mm, bottomMargin=14 * mm,
                            title=f"Payslip {slip['employee_code']} {slip['period_month'][:7]}", author=employer.get("legal_name") or "")
    width = A4[0] - 32 * mm
    earnings, deductions, employer_paid = split_lines(slip.get("lines") or [])
    total_ded = sum((tax.Decimal(a) for _, a in deductions), tax.Decimal(0))
    flow = []
    head = Table([[[_p(employer.get("legal_name") or "", st["name"]), _p(employer.get("registered_address") or "", st["small"])],
                   [_p("PAYSLIP" if slip.get("final") else "PAYSLIP (DRAFT)", st["title"]), _p(month_label(slip["period_month"]), st["right"])]]], colWidths=[width * 0.6, width * 0.4])
    head.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP")]))
    flow += [head, Spacer(1, 6 * mm)]

    who = [("Employee", slip["employee_name"]), ("Employee code", slip["employee_code"]), ("Designation", slip.get("designation") or "-"), ("Department", slip.get("department") or "-")]
    more = [("Date of joining", _dmy(slip.get("date_of_joining"))), ("PAN", f"XXXXXX{slip['pan_last4']}" if slip.get("pan_last4") else "-"), ("UAN", slip.get("uan") or "-"),
            ("Paid days / unpaid days", f"{slip['paid_days']:g} / {slip['lop_days']:g}")]
    rows = [[_p(a[0], st["small"]), _p(a[1], st["body"]), _p(b[0], st["small"]), _p(b[1], st["body"])] for a, b in zip(who, more)]
    info = Table(rows, colWidths=[width * 0.18, width * 0.32, width * 0.22, width * 0.28])
    info.setStyle(TableStyle([("BOX", (0, 0), (-1, -1), 0.5, LINE), ("VALIGN", (0, 0), (-1, -1), "TOP"), ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 3)]))
    flow += [info, Spacer(1, 5 * mm)]

    n = max(len(earnings), len(deductions), 1)
    body = [[_p("Earnings", st["head"]), _p("Amount (Rs.)", st["headr"]), _p("Deductions", st["head"]), _p("Amount (Rs.)", st["headr"])]]
    for i in range(n):
        e = earnings[i] if i < len(earnings) else ("", None)
        d = deductions[i] if i < len(deductions) else ("", None)
        body.append([_p(e[0], st["cell"]), _p(inr(e[1]) if e[1] is not None else "", st["cellr"]), _p(d[0], st["cell"]), _p(inr(d[1]) if d[1] is not None else "", st["cellr"])])
    body.append([_p("Gross pay", st["bold"]), _p(inr(slip["gross"]), st["cellr"]), _p("Total deductions", st["bold"]), _p(inr(total_ded), st["cellr"])])
    pay = Table(body, colWidths=[width * 0.3, width * 0.2, width * 0.3, width * 0.2], repeatRows=1)
    pay.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, 0), INK), ("GRID", (0, 0), (-1, -1), 0.4, LINE), ("LINEABOVE", (0, -1), (-1, -1), 0.8, INK),
                             ("VALIGN", (0, 0), (-1, -1), "MIDDLE"), ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 3)]))
    flow += [pay, Spacer(1, 4 * mm)]

    net = Table([[_p("Net pay", st["bold"]), _p(rs(slip["net_pay"]), st["title"])], [_p(tax.in_words(tax.Decimal(slip["net_pay"])), st["small"]), ""]], colWidths=[width * 0.5, width * 0.5])
    net.setStyle(TableStyle([("BOX", (0, 0), (-1, -1), 0.8, INK), ("SPAN", (0, 1), (1, 1)), ("VALIGN", (0, 0), (-1, -1), "MIDDLE"), ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#f4f5f8"))]))
    flow += [net, Spacer(1, 5 * mm)]

    if employer_paid:
        flow.append(_p("Paid by the employer on top of your pay: " + "; ".join(f"{label} {rs(amount)}" for label, amount in employer_paid), st["small"]))
        flow.append(Spacer(1, 2 * mm))
    ytd = slip.get("year_to_date")
    if ytd and slip.get("final"):
        flow.append(_p(f"This financial year so far: gross {rs(ytd['gross'])}, provident fund {rs(ytd['pf'])}, income tax deducted {rs(ytd['tds'])}, net paid {rs(ytd['net'])}.", st["small"]))
        flow.append(Spacer(1, 2 * mm))
    flow.append(_p("This is a computer-generated payslip and needs no signature." if slip.get("final")
                   else "DRAFT: this payroll has not been approved. The figures may change.", st["small"]))
    doc.build(flow)
    return buf.getvalue()
