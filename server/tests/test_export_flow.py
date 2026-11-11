"""The export flow: a customer outside India turns an invoice into an export invoice (currency and rate, LUT or IGST), issuing it opens a draft
shipping bill, and the customs details turn the draft into a filed one. Pure checks first, then the API."""
import datetime as dt
import os
import uuid
from decimal import Decimal as D

import psycopg
import pytest
from psycopg.rows import dict_row

from app import ledger, tax, trade, worker

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


def test_a_note_on_an_export_invoice_follows_the_invoice_and_needs_no_buyer_gstin():
    credit = tax.compute(GOODS, ctx(doc_type="credit_note", original_doc_date=dt.date(2020, 4, 1)))                  # far past the Sec 34 limit, but no GST was charged to reduce
    assert credit.igst_total == 0 and credit.grand_total == D("10000")
    c = codes(credit)
    assert c["export_note"] == "ok" and c["export_foreign_value"] == "info"
    assert not {"b2b_needs_gstin", "sec34_time_limit", "shipping_bill_follows", "export_under_lut"} & set(c)
    taxed = tax.compute(GOODS, ctx(doc_type="debit_note", zero_rated=False, lut="none", supply_type="export_with_tax"))
    assert taxed.igst_total == D("1800.00") and codes(taxed)["export_note"] == "ok" and "export_no_lut" not in codes(taxed)
    late = codes(tax.compute(GOODS, ctx(doc_type="credit_note", zero_rated=False, lut="none", supply_type="export_with_tax", original_doc_date=dt.date(2020, 4, 1))))
    assert late["sec34_time_limit"] == "block"                                                                        # IGST was charged, so the time limit still applies


def test_proceeds_from_abroad_settle_the_invoice_at_its_own_rate_and_the_rest_is_forex():
    P = "party"
    gain = ledger.export_proceeds_lines(received=D("83500.00"), charges=D("0"), booked=D("83000.00"), party_id=P)
    assert ledger.balanced(gain) and [(l.key, l.debit, l.credit) for l in gain] == [("bank", D("83500.00"), D(0)), ("receivable", D(0), D("83000.00")), ("forex_gain_loss", D(0), D("500.00"))]
    loss = ledger.export_proceeds_lines(received=D("82600.00"), charges=D("0"), booked=D("83000.00"), party_id=P)
    assert ledger.balanced(loss) and loss[-1].key == "forex_gain_loss" and loss[-1].debit == D("400.00")
    charged = ledger.export_proceeds_lines(received=D("82900.00"), charges=D("100.00"), booked=D("83000.00"), party_id=P, bank_account_key="account:x")
    assert ledger.balanced(charged) and [l.key for l in charged] == ["account:x", "bank_charges", "receivable"]          # the charges account for the gap: no forex
    assert ledger.balanced(ledger.export_proceeds_lines(received=D("83000.00"), charges=D("0"), booked=D("83000.00"), party_id=P))


def test_a_bank_credit_is_ranked_against_shipping_bills_without_needing_an_exact_amount():
    cand = {"sb_id": "sb1", "sb_number": "7790001", "number": "EXP/2026-27/0001", "legal_name": "Acme Inc", "currency": "USD", "rate": D("83.0000"), "remaining": D("500.00"), "balance_due": D("41500.00")}
    got = trade.rank_proceeds(D("41150.00"), "INWARD REMITTANCE ACME INC USD 500.00 SWIFT", "ACME INC", [cand])             # the bank took ₹350 and converted at its own rate
    assert [(g["shipping_bill_id"], g["confidence"], g["proposed_foreign"]) for g in got] == [("sb1", D("0.95"), D("500.00"))]
    assert trade.rank_proceeds(D("41150.00"), "NEFT SALARY REFUND", None, [cand]) == []                                      # nothing says it is from abroad
    assert trade.rank_proceeds(D("1000.00"), "Acme Inc", None, [cand]) == []                                                # a name alone is not enough
    far = trade.rank_proceeds(D("20000.00"), "IRM FCY ACME INC", None, [cand])                                               # about half the invoice: a part payment, the dollars are guessed
    assert far and far[0]["confidence"] == D("0.60") and far[0]["proposed_foreign"] == D("240.96")
    by_number = trade.rank_proceeds(D("5000.00"), "TT EXP/2026-27/0001", None, [cand])
    assert by_number and by_number[0]["confidence"] == D("0.75") and by_number[0]["proposed_foreign"] == D("60.24")
    assert trade.rank_proceeds(D("41150.00"), "INWARD REMITTANCE ACME INC USD 900.00", None, [cand])[0]["proposed_foreign"] == D("500.00")      # more than is owed: ignored
    assert trade.rank_proceeds(D("41150.00"), "INWARD REMITTANCE ACME INC", None, [{**cand, "remaining": D("0.00")}]) == []


