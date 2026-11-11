"""Exports and imports: shipping bills and the realisation of export proceeds, bills of entry and the duty paid on them, the LUT, RoDTEP and drawback claims.

A person records what customs and the bank issued. Nothing here calls ICEGATE, DGFT or a bank, and nothing posts to the ledger yet. Rupee values are
worked out in the database from the foreign amount and the exchange rate, and the realisation clock lives in `trade.py` (a starter library for a CA to review).
"""
from __future__ import annotations

import datetime as dt
import uuid
from decimal import Decimal
from typing import Literal

from fastapi import APIRouter, Header, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from . import ledger, trade
from .core import ApiError, Auth, Principal, audit, emit, idempotency_begin, idempotency_finish, require, tx
from .routes_finance import txn_out
from .routes_orders import js, offset_of, page
from .routes_sales import check_shipment_total, reconcile_shipments

router = APIRouter()
E = "/entities/{entity_id}"
Money = Decimal
CODE = r"^[0-9A-Z/-]{1,20}$"
PORT = r"^[A-Z]{2}[A-Z0-9]{4}$"
CURRENCY = r"^[A-Z]{3}$"
CENT = Decimal("0.01")


# ================================================================== shipping bills
SB_STATUS = Literal["draft", "filed", "let_export", "shipped", "closed"]


class ShippingBillIn(BaseModel):
    sb_number: str = Field(pattern=CODE)
    sb_date: dt.date
    port_code: str = Field(pattern=PORT)
    currency: str = Field(pattern=CURRENCY)
    fob_foreign: Money = Field(gt=0, max_digits=15, decimal_places=2)
    exchange_rate: Money = Field(gt=0, max_digits=12, decimal_places=4)
    sales_document_id: uuid.UUID | None = None
    buyer_party_id: uuid.UUID | None = None
    status: SB_STATUS = "filed"
    exported_on: dt.date | None = None
    rodtep_claimed: Money = Field(default=Decimal(0), ge=0, max_digits=15, decimal_places=2)
    drawback_claimed: Money = Field(default=Decimal(0), ge=0, max_digits=15, decimal_places=2)
    brc_ref: str | None = Field(default=None, max_length=60)


class ShippingBillPatch(BaseModel):
    sb_number: str | None = Field(default=None, pattern=CODE)          # customs details: only while the shipping bill is still a draft
    sb_date: dt.date | None = None
    port_code: str | None = Field(default=None, pattern=PORT)
    fob_foreign: Money | None = Field(default=None, gt=0, max_digits=15, decimal_places=2)
    exchange_rate: Money | None = Field(default=None, gt=0, max_digits=12, decimal_places=4)
    status: SB_STATUS | None = None
    exported_on: dt.date | None = None
    rodtep_claimed: Money | None = Field(default=None, ge=0, max_digits=15, decimal_places=2)
    rodtep_credited: Money | None = Field(default=None, ge=0, max_digits=15, decimal_places=2)
    drawback_claimed: Money | None = Field(default=None, ge=0, max_digits=15, decimal_places=2)
    drawback_received: Money | None = Field(default=None, ge=0, max_digits=15, decimal_places=2)
    brc_ref: str | None = Field(default=None, max_length=60)


SB_SQL = """SELECT s.id, s.sb_number, s.sb_date, s.port_code, s.currency, s.fob_foreign, s.exchange_rate::text AS exchange_rate, s.fob_inr, s.status, s.exported_on,
                   s.sales_document_id, d.number AS invoice_number, d.status AS invoice_status, s.buyer_party_id, p.legal_name AS buyer_name,
                   s.rodtep_claimed, s.rodtep_credited, s.drawback_claimed, s.drawback_received, s.brc_ref,
                   coalesce((SELECT sum(r.amount_foreign) FROM export_realisations r WHERE r.shipping_bill_id = s.id), 0) AS realised_foreign
              FROM shipping_bills s LEFT JOIN sales_documents d ON d.id = s.sales_document_id LEFT JOIN parties p ON p.id = s.buyer_party_id"""


def _sb_out(cur, entity_id, sid) -> dict:
    cur.execute(SB_SQL + " WHERE s.id = %s AND s.entity_id = %s", (sid, entity_id))
    s = cur.fetchone()
    if not s:
        raise ApiError(404, "not_found", "Not found")
    cur.execute("""SELECT id, received_on, amount_foreign, bank_ref, firc_no, amount_inr, bank_charges, booked_inr, forex_gain, receipt_id
                     FROM export_realisations WHERE shipping_bill_id = %s ORDER BY received_on, created_at""", (sid,))
    s["realisations"] = cur.fetchall()
    st = trade.realisation_state(s["fob_foreign"], s["realised_foreign"], s["exported_on"], dt.date.today())
    s["realisation_state"] = st["state"]
    s["realisation_due_on"] = st["due_on"]
    s["days_left"] = st["days_left"]
    s["outstanding_foreign"] = st["outstanding_foreign"]
    s["outstanding_inr"] = (st["outstanding_foreign"] * Decimal(s["exchange_rate"])).quantize(CENT)
    return js(s)


