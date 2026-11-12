"use client";
import { useState } from "react";
import { api, newIdempotencyKey, unwrap } from "@/lib/api";
import { isoDate } from "@/lib/format";
import { AdjustForm, AdjustKind, BOOKINGS, GST_RATES, ItemForm, KINDS, StockItem, adjustBody, adjustProblem, blankAdjust, blankItem, costText, countNote, itemBody, itemProblem, qtyText } from "@/lib/inventory";
import { Field, Shell, grid } from "./TradeForms";

const h40 = { height: 40 } as const;

/** Add an item that is kept in stock. Only goods; services have no stock. */
export function AddItem({ entityId, onDone, onCancel }: { entityId: string; onDone: () => void; onCancel: () => void }) {
  const [f, setF] = useState<ItemForm>(blankItem);
  const set = (p: Partial<ItemForm>) => setF((x) => ({ ...x, ...p }));
  return (
    <Shell label="Add item" problem={itemProblem(f)} started={!!f.sku || !!f.name} onCancel={onCancel} onSubmit={async () => {
      unwrap(await api.POST("/entities/{entityId}/items", { params: { path: { entityId } }, body: itemBody(f) as never })); onDone();
    }}>
      <div style={grid}>
        <Field id="it-sku" label="Item code (SKU)"><input id="it-sku" className="input mono" style={h40} autoComplete="off" maxLength={40} value={f.sku} onChange={(e) => set({ sku: e.target.value })} /></Field>
        <Field id="it-name" label="Name"><input id="it-name" className="input" style={h40} autoComplete="off" value={f.name} onChange={(e) => set({ name: e.target.value })} /></Field>
        <Field id="it-hsn" label="HSN code"><input id="it-hsn" className="input mono" style={h40} inputMode="numeric" autoComplete="off" maxLength={8} value={f.hsn} onChange={(e) => set({ hsn: e.target.value })} /></Field>
        <Field id="it-uqc" label="Unit"><input id="it-uqc" className="input mono" style={h40} autoComplete="off" maxLength={8} value={f.uqc} onChange={(e) => set({ uqc: e.target.value.toUpperCase() })} /></Field>
        <Field id="it-gst" label="GST rate (%)">
          <select id="it-gst" className="input" style={h40} value={f.gstRate} onChange={(e) => set({ gstRate: e.target.value })}>{GST_RATES.map((r) => <option key={r} value={r}>{r}</option>)}</select>
        </Field>
        <Field id="it-buy" label="Purchase rate (₹, optional)"><input id="it-buy" className="input mono" style={h40} inputMode="decimal" autoComplete="off" value={f.purchaseRate} onChange={(e) => set({ purchaseRate: e.target.value })} /></Field>
        <Field id="it-sell" label="Sale rate (₹, optional)"><input id="it-sell" className="input mono" style={h40} inputMode="decimal" autoComplete="off" value={f.saleRate} onChange={(e) => set({ saleRate: e.target.value })} /></Field>
      </div>
      <span className="t13">The item is kept in stock from now on: a booked purchase bill with this item adds to it and an issued invoice takes from it. The HSN code and GST rate are yours to confirm with your CA.</span>
    </Shell>
  );
}

/** Opening stock, a stock count or a write-off. The journal is posted with it. */
export function AdjustStock({ entityId, items, itemId, kind, onDone, onCancel }: { entityId: string; items: StockItem[]; itemId?: string; kind?: AdjustKind; onDone: () => void; onCancel: () => void }) {
  const [f, setF] = useState<AdjustForm>(() => blankAdjust(itemId ?? items[0]?.item_id ?? "", kind ?? "count"));
  const set = (p: Partial<AdjustForm>) => setF((x) => ({ ...x, ...p }));
  const today = isoDate(new Date());
  const item = items.find((i) => i.item_id === f.itemId);
  const help = KINDS.find((k) => k[0] === f.kind)![2];
  const note = f.kind === "count" && item ? countNote(item.qty, f.counted, item.uqc) : null;
  const sfx = itemId ? `-${itemId}` : "";
  return (
    <Shell compact={!!itemId} label="Save adjustment" problem={adjustProblem(f, today)} started={!!f.qty || !!f.counted || !!f.reason} onCancel={onCancel} onSubmit={async () => {
      unwrap(await api.POST("/entities/{entityId}/stock-adjustments", { params: { path: { entityId }, header: { "Idempotency-Key": newIdempotencyKey() } }, body: adjustBody(f) as never })); onDone();
    }}>
      <div style={grid}>
        {!itemId && <Field id="adj-item" label="Item">
          <select id="adj-item" className="input" style={h40} value={f.itemId} onChange={(e) => set({ itemId: e.target.value })}>{items.map((i) => <option key={i.item_id} value={i.item_id}>{i.sku} · {i.name}</option>)}</select>
        </Field>}
        <Field id={`adj-kind${sfx}`} label="What happened">
          <select id={`adj-kind${sfx}`} className="input" style={h40} value={f.kind} onChange={(e) => set({ kind: e.target.value as AdjustKind })}>{KINDS.map(([v, label]) => <option key={v} value={v}>{label}</option>)}</select>
        </Field>
        <Field id={`adj-date${sfx}`} label="Date"><input id={`adj-date${sfx}`} type="date" className="input" style={h40} max={today} value={f.date} onChange={(e) => set({ date: e.target.value })} /></Field>
        {f.kind === "count"
          ? <Field id={`adj-counted${sfx}`} label={`Quantity counted${item ? ` (${item.uqc})` : ""}`}><input id={`adj-counted${sfx}`} className="input mono" style={h40} inputMode="decimal" autoComplete="off" value={f.counted} onChange={(e) => set({ counted: e.target.value })} /></Field>
          : <Field id={`adj-qty${sfx}`} label={`Quantity${item ? ` (${item.uqc})` : ""}`}><input id={`adj-qty${sfx}`} className="input mono" style={h40} inputMode="decimal" autoComplete="off" value={f.qty} onChange={(e) => set({ qty: e.target.value })} /></Field>}
        {f.kind !== "write_off" && <Field id={`adj-cost${sfx}`} label={f.kind === "opening" ? "Cost of one unit (₹)" : "Cost of one unit (₹, only if stock is found and has no cost)"}>
          <input id={`adj-cost${sfx}`} className="input mono" style={h40} inputMode="decimal" autoComplete="off" value={f.unitCost} onChange={(e) => set({ unitCost: e.target.value })} />
        </Field>}
        {f.kind === "opening"
          ? <Field id={`adj-booking${sfx}`} label="In the books">
            <select id={`adj-booking${sfx}`} className="input" style={h40} value={f.booking} onChange={(e) => set({ booking: e.target.value as AdjustForm["booking"] })}>{BOOKINGS.map(([v, label]) => <option key={v} value={v}>{label}</option>)}</select>
          </Field>
          : <Field id={`adj-reason${sfx}`} label="Reason"><input id={`adj-reason${sfx}`} className="input" style={h40} autoComplete="off" maxLength={200} value={f.reason} onChange={(e) => set({ reason: e.target.value })} /></Field>}
      </div>
      {item && <span className="t13">On hand now: {qtyText(item.qty)} {item.uqc} at an average of ₹{costText(item.average_cost)}.{note ? ` ${note}.` : ""}</span>}
      <span className="t13">{help}{f.kind === "opening" ? " If you imported an opening trial balance that already has your stock value, choose that so it is not counted twice; left to us, we post it only when no opening balances were imported." : " The journal is posted against Stock adjustments, and a date in a locked period is refused."}</span>
    </Shell>
  );
}