def test_the_e_invoice_carries_the_export_details_only_for_an_export():
    doc = {"supply_type": "export_under_lut", "currency": "USD", "country": "US"}
    assert trade.irp_export_details({"supply_type": "b2b"}, []) is None
    d = trade.irp_export_details(doc, [])
    assert d == {"supply_type": "EXPWOP", "buyer_gstin": "URP", "place_of_supply": "96", "foreign_currency": "USD", "buyer_country": "US"}
    with_sb = trade.irp_export_details({**doc, "supply_type": "export_with_tax"}, [{"sb_number": "7712345", "sb_date": dt.date(2026, 11, 3), "port_code": "INMAA1"}])
    assert with_sb["supply_type"] == "EXPWP" and with_sb["shipping_bill_no"] == "7712345" and with_sb["shipping_bill_date"] == "2026-11-03" and with_sb["port_code"] == "INMAA1"
    assert "shipping_bill_no" not in trade.irp_export_details(doc, [{"sb_number": None, "sb_date": None, "port_code": None}])


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
        db.execute("INSERT INTO document_series (entity_id, registration_id, doc_type, fy, prefix) VALUES (%s, %s, 'credit_note', %s, 'XC/')", (EN, REG, fy))
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


def lut_duty(db):
    return db.execute("""SELECT o.status::text AS status, o.period_start, o.due_date, o.acknowledgement, o.reason_snapshot FROM obligations o
                           JOIN compliance_rules r ON r.id = o.rule_id WHERE o.entity_id = %s AND r.code = 'GST.LUT.ANNUAL' ORDER BY o.period_start""", (EN,)).fetchall()


def test_an_exporter_with_no_lut_is_told_at_the_start_of_the_year_and_recording_one_closes_it(client, db):
    o = login(client, OWNER)
    assert client.post(url("/compliance/engine-runs"), headers=o).status_code == 201                                  # the customer abroad makes the company an exporter
    duty, *later = lut_duty(db)
    start = TODAY.replace(month=4, day=1) if TODAY.month >= 4 else TODAY.replace(year=TODAY.year - 1, month=4, day=1)
    assert duty["period_start"] == start and duty["due_date"] == start                                               # due as the year starts, however long ago that was
    assert duty["status"] == ("overdue" if TODAY > start else "upcoming")
    assert {c["fact"]: c["result"] for c in duty["reason_snapshot"]["applicability"]} == {"is_exporter": True}
    assert client.post(url("/registrations"), headers=o, json=S["lut"]).status_code == 201                            # recording a LUT that covers the year closes the duty
    duty, *later = lut_duty(db)
    assert duty["status"] == "filed" and duty["acknowledgement"] == S["lut"]["number"]
    for nxt in later:                                                                                                  # the next year's LUT shows up ahead of 1 April, and this LUT does not cover it
        assert nxt["period_start"] > start and nxt["status"] == "upcoming"


def test_a_valid_lut_makes_the_supply_tax_free_and_a_missing_rate_blocks_it(client):
    o = login(client, OWNER)
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


def test_an_export_of_services_needs_no_shipping_bill(client, db):
    o = login(client, OWNER)
    before = len(client.get(url("/shipping-bills"), headers=o).json()["data"])
    d = client.post(url("/sales-documents"), headers=o, json=doc(S["abroad"], [SERVICE_LINE], currency="USD", exchange_rate="83")).json()
    assert d["doc_type"] == "export_invoice" and d["grand_total"] == "83000.00"
    assert client.post(url(f"/sales-documents/{d['id']}/issue"), headers={**o, **key()}, json={}).status_code == 202
    assert len(client.get(url("/shipping-bills"), headers=o).json()["data"]) == before


CN_LINE = {"description": "Valve", "hsn_sac": "8481", "qty": "2", "rate": "8300", "gst_rate": "18"}


