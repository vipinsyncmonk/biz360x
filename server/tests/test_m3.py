"""M3 end to end: purchases and the MSME clock, vendor payments with approval, GST returns from the books,
2B matching, TDS deposits and returns, advance tax.

Own entity (EN3); tests run in order and build on one another. Portal calls go through the sandbox adapters via the worker.
"""
import datetime as dt
import os
import uuid
from decimal import Decimal as D

import psycopg
import pytest
from psycopg.rows import dict_row

DB = os.environ["DATABASE_URL"]
ORG = "00000000-0000-0000-0000-0000000000a1"
EN = "00000000-0000-0000-0000-0000000000c1"
OWNER_ID, APPROVER_ID, VIEWER_ID = ("00000000-0000-0000-0000-0000000000c%d" % i for i in (2, 3, 4))
REG, TAN = "00000000-0000-0000-0000-0000000000c5", "00000000-0000-0000-0000-0000000000c6"
V_MSME, V_BIG, V_LOCAL, BUYER = ("00000000-0000-0000-0000-0000000000d%d" % i for i in (1, 2, 3, 4))
G_MSME, G_BIG, G_BUYER = ("00000000-0000-0000-0000-0000000000d%d" % i for i in (5, 6, 7))
BANK_LEDGER, BANK = "00000000-0000-0000-0000-0000000000d8", "00000000-0000-0000-0000-0000000000d9"
GSTIN = "29AAACN3333C1Z5"
OWNER, APPROVER, VIEWER = "m3-owner@example.in", "m3-approver@example.in", "m3-viewer@example.in"
AS_OF = dt.date(2026, 11, 6)
S: dict = {}
KINDS = "('compliance.run_engine','collections.dunning','collections.recurring','purchases.msme_alerts','einvoice.generate_irn')"


@pytest.fixture(scope="module")
def db():
    with psycopg.connect(DB, autocommit=True, row_factory=dict_row) as c:
        yield c


@pytest.fixture(scope="module", autouse=True)
def entity(db):
    db.execute("INSERT INTO entities (id, organization_id, legal_name, constitution, track) VALUES (%s, %s, 'M3 Test Pvt Ltd', 'private_limited', 'business')", (EN, ORG))
    db.execute("""INSERT INTO entity_registrations (id, entity_id, kind, number, state_code, gst_filing_frequency) VALUES
                  (%s, %s, 'gstin', %s, '29', 'monthly'), (%s, %s, 'tan', 'BLRN33333C', '29', NULL)""", (REG, EN, GSTIN, TAN, EN))
    db.execute("INSERT INTO document_series (entity_id, registration_id, doc_type, fy, prefix) VALUES (%s, %s, 'tax_invoice', '2026-27', 'N3/KA/'), (%s, %s, 'credit_note', '2026-27', 'N3/CN/')", (EN, REG, EN, REG))
    db.execute("INSERT INTO users (id, email, full_name) VALUES (%s, %s, 'M3 Owner'), (%s, %s, 'M3 Approver'), (%s, %s, 'M3 Viewer')", (OWNER_ID, OWNER, APPROVER_ID, APPROVER, VIEWER_ID, VIEWER))
    db.execute("INSERT INTO memberships (entity_id, user_id, role) VALUES (%s, %s, 'owner'), (%s, %s, 'approver'), (%s, %s, 'viewer')", (EN, OWNER_ID, EN, APPROVER_ID, EN, VIEWER_ID))
    db.execute("""INSERT INTO parties (id, entity_id, legal_name, is_vendor, is_customer, pan, msme_class, udyam_number, payment_terms_days) VALUES
                  (%s, %s, 'Shakti Castings', true, false, 'AAKFS1234A', 'small', 'UDYAM-KR-03-0012345', 30),
                  (%s, %s, 'Vega Electro Components', true, false, 'AAFCV5555B', NULL, NULL, 45),
                  (%s, %s, 'Local Haulier', true, false, NULL, NULL, NULL, 15),
                  (%s, %s, 'M3 Buyer', false, true, NULL, NULL, NULL, 30)""", (V_MSME, EN, V_BIG, EN, V_LOCAL, EN, BUYER, EN))
    db.execute("""INSERT INTO party_gst_registrations (id, party_id, gstin, state_code, is_default) VALUES
                  (%s, %s, '29AAKFS1234A1Z5', '29', true), (%s, %s, '33AAFCV5555B1Z7', '33', true), (%s, %s, '32AABCN9999K1ZP', '32', true)""", (G_MSME, V_MSME, G_BIG, V_BIG, G_BUYER, BUYER))
    db.execute("INSERT INTO ledger_accounts (id, entity_id, code, name, type, subtype) VALUES (%s, %s, '1100', 'HDFC current', 'asset', 'bank')", (BANK_LEDGER, EN))
    db.execute("INSERT INTO bank_accounts (id, entity_id, ledger_account_id, bank_name, account_last4, ifsc, kind) VALUES (%s, %s, %s, 'HDFC', '9001', 'HDFC0000123', 'current')", (BANK, EN, BANK_LEDGER))
    db.execute("INSERT INTO entity_facts (entity_id, fact_key, value_num, as_of, source) VALUES (%s, 'estimated_tax', 120000, '2026-04-01', 'user')", (EN,))
    yield
    db.execute(f"UPDATE jobs SET status = 'cancelled' WHERE status = 'queued' AND kind IN {KINDS}")


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


def url(path):
    return f"/v1/entities/{EN}{path}"


