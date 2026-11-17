"""Module confidentiality in the database (migration 40). These read as `biz_app` on a raw connection, with no API check in the way:
a person without the module must get no rows from the tables behind it, whatever a handler forgets. Then the few places where the
API had to change because of it."""
import datetime as dt
import json
import os
import uuid

import psycopg
import pytest
from psycopg.rows import dict_row

DB = os.environ["DATABASE_URL"]
ORG = "00000000-0000-0000-0000-0000006d0000"
EN, OTHER_EN = "00000000-0000-0000-0000-0000006d0010", "00000000-0000-0000-0000-0000006d0011"
U = {k: "00000000-0000-0000-0000-0000006d01%02d" % i
     for i, k in enumerate(("owner", "sales", "finance", "hr", "payroll", "capital", "approver", "gst", "expired", "wild"), 1)}
SCOPES = {"sales": {"sales": "write", "gst": "write", "documents": "write"}, "finance": {"finance": "write"}, "hr": {"hr": "write"}, "payroll": {"payroll": "read"},
          "capital": {"capital": "read"}, "approver": {"sales": "approve"}, "wild": {"*": "read"}}
EMP, RUN, LEDGER, BANK, TXN, DOC = ("00000000-0000-0000-0000-0000006d02%02d" % i for i in range(1, 7))
MONTH = dt.date.today().replace(day=1)

