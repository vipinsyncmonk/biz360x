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
TODAY = dt.date.today()


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
