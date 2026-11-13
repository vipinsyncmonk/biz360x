"use client";
import Link from "next/link";
import { useState } from "react";
import { api, unwrap } from "@/lib/api";
import { formatInr } from "@/lib/format";
import { useLoad } from "@/lib/hooks";
import { RUN_STATUS, runName, type Run } from "@/lib/payroll";
import { AccessForm, LEVELS, LEVEL_LABEL, Presets, Tile, accessBody, accessProblem, applyPreset, blankAccess, setLevel, type Access } from "@/lib/roles";
import { ErrorNote } from "./ui";

/** Invite a person, or change what an existing member may do: a role to start from, then a level for each module. */
export function AccessEditor({ entityId, presets, member, onDone, onCancel }: {
  entityId: string; presets: Presets; member?: { id: string; name: string; title: string | null; preset: string | null; role: string; scopes: Record<string, string> };
  onDone: () => void; onCancel: () => void;
}) {
  const inviting = !member;
  const choices = presets.presets.filter((p) => p.kind === "member");
  const [f, setF] = useState<AccessForm>(() => {
    if (!member) return blankAccess();
    const star = member.scopes["*"];
    const scopes: Record<string, string> = {};
    for (const m of presets.modules) { const l = member.scopes[m.key] ?? star; if (l) scopes[m.key] = l; }
    return { ...blankAccess(), preset: member.preset ?? "", title: member.title ?? "", owner: member.role === "owner", scopes };
  });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const preset = choices.find((p) => p.key === f.preset);
  const problem = accessProblem(f, inviting);
  const sfx = member ? `-${member.id}` : "";

  async function save() {
    setBusy(true); setError(null);
    try {
      const body = accessBody(f, inviting, preset, presets.modules) as never;
      if (member) unwrap(await api.PATCH("/entities/{entityId}/members/{id}", { params: { path: { entityId, id: member.id }, header: { "If-Match": "*" } }, body }));
      else unwrap(await api.POST("/entities/{entityId}/members", { params: { path: { entityId } }, body }));
      onDone();
    } catch (e) { setError(e); setBusy(false); }
  }

  return (
    <form className="card-pad" style={{ border: "1px solid var(--line)", borderRadius: 12, background: "var(--surface-2)", display: "flex", flexDirection: "column", gap: 12 }}
          aria-label={member ? `Access of ${member.name}` : "Invite a person"} onSubmit={(e) => { e.preventDefault(); if (!problem && !busy) void save(); }}>
      <h3 style={{ margin: 0, fontSize: 15 }}>{member ? `What ${member.name} may do` : "Invite a person"}</h3>
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(220px, 1fr))", gap: 12 }}>
        {inviting && <div className="field"><label htmlFor="acc-email">Work email</label><input id="acc-email" type="email" className="input" autoComplete="off" value={f.email} onChange={(e) => setF({ ...f, email: e.target.value })} /></div>}
        {inviting && <div className="field"><label htmlFor="acc-name">Name (optional)</label><input id="acc-name" className="input" autoComplete="off" value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} /></div>}
        <div className="field"><label htmlFor={`acc-preset${sfx}`}>Start from a role</label>
          <select id={`acc-preset${sfx}`} className="input" value={f.preset} onChange={(e) => setF(applyPreset(f, choices.find((p) => p.key === e.target.value), presets.modules))}>
            <option value="">Custom</option>{choices.map((p) => <option key={p.key} value={p.key}>{p.title}</option>)}
          </select></div>
        <div className="field"><label htmlFor={`acc-title${sfx}`}>Title (optional)</label><input id={`acc-title${sfx}`} className="input" autoComplete="off" maxLength={80} placeholder="What you call them" value={f.title} onChange={(e) => setF({ ...f, title: e.target.value })} /></div>
      </div>
      {preset && <span className="t13">{preset.about}</span>}
      <label style={{ display: "flex", gap: 8, alignItems: "center", fontSize: 14 }}><input type="checkbox" checked={f.owner} onChange={(e) => setF({ ...f, owner: e.target.checked })} />
        <span>Owner: everything, and the only one who can invite people and change access</span></label>
      {!f.owner && (
        <div className="tablewrap"><table style={{ minWidth: 420 }} aria-label="Access by module">
          <thead><tr><th scope="col">Module</th><th scope="col">May</th></tr></thead>
          <tbody>{presets.modules.map((m) => (
            <tr key={m.key}><td><label htmlFor={`lvl-${m.key}${sfx}`}>{m.label}</label></td>
              <td><select id={`lvl-${m.key}${sfx}`} className="input" style={{ height: 36, fontSize: 14 }} value={f.scopes[m.key] ?? ""} onChange={(e) => setF(setLevel(f, m.key, e.target.value))}>
                <option value="">{LEVEL_LABEL[""]}</option>{LEVELS.map((l) => <option key={l} value={l}>{LEVEL_LABEL[l]}</option>)}
              </select></td></tr>
          ))}</tbody>
        </table></div>
      )}
      {problem && (f.email || f.preset || member) && <span className="t13" role="alert" style={{ color: "var(--amber)" }}>{problem}</span>}
      <ErrorNote error={error} />
      <span style={{ display: "flex", gap: 10 }}>
        <button type="submit" className="btn primary" disabled={busy || !!problem}>{busy ? "Saving…" : member ? "Save access" : "Send invite"}</button>
        <button type="button" className="btn" onClick={onCancel}>Cancel</button>
      </span>
      <span className="t13">{inviting ? "They sign in with a one-time code sent to this address. Nothing is emailed yet, so tell them to sign in. " : ""}A role is a starting point: change any line. Approvals and tasks follow the person&apos;s highest level.</span>
    </form>
  );
}

