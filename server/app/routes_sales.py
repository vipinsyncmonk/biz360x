"""Sales documents (invoices / notes), preview, issue, receipts."""
from __future__ import annotations

import datetime as dt
import json
import uuid
from decimal import Decimal
from typing import Literal

from fastapi import APIRouter, Header, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from . import ledger, tax, trade
from .core import (ApiError, Auth, Principal, audit, emit, etag_of, idempotency_begin, idempotency_finish, jsonable, money,
                   require, tx)

router = APIRouter()
E = "/entities/{entity_id}"


class LineIn(BaseModel):
    item_id: uuid.UUID | None = None
    description: str
    hsn_sac: str = Field(pattern=r"^[0-9]{4,8}$")
    qty: Decimal = Field(gt=0)
    rate: Decimal = Field(ge=0)
    gst_rate: Decimal
    discount: Decimal = Decimal("0")
    uqc: str = "NOS"
    so_line_id: int | None = None
    original_line_id: int | None = None


class SalesDocIn(BaseModel):
    doc_type: Literal["tax_invoice", "credit_note", "debit_note", "export_invoice", "bill_of_supply"] = "tax_invoice"
    doc_date: dt.date
    registration_id: uuid.UUID
    party_id: uuid.UUID
    party_gst_id: uuid.UUID | None = None
    place_of_supply: str | None = Field(default=None, pattern=r"^[0-9]{2}$")
    supply_type: str = "b2b"
    payment_terms_days: int | None = None
    customer_po_ref: str | None = None
    original_document_id: uuid.UUID | None = None
    cn_reason: str | None = None
    gst_adjusted: bool = True
    currency: str | None = Field(default=None, pattern=r"^[A-Z]{3}$")      # an export invoice: what the customer pays in
    exchange_rate: Decimal | None = Field(default=None, gt=0, max_digits=12, decimal_places=4)   # rupees per unit of that currency
    quote_id: uuid.UUID | None = None
    sales_order_id: uuid.UUID | None = None
    lines: list[LineIn] = Field(min_length=1)


def _context(cur, entity_id, body: SalesDocIn):
    cur.execute("SELECT state_code, number FROM entity_registrations WHERE id = %s AND entity_id = %s AND kind = 'gstin'",
                (body.registration_id, entity_id))
    reg = cur.fetchone()
    if not reg:
        raise ApiError(400, "validation_failed", "registration_id is not a GSTIN of this entity",
                       errors=[{"field": "registration_id", "message": "unknown GSTIN"}])
    cur.execute("SELECT legal_name, deducts_tds_194q, payment_terms_days, country, currency FROM parties WHERE id = %s AND entity_id = %s AND is_customer",
                (body.party_id, entity_id))
    party = cur.fetchone()
    if not party:
        raise ApiError(400, "validation_failed", "party_id is not a customer of this entity",
                       errors=[{"field": "party_id", "message": "unknown customer"}])
    gst = None
    if body.party_gst_id:
        cur.execute("SELECT gstin, state_code FROM party_gst_registrations WHERE id = %s AND party_id = %s", (body.party_gst_id, body.party_id))
        gst = cur.fetchone()
        if not gst:
            raise ApiError(400, "validation_failed", "party_gst_id does not belong to the party")
    cur.execute("SELECT value_num FROM entity_facts_current WHERE entity_id = %s AND fact_key = 'aato'", (entity_id,))
    f = cur.fetchone()
    cur.execute("SELECT udyam_class::text FROM entities WHERE id = %s", (entity_id,))
    cls = cur.fetchone()["udyam_class"]
    original_date = original = None
    if body.original_document_id:
        cur.execute("""SELECT doc_date, doc_type::text AS doc_type, party_id, supply_type::text AS supply_type, currency, exchange_rate
                         FROM sales_documents WHERE id = %s AND entity_id = %s""", (body.original_document_id, entity_id))
        original = cur.fetchone()
        if not original:
            raise ApiError(400, "validation_failed", "original_document_id not found")
        original_date = original["doc_date"]
    if party["country"] != "IN" and body.doc_type in ("credit_note", "debit_note") and (
            not original or original["doc_type"] != "export_invoice" or original["party_id"] != body.party_id):
        raise ApiError(400, "export_note_needs_invoice", "Raise this note against the export invoice it corrects",
                       "A credit or debit note to a customer abroad follows the export invoice it corrects: same currency, exchange rate and tax treatment. Choose that invoice as the original.")
    ex = export_terms(cur, entity_id, body, party, original)
    pos = "96" if ex["is_export"] else body.place_of_supply or (gst["state_code"] if gst else reg["state_code"])
    terms = body.payment_terms_days if body.payment_terms_days is not None else party["payment_terms_days"]
    ctx = tax.Context(doc_type=ex["doc_type"], supplier_state=reg["state_code"], place_of_supply=pos, doc_date=body.doc_date,
                      buyer_gstin=gst["gstin"] if gst else None, aato=f["value_num"] if f else None,
                      deducts_194q=party["deducts_tds_194q"], payment_terms_days=terms,
                      supplier_is_mse=cls in ("micro", "small"), original_doc_date=original_date, gst_adjusted=body.gst_adjusted,
                      is_export=ex["is_export"], zero_rated=ex["zero_rated"], lut=ex["lut"],
                      currency=ex["currency"], exchange_rate=ex["exchange_rate"], country=party["country"], supply_type=ex["supply_type"])
    lines = [tax.Line(l.description, l.hsn_sac, l.qty, l.rate, l.gst_rate, l.discount, is_service=l.hsn_sac.startswith("99"))
             for l in body.lines]
    return ctx, lines, party, gst, reg


