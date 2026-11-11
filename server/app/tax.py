"""GST computation and pre-send checks for sales documents.

Pure functions: the same code backs POST /sales-documents/preview (live
editor) and the server-side totals stored on create, so the preview can
never disagree with what is saved.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal

TWO = Decimal("0.01")
EINVOICE_AATO = Decimal("50000000")       # ₹5 Cr
EWAY_THRESHOLD = Decimal("50000")         # consignment value
TDS_194Q_RATE = Decimal("0.001")
MSME_MAX_DAYS = 45


def r2(x: Decimal) -> Decimal:
    return x.quantize(TWO, rounding=ROUND_HALF_UP)


@dataclass
class Line:
    description: str
    hsn_sac: str
    qty: Decimal
    rate: Decimal
    gst_rate: Decimal
    discount: Decimal = Decimal("0")
    is_service: bool = False

    @property
    def taxable(self) -> Decimal:
        return r2(self.qty * self.rate - self.discount)


@dataclass
class Context:
    doc_type: str
    supplier_state: str
    place_of_supply: str
    doc_date: dt.date
    buyer_gstin: str | None
    aato: Decimal | None
    deducts_194q: bool
    payment_terms_days: int
    supplier_is_mse: bool = False
    original_doc_date: dt.date | None = None
    gst_adjusted: bool = True
    is_export: bool = False             # the customer is outside India
    zero_rated: bool = False            # supplied under a Letter of Undertaking: no tax is charged
    lut: str = "none"                   # the LUT today: valid, expiring, expired or none
    currency: str = "INR"
    exchange_rate: Decimal | None = None
    country: str | None = None
    supply_type: str = ""               # b2b, export_under_lut, export_with_tax ...


@dataclass
class Result:
    lines: list = field(default_factory=list)
    is_inter_state: bool = False
    taxable_total: Decimal = Decimal("0")
    cgst_total: Decimal = Decimal("0")
    sgst_total: Decimal = Decimal("0")
    igst_total: Decimal = Decimal("0")
    round_off: Decimal = Decimal("0")
    grand_total: Decimal = Decimal("0")
    tds_expected: Decimal = Decimal("0")
    due_date: dt.date | None = None
    checks: list = field(default_factory=list)


def compute(lines: list[Line], ctx: Context) -> Result:
    res = Result(is_inter_state=ctx.supplier_state != ctx.place_of_supply)
    for i, ln in enumerate(lines, start=1):
        taxable = ln.taxable
        tax = r2(taxable * ln.gst_rate / 100) if ctx.gst_adjusted and not ctx.zero_rated else Decimal("0")
        if res.is_inter_state:
            cg = sg = Decimal("0"); ig = tax
        else:
            # split the line's tax exactly in half so CGST always equals SGST
            cg = sg = r2(tax / 2); ig = Decimal("0")
        res.lines.append({"line_no": i, "description": ln.description, "hsn_sac": ln.hsn_sac, "qty": str(ln.qty),
                          "rate": f"{ln.rate:.2f}", "discount": f"{ln.discount:.2f}", "gst_rate": float(ln.gst_rate),
                          "taxable_value": taxable, "cgst_amount": cg, "sgst_amount": sg, "igst_amount": ig, "cess_amount": Decimal("0")})
        res.taxable_total += taxable; res.cgst_total += cg; res.sgst_total += sg; res.igst_total += ig
    exact = res.taxable_total + res.cgst_total + res.sgst_total + res.igst_total
    res.grand_total = exact.quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    res.round_off = res.grand_total - exact
    res.due_date = ctx.doc_date + dt.timedelta(days=ctx.payment_terms_days)
    if ctx.deducts_194q and ctx.doc_type == "tax_invoice":
        res.tds_expected = r2(res.taxable_total * TDS_194Q_RATE)
    res.checks = checks(lines, ctx, res)
    return res


def check(code, level, title, detail):
    return {"code": code, "level": level, "title": title, "detail": detail}


def checks(lines: list[Line], ctx: Context, res: Result) -> list[dict]:
    out = []
    out.append(check("supply_type", "ok", "Inter-state supply." if res.is_inter_state else "Intra-state supply.",
                     ("IGST" if res.is_inter_state else "CGST + SGST") +
                     f" — supplier state {ctx.supplier_state}, place of supply {ctx.place_of_supply}."))
    if ctx.doc_type in ("tax_invoice", "credit_note", "debit_note") and not ctx.buyer_gstin and not ctx.is_export:
        out.append(check("b2b_needs_gstin", "block", "Buyer GSTIN missing.", "Choose a registered GSTIN or issue a B2C invoice."))
    if ctx.is_export:
        out.extend(export_checks(lines, ctx, res))
    if ctx.aato is not None and ctx.aato > EINVOICE_AATO and (ctx.buyer_gstin or ctx.is_export) and ctx.gst_adjusted:
        out.append(check("einvoice_required", "ok", "E-invoice required.", "Turnover was above ₹5 Cr in an earlier year, so the IRN is generated on issue."))
    goods = sum((r2(ln.taxable * (1 + ln.gst_rate / 100)) for ln in lines if not ln.is_service), Decimal("0"))
    if ctx.doc_type == "tax_invoice":
        if goods > EWAY_THRESHOLD:
            out.append(check("eway_bill_required", "ok", "E-way bill required.", f"Goods worth ₹{goods:,.2f} cross ₹50,000."))
        elif goods > 0:
            out.append(check("eway_bill_not_needed", "info", "E-way bill not needed.", f"Goods worth ₹{goods:,.2f} are under ₹50,000."))
    if res.tds_expected:
        out.append(check("tds_194q", "info", "Customer deducts TDS.",
                         f"0.1% under Sec 194Q — expect ₹{res.grand_total - res.tds_expected:,.2f}, claim ₹{res.tds_expected:,.2f} from 26AS."))
    if ctx.supplier_is_mse and ctx.payment_terms_days > MSME_MAX_DAYS:
        out.append(check("msme_terms_exceeded", "warn", "Longer than 45 days.",
                         "As a Udyam-registered micro/small supplier you are owed payment within 45 days whatever the invoice says."))
    if ctx.doc_type == "credit_note" and ctx.original_doc_date and ctx.gst_adjusted and not ctx.zero_rated:   # nothing to reduce when no GST was charged
        deadline = sec34_deadline(ctx.original_doc_date)
        if ctx.doc_date > deadline:
            out.append(check("sec34_time_limit", "block", "Too late to reduce GST.",
                             f"Credit notes for this supply had to be issued by {deadline:%d %b %Y}. Issue a commercial credit note instead."))
        else:
            out.append(check("sec34_time_limit", "ok", "Within the time limit.", f"Can be issued until {deadline:%d %b %Y} (or the annual return date, if earlier)."))
    return out


def foreign_amount(grand_total: Decimal, rate: Decimal | None) -> Decimal | None:
    return r2(grand_total / rate) if rate else None


def export_checks(lines: list[Line], ctx: Context, res: Result) -> list[dict]:
    """What an export invoice needs: where it goes, the currency and rate, the LUT, and whether a shipping bill follows."""
    if ctx.doc_type in ("credit_note", "debit_note"):
        word = "Credit note" if ctx.doc_type == "credit_note" else "Debit note"
        how = "no GST, as the invoice was supplied under LUT" if ctx.zero_rated else "IGST, as the invoice was exported with payment of tax"
        out = [check("export_note", "ok", f"{word} on an export invoice.", f"It follows the invoice: same currency and exchange rate, {how}.")]
        if ctx.exchange_rate:
            out.append(check("export_foreign_value", "info", f"{ctx.currency} {foreign_amount(res.grand_total, ctx.exchange_rate):,.2f}",
                             f"What your customer is {'credited' if ctx.doc_type == 'credit_note' else 'charged'}; ₹{res.grand_total:,.2f} at ₹{ctx.exchange_rate:f} per {ctx.currency} is what the books and GST show."))
        return out
    out = [check("export_detected", "ok", "Export invoice.", f"The customer is in {ctx.country or 'another country'}; the place of supply is outside India.")]
    if ctx.currency == "INR" or not ctx.exchange_rate:
        out.append(check("export_needs_rate", "block", "Choose the currency and exchange rate.",
                         "An export invoice states the foreign currency and the rate used to convert it. Enter both."))
    else:
        out.append(check("export_foreign_value", "info", f"{ctx.currency} {foreign_amount(res.grand_total, ctx.exchange_rate):,.2f}",
                         f"The customer pays this amount; ₹{res.grand_total:,.2f} at ₹{ctx.exchange_rate:f} per {ctx.currency} is what the books and GST show."))
    if ctx.supply_type == "export_under_lut" and not ctx.zero_rated:
        out.append(check("lut_not_valid", "block", "No valid LUT for this date.", "A supply under LUT needs a Letter of Undertaking that covers the invoice date. Record one under Settings, or export with payment of IGST."))
    elif ctx.zero_rated:
        out.append(check("export_under_lut", "ok", "Supplied under LUT.", "No GST is charged. Your Letter of Undertaking covers this date."))
    elif ctx.lut in ("none", "expired"):
        out.append(check("export_no_lut", "warn", "No valid LUT: IGST is charged.",
                         "Without a Letter of Undertaking you pay IGST and claim it back as a refund. File a LUT on the GST portal and record it under Settings to export without tax."))
    else:
        out.append(check("export_with_tax", "info", "Exported with payment of IGST.", "IGST is charged and claimed back as a refund."))
    if ctx.lut == "expiring" and ctx.zero_rated:
        out.append(check("lut_expiring", "warn", "Your LUT is about to expire.", "Renew it before the next financial year's exports."))
    if any(not ln.is_service for ln in lines):
        out.append(check("shipping_bill_follows", "info", "A draft shipping bill opens when you issue this.",
                         "Add the number, port and date customs gives you. The date of export starts the nine-month clock to bring the money home."))
    else:
        out.append(check("export_services", "info", "Export of services: no shipping bill.", "Keep the foreign inward remittance certificate (FIRC) from your bank as proof of receipt."))
    return out


def sec34_deadline(original: dt.date) -> dt.date:
    fy_end_year = original.year + 1 if original.month >= 4 else original.year
    return dt.date(fy_end_year, 11, 30)


# ------------------------------------------------------------------ amount in words (Indian numbering)
_A = ["", "One", "Two", "Three", "Four", "Five", "Six", "Seven", "Eight", "Nine", "Ten", "Eleven", "Twelve", "Thirteen",
      "Fourteen", "Fifteen", "Sixteen", "Seventeen", "Eighteen", "Nineteen"]
_B = ["", "", "Twenty", "Thirty", "Forty", "Fifty", "Sixty", "Seventy", "Eighty", "Ninety"]


def _two(n): return _A[n] if n < 20 else _B[n // 10] + (" " + _A[n % 10] if n % 10 else "")
def _three(n): return ((_A[n // 100] + " Hundred" + (" " if n % 100 else "")) if n // 100 else "") + (_two(n % 100) if n % 100 else "")


def _words(n: int) -> str:
    parts = []
    crore, n = divmod(n, 10**7)
    if crore:
        parts.append(_words(crore) + " Crore")       # crores can exceed 99
    for size, label in ((10**5, "Lakh"), (10**3, "Thousand")):
        k, n = divmod(n, size)
        if k:
            parts.append(_two(k) + " " + label)
    if n:
        parts.append(_three(n))
    return " ".join(parts)


def in_words(amount: Decimal) -> str:
    return "Rupees " + (_words(int(amount)) or "Zero") + " only"
