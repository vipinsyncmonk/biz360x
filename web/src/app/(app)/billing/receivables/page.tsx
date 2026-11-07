"use client";
import Link from "next/link";
import { useMemo, useState } from "react";
import { api, newIdempotencyKey, unwrap, unwrapEmpty } from "@/lib/api";
import { formatInr, isoDate, shortDate } from "@/lib/format";
import { useLoad } from "@/lib/hooks";
import {
  Allocation, BUCKETS, FREQ_LABEL, METHODS, METHOD_LABEL, OpenInvoice, RECEIPT_STATUS, allocationBody, allocationProblem, allocatedTotal, oldestFirst,
  pauseDefault, pauseReady, receiptReady, recurringState, stepSentence, suggestAllocation, unallocated,
} from "@/lib/receivables";
import { useSession } from "@/lib/session";
import { ErrorNote, Loading, Pill } from "@/components/ui";

interface Summary { collected: string; outstanding: string; overdue: string; overdue_count: number; tds_to_claim: string }
interface AgeRow { party_id: string; legal_name: string; msme_class: string | null; b0_30: string; b31_45: string; b46_60: string; b61_90: string; b90_plus: string; total: string; [k: string]: unknown }
interface Receipt { id: string; receipt_no: string; party_id: string; party_name: string; received_on: string; amount: string; method: string; utr: string | null; status: string; unallocated: string; allocations: Array<{ invoice_id: string; number: string | null; amount: string }> }
interface Party { id: string; legal_name: string }
interface Sequence { id: string; name: string; is_default: boolean; steps: Array<{ offset_days: number; action: string; channels?: string[] | null }> }
interface Recurring { id: string; name: string; party_id: string; frequency: string; next_run_on: string; ends_on: string | null; auto_issue: boolean; is_active: boolean }
interface DocRow extends OpenInvoice { doc_type: string; status: string; party_id: string }

const money = (v: string) => formatInr(v);

