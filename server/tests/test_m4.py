"""M4 end to end: employees and salary structures, a payroll run from inputs to bank file, the payroll journal,
statutory challans through the obligations, the wage-code check, and labour compliance.

Own entity (EP); tests run in order and build on one another.
"""
import datetime as dt
import os
import uuid
from decimal import Decimal as D

import psycopg
import pytest
from psycopg.rows import dict_row

from app import payroll as pr

DB = os.environ["DATABASE_URL"]
ORG = "00000000-0000-0000-0000-0000000000a1"
EP = "00000000-0000-0000-0000-0000000000e2"
U_OWNER, U_APPROVER, U_VIEWER = ("00000000-0000-0000-0000-0000000000e%d" % i for i in (3, 4, 5))
BANK_LEDGER, BANK = "00000000-0000-0000-0000-0000000000e6", "00000000-0000-0000-0000-0000000000e7"
OWNER, APPROVER, VIEWER = "m4-owner@example.in", "m4-approver@example.in", "m4-viewer@example.in"
NOV = "2026-11-01"
S: dict = {}
KINDS = "('compliance.run_engine','collections.dunning','collections.recurring','purchases.msme_alerts','einvoice.generate_irn')"

# code, name, PAN, monthly components (code, amount, in_wages)
STAFF = [
    ("EMP-1", "Meera Iyer", "AAAPI1111A", [("BASIC", 120000, True), ("HRA", 48000, False), ("SPECIAL", 72000, False)]),
    ("EMP-2", "Arjun Shetty", "AAAPS2222B", [("BASIC", 55000, True), ("HRA", 22000, False), ("SPECIAL", 33000, False)]),
    ("EMP-3", "Kiran Gowda", "AAAPG3333C", [("BASIC", 22500, True), ("SPECIAL", 22500, False)]),
    ("EMP-4", "Lakshmi N", "AAAPN4444D", [("BASIC", 16000, True), ("SPECIAL", 16000, False)]),
    ("EMP-5", "Sneha R", "AAAPR5555E", [("BASIC", 10000, True), ("HRA", 5000, False), ("SPECIAL", 6000, False)]),
    ("EMP-6", "Imran Pasha", "AAAPP6666F", [("BASIC", 9000, True), ("SPECIAL", 10500, False)]),
]


@pytest.fixture(scope="module")
def db():
    with psycopg.connect(DB, autocommit=True, row_factory=dict_row) as c:
        yield c


@pytest.fixture(scope="module", autouse=True)
def entity(db):
    db.execute("INSERT INTO entities (id, organization_id, legal_name, constitution, track) VALUES (%s, %s, 'M4 Test Pvt Ltd', 'private_limited', 'business')", (EP, ORG))
    db.execute("""INSERT INTO entity_registrations (entity_id, kind, number, state_code, valid_until) VALUES
                  (%s, 'tan', 'BLRM44444D', '29', NULL), (%s, 'epf', 'KNBLR0044444000', '29', NULL), (%s, 'esic', '31000444440000999', '29', NULL),
                  (%s, 'professional_tax', 'PT-KA-4444', '29', NULL), (%s, 'shops_establishment', 'SHOP-4444', '29', '2027-01-15')""", (EP,) * 5)
    db.execute("INSERT INTO users (id, email, full_name) VALUES (%s, %s, 'M4 Owner'), (%s, %s, 'M4 Approver'), (%s, %s, 'M4 Viewer')", (U_OWNER, OWNER, U_APPROVER, APPROVER, U_VIEWER, VIEWER))
    db.execute("INSERT INTO memberships (entity_id, user_id, role) VALUES (%s, %s, 'owner'), (%s, %s, 'approver'), (%s, %s, 'viewer')", (EP, U_OWNER, EP, U_APPROVER, EP, U_VIEWER))
    db.execute("INSERT INTO ledger_accounts (id, entity_id, code, name, type, subtype) VALUES (%s, %s, '1100', 'HDFC current', 'asset', 'bank')", (BANK_LEDGER, EP))
    db.execute("INSERT INTO bank_accounts (id, entity_id, ledger_account_id, bank_name, account_last4, ifsc, kind) VALUES (%s, %s, %s, 'HDFC', '7001', 'HDFC0000123', 'current')", (BANK, EP, BANK_LEDGER))
    db.execute("INSERT INTO entity_facts (entity_id, fact_key, value_num, as_of, source) VALUES (%s, 'headcount', 25, '2026-04-01', 'user')", (EP,))
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
    return f"/v1/entities/{EP}{path}"


