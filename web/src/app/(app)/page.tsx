"use client";
import Link from "next/link";
import { useState } from "react";
import { ApiProblem, api, newIdempotencyKey, unwrap, type ApprovalRequest, type Obligation } from "@/lib/api";
import { addDays, formatInr, greeting, isoDate, shortDate } from "@/lib/format";
import { daysLabel, isOpen, kpis, standing, statusInfo, weekBuckets, type CalDay, type Obl } from "@/lib/compliance";
import { useLoad } from "@/lib/hooks";
import { canOpen, homeTiles, levelOf, roleText } from "@/lib/roles";
import { useSession } from "@/lib/session";
import { HomeTiles } from "@/components/RoleForms";
import { WorkQueue } from "@/components/WorkPanels";
import { ErrorNote, Icon, Loading, Pill } from "@/components/ui";

const SUBJECT_ICON: Record<string, string> = {
  obligation: "M6 3h9l3 3v15H6zM9 12h6M9 16h6",
  vendor_payment: "M3 10l9-6 9 6M5 10v9M19 10v9M3 21h18",
  payroll_run: "M16 11a4 4 0 1 0-8 0M4 21a8 8 0 0 1 16 0",
  board_resolution: "M4 6h16M4 12h16M4 18h10",
};
const SUBJECT_LABEL: Record<string, string> = {
  obligation: "Filing", vendor_payment: "Vendor payment", payroll_run: "Payroll run", board_resolution: "Board resolution",
  sales_document: "Invoice", purchase_bill: "Bill", journal_entry: "Journal", contract: "Contract", credit_note: "Credit note", quote: "Quote", copilot_action: "Copilot suggestion",
};

