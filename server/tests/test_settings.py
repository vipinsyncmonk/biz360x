"""Read endpoints for Settings (people, advisors, certificates, notification choices) and the Group view (client grid, workload)."""
import datetime as dt
import os
import uuid

import psycopg
import pytest
from psycopg.rows import dict_row

DB = os.environ["DATABASE_URL"]
ORG = "00000000-0000-0000-0000-000000000600"
ORG_OTHER = "00000000-0000-0000-0000-000000000601"
E1, E2, E3 = ("00000000-0000-0000-0000-00000000061%d" % i for i in (1, 2, 3))
U_PARTNER, U_STAFF, U_VIEWER, U_STRANGER, U_MIA = ("00000000-0000-0000-0000-00000000062%d" % i for i in (1, 2, 3, 4, 5))
PARTNER, STAFF, VIEWER, STRANGER, MIA = "st-partner@example.in", "st-staff@example.in", "st-viewer@example.in", "st-stranger@example.in", "st-mia@example.in"
TODAY = dt.datetime.now(dt.timezone.utc).date()   # the database counts days in UTC


@pytest.fixture(scope="module")
def db():
    with psycopg.connect(DB, autocommit=True, row_factory=dict_row) as c:
        yield c


@pytest.fixture(scope="module")
def client():
    from fastapi.testclient import TestClient
    from app.main import app
    return TestClient(app)


def rule(db, where):
    return db.execute(f"SELECT id FROM compliance_rules WHERE {where} ORDER BY code, version DESC LIMIT 1").fetchone()["id"]


def obligation(db, entity, rule_id, status, due, owner=None, label="Oct 2026"):
    db.execute("""INSERT INTO obligations (entity_id, rule_id, fy, period_start, period_end, period_label, due_date, status, owner_user_id, filed_at, reason_snapshot)
                  VALUES (%s, %s, '2026-27', %s, %s, %s, %s, %s, %s, CASE WHEN %s = 'filed' THEN now() END, '{}')""",
               (entity, rule_id, due.replace(day=1), due, label, due, status, owner, status))


@pytest.fixture(scope="module", autouse=True)
def seed(db):
    db.execute("INSERT INTO organizations (id, name, kind) VALUES (%s, 'Settings CA Firm', 'ca_firm'), (%s, 'Another Firm', 'ca_firm')", (ORG, ORG_OTHER))
    for e, name, org in ((E1, "Alpha Client Pvt Ltd", ORG), (E2, "Beta Client Pvt Ltd", ORG), (E3, "Gamma Elsewhere Pvt Ltd", ORG_OTHER)):
        db.execute("INSERT INTO entities (id, organization_id, legal_name, constitution, track) VALUES (%s, %s, %s, 'private_limited', 'business')", (e, org, name))
    db.execute("""INSERT INTO users (id, email, full_name, mfa_enabled) VALUES (%s, %s, 'Priya Partner', false), (%s, %s, 'Sam Staff', false),
                  (%s, %s, 'Vik Viewer', false), (%s, %s, 'Stranger', false), (%s, %s, 'Mia Mfa', true)""", (U_PARTNER, PARTNER, U_STAFF, STAFF, U_VIEWER, VIEWER, U_STRANGER, STRANGER, U_MIA, MIA))
    db.execute("INSERT INTO org_members VALUES (%s, %s, 'partner'), (%s, %s, 'staff')", (ORG, U_PARTNER, ORG, U_STAFF))
    db.execute("INSERT INTO memberships (entity_id, user_id, role, accepted_at) VALUES (%s, %s, 'owner', now()), (%s, %s, 'viewer', NULL), (%s, %s, 'approver', now())", (E1, U_PARTNER, E1, U_VIEWER, E1, U_MIA))
    db.execute("INSERT INTO memberships (entity_id, user_id, role) VALUES (%s, %s, 'owner')", (E3, U_STRANGER))
    db.execute("INSERT INTO advisor_grants (entity_id, advisor_name, advisor_kind, scopes, status, expires_on) VALUES (%s, 'Mehta and Co', 'chartered_accountant', %s, 'active', %s)",
               (E1, '{"gst": "prepare"}', TODAY - dt.timedelta(days=1)))
    pid = db.execute("INSERT INTO persons (entity_id, full_name) VALUES (%s, 'Director One') RETURNING id", (E1,)).fetchone()["id"]
    db.execute("""INSERT INTO dsc_certificates (entity_id, person_id, valid_from, valid_until, serial_hash, registered_on)
                  VALUES (%s, %s, %s, %s, 'abc', '{MCA,GSTN}')""", (E1, pid, TODAY - dt.timedelta(days=300), TODAY + dt.timedelta(days=65)))
    gst, mca = rule(db, "authority_code = 'GSTN'"), rule(db, "authority_code = 'MCA'")
    obligation(db, E1, gst, "awaiting_client", TODAY + dt.timedelta(days=3), U_STAFF)
    obligation(db, E1, mca, "overdue", TODAY - dt.timedelta(days=5), U_STAFF, "FY 2025-26")
    obligation(db, E2, gst, "filed", TODAY - dt.timedelta(days=10))
    yield
    db.execute("DELETE FROM obligations WHERE entity_id IN (%s, %s, %s)", (E1, E2, E3))


