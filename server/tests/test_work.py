"""Work routing: the rules (pure), then the responsibility matrix, filings and approvals that find their owner, a person's queue and
calendar, and tasks, through the API."""
import datetime as dt
import json
import os
import uuid

import psycopg
import pytest
from psycopg.rows import dict_row

from app import routing

DB = os.environ["DATABASE_URL"]
ORG = "00000000-0000-0000-0000-0000006a7000"
EN = "00000000-0000-0000-0000-0000006a7001"
U_OWNER = "00000000-0000-0000-0000-0000006a7011"
OWNER, ACCOUNTANT, CFO, OBSERVER = "work-owner@example.in", "work-accountant@example.in", "work-cfo@example.in", "work-observer@example.in"
TODAY = dt.date.today()
S: dict = {}


# ------------------------------------------------------------------ pure
def test_each_kind_of_work_has_only_the_steps_that_change_something():
    assert routing.valid_slot("gst", "prepare") and routing.valid_slot("gst", "approve") and routing.valid_slot("leave", "approve")
    assert not routing.valid_slot("leave", "prepare") and not routing.valid_slot("payroll_run", "prepare") and not routing.valid_slot("bogus", "approve")
    assert routing.approve_module("leave") == "hr" and routing.approve_module("gst") == "approvals"
    assert {k["key"] for k in routing.kinds_out()} == set(routing.KINDS) and all(routing.valid_slot(k, s) for slots in routing.PRESET_WORK.values() for k, s in slots)


def test_a_preset_takes_only_the_slots_nobody_holds():
    assert routing.preset_slots("accountant", set()) == [("gst", "prepare"), ("tds", "prepare"), ("income_tax", "prepare"), ("other_filing", "prepare")]
    assert routing.preset_slots("accountant", {("gst", "prepare"), ("tds", "prepare")}) == [("income_tax", "prepare"), ("other_filing", "prepare")]
    assert routing.preset_slots("viewer", set()) == [] and routing.preset_slots(None, set()) == []


def test_the_queue_is_ordered_overdue_first_then_by_date_with_undated_last():
    d = lambda n: TODAY + dt.timedelta(days=n)
    items = [{"kind": "task", "title": "No date", "due_on": None}, {"kind": "filing", "title": "Next week", "due_on": d(7)}, {"kind": "filing", "title": "Late", "due_on": d(-2)},
             {"kind": "task", "title": "Also next week", "due_on": d(7)}, {"kind": "approval", "title": "Decide", "due_on": d(7)}]
    out = routing.sort_work(items, TODAY)
    assert [i["title"] for i in out] == ["Late", "Decide", "Next week", "Also next week", "No date"]
    assert [i["overdue"] for i in out] == [True, False, False, False, False]


# ------------------------------------------------------------------ API
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


def key():
    return {"Idempotency-Key": str(uuid.uuid4())}


@pytest.fixture(scope="module", autouse=True)
def seed(db):
    db.execute("INSERT INTO organizations (id, name, kind) VALUES (%s, 'Work Org', 'business_group')", (ORG,))
    db.execute("INSERT INTO entities (id, organization_id, legal_name, constitution, track) VALUES (%s, %s, 'Routing Test Pvt Ltd', 'private_limited', 'business')", (EN, ORG))
    db.execute("INSERT INTO users (id, email, full_name) VALUES (%s, %s, 'Work Owner')", (U_OWNER, OWNER))
    db.execute("INSERT INTO memberships (entity_id, user_id, role, accepted_at) VALUES (%s, %s, 'owner', now())", (EN, U_OWNER))
    yield
    db.execute("DELETE FROM tasks WHERE entity_id = %s", (EN,))


