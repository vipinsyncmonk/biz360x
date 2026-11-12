"""HR: the CTC split and the leave maths (pure), then policy, onboarding from a CTC, leave, the payroll run it feeds, payslips and the
handbook through the API."""
import datetime as dt
import os
import uuid
from decimal import Decimal as D

import psycopg
import pytest
from psycopg.rows import dict_row

from app import hr

DB = os.environ["DATABASE_URL"]
ORG = "00000000-0000-0000-0000-0000004a7000"
EN = "00000000-0000-0000-0000-0000004a7001"
U_OWNER, U_APPROVER, U_VIEWER = "00000000-0000-0000-0000-0000004a7011", "00000000-0000-0000-0000-0000004a7012", "00000000-0000-0000-0000-0000004a7013"
OWNER, APPROVER, VIEWER = "hr-owner@example.in", "hr-approver@example.in", "hr-viewer@example.in"
TODAY = dt.date.today()
MONTH = TODAY.replace(day=1)
MON = MONTH + dt.timedelta(days=(7 - MONTH.weekday()) % 7)            # the first Monday of this month
NEXT = (MONTH + dt.timedelta(days=32)).replace(day=1)
NEXT_MON = NEXT + dt.timedelta(days=(7 - NEXT.weekday()) % 7)
S: dict = {}
SAL = dict(hr.DEFAULT_SALARY)


def day(n):
    return str(MON + dt.timedelta(days=n))


# ------------------------------------------------------------------ pure: CTC
def by_code(comps):
    return {c["code"]: c["monthly_amount"] for c in comps}


def test_a_ctc_is_split_by_the_policy_and_adds_up_to_the_paisa():
    comps = hr.ctc_breakup(D("1200000"), SAL, pf_registered=True, esi_registered=False)
    assert by_code(comps) == {"BASIC": D("50000.00"), "HRA": D("20000.00"), "SPECIAL": D("28200.00"), "EMPLOYER_PF": D("1800")}
    assert sum(c["monthly_amount"] for c in comps) == D("100000.00")
    odd = hr.ctc_breakup(D("1000000"), SAL, pf_registered=True, esi_registered=False)             # 83,333.33 a month: the special allowance takes the rounding
    assert sum(c["monthly_amount"] for c in odd) == D("83333.33")
    assert next(c for c in comps if c["code"] == "BASIC")["in_wages"] and all(c["kind"] == "employer_contribution" for c in comps if c["code"].startswith("EMPLOYER"))


def test_employer_esi_is_inside_the_ctc_only_while_the_pay_is_under_the_limit():
    low = by_code(hr.ctc_breakup(D("240000"), SAL, pf_registered=True, esi_registered=True))
    assert low == {"BASIC": D("10000.00"), "HRA": D("4000.00"), "SPECIAL": D("4208.00"), "EMPLOYER_PF": D("1200"), "EMPLOYER_ESI": D("592")}
    assert "EMPLOYER_ESI" not in by_code(hr.ctc_breakup(D("300000"), SAL, pf_registered=True, esi_registered=True))       # gross above 21,000
    assert "EMPLOYER_ESI" not in by_code(hr.ctc_breakup(D("240000"), SAL, pf_registered=True, esi_registered=False))


def test_the_policy_decides_what_the_ctc_includes():
    none = by_code(hr.ctc_breakup(D("1200000"), {**SAL, "employer_pf_in_ctc": False}, pf_registered=True, esi_registered=False))
    assert "EMPLOYER_PF" not in none and none["SPECIAL"] == D("30000.00")
    full = by_code(hr.ctc_breakup(D("1200000"), {**SAL, "pf_on_full_basic": True, "gratuity_in_ctc": True}, pf_registered=True, esi_registered=False))
    assert full["EMPLOYER_PF"] == D("6000") and full["GRATUITY"] == D("2405.00") and sum(full.values()) == D("100000.00")
    assert "EMPLOYER_PF" not in by_code(hr.ctc_breakup(D("1200000"), SAL, pf_registered=False, esi_registered=False))     # a company not registered for PF
    with pytest.raises(hr.PolicyError):
        hr.ctc_breakup(D("1200000"), {**SAL, "basic_pct_of_ctc": "80", "hra_pct_of_basic": "50"}, pf_registered=True, esi_registered=False)


