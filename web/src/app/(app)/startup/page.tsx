"use client";
import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import { api, unwrap } from "@/lib/api";
import { CLOSED, type Obl } from "@/lib/compliance";
import { formatInr, shortDate } from "@/lib/format";
import { useLoad } from "@/lib/hooks";
import type { Employee } from "@/lib/payroll";
import { useSession } from "@/lib/session";
import {
  BENEFIT_STATUS, Benefit, CapTable, Grant, Holder, Pool, Round, ShareClass, STATUS_LABEL, barWidths, benefitsChecked, currentRound, esopSummary, groupIndian, investorUpdate, nextStatus,
  pctText, roundHeadline, roundSummary,
} from "@/lib/startup";
import { AllotmentFormView, ClassFormView, GrantFormView, HolderFormView, PoolFormView, RecordBenefit, RoundFormView } from "@/components/StartupForms";
import { ErrorNote, Loading, Pill } from "@/components/ui";

interface Fin { cash_in_bank: string; net_burn: string | null; runway_months: number | null; revenue: string }
const SETUP: Array<[string, string]> = [["class", "Share class"], ["holder", "Shareholder"], ["allotment", "Allotment"], ["round", "Round"], ["pool", "ESOP pool"], ["grant", "Option grant"]];

export default function Startup() {
  const { entity } = useSession();
  const id = entity?.id ?? null;
  const [post, setPost] = useState(false);
  const [setup, setSetup] = useState<string | null>(null);
  const [editing, setEditing] = useState<string | null>(null);
  const [note, setNote] = useState("");
  const [preview, setPreview] = useState(false);
  const [copied, setCopied] = useState(false);
  const [advanceError, setAdvanceError] = useState<unknown>(null);
  const [advancing, setAdvancing] = useState(false);
  const [checkError, setCheckError] = useState<unknown>(null);
  const checked = useRef<string | null>(null);

  const p = { path: { entityId: id! } };
  const rounds = useLoad(async () => unwrap(await api.GET("/entities/{entityId}/funding-rounds", { params: { ...p, query: { limit: 50 } } })).data as unknown as Round[], id);
  const classes = useLoad(async () => unwrap(await api.GET("/entities/{entityId}/share-classes", { params: p })).data as unknown as ShareClass[], id);
  const holders = useLoad(async () => unwrap(await api.GET("/entities/{entityId}/shareholders", { params: p })).data as unknown as Holder[], id);
  const pools = useLoad(async () => unwrap(await api.GET("/entities/{entityId}/esop-pools", { params: p })).data as unknown as Pool[], id);
  const grants = useLoad(async () => unwrap(await api.GET("/entities/{entityId}/esop-grants", { params: { ...p, query: { limit: 100 } } })).data as unknown as Grant[], id);
  const benefits = useLoad(async () => unwrap(await api.GET("/entities/{entityId}/benefits", { params: p })) as unknown as Benefit[], id);
  const fin = useLoad(async () => unwrap(await api.GET("/entities/{entityId}/finance/summary", { params: p })) as unknown as Fin, id);
  const people = useLoad(async () => unwrap(await api.GET("/entities/{entityId}/employees", { params: { ...p, query: { limit: 200 } } })).data as unknown as Employee[], id);
  const obls = useLoad(async () => unwrap(await api.GET("/entities/{entityId}/obligations", { params: { ...p, query: { limit: 200 } } })).data as unknown as Obl[], id);

  const round = currentRound(rounds.data ?? []);
  const modelled = !!(round && round.target_amount && round.pre_money_valuation && round.share_class_id);
  const scenario = post && modelled ? "post_round" : "current";
  const cap = useLoad(async () => unwrap(await api.GET("/entities/{entityId}/cap-table", { params: { ...p, query: { scenario, ...(scenario === "post_round" ? { round_id: round!.id } : {}) } } })) as unknown as CapTable,
    id ? `${id}|${scenario}|${round?.id ?? ""}|${(rounds.data ?? []).length}` : null);

  // the first visit checks eligibility so the list shows real answers, not "not checked"
  useEffect(() => {
    if (!id || !benefits.data || benefits.data.length === 0 || benefitsChecked(benefits.data) || checked.current === id) return;
    checked.current = id;
    void check();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [id, benefits.data]);

  async function check() {
    setCheckError(null);
    try { unwrap(await api.POST("/entities/{entityId}/benefits/check", { params: p })); benefits.reload(); }
    catch (e) { setCheckError(e); }
  }
  async function advance(r: Round, status: string) {
    setAdvancing(true); setAdvanceError(null);
    try { unwrap(await api.PATCH("/entities/{entityId}/funding-rounds/{id}", { params: { path: { entityId: id!, id: r.id }, header: { "If-Match": "*" } }, body: { status } as never })); rounds.reload(); cap.reload(); }
    catch (e) { setAdvanceError(e); }
    setAdvancing(false);
  }
  const reloadAll = () => { rounds.reload(); classes.reload(); holders.reload(); pools.reload(); grants.reload(); cap.reload(); };

  const table = cap.data;
  const widths = barWidths(table?.rows ?? []);
  const esop = esopSummary(pools.data ?? [], table?.total_fd_shares ?? 0);
  const team = (people.data ?? []).filter((e) => e.status === "active" || e.status === "notice").length;
  const next = round ? nextStatus(round.status) : null;
  const upcoming = (obls.data ?? []).filter((o) => !CLOSED.has(o.status ?? "") && o.due_on).sort((a, b) => (a.due_on ?? "").localeCompare(b.due_on ?? "")).slice(0, 5);
  const month = new Date().toLocaleDateString("en-IN", { month: "long", year: "numeric" });
  const draft = investorUpdate({ company: entity?.legal_name ?? "Our company", month, cash: fin.data?.cash_in_bank ?? null, burn: fin.data?.net_burn ?? null, runway: fin.data?.runway_months ?? null,
    revenue: fin.data?.revenue ?? null, team: people.data ? team : null, roundLine: round ? roundHeadline(round) : null, note });

  async function copy() {
    try { await navigator.clipboard.writeText(draft); setCopied(true); setTimeout(() => setCopied(false), 2000); } catch { setCopied(false); }
  }
  const empty = !cap.loading && (table?.rows ?? []).length === 0;

  return (
    <main className="page">
      <div style={{ display: "flex", flexWrap: "wrap", gap: 16, justifyContent: "space-between", alignItems: "flex-end" }}>
        <div>
          <span className="mono t13">STARTUP TRACK{entity?.legal_name ? ` · ${entity.legal_name.toUpperCase()}` : ""}</span>
          <h1 className="disp" style={{ margin: "6px 0 0", fontSize: 36 }}>{roundHeadline(round)}</h1>
        </div>
        <span style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
          <Link href="/documents/rooms" className="btn" style={{ display: "inline-flex", alignItems: "center", textDecoration: "none" }}>Share data room</Link>
          <button type="button" className="btn primary" onClick={() => { setPreview(true); document.getElementById("update")?.scrollIntoView?.({ behavior: "smooth" }); }}>Draft investor update</button>
        </span>
      </div>

      <section aria-label="Key numbers" className="kpis">
        <Tile label="Cash in bank" value={fin.data ? formatInr(fin.data.cash_in_bank, { decimals: false }) : "…"} note="From your ledger" />
        <Tile label="Net burn" value={fin.data ? (fin.data.net_burn ? `${formatInr(fin.data.net_burn, { decimals: false })} / mo` : "None") : "…"} note="This year so far" />
        <Tile label="Runway" value={fin.data ? (fin.data.runway_months !== null ? `${fin.data.runway_months} mo` : "–") : "…"} note="At the current burn" />
        <Tile label="Team" value={people.data ? String(team) : "…"} note="Active employees" />
        <Tile label="ESOP pool" value={esop.pctOfFd !== null ? `${esop.pctOfFd.toFixed(1)}%` : "–"} note={esop.size ? `${groupIndian(esop.granted)} of ${groupIndian(esop.size)} options granted` : "No pool yet"} />
      </section>
      <ErrorNote error={fin.error ?? rounds.error ?? benefits.error ?? people.error} />

      <section className="card card-pad" style={{ display: "flex", flexDirection: "column", gap: 16 }}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", gap: 12, flexWrap: "wrap" }}>
          <h2 style={{ margin: 0 }}>{round ? `${round.name} · ${STATUS_LABEL[round.status] ?? round.status}` : "Funding round"}</h2>
          {round && <span className="t13">{roundSummary(round)}</span>}
        </div>
        {!round && !rounds.loading && <span className="t13">No round is open. Start one to follow it from term sheet to FC-GPR and see what it does to the cap table.</span>}
        {round && (
          <>
            <ol style={{ margin: 0, padding: 0, listStyle: "none", display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(min(150px, 100%), 1fr))", gap: 10 }}>
              {round.steps.map((s, i) => (
                <li key={s.key} aria-label={`${s.title}: ${s.status === "done" ? "done" : s.status === "in_progress" ? "in progress" : "next"}`}
                    style={{ display: "flex", flexDirection: "column", gap: 8, padding: 14, borderRadius: 12, background: s.status === "in_progress" ? "#E8ECFF" : "var(--paper, #FAFAF7)", border: s.status === "in_progress" ? "1px solid var(--cobalt, #2846E0)" : "1px solid var(--line-2)" }}>
                  <span style={{ display: "flex", alignItems: "center", gap: 8 }}>
                    <span className="mono" style={{ width: 24, height: 24, borderRadius: "50%", display: "flex", alignItems: "center", justifyContent: "center", fontSize: 12, fontWeight: 600, background: s.status === "done" ? "#14692F" : s.status === "in_progress" ? "#2846E0" : "#E2E1DA", color: s.status === "next" ? "#3D424B" : "#fff" }}>{s.status === "done" ? "✓" : i + 1}</span>
                    <span className="mono t13">{s.status === "done" ? "DONE" : s.status === "in_progress" ? "IN PROGRESS" : "NEXT"}</span>
                  </span>
                  <span style={{ fontSize: 14, fontWeight: 600, lineHeight: 1.3 }}>{s.title}</span>
                  <span className="t13">{s.detail}</span>
                </li>
              ))}
            </ol>
            <span style={{ display: "flex", gap: 10, flexWrap: "wrap", alignItems: "center" }}>
              {next && <button type="button" className="btn primary" disabled={advancing} onClick={() => void advance(round, next[0])}>{advancing ? "Saving…" : `Move to ${next[1]}`}</button>}
              <Link href="/board" className="btn" style={{ display: "inline-flex", alignItems: "center", textDecoration: "none" }}>Record the resolution</Link>
              <span className="t13">Dates and timelines here are a starter library; have your company secretary confirm them.</span>
            </span>
            <ErrorNote error={advanceError} />
          </>
        )}
      </section>

      <div className="cols">
        <div className="col-main">
          <section className="card" style={{ overflow: "hidden" }}>
            <div style={{ padding: "18px 20px", display: "flex", justifyContent: "space-between", alignItems: "center", gap: 12, flexWrap: "wrap" }}>
              <h2 style={{ margin: 0 }}>Cap table · fully diluted</h2>
              <div className="seg" role="group" aria-label="Scenario">
                <button type="button" aria-pressed={!post} onClick={() => setPost(false)}>Today</button>
                <button type="button" aria-pressed={post} onClick={() => setPost(true)} disabled={!modelled} title={modelled ? "" : "Give the round an instrument, a target and a pre-money valuation first"}>{round ? `After ${round.name}` : "After the round"}</button>
              </div>
            </div>
            <div style={{ padding: "0 20px" }}><ErrorNote error={cap.error ?? classes.error} /></div>
            {post && table?.round && <div style={{ padding: "0 20px 12px" }} className="t13">New shares: {groupIndian(table.round.new_shares)} at {formatInr(table.round.price_per_share)} each · post-money {formatInr(table.round.post_money, { decimals: false })}</div>}
            {cap.loading && !table ? <Loading what="the cap table" /> : (
              <div className="tablewrap">
                <table style={{ minWidth: 520 }}>
                  <thead><tr><th scope="col">Holder</th><th scope="col">Instrument</th><th scope="col" style={{ width: "30%" }}><span className="sr">Share</span></th><th scope="col" style={{ textAlign: "right" }}>Shares</th><th scope="col" style={{ textAlign: "right" }}>Fully diluted</th></tr></thead>
                  <tbody>
                    {(table?.rows ?? []).map((r, i) => (
                      <tr key={`${r.holder}|${r.class_name}`}>
                        <td style={{ fontWeight: 500 }}>{r.holder}</td><td>{r.class_name}</td>
                        <td><div title={`${r.holder}: ${pctText(r.fd_pct)}`} style={{ height: 10, borderRadius: "0 4px 4px 0", background: "#2846E0", width: `${widths[i]}%` }} /></td>
                        <td className="mono" style={{ textAlign: "right" }}>{groupIndian(r.fd_shares)}</td>
                        <td className="mono" style={{ textAlign: "right", fontWeight: 600 }}>{pctText(r.fd_pct)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
                {empty && <div className="empty">No shares are on the cap table yet. Add a share class and the holders, then record the allotments below.</div>}
              </div>
            )}
          </section>

          <section className="card card-pad" style={{ display: "flex", flexDirection: "column", gap: 12 }}>
            <h2 style={{ margin: 0 }}>ESOP</h2>
            <ErrorNote error={pools.error ?? grants.error} />
            {(pools.data ?? []).map((pl) => <div key={pl.id} style={{ display: "flex", justifyContent: "space-between", gap: 8, fontSize: 14 }}><span>{pl.name}<span className="t13" style={{ display: "block" }}>Approved {shortDate(pl.approved_on)}</span></span><span className="mono">{groupIndian(pl.granted)} of {groupIndian(pl.size_options)} granted</span></div>)}
            {!pools.loading && (pools.data ?? []).length === 0 && <span className="t13">No pool yet. Add one under Set up.</span>}
            {(grants.data ?? []).length > 0 && (
              <div className="tablewrap">
                <table style={{ minWidth: 520 }}>
                  <thead><tr><th scope="col">Employee</th><th scope="col" style={{ textAlign: "right" }}>Options</th><th scope="col" style={{ textAlign: "right" }}>Vested</th><th scope="col">Next vest</th></tr></thead>
                  <tbody>{(grants.data ?? []).map((g) => (
                    <tr key={g.id}><td>{g.employee_name}<span className="t13" style={{ display: "block" }}>Granted {shortDate(g.granted_on)} · strike {formatInr(g.strike_price)}</span></td>
                      <td className="mono" style={{ textAlign: "right" }}>{groupIndian(g.options)}</td><td className="mono" style={{ textAlign: "right" }}>{groupIndian(g.vested_to_date)}</td>
                      <td className="t13">{g.next_vest ? `${shortDate(g.next_vest.date)} · ${groupIndian(g.next_vest.options)}` : "All vested"}</td></tr>
                  ))}</tbody>
                </table>
              </div>
            )}
          </section>
        </div>

        <aside className="col-side">
          <section className="card" style={{ overflow: "hidden" }}>
            <div style={{ padding: "18px 20px", display: "flex", justifyContent: "space-between", alignItems: "center", gap: 8 }}>
              <h2 style={{ margin: 0 }}>Startup India benefits</h2>
              <button type="button" className="btn" onClick={() => void check()}>Check again</button>
            </div>
            <div style={{ padding: "0 20px" }}><ErrorNote error={checkError} /></div>
            {(benefits.data ?? []).length === 0 && !benefits.loading && <div className="empty">No schemes apply to this company&apos;s track.</div>}
            {(benefits.data ?? []).map((b) => {
              const st = BENEFIT_STATUS[b.status] ?? { label: b.status, tone: "prep" as const };
              return (
                <div key={b.scheme_code} style={{ borderTop: "1px solid var(--line-2)" }}>
                  <div style={{ display: "flex", gap: 12, alignItems: "flex-start", padding: "14px 20px" }}>
                    <div style={{ flex: 1, minWidth: 0, display: "flex", flexDirection: "column", gap: 3 }}>
                      <span style={{ fontSize: 14, fontWeight: 600 }}>{b.name}</span>
                      <span className="t13">{b.reason}</span>
                      {b.reference_no && <span className="mono t13">Ref {b.reference_no}{b.valid_until ? ` · valid to ${shortDate(b.valid_until)}` : ""}</span>}
                      <button type="button" className="linkbtn" style={{ color: "var(--cobalt)" }} aria-expanded={editing === b.scheme_code} onClick={() => setEditing(editing === b.scheme_code ? null : b.scheme_code)}>{b.next_action ?? "Record where it stands"}</button>
                    </div>
                    <Pill tone={st.tone}>{st.label}</Pill>
                  </div>
                  {editing === b.scheme_code && id && <RecordBenefit entityId={id} benefit={b} onDone={() => { setEditing(null); void check(); }} onCancel={() => setEditing(null)} />}
                </div>
              );
            })}
          </section>

          <section className="card card-pad" style={{ display: "flex", flexDirection: "column", gap: 12 }}>
            <h2 style={{ margin: 0 }}>Founder calendar</h2>
            {upcoming.length === 0 && !obls.loading && <span className="t13">Nothing is due.</span>}
            {upcoming.map((o) => (
              <Link key={o.id} href={`/compliance?o=${o.id}`} style={{ display: "flex", gap: 14, alignItems: "flex-start", color: "inherit", textDecoration: "none" }}>
                <span className="mono t13" style={{ width: 52, flex: "none", textAlign: "center", padding: "6px 0", borderRadius: 10, background: "var(--paper, #F4F3EE)" }}>{shortDate(o.due_on)}</span>
                <span style={{ fontSize: 14, fontWeight: 600 }}>{o.rule?.title}<span className="t13" style={{ display: "block", fontWeight: 400 }}>{o.rule?.authority_code}</span></span>
              </Link>
            ))}
          </section>
        </aside>
      </div>

      <section id="update" className="card card-pad" style={{ display: "flex", flexDirection: "column", gap: 12 }}>
        <h2 style={{ margin: 0 }}>{month} investor update, drafted from your books</h2>
        <span className="t13">The numbers come from your ledger and payroll. Write the founder note yourself; nothing is sent from here.</span>
        <div className="field"><label htmlFor="note">Founder note</label>
          <textarea id="note" className="input" rows={4} style={{ height: "auto", padding: 10 }} placeholder="Highlights, asks and hires" value={note} onChange={(e) => setNote(e.target.value)} /></div>
        <span style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
          <button type="button" className="btn primary" aria-expanded={preview} onClick={() => setPreview(!preview)}>{preview ? "Hide preview" : "Preview"}</button>
          <button type="button" className="btn" onClick={() => void copy()}>{copied ? "Copied" : "Copy text"}</button>
        </span>
        {preview && <pre aria-label="Investor update preview" style={{ margin: 0, padding: 14, borderRadius: 12, background: "var(--paper, #FAFAF7)", border: "1px solid var(--line)", whiteSpace: "pre-wrap", font: "inherit", fontSize: 14, lineHeight: 1.6 }}>{draft}</pre>}
      </section>

      <section className="card card-pad" style={{ display: "flex", flexDirection: "column", gap: 12 }}>
        <h2 style={{ margin: 0 }}>Set up and record</h2>
        <div className="seg" role="tablist" aria-label="What to record" style={{ flexWrap: "wrap" }}>
          {SETUP.map(([k, l]) => <button key={k} type="button" role="tab" aria-selected={setup === k} onClick={() => setSetup(setup === k ? null : k)}>{l}</button>)}
        </div>
        {!setup && <span className="t13">Start with a share class and your shareholders, then record each allotment. A round, an ESOP pool and option grants come after.</span>}
        {id && setup === "class" && <ClassFormView entityId={id} onDone={reloadAll} />}
        {id && setup === "holder" && <HolderFormView entityId={id} onDone={reloadAll} />}
        {id && setup === "allotment" && <AllotmentFormView entityId={id} classes={classes.data ?? []} holders={holders.data ?? []} rounds={rounds.data ?? []} onDone={reloadAll} />}
        {id && setup === "round" && <RoundFormView entityId={id} classes={classes.data ?? []} onDone={reloadAll} />}
        {id && setup === "pool" && <PoolFormView entityId={id} onDone={reloadAll} />}
        {id && setup === "grant" && <GrantFormView entityId={id} pools={pools.data ?? []} people={people.data ?? []} onDone={reloadAll} />}
      </section>
    </main>
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
