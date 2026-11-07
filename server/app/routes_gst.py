"""GST returns, 2A/2B matching, TDS, challans and advance tax; plus the obligation `prepare` action that drives them.

Portal calls never run in a request: each one is a job. The two adapters (`GSP`, `TAXPORTAL`) are sandboxes here;
the licensed GSP and TRACES/ITD integrations replace them in M6 without touching this module.
"""
from __future__ import annotations

import calendar
import datetime as dt
import json
import uuid
from decimal import Decimal
from typing import Protocol

from fastapi import APIRouter, Header, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from . import ledger, taxcalc
from .core import ApiError, Auth, Principal, audit, emit, idempotency_begin, idempotency_finish, jsonable, money, require, tx
from .routes_orders import js, offset_of, page

router = APIRouter()
E = "/entities/{entity_id}"
ZERO = Decimal(0)
RETURN_FOR_RULE = {"GST.GSTR1.MONTHLY": "GSTR1", "GST.GSTR3B.MONTHLY": "GSTR3B"}


# ================================================================== adapters (sandbox)
class GspAdapter(Protocol):
    def fetch_inward(self, gstin: str, period: str, source: str) -> list[dict]: ...
    def file_return(self, gstin: str, return_type: str, period: str, payload: dict) -> dict: ...


class SandboxGsp:
    inward: dict[tuple[str, str, str], list[dict]] = {}             # tests (and demos) preload what "the portal" reports

    def fetch_inward(self, gstin, period, source):
        return list(self.inward.get((gstin, period, source), []))

    def file_return(self, gstin, return_type, period, payload):
        return {"arn": "AA" + uuid.uuid5(uuid.NAMESPACE_URL, f"{gstin}|{return_type}|{period}").hex[:13].upper()}


class TaxPortalAdapter(Protocol):
    def pay_challan(self, payload: dict) -> dict: ...
    def fvu_validate(self, payload: dict) -> dict: ...


class SandboxTaxPortal:
    def pay_challan(self, payload):
        h = uuid.uuid5(uuid.NAMESPACE_URL, payload["challan_id"]).hex
        return {"challan_ref": "CIN" + h[:14].upper(), "bsr_code": "0510308"}

    def fvu_validate(self, payload):
        return {"token": "TKN" + uuid.uuid5(uuid.NAMESPACE_URL, payload["return_id"]).hex[:12].upper()}


GSP: GspAdapter = SandboxGsp()
TAXPORTAL: TaxPortalAdapter = SandboxTaxPortal()


# ================================================================== helpers
def _month_bounds(period: str) -> tuple[dt.date, dt.date]:
    y, m = int(period[:4]), int(period[5:7])
    return dt.date(y, m, 1), dt.date(y, m, calendar.monthrange(y, m)[1])


def _first_bank_ledger(cur, entity_id) -> str:
    cur.execute("SELECT ledger_account_id FROM bank_accounts WHERE entity_id = %s AND is_active ORDER BY (kind = 'current') DESC, id LIMIT 1", (entity_id,))
    b = cur.fetchone()
    if not b:
        raise ApiError(409, "conflict", "Add a bank account before paying taxes from the books")
    return "account:" + str(b["ledger_account_id"])


def _obligation_done(cur, entity_id, obligation_id, ack: str) -> None:
    if not obligation_id:
        return
    cur.execute("SELECT status::text FROM obligations WHERE id = %s", (obligation_id,))
    o = cur.fetchone()
    if o and o["status"] not in ("filed", "waived"):
        cur.execute("UPDATE obligations SET status = 'filed', filed_at = now(), acknowledgement = %s WHERE id = %s", (ack, obligation_id))
        cur.execute("""INSERT INTO obligation_events (obligation_id, kind, from_status, to_status, note)
                       VALUES (%s, 'filed', %s::obligation_status, 'filed', %s)""", (obligation_id, o["status"], f"Acknowledgement {ack}"))
        emit(cur, entity_id, "obligation.filed", "obligations", obligation_id, {"acknowledgement": ack})


# ================================================================== GST return computation
def outward_totals(cur, entity_id, reg_id, period) -> dict:
    """GSTR-1 tables from issued sales documents for one GSTIN and month. Credit notes count negatively."""
    start, end = _month_bounds(period)
    cur.execute("""SELECT id, doc_type::text, number, supply_type::text, buyer_gstin, taxable_total, cgst_total, sgst_total, igst_total, cess_total, status
                     FROM sales_documents WHERE entity_id = %s AND registration_id = %s AND doc_date BETWEEN %s AND %s
                      AND status IN ('issued', 'cancelled') AND doc_type <> 'proforma' ORDER BY number""", (entity_id, reg_id, start, end))
    docs = cur.fetchall()

    def bucket():
        return {"count": 0, "taxable": ZERO, "igst": ZERO, "cgst": ZERO, "sgst": ZERO, "cess": ZERO}

    t = {k: bucket() for k in ("b2b", "cdnr", "b2c", "exports")}
    totals, issued_ids = bucket(), []
    docs_table: dict[str, dict] = {}
    for d in docs:
        row = docs_table.setdefault(d["doc_type"], {"from": d["number"], "to": d["number"], "total": 0, "cancelled": 0})
        row["from"], row["to"] = min(row["from"], d["number"]), max(row["to"], d["number"])
        row["total"] += 1
        if d["status"] == "cancelled":
            row["cancelled"] += 1
            continue
        issued_ids.append(d["id"])
        sign = -1 if d["doc_type"] == "credit_note" else 1
        if d["supply_type"].startswith("export"):
            key = "exports"
        elif d["doc_type"] in ("credit_note", "debit_note") and d["buyer_gstin"]:
            key = "cdnr"
        elif d["buyer_gstin"]:
            key = "b2b"
        else:
            key = "b2c"
        for b in (t[key], totals):
            b["count"] += 1
            b["taxable"] += sign * d["taxable_total"]
            for h in ("igst", "cgst", "sgst", "cess"):
                b[h] += sign * d[h + "_total"]
    cur.execute("""SELECT l.hsn_sac, sum(CASE WHEN d.doc_type = 'credit_note' THEN -l.qty ELSE l.qty END) AS qty,
                          sum(CASE WHEN d.doc_type = 'credit_note' THEN -l.taxable_value ELSE l.taxable_value END) AS taxable,
                          sum(CASE WHEN d.doc_type = 'credit_note' THEN -l.igst_amount ELSE l.igst_amount END) AS igst,
                          sum(CASE WHEN d.doc_type = 'credit_note' THEN -l.cgst_amount ELSE l.cgst_amount END) AS cgst,
                          sum(CASE WHEN d.doc_type = 'credit_note' THEN -l.sgst_amount ELSE l.sgst_amount END) AS sgst
                     FROM sales_document_lines l JOIN sales_documents d ON d.id = l.document_id
                    WHERE d.id = ANY(%s) GROUP BY l.hsn_sac ORDER BY l.hsn_sac""", (issued_ids,))
    return {"tables": t, "totals": totals, "hsn": cur.fetchall(), "docs": docs_table, "issued_ids": issued_ids}