def test_the_summary_shows_the_gross_and_a_take_home_before_tax():
    s = hr.breakup_summary(hr.ctc_breakup(D("1200000"), SAL, pf_registered=True, esi_registered=False))
    assert (s["monthly_ctc"], s["monthly_gross"], s["employer_contributions"], s["employee_pf"], s["take_home_before_tax"]) == (D("100000.00"), D("98200.00"), D("1800"), D("1800"), D("96400.00"))
    assert s["wage_pct"] == D("50.9")


# ------------------------------------------------------------------ pure: leave
FY = dt.date(2026, 4, 1)


def test_leave_is_earned_by_the_year_or_by_the_month_and_pro_rated_for_a_joiner():
    old = dt.date(2024, 6, 1)
    assert hr.entitlement(D(7), "yearly", old, FY, dt.date(2026, 4, 1)) == D("7")
    assert hr.entitlement(D(15), "monthly", old, FY, dt.date(2026, 11, 12)) == D("10")              # April to November: 8 of 12 months
    assert hr.entitlement(D(15), "monthly", old, FY, dt.date(2027, 3, 31)) == D("15")
    new = dt.date(2026, 10, 15)
    assert hr.entitlement(D(7), "yearly", new, FY, dt.date(2026, 11, 12)) == D("3.5")               # October to March: 6 of 12 months
    assert hr.entitlement(D(15), "monthly", new, FY, dt.date(2026, 11, 12)) == D("2.5")
    assert hr.entitlement(D(7), "yearly", dt.date(2027, 5, 1), FY, dt.date(2026, 11, 12)) == D("0")
    assert hr.entitlement(None, "yearly", old, FY, dt.date(2026, 11, 12)) is None                   # unpaid leave has no quota
    assert hr.leave_year_start(dt.date(2027, 2, 10)) == FY and hr.leave_year_start(dt.date(2027, 2, 10), "calendar") == dt.date(2027, 1, 1)
    assert hr.leave_year_end(FY) == dt.date(2027, 3, 31)


def test_leave_days_leave_out_the_weekly_off_and_holidays():
    off, hol = [5, 6], {dt.date(2026, 11, 10)}
    assert hr.leave_days(dt.date(2026, 11, 9), dt.date(2026, 11, 11), False, off, hol) == D(2)      # Monday to Wednesday, Tuesday a holiday
    assert hr.leave_days(dt.date(2026, 11, 13), dt.date(2026, 11, 16), False, off, set()) == D(2)   # Friday to Monday
    assert hr.leave_days(dt.date(2026, 11, 14), dt.date(2026, 11, 15), False, off, set()) == D(0)
    assert hr.leave_days(dt.date(2026, 11, 9), dt.date(2026, 11, 9), True, off, set()) == D("0.5")
    assert hr.leave_days(dt.date(2026, 11, 14), dt.date(2026, 11, 16), False, [6], set()) == D(2)   # a six-day week
    with pytest.raises(hr.PolicyError):
        hr.leave_days(dt.date(2026, 11, 9), dt.date(2026, 11, 10), True, off, set())


def test_leave_beyond_the_balance_is_unpaid_only_when_the_policy_allows_it():
    assert hr.split_paid_unpaid(D(3), D(5), paid_type=True, lop_when_exhausted=True) == (D(3), D(0))
    assert hr.split_paid_unpaid(D(10), D(7), paid_type=True, lop_when_exhausted=True) == (D(7), D(3))
    assert hr.split_paid_unpaid(D(2), D(-1), paid_type=True, lop_when_exhausted=True) == (D(0), D(2))
    assert hr.split_paid_unpaid(D(2), None, paid_type=False, lop_when_exhausted=False) == (D(0), D(2))
    with pytest.raises(hr.PolicyError):
        hr.split_paid_unpaid(D(10), D(7), paid_type=True, lop_when_exhausted=False)


