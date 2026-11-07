"""Auth, /me and /jobs."""
from __future__ import annotations

import uuid
from typing import Literal

from fastapi import APIRouter, Response
from pydantic import BaseModel

from . import auth
from .core import ApiError, Auth, Principal, tx

router = APIRouter()


class OtpRequest(BaseModel):
    email: str | None = None
    mobile_e164: str | None = None


class TokenRequest(BaseModel):
    grant_type: Literal["otp", "refresh_token", "password", "authorization_code"]
    email: str | None = None
    mobile_e164: str | None = None
    otp: str | None = None
    refresh_token: str | None = None
    code: str | None = None
    redirect_uri: str | None = None
    mfa_code: str | None = None


class LogoutRequest(BaseModel):
    refresh_token: str


@router.post("/auth/otp", status_code=204, tags=["Auth"])
def request_otp(body: OtpRequest):
    auth.request_code(body.email, body.mobile_e164)
    return Response(status_code=204)


@router.post("/auth/token", tags=["Auth"])
def issue_token(body: TokenRequest):
    if body.grant_type == "otp":
        return auth.login_with_code(body.email, body.mobile_e164, body.otp, body.mfa_code)
    if body.grant_type == "refresh_token":
        return auth.refresh(body.refresh_token)
    if body.grant_type == "authorization_code":
        return auth.login_with_idp(body.code, body.redirect_uri)
    raise ApiError(400, "validation_failed", "This grant type is not enabled", "Use otp, refresh_token or authorization_code.")


@router.post("/auth/logout", status_code=204, tags=["Auth"])
def logout(body: LogoutRequest, p: Principal = Auth):
    auth.logout(body.refresh_token)
    return Response(status_code=204)


@router.get("/me", tags=["Me"])
def get_me(p: Principal = Auth):
    with tx(p.user_id) as cur:
        cur.execute("SELECT id, email, full_name, mobile_e164, mfa_enabled FROM users WHERE id = app.current_user_id()")
        me = cur.fetchone()
        cur.execute("""
            SELECT e.id, e.legal_name, e.constitution::text, e.track::text,
                   coalesce(m.role::text, CASE WHEN g.id IS NOT NULL THEN 'advisor' ELSE 'org_staff' END) AS role,
                   coalesce(m.modules, ARRAY(SELECT jsonb_object_keys(g.scopes))) AS modules
              FROM entities e
              LEFT JOIN memberships m ON m.entity_id = e.id AND m.user_id = app.current_user_id() AND m.revoked_at IS NULL
              LEFT JOIN advisor_grants g ON g.entity_id = e.id AND g.status = 'active'
                    AND g.advisor_org_id IN (SELECT app.my_org_ids())
             ORDER BY e.legal_name""")
        me["entities"] = cur.fetchall()
        cur.execute("""SELECT o.id, o.name, o.kind::text, om.role FROM org_members om
                        JOIN organizations o ON o.id = om.organization_id WHERE om.user_id = app.current_user_id()""")
        me["organizations"] = cur.fetchall()
    return me


@router.get("/jobs/{job_id}", tags=["Portals & jobs"])
def get_job(job_id: uuid.UUID, p: Principal = Auth):
    with tx(p.user_id) as cur:
        cur.execute("""SELECT id, kind, status, progress, subject, subject_id, result, error, created_at, finished_at
                         FROM jobs WHERE id = %s""", (job_id,))
        j = cur.fetchone()
    if not j:
        raise ApiError(404, "not_found", "Not found")
    return j
