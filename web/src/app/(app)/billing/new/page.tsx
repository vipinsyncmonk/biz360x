"use client";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useMemo, useState } from "react";
import { ApiProblem, api, newIdempotencyKey, unwrap } from "@/lib/api";
import { GST_RATES, ISSUE_BODY, blankLine, docBody, formReady, hasBlock, lineProblem, type DocForm, type LineDraft } from "@/lib/billing";
import { formatInr, isoDate, shortDate } from "@/lib/format";
import { useLoad } from "@/lib/hooks";
import { useSession } from "@/lib/session";
import { ErrorNote, Loading } from "@/components/ui";

interface Party { id: string; legal_name: string; payment_terms_days: number; gstins: Array<{ id: string; gstin: string | null; state_code: string; is_default: boolean }> }
interface Reg { id: string; number: string; state_code: string; label: string | null }
interface Check { code: string; level: string; title: string; detail: string }
interface Preview {
  lines: Array<{ taxable_value: string; cgst_amount: string; sgst_amount: string; igst_amount: string }>;
  taxable_total: string; cgst_total: string; sgst_total: string; igst_total: string; round_off: string; grand_total: string; amount_in_words: string;
  due_date: string; tds_expected: string; expected_receipt: string; next_number: string | null; is_inter_state: boolean; place_of_supply: string; checks: Check[];
}

