"use client";
import { useRef, useState } from "react";
import { api, toProblem, unwrap } from "@/lib/api";
import { isoDate } from "@/lib/format";
import { Doc, FOLDERS, fileSize } from "@/lib/legal";
import { EditForm, STAGE_LABEL, UploadStage, editBody, editProblem, mimeOf, registerBody, sameOriginUrl, sha256Hex, shareable, titleFromFile, uploadProblem } from "@/lib/vault";
import { ErrorNote } from "./ui";

const box = { border: "1px solid var(--line)", borderRadius: 12, background: "var(--surface-2)", padding: 16, display: "flex", flexDirection: "column", gap: 12 } as const;
const FOLDER_CHOICES = FOLDERS.filter(([k]) => k);

interface Ticket { document: Doc; upload_url: string | null; upload_headers: Record<string, string> | null }

/** Pick a file, hash it, register it, send the bytes. The server checks the size and hash again. */
export function UploadDocument({ entityId, defaultFolder, onDone, onCancel }: { entityId: string; defaultFolder: string; onDone: () => void; onCancel: () => void }) {
  const input = useRef<HTMLInputElement>(null);
  const [file, setFile] = useState<File | null>(null);
  const [title, setTitle] = useState("");
  const [folder, setFolder] = useState(defaultFolder || "other");
  const [period, setPeriod] = useState("");
  const [validUntil, setValidUntil] = useState("");
  const [stage, setStage] = useState<UploadStage | null>(null);
  const [error, setError] = useState<unknown>(null);
  const busy = stage !== null && stage !== "done" && stage !== "existing";
  const problem = uploadProblem(file, title);

  function pick(f: File | null) {
    setFile(f); setStage(null); setError(null);
    if (f && !title.trim()) setTitle(titleFromFile(f.name));
  }

  async function send() {
    if (!file || problem) return;
    setError(null);
    try {
      setStage("reading");
      const sha = await sha256Hex(await file.arrayBuffer());
      const mime = mimeOf(file.name, file.type);
      setStage("registering");
      const ticket = unwrap(await api.POST("/entities/{entityId}/documents", { params: { path: { entityId } }, body: registerBody(file, mime, sha, { title, folder, period, validUntil }) as never })) as unknown as Ticket;
      const url = sameOriginUrl(ticket.upload_url);
      if (!url) { setStage("existing"); onDone(); return; }          // the same bytes were already in the vault
      setStage("uploading");
      const res = await fetch(url, { method: "PUT", headers: ticket.upload_headers ?? { "Content-Type": mime }, body: file });
      if (!res.ok) throw await toProblem(res);
      setStage("done");
      onDone();
    } catch (e) { setError(e); setStage(null); }
  }

  return (
    <form className="card card-pad" style={{ display: "flex", flexDirection: "column", gap: 14 }} onSubmit={(e) => { e.preventDefault(); if (!busy) void send(); }}>
      <h2 style={{ margin: 0 }}>Add a document</h2>
      <div className="field"><label htmlFor="up-file">File</label>
        <input id="up-file" ref={input} type="file" className="input" style={{ height: "auto", padding: 10 }} onChange={(e) => pick(e.target.files?.[0] ?? null)} />
        {file && <span className="t13">{file.name} · {fileSize(file.size)}</span>}</div>
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(210px, 1fr))", gap: 14 }}>
        <div className="field"><label htmlFor="up-title">Title</label><input id="up-title" className="input" value={title} onChange={(e) => setTitle(e.target.value)} /></div>
        <div className="field"><label htmlFor="up-folder">Folder</label><select id="up-folder" className="input" value={folder} onChange={(e) => setFolder(e.target.value)}>{FOLDER_CHOICES.map(([k, l]) => <option key={k} value={k}>{l}</option>)}</select></div>
        <div className="field"><label htmlFor="up-period">Period (optional)</label><input id="up-period" className="input" placeholder="FY 2025-26" value={period} onChange={(e) => setPeriod(e.target.value)} /></div>
        <div className="field"><label htmlFor="up-valid">Valid until (optional)</label><input id="up-valid" type="date" className="input" min={isoDate(new Date())} value={validUntil} onChange={(e) => setValidUntil(e.target.value)} /></div>
      </div>
      <span className="t13">The file is checked by size and fingerprint, so a file already in the vault is not stored twice.</span>
      {file && problem && <span className="t13" role="alert" style={{ color: "var(--amber)" }}>{problem}</span>}
      <ErrorNote error={error} />
      {stage && <span className="t13" role="status">{STAGE_LABEL[stage]}</span>}
      <span style={{ display: "flex", gap: 10 }}>
        <button type="submit" className="btn primary" disabled={busy || !!problem}>{busy ? "Working…" : "Upload"}</button>
        <button type="button" className="btn" onClick={onCancel}>{stage === "done" || stage === "existing" ? "Close" : "Cancel"}</button>
      </span>
    </form>
  );
}

