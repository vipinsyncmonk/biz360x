"""Inventory: the weighted average costing (pure), then stock moving through bills, invoices, returns and adjustments via the API."""
import datetime as dt
import os
import uuid
from decimal import Decimal as D

import psycopg
import pytest
from psycopg.rows import dict_row

from app import inventory, ledger
from app.inventory import Position

DB = os.environ["DATABASE_URL"]
ORG = "00000000-0000-0000-0000-0000001a7000"
EN = "00000000-0000-0000-0000-0000001a7001"
REG = "00000000-0000-0000-0000-0000001a7002"
VENDOR, V_GST = "00000000-0000-0000-0000-0000001a7003", "00000000-0000-0000-0000-0000001a7004"
BUYER, B_GST = "00000000-0000-0000-0000-0000001a7005", "00000000-0000-0000-0000-0000001a7006"
U_OWNER, U_VIEWER = "00000000-0000-0000-0000-0000001a7011", "00000000-0000-0000-0000-0000001a7012"
OWNER, VIEWER = "inv-owner@example.in", "inv-viewer@example.in"
TODAY = dt.date.today()
LOCKED = dt.date(2024, 1, 15)                # a day inside the period the seed locks
S: dict = {}


# ------------------------------------------------------------------ pure
def test_the_average_is_value_over_quantity_and_receipts_move_it():
    pos = inventory.receive(Position(), D("10"), D("1000"))
    assert pos.average == D("100.0000")
    pos = inventory.receive(pos, D("10"), D("1300"))
    assert (pos.qty, pos.value, pos.average) == (D("20"), D("2300"), D("115.0000"))
    assert Position().average == D("0") and Position(D("-2"), D("-200")).average == D("0")     # nothing on hand has no average


def test_an_issue_leaves_at_the_average_and_keeps_the_average_of_what_stays():
    value, unit, after = inventory.issue(Position(D("20"), D("2300")), D("4"))
    assert (value, unit) == (D("460.00"), D("115.0000")) and (after.qty, after.value, after.average) == (D("16"), D("1840.00"), D("115.0000"))


def test_taking_the_last_units_takes_the_whole_value_so_no_paise_are_left_behind():
    pos = Position(D("3"), D("100.00"))                                   # 33.3333 each
    v1, _, pos = inventory.issue(pos, D("1"))
    v2, _, pos = inventory.issue(pos, D("1"))
    v3, _, pos = inventory.issue(pos, D("1"))
    assert (v1, v2, v3) == (D("33.33"), D("33.34"), D("33.33")) or v1 + v2 + v3 == D("100.00")
    assert v1 + v2 + v3 == D("100.00") and pos == Position(D("0"), D("0.00"))


def test_with_nothing_on_hand_an_issue_uses_the_last_known_cost_and_carries_on_at_it():
    value, unit, after = inventory.issue(Position(), D("2"), D("50"))
    assert (value, unit, after) == (D("100.00"), D("50.0000"), Position(D("-2"), D("-100.00")))
    value, unit, after = inventory.issue(after, D("1"), D("999"))          # already negative: the cost it went negative at, not the fallback
    assert (value, unit, after.qty, after.value) == (D("50.00"), D("50.0000"), D("-3"), D("-150.00"))
    with pytest.raises(ValueError):
        inventory.issue(Position(D("1"), D("1")), D("0"))


def test_returned_goods_come_back_at_the_cost_they_left_at():
    assert inventory.return_value(D("1"), D("115.0000")) == D("115.00")
    assert inventory.return_value(D("3"), D("33.3333"), original_qty=D("3"), original_value=D("100.00")) == D("100.00")     # a full return: exactly what left
    assert inventory.return_value(D("2"), D("33.3333"), original_qty=D("3"), original_value=D("100.00")) == D("66.67")


def test_a_cost_is_shared_over_lines_to_the_paisa():
    assert inventory.allocate(D("100.00"), [D("1"), D("1"), D("1")]) == [D("33.33"), D("33.33"), D("33.34")]
    assert inventory.allocate(D("330.00"), [D("1000"), D("0")]) == [D("330.00"), D("0")]
    assert inventory.allocate(D("10"), []) == [] and inventory.allocate(D("10"), [D("0")]) == [D("0")]
    assert inventory.count_difference(D("21"), D("20")) == D("-1")


