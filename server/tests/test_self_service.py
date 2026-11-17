"""Employee self-service (migration 41): HR opens it, the person accepts with the email on their record, and then sees and does only
what is their own, through the API and on a raw connection as `biz_self`."""
import datetime as dt
import os
import uuid

import psycopg
import pytest
from psycopg.rows import dict_row

DB = os.environ["DATABASE_URL"]
ORG = "00000000-0000-0000-0000-0000005e1f00"
EN = "00000000-0000-0000-0000-0000005e1f10"
U_OWNER, U_HR, U_SALES = ("00000000-0000-0000-0000-0000005e1f2%d" % i for i in (1, 2, 3))
OWNER, HR, SALES = "self-owner@example.in", "self-hr@example.in", "self-sales@example.in"
ASHA, BALU, NOMAIL = ("00000000-0000-0000-0000-0000005e1f3%d" % i for i in (1, 2, 3))
ASHA_EMAIL, BALU_EMAIL, STRANGER = "self-asha@example.in", "self-balu@example.in", "self-stranger@example.in"
RUN_PAID, RUN_OPEN = "00000000-0000-0000-0000-0000005e1f41", "00000000-0000-0000-0000-0000005e1f42"
TODAY = dt.date.today()
THIS_MONTH = TODAY.replace(day=1)
LAST_MONTH = (THIS_MONTH - dt.timedelta(days=1)).replace(day=1)
S: dict = {}


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
    x("INSERT INTO organizations (id, name, kind) VALUES (%s, 'Self Org', 'business_group')", (ORG,))
    x("INSERT INTO entities (id, organization_id, legal_name, constitution, track, registered_address) VALUES (%s, %s, 'Self Co Pvt Ltd', 'private_limited', 'business', '1 MG Road, Bengaluru')",
      (EN, ORG))
    x("INSERT INTO users (id, email, full_name) VALUES (%s, %s, 'Self Owner'), (%s, %s, 'Self HR'), (%s, %s, 'Self Sales'), (gen_random_uuid(), %s, 'A Stranger')", (U_OWNER, OWNER, U_HR, HR, U_SALES, SALES, STRANGER))
    x("""INSERT INTO memberships (entity_id, user_id, role, scopes, accepted_at) VALUES (%s, %s, 'owner', NULL, now()),
         (%s, %s, 'approver', '{"hr": "approve"}', now()), (%s, %s, 'maker', '{"sales": "write"}', now())""", (EN, U_OWNER, EN, U_HR, EN, U_SALES))
    x("""INSERT INTO employees (id, entity_id, employee_code, full_name, email, pan, date_of_joining, work_state, designation, status) VALUES
         (%s, %s, 'E1', 'Asha Rao', %s, 'ABCPR1234K', '2023-04-01', '29', 'Engineer', 'active'),
         (%s, %s, 'E2', 'Balu Nair', %s, NULL, '2023-04-01', '29', 'Designer', 'active'),
         (%s, %s, 'E3', 'No Mail', NULL, NULL, '2023-04-01', '29', NULL, 'active')""", (ASHA, EN, ASHA_EMAIL, BALU, EN, BALU_EMAIL, NOMAIL, EN))
    x("INSERT INTO payroll_runs (id, entity_id, period_month, status, headcount, paid_on) VALUES (%s, %s, %s, 'paid', 2, %s), (%s, %s, %s, 'computed', 2, NULL)",
      (RUN_PAID, EN, LAST_MONTH, THIS_MONTH, RUN_OPEN, EN, THIS_MONTH))
    for run in (RUN_PAID, RUN_OPEN):
        for emp, gross in ((ASHA, 50000), (BALU, 90000)):
            slip = x("""INSERT INTO payslips (run_id, employee_id, paid_days, gross, wages, pf_wage, pf_employee, pf_employer_epf, pf_employer_eps, esi_applicable, professional_tax, net_pay)
                        VALUES (%s, %s, 30, %s, 15000, 15000, 1800, 550, 1250, false, 200, %s) RETURNING id""", (run, emp, gross, gross - 2000)).fetchone()["id"]
            x("INSERT INTO payslip_lines (payslip_id, code, label, kind, amount) VALUES (%s, 'BASIC', 'Basic', 'earning', %s)", (slip, gross))
            S[(run, emp)] = str(slip)
    x("INSERT INTO onboarding_tasks (entity_id, employee_id, key, title, required, auto, sort) VALUES (%s, %s, 'pan', 'PAN on file', true, 'pan', 1), (%s, %s, 'laptop', 'Laptop issued', false, NULL, 2)",
      (EN, ASHA, EN, ASHA))
    x("INSERT INTO holidays (entity_id, holiday_date, name) VALUES (%s, %s, 'Founders day')", (EN, TODAY + dt.timedelta(days=40)))
    yield


