"""Opening position imported from the company's earlier books: opening trial balance, open invoices, open bills.

One endpoint checks (dry run, the default) or saves a batch; a batch can be listed and undone as a whole. Nothing here calls a portal, issues an
e-invoice or reaches the GST returns: the rows are records of what was true on the cut-off date, so the receivables ageing, payables and
balance sheet start from the real position. See docs/API_GUIDE.md.
"""
from __future__ import annotations

import datetime as dt
import json
import uuid
from decimal import Decimal
from typing import Literal

from fastapi import APIRouter, Header
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from . import history, ledger
from .core import ApiError, Auth, Principal, audit, idempotency_begin, idempotency_finish, require, tx
from .routes_orders import js

router = APIRouter()
E = "/entities/{entity_id}"
CHECKS = {"opening_balances": history.check_opening, "open_invoices": history.check_invoices, "open_bills": history.check_bills}


class HistoryImportIn(BaseModel):
    kind: Literal["opening_balances", "open_invoices", "open_bills"]
    as_of: dt.date
    label: str | None = Field(default=None, max_length=120)
    dry_run: bool = True
    rows: list[dict] = Field(min_length=1, max_length=5000)


def _gstins(cur, entity_id) -> list[dict]:
    cur.execute("""SELECT id, number, state_code FROM entity_registrations WHERE entity_id = %s AND kind = 'gstin' AND status = 'active'
                    ORDER BY created_at, number""", (entity_id,))
    return cur.fetchall()


def _party(cur, entity_id, cache: dict, name: str, gstin: str | None, *, customer: bool) -> uuid.UUID:
    key = (gstin or name.lower(), customer)
    if key in cache:
        return cache[key]
    cur.execute("""SELECT p.id FROM parties p LEFT JOIN party_gst_registrations g ON g.party_id = p.id
                    WHERE p.entity_id = %s AND ((%s::text IS NOT NULL AND g.gstin = %s) OR lower(p.legal_name) = lower(%s)) ORDER BY (g.gstin = %s) DESC NULLS LAST LIMIT 1""",
                (entity_id, gstin, gstin, name, gstin))
    r = cur.fetchone()
    if r:
        col = "is_customer" if customer else "is_vendor"
        cur.execute(f"UPDATE parties SET {col} = true WHERE id = %s AND NOT {col}", (r["id"],))
        pid = r["id"]
    else:
        cur.execute("INSERT INTO parties (entity_id, legal_name, is_customer, is_vendor, pan) VALUES (%s, %s, %s, %s, %s) RETURNING id",
                    (entity_id, name, customer, not customer, gstin[2:12] if gstin else None))
        pid = cur.fetchone()["id"]
        if gstin:
            cur.execute("INSERT INTO party_gst_registrations (party_id, gstin, state_code, is_default) VALUES (%s, %s, %s, true)", (pid, gstin, gstin[:2]))
    cache[key] = pid
    return pid


