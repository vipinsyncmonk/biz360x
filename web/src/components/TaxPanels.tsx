"use client";
import { useEffect, useState } from "react";
import { api, unwrap } from "@/lib/api";
import { formatInr, shortDate } from "@/lib/format";
import { useLoad } from "@/lib/hooks";
import {
  DECL_STATUS, blankItem, capText, declBody, draftFromDecl, form16Rows, formProblem, fyOptions, itemLine, itemProblem, regimeLabel,
  type DeclRow, type Form16, type ItemDraft, type TaxDecl,
} from "@/lib/taxdecl";
import { ErrorNote, Loading, Pill } from "./ui";

const row = { borderTop: "1px solid var(--line-2)", padding: "14px 20px" } as const;

function Form16Card({ f }: { f: Form16 }) {
  return (
    <div style={{ border: "1px solid var(--line)", borderRadius: 10, padding: 14, display: "flex", flexDirection: "column", gap: 6 }} aria-label={`Form 16 Part B for ${f.fy}`}>
      <b>Form 16 Part B · {f.fy} (assessment year {f.assessment_year})</b>
      <span className="t13">{f.months} month{f.months === 1 ? "" : "s"} of approved payslips · {regimeLabel(f.regime)}</span>
      {form16Rows(f).map(([k, v]) => <span key={k} style={{ display: "flex", justifyContent: "space-between", gap: 12 }}><span>{k}</span><span className="mono">{v}</span></span>)}
      {!f.deductions_verified && f.regime === "old" && <span className="t13">No deductions are counted yet: HR has not verified a declaration.</span>}
      <span className="t13">{f.note}</span>
    </div>
  );
}

