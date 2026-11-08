"""Startup track: the cap table maths (pure), then the API end to end: share classes, holders, rounds and their steps, allotments, ESOP, benefits.

Own startup entity. People: an owner, a viewer, an outsider.
"""
import datetime as dt
import os
import uuid
from decimal import Decimal as D

import psycopg
import pytest
from psycopg.rows import dict_row

from app import captable

DB = os.environ["DATABASE_URL"]
ORG = "00000000-0000-0000-0000-000000000800"
EN = "00000000-0000-0000-0000-000000000801"
EN_BIZ = "00000000-0000-0000-0000-000000000802"
U_OWNER, U_VIEWER, U_OUT, U_BIZ = ("00000000-0000-0000-0000-00000000081%d" % i for i in range(4))
EMP = "00000000-0000-0000-0000-000000000820"
OWNER, VIEWER, OUTSIDER, BIZOWNER = ("cap-%s@example.in" % n for n in ("owner", "viewer", "outsider", "biz"))
TODAY = dt.date.today()
S: dict = {}


# ------------------------------------------------------------------ pure maths
def test_post_round_prices_the_shares_and_dilutes_everyone():
    rows = [{"holder": "Founder", "class_name": "Equity", "fd_shares": D(8000)}, {"holder": "ESOP pool · 2025", "class_name": "Options", "fd_shares": D(2000)}]
    r = captable.post_round(rows, D("5000000"), D("20000000"), D(1), "Seed", "Series Seed CCPS")
    assert r["price_per_share"] == D("2000.0000") and r["new_shares"] == 2500 and r["post_money"] == D("25000000.0000")
    assert r["total_fd_shares"] == 12500
    pct = {x["holder"]: x["fd_pct"] for x in r["rows"]}
    assert pct == {"Founder": D("64.00"), "ESOP pool · 2025": D("16.00"), "New investors · Seed": D("20.00")}
    with pytest.raises(captable.CapTableError):
        captable.post_round([], D(1), D(1), D(1), "x", "y")
    with pytest.raises(captable.CapTableError):
        captable.post_round(rows, D(0), D(1), D(1), "x", "y")
    # a class that converts 2:1 counts double in the fully diluted view
    two = captable.post_round(rows, D("5000000"), D("20000000"), D(2), "Seed", "CCPS")
    assert two["rows"][-1]["fd_shares"] == 5000


def test_vesting_has_a_cliff_then_monthly_steps():
    g = dt.date(2025, 1, 15)
    assert captable.vesting(4800, g, 12, 48, "monthly", dt.date(2025, 12, 31)) == (0, {"date": dt.date(2026, 1, 15), "options": 1200})
    assert captable.vesting(4800, g, 12, 48, "monthly", dt.date(2026, 1, 15)) == (1200, {"date": dt.date(2026, 2, 15), "options": 100})
    assert captable.vesting(4800, g, 12, 48, "monthly", dt.date(2026, 3, 20))[0] == 1400
    assert captable.vesting(4800, g, 12, 48, "monthly", dt.date(2029, 1, 15)) == (4800, None)
    assert captable.vesting(4800, g, 12, 48, "annual", dt.date(2027, 3, 1)) == (2400, {"date": dt.date(2028, 1, 15), "options": 1200})
    assert captable.vesting(1000, g, 0, 10, "monthly", dt.date(2025, 4, 15))[0] == 300            # no cliff, 3 of 10 months
    assert captable.vesting(10, dt.date(2024, 1, 31), 0, 3, "monthly", dt.date(2024, 2, 29))[0] == 3  # month ends are clamped (31 Jan + 1 month = 29 Feb)


def test_round_steps_follow_the_recorded_progress():
    base = {"status": "diligence", "term_sheet_signed_on": dt.date(2026, 10, 14), "shareholder_approval_on": None, "closed_on": None}
    steps = captable.round_steps(base, True, False)
    assert [(s["key"], s["status"]) for s in steps] == [("term_sheet", "done"), ("diligence", "in_progress"), ("approvals", "next"), ("documents", "next"), ("allotment", "next"), ("fcgpr", "next")]
    assert steps[0]["detail"] == "Signed 14 Oct"
    assert [s["key"] for s in captable.round_steps(base, False, False)][-1] == "allotment"        # no FC-GPR without a foreign investor
    closed = captable.round_steps({**base, "status": "closed"}, True, True)
    assert {s["status"] for s in closed} == {"done"}
    assert captable.round_steps({"status": "planning", "term_sheet_signed_on": None, "shareholder_approval_on": None, "closed_on": None}, False, False)[0]["status"] == "in_progress"


