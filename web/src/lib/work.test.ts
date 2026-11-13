import { describe, expect, it } from "vitest";
import { blankTask, choices, dueText, elsewhereText, matrixBody, personLabel, queueLine, sameMatrix, setSlot, slotUser, splitWork, taskBody, taskProblem, type Person, type Slot, type WorkItem } from "./work";

const TODAY = "2026-11-14";
const item = (p: Partial<WorkItem>): WorkItem => ({ kind: "filing", id: "1", entity_id: "e1", entity_name: "Acme Pvt Ltd", title: "GSTR-3B · Oct 2026", detail: null, due_on: "2026-11-20", overdue: false, href: "/compliance", ...p });
const people: Person[] = [{ user_id: "u1", name: "Anil Accounts", title: "Accountant", approves: [] }, { user_id: "u2", name: "Chitra Finance", title: null, approves: ["gst", "payroll_run"] }];

describe("the work queue", () => {
  it("says when something is due the way a person would", () => {
    expect(dueText("2026-11-11", TODAY)).toBe("Overdue by 3 days");
    expect(dueText("2026-11-13", TODAY)).toBe("Overdue by 1 day");
    expect(dueText("2026-11-14", TODAY)).toBe("Due today");
    expect(dueText("2026-11-15", TODAY)).toBe("Due tomorrow");
    expect(dueText("2026-12-07", TODAY)).toBe("Due 7 Dec");
    expect(dueText("2027-01-01", "2026-12-31")).toBe("Due tomorrow");                       // across a year end
    expect(dueText(null, TODAY)).toBe("No date");
  });
  it("keeps this company's work apart from the rest, and approvals for their own card", () => {
    const items = [item({ id: "a" }), item({ id: "b", kind: "approval" }), item({ id: "c", kind: "task", entity_id: "e2", entity_name: "Beta LLP" }), item({ id: "d", kind: "approval", entity_id: "e2", entity_name: "Beta LLP" }),
      item({ id: "e", entity_id: "e3", entity_name: "Gamma Ltd" })];
    const { here, elsewhere } = splitWork(items, "e1");
    expect(here.map((i) => i.id)).toEqual(["a"]);
    expect(elsewhere.map((i) => i.id)).toEqual(["c", "d", "e"]);
    expect(elsewhereText(elsewhere)).toBe("2 things in Beta LLP, 1 in Gamma Ltd");
    expect(elsewhereText([item({ entity_name: "Beta LLP" })])).toBe("1 thing in Beta LLP");
  });
  it("sums up the queue in a line", () => {
    expect(queueLine([])).toBe("Nothing is waiting on you here.");
    expect(queueLine([item({})])).toBe("1 thing to do");
    expect(queueLine([item({}), item({ overdue: true }), item({ overdue: true })])).toBe("3 things to do, 2 overdue");
  });
});

describe("who does what", () => {
  it("names one person for a step and clears it again", () => {
    let data: Slot[] = [];
    data = setSlot(data, "gst", "prepare", "u1");
    data = setSlot(data, "gst", "approve", "u2");
    data = setSlot(data, "gst", "prepare", "u2");                                            // replaced, not added
    expect(data).toHaveLength(2);
    expect(slotUser(data, "gst", "prepare")).toBe("u2");
    expect(slotUser(data, "tds", "prepare")).toBe("");
    expect(setSlot(data, "gst", "approve", "")).toEqual([{ work_kind: "gst", step: "prepare", user_id: "u2" }]);
  });
  it("offers anyone to prepare and only those who can approve that work to approve", () => {
    expect(choices(people, "gst", "prepare").map((p) => p.user_id)).toEqual(["u1", "u2"]);
    expect(choices(people, "gst", "approve").map((p) => p.user_id)).toEqual(["u2"]);
    expect(choices(people, "leave", "approve")).toEqual([]);
    expect(personLabel(people[0]!)).toBe("Anil Accounts · Accountant");
    expect(personLabel(people[1]!)).toBe("Chitra Finance");
  });
  it("sends only the slots, and knows when nothing changed", () => {
    const data: Slot[] = [{ work_kind: "gst", step: "prepare", user_id: "u1", name: "Anil Accounts" }];
    expect(matrixBody(data)).toEqual({ data: [{ work_kind: "gst", step: "prepare", user_id: "u1" }] });
    expect(sameMatrix(data, [{ work_kind: "gst", step: "prepare", user_id: "u1" }])).toBe(true);
    expect(sameMatrix(data, setSlot(data, "gst", "prepare", "u2"))).toBe(false);
    expect(sameMatrix([], [])).toBe(true);
  });
});

describe("a new task", () => {
  it("needs only a title", () => {
    expect(taskProblem(blankTask())).toMatch(/what needs doing/i);
    expect(taskProblem({ ...blankTask(), title: "x".repeat(201) })).toMatch(/200/);
    expect(taskProblem({ ...blankTask(), title: "Renew the licence" })).toBeNull();
    expect(taskBody({ title: " Renew the licence ", assignee: "", due: "" })).toEqual({ title: "Renew the licence", assignee_id: null, due_on: null });
    expect(taskBody({ title: "Renew", assignee: "u1", due: "2026-11-20" })).toEqual({ title: "Renew", assignee_id: "u1", due_on: "2026-11-20" });
  });
});
