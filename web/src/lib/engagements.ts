// Practitioner engagements: what the company's form sends, what an invited practitioner is shown, and a firm's staff per client.
// The server decides every rule (owner only, audit is read only, independence); this words them and stops useless calls.
import type { Tone } from "./compliance";
import type { Preset } from "./roles";

export const SERVICES: Array<[string, string]> = [
  ["bookkeeping", "Bookkeeping and accounts"], ["gst", "GST"], ["tds", "TDS"], ["pf_esi", "PF and ESI"], ["payroll", "Payroll"], ["secretarial", "Company secretarial"],
  ["trade", "Exports and imports"], ["statutory_audit", "Statutory audit"], ["tax_audit", "Tax audit"], ["internal_audit", "Internal audit"], ["virtual_cfo", "Virtual CFO"], ["legal", "Legal"],
];
export const AUDIT_SERVICES = ["statutory_audit", "tax_audit", "internal_audit"];
export const serviceLabel = (s: string | null | undefined) => SERVICES.find(([k]) => k === s)?.[1] ?? null;
export const isAudit = (service: string) => AUDIT_SERVICES.includes(service);

export const GRANT_LEVELS: Array<[string, string]> = [["read", "View"], ["comment", "View and query"], ["prepare", "Prepare"], ["certify", "Prepare and file"]];
/** The modules a practitioner can be given. Settings is never one of them. */
export const GRANT_MODULES: Array<[string, string]> = [
  ["finance", "Finance and books"], ["gst", "GST"], ["tds", "TDS"], ["sales", "Sales and billing"], ["purchases", "Purchases"], ["parties", "Customers, vendors and items"],
  ["inventory", "Inventory"], ["payroll", "Payroll and salaries"], ["hr", "HR"], ["compliance", "Compliance"], ["trade", "Exports and imports"], ["legal", "Legal, contracts and board"],
  ["capital", "Cap table and funding"], ["documents", "Document vault"], ["audit", "Audit log"],
];

export interface Engagement {
  id: string; advisor_name: string; advisor_kind: string; service: string | null; scopes: Record<string, string>; expires_on: string | null; period_fy: string | null;
  invited_email: string | null; party: "person" | "firm" | "record"; audit: boolean; status: string;
}
export const ENGAGEMENT_STATUS: Record<string, { label: string; tone: Tone }> = {
  invited: { label: "Invited", tone: "wait" }, active: { label: "Active", tone: "ok" }, declined: { label: "Declined", tone: "due" }, revoked: { label: "Ended", tone: "prep" },
  expired: { label: "Expired", tone: "due" },
};
const PARTY: Record<Engagement["party"], string> = { person: "as a person", firm: "through their firm", record: "not invited; a record only" };
const levelLabel = (v: string) => GRANT_LEVELS.find(([k]) => k === v)?.[1] ?? v;
const moduleLabel = (k: string) => GRANT_MODULES.find(([m]) => m === k)?.[1] ?? k.replace(/_/g, " ");

/** "GST · as a person · GST: Prepare and file, Sales and billing: View · FY 2026-27 · until 31 Mar" under the practitioner's name. */
export function engagementLine(g: Engagement, shortDate: (iso: string) => string): string {
  const scopes = Object.entries(g.scopes).sort(([a], [b]) => a.localeCompare(b)).map(([k, v]) => `${moduleLabel(k)}: ${levelLabel(v)}`).join(", ");
  return [serviceLabel(g.service), g.status === "invited" && g.invited_email ? `invited at ${g.invited_email}` : PARTY[g.party], scopes, g.period_fy ? `FY ${g.period_fy}` : null,
    g.expires_on ? `until ${shortDate(g.expires_on)}` : null].filter(Boolean).join(" · ");
}

// ------------------------------------------------------------------ the company engages a practitioner
export interface EngageForm { name: string; email: string; preset: string; service: string; expires: string; fy: string; membershipNo: string; levels: Record<string, string> }
export const blankEngage = (): EngageForm => ({ name: "", email: "", preset: "", service: "", expires: "", fy: "", membershipNo: "", levels: {} });

/** Start from a practitioner preset: its service and its scopes, each line still changeable. */
export function applyEngagePreset(f: EngageForm, p: (Preset & { service?: string | null }) | undefined): EngageForm {
  if (!p) return { ...f, preset: "" };
  return { ...f, preset: p.key, service: p.service ?? f.service, levels: { ...p.scopes } };
}

