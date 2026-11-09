"""Opening position from earlier books: opening trial balance, open invoices, open bills. Own entity; tests build on one another."""
import datetime as dt
import os
import uuid

import psycopg
import pytest
from psycopg.rows import dict_row

from app import history

DB = os.environ["DATABASE_URL"]
ORG = "00000000-0000-0000-0000-0000000000a1"
EX = "00000000-0000-0000-0000-0000000000d7"
REG = "00000000-0000-0000-0000-000000000531"
U_OWNER, U_VIEWER = "00000000-0000-0000-0000-000000000611", "00000000-0000-0000-0000-000000000612"
OWNER, VIEWER = "hist-owner@example.in", "hist-viewer@example.in"
GSTIN = "29AAAPH7777Q1Z5"
S: dict = {}
TODAY = dt.date(2026, 11, 8)
AS_OF = "2026-03-31"


@pytest.fixture(scope="module")
def db():
    with psycopg.connect(DB, autocommit=True, row_factory=dict_row) as c:
        yield c


@pytest.fixture(scope="module", autouse=True)
def entity(db):
    db.execute("""INSERT INTO entities (id, organization_id, legal_name, constitution, track, pan, cin, incorporated_on, registered_state)
                  VALUES (%s, %s, 'History Test Pvt Ltd', 'private_limited', 'business', 'AAAPH7777Q', 'U26510KA2019PTC777777', '2019-08-14', '29')""", (EX, ORG))
    db.execute("INSERT INTO entity_registrations (id, entity_id, kind, number, state_code, gst_filing_frequency) VALUES (%s, %s, 'gstin', %s, '29', 'monthly')", (REG, EX, GSTIN))
    db.execute("INSERT INTO users (id, email, full_name) VALUES (%s, %s, 'Hist Owner'), (%s, %s, 'Hist Viewer')", (U_OWNER, OWNER, U_VIEWER, VIEWER))
    db.execute("INSERT INTO memberships (entity_id, user_id, role) VALUES (%s, %s, 'owner'), (%s, %s, 'viewer')", (EX, U_OWNER, EX, U_VIEWER))


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
def hv(client): return login(client, VIEWER)


def post(client, h, kind, rows, dry_run=True, **extra):
    body = {"kind": kind, "as_of": AS_OF, "dry_run": dry_run, "rows": rows, **extra}
    return client.post(f"/v1/entities/{EX}/history/imports", headers={**h, "Idempotency-Key": str(uuid.uuid4())}, json=body)


TB = [
    {"account_code": "1100", "account_name": "ICICI current", "type": "asset", "subtype": "bank", "debit": "500000.00"},
    {"account_code": "1200", "account_name": "Trade receivables", "type": "asset", "subtype": "receivable", "debit": "236000.00"},
    {"account_code": "2100", "account_name": "Trade payables", "type": "liability", "subtype": "payable", "credit": "118000.00"},
    {"account_code": "3000", "account_name": "Share capital", "type": "equity", "credit": "618000.00"},
]
INV = [
    {"number": "INV/25-26/001", "date": "2026-02-10", "customer_name": "Coastal Grid Utilities", "customer_gstin": "32AABCX9999K1ZP", "taxable": "100000.00", "igst": "18000.00", "total": "118000.00"},
    {"number": "INV/25-26/002", "date": "2026-03-05", "customer_name": "Local Retail", "customer_gstin": "29AABCL1234F1Z5", "taxable": "100000.00", "cgst": "9000.00", "sgst": "9000.00",
     "total": "118000.00", "outstanding": "58000.00"},
]
BILL = [
    {"bill_no": "SC-778", "date": "2026-03-01", "supplier_name": "Shakti Castings", "supplier_gstin": "29AAECS5555M1Z3", "taxable": "100000.00", "cgst": "9000.00", "sgst": "9000.00", "total": "118000.00"},
]


# ------------------------------------------------------------------ pure checks
def test_money_cells_are_read_exactly():
    assert history.d2("1,18,000.50") == history.d2("118000.50") and history.d2("") is None
    with pytest.raises(ValueError):
        history.d2("1.234")
    with pytest.raises(ValueError):
        history.d2("abc")


