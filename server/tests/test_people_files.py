"""Employee self-service, the rest (migration 44): address and emergency contact, proofs, tasks, a manager who decides leave, and the emailed invitation."""
import base64
import datetime as dt
import os

import psycopg
import pytest
from psycopg.rows import dict_row

DB = os.environ["DATABASE_URL"]
ORG = "00000000-0000-0000-0000-0000007c0000"
EN = "00000000-0000-0000-0000-0000007c0010"
U_OWNER, U_HR, U_SALES = ("00000000-0000-0000-0000-0000007c002%d" % i for i in (1, 2, 3))
MIRA, RAVI, TARA = ("00000000-0000-0000-0000-0000007c003%d" % i for i in (1, 2, 3))
OWNER, HR, SALES = "pf-owner@example.in", "pf-hr@example.in", "pf-sales@example.in"
MIRA_EMAIL, RAVI_EMAIL, TARA_EMAIL = "pf-mira@example.in", "pf-ravi@example.in", "pf-tara@example.in"
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
PDF = b"%PDF-1.4\n" + b"x" * 64
S: dict = {}
TODAY = dt.date.today()


def weekday_from(n):
    d = TODAY + dt.timedelta(days=n)
    while d.weekday() >= 5:
        d += dt.timedelta(days=1)
    return d


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
    x("INSERT INTO organizations (id, name, kind) VALUES (%s, 'PF Org', 'business_group')", (ORG,))
    x("INSERT INTO entities (id, organization_id, legal_name, constitution, track) VALUES (%s, %s, 'People Files Pvt Ltd', 'private_limited', 'business')", (EN, ORG))
    x("INSERT INTO users (id, email, full_name) VALUES (%s, %s, 'Pf Owner'), (%s, %s, 'Pf HR'), (%s, %s, 'Pf Sales')", (U_OWNER, OWNER, U_HR, HR, U_SALES, SALES))
    x("""INSERT INTO memberships (entity_id, user_id, role, scopes, accepted_at) VALUES (%s, %s, 'owner', NULL, now()),
         (%s, %s, 'approver', '{"hr": "approve"}', now()), (%s, %s, 'maker', '{"sales": "write"}', now())""", (EN, U_OWNER, EN, U_HR, EN, U_SALES))
    x("""INSERT INTO employees (id, entity_id, employee_code, full_name, email, date_of_joining, work_state, designation, status) VALUES
         (%s, %s, 'M1', 'Mira Menon', %s, '2023-04-01', '29', 'Manager', 'active'),
         (%s, %s, 'R1', 'Ravi Rao', %s, '2023-04-01', '29', 'Engineer', 'active'),
         (%s, %s, 'T1', 'Tara Tiwari', %s, '2023-04-01', '29', 'Designer', 'active')""", (MIRA, EN, MIRA_EMAIL, RAVI, EN, RAVI_EMAIL, TARA, EN, TARA_EMAIL))
    yield


def login(client, email):
    r = client.post("/v1/auth/token", json={"grant_type": "otp", "email": email, "otp": "000000"})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def url(path):
    return f"/v1/entities/{EN}{path}"


def b64(b):
    return base64.b64encode(b).decode()


def me(who, path=""):
    return f"/v1/self/{who}{path}"


