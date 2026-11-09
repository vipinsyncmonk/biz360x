"use client";
import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import { api, newIdempotencyKey, unwrap } from "@/lib/api";
import { CLOSED, type Obl } from "@/lib/compliance";
import { formatInr, shortDate } from "@/lib/format";
import { useLoad } from "@/lib/hooks";
import { useSession } from "@/lib/session";
import {
  CLASS_LABEL, Claim, Classification, LateBuyers, Payable, Treds, ageBars, buyerAction, classMismatch, headline, lakh, payablesSummary, rateNote, udyamBar,
} from "@/lib/msme";
import { BENEFIT_STATUS, Benefit, benefitsChecked } from "@/lib/startup";
import { RecordBenefit } from "@/components/StartupForms";
import { ClaimCard, TredsRow, UdyamFacts, UdyamRegister, UploadTreds } from "@/components/MsmeParts";
import { JobFollow } from "@/components/JobFollow";
import { ErrorNote, Loading, Pill } from "@/components/ui";

export default function Msme() {
  const { entity } = useSession();
  const id = entity?.id ?? null;
  const [uploading, setUploading] = useState(false);
  const [editing, setEditing] = useState<string | null>(null);
  const [starting, setStarting] = useState<string | null>(null);
  const [filing, setFiling] = useState<{ buyer: string; job: string } | null>(null);
  const [actionError, setActionError] = useState<unknown>(null);
  const [checkError, setCheckError] = useState<unknown>(null);
  const [figures, setFigures] = useState(false);
  const checked = useRef<string | null>(null);

  const p = { path: { entityId: id! } };
  const late = useLoad(async () => unwrap(await api.GET("/entities/{entityId}/msme/late-buyers", { params: p })) as unknown as LateBuyers, id);
  const ageing = useLoad(async () => unwrap(await api.GET("/entities/{entityId}/receivables/ageing", { params: p })) as unknown as Array<Record<string, unknown>>, id);
  const claims = useLoad(async () => unwrap(await api.GET("/entities/{entityId}/delayed-payment-claims", { params: { ...p, query: { limit: 50 } } })).data as unknown as Claim[], id);
  const treds = useLoad(async () => unwrap(await api.GET("/entities/{entityId}/treds-invoices", { params: { ...p, query: { limit: 100 } } })).data as unknown as Treds[], id);
  const benefits = useLoad(async () => unwrap(await api.GET("/entities/{entityId}/benefits", { params: p })) as unknown as Benefit[], id);
  const udyam = useLoad(async () => unwrap(await api.GET("/entities/{entityId}/msme/classification", { params: p })) as unknown as Classification, id);
  const payables = useLoad(async () => unwrap(await api.GET("/entities/{entityId}/msme-payables", { params: p })) as unknown as Payable[], id);
  const obls = useLoad(async () => unwrap(await api.GET("/entities/{entityId}/obligations", { params: { ...p, query: { limit: 200 } } })).data as unknown as Obl[], id);

  // the first visit checks scheme eligibility so the cards show real answers
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
  const reload = () => { late.reload(); claims.reload(); treds.reload(); ageing.reload(); };

  async function startClaim(buyerId: string, invoiceIds: string[]) {
    setStarting(buyerId); setActionError(null);
    try { unwrap(await api.POST("/entities/{entityId}/delayed-payment-claims", { params: p, body: { buyer_party_id: buyerId, invoice_ids: invoiceIds } as never })); reload(); }
    catch (e) { setActionError(e); }
    setStarting(null);
  }
  async function fileClaim(buyerName: string, claimId: string) {
    setStarting(claimId); setActionError(null);
    try {
      const j = unwrap(await api.POST("/entities/{entityId}/delayed-payment-claims/{id}/file", { params: { path: { entityId: id!, id: claimId }, header: { "Idempotency-Key": newIdempotencyKey() } } })) as unknown as { id: string };
      setFiling({ buyer: buyerName, job: j.id });
    } catch (e) { setActionError(e); }
    setStarting(null);
  }

  const data = late.data;
  const buyers = data?.buyers ?? [];
  const applies = data?.interest_applies ?? false;
  const owe = payablesSummary(payables.data ?? []);
  const bars = ageBars(ageing.data ?? []);
  const cls = udyam.data;
  const upcoming = (obls.data ?? []).filter((o) => !CLOSED.has(o.status ?? "") && o.due_on).sort((a, b) => (a.due_on ?? "").localeCompare(b.due_on ?? "")).slice(0, 3);
  const finance = (treds.data ?? []).filter((t) => t.status === "financed" || t.status === "settled").length;

  return (
    <main className="page">
      <div style={{ display: "flex", flexWrap: "wrap", gap: 16, justifyContent: "space-between", alignItems: "flex-end" }}>
        <div style={{ maxWidth: 900 }}>
          <span className="mono t13">MSME TRACK{cls?.udyam_number ? ` · ${cls.udyam_number}` : ""}{cls?.declared ? ` · ${(CLASS_LABEL[cls.declared] ?? cls.declared).toUpperCase()}` : ""}</span>
          <h1 className="disp" style={{ margin: "6px 0 0", fontSize: 36 }}>{data ? headline(data.totals, applies) : "Your MSME position"}</h1>
        </div>
        <Link href="/billing/receivables" className="btn primary" style={{ display: "inline-flex", alignItems: "center", textDecoration: "none" }}>Open Collections</Link>
      </div>

      <section aria-label="Key numbers" className="kpis">
        <Tile label="Past the due day" value={data ? lakh(data.totals.principal) : "…"} note={data ? `${data.totals.buyers} ${data.totals.buyers === 1 ? "buyer" : "buyers"} · ${data.totals.invoices} ${data.totals.invoices === 1 ? "invoice" : "invoices"}` : ""} warn={!!data && data.totals.buyers > 0} />
        <Tile label="Interest you can claim" value={data ? (applies ? lakh(data.totals.interest) : "–") : "…"} note={data ? (applies ? "Under MSMED Act Sec 16" : "Micro and small suppliers only") : ""} />
        <Tile label="You owe small suppliers" value={payables.data ? lakh(owe.owed) : "…"} note={payables.data ? (owe.breached > 0 ? `${owe.breached} past 45 days` : "All within 45 days") : ""} warn={owe.breached > 0} />
        <Tile label="On TReDS" value={treds.data ? String((treds.data ?? []).length) : "…"} note={treds.data ? `${finance} financed or settled` : ""} />
        <Tile label="Udyam class" value={cls?.declared ? (CLASS_LABEL[cls.declared] ?? cls.declared).replace(" enterprise", "") : "–"} note={cls?.computed ? `Your figures say ${CLASS_LABEL[cls.computed] ?? cls.computed}` : "Record your figures"} />
      </section>
      <ErrorNote error={late.error ?? payables.error ?? udyam.error ?? actionError} />
      {filing && <section className="card card-pad" role="status" style={{ display: "flex", gap: 10, alignItems: "center", flexWrap: "wrap" }}>Filing the claim against {filing.buyer}: <JobFollow jobId={filing.job} doneLabel="Filed" onFinished={() => { setFiling(null); reload(); }} /></section>}

      <div className="cols">
        <div className="col-main">
          <section className="card" style={{ overflow: "hidden" }}>
            <div style={{ padding: "18px 20px", display: "flex", flexDirection: "column", gap: 4 }}>
              <h2 style={{ margin: 0 }}>Buyers past the due day</h2>
              <span className="t13">{data ? (applies ? rateNote(data.rate) : "Only micro and small suppliers can claim interest under the MSMED Act. You can still chase these buyers and discount their invoices.") : ""}</span>
            </div>
            {late.loading && !data ? <Loading what="late buyers" /> : (
              <div className="tablewrap">
                <table style={{ minWidth: 680 }}>
                  <thead><tr><th scope="col">Buyer</th><th scope="col">Late by</th><th scope="col" style={{ textAlign: "right" }}>Owed</th><th scope="col" style={{ textAlign: "right" }}>Interest</th><th scope="col">What to do</th></tr></thead>
                  <tbody>
                    {buyers.map((b) => {
                      const a = buyerAction(b, applies);
                      return (
                        <tr key={b.party_id}>
                          <td><span style={{ fontWeight: 500 }}>{b.name}</span><span className="t13" style={{ display: "block" }}>{b.invoices.length} {b.invoices.length === 1 ? "invoice" : "invoices"}{b.on_treds ? " · on TReDS" : ""}</span></td>
                          <td className="mono" style={{ fontWeight: 600, color: "var(--amber)" }}>{b.days_late} {b.days_late === 1 ? "day" : "days"}</td>
                          <td className="mono" style={{ textAlign: "right" }}>{formatInr(b.principal, { decimals: false })}</td>
                          <td className="mono" style={{ textAlign: "right" }}>{b.interest ? formatInr(b.interest, { decimals: false }) : "–"}</td>
                          <td>
                            <span style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
                              {a.claim === "start" && <button type="button" className="btn primary" style={{ height: 36 }} disabled={starting === b.party_id} onClick={() => void startClaim(b.party_id, b.invoices.map((i) => i.document_id))}>{starting === b.party_id ? "Drafting…" : "Start claim"}</button>}
                              {a.claim === "file" && b.claim && <button type="button" className="btn primary" style={{ height: 36 }} disabled={starting === b.claim.id} onClick={() => void fileClaim(b.name, b.claim!.id)}>File on MSME Samadhaan</button>}
                              {a.claim === "view" && <Pill tone="wait">Claim {b.claim?.status}</Pill>}
                              {a.treds && <button type="button" className="btn" style={{ height: 36 }} onClick={() => setUploading(true)}>Discount on TReDS</button>}
                              <Link href="/billing/receivables" className="btn" style={{ height: 36, display: "inline-flex", alignItems: "center", textDecoration: "none" }}>Remind</Link>
                            </span>
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
                {buyers.length === 0 && <div className="empty">No buyer is past the day payment was due.</div>}
              </div>
            )}
          </section>

          <section className="card" style={{ overflow: "hidden" }}>
            <div style={{ padding: "18px 20px", display: "flex", justifyContent: "space-between", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
              <h2 style={{ margin: 0 }}>Claims for delayed payment</h2>
              <span className="t13">Filed on MSME Samadhaan</span>
            </div>
            <div style={{ padding: "0 20px" }}><ErrorNote error={claims.error} /></div>
            {(claims.data ?? []).length === 0 && !claims.loading && <div className="empty">No claims yet. Start one from a late buyer above.</div>}
            {id && (claims.data ?? []).map((c) => <ClaimCard key={c.id} entityId={id} claim={c} onChanged={reload} />)}
          </section>

          <section className="card" style={{ overflow: "hidden" }}>
            <div style={{ padding: "18px 20px", display: "flex", justifyContent: "space-between", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
              <h2 style={{ margin: 0 }}>TReDS invoices</h2>
              <button type="button" className="btn" aria-expanded={uploading} onClick={() => setUploading(!uploading)}>+ Record an upload</button>
            </div>
            {uploading && id && <UploadTreds entityId={id} buyers={buyers} onDone={() => { setUploading(false); reload(); }} onCancel={() => setUploading(false)} />}
            <div style={{ padding: "0 20px" }}><ErrorNote error={treds.error} /></div>
            {(treds.data ?? []).length === 0 && !treds.loading && <div className="empty">Nothing uploaded yet. TReDS lets you sell approved invoices to financiers at auction rates, without recourse to you.</div>}
            {id && (treds.data ?? []).map((t) => <TredsRow key={t.id} entityId={id} t={t} onChanged={reload} />)}
          </section>
        </div>

        <aside className="col-side">
          <section className="card card-pad" style={{ display: "flex", flexDirection: "column", gap: 12 }}>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline" }}><h2 style={{ margin: 0 }}>Receivables ageing</h2><span className="t13">₹ lakh</span></div>
            <ErrorNote error={ageing.error} />
            {bars.map((b) => (
              <div key={b.key} style={{ display: "grid", gridTemplateColumns: "64px minmax(0, 1fr) 56px", gap: 10, alignItems: "center" }}>
                <span className="mono t13">{b.label}</span>
                <div style={{ height: 14, background: "var(--paper, #F4F3EE)", borderRadius: "0 4px 4px 0" }}><div title={`${b.label} days: ${formatInr(b.value, { decimals: false })}`} style={{ height: "100%", borderRadius: "0 4px 4px 0", width: `${b.width}%`, background: b.late ? "#C2570C" : "#9AA0AA" }} /></div>
                <span className="mono t13" style={{ textAlign: "right", fontWeight: 600 }}>{(Number(b.value) / 100000).toFixed(1)}</span>
              </div>
            ))}
            <span className="t13">Past day 45 is shown in amber.</span>
          </section>

          <section className="card" style={{ overflow: "hidden" }}>
            <div style={{ padding: "18px 20px", display: "flex", justifyContent: "space-between", alignItems: "center", gap: 8 }}>
              <h2 style={{ margin: 0 }}>Schemes &amp; credit</h2>
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
                      <button type="button" className="linkbtn" style={{ color: "var(--cobalt)" }} aria-expanded={editing === b.scheme_code} onClick={() => setEditing(editing === b.scheme_code ? null : b.scheme_code)}>{b.next_action ?? "Record where it stands"}</button>
                    </div>
                    <Pill tone={st.tone}>{st.label}</Pill>
                  </div>
                  {editing === b.scheme_code && id && <RecordBenefit entityId={id} benefit={b} onDone={() => { setEditing(null); void check(); }} onCancel={() => setEditing(null)} />}
                </div>
              );
            })}
          </section>

          <section style={{ background: "#111318", color: "#ECEDEF", borderRadius: 16, padding: 22, display: "flex", flexDirection: "column", gap: 16 }}>
            <span className="mono" style={{ fontSize: 11, color: "#8A909A" }}>UDYAM CLASSIFICATION</span>
            <span className="disp" style={{ fontSize: 28, fontWeight: 700 }}>{cls?.declared ? (CLASS_LABEL[cls.declared] ?? cls.declared) : "Not registered"}</span>
            <span style={{ fontSize: 13, color: "#B7BCC5", lineHeight: 1.45 }}>Both tests must stay within the limit. Crossing either moves you to the next class at the next annual update. The limits are a starter library for your CA to confirm.</span>
            {(cls?.tests ?? []).map((t) => {
              const bar = udyamBar(t);
              return (
                <div key={t.key} style={{ display: "flex", flexDirection: "column", gap: 8 }}>
                  <div style={{ display: "flex", justifyContent: "space-between", fontSize: 14, gap: 8 }}><span>{bar.label}</span><span className="mono">{bar.value}</span></div>
                  <div style={{ height: 8, borderRadius: 4, background: "#262A33", overflow: "hidden" }}><div style={{ height: "100%", borderRadius: 4, background: "#8EA2FF", width: `${bar.width}%` }} /></div>
                </div>
              );
            })}
            {cls && classMismatch(cls) && <span className="t13" role="status" style={{ color: "#FFC58A" }}>Your figures say {CLASS_LABEL[cls.computed ?? ""] ?? cls.computed}, not {CLASS_LABEL[cls.declared ?? ""] ?? cls.declared}. Check your Udyam registration.</span>}
            {cls && !cls.declared && id && <UdyamRegister entityId={id} onDone={() => { udyam.reload(); late.reload(); void check(); }} />}
            {cls?.declared && <span><button type="button" className="btn" aria-expanded={figures} onClick={() => setFigures(!figures)}>{figures ? "Hide figures" : "Update figures"}</button></span>}
            {figures && id && <UdyamFacts entityId={id} current={cls ?? null} onDone={() => { setFigures(false); udyam.reload(); void check(); }} />}
            <div style={{ borderTop: "1px solid #23272F", paddingTop: 14, display: "flex", flexDirection: "column", gap: 10 }}>
              <span className="mono" style={{ fontSize: 11, color: "#8A909A" }}>ALSO ON THE CALENDAR</span>
              {upcoming.length === 0 && <span className="t13" style={{ color: "#B7BCC5" }}>Nothing is due.</span>}
              {upcoming.map((o) => <div key={o.id} style={{ display: "flex", justifyContent: "space-between", gap: 10, fontSize: 14 }}><span>{o.rule?.title}</span><span className="mono" style={{ color: "#C7CBD2" }}>{shortDate(o.due_on)}</span></div>)}
            </div>
          </section>
        </aside>
      </div>
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

