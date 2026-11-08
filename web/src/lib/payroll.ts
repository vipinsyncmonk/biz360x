/** Pure helpers for the Payroll & HR screen. Pay, PF, ESI, PT and TDS are computed by the server; nothing here calculates them. */
import type { Tone } from "./compliance";

export interface Run { id: string; period_month: string; status: string; headcount: number; gross_total: string; net_total: string; employer_cost_total: string; approval_request_id: string | null; paid_on: string | null; statutory: Array<{ kind: string; amount: string; due_date: string; obligation_id: string | null }> }
export interface Slip { id: string; employee_id: string; employee_name: string; paid_days: number; lop_days: number; gross: string; wages: string; pf_employee: string; esi_employee: string; esi_applicable: boolean; professional_tax: string; tds: string; net_pay: string; flags: string[] }
export interface Labour {
  headcount: number;
  wage_code: { flagged: number; employees: number; status: string };
  gratuity: { applies: boolean; liability_estimate: string; status: string };
  statutory_bonus: { eligible: number; accrued_employees: number; accrued_total: string; pay_by: string | null };
  posh: { required: boolean; constituted: boolean; members: number; has_external_member: boolean; status: string };
  shops_establishment: { registered: boolean; valid_until: string | null; status: string };
}

const MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"];

/** "2026-10-01" -> "October 2026" */
export function runName(periodMonth: string): string {
  const [y, m] = periodMonth.split("-");
  return `${MONTHS[Number(m) - 1] ?? ""} ${y}`;
}

/** The first day of the month after the newest run, or of the current month when there is no run yet. */
export function nextRunMonth(runs: Run[], today: Date = new Date()): string {
  const newest = runs.map((r) => r.period_month).sort().pop();
  const [y, m] = newest ? newest.split("-").map(Number) as [number, number] : [today.getFullYear(), today.getMonth()];
  const ny = m === 12 ? y + 1 : y, nm = m === 12 ? 1 : m + 1;
  return `${ny}-${String(nm).padStart(2, "0")}-01`;
}

export const RUN_STATUS: Record<string, { label: string; tone: Tone }> = {
  inputs_open: { label: "Inputs open", tone: "prep" }, inputs_locked: { label: "Inputs locked", tone: "prep" }, computed: { label: "Computed", tone: "wait" },
  approved: { label: "Approved", tone: "ok" }, bank_file_sent: { label: "Bank file sent", tone: "ok" }, paid: { label: "Paid", tone: "ok" }, statutory_done: { label: "Statutory done", tone: "ok" },
};

export const STEPS: Array<[string, string]> = [["inputs_open", "Inputs"], ["computed", "Computed"], ["approved", "Approved"], ["bank_file_sent", "Bank file"], ["paid", "Paid"], ["statutory_done", "Deposits"]];

/** How far through the run is: 0-based index of the current step. inputs_locked counts as the inputs step. */
export function stepIndex(status: string): number {
  const i = STEPS.findIndex(([k]) => k === status);
  return i >= 0 ? i : 0;
}

/** What the person can do next. Approval itself is a second person's job, so a computed run is only submitted. */
export function nextAction(r: Pick<Run, "status" | "approval_request_id">): "compute" | "submit" | "waiting" | null {
  if (r.status === "inputs_open" || r.status === "inputs_locked") return "compute";
  if (r.status === "computed") return r.approval_request_id ? "waiting" : "submit";
  return null;
}

export const WAGE_FLAG = "wage_below_50pct";

export const LABOUR_STATUS: Record<string, { label: string; tone: Tone }> = {
  ok: { label: "On track", tone: "ok" }, fix: { label: "Needs a fix", tone: "due" }, not_applicable: { label: "Not applicable", tone: "prep" },
  provided: { label: "Provided", tone: "ok" }, valuation_needed: { label: "Valuation needed", tone: "due" }, constituted: { label: "Constituted", tone: "ok" },
  action_needed: { label: "Action needed", tone: "due" }, missing: { label: "Not registered", tone: "due" }, expiring: { label: "Expiring", tone: "due" }, valid: { label: "Valid", tone: "ok" },
};

// ---- employees, salary structures and monthly inputs. The server validates and computes; these only stop useless calls.
import { toPaise } from "./format";

