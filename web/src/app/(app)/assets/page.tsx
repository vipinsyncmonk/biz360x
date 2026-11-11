"use client";
import { useState } from "react";
import { api, unwrap } from "@/lib/api";
import { Asset, AssetCategory, methodText, monthLabel } from "@/lib/assets";
import { formatInr, shortDate } from "@/lib/format";
import { useLoad } from "@/lib/hooks";
import { useSession } from "@/lib/session";
import { AddAsset, DisposeAsset, RunDepreciation } from "@/components/AssetForms";
import { ErrorNote, Loading, Pill } from "@/components/ui";

interface Register { data: Asset[]; totals: { cost: string; accumulated_depreciation: string; book_value: string } }
type Panel = "add" | "run" | null;

export default function Assets() {
  const { entity } = useSession();
  const id = entity?.id ?? null;
  const [tab, setTab] = useState<"in_use" | "disposed">("in_use");
  const [panel, setPanel] = useState<Panel>(null);
  const [open, setOpen] = useState<{ kind: "schedule" | "dispose"; id: string } | null>(null);

  const p = { path: { entityId: id! } };
  const register = useLoad(async () => unwrap(await api.GET("/entities/{entityId}/fixed-assets", { params: { ...p, query: { status: tab } } })) as unknown as Register, id ? `${id}|${tab}` : null);
  const totals = useLoad(async () => unwrap(await api.GET("/entities/{entityId}/fixed-assets", { params: { ...p, query: { status: "in_use" } } })).totals as unknown as Register["totals"], id);
  const cats = useLoad(async () => unwrap(await api.GET("/entities/{entityId}/fixed-asset-categories", { params: p })).data as unknown as AssetCategory[], id);
  const detail = useLoad(async () => (open?.kind === "schedule" ? (unwrap(await api.GET("/entities/{entityId}/fixed-assets/{id}", { params: { path: { entityId: id!, id: open.id } } })) as unknown as Asset) : null), id && open?.kind === "schedule" ? `${id}|${open.id}` : null);

  const reload = () => { register.reload(); totals.reload(); setPanel(null); setOpen(null); };
  const rows = register.data?.data ?? [];
  const t = totals.data;
  const toggle = (kind: "schedule" | "dispose", rowId: string) => setOpen(open && open.kind === kind && open.id === rowId ? null : { kind, id: rowId });
  const label = (c: string) => cats.data?.find((x) => x.category === c)?.label ?? c;

  return (
    <main className="page">
      <div style={{ display: "flex", flexWrap: "wrap", gap: 16, justifyContent: "space-between", alignItems: "flex-end" }}>
        <div>
          <span className="mono t13">{entity?.legal_name}</span>
          <h1 className="disp" style={{ margin: "6px 0 0", fontSize: 36 }}>Fixed assets</h1>
        </div>
        <span style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
          <button type="button" className="btn" aria-expanded={panel === "run"} onClick={() => setPanel(panel === "run" ? null : "run")}>Run depreciation</button>
          <button type="button" className="btn primary" aria-expanded={panel === "add"} onClick={() => setPanel(panel === "add" ? null : "add")}>+ Asset</button>
        </span>
      </div>
      <span className="t13">The register of what you own for more than a year, with Companies Act depreciation charged monthly to the books. Income-tax depreciation and GST on a sale are not calculated here. Lives and residual values are a starter list for your CA to review.</span>

      <section aria-label="Key numbers" className="kpis">
        <Tile label="Cost of assets in use" value={t ? formatInr(t.cost, { decimals: false }) : "…"} note="" />
        <Tile label="Depreciation so far" value={t ? formatInr(t.accumulated_depreciation, { decimals: false }) : "…"} note="Posted to the books" />
        <Tile label="Book value" value={t ? formatInr(t.book_value, { decimals: false }) : "…"} note="Cost less depreciation" />
      </section>
      <ErrorNote error={register.error ?? totals.error ?? cats.error} />

      {id && panel === "add" && <section className="card card-pad" aria-label="Register an asset"><AddAsset entityId={id} categories={cats.data ?? []} onDone={reload} onCancel={() => setPanel(null)} /></section>}
      {id && panel === "run" && <section className="card card-pad" aria-label="Run depreciation"><RunDepreciation entityId={id} onDone={reload} onCancel={() => setPanel(null)} /></section>}

      <div className="seg" role="tablist" aria-label="In use or disposed" style={{ alignSelf: "flex-start" }}>
        <button type="button" role="tab" aria-selected={tab === "in_use"} onClick={() => { setTab("in_use"); setOpen(null); }}>In use</button>
        <button type="button" role="tab" aria-selected={tab === "disposed"} onClick={() => { setTab("disposed"); setOpen(null); }}>Sold or written off</button>
      </div>

      <section className="card" style={{ overflow: "hidden" }}>
        <div style={{ padding: "18px 20px" }}><h2 style={{ margin: 0 }}>{tab === "in_use" ? "Assets in use" : "Sold or written off"}</h2></div>
        {register.loading && !register.data ? <Loading what="assets" /> : rows.length === 0
          ? <div className="empty">{tab === "in_use" ? "No assets yet. Register a computer, a vehicle or a machine and depreciation is worked out for you." : "Nothing has been sold or written off."}</div>
          : rows.map((a) => (
            <div key={a.id} style={{ borderTop: "1px solid var(--line-2)" }}>
              <div style={{ padding: "14px 20px", display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(190px, 1fr))", gap: 12, alignItems: "start" }}>
                <span style={{ fontWeight: 600 }}>{a.name}<span className="t13 mono" style={{ display: "block", fontWeight: 400 }}>{a.asset_no} · {label(a.category)}{a.location ? ` · ${a.location}` : ""}</span></span>
                <span>Cost {formatInr(a.cost, { decimals: false })}<span className="t13" style={{ display: "block" }}>{methodText(a)}</span></span>
                <span>Book value {formatInr(a.book_value, { decimals: false })}<span className="t13" style={{ display: "block" }}>Depreciated {formatInr(a.accumulated_depreciation, { decimals: false })}</span></span>
                <span>{a.status === "disposed"
                  ? <><Pill tone="ok">Sold {shortDate(a.disposed_on!)}</Pill><span className="t13" style={{ display: "block" }}>Received {formatInr(a.disposal_proceeds ?? "0.00", { decimals: false })}</span></>
                  : <span className="t13">In use since {shortDate(a.put_to_use_on ?? a.acquired_on)}</span>}</span>
              </div>
              <div style={{ padding: "0 20px 14px", display: "flex", gap: 8, flexWrap: "wrap" }}>
                <button type="button" className="btn" aria-expanded={open?.kind === "schedule" && open.id === a.id} aria-label={`Schedule for ${a.asset_no}`} onClick={() => toggle("schedule", a.id)}>Schedule</button>
                {a.status === "in_use" && <button type="button" className="btn" aria-expanded={open?.kind === "dispose" && open.id === a.id} aria-label={`Sell or write off ${a.asset_no}`} onClick={() => toggle("dispose", a.id)}>Sell or write off</button>}
              </div>
              {id && open?.kind === "dispose" && open.id === a.id && <DisposeAsset entityId={id} asset={a} onDone={reload} onCancel={() => setOpen(null)} />}
              {open?.kind === "schedule" && open.id === a.id && (
                <div style={{ padding: "0 20px 14px" }}>
                  <ErrorNote error={detail.error} />
                  {detail.loading && !detail.data ? <Loading what="the schedule" /> : (
                    <div className="tablewrap"><table style={{ minWidth: 460 }} aria-label={`Depreciation schedule for ${a.asset_no}`}>
                      <thead><tr><th scope="col">Month</th><th scope="col" style={{ textAlign: "right" }}>Depreciation</th><th scope="col" style={{ textAlign: "right" }}>Book value after</th><th scope="col">Status</th></tr></thead>
                      <tbody>{(detail.data?.schedule ?? []).map((r) => (
                        <tr key={r.month}><td>{monthLabel(r.month)}</td><td className="mono" style={{ textAlign: "right" }}>{formatInr(r.amount)}</td><td className="mono" style={{ textAlign: "right" }}>{formatInr(r.book_value_after)}</td><td>{r.posted ? "Posted" : "To come"}</td></tr>
                      ))}</tbody>
                    </table></div>
                  )}
                </div>
              )}
            </div>
          ))}
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
