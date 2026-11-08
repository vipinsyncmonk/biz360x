/** MSME track screen: wording and small rules. Interest, claims and classification are all worked out by the server; nothing here calculates them. */
import type { Tone } from "./compliance";
import { formatInr, sumMoney, toPaise } from "./format";

export interface LateInvoice { document_id: string; number: string; balance_due: string; appointed_on: string; days_late: number; interest: string | null; on_treds: boolean }
export interface LateBuyer { party_id: string; name: string; invoices: LateInvoice[]; principal: string; interest: string | null; days_late: number; on_treds: boolean; claim: { id: string; status: string } | null }
export interface LateBuyers {
  as_of: string; supplier_class: string | null; interest_applies: boolean; rate: { bank_rate_pct: string; effective_from: string; annual_pct: string } | null; buyers: LateBuyer[];
  totals: { buyers: number; invoices: number; principal: string; interest: string | null };
}
export interface Claim { id: string; buyer_party_id: string; buyer_name: string; invoices: Array<{ number: string; balance_due: string }>; principal: string; interest_claimed: string; forum: string; status: string; case_no: string | null; filed_on: string | null }
export interface Treds { id: string; sales_document_id: string; invoice_number: string; buyer_name: string; invoice_total: string; platform: string; uploaded_on: string; status: string; discount_rate_pct: string | null; amount_received: string | null }
export interface UdyamTest { key: string; label: string; value: string | null; limit: string; pct: number | null }
export interface Classification { declared: string | null; udyam_number: string | null; computed: string | null; investment: string | null; turnover: string | null; tests: UdyamTest[] }
export interface Payable { bill_id: string; legal_name: string; msme_pay_by: string; days_left: number; breached: boolean; balance_due: string }

// ---- money in lakh and crore, the way the design shows it
/** "1840000.00" -> "₹18.4 L"; a crore or more reads "₹1.2 Cr"; under a lakh reads in rupees. */
export function lakh(v: string | null | undefined): string {
  if (v === null || v === undefined) return "–";
  const p = toPaise(v), abs = p < 0n ? -p : p;
  const sign = p < 0n ? "-" : "";
  if (abs >= 1_000_000_000n) return `${sign}₹${(Number(abs / 10_000_000n) / 100).toFixed(1)} Cr`;
  if (abs >= 10_000_000n) return `${sign}₹${(Number(abs / 1_000_000n) / 10).toFixed(1)} L`;
  return formatInr(v, { decimals: false });
}

export function headline(t: LateBuyers["totals"], applies: boolean): string {
  if (t.buyers === 0) return "No buyer is past the day payment was due.";
  const who = `${t.buyers} ${t.buyers === 1 ? "buyer" : "buyers"}`;
  return `${lakh(t.principal)} is with ${who} past the due day.${applies ? " Here is how to get it back." : ""}`;
}
export const rateNote = (r: LateBuyers["rate"]): string => (r ? `Interest is three times the RBI bank rate (${r.bank_rate_pct}% since ${r.effective_from.slice(8, 10)}/${r.effective_from.slice(5, 7)}/${r.effective_from.slice(0, 4)}), so ${r.annual_pct}% a year, compounded monthly.` : "No RBI bank rate is on record, so interest cannot be worked out.");
export const daysLate = (n: number) => `${n} ${n === 1 ? "day" : "days"} late`;

// ---- ageing
export const AGE_BUCKETS: Array<[string, string]> = [["b0_30", "0–30"], ["b31_45", "31–45"], ["b46_60", "46–60"], ["b61_90", "61–90"], ["b90_plus", "90+"]];
export interface Bar { key: string; label: string; value: string; width: number; late: boolean }
/** One bar per bucket across all buyers; the biggest fills the bar; past day 45 is marked late. */
export function ageBars(rows: Array<Record<string, unknown>>): Bar[] {
  const sums = AGE_BUCKETS.map(([k]) => sumMoney(rows.map((r) => (typeof r[k] === "string" ? (r[k] as string) : "0"))));
  const max = sums.reduce((m, s) => (toPaise(s) > m ? toPaise(s) : m), 0n);
  return AGE_BUCKETS.map(([key, label], i) => ({ key, label, value: sums[i]!, width: max > 0n ? Number((toPaise(sums[i]!) * 100n) / max) : 0, late: i >= 2 }));
}

// ---- claims
export const CLAIM_STATE: Record<string, { label: string; tone: Tone }> = {
  draft: { label: "Draft", tone: "prep" }, filed: { label: "Filed", tone: "wait" }, conciliation: { label: "In conciliation", tone: "wait" }, arbitration: { label: "In arbitration", tone: "wait" },
  settled: { label: "Settled", tone: "ok" }, closed: { label: "Closed", tone: "prep" },
};
export interface BuyerAction { claim: "start" | "file" | "view" | null; treds: boolean; remind: boolean }
/** What can be done about a buyer: claim the interest, discount invoices on TReDS, or remind them from Collections. */
export function buyerAction(b: Pick<LateBuyer, "claim" | "invoices" | "on_treds">, interestApplies: boolean): BuyerAction {
  const claim = !interestApplies ? null : !b.claim ? "start" : b.claim.status === "draft" ? "file" : "view";
  return { claim, treds: b.invoices.some((i) => !i.on_treds), remind: true };
}
export const claimLine = (c: Claim) => `${c.invoices.length} ${c.invoices.length === 1 ? "invoice" : "invoices"} · principal ${formatInr(c.principal, { decimals: false })} + interest ${formatInr(c.interest_claimed, { decimals: false })}`;

