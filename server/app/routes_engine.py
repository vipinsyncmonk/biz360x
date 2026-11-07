"""Compliance engine endpoints: engine runs, calendar, thresholds, onboarding preview."""
from __future__ import annotations

import datetime as dt
import json
import uuid

from fastapi import APIRouter, Header, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from . import engine
from .core import ApiError, Auth, Principal, audit, emit, idempotency_begin, idempotency_finish, require, tx

router = APIRouter()
E = "/entities/{entity_id}"
RUN_FIELDS = ("id", "trigger", "started_at", "finished_at", "rules_evaluated", "obligations_created", "obligations_changed")


def _run(row: dict) -> dict:
    return {k: row[k] for k in RUN_FIELDS}


@router.post(E + "/compliance/engine-runs", status_code=201, tags=["Compliance"])
def run_engine_now(entity_id: uuid.UUID, p: Principal = Auth):
    with tx(p.user_id) as cur:
        require(cur, entity_id, "compliance:write")
        return _run(engine.run_engine(cur, entity_id, "manual"))


@router.get(E + "/compliance/engine-runs/{run_id}", tags=["Compliance"])
def get_engine_run(entity_id: uuid.UUID, run_id: uuid.UUID, p: Principal = Auth):
    with tx(p.user_id) as cur:
        require(cur, entity_id, "compliance:read")
        cur.execute("SELECT * FROM engine_runs WHERE id = %s AND entity_id = %s", (run_id, entity_id))
        row = cur.fetchone()
        if not row:
            raise ApiError(404, "not_found", "Not found")
        return _run(row)


@router.get(E + "/compliance/calendar", tags=["Compliance"])
def calendar(entity_id: uuid.UUID, p: Principal = Auth, from_: dt.date | None = Query(default=None, alias="from"),
             to: dt.date | None = None):
    start = from_ or dt.date.today()
    end = to or start + dt.timedelta(days=30)
    if end < start or (end - start).days > 366:
        raise ApiError(400, "validation_failed", "`to` must be on or after `from`, within 366 days")
    with tx(p.user_id) as cur:
        require(cur, entity_id, "compliance:read")
        cur.execute("""SELECT coalesce(o.extended_due_date, o.due_date) AS day, o.id AS obligation_id, r.title, r.authority_code AS authority,
                              o.status::text AS status, er.number AS registration
                         FROM obligations o JOIN compliance_rules r ON r.id = o.rule_id
                         LEFT JOIN entity_registrations er ON er.id = o.registration_id
                        WHERE o.entity_id = %s AND o.status <> 'not_applicable'
                          AND coalesce(o.extended_due_date, o.due_date) BETWEEN %s AND %s
                        ORDER BY day, r.title""", (entity_id, start, end))
        days: dict[dt.date, list] = {}
        for r in cur.fetchall():
            days.setdefault(r.pop("day"), []).append(r)
        return [{"date": d, "items": items} for d, items in days.items()]


@router.get(E + "/compliance/thresholds", tags=["Compliance"])
def thresholds(entity_id: uuid.UUID, p: Principal = Auth):
    with tx(p.user_id) as cur:
        require(cur, entity_id, "compliance:read")
        return [{"rule_code": w["rule_code"], "title": w["title"], "fact_key": w["fact_key"], "threshold": w["threshold"],
                 "current_value": w["current_value"], "pct": w["pct"], "note": w["note"]}
                for w in engine.threshold_rows(cur, entity_id)]


class OnboardingAnswers(BaseModel):
    lookup_id: uuid.UUID
    track: str = "business"
    organization_id: uuid.UUID | None = None
    gstins: list[str] = []
    headcount_band: str | None = None
    turnover_band: str | None = None
    has_foreign_shareholder: bool = False
    buys_from_mse: str | None = None


@router.post("/onboarding/preview", tags=["Onboarding"])
def preview_obligations(body: OnboardingAnswers, p: Principal = Auth):
    if body.headcount_band and body.headcount_band not in engine.BAND_HEADCOUNT:
        raise ApiError(400, "validation_failed", "Unknown headcount_band")
    if body.turnover_band and body.turnover_band not in engine.BAND_TURNOVER:
        raise ApiError(400, "validation_failed", "Unknown turnover_band")
    with tx(p.user_id) as cur:                # read-only: the preview never writes
        answers = body.model_dump()
        kw = {}
        cur.execute("SELECT status, result FROM registry_lookups WHERE id = %s", (body.lookup_id,))
        lk = cur.fetchone()
        if lk and lk["status"] == "found":                      # what the registry says about the company beats assumptions
            ent = (lk["result"] or {}).get("entity", {})
            if ent.get("constitution"):
                kw["constitution"] = ent["constitution"]
            if ent.get("incorporated_on"):
                kw["incorporated_on"] = dt.date.fromisoformat(ent["incorporated_on"])
            if not answers["gstins"]:
                answers["gstins"] = [g["gstin"] for g in (lk["result"] or {}).get("gstins", [])]
        return engine.preview(cur, answers, **kw)