def _check_links(cur, entity_id, sales_document_id, buyer_party_id) -> uuid.UUID | None:
    """The export invoice and the buyer must be this company's own. Returns the buyer to use (the invoice's, when none is given)."""
    buyer = buyer_party_id
    if sales_document_id:
        cur.execute("SELECT doc_type::text AS t, party_id FROM sales_documents WHERE id = %s AND entity_id = %s", (sales_document_id, entity_id))
        d = cur.fetchone()
        if not d or d["t"] != "export_invoice":
            raise ApiError(400, "validation_failed", "That is not one of this company's export invoices", errors=[{"field": "sales_document_id", "message": "choose an export invoice"}])
        buyer = buyer or d["party_id"]
    if buyer:
        cur.execute("SELECT 1 FROM parties WHERE id = %s AND entity_id = %s", (buyer, entity_id))
        if not cur.fetchone():
            raise ApiError(400, "validation_failed", "Unknown buyer", errors=[{"field": "buyer_party_id", "message": "not one of this company's parties"}])
    return buyer


def _realisation_task(cur, entity_id, number, exported_on, fob, currency) -> None:
    due = trade.realisation_due(exported_on)
    cur.execute("INSERT INTO tasks (entity_id, title, due_on, source) VALUES (%s, %s, %s, 'engine')",
                (entity_id, f"Realise the export proceeds of shipping bill {number} ({currency} {fob}) by {due:%d %b %Y}", due))


@router.post(E + "/shipping-bills", status_code=201, tags=["Export & import"])
def create_shipping_bill(entity_id: uuid.UUID, body: ShippingBillIn, p: Principal = Auth):
    with tx(p.user_id) as cur:
        require(cur, entity_id, "trade:write")
        today = dt.date.today()
        if body.sb_date > today:
            raise ApiError(400, "validation_failed", "A shipping bill cannot be dated in the future", errors=[{"field": "sb_date", "message": "record it once customs has issued it"}])
        if body.exported_on and body.exported_on > today:
            raise ApiError(400, "validation_failed", "The date of export cannot be in the future", errors=[{"field": "exported_on", "message": "record it once the goods have left"}])
        buyer = _check_links(cur, entity_id, body.sales_document_id, body.buyer_party_id)
        if body.sales_document_id:                      # the invoice already opened a draft: fill that one in instead of adding a second
            cur.execute("SELECT id FROM shipping_bills WHERE entity_id = %s AND sales_document_id = %s AND status = 'draft' ORDER BY created_at LIMIT 1 FOR UPDATE",
                        (entity_id, body.sales_document_id))
            draft = cur.fetchone()
            if draft:
                cur.execute("""UPDATE shipping_bills SET sb_number = %s, sb_date = %s, port_code = %s, currency = %s, fob_foreign = %s, exchange_rate = %s, status = %s,
                                      exported_on = %s, rodtep_claimed = %s, drawback_claimed = %s, brc_ref = %s WHERE id = %s""",
                            (body.sb_number, body.sb_date, body.port_code, body.currency, body.fob_foreign, body.exchange_rate, body.status, body.exported_on,
                             body.rodtep_claimed, body.drawback_claimed, body.brc_ref, draft["id"]))
                check_shipment_total(cur, body.sales_document_id)
                reconcile_shipments(cur, entity_id, body.sales_document_id)
                if body.exported_on:
                    _realisation_task(cur, entity_id, body.sb_number, body.exported_on, body.fob_foreign, body.currency)
                audit(cur, entity_id, "shipping_bill.recorded", "shipping_bills", draft["id"], after={"sb_number": body.sb_number, "fob_foreign": body.fob_foreign, "currency": body.currency})
                return _sb_out(cur, entity_id, draft["id"])
        cur.execute("""INSERT INTO shipping_bills (entity_id, sales_document_id, buyer_party_id, sb_number, sb_date, port_code, currency, fob_foreign, exchange_rate, status, exported_on,
                                                   rodtep_claimed, drawback_claimed, brc_ref)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id""",
                    (entity_id, body.sales_document_id, buyer, body.sb_number, body.sb_date, body.port_code, body.currency, body.fob_foreign, body.exchange_rate, body.status,
                     body.exported_on, body.rodtep_claimed, body.drawback_claimed, body.brc_ref))
        sid = cur.fetchone()["id"]
        if body.sales_document_id:
            check_shipment_total(cur, body.sales_document_id)
            reconcile_shipments(cur, entity_id, body.sales_document_id)
        if body.exported_on:
            _realisation_task(cur, entity_id, body.sb_number, body.exported_on, body.fob_foreign, body.currency)
        audit(cur, entity_id, "shipping_bill.recorded", "shipping_bills", sid, after={"sb_number": body.sb_number, "fob_foreign": body.fob_foreign, "currency": body.currency})
        emit(cur, entity_id, "shipping_bill.recorded", "shipping_bills", sid, {"sb_number": body.sb_number})
        return _sb_out(cur, entity_id, sid)