def test_unpaid_days_are_the_last_days_of_a_request_and_land_in_their_own_month():
    off = [5, 6]
    out = hr.lop_by_month(dt.date(2026, 11, 26), dt.date(2026, 12, 3), D(6), D(4), off, set())      # Thu 26 Nov to Thu 3 Dec: 6 working days, the last 4 unpaid
    assert out == {dt.date(2026, 12, 1): D(3), dt.date(2026, 11, 1): D(1)}
    assert hr.lop_by_month(dt.date(2026, 11, 9), dt.date(2026, 11, 9), D("0.5"), D("0.5"), off, set()) == {dt.date(2026, 11, 1): D("0.5")}
    assert hr.lop_by_month(dt.date(2026, 11, 9), dt.date(2026, 11, 10), D(2), D(0), off, set()) == {}
    b = hr.balance(quota=D(7), accrual="yearly", joined=dt.date(2024, 6, 1), year_start=FY, as_of=dt.date(2026, 11, 12), adjustments=D(2), taken=D(3), pending=D(1))
    assert (b["earned"], b["available"]) == (D(7), D(5))


def test_the_checklist_reads_what_it_can_from_the_record():
    tasks = [{"key": "offer", "title": "Offer", "required": True, "auto": None, "done_at": None}, {"key": "pan", "title": "PAN", "required": True, "auto": "pan"},
             {"key": "handbook", "title": "Policies", "required": False, "auto": "handbook"}]
    state, open_ = hr.checklist_state(tasks, has_pan=True, has_bank=False, has_salary=True, handbook_done=False)
    assert [t["done"] for t in state] == [False, True, False] and open_ == ["Offer"]
    assert hr.next_employee_code(["EMP-6", "EMP-0002", "X-99", "emp-12"]) == "EMP-0013" and hr.next_employee_code([]) == "EMP-0001"


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
    db.execute("INSERT INTO organizations (id, name, kind) VALUES (%s, 'HR Org', 'business_group')", (ORG,))
    db.execute("INSERT INTO entities (id, organization_id, legal_name, constitution, track) VALUES (%s, %s, 'People Test Pvt Ltd', 'private_limited', 'business')", (EN, ORG))
    db.execute("INSERT INTO entity_registrations (entity_id, kind, number, state_code) VALUES (%s, 'epf', 'KNBLR0099999000', '29'), (%s, 'tan', 'BLRH99999D', '29')", (EN, EN))
    db.execute("INSERT INTO users (id, email, full_name) VALUES (%s, %s, 'Hr Owner'), (%s, %s, 'Hr Approver'), (%s, %s, 'Hr Viewer')", (U_OWNER, OWNER, U_APPROVER, APPROVER, U_VIEWER, VIEWER))
    db.execute("INSERT INTO memberships (entity_id, user_id, role, accepted_at) VALUES (%s, %s, 'owner', now()), (%s, %s, 'approver', now()), (%s, %s, 'viewer', now())",
               (EN, U_OWNER, EN, U_APPROVER, EN, U_VIEWER))
    yield
    db.execute("UPDATE jobs SET status = 'cancelled' WHERE status = 'queued' AND entity_id = %s", (EN,))


def policy_body(**over):
    p = hr.default_policy()
    return {"effective_from": str(TODAY), **p, **over}


