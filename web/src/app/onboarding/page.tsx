"use client";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import { ApiProblem, api, newIdempotencyKey, unwrap } from "@/lib/api";
import type { components } from "@/api/schema";
import { formatInr, shortDate } from "@/lib/format";
import {
  DEFAULT_ANSWERS, HEADCOUNT, LOOKUP_GIVE_UP_MS, LOOKUP_POLL_MS, MSE, TRACK_KEY, TURNOVER, answersBody, hints, identifierKind, isLookupFinished,
  isTrack, normaliseIdentifier, type Answers, type Track,
} from "@/lib/onboarding";
import { orgFromSearch } from "@/lib/group";
import { useSession } from "@/lib/session";
import { ErrorNote, Logo } from "@/components/ui";

type Lookup = components["schemas"]["RegistryLookup"];
type Preview = components["schemas"]["ObligationPreview"];
type Created = components["schemas"]["WorkspaceCreated"];

const STEPS = ["Identify", "Confirm", "Shape", "Your graph"];
const SOURCES: Array<[string, string]> = [
  ["MCA", "Company master data, directors and filing history"],
  ["GSTN", "Every GSTIN linked to your PAN, with state and filing frequency"],
  ["Udyam", "MSME registration and classification, if one exists"],
  ["Income Tax", "TAN status: once you connect, deductions and 26AS"],
];
const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

