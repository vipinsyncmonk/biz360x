"""Read endpoints the billing screens need: GSTIN registrations, customers, the invoice list and the sales summary."""
from __future__ import annotations

import datetime as dt
import uuid
from typing import Literal

from fastapi import APIRouter, Query
from pydantic import BaseModel, Field

from . import engine
from .core import ApiError, Auth, Principal, audit, emit, money, require, tx
from .routes_orders import js, offset_of, page

router = APIRouter()
E = "/entities/{entity_id}"


def _like(q: str | None) -> str | None:
    if not q:
        return None
    return "%" + q.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


def current_fy_start(today: dt.date) -> dt.date:
    return dt.date(today.year if today.month >= 4 else today.year - 1, 4, 1)


@router.get(E + "/registrations", tags=["Entities"])
def list_registrations(entity_id: uuid.UUID, kind: str | None = None, limit: int = Query(default=100, ge=1, le=200), cursor: str | None = None, p: Principal = Auth):
    off = offset_of(cursor)
    with tx(p.user_id) as cur:
        require(cur, entity_id, "settings:read")
        cur.execute("""SELECT id, kind::text, number, state_code, label, gst_filing_frequency, valid_from, valid_until, status
                         FROM entity_registrations WHERE entity_id = %s AND (%s::text IS NULL OR kind::text = %s)
                        ORDER BY kind, number LIMIT %s OFFSET %s""", (entity_id, kind, kind, limit + 1, off))
        return js(page(cur.fetchall(), limit, off))


@router.get(E + "/parties", tags=["Parties"])
def list_parties(entity_id: uuid.UUID, role: str | None = Query(default=None, pattern="^(customer|vendor)$"), q: str | None = None,
                 limit: int = Query(default=50, ge=1, le=200), cursor: str | None = None, p: Principal = Auth):
    off = offset_of(cursor)
    with tx(p.user_id) as cur:
        require(cur, entity_id, "parties:read")
        cur.execute("""SELECT pa.id, pa.legal_name, pa.display_name, pa.is_customer, pa.is_vendor, pa.pan, pa.payment_terms_days, pa.status, pa.country, pa.currency,
                              coalesce((SELECT json_agg(json_build_object('id', g.id, 'gstin', g.gstin, 'state_code', g.state_code,
                                                                          'taxpayer_type', g.taxpayer_type::text, 'is_default', g.is_default) ORDER BY g.is_default DESC, g.gstin)
                                          FROM party_gst_registrations g WHERE g.party_id = pa.id), '[]'::json) AS gstins,
                              coalesce((SELECT sum(d.balance_due) FROM sales_documents d WHERE d.party_id = pa.id AND d.status = 'issued'
                                           AND d.doc_type IN ('tax_invoice', 'export_invoice', 'bill_of_supply', 'debit_note')), 0) AS balance_receivable
                         FROM parties pa
                        WHERE pa.entity_id = %s AND pa.status <> 'archived'
                          AND (%s::text IS NULL OR (%s = 'customer' AND pa.is_customer) OR (%s = 'vendor' AND pa.is_vendor))
                          AND (%s::text IS NULL OR pa.legal_name ILIKE %s OR pa.display_name ILIKE %s)
                        ORDER BY pa.legal_name, pa.id LIMIT %s OFFSET %s""", (entity_id, role, role, role, q, _like(q), _like(q), limit + 1, off))
        rows = cur.fetchall()
    return js(page(rows, limit, off))


GSTIN = r"^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][1-9A-Z]Z[0-9A-Z]$"


class PartyIn(BaseModel):
    legal_name: str = Field(min_length=2, max_length=200)
    is_customer: bool = True
    is_vendor: bool = False
    country: str = Field(default="IN", pattern=r"^[A-Z]{2}$")
    currency: str | None = Field(default=None, pattern=r"^[A-Z]{3}$")           # what an overseas customer is invoiced in
    gstin: str | None = Field(default=None, pattern=GSTIN)
    pan: str | None = Field(default=None, pattern=r"^[A-Z]{5}[0-9]{4}[A-Z]$")
    payment_terms_days: int | None = Field(default=None, ge=0, le=365)


