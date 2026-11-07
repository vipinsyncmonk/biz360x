"""Sign-in: one-time codes, refresh-token rotation, TOTP, and the OIDC/SAML provider interface.

The auth service reads and writes users and tokens as `biz_worker`: there is no identity yet, so row-level security cannot
apply. Nothing here is reachable by a request handler running as `biz_app`.

Providers (`OtpSender`, `IdentityProvider`) are chosen by BIZ_OTP_PROVIDER / BIZ_IDP_PROVIDER. The sandbox ones exist for
development and are refused by `security.check_environment()` outside it.
"""
from __future__ import annotations

import base64
import datetime as dt
import hashlib
import hmac
import os
import secrets
import struct
import time
import uuid
from typing import Protocol

from . import secretbox
from .core import JWT_SECRET, ApiError, is_production, issue_access_token, tx

OTP_TTL = dt.timedelta(minutes=5)
OTP_MAX_ATTEMPTS = 5
OTP_PER_HOUR = 5
REFRESH_TTL = dt.timedelta(days=30)
DEV_CODE = "000000"


# ------------------------------------------------------------------ providers
class OtpSender(Protocol):
    def send(self, channel: str, destination: str, code: str) -> None: ...


class IdentityProvider(Protocol):
    name: str

    def exchange(self, code: str, redirect_uri: str | None) -> dict:
        """Return {"email", "full_name", "subject"} for a verified identity, or raise ApiError(401)."""


class SandboxOtpSender:
    """Keeps the codes in memory so a test (or a developer) can read what a person would have received."""
    sent: list[tuple[str, str, str]] = []

    def send(self, channel, destination, code):
        self.sent.append((channel, destination, code))

    @classmethod
    def last(cls, destination: str) -> str | None:
        for _, d, c in reversed(cls.sent):
            if d == destination:
                return c
        return None


class SandboxIdp:
    """Development identity provider: the code is `sandbox:<email>`. It proves nothing and must never run in production."""
    name = "sandbox"

    def exchange(self, code, redirect_uri):
        if not code.startswith("sandbox:"):
            raise ApiError(401, "unauthorized", "Invalid code")
        email = code.split(":", 1)[1].strip().lower()
        return {"email": email, "full_name": email.split("@")[0], "subject": "sandbox|" + email}


OTP_SENDERS: dict[str, type] = {"sandbox": SandboxOtpSender}
IDP_PROVIDERS: dict[str, type] = {"sandbox": SandboxIdp}
_active: dict[str, object] = {}


def register_otp_sender(name: str, cls: type) -> None:
    OTP_SENDERS[name] = cls


def register_idp(name: str, cls: type) -> None:
    IDP_PROVIDERS[name] = cls


def otp_provider_name() -> str:
    return os.environ.get("BIZ_OTP_PROVIDER", "sandbox")


def idp_provider_name() -> str:
    return os.environ.get("BIZ_IDP_PROVIDER", "sandbox")


def otp_sender() -> OtpSender:
    if "otp" not in _active:
        name = otp_provider_name()
        if name not in OTP_SENDERS:
            raise RuntimeError(f"BIZ_OTP_PROVIDER selects '{name}', but only {sorted(OTP_SENDERS)} are installed")
        _active["otp"] = OTP_SENDERS[name]()
    return _active["otp"]


def idp() -> IdentityProvider:
    if "idp" not in _active:
        name = idp_provider_name()
        if name not in IDP_PROVIDERS:
            raise RuntimeError(f"BIZ_IDP_PROVIDER selects '{name}', but only {sorted(IDP_PROVIDERS)} are installed")
        _active["idp"] = IDP_PROVIDERS[name]()
    return _active["idp"]


def reset() -> None:
    _active.clear()


def dev_code_enabled() -> bool:
    """The fixed development code 000000 works only in development with the sandbox sender. security.check_environment()
    refuses to start the server in any other combination, so this can never be true in production."""
    return not is_production() and otp_provider_name() == "sandbox"


