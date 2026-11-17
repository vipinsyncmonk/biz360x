"use client";
import Link from "next/link";
import { useState } from "react";
import { api, newIdempotencyKey, unwrap } from "@/lib/api";
import { ChaseResult, SKIP_REASON, addClientHref, chaseLabel, chaseSummary, chaseTargets } from "@/lib/group";
import { shortDate } from "@/lib/format";
import { useLoad } from "@/lib/hooks";
import { CELL_COLUMNS, CELL_LABEL, StaffLoad, Standing, byTrouble, groupCounts } from "@/lib/settings";
import { useSession } from "@/lib/session";
import { ErrorNote, Loading, Pill } from "@/components/ui";
import { ClientStaffPanel } from "@/components/EngagementPanels";

type Filter = "all" | "waiting_on_client" | "overdue";
const FILTERS: Array<[Filter, string]> = [["all", "All"], ["waiting_on_client", "Waiting on client"], ["overdue", "Overdue"]];

export default function Group() {
  const { me, setEntityId } = useSession();
  const org = me?.organizations?.[0];
  const orgId = org?.id ?? null;
  const [filter, setFilter] = useState<Filter>("all");
  const [picked, setPicked] = useState<string | null>(null);
  const [chasing, setChasing] = useState(false);
  const [chaseError, setChaseError] = useState<unknown>(null);
  const [result, setResult] = useState<ChaseResult | null>(null);

  const all = useLoad(async () => unwrap(await api.GET("/organizations/{id}/entities", { params: { path: { id: orgId! }, query: { limit: 200 } } })).data as unknown as Standing[], orgId);
  const shown = useLoad(async () => unwrap(await api.GET("/organizations/{id}/entities", { params: { path: { id: orgId! }, query: { limit: 200, filter } } })).data as unknown as Standing[], orgId ? `${orgId}|${filter}` : null);
  const load = useLoad(async () => unwrap(await api.GET("/organizations/{id}/workload", { params: { path: { id: orgId! } } })) as unknown as StaffLoad[], orgId);

  const rows = byTrouble(shown.data ?? []);
  const current = rows.find((r) => r.entity_id === picked) ?? rows[0];
  const counts = groupCounts(all.data ?? []);
  const targets = chaseTargets(all.data ?? []);

  async function chase() {
    setChasing(true); setChaseError(null);
    try {
      const out = unwrap(await api.POST("/organizations/{id}/client-reminders", { params: { path: { id: orgId! }, header: { "Idempotency-Key": newIdempotencyKey() } }, body: { entity_ids: targets } })) as unknown as ChaseResult;
      setResult(out);
    } catch (e) { setChaseError(e); }
    setChasing(false);
  }

  if (me && !org) return <main className="page"><div className="card empty">The group view is for firms and groups with more than one workspace. Your account has no organization yet.</div></main>;

  return (
    <main className="page">
      <div style={{ display: "flex", flexWrap: "wrap", gap: 16, justifyContent: "space-between", alignItems: "flex-end" }}>
        <div>
          <span className="muted" style={{ fontSize: 14 }}>GROUP VIEW · {org?.name}</span>
          <h1 className="disp" style={{ margin: "6px 0 0", fontSize: 36 }}>{counts.waiting} {counts.waiting === 1 ? "client has" : "clients have"} filings waiting on their input</h1>
        </div>
        <span style={{ display: "flex", gap: 10, flexWrap: "wrap" }}>
          {orgId && <Link href={addClientHref(orgId)} className="btn">Add client</Link>}
          <button type="button" className="btn primary" disabled={chasing || targets.length === 0} onClick={() => void chase()}>{chasing ? "Queuing…" : chaseLabel(targets.length)}</button>
        </span>
      </div>
      <ErrorNote error={chaseError} />
      {result && (
        <section className="card card-pad" role="status" style={{ display: "flex", flexDirection: "column", gap: 8 }}>
          <b>{chaseSummary(result)}</b>
          <span className="t13">Reminders go to each client&apos;s own owners by email. They are queued here and sent by the notification service.</span>
          {result.reminded.map((r) => <div key={r.entity_id} style={{ fontSize: 14 }}>{r.legal_name}<span className="t13"> · {r.items} {r.items === 1 ? "filing" : "filings"} · {r.recipients} {r.recipients === 1 ? "person" : "people"}</span></div>)}
          {result.skipped.map((r) => <div key={r.entity_id} style={{ fontSize: 14 }}>{r.legal_name}<span className="t13"> · {SKIP_REASON[r.reason] ?? r.reason}</span></div>)}
        </section>
      )}

      <ErrorNote error={all.error} />
      <section aria-label="Key numbers" className="kpis">
        <Tile label="Client entities" value={String(counts.clients)} note="In this organization" />
        <Tile label="Waiting on client" value={String(counts.waiting)} note="Filings that need their input" warn={counts.waiting > 0} />
        <Tile label="Overdue" value={String(counts.overdue)} note="Entities with a late filing" warn={counts.overdue > 0} />
      </section>

      <div className="cols">
        <div className="col-main">
          <section className="card" style={{ overflow: "hidden" }}>
            <div style={{ padding: "14px 20px", display: "flex", justifyContent: "space-between", alignItems: "center", flexWrap: "wrap", gap: 12, borderBottom: "1px solid var(--line-2)" }}>
              <h2 style={{ margin: 0 }}>Clients × authorities</h2>
              <div className="seg" role="tablist" aria-label="Filter">
                {FILTERS.map(([k, l]) => <button key={k} type="button" role="tab" aria-selected={filter === k} onClick={() => setFilter(k)}>{l}</button>)}
              </div>
            </div>
            <div style={{ padding: "0 20px" }}><ErrorNote error={shown.error} /></div>
            {shown.loading ? <Loading what="clients" /> : (
              <div className="tablewrap">
                <table style={{ minWidth: 760 }}>
                  <thead><tr><th scope="col">Client</th>{CELL_COLUMNS.map(([k, l]) => <th key={k} scope="col">{l}</th>)}<th scope="col">Owner</th></tr></thead>
                  <tbody>
                    {rows.map((r) => (
                      <tr key={r.entity_id} className="trow" onClick={() => setPicked(r.entity_id)} style={current?.entity_id === r.entity_id ? { background: "var(--bg-2, #FAFAF7)" } : undefined}>
                        <td><button type="button" style={{ all: "unset", cursor: "pointer", fontWeight: 600 }} onClick={() => setPicked(r.entity_id)}>{r.legal_name}</button></td>
                        {CELL_COLUMNS.map(([k]) => { const c = CELL_LABEL[r.cells[k] ?? "na"] ?? CELL_LABEL.na!; return <td key={k}>{c.tone === "none" ? <span className="t13">–</span> : <Pill tone={c.tone}>{c.label}</Pill>}</td>; })}
                        <td className="t13">{r.owner_staff ?? "Unassigned"}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
                {rows.length === 0 && <div className="empty">{filter === "all" ? "No client entities yet." : "No client matches."}</div>}
              </div>
            )}
          </section>
        </div>

        <aside className="col-side">
          <section className="card card-pad" style={{ display: "flex", flexDirection: "column", gap: 8 }}>
            <span className="t13">OPEN ITEMS</span>
            <h2 style={{ margin: 0 }}>{current?.legal_name ?? "No client selected"}</h2>
            {current && current.open_items.length === 0 && <span className="t13">Nothing open.</span>}
            {current?.open_items.map((i, n) => (
              <div key={n} style={{ borderTop: "1px solid var(--line-2)", paddingTop: 8, fontSize: 14 }}>{i.title}<span className="t13" style={{ display: "block" }}>{i.detail} · due {shortDate(i.due_on)}</span></div>
            ))}
            {current && <button type="button" className="btn" onClick={() => setEntityId(current.entity_id)}>Open workspace</button>}
          </section>
          <section className="card card-pad" style={{ display: "flex", flexDirection: "column", gap: 8 }}>
            <h2 style={{ margin: 0 }}>Team load this week</h2>
            <span className="t13">Filings assigned, due within 7 days or late</span>
            <ErrorNote error={load.error} />
            {(load.data ?? []).map((t) => (
              <div key={t.user_id} style={{ borderTop: "1px solid var(--line-2)", paddingTop: 8, display: "flex", justifyContent: "space-between", fontSize: 14 }}><span>{t.name}</span><span className="mono">{t.filings_due}</span></div>
            ))}
          </section>
        </aside>
      </div>

      {orgId && org?.kind !== "business_group" && <ClientStaffPanel orgId={orgId} canAssign={org?.role === "admin" || org?.role === "partner"} />}
    </main>
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
