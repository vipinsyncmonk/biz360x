"""M2 end to end: ledger posting, quotes -> orders -> invoices, credit notes, payment links and the public page,
bank matching, month-end close and period lock, reports, dunning and recurring invoices.

Uses its own entity (EM), and runs in order: each test builds on the state the previous one left.
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
EM = "00000000-0000-0000-0000-0000000000b1"
OWNER_ID, VIEWER_ID = "00000000-0000-0000-0000-0000000000b2", "00000000-0000-0000-0000-0000000000b3"
REG = "00000000-0000-0000-0000-0000000000b4"
CUST_IGST, CUST_CGST = "00000000-0000-0000-0000-0000000000b5", "00000000-0000-0000-0000-0000000000b6"
GST_IGST, GST_CGST = "00000000-0000-0000-0000-0000000000b7", "00000000-0000-0000-0000-0000000000b8"
BANK_LEDGER, BANK = "00000000-0000-0000-0000-0000000000b9", "00000000-0000-0000-0000-0000000000ba"
OWNER, VIEWER = "m2-owner@example.in", "m2-viewer@example.in"
S: dict = {}                        # state handed from test to test


def item(qty="10", rate="1000.00", gst=18, desc="Pressure transmitter"):
    return {"description": desc, "hsn_sac": "902620", "qty": qty, "rate": rate, "gst_rate": gst}


def draft(party, gst_id, lines, date="2026-10-05"):
    return {"doc_type": "tax_invoice", "doc_date": date, "registration_id": REG, "party_id": party, "party_gst_id": gst_id, "lines": lines}


@pytest.fixture(scope="module")
def db():
    with psycopg.connect(DB, autocommit=True, row_factory=dict_row) as c:
        yield c


@pytest.fixture(scope="module", autouse=True)
def entity(db):
    db.execute("INSERT INTO entities (id, organization_id, legal_name, constitution, track) VALUES (%s, %s, 'M2 Test Pvt Ltd', 'private_limited', 'business')", (EM, ORG))
    db.execute("INSERT INTO entity_registrations (id, entity_id, kind, number, state_code, gst_filing_frequency) VALUES (%s, %s, 'gstin', '29AAACM2222B1Z5', '29', 'monthly')", (REG, EM))
    db.execute("""INSERT INTO document_series (entity_id, registration_id, doc_type, fy, prefix) VALUES
                  (%s, %s, 'tax_invoice', '2026-27', 'M2/KA/'), (%s, %s, 'credit_note', '2026-27', 'M2/CN/')""", (EM, REG, EM, REG))
    db.execute("INSERT INTO users (id, email, full_name) VALUES (%s, %s, 'M2 Owner'), (%s, %s, 'M2 Viewer')", (OWNER_ID, OWNER, VIEWER_ID, VIEWER))
    db.execute("INSERT INTO memberships (entity_id, user_id, role) VALUES (%s, %s, 'owner'), (%s, %s, 'viewer')", (EM, OWNER_ID, EM, VIEWER_ID))
    db.execute("INSERT INTO parties (id, entity_id, legal_name, is_customer, payment_terms_days) VALUES (%s, %s, 'Coastal Grid Utilities', true, 30), (%s, %s, 'Bengaluru Motors', true, 30)",
               (CUST_IGST, EM, CUST_CGST, EM))
    db.execute("INSERT INTO party_gst_registrations (id, party_id, gstin, state_code, is_default) VALUES (%s, %s, '32AABCM9999K1ZP', '32', true), (%s, %s, '29AABCB7777L1ZQ', '29', true)",
               (GST_IGST, CUST_IGST, GST_CGST, CUST_CGST))
    db.execute("INSERT INTO ledger_accounts (id, entity_id, code, name, type, subtype) VALUES (%s, %s, '1100', 'HDFC current', 'asset', 'bank')", (BANK_LEDGER, EM))
    db.execute("INSERT INTO bank_accounts (id, entity_id, ledger_account_id, bank_name, account_last4, ifsc, kind) VALUES (%s, %s, %s, 'HDFC', '4412', 'HDFC0000123', 'current')", (BANK, EM, BANK_LEDGER))
    yield
    db.execute("UPDATE jobs SET status = 'cancelled' WHERE status = 'queued' AND kind LIKE 'collections.%'")
    db.execute("UPDATE jobs SET status = 'cancelled' WHERE status = 'queued' AND kind = 'compliance.run_engine'")


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
def h(client):
    return login(client, OWNER)


@pytest.fixture(scope="module")
def hv(client):
    return login(client, VIEWER)


def url(path):
    return f"/v1/entities/{EM}{path}"


def key():
    return {"Idempotency-Key": str(uuid.uuid4())}


def issue(client, h, doc_id):
    r = client.post(url(f"/sales-documents/{doc_id}/issue"), headers={**h, **key()}, json={})
    assert r.status_code == 202, r.text
    return client.get(url(f"/sales-documents/{doc_id}"), headers=h).json()


def new_invoice(client, h, party=CUST_IGST, gst=GST_IGST, lines=None, date="2026-10-05"):
    r = client.post(url("/sales-documents"), headers=h, json=draft(party, gst, lines or [item()], date))
    assert r.status_code == 201, r.text
    return issue(client, h, r.json()["id"])


def journal_for(client, h, source, source_id):
    js = client.get(url("/journals"), headers=h, params={"source": source}).json()["data"]
    return next(j for j in js if j["source_id"] == source_id)


def totals(j):
    return sum(D(l["debit"]) for l in j["lines"]), sum(D(l["credit"]) for l in j["lines"])


# ================================================================== ledger posting
def test_issue_posts_a_balanced_journal(client, h):
    inv = new_invoice(client, h)
    S["inv"] = inv
    assert inv["grand_total"] == "11800.00" and inv["number"] == "M2/KA/001" and inv["einvoice"]["status"] == "not_required"
    j = journal_for(client, h, "sales_document", inv["id"])
    assert j["status"] == "posted" and j["entry_no"].startswith("JE-") and totals(j) == (D("11800.00"), D("11800.00"))
    names = {a["id"]: a["name"] for a in client.get(url("/accounts"), headers=h).json()["data"]}
    assert {names[l["account_id"]]: (l["debit"], l["credit"]) for l in j["lines"]} == {
        "Trade receivables": ("11800.00", "0.00"), "Sales": ("0.00", "10000.00"), "Output IGST": ("0.00", "1800.00")}


def test_intra_state_invoice_posts_cgst_sgst_and_round_off(client, h):
    inv = new_invoice(client, h, CUST_CGST, GST_CGST, [item("3", "333.33")])
    S["inv2"] = inv
    assert inv["grand_total"] == "1180.00" and inv["cgst_total"] == "90.00" == inv["sgst_total"]
    j = journal_for(client, h, "sales_document", inv["id"])
    names = {a["id"]: a["name"] for a in client.get(url("/accounts"), headers=h).json()["data"]}
    got = {names[l["account_id"]]: (l["debit"], l["credit"]) for l in j["lines"]}
    assert got["Output CGST"] == ("0.00", "90.00") == got["Output SGST"] and got["Round off"] == ("0.00", "0.01")
    assert totals(j)[0] == totals(j)[1] == D("1180.00")


def test_receipt_posts_bank_receivable_and_advance(client, h):
    r = client.post(url("/receipts"), headers={**h, **key()}, json={
        "party_id": CUST_IGST, "received_on": "2026-10-12", "amount": "5000.00", "method": "neft", "utr": "UTR1",
        "allocations": [{"invoice_id": S["inv"]["id"], "amount": "3000.00"}]})
    assert r.status_code == 201 and r.json()["unallocated"] == "2000.00"
    j = journal_for(client, h, "receipt", r.json()["id"])
    names = {a["id"]: a["name"] for a in client.get(url("/accounts"), headers=h).json()["data"]}
    assert {names[l["account_id"]]: (l["debit"], l["credit"]) for l in j["lines"]} == {
        "HDFC current": ("5000.00", "0.00"), "Trade receivables": ("0.00", "3000.00"), "Customer advances": ("0.00", "2000.00")}
    assert client.get(url(f"/sales-documents/{S['inv']['id']}"), headers=h).json()["balance_due"] == "8800.00"


def test_manual_journal_lifecycle_and_guards(client, h):
    acc = {a["code"]: a["id"] for a in client.get(url("/accounts"), headers=h).json()["data"]}
    bad = client.post(url("/journals"), headers=h, json={"entry_date": "2026-11-02", "lines": [
        {"account_id": acc["1100"], "debit": "100.00"}, {"account_id": acc["4000"], "credit": "90.00"}]})
    assert bad.status_code == 201
    unbalanced = client.post(url(f"/journals/{bad.json()['id']}/post"), headers={**h, **key()})
    assert unbalanced.status_code == 409 and unbalanced.json()["code"] == "journal_unbalanced"
    assert client.delete(url(f"/journals/{bad.json()['id']}"), headers=h).status_code == 204
    assert client.post(url("/journals"), headers=h, json={"entry_date": "2026-11-02", "lines": [
        {"account_id": acc["1100"], "debit": "100.00", "credit": "5.00"}, {"account_id": acc["4000"], "credit": "100.00"}]}).json()["code"] == "validation_failed"
    ok = client.post(url("/journals"), headers=h, json={"entry_date": "2026-11-02", "narration": "Owner capital", "lines": [
        {"account_id": acc["1100"], "debit": "100.00"}, {"account_id": acc["4000"], "credit": "100.00"}]}).json()
    k = key()
    posted = client.post(url(f"/journals/{ok['id']}/post"), headers={**h, **k})
    assert posted.status_code == 200 and posted.json()["status"] == "posted"
    assert client.post(url(f"/journals/{ok['id']}/post"), headers={**h, **k}).json()["id"] == ok["id"]            # replay
    assert client.delete(url(f"/journals/{ok['id']}"), headers=h).json()["code"] == "conflict"
    rev = client.post(url(f"/journals/{ok['id']}/reverse"), headers={**h, **key()}, json={"reversal_date": "2026-11-03"})
    assert rev.status_code == 201 and rev.json()["lines"][0]["credit"] == "100.00"
    assert client.get(url(f"/journals/{ok['id']}"), headers=h).json()["status"] == "reversed"
    assert client.post(url(f"/journals/{ok['id']}/reverse"), headers={**h, **key()}, json={"reversal_date": "2026-11-03"}).json()["code"] == "conflict"


def test_account_endpoints(client, h, hv):
    r = client.post(url("/accounts"), headers=h, json={"code": "5100", "name": "Freight", "type": "expense"})
    assert r.status_code == 201 and client.get(url(f"/accounts/{r.json()['id']}"), headers=hv).json()["code"] == "5100"
    assert client.post(url("/accounts"), headers=h, json={"code": "5100", "name": "Dup", "type": "expense"}).json()["code"] == "conflict"
    assert client.post(url("/accounts"), headers=hv, json={"code": "5200", "name": "x", "type": "expense"}).json()["code"] == "permission_denied"
    assert [a["code"] for a in client.get(url("/accounts"), headers=h, params={"type": "income"}).json()["data"]] == ["4000", "4900"]


# ================================================================== credit notes
def test_credit_note_flow(client, h):
    inv = S["inv"]
    line_id = None
    r = client.post(url(f"/sales-documents/{inv['id']}/credit-notes"), headers=h, json={
        "cn_reason": "sales_return", "lines": [item("2", "1000.00", desc="Returned units")]})
    assert r.status_code == 201, r.text
    cn = r.json()
    assert cn["doc_type"] == "credit_note" and cn["number"] is None and cn["original_document_id"] == inv["id"] and cn["grand_total"] == "2360.00"
    issued = issue(client, h, cn["id"])
    assert issued["number"] == "M2/CN/001" and issued["status"] == "issued"
    assert client.get(url(f"/sales-documents/{inv['id']}"), headers=h).json()["balance_due"] == "6440.00"      # 11800 - 3000 receipt - 2360 CN
    j = journal_for(client, h, "sales_document", cn["id"])
    names = {a["id"]: a["name"] for a in client.get(url("/accounts"), headers=h).json()["data"]}
    assert {names[l["account_id"]]: (l["debit"], l["credit"]) for l in j["lines"]} == {
        "Trade receivables": ("0.00", "2360.00"), "Sales returns and credit notes": ("2000.00", "0.00"), "Output IGST": ("360.00", "0.00")}


def test_credit_note_guards(client, h):
    inv = S["inv"]
    over = client.post(url(f"/sales-documents/{inv['id']}/credit-notes"), headers=h, json={"cn_reason": "correction", "lines": [item("100", "1000.00")]})
    assert over.status_code == 400 and over.json()["code"] == "validation_failed"
    d = client.post(url("/sales-documents"), headers=h, json=draft(CUST_IGST, GST_IGST, [item()])).json()
    assert client.post(url(f"/sales-documents/{d['id']}/credit-notes"), headers=h, json={"cn_reason": "correction", "lines": [item("1")]}).json()["code"] == "conflict"
    assert client.post(url(f"/sales-documents/{uuid.uuid4()}/credit-notes"), headers=h, json={"cn_reason": "correction", "lines": [item("1")]}).status_code == 404
    commercial = client.post(url(f"/sales-documents/{inv['id']}/credit-notes"), headers=h, json={
        "cn_reason": "post_sale_discount", "gst_adjusted": False, "lines": [item("1", "500.00", desc="Discount")]}).json()
    assert commercial["grand_total"] == "500.00" and commercial["igst_total"] == "0.00"


# ================================================================== quotes -> orders -> invoices
QUOTE = {"party_id": CUST_IGST, "registration_id": REG, "title": "14 transmitters, 2 lots", "valid_until": "2026-12-31", "advance_pct": "20",
         "lines": [item("10", "1000.00", desc="Transmitter PT-400"), item("4", "500.00", desc="Calibration")]}


def test_quote_create_send_revise(client, h, hv):
    assert client.post(url("/quotes"), headers=hv, json=QUOTE).json()["code"] == "permission_denied"
    q = client.post(url("/quotes"), headers=h, json=QUOTE)
    assert q.status_code == 201, q.text
    q = q.json()
    assert q["quote_no"] == "Q-0001" and q["version"] == 1 and q["status"] == "draft" and q["taxable_total"] == "12000.00"
    assert q["grand_total"] == "14160.00" and q["tax_total"] == "2160.00" and len(q["lines"]) == 2 and q["invoiced_pct"] == 0
    sent = client.post(url(f"/quotes/{q['id']}/send"), headers=h, json={"channels": ["email"], "to": ["a@b.in"]}).json()
    assert sent["status"] == "sent"
    v2 = client.post(url(f"/quotes/{q['id']}/revise"), headers=h)
    assert v2.status_code == 201 and v2.json()["version"] == 2 and v2.json()["quote_no"] == "Q-0001" and v2.json()["status"] == "draft"
    assert client.get(url(f"/quotes/{q['id']}"), headers=h).json()["status"] == "negotiating"
    assert client.post(url(f"/quotes/{q['id']}/revise"), headers=h).json()["code"] == "conflict"            # only the latest version can be revised
    S["q2"] = client.post(url(f"/quotes/{v2.json()['id']}/send"), headers=h, json={}).json()


def test_accept_opens_a_sales_order_idempotently(client, h):
    q, k = S["q2"], key()
    r = client.post(url(f"/quotes/{q['id']}/accept"), headers={**h, **k}, json={"customer_po_ref": "PO-77"})
    assert r.status_code == 201, r.text
    so = r.json()
    assert so["order_no"] == "SO-0001" and so["quote_id"] == q["id"] and so["status"] == "open" and so["customer_po_ref"] == "PO-77"
    assert [(l["ordered_qty"], l["invoiced_qty"]) for l in so["lines"]] == [("10.00", "0.00"), ("4.00", "0.00")]
    assert client.post(url(f"/quotes/{q['id']}/accept"), headers={**h, **k}, json={"customer_po_ref": "PO-77"}).json()["id"] == so["id"]
    assert client.post(url(f"/quotes/{q['id']}/accept"), headers={**h, **key()}, json={}).json()["code"] == "conflict"
    assert client.post(url(f"/quotes/{q['id']}/accept"), headers=h, json={}).json()["code"] == "validation_failed"        # key is mandatory
    S["so"] = so


def test_invoice_by_lot(client, h):
    so = S["so"]
    first = so["lines"][0]["id"]
    over = client.post(url(f"/sales-orders/{so['id']}/invoice"), headers=h, json={"lines": [{"so_line_id": first, "qty": "11"}]})
    assert over.status_code == 400
    assert client.post(url(f"/sales-orders/{so['id']}/invoice"), headers=h, json={"lines": [{"so_line_id": 999999, "qty": "1"}]}).status_code == 400
    lot1 = client.post(url(f"/sales-orders/{so['id']}/invoice"), headers=h, json={"lines": [{"so_line_id": first, "qty": "6"}], "doc_date": "2026-10-20"})
    assert lot1.status_code == 201 and lot1.json()["status"] == "draft" and lot1.json()["taxable_total"] == "6000.00"
    cur = client.get(url(f"/sales-orders/{so['id']}"), headers=h).json()
    assert cur["status"] == "partially_invoiced" and cur["lines"][0]["invoiced_qty"] == "6.00"
    assert client.get(url(f"/quotes/{S['q2']['id']}"), headers=h).json()["invoiced_pct"] == 42.9                  # 6 of 14 units
    lot2 = client.post(url(f"/sales-orders/{so['id']}/invoice"), headers=h, json={"doc_date": "2026-10-25"})
    assert lot2.status_code == 201 and lot2.json()["taxable_total"] == "6000.00"                                  # 4 x 1000 + 4 x 500
    assert client.get(url(f"/sales-orders/{so['id']}"), headers=h).json()["status"] == "invoiced"
    assert client.post(url(f"/sales-orders/{so['id']}/invoice"), headers=h, json={}).json()["code"] == "conflict"
    assert client.get(url(f"/quotes/{S['q2']['id']}"), headers=h).json()["invoiced_pct"] == 100.0
    assert client.get(url("/sales-orders"), headers=h, params={"status": "invoiced"}).json()["data"][0]["id"] == so["id"]
    issued = issue(client, h, lot1.json()["id"])
    assert issued["number"] == "M2/KA/003" and issued["status"] == "issued"      # after M2/KA/001 and the intra-state invoice (002)


def test_quote_lost_and_listing(client, h):
    q = client.post(url("/quotes"), headers=h, json={**QUOTE, "title": "Lost deal"}).json()
    lost = client.post(url(f"/quotes/{q['id']}/mark-lost"), headers=h, json={"reason": "Price"}).json()
    assert lost["status"] == "lost"
    assert client.post(url(f"/quotes/{q['id']}/send"), headers=h, json={}).json()["code"] == "conflict"
    listing = client.get(url("/quotes"), headers=h, params={"status": "lost"}).json()
    assert [x["id"] for x in listing["data"]] == [q["id"]] and listing["next_cursor"] is None
    page1 = client.get(url("/quotes"), headers=h, params={"limit": 1}).json()
    assert len(page1["data"]) == 1 and page1["next_cursor"] == "1"
    assert client.get(url("/quotes"), headers=h, params={"limit": 1, "cursor": "x"}).status_code == 400


# ================================================================== payment links and the public page
def test_payment_link_and_public_page(client, h):
    inv, k = S["inv"], key()
    r = client.post(url(f"/sales-documents/{inv['id']}/payment-links"), headers={**h, **k}, json={})
    assert r.status_code == 201, r.text
    link = r.json()
    assert link["amount"] == "6440.00" and link["status"] == "active" and link["url"].endswith("/" + link["short_code"])
    assert link["virtual_account"]["ifsc"] == "HDFC0000123" and link["virtual_account"]["va_number"].startswith("BZX")
    assert client.post(url(f"/sales-documents/{inv['id']}/payment-links"), headers={**h, **k}, json={}).json()["id"] == link["id"]
    assert client.post(url(f"/sales-documents/{inv['id']}/payment-links"), headers={**h, **key()}, json={"amount": "99999.00"}).status_code == 400
    S["link"] = link

    page = client.get(f"/v1/pay/{link['short_code']}")                  # no Authorization header
    assert page.status_code == 200, page.text
    p = page.json()
    assert (p["supplier_name"], p["supplier_gstin"], p["number"], p["amount_due"], p["status"]) == ("M2 Test Pvt Ltd", "29AAACM2222B1Z5", "M2/KA/001", "6440.00", "due")
    assert p["virtual_account"]["va_number"] == link["virtual_account"]["va_number"] and p["taxable_total"] == "10000.00" and p["tax_total"] == "1800.00"
    assert "id" not in p and "party_id" not in p                          # only what is printed on the invoice
    assert client.get(f"/v1/pay/nope").status_code == 404

    bt = client.post(f"/v1/pay/{link['short_code']}/intents", json={"method": "bank_transfer"})
    assert bt.status_code == 201 and link["virtual_account"]["va_number"] in bt.json()["instructions"] and bt.json()["reference"].startswith("PI-")
    assert "adapter" in client.post(f"/v1/pay/{link['short_code']}/intents", json={"method": "upi"}).json()["instructions"]
    assert client.post(f"/v1/pay/{link['short_code']}/intents", json={"method": "card"}).json()["code"] == "conflict"       # not enabled on this link
    assert client.post(f"/v1/pay/{link['short_code']}/intents", json={"method": "cheque"}).status_code == 400

    q = client.post(f"/v1/pay/{link['short_code']}/queries", json={"email": "ap@coastal.in", "message": "Wrong PO number"})
    assert q.status_code == 204


def test_public_page_is_rate_limited(client):
    from app import routes_orders
    routes_orders._hits.clear()
    codes = [client.get("/v1/pay/nope").status_code for _ in range(31)]
    assert codes[:30] == [404] * 30 and codes[30] == 429
    routes_orders._hits.clear()


def test_query_became_a_task_and_view_was_recorded(db):
    t = db.execute("SELECT title FROM tasks WHERE entity_id = %s AND title LIKE 'Invoice query%%'", (EM,)).fetchone()
    assert "M2/KA/001" in t["title"] and "ap@coastal.in" in t["title"]
    assert db.execute("SELECT first_viewed_at FROM payment_links WHERE short_code = %s", (S["link"]["short_code"],)).fetchone()["first_viewed_at"] is not None


# ================================================================== bank matching
def feed(lines):
    return {"transactions": lines}


def test_bank_feed_import_and_suggestions(client, h):
    va = S["link"]["virtual_account"]["va_number"]
    lines = [
        {"external_id": "T1", "txn_date": "2026-10-20", "amount": "6440.00", "description": "NEFT-HDFC-COASTAL GRID", "utr": "N1", "counterparty_va": va},
        {"external_id": "T2", "txn_date": "2026-10-21", "amount": "1180.00", "description": "RTGS BENGALURU MOTORS PAYMENT FOR M2/KA/002", "utr": "R1"},
        {"external_id": "T3", "txn_date": "2026-10-22", "amount": "-500.00", "description": "SERVICE CHARGES"},
    ]
    r = client.post(url(f"/bank-accounts/{BANK}/transactions"), headers=h, json=feed(lines))
    assert r.status_code == 201 and r.json() == {"imported": 3, "duplicates": 0, "suggested": 2}
    again = client.post(url(f"/bank-accounts/{BANK}/transactions"), headers=h, json=feed(lines)).json()
    assert again == {"imported": 0, "duplicates": 3, "suggested": 0}
    tx = {t["description"][:4]: t for t in client.get(url("/bank-transactions"), headers=h).json()["data"]}
    assert tx["NEFT"]["match_status"] == "suggested" and tx["RTGS"]["match_status"] == "suggested" and tx["SERV"]["match_status"] == "unmatched"
    s_va, s_num = tx["NEFT"]["suggestions"][0], tx["RTGS"]["suggestions"][0]
    assert (s_va["rule"], s_va["confidence"], s_va["amount"]) == ("virtual_account", 1.0, "6440.00")
    assert (s_num["rule"], s_num["confidence"]) == ("invoice_number", 0.95) and s_num["label"].startswith("Receipt RCPT-")
    S.update(tx_va=tx["NEFT"], tx_num=tx["RTGS"], tx_fee=tx["SERV"])
    bank = client.get(url(f"/bank-accounts/{BANK}"), headers=h).json()
    assert bank["balance"] == "7120.00" and bank["bank_name"] == "HDFC"
    assert client.post(url(f"/bank-accounts/{uuid.uuid4()}/transactions"), headers=h, json=feed(lines)).status_code == 404


def test_accept_settles_the_invoice_and_posts(client, h):
    m = S["tx_va"]["suggestions"][0]
    k = key()
    r = client.post(url(f"/bank-matches/{m['id']}/accept"), headers={**h, **k})
    assert r.status_code == 200 and r.json()["match_status"] == "matched" and r.json()["suggestions"][0]["status"] == "accepted"
    assert client.post(url(f"/bank-matches/{m['id']}/accept"), headers={**h, **k}).json()["match_status"] == "matched"      # replay
    assert client.post(url(f"/bank-matches/{m['id']}/accept"), headers={**h, **key()}).json()["code"] == "conflict"
    inv = client.get(url(f"/sales-documents/{S['inv']['id']}"), headers=h).json()
    assert inv["balance_due"] == "0.00" and inv["payment_status"] == "paid"
    assert client.get(f"/v1/pay/{S['link']['short_code']}").json()["status"] == "paid"
    assert client.post(url(f"/sales-documents/{S['inv']['id']}/payment-links"), headers={**h, **key()}, json={}).json()["code"] == "conflict"


def test_reject_and_manual_match(client, h, db):
    m = S["tx_num"]["suggestions"][0]
    r = client.post(url(f"/bank-matches/{m['id']}/reject"), headers=h)
    assert r.status_code == 200 and r.json()["match_status"] == "unmatched"
    assert db.execute("SELECT status FROM receipts WHERE id = %s", (m["target_id"],)).fetchone()["status"] == "reversed"
    assert client.post(url(f"/bank-matches/{m['id']}/reject"), headers=h).json()["code"] == "conflict"
    rec = client.post(url("/receipts"), headers={**h, **key()}, json={"party_id": CUST_CGST, "received_on": "2026-10-21", "amount": "1180.00", "method": "neft",
                      "allocations": [{"invoice_id": S["inv2"]["id"], "amount": "1180.00"}]}).json()
    wrong = client.post(url(f"/bank-transactions/{S['tx_num']['id']}/match"), headers=h, json={"targets": [{"target_kind": "receipt", "target_id": rec["id"], "amount": "1000.00"}]})
    assert wrong.status_code == 400
    ok = client.post(url(f"/bank-transactions/{S['tx_num']['id']}/match"), headers=h, json={"targets": [{"target_kind": "receipt", "target_id": rec["id"], "amount": "1180.00"}]})
    assert ok.status_code == 200 and ok.json()["match_status"] == "matched"
    assert client.post(url(f"/bank-transactions/{S['tx_num']['id']}/match"), headers=h, json={"targets": [{"target_kind": "receipt", "target_id": rec["id"], "amount": "1180.00"}]}).json()["code"] == "conflict"
    acc = {a["code"]: a["id"] for a in client.get(url("/accounts"), headers=h).json()["data"]}
    fee = client.post(url("/journals"), headers=h, json={"entry_date": "2026-10-22", "narration": "Bank charges", "lines": [
        {"account_id": acc["5100"], "debit": "500.00"}, {"account_id": acc["1100"], "credit": "500.00"}]}).json()
    client.post(url(f"/journals/{fee['id']}/post"), headers={**h, **key()})
    ok = client.post(url(f"/bank-transactions/{S['tx_fee']['id']}/match"), headers=h, json={"targets": [{"target_kind": "journal_entry", "target_id": fee["id"], "amount": "500.00"}]})
    assert ok.json()["match_status"] == "matched"
    assert client.get(url("/bank-transactions"), headers=h, params={"match_status": "unmatched"}).json()["data"] == []


# ================================================================== close, lock, reports
def october(client, h):
    return next(p for p in client.get(url("/periods"), headers=h, params={"fy": "2026-27"}).json() if p["period_start"] == "2026-10-01")


def test_periods_and_close_tasks(client, h, hv):
    ps = client.get(url("/periods"), headers=h, params={"fy": "2026-27"}).json()
    assert len(ps) == 12 and ps[0]["period_start"] == "2026-04-01" and ps[-1]["period_end"] == "2027-03-31" and {p["status"] for p in ps} == {"open"}
    assert client.get(url("/periods"), headers=h, params={"fy": "bad"}).status_code == 400
    oct_ = october(client, h)
    tasks = client.get(url(f"/periods/{oct_['id']}/close-tasks"), headers=h).json()
    by = {t["task_key"]: t for t in tasks}
    assert len(tasks) == 10 and by["bank_feeds"]["done_at"] and by["sales_posted"]["done_at"] and by["bank_recon"]["done_at"]     # computed from the books
    assert by["cfo_review"]["done_at"] is None
    assert client.patch(url(f"/periods/{oct_['id']}/close-tasks/{by['bank_feeds']['id']}"), headers=h, json={"done": False}).json()["code"] == "conflict"
    assert client.patch(url(f"/periods/{oct_['id']}/close-tasks/{by['cfo_review']['id']}"), headers=hv, json={"done": True}).json()["code"] == "permission_denied"
    S["oct"], S["tasks"] = oct_, by


def test_lock_needs_every_task(client, h, hv):
    oct_ = S["oct"]
    blocked = client.post(url(f"/periods/{oct_['id']}/lock"), headers={**h, **key()})
    assert blocked.status_code == 409 and "7 close task(s) open" in blocked.json()["detail"]
    for key_ in ("purchases_posted", "payroll_journal", "depreciation", "prepaids", "gst_recon", "branch_transfers", "cfo_review"):
        r = client.patch(url(f"/periods/{oct_['id']}/close-tasks/{S['tasks'][key_]['id']}"), headers=h, json={"done": True})
        assert r.status_code == 200 and r.json()["done_at"]
    assert client.post(url(f"/periods/{oct_['id']}/lock"), headers={**hv, **key()}).json()["code"] == "permission_denied"
    k = key()
    locked = client.post(url(f"/periods/{oct_['id']}/lock"), headers={**h, **k})
    assert locked.status_code == 200 and locked.json()["status"] == "locked" and locked.json()["close_progress"] == {"done": 10, "total": 10}
    assert client.post(url(f"/periods/{oct_['id']}/lock"), headers={**h, **k}).json()["status"] == "locked"          # replay
    assert client.post(url(f"/periods/{oct_['id']}/lock"), headers={**h, **key()}).json()["code"] == "conflict"
    assert client.patch(url(f"/periods/{oct_['id']}/close-tasks/{S['tasks']['cfo_review']['id']}"), headers=h, json={"done": False}).json()["code"] == "period_locked"


def test_locked_period_rejects_new_postings(client, h):
    d = client.post(url("/sales-documents"), headers=h, json=draft(CUST_IGST, GST_IGST, [item()], "2026-10-28")).json()
    r = client.post(url(f"/sales-documents/{d['id']}/issue"), headers={**h, **key()}, json={})
    assert r.status_code == 409 and r.json()["code"] == "period_locked"
    assert client.get(url(f"/sales-documents/{d['id']}"), headers=h).json()["status"] == "draft"                   # nothing half-done
    nov = client.post(url("/sales-documents"), headers=h, json=draft(CUST_IGST, GST_IGST, [item()], "2026-11-02")).json()
    assert client.post(url(f"/sales-documents/{nov['id']}/issue"), headers={**h, **key()}, json={}).status_code == 202   # next month is open


def test_reports(client, h):
    pl = client.get(url("/reports/profit-and-loss"), headers=h, params={"from": "2026-04-01", "to": "2026-11-30"}).json()
    assert pl["columns"] == ["2026-10", "2026-11"] and pl["provisional"] is True
    rows = {r["line"]: r["values"] for r in pl["rows"]}
    assert rows["4000 Sales"] == ["16999.99", "10000.00"] and rows["Total income"] == ["17000.00", "8000.00"]      # Oct: two invoices and a lot (+0.01 round-off); Nov: the credit note (2 000) comes off
    assert rows["Net profit"] == [f"{D(a) - D(b):.2f}" for a, b in zip(rows["Total income"], rows["Total expenses"])]
    tb = client.get(url("/reports/trial-balance"), headers=h, params={"to": "2026-12-31"}).json()
    total = tb["rows"][-1]
    assert total["line"] == "Total" and total["values"][0] == total["values"][1] and D(total["values"][0]) > 0
    bs = client.get(url("/reports/balance-sheet"), headers=h, params={"to": "2026-12-31"}).json()
    r = {x["line"]: D(x["values"][0]) for x in bs["rows"]}
    assert r["Total assets"] == r["Total liabilities and equity"]
    gl = client.get(url("/reports/gst-liability"), headers=h, params={"from": "2026-04-01", "to": "2026-12-31"}).json()
    assert {x["line"].split(" ", 1)[1] for x in gl["rows"]} == {"Output CGST", "Output SGST", "Output IGST"}
    assert client.get(url("/reports/cash-bridge"), headers=h).status_code == 400
    assert client.get(url("/reports/profit-and-loss"), headers=h, params={"format": "pdf"}).status_code == 400
    assert client.get(url("/reports/nonsense"), headers=h).status_code == 400


# ================================================================== dunning and recurring
def test_dunning_sequence_and_run(client, h, hv, db):
    from app import receivables
    seq = client.post(url("/dunning-sequences"), headers=h, json={"name": "Standard", "is_default": True, "steps": [
        {"offset_days": 0, "action": "message", "channels": ["email", "whatsapp"]}, {"offset_days": 7, "action": "task"}]})
    assert seq.status_code == 201 and seq.json()["is_default"] and [s["offset_days"] for s in seq.json()["steps"]] == [0, 7]
    other = client.post(url("/dunning-sequences"), headers=h, json={"name": "Gentle", "is_default": True, "steps": [{"offset_days": 3, "action": "message"}]}).json()
    assert not client.get(url(f"/dunning-sequences/{seq.json()['id']}"), headers=h).json()["is_default"]          # only one default
    patched = client.patch(url(f"/dunning-sequences/{other['id']}"), headers=h, json={"is_default": False}).json()
    assert patched["is_default"] is False and patched["steps"][0]["offset_days"] == 3
    assert client.patch(url(f"/dunning-sequences/{seq.json()['id']}"), headers=h, json={"is_default": True}).json()["is_default"]
    assert client.patch(url(f"/dunning-sequences/{seq.json()['id']}"), headers=h, json={"steps": []}).status_code == 400
    assert len(client.get(url("/dunning-sequences"), headers=h).json()["data"]) == 2
    assert client.post(url("/dunning-sequences"), headers=hv, json={"name": "x", "steps": [{"offset_days": 0, "action": "task"}]}).json()["code"] == "permission_denied"

    row = db.execute("SELECT id, due_date, party_id FROM sales_documents WHERE entity_id = %s AND status = 'issued' AND doc_type = 'tax_invoice' AND balance_due > 0 ORDER BY number", (EM,)).fetchone()
    with db.transaction():
        first = receivables.run_dunning(db.cursor(), EM, row["due_date"])
    assert first == {"messages_queued": 2, "tasks_created": 0}
    with db.transaction():
        assert receivables.run_dunning(db.cursor(), EM, row["due_date"]) == {"messages_queued": 0, "tasks_created": 0}          # each step runs once
        late = receivables.run_dunning(db.cursor(), EM, row["due_date"] + dt.timedelta(days=7))
    assert late["tasks_created"] >= 1
    t = db.execute("SELECT title, source FROM tasks WHERE entity_id = %s AND source = 'dunning'", (EM,)).fetchone()
    assert "Follow up" in t["title"]
    assert client.post(url(f"/parties/{row['party_id']}/pause-dunning"), headers=h, json={"until": "2027-01-01", "reason": "Dispute"}).status_code == 204
    with db.transaction():
        assert receivables.run_dunning(db.cursor(), EM, row["due_date"] + dt.timedelta(days=21)) == {"messages_queued": 0, "tasks_created": 0}
    assert client.post(url(f"/parties/{uuid.uuid4()}/pause-dunning"), headers=h, json={"until": "2027-01-01"}).status_code == 404


def test_recurring_invoices(client, h, db):
    from app import receivables
    tpl = S["inv2"]
    body = {"name": "Calibration AMC", "party_id": CUST_CGST, "registration_id": REG, "template_document_id": tpl["id"], "frequency": "monthly", "next_run_on": "2026-11-05"}
    r = client.post(url("/recurring-invoices"), headers=h, json=body)
    assert r.status_code == 201 and r.json()["auto_issue"] is False
    rid = r.json()["id"]
    assert client.post(url("/recurring-invoices"), headers=h, json={**body, "party_id": CUST_IGST}).status_code == 400            # template belongs to another customer
    assert client.post(url("/recurring-invoices"), headers=h, json={**body, "ends_on": "2026-01-01"}).status_code == 400
    with db.transaction():
        out = receivables.run_recurring(db.cursor(), EM, dt.date(2026, 12, 20))
    assert out["drafted"] == 2 and out["issued"] == 0                                    # 5 Nov and 5 Dec caught up
    nxt = client.get(url(f"/recurring-invoices/{rid}"), headers=h).json()["next_run_on"]
    assert nxt == "2027-01-05"
    with db.transaction():
        assert receivables.run_recurring(db.cursor(), EM, dt.date(2026, 12, 20))["drafted"] == 0
    drafts = db.execute("SELECT doc_date, status, grand_total FROM sales_documents WHERE entity_id = %s AND party_id = %s AND status = 'draft' ORDER BY doc_date", (EM, CUST_CGST)).fetchall()
    assert [d["doc_date"] for d in drafts] == [dt.date(2026, 11, 5), dt.date(2026, 12, 5)] and drafts[0]["grand_total"] == D("1180.00")

    auto = client.post(url("/recurring-invoices"), headers=h, json={**body, "name": "Auto", "auto_issue": True, "next_run_on": "2026-12-06", "ends_on": "2026-12-31"}).json()
    with db.transaction():
        out = receivables.run_recurring(db.cursor(), EM, dt.date(2026, 12, 20))
    assert out["drafted"] == 1 and out["issued"] == 1
    assert db.execute("SELECT status, number FROM sales_documents WHERE id = %s", (out["document_ids"][0],)).fetchone()["status"] == "issued"
    assert client.get(url(f"/recurring-invoices/{auto['id']}"), headers=h).json()["is_active"] is False              # ran past ends_on
    p = client.patch(url(f"/recurring-invoices/{rid}"), headers=h, json={"frequency": "quarterly", "is_active": False}).json()
    assert "frequency" in p, p
    assert p["frequency"] == "quarterly" and p["is_active"] is False
    assert client.patch(url(f"/recurring-invoices/{rid}"), headers=h, json={"frequency": "weekly"}).status_code == 400
    assert len(client.get(url("/recurring-invoices"), headers=h).json()["data"]) == 2
    assert client.delete(url(f"/recurring-invoices/{rid}"), headers=h).status_code == 204
    assert client.delete(url(f"/recurring-invoices/{rid}"), headers=h).status_code == 404


def test_daily_jobs_are_queued_once(db):
    from app import worker
    db.execute("UPDATE jobs SET status = 'cancelled' WHERE status = 'queued' AND kind LIKE 'collections.%'")
    with db.transaction():
        assert worker.schedule_daily_jobs(db.cursor()) >= 2
    with db.transaction():
        assert worker.schedule_daily_jobs(db.cursor()) == 0
    assert {r["kind"] for r in db.execute("SELECT kind FROM jobs WHERE entity_id = %s AND kind LIKE 'collections.%%'", (EM,)).fetchall()} == {"collections.dunning", "collections.recurring"}
    from app import worker as w
    with db.transaction():
        j = db.execute("SELECT * FROM jobs WHERE entity_id = %s AND kind = 'collections.dunning'", (EM,)).fetchone()
        assert set(w.handle_dunning(db.cursor(), j)) == {"messages_queued", "tasks_created"}


# ================================================================== invariants
def test_visibility_and_the_audit_chain(client, db):
    stranger = login(client, "stranger@example.in")
    assert client.get(url("/quotes"), headers=stranger).status_code == 404
    assert client.get(url("/reports/trial-balance"), headers=stranger).status_code == 404
    assert db.execute("SELECT app.audit_verify() AS v").fetchone()["v"] is None


def test_every_posted_journal_balances(db):
    bad = db.execute("""SELECT e.entry_no FROM journal_entries e JOIN journal_lines l ON l.entry_id = e.id WHERE e.entity_id = %s AND e.status <> 'draft'
                        GROUP BY e.id, e.entry_no HAVING sum(l.debit) <> sum(l.credit)""", (EM,)).fetchall()
    assert bad == []
    assert db.execute("SELECT count(*) AS n FROM journal_entries WHERE entity_id = %s AND source = 'sales_document'", (EM,)).fetchone()["n"] >= 4
    assert db.execute("SELECT count(*) AS n FROM sales_documents WHERE entity_id = %s AND status = 'issued' AND journal_entry_id IS NULL", (EM,)).fetchone()["n"] == 0
