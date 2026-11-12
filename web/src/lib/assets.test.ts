import { describe, expect, it } from "vitest";
import { assetBody, assetProblem, blankAsset, currentPeriod, disposeProblem, lifeText, methodText, monthLabel, moneyText, type Asset, type AssetForm } from "./assets";

const TODAY = "2026-11-11";
const ok = (p: Partial<AssetForm> = {}): AssetForm => ({ ...blankAsset(), name: "Dell laptop", acquiredOn: "2026-04-01", cost: "120000", ...p });

describe("registering an asset", () => {
  it("accepts a plain asset and sends money with two decimals, leaving life and residual to the server", () => {
    expect(assetProblem(ok(), TODAY)).toBeNull();
    expect(assetBody(ok())).toMatchObject({ name: "Dell laptop", category: "computers", cost: "120000.00", residual_value: null, method: "slm", life_months: null, rate_pct: null, booking: "reclassify", opening_through: null });
  });
  it("explains what is wrong, one thing at a time", () => {
    expect(assetProblem(ok({ name: " " }), TODAY)).toMatch(/name/);
    expect(assetProblem(ok({ acquiredOn: "" }), TODAY)).toMatch(/acquired/);
    expect(assetProblem(ok({ acquiredOn: "2026-12-01" }), TODAY)).toMatch(/future/);
    expect(assetProblem(ok({ putToUseOn: "2026-03-01" }), TODAY)).toMatch(/put to use/);
    expect(assetProblem(ok({ cost: "12,000" }), TODAY)).toMatch(/cost/);
    expect(assetProblem(ok({ cost: "0" }), TODAY)).toMatch(/cost/);
    expect(assetProblem(ok({ residual: "120000" }), TODAY)).toMatch(/residual/);
    expect(assetProblem(ok({ lifeMonths: "3 years" }), TODAY)).toMatch(/months/);
    expect(assetProblem(ok({ method: "wdv" }), TODAY)).toMatch(/rate/);
  });
  it("sends the rate for written down value and the life for straight line, never both", () => {
    expect(assetBody(ok({ method: "wdv", ratePct: "40", lifeMonths: "36" }))).toMatchObject({ method: "wdv", rate_pct: "40.00", life_months: null });
    expect(assetBody(ok({ method: "slm", ratePct: "40", lifeMonths: "48" }))).toMatchObject({ method: "slm", rate_pct: null, life_months: 48 });
  });
  it("an asset from earlier books needs the depreciation so far, as of a month-end", () => {
    const o = ok({ booking: "opening", openingThrough: "2026-03-31", openingAccumulated: "20000" });
    expect(assetProblem(o, TODAY)).toBeNull();
    expect(assetBody(o)).toMatchObject({ booking: "opening", opening_accumulated: "20000.00", opening_through: "2026-03-31" });
    expect(assetProblem({ ...o, openingThrough: "2026-03-30" }, TODAY)).toMatch(/last day/);
    expect(assetProblem({ ...o, openingThrough: "" }, TODAY)).toMatch(/as of/);
    expect(assetProblem({ ...o, openingAccumulated: "" }, TODAY)).toMatch(/so far/);
  });
  it("turns what was typed into money without a float", () => {
    expect(moneyText("90000")).toBe("90000.00");
    expect(moneyText("0.5")).toBe("0.50");
    expect(moneyText(" 1234567.89 ")).toBe("1234567.89");
  });
});

describe("selling an asset", () => {
  const a = { acquired_on: "2026-04-01", put_to_use_on: "2026-04-10" } as Asset;
  it("needs a date that is not before it was used nor in the future, and an amount (0 for a write-off)", () => {
    expect(disposeProblem("", "100", a, TODAY)).toMatch(/date/);
    expect(disposeProblem("2026-04-05", "100", a, TODAY)).toMatch(/put to use/);
    expect(disposeProblem("2027-01-01", "100", a, TODAY)).toMatch(/future/);
    expect(disposeProblem("2026-09-01", "", a, TODAY)).toMatch(/received/);
    expect(disposeProblem("2026-09-01", "0", a, TODAY)).toBeNull();
  });
});

describe("how things read", () => {
  it("months, lives and methods", () => {
    expect(monthLabel("2025-04-01")).toBe("Apr 2025");
    expect(currentPeriod(new Date(2026, 10, 11))).toBe("2026-11");
    expect([lifeText(36), lifeText(18), lifeText(8), lifeText(12)]).toEqual(["3 years", "1 year 6 months", "8 months", "1 year"]);
    expect(methodText({ method: "slm", life_months: 36 } as Asset)).toBe("Straight line · 3 years");
    expect(methodText({ method: "wdv", rate_pct: "40.00" } as Asset)).toBe("Written down value · 40.00% a year");
  });
});
