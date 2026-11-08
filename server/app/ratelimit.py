"""Request rate limits: 600/min per caller, 60/min per caller for portal-calling (202) actions, with IETF RateLimit headers.

The window store is in process memory, which is right for one server process and for tests. Several processes need a shared
store (Redis): implement `hit()` over it and assign `LIMITER`. Fixed one-minute windows keep the arithmetic simple and the
headers exact; a caller can burst twice the limit across a window edge, which is acceptable for protection against abuse.
"""
from __future__ import annotations

import os
import re
import threading
import time

import jwt

from .core import API_KEY_RE, JWT_SECRET

WINDOW = 60


def limits() -> dict[str, int]:
    return {"user": int(os.environ.get("BIZ_RATE_USER", "600")), "heavy": int(os.environ.get("BIZ_RATE_HEAVY", "60"))}


class MemoryLimiter:
    def __init__(self):
        self._lock = threading.Lock()
        self._windows: dict[str, tuple[int, int]] = {}          # key -> (window start, count)

    def hit(self, key: str, limit: int, now: float | None = None) -> tuple[bool, int, int]:
        """Count one request. Returns (allowed, remaining, seconds until the window resets)."""
        now = time.time() if now is None else now
        start = int(now // WINDOW) * WINDOW
        with self._lock:
            s, n = self._windows.get(key, (start, 0))
            if s != start:
                s, n = start, 0
            n += 1
            self._windows[key] = (s, n)
            if len(self._windows) > 50_000:                        # drop stale windows so memory stays bounded
                self._windows = {k: v for k, v in self._windows.items() if v[0] == start}
        return n <= limit, max(limit - n, 0), int(s + WINDOW - now) or 1

    def reset(self):
        with self._lock:
            self._windows.clear()


LIMITER = MemoryLimiter()
HEAVY_PATHS: list[re.Pattern] = []          # filled from the routes declared with status_code=202


def register_heavy_routes(routers, prefix: str = "") -> None:
    """Portal-calling actions are the POST routes that answer 202 (they create a job). Read from the routers, because the
    application's own route list wraps included routers in a framework-specific object."""
    HEAVY_PATHS.clear()
    for router in routers:
        for r in router.routes:
            if getattr(r, "status_code", None) == 202 and "POST" in getattr(r, "methods", ()):
                HEAVY_PATHS.append(re.compile("^" + prefix + re.sub(r"\{[^/}]+\}", "[^/]+", r.path) + "$"))       # route paths hold no regex specials


def caller_key(authorization: str, client_ip: str) -> str:
    """Who to count. Unauthenticated calls count against the IP; a bad token is counted as the IP too, so guessing is limited."""
    if authorization.startswith("Bearer "):
        token = authorization[7:]
        m = API_KEY_RE.fullmatch(token) if token.startswith("bzx_") else None
        if m:
            return "key:" + m.group(1)
        try:
            return "user:" + str(jwt.decode(token, JWT_SECRET, algorithms=["HS256"])["sub"])
        except Exception:
            pass
    return "ip:" + client_ip


def check(method: str, path: str, authorization: str, client_ip: str) -> tuple[bool, dict, int]:
    """Returns (allowed, headers, status). Headers go on every response; Retry-After is added when refused."""
    lim = limits()
    key = caller_key(authorization, client_ip)
    ok, remaining, reset = LIMITER.hit(key + ":all", lim["user"])
    headers = {"RateLimit": f"limit={lim['user']}, remaining={remaining}, reset={reset}"}
    if ok and method == "POST" and any(p.match(path) for p in HEAVY_PATHS):
        ok2, rem2, reset2 = LIMITER.hit(key + ":heavy", lim["heavy"])
        if not ok2 or rem2 < remaining:
            headers["RateLimit"] = f"limit={lim['heavy']}, remaining={rem2}, reset={reset2}"
        if not ok2:
            ok, reset = False, reset2
    if not ok:
        headers["Retry-After"] = str(reset)
    return ok, headers, 429
