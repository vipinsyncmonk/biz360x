"use client";
import { useState, type ReactNode } from "react";
import { api, unwrap } from "@/lib/api";
import {
  COUNTRIES, CustomerForm, CustomsForm, RegistrationForm, blankCustomer, blankCustoms, blankRegistration, customerBody, customerCountry, customerProblem, customsBody, customsProblem,
  registrationBody, registrationProblem,
} from "@/lib/export";
import { isoDate } from "@/lib/format";
import type { ShippingBill } from "@/lib/trade";
import { CURRENCIES } from "@/lib/trade";
import { ErrorNote } from "./ui";

const grid = { display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(170px, 1fr))", gap: 12 } as const;
const Field = ({ id, label, children }: { id: string; label: string; children: ReactNode }) => <div className="field"><label htmlFor={id}>{label}</label>{children}</div>;

function Shell({ problem, started, label, onSubmit, onCancel, children, compact }: { problem: string | null; started: boolean; label: string; onSubmit: () => Promise<void>; onCancel?: () => void; children: ReactNode; compact?: boolean }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  async function go() {
    setBusy(true); setError(null);
    try { await onSubmit(); } catch (e) { setError(e); setBusy(false); return; }
    setBusy(false);
  }
  return (
    <form style={{ display: "flex", flexDirection: "column", gap: 12, padding: compact ? "0 20px 14px" : 0 }} onSubmit={(e) => { e.preventDefault(); if (!problem && !busy) void go(); }}>
      {children}
      {problem && started && <span className="t13" role="alert" style={{ color: "var(--amber)" }}>{problem}</span>}
      <ErrorNote error={error} />
      <span style={{ display: "flex", gap: 10 }}><button type="submit" className="btn primary" disabled={busy || !!problem}>{busy ? "Saving…" : label}</button>{onCancel && <button type="button" className="btn" onClick={onCancel}>Cancel</button>}</span>
    </form>
  );
}

/** A customer, here or abroad. A customer outside India has no GSTIN and is invoiced in a foreign currency: that is what makes its invoices export invoices. */
export function NewCustomer({ entityId, onDone, onCancel }: { entityId: string; onDone: (partyId: string) => void; onCancel: () => void }) {
  const [f, setF] = useState<CustomerForm>(blankCustomer());
  const set = (p: Partial<CustomerForm>) => setF({ ...f, ...p });
  const abroad = customerCountry(f) !== "IN";
  return (
    <div className="card card-pad" style={{ display: "flex", flexDirection: "column", gap: 12 }} aria-label="New customer">
      <h2 style={{ margin: 0 }}>New customer</h2>
      <Shell label="Add customer" problem={customerProblem(f)} started={!!f.name} onCancel={onCancel} onSubmit={async () => {
        const r = unwrap(await api.POST("/entities/{entityId}/parties", { params: { path: { entityId } }, body: customerBody(f) as never })) as unknown as { id: string };
        onDone(r.id);
      }}>
        <div style={grid}>
          <Field id="nc-name" label="Name"><input id="nc-name" className="input" value={f.name} onChange={(e) => set({ name: e.target.value })} /></Field>
          <Field id="nc-country" label="Country">
            <select id="nc-country" className="input" value={f.country} onChange={(e) => { const c = COUNTRIES.find((x) => x.code === e.target.value); set({ country: e.target.value, currency: c && c.code !== "IN" ? c.currency : "", gstin: "" }); }}>
              {COUNTRIES.map((c) => <option key={c.code} value={c.code}>{c.name}</option>)}
            </select></Field>
          {f.country === "OTHER" && <Field id="nc-other" label="Country code"><input id="nc-other" className="input mono" maxLength={2} placeholder="US" value={f.otherCountry} onChange={(e) => set({ otherCountry: e.target.value.toUpperCase() })} /></Field>}
          {abroad
            ? <Field id="nc-ccy" label="Invoiced in"><input id="nc-ccy" className="input mono" list="nc-ccys" maxLength={3} value={f.currency} onChange={(e) => set({ currency: e.target.value.toUpperCase() })} /></Field>
            : <Field id="nc-gstin" label="GSTIN (if registered)"><input id="nc-gstin" className="input mono" maxLength={15} value={f.gstin} onChange={(e) => set({ gstin: e.target.value.toUpperCase() })} /></Field>}
          <Field id="nc-terms" label="Payment terms (days)"><input id="nc-terms" className="input mono" inputMode="numeric" placeholder="30" value={f.terms} onChange={(e) => set({ terms: e.target.value.replace(/\D/g, "") })} /></Field>
        </div>
        <datalist id="nc-ccys">{CURRENCIES.map((c) => <option key={c} value={c} />)}</datalist>
        <span className="t13">{abroad ? "An invoice to this customer becomes an export invoice automatically: in this currency, with the place of supply outside India." : "Leave the GSTIN empty for a customer without one; you can still invoice them."}</span>
      </Shell>
    </div>
  );
}

