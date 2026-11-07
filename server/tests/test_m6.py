"""M6 end to end: portal connections and OTP-paused jobs, e-invoice cancellation, e-way bills, filing obligations with their
portals, Form 26AS, the Account Aggregator, and registry lookups. All portal calls go through the sandbox adapters.

Own entity (EX); tests run in order and build on one another.
"""
import datetime as dt
import json
import os
import tempfile
import uuid

import psycopg
import pytest
from psycopg.rows import dict_row

from app import adapters

DB = os.environ["DATABASE_URL"]
ORG = "00000000-0000-0000-0000-0000000000a1"
EX = "00000000-0000-0000-0000-0000000000f9"
U_OWNER, U_MAKER, U_VIEWER, U_APPROVER = ("00000000-0000-0000-0000-00000000020%d" % i for i in (1, 2, 3, 4))
REG, TAN, EPF, PARTY, PARTY_GST = ("00000000-0000-0000-0000-00000000021%d" % i for i in (1, 2, 3, 4, 5))
BANK_L1, BANK1, BANK_L2, BANK2 = ("00000000-0000-0000-0000-00000000022%d" % i for i in (1, 2, 3, 4))
OWNER, MAKER, VIEWER, APPROVER = "m6-owner@example.in", "m6-maker@example.in", "m6-viewer@example.in", "m6-approver@example.in"
GSTIN = "29AAAPX6666Q1Z5"
S: dict = {}
KINDS = "('compliance.run_engine','collections.dunning','collections.recurring','purchases.msme_alerts','einvoice.generate_irn','portal.check_sessions','aa.sync')"
AS_OF = dt.date(2026, 11, 6)


@pytest.fixture(scope="module")
def db():
    with psycopg.connect(DB, autocommit=True, row_factory=dict_row) as c:
        yield c


@pytest.fixture(scope="module", autouse=True)
def entity(db):
    S["secrets"] = tempfile.mkdtemp()
    os.environ["BIZ_SECRETS_DIR"] = S["secrets"]
    adapters.reset()
    db.execute("""INSERT INTO entities (id, organization_id, legal_name, constitution, track, pan, cin, incorporated_on, registered_state)
                  VALUES (%s, %s, 'M6 Test Pvt Ltd', 'private_limited', 'business', 'AAAPX6666Q', 'U26510KA2019PTC666666', '2019-08-14', '29')""", (EX, ORG))
    db.execute("""INSERT INTO entity_registrations (id, entity_id, kind, number, state_code, gst_filing_frequency) VALUES (%s, %s, 'gstin', %s, '29', 'monthly'),
                  (%s, %s, 'tan', 'BLRM66666C', '29', NULL), (%s, %s, 'epf', 'KNBLR0066666000', '29', NULL)""", (REG, EX, GSTIN, TAN, EX, EPF, EX))
    db.execute("INSERT INTO document_series (entity_id, registration_id, doc_type, fy, prefix) VALUES (%s, %s, 'tax_invoice', '2026-27', 'X6/')", (EX, REG))
    db.execute("INSERT INTO users (id, email, full_name) VALUES (%s, %s, 'M6 Owner'), (%s, %s, 'M6 Maker'), (%s, %s, 'M6 Viewer'), (%s, %s, 'M6 Approver')",
               (U_OWNER, OWNER, U_MAKER, MAKER, U_VIEWER, VIEWER, U_APPROVER, APPROVER))
    db.execute("INSERT INTO memberships (entity_id, user_id, role) VALUES (%s, %s, 'owner'), (%s, %s, 'maker'), (%s, %s, 'viewer'), (%s, %s, 'approver')",
               (EX, U_OWNER, EX, U_MAKER, EX, U_VIEWER, EX, U_APPROVER))
    db.execute("INSERT INTO parties (id, entity_id, legal_name, is_customer, payment_terms_days) VALUES (%s, %s, 'Coastal Grid Utilities', true, 30)", (PARTY, EX))
    db.execute("INSERT INTO party_gst_registrations (id, party_id, gstin, state_code, is_default) VALUES (%s, %s, '32AABCX9999K1ZP', '32', true)", (PARTY_GST, PARTY))
    db.execute("""INSERT INTO ledger_accounts (id, entity_id, code, name, type, subtype) VALUES (%s, %s, '1100', 'HDFC current', 'asset', 'bank'), (%s, %s, '1101', 'ICICI current', 'asset', 'bank2')""",
               (BANK_L1, EX, BANK_L2, EX))
    db.execute("""INSERT INTO bank_accounts (id, entity_id, ledger_account_id, bank_name, account_last4, ifsc, kind) VALUES (%s, %s, %s, 'HDFC', '4412', 'HDFC0000123', 'current'),
                  (%s, %s, %s, 'ICICI', '0937', 'ICIC0000456', 'current')""", (BANK1, EX, BANK_L1, BANK2, EX, BANK_L2))
    db.execute("INSERT INTO entity_facts (entity_id, fact_key, value_num, as_of, source) VALUES (%s, 'aato', 60000000, '2026-03-31', 'books'), (%s, 'headcount', 25, '2026-04-01', 'user')", (EX, EX))
    yield
    db.execute(f"UPDATE jobs SET status = 'cancelled' WHERE status IN ('queued', 'waiting_for_otp') AND kind IN {KINDS}")
    adapters.reset()
    os.environ.pop("BIZ_SECRETS_DIR", None)


@pytest.fixture(scope="module")
def client():
    from fastapi.testclient import TestClient
    from app.main import app
    return TestClient(app)


def login(client, email):
    r = client.post("/v1/auth/token", json={"grant_type": "otp", "email": email, "otp": "000000"})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


@pytest.fixture(scope="module")
def h(client): return login(client, OWNER)
@pytest.fixture(scope="module")
def hm(client): return login(client, MAKER)
@pytest.fixture(scope="module")
def hv(client): return login(client, VIEWER)
@pytest.fixture(scope="module")
def ha(client): return login(client, APPROVER)


