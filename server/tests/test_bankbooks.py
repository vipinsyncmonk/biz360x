"""Booking the bank statement: narrations read into parts, the other half suggested (a document the app holds, a rule, the starter list), a line classified,
a rule applied to similar lines, and the journal each of those posts. The lines are the ones from a real statement."""
import datetime as dt
import os
import uuid
from decimal import Decimal as D

import psycopg
import pytest
from psycopg.rows import dict_row

from app import bank_rules as br

DB = os.environ["DATABASE_URL"]
ORG = "00000000-0000-0000-0000-000000f2a000"
EN = "00000000-0000-0000-0000-000000f2a001"
LEDGER_BANK = "00000000-0000-0000-0000-000000f2a003"
BANK = "00000000-0000-0000-0000-000000f2a004"
U_OWNER, U_VIEWER = "00000000-0000-0000-0000-000000f2a011", "00000000-0000-0000-0000-000000f2a012"
OWNER, VIEWER = "bk-owner@example.in", "bk-viewer@example.in"
TODAY = dt.date.today()
S: dict = {}

UPI1 = "UPI/110204743611/UPI Pay/8792003390@ibl//ICI8d18597f26b645cb84742310b7fb5163/"
UPI2 = "UPI/110204743999/UPI Pay/8792003390@ibl//ICIaaaa/"
UPI3 = "UPI/110204744000/UPI Pay/9000000001@ibl//ICIbbbb/"
CARD = "ATD/Auto Debit CC1xx6441"
FD = "TRF TO FD no. 317113010027"
SAL = "INF/NEFT/IN42609256188348/KKBK0008107/SalaryMarch26  /Madan"
RENT = "INF/NEFT/IN42609256190107/SBIN0010366/March26Rent   /SUNDARESAN"
DIRECTOR = "INF/NEFT/IN42609256057825/UTIB0001888/DirectorPayment/Yamini"
CHARGES = "SMS CHARGES Q2"
POWER = "UPI-BESCOM-bescom@icici-ICIC0000001-123456789012-Bill"
STATEMENT = [(UPI1, "-2500.00"), (UPI2, "-1800.00"), (UPI3, "-900.00"), (CARD, "-45000.00"), (FD, "-300000.00"), (SAL, "-15000.00"), (RENT, "-66150.00"),
             (DIRECTOR, "-1100000.00"), (CHARGES, "-118.00"), (POWER, "-8400.00")]


# ------------------------------------------------------------------ pure
def tline(description, amount, **kw):
    from app.bank_parse import parse_narration
    p = parse_narration(description)
    return {"amount": D(amount), "description": description, "mode": p.mode, "counterparty_key": p.key, **kw}


def test_a_rule_needs_every_condition_it_states_and_matches_whole_words():
    from app.bank_parse import words
    t = tline(RENT, "-66150")
    assert br.rule_matches({"keyword": "rent"}, t, words(t["description"])) and not br.rule_matches({"keyword": "rent", "direction": "credit"}, t, words(t["description"]))
    cur_acc = tline("CURRENT ACCOUNT CHARGES", "-10")
    assert not br.rule_matches({"keyword": "rent"}, cur_acc, words(cur_acc["description"]))                       # "rent" is not in "current"
    assert br.rule_matches({"counterparty_key": "sundaresan", "mode": "neft"}, t, words(t["description"])) and not br.rule_matches({"counterparty_key": "sundaresan", "mode": "upi"}, t, [])
    assert not br.rule_matches({}, t, words(t["description"]))                                                      # no condition, never matches
    assert [br.specificity(r) for r in ({"counterparty_key": "x"}, {"keyword": "x"}, {"mode": "upi"})] == [3, 2, 1]


