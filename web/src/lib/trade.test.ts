import { describe, expect, it } from "vitest";
import {
  blankBoe, blankRealisation, blankSb, boeBody, boeProblem, claimsBody, claimsForm, claimsPending, claimsProblem, clockText, lutText, previewInr, previewText, realisationBody, realisationProblem, receivedText,
  sbBody, sbProblem,
} from "./trade";

const TODAY = "2026-11-08";
const sb = (over: object) => ({ ...blankSb(), number: "7712345", date: "2026-10-01", port: "inmaa1", currency: "usd", fob: "10000.00", rate: "83.2150", ...over });

describe("exports and imports", () => {
  it("previews the rupee value the way the server rounds it", () => {
    expect(previewInr("10000.00", "83.2150")).toBe("832150.00");
    expect(previewInr("1.01", "83.3333")).toBe("84.17");        // 84.166633 rounds up
    expect(previewInr("0.01", "83.4999")).toBe("0.83");
    expect(previewInr("", "83")).toBeNull();
    expect(previewInr("10", "0")).toBeNull();
    expect(previewInr("1,000", "83")).toBeNull();
    expect(previewText("10000", "83.215")).toBe("About ₹8,32,150.00");
  });

  it("says where the nine-month clock stands", () => {
    expect(clockText({ realisation_state: "awaiting_export", realisation_due_on: null, days_left: null })).toMatch(/date of export/);
    expect(clockText({ realisation_state: "pending", realisation_due_on: "2027-10-15", days_left: 120 })).toBe("Due 15 Oct, 120 days left");
    expect(clockText({ realisation_state: "pending", realisation_due_on: "2027-10-15", days_left: 1 })).toBe("Due 15 Oct, 1 day left");
    expect(clockText({ realisation_state: "overdue", realisation_due_on: "2026-10-15", days_left: -24 })).toBe("Was due 15 Oct, overdue by 24 days");
    expect(clockText({ realisation_state: "realised", realisation_due_on: "2026-10-15", days_left: null })).toBe("All proceeds received");
  });

  it("explains the LUT in plain words", () => {
    expect(lutText({ state: "none", valid_until: null, days_left: null })).toMatch(/would be charged IGST/);
    expect(lutText({ state: "expiring", valid_until: "2026-11-18", days_left: 10 })).toBe("It ends on 18 Nov, in 10 days. File the next one before it lapses.");
    expect(lutText({ state: "expired", valid_until: "2026-03-31", days_left: -222 })).toMatch(/expired on 31 Mar/);
    expect(lutText({ state: "valid", valid_until: "2027-03-31", days_left: 143 })).toBe("Valid until 31 Mar.");
    expect(lutText({ state: "valid", valid_until: null, days_left: null })).toMatch(/no end date/);
  });

  it("checks a shipping bill and sends it in capitals", () => {
    expect(sbProblem(sb({}), TODAY)).toBeNull();
    expect(sbProblem(sb({ number: "no spaces" }), TODAY)).toMatch(/shipping bill number/);
    expect(sbProblem(sb({ date: "2026-12-01" }), TODAY)).toMatch(/future/);
    expect(sbProblem(sb({ port: "MAA" }), TODAY)).toMatch(/port code/);
    expect(sbProblem(sb({ currency: "US" }), TODAY)).toMatch(/currency/);
    expect(sbProblem(sb({ fob: "0" }), TODAY)).toMatch(/FOB/);
    expect(sbProblem(sb({ rate: "abc" }), TODAY)).toMatch(/exchange rate/);
    expect(sbProblem(sb({ exportedOn: "2026-09-01" }), TODAY)).toMatch(/before the shipping bill date/);
    expect(sbBody(sb({ invoiceId: "inv1", exportedOn: "2026-10-05" }))).toEqual({
      sb_number: "7712345", sb_date: "2026-10-01", port_code: "INMAA1", currency: "USD", fob_foreign: "10000.00", exchange_rate: "83.2150", sales_document_id: "inv1", exported_on: "2026-10-05",
    });
    expect(sbBody(sb({}))).not.toHaveProperty("sales_document_id");
  });

  it("checks money received against what is still owed", () => {
    const f = { ...blankRealisation("4000.00"), date: "2026-11-01" };
    expect(f.amount).toBe("4000.00");                                                      // suggests the whole balance
    expect(realisationProblem(f, "4000.00", TODAY)).toBeNull();
    expect(realisationProblem({ ...f, amount: "4000.02" }, "4000.00", TODAY)).toMatch(/Only 4000.00/);
    expect(realisationProblem({ ...f, amount: "4000.01" }, "4000.00", TODAY)).toBeNull();     // a cent of rounding
    expect(realisationProblem({ ...f, date: "" }, "4000.00", TODAY)).toMatch(/when the money/);
    expect(realisationProblem({ ...f, date: "2026-12-01" }, "4000.00", TODAY)).toMatch(/future/);
    expect(blankRealisation("0.00").amount).toBe("");
    expect(realisationBody({ ...f, bankRef: " UTR1 ", firc: "" })).toEqual({ received_on: "2026-11-01", amount_foreign: "4000.00", bank_ref: "UTR1" });
    expect(realisationBody({ ...f, inr: " 331800.00 ", charges: "450.00" })).toMatchObject({ amount_inr: "331800.00", bank_charges: "450.00" });
    expect(realisationBody({ ...f, inr: "", charges: "450.00" })).not.toHaveProperty("bank_charges");
    expect(realisationProblem({ ...f, inr: "abc" }, "4000.00", "2026-11-09")).toMatch(/rupees/);
    expect(realisationProblem({ ...f, charges: "10.00" }, "4000.00", "2026-11-09")).toMatch(/Bank charges/);
    expect(realisationProblem({ ...f, inr: "331800.00", charges: "450.00" }, "4000.00", "2026-11-09")).toBeNull();
    const r = { id: "r", received_on: "2026-11-01", amount_foreign: "4000.00", bank_ref: null, firc_no: "F1", amount_inr: "331800.00", bank_charges: "0.00", booked_inr: "332000.00", forex_gain: "-200.00" };
    expect(receivedText("USD", r, "1 Nov")).toBe("USD 4000.00 on 1 Nov (FIRC F1) · exchange loss ₹200.00");
    expect(receivedText("USD", { ...r, forex_gain: "1500.5" }, "1 Nov")).toMatch(/exchange gain ₹1,500\.50/);
    expect(receivedText("USD", { ...r, forex_gain: null, firc_no: null }, "1 Nov")).toBe("USD 4000.00 on 1 Nov");
  });

  it("checks RoDTEP and drawback figures", () => {
    const b = { rodtep_claimed: "12000.50", rodtep_credited: "4000.25", drawback_claimed: "0.00", drawback_received: "0.00", brc_ref: null } as never;
    const f = claimsForm(b);
    expect(claimsPending(b)).toBe(true);
    expect(claimsPending({ rodtep_claimed: "0", rodtep_credited: "0", drawback_claimed: "5", drawback_received: "5" } as never)).toBe(false);
    expect(claimsProblem(f)).toBeNull();
    expect(claimsProblem({ ...f, rodtepCredited: "12000.51" })).toMatch(/more RoDTEP|More RoDTEP/);
    expect(claimsProblem({ ...f, drawbackReceived: "1" })).toMatch(/More drawback/);
    expect(claimsProblem({ ...f, rodtepClaimed: "abc" })).toMatch(/RoDTEP claimed/);
    expect(claimsBody({ ...f, brc: " BRC/1 " })).toEqual({ rodtep_claimed: "12000.50", rodtep_credited: "4000.25", drawback_claimed: "0.00", drawback_received: "0.00", brc_ref: "BRC/1" });
    expect(claimsBody(f).brc_ref).toBeNull();
  });

  it("checks a bill of entry and marks it paid only when a payment date is given", () => {
    const f = { ...blankBoe(), number: "5500123", date: "2026-10-20", port: "inblr4", cif: "100000", rate: "11.5", igst: "216000" };
    expect(boeProblem(f, TODAY)).toBeNull();
    expect(boeProblem({ ...f, port: "BLR" }, TODAY)).toMatch(/port code/);
    expect(boeProblem({ ...f, igst: "-1" }, TODAY)).toMatch(/IGST/);
    expect(boeProblem({ ...f, paidOn: "2026-12-01" }, TODAY)).toMatch(/future/);
    expect(boeBody(f)).toMatchObject({ boe_number: "5500123", port_code: "INBLR4", currency: "USD", igst: "216000", bcd: "0" });
    expect(boeBody(f)).not.toHaveProperty("status");
    expect(boeBody({ ...f, paidOn: "2026-10-25" })).toMatchObject({ status: "duty_paid", duty_paid_on: "2026-10-25" });
  });
});
