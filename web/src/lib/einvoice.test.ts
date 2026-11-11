import { describe as suite, expect, it } from "vitest";
import { declareBody, declareProblem, declaredOn, describe, yearOptions, type EInvoiceStatus } from "./einvoice";

const base: EInvoiceStatus = { limit: "50000000.00", financial_year: "2026-27", applies_today: false, level: "clear", applies_from: null, crossed_in: null,
  this_year: { turnover: "12000000.00", percent_of_limit: "24.0" }, years: [] };

suite("e-invoicing wording", () => {
  it("says it is not required, with how far turnover has got", () => {
    const d = describe(base);
    expect(d.pill).toBe("Not required"); expect(d.tone).toBe("ok");
    expect(d.detail).toMatch(/24\.0% of/);
  });
  it("warns when turnover is close", () => {
    expect(describe({ ...base, level: "approaching", this_year: { turnover: "42000000.00", percent_of_limit: "84.0" } }).pill).toBe("Getting close");
  });
  it("says when it starts after the limit is crossed, and that it is not needed until then", () => {
    const d = describe({ ...base, level: "crossed", crossed_in: "2026-27", applies_from: "2027-04-01" });
    expect(d.pill).toBe("From 1 Apr 2027");
    expect(d.detail).toMatch(/Not needed for the rest of FY 2026-27/); expect(d.detail).toMatch(/1 Apr 2027/);
  });
  it("says it applies and why", () => {
    const d = describe({ ...base, level: "applies", applies_today: true, crossed_in: "2025-26", applies_from: "2026-04-01" });
    expect(d.pill).toBe("Required"); expect(d.detail).toMatch(/FY 2025-26/);
  });
});

suite("declaring a year's turnover", () => {
  it("offers this year and the five before it", () => {
    expect(yearOptions("2026-27")).toEqual(["2026-27", "2025-26", "2024-25", "2023-24", "2022-23", "2021-22"]);
    expect(yearOptions("2000-01")[0]).toBe("2000-01");
  });
  it("dates a finished year on its 31 March and the running year today", () => {
    const today = new Date(2026, 10, 11);                                   // 11 Nov 2026
    expect(declaredOn("2025-26", today)).toBe("2026-03-31");
    expect(declaredOn("2026-27", today)).toBe("2026-11-11");
  });
  it("accepts rupees with commas and refuses anything else", () => {
    expect(declareProblem({ fy: "2025-26", amount: "4,50,00,000" })).toBeNull();
    expect(declareProblem({ fy: "2025-26", amount: "₹ 450.50" })).toBeNull();
    expect(declareProblem({ fy: "2025-26", amount: "" })).toMatch(/rupees/);
    expect(declareProblem({ fy: "2025-26", amount: "4 crore" })).toMatch(/rupees/);
  });
  it("sends one dated aato fact", () => {
    expect(declareBody({ fy: "2025-26", amount: "4,50,00,000" }, new Date(2026, 10, 11))).toEqual([{ fact_key: "aato", value: 45000000, as_of: "2026-03-31" }]);
  });
});