def key():
    return {"Idempotency-Key": str(uuid.uuid4())}


def run_worker(db):
    """Run every queued job the worker knows (cancelling unrelated background kinds first)."""
    from app import worker
    db.execute(f"UPDATE jobs SET status = 'cancelled' WHERE status = 'queued' AND kind IN {KINDS}")
    n = 0
    while worker.run_once():
        n += 1
        assert n < 50
    return n


def job_status(db, job_id):
    return db.execute("SELECT status, result, error FROM jobs WHERE id = %s", (job_id,)).fetchone()


def line(desc="Castings", qty="10", rate="1000.00", gst=18, **kw):
    return {"description": desc, "hsn_sac": "7325", "qty": qty, "rate": rate, "gst_rate": gst, **kw}


def bill_body(party=V_MSME, no="SC/001", bill_date="2026-10-08", accepted="2026-10-10", lines=None, **kw):
    return {"party_id": party, "registration_id": REG, "bill_no": no, "bill_date": bill_date, "accepted_on": accepted, "lines": lines or [line()], **kw}


def accounts(client, h):
    return {a["id"]: a["name"] for a in client.get(url("/accounts"), headers=h, params={"limit": 500}).json()["data"]}


def journal_of(client, h, source, source_id):
    js = client.get(url("/journals"), headers=h, params={"source": source}).json()["data"]
    return next(j for j in js if j["source_id"] == source_id)


# ================================================================== purchase orders
def test_purchase_orders(client, h, hv):
    body = {"party_id": V_MSME, "po_date": "2026-10-01", "lines": [line("Castings", "100", "250.00"), line("Machining", "10", "100.00", 18)]}
    assert client.post(url("/purchase-orders"), headers=hv, json=body).json()["code"] == "permission_denied"
    assert client.post(url("/purchase-orders"), headers=h, json={**body, "party_id": BUYER}).json()["code"] == "validation_failed"        # a customer, not a vendor
    po = client.post(url("/purchase-orders"), headers=h, json=body)
    assert po.status_code == 201, po.text
    po = po.json()
    assert po["po_no"] == "PO-0001" and po["status"] == "open" and po["total"] == "30680.00" and [l["received_qty"] for l in po["lines"]] == ["0.00", "0.00"]
    assert client.get(url(f"/purchase-orders/{po['id']}"), headers=hv).json()["lines"][0]["description"] == "Castings"
    assert client.get(url("/purchase-orders"), headers=h).json()["data"][0]["id"] == po["id"]
    assert client.get(url(f"/purchase-orders/{uuid.uuid4()}"), headers=h).status_code == 404


# ================================================================== bills
def test_bill_totals_tds_and_msme_dates(client, h):
    r = client.post(url("/purchase-bills"), headers=h, json=bill_body(tds_section="194C", agreed_terms_days=30))
    assert r.status_code == 201, r.text
    b = r.json()
    assert (b["status"], b["taxable_total"], b["cgst_total"], b["sgst_total"], b["igst_total"], b["grand_total"]) == ("draft", "10000.00", "900.00", "900.00", "0.00", "11800.00")
    assert b["tds_amount"] == "100.00" and b["balance_due"] == "11700.00"
    assert b["msme_applicable"] is True and b["msme_pay_by"] == "2026-11-09"                  # accepted 10 Oct + 30 agreed days (cap is 45)
    assert b["due_date"] == "2026-11-07"                                                      # bill date + the agreed 30 days
    S["b1"] = b
    s = client.post(url("/purchase-bills"), headers=h, json=bill_body(V_MSME, "SC/000", "2026-09-15", "2026-09-20", agreed_terms_days=60)).json()
    assert s["msme_pay_by"] == "2026-11-04"                                                   # 60 agreed days are capped at 45 from acceptance
    S["b_old"] = s
    inter = client.post(url("/purchase-bills"), headers=h, json=bill_body(V_BIG, "VE/77", lines=[line("Sensors", "5", "2000.00")])).json()
    assert (inter["igst_total"], inter["cgst_total"], inter["grand_total"], inter["msme_applicable"], inter["msme_pay_by"]) == ("1800.00", "0.00", "11800.00", False, None)
    S["b_big"] = inter


def test_bill_guards(client, h):
    assert client.post(url("/purchase-bills"), headers=h, json=bill_body(reverse_charge=True)).json()["code"] == "validation_failed"
    assert client.post(url("/purchase-bills"), headers=h, json=bill_body(itc_eligibility="partial")).json()["code"] == "validation_failed"
    assert client.post(url("/purchase-bills"), headers=h, json=bill_body(tds_section="194ZZ")).json()["code"] == "validation_failed"
    assert client.post(url("/purchase-bills"), headers=h, json=bill_body(party=BUYER)).json()["code"] == "validation_failed"            # not a vendor
    unregistered = client.post(url("/purchase-bills"), headers=h, json=bill_body(V_LOCAL, "LH/1"))
    assert unregistered.status_code == 400                                                    # GST charged by an unregistered supplier cannot be claimed
    ok = client.post(url("/purchase-bills"), headers=h, json=bill_body(V_LOCAL, "LH/1", lines=[line("Freight", "1", "5000.00", 0)], tds_section="194C", itc_eligibility="ineligible"))
    assert ok.status_code == 201 and ok.json()["tds_amount"] == "1000.00"                     # no PAN: 20%, not 1%
    S["b_local"] = ok.json()


