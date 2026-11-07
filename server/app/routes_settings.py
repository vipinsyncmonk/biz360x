"""Read endpoints for the Settings and Group screens: people, advisors, certificates, notification choices, and the firm's client grid."""
from __future__ import annotations

import datetime as dt
import uuid
from typing import Literal

from fastapi import APIRouter, Query
from pydantic import BaseModel

from .core import ApiError, Auth, Principal, require, tx
from .routes_orders import js, offset_of, page

router = APIRouter()
E = "/entities/{entity_id}"

AUTH_LABEL = {"password": "Password", "otp": "One-time code", "google": "Google", "microsoft": "Microsoft", "saml": "SSO", "service": "API key"}


@router.get(E + "/members", tags=["Entities"])
def list_members(entity_id: uuid.UUID, p: Principal = Auth, limit: int = Query(default=100, ge=1, le=200), cursor: str | None = None):
    off = offset_of(cursor)
    with tx(p.user_id) as cur:
        require(cur, entity_id, "settings:read")
        cur.execute("""SELECT m.id, m.user_id, u.email, u.full_name, m.role::text, m.modules, m.accepted_at, u.auth_provider, u.mfa_enabled
                         FROM memberships m JOIN users u ON u.id = m.user_id
                        WHERE m.entity_id = %s AND m.revoked_at IS NULL AND u.auth_provider <> 'service'
                        ORDER BY m.role, u.full_name LIMIT %s OFFSET %s""", (entity_id, limit + 1, off))
        rows = []
        for r in cur.fetchall():
            provider, mfa = r.pop("auth_provider"), r.pop("mfa_enabled")
            r["auth"] = AUTH_LABEL.get(provider, provider) + (" · 2FA" if mfa else "")
            rows.append(r)
        return page(js(rows), limit, off)


@router.get(E + "/advisor-grants", tags=["Entities"])
def list_advisor_grants(entity_id: uuid.UUID, p: Principal = Auth, limit: int = Query(default=100, ge=1, le=200), cursor: str | None = None):
    off = offset_of(cursor)
    with tx(p.user_id) as cur:
        require(cur, entity_id, "settings:read")
        cur.execute("""SELECT id, advisor_org_id, advisor_name, advisor_kind, scopes, document_folders, expires_on,
                              CASE WHEN status IN ('invited', 'active') AND expires_on < current_date THEN 'expired' ELSE status END AS status
                         FROM advisor_grants WHERE entity_id = %s ORDER BY created_at DESC, id LIMIT %s OFFSET %s""", (entity_id, limit + 1, off))
        return page(js(cur.fetchall()), limit, off)


@router.get(E + "/dsc-certificates", tags=["Entities"])
def list_dsc(entity_id: uuid.UUID, p: Principal = Auth, limit: int = Query(default=100, ge=1, le=200), cursor: str | None = None):
    off = offset_of(cursor)
    with tx(p.user_id) as cur:
        require(cur, entity_id, "settings:read")
        cur.execute("""SELECT c.id, p.full_name AS person_name, c.dsc_class, c.valid_from, c.valid_until, c.registered_on
                         FROM dsc_certificates c JOIN persons p ON p.id = c.person_id WHERE c.entity_id = %s ORDER BY c.valid_until, c.id LIMIT %s OFFSET %s""",
                    (entity_id, limit + 1, off))
        return page(js(cur.fetchall()), limit, off)


# ------------------------------------------------------------------ the signed-in person's notification choices
class PrefIn(BaseModel):
    entity_id: uuid.UUID
    event_type: Literal["due_in_7_days", "due_tomorrow", "approval_requested", "notice_received", "threshold_crossed", "copilot_suggestion"]
    channel: Literal["email", "whatsapp", "push", "sms"]
    enabled: bool


@router.get("/me/notification-preferences", tags=["Me"])
def list_prefs(p: Principal = Auth):
    with tx(p.user_id) as cur:
        cur.execute("SELECT entity_id, event_type, channel, enabled FROM notification_preferences WHERE user_id = app.current_user_id() ORDER BY entity_id, event_type, channel")
        return js(cur.fetchall())


@router.put("/me/notification-preferences", tags=["Me"])
def replace_prefs(body: list[PrefIn], p: Principal = Auth):
    """Replaces the person's choices for the entities named in the body; other entities are left alone."""
    with tx(p.user_id) as cur:
        for eid in {b.entity_id for b in body}:
            cur.execute("SELECT 1 FROM entities WHERE id = %s", (eid,))
            if not cur.fetchone():
                raise ApiError(404, "not_found", "Not found")
            cur.execute("DELETE FROM notification_preferences WHERE entity_id = %s AND user_id = app.current_user_id()", (eid,))
        for b in body:
            cur.execute("""INSERT INTO notification_preferences (user_id, entity_id, event_type, channel, enabled) VALUES (app.current_user_id(), %s, %s, %s, %s)
                           ON CONFLICT (user_id, entity_id, event_type, channel) DO UPDATE SET enabled = EXCLUDED.enabled""",
                        (b.entity_id, b.event_type, b.channel, b.enabled))
        cur.execute("SELECT entity_id, event_type, channel, enabled FROM notification_preferences WHERE user_id = app.current_user_id() ORDER BY entity_id, event_type, channel")
        return js(cur.fetchall())


