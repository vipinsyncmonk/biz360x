"use client";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { initials } from "@/lib/format";
import { useSession } from "@/lib/session";
import { NAV, TABS } from "@/components/nav";
import { Icon } from "@/components/ui";

const TRACK: Record<string, string> = { business: "Business", startup: "Startup", msme: "MSME" };
const IN_TABS = new Set(TABS.map((t) => t.href));

/** The phone's "More" tab: switch entity, reach every module, account. */
export default function More() {
  const { me, entity, setEntityId, logout } = useSession();
  const router = useRouter();
  const entities = me?.entities ?? [];
  const groups = NAV.map((g) => ({ ...g, items: g.items.filter((i) => !IN_TABS.has(i.href)) })).filter((g) => g.items.length > 0);

  return (
    <main className="page">
      <h1 className="disp" style={{ margin: 0, fontSize: 32 }}>More</h1>

      <section aria-label="Your entities">
        <span className="t13 mono">YOUR ENTITIES</span>
        <div className="card mlist" style={{ marginTop: 8, overflow: "hidden" }}>
          {entities.filter((e) => e.id).map((e) => (
            <button key={e.id} type="button" className="row" aria-pressed={entity?.id === e.id} onClick={() => setEntityId(e.id!)}>
              <span className="badge mono" aria-hidden="true" style={{ width: 36, height: 36, borderRadius: 9, background: entity?.id === e.id ? "var(--cobalt)" : "var(--prep-soft)", color: entity?.id === e.id ? "#fff" : "var(--ink)", display: "flex", alignItems: "center", justifyContent: "center", fontSize: 13, fontWeight: 600 }}>{initials(e.legal_name ?? "?").slice(0, 2)}</span>
              <span style={{ flex: 1, minWidth: 0 }}>{e.legal_name}<span className="t13" style={{ display: "block" }}>{TRACK[e.track ?? ""] ?? e.track} · {e.role}</span></span>
              {entity?.id === e.id && <span className="t13" style={{ color: "var(--cobalt)" }}>Current</span>}
            </button>
          ))}
        </div>
      </section>

      {groups.map((g) => (
        <section key={g.h} aria-label={g.h}>
          <span className="t13 mono">{g.h}</span>
          <div className="card mlist" style={{ marginTop: 8, overflow: "hidden" }}>
            {g.items.map((n) => (
              <Link key={n.href} href={n.href}><Icon d={n.icon} /><span style={{ flex: 1 }}>{n.t}</span>{!n.built && <span className="t13">Soon</span>}</Link>
            ))}
          </div>
        </section>
      ))}

      <section aria-label="Account">
        <span className="t13 mono">ACCOUNT</span>
        <div className="card mlist" style={{ marginTop: 8, overflow: "hidden" }}>
          <div style={{ padding: "12px 16px", fontSize: 14 }}>{me?.full_name}<span className="t13" style={{ display: "block" }}>{me?.email}</span></div>
          <button type="button" className="row" onClick={() => void logout().then(() => router.replace("/login"))}>Sign out</button>
        </div>
      </section>
    </main>
  );
}
