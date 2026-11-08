"""Copilot: conversations answered from the company's own records with citations, and actions that only run after a different person approves.

Own entity; the people are an owner who asks, an approver, a maker (can use the copilot, cannot approve), a viewer (cannot use it) and an
outsider. Everything goes through the HTTP API against real tables; the sandbox brain is rule-based, so the answers are deterministic.
"""
import datetime as dt
import json
import os
import uuid

import psycopg
import pytest
from psycopg.rows import dict_row

DB = os.environ["DATABASE_URL"]
ORG = "00000000-0000-0000-0000-000000000700"
EN = "00000000-0000-0000-0000-000000000701"
REG = "00000000-0000-0000-0000-000000000702"
SUPPLIER, CUSTOMER = "00000000-0000-0000-0000-000000000703", "00000000-0000-0000-0000-000000000704"
U_OWNER, U_APPROVER, U_MAKER, U_VIEWER, U_OUT = ("00000000-0000-0000-0000-00000000071%d" % i for i in range(5))
OWNER, APPROVER, MAKER, VIEWER, OUTSIDER = ("cp-%s@example.in" % n for n in ("owner", "approver", "maker", "viewer", "outsider"))
TODAY = dt.date.today()
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


def idem(h):
    return {**h, "Idempotency-Key": str(uuid.uuid4())}


@pytest.fixture(scope="module", autouse=True)
def seed(db):
    db.execute("INSERT INTO organizations (id, name, kind) VALUES (%s, 'Copilot Test Org', 'business_group')", (ORG,))
    db.execute("INSERT INTO entities (id, organization_id, legal_name, constitution, track) VALUES (%s, %s, 'Copilot Test Pvt Ltd', 'private_limited', 'business')", (EN, ORG))
    db.execute("INSERT INTO entity_registrations (id, entity_id, kind, number, state_code, gst_filing_frequency) VALUES (%s, %s, 'gstin', '29AAACC7777C1Z5', '29', 'monthly')", (REG, EN))
    db.execute("""INSERT INTO users (id, email, full_name) VALUES (%s, %s, 'Cp Owner'), (%s, %s, 'Cp Approver'), (%s, %s, 'Cp Maker'), (%s, %s, 'Cp Viewer'), (%s, %s, 'Cp Outsider')""",
               (U_OWNER, OWNER, U_APPROVER, APPROVER, U_MAKER, MAKER, U_VIEWER, VIEWER, U_OUT, OUTSIDER))
    db.execute("""INSERT INTO memberships (entity_id, user_id, role, accepted_at) VALUES (%s, %s, 'owner', now()), (%s, %s, 'approver', now()), (%s, %s, 'maker', now()), (%s, %s, 'viewer', now())""",
               (EN, U_OWNER, EN, U_APPROVER, EN, U_MAKER, EN, U_VIEWER))
    db.execute("""INSERT INTO parties (id, entity_id, legal_name, is_customer, is_vendor, payment_terms_days, status) VALUES
                  (%s, %s, 'Shakti Castings', false, true, 30, 'active'), (%s, %s, 'Delta Retail', true, false, 30, 'active')""", (SUPPLIER, EN, CUSTOMER, EN))
    # a booked bill the supplier has not reported, and one issued invoice that is 20 days late
    bill = db.execute("""INSERT INTO purchase_bills (entity_id, party_id, registration_id, bill_no, bill_date, accepted_on, supplier_state, place_of_supply, taxable_total, cgst_total, sgst_total,
                                                     grand_total, due_date, status) VALUES (%s, %s, %s, 'SC/118', %s, %s, '29', '29', 100000, 9000, 9000, 118000, %s, 'booked') RETURNING id""",
                       (EN, SUPPLIER, REG, TODAY - dt.timedelta(days=20), TODAY - dt.timedelta(days=20), TODAY + dt.timedelta(days=10))).fetchone()["id"]
    db.execute("INSERT INTO itc_matches (entity_id, status, purchase_bill_id, tax_difference) VALUES (%s, 'not_in_portal', %s, 18000)", (EN, bill))
    db.execute("""INSERT INTO sales_documents (entity_id, doc_type, doc_date, registration_id, supplier_state, party_id, buyer_legal_name, bill_to, place_of_supply, supply_type, number, due_date,
                                               taxable_total, cgst_total, sgst_total, grand_total, status, payment_status)
                  VALUES (%s, 'tax_invoice', %s, %s, '29', %s, 'Delta Retail', '{}', '29', 'b2c_small', 'CP/0001', %s, 10000, 900, 900, 11800, 'issued', 'unpaid')""",
               (EN, TODAY - dt.timedelta(days=50), REG, CUSTOMER, TODAY - dt.timedelta(days=20)))
    rule = db.execute("SELECT id FROM compliance_rules WHERE authority_code = 'GSTN' ORDER BY code, version DESC LIMIT 1").fetchone()["id"]
    for start, due, status, label in ((TODAY - dt.timedelta(days=40), TODAY - dt.timedelta(days=5), "overdue", "Sep 2026"),
                                      (TODAY - dt.timedelta(days=9), TODAY + dt.timedelta(days=6), "upcoming", "Oct 2026")):
        db.execute("""INSERT INTO obligations (entity_id, rule_id, fy, period_start, period_end, period_label, due_date, status, reason_snapshot)
                      VALUES (%s, %s, '2026-27', %s, %s, %s, %s, %s, '{}')""", (EN, rule, start, due, label, due, status))
    yield
    db.execute("DELETE FROM tasks WHERE entity_id = %s", (EN,))