export interface Component { code: string; monthly_amount: string; kind: string; in_wages: boolean; taxable: boolean }
export interface Employee {
  id: string; employee_code: string; full_name: string; pan: string | null; uan: string | null; email: string | null; date_of_joining: string; date_of_leaving: string | null;
  designation: string | null; department: string | null; work_state: string; employment_type: string; tax_regime: string; status: string;
  bank_account: { account_last4: string; ifsc: string } | null;
  current_structure: { id: string; effective_from: string; ctc_annual: string; components: Component[] } | null;
}
export interface InputRow { employee_id: string; lop_days: string; overtime: string; bonus: string; reimbursements: string; other_deductions: string }

export const EMP_STATUS: Record<string, { label: string; tone: Tone }> = { onboarding: { label: "Onboarding", tone: "prep" }, active: { label: "Active", tone: "ok" }, notice: { label: "On notice", tone: "wait" }, exited: { label: "Exited", tone: "prep" } };
export const EMP_TYPES: Array<[string, string]> = [["permanent", "Permanent"], ["fixed_term", "Fixed term"], ["intern", "Intern"], ["apprentice", "Apprentice"], ["contract", "Contract"]];

export interface EmployeeForm { code: string; name: string; joined: string; state: string; regime: string; type: string; pan: string; uan: string; email: string; designation: string; department: string; account: string; ifsc: string }
export const blankEmployee = (): EmployeeForm => ({ code: "", name: "", joined: "", state: "", regime: "new", type: "permanent", pan: "", uan: "", email: "", designation: "", department: "", account: "", ifsc: "" });

const PAN = /^[A-Z]{5}[0-9]{4}[A-Z]$/, UAN = /^[0-9]{12}$/, STATE = /^[0-9]{2}$/, ACCT = /^[0-9]{6,18}$/, IFSC = /^[A-Z]{4}0[A-Z0-9]{6}$/;

/** The first thing wrong with the form, in words, or null. Bank details are all-or-nothing. */
export function employeeProblem(f: EmployeeForm): string | null {
  if (!f.code.trim()) return "Give the employee a code";
  if (!f.name.trim()) return "Give the employee a name";
  if (!f.joined) return "Choose the date of joining";
  if (!STATE.test(f.state.trim())) return "Work state is the two-digit GST state code, like 29";
  if (f.pan.trim() && !PAN.test(f.pan.trim().toUpperCase())) return "PAN looks like ABCDE1234F";
  if (f.uan.trim() && !UAN.test(f.uan.trim())) return "UAN is 12 digits";
  if ((f.account.trim() === "") !== (f.ifsc.trim() === "")) return "Give both the account number and the IFSC, or neither";
  if (f.account.trim() && !ACCT.test(f.account.trim())) return "Account number is 6 to 18 digits";
  if (f.ifsc.trim() && !IFSC.test(f.ifsc.trim().toUpperCase())) return "IFSC looks like HDFC0001234";
  return null;
}
export const employeeBody = (f: EmployeeForm) => ({
  employee_code: f.code.trim(), full_name: f.name.trim(), date_of_joining: f.joined, work_state: f.state.trim(), tax_regime: f.regime, employment_type: f.type,
  ...(f.pan.trim() ? { pan: f.pan.trim().toUpperCase() } : {}), ...(f.uan.trim() ? { uan: f.uan.trim() } : {}), ...(f.email.trim() ? { email: f.email.trim() } : {}),
  ...(f.designation.trim() ? { designation: f.designation.trim() } : {}), ...(f.department.trim() ? { department: f.department.trim() } : {}),
  ...(f.account.trim() ? { bank_account: { account_no: f.account.trim(), ifsc: f.ifsc.trim().toUpperCase() } } : {}),
});

