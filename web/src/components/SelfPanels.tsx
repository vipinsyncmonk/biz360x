"use client";
import { useState } from "react";
import { ApiProblem, api, unwrap } from "@/lib/api";
import { formatInr, isoDate, shortDate } from "@/lib/format";
import { LEAVE_STATUS, categoryLabel, daysText, weekOffText } from "@/lib/hr";
import { useLoad } from "@/lib/hooks";
import { runName } from "@/lib/payroll";
import {
  SELF_SERVICE, STATUS_WORDS, balanceLine, blankMyLeave, checklistLine, docState, inviteLine, leaveRefusal, myLeaveBody, myLeaveProblem, myToDo, openMyPayslipPdf, selfServiceAction,
  upcomingHolidays, type MyDoc, type MyEmployments, type MyLeave, type MyLeaveForm, type MyRecord, type MySlip, type SelfService,
} from "@/lib/self";
import { ErrorNote, Loading, Pill } from "./ui";

const h40 = { height: 40 };
const row = { borderTop: "1px solid var(--line-2)", padding: "14px 20px" } as const;

/** Invitations to an employee page waiting for the signed-in person. Renders nothing when there are none. */
export function EmploymentInvitations({ onAccepted }: { onAccepted: (employeeId: string) => void }) {
  const mine = useLoad(async () => unwrap(await api.GET("/me/employments")) as unknown as MyEmployments, "me");
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<unknown>(null);
  const list = mine.data?.invitations ?? [];
  if (list.length === 0) return null;

  async function accept(id: string) {
    setBusy(id); setError(null);
    try { unwrap(await api.POST("/me/employments/{id}/accept", { params: { path: { id } } })); mine.reload(); onAccepted(id); }
    catch (e) { setError(e); } finally { setBusy(null); }
  }

  return (
    <section className="card" style={{ overflow: "hidden" }} aria-label="Your employee page">
      <div className="cardhead"><h2>Your employee page</h2><Pill tone="wait">{list.length} waiting</Pill></div>
      <div style={{ padding: "0 20px" }}><ErrorNote error={error} /></div>
      {list.map((i) => (
        <div key={i.employee_id} style={{ ...row, display: "flex", flexWrap: "wrap", gap: 12, alignItems: "center", justifyContent: "space-between" }}>
          <span><span style={{ fontWeight: 600 }}>{i.legal_name}</span>
            <span className="t13" style={{ display: "block" }}>{inviteLine(i)} Accept to see your payslips, apply for leave and read the company&apos;s policies. You will not see anything else of the company.</span></span>
          <button type="button" className="btn primary" disabled={busy === i.employee_id} aria-label={`Open my employee page at ${i.legal_name}`} onClick={() => void accept(i.employee_id)}>Accept</button>
        </div>
      ))}
    </section>
  );
}

