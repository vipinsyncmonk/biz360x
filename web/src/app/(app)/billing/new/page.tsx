"use client";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useMemo, useState } from "react";
import { ApiProblem, api, newIdempotencyKey, unwrap } from "@/lib/api";
import { ISSUE_BODY, blankLine, docBody, formReady, hasBlock, type DocForm } from "@/lib/billing";
import { formatInr, isoDate, shortDate } from "@/lib/format";
import { useLoad } from "@/lib/hooks";
import { useSession } from "@/lib/session";
import { LineEditor } from "@/components/LineEditor";
import { NewCustomer } from "@/components/ExportForms";
import { countryName, rateProblem } from "@/lib/export";
import { ErrorNote, Loading } from "@/components/ui";

interface Party { id: string; legal_name: string; payment_terms_days: number; country: string; currency: string; gstins: Array<{ id: string; gstin: string | null; state_code: string; is_default: boolean }> }
interface Reg { id: string; number: string; state_code: string; label: string | null }
interface Check { code: string; level: string; title: string; detail: string }
interface Preview {
  lines: Array<{ taxable_value: string; cgst_amount: string; sgst_amount: string; igst_amount: string }>;
  taxable_total: string; cgst_total: string; sgst_total: string; igst_total: string; round_off: string; grand_total: string; amount_in_words: string;
  due_date: string; tds_expected: string; expected_receipt: string; next_number: string | null; is_inter_state: boolean; place_of_supply: string; checks: Check[];
  doc_type: string; is_export: boolean; currency: string; exchange_rate: string | null; foreign_total: string | null; supply_type: string;
  priced_in_foreign: boolean; foreign_taxable_total: string | null; amount_in_words_foreign: string | null;
}

