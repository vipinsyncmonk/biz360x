"""Exports and imports: the realisation clock and the LUT (pure), then shipping bills, realisations, bills of entry and the summary through the API."""
import datetime as dt
import os
import uuid
from decimal import Decimal as D

import psycopg
import pytest
from psycopg.rows import dict_row

from app import trade

DB = os.environ["DATABASE_URL"]
ORG = "00000000-0000-0000-0000-000000000a00"
EN = "00000000-0000-0000-0000-000000000a01"
REG = "00000000-0000-0000-0000-000000000a02"
BUYER, SUPPLIER = "00000000-0000-0000-0000-000000000a03", "00000000-0000-0000-0000-000000000a04"
U_OWNER, U_VIEWER, U_OUT = ("00000000-0000-0000-0000-000000000a1%d" % i for i in range(3))
OWNER, VIEWER, OUTSIDER = ("tr-%s@example.in" % n for n in ("owner", "viewer", "outsider"))
TODAY = dt.date.today()
S: dict = {}


# ------------------------------------------------------------------ pure rules
def test_proceeds_are_due_nine_calendar_months_after_the_date_of_export():
    assert trade.realisation_due(dt.date(2026, 1, 31)) == dt.date(2026, 10, 31)
    assert trade.realisation_due(dt.date(2026, 5, 31)) == dt.date(2027, 2, 28)             # a short month clamps to its last day
    assert trade.realisation_due(dt.date(2026, 4, 1)) == dt.date(2027, 1, 1)
    assert trade.realisation_due(None) is None


def test_a_shipping_bill_is_awaiting_pending_overdue_or_realised():
    exp = dt.date(2026, 1, 15)
    st = lambda realised, on, today: trade.realisation_state(D("1000"), D(realised), on, today)      # noqa: E731
    assert st("0", None, dt.date(2026, 6, 1)) == {"state": "awaiting_export", "due_on": None, "days_left": None, "outstanding_foreign": D("1000")}
    pending = st("400", exp, dt.date(2026, 10, 14))
    assert pending["state"] == "pending" and pending["days_left"] == 1 and pending["outstanding_foreign"] == D("600") and pending["due_on"] == dt.date(2026, 10, 15)
    assert st("400", exp, dt.date(2026, 10, 15))["state"] == "pending"                          # the last day is still in time
    late = st("400", exp, dt.date(2026, 10, 16))
    assert late["state"] == "overdue" and late["days_left"] == -1
    assert st("1000", exp, dt.date(2027, 6, 1))["state"] == "realised" and st("999.995", exp, dt.date(2027, 6, 1))["state"] == "realised"      # a bank charge of a cent does not count
    assert st("1000", exp, dt.date(2027, 6, 1))["outstanding_foreign"] == 0
    assert st("1200", exp, dt.date(2026, 3, 1))["state"] == "realised"


def test_the_financial_year_starts_on_1_april_and_the_lut_has_four_states():
    assert trade.fy_start(dt.date(2026, 11, 8)) == dt.date(2026, 4, 1)
    assert trade.fy_start(dt.date(2027, 3, 31)) == dt.date(2026, 4, 1)
    assert trade.fy_start(dt.date(2027, 4, 1)) == dt.date(2027, 4, 1)
    today = dt.date(2026, 11, 8)
    assert trade.lut_state(None, today, False)["state"] == "none"
    assert trade.lut_state(dt.date(2027, 3, 31), today, True) == {"state": "valid", "valid_until": dt.date(2027, 3, 31), "days_left": 143}
    assert trade.lut_state(dt.date(2026, 12, 1), today, True)["state"] == "expiring"
    assert trade.lut_state(dt.date(2026, 11, 7), today, True)["state"] == "expired"
    assert trade.lut_state(None, today, True)["state"] == "valid"                                  # an open-ended LUT


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


def url(path, en=EN):
    return f"/v1/entities/{en}{path}"


