"""MSME track: interest on late payments (the maths, then the API), claims filed as a job, TReDS uploads, Udyam classification, and recording company facts.

Two entities: a small supplier with Udyam registration, and a medium one that has no interest rights. Dates are relative to today.
"""
import datetime as dt
import os
import uuid
from decimal import Decimal as D

import psycopg
import pytest
from psycopg.rows import dict_row

from app import msme

DB = os.environ["DATABASE_URL"]
ORG = "00000000-0000-0000-0000-000000000900"
EN, EN_MED, EN_NONE = ("00000000-0000-0000-0000-00000000091%d" % i for i in range(3))
REG, REG_MED, REG_NONE = ("00000000-0000-0000-0000-00000000092%d" % i for i in range(3))
BUYER, BUYER2, BUYER_MED = ("00000000-0000-0000-0000-00000000093%d" % i for i in range(3))
U_OWNER, U_VIEWER, U_OUT = ("00000000-0000-0000-0000-00000000094%d" % i for i in range(3))
OWNER, VIEWER, OUTSIDER = ("ms-%s@example.in" % n for n in ("owner", "viewer", "outsider"))
TODAY = dt.date.today()
S: dict = {}
RATES = [(dt.date(2025, 6, 6), D("5.75")), (dt.date(2025, 12, 5), D("5.50"))]


# ------------------------------------------------------------------ pure maths
def test_the_appointed_day_is_the_agreed_terms_capped_at_45_days():
    d = dt.date(2026, 1, 1)
    assert msme.appointed_day(d, dt.date(2026, 1, 31)) == dt.date(2026, 1, 31)      # 30 days agreed
    assert msme.appointed_day(d, dt.date(2026, 3, 1)) == dt.date(2026, 2, 15)       # 59 days agreed: capped at 45
    assert msme.appointed_day(d, d) == dt.date(2026, 1, 16)                         # nothing agreed: 15 days
    assert msme.appointed_day(d, None) == dt.date(2026, 1, 16)


def test_interest_compounds_monthly_at_three_times_the_bank_rate():
    rates = [(dt.date(2025, 6, 6), D("5.75"))]                                     # 3 x 5.75 = 17.25% a year = 1.4375% a month
    a = dt.date(2026, 1, 1)
    assert msme.interest(D("100000"), a, a, rates) == D("0.00")                    # not late yet
    assert msme.interest(D("100000"), a, a + dt.timedelta(days=1), rates) == D("46.37")           # one day of a 31-day month
    assert msme.interest(D("100000"), a, dt.date(2026, 2, 1), rates) == D("1437.50")              # exactly one month
    assert msme.interest(D("100000"), a, dt.date(2026, 3, 1), rates) == D("2895.66")              # the second month earns interest on the first: 100,000 x 1.014375^2
    assert msme.interest(D("0"), a, dt.date(2026, 6, 1), rates) == D("0.00")
    # the rate in force when each month begins is the one used
    changing = [(dt.date(2025, 6, 6), D("6.00")), (dt.date(2026, 2, 2), D("4.00"))]
    two = msme.interest(D("100000"), a, dt.date(2026, 3, 1), changing)
    assert two == D("2515.00")                                                      # 1.5% then 1.0%: 100,000 x 1.015 x 1.01 - 100,000
    with pytest.raises(msme.MsmeError):
        msme.interest(D("1"), dt.date(2020, 1, 1), dt.date(2020, 3, 1), rates)


def test_udyam_classification_needs_both_limits():
    cr = D(10_000_000)
    assert msme.classify(D("2") * cr, D("9") * cr) == "micro"
    assert msme.classify(D("2") * cr, D("11") * cr) == "small"                      # turnover over the micro limit
    assert msme.classify(D("30") * cr, D("50") * cr) == "medium"                    # investment over the small limit
    assert msme.classify(D("200") * cr, D("50") * cr) == "above_medium"
    assert msme.classify(None, D("50") * cr) is None
    t = msme.tests("small", D("5") * cr, D("50") * cr)
    assert [(x["key"], x["pct"]) for x in t] == [("investment", 20.0), ("turnover", 50.0)]
    assert msme.tests(None, None, None) == []
    assert msme.tests("micro", None, None)[0]["value"] is None


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