def test_a_company_starts_on_the_starter_policy_and_saves_its_own(client):
    o, v = login(client, OWNER), login(client, VIEWER)
    p = client.get(url("/hr/policy"), headers=v).json()
    assert p["is_default"] is True and p["salary"]["basic_pct_of_ctc"] == "50" and [t["code"] for t in p["leave_types"]] == ["CL", "EL", "SL", "LOP"]
    assert len(p["onboarding"]["checklist"]) == 8 and p["leave"]["week_off"] == [5, 6]
    assert client.put(url("/hr/policy"), headers=v, json=policy_body()).status_code == 403
    r = client.put(url("/hr/policy"), headers=o, json=policy_body(effective_from=str(TODAY - dt.timedelta(days=1))))
    assert r.status_code == 400 and r.json()["errors"][0]["field"] == "effective_from"
    bad = policy_body()
    bad["leave"]["week_off"] = [7]
    assert client.put(url("/hr/policy"), headers=o, json=bad).status_code == 400
    mine = policy_body()
    mine["salary"]["basic_pct_of_ctc"] = "40"
    r = client.put(url("/hr/policy"), headers=o, json=mine)
    assert r.status_code == 200, r.text
    assert r.json()["is_default"] is False and r.json()["effective_from"] == str(TODAY) and r.json()["salary"]["basic_pct_of_ctc"] == "40"
    r = client.post(url("/leave-types"), headers=o, json={"code": "wfh", "name": "Work from home", "annual_quota": "12", "accrual": "yearly"})
    assert r.status_code == 200 and r.json()["code"] == "WFH"
    assert client.post(url("/leave-types"), headers=o, json={"code": "PL", "name": "Paid, no quota"}).status_code == 400
    assert client.post(url("/leave-types"), headers=v, json={"code": "XX", "name": "No", "annual_quota": "1"}).status_code == 403


def test_the_ctc_breakup_is_previewed_from_the_policy(client):
    o = login(client, OWNER)
    r = client.get(url("/payroll/ctc-breakup"), headers=o, params={"ctc_annual": "1200000"})
    assert r.status_code == 200, r.text
    b = r.json()
    assert {c["code"]: c["monthly_amount"] for c in b["components"]} == {"BASIC": "40000.00", "HRA": "16000.00", "SPECIAL": "42200.00", "EMPLOYER_PF": "1800.00"}
    assert (b["monthly_ctc"], b["monthly_gross"], b["take_home_before_tax"]) == ("100000.00", "98200.00", "96400.00") and b["wage_pct"] == 40.7
    assert client.get(url("/payroll/ctc-breakup"), headers=o, params={"ctc_annual": "0"}).status_code == 400


def test_a_new_hire_is_entered_once_with_a_ctc_and_gets_a_salary_and_a_checklist(client, db):
    o, v = login(client, OWNER), login(client, VIEWER)
    body = {"full_name": "Asha Rao", "date_of_joining": "2024-06-01", "work_state": "29", "ctc_annual": "1200000.00", "designation": "Engineer"}
    assert client.post(url("/employees/onboard"), headers=v, json=body).status_code == 403
    r = client.post(url("/employees/onboard"), headers=o, json=body)
    assert r.status_code == 201, r.text
    out = r.json()
    e = out["employee"]
    S["asha"] = e["id"]
    assert (e["employee_code"], e["status"], e["current_structure"]["ctc_annual"]) == ("EMP-0001", "onboarding", "1200000.00")
    assert {c["code"]: c["monthly_amount"] for c in e["current_structure"]["components"]} == {"BASIC": "40000.00", "HRA": "16000.00", "SPECIAL": "42200.00", "EMPLOYER_PF": "1800.00"}
    done = {t["key"]: t["done"] for t in out["checklist"]}
    assert done["salary"] is True and done["pan"] is False and done["bank"] is False and out["ready"] is False and (out["done"], out["total"]) == (1, 8)
    assert out["open_required"] == ["Offer letter signed", "PAN on file", "Identity and address proof collected", "Bank account for salary"]
    assert client.post(url("/employees/onboard"), headers=o, json={**body, "ctc_annual": "-1"}).status_code == 400


