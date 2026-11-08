"use client";
import { Suspense, useMemo, useState } from "react";
import { useSearchParams } from "next/navigation";
import { api, unwrap, type Obligation } from "@/lib/api";
import { formatInr, isoDate, shortDate } from "@/lib/format";
import { CLOSED, clauseText, daysLabel, monthGrid, reasonLines, statusInfo, type CalDay, type Clause } from "@/lib/compliance";
import { useLoad } from "@/lib/hooks";
import { useSession } from "@/lib/session";
import { ErrorNote, Loading, Pill, StatusPill } from "@/components/ui";

type Tab = "list" | "calendar" | "thresholds";
const STATUS_FILTERS: Array<[string, string]> = [["open", "Open"], ["needs", "Needs action"], ["filed", "Filed"], ["all", "All"]];

export default function CompliancePage() {
  return <Suspense fallback={<Loading what="compliance" />}><Compliance /></Suspense>;
}

function Compliance() {
  const { entity } = useSession();
  const id = entity?.id ?? null;
  const [tab, setTab] = useState<Tab>("list");
  const [authority, setAuthority] = useState("All");
  const [status, setStatus] = useState("open");
  const [selected, setSelected] = useState<string | null>(useSearchParams().get("o"));
  const [running, setRunning] = useState(false);
  const [runError, setRunError] = useState<unknown>(null);

  const obls = useLoad(async () => unwrap(await api.GET("/entities/{entityId}/obligations", { params: { path: { entityId: id! }, query: { limit: 200 } } })).data as Obligation[], id);
  const rows = obls.data ?? [];

  const authorities = useMemo(() => ["All", ...[...new Set(rows.map((r) => r.rule?.authority_code ?? "Other"))].sort()], [rows]);
  const shown = rows.filter((r) => {
    if (authority !== "All" && (r.rule?.authority_code ?? "Other") !== authority) return false;
    const s = r.status ?? "";
    if (status === "open") return !CLOSED.has(s);
    if (status === "needs") return ["overdue", "awaiting_approval", "awaiting_client"].includes(s);
    if (status === "filed") return s === "filed";
    return true;
  });
  const current = rows.find((r) => r.id === selected) ?? shown[0] ?? null;

  async function runEngine() {
    setRunning(true); setRunError(null);
    try {
      unwrap(await api.POST("/entities/{entityId}/compliance/engine-runs", { params: { path: { entityId: id! } } }));
      obls.reload();
    } catch (e) {
      setRunError(e);
    } finally {
      setRunning(false);
    }
  }

  return (
    <main className="page">
      <div style={{ display: "flex", flexWrap: "wrap", gap: 16, justifyContent: "space-between", alignItems: "flex-end" }}>
        <div>
          <span className="muted" style={{ fontSize: 14 }}>{entity?.legal_name}</span>
          <h1 className="disp" style={{ margin: "6px 0 0", fontSize: 36 }}>Compliance Engine</h1>
        </div>
        <div style={{ display: "flex", gap: 10, flexWrap: "wrap" }}>
          <div className="seg" role="tablist" aria-label="View">
            {(["list", "calendar", "thresholds"] as Tab[]).map((t) => (
              <button key={t} type="button" role="tab" aria-selected={tab === t} onClick={() => setTab(t)}>{t === "list" ? "Obligations" : t === "calendar" ? "Calendar" : "Thresholds"}</button>
            ))}
          </div>
          <button type="button" className="btn" disabled={running || !id} onClick={() => void runEngine()}>{running ? "Running…" : "Re-run engine"}</button>
        </div>
      </div>
      <ErrorNote error={runError} />
      {!entity && <div className="card card-pad">Select a workspace to see its obligations.</div>}
      {entity && tab === "list" && (
        <>
          <ErrorNote error={obls.error} />
          <div style={{ display: "flex", gap: 12, flexWrap: "wrap" }}>
            <div className="seg" role="group" aria-label="Authority">
              {authorities.map((a) => <button key={a} type="button" aria-pressed={authority === a} onClick={() => setAuthority(a)}>{a}</button>)}
            </div>
            <div className="seg" role="group" aria-label="Status">
              {STATUS_FILTERS.map(([k, l]) => <button key={k} type="button" aria-pressed={status === k} onClick={() => setStatus(k)}>{l}</button>)}
            </div>
          </div>
          <div className="cols">
            <section className="card col-main" style={{ overflow: "hidden", gap: 0 }}>
              {obls.loading ? <Loading what="obligations" /> : (
                <div className="tablewrap">
                  <table>
                    <thead><tr><th scope="col">Obligation</th><th scope="col">Authority</th><th scope="col">Due</th><th scope="col">Status</th></tr></thead>
                    <tbody>
                      {shown.map((o) => (
                        <tr key={o.id} className={`click ${current?.id === o.id ? "sel" : ""}`} onClick={() => setSelected(o.id ?? null)}>
                          <td>
                            <button type="button" onClick={() => setSelected(o.id ?? null)} style={{ all: "unset", cursor: "pointer", display: "block" }}>
                              <span className="t15" style={{ display: "block" }}>{o.rule?.title}</span>
                              <span className="t13">{o.period_label}{o.registration?.number ? ` · ${o.registration.number}` : ""}</span>
                            </button>
                          </td>
                          <td>{o.rule?.authority_code}</td>
                          <td className="mono">{shortDate(o.due_on)}<span className="t13" style={{ display: "block" }}>{daysLabel(o.days_left)}</span></td>
                          <td><StatusPill status={o.status} /></td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                  {shown.length === 0 && <div className="empty">Nothing matches these filters.</div>}
                </div>
              )}
            </section>
            <aside className="col-side">{current ? <Detail o={current} /> : <div className="card card-pad t13">Select an obligation to see why it applies.</div>}</aside>
          </div>
        </>
      )}
      {entity && tab === "calendar" && id && <CalendarTab entityId={id} />}
      {entity && tab === "thresholds" && id && <Thresholds entityId={id} />}
    </main>
  );
}

function Detail({ o }: { o: Obligation }) {
  const reason = (o.reason ?? {}) as Record<string, unknown>;
  const facts: Array<[string, string]> = [
    ["STATUTE", o.rule?.statute_ref ?? "—"],
    ["FORM", o.rule?.form_code ?? "—"],
    ["PERIOD", `${o.period_label ?? ""} (${shortDate(o.period_start)} – ${shortDate(o.period_end)})`],
    ["DUE", `${shortDate(o.due_on)}${o.extended_due_date ? ` (extended from ${shortDate(o.due_date)})` : ""}`],
    ["LATE FEE", formatInr(o.late_fee_accrued ?? "0.00")],
    ["INTEREST", formatInr(o.interest_accrued ?? "0.00")],
  ];
  return (
    <section className="card card-pad" style={{ display: "flex", flexDirection: "column", gap: 16 }}>
      <div style={{ display: "flex", justifyContent: "space-between", gap: 10, alignItems: "flex-start" }}>
        <h2>{o.rule?.title}</h2>
        <StatusPill status={o.status} />
      </div>
      <dl className="facts">{facts.map(([k, v]) => <div key={k} style={{ display: "contents" }}><dt className="mono">{k}</dt><dd>{v}</dd></div>)}</dl>
      <div>
        <span className="mono t13">WHY IT APPLIES</span>
        {Object.keys(reason).length === 0 ? <p className="t13">No reason was recorded for this obligation.</p> : (
          <>
            <dl className="facts" style={{ marginTop: 8 }}>
              {reasonLines(reason).map((l) => <div key={l.label} style={{ display: "contents" }}><dt className="mono">{l.label.toUpperCase()}</dt><dd>{l.text}</dd></div>)}
            </dl>
            {Array.isArray(reason.applicability) && reason.applicability.length > 0 && (
              <div style={{ marginTop: 10, display: "flex", flexDirection: "column", gap: 6 }}>
                <span className="t13">It applies to you when:</span>
                {(reason.applicability as Clause[]).map((c, i) => {
                  const t = clauseText(c);
                  return <div key={i} style={{ fontSize: 14, display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}><Pill tone={t.met ? "ok" : "due"}>{t.met ? "Met" : "Not met"}</Pill>{t.text}{!t.met || c.missing ? <span className="t13">· {t.note}</span> : null}</div>;
                })}
              </div>
            )}
          </>
        )}
      </div>
      <p className="t13" style={{ margin: 0 }}>Dates, fees and thresholds are a starter library and need review by a CA or CS before you rely on them.</p>
    </section>
  );
}

function CalendarTab({ entityId }: { entityId: string }) {
  const [cursor, setCursor] = useState(() => { const n = new Date(); return { y: n.getFullYear(), m: n.getMonth() }; });
  const from = isoDate(new Date(cursor.y, cursor.m, 1));
  const to = isoDate(new Date(cursor.y, cursor.m + 1, 0));
  const cal = useLoad(async () => unwrap(await api.GET("/entities/{entityId}/compliance/calendar", { params: { path: { entityId }, query: { from, to } } })) as unknown as CalDay[], `${entityId}${from}`);
  const byDay = new Map((cal.data ?? []).map((d) => [d.date, d.items]));
  const grid = monthGrid(cursor.y, cursor.m);
  const todayIso = isoDate(new Date());
  const label = new Date(cursor.y, cursor.m, 1).toLocaleDateString("en-IN", { month: "long", year: "numeric" });
  const step = (n: number) => setCursor((c) => { const d = new Date(c.y, c.m + n, 1); return { y: d.getFullYear(), m: d.getMonth() }; });

  return (
    <section className="card card-pad" style={{ display: "flex", flexDirection: "column", gap: 16 }}>
      <div style={{ display: "flex", gap: 10, alignItems: "center", flexWrap: "wrap" }}>
        <button type="button" className="btn" aria-label="Previous month" onClick={() => step(-1)}>‹</button>
        <h2 style={{ minWidth: 160, textAlign: "center" }}>{label}</h2>
        <button type="button" className="btn" aria-label="Next month" onClick={() => step(1)}>›</button>
      </div>
      <ErrorNote error={cal.error} />
      <div className="tablewrap">
        <div className="month" role="grid" aria-label={label}>
          {["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"].map((d) => <div key={d} className="mono t13" role="columnheader">{d}</div>)}
          {grid.flat().map((day, i) => {
            if (day === null) return <div key={i} className="cell empty-cell" />;
            const iso = isoDate(new Date(cursor.y, cursor.m, day));
            const items = byDay.get(iso) ?? [];
            return (
              <div key={i} className={`cell ${iso === todayIso ? "today" : ""}`} role="gridcell">
                <span className="mono" style={{ fontWeight: 600 }}>{day}</span>
                {items.map((it) => { const s = statusInfo(it.status); return <span key={it.obligation_id} style={{ display: "flex", gap: 6, alignItems: "flex-start" }}><span className={`dot ${s.tone}`} style={{ marginTop: 4 }} /><span>{it.title}</span></span>; })}
              </div>
            );
          })}
        </div>
      </div>
    </section>
  );
}

function Thresholds({ entityId }: { entityId: string }) {
  const t = useLoad(async () => unwrap(await api.GET("/entities/{entityId}/compliance/thresholds", { params: { path: { entityId } } })) as unknown as Array<{ rule_code: string; title: string; fact_key: string; threshold: number; current_value: number; pct: number; note?: string }>, entityId);
  return (
    <section className="card" style={{ overflow: "hidden" }}>
      <div className="cardhead"><h2>Thresholds you are approaching</h2></div>
      <ErrorNote error={t.error} />
      {t.loading && <Loading what="thresholds" />}
      {!t.loading && (t.data ?? []).length === 0 && <div className="empty">No thresholds are being watched yet. Facts such as turnover and headcount feed these.</div>}
      {(t.data ?? []).map((w) => (
        <div className="row" key={`${w.rule_code}${w.fact_key}`}>
          <div className="grow">
            <span className="t15">{w.title}</span>
            <span className="t13">{w.note ?? w.fact_key}</span>
          </div>
          <div style={{ flex: "1 1 220px" }}>
            <div className="bar" role="progressbar" aria-valuenow={Math.min(Math.round(w.pct), 100)} aria-valuemin={0} aria-valuemax={100} aria-label={w.title}><span style={{ width: `${Math.min(w.pct, 100)}%` }} className={w.pct >= 90 ? "hot" : ""} /></div>
            <span className="mono t13">{w.current_value.toLocaleString("en-IN")} of {w.threshold.toLocaleString("en-IN")} · {Math.round(w.pct)}%</span>
          </div>
        </div>
      ))}
    </section>
  );
}
