"use client";
import Link from "next/link";
import { useState } from "react";
import { api, newIdempotencyKey, unwrap } from "@/lib/api";
import { formatInr, shortDate } from "@/lib/format";
import { Batch, Kind, Read, SPECS, readRows, templateCsv } from "@/lib/history";
import { useLoad } from "@/lib/hooks";
import { useSession } from "@/lib/session";
import { ErrorNote, Loading, Pill } from "@/components/ui";

interface Result { dry_run: boolean; accepted: number; problem_count: number; problems: Array<{ row: number; field: string | null; message: string }>; warnings: string[]; summary: Record<string, string | number>; batch_id: string | null }
const KINDS = Object.keys(SPECS) as Kind[];
const MONEY_KEYS = new Set(["debits", "credits", "billed", "outstanding", "settled"]);

function download(kind: Kind) {
  const url = URL.createObjectURL(new Blob([templateCsv(kind)], { type: "text/csv" }));
  const a = document.createElement("a");
  a.href = url; a.download = `${kind}-template.csv`; a.click();
  URL.revokeObjectURL(url);
}

export default function History() {
  const { entity } = useSession();
  const id = entity?.id ?? null;
  const [kind, setKind] = useState<Kind>("opening_balances");
  const [asOf, setAsOf] = useState("2026-03-31");
  const [label, setLabel] = useState("");
  const [file, setFile] = useState<{ name: string; read: Read } | null>(null);
  const [result, setResult] = useState<Result | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [done, setDone] = useState<string | null>(null);

  const batches = useLoad(async () => unwrap(await api.GET("/entities/{entityId}/history/batches", { params: { path: { entityId: id! } } })).data as unknown as Batch[], id);
  const spec = SPECS[kind];

  function reset() { setResult(null); setDone(null); setError(null); }
  async function pick(f: File | undefined) { reset(); setFile(f ? { name: f.name, read: readRows(kind, await f.text()) } : null); }

  async function send(dry: boolean) {
    if (!file || file.read.fatal) return;
    setBusy(dry ? "check" : "import"); setError(null); setDone(null);
    try {
      const r = unwrap(await api.POST("/entities/{entityId}/history/imports", {
        params: { path: { entityId: id! }, header: { "Idempotency-Key": newIdempotencyKey() } },
        body: { kind, as_of: asOf, label: label.trim() || null, dry_run: dry, rows: file.read.rows },
      })) as unknown as Result;
      setResult(r);
      if (!dry) { setDone(`Imported ${r.accepted} ${kind === "opening_balances" ? "lines" : kind === "open_invoices" ? "invoices" : "bills"}.`); setFile(null); setResult(null); batches.reload(); }
    } catch (e) { setError(e); } finally { setBusy(null); }
  }

  async function undo(b: Batch) {
    setBusy(b.id); setError(null); setDone(null);
    try {
      unwrap(await api.POST("/entities/{entityId}/history/batches/{id}/undo", { params: { path: { entityId: id!, id: b.id }, header: { "Idempotency-Key": newIdempotencyKey() } } }));
      setDone("Import undone."); batches.reload();
    } catch (e) { setError(e); } finally { setBusy(null); }
  }

  const sheetRowOf = (n: number) => (file && n > 0 ? file.read.sheetRows[n - 1] ?? n : null);
  const fmt = (k: string, v: string | number) => (MONEY_KEYS.has(k) ? formatInr(String(v)) : String(v));
  const clean = result && result.problem_count === 0;

  return (
    <main className="page">
      <div>
        <Link href="/finance" className="muted" style={{ fontSize: 14 }}>← Finance &amp; Books</Link>
        <h1 className="disp" style={{ margin: "6px 0 0", fontSize: 36 }}>Opening position from earlier books</h1>
      </div>
      <span className="t13">Start from where the old books stood. Load the opening trial balance on a cut-off date, then the invoices and bills still open on it, so receivables, payables and the balance sheet begin from the real position. Every file is checked first and nothing is saved until you import. Invoices and bills are records only: nothing is e-invoiced, filed or posted again, and each import can be undone as a whole.</span>

      <ErrorNote error={batches.error ?? error} />
      {done && <div className="card card-pad" role="status">{done}</div>}

      <section className="card card-pad" style={{ display: "flex", flexDirection: "column", gap: 14 }}>
        <div role="tablist" aria-label="What to import" style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
          {KINDS.map((k) => <button key={k} type="button" role="tab" aria-selected={k === kind} className={k === kind ? "btn primary" : "btn"} onClick={() => { setKind(k); setFile(null); reset(); }}>{SPECS[k].label}</button>)}
        </div>
        <span className="t13">{spec.blurb}</span>
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(200px, 1fr))", gap: 12 }}>
          <label style={{ display: "flex", flexDirection: "column", gap: 4 }}><span className="t13">Cut-off date</span><input type="date" value={asOf} onChange={(e) => { setAsOf(e.target.value); setResult(null); }} /></label>
          <label style={{ display: "flex", flexDirection: "column", gap: 4 }}><span className="t13">Label (optional)</span><input value={label} onChange={(e) => setLabel(e.target.value)} placeholder="Closing books FY 2025-26" /></label>
        </div>
        <div style={{ display: "flex", gap: 12, flexWrap: "wrap", alignItems: "flex-end" }}>
          <label style={{ display: "flex", flexDirection: "column", gap: 4 }}><span className="t13">File (.csv)</span><input type="file" accept=".csv,.txt,text/csv" onChange={(e) => void pick(e.target.files?.[0])} /></label>
          <button type="button" className="btn" onClick={() => download(kind)}>Download template</button>
        </div>
      </section>

      {file?.read.fatal && <div className="error" role="alert">{file.read.fatal}</div>}
      {file && !file.read.fatal && (
        <section className="card card-pad" style={{ display: "flex", flexDirection: "column", gap: 14 }}>
          <div style={{ display: "flex", flexWrap: "wrap", gap: 8, alignItems: "center" }}>
            <strong>{file.name}</strong>
            <Pill tone="prep">{file.read.rows.length} rows</Pill>
            {file.read.problems.length > 0 && <Pill tone="due">{file.read.problems.length} cell{file.read.problems.length === 1 ? "" : "s"} unreadable</Pill>}
          </div>
          <div className="t13">Read from: {Object.entries(file.read.columns).map(([k, v]) => `${k.replace("_", " ")} ← “${v}”`).join(" · ")}</div>
          {file.read.problems.length > 0 && (
            <details open>
              <summary>Cells I could not read</summary>
              <ul style={{ margin: "8px 0 0", paddingLeft: 18 }}>{file.read.problems.slice(0, 50).map((p, i) => <li key={i} className="t13">Row {p.row}: {p.message}</li>)}</ul>
            </details>
          )}

          {result && (
            <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
              {clean ? <Pill tone="ok">Checked: nothing is wrong with {result.accepted} {result.accepted === 1 ? "row" : "rows"}</Pill> : <Pill tone="due">{result.problem_count} problem{result.problem_count === 1 ? "" : "s"} found; nothing can be imported yet</Pill>}
              <div style={{ display: "flex", flexWrap: "wrap", gap: 16 }}>
                {Object.entries(result.summary).map(([k, v]) => <div key={k}><span className="t13">{k.replace("_", " ")}</span><div style={{ fontSize: 18 }}>{fmt(k, v)}</div></div>)}
              </div>
              {result.warnings.map((w, i) => <div key={i} className="t13">⚠ {w}</div>)}
              {result.problems.length > 0 && (
                <ul style={{ margin: 0, paddingLeft: 18 }}>
                  {result.problems.map((p, i) => <li key={i} className="t13">{p.row > 0 ? `Row ${sheetRowOf(p.row)}${p.field ? ` (${p.field.replace("_", " ")})` : ""}: ` : ""}{p.message}</li>)}
                </ul>
              )}
            </div>
          )}

          <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
            <button type="button" className={clean ? "btn" : "btn primary"} disabled={busy !== null || file.read.rows.length === 0} onClick={() => void send(true)}>{busy === "check" ? "Checking…" : "Check the file"}</button>
            {clean && <button type="button" className="btn primary" disabled={busy !== null} onClick={() => void send(false)}>{busy === "import" ? "Importing…" : `Import ${result.accepted} rows`}</button>}
          </div>
        </section>
      )}

      <section className="card" style={{ overflow: "hidden" }}>
        <div style={{ padding: "14px 20px" }}><h2 style={{ margin: 0 }}>Earlier imports</h2></div>
        {batches.loading ? <Loading what="imports" /> : (batches.data ?? []).length === 0 ? <div className="empty">Nothing imported yet.</div> : (
          <div className="tablewrap">
            <table>
              <thead><tr><th scope="col">What</th><th scope="col">Cut-off</th><th scope="col" style={{ textAlign: "right" }}>Rows</th><th scope="col">Imported</th><th scope="col"><span className="sr">Action</span></th></tr></thead>
              <tbody>
                {(batches.data ?? []).map((b) => (
                  <tr key={b.id}>
                    <td>{SPECS[b.kind].label}{b.label ? <span className="t13" style={{ display: "block" }}>{b.label}</span> : null}</td>
                    <td>{shortDate(b.as_of)}</td>
                    <td style={{ textAlign: "right" }}>{b.row_count}</td>
                    <td>{b.undone_at ? <Pill tone="wait">Undone {shortDate(b.undone_at)}</Pill> : shortDate(b.created_at)}</td>
                    <td style={{ textAlign: "right" }}>{!b.undone_at && <button type="button" className="btn" style={{ height: 36, fontSize: 13 }} disabled={busy === b.id} onClick={() => void undo(b)}>{busy === b.id ? "Undoing…" : "Undo"}</button>}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </main>
  );
}