def key():
    return {"Idempotency-Key": str(uuid.uuid4())}


def run_worker(db):
    from app import worker
    db.execute(f"UPDATE jobs SET status = 'cancelled' WHERE status = 'queued' AND kind IN {KINDS}")
    n = 0
    while worker.run_once():
        n += 1
        assert n < 50


def emp_body(code, name, pan, joined="2020-01-01", **kw):
    return {"employee_code": code, "full_name": name, "pan": pan, "date_of_joining": joined, "work_state": "29", "designation": "Staff",
            "bank_account": {"account_no": "50100" + code[-1] * 6, "ifsc": "HDFC0000123"}, **kw}


def structure_body(comps, frm="2025-04-01"):
    monthly = sum(a for _, a, _ in comps)
    return {"effective_from": frm, "ctc_annual": f"{monthly * 12}.00", "components": [{"code": c, "monthly_amount": f"{a}.00", "kind": "earning", "in_wages": w} for c, a, w in comps]}


def payslips(client, h, run_id):
    return {p["employee_name"]: p for p in client.get(url(f"/payroll-runs/{run_id}/payslips"), headers=h).json()["data"]}


# ================================================================== employees
def test_create_and_read_employees(client, h, hv, db):
    assert client.post(url("/employees"), headers=hv, json=emp_body("EMP-1", "Meera Iyer", "AAAPI1111A")).json()["code"] == "permission_denied"
    assert client.post(url("/employees"), headers=h, json=emp_body("EMP-X", "Bad Pan", "123")).status_code == 400
    for code, name, pan, _ in STAFF:
        r = client.post(url("/employees"), headers=h, json=emp_body(code, name, pan))
        assert r.status_code == 201, r.text
        S[code] = r.json()
    e = S["EMP-1"]
    assert e["employee_code"] == "EMP-1" and e["status"] == "active" and e["tax_regime"] == "new" and e["current_structure"] is None
    assert e["bank_account"] == {"account_last4": "1111", "ifsc": "HDFC0000123"} and "account_no" not in e["bank_account"]
    raw = db.execute("SELECT bank_account_enc FROM employees WHERE id = %s", (e["id"],)).fetchone()["bank_account_enc"]
    assert b"501001111111" not in bytes(raw)                                                  # the account number is encrypted at rest
    assert client.post(url("/employees"), headers=h, json=emp_body("EMP-1", "Again", "AAAPA9999Z")).json()["code"] == "conflict"


def test_update_and_list_employees(client, h):
    r = client.patch(url(f"/employees/{S['EMP-3']['id']}"), headers=h, json={"designation": "Sales lead", "bank_account": {"account_no": "9988776655", "ifsc": "ICIC0000456"}})
    assert r.status_code == 200 and r.json()["designation"] == "Sales lead" and r.json()["bank_account"] == {"account_last4": "6655", "ifsc": "ICIC0000456"}
    assert client.patch(url(f"/employees/{S['EMP-3']['id']}"), headers=h, json={"pan": "bad"}).status_code == 400
    assert client.patch(url(f"/employees/{uuid.uuid4()}"), headers=h, json={}).status_code == 404
    listed = client.get(url("/employees"), headers=h).json()
    assert [e["employee_code"] for e in listed["data"]] == [f"EMP-{i}" for i in range(1, 7)]
    assert [e["full_name"] for e in client.get(url("/employees"), headers=h, params={"q": "imran"}).json()["data"]] == ["Imran Pasha"]
    assert client.get(url("/employees"), headers=h, params={"limit": 2}).json()["next_cursor"] == "2"


