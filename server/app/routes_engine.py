"""Compliance engine endpoints: engine runs, calendar, thresholds, onboarding preview."""
from __future__ import annotations

import datetime as dt
import uuid

from fastapi import APIRouter, Query
from pydantic import BaseModel

from . import engine
from .core import ApiError, Auth, Principal, require, tx

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
        return engine.preview(cur, body.model_dump())
