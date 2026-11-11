"""Uploading a customs Bill of Entry: the PDF is read, kept in the vault, and the figures come back for a person to confirm before anything is recorded.

The PDF here is drawn with the same columns as the assessed copy ICEGATE issues (labels in a header row, values underneath, blank cells absent),
with made-up parties, so the whole path (text layer, columns, figures) is exercised without a real company's document in the repository."""
import base64
import io
import os
import re
import uuid
from decimal import Decimal as D

import psycopg
import pytest
from psycopg.rows import dict_row
from reportlab.pdfgen import canvas

from app import boe_parse

DB = os.environ["DATABASE_URL"]
ORG = "00000000-0000-0000-0000-000000000b00"
EN = "00000000-0000-0000-0000-000000000b01"
U_OWNER, U_VIEWER, U_OUT = ("00000000-0000-0000-0000-000000000b1%d" % i for i in range(3))
OWNER, VIEWER, OUTSIDER = ("boe-%s@example.in" % n for n in ("owner", "viewer", "outsider"))
GSTIN = "29AABCO5678B1Z3"
S: dict = {}


def boe_pdf(gstin=GSTIN, importer="ORCHID OPTICS PRIVATE LIMITED", supplier="TESTCO ELECTRIC CO., LTD", number="2260434", date="24/05/2025", ooc="", paid="") -> bytes:
    """The rows of an assessed bill of entry, at the columns customs prints them."""
    lines = f"""
                                                                                       Port Code           BE No         BE Date      BE Type
                                                                                       INBLR4            {number}       {date}         H
                                                                                       IEC/Br              AAACE0001A/0          FIRST COPY
                                                                                       GSTIN/TYPE               {gstin}/G
                      INDIAN  CUSTOMS                                                  CB CODE                   AASCS0664BCH001
   PORT : ACC BANGALORE BENGALURU INTERNATIONAL AIRPORT
                 BILL OF ENTRY FOR HOME CONSUMPTION
                                                           PART - I  -  BILL OF ENTRY SUMMARY
      STATUS13.COUNTRY OF ORIGIN                          JAPAN                        14.COUNTRY OF CONSIGNMENT                                  JAPAN
        1.IMPORTER   NAME  & ADDRESS
        {importer}
        367, 7th Cross,
             1.BCD            2.ACD            3.SWS          4.NCCD        5.ADD               6.CVD                      7.IGST             8.G.CESS   18.TOT.ASS VAL
             2134.5              0             213.5                           0                0                    35769                0            196369.92
              9.SG             10.SAED         11.GSIA         12.TTA     13.HEALTH  14.TOTAL DUTY     15.INT    16.PNLTY   17.FINE   19.TOT. AMOUNT
  C. DUTY                                                                                     38118              0            0           0              38118
          1.BOND NO.      2.PORT      3.BOND CD          4.DEBT AMT      5.BG AMT                1.SR NO     2.CHALLAN NO     3.PAID ON     4.AMOUNT(Rs.)
                                                                                                     1          2055012136          {paid}               38118
           1.EVENT          2.DATE          3.TIME            EXCHANGE RATE
        Submission         24-MAY-25          15:43                INR=INR
        Assessment         24-MAY-25          19:30            1 USD=86.4INR
        Examination
        OOC          {ooc}
                                   PART - II - INVOICE & VALUATION DETAILS (Invoice                                   1  1    )
     INVOICE1        FECS25-I-050M
                           1.BUYER'S NAME & ADDRESS                                                              2.SELLER'S NAME & ADDRESS
                           3.SUPPLIER NAME & ADDRESS                                                    4.THIRD PARTY NAME & ADDRESS
        {supplier}
        2-20,NISHINOMIYAHAMA, NISHINOMIYA,
        1.INV VALUE      2.FREIGHT 3.INSURANCE      4.HSS.             5.LOADING6.COMMN           7.PAY TERMS                   8.VALUATION METHOD
            2272.8                                                                                      FC                RULE 4 (TRANSACTION VALUE)
        14.Cur    USD                                                                             9.RELTD    10.SVB CH       11.SVB NO           12.DATE      13LOA
        15.Term   CIF                                                                                 No
""".splitlines()
    return render(lines)


def render(lines: list[str]) -> bytes:
    buf = io.BytesIO()
    cw = 3.6                                                          # Courier at 6 pt: every character is 3.6 pt wide, so a column is a fixed x
    c = canvas.Canvas(buf, pagesize=(700, 14 + 8 * len(lines)))
    c.setFont("Courier", 6)
    for n, ln in enumerate(lines):
        y = 8 * len(lines) - 8 * n
        for m in re.finditer(r"\S+(?: \S+)*", ln):
            c.drawString(m.start() * cw, y, m.group())
    c.save()
    return buf.getvalue()


def plain_pdf(text: str) -> bytes:
    buf = io.BytesIO()
    c = canvas.Canvas(buf)
    c.drawString(72, 700, text)
    c.save()
    return buf.getvalue()


b64 = lambda data: base64.b64encode(data).decode()      # noqa: E731


