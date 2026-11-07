"use client";
import { useState } from "react";
import { api, newIdempotencyKey, unwrap } from "@/lib/api";
import { formatInr } from "@/lib/format";
import { ITC_LABEL } from "@/lib/finance";
import { jobStage } from "@/lib/integrations";
import { ACTION_LABEL, ItcMatch, SIGNING, SOURCES, canCompute, canFile, fileBody, isOpen, lastMonth, matchActions, periodOk, remindLabel, remindable, resolveBody, syncBody, taxNote } from "@/lib/gst";
import { ErrorNote, Loading, Pill } from "./ui";
import { JobFollow } from "./JobFollow";

interface Accepted { id: string }

/** Compute or file one return. Both are background jobs; filing may stop for a one-time code. */
export function ReturnActions({ entityId, returnId, status, onChanged }: { entityId: string; returnId: string; status: string; onChanged: () => void }) {
  const [job, setJob] = useState<{ id: string; label: string } | null>(null);
  const [signing, setSigning] = useState("evc_otp");
  const [filing, setFiling] = useState(false);
  const [outcome, setOutcome] = useState<{ label: string; tone: "ok" | "due" | "wait" | "prep" } | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);

  async function start(kind: "compute" | "file") {
    setBusy(true); setError(null); setOutcome(null);
    try {
      const j = kind === "compute"
        ? unwrap(await api.POST("/entities/{entityId}/gst-returns/{id}/compute", { params: { path: { entityId, id: returnId } } }))
        : unwrap(await api.POST("/entities/{entityId}/gst-returns/{id}/file", { params: { path: { entityId, id: returnId }, header: { "Idempotency-Key": newIdempotencyKey() } }, body: fileBody(signing) as never }));
      setJob({ id: (j as unknown as Accepted).id, label: kind === "compute" ? "Recomputed" : "Filed" });
      setFiling(false);
    } catch (e) { setError(e); }
    setBusy(false);
  }

  if (job) return <JobFollow jobId={job.id} doneLabel={job.label} onFinished={(j) => { setOutcome(jobStage(j, job.label)); setJob(null); onChanged(); }} />;
  return (
    <span style={{ display: "flex", flexDirection: "column", gap: 6, alignItems: "flex-start" }}>
      <span style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
        {canCompute(status) && <button type="button" className="btn" disabled={busy} onClick={() => void start("compute")}>Recompute</button>}
        {canFile(status) && <button type="button" className="btn primary" disabled={busy} aria-expanded={filing} onClick={() => setFiling(!filing)}>File</button>}
      </span>
      {filing && (
        <span style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center" }}>
          <label className="sr" htmlFor={`sg-${returnId}`}>Sign with</label>
          <select id={`sg-${returnId}`} className="input" style={{ height: 36, fontSize: 14 }} value={signing} onChange={(e) => setSigning(e.target.value)}>{SIGNING.map(([k, l]) => <option key={k} value={k}>{l}</option>)}</select>
          <button type="button" className="btn primary" disabled={busy} onClick={() => void start("file")}>{busy ? "Starting…" : "Confirm filing"}</button>
        </span>
      )}
      {outcome && <Pill tone={outcome.tone}>{outcome.label}</Pill>}
      <ErrorNote error={error} />
    </span>
  );
}

