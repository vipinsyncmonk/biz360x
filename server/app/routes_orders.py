"""Quotes, sales orders, credit notes, payment links and the public pay page."""
from __future__ import annotations

import collections
import datetime as dt
import json
import os
import secrets
import time
import uuid
from decimal import Decimal

from fastapi import APIRouter, Header, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from .core import (ApiError, Auth, Principal, audit, emit, idempotency_begin, idempotency_finish, jsonable, money, require, tx)
from .routes_sales import LineIn, SalesDocIn, _context, create_draft, load_document
from . import tax

router = APIRouter()
E = "/entities/{entity_id}"
PAY_BASE = os.environ.get("PAY_BASE_URL", "https://pay.biz360x.example")


# ------------------------------------------------------------------ paging
def page(rows: list, limit: int, offset: int) -> dict:
    more = len(rows) > limit
    return {"data": rows[:limit], "next_cursor": str(offset + limit) if more else None}


def offset_of(cursor: str | None) -> int:
    try:
        return int(cursor) if cursor else 0
    except ValueError:
        raise ApiError(400, "validation_failed", "Bad cursor")


def js(x):
    return json.loads(json.dumps(x, default=jsonable))


def next_number(cur, entity_id, table: str, column: str, prefix: str, width: int) -> str:
    cur.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (f"{table}:{entity_id}",))
    cur.execute(f"SELECT coalesce(max(substr({column}, %s)::int), 0) + 1 AS n FROM {table} WHERE entity_id = %s AND {column} ~ %s",
                (len(prefix) + 1, entity_id, f"^{prefix}[0-9]+$"))
    return f"{prefix}{cur.fetchone()['n']:0{width}d}"


def default_party_gst(cur, party_id):
    cur.execute("SELECT id FROM party_gst_registrations WHERE party_id = %s ORDER BY is_default DESC, id LIMIT 1", (party_id,))
    r = cur.fetchone()
    return r["id"] if r else None


# ------------------------------------------------------------------ quotes
class QuoteIn(BaseModel):
    party_id: uuid.UUID
    registration_id: uuid.UUID
    title: str | None = None
    place_of_supply: str | None = Field(default=None, pattern=r"^[0-9]{2}$")
    valid_until: dt.date | None = None
    advance_pct: Decimal | None = Field(default=None, ge=0, le=100)
    payment_terms_days: int | None = None
    customer_po_ref: str | None = None
    lines: list[LineIn] = Field(min_length=1)


def _quote_totals(cur, entity_id, body: QuoteIn):
    doc = SalesDocIn(doc_type="tax_invoice", doc_date=dt.date.today(), registration_id=body.registration_id, party_id=body.party_id,
                     party_gst_id=default_party_gst(cur, body.party_id), place_of_supply=body.place_of_supply,
                     payment_terms_days=body.payment_terms_days, lines=body.lines)
    ctx, lines, _, _, _ = _context(cur, entity_id, doc)
    return tax.compute(lines, ctx), ctx