export default function Receivables() {
  const { entity } = useSession();
  const id = entity?.id ?? null;
  const [error, setError] = useState<unknown>(null);

  const summary = useLoad(async () => unwrap(await api.GET("/entities/{entityId}/sales/summary", { params: { path: { entityId: id! } } })) as unknown as Summary, id);
  const ageing = useLoad(async () => unwrap(await api.GET("/entities/{entityId}/receivables/ageing", { params: { path: { entityId: id! } } })) as unknown as AgeRow[], id);
  const receipts = useLoad(async () => unwrap(await api.GET("/entities/{entityId}/receipts", { params: { path: { entityId: id! }, query: { limit: 20 } } })).data as unknown as Receipt[], id);
  const parties = useLoad(async () => unwrap(await api.GET("/entities/{entityId}/parties", { params: { path: { entityId: id! }, query: { role: "customer", limit: 200 } } })).data as unknown as Party[], id);
  const sequences = useLoad(async () => unwrap(await api.GET("/entities/{entityId}/dunning-sequences", { params: { path: { entityId: id! } } })).data as unknown as Sequence[], id);
  const recurring = useLoad(async () => unwrap(await api.GET("/entities/{entityId}/recurring-invoices", { params: { path: { entityId: id! } } })).data as unknown as Recurring[], id);

  const reloadMoney = () => { summary.reload(); ageing.reload(); receipts.reload(); };
  const nameOf = (pid: string) => (parties.data ?? []).find((p) => p.id === pid)?.legal_name ?? "Customer";
  const s = summary.data;

  if (!entity) return <main className="page"><div className="card card-pad">Select a workspace first.</div></main>;
  return (
    <main className="page">
      <div>
        <Link href="/billing" style={{ fontSize: 14 }}>← Sales &amp; Billing</Link>
        <h1 className="disp" style={{ margin: "8px 0 0", fontSize: 36 }}>Collections</h1>
      </div>
      <ErrorNote error={error ?? summary.error} />

      <section aria-label="Key numbers" className="kpis">
        <Tile label="Outstanding" value={s ? formatInr(s.outstanding, { decimals: false }) : "…"} note="Open balance on issued invoices" />
        <Tile label="Overdue" value={s ? formatInr(s.overdue, { decimals: false }) : "…"} note={s ? `${s.overdue_count} ${s.overdue_count === 1 ? "invoice" : "invoices"}` : ""} warn={!!s && s.overdue_count > 0} />
        <Tile label="Collected this FY" value={s ? formatInr(s.collected, { decimals: false }) : "…"} note="Confirmed receipts" />
        <Tile label="TDS to claim" value={s ? formatInr(s.tds_to_claim, { decimals: false }) : "…"} note="Deducted by customers" />
      </section>

      <div className="cols">
        <div className="col-main">
          <Ageing rows={ageing.data} loading={ageing.loading} error={ageing.error} entityId={id!} onError={setError} />
          <Receipts rows={receipts.data} loading={receipts.loading} error={receipts.error} entityId={id!} onChange={reloadMoney} onError={setError} />
        </div>
        <aside className="col-side">
          <RecordReceipt entityId={id!} parties={parties.data ?? []} onSaved={reloadMoney} />
          <section className="card card-pad" style={{ display: "flex", flexDirection: "column", gap: 10 }}>
            <h2 style={{ margin: 0 }}>Payment reminders</h2>
            <ErrorNote error={sequences.error} />
            {sequences.loading && <Loading what="reminders" />}
            {(sequences.data ?? []).length === 0 && !sequences.loading && <span className="t13">No reminder sequence is set up yet.</span>}
            {(sequences.data ?? []).map((q) => (
              <div key={q.id} style={{ borderTop: "1px solid var(--line-2)", paddingTop: 8 }}>
                <div style={{ display: "flex", gap: 8, alignItems: "center" }}><strong>{q.name}</strong>{q.is_default && <Pill tone="ok">Default</Pill>}</div>
                <ol style={{ margin: "6px 0 0", paddingLeft: 18, fontSize: 13, lineHeight: 1.5 }}>{q.steps.map((st, i) => <li key={i}>{stepSentence(st)}</li>)}</ol>
              </div>
            ))}
            <span className="t13">Reminders are queued for sending. Each step runs once per invoice. Pause a customer from the ageing table.</span>
          </section>
          <section className="card card-pad" style={{ display: "flex", flexDirection: "column", gap: 10 }}>
            <h2 style={{ margin: 0 }}>Recurring invoices</h2>
            <ErrorNote error={recurring.error} />
            {recurring.loading && <Loading what="recurring invoices" />}
            {(recurring.data ?? []).length === 0 && !recurring.loading && <span className="t13">Nothing recurring yet.</span>}
            {(recurring.data ?? []).map((r) => {
              const st = recurringState(r);
              return (
                <div key={r.id} style={{ borderTop: "1px solid var(--line-2)", paddingTop: 8, display: "flex", justifyContent: "space-between", gap: 8, alignItems: "flex-start" }}>
                  <span style={{ fontSize: 14 }}>{r.name}
                    <span className="t13" style={{ display: "block" }}>{nameOf(r.party_id)} · {FREQ_LABEL[r.frequency] ?? r.frequency}</span>
                    <span className="t13" style={{ display: "block" }}>Next {shortDate(r.next_run_on)}{r.ends_on ? ` · ends ${shortDate(r.ends_on)}` : ""}{r.auto_issue ? " · issues itself" : " · drafts only"}</span>
                  </span>
                  <span style={{ display: "flex", flexDirection: "column", gap: 6, alignItems: "flex-end" }}>
                    <Pill tone={st.tone}>{st.label}</Pill>
                    <button type="button" className="linkbtn" style={{ color: "var(--cobalt)" }} onClick={async () => {
                      try { setError(null); unwrap(await api.PATCH("/entities/{entityId}/recurring-invoices/{id}", { params: { path: { entityId: id!, id: r.id }, header: { "If-Match": "*" } }, body: { is_active: !r.is_active } as never })); recurring.reload(); }
                      catch (e) { setError(e); }
                    }}>{r.is_active ? "Pause" : "Resume"}</button>
                  </span>
                </div>
              );
            })}
          </section>
        </aside>
      </div>
    </main>
  );
}

