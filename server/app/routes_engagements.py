"""Engagements from the practitioner's side: the invitations waiting for a person, accepting one as themselves or for their firm,
declining it, and which of a firm's staff work on which client.

A company engages a practitioner from Settings (`POST /advisor-grants`, routes_settings.py). The person invited cannot see the company
until they accept, so the invitation is read and answered through database functions that reach exactly that row (migration 39)."""
from __future__ import annotations

import uuid

from fastapi import APIRouter
from pydantic import BaseModel, Field

from . import roles
from .core import ApiError, Auth, Principal, tx
from .routes_orders import js
from .routes_work import fill_from_preset

router = APIRouter()
FIRM_KINDS = ("ca_firm", "cs_firm", "law_firm")


def _bad(field: str, message: str):
    return ApiError(400, "validation_failed", message, errors=[{"field": field, "message": message}])


class AcceptIn(BaseModel):
    organization_id: uuid.UUID | None = None       # accept for a firm the caller is a partner or admin of; left out, they accept as themselves


def _invites(cur) -> list[dict]:
    cur.execute("SELECT * FROM app.my_engagement_invites()")
    out = []
    for r in cur.fetchall():
        row = js(r)
        row.update(service_label=roles.SERVICES.get(r["service"]), audit=r["service"] in roles.AUDIT_SERVICES)
        out.append(row)
    return out


def _my_firms(cur) -> list[dict]:
    cur.execute("""SELECT o.id, o.name FROM org_members om JOIN organizations o ON o.id = om.organization_id
                    WHERE om.user_id = app.current_user_id() AND om.role IN ('admin', 'partner') AND o.kind = ANY(%s) ORDER BY o.name""", (list(FIRM_KINDS),))
    return js(cur.fetchall())


@router.get("/me/engagements", tags=["Me"])
def my_engagements(p: Principal = Auth):
    """Invitations waiting for the caller, and the firms they could accept one for."""
    with tx(p.user_id) as cur:
        return {"invitations": _invites(cur), "firms": _my_firms(cur)}


def _answer(grant_id, p: Principal, accept: bool, org_id=None) -> dict:
    with tx(p.user_id) as cur:
        if org_id is not None and str(org_id) not in {f["id"] for f in _my_firms(cur)}:
            raise _bad("organization_id", "You can accept only for a firm you are a partner or admin of")
        cur.execute("SELECT preset FROM app.my_engagement_invites() i JOIN LATERAL (SELECT preset FROM advisor_grants g WHERE g.id = i.id) g ON true WHERE i.id = %s", (grant_id,))
        cur.execute("SELECT app.answer_engagement(%s, %s, %s) AS entity_id", (grant_id, accept, org_id))
        entity_id = cur.fetchone()["entity_id"]
        if entity_id is None:
            raise ApiError(404, "not_found", "Not found")                        # not theirs, already answered, withdrawn or expired
        out = {"id": str(grant_id), "status": "active" if accept else "declined", "entity_id": str(entity_id), "accepted_for": str(org_id) if org_id else None, "work_taken_on": 0}
        if accept and org_id is None:                                            # engaged as a person: take the work the preset usually prepares, where it is free
            cur.execute("SELECT preset, scopes FROM advisor_grants WHERE id = %s", (grant_id,))
            g = cur.fetchone()
            cur.execute("SELECT app.current_user_id() AS me")
            out["work_taken_on"] = fill_from_preset(cur, entity_id, cur.fetchone()["me"], g["preset"], g["scopes"])
        return out


@router.post("/me/engagements/{grant_id}/accept", tags=["Me"])
def accept_engagement(grant_id: uuid.UUID, body: AcceptIn | None = None, p: Principal = Auth):
    """Accept an invitation. Access to the company starts now. Accepting for a firm makes it the firm's engagement: its partners see the
    client, and its staff once they are assigned to it. Refused with `independence_conflict` when the same practitioner would hold the
    statutory audit and the books."""
    return _answer(grant_id, p, True, body.organization_id if body else None)


@router.post("/me/engagements/{grant_id}/decline", tags=["Me"])
def decline_engagement(grant_id: uuid.UUID, p: Principal = Auth):
    return _answer(grant_id, p, False)


# ================================================================== a firm's staff and its clients
class StaffIn(BaseModel):
    user_ids: list[uuid.UUID] = Field(max_length=200)


