/** Pure helpers for the Settings and Group screens. Visibility and cell states are decided by the server. */
import type { Tone } from "./compliance";
import { daysUntil } from "./legal";

export interface Member { id: string; user_id: string; email: string; full_name: string; role: string; modules: string[]; accepted_at: string | null; auth: string }
export interface Grant { id: string; advisor_name: string; advisor_kind: string; scopes: Record<string, string>; expires_on: string | null; status: string }
export interface Dsc { id: string; person_name: string; dsc_class: string; valid_from: string; valid_until: string; registered_on: string[] }
export interface PortalConn { id: string; portal: string; via: string; status: string; session_expires_at: string | null; last_sync_at: string | null; last_error: string | null }
export interface Pref { entity_id: string; event_type: string; channel: string; enabled: boolean }
export interface Standing { entity_id: string; legal_name: string; constitution: string; owner_staff: string | null; cells: Record<string, string>; open_items: Array<{ title: string; detail: string; due_on: string }> }
export interface StaffLoad { user_id: string; name: string; filings_due: number }

export const ROLE_LABEL: Record<string, string> = { owner: "Owner", approver: "Approver", maker: "Preparer", viewer: "Viewer" };
export const KIND_LABEL: Record<string, string> = { chartered_accountant: "Chartered accountant", company_secretary: "Company secretary", counsel: "Counsel", lender: "Lender", investor: "Investor", auditor: "Auditor" };
export const SCOPE_LABEL: Record<string, string> = { read: "View", comment: "Comment", prepare: "Prepare", certify: "Certify" };
export const GRANT_STATUS: Record<string, { label: string; tone: Tone }> = { invited: { label: "Invited", tone: "wait" }, active: { label: "Active", tone: "ok" }, revoked: { label: "Revoked", tone: "prep" }, expired: { label: "Expired", tone: "due" } };

/** "*" means every module. */
export const modulesLabel = (m: string[]) => (m.includes("*") ? "All modules" : m.join(", "));

export const EVENTS: Array<[string, string]> = [
  ["due_in_7_days", "Due in 7 days"], ["due_tomorrow", "Due tomorrow"], ["approval_requested", "Approval requested"], ["notice_received", "Notice received"],
  ["threshold_crossed", "Threshold crossed"], ["copilot_suggestion", "Copilot suggestion"],
];
export const CHANNELS: Array<[string, string]> = [["email", "Email"], ["whatsapp", "WhatsApp"], ["push", "Push"], ["sms", "SMS"]];

export const prefKey = (event: string, channel: string) => `${event}|${channel}`;

/** What is switched on, from the saved rows. A choice never saved counts as off. */
export function prefState(rows: Pref[], entityId: string): Record<string, boolean> {
  const out: Record<string, boolean> = {};
  for (const r of rows) if (r.entity_id === entityId) out[prefKey(r.event_type, r.channel)] = r.enabled;
  return out;
}

/** The full matrix for one entity, ready to PUT. Every cell is sent, so what the person sees is what is stored. */
export function prefBody(entityId: string, state: Record<string, boolean>): Pref[] {
  return EVENTS.flatMap(([e]) => CHANNELS.map(([c]) => ({ entity_id: entityId, event_type: e, channel: c, enabled: !!state[prefKey(e, c)] })));
}

/** How a certificate is doing: expired, expiring within 60 days, or fine. */
export function dscStatus(validUntil: string, today: Date = new Date()): { label: string; tone: Tone } {
  const n = daysUntil(validUntil, today);
  if (n < 0) return { label: "Expired", tone: "due" };
  if (n <= 60) return { label: `Expires in ${n} ${n === 1 ? "day" : "days"}`, tone: "due" };
  return { label: "Valid", tone: "ok" };
}

export const PORTAL_LABEL: Record<string, string> = {
  gstn: "GST portal", mca: "MCA", income_tax: "Income Tax", traces: "TRACES", epfo: "EPFO", esic: "ESIC", irp: "e-invoice portal", eway: "E-way bill portal", professional_tax: "Professional tax",
  udyam: "Udyam", account_aggregator: "Account aggregator", bank_api: "Bank", treds: "TReDS", digilocker: "DigiLocker",
};
export const CONN_STATUS: Record<string, { label: string; tone: Tone }> = {
  connected: { label: "Connected", tone: "ok" }, expiring: { label: "Expires within a day", tone: "due" }, expired: { label: "Session expired", tone: "due" }, error: { label: "Error", tone: "due" },
  revoked: { label: "Revoked", tone: "prep" }, not_connected: { label: "Not connected", tone: "prep" },
};

// ---- group
export const CELL_COLUMNS: Array<[string, string]> = [["MCA", "MCA"], ["GST", "GST"], ["TDS", "TDS"], ["EPFO_ESIC", "EPFO / ESIC"], ["INCOME_TAX", "Income tax"]];
export const CELL_LABEL: Record<string, { label: string; tone: Tone | "none" }> = {
  clear: { label: "Clear", tone: "ok" }, due: { label: "Due", tone: "wait" }, client: { label: "Client", tone: "due" }, overdue: { label: "Overdue", tone: "due" }, na: { label: "–", tone: "none" },
};

