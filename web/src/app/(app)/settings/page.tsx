"use client";
import { Fragment, useEffect, useState } from "react";
import { api, unwrap, unwrapEmpty } from "@/lib/api";
import { shortDate } from "@/lib/format";
import { useLoad } from "@/lib/hooks";
import { PortalsSection, WebhooksSection } from "@/components/IntegrationsPanels";
import { AccessEditor } from "@/components/RoleForms";
import { WhoDoesWhat } from "@/components/WorkPanels";
import { EngagePractitioner } from "@/components/EngagementPanels";
import { AddCertificate, CreateApiKey } from "@/components/SettingsForms";
import { ENGAGEMENT_STATUS, engagementLine, type Engagement } from "@/lib/engagements";
import { scopesText, type Presets } from "@/lib/roles";
import { ApiKeyRow, CHANNELS, CONN_STATUS, Dsc, EVENTS, GRANT_STATUS, Grant, KIND_LABEL, Member, PORTAL_LABEL, PortalConn, Pref, ROLE_LABEL, SCOPE_LABEL, dscStatus, modulesLabel, permissionLabel, prefBody, prefKey, prefState } from "@/lib/settings";
import { useSession } from "@/lib/session";
import { ErrorNote, Loading, Pill } from "@/components/ui";

interface Verify { intact: boolean; checked: number }
type PanelKey = "invite" | "grant" | "dsc" | "key" | null;

