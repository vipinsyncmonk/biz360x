import { describe, expect, it } from "vitest";
import { NAV } from "../components/nav";
import { accessBody, accessProblem, applyPreset, blankAccess, canOpen, canWrite, homeOf, homeTiles, levelOf, navFor, roleText, scopesText, setLevel, type Access, type Preset } from "./roles";

const MODULES = [{ key: "finance", label: "Finance and books" }, { key: "gst", label: "GST" }, { key: "payroll", label: "Payroll and salaries" }, { key: "hr", label: "HR" }, { key: "inventory", label: "Inventory" }];
const accountant: Access = { role: "maker", modules: ["finance", "gst", "inventory", "sales"], scopes: { finance: "write", gst: "write", inventory: "write", sales: "write" }, title: "Accountant", home: "finance" };
const owner: Access = { role: "owner", modules: ["*"], scopes: { "*": "file" }, title: null, home: "leadership" };
const hrOnly: Access = { role: "maker", modules: ["hr"], scopes: { hr: "write" }, title: "HR executive", home: "people" };
const titles = (a: Access, inFirm = false) => navFor(NAV, a, inFirm).flatMap((g) => g.items.map((i) => i.t));
const preset = (p: Partial<Preset>): Preset => ({ key: "accountant", kind: "member", title: "Accountant", about: "", owner: false, home: "finance", scopes: { finance: "write", gst: "write" }, ...p });

describe("what a person can open", () => {
  it("reads the module list, with * for everything", () => {
    expect(canOpen(accountant, "finance")).toBe(true);
    expect(canOpen(accountant, "payroll")).toBe(false);
    expect(canOpen(owner, "payroll")).toBe(true);
    expect(canOpen(null, "finance")).toBe(false);
  });
  it("ranks the level on a module, falling back to *", () => {
    expect(levelOf(accountant, "finance")).toBe(3);
    expect(levelOf(accountant, "payroll")).toBe(0);
    expect(levelOf(owner, "legal")).toBe(5);
    expect(levelOf({ scopes: { gst: "certify", sales: "prepare" } }, "gst")).toBe(5);       // a practitioner's words
    expect(canWrite(accountant, "gst")).toBe(true);
    expect(canWrite({ scopes: { gst: "read" } }, "gst")).toBe(false);
  });
});

describe("the sidebar", () => {
  it("shows an owner every screen, without the firm's group view", () => {
    const t = titles(owner);
    expect(t).toEqual(expect.arrayContaining(["Today", "Compliance", "Finance & Books", "Payroll & HR", "Inventory", "Settings", "All modules"]));
    expect(t).not.toContain("Group view");
    expect(t).not.toContain("HR");                                                          // Payroll & HR already covers it
    expect(titles(owner, true)).toContain("Group view");
  });
  it("leaves out what an accountant cannot open", () => {
    const t = titles(accountant);
    expect(t).toEqual(expect.arrayContaining(["Today", "Sales & Billing", "Finance & Books", "Fixed assets", "Inventory", "GST & Tax"]));
    for (const hidden of ["Payroll & HR", "HR", "Settings", "Startup", "Legal & Contracts", "Compliance", "Copilot"]) expect(t).not.toContain(hidden);
  });
  it("gives someone with only HR a way in that is not the payroll screen", () => {
    const t = titles(hrOnly);
    expect(t).toContain("HR");
    expect(t).not.toContain("Payroll & HR");
    expect(navFor(NAV, hrOnly, false).every((g) => g.items.length > 0)).toBe(true);        // no empty group headings
  });
});

describe("the home", () => {
  it("names the person by their title, else their level", () => {
    expect(roleText(accountant)).toBe("Accountant");
    expect(roleText(owner)).toBe("Owner");
    expect(roleText({ role: "advisor" })).toBe("Advisor");
    expect(roleText(null)).toBe("—");
  });
  it("shows the numbers of the role, less what the person cannot open", () => {
    expect(homeOf(accountant)).toBe("finance");
    expect(homeTiles(accountant)).toEqual(["cash", "receivables", "net_burn", "stock"]);
    expect(homeTiles({ ...accountant, modules: ["finance"] })).toEqual(["cash", "receivables", "net_burn"]);
    expect(homeTiles(owner)).toEqual(["cash", "runway", "receivables", "people"]);
    expect(homeTiles(hrOnly)).toEqual(["onboarding", "leave_waiting", "people"]);          // no payroll run for someone who cannot see pay
    expect(homeTiles({ home: "general", modules: ["*"] })).toEqual([]);
    expect(homeOf({ home: "nonsense" })).toBe("general");
  });
});

describe("inviting and changing access", () => {
  it("starts from a preset with a level spelled out for each module", () => {
    const f = applyPreset(blankAccess(), preset({}), MODULES);
    expect(f).toMatchObject({ preset: "accountant", title: "Accountant", owner: false, scopes: { finance: "write", gst: "write" } });
    expect(applyPreset(blankAccess(), preset({ key: "viewer", scopes: { "*": "read" } }), MODULES).scopes).toEqual({ finance: "read", gst: "read", payroll: "read", hr: "read", inventory: "read" });
    expect(applyPreset(f, undefined, MODULES)).toMatchObject({ preset: "", scopes: f.scopes });      // "Custom" keeps what was there
  });
  it("names the preset only while its scopes are untouched", () => {
    const p = preset({});
    const f = { ...applyPreset(blankAccess(), p, MODULES), email: " a@b.in ", name: " Anil " };
    expect(accessBody(f, true, p, MODULES)).toEqual({ owner: false, scopes: { finance: "write", gst: "write" }, title: "Accountant", preset: "accountant", email: "a@b.in", full_name: "Anil" });
    const changed = setLevel(f, "payroll", "read");
    expect(accessBody(changed, true, p, MODULES)).not.toHaveProperty("preset");
    expect(accessBody(changed, false, p, MODULES)).toEqual({ owner: false, scopes: { finance: "write", gst: "write", payroll: "read" }, title: "Accountant" });
    expect(setLevel(changed, "gst", "").scopes).toEqual({ finance: "write", payroll: "read" });
  });
  it("sends no scopes for an owner, and refuses an invite that gives nothing", () => {
    expect(accessBody({ ...blankAccess(), email: "a@b.in", owner: true, scopes: { gst: "read" } }, true)).toEqual({ owner: true, email: "a@b.in" });
    expect(accessProblem({ ...blankAccess(), email: "a@b.in" }, true)).toMatch(/role/);
    expect(accessProblem({ ...blankAccess(), email: "nope", owner: true }, true)).toMatch(/email/);
    expect(accessProblem({ ...blankAccess(), owner: true }, false)).toBeNull();
    expect(accessProblem({ ...blankAccess(), email: "a@b.in", scopes: { gst: "read" } }, true)).toBeNull();
  });
  it("writes what a person may do in words", () => {
    expect(scopesText({ "*": "file" })).toBe("Everything");
    expect(scopesText({ "*": "read" })).toBe("Everything: View");
    expect(scopesText({ hr: "read", finance: "file", payroll: "approve" }, MODULES)).toBe("Finance and books: Approve, file and pay · Payroll and salaries: Approve · HR: View");
    expect(scopesText(undefined)).toBe("—");
  });
});
