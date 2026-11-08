"""Read endpoints the billing screens need: GSTIN registrations, customers, the invoice list and the sales summary."""
from __future__ import annotations

import datetime as dt
import uuid

from fastapi import APIRouter, Query

from .core import ApiError, Auth, Principal, money, require, tx
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
        cur.execute("""SELECT pa.id, pa.legal_name, pa.display_name, pa.is_customer, pa.is_vendor, pa.pan, pa.payment_terms_days, pa.status,
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
