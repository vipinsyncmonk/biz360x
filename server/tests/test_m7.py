"""M7: sign-in (codes, refresh rotation, MFA, SSO), API keys, webhooks, rate limits, audit verification, the startup guard,
and database-level security checks. Own entity (EX); tests run in order and build on one another."""
import datetime as dt
import json
import os
import time
import uuid

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.rows import dict_row

from app import adapters, auth, core, ratelimit, secretbox, security, webhooks, worker

DB = os.environ["DATABASE_URL"]
ORG = "00000000-0000-0000-0000-0000000000a1"
EX, EX2 = "00000000-0000-0000-0000-0000000000fa", "00000000-0000-0000-0000-0000000000fb"
U_OWNER, U_VIEWER, U_OTHER = ("00000000-0000-0000-0000-00000000030%d" % i for i in (1, 2, 3))
OWNER, VIEWER, OTHER = "m7-owner@example.in", "m7-viewer@example.in", "m7-other@example.in"
S: dict = {}


@pytest.fixture(scope="module")
def db():
    with psycopg.connect(DB, autocommit=True, row_factory=dict_row) as c:
        yield c


@pytest.fixture(scope="module")
def client():
    from app.main import app
    return TestClient(app)


@pytest.fixture(scope="module", autouse=True)
def seed(db):
    db.execute("DELETE FROM auth_otps WHERE identifier IN (%s, %s, %s, %s, '+919800000301')", (OWNER, VIEWER, OTHER, "nobody-m7@example.in"))
    for e, n in ((EX, "M7 Test Pvt Ltd"), (EX2, "M7 Other Pvt Ltd")):
        db.execute("INSERT INTO entities (id, organization_id, legal_name, constitution, track, pan, registered_state) VALUES (%s, %s, %s, 'private_limited', 'business', 'AAAPX7777Q', '29')", (e, ORG, n))
    db.execute("INSERT INTO users (id, email, full_name, mobile_e164) VALUES (%s, %s, 'M7 Owner', '+919800000301'), (%s, %s, 'M7 Viewer', NULL), (%s, %s, 'M7 Other', NULL)",
               (U_OWNER, OWNER, U_VIEWER, VIEWER, U_OTHER, OTHER))
    db.execute("INSERT INTO memberships (entity_id, user_id, role) VALUES (%s, %s, 'owner'), (%s, %s, 'viewer'), (%s, %s, 'owner')", (EX, U_OWNER, EX, U_VIEWER, EX2, U_OTHER))
    db.execute("INSERT INTO ledger_accounts (entity_id, code, name, type) VALUES (%s, '9001', 'M7 account', 'asset')", (EX,))
    yield
    db.execute("UPDATE webhook_endpoints SET is_active = false WHERE entity_id IN (%s, %s)", (EX, EX2))


def code_login(client, email):
    """Sign in with the development code, so tests that only need a session do not use up the five real codes an hour."""
    return client.post("/v1/auth/token", json={"grant_type": "otp", "email": email, "otp": "000000"})


def bearer(r):
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


# ------------------------------------------------------------------ one-time codes
def test_a_real_code_signs_in_once(client, db):
    db.execute("DELETE FROM auth_otps WHERE identifier = %s", (OWNER,))
    client.post("/v1/auth/otp", json={"email": OWNER})
    code = auth.SandboxOtpSender.last(OWNER)
    assert code and len(code) == 6
    row = db.execute("SELECT code_hash FROM auth_otps WHERE identifier = %s", (OWNER,)).fetchone()
    assert code not in row["code_hash"]                                   # only a keyed hash is stored
    r = client.post("/v1/auth/token", json={"grant_type": "otp", "email": OWNER, "otp": code})
    assert r.status_code == 200 and {"access_token", "refresh_token"} <= set(r.json())
    S["owner"] = r.json()
    again = client.post("/v1/auth/token", json={"grant_type": "otp", "email": OWNER, "otp": code})
    assert again.status_code == 401                                       # consumed


def test_wrong_codes_burn_the_code_after_five_attempts(client, db):
    db.execute("DELETE FROM auth_otps WHERE identifier = %s", (OTHER,))
    client.post("/v1/auth/otp", json={"email": OTHER})
    good = auth.SandboxOtpSender.last(OTHER)
    bad = "000001" if good != "000001" else "000002"
    for _ in range(5):
        assert client.post("/v1/auth/token", json={"grant_type": "otp", "email": OTHER, "otp": bad}).status_code == 401
    assert client.post("/v1/auth/token", json={"grant_type": "otp", "email": OTHER, "otp": good}).status_code == 401   # too late: the attempts persisted


