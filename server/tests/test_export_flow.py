"""The export flow: a customer outside India turns an invoice into an export invoice (currency and rate, LUT or IGST), issuing it opens a draft
shipping bill, and the customs details turn the draft into a filed one. Pure checks first, then the API."""
import datetime as dt
import os
import uuid
from decimal import Decimal as D

import psycopg
import pytest
from psycopg.rows import dict_row

from app import tax

DB = os.environ["DATABASE_URL"]
ORG = "00000000-0000-0000-0000-000000f1a000"
EN = "00000000-0000-0000-0000-000000f1a001"
REG = "00000000-0000-0000-0000-000000f1a002"
U_OWNER, U_VIEWER = "00000000-0000-0000-0000-000000f1a011", "00000000-0000-0000-0000-000000f1a012"
OWNER, VIEWER = "xf-owner@example.in", "xf-viewer@example.in"
TODAY = dt.date.today()
S: dict = {}


# ------------------------------------------------------------------ pure rules
def ctx(**over):
    base = dict(doc_type="export_invoice", supplier_state="29", place_of_supply="96", doc_date=TODAY, buyer_gstin=None, aato=None, deducts_194q=False, payment_terms_days=30,
                is_export=True, zero_rated=True, lut="valid", currency="USD", exchange_rate=D("83.0000"), country="US", supply_type="export_under_lut")
    return tax.Context(**{**base, **over})


GOODS = [tax.Line("Valve", "8481", D("10"), D("1000"), D("18"))]
SERVICE = [tax.Line("Design work", "998314", D("1"), D("100000"), D("18"), is_service=True)]


def codes(res):
    return {c["code"]: c["level"] for c in res.checks}


def test_a_supply_under_lut_charges_no_tax_and_one_with_payment_charges_igst():
    under = tax.compute(GOODS, ctx())
    assert under.igst_total == 0 and under.grand_total == D("10000") and under.is_inter_state
    assert codes(under)["export_under_lut"] == "ok" and codes(under)["shipping_bill_follows"] == "info"
    with_tax = tax.compute(GOODS, ctx(zero_rated=False, lut="none", supply_type="export_with_tax"))
    assert with_tax.igst_total == D("1800.00") and with_tax.grand_total == D("11800") and codes(with_tax)["export_no_lut"] == "warn"


def test_an_export_needs_a_currency_and_a_rate_and_a_claimed_lut_must_exist():
    assert codes(tax.compute(GOODS, ctx(currency="INR", exchange_rate=None)))["export_needs_rate"] == "block"
    assert codes(tax.compute(GOODS, ctx(zero_rated=False, lut="expired")))["lut_not_valid"] == "block"
    assert tax.foreign_amount(D("83000.00"), D("83.0000")) == D("1000.00")
    services = codes(tax.compute(SERVICE, ctx()))
    assert services["export_services"] == "info" and "shipping_bill_follows" not in services
    assert codes(tax.compute(SERVICE, ctx(lut="expiring")))["lut_expiring"] == "warn"


def test_an_export_is_einvoiced_without_a_buyer_gstin_above_the_turnover_limit():
    assert codes(tax.compute(GOODS, ctx(aato=D("60000000"))))["einvoice_required"] == "ok"
    assert "einvoice_required" not in codes(tax.compute(GOODS, ctx(aato=D("1000000"))))


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
    db.execute("INSERT INTO organizations (id, name, kind) VALUES (%s, 'Export Flow Org', 'business_group')", (ORG,))
    db.execute("INSERT INTO entities (id, organization_id, legal_name, constitution, track) VALUES (%s, %s, 'Exim Flow Pvt Ltd', 'private_limited', 'business')", (EN, ORG))
    db.execute("INSERT INTO entity_registrations (id, entity_id, kind, number, state_code, label, gst_filing_frequency) VALUES (%s, %s, 'gstin', '29AAACX7001A1Z5', '29', 'Karnataka', 'monthly')", (REG, EN))
    for fy in {tax_fy(TODAY)}:
        db.execute("INSERT INTO document_series (entity_id, registration_id, doc_type, fy, prefix) VALUES (%s, %s, 'tax_invoice', %s, 'XF/')", (EN, REG, fy))
    db.execute("INSERT INTO users (id, email, full_name) VALUES (%s, %s, 'Xf Owner'), (%s, %s, 'Xf Viewer')", (U_OWNER, OWNER, U_VIEWER, VIEWER))
    db.execute("INSERT INTO memberships (entity_id, user_id, role, accepted_at) VALUES (%s, %s, 'owner', now()), (%s, %s, 'viewer', now())", (EN, U_OWNER, EN, U_VIEWER))
    yield
    db.execute("UPDATE jobs SET status = 'cancelled' WHERE status = 'queued' AND kind IN ('einvoice.generate_irn', 'compliance.run_engine')")
    db.execute("DELETE FROM tasks WHERE entity_id = %s", (EN,))


