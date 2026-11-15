"""Employee self-service, own details and checklist (migration 43): an employee proposes their PAN, bank account and mobile, HR confirms before the
record changes; and they tick only the checklist items that are theirs."""
import os

import psycopg
import pytest
from psycopg.rows import dict_row

DB = os.environ["DATABASE_URL"]
ORG = "00000000-0000-0000-0000-0000007b0000"
EN = "00000000-0000-0000-0000-0000007b0010"
U_OWNER, U_HR, U_SALES = ("00000000-0000-0000-0000-0000007b002%d" % i for i in (1, 2, 3))
OWNER, HR, SALES = "sd-owner@example.in", "sd-hr@example.in", "sd-sales@example.in"
EMAIL, STRANGER = "sd-emp@example.in", "sd-stranger@example.in"
ACCOUNT = "123456789012"
S: dict = {}


@pytest.fixture(scope="module")
def db():
    with psycopg.connect(DB, autocommit=True, row_factory=dict_row) as c:
        yield c


@pytest.fixture(scope="module")
def client():
    from fastapi.testclient import TestClient
    from app.main import app
    return TestClient(app)


@pytest.fixture(scope="module", autouse=True)
def seed(db):
    x = db.execute
    x("INSERT INTO organizations (id, name, kind) VALUES (%s, 'SD Org', 'business_group')", (ORG,))
    x("INSERT INTO entities (id, organization_id, legal_name, constitution, track) VALUES (%s, %s, 'Self Details Pvt Ltd', 'private_limited', 'business')", (EN, ORG))
    x("INSERT INTO users (id, email, full_name) VALUES (%s, %s, 'Sd Owner'), (%s, %s, 'Sd HR'), (%s, %s, 'Sd Sales'), (gen_random_uuid(), %s, 'A Stranger')",
      (U_OWNER, OWNER, U_HR, HR, U_SALES, SALES, STRANGER))
    x("""INSERT INTO memberships (entity_id, user_id, role, scopes, accepted_at) VALUES (%s, %s, 'owner', NULL, now()),
         (%s, %s, 'approver', '{"hr": "approve"}', now()), (%s, %s, 'maker', '{"sales": "write"}', now())""", (EN, U_OWNER, EN, U_HR, EN, U_SALES))
    yield


def login(client, email):
    r = client.post("/v1/auth/token", json={"grant_type": "otp", "email": email, "otp": "000000"})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def url(path):
    return f"/v1/entities/{EN}{path}"


def me(path=""):
    return f"/v1/self/{S['emp']}{path}"


def test_setup_an_employee_with_their_own_page(client):
    o, hr = login(client, OWNER), login(client, HR)
    r = client.post(url("/employees/onboard"), headers=o, json={"full_name": "Ira Shah", "date_of_joining": "2026-01-05", "work_state": "29", "ctc_annual": "900000", "email": EMAIL})
    assert r.status_code == 201, r.text
    S["emp"] = r.json()["employee"]["id"]
    assert client.post(url(f"/employees/{S['emp']}/self-service"), headers=hr).status_code == 200
    S["h"] = login(client, EMAIL)
    assert client.post(f"/v1/me/employments/{S['emp']}/accept", headers=S["h"]).status_code == 200
    got = {t["key"]: (t["by_employee"], t["auto"]) for t in client.get(me(), headers=S["h"]).json()["onboarding"]["checklist"]}
    assert got["pf_form"] == (True, None) and got["tax_regime"] == (True, None) and got["offer"] == (False, None) and got["pan"][1] == "pan"


def test_they_tick_only_their_own_items(client):
    h = S["h"]
    r = client.put(me("/checklist/pf_form"), headers=h, json={"done": True})
    assert r.status_code == 200, r.text
    assert {t["key"]: t["done"] for t in r.json()["checklist"]}["pf_form"] is True
    assert client.put(me("/checklist/pf_form"), headers=h, json={"done": True}).status_code == 200                      # again: nothing changes
    assert client.put(me("/checklist/offer"), headers=h, json={"done": True}).status_code == 409                       # HR's item
    assert client.put(me("/checklist/pan"), headers=h, json={"done": True}).status_code == 409                         # read from the record
    assert client.put(me("/checklist/nope"), headers=h, json={"done": True}).status_code == 404
    r = client.put(me("/checklist/pf_form"), headers=h, json={"done": False})
    assert {t["key"]: t["done"] for t in r.json()["checklist"]}["pf_form"] is False
    assert client.put(me("/checklist/pf_form"), headers=login(client, STRANGER), json={"done": True}).status_code == 404   # someone else's page


def test_asking_for_a_change_leaves_the_record_alone_until_hr_confirms(client, db):
    h = S["h"]
    assert client.get(me("/details"), headers=h).json()["status"] == "none"
    assert client.put(me("/details"), headers=h, json={}).status_code == 400
    assert client.put(me("/details"), headers=h, json={"mobile": "98123"}).status_code in (400, 422)
    assert client.put(me("/details"), headers=h, json={"pan": "ABCD1234"}).status_code in (400, 422)
    r = client.put(me("/details"), headers=h, json={"pan": "abcpi1234k", "mobile": "+919812345678"})
    assert r.status_code == 200, r.text
    first = r.json()["request"]["id"]
    r = client.put(me("/details"), headers=h, json={"pan": "abcpi1234k", "bank_account": {"account_no": ACCOUNT, "ifsc": "HDFC0001234"}})
    d = r.json()
    assert d["status"] == "pending" and d["request"]["id"] != first and (d["request"]["pan_last4"], d["request"]["bank_last4"], d["request"]["mobile"]) == ("234K", "9012", None)
    assert d["current"] == {"pan_last4": None, "bank_on_file": False, "bank_ifsc": None, "mobile": None}                 # the record has not changed
    assert db.execute("SELECT status FROM employee_detail_requests WHERE id = %s", (first,)).fetchone()["status"] == "withdrawn"
    raw = db.execute("SELECT bank_account_enc FROM employee_detail_requests WHERE id = %s", (d["request"]["id"],)).fetchone()["bank_account_enc"]
    assert ACCOUNT.encode() not in bytes(raw)                                                                          # stored encrypted
    assert db.execute("SELECT pan FROM employees WHERE id = %s", (S["emp"],)).fetchone()["pan"] is None