@router.get(E + "/shipping-bills", tags=["Export & import"])
def list_shipping_bills(entity_id: uuid.UUID, p: Principal = Auth, state: Literal["awaiting_export", "pending", "overdue", "realised"] | None = None,
                        limit: int = Query(default=100, ge=1, le=200), cursor: str | None = None):
    off = offset_of(cursor)
    with tx(p.user_id) as cur:
        require(cur, entity_id, "trade:read")
        cur.execute("SELECT id FROM shipping_bills WHERE entity_id = %s ORDER BY sb_date DESC, sb_number", (entity_id,))
        rows = [_sb_out(cur, entity_id, r["id"]) for r in cur.fetchall()]
    if state:
        rows = [r for r in rows if r["realisation_state"] == state]
    return page(rows[off:off + limit + 1], limit, off)


@router.get(E + "/shipping-bills/{sb_id}", tags=["Export & import"])
def get_shipping_bill(entity_id: uuid.UUID, sb_id: uuid.UUID, p: Principal = Auth):
    with tx(p.user_id) as cur:
        require(cur, entity_id, "trade:read")
        return _sb_out(cur, entity_id, sb_id)


@router.patch(E + "/shipping-bills/{sb_id}", tags=["Export & import"])
def update_shipping_bill(entity_id: uuid.UUID, sb_id: uuid.UUID, body: ShippingBillPatch, p: Principal = Auth):
    with tx(p.user_id) as cur:
        require(cur, entity_id, "trade:write")
        cur.execute("SELECT sb_number, sb_date, port_code, currency, fob_foreign, exported_on, status FROM shipping_bills WHERE id = %s AND entity_id = %s FOR UPDATE", (sb_id, entity_id))
        s = cur.fetchone()
        if not s:
            raise ApiError(404, "not_found", "Not found")
        changes = body.model_dump(exclude_unset=True)
        if not changes:
            raise ApiError(400, "validation_failed", "Nothing to change")
        customs = [k for k in ("sb_number", "sb_date", "port_code", "fob_foreign", "exchange_rate") if k in changes]
        if customs and s["status"] != "draft":
            raise ApiError(409, "conflict", "The customs details are already recorded", "They are fixed once the shipping bill is filed.")
        if s["status"] == "draft":
            merged = {k: changes.get(k, s[k]) for k in ("sb_number", "sb_date", "port_code")}
            if merged["sb_date"] and merged["sb_date"] > dt.date.today():
                raise ApiError(400, "validation_failed", "A shipping bill cannot be dated in the future", errors=[{"field": "sb_date", "message": "record it once customs has issued it"}])
            complete = all(merged.values())
            if "status" in changes and changes["status"] != "draft" and not complete:
                raise ApiError(400, "validation_failed", "Add the customs number, port and date first",
                               errors=[{"field": k, "message": "required once the shipping bill is filed"} for k, v in merged.items() if not v])
            if complete and "status" not in changes:
                changes["status"] = "filed"                 # the customs details are what turn a draft into a filed shipping bill
        if changes.get("exported_on"):
            if s["status"] == "draft" and not all((changes.get("sb_number", s["sb_number"]), changes.get("sb_date", s["sb_date"]), changes.get("port_code", s["port_code"]))):
                raise ApiError(400, "validation_failed", "Add the customs number, port and date first",
                               errors=[{"field": "exported_on", "message": "the date of export follows the shipping bill"}])
            if changes["exported_on"] > dt.date.today():
                raise ApiError(400, "validation_failed", "The date of export cannot be in the future", errors=[{"field": "exported_on", "message": "record it once the goods have left"}])
            if s["exported_on"] and changes["exported_on"] != s["exported_on"]:
                raise ApiError(409, "conflict", "The date of export is already recorded", "It starts the realisation clock, so it is not changed after the fact.")
        sets = ", ".join(f"{k} = %s" for k in changes)
        cur.execute(f"UPDATE shipping_bills SET {sets} WHERE id = %s", (*changes.values(), sb_id))
        cur.execute("SELECT sales_document_id FROM shipping_bills WHERE id = %s", (sb_id,))
        doc = cur.fetchone()["sales_document_id"]
        if doc and ("fob_foreign" in changes or (s["status"] == "draft" and changes.get("status", "draft") != "draft")):
            check_shipment_total(cur, doc)
            if s["status"] == "draft" and changes.get("status", "draft") != "draft":
                reconcile_shipments(cur, entity_id, doc)
        if changes.get("exported_on") and not s["exported_on"]:
            _realisation_task(cur, entity_id, s["sb_number"], changes["exported_on"], s["fob_foreign"], s["currency"])
        audit(cur, entity_id, "shipping_bill.updated", "shipping_bills", sb_id, before={"status": s["status"]}, after=js(changes))
        return _sb_out(cur, entity_id, sb_id)


