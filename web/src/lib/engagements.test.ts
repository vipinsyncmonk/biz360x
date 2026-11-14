import { describe, expect, it } from "vitest";
import {
  answerProblem, applyEngagePreset, blankEngage, engageBody, engageProblem, engagementLine, inviteLine, isAudit, serviceLabel, staffLine, staffOnly, toggleStaff,
  type EngageForm, type Engagement, type FirmClient, type FirmPerson, type Invite,
} from "./engagements";

const TODAY = "2026-11-14";
const short = (iso: string) => `${Number(iso.slice(8, 10))}/${Number(iso.slice(5, 7))}`;
const form = (p: Partial<EngageForm> = {}): EngageForm => ({ ...blankEngage(), name: "Gita GST", email: "gita@example.in", service: "gst", levels: { gst: "certify", sales: "read" }, ...p });
const people: FirmPerson[] = [{ user_id: "p1", name: "Ravi Rao", role: "partner", sees_all: true }, { user_id: "s1", name: "Sita Staff", role: "staff", sees_all: false },
  { user_id: "s2", name: "Tara Staff", role: "staff", sees_all: false }];
const client = (p: Partial<FirmClient> = {}): FirmClient => ({ entity_id: "e1", legal_name: "Acme Pvt Ltd", set_up_by_firm: false, services: ["GST"], staff: [], ...p });

describe("engaging a practitioner", () => {
  it("starts from a preset and sends the service, the scopes and the invitation", () => {
    const f = applyEngagePreset({ ...blankEngage(), name: "Gita GST", email: " gita@example.in " }, { key: "gst_consultant", kind: "practitioner", title: "GST consultant", about: "", owner: false, home: null,
      service: "gst", scopes: { gst: "certify", sales: "read" } });
    expect(f).toMatchObject({ preset: "gst_consultant", service: "gst", levels: { gst: "certify", sales: "read" } });
    expect(engageProblem(f, TODAY)).toBeNull();
    expect(engageBody(f)).toEqual({ advisor_name: "Gita GST", advisor_kind: "chartered_accountant", service: "gst", scopes: { gst: "certify", sales: "read" }, invite_email: "gita@example.in",
      expires_on: null, period_fy: null, membership_no: null });
    const preset = { key: "gst_consultant", scopes: { sales: "read", gst: "certify" } };
    const named = engageBody(f, preset);                                                             // untouched: the preset is named and carries the scopes
    expect(named).toMatchObject({ preset: "gst_consultant", service: "gst" });
    expect(named).not.toHaveProperty("scopes");
    const changed = engageBody({ ...f, levels: { ...f.levels, purchases: "read" } }, preset);        // one line changed: the scopes are sent as they stand
    expect(changed).not.toHaveProperty("preset");
    expect(changed.scopes).toEqual({ gst: "certify", sales: "read", purchases: "read" });
    expect(applyEngagePreset(f, undefined)).toMatchObject({ preset: "", service: "gst" });           // "Custom" keeps what was chosen
  });
  it("is only a record without an email, and drops modules set back to no access", () => {
    const body = engageBody(form({ email: "", levels: { gst: "certify", sales: "" } }));
    expect(body).not.toHaveProperty("invite_email");
    expect(body.scopes).toEqual({ gst: "certify" });
    expect(engageBody(form({ service: "secretarial" })).advisor_kind).toBe("company_secretary");
    expect(engageBody(form({ service: "statutory_audit" })).advisor_kind).toBe("auditor");
  });
  it("explains what is missing or wrong, one thing at a time", () => {
    expect(engageProblem(form({ name: " " }), TODAY)).toMatch(/name/);
    expect(engageProblem(form({ email: "gita" }), TODAY)).toMatch(/email/);
    expect(engageProblem(form({ service: "" }), TODAY)).toMatch(/engaged for/);
    expect(engageProblem(form({ levels: { gst: "" } }), TODAY)).toMatch(/at least one module/);
    expect(engageProblem(form({ expires: TODAY }), TODAY)).toMatch(/after today/);
    expect(engageProblem(form({ fy: "2026" }), TODAY)).toMatch(/2026-27/);
    expect(engageProblem(form({ membershipNo: "1" }), TODAY)).toMatch(/membership/);
  });
  it("holds an auditor to viewing and querying, with an end date", () => {
    expect(isAudit("tax_audit")).toBe(true);
    expect(isAudit("gst")).toBe(false);
    const audit = form({ service: "statutory_audit", levels: { finance: "comment", sales: "read" }, expires: "2027-09-30" });
    expect(engageProblem(audit, TODAY)).toBeNull();
    expect(engageProblem({ ...audit, levels: { finance: "prepare" } }, TODAY)).toMatch(/only view and raise queries/);
    expect(engageProblem({ ...audit, expires: "" }, TODAY)).toMatch(/end date/);
  });
  it("describes an engagement in a line", () => {
    const g: Engagement = { id: "1", advisor_name: "Gita GST", advisor_kind: "chartered_accountant", service: "gst", scopes: { sales: "read", gst: "certify" }, expires_on: "2027-03-31", period_fy: "2026-27",
      invited_email: "gita@example.in", party: "person", audit: false, status: "active" };
    expect(engagementLine(g, short)).toBe("GST · as a person · GST: Prepare and file, Sales and billing: View · FY 2026-27 · until 31/3");
    expect(engagementLine({ ...g, status: "invited", expires_on: null, period_fy: null }, short)).toBe("GST · invited at gita@example.in · GST: Prepare and file, Sales and billing: View");
    expect(engagementLine({ ...g, party: "record", service: null, invited_email: null, expires_on: null, period_fy: null }, short)).toMatch(/^not invited; a record only/);
    expect(serviceLabel("pf_esi")).toBe("PF and ESI");
    expect(serviceLabel(null)).toBeNull();
  });
});

