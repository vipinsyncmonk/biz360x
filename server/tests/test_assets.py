"""Fixed assets: the depreciation schedule (pure), then the register, monthly depreciation journals and a sale through the API."""
import datetime as dt
import os
import uuid
from decimal import Decimal as D

import psycopg
import pytest
from psycopg.rows import dict_row

from app import assets

DB = os.environ["DATABASE_URL"]
ORG = "00000000-0000-0000-0000-000000f3a000"
EN = "00000000-0000-0000-0000-000000f3a001"
U_OWNER, U_VIEWER = "00000000-0000-0000-0000-000000f3a011", "00000000-0000-0000-0000-000000f3a012"
OWNER, VIEWER = "fa-owner@example.in", "fa-viewer@example.in"
S: dict = {}


def asset(**kw):
    base = {"booking": "reclassify", "put_to_use_on": dt.date(2025, 4, 1), "cost": D("120000"), "residual_value": D("6000"), "method": "slm", "life_months": 36,
            "rate_pct": None, "opening_accumulated": D("0"), "opening_through": None, "disposed_on": None}
    return {**base, **kw}


# ------------------------------------------------------------------ pure
def test_straight_line_charges_an_even_amount_and_stops_at_the_residual_value():
    rows = assets.schedule(asset(), {}, dt.date(2028, 12, 1))
    assert len(rows) == 36 and rows[0]["month"] == dt.date(2025, 4, 1) and rows[-1]["month"] == dt.date(2028, 3, 1)
    assert rows[0]["amount"] == D("3166.67") and rows[0]["book_value_after"] == D("116833.33")
    assert rows[-1]["book_value_after"] == D("6000.00")                                  # rounding never takes it below the residual value
    assert sum(r["amount"] for r in rows) == D("114000.00")


def test_the_first_month_is_charged_only_for_the_days_the_asset_was_in_use():
    rows = assets.schedule(asset(put_to_use_on=dt.date(2025, 4, 16)), {}, dt.date(2025, 5, 1))
    assert rows[0]["amount"] == D("1583.33") and rows[1]["amount"] == D("3166.67")      # 15 of 30 days in April


def test_written_down_value_uses_the_book_value_at_the_start_of_the_year():
    a = asset(method="wdv", life_months=None, rate_pct=D("40"), residual_value=D("5000"), cost=D("100000"))
    rows = assets.schedule(a, {}, dt.date(2026, 5, 1))
    assert rows[0]["amount"] == D("3333.33") and rows[11]["amount"] == D("3333.33")      # 100000 x 40% / 12, all year
    year1 = sum(r["amount"] for r in rows[:12])
    assert year1 == D("39999.96")
    assert rows[12]["amount"] == ((D("100000") - year1) * D("40") / 100 / 12).quantize(D("0.01"))   # April 2026 starts from the lower book value


def test_posted_months_keep_what_was_posted_and_the_rest_carry_on_from_there():
    posted = {dt.date(2025, 4, 1): D("3000.00"), dt.date(2025, 5, 1): D("3000.00")}
    rows = assets.schedule(asset(), posted, dt.date(2025, 6, 1))
    assert [r["posted"] for r in rows] == [True, True, False] and rows[1]["book_value_after"] == D("114000.00") and rows[2]["amount"] == D("3166.67")
    assert assets.accumulated(asset(), posted) == D("6000.00")


def test_an_opening_asset_starts_the_month_after_the_balance_date():
    a = asset(booking="opening", opening_accumulated=D("60000"), opening_through=dt.date(2025, 3, 31), put_to_use_on=dt.date(2023, 4, 1))
    rows = assets.schedule(a, {}, dt.date(2025, 5, 1))
    assert rows[0]["month"] == dt.date(2025, 4, 1) and rows[0]["amount"] == D("3166.67") and rows[0]["book_value_after"] == D("56833.33")
    assert assets.accumulated(a, {}) == D("60000")


def test_the_month_of_a_sale_is_charged_up_to_the_day_of_sale_and_nothing_after():
    rows = assets.schedule(asset(disposed_on=dt.date(2025, 7, 15)), {}, dt.date(2026, 1, 1))
    assert rows[-1]["month"] == dt.date(2025, 7, 1) and rows[-1]["amount"] == D("1532.26")     # 15 of 31 days
    assert assets.disposal_result(cost=D("100"), accumulated_before=D("30"), last_month_charge=D("5"), proceeds=D("80")) == {"book_value": D("65"), "gain": D("15")}


