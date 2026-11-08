"use client";
import { useState } from "react";
import { api, newIdempotencyKey, unwrap } from "@/lib/api";
import { CloseTask, Period, Report, BankTxn, closeCounts, confidenceLabel, currentPeriod, isTotalRow, lakh, periodName, returnPeriod } from "@/lib/finance";
import { formatInr, shortDate } from "@/lib/format";
import { useLoad } from "@/lib/hooks";
import { useSession } from "@/lib/session";
import { ErrorNote, Loading, Pill } from "@/components/ui";

interface Summary { revenue: string; gross_margin_pct: number; ebitda: string; cash_in_bank: string; cash_change: string; receivables: string; receivables_overdue: string; runway_months: number | null; net_burn: string | null }

export default function Finance() {
  const { entity } = useSession();
  const id = entity?.id ?? null;
  const [busy, setBusy] = useState<string | null>(null);
  const [failure, setFailure] = useState<unknown>(null);

  const summary = useLoad(async () => unwrap(await api.GET("/entities/{entityId}/finance/summary", { params: { path: { entityId: id! } } })) as unknown as Summary, id);
  const periods = useLoad(async () => unwrap(await api.GET("/entities/{entityId}/periods", { params: { path: { entityId: id! } } })) as unknown as Period[], id);
  const period = periods.data ? currentPeriod(periods.data) : undefined;
  const tasks = useLoad(async () => unwrap(await api.GET("/entities/{entityId}/periods/{id}/close-tasks", { params: { path: { entityId: id!, id: period!.id } } })) as unknown as CloseTask[], id && period ? `${id}|${period.id}` : null);
  const txns = useLoad(async () => unwrap(await api.GET("/entities/{entityId}/bank-transactions", { params: { path: { entityId: id! }, query: { limit: 50 } } })).data as unknown as BankTxn[], id);
  const pl = useLoad(async () => unwrap(await api.GET("/entities/{entityId}/reports/{report}", { params: { path: { entityId: id!, report: "profit-and-loss" } } })) as unknown as Report, id);

  async function act(key: string, run: () => Promise<unknown>, after: () => void) {
    setBusy(key); setFailure(null);
    try { await run(); after(); } catch (e) { setFailure(e); } finally { setBusy(null); }
  }
  const tick = (t: CloseTask) => act(t.id, async () => unwrap(await api.PATCH("/entities/{entityId}/periods/{id}/close-tasks/{taskId}", { params: { path: { entityId: id!, id: period!.id, taskId: t.id } }, body: { done: !t.done_at } })), () => { tasks.reload(); periods.reload(); });
  const lock = () => act("lock", async () => unwrap(await api.POST("/entities/{entityId}/periods/{id}/lock", { params: { path: { entityId: id!, id: period!.id }, header: { "Idempotency-Key": newIdempotencyKey() } } })), () => { periods.reload(); tasks.reload(); });
  const decide = (m: string, verb: "accept" | "reject") => act(m, async () => verb === "accept"
    ? unwrap(await api.POST("/entities/{entityId}/bank-matches/{id}/accept", { params: { path: { entityId: id!, id: m }, header: { "Idempotency-Key": newIdempotencyKey() } } }))
    : unwrap(await api.POST("/entities/{entityId}/bank-matches/{id}/reject", { params: { path: { entityId: id!, id: m } } })), () => { txns.reload(); summary.reload(); });

  const s = summary.data;
  const list = tasks.data ?? [];
  const counts = closeCounts(list);
  const locked = period?.status === "locked";
  const rows = txns.data ?? [];
  const open = rows.filter((t) => t.match_status !== "matched" && t.match_status !== "ignored");
  const r = pl.data;

  return (
    <main className="page">
      <div style={{ display: "flex", flexWrap: "wrap", gap: 16, justifyContent: "space-between", alignItems: "flex-end" }}>
        <div>
          <span className="muted" style={{ fontSize: 14 }}>{entity?.legal_name}{period ? ` · ${periodName(period.period_start)} close` : ""}</span>
          <h1 className="disp" style={{ margin: "6px 0 0", fontSize: 36 }}>Finance &amp; Books</h1>
        </div>
        {period && !locked && <button type="button" className="btn primary" disabled={busy === "lock" || counts.done < counts.total} title={counts.done < counts.total ? "Finish the close checklist first" : undefined} onClick={() => void lock()}>{busy === "lock" ? "Locking…" : `Lock ${periodName(period.period_start).split(" ")[0]}`}</button>}
        {locked && period && <Pill tone="ok">{periodName(period.period_start)} locked</Pill>}
      </div>

      <ErrorNote error={summary.error ?? periods.error} />
      <ErrorNote error={failure} />
      <section aria-label="Key numbers" className="kpis">
        <Tile label="Revenue" value={s ? formatInr(s.revenue, { decimals: false }) : "…"} note="This financial year, posted" />
        <Tile label="Gross margin" value={s ? `${s.gross_margin_pct.toFixed(1)}%` : "…"} note="After purchases" />
        <Tile label="EBITDA" value={s ? formatInr(s.ebitda, { decimals: false }) : "…"} note="Before interest and penalties" />
        <Tile label="Cash in bank" value={s ? formatInr(s.cash_in_bank, { decimals: false }) : "…"} note={s ? `${formatInr(s.cash_change, { decimals: false })} this period` : ""} />
        <Tile label="Receivables" value={s ? formatInr(s.receivables, { decimals: false }) : "…"} note={s ? `${formatInr(s.receivables_overdue, { decimals: false })} overdue` : ""} warn={!!s && s.receivables_overdue !== "0.00"} />
        <Tile label="Runway" value={s ? (s.runway_months === null ? "Not burning" : `${s.runway_months} mo`) : "…"} note={s?.net_burn ? `${formatInr(s.net_burn, { decimals: false })} a month` : "Cash did not fall"} />
      </section>

      <div className="cols">
        <div className="col-main">
          <section className="card" style={{ overflow: "hidden" }}>
            <div style={{ padding: "14px 20px", display: "flex", justifyContent: "space-between", borderBottom: "1px solid var(--line-2)" }}>
              <h2 style={{ margin: 0 }}>Bank reconciliation</h2>
              <span className="t13">{open.length} {open.length === 1 ? "line" : "lines"} left</span>
            </div>
            <div style={{ padding: "0 20px" }}><ErrorNote error={txns.error} /></div>
            {txns.loading ? <Loading what="bank lines" /> : (
              <div className="tablewrap">
                <table style={{ minWidth: 640 }}>
                  <thead><tr><th scope="col">Bank line</th><th scope="col" style={{ textAlign: "right" }}>Amount</th><th scope="col">Suggested match</th><th scope="col"><span className="sr">Action</span></th></tr></thead>
                  <tbody>
                    {rows.map((t) => {
                      const sug = t.suggestions.find((x) => x.status === "suggested");
                      return (
                        <tr key={t.id}>
                          <td><span style={{ fontWeight: 500 }}>{t.counterparty_name ?? t.description}</span><span className="t13" style={{ display: "block" }}>{shortDate(t.txn_date)} · {t.description}</span></td>
                          <td className="mono" style={{ textAlign: "right", whiteSpace: "nowrap" }}>{formatInr(t.amount)}</td>
                          <td>{t.match_status === "matched" ? <Pill tone="ok">Matched</Pill> : sug ? <><span>{sug.label}</span><span className="t13" style={{ display: "block" }}>{confidenceLabel(sug.confidence)}</span></> : <span className="t13">No suggestion</span>}</td>
                          <td style={{ textAlign: "right", whiteSpace: "nowrap" }}>
                            {sug && t.match_status !== "matched" && sug.target_kind === "receipt" && <button type="button" className="btn" style={{ height: 36, fontSize: 13 }} disabled={busy === sug.id} onClick={() => void decide(sug.id, "accept")}>Accept</button>}
                            {sug && t.match_status !== "matched" && <button type="button" className="btn" style={{ height: 36, fontSize: 13, marginLeft: 6 }} disabled={busy === sug.id} onClick={() => void decide(sug.id, "reject")}>Reject</button>}
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
                {rows.length === 0 && <div className="empty">No bank lines yet. Connect an account or import a statement.</div>}
              </div>
            )}
          </section>

          <section className="card" style={{ overflow: "hidden" }}>
            <div style={{ padding: "14px 20px", display: "flex", justifyContent: "space-between", borderBottom: "1px solid var(--line-2)" }}>
              <h2 style={{ margin: 0 }}>Profit &amp; loss</h2>
              <span className="t13">₹ lakh{r?.provisional ? " · provisional" : ""}</span>
            </div>
            <div style={{ padding: "0 20px" }}><ErrorNote error={pl.error} /></div>
            {pl.loading ? <Loading what="the profit and loss" /> : r && (
              <div className="tablewrap">
                <table style={{ minWidth: 520 }}>
                  <thead><tr><th scope="col">Line</th>{r.columns.map((c) => <th key={c} scope="col" style={{ textAlign: "right" }}>{returnPeriod(c)}</th>)}</tr></thead>
                  <tbody>
                    {r.rows.map((row) => (
                      <tr key={row.line} style={isTotalRow(row.line) ? { fontWeight: 700 } : undefined}>
                        <td>{row.line}</td>{row.values.map((v, i) => <td key={i} className="mono" style={{ textAlign: "right" }} title={formatInr(v)}>{lakh(v)}</td>)}
                      </tr>
                    ))}
                  </tbody>
                </table>
                {r.rows.length === 0 && <div className="empty">Nothing is posted for this period yet.</div>}
              </div>
            )}
          </section>
        </div>

        <aside className="col-side">
          <section className="card card-pad" style={{ display: "flex", flexDirection: "column", gap: 4 }}>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline" }}>
              <h2 style={{ margin: 0 }}>Close checklist</h2>
              <span className="mono t13">{counts.done} / {counts.total}</span>
            </div>
            <ErrorNote error={tasks.error} />
            {tasks.loading && <Loading what="the checklist" />}
            {list.map((t) => (
              <label key={t.id} className="chk" style={{ display: "flex", gap: 10, alignItems: "flex-start", padding: "8px 0", borderTop: "1px solid var(--line-2)", fontSize: 14 }}>
                <input type="checkbox" checked={!!t.done_at} disabled={t.is_automatic || locked || busy === t.id} onChange={() => void tick(t)} />
                <span>{t.title}{t.is_automatic && <span className="t13" style={{ display: "block" }}>Computed from the books</span>}</span>
              </label>
            ))}
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