def login(client, email):
    r = client.post("/v1/auth/token", json={"grant_type": "otp", "email": email, "otp": "000000"})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def test_members_with_how_they_sign_in(client):
    h = login(client, PARTNER)
    rows = client.get(f"/v1/entities/{E1}/members", headers=h).json()["data"]
    by = {r["email"]: r for r in rows}
    assert set(by) == {PARTNER, VIEWER, MIA}
    assert by[PARTNER]["role"] == "owner" and by[PARTNER]["auth"] == "Password" and by[MIA]["auth"] == "Password · 2FA" and by[PARTNER]["accepted_at"]
    assert by[VIEWER]["accepted_at"] is None and by[VIEWER]["modules"] == ["*"]
    assert client.get(f"/v1/entities/{E3}/members", headers=h).status_code == 404            # another firm's client is invisible
    assert client.get(f"/v1/entities/{E1}/members", headers=login(client, STRANGER)).status_code == 404


def test_advisors_show_expired_and_certificates_their_dates(client):
    h = login(client, PARTNER)
    g = client.get(f"/v1/entities/{E1}/advisor-grants", headers=h).json()["data"]
    assert len(g) == 1 and g[0]["advisor_name"] == "Mehta and Co" and g[0]["status"] == "expired" and g[0]["scopes"] == {"gst": "prepare"}
    d = client.get(f"/v1/entities/{E1}/dsc-certificates", headers=h).json()["data"]
    assert d[0]["person_name"] == "Director One" and d[0]["registered_on"] == ["MCA", "GSTN"] and d[0]["valid_until"] == (TODAY + dt.timedelta(days=65)).isoformat()


def test_notification_choices_are_private_and_replace_per_entity(client):
    h = login(client, PARTNER)
    assert client.get("/v1/me/notification-preferences", headers=h).json() == []
    body = [{"entity_id": E1, "event_type": "due_in_7_days", "channel": "email", "enabled": True}, {"entity_id": E1, "event_type": "due_in_7_days", "channel": "sms", "enabled": False}]
    r = client.put("/v1/me/notification-preferences", headers=h, json=body)
    assert r.status_code == 200 and len(r.json()) == 2
    again = client.put("/v1/me/notification-preferences", headers=h, json=body[:1]).json()
    assert [(x["channel"], x["enabled"]) for x in again] == [("email", True)]                # replaced, not merged
    hs = login(client, STAFF)
    assert client.get("/v1/me", headers=hs).json()["email"] == STAFF
    assert client.get("/v1/me/notification-preferences", headers=hs).json() == []
    assert client.put("/v1/me/notification-preferences", headers=h, json=[{**body[0], "entity_id": E3}]).status_code == 404
    assert client.put("/v1/me/notification-preferences", headers=h, json=[{**body[0], "channel": "pigeon"}]).status_code == 400