def login(client, email):
    r = client.post("/v1/auth/token", json={"grant_type": "otp", "email": email, "otp": "000000"})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def url(path):
    return f"/v1/entities/{EN}{path}"


def me(path=""):
    return f"/v1/self/{ASHA}{path}"


def weekday_from(days: int) -> dt.date:
    """A Wednesday at least `days` ahead, so a two-day request never touches a weekly off."""
    d = TODAY + dt.timedelta(days=days)
    return d + dt.timedelta(days=(2 - d.weekday()) % 7)


# ------------------------------------------------------------------ opening it and accepting
def test_hr_opens_self_service_for_a_person_who_has_an_email(client):
    hr, sales = login(client, HR), login(client, SALES)
    assert client.post(url(f"/employees/{ASHA}/self-service"), headers=sales).status_code == 403
    r = client.post(url(f"/employees/{NOMAIL}/self-service"), headers=hr)
    assert r.status_code == 409 and r.json()["code"] == "employee_email_missing", r.text
    r = client.post(url(f"/employees/{ASHA}/self-service"), headers=hr)
    assert r.status_code == 200 and r.json() == {"employee_id": ASHA, "self_service": "invited", "email": ASHA_EMAIL}, r.text
    assert client.post(url(f"/employees/{ASHA}/self-service"), headers=hr).json()["self_service"] == "invited"                # twice changes nothing
    people = {p["employee_code"]: p["self_service"] for p in client.get(url("/hr/people"), headers=hr).json()["data"]}
    assert people == {"E1": "invited", "E2": "off", "E3": "off"}


def test_only_the_person_with_that_email_sees_and_accepts_the_invitation(client, db):
    stranger, asha = login(client, STRANGER), login(client, ASHA_EMAIL)
    assert client.get("/v1/me/employments", headers=stranger).json() == {"employments": [], "invitations": []}
    assert client.post(f"/v1/me/employments/{ASHA}/accept", headers=stranger).status_code == 404
    mine = client.get("/v1/me/employments", headers=asha).json()
    assert mine["employments"] == [] and [(i["employee_id"], i["legal_name"], i["full_name"]) for i in mine["invitations"]] == [(ASHA, "Self Co Pvt Ltd", "Asha Rao")]
    assert client.get(me(), headers=asha).status_code == 404                                                                  # nothing before accepting
    assert client.post(f"/v1/me/employments/{BALU}/accept", headers=asha).status_code == 404                                  # not invited, and not theirs
    r = client.post(f"/v1/me/employments/{ASHA}/accept", headers=asha)
    assert r.status_code == 200 and r.json() == {"employee_id": ASHA, "entity_id": EN}, r.text
    assert client.post(f"/v1/me/employments/{ASHA}/accept", headers=asha).status_code == 404                                  # already taken
    mine = client.get("/v1/me/employments", headers=asha).json()
    assert mine["invitations"] == [] and [e["employee_id"] for e in mine["employments"]] == [ASHA]
    who = client.get("/v1/me", headers=asha).json()
    assert who["entities"] == [] and [(e["employee_id"], e["legal_name"]) for e in who["employments"]] == [(ASHA, "Self Co Pvt Ltd")]
    assert db.execute("SELECT count(*) AS n FROM memberships m JOIN users u ON u.id = m.user_id WHERE u.email = %s", (ASHA_EMAIL,)).fetchone()["n"] == 0
    hr = login(client, HR)
    assert {p["employee_code"]: p["self_service"] for p in client.get(url("/hr/people"), headers=hr).json()["data"]}["E1"] == "active"


def test_an_employee_is_not_a_member_and_reaches_nothing_of_the_company(client):
    asha = login(client, ASHA_EMAIL)
    for path in ("/employees", "/hr/people", "/leave-requests", "/payroll-runs", "/journal-entries", "/sales-documents", f"/employees/{ASHA}/payslips"):
        assert client.get(url(path), headers=asha).status_code == 404, path