def obligation(db, code, due: dt.date, label="Oct 2026"):
    rule = db.execute("SELECT id FROM compliance_rules WHERE code = %s ORDER BY version DESC LIMIT 1", (code,)).fetchone()["id"]
    return db.execute("""INSERT INTO obligations (entity_id, rule_id, fy, period_start, period_end, period_label, due_date, reason_snapshot)
                         VALUES (%s, %s, '2026-27', %s, %s, %s, %s, %s) RETURNING id, owner_user_id::text, approver_user_id::text""",
                      (EN, rule, due - dt.timedelta(days=40), due - dt.timedelta(days=11), label, due, json.dumps({"test": True}))).fetchone()


def queue(client, h, **params):
    r = client.get("/v1/me/work", headers=h, params={"entity_id": EN, **params})
    assert r.status_code == 200, r.text
    return r.json()


def test_the_database_knows_which_kind_of_work_a_rule_is(db):
    got = {r["code"]: r["kind"] for r in db.execute("SELECT code, app.work_kind_of_rule(code, authority_code) AS kind FROM compliance_rules").fetchall()}
    assert got["GST.GSTR1.MONTHLY"] == "gst" and got["GST.LUT.ANNUAL"] == "gst" and got["TDS.RETURN.QUARTERLY"] == "tds" and got["EPF.ECR.MONTHLY"] == "pf_esi"
    assert got["ESI.MONTHLY"] == "pf_esi" and got["PT.KA.MONTHLY"] == "professional_tax" and got["MCA.AOC4"] == "mca" and got["IT.ADVANCE_TAX"] == "income_tax"
    assert set(got.values()) <= set(routing.KINDS)


def test_the_matrix_starts_empty_and_work_is_unowned(client, db):
    o = login(client, OWNER)
    m = client.get(url("/responsibilities"), headers=o).json()
    assert m["data"] == [] and [k["key"] for k in m["kinds"]][:2] == ["gst", "tds"] and [p["name"] for p in m["people"]] == ["Work Owner"]
    assert set(m["people"][0]["approves"]) == set(routing.KINDS)
    first = obligation(db, "GST.GSTR1.MONTHLY", TODAY + dt.timedelta(days=5), "Sep 2026")
    assert first["owner_user_id"] is None and first["approver_user_id"] is None
    S["gst_old"] = first["id"]
    assert queue(client, o)["data"] == []


def test_people_invited_with_a_preset_take_on_its_work_and_open_filings_follow(client, db):
    o = login(client, OWNER)
    a = client.post(url("/members"), headers=o, json={"email": ACCOUNTANT, "full_name": "Anil Accounts", "preset": "accountant"})
    assert a.status_code == 201 and a.json()["work_taken_on"] == 4, a.text
    c = client.post(url("/members"), headers=o, json={"email": CFO, "full_name": "Chitra Finance", "preset": "cfo"})
    assert c.status_code == 201 and c.json()["work_taken_on"] == 6, c.text
    v = client.post(url("/members"), headers=o, json={"email": OBSERVER, "full_name": "Omar Observer", "preset": "viewer"})
    assert v.status_code == 201 and v.json()["work_taken_on"] == 0
    S.update(accountant=a.json()["user_id"], cfo=c.json()["user_id"], observer=v.json()["user_id"], observer_member=v.json()["id"], accountant_member=a.json()["id"])
    m = client.get(url("/responsibilities"), headers=o).json()
    by = {(r["work_kind"], r["step"]): r["name"] for r in m["data"]}
    assert by[("gst", "prepare")] == "Anil Accounts" and by[("gst", "approve")] == "Chitra Finance" and by[("payroll_run", "approve")] == "Chitra Finance" and ("mca", "prepare") not in by
    people = {p["name"]: p for p in m["people"]}
    assert people["Anil Accounts"]["approves"] == [] and "leave" not in people["Chitra Finance"]["approves"] and "payroll_run" in people["Chitra Finance"]["approves"]
    row = db.execute("SELECT owner_user_id::text AS o, approver_user_id::text AS a FROM obligations WHERE id = %s", (S["gst_old"],)).fetchone()
    assert (row["o"], row["a"]) == (S["accountant"], S["cfo"])                                  # the filing nobody owned now has both