# seeded here with a row each, so "nothing comes back" means something
PAY = ("employees", "salary_structures", "salary_components", "payslips", "payslip_lines", "payroll_run_inputs", "leave_requests")
CAPITAL = ("shareholders", "share_classes", "funding_rounds", "share_allotments", "esop_pools", "esop_grants")
BANK_TABLES = ("bank_transactions", "bank_matches")
TABLES = PAY + ("payroll_runs",) + CAPITAL + BANK_TABLES
# not seeded: covered by checking that the policy is there
UNSEEDED = ("leave_adjustments", "onboarding_tasks", "hr_policy_acks", "gratuity_valuations", "statutory_bonus", "share_transfers", "esop_exercises", "document_links")


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
    x("INSERT INTO organizations (id, name, kind) VALUES (%s, 'Rls Org', 'business_group')", (ORG,))
    x("""INSERT INTO entities (id, organization_id, legal_name, constitution, track) VALUES (%s, %s, 'Rls Co Pvt Ltd', 'private_limited', 'business'),
         (%s, %s, 'Rls Other Pvt Ltd', 'private_limited', 'business')""", (EN, ORG, OTHER_EN, ORG))
    for k, uid in U.items():
        x("INSERT INTO users (id, email, full_name) VALUES (%s, %s, %s)", (uid, f"rls-{k}@example.in", f"Rls {k}"))
    x("INSERT INTO memberships (entity_id, user_id, role, accepted_at) VALUES (%s, %s, 'owner', now())", (EN, U["owner"]))
    for k, scopes in SCOPES.items():
        x("INSERT INTO memberships (entity_id, user_id, role, scopes, accepted_at) VALUES (%s, %s, %s, %s, now())", (EN, U[k], "approver" if k == "approver" else "maker", json.dumps(scopes)))
    # two practitioners engaged as people for GST: one current, one whose engagement ran out yesterday
    for k, expires in (("gst", 30), ("expired", -1)):
        x("""INSERT INTO advisor_grants (entity_id, advisor_name, advisor_kind, practitioner_user_id, service, scopes, status, expires_on, accepted_at)
             VALUES (%s, %s, 'chartered_accountant', %s, 'gst', '{"gst": "certify", "sales": "read"}', 'active', current_date + %s, now())""", (EN, f"Rls {k}", U[k], expires))
    # in the other company the sales person holds payroll, capital and finance: it must not carry over to this one
    x("""INSERT INTO memberships (entity_id, user_id, role, scopes, accepted_at) VALUES (%s, %s, 'maker', '{"payroll": "write", "capital": "write", "finance": "write"}', now())""",
      (OTHER_EN, U["sales"]))

    # pay and people
    x("INSERT INTO employees (id, entity_id, employee_code, full_name, date_of_joining, work_state) VALUES (%s, %s, 'E1', 'Meera Iyer', '2022-01-01', '29')", (EMP, EN))
    st = x("INSERT INTO salary_structures (employee_id, effective, ctc_annual) VALUES (%s, daterange('2022-01-01', NULL), 600000) RETURNING id", (EMP,)).fetchone()["id"]
    x("INSERT INTO salary_components (structure_id, code, monthly_amount, kind, in_wages) VALUES (%s, 'BASIC', 25000, 'earning', true)", (st,))
    x("INSERT INTO payroll_runs (id, entity_id, period_month, status, headcount, paid_on) VALUES (%s, %s, %s, 'paid', 1, current_date)", (RUN, EN, MONTH))
    slip = x("""INSERT INTO payslips (run_id, employee_id, paid_days, gross, wages, pf_wage, pf_employee, pf_employer_epf, pf_employer_eps, esi_applicable, esi_employee, esi_employer,
                                      professional_tax, net_pay) VALUES (%s, %s, 30, 20000, 10000, 10000, 1200, 367, 833, true, 150, 650, 200, 18450) RETURNING id""", (RUN, EMP)).fetchone()["id"]
    x("INSERT INTO payslip_lines (payslip_id, code, label, kind, amount) VALUES (%s, 'BASIC', 'Basic', 'earning', 20000)", (slip,))
    x("INSERT INTO payroll_run_inputs (run_id, inputs) VALUES (%s, '{}')", (RUN,))
    lt = x("INSERT INTO leave_types (entity_id, code, name) VALUES (%s, 'RLS', 'Rls leave') RETURNING id", (EN,)).fetchone()["id"]
    x("INSERT INTO leave_requests (employee_id, leave_type_id, dates, days) VALUES (%s, %s, daterange(current_date + 10, current_date + 12), 2)", (EMP, lt))
    # the cap table
    sc = x("INSERT INTO share_classes (entity_id, name, instrument, face_value) VALUES (%s, 'Equity', 'equity', 10) RETURNING id", (EN,)).fetchone()["id"]
    sh = x("INSERT INTO shareholders (entity_id, name, kind) VALUES (%s, 'Founder One', 'founder') RETURNING id", (EN,)).fetchone()["id"]
    fr = x("INSERT INTO funding_rounds (entity_id, name, share_class_id) VALUES (%s, 'Seed', %s) RETURNING id", (EN, sc)).fetchone()["id"]
    x("INSERT INTO share_allotments (entity_id, round_id, share_class_id, shareholder_id, quantity, issue_price) VALUES (%s, %s, %s, %s, 1000, 10)", (EN, fr, sc, sh))
    pool = x("INSERT INTO esop_pools (entity_id, name, approved_on, size_options) VALUES (%s, 'ESOP 2026', current_date - 30, 1000) RETURNING id", (EN,)).fetchone()["id"]
    x("INSERT INTO esop_grants (pool_id, employee_id, granted_on, options, strike_price) VALUES (%s, %s, current_date - 20, 100, 10)", (pool, EMP))
    # the bank statement, with the salary on it
    x("INSERT INTO ledger_accounts (id, entity_id, code, name, type, subtype) VALUES (%s, %s, '1100', 'HDFC current', 'asset', 'bank')", (LEDGER, EN))
    x("INSERT INTO bank_accounts (id, entity_id, ledger_account_id, bank_name, account_last4, ifsc, kind) VALUES (%s, %s, %s, 'HDFC', '4410', 'HDFC0000123', 'current')", (BANK, EN, LEDGER))
    x("""INSERT INTO bank_transactions (id, bank_account_id, entity_id, txn_date, amount, description, external_id)
         VALUES (%s, %s, %s, current_date, -18450, 'NEFT SALARY MEERA IYER', 'RLS-1')""", (TXN, BANK, EN))
    x("INSERT INTO bank_matches (bank_transaction_id, target_kind, target_id, amount, status) VALUES (%s, 'payroll_run', %s, 18450, 'suggested')", (TXN, RUN))
    # the vault: one document in the payroll folder, one elsewhere
    x("""INSERT INTO documents (id, entity_id, folder, title, file_name, mime_type, size_bytes, sha256, storage_key, source)
         VALUES (%s, %s, 'payroll', 'Salary register', 'register.pdf', 'application/pdf', 10, %s, 'rls/register.pdf', 'upload'),
                (gen_random_uuid(), %s, 'sales', 'Price list', 'prices.pdf', 'application/pdf', 10, %s, 'rls/prices.pdf', 'upload')""", (DOC, EN, "ab" * 32, EN, "ac" * 32))
    yield


