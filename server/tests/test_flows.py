"""End-to-end tests of the reference API against a real PostgreSQL 16
database built from the schema + tests/demo_and_checks.sql.

    DATABASE_URL=postgresql://postgres@/biz?host=/path/to/socket&port=5499 pytest -q
"""
import os
import uuid

import psycopg
import pytest
from fastapi.testclient import TestClient

DB = os.environ["DATABASE_URL"]
ENTITY = "00000000-0000-0000-0000-0000000000e1"
REG_KA = "00000000-0000-0000-0000-000000000071"
COASTAL = "00000000-0000-0000-0000-000000000021"
COASTAL_GST = "00000000-0000-0000-0000-000000000031"
INVOICE_212 = "00000000-0000-0000-0000-000000000051"


@pytest.fixture(scope="session", autouse=True)
def seed():
    with psycopg.connect(DB, autocommit=True) as c:
        c.execute("""INSERT INTO entity_facts (entity_id, fact_key, value_num, as_of, source)
                     VALUES (%s, 'aato', 134000000, '2026-03-31', 'books')""", (ENTITY,))
        c.execute("UPDATE jobs SET status = 'cancelled' WHERE kind = 'compliance.run_engine' AND status = 'queued'")   # tests drive the worker themselves


@pytest.fixture(scope="session")
def client():
    from app.main import app
    return TestClient(app)


def login(client, email):
    r = client.post("/v1/auth/token", json={"grant_type": "otp", "email": email, "otp": "000000"})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


@pytest.fixture(scope="session")
def ananya(client): return login(client, "ananya@example.in")      # owner
@pytest.fixture(scope="session")
def priya(client): return login(client, "priya@example.in")        # maker
@pytest.fixture(scope="session")
def rohan(client): return login(client, "rohan@example.in")        # CA partner (advisor grant: finance read, gst prepare)
@pytest.fixture(scope="session")
def stranger(client): return login(client, "stranger@example.in")


LINES = [
    {"description": "Pressure transmitter PT-400", "hsn_sac": "902620", "qty": "15", "rate": "14500.00", "gst_rate": 18},
    {"description": "NABL calibration certificate", "hsn_sac": "998346", "qty": "15", "rate": "1200.00", "gst_rate": 18},
    {"description": "Freight and transit insurance", "hsn_sac": "996511", "qty": "1", "rate": "4500.00", "gst_rate": 18},
]
DOC = {"doc_type": "tax_invoice", "doc_date": "2026-11-04", "registration_id": REG_KA, "party_id": COASTAL,
       "party_gst_id": COASTAL_GST, "lines": LINES}


def test_bad_otp_is_rejected(client):
    r = client.post("/v1/auth/token", json={"grant_type": "otp", "email": "ananya@example.in", "otp": "123456"})
    assert r.status_code == 401 and r.headers["content-type"].startswith("application/problem+json")


def test_me_lists_visible_entities(client, ananya, rohan, stranger):
    me = client.get("/v1/me", headers=ananya).json()
    assert [e["legal_name"] for e in me["entities"]] == ["Nimbus Instruments Private Limited"]
    assert me["entities"][0]["role"] == "owner"
    ca = client.get("/v1/me", headers=rohan).json()
    assert ca["entities"][0]["role"] == "advisor" and ca["organizations"][0]["name"] == "Mehta & Co"
    assert client.get("/v1/me", headers=stranger).json()["entities"] == []


def test_invisible_entity_is_404_not_403(client, stranger):
    r = client.get(f"/v1/entities/{ENTITY}/obligations", headers=stranger)
    assert r.status_code == 404 and r.json()["code"] == "not_found"


def test_obligations_list_and_cursor(client, ananya):
    r = client.get(f"/v1/entities/{ENTITY}/obligations", headers=ananya, params={"limit": 1})
    body = r.json()
    assert r.status_code == 200 and body["data"][0]["rule"]["form_code"] == "GSTR-1"
    assert body["next_cursor"] is None


def test_preview_inter_state_igst_and_checks(client, priya):
    r = client.post(f"/v1/entities/{ENTITY}/sales-documents/preview", headers=priya, json=DOC)
    p = r.json()
    assert r.status_code == 200, r.text
    assert p["is_inter_state"] is True and p["place_of_supply"] == "32"
    assert p["taxable_total"] == "240000.00" and p["igst_total"] == "43200.00" and p["cgst_total"] == "0.00"
    assert p["grand_total"] == "283200.00" and p["tds_expected"] == "240.00" and p["expected_receipt"] == "282960.00"
    assert p["next_number"] == "NI/KA/213"
    assert p["amount_in_words"] == "Rupees Two Lakh Eighty Three Thousand Two Hundred only"
    codes = {c["code"] for c in p["checks"]}
    assert {"einvoice_required", "eway_bill_required", "tds_194q"} <= codes


def test_intra_state_splits_cgst_sgst(client, priya):
    r = client.post(f"/v1/entities/{ENTITY}/sales-documents/preview", headers=priya, json={**DOC, "place_of_supply": "29"})
    p = r.json()
    assert p["is_inter_state"] is False and p["cgst_total"] == p["sgst_total"] == "21600.00" and p["igst_total"] == "0.00"


def test_b2b_without_gstin_maps_constraint_to_code(client, priya):
    r = client.post(f"/v1/entities/{ENTITY}/sales-documents", headers=priya, json={**DOC, "party_gst_id": None})
    assert r.status_code == 400 and r.json()["code"] == "b2b_needs_gstin"


def test_validation_errors_are_problem_json(client, priya):
    r = client.post(f"/v1/entities/{ENTITY}/sales-documents", headers=priya, json={**DOC, "lines": [{**LINES[0], "hsn_sac": "12"}]})
    assert r.status_code == 400 and r.json()["errors"][0]["field"].startswith("lines.0.hsn_sac")


