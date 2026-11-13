"""Roles: the level-per-module rules and presets (pure), then inviting people with a preset, what each can and cannot do, and who may
manage access, through the API."""
import datetime as dt
import os
import uuid

import psycopg
import pytest
from psycopg.rows import dict_row

from app import roles

DB = os.environ["DATABASE_URL"]
ORG = "00000000-0000-0000-0000-0000005a7000"
EN = "00000000-0000-0000-0000-0000005a7001"
U_OWNER, U_LEGACY = "00000000-0000-0000-0000-0000005a7011", "00000000-0000-0000-0000-0000005a7012"
OWNER, LEGACY = "role-owner@example.in", "role-legacy-maker@example.in"
ACCOUNTANT, HR_EXEC, CFO, SECOND_OWNER = "role-accountant@example.in", "role-hr@example.in", "role-cfo@example.in", "role-cofounder@example.in"
S: dict = {}


# ------------------------------------------------------------------ pure
def test_a_level_is_looked_up_per_module_with_a_fallback_for_everything_else():
    cfo = {"finance": "file", "payroll": "approve", "hr": "read"}
    assert roles.level_for(cfo, "finance") == "file" and roles.level_for(cfo, "hr") == "read" and roles.level_for(cfo, "legal") is None
    assert roles.level_for({"*": "read", "gst": "write"}, "legal") == "read" and roles.level_for({"*": "read", "gst": "write"}, "gst") == "write"
    assert roles.level_for(cfo, "approvals") == "file" and roles.level_for({"hr": "write"}, "tasks") == "write"          # shared: the person's highest level


def test_each_level_allows_what_the_ones_below_it_do():
    s = {"finance": "file", "payroll": "approve", "hr": "write", "legal": "comment", "gst": "read"}
    assert roles.allows(s, "finance", "pay") and roles.allows(s, "finance", "file")
    assert roles.allows(s, "payroll", "approve") and not roles.allows(s, "payroll", "file") and not roles.allows(s, "payroll", "pay")
    assert roles.allows(s, "hr", "write") and not roles.allows(s, "hr", "approve")
    assert roles.allows(s, "legal", "read") and not roles.allows(s, "legal", "write")
    assert roles.allows(s, "gst", "read") and not roles.allows(s, "gst", "write") and not roles.allows(s, "capital", "read")
    assert roles.allows({"*": "write"}, "copilot", "use") and not roles.allows({"*": "read"}, "copilot", "use")


def test_scopes_are_checked_and_stored_one_way():
    assert roles.clean_scopes({"*": "read", "gst": "read", "finance": "write"}) == {"*": "read", "finance": "write"}        # repeating the fallback says nothing
    for bad in ({}, {"bogus": "read"}, {"finance": "admin"}, {"approvals": "approve"}, None):
        with pytest.raises(roles.ScopeError):
            roles.clean_scopes(bad)


def test_the_home_follows_the_work_when_nobody_chose():
    assert roles.default_home({"*": "file"}, owner=True) == "leadership"
    assert roles.default_home({"finance": "write", "gst": "write"}) == "finance" and roles.default_home({"hr": "approve", "payroll": "write"}) == "people"
    assert roles.default_home({"*": "read"}) == "general" and roles.default_home({"finance": "read", "hr": "read"}) == "general"


def test_every_preset_uses_real_modules_and_levels():
    for key, p in roles.PRESETS.items():
        assert p["kind"] in ("member", "practitioner") and p["title"] and p["about"], key
        levels = roles.LEVELS if p["kind"] == "member" else ("read", "comment", "prepare", "certify")
        for module, level in p["scopes"].items():
            assert (module == "*" or module in roles.MODULES) and module not in roles.SHARED and level in levels, (key, module, level)
        if p["kind"] == "member":
            assert p["home"] in roles.HOMES and roles.clean_scopes(p["scopes"]), key
    auditor = roles.PRESETS["statutory_auditor"]["scopes"]
    assert set(auditor.values()) <= {"read", "comment"}                                    # an auditor never prepares or files
    assert "payroll" not in roles.PRESETS["accountant"]["scopes"] and "payroll" not in roles.PRESETS["hr_executive"]["scopes"]
    assert {p["key"] for p in roles.presets_out()} == set(roles.PRESETS)


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