def test_onboarding_finishes_only_when_every_required_item_is_done(client):
    o = login(client, OWNER)
    r = client.post(url(f"/employees/{S['asha']}/activate"), headers=o)
    assert r.status_code == 409 and r.json()["code"] == "onboarding_incomplete" and "PAN on file" in r.json()["detail"]
    assert client.put(url(f"/employees/{S['asha']}/onboarding/pan"), headers=o, json={"done": True}).status_code == 409        # read from the record, not ticked
    assert client.put(url(f"/employees/{S['asha']}/onboarding/nope"), headers=o, json={"done": True}).status_code == 404
    for k in ("offer", "id_proof"):
        assert client.put(url(f"/employees/{S['asha']}/onboarding/{k}"), headers=o, json={"done": True}).status_code == 200
    r = client.patch(url(f"/employees/{S['asha']}"), headers=o, json={"pan": "AAAPR1234A", "bank_account": {"account_no": "501009876543", "ifsc": "HDFC0000123"}})
    assert r.status_code == 200 and r.json()["status"] == "onboarding"
    st = client.get(url(f"/employees/{S['asha']}/onboarding"), headers=o).json()
    assert st["ready"] is True and st["open_required"] == []
    r = client.post(url(f"/employees/{S['asha']}/activate"), headers=o)
    assert r.status_code == 200 and r.json()["employee"]["status"] == "active"
    assert client.post(url(f"/employees/{S['asha']}/activate"), headers=o).status_code == 409


def test_holidays_are_kept_and_not_counted_as_leave(client):
    o = login(client, OWNER)
    r = client.post(url("/holidays"), headers=o, json={"date": day(1), "name": "Founders' day"})
    assert r.status_code == 201, r.text
    S["holiday"] = r.json()["id"]
    assert client.post(url("/holidays"), headers=o, json={"date": day(1), "name": "Again"}).status_code == 409
    assert [h["name"] for h in client.get(url("/holidays"), headers=o, params={"year": MON.year}).json()["data"]] == ["Founders' day"]
    extra = client.post(url("/holidays"), headers=o, json={"date": str(NEXT_MON + dt.timedelta(days=200)), "name": "Far away"}).json()["id"]
    assert client.delete(url(f"/holidays/{extra}"), headers=o).status_code == 204 and client.delete(url(f"/holidays/{extra}"), headers=o).status_code == 404


def leave(client, h, typ, a, b, **kw):
    return client.post(url("/leave-requests"), headers=h, json={"employee_id": S["asha"], "leave_type": typ, "from": a, "to": b, **kw})


def balance_of(client, h, code):
    r = client.get(url("/leave-balances"), headers=h, params={"employee_id": S["asha"]})
    assert r.status_code == 200, r.text
    return next(b for b in r.json()["data"][0]["balances"] if b["leave_type"] == code)


def test_leave_is_counted_in_working_days_against_the_balance(client):
    o, v = login(client, OWNER), login(client, VIEWER)
    assert balance_of(client, o, "CL") | {} == {**balance_of(client, o, "CL"), "earned": "7", "available": "7", "taken": "0"}
    assert balance_of(client, o, "LOP")["available"] is None
    assert leave(client, v, "CL", day(0), day(2)).status_code == 403
    r = leave(client, o, "CL", day(0), day(2), reason="Family function")                                    # Monday to Wednesday, Tuesday a holiday
    assert r.status_code == 201, r.text
    S["cl"] = r.json()["id"]
    assert (r.json()["days"], r.json()["lop_days"], r.json()["status"], r.json()["employee_name"]) == ("2", "0", "pending", "Asha Rao")
    b = balance_of(client, o, "CL")
    assert (b["pending"], b["available"]) == ("2", "5")
    assert leave(client, o, "SL", day(2), day(3)).status_code == 409                                         # overlaps the request above
    assert leave(client, o, "CL", day(5), day(6)).status_code == 400                                         # a weekend: no working days
    assert leave(client, o, "CL", day(7), day(8), half_day=True).status_code == 400
    assert leave(client, o, "ZZ", day(7), day(8)).status_code == 400
    assert leave(client, o, "CL", "2020-01-06", "2020-01-07").status_code == 400                             # before joining
    half = leave(client, o, "SL", day(7), day(7), half_day=True)
    assert half.status_code == 201 and half.json()["days"] == "0.5"
    assert client.post(url(f"/leave-requests/{half.json()['id']}/reject"), headers=o, json={"note": "Client visit that day"}).json()["status"] == "rejected"
    assert client.post(url(f"/leave-requests/{S['cl']}/approve"), headers=v).status_code == 403
    r = client.post(url(f"/leave-requests/{S['cl']}/approve"), headers=o)
    assert r.status_code == 200 and r.json()["status"] == "approved" and "payroll_note" not in r.json()
    assert client.post(url(f"/leave-requests/{S['cl']}/approve"), headers=o).status_code == 409
    b = balance_of(client, o, "CL")
    assert (b["taken"], b["pending"], b["available"]) == ("2", "0", "5")
    rows = client.get(url("/leave-requests"), headers=o, params={"status": "approved"}).json()["data"]
    assert [x["id"] for x in rows] == [S["cl"]]


