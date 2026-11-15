"""Year-end tax declarations (migration 42): the maths (pure), then the employee's own declaration through /self, HR's verification, the TDS that
follows from verified figures only, Form 16 Part B, and the module's confidentiality on a raw connection."""
import datetime as dt
import os
from decimal import Decimal as D

import psycopg
import pytest
from psycopg.rows import dict_row

from app import payroll as pr, taxdecl

DB = os.environ["DATABASE_URL"]
ORG = "00000000-0000-0000-0000-0000007a0000"
EN = "00000000-0000-0000-0000-0000007a0010"
U_OWNER, U_HR, U_SALES = ("00000000-0000-0000-0000-0000007a002%d" % i for i in (1, 2, 3))
OWNER, HR, SALES = "td-owner@example.in", "td-hr@example.in", "td-sales@example.in"
DEV_EMAIL = "td-dev@example.in"
FY = "2026-27"
MONTH = dt.date(2026, 4, 1)
CAPS = {"80C": D(150000), "80CCD1B": D(50000), "80D": D(25000), "24B": D(200000), "80E": None}
S: dict = {}


# ------------------------------------------------------------------ the maths
def item(section, declared, verified=None, status="verified"):
    return {"section": section, "declared_amount": D(declared), "verified_amount": None if verified is None else D(verified), "status": status}


def test_financial_year_labels():
    assert taxdecl.fy_of(dt.date(2026, 4, 1)) == "2026-27" and taxdecl.fy_of(dt.date(2027, 3, 31)) == "2026-27" and taxdecl.fy_of(dt.date(2099, 12, 31)) == "2099-00"
    assert taxdecl.fy_bounds("2026-27") == (dt.date(2026, 4, 1), dt.date(2027, 3, 31))


def test_only_a_verified_declaration_under_the_old_regime_adds_deductions():
    items = [item("80C", 200000, 200000), item("80D", 40000, 40000), item("24B", 10000, None, "pending")]
    d = taxdecl.declared_for_payroll("old", "verified", items, CAPS)
    assert (d.regime, d.claimed_80c, d.other, d.cap_80c) == ("old", D(200000), D(25000), D(150000))             # 80D is held to its cap; the pending item counts for nothing
    assert taxdecl.declared_for_payroll("old", "submitted", items, CAPS) == taxdecl.Declared("old")             # chosen, not yet verified: the regime only
    assert taxdecl.declared_for_payroll("new", "verified", items, CAPS) == taxdecl.Declared("new")
    assert taxdecl.declared_for_payroll("old", "draft", items, CAPS) == taxdecl.Declared(None)
    assert taxdecl.declared_for_payroll("old", "returned", items, CAPS) == taxdecl.Declared(None)


def test_tds_on_salary_uses_the_verified_deductions():
    base = pr.TdsInput("old", D(100000), 0)
    assert pr.tds_on_salary(base) == D(13650)                       # 12 lakh less 50,000: 11.5 lakh, 1,63,800 with cess, over 12 months
    claimed = pr.TdsInput("old", D(100000), 0, declared_80c=D(200000), declared_other=D(25000), cap_80c=D(150000))
    assert D(0) < pr.tds_on_salary(claimed) < D(9750)         # 2,25,000 of deductions in all: 9,75,000 taxable
    only_80c = pr.TdsInput("old", D(100000), 0, declared_80c=D(150000))
    assert pr.tds_on_salary(only_80c) == D(9750)                    # 10 lakh taxable: 1,12,500 and cess is 1,17,000
    assert pr.tds_on_salary(pr.TdsInput("new", D(100000), 0, declared_80c=D(150000))) == pr.tds_on_salary(pr.TdsInput("new", D(100000), 0))     # the new regime has none


def test_form16_part_b_adds_up():
    slips = [{"gross": D(100000), "reimbursements": D(0), "pf_employee": D(0), "tds": D(9750)} for _ in range(12)]
    b = taxdecl.part_b(fy=FY, regime="old", slips=slips, items=[item("80C", 200000, 200000), item("80D", 10000, 10000)], caps=CAPS, verified=True)
    assert b["gross_salary"] == D(1200000) and b["standard_deduction"] == D(50000)
    assert [(x["section"], x["amount"]) for x in b["chapter_vi_a"]] == [("80C", D(150000)), ("80D", D(10000))] and b["chapter_vi_a_total"] == D(160000)
    assert b["taxable_income"] == D(990000) and b["tax_with_cess"] == pr.annual_tax(D(990000), "old")
    assert b["tds_deducted"] == D(117000) and b["difference"] == b["tax_with_cess"] - D(117000) and b["assessment_year"] == "2027-28"
    unverified = taxdecl.part_b(fy=FY, regime="old", slips=slips, items=[item("80C", 200000, 200000)], caps=CAPS, verified=False)
    assert unverified["chapter_vi_a_total"] == D(0) and unverified["taxable_income"] == D(1150000)
    new = taxdecl.part_b(fy=FY, regime="new", slips=slips, items=[item("80C", 200000, 200000)], caps=CAPS, verified=True)
    assert new["standard_deduction"] == D(75000) and new["chapter_vi_a"] == []


