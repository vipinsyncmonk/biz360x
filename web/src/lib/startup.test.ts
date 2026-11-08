import { describe, expect, it } from "vitest";
import {
  allotmentBody, allotmentProblem, barWidths, benefitBody, benefitsChecked, blankAllotment, blankBenefit, blankClass, blankGrant, blankHolder, blankRound, classBody, classProblem,
  currentRound, esopSummary, grantBody, grantProblem, groupIndian, holderBody, holderProblem, investorUpdate, nextStatus, poolBody, poolProblem, roundBody, roundHeadline, roundProblem,
  roundSummary, stepsLeft,
} from "./startup";

const round = (over: object) => ({
  id: "r", name: "Seed extension", share_class_id: null, route: "private_placement", target_amount: "5000000.00", pre_money_valuation: "18000000.00", term_sheet_signed_on: null,
  shareholder_approval_on: null, closed_on: null, status: "diligence",
  steps: [{ key: "a", title: "A", status: "done", detail: "" }, { key: "b", title: "B", status: "in_progress", detail: "" }, { key: "c", title: "C", status: "next", detail: "" }], ...over,
});

describe("startup track", () => {
  it("groups share counts the Indian way", () => {
    expect(groupIndian(1234567)).toBe("12,34,567");
    expect(groupIndian(999)).toBe("999");
    expect(groupIndian(100000)).toBe("1,00,000");
    expect(groupIndian(-2500)).toBe("-2,500");
    expect(groupIndian(0)).toBe("0");
  });

  it("scales the bars to the biggest holder", () => {
    expect(barWidths([{ fd_pct: 50 }, { fd_pct: 25 }, { fd_pct: 0 }])).toEqual([100, 50, 0]);
    expect(barWidths([])).toEqual([]);
  });

  it("follows the open round and says how far it is from done", () => {
    const rounds = [round({ id: "closed", status: "closed" }), round({ id: "open" })];
    expect(currentRound(rounds as never)?.id).toBe("open");
    expect(currentRound([round({ status: "closed" })] as never)).toBeNull();
    expect(stepsLeft(round({}) as never)).toBe(2);
    expect(roundHeadline(round({}) as never)).toBe("Seed extension is 2 steps from done.");
    expect(roundHeadline(round({ steps: [{ key: "a", title: "A", status: "next", detail: "" }] }) as never)).toBe("Seed extension is 1 step from done.");
    expect(roundHeadline(round({ steps: [{ key: "a", title: "A", status: "done", detail: "" }] }) as never)).toBe("Seed extension is complete.");
    expect(roundHeadline(null)).toMatch(/cap table/);
    expect(roundSummary(round({}) as never)).toBe("Private placement · ₹50,00,000 target · ₹1,80,00,000 pre-money");
    expect(nextStatus("planning")).toEqual(["term_sheet", "Term sheet"]);
    expect(nextStatus("allotment")).toEqual(["closed", "Closed"]);
    expect(nextStatus("closed")).toBeNull();
  });

  it("sums the pools", () => {
    const pools = [{ id: "1", name: "A", approved_on: "2025-01-01", size_options: 1000, granted: 400, free: 600 }, { id: "2", name: "B", approved_on: "2026-01-01", size_options: 500, granted: 0, free: 500 }];
    expect(esopSummary(pools, 15000)).toEqual({ size: 1500, granted: 400, free: 1100, pctOfFd: 10 });
    expect(esopSummary([], 100).pctOfFd).toBeNull();
  });

  it("checks each setup form and sends exact values", () => {
    expect(classProblem(blankClass())).toMatch(/Name the class/);
    expect(classProblem({ ...blankClass(), name: "Equity" })).toBeNull();
    expect(classProblem({ ...blankClass(), name: "E", face: "0" })).toMatch(/Face value/);
    expect(classBody({ ...blankClass(), name: " Equity " })).toEqual({ name: "Equity", instrument: "equity", face_value: "10", conversion_ratio: "1" });

    expect(holderProblem({ ...blankHolder(), name: "K", pan: "abc" })).toMatch(/PAN/);
    expect(holderProblem({ ...blankHolder(), name: "K", country: "India" })).toMatch(/country/);
    expect(holderBody({ ...blankHolder(), name: "Fund", country: "sg", kind: "investor" })).toEqual({ name: "Fund", kind: "investor", is_non_resident: true, country: "SG" });
    expect(holderBody({ ...blankHolder(), name: "K", pan: "abcpk1234f" })).toEqual({ name: "K", kind: "founder", pan: "ABCPK1234F", is_non_resident: false, country: "IN" });

    const a = { ...blankAllotment(), holderId: "h", classId: "c", quantity: "1000", price: "2000", received: "2026-11-01", allotted: "2026-11-05" };
    expect(allotmentProblem(a, "2026-11-08")).toBeNull();
    expect(allotmentProblem({ ...a, quantity: "1.5" }, "2026-11-08")).toMatch(/whole number/);
    expect(allotmentProblem({ ...a, price: "0" }, "2026-11-08")).toMatch(/issue price/);
    expect(allotmentProblem({ ...a, allotted: "2026-12-01" }, "2026-11-08")).toMatch(/future/);
    expect(allotmentProblem({ ...a, received: "2026-08-01", allotted: "2026-11-05" }, "2026-11-08")).toMatch(/60 days/);
    expect(allotmentProblem({ ...a, received: "2026-11-05", allotted: "2026-11-01" }, "2026-11-08")).toMatch(/before the money/);
    expect(allotmentBody(a)).toEqual({ shareholder_id: "h", share_class_id: "c", quantity: 1000, issue_price: "2000", money_received_on: "2026-11-01", allotted_on: "2026-11-05" });

    expect(roundProblem(blankRound())).toMatch(/Name the round/);
    expect(roundProblem({ ...blankRound(), name: "Seed", target: "abc" })).toMatch(/target/);
    expect(roundBody({ ...blankRound(), name: "Seed", classId: "c", target: "5000000", preMoney: "18000000", termSheet: "2026-10-14" }))
      .toEqual({ name: "Seed", share_class_id: "c", target_amount: "5000000", pre_money_valuation: "18000000", term_sheet_signed_on: "2026-10-14" });
    expect(roundBody({ ...blankRound(), name: "Idea" })).toEqual({ name: "Idea" });

    expect(poolProblem({ name: "ESOP", approved: "", size: "100" })).toMatch(/approved/);
    expect(poolBody({ name: "ESOP", approved: "2025-01-01", size: "2000" })).toEqual({ name: "ESOP", approved_on: "2025-01-01", size_options: 2000 });

    const g = { ...blankGrant(), poolId: "p", employeeId: "e", grantedOn: "2026-01-01", options: "480", strike: "10" };
    expect(grantProblem(g, 1000)).toBeNull();
    expect(grantProblem(g, 100)).toMatch(/Only 100 options/);
    expect(grantProblem({ ...g, cliff: "60", vesting: "48" }, null)).toMatch(/cliff/);
    expect(grantProblem({ ...g, strike: "-1" }, null)).toMatch(/strike/);
    expect(grantBody(g)).toEqual({ pool_id: "p", employee_id: "e", granted_on: "2026-01-01", options: 480, strike_price: "10", cliff_months: 12, vesting_months: 48, vesting_frequency: "monthly" });
  });

  it("records a benefit and knows when they have been checked", () => {
    expect(blankBenefit({ status: "eligible", reference_no: null, valid_until: null })).toEqual({ status: "applied", reference: "", validUntil: "" });
    expect(blankBenefit({ status: "granted", reference_no: "D1", valid_until: "2034-01-01" })).toEqual({ status: "granted", reference: "D1", validUntil: "2034-01-01" });
    expect(benefitBody({ status: "granted", reference: " D1 ", validUntil: "" })).toEqual({ status: "granted", reference_no: "D1", valid_until: null });
    const b = (status: string) => ({ scheme_code: "X", name: "X", kind: "k", status, reason: "", reference_no: null, valid_until: null, next_action: null, info_url: null });
    expect(benefitsChecked([b("eligible"), b("not_eligible")])).toBe(true);
    expect(benefitsChecked([b("not_checked")])).toBe(false);
    expect(benefitsChecked([])).toBe(false);
  });

  it("drafts the investor update without inventing numbers", () => {
    const text = investorUpdate({ company: "Orbit Labs", month: "October 2026", cash: "42000000.00", burn: "2200000.00", runway: 19.1, revenue: "9500000.00", team: 24, roundLine: "Seed extension is 2 steps from done.", note: "" });
    expect(text).toContain("Orbit Labs: investor update, October 2026");
    expect(text).toContain("- Cash in bank: ₹4,20,00,000");
    expect(text).toContain("- Runway: 19.1 months");
    expect(text).toContain("Seed extension is 2 steps from done.");
    expect(text).toContain("[Highlights, asks and hires. Written by you.]");
    const none = investorUpdate({ company: "X", month: "M", cash: null, burn: null, runway: null, revenue: null, team: null, roundLine: null, note: "Hired two." });
    expect(none).toContain("- Cash in bank: not available");
    expect(none).toContain("- Net burn per month: none (cash did not fall)");
    expect(none).toContain("Hired two.");
    expect(none).not.toContain("Fundraise");
  });
});