def test_the_starter_list_reads_the_lines_that_are_not_expenses_as_what_they_are():
    from app.bank_parse import words
    def starter(desc, amount="-100"):
        t = tline(desc, amount)
        s = br.starter_for(t, words(desc))
        return s["id"] if s else None
    assert (starter(FD), starter(CARD), starter(SAL), starter(RENT), starter(DIRECTOR), starter(CHARGES), starter(POWER)) == ("fd_out", "card_payment", "salary", "rent", "director", "bank_charges", "electricity")
    assert starter(UPI1) is None and starter("NEFT-AXOIN12345678-ACME TRADERS-HDFC0000001-INV 17") is None        # nothing to go on: left to a person
    assert starter("INT.PD:01-07-2026 to 30-09-2026", "+500") == "interest_in" and starter(FD, "+100") == "fd_in"
    assert starter(SAL, "+100") is None                                                                           # the starter list reads a salary only on money going out


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
    db.execute("INSERT INTO organizations (id, name, kind) VALUES (%s, 'Bank Books Org', 'business_group')", (ORG,))
    db.execute("INSERT INTO entities (id, organization_id, legal_name, constitution, track) VALUES (%s, %s, 'Syncish Technologies Pvt Ltd', 'private_limited', 'business')", (EN, ORG))
    db.execute("INSERT INTO users (id, email, full_name) VALUES (%s, %s, 'Bk Owner'), (%s, %s, 'Bk Viewer')", (U_OWNER, OWNER, U_VIEWER, VIEWER))
    db.execute("INSERT INTO memberships (entity_id, user_id, role, accepted_at) VALUES (%s, %s, 'owner', now()), (%s, %s, 'viewer', now())", (EN, U_OWNER, EN, U_VIEWER))
    db.execute("INSERT INTO ledger_accounts (id, entity_id, code, name, type, subtype) VALUES (%s, %s, '1100', 'ICICI current', 'asset', 'bank')", (LEDGER_BANK, EN))
    db.execute("INSERT INTO bank_accounts (id, entity_id, ledger_account_id, bank_name, account_last4, kind) VALUES (%s, %s, %s, 'ICICI', '0334', 'current')", (BANK, EN, LEDGER_BANK))
    yield
    db.execute("DELETE FROM tasks WHERE entity_id = %s", (EN,))


def feed(rows, start=0):
    return {"transactions": [{"external_id": f"BK-{start + i}", "txn_date": str(TODAY), "amount": a, "description": d} for i, (d, a) in enumerate(rows)]}


def lines(client, o):
    return {x["description"]: x for x in client.get(url(f"/bank-transactions?bank_account_id={BANK}&limit=200"), headers=o).json()["data"]}


def open_suggestion(t):
    return next((s for s in t["suggestions"] if s["status"] == "suggested"), None)


def journal_of(db, txn_id):
    row = db.execute("SELECT journal_entry_id FROM bank_transactions WHERE id = %s", (txn_id,)).fetchone()
    if not row["journal_entry_id"]:
        return None
    ls = db.execute("SELECT a.code, l.debit, l.credit FROM journal_lines l JOIN ledger_accounts a ON a.id = l.account_id WHERE l.entry_id = %s ORDER BY l.line_no", (row["journal_entry_id"],)).fetchall()
    assert sum(l["debit"] for l in ls) == sum(l["credit"] for l in ls)
    return [(l["code"], l["debit"], l["credit"]) for l in ls]


def test_the_statement_is_read_and_each_line_gets_the_other_half_suggested_where_it_can(client, db):
    o = login(client, OWNER)
    r = client.post(url(f"/bank-accounts/{BANK}/transactions"), headers=o, json=feed(STATEMENT))
    assert r.status_code == 201 and r.json()["imported"] == 10 and r.json()["suggested"] == 7
    t = lines(client, o)
    S["t"] = t
    assert (t[UPI1]["mode"], t[UPI1]["counterparty_key"], t[UPI1]["remark"]) == ("upi", "8792003390@ibl", None)
    assert (t[SAL]["mode"], t[SAL]["remark"], t[SAL]["counterparty_key"]) == ("neft", "SalaryMarch26", "madan")
    assert (t[CARD]["mode"], t[CARD]["counterparty_key"]) == ("auto_debit", "card:cc1xx6441")
    want = {FD: "Book to 1590 Fixed deposits", CARD: "Book to 2590 Credit card payable", SAL: "Book to 5200 Salaries and wages", RENT: "Book to 5390 Rent",
            DIRECTOR: "Book to 2690 Directors' loan account", CHARGES: "Book to 5910 Bank charges", POWER: "Book to 5391 Electricity, telephone and internet"}
    for desc, label in want.items():
        s = open_suggestion(t[desc])
        assert (s["target_kind"], s["label"], s["rule"]) == ("ledger_account", label, "starter"), desc
        assert t[desc]["match_status"] == "suggested" and "starter list" in s["reason"]
    assert open_suggestion(t[DIRECTOR])["confidence"] == 0.55 and "loan, remuneration" in open_suggestion(t[DIRECTOR])["reason"]
    assert [t[x]["match_status"] for x in (UPI1, UPI2, UPI3)] == ["unmatched"] * 3                                  # a bare mobile-number handle: nothing to go on
    assert "fixed deposit" in open_suggestion(t[FD])["reason"] and "twice" in open_suggestion(t[CARD])["reason"]