def url(path):
    return f"/v1/entities/{EX}{path}"


def key():
    return {"Idempotency-Key": str(uuid.uuid4())}


NOISE = "('compliance.run_engine','collections.dunning','collections.recurring','purchases.msme_alerts')"


def run_worker(db):
    from app import worker
    db.execute(f"UPDATE jobs SET status = 'cancelled' WHERE status = 'queued' AND kind IN {NOISE}")
    n = 0
    while worker.run_once():
        n += 1
        assert n < 60


def job(db, jid):
    return db.execute("SELECT status, result, error, attempts, payload FROM jobs WHERE id = %s", (jid,)).fetchone()


def api_job(client, h, jid):
    return client.get(f"/v1/jobs/{jid}", headers=h).json()


# ================================================================== portal connections
def test_connections_store_secrets_outside_the_database(client, h, hv, db):
    body = {"portal": "gstn", "registration_id": REG, "via": "GSP", "credentials": {"username": "m6.gst", "password": "correct-horse-battery"}}
    assert client.post(url("/portal-connections"), headers=hv, json=body).json()["code"] == "permission_denied"
    assert client.post(url("/portal-connections"), headers=h, json={**body, "registration_id": str(uuid.uuid4())}).status_code == 400
    assert client.post(url("/portal-connections"), headers=h, json={**body, "portal": "nonesuch"}).status_code == 400
    r = client.post(url("/portal-connections"), headers=h, json=body)
    assert r.status_code == 201, r.text
    c = r.json()
    assert c["status"] == "connected" and c["via"] == "GSP" and c["owner_user_id"] == U_OWNER and c["session_expires_at"] is None
    assert "credentials" not in c and "password" not in json.dumps(c)
    ref = db.execute("SELECT credential_ref FROM portal_connections WHERE id = %s", (c["id"],)).fetchone()["credential_ref"]
    assert ref.startswith(f"portal/{EX}/gstn/") and adapters.secrets.get(ref) == body["credentials"]
    assert b"correct-horse" not in b"".join(open(os.path.join(S["secrets"], f), "rb").read() for f in os.listdir(S["secrets"]))     # encrypted on disk
    assert client.post(url("/portal-connections"), headers=h, json=body).json()["code"] == "conflict"
    bare = client.post(url("/portal-connections"), headers=h, json={"portal": "udyam"}).json()
    assert bare["status"] == "not_connected"
    S["gst_conn"], S["udyam_conn"] = c, bare
    assert {x["portal"] for x in client.get(url("/portal-connections"), headers=hv).json()["data"]} == {"gstn", "udyam"}
    rot = client.patch(url(f"/portal-connections/{c['id']}"), headers=h, json={"via": "ASP", "credentials": {"username": "m6.gst", "password": "rotated"}})
    assert rot.status_code == 200 and rot.json()["via"] == "ASP" and adapters.secrets.get(ref)["password"] == "rotated"
    assert client.patch(url(f"/portal-connections/{c['id']}"), headers=h, json={"via": "carrier pigeon"}).status_code == 400
    up = client.patch(url(f"/portal-connections/{bare['id']}"), headers=h, json={"credentials": {"u": "x"}})
    assert up.json()["status"] == "connected"                                                                    # adding credentials connects it
    assert client.get(url(f"/portal-connections/{uuid.uuid4()}"), headers=h).status_code == 404


# ================================================================== OTP-paused jobs
def test_a_session_renewal_waits_for_the_otp(client, h, hv, db):
    c = S["gst_conn"]
    assert client.post(url(f"/portal-connections/{S['udyam_conn']['id']}/renew"), headers=h).json()["code"] == "conflict"         # udyam has no OTP session
    r = client.post(url(f"/portal-connections/{c['id']}/renew"), headers=h)
    assert r.status_code == 202 and r.json()["kind"] == "portal.renew_session"
    run_worker(db)
    j = api_job(client, h, r.json()["id"])
    assert j["status"] == "waiting_for_otp" and j["progress"] == 0.5 and j["finished_at"] is None
    pl = job(db, r.json()["id"])["payload"]
    assert pl["otp_session"] and pl["otp_channel"] == "mobile" and "otp_enc" not in pl
    assert job(db, r.json()["id"])["attempts"] == 0                                                              # waiting is not an attempt
    assert client.post(f"/v1/jobs/{r.json()['id']}/otp", headers=h, json={"otp": "12ab56"}).status_code == 400
    assert client.post(f"/v1/jobs/{r.json()['id']}/otp", headers=hv, json={"otp": "123456"}).json()["code"] == "permission_denied"   # a viewer did not start it
    S["renew_job"] = r.json()["id"]


def test_a_wrong_otp_fails_the_job_without_retries(client, h, db):
    jid = S["renew_job"]
    q = client.post(f"/v1/jobs/{jid}/otp", headers=h, json={"otp": "000000"})
    assert q.status_code == 200 and q.json()["status"] == "queued"
    pl = job(db, jid)["payload"]
    assert "otp_enc" in pl and "000000" not in json.dumps(pl)                                                     # the OTP is stored encrypted
    run_worker(db)
    done = job(db, jid)
    assert done["status"] == "failed" and done["error"]["code"] == "portal_rejected" and done["error"]["portal_code"] == "OTP" and "OTP" in done["error"]["detail"] and done["attempts"] == 1
    assert "otp_enc" not in done["payload"] and "otp_session" not in done["payload"]                             # nothing sensitive is left behind
    assert db.execute("SELECT last_error FROM portal_connections WHERE id = %s", (S["gst_conn"]["id"],)).fetchone()["last_error"].startswith("The OTP is incorrect")
    assert client.post(f"/v1/jobs/{jid}/otp", headers=h, json={"otp": "123456"}).json()["code"] == "conflict"       # no longer waiting


