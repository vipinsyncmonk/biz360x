"""An import that is free of charge (a sample): no purchase bill, and the IGST is credit only when it is decided to be."""
import datetime as dt
import os
import uuid

import psycopg
import pytest
from psycopg.rows import dict_row

DB = os.environ["DATABASE_URL"]
ORG = "00000000-0000-0000-0000-000000f5a000"
EN = "00000000-0000-0000-0000-000000f5a001"
U_OWNER, U_VIEWER = "00000000-0000-0000-0000-000000f5a011", "00000000-0000-0000-0000-000000f5a012"
OWNER, VIEWER = "foc-owner@example.in", "foc-viewer@example.in"
TODAY = dt.date.today().isoformat()
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


def login(client, email):
    r = client.post("/v1/auth/token", json={"grant_type": "otp", "email": email, "otp": "000000"})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def url(path):
    return f"/v1/entities/{EN}{path}"


@pytest.fixture(scope="module", autouse=True)
def seed(db):
    db.execute("INSERT INTO organizations (id, name, kind) VALUES (%s, 'Foc Org', 'business_group')", (ORG,))
    db.execute("INSERT INTO entities (id, organization_id, legal_name, constitution, track) VALUES (%s, %s, 'Sample Imports Pvt Ltd', 'private_limited', 'business')", (EN, ORG))
    db.execute("INSERT INTO users (id, email, full_name) VALUES (%s, %s, 'Foc Owner'), (%s, %s, 'Foc Viewer')", (U_OWNER, OWNER, U_VIEWER, VIEWER))
    db.execute("INSERT INTO memberships (entity_id, user_id, role, accepted_at) VALUES (%s, %s, 'owner', now()), (%s, %s, 'viewer', now())", (EN, U_OWNER, EN, U_VIEWER))
    yield


def boe(number, **kw):
    return {"boe_number": number, "boe_date": TODAY, "port_code": "INBLR4", "currency": "USD", "cif_foreign": "100.00", "exchange_rate": "86.4000",
            "bcd": "864.00", "sws": "86.40", "igst": "1000.00", "other_duty": "0.00", "status": "duty_paid", "duty_paid_on": TODAY, **kw}


def imports(client, o):
    return client.get(url("/trade/summary"), headers=o).json()["imports"]


def test_an_ordinary_import_keeps_its_credit_as_before(client):
    o = login(client, OWNER)
    r = client.post(url("/bills-of-entry"), headers=o, json=boe("7000001"))
    assert r.status_code == 201, r.text
    b = r.json()
    assert (b["free_of_charge"], b["igst_credit_status"], b["igst_credit"]) == (False, "eligible", "1000.00")
    assert (imports(client, o)["igst_credit_inr"], imports(client, o)["igst_undecided_inr"], imports(client, o)["igst_not_claimed_inr"]) == ("1000.00", "0.00", "0.00")


def test_a_free_sample_leaves_the_credit_to_be_decided_and_counts_none_of_it(client):
    o = login(client, OWNER)
    r = client.post(url("/bills-of-entry"), headers=o, json=boe("7000002", free_of_charge=True))
    assert r.status_code == 201, r.text
    b = r.json()
    S["sample"] = b["id"]
    assert (b["free_of_charge"], b["igst_credit_status"], b["igst_credit"], b["purchase_bill_id"]) == (True, "undecided", "0.00", None)
    i = imports(client, o)
    assert (i["igst_credit_inr"], i["igst_undecided_inr"], i["igst_not_claimed_inr"]) == ("1000.00", "1000.00", "0.00")     # the sample's IGST waits for the decision
    assert i["duty_inr"] == "4000.80" or i["bills"] == 2                                       # the duty is still a cost of the import