@pytest.fixture(scope="module", autouse=True)
def seed(db):
    db.execute("INSERT INTO organizations (id, name, kind) VALUES (%s, 'Roles Org', 'business_group')", (ORG,))
    db.execute("INSERT INTO entities (id, organization_id, legal_name, constitution, track) VALUES (%s, %s, 'Roles Test Pvt Ltd', 'private_limited', 'business')", (EN, ORG))
    db.execute("INSERT INTO users (id, email, full_name) VALUES (%s, %s, 'Role Owner'), (%s, %s, 'Old Maker')", (U_OWNER, OWNER, U_LEGACY, LEGACY))
    db.execute("INSERT INTO memberships (entity_id, user_id, role, accepted_at) VALUES (%s, %s, 'owner', now())", (EN, U_OWNER))
    db.execute("INSERT INTO memberships (entity_id, user_id, role, modules, accepted_at) VALUES (%s, %s, 'maker', '{gst,finance}', now())", (EN, U_LEGACY))      # written the earlier way
    yield


def me_entity(client, h):
    return next(e for e in client.get("/v1/me", headers=h).json()["entities"] if e["id"] == EN)


def test_members_written_the_earlier_way_get_scopes_and_the_two_stay_in_step(db):
    rows = {r["user_id"]: r for r in db.execute("SELECT user_id::text, role::text, modules, scopes FROM memberships WHERE entity_id = %s", (EN,)).fetchall()}
    assert rows[U_OWNER]["scopes"] == {"*": "file"} and rows[U_OWNER]["modules"] == ["*"]
    assert rows[U_LEGACY]["scopes"] == {"gst": "write", "finance": "write"} and rows[U_LEGACY]["role"] == "maker"
    # writing scopes moves the role and the module list; writing the role the earlier way moves the scopes
    db.execute("UPDATE memberships SET scopes = '{\"hr\": \"approve\", \"payroll\": \"read\"}' WHERE user_id = %s AND entity_id = %s", (U_LEGACY, EN))
    r = db.execute("SELECT role::text, modules FROM memberships WHERE user_id = %s AND entity_id = %s", (U_LEGACY, EN)).fetchone()
    assert r == {"role": "approver", "modules": ["hr", "payroll"]}
    db.execute("UPDATE memberships SET role = 'maker', modules = '{gst,finance}' WHERE user_id = %s AND entity_id = %s", (U_LEGACY, EN))
    assert db.execute("SELECT scopes FROM memberships WHERE user_id = %s AND entity_id = %s", (U_LEGACY, EN)).fetchone()["scopes"] == {"gst": "write", "finance": "write"}
    for bad in ('{}', '{"finance": "boss"}', '{"Fin ance": "read"}'):
        with pytest.raises(psycopg.errors.CheckViolation):
            db.execute("UPDATE memberships SET scopes = %s::jsonb WHERE user_id = %s AND entity_id = %s", (bad, U_LEGACY, EN))


def test_the_presets_are_listed_with_the_modules_and_levels(client):
    r = client.get("/v1/role-presets", headers=login(client, OWNER))
    assert r.status_code == 200, r.text
    out = r.json()
    by = {p["key"]: p for p in out["presets"]}
    assert {"ceo", "coo", "cfo", "accountant", "hr_manager", "hr_executive", "viewer", "gst_consultant", "statutory_auditor"} <= set(by)
    assert by["cfo"]["home"] == "finance" and by["cfo"]["scopes"]["payroll"] == "approve" and by["ceo"]["owner"] is True and by["gst_consultant"]["kind"] == "practitioner"
    assert out["levels"] == ["read", "comment", "write", "approve", "file"]
    keys = [m["key"] for m in out["modules"]]
    assert "hr" in keys and "inventory" in keys and "approvals" not in keys
    assert client.get("/v1/role-presets").status_code == 401


def test_an_accountant_is_invited_with_a_preset_and_lands_on_the_finance_home(client):
    o = login(client, OWNER)
    r = client.post(url("/members"), headers=o, json={"email": ACCOUNTANT, "full_name": "Anil Accounts", "preset": "accountant"})
    assert r.status_code == 201, r.text
    m = r.json()
    S["accountant"] = m["id"]
    assert (m["role"], m["title"], m["preset"], m["home"]) == ("maker", "Accountant", "accountant", "finance")
    assert m["scopes"]["finance"] == "write" and "payroll" not in m["scopes"] and "payroll" not in m["modules"]
    assert client.post(url("/members"), headers=o, json={"email": ACCOUNTANT, "preset": "accountant"}).status_code == 409
    a = login(client, ACCOUNTANT)
    e = me_entity(client, a)
    assert (e["home"], e["title"], e["role"]) == ("finance", "Accountant", "maker") and e["scopes"]["gst"] == "write" and "payroll" not in e["modules"]
    assert me_entity(client, o)["home"] == "general" and me_entity(client, o)["scopes"] == {"*": "file"}      # the seeded owner never chose a home


