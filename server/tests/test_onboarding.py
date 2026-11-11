"""POST /onboarding/workspaces: a person with no workspace creates one from a registry lookup."""
import datetime as dt
import json
import os
import uuid

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.rows import dict_row

DB = os.environ["DATABASE_URL"]
ORG_DEMO = "00000000-0000-0000-0000-0000000000a1"
U_NEW, U_OTHER = "00000000-0000-0000-0000-000000000401", "00000000-0000-0000-0000-000000000402"
NEW, OTHER = "ob-new@example.in", "ob-other@example.in"
CIN, CIN2 = "U11111KA2021PTC111111", "U22222KA2021PTC222222"
GST, GST2 = "29AAKCT1111A1Z5", "29AAKCT2222B1Z8"
S: dict = {}


@pytest.fixture(scope="module")
def db():
    with psycopg.connect(DB, autocommit=True, row_factory=dict_row) as c:
        yield c


@pytest.fixture(scope="module")
def client():
    from app.main import app
    return TestClient(app)


def lookup(db, user, ident, gstins, status="found", **over):
    ent = {"legal_name": "Teal Works Private Limited", "constitution": "private_limited", "incorporated_on": "2021-03-10", "registered_state": "29",
           "registered_address": "Indiranagar, Bengaluru", "paid_up_capital": "500000.00", "nic_code": "25999", "cin": ident, **over}
    ent = {k: v for k, v in ent.items() if v is not None}
    result = {"entity": ent, "gstins": [{"gstin": g, "state_code": g[:2], "status": "active", "filing_frequency": "monthly", "label": "Karnataka"} for g in gstins], "udyam": None}
    return db.execute("""INSERT INTO registry_lookups (user_id, identifier, kind, status, result, finished_at) VALUES (%s, %s, 'cin', %s, %s, now()) RETURNING id""",
                      (user, ident, status, json.dumps(result))).fetchone()["id"]


@pytest.fixture(scope="module", autouse=True)
def seed(db):
    db.execute("INSERT INTO users (id, email, full_name) VALUES (%s, %s, 'Newcomer'), (%s, %s, 'Someone Else')", (U_NEW, NEW, U_OTHER, OTHER))


def login(client, email):
    r = client.post("/v1/auth/token", json={"grant_type": "otp", "email": email, "otp": "000000"})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def key():
    return {"Idempotency-Key": str(uuid.uuid4())}


def answers(lk, **over):
    return {"lookup_id": str(lk), "track": "business", "gstins": [GST], "headcount_band": "20-49", "turnover_band": "1.5-5cr", "has_foreign_shareholder": False, "buys_from_mse": "no", **over}


def test_someone_with_no_workspace_creates_one(client, db):
    h = login(client, NEW)
    assert client.get("/v1/me", headers=h).json()["entities"] == []
    lk = lookup(db, U_NEW, CIN, [GST])
    body = answers(lk)
    k = key()
    r = client.post("/v1/onboarding/workspaces", headers={**h, **k}, json=body)
    assert r.status_code == 201, r.text
    out = r.json()
    assert out["obligations_created"] > 0 and uuid.UUID(out["entity_id"]) and uuid.UUID(out["engine_run_id"])
    S.update(h=h, key=k, body=body, out=out, lk=lk)
    me = client.get("/v1/me", headers=h).json()
    assert [(e["legal_name"], e["role"], e["track"]) for e in me["entities"]] == [("Teal Works Private Limited", "owner", "business")]
    assert [o["role"] for o in me["organizations"]] == ["admin"]
    ent = db.execute("SELECT cin, pan, constitution::text AS c, registered_state FROM entities WHERE id = %s", (out["entity_id"],)).fetchone()
    assert ent == {"cin": CIN, "pan": "AAKCT1111A", "c": "private_limited", "registered_state": "29"}
    assert db.execute("SELECT number, state_code FROM entity_registrations WHERE entity_id = %s", (out["entity_id"],)).fetchall() == [{"number": GST, "state_code": "29"}]
    facts = {f["fact_key"]: f for f in db.execute("SELECT fact_key, value_num, value_bool, source FROM entity_facts WHERE entity_id = %s", (out["entity_id"],)).fetchall()}
    assert facts["headcount"]["value_num"] == 30 and facts["headcount"]["source"] == "user"
    assert facts["paid_up_capital"]["source"] == "registry" and facts["has_foreign_investor"]["value_bool"] is False
    assert facts["has_mse_dues_over_45_days"]["value_bool"] is False
    today = dt.date.today()
    last_year_end = dt.date(today.year if today.month >= 4 else today.year - 1, 3, 31)
    dated = {f["fact_key"]: f["as_of"] for f in db.execute("SELECT fact_key, as_of FROM entity_facts WHERE entity_id = %s", (out["entity_id"],)).fetchall()}
    assert dated["aato"] == last_year_end and dated["headcount"] == today                  # "turnover last financial year" belongs to last year, so e-invoicing starts this year if it was over the limit


