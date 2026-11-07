"""M5 end to end: the document vault (signed upload and download, search, expiry, requests, data rooms), contracts and
their terms, templates, signature requests, board meetings and resolutions (with MGT-14), and the notice desk.

Own entity (EL); tests run in order and build on one another.
"""
import datetime as dt
import hashlib
import os
import tempfile
import uuid

import psycopg
import pytest
from psycopg.rows import dict_row

DB = os.environ["DATABASE_URL"]
ORG = "00000000-0000-0000-0000-0000000000a1"
EL = "00000000-0000-0000-0000-0000000000f4"
U_OWNER, U_APPROVER, U_VIEWER, U_MAKER = ("00000000-0000-0000-0000-0000000000f%d" % i for i in (5, 6, 7, 8))
PERSON1, PERSON2, PERSON3 = ("00000000-0000-0000-0000-00000000010%d" % i for i in (1, 2, 3))
PARTY, REG = "00000000-0000-0000-0000-000000000111", "00000000-0000-0000-0000-000000000112"
OWNER, APPROVER, VIEWER, MAKER = "m5-owner@example.in", "m5-approver@example.in", "m5-viewer@example.in", "m5-maker@example.in"
TODAY = dt.date.today()
S: dict = {}
KINDS = "('compliance.run_engine','collections.dunning','collections.recurring','purchases.msme_alerts','einvoice.generate_irn')"

CONTRACT_TEXT = b"""SUPPLY AGREEMENT between Nimbus and Meridian.
Payment shall be made within 60 days of receipt of invoice. This agreement renews automatically for 12 months unless either party gives 90 days' written notice.
This agreement is governed by the laws of India. The liability of the Supplier is capped at the fees paid.
"""


@pytest.fixture(scope="module")
def db():
    with psycopg.connect(DB, autocommit=True, row_factory=dict_row) as c:
        yield c


@pytest.fixture(scope="module", autouse=True)
def entity(db):
    store = tempfile.mkdtemp()
    old = os.environ.get("BIZ_STORAGE_DIR")
    os.environ["BIZ_STORAGE_DIR"] = store
    S["store"] = store
    db.execute("""INSERT INTO entities (id, organization_id, legal_name, constitution, track, udyam_number, udyam_class)
                  VALUES (%s, %s, 'M5 Test Pvt Ltd', 'private_limited', 'business', 'UDYAM-KA-03-0054321', 'small')""", (EL, ORG))
    db.execute("INSERT INTO entity_registrations (id, entity_id, kind, number, state_code) VALUES (%s, %s, 'gstin', '29AAACL5555D1Z5', '29')", (REG, EL))
    db.execute("""INSERT INTO users (id, email, full_name) VALUES (%s, %s, 'M5 Owner'), (%s, %s, 'M5 Approver'), (%s, %s, 'M5 Viewer'), (%s, %s, 'M5 Maker')""",
               (U_OWNER, OWNER, U_APPROVER, APPROVER, U_VIEWER, VIEWER, U_MAKER, MAKER))
    db.execute("INSERT INTO memberships (entity_id, user_id, role) VALUES (%s, %s, 'owner'), (%s, %s, 'approver'), (%s, %s, 'viewer'), (%s, %s, 'maker')",
               (EL, U_OWNER, EL, U_APPROVER, EL, U_VIEWER, EL, U_MAKER))
    db.execute("""INSERT INTO persons (id, entity_id, user_id, full_name, designation) VALUES (%s, %s, %s, 'Ananya Rao', 'Director'),
                  (%s, %s, %s, 'Ravi Kumar', 'Director'), (%s, %s, NULL, 'Meera Iyer', 'Director')""", (PERSON1, EL, U_OWNER, PERSON2, EL, U_APPROVER, PERSON3, EL))
    db.execute("INSERT INTO parties (id, entity_id, legal_name, is_customer) VALUES (%s, %s, 'Meridian Appliances Ltd', true)", (PARTY, EL))
    db.execute("INSERT INTO entity_facts (entity_id, fact_key, value_text, as_of, source) VALUES (%s, 'constitution', 'private_limited', '2026-04-01', 'user')", (EL,))
    yield
    db.execute(f"UPDATE jobs SET status = 'cancelled' WHERE status = 'queued' AND kind IN {KINDS}")
    if old is None:
        os.environ.pop("BIZ_STORAGE_DIR", None)
    else:
        os.environ["BIZ_STORAGE_DIR"] = old


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
def ha(client): return login(client, APPROVER)
@pytest.fixture(scope="module")
def hv(client): return login(client, VIEWER)
@pytest.fixture(scope="module")
def hm(client): return login(client, MAKER)


def url(path):
    return f"/v1/entities/{EL}{path}"


def key():
    return {"Idempotency-Key": str(uuid.uuid4())}


def run_worker(db):
    from app import worker
    db.execute(f"UPDATE jobs SET status = 'cancelled' WHERE status = 'queued' AND kind IN {KINDS}")
    n = 0
    while worker.run_once():
        n += 1
        assert n < 50


def job(db, job_id):
    return db.execute("SELECT status, result, error FROM jobs WHERE id = %s", (job_id,)).fetchone()


def upload(client, h, data: bytes, title="A document", folder="other", mime="text/plain", file_name="doc.txt", **extra):
    """Register, PUT the bytes, return the document."""
    sha = hashlib.sha256(data).hexdigest()
    r = client.post(url("/documents"), headers=h, json={"folder": folder, "title": title, "file_name": file_name, "mime_type": mime, "size_bytes": len(data), "sha256": sha, **extra})
    assert r.status_code in (200, 201), r.text
    t = r.json()
    if t["upload_url"]:
        put = client.put(t["upload_url"].split("http://localhost:8000")[1], content=data, headers={"Content-Type": mime})
        assert put.status_code == 204, put.text
    return client.get(url(f"/documents/{t['document']['id']}"), headers=h).json()