def test_five_codes_an_hour_and_no_enumeration(client, db):
    ghost = "nobody-m7@example.in"
    db.execute("DELETE FROM auth_otps WHERE identifier = %s", (ghost,))
    codes = [client.post("/v1/auth/otp", json={"email": ghost}).status_code for _ in range(6)]
    assert codes == [204] * 5 + [429]                                     # an unknown address behaves exactly like a known one
    assert auth.SandboxOtpSender.last(ghost) is None                      # and nothing was sent
    r = client.post("/v1/auth/otp", json={"email": ghost})
    assert r.json()["code"] == "rate_limited"


def test_sign_in_by_mobile(client, db):
    db.execute("DELETE FROM auth_otps WHERE identifier = '+919800000301'")
    assert client.post("/v1/auth/otp", json={"mobile_e164": "+919800000301"}).status_code == 204
    code = auth.SandboxOtpSender.last("+919800000301")
    r = client.post("/v1/auth/token", json={"grant_type": "otp", "mobile_e164": "+919800000301", "otp": code})
    assert r.status_code == 200


def test_the_development_code_works_only_in_development(client, monkeypatch):
    assert auth.dev_code_enabled()
    assert client.post("/v1/auth/token", json={"grant_type": "otp", "email": OWNER, "otp": "000000"}).status_code == 200
    monkeypatch.setattr(core, "ENV", "production")
    assert not auth.dev_code_enabled()
    assert client.post("/v1/auth/token", json={"grant_type": "otp", "email": OWNER, "otp": "000000"}).status_code == 401
    monkeypatch.setattr(core, "ENV", "dev")
    monkeypatch.setenv("BIZ_OTP_PROVIDER", "acme")                          # a real provider in development also turns the shortcut off
    assert not auth.dev_code_enabled()
    monkeypatch.delenv("BIZ_OTP_PROVIDER")
    assert auth.dev_code_enabled()


# ------------------------------------------------------------------ refresh tokens
def test_refresh_rotates_and_reuse_ends_the_session(client, db):
    first = code_login(client, OWNER).json()
    second = client.post("/v1/auth/token", json={"grant_type": "refresh_token", "refresh_token": first["refresh_token"]})
    assert second.status_code == 200 and second.json()["refresh_token"] != first["refresh_token"]
    third = client.post("/v1/auth/token", json={"grant_type": "refresh_token", "refresh_token": second.json()["refresh_token"]})
    assert third.status_code == 200
    # the first token comes back: it was already used, so someone has a copy; the whole family dies, newest token included
    replay = client.post("/v1/auth/token", json={"grant_type": "refresh_token", "refresh_token": first["refresh_token"]})
    assert replay.status_code == 401
    dead = client.post("/v1/auth/token", json={"grant_type": "refresh_token", "refresh_token": third.json()["refresh_token"]})
    assert dead.status_code == 401
    assert client.post("/v1/auth/token", json={"grant_type": "refresh_token", "refresh_token": "rt_nope"}).status_code == 401
    assert db.execute("SELECT count(*) AS n FROM refresh_tokens WHERE token_hash = %s", (first["refresh_token"],)).fetchone()["n"] == 0   # stored hashed


def test_logout_revokes_the_family_and_expired_tokens_fail(client, db):
    t = code_login(client, OWNER).json()
    assert client.post("/v1/auth/logout", headers={"Authorization": f"Bearer {t['access_token']}"}, json={"refresh_token": t["refresh_token"]}).status_code == 204
    assert client.post("/v1/auth/token", json={"grant_type": "refresh_token", "refresh_token": t["refresh_token"]}).status_code == 401
    t2 = code_login(client, OWNER).json()
    db.execute("UPDATE refresh_tokens SET expires_at = now() - interval '1 minute' WHERE token_hash = encode(sha256(%s::bytea), 'hex')", (t2["refresh_token"].encode(),))
    assert client.post("/v1/auth/token", json={"grant_type": "refresh_token", "refresh_token": t2["refresh_token"]}).status_code == 401


def test_a_refresh_token_is_not_an_access_token(client):
    t = code_login(client, OWNER).json()
    assert client.get("/v1/me", headers={"Authorization": f"Bearer {t['refresh_token']}"}).status_code == 401


# ------------------------------------------------------------------ MFA and SSO
def test_mfa_requires_a_valid_authenticator_code(client, db):
    assert auth.totp("GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ", at=59) == "287082"                   # RFC 6238 test vector (secret "12345678901234567890", 6 digits)
    db.execute("UPDATE users SET mfa_enabled = true, totp_secret_enc = NULL WHERE id = %s", (U_VIEWER,))
    r = code_login(client, VIEWER)
    assert r.status_code == 401 and r.json()["code"] == "mfa_required"        # enabled but not enrolled: fail closed
    secret = "JBSWY3DPEHPK3PXP"
    db.execute("UPDATE users SET totp_secret_enc = %s WHERE id = %s", (secretbox.encrypt(secret), U_VIEWER))
    client.post("/v1/auth/otp", json={"email": VIEWER})
    code = auth.SandboxOtpSender.last(VIEWER)
    no = client.post("/v1/auth/token", json={"grant_type": "otp", "email": VIEWER, "otp": code})
    assert no.status_code == 401 and no.json()["code"] == "mfa_required"
    client.post("/v1/auth/otp", json={"email": VIEWER})
    code = auth.SandboxOtpSender.last(VIEWER)
    wrong = client.post("/v1/auth/token", json={"grant_type": "otp", "email": VIEWER, "otp": code, "mfa_code": "123456"})
    assert wrong.status_code == 401
    client.post("/v1/auth/otp", json={"email": VIEWER})
    code = auth.SandboxOtpSender.last(VIEWER)
    ok = client.post("/v1/auth/token", json={"grant_type": "otp", "email": VIEWER, "otp": code, "mfa_code": auth.totp(secret)})
    assert ok.status_code == 200
    db.execute("UPDATE users SET mfa_enabled = false WHERE id = %s", (U_VIEWER,))


