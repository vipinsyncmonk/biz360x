/** Helpers for the collections screen: receipts, ageing, reminders and recurring invoices. The server checks every rule; these only stop useless calls. */
import { addDays, isoDate, toPaise } from "./format";
import type { Tone } from "./compliance";

export const BUCKETS: Array<[string, string]> = [["b0_30", "0–30"], ["b31_45", "31–45"], ["b46_60", "46–60"], ["b61_90", "61–90"], ["b90_plus", "90+"]];

export const METHODS: Array<[string, string]> = [
  ["neft", "NEFT"], ["rtgs", "RTGS"], ["imps", "IMPS"], ["upi", "UPI"], ["cheque", "Cheque"], ["card", "Card"], ["netbanking", "Net banking"], ["treds", "TReDS"], ["cash", "Cash"],
];
export const METHOD_LABEL: Record<string, string> = Object.fromEntries([...METHODS, ["adjustment", "Adjustment"]]);

export interface OpenInvoice { id: string; number: string | null; doc_date: string; due_date: string | null; balance_due: string }
export type Allocation = Record<string, string>;

const paiseStr = (p: bigint) => `${p / 100n}.${(p % 100n).toString().padStart(2, "0")}`;
const isAmount = (v: string) => /^\d+(\.\d{1,2})?$/.test(v.trim());

/** Oldest invoice first, then by number. */
export function oldestFirst<T extends OpenInvoice>(rows: T[]): T[] {
  return [...rows].sort((a, b) => (a.due_date ?? a.doc_date).localeCompare(b.due_date ?? b.doc_date) || (a.number ?? "").localeCompare(b.number ?? ""));
}

/** Spread an amount over the open invoices, oldest first, never past an invoice's balance. Only a suggestion: the person can change every figure. */
export function suggestAllocation(amount: string, invoices: OpenInvoice[]): Allocation {
  let left = isAmount(amount) ? toPaise(amount) : 0n;
  const out: Allocation = {};
  for (const inv of oldestFirst(invoices)) {
    if (left <= 0n) break;
    const owed = toPaise(inv.balance_due);
    const take = left < owed ? left : owed;
    if (take > 0n) { out[inv.id] = paiseStr(take); left -= take; }
  }
  return out;
}

export function allocatedTotal(alloc: Allocation): string {
  return paiseStr(Object.values(alloc).reduce<bigint>((a, v) => a + (isAmount(v) ? toPaise(v) : 0n), 0n));
}

export function unallocated(amount: string, alloc: Allocation): string {
  const left = (isAmount(amount) ? toPaise(amount) : 0n) - toPaise(allocatedTotal(alloc));
  return left < 0n ? "0.00" : paiseStr(left);
}

/** The first thing wrong with an allocation, in words, or null. `available` is the receipt amount or what is left of it. */
export function allocationProblem(available: string, alloc: Allocation, invoices: OpenInvoice[]): string | null {
  let total = 0n;
  for (const [id, v] of Object.entries(alloc)) {
    if (v.trim() === "") continue;
    const inv = invoices.find((i) => i.id === id);
    if (!isAmount(v)) return `${inv?.number ?? "An invoice"}: enter an amount like 1500.00`;
    const p = toPaise(v);
    if (inv && p > toPaise(inv.balance_due)) return `${inv.number ?? "An invoice"}: more than the ${inv.balance_due} still owed`;
    total += p;
  }
  if (total > (isAmount(available) ? toPaise(available) : 0n)) return "The invoices add up to more than the receipt";
  return null;
}

/** The body for POST /receipts and POST /receipts/{id}/allocate: only the invoices with an amount. */
export function allocationBody(alloc: Allocation): Array<{ invoice_id: string; amount: string }> {
  return Object.entries(alloc).filter(([, v]) => isAmount(v) && toPaise(v) > 0n).map(([invoice_id, v]) => ({ invoice_id, amount: paiseStr(toPaise(v)) }));
}

export const receiptReady = (r: { partyId: string; date: string; amount: string }) => !!r.partyId && !!r.date && isAmount(r.amount) && toPaise(r.amount) > 0n;

export const RECEIPT_STATUS: Record<string, string> = { pending: "Pending", confirmed: "Confirmed", bounced: "Bounced", reversed: "Reversed" };

// ---- reminders
export const ACTION_LABEL: Record<string, string> = { message: "Send a reminder", task: "Create a task", escalate: "Escalate", hold_orders: "Hold new orders" };
export const CHANNEL_LABEL: Record<string, string> = { email: "email", whatsapp: "WhatsApp", sms: "SMS" };

export function offsetLabel(days: number): string {
  if (days === 0) return "On the due date";
  const n = Math.abs(days);
  return `${n} ${n === 1 ? "day" : "days"} ${days < 0 ? "before the due date" : "after the due date"}`;
}

export interface Step { offset_days: number; action: string; channels?: string[] | null }
export function stepSentence(s: Step): string {
  const how = s.action === "message" && s.channels?.length ? ` by ${s.channels.map((c) => CHANNEL_LABEL[c] ?? c).join(" and ")}` : "";
  return `${offsetLabel(s.offset_days)}: ${(ACTION_LABEL[s.action] ?? s.action).toLowerCase()}${how}`;
}

export const pauseDefault = (today: Date = new Date()) => isoDate(addDays(today, 14));
export const pauseReady = (until: string, today: Date = new Date()) => /^\d{4}-\d{2}-\d{2}$/.test(until) && until > isoDate(today);

// ---- recurring invoices
export const FREQ_LABEL: Record<string, string> = { monthly: "Every month", quarterly: "Every quarter", half_yearly: "Every six months", annual: "Every year" };

export function recurringState(r: { is_active: boolean; next_run_on: string; ends_on: string | null }, today: Date = new Date()): { label: string; tone: Tone } {
  const t = isoDate(today);
  if (!r.is_active) return { label: "Paused", tone: "prep" };
  if (r.ends_on && r.ends_on < t) return { label: "Ended", tone: "prep" };
  if (r.next_run_on <= t) return { label: "Due now", tone: "due" };
  return { label: "Active", tone: "ok" };
}