def test_every_category_has_a_life_and_the_default_is_in_months():
    assert assets.default_life_months("computers") == 36 and assets.default_life_months("building") == 720
    assert all(v[1] > 0 for v in assets.CATEGORIES.values())


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
    db.execute("INSERT INTO organizations (id, name, kind) VALUES (%s, 'Assets Org', 'business_group')", (ORG,))
    db.execute("INSERT INTO entities (id, organization_id, legal_name, constitution, track) VALUES (%s, %s, 'Assets Test Pvt Ltd', 'private_limited', 'business')", (EN, ORG))
    db.execute("INSERT INTO users (id, email, full_name) VALUES (%s, %s, 'Fa Owner'), (%s, %s, 'Fa Viewer')", (U_OWNER, OWNER, U_VIEWER, VIEWER))
    db.execute("INSERT INTO memberships (entity_id, user_id, role, accepted_at) VALUES (%s, %s, 'owner', now()), (%s, %s, 'viewer', now())", (EN, U_OWNER, EN, U_VIEWER))
    yield


def balance(db, key_):
    """Debit minus credit of the entity's account for a system key (by subtype)."""
    r = db.execute("""SELECT coalesce(sum(l.debit - l.credit), 0) AS b FROM journal_lines l JOIN journal_entries j ON j.id = l.entry_id
                       JOIN ledger_accounts a ON a.id = l.account_id WHERE j.entity_id = %s AND a.subtype = %s""", (EN, key_)).fetchone()
    return r["b"]


LAPTOPS = {"name": "Dell laptop", "category": "computers", "acquired_on": "2025-04-01", "cost": "120000.00"}


def test_registering_an_asset_moves_its_cost_out_of_expenses_and_defaults_the_life_and_residual(client, db):
    o = login(client, OWNER)
    r = client.post(url("/fixed-assets"), headers={**o, **key()}, json=LAPTOPS)
    assert r.status_code == 201, r.text
    a = r.json()
    S["laptop"] = a["id"]
    assert (a["asset_no"], a["method"], a["life_months"], a["residual_value"], a["book_value"], a["status"]) == ("FA-0001", "slm", 36, "6000.00", "120000.00", "in_use")
    assert balance(db, "fixed_assets") == D("120000") and balance(db, "purchases") == D("-120000")
    r = client.post(url("/fixed-assets"), headers={**o, **key()}, json={"name": "Office chairs", "category": "furniture", "acquired_on": "2025-02-01", "cost": "50000.00", "booking": "opening",
                                                                       "opening_accumulated": "20000.00", "opening_through": "2025-03-31", "residual_value": "2500.00"})
    assert r.status_code == 201, r.text
    S["chairs"] = r.json()["id"]
    assert (r.json()["asset_no"], r.json()["book_value"], r.json()["life_months"]) == ("FA-0002", "30000.00", 120)
    assert balance(db, "fixed_assets") == D("120000")                                            # an opening asset posts nothing: the opening balances hold it


def test_the_same_key_does_not_register_twice(client, db):
    o = login(client, OWNER)
    h = {**o, **key()}
    body = {"name": "Router", "category": "servers_networks", "acquired_on": "2025-05-01", "cost": "30000.00", "booking": "already_booked"}
    a, b = client.post(url("/fixed-assets"), headers=h, json=body), client.post(url("/fixed-assets"), headers=h, json=body)
    assert a.status_code == 201 and b.status_code == 201 and a.json()["id"] == b.json()["id"]
    assert balance(db, "fixed_assets") == D("120000")                                            # "already booked" is a record only
    S["router"] = a.json()["id"]
    assert client.post(url("/fixed-assets"), headers=o, json=body).status_code == 400            # a key is required


