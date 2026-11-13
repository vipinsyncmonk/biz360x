"use client";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, type ReactNode } from "react";
import { useSession } from "@/lib/session";
import { initials } from "@/lib/format";
import { navFor, roleText } from "@/lib/roles";
import { NAV, TABS, activeTab } from "./nav";
import { Icon, Logo } from "./ui";

export default function Shell({ children }: { children: ReactNode }) {
  const { status, me, entity, setEntityId, logout } = useSession();
  const path = usePathname();
  const router = useRouter();

  useEffect(() => { if (status === "anon") router.replace("/login"); }, [status, router]);
  const noWorkspace = status === "authed" && (me?.entities ?? []).length === 0;
  useEffect(() => { if (noWorkspace) router.replace("/onboarding"); }, [noWorkspace, router]);

  if (status !== "authed" || !me) return <div className="empty" role="status">Loading…</div>;

  const entities = me.entities ?? [];
  const name = me.full_name ?? me.email ?? "";
  const isCurrent = (href: string) => (href === "/" ? path === "/" : path === href || path.startsWith(href + "/"));

  return (
    <div className="shell">
      <aside className="side">
        <Link href="/" className="brand"><Logo /><b className="disp">Biz360X</b></Link>
        <div className="switcher">
          <span className="badge mono" aria-hidden="true">{initials(entity?.legal_name ?? "?").slice(0, 2)}</span>
          <label className="sr" htmlFor="entity">Entity</label>
          {entities.length ? (
            <select id="entity" value={entity?.id ?? ""} onChange={(e) => setEntityId(e.target.value)}>
              {entities.map((e) => <option key={e.id} value={e.id}>{e.legal_name}</option>)}
            </select>
          ) : <span style={{ fontSize: 14 }}>No workspace yet</span>}
        </div>
        <nav aria-label="Workspace" style={{ display: "flex", flexDirection: "column", gap: 18 }}>
          {navFor(NAV, entity, (me.organizations ?? []).length > 0).map((g) => (
            <div className="navgroup" key={g.h}>
              <span className="navhead mono">{g.h}</span>
              {g.items.map((n) => (
                <Link key={n.href} href={n.href} className="nav" aria-current={isCurrent(n.href) ? "page" : undefined}>
                  <Icon d={n.icon} />
                  <span style={{ flex: 1 }}>{n.t}</span>
                  {!n.built && <span className="soon mono">soon</span>}
                </Link>
              ))}
            </div>
          ))}
        </nav>
        <div className="who">
          <span className="avatar" aria-hidden="true">{initials(name)}</span>
          <span style={{ display: "flex", flexDirection: "column", minWidth: 0 }}>
            <span style={{ fontSize: 14, overflow: "hidden", textOverflow: "ellipsis" }}>{name}</span>
            <span style={{ fontSize: 12, color: "#9CA3AF" }}>{roleText(entity)}</span>
            <button type="button" className="linkbtn" onClick={() => void logout().then(() => router.replace("/login"))}>Sign out</button>
          </span>
        </div>
      </aside>
      <div className="main">
        <header className="mtop">
          <Link href="/" aria-label="Biz360X home"><Logo size={22} /></Link>
          <label className="sr" htmlFor="m-entity">Entity</label>
          <select id="m-entity" value={entity?.id ?? ""} onChange={(e) => setEntityId(e.target.value)}>
            {entities.map((e) => <option key={e.id} value={e.id}>{e.legal_name}</option>)}
          </select>
        </header>
        {children}
      </div>
      <nav className="tabbar" aria-label="Main">
        {TABS.map((t) => (
          <Link key={t.key} href={t.href} aria-current={activeTab(path) === t.key ? "page" : undefined}>
            <Icon d={t.icon} size={22} />{t.t}
          </Link>
        ))}
      </nav>
    </div>
  );
}