# ================================================================== vault: signed upload and download
def test_register_upload_and_download(client, h, hv):
    data = b"Board pack for the November meeting. Revenue grew 11% on September."
    sha = hashlib.sha256(data).hexdigest()
    body = {"folder": "board", "title": "Board pack Nov", "file_name": "pack.txt", "mime_type": "text/plain", "size_bytes": len(data), "sha256": sha, "period_label": "Nov 2026"}
    assert client.post(url("/documents"), headers=hv, json=body).json()["code"] == "permission_denied"
    assert client.post(url("/documents"), headers=h, json={**body, "size_bytes": 10**10}).status_code == 400
    assert client.post(url("/documents"), headers=h, json={**body, "sha256": "xyz"}).status_code == 400
    assert client.post(url("/documents"), headers=h, json={**body, "folder": "nowhere"}).status_code == 400
    r = client.post(url("/documents"), headers=h, json=body)
    assert r.status_code == 201, r.text
    t = r.json()
    doc = t["document"]
    assert doc["download_url"] is None and doc["sha256"] == sha and doc["source"] == "upload" and t["upload_method"] == "PUT" and t["upload_headers"] == {"Content-Type": "text/plain"}
    path = t["upload_url"].split("http://localhost:8000")[1]
    again = client.post(url("/documents"), headers=h, json=body)
    assert again.status_code == 200 and again.json()["document"]["id"] == doc["id"] and again.json()["upload_url"]               # not uploaded yet: still offered a ticket
    assert client.put(path, content=b"tampered bytes").status_code == 400                                                           # wrong size and hash
    assert client.put(path[:-4] + "abcd", content=data).status_code == 403                                                          # forged token
    assert client.put(path, content=data, headers={"Content-Type": "text/plain"}).status_code == 204
    assert client.put(path, content=data).status_code == 204                                                                       # idempotent
    got = client.get(url(f"/documents/{doc['id']}"), headers=hv).json()
    assert got["download_url"]
    dl = client.get(got["download_url"].split("http://localhost:8000")[1])
    assert dl.status_code == 200 and dl.content == data and "pack.txt" in dl.headers["content-disposition"] and dl.headers["content-type"].startswith("text/plain")
    done = client.post(url("/documents"), headers=h, json=body)
    assert done.status_code == 200 and done.json()["upload_url"] is None and done.json()["document"]["download_url"]
    S["board_pack"] = doc


def test_tokens_cannot_be_reused_across_operations_or_after_expiry(client, h):
    from app import storage
    doc = S["board_pack"]
    key_ = storage.storage_key(EL, doc["sha256"])
    assert client.get("/v1/storage/" + storage.sign("get", key_, -10, name="x", mime="text/plain")).status_code == 403              # expired
    assert client.get("/v1/storage/" + storage.sign("put", key_, 60, doc=doc["id"], sha256=doc["sha256"], size=1)).status_code == 403   # an upload token cannot download
    assert client.get("/v1/storage/" + storage.sign("get", "../../etc/passwd", 60, name="x", mime="text/plain")).status_code == 404


# ================================================================== vault: search, expiry, links
def test_search_filters_and_expiry(client, h, ha):
    contract_doc = upload(client, h, CONTRACT_TEXT, "Supply agreement - Meridian", "contracts", "text/plain", "supply.txt")
    S["contract_doc"] = contract_doc
    lic = upload(client, h, b"Factory licence certificate", "Factory licence", "licences", valid_until=(TODAY + dt.timedelta(days=30)).isoformat(), file_name="licence.txt")
    far = upload(client, h, b"ISO 9001 certificate", "ISO 9001", "licences", valid_until=(TODAY + dt.timedelta(days=300)).isoformat(), file_name="iso.txt")
    titles = lambda **p: [d["title"] for d in client.get(url("/documents"), headers=h, params=p).json()["data"]]
    assert titles(q="renews automatically") == ["Supply agreement - Meridian"]                     # found in the text layer
    assert titles(q="licence") == ["Factory licence"] and titles(q="nonexistentword") == []
    assert set(titles(folder="licences")) == {"Factory licence", "ISO 9001"}
    assert titles(expiring_within_days=60) == ["Factory licence"] and set(titles(expiring_within_days=400)) == {"Factory licence", "ISO 9001"}
    assert titles(source="upload", folder="board") == ["Board pack Nov"] and titles(source="gstn") == []
    page = client.get(url("/documents"), headers=h, params={"limit": 2}).json()
    assert len(page["data"]) == 2 and page["next_cursor"] == "2"
    S["lic"], S["far"] = lic, far


def test_update_and_link_documents(client, h, hv):
    lic = S["lic"]
    r = client.patch(url(f"/documents/{lic['id']}"), headers=h, json={"title": "Factory licence 2026", "valid_until": (TODAY + dt.timedelta(days=45)).isoformat(),
                                                                        "links": [{"subject": "entity_registrations", "subject_id": REG, "role": "certificate"}]})
    assert r.status_code == 200 and r.json()["title"] == "Factory licence 2026" and r.json()["links"] == [{"subject": "entity_registrations", "subject_id": REG, "role": "certificate"}]
    by_link = client.get(url("/documents"), headers=h, params={"subject": "entity_registrations", "subject_id": REG}).json()["data"]
    assert [d["id"] for d in by_link] == [lic["id"]]
    assert client.patch(url(f"/documents/{lic['id']}"), headers=h, json={"folder": "bogus"}).status_code == 400
    assert client.patch(url(f"/documents/{lic['id']}"), headers=h, json={"valid_until": "soon"}).status_code == 400
    assert client.patch(url(f"/documents/{lic['id']}"), headers=hv, json={"title": "x"}).json()["code"] == "permission_denied"
    assert client.patch(url(f"/documents/{uuid.uuid4()}"), headers=h, json={}).status_code == 404