def export_terms(cur, entity_id, body: SalesDocIn, party, original=None) -> dict:
    """Whether this document is an export, decided by where the customer is: a customer outside India turns a tax invoice into an export invoice,
    supplied under LUT when a valid one covers the date and with payment of IGST when not. A credit or debit note to a customer abroad follows
    the export invoice it corrects: its currency, its rate and its tax treatment, whatever the LUT says today."""
    if party["country"] != "IN" and body.doc_type in ("credit_note", "debit_note") and original:
        return {"is_export": True, "doc_type": body.doc_type, "supply_type": original["supply_type"], "lut": "none", "zero_rated": original["supply_type"] == "export_under_lut",
                "currency": original["currency"], "exchange_rate": original["exchange_rate"]}
    is_export = party["country"] != "IN" and body.doc_type in ("tax_invoice", "export_invoice")
    if not is_export:
        return {"is_export": False, "doc_type": body.doc_type, "supply_type": body.supply_type, "lut": "none", "zero_rated": False, "currency": "INR", "exchange_rate": None}
    cur.execute("""SELECT valid_until FROM entity_registrations WHERE entity_id = %s AND kind = 'lut' AND status = 'active'
                      AND (valid_from IS NULL OR valid_from <= %s) ORDER BY valid_until DESC NULLS FIRST LIMIT 1""", (entity_id, body.doc_date))
    lut_row = cur.fetchone()
    lut = trade.lut_state(lut_row["valid_until"] if lut_row else None, body.doc_date, lut_row is not None)["state"]
    if body.supply_type in ("export_under_lut", "export_with_tax"):
        supply = body.supply_type
    else:
        supply = "export_under_lut" if lut in ("valid", "expiring") else "export_with_tax"
    currency = body.currency or (party["currency"] if party["currency"] != "INR" else "INR")
    rate = body.exchange_rate if currency != "INR" else None
    return {"is_export": True, "doc_type": "export_invoice", "supply_type": supply, "lut": lut, "zero_rated": supply == "export_under_lut" and lut in ("valid", "expiring"),
            "currency": currency, "exchange_rate": rate}


def ensure_export_series(cur, entity_id, registration_id, fy) -> None:
    """The first export invoice of a year for a GSTIN gets its own number series."""
    cur.execute("""INSERT INTO document_series (entity_id, registration_id, doc_type, fy, prefix) VALUES (%s, %s, 'export_invoice', %s, 'EXP/')
                   ON CONFLICT (registration_id, doc_type, fy) DO NOTHING""", (entity_id, registration_id, fy))


def _peek_number(cur, registration_id, doc_type, fy):
    cur.execute("""SELECT prefix || lpad(next_number::text, pad_to, '0') AS n FROM document_series
                    WHERE registration_id = %s AND doc_type = %s AND fy = %s""", (registration_id, doc_type, fy))
    r = cur.fetchone()
    return r["n"] if r else None


def _money_out(d: dict) -> dict:
    return {k: (money(v) if isinstance(v, Decimal) else v) for k, v in d.items()}