def test_a_new_filing_gets_its_owner_and_approver_from_the_matrix(client, db):
    gst = obligation(db, "GST.GSTR3B.MONTHLY", TODAY - dt.timedelta(days=3))                   # already late
    tds = obligation(db, "TDS.DEPOSIT.MONTHLY", TODAY + dt.timedelta(days=9))
    mca = obligation(db, "MCA.AOC4", TODAY + dt.timedelta(days=60), "FY 2025-26")
    assert (gst["owner_user_id"], gst["approver_user_id"]) == (S["accountant"], S["cfo"]) and tds["owner_user_id"] == S["accountant"]
    assert mca["owner_user_id"] is None and mca["approver_user_id"] is None                     # nobody is named for company filings
    S.update(gst=gst["id"], tds=tds["id"], mca=mca["id"])
    q = queue(client, login(client, ACCOUNTANT))
    assert [i["kind"] for i in q["data"]] == ["filing", "filing", "filing"] and q["total"] == 3 and q["overdue"] == 1 and q["counts"] == {"filing": 3}
    first = q["data"][0]
    assert first["overdue"] is True and first["title"].startswith("GSTR-3B") and first["entity_name"] == "Routing Test Pvt Ltd" and first["href"] == f"/compliance?o={gst['id']}"
    assert queue(client, login(client, CFO))["data"] == [] and queue(client, login(client, OBSERVER))["data"] == []


def test_a_request_for_approval_goes_to_the_named_approver_but_never_to_whoever_raised_it(client, db):
    a, c, o = login(client, ACCOUNTANT), login(client, CFO), login(client, OWNER)
    r = client.post(url(f"/obligations/{S['gst']}/submit"), headers=a, json={})
    assert r.status_code == 201 and str(r.json()["assigned_to"]) == S["cfo"], r.text
    S["approval"] = r.json()["id"]
    assert [(i["kind"], i["title"][:7]) for i in queue(client, c)["data"]] == [("approval", "Approve")]
    assert [i["kind"] for i in queue(client, o)["data"]] == []                                  # it has a named approver, so it is not in the owner's queue
    mine = client.post(url(f"/obligations/{S['tds']}/submit"), headers=c, json={})              # the CFO is the named approver, and raises it
    assert mine.status_code == 201 and mine.json()["assigned_to"] is None
    assert [i["kind"] for i in queue(client, o)["data"]] == ["approval"]                        # so it falls to the other approvers
    assert len(queue(client, c)["data"]) == 1                                                   # and is not offered to the person who raised it
    ok = client.post(url(f"/approvals/{S['approval']}/approve"), headers={**c, **key()}, json={})
    assert ok.status_code == 200, ok.text
    assert queue(client, c)["data"] == []


def test_only_an_owner_sets_the_matrix_and_approvers_must_be_able_to_approve(client, db):
    o, c = login(client, OWNER), login(client, CFO)
    slot = lambda kind, step, who: {"work_kind": kind, "step": step, "user_id": who}
    good = [slot("gst", "prepare", S["cfo"]), slot("gst", "approve", U_OWNER), slot("mca", "prepare", S["cfo"])]
    assert client.put(url("/responsibilities"), headers=c, json={"data": good}).status_code == 403
    assert client.get(url("/responsibilities"), headers=c).status_code == 200
    for bad, field in (([slot("payroll_run", "prepare", S["cfo"])], "work_kind"), ([slot("gst", "approve", S["accountant"])], "user_id"), ([slot("leave", "approve", S["cfo"])], "user_id"),
                       ([slot("gst", "prepare", str(uuid.uuid4()))], "user_id"), ([slot("gst", "prepare", S["cfo"]), slot("gst", "prepare", U_OWNER)], "step")):
        r = client.put(url("/responsibilities"), headers=o, json={"data": bad})
        assert r.status_code == 400 and field in r.json()["errors"][0]["field"], r.text
    db.execute("UPDATE obligations SET owner_user_id = %s WHERE id = %s", (S["observer"], S["mca"]))        # an owner chosen by hand
    r = client.put(url("/responsibilities"), headers=o, json={"data": good})
    assert r.status_code == 200, r.text
    assert len(r.json()["data"]) == 3 and r.json()["filings_reassigned"] >= 1
    rows = {str(x["id"]): x for x in db.execute("SELECT id, owner_user_id::text AS o, approver_user_id::text AS a, status::text FROM obligations WHERE entity_id = %s", (EN,)).fetchall()}
    assert rows[str(S["gst_old"])]["o"] == S["cfo"] and rows[str(S["gst_old"])]["a"] == U_OWNER      # moved with the matrix
    assert rows[str(S["mca"])]["o"] == S["observer"]                                                  # the hand-picked owner stays
    assert rows[str(S["tds"])]["o"] is None                                                           # nobody prepares TDS now, and it was the matrix's choice
    assert db.execute("SELECT count(*) AS n FROM audit_log WHERE entity_id = %s AND action = 'responsibilities.set'", (EN,)).fetchone()["n"] == 1