def compute_gstr1(cur, entity_id, reg_id, period) -> dict:
    o = outward_totals(cur, entity_id, reg_id, period)
    summary = {"b2b": o["tables"]["b2b"], "cdnr": o["tables"]["cdnr"], "b2c": o["tables"]["b2c"], "exports": o["tables"]["exports"],
               "hsn": o["hsn"], "docs": o["docs"], "totals": o["totals"]}
    t = o["totals"]
    return {"summary": summary, "output_tax": t["igst"] + t["cgst"] + t["sgst"] + t["cess"], "itc_claimed": None, "cash_paid": None,
            "issued_ids": o["issued_ids"]}


def compute_gstr3b(cur, entity_id, reg_id, period) -> dict:
    start, end = _month_bounds(period)
    out = outward_totals(cur, entity_id, reg_id, period)["totals"]
    cur.execute("""SELECT b.id, b.cgst_total, b.sgst_total, b.igst_total,
                          coalesce((SELECT m.status FROM itc_matches m WHERE m.purchase_bill_id = b.id ORDER BY m.run_at DESC LIMIT 1) = 'matched', false) AS matched
                     FROM purchase_bills b WHERE b.entity_id = %s AND b.registration_id = %s AND b.itc_eligibility = 'eligible'
                      AND b.status IN ('booked', 'approved', 'partially_paid', 'paid') AND b.bill_date <= %s
                      AND (b.itc_claim_period IS NULL OR b.itc_claim_period = %s)""", (entity_id, reg_id, end, period))
    claim, pending, bills = {h: ZERO for h in taxcalc.HEADS}, {h: ZERO for h in taxcalc.HEADS}, []
    for b in cur.fetchall():
        target = claim if b["matched"] else pending
        for h in taxcalc.HEADS:
            target[h] += b[h + "_total"]
        if b["matched"]:
            bills.append(b["id"])
    util = taxcalc.utilise({h: out[h] for h in taxcalc.HEADS}, claim)
    summary = {"3.1a": {"taxable": out["taxable"], **{h: out[h] for h in taxcalc.HEADS}, "cess": out["cess"]},
               "4a": claim, "4a_pending_in_2b": pending, "utilisation": util,
               "provisional": any(v for v in pending.values()) and "some ITC is not in GSTR-2B yet and is not claimed"}
    return {"summary": summary, "output_tax": sum((out[h] for h in taxcalc.HEADS), ZERO) + out["cess"], "itc_claimed": sum(claim.values(), ZERO),
            "cash_paid": sum(util["cash"].values(), ZERO), "claimed_bills": bills}


def upsert_return(cur, entity_id, reg_id, rtype: str, period: str, obligation_id=None) -> uuid.UUID:
    cur.execute("""INSERT INTO gst_returns (entity_id, registration_id, return_type, period, obligation_id) VALUES (%s, %s, %s::gst_return_type, %s, %s)
                   ON CONFLICT (registration_id, return_type, period) DO UPDATE SET obligation_id = coalesce(gst_returns.obligation_id, EXCLUDED.obligation_id)
                   RETURNING id, status""", (entity_id, reg_id, rtype, period, obligation_id))
    r = cur.fetchone()
    if r["status"] in ("submitted", "filed"):
        raise ApiError(409, "conflict", f"The {rtype} for {period} is already {r['status']}")
    return r["id"]


def compute_return(cur, entity_id, return_id) -> dict:
    cur.execute("SELECT * FROM gst_returns WHERE id = %s AND entity_id = %s FOR UPDATE", (return_id, entity_id))
    r = cur.fetchone()
    if not r:
        raise ApiError(404, "not_found", "Not found")
    if r["status"] in ("submitted", "filed"):
        raise ApiError(409, "conflict", f"Return is already {r['status']}")
    if r["return_type"] == "GSTR1":
        c = compute_gstr1(cur, entity_id, r["registration_id"], r["period"])
        cur.execute("UPDATE sales_documents SET gstr1_return_id = %s WHERE id = ANY(%s)", (return_id, c["issued_ids"]))
    elif r["return_type"] == "GSTR3B":
        c = compute_gstr3b(cur, entity_id, r["registration_id"], r["period"])
    else:
        raise ApiError(409, "conflict", f"{r['return_type']} cannot be computed yet")
    cur.execute("""UPDATE gst_returns SET summary = %s, output_tax = %s, itc_claimed = %s, cash_paid = %s, status = 'ready' WHERE id = %s""",
                (json.dumps(c["summary"], default=jsonable), c["output_tax"], c["itc_claimed"], c["cash_paid"], return_id))
    audit(cur, entity_id, "gst_return.computed", "gst_returns", return_id, after={"type": r["return_type"], "period": r["period"]}, actor_kind="engine")
    return c


def return_out(cur, entity_id, rid) -> dict:
    cur.execute("""SELECT id, registration_id, return_type::text, period, status, summary, output_tax, itc_claimed, cash_paid, arn, filed_at, obligation_id
                     FROM gst_returns WHERE id = %s AND entity_id = %s""", (rid, entity_id))
    r = cur.fetchone()
    if not r:
        raise ApiError(404, "not_found", "Not found")
    return js(r)


@router.get(E + "/gst-returns", tags=["GST & tax"])
def list_returns(entity_id: uuid.UUID, p: Principal = Auth, registration_id: uuid.UUID | None = None, return_type: str | None = None,
                 period: str | None = None, limit: int = Query(default=50, ge=1, le=200), cursor: str | None = None):
    off = offset_of(cursor)
    with tx(p.user_id) as cur:
        require(cur, entity_id, "gst:read")
        cur.execute("""SELECT id FROM gst_returns WHERE entity_id = %s AND (%s::uuid IS NULL OR registration_id = %s) AND (%s::text IS NULL OR return_type::text = %s)
                          AND (%s::text IS NULL OR period = %s) ORDER BY period DESC, return_type LIMIT %s OFFSET %s""",
                    (entity_id, registration_id, registration_id, return_type, return_type, period, period, limit + 1, off))
        return page([return_out(cur, entity_id, r["id"]) for r in cur.fetchall()], limit, off)


@router.get(E + "/gst-returns/{return_id}", tags=["GST & tax"])
def get_return(entity_id: uuid.UUID, return_id: uuid.UUID, p: Principal = Auth):
    with tx(p.user_id) as cur:
        require(cur, entity_id, "gst:read")
        return return_out(cur, entity_id, return_id)


