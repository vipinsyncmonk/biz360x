/** Pure helpers for the Finance & Books and GST & Tax screens. Every figure comes from the server; nothing here does tax or ledger maths. */
import { isoDate, sumMoney, toPaise } from "./format";
import type { Tone } from "./compliance";

export interface Period { id: string; fy: string; period_start: string; period_end: string; status: string; locked_at: string | null; close_progress: { done: number; total: number } }
export interface CloseTask { id: string; task_key: string; title: string; is_automatic: boolean; done_at: string | null }
export interface Suggestion { id: string; target_kind: string; label: string; amount: string; confidence: number; rule: string | null; status: string }
export interface BankTxn { id: string; txn_date: string; amount: string; description: string; counterparty_name: string | null; match_status: string; suggestions: Suggestion[] }
export interface ReportRow { line: string; values: string[] }
export interface Report { columns: string[]; rows: ReportRow[]; provisional: boolean }

const MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"];

/** "2026-10-01" -> "October 2026" */
export function periodName(start: string): string {
  const [y, m] = start.split("-");
  return `${MONTHS[Number(m) - 1] ?? ""} ${y}`;
}

/** The month to close: the latest unlocked month that has already ended (October, in early November); else the first unlocked one; else the last. */
export function currentPeriod(periods: Period[], today: Date = new Date()): Period | undefined {
  const now = isoDate(today);
  const open = periods.filter((p) => p.status !== "locked");
  const ended = open.filter((p) => p.period_end < now);
  return ended[ended.length - 1] ?? open[0] ?? periods[periods.length - 1];
}

export function closeCounts(tasks: CloseTask[]): { done: number; total: number } {
  return { done: tasks.filter((t) => t.done_at).length, total: tasks.length };
}

export const confidenceLabel = (c: number) => `${Math.round(c * 100)}% match`;

/** Report cells are two-decimal strings; the design shows ₹ lakh. Display only: rounds to one decimal using integers. */
export function lakh(v: string): string {
  const p = toPaise(v);
  const neg = p < 0n;
  const abs = neg ? -p : p;
  const tenths = (abs + 500_000n) / 1_000_000n;       // paise -> tenths of a lakh (1 lakh = 10,000,000 paise), rounded half up
  const lk = `${tenths / 10n}.${tenths % 10n}`;
  return `${neg && tenths !== 0n ? "-" : ""}${lk}`;
}

export const isTotalRow = (line: string) => /^(Total|Net profit)/.test(line);

// ---- GST
export interface GstReturn { id: string; registration_id: string; return_type: string; period: string; status: string; output_tax: string; itc_claimed: string; cash_paid: string; arn: string | null; filed_at: string | null }
export interface GstBalance { registration_id: string; ledger: string; head: string; as_of: string; balance: string }
export interface ItcBucket { status: string; count: number; itc: string }

export const RETURN_STATUS: Record<string, { label: string; tone: Tone }> = {
  drafting: { label: "Drafting", tone: "prep" }, ready: { label: "Ready to file", tone: "wait" }, submitted: { label: "Submitted", tone: "wait" }, filed: { label: "Filed", tone: "ok" },
};

export const RETURN_LABEL: Record<string, string> = {
  GSTR1: "GSTR-1 · outward supplies", IFF: "IFF · invoice furnishing", GSTR3B: "GSTR-3B · summary and payment", GSTR9: "GSTR-9 · annual return", GSTR9C: "GSTR-9C · reconciliation",
  CMP08: "CMP-08 · composition payment", GSTR4: "GSTR-4 · composition annual", ITC04: "ITC-04 · job work",
};

export const ITC_LABEL: Record<string, string> = {
  matched: "Matched", amount_differs: "Amount differs", not_in_portal: "Not on the GST portal", not_in_books: "Not in your books",
};

export const HEADS = ["igst", "cgst", "sgst", "cess"] as const;

/** Sum the balances of one ledger across registrations, per head. */
export function ledgerTotals(rows: GstBalance[], ledger: string): Record<string, string> {
  const out: Record<string, string> = {};
  for (const h of HEADS) out[h] = sumMoney(rows.filter((r) => r.ledger === ledger && r.head === h).map((r) => r.balance));
  return out;
}

export const totalOf = (t: Record<string, string>) => sumMoney(Object.values(t));

/** "2026-10" -> "Oct 2026" */
export function returnPeriod(period: string): string {
  const [y, m] = period.split("-");
  return m ? `${(MONTHS[Number(m) - 1] ?? "").slice(0, 3)} ${y}` : period;
}
