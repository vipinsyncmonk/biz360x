"use client";
import { useState, type ReactNode } from "react";
import { api, unwrap } from "@/lib/api";
import { formatInr, isoDate } from "@/lib/format";
import {
  BoeForm, ClaimsForm, CURRENCIES, ExportInvoice, RealisationForm, SbForm, ShippingBill, blankBoe, blankRealisation, blankSb, boeBody, boeProblem, claimsBody, claimsForm, claimsProblem,
  previewText, realisationBody, realisationProblem, sbBody, sbProblem,
} from "@/lib/trade";
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

/** Record a shipping bill as customs issued it. Linking it to the export invoice fills in the buyer. */
export function AddShippingBill({ entityId, invoices, onDone, onCancel }: { entityId: string; invoices: ExportInvoice[]; onDone: () => void; onCancel: () => void }) {
  const [f, setF] = useState<SbForm>(blankSb());
  const set = (p: Partial<SbForm>) => setF({ ...f, ...p });
  const today = isoDate(new Date());
  const preview = previewText(f.fob, f.rate);
  return (
    <div className="card card-pad" style={{ display: "flex", flexDirection: "column", gap: 12 }}>
      <h2 style={{ margin: 0 }}>Record a shipping bill</h2>
      <Shell label="Record shipping bill" problem={sbProblem(f, today)} started={!!(f.number || f.fob)} onCancel={onCancel} onSubmit={async () => { unwrap(await api.POST("/entities/{entityId}/shipping-bills", { params: { path: { entityId } }, body: sbBody(f) as never })); onDone(); }}>
        <div style={grid}>
          <Field id="sb-no" label="Shipping bill no."><input id="sb-no" className="input mono" value={f.number} onChange={(e) => set({ number: e.target.value })} /></Field>
          <Field id="sb-date" label="Shipping bill date"><input id="sb-date" type="date" className="input" max={today} value={f.date} onChange={(e) => set({ date: e.target.value })} /></Field>
          <Field id="sb-port" label="Port code"><input id="sb-port" className="input mono" placeholder="INMAA1" maxLength={6} value={f.port} onChange={(e) => set({ port: e.target.value.toUpperCase() })} /></Field>
          <Field id="sb-ccy" label="Currency"><input id="sb-ccy" className="input mono" list="ccy" maxLength={3} value={f.currency} onChange={(e) => set({ currency: e.target.value.toUpperCase() })} /></Field>
          <Field id="sb-fob" label="FOB value (foreign)"><input id="sb-fob" className="input mono" inputMode="decimal" value={f.fob} onChange={(e) => set({ fob: e.target.value })} /></Field>
          <Field id="sb-rate" label="Customs exchange rate"><input id="sb-rate" className="input mono" inputMode="decimal" value={f.rate} onChange={(e) => set({ rate: e.target.value })} /></Field>
          <Field id="sb-inv" label="Export invoice (optional)">
            <select id="sb-inv" className="input" value={f.invoiceId} onChange={(e) => set({ invoiceId: e.target.value })}><option value="">None</option>{invoices.map((i) => <option key={i.id} value={i.id}>{i.number} · {i.buyer_legal_name}</option>)}</select></Field>
          <Field id="sb-exp" label="Date of export (if the goods have left)"><input id="sb-exp" type="date" className="input" max={today} value={f.exportedOn} onChange={(e) => set({ exportedOn: e.target.value })} /></Field>
        </div>
        <datalist id="ccy">{CURRENCIES.map((c) => <option key={c} value={c} />)}</datalist>
        <span className="t13">{preview ? `${preview} at that rate. The server works out the final figure.` : "The date of export starts the nine-month clock for bringing the money home."}</span>
      </Shell>
    </div>
  );
}

