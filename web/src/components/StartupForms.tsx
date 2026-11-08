"use client";
import { useState, type ReactNode } from "react";
import { api, newIdempotencyKey, unwrap } from "@/lib/api";
import { isoDate } from "@/lib/format";
import type { Employee } from "@/lib/payroll";
import {
  AllotmentForm, BenefitForm, ClassForm, GrantForm, HOLDER_KINDS, HolderForm, Holder, INSTRUMENTS, PoolForm, Pool, Round, RoundForm, ShareClass, RECORDABLE, allotmentBody, allotmentProblem,
  benefitBody, blankAllotment, blankBenefit, blankClass, blankGrant, blankHolder, blankPool, blankRound, classBody, classProblem, grantBody, grantProblem, groupIndian, holderBody,
  holderProblem, isOpenRound, poolBody, poolProblem, roundBody, roundProblem, Benefit,
} from "@/lib/startup";
import { ErrorNote } from "./ui";

const grid = { display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(190px, 1fr))", gap: 12 } as const;

/** A form that posts once and tells the page to reload. Each field is a controlled input; the submit button stays off until the form is valid. */
function FormShell({ problem, started, onSubmit, children, label }: { problem: string | null; started: boolean; onSubmit: () => Promise<void>; children: ReactNode; label: string }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [saved, setSaved] = useState(false);
  async function go() {
    setBusy(true); setError(null); setSaved(false);
    try { await onSubmit(); setSaved(true); } catch (e) { setError(e); }
    setBusy(false);
  }
  return (
    <form style={{ display: "flex", flexDirection: "column", gap: 12 }} onSubmit={(e) => { e.preventDefault(); if (!problem && !busy) void go(); }}>
      {children}
      {problem && started && <span className="t13" role="alert" style={{ color: "var(--amber)" }}>{problem}</span>}
      <ErrorNote error={error} />
      {saved && <span className="t13" role="status">Saved.</span>}
      <span><button type="submit" className="btn primary" disabled={busy || !!problem}>{busy ? "Saving…" : label}</button></span>
    </form>
  );
}

function Field({ id, label, children }: { id: string; label: string; children: ReactNode }) {
  return <div className="field"><label htmlFor={id}>{label}</label>{children}</div>;
}

export function ClassFormView({ entityId, onDone }: { entityId: string; onDone: () => void }) {
  const [f, setF] = useState<ClassForm>(blankClass());
  const set = (p: Partial<ClassForm>) => setF({ ...f, ...p });
  return (
    <FormShell label="Add share class" problem={classProblem(f)} started={!!f.name} onSubmit={async () => {
      unwrap(await api.POST("/entities/{entityId}/share-classes", { params: { path: { entityId } }, body: classBody(f) as never })); setF(blankClass()); onDone();
    }}>
      <div style={grid}>
        <Field id="sc-name" label="Name"><input id="sc-name" className="input" placeholder="Series Seed CCPS" value={f.name} onChange={(e) => set({ name: e.target.value })} /></Field>
        <Field id="sc-inst" label="Instrument"><select id="sc-inst" className="input" value={f.instrument} onChange={(e) => set({ instrument: e.target.value })}>{INSTRUMENTS.map(([k, l]) => <option key={k} value={k}>{l}</option>)}</select></Field>
        <Field id="sc-face" label="Face value (₹)"><input id="sc-face" className="input mono" inputMode="decimal" value={f.face} onChange={(e) => set({ face: e.target.value })} /></Field>
        <Field id="sc-ratio" label="Converts to equity at"><input id="sc-ratio" className="input mono" inputMode="decimal" value={f.ratio} onChange={(e) => set({ ratio: e.target.value })} /></Field>
      </div>
    </FormShell>
  );
}