def test_hr_sees_the_request_beside_the_record_and_confirms_it(client, db):
    hr, sales = login(client, HR), login(client, SALES)
    assert client.get(url("/employee-detail-requests"), headers=sales).status_code == 403
    rows = client.get(url("/employee-detail-requests"), headers=hr).json()["data"]
    assert len(rows) == 1
    r = rows[0]
    assert (r["employee_name"], r["pan"], r["bank_account"], r["bank_ifsc"], r["current_pan"], r["current_bank_account"]) == ("Ira Shah", "ABCPI1234K", "…9012", "HDFC0001234", None, None)
    S["req"] = r["id"]
    assert client.post(url(f"/employee-detail-requests/{S['req']}/confirm"), headers=sales, json={}).status_code == 403
    done = client.post(url(f"/employee-detail-requests/{S['req']}/confirm"), headers=hr, json={"note": "Matches the cancelled cheque"})
    assert done.status_code == 200 and done.json()["status"] == "confirmed", done.text
    e = db.execute("SELECT pan, bank_ifsc, bank_account_enc IS NOT NULL AS bank FROM employees WHERE id = %s", (S["emp"],)).fetchone()
    assert (e["pan"], e["bank_ifsc"], e["bank"]) == ("ABCPI1234K", "HDFC0001234", True)
    mine = client.get(me("/details"), headers=S["h"]).json()
    assert mine["status"] == "confirmed" and mine["current"]["pan_last4"] == "234K" and mine["current"]["bank_on_file"] is True and mine["request"]["hr_note"] == "Matches the cancelled cheque"
    tasks = {t["key"]: t["done"] for t in client.get(me(), headers=S["h"]).json()["onboarding"]["checklist"]}
    assert tasks["pan"] and tasks["bank"]                                                                              # the auto items tick themselves
    assert client.post(url(f"/employee-detail-requests/{S['req']}/confirm"), headers=hr, json={}).status_code == 409
    assert db.execute("SELECT count(*) AS n FROM audit_log WHERE entity_id = %s AND action = 'employee_details.confirmed'", (EN,)).fetchone()["n"] == 1
    assert ACCOUNT not in str(db.execute("SELECT after, before FROM audit_log WHERE action = 'employee_details.confirmed' AND entity_id = %s", (EN,)).fetchone())


def test_rejecting_and_withdrawing(client):
    h, hr = S["h"], login(client, HR)
    client.put(me("/details"), headers=h, json={"bank_account": {"account_no": "999988887777", "ifsc": "ICIC0004321"}})
    rid = client.get(url("/employee-detail-requests"), headers=hr).json()["data"][0]["id"]
    assert client.post(url(f"/employee-detail-requests/{rid}/reject"), headers=hr, json={}).status_code in (400, 422)
    r = client.post(url(f"/employee-detail-requests/{rid}/reject"), headers=hr, json={"note": "Send a cancelled cheque first"})
    assert r.status_code == 200 and r.json()["status"] == "rejected"
    mine = client.get(me("/details"), headers=h).json()
    assert mine["status"] == "rejected" and mine["request"]["hr_note"] == "Send a cancelled cheque first" and mine["current"]["bank_ifsc"] == "HDFC0001234"
    client.put(me("/details"), headers=h, json={"mobile": "+919800000001"})
    assert client.delete(me("/details"), headers=h).json()["status"] == "withdrawn"
    assert client.get(url("/employee-detail-requests"), headers=hr).json()["data"] == []


def test_the_requester_cannot_decide_and_the_role_reads_no_table(client, db):
    # an employee who is also an HR member is still not allowed to confirm their own change
    db.execute("UPDATE employees SET user_id = (SELECT id FROM users WHERE email = %s) WHERE id = %s", (HR, S["emp"]))
    hr = login(client, HR)
    assert client.put(me("/details"), headers=hr, json={"mobile": "+919800000002"}).status_code == 200
    rid = client.get(url("/employee-detail-requests"), headers=hr).json()["data"][0]["id"]
    r = client.post(url(f"/employee-detail-requests/{rid}/confirm"), headers=hr, json={})
    assert r.status_code == 403 and r.json()["code"] == "two_person_rule", r.text
    assert client.post(url(f"/employee-detail-requests/{rid}/confirm"), headers=login(client, OWNER), json={}).status_code == 200
    with psycopg.connect(DB, autocommit=False, row_factory=dict_row) as c:
        c.execute("SET LOCAL ROLE biz_self")
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            c.execute("SELECT 1 FROM employee_detail_requests")
        c.rollback()
        c.execute("SET LOCAL ROLE biz_app")
        c.execute("SELECT set_config('app.user_id', %s, true)", (U_SALES,))
        assert c.execute("SELECT count(*) AS n FROM employee_detail_requests").fetchone()["n"] == 0        # no payroll or HR scope: nothing
