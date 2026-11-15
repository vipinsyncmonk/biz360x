import { describe, expect, it } from "vitest";
import { blankContact, cannotApprove, contactBody, contactForm, contactProblem, dueLine, fileSize, hasTeam, kindLabel, proofProblem, taskProblem } from "./people";

const sd = (iso: string) => iso;
const form = (o: Partial<ReturnType<typeof blankContact>> = {}) => ({ ...blankContact(), ...o });
const address = { line1: "12 Park Street", city: "Bengaluru", state: "29", pincode: "560001" };
const emergency = { name: "Sita Rao", relation: "Mother", mobile: "+919812345678" };

describe("address and emergency contact form", () => {
  it("may be empty, because both parts are optional", () => { expect(contactProblem(form())).toBeNull(); });
  it("wants the whole address or none of it", () => {
    expect(contactProblem(form({ line1: "12 Park Street" }))).toMatch(/whole address/);
    expect(contactProblem(form(address))).toBeNull();
    expect(contactProblem(form({ ...address, pincode: "56001" }))).toMatch(/six digits/);
  });
  it("wants the whole emergency contact or none, with a country code", () => {
    expect(contactProblem(form({ name: "Sita" }))).toMatch(/name, relation and mobile/);
    expect(contactProblem(form({ ...emergency, mobile: "9812345678" }))).toMatch(/country code/);
    expect(contactProblem(form(emergency))).toBeNull();
  });
  it("sends empty parts as null and round-trips what the server has", () => {
    expect(contactBody(form({ ...address, line2: "  " }))).toMatchObject({ address_line1: "12 Park Street", address_line2: null, emergency_name: null });
    const c = { address_line1: "a", address_line2: null, city: "c", state_code: "29", pincode: "560001", emergency_name: null, emergency_relation: null, emergency_mobile: null, updated_at: null };
    expect(contactForm(c)).toMatchObject({ line1: "a", line2: "", city: "c", state: "29" });
    expect(contactForm(null)).toEqual(blankContact());
  });
});

describe("proof upload", () => {
  const ok = { name: "aadhaar.PNG", size: 1000 };
  it("checks the file and the label", () => {
    expect(proofProblem(null, "x")).toMatch(/Choose/);
    expect(proofProblem({ ...ok, size: 0 }, "x")).toMatch(/empty/);
    expect(proofProblem({ ...ok, size: 5 * 1024 * 1024 + 1 }, "x")).toMatch(/5 MB/);
    expect(proofProblem({ name: "a.docx", size: 10 }, "x")).toMatch(/PDF, JPEG or PNG/);
    expect(proofProblem(ok, "  ")).toMatch(/what it is/);
    expect(proofProblem(ok, "Aadhaar card")).toBeNull();
  });
  it("words sizes and kinds", () => {
    expect(fileSize(100)).toBe("1 KB");
    expect(fileSize(2048)).toBe("2 KB");
    expect(fileSize(3 * 1024 * 1024)).toBe("3.0 MB");
    expect(kindLabel("tax")).toBe("Tax");
    expect(kindLabel("zzz")).toBe("zzz");
  });
});

describe("tasks", () => {
  it("says when a task is due and whether it is late", () => {
    expect(dueLine({ due_on: null, status: "open" }, "2026-11-15", sd)).toEqual({ text: "No date", late: false });
    expect(dueLine({ due_on: "2026-11-15", status: "open" }, "2026-11-15", sd)).toEqual({ text: "Due today", late: false });
    expect(dueLine({ due_on: "2026-11-10", status: "open" }, "2026-11-15", sd)).toEqual({ text: "Was due 2026-11-10", late: true });
    expect(dueLine({ due_on: "2026-11-10", status: "done" }, "2026-11-15", sd)).toEqual({ text: "Done", late: false });
  });
  it("needs a person and a title", () => {
    expect(taskProblem("x", "")).toMatch(/person/);
    expect(taskProblem(" ", "e1")).toMatch(/what/);
    expect(taskProblem("Send it", "e1")).toBeNull();
  });
});

describe("the team", () => {
  it("shows the team panel only to someone with a manager or reports", () => {
    expect(hasTeam(null)).toBe(false);
    expect(hasTeam({ manager: null, reports: [] })).toBe(false);
    expect(hasTeam({ manager: { id: "m", full_name: "M", designation: null }, reports: [] })).toBe(true);
  });
  it("explains why unpaid leave cannot be approved by a manager", () => {
    expect(cannotApprove({ status: "pending", mine_to_approve: false })).toMatch(/HR decides/);
    expect(cannotApprove({ status: "pending", mine_to_approve: true })).toBeNull();
    expect(cannotApprove({ status: "approved", mine_to_approve: false })).toBeNull();
  });
});
