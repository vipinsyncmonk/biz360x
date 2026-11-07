"""Read endpoints for the billing screens: registrations, customers, the invoice list and the sales summary."""
import datetime as dt
import os
import uuid
from decimal import Decimal as D

import psycopg
import pytest
from psycopg.rows import dict_row

DB = os.environ["DATABASE_URL"]
ORG = "00000000-0000-0000-0000-0000000000a1"
EB, EB2 = "00000000-0000-0000-0000-000000000501", "00000000-0000-0000-0000-000000000502"
U_OWNER, U_VIEWER, U_OTHER = ("00000000-0000-0000-0000-00000000051%d" % i for i in (1, 2, 3))
REG, REG2 = "00000000-0000-0000-0000-000000000521", "00000000-0000-0000-0000-000000000522"
P_A, P_B, P_V, P_ARCH, P_X = ("00000000-0000-0000-0000-00000000053%d" % i for i in range(1, 6))
G_A, G_B = "00000000-0000-0000-0000-000000000541", "00000000-0000-0000-0000-000000000542"
OWNER, VIEWER, OTHER = "bl-owner@example.in", "bl-viewer@example.in", "bl-other@example.in"
S: dict = {}
TODAY = dt.date.today()


def fy_label(d):
    y = d.year if d.month >= 4 else d.year - 1
    return f"{y}-{(y + 1) % 100:02d}"


def line(qty="10", rate="1000.00"):
    return {"description": "Pressure transmitter", "hsn_sac": "902620", "qty": qty, "rate": rate, "gst_rate": 18}


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
    for e, n, reg, num in ((EB, "Billing Test Pvt Ltd", REG, "29AAACB5555B1Z5"), (EB2, "Billing Other Pvt Ltd", REG2, "29AAACB6666C1Z7")):
        db.execute("INSERT INTO entities (id, organization_id, legal_name, constitution, track) VALUES (%s, %s, %s, 'private_limited', 'business')", (e, ORG, n))
        db.execute("INSERT INTO entity_registrations (id, entity_id, kind, number, state_code, label, gst_filing_frequency) VALUES (%s, %s, 'gstin', %s, '29', 'Karnataka', 'monthly')", (reg, e, num))
    db.execute("INSERT INTO entity_registrations (entity_id, kind, number, state_code) VALUES (%s, 'tan', 'BLRB55555B', '29')", (EB,))
    for fy in {fy_label(TODAY), fy_label(TODAY - dt.timedelta(days=60))}:
        db.execute("INSERT INTO document_series (entity_id, registration_id, doc_type, fy, prefix) VALUES (%s, %s, 'tax_invoice', %s, 'B5/'), (%s, %s, 'credit_note', %s, 'B5/CN/')", (EB, REG, fy, EB, REG, fy))
    db.execute("INSERT INTO users (id, email, full_name) VALUES (%s, %s, 'BL Owner'), (%s, %s, 'BL Viewer'), (%s, %s, 'BL Other')", (U_OWNER, OWNER, U_VIEWER, VIEWER, U_OTHER, OTHER))
    db.execute("INSERT INTO memberships (entity_id, user_id, role) VALUES (%s, %s, 'owner'), (%s, %s, 'viewer'), (%s, %s, 'owner')", (EB, U_OWNER, EB, U_VIEWER, EB2, U_OTHER))
    db.execute("""INSERT INTO parties (id, entity_id, legal_name, display_name, is_customer, is_vendor, payment_terms_days, status) VALUES
                  (%s, %s, 'Alpha Foods 100%% Pvt Ltd', NULL, true, false, 30, 'active'), (%s, %s, 'Beta_Steel Works', 'Beta', true, false, 15, 'active'),
                  (%s, %s, 'Vendor Only Co', NULL, false, true, 30, 'active'), (%s, %s, 'Archived Customer', NULL, true, false, 30, 'archived'),
                  (%s, %s, 'Other Tenant Customer', NULL, true, false, 30, 'active')""", (P_A, EB, P_B, EB, P_V, EB, P_ARCH, EB, P_X, EB2))
    db.execute("INSERT INTO party_gst_registrations (id, party_id, gstin, state_code, is_default) VALUES (%s, %s, '29AABCA1111A1Z5', '29', true), (%s, %s, '32AABCB2222B1ZP', '32', true)", (G_A, P_A, G_B, P_B))
    yield
    db.execute("UPDATE jobs SET status = 'cancelled' WHERE status = 'queued' AND kind IN ('einvoice.generate_irn', 'compliance.run_engine', 'collections.dunning', 'collections.recurring')")


def login(client, email):
    r = client.post("/v1/auth/token", json={"grant_type": "otp", "email": email, "otp": "000000"})
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def key():
    return {"Idempotency-Key": str(uuid.uuid4())}


def url(path, e=EB):
    return f"/v1/entities/{e}{path}"


