import { describe, expect, it } from "vitest";
import { addDays, formatInr, greeting, initials, isoDate, shortDate, sumMoney, toPaise } from "./format";
import { daysLabel, kpis, monthGrid, standing, statusInfo, weekBuckets } from "./compliance";
import { decodeExp, needsRefresh } from "./tokens";

describe("money", () => {
  it("groups digits the Indian way and never uses floats", () => {
    expect(formatInr("283200.00")).toBe("₹2,83,200.00");
    expect(formatInr("12345678.5")).toBe("₹1,23,45,678.50");
    expect(formatInr("999.99")).toBe("₹999.99");
    expect(formatInr("0")).toBe("₹0.00");
    expect(formatInr("-1500.00")).toBe("-₹1,500.00");
    expect(formatInr("4820000.00", { decimals: false })).toBe("₹48,20,000");
    expect(formatInr(null)).toBe("₹0.00");
  });
  it("adds exactly", () => {
    expect(sumMoney(["0.10", "0.20"])).toBe("0.30");        // 0.1 + 0.2 is 0.30000000000000004 as floats
    expect(sumMoney(["100.00", null, "-25.50"])).toBe("74.50");
    expect(sumMoney([])).toBe("0.00");
    expect(toPaise("1.5")).toBe(150n);
  });
  it("rejects what is not money", () => {
    expect(() => toPaise("abc")).toThrow();
  });
});

describe("dates and names", () => {
  it("formats plain calendar dates without timezone drift", () => {
    expect(shortDate("2026-10-30")).toBe("30 Oct");
    expect(shortDate("2026-01-05T00:00:00Z")).toBe("05 Jan");
    expect(shortDate(null)).toBe("");
    expect(isoDate(addDays(new Date(2026, 9, 30), 3))).toBe("2026-11-02");
  });
  it("greets and abbreviates", () => {
    expect(greeting("Ananya Rao", 9)).toBe("Good morning, Ananya");
    expect(greeting("", 20)).toBe("Good evening, there");
    expect(initials("Ananya Rao")).toBe("AR");
    expect(initials("Nimbus")).toBe("N");
    expect(initials("")).toBe("?");
  });
});

describe("compliance helpers", () => {
  const obls = [
    { status: "awaiting_approval", days_left: 1, late_fee_accrued: "0.00", rule: { authority_code: "MCA" } },
    { status: "overdue", days_left: -3, late_fee_accrued: "300.00", rule: { authority_code: "GSTN" } },
    { status: "upcoming", days_left: 12, late_fee_accrued: "0.00", rule: { authority_code: "GSTN" } },
    { status: "filed", days_left: -10, filed_at: "2026-10-18T10:00:00Z", late_fee_accrued: "50.00", rule: { authority_code: "EPFO" } },
  ];
  it("labels statuses and days", () => {
    expect(statusInfo("awaiting_approval")).toEqual({ label: "Awaiting approval", tone: "due" });
    expect(statusInfo("filed").tone).toBe("ok");
    expect(statusInfo("something_new").label).toBe("something_new");
    expect(daysLabel(0)).toBe("Due today");
    expect(daysLabel(1)).toBe("Due tomorrow");
    expect(daysLabel(5)).toBe("5 days left");
    expect(daysLabel(-1)).toBe("1 day overdue");
    expect(daysLabel(-4)).toBe("4 days overdue");
    expect(daysLabel(null)).toBe("");
  });
  it("counts what needs a person, ignoring closed items", () => {
    expect(kpis(obls)).toEqual({ dueSoon: 1, needsAction: 2, overdue: 1, lateFees: "350.00" });
  });
  it("summarises each authority", () => {
    const s = Object.fromEntries(standing(obls).map((r) => [r.authority, r]));
    expect(s.MCA).toMatchObject({ open: 1, tone: "due", label: "Action due" });
    expect(s.GSTN).toMatchObject({ open: 2, tone: "due" });
    expect(s.EPFO).toMatchObject({ open: 0, tone: "ok", label: "Clear", lastFiled: "2026-10-18" });
  });
  it("groups the calendar into weeks and keeps empty weeks", () => {
    const days = [{ date: "2026-10-30", items: [{ title: "AOC-4" }] }, { date: "2026-11-11", items: [{ title: "GSTR-1" }] }];
    const w = weekBuckets(days, new Date(2026, 9, 29), 5);
    expect(w).toHaveLength(5);
    expect(w[0]!.days.map((d) => d.day)).toEqual(["30 Oct"]);
    expect(w[2]!.days).toEqual([]);
    expect(w[1]!.days.map((d) => d.day)).toEqual(["11 Nov"]);
    expect(w[0]!.label).toBe("29 OCT – 04 NOV");
  });
  it("builds a Monday-first month grid", () => {
    const g = monthGrid(2026, 10);                           // November 2026 starts on a Sunday
    expect(g[0]!.slice(0, 6).every((c) => c === null)).toBe(true);
    expect(g[0]![6]).toBe(1);
    expect(g.flat().filter((c) => c !== null)).toHaveLength(30);
    expect(g.every((r) => r.length === 7)).toBe(true);
  });
});

