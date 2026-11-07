"""Compliance engine against PostgreSQL: obligations, reasons, idempotence, triggers, worker, endpoints.

Uses its own entity (EN) so it never disturbs the demo tenant that test_flows relies on.
"""
import datetime as dt
import os
import uuid
from decimal import Decimal

import psycopg
import pytest
from psycopg.rows import dict_row

from app import engine

DB = os.environ["DATABASE_URL"]
ORG = "00000000-0000-0000-0000-0000000000a1"
EN = "00000000-0000-0000-0000-0000000000f1"
OWNER = "00000000-0000-0000-0000-0000000000f2"
VIEWER = "00000000-0000-0000-0000-0000000000f3"
GSTIN_KA, GSTIN_MH = "29AAAPL1234C1Z5", "27AAAPL1234C1ZX"
AS_OF = dt.date(2026, 11, 6)


@pytest.fixture(scope="module")
def db():
    with psycopg.connect(DB, autocommit=True, row_factory=dict_row) as c:
        yield c


@pytest.fixture(scope="module", autouse=True)
def entity(db):
    db.execute("""INSERT INTO entities (id, organization_id, legal_name, constitution, track)
                  VALUES (%s, %s, 'Engine Test Pvt Ltd', 'private_limited', 'business')""", (EN, ORG))
    db.execute("""INSERT INTO entity_registrations (entity_id, kind, number, state_code, gst_filing_frequency) VALUES
                  (%s, 'gstin', %s, '29', 'monthly'), (%s, 'gstin', %s, '27', 'monthly'),
                  (%s, 'tan', 'PNEN54321B', '29', NULL), (%s, 'epf', 'KNBLR0012345000', '29', NULL),
                  (%s, 'esic', '31000123450000999', '29', NULL)""",
               (EN, GSTIN_KA, EN, GSTIN_MH, EN, EN, EN))
    db.execute("INSERT INTO users (id, email, full_name) VALUES (%s, 'engine-owner@example.in', 'Engine Owner'), (%s, 'engine-viewer@example.in', 'Engine Viewer')",
               (OWNER, VIEWER))
    db.execute("INSERT INTO memberships (entity_id, user_id, role) VALUES (%s, %s, 'owner'), (%s, %s, 'viewer')", (EN, OWNER, EN, VIEWER))
    for k, v in {"headcount": 25, "aato": 30_000_000, "estimated_tax": 50_000}.items():
        db.execute("INSERT INTO entity_facts (entity_id, fact_key, value_num, as_of, source) VALUES (%s, %s, %s, '2026-03-31', 'books')", (EN, k, v))
    db.execute("INSERT INTO entity_facts (entity_id, fact_key, value_bool, as_of, source) VALUES (%s, 'has_mse_dues_over_45_days', true, '2026-10-01', 'books')", (EN,))
    db.execute("UPDATE jobs SET status = 'cancelled' WHERE kind = 'compliance.run_engine' AND status = 'queued'")   # the tests drive runs themselves
    yield
    db.execute("UPDATE jobs SET status = 'cancelled' WHERE kind = 'compliance.run_engine' AND status = 'queued'")


def run(db, trigger="manual", as_of=AS_OF):
    with db.transaction():
        return engine.run_engine(db.cursor(), EN, trigger, as_of)


def obligations(db, code=None, where=""):
    q = """SELECT o.*, o.status::text AS st, r.code, r.version, er.number AS reg
             FROM obligations o JOIN compliance_rules r ON r.id = o.rule_id LEFT JOIN entity_registrations er ON er.id = o.registration_id
            WHERE o.entity_id = %s """ + ("AND r.code = %s " if code else "") + where + " ORDER BY o.period_start, er.number"
    return db.execute(q, (EN, code) if code else (EN,)).fetchall()