def test_booking_posts_the_ledger_and_the_tds_deduction(client, h, db):
    k = key()
    r = client.post(url(f"/purchase-bills/{S['b1']['id']}/book"), headers={**h, **k})
    assert r.status_code == 200 and r.json()["status"] == "booked", r.text
    assert client.post(url(f"/purchase-bills/{S['b1']['id']}/book"), headers={**h, **k}).json()["id"] == S["b1"]["id"]            # replay
    assert client.post(url(f"/purchase-bills/{S['b1']['id']}/book"), headers={**h, **key()}).json()["code"] == "conflict"
    names = accounts(client, h)
    j = journal_of(client, h, "purchase_bill", S["b1"]["id"])
    assert {names[l["account_id"]]: (l["debit"], l["credit"]) for l in j["lines"]} == {
        "Purchases and expenses": ("10000.00", "0.00"), "Input CGST": ("900.00", "0.00"), "Input SGST": ("900.00", "0.00"),
        "Trade payables": ("0.00", "11700.00"), "TDS payable": ("0.00", "100.00")}
    td = db.execute("SELECT section, base_amount, rate_pct, tds_amount, deductee_pan, source_kind FROM tds_deductions WHERE source_id = %s", (S["b1"]["id"],)).fetchone()
    assert (td["section"], td["base_amount"], td["tds_amount"], td["deductee_pan"], td["source_kind"]) == ("194C", D("10000.00"), D("100.00"), "AAKFS1234A", "purchase_bill")
    for b in ("b_old", "b_big", "b_local"):
        assert client.post(url(f"/purchase-bills/{S[b]['id']}/book"), headers={**h, **key()}).status_code == 200
    local = journal_of(client, h, "purchase_bill", S["b_local"]["id"])
    assert {names[l["account_id"]]: (l["debit"], l["credit"]) for l in local["lines"]} == {
        "Purchases and expenses": ("5000.00", "0.00"), "Trade payables": ("0.00", "4000.00"), "TDS payable": ("0.00", "1000.00")}
    dup = client.post(url("/purchase-bills"), headers=h, json=bill_body(V_MSME, "SC/001"))
    assert dup.status_code == 409 and dup.json()["code"] == "conflict"                        # same supplier, number and FY


def test_bill_listing_and_filters(client, h):
    page = client.get(url("/purchase-bills"), headers=h, params={"msme": "true", "status": "booked"}).json()
    assert {b["bill_no"] for b in page["data"]} == {"SC/000", "SC/001"}
    assert [b["bill_no"] for b in client.get(url("/purchase-bills"), headers=h, params={"party_id": V_BIG}).json()["data"]] == ["VE/77"]
    assert client.get(url(f"/purchase-bills/{S['b1']['id']}"), headers=h).json()["lines"][0]["cgst_amount"] == "900.00"
    assert client.get(url(f"/purchase-bills/{uuid.uuid4()}"), headers=h).status_code == 404


# ================================================================== MSME clock
def test_msme_payables(client, h, db):
    rows = client.get(url("/msme-payables"), headers=h).json()
    assert [r["bill_no"] for r in rows] == ["SC/000", "SC/001"] and all(r["legal_name"] == "Shakti Castings" for r in rows)
    old = rows[0]
    assert old["breached"] is True and old["deduction_at_risk_43bh"] == old["balance_due"] == "11800.00"
    late = D(rows[1]["days_left"]) < 0                                                       # SC/001 is due on 9 Nov 2026, so whether it has breached depends on the day the suite runs
    assert rows[1]["breached"] is late and rows[1]["deduction_at_risk_43bh"] == (rows[1]["balance_due"] if late else "0.00")
    assert [r["bill_no"] for r in client.get(url("/msme-payables"), headers=h, params={"breached": "true"}).json()] == (["SC/000", "SC/001"] if late else ["SC/000"])
    from app import routes_purchases
    with db.transaction():
        first = routes_purchases.msme_alerts(db.cursor(), EN, dt.date(2026, 11, 6))
    with db.transaction():
        again = routes_purchases.msme_alerts(db.cursor(), EN, dt.date(2026, 11, 6))
    assert first == 2 and again == 0                                                         # SC/000 breached, SC/001 due in 3 days; each alert once
    ev = db.execute("SELECT data->>'stage' AS stage FROM event_outbox WHERE entity_id = %s AND type = 'msme_payable.due_soon' ORDER BY id", (EN,)).fetchall()
    assert sorted(e["stage"] for e in ev) == ["breached", "due_soon"]


# ================================================================== vendor payments
def pay_body(bill, amount="11700.00", **kw):
    return {"party_id": V_MSME, "amount": amount, "method": "neft", "bank_account_id": BANK, "scheduled_on": "2026-11-06",
            "allocations": [{"bill_id": bill["id"], "amount": amount}], **kw}


