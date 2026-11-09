"use client";
import { useState } from "react";
import { api, newIdempotencyKey, unwrap } from "@/lib/api";
import { formatInr, isoDate, shortDate } from "@/lib/format";
import {
  CLAIM_STATE, Claim, Classification, FactsForm, LateBuyer, PLATFORMS, TREDS_STATE, Treds, TredsForm, UdyamForm, blankFacts, blankUdyam, claimLine, factsBody, factsProblem, tredsBody, tredsNext, tredsProblem, udyamBody, udyamProblem, uploadable,
} from "@/lib/msme";
import { ErrorNote, Pill } from "./ui";
import { JobFollow } from "./JobFollow";

/** One claim for delayed payment: a draft you can file, or the case it became. Filing is a background job on MSME Samadhaan. */
export function ClaimCard({ entityId, claim, onChanged }: { entityId: string; claim: Claim; onChanged: () => void }) {
  const st = CLAIM_STATE[claim.status] ?? { label: claim.status, tone: "prep" as const };
  const [job, setJob] = useState<string | null>(null);
  const [asking, setAsking] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  async function file() {
    setBusy(true); setError(null);
    try {
      const j = unwrap(await api.POST("/entities/{entityId}/delayed-payment-claims/{id}/file", { params: { path: { entityId, id: claim.id }, header: { "Idempotency-Key": newIdempotencyKey() } } })) as unknown as { id: string };
      setJob(j.id); setAsking(false);
    } catch (e) { setError(e); }
    setBusy(false);
  }
  return (
    <div style={{ borderTop: "1px solid var(--line-2)", padding: "14px 20px", display: "flex", flexDirection: "column", gap: 8 }}>
      <div style={{ display: "flex", justifyContent: "space-between", gap: 10, flexWrap: "wrap" }}>
        <span style={{ fontSize: 14, fontWeight: 600 }}>{claim.buyer_name}<span className="t13" style={{ display: "block", fontWeight: 400 }}>{claimLine(claim)}</span></span>
        <Pill tone={st.tone}>{st.label}</Pill>
      </div>
      {claim.case_no && <span className="mono t13">Case {claim.case_no}{claim.filed_on ? ` · filed ${shortDate(claim.filed_on)}` : ""}</span>}
      {claim.status === "draft" && !job && !asking && <span><button type="button" className="btn primary" onClick={() => setAsking(true)}>File on MSME Samadhaan</button></span>}
      {asking && (
        <span style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center" }}>
          <span className="t13">File a claim for {formatInr(claim.principal, { decimals: false })} plus {formatInr(claim.interest_claimed, { decimals: false })} interest?</span>
          <button type="button" className="btn primary" disabled={busy} onClick={() => void file()}>{busy ? "Filing…" : "Yes, file it"}</button>
          <button type="button" className="btn" onClick={() => setAsking(false)}>Not now</button>
        </span>
      )}
      {job && <JobFollow jobId={job} doneLabel="Filed" onFinished={() => { setJob(null); onChanged(); }} />}
      <ErrorNote error={error} />
    </div>
  );
}