# ================================================================== vault: requests and data rooms
def test_advisor_requests(client, h, hv):
    r = client.post(url("/document-requests"), headers=h, json={"title": "Bank confirmations as of 31 Mar", "due_on": (TODAY + dt.timedelta(days=7)).isoformat()})
    assert r.status_code == 201 and r.json()["status"] == "open" and r.json()["requested_by_name"] == "M5 Owner" and r.json()["document_ids"] == []
    rid = r.json()["id"]
    pending = client.post(url("/documents"), headers=h, json={"folder": "other", "title": "No file yet", "file_name": "n.txt", "mime_type": "text/plain", "size_bytes": 3,
                                                               "sha256": hashlib.sha256(b"abc").hexdigest()}).json()["document"]
    assert client.post(url(f"/document-requests/{rid}/share"), headers=h, json={"document_ids": [pending["id"]]}).status_code == 400   # nothing to share yet
    assert client.post(url(f"/document-requests/{rid}/share"), headers=hv, json={"document_ids": [S["lic"]["id"]]}).json()["code"] == "permission_denied"
    done = client.post(url(f"/document-requests/{rid}/share"), headers=h, json={"document_ids": [S["lic"]["id"], S["far"]["id"]]})
    assert done.status_code == 200 and done.json()["status"] == "fulfilled" and sorted(done.json()["document_ids"]) == sorted([S["lic"]["id"], S["far"]["id"]])
    assert client.post(url(f"/document-requests/{rid}/share"), headers=h, json={"document_ids": [S["lic"]["id"]]}).json()["code"] == "conflict"
    assert [x["id"] for x in client.get(url("/document-requests"), headers=h, params={"status": "open"}).json()["data"]] == []
    assert len(client.get(url("/document-requests"), headers=h, params={"status": "fulfilled"}).json()["data"]) == 1
    assert client.post(url(f"/document-requests/{uuid.uuid4()}/share"), headers=h, json={"document_ids": [S["lic"]["id"]]}).status_code == 404


def test_data_rooms(client, h):
    body = {"name": "Seed extension diligence", "purpose": "fundraise", "document_ids": [S["lic"]["id"]], "access": [{"email": "Lead@Fund.in"}, {"email": "counsel@firm.in"}]}
    assert client.post(url("/data-rooms"), headers=h, json={**body, "document_ids": [str(uuid.uuid4())]}).status_code == 400
    assert client.post(url("/data-rooms"), headers=h, json={**body, "access": [{"email": "not-an-email"}]}).status_code == 400
    r = client.post(url("/data-rooms"), headers=h, json=body)
    assert r.status_code == 201, r.text
    room = r.json()
    assert room["watermark"] is True and room["allow_download"] is False and [a["email"] for a in room["access"]] == ["counsel@firm.in", "lead@fund.in"]
    upd = client.patch(url(f"/data-rooms/{room['id']}"), headers=h, json={"access": [{"email": "lead@fund.in"}], "document_ids": [S["lic"]["id"], S["far"]["id"]], "allow_download": True})
    assert upd.status_code == 200 and [a["email"] for a in upd.json()["access"]] == ["lead@fund.in"] and len(upd.json()["document_ids"]) == 2 and upd.json()["allow_download"]
    back = client.patch(url(f"/data-rooms/{room['id']}"), headers=h, json={"access": [{"email": "lead@fund.in"}, {"email": "counsel@firm.in"}]}).json()
    assert len(back["access"]) == 2                                                                  # a revoked viewer can be re-invited
    assert client.get(url(f"/data-rooms/{room['id']}"), headers=h).json()["name"] == "Seed extension diligence"
    assert len(client.get(url("/data-rooms"), headers=h).json()["data"]) == 1
    assert client.delete(url(f"/data-rooms/{room['id']}"), headers=h).status_code == 204
    assert client.delete(url(f"/data-rooms/{room['id']}"), headers=h).status_code == 404
    assert client.get(url(f"/data-rooms/{room['id']}"), headers=h).status_code == 404


# ================================================================== contracts
def contract_body(**kw):
    return {"party_id": PARTY, "counterparty_name": "Meridian Appliances Ltd", "title": "Supply agreement", "kind": "customer", "status": "active",
            "start_date": (TODAY - dt.timedelta(days=320)).isoformat(), "end_date": (TODAY + dt.timedelta(days=45)).isoformat(), "auto_renews": True,
            "renewal_months": 12, "notice_days": 20, "payment_terms_days": 60, "value_amount": "4800000.00", "value_period": "annual", **kw}