export interface CompDraft { code: string; amount: string; kind: string; in_wages: boolean; taxable: boolean }
export const COMP_KINDS: Array<[string, string]> = [["earning", "Earning"], ["employer_contribution", "Employer contribution"], ["reimbursement", "Reimbursement"]];
export const defaultComponents = (): CompDraft[] => [
  { code: "BASIC", amount: "", kind: "earning", in_wages: true, taxable: true },
  { code: "HRA", amount: "", kind: "earning", in_wages: false, taxable: true },
  { code: "SPECIAL", amount: "", kind: "earning", in_wages: false, taxable: true },
];
const isAmount = (v: string) => /^\d+(\.\d{1,2})?$/.test(v.trim());
const paise = (p: bigint) => `${p / 100n}.${(p % 100n).toString().padStart(2, "0")}`;
/** Twelve months of what is entered, for display. The server checks it against the CTC. */
export function annualOf(comps: CompDraft[]): string {
  return paise(comps.reduce<bigint>((a, c) => a + (isAmount(c.amount) ? toPaise(c.amount) : 0n), 0n) * 12n);
}
export function structureProblem(from: string, comps: CompDraft[]): string | null {
  if (!from) return "Choose the date the new salary starts";
  if (comps.length === 0) return "Add at least one component";
  const codes = new Set<string>();
  for (const c of comps) {
    const code = c.code.trim().toUpperCase();
    if (!code) return "Every component needs a code";
    if (codes.has(code)) return `${code} is listed twice`;
    codes.add(code);
    if (!isAmount(c.amount)) return `${code}: enter the monthly amount, like 25000`;
  }
  if (!comps.some((c) => c.kind === "earning" && c.in_wages && isAmount(c.amount) && toPaise(c.amount) > 0n)) return "Basic pay must be an earning that counts as wages";
  return null;
}
export const structureBody = (from: string, comps: CompDraft[]) => ({
  effective_from: from, ctc_annual: annualOf(comps),
  components: comps.map((c) => ({ code: c.code.trim().toUpperCase(), monthly_amount: paise(toPaise(c.amount)), kind: c.kind, in_wages: c.in_wages, taxable: c.taxable })),
});
export const draftsFrom = (cs: Component[]): CompDraft[] => cs.map((c) => ({ code: c.code, amount: c.monthly_amount, kind: c.kind, in_wages: c.in_wages, taxable: c.taxable }));

export const INPUT_FIELDS: Array<[keyof Omit<InputRow, "employee_id">, string]> = [["lop_days", "Days without pay"], ["overtime", "Overtime ₹"], ["bonus", "Bonus ₹"], ["reimbursements", "Reimbursements ₹"], ["other_deductions", "Other deductions ₹"]];
export type InputDraft = Record<string, Partial<Omit<InputRow, "employee_id">>>;

export function inputProblem(field: string, v: string | undefined): string | null {
  if (v === undefined || v.trim() === "") return null;
  if (field === "lop_days") return /^\d{1,2}(\.\d)?$/.test(v.trim()) && Number(v) <= 31 ? null : "0 to 31 days";
  return isAmount(v) ? null : "An amount like 1500";
}
/** Rows for PUT, only for people with something entered; blanks and zeros are dropped. The call replaces the whole month. */
export function inputsBody(draft: InputDraft): InputRow[] {
  const out: InputRow[] = [];
  for (const [employee_id, d] of Object.entries(draft)) {
    const row: InputRow = { employee_id, lop_days: "0", overtime: "0", bonus: "0", reimbursements: "0", other_deductions: "0" };
    let any = false;
    for (const [k] of INPUT_FIELDS) {
      const v = (d[k] ?? "").trim();
      if (v === "" || Number(v) === 0) continue;
      row[k] = k === "lop_days" ? v : paise(toPaise(v));
      any = true;
    }
    if (any) out.push(row);
  }
  return out;
}
export const draftFromRows = (rows: InputRow[]): InputDraft =>
  Object.fromEntries(rows.map((r) => [r.employee_id, Object.fromEntries(INPUT_FIELDS.map(([k]) => [k, Number(r[k]) === 0 ? "" : r[k]]))]));
export const inputsProblem = (draft: InputDraft) => Object.values(draft).some((d) => INPUT_FIELDS.some(([k]) => inputProblem(k, d[k]) !== null));

/** What an employee earns in a month before deductions: the earning components added up exactly. */
export const monthlyEarnings = (cs: Component[]): string => paise(cs.filter((c) => c.kind === "earning").reduce<bigint>((a, c) => a + toPaise(c.monthly_amount), 0n));