def test_a_credit_note_on_an_export_invoice_under_lut_credits_in_the_invoice_currency_with_no_tax(client, db):
    o = login(client, OWNER)
    bills = len(client.get(url("/shipping-bills"), headers=o).json()["data"])
    r = client.post(url(f"/sales-documents/{S['inv']}/credit-notes"), headers=o, json={"cn_reason": "sales_return", "lines": [CN_LINE]})
    assert r.status_code == 201, r.text
    cn = r.json()
    assert cn["doc_type"] == "credit_note" and cn["supply_type"] == "export_under_lut" and cn["place_of_supply"] == "96"
    assert cn["currency"] == "USD" and cn["exchange_rate"] == "83.0000" and cn["igst_total"] == "0.00" and cn["grand_total"] == "16600.00" and cn["foreign_total"] == "200.00"
    assert client.post(url(f"/sales-documents/{cn['id']}/issue"), headers={**o, **key()}, json={}).status_code == 202
    issued = client.get(url(f"/sales-documents/{cn['id']}"), headers=o).json()
    assert issued["status"] == "issued" and issued["number"].startswith("XC/")
    assert client.get(url(f"/sales-documents/{S['inv']}"), headers=o).json()["balance_due"] == "66400.00"          # ₹83,000 less ₹16,600
    assert len(client.get(url("/shipping-bills"), headers=o).json()["data"]) == bills                              # a credit note opens no shipping bill


def test_a_credit_note_on_an_export_invoice_with_igst_credits_the_igst(client):
    o = login(client, OWNER)
    d = client.post(url("/sales-documents"), headers=o, json=doc(S["abroad"], [GOODS_LINE], currency="USD", exchange_rate="83", supply_type="export_with_tax")).json()
    assert d["supply_type"] == "export_with_tax" and d["igst_total"] == "14940.00"
    assert client.post(url(f"/sales-documents/{d['id']}/issue"), headers={**o, **key()}, json={}).status_code == 202
    body = {"doc_type": "credit_note", "doc_date": str(TODAY), "registration_id": REG, "party_id": S["abroad"], "original_document_id": d["id"], "cn_reason": "sales_return",
            "payment_terms_days": 0, "lines": [{**CN_LINE, "qty": "1"}]}
    p = client.post(url("/sales-documents/preview"), headers=o, json=body).json()
    assert p["is_export"] and p["doc_type"] == "credit_note" and p["igst_total"] == "1494.00" and p["grand_total"] == "9794.00" and p["foreign_total"] == "118.00"
    assert {c["code"] for c in p["checks"]} >= {"export_note", "export_foreign_value"} and not any(c["level"] == "block" for c in p["checks"])
    r = client.post(url("/sales-documents"), headers=o, json=body)
    assert r.status_code == 201 and r.json()["supply_type"] == "export_with_tax" and r.json()["igst_total"] == "1494.00"


def invoice_with_draft(client, o, qty="10"):
    d = client.post(url("/sales-documents"), headers=o, json=doc(S["abroad"], [{**GOODS_LINE, "qty": qty}], currency="USD", exchange_rate="83")).json()
    assert client.post(url(f"/sales-documents/{d['id']}/issue"), headers={**o, **key()}, json={}).status_code == 202
    return d["id"]


def bills_of(client, o, inv):
    return sorted((b for b in client.get(url("/shipping-bills"), headers=o).json()["data"] if b["sales_document_id"] == inv), key=lambda b: b["fob_foreign"])


def test_goods_credited_back_after_the_bill_was_filed_leave_a_task_for_customs(client, db):
    assert db.execute("SELECT count(*) AS n FROM tasks WHERE entity_id = %s AND title LIKE 'Goods worth USD 200.00%%'", (EN,)).fetchone()["n"] == 1      # the first credit note: 2 of 10 valves


