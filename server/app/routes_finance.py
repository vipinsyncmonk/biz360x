"""Finance & books: accounts, journals, periods and month-end close, banking and matching, reports."""
from __future__ import annotations

import calendar
import datetime as dt
import re
import uuid
from decimal import Decimal
from typing import Literal

from fastapi import APIRouter, Header, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from . import ledger
from .core import ApiError, Auth, Principal, audit, emit, idempotency_begin, idempotency_finish, money, require, tx
from .routes_orders import js, offset_of, page

router = APIRouter()
E = "/entities/{entity_id}"
ZERO = Decimal(0)


# ------------------------------------------------------------------ accounts
class AccountIn(BaseModel):
    code: str
    name: str
    type: Literal["asset", "liability", "equity", "income", "expense"]
    subtype: str | None = None
    schedule_iii_head: str | None = None
    parent_id: uuid.UUID | None = None


@router.get(E + "/accounts", tags=["Finance & books"])
def list_accounts(entity_id: uuid.UUID, p: Principal = Auth, type: str | None = None, q: str | None = None,
                  limit: int = Query(default=100, ge=1, le=500), cursor: str | None = None):
    off = offset_of(cursor)
    with tx(p.user_id) as cur:
        require(cur, entity_id, "finance:read")
        cur.execute("""SELECT id, code, name, type::text, subtype, schedule_iii_head, parent_id, is_system, is_active FROM ledger_accounts
                        WHERE entity_id = %s AND (%s::text IS NULL OR type::text = %s) AND (%s::text IS NULL OR name ILIKE '%%' || %s || '%%' OR code = %s)
                        ORDER BY code LIMIT %s OFFSET %s""", (entity_id, type, type, q, q, q, limit + 1, off))
        return page(js(cur.fetchall()), limit, off)


@router.post(E + "/accounts", status_code=201, tags=["Finance & books"])
def create_account(entity_id: uuid.UUID, body: AccountIn, p: Principal = Auth):
    with tx(p.user_id) as cur:
        require(cur, entity_id, "finance:write")
        cur.execute("""INSERT INTO ledger_accounts (entity_id, code, name, type, subtype, schedule_iii_head, parent_id)
                       VALUES (%s, %s, %s, %s::account_type, %s, %s, %s) RETURNING id, code, name, type::text, subtype, schedule_iii_head,
                              parent_id, is_system, is_active""",
                    (entity_id, body.code, body.name, body.type, body.subtype, body.schedule_iii_head, body.parent_id))
        acc = cur.fetchone()
        audit(cur, entity_id, "account.created", "ledger_accounts", acc["id"], after={"code": body.code})
        return js(acc)


@router.get(E + "/accounts/{account_id}", tags=["Finance & books"])
def get_account(entity_id: uuid.UUID, account_id: uuid.UUID, p: Principal = Auth):
    with tx(p.user_id) as cur:
        require(cur, entity_id, "finance:read")
        cur.execute("""SELECT id, code, name, type::text, subtype, schedule_iii_head, parent_id, is_system, is_active FROM ledger_accounts
                        WHERE id = %s AND entity_id = %s""", (account_id, entity_id))
        a = cur.fetchone()
        if not a:
            raise ApiError(404, "not_found", "Not found")
        return js(a)


# ------------------------------------------------------------------ journals
class JournalLineIn(BaseModel):
    account_id: uuid.UUID
    party_id: uuid.UUID | None = None
    registration_id: uuid.UUID | None = None
    cost_center: str | None = None
    debit: Decimal = Field(default=ZERO, ge=0)
    credit: Decimal = Field(default=ZERO, ge=0)
    memo: str | None = None


class JournalIn(BaseModel):
    entry_date: dt.date
    narration: str | None = None
    lines: list[JournalLineIn] = Field(min_length=2)


def load_journal(cur, entity_id, jid) -> dict:
    cur.execute("""SELECT id, entry_no, entry_date, source::text, source_id, narration, status, posted_at, reversal_of FROM journal_entries
                    WHERE id = %s AND entity_id = %s""", (jid, entity_id))
    j = cur.fetchone()
    if not j:
        raise ApiError(404, "not_found", "Not found")
    cur.execute("""SELECT line_no, account_id, party_id, registration_id, cost_center, debit, credit, memo FROM journal_lines
                    WHERE entry_id = %s ORDER BY line_no""", (jid,))
    j["lines"] = [{**l, "debit": money(l["debit"]), "credit": money(l["credit"])} for l in cur.fetchall()]
    return js(j)