def test_inviting_queues_an_email_the_worker_sends(client, db):
    from app import mailer, worker
    hr = login(client, HR)
    mailer.SandboxMailer.sent.clear()
    r = client.post(url(f"/employees/{RAVI}/self-service"), headers=hr)
    assert r.status_code == 200 and r.json()["self_service"] == "invited", r.text
    n = db.execute("SELECT status, payload FROM notifications WHERE event_type = 'employee_invitation' AND subject_id = %s", (RAVI,)).fetchone()
    assert n["status"] == "queued" and n["payload"]["to"] == RAVI_EMAIL and "People Files Pvt Ltd" in n["payload"]["subject"]
    assert mailer.SandboxMailer.sent == []                                                       # a request never sends
    assert client.post(url(f"/employees/{RAVI}/self-service"), headers=hr).status_code == 200      # again: nothing more is queued
    assert db.execute("SELECT count(*) AS n FROM notifications WHERE subject_id = %s", (RAVI,)).fetchone()["n"] == 1
    while worker.run_once():
        pass
    to, subject, body = mailer.SandboxMailer.sent[-1]
    assert to == RAVI_EMAIL and "Ravi Rao" in body and "one-time code" in body and "000000" not in body
    assert db.execute("SELECT status FROM notifications WHERE subject_id = %s", (RAVI,)).fetchone()["status"] == "sent"
    assert client.post(url(f"/employees/{RAVI}/self-service/invitation"), headers=hr).status_code == 409        # sent a moment ago
    db.execute("UPDATE notifications SET created_at = now() - interval '1 hour' WHERE subject_id = %s", (RAVI,))
    assert client.post(url(f"/employees/{RAVI}/self-service/invitation"), headers=login(client, SALES)).status_code == 403
    assert client.post(url(f"/employees/{RAVI}/self-service/invitation"), headers=hr).status_code == 202
    while worker.run_once():
        pass
    assert len([s for s in mailer.SandboxMailer.sent if s[0] == RAVI_EMAIL]) == 2
    for who, email in ((MIRA, MIRA_EMAIL), (TARA, TARA_EMAIL)):
        assert client.post(url(f"/employees/{who}/self-service"), headers=hr).status_code == 200
        h = login(client, email)
        assert client.post(f"/v1/me/employments/{who}/accept", headers=h).status_code == 200
        S[who] = h
    h = login(client, RAVI_EMAIL)
    assert client.post(f"/v1/me/employments/{RAVI}/accept", headers=h).status_code == 200
    S[RAVI] = h
    assert client.post(url(f"/employees/{RAVI}/self-service/invitation"), headers=hr).status_code == 409        # accepted: nothing to resend


def test_the_production_guard_refuses_the_sandbox_mailer(monkeypatch):
    from app import security
    monkeypatch.setattr(security, "is_production", lambda: True)
    assert any("BIZ_MAIL_PROVIDER" in p for p in security.problems())


def test_address_and_emergency_contact(client, db):
    h, hr = S[RAVI], login(client, HR)
    assert client.get(me(RAVI, "/contact"), headers=h).json()["address_line1"] is None
    full = {"address_line1": "12 Park Street", "city": "Bengaluru", "state_code": "29", "pincode": "560001", "emergency_name": "Sita Rao", "emergency_relation": "Mother", "emergency_mobile": "+919812345678"}
    assert client.put(me(RAVI, "/contact"), headers=h, json={**full, "pincode": "56001"}).status_code == 400
    assert client.put(me(RAVI, "/contact"), headers=h, json={**full, "city": None}).status_code == 400                     # half an address
    assert client.put(me(RAVI, "/contact"), headers=h, json={**full, "emergency_mobile": None}).status_code == 400          # half a contact
    r = client.put(me(RAVI, "/contact"), headers=h, json=full)
    assert r.status_code == 200 and r.json()["city"] == "Bengaluru" and r.json()["emergency_name"] == "Sita Rao"
    seen = client.get(url(f"/employees/{RAVI}/contact"), headers=hr).json()
    assert seen["pincode"] == "560001" and seen["emergency_mobile"] == "+919812345678"
    assert client.get(url(f"/employees/{RAVI}/contact"), headers=login(client, SALES)).status_code == 403
    assert client.get(me(RAVI, "/contact"), headers=S[TARA]).status_code == 404                                              # someone else's page
    fixed = client.put(url(f"/employees/{RAVI}/contact"), headers=hr, json={**full, "city": "Mysuru"})
    assert fixed.status_code == 200 and client.get(me(RAVI, "/contact"), headers=h).json()["city"] == "Mysuru"
    assert client.put(me(RAVI, "/contact"), headers=h, json={}).json()["address_line1"] is None                              # clearing is allowed
    a = db.execute("SELECT after FROM audit_log WHERE action = 'employee_contact.saved' AND entity_id = %s", (EN,)).fetchone()["after"]
    assert "Park Street" not in str(a) and "Sita" not in str(a)                                                              # the log says that, not what


