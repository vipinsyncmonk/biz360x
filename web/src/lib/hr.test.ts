import { describe, expect, it } from "vitest";
import {
  ackText, balanceText, blankHire, blankLeave, categoryLabel, checklistKey, checklistLine, componentLabel, ctcIsValid, daysText, docProblem, hireBody, hireProblem, leaveBody, leaveProblem,
  peopleText, policyBody, policyProblem, readinessLine, weekOffText, type Balance, type HireForm, type LeaveForm, type Policy, type Readiness,
} from "./hr";

const policy = (): Policy => ({
  effective_from: null, is_default: true, next_change_on: null,
  salary: { basic_pct_of_ctc: "50", hra_pct_of_basic: "40", employer_pf_in_ctc: true, pf_on_full_basic: false, employer_esi_in_ctc: true, gratuity_in_ctc: false },
  leave: { leave_year: "financial", week_off: [6, 5], lop_when_exhausted: true },
  payroll: { inputs_cutoff_day: 25, pay_day: 30, probation_months: 6, notice_days: 30 },
  onboarding: { checklist: [{ key: "offer", title: " Offer letter signed ", required: true, auto: null }, { key: "pan", title: "PAN on file", required: true, auto: "pan" }] },
  leave_types: [],
});
const hire = (p: Partial<HireForm> = {}): HireForm => ({ ...blankHire(), name: "Asha Rao", joining: "2026-12-01", state: "29", ctc: "1200000", ...p });
const leave = (p: Partial<LeaveForm> = {}): LeaveForm => ({ ...blankLeave("emp-1", "CL"), from: "2026-11-16", to: "2026-11-18", ...p });
const ready = (p: Partial<Readiness> = {}): Readiness => ({ ready: true, headcount: 4, blockers: [], warnings: [], joiners: [], leavers: [], lop_from_leave: [], ...p });
const who = (n: number) => Array.from({ length: n }, (_, i) => ({ employee_id: `e${i}`, employee_code: `EMP-${i}`, name: `Person ${i + 1}` }));

describe("the HR policy", () => {
  it("is sent without what the server added, with the weekly off in order and titles trimmed", () => {
    expect(policyProblem(policy())).toBeNull();
    const body = policyBody(policy(), "2026-11-12");
    expect(body.effective_from).toBe("2026-11-12");
    expect(body.leave.week_off).toEqual([5, 6]);
    expect(body.onboarding.checklist[0]).toEqual({ key: "offer", title: "Offer letter signed", required: true, auto: null });
    expect(Object.keys(body).sort()).toEqual(["effective_from", "leave", "onboarding", "payroll", "salary"]);
  });
  it("explains a policy that cannot be saved", () => {
    const p = policy();
    expect(policyProblem({ ...p, salary: { ...p.salary, basic_pct_of_ctc: "0" } })).toMatch(/Basic/);
    expect(policyProblem({ ...p, salary: { ...p.salary, hra_pct_of_basic: "140" } })).toMatch(/HRA/);
    expect(policyProblem({ ...p, leave: { ...p.leave, week_off: [0, 1, 2, 3, 4, 5, 6] } })).toMatch(/working day/);
    expect(policyProblem({ ...p, payroll: { ...p.payroll, pay_day: 32 } })).toMatch(/pay day/);
    expect(policyProblem({ ...p, onboarding: { checklist: [] } })).toMatch(/checklist/);
    expect(policyProblem({ ...p, onboarding: { checklist: [{ key: "x", title: " ", required: true, auto: null }] } })).toMatch(/title/);
  });
  it("names the weekly off and makes a key for a new checklist item", () => {
    expect(weekOffText([6, 5])).toBe("Saturday and Sunday");
    expect(weekOffText([])).toBe("No weekly off");
    expect(checklistKey("Laptop & ID card issued", ["offer"])).toBe("laptop_id_card_issued");
    expect(checklistKey("Offer", ["offer", "offer_2"])).toBe("offer_3");
    expect(checklistKey("!!!", [])).toBe("item");
  });
});

