"use client";
import { useState } from "react";
import { api, unwrap } from "@/lib/api";
import { formatInr, isoDate, shortDate } from "@/lib/format";
import { useLoad } from "@/lib/hooks";
import { Movement, StockList, Valuation, costText, hasDifference, movementLabel, movementQty, outOfStock, qtyText } from "@/lib/inventory";
import { useSession } from "@/lib/session";
import { AddItem, AdjustStock } from "@/components/InventoryForms";
import { ErrorNote, Loading, Pill } from "@/components/ui";

type Panel = "item" | "adjust" | "opening" | null;

export default function Inventory() {
  const { entity } = useSession();
  const id = entity?.id ?? null;
  const [tab, setTab] = useState<"stock" | "valuation">("stock");
  const [panel, setPanel] = useState<Panel>(null);
  const [open, setOpen] = useState<{ kind: "moves" | "adjust"; id: string } | null>(null);
  const [asOf, setAsOf] = useState(isoDate(new Date()));
  const [settingError, setSettingError] = useState<unknown>(null);

  const p = { path: { entityId: id! } };
  const stock = useLoad(async () => unwrap(await api.GET("/entities/{entityId}/stock-items", { params: p })) as unknown as StockList, id);
  const moves = useLoad(async () => (open?.kind === "moves"
    ? (unwrap(await api.GET("/entities/{entityId}/stock-movements", { params: { ...p, query: { item_id: open.id, limit: 100 } } })).data as unknown as Movement[]) : null),
    id && open?.kind === "moves" ? `${id}|${open.id}` : null);
  const valuation = useLoad(async () => (tab === "valuation" && /^\d{4}-\d{2}-\d{2}$/.test(asOf)
    ? (unwrap(await api.GET("/entities/{entityId}/stock-valuation", { params: { ...p, query: { as_of: asOf } } })) as unknown as Valuation) : null),
    id && tab === "valuation" ? `${id}|${asOf}` : null);

  const reload = () => { stock.reload(); valuation.reload(); moves.reload(); setPanel(null); setOpen((o) => (o?.kind === "adjust" ? null : o)); };
  const rows = stock.data?.data ?? [];
  const t = stock.data?.totals;
  const toggle = (kind: "moves" | "adjust", rowId: string) => setOpen(open && open.kind === kind && open.id === rowId ? null : { kind, id: rowId });
  const show = (next: Panel) => setPanel(panel === next ? null : next);

  async function setNegative(allow: boolean) {
    setSettingError(null);
    try { unwrap(await api.PUT("/entities/{entityId}/inventory/settings", { params: p, body: { allow_negative_stock: allow } })); stock.reload(); } catch (e) { setSettingError(e); }
  }

  return (
    <main className="page">
      <div style={{ display: "flex", flexWrap: "wrap", gap: 16, justifyContent: "space-between", alignItems: "flex-end" }}>
        <div>
          <span className="mono t13">{entity?.legal_name}</span>
          <h1 className="disp" style={{ margin: "6px 0 0", fontSize: 36 }}>Inventory</h1>
        </div>
        <span style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
          <button type="button" className="btn" aria-expanded={panel === "opening"} disabled={rows.length === 0} onClick={() => show("opening")}>Opening stock</button>
          <button type="button" className="btn" aria-expanded={panel === "adjust"} disabled={rows.length === 0} onClick={() => show("adjust")}>Adjust stock</button>
          <button type="button" className="btn primary" aria-expanded={panel === "item"} onClick={() => show("item")}>+ Item</button>
        </span>
      </div>
      <span className="t13">Stock of the items you keep, valued at weighted average cost. Booking a purchase bill adds stock and issuing an invoice takes it out, with the cost of goods sold posted to the books. Batches, serial numbers, expiry, manufacturing, FIFO and reorder alerts are not here yet.</span>

      <section aria-label="Key numbers" className="kpis">
        <Tile label="Value of stock" value={t ? formatInr(t.value, { decimals: false }) : "…"} note="At average cost" />
        <Tile label="Items kept in stock" value={t ? String(t.items) : "…"} note="" />
        <Tile label="Out of stock" value={t ? String(t.out_of_stock) : "…"} note={t && t.out_of_stock > 0 ? "Nothing on hand" : ""} />
      </section>
      <ErrorNote error={stock.error} />

      {id && panel === "item" && <section className="card card-pad" aria-label="Add an item"><AddItem entityId={id} onDone={reload} onCancel={() => setPanel(null)} /></section>}
      {id && panel === "adjust" && <section className="card card-pad" aria-label="Adjust stock"><AdjustStock entityId={id} items={rows} onDone={reload} onCancel={() => setPanel(null)} /></section>}
      {id && panel === "opening" && <section className="card card-pad" aria-label="Opening stock"><AdjustStock entityId={id} items={rows} kind="opening" onDone={reload} onCancel={() => setPanel(null)} /></section>}

      <div className="seg" role="tablist" aria-label="Stock on hand or valuation" style={{ alignSelf: "flex-start" }}>
        <button type="button" role="tab" aria-selected={tab === "stock"} onClick={() => setTab("stock")}>Stock on hand</button>
        <button type="button" role="tab" aria-selected={tab === "valuation"} onClick={() => setTab("valuation")}>Valuation</button>
      </div>

      {tab === "stock" && (
        <section className="card" style={{ overflow: "hidden" }}>
          <div style={{ padding: "18px 20px" }}><h2 style={{ margin: 0 }}>Stock on hand</h2></div>
          {stock.loading && !stock.data ? <Loading what="stock" /> : rows.length === 0
            ? <div className="empty">No items are kept in stock yet. Add an item, then record its opening stock or book a purchase bill for it.</div>
            : rows.map((i) => (
              <div key={i.item_id} style={{ borderTop: "1px solid var(--line-2)" }}>
                <div style={{ padding: "14px 20px", display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(190px, 1fr))", gap: 12, alignItems: "start" }}>
                  <span style={{ fontWeight: 600 }}>{i.name}<span className="t13 mono" style={{ display: "block", fontWeight: 400 }}>{i.sku} · HSN {i.hsn_sac}</span></span>
                  <span>{qtyText(i.qty)} {i.uqc} on hand
                    <span className="t13" style={{ display: "block" }}>{i.levels.length > 1 ? i.levels.map((l) => `${l.code} ${qtyText(l.qty)}`).join(" · ") : i.last_moved_on ? `Last moved ${shortDate(i.last_moved_on)}` : "No movements yet"}</span></span>
                  <span>Value {formatInr(i.value)}<span className="t13" style={{ display: "block" }}>Average cost ₹{costText(i.average_cost)}</span></span>
                  <span>{outOfStock(i) ? <Pill tone="due">{i.qty.startsWith("-") ? "Below zero" : "Out of stock"}</Pill> : <Pill tone="ok">In stock</Pill>}</span>
                </div>
                <div style={{ padding: "0 20px 14px", display: "flex", gap: 8, flexWrap: "wrap" }}>
                  <button type="button" className="btn" aria-expanded={open?.kind === "moves" && open.id === i.item_id} aria-label={`Movements for ${i.sku}`} onClick={() => toggle("moves", i.item_id)}>Movements</button>
                  <button type="button" className="btn" aria-expanded={open?.kind === "adjust" && open.id === i.item_id} aria-label={`Adjust ${i.sku}`} onClick={() => toggle("adjust", i.item_id)}>Adjust</button>
                </div>
                {id && open?.kind === "adjust" && open.id === i.item_id && <AdjustStock entityId={id} items={rows} itemId={i.item_id} onDone={reload} onCancel={() => setOpen(null)} />}
                {open?.kind === "moves" && open.id === i.item_id && (
                  <div style={{ padding: "0 20px 14px" }}>
                    <ErrorNote error={moves.error} />
                    {moves.loading && !moves.data ? <Loading what="movements" /> : (moves.data ?? []).length === 0 ? <span className="t13">Nothing has moved yet.</span> : (
                      <div className="tablewrap"><table style={{ minWidth: 620 }} aria-label={`Stock movements for ${i.sku}`}>
                        <thead><tr><th scope="col">Date</th><th scope="col">What</th><th scope="col" style={{ textAlign: "right" }}>Quantity</th><th scope="col" style={{ textAlign: "right" }}>Unit cost</th><th scope="col" style={{ textAlign: "right" }}>Value</th><th scope="col">Journal</th></tr></thead>
                        <tbody>{(moves.data ?? []).map((m) => (
                          <tr key={m.id}><td>{shortDate(m.date)}</td><td>{movementLabel(m)}</td><td className="mono" style={{ textAlign: "right" }}>{movementQty(m)}</td>
                            <td className="mono" style={{ textAlign: "right" }}>{costText(m.unit_cost)}</td><td className="mono" style={{ textAlign: "right" }}>{formatInr(m.value)}</td><td className="mono">{m.entry_no ?? "—"}</td></tr>
                        ))}</tbody>
                      </table></div>
                    )}
                  </div>
                )}
              </div>
            ))}
        </section>
      )}

      {tab === "valuation" && (
        <section className="card" style={{ overflow: "hidden" }} aria-label="Stock valuation">
          <div style={{ padding: "18px 20px", display: "flex", gap: 16, flexWrap: "wrap", alignItems: "flex-end", justifyContent: "space-between" }}>
            <h2 style={{ margin: 0 }}>Stock valuation</h2>
            <div className="field" style={{ maxWidth: 200 }}><label htmlFor="val-asof">As of</label><input id="val-asof" type="date" className="input" style={{ height: 40 }} max={isoDate(new Date())} value={asOf} onChange={(e) => setAsOf(e.target.value)} /></div>
          </div>
          <div style={{ padding: "0 20px 18px", display: "flex", flexDirection: "column", gap: 12 }}>
            <ErrorNote error={valuation.error} />
            {valuation.loading && !valuation.data ? <Loading what="the valuation" /> : !valuation.data ? null : valuation.data.items.length === 0
              ? <div className="empty">There was no stock on {shortDate(valuation.data.as_of)}.</div>
              : <>
                <div className="tablewrap"><table style={{ minWidth: 560 }} aria-label="Stock valuation by item">
                  <thead><tr><th scope="col">Item</th><th scope="col" style={{ textAlign: "right" }}>Quantity</th><th scope="col" style={{ textAlign: "right" }}>Average cost</th><th scope="col" style={{ textAlign: "right" }}>Value</th></tr></thead>
                  <tbody>{valuation.data.items.map((r) => (
                    <tr key={r.item_id}><td>{r.name}<span className="t13 mono" style={{ display: "block" }}>{r.sku}</span></td><td className="mono" style={{ textAlign: "right" }}>{qtyText(r.qty)} {r.uqc}</td>
                      <td className="mono" style={{ textAlign: "right" }}>{costText(r.average_cost)}</td><td className="mono" style={{ textAlign: "right" }}>{formatInr(r.value)}</td></tr>
                  ))}</tbody>
                  <tfoot><tr><th scope="row" colSpan={3}>Total</th><td className="mono" style={{ textAlign: "right", fontWeight: 600 }}>{formatInr(valuation.data.total_value)}</td></tr></tfoot>
                </table></div>
                <span className="t13" role="status">{hasDifference(valuation.data)
                  ? `The Inventory account in the books shows ${formatInr(valuation.data.ledger_balance)} on this date, ${formatInr(valuation.data.difference)} apart from the stock here. That happens when stock sits in another account in your opening balances, or a journal was posted to Inventory by hand.`
                  : `This agrees with the Inventory account in the books (${formatInr(valuation.data.ledger_balance)}).`}</span>
              </>}
          </div>
        </section>
      )}

      <section className="card card-pad" aria-label="Inventory settings" style={{ display: "flex", flexDirection: "column", gap: 8 }}>
        <label style={{ display: "flex", gap: 10, alignItems: "center" }}>
          <input type="checkbox" checked={stock.data?.allow_negative_stock ?? false} disabled={!stock.data} onChange={(e) => void setNegative(e.target.checked)} />
          <span>Allow stock to go below zero</span>
        </label>
        <span className="t13">Off, an invoice or a write-off for more than is on hand is refused. On, you can sell before the purchase bill is entered, and the cost of those sales is an estimate from the last cost until the bill is booked. Only an owner or approver can change this.</span>
        <ErrorNote error={settingError} />
      </section>
    </main>
  );
}

function Tile({ label, value, note }: { label: string; value: string; note: string }) {
  return (
    <div className="card kpi">
      <span className="t13">{label}</span>
      <span className="disp v" style={{ fontSize: 28 }}>{value}</span>
      <span className="t13">{note}</span>
    </div>
  );
}