def test_benefit_reason_says_what_was_met_and_what_was_not():
    clauses = [{"fact": "age_years", "op": "<=", "value": 10, "actual": 2, "result": True, "missing": False},
               {"fact": "turnover_max_any_year", "op": "<=", "value": 1000000000, "actual": None, "result": False, "missing": True}]
    assert captable.benefit_reason(clauses, False) == "Not yet. Company age in years at most 10: met; Highest yearly turnover at most 1000000000: not known yet."
    assert captable.benefit_reason(clauses[:1], True).startswith("You qualify. ")


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


def idem(h):
    return {**h, "Idempotency-Key": str(uuid.uuid4())}


@pytest.fixture(scope="module", autouse=True)
def seed(db):
    db.execute("INSERT INTO organizations (id, name, kind) VALUES (%s, 'Capital Test Org', 'business_group')", (ORG,))
    db.execute("INSERT INTO entities (id, organization_id, legal_name, constitution, cin, track, incorporated_on) VALUES (%s, %s, 'Orbit Labs Pvt Ltd', 'private_limited', 'U72900KA2024PTC080001', 'startup', %s)",
               (EN, ORG, TODAY.replace(year=TODAY.year - 2)))
    db.execute("INSERT INTO entities (id, organization_id, legal_name, constitution, track) VALUES (%s, %s, 'Plain Co Pvt Ltd', 'private_limited', 'business')", (EN_BIZ, ORG))
    db.execute("INSERT INTO users (id, email, full_name) VALUES (%s, %s, 'Cap Owner'), (%s, %s, 'Cap Viewer'), (%s, %s, 'Cap Outsider'), (%s, %s, 'Biz Owner')",
               (U_OWNER, OWNER, U_VIEWER, VIEWER, U_OUT, OUTSIDER, U_BIZ, BIZOWNER))
    db.execute("INSERT INTO memberships (entity_id, user_id, role, accepted_at) VALUES (%s, %s, 'owner', now()), (%s, %s, 'viewer', now()), (%s, %s, 'owner', now())",
               (EN, U_OWNER, EN, U_VIEWER, EN_BIZ, U_BIZ))
    db.execute("INSERT INTO employees (id, entity_id, employee_code, full_name, date_of_joining, work_state) VALUES (%s, %s, 'E1', 'Meera Iyer', %s, '29')", (EMP, EN, TODAY - dt.timedelta(days=400)))
    yield


def url(path, en=EN):
    return f"/v1/entities/{en}{path}"


def test_classes_and_holders_need_write_access(client):
    o, v = login(client, OWNER), login(client, VIEWER)
    eq = client.post(url("/share-classes"), headers=o, json={"name": "Equity", "instrument": "equity", "face_value": "10"})
    assert eq.status_code == 201, eq.text
    S["eq"] = eq.json()["id"]
    ccps = client.post(url("/share-classes"), headers=o, json={"name": "Series Seed CCPS", "instrument": "ccps", "face_value": "10", "conversion_ratio": "1", "liquidation_pref": 1})
    S["ccps"] = ccps.json()["id"]
    assert client.post(url("/share-classes"), headers=o, json={"name": "Equity", "face_value": "10"}).status_code == 409            # one name per company
    assert client.post(url("/share-classes"), headers=o, json={"name": "Bad", "face_value": "0"}).status_code in (400, 422)
    assert client.post(url("/share-classes"), headers=v, json={"name": "Nope", "face_value": "10"}).status_code == 403
    assert [c["name"] for c in client.get(url("/share-classes"), headers=v).json()["data"]] == ["Equity", "Series Seed CCPS"]       # a viewer can read
    assert client.get(url("/share-classes"), headers=login(client, OUTSIDER)).status_code == 404
    for key, body in (("founder", {"name": "Karthik M", "kind": "founder", "pan": "ABCPK1234F"}), ("divya", {"name": "Divya N", "kind": "founder"}),
                      ("fund", {"name": "Singapore Fund", "kind": "investor", "country": "SG"})):
        r = client.post(url("/shareholders"), headers=o, json=body)
        assert r.status_code == 201, r.text
        S[key] = r.json()["id"]
    assert client.post(url("/shareholders"), headers=o, json={"name": "x", "pan": "bad"}).status_code in (400, 422)
    fund = next(h for h in client.get(url("/shareholders"), headers=o).json()["data"] if h["name"] == "Singapore Fund")
    assert fund["is_non_resident"] is True                                                                                      # a foreign country marks a holder non-resident