def test_vendor_payment_needs_an_independent_approval(client, h, ha, hv, db):
    assert client.post(url("/vendor-payments"), headers={**h, **key()}, json=pay_body(S["b1"], "99999.00")).json()["code"] == "over_settlement"
    assert client.post(url("/vendor-payments"), headers={**h, **key()}, json=pay_body(S["b_big"])).json()["code"] == "validation_failed"      # another vendor's bill
    assert client.post(url("/vendor-payments"), headers=h, json=pay_body(S["b1"])).json()["code"] == "validation_failed"                    # key is mandatory
    k = key()
    r = client.post(url("/vendor-payments"), headers={**h, **k}, json=pay_body(S["b1"]))
    assert r.status_code == 201, r.text
    vp = r.json()
    assert vp["payment_no"] == "VP-0001" and vp["status"] == "scheduled" and vp["approval_request_id"]
    assert client.post(url("/vendor-payments"), headers={**h, **k}, json=pay_body(S["b1"])).json()["id"] == vp["id"]
    assert client.post(url("/vendor-payments"), headers={**h, **key()}, json=pay_body(S["b1"])).json()["code"] == "over_settlement"       # already committed
    rel = client.post(url(f"/vendor-payments/{vp['id']}/release"), headers={**h, **key()})
    assert rel.status_code == 409 and rel.json()["code"] == "approval_required"
    own = client.post(f"/v1/entities/{EN}/approvals/{vp['approval_request_id']}/approve", headers={**h, **key()}, json={})
    assert own.status_code == 403 and own.json()["code"] == "two_person_rule"
    done = client.post(f"/v1/entities/{EN}/approvals/{vp['approval_request_id']}/approve", headers={**ha, **key()}, json={"note": "ok"})
    assert done.status_code == 200
    assert client.get(url(f"/vendor-payments/{vp['id']}"), headers=h).json()["status"] == "approved"
    assert client.post(url(f"/vendor-payments/{vp['id']}/release"), headers={**hv, **key()}).json()["code"] == "permission_denied"
    S["vp"] = vp


def test_release_pays_the_bank_and_closes_the_bill(client, h, db):
    k = key()
    rel = client.post(url(f"/vendor-payments/{S['vp']['id']}/release"), headers={**h, **k})
    assert rel.status_code == 202 and rel.json()["kind"] == "bank.release_payment"
    assert client.post(url(f"/vendor-payments/{S['vp']['id']}/release"), headers={**h, **k}).json()["id"] == rel.json()["id"]
    assert client.post(url(f"/vendor-payments/{S['vp']['id']}/release"), headers={**h, **key()}).json()["code"] == "conflict"       # already sent
    run_worker(db)
    assert job_status(db, rel.json()["id"])["status"] == "succeeded"
    vp = client.get(url(f"/vendor-payments/{S['vp']['id']}"), headers=h).json()
    assert vp["status"] == "paid" and vp["utr"].startswith("SBX") and vp["paid_on"]
    bill = client.get(url(f"/purchase-bills/{S['b1']['id']}"), headers=h).json()
    assert bill["status"] == "paid" and bill["amount_paid"] == "11700.00" and bill["balance_due"] == "0.00"
    names = accounts(client, h)
    j = journal_of(client, h, "vendor_payment", S["vp"]["id"])
    assert {names[l["account_id"]]: (l["debit"], l["credit"]) for l in j["lines"]} == {"Trade payables": ("11700.00", "0.00"), "HDFC current": ("0.00", "11700.00")}
    assert [r["bill_no"] for r in client.get(url("/msme-payables"), headers=h).json()] == ["SC/000"]                                  # SC/001 left the clock
    assert len(client.get(url("/vendor-payments"), headers=h, params={"status": "paid"}).json()["data"]) == 1


def test_rejected_payment_is_cancelled(client, h, ha, db):
    vp = client.post(url("/vendor-payments"), headers={**h, **key()}, json=pay_body(S["b_old"], "5000.00", allocations=[{"bill_id": S["b_old"]["id"], "amount": "5000.00"}])).json()
    r = client.post(f"/v1/entities/{EN}/approvals/{vp['approval_request_id']}/reject", headers={**ha, **key()}, json={"note": "dispute"})
    assert r.status_code == 200
    assert client.get(url(f"/vendor-payments/{vp['id']}"), headers=h).json()["status"] == "cancelled"
    assert client.post(url(f"/vendor-payments/{vp['id']}/release"), headers={**h, **key()}).json()["code"] == "approval_required"


# ================================================================== sales for GSTR-1, obligations
def sale(client, h, lines, date="2026-10-12", party=BUYER, gst=G_BUYER, **kw):
    d = client.post(url("/sales-documents"), headers=h, json={"doc_type": kw.pop("doc_type", "tax_invoice"), "doc_date": date, "registration_id": REG,
                                                              "party_id": party, "party_gst_id": gst, "lines": lines, **kw})
    assert d.status_code == 201, d.text
    assert client.post(url(f"/sales-documents/{d.json()['id']}/issue"), headers={**h, **key()}, json={}).status_code == 202
    return client.get(url(f"/sales-documents/{d.json()['id']}"), headers=h).json()


def test_engine_creates_the_october_obligations(db):
    from app import engine
    with db.transaction():
        run = engine.run_engine(db.cursor(), EN, "manual", AS_OF)
    assert run["obligations_created"] > 5
    rows = db.execute("""SELECT r.code, o.period_label, o.id, o.status::text AS st FROM obligations o JOIN compliance_rules r ON r.id = o.rule_id
                          WHERE o.entity_id = %s AND r.code IN ('GST.GSTR1.MONTHLY', 'GST.GSTR3B.MONTHLY', 'TDS.DEPOSIT.MONTHLY', 'TDS.RETURN.QUARTERLY', 'IT.ADVANCE_TAX')""", (EN,)).fetchall()
    S["ob"] = {(r["code"], r["period_label"]): r["id"] for r in rows}
    for k in (("GST.GSTR1.MONTHLY", "Oct 2026"), ("GST.GSTR3B.MONTHLY", "Oct 2026"), ("TDS.DEPOSIT.MONTHLY", "Oct 2026"), ("TDS.RETURN.QUARTERLY", "Q3 FY26-27"),
              ("IT.ADVANCE_TAX", "Q3 FY26-27")):
        assert k in S["ob"], (k, sorted(S["ob"]))