def test_single_sign_on(client, monkeypatch):
    r = client.post("/v1/auth/token", json={"grant_type": "authorization_code", "code": f"sandbox:{OWNER}"})
    assert r.status_code == 200
    assert client.post("/v1/auth/token", json={"grant_type": "authorization_code", "code": "sandbox:stranger-m7@example.in"}).status_code == 401   # no workspace
    assert client.post("/v1/auth/token", json={"grant_type": "authorization_code", "code": "garbage"}).status_code == 401
    monkeypatch.setattr(core, "ENV", "production")                          # the sandbox identity provider accepts any email: off in production
    off = client.post("/v1/auth/token", json={"grant_type": "authorization_code", "code": f"sandbox:{OWNER}"})
    assert off.status_code == 400
    assert client.post("/v1/auth/token", json={"grant_type": "password", "email": OWNER}).status_code == 400


# ------------------------------------------------------------------ API keys
def test_creating_a_key_shows_it_once(client, db):
    h = bearer(code_login(client, OWNER))
    S["h"] = h
    r = client.post(f"/v1/entities/{EX}/api-keys", headers=h, json={"name": "ERP sync", "permissions": ["finance:read"]})
    assert r.status_code == 201, r.text
    key = r.json()["key"]
    S["key"], S["key_id"] = key, r.json()["id"]
    assert key.startswith("bzx_live_") and r.json()["prefix"] == key[:17]
    stored = db.execute("SELECT secret_hash FROM api_keys WHERE id = %s", (S["key_id"],)).fetchone()["secret_hash"]
    assert stored == core.hash_api_key(key) and key not in stored
    listed = client.get(f"/v1/entities/{EX}/api-keys", headers=h).json()["data"]
    assert [k["name"] for k in listed] == ["ERP sync"] and "key" not in listed[0] and "secret_hash" not in listed[0]
    assert db.execute("SELECT auth_provider FROM users u JOIN api_keys k ON k.acts_as_user = u.id WHERE k.id = %s", (S["key_id"],)).fetchone()["auth_provider"] == "service"


def test_a_key_does_what_its_list_allows_and_no_more(client):
    k = {"Authorization": f"Bearer {S['key']}"}
    r = client.get(f"/v1/entities/{EX}/accounts", headers=k)
    assert r.status_code == 200 and [a["code"] for a in r.json()["data"]] == ["9001"]
    w = client.post(f"/v1/entities/{EX}/accounts", headers=k, json={"code": "9002", "name": "x", "type": "asset"})
    assert w.status_code == 403 and w.json()["code"] == "permission_denied"           # finance:write was not granted
    assert client.get(f"/v1/entities/{EX}/journals", headers=k).status_code == 200  # same module, read
    assert client.get(f"/v1/entities/{EX}/webhooks", headers=k).status_code == 403    # settings:read was not granted
    assert client.get(f"/v1/entities/{EX2}/accounts", headers=k).status_code == 404   # another entity is invisible, not forbidden
    assert client.get("/v1/me", headers=k).json()["entities"][0]["id"] == EX


def test_a_key_cannot_create_keys_or_sign_in(client, db):
    k = {"Authorization": f"Bearer {S['key']}"}
    r = client.post(f"/v1/entities/{EX}/api-keys", headers=k, json={"name": "x", "permissions": ["finance:read"]})
    assert r.status_code == 403
    svc = db.execute("SELECT u.email FROM users u JOIN api_keys k ON k.acts_as_user = u.id WHERE k.id = %s", (S["key_id"],)).fetchone()["email"]
    assert client.post("/v1/auth/token", json={"grant_type": "otp", "email": svc, "otp": "000000"}).status_code == 401
    assert client.post("/v1/auth/token", json={"grant_type": "authorization_code", "code": f"sandbox:{svc}"}).status_code == 401