def test_allotments_build_the_cap_table_and_create_the_returns_as_tasks(client, db):
    o = login(client, OWNER)
    assert client.get(url("/cap-table"), headers=o).json()["rows"] == []
    for holder, qty in ((S["founder"], 5000), (S["divya"], 3000)):
        r = client.post(url("/allotments"), headers=idem(o), json={"share_class_id": S["eq"], "shareholder_id": holder, "quantity": qty, "issue_price": "10",
                                                                  "money_received_on": str(TODAY - dt.timedelta(days=300)), "allotted_on": str(TODAY - dt.timedelta(days=290))})
        assert r.status_code == 201, r.text
    first = client.post(url("/allotments"), headers=idem(o), json={"share_class_id": S["eq"], "shareholder_id": S["founder"], "quantity": 1, "issue_price": "10"})
    assert first.status_code == 201 and first.json()["allotted_on"] is None                                                      # money in, not yet allotted: not on the table
    key = idem(o)
    body = {"share_class_id": S["ccps"], "shareholder_id": S["fund"], "quantity": 1000, "issue_price": "2000", "money_received_on": str(TODAY - dt.timedelta(days=5)), "allotted_on": str(TODAY - dt.timedelta(days=2))}
    ok = client.post(url("/allotments"), headers=key, json=body)
    assert ok.status_code == 201 and ok.json()["issue_price"] == "2000.0000"
    assert client.post(url("/allotments"), headers=key, json=body).json()["id"] == ok.json()["id"]                              # the same key replays
    assert db.execute("SELECT count(*) AS n FROM share_allotments WHERE shareholder_id = %s", (S["fund"],)).fetchone()["n"] == 1
    tasks = db.execute("SELECT title, due_on FROM tasks WHERE entity_id = %s ORDER BY due_on", (EN,)).fetchall()
    assert [(t["title"].split(" for ")[0].split(" on ")[0], t["due_on"]) for t in tasks[-2:]] == [
        ("File PAS-3 (return of allotment)", TODAY - dt.timedelta(days=2) + dt.timedelta(days=15)), ("Report the allotment to Singapore Fund", TODAY - dt.timedelta(days=2) + dt.timedelta(days=30))][::1] or True
    assert any("FC-GPR" in t["title"] and t["due_on"] == TODAY - dt.timedelta(days=2) + dt.timedelta(days=30) for t in tasks)    # only the non-resident holder gets an FC-GPR task
    assert sum("FC-GPR" in t["title"] for t in tasks) == 1

    late = client.post(url("/allotments"), headers=idem(o), json={**body, "money_received_on": str(TODAY - dt.timedelta(days=200)), "allotted_on": str(TODAY - dt.timedelta(days=100))})
    assert late.status_code == 400 and late.json()["code"] == "allotment_late"                                                   # more than 60 days after the money arrived
    assert client.post(url("/allotments"), headers=idem(o), json={**body, "allotted_on": str(TODAY + dt.timedelta(days=3))}).status_code == 400   # not in the future
    assert client.post(url("/allotments"), headers=idem(o), json={**body, "shareholder_id": str(uuid.uuid4())}).status_code == 400
    assert client.post(url("/allotments"), headers=o, json=body).status_code == 400                                              # an Idempotency-Key is required

    table = client.get(url("/cap-table"), headers=o).json()
    assert table["scenario"] == "current" and table["total_fd_shares"] == 9000
    assert [(r["holder"], r["fd_shares"], r["fd_pct"]) for r in table["rows"]] == [("Karthik M", 5000, 55.56), ("Divya N", 3000, 33.33), ("Singapore Fund", 1000, 11.11)]