def test_the_decision_moves_the_igst_between_credit_and_cost(client):
    o = login(client, OWNER)
    blocked = client.patch(url(f"/bills-of-entry/{S['sample']}"), headers=o, json={"igst_credit_status": "blocked"})
    assert blocked.status_code == 200 and (blocked.json()["igst_credit_status"], blocked.json()["igst_credit"]) == ("blocked", "0.00")
    i = imports(client, o)
    assert (i["igst_credit_inr"], i["igst_undecided_inr"], i["igst_not_claimed_inr"]) == ("1000.00", "0.00", "1000.00")
    own_use = client.patch(url(f"/bills-of-entry/{S['sample']}"), headers=o, json={"igst_credit_status": "eligible"})
    assert own_use.json()["igst_credit"] == "1000.00" and imports(client, o)["igst_credit_inr"] == "2000.00"


def test_a_sample_can_be_recorded_with_the_decision_already_made(client):
    o = login(client, OWNER)
    r = client.post(url("/bills-of-entry"), headers=o, json=boe("7000003", free_of_charge=True, igst_credit_status="blocked"))
    assert r.status_code == 201 and (r.json()["igst_credit_status"], r.json()["igst_credit"]) == ("blocked", "0.00")


def test_unpaid_duty_gives_no_credit_whatever_the_decision(client):
    o = login(client, OWNER)
    r = client.post(url("/bills-of-entry"), headers=o, json=boe("7000004", status="assessed", duty_paid_on=None, free_of_charge=True, igst_credit_status="eligible"))
    assert r.status_code == 201 and r.json()["igst_credit"] == "0.00"


def test_a_free_import_has_no_purchase_bill(client):
    o = login(client, OWNER)
    r = client.post(url("/bills-of-entry"), headers=o, json=boe("7000005", free_of_charge=True, purchase_bill_id=str(uuid.uuid4())))
    assert r.status_code == 400 and "purchase_bill_id" in str(r.json()["errors"]), r.text


def test_the_decision_is_audited_and_a_viewer_cannot_make_it(client, db):
    row = db.execute("SELECT before, after FROM audit_log WHERE entity_id = %s AND subject_id = %s AND action = 'bill_of_entry.updated' ORDER BY at LIMIT 1", (EN, S["sample"])).fetchone()
    assert row["before"]["igst_credit_status"] == "undecided" and row["after"]["igst_credit_status"] == "blocked"
    v = login(client, VIEWER)
    assert client.patch(url(f"/bills-of-entry/{S['sample']}"), headers=v, json={"igst_credit_status": "blocked"}).status_code == 403
    assert client.get(url("/bills-of-entry"), headers=v).status_code == 200


def test_a_free_import_and_each_change_of_its_decision_open_a_task_for_the_ca(client, db):
    tasks = [r["title"] for r in db.execute("SELECT title FROM tasks WHERE entity_id = %s AND source = 'advisor' ORDER BY created_at", (EN,)).fetchall()]
    assert "CA to review: IGST credit on free-of-charge import BoE 7000002 is not decided yet" in tasks
    assert "CA to review: IGST credit on free-of-charge import BoE 7000002 is not claimed (a cost)" in tasks
    assert "CA to review: IGST credit on free-of-charge import BoE 7000002 is claimed as credit" in tasks
    assert "CA to review: IGST credit on free-of-charge import BoE 7000003 is not claimed (a cost)" in tasks
    assert not any("7000001" in t for t in tasks)                                              # an ordinary import is not flagged
    o = login(client, OWNER)
    before = db.execute("SELECT count(*) AS n FROM tasks WHERE entity_id = %s AND source = 'advisor'", (EN,)).fetchone()["n"]
    client.patch(url(f"/bills-of-entry/{S['sample']}"), headers=o, json={"igst_credit_status": "eligible"})     # no change: no new task
    assert db.execute("SELECT count(*) AS n FROM tasks WHERE entity_id = %s AND source = 'advisor'", (EN,)).fetchone()["n"] == before
    assert db.execute("SELECT count(*) AS n FROM event_outbox WHERE entity_id = %s AND type = 'bill_of_entry.ca_review_needed'", (EN,)).fetchone()["n"] >= 4