def test_a_request_beyond_the_balance_is_refused_when_the_policy_says_so(client):
    o = login(client, OWNER)
    long_to = str(NEXT_MON + dt.timedelta(days=81))                                                          # twelve weeks: far more than any balance
    r = leave(client, o, "CL", str(NEXT_MON), long_to)
    assert r.status_code == 201 and D(r.json()["lop_days"]) > 0 and D(r.json()["days"]) == D(r.json()["lop_days"]) + min(D(r.json()["days"]), D("5"), D(r.json()["days"]) - D(r.json()["lop_days"]))
    assert client.post(url(f"/leave-requests/{r.json()['id']}/cancel"), headers=o).json()["status"] == "cancelled"
    strict = policy_body()
    strict["salary"]["basic_pct_of_ctc"] = "40"
    strict["leave"]["lop_when_exhausted"] = False
    assert client.put(url("/hr/policy"), headers=o, json=strict).status_code == 200
    if NEXT_MON > TODAY:                                                                                     # the stricter policy is in force from today
        r = leave(client, o, "CL", str(NEXT_MON), long_to)
        assert r.status_code == 409 and r.json()["code"] == "leave_balance_short", r.text
    back = policy_body()
    back["salary"]["basic_pct_of_ctc"] = "40"
    assert client.put(url("/hr/policy"), headers=o, json=back).status_code == 200