# ------------------------------------------------------------------ what they see
def test_my_record_shows_me_and_hides_what_is_not_needed(client):
    asha = login(client, ASHA_EMAIL)
    r = client.get(me(), headers=asha)
    assert r.status_code == 200, r.text
    e = r.json()["employee"]
    assert (e["full_name"], e["employee_code"], e["legal_name"], e["designation"], e["pan_last4"], e["bank_on_file"], e["status"]) == \
        ("Asha Rao", "E1", "Self Co Pvt Ltd", "Engineer", "234K", False, "active")
    assert "pan" not in e and "bank_account_enc" not in e and "user_id" not in e
    ob = r.json()["onboarding"]
    assert {t["key"]: t["done"] for t in ob["checklist"]} == {"pan": True, "laptop": False} and ob["total"] == 2
    for other in (BALU, NOMAIL, str(uuid.uuid4())):
        for path in ("", "/leave", "/payslips", "/policies"):
            assert client.get(f"/v1/self/{other}{path}", headers=asha).status_code == 404, (other, path)
    assert client.get(me(), headers=login(client, HR)).status_code == 404                                                     # HR is not Asha either
    assert client.get(me()).status_code == 401


def test_my_payslips_are_mine_and_only_from_approved_runs(client):
    asha = login(client, ASHA_EMAIL)
    slips = client.get(me("/payslips"), headers=asha).json()["data"]
    assert [(s["id"], s["gross"], s["net_pay"], s["final"]) for s in slips] == [(S[(RUN_PAID, ASHA)], "50000.00", "48000.00", True)]
    assert slips[0]["pdf_url"] == f"/v1/self/{ASHA}/payslips/{S[(RUN_PAID, ASHA)]}/pdf" and slips[0]["pan_last4"] == "234K" and "pan" not in slips[0]
    one = client.get(me(f"/payslips/{S[(RUN_PAID, ASHA)]}"), headers=asha).json()
    assert one["lines"] == [{"code": "BASIC", "label": "Basic", "kind": "earning", "amount": "50000.00"}]
    assert one["year_to_date"] == {"gross": "50000.00", "tds": "0.00", "pf": "1800.00", "net": "48000.00"}
    pdf = client.get(me(f"/payslips/{S[(RUN_PAID, ASHA)]}/pdf"), headers=asha)
    assert pdf.status_code == 200 and pdf.headers["content-type"] == "application/pdf" and pdf.content[:5] == b"%PDF-"
    for not_mine in (S[(RUN_PAID, BALU)], S[(RUN_OPEN, ASHA)], str(uuid.uuid4())):                                            # a colleague's, and my own from a run not approved yet
        assert client.get(me(f"/payslips/{not_mine}"), headers=asha).status_code == 404
        assert client.get(me(f"/payslips/{not_mine}/pdf"), headers=asha).status_code == 404


def test_i_apply_for_leave_it_reaches_hr_and_i_can_withdraw_it_only_while_it_waits(client, db):
    owner, hr, asha = login(client, OWNER), login(client, HR), login(client, ASHA_EMAIL)
    types = client.get(url("/leave-types"), headers=owner).json()["data"]                                                     # the starter types are created on first use
    paid = next(t for t in types if t["paid"])
    mine = client.get(me("/leave"), headers=asha).json()
    assert mine["can_apply"] and mine["requests"] == [] and [h["name"] for h in mine["holidays"]] == ["Founders day"]
    before = next(b for b in mine["balances"] if b["leave_type"] == paid["code"])
    start = weekday_from(14)
    body = {"leave_type": paid["code"], "from": str(start), "to": str(start + dt.timedelta(days=1)), "reason": "Family function"}
    assert client.post(me("/leave-requests"), headers=asha, json={**body, "to": str(start - dt.timedelta(days=1))}).status_code == 400
    assert client.post(me("/leave-requests"), headers=asha, json={**body, "leave_type": "ZZ"}).status_code == 400
    assert client.post(me("/leave-requests"), headers=asha, json={**body, "from": "2020-01-06", "to": "2020-01-07"}).status_code == 400    # before joining
    assert client.post(f"/v1/self/{BALU}/leave-requests", headers=asha, json=body).status_code == 404                         # not for a colleague
    r = client.post(me("/leave-requests"), headers=asha, json=body)
    assert r.status_code == 201, r.text
    req = r.json()
    assert (req["status"], req["days"], req["can_cancel"], req["leave_type"]) == ("pending", "2", True, paid["code"])
    assert client.post(me("/leave-requests"), headers=asha, json=body).status_code == 409                                     # overlaps the one just made
    after = next(b for b in client.get(me("/leave"), headers=asha).json()["balances"] if b["leave_type"] == paid["code"])
    assert float(after["pending"]) == float(before["pending"]) + 2 and float(after["available"]) == float(before["available"]) - 2
    seen = client.get(url("/leave-requests?status=pending"), headers=hr).json()["data"]
    assert [(x["id"], x["employee_name"]) for x in seen] == [(req["id"], "Asha Rao")]                                         # HR sees it like any other request
    row = db.execute("SELECT requested_by FROM leave_requests WHERE id = %s", (req["id"],)).fetchone()
    assert row["requested_by"] == db.execute("SELECT id FROM users WHERE email = %s", (ASHA_EMAIL,)).fetchone()["id"]
    gone = client.post(me(f"/leave-requests/{req['id']}/cancel"), headers=asha)
    assert gone.status_code == 200 and gone.json()["status"] == "cancelled" and not gone.json()["can_cancel"], gone.text
    assert client.post(me(f"/leave-requests/{req['id']}/cancel"), headers=asha).status_code == 409
    again = client.post(me("/leave-requests"), headers=asha, json=body).json()
    assert client.post(url(f"/leave-requests/{again['id']}/approve"), headers=hr, json={}).status_code == 200
    r = client.post(me(f"/leave-requests/{again['id']}/cancel"), headers=asha)
    assert r.status_code == 409 and "HR" in r.json()["detail"], r.text                                                        # approved: HR cancels it
    assert client.post(me(f"/leave-requests/{uuid.uuid4()}/cancel"), headers=asha).status_code == 404


