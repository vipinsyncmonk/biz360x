"""Company profiles: your own company in the sandbox registry, checked on load and usable by the real onboarding flow."""
import copy
import datetime as dt
import json
import os
import pathlib
import uuid

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.rows import dict_row

from app import adapters, company_profiles as CP

DB = os.environ["DATABASE_URL"]
EXAMPLE = pathlib.Path(__file__).resolve().parents[2] / "docs" / "company-profile.example.json"
TODAY = dt.date(2026, 11, 8)
U_NEW, NEW = "00000000-0000-0000-0000-000000000411", "cp-new@example.in"


def example():
    return json.loads(EXAMPLE.read_text(encoding="utf-8"))


def problems(data):
    with pytest.raises(CP.ProfileError) as e:
        CP.validate(data, TODAY)
    return e.value.problems


def mutate(**changes):
    d = example()
    d["companies"][0].update(changes)
    return d


# ------------------------------------------------------------------ the validator
def test_the_shipped_example_is_valid_and_summarised():
    out = CP.validate(example(), TODAY)
    e = out["U74999DL2018PTC987654"]
    assert e["kind"] == "cin" and e["gstins"][0]["state_code"] == "07" and e["paid_up_capital"] == "2500000.00" and e["pan"] == "AABCE1234F"
    line = CP.summary(out)[0]
    assert "Example Traders Private Limited" in line and "1 GSTIN" in line and "2 directors" in line and "Udyam micro" in line


def test_every_problem_is_reported_in_one_pass():
    d = mutate(cin="X", pan="bad", incorporated_on="2999-01-01", registered_state="Delhi", paid_up_capital="1,00,000")
    p = problems(d)
    assert len(p) >= 5
    text = " ".join(p)
    for needle in ("cin is required", "pan has the wrong shape", "in the future", "two-digit", "paid_up_capital"):
        assert needle in text


def test_gstins_must_match_the_pan_and_each_other():
    d = mutate(gstins=[{"gstin": "07AABCE1234F1Z5"}, {"gstin": "07AABCE1234F1Z5"}])
    assert any("appears twice" in p for p in problems(d))
    d = mutate(gstins=[{"gstin": "07AAKCT1111A1Z5"}])
    assert any("embeds PAN AAKCT1111A" in p for p in problems(d))
    assert any("not a valid GSTIN" in p for p in problems(mutate(gstins=[{"gstin": "07AABCE1234F1Z"}])))


def test_a_gstin_cannot_belong_to_two_companies():
    d = example()
    twin = copy.deepcopy(d["companies"][0])
    twin["cin"] = "U74999DL2019PTC111111"
    d["companies"].append(twin)
    assert any("already listed" in p for p in problems(d))
    twin2 = copy.deepcopy(d["companies"][0])
    d["companies"] = [d["companies"][0], twin2]
    assert any("listed twice" in p for p in problems(d))


def test_an_llp_is_keyed_by_its_llpin():
    d = {"companies": [{"llpin": "AAB-1234", "legal_name": "Pine LLP", "constitution": "llp", "incorporated_on": "2020-01-15", "registered_state": "27"}]}
    out = CP.validate(d, TODAY)
    assert list(out) == ["AAB-1234"] and out["AAB-1234"]["kind"] == "llpin" and out["AAB-1234"]["gstins"] == []
    d["companies"][0]["llpin"] = "1234"
    assert any("llpin is required" in p for p in problems(d))


def test_the_file_shape_and_missing_files_are_reported(tmp_path):
    assert any("at least one company" in p for p in problems({}))
    assert any("at least one company" in p for p in problems({"companies": []}))
    with pytest.raises(CP.ProfileError) as missing:
        CP.load_file(str(tmp_path / "nope.json"))
    assert "does not exist" in missing.value.problems[0]
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    with pytest.raises(CP.ProfileError) as e:
        CP.load_file(str(bad))
    assert "not valid JSON" in e.value.problems[0]


def test_udyam_directors_and_dates():
    p = problems(mutate(udyam={"number": "UDYAM-1", "class": "huge"}, directors=[{"name": "", "din": "12"}], last_agm_on="31-09-2025"))
    text = " ".join(p)
    for needle in ("udyam.number", "udyam.class", "needs a name", "DIN must be eight digits", "last_agm_on must be a date"):
        assert needle in text