def invoice(db, entity, reg, party, number, age, terms, total="11800", status="issued", paid="unpaid"):
    doc = TODAY - dt.timedelta(days=age)
    return db.execute("""INSERT INTO sales_documents (entity_id, doc_type, doc_date, registration_id, supplier_state, party_id, buyer_legal_name, bill_to, place_of_supply, supply_type, number, due_date,
                                                      taxable_total, cgst_total, sgst_total, grand_total, status, payment_status)
                         VALUES (%s, 'tax_invoice', %s, %s, '29', %s, 'Buyer', '{}', '29', 'b2c_small', %s, %s, 10000, 900, 900, %s, %s, %s) RETURNING id""",
                      (entity, doc, reg, party, number, doc + dt.timedelta(days=terms), total, status, paid)).fetchone()["id"]


@pytest.fixture(scope="module", autouse=True)
def seed(db):
    db.execute("INSERT INTO organizations (id, name, kind) VALUES (%s, 'MSME Test Org', 'business_group')", (ORG,))
    for en, name, cls, num, cin in ((EN, "Small Forge Pvt Ltd", "small", "UDYAM-KA-03-0054321", "U28900KA2019PTC090001"), (EN_MED, "Medium Works Pvt Ltd", "medium", "UDYAM-KA-03-0054322", "U28900KA2019PTC090002"),
                                    (EN_NONE, "No Udyam Pvt Ltd", None, None, "U28900KA2019PTC090003")):
        db.execute("INSERT INTO entities (id, organization_id, legal_name, constitution, cin, track, udyam_class, udyam_number) VALUES (%s, %s, %s, 'private_limited', %s, 'msme', %s, %s)", (en, ORG, name, cin, cls, num))
    for reg, en, n in ((REG, EN, "29AAACS9001A1Z5"), (REG_MED, EN_MED, "29AAACM9002B1Z5"), (REG_NONE, EN_NONE, "29AAACN9003C1Z5")):
        db.execute("INSERT INTO entity_registrations (id, entity_id, kind, number, state_code, gst_filing_frequency) VALUES (%s, %s, 'gstin', %s, '29', 'monthly')", (reg, en, n))
    db.execute("INSERT INTO users (id, email, full_name) VALUES (%s, %s, 'Ms Owner'), (%s, %s, 'Ms Viewer'), (%s, %s, 'Ms Outsider')", (U_OWNER, OWNER, U_VIEWER, VIEWER, U_OUT, OUTSIDER))
    for en, role, u in ((EN, "owner", U_OWNER), (EN, "viewer", U_VIEWER), (EN_MED, "owner", U_OWNER), (EN_NONE, "owner", U_OWNER)):
        db.execute("INSERT INTO memberships (entity_id, user_id, role, accepted_at) VALUES (%s, %s, %s, now())", (en, u, role))
    for en, pid, name in ((EN, BUYER, "Rathi Auto"), (EN, BUYER2, "Nilgiri Tools"), (EN_MED, BUYER_MED, "Big Buyer")):
        db.execute("INSERT INTO parties (id, entity_id, legal_name, is_customer, payment_terms_days, status) VALUES (%s, %s, %s, true, 30, 'active')", (pid, en, name))
    S["late1"] = invoice(db, EN, REG, BUYER, "SF/001", 100, 30)             # due 70 days ago
    S["late2"] = invoice(db, EN, REG, BUYER, "SF/002", 50, 30)              # due 20 days ago
    S["ontime"] = invoice(db, EN, REG, BUYER, "SF/003", 10, 30)             # not due yet
    S["paid"] = invoice(db, EN, REG, BUYER, "SF/004", 120, 30, paid="paid")
    db.execute("UPDATE sales_documents SET amount_settled = grand_total WHERE id = %s", (S["paid"],))
    S["other"] = invoice(db, EN, REG, BUYER2, "SF/005", 80, 30)
    S["draft"] = invoice(db, EN, REG, BUYER2, "SF/006", 80, 30, status="draft")
    S["med"] = invoice(db, EN_MED, REG_MED, BUYER_MED, "MW/001", 100, 30)
    S["none"] = invoice(db, EN_NONE, REG_NONE, BUYER_MED, "NU/001", 100, 30)
    yield


def url(path, en=EN):
    return f"/v1/entities/{en}{path}"


def rates(db):
    return [(r["effective_from"], r["rate_pct"]) for r in db.execute("SELECT effective_from, rate_pct FROM ref_rbi_bank_rate ORDER BY effective_from").fetchall()]


def test_the_rate_history_is_a_numbered_migration(db):
    got = rates(db)
    assert got and got == sorted(got) and all(D("0") < r < D("20") for _, r in got)