def ask(client, h, thread, text, **kw):
    return client.post(f"/v1/entities/{EN}/copilot/threads/{thread}/messages", headers=h, json={"content": text}, **kw)


def test_threads_belong_to_the_person_who_started_them(client):
    owner, maker = login(client, OWNER), login(client, MAKER)
    t = client.post(f"/v1/entities/{EN}/copilot/threads", headers=owner, json={})
    assert t.status_code == 201 and t.json()["title"] == "New conversation" and t.json()["messages"] == []
    S["thread"] = t.json()["id"]
    assert [x["id"] for x in client.get(f"/v1/entities/{EN}/copilot/threads", headers=owner).json()["data"]] == [S["thread"]]
    assert client.get(f"/v1/entities/{EN}/copilot/threads", headers=maker).json()["data"] == []            # a colleague's conversations are not shared
    assert client.get(f"/v1/entities/{EN}/copilot/threads/{S['thread']}", headers=maker).status_code == 404
    assert ask(client, maker, S["thread"], "what is due?").status_code == 404
    assert client.post(f"/v1/entities/{EN}/copilot/threads", headers=login(client, VIEWER), json={}).status_code == 403      # a viewer cannot use it
    assert client.get(f"/v1/entities/{EN}/copilot/threads", headers=login(client, OUTSIDER)).status_code == 404            # an outsider cannot see the company at all


def test_it_answers_from_the_records_and_cites_every_fact(client, db):
    h = login(client, OWNER)
    r = ask(client, h, S["thread"], "Which suppliers are holding up my GST credit?")
    assert r.status_code == 201, r.text
    m = r.json()
    assert m["role"] == "assistant" and "Shakti Castings" in m["content"] and "Rs 18,000" in m["content"] and "[1]" in m["content"]
    assert [(c["ordinal"], c["subject"]) for c in m["citations"]] == [(1, "itc_matches")]
    assert m["citations"][0]["label"].endswith("Shakti Castings")
    [a] = m["actions"]
    assert a["kind"] == "send_supplier_reminders" and a["status"] == "proposed" and a["approval_request_id"] is None and a["payload"]["party_ids"] == [SUPPLIER]
    S["remind"] = a["id"]
    t = client.get(f"/v1/entities/{EN}/copilot/threads/{S['thread']}", headers=h).json()
    assert t["title"].startswith("Which suppliers are holding up") and [x["role"] for x in t["messages"]] == ["user", "assistant"]
    assert db.execute("SELECT model FROM copilot_messages WHERE id = %s", (m["id"],)).fetchone()["model"] == "sandbox"
    # nothing happened: asking is not doing
    assert db.execute("SELECT count(*) AS n FROM tasks WHERE entity_id = %s", (EN,)).fetchone()["n"] == 0
    assert db.execute("SELECT action_taken_at FROM itc_matches WHERE entity_id = %s", (EN,)).fetchone()["action_taken_at"] is None