def test_proofs_are_uploaded_read_back_and_decided_by_hr(client, db):
    h, hr = S[RAVI], login(client, HR)
    body = {"kind": "identity", "label": "Aadhaar card", "file_name": "aadhaar.png", "content_base64": b64(PNG)}
    assert client.post(me(RAVI, "/proofs"), headers=h, json={**body, "content_base64": "!!"}).status_code == 400
    assert client.post(me(RAVI, "/proofs"), headers=h, json={**body, "content_base64": b64(b"MZ" + b"\x00" * 40)}).status_code == 422       # not PDF, JPEG or PNG
    assert client.post(me(RAVI, "/proofs"), headers=h, json={**body, "content_base64": b64(PDF + b"y" * (5 * 1024 * 1024))}).status_code == 400
    assert client.post(me(RAVI, "/proofs"), headers=h, json={**body, "kind": "selfie"}).status_code == 400
    r = client.post(me(RAVI, "/proofs"), headers=h, json=body)
    assert r.status_code == 201, r.text
    first = r.json()
    assert (first["status"], first["mime_type"], first["size_bytes"]) == ("pending", "image/png", len(PNG))
    second = client.post(me(RAVI, "/proofs"), headers=h, json={"kind": "tax", "label": "Rent receipts", "file_name": "rent.pdf", "content_base64": b64(PDF)}).json()
    assert second["mime_type"] == "application/pdf"
    got = client.get(me(RAVI, f"/proofs/{first['id']}/file"), headers=h)
    assert got.status_code == 200 and got.content == PNG and got.headers["content-type"] == "image/png" and got.headers["x-content-type-options"] == "nosniff"
    assert client.get(me(RAVI, f"/proofs/{first['id']}/file"), headers=S[TARA]).status_code == 404
    assert client.get(me(TARA, f"/proofs/{first['id']}/file"), headers=S[TARA]).status_code == 404                           # nor through their own page
    assert [p["label"] for p in client.get(me(RAVI, "/proofs"), headers=h).json()["data"]] == ["Rent receipts", "Aadhaar card"]
    # HR
    listed = client.get(url(f"/employees/{RAVI}/proofs"), headers=hr).json()["data"]
    assert len(listed) == 2
    assert client.get(url(f"/employees/{RAVI}/proofs"), headers=login(client, SALES)).status_code == 403
    assert client.get(url(f"/employees/{RAVI}/proofs/{first['id']}/file"), headers=hr).content == PNG
    assert client.post(url(f"/employees/{RAVI}/proofs/{first['id']}/accept"), headers=login(client, SALES), json={}).status_code == 403
    assert client.post(url(f"/employees/{RAVI}/proofs/{second['id']}/reject"), headers=hr, json={}).status_code in (400, 422)
    assert client.post(url(f"/employees/{RAVI}/proofs/{first['id']}/accept"), headers=hr, json={"note": "Matches the original"}).json()["status"] == "accepted"
    assert client.post(url(f"/employees/{RAVI}/proofs/{first['id']}/accept"), headers=hr, json={}).status_code == 409
    rej = client.post(url(f"/employees/{RAVI}/proofs/{second['id']}/reject"), headers=hr, json={"note": "Show the landlord's PAN"})
    assert rej.json()["status"] == "rejected"
    mine = {p["label"]: p for p in client.get(me(RAVI, "/proofs"), headers=h).json()["data"]}
    assert mine["Rent receipts"]["hr_note"] == "Show the landlord's PAN" and mine["Aadhaar card"]["status"] == "accepted"
    assert client.delete(me(RAVI, f"/proofs/{first['id']}"), headers=h).status_code == 409                                    # decided: it stays
    third = client.post(me(RAVI, "/proofs"), headers=h, json={**body, "label": "Passport"}).json()
    gone = client.delete(me(RAVI, f"/proofs/{third['id']}"), headers=h)
    assert gone.status_code == 200 and len(gone.json()["data"]) == 2
    assert db.execute("SELECT count(*) AS n FROM audit_log WHERE action = 'employee_proof.viewed' AND entity_id = %s", (EN,)).fetchone()["n"] == 1


