"use client";
import { useState } from "react";
import { api, newIdempotencyKey, unwrap } from "@/lib/api";
import { BankTxn, ExportHint, proceedsBody, proceedsForm, proceedsProblem } from "@/lib/finance";
import { formatInr } from "@/lib/format";
import { ErrorNote } from "@/components/ui";

/** Take a bank credit as export proceeds. The statement shows rupees after the bank's charges and its own rate, so the person says how many dollars it was and what the bank deducted. */
export function ProceedsRow({ entityId, txn, hint, onDone, onCancel }: { entityId: string; txn: BankTxn; hint: ExportHint; onDone: () => void; onCancel: () => void }) {
  const [f, setF] = useState(() => proceedsForm(hint));
  const [busy, setBusy] = useState(false);
  const [failure, setFailure] = useState<unknown>(null);
  const set = (p: Partial<typeof f>) => setF((x) => ({ ...x, ...p }));
  const problem = proceedsProblem(f);
  const ccy = hint.currency ?? "";
  const k = txn.id;

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (problem) return;
    setBusy(true); setFailure(null);
    try {
      unwrap(await api.POST("/entities/{entityId}/bank-transactions/{id}/export-proceeds", {
        params: { path: { entityId, id: txn.id }, header: { "Idempotency-Key": newIdempotencyKey() } }, body: proceedsBody(hint.shipping_bill_id, f),
      }));
      onDone();
    } catch (err) { setFailure(err); } finally { setBusy(false); }
  }

  return (
    <tr>
      <td colSpan={4} style={{ padding: "12px 20px 16px", background: "var(--bg-2, rgba(0,0,0,0.03))" }}>
        <form onSubmit={(e) => void submit(e)} aria-label="Record as export proceeds" style={{ display: "flex", flexDirection: "column", gap: 10 }}>
          <span className="t13">The bank credited {formatInr(txn.amount)} after taking its own charges and converting at its own rate. Enter the {ccy} amount from your FIRC or the customer&apos;s advice and what the bank deducted. The books settle the invoice at its own rate and post the difference as exchange gain or loss.</span>
          <ErrorNote error={failure} />
          <div style={{ display: "flex", flexWrap: "wrap", gap: 12, alignItems: "flex-end" }}>
            <div className="field"><label htmlFor={`bp-a-${k}`}>Amount received ({ccy})</label><input id={`bp-a-${k}`} className="input mono" style={{ height: 40 }} inputMode="decimal" value={f.foreign} onChange={(e) => set({ foreign: e.target.value })} /></div>
            <div className="field"><label htmlFor={`bp-c-${k}`}>Bank charges deducted (₹)</label><input id={`bp-c-${k}`} className="input mono" style={{ height: 40 }} inputMode="decimal" placeholder="0.00" value={f.charges} onChange={(e) => set({ charges: e.target.value })} /></div>
            <div className="field"><label htmlFor={`bp-f-${k}`}>FIRC no. (optional)</label><input id={`bp-f-${k}`} className="input mono" style={{ height: 40 }} value={f.firc} onChange={(e) => set({ firc: e.target.value })} /></div>
            <span style={{ display: "flex", gap: 10 }}>
              <button type="submit" className="btn primary" style={{ height: 40 }} disabled={busy || !!problem}>{busy ? "Saving…" : "Record as export proceeds"}</button>
              <button type="button" className="btn" style={{ height: 40 }} onClick={onCancel}>Cancel</button>
            </span>
          </div>
          {problem && f.foreign !== "" && <span className="t13" role="status">{problem}</span>}
        </form>
      </td>
    </tr>
  );
}