// ------------------------------------------------------------------ the employee's own
/** My tax declaration for a year: the regime, the deductions I will claim, and sending it to HR. */
export function MyTaxPanel({ employeeId }: { employeeId: string }) {
  const years = fyOptions();
  const [fy, setFy] = useState(years[0]!);
  const decl = useLoad(async () => unwrap(await api.GET("/self/{employeeId}/tax-declaration", { params: { path: { employeeId }, query: { fy } } })) as unknown as TaxDecl, `${employeeId}:${fy}`);
  const [regime, setRegime] = useState<"new" | "old">("new");
  const [items, setItems] = useState<ItemDraft[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [form16, setForm16] = useState<Form16 | null>(null);
  const [shown, setShown] = useState(false);
  const d = decl.data;

  useEffect(() => { if (d) { const x = draftFromDecl(d); setRegime(x.regime); setItems(x.items); } }, [d]);
  useEffect(() => { setForm16(null); setShown(false); }, [fy]);

  const problem = formProblem(items);
  const edit = (n: number, patch: Partial<ItemDraft>) => setItems((xs) => xs.map((x, i) => (i === n ? { ...x, ...patch } : x)));

  async function save(send: boolean) {
    setBusy(true); setError(null);
    try {
      unwrap(await api.PUT("/self/{employeeId}/tax-declaration", { params: { path: { employeeId }, query: { fy } }, body: declBody(regime, items) as never }));
      if (send) unwrap(await api.POST("/self/{employeeId}/tax-declaration/submit", { params: { path: { employeeId }, query: { fy } } }));
      decl.reload();
    } catch (e) { setError(e); } finally { setBusy(false); }
  }
  async function showForm16() {
    setShown(true); setError(null);
    try { setForm16(unwrap(await api.GET("/self/{employeeId}/form16", { params: { path: { employeeId }, query: { fy } } })) as unknown as Form16); } catch (e) { setError(e); }
  }

  return (
    <section className="card" style={{ overflow: "hidden" }} aria-label="My tax declaration">
      <div className="cardhead">
        <h2>My tax declaration</h2>
        <span style={{ display: "flex", gap: 10, alignItems: "center" }}>
          {d && <Pill tone={DECL_STATUS[d.status].tone}>{DECL_STATUS[d.status].label}</Pill>}
          <label className="sr" htmlFor="tax-fy">Financial year</label>
          <select id="tax-fy" className="input" style={{ height: 36, width: "auto" }} value={fy} onChange={(e) => setFy(e.target.value)}>{years.map((y) => <option key={y} value={y}>{y}</option>)}</select>
        </span>
      </div>
      <div style={{ padding: "0 20px" }}><ErrorNote error={decl.error ?? error} /></div>
      {decl.loading && !d ? <Loading what="your declaration" /> : d && (
        <div style={{ padding: "0 20px 18px", display: "flex", flexDirection: "column", gap: 14 }}>
          <span className="t13">{DECL_STATUS[d.status].hint}</span>
          {d.status === "returned" && d.hr_note && <span role="status" style={{ fontWeight: 600 }}>HR&apos;s note: {d.hr_note}</span>}
          <div className="field" style={{ maxWidth: 320 }}><label htmlFor="tax-regime">Tax regime for {fy}</label>
            <select id="tax-regime" className="input" disabled={!d.editable} value={regime} onChange={(e) => setRegime(e.target.value as "new" | "old")}>
              <option value="new">{regimeLabel("new")}</option><option value="old">{regimeLabel("old")}</option></select></div>
          {regime === "old" ? (
            d.editable ? (
              <div style={{ display: "flex", flexDirection: "column", gap: 10 }} role="group" aria-label="Deductions">
                {items.map((it, n) => (
                  <div key={n} style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(150px, 1fr))", gap: 8, alignItems: "end" }}>
                    <div className="field"><label htmlFor={`sec-${n}`}>Section</label>
                      <select id={`sec-${n}`} className="input" value={it.section} onChange={(e) => edit(n, { section: e.target.value })}>
                        {d.sections.map((s) => <option key={s.code} value={s.code}>{s.code}</option>)}</select></div>
                    <div className="field"><label htmlFor={`desc-${n}`}>What it is</label><input id={`desc-${n}`} className="input" value={it.description} maxLength={200} onChange={(e) => edit(n, { description: e.target.value })} /></div>
                    <div className="field"><label htmlFor={`amt-${n}`}>Amount for the year (₹)</label><input id={`amt-${n}`} className="input" inputMode="decimal" value={it.amount} onChange={(e) => edit(n, { amount: e.target.value })} /></div>
                    <div className="field"><label htmlFor={`prf-${n}`}>Proof (policy or receipt no.)</label><input id={`prf-${n}`} className="input" value={it.proof} maxLength={200} onChange={(e) => edit(n, { proof: e.target.value })} /></div>
                    <button type="button" className="btn" aria-label={`Remove line ${n + 1}`} onClick={() => setItems((xs) => xs.filter((_, i) => i !== n))}>Remove</button>
                    {itemProblem(it) && it.description + it.amount !== "" && <span className="t13" role="alert" style={{ gridColumn: "1 / -1" }}>{itemProblem(it)}</span>}
                  </div>
                ))}
                <span><button type="button" className="btn" onClick={() => setItems((xs) => [...xs, blankItem(d.sections[0]?.code)])}>Add a deduction</button></span>
                <ul className="t13" style={{ margin: 0, paddingLeft: 18 }}>{d.sections.map((s) => <li key={s.code}><b>{s.code}</b>: {s.label}, {capText(s)}</li>)}</ul>
              </div>
            ) : (
              <div role="list" aria-label="Deductions">{d.items.length === 0 ? <span className="t13">No deductions declared.</span> : d.items.map((i) => (
                <div key={i.id} role="listitem" style={{ display: "flex", justifyContent: "space-between", gap: 12, padding: "4px 0" }}>
                  <span>{i.section} · {i.description}<span className="t13" style={{ display: "block" }}>{itemLine(i)}</span></span><span className="mono">{formatInr(i.declared_amount)}</span></div>))}
              </div>
            )
          ) : <span className="t13">Under the new regime only the standard deduction applies, so there is nothing to declare.</span>}
          {d.summary.length > 0 && regime === "old" && (
            <span className="t13" aria-label="What counts">What counts so far: {d.summary.map((s) => `${s.section} ${formatInr(s.counts)}`).join(" · ")}{d.status !== "verified" ? " (declared, not yet verified; TDS uses verified figures only)" : ""}</span>
          )}
          {d.editable && (
            <span style={{ display: "flex", gap: 10, flexWrap: "wrap" }}>
              <button type="button" className="btn" disabled={busy || (regime === "old" && !!problem)} onClick={() => void save(false)}>{busy ? "Saving…" : "Save draft"}</button>
              <button type="button" className="btn primary" disabled={busy || (regime === "old" && !!problem)} onClick={() => void save(true)}>Send to HR</button>
            </span>
          )}
          {d.submitted_at && <span className="t13">Sent on {shortDate(d.submitted_at)}{d.verified_at ? ` · verified on ${shortDate(d.verified_at)}` : ""}.</span>}
          <span className="t13">{d.note}</span>
          <span><button type="button" className="btn" onClick={() => void showForm16()}>{shown ? "Refresh Form 16 Part B" : "Show Form 16 Part B"}</button></span>
          {form16 && <Form16Card f={form16} />}
        </div>
      )}
    </section>
  );
}