def test_a_trial_balance_that_does_not_tie_is_reported_with_the_difference():
    rows = [dict(TB[0]), dict(TB[3], credit="600000.00")]
    _, problems, summary, _ = history.check_opening(rows, dt.date(2026, 3, 31), TODAY)
    assert problems and "difference 100000.00" in problems[0]["message"] and problems[0]["row"] == 0


def test_invoice_rows_are_checked_against_each_other():
    regs = [{"id": REG, "number": GSTIN, "state_code": "29"}]
    rows = [dict(INV[0], igst="0", cgst="9000", sgst="9000"),                      # inter-state but split tax
            dict(INV[1], total="130000.00"),                                       # total does not add up
            dict(INV[1], number="X", customer_gstin="", place_of_supply=""),       # no place of supply
            dict(INV[0], number="INV/25-26/001"), dict(INV[0], date="2999-01-01")]
    _, problems, _, _ = history.check_invoices(rows, dt.date(2026, 3, 31), TODAY, regs)
    text = " | ".join(f"{p['row']}:{p['field']}:{p['message']}" for p in problems)
    for needle in ("1:cgst", "2:total", "3:place_of_supply", "in the future"):
        assert needle in text, text


# ------------------------------------------------------------------ the API
def test_a_viewer_cannot_import(client, hv):
    assert post(client, hv, "opening_balances", TB).status_code in (403, 404)


def test_opening_dry_run_saves_nothing(client, h, db):
    bad = post(client, h, "opening_balances", [TB[0], TB[3]]).json()
    assert bad["accepted"] == 0 and bad["problem_count"] == 1 and "does not tie" in bad["problems"][0]["message"]
    ok = post(client, h, "opening_balances", TB).json()
    assert ok["dry_run"] is True and ok["accepted"] == 4 and ok["summary"] == {"debits": "736000.00", "credits": "736000.00", "lines": 4} and ok["batch_id"] is None
    assert db.execute("SELECT count(*) AS n FROM import_batches WHERE entity_id = %s", (EX,)).fetchone()["n"] == 0
    assert db.execute("SELECT count(*) AS n FROM journal_entries WHERE entity_id = %s", (EX,)).fetchone()["n"] == 0


def test_a_rejected_real_import_is_a_422_with_rows(client, h):
    r = post(client, h, "opening_balances", [TB[0], TB[3]], dry_run=False)
    assert r.status_code == 422 and r.json()["code"] == "import_rejected" and r.json()["errors"]


def test_opening_balances_post_one_journal_and_cannot_be_imported_twice(client, h, db):
    r = post(client, h, "opening_balances", TB, dry_run=False, label="Opening 1 Apr 2026")
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["batch_id"] and out["summary"]["accounts_created"] == 4
    lines = db.execute("""SELECT a.code, l.debit, l.credit, j.source::text AS src, j.entry_date FROM journal_lines l JOIN journal_entries j ON j.id = l.entry_id
                          JOIN ledger_accounts a ON a.id = l.account_id WHERE j.entity_id = %s AND j.status = 'posted' ORDER BY a.code""", (EX,)).fetchall()
    assert [(l["code"], str(l["debit"]), str(l["credit"])) for l in lines][:2] == [("1100", "500000.00", "0.00"), ("1200", "236000.00", "0.00")]
    assert {l["src"] for l in lines} == {"opening"} and str(lines[0]["entry_date"]) == AS_OF
    again = post(client, h, "opening_balances", TB, dry_run=False)
    assert again.status_code == 409 and again.json()["code"] == "conflict"
    S["opening"] = out["batch_id"]


def test_open_invoices_dry_run_then_import(client, h, db):
    dry = post(client, h, "open_invoices", INV).json()
    assert dry["accepted"] == 2 and dry["summary"] == {"invoices": 2, "billed": "236000.00", "outstanding": "176000.00", "settled": "60000.00"}
    r = post(client, h, "open_invoices", INV, dry_run=False)
    assert r.status_code == 200, r.text
    S["inv"] = r.json()["batch_id"]
    docs = {d["number"]: d for d in db.execute("SELECT * FROM sales_documents WHERE entity_id = %s", (EX,)).fetchall()}
    a, b = docs["INV/25-26/001"], docs["INV/25-26/002"]
    assert (a["status"], a["payment_status"], str(a["igst_total"]), a["is_inter_state"], a["irn_status"]) == ("issued", "unpaid", "18000.00", True, "not_required")
    assert (b["payment_status"], str(b["amount_settled"]), str(b["balance_due"]), a["fy"]) == ("partially_paid", "60000.00", "58000.00", "2025-26")
    assert b["journal_entry_id"] is None and str(b["due_date"]) == "2026-04-04"
    assert db.execute("SELECT count(*) AS n FROM parties WHERE entity_id = %s AND is_customer", (EX,)).fetchone()["n"] == 2