class As:
    """A raw connection the way a request has it: SET LOCAL ROLE biz_app and the user's id, nothing else."""
    def __init__(self, user_id):
        self.user_id = user_id

    def __enter__(self):
        self.conn = psycopg.connect(DB, row_factory=dict_row)
        self.cur = self.conn.cursor()
        self.cur.execute("SET LOCAL ROLE biz_app")
        self.cur.execute("SELECT set_config('app.user_id', %s, true)", (str(self.user_id),))
        return self

    def __exit__(self, *exc):
        self.conn.rollback()
        self.conn.close()

    def count(self, table, where="true") -> int:
        return self.cur.execute(f"SELECT count(*) AS n FROM {table} WHERE {where}").fetchone()["n"]

    def one(self, sql, args=()):
        return self.cur.execute(sql, args).fetchone()


def counts(user_id) -> dict:
    with As(user_id) as a:
        return {t: a.count(t) for t in TABLES}


def login(client, email):
    r = client.post("/v1/auth/token", json={"grant_type": "otp", "email": email, "otp": "000000"})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


# ------------------------------------------------------------------ the raw-connection test of the design
def test_the_owner_and_a_member_with_every_module_still_read_everything():
    mine = counts(U["owner"])
    assert all(n > 0 for n in mine.values()), {t: n for t, n in mine.items() if not n}
    assert counts(U["wild"]) == mine                                                        # `*` stands for every module


@pytest.mark.parametrize("who", ["sales", "gst", "expired", "approver"])
def test_without_the_module_every_sensitive_table_is_empty(who):
    """A member with sales and GST, a practitioner engaged for GST, one whose engagement ended, and an approver of sales."""
    got = counts(U[who])
    assert got == {t: 0 for t in TABLES}, {t: n for t, n in got.items() if n}


def test_each_module_opens_its_own_tables_and_no_others():
    full = counts(U["owner"])
    hr, payroll, finance, capital = (counts(U[k]) for k in ("hr", "payroll", "finance", "capital"))
    for t in PAY + ("payroll_runs",):
        assert hr[t] == full[t] and payroll[t] == full[t], t
        assert capital[t] == 0, t
    for t in PAY:
        assert finance[t] == 0, t                                                           # finance never reads a payslip or a salary
    assert finance["payroll_runs"] == full["payroll_runs"]                                  # only the run: its month and totals, to match the bank
    for t in CAPITAL:
        assert capital[t] == full[t] and hr[t] == 0 and payroll[t] == 0 and finance[t] == 0, t
    for t in BANK_TABLES:
        assert finance[t] == full[t] and hr[t] == 0 and payroll[t] == 0 and capital[t] == 0, t


def test_every_sensitive_table_carries_the_policy_or_follows_a_parent_that_does(db):
    """The tables this file does not fill are checked in the catalogue: a restrictive read policy of their own, or a parent with one."""
    own = {r["t"] for r in db.execute("""SELECT c.relname AS t FROM pg_policy p JOIN pg_class c ON c.oid = p.polrelid
                                          WHERE p.polname = 'module_access' AND NOT p.polpermissive AND p.polcmd = '*'""").fetchall()}
    via = {r["t"]: r["q"] for r in db.execute("""SELECT c.relname AS t, pg_get_expr(p.polqual, p.polrelid) AS q FROM pg_policy p JOIN pg_class c ON c.oid = p.polrelid
                                                  WHERE p.polname = 'via_parent'""").fetchall()}
    parent = {"salary_structures": "employees", "salary_components": "salary_structures", "leave_requests": "employees", "payslip_lines": "payslips", "esop_grants": "esop_pools",
              "esop_exercises": "esop_grants", "bank_matches": "bank_transactions", "document_links": "documents"}
    for t in TABLES + UNSEEDED:
        top = t
        while top in parent:
            assert top in via and parent[top] in via[top], f"{top} is not read through {parent[top]}"
            top = parent[top]
        assert top in own, f"{t} has no module policy (through {top})"
    assert "documents" in own
    for t in own:                                                                           # and none of them was left without row-level security forced
        assert db.execute("SELECT relrowsecurity AND relforcerowsecurity AS ok FROM pg_class WHERE relname = %s AND relnamespace = 'public'::regnamespace", (t,)).fetchone()["ok"], t


def test_a_module_held_in_another_company_does_not_carry_over():
    with As(U["sales"]) as a:                                                               # payroll, capital and finance in the other company only
        assert a.count("employees") == 0 and a.count("shareholders") == 0 and a.count("bank_transactions") == 0
        assert a.one("SELECT app.can(%s, 'payroll') AS here, app.can(%s, 'payroll', 'write') AS there, app.can(%s, 'payroll', 'approve') AS too_much",
                     (EN, OTHER_EN, OTHER_EN)) == {"here": False, "there": True, "too_much": False}


