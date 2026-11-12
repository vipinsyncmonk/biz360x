"use client";
import { useState } from "react";
import { api, unwrap } from "@/lib/api";
import { sameOriginUrl } from "@/lib/vault";
import { formatInr, shortDate } from "@/lib/format";
import { useLoad } from "@/lib/hooks";
import { useSession } from "@/lib/session";
import { BOE_STATUS, BillOfEntry, ExportInvoice, LUT_STATE, REALISATION, ShippingBill, Summary, claimsPending, clockText, lutText, receivedText } from "@/lib/trade";
import { AddBoe, AddShippingBill, EditClaims, MarkExported, PayDuty, RecordMoney, money } from "@/components/TradeForms";
import { FileDraft, RecordRegistration } from "@/components/ExportForms";
import { draftText } from "@/lib/export";
import { ErrorNote, Loading, Pill } from "@/components/ui";

type Open = { kind: "money" | "exported" | "claims" | "duty" | "customs"; id: string } | null;

export default function Trade() {
  const { entity } = useSession();
  const id = entity?.id ?? null;
  const [tab, setTab] = useState<"exports" | "imports">("exports");
  const [adding, setAdding] = useState(false);
  const [open, setOpen] = useState<Open>(null);
  const [registering, setRegistering] = useState<"lut" | "iec" | null>(null);

  const p = { path: { entityId: id! } };
  const summary = useLoad(async () => unwrap(await api.GET("/entities/{entityId}/trade/summary", { params: p })) as unknown as Summary, id);
  const bills = useLoad(async () => unwrap(await api.GET("/entities/{entityId}/shipping-bills", { params: { ...p, query: { limit: 200 } } })).data as unknown as ShippingBill[], id);
  const boes = useLoad(async () => unwrap(await api.GET("/entities/{entityId}/bills-of-entry", { params: { ...p, query: { limit: 200 } } })).data as unknown as BillOfEntry[], id);
  const boeDocs = useLoad(async () => {
    const docs = unwrap(await api.GET("/entities/{entityId}/documents", { params: { ...p, query: { subject: "bills_of_entry", limit: 200 } } })).data as unknown as Array<{ id: string; download_url: string | null }>;
    return Object.fromEntries(docs.filter((d) => d.download_url).map((d) => [d.id, sameOriginUrl(d.download_url) ?? d.download_url!]));
  }, id);
  const invoices = useLoad(async () => unwrap(await api.GET("/entities/{entityId}/sales-documents", { params: { ...p, query: { doc_type: "export_invoice", status: "issued", limit: 100 } } })).data as unknown as ExportInvoice[], id);

  const reload = () => { summary.reload(); bills.reload(); boes.reload(); boeDocs.reload(); setOpen(null); setAdding(false); setRegistering(null); };
  const s = summary.data;
  const lut = s ? (LUT_STATE[s.lut.state] ?? LUT_STATE.none!) : null;
  const toggle = (kind: NonNullable<Open>["kind"], rowId: string) => setOpen(open && open.kind === kind && open.id === rowId ? null : { kind, id: rowId });

  return (
    <main className="page">
      <div style={{ display: "flex", flexWrap: "wrap", gap: 16, justifyContent: "space-between", alignItems: "flex-end" }}>
        <div>
          <span className="mono t13">{entity?.legal_name}{s?.iec ? ` · IEC ${s.iec}` : ""}</span>
          <h1 className="disp" style={{ margin: "6px 0 0", fontSize: 36 }}>Exports &amp; imports</h1>
        </div>
        <button type="button" className="btn primary" aria-expanded={adding} onClick={() => setAdding(!adding)}>{tab === "exports" ? "+ Shipping bill" : "+ Bill of entry"}</button>
      </div>
      <span className="t13">You record what customs and your bank issued. Nothing here connects to ICEGATE, DGFT or a bank, and nothing is posted to your books from this screen yet.</span>

      <section aria-label="Key numbers" className="kpis">
        <Tile label="Exports this year" value={s ? money(s.exports.value_inr) : "…"} note={s ? `${s.exports.bills} shipping ${s.exports.bills === 1 ? "bill" : "bills"} since ${shortDate(s.financial_year_from)}${s.exports.drafts ? ` · ${s.exports.drafts} draft${s.exports.drafts === 1 ? "" : "s"} to complete` : ""}` : ""} />
        <Tile label="Proceeds still to come home" value={s ? money(s.realisation.outstanding_inr) : "…"} note={s ? `${s.realisation.outstanding_bills} ${s.realisation.outstanding_bills === 1 ? "bill" : "bills"}${s.realisation.awaiting_export ? ` · ${s.realisation.awaiting_export} awaiting export` : ""}` : ""} />
        <Tile label="Overdue (past nine months)" value={s ? money(s.realisation.overdue_inr) : "…"} note={s ? `${s.realisation.overdue_bills} ${s.realisation.overdue_bills === 1 ? "bill" : "bills"}` : ""} warn={!!s && s.realisation.overdue_bills > 0} />
        <Tile label="RoDTEP and drawback pending" value={s ? money(String(Number(s.claims.rodtep_pending) + Number(s.claims.drawback_pending))) : "…"} note={s ? `RoDTEP ${money(s.claims.rodtep_pending)} · drawback ${money(s.claims.drawback_pending)}` : ""} />
        <Tile label="Import IGST credit" value={s ? money(s.imports.igst_credit_inr) : "…"} note={s ? `${s.imports.bills} ${s.imports.bills === 1 ? "bill" : "bills"} · duty ${money(s.imports.duty_inr)}` : ""} />
      </section>
      <ErrorNote error={summary.error} />

      {s && lut && (
        <section className="card card-pad" aria-label="Letter of Undertaking" style={{ display: "flex", flexWrap: "wrap", gap: 12, alignItems: "center", justifyContent: "space-between" }}>
          <span><b>Letter of Undertaking (LUT)</b><span className="t13" style={{ display: "block" }}>{lutText(s.lut)}</span></span>
          <span style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
            <Pill tone={lut.tone}>{lut.label}</Pill>
            <button type="button" className="btn" aria-expanded={registering === "lut"} onClick={() => setRegistering(registering === "lut" ? null : "lut")}>{s.lut.state === "none" ? "Record your LUT" : "Record a new LUT"}</button>
            {!s.iec && <button type="button" className="btn" aria-expanded={registering === "iec"} onClick={() => setRegistering(registering === "iec" ? null : "iec")}>Record your IEC</button>}
          </span>
        </section>
      )}
      {id && registering && <RecordRegistration entityId={id} kind={registering} onDone={reload} onCancel={() => setRegistering(null)} />}

      <div className="seg" role="tablist" aria-label="Exports or imports" style={{ alignSelf: "flex-start" }}>
        <button type="button" role="tab" aria-selected={tab === "exports"} onClick={() => { setTab("exports"); setAdding(false); setOpen(null); }}>Exports</button>
        <button type="button" role="tab" aria-selected={tab === "imports"} onClick={() => { setTab("imports"); setAdding(false); setOpen(null); }}>Imports</button>
      </div>

      {tab === "exports" && (
        <>
          {adding && id && <AddShippingBill entityId={id} invoices={invoices.data ?? []} onDone={reload} onCancel={() => setAdding(false)} />}
          <section className="card" style={{ overflow: "hidden" }}>
            <div style={{ padding: "18px 20px" }}><h2 style={{ margin: 0 }}>Shipping bills</h2></div>
            <div style={{ padding: "0 20px" }}><ErrorNote error={bills.error} /></div>
            {bills.loading && !bills.data ? <Loading what="shipping bills" /> : (bills.data ?? []).length === 0 ? <div className="empty">No shipping bills yet. Issue an export invoice with goods and a draft opens here, or record one when customs issues it.</div> : (bills.data ?? []).map((b) => {
              const draft = b.status === "draft";
              const st = draft ? { label: "Draft", tone: "wait" as const } : REALISATION[b.realisation_state] ?? { label: b.realisation_state, tone: "prep" as const };
              const here = open?.id === b.id ? open.kind : null;
              return (
                <div key={b.id} style={{ borderTop: "1px solid var(--line-2)" }}>
                  <div style={{ padding: "14px 20px", display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(190px, 1fr))", gap: 12, alignItems: "start" }}>
                    <span style={{ fontWeight: 600 }}>{draft ? "Shipping bill to complete" : `SB ${b.sb_number}`}<span className="t13" style={{ display: "block", fontWeight: 400 }}>{draft ? draftText(b.invoice_number, b.invoice_status) : `${shortDate(b.sb_date)} · ${b.port_code}${b.invoice_number ? ` · invoice ${b.invoice_number}` : ""}`}</span></span>
                    <span>{b.buyer_name ?? "–"}<span className="t13 mono" style={{ display: "block" }}>{b.currency} {b.fob_foreign} · {money(b.fob_inr)}</span></span>
                    <span><Pill tone={st.tone}>{st.label}</Pill><span className="t13" style={{ display: "block" }}>{draft ? "The nine-month clock starts from the date of export, after filing" : clockText(b)}</span>{Number(b.outstanding_foreign) > 0 && b.realisation_state !== "awaiting_export" && <span className="t13 mono" style={{ display: "block" }}>{b.currency} {b.outstanding_foreign} to come · {money(b.outstanding_inr)}</span>}</span>
                    <span className="t13">{claimsPending(b) ? `RoDTEP ${formatInr(b.rodtep_claimed)}, drawback ${formatInr(b.drawback_claimed)} claimed` : Number(b.rodtep_claimed) + Number(b.drawback_claimed) > 0 ? "Claims received" : "No claims recorded"}{b.brc_ref ? ` · e-BRC ${b.brc_ref}` : ""}</span>
                  </div>
                  <div style={{ padding: "0 20px 14px", display: "flex", gap: 8, flexWrap: "wrap" }}>
                    {draft && <button type="button" className="btn primary" aria-expanded={here === "customs"} onClick={() => toggle("customs", b.id)}>Add the customs details</button>}
                    {!draft && !b.exported_on && <button type="button" className="btn" aria-expanded={here === "exported"} onClick={() => toggle("exported", b.id)}>Record date of export</button>}
                    {!draft && b.realisation_state !== "realised" && <button type="button" className="btn" aria-expanded={here === "money"} onClick={() => toggle("money", b.id)}>Record money received</button>}
                    {!draft && <button type="button" className="btn" aria-expanded={here === "claims"} onClick={() => toggle("claims", b.id)}>Claims and e-BRC</button>}
                  </div>
                  {(b.realisations ?? []).length > 0 && <div className="t13" style={{ padding: "0 20px 12px" }}>Received: {b.realisations.map((r) => receivedText(b.currency, r, shortDate(r.received_on))).join("; ")}</div>}
                  {id && here === "customs" && <FileDraft entityId={id} bill={b} onDone={reload} onCancel={() => setOpen(null)} />}
                  {id && here === "money" && <RecordMoney entityId={id} bill={b} onDone={reload} onCancel={() => setOpen(null)} />}
                  {id && here === "exported" && <MarkExported entityId={id} bill={b} onDone={reload} onCancel={() => setOpen(null)} />}
                  {id && here === "claims" && <EditClaims entityId={id} bill={b} onDone={reload} onCancel={() => setOpen(null)} />}
                </div>
              );
            })}
          </section>
          <span className="t13">Proceeds are due nine calendar months after the date of export. That period, and how it starts, is a starter rule: your authorised dealer bank can allow longer in some cases. Have your CA confirm it.</span>
        </>
      )}

      {tab === "imports" && (
        <>
          {adding && id && <AddBoe entityId={id} onDone={reload} onCancel={() => setAdding(false)} />}
          <section className="card" style={{ overflow: "hidden" }}>
            <div style={{ padding: "18px 20px" }}><h2 style={{ margin: 0 }}>Bills of entry</h2></div>
            <div style={{ padding: "0 20px" }}><ErrorNote error={boes.error} /></div>
            {boes.loading && !boes.data ? <Loading what="bills of entry" /> : (boes.data ?? []).length === 0 ? <div className="empty">No bills of entry yet. Record one when customs assesses an import.</div> : (boes.data ?? []).map((b) => {
              const st = BOE_STATUS[b.status] ?? { label: b.status, tone: "prep" as const };
              return (
                <div key={b.id} style={{ borderTop: "1px solid var(--line-2)" }}>
                  <div style={{ padding: "14px 20px", display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(190px, 1fr))", gap: 12, alignItems: "start" }}>
                    <span style={{ fontWeight: 600 }}>BoE {b.boe_number}<span className="t13" style={{ display: "block", fontWeight: 400 }}>{shortDate(b.boe_date)} · {b.port_code}</span>{b.document_id && boeDocs.data?.[b.document_id] && <a className="t13" style={{ display: "block" }} href={boeDocs.data[b.document_id]} aria-label={`View bill of entry ${b.boe_number} (PDF)`}>View PDF</a>}</span>
                    <span>{b.supplier_name ?? "Supplier not linked"}<span className="t13 mono" style={{ display: "block" }}>{b.currency} {b.cif_foreign} · {money(b.cif_inr)}</span></span>
                    <span>Duty {money(b.duty_total)}<span className="t13" style={{ display: "block" }}>BCD {money(b.bcd)} · SWS {money(b.sws)} · IGST {money(b.igst)}{Number(b.other_duty) > 0 ? ` · other ${money(b.other_duty)}` : ""}</span></span>
                    <span><Pill tone={st.tone}>{st.label}</Pill><span className="t13" style={{ display: "block" }}>{b.duty_paid_on ? `Paid ${shortDate(b.duty_paid_on)} · IGST credit ${formatInr(b.igst_credit, { decimals: false })}` : "IGST credit once the duty is paid"}</span></span>
                  </div>
                  {!b.duty_paid_on && <div style={{ padding: "0 20px 14px" }}><button type="button" className="btn" aria-expanded={open?.id === b.id} onClick={() => toggle("duty", b.id)}>Mark duty paid</button></div>}
                  {id && open?.kind === "duty" && open.id === b.id && <PayDuty entityId={id} id={b.id} number={b.boe_number} onDone={reload} onCancel={() => setOpen(null)} />}
                </div>
              );
            })}
          </section>
        </>
      )}
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
