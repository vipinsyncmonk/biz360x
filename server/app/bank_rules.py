"""Finding the other half of a bank line, and posting it.

For each unmatched line, in this order:
  1. a document the app already holds: a vendor payment (same UTR, or the same amount on the same days), a challan, a payroll run. These were booked when they
     were paid, so the line is only linked to them; posting it again would count the money twice;
  2. a rule the company taught ("lines to this UPI handle go to Rent");
  3. the starter list: a small set of well-known shapes (a transfer to a fixed deposit, a card payment, bank charges, rent, electricity) mapped to standard accounts.
     It is a starting point and a CA should review it.
The result is only ever a suggestion. A person accepts it, or tells the app to apply a rule they just made to similar lines.
"""
from __future__ import annotations

import datetime as dt
import uuid
from decimal import Decimal

from . import ledger
from .bank_parse import parse_narration, words
from .core import ApiError, audit

ZERO = Decimal("0")
CONF_BY_SPECIFICITY = {3: Decimal("0.95"), 2: Decimal("0.90"), 1: Decimal("0.70")}

# id, direction, mode, phrases (any of), account key, confidence, what it is. A phrase is a run of whole words.
STARTER: list[dict] = [
    {"id": "fd_out", "dir": "debit", "mode": "transfer", "any": [("fd",), ("rd",), ("deposit",)], "key": "fixed_deposits", "conf": 0.85, "what": "A transfer to a fixed deposit, which is an asset and not an expense"},
    {"id": "fd_in", "dir": "credit", "mode": "transfer", "any": [("fd",), ("rd",), ("deposit",)], "key": "fixed_deposits", "conf": 0.80, "what": "Money back from a fixed deposit"},
    {"id": "interest_in", "dir": "credit", "mode": "interest", "any": [], "key": "interest_income", "conf": 0.75, "what": "Interest the bank paid you (tax may have been deducted at source)"},
    {"id": "card_payment", "dir": "debit", "mode": "auto_debit", "any": [("credit", "card"), ("cc",), ("card",)], "key": "credit_card_payable", "conf": 0.80,
     "what": "A credit-card bill paid from the bank. The spending is on the card statement, so booking it as an expense here would count it twice"},
    {"id": "card_payment2", "dir": "debit", "mode": None, "any": [("credit", "card"), ("card", "payment"), ("cc", "payment")], "key": "credit_card_payable", "conf": 0.75, "what": "A credit-card bill paid from the bank"},
    {"id": "salary", "dir": "debit", "mode": None, "any": [("salary",), ("salaries",), ("payroll",), ("wages",)], "key": "salary", "conf": 0.80, "what": "Salary"},
    {"id": "director", "dir": "debit", "mode": None, "any": [("director",)], "key": "director_loan", "conf": 0.55,
     "what": "A payment to a director. Whether it is a loan, remuneration or reimbursement decides the treatment, so check before accepting"},
    {"id": "rent", "dir": "debit", "mode": None, "any": [("rent",)], "key": "rent_expense", "conf": 0.75, "what": "Rent (check TDS on rent, and whether GST is charged or payable by you)"},
    {"id": "electricity", "dir": "debit", "mode": None, "any": [("electricity",), ("bescom",), ("msedcl",), ("tneb",), ("tangedco",), ("bses",), ("tpddl",), ("cesc",), ("kseb",), ("apspdcl",), ("tsspdcl",), ("uppcl",), ("pspcl",), ("torrent", "power"), ("tata", "power")],
     "key": "utilities_expense", "conf": 0.85, "what": "Electricity"},
    {"id": "telecom", "dir": "debit", "mode": None, "any": [("airtel",), ("jio",), ("vodafone",), ("bsnl",), ("broadband",), ("telephone",), ("fibernet",)], "key": "utilities_expense", "conf": 0.70, "what": "Telephone or internet"},
    {"id": "bank_charges", "dir": "debit", "mode": "charges", "any": [], "key": "bank_charges", "conf": 0.85, "what": "Bank charges (and the GST on them)"},
    {"id": "insurance", "dir": "debit", "mode": None, "any": [("insurance",), ("lombard",), ("ergo",)], "key": "insurance_expense", "conf": 0.70, "what": "Insurance"},
    {"id": "epf", "dir": "debit", "mode": None, "any": [("epf",), ("epfo",), ("provident", "fund")], "key": "pf_payable", "conf": 0.70, "what": "Provident fund paid over (settles the PF payable)"},
    {"id": "esi", "dir": "debit", "mode": None, "any": [("esic",), ("esi",)], "key": "esi_payable", "conf": 0.70, "what": "ESI paid over (settles the ESI payable)"},
    {"id": "pt", "dir": "debit", "mode": None, "any": [("professional", "tax"), ("prof", "tax")], "key": "pt_payable", "conf": 0.70, "what": "Professional tax paid over"},
]


