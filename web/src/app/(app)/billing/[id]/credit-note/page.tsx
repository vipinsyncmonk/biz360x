"use client";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { use, useEffect, useMemo, useState } from "react";
import { api, newIdempotencyKey, unwrap } from "@/lib/api";
import { CN_REASONS, DocLine, ISSUE_BODY, creditLines, creditProblem, creditReady, hasBlock } from "@/lib/billing";
import { formatInr, isoDate } from "@/lib/format";
import { useLoad } from "@/lib/hooks";
import { useSession } from "@/lib/session";
import { ErrorNote, Loading } from "@/components/ui";

interface Doc { id: string; doc_type: string; number: string | null; status: string; buyer_legal_name: string; registration_id: string; party_id: string; party_gst_id: string | null; place_of_supply: string; grand_total: string; balance_due: string; lines: DocLine[] }
interface Check { code: string; level: string; title: string; detail: string }
interface Preview { is_export: boolean; currency: string; foreign_total: string | null; taxable_total: string; cgst_total: string; sgst_total: string; igst_total: string; grand_total: string; is_inter_state: boolean; checks: Check[] }

export default function CreditNote({ params }: { params: Promise<{ id: string }> }) {
  const { id: docId } = use(params);
  const { entity } = useSession();
  const id = entity?.id ?? null;
  const router = useRouter();
  const [reason, setReason] = useState("sales_return");
  const [agreed, setAgreed] = useState(false);
  const [entered, setEntered] = useState<Record<number, string>>({});
  const [preview, setPreview] = useState<Preview | null>(null);
  const [previewError, setPreviewError] = useState<unknown>(null);
  const [saving, setSaving] = useState<"draft" | "issue" | null>(null);
  const [error, setError] = useState<unknown>(null);

  const doc = useLoad(async () => unwrap(await api.GET("/entities/{entityId}/sales-documents/{id}", { params: { path: { entityId: id!, id: docId } } })) as unknown as Doc, id ? `${id}|${docId}` : null);
  const d = doc.data;
  const lines = creditLines(d?.lines ?? [], entered);
  const ready = !!d && creditReady(d.lines, entered);
  const body = useMemo(() => (d && ready ? {
    doc_type: "credit_note" as const, doc_date: isoDate(new Date()), registration_id: d.registration_id, party_id: d.party_id, ...(d.party_gst_id ? { party_gst_id: d.party_gst_id } : {}),
    place_of_supply: d.place_of_supply, original_document_id: d.id, cn_reason: reason, gst_adjusted: true, payment_terms_days: 0, lines,
  } : null), [d, ready, reason, entered]);   // eslint-disable-line react-hooks/exhaustive-deps
  const bodyJson = body ? JSON.stringify(body) : null;

  useEffect(() => {
    if (!id || !body) { setPreview(null); return; }
    let cancelled = false;
    const t = setTimeout(async () => {
      try {
        const p = unwrap(await api.POST("/entities/{entityId}/sales-documents/preview", { params: { path: { entityId: id } }, body: body as never })) as unknown as Preview;
        if (!cancelled) { setPreview(p); setPreviewError(null); }
      } catch (e) {
        if (!cancelled) { setPreview(null); setPreviewError(e); }
      }
    }, 300);
    return () => { cancelled = true; clearTimeout(t); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [id, bodyJson]);

  async function save(issue: boolean) {
    if (!id || !d || !ready) return;
    setSaving(issue ? "issue" : "draft"); setError(null);
    try {
      const cn = unwrap(await api.POST("/entities/{entityId}/sales-documents/{id}/credit-notes", { params: { path: { entityId: id, id: d.id } },
        body: { cn_reason: reason, gst_adjusted: true, ...(reason === "post_sale_discount" ? { agreed_before_supply: agreed } : {}), lines } as never })) as unknown as { id: string };
      if (issue) unwrap(await api.POST("/entities/{entityId}/sales-documents/{id}/issue", { params: { path: { entityId: id, id: cn.id }, header: { "Idempotency-Key": newIdempotencyKey() } }, body: ISSUE_BODY }));
      router.push(`/billing/${cn.id}`);
    } catch (e) {
      setError(e);
      setSaving(null);
    }
  }

  const blocked = !!preview && hasBlock(preview.checks);
  const eligible = !d || (d.status === "issued" && ["tax_invoice", "export_invoice", "bill_of_supply"].includes(d.doc_type));

  return (
    <main className="page">
      <div>
        <Link href={`/billing/${docId}`} style={{ fontSize: 14 }}>← Invoice {d?.number ?? ""}</Link>
        <h1 className="disp" style={{ margin: "8px 0 0", fontSize: 36 }}>Credit note{d ? ` for ${d.buyer_legal_name}` : ""}</h1>
      </div>
      <ErrorNote error={doc.error} />
      {doc.loading && <Loading what="the invoice" />}
      {d && !eligible && <div className="card card-pad">A credit note can only be raised against an issued invoice.</div>}
      {d && eligible && (
        <div className="cols">
          <div className="col-main">
            <section className="card card-pad" style={{ display: "flex", flexDirection: "column", gap: 10 }}>
              <h2 style={{ margin: 0 }}>Why are you issuing this?</h2>
              <div role="radiogroup" aria-label="Reason" style={{ display: "grid", gap: 8 }}>
                {CN_REASONS.map(([k, label, hint]) => (
                  <label key={k} style={{ display: "flex", gap: 10, alignItems: "flex-start", padding: "10px 12px", border: `1px solid ${reason === k ? "var(--ink)" : "var(--line)"}`, borderRadius: 10, cursor: "pointer" }}>
                    <input type="radio" name="reason" checked={reason === k} onChange={() => setReason(k)} />
                    <span>{label}<span className="t13" style={{ display: "block" }}>{hint}</span></span>
                  </label>
                ))}
              </div>
              {reason === "post_sale_discount" && (
                <label style={{ display: "flex", gap: 10, alignItems: "center", fontSize: 14 }}>
                  <input type="checkbox" checked={agreed} onChange={(e) => setAgreed(e.target.checked)} /> The discount was agreed before the supply and is linked to this invoice
                </label>
              )}
            </section>

            <section className="card" style={{ overflow: "hidden" }}>
              <div className="cardhead"><h2>What is being credited</h2></div>
              <div className="tablewrap">
                <table style={{ minWidth: 640 }}>
                  <thead><tr><th scope="col">Item</th><th scope="col" style={{ textAlign: "right" }}>Supplied</th><th scope="col" style={{ textAlign: "right" }}>Rate</th><th scope="col">Quantity to credit</th></tr></thead>
                  <tbody>
                    {d.lines.map((l) => {
                      const problem = creditProblem(entered[l.line_no] ?? "", l.qty);
                      return (
                        <tr key={l.line_no}>
                          <td>{l.description}<span className="mono t13" style={{ display: "block" }}>{l.hsn_sac}</span></td>
                          <td className="mono" style={{ textAlign: "right" }}>{l.qty} {l.uqc}</td>
                          <td className="mono" style={{ textAlign: "right" }}>{formatInr(l.rate)}</td>
                          <td>
                            <input aria-label={`Quantity to credit, item ${l.line_no}`} className="input mono" style={{ height: 40, fontSize: 14, width: 110 }} inputMode="decimal" placeholder="0"
                                   value={entered[l.line_no] ?? ""} onChange={(e) => setEntered({ ...entered, [l.line_no]: e.target.value })} />
                            {problem && <span className="t13" style={{ display: "block", color: "var(--amber)" }}>{problem}</span>}
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            </section>
            <ErrorNote error={error} />
          </div>

          <aside className="col-side" aria-live="polite">
            <section className="card card-pad" style={{ display: "flex", flexDirection: "column", gap: 12 }}>
              <h2 style={{ margin: 0 }}>What changes when you issue it</h2>
              {!ready && <span className="t13">Enter the quantity to credit for at least one item.</span>}
              <ErrorNote error={previewError} />
              {preview && (
                <>
                  <Row k="Taxable value" v={`-${formatInr(preview.taxable_total)}`} />
                  {preview.is_inter_state ? <Row k="IGST" v={`-${formatInr(preview.igst_total)}`} /> : <><Row k="CGST" v={`-${formatInr(preview.cgst_total)}`} /><Row k="SGST" v={`-${formatInr(preview.sgst_total)}`} /></>}
                  <div style={{ display: "flex", justifyContent: "space-between", paddingTop: 12, borderTop: "1px solid var(--line)", fontWeight: 700, fontSize: 18 }}><span>Credit total</span><span className="mono">-{formatInr(preview.grand_total)}</span></div>
                  {preview.is_export && preview.foreign_total && <Row k={`Customer is credited (${preview.currency})`} v={`-${preview.currency} ${Number(preview.foreign_total).toLocaleString("en-US", { minimumFractionDigits: 2 })}`} />}
                  <span className="t13">The customer owes {formatInr(d.balance_due)} on this invoice now; the credit reduces that and your output GST.</span>
                  {preview.checks.map((c) => (
                    <div key={c.code} style={{ display: "flex", gap: 8, fontSize: 13, lineHeight: 1.4 }}>
                      <span aria-hidden="true" style={{ color: c.level === "block" || c.level === "warn" ? "var(--amber)" : "var(--green)" }}>{c.level === "ok" ? "✓" : "!"}</span>
                      <span><strong>{c.title}</strong> <span className="muted">{c.detail}</span></span>
                    </div>
                  ))}
                </>
              )}
              <div style={{ display: "flex", gap: 10, flexWrap: "wrap" }}>
                <button type="button" className="btn" disabled={!!saving || !ready || blocked} onClick={() => void save(false)}>{saving === "draft" ? "Saving…" : "Save draft"}</button>
                <button type="button" className="btn primary" disabled={!!saving || !ready || blocked} onClick={() => void save(true)}>{saving === "issue" ? "Issuing…" : "Issue credit note"}</button>
              </div>
              <span className="t13">An issued credit note takes the next number and cannot be edited.</span>
            </section>
          </aside>
        </div>
      )}
    </main>
  );
}

function Row({ k, v }: { k: string; v: string }) {
  return <div style={{ display: "flex", justifyContent: "space-between", fontSize: 14 }}><span className="muted">{k}</span><span className="mono">{v}</span></div>;
}
