"use client";
import { useState } from "react";
import { api, newIdempotencyKey, unwrap, unwrapEmpty } from "@/lib/api";
import { Account, BankRule, BankTxn, MODE_LABEL, TYPE_ORDER, accountLabel, blankClassify, classifyBody, classifyProblem, ruleText, similarLines } from "@/lib/finance";
import { formatInr } from "@/lib/format";
import { useLoad } from "@/lib/hooks";
import { ErrorNote } from "@/components/ui";

/** Book a bank line to an account: the other half of the entry. The person can also teach a rule, and have it applied to the lines that look the same. */
export function ClassifyRow({ entityId, txn, rows, accounts, defaultAccount, onDone, onCancel }:
  { entityId: string; txn: BankTxn; rows: BankTxn[]; accounts: Account[]; defaultAccount?: string; onDone: (alsoBooked: number) => void; onCancel: () => void }) {
  const [f, setF] = useState(() => blankClassify(defaultAccount));
  const [busy, setBusy] = useState(false);
  const [failure, setFailure] = useState<unknown>(null);
  const set = (p: Partial<typeof f>) => setF((x) => ({ ...x, ...p }));
  const problem = classifyProblem(f, txn);
  const similar = similarLines(rows, txn);
  const k = txn.id;
  const out = Number(txn.amount) < 0;

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (problem) return;
    setBusy(true); setFailure(null);
    try {
      const res = unwrap(await api.POST("/entities/{entityId}/bank-transactions/{id}/classify", {
        params: { path: { entityId, id: txn.id }, header: { "Idempotency-Key": newIdempotencyKey() } }, body: classifyBody(f) as never,
      })) as unknown as { also_booked: number };
      onDone(res.also_booked);
    } catch (err) { setFailure(err); } finally { setBusy(false); }
  }

  return (
    <tr>
      <td colSpan={4} style={{ padding: "12px 20px 16px", background: "var(--bg-2, rgba(0,0,0,0.03))" }}>
        <form onSubmit={(e) => void submit(e)} aria-label="Book this bank line" style={{ display: "flex", flexDirection: "column", gap: 10 }}>
          <span className="t13">{out ? "Money out" : "Money in"} {formatInr(txn.amount.replace("-", ""))}{txn.mode ? ` · ${MODE_LABEL[txn.mode] || txn.mode}` : ""}{txn.counterparty_key ? ` · ${txn.counterparty_key}` : ""}. Booking posts a journal: {out ? "the account is debited and the bank credited" : "the bank is debited and the account credited"}.</span>
          <ErrorNote error={failure} />
          <div style={{ display: "flex", flexWrap: "wrap", gap: 12, alignItems: "flex-end" }}>
            <div className="field"><label htmlFor={`cl-a-${k}`}>Book to</label>
              <select id={`cl-a-${k}`} className="input" style={{ height: 40, minWidth: 240 }} value={f.account} onChange={(e) => set({ account: e.target.value })}>
                <option value="">Choose an account…</option>
                {TYPE_ORDER.map(([type, label]) => {
                  const list = accounts.filter((a) => a.type === type);
                  return list.length ? <optgroup key={type} label={label}>{list.map((a) => <option key={a.id} value={a.id}>{accountLabel(a)}</option>)}</optgroup> : null;
                })}
              </select></div>
            <div className="field"><label htmlFor={`cl-m-${k}`}>Note (optional)</label><input id={`cl-m-${k}`} className="input" style={{ height: 40 }} autoComplete="off" value={f.memo} onChange={(e) => set({ memo: e.target.value })} /></div>
            <div className="field"><label htmlFor={`cl-r-${k}`}>Next time</label>
              <select id={`cl-r-${k}`} className="input" style={{ height: 40 }} value={f.remember} onChange={(e) => set({ remember: e.target.value as typeof f.remember, applySimilar: false })}>
                <option value="no">Just this line</option>
                {txn.counterparty_key && <option value="counterparty">Remember lines {out ? "to" : "from"} {txn.counterparty_key}</option>}
                <option value="keyword">Remember a word in the narration</option>
              </select></div>
            {f.remember === "keyword" && <div className="field"><label htmlFor={`cl-k-${k}`}>Word to remember</label><input id={`cl-k-${k}`} className="input" style={{ height: 40 }} autoComplete="off" value={f.keyword} onChange={(e) => set({ keyword: e.target.value })} /></div>}
          </div>
          {f.remember !== "no" && (
            <label style={{ display: "flex", gap: 8, alignItems: "center", fontSize: 14 }}>
              <input type="checkbox" checked={f.applySimilar} onChange={(e) => set({ applySimilar: e.target.checked })} />
              {f.remember === "counterparty" ? `Also book the ${similar.length} other open ${similar.length === 1 ? "line" : "lines"} to ${txn.counterparty_key}` : "Also book the other open lines that mention that word"}
            </label>
          )}
          {problem && (f.account === "" ? false : true) && <span className="t13" role="status">{problem}</span>}
          <span style={{ display: "flex", gap: 10 }}>
            <button type="submit" className="btn primary" style={{ height: 40 }} disabled={busy || !!problem}>{busy ? "Booking…" : "Book it"}</button>
            <button type="button" className="btn" style={{ height: 40 }} onClick={onCancel}>Cancel</button>
          </span>
        </form>
      </td>
    </tr>
  );
}

/** The rules the company has taught, with how often each has been used, and a way to remove one. */
export function BankRules({ entityId, version, onChange }: { entityId: string; version: number; onChange: () => void }) {
  const rules = useLoad(async () => unwrap(await api.GET("/entities/{entityId}/bank-rules", { params: { path: { entityId } } })) as unknown as BankRule[], `${entityId}|${version}`);
  const [failure, setFailure] = useState<unknown>(null);
  const list = rules.data ?? [];
  if (list.length === 0) return null;
  async function remove(r: BankRule) {
    setFailure(null);
    try { unwrapEmpty(await api.DELETE("/entities/{entityId}/bank-rules/{id}", { params: { path: { entityId, id: r.id } } })); rules.reload(); onChange(); }
    catch (e) { setFailure(e); }
  }
  return (
    <section className="card" aria-label="Booking rules" style={{ overflow: "hidden" }}>
      <div style={{ padding: "14px 20px", borderBottom: "1px solid var(--line-2)" }}><h2 style={{ margin: 0 }}>Booking rules</h2><span className="t13">What you taught: matching lines are suggested for the account. Removing a rule does not undo lines already booked.</span></div>
      <div style={{ padding: "0 20px" }}><ErrorNote error={failure} /></div>
      <div className="tablewrap">
        <table style={{ minWidth: 520 }}>
          <thead><tr><th scope="col">When</th><th scope="col">Book to</th><th scope="col" style={{ textAlign: "right" }}>Used</th><th scope="col"><span className="sr">Remove</span></th></tr></thead>
          <tbody>
            {list.map((r) => (
              <tr key={r.id}><td>{ruleText(r)}</td><td>{r.account_code} · {r.account_name}</td><td className="mono" style={{ textAlign: "right" }}>{r.hits}</td>
                <td style={{ textAlign: "right" }}><button type="button" className="btn" style={{ height: 32, fontSize: 13 }} onClick={() => void remove(r)} aria-label={`Remove rule: ${ruleText(r)}`}>Remove</button></td></tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}
