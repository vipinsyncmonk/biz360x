"""Adapter registry, sandboxes, secret store and small pure helpers of the portal layer."""
import datetime as dt
import tempfile

import pytest

from app import adapters as A
from app import routes_portals as P

UTC = dt.timezone.utc


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    monkeypatch.delenv("BIZ_ADAPTERS", raising=False)
    monkeypatch.setenv("BIZ_ENV", "dev")
    A.reset()
    yield
    A.reset()


# ------------------------------------------------------------------ registry
def test_sandbox_is_the_default_in_development():
    assert isinstance(A.get("gsp"), A.SandboxGsp) and A.get("gsp") is A.get("gsp")           # one instance per family
    assert set(A.status()) == set(A.IMPLEMENTATIONS) and A.status()["irp"] == "SandboxIrp"


def test_a_vendor_adapter_is_selected_by_name():
    class Acme(A.SandboxGsp):
        pass
    A.register("gsp", "acme", Acme)
    import os
    os.environ["BIZ_ADAPTERS"] = "gsp=acme,irp=sandbox"
    try:
        assert isinstance(A.get("gsp"), Acme) and isinstance(A.get("irp"), A.SandboxIrp)
    finally:
        del os.environ["BIZ_ADAPTERS"]
        del A.IMPLEMENTATIONS["gsp"]["acme"]


def test_an_unknown_adapter_name_is_a_startup_error(monkeypatch):
    monkeypatch.setenv("BIZ_ADAPTERS", "gsp=nonesuch")
    with pytest.raises(RuntimeError, match="nonesuch"):
        A.get("gsp")


def test_the_sandbox_cannot_run_in_production(monkeypatch):
    monkeypatch.setenv("BIZ_ENV", "prod")
    with pytest.raises(RuntimeError, match="sandbox"):
        A.get("irp")


def test_use_and_proxy():
    fake = A.SandboxEway()
    A.use("eway", fake)
    proxy = A.proxy("eway")
    assert proxy.generate({"seller_gstin": "G", "doc_no": "1", "to_pincode": "560001", "distance_km": 10})["ewb_no"] and A.get("eway") is fake
    other = A.SandboxEway()
    A.use("eway", other)
    assert proxy.__getattr__("update_vehicle") == other.update_vehicle                       # always forwards to the current adapter


# ------------------------------------------------------------------ sandboxes behave like portals
def test_irp_is_deterministic_and_can_refuse():
    irp = A.SandboxIrp()
    doc = {"seller_gstin": "29AAA", "fy": "2026-27", "doc_type": "tax_invoice", "number": "N/1"}
    assert irp.generate_irn(doc) == irp.generate_irn(doc) and len(irp.generate_irn(doc)["irn"]) == 64
    with pytest.raises(A.PortalRejected) as e:
        irp.generate_irn({**doc, "number": "REJECT/1"})
    assert e.value.code == "2150" and irp.cancel_irn("x", "duplicate")["cancelled"] is True


def test_gsp_filing_needs_an_otp_and_checks_it():
    gsp = A.SandboxGsp()
    with pytest.raises(A.OtpRequired) as e:
        gsp.file_return("29AAA", "GSTR1", "2026-10", {})
    assert e.value.session_ref and e.value.channel == "mobile"
    with pytest.raises(A.PortalRejected, match="OTP"):
        gsp.file_return("29AAA", "GSTR1", "2026-10", {}, otp="000000")
    arn = gsp.file_return("29AAA", "GSTR1", "2026-10", {}, otp="123456")["arn"]
    assert arn.startswith("AA") and arn == gsp.file_return("29AAA", "GSTR1", "2026-10", {}, signing="dsc")["arn"]      # DSC signing needs no OTP


def test_eway_validity_is_a_day_per_200_km():
    eway = A.SandboxEway()
    now = dt.datetime.now(UTC)
    days = lambda km: (dt.datetime.fromisoformat(eway.generate({"seller_gstin": "G", "doc_no": "d", "to_pincode": "560001", "distance_km": km})["valid_until"]) - now).total_seconds() / 86400
    assert round(days(150)) == 1 and round(days(200)) == 1 and round(days(201)) == 2 and round(days(1000)) == 5
    with pytest.raises(A.PortalRejected):
        eway.generate({"seller_gstin": "G", "doc_no": "d", "to_pincode": "560001", "distance_km": 5000})


def test_mca_resolves_a_cin_gstin_or_pan():
    mca = A.SandboxMca()
    assert mca.lookup("U26510KA2019PTC123456", "cin")["legal_name"] == "Nimbus Instruments Private Limited"
    assert mca.lookup("33AAHCN1234Q1Z4", "gstin")["legal_name"].startswith("Nimbus") and mca.lookup("AAHCN1234Q", "pan")["constitution"] == "private_limited"
    assert mca.lookup("U12345KA2000PTC000001", "cin") is None and mca.lookup("29ZZZZZ9999Z1Z9", "gstin") is None
    with pytest.raises(A.PortalUnavailable):
        mca.lookup("U99999KA2020PTC000000", "cin")