# ------------------------------------------------------------------ first run
def test_first_run_creates_obligations_with_reasons(db):
    r = run(db)
    assert r["rules_evaluated"] >= 10 and r["obligations_created"] > 20 and r["finished_at"] is not None
    gstr1 = obligations(db, "GST.GSTR1.MONTHLY")
    oct_ = [o for o in gstr1 if o["period_label"] == "Oct 2026"]
    assert {o["reg"] for o in oct_} == {GSTIN_KA, GSTIN_MH}                       # one per GSTIN
    o = oct_[0]
    assert o["due_date"] == dt.date(2026, 11, 11) and o["st"] == "upcoming" and o["fy"] == "2026-27"
    snap = o["reason_snapshot"]
    assert snap["rule"]["code"] == "GST.GSTR1.MONTHLY" and snap["rule"]["statute_ref"].startswith("CGST Act")
    assert snap["applicability"][0]["fact"] == "gst_frequency" and snap["applicability"][0]["actual"] == "monthly"
    assert snap["registration"]["number"] == o["reg"]


def test_window_and_status_by_date(db):
    gstr1 = {o["period_label"]: o for o in obligations(db, "GST.GSTR1.MONTHLY") if o["reg"] == GSTIN_KA}
    assert gstr1["Sep 2026"]["st"] == "overdue" and gstr1["Sep 2026"]["due_date"] == dt.date(2026, 10, 11)
    assert "Aug 2026" not in gstr1                       # due 11 Sep: older than the 45-day lookback, so not invented now
    assert "Mar 2027" in gstr1 and "Apr 2027" not in gstr1


def test_late_fee_accrues_on_overdue_only(db):
    sep = next(o for o in obligations(db, "GST.GSTR1.MONTHLY") if o["period_label"] == "Sep 2026" and o["reg"] == GSTIN_KA)
    assert sep["late_fee_accrued"] == Decimal("1300.00")             # 26 days x 50, below the 5,000 cap for 3 Cr turnover
    assert all(o["late_fee_accrued"] == 0 for o in obligations(db, where="AND o.status = 'upcoming'"))


def test_rule_specific_schedules(db):
    tds = {o["period_label"]: o["due_date"] for o in obligations(db, "TDS.DEPOSIT.MONTHLY")}
    assert tds["Oct 2026"] == dt.date(2026, 11, 7) and tds["Mar 2027"] == dt.date(2027, 4, 30)
    adv = {o["period_label"]: o["due_date"] for o in obligations(db, "IT.ADVANCE_TAX")}
    assert adv["Q3 FY26-27"] == dt.date(2026, 12, 15)
    aoc = obligations(db, "MCA.AOC4")
    assert aoc and aoc[0]["reason_snapshot"].get("provisional")       # no AGM recorded yet, so the date is provisional


def test_facts_decide_what_applies(db):
    codes = {o["code"] for o in obligations(db)}
    assert {"EPF.ECR.MONTHLY", "ESI.MONTHLY", "MCA.MSME1", "IT.ADVANCE_TAX"} <= codes
    assert "GST.EINVOICE" not in codes                    # per-document duty has no schedule
    assert "PT.KA.MONTHLY" not in codes                   # no professional-tax registration


def test_rerun_is_idempotent(db):
    before = len(obligations(db))
    r = run(db)
    assert (r["obligations_created"], r["obligations_changed"]) == (0, 0) and len(obligations(db)) == before


# ------------------------------------------------------------------ change handling
def set_fact(db, key, num):
    db.execute("INSERT INTO entity_facts (entity_id, fact_key, value_num, as_of, source) VALUES (%s, %s, %s, '2026-11-01', 'user')", (EN, key, num))


