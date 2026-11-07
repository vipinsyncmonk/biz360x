"""Ledger posting: system accounts, journal templates and the posting helper.

Templates are pure functions returning balanced `Line`s keyed by *account key*
(not account id), so they are unit-tested without a database. `post_journal`
resolves keys to the entity's accounts (creating the standard system accounts on
first use), writes the entry and posts it in the caller's transaction. The
database guards (balance, open period, frozen lines) are the final authority.

Templates exist for sales documents (invoice, debit note, credit note) and
receipts. Purchase-bill and payroll templates arrive with those modules (M3, M4).
"""
from __future__ import annotations

import datetime as dt
import json
import uuid
from dataclasses import dataclass
from decimal import Decimal

from .core import jsonable

ZERO = Decimal("0")

# key -> (code, name, type, subtype)
SYSTEM_ACCOUNTS = {
    "bank": ("1100", "Bank", "asset", "bank"),
    "cash": ("1110", "Cash", "asset", "cash"),
    "receivable": ("1200", "Trade receivables", "asset", "receivable"),
    "tds_receivable": ("1350", "TDS receivable", "asset", "tds_receivable"),
    "gst_input_cgst": ("1310", "Input CGST", "asset", "gst_input_cgst"),
    "gst_input_sgst": ("1320", "Input SGST", "asset", "gst_input_sgst"),
    "gst_input_igst": ("1330", "Input IGST", "asset", "gst_input_igst"),
    "payable": ("2100", "Trade payables", "liability", "payable"),
    "tds_payable": ("2200", "TDS payable", "liability", "tds_payable"),
    "purchases": ("5000", "Purchases and expenses", "expense", "purchases"),
    "salary_expense": ("5200", "Salaries and wages", "expense", "salary_expense"),
    "employer_contrib_expense": ("5210", "Employer PF and ESI", "expense", "employer_contrib_expense"),
    "salary_payable": ("2400", "Salary payable", "liability", "salary_payable"),
    "pf_payable": ("2410", "PF payable", "liability", "pf_payable"),
    "esi_payable": ("2420", "ESI payable", "liability", "esi_payable"),
    "pt_payable": ("2430", "Professional tax payable", "liability", "pt_payable"),
    "payroll_recoveries": ("2440", "Employee recoveries", "liability", "payroll_recoveries"),
    "interest_penalty": ("5900", "Interest and penalties", "expense", "interest_penalty"),
    "advance_tax": ("1360", "Advance tax paid", "asset", "advance_tax"),
    "advances_received": ("2150", "Customer advances", "liability", "customer_advance"),
    "gst_output_cgst": ("2310", "Output CGST", "liability", "gst_output_cgst"),
    "gst_output_sgst": ("2320", "Output SGST", "liability", "gst_output_sgst"),
    "gst_output_igst": ("2330", "Output IGST", "liability", "gst_output_igst"),
    "gst_output_cess": ("2340", "Output cess", "liability", "gst_output_cess"),
    "sales": ("4000", "Sales", "income", "sales"),
    "sales_returns": ("4100", "Sales returns and credit notes", "income", "sales_returns"),
    "round_off": ("4900", "Round off", "income", "round_off"),
}


@dataclass
class Line:
    key: str                     # SYSTEM_ACCOUNTS key, or "account:<uuid>" for an explicit account
    debit: Decimal = ZERO
    credit: Decimal = ZERO
    party_id: uuid.UUID | str | None = None
    memo: str | None = None


class PostingError(ValueError):
    pass


def balanced(lines: list[Line]) -> bool:
    dr, cr = sum((l.debit for l in lines), ZERO), sum((l.credit for l in lines), ZERO)
    return dr == cr and dr > 0


# ------------------------------------------------------------------ templates
def sales_document_lines(doc: dict) -> list[Line]:
    """Invoice / debit note: Dr receivable, Cr sales and output GST. Credit note: the mirror image.
    `doc` needs doc_type, party_id, grand_total, taxable_total, cgst/sgst/igst/cess_total, round_off."""
    credit_note = doc["doc_type"] == "credit_note"
    party = doc["party_id"]
    revenue = "sales_returns" if credit_note else "sales"
    parts: list[tuple[str, Decimal]] = [(revenue, doc["taxable_total"])]
    for k, f in (("gst_output_cgst", "cgst_total"), ("gst_output_sgst", "sgst_total"),
                 ("gst_output_igst", "igst_total"), ("gst_output_cess", "cess_total")):
        if doc.get(f):
            parts.append((k, doc[f]))
    if doc["round_off"]:
        parts.append(("round_off", doc["round_off"]))
    out: list[Line] = [Line("receivable", debit=doc["grand_total"], party_id=party) if not credit_note
                       else Line("receivable", credit=doc["grand_total"], party_id=party)]
    for key, amount in parts:
        if credit_note:
            # revenue and tax are reversed; a positive round-off on the document is income, so it is reversed too
            out.append(Line(key, debit=amount) if amount > 0 else Line(key, credit=-amount))
        else:
            out.append(Line(key, credit=amount) if amount > 0 else Line(key, debit=-amount))
    if not balanced(out):
        raise PostingError(f"document {doc.get('number')} does not balance: check taxable + tax + round_off = grand_total")
    return out


