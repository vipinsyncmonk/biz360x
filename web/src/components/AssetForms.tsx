"use client";
import { useState } from "react";
import { api, newIdempotencyKey, unwrap } from "@/lib/api";
import { Asset, AssetCategory, AssetForm, BOOKING, Preview, assetBody, assetProblem, blankAsset, currentPeriod, disposeProblem, lifeText, monthLabel, moneyText } from "@/lib/assets";
import { formatInr, isoDate } from "@/lib/format";
import { ErrorNote } from "./ui";
import { Field, Shell, grid } from "./TradeForms";

const inr = (v: string) => formatInr(v);

/** Register an asset. The category fills in the usual life and residual value; the person can change both. */
export function AddAsset({ entityId, categories, onDone, onCancel }: { entityId: string; categories: AssetCategory[]; onDone: () => void; onCancel: () => void }) {
  const [f, setF] = useState<AssetForm>(blankAsset);
  const set = (p: Partial<AssetForm>) => setF((x) => ({ ...x, ...p }));
  const today = isoDate(new Date());
  const cat = categories.find((c) => c.category === f.category);
  const problem = assetProblem(f, today);
  const help = BOOKING.find((b) => b[0] === f.booking)![2];
  return (
    <Shell label="Register asset" problem={problem} started={!!f.name || !!f.cost} onCancel={onCancel} onSubmit={async () => {
      unwrap(await api.POST("/entities/{entityId}/fixed-assets", { params: { path: { entityId }, header: { "Idempotency-Key": newIdempotencyKey() } }, body: assetBody(f) as never })); onDone();
    }}>
      <div style={grid}>
        <Field id="fa-name" label="Asset"><input id="fa-name" className="input" style={{ height: 40 }} autoComplete="off" value={f.name} onChange={(e) => set({ name: e.target.value })} /></Field>
        <Field id="fa-cat" label="Category">
          <select id="fa-cat" className="input" style={{ height: 40 }} value={f.category} onChange={(e) => set({ category: e.target.value })}>{categories.map((c) => <option key={c.category} value={c.category}>{c.label}</option>)}</select>
        </Field>
        <Field id="fa-acq" label="Acquired on"><input id="fa-acq" type="date" className="input" style={{ height: 40 }} max={today} value={f.acquiredOn} onChange={(e) => set({ acquiredOn: e.target.value })} /></Field>
        <Field id="fa-use" label="Put to use on (if later)"><input id="fa-use" type="date" className="input" style={{ height: 40 }} value={f.putToUseOn} onChange={(e) => set({ putToUseOn: e.target.value })} /></Field>
        <Field id="fa-cost" label="Cost (₹, without GST you claim)"><input id="fa-cost" className="input mono" style={{ height: 40 }} inputMode="decimal" autoComplete="off" value={f.cost} onChange={(e) => set({ cost: e.target.value })} /></Field>
        <Field id="fa-res" label="Residual value (₹)"><input id="fa-res" className="input mono" style={{ height: 40 }} inputMode="decimal" autoComplete="off" placeholder={cat ? `${cat.residual_pct}% of cost` : ""} value={f.residual} onChange={(e) => set({ residual: e.target.value })} /></Field>
        <Field id="fa-method" label="Method">
          <select id="fa-method" className="input" style={{ height: 40 }} value={f.method} onChange={(e) => set({ method: e.target.value as AssetForm["method"] })}><option value="slm">Straight line</option><option value="wdv">Written down value</option></select>
        </Field>
        {f.method === "slm"
          ? <Field id="fa-life" label="Life (months)"><input id="fa-life" className="input mono" style={{ height: 40 }} inputMode="numeric" autoComplete="off" placeholder={cat ? `${cat.life_months} (${lifeText(cat.life_months)})` : ""} value={f.lifeMonths} onChange={(e) => set({ lifeMonths: e.target.value })} /></Field>
          : <Field id="fa-rate" label="Rate (% a year)"><input id="fa-rate" className="input mono" style={{ height: 40 }} inputMode="decimal" autoComplete="off" value={f.ratePct} onChange={(e) => set({ ratePct: e.target.value })} /></Field>}
        <Field id="fa-loc" label="Location (optional)"><input id="fa-loc" className="input" style={{ height: 40 }} autoComplete="off" value={f.location} onChange={(e) => set({ location: e.target.value })} /></Field>
        <Field id="fa-serial" label="Serial no. (optional)"><input id="fa-serial" className="input" style={{ height: 40 }} autoComplete="off" value={f.serialNo} onChange={(e) => set({ serialNo: e.target.value })} /></Field>
      </div>
      <div style={grid}>
        <Field id="fa-booking" label="How it was booked">
          <select id="fa-booking" className="input" style={{ height: 40 }} value={f.booking} onChange={(e) => set({ booking: e.target.value as AssetForm["booking"] })}>{BOOKING.map(([v, label]) => <option key={v} value={v}>{label}</option>)}</select>
        </Field>
        {f.booking === "opening" && <>
          <Field id="fa-oacc" label="Depreciation charged so far (₹)"><input id="fa-oacc" className="input mono" style={{ height: 40 }} inputMode="decimal" autoComplete="off" value={f.openingAccumulated} onChange={(e) => set({ openingAccumulated: e.target.value })} /></Field>
          <Field id="fa-othru" label="…as of (a month-end)"><input id="fa-othru" type="date" className="input" style={{ height: 40 }} value={f.openingThrough} onChange={(e) => set({ openingThrough: e.target.value })} /></Field>
        </>}
      </div>
      <span className="t13">{help} Lives and residual values are the Schedule II starter list: have your CA confirm them.</span>
    </Shell>
  );
}