def test_the_receivables_ageing_sees_the_imported_invoices(client, h):
    r = client.get(f"/v1/entities/{EX}/receivables/ageing", headers=h)
    assert r.status_code == 200, r.text
    assert sorted(x["total"] for x in r.json()) == ["118000.00", "58000.00"]


def test_an_invoice_number_cannot_be_imported_twice(client, h):
    r = post(client, h, "open_invoices", [INV[0]]).json()
    assert r["accepted"] == 0 and "already exists" in r["problems"][0]["message"]


def test_open_bills_import_marks_the_credit_as_claimed(client, h, db):
    rows = BILL + [dict(BILL[0], bill_no="SC-779", date="2026-03-10", outstanding="50000.00", tds="1000.00")]
    dry = post(client, h, "open_bills", rows).json()
    assert dry["accepted"] == 2 and dry["summary"]["outstanding"] == "168000.00", dry
    r = post(client, h, "open_bills", rows, dry_run=False)
    assert r.status_code == 200, r.text
    S["bill"] = r.json()["batch_id"]
    bills = {b["bill_no"]: b for b in db.execute("SELECT * FROM purchase_bills WHERE entity_id = %s", (EX,)).fetchall()}
    a, b = bills["SC-778"], bills["SC-779"]
    assert (a["status"], str(a["balance_due"]), a["itc_claim_period"], a["is_inter_state"]) == ("booked", "118000.00", "2026-03", False)
    assert (b["status"], str(b["amount_paid"]), str(b["balance_due"])) == ("partially_paid", "67000.00", "50000.00")


def test_batches_list_and_undo(client, h, db):
    k = {**h, "Idempotency-Key": str(uuid.uuid4())}
    listed = client.get(f"/v1/entities/{EX}/history/batches", headers=h).json()["data"]
    assert {b["kind"] for b in listed} == {"opening_balances", "open_invoices", "open_bills"}
    # money received against an imported invoice blocks the undo
    db.execute("UPDATE sales_documents SET amount_settled = amount_settled + 1000 WHERE number = 'INV/25-26/001' AND entity_id = %s", (EX,))
    blocked = client.post(f"/v1/entities/{EX}/history/batches/{S['inv']}/undo", headers=k)
    assert blocked.status_code == 409
    db.execute("UPDATE sales_documents SET amount_settled = amount_settled - 1000 WHERE number = 'INV/25-26/001' AND entity_id = %s", (EX,))
    done = client.post(f"/v1/entities/{EX}/history/batches/{S['inv']}/undo", headers={**h, "Idempotency-Key": str(uuid.uuid4())})
    assert done.status_code == 200 and done.json()["removed"] == 2 and done.json()["undone_at"]
    assert db.execute("SELECT count(*) AS n FROM sales_documents WHERE entity_id = %s", (EX,)).fetchone()["n"] == 0
    assert client.post(f"/v1/entities/{EX}/history/batches/{S['inv']}/undo", headers={**h, "Idempotency-Key": str(uuid.uuid4())}).status_code == 409
    ok = client.post(f"/v1/entities/{EX}/history/batches/{S['opening']}/undo", headers={**h, "Idempotency-Key": str(uuid.uuid4())})
    assert ok.status_code == 200, ok.text
    assert db.execute("SELECT status FROM journal_entries WHERE import_batch_id = %s", (S["opening"],)).fetchone()["status"] == "reversed"
    assert client.post(f"/v1/entities/{EX}/history/batches/{uuid.uuid4()}/undo", headers={**h, "Idempotency-Key": str(uuid.uuid4())}).status_code == 404
    assert db.execute("SELECT app.audit_verify() AS v").fetchone()["v"] is None