def test_advisor_scope_blocks_sales_write(client, rohan):
    r = client.post(f"/v1/entities/{ENTITY}/sales-documents", headers=rohan, json=DOC)
    assert r.status_code == 403 and r.json()["code"] == "permission_denied"


def test_draft_issue_irn_and_idempotency(client, priya, ananya):
    from app import worker
    r = client.post(f"/v1/entities/{ENTITY}/sales-documents", headers=priya, json=DOC)
    assert r.status_code == 201, r.text
    doc = r.json()
    assert doc["status"] == "draft" and doc["number"] is None and doc["grand_total"] == "283200.00" and r.headers["ETag"]

    key = str(uuid.uuid4())
    path = f"/v1/entities/{ENTITY}/sales-documents/{doc['id']}/issue"
    j1 = client.post(path, headers={**priya, "Idempotency-Key": key}, json={})
    assert j1.status_code == 202 and j1.json()["kind"] == "einvoice.generate_irn" and j1.headers["Location"].startswith("/v1/jobs/")
    j2 = client.post(path, headers={**priya, "Idempotency-Key": key}, json={})
    assert j2.status_code == 202 and j2.json()["id"] == j1.json()["id"]                       # replay, not a second issue
    j3 = client.post(path, headers={**priya, "Idempotency-Key": key}, json={"generate_eway_bill": False})
    assert j3.status_code == 409 and j3.json()["code"] == "idempotency_conflict"
    j4 = client.post(path, headers={**priya, "Idempotency-Key": str(uuid.uuid4())}, json={})
    assert j4.status_code == 409 and j4.json()["code"] == "conflict"                          # already issued
    assert client.post(path, headers=priya, json={}).json()["code"] == "validation_failed"     # key is mandatory

    issued = client.get(f"/v1/entities/{ENTITY}/sales-documents/{doc['id']}", headers=ananya).json()
    assert issued["number"] == "NI/KA/213" and issued["status"] == "issued" and issued["einvoice"]["status"] == "pending"

    assert worker.run_once() is True
    assert worker.run_once() is False
    job = client.get(f"/v1/jobs/{j1.json()['id']}", headers=priya).json()
    assert job["status"] == "succeeded" and len(job["result"]["irn"]) == 64
    final = client.get(f"/v1/entities/{ENTITY}/sales-documents/{doc['id']}", headers=priya).json()
    assert final["einvoice"]["status"] == "generated" and final["einvoice"]["irn"] == job["result"]["irn"]


def test_receipt_allocation_and_over_settlement(client, priya):
    url = f"/v1/entities/{ENTITY}/receipts"
    too_much = {"party_id": COASTAL, "received_on": "2026-11-25", "amount": "300000.00", "method": "neft",
                "allocations": [{"invoice_id": INVOICE_212, "amount": "300000.00"}]}
    r = client.post(url, headers={**priya, "Idempotency-Key": str(uuid.uuid4())}, json=too_much)
    assert r.status_code == 409 and r.json()["code"] == "over_settlement"                     # 272,000 was left on NI/KA/212
    ok = {**too_much, "amount": "272000.00", "utr": "UTR777", "allocations": [{"invoice_id": INVOICE_212, "amount": "272000.00"}]}
    r = client.post(url, headers={**priya, "Idempotency-Key": str(uuid.uuid4())}, json=ok)
    assert r.status_code == 201 and r.json()["unallocated"] == "0.00"
    inv = client.get(f"/v1/entities/{ENTITY}/sales-documents/{INVOICE_212}", headers=priya).json()
    assert inv["payment_status"] == "paid" and inv["balance_due"] == "0.00"


def test_two_person_rule_and_approval_flow(client, ananya, priya):
    obl = client.get(f"/v1/entities/{ENTITY}/obligations", headers=ananya).json()["data"][0]["id"]
    ar = client.post(f"/v1/entities/{ENTITY}/obligations/{obl}/submit", headers=priya, json={}).json()
    assert ar["status"] == "pending"
    # the maker has no approve permission
    r = client.post(f"/v1/entities/{ENTITY}/approvals/{ar['id']}/approve", headers={**priya, "Idempotency-Key": str(uuid.uuid4())}, json={})
    assert r.status_code == 403 and r.json()["code"] == "permission_denied"
    ok = client.post(f"/v1/entities/{ENTITY}/approvals/{ar['id']}/approve", headers={**ananya, "Idempotency-Key": str(uuid.uuid4())},
                     json={"note": "Checked against the sales register"})
    assert ok.status_code == 200 and ok.json()["status"] == "approved"


def test_owner_cannot_approve_own_request(client, ananya):
    with psycopg.connect(DB, autocommit=True) as c:
        c.execute("UPDATE obligations SET status = 'in_preparation'")
    obl = client.get(f"/v1/entities/{ENTITY}/obligations", headers=ananya).json()["data"][0]["id"]
    ar = client.post(f"/v1/entities/{ENTITY}/obligations/{obl}/submit", headers=ananya, json={}).json()
    r = client.post(f"/v1/entities/{ENTITY}/approvals/{ar['id']}/approve", headers={**ananya, "Idempotency-Key": str(uuid.uuid4())}, json={})
    assert r.status_code == 403 and r.json()["code"] == "two_person_rule"


def test_audit_chain_still_intact():
    with psycopg.connect(DB) as c:
        assert c.execute("SELECT app.audit_verify()").fetchone()[0] is None
        assert c.execute("SELECT count(*) FROM audit_log").fetchone()[0] >= 8
        assert c.execute("SELECT count(*) FROM event_outbox WHERE type = 'sales_document.paid'").fetchone()[0] == 1
