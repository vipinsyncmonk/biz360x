"""Purchases: purchase orders, bills (booked to the ledger, TDS withheld), the MSME 45-day clock, vendor payments."""
from __future__ import annotations

import datetime as dt
import json
import uuid
from decimal import Decimal
from typing import Literal, Protocol

from fastapi import APIRouter, Header, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from . import adapters, inventory, ledger, tax, taxcalc
from .core import ApiError, Auth, Principal, audit, emit, idempotency_begin, idempotency_finish, jsonable, money, require, tx
from .routes_orders import js, next_number, offset_of, page
from .routes_sales import LineIn

router = APIRouter()
E = "/entities/{entity_id}"


class PurchaseLineIn(LineIn):
    expense_account_id: uuid.UUID | None = None


# ================================================================== purchase orders
class PurchaseOrderIn(BaseModel):
    party_id: uuid.UUID
    po_date: dt.date
    lines: list[LineIn] = Field(min_length=1)


def load_po(cur, entity_id, pid) -> dict:
    cur.execute("SELECT id, po_no, party_id, po_date, status, total FROM purchase_orders WHERE id = %s AND entity_id = %s", (pid, entity_id))
    po = cur.fetchone()
    if not po:
        raise ApiError(404, "not_found", "Not found")
    cur.execute("""SELECT line_no, item_id, description, hsn_sac, qty, uqc, rate, gst_rate, received_qty FROM purchase_order_lines
                    WHERE po_id = %s ORDER BY line_no""", (pid,))
    po["lines"] = cur.fetchall()
    return js(po)


@router.post(E + "/purchase-orders", status_code=201, tags=["Purchases"])
def create_po(entity_id: uuid.UUID, body: PurchaseOrderIn, p: Principal = Auth):
    with tx(p.user_id) as cur:
        require(cur, entity_id, "purchases:write")
        cur.execute("SELECT 1 FROM parties WHERE id = %s AND entity_id = %s AND is_vendor", (body.party_id, entity_id))
        if not cur.fetchone():
            raise ApiError(400, "validation_failed", "party_id is not a vendor of this entity")
        total = sum((tax.r2(l.qty * l.rate - l.discount) * (1 + l.gst_rate / 100) for l in body.lines), Decimal(0))
        cur.execute("INSERT INTO purchase_orders (entity_id, party_id, po_no, po_date, status, total) VALUES (%s, %s, %s, %s, 'open', %s) RETURNING id",
                    (entity_id, body.party_id, next_number(cur, entity_id, "purchase_orders", "po_no", "PO-", 4), body.po_date, tax.r2(total)))
        pid = cur.fetchone()["id"]
        for i, l in enumerate(body.lines, 1):
            cur.execute("""INSERT INTO purchase_order_lines (po_id, line_no, item_id, description, hsn_sac, qty, uqc, rate, gst_rate)
                           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)""", (pid, i, l.item_id, l.description, l.hsn_sac, l.qty, l.uqc, l.rate, l.gst_rate))
        audit(cur, entity_id, "purchase_order.created", "purchase_orders", pid)
        return load_po(cur, entity_id, pid)


@router.get(E + "/purchase-orders", tags=["Purchases"])
def list_pos(entity_id: uuid.UUID, p: Principal = Auth, limit: int = Query(default=50, ge=1, le=200), cursor: str | None = None):
    off = offset_of(cursor)
    with tx(p.user_id) as cur:
        require(cur, entity_id, "purchases:read")
        cur.execute("SELECT id FROM purchase_orders WHERE entity_id = %s ORDER BY po_date DESC, po_no DESC LIMIT %s OFFSET %s", (entity_id, limit + 1, off))
        return page([load_po(cur, entity_id, r["id"]) for r in cur.fetchall()], limit, off)


@router.get(E + "/purchase-orders/{po_id}", tags=["Purchases"])
def get_po(entity_id: uuid.UUID, po_id: uuid.UUID, p: Principal = Auth):
    with tx(p.user_id) as cur:
        require(cur, entity_id, "purchases:read")
        return load_po(cur, entity_id, po_id)