// ---------------------------------------------------------------- ageing
function Ageing({ rows, loading, error, entityId, onError }: { rows: AgeRow[] | null; loading: boolean; error: unknown; entityId: string; onError: (e: unknown) => void }) {
  const [pausing, setPausing] = useState<string | null>(null);
  const [until, setUntil] = useState(pauseDefault());
  const [reason, setReason] = useState("");
  const [paused, setPaused] = useState<Record<string, string>>({});

  async function pause(partyId: string) {
    try {
      onError(null);
      unwrapEmpty(await api.POST("/entities/{entityId}/parties/{id}/pause-dunning", { params: { path: { entityId, id: partyId } }, body: { until, ...(reason.trim() ? { reason: reason.trim() } : {}) } }));
      setPaused({ ...paused, [partyId]: until });
      setPausing(null); setReason("");
    } catch (e) { onError(e); }
  }

  return (
    <section className="card" style={{ overflow: "hidden" }}>
      <div className="cardhead"><h2>Who owes what, by age of invoice</h2></div>
      <div style={{ padding: "0 20px" }}><ErrorNote error={error} /></div>
      {loading ? <Loading what="the ageing" /> : (
        <div className="tablewrap">
          <table style={{ minWidth: 720 }}>
            <thead><tr><th scope="col">Customer</th>{BUCKETS.map(([k, l]) => <th key={k} scope="col" style={{ textAlign: "right" }}>{l} days</th>)}<th scope="col" style={{ textAlign: "right" }}>Total</th><th scope="col"><span className="sr">Reminders</span></th></tr></thead>
            <tbody>
              {(rows ?? []).map((r) => (
                <tr key={r.party_id}>
                  <td>{r.legal_name}{r.msme_class && <span className="t13" style={{ display: "block" }}>MSME · {r.msme_class}</span>}</td>
                  {BUCKETS.map(([k]) => { const v = String(r[k] ?? "0.00"); return <td key={k} className="mono" style={{ textAlign: "right", color: v === "0.00" ? "var(--muted)" : undefined }}>{v === "0.00" ? "–" : money(v)}</td>; })}
                  <td className="mono" style={{ textAlign: "right", fontWeight: 600 }}>{money(r.total)}</td>
                  <td style={{ minWidth: 150 }}>
                    {paused[r.party_id] ? <span className="t13">Paused to {shortDate(paused[r.party_id])}</span>
                      : pausing === r.party_id ? (
                        <span style={{ display: "flex", flexDirection: "column", gap: 6 }}>
                          <input aria-label={`Pause reminders for ${r.legal_name} until`} type="date" className="input" style={{ height: 36, fontSize: 13 }} value={until} onChange={(e) => setUntil(e.target.value)} />
                          <input aria-label="Reason" className="input" style={{ height: 36, fontSize: 13 }} placeholder="Reason (optional)" value={reason} onChange={(e) => setReason(e.target.value)} />
                          <span style={{ display: "flex", gap: 6 }}>
                            <button type="button" className="btn primary" style={{ height: 34 }} disabled={!pauseReady(until)} onClick={() => void pause(r.party_id)}>Pause</button>
                            <button type="button" className="btn" style={{ height: 34 }} onClick={() => setPausing(null)}>Cancel</button>
                          </span>
                        </span>
                      ) : <button type="button" className="linkbtn" style={{ color: "var(--cobalt)" }} onClick={() => { setPausing(r.party_id); setUntil(pauseDefault()); }}>Pause reminders</button>}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          {(rows ?? []).length === 0 && <div className="empty">Nothing is owed to you right now.</div>}
        </div>
      )}
    </section>
  );
}

// ---------------------------------------------------------------- receipts
function Receipts({ rows, loading, error, entityId, onChange, onError }: { rows: Receipt[] | null; loading: boolean; error: unknown; entityId: string; onChange: () => void; onError: (e: unknown) => void }) {
  const [open, setOpen] = useState<string | null>(null);
  return (
    <section className="card" style={{ overflow: "hidden" }}>
      <div className="cardhead"><h2>Recent receipts</h2></div>
      <div style={{ padding: "0 20px" }}><ErrorNote error={error} /></div>
      {loading ? <Loading what="receipts" /> : (
        <div className="tablewrap">
          <table style={{ minWidth: 640 }}>
            <thead><tr><th scope="col">Receipt</th><th scope="col">Customer</th><th scope="col">Received</th><th scope="col">Method</th><th scope="col" style={{ textAlign: "right" }}>Amount</th><th scope="col" style={{ textAlign: "right" }}>Not yet allocated</th></tr></thead>
            <tbody>
              {(rows ?? []).map((r) => (
                <ReceiptRow key={r.id} r={r} entityId={entityId} open={open === r.id} onToggle={() => setOpen(open === r.id ? null : r.id)} onDone={() => { setOpen(null); onChange(); }} onError={onError} />
              ))}
            </tbody>
          </table>
          {(rows ?? []).length === 0 && <div className="empty">No receipts yet. Record one on the right, or accept a suggested match under Finance &amp; Books.</div>}
        </div>
      )}
    </section>
  );
}

function ReceiptRow({ r, entityId, open, onToggle, onDone, onError }: { r: Receipt; entityId: string; open: boolean; onToggle: () => void; onDone: () => void; onError: (e: unknown) => void }) {
  const left = r.unallocated !== "0.00" && r.status === "confirmed";
  return (
    <>
      <tr>
        <td className="mono" style={{ fontWeight: 600 }}>{r.receipt_no}{r.status !== "confirmed" && <span className="t13" style={{ display: "block" }}>{RECEIPT_STATUS[r.status] ?? r.status}</span>}
          {r.utr && <span className="t13" style={{ display: "block" }}>{r.utr}</span>}</td>
        <td>{r.party_name}{r.allocations.length > 0 && <span className="t13" style={{ display: "block" }}>Against {r.allocations.map((a) => a.number ?? "invoice").join(", ")}</span>}</td>
        <td className="t13">{shortDate(r.received_on)}</td>
        <td className="t13">{METHOD_LABEL[r.method] ?? r.method}</td>
        <td className="mono" style={{ textAlign: "right" }}>{money(r.amount)}</td>
        <td style={{ textAlign: "right" }}>
          {left ? <button type="button" className="linkbtn" style={{ color: "var(--cobalt)" }} aria-expanded={open} onClick={onToggle}>{money(r.unallocated)} · Allocate</button> : <span className="t13">–</span>}
        </td>
      </tr>
      {open && <tr><td colSpan={6} style={{ background: "var(--bg-2, #FAFAF7)" }}><AllocateMore r={r} entityId={entityId} onDone={onDone} onError={onError} /></td></tr>}
    </>
  );
}

/** Customer's open invoices, loaded once the person asks. */
function useOpenInvoices(entityId: string, partyId: string | null) {
  return useLoad(async () => {
    const d = unwrap(await api.GET("/entities/{entityId}/sales-documents", { params: { path: { entityId }, query: { party_id: partyId!, status: "issued", limit: 200 } } })).data as unknown as DocRow[];
    return oldestFirst(d.filter((x) => ["tax_invoice", "export_invoice", "bill_of_supply", "debit_note"].includes(x.doc_type) && x.balance_due !== "0.00"));
  }, partyId ? `${entityId}|${partyId}` : null);
}

function AllocateMore({ r, entityId, onDone, onError }: { r: Receipt; entityId: string; onDone: () => void; onError: (e: unknown) => void }) {
  const inv = useOpenInvoices(entityId, r.party_id);
  const [manual, setManual] = useState<Allocation | null>(null);
  const [saving, setSaving] = useState(false);
  const invoices = inv.data ?? [];
  const alloc = manual ?? suggestAllocation(r.unallocated, invoices);
  const problem = allocationProblem(r.unallocated, alloc, invoices);
  const body = allocationBody(alloc);

  async function save() {
    setSaving(true); onError(null);
    try {
      unwrap(await api.POST("/entities/{entityId}/receipts/{id}/allocate", { params: { path: { entityId, id: r.id }, header: { "Idempotency-Key": newIdempotencyKey() } }, body: body as never }));
      onDone();
    } catch (e) { onError(e); setSaving(false); }
  }
  return (
    <div style={{ padding: "8px 0", display: "flex", flexDirection: "column", gap: 10 }}>
      <ErrorNote error={inv.error} />
      {inv.loading && <Loading what="open invoices" />}
      {!inv.loading && invoices.length === 0 && <span className="t13">{r.party_name} has no open invoices. The money stays as an advance.</span>}
      {invoices.length > 0 && <AllocTable invoices={invoices} alloc={alloc} onChange={setManual} />}
      {problem && <span className="t13" style={{ color: "var(--amber)" }}>{problem}</span>}
      <span style={{ display: "flex", gap: 10, alignItems: "center" }}>
        <button type="button" className="btn primary" disabled={saving || body.length === 0 || !!problem} onClick={() => void save()}>{saving ? "Saving…" : `Allocate ${money(allocatedTotal(alloc))}`}</button>
        <span className="t13">of {money(r.unallocated)} not yet allocated</span>
      </span>
    </div>
  );
}

function AllocTable({ invoices, alloc, onChange }: { invoices: OpenInvoice[]; alloc: Allocation; onChange: (a: Allocation) => void }) {
  return (
    <table style={{ minWidth: 480 }}>
      <thead><tr><th scope="col">Invoice</th><th scope="col">Due</th><th scope="col" style={{ textAlign: "right" }}>Owed</th><th scope="col">Apply</th></tr></thead>
      <tbody>
        {invoices.map((i) => (
          <tr key={i.id}>
            <td className="mono">{i.number ?? "Draft"}</td>
            <td className="t13">{i.due_date ? shortDate(i.due_date) : "–"}</td>
            <td className="mono" style={{ textAlign: "right" }}>{money(i.balance_due)}</td>
            <td><input aria-label={`Amount to apply to ${i.number}`} className="input mono" style={{ height: 36, fontSize: 14, width: 120 }} inputMode="decimal" placeholder="0.00"
                       value={alloc[i.id] ?? ""} onChange={(e) => onChange({ ...alloc, [i.id]: e.target.value })} /></td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

// ---------------------------------------------------------------- record a receipt
function RecordReceipt({ entityId, parties, onSaved }: { entityId: string; parties: Party[]; onSaved: () => void }) {
  const [partyId, setPartyId] = useState("");
  const [date, setDate] = useState(isoDate(new Date()));
  const [amount, setAmount] = useState("");
  const [method, setMethod] = useState("neft");
  const [utr, setUtr] = useState("");
  const [manual, setManual] = useState<Allocation | null>(null);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [done, setDone] = useState<string | null>(null);

  const inv = useOpenInvoices(entityId, partyId || null);
  const invoices = useMemo(() => inv.data ?? [], [inv.data]);
  const alloc = manual ?? suggestAllocation(amount, invoices);
  const problem = allocationProblem(amount, alloc, invoices);
  const ready = receiptReady({ partyId, date, amount }) && !problem;
  const advance = unallocated(amount, alloc);

  async function save() {
    setSaving(true); setError(null); setDone(null);
    try {
      const r = unwrap(await api.POST("/entities/{entityId}/receipts", { params: { path: { entityId }, header: { "Idempotency-Key": newIdempotencyKey() } },
        body: { party_id: partyId, received_on: date, amount: amount.trim(), method, ...(utr.trim() ? { utr: utr.trim() } : {}), allocations: allocationBody(alloc) } as never })) as unknown as { receipt_no: string };
      setDone(r.receipt_no);
      setAmount(""); setUtr(""); setManual(null);
      onSaved(); inv.reload();
    } catch (e) { setError(e); }
    setSaving(false);
  }

  return (
    <section className="card card-pad" style={{ display: "flex", flexDirection: "column", gap: 12 }}>
      <h2 style={{ margin: 0 }}>Record a receipt</h2>
      <div className="field"><label htmlFor="rc-party">Customer</label>
        <select id="rc-party" className="input" value={partyId} onChange={(e) => { setPartyId(e.target.value); setManual(null); }}>
          <option value="">Choose…</option>{parties.map((p) => <option key={p.id} value={p.id}>{p.legal_name}</option>)}
        </select></div>
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 10 }}>
        <div className="field"><label htmlFor="rc-amount">Amount (₹)</label><input id="rc-amount" className="input mono" inputMode="decimal" value={amount} onChange={(e) => { setAmount(e.target.value); setManual(null); }} /></div>
        <div className="field"><label htmlFor="rc-date">Received on</label><input id="rc-date" type="date" className="input" value={date} onChange={(e) => setDate(e.target.value)} /></div>
        <div className="field"><label htmlFor="rc-method">Method</label>
          <select id="rc-method" className="input" value={method} onChange={(e) => setMethod(e.target.value)}>{METHODS.map(([k, l]) => <option key={k} value={k}>{l}</option>)}</select></div>
        <div className="field"><label htmlFor="rc-utr">UTR or cheque no.</label><input id="rc-utr" className="input mono" value={utr} onChange={(e) => setUtr(e.target.value)} /></div>
      </div>
      {partyId && inv.loading && <Loading what="open invoices" />}
      <ErrorNote error={inv.error} />
      {partyId && !inv.loading && invoices.length === 0 && <span className="t13">No open invoices for this customer. The money will be held as an advance.</span>}
      {invoices.length > 0 && (
        <div className="tablewrap">
          <AllocTable invoices={invoices} alloc={alloc} onChange={setManual} />
          <span className="t13" style={{ display: "block", marginTop: 6 }}>Filled in oldest first. Change any figure.</span>
        </div>
      )}
      {problem && <span className="t13" style={{ color: "var(--amber)" }}>{problem}</span>}
      {receiptReady({ partyId, date, amount }) && !problem && (
        <span className="t13">{money(allocatedTotal(alloc))} settles invoices{advance !== "0.00" ? `; ${money(advance)} is kept as a customer advance` : ""}.</span>
      )}
      <ErrorNote error={error} />
      {done && <span className="t13" role="status" style={{ color: "var(--green)" }}>Recorded as {done}.</span>}
      <button type="button" className="btn primary" disabled={saving || !ready} onClick={() => void save()}>{saving ? "Saving…" : "Record receipt"}</button>
      <span className="t13">The receipt is posted to the books straight away.</span>
    </section>
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
