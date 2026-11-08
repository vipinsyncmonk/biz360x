"""Startup track: cap table, share classes and holders, funding rounds, allotments, ESOP pools and grants, and scheme benefits (Startup India).

Money is Decimal and share counts are whole numbers; the maths (post-round table, vesting, round steps) lives in `captable.py`.
Statutory timelines in the round steps and the 15 and 30 day return tasks are a starter library and need review by a CA or CS.
"""
from __future__ import annotations

import datetime as dt
import json
import uuid
from decimal import Decimal
from typing import Literal

from fastapi import APIRouter, Header, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from . import captable, engine
from .core import ApiError, Auth, Principal, audit, emit, idempotency_begin, idempotency_finish, require, tx
from .routes_orders import js, offset_of, page

router = APIRouter()
E = "/entities/{entity_id}"


def _num(v):
    d = Decimal(v)
    return int(d) if d == d.to_integral_value() else float(d)


# ================================================================== share classes and holders
class ShareClassIn(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    instrument: Literal["equity", "ccps", "ocps", "ccd", "safe", "warrant"] = "equity"
    face_value: Decimal = Field(gt=0, max_digits=12, decimal_places=4)
    conversion_ratio: Decimal = Field(default=Decimal(1), gt=0, max_digits=12, decimal_places=6)
    liquidation_pref: Decimal | None = Field(default=None, ge=0, le=100)


SC_COLS = "id, name, instrument, face_value::text AS face_value, conversion_ratio::text AS conversion_ratio, liquidation_pref::float8 AS liquidation_pref"


@router.post(E + "/share-classes", status_code=201, tags=["Capital"])
def create_share_class(entity_id: uuid.UUID, body: ShareClassIn, p: Principal = Auth):
    with tx(p.user_id) as cur:
        require(cur, entity_id, "capital:write")
        cur.execute(f"INSERT INTO share_classes (entity_id, name, instrument, face_value, conversion_ratio, liquidation_pref) VALUES (%s, %s, %s, %s, %s, %s) RETURNING {SC_COLS}",
                    (entity_id, body.name.strip(), body.instrument, body.face_value, body.conversion_ratio, body.liquidation_pref))
        out = js(cur.fetchone())
        audit(cur, entity_id, "share_class.created", "share_classes", out["id"], after={"name": body.name, "instrument": body.instrument})
        return out


@router.get(E + "/share-classes", tags=["Capital"])
def list_share_classes(entity_id: uuid.UUID, p: Principal = Auth):
    with tx(p.user_id) as cur:
        require(cur, entity_id, "capital:read")
        cur.execute(f"SELECT {SC_COLS} FROM share_classes WHERE entity_id = %s ORDER BY name", (entity_id,))
        return page(js(cur.fetchall()), 200, 0)


class ShareholderIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    kind: Literal["founder", "investor", "angel", "employee_trust", "company", "other"] = "other"
    pan: str | None = Field(default=None, pattern=r"^[A-Z]{5}[0-9]{4}[A-Z]$")
    is_non_resident: bool = False
    country: str = Field(default="IN", pattern=r"^[A-Z]{2}$")


SH_COLS = "id, name, kind, pan, is_non_resident, country"


@router.post(E + "/shareholders", status_code=201, tags=["Capital"])
def create_shareholder(entity_id: uuid.UUID, body: ShareholderIn, p: Principal = Auth):
    with tx(p.user_id) as cur:
        require(cur, entity_id, "capital:write")
        cur.execute(f"INSERT INTO shareholders (entity_id, name, kind, pan, is_non_resident, country) VALUES (%s, %s, %s, %s, %s, %s) RETURNING {SH_COLS}",
                    (entity_id, body.name.strip(), body.kind, body.pan, body.is_non_resident or body.country != "IN", body.country))
        out = js(cur.fetchone())
        audit(cur, entity_id, "shareholder.created", "shareholders", out["id"], after={"name": body.name, "kind": body.kind})
        return out


@router.get(E + "/shareholders", tags=["Capital"])
def list_shareholders(entity_id: uuid.UUID, p: Principal = Auth):
    with tx(p.user_id) as cur:
        require(cur, entity_id, "capital:read")
        cur.execute(f"SELECT {SH_COLS} FROM shareholders WHERE entity_id = %s ORDER BY name", (entity_id,))
        return page(js(cur.fetchall()), 200, 0)


# ================================================================== cap table
@router.get(E + "/cap-table", tags=["Capital"])
def cap_table(entity_id: uuid.UUID, p: Principal = Auth, scenario: Literal["current", "post_round"] = "current", round_id: uuid.UUID | None = None):
    with tx(p.user_id) as cur:
        require(cur, entity_id, "capital:read")
        cur.execute("SELECT holder, class_name, fd_shares FROM v_cap_table WHERE entity_id = %s ORDER BY fd_shares DESC, holder", (entity_id,))
        rows = cur.fetchall()
        total = sum((Decimal(r["fd_shares"]) for r in rows), Decimal(0))
        base = {"as_of": dt.date.today().isoformat()}
        if scenario == "current":
            return {**base, "scenario": "current", "total_fd_shares": _num(total),
                    "rows": [{"holder": r["holder"], "class_name": r["class_name"], "fd_shares": _num(r["fd_shares"]), "fd_pct": float(captable.percent(Decimal(r["fd_shares"]), total))} for r in rows]}
        if not round_id:
            raise ApiError(400, "validation_failed", "Choose the round to model", errors=[{"field": "round_id", "message": "required for post_round"}])
        cur.execute("""SELECT r.name, r.target_amount, r.pre_money_valuation, c.name AS class_name, c.conversion_ratio
                         FROM funding_rounds r LEFT JOIN share_classes c ON c.id = r.share_class_id WHERE r.id = %s AND r.entity_id = %s""", (round_id, entity_id))
        r = cur.fetchone()
        if not r:
            raise ApiError(404, "not_found", "Not found")
        if r["target_amount"] is None or r["pre_money_valuation"] is None or r["class_name"] is None:
            raise ApiError(400, "validation_failed", "The round needs a share class, a target amount and a pre-money valuation to be modelled")
        try:
            res = captable.post_round(rows, r["target_amount"], r["pre_money_valuation"], r["conversion_ratio"], r["name"], r["class_name"])
        except captable.CapTableError as e:
            raise ApiError(409, "conflict", str(e)) from e
    return {**base, "scenario": "post_round", "total_fd_shares": _num(res["total_fd_shares"]),
            "round": {"name": r["name"], "new_shares": res["new_shares"], "price_per_share": str(res["price_per_share"]), "post_money": str(res["post_money"].quantize(Decimal("0.01")))},
            "rows": [{"holder": x["holder"], "class_name": x["class_name"], "fd_shares": _num(x["fd_shares"]), "fd_pct": float(x["fd_pct"])} for x in res["rows"]]}


# ================================================================== funding rounds
ROUTES = ("private_placement", "rights", "preferential", "bonus")
STATUSES = ("planning", "term_sheet", "diligence", "documentation", "allotment", "closed", "abandoned")
ROUND_COLS = "id, name, share_class_id, route, target_amount, pre_money_valuation, term_sheet_signed_on, shareholder_approval_on, closed_on, status"


class RoundIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    share_class_id: uuid.UUID | None = None
    route: Literal["private_placement", "rights", "preferential", "bonus"] = "private_placement"
    target_amount: Decimal | None = Field(default=None, gt=0, max_digits=15, decimal_places=2)
    pre_money_valuation: Decimal | None = Field(default=None, gt=0, max_digits=15, decimal_places=2)
    term_sheet_signed_on: dt.date | None = None
    shareholder_approval_on: dt.date | None = None
    status: Literal["planning", "term_sheet", "diligence", "documentation", "allotment", "closed", "abandoned"] | None = None


class RoundPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    share_class_id: uuid.UUID | None = None
    route: Literal["private_placement", "rights", "preferential", "bonus"] | None = None
    target_amount: Decimal | None = Field(default=None, gt=0, max_digits=15, decimal_places=2)
    pre_money_valuation: Decimal | None = Field(default=None, gt=0, max_digits=15, decimal_places=2)
    term_sheet_signed_on: dt.date | None = None
    shareholder_approval_on: dt.date | None = None
    closed_on: dt.date | None = None
    status: Literal["planning", "term_sheet", "diligence", "documentation", "allotment", "closed", "abandoned"] | None = None


def _check_class(cur, entity_id, class_id) -> None:
    if class_id:
        cur.execute("SELECT 1 FROM share_classes WHERE id = %s AND entity_id = %s", (class_id, entity_id))
        if not cur.fetchone():
            raise ApiError(400, "validation_failed", "Unknown share class", errors=[{"field": "share_class_id", "message": "not one of this company's share classes"}])


def round_out(cur, entity_id, row: dict) -> dict:
    cur.execute("SELECT EXISTS (SELECT 1 FROM shareholders WHERE entity_id = %s AND is_non_resident) AS foreign_holder, "
                "EXISTS (SELECT 1 FROM share_allotments WHERE round_id = %s AND allotted_on IS NOT NULL) AS allotted", (entity_id, row["id"]))
    f = cur.fetchone()
    out = dict(row)
    out["steps"] = captable.round_steps(row, f["foreign_holder"], f["allotted"])
    return js(out)


@router.post(E + "/funding-rounds", status_code=201, tags=["Capital"])
def create_round(entity_id: uuid.UUID, body: RoundIn, p: Principal = Auth):
    with tx(p.user_id) as cur:
        require(cur, entity_id, "capital:write")
        _check_class(cur, entity_id, body.share_class_id)
        status = body.status or ("term_sheet" if body.term_sheet_signed_on else "planning")
        cur.execute(f"""INSERT INTO funding_rounds (entity_id, name, share_class_id, route, target_amount, pre_money_valuation, term_sheet_signed_on, shareholder_approval_on, status)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING {ROUND_COLS}""",
                    (entity_id, body.name.strip(), body.share_class_id, body.route, body.target_amount, body.pre_money_valuation, body.term_sheet_signed_on, body.shareholder_approval_on, status))
        row = cur.fetchone()
        audit(cur, entity_id, "funding_round.created", "funding_rounds", row["id"], after={"name": body.name, "status": status})
        return round_out(cur, entity_id, row)


@router.get(E + "/funding-rounds", tags=["Capital"])
def list_rounds(entity_id: uuid.UUID, p: Principal = Auth, limit: int = Query(default=50, ge=1, le=200), cursor: str | None = None):
    off = offset_of(cursor)
    with tx(p.user_id) as cur:
        require(cur, entity_id, "capital:read")
        cur.execute(f"SELECT {ROUND_COLS} FROM funding_rounds WHERE entity_id = %s ORDER BY (status IN ('closed', 'abandoned')), term_sheet_signed_on DESC NULLS LAST, name LIMIT %s OFFSET %s",
                    (entity_id, limit + 1, off))
        return page([round_out(cur, entity_id, r) for r in cur.fetchall()], limit, off)


@router.get(E + "/funding-rounds/{round_id}", tags=["Capital"])
def get_round(entity_id: uuid.UUID, round_id: uuid.UUID, p: Principal = Auth):
    with tx(p.user_id) as cur:
        require(cur, entity_id, "capital:read")
        cur.execute(f"SELECT {ROUND_COLS} FROM funding_rounds WHERE id = %s AND entity_id = %s", (round_id, entity_id))
        r = cur.fetchone()
        if not r:
            raise ApiError(404, "not_found", "Not found")
        return round_out(cur, entity_id, r)


@router.patch(E + "/funding-rounds/{round_id}", tags=["Capital"])
def update_round(entity_id: uuid.UUID, round_id: uuid.UUID, body: RoundPatch, p: Principal = Auth):
    with tx(p.user_id) as cur:
        require(cur, entity_id, "capital:write")
        cur.execute(f"SELECT {ROUND_COLS} FROM funding_rounds WHERE id = %s AND entity_id = %s FOR UPDATE", (round_id, entity_id))
        r = cur.fetchone()
        if not r:
            raise ApiError(404, "not_found", "Not found")
        changes = body.model_dump(exclude_unset=True)
        if "share_class_id" in changes:
            _check_class(cur, entity_id, changes["share_class_id"])
        if changes.get("status") == "closed" and not (changes.get("closed_on") or r["closed_on"]):
            changes["closed_on"] = dt.date.today()
        if not changes:
            return round_out(cur, entity_id, r)
        sets = ", ".join(f"{k} = %s" for k in changes)
        cur.execute(f"UPDATE funding_rounds SET {sets} WHERE id = %s RETURNING {ROUND_COLS}", (*changes.values(), round_id))
        row = cur.fetchone()
        audit(cur, entity_id, "funding_round.updated", "funding_rounds", round_id, before={"status": r["status"]}, after=js({k: v for k, v in changes.items()}))
        return round_out(cur, entity_id, row)


# ================================================================== allotments
class AllotmentIn(BaseModel):
    round_id: uuid.UUID | None = None
    share_class_id: uuid.UUID
    shareholder_id: uuid.UUID
    quantity: int = Field(gt=0)
    issue_price: Decimal = Field(gt=0, max_digits=14, decimal_places=4)
    money_received_on: dt.date | None = None
    allotted_on: dt.date | None = None


AL_COLS = "id, round_id, share_class_id, shareholder_id, quantity, issue_price::text AS issue_price, money_received_on, allotted_on, pas3_obligation_id, fcgpr_obligation_id"


@router.post(E + "/allotments", status_code=201, tags=["Capital"])
def create_allotment(entity_id: uuid.UUID, body: AllotmentIn, p: Principal = Auth, idempotency_key: str | None = Header(default=None, alias="Idempotency-Key")):
    with tx(p.user_id) as cur:
        require(cur, entity_id, "capital:write")
        replay = idempotency_begin(cur, idempotency_key, "POST", f"/entities/{entity_id}/allotments", body.model_dump(mode="json"))
        if replay:
            return JSONResponse(replay["body"], status_code=replay["status"] or 201)
        today = dt.date.today()
        for d, f in ((body.allotted_on, "allotted_on"), (body.money_received_on, "money_received_on")):
            if d and d > today:
                raise ApiError(400, "validation_failed", "A date cannot be in the future", errors=[{"field": f, "message": "record it once it has happened"}])
        _check_class(cur, entity_id, body.share_class_id)
        cur.execute("SELECT name, is_non_resident FROM shareholders WHERE id = %s AND entity_id = %s", (body.shareholder_id, entity_id))
        holder = cur.fetchone()
        if not holder:
            raise ApiError(400, "validation_failed", "Unknown shareholder", errors=[{"field": "shareholder_id", "message": "not one of this company's shareholders"}])
        if body.round_id:
            cur.execute("SELECT 1 FROM funding_rounds WHERE id = %s AND entity_id = %s", (body.round_id, entity_id))
            if not cur.fetchone():
                raise ApiError(400, "validation_failed", "Unknown round", errors=[{"field": "round_id", "message": "not one of this company's rounds"}])
        cur.execute(f"""INSERT INTO share_allotments (entity_id, round_id, share_class_id, shareholder_id, quantity, issue_price, money_received_on, allotted_on)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s) RETURNING {AL_COLS}""",
                    (entity_id, body.round_id, body.share_class_id, body.shareholder_id, body.quantity, body.issue_price, body.money_received_on, body.allotted_on))
        out = js(cur.fetchone())
        if body.allotted_on:                                    # the returns that follow an allotment become tasks with their dates
            cur.execute("INSERT INTO tasks (entity_id, title, due_on, source) VALUES (%s, %s, %s, 'engine')",
                        (entity_id, f"File PAS-3 (return of allotment) for the shares allotted to {holder['name']}", body.allotted_on + dt.timedelta(days=15)))
            if holder["is_non_resident"]:
                cur.execute("INSERT INTO tasks (entity_id, title, due_on, source) VALUES (%s, %s, %s, 'engine')",
                            (entity_id, f"Report the allotment to {holder['name']} on FIRMS (FC-GPR)", body.allotted_on + dt.timedelta(days=30)))
        audit(cur, entity_id, "allotment.recorded", "share_allotments", out["id"], after={"holder": holder["name"], "quantity": body.quantity, "issue_price": body.issue_price})
        emit(cur, entity_id, "allotment.recorded", "share_allotments", out["id"], {"quantity": body.quantity})
        idempotency_finish(cur, idempotency_key, 201, out)
    return JSONResponse(out, status_code=201)


@router.get(E + "/allotments", tags=["Capital"])
def list_allotments(entity_id: uuid.UUID, p: Principal = Auth, round_id: uuid.UUID | None = None, limit: int = Query(default=100, ge=1, le=200), cursor: str | None = None):
    off = offset_of(cursor)
    with tx(p.user_id) as cur:
        require(cur, entity_id, "capital:read")
        cur.execute(f"SELECT {AL_COLS} FROM share_allotments WHERE entity_id = %s AND (%s::uuid IS NULL OR round_id = %s) ORDER BY coalesce(allotted_on, money_received_on) DESC NULLS LAST, id LIMIT %s OFFSET %s",
                    (entity_id, round_id, round_id, limit + 1, off))
        return page(js(cur.fetchall()), limit, off)


# ================================================================== ESOP
class PoolIn(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    approved_on: dt.date
    size_options: int = Field(gt=0)


@router.post(E + "/esop-pools", status_code=201, tags=["Capital"])
def create_pool(entity_id: uuid.UUID, body: PoolIn, p: Principal = Auth):
    with tx(p.user_id) as cur:
        require(cur, entity_id, "capital:write")
        cur.execute("INSERT INTO esop_pools (entity_id, name, approved_on, size_options) VALUES (%s, %s, %s, %s) RETURNING id", (entity_id, body.name.strip(), body.approved_on, body.size_options))
        pid = cur.fetchone()["id"]
        audit(cur, entity_id, "esop_pool.created", "esop_pools", pid, after={"name": body.name, "size_options": body.size_options})
        return _pool(cur, entity_id, pid)


def _pool(cur, entity_id, pool_id) -> dict:
    cur.execute("""SELECT p.id, p.name, p.approved_on, p.size_options,
                          coalesce((SELECT sum(g.options) FROM esop_grants g WHERE g.pool_id = p.id AND g.status IN ('active', 'fully_vested')), 0)::bigint AS granted
                     FROM esop_pools p WHERE p.id = %s AND p.entity_id = %s""", (pool_id, entity_id))
    r = cur.fetchone()
    if not r:
        raise ApiError(404, "not_found", "Not found")
    r["free"] = r["size_options"] - r["granted"]
    return js(r)


@router.get(E + "/esop-pools", tags=["Capital"])
def list_pools(entity_id: uuid.UUID, p: Principal = Auth):
    with tx(p.user_id) as cur:
        require(cur, entity_id, "capital:read")
        cur.execute("SELECT id FROM esop_pools WHERE entity_id = %s ORDER BY approved_on, name", (entity_id,))
        return page([_pool(cur, entity_id, r["id"]) for r in cur.fetchall()], 200, 0)


class GrantIn(BaseModel):
    pool_id: uuid.UUID
    employee_id: uuid.UUID
    granted_on: dt.date
    options: int = Field(gt=0)
    strike_price: Decimal = Field(ge=0, max_digits=14, decimal_places=4)
    cliff_months: int = Field(default=12, ge=0, le=60)
    vesting_months: int = Field(default=48, gt=0, le=120)
    vesting_frequency: Literal["monthly", "quarterly", "annual"] = "monthly"


GR_SQL = """SELECT g.id, g.pool_id, g.employee_id, e.full_name AS employee_name, g.granted_on, g.options, g.strike_price::text AS strike_price, g.cliff_months, g.vesting_months, g.vesting_frequency, g.status
              FROM esop_grants g JOIN employees e ON e.id = g.employee_id JOIN esop_pools p ON p.id = g.pool_id"""


def _grant_out(g: dict, today: dt.date) -> dict:
    vested, nxt = captable.vesting(g["options"], g["granted_on"], g["cliff_months"], g["vesting_months"], g["vesting_frequency"], today)
    return js({**g, "vested_to_date": vested, "next_vest": nxt})


@router.post(E + "/esop-grants", status_code=201, tags=["Capital"])
def create_grant(entity_id: uuid.UUID, body: GrantIn, p: Principal = Auth):
    with tx(p.user_id) as cur:
        require(cur, entity_id, "capital:write")
        if body.cliff_months > body.vesting_months:
            raise ApiError(400, "validation_failed", "The cliff cannot be longer than the vesting period", errors=[{"field": "cliff_months", "message": "must not exceed vesting_months"}])
        pool = _pool(cur, entity_id, body.pool_id)
        cur.execute("SELECT 1 FROM employees WHERE id = %s AND entity_id = %s", (body.employee_id, entity_id))
        if not cur.fetchone():
            raise ApiError(400, "validation_failed", "Unknown employee", errors=[{"field": "employee_id", "message": "not an employee of this company"}])
        if body.options > pool["free"]:
            raise ApiError(409, "conflict", "The pool does not have that many options left", f"{pool['free']} of {pool['size_options']} options are free in {pool['name']}.")
        cur.execute("""INSERT INTO esop_grants (pool_id, employee_id, granted_on, options, strike_price, cliff_months, vesting_months, vesting_frequency)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s) RETURNING id""",
                    (body.pool_id, body.employee_id, body.granted_on, body.options, body.strike_price, body.cliff_months, body.vesting_months, body.vesting_frequency))
        gid = cur.fetchone()["id"]
        audit(cur, entity_id, "esop_grant.created", "esop_grants", gid, after={"options": body.options, "employee_id": body.employee_id})
        cur.execute(GR_SQL + " WHERE g.id = %s", (gid,))
        return _grant_out(cur.fetchone(), dt.date.today())


@router.get(E + "/esop-grants", tags=["Capital"])
def list_grants(entity_id: uuid.UUID, p: Principal = Auth, limit: int = Query(default=100, ge=1, le=200), cursor: str | None = None):
    off = offset_of(cursor)
    with tx(p.user_id) as cur:
        require(cur, entity_id, "capital:read")
        cur.execute(GR_SQL + " WHERE p.entity_id = %s ORDER BY g.granted_on DESC, g.id LIMIT %s OFFSET %s", (entity_id, limit + 1, off))
        today = dt.date.today()
        return page([_grant_out(g, today) for g in cur.fetchall()], limit, off)


# ================================================================== schemes and benefits
SCHEME_SQL = """SELECT s.code AS scheme_code, s.name, s.kind, s.info_url, b.status, b.reason, b.reference_no, b.valid_until, b.applied_on, b.granted_on
                  FROM ref_schemes s JOIN entities en ON en.id = %s AND en.track = ANY(s.for_tracks)
                  LEFT JOIN entity_benefits b ON b.scheme_code = s.code AND b.entity_id = en.id
                 ORDER BY s.code"""
KEEP = ("applied", "in_progress", "granted", "rejected", "expired")


def _benefit_out(r: dict) -> dict:
    status = r["status"] or "not_checked"
    nxt = {"eligible": "Apply for this scheme", "applied": "Waiting for the authority", "in_progress": "Waiting for the authority"}.get(status)
    return js({"scheme_code": r["scheme_code"], "name": r["name"], "kind": r["kind"], "status": status, "reason": r["reason"] or "Not checked yet. Check eligibility to see where you stand.",
               "reference_no": r["reference_no"], "valid_until": r["valid_until"], "next_action": nxt, "info_url": r["info_url"]})


@router.get(E + "/benefits", tags=["Schemes & credit"])
def list_benefits(entity_id: uuid.UUID, p: Principal = Auth):
    with tx(p.user_id) as cur:
        require(cur, entity_id, "schemes:read")
        cur.execute(SCHEME_SQL, (entity_id,))
        return js([_benefit_out(r) for r in cur.fetchall()])


@router.post(E + "/benefits/check", tags=["Schemes & credit"])
def check_benefits(entity_id: uuid.UUID, p: Principal = Auth):
    with tx(p.user_id) as cur:
        require(cur, entity_id, "schemes:read")
        ctx = engine.load_context(cur, entity_id)
        facts = engine.derived_facts(ctx.facts, ctx.registrations)
        today = dt.date.today()
        if ctx.incorporated_on:
            years = today.year - ctx.incorporated_on.year - ((today.month, today.day) < (ctx.incorporated_on.month, ctx.incorporated_on.day))
            facts.setdefault("age_years", years)
        if "aato" in facts:                                    # onboarding records annual turnover; the DPIIT test reads the highest year
            facts.setdefault("turnover_max_any_year", facts["aato"])
        cur.execute("SELECT status, valid_until FROM entity_benefits WHERE entity_id = %s AND scheme_code = 'DPIIT'", (entity_id,))
        d = cur.fetchone()
        facts["dpiit_recognised"] = bool(d and d["status"] == "granted" and (d["valid_until"] is None or d["valid_until"] >= today))
        cur.execute("SELECT s.code, s.eligibility FROM ref_schemes s JOIN entities en ON en.id = %s AND en.track = ANY(s.for_tracks)", (entity_id,))
        for s in cur.fetchall():
            ok, clauses = engine.evaluate_applicability(s["eligibility"], facts)
            reason = captable.benefit_reason(clauses, ok)
            cur.execute("""INSERT INTO entity_benefits (entity_id, scheme_code, status, reason) VALUES (%s, %s, %s, %s)
                           ON CONFLICT (entity_id, scheme_code) DO UPDATE SET reason = EXCLUDED.reason, checked_at = now(),
                               status = CASE WHEN entity_benefits.status = ANY(%s) THEN entity_benefits.status ELSE EXCLUDED.status END""",
                        (entity_id, s["code"], "eligible" if ok else "not_eligible", reason, list(KEEP)))
        audit(cur, entity_id, "benefits.checked", "entity_benefits", None)
        cur.execute(SCHEME_SQL, (entity_id,))
        return js([_benefit_out(r) for r in cur.fetchall()])


class BenefitIn(BaseModel):
    status: Literal["applied", "in_progress", "granted", "rejected", "expired"]
    reference_no: str | None = Field(default=None, max_length=80)
    applied_on: dt.date | None = None
    granted_on: dt.date | None = None
    valid_until: dt.date | None = None


@router.put(E + "/benefits/{scheme_code}", tags=["Schemes & credit"])
def record_benefit(entity_id: uuid.UUID, scheme_code: str, body: BenefitIn, p: Principal = Auth):
    """Record where an application stands, for example that DPIIT recognition was granted with its certificate number."""
    with tx(p.user_id) as cur:
        require(cur, entity_id, "schemes:write")
        cur.execute("SELECT 1 FROM ref_schemes s JOIN entities en ON en.id = %s AND en.track = ANY(s.for_tracks) WHERE s.code = %s", (entity_id, scheme_code))
        if not cur.fetchone():
            raise ApiError(404, "not_found", "Not found")
        if body.status == "granted" and not body.granted_on:
            body = body.model_copy(update={"granted_on": dt.date.today()})
        cur.execute("""INSERT INTO entity_benefits (entity_id, scheme_code, status, reason, reference_no, applied_on, granted_on, valid_until)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                       ON CONFLICT (entity_id, scheme_code) DO UPDATE SET status = EXCLUDED.status, reference_no = EXCLUDED.reference_no, applied_on = EXCLUDED.applied_on,
                           granted_on = EXCLUDED.granted_on, valid_until = EXCLUDED.valid_until, reason = EXCLUDED.reason""",
                    (entity_id, scheme_code, body.status, f"Recorded by a person: {body.status.replace('_', ' ')}.", body.reference_no, body.applied_on, body.granted_on, body.valid_until))
        audit(cur, entity_id, "benefit.recorded", "entity_benefits", None, after={"scheme": scheme_code, "status": body.status, "reference_no": body.reference_no})
        cur.execute(SCHEME_SQL, (entity_id,))
        return next(_benefit_out(r) for r in cur.fetchall() if r["scheme_code"] == scheme_code)