def tax_fy(d: dt.date) -> str:
    y = d.year if d.month >= 4 else d.year - 1
    return f"{y}-{str(y + 1)[2:]}"


def doc(party, lines, **over):
    return {"doc_type": "tax_invoice", "doc_date": str(TODAY), "registration_id": REG, "party_id": party, "lines": lines, **over}


GOODS_LINE = {"description": "Valve", "hsn_sac": "8481", "qty": "10", "rate": "8300", "gst_rate": "18"}          # ₹83,000 = USD 1,000 at 83
SERVICE_LINE = {"description": "Design work", "hsn_sac": "998314", "qty": "1", "rate": "83000", "gst_rate": "18"}


def test_a_party_outside_india_needs_a_currency_and_no_gstin(client):
    o, v = login(client, OWNER), login(client, VIEWER)
    assert client.post(url("/parties"), headers=v, json={"legal_name": "Acme Inc", "country": "US", "currency": "USD"}).status_code == 403
    r = client.post(url("/parties"), headers=o, json={"legal_name": "Acme Inc", "country": "US"})
    assert r.status_code == 400 and r.json()["errors"][0]["field"] == "currency"
    assert client.post(url("/parties"), headers=o, json={"legal_name": "Acme Inc", "country": "US", "currency": "USD", "gstin": "29AABCA1111A1Z5"}).status_code == 400
    assert client.post(url("/parties"), headers=o, json={"legal_name": "Local Co", "currency": "USD"}).status_code == 400
    r = client.post(url("/parties"), headers=o, json={"legal_name": "Acme Inc", "country": "US", "currency": "USD"})
    assert r.status_code == 201, r.text
    S["abroad"] = r.json()["id"]
    assert r.json()["country"] == "US" and r.json()["gstins"][0]["taxpayer_type"] == "overseas" and r.json()["gstins"][0]["state_code"] == "96"
    assert client.post(url("/parties"), headers=o, json={"legal_name": "acme inc", "country": "US", "currency": "USD"}).status_code == 409        # the same name twice
    r = client.post(url("/parties"), headers=o, json={"legal_name": "Local Buyer Pvt Ltd", "gstin": "29AABCL2222B1Z5"})
    assert r.status_code == 201 and r.json()["country"] == "IN" and r.json()["pan"] == "AABCL2222B" and r.json()["gstins"][0]["state_code"] == "29"
    S["local"], S["local_gst"] = r.json()["id"], r.json()["gstins"][0]["id"]
    listed = {p["legal_name"]: p for p in client.get(url("/parties?role=customer"), headers=o).json()["data"]}
    assert listed["Acme Inc"]["country"] == "US" and listed["Acme Inc"]["currency"] == "USD"


def test_the_lut_is_recorded_by_the_owner_with_its_dates(client):
    o, v = login(client, OWNER), login(client, VIEWER)
    lut = {"kind": "lut", "number": "AD2904260000999", "valid_from": f"{TODAY.year - 1}-04-01", "valid_until": f"{TODAY.year + 1}-03-31"}
    assert client.post(url("/registrations"), headers=v, json=lut).status_code == 403
    assert client.post(url("/registrations"), headers=o, json={**lut, "valid_until": None}).status_code == 400                  # a LUT is valid for a period
    assert client.post(url("/registrations"), headers=o, json={"kind": "iec", "number": "SHORT123"}).status_code in (400, 422)
    S["lut"] = lut