def test_sessions_open_with_an_otp_and_expire_per_portal():
    s = A.SandboxSessions()
    with pytest.raises(A.OtpRequired) as e:
        s.start("gstn", "ref")
    with pytest.raises(A.PortalRejected):
        s.complete("gstn", e.value.session_ref, "654321")
    now = dt.datetime.now(UTC)
    assert dt.timedelta(hours=5) < s.complete("gstn", e.value.session_ref, "123456") - now <= dt.timedelta(hours=6, seconds=5)
    assert s.complete("mca", "x", "123456") - now > dt.timedelta(hours=23)


def test_aa_filters_lines_by_date():
    aa = A.SandboxAa()
    aa.data = {("H", "4412"): [{"txn_date": "2026-10-01", "x": 1}, {"txn_date": "2026-11-01", "x": 2}]}
    assert [l["x"] for l in aa.fetch("H", "4412", dt.date(2026, 10, 15), dt.date(2026, 11, 30))] == [2] and aa.consent_url("H").endswith("/H")


def test_traces_and_labour_sandboxes():
    t = A.SandboxTraces()
    t.entries = {("PAN", "2026-27"): [{"section": "194Q", "amount": 5}]}
    assert t.fetch_26as("PAN", "2026-27") == [{"section": "194Q", "amount": 5}] and t.fetch_26as("PAN", "2025-26") == []
    assert t.upload_return("TKN", "26Q")["ack"].startswith("TRACES") and t.pay_challan({"challan_id": "c"})["bsr_code"]
    with pytest.raises(A.OtpRequired):
        A.SandboxLabour().upload_ecr({"establishment": "E", "period": "2026-11"})
    assert A.SandboxLabour().upload_ecr({"establishment": "E", "period": "2026-11"}, otp="123456")["trrn"].startswith("TRRN")


# ------------------------------------------------------------------ secrets and signatures
def test_secret_store_roundtrip_and_ciphertext():
    store = A.LocalSecretStore(tempfile.mkdtemp())
    store.put("portal/e/gstn/x", {"username": "ananya", "password": "hunter2-not-in-plaintext"})
    assert store.get("portal/e/gstn/x") == {"username": "ananya", "password": "hunter2-not-in-plaintext"}
    raw = store._path("portal/e/gstn/x").read_bytes()
    assert b"hunter2" not in raw and b"ananya" not in raw
    store.delete("portal/e/gstn/x")
    assert store.get("portal/e/gstn/x") is None
    store.delete("portal/e/gstn/x")                                                          # deleting twice is fine


def test_webhook_signature_is_hmac_sha256():
    sig = A.webhook_signature("s3cret", b'{"a":1}')
    assert len(sig) == 64 and sig == A.webhook_signature("s3cret", b'{"a":1}') and sig != A.webhook_signature("other", b'{"a":1}') and sig != A.webhook_signature("s3cret", b'{"a":2}')


# ------------------------------------------------------------------ small helpers
def conn(status="connected", exp=None):
    return {"status": status, "session_expires_at": exp}


def test_effective_connection_status():
    now = dt.datetime(2026, 11, 7, 12, 0, tzinfo=UTC)
    assert P.effective_status(conn(exp=now + dt.timedelta(days=3)), now) == "connected"
    assert P.effective_status(conn(exp=now + dt.timedelta(hours=23)), now) == "expiring"
    assert P.effective_status(conn(exp=now - dt.timedelta(seconds=1)), now) == "expired"
    assert P.effective_status(conn(exp=None), now) == "connected"
    for s in ("revoked", "not_connected", "error"):
        assert P.effective_status(conn(s, now - dt.timedelta(days=1)), now) == s


@pytest.mark.parametrize("v,ok", [("KA01AB1234", True), ("MH12A1234", True), ("DL1CAB0001", True), ("ka01ab1234", False), ("KA-01-AB-1234", False), ("1234", False), ("KA01AB12345", False)])
def test_vehicle_numbers(v, ok):
    assert bool(P.VEHICLE.match(v)) is ok


@pytest.mark.parametrize("ident,kind", [("U26510KA2019PTC123456", "cin"), ("AAA-1234", "llpin"), ("29AAHCN1234Q1Z8", "gstin"), ("AAHCN1234Q", "pan")])
def test_identifier_kinds(ident, kind):
    assert [k for k, rx in P.KINDS if rx.match(ident)] == [kind]
    assert [k for k, rx in P.KINDS if rx.match("hello")] == []