@router.post(E + "/parties", status_code=201, tags=["Parties"])
def create_party(entity_id: uuid.UUID, body: PartyIn, p: Principal = Auth):
    """A customer or vendor. One outside India has no GSTIN and is invoiced in a foreign currency, which is what turns its invoices into export invoices."""
    name = body.legal_name.strip()
    if not (body.is_customer or body.is_vendor):
        raise ApiError(400, "validation_failed", "A party is a customer, a vendor or both", errors=[{"field": "is_customer", "message": "choose at least one"}])
    overseas = body.country != "IN"
    if overseas and body.gstin:
        raise ApiError(400, "validation_failed", "A customer outside India has no GSTIN", errors=[{"field": "gstin", "message": "leave it empty for an overseas party"}])
    if overseas and (not body.currency or body.currency == "INR"):
        raise ApiError(400, "validation_failed", "Choose the currency this customer is invoiced in", errors=[{"field": "currency", "message": "for example USD, EUR or GBP"}])
    if not overseas and body.currency and body.currency != "INR":
        raise ApiError(400, "validation_failed", "Only a customer outside India is invoiced in a foreign currency", errors=[{"field": "currency", "message": "leave it as INR for an Indian party"}])
    with tx(p.user_id) as cur:
        require(cur, entity_id, "parties:write")
        cur.execute("SELECT 1 FROM parties WHERE entity_id = %s AND lower(legal_name) = lower(%s) AND status <> 'archived'", (entity_id, name))
        if cur.fetchone():
            raise ApiError(409, "conflict", "A party with that name already exists", "Open it instead of adding it again.")
        state = body.gstin[:2] if body.gstin else None
        if state:
            cur.execute("SELECT 1 FROM ref_states WHERE gst_code = %s", (state,))
            if not cur.fetchone():
                raise ApiError(400, "validation_failed", "That GSTIN starts with an unknown state code", errors=[{"field": "gstin", "message": "check the first two digits"}])
        pan = body.pan or (body.gstin[2:12] if body.gstin else None)
        cur.execute("""INSERT INTO parties (entity_id, legal_name, is_customer, is_vendor, pan, country, currency, payment_terms_days)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, coalesce(%s, 30)) RETURNING id""",
                    (entity_id, name, body.is_customer, body.is_vendor, pan, body.country, body.currency or "INR", body.payment_terms_days))
        pid = cur.fetchone()["id"]
        if body.gstin or overseas:
            cur.execute("""INSERT INTO party_gst_registrations (party_id, gstin, state_code, taxpayer_type, is_default) VALUES (%s, %s, %s, %s, true)""",
                        (pid, body.gstin, state or "96", "overseas" if overseas else "regular"))
        audit(cur, entity_id, "party.created", "parties", pid, after={"legal_name": name, "country": body.country, "currency": body.currency or "INR"})
        emit(cur, entity_id, "party.created", "parties", pid, {"legal_name": name})
        cur.execute("""SELECT pa.id, pa.legal_name, pa.is_customer, pa.is_vendor, pa.pan, pa.payment_terms_days, pa.status, pa.country, pa.currency,
                              coalesce((SELECT json_agg(json_build_object('id', g.id, 'gstin', g.gstin, 'state_code', g.state_code, 'taxpayer_type', g.taxpayer_type::text, 'is_default', g.is_default))
                                          FROM party_gst_registrations g WHERE g.party_id = pa.id), '[]'::json) AS gstins FROM parties pa WHERE pa.id = %s""", (pid,))
        return js(cur.fetchone())


class RegistrationIn(BaseModel):
    kind: Literal["lut", "iec"]
    number: str = Field(pattern=r"^[A-Z0-9]{10,20}$")
    valid_from: dt.date | None = None
    valid_until: dt.date | None = None
    label: str | None = Field(default=None, max_length=80)


@router.post(E + "/registrations", status_code=201, tags=["Entities"])
def create_registration(entity_id: uuid.UUID, body: RegistrationIn, p: Principal = Auth):
    """Record the Letter of Undertaking (what lets an export go out without IGST) or the Importer-Exporter Code. GSTINs come from onboarding; this is for the two an exporter adds."""
    if body.kind == "lut" and not (body.valid_from and body.valid_until):
        raise ApiError(400, "validation_failed", "A LUT is valid for a period", errors=[{"field": "valid_until", "message": "give the dates on the LUT"}])
    if body.kind == "iec" and len(body.number) != 10:
        raise ApiError(400, "validation_failed", "An IEC has ten characters", errors=[{"field": "number", "message": "check the code on your IEC certificate"}])
    if body.valid_from and body.valid_until and body.valid_until < body.valid_from:
        raise ApiError(400, "validation_failed", "It cannot end before it starts", errors=[{"field": "valid_until", "message": "choose a later date"}])
    with tx(p.user_id) as cur:
        require(cur, entity_id, "settings:write")
        cur.execute("SELECT 1 FROM entity_registrations WHERE kind = %s AND number = %s", (body.kind, body.number))
        if cur.fetchone():
            raise ApiError(409, "conflict", "That number is already recorded")
        cur.execute("""INSERT INTO entity_registrations (entity_id, kind, number, label, valid_from, valid_until, status)
                       VALUES (%s, %s, %s, %s, %s, %s, 'active') RETURNING id, kind::text, number, state_code, label, gst_filing_frequency, valid_from, valid_until, status""",
                    (entity_id, body.kind, body.number, body.label, body.valid_from, body.valid_until))
        row = cur.fetchone()
        audit(cur, entity_id, "registration.recorded", "entity_registrations", row["id"], after={"kind": body.kind, "number": body.number})
        engine.run_engine(cur, entity_id, "fact_change")        # a LUT closes the year's LUT duty; an IEC makes the entity an exporter
        return js(row)