# ------------------------------------------------------------------ the firm's client grid
GROUP_SQL = """CASE WHEN r.authority_code = 'MCA' THEN 'MCA' WHEN r.authority_code = 'GSTN' THEN 'GST'
                     WHEN r.authority_code = 'CBDT' AND r.code LIKE 'TDS.%%' THEN 'TDS' WHEN r.authority_code = 'CBDT' THEN 'INCOME_TAX'
                     WHEN r.authority_code IN ('EPFO', 'ESIC') THEN 'EPFO_ESIC' END"""
CELLS = ("MCA", "GST", "TDS", "EPFO_ESIC", "INCOME_TAX")
WEEK = 7


def _member_org(cur, org_id: uuid.UUID) -> None:
    cur.execute("SELECT 1 FROM org_members WHERE organization_id = %s AND user_id = app.current_user_id()", (org_id,))
    if not cur.fetchone():
        raise ApiError(404, "not_found", "Not found")


def cell_state(rows: list[dict], today: dt.date) -> str:
    """One authority for one client: overdue beats waiting on the client beats due soon; filed work is clear; nothing at all is n/a."""
    if not rows:
        return "na"
    open_ = [r for r in rows if r["status"] not in ("filed", "not_applicable", "waived")]
    if any(r["status"] == "overdue" or r["due"] < today for r in open_):
        return "overdue"
    if any(r["status"] == "awaiting_client" for r in open_):
        return "client"
    if any(r["due"] <= today + dt.timedelta(days=30) for r in open_):
        return "due"
    return "clear"


@router.get("/organizations/{org_id}/entities", tags=["Organizations"])
def organization_entities(org_id: uuid.UUID, p: Principal = Auth, filter: Literal["all", "waiting_on_client", "overdue"] = "all", staff: uuid.UUID | None = None,
                          limit: int = Query(default=100, ge=1, le=200), cursor: str | None = None):
    off, today = offset_of(cursor), dt.date.today()
    with tx(p.user_id) as cur:
        _member_org(cur, org_id)
        cur.execute("SELECT id, legal_name, constitution::text FROM entities WHERE organization_id = %s ORDER BY legal_name", (org_id,))
        entities = cur.fetchall()
        ids = [e["id"] for e in entities]
        cur.execute(f"""SELECT o.entity_id, o.status::text AS status, coalesce(o.extended_due_date, o.due_date) AS due, o.owner_user_id, o.period_label, r.title, r.authority_code,
                               {GROUP_SQL} AS grp
                          FROM obligations o JOIN compliance_rules r ON r.id = o.rule_id WHERE o.entity_id = ANY(%s)
                           AND (o.status NOT IN ('filed', 'not_applicable', 'waived') OR o.due_date >= current_date - 45)""", (ids,))
        by_entity: dict = {}
        for r in cur.fetchall():
            by_entity.setdefault(r["entity_id"], []).append(r)
        cur.execute("SELECT user_id, full_name FROM app.org_people(%s)", (org_id,))
        names = {u["user_id"]: u["full_name"] for u in cur.fetchall()}
    out = []
    for e in entities:
        rows = by_entity.get(e["id"], [])
        cells = {c: cell_state([r for r in rows if r["grp"] == c], today) for c in CELLS}
        open_ = sorted((r for r in rows if r["status"] not in ("filed", "not_applicable", "waived")), key=lambda r: r["due"])
        owners = [r["owner_user_id"] for r in open_ if r["owner_user_id"]]
        owner = max(set(owners), key=owners.count) if owners else None
        if filter == "waiting_on_client" and "client" not in cells.values():
            continue
        if filter == "overdue" and "overdue" not in cells.values():
            continue
        if staff and staff not in owners:
            continue
        out.append({"entity_id": e["id"], "legal_name": e["legal_name"], "constitution": e["constitution"], "owner_staff": names.get(owner), "cells": cells,
                    "open_items": [{"title": r["title"], "detail": f"{r['period_label']} · {r['authority_code']}" + (" · waiting on client" if r["status"] == "awaiting_client" else ""),
                                    "due_on": r["due"]} for r in open_[:3]]})
    return page(js(out[off:off + limit + 1]), limit, off)


@router.get("/organizations/{org_id}/workload", tags=["Organizations"])
def organization_workload(org_id: uuid.UUID, p: Principal = Auth):
    with tx(p.user_id) as cur:
        _member_org(cur, org_id)
        cur.execute("""SELECT p.user_id, p.full_name AS name,
                              count(o.id) FILTER (WHERE o.status NOT IN ('filed', 'not_applicable', 'waived') AND coalesce(o.extended_due_date, o.due_date) <= current_date + %s) AS filings_due
                         FROM app.org_people(%s) p
                         LEFT JOIN obligations o ON o.owner_user_id = p.user_id AND o.entity_id IN (SELECT id FROM entities WHERE organization_id = %s)
                        GROUP BY p.user_id, p.full_name ORDER BY filings_due DESC, p.full_name""", (WEEK, org_id, org_id))
        return js(cur.fetchall())