def test_contract_alerts_and_notice_windows(client, h, hv):
    assert client.post(url("/contracts"), headers=hv, json=contract_body()).json()["code"] == "permission_denied"
    assert client.post(url("/contracts"), headers=h, json=contract_body(end_date=(TODAY - dt.timedelta(days=400)).isoformat())).status_code == 400
    assert client.post(url("/contracts"), headers=h, json=contract_body(notice_days=None)).status_code == 400                      # auto-renewal needs a notice period
    assert client.post(url("/contracts"), headers=h, json=contract_body(party_id=str(uuid.uuid4()))).status_code == 400
    assert client.post(url("/contracts"), headers=h, json=contract_body(kind="spaceship")).status_code == 400
    r = client.post(url("/contracts"), headers=h, json=contract_body())
    assert r.status_code == 201, r.text
    c = r.json()
    assert c["notice_deadline"] == (TODAY + dt.timedelta(days=25)).isoformat() and c["terms"] == []
    al = {a["code"]: a for a in c["alerts"]}
    assert al["notice_window"]["level"] == "warn" and al["msme_terms"]["level"] == "warn" and al["no_signed_copy"]["level"] == "warn"
    S["contract"] = c
    other = client.post(url("/contracts"), headers=h, json=contract_body(title="Calibration services", kind="vendor", counterparty_name="Orion Calibration", party_id=None,
                                                                         end_date=(TODAY + dt.timedelta(days=200)).isoformat(), notice_days=30, payment_terms_days=30)).json()
    S["vendor_contract"] = other
    titles = lambda **p: [x["title"] for x in client.get(url("/contracts"), headers=h, params=p).json()["data"]]
    assert titles(notice_within_days=30) == ["Supply agreement"] and titles(notice_within_days=10) == []
    assert titles(kind="vendor") == ["Calibration services"] and titles(status="active") == ["Supply agreement", "Calibration services"]
    assert client.get(url(f"/contracts/{c['id']}"), headers=hv).json()["title"] == "Supply agreement"
    assert client.get(url(f"/contracts/{uuid.uuid4()}"), headers=h).status_code == 404


def test_update_contract(client, h):
    c = S["contract"]
    r = client.patch(url(f"/contracts/{c['id']}"), headers=h, json={"payment_terms_days": 45, "governing_law": "India", "dispute_forum": "Arbitration, Bengaluru"})
    assert r.status_code == 200 and r.json()["payment_terms_days"] == 45 and "msme_terms" not in {a["code"] for a in r.json()["alerts"]}
    assert client.patch(url(f"/contracts/{c['id']}"), headers=h, json={"end_date": "2020-01-01"}).status_code == 400
    assert client.patch(url(f"/contracts/{c['id']}"), headers=h, json={"signed_document_id": str(uuid.uuid4())}).status_code == 400
    ok = client.patch(url(f"/contracts/{c['id']}"), headers=h, json={"signed_document_id": S["contract_doc"]["id"], "payment_terms_days": 60})
    assert ok.status_code == 200 and "no_signed_copy" not in {a["code"] for a in ok.json()["alerts"]}


def test_terms_are_extracted_and_verified_by_people(client, h, hv, db):
    c = S["contract"]
    assert client.post(url(f"/contracts/{S['vendor_contract']['id']}/extract-terms"), headers=h).json()["code"] == "conflict"          # no signed copy
    ver = client.put(url(f"/contracts/{c['id']}/terms/payment"), headers=h, json={"value_text": "60 days from acceptance, to be cut to 45", "verified": True, "source_page": 3})
    assert ver.status_code == 200 and ver.json()["verified"] is True and ver.json()["extracted_by"] == "user"
    assert client.put(url(f"/contracts/{c['id']}/terms/Bad Key"), headers=h, json={"value_text": "x"}).status_code == 400
    assert client.put(url(f"/contracts/{c['id']}/terms/lock_in"), headers=hv, json={"value_text": "x"}).json()["code"] == "permission_denied"
    r = client.post(url(f"/contracts/{c['id']}/extract-terms"), headers=h)
    assert r.status_code == 202
    run_worker(db)
    res = job(db, r.json()["id"])
    assert res["status"] == "succeeded" and set(res["result"]["keys"]) == {"payment", "notice", "renewal", "governing_law", "liability_cap"}
    terms = {t["term_key"]: t for t in client.get(url(f"/contracts/{c['id']}"), headers=h).json()["terms"]}
    assert terms["notice"]["extracted_by"] == "copilot" and terms["notice"]["confidence"] == 0.5 and terms["notice"]["verified"] is False and "90 days" in terms["notice"]["value_text"]
    assert terms["payment"]["value_text"].startswith("60 days from acceptance") and terms["payment"]["verified"] is True          # a person's word is never overwritten


def test_notice_drafts_land_in_the_vault(client, h, db):
    c = S["contract"]
    assert client.post(url(f"/contracts/{c['id']}/notices"), headers=h, json={"kind": "bogus"}).status_code == 400
    r = client.post(url(f"/contracts/{c['id']}/notices"), headers=h, json={"kind": "non_renewal"})
    assert r.status_code == 202
    run_worker(db)
    res = job(db, r.json()["id"])
    assert res["status"] == "succeeded"
    docs = client.get(url("/documents"), headers=h, params={"subject": "contracts", "subject_id": c["id"]}).json()["data"]
    draft = next(d for d in docs if d["title"].startswith("Draft non-renewal"))
    assert {"subject": "contracts", "subject_id": c["id"], "role": "draft"} in draft["links"] and draft["download_url"]
    text = client.get(draft["download_url"].split("http://localhost:8000")[1]).text
    assert "DRAFT - REVIEW BEFORE SENDING" in text and "Meridian Appliances Ltd" in text and "does not wish to renew" in text and c["notice_deadline"] in text
    t = db.execute("SELECT title, due_on, source FROM tasks WHERE entity_id = %s AND subject = 'contract' AND subject_id = %s", (EL, c["id"])).fetchone()
    assert "non renewal notice" in t["title"] and str(t["due_on"]) == c["notice_deadline"] and t["source"] == "engine"
    assert client.post(url(f"/contracts/{S['vendor_contract']['id']}/notices"), headers=h, json={"kind": "termination"}).status_code == 202
    client.patch(url(f"/contracts/{S['vendor_contract']['id']}"), headers=h, json={"status": "terminated"})
    assert client.post(url(f"/contracts/{S['vendor_contract']['id']}/notices"), headers=h, json={"kind": "termination"}).json()["code"] == "conflict"