export function HolderFormView({ entityId, onDone }: { entityId: string; onDone: () => void }) {
  const [f, setF] = useState<HolderForm>(blankHolder());
  const set = (p: Partial<HolderForm>) => setF({ ...f, ...p });
  return (
    <FormShell label="Add shareholder" problem={holderProblem(f)} started={!!f.name} onSubmit={async () => {
      unwrap(await api.POST("/entities/{entityId}/shareholders", { params: { path: { entityId } }, body: holderBody(f) as never })); setF(blankHolder()); onDone();
    }}>
      <div style={grid}>
        <Field id="sh-name" label="Name"><input id="sh-name" className="input" value={f.name} onChange={(e) => set({ name: e.target.value })} /></Field>
        <Field id="sh-kind" label="Kind"><select id="sh-kind" className="input" value={f.kind} onChange={(e) => set({ kind: e.target.value })}>{HOLDER_KINDS.map(([k, l]) => <option key={k} value={k}>{l}</option>)}</select></Field>
        <Field id="sh-pan" label="PAN (optional)"><input id="sh-pan" className="input mono" maxLength={10} value={f.pan} onChange={(e) => set({ pan: e.target.value.toUpperCase() })} /></Field>
        <Field id="sh-country" label="Country"><input id="sh-country" className="input mono" maxLength={2} value={f.country} onChange={(e) => set({ country: e.target.value.toUpperCase() })} /></Field>
      </div>
      <span className="t13">A holder outside India is reported on FIRMS (FC-GPR) after an allotment.</span>
    </FormShell>
  );
}

export function AllotmentFormView({ entityId, classes, holders, rounds, onDone }: { entityId: string; classes: ShareClass[]; holders: Holder[]; rounds: Round[]; onDone: () => void }) {
  const [f, setF] = useState<AllotmentForm>(blankAllotment());
  const set = (p: Partial<AllotmentForm>) => setF({ ...f, ...p });
  const today = isoDate(new Date());
  return (
    <FormShell label="Record allotment" problem={allotmentProblem(f, today)} started={!!(f.quantity || f.price)} onSubmit={async () => {
      unwrap(await api.POST("/entities/{entityId}/allotments", { params: { path: { entityId }, header: { "Idempotency-Key": newIdempotencyKey() } }, body: allotmentBody(f) as never })); setF(blankAllotment()); onDone();
    }}>
      <div style={grid}>
        <Field id="al-holder" label="Shares for"><select id="al-holder" className="input" value={f.holderId} onChange={(e) => set({ holderId: e.target.value })}><option value="">Choose…</option>{holders.map((h) => <option key={h.id} value={h.id}>{h.name}</option>)}</select></Field>
        <Field id="al-class" label="Class"><select id="al-class" className="input" value={f.classId} onChange={(e) => set({ classId: e.target.value })}><option value="">Choose…</option>{classes.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}</select></Field>
        <Field id="al-round" label="Round (optional)"><select id="al-round" className="input" value={f.roundId} onChange={(e) => set({ roundId: e.target.value })}><option value="">None</option>{rounds.filter(isOpenRound).map((r) => <option key={r.id} value={r.id}>{r.name}</option>)}</select></Field>
        <Field id="al-qty" label="Shares"><input id="al-qty" className="input mono" inputMode="numeric" value={f.quantity} onChange={(e) => set({ quantity: e.target.value.replace(/\D/g, "") })} /></Field>
        <Field id="al-price" label="Issue price per share (₹)"><input id="al-price" className="input mono" inputMode="decimal" value={f.price} onChange={(e) => set({ price: e.target.value })} /></Field>
        <Field id="al-recv" label="Money received on"><input id="al-recv" type="date" className="input" max={today} value={f.received} onChange={(e) => set({ received: e.target.value })} /></Field>
        <Field id="al-date" label="Allotted on"><input id="al-date" type="date" className="input" max={today} value={f.allotted} onChange={(e) => set({ allotted: e.target.value })} /></Field>
      </div>
      <span className="t13">Leave the allotment date empty while the money is in but the shares are not issued yet: they are not on the cap table until they are. Allotting adds a PAS-3 task due in 15 days.</span>
    </FormShell>
  );
}