def test_the_client_grid_for_a_firm(client):
    h = login(client, PARTNER)
    rows = client.get(f"/v1/organizations/{ORG}/entities", headers=h).json()["data"]
    by = {r["legal_name"]: r for r in rows}
    assert set(by) == {"Alpha Client Pvt Ltd", "Beta Client Pvt Ltd"}
    a = by["Alpha Client Pvt Ltd"]
    assert a["cells"]["GST"] == "client" and a["cells"]["MCA"] == "overdue" and a["cells"]["TDS"] == "na" and a["owner_staff"] == "Sam Staff"
    assert [i["title"] for i in a["open_items"]] and a["open_items"][0]["due_on"] < a["open_items"][1]["due_on"]            # earliest first
    assert a["waiting_filings"] == 1 and by["Beta Client Pvt Ltd"]["waiting_filings"] == 0
    assert by["Beta Client Pvt Ltd"]["cells"]["GST"] == "clear" and by["Beta Client Pvt Ltd"]["open_items"] == []
    waiting = client.get(f"/v1/organizations/{ORG}/entities?filter=waiting_on_client", headers=h).json()["data"]
    assert [r["legal_name"] for r in waiting] == ["Alpha Client Pvt Ltd"]
    assert client.get(f"/v1/organizations/{ORG}/entities?filter=overdue", headers=h).json()["data"][0]["legal_name"] == "Alpha Client Pvt Ltd"
    assert [r["legal_name"] for r in client.get(f"/v1/organizations/{ORG}/entities?staff={U_PARTNER}", headers=h).json()["data"]] == []
    assert [r["legal_name"] for r in client.get(f"/v1/organizations/{ORG}/entities?staff={U_STAFF}", headers=h).json()["data"]] == ["Alpha Client Pvt Ltd"]
    assert client.get(f"/v1/organizations/{ORG}/entities?filter=nonsense", headers=h).status_code == 400


def test_the_grid_and_workload_are_for_the_firms_own_people(client):
    for who in (STRANGER, VIEWER):
        h = login(client, who)
        assert client.get(f"/v1/organizations/{ORG}/entities", headers=h).status_code == 404
        assert client.get(f"/v1/organizations/{ORG}/workload", headers=h).status_code == 404
    load = client.get(f"/v1/organizations/{ORG}/workload", headers=login(client, STAFF)).json()
    assert {x["name"]: x["filings_due"] for x in load} == {"Sam Staff": 2, "Priya Partner": 0}                              # the overdue one and the one due in 3 days


# ------------------------------------------------------------------ writes
def test_inviting_and_removing_people(client, db):
    h = login(client, PARTNER)
    url = f"/v1/entities/{E1}/members"
    r = client.post(url, headers=h, json={"email": "New.Person@Example.in", "full_name": "Nina New", "role": "maker", "modules": ["gst", "finance"]})
    assert r.status_code == 201, r.text
    m = r.json()
    assert m["email"] == "new.person@example.in" and m["full_name"] == "Nina New" and m["role"] == "maker" and m["modules"] == ["finance", "gst"]
    assert m["accepted_at"] is None and m["auth"] == "One-time code"
    assert m["email"] in {x["email"] for x in client.get(url, headers=h).json()["data"]}
    assert client.post(url, headers=h, json={"email": "new.person@example.in", "role": "viewer"}).json()["code"] == "conflict"        # already a member
    assert client.post(url, headers=h, json={"email": PARTNER, "role": "viewer"}).status_code == 409
    assert client.post(url, headers=h, json={"email": "not-an-email", "role": "viewer"}).status_code == 400
    assert client.post(url, headers=h, json={"email": "x@example.in", "role": "boss"}).status_code == 400
    assert client.post(url, headers=h, json={"email": "x@example.in", "role": "viewer", "modules": ["Bad Name"]}).status_code == 400
    assert client.post(url, headers=login(client, VIEWER), json={"email": "y@example.in", "role": "viewer"}).json()["code"] == "permission_denied"
    assert client.post(url, headers=login(client, STRANGER), json={"email": "y@example.in", "role": "viewer"}).status_code == 404

    assert client.delete(f"{url}/{m['id']}", headers=login(client, VIEWER)).status_code == 403
    assert client.delete(f"{url}/{m['id']}", headers=h).status_code == 204
    assert m["email"] not in {x["email"] for x in client.get(url, headers=h).json()["data"]}
    assert client.delete(f"{url}/{m['id']}", headers=h).status_code == 404                                                        # already gone
    again = client.post(url, headers=h, json={"email": m["email"], "role": "viewer"})                                              # a returning person is re-invited
    assert again.status_code == 201 and again.json()["id"] == m["id"] and again.json()["role"] == "viewer"

    me = next(x for x in client.get(url, headers=h).json()["data"] if x["email"] == PARTNER)
    own = client.delete(f"{url}/{me['id']}", headers=h)
    assert own.status_code == 409 and own.json()["code"] == "conflict"                                                             # not your own access
    assert db.execute("SELECT count(*) AS n FROM audit_log WHERE entity_id = %s AND action IN ('member.invited', 'member.removed')", (E1,)).fetchone()["n"] == 3