# ------------------------------------------------------------------ the API
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
    x = db.execute
    x("INSERT INTO organizations (id, name, kind) VALUES (%s, 'TD Org', 'business_group')", (ORG,))
    x("INSERT INTO entities (id, organization_id, legal_name, constitution, track) VALUES (%s, %s, 'Tax Decl Pvt Ltd', 'private_limited', 'business')", (EN, ORG))
    x("INSERT INTO users (id, email, full_name) VALUES (%s, %s, 'Td Owner'), (%s, %s, 'Td HR'), (%s, %s, 'Td Sales')", (U_OWNER, OWNER, U_HR, HR, U_SALES, SALES))
    x("""INSERT INTO memberships (entity_id, user_id, role, scopes, accepted_at) VALUES (%s, %s, 'owner', NULL, now()),
         (%s, %s, 'approver', '{"hr": "approve"}', now()), (%s, %s, 'maker', '{"sales": "write"}', now())""", (EN, U_OWNER, EN, U_HR, EN, U_SALES))
    yield


def login(client, email):
    r = client.post("/v1/auth/token", json={"grant_type": "otp", "email": email, "otp": "000000"})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def url(path):
    return f"/v1/entities/{EN}{path}"


def me(path=""):
    return f"/v1/self/{S['dev']}{path}"


def test_the_employee_opens_a_declaration_and_sees_the_sections(client, db):
    o, hr = login(client, OWNER), login(client, HR)
    r = client.post(url("/employees/onboard"), headers=o, json={"full_name": "Dev Menon", "date_of_joining": "2026-04-01", "work_state": "29", "ctc_annual": "1800000", "email": DEV_EMAIL})
    assert r.status_code == 201, r.text
    S["dev"] = r.json()["employee"]["id"]
    db.execute("UPDATE employees SET status = 'active', tax_regime = 'new' WHERE id = %s", (S["dev"],))
    assert client.post(url(f"/employees/{S['dev']}/self-service"), headers=hr).status_code == 200
    dev = login(client, DEV_EMAIL)
    assert client.post(f"/v1/me/employments/{S['dev']}/accept", headers=dev).status_code == 200
    d = client.get(me("/tax-declaration"), headers=dev, params={"fy": FY}).json()
    assert (d["status"], d["regime"], d["editable"], d["items"]) == ("none", "new", True, [])
    assert {s["code"]: s["annual_cap"] for s in d["sections"]} == {"80C": "150000.00", "80CCD1B": "50000.00", "80D": "25000.00", "24B": "200000.00", "80E": None}
    assert client.get(me("/tax-declaration"), headers=dev, params={"fy": "2031-32"}).status_code == 404            # no sections set up for that year
    assert client.get(me("/tax-declaration"), headers=dev, params={"fy": "2026-28"}).status_code == 400
    S["dev_h"] = dev


def test_saving_and_sending_a_declaration(client):
    dev = S["dev_h"]
    body = {"regime": "old", "items": [{"section": "80C", "description": "PPF", "declared_amount": "100000.00", "proof_ref": "PPF-123"},
                                      {"section": "80C", "description": "ELSS", "declared_amount": "80000.00"},
                                      {"section": "80D", "description": "Health insurance", "declared_amount": "30000.00"}]}
    bad = client.put(me("/tax-declaration"), headers=dev, params={"fy": FY}, json={**body, "items": [{"section": "99Z", "description": "x", "declared_amount": "1"}]})
    assert bad.status_code == 400 and bad.json()["errors"][0]["field"] == "items.0.section"
    assert client.put(me("/tax-declaration"), headers=dev, params={"fy": FY}, json={**body, "items": [{"section": "80C", "description": "x", "declared_amount": "-1"}]}).status_code in (400, 422)
    assert client.post(me("/tax-declaration/submit"), headers=dev, params={"fy": FY}).status_code == 409           # nothing saved yet
    r = client.put(me("/tax-declaration"), headers=dev, params={"fy": FY}, json=body)
    assert r.status_code == 200, r.text
    d = r.json()
    assert (d["status"], d["regime"], len(d["items"]), d["editable"]) == ("draft", "old", 3, True)
    assert {s["section"]: (s["declared"], s["counts"]) for s in d["summary"]} == {"80C": ("180000.00", "180000.00"), "80D": ("30000.00", "25000.00")}   # held to its cap
    assert client.put(me("/tax-declaration"), headers=dev, params={"fy": FY}, json={**body, "regime": "new", "items": []}).json()["items"] == []        # replaced as a whole
    assert client.put(me("/tax-declaration"), headers=dev, params={"fy": FY}, json=body).status_code == 200
    r = client.post(me("/tax-declaration/submit"), headers=dev, params={"fy": FY})
    assert r.status_code == 200 and r.json()["status"] == "submitted" and r.json()["editable"] is False
    S["decl"] = r.json()["id"]
    assert client.post(me("/tax-declaration/submit"), headers=dev, params={"fy": FY}).json()["status"] == "submitted"       # twice changes nothing
    r = client.put(me("/tax-declaration"), headers=dev, params={"fy": FY}, json=body)
    assert r.status_code == 409, r.text                                                                              # locked while with HR


