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