# ================================================================== purchase bills
class PurchaseBillIn(BaseModel):
    party_id: uuid.UUID
    party_gst_id: uuid.UUID | None = None
    registration_id: uuid.UUID
    bill_no: str = Field(min_length=1, max_length=40)
    bill_date: dt.date
    accepted_on: dt.date
    place_of_supply: str | None = Field(default=None, pattern=r"^[0-9]{2}$")
    reverse_charge: bool = False
    tds_section: str | None = None
    itc_eligibility: Literal["eligible", "blocked_17_5", "ineligible", "partial", "reverse_charge"] = "eligible"
    agreed_terms_days: int | None = Field(default=None, ge=0, le=365)
    due_date: dt.date | None = None
    document_id: uuid.UUID | None = None
    lines: list[PurchaseLineIn] = Field(min_length=1)


def _prepare_bill(cur, entity_id, b: PurchaseBillIn) -> dict:
    if b.reverse_charge or b.itc_eligibility in ("partial", "reverse_charge"):
        raise ApiError(400, "validation_failed", "Reverse charge and partial ITC are not supported yet",
                       errors=[{"field": "itc_eligibility", "message": "use eligible, blocked_17_5 or ineligible"}])
    cur.execute("SELECT state_code FROM entity_registrations WHERE id = %s AND entity_id = %s AND kind = 'gstin'", (b.registration_id, entity_id))
    reg = cur.fetchone()
    cur.execute("SELECT legal_name, pan, payment_terms_days FROM parties WHERE id = %s AND entity_id = %s AND is_vendor", (b.party_id, entity_id))
    party = cur.fetchone()
    if not reg or not party:
        raise ApiError(400, "validation_failed", "registration_id must be one of our GSTINs and party_id a vendor")
    cur.execute("SELECT id, gstin, state_code FROM party_gst_registrations WHERE party_id = %s AND (%s::uuid IS NULL OR id = %s) ORDER BY is_default DESC LIMIT 1",
                (b.party_id, b.party_gst_id, b.party_gst_id))
    sg = cur.fetchone()
    if b.party_gst_id and not sg:
        raise ApiError(400, "validation_failed", "party_gst_id does not belong to the vendor")
    supplier_state = sg["state_code"] if sg else reg["state_code"]          # an unregistered supplier is treated as intra-state, no ITC
    pos = b.place_of_supply or reg["state_code"]
    terms = b.agreed_terms_days if b.agreed_terms_days is not None else party["payment_terms_days"]
    ctx = tax.Context(doc_type="tax_invoice", supplier_state=supplier_state, place_of_supply=pos, doc_date=b.bill_date, buyer_gstin=None,
                      aato=None, deducts_194q=False, payment_terms_days=terms)
    res = tax.compute([tax.Line(l.description, l.hsn_sac, l.qty, l.rate, l.gst_rate, l.discount, l.hsn_sac.startswith("99")) for l in b.lines], ctx)
    if not sg and (res.cgst_total or res.sgst_total or res.igst_total) and b.itc_eligibility == "eligible":
        raise ApiError(400, "validation_failed", "An unregistered supplier cannot pass on GST credit; set itc_eligibility to ineligible")
    tds_amt, rate = Decimal(0), None
    if b.tds_section:
        try:
            tds_amt, rate = taxcalc.tds_amount(res.taxable_total, b.tds_section, bool(party["pan"]))
        except ValueError as e:
            raise ApiError(400, "validation_failed", str(e))
    exact = res.taxable_total + res.cgst_total + res.sgst_total + res.igst_total
    return {"res": res, "sg": sg, "party": party, "supplier_state": supplier_state, "pos": pos, "grand": exact, "tds": tds_amt, "tds_rate": rate,
            "due": b.due_date or b.bill_date + dt.timedelta(days=terms), "terms": terms}