const TILE_LINK: Record<Tile, [string, string]> = {
  cash: ["Cash in bank", "/finance"], runway: ["Runway", "/finance"], receivables: ["Receivables", "/billing/receivables"], net_burn: ["Net burn a month", "/finance"], stock: ["Value of stock", "/inventory"],
  people: ["People", "/payroll/leave"], onboarding: ["Being onboarded", "/payroll/onboard"], leave_waiting: ["Leave waiting for a decision", "/payroll/leave"], payroll_run: ["Latest payroll run", "/payroll"],
};
interface Fin { cash_in_bank: string; receivables: string; receivables_overdue: string; runway_months: number | null; net_burn: string | null }

/** The numbers a role watches, above the sections everyone shares. Each tile opens the screen behind it; a tile the person has no access to is not asked for. */
export function HomeTiles({ entityId, tiles, access }: { entityId: string; tiles: Tile[]; access: Access }) {
  const p = { path: { entityId } };
  const has = (...t: Tile[]) => t.some((x) => tiles.includes(x));
  const key = (on: boolean) => (on ? `${entityId}|${access.home}` : null);
  const fin = useLoad(async () => unwrap(await api.GET("/entities/{entityId}/finance/summary", { params: p })) as unknown as Fin, key(has("cash", "runway", "receivables", "net_burn")));
  const stock = useLoad(async () => (unwrap(await api.GET("/entities/{entityId}/stock-items", { params: p })) as unknown as { totals: { value: string; items: number } }).totals, key(has("stock")));
  const people = useLoad(async () => unwrap(await api.GET("/entities/{entityId}/hr/people", { params: p })).data as unknown as Array<{ status: string }>, key(has("people", "onboarding")));
  const leave = useLoad(async () => unwrap(await api.GET("/entities/{entityId}/leave-requests", { params: { ...p, query: { status: "pending", limit: 200 } } })).data as unknown as unknown[], key(has("leave_waiting")));
  const runs = useLoad(async () => unwrap(await api.GET("/entities/{entityId}/payroll-runs", { params: { ...p, query: { limit: 1 } } })).data as unknown as Run[], key(has("payroll_run")));
  if (tiles.length === 0) return null;

  const count = (status?: string) => (people.data ? String(people.data.filter((e) => (status ? e.status === status : e.status !== "exited")).length) : "…");
  const value: Record<Tile, [string, string]> = {
    cash: [fin.data ? formatInr(fin.data.cash_in_bank, { decimals: false }) : "…", "Across bank accounts"],
    runway: [fin.data ? (fin.data.runway_months === null ? "—" : `${fin.data.runway_months} months`) : "…", "At the current burn"],
    receivables: [fin.data ? formatInr(fin.data.receivables, { decimals: false }) : "…", fin.data ? `${formatInr(fin.data.receivables_overdue, { decimals: false })} overdue` : ""],
    net_burn: [fin.data ? (fin.data.net_burn === null ? "—" : formatInr(fin.data.net_burn, { decimals: false })) : "…", "Money out less money in"],
    stock: [stock.data ? formatInr(stock.data.value, { decimals: false }) : "…", stock.data ? `${stock.data.items} items kept` : ""],
    people: [count(), "On the rolls"],
    onboarding: [count("onboarding"), "Not on payroll yet"],
    leave_waiting: [leave.data ? String(leave.data.length) : "…", "Requests to approve or reject"],
    payroll_run: [runs.data ? (runs.data[0] ? (RUN_STATUS[runs.data[0].status]?.label ?? runs.data[0].status) : "None yet") : "…", runs.data?.[0] ? runName(runs.data[0].period_month) : "Start the month's run"],
  };
  return (
    <>
      <ErrorNote error={fin.error ?? stock.error ?? people.error ?? leave.error ?? runs.error} />
      <section aria-label="Your numbers" className="kpis">
        {tiles.map((t) => (
          <Link key={t} href={TILE_LINK[t][1]} className="card kpi" style={{ textDecoration: "none", color: "inherit" }}>
            <span className="t13">{TILE_LINK[t][0]}</span>
            <span className="disp v">{value[t][0]}</span>
            <span className="t13">{value[t][1]}</span>
          </Link>
        ))}
      </section>
    </>
  );
}
