"""Booking the bank statement: rules a company teaches, and classifying a line (which posts its journal)."""
from __future__ import annotations

import uuid
from typing import Literal

from fastapi import APIRouter, Header
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from . import bank_rules
from .bank_parse import words
from .core import ApiError, Auth, Principal, audit, idempotency_begin, idempotency_finish, require, tx
from .routes_finance import txn_out
from .routes_orders import js

router = APIRouter()
E = "/entities/{entity_id}"
MODES = ("upi", "neft", "rtgs", "imps", "auto_debit", "transfer", "cheque", "card", "cash", "charges", "interest", "other")
RULE_SQL = """SELECT r.id, r.direction, r.counterparty_key, r.keyword, r.mode, r.account_id, a.code AS account_code, a.name AS account_name, r.party_id, r.memo,
                     r.hits, r.last_hit_on, r.created_at
                FROM bank_rules r JOIN ledger_accounts a ON a.id = r.account_id"""


class RuleIn(BaseModel):
    direction: Literal["debit", "credit", "any"] = "any"
    counterparty_key: str | None = Field(default=None, max_length=120)
    keyword: str | None = Field(default=None, max_length=60)
    mode: Literal["upi", "neft", "rtgs", "imps", "auto_debit", "transfer", "cheque", "card", "cash", "charges", "interest", "other"] | None = None
    account_id: uuid.UUID
    party_id: uuid.UUID | None = None
    memo: str | None = Field(default=None, max_length=200)


def _norm_rule(body: RuleIn) -> dict:
    key = (body.counterparty_key or "").strip().lower() or None
    kw = " ".join(words(body.keyword)) or None
    if not (key or kw or body.mode):
        raise ApiError(400, "validation_failed", "A rule needs something to look for", errors=[{"field": "counterparty_key", "message": "give a counterparty, a keyword or a mode"}])
    return {"direction": body.direction, "counterparty_key": key, "keyword": kw, "mode": body.mode, "account_id": body.account_id, "party_id": body.party_id,
            "memo": (body.memo or "").strip() or None}


def _insert_rule(cur, entity_id, r: dict) -> dict:
    """One live rule per set of conditions: a second rule for the same conditions replaces the account of the first."""
    cur.execute("""SELECT id FROM bank_rules WHERE entity_id = %s AND is_active AND direction = %s AND coalesce(counterparty_key, '') = coalesce(%s, '')
                      AND coalesce(keyword, '') = coalesce(%s, '') AND coalesce(mode, '') = coalesce(%s, '')""",
                (entity_id, r["direction"], r["counterparty_key"], r["keyword"], r["mode"]))
    old = cur.fetchone()
    if old:
        cur.execute("UPDATE bank_rules SET account_id = %s, party_id = %s, memo = %s WHERE id = %s", (r["account_id"], r["party_id"], r["memo"], old["id"]))
        rid = old["id"]
    else:
        cur.execute("""INSERT INTO bank_rules (entity_id, direction, counterparty_key, keyword, mode, account_id, party_id, memo, created_by)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s, app.current_user_id()) RETURNING id""",
                    (entity_id, r["direction"], r["counterparty_key"], r["keyword"], r["mode"], r["account_id"], r["party_id"], r["memo"]))
        rid = cur.fetchone()["id"]
    audit(cur, entity_id, "bank_rule.saved", "bank_rules", rid, after={k: str(v) if v is not None else None for k, v in r.items()})
    return rid


@router.get(E + "/bank-rules", tags=["Finance & books"])
def list_rules(entity_id: uuid.UUID, p: Principal = Auth):
    with tx(p.user_id) as cur:
        require(cur, entity_id, "finance:read")
        cur.execute(RULE_SQL + " WHERE r.entity_id = %s AND r.is_active ORDER BY r.created_at DESC", (entity_id,))
        return js(cur.fetchall())


@router.post(E + "/bank-rules", status_code=201, tags=["Finance & books"])
def create_rule(entity_id: uuid.UUID, body: RuleIn, p: Principal = Auth):
    with tx(p.user_id) as cur:
        require(cur, entity_id, "finance:write")
        r = _norm_rule(body)
        cur.execute("SELECT 1 FROM ledger_accounts WHERE id = %s AND entity_id = %s AND is_active", (r["account_id"], entity_id))
        if not cur.fetchone():
            raise ApiError(400, "validation_failed", "Choose an account of this company", errors=[{"field": "account_id", "message": "unknown or inactive account"}])
        rid = _insert_rule(cur, entity_id, r)
        cur.execute(RULE_SQL + " WHERE r.id = %s", (rid,))
        return js(cur.fetchone())