def test_late_buyers_are_grouped_with_the_interest_they_owe(client, db):
    o = login(client, OWNER)
    r = client.get(url("/msme/late-buyers"), headers=o)
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["supplier_class"] == "small" and j["interest_applies"] is True and j["rate"]["annual_pct"] == str(3 * D(j["rate"]["bank_rate_pct"]))
    assert [b["name"] for b in j["buyers"]] == ["Rathi Auto", "Nilgiri Tools"]                                          # most days late first
    rathi = j["buyers"][0]
    assert [i["number"] for i in rathi["invoices"]] == ["SF/001", "SF/002"] and rathi["days_late"] == 70 and rathi["principal"] == "23600.00"   # the on-time and the paid invoice are left out
    appointed1 = TODAY - dt.timedelta(days=70)
    expect = msme.interest(D("11800"), appointed1, TODAY, rates(db)) + msme.interest(D("11800"), TODAY - dt.timedelta(days=20), TODAY, rates(db))
    assert D(rathi["interest"]) == expect and expect > 0
    assert rathi["invoices"][0]["appointed_on"] == str(appointed1) and rathi["invoices"][0]["days_late"] == 70
    assert j["totals"] == {"buyers": 2, "invoices": 3, "principal": "35400.00", "interest": j["totals"]["interest"]}      # the draft invoice is not counted
    assert D(j["totals"]["interest"]) == D(rathi["interest"]) + D(j["buyers"][1]["interest"])
    past = client.get(url("/msme/late-buyers"), headers=o, params={"as_of": str(TODAY - dt.timedelta(days=60))}).json()   # as of an earlier day
    assert past["totals"]["invoices"] == 1 and past["buyers"][0]["days_late"] == 10
    assert client.get(url("/msme/late-buyers"), headers=login(client, VIEWER)).status_code == 200                       # a viewer can read it
    assert client.get(url("/msme/late-buyers"), headers=login(client, OUTSIDER)).status_code == 404


def test_a_medium_supplier_sees_the_late_buyers_but_no_interest(client):
    j = client.get(url("/msme/late-buyers", EN_MED), headers=login(client, OWNER)).json()
    assert j["supplier_class"] == "medium" and j["interest_applies"] is False and j["totals"]["interest"] is None
    assert j["buyers"][0]["interest"] is None and j["buyers"][0]["invoices"][0]["interest"] is None and j["totals"]["principal"] == "11800.00"


def test_a_claim_is_drafted_from_the_invoices_and_filed_by_a_job(client, db):
    from app import worker
    o, v = login(client, OWNER), login(client, VIEWER)
    body = {"buyer_party_id": BUYER, "invoice_ids": [str(S["late1"]), str(S["late2"])]}
    assert client.post(url("/delayed-payment-claims"), headers=v, json=body).status_code == 403
    assert client.post(url("/delayed-payment-claims", EN_MED), headers=o, json={"buyer_party_id": BUYER_MED, "invoice_ids": [str(S["med"])]}).status_code == 409   # medium: no Sec 16 rights
    for bad in ([str(S["ontime"])], [str(S["paid"])], [str(S["other"])], [str(S["draft"])], [str(uuid.uuid4())]):
        assert client.post(url("/delayed-payment-claims"), headers=o, json={"buyer_party_id": BUYER, "invoice_ids": bad}).status_code == 400
    c = client.post(url("/delayed-payment-claims"), headers=o, json=body)
    assert c.status_code == 201, c.text
    claim = c.json()
    S["claim"] = claim["id"]
    assert claim["status"] == "draft" and claim["principal"] == "23600.00" and claim["buyer_name"] == "Rathi Auto" and [i["number"] for i in claim["invoices"]] == ["SF/001", "SF/002"]
    assert D(claim["interest_claimed"]) == D(client.get(url("/msme/late-buyers"), headers=o).json()["buyers"][0]["interest"])
    assert client.post(url("/delayed-payment-claims"), headers=o, json={"buyer_party_id": BUYER, "invoice_ids": [str(S["late1"])]}).status_code == 409           # already in a claim
    buyers = client.get(url("/msme/late-buyers"), headers=o).json()["buyers"]
    assert buyers[0]["claim"] == {"id": S["claim"], "status": "draft"}

    assert client.post(url(f"/delayed-payment-claims/{S['claim']}/file"), headers=o).status_code == 400                       # an Idempotency-Key is required
    key = idem(o)
    job = client.post(url(f"/delayed-payment-claims/{S['claim']}/file"), headers=key)
    assert job.status_code == 202 and job.json()["kind"] == "msme.file_claim"
    assert client.post(url(f"/delayed-payment-claims/{S['claim']}/file"), headers=key).json()["id"] == job.json()["id"]                      # the same key replays
    assert db.execute("SELECT status FROM delayed_payment_claims WHERE id = %s", (S["claim"],)).fetchone()["status"] == "draft"            # nothing is filed inside the request
    db.execute("UPDATE jobs SET status = 'cancelled' WHERE status = 'queued' AND kind IN ('compliance.run_engine', 'collections.dunning', 'collections.recurring', 'purchases.msme_alerts')")
    n = 0
    while worker.run_once():
        n += 1
        assert n < 20
    done = client.get(url(f"/delayed-payment-claims/{S['claim']}"), headers=o).json()
    assert done["status"] == "filed" and done["case_no"].startswith("SAM/") and done["filed_on"] == str(TODAY) and done["forum"] == "msme_samadhaan"
    assert client.post(url(f"/delayed-payment-claims/{S['claim']}/file"), headers=idem(o)).status_code == 409                          # filed once
    assert [x["id"] for x in client.get(url("/delayed-payment-claims"), headers=o).json()["data"]] == [S["claim"]]
    assert client.get(url(f"/delayed-payment-claims/{S['claim']}"), headers=login(client, OUTSIDER)).status_code == 404