def _queue(cur, entity_id, kind: str, subject: str, subject_id, payload: dict | None = None) -> dict:
    cur.execute("""INSERT INTO jobs (entity_id, kind, subject, subject_id, payload, created_by) VALUES (%s, %s, %s, %s, %s, app.current_user_id())
                   RETURNING id, kind, status, progress, subject, subject_id, result, created_at, finished_at""",
                (entity_id, kind, subject, subject_id, json.dumps(payload or {}, default=jsonable)))
    return js(cur.fetchone())


def _accepted(job: dict) -> JSONResponse:
    return JSONResponse(job, status_code=202, headers={"Location": f"/v1/jobs/{job['id']}"})


@router.post(E + "/gst-returns/{return_id}/compute", status_code=202, tags=["GST & tax"])
def compute_endpoint(entity_id: uuid.UUID, return_id: uuid.UUID, p: Principal = Auth):
    with tx(p.user_id) as cur:
        require(cur, entity_id, "gst:write")
        return_out(cur, entity_id, return_id)
        job = _queue(cur, entity_id, "gst.compute_return", "gst_returns", return_id)
    return _accepted(job)


class FileIn(BaseModel):
    signing: str = "evc_otp"
    dsc_certificate_id: uuid.UUID | None = None


@router.post(E + "/gst-returns/{return_id}/file", status_code=202, tags=["GST & tax"])
def file_return_endpoint(entity_id: uuid.UUID, return_id: uuid.UUID, body: FileIn, p: Principal = Auth,
                         idempotency_key: str | None = Header(default=None, alias="Idempotency-Key")):
    with tx(p.user_id) as cur:
        require(cur, entity_id, "gst:file")
        replay = idempotency_begin(cur, idempotency_key, "POST", f"/entities/{entity_id}/gst-returns/{return_id}/file", body.model_dump(mode="json"))
        if replay:
            return JSONResponse(replay["body"], status_code=replay["status"])
        r = return_out(cur, entity_id, return_id)
        if r["status"] == "filed":
            raise ApiError(409, "conflict", "Already filed")
        if r["status"] != "ready":
            raise ApiError(409, "conflict", "Compute the return before filing it")
        if r["obligation_id"]:
            cur.execute("SELECT status::text FROM obligations WHERE id = %s", (r["obligation_id"],))
            if cur.fetchone()["status"] != "ready_to_file":
                raise ApiError(409, "approval_required", "The obligation has not been approved for filing")
        cur.execute("UPDATE gst_returns SET status = 'submitted' WHERE id = %s", (return_id,))
        job = _queue(cur, entity_id, "gst.file_return", "gst_returns", return_id, body.model_dump(mode="json"))
        audit(cur, entity_id, "gst_return.submitted", "gst_returns", return_id)
        idempotency_finish(cur, idempotency_key, 202, job)
    return _accepted(job)


def handle_compute_return(cur, job: dict) -> dict:
    c = compute_return(cur, job["entity_id"], job["subject_id"])
    return {"output_tax": money(c["output_tax"])}


def handle_file_return(cur, job: dict) -> dict:
    entity_id = job["entity_id"]
    cur.execute("SELECT r.*, e.number AS gstin FROM gst_returns r JOIN entity_registrations e ON e.id = r.registration_id WHERE r.id = %s FOR UPDATE", (job["subject_id"],))
    r = cur.fetchone()
    if r["status"] != "submitted":
        raise RuntimeError(f"return is {r['status']}, expected submitted")
    res = GSP.file_return(r["gstin"], r["return_type"], r["period"], r["summary"])
    cur.execute("UPDATE gst_returns SET status = 'filed', arn = %s, filed_at = now() WHERE id = %s", (res["arn"], r["id"]))
    if r["return_type"] == "GSTR3B":
        s = r["summary"]
        out = {h: Decimal(s["3.1a"][h]) for h in taxcalc.HEADS}
        claim = {h: Decimal(s["4a"][h]) for h in taxcalc.HEADS}
        left = {h: Decimal(s["utilisation"]["left"][h]) for h in taxcalc.HEADS}
        cash = {h: Decimal(s["utilisation"]["cash"][h]) for h in taxcalc.HEADS}
        used = {h: claim[h] - left[h] for h in taxcalc.HEADS}
        lines = [ledger.Line(f"gst_output_{h}", debit=out[h]) for h in taxcalc.HEADS if out[h]]
        lines += [ledger.Line(f"gst_input_{h}", credit=used[h]) for h in taxcalc.HEADS if used[h]]
        cash_total = sum(cash.values(), ZERO)
        if cash_total:
            lines.append(ledger.Line(_first_bank_ledger(cur, entity_id), credit=cash_total))
        if lines and all(v >= 0 for v in out.values()) and ledger.balanced(lines):
            _, end = _month_bounds(r["period"])
            ledger.post_journal(cur, entity_id, end, "gst_setoff", r["id"], f"GSTR-3B {r['period']} set-off", lines)
        cur.execute("""UPDATE purchase_bills SET itc_claim_period = %s WHERE entity_id = %s AND registration_id = %s AND itc_eligibility = 'eligible'
                          AND itc_claim_period IS NULL AND id IN (SELECT purchase_bill_id FROM itc_matches WHERE status = 'matched' AND purchase_bill_id IS NOT NULL)
                          AND bill_date <= %s""", (r["period"], entity_id, r["registration_id"], _month_bounds(r["period"])[1]))
    _obligation_done(cur, entity_id, r["obligation_id"], res["arn"])
    audit(cur, entity_id, "gst_return.filed", "gst_returns", r["id"], after={"arn": res["arn"]}, actor_kind="integration")
    return {"arn": res["arn"]}


