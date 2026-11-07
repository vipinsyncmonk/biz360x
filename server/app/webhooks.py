"""Webhook delivery: fan the event outbox out to endpoints, sign, send, retry with backoff for 72 hours.

Called by the worker loop (`relay`), never by a request. One `webhook_deliveries` row is one attempt: a failed attempt is
closed and the next one is inserted with a later `next_retry_at`, so the table is also the delivery log.

Signature (documented in the contract): `Biz360X-Signature: t=<unix>,v1=<hex HMAC-SHA256(secret, t + "." + body)>`.
"""
from __future__ import annotations

import hashlib
import hmac
import ipaddress
import json
import logging
import socket
import time
import urllib.error
import urllib.parse
import urllib.request

from . import secretbox
from .core import ApiError, is_production, jsonable

log = logging.getLogger("biz360x")
GIVE_UP_AFTER_HOURS = 72
BACKOFF_SECONDS = [60, 300, 1800, 7200, 21600]            # then every 6 hours until 72 h have passed
TIMEOUT = 10


def sign(secret: str, body: bytes, t: int | None = None) -> str:
    t = int(time.time()) if t is None else t
    mac = hmac.new(secret.encode(), f"{t}.".encode() + body, hashlib.sha256).hexdigest()
    return f"t={t},v1={mac}"


def verify(secret: str, header: str, body: bytes, tolerance: int = 300, now: float | None = None) -> bool:
    """What a receiver does. Used by the tests and offered to integrators as the reference."""
    try:
        parts = dict(p.split("=", 1) for p in header.split(","))
        t = int(parts["t"])
    except (ValueError, KeyError):
        return False
    if abs((time.time() if now is None else now) - t) > tolerance:
        return False
    return hmac.compare_digest(sign(secret, body, t).split("v1=")[1], parts.get("v1", ""))


def check_url(url: str) -> None:
    """Refuse targets that would let a customer make the server call its own network."""
    u = urllib.parse.urlparse(url)
    if u.scheme != "https" or not u.hostname:
        raise ApiError(400, "validation_failed", "A webhook URL must be https")
    if u.username or u.password:
        raise ApiError(400, "validation_failed", "A webhook URL cannot carry credentials")
    try:
        infos = socket.getaddrinfo(u.hostname, u.port or 443, proto=socket.IPPROTO_TCP)
    except socket.gaierror:
        if is_production():
            raise ApiError(400, "validation_failed", "The webhook host does not resolve")
        return                                                  # development and tests may use names that do not resolve
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast or ip.is_unspecified:
            raise ApiError(400, "validation_failed", "A webhook URL cannot point at a private or internal address")


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None                                             # a 3xx is a failed delivery, not a hop into another network


def http_send(url: str, headers: dict, body: bytes) -> int:
    check_url(url)                                              # again at send time: DNS can change after the endpoint was saved
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.build_opener(_NoRedirect).open(req, timeout=TIMEOUT) as r:
            return r.status
    except urllib.error.HTTPError as e:
        return e.code


SENDER = http_send                                              # tests replace this


def new_secret() -> str:
    import secrets
    return "whsec_" + secrets.token_urlsafe(32)


def envelope(ev: dict) -> bytes:
    return json.dumps({"id": str(ev["id"]), "type": ev["type"], "entity_id": ev["entity_id"], "occurred_at": ev["occurred_at"],
                       "subject": ev["subject"], "subject_id": ev["subject_id"], "data": ev["data"]}, default=jsonable, separators=(",", ":")).encode()


def fan_out(cur, batch: int = 500) -> int:
    """Create the first attempt for every (event, endpoint) pair, then mark the events relayed."""
    cur.execute("""SELECT id FROM event_outbox WHERE relayed_at IS NULL ORDER BY id LIMIT %s FOR UPDATE SKIP LOCKED""", (batch,))
    ids = [r["id"] for r in cur.fetchall()]
    if not ids:
        return 0
    cur.execute("""INSERT INTO webhook_deliveries (endpoint_id, event_id, attempt, next_retry_at)
                   SELECT w.id, e.id, 1, now() FROM event_outbox e JOIN webhook_endpoints w ON w.entity_id = e.entity_id
                    WHERE e.id = ANY(%s) AND w.is_active AND e.occurred_at >= w.created_at
                      AND (e.type = ANY(w.events) OR '*' = ANY(w.events))
                      AND (e.type <> 'webhook.test' OR w.id::text = e.data->>'endpoint_id')
                   ON CONFLICT DO NOTHING""", (ids,))
    n = cur.rowcount
    cur.execute("UPDATE event_outbox SET relayed_at = now() WHERE id = ANY(%s)", (ids,))
    return n


def deliver_due(cur, limit: int = 20) -> dict:
    cur.execute("""SELECT d.id, d.attempt, d.endpoint_id, d.event_id, w.url, w.secret_enc, w.is_active,
                          e.id AS eid, e.type, e.entity_id, e.subject, e.subject_id, e.data, e.occurred_at,
                          e.occurred_at < now() - make_interval(hours => %s) AS too_old
                     FROM webhook_deliveries d JOIN webhook_endpoints w ON w.id = d.endpoint_id JOIN event_outbox e ON e.id = d.event_id
                    WHERE d.attempted_at IS NULL AND d.next_retry_at <= now()
                    ORDER BY d.next_retry_at LIMIT %s FOR UPDATE OF d SKIP LOCKED""", (GIVE_UP_AFTER_HOURS, limit))
    rows = cur.fetchall()
    sent = failed = 0
    for d in rows:
        status, error = None, None
        if not d["is_active"]:
            cur.execute("UPDATE webhook_deliveries SET attempted_at = now(), next_retry_at = NULL, error = 'endpoint disabled' WHERE id = %s", (d["id"],))
            continue
        body = envelope({**d, "id": d["eid"]})                  # d["id"] is the delivery, the event id is what receivers deduplicate on
        headers = {"Content-Type": "application/json", "User-Agent": "Biz360X-Webhooks/1", "Biz360X-Event-Id": str(d["eid"]),
                   "Biz360X-Signature": sign(secretbox.decrypt(d["secret_enc"]), body)}
        try:
            status = SENDER(d["url"], headers, body)
        except Exception as ex:                                  # network error, timeout, refused target
            error = f"{type(ex).__name__}: {ex}"[:300]
        ok = status is not None and 200 <= status < 300
        cur.execute("""UPDATE webhook_deliveries SET attempted_at = now(), status_code = %s, error = %s, next_retry_at = NULL,
                              delivered_at = CASE WHEN %s THEN now() END WHERE id = %s""", (status, error, ok, d["id"]))
        if ok:
            sent += 1
            continue
        failed += 1
        if not d["too_old"]:
            wait = BACKOFF_SECONDS[min(d["attempt"] - 1, len(BACKOFF_SECONDS) - 1)] if d["attempt"] <= len(BACKOFF_SECONDS) else 21600
            cur.execute("""INSERT INTO webhook_deliveries (endpoint_id, event_id, attempt, next_retry_at)
                           VALUES (%s, %s, %s, now() + make_interval(secs => %s)) ON CONFLICT DO NOTHING""",
                        (d["endpoint_id"], d["event_id"], d["attempt"] + 1, wait))
        else:
            log.warning("webhook %s gave up on event %s after %s h", d["endpoint_id"], d["event_id"], GIVE_UP_AFTER_HOURS)
    return {"delivered": sent, "failed": failed}


def relay(cur) -> dict:
    return {"queued": fan_out(cur), **deliver_due(cur)}