def test_gstr1_is_built_from_the_books(client, h, ha, db):
    inv1 = sale(client, h, [line("Gauges", "10", "1000.00")])                       # 10 000 + 1 800 IGST
    inv2 = sale(client, h, [line("Gauges", "5", "2000.00")], date="2026-10-20")      # 10 000 + 1 800 IGST
    S["inv1"] = inv1
    cn = sale(client, h, [line("Return", "2", "1000.00")], date="2026-10-25", doc_type="credit_note", original_document_id=inv1["id"], cn_reason="sales_return", payment_terms_days=0)
    assert cn["number"].startswith("N3/CN/") and cn["igst_total"] == "360.00"
    ob = S["ob"][("GST.GSTR1.MONTHLY", "Oct 2026")]
    r = client.post(url(f"/obligations/{ob}/prepare"), headers=h)
    assert r.status_code == 202 and r.json()["kind"] == "obligation.prepare"
    assert client.post(url(f"/obligations/{S['ob'][('IT.ADVANCE_TAX', 'Q3 FY26-27')]}/prepare"), headers=h).status_code == 202            # queued, processed below
    run_worker(db)
    assert job_status(db, r.json()["id"])["status"] == "succeeded", job_status(db, r.json()["id"])
    ret = client.get(url("/gst-returns"), headers=h, params={"return_type": "GSTR1", "period": "2026-10"}).json()["data"][0]
    s = ret["summary"]
    assert ret["status"] == "ready" and ret["obligation_id"] == str(ob)
    assert (s["b2b"]["count"], s["b2b"]["taxable"], s["b2b"]["igst"]) == (2, "20000.00", "3600.00")
    assert (s["cdnr"]["count"], s["cdnr"]["taxable"], s["cdnr"]["igst"]) == (1, "-2000.00", "-360.00")               # credit notes count negatively
    assert ret["output_tax"] == "3240.00" and s["totals"]["taxable"] == "18000.00"
    assert [(x["hsn_sac"], x["taxable"]) for x in s["hsn"]] == [("7325", "18000.00")]
    assert s["docs"]["tax_invoice"]["total"] == 2 and s["docs"]["credit_note"]["total"] == 1
    assert db.execute("SELECT count(*) AS n FROM sales_documents WHERE gstr1_return_id = %s", (ret["id"],)).fetchone()["n"] == 3
    assert db.execute("SELECT status::text AS st FROM obligations WHERE id = %s", (ob,)).fetchone()["st"] == "in_preparation"
    S["gstr1"] = ret
    assert client.get(url(f"/gst-returns/{ret['id']}"), headers=h).json()["id"] == ret["id"]
    assert client.get(url(f"/gst-returns/{uuid.uuid4()}"), headers=h).status_code == 404


# ================================================================== 2B matching
def test_inward_sync_and_matching(client, h, hv, db):
    from app import routes_gst
    portal = [
        {"supplier_gstin": "29AAKFS1234A1Z5", "supplier_name": "Shakti Castings", "doc_number": "sc-1", "doc_date": "2026-10-08", "taxable_value": "10000.00", "cgst": "900.00", "sgst": "900.00"},
        {"supplier_gstin": "33AAFCV5555B1Z7", "supplier_name": "Vega Electro", "doc_number": "VE/0077", "doc_date": "2026-10-08", "taxable_value": "10000.00", "igst": "1500.00"},
        {"supplier_gstin": "27AAPCP1111Q1Z2", "supplier_name": "Prime Logistics", "doc_number": "PL/9", "doc_date": "2026-10-15", "taxable_value": "7500.00", "igst": "1350.00"}]
    routes_gst.GSP.inward[(GSTIN, "2026-10", "2B")] = portal
    r = client.post(url("/gst/inward-sync"), headers=h, json={"period": "2026-10", "source": "2B"})
    assert r.status_code == 202
    assert client.post(url("/gst/inward-sync"), headers=hv, json={"period": "2026-10", "source": "2B"}).json()["code"] == "permission_denied"
    assert client.post(url("/gst/inward-sync"), headers=h, json={"period": "Oct", "source": "2B"}).status_code == 400
    run_worker(db)
    res = job_status(db, r.json()["id"])
    assert res["status"] == "succeeded" and res["result"]["fetched"] == 3 and res["result"]["matches"] == {"matched": 1, "amount_differs": 1, "not_in_books": 1}
    summary = {x["status"]: x for x in client.get(url("/itc-matches/summary"), headers=h, params={"period": "2026-10"}).json()}
    assert summary["matched"]["itc"] == "1800.00" and summary["amount_differs"]["itc"] == "1800.00" and summary["not_in_books"]["itc"] == "1350.00"
    ms = {m["status"]: m for m in client.get(url("/itc-matches"), headers=h, params={"open": "true"}).json()["data"]}
    assert ms["amount_differs"]["supplier_gstin"] == "33AAFCV5555B1Z7" and ms["amount_differs"]["books_tax"] == "1800.00" and ms["amount_differs"]["portal_tax"] == "1500.00"
    assert ms["amount_differs"]["tax_difference"] == "300.00" and ms["amount_differs"]["suggested_action"] == "claim_lower"
    assert ms["not_in_books"]["supplier_name"] == "Prime Logistics" and ms["not_in_books"]["purchase_bill_id"] is None
    S["m"] = ms
    assert client.get(url("/itc-matches"), headers=h, params={"status": "matched"}).json()["data"][0]["finding"].startswith("Matches")
    bill = client.get(url(f"/purchase-bills/{S['b1']['id']}"), headers=h).json()
    assert bill["itc_match_status"] == "matched"