# ================================================================== 2A / 2B matching
def run_matching(cur, entity_id, period: str) -> dict:
    """Match booked bills of the month against supplier-reported invoices. Pairs a person already acted on are kept as they are."""
    start, end = _month_bounds(period)
    cur.execute("""DELETE FROM itc_matches WHERE entity_id = %s AND action_taken_at IS NULL AND (
                       purchase_bill_id IN (SELECT id FROM purchase_bills WHERE entity_id = %s AND bill_date BETWEEN %s AND %s)
                       OR inward_doc_id IN (SELECT id FROM gst_inward_docs WHERE entity_id = %s AND return_period = %s))""",
                (entity_id, entity_id, start, end, entity_id, period))
    cur.execute("""SELECT b.id, b.supplier_gstin, b.bill_no, (b.cgst_total + b.sgst_total + b.igst_total) AS tax, b.party_id, p.legal_name
                     FROM purchase_bills b JOIN parties p ON p.id = b.party_id
                    WHERE b.entity_id = %s AND b.bill_date BETWEEN %s AND %s AND b.status NOT IN ('draft', 'cancelled') AND b.itc_eligibility = 'eligible'""",
                (entity_id, start, end))
    books = [taxcalc.Book(str(r["id"]), r["supplier_gstin"] or "", r["bill_no"], r["tax"], str(r["party_id"]), r["legal_name"]) for r in cur.fetchall()]
    cur.execute("""SELECT id, supplier_gstin, doc_number, (igst + cgst + sgst) AS tax, supplier_name FROM gst_inward_docs
                    WHERE entity_id = %s AND return_period = %s AND doc_kind = 'invoice'""", (entity_id, period))
    portal = [taxcalc.Portal(str(r["id"]), r["supplier_gstin"], r["doc_number"], r["tax"], r["supplier_name"]) for r in cur.fetchall()]
    cur.execute("SELECT purchase_bill_id, inward_doc_id FROM itc_matches WHERE entity_id = %s AND action_taken_at IS NOT NULL", (entity_id,))
    done = {(str(r["purchase_bill_id"]), str(r["inward_doc_id"])) for r in cur.fetchall()}
    counts: dict[str, int] = {}
    for m in taxcalc.match_inward(books, portal):
        b, pdoc = m["book"], m["portal"]
        if (b.id if b else "None", pdoc.id if pdoc else "None") in done:
            continue
        cur.execute("""INSERT INTO itc_matches (entity_id, purchase_bill_id, inward_doc_id, status, tax_difference) VALUES (%s, %s, %s, %s, %s)""",
                    (entity_id, b.id if b else None, pdoc.id if pdoc else None, m["status"], m["tax_difference"]))
        counts[m["status"]] = counts.get(m["status"], 0) + 1
    return counts


class InwardSyncIn(BaseModel):
    registration_ids: list[uuid.UUID] = []
    period: str = Field(pattern=r"^[0-9]{4}-[0-9]{2}$")
    source: str = Field(pattern="^(2A|2B|IMS)$")


@router.post(E + "/gst/inward-sync", status_code=202, tags=["GST & tax"])
def inward_sync(entity_id: uuid.UUID, body: InwardSyncIn, p: Principal = Auth):
    with tx(p.user_id) as cur:
        require(cur, entity_id, "gst:write")
        job = _queue(cur, entity_id, "gst.inward_sync", "entities", entity_id, body.model_dump(mode="json"))
    return _accepted(job)


def handle_inward_sync(cur, job: dict) -> dict:
    entity_id, pl = job["entity_id"], job["payload"]
    cur.execute("""SELECT id, number FROM entity_registrations WHERE entity_id = %s AND kind = 'gstin' AND status = 'active'
                      AND (cardinality(%s::uuid[]) = 0 OR id = ANY(%s::uuid[]))""", (entity_id, pl.get("registration_ids", []), pl.get("registration_ids", [])))
    fetched = 0
    for reg in cur.fetchall():
        for d in GSP.fetch_inward(reg["number"], pl["period"], pl["source"]):
            cur.execute("""INSERT INTO gst_inward_docs (entity_id, registration_id, source, return_period, supplier_gstin, supplier_name, doc_kind, doc_number,
                                  doc_date, taxable_value, igst, cgst, sgst, supplier_filed_on)
                           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                           ON CONFLICT (registration_id, source, return_period, supplier_gstin, doc_kind, doc_number)
                           DO UPDATE SET taxable_value = EXCLUDED.taxable_value, igst = EXCLUDED.igst, cgst = EXCLUDED.cgst, sgst = EXCLUDED.sgst, fetched_at = now()""",
                        (entity_id, reg["id"], pl["source"], pl["period"], d["supplier_gstin"], d.get("supplier_name"), d.get("doc_kind", "invoice"),
                         d["doc_number"], d["doc_date"], d["taxable_value"], d.get("igst", 0), d.get("cgst", 0), d.get("sgst", 0), d.get("supplier_filed_on")))
            fetched += 1
    return {"fetched": fetched, "matches": run_matching(cur, entity_id, pl["period"])}


MATCH_SQL = """SELECT m.id, m.status, b.party_id, coalesce(b.supplier_gstin, d.supplier_gstin) AS supplier_gstin,
                      coalesce(d.supplier_name, p.legal_name) AS supplier_name, m.purchase_bill_id, m.inward_doc_id,
                      (b.cgst_total + b.sgst_total + b.igst_total) AS books_tax, (d.igst + d.cgst + d.sgst) AS portal_tax, m.tax_difference,
                      m.action, m.action_taken_at
                 FROM itc_matches m LEFT JOIN purchase_bills b ON b.id = m.purchase_bill_id LEFT JOIN gst_inward_docs d ON d.id = m.inward_doc_id
                 LEFT JOIN parties p ON p.id = b.party_id"""


def match_out(m: dict) -> dict:
    finding, suggested = taxcalc.FINDINGS[m["status"]]
    return js({**m, "finding": finding, "suggested_action": suggested})


@router.get(E + "/itc-matches", tags=["GST & tax"])
def list_matches(entity_id: uuid.UUID, p: Principal = Auth, status: str | None = None, registration_id: uuid.UUID | None = None,
                 period: str | None = None, open: bool | None = None, limit: int = Query(default=50, ge=1, le=200), cursor: str | None = None):
    off = offset_of(cursor)
    with tx(p.user_id) as cur:
        require(cur, entity_id, "gst:read")
        cur.execute(MATCH_SQL + """ WHERE m.entity_id = %s AND (%s::text IS NULL OR m.status = %s)
                          AND (%s::uuid IS NULL OR b.registration_id = %s OR d.registration_id = %s)
                          AND (%s::text IS NULL OR to_char(b.bill_date, 'YYYY-MM') = %s OR d.return_period = %s)
                          AND (%s::boolean IS NULL OR (m.action_taken_at IS NULL) = %s)
                        ORDER BY m.status, m.id LIMIT %s OFFSET %s""",
                    (entity_id, status, status, registration_id, registration_id, registration_id, period, period, period, open, open, limit + 1, off))
        return page([match_out(r) for r in cur.fetchall()], limit, off)


@router.get(E + "/itc-matches/summary", tags=["GST & tax"])
def match_summary(entity_id: uuid.UUID, p: Principal = Auth, period: str | None = None):
    with tx(p.user_id) as cur:
        require(cur, entity_id, "gst:read")
        cur.execute("""SELECT m.status, count(*) AS count,
                              coalesce(sum(CASE WHEN m.status = 'not_in_books' THEN d.igst + d.cgst + d.sgst ELSE b.cgst_total + b.sgst_total + b.igst_total END), 0) AS itc
                         FROM itc_matches m LEFT JOIN purchase_bills b ON b.id = m.purchase_bill_id LEFT JOIN gst_inward_docs d ON d.id = m.inward_doc_id
                        WHERE m.entity_id = %s AND (%s::text IS NULL OR to_char(b.bill_date, 'YYYY-MM') = %s OR d.return_period = %s)
                        GROUP BY m.status ORDER BY m.status""", (entity_id, period, period, period))
        return js(cur.fetchall())