@pytest.fixture(scope="module", autouse=True)
def seed(db):
    db.execute("INSERT INTO organizations (id, name, kind) VALUES (%s, 'Trade Test Org', 'business_group')", (ORG,))
    db.execute("INSERT INTO entities (id, organization_id, legal_name, constitution, cin, track) VALUES (%s, %s, 'Exim Forge Pvt Ltd', 'private_limited', 'U28900KA2019PTC110001', 'business')", (EN, ORG))
    db.execute("INSERT INTO entity_registrations (id, entity_id, kind, number, state_code, gst_filing_frequency) VALUES (%s, %s, 'gstin', '29AAACE0001A1Z5', '29', 'monthly')", (REG, EN))
    db.execute("INSERT INTO users (id, email, full_name) VALUES (%s, %s, 'Tr Owner'), (%s, %s, 'Tr Viewer'), (%s, %s, 'Tr Outsider')", (U_OWNER, OWNER, U_VIEWER, VIEWER, U_OUT, OUTSIDER))
    db.execute("INSERT INTO memberships (entity_id, user_id, role, accepted_at) VALUES (%s, %s, 'owner', now()), (%s, %s, 'viewer', now())", (EN, U_OWNER, EN, U_VIEWER))
    db.execute("INSERT INTO parties (id, entity_id, legal_name, is_customer, is_vendor, payment_terms_days, status) VALUES (%s, %s, 'Acme Imports LLC', true, false, 30, 'active'), (%s, %s, 'Shenzhen Parts Co', false, true, 30, 'active')",
               (BUYER, EN, SUPPLIER, EN))
    S["export_doc"] = db.execute("""INSERT INTO sales_documents (entity_id, doc_type, doc_date, registration_id, supplier_state, party_id, buyer_legal_name, bill_to, place_of_supply, supply_type, number, due_date,
                                                                taxable_total, grand_total, status, payment_status)
                                    VALUES (%s, 'export_invoice', %s, %s, '29', %s, 'Acme', '{}', '96', 'export_under_lut', 'EX/0001', %s, 10000, 10000, 'issued', 'unpaid') RETURNING id""",
                                 (EN, TODAY - dt.timedelta(days=40), REG, BUYER, TODAY - dt.timedelta(days=10))).fetchone()["id"]
    S["tax_doc"] = db.execute("""INSERT INTO sales_documents (entity_id, doc_type, doc_date, registration_id, supplier_state, party_id, buyer_legal_name, bill_to, place_of_supply, supply_type, number, due_date,
                                                              taxable_total, cgst_total, sgst_total, grand_total, status, payment_status)
                                 VALUES (%s, 'tax_invoice', %s, %s, '29', %s, 'Acme', '{}', '29', 'b2c_small', 'TX/0001', %s, 100, 9, 9, 118, 'issued', 'unpaid') RETURNING id""",
                              (EN, TODAY - dt.timedelta(days=40), REG, BUYER, TODAY)).fetchone()["id"]
    yield
    db.execute("DELETE FROM tasks WHERE entity_id = %s", (EN,))


def sb(**over):
    return {"sb_number": "7712345", "sb_date": str(TODAY - dt.timedelta(days=30)), "port_code": "INMAA1", "currency": "USD", "fob_foreign": "10000.00", "exchange_rate": "83.2150", **over}