describe("tokens", () => {
  const jwt = (exp: number) => `h.${Buffer.from(JSON.stringify({ exp })).toString("base64url")}.s`;
  it("reads the expiry and asks for a refresh a little early", () => {
    expect(decodeExp(jwt(2000))).toBe(2000);
    expect(decodeExp("garbage")).toBeNull();
    expect(needsRefresh(jwt(2000), 1900 * 1000)).toBe(false);
    expect(needsRefresh(jwt(2000), 1980 * 1000)).toBe(true);   // inside the 30 s margin
    expect(needsRefresh(null, 0)).toBe(true);
  });
});

import { answersBody, DEFAULT_ANSWERS, hints, identifierKind, isLookupFinished, isTrack, normaliseIdentifier } from "./onboarding";

describe("onboarding helpers", () => {
  it("recognises what a person pastes", () => {
    expect(identifierKind("u26510ka2019ptc123456")).toBe("cin");
    expect(identifierKind(" U26510KA2019PTC123456 ")).toBe("cin");
    expect(identifierKind("AAB-1234")).toBe("llpin");
    expect(identifierKind("29AAHCN1234Q1Z8")).toBe("gstin");
    expect(identifierKind("AAHCN1234Q")).toBe("pan");
    expect(identifierKind("12345")).toBeNull();
    expect(identifierKind("")).toBeNull();
    expect(normaliseIdentifier(" 29aahcn 1234q1z8 ")).toBe("29AAHCN1234Q1Z8");
  });
  it("builds the request body the API expects", () => {
    expect(answersBody("L1", "msme", ["29AAHCN1234Q1Z8"], { ...DEFAULT_ANSWERS, foreign: true })).toEqual({
      lookup_id: "L1", track: "msme", gstins: ["29AAHCN1234Q1Z8"], headcount_band: "20-49", turnover_band: "1.5-5cr", has_foreign_shareholder: true, buys_from_mse: "unsure",
    });
  });
  it("explains each answer and knows when a lookup is over", () => {
    expect(hints.foreign(true)).toMatch(/FEMA/);
    expect(hints.headcount("1-9")).toMatch(/Below/);
    expect(hints.turnover(">10cr")).toMatch(/audit/);
    expect(isLookupFinished("running")).toBe(false);
    expect(["found", "not_found", "failed"].every(isLookupFinished)).toBe(true);
    expect(isTrack("startup")).toBe(true);
    expect(isTrack("other")).toBe(false);
  });
});

import { ageLabel, blankLine, docBody, docStatus, einvoiceLabel, formReady, hasBlock, lineProblem, linesBody, linesComplete, tabQuery } from "./billing";

