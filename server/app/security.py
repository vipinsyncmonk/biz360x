"""Startup guard and response hardening.

`check_environment()` runs when the API server or the worker starts. Outside development it refuses to start with a
default secret, a missing field key, or any sandbox provider (OTP, identity, portal adapters). That is how the development
OTP `000000` is kept out of production: it works only when `auth.dev_code_enabled()`, which this guard makes impossible there.
"""
from __future__ import annotations

import os

from . import adapters, auth
from .core import ENV, JWT_SECRET, is_production

DEFAULT_JWT_SECRET = "dev-only-secret-change-me-in-every-environment"


def problems() -> list[str]:
    out: list[str] = []
    if ENV not in ("dev", "test", "staging", "production"):
        out.append(f"BIZ_ENV='{ENV}' is not one of dev, test, staging, production")
    if not is_production():
        return out
    if JWT_SECRET == DEFAULT_JWT_SECRET or len(JWT_SECRET) < 32:
        out.append("JWT_SECRET must be set to a random value of at least 32 characters")
    if not os.environ.get("BIZ_FIELD_KEY"):
        out.append("BIZ_FIELD_KEY must be set (a Fernet key from the secret manager)")
    if auth.otp_provider_name() == "sandbox":
        out.append("BIZ_OTP_PROVIDER is 'sandbox': the development code 000000 would be accepted. Choose a real provider")
    try:
        sandboxes = [f for f, n in adapters.status().items() if n.startswith("Sandbox")]
    except RuntimeError as e:                    # the adapter registry already refuses a sandbox here
        out.append(str(e))
        sandboxes = []
    if sandboxes:
        out.append("Sandbox portal adapters selected for: " + ", ".join(sandboxes))
    if os.environ.get("BIZ_RATE_USER") == "0" or os.environ.get("BIZ_RATE_HEAVY") == "0":
        out.append("Rate limits must not be zero")
    return out


def check_environment() -> None:
    p = problems()
    if p:
        raise RuntimeError("Refusing to start:\n  - " + "\n  - ".join(p))


SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "Cache-Control": "no-store",
    "Referrer-Policy": "no-referrer",
    "Strict-Transport-Security": "max-age=63072000; includeSubDomains",
    "Content-Security-Policy": "default-src 'none'; frame-ancestors 'none'",
}