def test_resolving_matches_and_reminding_suppliers(client, h, db):
    m = S["m"]["amount_differs"]
    bad = client.post(url(f"/itc-matches/{m['id']}/resolve"), headers=h, json={"action": "claim"})
    assert bad.status_code == 400 and "claim_lower" in bad.json()["errors"][0]["message"]
    ok = client.post(url(f"/itc-matches/{m['id']}/resolve"), headers=h, json={"action": "ask_to_amend", "note": "rate mismatch"})
    assert ok.status_code == 200 and ok.json()["action"] == "ask_to_amend" and ok.json()["action_taken_at"]
    # a bill the supplier has not reported
    nb = client.post(url("/purchase-bills"), headers=h, json=bill_body(V_BIG, "VE/90", "2026-10-18", "2026-10-19", lines=[line("Cables", "1", "4000.00")])).json()
    client.post(url(f"/purchase-bills/{nb['id']}/book"), headers={**h, **key()})
    run_worker(db)
    from app import routes_gst
    with db.transaction():
        routes_gst.run_matching(db.cursor(), EN, "2026-10")
    miss = client.get(url("/itc-matches"), headers=h, params={"status": "not_in_portal"}).json()["data"]
    assert [x["supplier_name"] for x in miss] == ["Vega Electro Components"]
    k = key()
    rem = client.post(url("/itc-matches/remind-suppliers"), headers={**h, **k}, json={})
    assert rem.status_code == 202 and rem.json() == {"suppliers": 1, "invoices": 1}
    assert client.post(url("/itc-matches/remind-suppliers"), headers={**h, **k}, json={}).json() == {"suppliers": 1, "invoices": 1}      # replay
    assert client.post(url("/itc-matches/remind-suppliers"), headers={**h, **key()}, json={}).json() == {"suppliers": 0, "invoices": 0}    # nothing left to chase
    assert db.execute("SELECT count(*) AS n FROM tasks WHERE entity_id = %s AND title LIKE 'Remind Vega%%'", (EN,)).fetchone()["n"] == 1
    again = client.get(url("/itc-matches"), headers=h, params={"status": "matched"}).json()["data"][0]
    assert again["action"] is None                                                           # a re-match did not wipe untouched rows into limbo
    assert client.post(url(f"/itc-matches/{uuid.uuid4()}/resolve"), headers=h, json={"action": "defer"}).status_code == 404


# ================================================================== GSTR-3B, approval, filing
def test_gstr3b_claims_only_what_is_in_2b_and_files(client, h, ha, db):
    ob = S["ob"][("GST.GSTR3B.MONTHLY", "Oct 2026")]
    r = client.post(url(f"/obligations/{ob}/prepare"), headers=h)
    run_worker(db)
    assert job_status(db, r.json()["id"])["status"] == "succeeded"
    ret = client.get(url("/gst-returns"), headers=h, params={"return_type": "GSTR3B"}).json()["data"][0]
    s = ret["summary"]
    assert s["3.1a"]["igst"] == "3240.00" and s["3.1a"]["taxable"] == "18000.00"
    assert s["4a"] == {"igst": "0.00", "cgst": "900.00", "sgst": "900.00"}                   # only the matched Shakti bill
    assert s["4a_pending_in_2b"] == {"igst": "2520.00", "cgst": "900.00", "sgst": "900.00"} and s["provisional"]       # Vega (not matched) and the September bill
    assert s["utilisation"]["cash"] == {"igst": "1440.00", "cgst": "0.00", "sgst": "0.00"} and s["utilisation"]["left"] == {"igst": "0.00", "cgst": "0.00", "sgst": "0.00"}
    assert ret["itc_claimed"] == "1800.00" and ret["cash_paid"] == "1440.00" and ret["output_tax"] == "3240.00"      # CGST/SGST credit pays IGST
    S["gstr3b"] = ret
    # not approved yet
    nope = client.post(url(f"/gst-returns/{ret['id']}/file"), headers={**h, **key()}, json={"signing": "evc_otp"})
    assert nope.status_code == 409 and nope.json()["code"] == "approval_required"
    ap = client.post(url(f"/obligations/{ob}/submit"), headers=h, json={}).json()
    assert client.post(f"/v1/entities/{EN}/approvals/{ap['id']}/approve", headers={**ha, **key()}, json={}).status_code == 200
    k = key()
    f = client.post(url(f"/gst-returns/{ret['id']}/file"), headers={**h, **k}, json={"signing": "evc_otp"})
    assert f.status_code == 202, f.text
    assert client.post(url(f"/gst-returns/{ret['id']}/file"), headers={**h, **k}, json={"signing": "evc_otp"}).json()["id"] == f.json()["id"]
    assert client.post(url(f"/gst-returns/{ret['id']}/file"), headers={**h, **key()}, json={}).json()["code"] == "conflict"          # already submitted
    assert client.post(url(f"/gst-returns/{ret['id']}/compute"), headers=h).status_code == 202                                       # queued, will be refused
    run_worker(db)
    assert job_status(db, f.json()["id"])["status"] == "waiting_for_otp"                                                              # the EVC OTP is asked for
    assert client.post(f"/v1/jobs/{f.json()['id']}/otp", headers=h, json={"otp": "123456"}).json()["status"] == "queued"
    run_worker(db)
    done = client.get(url(f"/gst-returns/{ret['id']}"), headers=h).json()
    assert done["status"] == "filed" and done["arn"].startswith("AA") and done["filed_at"]
    o = db.execute("SELECT status::text AS st, acknowledgement FROM obligations WHERE id = %s", (ob,)).fetchone()
    assert (o["st"], o["acknowledgement"]) == ("filed", done["arn"])
    names = accounts(client, h)
    j = journal_of(client, h, "gst_setoff", ret["id"])
    got = {names[l["account_id"]]: (l["debit"], l["credit"]) for l in j["lines"]}
    assert got == {"Output IGST": ("3240.00", "0.00"), "Input CGST": ("0.00", "900.00"), "Input SGST": ("0.00", "900.00"), "HDFC current": ("0.00", "1440.00")}
    assert db.execute("SELECT itc_claim_period FROM purchase_bills WHERE id = %s", (S["b1"]["id"],)).fetchone()["itc_claim_period"] == "2026-10"
    assert db.execute("SELECT status FROM jobs WHERE kind = 'gst.compute_return' AND subject_id = %s", (ret["id"],)).fetchone()["status"] in ("queued", "failed")