def test_tasks_are_given_to_a_person_and_finished_by_them(client):
    o, v, a = login(client, OWNER), login(client, OBSERVER), login(client, ACCOUNTANT)
    body = {"title": "Collect the bank confirmations", "description": "For the audit", "assignee_id": S["observer"], "due_on": str(TODAY + dt.timedelta(days=2))}
    assert client.post(url("/tasks"), headers=v, json=body).status_code == 403                 # an observer cannot hand out work
    assert client.post(url("/tasks"), headers=o, json={**body, "assignee_id": str(uuid.uuid4())}).status_code == 400
    r = client.post(url("/tasks"), headers=o, json=body)
    assert r.status_code == 201, r.text
    t = r.json()
    assert (t["assignee_name"], t["created_by_name"], t["status"], t["source"]) == ("Omar Observer", "Work Owner", "open", "user")
    loose = client.post(url("/tasks"), headers=a, json={"title": "Renew the domain"}).json()
    assert loose["assignee_id"] is None
    assert [x["title"] for x in client.get(url("/tasks"), headers=v, params={"assignee": "me"}).json()["data"]] == ["Collect the bank confirmations"]
    assert [x["title"] for x in client.get(url("/tasks"), headers=o, params={"assignee": "none"}).json()["data"]] == ["Renew the domain"]
    assert client.get(url("/tasks"), headers=o, params={"assignee": "bogus"}).status_code == 400
    tasks = lambda h: [(i["title"], i["detail"]) for i in queue(client, h)["data"] if i["kind"] == "task"]
    assert tasks(v) == [("Collect the bank confirmations", "For the audit")]
    assert queue(client, v)["counts"] == {"filing": 1, "task": 1}                              # with the company filing they were given by hand
    assert client.patch(url(f"/tasks/{t['id']}"), headers=v, json={"title": "Something easier"}).status_code == 403      # theirs to finish, not to rewrite
    assert client.patch(url(f"/tasks/{loose['id']}"), headers=v, json={"status": "done"}).status_code == 403            # not theirs at all
    done = client.patch(url(f"/tasks/{t['id']}"), headers=v, json={"status": "done"})
    assert done.status_code == 200 and done.json()["status"] == "done" and done.json()["completed_at"]
    assert tasks(v) == []
    moved = client.patch(url(f"/tasks/{loose['id']}"), headers=o, json={"assignee_id": S["accountant"], "due_on": str(TODAY)})
    assert moved.status_code == 200 and moved.json()["assignee_name"] == "Anil Accounts"
    assert "Renew the domain" in [i["title"] for i in queue(client, a)["data"]]
    assert client.patch(url(f"/tasks/{uuid.uuid4()}"), headers=o, json={"status": "done"}).status_code == 404