def _org(cur, org_id, *, partner: bool = False) -> None:
    cur.execute("SELECT role FROM org_members WHERE organization_id = %s AND user_id = app.current_user_id()", (org_id,))
    r = cur.fetchone()
    if not r:
        raise ApiError(404, "not_found", "Not found")
    if partner and r["role"] not in ("admin", "partner"):
        raise ApiError(403, "permission_denied", "Only a partner or admin of the firm decides who works on a client")


def _client_staff(cur, org_id) -> dict:
    cur.execute("""SELECT c.entity_id, e.legal_name, bool_or(c.own) AS set_up_by_firm, array_remove(array_agg(DISTINCT c.service), NULL) AS services
                     FROM (SELECT id AS entity_id, true AS own, NULL::text AS service FROM entities WHERE organization_id = %s
                           UNION ALL SELECT entity_id, false, service FROM advisor_grants WHERE advisor_org_id = %s AND status = 'active'
                                        AND (expires_on IS NULL OR expires_on >= current_date)) c
                     JOIN entities e ON e.id = c.entity_id GROUP BY c.entity_id, e.legal_name ORDER BY e.legal_name""", (org_id, org_id))
    clients = cur.fetchall()
    cur.execute("SELECT entity_id, user_id FROM firm_client_staff WHERE organization_id = %s", (org_id,))
    by: dict = {}
    for r in cur.fetchall():
        by.setdefault(r["entity_id"], []).append(str(r["user_id"]))
    cur.execute("SELECT user_id, full_name AS name, role FROM app.org_people(%s)", (org_id,))
    people = [{"user_id": str(r["user_id"]), "name": r["name"], "role": r["role"], "sees_all": r["role"] in ("admin", "partner")} for r in cur.fetchall()]
    return {"clients": [{"entity_id": str(c["entity_id"]), "legal_name": c["legal_name"], "set_up_by_firm": c["set_up_by_firm"],
                         "services": [roles.SERVICES.get(s, s) for s in c["services"]], "staff": sorted(by.get(c["entity_id"], []))} for c in clients], "people": people}


@router.get("/organizations/{org_id}/client-staff", tags=["Organizations"])
def get_client_staff(org_id: uuid.UUID, p: Principal = Auth):
    """The firm's clients and which staff work on each. Partners and admins see every client; staff see the ones they are assigned to."""
    with tx(p.user_id) as cur:
        _org(cur, org_id)
        return _client_staff(cur, org_id)


@router.put("/organizations/{org_id}/client-staff/{entity_id}", tags=["Organizations"])
def set_client_staff(org_id: uuid.UUID, entity_id: uuid.UUID, body: StaffIn, p: Principal = Auth):
    """Say which staff work on a client. Only a partner or admin can. Someone taken off a client stops seeing it at once."""
    with tx(p.user_id) as cur:
        _org(cur, org_id, partner=True)
        state = _client_staff(cur, org_id)
        if str(entity_id) not in {c["entity_id"] for c in state["clients"]}:
            raise ApiError(404, "not_found", "Not found")
        staff = {x["user_id"] for x in state["people"] if x["role"] == "staff"}
        wanted = {str(u) for u in body.user_ids}
        if wanted - staff:
            raise _bad("user_ids", "Only the firm's staff are assigned to clients; partners and admins already see every client")
        cur.execute("DELETE FROM firm_client_staff WHERE organization_id = %s AND entity_id = %s AND user_id <> ALL(%s::uuid[])", (org_id, entity_id, list(wanted)))
        for u in wanted:
            cur.execute("""INSERT INTO firm_client_staff (organization_id, entity_id, user_id, assigned_by) VALUES (%s, %s, %s, app.current_user_id())
                           ON CONFLICT DO NOTHING""", (org_id, entity_id, u))
        cur.execute("""INSERT INTO audit_log (entity_id, actor_kind, actor_user_id, action, subject, subject_id, after)
                       VALUES (%s, 'user', app.current_user_id(), 'firm.client_staff_set', 'entities', %s, %s::jsonb)""",
                    (entity_id, entity_id, __import__("json").dumps({"organization_id": str(org_id), "staff": sorted(wanted)})))
        return _client_staff(cur, org_id)