def has_phrase(ws: list[str], phrase: tuple[str, ...] | list[str]) -> bool:
    n = len(phrase)
    return n > 0 and any(tuple(ws[i:i + n]) == tuple(phrase) for i in range(len(ws) - n + 1))


def direction_of(amount) -> str:
    return "credit" if Decimal(str(amount)) > 0 else "debit"


def rule_matches(rule: dict, t: dict, ws: list[str]) -> bool:
    """Every condition a rule states must hold. A rule with no condition never matches."""
    stated = False
    if rule.get("direction", "any") not in ("any", direction_of(t["amount"])):
        return False
    if rule.get("counterparty_key"):
        stated = True
        if (t.get("counterparty_key") or "") != rule["counterparty_key"].lower():
            return False
    if rule.get("keyword"):
        stated = True
        if not has_phrase(ws, words(rule["keyword"])):
            return False
    if rule.get("mode"):
        stated = True
        if (t.get("mode") or "") != rule["mode"]:
            return False
    return stated


def specificity(rule: dict) -> int:
    return 3 if rule.get("counterparty_key") else 2 if rule.get("keyword") else 1


def starter_for(t: dict, ws: list[str]) -> dict | None:
    d = direction_of(t["amount"])
    for s in STARTER:
        if s["dir"] != d or (s["mode"] and s["mode"] != t.get("mode")):
            continue
        if s["any"] and not any(has_phrase(ws, p) for p in s["any"]):
            continue
        if not s["mode"] and not s["any"]:
            continue
        return s
    return None


def condition_text(rule: dict) -> str:
    bits = []
    if rule.get("counterparty_key"):
        bits.append(f"to or from {rule['counterparty_key']}")
    if rule.get("keyword"):
        bits.append(f"mentioning “{rule['keyword']}”")
    if rule.get("mode"):
        bits.append(f"by {rule['mode'].replace('_', ' ')}")
    d = {"debit": "Payments", "credit": "Receipts"}.get(rule.get("direction", "any"), "Lines")
    return f"{d} " + " ".join(bits)


# ------------------------------------------------------------------ parsing what the feed gave us
def parse_unparsed(cur, entity_id) -> int:
    cur.execute("SELECT id, description, utr, counterparty_name FROM bank_transactions WHERE entity_id = %s AND mode IS NULL", (entity_id,))
    rows = cur.fetchall()
    for r in rows:
        p = parse_narration(r["description"])
        cur.execute("""UPDATE bank_transactions SET mode = %s, counterparty_key = %s, remark = %s,
                              counterparty_name = coalesce(counterparty_name, %s), utr = coalesce(utr, %s) WHERE id = %s""",
                    (p.mode, p.key, p.remark, p.name, p.reference, r["id"]))
    return len(rows)