export default function Settings() {
  const { entity, me } = useSession();
  const id = entity?.id ?? null;
  const members = useLoad(async () => unwrap(await api.GET("/entities/{entityId}/members", { params: { path: { entityId: id! } } })).data as unknown as Member[], id);
  const grants = useLoad(async () => unwrap(await api.GET("/entities/{entityId}/advisor-grants", { params: { path: { entityId: id! } } })).data as unknown as Engagement[], id);
  const dsc = useLoad(async () => unwrap(await api.GET("/entities/{entityId}/dsc-certificates", { params: { path: { entityId: id! } } })).data as unknown as Dsc[], id);
  const prefs = useLoad(async () => unwrap(await api.GET("/me/notification-preferences")) as unknown as Pref[], id);
  const keys = useLoad(async () => unwrap(await api.GET("/entities/{entityId}/api-keys", { params: { path: { entityId: id! } } })).data as unknown as ApiKeyRow[], id);
  const audit = useLoad(async () => unwrap(await api.GET("/entities/{entityId}/audit-log/verify", { params: { path: { entityId: id! } } })) as unknown as Verify, id);

  const [state, setState] = useState<Record<string, boolean>>({});
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [failure, setFailure] = useState<unknown>(null);
  const [panel, setPanel] = useState<PanelKey>(null);
  const [editing, setEditing] = useState<string | null>(null);
  const presets = useLoad(async () => unwrap(await api.GET("/role-presets")) as unknown as Presets, id);
  const isOwner = entity?.role === "owner";
  const [confirm, setConfirm] = useState<string | null>(null);
  const [actionError, setActionError] = useState<unknown>(null);
  const [newKey, setNewKey] = useState<string | null>(null);
  useEffect(() => { if (prefs.data && id) setState(prefState(prefs.data, id)); }, [prefs.data, id]);

  async function toggle(event: string, channel: string) {
    const next = { ...state, [prefKey(event, channel)]: !state[prefKey(event, channel)] };
    setState(next); setSaving(true); setSaved(false); setFailure(null);
    try {
      unwrap(await api.PUT("/me/notification-preferences", { body: prefBody(id!, next) as never }));
      setSaved(true);
    } catch (e) {
      setState(state);                                  // show what is really stored
      setFailure(e);
    } finally {
      setSaving(false);
    }
  }

  /** Run a delete-style call, then reload what it changed. */
  async function remove(path: "members" | "advisor-grants" | "api-keys", rowId: string, reload: () => void) {
    setActionError(null);
    try {
      unwrapEmpty(await api.DELETE(`/entities/{entityId}/${path}/{id}` as "/entities/{entityId}/members/{id}", { params: { path: { entityId: id!, id: rowId } } }));
      setConfirm(null); reload();
    } catch (e) { setActionError(e); setConfirm(null); }
  }
  const done = (reload: () => void) => () => { setPanel(null); reload(); };
  const Confirm = ({ rowId, what, reload, path }: { rowId: string; what: string; reload: () => void; path: "members" | "advisor-grants" | "api-keys" }) =>
    confirm === rowId ? (
      <span style={{ display: "inline-flex", gap: 6, alignItems: "center" }}>
        <button type="button" className="btn" style={{ height: 32, color: "var(--amber)" }} onClick={() => void remove(path, rowId, reload)}>{what}</button>
        <button type="button" className="linkbtn" style={{ color: "var(--cobalt)" }} onClick={() => setConfirm(null)}>Keep</button>
      </span>
    ) : <button type="button" className="linkbtn" style={{ color: "var(--cobalt)" }} onClick={() => setConfirm(rowId)}>{path === "api-keys" ? "Revoke" : path === "advisor-grants" ? "Revoke" : "Remove"}</button>;

  return (
    <main className="page">
      <div>
        <span className="muted" style={{ fontSize: 14 }}>SETTINGS · {entity?.legal_name}</span>
        <h1 className="disp" style={{ margin: "6px 0 0", fontSize: 36 }}>Who can see, prepare, approve and sign</h1>
      </div>

      <section className="card" style={{ overflow: "hidden" }}>
        <div style={{ padding: "14px 20px", borderBottom: "1px solid var(--line-2)", display: "flex", justifyContent: "space-between", alignItems: "center", gap: 12, flexWrap: "wrap" }}>
          <h2 style={{ margin: 0 }}>People{members.data ? ` · ${members.data.length}` : ""}</h2>
          {isOwner && <button type="button" className="btn" aria-expanded={panel === "invite"} onClick={() => setPanel(panel === "invite" ? null : "invite")}>+ Invite person</button>}
        </div>
        <div style={{ padding: "0 20px" }}><ErrorNote error={members.error ?? actionError} /></div>
        {panel === "invite" && presets.data && <div style={{ padding: 16 }}><AccessEditor entityId={id!} presets={presets.data} onDone={done(members.reload)} onCancel={() => setPanel(null)} /></div>}
        {!isOwner && <div className="t13" style={{ padding: "8px 20px" }}>Only an owner can invite people or change what they may do.</div>}
        {members.loading ? <Loading what="people" /> : (
          <div className="tablewrap">
            <table style={{ minWidth: 640 }}>
              <thead><tr><th scope="col">Person</th><th scope="col">Role</th><th scope="col">May</th><th scope="col">Sign-in</th><th scope="col"><span className="sr">Actions</span></th></tr></thead>
              <tbody>
                {(members.data ?? []).map((m) => (
                  <Fragment key={m.id}><tr>
                    <td><span style={{ fontWeight: 500 }}>{m.full_name}</span><span className="t13" style={{ display: "block" }}>{m.email}{m.accepted_at ? "" : " · invited"}</span></td>
                    <td>{m.title ?? ROLE_LABEL[m.role] ?? m.role}{m.title && <span className="t13" style={{ display: "block" }}>{ROLE_LABEL[m.role] ?? m.role}</span>}</td><td className="t13">{m.scopes ? scopesText(m.scopes, presets.data?.modules) : modulesLabel(m.modules)}</td><td className="t13">{m.auth}</td>
                    <td style={{ textAlign: "right" }}>{isOwner && m.email !== me?.email && <><button type="button" className="linkbtn" style={{ color: "var(--cobalt)", marginRight: 12 }} aria-expanded={editing === m.id} aria-label={`Change access of ${m.full_name}`} onClick={() => setEditing(editing === m.id ? null : m.id)}>Change access</button><Confirm rowId={m.id} what="Remove access" reload={members.reload} path="members" /></>}</td>
                  </tr>
                  {editing === m.id && presets.data && <tr><td colSpan={5}><AccessEditor entityId={id!} presets={presets.data} member={{ id: m.id, name: m.full_name, title: m.title ?? null, preset: m.preset ?? null, role: m.role, scopes: m.scopes ?? {} }} onDone={() => { setEditing(null); members.reload(); }} onCancel={() => setEditing(null)} /></td></tr>}</Fragment>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      {id && <WhoDoesWhat entityId={id} isOwner={isOwner} />}

      <div className="cols">
        <div className="col-main">
          <section className="card card-pad" style={{ display: "flex", flexDirection: "column", gap: 8 }}>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 12 }}><h2 style={{ margin: 0 }}>Practitioners</h2>
              {isOwner && <button type="button" className="btn" aria-expanded={panel === "grant"} onClick={() => setPanel(panel === "grant" ? null : "grant")}>+ Engage practitioner</button>}</div>
            <span className="t13">Your CA, CS, auditor and consultants. Each sees only what their engagement gives, from the day they accept until it ends.</span>
            <ErrorNote error={grants.error} />
            {panel === "grant" && <EngagePractitioner entityId={id!} presets={presets.data} onDone={done(grants.reload)} onCancel={() => setPanel(null)} />}
            {!grants.loading && (grants.data ?? []).length === 0 && <span className="t13">No practitioner is engaged.</span>}
            {(grants.data ?? []).map((g) => {
              const st = ENGAGEMENT_STATUS[g.status] ?? { label: g.status, tone: "prep" as const };
              return (
                <div key={g.id} style={{ borderTop: "1px solid var(--line-2)", paddingTop: 8, display: "flex", justifyContent: "space-between", gap: 12, fontSize: 14 }}>
                  <span>{g.advisor_name}{g.audit && <span className="t13"> · read only</span>}<span className="t13" style={{ display: "block" }}>{engagementLine(g, shortDate)}</span></span>
                  <span style={{ display: "flex", flexDirection: "column", gap: 6, alignItems: "flex-end" }}><Pill tone={st.tone}>{st.label}</Pill>{isOwner && (g.status === "active" || g.status === "invited") && <Confirm rowId={g.id} what={g.status === "invited" ? "Withdraw invitation" : "End engagement"} reload={grants.reload} path="advisor-grants" />}</span>
                </div>
              );
            })}
          </section>

          <section className="card card-pad" style={{ display: "flex", flexDirection: "column", gap: 8 }}>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 12 }}><h2 style={{ margin: 0 }}>Digital signature certificates</h2><button type="button" className="btn" onClick={() => setPanel(panel === "dsc" ? null : "dsc")}>+ Add certificate</button></div>
            <ErrorNote error={dsc.error} />
            {panel === "dsc" && <AddCertificate entityId={id!} onDone={done(dsc.reload)} onCancel={() => setPanel(null)} />}
            {!dsc.loading && (dsc.data ?? []).length === 0 && <span className="t13">No certificates recorded.</span>}
            {(dsc.data ?? []).map((d) => {
              const st = dscStatus(d.valid_until);
              return (
                <div key={d.id} style={{ borderTop: "1px solid var(--line-2)", paddingTop: 8, display: "flex", justifyContent: "space-between", gap: 12, fontSize: 14 }}>
                  <span>{d.person_name}<span className="t13" style={{ display: "block" }}>{d.dsc_class} · used on {d.registered_on.length ? d.registered_on.join(", ") : "no portal yet"} · until {shortDate(d.valid_until)}</span></span>
                  <Pill tone={st.tone}>{st.label}</Pill>
                </div>
              );
            })}
          </section>

          <PortalsSection entityId={id!} />
          <section className="card card-pad" style={{ display: "flex", flexDirection: "column", gap: 8 }}>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 12 }}><h2 style={{ margin: 0 }}>API keys</h2><button type="button" className="btn" onClick={() => { setPanel(panel === "key" ? null : "key"); setNewKey(null); }}>+ Create key</button></div>
            <span className="t13">For connecting other software. A key acts as its own named user and can never approve, file or pay.</span>
            <ErrorNote error={keys.error} />
            {newKey && (
              <div role="status" style={{ background: "var(--green-soft)", borderRadius: 12, padding: 12, display: "flex", flexDirection: "column", gap: 6 }}>
                <strong>Copy this key now. It is not shown again.</strong>
                <code className="mono" style={{ wordBreak: "break-all", fontSize: 13 }}>{newKey}</code>
                <button type="button" className="btn" style={{ alignSelf: "flex-start" }} onClick={() => setNewKey(null)}>I have copied it</button>
              </div>
            )}
            {panel === "key" && <CreateApiKey entityId={id!} onDone={(k) => { setPanel(null); setNewKey(k); keys.reload(); }} onCancel={() => setPanel(null)} />}
            {!keys.loading && (keys.data ?? []).length === 0 && <span className="t13">No keys yet.</span>}
            {(keys.data ?? []).map((k) => (
              <div key={k.id} style={{ borderTop: "1px solid var(--line-2)", paddingTop: 8, display: "flex", justifyContent: "space-between", gap: 12, fontSize: 14 }}>
                <span>{k.name}<span className="t13" style={{ display: "block" }}><span className="mono">{k.prefix}…</span> · {permissionLabel(k.permissions)} · {k.last_used_at ? `used ${shortDate(k.last_used_at)}` : "never used"}</span></span>
                {k.revoked_at ? <Pill tone="prep">Revoked</Pill> : <Confirm rowId={k.id} what="Revoke key" reload={keys.reload} path="api-keys" />}
              </div>
            ))}
          </section>
          <WebhooksSection entityId={id!} />
        </div>

        <aside className="col-side">
          <section className="card card-pad" style={{ display: "flex", flexDirection: "column", gap: 8 }}>
            <h2 style={{ margin: 0 }}>Notify me about</h2>
            <span className="t13">For {entity?.legal_name}. Your choices are saved now; sending follows with the notification service.</span>
            <ErrorNote error={prefs.error ?? failure} />
            <div className="tablewrap">
              <table style={{ minWidth: 360 }}>
                <thead><tr><th scope="col">Event</th>{CHANNELS.map(([, l]) => <th key={l} scope="col" style={{ textAlign: "center", fontSize: 12 }}>{l}</th>)}</tr></thead>
                <tbody>
                  {EVENTS.map(([e, label]) => (
                    <tr key={e}>
                      <td style={{ fontSize: 13 }}>{label}</td>
                      {CHANNELS.map(([c, cl]) => (
                        <td key={c} style={{ textAlign: "center" }}>
                          <input type="checkbox" aria-label={`${label} by ${cl}`} checked={!!state[prefKey(e, c)]} disabled={saving || prefs.loading} onChange={() => void toggle(e, c)} />
                        </td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <span className="t13" role="status">{saving ? "Saving…" : saved ? "Saved" : ""}</span>
          </section>

          <section className="card card-pad" style={{ display: "flex", flexDirection: "column", gap: 6 }}>
            <h2 style={{ margin: 0 }}>Audit trail</h2>
            <ErrorNote error={audit.error} />
            {audit.data && (
              <>
                <Pill tone={audit.data.intact ? "ok" : "due"}>{audit.data.intact ? "Chain intact" : "Chain broken"}</Pill>
                <span className="t13">{audit.data.checked} entries for this entity. Each is linked to the one before, so an edit or removal would show.</span>
              </>
            )}
          </section>
        </aside>
      </div>
    </main>
  );
}