# ------------------------------------------------------------------ create a workspace
COMPANY_KINDS = ("private_limited", "public_limited", "opc", "section8")


def _facts_from(answers: OnboardingAnswers, ent: dict) -> list[dict]:
    out = []
    if answers.headcount_band:
        out.append({"key": "headcount", "num": engine.BAND_HEADCOUNT[answers.headcount_band], "source": "user"})
    if answers.turnover_band:
        out.append({"key": "aato", "num": str(engine.BAND_TURNOVER[answers.turnover_band]), "source": "user"})
    out.append({"key": "has_foreign_investor", "bool": answers.has_foreign_shareholder, "source": "user"})
    if answers.buys_from_mse in ("yes", "no"):
        out.append({"key": "has_mse_dues_over_45_days", "bool": answers.buys_from_mse == "yes", "source": "user"})
    if ent.get("paid_up_capital"):
        out.append({"key": "paid_up_capital", "num": str(ent["paid_up_capital"]), "source": "registry"})
    return out


@router.post("/onboarding/workspaces", status_code=201, tags=["Onboarding"])
def create_workspace(body: OnboardingAnswers, p: Principal = Auth, idempotency_key: str | None = Header(default=None, alias="Idempotency-Key")):
    """Create the organization (unless one is given), the entity, its registrations and facts, then run the engine once.
    Everything comes from the registry lookup the person made, not from what the browser claims about the company."""
    if body.headcount_band and body.headcount_band not in engine.BAND_HEADCOUNT:
        raise ApiError(400, "validation_failed", "Unknown headcount_band")
    if body.turnover_band and body.turnover_band not in engine.BAND_TURNOVER:
        raise ApiError(400, "validation_failed", "Unknown turnover_band")
    if p.api_key_id:
        raise ApiError(403, "permission_denied", "An API key cannot create a workspace")
    with tx(p.user_id) as cur:
        replay = idempotency_begin(cur, idempotency_key, "POST", "/onboarding/workspaces", body.model_dump(mode="json"))
        if replay:
            return JSONResponse(replay["body"], status_code=replay["status"] or 201)
        cur.execute("SELECT kind, identifier, status, result FROM registry_lookups WHERE id = %s", (body.lookup_id,))
        lk = cur.fetchone()
        if not lk:
            raise ApiError(404, "not_found", "Not found")
        if lk["status"] != "found":
            raise ApiError(409, "conflict", "The registry lookup has not found a company", "Look the company up again before creating the workspace.")
        res = lk["result"] or {}
        ent = res.get("entity") or {}
        known = {g["gstin"]: g for g in res.get("gstins", [])}
        wanted = body.gstins or list(known)
        stray = [g for g in wanted if g not in known]
        if stray:
            raise ApiError(400, "validation_failed", "A registration was not in the registry result", errors=[{"field": "gstins", "message": f"{g} is not registered to this company" } for g in stray])
        constitution = ent.get("constitution")
        cin = ent.get("cin") or (lk["identifier"] if lk["kind"] == "cin" else None)
        llpin = ent.get("llpin") or (lk["identifier"] if lk["kind"] == "llpin" else None)
        if not ent.get("legal_name") or not constitution:
            raise ApiError(409, "conflict", "The registry result is incomplete", "It has no legal name or constitution.")
        if (constitution in COMPANY_KINDS and not cin) or (constitution == "llp" and not llpin):
            raise ApiError(409, "conflict", "The registry result has no CIN or LLPIN", "Look the company up by its CIN or LLPIN.")
        pan = (wanted[0][2:12] if wanted else None) or (lk["identifier"] if lk["kind"] == "pan" else None)
        udy = res.get("udyam") or {}
        entity = {"legal_name": ent["legal_name"], "constitution": constitution, "track": body.track, "pan": pan, "cin": cin, "llpin": llpin,
                  "incorporated_on": ent.get("incorporated_on"), "registered_state": ent.get("registered_state"),
                  "registered_address": ent.get("registered_address"), "nic_code": ent.get("nic_code"),
                  "udyam_number": udy.get("number"), "udyam_class": udy.get("class")}
        cur.execute("SELECT app.create_workspace(%s, %s, %s, %s, %s) AS id",
                    (body.organization_id, ent["legal_name"], json.dumps(entity), json.dumps([known[g] for g in wanted]), json.dumps(_facts_from(body, ent))))
        entity_id = cur.fetchone()["id"]
        run = engine.run_engine(cur, entity_id, "onboarding")
        out = {"entity_id": str(entity_id), "engine_run_id": str(run["id"]), "obligations_created": run["obligations_created"]}
        audit(cur, entity_id, "workspace.created", "entities", entity_id, {"legal_name": ent["legal_name"], "track": body.track, "registrations": wanted})
        emit(cur, entity_id, "workspace.created", "entities", entity_id, {"legal_name": ent["legal_name"]})
        idempotency_finish(cur, idempotency_key, 201, out)
    return out
