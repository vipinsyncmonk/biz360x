"use client";
import Link from "next/link";
import { useEffect, useState } from "react";
import { api, unwrap } from "@/lib/api";
import { isoDate } from "@/lib/format";
import { useLoad } from "@/lib/hooks";
import {
  KIND_LABEL, KIND_TONE, Matrix, MyWork, Slot, TaskForm, blankTask, choices, dueText, elsewhereText, matrixBody, personLabel, queueLine, sameMatrix, setSlot, slotUser, splitWork, taskBody,
  taskProblem,
} from "@/lib/work";
import { ErrorNote, Loading, Pill } from "./ui";

/** What is waiting on the signed-in person in this company, in the order to work it, and a line about their other companies. */
export function WorkQueue({ entityId, canAssign }: { entityId: string; canAssign: boolean }) {
  const work = useLoad(async () => unwrap(await api.GET("/me/work", { params: { query: { limit: 200 } } })) as unknown as MyWork, entityId);
  const [adding, setAdding] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);
  const [failure, setFailure] = useState<unknown>(null);
  const today = isoDate(new Date());
  const { here, elsewhere } = splitWork(work.data?.data ?? [], entityId);

  async function finish(id: string) {
    setBusy(id); setFailure(null);
    try { unwrap(await api.PATCH("/entities/{entityId}/tasks/{id}", { params: { path: { entityId, id }, header: { "If-Match": "*" } }, body: { status: "done" } as never })); work.reload(); }
    catch (e) { setFailure(e); } finally { setBusy(null); }
  }

  return (
    <section className="card" style={{ overflow: "hidden" }} aria-label="Your work">
      <div className="cardhead"><h2>Your work</h2>
        {canAssign && <button type="button" className="btn" aria-expanded={adding} onClick={() => setAdding(!adding)}>+ Task</button>}</div>
      <div style={{ padding: "0 20px" }}><ErrorNote error={work.error ?? failure} /></div>
      {adding && <div style={{ padding: "0 20px 16px" }}><AddTask entityId={entityId} onDone={() => { setAdding(false); work.reload(); }} onCancel={() => setAdding(false)} /></div>}
      {work.loading && !work.data ? <Loading what="your work" /> : (
        <>
          {here.length === 0 && <div className="empty">{queueLine(here)}</div>}
          {here.map((i) => (
            <div className="row" key={`${i.kind}${i.id}`}>
              <div className="grow">
                <span className="t15">{i.title}</span>
                <span className="t13">{KIND_LABEL[i.kind]}{i.detail ? ` · ${i.detail}` : ""}</span>
              </div>
              <Pill tone={i.overdue ? "due" : KIND_TONE[i.kind]}>{dueText(i.due_on, today)}</Pill>
              {i.kind === "task"
                ? <button type="button" className="btn" disabled={busy === i.id} aria-label={`Mark done: ${i.title}`} onClick={() => void finish(i.id!)}>Done</button>
                : <Link href={i.href} className="btn" style={{ display: "inline-flex", alignItems: "center", textDecoration: "none" }} aria-label={`Open ${i.title}`}>Open</Link>}
            </div>
          ))}
          {elsewhere.length > 0 && <div className="t13" role="status" style={{ padding: "12px 20px", borderTop: "1px solid var(--line-2)" }}>Also waiting on you: {elsewhereText(elsewhere)}. Switch company to see them.</div>}
        </>
      )}
    </section>
  );
}

function AddTask({ entityId, onDone, onCancel }: { entityId: string; onDone: () => void; onCancel: () => void }) {
  const people = useLoad(async () => (unwrap(await api.GET("/entities/{entityId}/responsibilities", { params: { path: { entityId } } })) as unknown as Matrix).people, entityId);
  const [f, setF] = useState<TaskForm>(blankTask);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const problem = taskProblem(f);
  async function save() {
    setBusy(true); setError(null);
    try { unwrap(await api.POST("/entities/{entityId}/tasks", { params: { path: { entityId } }, body: taskBody(f) as never })); onDone(); } catch (e) { setError(e); setBusy(false); }
  }
  return (
    <form style={{ display: "flex", flexDirection: "column", gap: 10 }} aria-label="Add a task" onSubmit={(e) => { e.preventDefault(); if (!problem && !busy) void save(); }}>
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(190px, 1fr))", gap: 12 }}>
        <div className="field"><label htmlFor="task-title">What needs doing</label><input id="task-title" className="input" style={{ height: 40 }} autoComplete="off" maxLength={200} value={f.title} onChange={(e) => setF({ ...f, title: e.target.value })} /></div>
        <div className="field"><label htmlFor="task-who">Give it to</label>
          <select id="task-who" className="input" style={{ height: 40 }} value={f.assignee} onChange={(e) => setF({ ...f, assignee: e.target.value })}>
            <option value="">Nobody yet</option>{(people.data ?? []).map((p) => <option key={p.user_id} value={p.user_id}>{personLabel(p)}</option>)}
          </select></div>
        <div className="field"><label htmlFor="task-due">Due (optional)</label><input id="task-due" type="date" className="input" style={{ height: 40 }} value={f.due} onChange={(e) => setF({ ...f, due: e.target.value })} /></div>
      </div>
      <ErrorNote error={error ?? people.error} />
      <span style={{ display: "flex", gap: 10 }}><button type="submit" className="btn primary" disabled={busy || !!problem}>{busy ? "Saving…" : "Add task"}</button><button type="button" className="btn" onClick={onCancel}>Cancel</button></span>
    </form>
  );
}