# ================================================================== templates
def test_templates_generate_drafts(client, h, hv):
    tpls = {t["name"]: t for t in client.get(url("/contract-templates"), headers=hv).json()}
    assert set(tpls) == {"Mutual NDA", "Master services agreement", "Supply agreement", "Offer letter and employment", "Consultant agreement"}
    assert all(t["global"] is True and t["reviewed_by"] is None and t["version"] == "v1" for t in tpls.values())
    sup = tpls["Supply agreement"]
    missing = client.post(url(f"/contract-templates/{sup['id']}/generate"), headers=h, json={"party_id": PARTY, "fields": {"payment_terms_days": "30"}})
    assert missing.status_code == 400 and {e["field"] for e in missing.json()["errors"]} == {"fields.start_date", "fields.end_date", "fields.renewal_months", "fields.notice_days", "fields.arbitration_seat"}
    assert client.post(url(f"/contract-templates/{sup['id']}/generate"), headers=hv, json={"party_id": PARTY, "fields": {}}).json()["code"] == "permission_denied"
    assert client.post(url(f"/contract-templates/{uuid.uuid4()}/generate"), headers=h, json={"party_id": PARTY, "fields": {}}).status_code == 404
    fields = {"payment_terms_days": "30", "start_date": TODAY.isoformat(), "end_date": (TODAY + dt.timedelta(days=365)).isoformat(), "renewal_months": "12", "notice_days": "60", "arbitration_seat": "Bengaluru"}
    assert client.post(url(f"/contract-templates/{sup['id']}/generate"), headers=h, json={"party_id": PARTY, "fields": {**fields, "start_date": "tomorrow"}}).status_code == 400
    r = client.post(url(f"/contract-templates/{sup['id']}/generate"), headers=h, json={"party_id": PARTY, "fields": fields})
    assert r.status_code == 201, r.text
    c = r.json()
    assert (c["kind"], c["status"], c["title"], c["auto_renews"], c["notice_days"], c["payment_terms_days"]) == ("customer", "draft", "Supply agreement - Meridian Appliances Ltd", True, 60, 30)
    assert c["template_id"] == sup["id"] and c["notice_deadline"] == (TODAY + dt.timedelta(days=305)).isoformat()
    doc = next(d for d in client.get(url("/documents"), headers=h, params={"subject": "contracts", "subject_id": c["id"]}).json()["data"])
    body = client.get(doc["download_url"].split("http://localhost:8000")[1]).text
    assert "Meridian Appliances Ltd" in body and "M5 Test Pvt Ltd" in body and "within 30 days of acceptance" in body and "45 days from the day of acceptance" in body and "{{" not in body
    S["draft_contract"], S["draft_doc"] = c, doc


# ================================================================== signatures
def signers(*rows):
    return [dict(zip(("person_id", "external_name", "external_email", "sign_order"), r)) for r in rows]


def sig_body(subject, subject_id, document_id, sg, method="aadhaar_esign"):
    return {"subject": subject, "subject_id": subject_id, "document_id": document_id, "method": method, "signers": sg}


def test_signature_request_flow(client, h, ha, hv, hm):
    c, doc = S["draft_contract"], S["draft_doc"]
    sg = [{"person_id": PERSON1, "sign_order": 1}, {"external_name": "Meridian signatory", "external_email": "signer@meridian.in", "sign_order": 2}]
    assert client.post(url("/signature-requests"), headers=h, json=sig_body("contract", c["id"], doc["id"], sg)).json()["code"] == "validation_failed"     # key is mandatory
    assert client.post(url("/signature-requests"), headers={**hv, **key()}, json=sig_body("contract", c["id"], doc["id"], sg)).json()["code"] == "permission_denied"
    assert client.post(url("/signature-requests"), headers={**h, **key()}, json=sig_body("contract", c["id"], doc["id"], [{"sign_order": 1}])).status_code == 400
    assert client.post(url("/signature-requests"), headers={**h, **key()}, json=sig_body("contract", c["id"], doc["id"], [{"person_id": str(uuid.uuid4())}])).status_code == 400
    assert client.post(url("/signature-requests"), headers={**h, **key()}, json=sig_body("contract", str(uuid.uuid4()), doc["id"], sg)).status_code == 400
    k = key()
    r = client.post(url("/signature-requests"), headers={**h, **k}, json=sig_body("contract", c["id"], doc["id"], sg))
    assert r.status_code == 201, r.text
    req = r.json()
    assert client.post(url("/signature-requests"), headers={**h, **k}, json=sig_body("contract", c["id"], doc["id"], sg)).json()["id"] == req["id"]       # replay
    assert req["status"] == "sent" and [(s["sign_order"], s["status"]) for s in req["signers"]] == [(1, "waiting"), (2, "queued")] and req["signers"][0]["person_name"] == "Ananya Rao"
    assert client.get(url(f"/contracts/{c['id']}"), headers=h).json()["status"] == "out_for_signature"
    first, second = req["signers"]
    sign = lambda headers, sid, **kw: client.post(url(f"/signature-requests/{req['id']}/signers/{sid}/sign"), headers=headers, json=kw)
    r_ = sign(h, second["id"])
    assert r_.json()["code"] == "conflict"                                                  # not up yet
    assert sign(hm, first["id"]).json()["code"] == "permission_denied"                                 # a maker is neither the signer nor an approver
    one = sign(h, first["id"], evidence="DSC serial 12345")
    assert one.status_code == 200 and one.json()["status"] == "partially_signed" and [s["status"] for s in one.json()["signers"]] == ["signed", "waiting"]
    assert sign(h, first["id"]).json()["code"] == "conflict"                                           # already signed
    assert client.post(url(f"/signature-requests/{req['id']}/remind"), headers=hv).status_code == 204
    two = sign(ha, second["id"], evidence="eSign transaction ESG-77 (recorded by approver)")
    assert two.status_code == 200 and two.json()["status"] == "completed" and two.json()["completed_at"]
    done = client.get(url(f"/contracts/{c['id']}"), headers=h).json()
    assert done["status"] == "active" and done["signed_document_id"] == doc["id"]
    links = client.get(url(f"/documents/{doc['id']}"), headers=h).json()["links"]
    assert {"subject": "contracts", "subject_id": c["id"], "role": "signed_copy"} in links
    assert client.post(url(f"/signature-requests/{req['id']}/remind"), headers=h).json()["code"] == "conflict"
    assert [x["id"] for x in client.get(url("/signature-requests"), headers=h, params={"status": "completed"}).json()["data"]] == [req["id"]]
    assert client.get(url(f"/signature-requests/{req['id']}"), headers=hv).json()["id"] == req["id"]
    assert client.get(url(f"/signature-requests/{uuid.uuid4()}"), headers=h).status_code == 404