def test_what_cannot_be_read_cannot_be_changed_either(db):
    with As(U["sales"]) as a:
        for sql in ("UPDATE employees SET designation = 'Leaked'", "DELETE FROM bank_transactions", "UPDATE payslips SET net_pay = 1", "UPDATE shareholders SET name = 'Leaked'"):
            a.cur.execute(sql)
            assert a.cur.rowcount == 0, sql
        assert a.count("v_cap_table", f"entity_id = '{EN}'") == 0 and a.count("v_wage_code_check", f"entity_id = '{EN}'") == 0       # the views read as the caller
    for sql in (f"INSERT INTO employees (entity_id, employee_code, full_name, date_of_joining, work_state) VALUES ('{EN}', 'X9', 'Planted', '2024-01-01', '29')",
                f"INSERT INTO shareholders (entity_id, name, kind) VALUES ('{EN}', 'Planted', 'founder')",
                f"UPDATE documents SET folder = 'payroll' WHERE entity_id = '{EN}' AND folder = 'sales'"):
        with As(U["sales"]) as a, pytest.raises(psycopg.errors.InsufficientPrivilege):      # nor can a row be planted in a module they do not hold
            a.cur.execute(sql)
    with As(U["owner"]) as a:
        assert a.count("v_cap_table", f"entity_id = '{EN}'") > 0
    assert db.execute("SELECT designation FROM employees WHERE id = %s", (EMP,)).fetchone()["designation"] is None


def test_can_answers_by_level_for_members_and_practitioners():
    def can(user, module, level="read", entity=EN):
        with As(U[user]) as a:
            return a.one("SELECT app.can(%s, %s, %s) AS ok", (entity, module, level))["ok"]
    assert can("owner", "payroll", "file") and can("owner", "capital", "approve")
    assert can("hr", "hr") and can("hr", "hr", "write") and not can("hr", "hr", "approve") and not can("hr", "payroll")
    assert can("payroll", "payroll") and not can("payroll", "payroll", "write")
    assert can("wild", "capital") and not can("wild", "capital", "write")
    assert can("gst", "gst", "file") and can("gst", "gst", "write") and can("gst", "sales") and not can("gst", "sales", "write")       # certify files, read only reads
    assert not can("gst", "gst", "approve") and not can("gst", "payroll")                                                           # a practitioner never approves
    assert not can("expired", "gst") and not can("hr", "hr", "bogus") and not can("hr", "hr", entity=str(uuid.uuid4()))


def test_an_api_key_reads_only_the_modules_its_permissions_name(db):
    with As(U["owner"]) as a:
        key = a.one("SELECT app.create_api_key(%s, 'rls key', 'rlskey01', repeat('a', 64), ARRAY['sales:read', 'payroll:read'], NULL) AS id", (EN,))["id"]
        a.conn.commit()
    svc = db.execute("SELECT acts_as_user FROM api_keys WHERE id = %s", (key,)).fetchone()["acts_as_user"]
    got = counts(svc)
    assert got["employees"] == 1 and got["payslips"] == 1 and got["shareholders"] == 0 and got["bank_transactions"] == 0
    db.execute("""UPDATE memberships SET scopes = '{"*": "file"}' WHERE user_id = %s""", (svc,))                                      # even if its membership were widened
    got = counts(svc)
    assert got["employees"] == 1 and got["shareholders"] == 0 and got["bank_transactions"] == 0


# ------------------------------------------------------------------ the little that other modules are handed
def test_a_challan_gets_the_months_total_without_the_payslips():
    q = "SELECT run_id, amount FROM app.payroll_statutory_total(%s, %s, %s)"
    with As(U["gst"]) as a:                                                                 # sees the company, not payroll
        assert a.count("payslips") == 0
        for what, amount in (("pf", 2400), ("esi", 800), ("pt", 200)):
            got = a.one(q, (EN, MONTH, what))
            assert str(got["run_id"]) == RUN and got["amount"] == amount, what
        assert a.one(q, (EN, MONTH - dt.timedelta(days=400), "pf")) is None                 # no run that month
        assert a.one(q, (OTHER_EN, MONTH, "pf")) is None                                    # a company they cannot see
        with pytest.raises(psycopg.Error):
            a.one(q, (EN, MONTH, "net_pay"))                                                # only the three totals, never an arbitrary column
    with As(str(uuid.uuid4())) as a:                                                        # a stranger gets nothing, not an error
        assert a.one(q, (EN, MONTH, "pf")) is None