def test_fact_change_switches_obligations_off_and_on_but_never_touches_filed(db):
    pf = obligations(db, "EPF.ECR.MONTHLY")
    filed = next(o for o in pf if o["period_label"] == "Nov 2026")
    db.execute("UPDATE obligations SET status = 'filed', filed_at = now(), acknowledgement = 'TRRN1' WHERE id = %s", (filed["id"],))
    set_fact(db, "headcount", 12)                                      # under the EPF threshold of 20, still over ESI's 10
    r = run(db)
    assert r["obligations_changed"] > 0
    after = {o["period_label"]: o for o in obligations(db, "EPF.ECR.MONTHLY")}
    assert after["Dec 2026"]["st"] == "not_applicable" and after["Nov 2026"]["st"] == "filed"
    assert all(o["st"] != "not_applicable" for o in obligations(db, "ESI.MONTHLY"))
    ev = db.execute("SELECT note, kind FROM obligation_events WHERE obligation_id = %s AND to_status = 'not_applicable'", (after["Dec 2026"]["id"],)).fetchone()
    assert "headcount >= 20" in ev["note"] and "is 12" in ev["note"]
    set_fact(db, "headcount", 40)
    run(db)
    back = {o["period_label"]: o for o in obligations(db, "EPF.ECR.MONTHLY")}
    assert back["Dec 2026"]["st"] == "upcoming" and back["Nov 2026"]["st"] == "filed"
    assert db.execute("SELECT count(*) AS n FROM obligation_events WHERE obligation_id = %s AND to_status = 'upcoming'", (back["Dec 2026"]["id"],)).fetchone()["n"] >= 1


def test_filed_obligation_keeps_its_snapshot_and_dates(db):
    filed = next(o for o in obligations(db, "EPF.ECR.MONTHLY") if o["period_label"] == "Nov 2026")
    assert filed["st"] == "filed" and filed["acknowledgement"] == "TRRN1"


def test_dates_roll_forward_and_overdue_is_detected(db):
    nov = next(o for o in obligations(db, "GST.GSTR1.MONTHLY") if o["period_label"] == "Oct 2026" and o["reg"] == GSTIN_KA)
    assert nov["st"] == "upcoming"
    run(db, "schedule", dt.date(2026, 11, 20))
    row = db.execute("SELECT status::text AS st, late_fee_accrued FROM obligations WHERE id = %s", (nov["id"],)).fetchone()
    assert row["st"] == "overdue" and row["late_fee_accrued"] == Decimal("450.00")      # 9 days x 50
    run(db, "schedule", AS_OF)                                         # going back in time restores it: nothing is sticky
    assert db.execute("SELECT status::text AS st FROM obligations WHERE id = %s", (nov["id"],)).fetchone()["st"] == "upcoming"
    assert db.execute("SELECT late_fee_accrued FROM obligations WHERE id = %s", (nov["id"],)).fetchone()["late_fee_accrued"] == 0


def test_extended_due_date_is_respected(db):
    o = next(o for o in obligations(db, "GST.GSTR1.MONTHLY") if o["period_label"] == "Oct 2026" and o["reg"] == GSTIN_MH)
    db.execute("UPDATE obligations SET extended_due_date = '2026-12-31' WHERE id = %s", (o["id"],))
    run(db, "schedule", dt.date(2026, 11, 20))
    assert db.execute("SELECT status::text AS st FROM obligations WHERE id = %s", (o["id"],)).fetchone()["st"] == "upcoming"
    db.execute("UPDATE obligations SET extended_due_date = NULL WHERE id = %s", (o["id"],))
    run(db)


def test_agm_makes_the_due_date_firm(db):
    db.execute("INSERT INTO meetings (entity_id, kind, scheduled_for, held_on) VALUES (%s, 'agm', '2026-09-15 10:00+05:30', '2026-09-15')", (EN,))
    run(db)
    aoc = next(o for o in obligations(db, "MCA.AOC4") if o["period_label"].endswith("2025–26"))
    mgt = next(o for o in obligations(db, "MCA.MGT7") if o["period_label"].endswith("2025–26"))
    assert aoc["due_date"] == dt.date(2026, 10, 15) and "provisional" not in aoc["reason_snapshot"]
    assert mgt["due_date"] == dt.date(2026, 11, 14)
    assert db.execute("SELECT count(*) AS n FROM obligation_events WHERE obligation_id = %s AND kind = 'due_changed'", (aoc["id"],)).fetchone()["n"] == 1