def test_declining_sends_the_contract_back(client, h, ha):
    c = client.post(url("/contracts"), headers=h, json=contract_body(title="Rate contract", status="negotiating", auto_renews=False, end_date=None, notice_days=None)).json()
    r = client.post(url("/signature-requests"), headers={**h, **key()}, json=sig_body("contract", c["id"], S["draft_doc"]["id"], [{"person_id": PERSON2, "sign_order": 1}], "click_to_sign"))
    req = r.json()
    assert client.get(url(f"/contracts/{c['id']}"), headers=h).json()["status"] == "out_for_signature"
    d = client.post(url(f"/signature-requests/{req['id']}/signers/{req['signers'][0]['id']}/decline"), headers=ha, json={"reason": "Price too high"})
    assert d.status_code == 200 and d.json()["status"] == "declined" and d.json()["signers"][0]["status"] == "declined"
    assert client.get(url(f"/contracts/{c['id']}"), headers=h).json()["status"] == "negotiating"
    assert client.post(url(f"/signature-requests/{req['id']}/signers/{req['signers'][0]['id']}/sign"), headers=ha, json={}).json()["code"] == "conflict"


# ================================================================== board meetings and resolutions
def meeting(client, h, **kw):
    r = client.post(url("/meetings"), headers=h, json={"kind": "board", "scheduled_for": "2026-10-20T10:00:00+05:30", **kw})
    assert r.status_code == 201, r.text
    return r.json()


def test_meetings_and_the_120_day_rule(client, h, hv):
    assert client.post(url("/meetings"), headers=hv, json={"kind": "board", "scheduled_for": "2026-06-10T10:00:00+05:30"}).json()["code"] == "permission_denied"
    assert client.post(url("/meetings"), headers=h, json={"kind": "board", "scheduled_for": "2027-01-10T10:00:00+05:30", "held_on": "2099-01-01"}).status_code == 400   # cannot have been held yet
    assert client.post(url("/meetings"), headers=h, json={"kind": "board", "scheduled_for": "2027-01-10T10:00:00+05:30", "quorum_met": True}).status_code == 400
    assert client.post(url("/meetings"), headers=h, json={"kind": "board", "scheduled_for": "2026-06-10T10:00:00+05:30", "notice_sent_on": "2026-07-01"}).status_code == 400
    m1 = meeting(client, h, scheduled_for="2026-06-10T10:00:00+05:30", held_on="2026-06-10", quorum_met=True, notice_sent_on="2026-06-01")
    assert m1["days_since_last_board"] is None
    m2 = meeting(client, h, held_on="2026-10-20", quorum_met=True)
    assert m2["days_since_last_board"] == 132                                                       # 10 Jun to 20 Oct: over the 120 days the Act allows
    m3 = meeting(client, h, scheduled_for="2026-12-15T10:00:00+05:30")
    assert m3["held_on"] is None and m3["quorum_met"] is None and m3["days_since_last_board"] == 56
    agm = meeting(client, h, kind="agm", scheduled_for="2026-09-15T10:00:00+05:30", held_on="2026-09-15", quorum_met=True)
    assert agm["days_since_last_board"] is None
    S.update(m1=m1, m2=m2, m3=m3)
    assert [m["id"] for m in client.get(url("/meetings"), headers=h, params={"kind": "agm"}).json()["data"]] == [agm["id"]]
    assert len(client.get(url("/meetings"), headers=h, params={"from": "2026-10-01", "to": "2026-12-31"}).json()["data"]) == 2
    p = client.patch(url(f"/meetings/{m3['id']}"), headers=h, json={"notice_sent_on": "2026-12-01"})
    assert p.status_code == 200 and p.json()["notice_sent_on"] == "2026-12-01"
    assert client.patch(url(f"/meetings/{m3['id']}"), headers=h, json={"minutes_document_id": str(uuid.uuid4())}).status_code == 400
    assert client.patch(url(f"/meetings/{m3['id']}"), headers=h, json={"minutes_document_id": S["board_pack"]["id"]}).json()["minutes_document_id"] == S["board_pack"]["id"]
    assert client.get(url(f"/meetings/{uuid.uuid4()}"), headers=h).status_code == 404