def test_the_right_otp_opens_the_session(client, h, db):
    c = S["gst_conn"]
    r = client.post(url(f"/portal-connections/{c['id']}/renew"), headers=h)
    run_worker(db)
    assert api_job(client, h, r.json()["id"])["status"] == "waiting_for_otp"
    assert client.post(f"/v1/jobs/{r.json()['id']}/otp", headers=h, json={"otp": "123456"}).json()["status"] == "queued"
    run_worker(db)
    j = api_job(client, h, r.json()["id"])
    assert j["status"] == "succeeded" and j["result"]["session_expires_at"]
    got = client.get(url(f"/portal-connections/{c['id']}"), headers=h).json()
    assert got["status"] == "expiring" and got["last_error"] is None                                              # a GST session lasts 6 hours: renew daily
    exp = dt.datetime.fromisoformat(got["session_expires_at"])
    assert dt.timedelta(hours=5) < exp - dt.datetime.now(dt.timezone.utc) <= dt.timedelta(hours=6, seconds=10)


def test_cancelling_a_waiting_job(client, h, hv, db):
    r = client.post(url(f"/portal-connections/{S['gst_conn']['id']}/renew"), headers=h)
    run_worker(db)
    assert client.post(f"/v1/jobs/{r.json()['id']}/cancel", headers=hv).json()["code"] == "permission_denied"
    c = client.post(f"/v1/jobs/{r.json()['id']}/cancel", headers=h)
    assert c.status_code == 200 and c.json()["status"] == "cancelled" and c.json()["finished_at"]
    assert client.post(f"/v1/jobs/{r.json()['id']}/cancel", headers=h).json()["code"] == "conflict"
    assert client.post(f"/v1/jobs/{uuid.uuid4()}/otp", headers=h, json={"otp": "123456"}).status_code == 404
    assert db.execute("SELECT last_error FROM portal_connections WHERE id = %s", (S["gst_conn"]["id"],)).fetchone()["last_error"] == "cancelled"


def test_the_session_upkeep_job_expires_sessions_and_warns_once(client, h, db):
    c = S["gst_conn"]
    db.execute("UPDATE portal_connections SET session_expires_at = now() - interval '1 hour', status = 'connected' WHERE id = %s", (c["id"],))
    q = lambda: db.execute("INSERT INTO jobs (entity_id, kind, subject, subject_id) VALUES (%s, 'portal.check_sessions', 'entities', %s) RETURNING id", (EX, EX)).fetchone()["id"]
    first = q()
    from app import worker
    db.execute("UPDATE jobs SET status = 'cancelled' WHERE status = 'queued' AND id <> %s AND kind IN " + KINDS, (first,))
    while worker.run_once():
        pass
    assert job(db, first)["result"]["expired"] == 1
    assert client.get(url(f"/portal-connections/{c['id']}"), headers=h).json()["status"] == "expired"
    ev = db.execute("SELECT data FROM event_outbox WHERE entity_id = %s AND type = 'portal.session_expiring' AND subject_id = %s", (EX, c["id"])).fetchall()
    assert [e["data"]["state"] for e in ev] == ["expired"]
    second = q()
    while worker.run_once():
        pass
    assert job(db, second)["result"]["expired"] == 0 and len(db.execute("SELECT 1 FROM event_outbox WHERE entity_id = %s AND type = 'portal.session_expiring'", (EX,)).fetchall()) == 1


def test_revoking_deletes_the_secret(client, h, db):
    c = S["gst_conn"]
    ref = db.execute("SELECT credential_ref FROM portal_connections WHERE id = %s", (c["id"],)).fetchone()["credential_ref"]
    assert adapters.secrets.get(ref)
    assert client.delete(url(f"/portal-connections/{c['id']}"), headers=h).status_code == 204
    assert adapters.secrets.get(ref) is None
    got = client.get(url(f"/portal-connections/{c['id']}"), headers=h).json()
    assert got["status"] == "revoked" and got["session_expires_at"] is None
    assert client.post(url(f"/portal-connections/{c['id']}/renew"), headers=h).json()["code"] == "conflict"
    assert client.patch(url(f"/portal-connections/{c['id']}"), headers=h, json={"via": "GSP"}).json()["code"] == "conflict"
    assert client.delete(url(f"/portal-connections/{uuid.uuid4()}"), headers=h).status_code == 404


# ================================================================== e-invoice: failure and cancellation
def draft_invoice(client, h, qty="10", rate="10000.00"):
    d = client.post(url("/sales-documents"), headers=h, json={"doc_type": "tax_invoice", "doc_date": "2026-11-05", "registration_id": REG, "party_id": PARTY, "party_gst_id": PARTY_GST,
                                                              "lines": [{"description": "Gauges", "hsn_sac": "902620", "qty": qty, "rate": rate, "gst_rate": 18}]})
    assert d.status_code == 201, d.text
    return d.json()


def issue(client, h, doc_id):
    r = client.post(url(f"/sales-documents/{doc_id}/issue"), headers={**h, **key()}, json={})
    assert r.status_code == 202, r.text
    return r.json()