def test_a_shipping_bill_values_itself_in_rupees_and_links_to_the_export_invoice(client, db):
    o, v = login(client, OWNER), login(client, VIEWER)
    r = client.post(url("/shipping-bills"), headers=o, json=sb(sales_document_id=str(S["export_doc"])))
    assert r.status_code == 201, r.text
    b = r.json()
    S["sb"] = b["id"]
    assert b["fob_inr"] == "832150.00" and b["exchange_rate"] == "83.2150" and b["status"] == "filed" and b["invoice_number"] == "EX/0001"
    assert b["buyer_name"] == "Acme Imports LLC" and b["buyer_party_id"] == BUYER                                        # the buyer comes from the invoice
    assert b["realisation_state"] == "awaiting_export" and b["realisation_due_on"] is None and b["outstanding_foreign"] == "10000.00" and b["realisations"] == []
    assert db.execute("SELECT count(*) AS n FROM tasks WHERE entity_id = %s", (EN,)).fetchone()["n"] == 0              # no date of export yet, no clock

    assert client.post(url("/shipping-bills"), headers=o, json=sb()).status_code == 409                                  # the same shipping bill twice
    assert client.post(url("/shipping-bills"), headers=v, json=sb(sb_number="1")).status_code == 403
    assert client.post(url("/shipping-bills"), headers=login(client, OUTSIDER), json=sb(sb_number="2")).status_code == 404
    for i, bad in enumerate([{"sb_date": str(TODAY + dt.timedelta(days=1))}, {"port_code": "maa1"}, {"currency": "usd"}, {"fob_foreign": "0"}, {"exchange_rate": "-1"}, {"sb_number": "bad number!"},
                {"exported_on": str(TODAY + dt.timedelta(days=2))}, {"exported_on": str(TODAY - dt.timedelta(days=60))}]):
        assert client.post(url("/shipping-bills"), headers=o, json={**sb(sb_number="99" + str(i)), **bad}).status_code in (400, 422), bad
    assert client.post(url("/shipping-bills"), headers=o, json=sb(sb_number="3", sales_document_id=str(S["tax_doc"]))).status_code == 400          # not an export invoice
    assert client.post(url("/shipping-bills"), headers=o, json=sb(sb_number="4", sales_document_id=str(uuid.uuid4()))).status_code == 400
    assert client.post(url("/shipping-bills"), headers=o, json=sb(sb_number="5", buyer_party_id=str(uuid.uuid4()))).status_code == 400


def test_the_date_of_export_starts_the_clock_and_cannot_be_moved(client, db):
    o = login(client, OWNER)
    exported = TODAY - dt.timedelta(days=20)
    p = client.patch(url(f"/shipping-bills/{S['sb']}"), headers=o, json={"status": "let_export", "exported_on": str(exported)})
    assert p.status_code == 200, p.text
    due = trade.realisation_due(exported)
    assert p.json()["realisation_state"] == "pending" and p.json()["realisation_due_on"] == str(due) and p.json()["days_left"] == (due - TODAY).days
    tasks = db.execute("SELECT title, due_on FROM tasks WHERE entity_id = %s", (EN,)).fetchall()
    assert len(tasks) == 1 and tasks[0]["due_on"] == due and "7712345" in tasks[0]["title"] and "USD 10000.00" in tasks[0]["title"]      # a task with the deadline
    assert client.patch(url(f"/shipping-bills/{S['sb']}"), headers=o, json={"exported_on": str(exported - dt.timedelta(days=1))}).status_code == 409
    assert client.patch(url(f"/shipping-bills/{S['sb']}"), headers=o, json={"exported_on": str(exported)}).status_code == 200      # restating the same date is fine
    assert client.patch(url(f"/shipping-bills/{S['sb']}"), headers=o, json={}).status_code == 400
    assert client.patch(url(f"/shipping-bills/{S['sb']}"), headers=o, json={"exported_on": str(TODAY + dt.timedelta(days=1))}).status_code == 400
    assert db.execute("SELECT count(*) AS n FROM tasks WHERE entity_id = %s", (EN,)).fetchone()["n"] == 1                  # still one task


def test_money_received_is_recorded_until_the_bill_is_realised(client):
    o, v = login(client, OWNER), login(client, VIEWER)
    path = url(f"/shipping-bills/{S['sb']}/realisations")
    assert client.post(path, headers=v, json={"received_on": str(TODAY), "amount_foreign": "100"}).status_code == 403
    part = client.post(path, headers=o, json={"received_on": str(TODAY - dt.timedelta(days=5)), "amount_foreign": "6000.00", "bank_ref": "UTR998", "firc_no": "FIRC/22"})
    assert part.status_code == 201, part.text
    j = part.json()
    assert j["realised_foreign"] == "6000.00" and j["outstanding_foreign"] == "4000.00" and j["outstanding_inr"] == "332860.00" and j["realisation_state"] == "pending"
    assert [(x["amount_foreign"], x["firc_no"]) for x in j["realisations"]] == [("6000.00", "FIRC/22")]
    assert client.post(path, headers=o, json={"received_on": str(TODAY), "amount_foreign": "4000.02"}).status_code == 409           # more than is owed (a cent of rounding is allowed)
    assert client.post(path, headers=o, json={"received_on": str(TODAY + dt.timedelta(days=1)), "amount_foreign": "1"}).status_code == 400
    assert client.post(path, headers=o, json={"received_on": str(TODAY), "amount_foreign": "0"}).status_code in (400, 422)
    done = client.post(path, headers=o, json={"received_on": str(TODAY), "amount_foreign": "4000.00"}).json()
    assert done["realisation_state"] == "realised" and done["outstanding_foreign"] == "0.00" and done["days_left"] is None and len(done["realisations"]) == 2
    assert client.post(url(f"/shipping-bills/{uuid.uuid4()}/realisations"), headers=o, json={"received_on": str(TODAY), "amount_foreign": "1"}).status_code == 404