def load_bill(cur, entity_id, bid) -> dict:
    cur.execute("""SELECT b.id, b.party_id, b.registration_id, b.bill_no, b.bill_date, b.accepted_on, b.place_of_supply, b.reverse_charge,
                          b.taxable_total, b.cgst_total, b.sgst_total, b.igst_total, b.grand_total, b.tds_section, b.tds_amount,
                          b.itc_eligibility::text, b.agreed_terms_days, b.due_date, b.msme_applicable, b.msme_pay_by, b.amount_paid,
                          b.balance_due, b.document_id, b.status,
                          (SELECT m.status FROM itc_matches m WHERE m.purchase_bill_id = b.id ORDER BY m.run_at DESC LIMIT 1) AS itc_match_status
                     FROM purchase_bills b WHERE b.id = %s AND b.entity_id = %s""", (bid, entity_id))
    b = cur.fetchone()
    if not b:
        raise ApiError(404, "not_found", "Not found")
    cur.execute("""SELECT line_no, item_id, description, hsn_sac, qty, gst_rate, taxable_value, cgst_amount, sgst_amount, igst_amount, expense_account_id
                     FROM purchase_bill_lines WHERE bill_id = %s ORDER BY line_no""", (bid,))
    b["lines"] = cur.fetchall()
    return js(b)


