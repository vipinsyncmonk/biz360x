"""A sales document as a PDF a customer can be sent: supplier and buyer, the lines with their tax, the totals in words, and for an export the
LUT or IGST statement and the foreign amount. Pure: it takes the document the API already loads and returns bytes, so it needs no database.

The built-in PDF fonts have no rupee sign, so amounts are written "Rs." Money arrives as strings and is only formatted here, never computed.
"""
from __future__ import annotations

import io
from decimal import Decimal
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

TITLES = {"tax_invoice": "TAX INVOICE", "export_invoice": "EXPORT INVOICE", "bill_of_supply": "BILL OF SUPPLY", "credit_note": "CREDIT NOTE",
          "debit_note": "DEBIT NOTE", "proforma": "PROFORMA INVOICE"}
INK, MUTED, LINE = colors.HexColor("#15171c"), colors.HexColor("#5b6270"), colors.HexColor("#c9ced8")


def inr(value) -> str:
    """12345678.5 -> '1,23,45,678.50': the Indian grouping of lakhs and crores, two decimals, no float."""
    d = Decimal(str(value or "0")).quantize(Decimal("0.01"))
    sign, digits = ("-" if d < 0 else ""), f"{abs(d):.2f}"
    whole, frac = digits.split(".")
    if len(whole) > 3:
        head, tail = whole[:-3], whole[-3:]
        parts = []
        while len(head) > 2:
            parts.insert(0, head[-2:])
            head = head[:-2]
        if head:
            parts.insert(0, head)
        whole = ",".join(parts + [tail])
    return f"{sign}{whole}.{frac}"


def rs(value) -> str:
    return f"Rs. {inr(value)}"


def _dmy(iso) -> str:
    s = str(iso or "")[:10]
    return f"{s[8:10]}/{s[5:7]}/{s[0:4]}" if len(s) == 10 and s[4] == "-" else s


def _p(text, style) -> Paragraph:
    return Paragraph(escape(str(text)).replace("\n", "<br/>"), style)


def _styles():
    base = getSampleStyleSheet()["Normal"]
    mk = lambda name, **kw: ParagraphStyle(name, parent=base, fontName=kw.pop("font", "Helvetica"), textColor=kw.pop("color", INK), **kw)
    return {"body": mk("body", fontSize=9, leading=12), "small": mk("small", fontSize=7.5, leading=10, color=MUTED),
            "bold": mk("bold", font="Helvetica-Bold", fontSize=9, leading=12), "name": mk("name", font="Helvetica-Bold", fontSize=14, leading=17),
            "title": mk("title", font="Helvetica-Bold", fontSize=16, leading=19, alignment=2), "right": mk("right", fontSize=9, leading=12, alignment=2),
            "cell": mk("cell", fontSize=8.5, leading=11), "cellr": mk("cellr", fontSize=8.5, leading=11, alignment=2),
            "head": mk("head", font="Helvetica-Bold", fontSize=8, leading=10, color=colors.white), "headr": mk("headr", font="Helvetica-Bold", fontSize=8, leading=10, color=colors.white, alignment=2)}


def _qr(payload: str, size: float):
    try:
        from reportlab.graphics.barcode.qr import QrCodeWidget
        from reportlab.graphics.shapes import Drawing
        w = QrCodeWidget(payload)
        x0, y0, x1, y1 = w.getBounds()
        d = Drawing(size, size, transform=[size / (x1 - x0), 0, 0, size / (y1 - y0), 0, 0])
        d.add(w)
        return d
    except Exception:                                           # a QR that cannot be drawn must not stop the invoice
        return None


def address_lines(a: dict | None, states: dict) -> list[str]:
    if not a:
        return []
    city = " ".join(x for x in (a.get("city"), a.get("pincode")) if x)
    state = states.get(a.get("state_code") or "")
    return [x for x in (a.get("line1"), a.get("line2"), ", ".join(x for x in (city, state) if x)) if x]