/** Who I am here, and what is asked of me. */
export function MyRecordCard({ rec }: { rec: MyRecord }) {
  const e = rec.employee;
  const todo = myToDo(rec);
  const c = rec.onboarding;
  return (
    <section className="card" style={{ overflow: "hidden" }} aria-label="My record">
      <div className="cardhead"><h2>{e.full_name}</h2><Pill tone={e.status === "active" ? "ok" : e.status === "exited" ? "due" : "prep"}>{STATUS_WORDS[e.status] ?? e.status}</Pill></div>
      <div style={{ padding: "0 20px 16px", display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(180px, 1fr))", gap: 12 }}>
        <span><span className="t13" style={{ display: "block" }}>Employee code</span><span className="mono">{e.employee_code}</span></span>
        <span><span className="t13" style={{ display: "block" }}>Role</span>{[e.designation, e.department].filter(Boolean).join(" · ") || "Not recorded"}</span>
        <span><span className="t13" style={{ display: "block" }}>Joined</span>{shortDate(e.date_of_joining)}</span>
        <span><span className="t13" style={{ display: "block" }}>PAN</span><span className="mono">{e.pan_last4 ? `ends ${e.pan_last4}` : "Not on file"}</span></span>
        <span><span className="t13" style={{ display: "block" }}>Salary account</span>{e.bank_on_file ? "On file" : "Not on file"}</span>
      </div>
      {todo.length > 0 && (
        <div style={row} role="status" aria-label="For you to do">
          <span style={{ fontWeight: 600 }}>For you to do</span>
          <ul style={{ margin: "6px 0 0", paddingLeft: 18 }}>{todo.map((t) => <li key={t}>{t}</li>)}</ul>
        </div>
      )}
      {c.total > 0 && (
        <div style={row} aria-label="Joining checklist">
          <span style={{ fontWeight: 600 }}>Joining checklist</span><span className="t13"> · {checklistLine(c)}</span>
          <ul style={{ listStyle: "none", margin: "8px 0 0", padding: 0, display: "flex", flexDirection: "column", gap: 4 }}>
            {c.checklist.map((t) => (
              <li key={t.key} style={{ display: "flex", gap: 8, alignItems: "baseline" }}>
                <span aria-hidden="true" className="mono" style={{ color: t.done ? "var(--green)" : "var(--ink-3)" }}>{t.done ? "✓" : "○"}</span>
                <span>{t.title}<span className="sr">{t.done ? ": done" : ": not done yet"}</span>{!t.required && <span className="t13"> · optional</span>}</span>
              </li>
            ))}
          </ul>
          <span className="t13" style={{ display: "block", marginTop: 6 }}>HR ticks these. Your PAN and bank account are entered by HR; tell them if something here is wrong.</span>
        </div>
      )}
    </section>
  );
}

/** My leave: what I have left, applying, and my requests. */
export function MyLeavePanel({ employeeId }: { employeeId: string }) {
  const leave = useLoad(async () => unwrap(await api.GET("/self/{employeeId}/leave", { params: { path: { employeeId } } })) as unknown as MyLeave, employeeId);
  const [open, setOpen] = useState(false);
  const [f, setF] = useState<MyLeaveForm>(blankMyLeave);
  const [busy, setBusy] = useState(false);
  const [failure, setFailure] = useState<string | null>(null);
  const [note, setNote] = useState("");
  const today = isoDate(new Date());
  const d = leave.data;
  const type = f.type || d?.balances[0]?.leave_type || "";
  const form = { ...f, type };
  const problem = myLeaveProblem(form, today);
  const say = (e: unknown, fallback: string) => leaveRefusal(e instanceof ApiProblem ? e.code : undefined, e instanceof Error ? e.message : fallback);

  async function apply() {
    setBusy(true); setFailure(null);
    try {
      const made = unwrap(await api.POST("/self/{employeeId}/leave-requests", { params: { path: { employeeId } }, body: myLeaveBody(form) as never })) as unknown as { days: string; lop_days: string };
      setOpen(false); setF(blankMyLeave()); setNote(`Your request for ${daysText(made)} is with your company for approval.`); leave.reload();
    } catch (e) { setFailure(say(e, "Could not send the request")); } finally { setBusy(false); }
  }
  async function withdraw(id: string) {
    setFailure(null); setNote("");
    try { unwrap(await api.POST("/self/{employeeId}/leave-requests/{id}/cancel", { params: { path: { employeeId, id } } })); setNote("The request was withdrawn."); leave.reload(); }
    catch (e) { setFailure(say(e, "Could not withdraw the request")); }
  }

  return (
    <section className="card" style={{ overflow: "hidden" }} aria-label="My leave">
      <div className="cardhead"><h2>Leave</h2>
        {d?.can_apply && <button type="button" className="btn primary" aria-expanded={open} onClick={() => { setOpen(!open); setFailure(null); setNote(""); }}>Apply for leave</button>}</div>
      <div style={{ padding: "0 20px" }}><ErrorNote error={leave.error} />{failure && <div role="alert" className="error">{failure}</div>}{note && <div role="status" className="t13">{note}</div>}</div>
      {!d ? <Loading what="your leave" /> : (
        <>
          <div style={{ padding: "8px 20px 16px", display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(200px, 1fr))", gap: 10 }} aria-label="Leave balances">
            {d.balances.length === 0 ? <span className="t13">Your company has not set up its leave types yet.</span>
              : d.balances.map((b) => <span key={b.leave_type} style={{ border: "1px solid var(--line)", borderRadius: 10, padding: "10px 12px" }}>{balanceLine(b)}</span>)}
          </div>
          <span className="t13" style={{ display: "block", padding: "0 20px 14px" }}>Leave year {shortDate(d.leave_year_start)} to {shortDate(d.leave_year_end)}. Weekly off: {weekOffText(d.week_off)}.
            {d.unpaid_when_exhausted ? " Leave beyond your balance is unpaid." : " You cannot apply beyond your balance."}</span>
          {open && (
            <form aria-label="Apply for leave" style={{ ...row, background: "var(--surface-2)", display: "flex", flexDirection: "column", gap: 12 }} onSubmit={(e) => { e.preventDefault(); if (!problem && !busy) void apply(); }}>
              <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(200px, 1fr))", gap: 12 }}>
                <div className="field"><label htmlFor="ml-type">Kind of leave</label>
                  <select id="ml-type" className="input" style={h40} value={type} onChange={(e) => setF({ ...f, type: e.target.value })}>
                    {d.balances.map((b) => <option key={b.leave_type} value={b.leave_type}>{b.name}{b.paid ? "" : " (unpaid)"}</option>)}
                  </select></div>
                <div className="field"><label htmlFor="ml-from">{f.halfDay ? "Day" : "First day"}</label><input id="ml-from" type="date" className="input" style={h40} min={today} value={f.from} onChange={(e) => setF({ ...f, from: e.target.value })} /></div>
                {!f.halfDay && <div className="field"><label htmlFor="ml-to">Last day</label><input id="ml-to" type="date" className="input" style={h40} min={f.from || today} value={f.to} onChange={(e) => setF({ ...f, to: e.target.value })} /></div>}
                <div className="field"><label htmlFor="ml-reason">Reason (optional)</label><input id="ml-reason" className="input" style={h40} autoComplete="off" maxLength={300} value={f.reason} onChange={(e) => setF({ ...f, reason: e.target.value })} /></div>
              </div>
              <label style={{ display: "flex", gap: 8, alignItems: "center" }}><input type="checkbox" checked={f.halfDay} onChange={(e) => setF({ ...f, halfDay: e.target.checked })} /><span>Half a day</span></label>
              {problem && (f.from || f.to) && <span className="t13" role="alert" style={{ color: "var(--amber)" }}>{problem}</span>}
              <span style={{ display: "flex", gap: 10 }}>
                <button type="submit" className="btn primary" disabled={busy || !!problem}>{busy ? "Sending…" : "Send request"}</button>
                <button type="button" className="btn" onClick={() => setOpen(false)}>Cancel</button>
              </span>
              <span className="t13">Weekly offs and company holidays inside the range are not counted.</span>
            </form>
          )}
          {d.requests.length === 0 ? <div className="empty">You have not applied for leave yet.</div> : d.requests.map((r) => (
            <div key={r.id} style={{ ...row, display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(170px, 1fr))", gap: 10, alignItems: "center" }}>
              <span style={{ fontWeight: 600 }}>{r.leave_type_name}<span className="t13" style={{ display: "block", fontWeight: 400 }}>{daysText(r)}{r.half_day ? " · half day" : ""}</span></span>
              <span>{r.from === r.to ? shortDate(r.from) : `${shortDate(r.from)} to ${shortDate(r.to)}`}{r.reason && <span className="t13" style={{ display: "block" }}>{r.reason}</span>}</span>
              <span><Pill tone={LEAVE_STATUS[r.status].tone}>{LEAVE_STATUS[r.status].label}</Pill>{r.decision_note && <span className="t13" style={{ display: "block" }}>{r.decision_note}</span>}</span>
              <span>{r.can_cancel && <button type="button" className="btn" aria-label={`Withdraw my ${r.leave_type_name} from ${shortDate(r.from)}`} onClick={() => void withdraw(r.id)}>Withdraw</button>}</span>
            </div>
          ))}
          {upcomingHolidays(d.holidays, today).length > 0 && (
            <div style={row} aria-label="Holidays coming up">
              <span style={{ fontWeight: 600 }}>Holidays coming up</span>
              <span className="t13" style={{ display: "block" }}>{upcomingHolidays(d.holidays, today).map((h) => `${h.name} (${shortDate(h.holiday_date)})`).join(" · ")}</span>
            </div>
          )}
        </>
      )}
    </section>
  );
}

export function MyPayslipsPanel({ employeeId }: { employeeId: string }) {
  const slips = useLoad(async () => (unwrap(await api.GET("/self/{employeeId}/payslips", { params: { path: { employeeId } } })) as unknown as { data: MySlip[] }).data, employeeId);
  const [error, setError] = useState<unknown>(null);
  const rows = slips.data ?? [];
  return (
    <section className="card" style={{ overflow: "hidden" }} aria-label="My payslips">
      <div className="cardhead"><h2>Payslips</h2></div>
      <div style={{ padding: "0 20px" }}><ErrorNote error={slips.error ?? error} /></div>
      {slips.loading && !slips.data ? <Loading what="your payslips" /> : rows.length === 0 ? <div className="empty">No payslips yet. One appears here when a month&apos;s payroll is approved.</div> : (
        <div className="tablewrap"><table style={{ minWidth: 460 }} aria-label="My payslips">
          <thead><tr><th scope="col">Month</th><th scope="col" style={{ textAlign: "right" }}>Gross</th><th scope="col" style={{ textAlign: "right" }}>Net pay</th><th scope="col"><span className="sr">Payslip</span></th></tr></thead>
          <tbody>{rows.map((s) => (
            <tr key={s.id}><td>{runName(s.period_month)}{s.lop_days > 0 && <span className="t13"> · {s.lop_days} unpaid</span>}</td>
              <td className="mono" style={{ textAlign: "right" }}>{formatInr(s.gross)}</td><td className="mono" style={{ textAlign: "right" }}>{formatInr(s.net_pay)}</td>
              <td style={{ textAlign: "right" }}><button type="button" className="linkbtn" style={{ color: "var(--cobalt)" }} aria-label={`Payslip for ${runName(s.period_month)}`}
                onClick={() => { setError(null); openMyPayslipPdf(employeeId, s.id).catch(setError); }}>View PDF</button></td></tr>
          ))}</tbody>
        </table></div>
      )}
    </section>
  );
}

export function MyPoliciesPanel({ employeeId, onChanged }: { employeeId: string; onChanged: () => void }) {
  const docs = useLoad(async () => (unwrap(await api.GET("/self/{employeeId}/policies", { params: { path: { employeeId } } })) as unknown as { data: MyDoc[] }).data, employeeId);
  const [open, setOpen] = useState<MyDoc | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const rows = docs.data ?? [];

  async function read(id: string) {
    if (open?.id === id) { setOpen(null); return; }
    setError(null);
    try { setOpen(unwrap(await api.GET("/self/{employeeId}/policies/{id}", { params: { path: { employeeId, id } } })) as unknown as MyDoc); } catch (e) { setError(e); }
  }
  async function acknowledge(id: string) {
    setBusy(true); setError(null);
    try { setOpen(unwrap(await api.POST("/self/{employeeId}/policies/{id}/acknowledge", { params: { path: { employeeId, id } } })) as unknown as MyDoc); docs.reload(); onChanged(); }
    catch (e) { setError(e); } finally { setBusy(false); }
  }

  return (
    <section className="card" style={{ overflow: "hidden" }} aria-label="Company policies">
      <div className="cardhead"><h2>Company policies</h2></div>
      <div style={{ padding: "0 20px" }}><ErrorNote error={docs.error ?? error} /></div>
      {docs.loading && !docs.data ? <Loading what="the policies" /> : rows.length === 0 ? <div className="empty">Your company has not published any policies yet.</div> : rows.map((d) => {
        const st = docState(d, shortDate);
        return (
          <div key={d.id} style={{ borderTop: "1px solid var(--line-2)" }}>
            <div style={{ padding: "14px 20px", display: "flex", flexWrap: "wrap", gap: 12, alignItems: "center", justifyContent: "space-between" }}>
              <span><span style={{ fontWeight: 600 }}>{d.title}</span><span className="t13" style={{ display: "block" }}>{categoryLabel(d.category)} · version {d.version} · {st.text}</span></span>
              <span style={{ display: "flex", gap: 10, alignItems: "center" }}>{st.todo && <Pill tone="wait">To do</Pill>}
                <button type="button" className="btn" aria-expanded={open?.id === d.id} aria-label={`Read ${d.title}`} onClick={() => void read(d.id)}>{open?.id === d.id ? "Close" : "Read"}</button></span>
            </div>
            {open?.id === d.id && (
              <div style={{ padding: "0 20px 16px", display: "flex", flexDirection: "column", gap: 12 }}>
                <div style={{ whiteSpace: "pre-wrap", border: "1px solid var(--line)", borderRadius: 10, padding: 14, background: "var(--surface-2)", maxHeight: 420, overflow: "auto" }} tabIndex={0} aria-label={`${d.title}, the text`}>{open.body}</div>
                {open.requires_ack && !open.acknowledged_on && (
                  <span><button type="button" className="btn primary" disabled={busy} onClick={() => void acknowledge(d.id)}>{busy ? "Saving…" : "I have read and accept this"}</button>
                    <span className="t13" style={{ display: "block", marginTop: 6 }}>This is recorded in your name with today&apos;s date.</span></span>
                )}
                {open.acknowledged_on && <span className="t13" role="status">You acknowledged this on {shortDate(open.acknowledged_on)}.</span>}
              </div>
            )}
          </div>
        );
      })}
    </section>
  );
}

// ------------------------------------------------------------------ HR's side
interface HrPerson { id: string; employee_code: string; full_name: string; status: string; email: string | null; self_service: SelfService }

/** HR opens, withdraws or ends a person's own sign-in. */
export function SelfServicePanel({ entityId }: { entityId: string }) {
  const people = useLoad(async () => unwrap(await api.GET("/entities/{entityId}/hr/people", { params: { path: { entityId } } })).data as unknown as HrPerson[], entityId);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<unknown>(null);
  const rows = (people.data ?? []).filter((p) => p.status !== "exited" || p.self_service !== "off");

  async function change(p: HrPerson, open: boolean) {
    setBusy(p.id); setError(null);
    const params = { path: { entityId, id: p.id } };
    try {
      if (open) unwrap(await api.POST("/entities/{entityId}/employees/{id}/self-service", { params }));
      else unwrap(await api.DELETE("/entities/{entityId}/employees/{id}/self-service", { params }));
      people.reload();
    } catch (e) { setError(e); } finally { setBusy(null); }
  }

  return (
    <section className="card" style={{ overflow: "hidden" }} aria-label="Employees' own sign-in">
      <div style={{ padding: "14px 20px", borderBottom: "1px solid var(--line-2)" }}>
        <h2 style={{ margin: 0 }}>Employees&apos; own sign-in</h2>
        <span className="t13">A person you open this for signs in with the email on their record and sees only their own payslips, leave, policies and joining checklist. They are not a member of the company and see nothing else. Nothing is emailed yet, so tell them to sign in.</span>
      </div>
      <div style={{ padding: "0 20px" }}><ErrorNote error={people.error ?? error} /></div>
      {people.loading && !people.data ? <Loading what="people" /> : rows.length === 0 ? <div className="empty">No employees yet.</div> : rows.map((p) => {
        const a = selfServiceAction(p);
        return (
          <div key={p.id} style={{ ...row, display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(190px, 1fr))", gap: 10, alignItems: "center" }}>
            <span style={{ fontWeight: 600 }}>{p.full_name}<span className="t13 mono" style={{ display: "block", fontWeight: 400 }}>{p.employee_code}{p.email ? ` · ${p.email}` : ""}</span></span>
            <span><Pill tone={p.self_service === "active" ? "ok" : p.self_service === "invited" ? "wait" : "prep"}>{SELF_SERVICE[p.self_service].label}</Pill>
              <span className="t13" style={{ display: "block" }}>{SELF_SERVICE[p.self_service].hint}</span></span>
            <span>{"blocked" in a ? <span className="t13">{a.blocked}</span>
              : <button type="button" className={a.open ? "btn primary" : "btn"} disabled={busy === p.id} aria-label={`${a.label} for ${p.full_name}`} onClick={() => void change(p, a.open)}>{a.label}</button>}</span>
          </div>
        );
      })}
    </section>
  );
}