class ResolveIn(BaseModel):
    action: str
    note: str | None = None


@router.post(E + "/itc-matches/{match_id}/resolve", tags=["GST & tax"])
def resolve_match(entity_id: uuid.UUID, match_id: uuid.UUID, body: ResolveIn, p: Principal = Auth):
    with tx(p.user_id) as cur:
        require(cur, entity_id, "gst:write")
        cur.execute("SELECT status, action_taken_at FROM itc_matches WHERE id = %s AND entity_id = %s FOR UPDATE", (match_id, entity_id))
        m = cur.fetchone()
        if not m:
            raise ApiError(404, "not_found", "Not found")
        if body.action not in taxcalc.ALLOWED_ACTIONS[m["status"]]:
            raise ApiError(400, "validation_failed", f"'{body.action}' does not apply to a {m['status']} match",
                           errors=[{"field": "action", "message": "allowed: " + ", ".join(sorted(taxcalc.ALLOWED_ACTIONS[m["status"]]))}])
        cur.execute("UPDATE itc_matches SET action = %s, action_taken_at = now(), action_by = app.current_user_id() WHERE id = %s", (body.action, match_id))
        audit(cur, entity_id, "itc_match.resolved", "itc_matches", match_id, after={"action": body.action, "note": body.note})
        cur.execute(MATCH_SQL + " WHERE m.id = %s", (match_id,))
        return match_out(cur.fetchone())


class RemindIn(BaseModel):
    party_ids: list[uuid.UUID] = []
    channels: list[str] = []


@router.post(E + "/itc-matches/remind-suppliers", status_code=202, tags=["GST & tax"])
def remind_suppliers(entity_id: uuid.UUID, body: RemindIn, p: Principal = Auth,
                     idempotency_key: str | None = Header(default=None, alias="Idempotency-Key")):
    with tx(p.user_id) as cur:
        require(cur, entity_id, "gst:write")
        replay = idempotency_begin(cur, idempotency_key, "POST", f"/entities/{entity_id}/itc-matches/remind-suppliers", body.model_dump(mode="json"))
        if replay:
            return JSONResponse(replay["body"], status_code=replay["status"])
        cur.execute("""SELECT b.party_id, p.legal_name, array_agg(m.id) AS ids, array_agg(b.bill_no) AS bills
                         FROM itc_matches m JOIN purchase_bills b ON b.id = m.purchase_bill_id JOIN parties p ON p.id = b.party_id
                        WHERE m.entity_id = %s AND m.status = 'not_in_portal' AND m.action_taken_at IS NULL
                          AND (cardinality(%s::uuid[]) = 0 OR b.party_id = ANY(%s::uuid[])) GROUP BY b.party_id, p.legal_name""",
                    (entity_id, body.party_ids, body.party_ids))
        groups = cur.fetchall()
        invoices = 0
        for g in groups:
            cur.execute("INSERT INTO tasks (entity_id, title, source) VALUES (%s, %s, 'user')",
                        (entity_id, f"Remind {g['legal_name']}: {len(g['ids'])} invoice(s) missing from GSTR-2A ({', '.join(g['bills'][:5])})"))
            cur.execute("UPDATE itc_matches SET action = 'remind_supplier', action_taken_at = now(), action_by = app.current_user_id() WHERE id = ANY(%s)", (g["ids"],))
            invoices += len(g["ids"])
        out = {"suppliers": len(groups), "invoices": invoices}
        audit(cur, entity_id, "itc_match.reminders", "itc_matches", None, after=out)
        idempotency_finish(cur, idempotency_key, 202, out)
    return JSONResponse(out, status_code=202)


@router.get(E + "/gst/ledger-balances", tags=["GST & tax"])
def ledger_balances(entity_id: uuid.UUID, p: Principal = Auth, registration_id: uuid.UUID | None = None):
    with tx(p.user_id) as cur:
        require(cur, entity_id, "gst:read")
        cur.execute("""SELECT DISTINCT ON (g.registration_id, g.ledger, g.head) g.registration_id, g.ledger, g.head, g.as_of, g.balance
                         FROM gst_ledger_balances g JOIN entity_registrations r ON r.id = g.registration_id
                        WHERE r.entity_id = %s AND (%s::uuid IS NULL OR g.registration_id = %s)
                        ORDER BY g.registration_id, g.ledger, g.head, g.as_of DESC""", (entity_id, registration_id, registration_id))
        return js(cur.fetchall())


# ================================================================== TDS, challans, advance tax
@router.get(E + "/tds-deductions", tags=["GST & tax"])
def list_deductions(entity_id: uuid.UUID, p: Principal = Auth, section: str | None = None, unpaid: bool | None = None, fy: str | None = None,
                    quarter: int | None = Query(default=None, ge=1, le=4), limit: int = Query(default=50, ge=1, le=200), cursor: str | None = None):
    off = offset_of(cursor)
    with tx(p.user_id) as cur:
        require(cur, entity_id, "tds:read")
        cur.execute("""SELECT id, deductee_party_id, deductee_employee_id, deductee_pan, section, deducted_on, base_amount, rate_pct, tds_amount, challan_id,
                              tds_return_id, certificate_issued_at FROM tds_deductions
                        WHERE entity_id = %s AND (%s::text IS NULL OR section = %s) AND (%s::boolean IS NULL OR (challan_id IS NULL) = %s)
                          AND (%s::text IS NULL OR fy = %s) AND (%s::int IS NULL OR ((extract(month FROM deducted_on)::int + 8) %% 12) / 3 + 1 = %s)
                        ORDER BY deducted_on DESC, id LIMIT %s OFFSET %s""",
                    (entity_id, section, section, unpaid, unpaid, fy, fy, quarter, quarter, limit + 1, off))
        return page(js(cur.fetchall()), limit, off)


class ChallanIn(BaseModel):
    kind: str = Field(pattern="^(itns281_tds|itns280_income_tax|gst_pmt06|epf_ecr|esic|professional_tax|other)$")
    registration_id: uuid.UUID | None = None
    period_label: str
    amount: Decimal = Field(gt=0)
    interest: Decimal = Field(default=ZERO, ge=0)
    penalty: Decimal = Field(default=ZERO, ge=0)
    obligation_id: uuid.UUID | None = None


CHALLAN_COLS = "id, kind, registration_id, period_label, amount, interest, penalty, paid_on, challan_ref, status, obligation_id"