@pytest.mark.parametrize("change,field", [
    ({"method": "wdv"}, "rate_pct"), ({"rate_pct": "20"}, "rate_pct"), ({"method": "wdv", "rate_pct": "20", "life_months": 12}, "life_months"),
    ({"residual_value": "120000"}, "residual_value"), ({"booking": "opening"}, "opening_through"), ({"opening_through": "2025-03-31"}, "opening_through"),
    ({"put_to_use_on": "2025-03-01"}, "put_to_use_on"),
    ({"booking": "opening", "opening_through": "2025-03-30", "opening_accumulated": "100"}, "opening_through"),
    ({"booking": "opening", "opening_through": "2025-03-31", "opening_accumulated": "999999"}, "opening_accumulated"),
    ({"purchase_bill_id": str(uuid.uuid4())}, "purchase_bill_id"),
])
def test_a_registration_that_does_not_make_sense_is_refused_with_the_field(client, change, field):
    r = client.post(url("/fixed-assets"), headers={**login(client, OWNER), **key()}, json={**LAPTOPS, **change})
    assert r.status_code == 400 and r.json()["code"] == "validation_failed" and field in str(r.json()["errors"]), r.text


def test_a_viewer_can_read_the_register_but_not_change_it(client):
    v = login(client, VIEWER)
    assert client.post(url("/fixed-assets"), headers={**v, **key()}, json=LAPTOPS).status_code == 403
    assert client.post(url("/depreciation/run"), headers={**v, **key()}, json={"period": "2025-06"}).status_code == 403
    r = client.get(url("/fixed-assets"), headers=v)
    assert r.status_code == 200 and [a["asset_no"] for a in r.json()["data"]] == ["FA-0001", "FA-0002", "FA-0003"]
    assert r.json()["totals"]["cost"] == "200000.00"


def test_the_preview_shows_each_month_and_posts_nothing(client, db):
    o = login(client, OWNER)
    r = client.get(url("/depreciation/preview?period=2025-06"), headers=o)
    assert r.status_code == 200, r.text
    months = r.json()["months"]
    assert [m["month"] for m in months] == ["2025-04-01", "2025-05-01", "2025-06-01"]
    assert months[0]["total"] == "3562.50" and months[1]["total"] == "3958.33"
    assert [a["asset_no"] for a in months[0]["assets"]] == ["FA-0001", "FA-0002"]               # the router (May) joins in the second month
    assert [a["asset_no"] for a in months[1]["assets"]] == ["FA-0001", "FA-0002", "FA-0003"]
    assert balance(db, "accumulated_depreciation") == D("0")
    assert client.get(url("/depreciation/preview?period=2099-01"), headers=o).status_code == 400   # not a month that has started


def test_running_a_month_posts_one_journal_per_month_and_a_second_run_posts_nothing(client, db):
    o = login(client, OWNER)
    h = {**o, **key()}
    r = client.post(url("/depreciation/run"), headers=h, json={"period": "2025-06"})
    assert r.status_code == 200, r.text
    out = r.json()
    assert [j["month"] for j in out["journals"]] == ["2025-04-01", "2025-05-01", "2025-06-01"] and [j["assets"] for j in out["journals"]] == [2, 3, 3]
    assert balance(db, "depreciation_expense") == D(out["total"]) and balance(db, "accumulated_depreciation") == -D(out["total"])
    again = client.post(url("/depreciation/run"), headers={**o, **key()}, json={"period": "2025-06"})
    assert again.status_code == 200 and again.json()["journals"] == [] and again.json()["total"] == "0.00"
    replay = client.post(url("/depreciation/run"), headers=h, json={"period": "2025-06"})
    assert replay.json() == out
    a = client.get(url(f"/fixed-assets/{S['laptop']}"), headers=o).json()
    assert a["accumulated_depreciation"] == "9500.01" and a["book_value"] == "110499.99"
    posted = [r for r in a["schedule"] if r["posted"]]
    assert [r["month"] for r in posted] == ["2025-04-01", "2025-05-01", "2025-06-01"]
    assert a["schedule"][3]["posted"] is False and a["schedule"][3]["amount"] == "3166.67"      # the coming months are shown, not posted