def test_the_journal_templates_balance():
    for lines in (inventory.cogs_lines(D("460")), inventory.return_lines(D("115")), inventory.adjustment_lines(D("5"), gain=True),
                  inventory.adjustment_lines(D("5"), gain=False), inventory.opening_lines(D("1000"))):
        assert ledger.balanced(lines)
    assert all(k in ledger.SYSTEM_ACCOUNTS for k in ("inventory", "cost_of_goods_sold", "stock_adjustments", "customs_duty_payable", "opening_balance_equity"))
    codes = [v[0] for v in ledger.SYSTEM_ACCOUNTS.values()]
    assert len(codes) == len(set(codes))                                  # no two system accounts share a code


def test_a_bill_puts_stock_lines_in_inventory_with_their_own_tax_when_credit_cannot_be_claimed():
    bill = {"itc_eligibility": "ineligible", "taxable_total": D("700"), "cgst_total": D("63"), "sgst_total": D("63"), "igst_total": D("0"), "grand_total": D("826"),
            "party_id": None, "bill_no": "X"}
    lines = ledger.purchase_bill_lines(bill, [("inventory", D("500")), ("purchases", D("200"))], {"inventory": D("90")})
    by = {l.key: l.debit - l.credit for l in lines}
    assert by == {"inventory": D("590"), "purchases": D("236"), "payable": D("-826")}
    assert {l.key: l.debit for l in ledger.purchase_bill_lines(bill, [("purchases", D("700"))]) if l.debit} == {"purchases": D("826")}      # unchanged without stock
    with pytest.raises(ledger.PostingError):
        ledger.purchase_bill_lines(bill, [("purchases", D("700"))], {"inventory": D("90")})


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


def fy(d: dt.date) -> str:
    y = d.year if d.month >= 4 else d.year - 1
    return f"{y}-{str(y + 1)[2:]}"


@pytest.fixture(scope="module", autouse=True)
def seed(db):
    db.execute("INSERT INTO organizations (id, name, kind) VALUES (%s, 'Inventory Org', 'business_group')", (ORG,))
    db.execute("INSERT INTO entities (id, organization_id, legal_name, constitution, track) VALUES (%s, %s, 'Stock Test Pvt Ltd', 'private_limited', 'business')", (EN, ORG))
    db.execute("INSERT INTO entity_registrations (id, entity_id, kind, number, state_code, label, gst_filing_frequency) VALUES (%s, %s, 'gstin', '29AAACS7001A1Z5', '29', 'Karnataka', 'monthly')", (REG, EN))
    db.execute("INSERT INTO document_series (entity_id, registration_id, doc_type, fy, prefix) VALUES (%s, %s, 'tax_invoice', %s, 'ST/'), (%s, %s, 'credit_note', %s, 'SC/')",
               (EN, REG, fy(TODAY), EN, REG, fy(TODAY)))
    db.execute("INSERT INTO users (id, email, full_name) VALUES (%s, %s, 'Inv Owner'), (%s, %s, 'Inv Viewer')", (U_OWNER, OWNER, U_VIEWER, VIEWER))
    db.execute("INSERT INTO memberships (entity_id, user_id, role, accepted_at) VALUES (%s, %s, 'owner', now()), (%s, %s, 'viewer', now())", (EN, U_OWNER, EN, U_VIEWER))
    db.execute("""INSERT INTO parties (id, entity_id, legal_name, is_vendor, is_customer, payment_terms_days) VALUES
                  (%s, %s, 'Parts Supplier', true, false, 30), (%s, %s, 'Stock Buyer', false, true, 30)""", (VENDOR, EN, BUYER, EN))
    db.execute("""INSERT INTO party_gst_registrations (id, party_id, gstin, state_code, is_default) VALUES
                  (%s, %s, '29AAKFP1234A1Z5', '29', true), (%s, %s, '29AABCB9999K1ZP', '29', true)""", (V_GST, VENDOR, B_GST, BUYER))
    db.execute("INSERT INTO fiscal_periods (entity_id, fy, period_start, period_end, status, locked_at) VALUES (%s, '2023-24', '2024-01-01', '2024-01-31', 'locked', now())", (EN,))
    yield
    db.execute("UPDATE jobs SET status = 'cancelled' WHERE status = 'queued' AND entity_id = %s", (EN,))
    db.execute("DELETE FROM tasks WHERE entity_id = %s", (EN,))


