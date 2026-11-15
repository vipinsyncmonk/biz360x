// Year-end tax declarations: the words for them and the form's checks. The server decides everything (the limits, what counts, the tax);
// this stops useless calls and says where a declaration stands. The sections and limits are a starter library a CA must review.
import { formatInr } from "./format";
import type { Tone } from "./compliance";

export interface TaxSection { code: string; label: string; annual_cap: string | null; old_regime_only: boolean; note: string | null }
export interface TaxItem { id: string; section: string; description: string; declared_amount: string; proof_ref: string | null; status: "pending" | "verified" | "rejected"; verified_amount: string | null; hr_note: string | null }
export interface TaxSummary { section: string; declared: string; verified: string; cap: string | null; counts: string }
export type DeclStatus = "none" | "draft" | "submitted" | "verified" | "returned";
export interface TaxDecl {
  fy: string; regime: "new" | "old"; status: DeclStatus; id: string | null; submitted_at: string | null; verified_at: string | null; hr_note: string | null; editable: boolean;
  sections: TaxSection[]; items: TaxItem[]; summary: TaxSummary[]; note: string; employee_id?: string; employee_code?: string; employee_name?: string;
}
export interface DeclRow { id: string; employee_id: string; employee_code: string; employee_name: string; regime: "new" | "old"; status: Exclude<DeclStatus, "none">; items: number; declared: string; submitted_at: string | null; verified_at: string | null }
export interface Form16 {
  fy: string; assessment_year: string; regime: "new" | "old"; employee_name: string; months: number; gross_salary: string; standard_deduction: string; chapter_vi_a: Array<{ section: string; label: string; amount: string }>;
  chapter_vi_a_total: string; taxable_income: string; tax_with_cess: string; tds_deducted: string; difference: string; deductions_verified: boolean; note: string;
}

export const DECL_STATUS: Record<DeclStatus, { label: string; tone: Tone; hint: string }> = {
  none: { label: "Not started", tone: "prep", hint: "You have not made a declaration for this year." },
  draft: { label: "Draft", tone: "prep", hint: "Saved, not sent. HR sees it only once you send it." },
  submitted: { label: "With HR", tone: "wait", hint: "Waiting for HR to verify each figure. You cannot change it meanwhile." },
  verified: { label: "Verified", tone: "ok", hint: "HR has verified it. Your TDS uses the verified figures from the next payroll." },
  returned: { label: "Returned", tone: "due", hint: "HR sent it back. Change what they asked for and send it again." },
};

export function regimeLabel(r: string): string { return r === "old" ? "Old regime (with deductions)" : "New regime (no deductions)"; }

/** The financial year a date falls in, as 2026-27, and the one before it. */
export function fyOf(d: Date): string {
  const start = d.getMonth() >= 3 ? d.getFullYear() : d.getFullYear() - 1;
  return `${start}-${String((start + 1) % 100).padStart(2, "0")}`;
}
export function fyOptions(today: Date = new Date()): string[] {
  const cur = fyOf(today);
  const prev = Number(cur.slice(0, 4)) - 1;
  return [cur, `${prev}-${String(Number(cur.slice(0, 4)) % 100).padStart(2, "0")}`];
}

export interface ItemDraft { section: string; description: string; amount: string; proof: string }
export const blankItem = (section = "80C"): ItemDraft => ({ section, description: "", amount: "", proof: "" });
export function draftFromDecl(d: TaxDecl): { regime: "new" | "old"; items: ItemDraft[] } {
  return { regime: d.regime, items: d.items.map((i) => ({ section: i.section, description: i.description, amount: i.declared_amount, proof: i.proof_ref ?? "" })) };
}

/** What is wrong with one line of the form, or null. */
export function itemProblem(i: ItemDraft): string | null {
  if (!i.description.trim()) return "Say what it is, for example PPF or health insurance.";
  if (!/^\d{1,12}(\.\d{1,2})?$/.test(i.amount.trim())) return "Enter the amount in rupees, like 50000 or 12500.50.";
  return null;
}
export function formProblem(items: ItemDraft[]): string | null {
  for (const i of items) { const p = itemProblem(i); if (p) return p; }
  return null;
}
export function declBody(regime: "new" | "old", items: ItemDraft[]) {
  return { regime, items: items.map((i) => ({ section: i.section, description: i.description.trim(), declared_amount: i.amount.trim(), proof_ref: i.proof.trim() || null })) };
}

export function capText(s: { annual_cap: string | null }): string { return s.annual_cap ? `up to ${formatInr(s.annual_cap, { decimals: false })} a year` : "no limit"; }
export function itemLine(i: TaxItem): string {
  if (i.status === "verified") return i.verified_amount === i.declared_amount ? `Verified ${formatInr(i.verified_amount)}` : `Verified ${formatInr(i.verified_amount)} of ${formatInr(i.declared_amount)}`;
  if (i.status === "rejected") return `Not accepted${i.hr_note ? `: ${i.hr_note}` : ""}`;
  return "Not verified yet";
}

export function form16Rows(f: Form16): Array<[string, string]> {
  const rows: Array<[string, string]> = [["Salary for the year", formatInr(f.gross_salary)], ["Standard deduction", `− ${formatInr(f.standard_deduction)}`]];
  for (const d of f.chapter_vi_a) rows.push([d.label, `− ${formatInr(d.amount)}`]);
  rows.push(["Taxable income", formatInr(f.taxable_income)], ["Tax with cess", formatInr(f.tax_with_cess)], ["TDS deducted by the company", formatInr(f.tds_deducted)]);
  const diff = Number(f.difference);
  rows.push([diff > 0 ? "Still to be paid" : diff < 0 ? "Deducted more than the tax" : "Tax and TDS match", formatInr(String(Math.abs(diff).toFixed(2)))]);
  return rows;
}