def test_a_sale_charges_the_part_month_and_books_the_gain_or_loss(client, db):
    o = login(client, OWNER)
    r = client.post(url(f"/fixed-assets/{S['laptop']}/dispose"), headers={**o, **key()}, json={"disposed_on": "2025-07-15", "proceeds": "90000.00"})
    assert r.status_code == 200, r.text
    a = r.json()
    charge = (D("114000") / 36 * D(15) / D(31)).quantize(D("0.01"))
    book = D("120000") - D("9500.01") - charge
    assert a["status"] == "disposed" and a["disposed_on"] == "2025-07-15" and D(a["gain_or_loss"]) == D("90000") - book < 0
    assert balance(db, "asset_disposal") == -(D("90000") - book)                                  # a loss is a debit on the gain-or-loss account
    assert balance(db, "fixed_assets") == D("0")                                                  # the laptop's cost is gone (the router was only recorded)
    assert balance(db, "accumulated_depreciation") == -D("1979.15")                              # what is left is the chairs and the router
    twice = client.post(url(f"/fixed-assets/{S['laptop']}/dispose"), headers={**o, **key()}, json={"disposed_on": "2025-07-20", "proceeds": "1.00"})
    assert twice.status_code == 409
    listed = client.get(url("/fixed-assets?status=in_use"), headers=o).json()
    assert [x["asset_no"] for x in listed["data"]] == ["FA-0002", "FA-0003"] and listed["totals"]["cost"] == "80000.00"


def test_a_sale_waits_for_the_months_before_it_to_be_depreciated(client):
    o = login(client, OWNER)
    r = client.post(url(f"/fixed-assets/{S['chairs']}/dispose"), headers={**o, **key()}, json={"disposed_on": "2025-09-10", "proceeds": "10000.00"})
    assert r.status_code == 409 and r.json()["code"] == "depreciation_pending" and "Jul 2025" in str(r.json()["errors"]), r.text
    early = client.post(url(f"/fixed-assets/{S['chairs']}/dispose"), headers={**o, **key()}, json={"disposed_on": "2025-03-15", "proceeds": "10000.00"})
    assert early.status_code == 400                                                              # before the opening balance date
    assert client.post(url(f"/fixed-assets/{uuid.uuid4()}/dispose"), headers={**o, **key()}, json={"disposed_on": "2025-09-10", "proceeds": "0"}).status_code == 404


def test_the_month_of_a_sale_is_not_charged_twice(client):
    o = login(client, OWNER)
    r = client.post(url("/depreciation/run"), headers={**o, **key()}, json={"period": "2025-07"})
    assert r.status_code == 200, r.text
    assert [(j["month"], j["assets"], j["total"]) for j in r.json()["journals"]] == [("2025-07-01", 2, "791.66")]   # the chairs and the router; the laptop was charged in its sale


def test_a_sale_in_a_month_already_run_gives_back_the_unused_part_of_the_month(client, db):
    o = login(client, OWNER)
    early = client.post(url(f"/fixed-assets/{S['chairs']}/dispose"), headers={**o, **key()}, json={"disposed_on": "2025-06-10", "proceeds": "1.00"})
    assert early.status_code == 409 and early.json()["code"] == "conflict"                      # July is posted: a sale cannot be dated before it
    r = client.post(url(f"/fixed-assets/{S['router']}/dispose"), headers={**o, **key()}, json={"disposed_on": "2025-07-10", "proceeds": "20000.00"})
    assert r.status_code == 200, r.text
    a = r.json()
    full = D("395.83")
    kept = (D("28500") / 72 * D(10) / D(31)).quantize(D("0.01"))                                # 10 of 31 days of July
    book = D("30000") - (D("395.83") * 2 + kept)
    assert D(a["gain_or_loss"]) == D("20000") - book and a["accumulated_depreciation"] == str(D("395.83") * 2 + kept)
    assert balance(db, "accumulated_depreciation") == -D("1583.32")                              # the chairs' four months are all that is left
    sched = client.get(url(f"/fixed-assets/{S['router']}"), headers=o).json()["schedule"]
    assert [(r["month"], r["amount"]) for r in sched][-1] == ("2025-07-01", str(kept)) and full > kept


def test_categories_come_with_a_life_and_a_residual(client):
    r = client.get(url("/fixed-asset-categories"), headers=login(client, OWNER))
    got = {c["category"]: c for c in r.json()["data"]}
    assert got["computers"]["life_months"] == 36 and got["computers"]["residual_pct"] == "5.00" and got["software"]["residual_pct"] == "0.00"
    assert len(got) == 10
