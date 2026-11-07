"use client";
import { useState } from "react";
import { api, unwrap } from "@/lib/api";
import { formatInr, shortDate } from "@/lib/format";
import { useLoad } from "@/lib/hooks";
import { CONTRACT_STATUS, Contract, KIND_LABEL, SIGNER_STATUS, SIGN_STATUS, SignatureRequest, Template, alertTone, byUrgency, nextDate, versionLabel } from "@/lib/legal";
import { useSession } from "@/lib/session";
import { ErrorNote, Loading, Pill } from "@/components/ui";

const PERIOD: Record<string, string> = { one_time: "one time", monthly: "a month", annual: "a year", rate_card: "rate card" };

export default function Legal() {
  const { entity } = useSession();
  const id = entity?.id ?? null;
  const [onlyNotice, setOnlyNotice] = useState(false);
  const [openId, setOpenId] = useState<string | null>(null);

  const contracts = useLoad(async () => unwrap(await api.GET("/entities/{entityId}/contracts", { params: { path: { entityId: id! }, query: { limit: 200, ...(onlyNotice ? { notice_within_days: 30 } : {}) } } })).data as unknown as Contract[], id ? `${id}|${onlyNotice}` : null);
  const sigs = useLoad(async () => unwrap(await api.GET("/entities/{entityId}/signature-requests", { params: { path: { entityId: id! }, query: { limit: 20 } } })).data as unknown as SignatureRequest[], id);
  const templates = useLoad(async () => unwrap(await api.GET("/entities/{entityId}/contract-templates", { params: { path: { entityId: id! } } })) as unknown as Template[], id);

  const rows = byUrgency(contracts.data ?? []);
  const current = rows.find((c) => c.id === openId) ?? rows[0];
  const active = rows.filter((c) => c.status === "active").length;
  const warned = rows.filter((c) => c.alerts.some((a) => a.level === "warn")).length;
  const pending = (sigs.data ?? []).filter((s) => s.status === "sent" || s.status === "partially_signed");

  return (
    <main className="page">
      <div>
        <span className="muted" style={{ fontSize: 14 }}>{entity?.legal_name}</span>
        <h1 className="disp" style={{ margin: "6px 0 0", fontSize: 36 }}>Legal &amp; Contracts</h1>
      </div>

      <ErrorNote error={contracts.error} />
      <section aria-label="Key numbers" className="kpis">
        <Tile label="Active contracts" value={String(active)} note={`${rows.length} in the register`} />
        <Tile label="Need attention" value={String(warned)} note="Open warnings" warn={warned > 0} />
        <Tile label="Out for signature" value={String(pending.length)} note="Waiting on signers" />
      </section>

      <div className="cols">
        <div className="col-main">
          <section className="card" style={{ overflow: "hidden" }}>
            <div style={{ padding: "14px 20px", display: "flex", justifyContent: "space-between", alignItems: "center", flexWrap: "wrap", gap: 12, borderBottom: "1px solid var(--line-2)" }}>
              <h2 style={{ margin: 0 }}>Contract register</h2>
              <div className="seg" role="tablist" aria-label="Filter">
                <button type="button" role="tab" aria-selected={!onlyNotice} onClick={() => setOnlyNotice(false)}>All</button>
                <button type="button" role="tab" aria-selected={onlyNotice} onClick={() => setOnlyNotice(true)}>Notice in 30 days</button>
              </div>
            </div>
            {contracts.loading ? <Loading what="contracts" /> : (
              <div className="tablewrap">
                <table style={{ minWidth: 720 }}>
                  <thead><tr><th scope="col">Contract</th><th scope="col">Type</th><th scope="col" style={{ textAlign: "right" }}>Value</th><th scope="col">Next date</th><th scope="col">Status</th></tr></thead>
                  <tbody>
                    {rows.map((c) => {
                      const nd = nextDate(c), st = CONTRACT_STATUS[c.status] ?? { label: c.status, tone: "prep" as const };
                      return (
                        <tr key={c.id} className="trow" aria-selected={current?.id === c.id} onClick={() => setOpenId(c.id)} style={current?.id === c.id ? { background: "var(--bg-2, #FAFAF7)" } : undefined}>
                          <td><button type="button" className="linklike" style={{ all: "unset", cursor: "pointer", fontWeight: 600 }} onClick={() => setOpenId(c.id)}>{c.title}</button><span className="t13" style={{ display: "block" }}>{c.counterparty_name ?? ""}</span></td>
                          <td>{KIND_LABEL[c.kind] ?? c.kind}</td>
                          <td className="mono" style={{ textAlign: "right", whiteSpace: "nowrap" }}>{c.value_amount ? formatInr(c.value_amount, { decimals: false }) : "–"}{c.value_amount && c.value_period ? <span className="t13" style={{ display: "block" }}>{PERIOD[c.value_period] ?? c.value_period}</span> : null}</td>
                          <td>{nd ? <><span className="mono" style={{ fontSize: 13 }}>{shortDate(nd.date)}</span><span className="t13" style={{ display: "block" }}>{nd.label}</span></> : "–"}</td>
                          <td><Pill tone={st.tone}>{st.label}</Pill></td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
                {rows.length === 0 && <div className="empty">{onlyNotice ? "No notice window closes in the next 30 days." : "No contracts yet."}</div>}
              </div>
            )}
          </section>

          {current && (
            <section className="card card-pad" style={{ display: "flex", flexDirection: "column", gap: 10 }}>
              <span className="t13">KEY TERMS · {current.signed_document_id ? "FROM THE SIGNED COPY" : "NO SIGNED COPY ON FILE"}</span>
              <h2 style={{ margin: 0 }}>{current.title}</h2>
              {current.terms.length === 0 && <span className="t13">No terms recorded yet.</span>}
              {current.terms.map((t) => (
                <div key={t.term_key} style={{ display: "flex", justifyContent: "space-between", gap: 12, padding: "8px 0", borderTop: "1px solid var(--line-2)", fontSize: 14 }}>
                  <span className="muted">{t.term_key.replace(/_/g, " ")}</span>
                  <span style={{ textAlign: "right" }}>{t.value_text}<span className="t13" style={{ display: "block" }}>{t.verified ? "Verified" : t.extracted_by === "copilot" ? "Suggested by the copilot, to be verified" : "Entered by hand"}{t.source_page ? ` · page ${t.source_page}` : ""}</span></span>
                </div>
              ))}
              {current.alerts.map((a) => (
                <div key={a.code} role="note" style={{ borderTop: "1px solid var(--line-2)", paddingTop: 8, fontSize: 14 }}>
                  <Pill tone={alertTone(a.level)}>{a.title}</Pill><span className="t13" style={{ display: "block", marginTop: 4 }}>{a.detail}</span>
                </div>
              ))}
            </section>
          )}
        </div>

        <aside className="col-side">
          <section className="card card-pad" style={{ display: "flex", flexDirection: "column", gap: 8 }}>
            <h2 style={{ margin: 0 }}>Out for signature</h2>
            <ErrorNote error={sigs.error} />
            {sigs.loading && <Loading what="signature requests" />}
            {!sigs.loading && pending.length === 0 && <span className="t13">Nothing is waiting on a signature.</span>}
            {pending.map((s) => {
              const st = SIGN_STATUS[s.status] ?? { label: s.status, tone: "prep" as const };
              return (
                <div key={s.id} style={{ borderTop: "1px solid var(--line-2)", paddingTop: 8, fontSize: 14 }}>
                  <div style={{ display: "flex", justifyContent: "space-between", gap: 8 }}><span>{s.subject.replace(/_/g, " ")}</span><Pill tone={st.tone}>{st.label}</Pill></div>
                  <span className="t13">{s.signers.map((g) => `${g.person_name ?? g.external_name ?? "Signer"}: ${SIGNER_STATUS[g.status] ?? g.status}`).join(" · ")}</span>
                </div>
              );
            })}
          </section>
          <section className="card card-pad" style={{ display: "flex", flexDirection: "column", gap: 8 }}>
            <h2 style={{ margin: 0 }}>Templates</h2>
            <ErrorNote error={templates.error} />
            {(templates.data ?? []).map((t) => (
              <div key={t.id} style={{ borderTop: "1px solid var(--line-2)", paddingTop: 8, fontSize: 14 }}>
                {t.name}<span className="t13" style={{ display: "block" }}>{KIND_LABEL[t.kind] ?? t.kind} · {versionLabel(t.version)} · {t.reviewed_by ? `reviewed by ${t.reviewed_by}` : "not yet reviewed by counsel"}</span>
              </div>
            ))}
            {!templates.loading && (templates.data ?? []).length === 0 && <span className="t13">No templates yet.</span>}
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