def test_irn_generated_then_cancelled_within_24_hours(client, h, db):
    d = draft_invoice(client, h)
    j = issue(client, h, d["id"])
    assert j["kind"] == "einvoice.generate_irn"
    run_worker(db)
    doc = client.get(url(f"/sales-documents/{d['id']}"), headers=h).json()
    assert doc["einvoice"]["status"] == "generated" and len(doc["einvoice"]["irn"]) == 64
    S["inv"] = doc
    assert client.post(url(f"/sales-documents/{d['id']}/cancel"), headers={**h, **key()}, json={"reason": "bogus"}).status_code == 400
    assert client.post(url(f"/sales-documents/{d['id']}/cancel"), headers=h, json={"reason": "duplicate"}).json()["code"] == "validation_failed"     # key is mandatory
    k = key()
    c = client.post(url(f"/sales-documents/{d['id']}/cancel"), headers={**h, **k}, json={"reason": "duplicate", "remark": "Raised twice"})
    assert c.status_code == 202 and c.json()["kind"] == "einvoice.cancel_irn"
    assert client.post(url(f"/sales-documents/{d['id']}/cancel"), headers={**h, **k}, json={"reason": "duplicate", "remark": "Raised twice"}).json()["id"] == c.json()["id"]
    run_worker(db)
    after = client.get(url(f"/sales-documents/{d['id']}"), headers=h).json()
    assert after["status"] == "cancelled" and after["einvoice"]["status"] == "cancelled"
    jr = db.execute("SELECT status FROM journal_entries WHERE source_id = %s AND source = 'sales_document' ORDER BY created_at", (d["id"],)).fetchall()
    assert [x["status"] for x in jr] == ["reversed", "posted"]                                                   # the original journal is reversed, not deleted
    assert client.post(url(f"/sales-documents/{d['id']}/cancel"), headers={**h, **key()}, json={"reason": "duplicate"}).json()["code"] == "conflict"


def test_cancellation_rules(client, h, db):
    old = draft_invoice(client, h)
    issue(client, h, old["id"])
    run_worker(db)
    db.execute("UPDATE sales_documents SET irn_ack_at = now() - interval '25 hours' WHERE id = %s", (old["id"],))
    r = client.post(url(f"/sales-documents/{old['id']}/cancel"), headers={**h, **key()}, json={"reason": "other"})
    assert r.status_code == 409 and "24 hours" in r.json()["title"]
    paid = draft_invoice(client, h)
    issue(client, h, paid["id"])
    run_worker(db)
    client.post(url("/receipts"), headers={**h, **key()}, json={"party_id": PARTY, "received_on": "2026-11-06", "amount": "1000.00", "method": "neft", "allocations": [{"invoice_id": paid["id"], "amount": "1000.00"}]})
    assert client.post(url(f"/sales-documents/{paid['id']}/cancel"), headers={**h, **key()}, json={"reason": "other"}).json()["code"] == "conflict"
    pending = draft_invoice(client, h)
    issue(client, h, pending["id"])                                                                              # IRN job still queued
    assert client.post(url(f"/sales-documents/{pending['id']}/cancel"), headers={**h, **key()}, json={"reason": "other"}).json()["code"] == "conflict"
    assert client.post(url(f"/sales-documents/{uuid.uuid4()}/cancel"), headers={**h, **key()}, json={"reason": "other"}).status_code == 404
    run_worker(db)
    S["eway_doc"] = paid


def test_a_portal_refusal_is_final_and_marks_the_irn_failed(client, h, db):
    db.execute("UPDATE document_series SET prefix = 'REJECT/' WHERE entity_id = %s AND doc_type = 'tax_invoice'", (EX,))
    d = draft_invoice(client, h)
    j = issue(client, h, d["id"])
    run_worker(db)
    done = job(db, j["id"])
    assert done["status"] == "failed" and done["attempts"] == 1 and done["error"]["code"] == "portal_rejected" and done["error"]["portal_code"] == "2150" and "Duplicate IRN" in done["error"]["detail"]       # no retries for a 4xx
    assert client.get(url(f"/sales-documents/{d['id']}"), headers=h).json()["einvoice"]["status"] == "failed"
    assert db.execute("SELECT count(*) AS n FROM event_outbox WHERE entity_id = %s AND type = 'job.failed' AND subject_id = %s", (EX, j["id"])).fetchone()["n"] == 1
    db.execute("UPDATE document_series SET prefix = 'X6/' WHERE entity_id = %s AND doc_type = 'tax_invoice'", (EX,))


def test_a_transient_failure_is_retried_with_backoff(client, h, db):
    class Flaky(adapters.SandboxIrp):
        calls = 0

        def generate_irn(self, payload):
            Flaky.calls += 1
            if Flaky.calls < 3:
                raise adapters.PortalUnavailable("IRP is busy")
            return super().generate_irn(payload)
    adapters.use("irp", Flaky())
    try:
        d = draft_invoice(client, h)
        j = issue(client, h, d["id"])
        from app import worker
        for _ in range(3):
            db.execute("UPDATE jobs SET run_after = now() WHERE id = %s", (j["id"],))
            run_worker(db)
            if _ < 2:
                st = job(db, j["id"])
                assert st["status"] == "queued" and st["error"]["code"] == "portal_unavailable" and st["attempts"] == _ + 1
        assert job(db, j["id"])["status"] == "succeeded" and Flaky.calls == 3
        assert client.get(url(f"/sales-documents/{d['id']}"), headers=h).json()["einvoice"]["status"] == "generated"
    finally:
        adapters.reset()


# ================================================================== e-way bills
def eway_body(**kw):
    return {"sales_document_id": S["eway_doc"]["id"], "from_pincode": "560058", "to_pincode": "682001", "distance_km": 520, "transporter_gstin": "29AAFCD1234K1ZM", **kw}