describe("answering an invitation", () => {
  const inv: Invite = { id: "1", entity_id: "e1", entity_name: "Acme Pvt Ltd", advisor_name: "Gita GST", service: "gst", service_label: "GST", scopes: { gst: "certify", sales: "read" },
    period_fy: "2026-27", expires_on: "2027-03-31", invited_by_name: "Asha", audit: false };
  it("says who invites, for what, until when and what it lets them do", () => {
    expect(inviteLine(inv, short)).toBe("Acme Pvt Ltd invites you for GST (FY 2026-27), until 31/3. You will be able to: GST: Prepare and file, Sales and billing: View.");
    expect(inviteLine({ ...inv, service_label: null, period_fy: null, expires_on: null, audit: true, scopes: { finance: "comment" } }, short))
      .toBe("Acme Pvt Ltd invites you for advisory work. You will be able to: Finance and books: View and query. As an auditor you can view and raise queries, nothing else.");
  });
  it("puts the independence rule in words", () => {
    expect(answerProblem("independence_conflict", "x")).toMatch(/already keep this company's books/);
    expect(answerProblem("not_found", "x")).toMatch(/no longer open/);
    expect(answerProblem(undefined, "Could not reach the server")).toBe("Could not reach the server");
  });
});

describe("a firm's staff per client", () => {
  it("ticks and unticks a person, keeping the list in order", () => {
    expect(toggleStaff([], "s2")).toEqual(["s2"]);
    expect(toggleStaff(["s2"], "s1")).toEqual(["s1", "s2"]);
    expect(toggleStaff(["s1", "s2"], "s1")).toEqual(["s2"]);
    expect(staffOnly(people).map((p) => p.user_id)).toEqual(["s1", "s2"]);                           // partners are never ticked: they see everything
  });
  it("says how many staff are on a client", () => {
    expect(staffLine(client({ staff: ["s1"] }), people)).toBe("1 of 2 staff");
    expect(staffLine(client(), people)).toBe("No staff assigned: only partners see this client");
    expect(staffLine(client(), [people[0]!])).toBe("The firm has no staff yet: partners see every client");
  });
});
