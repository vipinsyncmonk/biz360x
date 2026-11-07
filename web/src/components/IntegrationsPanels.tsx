"use client";
import { useEffect, useState } from "react";
import { api, unwrap, unwrapEmpty } from "@/lib/api";
import { shortDate } from "@/lib/format";
import { useLoad } from "@/lib/hooks";
import {
  ConnForm, Delivery, EVENT_TYPES, Hook, HookForm, Job, PORTAL_KEYS, PortalConn, VIA, blankConn, blankHook, canRenew, connBody, connReady, deliveryState, eventsLabel, hookBody, hookProblem, jobStage, otpReady,
} from "@/lib/integrations";
import { CONN_STATUS, PORTAL_LABEL } from "@/lib/settings";
import { ErrorNote, Loading, Pill } from "./ui";

const toggle = (list: string[], v: string) => (list.includes(v) ? list.filter((x) => x !== v) : [...list, v]);
const sub = { border: "1px solid var(--line)", borderRadius: 12, background: "var(--surface-2)", padding: 16, display: "flex", flexDirection: "column", gap: 12 } as const;

// ---------------------------------------------------------------- portals
export function PortalsSection({ entityId }: { entityId: string }) {
  const conns = useLoad(async () => unwrap(await api.GET("/entities/{entityId}/portal-connections", { params: { path: { entityId } } })).data as unknown as PortalConn[], entityId);
  const [adding, setAdding] = useState(false);
  const [renewing, setRenewing] = useState<string | null>(null);
  const [confirm, setConfirm] = useState<string | null>(null);
  const [error, setError] = useState<unknown>(null);

  async function disconnect(id: string) {
    setError(null);
    try { unwrapEmpty(await api.DELETE("/entities/{entityId}/portal-connections/{id}", { params: { path: { entityId, id } } })); setConfirm(null); conns.reload(); }
    catch (e) { setError(e); setConfirm(null); }
  }

  return (
    <section className="card card-pad" style={{ display: "flex", flexDirection: "column", gap: 8 }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 12 }}><h2 style={{ margin: 0 }}>Connected portals</h2><button type="button" className="btn" onClick={() => setAdding(!adding)}>+ Connect portal</button></div>
      <span className="t13">Credentials are never stored in plain text. Sessions that need an OTP ask the owner to renew.</span>
      <ErrorNote error={conns.error ?? error} />
      {adding && <ConnectPortal entityId={entityId} onDone={() => { setAdding(false); conns.reload(); }} onCancel={() => setAdding(false)} />}
      {conns.loading && <Loading what="portals" />}
      {!conns.loading && (conns.data ?? []).length === 0 && <span className="t13">No portal is connected.</span>}
      {(conns.data ?? []).map((c) => {
        const st = CONN_STATUS[c.status] ?? { label: c.status, tone: "prep" as const };
        return (
          <div key={c.id} style={{ borderTop: "1px solid var(--line-2)", paddingTop: 8, display: "flex", flexDirection: "column", gap: 8 }}>
            <div style={{ display: "flex", justifyContent: "space-between", gap: 12, fontSize: 14 }}>
              <span>{PORTAL_LABEL[c.portal] ?? c.portal}<span className="t13" style={{ display: "block" }}>{c.via ? `${c.via} · ` : ""}{c.last_sync_at ? `Last synced ${shortDate(c.last_sync_at)}` : "Not synced yet"}{c.session_expires_at ? ` · session to ${shortDate(c.session_expires_at)}` : ""}{c.last_error ? ` · ${c.last_error}` : ""}</span></span>
              <span style={{ display: "flex", flexDirection: "column", gap: 6, alignItems: "flex-end" }}>
                <Pill tone={st.tone}>{st.label}</Pill>
                {c.status !== "revoked" && (
                  <span style={{ display: "flex", gap: 10 }}>
                    {canRenew(c) && <button type="button" className="linkbtn" style={{ color: "var(--cobalt)" }} onClick={() => setRenewing(renewing === c.id ? null : c.id)}>Renew session</button>}
                    {confirm === c.id
                      ? <><button type="button" className="btn" style={{ height: 32, color: "var(--amber)" }} onClick={() => void disconnect(c.id)}>Disconnect</button><button type="button" className="linkbtn" style={{ color: "var(--cobalt)" }} onClick={() => setConfirm(null)}>Keep</button></>
                      : <button type="button" className="linkbtn" style={{ color: "var(--cobalt)" }} onClick={() => setConfirm(c.id)}>Disconnect…</button>}
                  </span>
                )}
              </span>
            </div>
            {renewing === c.id && <RenewFlow entityId={entityId} connectionId={c.id} onDone={() => { conns.reload(); }} />}
          </div>
        );
      })}
    </section>
  );
}

