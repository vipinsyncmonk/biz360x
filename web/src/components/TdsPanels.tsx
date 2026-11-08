"use client";
import { useEffect, useState } from "react";
import { api, newIdempotencyKey, unwrap } from "@/lib/api";
import { formatInr, shortDate } from "@/lib/format";
import { DepositObl, depositNote, depositOptions, CHALLAN_KINDS, CHALLAN_LABEL, CHALLAN_STATUS, Challan, ChallanForm, RETURN_STATE, TdsReturn, blankChallan, canPay, canValidate, challanBody, challanProblem, quarterLabel, validationNote } from "@/lib/tds";
import { ErrorNote, Pill } from "./ui";
import { JobFollow } from "./JobFollow";

interface Accepted { id: string }

/** Record a challan the company is about to pay. Paying it is a separate, deliberate step. */
export function NewChallan({ entityId, onDone, onCancel }: { entityId: string; onDone: () => void; onCancel: () => void }) {
  const [f, setF] = useState<ChallanForm>(blankChallan());
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const problem = challanProblem(f);
  async function save() {
    setBusy(true); setError(null);
    try { unwrap(await api.POST("/entities/{entityId}/challans", { params: { path: { entityId }, header: { "Idempotency-Key": newIdempotencyKey() } }, body: challanBody(f) as never })); onDone(); }
    catch (e) { setError(e); setBusy(false); }
  }
  return (
    <form className="card card-pad" style={{ display: "flex", flexDirection: "column", gap: 12 }} onSubmit={(e) => { e.preventDefault(); if (!problem && !busy) void save(); }}>
      <h2 style={{ margin: 0 }}>New challan</h2>
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(170px, 1fr))", gap: 12 }}>
        <div className="field"><label htmlFor="ch-kind">For</label><select id="ch-kind" className="input" value={f.kind} onChange={(e) => setF({ ...f, kind: e.target.value })}>{CHALLAN_KINDS.map(([k, l]) => <option key={k} value={k}>{l}</option>)}</select></div>
        <div className="field"><label htmlFor="ch-period">Period</label><input id="ch-period" className="input" placeholder="Oct 2026" value={f.period} onChange={(e) => setF({ ...f, period: e.target.value })} /></div>
        <div className="field"><label htmlFor="ch-amt">Tax (₹)</label><input id="ch-amt" className="input mono" inputMode="decimal" value={f.amount} onChange={(e) => setF({ ...f, amount: e.target.value })} /></div>
        <div className="field"><label htmlFor="ch-int">Interest (₹, optional)</label><input id="ch-int" className="input mono" inputMode="decimal" value={f.interest} onChange={(e) => setF({ ...f, interest: e.target.value })} /></div>
        <div className="field"><label htmlFor="ch-pen">Penalty (₹, optional)</label><input id="ch-pen" className="input mono" inputMode="decimal" value={f.penalty} onChange={(e) => setF({ ...f, penalty: e.target.value })} /></div>
      </div>
      {problem && (f.period || f.amount) && <span className="t13" role="alert" style={{ color: "var(--amber)" }}>{problem}</span>}
      <ErrorNote error={error} />
      <span style={{ display: "flex", gap: 10 }}><button type="submit" className="btn primary" disabled={busy || !!problem}>{busy ? "Saving…" : "Save challan"}</button><button type="button" className="btn" onClick={onCancel}>Cancel</button></span>
    </form>
  );
}

/** Pay one challan through the portal. It is a background job; the page reloads when it ends. */
export function PayChallan({ entityId, challan, onChanged }: { entityId: string; challan: Challan; onChanged: () => void }) {
  const [job, setJob] = useState<string | null>(null);
  const [asking, setAsking] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  async function pay() {
    setBusy(true); setError(null);
    try { const j = unwrap(await api.POST("/entities/{entityId}/challans/{id}/pay", { params: { path: { entityId, id: challan.id }, header: { "Idempotency-Key": newIdempotencyKey() } } })) as unknown as Accepted; setJob(j.id); setAsking(false); }
    catch (e) { setError(e); }
    setBusy(false);
  }
  if (job) return <JobFollow jobId={job} doneLabel="Paid" onFinished={() => { setJob(null); onChanged(); }} />;
  if (!canPay(challan.status)) return <span className="t13">{challan.challan_ref ? `Ref ${challan.challan_ref}` : ""}</span>;
  return (
    <span style={{ display: "flex", flexDirection: "column", gap: 6, alignItems: "flex-start" }}>
      {asking ? (
        <span style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center" }}>
          <span className="t13">Pay {formatInr(challan.amount)} from your bank?</span>
          <button type="button" className="btn primary" disabled={busy} onClick={() => void pay()}>{busy ? "Paying…" : "Yes, pay"}</button>
          <button type="button" className="btn" onClick={() => setAsking(false)}>Not now</button>
        </span>
      ) : <button type="button" className="btn" onClick={() => setAsking(true)}>Pay</button>}
      <ErrorNote error={error} />
    </span>
  );
}