const EMAIL = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
export function engageProblem(f: EngageForm, today: string): string | null {
  const levels = Object.entries(f.levels).filter(([, v]) => v);
  if (!f.name.trim()) return "Enter the practitioner's or the firm's name";
  if (f.email.trim() && !EMAIL.test(f.email.trim())) return "That email address does not look right";
  if (!f.service) return "Choose what they are engaged for";
  if (levels.length === 0) return "Give them at least one module";
  if (isAudit(f.service)) {
    if (levels.some(([, v]) => v !== "read" && v !== "comment")) return "An auditor can only view and raise queries";
    if (!f.expires) return "An audit engagement needs an end date";
  }
  if (f.expires && f.expires <= today) return "The end date must be after today";
  if (f.fy.trim() && !/^\d{4}-\d{2}$/.test(f.fy.trim())) return "Write the financial year as 2026-27";
  if (f.membershipNo.trim() && (f.membershipNo.trim().length < 3 || f.membershipNo.trim().length > 20)) return "The membership number is 3 to 20 characters";
  return null;
}

const KIND_OF_SERVICE: Record<string, string> = { secretarial: "company_secretary", legal: "counsel", statutory_audit: "auditor", tax_audit: "auditor", internal_audit: "auditor" };

/** What is sent. The preset is named, and the scopes left to it, only while its lines are untouched: the server then also gives the
 *  practitioner the work that preset usually prepares. Once a line is changed the scopes are sent as they stand. */
export interface EngageBody { advisor_name: string; advisor_kind: string; service: string; preset?: string; scopes?: Record<string, string>; invite_email?: string;
  expires_on: string | null; period_fy: string | null; membership_no: string | null }
export function engageBody(f: EngageForm, preset?: Pick<Preset, "key" | "scopes">): EngageBody {
  const t = (s: string) => (s.trim() ? s.trim() : null);
  const scopes = Object.fromEntries(Object.entries(f.levels).filter(([, v]) => v));
  const same = (a: Record<string, string>, b: Record<string, string>) => JSON.stringify(Object.entries(a).sort()) === JSON.stringify(Object.entries(b).sort());
  const untouched = !!preset && preset.key === f.preset && same(scopes, preset.scopes);
  return {
    advisor_name: f.name.trim(), advisor_kind: KIND_OF_SERVICE[f.service] ?? "chartered_accountant", service: f.service,
    ...(untouched ? { preset: preset!.key } : { scopes }),
    ...(t(f.email) ? { invite_email: f.email.trim() } : {}), expires_on: t(f.expires), period_fy: t(f.fy), membership_no: t(f.membershipNo),
  };
}

// ------------------------------------------------------------------ the practitioner answers
export interface Invite {
  id: string; entity_id: string; entity_name: string; advisor_name: string; service: string | null; service_label: string | null; scopes: Record<string, string>;
  period_fy: string | null; expires_on: string | null; invited_by_name: string | null; audit: boolean;
}
export interface MyEngagements { invitations: Invite[]; firms: Array<{ id: string; name: string }> }

/** "Engaging Co Pvt Ltd invites you for GST (FY 2026-27), until 31 Mar. You will be able to: GST: Prepare and file, Sales and billing: View." */
export function inviteLine(i: Invite, shortDate: (iso: string) => string): string {
  const what = i.service_label ?? "advisory work";
  const year = i.period_fy ? ` (FY ${i.period_fy})` : "";
  const until = i.expires_on ? `, until ${shortDate(i.expires_on)}` : "";
  const scopes = Object.entries(i.scopes).sort(([a], [b]) => a.localeCompare(b)).map(([k, v]) => `${moduleLabel(k)}: ${levelLabel(v)}`).join(", ");
  return `${i.entity_name} invites you for ${what}${year}${until}. You will be able to: ${scopes}.${i.audit ? " As an auditor you can view and raise queries, nothing else." : ""}`;
}

/** What went wrong when accepting, in words: the independence rule is the one a practitioner needs spelled out. */
export function answerProblem(code: string | undefined, fallback: string): string {
  if (code === "independence_conflict") return "You or your firm already keep this company's books (or audit it) for that year, so you cannot take this engagement as well.";
  if (code === "not_found") return "This invitation is no longer open. It may have been withdrawn or already answered.";
  return fallback;
}

// ------------------------------------------------------------------ a firm's staff per client
export interface FirmClient { entity_id: string; legal_name: string; set_up_by_firm: boolean; services: string[]; staff: string[] }
export interface FirmPerson { user_id: string; name: string; role: string; sees_all: boolean }
export interface ClientStaff { clients: FirmClient[]; people: FirmPerson[] }

export const toggleStaff = (staff: string[], userId: string) => (staff.includes(userId) ? staff.filter((u) => u !== userId) : [...staff, userId].sort());
export const staffOnly = (people: FirmPerson[]) => people.filter((p) => !p.sees_all);

/** "2 of 3 staff" or "No staff assigned: only partners see this client". */
export function staffLine(c: FirmClient, people: FirmPerson[]): string {
  const total = staffOnly(people).length;
  if (total === 0) return "The firm has no staff yet: partners see every client";
  return c.staff.length === 0 ? "No staff assigned: only partners see this client" : `${c.staff.length} of ${total} staff`;
}