# ================================================================== salary structures
def test_salary_structures(client, h, hv):
    bad_ctc = {**structure_body(STAFF[0][3]), "ctc_annual": "1.00"}
    assert client.post(url(f"/employees/{S['EMP-1']['id']}/salary-structures"), headers=h, json=bad_ctc).status_code == 400
    no_wages = structure_body([("HRA", 10000, False)])
    assert client.post(url(f"/employees/{S['EMP-1']['id']}/salary-structures"), headers=h, json=no_wages).status_code == 400
    dup = structure_body(STAFF[0][3])
    dup["components"].append(dup["components"][0])
    assert client.post(url(f"/employees/{S['EMP-1']['id']}/salary-structures"), headers=h, json=dup).status_code == 400
    assert client.post(url(f"/employees/{S['EMP-1']['id']}/salary-structures"), headers=hv, json=structure_body(STAFF[0][3])).json()["code"] == "permission_denied"
    for code, _, _, comps in STAFF:
        r = client.post(url(f"/employees/{S[code]['id']}/salary-structures"), headers=h, json=structure_body(comps))
        assert r.status_code == 201, r.text
    assert r.json()["effective_to"] is None and len(r.json()["components"]) == 2
    got = client.get(url(f"/employees/{S['EMP-5']['id']}"), headers=h).json()["current_structure"]
    assert got["ctc_annual"] == "252000.00" and [c["code"] for c in got["components"]] == ["BASIC", "HRA", "SPECIAL"]


def test_a_revision_closes_the_previous_structure(client, h, db):
    rev = structure_body([("BASIC", 12000, True), ("HRA", 5000, False), ("SPECIAL", 6000, False)], "2027-04-01")
    r = client.post(url(f"/employees/{S['EMP-5']['id']}/salary-structures"), headers=h, json=rev)
    assert r.status_code == 201
    rows = db.execute("SELECT lower(effective) AS lo, upper(effective) AS up FROM salary_structures WHERE employee_id = %s ORDER BY 1", (S["EMP-5"]["id"],)).fetchall()
    assert rows == [{"lo": dt.date(2025, 4, 1), "up": dt.date(2027, 4, 1)}, {"lo": dt.date(2027, 4, 1), "up": None}]
    assert client.post(url(f"/employees/{S['EMP-5']['id']}/salary-structures"), headers=h, json=structure_body([("BASIC", 1, True)], "2026-01-01")).status_code == 409
    assert client.get(url(f"/employees/{S['EMP-5']['id']}"), headers=h).json()["current_structure"]["ctc_annual"] == "252000.00"       # today still on the first
    assert client.post(url(f"/employees/{uuid.uuid4()}/salary-structures"), headers=h, json=rev).status_code == 404


# ================================================================== wage code
def test_wage_code_check_and_filters(client, h):
    rows = client.get(url("/payroll/wage-code-check"), headers=h).json()
    assert [(r["full_name"], r["wage_pct"], r["statutory_wage"], r["pf_wage_should_be"]) for r in rows] == [
        ("Imran Pasha", 46.2, "9750.00", "9750.00"), ("Sneha R", 47.6, "10500.00", "10500.00")]
    assert {e["full_name"] for e in client.get(url("/employees"), headers=h, params={"wage_code_flag": "true"}).json()["data"]} == {"Sneha R", "Imran Pasha"}
    assert {e["full_name"] for e in client.get(url("/employees"), headers=h, params={"esi_covered": "true"}).json()["data"]} == {"Sneha R", "Imran Pasha"}
    assert len(client.get(url("/employees"), headers=h, params={"wage_code_flag": "false"}).json()["data"]) == 4


# ================================================================== payroll run
def test_create_run_and_guards(client, h, hv):
    assert client.post(url("/payroll-runs"), headers=h, json={"period_month": "2026-11-15"}).status_code == 400
    assert client.post(url("/payroll-runs"), headers=hv, json={"period_month": NOV}).json()["code"] == "permission_denied"
    r = client.post(url("/payroll-runs"), headers=h, json={"period_month": NOV})
    assert r.status_code == 201, r.text
    run = r.json()
    assert run["status"] == "inputs_open" and run["headcount"] == 6 and run["gross_total"] == "0.00" and run["statutory"] == []
    assert client.post(url("/payroll-runs"), headers=h, json={"period_month": NOV}).json()["code"] == "conflict"
    S["run"] = run
    assert client.get(url("/payroll-runs"), headers=h, params={"fy": "2026-27"}).json()["data"][0]["id"] == run["id"]
    assert client.get(url("/payroll-runs"), headers=h, params={"fy": "2025-26"}).json()["data"] == []
    assert client.get(url(f"/payroll-runs/{uuid.uuid4()}"), headers=h).status_code == 404