def res_body(**kw):
    return {"number": "BR/2026-27/001", "title": "Open a second current account", "kind": "board", **kw}


def test_resolutions_and_mgt14(client, h, hv, db):
    m2, m3 = S["m2"], S["m3"]
    assert client.post(url("/resolutions"), headers=hv, json=res_body()).json()["code"] == "permission_denied"
    assert client.post(url("/resolutions"), headers=h, json=res_body(status="passed")).status_code == 400
    assert client.post(url("/resolutions"), headers=h, json=res_body(meeting_id=str(uuid.uuid4()))).status_code == 400
    ordinary = client.post(url("/resolutions"), headers=h, json=res_body(meeting_id=m2["id"])).json()
    assert ordinary["status"] == "draft" and ordinary["needs_mgt14"] is False
    assert client.post(url("/resolutions"), headers=h, json=res_body()).json()["code"] == "conflict"                              # numbers are unique
    special = client.post(url("/resolutions"), headers=h, json=res_body(number="SR/2026-27/001", title="Increase authorised capital", kind="special", meeting_id=m2["id"])).json()
    assert special["needs_mgt14"] is True
    unheld = client.post(url("/resolutions"), headers=h, json=res_body(number="BR/2026-27/002", meeting_id=m3["id"])).json()
    p = lambda rid, **b: client.patch(url(f"/resolutions/{rid}"), headers=h, json=b)
    assert p(unheld["id"], status="passed").json()["code"] == "conflict"                                                        # the meeting has not been held
    assert p(special["id"], status="circulated").json()["status"] == "circulated"
    done = p(special["id"], status="passed", passed_on="2026-10-20")
    assert done.status_code == 200, done.text
    assert done.json()["status"] == "passed" and done.json()["passed_on"] == "2026-10-20" and done.json()["mgt14_obligation_id"]
    ob = db.execute("""SELECT o.due_date, o.period_label, o.status::text AS st, r.code FROM obligations o JOIN compliance_rules r ON r.id = o.rule_id WHERE o.id = %s""",
                    (done.json()["mgt14_obligation_id"],)).fetchone()
    assert (ob["code"], ob["period_label"], ob["st"], ob["due_date"]) == ("MCA.MGT14", "Resolution SR/2026-27/001", "upcoming", dt.date(2026, 11, 19))   # 30 days to file
    assert p(special["id"], status="draft").json()["code"] == "conflict" and p(special["id"], title="Changed").json()["code"] == "conflict"
    plain = p(ordinary["id"], status="passed", passed_on="2026-10-20").json()
    assert plain["status"] == "passed" and plain["mgt14_obligation_id"] is None                                                  # a plain board resolution needs no filing
    assert [r["number"] for r in client.get(url(f"/meetings/{m2['id']}"), headers=h).json()["resolutions"]] == ["BR/2026-27/001", "SR/2026-27/001"]
    assert [r["number"] for r in client.get(url("/resolutions"), headers=h, params={"status": "passed"}).json()["data"]] == ["BR/2026-27/001", "SR/2026-27/001"]
    assert client.get(url(f"/resolutions/{special['id']}"), headers=hv).json()["title"] == "Increase authorised capital"
    assert client.get(url(f"/resolutions/{uuid.uuid4()}"), headers=h).status_code == 404


def test_no_quorum_no_resolution(client, h):
    m = meeting(client, h, scheduled_for="2026-11-03T10:00:00+05:30", held_on="2026-11-03")
    r = client.post(url("/resolutions"), headers=h, json=res_body(number="BR/2026-27/009", meeting_id=m["id"])).json()
    assert client.patch(url(f"/resolutions/{r['id']}"), headers=h, json={"status": "passed"}).json()["code"] == "conflict"       # held, but quorum was never recorded
    client.patch(url(f"/meetings/{m['id']}"), headers=h, json={"quorum_met": True})
    assert client.patch(url(f"/resolutions/{r['id']}"), headers=h, json={"status": "passed", "passed_on": "2026-11-03"}).json()["status"] == "passed"


def test_circular_resolution_needs_every_director(client, h, ha):
    r = client.post(url("/resolutions"), headers=h, json=res_body(number="CR/2026-27/001", title="Borrowing limits", kind="circular")).json()
    assert client.patch(url(f"/resolutions/{r['id']}"), headers=h, json={"status": "passed"}).json()["code"] == "conflict"       # no signature request yet
    doc = upload(client, h, b"Circular resolution on borrowing limits", "Circular resolution", "board", file_name="cr.txt")
    sg = client.post(url("/signature-requests"), headers={**h, **key()}, json=sig_body("board_resolution", r["id"], doc["id"],
                     [{"person_id": PERSON1, "sign_order": 1}, {"person_id": PERSON2, "sign_order": 1}], "click_to_sign")).json()
    cur = client.get(url(f"/resolutions/{r['id']}"), headers=h).json()
    assert cur["status"] == "circulated" and cur["signature_request_id"] == sg["id"]
    assert [s["status"] for s in sg["signers"]] == ["waiting", "waiting"]                                                           # same order: either may sign first
    assert client.patch(url(f"/resolutions/{r['id']}"), headers=h, json={"status": "passed"}).json()["code"] == "conflict"       # nobody has signed
    sign = lambda headers, s: client.post(url(f"/signature-requests/{sg['id']}/signers/{s['id']}/sign"), headers=headers, json={})
    assert sign(ha, sg["signers"][1]).json()["status"] == "partially_signed"
    assert client.patch(url(f"/resolutions/{r['id']}"), headers=h, json={"status": "passed"}).json()["code"] == "conflict"       # one director is still missing
    assert sign(h, sg["signers"][0]).json()["status"] == "completed"
    ok = client.patch(url(f"/resolutions/{r['id']}"), headers=h, json={"status": "passed", "passed_on": TODAY.isoformat()})
    assert ok.status_code == 200 and ok.json()["status"] == "passed" and ok.json()["mgt14_obligation_id"] is None


