"use client";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useMemo, useState } from "react";
import { api, unwrap } from "@/lib/api";
import { QuoteForm, blankLine, quoteBody, quotePreviewBody, quoteReady } from "@/lib/billing";
import { formatInr, isoDate } from "@/lib/format";
import { useLoad } from "@/lib/hooks";
import { useSession } from "@/lib/session";
import { LineEditor } from "@/components/LineEditor";
import { ErrorNote, Loading } from "@/components/ui";

interface Party { id: string; legal_name: string; payment_terms_days: number; gstins: Array<{ id: string; gstin: string | null; is_default: boolean }> }
interface Reg { id: string; number: string; label: string | null }
interface Preview { taxable_total: string; cgst_total: string; sgst_total: string; igst_total: string; grand_total: string; is_inter_state: boolean; place_of_supply: string }

export default function NewQuote() {
  const { entity } = useSession();
  const id = entity?.id ?? null;
  const router = useRouter();
  const [form, setForm] = useState<QuoteForm>({ registrationId: "", partyId: "", partyGstId: "", title: "", validUntil: "", terms: "", advance: "", lines: [blankLine()] });
  const [touched, setTouched] = useState(false);
  const [preview, setPreview] = useState<Preview | null>(null);
  const [previewError, setPreviewError] = useState<unknown>(null);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<unknown>(null);

  const parties = useLoad(async () => unwrap(await api.GET("/entities/{entityId}/parties", { params: { path: { entityId: id! }, query: { role: "customer", limit: 200 } } })).data as unknown as Party[], id);
  const regs = useLoad(async () => unwrap(await api.GET("/entities/{entityId}/registrations", { params: { path: { entityId: id! }, query: { kind: "gstin" } } })).data as unknown as Reg[], id);
  const set = (patch: Partial<QuoteForm>) => setForm((f) => ({ ...f, ...patch }));
  const party = (parties.data ?? []).find((p) => p.id === form.partyId);

  useEffect(() => { if (!form.registrationId && (regs.data ?? []).length === 1) set({ registrationId: regs.data![0]!.id }); }, [regs.data, form.registrationId]);
  const ready = quoteReady(form);
  const previewBody = useMemo(() => (ready ? quotePreviewBody(form, isoDate(new Date())) : null), [ready, form]);
  const previewJson = previewBody ? JSON.stringify(previewBody) : null;

  useEffect(() => {
    if (!id || !previewBody) { setPreview(null); return; }
    let cancelled = false;
    const t = setTimeout(async () => {
      try {
        const p = unwrap(await api.POST("/entities/{entityId}/sales-documents/preview", { params: { path: { entityId: id } }, body: previewBody })) as unknown as Preview;
        if (!cancelled) { setPreview(p); setPreviewError(null); }
      } catch (e) {
        if (!cancelled) { setPreview(null); setPreviewError(e); }
      }
    }, 300);
    return () => { cancelled = true; clearTimeout(t); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [id, previewJson]);

  async function save() {
    if (!id || !ready) return;
    setSaving(true); setError(null);
    try {
      unwrap(await api.POST("/entities/{entityId}/quotes", { params: { path: { entityId: id } }, body: quoteBody(form) as never }));
      router.push("/billing/quotes");
    } catch (e) {
      setError(e);
      setSaving(false);
    }
  }

  if (!entity) return <main className="page"><div className="card card-pad">Select a workspace first.</div></main>;
  return (
    <main className="page">
      <div>
        <Link href="/billing/quotes" style={{ fontSize: 14 }}>← Quotes</Link>
        <h1 className="disp" style={{ margin: "8px 0 0", fontSize: 36 }}>New quote</h1>
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
              <select id="party" className="input" value={form.partyId} onChange={(e) => { const p = (parties.data ?? []).find((x) => x.id === e.target.value); set({ partyId: e.target.value, partyGstId: p?.gstins.find((g) => g.is_default)?.id ?? p?.gstins[0]?.id ?? "" }); }}>
                <option value="">Choose…</option>
                {(parties.data ?? []).map((p) => <option key={p.id} value={p.id}>{p.legal_name}</option>)}
              </select></div>
            <div className="field"><label htmlFor="title">Title</label><input id="title" className="input" value={form.title} placeholder="Lot 2 · pressure transmitters" onChange={(e) => set({ title: e.target.value })} /></div>
            <div className="field"><label htmlFor="valid">Valid until</label><input id="valid" type="date" className="input" value={form.validUntil} onChange={(e) => set({ validUntil: e.target.value })} /></div>
            <div className="field"><label htmlFor="terms">Payment terms (days)</label><input id="terms" className="input mono" inputMode="numeric" value={form.terms} placeholder={party ? String(party.payment_terms_days) : "30"} onChange={(e) => set({ terms: e.target.value.replace(/\D/g, "") })} /></div>
            <div className="field"><label htmlFor="adv">Advance (%)</label><input id="adv" className="input mono" inputMode="decimal" value={form.advance} placeholder="0" onChange={(e) => set({ advance: e.target.value })} /></div>
          </section>
          <LineEditor lines={form.lines} onChange={(lines) => set({ lines })} touched={touched} />
          <ErrorNote error={error} />
        </div>
        <aside className="col-side" aria-live="polite">
          <section className="card card-pad" style={{ display: "flex", flexDirection: "column", gap: 12 }}>
            <h2>Summary</h2>
            {!ready && <span className="t13">Choose a GSTIN and a customer and complete every item to see the tax.</span>}
            {ready && !preview && !previewError && <Loading what="the tax" />}
            <ErrorNote error={previewError} />
            {preview && (
              <>
                <Row k="Taxable value" v={formatInr(preview.taxable_total)} />
                {preview.is_inter_state ? <Row k="IGST" v={formatInr(preview.igst_total)} /> : <><Row k="CGST" v={formatInr(preview.cgst_total)} /><Row k="SGST" v={formatInr(preview.sgst_total)} /></>}
                <div style={{ display: "flex", justifyContent: "space-between", paddingTop: 12, borderTop: "1px solid var(--line)", fontWeight: 700, fontSize: 18 }}><span>Total</span><span className="mono">{formatInr(preview.grand_total)}</span></div>
                <span className="t13">Worked out as for an invoice dated today. The quote keeps these figures; the invoice takes its own date.</span>
              </>
            )}
            <button type="button" className="btn primary" disabled={saving || !ready} onClick={() => void save()}>{saving ? "Saving…" : "Save quote"}</button>
            {!ready && <button type="button" className="linkbtn" style={{ color: "var(--cobalt)" }} onClick={() => setTouched(true)}>What is missing?</button>}
            <span className="t13">The quote is saved as a draft. Send it from the quotes list.</span>
          </section>
        </aside>
      </div>
    </main>
  );
}

function Row({ k, v }: { k: string; v: string }) {
  return <div style={{ display: "flex", justifyContent: "space-between", fontSize: 14 }}><span className="muted">{k}</span><span className="mono">{v}</span></div>;
}
