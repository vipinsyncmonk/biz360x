// HR: what the onboarding, leave and policy screens show and what their forms send. The server splits a CTC, counts leave and decides
// what a payroll run needs; nothing here calculates pay. Money and days stay strings.
import { api, toProblem, ApiProblem } from "./api";
import { moneyText } from "./assets";
import type { Tone } from "./compliance";
import type { Employee } from "./payroll";

// ------------------------------------------------------------------ policy
export interface LeaveType { id: string; code: string; name: string; annual_quota: string | null; accrual: "yearly" | "monthly"; paid: boolean; carry_forward_max: string; encashable: boolean; is_active: boolean }
export interface ChecklistItem { key: string; title: string; required: boolean; auto: "pan" | "bank" | "salary" | "handbook" | null }
export interface Policy {
  effective_from: string | null; is_default: boolean; next_change_on: string | null;
  salary: { basic_pct_of_ctc: string; hra_pct_of_basic: string; employer_pf_in_ctc: boolean; pf_on_full_basic: boolean; employer_esi_in_ctc: boolean; gratuity_in_ctc: boolean };
  leave: { leave_year: "financial" | "calendar"; week_off: number[]; lop_when_exhausted: boolean };
  payroll: { inputs_cutoff_day: number; pay_day: number; probation_months: number; notice_days: number };
  onboarding: { checklist: ChecklistItem[] };
  leave_types: LeaveType[];
}
export const WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"];

const PCT = /^\d{1,3}(\.\d{1,2})?$/;
const inRange = (v: string, lo: number, hi: number) => PCT.test(v.trim()) && Number(v) >= lo && Number(v) <= hi;

export function policyProblem(p: Policy): string | null {
  if (!inRange(p.salary.basic_pct_of_ctc, 1, 100)) return "Basic pay is a share of the CTC between 1 and 100";
  if (!inRange(p.salary.hra_pct_of_basic, 0, 100)) return "HRA is a share of basic between 0 and 100";
  if (p.leave.week_off.length > 6) return "Leave at least one working day in the week";
  for (const [v, what, lo, hi] of [[p.payroll.inputs_cutoff_day, "cut-off day", 1, 31], [p.payroll.pay_day, "pay day", 1, 31], [p.payroll.probation_months, "probation", 0, 24], [p.payroll.notice_days, "notice period", 0, 180]] as const)
    if (!Number.isInteger(v) || v < lo || v > hi) return `The ${what} must be between ${lo} and ${hi}`;
  if (p.onboarding.checklist.length === 0) return "Keep at least one item on the onboarding checklist";
  if (p.onboarding.checklist.some((c) => !c.title.trim())) return "Every checklist item needs a title";
  return null;
}

/** What is sent to save the policy from a date. Read-only fields the server added are left out. */
export function policyBody(p: Policy, effectiveFrom: string) {
  return { effective_from: effectiveFrom, salary: { ...p.salary, basic_pct_of_ctc: p.salary.basic_pct_of_ctc.trim(), hra_pct_of_basic: p.salary.hra_pct_of_basic.trim() },
    leave: { ...p.leave, week_off: [...p.leave.week_off].sort() }, payroll: p.payroll,
    onboarding: { checklist: p.onboarding.checklist.map((c) => ({ key: c.key, title: c.title.trim(), required: c.required, auto: c.auto })) } };
}

/** A key for a checklist item a person adds: lower-case letters, digits and underscores, unique in the list. */
export function checklistKey(title: string, taken: string[]): string {
  const base = title.toLowerCase().replace(/[^a-z0-9]+/g, "_").replace(/^_+|_+$/g, "").slice(0, 24) || "item";
  let key = base, n = 2;
  while (taken.includes(key)) key = `${base}_${n++}`;
  return key;
}

export const weekOffText = (days: number[]) => (days.length === 0 ? "No weekly off" : [...days].sort().map((d) => WEEKDAYS[d]).join(" and "));

// ------------------------------------------------------------------ CTC and onboarding
export interface CtcBreakup {
  ctc_annual: string; components: Array<{ code: string; monthly_amount: string; annual_amount: string; kind: string; in_wages: boolean }>;
  monthly_ctc: string; monthly_gross: string; employer_contributions: string; employee_pf: string; employee_esi: string; take_home_before_tax: string; wage_pct: number | null;
}
export interface Task { key: string; title: string; required: boolean; auto: string | null; done: boolean }
export interface Onboarding { employee: Employee; checklist: Task[]; open_required: string[]; ready: boolean; done: number; total: number }