def test_money_from_abroad_settles_the_invoice_and_posts_the_exchange_difference(client, db):
    o, v = login(client, OWNER), login(client, VIEWER)
    ok = {"received_on": str(TODAY), "amount_foreign": "800.00", "bank_ref": "SWIFT-1", "firc_no": "FIRC-9", "amount_inr": "66100.00", "bank_charges": "100.00"}
    assert client.post(url(f"/shipping-bills/{S['sb']}/realisations"), headers={**v, **key()}, json=ok).status_code == 403
    r = client.post(url(f"/shipping-bills/{S['sb']}/realisations"), headers=o, json=ok)
    assert r.status_code == 400                                                                                   # money needs an idempotency key
    r = client.post(url(f"/shipping-bills/{S['sb']}/realisations"), headers=o, json={k: v for k, v in ok.items() if k != "amount_inr"} | {"bank_charges": "5.00"})
    assert r.status_code == 400                                                                                   # charges go with the rupees credited
    k = key()
    r = client.post(url(f"/shipping-bills/{S['sb']}/realisations"), headers={**o, **k}, json=ok)
    assert r.status_code == 201, r.text
    got = r.json()["realisations"][0]
    assert got["amount_inr"] == "66100.00" and got["booked_inr"] == "66400.00" and got["forex_gain"] == "-200.00" and got["bank_charges"] == "100.00"          # 800 at the invoice's 83
    again = client.post(url(f"/shipping-bills/{S['sb']}/realisations"), headers={**o, **k}, json=ok)
    assert again.status_code == 201 and len(again.json()["realisations"]) == 1                                  # a replay adds nothing
    inv = client.get(url(f"/sales-documents/{S['inv']}"), headers=o).json()
    assert inv["balance_due"] == "0.00" and inv["payment_status"] == "paid"
    rows = db.execute("""SELECT a.subtype, l.debit, l.credit FROM journal_lines l JOIN ledger_accounts a ON a.id = l.account_id
                          WHERE l.entry_id = (SELECT journal_entry_id FROM receipts WHERE id = %s) ORDER BY l.line_no""", (got["receipt_id"],)).fetchall()
    assert [(x["subtype"], x["debit"], x["credit"]) for x in rows][1:] == [("bank_charges", D("100.00"), D("0.00")), ("receivable", D("0.00"), D("66400.00")), ("forex_gain_loss", D("200.00"), D("0.00"))]
    assert rows[0]["debit"] == D("66100.00")
    assert client.post(url(f"/shipping-bills/{S['sb']}/realisations"), headers={**o, **key()}, json={**ok, "amount_foreign": "300.00"}).status_code == 409          # more than the bill is worth
    assert client.post(url(f"/shipping-bills/{S['sb']}/realisations"), headers={**o, **key()}, json={**ok, "amount_foreign": "100.00", "amount_inr": "8300.00", "bank_charges": "0"}).status_code == 409   # the invoice is settled


def test_proceeds_need_an_issued_export_invoice_behind_the_shipping_bill(client):
    o = login(client, OWNER)
    r = client.post(url("/shipping-bills"), headers=o, json={"sb_number": "7799001", "sb_date": str(TODAY), "port_code": "INMAA1", "currency": "USD", "fob_foreign": "500.00", "exchange_rate": "83.0000"})
    assert r.status_code == 201
    r = client.post(url(f"/shipping-bills/{r.json()['id']}/realisations"), headers={**o, **key()}, json={"received_on": str(TODAY), "amount_foreign": "500.00", "amount_inr": "41500.00"})
    assert r.status_code == 409 and r.json()["code"] == "proceeds_need_invoice"


def test_a_partial_shipment_files_its_share_and_opens_a_draft_for_the_rest(client):
    o = login(client, OWNER)
    inv = invoice_with_draft(client, o)
    draft = bills_of(client, o, inv)[0]
    assert draft["status"] == "draft" and draft["fob_foreign"] == "1000.00"
    r = client.patch(url(f"/shipping-bills/{draft['id']}"), headers=o, json={"sb_number": "7788001", "sb_date": str(TODAY), "port_code": "INMAA1", "fob_foreign": "1100.00"})
    assert r.status_code == 409 and r.json()["code"] == "shipments_exceed_invoice"                                 # more than the goods on the invoice
    r = client.patch(url(f"/shipping-bills/{draft['id']}"), headers=o, json={"sb_number": "7788001", "sb_date": str(TODAY), "port_code": "INMAA1", "fob_foreign": "600.00"})
    assert r.status_code == 200 and r.json()["status"] == "filed"
    bills = bills_of(client, o, inv)
    assert [(b["status"], b["fob_foreign"]) for b in bills] == [("draft", "400.00"), ("filed", "600.00")]          # the rest waits as a draft
    rest = client.post(url("/shipping-bills"), headers=o, json={"sb_number": "7788002", "sb_date": str(TODAY), "port_code": "INBLR4", "currency": "USD", "fob_foreign": "400.00",
                                                              "exchange_rate": "83.0000", "sales_document_id": inv})
    assert rest.status_code == 201 and rest.json()["status"] == "filed"
    assert [(b["status"], b["fob_foreign"]) for b in bills_of(client, o, inv)] == [("filed", "400.00"), ("filed", "600.00")]       # filled in, no further draft
    extra = client.post(url("/shipping-bills"), headers=o, json={"sb_number": "7788003", "sb_date": str(TODAY), "port_code": "INBLR4", "currency": "USD", "fob_foreign": "10.00",
                                                               "exchange_rate": "83.0000", "sales_document_id": inv})
    assert extra.status_code == 409 and extra.json()["code"] == "shipments_exceed_invoice"