def test_eway_bill_lifecycle(client, h, hv, db):
    assert client.post(url("/eway-bills"), headers=hv, json=eway_body()).json()["code"] == "permission_denied"
    assert client.post(url("/eway-bills"), headers=h, json=eway_body(to_pincode="12345")).status_code == 400
    assert client.post(url("/eway-bills"), headers=h, json=eway_body(distance_km=5000)).status_code == 400
    assert client.post(url("/eway-bills"), headers=h, json=eway_body(vehicle_no="not a plate")).status_code == 400
    assert client.post(url("/eway-bills"), headers=h, json=eway_body(sales_document_id=str(uuid.uuid4()))).status_code == 404
    small = draft_invoice(client, h, qty="1", rate="1000.00")
    issue(client, h, small["id"])
    run_worker(db)
    below = client.post(url("/eway-bills"), headers=h, json=eway_body(sales_document_id=small["id"]))
    assert below.status_code == 400 and "50,000" in below.json()["title"]
    draft = draft_invoice(client, h)
    assert client.post(url("/eway-bills"), headers=h, json=eway_body(sales_document_id=draft["id"])).json()["code"] == "conflict"       # not issued yet
    r = client.post(url("/eway-bills"), headers=h, json=eway_body(vehicle_no="ka 01 ab 1234"))
    assert r.status_code == 201, r.text
    e = r.json()
    assert (e["status"], e["ewb_no"], e["vehicle_no"], e["goods_value"], e["purpose"]) == ("draft", None, "KA01AB1234", "118000.00", "supply")
    assert client.post(url("/eway-bills"), headers=h, json=eway_body()).json()["code"] == "conflict"                                     # one open bill per document and purpose
    assert client.post(url(f"/eway-bills/{e['id']}/vehicle"), headers={**h, **key()}, json={"vehicle_no": "MH12AB9999"}).json()["code"] == "conflict"   # Part B only after generation
    run_worker(db)
    got = {x["id"]: x for x in client.get(url("/eway-bills"), headers=h, params={"status": "generated"}).json()["data"]}[e["id"]]
    assert len(got["ewb_no"]) == 12 and got["ewb_no"].isdigit()
    valid = dt.datetime.fromisoformat(got["valid_until"]) - dt.datetime.now(dt.timezone.utc)
    assert dt.timedelta(days=2) < valid <= dt.timedelta(days=3, seconds=5)                                                               # 520 km: three days
    S["eway"] = got


def test_eway_vehicle_update_and_expiry(client, h, db):
    e = S["eway"]
    assert client.post(url(f"/eway-bills/{e['id']}/vehicle"), headers={**h, **key()}, json={"vehicle_no": "bad"}).status_code == 400
    k = key()
    r = client.post(url(f"/eway-bills/{e['id']}/vehicle"), headers={**h, **k}, json={"vehicle_no": "tn 09 cd 4321", "reason": "Breakdown"})
    assert r.status_code == 202 and client.post(url(f"/eway-bills/{e['id']}/vehicle"), headers={**h, **k}, json={"vehicle_no": "tn 09 cd 4321", "reason": "Breakdown"}).json()["id"] == r.json()["id"]
    run_worker(db)
    assert db.execute("SELECT vehicle_no FROM eway_bills WHERE id = %s", (e["id"],)).fetchone()["vehicle_no"] == "TN09CD4321"
    db.execute("UPDATE eway_bills SET valid_until = now() - interval '1 hour' WHERE id = %s", (e["id"],))
    jid = db.execute("INSERT INTO jobs (entity_id, kind, subject, subject_id) VALUES (%s, 'portal.check_sessions', 'entities', %s) RETURNING id", (EX, EX)).fetchone()["id"]
    run_worker(db)
    assert job(db, jid)["result"]["eway_expired"] == 1
    assert [x["status"] for x in client.get(url("/eway-bills"), headers=h, params={"status": "expired"}).json()["data"]] == ["expired"]
    assert client.post(url(f"/eway-bills/{e['id']}/vehicle"), headers={**h, **key()}, json={"vehicle_no": "KA01AB1234"}).json()["code"] == "conflict"


# ================================================================== filing obligations with their portals
def test_obligations_exist_and_filing_needs_approval(client, h, ha, hv, db):
    from app import engine
    with db.transaction():
        engine.run_engine(db.cursor(), EX, "manual", AS_OF)
    rows = db.execute("""SELECT r.code, o.id, o.period_label FROM obligations o JOIN compliance_rules r ON r.id = o.rule_id WHERE o.entity_id = %s
                          AND r.code IN ('MCA.AOC4', 'EPF.ECR.MONTHLY', 'TDS.RETURN.QUARTERLY', 'TDS.DEPOSIT.MONTHLY', 'GST.GSTR1.MONTHLY') ORDER BY o.period_start""", (EX,)).fetchall()
    ob = {}
    for r in rows:
        if r["code"] == "GST.GSTR1.MONTHLY" and r["period_label"] != "Oct 2026":
            continue
        ob.setdefault(r["code"], r["id"])
    S["ob"] = ob
    assert {"MCA.AOC4", "EPF.ECR.MONTHLY", "GST.GSTR1.MONTHLY"} <= set(ob)
    f = lambda headers, o, **b: client.post(url(f"/obligations/{o}/file"), headers=headers, json=b)
    assert f({**h, **key()}, ob["GST.GSTR1.MONTHLY"]).json()["code"] == "conflict"                                # GST has its own filing action
    assert f({**h, **key()}, ob["TDS.DEPOSIT.MONTHLY"]).json()["code"] == "conflict"                                   # filed through its payment
    assert f(h, ob["MCA.AOC4"]).json()["code"] == "validation_failed"                                             # key is mandatory
    assert f({**hv, **key()}, ob["MCA.AOC4"]).json()["code"] == "permission_denied"
    assert f({**h, **key()}, ob["MCA.AOC4"], signing="dsc").status_code == 400                                    # a DSC needs a certificate
    assert f({**h, **key()}, ob["MCA.AOC4"]).json()["code"] == "approval_required"
    assert f({**h, **key()}, str(uuid.uuid4())).status_code == 404
    ap = client.post(url(f"/obligations/{ob['MCA.AOC4']}/submit"), headers=h, json={}).json()
    assert client.post(url(f"/approvals/{ap['id']}/approve"), headers={**ha, **key()}, json={}).status_code == 200


