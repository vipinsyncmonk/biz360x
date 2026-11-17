"""Practitioner engagements: the scope rules (pure), then inviting a practitioner, accepting as a person or for a firm, audit mode,
independence, a firm's staff and their clients, and ending an engagement, through the API."""
import datetime as dt
import json
import os
import uuid

import psycopg
import pytest
from psycopg.rows import dict_row

from app import roles

DB = os.environ["DATABASE_URL"]
ORG, FIRM, OTHER_FIRM = ("00000000-0000-0000-0000-0000007a700%d" % i for i in (0, 1, 2))
EN = "00000000-0000-0000-0000-0000007a7010"
U_OWNER, U_MAKER, U_PARTNER, U_STAFF = ("00000000-0000-0000-0000-0000007a702%d" % i for i in (1, 2, 3, 4))
OWNER, MAKER, PARTNER, STAFF = "eng-owner@example.in", "eng-maker@example.in", "eng-partner@example.in", "eng-staff@example.in"
GSTMAN, CS, AUDITOR = "eng-gst@example.in", "eng-cs@example.in", "eng-auditor@example.in"
TODAY = dt.date.today()
NEXT_YEAR = str(TODAY + dt.timedelta(days=365))
S: dict = {}


# ------------------------------------------------------------------ pure
def test_a_practitioners_scopes_use_their_own_levels_and_never_reach_settings():
    assert roles.clean_grant_scopes({"gst": "certify", "sales": "read"}) == {"gst": "certify", "sales": "read"}
    for bad in ({}, {"settings": "read"}, {"approvals": "read"}, {"bogus": "read"}, {"gst": "approve"}, {"gst": "file"}):
        with pytest.raises(roles.ScopeError):
            roles.clean_grant_scopes(bad)
    assert roles.clean_grant_scopes({"finance": "comment", "sales": "read"}, "statutory_audit")
    for service in roles.AUDIT_SERVICES:
        with pytest.raises(roles.ScopeError):
            roles.clean_grant_scopes({"finance": "prepare"}, service)


def test_levels_from_several_engagements_combine_to_the_highest():
    merged = roles.merge_grant_scopes([{"gst": "read", "sales": "read"}, {"gst": "certify"}, {"sales": "comment", "tds": "prepare"}])
    assert merged == {"gst": "certify", "sales": "comment", "tds": "prepare"} and roles.merge_grant_scopes([]) == {}
    assert roles.grant_allows(merged, "gst", "file") and roles.grant_allows(merged, "tds", "write") and not roles.grant_allows(merged, "tds", "file")
    assert roles.grant_allows(merged, "sales", "read") and not roles.grant_allows(merged, "sales", "write") and not roles.grant_allows(merged, "payroll", "read")
    assert not roles.grant_allows(merged, "gst", "approve") and not roles.grant_allows(merged, "gst", "pay")      # approving and paying stay with the company


def test_practitioner_presets_name_a_service_and_the_auditor_only_reads():
    for key, p in roles.PRESETS.items():
        if p["kind"] == "practitioner":
            assert p["service"] in roles.SERVICES and roles.clean_grant_scopes(p["scopes"], p["service"]), key
    assert roles.PRESETS["statutory_auditor"]["service"] in roles.AUDIT_SERVICES


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
    db.execute("INSERT INTO organizations (id, name, kind) VALUES (%s, 'Engaging Org', 'business_group'), (%s, 'Rao and Co', 'ca_firm'), (%s, 'Other Firm', 'ca_firm')", (ORG, FIRM, OTHER_FIRM))
    db.execute("INSERT INTO entities (id, organization_id, legal_name, constitution, track) VALUES (%s, %s, 'Engaging Co Pvt Ltd', 'private_limited', 'business')", (EN, ORG))
    db.execute("""INSERT INTO users (id, email, full_name) VALUES (%s, %s, 'Eng Owner'), (%s, %s, 'Eng Maker'), (%s, %s, 'Ravi Rao'), (%s, %s, 'Sita Staff')""",
               (U_OWNER, OWNER, U_MAKER, MAKER, U_PARTNER, PARTNER, U_STAFF, STAFF))
    db.execute("INSERT INTO memberships (entity_id, user_id, role, accepted_at) VALUES (%s, %s, 'owner', now()), (%s, %s, 'maker', now())", (EN, U_OWNER, EN, U_MAKER))
    db.execute("INSERT INTO org_members VALUES (%s, %s, 'partner'), (%s, %s, 'staff')", (FIRM, U_PARTNER, FIRM, U_STAFF))
    yield