class RealisationIn(BaseModel):
    received_on: dt.date
    amount_foreign: Money = Field(gt=0, max_digits=15, decimal_places=2)
    bank_ref: str | None = Field(default=None, max_length=60)
    firc_no: str | None = Field(default=None, max_length=60)
    amount_inr: Money | None = Field(default=None, gt=0, max_digits=15, decimal_places=2)     # what the bank credited, net of its charges: posts the receipt and the forex gain or loss
    bank_charges: Money = Field(default=Decimal("0"), ge=0, max_digits=15, decimal_places=2)
    bank_account_id: uuid.UUID | None = None


@router.post(E + "/shipping-bills/{sb_id}/realisations", status_code=201, tags=["Export & import"])
def record_realisation(entity_id: uuid.UUID, sb_id: uuid.UUID, body: RealisationIn, p: Principal = Auth,
                       idempotency_key: str | None = Header(default=None, alias="Idempotency-Key")):
    """Money that has come in against a shipping bill. It may arrive before the goods leave (an advance); it may not exceed what is owed.
    With the rupees the bank credited it also settles the export invoice and posts the foreign exchange gain or loss."""
    with tx(p.user_id) as cur:
        require(cur, entity_id, "trade:write")
        if body.amount_inr is None and (body.bank_charges or body.bank_account_id):
            raise ApiError(400, "validation_failed", "Bank charges and the bank account go with the rupees credited", errors=[{"field": "amount_inr", "message": "enter what the bank credited"}])
        replay = idempotency_begin(cur, idempotency_key, "POST", f"/entities/{entity_id}/shipping-bills/{sb_id}/realisations", body.model_dump(mode="json")) if body.amount_inr else None
        if replay:
            return JSONResponse(replay["body"], status_code=replay["status"])
        _record(cur, entity_id, sb_id, body)
        out = _sb_out(cur, entity_id, sb_id)
        if body.amount_inr:
            idempotency_finish(cur, idempotency_key, 201, out)
        return out


def _record(cur, entity_id, sb_id, body: RealisationIn, txn_id=None) -> dict | None:
    """Save the money against the shipping bill and, when the rupees credited are known, post it. Shared by the shipping bill screen and the bank line."""
    cur.execute("SELECT fob_foreign, sb_date, sales_document_id FROM shipping_bills WHERE id = %s AND entity_id = %s FOR UPDATE", (sb_id, entity_id))
    s = cur.fetchone()
    if not s:
        raise ApiError(404, "not_found", "Not found")
    if body.received_on > dt.date.today():
        raise ApiError(400, "validation_failed", "Money cannot have been received in the future", errors=[{"field": "received_on", "message": "record it once it has arrived"}])
    cur.execute("SELECT coalesce(sum(amount_foreign), 0) AS n FROM export_realisations WHERE shipping_bill_id = %s", (sb_id,))
    already = cur.fetchone()["n"]
    if already + body.amount_foreign > s["fob_foreign"] + trade.TOLERANCE:
        raise ApiError(409, "conflict", "That is more than the shipping bill is worth", f"{s['fob_foreign'] - already} is still to be realised.")
    posted = _post_proceeds(cur, entity_id, s, body, txn_id) if body.amount_inr else None
    cur.execute("""INSERT INTO export_realisations (entity_id, shipping_bill_id, received_on, amount_foreign, bank_ref, firc_no, receipt_id, amount_inr, bank_charges, booked_inr, forex_gain)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id""",
                (entity_id, sb_id, body.received_on, body.amount_foreign, body.bank_ref, body.firc_no,
                 posted and posted["receipt_id"], body.amount_inr, body.bank_charges, posted and posted["booked"], posted and posted["gain"]))
    rid = cur.fetchone()["id"]
    audit(cur, entity_id, "export_realisation.recorded", "export_realisations", rid,
          after={"shipping_bill_id": sb_id, "amount_foreign": body.amount_foreign, **({"amount_inr": body.amount_inr, "forex_gain": posted["gain"]} if posted else {})})
    return posted


