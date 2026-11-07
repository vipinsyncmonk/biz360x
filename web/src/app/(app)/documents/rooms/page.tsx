"use client";
import Link from "next/link";
import { useState } from "react";
import { api, unwrap, unwrapEmpty } from "@/lib/api";
import { shortDate } from "@/lib/format";
import { useLoad } from "@/lib/hooks";
import { Doc } from "@/lib/legal";
import { PURPOSE_LABEL, Room, expiryNote, viewerNote } from "@/lib/rooms";
import { useSession } from "@/lib/session";
import { RoomEditor } from "@/components/RoomForm";
import { ErrorNote, Loading, Pill } from "@/components/ui";

export default function DataRooms() {
  const { entity } = useSession();
  const id = entity?.id ?? null;
  const [editing, setEditing] = useState<string | null>(null);   // a room id, "new", or null
  const [confirm, setConfirm] = useState<string | null>(null);
  const [error, setError] = useState<unknown>(null);

  const rooms = useLoad(async () => unwrap(await api.GET("/entities/{entityId}/data-rooms", { params: { path: { entityId: id! }, query: { limit: 100 } } })).data as unknown as Room[], id);
  const docs = useLoad(async () => unwrap(await api.GET("/entities/{entityId}/documents", { params: { path: { entityId: id! }, query: { limit: 100 } } })).data as unknown as Doc[], id);
  const byId = new Map((docs.data ?? []).map((d) => [d.id, d]));

  async function remove(roomId: string) {
    setError(null);
    try { unwrapEmpty(await api.DELETE("/entities/{entityId}/data-rooms/{id}", { params: { path: { entityId: id!, id: roomId } } })); setConfirm(null); rooms.reload(); }
    catch (e) { setError(e); }
  }

  const list = rooms.data ?? [];
  return (
    <main className="page">
      <div style={{ display: "flex", flexWrap: "wrap", gap: 16, justifyContent: "space-between", alignItems: "flex-end" }}>
        <div>
          <Link href="/documents" className="muted" style={{ fontSize: 14 }}>← Document vault</Link>
          <h1 className="disp" style={{ margin: "6px 0 0", fontSize: 36 }}>Data rooms</h1>
        </div>
        <button type="button" className="btn primary" onClick={() => setEditing(editing === "new" ? null : "new")}>+ New data room</button>
      </div>
      <span className="t13">A room is a chosen set of vault documents that named people outside the company can open, for an investor, a lender or an auditor. It ends on the date you set.</span>

      {editing === "new" && id && <RoomEditor entityId={id} room={null} docs={docs.data ?? []} onDone={() => { setEditing(null); rooms.reload(); }} onCancel={() => setEditing(null)} />}
      <ErrorNote error={rooms.error ?? docs.error ?? error} />
      {rooms.loading ? <Loading what="data rooms" /> : list.length === 0 && editing !== "new" ? <div className="empty">No data rooms yet.</div> : (
        <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
          {list.map((r) => {
            const exp = expiryNote(r);
            return (
              <section key={r.id} className="card card-pad" style={{ display: "flex", flexDirection: "column", gap: 12 }}>
                <div style={{ display: "flex", flexWrap: "wrap", gap: 12, justifyContent: "space-between", alignItems: "flex-start" }}>
                  <div>
                    <h2 style={{ margin: 0 }}>{r.name}</h2>
                    <span className="t13">{PURPOSE_LABEL[r.purpose] ?? r.purpose} · {r.document_ids.length} document{r.document_ids.length === 1 ? "" : "s"} · {viewerNote(r.access)}</span>
                  </div>
                  <span style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
                    <Pill tone={exp.expired ? "due" : "wait"}>{exp.label}</Pill>
                    <Pill tone="prep">{r.watermark ? "Watermarked" : "No watermark"}</Pill>
                    <Pill tone="prep">{r.allow_download ? "Downloads allowed" : "View only"}</Pill>
                  </span>
                </div>
                <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(260px, 1fr))", gap: 16 }}>
                  <div>
                    <span className="t13">Documents</span>
                    {r.document_ids.length === 0 && <div className="t13">None</div>}
                    {r.document_ids.map((d) => <div key={d} style={{ fontSize: 14 }}>{byId.get(d)?.title ?? "Document"}</div>)}
                  </div>
                  <div>
                    <span className="t13">Viewers</span>
                    {r.access.length === 0 && <div className="t13">Nobody can open this room yet.</div>}
                    {r.access.map((a) => <div key={a.email} style={{ fontSize: 14 }}>{a.email}<span className="t13" style={{ display: "block" }}>{a.last_viewed_at ? `Last opened ${shortDate(a.last_viewed_at)}` : "Not opened yet"}</span></div>)}
                  </div>
                </div>
                <span style={{ display: "flex", gap: 10, flexWrap: "wrap" }}>
                  <button type="button" className="btn" aria-expanded={editing === r.id} onClick={() => setEditing(editing === r.id ? null : r.id)}>Change</button>
                  {confirm === r.id ? (
                    <>
                      <button type="button" className="btn" onClick={() => void remove(r.id)}>Yes, delete this room</button>
                      <button type="button" className="btn" onClick={() => setConfirm(null)}>Keep it</button>
                    </>
                  ) : <button type="button" className="btn" onClick={() => setConfirm(r.id)}>Delete</button>}
                </span>
                {confirm === r.id && <span className="t13">Deleting ends everyone&apos;s access. The documents stay in the vault.</span>}
                {editing === r.id && id && <RoomEditor entityId={id} room={r} docs={docs.data ?? []} onDone={() => { setEditing(null); rooms.reload(); }} onCancel={() => setEditing(null)} />}
              </section>
            );
          })}
        </div>
      )}
    </main>
  );
}