@router.delete(E + "/bank-rules/{rule_id}", status_code=204, tags=["Finance & books"])
def delete_rule(entity_id: uuid.UUID, rule_id: uuid.UUID, p: Principal = Auth):
    with tx(p.user_id) as cur:
        require(cur, entity_id, "finance:write")
        cur.execute("UPDATE bank_rules SET is_active = false WHERE id = %s AND entity_id = %s AND is_active RETURNING id", (rule_id, entity_id))
        if not cur.fetchone():
            raise ApiError(404, "not_found", "Not found")
        audit(cur, entity_id, "bank_rule.removed", "bank_rules", rule_id)


class ClassifyIn(BaseModel):
    account_id: uuid.UUID
    party_id: uuid.UUID | None = None
    memo: str | None = Field(default=None, max_length=200)
    remember: Literal["counterparty", "keyword"] | None = None
    keyword: str | None = Field(default=None, max_length=60)
    apply_to_similar: bool = False


@router.post(E + "/bank-transactions/{txn_id}/classify", tags=["Finance & books"])
def classify(entity_id: uuid.UUID, txn_id: uuid.UUID, body: ClassifyIn, p: Principal = Auth,
             idempotency_key: str | None = Header(default=None, alias="Idempotency-Key")):
    """Book a line to an account: posts the journal (the account and the bank). With `remember` it also saves a rule for lines like this one, and with
    `apply_to_similar` books every other open line that rule matches, in the same transaction."""
    with tx(p.user_id) as cur:
        require(cur, entity_id, "finance:write")
        replay = idempotency_begin(cur, idempotency_key, "POST", f"/entities/{entity_id}/bank-transactions/{txn_id}/classify", body.model_dump(mode="json"))
        if replay:
            return JSONResponse(replay["body"], status_code=replay["status"])
        if body.apply_to_similar and not body.remember:
            raise ApiError(400, "validation_failed", "Choose what to remember to apply it to similar lines", errors=[{"field": "remember", "message": "counterparty or keyword"}])
        bank_rules.parse_unparsed(cur, entity_id)
        cur.execute("SELECT * FROM bank_transactions WHERE id = %s AND entity_id = %s FOR UPDATE", (txn_id, entity_id))
        t = cur.fetchone()
        if not t:
            raise ApiError(404, "not_found", "Not found")
        if t["match_status"] == "matched":
            raise ApiError(409, "conflict", "This line is already booked or matched")
        rule = None
        if body.remember:
            if body.remember == "counterparty" and not t["counterparty_key"]:
                raise ApiError(400, "validation_failed", "This line has no counterparty to remember", "Remember a keyword from the narration instead.")
            if body.remember == "keyword" and not words(body.keyword):
                raise ApiError(400, "validation_failed", "Give the keyword to remember", errors=[{"field": "keyword", "message": "a word from the narration"}])
            rule = {"direction": bank_rules.direction_of(t["amount"]), "counterparty_key": t["counterparty_key"] if body.remember == "counterparty" else None,
                    "keyword": " ".join(words(body.keyword)) if body.remember == "keyword" else None, "mode": None,
                    "account_id": body.account_id, "party_id": body.party_id, "memo": (body.memo or "").strip() or None}
            if body.remember == "keyword" and not bank_rules.has_phrase(words(t["description"]), words(body.keyword)):
                raise ApiError(400, "validation_failed", "That keyword is not in this line's narration", errors=[{"field": "keyword", "message": "use a word from the narration"}])
        bank_rules.post_line(cur, entity_id, t, body.account_id, body.party_id, (body.memo or "").strip() or None)
        also, rule_id = 0, None
        if rule:
            rule_id = _insert_rule(cur, entity_id, rule)
            cur.execute("UPDATE bank_rules SET hits = hits + 1, last_hit_on = %s WHERE id = %s", (t["txn_date"], rule_id))
            if body.apply_to_similar:
                cur.execute("SELECT * FROM bank_rules WHERE id = %s", (rule_id,))
                also = bank_rules.apply_rule(cur, entity_id, cur.fetchone())
        out = {**txn_out(cur, txn_id), "rule_id": str(rule_id) if rule_id else None, "also_booked": also}
        idempotency_finish(cur, idempotency_key, 200, out)
    return out


@router.post(E + "/bank-transactions/suggest", tags=["Finance & books"])
def suggest(entity_id: uuid.UUID, p: Principal = Auth):
    """Run the suggestions again over the open lines (after adding a rule or an account, or when lines were imported before this existed)."""
    from .routes_finance import suggest_matches
    with tx(p.user_id) as cur:
        require(cur, entity_id, "finance:write")
        return {"suggested": suggest_matches(cur, entity_id)}