def balance(db, subtype):
    r = db.execute("""SELECT coalesce(sum(l.debit - l.credit), 0) AS b FROM journal_lines l JOIN journal_entries j ON j.id = l.entry_id
                       JOIN ledger_accounts a ON a.id = l.account_id WHERE j.entity_id = %s AND a.subtype = %s""", (EN, subtype)).fetchone()
    return r["b"]


def stock(client, o, sku):
    r = client.get(url("/stock-items"), headers=o)
    assert r.status_code == 200, r.text
    return next(i for i in r.json()["data"] if i["sku"] == sku)


def make_item(client, o, sku, name, **over):
    r = client.post(url("/items"), headers=o, json={"sku": sku, "name": name, "kind": "goods", "hsn_sac": "8481", "gst_rate": 18, "track_inventory": True, **over})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def book_bill(client, o, no, lines, **over):
    r = client.post(url("/purchase-bills"), headers=o, json={"party_id": VENDOR, "registration_id": REG, "bill_no": no, "bill_date": str(TODAY), "accepted_on": str(TODAY),
                                                             "lines": lines, **over})
    assert r.status_code == 201, r.text
    b = client.post(url(f"/purchase-bills/{r.json()['id']}/book"), headers={**o, **key()})
    assert b.status_code == 200, b.text
    return r.json()["id"]


def line(item, qty, rate, **over):
    return {"item_id": item, "description": "Widget", "hsn_sac": "8481", "qty": str(qty), "rate": str(rate), "gst_rate": "18", **over}


def draft_invoice(client, o, lines):
    r = client.post(url("/sales-documents"), headers=o, json={"doc_type": "tax_invoice", "doc_date": str(TODAY), "registration_id": REG, "party_id": BUYER,
                                                              "party_gst_id": B_GST, "lines": lines})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def issue(client, o, doc_id):
    return client.post(url(f"/sales-documents/{doc_id}/issue"), headers={**o, **key()}, json={})


def test_items_are_created_and_only_goods_can_be_kept_in_stock(client):
    o = login(client, OWNER)
    S["widget"] = make_item(client, o, "WID-1", "Widget", purchase_rate="100.00", sale_rate="250.00")
    S["gizmo"] = make_item(client, o, "GIZ-1", "Gizmo")
    S["loose"] = make_item(client, o, "LOOSE-1", "Untracked bolt", track_inventory=False)
    r = client.post(url("/items"), headers=o, json={"sku": "SVC-1", "name": "Fitting", "kind": "service", "hsn_sac": "998719", "gst_rate": 18, "track_inventory": True})
    assert r.status_code == 400 and r.json()["errors"][0]["field"] == "track_inventory"
    assert client.post(url("/items"), headers=o, json={"sku": "WID-1", "name": "Again", "hsn_sac": "8481", "gst_rate": 18}).status_code == 409
    assert client.post(url("/items"), headers=o, json={"sku": "BAD", "name": "Bad rate", "hsn_sac": "8481", "gst_rate": 17}).status_code == 400
    r = client.get(url("/items"), headers=o, params={"q": "wid"})
    assert [i["sku"] for i in r.json()["data"]] == ["WID-1"] and r.json()["data"][0]["purchase_rate"] == "100.00" and r.json()["data"][0]["gst_rate"] == 18
    r = client.patch(url(f"/items/{S['loose']}"), headers=o, json={"name": "Loose bolt"})
    assert r.status_code == 200 and r.json()["name"] == "Loose bolt" and r.json()["track_inventory"] is False
    assert client.get(url(f"/items/{uuid.uuid4()}"), headers=o).status_code == 404
    assert [i["sku"] for i in client.get(url("/stock-items"), headers=o).json()["data"]] == ["GIZ-1", "WID-1"]          # only what is tracked


