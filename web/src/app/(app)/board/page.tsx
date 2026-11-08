"use client";
import Link from "next/link";
import { useState } from "react";
import { api, unwrap } from "@/lib/api";
import { isoDate, shortDate } from "@/lib/format";
import { useLoad } from "@/lib/hooks";
import {
  BOARD_GAP_DAYS, MEETING_KINDS, MEETING_LABEL, Meeting, MeetingForm, RES_KINDS, RES_STATUS, Resolution, ResolutionForm, blankMeeting, blankResolution, gapWarning, heldProblem,
  istDate, istTime, meetingBody, meetingReady, meetingState, nextResolutionNumber, resolutionActions, resolutionBody, resolutionReady,
} from "@/lib/board";
import { useSession } from "@/lib/session";
import { ErrorNote, Loading, Pill } from "@/components/ui";

export default function Board() {
  const { entity } = useSession();
  const id = entity?.id ?? null;
  const [addMeeting, setAddMeeting] = useState(false);
  const [addRes, setAddRes] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const today = isoDate(new Date());

  const meetings = useLoad(async () => unwrap(await api.GET("/entities/{entityId}/meetings", { params: { path: { entityId: id! }, query: { limit: 100 } } })).data as unknown as Meeting[], id);
  const resolutions = useLoad(async () => unwrap(await api.GET("/entities/{entityId}/resolutions", { params: { path: { entityId: id! }, query: { limit: 200 } } })).data as unknown as Resolution[], id);
  const reload = () => { meetings.reload(); resolutions.reload(); };
  const ms = meetings.data ?? [];
  const rs = resolutions.data ?? [];
  const meetingName = (mid: string | null) => { const m = ms.find((x) => x.id === mid); return m ? `${MEETING_LABEL[m.kind] ?? m.kind} · ${shortDate(istDate(m.scheduled_for))}` : null; };
  const open = rs.filter((r) => r.status === "draft" || r.status === "circulated").length;
  const mgt = rs.filter((r) => r.status === "passed" && r.needs_mgt14).length;

  if (!entity) return <main className="page"><div className="card card-pad">Select a workspace first.</div></main>;
  return (
    <main className="page">
      <div style={{ display: "flex", flexWrap: "wrap", gap: 16, justifyContent: "space-between", alignItems: "flex-end" }}>
        <div>
          <Link href="/legal" style={{ fontSize: 14 }}>← Legal &amp; Contracts</Link>
          <h1 className="disp" style={{ margin: "8px 0 0", fontSize: 36 }}>Board &amp; resolutions</h1>
        </div>
        <div style={{ display: "flex", gap: 10, flexWrap: "wrap" }}>
          <button type="button" className="btn" onClick={() => setAddRes(!addRes)}>+ Draft resolution</button>
          <button type="button" className="btn primary" onClick={() => setAddMeeting(!addMeeting)}>+ Schedule meeting</button>
        </div>
      </div>

      <ErrorNote error={meetings.error ?? resolutions.error ?? error} />
      <section aria-label="Key numbers" className="kpis">
        <Tile label="Meetings" value={String(ms.length)} note={`${ms.filter((m) => m.held_on).length} held`} />
        <Tile label="Open resolutions" value={String(open)} note="Draft or circulated" warn={open > 0} />
        <Tile label="MGT-14 filings raised" value={String(mgt)} note="From passed special resolutions" />
      </section>

      {addMeeting && id && <ScheduleMeeting entityId={id} onDone={() => { setAddMeeting(false); reload(); }} onCancel={() => setAddMeeting(false)} />}
      {addRes && id && <DraftResolution entityId={id} meetings={ms} suggested={nextResolutionNumber(rs.map((r) => r.number), new Date().getFullYear())} nameOf={meetingName} onDone={() => { setAddRes(false); reload(); }} onCancel={() => setAddRes(false)} />}

      <div className="cols">
        <div className="col-main">
          <section className="card" style={{ overflow: "hidden" }}>
            <div style={{ padding: "14px 20px", borderBottom: "1px solid var(--line-2)" }}><h2 style={{ margin: 0 }}>Meetings</h2></div>
            {meetings.loading ? <Loading what="meetings" /> : ms.length === 0 ? <div className="empty">No meetings yet. Schedule the first one.</div> : ms.map((m) => (
              <MeetingRow key={m.id} m={m} entityId={id!} today={today} onChange={reload} onError={setError} />
            ))}
          </section>
        </div>
        <aside className="col-side">
          <section className="card card-pad" style={{ display: "flex", flexDirection: "column", gap: 8 }}>
            <h2 style={{ margin: 0 }}>Resolutions</h2>
            {resolutions.loading && <Loading what="resolutions" />}
            {!resolutions.loading && rs.length === 0 && <span className="t13">No resolutions yet.</span>}
            {rs.map((r) => <ResolutionRow key={r.id} r={r} entityId={id!} where={meetingName(r.meeting_id)} onChange={reload} onError={setError} />)}
          </section>
        </aside>
      </div>
    </main>
  );
}

