"""Auth, /me and /jobs."""
from __future__ import annotations

import uuid
from typing import Literal

from fastapi import APIRouter, Response
from pydantic import BaseModel

from .core import ENV, ApiError, Auth, Principal, issue_access_token, tx

router = APIRouter()


class OtpRequest(BaseModel):
    email: str | None = None
    mobile_e164: str | None = None


class TokenRequest(BaseModel):
    grant_type: Literal["otp", "refresh_token", "password", "authorization_code"]
    email: str | None = None
    otp: str | None = None


@router.post("/auth/otp", status_code=204, tags=["Auth"])
def request_otp(body: OtpRequest):
    # Production: send via SMS / email provider and store a hashed code with a 5-minute expiry.
    return Response(status_code=204)


@router.post("/auth/token", tags=["Auth"])
def issue_token(body: TokenRequest):
    if body.grant_type != "otp":
        raise ApiError(400, "validation_failed", "Only the otp grant is enabled in this reference server")
    if ENV != "dev" or body.otp != "000000":
        raise ApiError(401, "unauthorized", "Invalid code")       # dev accepts 000000; prod verifies the stored hash
    with tx(None, role="biz_worker") as cur:                      # auth service reads users without RLS
        cur.execute("SELECT id, email FROM users WHERE email = %s", (body.email,))
        u = cur.fetchone()
        if not u:
            raise ApiError(401, "unauthorized", "Invalid code")
        cur.execute("UPDATE users SET last_login_at = now() WHERE id = %s", (u["id"],))
    return {"access_token": issue_access_token(u["id"], u["email"]), "token_type": "Bearer", "expires_in": 900}


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
