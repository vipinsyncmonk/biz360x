"use client";
import Link from "next/link";
import { useState } from "react";
import { LAYERS, RAILS, TRACKS, Module, Track, countLine, coverageLine, railCounts, switchedOn } from "@/lib/modules";
import { Icon } from "@/components/ui";

export default function Modules() {
  const [track, setTrack] = useState<Track | "All">("All");
  const rails = railCounts();

  return (
    <main className="page" style={{ gap: 28 }}>
      <div style={{ display: "flex", flexWrap: "wrap", gap: 20, justifyContent: "space-between", alignItems: "flex-end" }}>
        <div style={{ display: "flex", flexDirection: "column", gap: 8, maxWidth: 720 }}>
          <span className="mono t13">MODULE MAP · BIZ360X</span>
          <h1 className="disp" style={{ margin: 0, fontSize: 40, lineHeight: 1.05 }}>Four layers, one ledger. Switch on what your stage needs.</h1>
          <p style={{ margin: 0, fontSize: 15, lineHeight: 1.55, color: "var(--ink-2)" }}>
            Every module writes to the same foundation (company, ledger, documents, approvals and the obligation graph), so a payroll run, an invoice or a share allotment updates compliance
            without anyone re-keying it. Modules that open a screen are ready; the ones marked Planned are not built yet. Pick a track and the modules outside it turn dashed.
          </p>
        </div>
        <div style={{ display: "flex", flexDirection: "column", gap: 8, alignItems: "flex-start" }}>
          <span className="t13">Show modules for</span>
          <div className="seg" role="group" aria-label="Track">
            {(["All", ...TRACKS] as const).map((t) => <button key={t} type="button" aria-pressed={track === t} onClick={() => setTrack(t)}>{t}</button>)}
          </div>
          <span className="mono t13" role="status">{countLine(track)} · {coverageLine(track)}</span>
        </div>
      </div>

      {LAYERS.map((l) => (
        <section key={l.key} aria-label={l.title} style={{ display: "flex", flexWrap: "wrap", gap: 20, paddingTop: 20, borderTop: "1px solid var(--line)" }}>
          <div style={{ flex: "1 1 200px", maxWidth: 260, display: "flex", flexDirection: "column", gap: 6 }}>
            <span className="mono" style={{ fontSize: 13, fontWeight: 600, color: "var(--cobalt, #2846E0)" }}>{l.key}</span>
            <h2 className="disp" style={{ margin: 0, fontSize: 24 }}>{l.title}</h2>
            <span style={{ fontSize: 14, lineHeight: 1.5, color: "var(--ink-2)" }}>{l.about}</span>
          </div>
          <div style={{ flex: "999 1 520px", minWidth: 0, display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(min(220px, 100%), 1fr))", gap: 12 }}>
            {l.modules.map((m) => <ModuleCard key={m.name} m={m} on={switchedOn(m, track)} />)}
          </div>
        </section>
      ))}

      <section aria-label="Government and financial rails" style={{ background: "#111318", color: "#ECEDEF", borderRadius: 18, padding: 24, display: "flex", flexWrap: "wrap", gap: 24 }}>
        <div style={{ flex: "1 1 220px", maxWidth: 300, display: "flex", flexDirection: "column", gap: 6 }}>
          <span className="mono" style={{ fontSize: 13, fontWeight: 600, color: "#8EA2FF" }}>L0 · CONNECT</span>
          <h2 className="disp" style={{ margin: 0, fontSize: 24 }}>Government &amp; financial rails</h2>
          <span style={{ fontSize: 14, lineHeight: 1.5, color: "#B7BCC5" }}>
            The systems the engine reads from and files to. {rails.sandbox} run against a stand-in that behaves like the portal; nothing has been tried against a live one yet, and {rails.planned} are still planned.
          </span>
        </div>
        <ul style={{ flex: "999 1 520px", minWidth: 0, margin: 0, padding: 0, listStyle: "none", display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(min(200px, 100%), 1fr))", gap: 10 }}>
          {RAILS.map((r) => (
            <li key={r.name} style={{ border: "1px solid #2A2E38", borderRadius: 12, padding: "12px 14px", display: "flex", flexDirection: "column", gap: 2 }}>
              <span style={{ display: "flex", justifyContent: "space-between", gap: 8, alignItems: "center" }}>
                <span style={{ fontSize: 14, fontWeight: 600, color: "#fff" }}>{r.name}</span>
                <span className="mono" style={{ fontSize: 10, padding: "2px 6px", borderRadius: 6, background: r.state === "sandbox" ? "#262A33" : "transparent", border: r.state === "sandbox" ? 0 : "1px dashed #4A4F5A", color: "#C7CBD2" }}>{r.state === "sandbox" ? "SANDBOX" : "PLANNED"}</span>
              </span>
              <span style={{ fontSize: 12, color: "#9CA3AF" }}>{r.about}</span>
            </li>
          ))}
        </ul>
      </section>
    </main>
  );
}

function ModuleCard({ m, on }: { m: Module; on: boolean }) {
  const body = (
    <>
      <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
        <span style={{ width: 36, height: 36, flex: "none", borderRadius: 9, display: "flex", alignItems: "center", justifyContent: "center", background: on ? "var(--paper, #F4F3EE)" : "transparent", color: on ? "var(--ink)" : "#8A8F98" }}><Icon d={m.icon} /></span>
        <span style={{ fontSize: 15, fontWeight: 600, lineHeight: 1.25 }}>{m.name}</span>
      </div>
      <span style={{ fontSize: 13, lineHeight: 1.45, color: on ? "var(--ink-2)" : "#6B7079" }}>{m.about}</span>
      <div style={{ display: "flex", gap: 6, flexWrap: "wrap", marginTop: "auto", alignItems: "center" }}>
        {m.tracks.map((t) => <span key={t} className="mono" style={{ fontSize: 11, padding: "2px 7px", borderRadius: 6, background: on ? "#E8ECFF" : "#ECEBE5", color: on ? "#1A31A8" : "#5A5F68" }}>{t}</span>)}
        <span className="mono" style={{ fontSize: 11, marginLeft: "auto", color: m.href ? "var(--cobalt, #2846E0)" : "var(--muted, #6B7079)" }}>{m.href ? "Open →" : "Planned"}</span>
      </div>
    </>
  );
  const style = { borderRadius: 14, padding: 16, display: "flex", flexDirection: "column", gap: 10, background: on ? "#fff" : "transparent", border: on ? "1px solid var(--line)" : "1px dashed #CFCEC6", color: on ? "var(--ink)" : "#6B7079", textDecoration: "none" } as const;
  return m.href
    ? <Link href={m.href} className="mcard" style={style} aria-label={`${m.name}${on ? "" : " (not part of this track)"}`}>{body}</Link>
    : <div className="mcard" style={style} aria-label={`${m.name}, planned${on ? "" : " (not part of this track)"}`}>{body}</div>;
}