class ProceedsFromBank(BaseModel):
    shipping_bill_id: uuid.UUID
    amount_foreign: Money = Field(gt=0, max_digits=15, decimal_places=2)          # the dollars, from the FIRC or the customer's advice: the statement shows rupees only
    bank_charges: Money = Field(default=Decimal("0"), ge=0, max_digits=15, decimal_places=2)
    firc_no: str | None = Field(default=None, max_length=60)


@router.post(E + "/bank-transactions/{txn_id}/export-proceeds", tags=["Export & import"])
def bank_credit_as_export_proceeds(entity_id: uuid.UUID, txn_id: uuid.UUID, body: ProceedsFromBank, p: Principal = Auth,
                                   idempotency_key: str | None = Header(default=None, alias="Idempotency-Key")):
    """Take a credit on the bank statement as the proceeds of an export. The line is the rupees *after* the bank's charges, so the person says how many
    dollars it was and what the bank charged; the books settle the export invoice at its own rate and post the exchange difference."""
    with tx(p.user_id) as cur:
        require(cur, entity_id, "trade:write")
        require(cur, entity_id, "finance:write")
        replay = idempotency_begin(cur, idempotency_key, "POST", f"/entities/{entity_id}/bank-transactions/{txn_id}/export-proceeds", body.model_dump(mode="json"))
        if replay:
            return JSONResponse(replay["body"], status_code=replay["status"])
        cur.execute("SELECT * FROM bank_transactions WHERE id = %s AND entity_id = %s FOR UPDATE", (txn_id, entity_id))
        t = cur.fetchone()
        if not t:
            raise ApiError(404, "not_found", "Not found")
        if t["amount"] <= 0:
            raise ApiError(400, "validation_failed", "Only a credit can be export proceeds", errors=[{"field": "txn_id", "message": "this line is a debit"}])
        if t["match_status"] == "matched":
            raise ApiError(409, "conflict", "Already matched")
        inner = RealisationIn(received_on=t["txn_date"], amount_foreign=body.amount_foreign, bank_ref=t["utr"], firc_no=body.firc_no,
                              amount_inr=t["amount"], bank_charges=body.bank_charges, bank_account_id=t["bank_account_id"])
        posted = _record(cur, entity_id, body.shipping_bill_id, inner, txn_id)
        cur.execute("SELECT id, target_kind, target_id FROM bank_matches WHERE bank_transaction_id = %s AND status = 'suggested' FOR UPDATE", (txn_id,))
        chosen = None
        for m in cur.fetchall():
            if chosen is None and m["target_kind"] == "shipping_bill" and m["target_id"] == body.shipping_bill_id:
                chosen = m["id"]
            else:
                cur.execute("UPDATE bank_matches SET status = 'rejected', decided_by = app.current_user_id(), decided_at = now() WHERE id = %s", (m["id"],))
        cur.execute("UPDATE receipts SET status = 'reversed' WHERE bank_transaction_id = %s AND status = 'pending'", (txn_id,))      # a domestic guess for the same line
        if chosen:
            cur.execute("""UPDATE bank_matches SET status = 'accepted', target_kind = 'receipt', target_id = %s, decided_by = app.current_user_id(), decided_at = now()
                            WHERE id = %s""", (posted["receipt_id"], chosen))
        else:
            cur.execute("""INSERT INTO bank_matches (bank_transaction_id, target_kind, target_id, amount, confidence, rule, status, decided_by, decided_at)
                           VALUES (%s, 'receipt', %s, %s, 1, 'export_proceeds', 'accepted', app.current_user_id(), now())""", (txn_id, posted["receipt_id"], t["amount"]))
        cur.execute("UPDATE bank_transactions SET match_status = 'matched' WHERE id = %s", (txn_id,))
        audit(cur, entity_id, "bank_transaction.export_proceeds", "bank_transactions", txn_id,
              after={"shipping_bill_id": body.shipping_bill_id, "receipt": posted["receipt_id"], "forex_gain": posted["gain"]})
        out = txn_out(cur, txn_id)
        idempotency_finish(cur, idempotency_key, 200, out)
    return out


