"""Job worker. Claims queued jobs with SKIP LOCKED and runs portal adapters.

Run it as a separate process (`python -m app.worker`). Each adapter wraps one
external system behind a small interface so the GSP / IRP vendor can change
without touching the API.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import time
from typing import Protocol

from . import adapters, auth, secretbox, security, webhooks
from .core import ApiError, audit, emit, tx

WORKER_ID = os.environ.get("WORKER_ID", f"worker-{os.getpid()}")


IRP = adapters.proxy("irp")


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


def handle_dunning(cur, job: dict) -> dict:
    from . import receivables
    return receivables.run_dunning(cur, job["entity_id"])


def handle_recurring(cur, job: dict) -> dict:
    from . import receivables
    return receivables.run_recurring(cur, job["entity_id"])


def schedule_daily_jobs(cur) -> int:
    """Queue the daily dunning and recurring-invoice jobs once per entity per day."""
    n = 0
    for kind in ("collections.dunning", "collections.recurring", "purchases.msme_alerts", "portal.check_sessions"):
        cur.execute("""INSERT INTO jobs (entity_id, kind, subject, subject_id)
                       SELECT e.id, %s, 'entities', e.id FROM entities e
                        WHERE e.is_active AND NOT EXISTS (SELECT 1 FROM jobs j WHERE j.entity_id = e.id AND j.kind = %s
                                                           AND j.created_at > now() - interval '20 hours')""", (kind, kind))
        n += cur.rowcount
    cur.execute("""INSERT INTO jobs (entity_id, kind, subject, subject_id)
                   SELECT DISTINCT c.entity_id, 'aa.sync', 'entities', c.entity_id FROM aa_consents c
                    WHERE c.status = 'active' AND c.valid_until > now()
                      AND NOT EXISTS (SELECT 1 FROM jobs j WHERE j.entity_id = c.entity_id AND j.kind = 'aa.sync' AND j.created_at > now() - interval '20 hours')""")
    n += cur.rowcount
    return n


def handle_audit_verify(cur, job: dict) -> dict:
    """Walk the whole hash chain. A break raises an alert row, a critical log line and, when BIZ_ALERT_WEBHOOK is set, a POST."""
    cur.execute("SELECT app.audit_verify() AS broken")
    broken = cur.fetchone()["broken"]
    cur.execute("SELECT count(*) AS n FROM audit_log")
    n = cur.fetchone()["n"]
    if broken is None:
        cur.execute("UPDATE system_alerts SET resolved_at = now() WHERE kind = 'audit_chain_broken' AND resolved_at IS NULL")
        return {"intact": True, "checked": n}
    cur.execute("INSERT INTO system_alerts (kind, detail) SELECT 'audit_chain_broken', jsonb_build_object('first_broken_id', %s::bigint, 'checked', %s::bigint) "
                "WHERE NOT EXISTS (SELECT 1 FROM system_alerts WHERE kind = 'audit_chain_broken' AND resolved_at IS NULL AND (detail->>'first_broken_id')::bigint = %s)",
                (broken, n, broken))
    logging.getLogger("biz360x").critical("audit chain is broken at row %s", broken)
    send_alert({"kind": "audit_chain_broken", "first_broken_id": broken, "checked": n})
    return {"intact": False, "first_broken_id": broken, "checked": n}


def send_alert(payload: dict) -> None:
    """POST an alert to BIZ_ALERT_WEBHOOK (a pager or chat webhook). Failure to alert is logged, never raised: the alert row is the record."""
    url = os.environ.get("BIZ_ALERT_WEBHOOK")
    if not url:
        return
    try:
        webhooks.SENDER(url, {"Content-Type": "application/json"}, json.dumps({"text": "Biz360X: " + payload["kind"], **payload}).encode())
    except Exception as e:
        logging.getLogger("biz360x").error("could not send alert: %s", e)


def handle_housekeeping(cur, job: dict) -> dict:
    return {"purged": auth.purge_expired(cur)}


def schedule_system_jobs(cur) -> int:
    """Jobs that belong to no entity: the daily audit chain check and the purge of expired codes and tokens."""
    n = 0
    for kind in ("audit.verify", "system.housekeeping"):
        cur.execute("""INSERT INTO jobs (kind) SELECT %s::text WHERE NOT EXISTS (SELECT 1 FROM jobs j WHERE j.kind = %s AND j.created_at > now() - interval '20 hours')""", (kind, kind))
        n += cur.rowcount
    return n


def _m6_handlers() -> dict:
    from . import routes_portals as po
    return {"portal.renew_session": po.handle_renew_session, "portal.check_sessions": po.handle_check_sessions, "eway.generate": po.handle_eway_generate,
            "eway.update_vehicle": po.handle_eway_vehicle, "einvoice.cancel_irn": po.handle_cancel_document, "obligation.file": po.handle_file_obligation,
            "tds.fetch_26as": po.handle_fetch_26as, "aa.sync": po.handle_aa_sync, "registry.lookup": po.handle_registry_lookup}


def _m3_handlers() -> dict:
    from . import routes_gst as g, routes_purchases as pu
    return {"obligation.prepare": g.handle_prepare, "gst.compute_return": g.handle_compute_return, "gst.file_return": g.handle_file_return,
            "gst.inward_sync": g.handle_inward_sync, "tax.pay_challan": g.handle_pay_challan, "tds.validate_return": g.handle_validate_tds_return,
            "bank.release_payment": pu.handle_release_payment, "legal.extract_terms": __import__("app.routes_legal", fromlist=["x"]).handle_extract_terms, "legal.draft_notice": __import__("app.routes_legal", fromlist=["x"]).handle_draft_notice, "notice.draft_reply": __import__("app.routes_legal", fromlist=["x"]).handle_draft_reply, "payroll.bank_file": __import__("app.routes_payroll", fromlist=["x"]).handle_bank_file, "purchases.msme_alerts": pu.handle_msme_alerts}


def _m10_handlers() -> dict:
    from . import routes_msme as ms
    return {"msme.file_claim": ms.handle_file_claim}


HANDLERS = {"einvoice.generate_irn": handle_generate_irn, "compliance.run_engine": handle_run_engine,
            "collections.dunning": handle_dunning, "collections.recurring": handle_recurring, "audit.verify": handle_audit_verify,
            "system.housekeeping": handle_housekeeping, **_m3_handlers(), **_m6_handlers(), **_m10_handlers()}


FAILURE_HOOKS: dict = {}          # job kind -> fn(cur, job, exc): put the world back when a job ends without succeeding


def job_otp(job: dict) -> str | None:
    """The OTP a person submitted for a paused job (stored encrypted, wiped when the job ends)."""
    enc = (job["payload"] or {}).get("otp_enc")
    return secretbox.decrypt(enc.encode()) if enc else None


def _hook(cur, job: dict, exc: Exception) -> None:
    fn = FAILURE_HOOKS.get(job["kind"])
    if fn:
        try:
            with cur.connection.transaction():
                fn(cur, job, exc)
        except Exception:                                  # cleanup must never mask the original failure
            pass


def _fail(cur, job: dict, code: str, title: str, detail: str, status: int, exc: Exception, **extra) -> None:
    cur.execute("""UPDATE jobs SET status = 'failed', error = %s, locked_by = NULL, finished_at = now(), payload = payload - 'otp_enc' - 'otp_session' WHERE id = %s""",
                (json.dumps({"code": code, "title": title, "status": status, "detail": detail, **extra}), job["id"]))
    _hook(cur, job, exc)
    if job["entity_id"]:
        emit(cur, job["entity_id"], "job.failed", "jobs", job["id"], {"kind": job["kind"], "code": code})


def _irn_failed(cur, job: dict, exc: Exception) -> None:
    cur.execute("UPDATE sales_documents SET irn_status = 'failed' WHERE id = %s AND irn_status = 'pending'", (job["subject_id"],))


def _install_hooks() -> None:
    from . import routes_gst as g, routes_portals as po
    FAILURE_HOOKS.update({"gst.file_return": g.hook_file_return, "einvoice.generate_irn": _irn_failed, "portal.renew_session": po.hook_renew_session,
                          "registry.lookup": po.hook_registry_lookup})


def run_once() -> bool:
    """Process at most one job. Returns False when the queue is empty."""
    if not FAILURE_HOOKS:
        _install_hooks()
    with tx(None, role="biz_worker") as cur:
        cur.execute("SELECT * FROM app.claim_job(%s, %s)", (WORKER_ID, list(HANDLERS)))   # not (f()).* - that calls f once per column
        job = cur.fetchone()
        if not job or job["id"] is None:
            return False
        try:
            with cur.connection.transaction():            # savepoint: a failing adapter doesn't lose the claim
                result = HANDLERS[job["kind"]](cur, job)
            cur.execute("""UPDATE jobs SET status = 'succeeded', result = %s, progress = 1, finished_at = now(), locked_by = NULL,
                                  payload = payload - 'otp_enc' - 'otp_session' WHERE id = %s""", (json.dumps(result), job["id"]))
            if job["entity_id"]:
                emit(cur, job["entity_id"], "job.succeeded", "jobs", job["id"], {"kind": job["kind"]})
        except adapters.OtpRequired as o:                  # not a failure: wait for a person, then resume with the OTP
            cur.execute("""UPDATE jobs SET status = 'waiting_for_otp', progress = 0.5, locked_by = NULL, attempts = greatest(attempts - 1, 0),
                                  payload = (payload - 'otp_enc') || jsonb_build_object('otp_session', %s::text, 'otp_channel', %s::text, 'otp_message', %s::text)
                            WHERE id = %s""", (o.session_ref, o.channel, o.message, job["id"]))
        except adapters.PortalRejected as e:               # the portal said no: retrying the same request cannot help
            _fail(cur, job, "portal_rejected", "The portal rejected the request", str(e), 422, e, portal_code=e.code)
        except ApiError as e:                              # a business rule refused it (wrong state, missing data)
            _fail(cur, job, e.code, e.title, e.detail or e.title, e.status, e)
        except Exception as e:                             # transient: retry with backoff, give up after 5 attempts
            if job["attempts"] >= 5:
                _fail(cur, job, getattr(e, "code", "portal_error"), "Portal call failed", str(e), 502, e)
            else:
                cur.execute("""UPDATE jobs SET status = 'queued', error = %s, run_after = now() + (interval '30 seconds' * power(2, attempts)), locked_by = NULL WHERE id = %s""",
                            (json.dumps({"code": getattr(e, "code", "portal_error"), "title": "Portal call failed", "status": 502, "detail": str(e)}), job["id"]))
    return True


def main():
    security.check_environment()
    last_schedule = 0.0
    while True:
        if time.monotonic() - last_schedule > 3600:
            with tx(None, role="biz_worker") as cur:
                schedule_engine_runs(cur)
                schedule_daily_jobs(cur)
                schedule_system_jobs(cur)
            last_schedule = time.monotonic()
        with tx(None, role="biz_worker") as cur:
            webhooks.relay(cur)
        if not run_once():
            time.sleep(1)


if __name__ == "__main__":
    main()