def test_an_invoice_to_a_foreign_customer_becomes_an_export_invoice_with_igst_when_there_is_no_lut(client):
    o = login(client, OWNER)
    r = client.post(url("/sales-documents/preview"), headers=o, json=doc(S["abroad"], [GOODS_LINE], currency="USD", exchange_rate="83"))
    assert r.status_code == 200, r.text
    p = r.json()
    assert p["doc_type"] == "export_invoice" and p["is_export"] and p["supply_type"] == "export_with_tax" and p["place_of_supply"] == "96"
    assert p["igst_total"] == "14940.00" and p["cgst_total"] == "0.00" and p["grand_total"] == "97940.00"              # 18% IGST on ₹83,000
    assert p["foreign_total"] == "1180.00" and p["currency"] == "USD"
    assert {c["code"] for c in p["checks"]} >= {"export_detected", "export_no_lut", "shipping_bill_follows", "export_foreign_value"}


def test_a_domestic_customer_is_unchanged(client):
    o = login(client, OWNER)
    p = client.post(url("/sales-documents/preview"), headers=o, json=doc(S["local"], [GOODS_LINE], party_gst_id=S["local_gst"])).json()
    assert p["doc_type"] == "tax_invoice" and not p["is_export"] and p["currency"] == "INR" and p["foreign_total"] is None


def test_a_valid_lut_makes_the_supply_tax_free_and_a_missing_rate_blocks_it(client):
    o = login(client, OWNER)
    assert client.post(url("/registrations"), headers=o, json=S["lut"]).status_code == 201
    assert client.post(url("/registrations"), headers=o, json=S["lut"]).status_code == 409
    p = client.post(url("/sales-documents/preview"), headers=o, json=doc(S["abroad"], [GOODS_LINE], currency="USD", exchange_rate="83")).json()
    assert p["supply_type"] == "export_under_lut" and p["igst_total"] == "0.00" and p["grand_total"] == "83000.00" and p["foreign_total"] == "1000.00"
    assert {c["code"]: c["level"] for c in p["checks"]}["export_under_lut"] == "ok"
    no_rate = client.post(url("/sales-documents/preview"), headers=o, json=doc(S["abroad"], [GOODS_LINE])).json()           # the customer's own currency, but no rate
    assert {c["code"]: c["level"] for c in no_rate["checks"]}["export_needs_rate"] == "block"
    r = client.post(url("/sales-documents"), headers=o, json=doc(S["abroad"], [GOODS_LINE], currency="USD"))
    assert r.status_code == 400 and r.json()["code"] == "export_needs_rate"
    r = client.post(url("/sales-documents/preview"), headers=o, json=doc(S["abroad"], [GOODS_LINE], currency="USD", exchange_rate="83", supply_type="export_with_tax"))
    assert r.json()["igst_total"] == "14940.00"                                                                   # the exporter may choose to pay IGST even with a LUT


def test_issuing_an_export_invoice_with_goods_opens_a_draft_shipping_bill(client, db):
    o = login(client, OWNER)
    r = client.post(url("/sales-documents"), headers=o, json=doc(S["abroad"], [GOODS_LINE], currency="USD", exchange_rate="83"))
    assert r.status_code == 201, r.text
    d = r.json()
    assert d["doc_type"] == "export_invoice" and d["currency"] == "USD" and d["exchange_rate"] == "83.0000" and d["foreign_total"] == "1000.00" and d["place_of_supply"] == "96"
    assert db.execute("SELECT count(*) AS n FROM shipping_bills WHERE entity_id = %s", (EN,)).fetchone()["n"] == 0            # a draft invoice opens nothing yet
    assert client.post(url(f"/sales-documents/{d['id']}/issue"), headers={**o, **key()}, json={}).status_code == 202
    issued = client.get(url(f"/sales-documents/{d['id']}"), headers=o).json()
    assert issued["status"] == "issued" and issued["number"].startswith("EXP/")
    bills = client.get(url("/shipping-bills"), headers=o).json()["data"]
    assert len(bills) == 1
    b = bills[0]
    assert b["status"] == "draft" and b["sb_number"] is None and b["sales_document_id"] == d["id"] and b["invoice_number"] == issued["number"]
    assert b["fob_foreign"] == "1000.00" and b["currency"] == "USD" and b["exchange_rate"] == "83.0000" and b["fob_inr"] == "83000.00" and b["realisation_state"] == "awaiting_export"
    assert db.execute("SELECT count(*) AS n FROM tasks WHERE entity_id = %s AND title LIKE %s", (EN, "Add the shipping bill details%")).fetchone()["n"] == 1
    assert db.execute("SELECT supply_type::text AS s FROM sales_documents WHERE id = %s", (d["id"],)).fetchone()["s"] == "export_under_lut"          # GSTR-1 reads this
    S["sb"], S["inv"] = b["id"], d["id"]