@router.post(E + "/purchase-bills", status_code=201, tags=["Purchases"])
def create_bill(entity_id: uuid.UUID, body: PurchaseBillIn, p: Principal = Auth):
    with tx(p.user_id) as cur:
        require(cur, entity_id, "purchases:write")
        c = _prepare_bill(cur, entity_id, body)
        res = c["res"]
        cur.execute("""INSERT INTO purchase_bills (entity_id, party_id, party_gst_id, supplier_gstin, registration_id, bill_no, bill_date, accepted_on,
                              supplier_state, place_of_supply, taxable_total, cgst_total, sgst_total, igst_total, grand_total, tds_section,
                              tds_amount, itc_eligibility, agreed_terms_days, due_date, document_id)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id""",
                    (entity_id, body.party_id, c["sg"]["id"] if c["sg"] else None, c["sg"]["gstin"] if c["sg"] else None, body.registration_id,
                     body.bill_no, body.bill_date, body.accepted_on, c["supplier_state"], c["pos"], res.taxable_total, res.cgst_total, res.sgst_total,
                     res.igst_total, c["grand"], body.tds_section, c["tds"], body.itc_eligibility, body.agreed_terms_days, c["due"], body.document_id))
        bid = cur.fetchone()["id"]
        for l, src, r in zip(res.lines, body.lines, res.lines):
            cur.execute("""INSERT INTO purchase_bill_lines (bill_id, line_no, item_id, description, hsn_sac, qty, taxable_value, gst_rate, cgst_amount,
                                  sgst_amount, igst_amount, itc_eligibility, expense_account_id) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                        (bid, l["line_no"], src.item_id, src.description, src.hsn_sac, src.qty, l["taxable_value"], src.gst_rate, l["cgst_amount"],
                         l["sgst_amount"], l["igst_amount"], body.itc_eligibility, src.expense_account_id))
        audit(cur, entity_id, "purchase_bill.drafted", "purchase_bills", bid, after={"grand_total": c["grand"], "tds": c["tds"]})
        return load_bill(cur, entity_id, bid)


@router.get(E + "/purchase-bills", tags=["Purchases"])
def list_bills(entity_id: uuid.UUID, p: Principal = Auth, party_id: uuid.UUID | None = None, status: str | None = None, msme: bool | None = None,
               due_before: dt.date | None = None, limit: int = Query(default=50, ge=1, le=200), cursor: str | None = None):
    off = offset_of(cursor)
    with tx(p.user_id) as cur:
        require(cur, entity_id, "purchases:read")
        cur.execute("""SELECT id FROM purchase_bills WHERE entity_id = %s AND (%s::uuid IS NULL OR party_id = %s) AND (%s::text IS NULL OR status = %s)
                          AND (%s::boolean IS NULL OR msme_applicable = %s) AND (%s::date IS NULL OR due_date <= %s)
                        ORDER BY due_date, id LIMIT %s OFFSET %s""", (entity_id, party_id, party_id, status, status, msme, msme, due_before, due_before, limit + 1, off))
        return page([load_bill(cur, entity_id, r["id"]) for r in cur.fetchall()], limit, off)


@router.get(E + "/purchase-bills/{bill_id}", tags=["Purchases"])
def get_bill(entity_id: uuid.UUID, bill_id: uuid.UUID, p: Principal = Auth):
    with tx(p.user_id) as cur:
        require(cur, entity_id, "purchases:read")
        return load_bill(cur, entity_id, bill_id)


@router.post(E + "/purchase-bills/{bill_id}/book", tags=["Purchases"])
def book_bill(entity_id: uuid.UUID, bill_id: uuid.UUID, p: Principal = Auth,
              idempotency_key: str | None = Header(default=None, alias="Idempotency-Key")):
    with tx(p.user_id) as cur:
        require(cur, entity_id, "purchases:write")
        replay = idempotency_begin(cur, idempotency_key, "POST", f"/entities/{entity_id}/purchase-bills/{bill_id}/book", {})
        if replay:
            return JSONResponse(replay["body"], status_code=replay["status"])
        cur.execute("SELECT * FROM purchase_bills WHERE id = %s AND entity_id = %s FOR UPDATE", (bill_id, entity_id))
        b = cur.fetchone()
        if not b:
            raise ApiError(404, "not_found", "Not found")
        if b["status"] != "draft":
            raise ApiError(409, "conflict", f"Bill is already {b['status']}")
        stock = inventory.bill_stock_lines(cur, entity_id, b)                    # lines for tracked goods: they go to Inventory, not to an expense
        stock_ids = {l["id"] for l in stock}
        cur.execute("SELECT id, expense_account_id, taxable_value FROM purchase_bill_lines WHERE bill_id = %s ORDER BY line_no", (bill_id,))
        split: dict[str, Decimal] = {}
        for l in cur.fetchall():
            key = "inventory" if l["id"] in stock_ids else "account:" + str(l["expense_account_id"]) if l["expense_account_id"] else "purchases"
            split[key] = split.get(key, Decimal(0)) + l["taxable_value"]
        own_tax = {"inventory": sum((l["cost"] - l["taxable_value"] for l in stock), Decimal(0))} if stock else None
        try:
            lines = ledger.purchase_bill_lines({**b, "itc_eligibility": b["itc_eligibility"]}, list(split.items()), own_tax)
        except ledger.PostingError as e:
            raise ApiError(409, "conflict", str(e))
        jid = ledger.post_journal(cur, entity_id, b["bill_date"], "purchase_bill", bill_id, f"Bill {b['bill_no']}", lines)
        cur.execute("UPDATE purchase_bills SET status = 'booked', journal_entry_id = %s WHERE id = %s", (jid, bill_id))
        inventory.receive_bill(cur, entity_id, b, stock, jid)
        if b["tds_section"] and b["tds_amount"] > 0:
            cur.execute("SELECT pan FROM parties WHERE id = %s", (b["party_id"],))
            pan = cur.fetchone()["pan"]
            rate = taxcalc.tds_rate(b["tds_section"], bool(pan))
            cur.execute("""INSERT INTO tds_deductions (entity_id, deductee_party_id, deductee_pan, section, deducted_on, base_amount, rate_pct, tds_amount,
                                  source_kind, source_id) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 'purchase_bill', %s)""",
                        (entity_id, b["party_id"], pan, b["tds_section"], b["bill_date"], b["taxable_total"], rate, b["tds_amount"], bill_id))
        audit(cur, entity_id, "purchase_bill.booked", "purchase_bills", bill_id, after={"journal": jid})
        out = load_bill(cur, entity_id, bill_id)
        idempotency_finish(cur, idempotency_key, 200, out)
    return out


# ================================================================== MSME 45-day clock
@router.get(E + "/msme-payables", tags=["Purchases"])
def msme_payables(entity_id: uuid.UUID, p: Principal = Auth, breached: bool | None = None):
    with tx(p.user_id) as cur:
        require(cur, entity_id, "purchases:read")
        cur.execute("""SELECT bill_id, party_id, legal_name, msme_class::text, bill_no, accepted_on, msme_pay_by, days_left, breached, balance_due,
                              CASE WHEN breached THEN balance_due ELSE 0 END AS deduction_at_risk_43bh
                         FROM v_msme_payables WHERE entity_id = %s AND (%s::boolean IS NULL OR breached = %s) ORDER BY msme_pay_by, bill_no""",
                    (entity_id, breached, breached))
        return js(cur.fetchall())


def msme_alerts(cur, entity_id, today: dt.date | None = None, within_days: int = 7) -> int:
    """Emit msme_payable.due_soon once per bill when its 45-day date is near, and once more if it is breached."""
    today = today or dt.date.today()
    cur.execute("""SELECT bill_id, bill_no, msme_pay_by, breached, balance_due FROM v_msme_payables WHERE entity_id = %s AND msme_pay_by - %s <= %s""",
                (entity_id, today, within_days))
    n = 0
    for b in cur.fetchall():
        stage = "breached" if b["msme_pay_by"] < today else "due_soon"
        cur.execute("""SELECT 1 FROM event_outbox WHERE entity_id = %s AND type = 'msme_payable.due_soon' AND subject_id = %s AND data->>'stage' = %s""",
                    (entity_id, b["bill_id"], stage))
        if cur.fetchone():
            continue
        emit(cur, entity_id, "msme_payable.due_soon", "purchase_bills", b["bill_id"],
             {"stage": stage, "bill_no": b["bill_no"], "pay_by": b["msme_pay_by"], "balance_due": money(b["balance_due"])})
        n += 1
    return n


# ================================================================== vendor payments
class AllocIn(BaseModel):
    bill_id: uuid.UUID
    amount: Decimal = Field(gt=0)


class VendorPaymentIn(BaseModel):
    party_id: uuid.UUID
    amount: Decimal = Field(gt=0)
    method: Literal["neft", "rtgs", "imps", "upi", "cheque", "netbanking", "adjustment"]
    bank_account_id: uuid.UUID
    scheduled_on: dt.date | None = None
    allocations: list[AllocIn] = Field(min_length=1)


def load_payment(cur, entity_id, pid) -> dict:
    cur.execute("""SELECT id, payment_no, party_id, scheduled_on, paid_on, amount, method::text, bank_account_id, utr, approval_request_id, status
                     FROM vendor_payments WHERE id = %s AND entity_id = %s""", (pid, entity_id))
    v = cur.fetchone()
    if not v:
        raise ApiError(404, "not_found", "Not found")
    cur.execute("SELECT bill_id, amount FROM vendor_payment_allocations WHERE payment_id = %s ORDER BY bill_id", (pid,))
    v["allocations"] = cur.fetchall()
    return js(v)


@router.post(E + "/vendor-payments", status_code=201, tags=["Purchases"])
def create_payment(entity_id: uuid.UUID, body: VendorPaymentIn, p: Principal = Auth,
                   idempotency_key: str | None = Header(default=None, alias="Idempotency-Key")):
    with tx(p.user_id) as cur:
        require(cur, entity_id, "purchases:write")
        replay = idempotency_begin(cur, idempotency_key, "POST", f"/entities/{entity_id}/vendor-payments", body.model_dump(mode="json"))
        if replay:
            return JSONResponse(replay["body"], status_code=replay["status"])
        allocated = sum((a.amount for a in body.allocations), Decimal(0))
        if allocated > body.amount:
            raise ApiError(400, "validation_failed", "Allocations exceed the payment amount")
        cur.execute("SELECT legal_name FROM parties WHERE id = %s AND entity_id = %s AND is_vendor", (body.party_id, entity_id))
        vendor = cur.fetchone()
        cur.execute("SELECT 1 FROM bank_accounts WHERE id = %s AND entity_id = %s AND is_active", (body.bank_account_id, entity_id))
        if not vendor or not cur.fetchone():
            raise ApiError(400, "validation_failed", "party_id must be a vendor and bank_account_id one of our accounts")
        for a in body.allocations:
            cur.execute("SELECT balance_due, status, party_id FROM purchase_bills WHERE id = %s AND entity_id = %s FOR UPDATE", (a.bill_id, entity_id))
            b = cur.fetchone()
            if not b or b["party_id"] != body.party_id or b["status"] not in ("booked", "approved", "partially_paid"):
                raise ApiError(400, "validation_failed", f"Bill {a.bill_id} is not a payable bill of this vendor")
            cur.execute("""SELECT coalesce(sum(a.amount), 0) AS open FROM vendor_payment_allocations a JOIN vendor_payments v ON v.id = a.payment_id
                            WHERE a.bill_id = %s AND v.status IN ('scheduled', 'approved', 'sent')""", (a.bill_id,))
            if a.amount > b["balance_due"] - cur.fetchone()["open"]:
                raise ApiError(409, "over_settlement", "Allocation exceeds what is still unpaid on the bill", f"bill {a.bill_id}")
        cur.execute("""INSERT INTO vendor_payments (entity_id, party_id, payment_no, scheduled_on, amount, method, bank_account_id, status)
                       VALUES (%s, %s, %s, %s, %s, %s::payment_method, %s, 'scheduled') RETURNING id, payment_no""",
                    (entity_id, body.party_id, next_number(cur, entity_id, "vendor_payments", "payment_no", "VP-", 4), body.scheduled_on, body.amount,
                     body.method, body.bank_account_id))
        vp = cur.fetchone()
        for a in body.allocations:
            cur.execute("INSERT INTO vendor_payment_allocations (payment_id, bill_id, amount) VALUES (%s, %s, %s)", (vp["id"], a.bill_id, a.amount))
        cur.execute("""INSERT INTO approval_requests (entity_id, subject, subject_id, summary, amount, requested_by) VALUES (%s, 'vendor_payment', %s, %s, %s,
                              app.current_user_id()) RETURNING id""", (entity_id, vp["id"], f"Pay {vendor['legal_name']} {money(body.amount)} ({vp['payment_no']})", body.amount))
        cur.execute("UPDATE vendor_payments SET approval_request_id = %s WHERE id = %s", (cur.fetchone()["id"], vp["id"]))
        audit(cur, entity_id, "vendor_payment.requested", "vendor_payments", vp["id"], after={"amount": body.amount})
        emit(cur, entity_id, "approval.requested", "vendor_payments", vp["id"], {"summary": f"Pay {vendor['legal_name']}"})
        out = load_payment(cur, entity_id, vp["id"])
        idempotency_finish(cur, idempotency_key, 201, out)
    return JSONResponse(out, status_code=201)


@router.get(E + "/vendor-payments", tags=["Purchases"])
def list_payments(entity_id: uuid.UUID, p: Principal = Auth, status: str | None = None, limit: int = Query(default=50, ge=1, le=200), cursor: str | None = None):
    off = offset_of(cursor)
    with tx(p.user_id) as cur:
        require(cur, entity_id, "purchases:read")
        cur.execute("SELECT id FROM vendor_payments WHERE entity_id = %s AND (%s::text IS NULL OR status = %s) ORDER BY payment_no DESC LIMIT %s OFFSET %s",
                    (entity_id, status, status, limit + 1, off))
        return page([load_payment(cur, entity_id, r["id"]) for r in cur.fetchall()], limit, off)


@router.get(E + "/vendor-payments/{payment_id}", tags=["Purchases"])
def get_payment(entity_id: uuid.UUID, payment_id: uuid.UUID, p: Principal = Auth):
    with tx(p.user_id) as cur:
        require(cur, entity_id, "purchases:read")
        return load_payment(cur, entity_id, payment_id)


@router.post(E + "/vendor-payments/{payment_id}/release", status_code=202, tags=["Purchases"])
def release_payment(entity_id: uuid.UUID, payment_id: uuid.UUID, p: Principal = Auth,
                    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key")):
    with tx(p.user_id) as cur:
        require(cur, entity_id, "purchases:pay")
        replay = idempotency_begin(cur, idempotency_key, "POST", f"/entities/{entity_id}/vendor-payments/{payment_id}/release", {})
        if replay:
            return JSONResponse(replay["body"], status_code=replay["status"])
        cur.execute("SELECT status FROM vendor_payments WHERE id = %s AND entity_id = %s FOR UPDATE", (payment_id, entity_id))
        v = cur.fetchone()
        if not v:
            raise ApiError(404, "not_found", "Not found")
        if v["status"] in ("sent", "paid"):
            raise ApiError(409, "conflict", f"Payment is already {v['status']}")
        if v["status"] != "approved":
            raise ApiError(409, "approval_required", "The payment's approval request has not been approved")
        cur.execute("UPDATE vendor_payments SET status = 'sent' WHERE id = %s", (payment_id,))
        cur.execute("""INSERT INTO jobs (entity_id, kind, subject, subject_id, payload, created_by) VALUES (%s, 'bank.release_payment', 'vendor_payments', %s, '{}',
                              app.current_user_id()) RETURNING id, kind, status, progress, subject, subject_id, result, created_at, finished_at""", (entity_id, payment_id))
        job = js(cur.fetchone())
        audit(cur, entity_id, "vendor_payment.released", "vendor_payments", payment_id)
        idempotency_finish(cur, idempotency_key, 202, job)
    return JSONResponse(job, status_code=202, headers={"Location": f"/v1/jobs/{job['id']}"})


BANK = adapters.proxy("bank")


def handle_release_payment(cur, job: dict) -> dict:
    """Worker: send an approved payment to the bank, then mark bills paid and post Dr payables / Cr bank."""
    cur.execute("SELECT * FROM vendor_payments WHERE id = %s FOR UPDATE", (job["subject_id"],))
    v = cur.fetchone()
    if v["status"] != "sent":
        raise RuntimeError(f"payment is {v['status']}, expected sent")
    entity_id = v["entity_id"]
    res = BANK.send_payment({"payment_no": v["payment_no"], "amount": str(v["amount"]), "method": v["method"]})
    paid_on = dt.date.today()
    cur.execute("UPDATE vendor_payments SET status = 'paid', paid_on = %s, utr = %s WHERE id = %s", (paid_on, res["utr"], v["id"]))
    cur.execute("UPDATE vendor_payment_allocations SET amount = amount WHERE payment_id = %s", (v["id"],))       # re-fires the bill's amount_paid refresh
    cur.execute("""UPDATE purchase_bills b SET status = CASE WHEN b.balance_due = 0 THEN 'paid' ELSE 'partially_paid' END
                    WHERE b.id IN (SELECT bill_id FROM vendor_payment_allocations WHERE payment_id = %s)""", (v["id"],))
    cur.execute("SELECT ledger_account_id FROM bank_accounts WHERE id = %s", (v["bank_account_id"],))
    jid = ledger.post_journal(cur, entity_id, paid_on, "vendor_payment", v["id"], f"Payment {v['payment_no']}",
                              ledger.vendor_payment_lines(amount=v["amount"], party_id=v["party_id"],
                                                          bank_account_key="account:" + str(cur.fetchone()["ledger_account_id"])))
    cur.execute("UPDATE vendor_payments SET journal_entry_id = %s WHERE id = %s", (jid, v["id"]))
    audit(cur, entity_id, "vendor_payment.paid", "vendor_payments", v["id"], after={"utr": res["utr"]}, actor_kind="integration")
    emit(cur, entity_id, "job.succeeded", "vendor_payments", v["id"], {"kind": "bank.release_payment"})
    return {"utr": res["utr"], "paid_on": paid_on.isoformat()}


def handle_msme_alerts(cur, job: dict) -> dict:
    return {"events": msme_alerts(cur, job["entity_id"])}