/** Put late invoices on a TReDS platform. Uploading is recorded here; the platform itself is used outside Biz360X. */
export function UploadTreds({ entityId, buyers, onDone, onCancel }: { entityId: string; buyers: LateBuyer[]; onDone: () => void; onCancel: () => void }) {
  const options = uploadable(buyers);
  const [doc, setDoc] = useState(options[0]?.document_id ?? "");
  const [platform, setPlatform] = useState(PLATFORMS[0]!);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  async function save() {
    setBusy(true); setError(null);
    try { unwrap(await api.POST("/entities/{entityId}/treds-invoices", { params: { path: { entityId }, header: { "Idempotency-Key": newIdempotencyKey() } }, body: { sales_document_id: doc, platform } as never })); onDone(); }
    catch (e) { setError(e); setBusy(false); }
  }
  return (
    <form style={{ display: "flex", flexDirection: "column", gap: 10, padding: "0 20px 14px" }} onSubmit={(e) => { e.preventDefault(); if (doc && !busy) void save(); }}>
      {options.length === 0 ? <span className="t13">Every late invoice is already on TReDS.</span> : (
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(200px, 1fr))", gap: 12 }}>
          <div className="field"><label htmlFor="tr-inv">Invoice</label>
            <select id="tr-inv" className="input" value={doc} onChange={(e) => setDoc(e.target.value)}>{options.map((o) => <option key={o.document_id} value={o.document_id}>{o.number} · {o.buyer} · {formatInr(o.balance_due, { decimals: false })}</option>)}</select></div>
          <div className="field"><label htmlFor="tr-plat">Platform</label>
            <select id="tr-plat" className="input" value={platform} onChange={(e) => setPlatform(e.target.value)}>{PLATFORMS.map((p) => <option key={p} value={p}>{p}</option>)}</select></div>
        </div>
      )}
      <ErrorNote error={error} />
      <span style={{ display: "flex", gap: 10 }}><button type="submit" className="btn primary" disabled={busy || !doc}>{busy ? "Saving…" : "Record upload"}</button><button type="button" className="btn" onClick={onCancel}>Cancel</button></span>
    </form>
  );
}

/** Say how an upload is getting on at the platform. It only moves forward. */
export function TredsRow({ entityId, t, onChanged }: { entityId: string; t: Treds; onChanged: () => void }) {
  const st = TREDS_STATE[t.status] ?? { label: t.status, tone: "prep" as const };
  const next = tredsNext(t.status);
  const [open, setOpen] = useState(false);
  const [f, setF] = useState<TredsForm>({ status: next[0]?.[0] ?? "", rate: "", amount: "" });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const problem = tredsProblem(f);
  async function save() {
    setBusy(true); setError(null);
    try { unwrap(await api.PATCH("/entities/{entityId}/treds-invoices/{id}", { params: { path: { entityId, id: t.id } }, body: tredsBody(f) as never })); setOpen(false); onChanged(); }
    catch (e) { setError(e); }
    setBusy(false);
  }
  return (
    <div style={{ borderTop: "1px solid var(--line-2)", padding: "12px 20px", display: "flex", flexDirection: "column", gap: 8 }}>
      <div style={{ display: "flex", justifyContent: "space-between", gap: 10, flexWrap: "wrap" }}>
        <span style={{ fontSize: 14 }}>{t.invoice_number} · {t.buyer_name}<span className="t13" style={{ display: "block" }}>{t.platform} · {formatInr(t.invoice_total, { decimals: false })}{t.amount_received ? ` · received ${formatInr(t.amount_received, { decimals: false })} at ${t.discount_rate_pct}%` : ""}</span></span>
        <Pill tone={st.tone}>{st.label}</Pill>
      </div>
      {next.length > 0 && !open && <span><button type="button" className="linkbtn" style={{ color: "var(--cobalt)" }} onClick={() => setOpen(true)}>Record progress</button></span>}
      {open && (
        <form style={{ display: "flex", gap: 10, flexWrap: "wrap", alignItems: "flex-end" }} onSubmit={(e) => { e.preventDefault(); if (!problem && !busy) void save(); }}>
          <div className="field"><label htmlFor={`ts-${t.id}`}>Now</label><select id={`ts-${t.id}`} className="input" style={{ height: 40 }} value={f.status} onChange={(e) => setF({ ...f, status: e.target.value })}>{next.map(([k, l]) => <option key={k} value={k}>{l}</option>)}</select></div>
          {f.status === "financed" && <>
            <div className="field"><label htmlFor={`tr-${t.id}`}>Discount rate (%)</label><input id={`tr-${t.id}`} className="input mono" style={{ height: 40, width: 110 }} inputMode="decimal" value={f.rate} onChange={(e) => setF({ ...f, rate: e.target.value })} /></div>
            <div className="field"><label htmlFor={`ta-${t.id}`}>Received (₹)</label><input id={`ta-${t.id}`} className="input mono" style={{ height: 40, width: 150 }} inputMode="decimal" value={f.amount} onChange={(e) => setF({ ...f, amount: e.target.value })} /></div>
          </>}
          <button type="submit" className="btn primary" disabled={busy || !!problem}>{busy ? "Saving…" : "Save"}</button>
          <button type="button" className="btn" onClick={() => setOpen(false)}>Cancel</button>
        </form>
      )}
      {open && problem && <span className="t13" role="alert" style={{ color: "var(--amber)" }}>{problem}</span>}
      <ErrorNote error={error} />
    </div>
  );
}