export function EditDocument({ entityId, doc, onDone, onCancel }: { entityId: string; doc: Doc; onDone: () => void; onCancel: () => void }) {
  const [f, setF] = useState<EditForm>({ title: doc.title, folder: doc.folder, period: doc.period_label ?? "", validUntil: doc.valid_until ?? "" });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const problem = editProblem(f);
  async function save() {
    setBusy(true); setError(null);
    try { unwrap(await api.PATCH("/entities/{entityId}/documents/{id}", { params: { path: { entityId, id: doc.id }, header: { "If-Match": "*" } }, body: editBody(f) as never })); onDone(); }
    catch (e) { setError(e); setBusy(false); }
  }
  return (
    <form style={box} onSubmit={(e) => { e.preventDefault(); if (!problem && !busy) void save(); }}>
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(190px, 1fr))", gap: 12 }}>
        <div className="field"><label htmlFor={`et-${doc.id}`}>Title</label><input id={`et-${doc.id}`} className="input" value={f.title} onChange={(e) => setF({ ...f, title: e.target.value })} /></div>
        <div className="field"><label htmlFor={`ef-${doc.id}`}>Folder</label><select id={`ef-${doc.id}`} className="input" value={f.folder} onChange={(e) => setF({ ...f, folder: e.target.value })}>{FOLDER_CHOICES.map(([k, l]) => <option key={k} value={k}>{l}</option>)}</select></div>
        <div className="field"><label htmlFor={`ep-${doc.id}`}>Period</label><input id={`ep-${doc.id}`} className="input" value={f.period} onChange={(e) => setF({ ...f, period: e.target.value })} /></div>
        <div className="field"><label htmlFor={`ev-${doc.id}`}>Valid until</label><input id={`ev-${doc.id}`} type="date" className="input" value={f.validUntil} onChange={(e) => setF({ ...f, validUntil: e.target.value })} /></div>
      </div>
      <ErrorNote error={error} />
      <span style={{ display: "flex", gap: 10 }}><button type="submit" className="btn primary" disabled={busy || !!problem}>{busy ? "Saving…" : "Save"}</button><button type="button" className="btn" onClick={onCancel}>Cancel</button></span>
    </form>
  );
}

/** Choose documents from the vault to hand over for an advisor's request. */
export function ShareForRequest({ entityId, requestId, docs, onDone, onCancel }: { entityId: string; requestId: string; docs: Doc[]; onDone: () => void; onCancel: () => void }) {
  const [picked, setPicked] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const list = shareable(docs);
  async function share() {
    setBusy(true); setError(null);
    try { unwrap(await api.POST("/entities/{entityId}/document-requests/{id}/share", { params: { path: { entityId, id: requestId } }, body: { document_ids: picked } as never })); onDone(); }
    catch (e) { setError(e); setBusy(false); }
  }
  return (
    <div style={box}>
      <span className="t13">Choose what to hand over. Sharing marks the request as fulfilled.</span>
      {list.length === 0 && <span className="t13">No uploaded documents yet. Add one first.</span>}
      <div style={{ display: "flex", flexDirection: "column", gap: 6, maxHeight: 220, overflowY: "auto" }}>
        {list.map((d) => (
          <label key={d.id} style={{ display: "flex", gap: 8, fontSize: 14 }}>
            <input type="checkbox" checked={picked.includes(d.id)} onChange={() => setPicked(picked.includes(d.id) ? picked.filter((x) => x !== d.id) : [...picked, d.id])} />
            <span>{d.title}<span className="t13" style={{ display: "block" }}>{d.file_name}</span></span>
          </label>
        ))}
      </div>
      <ErrorNote error={error} />
      <span style={{ display: "flex", gap: 10 }}><button type="button" className="btn primary" disabled={busy || picked.length === 0} onClick={() => void share()}>{busy ? "Sharing…" : `Share ${picked.length || ""}`.trim()}</button><button type="button" className="btn" onClick={onCancel}>Cancel</button></span>
    </div>
  );
}