/** Money received against a shipping bill, from the bank's advice. */
export function RecordMoney({ entityId, bill, onDone, onCancel }: { entityId: string; bill: ShippingBill; onDone: () => void; onCancel: () => void }) {
  const [f, setF] = useState<RealisationForm>(blankRealisation(bill.outstanding_foreign));
  const set = (p: Partial<RealisationForm>) => setF({ ...f, ...p });
  const today = isoDate(new Date());
  const k = bill.id;
  return (
    <Shell compact label="Record money received" problem={realisationProblem(f, bill.outstanding_foreign, today)} started={!!f.date} onCancel={onCancel} onSubmit={async () => {
      unwrap(await api.POST("/entities/{entityId}/shipping-bills/{id}/realisations", { params: { path: { entityId, id: bill.id } }, body: realisationBody(f) as never })); onDone();
    }}>
      <div style={grid}>
        <Field id={`rm-d-${k}`} label="Received on"><input id={`rm-d-${k}`} type="date" className="input" style={{ height: 40 }} max={today} value={f.date} onChange={(e) => set({ date: e.target.value })} /></Field>
        <Field id={`rm-a-${k}`} label={`Amount (${bill.currency})`}><input id={`rm-a-${k}`} className="input mono" style={{ height: 40 }} inputMode="decimal" value={f.amount} onChange={(e) => set({ amount: e.target.value })} /></Field>
        <Field id={`rm-b-${k}`} label="Bank reference"><input id={`rm-b-${k}`} className="input mono" style={{ height: 40 }} value={f.bankRef} onChange={(e) => set({ bankRef: e.target.value })} /></Field>
        <Field id={`rm-f-${k}`} label="FIRC no. (optional)"><input id={`rm-f-${k}`} className="input mono" style={{ height: 40 }} value={f.firc} onChange={(e) => set({ firc: e.target.value })} /></Field>
      </div>
    </Shell>
  );
}

/** The date goods left India: it starts the realisation clock and is not changed afterwards. */
export function MarkExported({ entityId, bill, onDone, onCancel }: { entityId: string; bill: ShippingBill; onDone: () => void; onCancel: () => void }) {
  const [date, setDate] = useState("");
  const today = isoDate(new Date());
  const problem = !date ? "Choose the date of export" : date > today ? "The date of export cannot be in the future" : bill.sb_date && date < bill.sb_date ? "The date of export cannot be before the shipping bill date" : null;
  return (
    <Shell compact label="Start the clock" problem={problem} started={!!date} onCancel={onCancel} onSubmit={async () => {
      unwrap(await api.PATCH("/entities/{entityId}/shipping-bills/{id}", { params: { path: { entityId, id: bill.id } }, body: { exported_on: date, status: "let_export" } as never })); onDone();
    }}>
      <Field id={`ex-${bill.id}`} label="Date of export"><input id={`ex-${bill.id}`} type="date" className="input" style={{ height: 40, maxWidth: 220 }} max={today} min={bill.sb_date ?? undefined} value={date} onChange={(e) => setDate(e.target.value)} /></Field>
      <span className="t13">Proceeds must come home within nine months of this date. Once saved, the date is fixed.</span>
    </Shell>
  );
}

/** RoDTEP, duty drawback and the e-BRC reference for one shipping bill. */
export function EditClaims({ entityId, bill, onDone, onCancel }: { entityId: string; bill: ShippingBill; onDone: () => void; onCancel: () => void }) {
  const [f, setF] = useState<ClaimsForm>(claimsForm(bill));
  const set = (p: Partial<ClaimsForm>) => setF({ ...f, ...p });
  const k = bill.id;
  return (
    <Shell compact label="Save claims" problem={claimsProblem(f)} started onCancel={onCancel} onSubmit={async () => {
      unwrap(await api.PATCH("/entities/{entityId}/shipping-bills/{id}", { params: { path: { entityId, id: bill.id } }, body: claimsBody(f) as never })); onDone();
    }}>
      <div style={grid}>
        <Field id={`cl-rc-${k}`} label="RoDTEP claimed (₹)"><input id={`cl-rc-${k}`} className="input mono" style={{ height: 40 }} value={f.rodtepClaimed} onChange={(e) => set({ rodtepClaimed: e.target.value })} /></Field>
        <Field id={`cl-rr-${k}`} label="RoDTEP credited (₹)"><input id={`cl-rr-${k}`} className="input mono" style={{ height: 40 }} value={f.rodtepCredited} onChange={(e) => set({ rodtepCredited: e.target.value })} /></Field>
        <Field id={`cl-dc-${k}`} label="Drawback claimed (₹)"><input id={`cl-dc-${k}`} className="input mono" style={{ height: 40 }} value={f.drawbackClaimed} onChange={(e) => set({ drawbackClaimed: e.target.value })} /></Field>
        <Field id={`cl-dr-${k}`} label="Drawback received (₹)"><input id={`cl-dr-${k}`} className="input mono" style={{ height: 40 }} value={f.drawbackReceived} onChange={(e) => set({ drawbackReceived: e.target.value })} /></Field>
        <Field id={`cl-b-${k}`} label="e-BRC reference"><input id={`cl-b-${k}`} className="input mono" style={{ height: 40 }} value={f.brc} onChange={(e) => set({ brc: e.target.value })} /></Field>
      </div>
    </Shell>
  );
}