def test_treds_uploads_move_forward_with_the_platform(client):
    o, v = login(client, OWNER), login(client, VIEWER)
    body = {"sales_document_id": str(S["other"]), "platform": "RXIL"}
    assert client.post(url("/treds-invoices"), headers=idem(v), json=body).status_code == 403
    assert client.post(url("/treds-invoices", EN_NONE), headers=idem(o), json={"sales_document_id": str(S["none"]), "platform": "RXIL"}).status_code == 409     # not a registered MSME
    for bad in (S["draft"], S["paid"], uuid.uuid4()):
        assert client.post(url("/treds-invoices"), headers=idem(o), json={"sales_document_id": str(bad), "platform": "RXIL"}).status_code in (400, 409)
    assert client.post(url("/treds-invoices"), headers=idem(o), json={**body, "platform": "Nope"}).status_code in (400, 422)
    key = idem(o)
    t = client.post(url("/treds-invoices"), headers=key, json=body)
    assert t.status_code == 201, t.text
    S["treds"] = t.json()["id"]
    assert t.json()["status"] == "uploaded" and t.json()["invoice_number"] == "SF/005" and t.json()["buyer_name"] == "Nilgiri Tools" and t.json()["invoice_total"] == "11800.00"
    assert client.post(url("/treds-invoices"), headers=key, json=body).json()["id"] == S["treds"]                         # replay
    assert client.post(url("/treds-invoices"), headers=idem(o), json=body).status_code == 409                             # already on TReDS
    p = url(f"/treds-invoices/{S['treds']}")
    assert client.patch(p, headers=o, json={"status": "financed", "discount_rate_pct": "7.5", "amount_received": "11000"}).status_code == 409    # cannot skip steps
    assert client.patch(p, headers=o, json={"status": "accepted"}).json()["buyer_accepted_on"] == str(TODAY)
    assert client.patch(p, headers=o, json={"status": "auctioned"}).json()["status"] == "auctioned"
    assert client.patch(p, headers=o, json={"status": "financed"}).status_code == 400                                       # needs the rate and the money
    fin = client.patch(p, headers=o, json={"status": "financed", "discount_rate_pct": "7.5", "amount_received": "11000"}).json()
    assert fin["status"] == "financed" and fin["discount_rate_pct"] == "7.500" and fin["amount_received"] == "11000.00" and fin["financed_on"] == str(TODAY)
    assert client.patch(p, headers=o, json={"status": "settled"}).json()["status"] == "settled"
    assert client.patch(p, headers=o, json={"status": "rejected"}).status_code == 409                                       # settled is final
    assert [x["id"] for x in client.get(url("/treds-invoices"), headers=o).json()["data"]] == [S["treds"]]
    assert client.patch(url(f"/treds-invoices/{uuid.uuid4()}"), headers=o, json={"status": "accepted"}).status_code == 404