def test_bank_matching_counts_payslips_and_the_cap_table_gets_names_only():
    q = "SELECT app.payslips_with_net_pay(%s, %s) AS n"
    with As(U["finance"]) as a:
        assert a.one(q, (RUN, 18450))["n"] == 1 and a.one(q, (RUN, 1))["n"] == 0 and a.count("payslips") == 0
    with As(U["sales"]) as a:
        assert a.one(q, (RUN, 18450))["n"] == 0
    with As(U["capital"]) as a:
        rows = a.cur.execute("SELECT * FROM app.employee_names(%s)", (EN,)).fetchall()
        assert [r["full_name"] for r in rows] == ["Meera Iyer"] and set(rows[0]) == {"id", "full_name", "employee_code", "status"} and a.count("employees") == 0
    with As(U["sales"]) as a:
        assert a.one("SELECT count(*) AS n FROM app.employee_names(%s)", (EN,))["n"] == 0


# ------------------------------------------------------------------ through the API
def url(path):
    return f"/v1/entities/{EN}{path}"


def test_the_payroll_folder_of_the_vault_is_closed_to_others(client, db):
    with As(U["sales"]) as a:
        assert a.count("documents", "folder = 'payroll'") == 0 and a.count("documents", "folder <> 'payroll'") == 1
    with As(U["hr"]) as a:
        assert a.count("documents", f"id = '{DOC}'") == 1
    s = login(client, "rls-sales@example.in")
    listed = client.get(url("/documents?folder=payroll"), headers=s)
    assert listed.status_code == 200 and listed.json()["data"] == [], listed.text
    assert len(client.get(url("/documents"), headers=s).json()["data"]) == 1
    assert client.get(url(f"/documents/{DOC}"), headers=s).status_code == 404
    body = {"folder": "payroll", "title": "Mine", "file_name": "x.pdf", "mime_type": "application/pdf", "size_bytes": 10, "sha256": "cd" * 32}
    r = client.post(url("/documents"), headers=s, json=body)
    assert r.status_code == 403 and r.json()["code"] == "permission_denied", r.text
    made = client.post(url("/documents"), headers=s, json={**body, "folder": "sales"})
    assert made.status_code in (200, 201), made.text
    moved = client.patch(url(f"/documents/{made.json()['document']['id']}"), headers=s, json={"folder": "payroll"})
    assert moved.status_code == 403, moved.text                                             # nor moved into it
    h = login(client, "rls-hr@example.in")                                                  # HR has no vault access through the API, but the folder is theirs in the database
    assert client.get(url("/documents"), headers=h).status_code == 403


def test_an_approver_who_cannot_see_pay_cannot_decide_a_payroll_run(client, db):
    ar = db.execute("""INSERT INTO approval_requests (entity_id, subject, subject_id, summary, requested_by) VALUES (%s, 'payroll_run', %s, 'Approve payroll (rls test)', %s)
                       RETURNING id""", (EN, RUN, U["owner"])).fetchone()["id"]
    h = login(client, "rls-approver@example.in")
    for outcome in ("approve", "reject"):
        r = client.post(url(f"/approvals/{ar}/{outcome}"), headers={**h, "Idempotency-Key": str(uuid.uuid4())}, json={})
        assert r.status_code == 403 and r.json()["code"] == "permission_denied", r.text
    assert db.execute("SELECT status FROM approval_requests WHERE id = %s", (ar,)).fetchone()["status"] == "pending"


def test_the_esop_register_names_its_people_for_someone_with_only_the_cap_table(client):
    h = login(client, "rls-capital@example.in")
    r = client.get(url("/esop-grants"), headers=h)
    assert r.status_code == 200 and [g["employee_name"] for g in r.json()["data"]] == ["Meera Iyer"], r.text
    assert client.get(url("/employees"), headers=h).status_code == 403


def test_someone_who_prepares_the_pf_challan_without_payroll_gets_the_right_amount(db):
    """Before migration 40 the preparer summed payslips as the caller; a person without payroll would now have read zero."""
    from app.core import tx
    from app.routes_gst import PREPARERS
    ob = {"entity_id": uuid.UUID(EN), "period_start": MONTH, "period_label": f"{MONTH:%b %Y}", "registration_id": None, "id": None}
    with tx(U["sales"]) as cur:                                                             # gst: write, no payroll
        cur.execute("SELECT count(*) AS n FROM payslips")
        assert cur.fetchone()["n"] == 0
        out = PREPARERS["EPF.ECR.MONTHLY"](cur, ob)
    assert out["amount"] == "2400.00" and out["challan_id"], out
    assert db.execute("SELECT amount FROM tax_challans WHERE id = %s", (out["challan_id"],)).fetchone()["amount"] == 2400