@router.post(E + "/sales-documents/preview", tags=["Sales & billing"])
def preview(entity_id: uuid.UUID, body: SalesDocIn, p: Principal = Auth):
    with tx(p.user_id) as cur:
        require(cur, entity_id, "sales:read")
        ctx, lines, _, _, _ = _context(cur, entity_id, body)
        res = tax.compute(lines, ctx)
        cur.execute("SELECT app.fy_of(%s) AS fy", (body.doc_date,))
        nxt = _peek_number(cur, body.registration_id, ctx.doc_type, cur.fetchone()["fy"])
    return {"doc_type": ctx.doc_type, "supply_type": ctx.supply_type or body.supply_type, "is_export": ctx.is_export, "currency": ctx.currency,
            "exchange_rate": str(ctx.exchange_rate) if ctx.exchange_rate else None,
            "foreign_total": money(tax.foreign_amount(res.grand_total, ctx.exchange_rate)) if ctx.is_export and ctx.exchange_rate else None,
            "is_inter_state": res.is_inter_state, "place_of_supply": ctx.place_of_supply,
            "lines": [_money_out(l) for l in res.lines],
            "taxable_total": money(res.taxable_total), "cgst_total": money(res.cgst_total), "sgst_total": money(res.sgst_total),
            "igst_total": money(res.igst_total), "cess_total": "0.00", "round_off": money(res.round_off),
            "grand_total": money(res.grand_total), "amount_in_words": tax.in_words(res.grand_total),
            "due_date": res.due_date.isoformat(), "tds_expected": money(res.tds_expected),
            "expected_receipt": money(res.grand_total - res.tds_expected), "next_number": nxt, "checks": res.checks}


def load_document(cur, entity_id, doc_id) -> dict:
    cur.execute("""SELECT id, doc_type::text, number, doc_date, fy, registration_id, party_id, party_gst_id, buyer_gstin,
                          buyer_legal_name, place_of_supply, is_inter_state, supply_type::text, currency, exchange_rate::text AS exchange_rate, foreign_total, original_document_id,
                          cn_reason::text, gst_adjusted, payment_terms_days, due_date, taxable_total, cgst_total, sgst_total,
                          igst_total, cess_total, round_off, grand_total, tds_expected, amount_settled, balance_due, status,
                          payment_status, irn_status::text, irn, irn_ack_no, irn_ack_at, irn_signed_qr, issued_at
                     FROM sales_documents WHERE id = %s AND entity_id = %s""", (doc_id, entity_id))
    d = cur.fetchone()
    if not d:
        raise ApiError(404, "not_found", "Not found")
    cur.execute("""SELECT line_no, description, hsn_sac, qty, uqc, rate, discount, taxable_value, gst_rate,
                          cgst_amount, sgst_amount, igst_amount, cess_amount
                     FROM sales_document_lines WHERE document_id = %s ORDER BY line_no""", (doc_id,))
    d["lines"] = cur.fetchall()
    d["einvoice"] = {"status": d.pop("irn_status"), "irn": d.pop("irn"), "ack_no": d.pop("irn_ack_no"),
                     "ack_at": d.pop("irn_ack_at"), "signed_qr": d.pop("irn_signed_qr")}
    return json.loads(json.dumps(d, default=jsonable))


