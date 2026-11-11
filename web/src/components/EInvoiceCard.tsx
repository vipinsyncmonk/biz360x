"use client";
import { useState } from "react";
import { api, unwrap } from "@/lib/api";
import { DeclareForm, EInvoiceStatus, declareBody, declareProblem, describe, yearOptions } from "@/lib/einvoice";
import { formatInr } from "@/lib/format";
import { useLoad } from "@/lib/hooks";
import { ErrorNote, Pill } from "@/components/ui";

/** Whether e-invoicing applies, why, and the turnover of each year behind it, with a way to declare a year's figure when the books do not hold it. */
export function EInvoiceCard({ entityId }: { entityId: string }) {
  const status = useLoad(async () => unwrap(await api.GET("/entities/{entityId}/einvoice-status", { params: { path: { entityId } } })) as unknown as EInvoiceStatus, entityId);
  const [open, setOpen] = useState(false);
  const [form, setForm] = useState<DeclareForm | null>(null);
  const [busy, setBusy] = useState(false);
  const [failure, setFailure] = useState<unknown>(null);
  const s = status.data;
  if (status.error) return <ErrorNote error={status.error} />;
  if (!s) return null;
  const d = describe(s);
  const f = form ?? { fy: yearOptions(s.financial_year)[1]!, amount: "" };
  const problem = declareProblem(f);

  async function save(e: React.FormEvent) {
    e.preventDefault();
    if (problem) return;
    setBusy(true); setFailure(null);
    try {
      unwrap(await api.POST("/entities/{entityId}/facts", { params: { path: { entityId } }, body: declareBody(f) as never }));
      setForm(null); status.reload();
    } catch (err) { setFailure(err); } finally { setBusy(false); }
  }

  return (
    <section className="card card-pad" aria-label="E-invoicing" style={{ display: "flex", flexDirection: "column", gap: 10 }}>
      <div style={{ display: "flex", gap: 10, alignItems: "center", flexWrap: "wrap" }}>
        <h2 style={{ margin: 0 }}>E-invoicing</h2>
        <Pill tone={d.tone}>{d.pill}</Pill>
      </div>
      <span style={{ fontWeight: 600 }}>{d.headline}</span>
      <span className="t13">{d.detail}</span>
      <span>
        <button type="button" className="btn" style={{ height: 36, fontSize: 13 }} onClick={() => setOpen(!open)} aria-expanded={open}>{open ? "Hide turnover by year" : "Turnover by year"}</button>
      </span>
      {open && (
        <>
          <div className="tablewrap">
            <table style={{ minWidth: 520 }}>
              <thead><tr><th scope="col">Financial year</th><th scope="col" style={{ textAlign: "right" }}>From the books</th><th scope="col" style={{ textAlign: "right" }}>Declared</th><th scope="col" style={{ textAlign: "right" }}>Used</th></tr></thead>
              <tbody>
                {s.years.length === 0 && <tr><td colSpan={4} className="muted">No turnover yet. Issue invoices or declare a year below.</td></tr>}
                {s.years.map((y) => (
                  <tr key={y.financial_year}>
                    <td className="mono">{y.financial_year}</td>
                    <td className="mono" style={{ textAlign: "right" }}>{y.books ? formatInr(y.books) : "–"}</td>
                    <td className="mono" style={{ textAlign: "right" }}>{y.declared ? formatInr(y.declared) : "–"}</td>
                    <td className="mono" style={{ textAlign: "right" }}>{formatInr(y.used)}{y.above_limit ? " ▲" : ""}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <span className="t13">A declared figure is used in place of the books for that year. ▲ marks a year above the limit. The books show invoices, export invoices and debit notes less credit notes, excluding tax; the legal figure covers the whole PAN, so confirm it with your CA.</span>
          <form onSubmit={(e) => void save(e)} aria-label="Declare turnover" style={{ display: "flex", flexWrap: "wrap", gap: 12, alignItems: "flex-end" }}>
            <div className="field"><label htmlFor="ei-fy">Financial year</label>
              <select id="ei-fy" className="input" style={{ height: 40 }} value={f.fy} onChange={(e) => setForm({ ...f, fy: e.target.value })}>
                {yearOptions(s.financial_year).map((y) => <option key={y} value={y}>{y}</option>)}
              </select></div>
            <div className="field"><label htmlFor="ei-amt">Turnover (₹)</label><input id="ei-amt" className="input mono" style={{ height: 40 }} inputMode="decimal" value={f.amount} onChange={(e) => setForm({ ...f, amount: e.target.value })} /></div>
            <button type="submit" className="btn primary" style={{ height: 40 }} disabled={busy || !!problem}>{busy ? "Saving…" : "Declare turnover"}</button>
          </form>
          {problem && f.amount !== "" && <span className="t13" role="status">{problem}</span>}
          <ErrorNote error={failure} />
        </>
      )}
    </section>
  );
}