def test_compute_needs_a_structure_for_everyone(client, h):
    late = client.post(url("/employees"), headers=h, json=emp_body("EMP-7", "No Structure", "AAAPN7777G"))
    S["EMP-7"] = late.json()
    r = client.post(url(f"/payroll-runs/{S['run']['id']}/compute"), headers=h)
    assert r.status_code == 409 and "EMP-7" in r.json()["detail"]
    assert client.patch(url(f"/employees/{late.json()['id']}"), headers=h, json={"status": "onboarding"}).status_code == 200            # not on payroll yet
    assert client.post(url(f"/payroll-runs/{S['run']['id']}/compute"), headers=h).status_code == 200


def test_inputs_and_compute(client, h):
    rid = S["run"]["id"]
    bad = client.put(url(f"/payroll-runs/{rid}/inputs"), headers=h, json=[{"employee_id": str(uuid.uuid4()), "lop_days": "1"}])
    assert bad.status_code == 400
    assert client.put(url(f"/payroll-runs/{rid}/inputs"), headers=h, json=[{"employee_id": S["EMP-3"]["id"], "lop_days": "40"}]).status_code == 400
    r = client.put(url(f"/payroll-runs/{rid}/inputs"), headers=h, json=[
        {"employee_id": S["EMP-3"]["id"], "lop_days": "3"}, {"employee_id": S["EMP-4"]["id"], "overtime": "2000", "other_deductions": "500"}])
    assert r.status_code == 200 and r.json()["status"] == "inputs_open"
    got = {x["employee_id"]: x for x in client.get(url(f"/payroll-runs/{rid}/inputs"), headers=h).json()}
    assert set(got) == {S["EMP-3"]["id"], S["EMP-4"]["id"]}
    assert (got[S["EMP-3"]["id"]]["lop_days"], got[S["EMP-3"]["id"]]["overtime"]) == ("3", "0.00")
    assert (got[S["EMP-4"]["id"]]["overtime"], got[S["EMP-4"]["id"]]["other_deductions"]) == ("2000.00", "500.00")
    assert client.get(url(f"/payroll-runs/{uuid.uuid4()}/inputs"), headers=h).status_code == 404
    run = client.post(url(f"/payroll-runs/{rid}/compute"), headers=h).json()
    assert run["status"] == "computed" and run["headcount"] == 6
    ps = payslips(client, h, rid)
    assert len(ps) == 6
    for p in ps.values():
        assert D(p["net_pay"]) == D(p["gross"]) - D(p["pf_employee"]) - D(p["esi_employee"]) - D(p["professional_tax"]) - D(p["tds"]) - D(p["other_deductions"])
    sn = ps["Sneha R"]
    assert (sn["gross"], sn["wages"], sn["pf_wage"], sn["pf_employee"], sn["pf_employer_epf"], sn["pf_employer_eps"]) == ("21000.00", "10500.00", "10500.00", "1260.00", "385.00", "875.00")
    assert (sn["esi_applicable"], sn["esi_employee"], sn["esi_employer"], sn["professional_tax"], sn["tds"], sn["net_pay"], sn["flags"]) == (
        True, "158.00", "683.00", "0.00", "0.00", "19582.00", ["wage_below_50pct"])
    me = ps["Meera Iyer"]
    assert (me["pf_wage"], me["pf_employee"], me["esi_applicable"], me["esi_employee"], me["professional_tax"], me["flags"]) == ("15000.00", "1800.00", False, "0.00", "200.00", [])
    expected_tds = pr.tds_on_salary(pr.TdsInput("new", D(240000), 7, D(0), 0, D(0), D(1800)))
    assert D(me["tds"]) == expected_tds > 0
    ki = ps["Kiran Gowda"]
    assert (ki["paid_days"], ki["lop_days"], ki["gross"]) == (27.0, 3.0, "40500.00")                   # 3 days of loss of pay in a 30-day month
    la = ps["Lakshmi N"]
    assert (la["gross"], la["other_deductions"]) == ("34000.00", "500.00")                                   # 32 000 + 2 000 overtime
    assert {k for k, v in ps.items() if v["flags"]} == {"Imran Pasha", "Sneha R"}
    assert [p["employee_name"] for p in client.get(url(f"/payroll-runs/{rid}/payslips"), headers=h, params={"flag": "wage_below_50pct"}).json()["data"]] == ["Sneha R", "Imran Pasha"]
    page = client.get(url(f"/payroll-runs/{rid}/payslips"), headers=h, params={"limit": 4}).json()
    assert len(page["data"]) == 4 and page["next_cursor"] == "4" and len(client.get(url(f"/payroll-runs/{rid}/payslips"), headers=h, params={"limit": 4, "cursor": "4"}).json()["data"]) == 2
    total_gross = sum(D(p["gross"]) for p in ps.values())
    assert run["gross_total"] == f"{total_gross:.2f}" and run["net_total"] == f"{sum(D(p['net_pay']) for p in ps.values()):.2f}"
    assert D(run["employer_cost_total"]) == total_gross + sum(D(p["pf_employer_epf"]) + D(p["pf_employer_eps"]) + D(p["esi_employer"]) for p in ps.values())
    S["slips"], S["run"] = ps, run