# ------------------------------------------------------------------ one-time codes
def _norm(identifier: str) -> str:
    return identifier.strip().lower()


def _code_hash(identifier: str, code: str) -> str:
    return hmac.new(JWT_SECRET.encode(), f"{identifier}|{code}".encode(), hashlib.sha256).hexdigest()


def request_code(email: str | None, mobile: str | None) -> None:
    """Send a code if the person exists. The response never says whether they do, and the hourly limit counts requests for
    unknown identifiers too, so the limit cannot be used to find out who has an account."""
    ident = _norm(email or mobile or "")
    if not ident:
        raise ApiError(400, "validation_failed", "email or mobile_e164 is required")
    with tx(None, role="biz_worker") as cur:
        cur.execute("SELECT count(*) AS n FROM auth_otps WHERE identifier = %s AND created_at > now() - interval '1 hour'", (ident,))
        if cur.fetchone()["n"] >= OTP_PER_HOUR:
            raise ApiError(429, "rate_limited", "Too many codes requested", "At most 5 codes per hour for one identifier.")
        code = f"{secrets.randbelow(10 ** 6):06d}"
        cur.execute("INSERT INTO auth_otps (identifier, code_hash, expires_at) VALUES (%s, %s, now() + %s)", (ident, _code_hash(ident, code), OTP_TTL))
        cur.execute("SELECT 1 FROM users WHERE auth_provider <> 'service' AND (email = %s OR mobile_e164 = %s)", (ident, ident))
        exists = cur.fetchone() is not None
    if exists:                                          # sent after the commit; a send failure must not reveal anything either
        try:
            otp_sender().send("email" if email else "sms", ident, code)
        except Exception:
            pass


def _verify_code(cur, ident: str, code: str) -> bool:
    if dev_code_enabled() and hmac.compare_digest(code, DEV_CODE):
        return True
    cur.execute("""SELECT id, code_hash, attempts FROM auth_otps WHERE identifier = %s AND consumed_at IS NULL AND expires_at > now()
                    ORDER BY created_at DESC LIMIT 1 FOR UPDATE""", (ident,))
    row = cur.fetchone()
    if not row or row["attempts"] >= OTP_MAX_ATTEMPTS:
        return False
    cur.execute("UPDATE auth_otps SET attempts = attempts + 1 WHERE id = %s", (row["id"],))
    if not hmac.compare_digest(_code_hash(ident, code), row["code_hash"].strip()):
        return False
    cur.execute("UPDATE auth_otps SET consumed_at = now() WHERE id = %s", (row["id"],))
    return True