def _post_proceeds(cur, entity_id, s, body: RealisationIn, txn_id=None) -> dict:
    """The receipt, the settlement of the export invoice and the journal for proceeds from abroad. The invoice is settled at its own rate; the rest is forex."""
    if not s["sales_document_id"]:
        raise ApiError(409, "proceeds_need_invoice", "Link the shipping bill to its export invoice first", "The rupees credited settle that invoice in the books.")
    cur.execute("""SELECT status, party_id, exchange_rate, balance_due FROM sales_documents WHERE id = %s AND entity_id = %s FOR UPDATE""", (s["sales_document_id"], entity_id))
    inv = cur.fetchone()
    if not inv or inv["status"] != "issued" or not inv["exchange_rate"]:
        raise ApiError(409, "proceeds_need_invoice", "The export invoice must be issued first", "Money is posted against an issued invoice.")
    booked = min((body.amount_foreign * inv["exchange_rate"]).quantize(CENT), inv["balance_due"])
    if booked <= 0:
        raise ApiError(409, "conflict", "The export invoice is already settled", "There is nothing left to receive against it.")
    bank_key = None
    if body.bank_account_id:
        cur.execute("SELECT ledger_account_id FROM bank_accounts WHERE id = %s AND entity_id = %s", (body.bank_account_id, entity_id))
        b = cur.fetchone()
        if not b:
            raise ApiError(400, "validation_failed", "Unknown bank account", errors=[{"field": "bank_account_id", "message": "not one of this company's bank accounts"}])
        bank_key = "account:" + str(b["ledger_account_id"])
    gain = body.amount_inr + body.bank_charges - booked
    receipt_no = "RCPT-" + uuid.uuid4().hex[:8].upper()
    cur.execute("""INSERT INTO receipts (entity_id, party_id, receipt_no, received_on, amount, method, utr, bank_account_id, bank_transaction_id, is_advance)
                   VALUES (%s, %s, %s, %s, %s, 'rtgs', %s, %s, %s, false) RETURNING id""",
                (entity_id, inv["party_id"], receipt_no, body.received_on, body.amount_inr, body.bank_ref, body.bank_account_id, txn_id))
    rcid = cur.fetchone()["id"]
    cur.execute("INSERT INTO invoice_settlements (entity_id, invoice_id, source_kind, receipt_id, amount, settled_on) VALUES (%s, %s, 'receipt', %s, %s, %s)",
                (entity_id, s["sales_document_id"], rcid, booked, body.received_on))
    jid = ledger.post_journal(cur, entity_id, body.received_on, "receipt", rcid, f"Export proceeds {receipt_no}",
                              ledger.export_proceeds_lines(received=body.amount_inr, charges=body.bank_charges, booked=booked, party_id=inv["party_id"], bank_account_key=bank_key))
    cur.execute("UPDATE receipts SET journal_entry_id = %s WHERE id = %s", (jid, rcid))
    audit(cur, entity_id, "receipt.created", "receipts", rcid, after={"amount": body.amount_inr, "export": True})
    emit(cur, entity_id, "receipt.created", "receipts", rcid, {"amount": str(body.amount_inr)})
    cur.execute("SELECT payment_status FROM sales_documents WHERE id = %s", (s["sales_document_id"],))
    if cur.fetchone()["payment_status"] == "paid":
        emit(cur, entity_id, "sales_document.paid", "sales_documents", s["sales_document_id"])
    return {"receipt_id": rcid, "booked": booked, "gain": gain}


# ================================================================== bills of entry
BOE_STATUS = Literal["filed", "assessed", "duty_paid", "cleared"]


class BoeIn(BaseModel):
    boe_number: str = Field(pattern=CODE)
    boe_date: dt.date
    port_code: str = Field(pattern=PORT)
    currency: str = Field(pattern=CURRENCY)
    cif_foreign: Money = Field(gt=0, max_digits=15, decimal_places=2)
    exchange_rate: Money = Field(gt=0, max_digits=12, decimal_places=4)
    supplier_party_id: uuid.UUID | None = None
    purchase_bill_id: uuid.UUID | None = None
    bcd: Money = Field(default=Decimal(0), ge=0, max_digits=15, decimal_places=2)
    sws: Money = Field(default=Decimal(0), ge=0, max_digits=15, decimal_places=2)
    igst: Money = Field(default=Decimal(0), ge=0, max_digits=15, decimal_places=2)
    other_duty: Money = Field(default=Decimal(0), ge=0, max_digits=15, decimal_places=2)
    status: BOE_STATUS = "filed"
    duty_paid_on: dt.date | None = None


