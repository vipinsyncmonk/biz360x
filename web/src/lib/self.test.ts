import { describe, expect, it } from "vitest";
import {
  balanceLine, blankMyLeave, checklistLine, docState, employmentLine, homeWithoutWorkspace, inviteLine, leaveRefusal, myLeaveBody, myLeaveProblem, myToDo, pickEmployment, selfServiceAction,
  upcomingHolidays, type Employment, type MyRecord,
} from "./self";

const TODAY = "2026-11-14";
const short = (iso: string) => `${Number(iso.slice(8, 10))}/${Number(iso.slice(5, 7))}`;
const emp = (p: Partial<Employment> = {}): Employment => ({ employee_id: "e1", entity_id: "c1", legal_name: "Acme Pvt Ltd", employee_code: "E1", full_name: "Asha Rao", designation: "Engineer", status: "active", ...p });
const record = (employee: Partial<MyRecord["employee"]> = {}, onboarding: Partial<MyRecord["onboarding"]> = {}): MyRecord => ({
  employee: { employee_id: "e1", entity_id: "c1", legal_name: "Acme Pvt Ltd", employee_code: "E1", full_name: "Asha Rao", email: "asha@example.in", designation: null, department: null,
    date_of_joining: "2026-11-01", date_of_leaving: null, status: "active", pan_last4: "234K", uan: null, bank_ifsc: null, bank_on_file: true, has_salary: true, ...employee },
  onboarding: { checklist: [], open_required: [], done: 0, total: 0, to_acknowledge: 0, ...onboarding },
});

describe("which employment, and where to land", () => {
  it("keeps the one asked for while it is still mine, else the first", () => {
    const list = [emp(), emp({ employee_id: "e2", legal_name: "Beta LLP" })];
    expect(pickEmployment(list, "e2")?.legal_name).toBe("Beta LLP");
    expect(pickEmployment(list, "gone")?.employee_id).toBe("e1");
    expect(pickEmployment(list, null)?.employee_id).toBe("e1");
    expect(pickEmployment([], "e1")).toBeNull();
  });
  it("sends someone with no workspace to their employee page if they have one", () => {
    expect(homeWithoutWorkspace(1)).toBe("/self");
    expect(homeWithoutWorkspace(0)).toBe("/onboarding");
  });
  it("words an employment and an invitation", () => {
    expect(employmentLine(emp())).toBe("Asha Rao · E1 · Engineer");
    expect(employmentLine({ employee_code: "E1", designation: null })).toBe("E1");
    expect(inviteLine({ ...emp(), full_name: "Asha Rao", invited_at: "2026-11-14T10:00:00Z" })).toBe("Acme Pvt Ltd has opened your employee page. You are on its records as Asha Rao (E1).");
  });
});

describe("what needs my attention", () => {
  it("is empty when nothing does", () => {
    expect(myToDo(record())).toEqual([]);
  });
  it("lists policies to acknowledge, and what HR still needs from a new joiner", () => {
    expect(myToDo(record({}, { to_acknowledge: 1 }))).toEqual(["Read and acknowledge 1 policy"]);
    expect(myToDo(record({ status: "onboarding", pan_last4: null, bank_on_file: false }, { to_acknowledge: 2 })))
      .toEqual(["Read and acknowledge 2 policies", "Give HR your PAN", "Give HR your bank account for salary"]);
    expect(myToDo(record({ status: "active", pan_last4: null, bank_on_file: false }))).toEqual([]);          // only while joining
  });
  it("sums the checklist up", () => {
    expect(checklistLine(record().onboarding)).toBe("No joining checklist");
    expect(checklistLine({ checklist: [], open_required: ["PAN on file"], done: 2, total: 5, to_acknowledge: 0 })).toBe("2 of 5 done · 1 required item left");
    expect(checklistLine({ checklist: [], open_required: [], done: 5, total: 5, to_acknowledge: 0 })).toBe("5 of 5 done");
  });
});

