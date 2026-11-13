// Who sees what: the sidebar a person gets, the home they land on, and what the invite form sends. The server decides every permission;
// this only hides what a person could not open anyway and stops useless calls.
import type { NavGroup, NavItem } from "@/components/nav";

export type Level = "read" | "comment" | "write" | "approve" | "file";
export type Home = "leadership" | "finance" | "people" | "general" | "practitioner";
export interface Access { role?: string; modules?: string[]; scopes?: Record<string, string>; title?: string | null; home?: string }
export interface Preset { key: string; kind: "member" | "practitioner"; title: string; about: string; owner: boolean; home: string | null; scopes: Record<string, string> }
export interface Presets { presets: Preset[]; levels: string[]; modules: Array<{ key: string; label: string }> }

export const LEVELS: Level[] = ["read", "comment", "write", "approve", "file"];
export const LEVEL_LABEL: Record<string, string> = { "": "No access", read: "View", comment: "View and query", write: "Prepare", approve: "Approve", file: "Approve, file and pay" };
const RANK: Record<string, number> = { read: 1, comment: 2, write: 3, approve: 4, file: 5, prepare: 3, certify: 5 };       // a practitioner's grant uses prepare and certify

/** Whether the person can open a module at all. `*` in the list means every module. */
export function canOpen(a: Access | null | undefined, module: string): boolean {
  const mods = a?.modules ?? [];
  return mods.includes("*") || mods.includes(module);
}

/** The person's level on a module, as a rank: 0 none, 1 view … 5 file. */
export function levelOf(a: Access | null | undefined, module: string): number {
  const s = a?.scopes ?? {};
  return RANK[s[module] ?? s["*"] ?? ""] ?? 0;
}

export const canWrite = (a: Access | null | undefined, module: string) => levelOf(a, module) >= 3;

/** The sidebar for a person: items whose module they cannot open are left out, and so is a group that ends up empty. */
export function navFor(nav: NavGroup[], a: Access | null | undefined, inFirm: boolean): NavGroup[] {
  const shown = (i: NavItem) => {
    if (i.firmOnly && !inFirm) return false;
    if (i.unless && i.unless.some((m) => canOpen(a, m))) return false;
    return !i.modules || i.modules.some((m) => canOpen(a, m));
  };
  return nav.map((g) => ({ ...g, items: g.items.filter(shown) })).filter((g) => g.items.length > 0);
}

const ROLE_TEXT: Record<string, string> = { owner: "Owner", approver: "Approver", maker: "Preparer", viewer: "Viewer", advisor: "Advisor", org_staff: "Firm staff" };
/** What to call the person under their name: the title the company gave them, else their level. */
export const roleText = (a: Access | null | undefined) => a?.title || ROLE_TEXT[a?.role ?? ""] || "—";

export const homeOf = (a: Access | null | undefined): Home => (["leadership", "finance", "people", "general", "practitioner"].includes(a?.home ?? "") ? (a!.home as Home) : "general");

export type Tile = "cash" | "runway" | "receivables" | "net_burn" | "stock" | "people" | "onboarding" | "leave_waiting" | "payroll_run";
const TILES: Record<Home, Array<[Tile, string]>> = {
  leadership: [["cash", "finance"], ["runway", "finance"], ["receivables", "finance"], ["people", "hr"]],
  finance: [["cash", "finance"], ["receivables", "finance"], ["net_burn", "finance"], ["stock", "inventory"]],
  people: [["onboarding", "hr"], ["leave_waiting", "hr"], ["people", "hr"], ["payroll_run", "payroll"]],
  general: [],
  practitioner: [],
};
/** The numbers a home shows above the shared sections, less the ones the person has no access to. */
export const homeTiles = (a: Access | null | undefined): Tile[] => TILES[homeOf(a)].filter(([, m]) => canOpen(a, m)).map(([t]) => t);

export const HOME_LABEL: Record<string, string> = { leadership: "Leadership", finance: "Finance", people: "People", general: "General" };

// ------------------------------------------------------------------ inviting a person, or changing what they may do
export interface AccessForm { email: string; name: string; preset: string; title: string; owner: boolean; scopes: Record<string, string> }
export const blankAccess = (): AccessForm => ({ email: "", name: "", preset: "", title: "", owner: false, scopes: {} });

/** Start the form from a preset: its scopes spelled out per module, so each line can be changed. */
export function applyPreset(f: AccessForm, p: Preset | undefined, modules: Array<{ key: string }>): AccessForm {
  if (!p) return { ...f, preset: "", owner: false };
  const star = p.scopes["*"];
  const scopes: Record<string, string> = {};
  for (const m of modules) { const l = p.scopes[m.key] ?? star; if (l) scopes[m.key] = l; }
  return { ...f, preset: p.key, title: p.title, owner: p.owner, scopes };
}

export const setLevel = (f: AccessForm, module: string, level: string): AccessForm => {
  const scopes = { ...f.scopes };
  if (level) scopes[module] = level; else delete scopes[module];
  return { ...f, scopes };
};

const EMAIL = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
export function accessProblem(f: AccessForm, inviting: boolean): string | null {
  if (inviting && !EMAIL.test(f.email.trim())) return "Enter the person's work email";
  if (!f.owner && Object.keys(f.scopes).length === 0) return "Choose a role, or give at least one module";
  if (f.title.length > 80) return "Keep the title under 80 characters";
  return null;
}

/** What is sent. An owner needs no scopes; a preset is named only while its scopes are untouched, so the record says what really happened. */
export function accessBody(f: AccessForm, inviting: boolean, preset?: Preset, modules: Array<{ key: string }> = []) {
  const untouched = !!preset && JSON.stringify(applyPreset(f, preset, modules).scopes) === JSON.stringify(f.scopes) && preset.owner === f.owner;
  const body: Record<string, unknown> = { owner: f.owner, ...(f.owner ? {} : { scopes: f.scopes }), ...(f.title.trim() ? { title: f.title.trim() } : {}) };
  if (untouched) body.preset = preset!.key;
  if (inviting) { body.email = f.email.trim(); if (f.name.trim()) body.full_name = f.name.trim(); }
  return body;
}

/** "Finance: Approve, file and pay · Payroll: Approve · HR: View" for the people table; "Everything" for an owner. */
export function scopesText(scopes: Record<string, string> | undefined, labels: Array<{ key: string; label: string }> = []): string {
  const s = scopes ?? {};
  const keys = Object.keys(s);
  if (keys.length === 0) return "—";
  if (keys.length === 1 && s["*"]) return s["*"] === "file" ? "Everything" : `Everything: ${LEVEL_LABEL[s["*"]] ?? s["*"]}`;
  const name = (k: string) => (k === "*" ? "Everything else" : labels.find((l) => l.key === k)?.label ?? k);
  return keys.sort((a, b) => (RANK[s[b]!] ?? 0) - (RANK[s[a]!] ?? 0) || a.localeCompare(b)).map((k) => `${name(k)}: ${LEVEL_LABEL[s[k]!] ?? s[k]}`).join(" · ");
}