def test_mca_filing_returns_an_srn(client, h, db):
    ob = S["ob"]["MCA.AOC4"]
    k = key()
    r = client.post(url(f"/obligations/{ob}/file"), headers={**h, **k}, json={"signing": "none"})
    assert r.status_code == 202 and r.json()["kind"] == "obligation.file"
    assert client.post(url(f"/obligations/{ob}/file"), headers={**h, **k}, json={"signing": "none"}).json()["id"] == r.json()["id"]
    run_worker(db)
    assert job(db, r.json()["id"])["status"] == "succeeded"
    o = db.execute("SELECT status::text AS st, acknowledgement FROM obligations WHERE id = %s", (ob,)).fetchone()
    assert o["st"] == "filed" and o["acknowledgement"].startswith("T") and len(o["acknowledgement"]) == 9
    assert client.post(url(f"/obligations/{ob}/file"), headers={**h, **key()}, json={}).json()["code"] == "conflict"


def test_epf_filing_pauses_for_the_otp(client, h, ha, db):
    emp = db.execute("""INSERT INTO employees (entity_id, employee_code, full_name, date_of_joining, work_state) VALUES (%s, 'E1', 'Staff One', '2020-01-01', '29') RETURNING id""", (EX,)).fetchone()["id"]
    run = db.execute("INSERT INTO payroll_runs (entity_id, period_month, status, headcount) VALUES (%s, '2026-11-01', 'approved', 1) RETURNING id", (EX,)).fetchone()["id"]
    db.execute("""INSERT INTO payslips (run_id, employee_id, paid_days, gross, wages, pf_wage, pf_employee, pf_employer_epf, pf_employer_eps, esi_applicable, net_pay)
                  VALUES (%s, %s, 30, 20000, 10000, 10000, 1200, 367, 833, false, 18800)""", (run, emp))
    ob = db.execute("""SELECT o.id FROM obligations o JOIN compliance_rules r ON r.id = o.rule_id WHERE o.entity_id = %s AND r.code = 'EPF.ECR.MONTHLY' AND o.period_start = '2026-11-01'""", (EX,)).fetchone()["id"]
    before = client.post(url(f"/obligations/{ob}/file"), headers={**h, **key()}, json={})
    assert before.json()["code"] == "approval_required"
    db.execute("UPDATE obligations SET status = 'ready_to_file' WHERE id = %s", (ob,))
    r = client.post(url(f"/obligations/{ob}/file"), headers={**h, **key()}, json={"signing": "evc_otp"})
    run_worker(db)
    j = api_job(client, h, r.json()["id"])
    assert j["status"] == "waiting_for_otp"
    assert db.execute("SELECT status::text AS st FROM obligations WHERE id = %s", (ob,)).fetchone()["st"] == "ready_to_file"        # nothing filed while it waits
    client.post(f"/v1/jobs/{r.json()['id']}/otp", headers=h, json={"otp": "123456"})
    run_worker(db)
    assert api_job(client, h, r.json()["id"])["status"] == "succeeded"
    o = db.execute("SELECT status::text AS st, acknowledgement FROM obligations WHERE id = %s", (ob,)).fetchone()
    assert o["st"] == "filed" and o["acknowledgement"].startswith("TRRN")


def test_filing_failures_leave_the_obligation_untouched(client, h, db):
    ob = S["ob"]["TDS.RETURN.QUARTERLY"]
    db.execute("UPDATE obligations SET status = 'ready_to_file' WHERE id = %s", (ob,))
    r = client.post(url(f"/obligations/{ob}/file"), headers={**h, **key()}, json={})
    run_worker(db)
    j = job(db, r.json()["id"])
    assert j["status"] == "failed" and j["error"]["code"] == "conflict" and "Validate the TDS returns" in j["error"]["detail"] and j["attempts"] == 1
    assert db.execute("SELECT status::text AS st FROM obligations WHERE id = %s", (ob,)).fetchone()["st"] == "ready_to_file"


# ================================================================== Form 26AS
def test_form_26as_matching(client, h, hv, db):
    for q, sec, amt in ((2, "194Q", "400.00"), (2, "194C", "1000.00"), (3, "194Q", "250.00")):
        db.execute("INSERT INTO tds_receivables (entity_id, party_id, section, amount, fy, quarter) VALUES (%s, %s, %s, %s, '2026-27', %s)", (EX, PARTY, sec, amt, q))
    adapters.get("traces").entries = {("AAAPX6666Q", "2026-27"): [
        {"deductor_tan": "BLRC00001A", "section": "194Q", "amount": "400.40", "quarter": 2},           # within Rs 1: matches
        {"deductor_tan": "BLRC00001A", "section": "194C", "amount": "900.00", "quarter": 2},           # off by 100: no match
        {"deductor_tan": "BLRD00002B", "section": "194J", "amount": "5000.00", "quarter": 3}]}          # nothing to match against
    assert client.post(url("/tds-receivables/sync"), headers=hv, json={"fy": "2026-27"}).json()["code"] == "permission_denied"
    assert client.post(url("/tds-receivables/sync"), headers=h, json={"fy": "26-27"}).status_code == 400
    r = client.post(url("/tds-receivables/sync"), headers=h, json={"fy": "2026-27"})
    assert r.status_code == 202
    run_worker(db)
    assert job(db, r.json()["id"])["result"] == {"entries": 3, "matched": 1, "still_unmatched": 2}
    open_ = client.get(url("/tds-receivables"), headers=h, params={"unmatched": "true"}).json()["data"]
    assert sorted((x["section"], x["quarter"]) for x in open_) == [("194C", 2), ("194Q", 3)]
    adapters.get("traces").entries = {}
    db.execute("UPDATE entities SET pan = NULL WHERE id = %s", (EX,))
    assert client.post(url("/tds-receivables/sync"), headers=h, json={"fy": "2026-27"}).json()["code"] == "conflict"
    db.execute("UPDATE entities SET pan = 'AAAPX6666Q' WHERE id = %s", (EX,))


# ================================================================== Account Aggregator
def notify(client, body: dict, secret="dev-aa-webhook-secret", sig=None):
    raw = json.dumps(body).encode()
    return client.post("/v1/integrations/aa/notify", content=raw, headers={"X-AA-Signature": sig or adapters.webhook_signature(secret, raw), "Content-Type": "application/json"})