def test_the_accountant_prepares_the_books_but_cannot_see_payroll_or_approve(client):
    a = login(client, ACCOUNTANT)
    assert client.get(url("/finance/summary"), headers=a).status_code == 200
    assert client.get(url("/stock-items"), headers=a).status_code == 200                                     # inventory is its own module, and the preset has it
    for path in ("/payroll-runs", "/employees", "/hr/people", "/leave-requests", "/hr/policy", "/cap-table"):
        r = client.get(url(path), headers=a)
        assert r.status_code == 403 and r.json()["code"] == "permission_denied", path
    r = client.post(url("/items"), headers=a, json={"sku": "ROLE-1", "name": "Bolt", "hsn_sac": "7318", "gst_rate": 18, "track_inventory": True})
    assert r.status_code == 201, r.text                                                                       # write
    r = client.put(url("/inventory/settings"), headers=a, json={"allow_negative_stock": True})
    assert r.status_code == 403                                                                               # approve is above the accountant's level
    assert client.get(url("/approvals"), headers=a).status_code == 200                                        # approvals and tasks follow the highest level


def test_only_an_owner_manages_who_has_access(client, db):
    o, a, legacy = login(client, OWNER), login(client, ACCOUNTANT), login(client, LEGACY)
    db.execute("UPDATE memberships SET scopes = '{\"*\": \"write\"}' WHERE user_id = %s AND entity_id = %s", (U_LEGACY, EN))      # a preparer with every module, settings included
    for h in (a, legacy):
        r = client.post(url("/members"), headers=h, json={"email": "sneaky@example.in", "preset": "ceo"})
        assert r.status_code == 403 and "owner" in r.json()["title"], r.text
        assert client.patch(url(f"/members/{S['accountant']}"), headers=h, json={"owner": True}).status_code == 403
        assert client.delete(url(f"/members/{S['accountant']}"), headers=h).status_code == 403
    assert client.get(url("/members"), headers=legacy).status_code == 200                                     # reading the list is still a settings permission
    assert client.post(f"/v1/entities/{uuid.uuid4()}/members", headers=o, json={"email": "x@example.in", "preset": "viewer"}).status_code == 404
    assert db.execute("SELECT count(*) AS n FROM users WHERE email = 'sneaky@example.in'").fetchone()["n"] == 0


def test_levels_differ_by_module_for_one_person(client):
    o = login(client, OWNER)
    r = client.post(url("/members"), headers=o, json={"email": CFO, "preset": "cfo", "title": "Finance head"})
    assert r.status_code == 201 and r.json()["title"] == "Finance head" and r.json()["role"] == "approver"
    c = login(client, CFO)
    assert client.get(url("/hr/policy"), headers=c).status_code == 200                                        # hr: read
    body = client.get(url("/hr/policy"), headers=o).json()
    put = {"effective_from": str(dt.date.today()), **{k: body[k] for k in ("salary", "leave", "payroll", "onboarding")}}
    assert client.put(url("/hr/policy"), headers=c, json=put).status_code == 403                              # …but not approve
    assert client.put(url("/inventory/settings"), headers=c, json={"allow_negative_stock": False}).status_code == 200      # inventory: file
    assert client.get(url("/payroll-runs"), headers=c).status_code == 200


def test_hr_can_be_given_without_salaries(client):
    o = login(client, OWNER)
    r = client.post(url("/members"), headers=o, json={"email": HR_EXEC, "scopes": {"hr": "write"}, "title": "HR executive"})
    assert r.status_code == 201 and (r.json()["home"], r.json()["preset"], r.json()["role"]) == ("people", None, "maker"), r.text
    S["hr"] = r.json()["id"]
    h = login(client, HR_EXEC)
    assert client.get(url("/hr/people"), headers=h).json() == {"data": []}
    assert client.get(url("/leave-types"), headers=h).status_code == 200 and client.get(url("/holidays"), headers=h).status_code == 200
    assert client.post(url("/holidays"), headers=h, json={"date": "2027-01-26", "name": "Republic Day"}).status_code == 201
    for path in ("/employees", "/payroll-runs", "/payroll/ctc-breakup?ctc_annual=600000"):
        assert client.get(url(path), headers=h).status_code == 403, path
    hire = {"full_name": "New Person", "date_of_joining": str(dt.date.today()), "work_state": "29", "ctc_annual": "600000"}
    assert client.post(url("/employees/onboard"), headers=h, json=hire).status_code == 403                    # onboarding sets a salary: it needs payroll too
    made = client.post(url("/employees/onboard"), headers=o, json=hire)
    assert made.status_code == 201, made.text
    eid = made.json()["employee"]["id"]
    seen = client.get(url(f"/employees/{eid}/onboarding"), headers=h)
    assert seen.status_code == 200 and seen.json()["employee"]["current_structure"] is None                   # the checklist, without the salary
    assert client.get(url(f"/employees/{eid}/onboarding"), headers=o).json()["employee"]["current_structure"]["ctc_annual"] == "600000.00"
    r = client.patch(url(f"/employees/{eid}"), headers=h, json={"pan": "ABCDE1234F"})
    assert r.status_code == 200 and r.json()["pan"] == "ABCDE1234F" and r.json()["current_structure"] is None     # HR records the person's details
    assert [p["has_pan"] for p in client.get(url("/hr/people"), headers=h).json()["data"]] == [True]
    assert client.post(url("/leave-types"), headers=h, json={"code": "WFH", "name": "Work from home", "annual_quota": "12"}).status_code == 403      # approve