/** The two figures that decide a Udyam class. Recorded as dated company facts, which the scheme checks read too. */
export function UdyamFacts({ entityId, current, onDone }: { entityId: string; current: Classification | null; onDone: () => void }) {
  const [f, setF] = useState<FactsForm>(blankFacts(current));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const problem = factsProblem(f);
  async function save() {
    setBusy(true); setError(null);
    try { unwrap(await api.POST("/entities/{entityId}/facts", { params: { path: { entityId } }, body: factsBody(f, isoDate(new Date())) as never })); onDone(); }
    catch (e) { setError(e); }
    setBusy(false);
  }
  return (
    <form style={{ display: "flex", flexDirection: "column", gap: 10 }} onSubmit={(e) => { e.preventDefault(); if (!problem && !busy) void save(); }}>
      <div className="field"><label htmlFor="ud-inv" style={{ color: "#B7BCC5" }}>Investment in plant and machinery (₹)</label><input id="ud-inv" className="input mono" inputMode="numeric" value={f.investment} onChange={(e) => setF({ ...f, investment: e.target.value })} /></div>
      <div className="field"><label htmlFor="ud-turn" style={{ color: "#B7BCC5" }}>Annual turnover (₹)</label><input id="ud-turn" className="input mono" inputMode="numeric" value={f.turnover} onChange={(e) => setF({ ...f, turnover: e.target.value })} /></div>
      {problem && (f.investment || f.turnover) && <span className="t13" role="alert" style={{ color: "#FFC58A" }}>{problem}</span>}
      <ErrorNote error={error} />
      <span><button type="submit" className="btn" disabled={busy || !!problem}>{busy ? "Saving…" : "Save figures"}</button></span>
    </form>
  );
}

/** Record the company's Udyam registration and class, which turns on the MSME rights and schemes. */
export function UdyamRegister({ entityId, onDone }: { entityId: string; onDone: () => void }) {
  const [f, setF] = useState<UdyamForm>(blankUdyam());
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const problem = udyamProblem(f);
  async function save() {
    setBusy(true); setError(null);
    try { unwrap(await api.PATCH("/entities/{entityId}", { params: { path: { entityId }, header: { "If-Match": "*" } }, body: udyamBody(f) as never })); onDone(); }
    catch (e) { setError(e); }
    setBusy(false);
  }
  return (
    <form style={{ display: "flex", flexDirection: "column", gap: 10 }} onSubmit={(e) => { e.preventDefault(); if (!problem && !busy) void save(); }}>
      <div className="field"><label htmlFor="ur-num" style={{ color: "#B7BCC5" }}>Udyam registration number</label><input id="ur-num" className="input mono" placeholder="UDYAM-KA-03-0054321" value={f.number} onChange={(e) => setF({ ...f, number: e.target.value })} /></div>
      <div className="field"><label htmlFor="ur-cls" style={{ color: "#B7BCC5" }}>Class on the certificate</label>
        <select id="ur-cls" className="input" value={f.cls} onChange={(e) => setF({ ...f, cls: e.target.value })}><option value="micro">Micro</option><option value="small">Small</option><option value="medium">Medium</option></select></div>
      {problem && f.number && <span className="t13" role="alert" style={{ color: "#FFC58A" }}>{problem}</span>}
      <ErrorNote error={error} />
      <span><button type="submit" className="btn primary" disabled={busy || !!problem}>{busy ? "Saving…" : "Save registration"}</button></span>
    </form>
  );
}