def test_accepting_a_suggestion_posts_the_journal_and_a_refused_one_is_not_offered_again(client, db):
    o = login(client, OWNER)
    t = S["t"]
    fd = open_suggestion(t[FD])
    r = client.post(url(f"/bank-matches/{fd['id']}/accept"), headers={**o, **key()})
    assert r.status_code == 200 and r.json()["match_status"] == "matched"
    assert journal_of(db, t[FD]["id"]) == [("1590", D("300000.00"), D("0.00")), ("1100", D("0.00"), D("300000.00"))]      # money out: the deposit is debited, the bank credited
    assert db.execute("SELECT source::text AS s FROM journal_entries e JOIN bank_transactions b ON b.journal_entry_id = e.id WHERE b.id = %s", (t[FD]["id"],)).fetchone()["s"] == "bank"
    assert client.post(url(f"/bank-matches/{fd['id']}/accept"), headers={**o, **key()}).status_code == 409                 # already done

    rent = open_suggestion(t[RENT])
    assert client.post(url(f"/bank-matches/{rent['id']}/reject"), headers=o).status_code == 200
    assert client.post(url("/bank-transactions/suggest"), headers=o).json() == {"suggested": 0}
    again = lines(client, o)[RENT]
    assert again["match_status"] == "unmatched" and open_suggestion(again) is None                                     # the account the person refused is not offered again
    v = login(client, VIEWER)
    assert client.post(url(f"/bank-matches/{open_suggestion(t[SAL])['id']}/accept"), headers={**v, **key()}).status_code == 403