export const COMPONENT_LABEL: Record<string, string> = { BASIC: "Basic pay", HRA: "House rent allowance", SPECIAL: "Special allowance", EMPLOYER_PF: "Employer's PF", EMPLOYER_ESI: "Employer's ESI", GRATUITY: "Gratuity" };
export const componentLabel = (code: string) => COMPONENT_LABEL[code] ?? code.replace(/_/g, " ").toLowerCase().replace(/^./, (c) => c.toUpperCase());

const MONEY = /^\d{1,12}(\.\d{1,2})?$/;
export const ctcIsValid = (v: string) => MONEY.test(v.trim()) && /[1-9]/.test(v);

export interface HireForm { name: string; code: string; joining: string; state: string; ctc: string; designation: string; department: string; email: string; pan: string; accountNo: string; ifsc: string }
export const blankHire = (): HireForm => ({ name: "", code: "", joining: "", state: "", ctc: "", designation: "", department: "", email: "", pan: "", accountNo: "", ifsc: "" });

export function hireProblem(f: HireForm): string | null {
  if (!f.name.trim()) return "Enter the person's name";
  if (!f.joining) return "Enter the joining date";
  if (!/^\d{2}$/.test(f.state)) return "Choose the state they work in";
  if (!ctcIsValid(f.ctc)) return "Enter the yearly CTC, such as 1200000";
  if (f.pan.trim() && !/^[A-Z]{5}\d{4}[A-Z]$/.test(f.pan.trim().toUpperCase())) return "A PAN looks like ABCDE1234F";
  if (f.email.trim() && !/^\S+@\S+\.\S+$/.test(f.email.trim())) return "That email address does not look right";
  if (f.accountNo.trim() || f.ifsc.trim()) {
    if (!/^\d{6,18}$/.test(f.accountNo.trim())) return "The bank account number is 6 to 18 digits";
    if (!/^[A-Z]{4}0[A-Z0-9]{6}$/.test(f.ifsc.trim().toUpperCase())) return "An IFSC looks like HDFC0000123";
  }
  return null;
}

export function hireBody(f: HireForm) {
  const t = (s: string) => (s.trim() ? s.trim() : null);
  return { full_name: f.name.trim(), employee_code: t(f.code), date_of_joining: f.joining, work_state: f.state, ctc_annual: moneyText(f.ctc), designation: t(f.designation),
    department: t(f.department), email: t(f.email), pan: t(f.pan)?.toUpperCase() ?? null,
    bank_account: f.accountNo.trim() ? { account_no: f.accountNo.trim(), ifsc: f.ifsc.trim().toUpperCase() } : null };
}

/** "3 of 8 done · 2 required items left" */
export function checklistLine(o: Pick<Onboarding, "done" | "total" | "open_required" | "ready">): string {
  if (o.ready) return `${o.done} of ${o.total} done · ready to activate`;
  const n = o.open_required.length;
  return `${o.done} of ${o.total} done · ${n} required ${n === 1 ? "item" : "items"} left`;
}

export const AUTO_HINT: Record<string, string> = { pan: "Ticks itself when the PAN is on file", bank: "Ticks itself when the bank account is on file", salary: "Ticks itself when a salary is set",
  handbook: "Ticks itself when every policy that needs it is acknowledged" };

// ------------------------------------------------------------------ leave
export interface LeaveRequest {
  id: string; employee_id: string; employee_name: string; employee_code: string; leave_type: string; leave_type_name: string; from: string; to: string; half_day: boolean; reason: string | null;
  days: string; lop_days: string; status: "pending" | "approved" | "rejected" | "cancelled"; decision_note: string | null; payroll_note?: string | null;
}
export interface Balance { leave_type: string; name: string; paid: boolean; annual_quota: string | null; accrual: string; earned: string | null; adjustments: string; taken: string; pending: string; unpaid_taken: string; available: string | null }
export interface Balances { as_of: string; leave_year_start: string; leave_year_end: string; data: Array<{ employee_id: string; employee_code: string; employee_name: string; balances: Balance[] }> }
export interface Holiday { id: string; date: string; name: string }

export const LEAVE_STATUS: Record<LeaveRequest["status"], { label: string; tone: Tone }> = {
  pending: { label: "Waiting", tone: "wait" }, approved: { label: "Approved", tone: "ok" }, rejected: { label: "Rejected", tone: "due" }, cancelled: { label: "Withdrawn", tone: "prep" },
};

export interface LeaveForm { employeeId: string; type: string; from: string; to: string; halfDay: boolean; reason: string }
export const blankLeave = (employeeId = "", type = ""): LeaveForm => ({ employeeId, type, from: "", to: "", halfDay: false, reason: "" });

export function leaveProblem(f: LeaveForm): string | null {
  if (!f.employeeId) return "Choose the person";
  if (!f.type) return "Choose the kind of leave";
  if (!f.from) return "Enter the first day";
  const to = f.halfDay ? f.from : f.to;
  if (!to) return "Enter the last day";
  if (to < f.from) return "The last day cannot be before the first";
  return null;
}