def test_hr_sees_it_and_verifies_each_figure(client):
    o, hr, sales = login(client, OWNER), login(client, HR), login(client, SALES)
    assert client.get(url("/tax-declarations"), headers=sales, params={"fy": FY}).status_code == 403
    rows = client.get(url("/tax-declarations"), headers=hr, params={"fy": FY}).json()
    assert [(r["employee_name"], r["status"], r["items"], r["declared"]) for r in rows["data"]] == [("Dev Menon", "submitted", 3, "210000.00")]
    one = client.get(url(f"/tax-declarations/{S['decl']}"), headers=hr).json()
    ids = {i["description"]: i["id"] for i in one["items"]}
    assert one["employee_name"] == "Dev Menon"
    bad = client.post(url(f"/tax-declarations/{S['decl']}/verify"), headers=hr, json={"items": [{"id": ids["PPF"], "verified_amount": "100000.01"}]})
    assert bad.status_code == 400                                                                                    # more than declared
    r = client.post(url(f"/tax-declarations/{S['decl']}/verify"), headers=hr,
                    json={"items": [{"id": ids["ELSS"], "status": "rejected", "note": "No statement"}, {"id": ids["Health insurance"], "verified_amount": "20000.00"}], "note": "Checked"})
    assert r.status_code == 200, r.text
    v = r.json()
    assert v["status"] == "verified" and v["hr_note"] == "Checked"
    by = {i["description"]: (i["status"], i["verified_amount"]) for i in v["items"]}
    assert by == {"PPF": ("verified", "100000.00"), "ELSS": ("rejected", None), "Health insurance": ("verified", "20000.00")}
    assert client.post(url(f"/tax-declarations/{S['decl']}/verify"), headers=hr, json={}).status_code == 409        # already verified
    mine = client.get(me("/tax-declaration"), headers=S["dev_h"], params={"fy": FY}).json()
    assert mine["status"] == "verified" and mine["editable"] is False
    assert {s["section"]: s["counts"] for s in mine["summary"]} == {"80C": "100000.00", "80D": "20000.00"}


def run_tds(client, o, create: bool = False) -> D:
    if create:
        r = client.post(url("/payroll-runs"), headers=o, json={"period_month": str(MONTH)})
        assert r.status_code == 201, r.text
        S["run"] = r.json()["id"]
    assert client.post(url(f"/payroll-runs/{S['run']}/compute"), headers=o).json()["status"] == "computed"
    rows = client.get(url(f"/payroll-runs/{S['run']}/payslips"), headers=o).json()["data"]
    assert len(rows) == 1
    return D(str(rows[0]["tds"]))