function ConnectPortal({ entityId, onDone, onCancel }: { entityId: string; onDone: () => void; onCancel: () => void }) {
  const [f, setF] = useState<ConnForm>(blankConn());
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  async function save() {
    setBusy(true); setError(null);
    try { unwrap(await api.POST("/entities/{entityId}/portal-connections", { params: { path: { entityId } }, body: connBody(f) as never })); onDone(); }
    catch (e) { setError(e); setBusy(false); }
  }
  return (
    <form style={sub} onSubmit={(e) => { e.preventDefault(); if (connReady(f) && !busy) void save(); }}>
      <h3 style={{ margin: 0, fontSize: 15 }}>Connect a portal</h3>
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(200px, 1fr))", gap: 12 }}>
        <div className="field"><label htmlFor="pc-portal">Portal</label><select id="pc-portal" className="input" value={f.portal} onChange={(e) => setF({ ...f, portal: e.target.value })}>{PORTAL_KEYS.map((k) => <option key={k} value={k}>{PORTAL_LABEL[k]}</option>)}</select></div>
        <div className="field"><label htmlFor="pc-via">Connected through</label><select id="pc-via" className="input" value={f.via} onChange={(e) => setF({ ...f, via: e.target.value })}>{VIA.map(([k, l]) => <option key={k} value={k}>{l}</option>)}</select></div>
        <div className="field"><label htmlFor="pc-user">Portal user name</label><input id="pc-user" className="input" autoComplete="off" value={f.username} onChange={(e) => setF({ ...f, username: e.target.value })} /></div>
        <div className="field"><label htmlFor="pc-pass">Portal password</label><input id="pc-pass" type="password" className="input" autoComplete="new-password" value={f.password} onChange={(e) => setF({ ...f, password: e.target.value })} /></div>
      </div>
      <span className="t13">The password goes straight to the secret store and is never shown again. Leave both empty to list the portal now and add credentials later.</span>
      <ErrorNote error={error} />
      <span style={{ display: "flex", gap: 10 }}>
        <button type="submit" className="btn primary" disabled={!connReady(f) || busy}>{busy ? "Saving…" : "Connect"}</button>
        <button type="button" className="btn" onClick={onCancel}>Cancel</button>
      </span>
    </form>
  );
}