# ------------------------------------------------------------------ the sandbox registry
@pytest.fixture
def profiled(monkeypatch):
    monkeypatch.setenv("BIZ_ENV", "dev")
    monkeypatch.delenv("BIZ_ADAPTERS", raising=False)
    monkeypatch.setenv("BIZ_COMPANY_PROFILES", str(EXAMPLE))
    adapters.reset()
    yield adapters.get("mca")
    adapters.reset()


def test_the_profile_is_found_by_cin_gstin_and_pan_beside_the_demo_companies(profiled):
    assert profiled.lookup("U74999DL2018PTC987654", "cin")["legal_name"] == "Example Traders Private Limited"
    assert profiled.lookup("07AABCE1234F1Z5", "gstin")["cin"] == "U74999DL2018PTC987654"
    assert profiled.lookup("AABCE1234F", "pan")["cin"] == "U74999DL2018PTC987654"
    assert profiled.lookup("U26510KA2019PTC123456", "cin")["legal_name"] == "Nimbus Instruments Private Limited"


def test_without_a_profile_nothing_changes(monkeypatch):
    monkeypatch.setenv("BIZ_ENV", "dev")
    monkeypatch.delenv("BIZ_ADAPTERS", raising=False)
    monkeypatch.delenv("BIZ_COMPANY_PROFILES", raising=False)
    adapters.reset()
    try:
        assert adapters.get("mca").lookup("U74999DL2018PTC987654", "cin") is None
        assert "U74999DL2018PTC987654" not in adapters.SandboxMca.registry                 # the class-level demo registry is never mutated
    finally:
        adapters.reset()


def test_a_broken_profile_file_fails_loudly(monkeypatch, tmp_path):
    f = tmp_path / "p.json"
    f.write_text(json.dumps(mutate(cin="nope")), encoding="utf-8")
    monkeypatch.setenv("BIZ_ENV", "dev")
    monkeypatch.setenv("BIZ_COMPANY_PROFILES", str(f))
    adapters.reset()
    try:
        with pytest.raises(CP.ProfileError):
            adapters.get("mca")
    finally:
        adapters.reset()


def test_production_refuses_the_sandbox_registry_and_so_the_profile(monkeypatch):
    monkeypatch.setenv("BIZ_ENV", "prod")
    monkeypatch.delenv("BIZ_ADAPTERS", raising=False)
    monkeypatch.setenv("BIZ_COMPANY_PROFILES", str(EXAMPLE))
    adapters.reset()
    try:
        with pytest.raises(Exception):
            adapters.get("mca")
    finally:
        adapters.reset()


# ------------------------------------------------------------------ onboarding with a profiled company
@pytest.fixture(scope="module")
def db():
    with psycopg.connect(DB, autocommit=True, row_factory=dict_row) as c:
        yield c


def test_onboarding_creates_the_workspace_for_the_profiled_company(profiled, db):
    from app import worker
    from app.main import app
    client = TestClient(app)
    db.execute("INSERT INTO users (id, email, full_name) VALUES (%s, %s, 'Profile Owner') ON CONFLICT DO NOTHING", (U_NEW, NEW))
    tok = client.post("/v1/auth/token", json={"grant_type": "otp", "email": NEW, "otp": "000000"}).json()["access_token"]
    h = {"Authorization": f"Bearer {tok}"}
    lk = client.post("/v1/onboarding/lookups", headers=h, json={"identifier": "U74999DL2018PTC987654"}).json()
    n = 0
    while worker.run_once():
        n += 1
        assert n < 60
    got = client.get(f"/v1/onboarding/lookups/{lk['id']}", headers=h).json()
    assert got["status"] == "found" and got["entity"]["legal_name"] == "Example Traders Private Limited"
    assert [g["gstin"] for g in got["gstins"]] == ["07AABCE1234F1Z5"] and got["udyam"]["class"] == "micro"
    body = {"lookup_id": lk["id"], "track": "business", "gstins": ["07AABCE1234F1Z5"], "headcount_band": "1-9", "turnover_band": "1.5-5cr",
            "has_foreign_shareholder": False, "buys_from_mse": "no"}
    r = client.post("/v1/onboarding/workspaces", headers={**h, "Idempotency-Key": str(uuid.uuid4())}, json=body)
    assert r.status_code == 201, r.text
    eid = r.json()["entity_id"]
    ent = db.execute("SELECT legal_name, cin, pan, registered_state FROM entities WHERE id = %s", (eid,)).fetchone()
    assert ent["cin"] == "U74999DL2018PTC987654" and ent["pan"] == "AABCE1234F" and ent["registered_state"] == "07" and ent["legal_name"] == "Example Traders Private Limited"
    assert r.json()["obligations_created"] > 0
