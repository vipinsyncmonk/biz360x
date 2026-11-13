"use client";
import Link from "next/link";
import { useState } from "react";
import { api, unwrap, unwrapEmpty } from "@/lib/api";
import { shortDate } from "@/lib/format";
import { useLoad } from "@/lib/hooks";
import { Balances, Holiday, LEAVE_STATUS, LeaveRequest, LeaveType, balanceText, daysText } from "@/lib/hr";
import { canOpen } from "@/lib/roles";
import { useSession } from "@/lib/session";
import { ApplyLeave, HolidayAdd } from "@/components/HrForms";
import { ErrorNote, Loading, Pill } from "@/components/ui";

type Tab = "requests" | "balances" | "holidays";

export default function Leave() {
  const { entity } = useSession();
  const id = entity?.id ?? null;
  const [tab, setTab] = useState<Tab>("requests");
  const [applying, setApplying] = useState(false);
  const [failure, setFailure] = useState<unknown>(null);
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState<string | null>(null);

  const p = { path: { entityId: id! } };
  const requests = useLoad(async () => unwrap(await api.GET("/entities/{entityId}/leave-requests", { params: { ...p, query: { limit: 100 } } })).data as unknown as LeaveRequest[], id);
  const balances = useLoad(async () => unwrap(await api.GET("/entities/{entityId}/leave-balances", { params: p })) as unknown as Balances, id);
  const types = useLoad(async () => unwrap(await api.GET("/entities/{entityId}/leave-types", { params: p })).data as unknown as LeaveType[], id);
  const people = useLoad(async () => unwrap(await api.GET("/entities/{entityId}/hr/people", { params: p })).data as unknown as Array<{ id: string; employee_code: string; full_name: string; status: string }>, id);
  const holidays = useLoad(async () => unwrap(await api.GET("/entities/{entityId}/holidays", { params: p })).data as unknown as Holiday[], id);

  const reload = () => { requests.reload(); balances.reload(); };
  async function decide(r: LeaveRequest, what: "approve" | "reject" | "cancel") {
    setBusy(r.id); setFailure(null); setNote("");
    try {
      const path = { path: { entityId: id!, id: r.id } };
      const out = (what === "approve" ? unwrap(await api.POST("/entities/{entityId}/leave-requests/{id}/approve", { params: path, body: {} }))
        : what === "reject" ? unwrap(await api.POST("/entities/{entityId}/leave-requests/{id}/reject", { params: path, body: {} }))
          : unwrap(await api.POST("/entities/{entityId}/leave-requests/{id}/cancel", { params: path, body: {} }))) as unknown as LeaveRequest;
      if (out.payroll_note) setNote(out.payroll_note);
      else if (what === "approve" && /[1-9]/.test(out.lop_days)) setNote(`Approved. The ${out.lop_days} unpaid ${out.lop_days === "1" ? "day" : "days"} will be deducted in that month's payroll.`);
      reload();
    } catch (e) { setFailure(e); } finally { setBusy(null); }
  }
  async function removeHoliday(h: Holiday) {
    setFailure(null);
    try { unwrapEmpty(await api.DELETE("/entities/{entityId}/holidays/{id}", { params: { path: { entityId: id!, id: h.id } } })); holidays.reload(); } catch (e) { setFailure(e); }
  }

  const rows = requests.data ?? [];
  const staff = (people.data ?? []).filter((e) => e.status !== "exited").map((e) => ({ id: e.id, label: `${e.employee_code} · ${e.full_name}` }));
  const waiting = rows.filter((r) => r.status === "pending").length;
  const paidTypes = (types.data ?? []).filter((t) => t.is_active);

  if (!entity) return <main className="page"><div className="card card-pad">Select a workspace first.</div></main>;
  return (
    <main className="page">
      <div style={{ display: "flex", flexWrap: "wrap", gap: 16, justifyContent: "space-between", alignItems: "flex-end" }}>
        <div>
          {canOpen(entity, "payroll") && <Link href="/payroll" style={{ fontSize: 14 }}>← Payroll &amp; HR</Link>}
          <h1 className="disp" style={{ margin: "8px 0 0", fontSize: 36 }}>Leave</h1>
        </div>
        <button type="button" className="btn primary" aria-expanded={applying} disabled={staff.length === 0 || paidTypes.length === 0} onClick={() => setApplying(!applying)}>+ Record leave</button>
      </div>
      <span className="t13">Leave is counted in working days against what each person has earned under your <Link href="/payroll/policy">HR policy</Link>. Approved unpaid days go into that month&apos;s payroll by themselves. Year-end carry-forward and encashment are not automatic yet; the quotas are a starter set to review.</span>
      <ErrorNote error={failure ?? requests.error ?? balances.error ?? types.error ?? people.error} />
      {note && <div role="status" className="t13">{note}</div>}

      {applying && <section className="card card-pad" aria-label="Record leave"><ApplyLeave entityId={id!} people={staff} types={types.data ?? []} onCancel={() => setApplying(false)} onDone={() => { setApplying(false); setTab("requests"); reload(); }} /></section>}

      <div className="seg" role="tablist" aria-label="Leave views" style={{ alignSelf: "flex-start" }}>
        <button type="button" role="tab" aria-selected={tab === "requests"} onClick={() => setTab("requests")}>Requests{waiting ? ` (${waiting} waiting)` : ""}</button>
        <button type="button" role="tab" aria-selected={tab === "balances"} onClick={() => setTab("balances")}>Balances</button>
        <button type="button" role="tab" aria-selected={tab === "holidays"} onClick={() => setTab("holidays")}>Holidays</button>
      </div>

      {tab === "requests" && (
        <section className="card" style={{ overflow: "hidden" }}>
          <div style={{ padding: "18px 20px" }}><h2 style={{ margin: 0 }}>Leave requests</h2></div>
          {requests.loading && !requests.data ? <Loading what="leave requests" /> : rows.length === 0 ? <div className="empty">No leave has been recorded yet.</div> : rows.map((r) => {
            const st = LEAVE_STATUS[r.status];
            return (
              <div key={r.id} style={{ borderTop: "1px solid var(--line-2)", padding: "14px 20px", display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(190px, 1fr))", gap: 12, alignItems: "start" }}>
                <span style={{ fontWeight: 600 }}>{r.employee_name}<span className="t13 mono" style={{ display: "block", fontWeight: 400 }}>{r.employee_code} · {r.leave_type_name}</span></span>
                <span>{r.from === r.to ? shortDate(r.from) : `${shortDate(r.from)} to ${shortDate(r.to)}`}<span className="t13" style={{ display: "block" }}>{daysText(r)}{r.reason ? ` · ${r.reason}` : ""}</span></span>
                <span><Pill tone={st.tone}>{st.label}</Pill></span>
                <span style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
                  {r.status === "pending" && <button type="button" className="btn primary" disabled={busy === r.id} aria-label={`Approve leave of ${r.employee_name} from ${r.from}`} onClick={() => void decide(r, "approve")}>Approve</button>}
                  {r.status === "pending" && <button type="button" className="btn" disabled={busy === r.id} aria-label={`Reject leave of ${r.employee_name} from ${r.from}`} onClick={() => void decide(r, "reject")}>Reject</button>}
                  {r.status === "approved" && <button type="button" className="btn" disabled={busy === r.id} aria-label={`Withdraw leave of ${r.employee_name} from ${r.from}`} onClick={() => void decide(r, "cancel")}>Withdraw</button>}
                </span>
              </div>
            );
          })}
        </section>
      )}

      {tab === "balances" && (
        <section className="card" style={{ overflow: "hidden" }}>
          <div style={{ padding: "18px 20px" }}><h2 style={{ margin: 0 }}>Leave balances</h2>
            {balances.data && <span className="t13">Leave year {shortDate(balances.data.leave_year_start)} to {shortDate(balances.data.leave_year_end)}</span>}</div>
          {balances.loading && !balances.data ? <Loading what="balances" /> : (balances.data?.data ?? []).length === 0 ? <div className="empty">Add employees and their leave balances appear here.</div> : (
            <div className="tablewrap"><table style={{ minWidth: 640 }} aria-label="Leave balances">
              <thead><tr><th scope="col">Person</th>{(balances.data!.data[0]?.balances ?? []).map((b) => <th key={b.leave_type} scope="col">{b.name}</th>)}</tr></thead>
              <tbody>{balances.data!.data.map((e) => (
                <tr key={e.employee_id}><td><span style={{ fontWeight: 500 }}>{e.employee_name}</span><span className="t13 mono" style={{ display: "block" }}>{e.employee_code}</span></td>
                  {e.balances.map((b) => <td key={b.leave_type} className="t13">{balanceText(b)}</td>)}</tr>
              ))}</tbody>
            </table></div>
          )}
        </section>
      )}

      {tab === "holidays" && (
        <section className="card card-pad" style={{ display: "flex", flexDirection: "column", gap: 12 }} aria-label="Holidays">
          <h2 style={{ margin: 0 }}>Company holidays</h2>
          <HolidayAdd entityId={id!} onDone={() => holidays.reload()} />
          <ErrorNote error={holidays.error} />
          {(holidays.data ?? []).length === 0 ? <span className="t13">No holidays yet. A holiday inside a leave is not counted as a day of leave.</span> : (
            <ul aria-label="Holiday list" style={{ listStyle: "none", margin: 0, padding: 0 }}>
              {(holidays.data ?? []).map((h) => (
                <li key={h.id} style={{ display: "flex", justifyContent: "space-between", gap: 12, padding: "8px 0", borderTop: "1px solid var(--line-2)" }}>
                  <span>{h.name}<span className="t13" style={{ display: "block" }}>{shortDate(h.date)}</span></span>
                  <button type="button" className="linkbtn" aria-label={`Remove ${h.name}`} onClick={() => void removeHoliday(h)}>Remove</button>
                </li>
              ))}
            </ul>
          )}
        </section>
      )}
    </main>
  );
}