# ------------------------------------------------------------------ TOTP (RFC 6238), for users with mfa_enabled
def totp(secret_b32: str, at: float | None = None, step: int = 30, digits: int = 6) -> str:
    key = base64.b32decode(secret_b32.upper() + "=" * (-len(secret_b32) % 8))
    counter = int((time.time() if at is None else at) // step)
    h = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    o = h[-1] & 0x0F
    return f"{(struct.unpack('>I', h[o:o + 4])[0] & 0x7FFFFFFF) % 10 ** digits:0{digits}d}"


def _mfa_ok(user: dict, mfa_code: str | None) -> bool:
    if not user["mfa_enabled"]:
        return True
    if not user["totp_secret_enc"] or not mfa_code:        # enabled but not enrolled, or no code: fail closed
        return False
    secret = secretbox.decrypt(user["totp_secret_enc"])
    now = time.time()
    return any(hmac.compare_digest(totp(secret, now + d * 30), mfa_code) for d in (-1, 0, 1))


# ------------------------------------------------------------------ tokens
def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _new_refresh(cur, user_id, family_id) -> str:
    token = "rt_" + secrets.token_urlsafe(40)
    cur.execute("INSERT INTO refresh_tokens (family_id, user_id, token_hash, expires_at) VALUES (%s, %s, %s, now() + %s)",
                (family_id, user_id, _hash_token(token), REFRESH_TTL))
    return token


def _tokens(cur, user: dict, family_id=None) -> dict:
    cur.execute("UPDATE users SET last_login_at = now() WHERE id = %s", (user["id"],))
    return {"access_token": issue_access_token(user["id"], user["email"]), "refresh_token": _new_refresh(cur, user["id"], family_id or uuid.uuid4()),
            "token_type": "Bearer", "expires_in": 900}


def login_with_code(email: str | None, mobile: str | None, code: str | None, mfa_code: str | None) -> dict:
    ident = _norm(email or mobile or "")
    if not ident or not code:
        raise ApiError(400, "validation_failed", "email (or mobile_e164) and otp are required")
    err, result = None, None
    with tx(None, role="biz_worker") as cur:               # an error raised inside would roll back the attempt counter, so it is raised after
        cur.execute("SELECT id, email, mfa_enabled, totp_secret_enc FROM users WHERE auth_provider <> 'service' AND (email = %s OR mobile_e164 = %s)", (ident, ident))
        user = cur.fetchone()
        valid = _verify_code(cur, ident, code)             # a real code is consumed even when the person is unknown: same answer either way
        if not (valid and user):
            err = ApiError(401, "unauthorized", "Invalid code")
        elif not _mfa_ok(user, mfa_code):
            err = ApiError(401, "mfa_required", "A valid authenticator code is required")
        else:
            result = _tokens(cur, user)
    if err:
        raise err
    return result


def login_with_idp(code: str | None, redirect_uri: str | None) -> dict:
    if not code:
        raise ApiError(400, "validation_failed", "code is required")
    if is_production() and idp_provider_name() == "sandbox":   # the sandbox accepts any email: it is switched off, not merely unconfigured
        raise ApiError(400, "validation_failed", "Single sign-on is not configured")
    identity = idp().exchange(code, redirect_uri)
    with tx(None, role="biz_worker") as cur:
        cur.execute("SELECT id, email FROM users WHERE email = %s AND auth_provider <> 'service'", (identity["email"].lower(),))
        user = cur.fetchone()
        if not user:                                       # an identity provider proves who someone is, not that they have a workspace
            raise ApiError(401, "unauthorized", "No account for this identity")
        return _tokens(cur, user)


def refresh(token: str | None) -> dict:
    if not token:
        raise ApiError(400, "validation_failed", "refresh_token is required")
    with tx(None, role="biz_worker") as cur:
        cur.execute("SELECT * FROM refresh_tokens WHERE token_hash = %s FOR UPDATE", (_hash_token(token),))
        row = cur.fetchone()
        if row:
            if row["used_at"] or row["revoked_at"]:        # a used token came back: someone copied it, so end the whole session family
                cur.execute("UPDATE refresh_tokens SET revoked_at = coalesce(revoked_at, now()) WHERE family_id = %s", (row["family_id"],))
            elif row["expires_at"] > dt.datetime.now(dt.timezone.utc):
                cur.execute("UPDATE refresh_tokens SET used_at = now() WHERE id = %s", (row["id"],))
                cur.execute("SELECT id, email FROM users WHERE id = %s AND auth_provider <> 'service'", (row["user_id"],))
                user = cur.fetchone()
                if user:
                    return _tokens(cur, user, row["family_id"])
    raise ApiError(401, "unauthorized", "Invalid refresh token")      # raised after the block, so the family revocation above is committed


def logout(token: str | None) -> None:
    if not token:
        return
    with tx(None, role="biz_worker") as cur:
        cur.execute("""UPDATE refresh_tokens SET revoked_at = coalesce(revoked_at, now())
                        WHERE family_id IN (SELECT family_id FROM refresh_tokens WHERE token_hash = %s)""", (_hash_token(token),))


def purge_expired(cur) -> int:
    cur.execute("DELETE FROM auth_otps WHERE created_at < now() - interval '1 day'")
    n = cur.rowcount
    cur.execute("DELETE FROM refresh_tokens WHERE expires_at < now() - interval '7 days'")
    return n + cur.rowcount