def test_tds_follows_the_regime_and_only_verified_figures(client, db):
    o, hr = login(client, OWNER), login(client, HR)
    db.execute("UPDATE tax_declarations SET status = 'returned', verified_at = NULL, verified_by = NULL WHERE id = %s", (S["decl"],))    # as if HR had not verified yet
    with_regime_only = run_tds(client, o, create=True)
    db.execute("UPDATE tax_declarations SET status = 'draft' WHERE id = %s", (S["decl"],))
    new_regime = run_tds(client, o)
    assert with_regime_only == new_regime                                                                             # a returned or draft declaration changes nothing
    db.execute("UPDATE tax_declarations SET status = 'submitted' WHERE id = %s", (S["decl"],))
    old_unverified = run_tds(client, o)
    assert old_unverified != new_regime                                               # the regime they chose applies at once, the deductions do not
    db.execute("UPDATE tax_declarations SET status = 'verified', verified_at = now(), verified_by = %s WHERE id = %s", (U_HR, S["decl"]))
    old_verified = run_tds(client, o)
    assert old_verified < old_unverified                                                                              # 1,00,000 under 80C and 20,000 under 80D now count
    expected = pr.tds_on_salary(pr.TdsInput("old", D(150000), 0, declared_80c=D(100000), declared_other=D(20000)))
    assert old_verified == expected
    back = client.post(url(f"/tax-declarations/{S['decl']}/return"), headers=hr, json={"note": "Please attach the PPF passbook"})
    assert back.status_code == 200 and back.json()["status"] == "returned" and {i["status"] for i in back.json()["items"]} == {"pending"}
    assert client.post(url(f"/tax-declarations/{S['decl']}/return"), headers=hr, json={"note": "again"}).status_code == 409
    assert client.post(url(f"/tax-declarations/{S['decl']}/return"), headers=hr, json={"note": ""}).status_code in (400, 422)
    assert run_tds(client, o) == new_regime                                                                           # returned: no deductions, and the regime is the record's again
    mine = client.get(me("/tax-declaration"), headers=S["dev_h"], params={"fy": FY}).json()
    assert mine["status"] == "returned" and mine["editable"] is True and mine["hr_note"] == "Please attach the PPF passbook"


def test_form16_part_b_from_approved_payslips(client, db):
    o, hr, dev = login(client, OWNER), login(client, HR), S["dev_h"]
    assert client.get(me("/form16"), headers=dev, params={"fy": FY}).json()["months"] == 0                              # the run is not approved yet
    client.put(me("/tax-declaration"), headers=dev, params={"fy": FY}, json={"regime": "old", "items": [{"section": "80C", "description": "PPF", "declared_amount": "100000"}]})
    d = client.post(me("/tax-declaration/submit"), headers=dev, params={"fy": FY}).json()
    assert client.post(url(f"/tax-declarations/{d['id']}/verify"), headers=hr, json={}).status_code == 200
    db.execute("UPDATE payroll_runs SET status = 'approved' WHERE id = %s", (S["run"],))
    slip = db.execute("SELECT gross, tds, pf_employee FROM payslips WHERE run_id = %s", (S["run"],)).fetchone()
    f = client.get(me("/form16"), headers=dev, params={"fy": FY}).json()
    assert (f["months"], f["regime"], f["gross_salary"], f["deductions_verified"]) == (1, "old", f"{slip['gross']:.2f}", True)
    assert f["chapter_vi_a_total"] == "100000.00" and f["tds_deducted"] == f"{slip['tds']:.2f}" and f["standard_deduction"] == "50000.00"
    taxable = slip["gross"] - 50000 - 100000
    assert f["taxable_income"] == f"{taxable:.2f}" and f["tax_with_cess"] == f"{pr.annual_tax(taxable, 'old'):.2f}"
    assert client.get(url(f"/employees/{S['dev']}/form16"), headers=hr, params={"fy": FY}).json() == f | {"employee_name": f["employee_name"]}
    assert client.get(url(f"/employees/{S['dev']}/form16"), headers=login(client, SALES), params={"fy": FY}).status_code == 403


def test_someone_else_s_declaration_is_closed_to_them_and_to_the_wrong_module(client, db):
    sales, hr = login(client, SALES), login(client, HR)
    assert client.get(me("/tax-declaration"), headers=sales).status_code == 404                                         # not their record
    assert client.put(me("/tax-declaration"), headers=sales, json={"regime": "old", "items": []}).status_code == 404
    assert client.get(url(f"/tax-declarations/{S['decl']}"), headers=sales).status_code == 403
    # a raw connection as a member without payroll or HR reads nothing; the employee's role reads no table at all
    with psycopg.connect(DB, autocommit=False, row_factory=dict_row) as c:
        c.execute("SET LOCAL ROLE biz_app")
        c.execute("SELECT set_config('app.user_id', %s, true)", (U_SALES,))
        assert c.execute("SELECT count(*) AS n FROM tax_declarations").fetchone()["n"] == 0
        assert c.execute("SELECT count(*) AS n FROM tax_declaration_items").fetchone()["n"] == 0
        c.rollback()
        c.execute("SET LOCAL ROLE biz_app")
        c.execute("SELECT set_config('app.user_id', %s, true)", (U_HR,))
        assert c.execute("SELECT count(*) AS n FROM tax_declarations").fetchone()["n"] == 1
        c.rollback()
        c.execute("SET LOCAL ROLE biz_self")
        for t in ("tax_declarations", "tax_declaration_items", "tax_deduction_sections"):
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                c.execute(f"SELECT 1 FROM {t}")
            c.rollback()
            c.execute("SET LOCAL ROLE biz_self")