@router.post(E + "/challans", status_code=201, tags=["GST & tax"])
def create_challan(entity_id: uuid.UUID, body: ChallanIn, p: Principal = Auth,
                   idempotency_key: str | None = Header(default=None, alias="Idempotency-Key")):
    with tx(p.user_id) as cur:
        require(cur, entity_id, "tds:write")
        replay = idempotency_begin(cur, idempotency_key, "POST", f"/entities/{entity_id}/challans", body.model_dump(mode="json"))
        if replay:
            return JSONResponse(replay["body"], status_code=replay["status"])
        cur.execute(f"""INSERT INTO tax_challans (entity_id, kind, registration_id, period_label, amount, interest, penalty, obligation_id, status)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 'scheduled') RETURNING {CHALLAN_COLS}""",
                    (entity_id, body.kind, body.registration_id, body.period_label, body.amount, body.interest, body.penalty, body.obligation_id))
        out = js(cur.fetchone())
        audit(cur, entity_id, "challan.created", "tax_challans", out["id"], after={"kind": body.kind, "amount": body.amount})
        idempotency_finish(cur, idempotency_key, 201, out)
    return JSONResponse(out, status_code=201)


@router.get(E + "/challans", tags=["GST & tax"])
def list_challans(entity_id: uuid.UUID, p: Principal = Auth, kind: str | None = None, status: str | None = None,
                  limit: int = Query(default=50, ge=1, le=200), cursor: str | None = None):
    off = offset_of(cursor)
    with tx(p.user_id) as cur:
        require(cur, entity_id, "tds:read")
        cur.execute(f"""SELECT {CHALLAN_COLS} FROM tax_challans WHERE entity_id = %s AND (%s::text IS NULL OR kind = %s) AND (%s::text IS NULL OR status = %s)
                         ORDER BY coalesce(paid_on, current_date) DESC, id LIMIT %s OFFSET %s""", (entity_id, kind, kind, status, status, limit + 1, off))
        return page(js(cur.fetchall()), limit, off)


@router.post(E + "/challans/{challan_id}/pay", status_code=202, tags=["GST & tax"])
def pay_challan(entity_id: uuid.UUID, challan_id: uuid.UUID, p: Principal = Auth,
                idempotency_key: str | None = Header(default=None, alias="Idempotency-Key")):
    with tx(p.user_id) as cur:
        require(cur, entity_id, "tds:pay")
        replay = idempotency_begin(cur, idempotency_key, "POST", f"/entities/{entity_id}/challans/{challan_id}/pay", {})
        if replay:
            return JSONResponse(replay["body"], status_code=replay["status"])
        cur.execute("SELECT status FROM tax_challans WHERE id = %s AND entity_id = %s FOR UPDATE", (challan_id, entity_id))
        c = cur.fetchone()
        if not c:
            raise ApiError(404, "not_found", "Not found")
        if c["status"] == "paid":
            raise ApiError(409, "conflict", "Already paid")
        _first_bank_ledger(cur, entity_id)
        job = _queue(cur, entity_id, "tax.pay_challan", "tax_challans", challan_id)
        audit(cur, entity_id, "challan.payment_requested", "tax_challans", challan_id)
        idempotency_finish(cur, idempotency_key, 202, job)
    return _accepted(job)


def handle_pay_challan(cur, job: dict) -> dict:
    entity_id = job["entity_id"]
    cur.execute("SELECT * FROM tax_challans WHERE id = %s FOR UPDATE", (job["subject_id"],))
    c = cur.fetchone()
    if c["status"] == "paid":
        return {"challan_ref": c["challan_ref"]}
    res = TAXPORTAL.pay_challan({"challan_id": str(c["id"]), "amount": str(c["amount"])})
    paid_on = dt.date.today()
    cur.execute("UPDATE tax_challans SET status = 'paid', paid_on = %s, challan_ref = %s, bsr_code = %s WHERE id = %s", (paid_on, res["challan_ref"], res["bsr_code"], c["id"]))
    total = c["amount"] + c["interest"] + c["penalty"]
    debit_key = {"itns281_tds": "tds_payable", "itns280_income_tax": "advance_tax", "epf_ecr": "pf_payable", "esic": "esi_payable",
                 "professional_tax": "pt_payable"}.get(c["kind"], "purchases")
    lines = [ledger.Line(debit_key, debit=c["amount"])]
    if c["interest"] + c["penalty"]:
        lines.append(ledger.Line("interest_penalty", debit=c["interest"] + c["penalty"]))
    lines.append(ledger.Line(_first_bank_ledger(cur, entity_id), credit=total))
    ledger.post_journal(cur, entity_id, paid_on, "tds" if c["kind"] == "itns281_tds" else "bank", c["id"], f"Challan {res['challan_ref']} ({c['period_label']})", lines)
    _obligation_done(cur, entity_id, c["obligation_id"], res["challan_ref"])
    audit(cur, entity_id, "challan.paid", "tax_challans", c["id"], after={"ref": res["challan_ref"]}, actor_kind="integration")
    return {"challan_ref": res["challan_ref"], "paid_on": paid_on.isoformat()}


@router.get(E + "/tds-returns", tags=["GST & tax"])
def list_tds_returns(entity_id: uuid.UUID, p: Principal = Auth, fy: str | None = None, limit: int = Query(default=50, ge=1, le=200), cursor: str | None = None):
    off = offset_of(cursor)
    with tx(p.user_id) as cur:
        require(cur, entity_id, "tds:read")
        cur.execute("""SELECT id, registration_id, form, fy, quarter, status, fvu_validated_at, token_no, filed_at FROM tds_returns
                        WHERE entity_id = %s AND (%s::text IS NULL OR fy = %s) ORDER BY fy DESC, quarter DESC, form LIMIT %s OFFSET %s""",
                    (entity_id, fy, fy, limit + 1, off))
        return page(js(cur.fetchall()), limit, off)


@router.post(E + "/tds-returns/{return_id}/validate", status_code=202, tags=["GST & tax"])
def validate_tds_return(entity_id: uuid.UUID, return_id: uuid.UUID, p: Principal = Auth):
    with tx(p.user_id) as cur:
        require(cur, entity_id, "tds:write")
        cur.execute("SELECT status FROM tds_returns WHERE id = %s AND entity_id = %s", (return_id, entity_id))
        r = cur.fetchone()
        if not r:
            raise ApiError(404, "not_found", "Not found")
        if r["status"] in ("filed", "revised"):
            raise ApiError(409, "conflict", f"Return is already {r['status']}")
        job = _queue(cur, entity_id, "tds.validate_return", "tds_returns", return_id)
    return _accepted(job)


