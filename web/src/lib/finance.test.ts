import { describe, expect, it } from "vitest";
import { exportHintText, proceedsBody, proceedsForm, proceedsProblem, type ExportHint } from "./finance";

const hint: ExportHint = { shipping_bill_id: "sb1", sb_number: "7790001", currency: "USD", proposed_foreign: "500.00", expected_inr: "41500.00", difference_inr: "-350.00" };

describe("a bank credit taken as export proceeds", () => {
  it("starts from the dollars the matcher guessed and asks for the rest", () => {
    expect(proceedsForm(hint)).toEqual({ foreign: "500.00", charges: "", firc: "" });
    expect(proceedsForm({ ...hint, proposed_foreign: null }).foreign).toBe("");
  });
  it("needs the foreign amount and a sensible charge", () => {
    const f = proceedsForm(hint);
    expect(proceedsProblem(f)).toBeNull();
    expect(proceedsProblem({ ...f, foreign: "" })).toMatch(/foreign currency/);
    expect(proceedsProblem({ ...f, foreign: "0" })).toMatch(/foreign currency/);
    expect(proceedsProblem({ ...f, charges: "abc" })).toMatch(/charges/);
    expect(proceedsProblem({ ...f, charges: "350.00" })).toBeNull();
  });
  it("sends only what was filled in", () => {
    expect(proceedsBody("sb1", proceedsForm(hint))).toEqual({ shipping_bill_id: "sb1", amount_foreign: "500.00" });
    expect(proceedsBody("sb1", { foreign: " 500.00 ", charges: "350.00", firc: " F-9 " })).toEqual({ shipping_bill_id: "sb1", amount_foreign: "500.00", bank_charges: "350.00", firc_no: "F-9" });
  });
  it("says why the credit is not the invoice amount", () => {
    expect(exportHintText(hint)).toMatch(/USD 500\.00 .*41,500\.00 at the invoice rate\)\. The credit is .*350\.00 less/);
    expect(exportHintText({ ...hint, difference_inr: "120.00" })).toMatch(/120\.00 more/);
    expect(exportHintText({ ...hint, difference_inr: "0.00" })).toMatch(/matches to the rupee/);
    expect(exportHintText({ ...hint, proposed_foreign: null })).toBe("Looks like money from abroad");
  });
});