def test_key_permissions_are_validated(client):
    h = S["h"]
    for perms in (["finance:approve"], ["finance:pay"], ["finance:file"], ["finance:*"], ["settings:write"], ["approvals:read"], ["nonsense"], []):
        r = client.post(f"/v1/entities/{EX}/api-keys", headers=h, json={"name": "bad", "permissions": perms})
        assert r.status_code == 400, perms
    past = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=1)).isoformat()
    assert client.post(f"/v1/entities/{EX}/api-keys", headers=h, json={"name": "bad", "permissions": ["finance:read"], "expires_at": past}).status_code == 400


def test_only_someone_with_settings_write_manages_keys(client, db):
    hv = bearer(code_login(client, VIEWER))
    assert client.post(f"/v1/entities/{EX}/api-keys", headers=hv, json={"name": "x", "permissions": ["finance:read"]}).status_code == 403
    assert client.get(f"/v1/entities/{EX}/api-keys", headers=hv).status_code == 200
    ho = bearer(code_login(client, OTHER))
    assert client.get(f"/v1/entities/{EX}/api-keys", headers=ho).status_code == 404       # not their entity
    assert client.delete(f"/v1/entities/{EX}/api-keys/{S['key_id']}", headers=ho).status_code == 404


def test_expired_unknown_and_revoked_keys_are_refused(client, db):
    h = S["h"]
    soon = (dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=1)).isoformat()
    r = client.post(f"/v1/entities/{EX}/api-keys", headers=h, json={"name": "short", "permissions": ["finance:read"], "expires_at": soon})
    k2 = {"Authorization": f"Bearer {r.json()['key']}"}
    assert client.get(f"/v1/entities/{EX}/accounts", headers=k2).status_code == 200
    db.execute("UPDATE api_keys SET expires_at = now() - interval '1 second' WHERE id = %s", (r.json()["id"],))
    assert client.get(f"/v1/entities/{EX}/accounts", headers=k2).status_code == 401
    tampered = S["key"][:-3] + ("aaa" if not S["key"].endswith("aaa") else "bbb")
    assert client.get(f"/v1/entities/{EX}/accounts", headers={"Authorization": f"Bearer {tampered}"}).status_code == 401
    assert client.get(f"/v1/entities/{EX}/accounts", headers={"Authorization": "Bearer bzx_live_00000000_" + "a" * 40}).status_code == 401
    assert client.get(f"/v1/entities/{EX}/accounts", headers={"Authorization": "Bearer bzx_nonsense"}).status_code == 401
    assert client.delete(f"/v1/entities/{EX}/api-keys/{S['key_id']}", headers=h).status_code == 204
    assert client.get(f"/v1/entities/{EX}/accounts", headers={"Authorization": f"Bearer {S['key']}"}).status_code == 401
    listed = {k["name"]: k for k in client.get(f"/v1/entities/{EX}/api-keys", headers=h).json()["data"]}
    assert listed["ERP sync"]["revoked_at"] is not None and listed["ERP sync"]["last_used_at"] is not None
    acts = [a["action"] for a in client.get(f"/v1/entities/{EX}/audit-log?subject=api_keys", headers=h).json()["data"]]
    assert "api_key.created" in acts and "api_key.revoked" in acts


# ------------------------------------------------------------------ webhooks
class FakeReceiver:
    def __init__(self, statuses=None):
        self.calls, self.statuses = [], list(statuses or [])

    def __call__(self, url, headers, body):
        self.calls.append((url, headers, body))
        s = self.statuses.pop(0) if self.statuses else 200
        if isinstance(s, Exception):
            raise s
        return s


def relay(db=None):
    with core.tx(None, role="biz_worker") as cur:
        return webhooks.relay(cur)


def test_webhook_urls_are_checked(client):
    h = S["h"]
    for url in ("http://example.com/hook", "https://127.0.0.1/hook", "https://10.0.0.5/x", "https://169.254.169.254/latest", "https://user:pw@example.com/x", "ftp://example.com"):
        r = client.post(f"/v1/entities/{EX}/webhooks", headers=h, json={"url": url, "events": ["job.succeeded"]})
        assert r.status_code == 400, url
    assert client.post(f"/v1/entities/{EX}/webhooks", headers=h, json={"url": "https://hooks.example.com/x", "events": ["Not An Event"]}).status_code == 400