def test_i_read_the_handbook_and_acknowledge_it_myself(client, db):
    owner, asha = login(client, OWNER), login(client, ASHA_EMAIL)
    doc = client.post(url("/hr/policy-documents"), headers=owner, json={"title": "Code of conduct", "category": "conduct", "body": "Be kind. Be honest."}).json()
    listed = client.get(me("/policies"), headers=asha).json()["data"]
    assert [(d["id"], d["title"], d["acknowledged_on"]) for d in listed] == [(doc["id"], "Code of conduct", None)] and "body" not in listed[0]
    assert client.get(me(), headers=asha).json()["onboarding"]["to_acknowledge"] == 1
    assert client.get(me(f"/policies/{doc['id']}"), headers=asha).json()["body"] == "Be kind. Be honest."
    r = client.post(me(f"/policies/{doc['id']}/acknowledge"), headers=asha)
    assert r.status_code == 200 and r.json()["acknowledged_on"] == str(TODAY), r.text
    assert client.post(me(f"/policies/{doc['id']}/acknowledge"), headers=asha).status_code == 200                             # twice changes nothing
    hr_view = client.get(url(f"/hr/policy-documents/{doc['id']}"), headers=owner).json()
    assert [a["employee_name"] for a in hr_view["acknowledgements"]] == ["Asha Rao"]
    asha_id = db.execute("SELECT id FROM users WHERE email = %s", (ASHA_EMAIL,)).fetchone()["id"]
    logged = db.execute("SELECT actor_user_id FROM audit_log WHERE entity_id = %s AND action = 'hr_policy_document.acknowledged'", (EN,)).fetchall()
    assert [a["actor_user_id"] for a in logged] == [asha_id]                                                                  # once, and in her name
    assert client.post(me(f"/policies/{uuid.uuid4()}/acknowledge"), headers=asha).status_code == 404
    assert client.get(me(), headers=asha).json()["onboarding"]["to_acknowledge"] == 0


# ------------------------------------------------------------------ the raw connection
class As:
    def __init__(self, user_id, role="biz_self"):
        self.user_id, self.role = user_id, role

    def __enter__(self):
        self.conn = psycopg.connect(DB, row_factory=dict_row, autocommit=True)                  # each statement on its own, so a refusal does not end the session
        self.conn.execute(f"SET ROLE {self.role}")
        self.conn.execute("SELECT set_config('app.user_id', %s, false)", (str(self.user_id),))
        return self.conn

    def __exit__(self, *exc):
        self.conn.close()