def test_approved_unpaid_leave_reaches_the_months_payroll_by_itself(client, db):
    o, a = login(client, OWNER), login(client, APPROVER)
    late = client.post(url("/employees/onboard"), headers=o, json={"full_name": "Late Joiner", "date_of_joining": str(MONTH), "work_state": "29", "ctc_annual": "600000"})
    assert late.status_code == 201 and late.json()["employee"]["employee_code"] == "EMP-0002"
    S["late"] = late.json()["employee"]["id"]
    run = client.post(url("/payroll-runs"), headers=o, json={"period_month": str(MONTH)})
    assert run.status_code == 201, run.text
    S["run"] = run.json()["id"]
    assert run.json()["headcount"] == 1                                                                      # the one still onboarding is left out
    assert client.get(url(f"/payroll-runs/{S['run']}/inputs"), headers=o).json() == []
    r = leave(client, o, "LOP", day(3), day(4), reason="Personal")                                           # Thursday and Friday, unpaid
    assert r.status_code == 201 and (r.json()["days"], r.json()["lop_days"]) == ("2", "2")
    S["lop"] = r.json()["id"]
    ready = client.get(url(f"/payroll-runs/{S['run']}/readiness"), headers=o).json()
    assert ready["ready"] is True and ready["blockers"] == [] and ready["headcount"] == 1
    assert {w["code"] for w in ready["warnings"]} == {"onboarding_open", "leave_pending"}
    assert client.post(url(f"/leave-requests/{S['lop']}/approve"), headers=o).json()["status"] == "approved"
    inputs = client.get(url(f"/payroll-runs/{S['run']}/inputs"), headers=o).json()
    assert [(i["employee_id"], i["lop_days"]) for i in inputs] == [(S["asha"], "2")]
    ready = client.get(url(f"/payroll-runs/{S['run']}/readiness"), headers=o).json()
    assert ready["lop_from_leave"][0]["lop_days"] == "2" and {w["code"] for w in ready["warnings"]} == {"onboarding_open"}
    assert client.post(url(f"/payroll-runs/{S['run']}/compute"), headers=o).json()["status"] == "computed"
    slip = client.get(url(f"/payroll-runs/{S['run']}/payslips"), headers=o).json()["data"][0]
    assert slip["lop_days"] == 2.0 and slip["pdf_url"].endswith(f"/payslips/{slip['id']}/pdf")
    S["slip"] = slip["id"]
    # withdrawing the leave puts the run back to open with the unpaid days gone; taking it again brings them back
    assert client.post(url(f"/leave-requests/{S['lop']}/cancel"), headers=o).json()["status"] == "cancelled"
    assert client.get(url(f"/payroll-runs/{S['run']}"), headers=o).json()["status"] == "inputs_open"
    assert client.get(url(f"/payroll-runs/{S['run']}/inputs"), headers=o).json()[0]["lop_days"] == "0"
    again = leave(client, o, "LOP", day(3), day(4))
    S["lop"] = again.json()["id"]
    assert client.post(url(f"/leave-requests/{S['lop']}/approve"), headers=o).status_code == 200
    assert client.post(url(f"/payroll-runs/{S['run']}/inputs/from-leave"), headers=o).json()["data"] == [{"employee_id": S["asha"], "lop_days": "2"}]
    assert client.post(url(f"/payroll-runs/{S['run']}/compute"), headers=o).json()["status"] == "computed"
    ar = client.post(url(f"/payroll-runs/{S['run']}/submit"), headers=o)
    assert ar.status_code == 201, ar.text
    assert client.post(url(f"/approvals/{ar.json()['id']}/approve"), headers={**a, **key()}, json={}).status_code == 200
    assert client.get(url(f"/payroll-runs/{S['run']}"), headers=o).json()["status"] == "approved"
    r = client.post(url(f"/leave-requests/{S['lop']}/cancel"), headers=o)
    assert r.status_code == 409 and "already approved" in r.json()["title"]                                  # the pay was deducted: it cannot be undone by cancelling


def test_a_person_s_payslips_are_listed_and_printed(client):
    o, v = login(client, OWNER), login(client, VIEWER)
    rows = client.get(url(f"/employees/{S['asha']}/payslips"), headers=v).json()["data"]
    assert len(rows) == 1 and rows[0]["period_month"] == str(MONTH) and rows[0]["final"] is True
    sid = rows[0]["id"]
    s = client.get(url(f"/payslips/{sid}"), headers=o).json()
    assert s["employee_code"] == "EMP-0001" and s["lop_days"] == 2.0 and s["pan_last4"] == "234A" and "pan" not in s
    assert {l["code"] for l in s["lines"]} >= {"BASIC", "HRA", "SPECIAL", "PF_EMPLOYEE"} and s["year_to_date"]["gross"] == s["gross"]
    assert D(s["net_pay"]) == D(s["gross"]) - D(s["pf_employee"]) - D(s["professional_tax"]) - D(s["tds"])
    pdf = client.get(url(f"/payslips/{sid}/pdf"), headers=v, params={"download": "true"})
    assert pdf.status_code == 200 and pdf.headers["content-type"] == "application/pdf" and pdf.content[:5] == b"%PDF-" and len(pdf.content) > 1500
    assert f"payslip-EMP-0001-{str(MONTH)[:7]}.pdf" in pdf.headers["content-disposition"] and pdf.headers["content-disposition"].startswith("attachment")
    assert client.get(url(f"/payslips/{uuid.uuid4()}"), headers=o).status_code == 404
    assert client.get(url(f"/employees/{S['late']}/payslips"), headers=o).json()["data"] == []
    assert client.get(f"/v1/entities/{uuid.uuid4()}/payslips/{sid}/pdf", headers=o).status_code == 404


