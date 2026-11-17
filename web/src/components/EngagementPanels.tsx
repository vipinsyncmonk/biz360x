"use client";
import { useState } from "react";
import { ApiProblem, api, unwrap } from "@/lib/api";
import {
  ClientStaff, EngageForm, GRANT_LEVELS, GRANT_MODULES, MyEngagements, SERVICES, answerProblem, applyEngagePreset, blankEngage, engageBody, engageProblem, inviteLine, isAudit, staffLine, staffOnly,
  toggleStaff,
} from "@/lib/engagements";
import { isoDate, shortDate } from "@/lib/format";
import { useLoad } from "@/lib/hooks";
import type { Presets } from "@/lib/roles";
import { ErrorNote, Loading, Pill } from "./ui";

/** A company engages a practitioner: who, for what, and what they may do. The person is invited and access starts when they accept. */
export function EngagePractitioner({ entityId, presets, onDone, onCancel }: { entityId: string; presets: Presets | null; onDone: () => void; onCancel: () => void }) {
  const [f, setF] = useState<EngageForm>(blankEngage);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const today = isoDate(new Date());
  const choices = (presets?.presets ?? []).filter((p) => p.kind === "practitioner");
  const preset = choices.find((p) => p.key === f.preset);
  const problem = engageProblem(f, today);
  const audit = isAudit(f.service);
  const levels = audit ? GRANT_LEVELS.filter(([k]) => k === "read" || k === "comment") : GRANT_LEVELS;

  async function save() {
    setBusy(true); setError(null);
    try { unwrap(await api.POST("/entities/{entityId}/advisor-grants", { params: { path: { entityId } }, body: engageBody(f, preset) as never })); onDone(); }
    catch (e) { setError(e); setBusy(false); }
  }

  return (
    <form className="card-pad" aria-label="Engage a practitioner" style={{ border: "1px solid var(--line)", borderRadius: 12, background: "var(--surface-2)", display: "flex", flexDirection: "column", gap: 12 }}
          onSubmit={(e) => { e.preventDefault(); if (!problem && !busy) void save(); }}>
      <h3 style={{ margin: 0, fontSize: 15 }}>Engage a practitioner</h3>
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(220px, 1fr))", gap: 12 }}>
        <div className="field"><label htmlFor="eg-name">Practitioner or firm</label><input id="eg-name" className="input" autoComplete="off" value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} /></div>
        <div className="field"><label htmlFor="eg-email">Their email</label><input id="eg-email" type="email" className="input" autoComplete="off" value={f.email} onChange={(e) => setF({ ...f, email: e.target.value })} /></div>
        <div className="field"><label htmlFor="eg-preset">Start from</label>
          <select id="eg-preset" className="input" value={f.preset} onChange={(e) => setF(applyEngagePreset(f, choices.find((p) => p.key === e.target.value)))}>
            <option value="">Custom</option>{choices.map((p) => <option key={p.key} value={p.key}>{p.title}</option>)}
          </select></div>
        <div className="field"><label htmlFor="eg-service">Engaged for</label>
          <select id="eg-service" className="input" value={f.service} onChange={(e) => setF({ ...f, service: e.target.value })}>
            <option value="">Choose…</option>{SERVICES.map(([k, l]) => <option key={k} value={k}>{l}</option>)}
          </select></div>
        <div className="field"><label htmlFor="eg-fy">Financial year (optional)</label><input id="eg-fy" className="input mono" autoComplete="off" placeholder="2026-27" value={f.fy} onChange={(e) => setF({ ...f, fy: e.target.value })} /></div>
        <div className="field"><label htmlFor="eg-exp">{audit ? "Access ends" : "Access ends (optional)"}</label><input id="eg-exp" type="date" className="input" min={today} value={f.expires} onChange={(e) => setF({ ...f, expires: e.target.value })} /></div>
        <div className="field"><label htmlFor="eg-mem">Membership no. (optional)</label><input id="eg-mem" className="input mono" autoComplete="off" maxLength={20} value={f.membershipNo} onChange={(e) => setF({ ...f, membershipNo: e.target.value })} /></div>
      </div>
      {preset && <span className="t13">{preset.about}</span>}
      <fieldset style={{ border: 0, padding: 0, margin: 0 }}>
        <legend className="t13">What they may do, by module{audit ? " (an auditor can only view and raise queries)" : ""}</legend>
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(240px, 1fr))", gap: 8 }}>
          {GRANT_MODULES.map(([k, l]) => (
            <label key={k} style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 8, fontSize: 14 }}>{l}
              <select aria-label={`${l}: practitioner access`} className="input" style={{ height: 36, width: 150, fontSize: 13 }} value={f.levels[k] ?? ""} onChange={(e) => setF({ ...f, levels: { ...f.levels, [k]: e.target.value } })}>
                <option value="">No access</option>{levels.map(([v, lv]) => <option key={v} value={v}>{lv}</option>)}
              </select>
            </label>
          ))}
        </div>
      </fieldset>
      {problem && (f.name || f.email) && <span className="t13" role="alert" style={{ color: "var(--amber)" }}>{problem}</span>}
      <ErrorNote error={error} />
      <span style={{ display: "flex", gap: 10 }}>
        <button type="submit" className="btn primary" disabled={busy || !!problem}>{busy ? "Saving…" : f.email.trim() ? "Send invitation" : "Save record"}</button>
        <button type="button" className="btn" onClick={onCancel}>Cancel</button>
      </span>
      <span className="t13">With an email, the person is invited and sees your company only once they accept, as themselves or for their firm. Nothing is emailed yet, so tell them to sign in. Approving and paying always stay with your team. The membership number is recorded as given, not verified; whether an auditor may also provide another service is a rule to confirm with your CA.</span>
    </form>
  );
}