def test_a_webhook_is_signed_and_delivered(client, db, monkeypatch):
    h = S["h"]
    relay()                                                                # clear the outbox so only new events matter
    r = client.post(f"/v1/entities/{EX}/webhooks", headers=h, json={"url": "https://hooks.example.com/biz", "events": ["webhook.test", "api_key.created"]})
    assert r.status_code == 201
    secret, hook = r.json()["secret"], r.json()["id"]
    S["hook"], S["secret"] = hook, secret
    assert "secret" not in client.get(f"/v1/entities/{EX}/webhooks/{hook}", headers=h).json()
    assert db.execute("SELECT secret_enc FROM webhook_endpoints WHERE id = %s", (hook,)).fetchone()["secret_enc"] != secret.encode()
    recv = FakeReceiver()
    monkeypatch.setattr(webhooks, "SENDER", recv)
    assert client.post(f"/v1/entities/{EX}/webhooks/{hook}/test", headers=h).status_code == 204
    assert relay()["delivered"] == 1
    url, headers, body = recv.calls[0]
    assert url == "https://hooks.example.com/biz" and headers["Biz360X-Event-Id"]
    assert webhooks.verify(secret, headers["Biz360X-Signature"], body)
    assert not webhooks.verify(secret, headers["Biz360X-Signature"], body + b" ")                    # body changed
    assert not webhooks.verify("whsec_other", headers["Biz360X-Signature"], body)
    t = int(headers["Biz360X-Signature"].split(",")[0][2:])
    assert not webhooks.verify(secret, headers["Biz360X-Signature"], body, now=t + 301)             # replay window
    ev = json.loads(body)
    assert ev["type"] == "webhook.test" and ev["entity_id"] == EX and ev["id"] == headers["Biz360X-Event-Id"]
    d = client.get(f"/v1/entities/{EX}/webhooks/{hook}/deliveries", headers=h).json()["data"]
    assert d[0]["status_code"] == 200 and d[0]["delivered_at"] and d[0]["attempt"] == 1
    assert relay()["delivered"] == 0                                                                 # not sent twice


def test_events_go_only_to_matching_endpoints_of_their_own_entity(client, db, monkeypatch):
    other = bearer(code_login(client, OTHER))
    r = client.post(f"/v1/entities/{EX2}/webhooks", headers=other, json={"url": "https://other.example.com/x", "events": ["*"]})
    assert r.status_code == 201
    recv = FakeReceiver()
    monkeypatch.setattr(webhooks, "SENDER", recv)
    k = client.post(f"/v1/entities/{EX}/api-keys", headers=S["h"], json={"name": "evt", "permissions": ["finance:read"]})   # emits api_key.created for EX only
    assert k.status_code == 201
    relay()
    assert [c[0] for c in recv.calls] == ["https://hooks.example.com/biz"]                         # EX's hook, not EX2's catch-all
    client.post(f"/v1/entities/{EX}/webhooks/{S['hook']}/test", headers=S["h"])                    # a test event never reaches another endpoint
    recv.calls.clear()
    relay()
    assert len(recv.calls) == 1


def test_failures_retry_with_backoff_and_stop_after_72_hours(client, db, monkeypatch):
    recv = FakeReceiver([500, ConnectionError("refused")])
    monkeypatch.setattr(webhooks, "SENDER", recv)
    client.post(f"/v1/entities/{EX}/webhooks/{S['hook']}/test", headers=S["h"])
    relay()
    rows = db.execute("SELECT attempt, status_code, error, attempted_at, next_retry_at FROM webhook_deliveries WHERE endpoint_id = %s ORDER BY id DESC LIMIT 2", (S["hook"],)).fetchall()
    assert rows[0]["attempt"] == 2 and rows[0]["attempted_at"] is None and rows[0]["next_retry_at"] > dt.datetime.now(dt.timezone.utc)
    assert rows[1]["attempt"] == 1 and rows[1]["status_code"] == 500
    assert relay()["delivered"] == 0                                                                 # not due yet
    db.execute("UPDATE webhook_deliveries SET next_retry_at = now() WHERE endpoint_id = %s AND attempted_at IS NULL", (S["hook"],))
    relay()
    last = db.execute("SELECT attempt, error FROM webhook_deliveries WHERE endpoint_id = %s AND attempt = 2", (S["hook"],)).fetchone()
    assert "ConnectionError" in last["error"]
    wait2 = db.execute("SELECT extract(epoch FROM next_retry_at - now()) AS s FROM webhook_deliveries WHERE endpoint_id = %s AND attempt = 3", (S["hook"],)).fetchone()["s"]
    assert 240 < wait2 <= 300                                                                        # 60 s after the first failure, 300 s after the second, then 1800 s
    db.execute("UPDATE webhook_deliveries SET next_retry_at = now() WHERE endpoint_id = %s AND attempted_at IS NULL", (S["hook"],))
    relay()                                                                                          # the third attempt succeeds (the fake has no statuses left)
    assert db.execute("SELECT delivered_at FROM webhook_deliveries WHERE endpoint_id = %s AND attempt = 3", (S["hook"],)).fetchone()["delivered_at"]
    # 72 hours: an event that is older than that gets no further attempt after a failure
    recv2 = FakeReceiver([500])
    monkeypatch.setattr(webhooks, "SENDER", recv2)
    client.post(f"/v1/entities/{EX}/webhooks/{S['hook']}/test", headers=S["h"])
    with core.tx(None, role="biz_worker") as cur:
        webhooks.fan_out(cur)
    db.execute("""UPDATE webhook_deliveries SET next_retry_at = now() WHERE endpoint_id = %s AND attempted_at IS NULL""", (S["hook"],))
    db.execute("""UPDATE event_outbox SET occurred_at = now() - interval '73 hours' WHERE id = (SELECT max(event_id) FROM webhook_deliveries WHERE endpoint_id = %s)""", (S["hook"],))
    relay()
    assert db.execute("SELECT count(*) AS n FROM webhook_deliveries WHERE endpoint_id = %s AND attempted_at IS NULL", (S["hook"],)).fetchone()["n"] == 0