def test_recompute_after_changing_inputs(client, h):
    rid = S["run"]["id"]
    client.put(url(f"/payroll-runs/{rid}/inputs"), headers=h, json=[{"employee_id": S["EMP-3"]["id"], "lop_days": "0"}])
    assert client.get(url(f"/payroll-runs/{rid}"), headers=h).json()["status"] == "inputs_open"
    client.post(url(f"/payroll-runs/{rid}/compute"), headers=h)
    assert payslips(client, h, rid)["Kiran Gowda"]["gross"] == "45000.00"
    client.put(url(f"/payroll-runs/{rid}/inputs"), headers=h, json=[{"employee_id": S["EMP-3"]["id"], "lop_days": "3"}, {"employee_id": S["EMP-4"]["id"], "overtime": "2000", "other_deductions": "500"}])
    S["run"] = client.post(url(f"/payroll-runs/{rid}/compute"), headers=h).json()
    assert payslips(client, h, rid)["Kiran Gowda"]["gross"] == "40500.00" and S["run"]["gross_total"] == S["run"]["gross_total"]


# ================================================================== approval, journal, bank file
def test_submit_and_approve(client, h, ha, hv, db):
    rid = S["run"]["id"]
    assert client.post(url(f"/payroll-runs/{rid}/submit"), headers=hv).json()["code"] == "permission_denied"
    assert client.post(url(f"/payroll-runs/{rid}/bank-file"), headers={**h, **key()}, json={"bank_account_id": BANK}).json()["code"] == "approval_required"
    ar = client.post(url(f"/payroll-runs/{rid}/submit"), headers=h)
    assert ar.status_code == 201 and ar.json()["subject"] == "payroll_run" and "November 2026" in ar.json()["summary"] and ar.json()["amount"] == S["run"]["net_total"]
    assert client.post(url(f"/payroll-runs/{rid}/submit"), headers=h).json()["code"] == "conflict"
    own = client.post(url(f"/approvals/{ar.json()['id']}/approve"), headers={**h, **key()}, json={})
    assert own.status_code == 403 and own.json()["code"] == "two_person_rule"
    assert client.get(url(f"/payroll-runs/{rid}"), headers=h).json()["status"] == "computed"
    ok = client.post(url(f"/approvals/{ar.json()['id']}/approve"), headers={**ha, **key()}, json={})
    assert ok.status_code == 200, ok.text
    run = client.get(url(f"/payroll-runs/{rid}"), headers=h).json()
    assert run["status"] == "approved" and run["approval_request_id"] == ar.json()["id"]
    S["run"] = run