def _db_problems(cur, entity_id, kind: str, clean: list[dict]) -> list[dict]:
    """What only the database can tell: unknown state codes, numbers already used, accounts whose type disagrees."""
    out = []
    if kind == "opening_balances":
        cur.execute("SELECT id FROM import_batches WHERE entity_id = %s AND kind = 'opening_balances' AND undone_at IS NULL", (entity_id,))
        if cur.fetchone():
            raise ApiError(409, "conflict", "An opening balance has already been imported", "Undo that batch first, or the opening position would be counted twice.")
        cur.execute("SELECT code, type::text AS t FROM ledger_accounts WHERE entity_id = %s", (entity_id,))
        have = {r["code"]: r["t"] for r in cur.fetchall()}
        for i, c in enumerate(clean, 1):
            if c["account_code"] in have and have[c["account_code"]] != c["type"]:
                out.append({"row": i, "field": "type", "message": f"Account {c['account_code']} already exists as {have[c['account_code']]}, not {c['type']}"})
        return out
    cur.execute("SELECT gst_code FROM ref_states")
    states = {r["gst_code"] for r in cur.fetchall()}
    for i, c in enumerate(clean, 1):
        for f in ("place_of_supply", "supplier_state"):
            if c.get(f) and c[f] not in states:
                out.append({"row": i, "field": f, "message": f"{c[f]} is not a GST state code"})
    if kind == "open_invoices":
        for i, c in enumerate(clean, 1):
            cur.execute("""SELECT 1 FROM sales_documents WHERE registration_id = %s AND number = %s AND fy = app.fy_of(%s::date)""", (c["registration_id"], c["number"], c["date"]))
            if cur.fetchone():
                out.append({"row": i, "field": "number", "message": f"Invoice {c['number']} already exists for that financial year"})
    else:
        for i, c in enumerate(clean, 1):
            cur.execute("""SELECT 1 FROM purchase_bills b JOIN parties p ON p.id = b.party_id WHERE b.entity_id = %s AND lower(p.legal_name) = lower(%s)
                              AND b.bill_no = %s AND b.fy = app.fy_of(%s::date)""", (entity_id, c["supplier_name"], c["bill_no"], c["date"]))
            if cur.fetchone():
                out.append({"row": i, "field": "bill_no", "message": f"Bill {c['bill_no']} from {c['supplier_name']} already exists for that financial year"})
    return out


def _apply_opening(cur, entity_id, batch_id, body: HistoryImportIn, clean: list[dict]) -> dict:
    lines, created = [], 0
    for c in clean:
        cur.execute("SELECT id FROM ledger_accounts WHERE entity_id = %s AND code = %s", (entity_id, c["account_code"]))
        a = cur.fetchone()
        if not a:
            cur.execute("""INSERT INTO ledger_accounts (entity_id, code, name, type, subtype) VALUES (%s, %s, %s, %s::account_type, %s) RETURNING id""",
                        (entity_id, c["account_code"], c["account_name"], c["type"], c["subtype"]))
            a = cur.fetchone()
            created += 1
        lines.append(ledger.Line(key=f"account:{a['id']}", debit=c["debit"], credit=c["credit"], memo="Opening balance"))
    jid = ledger.post_journal(cur, entity_id, body.as_of, "opening", batch_id, body.label or "Opening balances", lines)
    cur.execute("UPDATE journal_entries SET import_batch_id = %s WHERE id = %s", (batch_id, jid))
    return {"journal_entry_id": str(jid), "accounts_created": created}


def _apply_invoices(cur, entity_id, batch_id, clean: list[dict]) -> dict:
    cache = {}
    for c in clean:
        pid = _party(cur, entity_id, cache, c["customer_name"], c["customer_gstin"], customer=True)
        settled = c["total"] - c["outstanding"]
        status = "paid" if c["outstanding"] == 0 else "partially_paid" if settled > 0 else "unpaid"
        cur.execute("""INSERT INTO sales_documents (entity_id, doc_type, number, doc_date, registration_id, supplier_state, party_id, buyer_gstin, buyer_legal_name, bill_to,
                              place_of_supply, supply_type, due_date, taxable_total, cgst_total, sgst_total, igst_total, round_off, grand_total, amount_settled,
                              status, payment_status, irn_status, issued_at, issued_by, created_by, import_batch_id)
                       VALUES (%s, 'tax_invoice', %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::supply_type, %s, %s, %s, %s, %s, %s, %s, %s, 'issued', %s, 'not_required',
                               now(), app.current_user_id(), app.current_user_id(), %s)""",
                    (entity_id, c["number"], c["date"], c["registration_id"], c["supplier_state"], pid, c["customer_gstin"], c["customer_name"],
                     json.dumps({"imported": True}), c["place_of_supply"], "b2b" if c["customer_gstin"] else "b2c_small", c["due_date"], c["taxable"], c["cgst"], c["sgst"],
                     c["igst"], c["round_off"], c["total"], settled, status, batch_id))
    return {"parties": len(cache)}


