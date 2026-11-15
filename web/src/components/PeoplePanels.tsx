"use client";
import { useRef, useState } from "react";
import { api, unwrap } from "@/lib/api";
import { isoDate, shortDate } from "@/lib/format";
import { useLoad } from "@/lib/hooks";
import {
  PROOF_KINDS, PROOF_STATUS, blankContact, cannotApprove, contactBody, contactForm, contactProblem, dueLine, fileSize, hasTeam, kindLabel, openFile, proofProblem, taskProblem,
  type Contact, type ContactForm, type MyTask, type MyTeam, type Proof, type ProofKind,
} from "@/lib/people";
import { toBase64 } from "@/lib/vault";
import { ErrorNote, Loading, Pill } from "./ui";

const row = { borderTop: "1px solid var(--line-2)", padding: "12px 20px" } as const;
const pad = { padding: "0 20px 18px", display: "flex", flexDirection: "column", gap: 12 } as const;
const grid = { display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(200px, 1fr))", gap: 10 } as const;

/** The address and emergency-contact fields, used by the person and by HR. */
function ContactFields({ idp, f, set }: { idp: string; f: ContactForm; set: (f: ContactForm) => void }) {
  const field = (key: keyof ContactForm, label: string, extra: Record<string, unknown> = {}) => (
    <div className="field"><label htmlFor={`${idp}-contact-${key}`}>{label}</label>
      <input id={`${idp}-contact-${key}`} className="input" autoComplete="off" value={f[key]} onChange={(e) => set({ ...f, [key]: e.target.value })} {...extra} /></div>
  );
  return (
    <>
      <div style={grid} role="group" aria-label="Address">
        {field("line1", "Address line 1", { maxLength: 120 })}{field("line2", "Address line 2", { maxLength: 120 })}{field("city", "City", { maxLength: 60 })}
        {field("state", "State code (GST code, like 29)", { maxLength: 2, inputMode: "numeric" })}{field("pincode", "PIN code", { maxLength: 6, inputMode: "numeric" })}
      </div>
      <div style={grid} role="group" aria-label="Emergency contact">
        {field("name", "Emergency contact: name", { maxLength: 80 })}{field("relation", "Relation", { maxLength: 40 })}
        {field("mobile", "Emergency contact: mobile (with country code)", { inputMode: "tel", placeholder: "+919812345678" })}
      </div>
    </>
  );
}

/** The contact form. What the server has fills it once; it never overwrites what the person has already started typing. */
function useContactForm(): [ContactForm, (x: ContactForm) => void, (c: Contact) => void] {
  const [f, setF] = useState<ContactForm>(blankContact());
  const edited = useRef(false);
  return [f, (x) => { edited.current = true; setF(x); }, (c) => { if (!edited.current) setF(contactForm(c)); }];
}

// ------------------------------------------------------------------ the employee's side
/** My address and emergency contact: mine to keep, so they take effect at once. */
export function MyContactPanel({ employeeId }: { employeeId: string }) {
  const [f, edit, fill] = useContactForm();
  const c = useLoad(async () => {
    const d = unwrap(await api.GET("/self/{employeeId}/contact", { params: { path: { employeeId } } })) as unknown as Contact;
    fill(d); return d;
  }, employeeId);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [saved, setSaved] = useState(false);
  const problem = contactProblem(f);

  async function save() {
    setBusy(true); setError(null); setSaved(false);
    try { unwrap(await api.PUT("/self/{employeeId}/contact", { params: { path: { employeeId } }, body: contactBody(f) as never })); setSaved(true); c.reload(); }
    catch (e) { setError(e); } finally { setBusy(false); }
  }
  return (
    <section className="card" style={{ overflow: "hidden" }} aria-label="My address and emergency contact">
      <div className="cardhead"><h2>Address and emergency contact</h2></div>
      <div style={pad}>
        <ErrorNote error={c.error ?? error} />
        <span className="t13">Where HR can reach you, and who to call if something happens to you at work. These take effect as soon as you save.</span>
        <ContactFields idp="my" f={f} set={(x) => { edit(x); setSaved(false); }} />
        {problem && <span className="t13" role="alert">{problem}</span>}
        {saved && <span className="t13" role="status">Saved.</span>}
        <span><button type="button" className="btn primary" disabled={busy || !!problem} onClick={() => void save()}>{busy ? "Saving…" : "Save"}</button></span>
      </div>
    </section>
  );
}

