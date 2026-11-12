"use client";
import Link from "next/link";
import { useState } from "react";
import { api, unwrap } from "@/lib/api";
import { formatInr, shortDate } from "@/lib/format";
import { useLoad } from "@/lib/hooks";
import type { Employee } from "@/lib/payroll";
import { useSession } from "@/lib/session";
import { Checklist, NewHire } from "@/components/HrForms";
import { ErrorNote, Loading, Pill } from "@/components/ui";

export default function Onboard() {
  const { entity } = useSession();
  const id = entity?.id ?? null;
  const [adding, setAdding] = useState(false);
  const [open, setOpen] = useState<string | null>(null);
  const [note, setNote] = useState("");
  const list = useLoad(async () => unwrap(await api.GET("/entities/{entityId}/employees", { params: { path: { entityId: id! }, query: { status: "onboarding", limit: 200 } } })).data as unknown as Employee[], id);
  const rows = list.data ?? [];

  if (!entity) return <main className="page"><div className="card card-pad">Select a workspace first.</div></main>;
  return (
    <main className="page">
      <div style={{ display: "flex", flexWrap: "wrap", gap: 16, justifyContent: "space-between", alignItems: "flex-end" }}>
        <div>
          <Link href="/payroll" style={{ fontSize: 14 }}>← Payroll &amp; HR</Link>
          <h1 className="disp" style={{ margin: "8px 0 0", fontSize: 36 }}>New hires</h1>
        </div>
        <button type="button" className="btn primary" aria-expanded={adding} onClick={() => { setAdding(!adding); setNote(""); }}>+ New hire</button>
      </div>
      <span className="t13">Enter a person once with the joining date and the yearly CTC. The salary is split by your <Link href="/payroll/policy">HR policy</Link>, the checklist comes from it, and the person joins payroll when onboarding is finished. The split is a starter policy: have your CA confirm it.</span>
      {note && <div role="status" className="t13">{note}</div>}

      {adding && <section className="card card-pad" aria-label="Add a new hire"><NewHire entityId={id!} onCancel={() => setAdding(false)} onDone={(o) => {
        setAdding(false); setOpen(o.employee.id); setNote(`${o.employee.full_name} was added as ${o.employee.employee_code}. Finish the checklist below.`); list.reload();
      }} /></section>}

      <section className="card" style={{ overflow: "hidden" }}>
        <div style={{ padding: "18px 20px" }}><h2 style={{ margin: 0 }}>Onboarding now</h2></div>
        <div style={{ padding: "0 20px" }}><ErrorNote error={list.error} /></div>
        {list.loading && !list.data ? <Loading what="new hires" /> : rows.length === 0
          ? <div className="empty">Nobody is being onboarded. Add a new hire and their salary and checklist are set up for you.</div>
          : rows.map((e) => (
            <div key={e.id} style={{ borderTop: "1px solid var(--line-2)" }}>
              <div style={{ padding: "14px 20px", display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(190px, 1fr))", gap: 12, alignItems: "start" }}>
                <span style={{ fontWeight: 600 }}>{e.full_name}<span className="t13 mono" style={{ display: "block", fontWeight: 400 }}>{e.employee_code}{e.designation ? ` · ${e.designation}` : ""}</span></span>
                <span>Joins {shortDate(e.date_of_joining)}<span className="t13" style={{ display: "block" }}>{e.current_structure ? `CTC ${formatInr(e.current_structure.ctc_annual, { decimals: false })} a year` : "No salary set"}</span></span>
                <span><Pill tone="prep">Onboarding</Pill></span>
                <span><button type="button" className="btn" aria-expanded={open === e.id} aria-label={`Checklist for ${e.full_name}`} onClick={() => setOpen(open === e.id ? null : e.id)}>Checklist</button></span>
              </div>
              {open === e.id && <Checklist entityId={id!} employeeId={e.id} onChanged={() => list.reload()} />}
            </div>
          ))}
      </section>
    </main>
  );
}
