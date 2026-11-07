"use client";
import { useState } from "react";
import { api, unwrap } from "@/lib/api";
import { CONTRACT_KINDS, CONTRACT_NEXT, ContractForm, VALUE_PERIODS, blankContract, contractBody, contractProblem, termKeyFrom, termReady } from "@/lib/board";
import { Contract, Term } from "@/lib/legal";
import { ErrorNote } from "./ui";

const box = { border: "1px solid var(--line)", borderRadius: 12, background: "var(--surface-2)", padding: 16, display: "flex", flexDirection: "column", gap: 12 } as const;

export function NewContract({ entityId, onDone, onCancel }: { entityId: string; onDone: (id: string) => void; onCancel: () => void }) {
  const [f, setF] = useState<ContractForm>(blankContract());
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [touched, setTouched] = useState(false);
  const problem = contractProblem(f);
  const set = (patch: Partial<ContractForm>) => setF({ ...f, ...patch });
  const text = (key: keyof ContractForm, label: string, extra: { type?: string; mono?: boolean; placeholder?: string } = {}) => (
    <div className="field"><label htmlFor={`c-${key}`}>{label}</label><input id={`c-${key}`} type={extra.type ?? "text"} className={`input${extra.mono ? " mono" : ""}`} placeholder={extra.placeholder} value={f[key] as string} onChange={(e) => set({ [key]: e.target.value })} /></div>
  );
  async function save() {
    setTouched(true);
    if (problem) return;
    setBusy(true); setError(null);
    try { const c = unwrap(await api.POST("/entities/{entityId}/contracts", { params: { path: { entityId } }, body: contractBody(f) as never })) as unknown as { id: string }; onDone(c.id); }
    catch (e) { setError(e); setBusy(false); }
  }
  return (
    <form className="card card-pad" style={{ display: "flex", flexDirection: "column", gap: 14 }} onSubmit={(e) => { e.preventDefault(); void save(); }}>
      <h2 style={{ margin: 0 }}>New contract</h2>
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(210px, 1fr))", gap: 14 }}>
        {text("title", "Title", { placeholder: "Annual maintenance agreement" })}
        {text("counterparty", "Other party")}
        <div className="field"><label htmlFor="c-kind">Type</label><select id="c-kind" className="input" value={f.kind} onChange={(e) => set({ kind: e.target.value })}>{CONTRACT_KINDS.map(([k, l]) => <option key={k} value={k}>{l}</option>)}</select></div>
        {text("start", "Starts on", { type: "date" })}
        {text("end", "Ends on", { type: "date" })}
        {text("value", "Value (₹)", { mono: true })}
        <div className="field"><label htmlFor="c-period">Value covers</label><select id="c-period" className="input" value={f.period} onChange={(e) => set({ period: e.target.value })}>{VALUE_PERIODS.map(([k, l]) => <option key={k} value={k}>{l}</option>)}</select></div>
        {text("terms", "Payment terms (days)", { mono: true })}
        {text("law", "Governing law")}
      </div>
      <label style={{ display: "flex", gap: 8, fontSize: 14 }}><input type="checkbox" checked={f.autoRenews} onChange={(e) => set({ autoRenews: e.target.checked })} />Renews by itself unless someone gives notice</label>
      {f.autoRenews && (
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(210px, 1fr))", gap: 14 }}>
          {text("noticeDays", "Notice needed (days before the end)", { mono: true })}
          {text("renewalMonths", "Each renewal (months)", { mono: true })}
        </div>
      )}
      <span className="t13">The contract is saved as a draft. Add its key terms afterwards and watch the notice date from the register.</span>
      {touched && problem && <span className="t13" role="alert" style={{ color: "var(--amber)" }}>{problem}</span>}
      <ErrorNote error={error} />
      <span style={{ display: "flex", gap: 10 }}>
        <button type="submit" className="btn primary" disabled={busy}>{busy ? "Saving…" : "Save contract"}</button>
        <button type="button" className="btn" onClick={onCancel}>Cancel</button>
      </span>
    </form>
  );
}

/** Change a contract's status, and add or verify its key terms. */
export function ContractActions({ entityId, contract, onChange }: { entityId: string; contract: Contract; onChange: () => void }) {
  const [label, setLabel] = useState("");
  const [value, setValue] = useState("");
  const [verified, setVerified] = useState(true);
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  const next = CONTRACT_NEXT[contract.status] ?? [];

  async function run(fn: () => Promise<void>) {
    setBusy(true); setError(null);
    try { await fn(); onChange(); } catch (e) { setError(e); }
    setBusy(false);
  }
  const putTerm = async (key: string, text: string, ok: boolean, page?: number | null) => {
    unwrap(await api.PUT("/entities/{entityId}/contracts/{id}/terms/{termKey}", { params: { path: { entityId, id: contract.id, termKey: key } }, body: { value_text: text, verified: ok, ...(page ? { source_page: page } : {}) } as never }));
  };

  return (
    <div style={box}>
      <ErrorNote error={error} />
      {next.length > 0 && (
        <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center" }}>
          <span className="t13">Move to:</span>
          {next.map(([s, l]) => (
            <button key={s} type="button" className="btn" style={{ height: 34 }} disabled={busy}
                    onClick={() => void run(async () => { unwrap(await api.PATCH("/entities/{entityId}/contracts/{id}", { params: { path: { entityId, id: contract.id }, header: { "If-Match": "*" } }, body: { status: s } as never })); })}>{l}</button>
          ))}
        </div>
      )}
      {contract.terms.filter((t: Term) => !t.verified).map((t) => (
        <div key={t.term_key} style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 8, fontSize: 14 }}>
          <span>{t.term_key.replace(/_/g, " ")}: {t.value_text}</span>
          <button type="button" className="btn" style={{ height: 32 }} disabled={busy} onClick={() => void run(() => putTerm(t.term_key, t.value_text, true, t.source_page))}>Mark verified</button>
        </div>
      ))}
      <form style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(180px, 1fr))", gap: 10, alignItems: "end" }}
            onSubmit={(e) => { e.preventDefault(); if (termReady(label, value) && !busy) void run(async () => { await putTerm(termKeyFrom(label), value.trim(), verified); setLabel(""); setValue(""); }); }}>
        <div className="field"><label htmlFor={`tl-${contract.id}`}>Add a term</label><input id={`tl-${contract.id}`} className="input" placeholder="Late payment interest" value={label} onChange={(e) => setLabel(e.target.value)} /></div>
        <div className="field"><label htmlFor={`tv-${contract.id}`}>What it says</label><input id={`tv-${contract.id}`} className="input" placeholder="18% a year" value={value} onChange={(e) => setValue(e.target.value)} /></div>
        <label style={{ display: "flex", gap: 6, fontSize: 13 }}><input type="checkbox" checked={verified} onChange={(e) => setVerified(e.target.checked)} />I checked this against the signed copy</label>
        <button type="submit" className="btn primary" disabled={!termReady(label, value) || busy}>Save term</button>
      </form>
    </div>
  );
}