def test_hr_hands_out_tasks_and_the_person_marks_them_done(client):
    h, hr = S[RAVI], login(client, HR)
    assert client.post(url("/employee-tasks"), headers=login(client, SALES), json={"employee_id": RAVI, "title": "x"}).status_code == 403
    assert client.post(url("/employee-tasks"), headers=hr, json={"employee_id": RAVI, "title": ""}).status_code == 400
    t = client.post(url("/employee-tasks"), headers=hr, json={"employee_id": RAVI, "title": "Send your last payslip", "detail": "From the previous employer", "due_on": str(weekday_from(7))})
    assert t.status_code == 201, t.text
    t2 = client.post(url("/employee-tasks"), headers=hr, json={"employee_id": RAVI, "title": "Sign the NDA"}).json()
    other = client.post(url("/employee-tasks"), headers=hr, json={"employee_id": TARA, "title": "Not yours"}).json()
    mine = client.get(me(RAVI, "/tasks"), headers=h).json()
    assert mine["open"] == 2 and [x["title"] for x in mine["data"]] == ["Send your last payslip", "Sign the NDA"]                  # dated first
    assert client.put(me(RAVI, f"/tasks/{other['id']}"), headers=h, json={"done": True}).status_code == 404
    done = client.put(me(RAVI, f"/tasks/{t.json()['id']}"), headers=h, json={"done": True}).json()
    assert done["open"] == 1 and done["data"][-1]["status"] == "done" and done["data"][-1]["done_at"]
    assert client.put(me(RAVI, f"/tasks/{t.json()['id']}"), headers=h, json={"done": True}).status_code == 200                      # again: nothing changes
    seen = client.get(url("/employee-tasks?employee_id=" + RAVI), headers=hr).json()["data"]
    assert [(x["title"], x["status"]) for x in seen] == [("Sign the NDA", "open"), ("Send your last payslip", "done")]
    assert client.post(url(f"/employee-tasks/{t2['id']}/cancel"), headers=hr).json()["status"] == "cancelled"
    assert client.post(url(f"/employee-tasks/{t2['id']}/cancel"), headers=hr).status_code == 409
    assert client.post(url(f"/employee-tasks/{t.json()['id']}/cancel"), headers=hr).status_code == 409                                  # done: stays
    assert [x["title"] for x in client.get(me(RAVI, "/tasks"), headers=h).json()["data"]] == ["Send your last payslip"]
    assert client.put(me(RAVI, f"/tasks/{t2['id']}"), headers=h, json={"done": True}).status_code == 404                                # withdrawn