def receipt_lines(*, amount: Decimal, allocated: Decimal, party_id, method: str, bank_account_key: str | None = None) -> list[Line]:
    """Dr bank/cash; Cr receivable for what is allocated, Cr customer advances for the rest."""
    if allocated > amount or allocated < 0:
        raise PostingError("allocated must be between 0 and the receipt amount")
    debit_key = bank_account_key or ("cash" if method == "cash" else "bank")
    out = [Line(debit_key, debit=amount)]
    if allocated:
        out.append(Line("receivable", credit=allocated, party_id=party_id))
    if amount - allocated:
        out.append(Line("advances_received", credit=amount - allocated, party_id=party_id))
    return out


def purchase_bill_lines(bill: dict, expense: list[tuple[str, Decimal]] | None = None) -> list[Line]:
    """Booked purchase bill. Dr expense (plus any tax that is not claimable as ITC), Dr input GST when the bill is
    eligible; Cr payables for the amount we will pay the supplier, Cr TDS payable for what we withhold.
    `expense` optionally splits the taxable value over account keys; it must add up to taxable_total."""
    eligible = bill["itc_eligibility"] == "eligible"
    taxes = [("gst_input_cgst", bill["cgst_total"]), ("gst_input_sgst", bill["sgst_total"]), ("gst_input_igst", bill["igst_total"])]
    tax_total = sum((t for _, t in taxes), ZERO)
    split = expense or [("purchases", bill["taxable_total"])]
    if sum((a for _, a in split), ZERO) != bill["taxable_total"]:
        raise PostingError("expense split does not add up to the taxable value")
    out: list[Line] = []
    for i, (key, amount) in enumerate(split):
        out.append(Line(key, debit=amount + (tax_total if (not eligible and i == 0) else ZERO)))
    if eligible:
        out += [Line(k, debit=t) for k, t in taxes if t]
    tds = bill.get("tds_amount") or ZERO
    out.append(Line("payable", credit=bill["grand_total"] - tds, party_id=bill["party_id"]))
    if tds:
        out.append(Line("tds_payable", credit=tds, party_id=bill["party_id"]))
    if bill["grand_total"] != bill["taxable_total"] + tax_total:
        raise PostingError(f"bill {bill.get('bill_no')} does not add up: taxable + tax != grand total")
    if not balanced(out):
        raise PostingError(f"bill {bill.get('bill_no')} does not balance")
    return out


def payroll_run_lines(t: dict) -> list[Line]:
    """Approved payroll. Dr salary expense (gross) and employer contributions; Cr salary payable (net) and each statutory
    liability. `t` holds the run totals: gross, net, pf_employee, pf_employer, esi_employee, esi_employer, pt, tds, other."""
    out = [Line("salary_expense", debit=t["gross"])]
    if t["pf_employer"] + t["esi_employer"]:
        out.append(Line("employer_contrib_expense", debit=t["pf_employer"] + t["esi_employer"]))
    out.append(Line("salary_payable", credit=t["net"]))
    for key, amount in (("pf_payable", t["pf_employee"] + t["pf_employer"]), ("esi_payable", t["esi_employee"] + t["esi_employer"]),
                        ("pt_payable", t["pt"]), ("tds_payable", t["tds"]), ("payroll_recoveries", t["other"])):
        if amount:
            out.append(Line(key, credit=amount))
    if not balanced(out):
        raise PostingError("payroll run does not balance: net != gross - deductions")
    return out


def vendor_payment_lines(*, amount: Decimal, party_id, bank_account_key: str) -> list[Line]:
    return [Line("payable", debit=amount, party_id=party_id), Line(bank_account_key, credit=amount)]