/** The Letter of Undertaking lets goods leave without IGST; the IEC identifies the exporter to customs. Both are issued elsewhere and only recorded here. */
export function RecordRegistration({ entityId, kind, onDone, onCancel }: { entityId: string; kind: "lut" | "iec"; onDone: () => void; onCancel: () => void }) {
  const [f, setF] = useState<RegistrationForm>(blankRegistration(kind));
  const set = (p: Partial<RegistrationForm>) => setF({ ...f, ...p });
  return (
    <div className="card card-pad" style={{ display: "flex", flexDirection: "column", gap: 12 }} aria-label={kind === "lut" ? "Record your LUT" : "Record your IEC"}>
      <h2 style={{ margin: 0 }}>{kind === "lut" ? "Record your Letter of Undertaking" : "Record your Importer-Exporter Code"}</h2>
      <Shell label={kind === "lut" ? "Record LUT" : "Record IEC"} problem={registrationProblem(f)} started={!!f.number} onCancel={onCancel} onSubmit={async () => {
        unwrap(await api.POST("/entities/{entityId}/registrations", { params: { path: { entityId } }, body: registrationBody(f) as never })); onDone();
      }}>
        <div style={grid}>
          <Field id="rg-no" label={kind === "lut" ? "LUT reference (ARN)" : "IEC"}><input id="rg-no" className="input mono" value={f.number} onChange={(e) => set({ number: e.target.value.toUpperCase() })} /></Field>
          {kind === "lut" && <Field id="rg-from" label="Valid from"><input id="rg-from" type="date" className="input" value={f.from} onChange={(e) => set({ from: e.target.value })} /></Field>}
          {kind === "lut" && <Field id="rg-until" label="Valid until"><input id="rg-until" type="date" className="input" value={f.until} onChange={(e) => set({ until: e.target.value })} /></Field>}
        </div>
        <span className="t13">{kind === "lut" ? "File the LUT on the GST portal first; this records its reference and dates so export invoices can be raised without tax." : "Issued by DGFT. It is shown on this screen and needed for shipping bills."}</span>
      </Shell>
    </div>
  );
}

/** What customs issued for a draft shipping bill: the number, the port, the date and the exchange rate on it. Filing the draft fixes these. */
export function FileDraft({ entityId, bill, onDone, onCancel }: { entityId: string; bill: ShippingBill; onDone: () => void; onCancel: () => void }) {
  const [f, setF] = useState<CustomsForm>(blankCustoms(bill.exchange_rate));
  const set = (p: Partial<CustomsForm>) => setF({ ...f, ...p });
  const today = isoDate(new Date());
  const k = bill.id;
  return (
    <Shell compact label="File shipping bill" problem={customsProblem(f, today)} started={!!f.number} onCancel={onCancel} onSubmit={async () => {
      unwrap(await api.PATCH("/entities/{entityId}/shipping-bills/{id}", { params: { path: { entityId, id: bill.id } }, body: customsBody(f) as never })); onDone();
    }}>
      <div style={grid}>
        <Field id={`fd-n-${k}`} label="Shipping bill no."><input id={`fd-n-${k}`} className="input mono" style={{ height: 40 }} value={f.number} onChange={(e) => set({ number: e.target.value })} /></Field>
        <Field id={`fd-d-${k}`} label="Shipping bill date"><input id={`fd-d-${k}`} type="date" className="input" style={{ height: 40 }} max={today} value={f.date} onChange={(e) => set({ date: e.target.value })} /></Field>
        <Field id={`fd-p-${k}`} label="Port code"><input id={`fd-p-${k}`} className="input mono" style={{ height: 40 }} placeholder="INMAA1" maxLength={6} value={f.port} onChange={(e) => set({ port: e.target.value.toUpperCase() })} /></Field>
        <Field id={`fd-r-${k}`} label={`Customs rate (₹ per ${bill.currency})`}><input id={`fd-r-${k}`} className="input mono" style={{ height: 40 }} inputMode="decimal" value={f.rate} onChange={(e) => set({ rate: e.target.value })} /></Field>
      </div>
      <span className="t13">The rate starts as the one on your invoice. Use the one customs printed: rupee values on this screen follow it.</span>
    </Shell>
  );
}