def test_consent_lifecycle_and_signed_callback(client, h, hv, db):
    today = dt.date.today()
    day = lambda n: (today - dt.timedelta(days=n)).isoformat()
    future = (dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=365)).isoformat()
    assert client.post(url("/aa-consents"), headers=hv, json={"bank_account_ids": [BANK1], "valid_until": future}).json()["code"] == "permission_denied"
    assert client.post(url("/aa-consents"), headers=h, json={"bank_account_ids": [str(uuid.uuid4())], "valid_until": future}).status_code == 400
    assert client.post(url("/aa-consents"), headers=h, json={"bank_account_ids": [BANK1], "valid_until": "2020-01-01T00:00:00+00:00"}).status_code == 400
    assert client.post(url("/aa-consents"), headers=h, json={"bank_account_ids": [BANK1], "valid_until": "2030-01-01T00:00:00"}).status_code == 400       # no time zone
    r = client.post(url("/aa-consents"), headers=h, json={"bank_account_ids": [BANK1, BANK2], "valid_until": future})
    assert r.status_code == 201, r.text
    c = r.json()
    assert c["status"] == "pending" and c["redirect_url"].startswith("https://aa.sandbox.biz360x.example/consent/AAC-") and sorted(c["bank_account_ids"]) == sorted([BANK1, BANK2])
    handle = c["redirect_url"].rsplit("/", 1)[1]
    assert [x["id"] for x in client.get(url("/aa-consents"), headers=hv).json()["data"]] == [c["id"]]
    assert notify(client, {"consent_handle": handle, "status": "ACTIVE"}, sig="0" * 64).status_code == 401
    assert notify(client, {"consent_handle": handle, "status": "ACTIVE"}, secret="wrong").status_code == 401
    assert notify(client, {"consent_handle": "AAC-NOPE", "status": "ACTIVE"}).status_code == 404
    assert notify(client, {"consent_handle": handle, "status": "WHATEVER"}).status_code == 400
    raw = b"not json"
    assert client.post("/v1/integrations/aa/notify", content=raw, headers={"X-AA-Signature": adapters.webhook_signature("dev-aa-webhook-secret", raw)}).status_code == 400
    assert db.execute("SELECT status FROM aa_consents WHERE id = %s", (c["id"],)).fetchone()["status"] == "pending"
    adapters.get("aa").data = {(handle, "4412"): [
        {"external_id": "AA1", "txn_date": day(3), "amount": "25000.00", "description": "NEFT FROM COASTAL GRID", "utr": "N77"},
        {"external_id": "AA2", "txn_date": day(2), "amount": "-1180.00", "description": "SERVICE CHARGES"}],
        (handle, "0937"): [{"external_id": "AB1", "txn_date": day(3), "amount": "9000.00", "description": "UPI COLLECTION"}]}
    assert notify(client, {"consent_handle": handle, "status": "ACTIVE"}).status_code == 204
    assert db.execute("SELECT status FROM aa_consents WHERE id = %s", (c["id"],)).fetchone()["status"] == "active"
    queued = db.execute("SELECT id FROM jobs WHERE kind = 'aa.sync' AND subject_id = %s AND status = 'queued'", (c["id"],)).fetchall()
    assert len(queued) == 1
    run_worker(db)
    assert job(db, queued[0]["id"])["result"] == {"accounts": 2, "imported": 3, "suggested": 0}
    lines = client.get(url("/bank-transactions"), headers=h).json()["data"]
    assert sorted((l["description"], l["amount"]) for l in lines) == [("NEFT FROM COASTAL GRID", "25000.00"), ("SERVICE CHARGES", "-1180.00"), ("UPI COLLECTION", "9000.00")]
    S["consent"], S["handle"] = c, handle


def test_the_sync_is_incremental_and_respects_revocation(client, h, db):
    c, handle = S["consent"], S["handle"]
    adapters.get("aa").data[(handle, "4412")].append({"external_id": "AA3", "txn_date": dt.date.today().isoformat(), "amount": "100.00", "description": "INTEREST"})
    jid = db.execute("INSERT INTO jobs (entity_id, kind, subject, subject_id) VALUES (%s, 'aa.sync', 'entities', %s) RETURNING id", (EX, EX)).fetchone()["id"]
    run_worker(db)
    assert job(db, jid)["result"]["imported"] == 1                                                              # AA1 and AA2 were already there
    assert len(client.get(url("/bank-transactions"), headers=h).json()["data"]) == 4
    assert notify(client, {"consent_handle": handle, "status": "REVOKED"}).status_code == 204
    adapters.get("aa").data[(handle, "4412")].append({"external_id": "AA4", "txn_date": dt.date.today().isoformat(), "amount": "1.00", "description": "AFTER REVOKE"})
    jid = db.execute("INSERT INTO jobs (entity_id, kind, subject, subject_id) VALUES (%s, 'aa.sync', 'entities', %s) RETURNING id", (EX, EX)).fetchone()["id"]
    run_worker(db)
    assert job(db, jid)["result"] == {"accounts": 0, "imported": 0, "suggested": 0}
    assert db.execute("SELECT status FROM aa_consents WHERE id = %s", (c["id"],)).fetchone()["status"] == "revoked"
    db.execute("UPDATE aa_consents SET status = 'active', valid_until = now() - interval '1 day' WHERE id = %s", (c["id"],))
    jid = db.execute("INSERT INTO jobs (entity_id, kind, subject, subject_id) VALUES (%s, 'aa.sync', 'entities', %s) RETURNING id", (EX, EX)).fetchone()["id"]
    run_worker(db)
    assert job(db, jid)["result"]["accounts"] == 0                                                              # an expired consent is never used