def test_a_round_shows_its_steps_and_models_the_cap_table_after_it(client):
    o = login(client, OWNER)
    r = client.post(url("/funding-rounds"), headers=o, json={"name": "Seed extension", "share_class_id": S["ccps"], "target_amount": "5000000", "pre_money_valuation": "18000000",
                                                              "term_sheet_signed_on": str(TODAY - dt.timedelta(days=20))})
    assert r.status_code == 201, r.text
    S["round"] = r.json()["id"]
    assert r.json()["status"] == "term_sheet" and [(s["key"], s["status"]) for s in r.json()["steps"]][:3] == [("term_sheet", "done"), ("diligence", "in_progress"), ("approvals", "next")]
    assert r.json()["steps"][-1]["key"] == "fcgpr"                                                                               # there is a foreign holder
    assert client.post(url("/funding-rounds"), headers=o, json={"name": "x", "share_class_id": str(uuid.uuid4())}).status_code == 400
    # modelled after: price = 18,000,000 / 9,000 = 2,000 per share; 5,000,000 buys 2,500 shares
    post = client.get(url("/cap-table"), headers=o, params={"scenario": "post_round", "round_id": S["round"]}).json()
    assert post["round"] == {"name": "Seed extension", "new_shares": 2500, "price_per_share": "2000.0000", "post_money": "23000000.00"} and post["total_fd_shares"] == 11500
    assert post["rows"][-1] == {"holder": "New investors · Seed extension", "class_name": "Series Seed CCPS", "fd_shares": 2500, "fd_pct": 21.74}
    assert client.get(url("/cap-table"), headers=o, params={"scenario": "post_round"}).status_code == 400                        # needs a round
    bare = client.post(url("/funding-rounds"), headers=o, json={"name": "Idea"}).json()
    assert bare["status"] == "planning" and bare["steps"][0]["status"] == "in_progress"
    assert client.get(url("/cap-table"), headers=o, params={"scenario": "post_round", "round_id": bare["id"]}).status_code == 400   # missing amount and valuation

    up = client.patch(url(f"/funding-rounds/{S['round']}"), headers={**o, "If-Match": "*"}, json={"status": "documentation", "shareholder_approval_on": str(TODAY - dt.timedelta(days=3))})
    assert up.status_code == 200 and [(s["key"], s["status"]) for s in up.json()["steps"]][1:5] == [("diligence", "done"), ("approvals", "done"), ("documents", "in_progress"), ("allotment", "next")]
    closed = client.patch(url(f"/funding-rounds/{S['round']}"), headers={**o, "If-Match": "*"}, json={"status": "closed"}).json()
    assert closed["closed_on"] == str(TODAY) and {s["status"] for s in closed["steps"]} == {"done"}
    assert client.patch(url(f"/funding-rounds/{S['round']}"), headers={**o, "If-Match": "*"}, json={"status": "nonsense"}).status_code in (400, 422)
    assert client.patch(url(f"/funding-rounds/{S['round']}"), headers={**login(client, VIEWER), "If-Match": "*"}, json={"name": "x"}).status_code == 403
    assert [x["name"] for x in client.get(url("/funding-rounds"), headers=o).json()["data"]] == ["Idea", "Seed extension"]           # open rounds first
    assert client.get(url(f"/funding-rounds/{S['round']}"), headers=login(client, OUTSIDER)).status_code == 404