const fmtForeign = (ccy: string, v: string) => `${ccy} ${Number(v).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;

export default function NewInvoice() {
  const { entity } = useSession();
  const id = entity?.id ?? null;
  const router = useRouter();
  const [form, setForm] = useState<DocForm>({ registrationId: "", partyId: "", partyGstId: "", docDate: isoDate(new Date()), terms: "", po: "", lines: [blankLine()], currency: "", rate: "", priceIn: "foreign" });
  const [newCustomer, setNewCustomer] = useState(false);
  const [pendingParty, setPendingParty] = useState<string | null>(null);
  const [touched, setTouched] = useState(false);
  const [preview, setPreview] = useState<Preview | null>(null);
  const [previewError, setPreviewError] = useState<unknown>(null);
  const [previewing, setPreviewing] = useState(false);
  const [saving, setSaving] = useState<"draft" | "issue" | null>(null);
  const [error, setError] = useState<unknown>(null);

  const parties = useLoad(async () => unwrap(await api.GET("/entities/{entityId}/parties", { params: { path: { entityId: id! }, query: { role: "customer", limit: 200 } } })).data as unknown as Party[], id);
  const regs = useLoad(async () => unwrap(await api.GET("/entities/{entityId}/registrations", { params: { path: { entityId: id! }, query: { kind: "gstin" } } })).data as unknown as Reg[], id);

  const party = (parties.data ?? []).find((p) => p.id === form.partyId);
  const overseas = !!party && party.country !== "IN";                         // the server decides what is an export; this only shows the currency and rate it needs
  const set = (patch: Partial<DocForm>) => setForm((f) => ({ ...f, ...patch }));

  // sensible defaults once the lists arrive: the only GSTIN, and a customer's default GSTIN and terms
  useEffect(() => { if (!form.registrationId && (regs.data ?? []).length === 1) set({ registrationId: regs.data![0]!.id }); }, [regs.data, form.registrationId]);
  function pickParty(partyId: string) {
    const p = (parties.data ?? []).find((x) => x.id === partyId);
    const abroad = !!p && p.country !== "IN";
    set({ partyId, partyGstId: abroad ? "" : p?.gstins.find((g) => g.is_default)?.id ?? p?.gstins[0]?.id ?? "", terms: "", currency: abroad ? p!.currency : "", rate: "", priceIn: "foreign" });
  }
  // a customer added from this screen is picked as soon as the list has it
  useEffect(() => {
    if (pendingParty && (parties.data ?? []).some((p) => p.id === pendingParty)) { pickParty(pendingParty); setPendingParty(null); }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [parties.data, pendingParty]);

  const ready = formReady(form) && (!overseas || !rateProblem(form.currency ?? "", form.rate ?? ""));
  const body = useMemo(() => (ready ? docBody(overseas ? form : { ...form, currency: "", rate: "", priceIn: "inr" }) : null), [ready, form, overseas]);
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

  const blocked = !!preview && hasBlock(preview.checks);
  const priced = !!preview?.priced_in_foreign && !!preview.foreign_total;

  if (!entity) return <main className="page"><div className="card card-pad">Select a workspace first.</div></main>;

  return (
    <main className="page">
      <div>
        <Link href="/billing" style={{ fontSize: 14 }}>← Sales &amp; Billing</Link>
        <h1 className="disp" style={{ margin: "8px 0 0", fontSize: 36 }}>{overseas || preview?.is_export ? "New export invoice" : "New tax invoice"}</h1>
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
                {(parties.data ?? []).map((p) => <option key={p.id} value={p.id}>{p.legal_name}{p.country !== "IN" ? ` · ${p.country}` : ""}</option>)}
              </select>
              <button type="button" className="linkbtn" style={{ color: "var(--cobalt)", alignSelf: "flex-start" }} aria-expanded={newCustomer} onClick={() => setNewCustomer(!newCustomer)}>+ New customer</button></div>
            {overseas
              ? <div className="field"><label htmlFor="pc">Customer's country</label><input id="pc" className="input" readOnly value={countryName(party!.country)} /></div>
              : <div className="field"><label htmlFor="pg">Customer GSTIN</label>
                  <select id="pg" className="input" value={form.partyGstId} onChange={(e) => set({ partyGstId: e.target.value })} disabled={!party}>
                    <option value="">{party && party.gstins.length === 0 ? "Unregistered" : "Choose…"}</option>
                    {(party?.gstins ?? []).map((g) => <option key={g.id} value={g.id}>{g.gstin ?? "Unregistered"}</option>)}
                  </select></div>}
            {overseas && <div className="field"><label htmlFor="ccy">Customer pays in</label><input id="ccy" className="input mono" maxLength={3} autoComplete="off" value={form.currency ?? ""} onChange={(e) => set({ currency: e.target.value.toUpperCase() })} /></div>}
            {overseas && <div className="field"><label htmlFor="pin">Enter prices in</label>
              <select id="pin" className="input" value={form.priceIn ?? "foreign"} onChange={(e) => set({ priceIn: e.target.value as "foreign" | "inr" })}>
                <option value="foreign">{form.currency || "Customer's currency"}: the invoice is in it</option>
                <option value="inr">Rupees (₹)</option>
              </select></div>}
            {overseas && <div className="field"><label htmlFor="rate">Exchange rate (₹ per {form.currency || "unit"})</label><input id="rate" className="input mono" inputMode="decimal" placeholder="83.2150" value={form.rate ?? ""} onChange={(e) => set({ rate: e.target.value })} /></div>}
            <div className="field"><label htmlFor="dt">Invoice date</label><input id="dt" type="date" className="input" value={form.docDate} onChange={(e) => set({ docDate: e.target.value })} /></div>
            <div className="field"><label htmlFor="terms">Payment terms (days)</label>
              <input id="terms" className="input mono" inputMode="numeric" value={form.terms} placeholder={party ? String(party.payment_terms_days) : "30"} onChange={(e) => set({ terms: e.target.value.replace(/\D/g, "") })} /></div>
            <div className="field"><label htmlFor="po">Customer PO reference</label><input id="po" className="input" value={form.po} onChange={(e) => set({ po: e.target.value })} /></div>
          </section>

          {newCustomer && id && <NewCustomer entityId={id} onCancel={() => setNewCustomer(false)} onDone={(pid) => { setNewCustomer(false); setPendingParty(pid); parties.reload(); }} />}
          {overseas && form.priceIn !== "inr" && <span className="t13">Enter prices in {form.currency || "the customer's currency"}, exactly as on your customer&apos;s order: the invoice is in {form.currency || "that currency"}. The rupee value for GST and the books is worked out from them at the rate above. When the customer pays, the bank credits rupees at that day&apos;s market rate; the difference from this rate is recorded as exchange gain or loss.</span>}
          {overseas && form.priceIn === "inr" && <span className="t13">Enter prices in rupees: the amount your customer pays in {form.currency || "their currency"} is worked out from the rate above and shown beside the total. Rupees are what the books and GST use.</span>}
          <LineEditor lines={form.lines} onChange={(lines) => set({ lines })} touched={touched} unit={overseas && form.priceIn !== "inr" && form.currency ? form.currency : "₹"} />
          <ErrorNote error={error instanceof ApiProblem ? new Error(error.fieldErrors.length ? `${error.title}: ${error.fieldErrors.map((f) => f.message).join("; ")}` : error.message) : error} />
        </div>

        <aside className="col-side" aria-live="polite">
          <section className="card card-pad" style={{ display: "flex", flexDirection: "column", gap: 12 }}>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline" }}>
              <h2>Summary</h2>
              <span className="mono t13">{preview?.next_number ? `Next no. ${preview.next_number}` : ""}</span>
            </div>
            {!ready && <span className="t13">{overseas && formReady(form) ? "Enter the currency and exchange rate to see the invoice." : "Choose a GSTIN and a customer and complete every item to see the tax."}</span>}
            {ready && previewing && !preview && <Loading what="the tax" />}
            <ErrorNote error={previewError} />
            {preview && (
              <>
                {priced && preview.foreign_total && <>
                  <Row k={`Amount (${preview.currency})`} v={fmtForeign(preview.currency, preview.foreign_taxable_total ?? preview.foreign_total)} />
                  {preview.foreign_taxable_total && Number(preview.foreign_total) !== Number(preview.foreign_taxable_total) && <Row k={`IGST (${preview.currency})`} v={fmtForeign(preview.currency, (Number(preview.foreign_total) - Number(preview.foreign_taxable_total)).toFixed(2))} />}
                  <div style={{ display: "flex", justifyContent: "space-between", paddingTop: 12, borderTop: "1px solid var(--line)", fontWeight: 700, fontSize: 18 }}>
                    <span>Invoice total</span><span className="mono">{fmtForeign(preview.currency, preview.foreign_total)}</span>
                  </div>
                  <span className="t13">{preview.amount_in_words_foreign ?? preview.amount_in_words}</span>
                  <div style={{ display: "flex", flexDirection: "column", gap: 6, paddingTop: 10, borderTop: "1px solid var(--line-2)" }}>
                    <span className="t13">Value for GST and the books, at ₹{preview.exchange_rate} per {preview.currency}</span>
                    <Row k="Taxable value" v={formatInr(preview.taxable_total)} />
                    {Number(preview.igst_total) > 0 && <Row k="IGST" v={formatInr(preview.igst_total)} />}
                    {preview.round_off !== "0.00" && <Row k="Round off" v={formatInr(preview.round_off)} />}
                    <Row k="Total in rupees" v={formatInr(preview.grand_total)} />
                  </div>
                </>}
                {!priced && <>
                <Row k="Taxable value" v={formatInr(preview.taxable_total)} />
                {preview.is_inter_state ? <Row k="IGST" v={formatInr(preview.igst_total)} /> : <><Row k="CGST" v={formatInr(preview.cgst_total)} /><Row k="SGST" v={formatInr(preview.sgst_total)} /></>}
                {preview.round_off !== "0.00" && <Row k="Round off" v={formatInr(preview.round_off)} />}
                <div style={{ display: "flex", justifyContent: "space-between", paddingTop: 12, borderTop: "1px solid var(--line)", fontWeight: 700, fontSize: 18 }}>
                  <span>Total</span><span className="mono">{formatInr(preview.grand_total)}</span>
                </div>
                {preview.is_export && preview.foreign_total && <Row k={`Customer pays (${preview.currency})`} v={fmtForeign(preview.currency, preview.foreign_total)} />}
                <span className="t13">{preview.amount_in_words}</span>
                </>}
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
            <span className="t13">Issuing takes the next number, posts the books and sends the invoice for its IRN. It cannot be edited afterwards; use a credit note.{preview?.is_export ? " An export invoice with goods also opens a draft shipping bill on Exports & imports." : ""}</span>
          </section>
        </aside>
      </div>
    </main>
  );
}

function Row({ k, v }: { k: string; v: string }) {
  return <div style={{ display: "flex", justifyContent: "space-between", fontSize: 14 }}><span className="muted">{k}</span><span className="mono">{v}</span></div>;
}
