/** The customer payment page. It has no sign-in, so it talks to the public /pay endpoints with plain fetch and never sends a token. */
import { API_BASE, newIdempotencyKey, toProblem } from "./api";
import type { components } from "@/api/schema";

export type PublicInvoice = components["schemas"]["PublicInvoice"];
export type IntentResult = components["schemas"]["PaymentIntentResult"];

export const METHOD_INFO: Record<string, { title: string; hint: string; cta: string }> = {
  bank_transfer: { title: "Bank transfer · NEFT, RTGS or IMPS", hint: "Matched to this invoice automatically.", cta: "I have sent the transfer" },
  upi: { title: "UPI", hint: "Any UPI app, up to your bank's daily limit.", cta: "Pay with UPI" },
  netbanking: { title: "Net banking", hint: "Corporate and retail accounts.", cta: "Continue to your bank" },
  card: { title: "Card", hint: "Debit or credit card.", cta: "Pay by card" },
};
const ORDER = ["bank_transfer", "upi", "netbanking", "card"];

/** The methods this invoice allows, bank transfer first, anything unknown dropped. */
export function orderMethods(methods: string[] | undefined): string[] {
  return ORDER.filter((m) => (methods ?? []).includes(m));
}

export function overdueLabel(days: number): string | null {
  return days > 0 ? `${days} ${days === 1 ? "day" : "days"} overdue` : null;
}

/** What the page shows for a link: the invoice to pay, a thank-you, or "no longer active". */
export function pageState(inv: Pick<PublicInvoice, "status">): "due" | "paid" | "closed" {
  return inv.status === "due" ? "due" : inv.status === "paid" ? "paid" : "closed";
}

export const queryReady = (q: { email: string; message: string }) =>
  /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(q.email.trim()) && q.message.trim().length > 0 && q.message.length <= 2000;

const base = (code: string) => `${API_BASE}/pay/${encodeURIComponent(code)}`;

export async function getInvoice(code: string): Promise<PublicInvoice> {
  const res = await fetch(base(code), { headers: { Accept: "application/json" } });
  if (!res.ok) throw await toProblem(res);
  return (await res.json()) as PublicInvoice;
}

export async function startPayment(code: string, method: string): Promise<IntentResult> {
  const res = await fetch(`${base(code)}/intents`, { method: "POST", headers: { "Content-Type": "application/json", "Idempotency-Key": newIdempotencyKey() }, body: JSON.stringify({ method }) });
  if (!res.ok) throw await toProblem(res);
  return (await res.json()) as IntentResult;
}

export async function raiseQuery(code: string, q: { name: string; email: string; message: string }): Promise<void> {
  const body = { email: q.email.trim(), message: q.message.trim(), ...(q.name.trim() ? { name: q.name.trim() } : {}) };
  const res = await fetch(`${base(code)}/queries`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  if (!res.ok) throw await toProblem(res);
}
