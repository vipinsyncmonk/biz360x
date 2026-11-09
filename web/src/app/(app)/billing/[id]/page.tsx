"use client";
import Link from "next/link";
import { use, useState } from "react";
import { ApiProblem, api, newIdempotencyKey, unwrap } from "@/lib/api";
import { DOC_TYPE_LABEL, ISSUE_BODY, docStatus, einvoiceLabel, type DocRow } from "@/lib/billing";
import { formatInr, shortDate } from "@/lib/format";
import { useLoad } from "@/lib/hooks";
import { useSession } from "@/lib/session";
import { ErrorNote, Loading, Pill } from "@/components/ui";

interface Line { line_no: number; description: string; hsn_sac: string; qty: string; uqc: string; rate: string; discount: string; taxable_value: string; gst_rate: string | number; cgst_amount: string; sgst_amount: string; igst_amount: string }
interface Doc extends Omit<DocRow, "days_overdue" | "irn_status"> {
  lines: Line[]; cgst_total: string; sgst_total: string; igst_total: string; round_off: string; place_of_supply: string; payment_terms_days: number | null;
  einvoice: { status: string; irn: string | null; ack_no: string | null }; cn_reason: string | null; currency: string; exchange_rate: string | null; foreign_total: string | null; supply_type: string; tds_expected: string; original_document_id: string | null;
}