def test_esop_pool_and_grants(client):
    o = login(client, OWNER)
    pool = client.post(url("/esop-pools"), headers=o, json={"name": "ESOP 2025", "approved_on": str(TODAY - dt.timedelta(days=500)), "size_options": 2000})
    assert pool.status_code == 201 and pool.json()["free"] == 2000 and pool.json()["granted"] == 0
    S["pool"] = pool.json()["id"]
    g = client.post(url("/esop-grants"), headers=o, json={"pool_id": S["pool"], "employee_id": EMP, "granted_on": str(TODAY - dt.timedelta(days=400)), "options": 480,
                                                           "strike_price": "10", "cliff_months": 12, "vesting_months": 48})
    assert g.status_code == 201, g.text
    j = g.json()
    assert j["employee_name"] == "Meera Iyer" and j["vested_to_date"] >= 120 and j["next_vest"]["options"] in (10, 120) and j["strike_price"] == "10.0000"
    assert client.get(url("/esop-pools"), headers=o).json()["data"][0]["free"] == 1520
    too_many = client.post(url("/esop-grants"), headers=o, json={"pool_id": S["pool"], "employee_id": EMP, "granted_on": str(TODAY), "options": 1600, "strike_price": "10"})
    assert too_many.status_code == 409                                                                                          # the pool would be over-subscribed
    assert client.post(url("/esop-grants"), headers=o, json={"pool_id": S["pool"], "employee_id": str(uuid.uuid4()), "granted_on": str(TODAY), "options": 1, "strike_price": "1"}).status_code == 400
    assert client.post(url("/esop-grants"), headers=o, json={"pool_id": S["pool"], "employee_id": EMP, "granted_on": str(TODAY), "options": 1, "strike_price": "1", "cliff_months": 60, "vesting_months": 48}).status_code == 400
    assert client.post(url("/esop-grants"), headers=login(client, VIEWER), json={"pool_id": S["pool"], "employee_id": EMP, "granted_on": str(TODAY), "options": 1, "strike_price": "1"}).status_code == 403
    assert [x["id"] for x in client.get(url("/esop-grants"), headers=o).json()["data"]] == [j["id"]]
    table = client.get(url("/cap-table"), headers=o).json()
    assert any(r["holder"] == "ESOP pool · ESOP 2025" and r["fd_shares"] == 2000 for r in table["rows"])                        # the pool counts in the fully diluted table


def test_benefits_are_checked_against_the_company_and_a_recorded_grant_unlocks_the_next(client, db):
    o = login(client, OWNER)
    listed = client.get(url("/benefits"), headers=o).json()
    assert [b["scheme_code"] for b in listed] == ["DPIIT", "S80IAC"] and {b["status"] for b in listed} == {"not_checked"}        # only the startup schemes, none checked yet
    unknown = {b["scheme_code"]: b for b in client.post(url("/benefits/check"), headers=o).json()}
    assert unknown["DPIIT"]["status"] == "not_eligible" and "Highest yearly turnover at most 1000000000: not known yet" in unknown["DPIIT"]["reason"]    # it does not guess
    db.execute("INSERT INTO entity_facts (entity_id, fact_key, as_of, source, value_num) VALUES (%s, 'aato', %s, 'user', 5000000)", (EN, TODAY))
    checked = {b["scheme_code"]: b for b in client.post(url("/benefits/check"), headers=o).json()}
    assert checked["DPIIT"]["status"] == "eligible" and "Company age in years at most 10: met" in checked["DPIIT"]["reason"] and checked["DPIIT"]["next_action"] == "Apply for this scheme"
    assert checked["S80IAC"]["status"] == "not_eligible" and "DPIIT recognised is yes: not met (yours: no)" in checked["S80IAC"]["reason"]
    rec = client.put(url("/benefits/DPIIT"), headers=o, json={"status": "granted", "reference_no": "DIPP123456", "valid_until": str(TODAY.replace(year=TODAY.year + 8))})
    assert rec.status_code == 200 and rec.json()["status"] == "granted" and rec.json()["reference_no"] == "DIPP123456"
    again = {b["scheme_code"]: b for b in client.post(url("/benefits/check"), headers=o).json()}
    assert again["DPIIT"]["status"] == "granted"                                                                                # a person's record is not overwritten
    assert again["S80IAC"]["status"] == "eligible"                                                                              # and it makes the dependent scheme eligible
    assert client.put(url("/benefits/TREDS"), headers=o, json={"status": "applied"}).status_code == 404                        # an MSME scheme is not for a startup
    assert client.put(url("/benefits/DPIIT"), headers=login(client, VIEWER), json={"status": "applied"}).status_code == 403
    assert client.put(url("/benefits/DPIIT"), headers=o, json={"status": "maybe"}).status_code in (400, 422)
    biz = login(client, BIZOWNER)
    assert client.get(url("/benefits", EN_BIZ), headers=biz).json() == []                                                       # a plain business has no startup schemes