/** Start an OTP renewal, follow the job, and ask for the code when the portal does. */
function RenewFlow({ entityId, connectionId, onDone }: { entityId: string; connectionId: string; onDone: () => void }) {
  const [job, setJob] = useState<Job | null>(null);
  const [otp, setOtp] = useState("");
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  const [polling, setPolling] = useState(true);
  const stage = job ? jobStage(job) : null;

  useEffect(() => {
    let live = true;
    (async () => {
      try {
        const j = unwrap(await api.POST("/entities/{entityId}/portal-connections/{id}/renew", { params: { path: { entityId, id: connectionId } } })) as unknown as Job;
        if (live) setJob(j);
      } catch (e) { if (live) { setError(e); setPolling(false); } }
    })();
    return () => { live = false; };
  }, [entityId, connectionId]);

  useEffect(() => {
    if (!job || !polling) return;
    const s = jobStage(job);
    if (s.finished) { setPolling(false); onDone(); return; }
    if (s.needsOtp) return;
    const t = setTimeout(async () => {
      try { setJob(unwrap(await api.GET("/jobs/{id}", { params: { path: { id: job.id } } })) as unknown as Job); } catch (e) { setError(e); setPolling(false); }
    }, 1500);
    return () => clearTimeout(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [job, polling]);

  async function send() {
    if (!job) return;
    setBusy(true); setError(null);
    try { setJob(unwrap(await api.POST("/jobs/{id}/otp", { params: { path: { id: job.id } }, body: { otp: otp.trim() } })) as unknown as Job); setOtp(""); }
    catch (e) { setError(e); }
    setBusy(false);
  }

  return (
    <div style={sub} role="status">
      {!job && !error && <Loading what="the renewal" />}
      {stage && <span style={{ display: "flex", gap: 8, alignItems: "center", fontSize: 14 }}><Pill tone={stage.tone}>{stage.label}</Pill></span>}
      {stage?.needsOtp && (
        <form style={{ display: "flex", gap: 10, alignItems: "flex-end", flexWrap: "wrap" }} onSubmit={(e) => { e.preventDefault(); if (otpReady(otp) && !busy) void send(); }}>
          <div className="field"><label htmlFor={`otp-${connectionId}`}>6-digit code sent by the portal</label><input id={`otp-${connectionId}`} className="input mono" inputMode="numeric" maxLength={6} style={{ width: 140 }} value={otp} onChange={(e) => setOtp(e.target.value.replace(/\D/g, ""))} /></div>
          <button type="submit" className="btn primary" disabled={!otpReady(otp) || busy}>{busy ? "Sending…" : "Submit code"}</button>
        </form>
      )}
      <ErrorNote error={error} />
    </div>
  );
}

// ---------------------------------------------------------------- webhooks
export function WebhooksSection({ entityId }: { entityId: string }) {
  const hooks = useLoad(async () => unwrap(await api.GET("/entities/{entityId}/webhooks", { params: { path: { entityId } } })).data as unknown as Hook[], entityId);
  const [adding, setAdding] = useState(false);
  const [secret, setSecret] = useState<string | null>(null);
  const [open, setOpen] = useState<string | null>(null);
  const [confirm, setConfirm] = useState<string | null>(null);
  const [note, setNote] = useState("");
  const [error, setError] = useState<unknown>(null);

  async function run(fn: () => Promise<void>, message = "") {
    setError(null); setNote("");
    try { await fn(); if (message) setNote(message); } catch (e) { setError(e); }
  }
  return (
    <section className="card card-pad" style={{ display: "flex", flexDirection: "column", gap: 8 }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 12 }}><h2 style={{ margin: 0 }}>Webhooks</h2><button type="button" className="btn" onClick={() => { setAdding(!adding); setSecret(null); }}>+ Add endpoint</button></div>
      <span className="t13">We post signed events to your address as they happen. Failed deliveries are retried.</span>
      <ErrorNote error={hooks.error ?? error} />
      {note && <span className="t13" role="status">{note}</span>}
      {secret && (
        <div role="status" style={{ background: "var(--green-soft)", borderRadius: 12, padding: 12, display: "flex", flexDirection: "column", gap: 6 }}>
          <strong>Copy the signing secret now. It is not shown again.</strong>
          <code className="mono" style={{ wordBreak: "break-all", fontSize: 13 }}>{secret}</code>
          <button type="button" className="btn" style={{ alignSelf: "flex-start" }} onClick={() => setSecret(null)}>I have copied it</button>
        </div>
      )}
      {adding && <AddHook entityId={entityId} onDone={(s) => { setAdding(false); setSecret(s); hooks.reload(); }} onCancel={() => setAdding(false)} />}
      {hooks.loading && <Loading what="webhooks" />}
      {!hooks.loading && (hooks.data ?? []).length === 0 && <span className="t13">No endpoints yet.</span>}
      {(hooks.data ?? []).map((h) => (
        <div key={h.id} style={{ borderTop: "1px solid var(--line-2)", paddingTop: 8, display: "flex", flexDirection: "column", gap: 8 }}>
          <div style={{ display: "flex", justifyContent: "space-between", gap: 12, fontSize: 14 }}>
            <span style={{ minWidth: 0 }}><span className="mono" style={{ wordBreak: "break-all" }}>{h.url}</span><span className="t13" style={{ display: "block" }}>{eventsLabel(h.events)}</span></span>
            <span style={{ display: "flex", flexDirection: "column", gap: 6, alignItems: "flex-end", flex: "none" }}>
              <Pill tone={h.is_active ? "ok" : "prep"}>{h.is_active ? "Active" : "Paused"}</Pill>
              <span style={{ display: "flex", gap: 10, flexWrap: "wrap", justifyContent: "flex-end" }}>
                <button type="button" className="linkbtn" style={{ color: "var(--cobalt)" }} onClick={() => void run(async () => { unwrap(await api.PATCH("/entities/{entityId}/webhooks/{id}", { params: { path: { entityId, id: h.id }, header: { "If-Match": "*" } }, body: { is_active: !h.is_active } as never })); hooks.reload(); })}>{h.is_active ? "Pause" : "Resume"}</button>
                <button type="button" className="linkbtn" style={{ color: "var(--cobalt)" }} onClick={() => void run(async () => { unwrapEmpty(await api.POST("/entities/{entityId}/webhooks/{id}/test", { params: { path: { entityId, id: h.id } } })); setOpen(h.id); }, h.events.includes("*") ? "Test event queued. It appears under deliveries shortly." : "Test event queued, but this endpoint only listens to the events you chose, so it will not be delivered. Subscribe it to all events to receive test events.")}>Send test</button>
                <button type="button" className="linkbtn" style={{ color: "var(--cobalt)" }} aria-expanded={open === h.id} onClick={() => setOpen(open === h.id ? null : h.id)}>Deliveries</button>
                {confirm === h.id
                  ? <><button type="button" className="btn" style={{ height: 32, color: "var(--amber)" }} onClick={() => void run(async () => { unwrapEmpty(await api.DELETE("/entities/{entityId}/webhooks/{id}", { params: { path: { entityId, id: h.id } } })); setConfirm(null); hooks.reload(); })}>Delete endpoint</button><button type="button" className="linkbtn" style={{ color: "var(--cobalt)" }} onClick={() => setConfirm(null)}>Keep</button></>
                  : <button type="button" className="linkbtn" style={{ color: "var(--cobalt)" }} onClick={() => setConfirm(h.id)}>Delete…</button>}
              </span>
            </span>
          </div>
          {open === h.id && <Deliveries entityId={entityId} hookId={h.id} />}
        </div>
      ))}
    </section>
  );
}

function Deliveries({ entityId, hookId }: { entityId: string; hookId: string }) {
  const d = useLoad(async () => unwrap(await api.GET("/entities/{entityId}/webhooks/{id}/deliveries", { params: { path: { entityId, id: hookId }, query: { limit: 20 } } })).data as unknown as Delivery[], `${entityId}|${hookId}`);
  return (
    <div style={sub}>
      <span style={{ display: "flex", justifyContent: "space-between" }}><strong style={{ fontSize: 14 }}>Recent deliveries</strong><button type="button" className="linkbtn" style={{ color: "var(--cobalt)" }} onClick={d.reload}>Refresh</button></span>
      <ErrorNote error={d.error} />
      {d.loading && <Loading what="deliveries" />}
      {!d.loading && (d.data ?? []).length === 0 && <span className="t13">Nothing sent yet.</span>}
      {(d.data ?? []).map((x) => {
        const st = deliveryState(x);
        return <div key={x.id} style={{ display: "flex", justifyContent: "space-between", gap: 8, fontSize: 13 }}><span><span className="mono">{x.type}</span><span className="t13" style={{ display: "block" }}>Attempt {x.attempt}{x.attempted_at ? ` · ${shortDate(x.attempted_at)}` : ""}{x.error ? ` · ${x.error}` : ""}</span></span><Pill tone={st.tone}>{st.label}</Pill></div>;
      })}
    </div>
  );
}

function AddHook({ entityId, onDone, onCancel }: { entityId: string; onDone: (secret: string) => void; onCancel: () => void }) {
  const [f, setF] = useState<HookForm>(blankHook());
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const problem = hookProblem(f);
  async function save() {
    setBusy(true); setError(null);
    try {
      const r = unwrap(await api.POST("/entities/{entityId}/webhooks", { params: { path: { entityId } }, body: hookBody(f) as never })) as unknown as { secret: string };
      onDone(r.secret);
    } catch (e) { setError(e); setBusy(false); }
  }
  return (
    <form style={sub} onSubmit={(e) => { e.preventDefault(); if (!problem && !busy) void save(); }}>
      <h3 style={{ margin: 0, fontSize: 15 }}>Add a webhook endpoint</h3>
      <div className="field"><label htmlFor="wh-url">Address</label><input id="wh-url" className="input mono" inputMode="url" value={f.url} onChange={(e) => setF({ ...f, url: e.target.value })} /></div>
      <label style={{ display: "flex", gap: 8, fontSize: 14 }}><input type="checkbox" checked={f.all} onChange={(e) => setF({ ...f, all: e.target.checked })} />Send every event</label>
      {!f.all && (
        <fieldset style={{ border: 0, padding: 0, margin: 0 }}>
          <legend className="t13">Events</legend>
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(240px, 1fr))", gap: "6px 16px" }}>
            {EVENT_TYPES.map(([k, l]) => <label key={k} style={{ display: "flex", gap: 8, fontSize: 13 }}><input type="checkbox" checked={f.events.includes(k)} onChange={() => setF({ ...f, events: toggle(f.events, k) })} /><span>{l}<span className="t13 mono" style={{ display: "block" }}>{k}</span></span></label>)}
          </div>
        </fieldset>
      )}
      {f.url.trim().length > 8 && problem && <span className="t13" style={{ color: "var(--amber)" }}>{problem}</span>}
      <ErrorNote error={error} />
      <span style={{ display: "flex", gap: 10 }}>
        <button type="submit" className="btn primary" disabled={!!problem || busy}>{busy ? "Saving…" : "Add endpoint"}</button>
        <button type="button" className="btn" onClick={onCancel}>Cancel</button>
      </span>
    </form>
  );
}
