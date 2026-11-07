"""Legal and vault pure logic: contract alerts, template rendering, term extraction, signed storage tokens."""
import datetime as dt
import os
import tempfile

import jwt
import pytest

from app import routes_legal as L, storage

d = dt.date
TODAY = d(2026, 11, 7)


def contract(**kw):
    base = {"status": "active", "kind": "customer", "auto_renews": True, "end_date": d(2026, 12, 31), "notice_days": 60, "notice_deadline": d(2026, 11, 1),
            "renewal_months": 12, "payment_terms_days": 30, "signed_document_id": "doc", "stamp_duty_paid": None}
    return {**base, **kw}


def codes(c, cls=None, today=TODAY):
    return {a["code"]: a for a in L.contract_alerts(c, cls, today)}


# ------------------------------------------------------------------ alerts
def test_notice_window_closing_warns_with_the_renewal_term():
    a = codes(contract(notice_deadline=d(2026, 11, 20)))["notice_window"]
    assert a["level"] == "warn" and "13 day" in a["title"] and "12 months" in a["detail"] and "20 Nov 2026" in a["detail"]


def test_notice_window_boundaries():
    assert "notice_window" in codes(contract(notice_deadline=TODAY)) and "notice_window" in codes(contract(notice_deadline=TODAY + dt.timedelta(days=30)))
    assert "notice_window" not in codes(contract(notice_deadline=TODAY + dt.timedelta(days=31)))
    passed = codes(contract(notice_deadline=d(2026, 10, 1)))
    assert "notice_window" not in passed and passed["notice_window_passed"]["level"] == "info"


def test_msme_payment_terms_only_matter_for_a_small_seller_with_a_customer_contract():
    c = contract(payment_terms_days=60)
    assert "msme_terms" in codes(c, "small") and "msme_terms" in codes(c, "micro")
    assert "msme_terms" not in codes(c, "medium") and "msme_terms" not in codes(c, None)
    assert "msme_terms" not in codes(contract(payment_terms_days=45), "small") and "msme_terms" not in codes(contract(payment_terms_days=60, kind="vendor"), "small")


def test_expiry_missing_signed_copy_and_stamp_duty():
    a = codes(contract(auto_renews=False, end_date=d(2027, 1, 15), signed_document_id=None))
    assert a["expiring"]["level"] == "info" and a["no_signed_copy"]["level"] == "warn" and "notice_window" not in a
    assert "expiring" not in codes(contract(auto_renews=False, end_date=d(2027, 6, 1)))
    assert "stamp_duty" in codes(contract(kind="property", auto_renews=False, end_date=None))
    assert "stamp_duty" not in codes(contract(kind="property", stamp_duty_paid=100, auto_renews=False, end_date=None))


def test_inactive_contracts_raise_nothing():
    assert L.contract_alerts(contract(status="expired", signed_document_id=None), "small", TODAY) == []
    assert L.contract_alerts(contract(status="draft"), "small", TODAY) == []


# ------------------------------------------------------------------ templates
def test_render_fills_placeholders_and_reports_the_missing():
    text, missing = L.render_template("Pay {{ amount }} to {{who}}. {{amount}} again.", {"amount": "10", "who": ""})
    assert text == "Pay 10 to . 10 again." and missing == ["who"]
    assert L.render_template("nothing here", {})[1] == []


def test_every_library_template_renders_with_its_own_fields():
    for name, body in L.TEMPLATE_BODIES.items():
        names = set(L.PLACEHOLDER.findall(body))
        values = {n: "x" for n in names}
        text, missing = L.render_template(body, values)
        assert missing == [] and "{{" not in text and text.startswith("DRAFT - NOT REVIEWED BY COUNSEL"), name


def test_supply_agreement_carries_the_msme_clause():
    assert "45 days from the day of acceptance" in L.TEMPLATE_BODIES["Supply agreement"]


# ------------------------------------------------------------------ term extraction
SAMPLE = """Payment shall be made within 60 days of receipt of invoice. This agreement renews automatically for 12 months unless either party gives 90 days' written notice.
This agreement is governed by the laws of India. The liability of the Supplier is capped at the fees paid."""


def test_extract_terms_from_plain_text():
    got = {t["term_key"]: t for t in L.extract_terms(SAMPLE)}
    assert set(got) == {"payment", "notice", "renewal", "governing_law", "liability_cap"}
    assert got["payment"]["value_json"] == {"match": "60"} and got["notice"]["value_json"] == {"match": "90"} and got["renewal"]["value_json"] == {"match": "12"}
    assert got["governing_law"]["value_json"] == {"match": "India"}
    assert L.extract_terms("Nothing relevant here.") == []


# ------------------------------------------------------------------ storage
@pytest.fixture(autouse=True)
def _store(monkeypatch):
    monkeypatch.setenv("BIZ_STORAGE_DIR", tempfile.mkdtemp())


def test_tokens_are_scoped_to_one_operation_and_expire():
    t = storage.sign("put", "k", 60, doc="1")
    assert storage.verify(t, "put")["key"] == "k"
    with pytest.raises(jwt.InvalidTokenError):
        storage.verify(t, "get")
    with pytest.raises(jwt.ExpiredSignatureError):
        storage.verify(storage.sign("get", "k", -5), "get")
    with pytest.raises(jwt.PyJWTError):
        storage.verify(t[:-3] + "abc", "put")


def test_put_get_roundtrip_and_path_traversal_is_refused():
    key = storage.storage_key("ent", storage.sha256_of(b"hello"))
    storage.put(key, b"hello")
    assert storage.get(key) == b"hello" and storage.get(key + "x") is None
    with pytest.raises(ValueError):
        storage.put("../escape", b"x")


def test_text_layer_only_for_text_types():
    assert storage.text_of(b"hi\x00there", "text/plain; charset=utf-8") == "hithere"
    assert storage.text_of(b"%PDF", "application/pdf") is None
    assert storage.text_of(b'{"a":1}', "application/json") == '{"a":1}'


def test_upload_ticket_names_the_expected_bytes():
    t = storage.upload_ticket("k", "doc-id", "ab" * 32, 5, "text/plain")
    claims = storage.verify(t["upload_url"].rsplit("/", 1)[1], "put")
    assert (claims["size"], claims["sha256"], claims["doc"]) == (5, "ab" * 32, "doc-id") and t["upload_method"] == "PUT"
    assert os.environ["BIZ_STORAGE_DIR"]
