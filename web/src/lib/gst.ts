/** GST page actions: computing and filing returns, syncing GSTR-2A/2B, and settling mismatches. The server owns every rule; this only words them. */

export interface ItcMatch {
  id: string; status: string; supplier_gstin: string; supplier_name: string; purchase_bill_id: string | null; books_tax: string | null; portal_tax: string | null;
  tax_difference: string; finding: string; suggested_action: string; action: string | null; action_taken_at: string | null;
}

/** What can be done to a mismatch, in the order to offer it. Mirrors the server's list; the server refuses anything else. */
export const MATCH_ACTIONS: Record<string, Array<[string, string]>> = {
  matched: [["claim", "Claim the credit"], ["defer", "Leave for later"]],
  amount_differs: [["claim_lower", "Claim the lower amount"], ["ask_to_amend", "Ask the supplier to amend"], ["investigate", "Investigate"], ["defer", "Leave for later"]],
  not_in_portal: [["remind_supplier", "Remind the supplier"], ["defer", "Leave for later"], ["reject", "Do not claim"]],
  not_in_books: [["book_bill", "Book the bill"], ["investigate", "Investigate"], ["reject", "Ignore"]],
};
export const matchActions = (status: string): Array<[string, string]> => MATCH_ACTIONS[status] ?? [];
export const ACTION_LABEL: Record<string, string> = Object.fromEntries(Object.values(MATCH_ACTIONS).flat());

export const resolveBody = (action: string, note: string) => ({ action, ...(note.trim() ? { note: note.trim() } : {}) });

/** Open mismatches only: nothing to do for a pair somebody already settled. */
export const isOpen = (m: Pick<ItcMatch, "action_taken_at">) => !m.action_taken_at;
/** Suppliers that can be reminded at once: invoices booked but missing from the portal and still open. */
export const remindable = (ms: ItcMatch[]): number => ms.filter((m) => isOpen(m) && m.status === "not_in_portal").length;
export const remindLabel = (n: number) => (n === 0 ? "No supplier to remind" : `Remind suppliers (${n} ${n === 1 ? "invoice" : "invoices"})`);

/** "2026-10": the month just ended, which is what the next return covers. */
export function lastMonth(today: Date = new Date()): string {
  const d = new Date(today.getFullYear(), today.getMonth() - 1, 1);
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}`;
}
export const periodOk = (p: string) => /^\d{4}-(0[1-9]|1[0-2])$/.test(p);
export const SOURCES: Array<[string, string]> = [["2B", "GSTR-2B"], ["2A", "GSTR-2A"], ["IMS", "Invoice management (IMS)"]];
export const syncBody = (period: string, source: string, registrationId: string) => ({ period, source, ...(registrationId ? { registration_ids: [registrationId] } : {}) });

/** What a return can be asked to do next. */
export const canCompute = (status: string) => status === "drafting" || status === "ready";
export const canFile = (status: string) => status === "ready";
export const SIGNING: Array<[string, string]> = [["evc_otp", "EVC (one-time code)"], ["dsc", "Digital signature (DSC)"]];
export const fileBody = (signing: string) => ({ signing });

/** One line for a mismatch: "Books ₹1,800.00, portal ₹1,500.00" */
export function taxNote(m: Pick<ItcMatch, "books_tax" | "portal_tax">, fmt: (v: string) => string): string {
  const parts: string[] = [];
  if (m.books_tax !== null) parts.push(`Books ${fmt(m.books_tax)}`);
  if (m.portal_tax !== null) parts.push(`Portal ${fmt(m.portal_tax)}`);
  return parts.join(", ");
}
