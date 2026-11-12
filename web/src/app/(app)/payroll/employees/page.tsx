"use client";
import Link from "next/link";
import { useState } from "react";
import { api, unwrap } from "@/lib/api";
import { formatInr, shortDate } from "@/lib/format";
import { useLoad } from "@/lib/hooks";
import {
  COMP_KINDS, CompDraft, EMP_STATUS, EMP_TYPES, Employee, EmployeeForm, annualOf, blankEmployee, defaultComponents, draftsFrom, employeeBody, employeeProblem, monthlyEarnings, structureBody, structureProblem,
} from "@/lib/payroll";
import { isoDate } from "@/lib/format";
import { useSession } from "@/lib/session";
import { ErrorNote, Loading, Pill } from "@/components/ui";
import { PayslipList } from "@/components/HrForms";

export default function Employees() {
  const { entity } = useSession();
  const id = entity?.id ?? null;
  const [q, setQ] = useState("");
  const [adding, setAdding] = useState(false);
  const [open, setOpen] = useState<string | null>(null);
  const list = useLoad(async () => unwrap(await api.GET("/entities/{entityId}/employees", { params: { path: { entityId: id! }, query: { limit: 200, ...(q.trim() ? { q: q.trim() } : {}) } } })).data as unknown as Employee[], id ? `${id}|${q.trim()}` : null);
  const rows = list.data ?? [];

  if (!entity) return <main className="page"><div className="card card-pad">Select a workspace first.</div></main>;
  return (
    <main className="page">
      <div style={{ display: "flex", flexWrap: "wrap", gap: 16, justifyContent: "space-between", alignItems: "flex-end" }}>
        <div>
          <Link href="/payroll" style={{ fontSize: 14 }}>← Payroll &amp; HR</Link>
          <h1 className="disp" style={{ margin: "8px 0 0", fontSize: 36 }}>Employees</h1>
        </div>
        <button type="button" className="btn primary" onClick={() => setAdding(!adding)}>+ Add employee</button>
      </div>

      {adding && <AddEmployee entityId={id!} onDone={() => { setAdding(false); list.reload(); }} onCancel={() => setAdding(false)} />}

      <section className="card" style={{ overflow: "hidden" }}>
        <div style={{ padding: "14px 20px", display: "flex", justifyContent: "space-between", alignItems: "center", gap: 12, flexWrap: "wrap", borderBottom: "1px solid var(--line-2)" }}>
          <h2 style={{ margin: 0 }}>{rows.length} {rows.length === 1 ? "person" : "people"}</h2>
          <div className="field"><label className="sr" htmlFor="emp-q">Search</label><input id="emp-q" className="input" style={{ height: 40, fontSize: 14 }} placeholder="Name or code" value={q} onChange={(e) => setQ(e.target.value)} /></div>
        </div>
        <div style={{ padding: "0 20px" }}><ErrorNote error={list.error} /></div>
        {list.loading ? <Loading what="employees" /> : (
          <div className="tablewrap">
            <table style={{ minWidth: 720 }}>
              <thead><tr><th scope="col">Employee</th><th scope="col">Status</th><th scope="col">Joined</th><th scope="col" style={{ textAlign: "right" }}>Monthly pay</th><th scope="col"><span className="sr">Salary</span></th></tr></thead>
              <tbody>
                {rows.map((e) => {
                  const st = EMP_STATUS[e.status] ?? { label: e.status, tone: "prep" as const };
                  return (
                    <EmployeeRow key={e.id} e={e} st={st} entityId={id!} open={open === e.id} onToggle={() => setOpen(open === e.id ? null : e.id)} onSaved={() => { setOpen(null); list.reload(); }} />
                  );
                })}
              </tbody>
            </table>
            {rows.length === 0 && <div className="empty">{q.trim() ? "No one matches." : "No employees yet. Add the first one."}</div>}
          </div>
        )}
      </section>
    </main>
  );
}