# ------------------------------------------------------------------ database helpers
def account_id(cur, entity_id, key: str):
    """Resolve an account key to an id, creating the standard system account on first use."""
    if key.startswith("account:"):
        return uuid.UUID(key.split(":", 1)[1])
    if key not in SYSTEM_ACCOUNTS:
        raise PostingError(f"unknown account key {key!r}")
    code, name, typ, subtype = SYSTEM_ACCOUNTS[key]
    cur.execute("SELECT id FROM ledger_accounts WHERE entity_id = %s AND (subtype = %s OR code = %s) ORDER BY (subtype = %s) DESC LIMIT 1",
                (entity_id, subtype, code, subtype))
    r = cur.fetchone()
    if r:
        return r["id"]
    cur.execute("""INSERT INTO ledger_accounts (entity_id, code, name, type, subtype, is_system)
                   VALUES (%s, %s, %s, %s::account_type, %s, true) RETURNING id""", (entity_id, code, name, typ, subtype))
    return cur.fetchone()["id"]


def next_entry_no(cur, entity_id) -> str:
    cur.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (f"journal:{entity_id}",))
    cur.execute("SELECT coalesce(max(substr(entry_no, 4)::int), 0) + 1 AS n FROM journal_entries WHERE entity_id = %s AND entry_no ~ '^JE-[0-9]+$'",
                (entity_id,))
    return f"JE-{cur.fetchone()['n']:06d}"


def post_journal(cur, entity_id, entry_date: dt.date, source: str, source_id, narration: str, lines: list[Line]) -> uuid.UUID:
    """Create and post a journal in the caller's transaction. Raises the database's own
    exceptions for an unbalanced entry or a locked period (mapped to API codes in core.map_db_error)."""
    if not balanced(lines):
        raise PostingError("journal does not balance")
    entry_no = next_entry_no(cur, entity_id)
    cur.execute("""INSERT INTO journal_entries (entity_id, entry_no, entry_date, source, source_id, narration)
                   VALUES (%s, %s, %s, %s::journal_source, %s, %s) RETURNING id""",
                (entity_id, entry_no, entry_date, source, source_id, narration))
    jid = cur.fetchone()["id"]
    for i, l in enumerate(lines, 1):
        cur.execute("""INSERT INTO journal_lines (entry_id, line_no, account_id, party_id, debit, credit, memo)
                       VALUES (%s, %s, %s, %s, %s, %s, %s)""",
                    (jid, i, account_id(cur, entity_id, l.key), l.party_id, l.debit, l.credit, l.memo))
    cur.execute("UPDATE journal_entries SET status = 'posted', posted_by = app.current_user_id() WHERE id = %s", (jid,))
    return jid


def reverse_journal(cur, entity_id, journal_id, reversal_date: dt.date, narration: str | None = None) -> uuid.UUID:
    cur.execute("SELECT * FROM journal_entries WHERE id = %s AND entity_id = %s FOR UPDATE", (journal_id, entity_id))
    j = cur.fetchone()
    if not j:
        return None
    if j["status"] != "posted":
        raise PostingError(f"only posted journals can be reversed (this one is {j['status']})")
    cur.execute("SELECT account_id, party_id, debit, credit, memo FROM journal_lines WHERE entry_id = %s ORDER BY line_no", (journal_id,))
    src = cur.fetchall()
    entry_no = next_entry_no(cur, entity_id)
    cur.execute("""INSERT INTO journal_entries (entity_id, entry_no, entry_date, source, source_id, narration, reversal_of)
                   VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING id""",
                (entity_id, entry_no, reversal_date, j["source"], j["source_id"], narration or f"Reversal of {j['entry_no']}", journal_id))
    rid = cur.fetchone()["id"]
    for i, l in enumerate(src, 1):
        cur.execute("""INSERT INTO journal_lines (entry_id, line_no, account_id, party_id, debit, credit, memo)
                       VALUES (%s, %s, %s, %s, %s, %s, %s)""", (rid, i, l["account_id"], l["party_id"], l["credit"], l["debit"], l["memo"]))
    cur.execute("UPDATE journal_entries SET status = 'posted', posted_by = app.current_user_id() WHERE id = %s", (rid,))
    cur.execute("UPDATE journal_entries SET status = 'reversed' WHERE id = %s", (journal_id,))
    return rid


def post_sales_document(cur, entity_id, doc_id) -> uuid.UUID:
    cur.execute("""SELECT id, doc_type::text, number, doc_date, party_id, grand_total, taxable_total, cgst_total, sgst_total,
                          igst_total, cess_total, round_off FROM sales_documents WHERE id = %s AND entity_id = %s""", (doc_id, entity_id))
    d = cur.fetchone()
    jid = post_journal(cur, entity_id, d["doc_date"], "sales_document", doc_id, f"{d['doc_type'].replace('_', ' ').title()} {d['number']}",
                       sales_document_lines(d))
    cur.execute("UPDATE sales_documents SET journal_entry_id = %s WHERE id = %s", (jid, doc_id))
    return jid


def dumps(x) -> str:
    return json.dumps(x, default=jsonable)