def render_invoice(doc: dict, supplier: dict, buyer_address: dict | None = None, states: dict | None = None, lut_number: str | None = None) -> bytes:
    """`doc` is routes_sales.load_document's result; `supplier` has legal_name, registered_address, pan, cin, gstin, state_code;
    `states` maps GST state codes to names."""
    states = states or {}
    st = _styles()
    buf = io.BytesIO()
    export = doc["doc_type"] == "export_invoice" or (doc.get("currency") or "INR") != "INR"
    is_draft = doc.get("status") == "draft" or not doc.get("number")
    title = TITLES.get(doc["doc_type"], doc["doc_type"].replace("_", " ").upper())
    pdf = SimpleDocTemplate(buf, pagesize=A4, leftMargin=14 * mm, rightMargin=14 * mm, topMargin=14 * mm, bottomMargin=16 * mm,
                            title=f"{title} {doc.get('number') or 'draft'}", author=supplier.get("legal_name") or "")
    width = A4[0] - 28 * mm
    story: list = []

    # ---- who is selling, and what this is
    left = [_p(supplier.get("legal_name") or "", st["name"])]
    for line in [supplier.get("registered_address")]:
        if line:
            left.append(_p(line, st["body"]))
    ids = [f"GSTIN {supplier['gstin']}" if supplier.get("gstin") else None, f"PAN {supplier['pan']}" if supplier.get("pan") else None,
           f"CIN {supplier['cin']}" if supplier.get("cin") else None]
    left.append(_p("   ·   ".join(x for x in ids if x), st["small"]))
    right = [_p(title, st["title"]), _p(f"No. {doc['number']}" if doc.get("number") else "DRAFT: not yet issued", st["right"]), _p(f"Date {_dmy(doc['doc_date'])}", st["right"])]
    if doc.get("due_date") and doc["doc_type"] not in ("credit_note",):
        right.append(_p(f"Due {_dmy(doc['due_date'])}", st["right"]))
    head = Table([[left, right]], colWidths=[width * 0.58, width * 0.42])
    head.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("BOTTOMPADDING", (0, 0), (-1, -1), 8), ("LINEBELOW", (0, 0), (-1, 0), 1, INK)]))
    story += [head, Spacer(1, 8)]

    # ---- who is buying
    buyer = [_p("BILL TO", st["small"]), _p(doc.get("buyer_legal_name") or "", st["bold"])]
    buyer += [_p(x, st["body"]) for x in address_lines(buyer_address, states)]
    if doc.get("buyer_gstin"):
        buyer.append(_p(f"GSTIN {doc['buyer_gstin']}", st["body"]))
    elif not export:
        buyer.append(_p("Unregistered buyer", st["small"]))
    pos = "Outside India (96)" if export else (f"{states.get(doc.get('place_of_supply') or '', '')} ({doc.get('place_of_supply')})" if doc.get("place_of_supply") else "")
    meta = [_p("PLACE OF SUPPLY", st["small"]), _p(pos or "-", st["body"])]
    if doc.get("original_document_id") and doc["doc_type"] in ("credit_note", "debit_note"):
        meta += [_p("REASON", st["small"]), _p((doc.get("cn_reason") or "").replace("_", " ").capitalize() or "-", st["body"])]
    details = Table([[buyer, meta]], colWidths=[width * 0.58, width * 0.42])
    details.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("BOTTOMPADDING", (0, 0), (-1, -1), 8)]))
    story += [details, Spacer(1, 4)]

    # ---- the lines
    inter = Decimal(str(doc.get("igst_total") or 0)) > 0
    cols = ["#", "Description", "HSN/SAC", "Qty", "Rate", "Taxable", "GST %", "Tax", "Amount"]
    rows = [[_p(c, st["headr"] if i >= 3 else st["head"]) for i, c in enumerate(cols)]]
    for l in doc["lines"]:
        tax_amt = sum(Decimal(str(l.get(k) or 0)) for k in ("cgst_amount", "sgst_amount", "igst_amount", "cess_amount"))
        taxable = Decimal(str(l["taxable_value"]))
        rows.append([_p(l["line_no"], st["cell"]), _p(l["description"], st["cell"]), _p(l["hsn_sac"], st["cell"]),
                     _p(f"{Decimal(str(l['qty'])).normalize():f} {l.get('uqc') or ''}".strip(), st["cellr"]), _p(inr(l["rate"]), st["cellr"]),
                     _p(inr(taxable), st["cellr"]), _p("Nil" if doc.get("supply_type") == "export_under_lut" else f"{Decimal(str(l['gst_rate'])).normalize():f}", st["cellr"]), _p(inr(tax_amt), st["cellr"]),
                     _p(inr(taxable + tax_amt), st["cellr"])])
    widths = [7, 44, 19, 16, 24, 26, 10, 23, 26]
    scale = width / (sum(widths) * mm)
    items = Table(rows, colWidths=[w * mm * scale for w in widths], repeatRows=1)
    items.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, 0), INK), ("VALIGN", (0, 0), (-1, -1), "TOP"), ("LINEBELOW", (0, 1), (-1, -1), 0.4, LINE),
                               ("TOPPADDING", (0, 0), (-1, -1), 4), ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                               ("LEFTPADDING", (0, 0), (-1, -1), 3), ("RIGHTPADDING", (0, 0), (-1, -1), 3)]))
    story += [items, Spacer(1, 8)]

    # ---- totals
    tot = [("Taxable value", doc["taxable_total"])]
    if inter or export:                                           # an export is always an inter-state supply: IGST, or none under a LUT
        tot.append(("IGST (nil: supplied under LUT)" if doc.get("supply_type") == "export_under_lut" else "IGST", doc["igst_total"]))
    else:
        tot += [("CGST", doc["cgst_total"]), ("SGST", doc["sgst_total"])]
    if Decimal(str(doc.get("cess_total") or 0)) > 0:
        tot.append(("Cess", doc["cess_total"]))
    if Decimal(str(doc.get("round_off") or 0)) != 0:
        tot.append(("Round off", doc["round_off"]))
    trows = [[_p(k, st["body"]), _p(rs(v), st["right"])] for k, v in tot]
    trows.append([_p("TOTAL", st["bold"]), _p(rs(doc["grand_total"]), ParagraphStyle("t", parent=st["right"], fontName="Helvetica-Bold", fontSize=11, leading=14))])
    totals = Table(trows, colWidths=[56 * mm, 36 * mm], hAlign="RIGHT")
    totals.setStyle(TableStyle([("LINEABOVE", (0, -1), (-1, -1), 0.8, INK), ("TOPPADDING", (0, 0), (-1, -1), 2), ("BOTTOMPADDING", (0, 0), (-1, -1), 2)]))
    story.append(totals)
    from .tax import in_words
    story += [Spacer(1, 4), _p(f"Amount in words: {in_words(Decimal(str(doc['grand_total'])))}", st["body"])]
    if doc.get("foreign_total") and doc.get("exchange_rate"):
        story.append(_p(f"Customer pays {doc['currency']} {inr(doc['foreign_total'])} (converted at Rs. {doc['exchange_rate']} per {doc['currency']}).", st["bold"]))

    # ---- what the law wants said
    notes: list[str] = []
    if doc["doc_type"] == "export_invoice" or export:
        if doc.get("supply_type") == "export_under_lut":
            notes.append("SUPPLY MEANT FOR EXPORT UNDER LETTER OF UNDERTAKING WITHOUT PAYMENT OF INTEGRATED TAX" + (f" (LUT No. {lut_number})." if lut_number else "."))
        elif doc.get("supply_type") == "export_with_tax":
            notes.append("SUPPLY MEANT FOR EXPORT ON PAYMENT OF INTEGRATED TAX.")
    if doc["doc_type"] == "bill_of_supply":
        notes.append("Composition taxable person, not eligible to collect tax on supplies.")
    einv = doc.get("einvoice") or {}
    if einv.get("irn"):
        notes += [f"IRN: {einv['irn']}", f"Ack No. {einv.get('ack_no') or '-'}   Ack Date {_dmy(einv.get('ack_at'))}"]
    story.append(Spacer(1, 6))
    qr = _qr(einv["signed_qr"], 28 * mm) if einv.get("signed_qr") else None
    note_cells = [[_p(n, st["small"]) for n in notes] or [_p("", st["small"])]]
    foot = Table([[note_cells[0], qr or ""]], colWidths=[width - 32 * mm, 32 * mm])
    foot.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP")]))
    story.append(foot)
    story += [Spacer(1, 14), Table([["", [_p(f"For {supplier.get('legal_name') or ''}", st["right"]), Spacer(1, 22), _p("Authorised signatory", st["right"])]]],
                                    colWidths=[width * 0.5, width * 0.5])]
    story += [Spacer(1, 8), _p("This is a computer generated document." + (" Draft: not valid as a tax invoice until it is issued." if is_draft else ""), st["small"])]

    def furniture(canvas, _doc):
        canvas.saveState()
        if is_draft:
            canvas.setFont("Helvetica-Bold", 90)
            canvas.setFillColor(colors.Color(0.85, 0.2, 0.2, alpha=0.12))
            canvas.translate(A4[0] / 2, A4[1] / 2)
            canvas.rotate(40)
            canvas.drawCentredString(0, 0, "DRAFT")
        canvas.restoreState()
        canvas.saveState()
        canvas.setFont("Helvetica", 7.5)
        canvas.setFillColor(MUTED)
        canvas.drawRightString(A4[0] - 14 * mm, 9 * mm, f"Page {canvas.getPageNumber()}")
        canvas.restoreState()

    pdf.build(story, onFirstPage=furniture, onLaterPages=furniture)
    return buf.getvalue()