/** My proofs: files I upload for HR to accept. */
export function MyProofsPanel({ employeeId }: { employeeId: string }) {
  const list = useLoad(async () => (unwrap(await api.GET("/self/{employeeId}/proofs", { params: { path: { employeeId } } })) as unknown as { data: Proof[] }).data, employeeId);
  const [kind, setKind] = useState<ProofKind>("identity");
  const [label, setLabel] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const input = useRef<HTMLInputElement>(null);
  const problem = proofProblem(file, label);
  const rows = list.data ?? [];

  async function upload() {
    if (!file) return;
    setBusy(true); setError(null);
    try {
      const content_base64 = toBase64(await file.arrayBuffer());
      unwrap(await api.POST("/self/{employeeId}/proofs", { params: { path: { employeeId } }, body: { kind, label: label.trim(), file_name: file.name, content_base64 } }));
      setLabel(""); setFile(null); if (input.current) input.current.value = ""; list.reload();
    } catch (e) { setError(e); } finally { setBusy(false); }
  }
  async function remove(id: string) {
    setBusy(true); setError(null);
    try { unwrap(await api.DELETE("/self/{employeeId}/proofs/{id}", { params: { path: { employeeId, id } } })); list.reload(); } catch (e) { setError(e); } finally { setBusy(false); }
  }
  const view = (id: string) => { setError(null); openFile(() => api.GET("/self/{employeeId}/proofs/{id}/file", { params: { path: { employeeId, id }, query: { download: false } }, parseAs: "blob" })).catch(setError); };

  return (
    <section className="card" style={{ overflow: "hidden" }} aria-label="My proofs">
      <div className="cardhead"><h2>My proofs</h2></div>
      <div style={pad}>
        <ErrorNote error={list.error ?? error} />
        <span className="t13">Upload a document HR asks for: identity, address, education, a cancelled cheque, tax proofs. A PDF, JPEG or PNG of up to 5 MB. HR accepts it or tells you why not.</span>
        <div style={grid} role="group" aria-label="Upload a proof">
          <div className="field"><label htmlFor="pf-kind">Kind</label>
            <select id="pf-kind" className="input" value={kind} onChange={(e) => setKind(e.target.value as ProofKind)}>{PROOF_KINDS.map((k) => <option key={k.value} value={k.value}>{k.label}</option>)}</select></div>
          <div className="field"><label htmlFor="pf-label">What it is</label><input id="pf-label" className="input" maxLength={80} placeholder="Aadhaar card" value={label} onChange={(e) => setLabel(e.target.value)} /></div>
          <div className="field"><label htmlFor="pf-file">File</label><input id="pf-file" ref={input} className="input" type="file" accept=".pdf,.jpg,.jpeg,.png,application/pdf,image/jpeg,image/png" onChange={(e) => setFile(e.target.files?.[0] ?? null)} /></div>
        </div>
        {(file || label) && problem && <span className="t13" role="alert">{problem}</span>}
        <span><button type="button" className="btn primary" disabled={busy || !!problem} onClick={() => void upload()}>{busy ? "Uploading…" : "Upload"}</button></span>
      </div>
      {list.loading && !list.data ? <Loading what="your files" /> : rows.length === 0 ? <div className="empty">Nothing uploaded yet.</div> : (
        <ul aria-label="Uploaded files" style={{ listStyle: "none", margin: 0, padding: 0 }}>
          {rows.map((p) => (
            <li key={p.id} style={{ ...row, display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(190px, 1fr))", gap: 10, alignItems: "center" }}>
              <span style={{ fontWeight: 600 }}>{p.label}<span className="t13" style={{ display: "block", fontWeight: 400 }}>{kindLabel(p.kind)} · {p.file_name} · {fileSize(p.size_bytes)}</span></span>
              <span><Pill tone={PROOF_STATUS[p.status].tone}>{PROOF_STATUS[p.status].label}</Pill>
                {p.hr_note && <span className="t13" style={{ display: "block" }}>{p.hr_note}</span>}</span>
              <span style={{ display: "flex", gap: 8 }}>
                <button type="button" className="linkbtn" style={{ color: "var(--cobalt)" }} aria-label={`View ${p.label}`} onClick={() => view(p.id)}>View</button>
                {p.status === "pending" && <button type="button" className="linkbtn" aria-label={`Remove ${p.label}`} disabled={busy} onClick={() => void remove(p.id)}>Remove</button>}
              </span>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

/** What HR has asked me to do. */
export function MyTasksPanel({ employeeId }: { employeeId: string }) {
  const tasks = useLoad(async () => unwrap(await api.GET("/self/{employeeId}/tasks", { params: { path: { employeeId } } })) as unknown as { data: MyTask[]; open: number }, employeeId);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<unknown>(null);
  const rows = tasks.data?.data ?? [];
  const today = isoDate(new Date());

  async function set(t: MyTask, done: boolean) {
    setBusy(t.id); setError(null);
    try { unwrap(await api.PUT("/self/{employeeId}/tasks/{id}", { params: { path: { employeeId, id: t.id } }, body: { done } })); tasks.reload(); } catch (e) { setError(e); } finally { setBusy(null); }
  }
  return (
    <section className="card" style={{ overflow: "hidden" }} aria-label="My tasks">
      <div className="cardhead"><h2>My tasks</h2>{!!tasks.data?.open && <Pill tone="wait">{tasks.data.open} open</Pill>}</div>
      <div style={{ padding: "0 20px" }}><ErrorNote error={tasks.error ?? error} /></div>
      {tasks.loading && !tasks.data ? <Loading what="your tasks" /> : rows.length === 0 ? <div className="empty">Nothing is waiting for you.</div> : (
        <ul style={{ listStyle: "none", margin: 0, padding: 0 }} aria-label="Task list">
          {rows.map((t) => {
            const due = dueLine(t, today, shortDate);
            return (
              <li key={t.id} style={{ ...row, display: "flex", gap: 12, alignItems: "flex-start" }}>
                <input type="checkbox" aria-label={t.title} checked={t.status === "done"} disabled={busy === t.id} onChange={() => void set(t, t.status !== "done")} style={{ marginTop: 4 }} />
                <span style={{ fontWeight: 600, textDecoration: t.status === "done" ? "line-through" : undefined }}>{t.title}
                  {t.detail && <span className="t13" style={{ display: "block", fontWeight: 400 }}>{t.detail}</span>}
                  <span className="t13" style={{ display: "block", fontWeight: 400, color: due.late ? "var(--danger, #b3261e)" : undefined }}>{due.text}</span></span>
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}

/** My manager, the people who report to me, and their leave. Shown only to someone who has a manager or a team. */
export function MyTeamPanel({ employeeId }: { employeeId: string }) {
  const team = useLoad(async () => unwrap(await api.GET("/self/{employeeId}/team", { params: { path: { employeeId } } })) as unknown as MyTeam, employeeId);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [why, setWhy] = useState<Record<string, string>>({});
  const t = team.data;
  if (!hasTeam(t)) return team.error ? <ErrorNote error={team.error} /> : null;

  async function decide(id: string, approve: boolean) {
    setBusy(id); setError(null);
    const params = { path: { employeeId, id } };
    const body = { note: (why[id] ?? "").trim() || null };
    try {
      if (approve) unwrap(await api.POST("/self/{employeeId}/team/leave/{id}/approve", { params, body }));
      else unwrap(await api.POST("/self/{employeeId}/team/leave/{id}/reject", { params, body }));
      team.reload();
    } catch (e) { setError(e); } finally { setBusy(null); }
  }
  return (
    <section className="card" style={{ overflow: "hidden" }} aria-label="My team">
      <div className="cardhead"><h2>My team</h2>{t!.pending > 0 && <Pill tone="wait">{t!.pending} waiting</Pill>}</div>
      <div style={pad}>
        <ErrorNote error={error} />
        {t!.manager && <span className="t13">You report to <b>{t!.manager.full_name}</b>{t!.manager.designation ? `, ${t!.manager.designation}` : ""}.</span>}
        {t!.reports.length > 0 && <span className="t13">{t!.reports.map((r) => r.full_name).join(", ")} report to you. You decide their leave here, except leave with unpaid days: that changes pay, so HR decides it.</span>}
      </div>
      {t!.leave.map((r) => {
        const blocked = cannotApprove(r);
        return (
          <div key={r.id} style={{ ...row, display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(200px, 1fr))", gap: 10, alignItems: "start" }}>
            <span style={{ fontWeight: 600 }}>{r.employee_name}<span className="t13" style={{ display: "block", fontWeight: 400 }}>{r.leave_type_name}</span></span>
            <span>{r.from === r.to ? shortDate(r.from) : `${shortDate(r.from)} to ${shortDate(r.to)}`}<span className="t13" style={{ display: "block" }}>{r.days} {r.days === "1" ? "day" : "days"}{/[1-9]/.test(r.lop_days) ? ` · ${r.lop_days} unpaid` : ""}{r.reason ? ` · ${r.reason}` : ""}</span></span>
            {r.status === "pending" ? (
              <span style={{ display: "flex", flexDirection: "column", gap: 6 }}>
                <button type="button" className="btn primary" disabled={busy === r.id || !!blocked} aria-label={`Approve leave of ${r.employee_name} from ${r.from}`} onClick={() => void decide(r.id, true)}>Approve</button>
                {blocked && <span className="t13">{blocked}</span>}
                <label className="sr" htmlFor={`tw-${r.id}`}>Note for {r.employee_name}</label>
                <input id={`tw-${r.id}`} className="input" placeholder="Note (optional)" maxLength={300} value={why[r.id] ?? ""} onChange={(e) => setWhy({ ...why, [r.id]: e.target.value })} />
                <button type="button" className="btn" disabled={busy === r.id} aria-label={`Refuse leave of ${r.employee_name} from ${r.from}`} onClick={() => void decide(r.id, false)}>Refuse</button>
              </span>
            ) : <span><Pill tone={r.status === "approved" ? "ok" : "prep"}>{r.status === "approved" ? "Approved" : r.status === "rejected" ? "Refused" : "Withdrawn"}</Pill>{r.decision_note && <span className="t13" style={{ display: "block" }}>{r.decision_note}</span>}</span>}
          </div>
        );
      })}
    </section>
  );
}

// ------------------------------------------------------------------ HR's side
interface Person { id: string; employee_code: string; full_name: string; status: string; manager_id: string | null; manager_name: string | null }
interface HrTask { id: string; title: string; detail: string | null; due_on: string | null; status: "open" | "done" | "cancelled" }

/** Everything HR keeps about one person beyond pay: who they report to, how to reach them, their files and their tasks. */
export function PeopleFilesPanel({ entityId }: { entityId: string }) {
  const people = useLoad(async () => unwrap(await api.GET("/entities/{entityId}/hr/people", { params: { path: { entityId } } })).data as unknown as Person[], entityId);
  const [who, setWho] = useState("");
  const rows = (people.data ?? []).filter((p) => p.status !== "exited");
  const current = rows.find((p) => p.id === who) ?? null;
  return (
    <section className="card" style={{ overflow: "hidden" }} aria-label="People files">
      <div style={{ padding: "14px 20px", borderBottom: "1px solid var(--line-2)" }}>
        <h2 style={{ margin: 0 }}>People files</h2>
        <span className="t13">Who a person reports to, how to reach them, the files they have uploaded and the tasks you have given them.</span>
      </div>
      <div style={{ ...pad, paddingTop: 14 }}>
        <ErrorNote error={people.error} />
        <div className="field" style={{ maxWidth: 360 }}><label htmlFor="pf-person">Person</label>
          <select id="pf-person" className="input" value={who} onChange={(e) => setWho(e.target.value)}>
            <option value="">Choose a person</option>{rows.map((p) => <option key={p.id} value={p.id}>{p.employee_code} · {p.full_name}</option>)}
          </select></div>
      </div>
      {current && <PersonFiles key={current.id} entityId={entityId} person={current} others={rows.filter((p) => p.id !== current.id)} onManager={() => people.reload()} />}
    </section>
  );
}

function PersonFiles({ entityId, person, others, onManager }: { entityId: string; person: Person; others: Person[]; onManager: () => void }) {
  const [f, edit, fill] = useContactForm();
  const p = { path: { entityId, id: person.id } };
  const contact = useLoad(async () => { const d = unwrap(await api.GET("/entities/{entityId}/employees/{id}/contact", { params: p })) as unknown as Contact; fill(d); return d; }, person.id);
  const proofs = useLoad(async () => (unwrap(await api.GET("/entities/{entityId}/employees/{id}/proofs", { params: p })) as unknown as { data: Proof[] }).data, person.id);
  const tasks = useLoad(async () => (unwrap(await api.GET("/entities/{entityId}/employee-tasks", { params: { path: { entityId }, query: { employee_id: person.id } } })) as unknown as { data: HrTask[] }).data, person.id);
  const [manager, setManager] = useState(person.manager_id ?? "");
  const [title, setTitle] = useState("");
  const [detail, setDetail] = useState("");
  const [due, setDue] = useState("");
  const [reasons, setReasons] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [note, setNote] = useState("");
  const today = isoDate(new Date());
  const problem = contactProblem(f);
  const taskIssue = taskProblem(title, person.id);

  async function run(key: string, fn: () => Promise<void>, ok = "") {
    setBusy(key); setError(null); setNote("");
    try { await fn(); setNote(ok); } catch (e) { setError(e); } finally { setBusy(null); }
  }
  const saveManager = () => run("manager", async () => {
    unwrap(await api.PUT("/entities/{entityId}/employees/{id}/manager", { params: p, body: { manager_id: manager || null } })); onManager();
  }, manager ? "Manager saved." : "Manager cleared.");
  const saveContact = () => run("contact", async () => { unwrap(await api.PUT("/entities/{entityId}/employees/{id}/contact", { params: p, body: contactBody(f) as never })); contact.reload(); }, "Contact details saved.");
  const decide = (proofId: string, accept: boolean) => run(proofId, async () => {
    const params = { path: { entityId, id: person.id, proofId } };
    if (accept) unwrap(await api.POST("/entities/{entityId}/employees/{id}/proofs/{proofId}/accept", { params, body: {} }));
    else unwrap(await api.POST("/entities/{entityId}/employees/{id}/proofs/{proofId}/reject", { params, body: { note: (reasons[proofId] ?? "").trim() } }));
    proofs.reload();
  });
  const view = (proofId: string) => { setError(null); openFile(() => api.GET("/entities/{entityId}/employees/{id}/proofs/{proofId}/file", { params: { path: { entityId, id: person.id, proofId }, query: { download: false } }, parseAs: "blob" })).catch(setError); };
  const addTask = () => run("task", async () => {
    unwrap(await api.POST("/entities/{entityId}/employee-tasks", { params: { path: { entityId } }, body: { employee_id: person.id, title: title.trim(), detail: detail.trim() || null, due_on: due || null } }));
    setTitle(""); setDetail(""); setDue(""); tasks.reload();
  }, "Task given.");
  const cancelTask = (id: string) => run(id, async () => { unwrap(await api.POST("/entities/{entityId}/employee-tasks/{id}/cancel", { params: { path: { entityId, id } } })); tasks.reload(); });

  return (
    <div aria-label={`Files of ${person.full_name}`}>
      <div style={{ padding: "0 20px" }}><ErrorNote error={error ?? contact.error ?? proofs.error ?? tasks.error} />{note && <div role="status" className="t13">{note}</div>}</div>

      <div style={{ ...row, display: "flex", flexWrap: "wrap", gap: 10, alignItems: "flex-end" }} role="group" aria-label="Reports to">
        <div className="field" style={{ minWidth: 260 }}><label htmlFor="pf-manager">Reports to</label>
          <select id="pf-manager" className="input" value={manager} onChange={(e) => setManager(e.target.value)}>
            <option value="">No one</option>{others.map((o) => <option key={o.id} value={o.id}>{o.employee_code} · {o.full_name}</option>)}
          </select></div>
        <button type="button" className="btn" disabled={busy === "manager" || manager === (person.manager_id ?? "")} onClick={() => void saveManager()}>Save manager</button>
        <span className="t13" style={{ flexBasis: "100%" }}>A manager decides their team&apos;s leave from their own page, except leave with unpaid days, which stays with you. They need their own sign-in.</span>
      </div>

      <div style={{ ...row, display: "flex", flexDirection: "column", gap: 10 }} role="group" aria-label="Contact details">
        <b>Address and emergency contact</b>
        <ContactFields idp="hr" f={f} set={edit} />
        {problem && <span className="t13" role="alert">{problem}</span>}
        <span><button type="button" className="btn" disabled={busy === "contact" || !!problem} onClick={() => void saveContact()}>Save contact details</button></span>
      </div>

      <div style={row}>
        <b>Uploaded files</b>
        {proofs.loading && !proofs.data ? <Loading what="files" /> : (proofs.data ?? []).length === 0 ? <div className="t13">Nothing uploaded.</div> : (proofs.data ?? []).map((pr) => (
          <div key={pr.id} style={{ borderTop: "1px solid var(--line-2)", marginTop: 10, paddingTop: 10, display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(210px, 1fr))", gap: 10, alignItems: "start" }}>
            <span style={{ fontWeight: 600 }}>{pr.label}<span className="t13" style={{ display: "block", fontWeight: 400 }}>{kindLabel(pr.kind)} · {pr.file_name} · {fileSize(pr.size_bytes)} · {shortDate(pr.uploaded_at)}</span></span>
            <span><Pill tone={PROOF_STATUS[pr.status].tone}>{PROOF_STATUS[pr.status].label}</Pill>{pr.hr_note && <span className="t13" style={{ display: "block" }}>{pr.hr_note}</span>}</span>
            <span style={{ display: "flex", flexDirection: "column", gap: 6 }}>
              <button type="button" className="linkbtn" style={{ color: "var(--cobalt)", textAlign: "left" }} aria-label={`Open ${pr.label}`} onClick={() => view(pr.id)}>Open</button>
              {pr.status === "pending" && (
                <>
                  <button type="button" className="btn primary" disabled={busy === pr.id} aria-label={`Accept ${pr.label}`} onClick={() => void decide(pr.id, true)}>Accept</button>
                  <label className="sr" htmlFor={`pw-${pr.id}`}>Reason for not accepting {pr.label}</label>
                  <input id={`pw-${pr.id}`} className="input" placeholder="Reason, to refuse" maxLength={300} value={reasons[pr.id] ?? ""} onChange={(e) => setReasons({ ...reasons, [pr.id]: e.target.value })} />
                  <button type="button" className="btn" disabled={busy === pr.id || !(reasons[pr.id] ?? "").trim()} aria-label={`Refuse ${pr.label}`} onClick={() => void decide(pr.id, false)}>Refuse</button>
                </>
              )}
            </span>
          </div>
        ))}
      </div>

      <div style={{ ...row, display: "flex", flexDirection: "column", gap: 10 }} role="group" aria-label="Tasks">
        <b>Tasks</b>
        <div style={grid}>
          <div className="field"><label htmlFor="tk-title">Ask them to</label><input id="tk-title" className="input" maxLength={120} value={title} onChange={(e) => setTitle(e.target.value)} placeholder="Send your last payslip" /></div>
          <div className="field"><label htmlFor="tk-detail">Details (optional)</label><input id="tk-detail" className="input" maxLength={600} value={detail} onChange={(e) => setDetail(e.target.value)} /></div>
          <div className="field"><label htmlFor="tk-due">Due (optional)</label><input id="tk-due" className="input" type="date" value={due} onChange={(e) => setDue(e.target.value)} /></div>
        </div>
        <span><button type="button" className="btn primary" disabled={busy === "task" || !!taskIssue} onClick={() => void addTask()}>Give task</button></span>
        {(tasks.data ?? []).filter((t) => t.status !== "cancelled").map((t) => {
          const d = dueLine({ due_on: t.due_on, status: t.status === "done" ? "done" : "open" }, today, shortDate);
          return (
            <div key={t.id} style={{ display: "flex", flexWrap: "wrap", gap: 10, justifyContent: "space-between", alignItems: "center", borderTop: "1px solid var(--line-2)", paddingTop: 8 }}>
              <span>{t.title}<span className="t13" style={{ display: "block" }}>{d.text}</span></span>
              {t.status === "open" && <button type="button" className="btn" disabled={busy === t.id} aria-label={`Withdraw task ${t.title}`} onClick={() => void cancelTask(t.id)}>Withdraw</button>}
            </div>
          );
        })}
      </div>
    </div>
  );
}
