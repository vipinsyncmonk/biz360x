/** Pure helpers for the Finance & Books and GST & Tax screens. Every figure comes from the server; nothing here does tax or ledger maths. */
import { formatInr, isoDate, sumMoney, toPaise } from "./format";
import type { Tone } from "./compliance";

export interface Period { id: string; fy: string; period_start: string; period_end: string; status: string; locked_at: string | null; close_progress: { done: number; total: number } }
export interface CloseTask { id: string; task_key: string; title: string; is_automatic: boolean; done_at: string | null }
export interface ExportHint { shipping_bill_id: string; sb_number: string | null; currency: string | null; proposed_foreign: string | null; expected_inr: string | null; difference_inr: string | null }
export interface Suggestion { id: string; target_kind: string; target_id?: string; label: string; amount: string; confidence: number; rule: string | null; reason?: string | null; status: string; export?: ExportHint | null }
export interface BankTxn { id: string; txn_date: string; amount: string; description: string; counterparty_name: string | null; match_status: string; suggestions: Suggestion[];
  mode?: string | null; counterparty_key?: string | null; remark?: string | null }
export interface Account { id: string; code: string; name: string; type: string }
export interface BankRule { id: string; direction: string; counterparty_key: string | null; keyword: string | null; mode: string | null; account_id: string; account_code: string; account_name: string; hits: number }
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

// ---- a credit that is money from abroad. The statement shows rupees after the bank's charges and its own rate, so it is never exact: the person gives the foreign amount and the charges.
const MONEY = /^\d{1,13}(\.\d{1,2})?$/;
export interface ProceedsForm { foreign: string; charges: string; firc: string }
export const proceedsForm = (h: ExportHint): ProceedsForm => ({ foreign: h.proposed_foreign ?? "", charges: "", firc: "" });
export function proceedsProblem(f: ProceedsForm): string | null {
  if (!MONEY.test(f.foreign.trim()) || Number(f.foreign) <= 0) return "Enter the amount in the foreign currency, from the FIRC or the customer's advice";
  if (f.charges.trim() && !MONEY.test(f.charges.trim())) return "Enter the bank's charges in rupees, such as 450.00";
  return null;
}
export const proceedsBody = (shippingBillId: string, f: ProceedsForm) => ({
  shipping_bill_id: shippingBillId, amount_foreign: f.foreign.trim(),
  ...(f.charges.trim() ? { bank_charges: f.charges.trim() } : {}), ...(f.firc.trim() ? { firc_no: f.firc.trim() } : {}),
});
/** What the matcher saw: the amount it expected at the invoice's rate and why the credit may differ from it. */
export function exportHintText(h: ExportHint): string {
  if (!h.proposed_foreign) return "Looks like money from abroad";
  const worth = h.expected_inr ? ` (${formatInr(h.expected_inr)} at the invoice rate)` : "";
  const d = h.difference_inr == null ? 0 : toPaise(h.difference_inr);
  const gap = d === 0n ? "The credit matches to the rupee." : `The credit is ${formatInr((h.difference_inr ?? "").replace("-", ""))} ${d < 0n ? "less" : "more"}, which the bank's charges and its own rate can explain.`;
  return `Looks like ${[h.currency, h.proposed_foreign].filter(Boolean).join(" ")}${worth}. ${gap}`;
}

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

// ---- booking a bank line: the other half is an account
export const MODE_LABEL: Record<string, string> = { upi: "UPI", neft: "NEFT", rtgs: "RTGS", imps: "IMPS", auto_debit: "Auto-debit", transfer: "Transfer", cheque: "Cheque", card: "Card", cash: "Cash", charges: "Charges", interest: "Interest", other: "" };
export const accountLabel = (a: Pick<Account, "code" | "name">) => `${a.code} · ${a.name}`;
export const TYPE_ORDER: Array<[string, string]> = [["expense", "Expenses"], ["asset", "Assets"], ["liability", "Liabilities"], ["income", "Income"], ["equity", "Equity"]];

/** The other open lines a rule on this line's counterparty would also cover: same counterparty, money moving the same way. */
export function similarLines(rows: BankTxn[], t: BankTxn): BankTxn[] {
  if (!t.counterparty_key) return [];
  return rows.filter((x) => x.id !== t.id && x.counterparty_key === t.counterparty_key && x.match_status !== "matched" && x.match_status !== "ignored" && (Number(x.amount) < 0) === (Number(t.amount) < 0));
}

export interface ClassifyForm { account: string; memo: string; remember: "no" | "counterparty" | "keyword"; keyword: string; applySimilar: boolean }
export const blankClassify = (account = ""): ClassifyForm => ({ account, memo: "", remember: "no", keyword: "", applySimilar: false });
export function classifyProblem(f: ClassifyForm, t: BankTxn): string | null {
  if (!f.account) return "Choose the account this line belongs to";
  if (f.remember === "counterparty" && !t.counterparty_key) return "This line has no counterparty to remember; remember a word instead";
  if (f.remember === "keyword") {
    if (!f.keyword.trim()) return "Type the word from the narration to remember";
    if (!(t.description ?? "").toLowerCase().includes(f.keyword.trim().toLowerCase())) return "That word is not in this line's narration";
  }
  return null;
}
export function classifyBody(f: ClassifyForm) {
  return { account_id: f.account, ...(f.memo.trim() ? { memo: f.memo.trim() } : {}),
           ...(f.remember !== "no" ? { remember: f.remember, ...(f.remember === "keyword" ? { keyword: f.keyword.trim() } : {}), ...(f.applySimilar ? { apply_to_similar: true } : {}) } : {}) };
}
export function ruleText(r: BankRule): string {
  const who = r.direction === "debit" ? "Payments" : r.direction === "credit" ? "Receipts" : "Lines";
  const bits = [r.counterparty_key ? `to or from ${r.counterparty_key}` : "", r.keyword ? `mentioning “${r.keyword}”` : "", r.mode ? `by ${(MODE_LABEL[r.mode] || r.mode).toLowerCase()}` : ""].filter(Boolean);
  return `${who} ${bits.join(" ")}`;
}
