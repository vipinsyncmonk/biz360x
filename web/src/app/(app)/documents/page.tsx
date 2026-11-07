"use client";
import { useState } from "react";
import { api, unwrap } from "@/lib/api";
import { shortDate } from "@/lib/format";
import { useLoad } from "@/lib/hooks";
import { Doc, DocRequest, FOLDERS, SOURCE_LABEL, expiryLabel, fileExt, fileSize } from "@/lib/legal";
import { useSession } from "@/lib/session";
import { ErrorNote, Loading, Pill } from "@/components/ui";

export default function Documents() {
  const { entity } = useSession();
  const id = entity?.id ?? null;
  const [folder, setFolder] = useState("");
  const [q, setQ] = useState("");

  const docs = useLoad(async () => unwrap(await api.GET("/entities/{entityId}/documents", { params: { path: { entityId: id! }, query: { limit: 100, ...(folder ? { folder } : {}), ...(q.trim() ? { q: q.trim() } : {}) } } })).data as unknown as Doc[], id ? `${id}|${folder}|${q.trim()}` : null);
  const expiring = useLoad(async () => unwrap(await api.GET("/entities/{entityId}/documents", { params: { path: { entityId: id! }, query: { expiring_within_days: 90, limit: 10 } } })).data as unknown as Doc[], id);
  const reqs = useLoad(async () => unwrap(await api.GET("/entities/{entityId}/document-requests", { params: { path: { entityId: id! }, query: { limit: 10 } } })).data as unknown as DocRequest[], id);

  const rows = docs.data ?? [];
  const open = (reqs.data ?? []).filter((r) => r.status === "open" || r.status === "partially_fulfilled");

  return (
    <main className="page">
      <div>
        <span className="muted" style={{ fontSize: 14 }}>{entity?.legal_name}</span>
        <h1 className="disp" style={{ margin: "6px 0 0", fontSize: 36 }}>Document vault</h1>
      </div>

      <div className="field" style={{ maxWidth: 480 }}>
        <label className="sr" htmlFor="dq">Search documents</label>
        <input id="dq" className="input" placeholder="Search titles, file names and text" value={q} onChange={(e) => setQ(e.target.value)} />
      </div>

      <div className="cols">
        <div className="col-main">
          <section className="card" style={{ overflow: "hidden" }}>
            <div style={{ padding: "14px 20px", borderBottom: "1px solid var(--line-2)", display: "flex", gap: 12, alignItems: "center", flexWrap: "wrap" }}>
              <div className="field"><label className="sr" htmlFor="folder">Folder</label>
                <select id="folder" className="input" style={{ height: 40, fontSize: 14 }} value={folder} onChange={(e) => setFolder(e.target.value)}>
                  {FOLDERS.map(([k, l]) => <option key={k} value={k}>{l}</option>)}
                </select></div>
              <span className="t13">Newest first{q.trim() ? " · ranked by match" : ""}</span>
            </div>
            <div style={{ padding: "0 20px" }}><ErrorNote error={docs.error} /></div>
            {docs.loading ? <Loading what="documents" /> : (
              <div className="tablewrap">
                <table style={{ minWidth: 640 }}>
                  <thead><tr><th scope="col">Document</th><th scope="col">Source</th><th scope="col">Linked to</th><th scope="col">Added</th></tr></thead>
                  <tbody>
                    {rows.map((d) => (
                      <tr key={d.id}>
                        <td>
                          <span className="mono t13" style={{ marginRight: 8 }}>{fileExt(d.file_name)}</span>
                          {d.download_url ? <a href={d.download_url} style={{ fontWeight: 600 }}>{d.title}</a> : <span style={{ fontWeight: 600 }}>{d.title}</span>}
                          <span className="t13" style={{ display: "block" }}>{d.file_name} · {fileSize(d.size_bytes)}{d.period_label ? ` · ${d.period_label}` : ""}{d.download_url ? "" : " · not uploaded yet"}</span>
                        </td>
                        <td>{SOURCE_LABEL[d.source] ?? d.source}</td>
                        <td className="t13">{d.links.length ? d.links.map((l) => l.subject.replace(/_/g, " ")).join(", ") : "–"}</td>
                        <td className="mono" style={{ fontSize: 13 }}>{shortDate(d.created_at)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
                {rows.length === 0 && <div className="empty">{q.trim() || folder ? "No document matches." : "No documents yet."}</div>}
              </div>
            )}
          </section>
        </div>

        <aside className="col-side">
          <section className="card card-pad" style={{ display: "flex", flexDirection: "column", gap: 8 }}>
            <h2 style={{ margin: 0 }}>Expiring soon</h2>
            <ErrorNote error={expiring.error} />
            {!expiring.loading && (expiring.data ?? []).length === 0 && <span className="t13">Nothing expires in the next 90 days.</span>}
            {(expiring.data ?? []).map((d) => (
              <div key={d.id} style={{ borderTop: "1px solid var(--line-2)", paddingTop: 8, fontSize: 14 }}>{d.title}<span className="t13" style={{ display: "block" }}>{d.valid_until ? expiryLabel(d.valid_until) : ""}</span></div>
            ))}
          </section>
          <section className="card card-pad" style={{ display: "flex", flexDirection: "column", gap: 8 }}>
            <h2 style={{ margin: 0 }}>Requests from advisors</h2>
            <ErrorNote error={reqs.error} />
            {!reqs.loading && open.length === 0 && <span className="t13">No open requests.</span>}
            {open.map((r) => (
              <div key={r.id} style={{ borderTop: "1px solid var(--line-2)", paddingTop: 8, fontSize: 14, display: "flex", justifyContent: "space-between", gap: 8 }}>
                <span>{r.title}<span className="t13" style={{ display: "block" }}>{r.requested_by_name ?? "Advisor"}{r.due_on ? ` · due ${shortDate(r.due_on)}` : ""}</span></span>
                <Pill tone={r.status === "open" ? "due" : "wait"}>{r.document_ids.length} shared</Pill>
              </div>
            ))}
          </section>
        </aside>
      </div>
    </main>
  );
}