def sees(client, h) -> dict | None:
    return next((e for e in client.get("/v1/me", headers=h).json()["entities"] if e["id"] == EN), None)


def grants(client, h) -> dict:
    return {g["advisor_name"]: g for g in client.get(url("/advisor-grants"), headers=h).json()["data"]}


def invite(client, h, **body):
    return client.post(url("/advisor-grants"), headers=h, json=body)


def test_only_an_owner_engages_a_practitioner_and_the_engagement_must_make_sense(client):
    o, m = login(client, OWNER), login(client, MAKER)
    ok = {"advisor_name": "Gita GST", "invite_email": GSTMAN, "preset": "gst_consultant"}
    assert invite(client, m, **ok).status_code == 403                                               # a preparer with every module cannot let outsiders in
    for body, field in (({"advisor_name": "X"}, "advisor_kind"), ({"advisor_name": "X", "advisor_kind": "auditor"}, "scopes"), ({"advisor_name": "X", "preset": "accountant"}, "preset"),
                        ({"advisor_name": "X", "preset": "gst_consultant", "scopes": {"settings": "read"}}, "scopes"),
                        ({"advisor_name": "X", "preset": "gst_consultant", "expires_on": str(TODAY)}, "expires_on"),
                        ({"advisor_name": "X", "preset": "statutory_auditor"}, "expires_on"),
                        ({"advisor_name": "X", "advisor_kind": "auditor", "service": "tax_audit", "scopes": {"finance": "prepare"}, "expires_on": NEXT_YEAR}, "scopes")):
        r = invite(client, o, **body)
        assert r.status_code == 400 and r.json()["errors"][0]["field"] == field, r.text
    assert invite(client, o, advisor_name="Maker again", invite_email=MAKER, preset="gst_consultant").status_code == 409      # already on the team


def test_an_invited_practitioner_sees_nothing_until_they_accept(client):
    o = login(client, OWNER)
    r = invite(client, o, advisor_name="Gita GST", invite_email=GSTMAN, preset="gst_consultant", membership_no="123456")
    assert r.status_code == 201, r.text
    g = r.json()
    S["gst"] = g["id"]
    assert (g["status"], g["party"], g["service"], g["preset"], g["advisor_kind"], g["audit"]) == ("invited", "person", "gst", "gst_consultant", "chartered_accountant", False)
    assert g["scopes"]["gst"] == "certify" and g["invited_email"] == GSTMAN
    assert invite(client, o, advisor_name="Gita GST", invite_email=GSTMAN, preset="gst_consultant").status_code == 409        # already invited for this
    h = login(client, GSTMAN)
    assert sees(client, h) is None and client.get(url("/sales-documents"), headers=h).status_code == 404
    mine = client.get("/v1/me/engagements", headers=h).json()
    assert mine["firms"] == [] and len(mine["invitations"]) == 1
    inv = mine["invitations"][0]
    assert (inv["id"], inv["entity_name"], inv["service_label"], inv["invited_by_name"], inv["audit"]) == (S["gst"], "Engaging Co Pvt Ltd", "GST", "Eng Owner", False)
    assert client.get("/v1/me/engagements", headers=login(client, MAKER)).json()["invitations"] == []
    assert client.post(f"/v1/me/engagements/{S['gst']}/accept", headers=login(client, MAKER)).status_code == 404              # not theirs to answer


