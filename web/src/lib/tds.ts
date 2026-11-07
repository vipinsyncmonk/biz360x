/** TDS & advance tax screen. Rates, due dates and instalments come from the server; nothing here calculates tax. */
import type { Tone } from "./compliance";
import { sumMoney, toPaise } from "./format";

export interface Deduction { id: string; deductee_pan: string; section: string; deducted_on: string; base_amount: string; rate_pct: string | number; tds_amount: string; challan_id: string | null; tds_return_id: string | null }
export interface Challan { id: string; kind: string; period_label: string; amount: string; interest: string; penalty: string; paid_on: string | null; challan_ref: string | null; status: string }
export interface TdsReturn { id: string; form: string; fy: string; quarter: number; status: string; fvu_validated_at: string | null; token_no: string | null; filed_at: string | null }
export interface Instalment { instalment_no: number; due_date: string; cumulative_pct: string | number; estimated_tax: string; paid: string; challan_id: string | null }
export interface Receivable { id: string; section: string; amount: string; fy: string; quarter: number; seen_in_26as_at: string | null }

/** Indian financial year of a date: April to March, written "2026-27". */
export function fyOf(d: Date = new Date()): string {
  const y = d.getMonth() >= 3 ? d.getFullYear() : d.getFullYear() - 1;
  return `${y}-${String((y + 1) % 100).padStart(2, "0")}`;
}
/** This year and the two before it, newest first. */
export function fyChoices(d: Date = new Date()): string[] {
  const y = Number(fyOf(d).slice(0, 4));
  return [0, 1, 2].map((i) => `${y - i}-${String((y - i + 1) % 100).padStart(2, "0")}`);
}

export const CHALLAN_KINDS: Array<[string, string]> = [
  ["itns281_tds", "TDS (ITNS 281)"], ["itns280_income_tax", "Income tax (ITNS 280)"], ["gst_pmt06", "GST (PMT-06)"], ["epf_ecr", "Provident fund"],
  ["esic", "ESIC"], ["professional_tax", "Professional tax"], ["other", "Other"],
];
export const CHALLAN_LABEL: Record<string, string> = Object.fromEntries(CHALLAN_KINDS);
export const CHALLAN_STATUS: Record<string, { label: string; tone: Tone }> = {
  draft: { label: "Draft", tone: "prep" }, scheduled: { label: "Ready to pay", tone: "wait" }, paid: { label: "Paid", tone: "ok" }, failed: { label: "Failed", tone: "due" },
};
export const RETURN_STATE: Record<string, { label: string; tone: Tone }> = {
  drafting: { label: "Drafting", tone: "prep" }, ready: { label: "Validated, ready to file", tone: "wait" }, filed: { label: "Filed", tone: "ok" }, revised: { label: "Revised", tone: "ok" },
};
export const quarterLabel = (q: number) => `Q${q}`;
export const canValidate = (status: string) => status !== "filed" && status !== "revised";
export const canPay = (status: string) => status !== "paid";

export interface ChallanForm { kind: string; period: string; amount: string; interest: string; penalty: string }
export const blankChallan = (): ChallanForm => ({ kind: "itns281_tds", period: "", amount: "", interest: "", penalty: "" });
const MONEY = /^\d{1,13}(\.\d{1,2})?$/;
export function challanProblem(f: ChallanForm): string | null {
  if (!f.period.trim()) return "Say which period it is for, such as Oct 2026";
  if (!MONEY.test(f.amount.trim()) || toPaise(f.amount.trim()) <= 0n) return "Enter the amount as a number above zero, such as 12500.50";
  if (f.interest.trim() && !MONEY.test(f.interest.trim())) return "Interest must be a number, such as 120.00";
  if (f.penalty.trim() && !MONEY.test(f.penalty.trim())) return "Penalty must be a number, such as 200.00";
  return null;
}
const two = (v: string) => { const p = toPaise(v.trim()); return `${p / 100n}.${(p % 100n).toString().padStart(2, "0")}`; };
export const challanBody = (f: ChallanForm) => ({
  kind: f.kind, period_label: f.period.trim(), amount: two(f.amount),
  ...(f.interest.trim() ? { interest: two(f.interest) } : {}), ...(f.penalty.trim() ? { penalty: two(f.penalty) } : {}),
});

