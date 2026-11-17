import { describe, expect, it } from "vitest";
import { blankItem, capText, declBody, draftFromDecl, form16Rows, formProblem, fyOf, fyOptions, itemLine, itemProblem, regimeLabel, type Form16, type TaxDecl, type TaxItem } from "./taxdecl";

const item = (o: Partial<TaxItem> = {}): TaxItem => ({ id: "i1", section: "80C", description: "PPF", declared_amount: "100000.00", proof_ref: null, status: "pending", verified_amount: null, hr_note: null, ...o });

describe("financial years", () => {
  it("runs April to March", () => {
    expect(fyOf(new Date(2026, 3, 1))).toBe("2026-27");
    expect(fyOf(new Date(2027, 2, 31))).toBe("2026-27");
    expect(fyOf(new Date(2099, 11, 1))).toBe("2099-00");
  });
  it("offers this year and the one before", () => { expect(fyOptions(new Date(2026, 10, 15))).toEqual(["2026-27", "2025-26"]); });
});

describe("the declaration form", () => {
  it("asks what it is and how much", () => {
    expect(itemProblem(blankItem())).toMatch(/Say what it is/);
    expect(itemProblem({ ...blankItem(), description: "PPF", amount: "abc" })).toMatch(/amount in rupees/);
    expect(itemProblem({ ...blankItem(), description: "PPF", amount: "1.234" })).toMatch(/amount in rupees/);
    expect(itemProblem({ ...blankItem(), description: "PPF", amount: "50000" })).toBeNull();
    expect(itemProblem({ ...blankItem(), description: "PPF", amount: "12500.5" })).toBeNull();
    expect(formProblem([{ ...blankItem(), description: "PPF", amount: "1" }, blankItem()])).not.toBeNull();
  });
  it("sends trimmed text and no empty proof", () => {
    expect(declBody("old", [{ section: "80D", description: " Health ", amount: " 20000 ", proof: " " }])).toEqual({ regime: "old", items: [{ section: "80D", description: "Health", declared_amount: "20000", proof_ref: null }] });
  });
  it("starts from what was saved", () => {
    const d = { regime: "old", items: [item({ proof_ref: "P-1" })] } as TaxDecl;
    expect(draftFromDecl(d)).toEqual({ regime: "old", items: [{ section: "80C", description: "PPF", amount: "100000.00", proof: "P-1" }] });
  });
});

describe("words", () => {
  it("says what HR did with a figure", () => {
    expect(itemLine(item())).toBe("Not verified yet");
    expect(itemLine(item({ status: "verified", verified_amount: "100000.00" }))).toBe("Verified ₹1,00,000.00");
    expect(itemLine(item({ status: "verified", verified_amount: "60000.00" }))).toBe("Verified ₹60,000.00 of ₹1,00,000.00");
    expect(itemLine(item({ status: "rejected", hr_note: "No statement" }))).toBe("Not accepted: No statement");
  });
  it("words a limit and a regime", () => {
    expect(capText({ annual_cap: "150000.00" })).toBe("up to ₹1,50,000 a year");
    expect(capText({ annual_cap: null })).toBe("no limit");
    expect(regimeLabel("old")).toMatch(/Old regime/);
    expect(regimeLabel("new")).toMatch(/New regime/);
  });
});

describe("Form 16 Part B", () => {
  const f: Form16 = { fy: "2026-27", assessment_year: "2027-28", regime: "old", employee_name: "Dev", months: 12, gross_salary: "1200000.00", standard_deduction: "50000.00",
    chapter_vi_a: [{ section: "80C", label: "Section 80C", amount: "150000.00" }], chapter_vi_a_total: "150000.00", taxable_income: "1000000.00", tax_with_cess: "117000.00",
    tds_deducted: "100000.00", difference: "17000.00", deductions_verified: true, note: "" };
  it("lists the lines in order and says who owes", () => {
    const rows = form16Rows(f);
    expect(rows.map((r) => r[0])).toEqual(["Salary for the year", "Standard deduction", "Section 80C", "Taxable income", "Tax with cess", "TDS deducted by the company", "Still to be paid"]);
    expect(rows[2]![1]).toBe("− ₹1,50,000.00");
    expect(form16Rows({ ...f, difference: "-500.00" }).at(-1)).toEqual(["Deducted more than the tax", "₹500.00"]);
    expect(form16Rows({ ...f, difference: "0.00" }).at(-1)![0]).toBe("Tax and TDS match");
  });
});