def test_other_questions_use_other_records(client):
    h = login(client, OWNER)
    th = client.post(f"/v1/entities/{EN}/copilot/threads", headers=h, json={"title": "Money and deadlines"}).json()["id"]
    money = ask(client, h, th, "Who owes me money?").json()
    assert "Rs 11,800" in money["content"] and "CP/0001" in money["content"] and "20 days late" in money["content"]
    assert [c["subject"] for c in money["citations"]] == ["sales_documents"]
    assert [a["kind"] for a in money["actions"]] == ["create_task"] and "Delta Retail" in money["actions"][0]["payload"]["title"]
    S["task_action"] = money["actions"][0]["id"]

    due = ask(client, h, th, "What filings are due or overdue this week?").json()
    assert "2 open filings" in due["content"] and "1 overdue" in due["content"] and "5 days late" in due["content"] and "was due " + (TODAY - dt.timedelta(days=5)).strftime("%-d %b") in due["content"]
    assert [c["subject"] for c in due["citations"]] == ["obligations", "obligations"]

    both = ask(client, h, th, "any overdue invoices or GST credit stuck with suppliers?").json()          # two topics in one question
    assert "Shakti Castings" in both["content"] and "Delta Retail" in both["content"] and [c["ordinal"] for c in both["citations"]] == [1, 2]

    hello = ask(client, h, th, "hello there").json()
    assert hello["citations"] == [] and hello["actions"] == [] and "Try asking" in hello["content"]       # it does not invent an answer
    assert client.get(f"/v1/entities/{EN}/copilot/threads/{th}", headers=h).json()["title"] == "Money and deadlines"     # a chosen title is kept
    assert ask(client, h, th, "   ").status_code in (400, 422)
    assert ask(client, h, th, "x" * 4001).status_code in (400, 422)


def test_the_answer_can_stream(client):
    h = login(client, OWNER)
    th = client.post(f"/v1/entities/{EN}/copilot/threads", headers=h, json={}).json()["id"]
    r = ask(client, {**h, "Accept": "text/event-stream"}, th, "Who owes me money?")
    assert r.status_code == 201 and r.headers["content-type"].startswith("text/event-stream")
    events = [(blk.split("\n")[0][7:], json.loads(blk.split("\n")[1][6:])) for blk in r.text.strip().split("\n\n")]
    kinds = [k for k, _ in events]
    assert kinds[0] == "token" and kinds[-1] == "done" and "citation" in kinds and "action" in kinds
    streamed = "".join(d["text"] for k, d in events if k == "token")
    saved = client.get(f"/v1/entities/{EN}/copilot/threads/{th}", headers=h).json()["messages"][-1]
    assert streamed == saved["content"] and events[-1][1]["message_id"] == saved["id"]