// ---- TReDS
export const PLATFORMS = ["RXIL", "M1xchange", "Invoicemart", "C2FO"];
export const TREDS_STATE: Record<string, { label: string; tone: Tone }> = {
  uploaded: { label: "Uploaded", tone: "prep" }, accepted: { label: "Buyer accepted", tone: "wait" }, auctioned: { label: "In auction", tone: "wait" }, financed: { label: "Financed", tone: "ok" },
  settled: { label: "Settled", tone: "ok" }, rejected: { label: "Rejected", tone: "due" },
};
const NEXT: Record<string, Array<[string, string]>> = {
  uploaded: [["accepted", "Buyer accepted"], ["rejected", "Rejected"]], accepted: [["auctioned", "In auction"], ["rejected", "Rejected"]],
  auctioned: [["financed", "Financed"], ["rejected", "Rejected"]], financed: [["settled", "Settled"]], settled: [], rejected: [],
};
export const tredsNext = (status: string): Array<[string, string]> => NEXT[status] ?? [];
export interface TredsForm { status: string; rate: string; amount: string }
export function tredsProblem(f: TredsForm): string | null {
  if (f.status !== "financed") return null;
  if (!/^\d{1,2}(\.\d{1,3})?$/.test(f.rate.trim()) || Number(f.rate) <= 0 || Number(f.rate) > 100) return "Enter the discount rate as a percentage, such as 7.5";
  if (!/^\d{1,13}(\.\d{1,2})?$/.test(f.amount.trim()) || !/[1-9]/.test(f.amount)) return "Enter the amount you received, such as 1100000";
  return null;
}
export const tredsBody = (f: TredsForm) => ({ status: f.status, ...(f.status === "financed" ? { discount_rate_pct: f.rate.trim(), amount_received: f.amount.trim() } : {}) });
/** Invoices of buyers who are late and not already uploaded, for the upload picker. */
export const uploadable = (buyers: LateBuyer[]) => buyers.flatMap((b) => b.invoices.filter((i) => !i.on_treds).map((i) => ({ ...i, buyer: b.name })));

// ---- what you owe small suppliers
export function payablesSummary(rows: Payable[]): { owed: string; breached: number; count: number } {
  return { owed: sumMoney(rows.map((r) => r.balance_due)), breached: rows.filter((r) => r.breached).length, count: rows.length };
}

// ---- Udyam
export const CLASS_LABEL: Record<string, string> = { micro: "Micro enterprise", small: "Small enterprise", medium: "Medium enterprise", above_medium: "Above medium" };
export function udyamBar(t: UdyamTest): { label: string; value: string; width: number } {
  const pct = t.pct ?? 0;
  return { label: t.label, value: t.value === null ? "Not recorded" : `${lakh(t.value)} of ${lakh(t.limit)}`, width: Math.max(0, Math.min(100, Math.round(pct))) };
}
/** True when the company's own figures put it in a different class than the one it declared. */
export const classMismatch = (c: Pick<Classification, "declared" | "computed">) => !!c.declared && !!c.computed && c.declared !== c.computed;

export interface FactsForm { investment: string; turnover: string }
export const blankFacts = (c: Pick<Classification, "investment" | "turnover"> | null): FactsForm => ({ investment: c?.investment ? String(Number(c.investment)) : "", turnover: c?.turnover ? String(Number(c.turnover)) : "" });
export function factsProblem(f: FactsForm): string | null {
  for (const [v, l] of [[f.investment, "investment in plant and machinery"], [f.turnover, "annual turnover"]] as const) {
    if (v.trim() && !/^\d{1,15}$/.test(v.trim())) return `Enter ${l} in whole rupees, with no commas`;
  }
  return !f.investment.trim() && !f.turnover.trim() ? "Enter at least one figure" : null;
}
export const factsBody = (f: FactsForm, today: string) => [
  ...(f.investment.trim() ? [{ fact_key: "investment_plant_machinery", value: Number(f.investment), as_of: today }] : []),
  ...(f.turnover.trim() ? [{ fact_key: "aato", value: Number(f.turnover), as_of: today }] : []),
];

// ---- registering with Udyam
export interface UdyamForm { number: string; cls: string }
export const blankUdyam = (): UdyamForm => ({ number: "", cls: "micro" });
export function udyamProblem(f: UdyamForm): string | null {
  if (!/^UDYAM-[A-Z]{2}-\d{2}-\d{7}$/.test(f.number.trim().toUpperCase())) return "A Udyam number looks like UDYAM-KA-03-0054321";
  return ["micro", "small", "medium"].includes(f.cls) ? null : "Choose the class on your Udyam certificate";
}
export const udyamBody = (f: UdyamForm) => ({ udyam_number: f.number.trim().toUpperCase(), udyam_class: f.cls });