def test_opening_stock_comes_in_at_its_cost_and_posts_against_opening_equity(client, db):
    o = login(client, OWNER)
    body = {"item_id": S["widget"], "kind": "opening", "date": str(TODAY), "qty": "10", "unit_cost": "100"}
    r = client.post(url("/stock-adjustments"), headers={**o, **key()}, json=body)
    assert r.status_code == 201, r.text
    out = r.json()
    assert out["booking"] == "post" and (out["item"]["qty"], out["item"]["value"], out["item"]["average_cost"]) == ("10.000", "1000.00", "100.0000")
    m = out["movements"][0]
    assert (m["qty_in"], m["unit_cost"], m["value"], m["source_kind"], m["warehouse_code"]) == ("10.000", "100.0000", "1000.00", "opening", "MAIN") and m["entry_no"]
    assert balance(db, "inventory") == D("1000") and balance(db, "opening_balance_equity") == D("-1000")
    assert client.post(url("/stock-adjustments"), headers={**o, **key()}, json=body).status_code == 409                # opening stock is recorded once
    assert client.post(url("/stock-adjustments"), headers=o, json=body).status_code == 400                            # a key is required
    r = client.post(url("/stock-adjustments"), headers={**o, **key()}, json={**body, "unit_cost": None})
    assert r.status_code == 400 and r.json()["errors"][0]["field"] == "unit_cost"


def test_opening_stock_already_in_the_opening_balances_is_recorded_without_a_journal(client, db):
    o = login(client, OWNER)
    r = client.post(url("/stock-adjustments"), headers={**o, **key()}, json={"item_id": S["gizmo"], "kind": "opening", "date": str(TODAY), "qty": "2", "unit_cost": "50",
                                                                             "booking": "in_opening_balances"})
    assert r.status_code == 201 and r.json()["movements"][0]["entry_no"] is None and r.json()["item"]["value"] == "100.00"
    assert balance(db, "inventory") == D("1000")
    v = client.get(url("/stock-valuation"), headers=o).json()
    assert (v["total_value"], v["ledger_balance"], v["difference"]) == ("1100.00", "1000.00", "-100.00")              # the report shows what the ledger does not carry
    # bring the ledger in line for the tests below, as the opening trial balance would have
    db.execute("SELECT set_config('app.user_id', %s, false)", (U_OWNER,))
    with db.cursor() as cur:
        ledger.post_journal(cur, EN, TODAY, "opening", None, "Opening stock in the trial balance", inventory.opening_lines(D("100")))
    assert client.get(url("/stock-valuation"), headers=o).json()["difference"] == "0.00"


def test_a_booked_bill_brings_stock_in_at_cost_and_debits_inventory_not_purchases(client, db):
    o = login(client, OWNER)
    S["bill1"] = book_bill(client, o, "PS/001", [line(S["widget"], 10, 130), {"description": "Freight", "hsn_sac": "996511", "qty": "1", "rate": "200", "gst_rate": "18"},
                                                 line(S["loose"], 5, 10)])
    w = stock(client, o, "WID-1")
    assert (w["qty"], w["value"], w["average_cost"]) == ("20.000", "2300.00", "115.0000")
    assert balance(db, "inventory") == D("2400") and balance(db, "purchases") == D("250")                             # freight and the untracked bolts stay an expense
    assert balance(db, "gst_input_cgst") == D("139.50") and balance(db, "payable") == D("-1829")
    r = client.get(url("/stock-movements"), headers=o, params={"item_id": S["widget"]})
    top = r.json()["data"][0]
    assert (top["source_kind"], top["reference"], top["qty_in"], top["unit_cost"], top["value"]) == ("purchase_bill", "PS/001", "10.000", "130.0000", "1300.00")
    assert len(r.json()["data"]) == 2 and top["entry_no"]


def test_tax_that_cannot_be_claimed_is_part_of_the_cost_of_the_stock(client, db):
    o = login(client, OWNER)
    before = balance(db, "inventory")
    book_bill(client, o, "PS/002", [line(S["gizmo"], 5, 100)], itc_eligibility="ineligible")
    g = stock(client, o, "GIZ-1")
    assert (g["qty"], g["value"]) == ("7.000", "690.00") and balance(db, "inventory") - before == D("590")            # 500 + 90 GST
    assert balance(db, "gst_input_cgst") == D("139.50")                                                                # no credit taken


