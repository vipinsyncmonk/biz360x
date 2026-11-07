"use client";
import Link from "next/link";
import { useState } from "react";
import { api, unwrap } from "@/lib/api";
import { formatInr, shortDate } from "@/lib/format";
import { useLoad } from "@/lib/hooks";
import { useSession } from "@/lib/session";
import type { Tone } from "@/lib/compliance";
import { ErrorNote, Loading, Pill } from "@/components/ui";

interface Quote { id: string; quote_no: string; version: number; party_id: string; title: string | null; status: string; valid_until: string | null; grand_total: string; sent_at: string | null; accepted_at: string | null; invoiced_pct: number; sales_order_id: string | null }

const STATUS: Record<string, { label: string; tone: Tone }> = {
  draft: { label: "Draft", tone: "prep" }, sent: { label: "Sent", tone: "wait" }, viewed: { label: "Viewed", tone: "wait" }, accepted: { label: "Accepted", tone: "ok" },
  lost: { label: "Lost", tone: "due" }, expired: { label: "Expired", tone: "due" }, superseded: { label: "Superseded", tone: "prep" },
};
const FILTERS: Array<[string, string]> = [["", "All"], ["draft", "Draft"], ["sent", "Sent"], ["accepted", "Accepted"], ["lost", "Lost"]];

export default function Quotes() {
  const { entity } = useSession();
  const id = entity?.id ?? null;
  const [status, setStatus] = useState("");
  const quotes = useLoad(async () => unwrap(await api.GET("/entities/{entityId}/quotes", { params: { path: { entityId: id! }, query: { limit: 100, ...(status ? { status } : {}) } } })).data as unknown as Quote[], id ? `${id}|${status}` : null);
  const names = useLoad(async () => {
    const rows = unwrap(await api.GET("/entities/{entityId}/parties", { params: { path: { entityId: id! }, query: { limit: 200 } } })).data as unknown as Array<{ id: string; legal_name: string }>;
    return Object.fromEntries(rows.map((p) => [p.id, p.legal_name]));
  }, id);
  const rows = quotes.data ?? [];

  return (
    <main className="page">
      <div>
        <Link href="/billing" style={{ fontSize: 14 }}>← Sales &amp; Billing</Link>
        <h1 className="disp" style={{ margin: "8px 0 0", fontSize: 36 }}>Quotes</h1>
      </div>
      <div className="seg" role="group" aria-label="Status">
        {FILTERS.map(([k, l]) => <button key={k} type="button" aria-pressed={status === k} onClick={() => setStatus(k)}>{l}</button>)}
      </div>
      <ErrorNote error={quotes.error} />
      <section className="card" style={{ overflow: "hidden" }}>
        {quotes.loading ? <Loading what="quotes" /> : (
          <div className="tablewrap">
            <table style={{ minWidth: 760 }}>
              <thead><tr><th scope="col">Quote</th><th scope="col">Customer</th><th scope="col">Valid until</th><th scope="col" style={{ textAlign: "right" }}>Amount</th><th scope="col">Invoiced</th><th scope="col">Status</th></tr></thead>
              <tbody>
                {rows.map((q) => {
                  const st = STATUS[q.status] ?? { label: q.status, tone: "prep" as Tone };
                  return (
                    <tr key={q.id}>
                      <td><span className="mono" style={{ fontWeight: 600 }}>{q.quote_no}{q.version > 1 ? ` v${q.version}` : ""}</span>{q.title && <span className="t13" style={{ display: "block" }}>{q.title}</span>}</td>
                      <td>{names.data?.[q.party_id] ?? "…"}</td>
                      <td className="mono">{shortDate(q.valid_until)}</td>
                      <td className="mono" style={{ textAlign: "right", fontWeight: 600 }}>{formatInr(q.grand_total)}</td>
                      <td className="mono">{q.sales_order_id ? `${q.invoiced_pct}%` : "—"}</td>
                      <td><Pill tone={st.tone}>{st.label}</Pill></td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
            {rows.length === 0 && <div className="empty">No quotes {status ? "with this status" : "yet"}.</div>}
          </div>
        )}
      </section>
      <p className="t13" style={{ margin: 0 }}>Creating, sending and accepting quotes, and credit notes, are done through the API for now; the screens for them come next.</p>
    </main>
  );
}