def test_the_customs_details_turn_the_draft_into_a_filed_shipping_bill(client):
    o, v = login(client, OWNER), login(client, VIEWER)
    assert client.patch(url(f"/shipping-bills/{S['sb']}"), headers=v, json={"sb_number": "7712345"}).status_code == 403
    r = client.patch(url(f"/shipping-bills/{S['sb']}"), headers=o, json={"status": "let_export"})
    assert r.status_code == 400                                                                                   # not before customs has issued its details
    r = client.patch(url(f"/shipping-bills/{S['sb']}"), headers=o, json={"exported_on": str(TODAY)})
    assert r.status_code == 400
    r = client.patch(url(f"/shipping-bills/{S['sb']}"), headers=o, json={"sb_number": "7712345", "sb_date": str(TODAY + dt.timedelta(days=2)), "port_code": "INMAA1"})
    assert r.status_code == 400                                                                                   # not dated in the future
    r = client.patch(url(f"/shipping-bills/{S['sb']}"), headers=o, json={"sb_number": "7712345", "port_code": "INMAA1"})
    assert r.status_code == 200 and r.json()["status"] == "draft"                                                 # still missing the date
    r = client.patch(url(f"/shipping-bills/{S['sb']}"), headers=o, json={"sb_date": str(TODAY), "exchange_rate": "83.4000"})
    b = r.json()
    assert r.status_code == 200 and b["status"] == "filed" and b["exchange_rate"] == "83.4000" and b["fob_inr"] == "83400.00"       # the customs rate replaces the invoice's
    r = client.patch(url(f"/shipping-bills/{S['sb']}"), headers=o, json={"sb_number": "9999999"})
    assert r.status_code == 409                                                                                   # fixed once filed
    r = client.patch(url(f"/shipping-bills/{S['sb']}"), headers=o, json={"status": "let_export", "exported_on": str(TODAY)})
    assert r.status_code == 200 and r.json()["realisation_state"] == "pending" and r.json()["realisation_due_on"] is not None      # the nine-month clock starts


def test_recording_a_shipping_bill_against_the_invoice_fills_its_draft_instead_of_adding_another(client, db):
    o = login(client, OWNER)
    d = client.post(url("/sales-documents"), headers=o, json=doc(S["abroad"], [GOODS_LINE], currency="USD", exchange_rate="83")).json()
    assert client.post(url(f"/sales-documents/{d['id']}/issue"), headers={**o, **key()}, json={}).status_code == 202
    before = len(client.get(url("/shipping-bills"), headers=o).json()["data"])
    body = {"sb_number": "7700001", "sb_date": str(TODAY), "port_code": "INBLR4", "currency": "USD", "fob_foreign": "1000.00", "exchange_rate": "83.1000", "sales_document_id": d["id"]}
    r = client.post(url("/shipping-bills"), headers=o, json=body)
    assert r.status_code == 201 and r.json()["status"] == "filed" and r.json()["invoice_number"].startswith("EXP/")
    assert len(client.get(url("/shipping-bills"), headers=o).json()["data"]) == before                           # filled in, not duplicated
    s = client.get(url("/trade/summary"), headers=o).json()
    assert s["exports"]["drafts"] == 0 and s["lut"]["state"] == "valid"


def test_an_export_of_services_needs_no_shipping_bill_and_notes_for_exports_are_refused(client, db):
    o = login(client, OWNER)
    before = len(client.get(url("/shipping-bills"), headers=o).json()["data"])
    d = client.post(url("/sales-documents"), headers=o, json=doc(S["abroad"], [SERVICE_LINE], currency="USD", exchange_rate="83")).json()
    assert d["doc_type"] == "export_invoice" and d["grand_total"] == "83000.00"
    assert client.post(url(f"/sales-documents/{d['id']}/issue"), headers={**o, **key()}, json={}).status_code == 202
    assert len(client.get(url("/shipping-bills"), headers=o).json()["data"]) == before
    r = client.post(url("/sales-documents"), headers=o, json={**doc(S["abroad"], [GOODS_LINE]), "doc_type": "credit_note", "original_document_id": S["inv"], "cn_reason": "sales_return"})
    assert r.status_code == 400 and r.json()["code"] == "export_notes_unsupported"


def test_the_audit_chain_is_intact(db):
    assert db.execute("SELECT app.audit_verify() AS v").fetchone()["v"] is None