def _insert_quote(cur, entity_id, body: QuoteIn, quote_no: str, version: int, supersedes=None, owner=True) -> uuid.UUID:
    res, ctx = _quote_totals(cur, entity_id, body)
    tax_total = res.cgst_total + res.sgst_total + res.igst_total
    cur.execute("""INSERT INTO quotes (entity_id, registration_id, party_id, quote_no, version, supersedes_id, title, place_of_supply,
                          valid_until, advance_pct, payment_terms_days, taxable_total, tax_total, grand_total, customer_po_ref, owner_user_id)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, app.current_user_id()) RETURNING id""",
                (entity_id, body.registration_id, body.party_id, quote_no, version, supersedes, body.title, ctx.place_of_supply,
                 body.valid_until, body.advance_pct, ctx.payment_terms_days, res.taxable_total, tax_total, res.grand_total, body.customer_po_ref))
    qid = cur.fetchone()["id"]
    for i, (l, r) in enumerate(zip(body.lines, res.lines), 1):
        gross = l.qty * l.rate
        pct = (l.discount * 100 / gross).quantize(Decimal("0.01")) if gross else Decimal(0)
        cur.execute("""INSERT INTO quote_lines (quote_id, line_no, item_id, description, hsn_sac, qty, uqc, rate, discount_pct, gst_rate)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                    (qid, i, l.item_id, l.description, l.hsn_sac, l.qty, l.uqc, l.rate, pct, l.gst_rate))
    return qid


def load_quote(cur, entity_id, qid) -> dict:
    cur.execute("""SELECT id, quote_no, version, party_id, registration_id, title, status, place_of_supply, valid_until, advance_pct,
                          payment_terms_days, taxable_total, tax_total, grand_total, view_count, customer_po_ref, sent_at, accepted_at,
                          lost_reason FROM quotes WHERE id = %s AND entity_id = %s""", (qid, entity_id))
    q = cur.fetchone()
    if not q:
        raise ApiError(404, "not_found", "Not found")
    cur.execute("""SELECT line_no, item_id, description, hsn_sac, qty, uqc, rate, gst_rate, (qty * rate - taxable_value) AS discount,
                          taxable_value FROM quote_lines WHERE quote_id = %s ORDER BY line_no""", (qid,))
    q["lines"] = cur.fetchall()
    cur.execute("""SELECT so.id, coalesce(sum(l.invoiced_qty) * 100 / nullif(sum(l.ordered_qty), 0), 0) AS pct
                     FROM sales_orders so LEFT JOIN sales_order_lines l ON l.order_id = so.id WHERE so.quote_id = %s GROUP BY so.id""", (qid,))
    so = cur.fetchone()
    q["sales_order_id"] = so["id"] if so else None
    q["invoiced_pct"] = float(round(so["pct"], 1)) if so else 0
    return js(q)


@router.post(E + "/quotes", status_code=201, tags=["Sales & billing"])
def create_quote(entity_id: uuid.UUID, body: QuoteIn, p: Principal = Auth):
    with tx(p.user_id) as cur:
        require(cur, entity_id, "sales:write")
        qid = _insert_quote(cur, entity_id, body, next_number(cur, entity_id, "quotes", "quote_no", "Q-", 4), 1)
        audit(cur, entity_id, "quote.created", "quotes", qid)
        return load_quote(cur, entity_id, qid)


@router.get(E + "/quotes", tags=["Sales & billing"])
def list_quotes(entity_id: uuid.UUID, p: Principal = Auth, status: str | None = None, party_id: uuid.UUID | None = None,
                limit: int = Query(default=50, ge=1, le=200), cursor: str | None = None):
    off = offset_of(cursor)
    with tx(p.user_id) as cur:
        require(cur, entity_id, "sales:read")
        cur.execute("""SELECT id FROM quotes WHERE entity_id = %s AND (%s::text IS NULL OR status = %s) AND (%s::uuid IS NULL OR party_id = %s)
                        ORDER BY created_at DESC, id LIMIT %s OFFSET %s""", (entity_id, status, status, party_id, party_id, limit + 1, off))
        return page([load_quote(cur, entity_id, r["id"]) for r in cur.fetchall()], limit, off)


@router.get(E + "/quotes/{quote_id}", tags=["Sales & billing"])
def get_quote(entity_id: uuid.UUID, quote_id: uuid.UUID, p: Principal = Auth):
    with tx(p.user_id) as cur:
        require(cur, entity_id, "sales:read")
        return load_quote(cur, entity_id, quote_id)


def _quote_for_update(cur, entity_id, quote_id, allowed: tuple[str, ...]):
    cur.execute("SELECT * FROM quotes WHERE id = %s AND entity_id = %s FOR UPDATE", (quote_id, entity_id))
    q = cur.fetchone()
    if not q:
        raise ApiError(404, "not_found", "Not found")
    if q["status"] not in allowed:
        raise ApiError(409, "conflict", f"Quote is {q['status']}; allowed from: {', '.join(allowed)}")
    return q


class SendIn(BaseModel):
    channels: list[str] = []
    to: list[str] = []
    message: str | None = None


@router.post(E + "/quotes/{quote_id}/send", tags=["Sales & billing"])
def send_quote(entity_id: uuid.UUID, quote_id: uuid.UUID, body: SendIn, p: Principal = Auth):
    with tx(p.user_id) as cur:
        require(cur, entity_id, "sales:write")
        _quote_for_update(cur, entity_id, quote_id, ("draft", "sent", "viewed", "negotiating"))
        cur.execute("UPDATE quotes SET status = 'sent', sent_at = now() WHERE id = %s", (quote_id,))
        audit(cur, entity_id, "quote.sent", "quotes", quote_id, after={"channels": body.channels, "to": body.to})
        return load_quote(cur, entity_id, quote_id)


@router.post(E + "/quotes/{quote_id}/revise", status_code=201, tags=["Sales & billing"])
def revise_quote(entity_id: uuid.UUID, quote_id: uuid.UUID, p: Principal = Auth):
    with tx(p.user_id) as cur:
        require(cur, entity_id, "sales:write")
        q = _quote_for_update(cur, entity_id, quote_id, ("draft", "sent", "viewed", "negotiating"))
        cur.execute("SELECT 1 FROM quotes WHERE entity_id = %s AND quote_no = %s AND version > %s", (entity_id, q["quote_no"], q["version"]))
        if cur.fetchone():
            raise ApiError(409, "conflict", "A newer version already exists")
        old = load_quote(cur, entity_id, quote_id)
        body = QuoteIn(party_id=q["party_id"], registration_id=q["registration_id"], title=q["title"], place_of_supply=q["place_of_supply"],
                       valid_until=q["valid_until"], advance_pct=q["advance_pct"], payment_terms_days=q["payment_terms_days"],
                       customer_po_ref=q["customer_po_ref"],
                       lines=[LineIn(item_id=l["item_id"], description=l["description"], hsn_sac=l["hsn_sac"], qty=Decimal(l["qty"]),
                                     rate=Decimal(l["rate"]), gst_rate=Decimal(str(l["gst_rate"])), discount=Decimal(l["discount"]), uqc=l["uqc"])
                              for l in old["lines"]])
        nid = _insert_quote(cur, entity_id, body, q["quote_no"], q["version"] + 1, supersedes=quote_id)
        cur.execute("UPDATE quotes SET status = 'negotiating' WHERE id = %s", (quote_id,))
        audit(cur, entity_id, "quote.revised", "quotes", nid, after={"quote_no": q["quote_no"], "version": q["version"] + 1})
        return load_quote(cur, entity_id, nid)


class AcceptIn(BaseModel):
    accepted_at: dt.datetime | None = None
    customer_po_ref: str | None = None


def load_order(cur, entity_id, oid) -> dict:
    cur.execute("""SELECT id, order_no, quote_id, party_id, order_date, customer_po_ref, status FROM sales_orders
                    WHERE id = %s AND entity_id = %s""", (oid, entity_id))
    o = cur.fetchone()
    if not o:
        raise ApiError(404, "not_found", "Not found")
    cur.execute("""SELECT id, line_no, description, hsn_sac, ordered_qty, invoiced_qty, rate, gst_rate FROM sales_order_lines
                    WHERE order_id = %s ORDER BY line_no""", (oid,))
    o["lines"] = cur.fetchall()
    return js(o)


@router.post(E + "/quotes/{quote_id}/accept", status_code=201, tags=["Sales & billing"])
def accept_quote(entity_id: uuid.UUID, quote_id: uuid.UUID, body: AcceptIn, p: Principal = Auth,
                 idempotency_key: str | None = Header(default=None, alias="Idempotency-Key")):
    with tx(p.user_id) as cur:
        require(cur, entity_id, "sales:write")
        replay = idempotency_begin(cur, idempotency_key, "POST", f"/entities/{entity_id}/quotes/{quote_id}/accept", body.model_dump(mode="json"))
        if replay:
            return JSONResponse(replay["body"], status_code=replay["status"])
        q = _quote_for_update(cur, entity_id, quote_id, ("sent", "viewed", "negotiating"))
        when = body.accepted_at or dt.datetime.now(dt.timezone.utc)
        po = body.customer_po_ref or q["customer_po_ref"]
        cur.execute("UPDATE quotes SET status = 'accepted', accepted_at = %s, customer_po_ref = %s WHERE id = %s", (when, po, quote_id))
        cur.execute("""INSERT INTO sales_orders (entity_id, quote_id, party_id, order_no, order_date, customer_po_ref)
                       VALUES (%s, %s, %s, %s, %s, %s) RETURNING id""",
                    (entity_id, quote_id, q["party_id"], next_number(cur, entity_id, "sales_orders", "order_no", "SO-", 4), when.date(), po))
        oid = cur.fetchone()["id"]
        cur.execute("""INSERT INTO sales_order_lines (order_id, line_no, item_id, description, hsn_sac, ordered_qty, rate, gst_rate)
                       SELECT %s, line_no, item_id, description, hsn_sac, qty, round(taxable_value / qty, 2), gst_rate
                         FROM quote_lines WHERE quote_id = %s""", (oid, quote_id))
        audit(cur, entity_id, "quote.accepted", "quotes", quote_id, after={"sales_order_id": oid})
        out = load_order(cur, entity_id, oid)
        idempotency_finish(cur, idempotency_key, 201, out)
    return JSONResponse(out, status_code=201)


class LostIn(BaseModel):
    reason: str | None = None


@router.post(E + "/quotes/{quote_id}/mark-lost", tags=["Sales & billing"])
def mark_quote_lost(entity_id: uuid.UUID, quote_id: uuid.UUID, body: LostIn, p: Principal = Auth):
    with tx(p.user_id) as cur:
        require(cur, entity_id, "sales:write")
        _quote_for_update(cur, entity_id, quote_id, ("draft", "sent", "viewed", "negotiating"))
        cur.execute("UPDATE quotes SET status = 'lost', lost_reason = %s WHERE id = %s", (body.reason, quote_id))
        audit(cur, entity_id, "quote.lost", "quotes", quote_id, after={"reason": body.reason})
        return load_quote(cur, entity_id, quote_id)


# ------------------------------------------------------------------ sales orders
@router.get(E + "/sales-orders", tags=["Sales & billing"])
def list_orders(entity_id: uuid.UUID, p: Principal = Auth, status: str | None = None, limit: int = Query(default=50, ge=1, le=200),
                cursor: str | None = None):
    off = offset_of(cursor)
    with tx(p.user_id) as cur:
        require(cur, entity_id, "sales:read")
        cur.execute("SELECT id FROM sales_orders WHERE entity_id = %s AND (%s::text IS NULL OR status = %s) ORDER BY order_date DESC, id LIMIT %s OFFSET %s",
                    (entity_id, status, status, limit + 1, off))
        return page([load_order(cur, entity_id, r["id"]) for r in cur.fetchall()], limit, off)


@router.get(E + "/sales-orders/{order_id}", tags=["Sales & billing"])
def get_order(entity_id: uuid.UUID, order_id: uuid.UUID, p: Principal = Auth):
    with tx(p.user_id) as cur:
        require(cur, entity_id, "sales:read")
        return load_order(cur, entity_id, order_id)


class OrderLineQty(BaseModel):
    so_line_id: int
    qty: Decimal = Field(gt=0)


class InvoiceFromOrder(BaseModel):
    lines: list[OrderLineQty] | None = None
    doc_date: dt.date | None = None


@router.post(E + "/sales-orders/{order_id}/invoice", status_code=201, tags=["Sales & billing"])
def invoice_order(entity_id: uuid.UUID, order_id: uuid.UUID, body: InvoiceFromOrder, p: Principal = Auth):
    with tx(p.user_id) as cur:
        require(cur, entity_id, "sales:write")
        cur.execute("SELECT * FROM sales_orders WHERE id = %s AND entity_id = %s FOR UPDATE", (order_id, entity_id))
        so = cur.fetchone()
        if not so:
            raise ApiError(404, "not_found", "Not found")
        if so["status"] in ("invoiced", "closed", "cancelled"):
            raise ApiError(409, "conflict", f"Order is {so['status']}")
        cur.execute("SELECT * FROM sales_order_lines WHERE order_id = %s ORDER BY line_no FOR UPDATE", (order_id,))
        so_lines = {l["id"]: l for l in cur.fetchall()}
        wanted = {l.so_line_id: l.qty for l in body.lines} if body.lines else {i: l["ordered_qty"] - l["invoiced_qty"] for i, l in so_lines.items()}
        picked = []
        for lid, qty in wanted.items():
            l = so_lines.get(lid)
            if not l:
                raise ApiError(400, "validation_failed", f"so_line_id {lid} is not on this order")
            if qty > l["ordered_qty"] - l["invoiced_qty"]:
                raise ApiError(400, "validation_failed", f"Line {l['line_no']}: {qty} exceeds the {l['ordered_qty'] - l['invoiced_qty']} left to invoice")
            if qty > 0:
                picked.append((l, qty))
        if not picked:
            raise ApiError(409, "conflict", "Nothing left to invoice")
        if so["quote_id"]:
            cur.execute("SELECT registration_id, place_of_supply, payment_terms_days FROM quotes WHERE id = %s", (so["quote_id"],))
            q = cur.fetchone()
        else:
            cur.execute("SELECT id AS registration_id FROM entity_registrations WHERE entity_id = %s AND kind = 'gstin' AND status = 'active' ORDER BY number LIMIT 1", (entity_id,))
            q = cur.fetchone()
            if not q:
                raise ApiError(400, "validation_failed", "The entity has no GSTIN to invoice from")
            q.update(place_of_supply=None, payment_terms_days=None)
        doc = SalesDocIn(doc_type="tax_invoice", doc_date=body.doc_date or dt.date.today(), registration_id=q["registration_id"],
                         party_id=so["party_id"], party_gst_id=default_party_gst(cur, so["party_id"]), place_of_supply=q["place_of_supply"],
                         payment_terms_days=q["payment_terms_days"], customer_po_ref=so["customer_po_ref"], quote_id=so["quote_id"],
                         sales_order_id=order_id,
                         lines=[LineIn(item_id=l["item_id"], description=l["description"], hsn_sac=l["hsn_sac"], qty=qty, rate=l["rate"],
                                       gst_rate=l["gst_rate"], so_line_id=l["id"]) for l, qty in picked])
        doc_id = create_draft(cur, entity_id, doc)
        for l, qty in picked:
            cur.execute("UPDATE sales_order_lines SET invoiced_qty = invoiced_qty + %s WHERE id = %s", (qty, l["id"]))
        cur.execute("SELECT bool_and(invoiced_qty = ordered_qty) AS done FROM sales_order_lines WHERE order_id = %s", (order_id,))
        cur.execute("UPDATE sales_orders SET status = %s WHERE id = %s", ("invoiced" if cur.fetchone()["done"] else "partially_invoiced", order_id))
        return load_document(cur, entity_id, doc_id)


# ------------------------------------------------------------------ credit notes
class CreditNoteIn(BaseModel):
    cn_reason: str
    gst_adjusted: bool = True
    agreed_before_supply: bool | None = None
    lines: list[LineIn] = Field(min_length=1)


@router.post(E + "/sales-documents/{doc_id}/credit-notes", status_code=201, tags=["Sales & billing"])
def create_credit_note(entity_id: uuid.UUID, doc_id: uuid.UUID, body: CreditNoteIn, p: Principal = Auth):
    with tx(p.user_id) as cur:
        require(cur, entity_id, "sales:write")
        cur.execute("SELECT * FROM sales_documents WHERE id = %s AND entity_id = %s FOR UPDATE", (doc_id, entity_id))
        o = cur.fetchone()
        if not o:
            raise ApiError(404, "not_found", "Not found")
        if o["status"] != "issued" or o["doc_type"] not in ("tax_invoice", "export_invoice", "bill_of_supply"):
            raise ApiError(409, "conflict", "Credit notes can only be raised against an issued invoice")
        cur.execute("SELECT id, qty, taxable_value FROM sales_document_lines WHERE document_id = %s", (doc_id,))
        orig = {l["id"]: l for l in cur.fetchall()}
        cur.execute("""SELECT coalesce(sum(cl.taxable_value), 0) AS t FROM sales_document_lines cl JOIN sales_documents c ON c.id = cl.document_id
                        WHERE c.original_document_id = %s AND c.doc_type = 'credit_note' AND c.status <> 'cancelled'""", (doc_id,))
        credited = cur.fetchone()["t"]
        new_total = sum((tax.r2(l.qty * l.rate - l.discount) for l in body.lines), Decimal(0))
        if credited + new_total > o["taxable_total"]:
            raise ApiError(400, "validation_failed", f"Credit notes would total {credited + new_total} of the invoice's {o['taxable_total']} taxable value")
        for l in body.lines:
            if l.original_line_id is not None and (l.original_line_id not in orig or l.qty > orig[l.original_line_id]["qty"]):
                raise ApiError(400, "validation_failed", "original_line_id is not on the invoice, or qty exceeds what was supplied")
        cn = SalesDocIn(doc_type="credit_note", doc_date=dt.date.today(), registration_id=o["registration_id"], party_id=o["party_id"],
                        party_gst_id=o["party_gst_id"], place_of_supply=o["place_of_supply"], supply_type=o["supply_type"],
                        payment_terms_days=0, original_document_id=doc_id, cn_reason=body.cn_reason, gst_adjusted=body.gst_adjusted,
                        lines=body.lines)
        return load_document(cur, entity_id, create_draft(cur, entity_id, cn))


# ------------------------------------------------------------------ payment links
class PaymentLinkIn(BaseModel):
    amount: Decimal | None = Field(default=None, gt=0)
    methods: list[str] | None = None
    expires_at: dt.datetime | None = None


def _link_out(cur, link: dict) -> dict:
    cur.execute("""SELECT v.va_number, v.ifsc, b.bank_name FROM virtual_accounts v JOIN bank_accounts b ON b.id = v.bank_account_id
                    WHERE v.sales_document_id = %s AND v.status = 'active'""", (link["sales_document_id"],))
    va = cur.fetchone()
    return js({"id": link["id"], "sales_document_id": link["sales_document_id"], "short_code": link["short_code"],
               "url": f"{PAY_BASE}/{link['short_code']}", "amount": link["amount"], "methods": link["methods"], "virtual_account": va,
               "status": link["status"], "first_viewed_at": link["first_viewed_at"], "expires_at": link["expires_at"]})


@router.post(E + "/sales-documents/{doc_id}/payment-links", status_code=201, tags=["Sales & billing"])
def create_payment_link(entity_id: uuid.UUID, doc_id: uuid.UUID, body: PaymentLinkIn, p: Principal = Auth,
                        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key")):
    with tx(p.user_id) as cur:
        require(cur, entity_id, "sales:write")
        replay = idempotency_begin(cur, idempotency_key, "POST", f"/entities/{entity_id}/sales-documents/{doc_id}/payment-links",
                                   body.model_dump(mode="json"))
        if replay:
            return JSONResponse(replay["body"], status_code=replay["status"])
        cur.execute("SELECT status, balance_due FROM sales_documents WHERE id = %s AND entity_id = %s", (doc_id, entity_id))
        d = cur.fetchone()
        if not d:
            raise ApiError(404, "not_found", "Not found")
        if d["status"] != "issued" or d["balance_due"] <= 0:
            raise ApiError(409, "conflict", "A payment link needs an issued invoice with a balance due")
        amount = body.amount or d["balance_due"]
        if amount > d["balance_due"]:
            raise ApiError(400, "validation_failed", "Amount exceeds the balance due")
        cur.execute("""INSERT INTO payment_links (entity_id, sales_document_id, short_code, amount, methods, expires_at)
                       VALUES (%s, %s, %s, %s, %s, %s) RETURNING *""",
                    (entity_id, doc_id, secrets.token_urlsafe(6).replace("_", "x").replace("-", "y"), amount,
                     body.methods or ["bank_transfer", "upi", "netbanking"],
                     body.expires_at or dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=30)))
        link = cur.fetchone()
        cur.execute("""SELECT id, ifsc FROM bank_accounts WHERE entity_id = %s AND is_active AND ifsc IS NOT NULL
                        ORDER BY (kind = 'current') DESC, id LIMIT 1""", (entity_id,))
        bank = cur.fetchone()
        if bank:                                         # one virtual account per invoice: payments to it match automatically
            cur.execute("SELECT 1 FROM virtual_accounts WHERE sales_document_id = %s", (doc_id,))
            if not cur.fetchone():
                cur.execute("INSERT INTO virtual_accounts (entity_id, bank_account_id, va_number, ifsc, sales_document_id) VALUES (%s, %s, %s, %s, %s)",
                            (entity_id, bank["id"], "BZX" + str(doc_id.int % 10**12).zfill(12), bank["ifsc"], doc_id))
        out = _link_out(cur, link)
        audit(cur, entity_id, "payment_link.created", "payment_links", link["id"], after={"short_code": link["short_code"], "amount": amount})
        idempotency_finish(cur, idempotency_key, 201, out)
    return JSONResponse(out, status_code=201)


# ------------------------------------------------------------------ public pay page (no authentication)
_hits: dict[str, collections.deque] = collections.defaultdict(collections.deque)
RATE_PER_MIN = int(os.environ.get("PUBLIC_RATE_PER_MIN", "30"))


def rate_limit(request: Request) -> None:
    """30 requests/minute per client IP (in-process; put a shared limiter in front when running several replicas)."""
    ip = request.client.host if request.client else "?"
    q, now = _hits[ip], time.monotonic()
    while q and now - q[0] > 60:
        q.popleft()
    if len(q) >= RATE_PER_MIN:
        raise ApiError(429, "rate_limited", "Too many requests", "Try again in a minute")
    q.append(now)


def _public_link(cur, code: str) -> dict:
    cur.execute("""SELECT l.id AS link_id, l.entity_id, l.short_code, l.amount, l.methods, l.status AS link_status, l.expires_at, l.first_viewed_at,
                          d.id AS doc_id, d.number, d.doc_date, d.due_date, d.taxable_total,
                          (d.cgst_total + d.sgst_total + d.igst_total + d.cess_total) AS tax_total, d.balance_due, d.status AS doc_status,
                          d.buyer_legal_name, d.irn_status::text AS irn_status, e.legal_name AS supplier_name, r.number AS supplier_gstin
                     FROM payment_links l JOIN sales_documents d ON d.id = l.sales_document_id JOIN entities e ON e.id = l.entity_id
                     JOIN entity_registrations r ON r.id = d.registration_id WHERE l.short_code = %s""", (code,))
    r = cur.fetchone()
    if not r:
        raise ApiError(404, "not_found", "Not found")
    return r


@router.get("/pay/{short_code}", tags=["Public pay"])
def public_invoice(short_code: str, request: Request):
    rate_limit(request)
    with tx(None, role="biz_worker") as cur:          # public, read-only, exposes only what is printed on the invoice
        r = _public_link(cur, short_code)
        if not r["first_viewed_at"]:
            cur.execute("UPDATE payment_links SET first_viewed_at = now() WHERE id = %s", (r["link_id"],))
        cur.execute("""SELECT v.va_number, v.ifsc, b.bank_name FROM virtual_accounts v JOIN bank_accounts b ON b.id = v.bank_account_id
                        WHERE v.sales_document_id = %s AND v.status = 'active'""", (r["doc_id"],))
        va = cur.fetchone()
    expired = r["expires_at"] and r["expires_at"] < dt.datetime.now(dt.timezone.utc)
    status = "cancelled" if r["doc_status"] == "cancelled" or r["link_status"] in ("cancelled", "expired") or expired \
        else "paid" if r["balance_due"] <= 0 or r["link_status"] == "paid" else "due"
    return js({"supplier_name": r["supplier_name"], "supplier_gstin": r["supplier_gstin"], "buyer_name": r["buyer_legal_name"],
               "number": r["number"], "doc_date": r["doc_date"], "due_date": r["due_date"], "taxable_total": money(r["taxable_total"]),
               "tax_total": money(r["tax_total"]), "amount_due": money(r["balance_due"]),
               "days_overdue": max(0, (dt.date.today() - r["due_date"]).days) if r["due_date"] and status == "due" else 0,
               "irn_verified": r["irn_status"] == "generated", "pdf_url": f"{PAY_BASE}/{short_code}/invoice.pdf", "methods": r["methods"],
               "virtual_account": va, "status": status})


class IntentIn(BaseModel):
    method: str = Field(pattern="^(upi|netbanking|card|bank_transfer)$")
    return_url: str | None = None


@router.post("/pay/{short_code}/intents", status_code=201, tags=["Public pay"])
def create_intent(short_code: str, body: IntentIn, request: Request):
    rate_limit(request)
    with tx(None, role="biz_worker") as cur:
        r = _public_link(cur, short_code)
        if r["balance_due"] <= 0 or r["link_status"] != "active":
            raise ApiError(409, "conflict", "This invoice is not payable")
        if body.method not in r["methods"]:
            raise ApiError(409, "conflict", f"{body.method} is not enabled for this invoice")
        cur.execute("SELECT va_number, ifsc FROM virtual_accounts WHERE sales_document_id = %s AND status = 'active'", (r["doc_id"],))
        va = cur.fetchone()
        ref = "PI-" + secrets.token_hex(5).upper()
        audit(cur, r["entity_id"], "payment_intent.created", "payment_links", r["link_id"], after={"method": body.method, "reference": ref},
              actor_kind="integration")
    if body.method == "bank_transfer":
        text = (f"Transfer {money(r['balance_due'])} by NEFT/RTGS/IMPS to account {va['va_number']} (IFSC {va['ifsc']}). "
                "This account is unique to the invoice, so the payment is matched automatically.") if va else \
               "Pay by bank transfer to the account printed on the invoice and quote the invoice number."
        return {"redirect_url": None, "upi_intent": None, "instructions": text, "reference": ref}
    return {"redirect_url": None, "upi_intent": None, "reference": ref,
            "instructions": f"{body.method} needs a payment-gateway adapter, which is not configured. Use bank transfer."}


class QueryIn(BaseModel):
    name: str | None = None
    email: str
    message: str = Field(max_length=2000)


@router.post("/pay/{short_code}/queries", status_code=204, tags=["Public pay"])
def raise_query(short_code: str, body: QueryIn, request: Request):
    rate_limit(request)
    with tx(None, role="biz_worker") as cur:
        r = _public_link(cur, short_code)
        cur.execute("""INSERT INTO tasks (entity_id, title, subject, subject_id, source) VALUES (%s, %s, 'sales_document', %s, 'user') RETURNING id""",
                    (r["entity_id"], f"Invoice query on {r['number']} from {body.email}: {body.message[:120]}", r["doc_id"]))
        audit(cur, r["entity_id"], "invoice_query.raised", "sales_documents", r["doc_id"], after={"email": body.email}, actor_kind="integration")
    return None