def test_a_bill_past_nine_months_is_overdue_and_shows_what_is_still_owed(client):
    o = login(client, OWNER)
    old = client.post(url("/shipping-bills"), headers=o, json=sb(sb_number="8800001", sb_date=str(TODAY - dt.timedelta(days=320)), fob_foreign="5000.00", exchange_rate="80", exported_on=str(TODAY - dt.timedelta(days=300))))
    assert old.status_code == 201, old.text
    S["old"] = old.json()["id"]
    j = old.json()
    assert j["realisation_state"] == "overdue" and j["days_left"] < 0 and j["outstanding_inr"] == "400000.00" and j["fob_inr"] == "400000.00"
    adv = client.post(url("/shipping-bills"), headers=o, json=sb(sb_number="8800002", fob_foreign="2000.00"))                         # an advance can arrive before the goods leave
    assert client.post(url(f"/shipping-bills/{adv.json()['id']}/realisations"), headers=o, json={"received_on": str(TODAY), "amount_foreign": "500"}).json()["realisation_state"] == "awaiting_export"
    S["adv"] = adv.json()["id"]
    states = lambda s: [x["sb_number"] for x in client.get(url("/shipping-bills"), headers=o, params={"state": s}).json()["data"]]      # noqa: E731
    assert states("overdue") == ["8800001"] and states("realised") == ["7712345"] and states("awaiting_export") == ["8800002"] and states("pending") == []
    assert [x["sb_number"] for x in client.get(url("/shipping-bills"), headers=o).json()["data"]] == ["7712345", "8800002", "8800001"]     # newest first, then by number
    assert client.get(url("/shipping-bills"), headers=o, params={"state": "nonsense"}).status_code in (400, 422)
    assert client.get(url(f"/shipping-bills/{S['old']}"), headers=login(client, OUTSIDER)).status_code == 404
    assert client.get(url(f"/shipping-bills/{S['old']}"), headers=login(client, VIEWER)).status_code == 200


def test_rodtep_and_drawback_claims_are_tracked_and_cannot_be_over_credited(client):
    o = login(client, OWNER)
    p = url(f"/shipping-bills/{S['old']}")
    ok = client.patch(p, headers=o, json={"rodtep_claimed": "12000.50", "rodtep_credited": "4000.25", "drawback_claimed": "3000", "brc_ref": "BRC/2026/77"})
    assert ok.status_code == 200 and ok.json()["rodtep_claimed"] == "12000.50" and ok.json()["rodtep_credited"] == "4000.25" and ok.json()["brc_ref"] == "BRC/2026/77"
    assert client.patch(p, headers=o, json={"drawback_received": "3000.01"}).status_code == 400                                       # more received than claimed
    assert client.patch(p, headers=o, json={"rodtep_claimed": "-1"}).status_code in (400, 422)


