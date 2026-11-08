/** Helpers for the portal connection and webhook screens. The server decides what is allowed; these only stop useless calls. */
import type { Tone } from "./compliance";
import { PORTAL_LABEL } from "./settings";

export const PORTAL_KEYS = Object.keys(PORTAL_LABEL);
export const VIA: Array<[string, string]> = [["", "Not stated"], ["GSP", "GSP"], ["ASP", "ASP"], ["direct", "Direct"]];
/** Portals that hand out an OTP session; the others have nothing to renew. Mirrors the server's list. */
export const SESSION_PORTALS = ["gstn", "irp", "eway", "mca", "income_tax", "traces", "epfo", "esic"];

export interface PortalConn { id: string; portal: string; via: string | null; status: string; session_expires_at: string | null; last_sync_at: string | null; last_error: string | null }
export interface ConnForm { portal: string; via: string; username: string; password: string }
export const blankConn = (): ConnForm => ({ portal: "gstn", via: "", username: "", password: "" });
/** Credentials are all-or-nothing; without them the portal is listed as not connected until they are added. */
export const connReady = (f: ConnForm) => PORTAL_KEYS.includes(f.portal) && (f.username.trim() === "") === (f.password === "");
export const connBody = (f: ConnForm) => ({
  portal: f.portal, ...(f.via ? { via: f.via } : {}), ...(f.username.trim() ? { credentials: { username: f.username.trim(), password: f.password } } : {}),
});
export const canRenew = (c: Pick<PortalConn, "portal" | "status">) => SESSION_PORTALS.includes(c.portal) && !["revoked", "not_connected"].includes(c.status);

export interface JobError { code?: string; title?: string; detail?: string }
export interface Job { id: string; kind: string; status: string; progress: number; result: Record<string, unknown> | null; error: JobError | string | null }
const errorText = (e: JobError | string | null): string => (!e ? "" : typeof e === "string" ? e : e.detail || e.title || e.code || "")
/** Where an OTP renewal stands, in words. */
export function jobStage(j: Pick<Job, "status" | "error">, doneLabel = "Renewed"): { label: string; tone: Tone; needsOtp: boolean; finished: boolean } {
  switch (j.status) {
    case "waiting_for_otp": return { label: "Waiting for the one-time code", tone: "wait", needsOtp: true, finished: false };
    case "succeeded": return { label: doneLabel, tone: "ok", needsOtp: false, finished: true };
    case "failed": return { label: errorText(j.error) ? `Failed: ${errorText(j.error)}` : "Failed", tone: "due", needsOtp: false, finished: true };
    case "cancelled": return { label: "Cancelled", tone: "prep", needsOtp: false, finished: true };
    case "running": return { label: "Working…", tone: "wait", needsOtp: false, finished: false };
    default: return { label: "Starting…", tone: "wait", needsOtp: false, finished: false };
  }
}
export const otpReady = (v: string) => /^[0-9]{6}$/.test(v.trim());

// ---- webhooks
export const EVENT_TYPES: Array<[string, string]> = [
  ["obligation.created", "A new obligation switched on"], ["obligation.due_soon", "An obligation is close to due"], ["obligation.filed", "Filed, acknowledgement attached"],
  ["obligation.overdue", "Passed its due date unfiled"], ["approval.requested", "Something waits on an approver"], ["approval.decided", "Approved or rejected"],
  ["notice.received", "A notice arrived from an authority"], ["threshold.crossed", "A fact crossed a rule threshold"], ["sales_document.issued", "Invoice or note issued"],
  ["sales_document.irn_generated", "E-invoice IRN returned"], ["sales_document.paid", "Invoice fully settled"], ["receipt.created", "Money received and allocated"],
  ["msme_payable.due_soon", "A small-supplier bill nears day 45"], ["job.succeeded", "A long-running job finished"], ["job.failed", "A long-running job failed"],
  ["portal.session_expiring", "A portal session needs an OTP renewal"],
];
export interface Hook { id: string; url: string; events: string[]; is_active: boolean; created_at: string }
export interface Delivery { id: number; type: string; attempt: number; status_code: number | null; error: string | null; attempted_at: string | null; delivered_at: string | null; next_retry_at: string | null }
export interface HookForm { url: string; events: string[]; all: boolean }
export const blankHook = (): HookForm => ({ url: "https://", events: [], all: false });
export function hookProblem(f: HookForm): string | null {
  let u: URL;
  try { u = new URL(f.url.trim()); } catch { return "Enter the full address, starting with https://"; }
  if (u.protocol !== "https:") return "The address must start with https://";
  if (u.username || u.password) return "The address cannot contain a user name or password";
  if (!f.all && f.events.length === 0) return "Choose at least one event, or all events";
  return null;
}
export const hookBody = (f: HookForm) => ({ url: f.url.trim(), events: f.all ? ["*"] : [...f.events].sort() });
export const eventsLabel = (events: string[]) => (events.includes("*") ? "All events" : events.length <= 2 ? events.join(", ") : `${events.length} events`);

/** How a delivery attempt went. */
export function deliveryState(d: Pick<Delivery, "delivered_at" | "status_code" | "next_retry_at" | "error">): { label: string; tone: Tone } {
  if (d.delivered_at) return { label: `Delivered${d.status_code ? ` (${d.status_code})` : ""}`, tone: "ok" };
  if (d.next_retry_at) return { label: `Will retry${d.status_code ? ` after ${d.status_code}` : ""}`, tone: "wait" };
  return { label: d.status_code ? `Failed (${d.status_code})` : d.error ? "Failed" : "Pending", tone: d.status_code || d.error ? "due" : "prep" };
}