function EmployeeRow({ e, st, entityId, open, onToggle, onSaved }: { e: Employee; st: { label: string; tone: "due" | "ok" | "wait" | "prep" }; entityId: string; open: boolean; onToggle: () => void; onSaved: () => void }) {
  const s = e.current_structure;
  const [slips, setSlips] = useState(false);
  return (
    <>
      <tr>
        <td><span style={{ fontWeight: 500 }}>{e.full_name}</span><span className="t13" style={{ display: "block" }}>{e.employee_code}{e.designation ? ` · ${e.designation}` : ""}{e.bank_account ? ` · bank ••${e.bank_account.account_last4}` : " · no bank account"}</span></td>
        <td><Pill tone={st.tone}>{st.label}</Pill></td>
        <td className="t13">{shortDate(e.date_of_joining)}</td>
        <td className="mono" style={{ textAlign: "right" }}>{s ? formatInr(monthlyEarnings(s.components), { decimals: false }) : <span className="t13">No salary</span>}</td>
        <td style={{ textAlign: "right" }}><button type="button" className="linkbtn" style={{ color: "var(--cobalt)" }} aria-expanded={open} onClick={onToggle}>{s ? "Revise salary" : "Set salary"}</button><button type="button" className="linkbtn" style={{ color: "var(--cobalt)", marginLeft: 12 }} aria-expanded={slips} aria-label={`Payslips of ${e.full_name}`} onClick={() => setSlips(!slips)}>Payslips</button></td>
      </tr>
      {open && <tr><td colSpan={5} style={{ background: "var(--surface-2)" }}><SalaryForm e={e} entityId={entityId} onSaved={onSaved} /></td></tr>}
      {slips && <tr><td colSpan={5} style={{ background: "var(--surface-2)" }}><PayslipList entityId={entityId} employeeId={e.id} name={e.full_name} /></td></tr>}
    </>
  );
}

