// Employee self-service: what a person sees of their own record, the words for it, and the leave form. The server decides everything
// (whose record it is, the working days, the balance); this words it and stops useless calls.
import { ApiProblem, api, toProblem } from "./api";
import { balanceText, type Balance } from "./hr";

export interface Employment { employee_id: string; entity_id: string; legal_name: string; employee_code: string; full_name?: string; designation: string | null; status: string }
export interface EmploymentInvite { employee_id: string; entity_id: string; legal_name: string; employee_code: string; full_name: string; designation: string | null; invited_at: string }
export interface MyEmployments { employments: Employment[]; invitations: EmploymentInvite[] }

export interface MyEmployee {
  employee_id: string; entity_id: string; legal_name: string; employee_code: string; full_name: string; email: string | null; designation: string | null; department: string | null;
  date_of_joining: string; date_of_leaving: string | null; status: string; pan_last4: string | null; uan: string | null; bank_ifsc: string | null; bank_on_file: boolean; has_salary: boolean;
}
export interface MyTask { key: string; title: string; required: boolean; auto: string | null; done: boolean }
export interface MyChecklist { checklist: MyTask[]; open_required: string[]; done: number; total: number; to_acknowledge: number }
export interface MyRecord { employee: MyEmployee; onboarding: MyChecklist }

export interface MyLeaveRequest {
  id: string; leave_type: string; leave_type_name: string; from: string; to: string; days: string; lop_days: string; half_day: boolean; reason: string | null;
  status: "pending" | "approved" | "rejected" | "cancelled"; decision_note: string | null; can_cancel: boolean;
}
export interface MyLeave {
  as_of: string; leave_year_start: string; leave_year_end: string; week_off: number[]; unpaid_when_exhausted: boolean; can_apply: boolean; balances: Balance[]; requests: MyLeaveRequest[];
  holidays: Array<{ holiday_date: string; name: string }>;
}
export interface MySlip { id: string; period_month: string; gross: string; net_pay: string; lop_days: number; paid_on: string | null }
export interface MyDoc { id: string; title: string; category: string; version: number; requires_ack: boolean; published_at: string; acknowledged_on: string | null; body?: string }

/** Which employment to show: the one asked for if it is still mine, else the first. */
export function pickEmployment(list: Employment[], wanted: string | null): Employment | null {
  return list.find((e) => e.employee_id === wanted) ?? list[0] ?? null;
}

/** Where someone with no company workspace should land: their employee page if they have one, else setting a company up. */
export const homeWithoutWorkspace = (employments: number) => (employments > 0 ? "/self" : "/onboarding");

/** "Asha Rao · E1 · Engineer" under the company's name. */
export const employmentLine = (e: Pick<Employment, "employee_code" | "designation"> & { full_name?: string }) =>
  [e.full_name, e.employee_code, e.designation].filter(Boolean).join(" · ");

/** "Self Co Pvt Ltd has opened your employee page. You are on its records as Asha Rao (E1)." */
export const inviteLine = (i: EmploymentInvite) => `${i.legal_name} has opened your employee page. You are on its records as ${i.full_name} (${i.employee_code}).`;

export const STATUS_WORDS: Record<string, string> = { onboarding: "Joining", active: "Active", notice: "Serving notice", exited: "Left the company" };

// ------------------------------------------------------------------ what needs my attention
/** The things a person is asked to do, most pressing first. Nothing to do returns an empty list. */
export function myToDo(rec: MyRecord): string[] {
  const out: string[] = [];
  const n = rec.onboarding.to_acknowledge;
  if (n > 0) out.push(`Read and acknowledge ${n} ${n === 1 ? "policy" : "policies"}`);
  if (rec.employee.status === "onboarding") {
    if (!rec.employee.pan_last4) out.push("Give HR your PAN");
    if (!rec.employee.bank_on_file) out.push("Give HR your bank account for salary");
  }
  return out;
}

/** "2 of 5 done" and whether HR is still waiting on something required. */
export function checklistLine(c: MyChecklist): string {
  if (c.total === 0) return "No joining checklist";
  const n = c.open_required.length;
  return `${c.done} of ${c.total} done${n ? ` · ${n} required ${n === 1 ? "item" : "items"} left` : ""}`;
}