def test_the_handbook_is_versioned_and_acknowledgements_tick_the_checklist(client):
    o, v = login(client, OWNER), login(client, VIEWER)
    doc = {"title": "Leave policy", "category": "leave", "body": "Earned leave is 15 days a year.\nApply two days ahead."}
    assert client.post(url("/hr/policy-documents"), headers=v, json=doc).status_code == 403
    r = client.post(url("/hr/policy-documents"), headers=o, json=doc)
    assert r.status_code == 201, r.text
    d1 = r.json()
    assert (d1["slug"], d1["version"], d1["acknowledged"], d1["people"]) == ("leave-policy", 1, 0, 2) and len(d1["waiting"]) == 2
    r = client.post(url(f"/hr/policy-documents/{d1['id']}/acknowledgements"), headers=o, json={"employee_id": S["late"]})
    assert r.status_code == 200 and r.json()["acknowledged"] == 1 and [w["employee_code"] for w in r.json()["waiting"]] == ["EMP-0001"]
    assert client.post(url(f"/hr/policy-documents/{d1['id']}/acknowledgements"), headers=o, json={"employee_id": S["late"]}).json()["acknowledged"] == 1
    st = client.get(url(f"/employees/{S['late']}/onboarding"), headers=o).json()
    assert next(t for t in st["checklist"] if t["key"] == "handbook")["done"] is True
    d2 = client.post(url("/hr/policy-documents"), headers=o, json={**doc, "slug": "leave-policy", "body": "Earned leave is 18 days a year."}).json()
    assert (d2["version"], d2["acknowledged"]) == (2, 0)
    assert client.post(url(f"/hr/policy-documents/{d1['id']}/acknowledgements"), headers=o, json={"employee_id": S["asha"]}).status_code == 409
    assert next(t for t in client.get(url(f"/employees/{S['late']}/onboarding"), headers=o).json()["checklist"] if t["key"] == "handbook")["done"] is False
    listed = client.get(url("/hr/policy-documents"), headers=v).json()["data"]
    assert [(x["slug"], x["version"]) for x in listed] == [("leave-policy", 2)] and "body" not in listed[0]
    assert client.get(url(f"/hr/policy-documents/{d1['id']}"), headers=v).json()["is_current"] is False


def test_balances_can_be_brought_in_and_a_raise_needs_only_the_new_ctc(client, db):
    o, v = login(client, OWNER), login(client, VIEWER)
    body = {"employee_id": S["asha"], "leave_type": "EL", "days": "3", "kind": "opening", "note": "Carried in from the old register"}
    assert client.post(url("/leave-adjustments"), headers=v, json=body).status_code == 403
    before = D(balance_of(client, o, "EL")["available"])
    r = client.post(url("/leave-adjustments"), headers=o, json=body)
    assert r.status_code == 201 and r.json()["balance"]["adjustments"] == "3" and D(r.json()["balance"]["available"]) == before + 3
    assert client.post(url("/leave-adjustments"), headers=o, json={**body, "leave_type": "LOP"}).status_code == 400
    r = client.post(url(f"/employees/{S['asha']}/salary-structures"), headers=o, json={"effective_from": str(NEXT), "ctc_annual": "1500000.00"})
    assert r.status_code == 201, r.text
    assert {c["code"]: c["monthly_amount"] for c in r.json()["components"]} == {"BASIC": "50000.00", "HRA": "20000.00", "SPECIAL": "53200.00", "EMPLOYER_PF": "1800.00"}
    assert db.execute("SELECT app.audit_verify() AS v").fetchone()["v"] is None
    kinds = {r["type"] for r in db.execute("SELECT DISTINCT type FROM event_outbox WHERE entity_id = %s", (EN,)).fetchall()}
    assert {"employee.onboarding_started", "employee.activated", "leave.requested", "leave.decided", "hr_policy.changed"} <= kinds