function SalaryForm({ e, entityId, onSaved }: { e: Employee; entityId: string; onSaved: () => void }) {
  const [from, setFrom] = useState(isoDate(new Date()));
  const [comps, setComps] = useState<CompDraft[]>(e.current_structure ? draftsFrom(e.current_structure.components) : defaultComponents());
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const problem = structureProblem(from, comps);
  const set = (i: number, patch: Partial<CompDraft>) => setComps(comps.map((c, j) => (j === i ? { ...c, ...patch } : c)));

  async function save() {
    setBusy(true); setError(null);
    try { unwrap(await api.POST("/entities/{entityId}/employees/{id}/salary-structures", { params: { path: { entityId, id: e.id } }, body: structureBody(from, comps) as never })); onSaved(); }
    catch (x) { setError(x); setBusy(false); }
  }
  return (
    <div style={{ padding: "8px 0", display: "flex", flexDirection: "column", gap: 10 }}>
      <div className="field" style={{ maxWidth: 220 }}><label htmlFor={`from-${e.id}`}>New salary starts on</label><input id={`from-${e.id}`} type="date" className="input" value={from} onChange={(x) => setFrom(x.target.value)} /></div>
      <table style={{ minWidth: 560 }}>
        <thead><tr><th scope="col">Component</th><th scope="col">Monthly ₹</th><th scope="col">Kind</th><th scope="col">Counts as wages</th><th scope="col">Taxable</th><th scope="col"><span className="sr">Remove</span></th></tr></thead>
        <tbody>
          {comps.map((c, i) => (
            <tr key={i}>
              <td><input aria-label={`Component ${i + 1} code`} className="input mono" style={{ height: 36, fontSize: 14, width: 130 }} value={c.code} onChange={(x) => set(i, { code: x.target.value })} /></td>
              <td><input aria-label={`${c.code || "Component"} monthly amount`} className="input mono" style={{ height: 36, fontSize: 14, width: 120 }} inputMode="decimal" value={c.amount} onChange={(x) => set(i, { amount: x.target.value })} /></td>
              <td><select aria-label={`${c.code || "Component"} kind`} className="input" style={{ height: 36, fontSize: 13 }} value={c.kind} onChange={(x) => set(i, { kind: x.target.value })}>{COMP_KINDS.map(([k, l]) => <option key={k} value={k}>{l}</option>)}</select></td>
              <td><input type="checkbox" aria-label={`${c.code || "Component"} counts as wages`} checked={c.in_wages} onChange={(x) => set(i, { in_wages: x.target.checked })} /></td>
              <td><input type="checkbox" aria-label={`${c.code || "Component"} is taxable`} checked={c.taxable} onChange={(x) => set(i, { taxable: x.target.checked })} /></td>
              <td>{comps.length > 1 && <button type="button" className="btn" style={{ height: 32 }} aria-label={`Remove ${c.code}`} onClick={() => setComps(comps.filter((_, j) => j !== i))}>✕</button>}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <button type="button" className="linkbtn" style={{ color: "var(--cobalt)", alignSelf: "flex-start" }} onClick={() => setComps([...comps, { code: "", amount: "", kind: "earning", in_wages: false, taxable: true }])}>+ Add component</button>
      <span className="t13">Yearly total {formatInr(annualOf(comps))}. PF, ESI, professional tax and TDS are worked out from this when the month is computed.</span>
      {problem && <span className="t13" style={{ color: "var(--amber)" }}>{problem}</span>}
      <ErrorNote error={error} />
      <span><button type="button" className="btn primary" disabled={busy || !!problem} onClick={() => void save()}>{busy ? "Saving…" : "Save salary"}</button></span>
    </div>
  );
}

function AddEmployee({ entityId, onDone, onCancel }: { entityId: string; onDone: () => void; onCancel: () => void }) {
  const [f, setF] = useState<EmployeeForm>(blankEmployee());
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [touched, setTouched] = useState(false);
  const problem = employeeProblem(f);
  const set = (patch: Partial<EmployeeForm>) => setF({ ...f, ...patch });
  const field = (key: keyof EmployeeForm, label: string, extra: { type?: string; mono?: boolean; placeholder?: string } = {}) => (
    <div className="field"><label htmlFor={`e-${key}`}>{label}</label>
      <input id={`e-${key}`} type={extra.type ?? "text"} className={`input${extra.mono ? " mono" : ""}`} placeholder={extra.placeholder} value={f[key]} onChange={(e) => set({ [key]: e.target.value })} /></div>
  );

  async function save() {
    setTouched(true);
    if (problem) return;
    setBusy(true); setError(null);
    try { unwrap(await api.POST("/entities/{entityId}/employees", { params: { path: { entityId } }, body: employeeBody(f) as never })); onDone(); }
    catch (e) { setError(e); setBusy(false); }
  }
  return (
    <form className="card card-pad" style={{ display: "flex", flexDirection: "column", gap: 14 }} onSubmit={(e) => { e.preventDefault(); void save(); }}>
      <h2 style={{ margin: 0 }}>Add an employee</h2>
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(200px, 1fr))", gap: 14 }}>
        {field("code", "Employee code", { mono: true, placeholder: "EMP-014" })}
        {field("name", "Full name")}
        {field("joined", "Date of joining", { type: "date" })}
        {field("state", "Work state code", { mono: true, placeholder: "29" })}
        <div className="field"><label htmlFor="e-regime">Tax regime</label><select id="e-regime" className="input" value={f.regime} onChange={(e) => set({ regime: e.target.value })}><option value="new">New</option><option value="old">Old</option></select></div>
        <div className="field"><label htmlFor="e-type">Employment</label><select id="e-type" className="input" value={f.type} onChange={(e) => set({ type: e.target.value })}>{EMP_TYPES.map(([k, l]) => <option key={k} value={k}>{l}</option>)}</select></div>
        {field("designation", "Designation")}
        {field("department", "Department")}
        {field("email", "Email", { type: "email" })}
        {field("pan", "PAN", { mono: true, placeholder: "ABCDE1234F" })}
        {field("uan", "UAN", { mono: true })}
        {field("account", "Bank account no.", { mono: true })}
        {field("ifsc", "IFSC", { mono: true, placeholder: "HDFC0001234" })}
      </div>
      <span className="t13">Bank account numbers are stored encrypted and only the last four digits are shown afterwards.</span>
      {touched && problem && <span className="t13" role="alert" style={{ color: "var(--amber)" }}>{problem}</span>}
      <ErrorNote error={error} />
      <span style={{ display: "flex", gap: 10 }}>
        <button type="submit" className="btn primary" disabled={busy}>{busy ? "Saving…" : "Add employee"}</button>
        <button type="button" className="btn" onClick={onCancel}>Cancel</button>
      </span>
    </form>
  );
}