class BoePatch(BaseModel):
    status: BOE_STATUS | None = None
    duty_paid_on: dt.date | None = None
    bcd: Money | None = Field(default=None, ge=0, max_digits=15, decimal_places=2)
    sws: Money | None = Field(default=None, ge=0, max_digits=15, decimal_places=2)
    igst: Money | None = Field(default=None, ge=0, max_digits=15, decimal_places=2)
    other_duty: Money | None = Field(default=None, ge=0, max_digits=15, decimal_places=2)
    purchase_bill_id: uuid.UUID | None = None


BOE_SQL = """SELECT b.id, b.boe_number, b.boe_date, b.port_code, b.currency, b.cif_foreign, b.exchange_rate::text AS exchange_rate, b.cif_inr, b.bcd, b.sws, b.igst, b.other_duty, b.duty_total,
                    b.status, b.duty_paid_on, b.supplier_party_id, p.legal_name AS supplier_name, b.purchase_bill_id,
                    CASE WHEN b.status IN ('duty_paid', 'cleared') THEN b.igst ELSE 0 END AS igst_credit
               FROM bills_of_entry b LEFT JOIN parties p ON p.id = b.supplier_party_id"""


def _boe_out(cur, entity_id, bid) -> dict:
    cur.execute(BOE_SQL + " WHERE b.id = %s AND b.entity_id = %s", (bid, entity_id))
    b = cur.fetchone()
    if not b:
        raise ApiError(404, "not_found", "Not found")
    return js(b)


def _check_boe_links(cur, entity_id, supplier, bill) -> None:
    if supplier:
        cur.execute("SELECT 1 FROM parties WHERE id = %s AND entity_id = %s", (supplier, entity_id))
        if not cur.fetchone():
            raise ApiError(400, "validation_failed", "Unknown supplier", errors=[{"field": "supplier_party_id", "message": "not one of this company's parties"}])
    if bill:
        cur.execute("SELECT 1 FROM purchase_bills WHERE id = %s AND entity_id = %s", (bill, entity_id))
        if not cur.fetchone():
            raise ApiError(400, "validation_failed", "Unknown purchase bill", errors=[{"field": "purchase_bill_id", "message": "not one of this company's bills"}])


@router.post(E + "/bills-of-entry", status_code=201, tags=["Export & import"])
def create_bill_of_entry(entity_id: uuid.UUID, body: BoeIn, p: Principal = Auth):
    with tx(p.user_id) as cur:
        require(cur, entity_id, "trade:write")
        if body.boe_date > dt.date.today():
            raise ApiError(400, "validation_failed", "A bill of entry cannot be dated in the future", errors=[{"field": "boe_date", "message": "record it once customs has issued it"}])
        if body.status in ("duty_paid", "cleared") and not body.duty_paid_on:
            raise ApiError(400, "validation_failed", "Say when the duty was paid", errors=[{"field": "duty_paid_on", "message": "required once duty is paid"}])
        _check_boe_links(cur, entity_id, body.supplier_party_id, body.purchase_bill_id)
        cur.execute("""INSERT INTO bills_of_entry (entity_id, supplier_party_id, purchase_bill_id, boe_number, boe_date, port_code, currency, cif_foreign, exchange_rate, bcd, sws, igst, other_duty, status, duty_paid_on)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id""",
                    (entity_id, body.supplier_party_id, body.purchase_bill_id, body.boe_number, body.boe_date, body.port_code, body.currency, body.cif_foreign, body.exchange_rate,
                     body.bcd, body.sws, body.igst, body.other_duty, body.status, body.duty_paid_on))
        bid = cur.fetchone()["id"]
        audit(cur, entity_id, "bill_of_entry.recorded", "bills_of_entry", bid, after={"boe_number": body.boe_number, "igst": body.igst})
        emit(cur, entity_id, "bill_of_entry.recorded", "bills_of_entry", bid, {"boe_number": body.boe_number})
        return _boe_out(cur, entity_id, bid)


@router.get(E + "/bills-of-entry", tags=["Export & import"])
def list_bills_of_entry(entity_id: uuid.UUID, p: Principal = Auth, limit: int = Query(default=100, ge=1, le=200), cursor: str | None = None):
    off = offset_of(cursor)
    with tx(p.user_id) as cur:
        require(cur, entity_id, "trade:read")
        cur.execute(BOE_SQL + " WHERE b.entity_id = %s ORDER BY b.boe_date DESC, b.boe_number LIMIT %s OFFSET %s", (entity_id, limit + 1, off))
        return page(js(cur.fetchall()), limit, off)