def test_accepting_gives_the_scopes_of_the_engagement_and_nothing_more(client, db):
    h, o = login(client, GSTMAN), login(client, OWNER)
    r = client.post(f"/v1/me/engagements/{S['gst']}/accept", headers=h)
    assert r.status_code == 200, r.text
    assert (r.json()["status"], r.json()["accepted_for"], r.json()["work_taken_on"]) == ("active", None, 1)       # they now prepare GST
    assert client.post(f"/v1/me/engagements/{S['gst']}/accept", headers=h).status_code == 404                     # answered once
    e = sees(client, h)
    assert (e["role"], e["home"], e["title"]) == ("advisor", "practitioner", "GST") and e["modules"] == ["gst", "parties", "purchases", "sales"]
    assert client.get(url("/sales-documents"), headers=h).status_code == 200                                      # read
    for path in ("/payroll-runs", "/finance/summary", "/members", "/approvals", "/hr/people"):
        assert client.get(url(path), headers=h).status_code == 403, path
    assert client.post(url("/parties"), headers=h, json={"legal_name": "Sneaky Vendor"}).status_code == 403       # parties is read only for them
    assert invite(client, h, advisor_name="Friend", preset="gst_consultant").status_code == 403                   # a practitioner cannot let others in
    g = grants(client, o)["Gita GST"]
    assert g["status"] == "active" and g["accepted_at"]
    assert db.execute("SELECT count(*) AS n FROM audit_log WHERE entity_id = %s AND action = 'engagement.accepted'", (EN,)).fetchone()["n"] == 1


def test_a_practitioner_can_be_named_to_prepare_work_but_never_to_approve(client, db):
    o, h = login(client, OWNER), login(client, GSTMAN)
    m = client.get(url("/responsibilities"), headers=o).json()
    gita = next(p for p in m["people"] if p["name"] == "Gita GST")
    assert gita["practitioner"] is True and gita["title"] == "GST" and gita["approves"] == []
    assert [(s["work_kind"], s["step"], s["name"]) for s in m["data"]] == [("gst", "prepare", "Gita GST")]
    bad = client.put(url("/responsibilities"), headers=o, json={"data": [{"work_kind": "gst", "step": "approve", "user_id": gita["user_id"]}]})
    assert bad.status_code == 400 and "cannot approve" in bad.json()["errors"][0]["message"]
    rule = db.execute("SELECT id FROM compliance_rules WHERE code = 'GST.GSTR1.MONTHLY' ORDER BY version DESC LIMIT 1").fetchone()["id"]
    ob = db.execute("""INSERT INTO obligations (entity_id, rule_id, fy, period_start, period_end, period_label, due_date, reason_snapshot)
                       VALUES (%s, %s, '2026-27', %s, %s, 'Oct 2026', %s, %s) RETURNING owner_user_id::text AS o""",
                    (EN, rule, TODAY - dt.timedelta(days=40), TODAY - dt.timedelta(days=10), TODAY + dt.timedelta(days=6), json.dumps({}))).fetchone()
    assert ob["o"] == gita["user_id"]
    q = client.get("/v1/me/work", headers=h).json()
    assert [(i["kind"], i["entity_name"]) for i in q["data"]] == [("filing", "Engaging Co Pvt Ltd")]
    t = client.post(url("/tasks"), headers=o, json={"title": "Send the October sales register", "assignee_id": gita["user_id"]})
    assert t.status_code == 201 and t.json()["assignee_name"] == "Gita GST"                                       # a task can be given to them too