def handle_validate_tds_return(cur, job: dict) -> dict:
    cur.execute("SELECT * FROM tds_returns WHERE id = %s FOR UPDATE", (job["subject_id"],))
    r = cur.fetchone()
    cur.execute("""SELECT d.id, d.section, d.tds_amount, c.status AS challan_status FROM tds_deductions d LEFT JOIN tax_challans c ON c.id = d.challan_id
                    WHERE d.tds_return_id = %s""", (r["id"],))
    rows = cur.fetchall()
    errors = []
    if not rows:
        errors.append("The return has no deductions")
    errors += [f"Deduction {x['id']} ({x['section']}) has no challan" for x in rows if not x["challan_status"]]
    errors += [f"Deduction {x['id']} ({x['section']}) is on a challan that is not paid" for x in rows if x["challan_status"] and x["challan_status"] != "paid"]
    if errors:
        return {"valid": False, "errors": errors[:20]}
    res = TAXPORTAL.fvu_validate({"return_id": str(r["id"])})
    cur.execute("UPDATE tds_returns SET fvu_validated_at = now(), token_no = %s, status = 'ready' WHERE id = %s", (res["token"], r["id"]))
    if r["obligation_id"]:
        cur.execute("UPDATE obligations SET status = 'ready_to_file' WHERE id = %s AND status IN ('upcoming', 'in_preparation', 'overdue')", (r["obligation_id"],))
    audit(cur, job["entity_id"], "tds_return.validated", "tds_returns", r["id"], after={"token": res["token"]}, actor_kind="integration")
    return {"valid": True, "token": res["token"], "deductions": len(rows)}


@router.get(E + "/advance-tax", tags=["GST & tax"])
def advance_tax(entity_id: uuid.UUID, p: Principal = Auth, fy: str | None = Query(default=None, pattern=r"^[0-9]{4}-[0-9]{2}$")):
    with tx(p.user_id) as cur:
        require(cur, entity_id, "tds:read")
        cur.execute("SELECT app.fy_of(current_date) AS fy")
        fy = fy or cur.fetchone()["fy"]
        cur.execute("SELECT value_num FROM entity_facts_current WHERE entity_id = %s AND fact_key = 'estimated_tax'", (entity_id,))
        f = cur.fetchone()
        plan = taxcalc.advance_tax_plan(f["value_num"] if f else ZERO, int(fy[:4]))
        cur.execute("""SELECT a.instalment_no, a.challan_id, coalesce(c.amount, 0) AS paid FROM advance_tax_instalments a
                         LEFT JOIN tax_challans c ON c.id = a.challan_id AND c.status = 'paid' WHERE a.entity_id = %s AND a.fy = %s""", (entity_id, fy))
        paid = {r["instalment_no"]: r for r in cur.fetchall()}
        return js([{"instalment_no": x["instalment_no"], "due_date": x["due_date"], "cumulative_pct": x["cumulative_pct"], "estimated_tax": x["cumulative_due"],
                    "paid": paid.get(x["instalment_no"], {}).get("paid", ZERO), "challan_id": paid.get(x["instalment_no"], {}).get("challan_id")} for x in plan])


@router.get(E + "/tds-receivables", tags=["GST & tax"])
def list_tds_receivables(entity_id: uuid.UUID, p: Principal = Auth, fy: str | None = None, unmatched: bool | None = None,
                         limit: int = Query(default=50, ge=1, le=200), cursor: str | None = None):
    off = offset_of(cursor)
    with tx(p.user_id) as cur:
        require(cur, entity_id, "tds:read")
        cur.execute("""SELECT id, party_id, sales_document_id, section, amount, fy, quarter, seen_in_26as_at, form16a_document_id FROM tds_receivables
                        WHERE entity_id = %s AND (%s::text IS NULL OR fy = %s) AND (%s::boolean IS NULL OR (seen_in_26as_at IS NULL) = %s)
                        ORDER BY fy DESC, quarter DESC, id LIMIT %s OFFSET %s""", (entity_id, fy, fy, unmatched, unmatched, limit + 1, off))
        return page(js(cur.fetchall()), limit, off)


# ================================================================== obligation.prepare
@router.post(E + "/obligations/{obligation_id}/prepare", status_code=202, tags=["Compliance"])
def prepare_obligation_endpoint(entity_id: uuid.UUID, obligation_id: uuid.UUID, p: Principal = Auth):
    with tx(p.user_id) as cur:
        require(cur, entity_id, "compliance:write")
        cur.execute("""SELECT o.status::text, r.code FROM obligations o JOIN compliance_rules r ON r.id = o.rule_id WHERE o.id = %s AND o.entity_id = %s""",
                    (obligation_id, entity_id))
        o = cur.fetchone()
        if not o:
            raise ApiError(404, "not_found", "Not found")
        if o["code"] not in PREPARERS:
            raise ApiError(409, "conflict", f"Preparing {o['code']} from the books is not available yet")
        if o["status"] in ("filed", "waived", "not_applicable"):
            raise ApiError(409, "conflict", f"Obligation is {o['status']}")
        job = _queue(cur, entity_id, "obligation.prepare", "obligations", obligation_id)
    return _accepted(job)


def _prep_gst(cur, o) -> dict:
    rid = upsert_return(cur, o["entity_id"], o["registration_id"], RETURN_FOR_RULE[o["code"]], o["period_start"].strftime("%Y-%m"), o["id"])
    compute_return(cur, o["entity_id"], rid)
    return {"gst_return_id": str(rid)}


def _prep_tds_deposit(cur, o) -> dict:
    cur.execute("SELECT id FROM tax_challans WHERE obligation_id = %s AND status IN ('draft', 'scheduled') FOR UPDATE", (o["id"],))
    existing = cur.fetchone()
    cur.execute("""SELECT coalesce(sum(tds_amount), 0) AS amt, count(*) AS n FROM tds_deductions
                    WHERE entity_id = %s AND deducted_on BETWEEN %s AND %s AND (challan_id IS NULL OR challan_id = %s)""",
                (o["entity_id"], o["period_start"], o["period_end"], existing["id"] if existing else None))
    s = cur.fetchone()
    if s["amt"] == 0:
        return {"challan_id": None, "note": "No TDS was deducted in this period"}
    interest = taxcalc.tds_late_interest(s["amt"], o["due_date"], dt.date.today())
    if existing:
        cur.execute("UPDATE tax_challans SET amount = %s, interest = %s WHERE id = %s", (s["amt"], interest, existing["id"]))
        cid = existing["id"]
    else:
        cur.execute("""INSERT INTO tax_challans (entity_id, kind, registration_id, period_label, amount, interest, obligation_id, status)
                       VALUES (%s, 'itns281_tds', %s, %s, %s, %s, %s, 'scheduled') RETURNING id""",
                    (o["entity_id"], o["registration_id"], o["period_label"], s["amt"], interest, o["id"]))
        cid = cur.fetchone()["id"]
    cur.execute("UPDATE tds_deductions SET challan_id = %s WHERE entity_id = %s AND challan_id IS NULL AND deducted_on BETWEEN %s AND %s",
                (cid, o["entity_id"], o["period_start"], o["period_end"]))
    return {"challan_id": str(cid), "amount": money(s["amt"]), "interest": money(interest), "deductions": s["n"]}