def test_an_invoice_takes_stock_out_at_the_average_and_posts_cost_of_goods_sold(client, db):
    o = login(client, OWNER)
    S["inv1"] = draft_invoice(client, o, [line(S["widget"], 4, 250), line(S["loose"], 1, 20)])
    r = issue(client, o, S["inv1"])
    assert r.status_code == 202, r.text
    w = stock(client, o, "WID-1")
    assert (w["qty"], w["value"], w["average_cost"]) == ("16.000", "1840.00", "115.0000")
    assert balance(db, "cost_of_goods_sold") == D("460") and balance(db, "sales") == D("-1020")
    j = db.execute("SELECT source::text AS source, entry_date FROM journal_entries WHERE entity_id = %s AND source_id = %s ORDER BY entry_no", (EN, S["inv1"])).fetchall()
    assert [x["source"] for x in j] == ["sales_document", "inventory"]
    top = client.get(url("/stock-movements"), headers=o, params={"item_id": S["widget"]}).json()["data"][0]
    assert (top["qty_out"], top["unit_cost"], top["value"], top["source_kind"]) == ("4.000", "115.0000", "-460.00", "sales_document") and top["reference"].startswith("ST/")


def test_a_sale_of_more_than_is_on_hand_is_refused_and_nothing_is_issued(client, db):
    o = login(client, OWNER)
    doc = draft_invoice(client, o, [line(S["widget"], 100, 250)])
    r = issue(client, o, doc)
    assert r.status_code == 409 and r.json()["code"] == "insufficient_stock" and "16 NOS on hand, 100 needed" in r.json()["detail"], r.text
    assert client.get(url(f"/sales-documents/{doc}"), headers=o).json()["status"] == "draft"
    assert stock(client, o, "WID-1")["qty"] == "16.000" and balance(db, "cost_of_goods_sold") == D("460")
    # the database refuses it too, whatever the API checked
    wh = db.execute("SELECT id FROM warehouses WHERE entity_id = %s AND is_default", (EN,)).fetchone()["id"]
    with pytest.raises(psycopg.errors.CheckViolation) as e:
        db.execute("""INSERT INTO stock_movements (entity_id, item_id, warehouse_id, movement_date, qty_out, unit_cost, value, source_kind)
                      VALUES (%s, %s, %s, %s, 100, 115, -11500, 'adjustment')""", (EN, S["widget"], wh, TODAY))
    assert e.value.diag.constraint_name == "stock_not_negative"


def test_returned_goods_go_back_into_stock_at_the_cost_they_left_at(client, db):
    o = login(client, OWNER)
    book_bill(client, o, "PS/003", [line(S["widget"], 4, 200)])                                                        # the average moves to 132 after the sale
    assert stock(client, o, "WID-1")["average_cost"] == "132.0000"
    orig = db.execute("SELECT id FROM sales_document_lines WHERE document_id = %s AND item_id = %s", (S["inv1"], S["widget"])).fetchone()["id"]
    r = client.post(url(f"/sales-documents/{S['inv1']}/credit-notes"), headers=o, json={"cn_reason": "sales_return", "lines": [line(S["widget"], 1, 250, original_line_id=orig)]})
    assert r.status_code == 201, r.text
    assert issue(client, o, r.json()["id"]).status_code == 202
    w = stock(client, o, "WID-1")
    assert (w["qty"], w["value"]) == ("21.000", "2755.00")                                                            # 2640 + 115, not + 132
    assert balance(db, "cost_of_goods_sold") == D("345")
    # a credit note for a price difference moves no stock
    r = client.post(url(f"/sales-documents/{S['inv1']}/credit-notes"), headers=o, json={"cn_reason": "post_sale_discount", "lines": [line(S["widget"], 1, 10, original_line_id=orig)]})
    assert r.status_code == 201 and issue(client, o, r.json()["id"]).status_code == 202
    assert stock(client, o, "WID-1")["qty"] == "21.000"