# ------------------------------------------------------------------ rule versions
def test_new_rule_version_repoints_open_obligations_and_spares_filed(db):
    code = "GST.GSTR1.MONTHLY"
    v1 = db.execute("SELECT id, effective FROM compliance_rules WHERE code = %s AND version = 1", (code,)).fetchone()
    first = next(o for o in obligations(db, code) if o["period_label"] == "Nov 2026" and o["reg"] == GSTIN_KA)
    db.execute("UPDATE obligations SET status = 'filed', filed_at = now(), acknowledgement = 'ARN1' WHERE id = %s", (first["id"],))
    try:
        db.execute("UPDATE compliance_rules SET effective = daterange('2021-06-01', '2026-12-01') WHERE id = %s", (v1["id"],))
        db.execute("""INSERT INTO compliance_rules (code, version, title, form_code, authority_code, statute_ref, frequency, per_registration,
                        applicability, due_rule, penalty_rule, effective, change_note)
                      SELECT code, 2, title, form_code, authority_code, statute_ref, frequency, per_registration, applicability,
                             '{"type":"day_of_following_month","day":12}', penalty_rule, daterange('2026-12-01', NULL), 'due date moves to 12th'
                        FROM compliance_rules WHERE id = %s""", (v1["id"],))
        queued = db.execute("SELECT count(*) AS n FROM jobs WHERE kind = 'compliance.run_engine' AND status = 'queued' AND entity_id = %s", (EN,)).fetchone()["n"]
        assert queued == 1                                              # publishing a rule queues a run (rule_change)
        r = run(db, "rule_change")
        rows = {o["period_label"]: o for o in obligations(db, code) if o["reg"] == GSTIN_KA}
        assert rows["Nov 2026"]["version"] == 1 and rows["Nov 2026"]["due_date"] == dt.date(2026, 12, 11)
        assert rows["Dec 2026"]["version"] == 2 and rows["Dec 2026"]["due_date"] == dt.date(2027, 1, 12)
        assert rows["Oct 2026"]["version"] == 1 and rows["Oct 2026"]["due_date"] == dt.date(2026, 11, 11)       # earlier periods keep their rule
        assert r["obligations_changed"] >= 2
        n = db.execute("SELECT count(*) AS n FROM obligations o JOIN compliance_rules r ON r.id = o.rule_id WHERE o.entity_id = %s AND r.code = %s AND o.period_label = 'Dec 2026' AND o.registration_id IS NOT NULL", (EN, code)).fetchone()["n"]
        assert n == 2                                                   # one per GSTIN, never one per version
        # a filed return stays on the version it was filed under
        assert next(o for o in obligations(db, code) if o["id"] == first["id"])["acknowledgement"] == "ARN1"
    finally:
        db.execute("UPDATE obligations SET rule_id = %s WHERE rule_id IN (SELECT id FROM compliance_rules WHERE code = %s AND version = 2)", (v1["id"], code))
        db.execute("DELETE FROM compliance_rules WHERE code = %s AND version = 2", (code,))
        db.execute("UPDATE compliance_rules SET effective = %s WHERE id = %s", (v1["effective"], v1["id"]))
        db.execute("UPDATE jobs SET status = 'cancelled' WHERE kind = 'compliance.run_engine' AND status = 'queued'")
    run(db)


def test_overlapping_versions_are_rejected_by_the_database(db):
    with pytest.raises(psycopg.errors.ExclusionViolation):
        db.execute("""INSERT INTO compliance_rules (code, version, title, authority_code, statute_ref, frequency, applicability, due_rule, effective)
                      VALUES ('GST.GSTR1.MONTHLY', 9, 't', 'GSTN', 's', 'monthly', '{"all":[]}', '{"type":"per_document"}', daterange('2025-01-01', NULL))""")
    db.execute("UPDATE jobs SET status = 'cancelled' WHERE kind = 'compliance.run_engine' AND status = 'queued'")