def test_the_self_service_role_can_read_no_table_at_all(db):
    asha_id = db.execute("SELECT id FROM users WHERE email = %s", (ASHA_EMAIL,)).fetchone()["id"]
    tables = [r["t"] for r in db.execute("""SELECT c.relname AS t FROM pg_class c WHERE c.relnamespace = 'public'::regnamespace AND c.relkind IN ('r', 'v', 'p', 'm')
                                             ORDER BY 1""").fetchall()]
    assert len(tables) > 120 and {"journal_entries", "journal_lines", "employees", "payslips", "bank_transactions", "audit_log", "users", "v_cap_table"} <= set(tables)
    with As(asha_id) as c:
        for t in tables:
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                c.execute(f'SELECT 1 FROM public."{t}" LIMIT 1')
        for sql in ("INSERT INTO public.leave_requests (employee_id, leave_type_id, dates, days) VALUES (gen_random_uuid(), gen_random_uuid(), '[2027-01-01,2027-01-02)', 1)",
                    "UPDATE public.employees SET designation = 'CEO'", "DELETE FROM public.audit_log", "SELECT app.visible_entity_ids()", "SELECT app.current_user_id()",
                    "SELECT * FROM app.employee_names(gen_random_uuid())", "SELECT * FROM app.my_employments()", f"SELECT * FROM self.mine('{ASHA}')",
                    "SELECT self.note(NULL, 'x', 'y', NULL, '{}')", "CREATE TABLE self.mine_too (x int)", "CREATE TABLE public.mine_too (x int)"):
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                c.execute(sql)
        assert c.execute("SELECT full_name FROM self.profile(%s)", (ASHA,)).fetchone()["full_name"] == "Asha Rao"            # the door itself works
        assert [e["employee_id"] for e in c.execute("SELECT employee_id FROM self.employments()").fetchall()] == [uuid.UUID(ASHA)]
        for sql in ("SELECT * FROM self.profile(%s)", "SELECT * FROM self.payslips(%s)", "SELECT * FROM self.leave_requests(%s)", "SELECT * FROM self.leave_types(%s)",
                    "SELECT * FROM self.policy_documents(%s)", "SELECT * FROM self.onboarding_tasks(%s)", "SELECT self.leave_policy(%s, current_date)",
                    "SELECT * FROM self.holidays(%s, current_date, current_date + 400)", "SELECT * FROM self.leave_used(%s, current_date - 400, current_date + 400)",
                    "SELECT * FROM self.leave_adjustments(%s, current_date)", "SELECT self.cancel_leave(%s, gen_random_uuid())",
                    "SELECT self.acknowledge(%s, gen_random_uuid())", "SELECT * FROM self.payslip_lines(%s, gen_random_uuid())",
                    "SELECT * FROM self.payslip_year_to_date(%s, gen_random_uuid())",
                    "SELECT self.request_leave(%s, gen_random_uuid(), current_date + 5, current_date + 6, 2, 0, false, NULL)"):
            with pytest.raises(psycopg.errors.InsufficientPrivilege):                                                         # every function refuses a colleague's record
                c.execute(sql, (BALU,))
    with As(uuid.uuid4()) as c, pytest.raises(psycopg.errors.InsufficientPrivilege):                                          # and a signed-in stranger
        c.execute("SELECT * FROM self.profile(%s)", (ASHA,))
    funcs = db.execute("""SELECT p.proname, has_function_privilege('biz_self', p.oid, 'EXECUTE') AS self_can, has_function_privilege('public', p.oid, 'EXECUTE') AS anyone
                            FROM pg_proc p WHERE p.pronamespace = 'self'::regnamespace""").fetchall()
    assert all(not f["anyone"] for f in funcs) and {f["proname"] for f in funcs if not f["self_can"]} == {"mine", "note"}
    assert not db.execute("SELECT has_schema_privilege('biz_self', 'app', 'USAGE') AS x").fetchone()["x"]
    held = db.execute("""SELECT count(*) AS n FROM pg_class c WHERE c.relnamespace = 'public'::regnamespace AND c.relkind IN ('r', 'v', 'p', 'm', 'S')
                            AND (has_table_privilege('biz_self', c.oid, 'SELECT') OR has_table_privilege('biz_self', c.oid, 'UPDATE')
                                 OR (c.relkind <> 'S' AND has_table_privilege('biz_self', c.oid, 'INSERT, DELETE, TRUNCATE')))""").fetchone()["n"]
    assert held == 0                                                                                                           # not one privilege on a table, view or sequence


def test_hr_ends_the_access_at_once_and_the_audit_chain_holds(client, db):
    hr, asha = login(client, HR), login(client, ASHA_EMAIL)
    assert client.get(me(), headers=asha).status_code == 200
    r = client.delete(url(f"/employees/{ASHA}/self-service"), headers=hr)
    assert r.status_code == 200 and r.json()["self_service"] == "off", r.text
    for path in ("", "/leave", "/payslips", "/policies"):
        assert client.get(me(path), headers=asha).status_code == 404, path
    assert client.get("/v1/me/employments", headers=asha).json() == {"employments": [], "invitations": []}
    assert client.get("/v1/me", headers=asha).json()["employments"] == []
    actions = [a["action"] for a in db.execute("SELECT action FROM audit_log WHERE entity_id = %s AND subject = 'employees' ORDER BY id", (EN,)).fetchall()]
    assert actions == ["employee.self_service", "employee.self_service_accepted", "employee.self_service"]
    assert db.execute("SELECT app.audit_verify() AS broken").fetchone()["broken"] is None