def test_a_count_books_the_difference_and_a_write_off_takes_stock_out_at_the_average(client, db):
    o = login(client, OWNER)
    base = {"item_id": S["widget"], "date": str(TODAY)}
    r = client.post(url("/stock-adjustments"), headers={**o, **key()}, json={**base, "kind": "count", "counted_qty": "20", "reason": "Year-end count"})
    assert r.status_code == 201, r.text
    m = r.json()["movements"][0]
    assert (m["qty_out"], m["value"], m["reason"], m["note"]) == ("1.000", "-131.19", "count", "Year-end count") and r.json()["item"]["value"] == "2623.81"
    assert balance(db, "stock_adjustments") == D("131.19")
    r = client.post(url("/stock-adjustments"), headers={**o, **key()}, json={**base, "kind": "write_off", "qty": "2", "reason": "Damaged in transit"})
    assert r.status_code == 201 and r.json()["movements"][0]["value"] == "-262.38" and r.json()["item"]["qty"] == "18.000"
    assert balance(db, "stock_adjustments") == D("393.57")
    r = client.post(url("/stock-adjustments"), headers={**o, **key()}, json={**base, "kind": "count", "counted_qty": "19", "reason": "Found one behind the rack"})
    assert r.status_code == 201 and r.json()["movements"][0]["qty_in"] == "1.000" and r.json()["movements"][0]["unit_cost"] == "131.1906"
    S["widget_value"] = D(r.json()["item"]["value"])
    for change, field in (({"kind": "count", "counted_qty": "5"}, "reason"), ({"kind": "write_off", "reason": "x"}, "qty"), ({"kind": "count", "qty": "1", "reason": "x"}, "counted_qty"),
                          ({"kind": "write_off", "qty": "1", "reason": "x", "date": str(TODAY + dt.timedelta(days=1))}, "date"),
                          ({"kind": "write_off", "qty": "1", "reason": "x", "warehouse_id": str(uuid.uuid4())}, "warehouse_id"),
                          ({"kind": "write_off", "qty": "1", "reason": "x", "item_id": str(uuid.uuid4())}, "item_id")):
        bad = client.post(url("/stock-adjustments"), headers={**o, **key()}, json={**base, **change})
        assert bad.status_code == 400 and field in str(bad.json()["errors"]), bad.text
    assert client.post(url("/stock-adjustments"), headers={**o, **key()}, json={**base, "kind": "count", "counted_qty": "19", "reason": "Again"}).status_code == 409
    r = client.post(url("/stock-adjustments"), headers={**o, **key()}, json={**base, "kind": "write_off", "qty": "500", "reason": "Too many"})
    assert r.status_code == 409 and r.json()["code"] == "insufficient_stock"
    r = client.post(url("/stock-adjustments"), headers={**o, **key()}, json={**base, "item_id": S["loose"], "kind": "write_off", "qty": "1", "reason": "x"})
    assert r.status_code == 409 and r.json()["code"] == "item_not_tracked"


def test_the_same_key_does_not_adjust_twice(client, db):
    o = login(client, OWNER)
    h = {**o, **key()}
    body = {"item_id": S["gizmo"], "kind": "write_off", "date": str(TODAY), "qty": "1", "reason": "Sample given away"}
    a, b = client.post(url("/stock-adjustments"), headers=h, json=body), client.post(url("/stock-adjustments"), headers=h, json=body)
    assert a.status_code == 201 and b.status_code == 201 and a.json()["movements"][0]["id"] == b.json()["movements"][0]["id"]
    assert stock(client, o, "GIZ-1")["qty"] == "6.000"
    assert client.post(url("/stock-adjustments"), headers=h, json={**body, "qty": "2"}).json()["code"] == "idempotency_conflict"