def _prep_tds_return(cur, o) -> dict:
    q = ((o["period_start"].month - 4) % 12) // 3 + 1
    made = []
    for form, cond in (("24Q", "section = '192'"), ("26Q", "section <> '192'")):
        cur.execute("""INSERT INTO tds_returns (entity_id, registration_id, form, fy, quarter, obligation_id) VALUES (%s, %s, %s, %s, %s, %s)
                       ON CONFLICT (registration_id, form, fy, quarter) DO UPDATE SET obligation_id = coalesce(tds_returns.obligation_id, EXCLUDED.obligation_id)
                       RETURNING id, status""", (o["entity_id"], o["registration_id"], form, o["fy"], q, o["id"]))
        r = cur.fetchone()
        if r["status"] in ("filed", "revised"):
            continue
        cur.execute(f"""UPDATE tds_deductions SET tds_return_id = %s WHERE entity_id = %s AND fy = %s AND {cond}
                           AND ((extract(month FROM deducted_on)::int + 8) %% 12) / 3 + 1 = %s""", (r["id"], o["entity_id"], o["fy"], q))
        made.append({"form": form, "id": str(r["id"]), "deductions": cur.rowcount})
    return {"returns": made}


def _prep_advance_tax(cur, o) -> dict:
    inst = {4: 1, 7: 2, 10: 3, 1: 4}.get(o["period_start"].month)
    cur.execute("SELECT value_num FROM entity_facts_current WHERE entity_id = %s AND fact_key = 'estimated_tax'", (o["entity_id"],))
    f = cur.fetchone()
    plan = {x["instalment_no"]: x for x in taxcalc.advance_tax_plan(f["value_num"] if f else ZERO, int(o["fy"][:4]))}
    due = plan[inst]["cumulative_due"]
    cur.execute("""SELECT coalesce(sum(c.amount), 0) AS paid FROM advance_tax_instalments a JOIN tax_challans c ON c.id = a.challan_id
                    WHERE a.entity_id = %s AND a.fy = %s AND a.instalment_no < %s""", (o["entity_id"], o["fy"], inst))
    amount = due - cur.fetchone()["paid"]
    cur.execute("""INSERT INTO advance_tax_instalments (entity_id, fy, instalment_no, due_date, cumulative_pct, estimated_tax) VALUES (%s, %s, %s, %s, %s, %s)
                   ON CONFLICT (entity_id, fy, instalment_no) DO UPDATE SET estimated_tax = EXCLUDED.estimated_tax RETURNING id, challan_id""",
                (o["entity_id"], o["fy"], inst, plan[inst]["due_date"], plan[inst]["cumulative_pct"], due))
    a = cur.fetchone()
    if amount <= 0:
        return {"challan_id": None, "note": "Nothing is due for this instalment"}
    if a["challan_id"]:
        cur.execute("UPDATE tax_challans SET amount = %s WHERE id = %s AND status IN ('draft', 'scheduled')", (amount, a["challan_id"]))
        return {"challan_id": str(a["challan_id"]), "amount": money(amount)}
    cur.execute("""INSERT INTO tax_challans (entity_id, kind, period_label, amount, obligation_id, status) VALUES (%s, 'itns280_income_tax', %s, %s, %s, 'scheduled')
                   RETURNING id""", (o["entity_id"], o["period_label"], amount, o["id"]))
    cid = cur.fetchone()["id"]
    cur.execute("UPDATE advance_tax_instalments SET challan_id = %s WHERE id = %s", (cid, a["id"]))
    return {"challan_id": str(cid), "amount": money(amount), "instalment_no": inst}


def _prep_payroll_statutory(kind: str, expr: str):
    """PF, ESI and professional tax challans come from the approved payroll run for the obligation's month."""
    def prep(cur, o) -> dict:
        cur.execute(f"""SELECT r.id, coalesce(sum({expr}), 0) AS amt FROM payroll_runs r JOIN payslips s ON s.run_id = r.id
                         WHERE r.entity_id = %s AND r.period_month = %s AND r.status IN ('approved', 'bank_file_sent', 'paid', 'statutory_done') GROUP BY r.id""",
                    (o["entity_id"], o["period_start"]))
        run = cur.fetchone()
        if not run or run["amt"] <= 0:
            return {"challan_id": None, "note": "No approved payroll with this contribution for the period"}
        cur.execute("SELECT id FROM tax_challans WHERE obligation_id = %s AND status IN ('draft', 'scheduled') FOR UPDATE", (o["id"],))
        ex = cur.fetchone()
        if ex:
            cur.execute("UPDATE tax_challans SET amount = %s WHERE id = %s", (run["amt"], ex["id"]))
            return {"challan_id": str(ex["id"]), "amount": money(run["amt"])}
        cur.execute("""INSERT INTO tax_challans (entity_id, kind, registration_id, period_label, amount, obligation_id, status) VALUES (%s, %s, %s, %s, %s, %s, 'scheduled')
                       RETURNING id""", (o["entity_id"], kind, o["registration_id"], o["period_label"], run["amt"], o["id"]))
        return {"challan_id": str(cur.fetchone()["id"]), "amount": money(run["amt"])}
    return prep


PREPARERS = {"GST.GSTR1.MONTHLY": _prep_gst, "GST.GSTR3B.MONTHLY": _prep_gst, "TDS.DEPOSIT.MONTHLY": _prep_tds_deposit,
             "TDS.RETURN.QUARTERLY": _prep_tds_return, "IT.ADVANCE_TAX": _prep_advance_tax,
             "EPF.ECR.MONTHLY": _prep_payroll_statutory("epf_ecr", "s.pf_employee + s.pf_employer_epf + s.pf_employer_eps"),
             "ESI.MONTHLY": _prep_payroll_statutory("esic", "s.esi_employee + s.esi_employer"),
             "PT.KA.MONTHLY": _prep_payroll_statutory("professional_tax", "s.professional_tax")}


def handle_prepare(cur, job: dict) -> dict:
    cur.execute("""SELECT o.*, r.code FROM obligations o JOIN compliance_rules r ON r.id = o.rule_id WHERE o.id = %s FOR UPDATE OF o""", (job["subject_id"],))
    o = cur.fetchone()
    out = PREPARERS[o["code"]](cur, o)
    if o["status"] in ("upcoming", "overdue"):
        cur.execute("UPDATE obligations SET status = 'in_preparation' WHERE id = %s", (o["id"],))
        cur.execute("""INSERT INTO obligation_events (obligation_id, kind, from_status, to_status, note) VALUES (%s, 'status_changed', %s::obligation_status, 'in_preparation', 'Draft prepared from the books')""",
                    (o["id"], o["status"]))
    audit(cur, o["entity_id"], "obligation.prepared", "obligations", o["id"], after=out, actor_kind="engine")
    return out