def test_an_action_needs_a_different_person_to_approve_it(client, db):
    owner, approver, maker = login(client, OWNER), login(client, APPROVER), login(client, MAKER)
    url = f"/v1/entities/{EN}/copilot/actions/{S['remind']}/request-approval"
    assert client.post(url, headers=idem(owner), json={"assigned_to": U_VIEWER}).status_code == 400            # a viewer cannot approve
    assert client.post(url, headers=idem(owner), json={"payload_overrides": {"nonsense": 1}}).status_code == 400
    assert client.post(url, headers=idem(maker), json={}).status_code == 404                                    # not their action
    assert client.post(url, headers=owner, json={}).status_code == 400                                          # an Idempotency-Key is required
    key = idem(owner)
    ar = client.post(url, headers=key, json={"assigned_to": U_APPROVER})
    assert ar.status_code == 201, ar.text
    assert ar.json()["subject"] == "copilot_action" and ar.json()["status"] == "pending" and ar.json()["assigned_to"] == U_APPROVER and "remind 1 supplier" in ar.json()["summary"]
    S["ar"] = ar.json()["id"]
    assert client.post(url, headers=key, json={"assigned_to": U_APPROVER}).json()["id"] == S["ar"]               # the same key replays
    assert client.post(url, headers=idem(owner), json={}).status_code == 409                                    # one request per action
    # sent for approval is not approved: nothing has run
    assert db.execute("SELECT status FROM copilot_actions WHERE id = %s", (S["remind"],)).fetchone()["status"] == "proposed"
    assert db.execute("SELECT count(*) AS n FROM tasks WHERE entity_id = %s", (EN,)).fetchone()["n"] == 0
    # the database itself refuses an action marked executed without an approval
    with pytest.raises(psycopg.errors.CheckViolation):
        db.execute("UPDATE copilot_actions SET status = 'executed' WHERE id = %s", (S["task_action"],))

    me = client.post(f"/v1/entities/{EN}/approvals/{S['ar']}/approve", headers=idem(owner), json={})
    assert me.status_code == 403 and me.json()["code"] == "two_person_rule"                                       # the requester cannot approve
    assert client.post(f"/v1/entities/{EN}/approvals/{S['ar']}/approve", headers=idem(maker), json={}).status_code == 403
    ok = client.post(f"/v1/entities/{EN}/approvals/{S['ar']}/approve", headers=idem(approver), json={"note": "go ahead"})
    assert ok.status_code == 200 and ok.json()["status"] == "approved"

    a = next(x for x in client.get(f"/v1/entities/{EN}/copilot/threads/{S['thread']}", headers=owner).json()["messages"][-1]["actions"])
    assert a["status"] == "executed" and a["approval_request_id"] == S["ar"]
    row = db.execute("SELECT payload, executed_at FROM copilot_actions WHERE id = %s", (S["remind"],)).fetchone()
    assert row["payload"]["result"] == {"suppliers": 1, "invoices": 1} and row["executed_at"] is not None
    task = db.execute("SELECT title, source FROM tasks WHERE entity_id = %s", (EN,)).fetchall()
    assert len(task) == 1 and task[0]["source"] == "copilot" and "Shakti Castings" in task[0]["title"]
    assert db.execute("SELECT action FROM itc_matches WHERE entity_id = %s", (EN,)).fetchone()["action"] == "remind_supplier"
    assert {r["action"] for r in db.execute("SELECT action FROM audit_log WHERE entity_id = %s AND action LIKE 'copilot%%'", (EN,)).fetchall()} >= {
        "copilot.answered", "copilot_action.approval_requested", "copilot_action.executed"}
    assert client.post(f"/v1/entities/{EN}/approvals/{S['ar']}/approve", headers=idem(approver), json={}).status_code == 409     # decided once


def test_a_rejected_action_never_runs(client, db):
    owner, approver = login(client, OWNER), login(client, APPROVER)
    ar = client.post(f"/v1/entities/{EN}/copilot/actions/{S['task_action']}/request-approval", headers=idem(owner), json={"assigned_to": U_APPROVER}).json()
    assert client.post(f"/v1/entities/{EN}/approvals/{ar['id']}/reject", headers=idem(approver), json={"note": "not now"}).json()["status"] == "rejected"
    assert db.execute("SELECT status FROM copilot_actions WHERE id = %s", (S["task_action"],)).fetchone()["status"] == "rejected"
    assert db.execute("SELECT count(*) AS n FROM tasks WHERE entity_id = %s", (EN,)).fetchone()["n"] == 1        # only the earlier, approved reminder task


def test_an_action_that_cannot_run_is_marked_failed_and_the_approval_stands(client, db):
    owner, approver = login(client, OWNER), login(client, APPROVER)
    th = client.post(f"/v1/entities/{EN}/copilot/threads", headers=owner, json={}).json()["id"]
    act = ask(client, owner, th, "what filings are overdue?").json()["actions"][0]
    db.execute("UPDATE copilot_actions SET payload = '{}' WHERE id = %s", (act["id"],))                        # a payload the executor cannot use
    ar = client.post(f"/v1/entities/{EN}/copilot/actions/{act['id']}/request-approval", headers=idem(owner), json={"assigned_to": U_APPROVER}).json()
    ok = client.post(f"/v1/entities/{EN}/approvals/{ar['id']}/approve", headers=idem(approver), json={})
    assert ok.status_code == 200 and ok.json()["status"] == "approved"
    row = db.execute("SELECT status, payload FROM copilot_actions WHERE id = %s", (act["id"],)).fetchone()
    assert row["status"] == "failed" and "error" in row["payload"]
    assert db.execute("SELECT count(*) AS n FROM tasks WHERE entity_id = %s", (EN,)).fetchone()["n"] == 1


def test_the_sandbox_brain_is_a_named_adapter():
    from app import adapters
    assert adapters.status()["copilot"] == "SandboxCopilot"