def test_an_owner_changes_what_a_person_may_do(client, db):
    o = login(client, OWNER)
    r = client.patch(url(f"/members/{S['hr']}"), headers=o, json={"preset": "hr_manager"})
    assert r.status_code == 200, r.text
    m = r.json()
    assert (m["preset"], m["title"], m["role"], m["scopes"]["hr"], m["scopes"]["payroll"]) == ("hr_manager", "HR executive", "approver", "approve", "write")
    assert client.get(url("/payroll-runs"), headers=login(client, HR_EXEC)).status_code == 200
    r = client.patch(url(f"/members/{S['hr']}"), headers=o, json={"title": "Head of People", "home": "leadership"})
    assert r.status_code == 200 and (r.json()["title"], r.json()["home"], r.json()["scopes"]["hr"]) == ("Head of People", "leadership", "approve")
    for body, field in (({"scopes": {"bogus": "read"}}, "scopes"), ({"scopes": {}}, "scopes"), ({"preset": "gst_consultant"}, "preset"), ({"scopes": {"finance": "admin"}}, "scopes")):
        bad = client.patch(url(f"/members/{S['hr']}"), headers=o, json=body)
        assert bad.status_code == 400 and bad.json()["errors"][0]["field"] == field, bad.text
    assert client.patch(url(f"/members/{S['hr']}"), headers=o, json={}).status_code == 400
    assert client.patch(url(f"/members/{uuid.uuid4()}"), headers=o, json={"title": "x"}).status_code == 404
    mine = db.execute("SELECT id FROM memberships WHERE user_id = %s AND entity_id = %s", (U_OWNER, EN)).fetchone()["id"]
    assert client.patch(url(f"/members/{mine}"), headers=o, json={"preset": "viewer"}).status_code == 409      # never your own access
    assert db.execute("SELECT count(*) AS n FROM audit_log WHERE entity_id = %s AND action = 'member.access_changed'", (EN,)).fetchone()["n"] == 2


def test_a_second_owner_can_be_made_and_one_owner_always_remains(client, db):
    o = login(client, OWNER)
    r = client.post(url("/members"), headers=o, json={"email": SECOND_OWNER, "preset": "ceo"})
    assert r.status_code == 201 and (r.json()["role"], r.json()["scopes"], r.json()["home"]) == ("owner", {"*": "file"}, "leadership")
    co = login(client, SECOND_OWNER)
    mine = db.execute("SELECT id FROM memberships WHERE user_id = %s AND entity_id = %s", (U_OWNER, EN)).fetchone()["id"]
    r = client.patch(url(f"/members/{mine}"), headers=co, json={"owner": False})
    assert r.status_code == 400 and r.json()["errors"][0]["field"] == "scopes"                                # say what they may do instead
    r = client.patch(url(f"/members/{mine}"), headers=co, json={"preset": "viewer"})
    assert r.status_code == 200 and (r.json()["role"], r.json()["scopes"]) == ("viewer", {"*": "read"})
    assert client.post(url("/members"), headers=o, json={"email": "late@example.in", "preset": "viewer"}).status_code == 403      # no longer an owner
    assert client.delete(url(f"/members/{mine}"), headers=co).status_code == 204
    theirs = r = db.execute("SELECT m.id FROM memberships m JOIN users u ON u.id = m.user_id WHERE u.email = %s AND m.entity_id = %s", (SECOND_OWNER, EN)).fetchone()["id"]
    assert client.delete(url(f"/members/{theirs}"), headers=co).status_code == 409                            # cannot remove yourself
    assert db.execute("SELECT app.audit_verify() AS v").fetchone()["v"] is None


def test_the_earlier_way_of_inviting_still_works(client):
    co = login(client, SECOND_OWNER)
    r = client.post(url("/members"), headers=co, json={"email": "old-style@example.in", "role": "viewer", "modules": ["gst", "finance"]})
    assert r.status_code == 201 and (r.json()["role"], r.json()["scopes"]) == ("viewer", {"gst": "read", "finance": "read"}), r.text
    assert client.post(url("/members"), headers=co, json={"email": "nothing@example.in"}).status_code == 400