def test_goods_credited_back_shrink_the_draft_and_remove_it_when_nothing_is_left(client):
    o = login(client, OWNER)
    inv = invoice_with_draft(client, o)
    for qty, left in (("2", "800.00"), ("8", None)):
        cn = client.post(url(f"/sales-documents/{inv}/credit-notes"), headers=o, json={"cn_reason": "sales_return", "lines": [{**CN_LINE, "qty": qty}]}).json()
        assert client.post(url(f"/sales-documents/{cn['id']}/issue"), headers={**o, **key()}, json={}).status_code == 202
        bills = bills_of(client, o, inv)
        assert [b["fob_foreign"] for b in bills] == ([left] if left else [])


def test_the_e_invoice_request_for_an_export_names_the_buyer_as_unregistered_and_carries_the_shipping_bill(client, db, monkeypatch):
    o = login(client, OWNER)
    inv = invoice_with_draft(client, o, qty="1")
    sent = {}

    class Irp:
        def generate_irn(self, payload):
            sent.update(payload)
            return {"irn": "i" * 64, "ack_no": "123456789012345", "signed_qr": "qr"}
    monkeypatch.setattr(worker, "IRP", Irp())
    with psycopg.connect(DB, row_factory=dict_row) as c:
        worker.handle_generate_irn(c.cursor(), {"subject_id": inv})
    assert sent["buyer_gstin"] == "URP" and sent["export"]["supply_type"] == "EXPWOP" and sent["export"]["place_of_supply"] == "96" and sent["export"]["foreign_currency"] == "USD"
    assert "shipping_bill_no" not in sent["export"]                                                                 # still a draft: customs has not issued the number yet


def test_a_credit_that_reads_like_an_inward_remittance_is_taken_as_proceeds_though_the_bank_charged_for_it(client, db):
    o, v = login(client, OWNER), login(client, VIEWER)
    inv = invoice_with_draft(client, o, qty="5")                                                                    # USD 500 at 83 = ₹41,500
    sb = bills_of(client, o, inv)[0]["id"]
    ledger_id, bank = str(uuid.uuid4()), str(uuid.uuid4())
    db.execute("INSERT INTO ledger_accounts (id, entity_id, code, name, type, subtype) VALUES (%s, %s, '1199', 'Export collections account', 'asset', 'bank')", (ledger_id, EN))
    db.execute("INSERT INTO bank_accounts (id, entity_id, ledger_account_id, bank_name, account_last4, ifsc, kind) VALUES (%s, %s, %s, 'HDFC', '5511', 'HDFC0000123', 'current')", (bank, EN, ledger_id))
    line = {"external_id": "XF-1", "txn_date": str(TODAY), "amount": "41150.00", "description": "INWARD REMITTANCE ACME INC USD 500.00 SWIFT", "utr": "SWIFT-77", "counterparty_name": "ACME INC"}
    r = client.post(url(f"/bank-accounts/{bank}/transactions"), headers=o, json={"transactions": [line]})
    assert r.status_code == 201 and r.json()["suggested"] == 1
    t = client.get(url(f"/bank-transactions?bank_account_id={bank}"), headers=o).json()["data"][0]
    assert t["match_status"] == "suggested"
    sug = next(s for s in t["suggestions"] if s["target_id"] == sb)
    assert sug["target_kind"] == "shipping_bill" and sug["rule"] == "export_proceeds" and sug["confidence"] == 0.95 and "Acme Inc" in sug["label"]
    assert sug["export"]["proposed_foreign"] == "500.00" and sug["export"]["expected_inr"] == "41500.00" and sug["export"]["difference_inr"] == "-350.00" and sug["export"]["currency"] == "USD"
    assert client.post(url(f"/bank-matches/{sug['id']}/accept"), headers={**o, **key()}).status_code == 409           # the dollars are needed, so the plain accept refuses
    body = {"shipping_bill_id": sb, "amount_foreign": "500.00", "bank_charges": "350.00", "firc_no": "FIRC-77"}
    assert client.post(url(f"/bank-transactions/{t['id']}/export-proceeds"), headers={**v, **key()}, json=body).status_code == 403
    assert client.post(url(f"/bank-transactions/{t['id']}/export-proceeds"), headers=o, json=body).status_code == 400    # money needs an idempotency key
    k = key()
    r = client.post(url(f"/bank-transactions/{t['id']}/export-proceeds"), headers={**o, **k}, json=body)
    assert r.status_code == 200, r.text
    got = r.json()
    assert got["match_status"] == "matched" and [(s["status"], s["target_kind"]) for s in got["suggestions"] if s["id"] == sug["id"]] == [("accepted", "receipt")]
    assert client.post(url(f"/bank-transactions/{t['id']}/export-proceeds"), headers={**o, **k}, json=body).status_code == 200                   # a replay
    assert client.post(url(f"/bank-transactions/{t['id']}/export-proceeds"), headers={**o, **key()}, json=body).status_code == 409                # already matched
    assert client.get(url(f"/sales-documents/{inv}"), headers=o).json()["balance_due"] == "0.00"
    real = next(x for x in next(b for b in client.get(url("/shipping-bills"), headers=o).json()["data"] if b["id"] == sb)["realisations"])
    assert real["amount_inr"] == "41150.00" and real["bank_charges"] == "350.00" and real["booked_inr"] == "41500.00" and real["forex_gain"] == "0.00" and real["firc_no"] == "FIRC-77"
    row = db.execute("SELECT bank_transaction_id, bank_account_id, utr FROM receipts WHERE id = %s", (real["receipt_id"],)).fetchone()
    assert str(row["bank_transaction_id"]) == t["id"] and str(row["bank_account_id"]) == bank and row["utr"] == "SWIFT-77"
    assert client.post(url(f"/bank-transactions/{uuid.uuid4()}/export-proceeds"), headers={**o, **key()}, json=body).status_code == 404


