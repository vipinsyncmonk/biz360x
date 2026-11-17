"use client";
import { useState } from "react";
import { api, unwrap } from "@/lib/api";
import { formatInr } from "@/lib/format";
import { useLoad } from "@/lib/hooks";
import {
  AUTO_HINT, CtcBreakup, DOC_CATEGORIES, HireForm, LeaveForm, LeaveType, Onboarding, PersonSlip, blankHire, blankLeave, checklistLine, componentLabel, ctcIsValid, docProblem, hireBody, hireProblem,
  leaveBody, leaveProblem, openPayslipPdf,
} from "@/lib/hr";
import { moneyText } from "@/lib/assets";
import { runName } from "@/lib/payroll";
import { ErrorNote, Loading } from "./ui";
import { Field, Shell, grid } from "./TradeForms";

const h40 = { height: 40 } as const;

/** What a CTC becomes under the company's policy. Shown while the CTC is being typed; nothing is saved. */
export function CtcPreview({ entityId, ctc }: { entityId: string; ctc: string }) {
  const ok = ctcIsValid(ctc);
  const b = useLoad(async () => unwrap(await api.GET("/entities/{entityId}/payroll/ctc-breakup", { params: { path: { entityId }, query: { ctc_annual: moneyText(ctc) } } })) as unknown as CtcBreakup,
    ok ? `${entityId}|${moneyText(ctc)}` : null);
  if (!ok) return <span className="t13">Enter the yearly CTC and the monthly salary is worked out from your policy.</span>;
  if (b.error) return <ErrorNote error={b.error} />;
  if (!b.data) return <Loading what="the salary" />;
  const d = b.data;
  return (
    <div className="tablewrap"><table style={{ minWidth: 380 }} aria-label="Salary from the CTC">
      <thead><tr><th scope="col">Component</th><th scope="col" style={{ textAlign: "right" }}>Monthly</th><th scope="col" style={{ textAlign: "right" }}>Yearly</th></tr></thead>
      <tbody>{d.components.map((c) => (
        <tr key={c.code}><td>{componentLabel(c.code)}{c.kind === "employer_contribution" && <span className="t13"> · paid by the company</span>}</td>
          <td className="mono" style={{ textAlign: "right" }}>{formatInr(c.monthly_amount)}</td><td className="mono" style={{ textAlign: "right" }}>{formatInr(c.annual_amount)}</td></tr>
      ))}</tbody>
      <tfoot>
        <tr><th scope="row">Monthly gross pay</th><td className="mono" style={{ textAlign: "right", fontWeight: 600 }}>{formatInr(d.monthly_gross)}</td><td /></tr>
        <tr><th scope="row">Take-home before income tax</th><td className="mono" style={{ textAlign: "right", fontWeight: 600 }}>{formatInr(d.take_home_before_tax)}</td><td /></tr>
      </tfoot>
    </table></div>
  );
}

/** Enter a new hire once: who, when they join, and the CTC. */
export function NewHire({ entityId, onDone, onCancel }: { entityId: string; onDone: (o: Onboarding) => void; onCancel: () => void }) {
  const [f, setF] = useState<HireForm>(blankHire);
  const set = (p: Partial<HireForm>) => setF((x) => ({ ...x, ...p }));
  return (
    <Shell label="Add new hire" problem={hireProblem(f)} started={!!f.name || !!f.ctc} onCancel={onCancel} onSubmit={async () => {
      onDone(unwrap(await api.POST("/entities/{entityId}/employees/onboard", { params: { path: { entityId } }, body: hireBody(f) as never })) as unknown as Onboarding);
    }}>
      <div style={grid}>
        <Field id="nh-name" label="Full name"><input id="nh-name" className="input" style={h40} autoComplete="off" value={f.name} onChange={(e) => set({ name: e.target.value })} /></Field>
        <Field id="nh-join" label="Joining date"><input id="nh-join" type="date" className="input" style={h40} value={f.joining} onChange={(e) => set({ joining: e.target.value })} /></Field>
        <Field id="nh-ctc" label="Yearly CTC (₹)"><input id="nh-ctc" className="input mono" style={h40} inputMode="decimal" autoComplete="off" value={f.ctc} onChange={(e) => set({ ctc: e.target.value })} /></Field>
        <Field id="nh-state" label="Work state code"><input id="nh-state" className="input mono" style={h40} inputMode="numeric" maxLength={2} placeholder="29" autoComplete="off" value={f.state} onChange={(e) => set({ state: e.target.value })} /></Field>
        <Field id="nh-desig" label="Designation (optional)"><input id="nh-desig" className="input" style={h40} autoComplete="off" value={f.designation} onChange={(e) => set({ designation: e.target.value })} /></Field>
        <Field id="nh-dept" label="Department (optional)"><input id="nh-dept" className="input" style={h40} autoComplete="off" value={f.department} onChange={(e) => set({ department: e.target.value })} /></Field>
        <Field id="nh-email" label="Email (optional)"><input id="nh-email" type="email" className="input" style={h40} autoComplete="off" value={f.email} onChange={(e) => set({ email: e.target.value })} /></Field>
        <Field id="nh-code" label="Employee code (optional)"><input id="nh-code" className="input mono" style={h40} autoComplete="off" placeholder="Next EMP- number" value={f.code} onChange={(e) => set({ code: e.target.value })} /></Field>
      </div>
      <CtcPreview entityId={entityId} ctc={f.ctc} />
      <span className="t13">PAN and the bank account can be added now or later from the checklist. The person stays outside payroll until onboarding is finished, and is then paid from the joining date.</span>
      <div style={grid}>
        <Field id="nh-pan" label="PAN (optional)"><input id="nh-pan" className="input mono" style={h40} autoComplete="off" maxLength={10} value={f.pan} onChange={(e) => set({ pan: e.target.value.toUpperCase() })} /></Field>
        <Field id="nh-acct" label="Bank account no. (optional)"><input id="nh-acct" className="input mono" style={h40} inputMode="numeric" autoComplete="off" value={f.accountNo} onChange={(e) => set({ accountNo: e.target.value })} /></Field>
        <Field id="nh-ifsc" label="IFSC"><input id="nh-ifsc" className="input mono" style={h40} autoComplete="off" maxLength={11} value={f.ifsc} onChange={(e) => set({ ifsc: e.target.value.toUpperCase() })} /></Field>
      </div>
    </Shell>
  );
}