# ------------------------------------------------------------------ documents the app already holds
def document_for(cur, entity_id, t: dict, ws: list[str]) -> dict | None:
    if Decimal(str(t["amount"])) >= 0:
        return None
    amt = -Decimal(str(t["amount"]))
    desc = (t["description"] or "").upper()
    utr = (t.get("utr") or "").upper()
    cur.execute("""SELECT id, payment_no, utr, paid_on FROM vendor_payments WHERE entity_id = %s AND status IN ('paid', 'sent') AND bank_transaction_id IS NULL
                      AND amount = %s AND (bank_account_id IS NULL OR bank_account_id = %s)""", (entity_id, amt, t["bank_account_id"]))
    best = None
    for v in cur.fetchall():
        vu = (v["utr"] or "").upper()
        if vu and (vu == utr or vu in desc):
            return {"kind": "vendor_payment", "id": v["id"], "conf": Decimal("0.99"), "rule": "vendor_payment_utr"}
        if v["paid_on"] and abs((v["paid_on"] - t["txn_date"]).days) <= 3 and not best:
            best = {"kind": "vendor_payment", "id": v["id"], "conf": Decimal("0.85"), "rule": "vendor_payment_amount"}
    if best:
        return best
    cur.execute("""SELECT id, challan_ref, paid_on FROM tax_challans WHERE entity_id = %s AND status = 'paid' AND bank_transaction_id IS NULL
                      AND amount + interest + penalty = %s""", (entity_id, amt))
    for c in cur.fetchall():
        if c["challan_ref"] and c["challan_ref"].upper() in desc:
            return {"kind": "tax_challan", "id": c["id"], "conf": Decimal("0.99"), "rule": "challan_ref"}
        if c["paid_on"] and abs((c["paid_on"] - t["txn_date"]).days) <= 3:
            best = {"kind": "tax_challan", "id": c["id"], "conf": Decimal("0.90"), "rule": "challan_amount"}
    if best:
        return best
    cur.execute("""SELECT r.id, (SELECT count(*) FROM payslips p2 WHERE p2.run_id = r.id AND p2.net_pay = %s) AS slips,
                          (SELECT count(*) FROM bank_matches m WHERE m.target_kind = 'payroll_run' AND m.target_id = r.id AND m.amount = %s AND m.status IN ('suggested', 'accepted')) AS used
                     FROM payroll_runs r WHERE r.entity_id = %s AND r.status IN ('paid', 'statutory_done') AND r.paid_on BETWEEN %s AND %s
                    ORDER BY abs(r.paid_on - %s) LIMIT 5""",
                (amt, amt, entity_id, t["txn_date"] - dt.timedelta(days=15), t["txn_date"] + dt.timedelta(days=15), t["txn_date"]))
    for r in cur.fetchall():
        if r["slips"] > r["used"]:
            return {"kind": "payroll_run", "id": r["id"], "conf": Decimal("0.85") if "salary" in ws else Decimal("0.75"), "rule": "payroll_net_pay"}
    return None


# ------------------------------------------------------------------ the pipeline
def suggest_classifications(cur, entity_id) -> int:
    """Propose the other half for each unmatched line that has no open suggestion. Nothing is posted."""
    parse_unparsed(cur, entity_id)
    cur.execute("SELECT * FROM bank_rules WHERE entity_id = %s AND is_active ORDER BY created_at DESC", (entity_id,))
    rules = cur.fetchall()
    cur.execute("SELECT EXISTS (SELECT 1 FROM payroll_runs WHERE entity_id = %s) AS used", (entity_id,))
    payroll_used = cur.fetchone()["used"]
    cur.execute("SELECT * FROM bank_transactions WHERE entity_id = %s AND match_status = 'unmatched' ORDER BY txn_date, id", (entity_id,))
    made = 0
    for t in cur.fetchall():
        ws = words(t["description"])
        cur.execute("SELECT target_kind, target_id FROM bank_matches WHERE bank_transaction_id = %s AND status = 'rejected'", (t["id"],))
        refused = {(r["target_kind"], r["target_id"]) for r in cur.fetchall()}
        pick = document_for(cur, entity_id, t, ws)
        if pick and (pick["kind"], pick["id"]) not in refused:
            _suggest(cur, t, pick["kind"], pick["id"], pick["conf"], pick["rule"])
            made += 1
            continue
        fits = sorted((r for r in rules if rule_matches(r, t, ws)), key=lambda r: (-specificity(r), -r["created_at"].timestamp()))     # the most specific rule, then the newest
        if fits:
            r = fits[0]
            if ("ledger_account", r["account_id"]) not in refused:
                _suggest(cur, t, "ledger_account", r["account_id"], CONF_BY_SPECIFICITY[specificity(r)], f"rule:{r['id']}")
                made += 1
            continue
        s = starter_for(t, ws)
        if s:
            key = ("salary_payable" if payroll_used else "salary_expense") if s["key"] == "salary" else s["key"]
            account = ledger.account_id(cur, entity_id, key)
            if ("ledger_account", account) not in refused:
                _suggest(cur, t, "ledger_account", account, Decimal(str(s["conf"])), f"starter:{s['id']}")
                made += 1
    return made


def _suggest(cur, t, kind, target_id, conf, rule) -> None:
    cur.execute("""INSERT INTO bank_matches (bank_transaction_id, target_kind, target_id, amount, confidence, rule) VALUES (%s, %s, %s, %s, %s, %s)""",
                (t["id"], kind, target_id, abs(t["amount"]), conf, rule))
    cur.execute("UPDATE bank_transactions SET match_status = 'suggested' WHERE id = %s", (t["id"],))