# ------------------------------------------------------------------ reading
def test_the_figures_are_read_from_the_columns_they_sit_under():
    b = boe_parse.parse_boe(boe_parse.read_pdf_text(boe_pdf()))
    assert (b["boe_number"], str(b["boe_date"]), b["port_code"]) == ("2260434", "2025-05-24", "INBLR4")
    assert b["importer_gstin"] == GSTIN and b["importer_iec"] == "AAACE0001A" and b["importer_name"] == "ORCHID OPTICS PRIVATE LIMITED"
    assert (b["currency"], b["exchange_rate"], b["cif_foreign"], b["incoterm"]) == ("USD", D("86.4"), D("2272.80"), "CIF")
    assert (b["bcd"], b["sws"], b["igst"], b["duty_total"]) == (D("2134.5"), D("213.5"), D("35769"), D("38118"))
    assert b["other_duty"] == D("1")                                  # customs rounds each item: the total is a rupee more than the parts
    assert b["supplier_name"] == "TESTCO ELECTRIC CO., LTD" and b["supplier_country"] == "JP" and b["invoice_number"] == "FECS25-I-050M" and b["challan_number"] == "2055012136"
    assert b["status"] == "assessed" and b["duty_paid_on"] is None and b["warnings"] == []


def test_a_paid_challan_or_an_out_of_charge_date_moves_the_status_on():
    paid = boe_parse.parse_boe(boe_parse.read_pdf_text(boe_pdf(paid="25/05/2025")))
    assert paid["status"] == "duty_paid" and str(paid["duty_paid_on"]) == "2025-05-25"
    cleared = boe_parse.parse_boe(boe_parse.read_pdf_text(boe_pdf(paid="25/05/2025", ooc="26-MAY-25")))
    assert cleared["status"] == "cleared" and str(cleared["out_of_charge_on"]) == "2025-05-26"


def test_a_pdf_that_is_not_a_bill_of_entry_is_refused():
    with pytest.raises(boe_parse.NotABillOfEntry):
        boe_parse.parse_boe(boe_parse.read_pdf_text(plain_pdf("Invoice for services rendered")))
    with pytest.raises(boe_parse.NotABillOfEntry):
        boe_parse.read_pdf_text(b"%PDF-1.4 not really")


# ------------------------------------------------------------------ through the API
@pytest.fixture()
def db():
    with psycopg.connect(DB, autocommit=True, row_factory=dict_row) as c:
        yield c


@pytest.fixture(scope="module")
def client():
    from fastapi.testclient import TestClient
    from app.main import app
    return TestClient(app)


@pytest.fixture(scope="module", autouse=True)
def seed():
    with psycopg.connect(DB, autocommit=True, row_factory=dict_row) as db:
        db.execute("INSERT INTO organizations (id, name, kind) VALUES (%s, 'Boe Test Org', 'business_group')", (ORG,))
        db.execute("INSERT INTO entities (id, organization_id, legal_name, constitution, cin, track) VALUES (%s, %s, 'Orchid Optics Pvt Ltd', 'private_limited', 'U28900KA2019PTC110002', 'business')", (EN, ORG))
        db.execute("INSERT INTO entity_registrations (entity_id, kind, number, state_code, gst_filing_frequency) VALUES (%s, 'gstin', %s, '29', 'monthly')", (EN, GSTIN))
        db.execute("INSERT INTO users (id, email, full_name) VALUES (%s, %s, 'Boe Owner'), (%s, %s, 'Boe Viewer'), (%s, %s, 'Boe Outsider')", (U_OWNER, OWNER, U_VIEWER, VIEWER, U_OUT, OUTSIDER))
        db.execute("INSERT INTO memberships (entity_id, user_id, role, accepted_at) VALUES (%s, %s, 'owner', now()), (%s, %s, 'viewer', now())", (EN, U_OWNER, EN, U_VIEWER))
    yield


def login(client, email):
    r = client.post("/v1/auth/token", json={"grant_type": "otp", "email": email, "otp": "000000"})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def url(path):
    return f"/v1/entities/{EN}{path}"