# ================================================================== TDS and advance tax
def test_tds_deposit_challan(client, h, db):
    from app import taxcalc
    ob = S["ob"][("TDS.DEPOSIT.MONTHLY", "Oct 2026")]
    r = client.post(url(f"/obligations/{ob}/prepare"), headers=h)
    assert r.status_code == 202
    run_worker(db)
    res = job_status(db, r.json()["id"])
    assert res["status"] == "succeeded" and res["result"]["amount"] == "1100.00" and res["result"]["deductions"] == 2        # 100 (194C, PAN) + 1 000 (194C, no PAN)
    due = dt.date(2026, 11, 7)
    assert D(res["result"]["interest"]) == taxcalc.tds_late_interest(D("1100"), due, dt.date.today())
    ch = client.get(url("/challans"), headers=h, params={"kind": "itns281_tds"}).json()["data"][0]
    assert ch["status"] == "scheduled" and ch["amount"] == "1100.00" and ch["obligation_id"] == str(ob)
    S["challan"] = ch
    ded = client.get(url("/tds-deductions"), headers=h, params={"unpaid": "false", "section": "194C"}).json()["data"]
    assert sorted(d["tds_amount"] for d in ded) == ["100.00", "1000.00"] and all(d["challan_id"] == ch["id"] for d in ded)
    assert client.get(url("/tds-deductions"), headers=h, params={"unpaid": "true"}).json()["data"] == []
    assert len(client.get(url("/tds-deductions"), headers=h, params={"fy": "2026-27", "quarter": 3}).json()["data"]) == 2
    assert client.get(url("/tds-deductions"), headers=h, params={"quarter": 2}).json()["data"] == []
    again = client.post(url(f"/obligations/{ob}/prepare"), headers=h)                    # preparing twice must not duplicate the challan
    run_worker(db)
    assert len(client.get(url("/challans"), headers=h, params={"kind": "itns281_tds"}).json()["data"]) == 1


def test_pay_challan_files_the_obligation(client, h, hv, db):
    ch = S["challan"]
    assert client.post(url(f"/challans/{ch['id']}/pay"), headers={**hv, **key()}).json()["code"] == "permission_denied"
    k = key()
    r = client.post(url(f"/challans/{ch['id']}/pay"), headers={**h, **k})
    assert r.status_code == 202 and client.post(url(f"/challans/{ch['id']}/pay"), headers={**h, **k}).json()["id"] == r.json()["id"]
    run_worker(db)
    assert job_status(db, r.json()["id"])["status"] == "succeeded"
    paid = next(c for c in client.get(url("/challans"), headers=h, params={"status": "paid"}).json()["data"] if c["id"] == ch["id"])
    assert paid["challan_ref"].startswith("CIN") and paid["paid_on"]
    o = db.execute("SELECT status::text AS st, acknowledgement FROM obligations WHERE id = %s", (ch["obligation_id"],)).fetchone()
    assert o["st"] == "filed" and o["acknowledgement"] == paid["challan_ref"]
    assert client.post(url(f"/challans/{ch['id']}/pay"), headers={**h, **key()}).json()["code"] == "conflict"
    j = journal_of(client, h, "tds", ch["id"])
    names = accounts(client, h)
    assert {names[l["account_id"]]: l["debit"] != "0.00" for l in j["lines"]}["TDS payable"] is True
    assert sum(D(l["credit"]) for l in j["lines"]) == D("1100.00") + D(ch["interest"])


