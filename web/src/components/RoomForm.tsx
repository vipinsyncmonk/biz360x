"use client";
import { useState } from "react";
import { api, unwrap } from "@/lib/api";
import { isoDate } from "@/lib/format";
import { Doc, fileSize } from "@/lib/legal";
import { PURPOSES, Room, RoomForm as Form, blankRoom, roomBody, roomForm, roomProblem } from "@/lib/rooms";
import { shareable } from "@/lib/vault";
import { ErrorNote } from "./ui";

/** Create a data room, or change one: the documents in it, who can open it, and the rules. */
export function RoomEditor({ entityId, room, docs, onDone, onCancel }: { entityId: string; room: Room | null; docs: Doc[]; onDone: () => void; onCancel: () => void }) {
  const [f, setF] = useState<Form>(room ? roomForm(room) : blankRoom());
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const today = isoDate(new Date());
  const problem = roomProblem(f, today);
  const list = shareable(docs);
  const k = room?.id ?? "new";
  const set = (patch: Partial<Form>) => setF({ ...f, ...patch });

  async function save() {
    setBusy(true); setError(null);
    try {
      if (room) unwrap(await api.PATCH("/entities/{entityId}/data-rooms/{id}", { params: { path: { entityId, id: room.id }, header: { "If-Match": "*" } }, body: roomBody(f) as never }));
      else unwrap(await api.POST("/entities/{entityId}/data-rooms", { params: { path: { entityId } }, body: roomBody(f) as never }));
      onDone();
    } catch (e) { setError(e); setBusy(false); }
  }

  return (
    <form className="card card-pad" style={{ display: "flex", flexDirection: "column", gap: 14 }} onSubmit={(e) => { e.preventDefault(); if (!problem && !busy) void save(); }}>
      <h2 style={{ margin: 0 }}>{room ? "Change data room" : "New data room"}</h2>
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(210px, 1fr))", gap: 14 }}>
        <div className="field"><label htmlFor={`rn-${k}`}>Name</label><input id={`rn-${k}`} className="input" placeholder="Series A diligence" value={f.name} onChange={(e) => set({ name: e.target.value })} /></div>
        <div className="field"><label htmlFor={`rp-${k}`}>Purpose</label><select id={`rp-${k}`} className="input" value={f.purpose} onChange={(e) => set({ purpose: e.target.value })}>{PURPOSES.map(([v, l]) => <option key={v} value={v}>{l}</option>)}</select></div>
        <div className="field"><label htmlFor={`rx-${k}`}>Access ends (optional)</label><input id={`rx-${k}`} type="date" className="input" min={today} value={f.expires} onChange={(e) => set({ expires: e.target.value })} /></div>
      </div>
      <div className="field"><label htmlFor={`re-${k}`}>Who can open it</label>
        <textarea id={`re-${k}`} className="input" rows={3} style={{ height: "auto", padding: 10 }} placeholder="One email address per line" value={f.emails} onChange={(e) => set({ emails: e.target.value })} />
        <span className="t13">Removing an address ends that person&apos;s access.</span></div>
      <fieldset style={{ border: 0, padding: 0, margin: 0, display: "flex", gap: 20, flexWrap: "wrap" }}>
        <legend className="sr">Rules</legend>
        <label style={{ display: "flex", gap: 8, fontSize: 14 }}><input type="checkbox" checked={f.watermark} onChange={(e) => set({ watermark: e.target.checked })} />Watermark every page with the viewer&apos;s address</label>
        <label style={{ display: "flex", gap: 8, fontSize: 14 }}><input type="checkbox" checked={f.allowDownload} onChange={(e) => set({ allowDownload: e.target.checked })} />Allow downloads</label>
      </fieldset>
      <div className="field"><span style={{ fontSize: 14, fontWeight: 600 }}>Documents ({f.documentIds.length} chosen)</span>
        {list.length === 0 && <span className="t13">No uploaded documents yet. Add some in the vault first.</span>}
        <div style={{ display: "flex", flexDirection: "column", gap: 6, maxHeight: 240, overflowY: "auto", border: "1px solid var(--line)", borderRadius: 12, padding: 12 }}>
          {list.map((d) => (
            <label key={d.id} style={{ display: "flex", gap: 8, fontSize: 14 }}>
              <input type="checkbox" checked={f.documentIds.includes(d.id)} onChange={() => set({ documentIds: f.documentIds.includes(d.id) ? f.documentIds.filter((x) => x !== d.id) : [...f.documentIds, d.id] })} />
              <span>{d.title}<span className="t13" style={{ display: "block" }}>{d.file_name} · {fileSize(d.size_bytes)}</span></span>
            </label>
          ))}
        </div>
      </div>
      {problem && f.name.trim() && <span className="t13" role="alert" style={{ color: "var(--amber)" }}>{problem}</span>}
      <ErrorNote error={error} />
      <span style={{ display: "flex", gap: 10 }}>
        <button type="submit" className="btn primary" disabled={busy || !!problem}>{busy ? "Saving…" : room ? "Save changes" : "Create room"}</button>
        <button type="button" className="btn" onClick={onCancel}>Cancel</button>
      </span>
    </form>
  );
}