def test_reading_keeps_the_pdf_in_the_vault_and_recording_links_it(client, db):
    o = login(client, OWNER)
    pdf = boe_pdf()
    r = client.post(url("/bills-of-entry/read"), headers=o, json={"file_name": "BE-2260434.pdf", "content_base64": b64(pdf)})
    assert r.status_code == 200, r.text
    got = r.json()
    assert got["boe_number"] == "2260434" and got["currency"] == "USD" and got["cif_foreign"] == "2272.80" and got["exchange_rate"] == "86.4" and got["status"] == "assessed"
    assert (got["bcd"], got["sws"], got["igst"], got["other_duty"]) == ("2134.50", "213.50", "35769.00", "1.00")
    assert got["warnings"] == [] and got["existing_boe_id"] is None and got["supplier_party_id"] is None and got["supplier_country"] == "JP"
    assert db.execute("SELECT count(*) AS n FROM bills_of_entry WHERE entity_id = %s", (EN,)).fetchone()["n"] == 0            # reading records nothing
    doc = db.execute("SELECT folder, file_name, mime_type, source::text AS source FROM documents WHERE id = %s", (got["document_id"],)).fetchone()
    assert doc == {"folder": "purchases", "file_name": "BE-2260434.pdf", "mime_type": "application/pdf", "source": "upload"}
    again = client.post(url("/bills-of-entry/read"), headers=o, json={"file_name": "again.pdf", "content_base64": b64(pdf)}).json()
    assert again["document_id"] == got["document_id"]                                                                         # the same bytes are one document

    body = {k: got[k] for k in ("boe_number", "boe_date", "port_code", "currency", "cif_foreign", "exchange_rate", "bcd", "sws", "igst", "other_duty", "status")}
    body.update(document_id=got["document_id"], new_supplier_name=got["supplier_name"], new_supplier_country=got["supplier_country"])
    made = client.post(url("/bills-of-entry"), headers=o, json=body)
    assert made.status_code == 201, made.text
    boe = made.json()
    S["boe"], S["supplier"] = boe["id"], boe["supplier_party_id"]
    assert boe["document_id"] == got["document_id"] and boe["duty_total"] == "38118.00" and boe["status"] == "assessed" and boe["cif_inr"] == "196369.92"
    assert boe["supplier_name"] == "TESTCO ELECTRIC CO., LTD"
    party = db.execute("SELECT is_vendor, country, currency FROM parties WHERE id = %s", (S["supplier"],)).fetchone()
    assert party == {"is_vendor": True, "country": "JP", "currency": "USD"}
    assert db.execute("SELECT taxpayer_type::text AS t FROM party_gst_registrations WHERE party_id = %s", (S["supplier"],)).fetchone()["t"] == "overseas"
    links = db.execute("SELECT subject, role FROM document_links WHERE document_id = %s", (got["document_id"],)).fetchall()
    assert links == [{"subject": "bills_of_entry", "role": "evidence"}]
    listed = client.get(url("/bills-of-entry"), headers=o).json()["data"]
    assert [b["document_id"] for b in listed] == [got["document_id"]]


def test_reading_the_same_bill_again_says_it_is_already_recorded_and_finds_the_supplier(client):
    o = login(client, OWNER)
    r = client.post(url("/bills-of-entry/read"), headers=o, json={"file_name": "BE.pdf", "content_base64": b64(boe_pdf())})
    assert r.status_code == 200
    got = r.json()
    assert got["existing_boe_id"] == S["boe"] and got["supplier_party_id"] == S["supplier"]                                     # "Co., Ltd" and punctuation do not matter
    # a second bill from the same supplier reuses the party rather than adding another
    other = client.post(url("/bills-of-entry/read"), headers=o, json={"file_name": "BE2.pdf", "content_base64": b64(boe_pdf(number="2260999", supplier="Testco Electric Co Ltd"))}).json()
    assert other["existing_boe_id"] is None and other["supplier_party_id"] == S["supplier"]
    body = {k: other[k] for k in ("boe_number", "boe_date", "port_code", "currency", "cif_foreign", "exchange_rate", "bcd", "sws", "igst", "other_duty")}
    made = client.post(url("/bills-of-entry"), headers=o, json={**body, "new_supplier_name": "Testco Electric Co Ltd", "document_id": other["document_id"]})
    assert made.status_code == 201 and made.json()["supplier_party_id"] == S["supplier"]


def test_a_bill_made_out_to_another_company_is_flagged(client):
    o = login(client, OWNER)
    r = client.post(url("/bills-of-entry/read"), headers=o, json={"file_name": "x.pdf", "content_base64": b64(boe_pdf(gstin="27AAAAA0000A1Z5", importer="SOMEONE ELSE LTD", number="1111111"))})
    assert r.status_code == 200
    assert any("27AAAAA0000A1Z5" in w and "not one of this company's registrations" in w for w in r.json()["warnings"])


def test_what_cannot_be_read_and_who_cannot_upload(client):
    o, v = login(client, OWNER), login(client, VIEWER)
    for pdf, why in ((plain_pdf("not a customs form"), "a PDF of something else"), (b"just some text", "not a PDF")):
        r = client.post(url("/bills-of-entry/read"), headers=o, json={"file_name": "x.pdf", "content_base64": b64(pdf)})
        assert r.status_code == 422 and r.json()["code"] == "not_a_bill_of_entry", why
    assert client.post(url("/bills-of-entry/read"), headers=o, json={"file_name": "x.pdf", "content_base64": "!!not base64!!"}).status_code == 400
    assert client.post(url("/bills-of-entry/read"), headers=v, json={"file_name": "x.pdf", "content_base64": b64(boe_pdf())}).status_code == 403
    assert client.post(url("/bills-of-entry/read"), headers=login(client, OUTSIDER), json={"file_name": "x.pdf", "content_base64": b64(boe_pdf())}).status_code == 404
    bad = client.post(url("/bills-of-entry"), headers=o, json={"boe_number": "5", "boe_date": "2025-05-24", "port_code": "INBLR4", "currency": "USD", "cif_foreign": "1.00", "exchange_rate": "86.4", "document_id": str(uuid.uuid4())})
    assert bad.status_code == 400                                                                                                # a document that is not in this vault
