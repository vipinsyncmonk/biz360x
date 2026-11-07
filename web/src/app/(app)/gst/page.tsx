"use client";
import { useState } from "react";
import { api, unwrap } from "@/lib/api";
import { GstBalance, GstReturn, HEADS, ITC_LABEL, ItcBucket, RETURN_LABEL, RETURN_STATUS, ledgerTotals, returnPeriod, totalOf } from "@/lib/finance";
import { formatInr } from "@/lib/format";
import { useLoad } from "@/lib/hooks";
import { useSession } from "@/lib/session";
import { ErrorNote, Loading, Pill } from "@/components/ui";

interface Reg { id: string; number: string; label: string | null; state_code: string | null }

export default function Gst() {
  const { entity } = useSession();
  const id = entity?.id ?? null;
  const [reg, setReg] = useState("");

  const regs = useLoad(async () => unwrap(await api.GET("/entities/{entityId}/registrations", { params: { path: { entityId: id! }, query: { kind: "gstin" } } })).data as unknown as Reg[], id);
  const returns = useLoad(async () => unwrap(await api.GET("/entities/{entityId}/gst-returns", { params: { path: { entityId: id! }, query: { limit: 50, ...(reg ? { registration_id: reg } : {}) } } })).data as unknown as GstReturn[], id ? `${id}|${reg}` : null);
  const ledgers = useLoad(async () => unwrap(await api.GET("/entities/{entityId}/gst/ledger-balances", { params: { path: { entityId: id! }, query: reg ? { registration_id: reg } : {} } })) as unknown as GstBalance[], id ? `${id}|${reg}` : null);
  const itc = useLoad(async () => unwrap(await api.GET("/entities/{entityId}/itc-matches/summary", { params: { path: { entityId: id! } } })) as unknown as ItcBucket[], id);

  const gstins = regs.data ?? [];
  const label = (rid: string) => { const g = gstins.find((x) => x.id === rid); return g ? `${g.label ?? g.state_code ?? ""} · ${g.number}` : ""; };
  const rows = returns.data ?? [];
  const bal = ledgers.data ?? [];
  const credit = ledgerTotals(bal, "credit"), cash = ledgerTotals(bal, "cash"), liability = ledgerTotals(bal, "liability");
  const buckets = itc.data ?? [];

  return (
    <main className="page">
      <div style={{ display: "flex", flexWrap: "wrap", gap: 16, justifyContent: "space-between", alignItems: "flex-end" }}>
        <div>
          <span className="muted" style={{ fontSize: 14 }}>{entity?.legal_name} · {gstins.length} {gstins.length === 1 ? "GSTIN" : "GSTINs"}</span>
          <h1 className="disp" style={{ margin: "6px 0 0", fontSize: 36 }}>GST &amp; Tax</h1>
        </div>
        <div className="seg" role="tablist" aria-label="GSTIN">
          <button type="button" role="tab" aria-selected={reg === ""} onClick={() => setReg("")}>All</button>
          {gstins.map((g) => <button key={g.id} type="button" role="tab" aria-selected={reg === g.id} onClick={() => setReg(g.id)}>{g.label ?? g.state_code ?? g.number}</button>)}
        </div>
      </div>

      <section className="card" style={{ overflow: "hidden" }}>
        <div style={{ padding: "14px 20px", borderBottom: "1px solid var(--line-2)" }}><h2 style={{ margin: 0 }}>Returns</h2></div>
        <div style={{ padding: "0 20px" }}><ErrorNote error={returns.error ?? regs.error} /></div>
        {returns.loading ? <Loading what="returns" /> : (
          <div className="tablewrap">
            <table style={{ minWidth: 760 }}>
              <thead><tr><th scope="col">Return</th><th scope="col">Period</th><th scope="col" style={{ textAlign: "right" }}>Output tax</th><th scope="col" style={{ textAlign: "right" }}>ITC claimed</th><th scope="col" style={{ textAlign: "right" }}>Cash paid</th><th scope="col">Status</th></tr></thead>
              <tbody>
                {rows.map((r) => {
                  const st = RETURN_STATUS[r.status] ?? { label: r.status, tone: "prep" as const };
                  return (
                    <tr key={r.id}>
                      <td><span style={{ fontWeight: 500 }}>{RETURN_LABEL[r.return_type] ?? r.return_type}</span>{!reg && <span className="mono t13" style={{ display: "block" }}>{label(r.registration_id)}</span>}</td>
                      <td>{returnPeriod(r.period)}</td>
                      <td className="mono" style={{ textAlign: "right" }}>{formatInr(r.output_tax)}</td>
                      <td className="mono" style={{ textAlign: "right" }}>{formatInr(r.itc_claimed)}</td>
                      <td className="mono" style={{ textAlign: "right" }}>{formatInr(r.cash_paid)}</td>
                      <td><Pill tone={st.tone}>{st.label}</Pill>{r.arn && <span className="mono t13" style={{ display: "block" }}>ARN {r.arn}</span>}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
            {rows.length === 0 && <div className="empty">No returns yet. They appear once the compliance engine prepares them.</div>}
          </div>
        )}
      </section>

      <div className="cols">
        <div className="col-main">
          <section className="card card-pad" style={{ display: "flex", flexDirection: "column", gap: 10 }}>
            <h2 style={{ margin: 0 }}>Purchase register vs GSTR-2B</h2>
            <ErrorNote error={itc.error} />
            {itc.loading && <Loading what="the match summary" />}
            {!itc.loading && buckets.length === 0 && <span className="t13">Nothing to match yet.</span>}
            {buckets.map((b) => (
              <div key={b.status} style={{ display: "flex", justifyContent: "space-between", padding: "8px 0", borderTop: "1px solid var(--line-2)", fontSize: 14 }}>
                <span>{ITC_LABEL[b.status] ?? b.status}<span className="t13" style={{ display: "block" }}>{b.count} {b.count === 1 ? "invoice" : "invoices"}</span></span>
                <span className="mono">{formatInr(b.itc)}</span>
              </div>
            ))}
          </section>
        </div>
        <aside className="col-side">
          <section className="card card-pad" style={{ display: "flex", flexDirection: "column", gap: 6 }}>
            <h2 style={{ margin: 0 }}>Electronic ledgers</h2>
            <ErrorNote error={ledgers.error} />
            {([["Credit ledger", credit], ["Cash ledger", cash], ["Liability ledger", liability]] as const).map(([name, t]) => (
              <div key={name} style={{ borderTop: "1px solid var(--line-2)", paddingTop: 8 }}>
                <div style={{ display: "flex", justifyContent: "space-between", fontWeight: 600, fontSize: 14 }}><span>{name}</span><span className="mono">{formatInr(totalOf(t))}</span></div>
                <div className="t13" style={{ display: "flex", gap: 12, flexWrap: "wrap" }}>{HEADS.filter((h) => t[h] !== "0.00").map((h) => <span key={h}>{h.toUpperCase()} {formatInr(t[h], { decimals: false })}</span>)}</div>
              </div>
            ))}
            <span className="t13">Balances as last read from the GST portal.</span>
          </section>
        </aside>
      </div>
    </main>
  );
}