@router.patch(E + "/bills-of-entry/{boe_id}", tags=["Export & import"])
def update_bill_of_entry(entity_id: uuid.UUID, boe_id: uuid.UUID, body: BoePatch, p: Principal = Auth):
    with tx(p.user_id) as cur:
        require(cur, entity_id, "trade:write")
        cur.execute("SELECT status, duty_paid_on FROM bills_of_entry WHERE id = %s AND entity_id = %s FOR UPDATE", (boe_id, entity_id))
        b = cur.fetchone()
        if not b:
            raise ApiError(404, "not_found", "Not found")
        changes = body.model_dump(exclude_unset=True)
        if not changes:
            raise ApiError(400, "validation_failed", "Nothing to change")
        merged_status = changes.get("status", b["status"])
        merged_paid = changes.get("duty_paid_on", b["duty_paid_on"])
        if merged_status in ("duty_paid", "cleared") and not merged_paid:
            raise ApiError(400, "validation_failed", "Say when the duty was paid", errors=[{"field": "duty_paid_on", "message": "required once duty is paid"}])
        if merged_paid and merged_paid > dt.date.today():
            raise ApiError(400, "validation_failed", "Duty cannot have been paid in the future", errors=[{"field": "duty_paid_on", "message": "record it once it is paid"}])
        _check_boe_links(cur, entity_id, None, changes.get("purchase_bill_id"))
        sets = ", ".join(f"{k} = %s" for k in changes)
        cur.execute(f"UPDATE bills_of_entry SET {sets} WHERE id = %s", (*changes.values(), boe_id))
        audit(cur, entity_id, "bill_of_entry.updated", "bills_of_entry", boe_id, before={"status": b["status"]}, after=js(changes))
        return _boe_out(cur, entity_id, boe_id)


# ================================================================== the picture for the screen
@router.get(E + "/trade/summary", tags=["Export & import"])
def trade_summary(entity_id: uuid.UUID, p: Principal = Auth):
    with tx(p.user_id) as cur:
        require(cur, entity_id, "trade:read")
        today = dt.date.today()
        start = trade.fy_start(today)
        cur.execute("SELECT id FROM shipping_bills WHERE entity_id = %s", (entity_id,))
        bills = [_sb_out(cur, entity_id, r["id"]) for r in cur.fetchall()]
        cur.execute("""SELECT count(*) AS n, coalesce(sum(duty_total), 0) AS duty, coalesce(sum(CASE WHEN status IN ('duty_paid', 'cleared') THEN igst ELSE 0 END), 0) AS igst
                         FROM bills_of_entry WHERE entity_id = %s AND boe_date >= %s""", (entity_id, start))
        imports = cur.fetchone()
        cur.execute("SELECT valid_until FROM entity_registrations WHERE entity_id = %s AND kind = 'lut' AND status = 'active' ORDER BY valid_until DESC NULLS FIRST LIMIT 1", (entity_id,))
        lut = cur.fetchone()
        cur.execute("SELECT number FROM entity_registrations WHERE entity_id = %s AND kind = 'iec' AND status = 'active' LIMIT 1", (entity_id,))
        iec = cur.fetchone()
    this_year = [b for b in bills if b["sb_date"] and b["sb_date"] >= start.isoformat()]
    owed = [b for b in bills if b["realisation_state"] in ("pending", "overdue")]
    overdue = [b for b in bills if b["realisation_state"] == "overdue"]
    claims_rodtep = sum((Decimal(b["rodtep_claimed"]) - Decimal(b["rodtep_credited"]) for b in bills), Decimal(0))
    claims_drawback = sum((Decimal(b["drawback_claimed"]) - Decimal(b["drawback_received"]) for b in bills), Decimal(0))
    return js({
        "as_of": today, "financial_year_from": start, "iec": iec["number"] if iec else None,
        "lut": trade.lut_state(lut["valid_until"] if lut else None, today, lut is not None),
        "exports": {"bills": len(this_year), "value_inr": sum((Decimal(b["fob_inr"]) for b in this_year), Decimal(0)), "drafts": sum(1 for b in bills if b["status"] == "draft")},
        "realisation": {"outstanding_bills": len(owed), "outstanding_inr": sum((Decimal(b["outstanding_inr"]) for b in owed), Decimal(0)), "overdue_bills": len(overdue),
                        "overdue_inr": sum((Decimal(b["outstanding_inr"]) for b in overdue), Decimal(0)), "awaiting_export": sum(1 for b in bills if b["realisation_state"] == "awaiting_export")},
        "claims": {"rodtep_pending": claims_rodtep, "drawback_pending": claims_drawback},
        "imports": {"bills": imports["n"], "duty_inr": imports["duty"], "igst_credit_inr": imports["igst"]},
    })