// ------------------------------------------------------------------ leave
export interface MyLeaveForm { type: string; from: string; to: string; halfDay: boolean; reason: string }
export const blankMyLeave = (type = ""): MyLeaveForm => ({ type, from: "", to: "", halfDay: false, reason: "" });

export function myLeaveProblem(f: MyLeaveForm, today: string): string | null {
  if (!f.type) return "Choose the kind of leave";
  if (!f.from) return "Enter the first day";
  const to = f.halfDay ? f.from : f.to;
  if (!to) return "Enter the last day";
  if (to < f.from) return "The last day cannot be before the first";
  if (to < today) return "That leave is already over. Ask HR to record leave you have taken.";
  return null;
}

export const myLeaveBody = (f: MyLeaveForm) => ({ leave_type: f.type, from: f.from, to: f.halfDay ? f.from : f.to, half_day: f.halfDay, reason: f.reason.trim() || null });

/** Why the request was refused, in the person's words. */
export function leaveRefusal(code: string | undefined, fallback: string): string {
  if (code === "leave_balance_short") return "You do not have enough of this leave left, and your company's policy does not allow unpaid leave beyond the balance.";
  if (code === "conflict") return "You already have leave on some of those days.";
  return fallback;
}

/** One line per leave type for the balance cards: the name, then "5 left of 7". */
export const balanceLine = (b: Balance) => `${b.name}: ${balanceText(b)}`;

/** The next holidays from today, at most `n`. */
export function upcomingHolidays(holidays: MyLeave["holidays"], today: string, n = 5): MyLeave["holidays"] {
  return holidays.filter((h) => h.holiday_date >= today).sort((a, b) => a.holiday_date.localeCompare(b.holiday_date)).slice(0, n);
}

// ------------------------------------------------------------------ the handbook
export function docState(d: Pick<MyDoc, "requires_ack" | "acknowledged_on">, shortDate: (iso: string) => string): { text: string; todo: boolean } {
  if (d.acknowledged_on) return { text: `Acknowledged on ${shortDate(d.acknowledged_on)}`, todo: false };
  return d.requires_ack ? { text: "Waiting for you to acknowledge", todo: true } : { text: "For reading", todo: false };
}

// ------------------------------------------------------------------ payslip PDF (a plain link cannot carry the session token)
export async function openMyPayslipPdf(employeeId: string, payslipId: string): Promise<void> {
  const tab = window.open("", "_blank");
  try {
    const { data, error, response } = await api.GET("/self/{employeeId}/payslips/{id}/pdf", { params: { path: { employeeId, id: payslipId }, query: { download: false } }, parseAs: "blob" });
    if (error !== undefined || !data) throw response ? await toProblem(response) : new ApiProblem(0, "network", "Could not reach the server");
    const url = URL.createObjectURL(data as Blob);
    if (tab) tab.location.href = url; else window.location.href = url;
    setTimeout(() => URL.revokeObjectURL(url), 5 * 60_000);
  } catch (e) {
    tab?.close();
    throw e;
  }
}

// ------------------------------------------------------------------ HR's side: opening it for a person
export type SelfService = "off" | "invited" | "active";
export const SELF_SERVICE: Record<SelfService, { label: string; hint: string }> = {
  off: { label: "Not opened", hint: "They cannot sign in" },
  invited: { label: "Invited", hint: "Waiting for them to sign in and accept" },
  active: { label: "Active", hint: "They see their own payslips, leave and policies" },
};
/** What the button does next, or why it cannot. */
export function selfServiceAction(p: { self_service: SelfService; email: string | null; status: string }): { label: string; open: boolean } | { blocked: string } {
  if (p.self_service !== "off") return { label: p.self_service === "active" ? "End access" : "Withdraw", open: false };
  if (p.status === "exited") return { blocked: "Has left the company" };
  if (!p.email) return { blocked: "Add their email first" };
  return { label: "Open sign-in", open: true };
}