/** Invitations waiting for the signed-in person: accept as themselves or for a firm they are a partner of, or decline. */
export function Invitations({ onAnswered }: { onAnswered: (entityId: string | null) => void }) {
  const mine = useLoad(async () => unwrap(await api.GET("/me/engagements")) as unknown as MyEngagements, "me");
  const [as, setAs] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState<string | null>(null);
  const [failure, setFailure] = useState<string | null>(null);
  const list = mine.data?.invitations ?? [];
  const firms = mine.data?.firms ?? [];
  if (list.length === 0) return null;

  async function answer(id: string, accept: boolean) {
    setBusy(id); setFailure(null);
    try {
      const path = { path: { id } };
      const out = accept
        ? unwrap(await api.POST("/me/engagements/{id}/accept", { params: path, body: { organization_id: as[id] || null } }))
        : unwrap(await api.POST("/me/engagements/{id}/decline", { params: path }));
      mine.reload();
      onAnswered(accept ? ((out as { entity_id?: string }).entity_id ?? null) : null);
    } catch (e) {
      setFailure(answerProblem(e instanceof ApiProblem ? e.code : undefined, e instanceof Error ? e.message : "Could not answer the invitation"));
    } finally { setBusy(null); }
  }

  return (
    <section className="card" style={{ overflow: "hidden" }} aria-label="Invitations">
      <div className="cardhead"><h2>Invitations</h2><Pill tone="wait">{list.length} waiting</Pill></div>
      {failure && <div style={{ padding: "0 20px" }}><div role="alert" className="error">{failure}</div></div>}
      {list.map((i) => (
        <div key={i.id} style={{ borderTop: "1px solid var(--line-2)", padding: "14px 20px", display: "flex", flexDirection: "column", gap: 10 }}>
          <span><span style={{ fontWeight: 600 }}>{i.entity_name}</span>{i.invited_by_name && <span className="t13"> · invited by {i.invited_by_name}</span>}
            <span className="t13" style={{ display: "block" }}>{inviteLine(i, shortDate)}</span></span>
          <span style={{ display: "flex", gap: 10, flexWrap: "wrap", alignItems: "center" }}>
            {firms.length > 0 && (
              <select aria-label={`Accept the invitation from ${i.entity_name} as`} className="input" style={{ height: 40, fontSize: 14 }} value={as[i.id] ?? ""} onChange={(e) => setAs({ ...as, [i.id]: e.target.value })}>
                <option value="">As myself</option>{firms.map((f) => <option key={f.id} value={f.id}>For {f.name}</option>)}
              </select>
            )}
            <button type="button" className="btn primary" disabled={busy === i.id} aria-label={`Accept the invitation from ${i.entity_name}`} onClick={() => void answer(i.id, true)}>Accept</button>
            <button type="button" className="btn" disabled={busy === i.id} aria-label={`Decline the invitation from ${i.entity_name}`} onClick={() => void answer(i.id, false)}>Decline</button>
          </span>
        </div>
      ))}
    </section>
  );
}