/** Record a bill of entry as customs issued it, with the duty it assessed. */
export function AddBoe({ entityId, onDone, onCancel }: { entityId: string; onDone: () => void; onCancel: () => void }) {
  const [f, setF] = useState<BoeForm>(blankBoe());
  const set = (p: Partial<BoeForm>) => setF({ ...f, ...p });
  const today = isoDate(new Date());
  const preview = previewText(f.cif, f.rate);
  return (
    <div className="card card-pad" style={{ display: "flex", flexDirection: "column", gap: 12 }}>
      <h2 style={{ margin: 0 }}>Record a bill of entry</h2>
      <Shell label="Record bill of entry" problem={boeProblem(f, today)} started={!!(f.number || f.cif)} onCancel={onCancel} onSubmit={async () => { unwrap(await api.POST("/entities/{entityId}/bills-of-entry", { params: { path: { entityId } }, body: boeBody(f) as never })); onDone(); }}>
        <div style={grid}>
          <Field id="be-no" label="Bill of entry no."><input id="be-no" className="input mono" value={f.number} onChange={(e) => set({ number: e.target.value })} /></Field>
          <Field id="be-date" label="Bill of entry date"><input id="be-date" type="date" className="input" max={today} value={f.date} onChange={(e) => set({ date: e.target.value })} /></Field>
          <Field id="be-port" label="Port code"><input id="be-port" className="input mono" placeholder="INBLR4" maxLength={6} value={f.port} onChange={(e) => set({ port: e.target.value.toUpperCase() })} /></Field>
          <Field id="be-ccy" label="Currency"><input id="be-ccy" className="input mono" list="ccy" maxLength={3} value={f.currency} onChange={(e) => set({ currency: e.target.value.toUpperCase() })} /></Field>
          <Field id="be-cif" label="CIF value (foreign)"><input id="be-cif" className="input mono" inputMode="decimal" value={f.cif} onChange={(e) => set({ cif: e.target.value })} /></Field>
          <Field id="be-rate" label="Customs exchange rate"><input id="be-rate" className="input mono" inputMode="decimal" value={f.rate} onChange={(e) => set({ rate: e.target.value })} /></Field>
          <Field id="be-bcd" label="Basic customs duty (₹)"><input id="be-bcd" className="input mono" value={f.bcd} onChange={(e) => set({ bcd: e.target.value })} /></Field>
          <Field id="be-sws" label="Social welfare surcharge (₹)"><input id="be-sws" className="input mono" value={f.sws} onChange={(e) => set({ sws: e.target.value })} /></Field>
          <Field id="be-igst" label="IGST (₹)"><input id="be-igst" className="input mono" value={f.igst} onChange={(e) => set({ igst: e.target.value })} /></Field>
          <Field id="be-oth" label="Other duty (₹)"><input id="be-oth" className="input mono" value={f.other} onChange={(e) => set({ other: e.target.value })} /></Field>
          <Field id="be-paid" label="Duty paid on (if paid)"><input id="be-paid" type="date" className="input" max={today} value={f.paidOn} onChange={(e) => set({ paidOn: e.target.value })} /></Field>
        </div>
        <datalist id="ccy">{CURRENCIES.map((c) => <option key={c} value={c} />)}</datalist>
        <span className="t13">{preview ? `${preview} CIF at that rate. ` : ""}IGST paid on an import is input tax credit once the duty is paid.</span>
      </Shell>
    </div>
  );
}

/** Say when the duty on a bill of entry was paid. */
export function PayDuty({ entityId, id, number, onDone, onCancel }: { entityId: string; id: string; number: string; onDone: () => void; onCancel: () => void }) {
  const [date, setDate] = useState("");
  const today = isoDate(new Date());
  const problem = !date ? "Choose the date the duty was paid" : date > today ? "Duty cannot have been paid in the future" : null;
  return (
    <Shell compact label="Mark duty paid" problem={problem} started={!!date} onCancel={onCancel} onSubmit={async () => {
      unwrap(await api.PATCH("/entities/{entityId}/bills-of-entry/{id}", { params: { path: { entityId, id } }, body: { status: "duty_paid", duty_paid_on: date } as never })); onDone();
    }}>
      <Field id={`pd-${id}`} label={`Duty on ${number} paid on`}><input id={`pd-${id}`} type="date" className="input" style={{ height: 40, maxWidth: 220 }} max={today} value={date} onChange={(e) => setDate(e.target.value)} /></Field>
    </Shell>
  );
}

export const money = (v: string) => formatInr(v, { decimals: false });