def test_tds_return_prepare_and_validate(client, h, db):
    ob = S["ob"][("TDS.RETURN.QUARTERLY", "Q3 FY26-27")]
    r = client.post(url(f"/obligations/{ob}/prepare"), headers=h)
    run_worker(db)
    out = job_status(db, r.json()["id"])["result"]["returns"]
    assert {x["form"]: x["deductions"] for x in out} == {"24Q": 0, "26Q": 2}
    rets = {x["form"]: x for x in client.get(url("/tds-returns"), headers=h, params={"fy": "2026-27"}).json()["data"]}
    assert rets["26Q"]["quarter"] == 3 and rets["26Q"]["status"] == "drafting"
    v26 = client.post(url(f"/tds-returns/{rets['26Q']['id']}/validate"), headers=h)
    v24 = client.post(url(f"/tds-returns/{rets['24Q']['id']}/validate"), headers=h)
    run_worker(db)
    assert job_status(db, v26.json()["id"])["result"]["valid"] is True
    bad = job_status(db, v24.json()["id"])["result"]
    assert bad["valid"] is False and "no deductions" in bad["errors"][0]
    after = {x["form"]: x for x in client.get(url("/tds-returns"), headers=h).json()["data"]}
    assert after["26Q"]["status"] == "ready" and after["26Q"]["token_no"].startswith("TKN") and after["24Q"]["status"] == "drafting"
    assert db.execute("SELECT status::text AS st FROM obligations WHERE id = %s", (ob,)).fetchone()["st"] == "ready_to_file"
    assert client.post(url(f"/tds-returns/{uuid.uuid4()}/validate"), headers=h).status_code == 404


def test_advance_tax(client, h, db):
    plan = client.get(url("/advance-tax"), headers=h, params={"fy": "2026-27"}).json()
    assert [(p["instalment_no"], p["due_date"], p["estimated_tax"], p["paid"]) for p in plan] == [
        (1, "2026-06-15", "18000.00", "0.00"), (2, "2026-09-15", "54000.00", "0.00"), (3, "2026-12-15", "90000.00", "0.00"), (4, "2027-03-15", "120000.00", "0.00")]
    ob = S["ob"][("IT.ADVANCE_TAX", "Q3 FY26-27")]
    ch = client.get(url("/challans"), headers=h, params={"kind": "itns280_income_tax"}).json()["data"]
    assert len(ch) == 1 and ch[0]["amount"] == "90000.00" and ch[0]["obligation_id"] == str(ob)                     # queued earlier, processed with the GSTR-1 run
    r = client.post(url(f"/challans/{ch[0]['id']}/pay"), headers={**h, **key()})
    run_worker(db)
    assert job_status(db, r.json()["id"])["status"] == "succeeded"
    plan = client.get(url("/advance-tax"), headers=h, params={"fy": "2026-27"}).json()
    assert plan[2]["paid"] == "90000.00" and plan[2]["challan_id"] == ch[0]["id"] and plan[0]["paid"] == "0.00"
    assert client.get(url("/advance-tax"), headers=h, params={"fy": "bad"}).status_code == 400


def test_receivables_and_ledger_balances_endpoints(client, h, db):
    db.execute("INSERT INTO tds_receivables (entity_id, party_id, section, amount, fy, quarter) VALUES (%s, %s, '194Q', 250, '2026-27', 3)", (EN, BUYER))
    rows = client.get(url("/tds-receivables"), headers=h, params={"unmatched": "true"}).json()["data"]
    assert len(rows) == 1 and rows[0]["section"] == "194Q" and rows[0]["amount"] == "250.00"
    assert client.get(url("/tds-receivables"), headers=h, params={"unmatched": "false"}).json()["data"] == []
    db.execute("INSERT INTO gst_ledger_balances (registration_id, ledger, head, as_of, balance) VALUES (%s, 'cash', 'igst', '2026-10-01', 10), (%s, 'cash', 'igst', '2026-11-01', 25)", (REG, REG))
    bal = client.get(url("/gst/ledger-balances"), headers=h, params={"registration_id": REG}).json()
    assert [(b["ledger"], b["head"], b["balance"], b["as_of"]) for b in bal] == [("cash", "igst", "25.00", "2026-11-01")]                 # latest only
    assert client.get(url("/gst/ledger-balances"), headers=h, params={"registration_id": uuid.uuid4()}).json() == []


def test_unpreparable_obligations_and_visibility(client, h, db):
    other = db.execute("""SELECT o.id FROM obligations o JOIN compliance_rules r ON r.id = o.rule_id WHERE o.entity_id = %s AND r.code = 'MCA.AOC4' LIMIT 1""", (EN,)).fetchone()
    if other:
        assert client.post(url(f"/obligations/{other['id']}/prepare"), headers=h).json()["code"] == "conflict"
    stranger = login(client, "stranger@example.in")
    assert client.get(url("/purchase-bills"), headers=stranger).status_code == 404
    assert client.get(url("/gst-returns"), headers=stranger).status_code == 404
    assert client.post(url(f"/obligations/{uuid.uuid4()}/prepare"), headers=h).status_code == 404


def test_invariants(db):
    assert db.execute("SELECT app.audit_verify() AS v").fetchone()["v"] is None
    bad = db.execute("""SELECT e.entry_no FROM journal_entries e JOIN journal_lines l ON l.entry_id = e.id WHERE e.entity_id = %s AND e.status <> 'draft'
                        GROUP BY e.id, e.entry_no HAVING sum(l.debit) <> sum(l.credit)""", (EN,)).fetchall()
    assert bad == []
    assert db.execute("SELECT count(*) AS n FROM purchase_bills WHERE entity_id = %s AND status IN ('booked', 'paid') AND journal_entry_id IS NULL", (EN,)).fetchone()["n"] == 0
    assert db.execute("SELECT count(*) AS n FROM jobs WHERE entity_id = %s AND status = 'failed'", (EN,)).fetchone()["n"] <= 1       # only the deliberately refused re-compute