export const leaveBody = (f: LeaveForm) => ({ employee_id: f.employeeId, leave_type: f.type, from: f.from, to: f.halfDay ? f.from : f.to, half_day: f.halfDay, reason: f.reason.trim() || null });

const plural = (v: string) => (v === "1" ? "day" : "days");
/** "2 days", "0.5 day"… and what part of it is not paid: "3 days (1 unpaid)". */
export function daysText(r: Pick<LeaveRequest, "days" | "lop_days">): string {
  const unpaid = /[1-9]/.test(r.lop_days);
  if (unpaid && r.lop_days === r.days) return `${r.days} ${plural(r.days)}, unpaid`;
  return `${r.days} ${plural(r.days)}${unpaid ? ` (${r.lop_days} unpaid)` : ""}`;
}

/** "5 left of 7" for a paid type, "2 taken" for unpaid leave. */
export function balanceText(b: Balance): string {
  if (b.available === null) return `${b.unpaid_taken} taken`;
  const waiting = /[1-9]/.test(b.pending) ? ` · ${b.pending} waiting` : "";
  return `${b.available} left of ${b.earned}${/[1-9]/.test(b.adjustments) ? ` + ${b.adjustments}` : ""}${waiting}`;
}

// ------------------------------------------------------------------ payroll run readiness
export interface Person { employee_id: string; employee_code: string; name: string }
export interface ReadinessItem { code: string; title: string; employees: Person[] }
export interface Readiness { ready: boolean; headcount: number; blockers: ReadinessItem[]; warnings: ReadinessItem[]; joiners: Person[]; leavers: Person[]; lop_from_leave: Array<Person & { lop_days: string }> }

export function readinessLine(r: Readiness): string {
  if (r.blockers.length) return `${r.blockers.length} ${r.blockers.length === 1 ? "thing stops" : "things stop"} this run from being computed`;
  if (r.headcount === 0) return "Nobody is on payroll for this month yet";
  if (r.warnings.length) return `Ready to compute · ${r.warnings.length} ${r.warnings.length === 1 ? "thing" : "things"} to look at first`;
  return "Ready to compute";
}

export const peopleText = (people: Person[]) => (people.length <= 3 ? people.map((p) => p.name).join(", ") : `${people.slice(0, 3).map((p) => p.name).join(", ")} and ${people.length - 3} more`);

// ------------------------------------------------------------------ handbook
export interface PolicyDoc {
  id: string; slug: string; title: string; category: string; version: number; requires_ack: boolean; is_current: boolean; published_at: string; acknowledged: number; people: number;
  body?: string; acknowledgements?: Array<{ employee_id: string; employee_name: string; employee_code: string; acknowledged_on: string }>; waiting?: Array<{ employee_id: string; employee_name: string; employee_code: string }>;
}
export const DOC_CATEGORIES: Array<[string, string]> = [["general", "General"], ["leave", "Leave"], ["conduct", "Code of conduct"], ["posh", "Prevention of sexual harassment"], ["payroll", "Pay and benefits"],
  ["it_security", "IT and security"], ["travel", "Travel and expenses"], ["exit", "Leaving the company"]];
export const categoryLabel = (c: string) => DOC_CATEGORIES.find((x) => x[0] === c)?.[1] ?? c;
export const docProblem = (title: string, body: string) => (!title.trim() ? "Give the document a title" : body.trim().length < 20 ? "Write the policy: a few lines at least" : null);
export const ackText = (d: Pick<PolicyDoc, "requires_ack" | "acknowledged" | "people">) => (d.requires_ack ? `${d.acknowledged} of ${d.people} acknowledged` : "For reading only");

// ------------------------------------------------------------------ payslip PDF (a plain link cannot carry the session token)
export interface PersonSlip { id: string; period_month: string; gross: string; net_pay: string; lop_days: number; final: boolean }

export async function openPayslipPdf(entityId: string, payslipId: string): Promise<void> {
  const tab = window.open("", "_blank");
  try {
    const { data, error, response } = await api.GET("/entities/{entityId}/payslips/{id}/pdf", { params: { path: { entityId, id: payslipId }, query: { download: false } }, parseAs: "blob" });
    if (error !== undefined || !data) throw response ? await toProblem(response) : new ApiProblem(0, "network", "Could not reach the server");
    const url = URL.createObjectURL(data as Blob);
    if (tab) tab.location.href = url; else window.location.href = url;
    setTimeout(() => URL.revokeObjectURL(url), 5 * 60_000);
  } catch (e) {
    tab?.close();
    throw e;
  }
}