/** Generate and validate one quarterly return. The job reports what blocks it, if anything. */
export function ValidateReturn({ entityId, ret, onChanged }: { entityId: string; ret: TdsReturn; onChanged: () => void }) {
  const [job, setJob] = useState<string | null>(null);
  const [note, setNote] = useState<{ ok: boolean; lines: string[] } | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  async function start() {
    setBusy(true); setError(null); setNote(null);
    try { const j = unwrap(await api.POST("/entities/{entityId}/tds-returns/{id}/validate", { params: { path: { entityId, id: ret.id } } })) as unknown as Accepted; setJob(j.id); }
    catch (e) { setError(e); }
    setBusy(false);
  }
  if (job) return <JobFollow jobId={job} doneLabel="Checked" onFinished={(j) => { setNote(validationNote(j.result)); setJob(null); onChanged(); }} />;
  return (
    <span style={{ display: "flex", flexDirection: "column", gap: 6, alignItems: "flex-start" }}>
      {canValidate(ret.status) && <button type="button" className="btn" disabled={busy} onClick={() => void start()}>{ret.fvu_validated_at ? "Validate again" : "Validate"}</button>}
      {note && <span className="t13" role="status" style={{ color: note.ok ? "var(--ink-2)" : "var(--amber)" }}>{note.lines.map((l, i) => <span key={i} style={{ display: "block" }}>{l}</span>)}</span>}
      <ErrorNote error={error} />
    </span>
  );
}

export function ChallanRow({ entityId, c, onChanged }: { entityId: string; c: Challan; onChanged: () => void }) {
  const st = CHALLAN_STATUS[c.status] ?? { label: c.status, tone: "prep" as const };
  return (
    <tr>
      <td><span style={{ fontWeight: 500 }}>{CHALLAN_LABEL[c.kind] ?? c.kind}</span><span className="t13" style={{ display: "block" }}>{c.period_label}</span></td>
      <td className="mono" style={{ textAlign: "right" }}>{formatInr(c.amount)}{(c.interest !== "0.00" || c.penalty !== "0.00") && <span className="t13" style={{ display: "block" }}>+ {formatInr(c.interest)} interest, {formatInr(c.penalty)} penalty</span>}</td>
      <td><Pill tone={st.tone}>{st.label}</Pill>{c.paid_on && <span className="t13" style={{ display: "block" }}>Paid {shortDate(c.paid_on)}</span>}</td>
      <td><PayChallan entityId={entityId} challan={c} onChanged={onChanged} /></td>
    </tr>
  );
}

export function ReturnRow({ entityId, r, onChanged }: { entityId: string; r: TdsReturn; onChanged: () => void }) {
  const st = RETURN_STATE[r.status] ?? { label: r.status, tone: "prep" as const };
  return (
    <tr>
      <td><span style={{ fontWeight: 500 }}>Form {r.form}</span><span className="t13" style={{ display: "block" }}>{quarterLabel(r.quarter)} FY {r.fy}</span></td>
      <td><Pill tone={st.tone}>{st.label}</Pill>{r.token_no && <span className="mono t13" style={{ display: "block" }}>Token {r.token_no}</span>}</td>
      <td><ValidateReturn entityId={entityId} ret={r} onChanged={onChanged} /></td>
    </tr>
  );
}

/** Put a month's deductions on a challan. The server totals them, adds any late interest and links them; the page then shows the challan to pay. */
export function PrepareDeposit({ entityId, onDone }: { entityId: string; onDone: () => void }) {
  const [options, setOptions] = useState<Array<{ id: string; label: string }> | null>(null);
  const [pick, setPick] = useState("");
  const [job, setJob] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    let live = true;
    (async () => {
      try {
        const rows = unwrap(await api.GET("/entities/{entityId}/obligations", { params: { path: { entityId }, query: { limit: 200 } } })).data as unknown as DepositObl[];
        if (!live) return;
        const o = depositOptions(rows);
        setOptions(o); setPick(o[0]?.id ?? "");
      } catch (e) { if (live) setError(e); }
    })();
    return () => { live = false; };
  }, [entityId]);

  async function start() {
    setBusy(true); setError(null); setNote(null);
    try { const j = unwrap(await api.POST("/entities/{entityId}/obligations/{id}/prepare", { params: { path: { entityId, id: pick } } })) as unknown as Accepted; setJob(j.id); }
    catch (e) { setError(e); }
    setBusy(false);
  }

  if (options === null && !error) return null;
  return (
    <div style={{ padding: "12px 20px", borderBottom: "1px solid var(--line-2)", display: "flex", flexDirection: "column", gap: 8 }}>
      {(options ?? []).length === 0 ? <span className="t13">No TDS deposit is waiting to be prepared.</span> : (
        <span style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center" }}>
          <label className="t13" htmlFor="dep-pick">Put deductions on a challan for</label>
          <select id="dep-pick" className="input" style={{ height: 36, fontSize: 14 }} value={pick} onChange={(e) => setPick(e.target.value)} disabled={!!job}>{(options ?? []).map((o) => <option key={o.id} value={o.id}>{o.label}</option>)}</select>
          <button type="button" className="btn" disabled={busy || !!job || !pick} onClick={() => void start()}>Prepare challan</button>
          {job && <JobFollow jobId={job} doneLabel="Prepared" onFinished={(j) => { setNote(depositNote(j.result, (v) => formatInr(v))); setJob(null); onDone(); }} />}
        </span>
      )}
      {note && <span className="t13" role="status">{note}</span>}
      <ErrorNote error={error} />
    </div>
  );
}
