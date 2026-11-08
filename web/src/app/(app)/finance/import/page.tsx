"use client";
import Link from "next/link";
import { useState } from "react";
import { api, unwrap } from "@/lib/api";
import { formatInr, shortDate } from "@/lib/format";
import { useLoad } from "@/lib/hooks";
import { useSession } from "@/lib/session";
import { AccountForm, Statement, StatementLine, accountProblem, bankBody, blankAccount, chunk, ledgerBody, ledgerCode, parseStatement } from "@/lib/statement";
import { ErrorNote, Loading, Pill } from "@/components/ui";

interface Account { id: string; bank_name: string; account_last4: string }
interface Preview { total: number; fresh: number; duplicates: number; duplicateIds: Set<string>; earliest: string | null; latest: string | null }
interface Done { imported: number; duplicates: number; suggested: number }

export default function ImportStatement() {
  const { entity } = useSession();
  const id = entity?.id ?? null;
  const accounts = useLoad(async () => unwrap(await api.GET("/entities/{entityId}/bank-accounts", { params: { path: { entityId: id! }, query: { limit: 50 } } })).data as unknown as Account[], id);
  const [account, setAccount] = useState("");
  const [file, setFile] = useState<{ name: string; parsed: Statement } | null>(null);
  const [preview, setPreview] = useState<Preview | null>(null);
  const [done, setDone] = useState<Done | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<unknown>(null);

  const [adding, setAdding] = useState(false);
  const [form, setForm] = useState<AccountForm>(blankAccount);
  const set = (k: keyof AccountForm) => (e: { target: { value: string } }) => setForm({ ...form, [k]: e.target.value });

  async function addAccount() {
    const bad = accountProblem(form);
    if (bad) { setError(new Error(bad)); return; }
    setBusy("account"); setError(null);
    try {
      const code = ledgerCode(form);
      const existing = unwrap(await api.GET("/entities/{entityId}/accounts", { params: { path: { entityId: id! }, query: { q: code, limit: 5 } } })).data as unknown as Array<{ id: string; code: string }>;
      const ledger = existing.find((a) => a.code === code) ?? (unwrap(await api.POST("/entities/{entityId}/accounts", { params: { path: { entityId: id! } }, body: ledgerBody(form) })) as unknown as { id: string });
      const made = unwrap(await api.POST("/entities/{entityId}/bank-accounts", { params: { path: { entityId: id! } }, body: bankBody(form, ledger.id) })) as unknown as Account;
      setAccount(made.id); setAdding(false); setForm(blankAccount); accounts.reload();
    } catch (e) { setError(e); } finally { setBusy(null); }
  }

  const chosen = account || accounts.data?.[0]?.id || "";
  const path = { entityId: id!, id: chosen };

  async function pick(f: File | undefined) {
    setPreview(null); setDone(null); setError(null);
    if (!f) { setFile(null); return; }
    setFile({ name: f.name, parsed: parseStatement(await f.text()) });
  }

  async function check() {
    if (!file) return;
    setBusy("check"); setError(null); setDone(null);
    try {
      const ids = new Set<string>();
      let earliest: string | null = null, latest: string | null = null;
      for (const part of chunk(file.parsed.lines)) {
        const r = unwrap(await api.POST("/entities/{entityId}/bank-accounts/{id}/transactions/preview", { params: { path }, body: { transactions: part as never } })) as unknown as
          { duplicate_ids: string[]; earliest_existing: string | null; latest_existing: string | null };
        r.duplicate_ids.forEach((d) => ids.add(d));
        earliest = r.earliest_existing; latest = r.latest_existing;
      }
      const total = file.parsed.lines.length;
      const dup = file.parsed.lines.filter((l) => ids.has(l.external_id)).length;
      setPreview({ total, fresh: total - dup, duplicates: dup, duplicateIds: ids, earliest, latest });
    } catch (e) { setError(e); } finally { setBusy(null); }
  }

  async function run() {
    if (!file || !preview) return;
    setBusy("import"); setError(null);
    const total: Done = { imported: 0, duplicates: 0, suggested: 0 };
    try {
      const fresh: StatementLine[] = file.parsed.lines.filter((l) => !preview.duplicateIds.has(l.external_id));
      for (const part of chunk(fresh)) {
        const r = unwrap(await api.POST("/entities/{entityId}/bank-accounts/{id}/transactions", { params: { path }, body: { transactions: part as never } })) as unknown as Done;
        total.imported += r.imported; total.duplicates += r.duplicates; total.suggested += r.suggested;
      }
      total.duplicates += preview.duplicates;
      setDone(total); setPreview(null); setFile(null);
    } catch (e) { setError(e); setPreview(null); } finally { setBusy(null); }
  }

  const s = file?.parsed;
  const noAccounts = !accounts.loading && (accounts.data ?? []).length === 0;
  return (
    <main className="page">
      <div>
        <Link href="/finance" className="muted" style={{ fontSize: 14 }}>← Finance &amp; Books</Link>
        <h1 className="disp" style={{ margin: "6px 0 0", fontSize: 36 }}>Import a bank statement</h1>
      </div>
      <span className="t13">Save the statement from your bank as CSV and choose it here. Nothing is sent until you have seen what was read and checked it against what is already on the account. Lines already imported are skipped, so overlapping statements are safe.</span>

      <ErrorNote error={accounts.error ?? error} />
      {accounts.loading ? <Loading what="bank accounts" /> : null}
      {!accounts.loading && (noAccounts || adding) && (
        <section className="card card-pad" style={{ display: "flex", flexDirection: "column", gap: 12 }}>
          <h2 style={{ margin: 0 }}>{noAccounts ? "Add the bank account first" : "Add a bank account"}</h2>
          <span className="t13">Statement lines are filed against a bank account, and each account gets its own ledger account to post to.</span>
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(200px, 1fr))", gap: 12 }}>
            <label style={{ display: "flex", flexDirection: "column", gap: 4 }}><span className="t13">Bank</span><input value={form.bank} onChange={set("bank")} /></label>
            <label style={{ display: "flex", flexDirection: "column", gap: 4 }}><span className="t13">Last four digits</span><input value={form.last4} onChange={set("last4")} inputMode="numeric" maxLength={4} /></label>
            <label style={{ display: "flex", flexDirection: "column", gap: 4 }}><span className="t13">IFSC (optional)</span><input value={form.ifsc} onChange={set("ifsc")} /></label>
            <label style={{ display: "flex", flexDirection: "column", gap: 4 }}><span className="t13">Kind</span>
              <select value={form.kind} onChange={set("kind")}>{["current", "savings", "cash_credit", "overdraft", "fixed_deposit", "escrow"].map((k) => <option key={k} value={k}>{k.replace("_", " ")}</option>)}</select></label>
          </div>
          <div style={{ display: "flex", gap: 8 }}>
            <button type="button" className="btn primary" disabled={busy !== null} onClick={() => void addAccount()}>{busy === "account" ? "Adding…" : "Add bank account"}</button>
            {adding && <button type="button" className="btn" onClick={() => setAdding(false)}>Cancel</button>}
          </div>
        </section>
      )}
      {!accounts.loading && !noAccounts && (
        <section className="card card-pad" style={{ display: "flex", flexDirection: "column", gap: 14 }}>
          <label style={{ display: "flex", flexDirection: "column", gap: 4, maxWidth: 420 }}>
            <span className="t13">Bank account</span>
            <select value={chosen} onChange={(e) => { setAccount(e.target.value); setPreview(null); }}>
              {(accounts.data ?? []).map((a) => <option key={a.id} value={a.id}>{a.bank_name} ····{a.account_last4}</option>)}
            </select>
          </label>
          {!adding && <div><button type="button" className="btn" onClick={() => setAdding(true)}>+ Add another account</button></div>}
          <label style={{ display: "flex", flexDirection: "column", gap: 4, maxWidth: 420 }}>
            <span className="t13">Statement file (.csv)</span>
            <input type="file" accept=".csv,.txt,text/csv" onChange={(e) => void pick(e.target.files?.[0])} />
          </label>
        </section>
      )}

      {done && (
        <section className="card card-pad" role="status">
          <h2 style={{ margin: 0 }}>Imported {done.imported} line{done.imported === 1 ? "" : "s"}</h2>
          <span className="t13">{done.duplicates} already on the account were skipped. {done.suggested} credit{done.suggested === 1 ? "" : "s"} now have a suggested receipt to accept or reject on <Link href="/finance">Finance &amp; Books</Link>.</span>
        </section>
      )}

      {s?.fatal && <div className="error" role="alert">{s.fatal}</div>}
      {s && !s.fatal && file && (
        <section className="card card-pad" style={{ display: "flex", flexDirection: "column", gap: 14 }}>
          <div style={{ display: "flex", flexWrap: "wrap", gap: 8, alignItems: "center" }}>
            <strong>{file.name}</strong>
            <Pill tone="prep">{s.lines.length} lines</Pill>
            <Pill tone="prep">{shortDate(s.from)} to {shortDate(s.to)}</Pill>
            {s.problems.length > 0 && <Pill tone="due">{s.problems.length} row{s.problems.length === 1 ? "" : "s"} skipped</Pill>}
            {"balance" in s.columns && (s.balanceBreaks === 0 ? <Pill tone="ok">Running balance agrees</Pill> : <Pill tone="due">{s.balanceBreaks} balance break{s.balanceBreaks === 1 ? "" : "s"}</Pill>)}
          </div>
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(180px, 1fr))", gap: 12 }}>
            <div><span className="t13">Money in</span><div style={{ fontSize: 20 }}>{formatInr(s.credits)}</div></div>
            <div><span className="t13">Money out</span><div style={{ fontSize: 20 }}>{formatInr(s.debits)}</div></div>
          </div>
          <div className="t13">Read from: {Object.entries(s.columns).map(([k, v]) => `${k.replace("_", " ")} ← “${v}”`).join(" · ")}</div>
          {s.balanceBreaks > 0 && <div className="t13">The balance after some lines does not follow from the line before. Check that the file is complete and in one piece before importing.</div>}
          {s.problems.length > 0 && (
            <details>
              <summary>Rows that were skipped</summary>
              <ul style={{ margin: "8px 0 0", paddingLeft: 18 }}>{s.problems.slice(0, 50).map((p) => <li key={p.row} className="t13">Row {p.row}: {p.message}</li>)}</ul>
            </details>
          )}
          <div className="tablewrap">
            <table>
              <thead><tr><th>Date</th><th>Narration</th><th style={{ textAlign: "right" }}>Amount</th><th style={{ textAlign: "right" }}>Balance</th>{preview && <th>On the account</th>}</tr></thead>
              <tbody>
                {s.lines.slice(0, 15).map((l) => (
                  <tr key={l.external_id}>
                    <td>{shortDate(l.txn_date)}</td><td>{l.description}</td>
                    <td style={{ textAlign: "right" }}>{formatInr(l.amount)}</td>
                    <td style={{ textAlign: "right" }}>{l.balance_after ? formatInr(l.balance_after) : ""}</td>
                    {preview && <td>{preview.duplicateIds.has(l.external_id) ? "Already there" : "New"}</td>}
                  </tr>
                ))}
              </tbody>
            </table>
            {s.lines.length > 15 && <span className="t13">The first 15 of {s.lines.length} lines.</span>}
          </div>
          {preview ? (
            <div style={{ display: "flex", flexWrap: "wrap", gap: 12, alignItems: "center" }}>
              <span>{preview.fresh} new, {preview.duplicates} already on the account{preview.latest ? ` (it holds lines from ${shortDate(preview.earliest)} to ${shortDate(preview.latest)})` : ""}.</span>
              <button type="button" className="btn primary" disabled={busy !== null || preview.fresh === 0} onClick={() => void run()}>{busy === "import" ? "Importing…" : `Import ${preview.fresh} line${preview.fresh === 1 ? "" : "s"}`}</button>
            </div>
          ) : (
            <div><button type="button" className="btn primary" disabled={busy !== null || !chosen} onClick={() => void check()}>{busy === "check" ? "Checking…" : "Check against the account"}</button></div>
          )}
        </section>
      )}
    </main>
  );
}
