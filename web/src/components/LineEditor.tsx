"use client";
import { GST_RATES, blankLine, lineProblem, type LineDraft } from "@/lib/billing";

/** The items table shared by the invoice and quote editors. Field-level rules are the server's; this only stops useless calls. */
export function LineEditor({ lines, onChange, touched }: { lines: LineDraft[]; onChange: (lines: LineDraft[]) => void; touched: boolean }) {
  const setLine = (i: number, patch: Partial<LineDraft>) => onChange(lines.map((l, j) => (j === i ? { ...l, ...patch } : l)));
  const problems = lines.map(lineProblem);
  return (
    <section className="card" style={{ overflow: "hidden" }}>
      <div className="cardhead"><h2>Items</h2><button type="button" className="btn" onClick={() => onChange([...lines, blankLine()])}>+ Add item</button></div>
      <div className="tablewrap">
        <table style={{ minWidth: 820 }}>
          <thead><tr><th scope="col">Description</th><th scope="col">HSN / SAC</th><th scope="col">Qty</th><th scope="col">Rate (₹)</th><th scope="col">Discount (₹)</th><th scope="col">GST %</th><th scope="col"><span className="sr">Remove</span></th></tr></thead>
          <tbody>
            {lines.map((l, i) => (
              <tr key={i}>
                <td style={{ minWidth: 220 }}>
                  <input aria-label={`Description, item ${i + 1}`} className="input" style={{ height: 40, fontSize: 14 }} value={l.description} onChange={(e) => setLine(i, { description: e.target.value })} />
                  {touched && problems[i] && <span className="t13" style={{ color: "var(--amber)" }}>{problems[i]}</span>}
                </td>
                <td><input aria-label={`HSN or SAC, item ${i + 1}`} className="input mono" style={{ height: 40, fontSize: 14, width: 110 }} inputMode="numeric" value={l.hsn_sac} onChange={(e) => setLine(i, { hsn_sac: e.target.value.replace(/\D/g, "").slice(0, 8) })} /></td>
                <td><input aria-label={`Quantity, item ${i + 1}`} className="input mono" style={{ height: 40, fontSize: 14, width: 80 }} inputMode="decimal" value={l.qty} onChange={(e) => setLine(i, { qty: e.target.value })} /></td>
                <td><input aria-label={`Rate, item ${i + 1}`} className="input mono" style={{ height: 40, fontSize: 14, width: 120 }} inputMode="decimal" value={l.rate} onChange={(e) => setLine(i, { rate: e.target.value })} /></td>
                <td><input aria-label={`Discount, item ${i + 1}`} className="input mono" style={{ height: 40, fontSize: 14, width: 100 }} inputMode="decimal" value={l.discount} onChange={(e) => setLine(i, { discount: e.target.value })} /></td>
                <td><select aria-label={`GST rate, item ${i + 1}`} className="input" style={{ height: 40, fontSize: 14, width: 80 }} value={l.gst_rate} onChange={(e) => setLine(i, { gst_rate: e.target.value })}>{GST_RATES.map((r) => <option key={r} value={r}>{r}%</option>)}</select></td>
                <td>{lines.length > 1 && <button type="button" className="btn" style={{ height: 36 }} aria-label={`Remove item ${i + 1}`} onClick={() => onChange(lines.filter((_, j) => j !== i))}>✕</button>}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}