/** A new hire's checklist: tick what a person does, add what the record needs, then activate. */
export function Checklist({ entityId, employeeId, onChanged }: { entityId: string; employeeId: string; onChanged: () => void }) {
  const o = useLoad(async () => unwrap(await api.GET("/entities/{entityId}/employees/{id}/onboarding", { params: { path: { entityId, id: employeeId } } })) as unknown as Onboarding, `${entityId}|${employeeId}`);
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  const [pan, setPan] = useState("");
  const [acct, setAcct] = useState("");
  const [ifsc, setIfsc] = useState("");
  async function run(fn: () => Promise<unknown>, after?: () => void) {
    setBusy(true); setError(null);
    try { await fn(); o.reload(); after?.(); } catch (e) { setError(e); } finally { setBusy(false); }
  }
  const p = { path: { entityId, id: employeeId } };
  const tick = (key: string, done: boolean) => run(async () => unwrap(await api.PUT("/entities/{entityId}/employees/{id}/onboarding/{key}", { params: { path: { entityId, id: employeeId, key } }, body: { done } })));
  const patch = (body: Record<string, unknown>) => run(async () => unwrap(await api.PATCH("/entities/{entityId}/employees/{id}", { params: { ...p, header: { "If-Match": "*" } }, body: body as never })), onChanged);
  if (o.loading && !o.data) return <Loading what="the checklist" />;
  const d = o.data;
  if (!d) return <ErrorNote error={o.error} />;
  const name = d.employee.full_name;
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 10, padding: "0 20px 16px" }}>
      <span className="t13" role="status">{checklistLine(d)}</span>
      {d.employee.current_structure && <span className="t13">CTC {formatInr(d.employee.current_structure.ctc_annual, { decimals: false })} a year</span>}
      <ul aria-label={`Onboarding checklist for ${name}`} style={{ listStyle: "none", margin: 0, padding: 0, display: "flex", flexDirection: "column", gap: 8 }}>
        {d.checklist.map((t) => (
          <li key={t.key} style={{ display: "flex", gap: 10, alignItems: "flex-start" }}>
            <input type="checkbox" id={`ck-${employeeId}-${t.key}`} checked={t.done} disabled={busy || !!t.auto} onChange={(e) => void tick(t.key, e.target.checked)} style={{ marginTop: 3 }} />
            <label htmlFor={`ck-${employeeId}-${t.key}`}>{t.title}{!t.required && <span className="t13"> · optional</span>}
              {t.auto && !t.done && <span className="t13" style={{ display: "block" }}>{AUTO_HINT[t.auto]}</span>}</label>
          </li>
        ))}
      </ul>
      {!d.employee.pan && (
        <form style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "flex-end" }} onSubmit={(e) => { e.preventDefault(); void patch({ pan: pan.trim().toUpperCase() }); }}>
          <Field id={`pan-${employeeId}`} label={`PAN of ${name}`}><input id={`pan-${employeeId}`} className="input mono" style={h40} maxLength={10} autoComplete="off" value={pan} onChange={(e) => setPan(e.target.value.toUpperCase())} /></Field>
          <button type="submit" className="btn" disabled={busy || !/^[A-Z]{5}\d{4}[A-Z]$/.test(pan.trim())}>Save PAN</button>
        </form>
      )}
      {!d.employee.bank_account && (
        <form style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "flex-end" }} onSubmit={(e) => { e.preventDefault(); void patch({ bank_account: { account_no: acct.trim(), ifsc: ifsc.trim().toUpperCase() } }); }}>
          <Field id={`acct-${employeeId}`} label={`Bank account of ${name}`}><input id={`acct-${employeeId}`} className="input mono" style={h40} inputMode="numeric" autoComplete="off" value={acct} onChange={(e) => setAcct(e.target.value)} /></Field>
          <Field id={`ifsc-${employeeId}`} label="IFSC"><input id={`ifsc-${employeeId}`} className="input mono" style={h40} maxLength={11} autoComplete="off" value={ifsc} onChange={(e) => setIfsc(e.target.value.toUpperCase())} /></Field>
          <button type="submit" className="btn" disabled={busy || !/^\d{6,18}$/.test(acct.trim()) || !/^[A-Z]{4}0[A-Z0-9]{6}$/.test(ifsc.trim())}>Save bank account</button>
        </form>
      )}
      <ErrorNote error={error ?? o.error} />
      {d.employee.status === "onboarding" && (
        <span><button type="button" className="btn primary" disabled={busy || !d.ready} aria-label={`Finish onboarding ${name}`}
          onClick={() => void run(async () => unwrap(await api.POST("/entities/{entityId}/employees/{id}/activate", { params: p })), onChanged)}>Finish onboarding</button></span>
      )}
    </div>
  );
}