def test_the_payroll_journal_and_tds_deductions(client, h, db):
    rid, ps = S["run"]["id"], S["slips"]
    j = next(x for x in client.get(url("/journals"), headers=h, params={"source": "payroll_run"}).json()["data"] if x["source_id"] == rid)
    names = {a["id"]: a["name"] for a in client.get(url("/accounts"), headers=h, params={"limit": 500}).json()["data"]}
    got = {names[l["account_id"]]: (D(l["debit"]), D(l["credit"])) for l in j["lines"]}
    s = lambda f: sum((D(p[f]) for p in ps.values()), D(0))
    assert got["Salaries and wages"] == (s("gross"), 0)
    assert got["Employer PF and ESI"] == (s("pf_employer_epf") + s("pf_employer_eps") + s("esi_employer"), 0)
    assert got["Salary payable"] == (0, s("net_pay")) and got["PF payable"] == (0, s("pf_employee") + s("pf_employer_epf") + s("pf_employer_eps"))
    assert got["ESI payable"] == (0, s("esi_employee") + s("esi_employer")) and got["Professional tax payable"] == (0, s("professional_tax"))
    assert got["TDS payable"] == (0, s("tds")) and got["Employee recoveries"] == (0, D(500))
    assert sum(v[0] for v in got.values()) == sum(v[1] for v in got.values())
    ded = db.execute("SELECT section, tds_amount, source_kind FROM tds_deductions WHERE source_id = %s", (rid,)).fetchall()
    assert {d["section"] for d in ded} == {"192"} and {d["source_kind"] for d in ded} == {"payroll_run"} and sum(d["tds_amount"] for d in ded) == s("tds")
    assert len(ded) == sum(1 for p in ps.values() if D(p["tds"]) > 0) >= 1
    dues = {d["kind"]: d for d in S["run"]["statutory"]}
    assert {k: v["due_date"] for k, v in dues.items()} == {"TDS on salary": "2026-12-07", "PF": "2026-12-15", "ESI": "2026-12-15", "Professional tax": "2026-12-20"}
    assert D(dues["PF"]["amount"]) == s("pf_employee") + s("pf_employer_epf") + s("pf_employer_eps") and D(dues["Professional tax"]["amount"]) == s("professional_tax")


def test_bank_file_pays_salaries(client, h, hv, db):
    rid = S["run"]["id"]
    assert client.post(url(f"/payroll-runs/{rid}/bank-file"), headers={**hv, **key()}, json={"bank_account_id": BANK}).json()["code"] == "permission_denied"
    assert client.post(url(f"/payroll-runs/{rid}/bank-file"), headers={**h, **key()}, json={"bank_account_id": str(uuid.uuid4())}).status_code == 400
    k = key()
    r = client.post(url(f"/payroll-runs/{rid}/bank-file"), headers={**h, **k}, json={"bank_account_id": BANK, "format": "csv"})
    assert r.status_code == 202 and r.json()["kind"] == "payroll.bank_file"
    assert client.post(url(f"/payroll-runs/{rid}/bank-file"), headers={**h, **k}, json={"bank_account_id": BANK, "format": "csv"}).json()["id"] == r.json()["id"]
    run_worker(db)
    job = db.execute("SELECT status, result FROM jobs WHERE id = %s", (r.json()["id"],)).fetchone()
    assert job["status"] == "succeeded" and job["result"]["batch_ref"].startswith("SAL") and job["result"]["employees"] == 6
    run = client.get(url(f"/payroll-runs/{rid}"), headers=h).json()
    assert run["status"] == "paid" and run["paid_on"]
    j = next(x for x in client.get(url("/journals"), headers=h, params={"source": "bank"}).json()["data"] if x["source_id"] == rid)
    names = {a["id"]: a["name"] for a in client.get(url("/accounts"), headers=h, params={"limit": 500}).json()["data"]}
    assert {names[l["account_id"]]: (l["debit"], l["credit"]) for l in j["lines"]} == {"Salary payable": (run["net_total"], "0.00"), "HDFC current": ("0.00", run["net_total"])}
    assert client.post(url(f"/payroll-runs/{rid}/bank-file"), headers={**h, **key()}, json={"bank_account_id": BANK}).json()["code"] == "conflict"
    S["run"] = run