def make_invoice(client, h, party, gst, date, qty="10", issue=True):
    r = client.post(url("/sales-documents"), headers=h, json={"doc_type": "tax_invoice", "doc_date": date.isoformat(), "registration_id": REG, "party_id": party, "party_gst_id": gst, "lines": [line(qty)]})
    assert r.status_code == 201, r.text
    d = r.json()
    if issue:
        assert client.post(url(f"/sales-documents/{d['id']}/issue"), headers={**h, **key()}, json={}).status_code == 202
    return d


# ------------------------------------------------------------------ registrations and parties
def test_registrations_list_and_filter(client):
    h = login(client, OWNER)
    rows = client.get(url("/registrations"), headers=h).json()["data"]
    assert {(r["kind"], r["number"]) for r in rows} == {("gstin", "29AAACB5555B1Z5"), ("tan", "BLRB55555B")}
    only = client.get(url("/registrations?kind=gstin"), headers=h).json()["data"]
    assert [r["number"] for r in only] == ["29AAACB5555B1Z5"] and only[0]["label"] == "Karnataka"
    assert client.get(url("/registrations", EB2), headers=h).status_code == 404
    assert client.get(url("/registrations"), headers=login(client, VIEWER)).status_code == 200


def test_customers_with_their_gstins_and_filters(client):
    h = login(client, OWNER)
    all_ = client.get(url("/parties"), headers=h).json()["data"]
    assert {p["legal_name"] for p in all_} == {"Alpha Foods 100% Pvt Ltd", "Beta_Steel Works", "Vendor Only Co"}      # not archived, not another tenant's
    cust = client.get(url("/parties?role=customer"), headers=h).json()["data"]
    assert {p["id"] for p in cust} == {P_A, P_B}
    assert {p["legal_name"] for p in client.get(url("/parties?role=vendor"), headers=h).json()["data"]} == {"Vendor Only Co"}
    a = next(p for p in cust if p["id"] == P_A)
    assert a["gstins"] == [{"id": G_A, "gstin": "29AABCA1111A1Z5", "state_code": "29", "taxpayer_type": "regular", "is_default": True}]
    assert a["balance_receivable"] == "0.00" and a["payment_terms_days"] == 30
    assert client.get(url("/parties?role=nobody"), headers=h).status_code == 400
    assert client.get(url("/parties", EB2), headers=h).status_code == 404


def test_party_search_treats_wildcards_as_text(client):
    h = login(client, OWNER)
    names = lambda q: sorted(p["legal_name"] for p in client.get(url("/parties"), headers=h, params={"q": q}).json()["data"])
    assert names("alpha") == ["Alpha Foods 100% Pvt Ltd"]
    assert names("100%") == ["Alpha Foods 100% Pvt Ltd"]                  # a literal percent sign, not "match anything"
    assert names("beta_s") == ["Beta_Steel Works"] and names("betaxs") == []
    assert names("Beta") == ["Beta_Steel Works"]                          # display name also matches
    assert names("%") == ["Alpha Foods 100% Pvt Ltd"]


# ------------------------------------------------------------------ invoice list
def test_the_invoice_list_and_its_filters(client, db):
    h = login(client, OWNER)
    old = TODAY - dt.timedelta(days=60)
    inv_a = make_invoice(client, h, P_A, G_A, TODAY)
    inv_b = make_invoice(client, h, P_B, G_B, old, qty="5")
    draft = make_invoice(client, h, P_A, G_A, TODAY, qty="1", issue=False)
    S.update(a=inv_a, b=inv_b, draft=draft)
    page1 = client.get(url("/sales-documents"), headers=h).json()
    assert [d["id"] for d in page1["data"]] == [draft["id"], inv_a["id"], inv_b["id"]]                                  # newest document date first, then newest created
    row = next(d for d in page1["data"] if d["id"] == inv_a["id"])
    assert row["number"].startswith("B5/") and row["status"] == "issued" and row["grand_total"] == "11800.00" and row["balance_due"] == "11800.00"
    assert row["buyer_legal_name"] == "Alpha Foods 100% Pvt Ltd" and row["days_overdue"] == 0
    assert next(d for d in page1["data"] if d["id"] == draft["id"])["number"] is None
    ids = lambda **q: {d["id"] for d in client.get(url("/sales-documents"), headers=h, params=q).json()["data"]}
    assert ids(status="draft") == {draft["id"]} and ids(status="issued") == {inv_a["id"], inv_b["id"]}
    assert ids(party_id=P_B) == {inv_b["id"]} and ids(doc_type="credit_note") == set()
    assert ids(payment_status="unpaid") == {inv_a["id"], draft["id"], inv_b["id"]}
    assert ids(**{"from": TODAY.isoformat()}) == {inv_a["id"], draft["id"]} and ids(to=(TODAY - dt.timedelta(days=30)).isoformat()) == {inv_b["id"]}
    assert ids(q="beta") == {inv_b["id"]} and ids(q=row["number"]) == {inv_a["id"]}
    assert ids(q="100%") == {inv_a["id"], draft["id"]}