def test_classifying_a_line_with_remember_and_apply_books_the_similar_lines_too(client, db):
    o, v = login(client, OWNER), login(client, VIEWER)
    t = lines(client, o)
    acct = str(db.execute("INSERT INTO ledger_accounts (entity_id, code, name, type) VALUES (%s, '5500', 'Staff welfare', 'expense') RETURNING id", (EN,)).fetchone()["id"])
    body = {"account_id": acct, "remember": "counterparty", "apply_to_similar": True}
    k = key()
    assert client.post(url(f"/bank-transactions/{t[UPI1]['id']}/classify"), headers=v, json=body).status_code == 403
    r = client.post(url(f"/bank-transactions/{t[UPI1]['id']}/classify"), headers={**o, **k}, json=body)
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["match_status"] == "matched" and out["also_booked"] == 1 and out["rule_id"]
    assert journal_of(db, t[UPI1]["id"]) == [("5500", D("2500.00"), D("0.00")), ("1100", D("0.00"), D("2500.00"))]
    assert journal_of(db, t[UPI2]["id"]) == [("5500", D("1800.00"), D("0.00")), ("1100", D("0.00"), D("1800.00"))]     # the other line to the same handle
    assert lines(client, o)[UPI3]["match_status"] == "unmatched"                                                      # a different handle is left alone
    before = db.execute("SELECT count(*) AS n FROM journal_entries WHERE entity_id = %s", (EN,)).fetchone()["n"]
    assert client.post(url(f"/bank-transactions/{t[UPI1]['id']}/classify"), headers={**o, **k}, json=body).json()["also_booked"] == 1      # a replay does nothing more
    assert db.execute("SELECT count(*) AS n FROM journal_entries WHERE entity_id = %s", (EN,)).fetchone()["n"] == before
    rule = client.get(url("/bank-rules"), headers=o).json()
    assert len(rule) == 1 and (rule[0]["counterparty_key"], rule[0]["account_code"], rule[0]["hits"], rule[0]["direction"]) == ("8792003390@ibl", "5500", 2, "debit")
    S["acct"] = acct

    new_key = key()
    assert client.post(url(f"/bank-transactions/{t[UPI1]['id']}/classify"), headers={**o, **new_key}, json={"account_id": acct}).status_code == 409          # already booked
    assert client.post(url(f"/bank-transactions/{uuid.uuid4()}/classify"), headers={**o, **key()}, json={"account_id": acct}).status_code == 404
    assert client.post(url(f"/bank-transactions/{t[UPI3]['id']}/classify"), headers={**o, **key()}, json={"account_id": LEDGER_BANK}).status_code == 400  # not to its own bank account
    assert client.post(url(f"/bank-transactions/{t[UPI3]['id']}/classify"), headers={**o, **key()}, json={"account_id": acct, "apply_to_similar": True}).status_code == 400
    assert client.post(url(f"/bank-transactions/{t[UPI3]['id']}/classify"), headers={**o, **key()}, json={"account_id": acct, "remember": "keyword", "keyword": "bescom"}).status_code == 400
    assert client.post(url(f"/bank-transactions/{t[CHARGES]['id']}/classify"), headers={**o, **key()}, json={"account_id": acct, "remember": "counterparty"}).status_code == 400
    assert client.post(url(f"/bank-transactions/{t[UPI3]['id']}/classify"), headers=o, json={"account_id": acct}).status_code == 400                       # the key is mandatory


def test_a_rule_the_company_made_is_suggested_for_new_lines_before_the_starter_list(client, db):
    o = login(client, OWNER)
    client.post(url(f"/bank-accounts/{BANK}/transactions"), headers=o, json=feed([("UPI/110204745555/UPI Pay/8792003390@ibl//ICIcccc/", "-700.00")], start=100))
    t = lines(client, o)["UPI/110204745555/UPI Pay/8792003390@ibl//ICIcccc/"]
    s = open_suggestion(t)
    assert (s["target_kind"], s["rule"], s["confidence"], s["label"]) == ("ledger_account", "rule", 0.95, "Book to 5500 Staff welfare")
    assert s["reason"].startswith("A rule you made: payments to or from 8792003390@ibl")
    # rules by hand: a keyword, a duplicate replaces the first, and removing one stops its suggestions
    v = login(client, VIEWER)
    new = {"keyword": "Prime Logistics", "account_id": S["acct"], "direction": "debit"}
    assert client.post(url("/bank-rules"), headers=v, json=new).status_code == 403
    assert client.post(url("/bank-rules"), headers=o, json={"account_id": S["acct"]}).status_code == 400                     # nothing to look for
    made = client.post(url("/bank-rules"), headers=o, json=new)
    assert made.status_code == 201 and made.json()["keyword"] == "prime logistics"
    same = client.post(url("/bank-rules"), headers=o, json={**new, "memo": "freight"})
    assert same.json()["id"] == made.json()["id"] and same.json()["memo"] == "freight" and len(client.get(url("/bank-rules"), headers=o).json()) == 2
    assert client.delete(url(f"/bank-rules/{made.json()['id']}"), headers=o).status_code == 204
    assert client.delete(url(f"/bank-rules/{made.json()['id']}"), headers=o).status_code == 404 and len(client.get(url("/bank-rules"), headers=o).json()) == 1