# ================================================================== statutory challans through the obligations
def test_statutory_challans_come_from_the_approved_run(client, h, db):
    from app import engine
    with db.transaction():
        engine.run_engine(db.cursor(), EP, "manual", dt.date(2026, 12, 1))
    ob = {r["code"]: r["id"] for r in db.execute("""SELECT r.code, o.id FROM obligations o JOIN compliance_rules r ON r.id = o.rule_id
                                                       WHERE o.entity_id = %s AND o.period_start = %s""", (EP, NOV)).fetchall()}
    for code in ("EPF.ECR.MONTHLY", "ESI.MONTHLY", "PT.KA.MONTHLY", "TDS.DEPOSIT.MONTHLY"):
        assert code in ob, sorted(ob)
        assert client.post(url(f"/obligations/{ob[code]}/prepare"), headers=h).status_code == 202
    run_worker(db)
    ch = {c["kind"]: c for c in client.get(url("/challans"), headers=h).json()["data"]}
    dues = {d["kind"]: D(d["amount"]) for d in client.get(url(f"/payroll-runs/{S['run']['id']}"), headers=h).json()["statutory"]}
    assert D(ch["epf_ecr"]["amount"]) == dues["PF"] and D(ch["esic"]["amount"]) == dues["ESI"] and D(ch["professional_tax"]["amount"]) == dues["Professional tax"]
    assert D(ch["itns281_tds"]["amount"]) == dues["TDS on salary"] and ch["epf_ecr"]["obligation_id"] == str(ob["EPF.ECR.MONTHLY"])
    again = client.post(url(f"/obligations/{ob['EPF.ECR.MONTHLY']}/prepare"), headers=h)
    run_worker(db)
    assert len([c for c in client.get(url("/challans"), headers=h, params={"kind": "epf_ecr"}).json()["data"]]) == 1            # re-preparing does not duplicate
    pay = client.post(url(f"/challans/{ch['epf_ecr']['id']}/pay"), headers={**h, **key()})
    run_worker(db)
    assert db.execute("SELECT status FROM jobs WHERE id = %s", (pay.json()["id"],)).fetchone()["status"] == "succeeded"
    j = next(x for x in client.get(url("/journals"), headers=h, params={"source": "bank"}).json()["data"] if x["source_id"] == ch["epf_ecr"]["id"])
    names = {a["id"]: a["name"] for a in client.get(url("/accounts"), headers=h, params={"limit": 500}).json()["data"]}
    assert {names[l["account_id"]] for l in j["lines"] if l["debit"] != "0.00"} == {"PF payable"}
    after = {d["kind"]: d for d in client.get(url(f"/payroll-runs/{S['run']['id']}"), headers=h).json()["statutory"]}
    assert after["PF"]["obligation_id"] == str(ob["EPF.ECR.MONTHLY"]) and after["TDS on salary"]["obligation_id"] == str(ob["TDS.DEPOSIT.MONTHLY"])
    assert db.execute("SELECT count(*) AS n FROM tds_deductions WHERE entity_id = %s AND section = '192' AND challan_id IS NOT NULL", (EP,)).fetchone()["n"] >= 1


# ================================================================== labour compliance
def test_labour_compliance_small_company(client, h):
    c = client.get(url("/payroll/labour-compliance"), headers=h, params={"as_of": "2026-12-31"}).json()
    assert c["headcount"] == 6 and c["wage_code"] == {"flagged": 2, "employees": 6, "status": "fix"}
    assert c["gratuity"]["applies"] is False and c["gratuity"]["status"] == "not_applicable" and c["gratuity"]["liability_estimate"] == "0.00"
    assert c["posh"]["required"] is False and c["posh"]["status"] == "not_applicable"
    assert c["shops_establishment"] == {"registered": True, "valid_until": "2027-01-15", "status": "expiring"}


def test_labour_compliance_once_the_company_has_ten_people(client, h, db):
    for i in range(8, 13):
        e = client.post(url("/employees"), headers=h, json=emp_body(f"EMP-{i}", f"Extra {i}", f"AAAPE{i:04d}Z"[:10], joined="2024-01-15"))
        assert e.status_code == 201, e.text
        client.post(url(f"/employees/{e.json()['id']}/salary-structures"), headers=h, json=structure_body([("BASIC", 30000, True), ("HRA", 12000, False)]))
    c = client.get(url("/payroll/labour-compliance"), headers=h, params={"as_of": "2026-12-31"}).json()
    assert c["headcount"] == 11 and c["gratuity"]["applies"] and c["gratuity"]["status"] == "valuation_needed"       # EMP-7 is still onboarding
    rows = db.execute("""SELECT e.date_of_joining, sum(c.monthly_amount) FILTER (WHERE c.in_wages AND c.kind = 'earning') AS wages FROM employees e
                           JOIN salary_structures s ON s.employee_id = e.id AND s.effective @> '2026-12-31'::date JOIN salary_components c ON c.structure_id = s.id
                          WHERE e.entity_id = %s AND e.status IN ('active', 'notice') GROUP BY e.id""", (EP,)).fetchall()
    assert D(c["gratuity"]["liability_estimate"]) == sum(pr.gratuity_provision(r["wages"], r["date_of_joining"], dt.date(2026, 12, 31)) for r in rows) > 0
    assert c["posh"] == {"required": True, "constituted": False, "members": 0, "has_external_member": False, "status": "action_needed"}