/** A firm's clients and which staff work on each. Partners tick the staff; staff see the list for the clients they have. */
export function ClientStaffPanel({ orgId, canAssign }: { orgId: string; canAssign: boolean }) {
  const loaded = useLoad(async () => unwrap(await api.GET("/organizations/{id}/client-staff", { params: { path: { id: orgId } } })) as unknown as ClientStaff, orgId);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<unknown>(null);
  const d = loaded.data;
  const staff = staffOnly(d?.people ?? []);

  async function set(entityId: string, userIds: string[]) {
    setBusy(entityId); setError(null);
    try { unwrap(await api.PUT("/organizations/{id}/client-staff/{entityId}", { params: { path: { id: orgId, entityId } }, body: { user_ids: userIds } })); loaded.reload(); }
    catch (e) { setError(e); } finally { setBusy(null); }
  }

  return (
    <section className="card" style={{ overflow: "hidden" }} aria-label="Who works on which client">
      <div style={{ padding: "14px 20px", borderBottom: "1px solid var(--line-2)" }}>
        <h2 style={{ margin: 0 }}>Who works on which client</h2>
        <span className="t13">Partners and admins see every client. Staff see only the clients ticked for them, and lose sight of one as soon as they are taken off it.</span>
      </div>
      <div style={{ padding: "0 20px" }}><ErrorNote error={loaded.error ?? error} /></div>
      {!d ? <Loading what="clients" /> : d.clients.length === 0 ? <div className="empty">No clients yet. A client appears here when it engages the firm or the firm sets it up.</div> : d.clients.map((c) => (
        <div key={c.entity_id} style={{ borderTop: "1px solid var(--line-2)", padding: "14px 20px", display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(220px, 1fr))", gap: 12 }}>
          <span style={{ fontWeight: 600 }}>{c.legal_name}<span className="t13" style={{ display: "block", fontWeight: 400 }}>{c.services.length ? c.services.join(", ") : c.set_up_by_firm ? "Set up by the firm" : ""}</span>
            <span className="t13" style={{ display: "block", fontWeight: 400 }}>{staffLine(c, d.people)}</span></span>
          <fieldset style={{ border: 0, padding: 0, margin: 0, display: "flex", gap: 14, flexWrap: "wrap" }} disabled={!canAssign || busy === c.entity_id}>
            <legend className="sr">Staff on {c.legal_name}</legend>
            {staff.map((p) => (
              <label key={p.user_id} style={{ display: "flex", gap: 6, alignItems: "center", fontSize: 14 }}>
                <input type="checkbox" aria-label={`${p.name} works on ${c.legal_name}`} checked={c.staff.includes(p.user_id)} onChange={() => void set(c.entity_id, toggleStaff(c.staff, p.user_id))} />{p.name}
              </label>
            ))}
          </fieldset>
        </div>
      ))}
    </section>
  );
}