describe("billing helpers", () => {
  const good = { ...blankLine(), description: "Pressure transmitter", hsn_sac: "902620", qty: "10", rate: "1000.00" };
  it("maps tabs to queries", () => {
    expect(tabQuery("all")).toEqual({});
    expect(tabQuery("overdue")).toEqual({ overdue: "true" });
    expect(tabQuery("unpaid")).toEqual({ status: "issued", payment_status: "unpaid" });
    expect(tabQuery("draft")).toEqual({ status: "draft" });
  });
  it("names a document's state", () => {
    const base = { status: "issued", payment_status: "unpaid", days_overdue: 0, irn_status: "generated" };
    expect(docStatus({ ...base, status: "draft" })).toEqual({ label: "Draft", tone: "prep" });
    expect(docStatus(base).label).toBe("Unpaid");
    expect(docStatus({ ...base, days_overdue: 12 })).toEqual({ label: "Overdue", tone: "due" });
    expect(docStatus({ ...base, payment_status: "paid" }).tone).toBe("ok");
    expect(docStatus({ ...base, payment_status: "partially_paid" }).label).toBe("Part paid");
    expect(einvoiceLabel("pending", "issued")).toBe("IRN pending");
    expect(einvoiceLabel("pending", "draft")).toBe("Not issued");
    expect(einvoiceLabel("not_required", "issued")).toBe("E-invoice not required");
  });
  it("describes age", () => {
    expect(ageLabel(12, "2026-10-01")).toBe("12 days overdue");
    expect(ageLabel(1, "2026-10-01")).toBe("1 day overdue");
    expect(ageLabel(0, "2026-11-10", new Date(2026, 10, 7))).toBe("Due in 3 days");
    expect(ageLabel(0, "2026-11-07", new Date(2026, 10, 7))).toBe("Due today");
    expect(ageLabel(0, null)).toBe("");
  });
  it("finds what is missing from a line", () => {
    expect(lineProblem(good)).toBeNull();
    expect(lineProblem({ ...good, description: " " })).toMatch(/Describe/);
    expect(lineProblem({ ...good, hsn_sac: "12" })).toMatch(/HSN/);
    expect(lineProblem({ ...good, qty: "0" })).toMatch(/Quantity/);
    expect(lineProblem({ ...good, qty: "1e3" })).toMatch(/Quantity/);
    expect(lineProblem({ ...good, rate: "" })).toMatch(/rate/);
    expect(lineProblem({ ...good, gst_rate: "7" })).toMatch(/GST/);
    expect(linesComplete([good, blankLine()])).toBe(false);
    expect(linesComplete([])).toBe(false);
  });
  it("builds the request without doing any maths on money", () => {
    expect(linesBody([{ ...good, description: " Pressure transmitter " }])[0]).toEqual({ description: "Pressure transmitter", hsn_sac: "902620", qty: "10", rate: "1000.00", gst_rate: 18, discount: "0", uqc: "NOS" });
    const f = { registrationId: "R", partyId: "P", partyGstId: "", docDate: "2026-11-07", terms: "", po: " PO-7 ", lines: [good] };
    expect(formReady(f)).toBe(true);
    expect(docBody(f)).toMatchObject({ doc_type: "tax_invoice", registration_id: "R", party_id: "P", customer_po_ref: "PO-7" });
    expect(docBody(f)).not.toHaveProperty("party_gst_id");
    expect(docBody(f)).not.toHaveProperty("payment_terms_days");
    expect(docBody({ ...f, partyGstId: "G", terms: "45" })).toMatchObject({ party_gst_id: "G", payment_terms_days: 45 });
    expect(formReady({ ...f, partyId: "" })).toBe(false);
    expect(formReady({ ...f, terms: "abc" })).toBe(false);
    expect(hasBlock([{ level: "ok" }, { level: "block" }])).toBe(true);
    expect(hasBlock([{ level: "warn" }])).toBe(false);
  });
});
