"use client";
import Link from "next/link";
import { useState } from "react";
import { ApiProblem, api, newIdempotencyKey, unwrap } from "@/lib/api";
import { DOC_TYPE_LABEL, ISSUE_BODY, TABS, ageLabel, docStatus, einvoiceLabel, tabQuery, type DocRow, type TabKey } from "@/lib/billing";
import { formatInr, shortDate } from "@/lib/format";
import { useLoad } from "@/lib/hooks";
import { useSession } from "@/lib/session";
import { ErrorNote, Loading, Pill } from "@/components/ui";

interface Summary { invoiced: string; collected: string; outstanding: string; overdue: string; overdue_count: number; tds_to_claim: string; collected_by_method: Record<string, string> }

const METHOD: Record<string, string> = { upi: "UPI", neft: "NEFT", rtgs: "RTGS", imps: "IMPS", cheque: "Cheque", card: "Card", netbanking: "Net banking", treds: "TReDS", cash: "Cash", adjustment: "Adjustment" };

export default function Billing() {
  const { entity } = useSession();
  const id = entity?.id ?? null;
  const [tab, setTab] = useState<TabKey>("all");
  const [q, setQ] = useState("");
  const [busy, setBusy] = useState<string | null>(null);
  const [failure, setFailure] = useState<unknown>(null);

  const summary = useLoad(async () => unwrap(await api.GET("/entities/{entityId}/sales/summary", { params: { path: { entityId: id! } } })) as unknown as Summary, id);
  const docs = useLoad(async () => {
    const query = { ...tabQuery(tab), ...(q.trim() ? { q: q.trim() } : {}), limit: 100 };
    return unwrap(await api.GET("/entities/{entityId}/sales-documents", { params: { path: { entityId: id! }, query } })).data as unknown as DocRow[];
  }, id ? `${id}|${tab}|${q.trim()}` : null);

  async function issue(d: DocRow) {
    setBusy(d.id); setFailure(null);
    try {
      unwrap(await api.POST("/entities/{entityId}/sales-documents/{id}/issue", { params: { path: { entityId: id!, id: d.id }, header: { "Idempotency-Key": newIdempotencyKey() } }, body: ISSUE_BODY }));
      docs.reload(); summary.reload();
    } catch (e) {
      setFailure(e instanceof ApiProblem ? e : e);
    } finally {
      setBusy(null);
    }
  }

  const s = summary.data;
  const methods = Object.entries(s?.collected_by_method ?? {});
  const maxM = Math.max(...methods.map(([, v]) => Number(v)), 1);        // only used for bar widths, never shown
  const rows = docs.data ?? [];

  return (
    <main className="page">
      <div style={{ display: "flex", flexWrap: "wrap", gap: 16, justifyContent: "space-between", alignItems: "flex-end" }}>
        <div>
          <span className="muted" style={{ fontSize: 14 }}>{entity?.legal_name}</span>
          <h1 className="disp" style={{ margin: "6px 0 0", fontSize: 36 }}>Sales &amp; Billing</h1>
        </div>
        <div style={{ display: "flex", gap: 10, flexWrap: "wrap" }}>
          <Link href="/billing/receivables" className="btn" style={{ display: "inline-flex", alignItems: "center", textDecoration: "none" }}>Collections</Link>
          <Link href="/billing/quotes" className="btn" style={{ display: "inline-flex", alignItems: "center", textDecoration: "none" }}>Quotes</Link>
          <Link href="/billing/new" className="btn primary" style={{ display: "inline-flex", alignItems: "center", textDecoration: "none" }}>+ New invoice</Link>
        </div>
      </div>

      <ErrorNote error={summary.error} />
      <section aria-label="Key numbers" className="kpis">
        <Tile label="Invoiced this FY" value={s ? formatInr(s.invoiced, { decimals: false }) : "…"} note="Issued, net of credit notes" />
        <Tile label="Collected this FY" value={s ? formatInr(s.collected, { decimals: false }) : "…"} note="Confirmed receipts" />
        <Tile label="Outstanding" value={s ? formatInr(s.outstanding, { decimals: false }) : "…"} note="Open balance on issued invoices" />
        <Tile label="Overdue" value={s ? formatInr(s.overdue, { decimals: false }) : "…"} note={s ? `${s.overdue_count} ${s.overdue_count === 1 ? "invoice" : "invoices"}` : ""} warn={!!s && s.overdue_count > 0} />
        <Tile label="TDS to claim" value={s ? formatInr(s.tds_to_claim, { decimals: false }) : "…"} note="Expected 194Q deductions" />
      </section>

      <section className="card" style={{ overflow: "hidden" }}>
        <div style={{ padding: "14px 20px", display: "flex", justifyContent: "space-between", alignItems: "center", gap: 12, flexWrap: "wrap", borderBottom: "1px solid var(--line-2)" }}>
          <div className="seg" role="tablist" aria-label="Invoice status">
            {TABS.map(([k, l]) => <button key={k} type="button" role="tab" aria-selected={tab === k} onClick={() => setTab(k)}>{l}</button>)}
          </div>
          <div className="field" style={{ minWidth: 220 }}>
            <label className="sr" htmlFor="q">Search invoices</label>
            <input id="q" className="input" style={{ height: 40, fontSize: 14 }} placeholder="Search number or customer" value={q} onChange={(e) => setQ(e.target.value)} />
          </div>
        </div>
        <div style={{ padding: "0 20px" }}><ErrorNote error={docs.error ?? failure} /></div>
        {docs.loading ? <Loading what="invoices" /> : (
          <div className="tablewrap">
            <table style={{ minWidth: 860 }}>
              <thead><tr><th scope="col">Invoice</th><th scope="col">Customer</th><th scope="col">Due</th><th scope="col" style={{ textAlign: "right" }}>Amount</th><th scope="col">E-invoice</th><th scope="col">Status</th><th scope="col"><span className="sr">Actions</span></th></tr></thead>
              <tbody>
                {rows.map((d) => {
                  const st = docStatus(d);
                  const credit = d.doc_type === "credit_note";
                  return (
                    <tr key={d.id}>
                      <td>
                        <Link href={`/billing/${d.id}`} className="mono" style={{ fontWeight: 600, color: "var(--ink)", textDecoration: "none" }}>{d.number ?? "Draft"}</Link>
                        <span className="t13" style={{ display: "block" }}>{DOC_TYPE_LABEL[d.doc_type] ?? d.doc_type} · {shortDate(d.doc_date)}</span>
                      </td>
                      <td><span style={{ fontWeight: 500 }}>{d.buyer_legal_name}</span><span className="mono t13" style={{ display: "block" }}>{d.buyer_gstin ?? "Unregistered"}</span></td>
                      <td><span className="mono" style={{ fontSize: 13 }}>{shortDate(d.due_date)}</span>
                        {d.status === "issued" && d.payment_status !== "paid" && <span className="t13" style={{ display: "block", color: d.days_overdue > 0 ? "var(--amber)" : undefined }}>{ageLabel(d.days_overdue, d.due_date)}</span>}</td>
                      <td className="mono" style={{ textAlign: "right", fontWeight: 600, whiteSpace: "nowrap" }}>
                        {credit ? "-" : ""}{formatInr(d.grand_total)}
                        {d.status === "issued" && !credit && d.balance_due !== d.grand_total && <span className="t13" style={{ display: "block", fontWeight: 400 }}>{formatInr(d.balance_due)} due</span>}
                      </td>
                      <td className="t13">{einvoiceLabel(d.irn_status, d.status)}</td>
                      <td><Pill tone={st.tone}>{st.label}</Pill></td>
                      <td style={{ textAlign: "right" }}>
                        {d.status === "draft" && <button type="button" className="btn" style={{ height: 36, fontSize: 13 }} disabled={busy === d.id} onClick={() => void issue(d)}>{busy === d.id ? "Issuing…" : "Issue"}</button>}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
            {rows.length === 0 && <div className="empty">{q.trim() || tab !== "all" ? "No invoices match." : "No invoices yet. Create your first one."}</div>}
          </div>
        )}
      </section>

      <section className="dark" style={{ maxWidth: 560 }}>
        <h2 style={{ margin: 0, fontSize: 17, fontWeight: 600 }}>How customers paid this FY</h2>
        {methods.length === 0 && <span style={{ fontSize: 13, color: "#B7BCC5" }}>No receipts yet.</span>}
        {methods.map(([m, v]) => (
          <div key={m} className="authbar" style={{ gridTemplateColumns: "120px minmax(0, 1fr) 96px", paddingTop: 10, borderTop: "1px solid #23272F" }}>
            <span style={{ fontSize: 13 }}>{METHOD[m] ?? m}</span>
            <div className="track"><div title={`${METHOD[m] ?? m}: ${formatInr(v, { decimals: false })}`} style={{ width: `${Math.round((Number(v) / maxM) * 100)}%` }} /></div>
            <span className="mono" style={{ fontSize: 13, textAlign: "right" }}>{formatInr(v, { decimals: false })}</span>
          </div>
        ))}
      </section>
    </main>
  );
}

function Tile({ label, value, note, warn }: { label: string; value: string; note: string; warn?: boolean }) {
  return (
    <div className="card kpi">
      <span className="t13">{label}</span>
      <span className="disp v" style={{ fontSize: 30, color: warn ? "var(--amber)" : undefined }}>{value}</span>
      <span className="t13">{note}</span>
    </div>
  );
}