def create_draft(cur, entity_id, body: SalesDocIn) -> uuid.UUID:
    """Validate, compute and insert a draft sales document. Shared by the API, quotes, orders, credit notes and recurring invoices."""
    ctx, lines, party, gst, reg = _context(cur, entity_id, body)
    res = tax.compute(lines, ctx)
    blocks = [c for c in res.checks if c["level"] == "block"]
    if blocks and (body.doc_type == "credit_note" or ctx.is_export):
        raise ApiError(400, blocks[0]["code"], blocks[0]["title"], blocks[0]["detail"])
    if ctx.doc_type == "export_invoice":
        cur.execute("SELECT app.fy_of(%s) AS fy", (body.doc_date,))
        ensure_export_series(cur, entity_id, body.registration_id, cur.fetchone()["fy"])
    cur.execute("""
        INSERT INTO sales_documents (entity_id, doc_type, doc_date, registration_id, supplier_state, party_id, party_gst_id,
               buyer_gstin, buyer_legal_name, bill_to, place_of_supply, supply_type, customer_po_ref, original_document_id,
               cn_reason, gst_adjusted, payment_terms_days, due_date, taxable_total, cgst_total, sgst_total, igst_total,
               round_off, grand_total, tds_expected, irn_status, currency, exchange_rate, created_by, quote_id, sales_order_id)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                app.current_user_id(), %s, %s)
        RETURNING id""",
        (entity_id, ctx.doc_type, body.doc_date, body.registration_id, ctx.supplier_state, body.party_id, body.party_gst_id,
         ctx.buyer_gstin, party["legal_name"], json.dumps({"gstin": ctx.buyer_gstin, "state": ctx.place_of_supply, **({"country": party["country"]} if ctx.is_export else {})}),
         ctx.place_of_supply, ctx.supply_type or body.supply_type, body.customer_po_ref, body.original_document_id, body.cn_reason,
         body.gst_adjusted, ctx.payment_terms_days, res.due_date, res.taxable_total, res.cgst_total, res.sgst_total,
         res.igst_total, res.round_off, res.grand_total, res.tds_expected,
         "pending" if any(c["code"] == "einvoice_required" for c in res.checks) else "not_required",
         ctx.currency, ctx.exchange_rate, body.quote_id, body.sales_order_id))
    doc_id = cur.fetchone()["id"]
    for l, src in zip(res.lines, body.lines):
        cur.execute("""INSERT INTO sales_document_lines (document_id, line_no, item_id, so_line_id, original_line_id, description, hsn_sac,
                              is_service, qty, uqc, rate, discount, taxable_value, gst_rate, cgst_amount, sgst_amount, igst_amount)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                    (doc_id, l["line_no"], src.item_id, src.so_line_id, src.original_line_id, src.description, src.hsn_sac,
                     src.hsn_sac.startswith("99"), src.qty, src.uqc, src.rate, src.discount, l["taxable_value"], src.gst_rate,
                     l["cgst_amount"], l["sgst_amount"], l["igst_amount"]))
    audit(cur, entity_id, "sales_document.drafted", "sales_documents", doc_id, after={"grand_total": res.grand_total})
    return doc_id


@router.post(E + "/sales-documents", status_code=201, tags=["Sales & billing"])
def create_document(entity_id: uuid.UUID, body: SalesDocIn, response: Response, p: Principal = Auth):
    with tx(p.user_id) as cur:
        require(cur, entity_id, "sales:write")
        doc_id = create_draft(cur, entity_id, body)
        doc = load_document(cur, entity_id, doc_id)
    response.headers["ETag"] = etag_of(doc)
    response.headers["Location"] = f"/v1/entities/{entity_id}/sales-documents/{doc_id}"
    return doc


@router.get(E + "/sales-documents/{doc_id}", tags=["Sales & billing"])
def get_document(entity_id: uuid.UUID, doc_id: uuid.UUID, response: Response, p: Principal = Auth):
    with tx(p.user_id) as cur:
        require(cur, entity_id, "sales:read")
        doc = load_document(cur, entity_id, doc_id)
    response.headers["ETag"] = etag_of(doc)
    return doc


class IssueRequest(BaseModel):
    create_payment_link: bool = True
    generate_eway_bill: bool = True


def open_draft_shipping_bill(cur, entity_id, doc_id, number) -> uuid.UUID | None:
    """An issued export invoice with goods on it opens a draft shipping bill (foreign value of the goods, at the invoice's rate) and a task to add what customs issues.
    Services need no shipping bill. Customs numbers, port and date come from a person: nothing here files with ICEGATE."""
    cur.execute("""SELECT d.party_id, d.currency, d.exchange_rate, d.doc_date,
                          coalesce(sum(l.taxable_value) FILTER (WHERE NOT l.is_service), 0) AS goods
                     FROM sales_documents d JOIN sales_document_lines l ON l.document_id = d.id WHERE d.id = %s GROUP BY d.id""", (doc_id,))
    d = cur.fetchone()
    if not d or not d["exchange_rate"] or d["goods"] <= 0:
        return None
    fob = tax.foreign_amount(d["goods"], d["exchange_rate"])
    if fob <= 0:
        return None
    cur.execute("""INSERT INTO shipping_bills (entity_id, sales_document_id, buyer_party_id, currency, fob_foreign, exchange_rate, status)
                   VALUES (%s, %s, %s, %s, %s, %s, 'draft') RETURNING id""", (entity_id, doc_id, d["party_id"], d["currency"], fob, d["exchange_rate"]))
    sid = cur.fetchone()["id"]
    cur.execute("INSERT INTO tasks (entity_id, title, due_on, source) VALUES (%s, %s, %s, 'engine')",
                (entity_id, f"Add the shipping bill details for export invoice {number} ({d['currency']} {fob:,.2f}): number, port and date from customs", dt.date.today()))
    audit(cur, entity_id, "shipping_bill.drafted", "shipping_bills", sid, after={"sales_document_id": str(doc_id), "fob_foreign": fob, "currency": d["currency"]})
    emit(cur, entity_id, "shipping_bill.drafted", "shipping_bills", sid, {"invoice": number})
    return sid


def invoice_goods_foreign(cur, doc_id):
    """What is to be shipped on an export invoice, in the foreign currency: its goods, less the goods credited back by issued credit notes."""
    cur.execute("""SELECT d.exchange_rate,
                          coalesce((SELECT sum(l.taxable_value) FROM sales_document_lines l WHERE l.document_id = d.id AND NOT l.is_service), 0)
                        - coalesce((SELECT sum(l.taxable_value) FROM sales_documents c JOIN sales_document_lines l ON l.document_id = c.id
                                     WHERE c.original_document_id = d.id AND c.doc_type = 'credit_note' AND c.status = 'issued' AND NOT l.is_service), 0) AS goods
                     FROM sales_documents d WHERE d.id = %s""", (doc_id,))
    r = cur.fetchone()
    if not r or not r["exchange_rate"]:
        return None
    return max(tax.foreign_amount(r["goods"], r["exchange_rate"]), Decimal("0"))


def check_shipment_total(cur, doc_id) -> None:
    """The shipping bills of one export invoice together cannot be worth more than the goods on it."""
    target = invoice_goods_foreign(cur, doc_id)
    if target is None:
        return
    cur.execute("SELECT coalesce(sum(fob_foreign), 0) AS n FROM shipping_bills WHERE sales_document_id = %s", (doc_id,))
    total = cur.fetchone()["n"]
    if total > target + trade.TOLERANCE:
        raise ApiError(409, "shipments_exceed_invoice", "These shipping bills are worth more than the goods on the invoice",
                       f"The invoice's goods come to {target}; its shipping bills would come to {total}. A partial shipment takes only its share.")


def reconcile_shipments(cur, entity_id, doc_id, number=None) -> None:
    """Keep the draft shipping bill in step with the goods still to ship on an export invoice.
    Goods credited back reduce the draft (or remove it); goods not yet covered by any shipping bill, once one has been filed, open a draft for the rest (a partial shipment).
    A credit that no draft can absorb leaves a task: the filed shipping bill is customs' and is amended there."""
    target = invoice_goods_foreign(cur, doc_id)
    if target is None:
        return
    cur.execute("""SELECT s.id, s.status, s.fob_foreign, s.currency, s.exchange_rate, s.buyer_party_id,
                          EXISTS (SELECT 1 FROM export_realisations r WHERE r.shipping_bill_id = s.id) AS has_money
                     FROM shipping_bills s WHERE s.sales_document_id = %s ORDER BY s.created_at""", (doc_id,))
    bills = cur.fetchall()
    if not bills:
        return
    total = sum((b["fob_foreign"] for b in bills), Decimal("0"))
    excess = total - target
    if excess > trade.TOLERANCE:
        for b in bills:
            if b["status"] != "draft" or b["has_money"] or excess <= trade.TOLERANCE:
                continue
            cut = min(b["fob_foreign"], excess)
            if b["fob_foreign"] - cut <= trade.TOLERANCE:
                cur.execute("DELETE FROM shipping_bills WHERE id = %s", (b["id"],))
            else:
                cur.execute("UPDATE shipping_bills SET fob_foreign = fob_foreign - %s WHERE id = %s", (cut, b["id"]))
            audit(cur, entity_id, "shipping_bill.reduced", "shipping_bills", b["id"], after={"reduced_by": cut, "sales_document_id": str(doc_id)})
            excess -= cut
        if excess > trade.TOLERANCE:
            cur.execute("INSERT INTO tasks (entity_id, title, due_on, source) VALUES (%s, %s, %s, 'engine')",
                        (entity_id, f"Goods worth {bills[0]['currency']} {excess:,.2f} on export invoice {number or ''} were credited back: have the filed shipping bill amended with customs",
                         dt.date.today()))
    elif -excess > trade.TOLERANCE and any(b["status"] != "draft" for b in bills) and not any(b["status"] == "draft" for b in bills):
        b = bills[0]
        cur.execute("""INSERT INTO shipping_bills (entity_id, sales_document_id, buyer_party_id, currency, fob_foreign, exchange_rate, status)
                       VALUES (%s, %s, %s, %s, %s, %s, 'draft') RETURNING id""", (entity_id, doc_id, b["buyer_party_id"], b["currency"], -excess, b["exchange_rate"]))
        sid = cur.fetchone()["id"]
        cur.execute("INSERT INTO tasks (entity_id, title, due_on, source) VALUES (%s, %s, %s, 'engine')",
                    (entity_id, f"Part of the goods on export invoice {number or ''} ({b['currency']} {-excess:,.2f}) is still to ship: add its shipping bill details when customs issues them", dt.date.today()))
        audit(cur, entity_id, "shipping_bill.drafted", "shipping_bills", sid, after={"sales_document_id": str(doc_id), "fob_foreign": -excess, "partial": True})


def issue_core(cur, entity_id, doc_id, *, eway_bill: bool = True, payment_link: bool = True) -> dict:
    """Take the next number, post the journal, settle a credit note against its invoice, queue the IRN job.
    Runs in the caller's transaction (API request or worker)."""
    cur.execute("SELECT * FROM sales_documents WHERE id = %s AND entity_id = %s FOR UPDATE", (doc_id, entity_id))
    d = cur.fetchone()
    if not d:
        raise ApiError(404, "not_found", "Not found")
    if d["status"] != "draft":
        raise ApiError(409, "conflict", f"Document is already {d['status']}")
    cur.execute("SELECT id FROM document_series WHERE registration_id = %s AND doc_type = %s AND fy = %s",
                (d["registration_id"], d["doc_type"], d["fy"]))
    series = cur.fetchone()
    if not series:
        raise ApiError(409, "conflict", f"No number series for this GSTIN, {d['doc_type']} and FY {d['fy']}")
    cur.execute("SELECT app.next_document_number(%s) AS n", (series["id"],))
    number = cur.fetchone()["n"]
    cur.execute("""UPDATE sales_documents SET number = %s, series_id = %s, status = 'issued', issued_at = now(),
                          issued_by = app.current_user_id() WHERE id = %s""", (number, series["id"], doc_id))
    ledger.post_sales_document(cur, entity_id, doc_id)
    if d["doc_type"] == "export_invoice":
        open_draft_shipping_bill(cur, entity_id, doc_id, number)
    if d["doc_type"] == "credit_note" and d["original_document_id"]:
        cur.execute("SELECT doc_type::text AS t, number FROM sales_documents WHERE id = %s", (d["original_document_id"],))
        o = cur.fetchone()
        if o and o["t"] == "export_invoice":                 # goods credited back are no longer to be shipped
            reconcile_shipments(cur, entity_id, d["original_document_id"], o["number"])
    if d["doc_type"] == "credit_note":                       # a credit note reduces what the customer owes on the invoice
        cur.execute("SELECT balance_due FROM sales_documents WHERE id = %s", (d["original_document_id"],))
        applicable = min(d["grand_total"], cur.fetchone()["balance_due"])
        if applicable > 0:
            cur.execute("""INSERT INTO invoice_settlements (entity_id, invoice_id, source_kind, credit_note_id, amount, settled_on)
                           VALUES (%s, %s, 'credit_note', %s, %s, %s)""", (entity_id, d["original_document_id"], doc_id, applicable, d["doc_date"]))
    needs_irn = d["irn_status"] == "pending"
    cur.execute("""INSERT INTO jobs (entity_id, kind, status, subject, subject_id, payload, created_by, result, finished_at, progress)
                   VALUES (%s, %s, %s, 'sales_documents', %s, %s, app.current_user_id(), %s, %s, %s) RETURNING *""",
                (entity_id, "einvoice.generate_irn" if needs_irn else "sales_document.issue",
                 "queued" if needs_irn else "succeeded", doc_id,
                 json.dumps({"number": number, "eway_bill": eway_bill, "payment_link": payment_link}),
                 None if needs_irn else json.dumps({"number": number}), None if needs_irn else dt.datetime.now(dt.timezone.utc),
                 0 if needs_irn else 1))
    job = json.loads(json.dumps(cur.fetchone(), default=jsonable))
    job = {k: job[k] for k in ("id", "kind", "status", "progress", "subject", "subject_id", "result", "created_at", "finished_at")}
    audit(cur, entity_id, "sales_document.issued", "sales_documents", doc_id, after={"number": number})
    emit(cur, entity_id, "sales_document.issued", "sales_documents", doc_id, {"number": number})
    return job


@router.post(E + "/sales-documents/{doc_id}/issue", status_code=202, tags=["Sales & billing"])
def issue_document(entity_id: uuid.UUID, doc_id: uuid.UUID, body: IssueRequest, p: Principal = Auth,
                   idempotency_key: str | None = Header(default=None, alias="Idempotency-Key")):
    path = f"/entities/{entity_id}/sales-documents/{doc_id}/issue"
    with tx(p.user_id) as cur:
        require(cur, entity_id, "sales:write")
        replay = idempotency_begin(cur, idempotency_key, "POST", path, body.model_dump())
        if replay:
            return JSONResponse(replay["body"], status_code=replay["status"])
        job = issue_core(cur, entity_id, doc_id, eway_bill=body.generate_eway_bill, payment_link=body.create_payment_link)
        idempotency_finish(cur, idempotency_key, 202, job)
    return JSONResponse(job, status_code=202, headers={"Location": f"/v1/jobs/{job['id']}"})


class AllocationIn(BaseModel):
    invoice_id: uuid.UUID
    amount: Decimal = Field(gt=0)


class ReceiptIn(BaseModel):
    party_id: uuid.UUID
    received_on: dt.date
    amount: Decimal = Field(gt=0)
    method: Literal["neft", "rtgs", "imps", "upi", "cheque", "card", "netbanking", "treds", "cash", "adjustment"]
    utr: str | None = None
    allocations: list[AllocationIn] = []


def record_receipt(cur, entity_id, body: ReceiptIn, *, bank_account_id=None, bank_transaction_id=None) -> dict:
    """Insert a confirmed receipt, settle the allocated invoices and post the journal (Dr bank, Cr receivable / advances)."""
    receipt_no = "RCPT-" + uuid.uuid4().hex[:8].upper()
    cur.execute("""INSERT INTO receipts (entity_id, party_id, receipt_no, received_on, amount, method, utr, bank_account_id,
                          bank_transaction_id, is_advance)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id""",
                (entity_id, body.party_id, receipt_no, body.received_on, body.amount, body.method, body.utr, bank_account_id,
                 bank_transaction_id, not body.allocations))
    rid = cur.fetchone()["id"]
    for a in body.allocations:
        cur.execute("SELECT 1 FROM sales_documents WHERE id = %s AND entity_id = %s AND party_id = %s AND status = 'issued'",
                    (a.invoice_id, entity_id, body.party_id))
        if not cur.fetchone():
            raise ApiError(400, "validation_failed", "Allocation must target an issued invoice of the same customer",
                           errors=[{"field": "allocations.invoice_id", "message": str(a.invoice_id)}])
        cur.execute("""INSERT INTO invoice_settlements (entity_id, invoice_id, source_kind, receipt_id, amount, settled_on)
                       VALUES (%s, %s, 'receipt', %s, %s, %s)""", (entity_id, a.invoice_id, rid, a.amount, body.received_on))
        cur.execute("SELECT payment_status FROM sales_documents WHERE id = %s", (a.invoice_id,))
        if cur.fetchone()["payment_status"] == "paid":
            emit(cur, entity_id, "sales_document.paid", "sales_documents", a.invoice_id)
    allocated = sum((a.amount for a in body.allocations), Decimal("0"))
    bank_key = None
    if bank_account_id:
        cur.execute("SELECT ledger_account_id FROM bank_accounts WHERE id = %s", (bank_account_id,))
        bank_key = "account:" + str(cur.fetchone()["ledger_account_id"])
    jid = ledger.post_journal(cur, entity_id, body.received_on, "receipt", rid, f"Receipt {receipt_no}",
                              ledger.receipt_lines(amount=body.amount, allocated=allocated, party_id=body.party_id,
                                                   method=body.method, bank_account_key=bank_key))
    cur.execute("UPDATE receipts SET journal_entry_id = %s WHERE id = %s", (jid, rid))
    out = {"id": str(rid), "receipt_no": receipt_no, "party_id": str(body.party_id), "received_on": body.received_on.isoformat(),
           "amount": money(body.amount), "method": body.method, "utr": body.utr, "status": "confirmed",
           "allocations": [{"invoice_id": str(a.invoice_id), "amount": money(a.amount)} for a in body.allocations],
           "unallocated": money(body.amount - allocated)}
    audit(cur, entity_id, "receipt.created", "receipts", rid, after=out)
    emit(cur, entity_id, "receipt.created", "receipts", rid, {"amount": out["amount"]})
    return out


@router.post(E + "/receipts", status_code=201, tags=["Sales & billing"])
def create_receipt(entity_id: uuid.UUID, body: ReceiptIn, p: Principal = Auth,
                   idempotency_key: str | None = Header(default=None, alias="Idempotency-Key")):
    if sum((a.amount for a in body.allocations), Decimal("0")) > body.amount:
        raise ApiError(400, "validation_failed", "Allocations exceed the receipt amount")
    with tx(p.user_id) as cur:
        require(cur, entity_id, "sales:write")
        replay = idempotency_begin(cur, idempotency_key, "POST", f"/entities/{entity_id}/receipts", body.model_dump())
        if replay:
            return JSONResponse(replay["body"], status_code=replay["status"])
        out = record_receipt(cur, entity_id, body)
        idempotency_finish(cur, idempotency_key, 201, out)
    return JSONResponse(out, status_code=201)


@router.post(E + "/receipts/{receipt_id}/allocate", tags=["Sales & billing"])
def allocate_receipt(entity_id: uuid.UUID, receipt_id: uuid.UUID, body: list[AllocationIn], p: Principal = Auth,
                     idempotency_key: str | None = Header(default=None, alias="Idempotency-Key")):
    """Settle invoices from what is left of a receipt. The advance on the ledger moves to receivables."""
    if not body:
        raise ApiError(400, "validation_failed", "Give at least one invoice to settle")
    path = f"/entities/{entity_id}/receipts/{receipt_id}/allocate"
    with tx(p.user_id) as cur:
        require(cur, entity_id, "sales:write")
        replay = idempotency_begin(cur, idempotency_key, "POST", path, [a.model_dump() for a in body])
        if replay:
            return JSONResponse(replay["body"], status_code=replay["status"])
        cur.execute("SELECT * FROM receipts WHERE id = %s AND entity_id = %s FOR UPDATE", (receipt_id, entity_id))
        rc = cur.fetchone()
        if not rc:
            raise ApiError(404, "not_found", "Not found")
        if rc["status"] != "confirmed":
            raise ApiError(409, "conflict", f"A {rc['status']} receipt cannot be allocated")
        cur.execute("SELECT coalesce(sum(amount), 0) AS s FROM invoice_settlements WHERE receipt_id = %s", (receipt_id,))
        left = rc["amount"] - cur.fetchone()["s"]
        total = sum((a.amount for a in body), Decimal("0"))
        if total > left:
            raise ApiError(409, "over_settlement", "Allocation exceeds what is left of the receipt", f"{money(left)} is unallocated")
        today = dt.date.today()
        for a in body:
            cur.execute("SELECT balance_due FROM sales_documents WHERE id = %s AND entity_id = %s AND party_id = %s AND status = 'issued' FOR UPDATE",
                        (a.invoice_id, entity_id, rc["party_id"]))
            inv = cur.fetchone()
            if not inv:
                raise ApiError(400, "validation_failed", "Allocation must target an issued invoice of the same customer",
                               errors=[{"field": "invoice_id", "message": str(a.invoice_id)}])
            if a.amount > inv["balance_due"]:
                raise ApiError(409, "over_settlement", "Allocation exceeds the invoice balance", f"invoice {a.invoice_id} owes {money(inv['balance_due'])}")
            cur.execute("""INSERT INTO invoice_settlements (entity_id, invoice_id, source_kind, receipt_id, amount, settled_on)
                           VALUES (%s, %s, 'receipt', %s, %s, %s)""", (entity_id, a.invoice_id, receipt_id, a.amount, today))
            cur.execute("SELECT payment_status FROM sales_documents WHERE id = %s", (a.invoice_id,))
            if cur.fetchone()["payment_status"] == "paid":
                emit(cur, entity_id, "sales_document.paid", "sales_documents", a.invoice_id)
        ledger.post_journal(cur, entity_id, today, "receipt", receipt_id, f"Receipt {rc['receipt_no']} allocated",
                            [ledger.Line("advances_received", debit=total, party_id=rc["party_id"]), ledger.Line("receivable", credit=total, party_id=rc["party_id"])])
        cur.execute("UPDATE receipts SET is_advance = (%s > 0) WHERE id = %s", (left - total, receipt_id))
        audit(cur, entity_id, "receipt.allocated", "receipts", receipt_id, after={"amount": money(total)})
        from .routes_billing import RECEIPT_SQL
        from .routes_orders import js
        cur.execute(RECEIPT_SQL + " WHERE r.id = %s", (receipt_id,))
        out = js(cur.fetchone())
        idempotency_finish(cur, idempotency_key, 200, out)
    return JSONResponse(out)