export default function Today() {
  const { me, entity } = useSession();
  const id = entity?.id ?? null;
  const today = new Date();
  const seesCompliance = canOpen(entity, "compliance");
  const cid = seesCompliance ? id : null;                  // someone without compliance is not asked for it

  const obls = useLoad(async () => unwrap(await api.GET("/entities/{entityId}/obligations", { params: { path: { entityId: id! }, query: { limit: 200 } } })).data as Obligation[], cid);
  const approvals = useLoad(async () => unwrap(await api.GET("/entities/{entityId}/approvals", { params: { path: { entityId: id! }, query: { assigned_to: "me" } } })).data as ApprovalRequest[], id);
  const cal = useLoad(async () => unwrap(await api.GET("/entities/{entityId}/compliance/calendar", { params: { path: { entityId: id! }, query: { from: isoDate(today), to: isoDate(addDays(today, 34)) } } })) as unknown as CalDay[], cid);
  const audit = useLoad(async () => {
    try { return unwrap(await api.GET("/entities/{entityId}/audit-log", { params: { path: { entityId: id! }, query: { limit: 5 } } })).data ?? []; }
    catch (e) { if (e instanceof ApiProblem && (e.status === 403 || e.status === 404)) return []; throw e; }      // not everyone may read the audit trail
  }, id);

  const list = (obls.data ?? []) as Obl[];
  const k = kpis(list);
  const open = approvals.data ?? [];
  const need = open.length;

  return (
    <>
      <header className="top">
        <div className="search" role="search"><span aria-hidden="true">⌕</span><span>Search filings, invoices, people</span></div>
      </header>
      <main className="page">
        <div>
          <span className="muted" style={{ fontSize: 14 }}>{today.toLocaleDateString("en-IN", { weekday: "long", day: "numeric", month: "long", year: "numeric" })}{entity ? ` · ${entity.legal_name} · ${roleText(entity)}` : ""}</span>
          <h1 className="disp" style={{ margin: "6px 0 0", fontSize: 36, fontWeight: 700 }}>
            {greeting(me?.full_name ?? "", today.getHours())}.{" "}
            {obls.loading || approvals.loading ? "" : k.needsAction + need === 0 ? "Nothing needs you today." : `${k.needsAction + need} ${k.needsAction + need === 1 ? "thing needs" : "things need"} you.`}
          </h1>
        </div>

        {!entity ? (
          <div className="card card-pad">You are not a member of any workspace yet. Ask an owner to invite you.</div>
        ) : (
          <>
            <HomeTiles entityId={entity.id!} tiles={homeTiles(entity)} access={entity} />
            <ErrorNote error={obls.error} />
            {seesCompliance && <section aria-label="Key numbers" className="kpis">
              <Kpi label="Due in the next 7 days" value={obls.loading ? "…" : `${k.dueSoon} ${k.dueSoon === 1 ? "filing" : "filings"}`} note="Open obligations" warn={k.dueSoon > 0} />
              <Kpi label="Needs action" value={obls.loading ? "…" : String(k.needsAction)} note="Overdue, awaiting approval or a client" warn={k.needsAction > 0} />
              <Kpi label="Waiting on you" value={approvals.loading ? "…" : String(need)} note="Approvals assigned to you or unassigned" warn={need > 0} />
              <Kpi label="Late fees accrued" value={obls.loading ? "…" : formatInr(k.lateFees, { decimals: false })} note="Across all obligations" warn={Number(k.lateFees) > 0} />
            </section>}

            <div className="cols">
              <div className="col-main">
                <WorkQueue entityId={entity.id!} canAssign={Object.keys(entity.scopes ?? {}).some((m) => levelOf(entity, m) >= 3)} />
                <ApprovalsCard entityId={entity.id!} items={open} loading={approvals.loading} error={approvals.error} reload={() => { approvals.reload(); obls.reload(); }} />

                {seesCompliance && <>
                <section className="card card-pad" style={{ display: "flex", flexDirection: "column", gap: 16 }}>
                  <div style={{ display: "flex", justifyContent: "space-between", flexWrap: "wrap", gap: 12 }}>
                    <h2>Next 5 weeks</h2>
                    <Legend />
                  </div>
                  <ErrorNote error={cal.error} />
                  {cal.loading ? <Loading what="the calendar" /> : (
                    <div className="tablewrap">
                      <div className="weeks">
                        {weekBuckets(cal.data ?? [], today).map((w) => (
                          <div key={w.label}>
                            <div className="weekhead mono">{w.label}</div>
                            {w.days.length === 0 && <div className="t13" style={{ paddingTop: 8 }}>Nothing due</div>}
                            {w.days.flatMap((d) => d.items.map((i) => {
                              const s = statusInfo(i.status);
                              return (
                                <div className="chip" key={`${d.date}${i.obligation_id}`}>
                                  <span className="mono t13" style={{ display: "flex", alignItems: "center", gap: 6 }}><span className={`dot ${s.tone}`} />{d.day} · {s.label}</span>
                                  <span style={{ fontSize: 13, fontWeight: 500 }}>{i.title}</span>
                                </div>
                              );
                            }))}
                          </div>
                        ))}
                      </div>
                    </div>
                  )}
                </section>

                <section className="card" style={{ overflow: "hidden" }}>
                  <div className="cardhead"><h2>Standing with each authority</h2><Link href="/compliance" style={{ fontSize: 14 }}>Open Compliance</Link></div>
                  <div className="tablewrap">
                    <table>
                      <thead><tr><th scope="col">Authority</th><th scope="col">Last filed</th><th scope="col">Open items</th><th scope="col">Status</th></tr></thead>
                      <tbody>
                        {standing(list).map((r) => (
                          <tr key={r.authority}>
                            <td style={{ fontWeight: 500 }}>{r.authority}</td>
                            <td>{r.lastFiled ? shortDate(r.lastFiled) : "—"}</td>
                            <td className="mono">{r.open}</td>
                            <td><Pill tone={r.tone}>{r.label}</Pill></td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                    {!obls.loading && list.length === 0 && <div className="empty">No obligations yet. The engine creates them from your registrations and facts.</div>}
                  </div>
                </section>
                </>}
              </div>

              <div className="col-side">
                {seesCompliance && (
                <section className="card card-pad" style={{ display: "flex", flexDirection: "column", gap: 12 }}>
                  <h2>Due soonest</h2>
                  {list.filter(isOpen).slice(0, 5).map((o) => (
                    <Link key={o.id} href={`/compliance?o=${o.id}`} style={{ textDecoration: "none", color: "inherit" }}>
                      <div style={{ display: "flex", justifyContent: "space-between", gap: 10, fontSize: 14 }}>
                        <span>{o.rule?.title}</span>
                        <span className="mono t13">{shortDate(o.due_on)}</span>
                      </div>
                      <span className="t13">{daysLabel(o.days_left)}</span>
                    </Link>
                  ))}
                  {!obls.loading && list.filter(isOpen).length === 0 && <span className="t13">All clear.</span>}
                </section>

                )}

                <section className="card card-pad" style={{ display: "flex", flexDirection: "column", gap: 12 }}>
                  <h2>Audit trail</h2>
                  {audit.loading && <span className="t13">Loading…</span>}
                  {(audit.data ?? []).map((l) => (
                    <div key={l.id} style={{ display: "flex", gap: 12, fontSize: 14, lineHeight: 1.45 }}>
                      <span className="mono t13" style={{ width: 44, flex: "none" }}>{shortDate(l.at)}</span>
                      <span><strong>{l.actor_name ?? "System"}</strong> {l.action}</span>
                    </div>
                  ))}
                  {!audit.loading && (audit.data ?? []).length === 0 && <span className="t13">Nothing to show, or you do not have access to the audit trail.</span>}
                </section>
              </div>
            </div>
          </>
        )}
      </main>
    </>
  );
}

function Kpi({ label, value, note, warn }: { label: string; value: string; note: string; warn: boolean }) {
  return (
    <div className="card kpi">
      <span className="t13">{label}</span>
      <span className="disp v">{value}</span>
      <span className="t13" style={warn ? { color: "var(--amber)" } : undefined}>{note}</span>
    </div>
  );
}

function Legend() {
  return (
    <div className="t13" style={{ display: "flex", gap: 14, flexWrap: "wrap", color: "var(--ink-2)" }}>
      <span><span className="dot ok" /> Filed</span> <span><span className="dot wait" /> Ready / upcoming</span> <span><span className="dot due" /> Needs action</span>
    </div>
  );
}

function ApprovalsCard({ entityId, items, loading, error, reload }: { entityId: string; items: ApprovalRequest[]; loading: boolean; error: unknown; reload: () => void }) {
  const [busy, setBusy] = useState<string | null>(null);
  const [failure, setFailure] = useState<unknown>(null);

  async function decide(a: ApprovalRequest, verdict: "approve" | "reject") {
    setBusy(a.id ?? ""); setFailure(null);
    try {
      unwrap(await api.POST(verdict === "approve" ? "/entities/{entityId}/approvals/{id}/approve" : "/entities/{entityId}/approvals/{id}/reject", {
        params: { path: { entityId, id: a.id! }, header: { "Idempotency-Key": newIdempotencyKey() } }, body: {},
      }));
      reload();
    } catch (e) {
      setFailure(e instanceof ApiProblem && e.code === "two_person_rule" ? new Error("You raised this request, so someone else has to decide it.") : e);
    } finally {
      setBusy(null);
    }
  }

  return (
    <section className="card" style={{ overflow: "hidden" }}>
      <div className="cardhead"><h2>Waiting on you</h2><Link href="/compliance" style={{ fontSize: 14 }}>Open Compliance</Link></div>
      <div style={{ padding: "0 20px" }}><ErrorNote error={error ?? failure} /></div>
      {loading && <Loading what="approvals" />}
      {!loading && items.length === 0 && <div className="empty">Nothing is waiting for your decision.</div>}
      {items.map((a) => (
        <div className="row" key={a.id}>
          <span className="icon" style={{ background: "var(--amber-soft)", color: "var(--amber)" }}><Icon d={SUBJECT_ICON[a.subject ?? ""] ?? "M5 12l5 5 9-10"} /></span>
          <div className="grow">
            <span className="t15">{a.summary || SUBJECT_LABEL[a.subject ?? ""] || "Approval"}</span>
            <span className="t13">{SUBJECT_LABEL[a.subject ?? ""] ?? a.subject}{a.amount ? ` · ${formatInr(a.amount as unknown as string)}` : ""}</span>
          </div>
          {a.due_on && <span className="mono t13">{shortDate(a.due_on)}</span>}
          <button type="button" className="btn" disabled={busy === a.id} onClick={() => void decide(a, "reject")}>Reject</button>
          <button type="button" className="btn primary" disabled={busy === a.id} onClick={() => void decide(a, "approve")}>Approve</button>
        </div>
      ))}
    </section>
  );
}
