"""Core plumbing: settings, database transactions with RLS identity,
RFC 9457 errors, authentication, permissions, idempotency, audit, events."""
from __future__ import annotations

import contextlib
import logging
import datetime as dt
import hashlib
import hmac
import json
import os
import re
import uuid
from dataclasses import dataclass
from decimal import Decimal

import jwt
import psycopg
from fastapi import Depends, Header, Request
from fastapi.responses import JSONResponse
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

# ------------------------------------------------------------------ settings
DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql://postgres@localhost:5432/biz360x")
JWT_SECRET = os.environ.get("JWT_SECRET", "dev-only-secret-change-me-in-every-environment")
ENV = os.environ.get("BIZ_ENV", "dev")
ACCESS_TTL = dt.timedelta(minutes=15)

_pool: ConnectionPool | None = None


def pool() -> ConnectionPool:
    global _pool
    if _pool is None:
        _pool = ConnectionPool(DATABASE_URL, min_size=1, max_size=10, kwargs={"row_factory": dict_row}, open=True)
    return _pool


@contextlib.contextmanager
def tx(user_id: uuid.UUID | str | None, role: str = "biz_app"):
    """One transaction per request. The caller's id goes on the session so
    PostgreSQL row-level security decides what is visible."""
    with pool().connection() as conn:
        with conn.transaction():
            cur = conn.cursor()
            cur.execute(f"SET LOCAL ROLE {role}")
            cur.execute("SELECT set_config('app.user_id', %s, true)", (str(user_id) if user_id else "",))
            try:
                yield cur
            except psycopg.Error as e:
                raise map_db_error(e) from e


# ------------------------------------------------------------------ errors
class ApiError(Exception):
    def __init__(self, status: int, code: str, title: str, detail: str | None = None, errors: list | None = None):
        self.status, self.code, self.title, self.detail, self.errors = status, code, title, detail, errors or []


def problem_response(request: Request, e: ApiError) -> JSONResponse:
    body = {"type": f"https://docs.biz360x.example/errors/{e.code}", "title": e.title, "status": e.status,
            "code": e.code, "request_id": request.headers.get("x-request-id", str(uuid.uuid4()))}
    if e.detail:
        body["detail"] = e.detail
    if e.errors:
        body["errors"] = e.errors
    return JSONResponse(body, status_code=e.status, media_type="application/problem+json")


# Constraint name → stable API error code
CONSTRAINT_CODES = {
    "tax_split": (400, "tax_split_mismatch", "Tax split does not match the place of supply"),
    "b2b_has_gstin": (400, "b2b_needs_gstin", "A B2B invoice needs the buyer's GSTIN"),
    "notes_link_invoice": (400, "note_needs_original", "Credit and debit notes must reference the original invoice"),
    "cn_has_reason": (400, "validation_failed", "A credit note needs a reason"),
    "number_len": (400, "number_too_long", "Document numbers are limited to 16 characters"),
    "two_person_rule": (403, "two_person_rule", "You cannot decide a request you raised"),
    "share_allotments_check": (400, "allotment_late", "Shares must be allotted within 60 days of receiving the money"),
    "stock_not_negative": (409, "insufficient_stock", "Not enough stock"),
}


def map_db_error(e: psycopg.Error) -> ApiError:
    diag = getattr(e, "diag", None)
    cname = (diag.constraint_name if diag else None) or ""
    msg = (diag.message_primary if diag else str(e)) or str(e)
    if isinstance(e, psycopg.errors.CheckViolation):
        for key, (status, code, title) in CONSTRAINT_CODES.items():
            if key in cname:
                return ApiError(status, code, title, msg)
        return ApiError(400, "validation_failed", "Validation failed", msg)
    if isinstance(e, psycopg.errors.InsufficientPrivilege):
        return ApiError(404, "not_found", "Not found")          # RLS WITH CHECK: never reveal existence
    if isinstance(e, psycopg.errors.UniqueViolation):
        return ApiError(409, "conflict", "Already exists", msg)
    if isinstance(e, psycopg.errors.ExclusionViolation):
        return ApiError(409, "conflict", "Overlaps an existing record", msg)
    if isinstance(e, psycopg.errors.ForeignKeyViolation):
        return ApiError(400, "validation_failed", "Referenced record does not exist", msg)
    if isinstance(e, psycopg.errors.RaiseException):
        if "does not balance" in msg:
            return ApiError(409, "journal_unbalanced", "Journal does not balance", msg)
        if "locked" in msg:
            return ApiError(409, "period_locked", "The period is locked", msg)
        if msg.startswith("Settlements"):
            return ApiError(409, "over_settlement", "Allocation exceeds the invoice balance", msg)
        return ApiError(409, "conflict", "Conflict", msg)
    if isinstance(e, (psycopg.errors.InvalidTextRepresentation, psycopg.errors.DataException)):
        return ApiError(400, "validation_failed", "Invalid value", msg)
    logging.getLogger("biz360x").error("unmapped database error: %s: %s", type(e).__name__, msg)
    return ApiError(500, "internal", "Internal error")