def explain(cur, kind: str, rule: str | None) -> str | None:
    """Why a suggestion was made, in words."""
    if not rule:
        return None
    head, _, rest = rule.partition(":")
    if head == "rule":
        cur.execute("SELECT * FROM bank_rules WHERE id = %s", (rest,))
        r = cur.fetchone()
        return f"A rule you made: {condition_text(r).lower()}." if r else "A rule you made."
    if head == "starter":
        s = next((x for x in STARTER if x["id"] == rest), None)
        return f"{s['what']}. From the starter list: check it." if s else None
    return {"vendor_payment_utr": "The UTR is the one on a payment released from here.", "vendor_payment_amount": "Same amount, within three days of a payment released from here.",
            "challan_ref": "The challan reference is in the narration.", "challan_amount": "Same amount, within three days of a recorded challan.",
            "payroll_net_pay": "The amount is an employee's net pay in a payroll run paid around then."}.get(head)


# ------------------------------------------------------------------ posting
def account_for_posting(cur, entity_id, bank_account_id, account_id):
    cur.execute("SELECT id, type::text, is_active FROM ledger_accounts WHERE id = %s AND entity_id = %s", (account_id, entity_id))
    a = cur.fetchone()
    if not a or not a["is_active"]:
        raise ApiError(400, "validation_failed", "Choose an account of this company", errors=[{"field": "account_id", "message": "unknown or inactive account"}])
    cur.execute("SELECT ledger_account_id FROM bank_accounts WHERE id = %s", (bank_account_id,))
    bank_ledger = cur.fetchone()["ledger_account_id"]
    if a["id"] == bank_ledger:
        raise ApiError(400, "validation_failed", "A bank line cannot be booked to its own bank account", errors=[{"field": "account_id", "message": "choose the other side"}])
    return bank_ledger


def post_line(cur, entity_id, t: dict, account_id, party_id=None, memo: str | None = None) -> uuid.UUID:
    """Book a bank line: the money out is a debit to the account and a credit to the bank; the money in is the reverse. The journal is
    posted in the caller's transaction, so a locked period or an unbalanced entry fails the whole request."""
    bank_ledger = account_for_posting(cur, entity_id, t["bank_account_id"], account_id)
    amt = abs(Decimal(str(t["amount"])))
    acct, bank = f"account:{account_id}", f"account:{bank_ledger}"
    if Decimal(str(t["amount"])) < 0:
        lines = [ledger.Line(acct, debit=amt, party_id=party_id, memo=memo), ledger.Line(bank, credit=amt)]
    else:
        lines = [ledger.Line(bank, debit=amt), ledger.Line(acct, credit=amt, party_id=party_id, memo=memo)]
    jid = ledger.post_journal(cur, entity_id, t["txn_date"], "bank", t["id"], f"Bank: {(t['description'] or '')[:150]}", lines)
    cur.execute("UPDATE bank_transactions SET journal_entry_id = %s, match_status = 'matched' WHERE id = %s", (jid, t["id"]))
    cur.execute("UPDATE bank_matches SET status = 'rejected' WHERE bank_transaction_id = %s AND status = 'suggested'", (t["id"],))
    cur.execute("""INSERT INTO bank_matches (bank_transaction_id, target_kind, target_id, amount, confidence, rule, status, decided_by, decided_at)
                   VALUES (%s, 'journal_entry', %s, %s, 1, 'classified', 'accepted', app.current_user_id(), now())""", (t["id"], jid, amt))
    audit(cur, entity_id, "bank_transaction.booked", "bank_transactions", t["id"], after={"journal": jid, "account": account_id})
    return jid


def apply_rule(cur, entity_id, rule: dict, limit: int = 500) -> int:
    """Book every other open line a rule matches. Lines with a suggestion of another kind (a receipt, a payment) are left for a person."""
    cur.execute("""SELECT t.* FROM bank_transactions t WHERE t.entity_id = %s AND t.match_status IN ('unmatched', 'suggested')
                      AND NOT EXISTS (SELECT 1 FROM bank_matches m WHERE m.bank_transaction_id = t.id AND m.status = 'suggested' AND m.target_kind <> 'ledger_account')
                    ORDER BY t.txn_date, t.id LIMIT %s""", (entity_id, limit))
    booked = 0
    for t in cur.fetchall():
        if rule_matches(rule, t, words(t["description"])):
            post_line(cur, entity_id, t, rule["account_id"], rule.get("party_id"), rule.get("memo"))
            cur.execute("UPDATE bank_rules SET hits = hits + 1, last_hit_on = %s WHERE id = %s", (t["txn_date"], rule["id"]))
            booked += 1
    return booked