function MeetingRow({ m, entityId, today, onChange, onError }: { m: Meeting; entityId: string; today: string; onChange: () => void; onError: (e: unknown) => void }) {
  const [marking, setMarking] = useState(false);
  const [held, setHeld] = useState(today);
  const [quorum, setQuorum] = useState(true);
  const [busy, setBusy] = useState(false);
  const st = meetingState(m, today);
  const problem = heldProblem(held, today);

  async function save() {
    setBusy(true); onError(null);
    try {
      unwrap(await api.PATCH("/entities/{entityId}/meetings/{id}", { params: { path: { entityId, id: m.id }, header: { "If-Match": "*" } }, body: { held_on: held, quorum_met: quorum } as never }));
      setMarking(false); onChange();
    } catch (e) { onError(e); }
    setBusy(false);
  }
  return (
    <div style={{ padding: "14px 20px", borderTop: "1px solid var(--line-2)", display: "flex", flexDirection: "column", gap: 8 }}>
      <div style={{ display: "flex", justifyContent: "space-between", gap: 12, flexWrap: "wrap" }}>
        <span style={{ fontWeight: 600 }}>{MEETING_LABEL[m.kind] ?? m.kind}<span className="t13" style={{ display: "block", fontWeight: 400 }}>{shortDate(istDate(m.scheduled_for))} · {istTime(m.scheduled_for)}{m.held_on && m.held_on !== istDate(m.scheduled_for) ? ` · held ${shortDate(m.held_on)}` : ""}</span></span>
        <span style={{ display: "flex", gap: 10, alignItems: "center" }}>
          <Pill tone={st.tone}>{st.label}</Pill>
          {!m.held_on && <button type="button" className="linkbtn" style={{ color: "var(--cobalt)" }} onClick={() => setMarking(!marking)}>Mark as held</button>}
        </span>
      </div>
      {gapWarning(m) && <span className="t13" role="note" style={{ color: "var(--amber)" }}>{m.days_since_last_board} days since the previous board meeting. The Companies Act expects no more than {BOARD_GAP_DAYS} days between them; this figure is a starter value for your company secretary to confirm.</span>}
      {m.resolutions.length > 0 && <span className="t13">{m.resolutions.length} {m.resolutions.length === 1 ? "resolution" : "resolutions"}: {m.resolutions.map((r) => r.number).join(", ")}</span>}
      {marking && (
        <div style={{ display: "flex", gap: 12, flexWrap: "wrap", alignItems: "flex-end" }}>
          <div className="field"><label htmlFor={`held-${m.id}`}>Held on</label><input id={`held-${m.id}`} type="date" className="input" max={today} value={held} onChange={(e) => setHeld(e.target.value)} /></div>
          <label style={{ display: "flex", gap: 6, fontSize: 14 }}><input type="checkbox" checked={quorum} onChange={(e) => setQuorum(e.target.checked)} />Quorum was present</label>
          <button type="button" className="btn primary" disabled={busy || !!problem} onClick={() => void save()}>{busy ? "Saving…" : "Save"}</button>
        </div>
      )}
    </div>
  );
}

function ResolutionRow({ r, entityId, where, onChange, onError }: { r: Resolution; entityId: string; where: string | null; onChange: () => void; onError: (e: unknown) => void }) {
  const [busy, setBusy] = useState(false);
  const st = RES_STATUS[r.status] ?? { label: r.status, tone: "prep" as const };
  async function move(status: string) {
    setBusy(true); onError(null);
    try { unwrap(await api.PATCH("/entities/{entityId}/resolutions/{id}", { params: { path: { entityId, id: r.id }, header: { "If-Match": "*" } }, body: { status } as never })); onChange(); }
    catch (e) { onError(e); }
    setBusy(false);
  }
  return (
    <div style={{ borderTop: "1px solid var(--line-2)", paddingTop: 8, display: "flex", flexDirection: "column", gap: 6, fontSize: 14 }}>
      <div style={{ display: "flex", justifyContent: "space-between", gap: 8 }}><span><span className="mono">{r.number}</span> · {r.title}<span className="t13" style={{ display: "block" }}>{RES_KINDS.find(([k]) => k === r.kind)?.[1] ?? r.kind}{where ? ` · ${where}` : ""}{r.passed_on ? ` · passed ${shortDate(r.passed_on)}` : ""}{r.needs_mgt14 ? " · needs MGT-14" : ""}</span></span><Pill tone={st.tone}>{st.label}</Pill></div>
      {resolutionActions(r.status).length > 0 && (
        <span style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
          {resolutionActions(r.status).map(([s, l]) => <button key={s} type="button" className="btn" style={{ height: 32 }} disabled={busy} onClick={() => void move(s)}>{l}</button>)}
        </span>
      )}
    </div>
  );
}