# ------------------------------------------------------------------ auth
@dataclass
class Principal:
    user_id: uuid.UUID
    email: str
    api_key_id: uuid.UUID | None = None          # set when the caller is an API key, not a person


def issue_access_token(user_id: uuid.UUID, email: str) -> str:
    now = dt.datetime.now(dt.timezone.utc)
    return jwt.encode({"sub": str(user_id), "email": email, "iat": now, "exp": now + ACCESS_TTL, "typ": "access"},
                      JWT_SECRET, algorithm="HS256")


API_KEY_RE = re.compile(r"bzx_live_([0-9a-f]{8})_([A-Za-z0-9_-]{32,})")


def hash_api_key(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


def _api_key_principal(token: str) -> Principal:
    m = API_KEY_RE.fullmatch(token)
    bad = ApiError(401, "unauthorized", "Invalid API key")
    if not m:
        raise bad
    with tx(None, role="biz_worker") as cur:                      # the key is looked up before any identity exists
        cur.execute("""SELECT k.id, k.secret_hash, k.revoked_at, (k.expires_at IS NULL OR k.expires_at > now()) AS live, u.id AS uid, u.email
                         FROM api_keys k JOIN users u ON u.id = k.acts_as_user WHERE k.prefix = %s""", (m.group(1),))
        k = cur.fetchone()
        if not k or not hmac.compare_digest(hash_api_key(token), k["secret_hash"].strip()) or k["revoked_at"] or not k["live"]:
            raise bad
        cur.execute("UPDATE api_keys SET last_used_at = now() WHERE id = %s AND (last_used_at IS NULL OR last_used_at < now() - interval '1 minute')", (k["id"],))
    return Principal(k["uid"], k["email"], k["id"])


def current_principal(authorization: str = Header(default="")) -> Principal:
    if not authorization.startswith("Bearer "):
        raise ApiError(401, "unauthorized", "Missing bearer token")
    token = authorization[7:]
    if token.startswith("bzx_"):
        return _api_key_principal(token)
    try:
        claims = jwt.decode(token, JWT_SECRET, algorithms=["HS256"])
    except jwt.PyJWTError:
        raise ApiError(401, "unauthorized", "Invalid or expired token")
    if claims.get("typ") != "access":
        raise ApiError(401, "unauthorized", "Invalid or expired token")
    return Principal(uuid.UUID(claims["sub"]), claims.get("email", ""))


def is_production() -> bool:
    """Anything that is not explicitly development or test is treated as production."""
    return ENV not in ("dev", "test")


# ------------------------------------------------------------------ permissions
ROLE_ACTIONS = {
    "owner": {"read", "write", "approve", "file", "pay", "use"},
    "approver": {"read", "write", "approve", "file", "pay", "use"},
    "maker": {"read", "write", "use"},
    "viewer": {"read"},
}
ADVISOR_ACTIONS = {"read": {"read"}, "comment": {"read"}, "prepare": {"read", "write"}, "certify": {"read", "write", "file"}}


def require(cur, entity_id: uuid.UUID, permission: str) -> None:
    """404 when the entity is invisible, 403 when visible but not allowed."""
    module, action = permission.split(":")
    cur.execute("SELECT 1 FROM entities WHERE id = %s", (entity_id,))
    if cur.fetchone() is None:
        raise ApiError(404, "not_found", "Not found")
    cur.execute("SELECT entity_id, permissions FROM api_keys WHERE acts_as_user = app.current_user_id()")
    key = cur.fetchone()                                    # an API key acts as a service user and is limited to its list
    if key:
        if key["entity_id"] != entity_id:
            raise ApiError(404, "not_found", "Not found")
        if permission not in key["permissions"] and f"{module}:*" not in key["permissions"]:
            raise ApiError(403, "permission_denied", f"This API key lacks {permission}")
    cur.execute("""SELECT role::text, modules FROM memberships
                    WHERE entity_id = %s AND user_id = app.current_user_id() AND revoked_at IS NULL""", (entity_id,))
    m = cur.fetchone()
    if m and action in ROLE_ACTIONS[m["role"]] and ("*" in m["modules"] or module in m["modules"] or module in ("approvals", "tasks")):
        return
    cur.execute("""SELECT g.scopes FROM advisor_grants g JOIN org_members om ON om.organization_id = g.advisor_org_id
                    WHERE g.entity_id = %s AND om.user_id = app.current_user_id() AND g.status = 'active'""", (entity_id,))
    for g in cur.fetchall():
        level = g["scopes"].get(module)
        if level and action in ADVISOR_ACTIONS.get(level, set()):
            return
    raise ApiError(403, "permission_denied", f"Missing permission {permission}")


# ------------------------------------------------------------------ idempotency
def request_hash(body) -> str:
    return hashlib.sha256(json.dumps(body, sort_keys=True, default=str).encode()).hexdigest()


def idempotency_begin(cur, key: str | None, method: str, path: str, body) -> dict | None:
    """Returns the stored response for a replay, or None to proceed.
    Runs inside the business transaction, so a failed request stores nothing."""
    if not key:
        raise ApiError(400, "validation_failed", "Idempotency-Key header is required for this operation")
    h = request_hash(body)
    cur.execute("""INSERT INTO api_idempotency_keys (user_id, idem_key, method, path, request_hash)
                   VALUES (app.current_user_id(), %s, %s, %s, %s) ON CONFLICT DO NOTHING RETURNING idem_key""",
                (key, method, path, h))
    if cur.fetchone():
        return None
    cur.execute("SELECT * FROM api_idempotency_keys WHERE user_id = app.current_user_id() AND idem_key = %s", (key,))
    prev = cur.fetchone()
    if prev["request_hash"] != h or prev["path"] != path:
        raise ApiError(409, "idempotency_conflict", "Idempotency-Key was used for a different request")
    return {"status": prev["status_code"], "body": prev["response_body"]}


def idempotency_finish(cur, key: str, status: int, body) -> None:
    cur.execute("""UPDATE api_idempotency_keys SET status_code = %s, response_body = %s
                    WHERE user_id = app.current_user_id() AND idem_key = %s""", (status, json.dumps(body, default=jsonable), key))


# ------------------------------------------------------------------ audit + events (same transaction as the change)
def audit(cur, entity_id, action: str, subject: str, subject_id, after=None, before=None, actor_kind="user"):
    cur.execute("""INSERT INTO audit_log (entity_id, actor_kind, actor_user_id, action, subject, subject_id, before, after)
                   VALUES (%s, %s, app.current_user_id(), %s, %s, %s, %s, %s)""",
                (entity_id, actor_kind, action, subject, subject_id,
                 json.dumps(before, default=jsonable) if before else None, json.dumps(after, default=jsonable) if after else None))


def emit(cur, entity_id, type_: str, subject: str, subject_id, data=None):
    cur.execute("INSERT INTO event_outbox (entity_id, type, subject, subject_id, data) VALUES (%s, %s, %s, %s, %s)",
                (entity_id, type_, subject, subject_id, json.dumps(data or {}, default=jsonable)))


# ------------------------------------------------------------------ serialisation
def jsonable(v):
    if isinstance(v, Decimal):
        return f"{v:.2f}"
    if isinstance(v, (dt.date, dt.datetime)):
        return v.isoformat()
    if isinstance(v, uuid.UUID):
        return str(v)
    raise TypeError(type(v))


def money(v) -> str:
    return f"{Decimal(v):.2f}"


def etag_of(row: dict) -> str:
    return '"' + hashlib.sha1(json.dumps(row, sort_keys=True, default=jsonable).encode()).hexdigest()[:16] + '"'


Auth = Depends(current_principal)
