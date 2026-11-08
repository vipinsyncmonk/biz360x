"""API keys, webhook endpoints, the audit trail and its verification."""
from __future__ import annotations

import datetime as dt
import re
import secrets
import uuid

from fastapi import APIRouter, Query, Response
from pydantic import BaseModel, Field

from . import secretbox, webhooks
from .core import ApiError, Auth, Principal, audit, emit, hash_api_key, require, tx
from .routes_orders import js, offset_of, page

router = APIRouter()
E = "/entities/{entity_id}"
PERMISSION_RE = re.compile(r"^[a-z_]+:(read|write|use)$")
FORBIDDEN_FOR_KEYS = {"settings:write", "approvals:read", "approvals:write", "approvals:use"}


# ------------------------------------------------------------------ API keys
class ApiKeyIn(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    permissions: list[str] = Field(min_length=1, max_length=50)
    expires_at: dt.datetime | None = None


def _key_row(r: dict) -> dict:
    return {"id": r["id"], "name": r["name"], "prefix": "bzx_live_" + r["prefix"], "permissions": r["permissions"], "last_used_at": r["last_used_at"],
            "expires_at": r["expires_at"], "revoked_at": r["revoked_at"], "created_at": r["created_at"]}


@router.get(E + "/api-keys", tags=["API keys"])
def list_api_keys(entity_id: uuid.UUID, limit: int = Query(50, ge=1, le=200), cursor: str | None = None, p: Principal = Auth):
    off = offset_of(cursor)
    with tx(p.user_id) as cur:
        require(cur, entity_id, "settings:read")
        cur.execute("SELECT * FROM api_keys WHERE entity_id = %s ORDER BY created_at DESC, id LIMIT %s OFFSET %s", (entity_id, limit + 1, off))
        rows = [_key_row(r) for r in cur.fetchall()]
    return js(page(rows, limit, off))


@router.post(E + "/api-keys", status_code=201, tags=["API keys"])
def create_api_key(entity_id: uuid.UUID, body: ApiKeyIn, p: Principal = Auth):
    """The full key is in this response and nowhere else: only its hash is stored."""
    if p.api_key_id:
        raise ApiError(403, "permission_denied", "An API key cannot create API keys")
    for perm in body.permissions:
        if not PERMISSION_RE.match(perm):
            raise ApiError(400, "validation_failed", f"'{perm}' is not a valid permission", "Use module:read, module:write or module:use. Approving, filing and paying need a person.")
        if perm in FORBIDDEN_FOR_KEYS:
            raise ApiError(400, "validation_failed", f"An API key cannot hold {perm}")
    if body.expires_at and body.expires_at <= dt.datetime.now(dt.timezone.utc):
        raise ApiError(400, "validation_failed", "expires_at must be in the future")
    prefix = secrets.token_hex(4)
    key = f"bzx_live_{prefix}_{secrets.token_urlsafe(32)}"
    perms = sorted(set(body.permissions))
    with tx(p.user_id) as cur:
        require(cur, entity_id, "settings:write")
        cur.execute("SELECT app.create_api_key(%s, %s, %s, %s, %s, %s) AS id", (entity_id, body.name, prefix, hash_api_key(key), perms, body.expires_at))
        kid = cur.fetchone()["id"]
        cur.execute("SELECT * FROM api_keys WHERE id = %s", (kid,))
        row = _key_row(cur.fetchone())
        audit(cur, entity_id, "api_key.created", "api_keys", kid, {"name": body.name, "permissions": perms, "prefix": row["prefix"]})
        emit(cur, entity_id, "api_key.created", "api_keys", kid, {"name": body.name})
    return js({**row, "key": key})


@router.delete(E + "/api-keys/{key_id}", status_code=204, tags=["API keys"])
def revoke_api_key(entity_id: uuid.UUID, key_id: uuid.UUID, p: Principal = Auth):
    with tx(p.user_id) as cur:
        require(cur, entity_id, "settings:write")
        cur.execute("SELECT 1 FROM api_keys WHERE id = %s AND entity_id = %s", (key_id, entity_id))
        if not cur.fetchone():
            raise ApiError(404, "not_found", "Not found")
        cur.execute("SELECT app.revoke_api_key(%s)", (key_id,))
        audit(cur, entity_id, "api_key.revoked", "api_keys", key_id)
        emit(cur, entity_id, "api_key.revoked", "api_keys", key_id)
    return Response(status_code=204)


# ------------------------------------------------------------------ webhooks
class WebhookIn(BaseModel):
    url: str = Field(max_length=500)
    events: list[str] = Field(min_length=1, max_length=40)
    is_active: bool = True


class WebhookPatch(BaseModel):
    url: str | None = Field(default=None, max_length=500)
    events: list[str] | None = Field(default=None, min_length=1, max_length=40)
    is_active: bool | None = None


EVENT_RE = re.compile(r"^(\*|[a-z_]+\.[a-z_]+)$")


def _check_events(events: list[str]) -> None:
    for e in events:
        if not EVENT_RE.match(e):
            raise ApiError(400, "validation_failed", f"'{e}' is not an event type", "Event types look like sales_document.paid; * means all.")


def _hook_row(r: dict, secret: str | None = None) -> dict:
    out = {"id": r["id"], "url": r["url"], "events": r["events"], "is_active": r["is_active"], "created_at": r["created_at"]}
    if secret:
        out["secret"] = secret
    return out


@router.get(E + "/webhooks", tags=["Events & webhooks"])
def list_webhooks(entity_id: uuid.UUID, limit: int = Query(50, ge=1, le=200), cursor: str | None = None, p: Principal = Auth):
    off = offset_of(cursor)
    with tx(p.user_id) as cur:
        require(cur, entity_id, "settings:read")
        cur.execute("SELECT * FROM webhook_endpoints WHERE entity_id = %s ORDER BY created_at DESC, id LIMIT %s OFFSET %s", (entity_id, limit + 1, off))
        rows = [_hook_row(r) for r in cur.fetchall()]
    return js(page(rows, limit, off))


@router.post(E + "/webhooks", status_code=201, tags=["Events & webhooks"])
def create_webhook(entity_id: uuid.UUID, body: WebhookIn, p: Principal = Auth):
    webhooks.check_url(body.url)
    _check_events(body.events)
    secret = webhooks.new_secret()
    with tx(p.user_id) as cur:
        require(cur, entity_id, "settings:write")
        cur.execute("INSERT INTO webhook_endpoints (entity_id, url, events, secret_enc, is_active) VALUES (%s, %s, %s, %s, %s) RETURNING *",
                    (entity_id, body.url, sorted(set(body.events)), secretbox.encrypt(secret), body.is_active))
        row = cur.fetchone()
        audit(cur, entity_id, "webhook.created", "webhook_endpoints", row["id"], {"url": body.url, "events": row["events"]})
    return js(_hook_row(row, secret))                       # the secret is shown this once


def _load_hook(cur, entity_id, hook_id) -> dict:
    cur.execute("SELECT * FROM webhook_endpoints WHERE id = %s AND entity_id = %s", (hook_id, entity_id))
    row = cur.fetchone()
    if not row:
        raise ApiError(404, "not_found", "Not found")
    return row


@router.get(E + "/webhooks/{hook_id}", tags=["Events & webhooks"])
def get_webhook(entity_id: uuid.UUID, hook_id: uuid.UUID, p: Principal = Auth):
    with tx(p.user_id) as cur:
        require(cur, entity_id, "settings:read")
        row = _load_hook(cur, entity_id, hook_id)
    return js(_hook_row(row))


@router.patch(E + "/webhooks/{hook_id}", tags=["Events & webhooks"])
def update_webhook(entity_id: uuid.UUID, hook_id: uuid.UUID, body: WebhookPatch, p: Principal = Auth):
    if body.url is not None:
        webhooks.check_url(body.url)
    if body.events is not None:
        _check_events(body.events)
    with tx(p.user_id) as cur:
        require(cur, entity_id, "settings:write")
        before = _load_hook(cur, entity_id, hook_id)
        cur.execute("""UPDATE webhook_endpoints SET url = coalesce(%s, url), events = coalesce(%s, events), is_active = coalesce(%s, is_active)
                        WHERE id = %s RETURNING *""", (body.url, sorted(set(body.events)) if body.events else None, body.is_active, hook_id))
        row = cur.fetchone()
        audit(cur, entity_id, "webhook.updated", "webhook_endpoints", hook_id, {"url": row["url"], "events": row["events"], "is_active": row["is_active"]},
              {"url": before["url"], "events": before["events"], "is_active": before["is_active"]})
    return js(_hook_row(row))


@router.delete(E + "/webhooks/{hook_id}", status_code=204, tags=["Events & webhooks"])
def delete_webhook(entity_id: uuid.UUID, hook_id: uuid.UUID, p: Principal = Auth):
    with tx(p.user_id) as cur:
        require(cur, entity_id, "settings:write")
        before = _load_hook(cur, entity_id, hook_id)
        cur.execute("DELETE FROM webhook_deliveries WHERE endpoint_id = %s", (hook_id,))
        cur.execute("DELETE FROM webhook_endpoints WHERE id = %s", (hook_id,))
        audit(cur, entity_id, "webhook.deleted", "webhook_endpoints", hook_id, None, {"url": before["url"]})
    return Response(status_code=204)


@router.post(E + "/webhooks/{hook_id}/test", status_code=204, tags=["Events & webhooks"])
def test_webhook(entity_id: uuid.UUID, hook_id: uuid.UUID, p: Principal = Auth):
    with tx(p.user_id) as cur:
        require(cur, entity_id, "settings:write")
        _load_hook(cur, entity_id, hook_id)
        emit(cur, entity_id, "webhook.test", "webhook_endpoints", hook_id, {"endpoint_id": str(hook_id), "message": "This is a test event"})
    return Response(status_code=204)


@router.get(E + "/webhooks/{hook_id}/deliveries", tags=["Events & webhooks"])
def list_deliveries(entity_id: uuid.UUID, hook_id: uuid.UUID, limit: int = Query(50, ge=1, le=200), cursor: str | None = None, p: Principal = Auth):
    off = offset_of(cursor)
    with tx(p.user_id) as cur:
        require(cur, entity_id, "settings:read")
        _load_hook(cur, entity_id, hook_id)
        cur.execute("""SELECT d.id, d.event_id, e.type, d.attempt, d.status_code, d.error, d.attempted_at, d.delivered_at, d.next_retry_at
                         FROM webhook_deliveries d JOIN event_outbox e ON e.id = d.event_id WHERE d.endpoint_id = %s
                        ORDER BY d.id DESC LIMIT %s OFFSET %s""", (hook_id, limit + 1, off))
        rows = cur.fetchall()
    return js(page(rows, limit, off))


# ------------------------------------------------------------------ audit trail
@router.get(E + "/audit-log", tags=["Audit"])
def list_audit(entity_id: uuid.UUID, limit: int = Query(50, ge=1, le=200), cursor: str | None = None, subject: str | None = None,
               subject_id: uuid.UUID | None = None, actor: uuid.UUID | None = None, from_: dt.date | None = Query(None, alias="from"),
               to: dt.date | None = None, p: Principal = Auth):
    off = offset_of(cursor)
    with tx(p.user_id) as cur:
        require(cur, entity_id, "audit:read")
        cur.execute("""SELECT a.id, a.at, a.actor_kind, a.actor_user_id, u.full_name AS actor_name, a.action, a.subject, a.subject_id,
                              a.before, a.after, a.hash, a.prev_hash
                         FROM audit_log a LEFT JOIN users u ON u.id = a.actor_user_id
                        WHERE a.entity_id = %s AND (%s::text IS NULL OR a.subject = %s) AND (%s::uuid IS NULL OR a.subject_id = %s)
                          AND (%s::uuid IS NULL OR a.actor_user_id = %s) AND (%s::date IS NULL OR a.at >= %s) AND (%s::date IS NULL OR a.at < %s::date + 1)
                        ORDER BY a.id DESC LIMIT %s OFFSET %s""",
                    (entity_id, subject, subject, subject_id, subject_id, actor, actor, from_, from_, to, to, limit + 1, off))
        rows = cur.fetchall()
    return js(page(rows, limit, off))


@router.get(E + "/audit-log/verify", tags=["Audit"])
def verify_audit(entity_id: uuid.UUID, p: Principal = Auth):
    """The chain is global across tenants, so it is checked as a whole. A break in another tenant's rows is reported as a break
    without saying where: this tenant's evidence is only as good as the chain it is part of."""
    with tx(p.user_id) as cur:
        require(cur, entity_id, "audit:read")
        cur.execute("SELECT app.audit_verify() AS broken")
        broken = cur.fetchone()["broken"]
        cur.execute("SELECT count(*) AS n FROM audit_log WHERE entity_id = %s", (entity_id,))
        n = cur.fetchone()["n"]
        first = None
        if broken is not None:
            cur.execute("SELECT 1 FROM audit_log WHERE id = %s AND entity_id = %s", (broken, entity_id))
            first = broken if cur.fetchone() else None
    return {"intact": broken is None, "first_broken_id": first, "checked": n}