/** Record a leave request for a person. The server counts the working days and says what part is unpaid. */
export function ApplyLeave({ entityId, people, types, onDone, onCancel }: { entityId: string; people: Array<{ id: string; label: string }>; types: LeaveType[]; onDone: () => void; onCancel: () => void }) {
  const active = types.filter((t) => t.is_active);
  const [f, setF] = useState<LeaveForm>(() => blankLeave(people[0]?.id ?? "", active[0]?.code ?? ""));
  const set = (p: Partial<LeaveForm>) => setF((x) => ({ ...x, ...p }));
  return (
    <Shell label="Record leave" problem={leaveProblem(f)} started={!!f.from || !!f.reason} onCancel={onCancel} onSubmit={async () => {
      unwrap(await api.POST("/entities/{entityId}/leave-requests", { params: { path: { entityId } }, body: leaveBody(f) as never })); onDone();
    }}>
      <div style={grid}>
        <Field id="lv-who" label="Person"><select id="lv-who" className="input" style={h40} value={f.employeeId} onChange={(e) => set({ employeeId: e.target.value })}>{people.map((p) => <option key={p.id} value={p.id}>{p.label}</option>)}</select></Field>
        <Field id="lv-type" label="Kind of leave"><select id="lv-type" className="input" style={h40} value={f.type} onChange={(e) => set({ type: e.target.value })}>{active.map((t) => <option key={t.code} value={t.code}>{t.name}{t.paid ? "" : " (unpaid)"}</option>)}</select></Field>
        <Field id="lv-from" label={f.halfDay ? "Day" : "First day"}><input id="lv-from" type="date" className="input" style={h40} value={f.from} onChange={(e) => set({ from: e.target.value })} /></Field>
        {!f.halfDay && <Field id="lv-to" label="Last day"><input id="lv-to" type="date" className="input" style={h40} min={f.from} value={f.to} onChange={(e) => set({ to: e.target.value })} /></Field>}
        <Field id="lv-reason" label="Reason (optional)"><input id="lv-reason" className="input" style={h40} autoComplete="off" maxLength={300} value={f.reason} onChange={(e) => set({ reason: e.target.value })} /></Field>
      </div>
      <label style={{ display: "flex", gap: 8, alignItems: "center" }}><input type="checkbox" checked={f.halfDay} onChange={(e) => set({ halfDay: e.target.checked })} /><span>Half a day</span></label>
      <span className="t13">Weekly offs and company holidays inside the range are not counted. Leave beyond the balance is unpaid if your policy allows it, and unpaid days reach that month&apos;s payroll once the request is approved.</span>
    </Shell>
  );
}