export default function NewInvoice() {
  const { entity } = useSession();
  const id = entity?.id ?? null;
  const router = useRouter();
  const [form, setForm] = useState<DocForm>({ registrationId: "", partyId: "", partyGstId: "", docDate: isoDate(new Date()), terms: "", po: "", lines: [blankLine()] });
  const [touched, setTouched] = useState(false);
  const [preview, setPreview] = useState<Preview | null>(null);
  const [previewError, setPreviewError] = useState<unknown>(null);
  const [previewing, setPreviewing] = useState(false);
  const [saving, setSaving] = useState<"draft" | "issue" | null>(null);
  const [error, setError] = useState<unknown>(null);

  const parties = useLoad(async () => unwrap(await api.GET("/entities/{entityId}/parties", { params: { path: { entityId: id! }, query: { role: "customer", limit: 200 } } })).data as unknown as Party[], id);
  const regs = useLoad(async () => unwrap(await api.GET("/entities/{entityId}/registrations", { params: { path: { entityId: id! }, query: { kind: "gstin" } } })).data as unknown as Reg[], id);

  const party = (parties.data ?? []).find((p) => p.id === form.partyId);
  const set = (patch: Partial<DocForm>) => setForm((f) => ({ ...f, ...patch }));

  // sensible defaults once the lists arrive: the only GSTIN, and a customer's default GSTIN and terms
  useEffect(() => { if (!form.registrationId && (regs.data ?? []).length === 1) set({ registrationId: regs.data![0]!.id }); }, [regs.data, form.registrationId]);
  function pickParty(partyId: string) {
    const p = (parties.data ?? []).find((x) => x.id === partyId);
    set({ partyId, partyGstId: p?.gstins.find((g) => g.is_default)?.id ?? p?.gstins[0]?.id ?? "", terms: "" });
  }

  const ready = formReady(form);
  const body = useMemo(() => (ready ? docBody(form) : null), [ready, form]);
  const bodyJson = body ? JSON.stringify(body) : null;

  // live preview: the server does all the tax maths, so the screen and the saved document cannot disagree
  useEffect(() => {
    if (!id || !body) { setPreview(null); return; }
    let cancelled = false;
    setPreviewing(true);
    const t = setTimeout(async () => {
      try {
        const p = unwrap(await api.POST("/entities/{entityId}/sales-documents/preview", { params: { path: { entityId: id } }, body })) as unknown as Preview;
        if (!cancelled) { setPreview(p); setPreviewError(null); }
      } catch (e) {
        if (!cancelled) { setPreview(null); setPreviewError(e); }
      } finally {
        if (!cancelled) setPreviewing(false);
      }
    }, 300);
    return () => { cancelled = true; clearTimeout(t); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [id, bodyJson]);

  async function save(issue: boolean) {
    if (!id || !body) return;
    setSaving(issue ? "issue" : "draft"); setError(null);
    try {
      const doc = unwrap(await api.POST("/entities/{entityId}/sales-documents", { params: { path: { entityId: id } }, body })) as unknown as { id: string };
      if (issue) {
        unwrap(await api.POST("/entities/{entityId}/sales-documents/{id}/issue", { params: { path: { entityId: id, id: doc.id }, header: { "Idempotency-Key": newIdempotencyKey() } }, body: ISSUE_BODY }));
      }
      router.push(`/billing/${doc.id}`);
    } catch (e) {
      setError(e);
      setSaving(null);
    }
  }

  const setLine = (i: number, patch: Partial<LineDraft>) => set({ lines: form.lines.map((l, j) => (j === i ? { ...l, ...patch } : l)) });
  const problems = form.lines.map(lineProblem);
  const blocked = !!preview && hasBlock(preview.checks);

  if (!entity) return <main className="page"><div className="card card-pad">Select a workspace first.</div></main>;

  return (
    <main className="page">
      <div>
        <Link href="/billing" style={{ fontSize: 14 }}>← Sales &amp; Billing</Link>
        <h1 className="disp" style={{ margin: "8px 0 0", fontSize: 36 }}>New tax invoice</h1>
      </div>
      <ErrorNote error={parties.error ?? regs.error} />
      <div className="cols">
        <div className="col-main">
          <section className="card card-pad" style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(220px, 1fr))", gap: 16 }}>
            <div className="field"><label htmlFor="reg">Supplier GSTIN</label>
              <select id="reg" className="input" value={form.registrationId} onChange={(e) => set({ registrationId: e.target.value })}>
                <option value="">Choose…</option>
                {(regs.data ?? []).map((r) => <option key={r.id} value={r.id}>{r.number}{r.label ? ` · ${r.label}` : ""}</option>)}
              </select></div>
            <div className="field"><label htmlFor="party">Customer</label>
              <select id="party" className="input" value={form.partyId} onChange={(e) => pickParty(e.target.value)}>
                <option value="">Choose…</option>
                {(parties.data ?? []).map((p) => <option key={p.id} value={p.id}>{p.legal_name}</option>)}
              </select></div>
            <div className="field"><label htmlFor="pg">Customer GSTIN</label>
              <select id="pg" className="input" value={form.partyGstId} onChange={(e) => set({ partyGstId: e.target.value })} disabled={!party}>
                <option value="">{party && party.gstins.length === 0 ? "Unregistered" : "Choose…"}</option>
                {(party?.gstins ?? []).map((g) => <option key={g.id} value={g.id}>{g.gstin ?? "Unregistered"}</option>)}
              </select></div>
            <div className="field"><label htmlFor="dt">Invoice date</label><input id="dt" type="date" className="input" value={form.docDate} onChange={(e) => set({ docDate: e.target.value })} /></div>
            <div className="field"><label htmlFor="terms">Payment terms (days)</label>
              <input id="terms" className="input mono" inputMode="numeric" value={form.terms} placeholder={party ? String(party.payment_terms_days) : "30"} onChange={(e) => set({ terms: e.target.value.replace(/\D/g, "") })} /></div>
            <div className="field"><label htmlFor="po">Customer PO reference</label><input id="po" className="input" value={form.po} onChange={(e) => set({ po: e.target.value })} /></div>
          </section>

          <section className="card" style={{ overflow: "hidden" }}>
            <div className="cardhead"><h2>Items</h2><button type="button" className="btn" onClick={() => set({ lines: [...form.lines, blankLine()] })}>+ Add item</button></div>
            <div className="tablewrap">
              <table style={{ minWidth: 820 }}>
                <thead><tr><th scope="col">Description</th><th scope="col">HSN / SAC</th><th scope="col">Qty</th><th scope="col">Rate (₹)</th><th scope="col">Discount (₹)</th><th scope="col">GST %</th><th scope="col"><span className="sr">Remove</span></th></tr></thead>
                <tbody>
                  {form.lines.map((l, i) => (
                    <tr key={i}>
                      <td style={{ minWidth: 220 }}>
                        <input aria-label={`Description, item ${i + 1}`} className="input" style={{ height: 40, fontSize: 14 }} value={l.description} onChange={(e) => setLine(i, { description: e.target.value })} />
                        {touched && problems[i] && <span className="t13" style={{ color: "var(--amber)" }}>{problems[i]}</span>}
                      </td>
                      <td><input aria-label={`HSN or SAC, item ${i + 1}`} className="input mono" style={{ height: 40, fontSize: 14, width: 110 }} inputMode="numeric" value={l.hsn_sac} onChange={(e) => setLine(i, { hsn_sac: e.target.value.replace(/\D/g, "").slice(0, 8) })} /></td>
                      <td><input aria-label={`Quantity, item ${i + 1}`} className="input mono" style={{ height: 40, fontSize: 14, width: 80 }} inputMode="decimal" value={l.qty} onChange={(e) => setLine(i, { qty: e.target.value })} /></td>
                      <td><input aria-label={`Rate, item ${i + 1}`} className="input mono" style={{ height: 40, fontSize: 14, width: 120 }} inputMode="decimal" value={l.rate} onChange={(e) => setLine(i, { rate: e.target.value })} /></td>
                      <td><input aria-label={`Discount, item ${i + 1}`} className="input mono" style={{ height: 40, fontSize: 14, width: 100 }} inputMode="decimal" value={l.discount} onChange={(e) => setLine(i, { discount: e.target.value })} /></td>
                      <td><select aria-label={`GST rate, item ${i + 1}`} className="input" style={{ height: 40, fontSize: 14, width: 80 }} value={l.gst_rate} onChange={(e) => setLine(i, { gst_rate: e.target.value })}>{GST_RATES.map((r) => <option key={r} value={r}>{r}%</option>)}</select></td>
                      <td>{form.lines.length > 1 && <button type="button" className="btn" style={{ height: 36 }} aria-label={`Remove item ${i + 1}`} onClick={() => set({ lines: form.lines.filter((_, j) => j !== i) })}>✕</button>}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </section>
          <ErrorNote error={error instanceof ApiProblem ? new Error(error.fieldErrors.length ? `${error.title}: ${error.fieldErrors.map((f) => f.message).join("; ")}` : error.message) : error} />
        </div>

        <aside className="col-side" aria-live="polite">
          <section className="card card-pad" style={{ display: "flex", flexDirection: "column", gap: 12 }}>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline" }}>
              <h2>Summary</h2>
              <span className="mono t13">{preview?.next_number ? `Next no. ${preview.next_number}` : ""}</span>
            </div>
            {!ready && <span className="t13">Choose a GSTIN and a customer and complete every item to see the tax.</span>}
            {ready && previewing && !preview && <Loading what="the tax" />}
            <ErrorNote error={previewError} />
            {preview && (
              <>
                <Row k="Taxable value" v={formatInr(preview.taxable_total)} />
                {preview.is_inter_state ? <Row k="IGST" v={formatInr(preview.igst_total)} /> : <><Row k="CGST" v={formatInr(preview.cgst_total)} /><Row k="SGST" v={formatInr(preview.sgst_total)} /></>}
                {preview.round_off !== "0.00" && <Row k="Round off" v={formatInr(preview.round_off)} />}
                <div style={{ display: "flex", justifyContent: "space-between", paddingTop: 12, borderTop: "1px solid var(--line)", fontWeight: 700, fontSize: 18 }}>
                  <span>Total</span><span className="mono">{formatInr(preview.grand_total)}</span>
                </div>
                <span className="t13">{preview.amount_in_words}</span>
                <span className="t13">Due {shortDate(preview.due_date)}{preview.tds_expected !== "0.00" ? ` · expect ${formatInr(preview.expected_receipt)} after 0.1% TDS` : ""}</span>
                <div style={{ display: "flex", flexDirection: "column", gap: 8, paddingTop: 8, borderTop: "1px solid var(--line-2)" }}>
                  {preview.checks.filter((c) => c.level !== "ok" || true).map((c) => (
                    <div key={c.code} style={{ display: "flex", gap: 8, fontSize: 13, lineHeight: 1.4 }}>
                      <span aria-hidden="true" style={{ color: c.level === "block" || c.level === "warn" ? "var(--amber)" : "var(--green)" }}>{c.level === "ok" ? "✓" : "!"}</span>
                      <span><strong>{c.title}</strong> <span className="muted">{c.detail}</span></span>
                    </div>
                  ))}
                </div>
              </>
            )}
            <div style={{ display: "flex", gap: 10, flexWrap: "wrap", paddingTop: 8 }}>
              <button type="button" className="btn" disabled={!!saving || !ready || blocked} onClick={() => void save(false)} onFocus={() => setTouched(true)}>{saving === "draft" ? "Saving…" : "Save draft"}</button>
              <button type="button" className="btn primary" disabled={!!saving || !ready || blocked} onClick={() => void save(true)}>{saving === "issue" ? "Issuing…" : "Issue invoice"}</button>
            </div>
            {!ready && <button type="button" className="linkbtn" style={{ color: "var(--cobalt)" }} onClick={() => setTouched(true)}>What is missing?</button>}
            <span className="t13">Issuing takes the next number, posts the books and sends the invoice for its IRN. It cannot be edited afterwards; use a credit note.</span>
          </section>
        </aside>
      </div>
    </main>
  );
}

function Row({ k, v }: { k: string; v: string }) {
  return <div style={{ display: "flex", justifyContent: "space-between", fontSize: 14 }}><span className="muted">{k}</span><span className="mono">{v}</span></div>;
}
