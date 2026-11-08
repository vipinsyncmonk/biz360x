/** Pure helpers for the Legal & Contracts and Document Vault screens. Alerts, notice dates and search ranking are the server's. */
import type { Tone } from "./compliance";

export interface Alert { code: string; level: string; title: string; detail: string }
export interface Term { term_key: string; value_text: string; source_page: number | null; extracted_by: string; confidence: number | null; verified: boolean }
export interface Contract {
  id: string; counterparty_name: string | null; title: string; kind: string; start_date: string | null; end_date: string | null; auto_renews: boolean; notice_days: number | null;
  notice_deadline: string | null; value_amount: string | null; value_period: string | null; status: string; signed_document_id: string | null; terms: Term[]; alerts: Alert[];
}
export interface Signer { id: string; person_name: string | null; external_name: string | null; sign_order: number; status: string }
export interface SignatureRequest { id: string; subject: string; status: string; sent_at: string | null; signers: Signer[] }
export interface Template { id: string; name: string; kind: string; version: string | number; reviewed_by: string | null; reviewed_on: string | null }
export interface Doc { id: string; folder: string; title: string; file_name: string; mime_type: string; size_bytes: number; source: string; period_label: string | null; valid_until: string | null; links: Array<{ subject: string; role: string }>; download_url: string | null; created_at: string }
export interface DocRequest { id: string; requested_by_name: string | null; title: string; due_on: string | null; status: string; document_ids: string[] }

export const KIND_LABEL: Record<string, string> = {
  customer: "Customer", vendor: "Vendor", property: "Property", employment: "Employment", nda: "NDA", consultant: "Consultant", licence: "Licence", loan: "Loan", shareholder: "Shareholder", other: "Other",
};

export const CONTRACT_STATUS: Record<string, { label: string; tone: Tone }> = {
  draft: { label: "Draft", tone: "prep" }, negotiating: { label: "Negotiating", tone: "prep" }, out_for_signature: { label: "Out for signature", tone: "wait" }, active: { label: "Active", tone: "ok" },
  expired: { label: "Expired", tone: "prep" }, terminated: { label: "Terminated", tone: "prep" }, renewed: { label: "Renewed", tone: "ok" },
};

export const SIGNER_STATUS: Record<string, string> = { queued: "Queued", waiting: "Waiting", signed: "Signed", declined: "Declined" };

export const SIGN_STATUS: Record<string, { label: string; tone: Tone }> = {
  draft: { label: "Draft", tone: "prep" }, sent: { label: "Sent", tone: "wait" }, partially_signed: { label: "Part signed", tone: "wait" }, completed: { label: "Completed", tone: "ok" },
  declined: { label: "Declined", tone: "due" }, expired: { label: "Expired", tone: "due" },
};

export const alertTone = (level: string): Tone => (level === "warn" ? "due" : "wait");

/** The date a person should look at: the notice deadline for auto-renewing contracts, else the end date. */
export function nextDate(c: Pick<Contract, "auto_renews" | "notice_deadline" | "end_date">): { label: string; date: string } | null {
  if (c.auto_renews && c.notice_deadline) return { label: "Notice by", date: c.notice_deadline };
  if (c.end_date) return { label: "Ends", date: c.end_date };
  return null;
}

/** Contracts that need action first: those with a warning, then by next date. Stable for equal keys. */
export function byUrgency(list: Contract[]): Contract[] {
  const rank = (c: Contract) => (c.alerts.some((a) => a.level === "warn") ? 0 : 1);
  return list.map((c, i) => ({ c, i })).sort((a, b) => rank(a.c) - rank(b.c) || (nextDate(a.c)?.date ?? "9999").localeCompare(nextDate(b.c)?.date ?? "9999") || a.i - b.i).map((x) => x.c);
}

// ---- documents
export const FOLDERS: Array<[string, string]> = [
  ["", "All files"], ["incorporation", "Incorporation"], ["tax", "Tax"], ["mca", "MCA"], ["payroll", "Payroll"], ["contracts", "Contracts"], ["board", "Board"],
  ["bank_finance", "Bank and finance"], ["sales", "Sales"], ["purchases", "Purchases"], ["licences", "Licences"], ["other", "Other"],
];

export const SOURCE_LABEL: Record<string, string> = {
  gstn: "GST portal", mca: "MCA", income_tax: "Income Tax", traces: "TRACES", epfo: "EPFO", esic: "ESIC", irp: "e-invoice portal", eway: "E-way bill portal", udyam: "Udyam",
  bank: "Bank", biz360x: "Biz360X", upload: "Uploaded", email: "Email",
};

export function fileSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

export function fileExt(name: string): string {
  const m = /\.([A-Za-z0-9]{1,5})$/.exec(name);
  return (m ? m[1]! : "file").toUpperCase();
}

/** Whole days from `today` to an ISO date; negative when past. */
export function daysUntil(iso: string, today: Date = new Date()): number {
  const [y, m, d] = iso.slice(0, 10).split("-").map(Number) as [number, number, number];
  return Math.round((Date.UTC(y, m - 1, d) - Date.UTC(today.getFullYear(), today.getMonth(), today.getDate())) / 86_400_000);
}

export function expiryLabel(iso: string, today: Date = new Date()): string {
  const n = daysUntil(iso, today);
  return n < 0 ? `Expired ${-n} ${-n === 1 ? "day" : "days"} ago` : n === 0 ? "Expires today" : `Expires in ${n} ${n === 1 ? "day" : "days"}`;
}

/** Template versions arrive as "v1"; show them once with a single "v". */
export const versionLabel = (v: string | number) => `v${String(v).replace(/^v/i, "")}`;