describe("applying for leave", () => {
  const f = (p = {}) => ({ ...blankMyLeave("CL"), from: "2026-11-18", to: "2026-11-19", ...p });
  it("asks for what is missing, one thing at a time", () => {
    expect(myLeaveProblem(blankMyLeave(), TODAY)).toMatch(/kind of leave/);
    expect(myLeaveProblem(blankMyLeave("CL"), TODAY)).toMatch(/first day/);
    expect(myLeaveProblem(f({ to: "" }), TODAY)).toMatch(/last day/);
    expect(myLeaveProblem(f({ to: "2026-11-17" }), TODAY)).toMatch(/before the first/);
    expect(myLeaveProblem(f({ from: "2026-11-02", to: "2026-11-03" }), TODAY)).toMatch(/already over/);
    expect(myLeaveProblem(f(), TODAY)).toBeNull();
    expect(myLeaveProblem(f({ from: TODAY, to: TODAY }), TODAY)).toBeNull();                               // today is still allowed
    expect(myLeaveProblem(f({ halfDay: true, to: "" }), TODAY)).toBeNull();                                // half a day needs one date
  });
  it("sends the code, the dates and no employee: the server knows who is asking", () => {
    expect(myLeaveBody(f({ reason: "  Family function " }))).toEqual({ leave_type: "CL", from: "2026-11-18", to: "2026-11-19", half_day: false, reason: "Family function" });
    expect(myLeaveBody(f({ halfDay: true, to: "2026-11-25" }))).toEqual({ leave_type: "CL", from: "2026-11-18", to: "2026-11-18", half_day: true, reason: null });
    expect(myLeaveBody(f())).not.toHaveProperty("employee_id");
  });
  it("puts a refusal in words", () => {
    expect(leaveRefusal("leave_balance_short", "x")).toMatch(/not have enough/);
    expect(leaveRefusal("conflict", "x")).toMatch(/already have leave/);
    expect(leaveRefusal(undefined, "Could not reach the server")).toBe("Could not reach the server");
  });
  it("shows a balance and the holidays still to come", () => {
    expect(balanceLine({ leave_type: "CL", name: "Casual leave", paid: true, annual_quota: "7", accrual: "yearly", earned: "7", adjustments: "0", taken: "2", pending: "1", unpaid_taken: "0", available: "4" }))
      .toBe("Casual leave: 4 left of 7 · 1 waiting");
    const h = [{ holiday_date: "2026-12-25", name: "Christmas" }, { holiday_date: "2026-01-26", name: "Republic Day" }, { holiday_date: TODAY, name: "Today" }];
    expect(upcomingHolidays(h, TODAY).map((x) => x.name)).toEqual(["Today", "Christmas"]);
    expect(upcomingHolidays(h, TODAY, 1)).toHaveLength(1);
  });
});

describe("the handbook and HR's switch", () => {
  it("says where a document stands for me", () => {
    expect(docState({ requires_ack: true, acknowledged_on: null }, short)).toEqual({ text: "Waiting for you to acknowledge", todo: true });
    expect(docState({ requires_ack: true, acknowledged_on: "2026-11-03" }, short)).toEqual({ text: "Acknowledged on 3/11", todo: false });
    expect(docState({ requires_ack: false, acknowledged_on: null }, short)).toEqual({ text: "For reading", todo: false });
  });
  it("offers HR the next step, or says why there is none", () => {
    expect(selfServiceAction({ self_service: "off", email: "a@b.in", status: "active" })).toEqual({ label: "Open sign-in", open: true });
    expect(selfServiceAction({ self_service: "off", email: null, status: "active" })).toEqual({ blocked: "Add their email first" });
    expect(selfServiceAction({ self_service: "off", email: "a@b.in", status: "exited" })).toEqual({ blocked: "Has left the company" });
    expect(selfServiceAction({ self_service: "invited", email: "a@b.in", status: "active" })).toEqual({ label: "Withdraw", open: false });
    expect(selfServiceAction({ self_service: "active", email: "a@b.in", status: "exited" })).toEqual({ label: "End access", open: false });
  });
});