def test_the_first_engine_run_and_the_audit_trail(client, db):
    h, eid = S["h"], S["out"]["entity_id"]
    runs = db.execute("SELECT trigger, obligations_created FROM engine_runs WHERE entity_id = %s AND trigger = 'onboarding'", (eid,)).fetchall()
    assert len(runs) == 1 and runs[0]["obligations_created"] == S["out"]["obligations_created"]
    obl = client.get(f"/v1/entities/{eid}/obligations?limit=200", headers=h).json()["data"]
    assert len(obl) >= S["out"]["obligations_created"] and any(o["rule"]["authority_code"] == "GSTN" for o in obl)
    log = client.get(f"/v1/entities/{eid}/audit-log?subject=entities", headers=h).json()["data"]
    assert [a["action"] for a in log] == ["workspace.created"]


def test_a_replay_returns_the_first_answer_and_a_changed_body_conflicts(client, db):
    r = client.post("/v1/onboarding/workspaces", headers={**S["h"], **S["key"]}, json=S["body"])
    assert r.status_code == 201 and r.json() == S["out"]
    assert db.execute("SELECT count(*) AS n FROM entities WHERE cin = %s", (CIN,)).fetchone()["n"] == 1
    other = client.post("/v1/onboarding/workspaces", headers={**S["h"], **S["key"]}, json={**S["body"], "track": "startup"})
    assert other.status_code == 409 and other.json()["code"] == "idempotency_conflict"
    assert client.post("/v1/onboarding/workspaces", headers=S["h"], json=S["body"]).status_code == 400          # the key is required


def test_the_same_company_cannot_get_a_second_workspace(client, db):
    lk = lookup(db, U_NEW, CIN, [GST])
    r = client.post("/v1/onboarding/workspaces", headers={**S["h"], **key()}, json=answers(lk))
    assert r.status_code == 409 and r.json()["code"] == "conflict"
    assert db.execute("SELECT count(*) AS n FROM entities WHERE cin = %s", (CIN,)).fetchone()["n"] == 1             # the failed attempt left nothing behind
    assert db.execute("SELECT count(*) AS n FROM organizations WHERE name = 'Teal Works Private Limited'").fetchone()["n"] == 1


def test_only_registrations_the_registry_returned_are_accepted(client, db):
    lk = lookup(db, U_NEW, CIN2, [GST2])
    r = client.post("/v1/onboarding/workspaces", headers={**S["h"], **key()}, json=answers(lk, gstins=["07AAACX9999Z1ZA"]))
    assert r.status_code == 400 and r.json()["errors"][0]["field"] == "gstins"
    assert db.execute("SELECT count(*) AS n FROM entities WHERE cin = %s", (CIN2,)).fetchone()["n"] == 0


def test_a_lookup_that_found_nothing_or_belongs_to_someone_else_creates_nothing(client, db):
    h = S["h"]
    missing = lookup(db, U_NEW, CIN2, [], status="not_found")
    assert client.post("/v1/onboarding/workspaces", headers={**h, **key()}, json=answers(missing, gstins=[])).status_code == 409
    theirs = lookup(db, U_OTHER, CIN2, [GST2])
    r = client.post("/v1/onboarding/workspaces", headers={**h, **key()}, json=answers(theirs, gstins=[GST2]))
    assert r.status_code == 404                                                                                      # a lookup is private to the person who made it
    assert client.post("/v1/onboarding/workspaces", headers={**h, **key()}, json=answers(uuid.uuid4())).status_code == 404


def test_an_incomplete_registry_result_is_refused(client, db):
    h = S["h"]
    no_name = lookup(db, U_NEW, CIN2, [GST2], legal_name=None)
    assert client.post("/v1/onboarding/workspaces", headers={**h, **key()}, json=answers(no_name, gstins=[GST2])).status_code == 409
    no_cin = lookup(db, U_NEW, "AAAPX2222B", [GST2], cin=None)
    db.execute("UPDATE registry_lookups SET kind = 'pan' WHERE id = %s", (no_cin,))
    r = client.post("/v1/onboarding/workspaces", headers={**h, **key()}, json=answers(no_cin, gstins=[GST2]))
    assert r.status_code == 409 and "CIN" in r.json()["title"]
    assert client.post("/v1/onboarding/workspaces", headers={**h, **key()}, json=answers(no_cin, headcount_band="500")).status_code == 400


