"""Registering a fixed asset from a purchase bill line: what is left to capitalise, the journal that moves the cost, and the guards."""
import os
import uuid
from decimal import Decimal as D

import psycopg
import pytest
from psycopg.rows import dict_row

DB = os.environ["DATABASE_URL"]
ORG = "00000000-0000-0000-0000-000000f4a000"
EN = "00000000-0000-0000-0000-000000f4a001"
REG = "00000000-0000-0000-0000-000000f4a002"
VENDOR, ASSET_ACCT, EXPENSE_ACCT = ("00000000-0000-0000-0000-000000f4a00%d" % i for i in (3, 4, 5))
U_OWNER, U_VIEWER = "00000000-0000-0000-0000-000000f4a011", "00000000-0000-0000-0000-000000f4a012"
OWNER, VIEWER = "ab-owner@example.in", "ab-viewer@example.in"
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


def key():
    return {"Idempotency-Key": str(uuid.uuid4())}


@pytest.fixture(scope="module", autouse=True)
def seed(db):
    db.execute("INSERT INTO organizations (id, name, kind) VALUES (%s, 'Asset Bills Org', 'business_group')", (ORG,))
    db.execute("INSERT INTO entities (id, organization_id, legal_name, constitution, track) VALUES (%s, %s, 'Asset Bills Pvt Ltd', 'private_limited', 'business')", (EN, ORG))
    db.execute("INSERT INTO entity_registrations (id, entity_id, kind, number, state_code, gst_filing_frequency) VALUES (%s, %s, 'gstin', '29AABCF4444D1Z6', '29', 'monthly')", (REG, EN))
    db.execute("INSERT INTO users (id, email, full_name) VALUES (%s, %s, 'Ab Owner'), (%s, %s, 'Ab Viewer')", (U_OWNER, OWNER, U_VIEWER, VIEWER))
    db.execute("INSERT INTO memberships (entity_id, user_id, role, accepted_at) VALUES (%s, %s, 'owner', now()), (%s, %s, 'viewer', now())", (EN, U_OWNER, EN, U_VIEWER))
    db.execute("INSERT INTO parties (id, entity_id, legal_name, is_vendor, is_customer, pan, payment_terms_days) VALUES (%s, %s, 'Compu Mart Pvt Ltd', true, false, 'AAKFC7777E', 30)", (VENDOR, EN))
    db.execute("INSERT INTO party_gst_registrations (party_id, gstin, state_code, is_default) VALUES (%s, '29AAKFC7777E1Z3', '29', true)", (VENDOR,))
    db.execute("""INSERT INTO ledger_accounts (id, entity_id, code, name, type, subtype) VALUES
                  (%s, %s, '1510', 'Computers (asset account)', 'asset', 'computers_asset'), (%s, %s, '5500', 'Computer expenses', 'expense', 'computer_expense')""",
               (ASSET_ACCT, EN, EXPENSE_ACCT, EN))
    yield


def line(desc, rate, qty="1", hsn="8471", **kw):
    return {"description": desc, "hsn_sac": hsn, "qty": qty, "rate": rate, "gst_rate": 18, **kw}


def make_bill(client, o, no, lines, book=True, **kw):
    r = client.post(url("/purchase-bills"), headers=o, json={"party_id": VENDOR, "registration_id": REG, "bill_no": no, "bill_date": "2025-06-10", "accepted_on": "2025-06-12",
                                                              "lines": lines, **kw})
    assert r.status_code == 201, r.text
    bill = r.json()
    if book:
        b = client.post(url(f"/purchase-bills/{bill['id']}/book"), headers={**o, **key()})
        assert b.status_code == 200, b.text
    return bill["id"]


def balance(db, code):
    return db.execute("""SELECT coalesce(sum(l.debit - l.credit), 0) AS b FROM journal_lines l JOIN journal_entries j ON j.id = l.entry_id
                          JOIN ledger_accounts a ON a.id = l.account_id WHERE j.entity_id = %s AND a.code = %s""", (EN, code)).fetchone()["b"]


def asset_body(**kw):
    return {"name": "Dell laptop", "category": "computers", "acquired_on": "2025-06-10", "cost": "60000.00", **kw}


def test_only_booked_bills_are_offered_with_what_is_left_on_each_line(client):
    o = login(client, OWNER)
    S["b1"] = make_bill(client, o, "CM/101", [line("Dell laptops", "60000.00", qty="2"), line("Installation", "5000.00", hsn="9987", expense_account_id=EXPENSE_ACCT)])
    S["b2"] = make_bill(client, o, "CM/102", [line("Office chair", "10000.00", hsn="9401")], itc_eligibility="ineligible")
    S["draft"] = make_bill(client, o, "CM/103", [line("Printer", "8000.00")], book=False)
    S["b4"] = make_bill(client, o, "CM/104", [line("Server rack", "90000.00", expense_account_id=ASSET_ACCT)])
    r = client.get(url("/fixed-assets/capitalisable-bills"), headers=o)
    assert r.status_code == 200, r.text
    bills = {b["bill_no"]: b for b in r.json()["data"]}
    assert set(bills) == {"CM/101", "CM/102", "CM/104"}                                         # the draft is not offered
    assert bills["CM/101"]["supplier_name"] == "Compu Mart Pvt Ltd" and [l["line_no"] for l in bills["CM/101"]["lines"]] == [1, 2]
    laptops = bills["CM/101"]["lines"][0]
    assert (laptops["description"], laptops["qty"], laptops["capitalisable"], laptops["remaining"], laptops["booked_to_asset_account"]) == ("Dell laptops", "2.00", "120000.00", "120000.00", False)
    assert bills["CM/102"]["lines"][0]["capitalisable"] == "11800.00"                          # GST that is not claimed as credit is part of the cost
    assert bills["CM/104"]["lines"][0]["booked_to_asset_account"] is True
    assert [b["bill_no"] for b in client.get(url("/fixed-assets/capitalisable-bills?q=chair"), headers=o).json()["data"]] == ["CM/102"]


