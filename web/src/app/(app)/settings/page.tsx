"use client";
import { useEffect, useState } from "react";
import { api, unwrap } from "@/lib/api";
import { shortDate } from "@/lib/format";
import { useLoad } from "@/lib/hooks";
import { CHANNELS, CONN_STATUS, Dsc, EVENTS, GRANT_STATUS, Grant, KIND_LABEL, Member, PORTAL_LABEL, PortalConn, Pref, ROLE_LABEL, SCOPE_LABEL, dscStatus, modulesLabel, prefBody, prefKey, prefState } from "@/lib/settings";
import { useSession } from "@/lib/session";
import { ErrorNote, Loading, Pill } from "@/components/ui";

interface Verify { intact: boolean; checked: number }

export default function Settings() {
  const { entity } = useSession();
  const id = entity?.id ?? null;
  const members = useLoad(async () => unwrap(await api.GET("/entities/{entityId}/members", { params: { path: { entityId: id! } } })).data as unknown as Member[], id);
  const grants = useLoad(async () => unwrap(await api.GET("/entities/{entityId}/advisor-grants", { params: { path: { entityId: id! } } })).data as unknown as Grant[], id);
  const dsc = useLoad(async () => unwrap(await api.GET("/entities/{entityId}/dsc-certificates", { params: { path: { entityId: id! } } })).data as unknown as Dsc[], id);
  const portals = useLoad(async () => unwrap(await api.GET("/entities/{entityId}/portal-connections", { params: { path: { entityId: id! } } })).data as unknown as PortalConn[], id);
  const prefs = useLoad(async () => unwrap(await api.GET("/me/notification-preferences")) as unknown as Pref[], id);
  const audit = useLoad(async () => unwrap(await api.GET("/entities/{entityId}/audit-log/verify", { params: { path: { entityId: id! } } })) as unknown as Verify, id);

  const [state, setState] = useState<Record<string, boolean>>({});
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [failure, setFailure] = useState<unknown>(null);
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

  return (
    <main className="page">
      <div>
        <span className="muted" style={{ fontSize: 14 }}>SETTINGS · {entity?.legal_name}</span>
        <h1 className="disp" style={{ margin: "6px 0 0", fontSize: 36 }}>Who can see, prepare, approve and sign</h1>
      </div>

      <section className="card" style={{ overflow: "hidden" }}>
        <div style={{ padding: "14px 20px", borderBottom: "1px solid var(--line-2)" }}><h2 style={{ margin: 0 }}>People{members.data ? ` · ${members.data.length}` : ""}</h2></div>
        <div style={{ padding: "0 20px" }}><ErrorNote error={members.error} /></div>
        {members.loading ? <Loading what="people" /> : (
          <div className="tablewrap">
            <table style={{ minWidth: 640 }}>
              <thead><tr><th scope="col">Person</th><th scope="col">Role</th><th scope="col">Modules</th><th scope="col">Sign-in</th></tr></thead>
              <tbody>
                {(members.data ?? []).map((m) => (
                  <tr key={m.id}>
                    <td><span style={{ fontWeight: 500 }}>{m.full_name}</span><span className="t13" style={{ display: "block" }}>{m.email}{m.accepted_at ? "" : " · invited"}</span></td>
                    <td>{ROLE_LABEL[m.role] ?? m.role}</td><td>{modulesLabel(m.modules)}</td><td className="t13">{m.auth}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      <div className="cols">
        <div className="col-main">
          <section className="card card-pad" style={{ display: "flex", flexDirection: "column", gap: 8 }}>
            <h2 style={{ margin: 0 }}>Advisors</h2>
            <ErrorNote error={grants.error} />
            {!grants.loading && (grants.data ?? []).length === 0 && <span className="t13">No advisor has access.</span>}
            {(grants.data ?? []).map((g) => {
              const st = GRANT_STATUS[g.status] ?? { label: g.status, tone: "prep" as const };
              return (
                <div key={g.id} style={{ borderTop: "1px solid var(--line-2)", paddingTop: 8, display: "flex", justifyContent: "space-between", gap: 12, fontSize: 14 }}>
                  <span>{g.advisor_name}<span className="t13" style={{ display: "block" }}>{KIND_LABEL[g.advisor_kind] ?? g.advisor_kind} · {Object.entries(g.scopes).map(([k, v]) => `${k.replace(/_/g, " ")}: ${SCOPE_LABEL[v] ?? v}`).join(", ")}{g.expires_on ? ` · until ${shortDate(g.expires_on)}` : ""}</span></span>
                  <Pill tone={st.tone}>{st.label}</Pill>
                </div>
              );
            })}
          </section>

          <section className="card card-pad" style={{ display: "flex", flexDirection: "column", gap: 8 }}>
            <h2 style={{ margin: 0 }}>Digital signature certificates</h2>
            <ErrorNote error={dsc.error} />
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

          <section className="card card-pad" style={{ display: "flex", flexDirection: "column", gap: 8 }}>
            <h2 style={{ margin: 0 }}>Connected portals</h2>
            <span className="t13">Credentials are never stored in plain text. Sessions that need an OTP ask the owner to renew.</span>
            <ErrorNote error={portals.error} />
            {!portals.loading && (portals.data ?? []).length === 0 && <span className="t13">No portal is connected.</span>}
            {(portals.data ?? []).map((c) => {
              const st = CONN_STATUS[c.status] ?? { label: c.status, tone: "prep" as const };
              return (
                <div key={c.id} style={{ borderTop: "1px solid var(--line-2)", paddingTop: 8, display: "flex", justifyContent: "space-between", gap: 12, fontSize: 14 }}>
                  <span>{PORTAL_LABEL[c.portal] ?? c.portal}<span className="t13" style={{ display: "block" }}>{c.last_sync_at ? `Last synced ${shortDate(c.last_sync_at)}` : "Not synced yet"}{c.last_error ? ` · ${c.last_error}` : ""}</span></span>
                  <Pill tone={st.tone}>{st.label}</Pill>
                </div>
              );
            })}
          </section>
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