def test_committees_and_gratuity_valuation(client, h, hv):
    members = [{"name": "Meera Iyer", "role": "presiding_officer"}, {"name": "Arjun Shetty", "role": "member"}]
    assert client.post(url("/committees"), headers=h, json={"kind": "posh_internal", "constituted_on": "2026-04-01", "members": [{"name": "Arjun", "role": "member"}]}).status_code == 400
    assert client.post(url("/committees"), headers=hv, json={"kind": "posh_internal", "constituted_on": "2026-04-01", "members": members}).json()["code"] == "permission_denied"
    ic = client.post(url("/committees"), headers=h, json={"kind": "posh_internal", "constituted_on": "2026-04-01", "members": members})
    assert ic.status_code == 201 and len(ic.json()["members"]) == 2
    c = client.get(url("/payroll/labour-compliance"), headers=h, params={"as_of": "2026-12-31"}).json()
    assert c["posh"]["status"] == "action_needed" and c["posh"]["members"] == 2                                      # no external member yet
    client.post(url("/committees"), headers=h, json={"kind": "posh_internal", "constituted_on": "2026-10-01", "members": members + [{"name": "Adv. Rao", "role": "external_member"}]})
    c = client.get(url("/payroll/labour-compliance"), headers=h, params={"as_of": "2026-12-31"}).json()
    assert c["posh"] == {"required": True, "constituted": True, "members": 3, "has_external_member": True, "status": "constituted"}
    assert [x["kind"] for x in client.get(url("/committees"), headers=h, params={"kind": "posh_internal"}).json()["data"]] == ["posh_internal", "posh_internal"]
    v = client.post(url("/payroll/gratuity-valuations"), headers=h, json={"as_of": "2026-03-31", "liability": "1860000.00", "funded_amount": "500000.00", "actuary": "Acme Actuaries"})
    assert v.status_code == 201 and v.json()["liability"] == "1860000.00"
    again = client.post(url("/payroll/gratuity-valuations"), headers=h, json={"as_of": "2026-03-31", "liability": "1900000.00"})
    assert again.json()["id"] == v.json()["id"] and again.json()["liability"] == "1900000.00"                      # one valuation per date
    c = client.get(url("/payroll/labour-compliance"), headers=h, params={"as_of": "2026-12-31"}).json()
    assert c["gratuity"]["status"] == "provided" and c["gratuity"]["latest_valuation"]["actuary"] == "Acme Actuaries"


def test_statutory_bonus(client, h, hv):
    assert client.post(url("/payroll/statutory-bonus/accrue"), headers=hv, json={"fy": "2026-27"}).json()["code"] == "permission_denied"
    assert client.post(url("/payroll/statutory-bonus/accrue"), headers=h, json={"fy": "26-27"}).status_code == 400
    r = client.post(url("/payroll/statutory-bonus/accrue"), headers=h, json={"fy": "2026-27"})
    assert r.status_code == 200, r.text
    assert r.json() == {"fy": "2026-27", "accrued": 3, "total": "20991.60", "pay_by": "2027-11-30"}                  # Lakshmi, Sneha, Imran: wages 7 000 cap x 12 x 8.33%
    assert client.post(url("/payroll/statutory-bonus/accrue"), headers=h, json={"fy": "2026-27"}).json()["total"] == "20991.60"        # idempotent
    c = client.get(url("/payroll/labour-compliance"), headers=h, params={"as_of": "2027-12-31"}).json()
    assert c["statutory_bonus"]["accrued_employees"] == 3 and c["statutory_bonus"]["accrued_total"] == "20991.60" and c["statutory_bonus"]["eligible"] == 3


def test_visibility_and_invariants(client, db):
    stranger = login(client, "stranger@example.in")
    assert client.get(url("/employees"), headers=stranger).status_code == 404
    assert client.get(url("/payroll/labour-compliance"), headers=stranger).status_code == 404
    assert db.execute("SELECT app.audit_verify() AS v").fetchone()["v"] is None
    bad = db.execute("""SELECT e.entry_no FROM journal_entries e JOIN journal_lines l ON l.entry_id = e.id WHERE e.entity_id = %s AND e.status <> 'draft'
                        GROUP BY e.id, e.entry_no HAVING sum(l.debit) <> sum(l.credit)""", (EP,)).fetchall()
    assert bad == []