def test_a_disabled_endpoint_gets_nothing_and_can_be_deleted(client, db, monkeypatch):
    recv = FakeReceiver()
    monkeypatch.setattr(webhooks, "SENDER", recv)
    h = S["h"]
    assert client.patch(f"/v1/entities/{EX}/webhooks/{S['hook']}", headers=h, json={"is_active": False}).json()["is_active"] is False
    client.post(f"/v1/entities/{EX}/webhooks/{S['hook']}/test", headers=h)
    relay()
    assert recv.calls == []
    assert client.patch(f"/v1/entities/{EX}/webhooks/{S['hook']}", headers=h, json={"url": "http://insecure.example.com"}).status_code == 400
    assert client.delete(f"/v1/entities/{EX}/webhooks/{S['hook']}", headers=h).status_code == 204
    assert client.get(f"/v1/entities/{EX}/webhooks/{S['hook']}", headers=h).status_code == 404
    assert client.get(f"/v1/entities/{EX}/webhooks", headers=bearer(code_login(client, VIEWER))).status_code == 200
    assert client.post(f"/v1/entities/{EX}/webhooks", headers=bearer(code_login(client, VIEWER)), json={"url": "https://a.example.com", "events": ["*"]}).status_code == 403


def test_the_signature_check_rejects_malformed_headers():
    assert not webhooks.verify("s", "garbage", b"x")
    assert not webhooks.verify("s", "t=abc,v1=00", b"x")
    sig = webhooks.sign("s", b"x", t=1_000_000)
    assert webhooks.verify("s", sig, b"x", now=1_000_100) and not webhooks.verify("s", sig, b"x", now=1_000_400)


# ------------------------------------------------------------------ rate limits
def test_fixed_windows_reset():
    lim = ratelimit.MemoryLimiter()
    t0 = 1_000_020.0
    results = [lim.hit("k", 3, now=t0)[0] for _ in range(5)]
    assert results == [True, True, True, False, False]
    ok, remaining, reset = lim.hit("k", 3, now=t0)
    assert not ok and remaining == 0 and 0 < reset <= 60
    assert lim.hit("k", 3, now=t0 + 60)[0]                                  # next window
    assert lim.hit("other", 3, now=t0)[0]                                    # keys are independent


def test_requests_are_limited_per_caller_with_headers(client, monkeypatch):
    monkeypatch.setenv("BIZ_RATE_USER", "4")
    ratelimit.LIMITER.reset()
    h = bearer(code_login(client, OWNER))                                    # one request already counted for the ip; the token is a different caller
    seen = [client.get("/v1/me", headers=h) for _ in range(5)]
    assert [r.status_code for r in seen] == [200, 200, 200, 200, 429]
    assert seen[0].headers["RateLimit"].startswith("limit=4, remaining=3, reset=")
    assert seen[3].headers["RateLimit"].startswith("limit=4, remaining=0")
    last = seen[4]
    assert int(last.headers["Retry-After"]) >= 1 and last.json()["code"] == "rate_limited"
    assert last.headers["content-type"].startswith("application/problem+json")
    h2 = bearer(code_login(client, OTHER))
    assert client.get("/v1/me", headers=h2).status_code == 200               # someone else is unaffected
    ratelimit.LIMITER.reset()


def test_portal_calling_actions_have_their_own_lower_limit(client, monkeypatch):
    assert any(p.match(f"/v1/entities/{EX}/sales-documents/{uuid.uuid4()}/issue") for p in ratelimit.HEAVY_PATHS)
    assert not any(p.match(f"/v1/entities/{EX}/accounts") for p in ratelimit.HEAVY_PATHS)
    monkeypatch.setenv("BIZ_RATE_HEAVY", "2")
    monkeypatch.setenv("BIZ_RATE_USER", "100")
    ratelimit.LIMITER.reset()
    h = bearer(code_login(client, OWNER))
    codes = [client.post(f"/v1/entities/{EX}/sales-documents/{uuid.uuid4()}/issue", headers={**h, "Idempotency-Key": str(uuid.uuid4())}).status_code for _ in range(4)]
    assert codes[2:] == [429, 429] and 429 not in codes[:2]
    assert client.get("/v1/me", headers=h).status_code == 200                # ordinary calls still have their 100
    ratelimit.LIMITER.reset()


