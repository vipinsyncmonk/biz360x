"""Job worker. Claims queued jobs with SKIP LOCKED and runs portal adapters.

Run it as a separate process (`python -m app.worker`). Each adapter wraps one
external system behind a small interface so the GSP / IRP vendor can change
without touching the API.
"""
from __future__ import annotations

import hashlib
import json
import os
import time
from typing import Protocol

from .core import audit, emit, tx

WORKER_ID = os.environ.get("WORKER_ID", f"worker-{os.getpid()}")


class IrpAdapter(Protocol):
    def generate_irn(self, payload: dict) -> dict: ...


class SandboxIrp:
    """Stand-in for the e-invoice registration portal (via your GSP/ASP)."""

    def generate_irn(self, payload: dict) -> dict:
        key = f"{payload['seller_gstin']}|{payload['fy']}|{payload['doc_type']}|{payload['number']}"
        return {"irn": hashlib.sha256(key.encode()).hexdigest(),
                "ack_no": str(int(hashlib.md5(key.encode()).hexdigest()[:12], 16))[:15],
                "signed_qr": "sandbox-signed-qr:" + hashlib.sha1(key.encode()).hexdigest()}


IRP: IrpAdapter = SandboxIrp()


def handle_generate_irn(cur, job: dict) -> dict:
    cur.execute("""SELECT d.id, d.entity_id, d.number, d.fy, d.doc_type::text, d.buyer_gstin, d.grand_total, r.number AS seller_gstin
                     FROM sales_documents d JOIN entity_registrations r ON r.id = d.registration_id
                    WHERE d.id = %s FOR UPDATE OF d""", (job["subject_id"],))
    d = cur.fetchone()
    out = IRP.generate_irn({"seller_gstin": d["seller_gstin"], "fy": d["fy"], "doc_type": d["doc_type"], "number": d["number"],
                            "buyer_gstin": d["buyer_gstin"], "total": str(d["grand_total"])})
    cur.execute("""UPDATE sales_documents SET irn = %s, irn_ack_no = %s, irn_ack_at = now(), irn_signed_qr = %s,
                          irn_status = 'generated' WHERE id = %s""", (out["irn"], out["ack_no"], out["signed_qr"], d["id"]))
    audit(cur, d["entity_id"], "sales_document.irn_generated", "sales_documents", d["id"], after={"ack_no": out["ack_no"]},
          actor_kind="integration")
    emit(cur, d["entity_id"], "sales_document.irn_generated", "sales_documents", d["id"], {"ack_no": out["ack_no"]})
    return {"irn": out["irn"], "ack_no": out["ack_no"], "number": d["number"]}


def handle_run_engine(cur, job: dict) -> dict:
    from . import engine
    run = engine.run_engine(cur, job["entity_id"], (job["payload"] or {}).get("trigger", "schedule"))
    return {"engine_run_id": str(run["id"]), "obligations_created": run["obligations_created"],
            "obligations_changed": run["obligations_changed"]}


def schedule_engine_runs(cur) -> int:
    """Queue a daily engine run for every active entity that has not had one in 24 hours
    (dates roll over, so obligations go overdue and late fees grow without any fact changing)."""
    cur.execute("""SELECT e.id FROM entities e
                    WHERE e.is_active AND NOT EXISTS (SELECT 1 FROM engine_runs r WHERE r.entity_id = e.id AND r.started_at > now() - interval '24 hours')""")
    ids = [r["id"] for r in cur.fetchall()]
    for i in ids:
        cur.execute("SELECT app.enqueue_engine_run(%s, 'schedule')", (i,))
    return len(ids)


HANDLERS = {"einvoice.generate_irn": handle_generate_irn, "compliance.run_engine": handle_run_engine}


def run_once() -> bool:
    """Process at most one job. Returns False when the queue is empty."""
    with tx(None, role="biz_worker") as cur:
        cur.execute("SELECT * FROM app.claim_job(%s, %s)", (WORKER_ID, list(HANDLERS)))   # not (f()).* — that calls f once per column
        job = cur.fetchone()
        if not job or job["id"] is None:
            return False
        try:
            with cur.connection.transaction():            # savepoint: a failing adapter doesn't lose the claim
                result = HANDLERS[job["kind"]](cur, job)
            cur.execute("UPDATE jobs SET status = 'succeeded', result = %s, progress = 1, finished_at = now(), locked_by = NULL WHERE id = %s",
                        (json.dumps(result), job["id"]))
            emit(cur, job["entity_id"], "job.succeeded", "jobs", job["id"], {"kind": job["kind"]})
        except Exception as e:                             # retry with backoff, give up after 5 attempts
            failed = job["attempts"] >= 5
            cur.execute("""UPDATE jobs SET status = %s, error = %s, run_after = now() + (interval '30 seconds' * power(2, attempts)),
                                  locked_by = NULL, finished_at = CASE WHEN %s THEN now() END WHERE id = %s""",
                        ("failed" if failed else "queued",
                         json.dumps({"code": "portal_rejected", "title": "Portal call failed", "status": 502, "detail": str(e)}),
                         failed, job["id"]))
    return True


def main():
    last_schedule = 0.0
    while True:
        if time.monotonic() - last_schedule > 3600:
            with tx(None, role="biz_worker") as cur:
                schedule_engine_runs(cur)
            last_schedule = time.monotonic()
        if not run_once():
            time.sleep(1)


if __name__ == "__main__":
    main()