def test_a_locked_period_refuses_a_movement_with_or_without_a_journal(client, db):
    o = login(client, OWNER)
    r = client.post(url("/stock-adjustments"), headers={**o, **key()}, json={"item_id": S["widget"], "kind": "write_off", "date": str(LOCKED), "qty": "1", "reason": "Old loss"})
    assert r.status_code == 409 and r.json()["code"] == "period_locked", r.text
    extra = make_item(client, o, "LOCK-1", "Locked period item")
    r = client.post(url("/stock-adjustments"), headers={**o, **key()}, json={"item_id": extra, "kind": "opening", "date": str(LOCKED), "qty": "1", "unit_cost": "10",
                                                                             "booking": "in_opening_balances"})
    assert r.status_code == 409 and r.json()["code"] == "period_locked", r.text                                      # no journal here: the movement itself is refused
    assert stock(client, o, "LOCK-1")["qty"] == "0.000" and D(stock(client, o, "WID-1")["value"]) == S["widget_value"]


def test_a_viewer_can_read_stock_but_not_change_it(client):
    v = login(client, VIEWER)
    assert client.post(url("/stock-adjustments"), headers={**v, **key()}, json={"item_id": S["widget"], "kind": "write_off", "date": str(TODAY), "qty": "1", "reason": "x"}).status_code == 403
    assert client.post(url("/items"), headers=v, json={"sku": "V-1", "name": "No", "hsn_sac": "8481", "gst_rate": 18}).status_code == 403
    assert client.patch(url(f"/items/{S['widget']}"), headers=v, json={"track_inventory": False}).status_code == 403
    assert client.post(url("/warehouses"), headers=v, json={"code": "B", "name": "Second"}).status_code == 403
    assert client.put(url("/inventory/settings"), headers=v, json={"allow_negative_stock": True}).status_code == 403
    assert client.post(url("/stock-transfers"), headers=v, json={"item_id": S["widget"], "from_warehouse_id": str(uuid.uuid4()), "to_warehouse_id": str(uuid.uuid4()),
                                                                 "date": str(TODAY), "qty": "1"}).status_code == 403
    for path in ("/stock-items", "/stock-movements", "/stock-valuation", "/warehouses", "/inventory/settings", "/items"):
        assert client.get(url(path), headers=v).status_code == 200, path
    assert client.get(f"/v1/entities/{uuid.uuid4()}/stock-items", headers=v).status_code == 404                        # an entity the caller cannot see


def test_a_move_between_warehouses_keeps_the_value_and_posts_nothing(client, db):
    o = login(client, OWNER)
    r = client.post(url("/warehouses"), headers=o, json={"code": "shop", "name": "Shop floor"})
    assert r.status_code == 201 and r.json()["code"] == "SHOP" and r.json()["is_default"] is False
    main = next(w for w in client.get(url("/warehouses"), headers=o).json()["data"] if w["is_default"])["id"]
    journals = db.execute("SELECT count(*) AS n FROM journal_entries WHERE entity_id = %s", (EN,)).fetchone()["n"]
    body = {"item_id": S["widget"], "from_warehouse_id": main, "to_warehouse_id": r.json()["id"], "date": str(TODAY), "qty": "5"}
    t = client.post(url("/stock-transfers"), headers=o, json=body)
    assert t.status_code == 201, t.text
    out, back = t.json()["movements"]
    assert out["qty_out"] == "5.000" and back["qty_in"] == "5.000" and D(out["value"]) == -D(back["value"]) and out["source_id"] == back["source_id"]
    w = t.json()["item"]
    assert w["qty"] == "19.000" and D(w["value"]) == S["widget_value"] and {l["code"]: l["qty"] for l in w["levels"]} == {"MAIN": "14.000", "SHOP": "5.000"}
    assert db.execute("SELECT count(*) AS n FROM journal_entries WHERE entity_id = %s", (EN,)).fetchone()["n"] == journals
    assert client.post(url("/stock-transfers"), headers=o, json={**body, "qty": "50"}).json()["code"] == "insufficient_stock"
    assert client.post(url("/stock-transfers"), headers=o, json={**body, "to_warehouse_id": main}).status_code == 400
    # invoices ship from the default warehouse: 14 there, so 15 is refused although the company holds 19
    r = issue(client, o, draft_invoice(client, o, [line(S["widget"], 15, 250)]))
    assert r.status_code == 409 and r.json()["code"] == "insufficient_stock"