@router.get(E + "/sales-documents", tags=["Sales & billing"])
def list_sales_documents(entity_id: uuid.UUID, doc_type: str | None = None, status: str | None = None, payment_status: str | None = None,
                         party_id: uuid.UUID | None = None, overdue: bool | None = None, from_: dt.date | None = Query(default=None, alias="from"),
                         to: dt.date | None = None, q: str | None = None, limit: int = Query(default=50, ge=1, le=200),
                         cursor: str | None = None, p: Principal = Auth):
    off = offset_of(cursor)
    with tx(p.user_id) as cur:
        require(cur, entity_id, "sales:read")
        cur.execute("""SELECT d.id, d.doc_type::text, d.number, d.doc_date, d.due_date, d.party_id, d.buyer_legal_name, d.buyer_gstin,
                              d.taxable_total, d.grand_total, d.amount_settled, d.balance_due, d.status, d.payment_status, d.irn_status::text AS irn_status,
                              d.original_document_id,
                              CASE WHEN d.status = 'issued' AND d.balance_due > 0 AND d.due_date < current_date THEN current_date - d.due_date ELSE 0 END AS days_overdue
                         FROM sales_documents d
                        WHERE d.entity_id = %s
                          AND (%s::text IS NULL OR d.doc_type::text = %s) AND (%s::text IS NULL OR d.status = %s)
                          AND (%s::text IS NULL OR d.payment_status = %s) AND (%s::uuid IS NULL OR d.party_id = %s)
                          AND (%s::boolean IS NULL OR (%s = (d.status = 'issued' AND d.balance_due > 0 AND d.due_date < current_date)))
                          AND (%s::date IS NULL OR d.doc_date >= %s) AND (%s::date IS NULL OR d.doc_date <= %s)
                          AND (%s::text IS NULL OR d.number ILIKE %s OR d.buyer_legal_name ILIKE %s)
                        ORDER BY d.doc_date DESC, d.created_at DESC, d.id LIMIT %s OFFSET %s""",
                    (entity_id, doc_type, doc_type, status, status, payment_status, payment_status, party_id, party_id, overdue, overdue,
                     from_, from_, to, to, q, _like(q), _like(q), limit + 1, off))
        rows = cur.fetchall()
    return js(page(rows, limit, off))


@router.get(E + "/sales/summary", tags=["Sales & billing"])
def sales_summary(entity_id: uuid.UUID, from_: dt.date | None = Query(default=None, alias="from"), to: dt.date | None = None, p: Principal = Auth):
    """Invoiced and collected inside the period (default: this financial year so far); outstanding and overdue are as of today."""
    today = dt.date.today()
    start, end = from_ or current_fy_start(today), to or today
    if end < start:
        raise ApiError(400, "validation_failed", "`to` must be on or after `from`")
    with tx(p.user_id) as cur:
        require(cur, entity_id, "sales:read")
        cur.execute("""SELECT coalesce(sum(CASE WHEN doc_type = 'credit_note' THEN -grand_total ELSE grand_total END), 0) AS invoiced
                         FROM sales_documents WHERE entity_id = %s AND status = 'issued' AND doc_type <> 'proforma' AND doc_date BETWEEN %s AND %s""", (entity_id, start, end))
        invoiced = cur.fetchone()["invoiced"]
        cur.execute("SELECT method::text AS method, sum(amount) AS amount FROM receipts WHERE entity_id = %s AND status = 'confirmed' AND received_on BETWEEN %s AND %s GROUP BY 1",
                    (entity_id, start, end))
        by_method = {r["method"]: money(r["amount"]) for r in cur.fetchall()}
        cur.execute("""SELECT coalesce(sum(balance_due), 0) AS outstanding,
                              coalesce(sum(balance_due) FILTER (WHERE due_date < current_date), 0) AS overdue,
                              count(*) FILTER (WHERE due_date < current_date) AS overdue_count,
                              coalesce(sum(tds_expected), 0) AS tds
                         FROM sales_documents WHERE entity_id = %s AND status = 'issued' AND balance_due > 0
                          AND doc_type IN ('tax_invoice', 'export_invoice', 'bill_of_supply', 'debit_note')""", (entity_id,))
        o = cur.fetchone()
    from decimal import Decimal
    return {"invoiced": money(invoiced), "collected": money(sum((Decimal(v) for v in by_method.values()), Decimal(0))), "outstanding": money(o["outstanding"]),
            "overdue": money(o["overdue"]), "overdue_count": o["overdue_count"], "tds_to_claim": money(o["tds"]), "collected_by_method": by_method}


