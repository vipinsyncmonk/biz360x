"use client";
import { useState } from "react";
import { api, newIdempotencyKey, unwrap } from "@/lib/api";
import { formatInr, isoDate, shortDate } from "@/lib/format";
import { useLoad } from "@/lib/hooks";
import { Challan, Deduction, Instalment, Receivable, TdsReturn, deductionTotals, fyChoices, fyOf, instalmentState, nextInstalment, quarterLabel, unmatched } from "@/lib/tds";
import { useSession } from "@/lib/session";
import { ChallanRow, NewChallan, PrepareDeposit, ReturnRow } from "@/components/TdsPanels";
import { JobFollow } from "@/components/JobFollow";
import { ErrorNote, Loading, Pill } from "@/components/ui";

export default function Tds() {
  const { entity } = useSession();
  const id = entity?.id ?? null;
  const today = isoDate(new Date());
  const [fy, setFy] = useState(fyOf());
  const [adding, setAdding] = useState(false);
  const [sync, setSync] = useState<string | null>(null);
  const [syncError, setSyncError] = useState<unknown>(null);

  const q = { path: { entityId: id! } };
  const ded = useLoad(async () => unwrap(await api.GET("/entities/{entityId}/tds-deductions", { params: { ...q, query: { fy, limit: 100 } } })).data as unknown as Deduction[], id ? `${id}|${fy}` : null);
  const chal = useLoad(async () => unwrap(await api.GET("/entities/{entityId}/challans", { params: { ...q, query: { limit: 50 } } })).data as unknown as Challan[], id);
  const rets = useLoad(async () => unwrap(await api.GET("/entities/{entityId}/tds-returns", { params: { ...q, query: { fy, limit: 20 } } })).data as unknown as TdsReturn[], id ? `${id}|${fy}` : null);
  const adv = useLoad(async () => unwrap(await api.GET("/entities/{entityId}/advance-tax", { params: { ...q, query: { fy } } })) as unknown as Instalment[], id ? `${id}|${fy}` : null);
  const rec = useLoad(async () => unwrap(await api.GET("/entities/{entityId}/tds-receivables", { params: { ...q, query: { fy, limit: 100 } } })).data as unknown as Receivable[], id ? `${id}|${fy}` : null);

  const reloadPay = () => { chal.reload(); ded.reload(); adv.reload(); };
  const totals = deductionTotals(ded.data ?? []);
  const next = nextInstalment(adv.data ?? [], today);
  const open = unmatched(rec.data ?? []);

  async function startSync() {
    setSyncError(null);
    try { const j = unwrap(await api.POST("/entities/{entityId}/tds-receivables/sync", { params: q, body: { fy } as never })) as unknown as { id: string }; setSync(j.id); }
    catch (e) { setSyncError(e); }
  }

  return (
    <main className="page">
      <div style={{ display: "flex", flexWrap: "wrap", gap: 16, justifyContent: "space-between", alignItems: "flex-end" }}>
        <div>
          <span className="muted" style={{ fontSize: 14 }}>{entity?.legal_name}</span>
          <h1 className="disp" style={{ margin: "6px 0 0", fontSize: 36 }}>TDS &amp; Advance tax</h1>
        </div>
        <div className="field"><label className="sr" htmlFor="fy">Financial year</label>
          <select id="fy" className="input" style={{ height: 44 }} value={fy} onChange={(e) => setFy(e.target.value)}>{fyChoices().map((y) => <option key={y} value={y}>FY {y}</option>)}</select></div>
      </div>

      <section aria-label="Key numbers" className="kpis">
        <Tile label="TDS deducted" value={formatInr(totals.deducted, { decimals: false })} note={`FY ${fy}`} />
        <Tile label="Not yet on a challan" value={formatInr(totals.unpaid, { decimals: false })} note={`${totals.unpaidCount} ${totals.unpaidCount === 1 ? "deduction" : "deductions"}`} warn={totals.unpaidCount > 0} />
        <Tile label="Advance tax to pay" value={next ? formatInr(next.estimated_tax, { decimals: false }) : "–"} note={next ? `Cumulative by instalment ${next.instalment_no}, ${next.due_date < today ? "was due" : "due"} ${shortDate(next.due_date)}` : "Nothing planned"} warn={!!next && next.due_date < today} />
        <Tile label="Not yet in Form 26AS" value={String(open.length)} note="TDS customers deducted from you" warn={open.length > 0} />
      </section>
      <ErrorNote error={ded.error ?? chal.error ?? rets.error ?? adv.error ?? rec.error} />

      <div className="cols">
        <div className="col-main">
          <section className="card" style={{ overflow: "hidden" }}>
            <div style={{ padding: "14px 20px", borderBottom: "1px solid var(--line-2)", display: "flex", justifyContent: "space-between", alignItems: "center", gap: 12, flexWrap: "wrap" }}>
              <h2 style={{ margin: 0 }}>Challans</h2>
              <button type="button" className="btn" aria-expanded={adding} onClick={() => setAdding(!adding)}>+ New challan</button>
            </div>
            {id && <PrepareDeposit entityId={id} onDone={reloadPay} />}
            {adding && id && <div style={{ padding: 16 }}><NewChallan entityId={id} onDone={() => { setAdding(false); chal.reload(); }} onCancel={() => setAdding(false)} /></div>}
            {chal.loading && !chal.data ? <Loading what="challans" /> : (
              <div className="tablewrap">
                <table style={{ minWidth: 640 }}>
                  <thead><tr><th scope="col">Challan</th><th scope="col" style={{ textAlign: "right" }}>Amount</th><th scope="col">Status</th><th scope="col">Action</th></tr></thead>
                  <tbody>{id && (chal.data ?? []).map((c) => <ChallanRow key={c.id} entityId={id} c={c} onChanged={reloadPay} />)}</tbody>
                </table>
                {(chal.data ?? []).length === 0 && <div className="empty">No challans yet.</div>}
              </div>
            )}
          </section>

          <section className="card" style={{ overflow: "hidden" }}>
            <div style={{ padding: "14px 20px", borderBottom: "1px solid var(--line-2)" }}><h2 style={{ margin: 0 }}>TDS returns</h2></div>
            {rets.loading && !rets.data ? <Loading what="returns" /> : (
              <div className="tablewrap">
                <table style={{ minWidth: 560 }}>
                  <thead><tr><th scope="col">Return</th><th scope="col">Status</th><th scope="col">Action</th></tr></thead>
                  <tbody>{id && (rets.data ?? []).map((r) => <ReturnRow key={r.id} entityId={id} r={r} onChanged={rets.reload} />)}</tbody>
                </table>
                {(rets.data ?? []).length === 0 && <div className="empty">No returns for FY {fy} yet. They appear once the compliance engine prepares them.</div>}
              </div>
            )}
          </section>

          <section className="card" style={{ overflow: "hidden" }}>
            <div style={{ padding: "14px 20px", borderBottom: "1px solid var(--line-2)" }}><h2 style={{ margin: 0 }}>Deductions</h2></div>
            {ded.loading && !ded.data ? <Loading what="deductions" /> : (
              <div className="tablewrap">
                <table style={{ minWidth: 640 }}>
                  <thead><tr><th scope="col">Date</th><th scope="col">PAN</th><th scope="col">Section</th><th scope="col" style={{ textAlign: "right" }}>On</th><th scope="col" style={{ textAlign: "right" }}>TDS</th><th scope="col">Challan</th></tr></thead>
                  <tbody>
                    {(ded.data ?? []).map((d) => (
                      <tr key={d.id}>
                        <td className="mono" style={{ fontSize: 13 }}>{shortDate(d.deducted_on)}</td><td className="mono">{d.deductee_pan}</td><td>{d.section}</td>
                        <td className="mono" style={{ textAlign: "right" }}>{formatInr(d.base_amount)}</td><td className="mono" style={{ textAlign: "right" }}>{formatInr(d.tds_amount)}</td>
                        <td>{d.challan_id ? <Pill tone="ok">On a challan</Pill> : <Pill tone="due">Not paid</Pill>}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
                {(ded.data ?? []).length === 0 && <div className="empty">No TDS deducted in FY {fy}.</div>}
              </div>
            )}
          </section>
        </div>

        <aside className="col-side">
          <section className="card card-pad" style={{ display: "flex", flexDirection: "column", gap: 8 }}>
            <h2 style={{ margin: 0 }}>Advance tax</h2>
            {(adv.data ?? []).length === 0 && !adv.loading && <span className="t13">No estimate is set for FY {fy}. Add the estimated tax in your company facts.</span>}
            {(adv.data ?? []).map((i) => {
              const s = instalmentState(i, today);
              return (
                <div key={i.instalment_no} style={{ borderTop: "1px solid var(--line-2)", paddingTop: 8, display: "flex", justifyContent: "space-between", gap: 8, fontSize: 14 }}>
                  <span>Instalment {i.instalment_no}<span className="t13" style={{ display: "block" }}>by {shortDate(i.due_date)} · {i.cumulative_pct}% cumulative</span></span>
                  <span style={{ textAlign: "right" }}><span className="mono">{formatInr(i.estimated_tax, { decimals: false })}</span><span style={{ display: "block" }}><Pill tone={s.tone}>{s.label}</Pill></span></span>
                </div>
              );
            })}
            <span className="t13">Amounts are cumulative tax due by each date, planned from your estimated tax. Pay one by adding an income tax challan.</span>
          </section>

          <section className="card card-pad" style={{ display: "flex", flexDirection: "column", gap: 8 }}>
            <h2 style={{ margin: 0 }}>TDS customers deducted</h2>
            <span className="t13">Tax your customers deducted before paying you, checked against Form 26AS.</span>
            <span style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
              <button type="button" className="btn" disabled={!!sync} onClick={() => void startSync()}>Fetch Form 26AS</button>
              {sync && <JobFollow jobId={sync} doneLabel="Matched" onFinished={() => { setSync(null); rec.reload(); }} />}
            </span>
            <ErrorNote error={syncError} />
            {(rec.data ?? []).map((r) => (
              <div key={r.id} style={{ borderTop: "1px solid var(--line-2)", paddingTop: 8, display: "flex", justifyContent: "space-between", fontSize: 14 }}>
                <span>Section {r.section}<span className="t13" style={{ display: "block" }}>{quarterLabel(r.quarter)} · FY {r.fy}</span></span>
                <span style={{ textAlign: "right" }}><span className="mono">{formatInr(r.amount)}</span><span style={{ display: "block" }}>{r.seen_in_26as_at ? <Pill tone="ok">In 26AS</Pill> : <Pill tone="due">Not seen</Pill>}</span></span>
              </div>
            ))}
            {!rec.loading && (rec.data ?? []).length === 0 && <span className="t13">None for FY {fy}.</span>}
          </section>
        </aside>
      </div>
    </main>
  );
}

function Tile({ label, value, note, warn }: { label: string; value: string; note: string; warn?: boolean }) {
  return (
    <div className="card kpi">
      <span className="t13">{label}</span>
      <span className="disp v" style={{ fontSize: 28, color: warn ? "var(--amber)" : undefined }}>{value}</span>
      <span className="t13">{note}</span>
    </div>
  );
}