/** Purchase register against the supplier-reported invoices: fetch 2B, then settle each difference. */
export function MatchesPanel({ entityId, registrationId, matches, loading, error, onChanged }: { entityId: string; registrationId: string; matches: ItcMatch[]; loading: boolean; error: unknown; onChanged: () => void }) {
  const [period, setPeriod] = useState(lastMonth());
  const [source, setSource] = useState("2B");
  const [sync, setSync] = useState<string | null>(null);
  const [syncError, setSyncError] = useState<unknown>(null);
  const [reminded, setReminded] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [actError, setActError] = useState<unknown>(null);

  const open = matches.filter(isOpen);
  const done = matches.filter((m) => !isOpen(m));
  const n = remindable(matches);

  async function startSync() {
    setSyncError(null); setBusy(true);
    try { const j = unwrap(await api.POST("/entities/{entityId}/gst/inward-sync", { params: { path: { entityId } }, body: syncBody(period, source, registrationId) as never })) as unknown as Accepted; setSync(j.id); }
    catch (e) { setSyncError(e); }
    setBusy(false);
  }
  async function remind() {
    setActError(null); setBusy(true);
    try {
      const r = unwrap(await api.POST("/entities/{entityId}/itc-matches/remind-suppliers", { params: { path: { entityId }, header: { "Idempotency-Key": newIdempotencyKey() } }, body: {} as never })) as unknown as { suppliers: number; invoices: number };
      setReminded(`Reminders added to your tasks for ${r.suppliers} ${r.suppliers === 1 ? "supplier" : "suppliers"} (${r.invoices} ${r.invoices === 1 ? "invoice" : "invoices"}).`);
      onChanged();
    } catch (e) { setActError(e); }
    setBusy(false);
  }
  async function resolve(id: string, action: string) {
    setActError(null); setBusy(true);
    try { unwrap(await api.POST("/entities/{entityId}/itc-matches/{id}/resolve", { params: { path: { entityId, id } }, body: resolveBody(action, "") as never })); onChanged(); }
    catch (e) { setActError(e); }
    setBusy(false);
  }

  return (
    <section className="card card-pad" style={{ display: "flex", flexDirection: "column", gap: 12 }}>
      <div style={{ display: "flex", flexWrap: "wrap", gap: 12, justifyContent: "space-between", alignItems: "flex-end" }}>
        <h2 style={{ margin: 0 }}>Differences to settle</h2>
        <button type="button" className="btn" disabled={busy || n === 0} onClick={() => void remind()}>{remindLabel(n)}</button>
      </div>
      <form style={{ display: "flex", gap: 10, flexWrap: "wrap", alignItems: "flex-end" }} onSubmit={(e) => { e.preventDefault(); if (periodOk(period) && !busy && !sync) void startSync(); }}>
        <div className="field"><label htmlFor="sy-period">Month</label><input id="sy-period" className="input mono" style={{ width: 130, height: 40 }} placeholder="2026-10" value={period} onChange={(e) => setPeriod(e.target.value)} /></div>
        <div className="field"><label htmlFor="sy-src">Fetch</label><select id="sy-src" className="input" style={{ height: 40 }} value={source} onChange={(e) => setSource(e.target.value)}>{SOURCES.map(([k, l]) => <option key={k} value={k}>{l}</option>)}</select></div>
        <button type="submit" className="btn" disabled={!periodOk(period) || busy || !!sync}>Fetch and match</button>
        {sync && <JobFollow jobId={sync} doneLabel="Matched" onFinished={() => { setSync(null); onChanged(); }} />}
      </form>
      <ErrorNote error={syncError ?? error ?? actError} />
      {reminded && <span className="t13" role="status">{reminded}</span>}
      {loading && matches.length === 0 && <Loading what="the differences" />}
      {!loading && open.length === 0 && <span className="t13">Nothing waits on you. Fetch a month to match the purchase register again.</span>}
      {open.map((m) => (
        <div key={m.id} style={{ borderTop: "1px solid var(--line-2)", paddingTop: 10, display: "flex", flexDirection: "column", gap: 6 }}>
          <div style={{ display: "flex", justifyContent: "space-between", gap: 12, flexWrap: "wrap" }}>
            <span style={{ fontSize: 14 }}><b>{m.supplier_name || m.supplier_gstin}</b><span className="mono t13" style={{ display: "block" }}>{m.supplier_gstin}</span></span>
            <span style={{ textAlign: "right" }}><Pill tone={m.status === "matched" ? "ok" : "due"}>{ITC_LABEL[m.status] ?? m.status}</Pill><span className="t13" style={{ display: "block" }}>{taxNote(m, (v) => formatInr(v))}</span></span>
          </div>
          <span className="t13">{m.finding}</span>
          <span style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
            {matchActions(m.status).map(([k, l]) => (
              <button key={k} type="button" className={k === m.suggested_action ? "btn primary" : "btn"} disabled={busy} onClick={() => void resolve(m.id, k)} aria-label={`${l}: ${m.supplier_name || m.supplier_gstin}`}>{l}</button>
            ))}
          </span>
        </div>
      ))}
      {done.length > 0 && <span className="t13">{done.length} already settled: {Array.from(new Set(done.map((m) => ACTION_LABEL[m.action ?? ""] ?? m.action))).join(", ")}.</span>}
    </section>
  );
}