# ------------------------------------------------------------------ thresholds
def test_threshold_alert_creates_one_task_and_one_event(db):
    db.execute("INSERT INTO threshold_watches (entity_id, rule_code, fact_key, threshold, alert_at_pct, note) VALUES (%s, 'TAX.AUDIT.44AB', 'turnover', 100000000, 90, 'cash under 5%%')", (EN,))
    set_fact(db, "turnover", 89_000_000)
    run(db)
    assert db.execute("SELECT count(*) AS n FROM tasks WHERE entity_id = %s AND source = 'engine'", (EN,)).fetchone()["n"] == 0
    w = engine.threshold_rows(db.cursor(), EN)[0]
    assert w["pct"] == Decimal("89.0") and not w["alert"] and not w["crossed"]
    set_fact(db, "turnover", 95_000_000)
    run(db)
    run(db)
    tasks = db.execute("SELECT title FROM tasks WHERE entity_id = %s AND source = 'engine'", (EN,)).fetchall()
    assert len(tasks) == 1 and "95.0%" in tasks[0]["title"]
    assert db.execute("SELECT count(*) AS n FROM event_outbox WHERE entity_id = %s AND type = 'threshold.crossed'", (EN,)).fetchone()["n"] == 1
    set_fact(db, "turnover", 120_000_000)
    assert engine.threshold_rows(db.cursor(), EN)[0]["crossed"] is True


# ------------------------------------------------------------------ queue, worker
def test_fact_changes_queue_one_deduplicated_job_and_the_worker_runs_it(db):
    from app import worker
    db.execute("UPDATE jobs SET status = 'cancelled' WHERE kind = 'compliance.run_engine' AND status = 'queued'")
    set_fact(db, "headcount", 41)
    set_fact(db, "headcount", 42)
    jobs = db.execute("SELECT id, payload FROM jobs WHERE kind = 'compliance.run_engine' AND status = 'queued' AND entity_id = %s", (EN,)).fetchall()
    assert len(jobs) == 1 and jobs[0]["payload"] == {"trigger": "fact_change"}
    db.execute("UPDATE jobs SET status = 'cancelled' WHERE kind = 'compliance.run_engine' AND status = 'queued' AND entity_id <> %s", (EN,))
    assert worker.run_once() is True
    j = db.execute("SELECT status, result FROM jobs WHERE id = %s", (jobs[0]["id"],)).fetchone()
    assert j["status"] == "succeeded" and "engine_run_id" in j["result"]
    assert db.execute("SELECT count(*) AS n FROM engine_runs WHERE entity_id = %s AND trigger = 'fact_change'", (EN,)).fetchone()["n"] == 1


def test_engine_facts_do_not_retrigger_the_engine(db):
    db.execute("UPDATE jobs SET status = 'cancelled' WHERE kind = 'compliance.run_engine' AND status = 'queued'")
    db.execute("INSERT INTO entity_facts (entity_id, fact_key, value_num, as_of, source) VALUES (%s, 'derived_x', 1, '2026-11-01', 'engine')", (EN,))
    assert db.execute("SELECT count(*) AS n FROM jobs WHERE kind = 'compliance.run_engine' AND status = 'queued' AND entity_id = %s", (EN,)).fetchone()["n"] == 0


def test_scheduler_queues_entities_without_a_recent_run(db):
    from app import worker
    with db.transaction():
        db.execute("UPDATE engine_runs SET started_at = now() - interval '2 days' WHERE entity_id = %s", (EN,))
        db.execute("UPDATE jobs SET status = 'cancelled' WHERE kind = 'compliance.run_engine' AND status = 'queued'")
        assert worker.schedule_engine_runs(db.cursor()) >= 1
    assert db.execute("SELECT payload FROM jobs WHERE kind = 'compliance.run_engine' AND status = 'queued' AND entity_id = %s", (EN,)).fetchone()["payload"] == {"trigger": "schedule"}
    db.execute("UPDATE jobs SET status = 'cancelled' WHERE kind = 'compliance.run_engine' AND status = 'queued'")


# ------------------------------------------------------------------ API
@pytest.fixture(scope="module")
def client():
    from fastapi.testclient import TestClient
    from app.main import app
    return TestClient(app)


