"use client";
import { useState } from "react";
import { api, unwrap } from "@/lib/api";
import { formatInr, isoDate, shortDate } from "@/lib/format";
import { useLoad } from "@/lib/hooks";
import {
  AUTHORITIES, AUTHORITY_LABEL, NOTICE_STATUS, Notice, NoticeForm, blankNotice, canDraft, deskTotals, noticeBody, noticeMoves, noticeProblem, replyState,
} from "@/lib/notices";
import { Doc, fileSize } from "@/lib/legal";
import { sameOriginUrl } from "@/lib/vault";
import { useSession } from "@/lib/session";
import { JobFollow } from "@/components/JobFollow";
import { ErrorNote, Loading, Pill } from "@/components/ui";

export default function Notices() {
  const { entity } = useSession();
  const id = entity?.id ?? null;
  const [adding, setAdding] = useState(false);
  const [openId, setOpenId] = useState<string | null>(null);
  const [error, setError] = useState<unknown>(null);

  const list = useLoad(async () => unwrap(await api.GET("/entities/{entityId}/notices", { params: { path: { entityId: id! }, query: { limit: 200 } } })).data as unknown as Notice[], id);
  const rows = list.data ?? [];
  const current = rows.find((n) => n.id === openId) ?? rows[0];
  const t = deskTotals(rows);

  if (!entity) return <main className="page"><div className="card card-pad">Select a workspace first.</div></main>;
  return (
    <main className="page">
      <div style={{ display: "flex", flexWrap: "wrap", gap: 16, justifyContent: "space-between", alignItems: "flex-end" }}>
        <div>
          <span className="muted" style={{ fontSize: 14 }}>{entity.legal_name}</span>
          <h1 className="disp" style={{ margin: "6px 0 0", fontSize: 36 }}>Notice desk</h1>
        </div>
        <button type="button" className="btn primary" onClick={() => setAdding(!adding)}>+ Record a notice</button>
      </div>

      <ErrorNote error={list.error ?? error} />
      <section aria-label="Key numbers" className="kpis">
        <Tile label="Open notices" value={String(t.open)} note="Not yet closed" />
        <Tile label="Replies due this week" value={String(t.dueSoon)} note="Within 7 days" warn={t.dueSoon > 0} />
        <Tile label="Replies overdue" value={String(t.overdue)} note="Past the reply date" warn={t.overdue > 0} />
        <Tile label="Demand outstanding" value={formatInr(t.demand, { decimals: false })} note="On open notices" />
      </section>

      {adding && <RecordNotice entityId={id!} onDone={(nid) => { setAdding(false); setOpenId(nid); list.reload(); }} onCancel={() => setAdding(false)} />}

      <div className="cols">
        <div className="col-main">
          <section className="card" style={{ overflow: "hidden" }}>
            <div style={{ padding: "14px 20px", borderBottom: "1px solid var(--line-2)" }}><h2 style={{ margin: 0 }}>Notices</h2></div>
            {list.loading ? <Loading what="notices" /> : (
              <div className="tablewrap">
                <table style={{ minWidth: 720 }}>
                  <thead><tr><th scope="col">Notice</th><th scope="col">Period</th><th scope="col">Reply</th><th scope="col" style={{ textAlign: "right" }}>Demand</th><th scope="col">Status</th></tr></thead>
                  <tbody>
                    {rows.map((n) => {
                      const st = NOTICE_STATUS[n.status] ?? { label: n.status, tone: "prep" as const };
                      const rs = replyState(n);
                      return (
                        <tr key={n.id} className="trow" onClick={() => setOpenId(n.id)} style={current?.id === n.id ? { background: "var(--bg-2, #FAFAF7)" } : undefined}>
                          <td><button type="button" style={{ all: "unset", cursor: "pointer", fontWeight: 600 }} onClick={() => setOpenId(n.id)}>{AUTHORITY_LABEL[n.authority_code] ?? n.authority_code} · {n.form_code}</button><span className="t13" style={{ display: "block" }}>{n.reference_no ?? "No reference"} · received {shortDate(n.received_on)}</span></td>
                          <td className="t13">{n.period_label ?? "–"}</td>
                          <td>{n.reply_due_on ? <><span className="mono" style={{ fontSize: 13 }}>{shortDate(n.reply_due_on)}</span>{rs && <span className="t13" style={{ display: "block", color: rs.tone === "due" ? "var(--amber)" : undefined }}>{rs.label}</span>}</> : <span className="t13">–</span>}</td>
                          <td className="mono" style={{ textAlign: "right" }}>{n.demand_amount ? formatInr(n.demand_amount, { decimals: false }) : "–"}</td>
                          <td><Pill tone={st.tone}>{st.label}</Pill></td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
                {rows.length === 0 && <div className="empty">No notices. Record one when it arrives and the reply date becomes a task.</div>}
              </div>
            )}
          </section>
        </div>

        <aside className="col-side">
          {current && id && <Detail key={current.id} entityId={id} n={current} onChange={list.reload} onError={setError} />}
        </aside>
      </div>
    </main>
  );
}

function Detail({ entityId, n, onChange, onError }: { entityId: string; n: Notice; onChange: () => void; onError: (e: unknown) => void }) {
  const [job, setJob] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const docs = useLoad(async () => unwrap(await api.GET("/entities/{entityId}/documents", { params: { path: { entityId }, query: { subject: "notices", subject_id: n.id, limit: 50 } } })).data as unknown as Doc[], `${entityId}|${n.id}|${n.document_ids.length}`);
  const st = NOTICE_STATUS[n.status] ?? { label: n.status, tone: "prep" as const };

  async function move(status: string) {
    setBusy(true); onError(null);
    try { unwrap(await api.PATCH("/entities/{entityId}/notices/{id}", { params: { path: { entityId, id: n.id }, header: { "If-Match": "*" } }, body: { status } as never })); onChange(); }
    catch (e) { onError(e); }
    setBusy(false);
  }
  async function draft() {
    setBusy(true); onError(null);
    try { const j = unwrap(await api.POST("/entities/{entityId}/notices/{id}/draft-reply", { params: { path: { entityId, id: n.id } } })) as unknown as { id: string }; setJob(j.id); }
    catch (e) { onError(e); }
    setBusy(false);
  }

  return (
    <section className="card card-pad" style={{ display: "flex", flexDirection: "column", gap: 10 }}>
      <span className="t13">{AUTHORITY_LABEL[n.authority_code] ?? n.authority_code}</span>
      <div style={{ display: "flex", justifyContent: "space-between", gap: 8, alignItems: "center" }}><h2 style={{ margin: 0 }}>{n.form_code}</h2><Pill tone={st.tone}>{st.label}</Pill></div>
      <dl style={{ margin: 0, display: "grid", gridTemplateColumns: "auto 1fr", gap: "4px 12px", fontSize: 14 }}>
        <dt className="muted">Received</dt><dd style={{ margin: 0, textAlign: "right" }}>{shortDate(n.received_on)}</dd>
        {n.reply_due_on && <><dt className="muted">Reply due</dt><dd style={{ margin: 0, textAlign: "right" }}>{shortDate(n.reply_due_on)}</dd></>}
        {n.hearing_on && <><dt className="muted">Hearing</dt><dd style={{ margin: 0, textAlign: "right" }}>{shortDate(n.hearing_on)}</dd></>}
        {n.demand_amount && <><dt className="muted">Demand</dt><dd className="mono" style={{ margin: 0, textAlign: "right" }}>{formatInr(n.demand_amount)}</dd></>}
      </dl>
      {n.summary && <p style={{ margin: 0, fontSize: 14 }}>{n.summary}</p>}

      {canDraft(n) && (
        <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
          <button type="button" className="btn" disabled={busy || !!job} onClick={() => void draft()}>Draft a reply</button>
          {job && <JobFollow jobId={job} doneLabel="Draft ready" onFinished={() => { setJob(null); onChange(); docs.reload(); }} />}
          <span className="t13">The draft is built from your own records for the period and saved to the vault. Have your adviser review it before it is filed.</span>
        </div>
      )}

      <div style={{ borderTop: "1px solid var(--line-2)", paddingTop: 8 }}>
        <span className="t13">DOCUMENTS</span>
        <ErrorNote error={docs.error} />
        {!docs.loading && (docs.data ?? []).length === 0 && <span className="t13" style={{ display: "block" }}>Nothing attached yet.</span>}
        {(docs.data ?? []).map((d) => (
          <div key={d.id} style={{ fontSize: 14, paddingTop: 6 }}>
            {d.download_url ? <a href={sameOriginUrl(d.download_url) ?? d.download_url}>{d.title}</a> : d.title}
            <span className="t13" style={{ display: "block" }}>{d.file_name} · {fileSize(d.size_bytes)}{d.links.some((l) => l.role === "draft") ? " · draft" : ""}</span>
          </div>
        ))}
      </div>

      {noticeMoves(n.status).length > 0 && (
        <div style={{ display: "flex", gap: 8, flexWrap: "wrap", borderTop: "1px solid var(--line-2)", paddingTop: 10 }}>
          {noticeMoves(n.status).map(([s, l]) => <button key={s} type="button" className="btn" style={{ height: 34 }} disabled={busy} onClick={() => void move(s)}>{l}</button>)}
        </div>
      )}
    </section>
  );
}

function RecordNotice({ entityId, onDone, onCancel }: { entityId: string; onDone: (id: string) => void; onCancel: () => void }) {
  const [f, setF] = useState<NoticeForm>(blankNotice(isoDate(new Date())));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [touched, setTouched] = useState(false);
  const problem = noticeProblem(f);
  const set = (patch: Partial<NoticeForm>) => setF({ ...f, ...patch });
  const text = (key: keyof NoticeForm, label: string, extra: { type?: string; mono?: boolean; placeholder?: string } = {}) => (
    <div className="field"><label htmlFor={`n-${key}`}>{label}</label><input id={`n-${key}`} type={extra.type ?? "text"} className={`input${extra.mono ? " mono" : ""}`} placeholder={extra.placeholder} value={f[key]} onChange={(e) => set({ [key]: e.target.value })} /></div>
  );
  async function save() {
    setTouched(true);
    if (problem) return;
    setBusy(true); setError(null);
    try { const n = unwrap(await api.POST("/entities/{entityId}/notices", { params: { path: { entityId } }, body: noticeBody(f) as never })) as unknown as { id: string }; onDone(n.id); }
    catch (e) { setError(e); setBusy(false); }
  }
  return (
    <form className="card card-pad" style={{ display: "flex", flexDirection: "column", gap: 14 }} onSubmit={(e) => { e.preventDefault(); void save(); }}>
      <h2 style={{ margin: 0 }}>Record a notice</h2>
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(210px, 1fr))", gap: 14 }}>
        <div className="field"><label htmlFor="n-authority">From</label><select id="n-authority" className="input" value={f.authority} onChange={(e) => set({ authority: e.target.value })}>{AUTHORITIES.map(([k, l]) => <option key={k} value={k}>{l}</option>)}</select></div>
        {text("form", "Form or notice code", { mono: true, placeholder: "ASMT-10" })}
        {text("reference", "Reference number", { mono: true })}
        {text("period", "Period it covers", { placeholder: "FY 2024-25" })}
        {text("received", "Received on", { type: "date" })}
        {text("replyDue", "Reply due on", { type: "date" })}
        {text("hearing", "Hearing on", { type: "date" })}
        {text("demand", "Demand (₹)", { mono: true })}
      </div>
      <div className="field"><label htmlFor="n-summary">What it says</label><textarea id="n-summary" className="input" rows={3} style={{ height: "auto", padding: 12 }} value={f.summary} onChange={(e) => set({ summary: e.target.value })} /></div>
      <span className="t13">A reply date and a hearing date each become a task, so they show up on Today.</span>
      {touched && problem && <span className="t13" role="alert" style={{ color: "var(--amber)" }}>{problem}</span>}
      <ErrorNote error={error} />
      <span style={{ display: "flex", gap: 10 }}><button type="submit" className="btn primary" disabled={busy}>{busy ? "Saving…" : "Save notice"}</button><button type="button" className="btn" onClick={onCancel}>Cancel</button></span>
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