describe("a new hire", () => {
  it("needs only a name, a joining date, a state and a CTC, and sends the CTC with two decimals", () => {
    expect(hireProblem(hire())).toBeNull();
    expect(hireBody(hire({ pan: "abcde1234f" }))).toEqual({ full_name: "Asha Rao", employee_code: null, date_of_joining: "2026-12-01", work_state: "29", ctc_annual: "1200000.00", designation: null,
      department: null, email: null, pan: "ABCDE1234F", bank_account: null });
    expect(hireBody(hire({ accountNo: "501009876543", ifsc: "hdfc0000123" })).bank_account).toEqual({ account_no: "501009876543", ifsc: "HDFC0000123" });
  });
  it("explains what is missing or wrong, one thing at a time", () => {
    expect(hireProblem(hire({ name: " " }))).toMatch(/name/);
    expect(hireProblem(hire({ joining: "" }))).toMatch(/joining/);
    expect(hireProblem(hire({ state: "" }))).toMatch(/state/);
    expect(hireProblem(hire({ ctc: "12,00,000" }))).toMatch(/CTC/);
    expect(hireProblem(hire({ ctc: "0" }))).toMatch(/CTC/);
    expect(hireProblem(hire({ pan: "ABC" }))).toMatch(/PAN/);
    expect(hireProblem(hire({ email: "asha" }))).toMatch(/email/);
    expect(hireProblem(hire({ accountNo: "123" }))).toMatch(/account/);
    expect(hireProblem(hire({ accountNo: "501009876543", ifsc: "HDFC" }))).toMatch(/IFSC/);
    expect(ctcIsValid("1200000.50")).toBe(true);
    expect(ctcIsValid("")).toBe(false);
  });
  it("says how far the checklist is and names the salary components", () => {
    expect(checklistLine({ done: 3, total: 8, open_required: ["PAN on file", "Bank account for salary"], ready: false })).toBe("3 of 8 done · 2 required items left");
    expect(checklistLine({ done: 4, total: 8, open_required: ["PAN on file"], ready: false })).toBe("4 of 8 done · 1 required item left");
    expect(checklistLine({ done: 5, total: 8, open_required: [], ready: true })).toBe("5 of 8 done · ready to activate");
    expect(componentLabel("EMPLOYER_PF")).toBe("Employer's PF");
    expect(componentLabel("NIGHT_SHIFT")).toBe("Night shift");
  });
});

describe("leave", () => {
  it("sends the range, and a half day as a single date", () => {
    expect(leaveProblem(leave())).toBeNull();
    expect(leaveBody(leave({ reason: " Family function " }))).toEqual({ employee_id: "emp-1", leave_type: "CL", from: "2026-11-16", to: "2026-11-18", half_day: false, reason: "Family function" });
    expect(leaveBody(leave({ halfDay: true }))).toMatchObject({ from: "2026-11-16", to: "2026-11-16", half_day: true, reason: null });
    expect(leaveProblem(leave({ halfDay: true, to: "" }))).toBeNull();
  });
  it("refuses a request that cannot be read", () => {
    expect(leaveProblem(leave({ employeeId: "" }))).toMatch(/person/);
    expect(leaveProblem(leave({ type: "" }))).toMatch(/kind/);
    expect(leaveProblem(leave({ from: "" }))).toMatch(/first day/);
    expect(leaveProblem(leave({ to: "" }))).toMatch(/last day/);
    expect(leaveProblem(leave({ to: "2026-11-10" }))).toMatch(/before/);
  });
  it("writes days and balances the way a person reads them", () => {
    expect(daysText({ days: "2", lop_days: "0" })).toBe("2 days");
    expect(daysText({ days: "1", lop_days: "0" })).toBe("1 day");
    expect(daysText({ days: "0.5", lop_days: "0" })).toBe("0.5 days");
    expect(daysText({ days: "10", lop_days: "3" })).toBe("10 days (3 unpaid)");
    expect(daysText({ days: "2", lop_days: "2" })).toBe("2 days, unpaid");
    const b = (p: Partial<Balance>): Balance => ({ leave_type: "CL", name: "Casual leave", paid: true, annual_quota: "7", accrual: "yearly", earned: "7", adjustments: "0", taken: "2", pending: "0", unpaid_taken: "0", available: "5", ...p });
    expect(balanceText(b({}))).toBe("5 left of 7");
    expect(balanceText(b({ pending: "1", available: "4" }))).toBe("4 left of 7 · 1 waiting");
    expect(balanceText(b({ adjustments: "3", available: "8" }))).toBe("8 left of 7 + 3");
    expect(balanceText(b({ paid: false, earned: null, available: null, unpaid_taken: "2" }))).toBe("2 taken");
  });
});

describe("payroll readiness and the handbook", () => {
  it("says in one line whether the run can be computed", () => {
    expect(readinessLine(ready())).toBe("Ready to compute");
    expect(readinessLine(ready({ warnings: [{ code: "no_pan", title: "No PAN", employees: who(1) }] }))).toBe("Ready to compute · 1 thing to look at first");
    expect(readinessLine(ready({ ready: false, blockers: [{ code: "no_salary", title: "No salary", employees: who(2) }] }))).toBe("1 thing stops this run from being computed");
    expect(readinessLine(ready({ ready: false, headcount: 0 }))).toMatch(/Nobody/);
    expect(peopleText(who(2))).toBe("Person 1, Person 2");
    expect(peopleText(who(5))).toBe("Person 1, Person 2, Person 3 and 2 more");
  });
  it("labels documents and their acknowledgements", () => {
    expect(categoryLabel("posh")).toBe("Prevention of sexual harassment");
    expect(categoryLabel("other")).toBe("other");
    expect(ackText({ requires_ack: true, acknowledged: 3, people: 12 })).toBe("3 of 12 acknowledged");
    expect(ackText({ requires_ack: false, acknowledged: 0, people: 12 })).toBe("For reading only");
    expect(docProblem(" ", "long enough body text here")).toMatch(/title/);
    expect(docProblem("Leave policy", "short")).toMatch(/Write/);
    expect(docProblem("Leave policy", "Earned leave is 15 days a year.")).toBeNull();
  });
});