def test_an_asset_from_a_line_moves_the_cost_and_takes_the_supplier(client, db):
    o = login(client, OWNER)
    before = balance(db, "5000")
    r = client.post(url("/fixed-assets"), headers={**o, **key()}, json=asset_body(purchase_bill_id=S["b1"], purchase_bill_line_no=1))
    assert r.status_code == 201, r.text
    a = r.json()
    assert (a["purchase_bill_line_no"], a["party_id"], a["booking"]) == (1, VENDOR, "reclassify")
    assert balance(db, "5000") == before - D("60000")                                           # out of Purchases and expenses
    assert balance(db, "1500") == D("60000")                                                    # into Fixed assets
    left = {b["bill_no"]: b for b in client.get(url("/fixed-assets/capitalisable-bills"), headers=o).json()["data"]}["CM/101"]["lines"][0]
    assert (left["already_registered"], left["remaining"]) == ("60000.00", "60000.00")


def test_the_second_unit_fits_and_more_than_the_line_is_refused(client):
    o = login(client, OWNER)
    ok = client.post(url("/fixed-assets"), headers={**o, **key()}, json=asset_body(name="Dell laptop 2", purchase_bill_id=S["b1"], purchase_bill_line_no=1))
    assert ok.status_code == 201
    over = client.post(url("/fixed-assets"), headers={**o, **key()}, json=asset_body(name="Dell laptop 3", cost="1.00", purchase_bill_id=S["b1"], purchase_bill_line_no=1))
    assert over.status_code == 400 and "cost" in str(over.json()["errors"]) and "left" in str(over.json()["errors"]), over.text
    bills = {b["bill_no"] for b in client.get(url("/fixed-assets/capitalisable-bills"), headers=o).json()["data"]}
    assert "CM/101" in bills                                                                    # the installation line still has something left


def test_a_line_booked_to_an_expense_account_gives_the_cost_back_to_that_account(client, db):
    o = login(client, OWNER)
    before = balance(db, "5500")
    assert before == D("5000")
    r = client.post(url("/fixed-assets"), headers={**o, **key()}, json=asset_body(name="Cabling", category="electrical", cost="5000.00", purchase_bill_id=S["b1"], purchase_bill_line_no=2))
    assert r.status_code == 201, r.text
    assert balance(db, "5500") == D("0")


def test_unclaimed_gst_can_be_capitalised_with_the_line(client):
    o = login(client, OWNER)
    r = client.post(url("/fixed-assets"), headers={**o, **key()}, json=asset_body(name="Chairs", category="furniture", cost="11800.00", purchase_bill_id=S["b2"], purchase_bill_line_no=1))
    assert r.status_code == 201, r.text


def test_a_line_already_booked_to_an_asset_account_is_only_recorded(client, db):
    o = login(client, OWNER)
    bad = client.post(url("/fixed-assets"), headers={**o, **key()}, json=asset_body(name="Rack", cost="90000.00", purchase_bill_id=S["b4"], purchase_bill_line_no=1))
    assert bad.status_code == 400 and "booking" in str(bad.json()["errors"])
    fixed = balance(db, "1500")
    ok = client.post(url("/fixed-assets"), headers={**o, **key()}, json=asset_body(name="Rack", category="servers_networks", cost="90000.00", booking="already_booked", purchase_bill_id=S["b4"], purchase_bill_line_no=1))
    assert ok.status_code == 201 and balance(db, "1500") == fixed                               # no journal: it is already in the asset account


@pytest.mark.parametrize("change,status,field", [
    ({"purchase_bill_line_no": 1}, 409, None),                                                  # replaced below with the draft bill
    ({"purchase_bill_id": "b1"}, 400, "purchase_bill_line_no"),
    ({"purchase_bill_id": "b1", "purchase_bill_line_no": 9}, 400, "purchase_bill_line_no"),
    ({"purchase_bill_line_no": 1, "purchase_bill_id": None}, 400, "purchase_bill_id"),
])
def test_guards(client, change, status, field):
    o = login(client, OWNER)
    body = {**asset_body(), **{k: (S.get(v) if isinstance(v, str) and v in S else v) for k, v in change.items()}}
    if status == 409:
        body["purchase_bill_id"] = S["draft"]
    r = client.post(url("/fixed-assets"), headers={**o, **key()}, json=body)
    assert r.status_code == status, r.text
    if status == 409:
        assert r.json()["code"] == "bill_not_booked"
    else:
        assert field in str(r.json()["errors"])


def test_a_viewer_can_see_the_choices_but_not_register(client):
    v = login(client, VIEWER)
    assert client.get(url("/fixed-assets/capitalisable-bills"), headers=v).status_code == 200
    assert client.post(url("/fixed-assets"), headers={**v, **key()}, json=asset_body(purchase_bill_id=S["b2"], purchase_bill_line_no=1)).status_code == 403