export function RoundFormView({ entityId, classes, onDone }: { entityId: string; classes: ShareClass[]; onDone: () => void }) {
  const [f, setF] = useState<RoundForm>(blankRound());
  const set = (p: Partial<RoundForm>) => setF({ ...f, ...p });
  return (
    <FormShell label="Start round" problem={roundProblem(f)} started={!!f.name} onSubmit={async () => {
      unwrap(await api.POST("/entities/{entityId}/funding-rounds", { params: { path: { entityId } }, body: roundBody(f) as never })); setF(blankRound()); onDone();
    }}>
      <div style={grid}>
        <Field id="rd-name" label="Name"><input id="rd-name" className="input" placeholder="Seed extension" value={f.name} onChange={(e) => set({ name: e.target.value })} /></Field>
        <Field id="rd-class" label="Instrument"><select id="rd-class" className="input" value={f.classId} onChange={(e) => set({ classId: e.target.value })}><option value="">Not decided</option>{classes.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}</select></Field>
        <Field id="rd-target" label="Target amount (₹)"><input id="rd-target" className="input mono" inputMode="decimal" value={f.target} onChange={(e) => set({ target: e.target.value })} /></Field>
        <Field id="rd-pre" label="Pre-money valuation (₹)"><input id="rd-pre" className="input mono" inputMode="decimal" value={f.preMoney} onChange={(e) => set({ preMoney: e.target.value })} /></Field>
        <Field id="rd-ts" label="Term sheet signed on"><input id="rd-ts" type="date" className="input" value={f.termSheet} onChange={(e) => set({ termSheet: e.target.value })} /></Field>
      </div>
      <span className="t13">With a target, a valuation and an instrument, the cap table can show how the round dilutes everyone.</span>
    </FormShell>
  );
}

export function PoolFormView({ entityId, onDone }: { entityId: string; onDone: () => void }) {
  const [f, setF] = useState<PoolForm>(blankPool());
  const set = (p: Partial<PoolForm>) => setF({ ...f, ...p });
  return (
    <FormShell label="Add pool" problem={poolProblem(f)} started={!!f.name} onSubmit={async () => {
      unwrap(await api.POST("/entities/{entityId}/esop-pools", { params: { path: { entityId } }, body: poolBody(f) as never })); setF(blankPool()); onDone();
    }}>
      <div style={grid}>
        <Field id="pl-name" label="Name"><input id="pl-name" className="input" placeholder="ESOP 2025" value={f.name} onChange={(e) => set({ name: e.target.value })} /></Field>
        <Field id="pl-date" label="Approved by shareholders on"><input id="pl-date" type="date" className="input" value={f.approved} onChange={(e) => set({ approved: e.target.value })} /></Field>
        <Field id="pl-size" label="Options in the pool"><input id="pl-size" className="input mono" inputMode="numeric" value={f.size} onChange={(e) => set({ size: e.target.value.replace(/\D/g, "") })} /></Field>
      </div>
    </FormShell>
  );
}

