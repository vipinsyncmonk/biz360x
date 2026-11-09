/** Navigation from the design. `built` marks the screens that exist; the rest open a placeholder so the whole map is visible. */
export interface NavItem { t: string; href: string; icon: string; built: boolean }
export interface NavGroup { h: string; items: NavItem[] }

const I = {
  today: "M3 12l9-8 9 8M5 10v10h14V10",
  shield: "M12 3l8 3v6c0 4.5-3.4 8.3-8 9-4.6-.7-8-4.5-8-9V6zM9 12l2 2 4-4",
  spark: "M12 3l1.9 5.6L19.5 10l-5.6 1.9L12 17.5l-1.9-5.6L4.5 10l5.6-1.4z",
  folder: "M3 7h6l2 2h10v11H3z",
  bill: "M6 3h12v18l-3-2-3 2-3-2-3 2zM9 8h6M9 12h6M9 16h3",
  book: "M4 4h16v16H4zM4 10h16M10 4v16",
  gst: "M7 3h10l2 4H5zM5 7v14h14V7M9 13h6M9 17h4",
  people: "M16 11a4 4 0 1 0-8 0M4 21a8 8 0 0 1 16 0",
  legal: "M12 3v18M5 7h14M7 7l-3 7a3 3 0 0 0 6 0zM17 7l-3 7a3 3 0 0 0 6 0z",
  rocket: "M5 15c-1 1-2 5-2 5s4-1 5-2M9 15l-3-3c1-4 5-9 12-9 0 7-5 11-9 12zM14 10h.01",
  factory: "M3 21V10l6 4V10l6 4V6h6v15z",
  grid: "M4 4h7v7H4zM13 4h7v7h-7zM4 13h7v7H4zM13 13h7v7h-7z",
  group: "M3 21V9l6-4 6 4v12M15 21V13h6v8M7 13h4M7 17h4",
  cog: "M12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6zM19 12a7 7 0 0 0-.1-1.2l2-1.6-2-3.4-2.4 1a7 7 0 0 0-2-1.2L14 3h-4l-.5 2.6a7 7 0 0 0-2 1.2l-2.4-1-2 3.4 2 1.6A7 7 0 0 0 5 12c0 .4 0 .8.1 1.2l-2 1.6 2 3.4 2.4-1c.6.5 1.3.9 2 1.2L10 21h4l.5-2.6c.7-.3 1.4-.7 2-1.2l2.4 1 2-3.4-2-1.6c.1-.4.1-.8.1-1.2z",
};

const soon = (t: string, slug: string, icon: string): NavItem => ({ t, href: `/m/${slug}`, icon, built: false });

export const NAV: NavGroup[] = [
  { h: "WORKSPACE", items: [
    { t: "Today", href: "/", icon: I.today, built: true },
    { t: "Compliance", href: "/compliance", icon: I.shield, built: true },
    { t: "Notice desk", href: "/notices", icon: I.legal, built: true },
    { t: "Copilot", href: "/copilot", icon: I.spark, built: true },
    { t: "Documents", href: "/documents", icon: I.folder, built: true },
  ] },
  { h: "RUN", items: [
    { t: "Sales & Billing", href: "/billing", icon: I.bill, built: true },
    { t: "Finance & Books", href: "/finance", icon: I.book, built: true }, { t: "GST & Tax", href: "/gst", icon: I.gst, built: true }, { t: "TDS & Advance tax", href: "/tds", icon: I.gst, built: true },
    { t: "Payroll & HR", href: "/payroll", icon: I.people, built: true }, { t: "Legal & Contracts", href: "/legal", icon: I.legal, built: true }, { t: "Board & resolutions", href: "/board", icon: I.book, built: true },
  ] },
  { h: "TRACKS", items: [{ t: "Startup", href: "/startup", icon: I.rocket, built: true }, { t: "MSME", href: "/msme", icon: I.factory, built: true }, soon("All modules", "modules", I.grid)] },
  { h: "ADMIN", items: [{ t: "Group view", href: "/group", icon: I.group, built: true }, { t: "Settings", href: "/settings", icon: I.cog, built: true }] },
];

export const SOON_TITLES: Record<string, string> = Object.fromEntries(
  NAV.flatMap((g) => g.items).filter((i) => !i.built).map((i) => [i.href.slice(3), i.t]),
);

/** The four tabs of the phone layout, as in the mobile designs. Anything else lives under More. */
export const TABS: Array<{ key: string; t: string; href: string; icon: string }> = [
  { key: "today", t: "Today", href: "/", icon: I.today },
  { key: "compliance", t: "Compliance", href: "/compliance", icon: I.shield },
  { key: "copilot", t: "Copilot", href: "/copilot", icon: I.spark },
  { key: "more", t: "More", href: "/more", icon: I.grid },
];

export function activeTab(path: string): string {
  if (path === "/") return "today";
  if (path === "/compliance" || path.startsWith("/compliance/")) return "compliance";
  if (path === "/copilot" || path.startsWith("/copilot/")) return "copilot";
  return "more";
}