// ------------------------------------------------------------------ HR's side
/** HR verifies each figure of a submitted declaration, or sends it back with a note. */
export function TaxDeclarationsPanel({ entityId }: { entityId: string }) {
  const years = fyOptions();
  const [fy, setFy] = useState(years[0]!);
  const list = useLoad(async () => unwrap(await api.GET("/entities/{entityId}/tax-declarations", { params: { path: { entityId }, query: { fy } } })).data as unknown as DeclRow[], `${entityId}:${fy}`);
  const [open, setOpen] = useState<TaxDecl | null>(null);
  const [amounts, setAmounts] = useState<Record<string, string>>({});
  const [rejected, setRejected] = useState<Record<string, boolean>>({});
  const [note, setNote] = useState("");
  const [form16, setForm16] = useState<Form16 | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);

  async function review(id: string) {
    setError(null); setForm16(null); setNote("");
    try {
      const d = unwrap(await api.GET("/entities/{entityId}/tax-declarations/{id}", { params: { path: { entityId, id } } })) as unknown as TaxDecl;
      setOpen(d);
      setAmounts(Object.fromEntries(d.items.map((i) => [i.id, i.verified_amount ?? i.declared_amount])));
      setRejected(Object.fromEntries(d.items.map((i) => [i.id, i.status === "rejected"])));
    } catch (e) { setError(e); }
  }
  async function verify() {
    if (!open) return;
    setBusy(true); setError(null);
    try {
      const items = open.items.map((i) => ({ id: i.id, status: rejected[i.id] ? "rejected" : "verified", verified_amount: rejected[i.id] ? null : amounts[i.id] }));
      setOpen(unwrap(await api.POST("/entities/{entityId}/tax-declarations/{id}/verify", { params: { path: { entityId, id: open.id! } }, body: { items, note: note.trim() || null } as never })) as unknown as TaxDecl);
      list.reload();
    } catch (e) { setError(e); } finally { setBusy(false); }
  }
  async function sendBack() {
    if (!open) return;
    setBusy(true); setError(null);
    try {
      setOpen(unwrap(await api.POST("/entities/{entityId}/tax-declarations/{id}/return", { params: { path: { entityId, id: open.id! } }, body: { note: note.trim() } })) as unknown as TaxDecl);
      list.reload();
    } catch (e) { setError(e); } finally { setBusy(false); }
  }
  async function showForm16(employeeId: string) {
    setError(null);
    try { setForm16(unwrap(await api.GET("/entities/{entityId}/employees/{id}/form16", { params: { path: { entityId, id: employeeId }, query: { fy } } })) as unknown as Form16); } catch (e) { setError(e); }
  }

  const rows = list.data ?? [];
  return (
    <section className="card" style={{ overflow: "hidden" }} aria-label="Tax declarations">
      <div style={{ padding: "14px 20px", borderBottom: "1px solid var(--line-2)", display: "flex", flexWrap: "wrap", gap: 12, justifyContent: "space-between" }}>
        <span><h2 style={{ margin: 0 }}>Tax declarations</h2>
          <span className="t13">Each person states their regime and deductions. Verify each figure; TDS uses the verified ones from the next payroll computation. The limits are a starter library a CA must review.</span></span>
        <span><label className="sr" htmlFor="hr-tax-fy">Financial year</label>
          <select id="hr-tax-fy" className="input" style={{ height: 36, width: "auto" }} value={fy} onChange={(e) => { setFy(e.target.value); setOpen(null); }}>{years.map((y) => <option key={y} value={y}>{y}</option>)}</select></span>
      </div>
      <div style={{ padding: "0 20px" }}><ErrorNote error={list.error ?? error} /></div>
      {list.loading && !list.data ? <Loading what="declarations" /> : rows.length === 0 ? <div className="empty">Nobody has started a declaration for {fy}.</div> : rows.map((r) => (
        <div key={r.id} style={{ ...row, display: "flex", flexWrap: "wrap", gap: 12, alignItems: "center", justifyContent: "space-between" }}>
          <span style={{ fontWeight: 600 }}>{r.employee_name}<span className="t13 mono" style={{ display: "block", fontWeight: 400 }}>{r.employee_code} · {r.regime === "old" ? "old regime" : "new regime"} · {r.items} deduction{r.items === 1 ? "" : "s"} · {formatInr(r.declared)}</span></span>
          <span style={{ display: "flex", gap: 10, alignItems: "center" }}><Pill tone={DECL_STATUS[r.status].tone}>{DECL_STATUS[r.status].label}</Pill>
            <button type="button" className="btn" aria-label={`Review ${r.employee_name}`} onClick={() => void review(r.id)}>Review</button></span>
        </div>
      ))}
      {open && (
        <div style={{ ...row, display: "flex", flexDirection: "column", gap: 12 }} role="region" aria-label={`Declaration of ${open.employee_name}`}>
          <b>{open.employee_name} · {open.fy} · {regimeLabel(open.regime)}</b>
          {open.items.length === 0 ? <span className="t13">No deductions declared.</span> : open.items.map((i) => (
            <div key={i.id} style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(150px, 1fr))", gap: 8, alignItems: "center" }}>
              <span>{i.section} · {i.description}<span className="t13" style={{ display: "block" }}>{i.proof_ref ? `Proof: ${i.proof_ref}` : "No proof reference"} · declared {formatInr(i.declared_amount)}</span></span>
              {open.status === "submitted" ? (
                <>
                  <div className="field"><label htmlFor={`v-${i.id}`}>Verified amount for {i.description}</label>
                    <input id={`v-${i.id}`} className="input" inputMode="decimal" disabled={rejected[i.id]} value={amounts[i.id] ?? ""} onChange={(e) => setAmounts((a) => ({ ...a, [i.id]: e.target.value }))} /></div>
                  <label style={{ display: "flex", gap: 6, alignItems: "center" }}><input type="checkbox" checked={!!rejected[i.id]} onChange={(e) => setRejected((r) => ({ ...r, [i.id]: e.target.checked }))} />Do not accept {i.description}</label>
                </>
              ) : <span className="t13">{itemLine(i)}</span>}
            </div>
          ))}
          {open.status === "returned" && open.hr_note && <span className="t13">Sent back with: {open.hr_note}</span>}
          {(open.status === "submitted" || open.status === "verified") && (
            <div className="field"><label htmlFor="tax-note">{open.status === "submitted" ? "Note (needed to send it back)" : "Why you are returning it"}</label>
              <input id="tax-note" className="input" maxLength={300} value={note} onChange={(e) => setNote(e.target.value)} /></div>
          )}
          <span style={{ display: "flex", gap: 10, flexWrap: "wrap" }}>
            {open.status === "submitted" && <button type="button" className="btn primary" disabled={busy} onClick={() => void verify()}>Verify</button>}
            {(open.status === "submitted" || open.status === "verified") && <button type="button" className="btn" disabled={busy || !note.trim()} onClick={() => void sendBack()}>Return with note</button>}
            <button type="button" className="btn" onClick={() => void showForm16(open.employee_id!)}>Form 16 Part B</button>
          </span>
          {form16 && <Form16Card f={form16} />}
        </div>
      )}
    </section>
  );
}