# ================================================================== notices
def notice_body(**kw):
    return {"authority_code": "GSTN", "registration_id": REG, "form_code": "ASMT-10", "reference_no": "ZD290000123", "period_label": "FY 2023-24",
            "received_on": "2026-10-30", "reply_due_on": "2026-11-14", "hearing_on": "2026-11-28", "demand_amount": "42180.00",
            "summary": "Scrutiny of ITC claimed against GSTR-2A", **kw}


def test_notice_desk(client, h, hv, db):
    assert client.post(url("/notices"), headers=hv, json=notice_body()).json()["code"] == "permission_denied"
    assert client.post(url("/notices"), headers=h, json=notice_body(authority_code="NOPE")).status_code == 400
    assert client.post(url("/notices"), headers=h, json=notice_body(reply_due_on="2026-10-01")).status_code == 400
    assert client.post(url("/notices"), headers=h, json=notice_body(registration_id=str(uuid.uuid4()))).status_code == 400
    r = client.post(url("/notices"), headers=h, json=notice_body())
    assert r.status_code == 201, r.text
    n = r.json()
    assert n["status"] == "open" and n["document_ids"] == [] and n["demand_amount"] == "42180.00"
    tasks = db.execute("SELECT title, due_on, source FROM tasks WHERE entity_id = %s AND subject = 'notice_reply' AND subject_id = %s ORDER BY due_on", (EL, n["id"])).fetchall()
    assert [(t["due_on"], t["source"]) for t in tasks] == [(dt.date(2026, 11, 14), "engine"), (dt.date(2026, 11, 28), "engine")] and "Reply to GSTN notice ASMT-10" in tasks[0]["title"]
    ev = db.execute("SELECT data FROM event_outbox WHERE entity_id = %s AND type = 'notice.received' AND subject_id = %s", (EL, n["id"])).fetchone()
    assert ev["data"]["form"] == "ASMT-10"
    income = client.post(url("/notices"), headers=h, json=notice_body(authority_code="CBDT", form_code="143(1)", reply_due_on=None, hearing_on=None, demand_amount=None, registration_id=None)).json()
    assert [x["form_code"] for x in client.get(url("/notices"), headers=h).json()["data"]] == ["ASMT-10", "143(1)"]            # dated replies first
    S["notice"], S["income"] = n, income


def test_draft_reply_and_closing_a_notice(client, h, hv, hm, db):
    n = S["notice"]
    assert client.post(url(f"/notices/{n['id']}/draft-reply"), headers=hv).json()["code"] == "permission_denied"                    # viewers cannot use the copilot
    assert client.post(url(f"/notices/{uuid.uuid4()}/draft-reply"), headers=h).status_code == 404
    r = client.post(url(f"/notices/{n['id']}/draft-reply"), headers=hm)
    assert r.status_code == 202
    run_worker(db)
    res = job(db, r.json()["id"])
    assert res["status"] == "succeeded" and res["result"]["document_id"]
    got = client.get(url(f"/notices/{n['id']}"), headers=h).json()
    assert got["status"] == "draft_reply" and got["document_ids"] == [res["result"]["document_id"]]
    doc = client.get(url(f"/documents/{res['result']['document_id']}"), headers=h).json()
    text = client.get(doc["download_url"].split("http://localhost:8000")[1]).text
    assert doc["folder"] == "tax" and "DRAFT REPLY" in text and "ASMT-10" in text and "42180.00" in text and "M5 Test Pvt Ltd" in text
    p = client.patch(url(f"/notices/{n['id']}"), headers=h, json={"status": "replied"})
    assert p.status_code == 200 and p.json()["status"] == "replied"
    assert db.execute("SELECT count(*) AS n FROM tasks WHERE subject = 'notice_reply' AND subject_id = %s AND status = 'open'", (n["id"],)).fetchone()["n"] == 0     # replying closes the tasks
    assert client.patch(url(f"/notices/{n['id']}"), headers=h, json={"hearing_on": "2026-01-01"}).status_code == 400
    client.patch(url(f"/notices/{n['id']}"), headers=h, json={"status": "closed"})
    assert client.post(url(f"/notices/{n['id']}/draft-reply"), headers=h).json()["code"] == "conflict"
    assert [x["status"] for x in client.get(url("/notices"), headers=h, params={"status": "open"}).json()["data"]] == ["open"]


# ================================================================== invariants
def test_visibility_files_and_the_audit_chain(client, db):
    stranger = login(client, "stranger@example.in")
    for path in ("/documents", "/contracts", "/meetings", "/notices", "/signature-requests", "/data-rooms"):
        assert client.get(url(path), headers=stranger).status_code == 404
    assert db.execute("SELECT app.audit_verify() AS v").fetchone()["v"] is None
    from app import storage
    for d in db.execute("SELECT storage_key, sha256, size_bytes FROM documents WHERE entity_id = %s AND uploaded_at IS NOT NULL", (EL,)).fetchall():
        data = storage.get(d["storage_key"])
        assert data is not None and len(data) == d["size_bytes"] and hashlib.sha256(data).hexdigest() == d["sha256"].strip()
    assert db.execute("SELECT count(*) AS n FROM documents WHERE entity_id = %s AND uploaded_at IS NULL", (EL,)).fetchone()["n"] == 1      # only the one that never received bytes