def test_bills_of_entry_record_duty_and_the_igst_credit(client, db):
    o, v = login(client, OWNER), login(client, VIEWER)
    body = {"boe_number": "5500123", "boe_date": str(TODAY - dt.timedelta(days=10)), "port_code": "INBLR4", "currency": "CNY", "cif_foreign": "100000.00", "exchange_rate": "11.5000",
            "supplier_party_id": SUPPLIER, "bcd": "57500.00", "sws": "5750.00", "igst": "216000.00"}
    r = client.post(url("/bills-of-entry"), headers=o, json=body)
    assert r.status_code == 201, r.text
    b = r.json()
    S["boe"] = b["id"]
    assert b["cif_inr"] == "1150000.00" and b["duty_total"] == "279250.00" and b["supplier_name"] == "Shenzhen Parts Co" and b["status"] == "filed" and b["igst_credit"] == "0.00"   # no credit until the duty is paid
    assert client.post(url("/bills-of-entry"), headers=o, json=body).status_code == 409
    assert client.post(url("/bills-of-entry"), headers=v, json={**body, "boe_number": "1"}).status_code == 403
    assert client.post(url("/bills-of-entry"), headers=o, json={**body, "boe_number": "2", "status": "duty_paid"}).status_code == 400            # paid needs a date
    assert client.post(url("/bills-of-entry"), headers=o, json={**body, "boe_number": "3", "boe_date": str(TODAY + dt.timedelta(days=1))}).status_code == 400
    assert client.post(url("/bills-of-entry"), headers=o, json={**body, "boe_number": "4", "supplier_party_id": str(uuid.uuid4())}).status_code == 400
    assert client.post(url("/bills-of-entry"), headers=o, json={**body, "boe_number": "6", "purchase_bill_id": str(uuid.uuid4())}).status_code == 400
    assert client.patch(url(f"/bills-of-entry/{S['boe']}"), headers=o, json={"status": "duty_paid"}).status_code == 400
    paid = client.patch(url(f"/bills-of-entry/{S['boe']}"), headers=o, json={"status": "duty_paid", "duty_paid_on": str(TODAY - dt.timedelta(days=8))})
    assert paid.status_code == 200 and paid.json()["igst_credit"] == "216000.00" and paid.json()["status"] == "duty_paid"
    assert client.patch(url(f"/bills-of-entry/{S['boe']}"), headers=o, json={"igst": "200000"}).json()["igst_credit"] == "200000.00"      # the credit follows the corrected duty
    assert client.patch(url(f"/bills-of-entry/{uuid.uuid4()}"), headers=o, json={"status": "assessed"}).status_code == 404
    assert [x["boe_number"] for x in client.get(url("/bills-of-entry"), headers=o).json()["data"]] == ["5500123"]
    assert client.get(url("/bills-of-entry"), headers=login(client, OUTSIDER)).status_code == 404


def test_the_summary_adds_it_up_and_reads_the_lut(client, db):
    o = login(client, OWNER)
    s = client.get(url("/trade/summary"), headers=o).json()
    assert s["lut"] == {"state": "none", "valid_until": None, "days_left": None} and s["iec"] is None
    assert s["financial_year_from"] == str(trade.fy_start(TODAY))
    assert s["realisation"]["overdue_bills"] == 1 and s["realisation"]["overdue_inr"] == "400000.00" and s["realisation"]["awaiting_export"] == 1
    assert s["realisation"]["outstanding_bills"] == 1                                                          # pending or overdue; the awaiting one is counted apart
    assert s["claims"] == {"rodtep_pending": "8000.25", "drawback_pending": "3000.00"}
    assert s["imports"]["bills"] == 1 and s["imports"]["duty_inr"] == "263250.00" and s["imports"]["igst_credit_inr"] == "200000.00"
    assert s["exports"]["bills"] >= 2 and D(s["exports"]["value_inr"]) >= D("832150.00")
    db.execute("INSERT INTO entity_registrations (entity_id, kind, number, valid_from, valid_until) VALUES (%s, 'lut', 'AD2904260000001', %s, %s), (%s, 'iec', '0512345678', NULL, NULL)",
               (EN, TODAY - dt.timedelta(days=100), TODAY + dt.timedelta(days=10), EN))
    s2 = client.get(url("/trade/summary"), headers=o).json()
    assert s2["lut"]["state"] == "expiring" and s2["lut"]["days_left"] == 10 and s2["iec"] == "0512345678"
    assert client.get(url("/trade/summary"), headers=login(client, OUTSIDER)).status_code == 404
