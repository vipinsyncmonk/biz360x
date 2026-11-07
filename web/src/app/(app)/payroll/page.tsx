"use client";
import { useState } from "react";
import { api, unwrap } from "@/lib/api";
import { formatInr, shortDate } from "@/lib/format";
import { useLoad } from "@/lib/hooks";
import { LABOUR_STATUS, Labour, RUN_STATUS, Run, STEPS, Slip, WAGE_FLAG, nextAction, nextRunMonth, runName, stepIndex } from "@/lib/payroll";
import { useSession } from "@/lib/session";
import { ErrorNote, Loading, Pill } from "@/components/ui";

export default function Payroll() {
  const { entity } = useSession();
  const id = entity?.id ?? null;
  const [picked, setPicked] = useState<string | null>(null);
  const [flagged, setFlagged] = useState(false);
  const [busy, setBusy] = useState(false);
  const [failure, setFailure] = useState<unknown>(null);
  const [note, setNote] = useState("");

  const runs = useLoad(async () => unwrap(await api.GET("/entities/{entityId}/payroll-runs", { params: { path: { entityId: id! }, query: { limit: 24 } } })).data as unknown as Run[], id);
  const list = runs.data ?? [];
  const run = list.find((r) => r.id === picked) ?? list[0];
  const slips = useLoad(async () => unwrap(await api.GET("/entities/{entityId}/payroll-runs/{id}/payslips", { params: { path: { entityId: id!, id: run!.id }, query: { limit: 200, ...(flagged ? { flag: WAGE_FLAG } : {}) } } })).data as unknown as Slip[], id && run ? `${id}|${run.id}|${run.status}|${flagged}` : null);
  const labour = useLoad(async () => unwrap(await api.GET("/entities/{entityId}/payroll/labour-compliance", { params: { path: { entityId: id! } } })) as unknown as Labour, id);

  async function act(what: "start" | "compute" | "submit") {
    setBusy(true); setFailure(null); setNote("");
    try {
      if (what === "start") {
        const r = unwrap(await api.POST("/entities/{entityId}/payroll-runs", { params: { path: { entityId: id! } }, body: { period_month: nextRunMonth(list) } })) as unknown as Run;
        setPicked(r.id);
      } else if (what === "compute") {
        unwrap(await api.POST("/entities/{entityId}/payroll-runs/{id}/compute", { params: { path: { entityId: id!, id: run!.id } } }));
      } else {
        unwrap(await api.POST("/entities/{entityId}/payroll-runs/{id}/submit", { params: { path: { entityId: id!, id: run!.id } } }));
        setNote("Sent for approval. A second person has to approve it before it is posted.");
      }
      runs.reload();
    } catch (e) {
      setFailure(e);
    } finally {
      setBusy(false);
    }
  }

  const st = run ? (RUN_STATUS[run.status] ?? { label: run.status, tone: "prep" as const }) : null;
  const action = run ? nextAction(run) : null;
  const rows = slips.data ?? [];
  const l = labour.data;

  return (
    <main className="page">
      <div style={{ display: "flex", flexWrap: "wrap", gap: 16, justifyContent: "space-between", alignItems: "flex-end" }}>
        <div>
          <span className="muted" style={{ fontSize: 14 }}>{entity?.legal_name}{run ? ` · ${runName(run.period_month)} run` : ""}</span>
          <h1 className="disp" style={{ margin: "6px 0 0", fontSize: 36 }}>Payroll &amp; HR</h1>
        </div>
        <div style={{ display: "flex", gap: 10, flexWrap: "wrap", alignItems: "center" }}>
          {list.length > 1 && (
            <div className="field"><label className="sr" htmlFor="run">Payroll run</label>
              <select id="run" className="input" style={{ height: 40, fontSize: 14 }} value={run?.id ?? ""} onChange={(e) => setPicked(e.target.value)}>
                {list.map((r) => <option key={r.id} value={r.id}>{runName(r.period_month)}</option>)}
              </select></div>
          )}
          <button type="button" className="btn" disabled={busy || runs.loading} onClick={() => void act("start")}>Start {runName(nextRunMonth(list))} run</button>
          {action === "compute" && <button type="button" className="btn primary" disabled={busy} onClick={() => void act("compute")}>{busy ? "Computing…" : "Compute pay"}</button>}
          {action === "submit" && <button type="button" className="btn primary" disabled={busy} onClick={() => void act("submit")}>{busy ? "Sending…" : "Send for approval"}</button>}
          {action === "waiting" && <Pill tone="wait">Waiting for approval</Pill>}
        </div>
      </div>

      <ErrorNote error={runs.error ?? failure} />
      {note && <div role="status" className="t13">{note}</div>}

      {run && st && (
        <>
          <section aria-label="Key numbers" className="kpis">
            <Tile label="Run status" value={st.label} note={run.paid_on ? `Paid ${shortDate(run.paid_on)}` : "Not paid yet"} />
            <Tile label="People" value={String(run.headcount)} note="On this run" />
            <Tile label="Gross pay" value={formatInr(run.gross_total, { decimals: false })} note="Before deductions" />
            <Tile label="Net pay" value={formatInr(run.net_total, { decimals: false })} note="To employees" />
            <Tile label="Employer cost" value={formatInr(run.employer_cost_total, { decimals: false })} note="Pay plus PF and ESI" />
          </section>

          <ol aria-label="Run progress" style={{ display: "flex", gap: 8, flexWrap: "wrap", listStyle: "none", margin: 0, padding: 0 }}>
            {STEPS.map(([k, label], i) => <li key={k}><Pill tone={i < stepIndex(run.status) ? "ok" : i === stepIndex(run.status) ? "wait" : "prep"}>{label}</Pill></li>)}
          </ol>

          <div className="cols">
            <div className="col-main">
              <section className="card" style={{ overflow: "hidden" }}>
                <div style={{ padding: "14px 20px", display: "flex", justifyContent: "space-between", alignItems: "center", flexWrap: "wrap", gap: 12, borderBottom: "1px solid var(--line-2)" }}>
                  <h2 style={{ margin: 0 }}>Salary register · {runName(run.period_month)}</h2>
                  <div className="seg" role="tablist" aria-label="Filter">
                    <button type="button" role="tab" aria-selected={!flagged} onClick={() => setFlagged(false)}>All</button>
                    <button type="button" role="tab" aria-selected={flagged} onClick={() => setFlagged(true)}>Wage code flags</button>
                  </div>
                </div>
                <div style={{ padding: "0 20px" }}><ErrorNote error={slips.error} /></div>
                {slips.loading ? <Loading what="payslips" /> : (
                  <div className="tablewrap">
                    <table style={{ minWidth: 820 }}>
                      <thead><tr><th scope="col">Employee</th>{["Gross", "PF", "ESI", "PT", "TDS", "Net pay"].map((h) => <th key={h} scope="col" style={{ textAlign: "right" }}>{h}</th>)}</tr></thead>
                      <tbody>
                        {rows.map((s) => (
                          <tr key={s.id}>
                            <td><span style={{ fontWeight: 500 }}>{s.employee_name}</span><span className="t13" style={{ display: "block" }}>{s.paid_days} days{s.lop_days > 0 ? ` · ${s.lop_days} LOP` : ""}{s.flags.includes(WAGE_FLAG) ? " · wages under 50%" : ""}</span></td>
                            <td className="mono" style={{ textAlign: "right" }}>{formatInr(s.gross, { decimals: false })}</td>
                            <td className="mono" style={{ textAlign: "right" }}>{formatInr(s.pf_employee, { decimals: false })}</td>
                            <td className="mono" style={{ textAlign: "right" }}>{s.esi_applicable ? formatInr(s.esi_employee, { decimals: false }) : "–"}</td>
                            <td className="mono" style={{ textAlign: "right" }}>{formatInr(s.professional_tax, { decimals: false })}</td>
                            <td className="mono" style={{ textAlign: "right" }}>{formatInr(s.tds, { decimals: false })}</td>
                            <td className="mono" style={{ textAlign: "right", fontWeight: 600 }}>{formatInr(s.net_pay, { decimals: false })}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                    {rows.length === 0 && <div className="empty">{flagged ? "No employee is flagged." : run.status === "inputs_open" ? "Compute the run to see payslips." : "No payslips in this run."}</div>}
                  </div>
                )}
              </section>
            </div>

            <aside className="col-side">
              <section className="card card-pad" style={{ display: "flex", flexDirection: "column", gap: 8 }}>
                <h2 style={{ margin: 0 }}>Statutory deposits</h2>
                {run.statutory.length === 0 && <span className="t13">They appear once the run is approved.</span>}
                {run.statutory.map((d) => (
                  <div key={d.kind} style={{ display: "flex", justifyContent: "space-between", padding: "8px 0", borderTop: "1px solid var(--line-2)", fontSize: 14 }}>
                    <span>{d.kind}<span className="t13" style={{ display: "block" }}>Due {shortDate(d.due_date)}</span></span>
                    <span className="mono">{formatInr(d.amount)}</span>
                  </div>
                ))}
              </section>

              <section className="card card-pad" style={{ display: "flex", flexDirection: "column", gap: 8 }}>
                <h2 style={{ margin: 0 }}>Labour compliance</h2>
                <ErrorNote error={labour.error} />
                {labour.loading && <Loading what="labour compliance" />}
                {l && (
                  <>
                    <Item title="Wage Code (50% rule)" status={l.wage_code.status} detail={`${l.wage_code.flagged} of ${l.wage_code.employees} flagged`} />
                    <Item title="Gratuity" status={l.gratuity.status} detail={l.gratuity.applies ? `Estimate ${formatInr(l.gratuity.liability_estimate, { decimals: false })}` : "Under 10 employees"} />
                    <Item title="POSH committee" status={l.posh.status} detail={l.posh.required ? `${l.posh.members} members${l.posh.has_external_member ? "" : ", no external member"}` : "Under 10 employees"} />
                    <Item title="Shops & establishment" status={l.shops_establishment.status} detail={l.shops_establishment.valid_until ? `Valid until ${shortDate(l.shops_establishment.valid_until)}` : ""} />
                    <Item title="Statutory bonus" status={l.statutory_bonus.accrued_employees > 0 ? "ok" : "not_applicable"} detail={`${l.statutory_bonus.eligible} eligible · ${l.statutory_bonus.accrued_employees} accrued`} />
                  </>
                )}
              </section>
            </aside>
          </div>
        </>
      )}
      {!run && !runs.loading && <div className="card empty">No payroll run yet. Start one to compute pay and deposits.</div>}
    </main>
  );
}

function Item({ title, status, detail }: { title: string; status: string; detail: string }) {
  const s = LABOUR_STATUS[status] ?? { label: status, tone: "prep" as const };
  return (
    <div style={{ display: "flex", justifyContent: "space-between", gap: 8, padding: "8px 0", borderTop: "1px solid var(--line-2)", fontSize: 14 }}>
      <span>{title}<span className="t13" style={{ display: "block" }}>{detail}</span></span>
      <Pill tone={s.tone}>{s.label}</Pill>
    </div>
  );
}

function Tile({ label, value, note }: { label: string; value: string; note: string }) {
  return (
    <div className="card kpi">
      <span className="t13">{label}</span>
      <span className="disp v" style={{ fontSize: 28 }}>{value}</span>
      <span className="t13">{note}</span>
    </div>
  );
}