def test_an_api_key_is_limited_as_its_own_caller(client, monkeypatch):
    k = client.post(f"/v1/entities/{EX}/api-keys", headers=S["h"], json={"name": "limited", "permissions": ["finance:read"]}).json()["key"]
    monkeypatch.setenv("BIZ_RATE_USER", "2")
    ratelimit.LIMITER.reset()
    codes = [client.get(f"/v1/entities/{EX}/accounts", headers={"Authorization": f"Bearer {k}"}).status_code for _ in range(3)]
    assert codes == [200, 200, 429]
    ratelimit.LIMITER.reset()


def test_responses_carry_security_headers(client):
    r = client.get("/healthz")
    assert r.headers["X-Content-Type-Options"] == "nosniff" and r.headers["Cache-Control"] == "no-store"
    assert "Strict-Transport-Security" in r.headers and "default-src 'none'" in r.headers["Content-Security-Policy"]


# ------------------------------------------------------------------ audit trail and its verification
def test_the_audit_trail_lists_and_verifies(client, db):
    h = S["h"]
    log = client.get(f"/v1/entities/{EX}/audit-log", headers=h).json()
    assert log["data"] and {"hash", "prev_hash", "actor_name"} <= set(log["data"][0])
    only = client.get(f"/v1/entities/{EX}/audit-log?subject=webhook_endpoints", headers=h).json()["data"]
    assert only and all(a["subject"] == "webhook_endpoints" for a in only)
    v = client.get(f"/v1/entities/{EX}/audit-log/verify", headers=h).json()
    assert v["intact"] is True and v["first_broken_id"] is None and v["checked"] > 0
    assert client.get(f"/v1/entities/{EX}/audit-log", headers=bearer(code_login(client, OTHER))).status_code == 404
    with core.tx(U_OWNER) as cur:                                               # as an ordinary user, who sees only their own tenant's rows
        cur.execute("SELECT app.audit_verify() AS b")
        assert cur.fetchone()["b"] is None


def test_tampering_is_found_alerted_and_resolved(client, db, monkeypatch):
    h = S["h"]
    row = db.execute("SELECT id, after FROM audit_log WHERE entity_id = %s AND after IS NOT NULL ORDER BY id LIMIT 1", (EX,)).fetchone()
    alerts = FakeReceiver()
    monkeypatch.setattr(webhooks, "SENDER", alerts)
    monkeypatch.setenv("BIZ_ALERT_WEBHOOK", "https://alerts.example.com/pager")
    db.execute("ALTER TABLE audit_log DISABLE TRIGGER audit_log_no_update")
    try:
        db.execute("UPDATE audit_log SET after = %s WHERE id = %s", (json.dumps({"tampered": True}), row["id"]))
        v = client.get(f"/v1/entities/{EX}/audit-log/verify", headers=h).json()
        assert v["intact"] is False and v["first_broken_id"] == row["id"]
        theirs = client.get(f"/v1/entities/{EX2}/audit-log/verify", headers=bearer(code_login(client, OTHER))).json()
        assert theirs["intact"] is False and theirs["first_broken_id"] is None      # a break elsewhere is reported, but not where
        with core.tx(None, role="biz_worker") as cur:
            res = worker.handle_audit_verify(cur, {})
            again = worker.handle_audit_verify(cur, {})
        assert res["intact"] is False and res["first_broken_id"] == row["id"] and again["intact"] is False
        assert db.execute("SELECT count(*) AS n FROM system_alerts WHERE kind = 'audit_chain_broken' AND resolved_at IS NULL").fetchone()["n"] == 1   # one alert, not one per run
        assert len(alerts.calls) == 2 and json.loads(alerts.calls[0][2])["first_broken_id"] == row["id"]
    finally:
        db.execute("UPDATE audit_log SET after = %s WHERE id = %s", (json.dumps(row["after"]), row["id"]))
        db.execute("ALTER TABLE audit_log ENABLE TRIGGER audit_log_no_update")
    with core.tx(None, role="biz_worker") as cur:
        assert worker.handle_audit_verify(cur, {})["intact"] is True
    assert db.execute("SELECT count(*) AS n FROM system_alerts WHERE kind = 'audit_chain_broken' AND resolved_at IS NULL").fetchone()["n"] == 0


def test_system_jobs_are_scheduled_once_a_day_and_housekeeping_purges(db):
    db.execute("DELETE FROM jobs WHERE kind IN ('audit.verify', 'system.housekeeping')")
    with core.tx(None, role="biz_worker") as cur:
        assert worker.schedule_system_jobs(cur) == 2
        assert worker.schedule_system_jobs(cur) == 0
    assert {"audit.verify", "system.housekeeping"} <= set(worker.HANDLERS)
    db.execute("INSERT INTO auth_otps (identifier, code_hash, expires_at, created_at) VALUES ('old@example.in', repeat('a', 64), now(), now() - interval '2 days')")
    with core.tx(None, role="biz_worker") as cur:
        assert worker.handle_housekeeping(cur, {})["purged"] >= 1
    db.execute("DELETE FROM jobs WHERE kind IN ('audit.verify', 'system.housekeeping') AND status = 'queued'")