def test_an_organization_is_used_only_by_someone_who_administers_it(client, db):
    h = S["h"]
    lk = lookup(db, U_NEW, CIN2, [GST2])
    r = client.post("/v1/onboarding/workspaces", headers={**h, **key()}, json=answers(lk, gstins=[GST2], organization_id=ORG_DEMO))
    assert r.status_code == 404                                                                                      # someone else's organization is invisible
    org = db.execute("SELECT organization_id FROM entities WHERE id = %s", (S["out"]["entity_id"],)).fetchone()["organization_id"]
    ok = client.post("/v1/onboarding/workspaces", headers={**h, **key()}, json=answers(lk, gstins=[GST2], organization_id=str(org)))
    assert ok.status_code == 201, ok.text
    assert db.execute("SELECT organization_id FROM entities WHERE id = %s", (ok.json()["entity_id"],)).fetchone()["organization_id"] == org
    me = client.get("/v1/me", headers=h).json()
    assert len(me["entities"]) == 2 and len(me["organizations"]) == 1


def test_a_new_workspace_is_invisible_to_everyone_else(client):
    h = login(client, OTHER)
    assert client.get("/v1/me", headers=h).json()["entities"] == []
    assert client.get(f"/v1/entities/{S['out']['entity_id']}/obligations", headers=h).status_code == 404


def test_the_preview_and_the_created_workspace_agree(client, db):
    lk = lookup(db, U_NEW, "U33333KA2021PTC333333", ["29AAKCT3333C1ZB"])
    body = answers(lk, gstins=["29AAKCT3333C1ZB"])
    pre = client.post("/v1/onboarding/preview", headers=S["h"], json=body).json()
    made = client.post("/v1/onboarding/workspaces", headers={**S["h"], **key()}, json=body).json()
    gst_pre = next(a["count"] for a in pre["by_authority"] if a["authority"] == "GSTN")
    gst_made = db.execute("""SELECT count(DISTINCT (o.rule_id, o.registration_id)) AS n FROM obligations o JOIN compliance_rules r ON r.id = o.rule_id
                              WHERE o.entity_id = %s AND r.authority_code = 'GSTN' AND o.status <> 'not_applicable'""", (made["entity_id"],)).fetchone()["n"]
    assert gst_pre == gst_made


# ------------------------------------------------------------------ sign-up
def test_signup_creates_a_person_who_signs_in_with_the_code(client, db):
    from app import auth
    email = f"signup-{uuid.uuid4().hex[:8]}@example.in"
    assert client.post("/v1/auth/signup", json={"email": email.upper(), "full_name": "  Isha   Menon "}).status_code == 204
    row = db.execute("SELECT full_name, auth_provider, email::text AS email FROM users WHERE email = %s", (email,)).fetchone()
    assert row == {"full_name": "Isha Menon", "auth_provider": "otp", "email": email}
    code = auth.SandboxOtpSender.last(email)
    r = client.post("/v1/auth/token", json={"grant_type": "otp", "email": email, "otp": code})
    assert r.status_code == 200
    assert client.get("/v1/me", headers={"Authorization": f"Bearer {r.json()['access_token']}"}).json()["entities"] == []


def test_signup_does_not_reveal_or_change_an_existing_account(client, db):
    before = db.execute("SELECT full_name FROM users WHERE email = %s", (NEW,)).fetchone()
    r = client.post("/v1/auth/signup", json={"email": NEW, "full_name": "Attacker Name"})
    assert r.status_code == 204 and r.content == b""
    assert db.execute("SELECT full_name FROM users WHERE email = %s", (NEW,)).fetchone() == before


def test_signup_input_is_checked(client):
    for body in ({"email": "not-an-email", "full_name": "A"}, {"email": "a@example.in", "full_name": "   "}, {"email": "a@example.in"}):
        assert client.post("/v1/auth/signup", json=body).status_code == 400


def test_signup_is_limited_per_address(client, monkeypatch):
    from app import ratelimit
    monkeypatch.setenv("BIZ_RATE_SIGNUP", "2")
    ratelimit.LIMITER.reset()
    codes = [client.post("/v1/auth/signup", json={"email": f"burst{i}-{uuid.uuid4().hex[:6]}@example.in", "full_name": "B"}).status_code for i in range(3)]
    assert codes == [204, 204, 429]
    ratelimit.LIMITER.reset()