def test_declining_leaves_no_access_and_is_on_the_record(client, db):
    o = login(client, OWNER)
    r = invite(client, o, advisor_name="Chetan CS", invite_email=CS, preset="company_secretary")
    assert r.status_code == 201
    h = login(client, CS)
    assert client.post(f"/v1/me/engagements/{r.json()['id']}/decline", headers=h).json()["status"] == "declined"
    assert sees(client, h) is None and client.get("/v1/me/engagements", headers=h).json()["invitations"] == []
    assert grants(client, o)["Chetan CS"]["status"] == "declined"
    assert db.execute("SELECT count(*) AS n FROM audit_log WHERE entity_id = %s AND action = 'engagement.declined'", (EN,)).fetchone()["n"] == 1


def test_a_partner_accepts_for_the_firm_and_staff_see_only_the_clients_they_are_given(client, db):
    o, pt, st = login(client, OWNER), login(client, PARTNER), login(client, STAFF)
    r = invite(client, o, advisor_name="Rao and Co", invite_email=PARTNER, preset="chartered_accountant", period_fy="2026-27")
    assert r.status_code == 201, r.text
    S["books"] = r.json()["id"]
    mine = client.get("/v1/me/engagements", headers=pt).json()
    assert [f["name"] for f in mine["firms"]] == ["Rao and Co"] and mine["invitations"][0]["service_label"] == "Bookkeeping and accounts"
    assert client.post(f"/v1/me/engagements/{S['books']}/accept", headers=pt, json={"organization_id": OTHER_FIRM}).status_code == 400      # not their firm
    a = client.post(f"/v1/me/engagements/{S['books']}/accept", headers=pt, json={"organization_id": FIRM})
    assert a.status_code == 200 and (a.json()["accepted_for"], a.json()["work_taken_on"]) == (FIRM, 0)            # a firm routes its own staff
    g = grants(client, o)["Rao and Co"]
    assert (g["party"], g["status"], g["advisor_org_id"], g["practitioner_user_id"]) == ("firm", "active", FIRM, None)
    assert sees(client, pt)["role"] == "advisor" and sees(client, pt)["title"] == "Bookkeeping and accounts"
    assert client.get(url("/finance/summary"), headers=pt).status_code == 200
    assert sees(client, st) is None and client.get(url("/finance/summary"), headers=st).status_code == 404        # staff, not assigned to this client
    cs = client.get(f"/v1/organizations/{FIRM}/client-staff", headers=pt).json()
    assert [(c["legal_name"], c["services"], c["staff"]) for c in cs["clients"]] == [("Engaging Co Pvt Ltd", ["Bookkeeping and accounts"], [])]
    assert {p["name"]: p["sees_all"] for p in cs["people"]} == {"Ravi Rao": True, "Sita Staff": False}
    put = lambda h, ids: client.put(f"/v1/organizations/{FIRM}/client-staff/{EN}", headers=h, json={"user_ids": ids})
    assert put(st, [U_STAFF]).status_code == 403 and put(o, [U_STAFF]).status_code == 404                         # staff cannot assign; the client is not in the firm at all
    assert put(pt, [U_PARTNER]).status_code == 400                                                                # partners already see every client
    assert client.put(f"/v1/organizations/{FIRM}/client-staff/{uuid.uuid4()}", headers=pt, json={"user_ids": []}).status_code == 404
    assert put(pt, [U_STAFF]).json()["clients"][0]["staff"] == [U_STAFF]
    assert sees(client, st)["role"] == "advisor" and client.get(url("/finance/summary"), headers=st).status_code == 200
    assert put(pt, []).json()["clients"][0]["staff"] == []
    assert sees(client, st) is None and client.get(url("/finance/summary"), headers=st).status_code == 404        # taken off: gone at once