/** Choose a month, see what would post, post it. */
export function RunDepreciation({ entityId, onDone, onCancel }: { entityId: string; onDone: () => void; onCancel: () => void }) {
  const [period, setPeriod] = useState(currentPeriod());
  const [preview, setPreview] = useState<Preview | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  async function show(next: string) {
    setPeriod(next); setPreview(null); setError(null);
    if (!/^\d{4}-\d{2}$/.test(next)) return;
    try { setPreview(unwrap(await api.GET("/entities/{entityId}/depreciation/preview", { params: { path: { entityId }, query: { period: next } } })) as unknown as Preview); } catch (e) { setError(e); }
  }
  async function post() {
    setBusy(true); setError(null);
    try { unwrap(await api.POST("/entities/{entityId}/depreciation/run", { params: { path: { entityId }, header: { "Idempotency-Key": newIdempotencyKey() } }, body: { period } as never })); onDone(); }
    catch (e) { setError(e); setBusy(false); }
  }
  const nothing = !!preview && preview.months.length === 0;
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
      <Field id="dep-period" label="Depreciate up to"><input id="dep-period" type="month" className="input" style={{ height: 40, maxWidth: 220 }} max={currentPeriod()} value={period} onChange={(e) => void show(e.target.value)} /></Field>
      <button type="button" className="btn" style={{ alignSelf: "flex-start" }} onClick={() => void show(period)}>Show what will post</button>
      <ErrorNote error={error} />
      {nothing && <span className="t13" role="status">Nothing to post up to {monthLabel(`${period}-01`)}: every month is already depreciated.</span>}
      {preview && !nothing && (
        <div className="tablewrap"><table style={{ minWidth: 420 }} aria-label="Depreciation to post">
          <thead><tr><th scope="col">Month</th><th scope="col">Assets</th><th scope="col" style={{ textAlign: "right" }}>Depreciation</th></tr></thead>
          <tbody>{preview.months.map((m) => <tr key={m.month}><td>{monthLabel(m.month)}</td><td>{m.assets.length}</td><td className="mono" style={{ textAlign: "right" }}>{inr(m.total)}</td></tr>)}</tbody>
          <tfoot><tr><th scope="row" colSpan={2}>Total</th><td className="mono" style={{ textAlign: "right", fontWeight: 600 }}>{inr(preview.total)}</td></tr></tfoot>
        </table></div>
      )}
      <span style={{ display: "flex", gap: 10 }}>
        <button type="button" className="btn primary" disabled={busy || !preview || nothing} onClick={() => void post()}>{busy ? "Posting…" : "Post depreciation"}</button>
        <button type="button" className="btn" onClick={onCancel}>Cancel</button>
      </span>
      <span className="t13">One journal is posted for each month: depreciation expense against accumulated depreciation. A month in a locked period is refused.</span>
    </div>
  );
}

/** Sell or write off. The depreciation to the day of sale is charged and the gain or loss is posted. */
export function DisposeAsset({ entityId, asset, onDone, onCancel }: { entityId: string; asset: Asset; onDone: () => void; onCancel: () => void }) {
  const [date, setDate] = useState("");
  const [proceeds, setProceeds] = useState("");
  const today = isoDate(new Date());
  const problem = disposeProblem(date, proceeds, asset, today);
  return (
    <Shell compact label="Sell or write off" problem={problem} started={!!date || !!proceeds} onCancel={onCancel} onSubmit={async () => {
      unwrap(await api.POST("/entities/{entityId}/fixed-assets/{id}/dispose", {
        params: { path: { entityId, id: asset.id }, header: { "Idempotency-Key": newIdempotencyKey() } }, body: { disposed_on: date, proceeds: moneyText(proceeds) } as never,
      })); onDone();
    }}>
      <div style={grid}>
        <Field id={`ds-d-${asset.id}`} label="Sold on"><input id={`ds-d-${asset.id}`} type="date" className="input" style={{ height: 40 }} max={today} value={date} onChange={(e) => setDate(e.target.value)} /></Field>
        <Field id={`ds-p-${asset.id}`} label="Received (₹, 0 to write off)"><input id={`ds-p-${asset.id}`} className="input mono" style={{ height: 40 }} inputMode="decimal" autoComplete="off" value={proceeds} onChange={(e) => setProceeds(e.target.value)} /></Field>
      </div>
      <span className="t13">The money is posted to your bank account. GST on the sale is not posted from here, and depreciation must be posted up to the month before.</span>
    </Shell>
  );
}