/** Deducted but not yet on a challan, and what has been deducted in all. */
export function deductionTotals(rows: Deduction[]): { deducted: string; unpaid: string; unpaidCount: number } {
  const unpaid = rows.filter((r) => !r.challan_id);
  return { deducted: sumMoney(rows.map((r) => r.tds_amount)), unpaid: sumMoney(unpaid.map((r) => r.tds_amount)), unpaidCount: unpaid.length };
}

export function instalmentState(i: Pick<Instalment, "due_date" | "estimated_tax" | "paid">, today: string): { label: string; tone: Tone } {
  if (toPaise(i.estimated_tax) > 0n && toPaise(i.paid) >= toPaise(i.estimated_tax)) return { label: "Paid", tone: "ok" };
  if (i.due_date < today) return { label: "Past due", tone: "due" };
  return { label: "Upcoming", tone: "wait" };
}
/** The first instalment not yet covered, which is the one to plan for. */
export const nextInstalment = (rows: Instalment[], today: string): Instalment | null =>
  [...rows].sort((a, b) => a.instalment_no - b.instalment_no).find((i) => instalmentState(i, today).label !== "Paid" && toPaise(i.estimated_tax) > 0n) ?? null;

export const unmatched = (rows: Receivable[]) => rows.filter((r) => !r.seen_in_26as_at);

/** What a validation job found, in the words to show. A valid return gets a short confirmation. */
export function validationNote(result: Record<string, unknown> | null): { ok: boolean; lines: string[] } {
  if (!result) return { ok: false, lines: [] };
  if (result.valid === true) return { ok: true, lines: [`Validated. Token ${String(result.token ?? "")}`.trim()] };
  const errs = Array.isArray(result.errors) ? (result.errors as unknown[]).map(String) : [];
  return { ok: false, lines: errs.length ? errs.map(plainError) : ["The return did not validate."] };
}

export const DEPOSIT_RULE = "TDS.DEPOSIT.MONTHLY";
export interface DepositObl { id: string; status: string; period_label: string; due_date?: string | null; extended_due_date?: string | null; rule?: { code?: string } | null }
/** Months whose TDS deposit can still be prepared, earliest first. */
export function depositOptions(list: DepositObl[]): Array<{ id: string; label: string }> {
  const due = (o: DepositObl) => o.extended_due_date ?? o.due_date ?? "";
  return list
    .filter((o) => o.rule?.code === DEPOSIT_RULE && !["filed", "waived", "not_applicable"].includes(o.status))
    .sort((a, b) => due(a).localeCompare(due(b)))
    .map((o) => ({ id: o.id, label: `${o.period_label}${due(o) ? ` · due ${due(o).slice(8, 10)}/${due(o).slice(5, 7)}` : ""}` }));
}

/** What preparing a deposit did, from the job's result. */
export function depositNote(result: Record<string, unknown> | null, fmt: (v: string) => string): string {
  if (!result) return "Done.";
  if (typeof result.note === "string") return result.note;
  if (typeof result.amount === "string") {
    const n = Number(result.deductions ?? 0);
    return `Challan ready for ${fmt(result.amount)}${typeof result.interest === "string" && result.interest !== "0.00" ? ` plus ${fmt(result.interest)} late interest` : ""}, covering ${n} ${n === 1 ? "deduction" : "deductions"}.`;
  }
  return "Done.";
}

/** The server names a deduction by its id; say it plainly instead. */
export const plainError = (line: string) => line.replace(/^Deduction [0-9a-f-]{36} \(([^)]+)\)/i, "A $1 deduction");