def test_a_payment_the_app_already_booked_is_linked_not_posted_again(client, db):
    o = login(client, OWNER)
    vendor = str(db.execute("INSERT INTO parties (entity_id, legal_name, is_vendor, is_customer) VALUES (%s, 'Acme Parts', true, false) RETURNING id", (EN,)).fetchone()["id"])
    vp = str(db.execute("""INSERT INTO vendor_payments (entity_id, party_id, payment_no, paid_on, amount, method, bank_account_id, utr, status)
                           VALUES (%s, %s, 'VP-0001', %s, 11700, 'neft', %s, 'UTRX123456789', 'paid') RETURNING id""", (EN, vendor, TODAY, BANK)).fetchone()["id"])
    ch = str(db.execute("""INSERT INTO tax_challans (entity_id, kind, period_label, amount, paid_on, challan_ref, status)
                           VALUES (%s, 'gst_pmt06', 'Oct 2026', 5000, %s, 'CIN26111100001', 'paid') RETURNING id""", (EN, TODAY)).fetchone()["id"])
    emp = db.execute("INSERT INTO employees (entity_id, employee_code, full_name, date_of_joining, work_state) VALUES (%s, 'E1', 'Staff One', '2020-01-01', '29')", (EN,))
    emp = db.execute("SELECT id FROM employees WHERE entity_id = %s", (EN,)).fetchone()["id"]
    run = str(db.execute("INSERT INTO payroll_runs (entity_id, period_month, status, headcount, paid_on) VALUES (%s, %s, 'paid', 1, %s) RETURNING id", (EN, TODAY.replace(day=1), TODAY)).fetchone()["id"])
    db.execute("""INSERT INTO payslips (run_id, employee_id, paid_days, gross, wages, pf_wage, pf_employee, pf_employer_epf, pf_employer_eps, esi_applicable, net_pay)
                  VALUES (%s, %s, 30, 20000, 10000, 10000, 1200, 367, 833, false, 18800)""", (run, emp))
    rows = [("NEFT/UTRX123456789/ACME PARTS", "-11700.00"), ("GST PAYMENT CIN26111100001", "-5000.00"), ("INF/NEFT/IN111222333/HDFC0000001/Salary/Staff One", "-18800.00"),
            ("INF/NEFT/IN999888777/HDFC0000001/SalaryApril/Other Person", "-9999.00")]
    assert client.post(url(f"/bank-accounts/{BANK}/transactions"), headers=o, json=feed(rows, start=200)).json()["suggested"] == 4
    t = lines(client, o)
    pay, chal, sal, other = (open_suggestion(t[d]) for d, _ in rows)
    assert (pay["target_kind"], pay["confidence"], pay["rule"], pay["label"]) == ("vendor_payment", 0.99, "vendor_payment_utr", "Vendor payment VP-0001")
    assert (chal["target_kind"], chal["confidence"], chal["label"]) == ("tax_challan", 0.99, "Challan gst_pmt06 Oct 2026")
    assert (sal["target_kind"], sal["confidence"], sal["label"]) == ("payroll_run", 0.85, f"Salaries {TODAY:%b %Y}")
    assert (other["target_kind"], other["label"]) == ("ledger_account", "Book to 2400 Salary payable")                      # payroll runs here, so a salary line goes to the payable the run created
    for desc, s in ((rows[0][0], pay), (rows[1][0], chal), (rows[2][0], sal)):
        out = client.post(url(f"/bank-matches/{s['id']}/accept"), headers={**o, **key()})
        assert out.status_code == 200 and out.json()["match_status"] == "matched"
        assert journal_of(db, t[desc]["id"]) is None                                                                   # linked only: the payment was booked when it was paid
    assert str(db.execute("SELECT bank_transaction_id FROM vendor_payments WHERE id = %s", (vp,)).fetchone()["bank_transaction_id"]) == t[rows[0][0]]["id"]
    assert str(db.execute("SELECT bank_transaction_id FROM tax_challans WHERE id = %s", (ch,)).fetchone()["bank_transaction_id"]) == t[rows[1][0]]["id"]
    # the same payment cannot be matched to a second line
    client.post(url(f"/bank-accounts/{BANK}/transactions"), headers=o, json=feed([("NEFT/UTRX123456789/ACME PARTS AGAIN", "-11700.00")], start=300))
    assert open_suggestion(lines(client, o)["NEFT/UTRX123456789/ACME PARTS AGAIN"]) is None


def test_the_audit_chain_is_intact(db):
    assert db.execute("SELECT app.audit_verify() AS v").fetchone()["v"] is None
