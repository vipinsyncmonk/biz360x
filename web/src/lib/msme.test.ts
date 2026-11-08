import { describe, expect, it } from "vitest";
import { ageBars, blankFacts, buyerAction, classMismatch, claimLine, daysLate, factsBody, factsProblem, headline, lakh, payablesSummary, rateNote, tredsBody, tredsNext, tredsProblem, udyamBar, uploadable } from "./msme";

const inv = (over: object) => ({ document_id: "d", number: "SF/1", balance_due: "11800.00", appointed_on: "2026-09-01", days_late: 70, interest: "900.00", on_treds: false, ...over });
const buyer = (over: object) => ({ party_id: "p", name: "Rathi Auto", invoices: [inv({})], principal: "11800.00", interest: "900.00", days_late: 70, on_treds: false, claim: null, ...over });

describe("MSME track", () => {
  it("reads money in lakh and crore", () => {
    expect(lakh("1840000.00")).toBe("₹18.4 L");
    expect(lakh("12000000.00")).toBe("₹1.2 Cr");
    expect(lakh("99999.00")).toBe("₹99,999");
    expect(lakh("0.00")).toBe("₹0");
    expect(lakh(null)).toBe("–");
    expect(lakh("-250000.00")).toBe("-₹2.5 L");
  });

  it("says what is owed, and only offers to get it back to those who can claim", () => {
    const t = { buyers: 6, invoices: 11, principal: "1840000.00", interest: "53340.00" };
    expect(headline(t, true)).toBe("₹18.4 L is with 6 buyers past the due day. Here is how to get it back.");
    expect(headline({ ...t, buyers: 1 }, false)).toBe("₹18.4 L is with 1 buyer past the due day.");
    expect(headline({ buyers: 0, invoices: 0, principal: "0.00", interest: null }, true)).toBe("No buyer is past the day payment was due.");
    expect(daysLate(1)).toBe("1 day late");
    expect(daysLate(70)).toBe("70 days late");
    expect(rateNote({ bank_rate_pct: "5.50", effective_from: "2025-12-05", annual_pct: "16.50" })).toBe("Interest is three times the RBI bank rate (5.50% since 05/12/2025), so 16.50% a year, compounded monthly.");
    expect(rateNote(null)).toMatch(/No RBI bank rate/);
  });

  it("bars the ageing and marks what is past day 45", () => {
    const bars = ageBars([{ b0_30: "100.00", b31_45: "50.00", b46_60: "25.00", b61_90: "0.00", b90_plus: "10.00" }, { b0_30: "100.00", b31_45: "0.00", b46_60: "0.00", b61_90: "0.00", b90_plus: "0.00" }]);
    expect(bars.map((b) => [b.label, b.value, b.width, b.late])).toEqual([["0–30", "200.00", 100, false], ["31–45", "50.00", 25, false], ["46–60", "25.00", 12, true], ["61–90", "0.00", 0, true], ["90+", "10.00", 5, true]]);
    expect(ageBars([]).every((b) => b.width === 0)).toBe(true);
  });

  it("offers the right action for each buyer", () => {
    expect(buyerAction(buyer({}) as never, true)).toEqual({ claim: "start", treds: true, remind: true });
    expect(buyerAction(buyer({ claim: { id: "c", status: "draft" } }) as never, true).claim).toBe("file");
    expect(buyerAction(buyer({ claim: { id: "c", status: "filed" } }) as never, true).claim).toBe("view");
    expect(buyerAction(buyer({}) as never, false).claim).toBeNull();                                          // medium suppliers have no interest claim
    expect(buyerAction(buyer({ invoices: [inv({ on_treds: true })], on_treds: true }) as never, true).treds).toBe(false);
    expect(uploadable([buyer({ invoices: [inv({ document_id: "a" }), inv({ document_id: "b", on_treds: true })] })] as never).map((i) => i.document_id)).toEqual(["a"]);
    expect(claimLine({ id: "c", buyer_party_id: "p", buyer_name: "B", invoices: [{ number: "1", balance_due: "1" }], principal: "23600.00", interest_claimed: "1800.00", forum: "msme_samadhaan", status: "draft", case_no: null, filed_on: null }))
      .toBe("1 invoice · principal ₹23,600 + interest ₹1,800");
  });

  it("walks a TReDS upload forward and asks for the money only when financed", () => {
    expect(tredsNext("uploaded").map(([k]) => k)).toEqual(["accepted", "rejected"]);
    expect(tredsNext("financed").map(([k]) => k)).toEqual(["settled"]);
    expect(tredsNext("settled")).toEqual([]);
    expect(tredsNext("nonsense")).toEqual([]);
    expect(tredsProblem({ status: "accepted", rate: "", amount: "" })).toBeNull();
    expect(tredsProblem({ status: "financed", rate: "", amount: "1000" })).toMatch(/discount rate/);
    expect(tredsProblem({ status: "financed", rate: "7.5", amount: "0" })).toMatch(/amount/);
    expect(tredsProblem({ status: "financed", rate: "7.5", amount: "1100000" })).toBeNull();
    expect(tredsBody({ status: "financed", rate: " 7.5 ", amount: "1100000" })).toEqual({ status: "financed", discount_rate_pct: "7.5", amount_received: "1100000" });
    expect(tredsBody({ status: "accepted", rate: "7.5", amount: "1" })).toEqual({ status: "accepted" });
  });

  it("totals what you owe small suppliers", () => {
    const row = (balance: string, breached: boolean) => ({ bill_id: "b", legal_name: "S", msme_pay_by: "2026-11-01", days_left: 3, breached, balance_due: balance });
    expect(payablesSummary([row("100.50", false), row("200.25", true)])).toEqual({ owed: "300.75", breached: 1, count: 2 });
    expect(payablesSummary([])).toEqual({ owed: "0.00", breached: 0, count: 0 });
  });

  it("shows how much of each Udyam limit is used", () => {
    expect(udyamBar({ key: "turnover", label: "Annual turnover", value: "500000000.00", limit: "1000000000.00", pct: 50 })).toEqual({ label: "Annual turnover", value: "₹50.0 Cr of ₹100.0 Cr", width: 50 });
    expect(udyamBar({ key: "turnover", label: "Annual turnover", value: null, limit: "1000000000.00", pct: null })).toEqual({ label: "Annual turnover", value: "Not recorded", width: 0 });
    expect(udyamBar({ key: "x", label: "X", value: "1", limit: "1", pct: 140 }).width).toBe(100);
    expect(classMismatch({ declared: "small", computed: "medium" })).toBe(true);
    expect(classMismatch({ declared: "small", computed: "small" })).toBe(false);
    expect(classMismatch({ declared: null, computed: "small" })).toBe(false);
  });

  it("checks the two figures that decide the class and sends them as dated facts", () => {
    expect(factsProblem({ investment: "", turnover: "" })).toMatch(/at least one/);
    expect(factsProblem({ investment: "5,00,000", turnover: "" })).toMatch(/whole rupees/);
    expect(factsProblem({ investment: "50000000", turnover: "500000000" })).toBeNull();
    expect(factsBody({ investment: "50000000", turnover: "" }, "2026-11-08")).toEqual([{ fact_key: "investment_plant_machinery", value: 50000000, as_of: "2026-11-08" }]);
    expect(factsBody({ investment: "1", turnover: "2" }, "2026-11-08").map((f) => f.fact_key)).toEqual(["investment_plant_machinery", "aato"]);
    expect(blankFacts({ investment: "50000000.00", turnover: null })).toEqual({ investment: "50000000", turnover: "" });
    expect(blankFacts(null)).toEqual({ investment: "", turnover: "" });
  });
});

describe("Udyam registration", () => {
  it("checks the number and sends it in capitals", async () => {
    const { blankUdyam, udyamBody, udyamProblem } = await import("./msme");
    expect(udyamProblem(blankUdyam())).toMatch(/UDYAM-KA/);
    expect(udyamProblem({ number: "udyam-ka-03-0054321", cls: "small" })).toBeNull();
    expect(udyamProblem({ number: "UDYAM-KA-3-1", cls: "small" })).toMatch(/looks like/);
    expect(udyamProblem({ number: "UDYAM-KA-03-0054321", cls: "huge" })).toMatch(/class/);
    expect(udyamBody({ number: " udyam-ka-03-0054321 ", cls: "small" })).toEqual({ udyam_number: "UDYAM-KA-03-0054321", udyam_class: "small" });
  });
});