def test_a_manager_decides_the_leave_of_their_team(client, db):
    hr, owner = login(client, HR), login(client, OWNER)
    types = client.get(url("/leave-types"), headers=owner).json()["data"]
    paid, unpaid = next(t for t in types if t["paid"]), next(t for t in types if not t["paid"])
    assert client.put(url(f"/employees/{RAVI}/manager"), headers=login(client, SALES), json={"manager_id": MIRA}).status_code == 403
    assert client.put(url(f"/employees/{RAVI}/manager"), headers=hr, json={"manager_id": RAVI}).status_code in (400, 409, 500)      # not oneself
    set_ = client.put(url(f"/employees/{RAVI}/manager"), headers=hr, json={"manager_id": MIRA})
    assert set_.status_code == 200 and set_.json()["manager_name"] == "Mira Menon"
    assert client.put(url(f"/employees/{MIRA}/manager"), headers=hr, json={"manager_id": RAVI}).status_code == 409                    # a loop
    client.put(url(f"/employees/{TARA}/manager"), headers=hr, json={"manager_id": RAVI})
    assert client.put(url(f"/employees/{MIRA}/manager"), headers=hr, json={"manager_id": TARA}).status_code == 409                    # a longer loop
    assert next(x for x in client.get(url("/hr/people"), headers=hr).json()["data"] if x["id"] == RAVI)["manager_name"] == "Mira Menon"
    team = client.get(me(MIRA, "/team"), headers=S[MIRA]).json()
    assert [r["full_name"] for r in team["reports"]] == ["Ravi Rao"] and team["manager"] is None and team["pending"] == 0
    assert client.get(me(RAVI, "/team"), headers=S[RAVI]).json()["manager"]["full_name"] == "Mira Menon"
    assert client.get(me(MIRA, "/team"), headers=S[TARA]).status_code == 404
    start = weekday_from(21)
    ask = {"leave_type": paid["code"], "from": str(start), "to": str(start), "reason": "Wedding"}
    rid = client.post(me(RAVI, "/leave-requests"), headers=S[RAVI], json=ask).json()["id"]
    team = client.get(me(MIRA, "/team"), headers=S[MIRA]).json()
    assert team["pending"] == 1 and team["leave"][0]["id"] == rid and team["leave"][0]["mine_to_approve"] is True
    assert client.post(me(TARA, f"/team/leave/{rid}/approve"), headers=S[TARA], json={}).status_code == 404                          # not their report
    assert client.post(me(RAVI, f"/team/leave/{rid}/approve"), headers=S[RAVI], json={}).status_code == 404                          # a person cannot approve their own
    ok = client.post(me(MIRA, f"/team/leave/{rid}/approve"), headers=S[MIRA], json={"note": "Enjoy"})
    assert ok.status_code == 200 and ok.json()["pending"] == 0 and ok.json()["leave"][0]["status"] == "approved" and ok.json()["leave"][0]["decided_by_me"]
    assert client.post(me(MIRA, f"/team/leave/{rid}/reject"), headers=S[MIRA], json={}).status_code == 409                           # already decided
    row = db.execute("SELECT status, decided_by_manager, approved_by FROM leave_requests WHERE id = %s", (rid,)).fetchone()
    assert (row["status"], str(row["decided_by_manager"])) == ("approved", MIRA) and row["approved_by"] is not None
    seen = next(r for r in client.get(url("/leave-requests"), headers=hr).json()["data"] if r["id"] == rid)
    assert seen["status"] == "approved" and seen["decided_by_manager"] == "Mira Menon"                                                  # HR sees who decided
    assert db.execute("SELECT count(*) AS n FROM audit_log WHERE action = 'leave.approved' AND subject_id = %s", (rid,)).fetchone()["n"] == 1
    # unpaid days are HR's: a manager can refuse them but not approve them
    start2 = weekday_from(28)
    lop = client.post(me(RAVI, "/leave-requests"), headers=S[RAVI], json={"leave_type": unpaid["code"], "from": str(start2), "to": str(start2)}).json()
    assert lop["lop_days"] != "0" and client.get(me(MIRA, "/team"), headers=S[MIRA]).json()["leave"][0]["mine_to_approve"] is False
    r = client.post(me(MIRA, f"/team/leave/{lop['id']}/approve"), headers=S[MIRA], json={})
    assert r.status_code == 409 and r.json()["code"] == "leave_unpaid_is_hr", r.text
    after = client.post(me(MIRA, f"/team/leave/{lop['id']}/reject"), headers=S[MIRA], json={"note": "Not now"}).json()
    assert next(x for x in after["leave"] if x["id"] == lop["id"])["status"] == "rejected" and after["pending"] == 0
    assert client.get(me(RAVI, "/leave"), headers=S[RAVI]).json()["requests"][0]["decision_note"] == "Not now"
    # a manager who leaves, or is removed, stops being one
    assert client.put(url(f"/employees/{RAVI}/manager"), headers=hr, json={"manager_id": None}).json()["manager_id"] is None
    assert client.get(me(MIRA, "/team"), headers=S[MIRA]).json()["reports"] == []


def test_the_new_tables_are_closed_to_everyone_without_hr(client, db):
    with psycopg.connect(DB, autocommit=False, row_factory=dict_row) as c:
        c.execute("SET LOCAL ROLE biz_self")
        for t in ("employee_contacts", "employee_proofs", "employee_tasks"):
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                c.execute(f"SELECT 1 FROM {t}")
            c.rollback()
            c.execute("SET LOCAL ROLE biz_self")
    with psycopg.connect(DB, autocommit=False, row_factory=dict_row) as c:
        c.execute("SET LOCAL ROLE biz_app")
        c.execute("SELECT set_config('app.user_id', %s, true)", (U_SALES,))
        for t in ("employee_contacts", "employee_proofs", "employee_tasks"):
            assert c.execute(f"SELECT count(*) AS n FROM {t}").fetchone()["n"] == 0