def test_granting_and_revoking_an_advisor(client):
    h = login(client, PARTNER)
    url = f"/v1/entities/{E1}/advisor-grants"
    ok = client.post(url, headers=h, json={"advisor_name": "Rao and Associates", "advisor_kind": "chartered_accountant", "invite_email": "ca@rao.in",
                                           "scopes": {"gst": "prepare", "finance": "read"}, "expires_on": (TODAY + dt.timedelta(days=90)).isoformat()})
    assert ok.status_code == 201, ok.text
    g = ok.json()
    assert g["status"] == "invited" and g["scopes"] == {"gst": "prepare", "finance": "read"} and g["expires_on"] == (TODAY + dt.timedelta(days=90)).isoformat()
    assert g["id"] in {x["id"] for x in client.get(url, headers=h).json()["data"]}
    bad = lambda **kw: client.post(url, headers=h, json={"advisor_name": "X", "advisor_kind": "auditor", "scopes": {"gst": "read"}, **kw})
    assert bad(scopes={}).status_code == 400
    assert bad(scopes={"gst": "own"}).status_code == 400
    assert bad(scopes={"GST!": "read"}).status_code == 400
    assert bad(advisor_kind="plumber").status_code == 400
    assert bad(expires_on=TODAY.isoformat()).status_code == 400
    assert client.post(url, headers=login(client, VIEWER), json={"advisor_name": "X", "advisor_kind": "auditor", "scopes": {"gst": "read"}}).status_code == 403

    assert client.delete(f"{url}/{g['id']}", headers=login(client, VIEWER)).status_code == 403
    assert client.delete(f"{url}/{g['id']}", headers=h).status_code == 204
    assert next(x for x in client.get(url, headers=h).json()["data"] if x["id"] == g["id"])["status"] == "revoked"
    assert client.delete(f"{url}/{uuid.uuid4()}", headers=h).status_code == 404
    assert client.delete(f"/v1/entities/{E3}/advisor-grants/{g['id']}", headers=h).status_code == 404


def test_recording_a_certificate(client, db):
    h = login(client, PARTNER)
    url = f"/v1/entities/{E1}/dsc-certificates"
    body = {"person_name": "Director One", "valid_from": (TODAY - dt.timedelta(days=30)).isoformat(), "valid_until": (TODAY + dt.timedelta(days=700)).isoformat(),
            "registered_on": ["MCA"], "serial": "5A3F9C01"}
    ok = client.post(url, headers=h, json=body)
    assert ok.status_code == 201, ok.text
    assert ok.json()["person_name"] == "Director One" and ok.json()["dsc_class"] == "class3" and "serial" not in ok.json()
    assert db.execute("SELECT count(*) AS n FROM persons WHERE entity_id = %s AND full_name = 'Director One'", (E1,)).fetchone()["n"] == 1      # the existing person is reused
    assert db.execute("SELECT serial_hash FROM dsc_certificates WHERE id = %s", (ok.json()["id"],)).fetchone()["serial_hash"] != "5A3F9C01"       # only a hash is kept
    assert client.post(url, headers=h, json=body).json()["code"] == "conflict"                                                                  # same serial twice
    new = client.post(url, headers=h, json={**body, "person_name": "Cfo Two", "serial": "77AA1234"})
    assert new.status_code == 201
    assert db.execute("SELECT count(*) AS n FROM persons WHERE entity_id = %s AND full_name = 'Cfo Two'", (E1,)).fetchone()["n"] == 1
    assert client.post(url, headers=h, json={**body, "serial": "BB00", "valid_until": body["valid_from"]}).status_code == 400
    assert client.post(url, headers=h, json={**body, "serial": "BB11", "registered_on": ["RBI"]}).status_code == 400
    assert client.post(url, headers=h, json={k: v for k, v in body.items() if k != "serial"}).status_code == 400
    assert client.post(url, headers=login(client, VIEWER), json={**body, "serial": "CC22CC22"}).status_code == 403
    assert len(client.get(url, headers=h).json()["data"]) == 3