@router.post(E + "/journals", status_code=201, tags=["Finance & books"])
def create_journal(entity_id: uuid.UUID, body: JournalIn, p: Principal = Auth):
    for l in body.lines:
        if (l.debit == 0) == (l.credit == 0):
            raise ApiError(400, "validation_failed", "Each line needs a debit or a credit, not both and not neither")
    with tx(p.user_id) as cur:
        require(cur, entity_id, "finance:write")
        cur.execute("INSERT INTO journal_entries (entity_id, entry_no, entry_date, source, narration) VALUES (%s, %s, %s, 'manual', %s) RETURNING id",
                    (entity_id, ledger.next_entry_no(cur, entity_id), body.entry_date, body.narration))
        jid = cur.fetchone()["id"]
        for i, l in enumerate(body.lines, 1):
            cur.execute("""INSERT INTO journal_lines (entry_id, line_no, account_id, party_id, registration_id, cost_center, debit, credit, memo)
                           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                        (jid, i, l.account_id, l.party_id, l.registration_id, l.cost_center, l.debit, l.credit, l.memo))
        audit(cur, entity_id, "journal.drafted", "journal_entries", jid)
        return load_journal(cur, entity_id, jid)


@router.get(E + "/journals", tags=["Finance & books"])
def list_journals(entity_id: uuid.UUID, p: Principal = Auth, from_: dt.date | None = Query(default=None, alias="from"), to: dt.date | None = None,
                  source: str | None = None, account_id: uuid.UUID | None = None, limit: int = Query(default=50, ge=1, le=200),
                  cursor: str | None = None):
    off = offset_of(cursor)
    with tx(p.user_id) as cur:
        require(cur, entity_id, "finance:read")
        cur.execute("""SELECT e.id FROM journal_entries e WHERE e.entity_id = %s AND (%s::date IS NULL OR e.entry_date >= %s)
                          AND (%s::date IS NULL OR e.entry_date <= %s) AND (%s::text IS NULL OR e.source::text = %s)
                          AND (%s::uuid IS NULL OR EXISTS (SELECT 1 FROM journal_lines l WHERE l.entry_id = e.id AND l.account_id = %s))
                        ORDER BY e.entry_date DESC, e.entry_no DESC LIMIT %s OFFSET %s""",
                    (entity_id, from_, from_, to, to, source, source, account_id, account_id, limit + 1, off))
        return page([load_journal(cur, entity_id, r["id"]) for r in cur.fetchall()], limit, off)


@router.get(E + "/journals/{journal_id}", tags=["Finance & books"])
def get_journal(entity_id: uuid.UUID, journal_id: uuid.UUID, p: Principal = Auth):
    with tx(p.user_id) as cur:
        require(cur, entity_id, "finance:read")
        return load_journal(cur, entity_id, journal_id)


@router.delete(E + "/journals/{journal_id}", status_code=204, tags=["Finance & books"])
def delete_journal(entity_id: uuid.UUID, journal_id: uuid.UUID, p: Principal = Auth):
    with tx(p.user_id) as cur:
        require(cur, entity_id, "finance:write")
        cur.execute("SELECT status FROM journal_entries WHERE id = %s AND entity_id = %s FOR UPDATE", (journal_id, entity_id))
        j = cur.fetchone()
        if not j:
            raise ApiError(404, "not_found", "Not found")
        if j["status"] != "draft":
            raise ApiError(409, "conflict", "Only drafts can be deleted; reverse a posted journal instead")
        cur.execute("DELETE FROM journal_entries WHERE id = %s", (journal_id,))
        audit(cur, entity_id, "journal.deleted", "journal_entries", journal_id)


@router.post(E + "/journals/{journal_id}/post", tags=["Finance & books"])
def post_journal(entity_id: uuid.UUID, journal_id: uuid.UUID, p: Principal = Auth,
                 idempotency_key: str | None = Header(default=None, alias="Idempotency-Key")):
    with tx(p.user_id) as cur:
        require(cur, entity_id, "finance:write")
        replay = idempotency_begin(cur, idempotency_key, "POST", f"/entities/{entity_id}/journals/{journal_id}/post", {})
        if replay:
            return JSONResponse(replay["body"], status_code=replay["status"])
        cur.execute("SELECT status FROM journal_entries WHERE id = %s AND entity_id = %s FOR UPDATE", (journal_id, entity_id))
        j = cur.fetchone()
        if not j:
            raise ApiError(404, "not_found", "Not found")
        if j["status"] != "draft":
            raise ApiError(409, "conflict", f"Journal is already {j['status']}")
        cur.execute("UPDATE journal_entries SET status = 'posted', posted_by = app.current_user_id() WHERE id = %s", (journal_id,))   # balance + period guards
        audit(cur, entity_id, "journal.posted", "journal_entries", journal_id)
        out = load_journal(cur, entity_id, journal_id)
        idempotency_finish(cur, idempotency_key, 200, out)
    return out


class ReverseIn(BaseModel):
    reversal_date: dt.date
    narration: str | None = None


@router.post(E + "/journals/{journal_id}/reverse", status_code=201, tags=["Finance & books"])
def reverse_journal(entity_id: uuid.UUID, journal_id: uuid.UUID, body: ReverseIn, p: Principal = Auth,
                    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key")):
    with tx(p.user_id) as cur:
        require(cur, entity_id, "finance:write")
        replay = idempotency_begin(cur, idempotency_key, "POST", f"/entities/{entity_id}/journals/{journal_id}/reverse", body.model_dump(mode="json"))
        if replay:
            return JSONResponse(replay["body"], status_code=replay["status"])
        try:
            rid = ledger.reverse_journal(cur, entity_id, journal_id, body.reversal_date, body.narration)
        except ledger.PostingError as e:
            raise ApiError(409, "conflict", str(e))
        if rid is None:
            raise ApiError(404, "not_found", "Not found")
        audit(cur, entity_id, "journal.reversed", "journal_entries", journal_id, after={"reversal": rid})
        out = load_journal(cur, entity_id, rid)
        idempotency_finish(cur, idempotency_key, 201, out)
    return JSONResponse(out, status_code=201)


# ------------------------------------------------------------------ periods and close
CLOSE_TASKS = [
    ("bank_feeds", "Bank feeds imported", True), ("sales_posted", "Sales invoices posted", True),
    ("purchases_posted", "Purchase bills posted", False), ("payroll_journal", "Payroll journal", False),
    ("depreciation", "Depreciation run", False), ("prepaids", "Prepaids and accruals", False),
    ("gst_recon", "GST reconciliation (books vs 2A)", False), ("bank_recon", "Bank reconciliation complete", True),
    ("branch_transfers", "Inter-branch stock transfers", False), ("cfo_review", "CFO review of P&L and balance sheet", False),
]


def ensure_periods(cur, entity_id, fy: str) -> None:
    cur.execute("SELECT 1 FROM fiscal_periods WHERE entity_id = %s AND fy = %s LIMIT 1", (entity_id, fy))
    if cur.fetchone():
        return
    year = int(fy[:4])
    for i in range(12):
        y, m = (year, 4 + i) if 4 + i <= 12 else (year + 1, 4 + i - 12)
        cur.execute("INSERT INTO fiscal_periods (entity_id, fy, period_start, period_end) VALUES (%s, %s, %s, %s) ON CONFLICT DO NOTHING",
                    (entity_id, fy, dt.date(y, m, 1), dt.date(y, m, calendar.monthrange(y, m)[1])))


def ensure_close_tasks(cur, entity_id, period) -> None:
    for i, (key, title, auto) in enumerate(CLOSE_TASKS):
        cur.execute("""INSERT INTO close_tasks (entity_id, period_id, task_key, title, is_automatic, sort_order) VALUES (%s, %s, %s, %s, %s, %s)
                       ON CONFLICT (period_id, task_key) DO NOTHING""", (entity_id, period["id"], key, title, auto, i))
    checks = {
        "bank_feeds": "SELECT EXISTS (SELECT 1 FROM bank_transactions WHERE entity_id = %(e)s AND txn_date BETWEEN %(s)s AND %(t)s) AS ok",
        "sales_posted": """SELECT NOT EXISTS (SELECT 1 FROM sales_documents WHERE entity_id = %(e)s AND status = 'issued' AND journal_entry_id IS NULL
                                              AND doc_date BETWEEN %(s)s AND %(t)s) AS ok""",
        "bank_recon": """SELECT NOT EXISTS (SELECT 1 FROM bank_transactions WHERE entity_id = %(e)s AND txn_date BETWEEN %(s)s AND %(t)s
                                             AND match_status IN ('unmatched', 'suggested')) AS ok""",
    }
    for key, sql in checks.items():
        cur.execute(sql, {"e": entity_id, "s": period["period_start"], "t": period["period_end"]})
        ok = cur.fetchone()["ok"]
        cur.execute("""UPDATE close_tasks SET done_at = CASE WHEN %s THEN coalesce(done_at, now()) END
                        WHERE period_id = %s AND task_key = %s""", (ok, period["id"], key))


def _period(cur, entity_id, pid) -> dict:
    cur.execute("SELECT * FROM fiscal_periods WHERE id = %s AND entity_id = %s", (pid, entity_id))
    p = cur.fetchone()
    if not p:
        raise ApiError(404, "not_found", "Not found")
    return p


def period_out(cur, p: dict) -> dict:
    cur.execute("SELECT count(*) FILTER (WHERE done_at IS NOT NULL) AS done, count(*) AS total FROM close_tasks WHERE period_id = %s", (p["id"],))
    return js({"id": p["id"], "fy": p["fy"], "period_start": p["period_start"], "period_end": p["period_end"], "status": p["status"],
               "locked_at": p["locked_at"], "close_progress": cur.fetchone()})


@router.get(E + "/periods", tags=["Finance & books"])
def list_periods(entity_id: uuid.UUID, p: Principal = Auth, fy: str | None = Query(default=None, pattern=r"^[0-9]{4}-[0-9]{2}$")):
    with tx(p.user_id) as cur:
        require(cur, entity_id, "finance:read")
        cur.execute("SELECT app.fy_of(current_date) AS fy")
        fy = fy or cur.fetchone()["fy"]
        ensure_periods(cur, entity_id, fy)
        cur.execute("SELECT * FROM fiscal_periods WHERE entity_id = %s AND fy = %s ORDER BY period_start", (entity_id, fy))
        return [period_out(cur, r) for r in cur.fetchall()]


@router.get(E + "/periods/{period_id}/close-tasks", tags=["Finance & books"])
def list_close_tasks(entity_id: uuid.UUID, period_id: uuid.UUID, p: Principal = Auth):
    with tx(p.user_id) as cur:
        require(cur, entity_id, "finance:read")
        per = _period(cur, entity_id, period_id)
        ensure_close_tasks(cur, entity_id, per)
        cur.execute("""SELECT id, task_key, title, owner_user_id, is_automatic, done_at, done_by FROM close_tasks WHERE period_id = %s
                        ORDER BY sort_order""", (period_id,))
        return js(cur.fetchall())


class CloseTaskPatch(BaseModel):
    done: bool


@router.patch(E + "/periods/{period_id}/close-tasks/{task_id}", tags=["Finance & books"])
def update_close_task(entity_id: uuid.UUID, period_id: uuid.UUID, task_id: uuid.UUID, body: CloseTaskPatch, p: Principal = Auth):
    with tx(p.user_id) as cur:
        require(cur, entity_id, "finance:write")
        per = _period(cur, entity_id, period_id)
        if per["status"] == "locked":
            raise ApiError(409, "period_locked", "The period is locked")
        cur.execute("SELECT * FROM close_tasks WHERE id = %s AND period_id = %s FOR UPDATE", (task_id, period_id))
        t = cur.fetchone()
        if not t:
            raise ApiError(404, "not_found", "Not found")
        if t["is_automatic"]:
            raise ApiError(409, "conflict", "This task is computed from the books and cannot be ticked by hand")
        cur.execute("""UPDATE close_tasks SET done_at = CASE WHEN %s THEN now() END, done_by = CASE WHEN %s THEN app.current_user_id() END
                        WHERE id = %s RETURNING id, task_key, title, owner_user_id, is_automatic, done_at, done_by""", (body.done, body.done, task_id))
        out = cur.fetchone()
        audit(cur, entity_id, "close_task.updated", "close_tasks", task_id, after={"done": body.done, "task": t["task_key"]})
        return js(out)


@router.post(E + "/periods/{period_id}/lock", tags=["Finance & books"])
def lock_period(entity_id: uuid.UUID, period_id: uuid.UUID, p: Principal = Auth,
                idempotency_key: str | None = Header(default=None, alias="Idempotency-Key")):
    with tx(p.user_id) as cur:
        require(cur, entity_id, "finance:approve")
        replay = idempotency_begin(cur, idempotency_key, "POST", f"/entities/{entity_id}/periods/{period_id}/lock", {})
        if replay:
            return JSONResponse(replay["body"], status_code=replay["status"])
        per = _period(cur, entity_id, period_id)
        if per["status"] == "locked":
            raise ApiError(409, "conflict", "The period is already locked")
        ensure_close_tasks(cur, entity_id, per)
        cur.execute("SELECT title FROM close_tasks WHERE period_id = %s AND done_at IS NULL ORDER BY sort_order", (period_id,))
        open_ = [r["title"] for r in cur.fetchall()]
        cur.execute("SELECT count(*) AS n FROM journal_entries WHERE entity_id = %s AND status = 'draft' AND entry_date BETWEEN %s AND %s",
                    (entity_id, per["period_start"], per["period_end"]))
        drafts = cur.fetchone()["n"]
        if open_ or drafts:
            raise ApiError(409, "conflict", "The period cannot be locked yet",
                           f"{len(open_)} close task(s) open" + (f" ({', '.join(open_[:3])})" if open_ else "") + (f"; {drafts} draft journal(s)" if drafts else ""))
        cur.execute("UPDATE fiscal_periods SET status = 'locked', locked_at = now(), locked_by = app.current_user_id() WHERE id = %s", (period_id,))
        audit(cur, entity_id, "period.locked", "fiscal_periods", period_id, after={"period": str(per["period_start"])})
        out = period_out(cur, _period(cur, entity_id, period_id))
        idempotency_finish(cur, idempotency_key, 200, out)
    return out


# ------------------------------------------------------------------ banking
class BankAccountIn(BaseModel):
    ledger_account_id: uuid.UUID
    bank_name: str
    account_last4: str = Field(pattern=r"^[0-9]{4}$")
    ifsc: str | None = Field(default=None, pattern=r"^[A-Z]{4}0[A-Z0-9]{6}$")
    kind: Literal["current", "savings", "cash_credit", "overdraft", "fixed_deposit", "escrow"]
    sanctioned_limit: Decimal | None = None
    feed: Literal["account_aggregator", "bank_api", "statement_upload", "manual"] = "manual"


def bank_out(cur, bid) -> dict:
    cur.execute("""SELECT b.id, b.ledger_account_id, b.bank_name, b.account_last4, b.ifsc, b.kind, b.sanctioned_limit, b.feed,
                          coalesce((SELECT sum(amount) FROM bank_transactions t WHERE t.bank_account_id = b.id), 0) AS balance,
                          (SELECT max(imported_at) FROM bank_transactions t WHERE t.bank_account_id = b.id) AS balance_as_of
                     FROM bank_accounts b WHERE b.id = %s""", (bid,))
    r = cur.fetchone()
    if not r:
        raise ApiError(404, "not_found", "Not found")
    return js({**r, "balance": money(r["balance"]), "sanctioned_limit": None if r["sanctioned_limit"] is None else money(r["sanctioned_limit"])})


@router.get(E + "/bank-accounts", tags=["Finance & books"])
def list_bank_accounts(entity_id: uuid.UUID, p: Principal = Auth):
    with tx(p.user_id) as cur:
        require(cur, entity_id, "finance:read")
        cur.execute("SELECT id FROM bank_accounts WHERE entity_id = %s AND is_active ORDER BY bank_name, account_last4", (entity_id,))
        return {"data": [bank_out(cur, r["id"]) for r in cur.fetchall()], "next_cursor": None}


@router.post(E + "/bank-accounts", status_code=201, tags=["Finance & books"])
def create_bank_account(entity_id: uuid.UUID, body: BankAccountIn, p: Principal = Auth):
    with tx(p.user_id) as cur:
        require(cur, entity_id, "finance:write")
        cur.execute("SELECT 1 FROM ledger_accounts WHERE id = %s AND entity_id = %s", (body.ledger_account_id, entity_id))
        if not cur.fetchone():
            raise ApiError(400, "validation_failed", "ledger_account_id is not an account of this entity")
        cur.execute("""INSERT INTO bank_accounts (entity_id, ledger_account_id, bank_name, account_last4, ifsc, kind, sanctioned_limit, feed)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s) RETURNING id""",
                    (entity_id, body.ledger_account_id, body.bank_name, body.account_last4, body.ifsc, body.kind, body.sanctioned_limit, body.feed))
        bid = cur.fetchone()["id"]
        audit(cur, entity_id, "bank_account.created", "bank_accounts", bid)
        return bank_out(cur, bid)


@router.get(E + "/bank-accounts/{bank_account_id}", tags=["Finance & books"])
def get_bank_account(entity_id: uuid.UUID, bank_account_id: uuid.UUID, p: Principal = Auth):
    with tx(p.user_id) as cur:
        require(cur, entity_id, "finance:read")
        cur.execute("SELECT 1 FROM bank_accounts WHERE id = %s AND entity_id = %s", (bank_account_id, entity_id))
        if not cur.fetchone():
            raise ApiError(404, "not_found", "Not found")
        return bank_out(cur, bank_account_id)


class BankLineIn(BaseModel):
    external_id: str
    txn_date: dt.date
    amount: Decimal
    description: str
    utr: str | None = None
    counterparty_name: str | None = None
    counterparty_va: str | None = None
    balance_after: Decimal | None = None


class BankFeedIn(BaseModel):
    transactions: list[BankLineIn] = Field(min_length=1, max_length=1000)


def _method_of(text: str) -> str:
    t = text.upper()
    return next((m.lower() for m in ("RTGS", "IMPS", "UPI", "NEFT") if m in t), "neft")


def suggest_matches(cur, entity_id) -> int:
    """Propose receipts for unmatched credits. Rule 1: paid into an invoice's virtual account (confidence 1.0).
    Rule 2: the invoice number is in the narration and the amount fits its balance (0.95). A suggestion is a
    *pending* receipt; nothing is settled or posted until a person accepts it."""
    cur.execute("SELECT * FROM bank_transactions WHERE entity_id = %s AND match_status = 'unmatched' AND amount > 0 ORDER BY txn_date, id", (entity_id,))
    made = 0
    for t in cur.fetchall():
        hit = None
        if t["counterparty_va"]:
            cur.execute("""SELECT d.id, d.party_id, d.balance_due FROM virtual_accounts v JOIN sales_documents d ON d.id = v.sales_document_id
                            WHERE v.va_number = %s AND v.entity_id = %s AND v.status = 'active' AND d.status = 'issued' AND d.balance_due > 0""",
                        (t["counterparty_va"], entity_id))
            r = cur.fetchone()
            hit = (r, "virtual_account", Decimal("1.000")) if r else None
        if not hit:
            cur.execute("""SELECT id, party_id, balance_due, number FROM sales_documents WHERE entity_id = %s AND status = 'issued' AND balance_due > 0
                              AND doc_type = 'tax_invoice' AND number IS NOT NULL AND upper(%s) LIKE '%%' || upper(number) || '%%'
                            ORDER BY length(number) DESC LIMIT 1""", (entity_id, t["description"]))
            r = cur.fetchone()
            hit = (r, "invoice_number", Decimal("0.950")) if r else None
        if not hit:
            continue
        doc, rule, conf = hit
        cur.execute("""INSERT INTO receipts (entity_id, party_id, receipt_no, received_on, amount, method, utr, bank_account_id, bank_transaction_id, status)
                       VALUES (%s, %s, %s, %s, %s, %s::payment_method, %s, %s, %s, 'pending') RETURNING id""",
                    (entity_id, doc["party_id"], "RCPT-" + uuid.uuid4().hex[:8].upper(), t["txn_date"], t["amount"], _method_of(t["description"]),
                     t["utr"], t["bank_account_id"], t["id"]))
        rid = cur.fetchone()["id"]
        cur.execute("""INSERT INTO bank_matches (bank_transaction_id, target_kind, target_id, amount, confidence, rule)
                       VALUES (%s, 'receipt', %s, %s, %s, %s)""", (t["id"], rid, t["amount"], conf, f"{rule}:{doc['id']}"))
        cur.execute("UPDATE bank_transactions SET match_status = 'suggested' WHERE id = %s", (t["id"],))
        made += 1
    return made


def import_lines(cur, entity_id, bank_account_id, lines) -> tuple[int, int]:
    """Insert bank lines (duplicates by external_id are ignored) and propose receipts for the new credits. Returns (imported, suggested)."""
    imported = 0
    for t in lines:
        t = t if isinstance(t, dict) else t.model_dump()
        if Decimal(str(t["amount"])) == 0:
            raise ApiError(400, "validation_failed", "amount cannot be zero")
        cur.execute("""INSERT INTO bank_transactions (bank_account_id, entity_id, txn_date, amount, description, utr, counterparty_name,
                              counterparty_va, balance_after, external_id) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                       ON CONFLICT (bank_account_id, external_id) DO NOTHING""",
                    (bank_account_id, entity_id, t["txn_date"], t["amount"], t["description"], t.get("utr"), t.get("counterparty_name"), t.get("counterparty_va"),
                     t.get("balance_after"), t["external_id"]))
        imported += cur.rowcount
    suggested = suggest_matches(cur, entity_id)
    audit(cur, entity_id, "bank_feed.imported", "bank_accounts", bank_account_id, after={"imported": imported, "suggested": suggested})
    return imported, suggested


@router.post(E + "/bank-accounts/{bank_account_id}/transactions", status_code=201, tags=["Finance & books"])
def import_bank_transactions(entity_id: uuid.UUID, bank_account_id: uuid.UUID, body: BankFeedIn, p: Principal = Auth):
    with tx(p.user_id) as cur:
        require(cur, entity_id, "finance:write")
        cur.execute("SELECT 1 FROM bank_accounts WHERE id = %s AND entity_id = %s", (bank_account_id, entity_id))
        if not cur.fetchone():
            raise ApiError(404, "not_found", "Not found")
        imported, suggested = import_lines(cur, entity_id, bank_account_id, body.transactions)
        return {"imported": imported, "duplicates": len(body.transactions) - imported, "suggested": suggested}


def txn_out(cur, tid) -> dict:
    cur.execute("""SELECT id, bank_account_id, txn_date, amount, description, utr, counterparty_name, match_status FROM bank_transactions WHERE id = %s""", (tid,))
    t = cur.fetchone()
    cur.execute("""SELECT m.id, m.target_kind, m.target_id, m.amount, m.confidence, m.rule, m.status,
                          CASE WHEN m.target_kind = 'receipt' THEN 'Receipt ' || coalesce((SELECT receipt_no FROM receipts WHERE id = m.target_id), '')
                               ELSE m.target_kind END AS label
                     FROM bank_matches m WHERE m.bank_transaction_id = %s ORDER BY m.status = 'rejected', m.confidence DESC NULLS LAST""", (tid,))
    t["suggestions"] = [{**m, "rule": m["rule"].split(":")[0] if m["rule"] else None, "amount": money(m["amount"]), "confidence": float(m["confidence"] or 0)}
                        for m in cur.fetchall()]
    t["amount"] = money(t["amount"])
    return js(t)


@router.get(E + "/bank-transactions", tags=["Finance & books"])
def list_bank_transactions(entity_id: uuid.UUID, p: Principal = Auth, bank_account_id: uuid.UUID | None = None, match_status: str | None = None,
                           from_: dt.date | None = Query(default=None, alias="from"), to: dt.date | None = None,
                           limit: int = Query(default=50, ge=1, le=200), cursor: str | None = None):
    off = offset_of(cursor)
    with tx(p.user_id) as cur:
        require(cur, entity_id, "finance:read")
        cur.execute("""SELECT id FROM bank_transactions WHERE entity_id = %s AND (%s::uuid IS NULL OR bank_account_id = %s)
                          AND (%s::text IS NULL OR match_status = %s) AND (%s::date IS NULL OR txn_date >= %s) AND (%s::date IS NULL OR txn_date <= %s)
                        ORDER BY txn_date DESC, id LIMIT %s OFFSET %s""",
                    (entity_id, bank_account_id, bank_account_id, match_status, match_status, from_, from_, to, to, limit + 1, off))
        return page([txn_out(cur, r["id"]) for r in cur.fetchall()], limit, off)


def _match_for_update(cur, entity_id, match_id):
    cur.execute("""SELECT m.*, t.entity_id, t.bank_account_id, t.txn_date FROM bank_matches m JOIN bank_transactions t ON t.id = m.bank_transaction_id
                    WHERE m.id = %s AND t.entity_id = %s FOR UPDATE OF m""", (match_id, entity_id))
    m = cur.fetchone()
    if not m:
        raise ApiError(404, "not_found", "Not found")
    if m["status"] != "suggested":
        raise ApiError(409, "conflict", f"Match is already {m['status']}")
    return m


@router.post(E + "/bank-matches/{match_id}/accept", tags=["Finance & books"])
def accept_match(entity_id: uuid.UUID, match_id: uuid.UUID, p: Principal = Auth,
                 idempotency_key: str | None = Header(default=None, alias="Idempotency-Key")):
    with tx(p.user_id) as cur:
        require(cur, entity_id, "finance:write")
        replay = idempotency_begin(cur, idempotency_key, "POST", f"/entities/{entity_id}/bank-matches/{match_id}/accept", {})
        if replay:
            return JSONResponse(replay["body"], status_code=replay["status"])
        m = _match_for_update(cur, entity_id, match_id)
        if m["target_kind"] != "receipt":
            raise ApiError(409, "conflict", "Only receipt matches can be accepted here")
        doc_id = uuid.UUID(m["rule"].split(":", 1)[1])
        cur.execute("SELECT * FROM receipts WHERE id = %s FOR UPDATE", (m["target_id"],))
        rc = cur.fetchone()
        cur.execute("SELECT balance_due FROM sales_documents WHERE id = %s FOR UPDATE", (doc_id,))
        allocate = min(rc["amount"], cur.fetchone()["balance_due"])
        if allocate > 0:
            cur.execute("""INSERT INTO invoice_settlements (entity_id, invoice_id, source_kind, receipt_id, amount, settled_on)
                           VALUES (%s, %s, 'receipt', %s, %s, %s)""", (entity_id, doc_id, rc["id"], allocate, rc["received_on"]))
            cur.execute("SELECT payment_status FROM sales_documents WHERE id = %s", (doc_id,))
            if cur.fetchone()["payment_status"] == "paid":
                emit(cur, entity_id, "sales_document.paid", "sales_documents", doc_id)
        cur.execute("SELECT ledger_account_id FROM bank_accounts WHERE id = %s", (m["bank_account_id"],))
        jid = ledger.post_journal(cur, entity_id, rc["received_on"], "receipt", rc["id"], f"Receipt {rc['receipt_no']} (bank match)",
                                  ledger.receipt_lines(amount=rc["amount"], allocated=allocate, party_id=rc["party_id"], method=rc["method"],
                                                       bank_account_key="account:" + str(cur.fetchone()["ledger_account_id"])))
        cur.execute("UPDATE receipts SET status = 'confirmed', journal_entry_id = %s, is_advance = %s WHERE id = %s", (jid, allocate < rc["amount"], rc["id"]))
        cur.execute("UPDATE bank_matches SET status = 'accepted', decided_by = app.current_user_id(), decided_at = now() WHERE id = %s", (match_id,))
        cur.execute("UPDATE bank_transactions SET match_status = 'matched' WHERE id = %s", (m["bank_transaction_id"],))
        audit(cur, entity_id, "bank_match.accepted", "bank_matches", match_id, after={"receipt": rc["id"], "invoice": doc_id})
        emit(cur, entity_id, "receipt.created", "receipts", rc["id"], {"amount": money(rc["amount"])})
        out = txn_out(cur, m["bank_transaction_id"])
        idempotency_finish(cur, idempotency_key, 200, out)
    return out


@router.post(E + "/bank-matches/{match_id}/reject", tags=["Finance & books"])
def reject_match(entity_id: uuid.UUID, match_id: uuid.UUID, p: Principal = Auth):
    with tx(p.user_id) as cur:
        require(cur, entity_id, "finance:write")
        m = _match_for_update(cur, entity_id, match_id)
        cur.execute("UPDATE bank_matches SET status = 'rejected', decided_by = app.current_user_id(), decided_at = now() WHERE id = %s", (match_id,))
        if m["target_kind"] == "receipt":
            cur.execute("UPDATE receipts SET status = 'reversed' WHERE id = %s AND status = 'pending'", (m["target_id"],))
        cur.execute("""UPDATE bank_transactions SET match_status = CASE WHEN EXISTS (SELECT 1 FROM bank_matches WHERE bank_transaction_id = %s
                                 AND status = 'suggested') THEN 'suggested' ELSE 'unmatched' END WHERE id = %s""", (m["bank_transaction_id"], m["bank_transaction_id"]))
        audit(cur, entity_id, "bank_match.rejected", "bank_matches", match_id)
        return txn_out(cur, m["bank_transaction_id"])


class MatchTarget(BaseModel):
    target_kind: Literal["receipt", "journal_entry"]
    target_id: uuid.UUID
    amount: Decimal = Field(gt=0)


class ManualMatch(BaseModel):
    targets: list[MatchTarget] = Field(min_length=1)


@router.post(E + "/bank-transactions/{txn_id}/match", tags=["Finance & books"])
def match_manually(entity_id: uuid.UUID, txn_id: uuid.UUID, body: ManualMatch, p: Principal = Auth):
    with tx(p.user_id) as cur:
        require(cur, entity_id, "finance:write")
        cur.execute("SELECT * FROM bank_transactions WHERE id = %s AND entity_id = %s FOR UPDATE", (txn_id, entity_id))
        t = cur.fetchone()
        if not t:
            raise ApiError(404, "not_found", "Not found")
        if t["match_status"] == "matched":
            raise ApiError(409, "conflict", "Already matched")
        if sum((x.amount for x in body.targets), ZERO) != abs(t["amount"]):
            raise ApiError(400, "validation_failed", f"Targets must add up to the line amount {money(abs(t['amount']))}")
        for x in body.targets:
            table = "receipts" if x.target_kind == "receipt" else "journal_entries"
            cur.execute(f"SELECT 1 FROM {table} WHERE id = %s AND entity_id = %s", (x.target_id, entity_id))
            if not cur.fetchone():
                raise ApiError(400, "validation_failed", f"{x.target_kind} {x.target_id} not found")
            cur.execute("""INSERT INTO bank_matches (bank_transaction_id, target_kind, target_id, amount, confidence, rule, status, decided_by, decided_at)
                           VALUES (%s, %s, %s, %s, 1, 'manual', 'accepted', app.current_user_id(), now())""", (txn_id, x.target_kind, x.target_id, x.amount))
            if x.target_kind == "receipt":
                cur.execute("UPDATE receipts SET bank_transaction_id = coalesce(bank_transaction_id, %s) WHERE id = %s", (txn_id, x.target_id))
        cur.execute("UPDATE bank_matches SET status = 'rejected' WHERE bank_transaction_id = %s AND status = 'suggested'", (txn_id,))
        cur.execute("UPDATE receipts SET status = 'reversed' WHERE bank_transaction_id = %s AND status = 'pending'", (txn_id,))
        cur.execute("UPDATE bank_transactions SET match_status = 'matched' WHERE id = %s", (txn_id,))
        audit(cur, entity_id, "bank_transaction.matched", "bank_transactions", txn_id, after={"targets": len(body.targets)})
        return txn_out(cur, txn_id)


# ------------------------------------------------------------------ reports
_MONTHS = re.compile(r"^\d{4}-\d{2}$")


def _fy_bounds(cur, from_, to):
    cur.execute("SELECT app.fy_of(current_date) AS fy")
    y = int(cur.fetchone()["fy"][:4])
    return from_ or dt.date(y, 4, 1), to or dt.date.today()


def _balances(cur, entity_id, types, start, end, by_month=False):
    sql = """SELECT a.id, a.code, a.name, a.type::text, {m} coalesce(sum(l.debit), 0) AS dr, coalesce(sum(l.credit), 0) AS cr
               FROM ledger_accounts a LEFT JOIN journal_lines l ON l.account_id = a.id
               LEFT JOIN journal_entries e ON e.id = l.entry_id AND e.status IN ('posted', 'reversed') AND e.entry_date BETWEEN %s AND %s
              WHERE a.entity_id = %s AND a.type::text = ANY(%s) AND (l.id IS NULL OR e.id IS NOT NULL)
              GROUP BY a.id, a.code, a.name, a.type {g} ORDER BY a.code"""
    cur.execute(sql.format(m="to_char(e.entry_date, 'YYYY-MM') AS month," if by_month else "", g=", to_char(e.entry_date, 'YYYY-MM')" if by_month else ""),
                (start, end, entity_id, types))
    return cur.fetchall()


@router.get(E + "/reports/{report}", tags=["Finance & books"])
def get_report(entity_id: uuid.UUID, report: Literal["profit-and-loss", "balance-sheet", "cash-bridge", "trial-balance", "gst-liability"],
               p: Principal = Auth, from_: dt.date | None = Query(default=None, alias="from"), to: dt.date | None = None,
               compare: str = "none", format: str = "json"):
    if format != "json":
        raise ApiError(400, "validation_failed", "Only format=json is available")
    with tx(p.user_id) as cur:
        require(cur, entity_id, "finance:read")
        start, end = _fy_bounds(cur, from_, to)
        if report == "cash-bridge":
            raise ApiError(400, "validation_failed", "The cash bridge needs purchase bills and payroll posted to the ledger, which arrive with those modules")
        if report == "profit-and-loss":
            rows = _balances(cur, entity_id, ["income", "expense"], start, end, by_month=True)
            months = sorted({r["month"] for r in rows if r["month"]})
            by_acc: dict = {}
            for r in rows:
                if r["month"]:
                    by_acc.setdefault((r["type"], r["code"], r["name"]), {})[r["month"]] = (r["cr"] - r["dr"]) if r["type"] == "income" else (r["dr"] - r["cr"])
            out, tot = [], {"income": [ZERO] * len(months), "expense": [ZERO] * len(months)}
            for kind in ("income", "expense"):
                for (t, code, name), vals in sorted(by_acc.items()):
                    if t != kind:
                        continue
                    row = [(vals.get(m, ZERO)) for m in months]
                    tot[kind] = [a + b for a, b in zip(tot[kind], row)]
                    out.append({"line": f"{code} {name}", "values": [money(v) for v in row]})
                out.append({"line": "Total income" if kind == "income" else "Total expenses", "values": [money(v) for v in tot[kind]]})
            out.append({"line": "Net profit", "values": [money(a - b) for a, b in zip(tot["income"], tot["expense"])]})
            return {"columns": months, "rows": out, "provisional": True}
        if report == "trial-balance":
            rows = _balances(cur, entity_id, ["asset", "liability", "equity", "income", "expense"], dt.date(1900, 1, 1), end)
            out = []
            for r in rows:
                net = r["dr"] - r["cr"]
                if net:
                    out.append({"line": f"{r['code']} {r['name']}", "values": [money(max(net, ZERO)), money(max(-net, ZERO))]})
            out.append({"line": "Total", "values": [money(sum((Decimal(r["values"][0]) for r in out), ZERO)), money(sum((Decimal(r["values"][1]) for r in out), ZERO))]})
            return {"columns": ["Debit", "Credit"], "rows": out, "provisional": True}
        if report == "balance-sheet":
            rows = _balances(cur, entity_id, ["asset", "liability", "equity", "income", "expense"], dt.date(1900, 1, 1), end)
            out, profit, totals = [], ZERO, {"asset": ZERO, "liability": ZERO, "equity": ZERO}
            for r in rows:
                net = r["dr"] - r["cr"]
                if r["type"] in ("income", "expense"):
                    profit -= net
                elif net:
                    val = net if r["type"] == "asset" else -net
                    totals[r["type"]] += val
                    out.append({"line": f"{r['code']} {r['name']}", "values": [money(val)]})
            totals["equity"] += profit
            out += [{"line": "Profit for the period", "values": [money(profit)]}, {"line": "Total assets", "values": [money(totals["asset"])]},
                    {"line": "Total liabilities and equity", "values": [money(totals["liability"] + totals["equity"])]}]
            return {"columns": ["Balance"], "rows": out, "provisional": True}
        rows = _balances(cur, entity_id, ["liability"], start, end)           # gst-liability
        out = [{"line": f"{r['code']} {r['name']}", "values": [money(r["cr"] - r["dr"])]} for r in rows if r["code"].startswith("23")]
        return {"columns": ["Output tax liability"], "rows": out, "provisional": True}


# ------------------------------------------------------------------ headline numbers
@router.get(E + "/finance/summary", tags=["Finance & books"])
def finance_summary(entity_id: uuid.UUID, p: Principal = Auth, from_: dt.date | None = Query(default=None, alias="from"), to: dt.date | None = None):
    """Tiles for the finance screen, from posted journals. Period defaults to this financial year so far; runway uses the period's average monthly burn."""
    with tx(p.user_id) as cur:
        require(cur, entity_id, "finance:read")
        start, end = _fy_bounds(cur, from_, to)
        if end < start:
            raise ApiError(400, "validation_failed", "`to` must be on or after `from`")
        cur.execute("""SELECT coalesce(sum(l.credit - l.debit) FILTER (WHERE a.type = 'income' AND a.subtype IS DISTINCT FROM 'round_off'), 0) AS revenue,
                              coalesce(sum(l.debit - l.credit) FILTER (WHERE a.subtype = 'purchases'), 0) AS purchases,
                              coalesce(sum(l.debit - l.credit) FILTER (WHERE a.type = 'expense' AND a.subtype IS DISTINCT FROM 'interest_penalty'), 0) AS opex
                         FROM journal_lines l JOIN journal_entries e ON e.id = l.entry_id AND e.status IN ('posted', 'reversed')
                         JOIN ledger_accounts a ON a.id = l.account_id
                        WHERE a.entity_id = %s AND e.entry_date BETWEEN %s AND %s""", (entity_id, start, end))
        t = cur.fetchone()
        cur.execute("""SELECT coalesce(sum(l.debit - l.credit) FILTER (WHERE e.entry_date <= %s), 0) AS closing,
                              coalesce(sum(l.debit - l.credit) FILTER (WHERE e.entry_date < %s), 0) AS opening
                         FROM journal_lines l JOIN journal_entries e ON e.id = l.entry_id AND e.status IN ('posted', 'reversed')
                         JOIN ledger_accounts a ON a.id = l.account_id
                        WHERE a.entity_id = %s AND a.subtype IN ('bank', 'cash')""", (end, start, entity_id))
        c = cur.fetchone()
        cur.execute("""SELECT coalesce(sum(balance_due), 0) AS receivables, coalesce(sum(balance_due) FILTER (WHERE due_date < current_date), 0) AS overdue
                         FROM sales_documents WHERE entity_id = %s AND status = 'issued' AND balance_due > 0
                          AND doc_type IN ('tax_invoice', 'export_invoice', 'bill_of_supply', 'debit_note')""", (entity_id,))
        r = cur.fetchone()
    revenue, change = t["revenue"], c["closing"] - c["opening"]
    margin = ((revenue - t["purchases"]) / revenue * 100).quantize(Decimal("0.1")) if revenue > 0 else ZERO
    months = max(Decimal((end - start).days + 1) / Decimal("30.4"), Decimal(1))
    burn = (-change / months).quantize(Decimal("0.01")) if change < 0 else None
    runway = float((c["closing"] / burn).quantize(Decimal("0.1"))) if burn and c["closing"] > 0 else None
    return {"revenue": money(revenue), "gross_margin_pct": float(margin), "ebitda": money(revenue - t["opex"]), "cash_in_bank": money(c["closing"]),
            "cash_change": money(change), "receivables": money(r["receivables"]), "receivables_overdue": money(r["overdue"]),
            "runway_months": runway, "net_burn": money(burn) if burn is not None else None}