def test_facts_can_be_recorded_and_feed_the_classification(client, db):
    o, v = login(client, OWNER), login(client, VIEWER)
    assert client.get(url("/msme/classification"), headers=o).json() == {"declared": "small", "udyam_number": "UDYAM-KA-03-0054321", "computed": None, "investment": None, "turnover": None,
                                                                            "tests": [{"key": "investment", "label": "Investment in plant and machinery", "value": None, "limit": "250000000.00", "pct": None},
                                                                                      {"key": "turnover", "label": "Annual turnover", "value": None, "limit": "1000000000.00", "pct": None}]}
    cr = 10_000_000
    rec = client.post(url("/facts"), headers=o, json=[{"fact_key": "investment_plant_machinery", "value": 5 * cr, "as_of": str(TODAY)}, {"fact_key": "aato", "value": 50 * cr, "as_of": str(TODAY)}])
    assert rec.status_code == 201, rec.text
    assert {x["fact_key"]: x["value"] for x in rec.json()} == {"aato": 50 * cr, "investment_plant_machinery": 5 * cr} and {x["source"] for x in rec.json()} == {"user"}
    c = client.get(url("/msme/classification"), headers=o).json()
    assert c["computed"] == "small" and [(t["key"], t["pct"]) for t in c["tests"]] == [("investment", 20.0), ("turnover", 50.0)]
    assert db.execute("SELECT count(*) AS n FROM engine_runs WHERE entity_id = %s AND trigger = 'fact_change'", (EN,)).fetchone()["n"] == 1        # a changed fact re-runs the rules
    client.post(url("/facts"), headers=o, json=[{"fact_key": "aato", "value": 60 * cr, "as_of": str(TODAY)}])
    now = {x["fact_key"]: x["value"] for x in client.get(url("/facts"), headers=o).json()}
    assert now["aato"] == 60 * cr                                                                                          # the latest value wins
    assert len([x for x in client.get(url("/facts"), headers=o, params={"history": "true"}).json() if x["fact_key"] == "aato"]) == 2
    txt = client.post(url("/facts"), headers=o, json=[{"fact_key": "udyam_note", "value": "renewed", "as_of": str(TODAY)}, {"fact_key": "has_export_orders", "value": True, "as_of": str(TODAY)}])
    assert {x["fact_key"]: x["value"] for x in txt.json()} == {"udyam_note": "renewed", "has_export_orders": True}
    assert client.post(url("/facts"), headers=o, json=[{"fact_key": "aato", "value": 1, "as_of": str(TODAY + dt.timedelta(days=1))}]).status_code == 400
    assert client.post(url("/facts"), headers=o, json=[{"fact_key": "aato", "value": -5, "as_of": str(TODAY)}]).status_code == 400
    assert client.post(url("/facts"), headers=o, json=[{"fact_key": "Bad Key", "value": 1, "as_of": str(TODAY)}]).status_code in (400, 422)
    assert client.post(url("/facts"), headers=o, json=[]).status_code == 400
    assert client.post(url("/facts"), headers=v, json=[{"fact_key": "aato", "value": 1, "as_of": str(TODAY)}]).status_code == 403
    assert client.get(url("/facts"), headers=login(client, OUTSIDER)).status_code == 404


def test_the_udyam_registration_can_be_recorded(client, db):
    o, v = login(client, OWNER), login(client, VIEWER)
    assert client.patch(url("", EN_NONE), headers=v, json={"udyam_class": "micro"}).status_code in (403, 404)
    assert client.patch(url("", EN_NONE), headers=o, json={}).status_code == 400
    assert client.patch(url("", EN_NONE), headers=o, json={"udyam_class": "micro"}).status_code == 400                         # a class needs the registration number
    assert client.patch(url("", EN_NONE), headers=o, json={"udyam_number": "not-a-number", "udyam_class": "micro"}).status_code in (400, 422)
    ok = client.patch(url("", EN_NONE), headers=o, json={"udyam_number": "UDYAM-KA-03-0054399", "udyam_class": "micro"})
    assert ok.status_code == 200 and ok.json()["udyam_class"] == "micro" and ok.json()["udyam_number"] == "UDYAM-KA-03-0054399"
    assert client.get(url("/msme/classification", EN_NONE), headers=o).json()["declared"] == "micro"
    assert client.patch(url("", EN_NONE), headers=o, json={"track": "startup"}).json()["track"] == "startup"
    assert client.patch(url("", EN), headers=login(client, OUTSIDER), json={"track": "msme"}).status_code == 404
    assert db.execute("SELECT count(*) AS n FROM audit_log WHERE entity_id = %s AND action = 'entity.updated'", (EN_NONE,)).fetchone()["n"] == 2