def _apply_bills(cur, entity_id, batch_id, clean: list[dict]) -> dict:
    cache = {}
    for c in clean:
        pid = _party(cur, entity_id, cache, c["supplier_name"], c["supplier_gstin"], customer=False)
        paid = c["total"] - c["tds"] - c["outstanding"]
        status = "paid" if c["outstanding"] == 0 else "partially_paid" if paid > 0 else "booked"
        claim = f"{c['date'].year}-{c['date'].month:02d}"                 # the input credit was taken in that month's return, in the earlier books
        cur.execute("""INSERT INTO purchase_bills (entity_id, party_id, supplier_gstin, registration_id, bill_no, bill_date, accepted_on, supplier_state, place_of_supply,
                              taxable_total, cgst_total, sgst_total, igst_total, grand_total, tds_amount, itc_claim_period, due_date, amount_paid, status, import_batch_id)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                    (entity_id, pid, c["supplier_gstin"], c["registration_id"], c["bill_no"], c["date"], c["accepted_on"], c["supplier_state"], c["place_of_supply"],
                     c["taxable"], c["cgst"], c["sgst"], c["igst"], c["total"], c["tds"], claim, c["due_date"], paid, status, batch_id))
    return {"parties": len(cache)}


@router.post(E + "/history/imports", tags=["Finance & books"])
def import_history(entity_id: uuid.UUID, body: HistoryImportIn, p: Principal = Auth,
                   idempotency_key: str | None = Header(default=None, alias="Idempotency-Key")):
    today = dt.date.today()
    with tx(p.user_id) as cur:
        require(cur, entity_id, "finance:write")
        if not body.dry_run:
            replay = idempotency_begin(cur, idempotency_key, "POST", f"/entities/{entity_id}/history/imports", body.model_dump(mode="json"))
            if replay:
                return JSONResponse(replay["body"], status_code=replay["status"])
        if body.as_of > today:
            raise ApiError(400, "validation_failed", "The cut-off date cannot be in the future")
        regs = _gstins(cur, entity_id)
        if body.kind != "opening_balances" and not regs:
            raise ApiError(409, "conflict", "This company has no active GST registration", "Add the GSTIN under Settings, then import invoices and bills.")
        check = CHECKS[body.kind]
        clean, problems, summary, warnings = (check(body.rows, body.as_of, today) if body.kind == "opening_balances"
                                              else check(body.rows, body.as_of, today, regs))
        if not problems:
            problems = _db_problems(cur, entity_id, body.kind, clean)
        shown = {"kind": body.kind, "dry_run": body.dry_run, "accepted": 0 if problems else len(clean), "problems": problems[:200], "problem_count": len(problems),
                 "warnings": warnings[:50], "summary": summary, "batch_id": None}
        if problems and not body.dry_run:
            raise ApiError(422, "import_rejected", "Nothing was imported", f"{len(problems)} row problem(s); fix them and try again.",
                           errors=[{"field": f"rows[{x['row']}].{x['field']}" if x["row"] else "rows", "message": x["message"]} for x in problems[:50]])
        if body.dry_run or problems:
            return js(shown)
        cur.execute("""INSERT INTO import_batches (entity_id, kind, label, as_of, row_count, summary, created_by) VALUES (%s, %s, %s, %s, %s, %s, app.current_user_id())
                       RETURNING id""", (entity_id, body.kind, body.label, body.as_of, len(clean), json.dumps(summary)))
        batch_id = cur.fetchone()["id"]
        extra = (_apply_opening(cur, entity_id, batch_id, body, clean) if body.kind == "opening_balances"
                 else _apply_invoices(cur, entity_id, batch_id, clean) if body.kind == "open_invoices" else _apply_bills(cur, entity_id, batch_id, clean))
        cur.execute("UPDATE import_batches SET summary = summary || %s::jsonb WHERE id = %s", (json.dumps(extra), batch_id))
        audit(cur, entity_id, "history.imported", "import_batches", batch_id, after={"kind": body.kind, "rows": len(clean), "as_of": str(body.as_of)})
        out = js({**shown, "batch_id": batch_id, "summary": {**summary, **extra}})
        idempotency_finish(cur, idempotency_key, 200, out)
        return out


def _batch(row) -> dict:
    return {"id": row["id"], "kind": row["kind"], "label": row["label"], "as_of": row["as_of"], "row_count": row["row_count"], "summary": row["summary"],
            "created_at": row["created_at"], "undone_at": row["undone_at"]}


@router.get(E + "/history/batches", tags=["Finance & books"])
def list_batches(entity_id: uuid.UUID, p: Principal = Auth):
    with tx(p.user_id) as cur:
        require(cur, entity_id, "finance:read")
        cur.execute("SELECT * FROM import_batches WHERE entity_id = %s ORDER BY created_at DESC LIMIT 100", (entity_id,))
        return {"data": js([_batch(r) for r in cur.fetchall()]), "next_cursor": None}


@router.post(E + "/history/batches/{batch_id}/undo", tags=["Finance & books"])
def undo_batch(entity_id: uuid.UUID, batch_id: uuid.UUID, p: Principal = Auth,
               idempotency_key: str | None = Header(default=None, alias="Idempotency-Key")):
    with tx(p.user_id) as cur:
        require(cur, entity_id, "finance:write")
        replay = idempotency_begin(cur, idempotency_key, "POST", f"/entities/{entity_id}/history/batches/{batch_id}/undo", {})
        if replay:
            return JSONResponse(replay["body"], status_code=replay["status"])
        cur.execute("SELECT * FROM import_batches WHERE id = %s AND entity_id = %s FOR UPDATE", (batch_id, entity_id))
        b = cur.fetchone()
        if not b:
            raise ApiError(404, "not_found", "Not found")
        if b["undone_at"]:
            raise ApiError(409, "conflict", "This import was already undone")
        if b["kind"] == "open_invoices":
            cur.execute("SELECT count(*) AS n, coalesce(sum(amount_settled), 0) AS s FROM sales_documents WHERE import_batch_id = %s", (batch_id,))
            r = cur.fetchone()
            if r["s"] != Decimal(b["summary"]["settled"]):
                raise ApiError(409, "conflict", "Money has been received against these invoices since the import", "Undo is only possible while they are as imported.")
            try:
                cur.execute("SAVEPOINT undo")
                cur.execute("DELETE FROM sales_documents WHERE import_batch_id = %s", (batch_id,))
            except Exception:
                cur.execute("ROLLBACK TO SAVEPOINT undo")
                raise ApiError(409, "conflict", "Something refers to these invoices (a reminder, receipt or credit note), so they cannot be removed")
            removed = r["n"]
        elif b["kind"] == "open_bills":
            cur.execute("SELECT count(*) AS n, coalesce(sum(amount_paid), 0) AS s FROM purchase_bills WHERE import_batch_id = %s", (batch_id,))
            r = cur.fetchone()
            if r["s"] != Decimal(b["summary"]["settled"]):
                raise ApiError(409, "conflict", "Payments have been made against these bills since the import", "Undo is only possible while they are as imported.")
            try:
                cur.execute("SAVEPOINT undo")
                cur.execute("DELETE FROM purchase_bills WHERE import_batch_id = %s", (batch_id,))
            except Exception:
                cur.execute("ROLLBACK TO SAVEPOINT undo")
                raise ApiError(409, "conflict", "Something refers to these bills (a payment or a note), so they cannot be removed")
            removed = r["n"]
        else:
            cur.execute("SELECT id FROM journal_entries WHERE import_batch_id = %s AND status = 'posted' AND reversal_of IS NULL", (batch_id,))
            j = cur.fetchone()
            ledger.reverse_journal(cur, entity_id, j["id"], dt.date.today(), "Opening balances undone")
            removed = b["row_count"]
        cur.execute("UPDATE import_batches SET undone_at = now(), undone_by = app.current_user_id() WHERE id = %s RETURNING *", (batch_id,))
        out = js({**_batch(cur.fetchone()), "removed": removed})
        audit(cur, entity_id, "history.undone", "import_batches", batch_id, after={"kind": b["kind"], "removed": removed})
        idempotency_finish(cur, idempotency_key, 200, out)
        return out