def test_chasing_clients_reminds_their_owners_not_the_firm(client, db):
    h, url = login(client, PARTNER), f"/v1/organizations/{ORG}/client-reminders"
    key = lambda: {**h, "Idempotency-Key": str(uuid.uuid4())}      # noqa: E731
    assert client.post(url, headers=h, json={}).status_code == 400                                      # a key is required
    first = client.post(url, headers=key(), json={})
    assert first.status_code == 202, first.text
    assert [s["reason"] for s in first.json()["skipped"] if s["entity_id"] == E1] == ["no_client_contact"]   # only the firm's own people are on it
    assert first.json()["reminded"] == []

    owner = uuid.uuid4()
    db.execute("INSERT INTO users (id, email, full_name) VALUES (%s, 'st-client-owner@example.in', 'Client Owner')", (owner,))
    db.execute("INSERT INTO memberships (entity_id, user_id, role, accepted_at) VALUES (%s, %s, 'owner', now())", (E1, owner))
    try:
        k = key()
        ok = client.post(url, headers=k, json={"entity_ids": [E1, E2]})
        assert ok.status_code == 202, ok.text
        assert ok.json()["reminded"] == [{"entity_id": E1, "legal_name": "Alpha Client Pvt Ltd", "items": 1, "recipients": 1}]
        assert ok.json()["skipped"] == [{"entity_id": E2, "legal_name": "Beta Client Pvt Ltd", "reason": "nothing_waiting"}]
        rows = db.execute("SELECT user_id, event_type, channel, status FROM notifications WHERE entity_id = %s", (E1,)).fetchall()
        assert [(str(r["user_id"]), r["event_type"], r["channel"], r["status"]) for r in rows] == [(str(owner), "client_reminder", "email", "queued")]
        assert db.execute("SELECT count(*) AS n FROM audit_log WHERE entity_id = %s AND action = 'client_reminder.queued'", (E1,)).fetchone()["n"] == 1

        assert client.post(url, headers=k, json={"entity_ids": [E1, E2]}).json() == ok.json()          # the same key replays the answer
        assert db.execute("SELECT count(*) AS n FROM notifications WHERE entity_id = %s", (E1,)).fetchone()["n"] == 1
        again = client.post(url, headers=key(), json={})                                                # pressing it again does not nag
        assert [s["reason"] for s in again.json()["skipped"] if s["entity_id"] == E1] == ["reminded_recently"]
        assert db.execute("SELECT count(*) AS n FROM notifications WHERE entity_id = %s", (E1,)).fetchone()["n"] == 1

        assert client.post(url, headers=key(), json={"entity_ids": [E3]}).status_code == 400           # another firm's client
        assert client.post(url, headers=login(client, STRANGER), json={}).status_code in (400, 404)
        assert client.post(url, headers={**login(client, STRANGER), "Idempotency-Key": str(uuid.uuid4())}, json={}).status_code == 404
    finally:
        db.execute("DELETE FROM notifications WHERE entity_id = %s", (E1,))
        db.execute("DELETE FROM memberships WHERE user_id = %s", (owner,))
        db.execute("DELETE FROM users WHERE id = %s", (owner,))