function ScheduleMeeting({ entityId, onDone, onCancel }: { entityId: string; onDone: () => void; onCancel: () => void }) {
  const [f, setF] = useState<MeetingForm>(blankMeeting());
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  async function save() {
    setBusy(true); setError(null);
    try { unwrap(await api.POST("/entities/{entityId}/meetings", { params: { path: { entityId } }, body: meetingBody(f) as never })); onDone(); }
    catch (e) { setError(e); setBusy(false); }
  }
  return (
    <form className="card card-pad" style={{ display: "flex", flexDirection: "column", gap: 12 }} onSubmit={(e) => { e.preventDefault(); if (meetingReady(f) && !busy) void save(); }}>
      <h2 style={{ margin: 0 }}>Schedule a meeting</h2>
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(200px, 1fr))", gap: 12 }}>
        <div className="field"><label htmlFor="m-kind">Kind</label><select id="m-kind" className="input" value={f.kind} onChange={(e) => setF({ ...f, kind: e.target.value })}>{MEETING_KINDS.map(([k, l]) => <option key={k} value={k}>{l}</option>)}</select></div>
        <div className="field"><label htmlFor="m-date">Date</label><input id="m-date" type="date" className="input" value={f.date} onChange={(e) => setF({ ...f, date: e.target.value })} /></div>
        <div className="field"><label htmlFor="m-time">Time (IST)</label><input id="m-time" type="time" className="input" value={f.time} onChange={(e) => setF({ ...f, time: e.target.value })} /></div>
      </div>
      <ErrorNote error={error} />
      <span style={{ display: "flex", gap: 10 }}><button type="submit" className="btn primary" disabled={!meetingReady(f) || busy}>{busy ? "Saving…" : "Schedule"}</button><button type="button" className="btn" onClick={onCancel}>Cancel</button></span>
    </form>
  );
}

function DraftResolution({ entityId, meetings, suggested, nameOf, onDone, onCancel }: { entityId: string; meetings: Meeting[]; suggested: string; nameOf: (id: string | null) => string | null; onDone: () => void; onCancel: () => void }) {
  const [f, setF] = useState<ResolutionForm>({ ...blankResolution(), number: suggested });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  async function save() {
    setBusy(true); setError(null);
    try { unwrap(await api.POST("/entities/{entityId}/resolutions", { params: { path: { entityId } }, body: resolutionBody(f) as never })); onDone(); }
    catch (e) { setError(e); setBusy(false); }
  }
  return (
    <form className="card card-pad" style={{ display: "flex", flexDirection: "column", gap: 12 }} onSubmit={(e) => { e.preventDefault(); if (resolutionReady(f) && !busy) void save(); }}>
      <h2 style={{ margin: 0 }}>Draft a resolution</h2>
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(200px, 1fr))", gap: 12 }}>
        <div className="field"><label htmlFor="r-number">Number</label><input id="r-number" className="input mono" value={f.number} onChange={(e) => setF({ ...f, number: e.target.value })} /></div>
        <div className="field"><label htmlFor="r-kind">Kind</label><select id="r-kind" className="input" value={f.kind} onChange={(e) => setF({ ...f, kind: e.target.value })}>{RES_KINDS.map(([k, l]) => <option key={k} value={k}>{l}</option>)}</select></div>
        <div className="field"><label htmlFor="r-meeting">Meeting</label><select id="r-meeting" className="input" value={f.meetingId} onChange={(e) => setF({ ...f, meetingId: e.target.value })}><option value="">None (by circulation)</option>{meetings.map((m) => <option key={m.id} value={m.id}>{nameOf(m.id)}</option>)}</select></div>
      </div>
      <div className="field"><label htmlFor="r-title">Resolved that</label><input id="r-title" className="input" value={f.title} onChange={(e) => setF({ ...f, title: e.target.value })} /></div>
      <span className="t13">A special resolution raises an MGT-14 filing once it is passed. A resolution can only be passed after its meeting is marked held with quorum.</span>
      <ErrorNote error={error} />
      <span style={{ display: "flex", gap: 10 }}><button type="submit" className="btn primary" disabled={!resolutionReady(f) || busy}>{busy ? "Saving…" : "Save draft"}</button><button type="button" className="btn" onClick={onCancel}>Cancel</button></span>
    </form>
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