def test_the_scheduler_queues_syncs_only_for_live_consents(db):
    from app import worker
    db.execute("UPDATE aa_consents SET status = 'active', valid_until = now() + interval '30 days' WHERE entity_id = %s", (EX,))
    db.execute("UPDATE jobs SET created_at = now() - interval '2 days' WHERE entity_id = %s AND kind = 'aa.sync'", (EX,))
    with db.transaction():
        worker.schedule_daily_jobs(db.cursor())
    assert db.execute("SELECT count(*) AS n FROM jobs WHERE entity_id = %s AND kind = 'aa.sync' AND created_at > now() - interval '1 hour'", (EX,)).fetchone()["n"] == 1
    with db.transaction():
        worker.schedule_daily_jobs(db.cursor())
    assert db.execute("SELECT count(*) AS n FROM jobs WHERE entity_id = %s AND kind = 'aa.sync' AND created_at > now() - interval '1 hour'", (EX,)).fetchone()["n"] == 1     # once a day
    db.execute("UPDATE aa_consents SET status = 'revoked' WHERE entity_id = %s", (EX,))


# ================================================================== registry lookups
def test_registry_lookup_flow(client, h, hm, db):
    assert client.post("/v1/onboarding/lookups", headers=h, json={"identifier": "hello"}).status_code == 400
    r = client.post("/v1/onboarding/lookups", headers=h, json={"identifier": "u26510ka2019ptc123456"})
    assert r.status_code == 201, r.text
    lk = r.json()
    assert lk["status"] == "running" and lk["identifier_kind"] == "cin" and lk["entity"] is None
    assert client.get(f"/v1/onboarding/lookups/{lk['id']}", headers=hm).status_code == 404                           # a lookup belongs to whoever asked
    job_row = db.execute("SELECT id, entity_id, status FROM jobs WHERE kind = 'registry.lookup' AND subject_id = %s", (lk["id"],)).fetchone()
    assert job_row["entity_id"] is None
    assert client.get(f"/v1/jobs/{job_row['id']}", headers=h).json()["status"] == "queued"                           # visible to its creator though it has no entity
    run_worker(db)
    got = client.get(f"/v1/onboarding/lookups/{lk['id']}", headers=h).json()
    assert got["status"] == "found" and got["entity"]["legal_name"] == "Nimbus Instruments Private Limited" and got["entity"]["constitution"] == "private_limited"
    assert [d["name"] for d in got["entity"]["directors"]] == ["Ananya Rao", "Ravi Kumar", "Meera Iyer"] and len(got["gstins"]) == 2 and got["udyam"]["class"] == "small"
    S["lookup"] = got


def test_lookups_are_cached_and_kinds_resolve(client, h, hm, db):
    again = client.post("/v1/onboarding/lookups", headers=hm, json={"identifier": "U26510KA2019PTC123456"})
    assert again.status_code == 201 and again.json()["status"] == "found" and again.json()["id"] != S["lookup"]["id"]       # answered at once from the cache
    gst = client.post("/v1/onboarding/lookups", headers=h, json={"identifier": "33AAHCN1234Q1Z4"}).json()
    pan = client.post("/v1/onboarding/lookups", headers=h, json={"identifier": "AAHCN1234Q"}).json()
    unknown = client.post("/v1/onboarding/lookups", headers=h, json={"identifier": "U12345KA2000PTC000001"}).json()
    run_worker(db)
    for lk, name in ((gst, "Nimbus Instruments Private Limited"), (pan, "Nimbus Instruments Private Limited")):
        assert client.get(f"/v1/onboarding/lookups/{lk['id']}", headers=h).json()["entity"]["legal_name"] == name
    assert client.get(f"/v1/onboarding/lookups/{unknown['id']}", headers=h).json()["status"] == "not_found"


def test_an_unavailable_registry_is_retried_then_reported(client, h, db):
    lk = client.post("/v1/onboarding/lookups", headers=h, json={"identifier": "U99999KA2020PTC000000"}).json()
    for i in range(6):
        db.execute("UPDATE jobs SET run_after = now() WHERE kind = 'registry.lookup' AND subject_id = %s", (lk["id"],))
        run_worker(db)
        if i == 0:
            assert client.get(f"/v1/onboarding/lookups/{lk['id']}", headers=h).json()["status"] == "running"        # still trying
    final = client.get(f"/v1/onboarding/lookups/{lk['id']}", headers=h).json()
    assert final["status"] == "failed"
    assert db.execute("SELECT error FROM registry_lookups WHERE id = %s", (lk["id"],)).fetchone()["error"].startswith("MCA is down")


def test_the_preview_uses_what_the_registry_found(client, h):
    answers = {"lookup_id": S["lookup"]["id"], "track": "business", "headcount_band": "20-49", "turnover_band": "5-10cr", "buys_from_mse": "no"}
    p = client.post("/v1/onboarding/preview", headers=h, json=answers).json()
    by = {a["authority"]: a["count"] for a in p["by_authority"]}
    assert by["GSTN"] == 4                                                                                          # two GSTINs from the registry, GSTR-1 and GSTR-3B each
    unknown = client.post("/v1/onboarding/preview", headers=h, json={**answers, "lookup_id": str(uuid.uuid4())}).json()
    assert "GSTN" not in {a["authority"] for a in unknown["by_authority"]}                                          # no lookup, no GSTINs: nothing assumed


# ================================================================== invariants
def test_visibility_and_no_secrets_left_in_jobs(client, db):
    stranger = login(client, "stranger@example.in")
    for path in ("/portal-connections", "/aa-consents", "/eway-bills"):
        assert client.get(url(path), headers=stranger).status_code == 404
    assert db.execute("SELECT app.audit_verify() AS v").fetchone()["v"] is None
    assert db.execute("SELECT count(*) AS n FROM jobs WHERE payload ? 'otp_enc' AND status IN ('succeeded', 'failed', 'cancelled')").fetchone()["n"] == 0
    assert db.execute("SELECT count(*) AS n FROM portal_connections WHERE entity_id = %s AND credential_ref IS NOT NULL AND status = 'revoked'", (EX,)).fetchone()["n"] == 0