export default function Onboarding() {
  const { status, me, reload } = useSession();
  const router = useRouter();
  const [step, setStep] = useState(1);
  const [ident, setIdent] = useState("");
  const [lookup, setLookup] = useState<Lookup | null>(null);
  const [looking, setLooking] = useState(false);
  const [picked, setPicked] = useState<Record<string, boolean>>({});
  const [answers, setAnswers] = useState<Answers>(DEFAULT_ANSWERS);
  const [track, setTrack] = useState<Track>("business");
  const [preview, setPreview] = useState<Preview | null>(null);
  const [previewError, setPreviewError] = useState<unknown>(null);
  const [error, setError] = useState<unknown>(null);
  const [creating, setCreating] = useState(false);
  const [created, setCreated] = useState<Created | null>(null);
  const [orgId, setOrgId] = useState<string | null>(null);
  const keyRef = useRef<{ body: string; key: string } | null>(null);
  const alive = useRef(true);

  useEffect(() => { alive.current = true; return () => { alive.current = false; }; }, []);
  useEffect(() => { if (status === "anon") router.replace("/login"); }, [status, router]);
  // a firm adding a client arrives as /onboarding?org=<its id>; the new company is filed under that firm
  useEffect(() => { setOrgId(orgFromSearch(window.location.search)); }, []);
  useEffect(() => {
    try { const t = window.sessionStorage.getItem(TRACK_KEY); if (isTrack(t)) setTrack(t); } catch { /* default stays */ }
  }, []);

  const gstins = (lookup?.gstins ?? []).filter((g) => picked[g.gstin ?? ""]).map((g) => g.gstin as string);
  const body = lookup?.id ? answersBody(lookup.id, track, gstins, answers, orgId) : null;
  const bodyJson = body ? JSON.stringify(body) : null;

  // live preview: re-evaluate the rules a moment after the last change on step 3
  useEffect(() => {
    if (step !== 3 || !body) return;
    let cancelled = false;
    const t = setTimeout(async () => {
      try {
        const p = unwrap(await api.POST("/onboarding/preview", { body }));
        if (!cancelled) { setPreview(p); setPreviewError(null); }
      } catch (e) {
        if (!cancelled) setPreviewError(e);
      }
    }, 250);
    return () => { cancelled = true; clearTimeout(t); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [step, bodyJson]);

  async function findCompany() {
    const id = normaliseIdentifier(ident);
    setError(null);
    if (!identifierKind(id)) { setError(new Error("That does not look like a CIN, LLPIN, GSTIN or PAN. Check it and try again.")); return; }
    setLooking(true);
    try {
      let lk = unwrap(await api.POST("/onboarding/lookups", { body: { identifier: id } })) as Lookup;
      const until = Date.now() + LOOKUP_GIVE_UP_MS;
      while (!isLookupFinished(lk.status) && alive.current) {
        if (Date.now() > until) throw new Error("The registry is taking too long. Try again in a minute.");
        await sleep(LOOKUP_POLL_MS);
        lk = unwrap(await api.GET("/onboarding/lookups/{id}", { params: { path: { id: lk.id! } } })) as Lookup;
      }
      if (!alive.current) return;
      if (lk.status === "not_found") throw new Error("No company was found for that number. Check it, or try your GSTIN or PAN.");
      if (lk.status === "failed") throw new Error("The registry could not be reached. Try again in a minute.");
      setLookup(lk);
      setPicked(Object.fromEntries((lk.gstins ?? []).map((g) => [g.gstin ?? "", true])));
      setStep(2);
    } catch (e) {
      setError(e instanceof ApiProblem && e.status === 400 ? new Error("That does not look like a CIN, LLPIN, GSTIN or PAN.") : e);
    } finally {
      setLooking(false);
    }
  }

  async function create() {
    if (!body || !bodyJson) return;
    setCreating(true); setError(null);
    // the same answers reuse the same key, so a retry after a dropped connection cannot create the workspace twice
    if (keyRef.current?.body !== bodyJson) keyRef.current = { body: bodyJson, key: newIdempotencyKey() };
    try {
      const out = unwrap(await api.POST("/onboarding/workspaces", { body, params: { header: { "Idempotency-Key": keyRef.current.key } } }));
      setCreated(out);
      setStep(4);
    } catch (e) {
      setError(e instanceof ApiProblem && e.status === 409 && e.code === "conflict"
        ? new Error("A workspace for this company already exists. Ask its owner to invite you.") : e);
    } finally {
      setCreating(false);
    }
  }

  async function openCommandCenter() {
    if (orgId) { await reload(); router.push("/group"); return; }   // a firm stays on its group view
    if (created?.entity_id) await reload(created.entity_id);
    router.push("/");
  }

  if (status !== "authed") return <div className="empty" role="status">Loading…</div>;

  const ent = lookup?.entity;
  const maxN = Math.max(...(preview?.by_authority ?? []).map((a) => a.count ?? 0), 1);
  const facts: Array<[string, string]> = ent ? [
    ["Incorporated", ent.incorporated_on ? `${shortDate(ent.incorporated_on)} ${ent.incorporated_on.slice(0, 4)}` : "—"],
    ["Registered office", ent.registered_address ?? "—"],
    ["Paid-up capital", ent.paid_up_capital ? formatInr(ent.paid_up_capital, { decimals: false }) : "—"],
    ["Directors", (ent.directors ?? []).map((d) => d.name).join(", ") || "—"],
    ["Last AGM", ent.last_agm_on ? `${shortDate(ent.last_agm_on)} ${ent.last_agm_on.slice(0, 4)}` : "—"],
    ["Industry (NIC)", ent.nic_code ?? "—"],
  ] : [];

  return (
    <div style={{ minHeight: "100vh" }}>
      <header style={{ borderBottom: "1px solid var(--line)", background: "#fff" }}>
        <div style={{ maxWidth: 1120, margin: "0 auto", padding: "0 24px", minHeight: 68, display: "flex", flexWrap: "wrap", alignItems: "center", gap: 24 }}>
          <span style={{ display: "flex", alignItems: "center", gap: 10, color: "var(--ink)" }}>
            <svg width="26" height="26" viewBox="0 0 28 28" aria-hidden="true"><circle cx="14" cy="14" r="10" fill="none" stroke="#14161A" strokeWidth="3.2" strokeDasharray="47 16" strokeLinecap="round" /><circle cx="21.1" cy="6.9" r="2.8" fill="#2846E0" /></svg>
            <b className="disp" style={{ fontSize: 20 }}>Biz360X</b>
          </span>
          <ol className="steps" aria-label="Setup steps">
            {STEPS.map((t, i) => {
              const n = i + 1;
              return <li key={t} aria-current={n === step ? "step" : undefined} className={n < step ? "done" : ""}><span className="n mono">{n < step ? "✓" : n}</span>{t}</li>;
            })}
          </ol>
        </div>
      </header>

      <main className="ob">
        {step === 1 && (
          <div style={{ display: "flex", flexWrap: "wrap", gap: 56, alignItems: "flex-start" }}>
            <form style={{ flex: "3 1 480px", minWidth: 0, display: "flex", flexDirection: "column", gap: 24 }} onSubmit={(e) => { e.preventDefault(); void findCompany(); }}>
              <span className="mono t13">STEP 1 OF 4</span>
              <h1 className="disp" style={{ fontSize: 56 }}>Start with one number.</h1>
              <p className="lead">Give us any identifier your business has. We read the public registries and draft your obligation graph. You confirm every detail before anything is used.</p>
              <div className="field" style={{ maxWidth: 560 }}>
                <label htmlFor="ident">CIN, LLPIN, GSTIN or PAN</label>
                <div style={{ display: "flex", flexWrap: "wrap", gap: 10 }}>
                  <input id="ident" className="input mono" style={{ flex: "1 1 300px", height: 56, fontSize: 17 }} value={ident} onChange={(e) => setIdent(e.target.value)}
                         placeholder="U12345KA2019PTC123456" autoCapitalize="characters" autoComplete="off" autoFocus />
                  <button type="submit" className="btn primary" style={{ height: 56, padding: "0 24px", fontSize: 16, borderRadius: 12 }} disabled={looking || !ident.trim()}>{looking ? "Looking up…" : "Look up"}</button>
                </div>
                {looking && <span className="t13" role="status">Reading the registries. This usually takes a few seconds.</span>}
                {process.env.NODE_ENV !== "production" && <span className="t13">Development server: try <span className="mono">U72900MH2022PTC999999</span>.</span>}
              </div>
              <ErrorNote error={error} />
            </form>
            <aside className="panel" style={{ flex: "2 1 320px", minWidth: 0 }}>
              <h2>What we look up</h2>
              {SOURCES.map(([s, d]) => (
                <div key={s} style={{ display: "flex", gap: 12, alignItems: "flex-start", paddingTop: 14, borderTop: "1px solid var(--line-2)" }}>
                  <span className="mono" style={{ fontSize: 12, color: "var(--cobalt)", width: 76, flex: "none", paddingTop: 2 }}>{s}</span>
                  <span style={{ fontSize: 14, lineHeight: 1.5, color: "var(--ink-2)" }}>{d}</span>
                </div>
              ))}
              <p style={{ margin: 0, padding: 14, borderRadius: 12, background: "var(--paper)", fontSize: 13, lineHeight: 1.5, color: "var(--ink-2)" }}>
                Read-only. Nothing is filed, and no portal credentials are asked for, until you connect them later.
              </p>
            </aside>
          </div>
        )}

        {step === 2 && ent && (
          <>
            <div>
              <span className="mono t13">STEP 2 OF 4</span>
              <h1 className="disp" style={{ marginTop: 12 }}>Is this your company?</h1>
            </div>
            <div style={{ display: "flex", flexWrap: "wrap", gap: 24, alignItems: "flex-start" }}>
              <section className="panel" style={{ flex: "3 1 480px", minWidth: 0, padding: 0, gap: 0, overflow: "hidden" }}>
                <div style={{ padding: 24, borderBottom: "1px solid var(--line-2)" }}>
                  <span className="disp" style={{ fontSize: 26, fontWeight: 700, display: "block" }}>{ent.legal_name}</span>
                  <span className="mono t13">{lookup?.identifier} · {(ent.constitution ?? "").replace(/_/g, " ")}</span>
                </div>
                <dl style={{ margin: 0, display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(min(220px, 100%), 1fr))" }}>
                  {facts.map(([k, v]) => (
                    <div key={k} style={{ padding: "16px 24px", borderBottom: "1px solid var(--line-2)" }}>
                      <dt className="t13">{k}</dt><dd style={{ margin: "4px 0 0", fontSize: 15, fontWeight: 500 }}>{v}</dd>
                    </div>
                  ))}
                </dl>
              </section>
              <section className="panel" style={{ flex: "2 1 320px", minWidth: 0 }}>
                <h2>GST registrations on this PAN</h2>
                <span className="t13">Untick any you do not want Biz360X to manage.</span>
                {(lookup?.gstins ?? []).length === 0 && <span className="t13">No GST registrations were found.</span>}
                {(lookup?.gstins ?? []).map((g) => (
                  <label key={g.gstin} className={`gstbox ${picked[g.gstin ?? ""] ? "on" : ""}`}>
                    <input type="checkbox" checked={!!picked[g.gstin ?? ""]} onChange={(e) => setPicked({ ...picked, [g.gstin ?? ""]: e.target.checked })} style={{ width: 18, height: 18, accentColor: "#14161A" }} />
                    <span style={{ display: "flex", flexDirection: "column", gap: 2 }}>
                      <span className="mono" style={{ fontSize: 14, fontWeight: 500 }}>{g.gstin}</span>
                      <span className="t13">{g.label ?? g.state_code} · {g.status}</span>
                    </span>
                  </label>
                ))}
              </section>
            </div>
            <div style={{ display: "flex", gap: 10, flexWrap: "wrap" }}>
              <button type="button" className="btn" style={{ height: 52, padding: "0 22px", fontSize: 16 }} onClick={() => setStep(1)}>Back</button>
              <button type="button" className="btn primary" style={{ height: 52, padding: "0 24px", fontSize: 16 }} onClick={() => setStep(3)}>Yes, that is us</button>
            </div>
          </>
        )}

        {step === 3 && (
          <div style={{ display: "flex", flexWrap: "wrap", gap: 40, alignItems: "flex-start" }}>
            <div style={{ flex: "3 1 520px", minWidth: 0, display: "flex", flexDirection: "column", gap: 28 }}>
              <div>
                <span className="mono t13">STEP 3 OF 4</span>
                <h1 className="disp" style={{ marginTop: 12 }}>Four things the registries do not know.</h1>
              </div>
              <Question legend="How many people are on payroll?" hint={hints.headcount(answers.headcount)} options={HEADCOUNT} value={answers.headcount} onPick={(v) => setAnswers({ ...answers, headcount: v })} />
              <Question legend="Turnover last financial year" hint={hints.turnover(answers.turnover)} options={TURNOVER} value={answers.turnover} onPick={(v) => setAnswers({ ...answers, turnover: v })} />
              <Question legend="Any foreign shareholders?" hint={hints.foreign(answers.foreign)} options={[["no", "No"], ["yes", "Yes"]]} value={answers.foreign ? "yes" : "no"} onPick={(v) => setAnswers({ ...answers, foreign: v === "yes" })} />
              <Question legend="Do you buy from micro or small enterprises?" hint={hints.mse(answers.mse)} options={MSE} value={answers.mse} onPick={(v) => setAnswers({ ...answers, mse: v })} />
              <ErrorNote error={error} />
              <div style={{ display: "flex", gap: 10, flexWrap: "wrap" }}>
                <button type="button" className="btn" style={{ height: 52, padding: "0 22px", fontSize: 16 }} onClick={() => setStep(2)}>Back</button>
                <button type="button" className="btn primary" style={{ height: 52, padding: "0 24px", fontSize: 16 }} disabled={creating} onClick={() => void create()}>{creating ? "Building…" : "Build my graph"}</button>
              </div>
            </div>
            <aside className="dark" aria-live="polite" style={{ flex: "2 1 300px", minWidth: 0 }}>
              <span className="mono" style={{ fontSize: 11, color: "#8A909A" }}>LIVE PREVIEW</span>
              <div style={{ display: "flex", alignItems: "baseline", gap: 10 }}>
                <span className="disp" style={{ fontSize: 64, fontWeight: 700, lineHeight: 1 }}>{preview ? preview.total : "…"}</span>
                <span style={{ fontSize: 15, color: "#B7BCC5" }}>obligations this FY</span>
              </div>
              {previewError ? <span style={{ fontSize: 13, color: "#FFC58A" }}>The preview could not be updated.</span> : null}
              <div style={{ display: "flex", flexDirection: "column", gap: 10, borderTop: "1px solid #23272F", paddingTop: 14 }}>
                {(preview?.by_authority ?? []).map((b) => (
                  <div key={b.authority} className="authbar">
                    <span style={{ fontSize: 13, color: "#C7CBD2" }}>{b.authority}</span>
                    <div className="track"><div style={{ width: `${Math.round(((b.count ?? 0) / maxN) * 100)}%` }} /></div>
                    <span className="mono" style={{ fontSize: 13, textAlign: "right" }}>{b.count}</span>
                  </div>
                ))}
              </div>
            </aside>
          </div>
        )}

        {step === 4 && created && (
          <>
            <div style={{ maxWidth: 760, display: "flex", flexDirection: "column", gap: 12 }}>
              <span className="mono t13">STEP 4 OF 4 · ENGINE RUN COMPLETE</span>
              <h1 className="disp" style={{ fontSize: 56 }}>
                {created.obligations_created} {created.obligations_created === 1 ? "obligation" : "obligations"} on your calendar.
              </h1>
              <p className="lead">Every one carries its statute and the reason it applies to you. Dates and thresholds are a starter library: have your CA or CS review them before you rely on them.</p>
            </div>
            <div style={{ display: "flex", flexWrap: "wrap", gap: 24, alignItems: "flex-start" }}>
              <section className="panel" style={{ flex: "1 1 340px", minWidth: 0 }}>
                <h2>Switched on because…</h2>
                {(preview?.triggers ?? []).map((t, i) => (
                  <div key={i} style={{ display: "flex", gap: 12, fontSize: 14, lineHeight: 1.45, paddingTop: 12, borderTop: "1px solid var(--line-2)" }}>
                    <span style={{ flex: 1, color: "var(--ink-2)" }}>{t.why}</span><span style={{ fontWeight: 600, textAlign: "right" }}>{t.what}</span>
                  </div>
                ))}
              </section>
              <section className="panel" style={{ flex: "1 1 340px", minWidth: 0 }}>
                <h2>Due first</h2>
                {(preview?.first_due ?? []).map((f, i) => (
                  <div key={i} style={{ display: "flex", gap: 14, alignItems: "center", paddingTop: 12, borderTop: "1px solid var(--line-2)" }}>
                    <span className="mono" style={{ width: 54, flex: "none", fontSize: 13, fontWeight: 600, color: "var(--amber)" }}>{shortDate(f.due_date)}</span>
                    <span style={{ display: "flex", flexDirection: "column", gap: 2 }}><span style={{ fontSize: 15, fontWeight: 500 }}>{f.title}</span><span className="t13">{f.authority}</span></span>
                  </div>
                ))}
                {(preview?.first_due ?? []).length === 0 && <span className="t13">Nothing falls due in the next few weeks.</span>}
              </section>
            </div>
            <div style={{ display: "flex", gap: 10, flexWrap: "wrap", alignItems: "center" }}>
              <button type="button" className="go" style={{ width: "auto", height: 56, padding: "0 28px", fontSize: 17 }} onClick={() => void openCommandCenter()}>{orgId ? "Back to the group view" : "Open Command Center"}</button>
            </div>
          </>
        )}

        {(me?.entities ?? []).length > 0 && step === 1 && <Link href={orgId ? "/group" : "/"} style={{ fontSize: 14 }}>{orgId ? "Back to the group view" : "Back to your workspace"}</Link>}
      </main>
    </div>
  );
}

function Question<T extends string>({ legend, hint, options, value, onPick }: { legend: string; hint: string; options: Array<[T, string]>; value: T; onPick: (v: T) => void }) {
  return (
    <fieldset style={{ border: 0, margin: 0, padding: "0 0 24px", borderBottom: "1px solid var(--line)", display: "flex", flexDirection: "column", gap: 12 }}>
      <legend style={{ fontSize: 17, fontWeight: 600, padding: 0, marginBottom: 12 }}>{legend}</legend>
      <div style={{ display: "flex", flexWrap: "wrap", gap: 8 }}>
        {options.map(([v, l]) => <button key={v} type="button" className="pillbtn" aria-pressed={value === v} onClick={() => onPick(v)}>{l}</button>)}
      </div>
      <span style={{ fontSize: 14, color: "var(--cobalt-dark)" }}>{hint}</span>
    </fieldset>
  );
}