/** The responsibility matrix: for each kind of work, who prepares it and who approves it. Everyone can read it; an owner changes it. */
export function WhoDoesWhat({ entityId, isOwner }: { entityId: string; isOwner: boolean }) {
  const loaded = useLoad(async () => unwrap(await api.GET("/entities/{entityId}/responsibilities", { params: { path: { entityId } } })) as unknown as Matrix, entityId);
  const [data, setData] = useState<Slot[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [note, setNote] = useState("");
  useEffect(() => { if (loaded.data) setData(loaded.data.data); }, [loaded.data]);
  const m = loaded.data;
  const changed = !!m && !sameMatrix(data, m.data);

  async function save() {
    setBusy(true); setError(null); setNote("");
    try {
      const out = unwrap(await api.PUT("/entities/{entityId}/responsibilities", { params: { path: { entityId } }, body: matrixBody(data) as never })) as unknown as Matrix;
      const n = out.filings_reassigned ?? 0;
      setNote(n ? `Saved. ${n} open ${n === 1 ? "filing" : "filings"} moved to the people now named.` : "Saved.");
      loaded.reload();
    } catch (e) { setError(e); } finally { setBusy(false); }
  }

  return (
    <section className="card" style={{ overflow: "hidden" }} aria-label="Who does what">
      <div style={{ padding: "14px 20px", borderBottom: "1px solid var(--line-2)" }}>
        <h2 style={{ margin: 0 }}>Who does what</h2>
        <span className="t13">New filings go to the person who prepares them, and a request for approval goes to the named approver (never to the person who raised it). Where nobody is named, the work is open to anyone who can do it.</span>
      </div>
      <div style={{ padding: "0 20px" }}><ErrorNote error={loaded.error ?? error} /></div>
      {!m ? <Loading what="responsibilities" /> : (
        <div className="tablewrap"><table style={{ minWidth: 620 }} aria-label="Responsibilities">
          <thead><tr><th scope="col">Work</th><th scope="col">Prepares</th><th scope="col">Approves</th></tr></thead>
          <tbody>{m.kinds.map((k) => (
            <tr key={k.key}>
              <td><span style={{ fontWeight: 500 }}>{k.label}</span><span className="t13" style={{ display: "block" }}>{k.about}</span></td>
              {(["prepare", "approve"] as const).map((step) => (
                <td key={step}>{!k.steps.includes(step) ? <span className="t13">Whoever starts it</span> : (
                  <select className="input" style={{ height: 36, fontSize: 14 }} aria-label={`${step === "prepare" ? "Prepares" : "Approves"} ${k.label}`} disabled={!isOwner || busy}
                          value={slotUser(data, k.key, step)} onChange={(e) => setData(setSlot(data, k.key, step, e.target.value))}>
                    <option value="">Nobody named</option>{choices(m.people, k.key, step).map((p) => <option key={p.user_id} value={p.user_id}>{personLabel(p)}</option>)}
                  </select>
                )}</td>
              ))}
            </tr>
          ))}</tbody>
        </table></div>
      )}
      <div style={{ padding: "12px 20px", display: "flex", gap: 12, flexWrap: "wrap", alignItems: "center" }}>
        {isOwner ? <button type="button" className="btn primary" disabled={busy || !changed} onClick={() => void save()}>{busy ? "Saving…" : "Save who does what"}</button>
          : <span className="t13">Only an owner can change this.</span>}
        {note && <span className="t13" role="status">{note}</span>}
      </div>
    </section>
  );
}