def test_overdue_is_issued_unpaid_and_past_due(client, db):
    h = login(client, OWNER)
    assert db.execute("SELECT due_date FROM sales_documents WHERE id = %s", (S["b"]["id"],)).fetchone()["due_date"] < TODAY             # 15-day terms, 60 days ago
    rows = client.get(url("/sales-documents"), headers=h, params={"overdue": "true"}).json()["data"]
    assert [d["id"] for d in rows] == [S["b"]["id"]] and rows[0]["days_overdue"] >= 40
    assert {d["id"] for d in client.get(url("/sales-documents"), headers=h, params={"overdue": "false"}).json()["data"]} == {S["a"]["id"], S["draft"]["id"]}


def test_the_list_pages_and_is_private_to_the_tenant(client):
    h = login(client, OWNER)
    p1 = client.get(url("/sales-documents?limit=2"), headers=h).json()
    assert len(p1["data"]) == 2 and p1["next_cursor"]
    p2 = client.get(url(f"/sales-documents?limit=2&cursor={p1['next_cursor']}"), headers=h).json()
    assert len(p2["data"]) == 1 and p2["next_cursor"] is None
    assert not ({d["id"] for d in p1["data"]} & {d["id"] for d in p2["data"]})
    ho = login(client, OTHER)
    assert client.get(url("/sales-documents"), headers=ho).status_code == 404
    assert client.get(url("/sales-documents", EB2), headers=ho).json()["data"] == []
    assert client.get(url("/sales-documents"), headers=login(client, VIEWER)).status_code == 200


def test_customer_balances_follow_the_invoices(client):
    h = login(client, OWNER)
    bal = {p["id"]: p["balance_receivable"] for p in client.get(url("/parties?role=customer"), headers=h).json()["data"]}
    assert bal == {P_A: "11800.00", P_B: "5900.00"}                      # drafts do not count


# ------------------------------------------------------------------ summary
def test_the_sales_summary(client, db):
    h = login(client, OWNER)
    r = client.post(url("/receipts"), headers={**h, **key()}, json={"party_id": P_A, "received_on": TODAY.isoformat(), "amount": "4000.00", "method": "upi",
                                                                       "allocations": [{"invoice_id": S["a"]["id"], "amount": "4000.00"}]})
    assert r.status_code == 201, r.text
    fy_start = dt.date(TODAY.year if TODAY.month >= 4 else TODAY.year - 1, 4, 1)
    invoiced = D("11800.00") + (D("5900.00") if TODAY - dt.timedelta(days=60) >= fy_start else D(0))
    s = client.get(url("/sales/summary"), headers=h).json()
    assert s == {"invoiced": f"{invoiced:.2f}", "collected": "4000.00", "outstanding": "13700.00", "overdue": "5900.00", "overdue_count": 1,
                 "tds_to_claim": "0.00", "collected_by_method": {"upi": "4000.00"}}
    tomorrow = (TODAY + dt.timedelta(days=1)).isoformat()
    narrow = client.get(url("/sales/summary"), headers=h, params={"from": tomorrow, "to": tomorrow}).json()
    assert narrow["invoiced"] == "0.00" and narrow["collected"] == "0.00" and narrow["outstanding"] == "13700.00"       # outstanding is as of today
    assert client.get(url("/sales/summary"), headers=h, params={"from": "2026-05-02", "to": "2026-05-01"}).status_code == 400
    assert client.get(url("/sales/summary", EB2), headers=h).status_code == 404


# ------------------------------------------------------------------ finance summary
def test_the_finance_summary(client):
    h = login(client, OWNER)
    fy_start = dt.date(TODAY.year if TODAY.month >= 4 else TODAY.year - 1, 4, 1)
    revenue = D("10000.00") + (D("5000.00") if TODAY - dt.timedelta(days=60) >= fy_start else D(0))
    s = client.get(url("/finance/summary"), headers=h).json()
    assert s["revenue"] == f"{revenue:.2f}" and s["gross_margin_pct"] == 100.0           # no purchases posted
    assert s["receivables"] == "13700.00" and s["receivables_overdue"] == "5900.00"
    assert s["cash_in_bank"] == "4000.00" and s["cash_change"] == "4000.00"              # the UPI receipt, banked
    assert s["net_burn"] is None and s["runway_months"] is None                           # cash grew, so there is no burn
    tomorrow = (TODAY + dt.timedelta(days=1)).isoformat()
    quiet = client.get(url("/finance/summary"), headers=h, params={"from": tomorrow, "to": tomorrow}).json()
    assert quiet["revenue"] == "0.00" and quiet["cash_change"] == "0.00" and quiet["gross_margin_pct"] == 0.0
    assert client.get(url("/finance/summary"), headers=h, params={"from": "2026-05-02", "to": "2026-05-01"}).status_code == 400
    assert client.get(url("/finance/summary", EB2), headers=h).status_code == 404
    assert client.get(url("/finance/summary"), headers=login(client, VIEWER)).status_code == 200