export function groupCounts(rows: Standing[]): { clients: number; waiting: number; overdue: number } {
  const has = (r: Standing, s: string) => Object.values(r.cells).includes(s);
  return { clients: rows.length, waiting: rows.filter((r) => has(r, "client")).length, overdue: rows.filter((r) => has(r, "overdue")).length };
}

/** Anything overdue first, then waiting on the client, then by name. */
export function byTrouble(rows: Standing[]): Standing[] {
  const rank = (r: Standing) => (Object.values(r.cells).includes("overdue") ? 0 : Object.values(r.cells).includes("client") ? 1 : 2);
  return [...rows].sort((a, b) => rank(a) - rank(b) || a.legal_name.localeCompare(b.legal_name));
}

// ---- write actions: invite, advisors, certificates, API keys. The server decides who may do what; these only stop useless calls.
export const MODULES: Array<[string, string]> = [
  ["sales", "Sales & billing"], ["purchases", "Purchases"], ["finance", "Finance & books"], ["gst", "GST"], ["tds", "TDS"],
  ["payroll", "Payroll & HR"], ["legal", "Legal & contracts"], ["documents", "Documents"], ["compliance", "Compliance"],
];
export const ROLES: Array<[string, string, string]> = [
  ["owner", "Owner", "Everything, including people and settings"],
  ["approver", "Approver", "Prepare, approve, file and pay"],
  ["maker", "Preparer", "Prepare work; cannot approve"],
  ["viewer", "Viewer", "Read only"],
];
export const emailOk = (v: string) => /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(v.trim());

export interface InviteForm { email: string; name: string; role: string; modules: string[] }
export const blankInvite = (): InviteForm => ({ email: "", name: "", role: "maker", modules: [] });
export const inviteReady = (f: InviteForm) => emailOk(f.email) && ROLES.some(([r]) => r === f.role);
/** No module ticked means every module. */
export const inviteBody = (f: InviteForm) => ({ email: f.email.trim(), role: f.role, modules: f.modules.length ? f.modules : ["*"], ...(f.name.trim() ? { full_name: f.name.trim() } : {}) });

export const LEVELS: Array<[string, string]> = [["read", "View"], ["comment", "Comment"], ["prepare", "Prepare"], ["certify", "Certify"]];
export interface GrantForm { name: string; kind: string; email: string; levels: Record<string, string>; expires: string }
export const blankGrant = (): GrantForm => ({ name: "", kind: "chartered_accountant", email: "", levels: {}, expires: "" });
export const scopesBody = (levels: Record<string, string>) => Object.fromEntries(Object.entries(levels).filter(([, v]) => v));
export function grantReady(f: GrantForm, today: string): boolean {
  return f.name.trim().length > 0 && Object.keys(scopesBody(f.levels)).length > 0 && (f.email.trim() === "" || emailOk(f.email)) && (f.expires === "" || f.expires > today);
}
export const grantBody = (f: GrantForm) => ({
  advisor_name: f.name.trim(), advisor_kind: f.kind, scopes: scopesBody(f.levels),
  ...(f.email.trim() ? { invite_email: f.email.trim() } : {}), ...(f.expires ? { expires_on: f.expires } : {}),
});

export const PORTALS_FOR_DSC: Array<[string, string]> = [["MCA", "MCA"], ["GSTN", "GST portal"], ["ITD", "Income tax"]];
export interface DscForm { person: string; cls: string; from: string; until: string; on: string[]; serial: string }
export const blankDsc = (): DscForm => ({ person: "", cls: "class3", from: "", until: "", on: [], serial: "" });
export const dscReady = (f: DscForm) => f.person.trim().length > 0 && f.serial.trim().length >= 4 && !!f.from && !!f.until && f.until > f.from;
export const dscBody = (f: DscForm) => ({ person_name: f.person.trim(), dsc_class: f.cls, valid_from: f.from, valid_until: f.until, registered_on: f.on, serial: f.serial.trim() });

export interface ApiKeyRow { id: string; name: string; prefix: string; permissions: string[]; last_used_at: string | null; expires_at: string | null; revoked_at: string | null; created_at: string }
/** "read" gives module:read; "write" gives module:read and module:write. Approving, filing and paying are never available to a key. */
export function keyPermissions(access: Record<string, string>): string[] {
  const out: string[] = [];
  for (const [m, a] of Object.entries(access)) {
    if (a === "read" || a === "write") out.push(`${m}:read`);
    if (a === "write") out.push(`${m}:write`);
  }
  return out.sort();
}
export const keyReady = (name: string, access: Record<string, string>) => name.trim().length > 0 && keyPermissions(access).length > 0;
export const permissionLabel = (p: string[]) => {
  const mods = [...new Set(p.map((x) => x.split(":")[0]!))];
  return mods.map((m) => `${(MODULES.find(([k]) => k === m)?.[1] ?? m)} ${p.includes(`${m}:write`) ? "read/write" : "read"}`).join(", ");
};