/** Publish a handbook document, or the next version of one. */
export function PublishDoc({ entityId, revise, onDone, onCancel }: { entityId: string; revise?: { slug: string; title: string; category: string; body: string; requires_ack: boolean }; onDone: () => void; onCancel: () => void }) {
  const [title, setTitle] = useState(revise?.title ?? "");
  const [category, setCategory] = useState(revise?.category ?? "general");
  const [body, setBody] = useState(revise?.body ?? "");
  const [ack, setAck] = useState(revise?.requires_ack ?? true);
  const sfx = revise ? `-${revise.slug}` : "";
  return (
    <Shell compact={!!revise} label={revise ? "Publish new version" : "Publish policy"} problem={docProblem(title, body)} started={!!title || !!body} onCancel={onCancel} onSubmit={async () => {
      unwrap(await api.POST("/entities/{entityId}/hr/policy-documents", { params: { path: { entityId } }, body: { slug: revise?.slug ?? null, title: title.trim(), category, body: body.trim(), requires_ack: ack } as never })); onDone();
    }}>
      <div style={grid}>
        <Field id={`pd-title${sfx}`} label="Title"><input id={`pd-title${sfx}`} className="input" style={h40} autoComplete="off" maxLength={120} value={title} onChange={(e) => setTitle(e.target.value)} /></Field>
        <Field id={`pd-cat${sfx}`} label="About"><select id={`pd-cat${sfx}`} className="input" style={h40} value={category} onChange={(e) => setCategory(e.target.value)}>{DOC_CATEGORIES.map(([v, l]) => <option key={v} value={v}>{l}</option>)}</select></Field>
      </div>
      <Field id={`pd-body${sfx}`} label="The policy"><textarea id={`pd-body${sfx}`} className="input" style={{ minHeight: 160, padding: 12, lineHeight: 1.5 }} value={body} onChange={(e) => setBody(e.target.value)} /></Field>
      <label style={{ display: "flex", gap: 8, alignItems: "center" }}><input type="checkbox" checked={ack} onChange={(e) => setAck(e.target.checked)} /><span>Everyone must acknowledge it</span></label>
      <span className="t13">{revise ? "The earlier version is kept and people acknowledge this one afresh." : "A published policy is never edited: publishing again adds a new version."}</span>
    </Shell>
  );
}

/** A person's payslips from approved runs, each opening as a PDF. */
export function PayslipList({ entityId, employeeId, name }: { entityId: string; employeeId: string; name: string }) {
  const slips = useLoad(async () => unwrap(await api.GET("/entities/{entityId}/employees/{id}/payslips", { params: { path: { entityId, id: employeeId } } })).data as unknown as PersonSlip[], `${entityId}|${employeeId}`);
  const [error, setError] = useState<unknown>(null);
  if (slips.loading && !slips.data) return <Loading what="payslips" />;
  const rows = slips.data ?? [];
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 8, padding: "12px 0" }}>
      <ErrorNote error={slips.error ?? error} />
      {rows.length === 0 ? <span className="t13">No payslips yet. They appear once a payroll run with {name} is approved.</span> : (
        <div className="tablewrap"><table style={{ minWidth: 460 }} aria-label={`Payslips of ${name}`}>
          <thead><tr><th scope="col">Month</th><th scope="col" style={{ textAlign: "right" }}>Gross</th><th scope="col" style={{ textAlign: "right" }}>Net pay</th><th scope="col"><span className="sr">Payslip</span></th></tr></thead>
          <tbody>{rows.map((s) => (
            <tr key={s.id}><td>{runName(s.period_month)}{s.lop_days > 0 && <span className="t13"> · {s.lop_days} unpaid</span>}</td><td className="mono" style={{ textAlign: "right" }}>{formatInr(s.gross)}</td>
              <td className="mono" style={{ textAlign: "right" }}>{formatInr(s.net_pay)}</td>
              <td style={{ textAlign: "right" }}><button type="button" className="linkbtn" style={{ color: "var(--cobalt)" }} aria-label={`Payslip for ${runName(s.period_month)}`}
                onClick={() => { setError(null); openPayslipPdf(entityId, s.id).catch(setError); }}>View PDF</button></td></tr>
          ))}</tbody>
        </table></div>
      )}
    </div>
  );
}

export function HolidayAdd({ entityId, onDone }: { entityId: string; onDone: () => void }) {
  const [date, setDate] = useState("");
  const [name, setName] = useState("");
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  async function add() {
    setBusy(true); setError(null);
    try { unwrap(await api.POST("/entities/{entityId}/holidays", { params: { path: { entityId } }, body: { date, name: name.trim() } as never })); setDate(""); setName(""); onDone(); }
    catch (e) { setError(e); } finally { setBusy(false); }
  }
  return (
    <form style={{ display: "flex", flexDirection: "column", gap: 10 }} onSubmit={(e) => { e.preventDefault(); if (date && name.trim() && !busy) void add(); }}>
      <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "flex-end" }}>
        <Field id="hol-date" label="Holiday on"><input id="hol-date" type="date" className="input" style={h40} value={date} onChange={(e) => setDate(e.target.value)} /></Field>
        <Field id="hol-name" label="Occasion"><input id="hol-name" className="input" style={h40} autoComplete="off" maxLength={80} value={name} onChange={(e) => setName(e.target.value)} /></Field>
        <button type="submit" className="btn" disabled={busy || !date || !name.trim()}>Add holiday</button>
      </div>
      <ErrorNote error={error} />
    </form>
  );
}