export function GrantFormView({ entityId, pools, people, onDone }: { entityId: string; pools: Pool[]; people: Employee[]; onDone: () => void }) {
  const [f, setF] = useState<GrantForm>(blankGrant());
  const set = (p: Partial<GrantForm>) => setF({ ...f, ...p });
  const free = pools.find((p) => p.id === f.poolId)?.free ?? null;
  return (
    <FormShell label="Grant options" problem={grantProblem(f, free)} started={!!f.options} onSubmit={async () => {
      unwrap(await api.POST("/entities/{entityId}/esop-grants", { params: { path: { entityId } }, body: grantBody(f) as never })); setF(blankGrant()); onDone();
    }}>
      <div style={grid}>
        <Field id="gr-pool" label="Pool"><select id="gr-pool" className="input" value={f.poolId} onChange={(e) => set({ poolId: e.target.value })}><option value="">Choose…</option>{pools.map((p) => <option key={p.id} value={p.id}>{p.name} ({groupIndian(p.free)} left)</option>)}</select></Field>
        <Field id="gr-emp" label="Employee"><select id="gr-emp" className="input" value={f.employeeId} onChange={(e) => set({ employeeId: e.target.value })}><option value="">Choose…</option>{people.map((p) => <option key={p.id} value={p.id}>{p.full_name}</option>)}</select></Field>
        <Field id="gr-date" label="Granted on"><input id="gr-date" type="date" className="input" value={f.grantedOn} onChange={(e) => set({ grantedOn: e.target.value })} /></Field>
        <Field id="gr-opt" label="Options"><input id="gr-opt" className="input mono" inputMode="numeric" value={f.options} onChange={(e) => set({ options: e.target.value.replace(/\D/g, "") })} /></Field>
        <Field id="gr-strike" label="Strike price (₹)"><input id="gr-strike" className="input mono" inputMode="decimal" value={f.strike} onChange={(e) => set({ strike: e.target.value })} /></Field>
        <Field id="gr-cliff" label="Cliff (months)"><input id="gr-cliff" className="input mono" inputMode="numeric" value={f.cliff} onChange={(e) => set({ cliff: e.target.value.replace(/\D/g, "") })} /></Field>
        <Field id="gr-vest" label="Vests over (months)"><input id="gr-vest" className="input mono" inputMode="numeric" value={f.vesting} onChange={(e) => set({ vesting: e.target.value.replace(/\D/g, "") })} /></Field>
        <Field id="gr-freq" label="Vests"><select id="gr-freq" className="input" value={f.frequency} onChange={(e) => set({ frequency: e.target.value })}><option value="monthly">Monthly</option><option value="quarterly">Quarterly</option><option value="annual">Yearly</option></select></Field>
      </div>
    </FormShell>
  );
}

/** Say where an application stands: applied, granted with its certificate number, and so on. */
export function RecordBenefit({ entityId, benefit, onDone, onCancel }: { entityId: string; benefit: Benefit; onDone: () => void; onCancel: () => void }) {
  const [f, setF] = useState<BenefitForm>(blankBenefit(benefit));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  async function save() {
    setBusy(true); setError(null);
    try { unwrap(await api.PUT("/entities/{entityId}/benefits/{scheme_code}", { params: { path: { entityId, scheme_code: benefit.scheme_code } }, body: benefitBody(f) as never })); onDone(); }
    catch (e) { setError(e); setBusy(false); }
  }
  const k = benefit.scheme_code;
  return (
    <form style={{ display: "flex", flexDirection: "column", gap: 10, padding: "0 20px 14px" }} onSubmit={(e) => { e.preventDefault(); if (!busy) void save(); }}>
      <div style={grid}>
        <Field id={`bs-${k}`} label="Status"><select id={`bs-${k}`} className="input" style={{ height: 40 }} value={f.status} onChange={(e) => setF({ ...f, status: e.target.value })}>{RECORDABLE.map(([v, l]) => <option key={v} value={v}>{l}</option>)}</select></Field>
        <Field id={`br-${k}`} label="Certificate or reference no."><input id={`br-${k}`} className="input mono" style={{ height: 40 }} value={f.reference} onChange={(e) => setF({ ...f, reference: e.target.value })} /></Field>
        <Field id={`bv-${k}`} label="Valid until"><input id={`bv-${k}`} type="date" className="input" style={{ height: 40 }} value={f.validUntil} onChange={(e) => setF({ ...f, validUntil: e.target.value })} /></Field>
      </div>
      <ErrorNote error={error} />
      <span style={{ display: "flex", gap: 10 }}><button type="submit" className="btn primary" disabled={busy}>{busy ? "Saving…" : "Save"}</button><button type="button" className="btn" onClick={onCancel}>Cancel</button></span>
    </form>
  );
}