def test_the_statutory_auditor_cannot_also_keep_the_books(client, db):
    o, pt = login(client, OWNER), login(client, PARTNER)
    audit = {"preset": "statutory_auditor", "period_fy": "2026-27", "expires_on": NEXT_YEAR}
    r = invite(client, o, advisor_name="Rao and Co (audit)", invite_email=PARTNER, **audit)
    assert r.status_code == 201 and r.json()["audit"] is True, r.text                                            # invited as a person: no clash yet
    clash = client.post(f"/v1/me/engagements/{r.json()['id']}/accept", headers=pt, json={"organization_id": FIRM})
    assert clash.status_code == 409 and clash.json()["code"] == "independence_conflict", clash.text              # the firm keeps this company's books
    assert grants(client, o)["Rao and Co (audit)"]["status"] == "invited"
    assert client.post(f"/v1/me/engagements/{r.json()['id']}/decline", headers=pt).status_code == 200
    a = invite(client, o, advisor_name="Asha Auditor", invite_email=AUDITOR, **audit)
    assert a.status_code == 201
    S["audit"] = a.json()["id"]
    h = login(client, AUDITOR)
    assert client.post(f"/v1/me/engagements/{S['audit']}/accept", headers=h).json()["work_taken_on"] == 0
    again = invite(client, o, advisor_name="Asha Auditor", invite_email=AUDITOR, preset="chartered_accountant", period_fy="2026-27")
    assert again.status_code == 409 and again.json()["code"] == "independence_conflict", again.text              # and the other way round
    other_year = invite(client, o, advisor_name="Asha Auditor (books 25-26)", invite_email=AUDITOR, preset="chartered_accountant", period_fy="2025-26")
    assert other_year.status_code == 201                                                                          # a different year is a different engagement


def test_an_auditor_reads_and_never_writes(client, db):
    h, o = login(client, AUDITOR), login(client, OWNER)
    e = sees(client, h)
    assert e["title"] == "Statutory audit" and set(e["scopes"].values()) <= {"read", "comment"}
    assert client.get(url("/finance/summary"), headers=h).status_code == 200 and client.get(url("/payroll-runs"), headers=h).status_code == 200
    assert client.post(url("/accounts"), headers=h, json={"code": "9990", "name": "Auditor's own account", "type": "expense"}).status_code == 403
    assert "Asha Auditor" not in [p["name"] for p in client.get(url("/responsibilities"), headers=o).json()["people"]]      # an auditor is never named to prepare
    with pytest.raises(psycopg.errors.CheckViolation) as err:                                                    # the database refuses it whatever the API checked
        db.execute("UPDATE advisor_grants SET scopes = '{\"finance\": \"prepare\"}' WHERE id = %s", (S["audit"],))
    assert err.value.diag.constraint_name == "audit_engagement_read_only"
    with pytest.raises(psycopg.errors.CheckViolation):
        db.execute("UPDATE advisor_grants SET expires_on = NULL WHERE id = %s", (S["audit"],))


def test_ending_an_engagement_stops_access_and_gives_the_work_back(client, db):
    o, m, h = login(client, OWNER), login(client, MAKER), login(client, GSTMAN)
    assert client.delete(url(f"/advisor-grants/{S['gst']}"), headers=m).status_code == 403
    assert client.delete(url(f"/advisor-grants/{S['gst']}"), headers=o).status_code == 204
    assert client.delete(url(f"/advisor-grants/{S['gst']}"), headers=o).status_code == 404
    assert sees(client, h) is None and client.get(url("/sales-documents"), headers=h).status_code == 404
    assert client.get(url("/responsibilities"), headers=o).json()["data"] == []
    assert grants(client, o)["Gita GST"]["status"] == "revoked"
    db.execute("UPDATE advisor_grants SET expires_on = %s WHERE id = %s", (TODAY - dt.timedelta(days=1), S["books"]))      # an engagement that has run out
    assert sees(client, login(client, PARTNER)) is None and grants(client, o)["Rao and Co"]["status"] == "expired"
    assert db.execute("SELECT app.audit_verify() AS v").fetchone()["v"] is None
    kinds = {r["type"] for r in db.execute("SELECT DISTINCT type FROM event_outbox WHERE entity_id = %s", (EN,)).fetchall()}
    assert {"engagement.invited", "engagement.accepted", "engagement.declined"} <= kinds
