/** E-invoicing: whether it applies, from when, and the turnover it rests on. The server decides; this only words it and builds the turnover declaration. */
import type { components } from "@/api/schema";
import { formatInr, isoDate } from "./format";
import type { Tone } from "./compliance";

export type EInvoiceStatus = components["schemas"]["EInvoiceStatus"];

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const longDate = (iso: string) => { const [y, m, d] = iso.split("-"); return `${Number(d)} ${MONTHS[Number(m) - 1]} ${y}`; };

export function describe(s: EInvoiceStatus): { tone: Tone; pill: string; headline: string; detail: string } {
  const limit = formatInr(s.limit, { decimals: false });
  const now = `${formatInr(s.this_year.turnover, { decimals: false })} so far in FY ${s.financial_year} (${s.this_year.percent_of_limit}% of ${limit})`;
  switch (s.level) {
    case "applies":
      return { tone: "prep", pill: "Required", headline: "E-invoicing applies: every B2B and export invoice needs an IRN.",
        detail: `Turnover went above ${limit} in FY ${s.crossed_in}, so it applies from ${s.applies_from ? longDate(s.applies_from) : "that year"} and stays on. ${now}.` };
    case "crossed":
      return { tone: "wait", pill: `From ${s.applies_from ? longDate(s.applies_from) : "next year"}`, headline: "Turnover has crossed the limit, so e-invoicing starts next financial year.",
        detail: `Not needed for the rest of FY ${s.financial_year}. B2B and export invoices dated ${s.applies_from ? longDate(s.applies_from) : "from the next 1 April"} onwards need an IRN, so choose an e-invoicing provider before then. ${now}.` };
    case "approaching":
      return { tone: "wait", pill: "Getting close", headline: "Turnover is nearing the e-invoicing limit.",
        detail: `If it goes above ${limit} this year, e-invoicing starts on the next 1 April. ${now}.` };
    default:
      return { tone: "ok", pill: "Not required", headline: "E-invoicing is not required yet.",
        detail: `It starts the financial year after turnover first goes above ${limit}. ${now}.` };
  }
}

/** Financial years a person can declare turnover for: this one and the five before it, newest first ("2026-27"). */
export function yearOptions(current: string): string[] {
  const start = Number(current.slice(0, 4));
  return Array.from({ length: 6 }, (_, i) => `${start - i}-${String((start - i + 1) % 100).padStart(2, "0")}`);
}

/** The date a year's turnover is dated: its 31 March, or today for a year that has not ended. The server reads the year from this date. */
export function declaredOn(fy: string, today: Date = new Date()): string {
  const end = `${Number(fy.slice(0, 4)) + 1}-03-31`;
  return end > isoDate(today) ? isoDate(today) : end;
}

export interface DeclareForm { fy: string; amount: string }
const AMOUNT = /^\d{1,13}(\.\d{1,2})?$/;
export function declareProblem(f: DeclareForm): string | null {
  const a = f.amount.replace(/[,\s₹]/g, "");
  return AMOUNT.test(a) ? null : "Enter the turnover in rupees, such as 4,50,00,000";
}
export const declareBody = (f: DeclareForm, today: Date = new Date()) =>
  [{ fact_key: "aato", value: Number(f.amount.replace(/[,\s₹]/g, "")), as_of: declaredOn(f.fy, today) }];