# ------------------------------------------------------------------ startup guard
@pytest.fixture
def production(monkeypatch):
    for mod in (core, security):
        monkeypatch.setattr(mod, "ENV", "production")
    monkeypatch.delenv("BIZ_FIELD_KEY", raising=False)
    monkeypatch.delenv("BIZ_OTP_PROVIDER", raising=False)
    adapters.reset()
    yield
    adapters.reset()


def test_production_refuses_to_start_with_defaults(production):
    msgs = " | ".join(security.problems())
    assert "JWT_SECRET" in msgs and "BIZ_FIELD_KEY" in msgs and "000000" in msgs
    with pytest.raises(RuntimeError, match="Refusing to start"):
        security.check_environment()


def test_production_refuses_sandbox_portal_adapters(production, monkeypatch):
    monkeypatch.setattr(security, "JWT_SECRET", "x" * 40)
    monkeypatch.setenv("BIZ_FIELD_KEY", "k")
    monkeypatch.setenv("BIZ_OTP_PROVIDER", "acme")
    msgs = " | ".join(security.problems())
    assert "sandbox" in msgs.lower() and "JWT_SECRET" not in msgs and "BIZ_FIELD_KEY" not in msgs and "000000" not in msgs


def test_a_fully_configured_production_starts(production, monkeypatch):
    class Real:
        pass
    monkeypatch.setattr(security, "JWT_SECRET", "x" * 40)
    monkeypatch.setenv("BIZ_FIELD_KEY", "k")
    monkeypatch.setenv("BIZ_OTP_PROVIDER", "acme")
    for fam in adapters.IMPLEMENTATIONS:
        adapters.use(fam, Real())
    assert security.problems() == []
    security.check_environment()


def test_an_unknown_environment_name_is_treated_as_production(monkeypatch):
    monkeypatch.setattr(core, "ENV", "prod")
    monkeypatch.setattr(security, "ENV", "prod")
    assert core.is_production() and any("BIZ_ENV" in p for p in security.problems())


# ------------------------------------------------------------------ security review, as tests
def test_every_entity_table_has_forced_row_level_security(db):
    rows = db.execute("""SELECT c.relname, c.relrowsecurity, c.relforcerowsecurity FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
                          WHERE n.nspname = 'public' AND c.relkind = 'r'
                            AND EXISTS (SELECT 1 FROM pg_attribute a WHERE a.attrelid = c.oid AND a.attname = 'entity_id' AND NOT a.attisdropped)
                          ORDER BY 1""").fetchall()
    assert len(rows) > 60
    unprotected = [r["relname"] for r in rows if not (r["relrowsecurity"] and r["relforcerowsecurity"])]
    assert unprotected == []


def test_secret_tables_are_closed_to_the_application_role(db):
    for t in ("auth_otps", "refresh_tokens", "system_alerts"):
        assert not db.execute("SELECT has_table_privilege('biz_app', %s, 'SELECT') AS p", (t,)).fetchone()["p"], t
    with core.tx(U_OWNER) as cur:
        for t in ("auth_otps", "refresh_tokens", "system_alerts"):
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                with cur.connection.transaction():
                    cur.execute(f"SELECT * FROM {t}")


def test_audit_log_is_append_only_for_everyone_but_the_owner(db):
    for role in ("biz_app", "biz_worker"):
        assert not db.execute("SELECT has_table_privilege(%s, 'audit_log', 'UPDATE') AS p", (role,)).fetchone()["p"]
        assert not db.execute("SELECT has_table_privilege(%s, 'audit_log', 'DELETE') AS p", (role,)).fetchone()["p"]


def test_sensitive_columns_are_encrypted_not_plain(db):
    cols = [r["column_name"] for r in db.execute("SELECT column_name FROM information_schema.columns WHERE table_schema = 'public' AND column_name LIKE '%\\_enc' ORDER BY 1").fetchall()]
    assert {"secret_enc", "bank_account_enc", "account_no_enc", "totp_secret_enc"} <= set(cols)
    for c in db.execute("SELECT table_name, column_name, data_type FROM information_schema.columns WHERE table_schema = 'public' AND column_name LIKE '%\\_enc'").fetchall():
        assert c["data_type"] == "bytea", c


def test_the_api_key_functions_refuse_an_entity_the_caller_cannot_see(db):
    with core.tx(U_VIEWER) as cur:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            with cur.connection.transaction():
                cur.execute("SELECT app.create_api_key(%s, 'x', 'deadbeef', repeat('a', 64), ARRAY['finance:read'], NULL)", (EX2,))
    with core.tx(U_OTHER) as cur:
        cur.execute("SELECT app.revoke_api_key(%s) AS ok", (S["key_id"],))
        assert cur.fetchone()["ok"] is False                                              # not theirs