export default function InvoiceDetail({ params }: { params: Promise<{ id: string }> }) {
  const { id: docId } = use(params);
  const { entity } = useSession();
  const id = entity?.id ?? null;
  const [busy, setBusy] = useState(false);
  const [failure, setFailure] = useState<unknown>(null);
  const doc = useLoad(async () => unwrap(await api.GET("/entities/{entityId}/sales-documents/{id}", { params: { path: { entityId: id!, id: docId } } })) as unknown as Doc, id ? `${id}|${docId}` : null);

  async function issue() {
    setBusy(true); setFailure(null);
    try {
      unwrap(await api.POST("/entities/{entityId}/sales-documents/{id}/issue", { params: { path: { entityId: id!, id: docId }, header: { "Idempotency-Key": newIdempotencyKey() } }, body: ISSUE_BODY }));
      doc.reload();
    } catch (e) {
      setFailure(e);
    } finally {
      setBusy(false);
    }
  }

  const d = doc.data;
  const inter = d ? Number(d.igst_total) > 0 : false;       // only chooses which tax columns to show
  const st = d && d.doc_type === "credit_note" && d.status === "issued" ? { label: "Issued", tone: "ok" as const } : d ? docStatus({ status: d.status, payment_status: d.payment_status, days_overdue: 0, irn_status: d.einvoice.status }) : null;

  return (
    <main className="page">
      <div><Link href="/billing" style={{ fontSize: 14 }}>← Sales &amp; Billing</Link></div>
      <ErrorNote error={doc.error instanceof ApiProblem && doc.error.status === 404 ? new Error("This document was not found.") : doc.error} />
      {doc.loading && <Loading what="the invoice" />}
      {d && st && (
        <>
          <div style={{ display: "flex", flexWrap: "wrap", gap: 16, justifyContent: "space-between", alignItems: "flex-end" }}>
            <div>
              <span className="muted" style={{ fontSize: 14 }}>{DOC_TYPE_LABEL[d.doc_type] ?? d.doc_type} · {shortDate(d.doc_date)}{d.original_document_id && <> · against <Link href={`/billing/${d.original_document_id}`}>the original invoice</Link></>}{d.cn_reason ? ` · ${d.cn_reason.replace(/_/g, " ")}` : ""}</span>
              <h1 className="disp mono" style={{ margin: "6px 0 0", fontSize: 34 }}>{d.number ?? "Draft"}</h1>
            </div>
            <div style={{ display: "flex", gap: 10, alignItems: "center", flexWrap: "wrap" }}>
              <Pill tone={st.tone}>{st.label}</Pill>
              {d.status === "issued" && ["tax_invoice", "export_invoice", "bill_of_supply"].includes(d.doc_type) && <Link href={`/billing/${docId}/credit-note`} className="btn" style={{ display: "inline-flex", alignItems: "center", textDecoration: "none" }}>Issue credit note</Link>}
              {d.status === "draft" && <button type="button" className="btn primary" disabled={busy} onClick={() => void issue()}>{busy ? "Issuing…" : "Issue invoice"}</button>}
            </div>
          </div>
          <ErrorNote error={failure} />
          <div className="cols">
            <div className="col-main">
              <section className="card" style={{ overflow: "hidden" }}>
                <div className="tablewrap">
                  <table style={{ minWidth: 720 }}>
                    <thead><tr><th scope="col">#</th><th scope="col">Item</th><th scope="col">HSN / SAC</th><th scope="col" style={{ textAlign: "right" }}>Qty</th><th scope="col" style={{ textAlign: "right" }}>Rate</th><th scope="col" style={{ textAlign: "right" }}>Taxable</th><th scope="col" style={{ textAlign: "right" }}>GST</th></tr></thead>
                    <tbody>
                      {d.lines.map((l) => (
                        <tr key={l.line_no}>
                          <td className="mono">{l.line_no}</td><td>{l.description}</td><td className="mono">{l.hsn_sac}</td>
                          <td className="mono" style={{ textAlign: "right" }}>{l.qty} {l.uqc}</td><td className="mono" style={{ textAlign: "right" }}>{formatInr(l.rate)}</td>
                          <td className="mono" style={{ textAlign: "right" }}>{formatInr(l.taxable_value)}</td>
                          <td className="mono" style={{ textAlign: "right" }}>{l.gst_rate}%</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </section>
            </div>
            <aside className="col-side">
              <section className="card card-pad" style={{ display: "flex", flexDirection: "column", gap: 10 }}>
                <h2>{d.buyer_legal_name}</h2>
                <span className="mono t13">{d.doc_type === "export_invoice" || d.currency !== "INR" ? "Customer outside India · place of supply outside India" : <>{d.buyer_gstin ?? "Unregistered buyer"} · place of supply {d.place_of_supply}</>}</span>
                <Row k="Taxable value" v={formatInr(d.taxable_total)} />
                {inter ? <Row k="IGST" v={formatInr(d.igst_total)} /> : <><Row k="CGST" v={formatInr(d.cgst_total)} /><Row k="SGST" v={formatInr(d.sgst_total)} /></>}
                {d.round_off !== "0.00" && <Row k="Round off" v={formatInr(d.round_off)} />}
                <div style={{ display: "flex", justifyContent: "space-between", paddingTop: 10, borderTop: "1px solid var(--line)", fontWeight: 700, fontSize: 18 }}><span>Total</span><span className="mono">{formatInr(d.grand_total)}</span></div>
                {d.foreign_total && <Row k={d.doc_type === "credit_note" ? `Customer is credited (${d.currency})` : `Customer pays (${d.currency})`} v={`${d.currency} ${Number(d.foreign_total).toLocaleString("en-US", { minimumFractionDigits: 2 })}`} />}
                {d.status === "issued" && <><Row k="Settled" v={formatInr(d.amount_settled)} /><Row k="Balance due" v={formatInr(d.balance_due)} /></>}
                <span className="t13">Due {shortDate(d.due_date)}{d.payment_terms_days != null ? ` · ${d.payment_terms_days} day terms` : ""}</span>
              </section>
              {(d.doc_type === "export_invoice" || d.currency !== "INR") && (
                <section className="card card-pad" style={{ display: "flex", flexDirection: "column", gap: 8 }} aria-label="Export">
                  <h2>{d.doc_type === "export_invoice" ? "Export" : "On an export invoice"}</h2>
                  <span style={{ fontSize: 14 }}>{d.supply_type === "export_under_lut" ? "Supplied under LUT: no GST charged." : "Exported with payment of IGST, claimed back as a refund."}</span>
                  {d.exchange_rate && <span className="t13">Converted at ₹{d.exchange_rate} per {d.currency}.</span>}
                  {d.status === "issued" && d.doc_type === "export_invoice" && <Link href="/trade" style={{ fontSize: 14 }}>Shipping bill and proceeds on Exports &amp; imports →</Link>}
                </section>
              )}
              <section className="card card-pad" style={{ display: "flex", flexDirection: "column", gap: 8 }}>
                <h2>E-invoice</h2>
                <span style={{ fontSize: 14 }}>{einvoiceLabel(d.einvoice.status, d.status)}</span>
                {d.einvoice.irn && <><span className="t13">IRN</span><span className="mono" style={{ fontSize: 12, wordBreak: "break-all" }}>{d.einvoice.irn}</span></>}
                {d.einvoice.ack_no && <span className="t13">Ack no. {d.einvoice.ack_no}</span>}
              </section>
            </aside>
          </div>
        </>
      )}
    </main>
  );
}

function Row({ k, v }: { k: string; v: string }) {
  return <div style={{ display: "flex", justifyContent: "space-between", fontSize: 14 }}><span className="muted">{k}</span><span className="mono">{v}</span></div>;
}
