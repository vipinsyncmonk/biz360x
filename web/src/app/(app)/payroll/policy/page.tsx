"use client";
import Link from "next/link";
import { useEffect, useState } from "react";
import { api, unwrap } from "@/lib/api";
import { isoDate, shortDate } from "@/lib/format";
import { useLoad } from "@/lib/hooks";
import { Policy, PolicyDoc, WEEKDAYS, ackText, categoryLabel, checklistKey, policyBody, policyProblem, weekOffText } from "@/lib/hr";
import { useSession } from "@/lib/session";
import { PublishDoc } from "@/components/HrForms";
import { ErrorNote, Loading, Pill } from "@/components/ui";

const h40 = { height: 40 } as const;
const grid = { display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(190px, 1fr))", gap: 12 } as const;

export default function HrPolicy() {
  const { entity } = useSession();
  const id = entity?.id ?? null;
  const p = { path: { entityId: id! } };
  const loaded = useLoad(async () => unwrap(await api.GET("/entities/{entityId}/hr/policy", { params: p })) as unknown as Policy, id);
  const docs = useLoad(async () => unwrap(await api.GET("/entities/{entityId}/hr/policy-documents", { params: p })).data as unknown as PolicyDoc[], id);
  const [pol, setPol] = useState<Policy | null>(null);
  const [from, setFrom] = useState(isoDate(new Date()));
  const [busy, setBusy] = useState(false);
  const [failure, setFailure] = useState<unknown>(null);
  const [note, setNote] = useState("");
  const [newItem, setNewItem] = useState("");
  const [publishing, setPublishing] = useState(false);
  const [open, setOpen] = useState<string | null>(null);
  const [revising, setRevising] = useState(false);
  useEffect(() => { if (loaded.data) setPol(loaded.data); }, [loaded.data]);
  const detail = useLoad(async () => unwrap(await api.GET("/entities/{entityId}/hr/policy-documents/{id}", { params: { path: { entityId: id!, id: open! } } })) as unknown as PolicyDoc, id && open ? `${id}|${open}` : null);

  if (!entity) return <main className="page"><div className="card card-pad">Select a workspace first.</div></main>;
  const problem = pol ? policyProblem(pol) : null;
  const sal = (patch: Partial<Policy["salary"]>) => setPol((x) => x && { ...x, salary: { ...x.salary, ...patch } });
  const lv = (patch: Partial<Policy["leave"]>) => setPol((x) => x && { ...x, leave: { ...x.leave, ...patch } });
  const pay = (patch: Partial<Policy["payroll"]>) => setPol((x) => x && { ...x, payroll: { ...x.payroll, ...patch } });
  const list = (fn: (c: Policy["onboarding"]["checklist"]) => Policy["onboarding"]["checklist"]) => setPol((x) => x && { ...x, onboarding: { checklist: fn(x.onboarding.checklist) } });
  const num = (v: string) => (/^\d{1,3}$/.test(v) ? Number(v) : 0);

  async function save() {
    if (!pol) return;
    setBusy(true); setFailure(null); setNote("");
    try {
      setPol(unwrap(await api.PUT("/entities/{entityId}/hr/policy", { params: p, body: policyBody(pol, from) as never })) as unknown as Policy);
      setNote(from === isoDate(new Date()) ? "Saved. New salaries and leave follow this policy from today." : `Saved. It takes effect on ${shortDate(from)}.`);
      loaded.reload();
    } catch (e) { setFailure(e); } finally { setBusy(false); }
  }
  async function acknowledge(docId: string, employeeId: string) {
    setFailure(null);
    try { unwrap(await api.POST("/entities/{entityId}/hr/policy-documents/{id}/acknowledgements", { params: { path: { entityId: id!, id: docId } }, body: { employee_id: employeeId } as never })); detail.reload(); docs.reload(); }
    catch (e) { setFailure(e); }
  }

  return (
    <main className="page">
      <div>
        <Link href="/payroll" style={{ fontSize: 14 }}>← Payroll &amp; HR</Link>
        <h1 className="disp" style={{ margin: "8px 0 0", fontSize: 36 }}>HR policy</h1>
      </div>
      <span className="t13">State the policy once and onboarding, leave and payroll follow it. Changing it affects salaries set and leave taken from the date you choose, never the past. The figures you start with are a starter policy: review them with your CA before they drive a real payroll.</span>
      <ErrorNote error={loaded.error ?? failure} />
      {note && <div role="status" className="t13">{note}</div>}

      {!pol ? <Loading what="the policy" /> : (
        <>
          {pol.is_default && <div className="card card-pad" role="status"><Pill tone="wait">Starter policy</Pill> <span className="t13">You have not saved a policy yet. These are the starting values; change what differs and save.</span></div>}

          <section className="card card-pad" aria-label="Salary from a CTC" style={{ display: "flex", flexDirection: "column", gap: 12 }}>
            <h2 style={{ margin: 0 }}>How a CTC becomes a salary</h2>
            <div style={grid}>
              <div className="field"><label htmlFor="pol-basic">Basic pay (% of CTC)</label><input id="pol-basic" className="input mono" style={h40} inputMode="decimal" value={pol.salary.basic_pct_of_ctc} onChange={(e) => sal({ basic_pct_of_ctc: e.target.value })} /></div>
              <div className="field"><label htmlFor="pol-hra">HRA (% of basic)</label><input id="pol-hra" className="input mono" style={h40} inputMode="decimal" value={pol.salary.hra_pct_of_basic} onChange={(e) => sal({ hra_pct_of_basic: e.target.value })} /></div>
            </div>
            {([["employer_pf_in_ctc", "The CTC includes the employer's PF"], ["pf_on_full_basic", "PF on the whole basic pay, not capped at ₹15,000"], ["employer_esi_in_ctc", "The CTC includes the employer's ESI"],
              ["gratuity_in_ctc", "The CTC includes gratuity (4.81% of basic)"]] as const).map(([k, label]) => (
              <label key={k} style={{ display: "flex", gap: 8, alignItems: "center" }}><input type="checkbox" checked={pol.salary[k]} onChange={(e) => sal({ [k]: e.target.checked })} /><span>{label}</span></label>
            ))}
            <span className="t13">The special allowance is whatever is left of the CTC. Basic below half of the gross pay is flagged by the wage-code check.</span>
          </section>

          <section className="card card-pad" aria-label="Leave" style={{ display: "flex", flexDirection: "column", gap: 12 }}>
            <h2 style={{ margin: 0 }}>Leave</h2>
            <div style={grid}>
              <div className="field"><label htmlFor="pol-year">Leave year</label>
                <select id="pol-year" className="input" style={h40} value={pol.leave.leave_year} onChange={(e) => lv({ leave_year: e.target.value as Policy["leave"]["leave_year"] })}><option value="financial">April to March</option><option value="calendar">January to December</option></select></div>
            </div>
            <fieldset style={{ border: 0, padding: 0, margin: 0 }}><legend style={{ fontSize: 14, fontWeight: 500, marginBottom: 6 }}>Weekly off · {weekOffText(pol.leave.week_off)}</legend>
              <span style={{ display: "flex", gap: 12, flexWrap: "wrap" }}>{WEEKDAYS.map((d, i) => (
                <label key={d} style={{ display: "flex", gap: 6, alignItems: "center" }}><input type="checkbox" checked={pol.leave.week_off.includes(i)}
                  onChange={(e) => lv({ week_off: e.target.checked ? [...pol.leave.week_off, i] : pol.leave.week_off.filter((x) => x !== i) })} /><span>{d}</span></label>
              ))}</span></fieldset>
            <label style={{ display: "flex", gap: 8, alignItems: "center" }}><input type="checkbox" checked={pol.leave.lop_when_exhausted} onChange={(e) => lv({ lop_when_exhausted: e.target.checked })} /><span>When leave runs out, the extra days are unpaid (otherwise the request is refused)</span></label>
            <div className="tablewrap"><table style={{ minWidth: 520 }} aria-label="Leave types">
              <thead><tr><th scope="col">Leave</th><th scope="col">Days a year</th><th scope="col">Earned</th><th scope="col">Carry forward</th></tr></thead>
              <tbody>{pol.leave_types.filter((t) => t.is_active).map((t) => (
                <tr key={t.code}><td>{t.name} <span className="t13 mono">{t.code}</span></td><td>{t.paid ? t.annual_quota : "Unpaid"}</td><td>{!t.paid ? "–" : t.accrual === "monthly" ? "Month by month" : "At the start of the year"}</td><td>{t.paid ? `Up to ${t.carry_forward_max}` : "–"}</td></tr>
              ))}</tbody>
            </table></div>
          </section>

          <section className="card card-pad" aria-label="Payroll calendar" style={{ display: "flex", flexDirection: "column", gap: 12 }}>
            <h2 style={{ margin: 0 }}>Payroll calendar and terms</h2>
            <div style={grid}>
              {([["inputs_cutoff_day", "Attendance cut-off (day of month)"], ["pay_day", "Pay day (day of month)"], ["probation_months", "Probation (months)"], ["notice_days", "Notice period (days)"]] as const).map(([k, label]) => (
                <div key={k} className="field"><label htmlFor={`pol-${k}`}>{label}</label><input id={`pol-${k}`} className="input mono" style={h40} inputMode="numeric" value={String(pol.payroll[k])} onChange={(e) => pay({ [k]: num(e.target.value) })} /></div>
              ))}
            </div>
          </section>

          <section className="card card-pad" aria-label="Onboarding checklist" style={{ display: "flex", flexDirection: "column", gap: 10 }}>
            <h2 style={{ margin: 0 }}>Onboarding checklist</h2>
            <span className="t13">Every new hire gets this list. Required items must be done before the person joins payroll.</span>
            {pol.onboarding.checklist.map((c, i) => (
              <div key={c.key} style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center" }}>
                <input aria-label={`Checklist item ${i + 1}`} className="input" style={{ ...h40, flex: "1 1 240px" }} value={c.title} onChange={(e) => list((l) => l.map((x, j) => (j === i ? { ...x, title: e.target.value } : x)))} />
                <label style={{ display: "flex", gap: 6, alignItems: "center" }}><input type="checkbox" checked={c.required} onChange={(e) => list((l) => l.map((x, j) => (j === i ? { ...x, required: e.target.checked } : x)))} /><span>Required</span></label>
                {c.auto ? <span className="t13">Ticks itself</span> : <button type="button" className="linkbtn" aria-label={`Remove ${c.title}`} onClick={() => list((l) => l.filter((_, j) => j !== i))}>Remove</button>}
              </div>
            ))}
            <form style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "flex-end" }} onSubmit={(e) => {
              e.preventDefault(); const t = newItem.trim(); if (!t) return;
              list((l) => [...l, { key: checklistKey(t, l.map((x) => x.key)), title: t, required: false, auto: null }]); setNewItem("");
            }}>
              <div className="field" style={{ flex: "1 1 240px" }}><label htmlFor="pol-newitem">Add an item</label><input id="pol-newitem" className="input" style={h40} autoComplete="off" maxLength={120} value={newItem} onChange={(e) => setNewItem(e.target.value)} /></div>
              <button type="submit" className="btn" disabled={!newItem.trim()}>Add</button>
            </form>
          </section>

          <section className="card card-pad" aria-label="Save the policy" style={{ display: "flex", gap: 12, flexWrap: "wrap", alignItems: "flex-end" }}>
            <div className="field"><label htmlFor="pol-from">Takes effect from</label><input id="pol-from" type="date" className="input" style={h40} min={isoDate(new Date())} value={from} onChange={(e) => setFrom(e.target.value)} /></div>
            <button type="button" className="btn primary" disabled={busy || !!problem || !from} onClick={() => void save()}>{busy ? "Saving…" : "Save policy"}</button>
            {problem && <span className="t13" role="alert" style={{ color: "var(--amber)" }}>{problem}</span>}
            {!problem && pol.effective_from && <span className="t13">In force since {shortDate(pol.effective_from)}{pol.next_change_on ? ` · a change is set for ${shortDate(pol.next_change_on)}` : ""}. Only an owner or approver can save it.</span>}
          </section>
        </>
      )}

      <section className="card" style={{ overflow: "hidden" }} aria-label="Handbook">
        <div style={{ padding: "18px 20px", display: "flex", justifyContent: "space-between", gap: 12, flexWrap: "wrap", alignItems: "center" }}>
          <h2 style={{ margin: 0 }}>Company policies for employees</h2>
          <button type="button" className="btn" aria-expanded={publishing} onClick={() => setPublishing(!publishing)}>+ Policy document</button>
        </div>
        <div style={{ padding: "0 20px" }}><ErrorNote error={docs.error} /></div>
        {publishing && <div style={{ padding: "0 20px 16px" }}><PublishDoc entityId={id!} onCancel={() => setPublishing(false)} onDone={() => { setPublishing(false); docs.reload(); }} /></div>}
        {docs.loading && !docs.data ? <Loading what="policies" /> : (docs.data ?? []).length === 0
          ? <div className="empty">No policy documents yet. Publish your leave policy, code of conduct or POSH policy and record who has acknowledged it.</div>
          : (docs.data ?? []).map((d) => (
            <div key={d.id} style={{ borderTop: "1px solid var(--line-2)" }}>
              <div style={{ padding: "14px 20px", display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(190px, 1fr))", gap: 12, alignItems: "start" }}>
                <span style={{ fontWeight: 600 }}>{d.title}<span className="t13" style={{ display: "block", fontWeight: 400 }}>{categoryLabel(d.category)} · version {d.version}</span></span>
                <span className="t13">{ackText(d)}<span style={{ display: "block" }}>Published {shortDate(d.published_at.slice(0, 10))}</span></span>
                <span><button type="button" className="btn" aria-expanded={open === d.id} aria-label={`Open ${d.title}`} onClick={() => { setOpen(open === d.id ? null : d.id); setRevising(false); }}>Open</button></span>
              </div>
              {open === d.id && (
                <div style={{ padding: "0 20px 16px", display: "flex", flexDirection: "column", gap: 10 }}>
                  <ErrorNote error={detail.error} />
                  {detail.loading && !detail.data ? <Loading what="the policy" /> : detail.data && <>
                    <p style={{ whiteSpace: "pre-wrap", margin: 0 }}>{detail.data.body}</p>
                    {detail.data.requires_ack && (detail.data.waiting ?? []).length > 0 && (
                      <ul aria-label={`Waiting to acknowledge ${d.title}`} style={{ listStyle: "none", margin: 0, padding: 0 }}>
                        {(detail.data.waiting ?? []).map((w) => (
                          <li key={w.employee_id} style={{ display: "flex", justifyContent: "space-between", gap: 12, padding: "6px 0", borderTop: "1px solid var(--line-2)" }}>
                            <span>{w.employee_name} <span className="t13 mono">{w.employee_code}</span></span>
                            <button type="button" className="linkbtn" style={{ color: "var(--cobalt)" }} aria-label={`Record acknowledgement by ${w.employee_name}`} onClick={() => void acknowledge(d.id, w.employee_id)}>Record acknowledgement</button>
                          </li>
                        ))}
                      </ul>
                    )}
                    {detail.data.requires_ack && (detail.data.waiting ?? []).length === 0 && <span className="t13" role="status">Everyone has acknowledged this version.</span>}
                    {!revising && <span><button type="button" className="btn" onClick={() => setRevising(true)}>Publish a new version</button></span>}
                  </>}
                  {revising && detail.data && <PublishDoc entityId={id!} revise={{ slug: detail.data.slug, title: detail.data.title, category: detail.data.category, body: detail.data.body ?? "", requires_ack: detail.data.requires_ack }}
                    onCancel={() => setRevising(false)} onDone={() => { setRevising(false); setOpen(null); docs.reload(); }} />}
                </div>
              )}
            </div>
          ))}
      </section>
    </main>
  );
}