def test_leave_and_document_requests_reach_the_people_who_can_act_on_them(client, db):
    o, a, c, v = login(client, OWNER), login(client, ACCOUNTANT), login(client, CFO), login(client, OBSERVER)
    hire = client.post(url("/employees/onboard"), headers=o, json={"full_name": "Leela Rao", "date_of_joining": str(TODAY - dt.timedelta(days=200)), "work_state": "29", "ctc_annual": "600000"})
    assert hire.status_code == 201, hire.text
    day = TODAY + dt.timedelta(days=(7 - TODAY.weekday()) % 7 + 7)                             # a Monday at least a week away
    lv = client.post(url("/leave-requests"), headers=o, json={"employee_id": hire.json()["employee"]["id"], "leave_type": "CL", "from": str(day), "to": str(day)})
    assert lv.status_code == 201, lv.text
    db.execute("INSERT INTO document_requests (entity_id, requested_by_name, title, due_on) VALUES (%s, 'Mehta & Co', 'Bank confirmations as of 31 March', %s)", (EN, TODAY + dt.timedelta(days=4)))
    kinds = lambda h: sorted({i["kind"] for i in queue(client, h)["data"]})
    assert "leave" in kinds(o) and "document_request" in kinds(o)                               # nobody is named for leave: whoever can approve HR decides
    assert "leave" not in kinds(c) and "document_request" in kinds(c) and "document_request" in kinds(a)      # the CFO only reads HR; both can answer a document request
    assert kinds(v) == ["filing"]                                                               # an observer can do neither; only the filing given to them by hand
    item = next(i for i in queue(client, o)["data"] if i["kind"] == "leave")
    assert item["title"] == "Leela Rao: Casual leave" and item["due_on"] == str(day) and item["href"] == "/payroll/leave"
    hr = client.patch(url(f"/members/{S['accountant_member']}"), headers=o, json={"preset": "hr_manager"})
    assert hr.status_code == 200 and hr.json()["work_taken_on"] == 3, hr.text                   # PF and ESI, professional tax, and leave
    assert "leave" in kinds(a) and "leave" not in kinds(o)                                      # now a named person decides leave


def test_the_calendar_shows_dated_work_and_holidays(client):
    o = login(client, OWNER)
    assert client.post(url("/holidays"), headers=o, json={"date": str(TODAY + dt.timedelta(days=3)), "name": "Founders' day"}).status_code == 201
    r = client.get("/v1/me/calendar", headers=o, params={"entity_id": EN})
    assert r.status_code == 200, r.text
    cal = r.json()
    assert cal["from"] == str(TODAY) and cal["to"] == str(TODAY + dt.timedelta(days=34))
    kinds = [i["kind"] for d in cal["days"] for i in d["items"]]
    assert "holiday" in kinds and "document_request" in kinds and "leave" not in kinds and [d["date"] for d in cal["days"]] == sorted(d["date"] for d in cal["days"])
    assert client.get("/v1/me/calendar", headers=o, params={"from": str(TODAY), "to": str(TODAY - dt.timedelta(days=1))}).status_code == 400
    assert client.get("/v1/me/work").status_code == 401


def test_a_person_who_leaves_or_loses_the_level_gives_the_work_back(client, db):
    o = login(client, OWNER)
    slots = lambda: {(r["work_kind"], r["step"]): r["user_id"] for r in client.get(url("/responsibilities"), headers=o).json()["data"]}
    assert slots()[("leave", "approve")] == S["accountant"]
    r = client.patch(url(f"/members/{S['accountant_member']}"), headers=o, json={"scopes": {"hr": "write"}})
    assert r.status_code == 200 and ("leave", "approve") not in slots() and slots()[("pf_esi", "prepare")] == S["accountant"]      # cannot approve leave any more; still prepares PF
    assert client.delete(url(f"/members/{S['accountant_member']}"), headers=o).status_code == 204
    assert all(u != S["accountant"] for u in slots().values())
    assert db.execute("SELECT app.audit_verify() AS v").fetchone()["v"] is None
    kinds = {r["type"] for r in db.execute("SELECT DISTINCT type FROM event_outbox WHERE entity_id = %s", (EN,)).fetchall()}
    assert {"responsibilities.changed", "task.assigned"} <= kinds