def test_a_refused_suggestion_is_not_offered_again_and_a_debit_is_never_proceeds(client, db):
    o = login(client, OWNER)
    bank = str(db.execute("SELECT id FROM bank_accounts WHERE entity_id = %s", (EN,)).fetchone()["id"])
    credit = {"external_id": "XF-2", "txn_date": str(TODAY), "amount": "20000.00", "description": "IRM FCY INWARD REMITTANCE ACME INC", "counterparty_name": "ACME INC"}
    assert client.post(url(f"/bank-accounts/{bank}/transactions"), headers=o, json={"transactions": [credit]}).json()["suggested"] >= 1
    txns = {x["external_id"] if "external_id" in x else x["description"]: x for x in client.get(url(f"/bank-transactions?bank_account_id={bank}"), headers=o).json()["data"]}
    t = txns["IRM FCY INWARD REMITTANCE ACME INC"]
    first = t["suggestions"][0]
    refused = [s["target_id"] for s in t["suggestions"]]
    for s in t["suggestions"]:
        assert client.post(url(f"/bank-matches/{s['id']}/reject"), headers=o).status_code == 200
    debit = {"external_id": "XF-3", "txn_date": str(TODAY), "amount": "-500.00", "description": "INWARD REMITTANCE CHARGES ACME INC"}
    assert client.post(url(f"/bank-accounts/{bank}/transactions"), headers=o, json={"transactions": [debit]}).status_code == 201           # imports re-run the matcher
    again = next(x for x in client.get(url(f"/bank-transactions?bank_account_id={bank}"), headers=o).json()["data"] if x["id"] == t["id"])
    assert not set(refused) & {s["target_id"] for s in again["suggestions"] if s["status"] == "suggested"}
    d = next(x for x in client.get(url(f"/bank-transactions?bank_account_id={bank}"), headers=o).json()["data"] if x["description"].startswith("INWARD REMITTANCE CHARGES"))
    assert d["suggestions"] == []
    r = client.post(url(f"/bank-transactions/{d['id']}/export-proceeds"), headers={**o, **key()}, json={"shipping_bill_id": first["target_id"], "amount_foreign": "10.00"})
    assert r.status_code == 400


def test_a_note_to_a_customer_abroad_must_correct_an_export_invoice(client):
    o = login(client, OWNER)
    r = client.post(url("/sales-documents"), headers=o, json={**doc(S["abroad"], [GOODS_LINE]), "doc_type": "credit_note", "cn_reason": "sales_return"})
    assert r.status_code == 400 and r.json()["code"] == "export_note_needs_invoice"
    r = client.post(url("/sales-documents"), headers=o, json={**doc(S["local"], [GOODS_LINE]), "doc_type": "credit_note", "original_document_id": S["inv"], "cn_reason": "sales_return"})
    assert r.status_code == 400                                                                                   # a domestic customer cannot be credited against another's export invoice


def test_the_audit_chain_is_intact(db):
    assert db.execute("SELECT app.audit_verify() AS v").fetchone()["v"] is None
