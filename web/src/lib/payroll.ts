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