def login(client, email):
    r = client.post("/v1/auth/token", json={"grant_type": "otp", "email": email, "otp": "000000"})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def test_post_engine_run_and_get(client):
    h = login(client, "engine-owner@example.in")
    r = client.post(f"/v1/entities/{EN}/compliance/engine-runs", headers=h)
    assert r.status_code == 201, r.text
    run_ = r.json()
    assert run_["trigger"] == "manual" and run_["rules_evaluated"] >= 10 and run_["finished_at"]
    got = client.get(f"/v1/entities/{EN}/compliance/engine-runs/{run_['id']}", headers=h)
    assert got.status_code == 200 and got.json()["id"] == run_["id"]
    assert client.get(f"/v1/entities/{EN}/compliance/engine-runs/{uuid.uuid4()}", headers=h).json()["code"] == "not_found"


def test_engine_run_permissions_and_visibility(client):
    viewer, stranger = login(client, "engine-viewer@example.in"), login(client, "stranger@example.in")
    assert client.post(f"/v1/entities/{EN}/compliance/engine-runs", headers=viewer).json()["code"] == "permission_denied"
    assert client.get(f"/v1/entities/{EN}/compliance/calendar", headers=viewer).status_code == 200
    assert client.post(f"/v1/entities/{EN}/compliance/engine-runs", headers=stranger).status_code == 404


def test_calendar_groups_by_day(client):
    h = login(client, "engine-owner@example.in")
    today = dt.date.today()
    r = client.get(f"/v1/entities/{EN}/compliance/calendar", headers=h, params={"from": today.isoformat(), "to": (today + dt.timedelta(days=120)).isoformat()})
    assert r.status_code == 200
    days = r.json()
    assert days and days == sorted(days, key=lambda d: d["date"])
    item = days[0]["items"][0]
    assert set(item) == {"obligation_id", "title", "authority", "status", "registration"}
    assert client.get(f"/v1/entities/{EN}/compliance/calendar", headers=h, params={"from": "2026-12-01", "to": "2026-11-01"}).json()["code"] == "validation_failed"


def test_thresholds_endpoint(client):
    h = login(client, "engine-owner@example.in")
    (w,) = client.get(f"/v1/entities/{EN}/compliance/thresholds", headers=h).json()
    assert w["rule_code"] == "TAX.AUDIT.44AB" and w["fact_key"] == "turnover" and w["pct"] == 120.0 and w["note"] == "cash under 5%"


ANSWERS = {"lookup_id": str(uuid.uuid4()), "track": "business", "gstins": [GSTIN_KA, GSTIN_MH], "headcount_band": "20-49",
           "turnover_band": "5-10cr", "buys_from_mse": "yes"}


def test_onboarding_preview_is_a_dry_run(client, db):
    h = login(client, "engine-owner@example.in")
    counts = lambda: (db.execute("SELECT count(*) AS n FROM obligations").fetchone()["n"], db.execute("SELECT count(*) AS n FROM engine_runs").fetchone()["n"],
                      db.execute("SELECT count(*) AS n FROM audit_log").fetchone()["n"])
    before = counts()
    r = client.post("/v1/onboarding/preview", headers=h, json=ANSWERS)
    assert r.status_code == 200, r.text
    p = r.json()
    by = {a["authority"]: a["count"] for a in p["by_authority"]}
    assert p["total"] == sum(by.values()) and by["GSTN"] == 4 and by["EPFO"] == 1 and by["ESIC"] == 1 and by["MCA"] >= 3    # 2 GSTINs x (GSTR-1, 3B)
    assert any(t["what"] == "ECR" for t in p["triggers"])
    assert p["first_due"] and p["first_due"] == sorted(p["first_due"], key=lambda d: d["due_date"])
    assert counts() == before                                            # nothing written


def test_onboarding_preview_responds_to_answers(client):
    h = login(client, "engine-owner@example.in")
    small = client.post("/v1/onboarding/preview", headers=h, json={**ANSWERS, "headcount_band": "1-9", "buys_from_mse": "no"}).json()
    by = {a["authority"]: a["count"] for a in small["by_authority"]}
    assert "EPFO" not in by and "ESIC" not in by
    assert client.post("/v1/onboarding/preview", headers=h, json={**ANSWERS, "headcount_band": "999"}).json()["code"] == "validation_failed"