def test_customs_duty_on_a_linked_bill_of_entry_is_added_to_the_cost_of_the_stock(client, db):
    o = login(client, OWNER)
    item = make_item(client, o, "IMP-1", "Imported valve")
    bill = book_bill(client, o, "PS/004", [line(item, 10, 1000)], itc_eligibility="ineligible", place_of_supply="29")
    before = D(stock(client, o, "IMP-1")["value"])
    r = client.post(url("/bills-of-entry"), headers=o, json={"boe_number": "7654321", "boe_date": str(TODAY), "port_code": "INNSA1", "currency": "USD", "cif_foreign": "120.00",
                                                             "exchange_rate": "83.0000", "bcd": "750.00", "sws": "75.00", "igst": "1950.00", "status": "assessed",
                                                             "purchase_bill_id": bill})
    assert r.status_code == 201, r.text
    s = stock(client, o, "IMP-1")
    assert D(s["value"]) - before == D("825") and s["qty"] == "10.000"                                                 # basic duty and surcharge, not the IGST
    assert balance(db, "customs_duty_payable") == D("-825")
    top = client.get(url("/stock-movements"), headers=o, params={"item_id": item}).json()["data"][0]
    assert (top["source_kind"], top["qty_in"], top["value"], top["reference"]) == ("bill_of_entry", "0.000", "825.00", "7654321")
    assert client.patch(url(f"/bills-of-entry/{r.json()['id']}"), headers=o, json={"purchase_bill_id": bill}).status_code == 200
    assert balance(db, "customs_duty_payable") == D("-825")                                                            # posted once


def test_a_company_that_allows_negative_stock_can_sell_before_the_bill_is_booked(client, db):
    o = login(client, OWNER)
    assert client.get(url("/inventory/settings"), headers=o).json() == {"allow_negative_stock": False, "valuation_method": "weighted_average"}
    assert client.put(url("/inventory/settings"), headers=o, json={"allow_negative_stock": True}).json()["allow_negative_stock"] is True
    assert issue(client, o, draft_invoice(client, o, [line(S["gizmo"], 8, 300)])).status_code == 202                   # 6 on hand at 98.5714
    g = stock(client, o, "GIZ-1")
    assert g["qty"] == "-2.000" and D(g["value"]) < 0
    assert client.get(url("/stock-items"), headers=o).json()["allow_negative_stock"] is True
    assert client.put(url("/inventory/settings"), headers=o, json={"allow_negative_stock": False}).status_code == 200
    r = issue(client, o, draft_invoice(client, o, [line(S["gizmo"], 1, 300)]))
    assert r.status_code == 409 and r.json()["code"] == "insufficient_stock"


def test_the_valuation_as_of_a_date_agrees_with_the_inventory_account(client, db):
    o = login(client, OWNER)
    v = client.get(url("/stock-valuation"), headers=o).json()
    assert v["valuation_method"] == "weighted_average" and v["as_of"] == str(TODAY)
    assert D(v["total_value"]) == sum(D(i["value"]) for i in v["items"]) == balance(db, "inventory") and v["difference"] == "0.00"
    assert {i["sku"] for i in v["items"]} == {"WID-1", "GIZ-1", "IMP-1"}
    old = client.get(url("/stock-valuation"), headers=o, params={"as_of": str(TODAY - dt.timedelta(days=30))}).json()
    assert old["items"] == [] and old["total_value"] == "0.00"
    listed = client.get(url("/stock-items"), headers=o).json()
    assert listed["totals"]["value"] == v["total_value"] and listed["totals"]["out_of_stock"] == 2                     # the gizmo below zero and the item that never moved


def test_an_item_with_stock_cannot_stop_being_tracked_and_the_audit_chain_holds(client, db):
    o = login(client, OWNER)
    r = client.patch(url(f"/items/{S['widget']}"), headers=o, json={"track_inventory": False})
    assert r.status_code == 409 and r.json()["code"] == "conflict"
    assert client.patch(url(f"/items/{S['loose']}"), headers=o, json={"track_inventory": True}).json()["track_inventory"] is True
    assert db.execute("SELECT app.audit_verify() AS v").fetchone()["v"] is None
    assert db.execute("SELECT count(*) AS n FROM event_outbox WHERE entity_id = %s AND type = 'stock.adjusted'", (EN,)).fetchone()["n"] >= 5