# ------------------------------------------------------------------ receipts and ageing
RECEIPT_SQL = """SELECT r.id, r.receipt_no, r.party_id, p.legal_name AS party_name, r.received_on, r.amount, r.method::text AS method, r.utr,
                         r.bank_account_id, r.bank_transaction_id, r.is_advance, r.status,
                         coalesce((SELECT json_agg(json_build_object('invoice_id', s.invoice_id, 'number', d.number, 'amount', s.amount::numeric(15,2)::text) ORDER BY s.id)
                                     FROM invoice_settlements s JOIN sales_documents d ON d.id = s.invoice_id WHERE s.receipt_id = r.id), '[]'::json) AS allocations,
                         r.amount - coalesce((SELECT sum(s.amount) FROM invoice_settlements s WHERE s.receipt_id = r.id), 0) AS unallocated
                    FROM receipts r JOIN parties p ON p.id = r.party_id"""


@router.get(E + "/receipts", tags=["Sales & billing"])
def list_receipts(entity_id: uuid.UUID, party_id: uuid.UUID | None = None, from_: dt.date | None = Query(default=None, alias="from"), to: dt.date | None = None,
                  limit: int = Query(default=50, ge=1, le=200), cursor: str | None = None, p: Principal = Auth):
    off = offset_of(cursor)
    with tx(p.user_id) as cur:
        require(cur, entity_id, "sales:read")
        cur.execute(RECEIPT_SQL + """ WHERE r.entity_id = %s AND (%s::uuid IS NULL OR r.party_id = %s) AND (%s::date IS NULL OR r.received_on >= %s)
                                       AND (%s::date IS NULL OR r.received_on <= %s)
                                     ORDER BY r.received_on DESC, r.receipt_no LIMIT %s OFFSET %s""",
                    (entity_id, party_id, party_id, from_, from_, to, to, limit + 1, off))
        return js(page(cur.fetchall(), limit, off))


@router.get(E + "/receipts/{receipt_id}", tags=["Sales & billing"])
def get_receipt(entity_id: uuid.UUID, receipt_id: uuid.UUID, p: Principal = Auth):
    with tx(p.user_id) as cur:
        require(cur, entity_id, "sales:read")
        cur.execute(RECEIPT_SQL + " WHERE r.entity_id = %s AND r.id = %s", (entity_id, receipt_id))
        r = cur.fetchone()
        if not r:
            raise ApiError(404, "not_found", "Not found")
        return js(r)


@router.get(E + "/receivables/ageing", tags=["Sales & billing"])
def receivables_ageing(entity_id: uuid.UUID, as_of: dt.date | None = None, p: Principal = Auth):
    """One row per customer, open balances bucketed by invoice age at `as_of` (default today). Balances are as they stand now."""
    day = as_of or dt.date.today()
    with tx(p.user_id) as cur:
        require(cur, entity_id, "sales:read")
        cur.execute("""SELECT d.party_id, p.legal_name, p.msme_class::text AS msme_class,
                              coalesce(sum(d.balance_due) FILTER (WHERE %s - d.doc_date <= 30), 0) AS b0_30,
                              coalesce(sum(d.balance_due) FILTER (WHERE %s - d.doc_date BETWEEN 31 AND 45), 0) AS b31_45,
                              coalesce(sum(d.balance_due) FILTER (WHERE %s - d.doc_date BETWEEN 46 AND 60), 0) AS b46_60,
                              coalesce(sum(d.balance_due) FILTER (WHERE %s - d.doc_date BETWEEN 61 AND 90), 0) AS b61_90,
                              coalesce(sum(d.balance_due) FILTER (WHERE %s - d.doc_date > 90), 0) AS b90_plus,
                              sum(d.balance_due) AS total
                         FROM sales_documents d JOIN parties p ON p.id = d.party_id
                        WHERE d.entity_id = %s AND d.status = 'issued' AND d.balance_due > 0 AND d.doc_date <= %s
                          AND d.doc_type IN ('tax_invoice', 'export_invoice', 'bill_of_supply', 'debit_note')
                        GROUP BY d.party_id, p.legal_name, p.msme_class ORDER BY sum(d.balance_due) DESC, p.legal_name""",
                    (day, day, day, day, day, entity_id, day))
        rows = cur.fetchall()
    for r in rows:
        r["interest_accrued_msmed"] = None
    return js(rows)
