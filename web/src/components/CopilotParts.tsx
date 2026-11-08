"use client";
import { useEffect, useState } from "react";
import { api, newIdempotencyKey, unwrap } from "@/lib/api";
import { formatInr } from "@/lib/format";
import { ACTION_STATE, CopilotAction, CopilotMessage, actionItems, actionResult, actionState, actionTitle, answerBlocks, canRequestApproval } from "@/lib/copilot";
import { Member } from "@/lib/settings";
import { ErrorNote, Pill } from "./ui";

/** An answer: paragraphs, bullet lines, and [n] markers as buttons that select the source on the right. */
export function AnswerText({ message, selected, onCite }: { message: CopilotMessage; selected: number | null; onCite: (n: number) => void }) {
  const blocks = answerBlocks(message.content, message.citations.length);
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 8, fontSize: 15, lineHeight: 1.6 }}>
      {blocks.map((b, i) => (
        <p key={i} style={{ margin: 0, paddingLeft: b.bullet ? 16 : 0, position: "relative" }}>
          {b.bullet && <span aria-hidden="true" style={{ position: "absolute", left: 2 }}>•</span>}
          {b.pieces.map((p, j) => p.type === "text" ? <span key={j}>{p.text}</span> : (
            <button key={j} type="button" className="cite" aria-label={`Source ${p.n}`} aria-pressed={selected === p.n} onClick={() => onCite(p.n)}
                    style={{ margin: "0 2px", minWidth: 20, height: 20, borderRadius: 6, border: 0, fontSize: 11, fontWeight: 600, cursor: "pointer", background: selected === p.n ? "#2846E0" : "#E8ECFF", color: selected === p.n ? "#fff" : "#1A31A8" }}>{p.n}</button>
          ))}
        </p>
      ))}
    </div>
  );
}

/** A thing the copilot suggests doing. It does nothing by itself: a person sends it for approval and someone else approves it. */
export function ActionCard({ entityId, meId, action, onChanged }: { entityId: string; meId: string | undefined; action: CopilotAction; onChanged: () => void }) {
  const st = actionState(action);
  const items = actionItems(action, (v) => formatInr(v));
  const result = actionResult(action);
  const [asking, setAsking] = useState(false);
  const [approvers, setApprovers] = useState<Member[] | null>(null);
  const [to, setTo] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);

  useEffect(() => {
    if (!asking || approvers !== null) return;
    (async () => {
      try {
        const rows = unwrap(await api.GET("/entities/{entityId}/members", { params: { path: { entityId }, query: { limit: 100 } } })).data as unknown as Member[];
        setApprovers(rows.filter((m) => (m.role === "owner" || m.role === "approver") && m.accepted_at && m.user_id !== meId));
      } catch { setApprovers([]); }       // without permission to list people, send it to whoever can approve
    })();
  }, [asking, approvers, entityId, meId]);

  async function send() {
    setBusy(true); setError(null);
    try {
      unwrap(await api.POST("/entities/{entityId}/copilot/actions/{id}/request-approval", { params: { path: { entityId, id: action.id }, header: { "Idempotency-Key": newIdempotencyKey() } }, body: (to ? { assigned_to: to } : {}) as never }));
      setAsking(false); onChanged();
    } catch (e) { setError(e); }
    setBusy(false);
  }

  return (
    <div style={{ border: "1px solid var(--ink)", borderRadius: 14, overflow: "hidden" }}>
      <div style={{ padding: "12px 16px", display: "flex", flexWrap: "wrap", justifyContent: "space-between", gap: 10, alignItems: "center", background: "var(--paper, #FAFAF7)", borderBottom: "1px solid var(--line)" }}>
        <span style={{ fontSize: 14, fontWeight: 600 }}>Proposed action · {actionTitle(action)}</span>
        <Pill tone={st.tone}>{st.label}</Pill>
      </div>
      {items.map((s) => <div key={s.name} style={{ display: "flex", justifyContent: "space-between", gap: 10, padding: "9px 16px", borderTop: "1px solid var(--line-2)", fontSize: 14 }}><span>{s.name}</span><span className="mono" style={{ color: "var(--muted, #5A5F68)" }}>{s.detail}</span></div>)}
      <div style={{ padding: "12px 16px", display: "flex", flexDirection: "column", gap: 8, borderTop: "1px solid var(--line-2)" }}>
        {result && <span className="t13" role="status">{result}</span>}
        {action.status === "proposed" && action.approval_request_id && (
          <span style={{ display: "flex", gap: 10, flexWrap: "wrap", alignItems: "center" }}>
            <span className="t13">Someone else has to approve it from their Today screen. Nothing runs until they do.</span>
            <button type="button" className="linkbtn" style={{ color: "var(--cobalt)" }} onClick={onChanged}>Check again</button>
          </span>
        )}
        {canRequestApproval(action) && !asking && (
          <span style={{ display: "flex", gap: 10, flexWrap: "wrap", alignItems: "center" }}>
            <button type="button" className="btn primary" onClick={() => setAsking(true)}>Send for approval</button>
            <span className="t13">Nothing happens until a different person approves. Logged to the audit trail.</span>
          </span>
        )}
        {asking && (
          <span style={{ display: "flex", gap: 10, flexWrap: "wrap", alignItems: "flex-end" }}>
            <div className="field"><label htmlFor={`ap-${action.id}`}>Ask</label>
              <select id={`ap-${action.id}`} className="input" style={{ height: 40 }} value={to} onChange={(e) => setTo(e.target.value)} disabled={approvers === null}>
                <option value="">Anyone who can approve</option>
                {(approvers ?? []).map((m) => <option key={m.user_id} value={m.user_id}>{m.full_name} ({m.role})</option>)}
              </select></div>
            <button type="button" className="btn primary" disabled={busy} onClick={() => void send()}>{busy ? "Sending…" : "Send"}</button>
            <button type="button" className="btn" onClick={() => setAsking(false)}>Cancel</button>
          </span>
        )}
        <ErrorNote error={error} />
      </div>
    </div>
  );
}

export { ACTION_STATE };
