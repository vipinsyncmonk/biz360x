import { describe, expect, it } from "vitest";
import { addDays, formatInr, greeting, initials, isoDate, shortDate, sumMoney, toPaise } from "./format";
import { daysLabel, kpis, monthGrid, standing, statusInfo, weekBuckets } from "./compliance";
import { decodeExp, needsRefresh } from "./tokens";
import { unwrapEmpty } from "./api";
import { allocatedTotal, allocationBody, allocationProblem, offsetLabel, pauseReady, receiptReady, recurringState, stepSentence, suggestAllocation, unallocated } from "./receivables";

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

import { closeCounts, currentPeriod, lakh, ledgerTotals, periodName, returnPeriod, totalOf, type Period } from "./finance";

describe("finance helpers", () => {
  const per = (start: string, status: string): Period => ({ id: start, fy: "2026-27", period_start: start, period_end: start.slice(0, 8) + "28", status, locked_at: null, close_progress: { done: 0, total: 0 } });
  it("names periods and picks the month being closed", () => {
    expect(periodName("2026-10-01")).toBe("October 2026");
    expect(returnPeriod("2026-10")).toBe("Oct 2026");
    const ps = [per("2026-09-01", "locked"), per("2026-10-01", "open"), per("2026-11-01", "open"), per("2026-12-01", "open")];
    expect(currentPeriod(ps, new Date(2026, 10, 7))?.period_start).toBe("2026-10-01");     // latest unlocked month that has ended
    expect(currentPeriod([per("2026-09-01", "locked")], new Date(2026, 10, 7))?.period_start).toBe("2026-09-01");
    expect(currentPeriod([])).toBeUndefined();
  });
  it("shows lakh to one decimal without floats", () => {
    expect(lakh("1234567.00")).toBe("12.3");
    expect(lakh("1250000.00")).toBe("12.5");
    expect(lakh("-642000.00")).toBe("-6.4");
    expect(lakh("0.00")).toBe("0.0");
    expect(lakh("-100.00")).toBe("0.0");               // rounds to zero: no negative zero
  });
  it("counts close tasks and sums GST ledgers per head", () => {
    expect(closeCounts([{ id: "a", task_key: "a", title: "a", is_automatic: false, done_at: "x" }, { id: "b", task_key: "b", title: "b", is_automatic: true, done_at: null }])).toEqual({ done: 1, total: 2 });
    const rows = [
      { registration_id: "r1", ledger: "credit", head: "igst", as_of: "2026-10-31", balance: "100.10" },
      { registration_id: "r2", ledger: "credit", head: "igst", as_of: "2026-10-31", balance: "0.20" },
      { registration_id: "r1", ledger: "cash", head: "cgst", as_of: "2026-10-31", balance: "50.00" },
    ];
    expect(ledgerTotals(rows, "credit")).toEqual({ igst: "100.30", cgst: "0.00", sgst: "0.00", cess: "0.00" });
    expect(totalOf(ledgerTotals(rows, "cash"))).toBe("50.00");
  });
});

import { nextAction, nextRunMonth, runName, stepIndex, type Run } from "./payroll";

describe("payroll helpers", () => {
  const run = (m: string, status = "paid"): Run => ({ id: m, period_month: m, status, headcount: 1, gross_total: "0.00", net_total: "0.00", employer_cost_total: "0.00", approval_request_id: null, paid_on: null, statutory: [] });
  it("names runs and chooses the next month, across a year end", () => {
    expect(runName("2026-10-01")).toBe("October 2026");
    expect(nextRunMonth([run("2026-09-01"), run("2026-10-01")])).toBe("2026-11-01");
    expect(nextRunMonth([run("2026-12-01")])).toBe("2027-01-01");
    expect(nextRunMonth([], new Date(2026, 10, 7))).toBe("2026-11-01");          // no run yet: this month
    expect(nextRunMonth([], new Date(2027, 0, 5))).toBe("2027-01-01");
  });
  it("offers the right next step", () => {
    expect(nextAction(run("2026-10-01", "inputs_open"))).toBe("compute");
    expect(nextAction(run("2026-10-01", "computed"))).toBe("submit");
    expect(nextAction({ status: "computed", approval_request_id: "a" })).toBe("waiting");
    expect(nextAction(run("2026-10-01", "approved"))).toBeNull();
    expect(stepIndex("inputs_locked")).toBe(0);
    expect(stepIndex("paid")).toBe(4);
  });
});

import { versionLabel, byUrgency, daysUntil, expiryLabel, fileExt, fileSize, nextDate, type Contract } from "./legal";

describe("legal helpers", () => {
  const c = (title: string, over: Partial<Contract> = {}): Contract => ({ id: title, counterparty_name: null, title, kind: "vendor", start_date: null, end_date: null, auto_renews: false, notice_days: null, notice_deadline: null,
    value_amount: null, value_period: null, status: "active", signed_document_id: null, terms: [], alerts: [], ...over });
  it("picks the date to watch and puts warnings first", () => {
    expect(nextDate(c("a", { auto_renews: true, notice_deadline: "2026-11-01", end_date: "2026-12-01" }))).toEqual({ label: "Notice by", date: "2026-11-01" });
    expect(nextDate(c("b", { end_date: "2026-12-01" }))).toEqual({ label: "Ends", date: "2026-12-01" });
    expect(nextDate(c("c"))).toBeNull();
    const warn = [{ code: "x", level: "warn", title: "t", detail: "d" }];
    const out = byUrgency([c("late", { end_date: "2027-01-01" }), c("soon", { end_date: "2026-12-01" }), c("warned", { end_date: "2027-06-01", alerts: warn }), c("none")]);
    expect(out.map((x) => x.title)).toEqual(["warned", "soon", "late", "none"]);
  });
  it("shows template versions once", () => { expect(versionLabel("v1")).toBe("v1"); expect(versionLabel(2)).toBe("v2"); });
  it("describes files and expiry without timezone drift", () => {
    expect(fileSize(512)).toBe("512 B");
    expect(fileSize(2048)).toBe("2 KB");
    expect(fileSize(5 * 1024 * 1024)).toBe("5.0 MB");
    expect(fileExt("GSTR-3B Oct.pdf")).toBe("PDF");
    expect(fileExt("README")).toBe("FILE");
    const today = new Date(2026, 10, 7);
    expect(daysUntil("2026-11-10", today)).toBe(3);
    expect(expiryLabel("2026-11-08", today)).toBe("Expires in 1 day");
    expect(expiryLabel("2026-11-07", today)).toBe("Expires today");
    expect(expiryLabel("2026-11-04", today)).toBe("Expired 3 days ago");
  });
});

import { byTrouble, dscStatus, groupCounts, modulesLabel, prefBody, prefKey, prefState, type Standing } from "./settings";

describe("settings and group helpers", () => {
  const s = (name: string, cells: Record<string, string>): Standing => ({ entity_id: name, legal_name: name, constitution: "private_limited", owner_staff: null, cells, open_items: [] });
  it("labels modules and certificates", () => {
    expect(modulesLabel(["*"])).toBe("All modules");
    expect(modulesLabel(["gst", "finance"])).toBe("gst, finance");
    const today = new Date(2026, 10, 7);
    expect(dscStatus("2026-11-06", today).label).toBe("Expired");
    expect(dscStatus("2027-01-05", today)).toEqual({ label: "Expires in 59 days", tone: "due" });
    expect(dscStatus("2027-06-01", today)).toEqual({ label: "Valid", tone: "ok" });
  });
  it("sends the whole notification matrix and treats an unsaved choice as off", () => {
    const state = prefState([{ entity_id: "e1", event_type: "due_tomorrow", channel: "email", enabled: true }, { entity_id: "e2", event_type: "due_tomorrow", channel: "sms", enabled: true }], "e1");
    expect(state).toEqual({ [prefKey("due_tomorrow", "email")]: true });
    const body = prefBody("e1", state);
    expect(body).toHaveLength(24);                                           // 6 events x 4 channels
    expect(body.filter((b) => b.enabled)).toEqual([{ entity_id: "e1", event_type: "due_tomorrow", channel: "email", enabled: true }]);
  });
  it("counts clients and puts trouble first", () => {
    const rows = [s("Calm", { GST: "clear" }), s("Waiting", { GST: "client", MCA: "due" }), s("Late", { MCA: "overdue" }), s("Also late", { TDS: "overdue", GST: "client" })];
    expect(groupCounts(rows)).toEqual({ clients: 4, waiting: 2, overdue: 2 });
    expect(byTrouble(rows).map((r) => r.legal_name)).toEqual(["Also late", "Late", "Waiting", "Calm"]);
  });
});

import { creditLines, creditProblem, creditReady, quoteActions, quoteBody, quoteReady, type DocLine, type QuoteForm } from "./billing";

describe("quotes and credit notes", () => {
  const line = { description: "Transmitter", hsn_sac: "902620", qty: "1", rate: "1000", gst_rate: "18", discount: "0" };
  const form = (over: Partial<QuoteForm> = {}): QuoteForm => ({ registrationId: "r", partyId: "p", partyGstId: "", title: "  Lot 2 ", validUntil: "", terms: "", advance: "", lines: [line], ...over });
  it("builds the quote body and checks the optional fields", () => {
    expect(quoteReady(form())).toBe(true);
    expect(quoteReady(form({ advance: "101" }))).toBe(false);
    expect(quoteReady(form({ advance: "25.5", terms: "45" }))).toBe(true);
    expect(quoteReady(form({ partyId: "" }))).toBe(false);
    const b = quoteBody(form({ advance: "25", terms: "45", validUntil: "2026-12-31" }));
    expect(b).toMatchObject({ title: "Lot 2", valid_until: "2026-12-31", payment_terms_days: 45, advance_pct: 25 });
    expect("title" in quoteBody(form({ title: " " }))).toBe(false);
  });
  it("offers the right actions for each status", () => {
    expect(quoteActions({ status: "draft", sales_order_id: null, invoiced_pct: 0 })).toEqual(["send", "revise", "lost"]);
    expect(quoteActions({ status: "negotiating", sales_order_id: null, invoiced_pct: 0 })).toEqual(["accept", "lost"]);
    expect(quoteActions({ status: "accepted", sales_order_id: "o", invoiced_pct: 40 })).toEqual(["invoice"]);
    expect(quoteActions({ status: "accepted", sales_order_id: "o", invoiced_pct: 100 })).toEqual([]);
    expect(quoteActions({ status: "lost", sales_order_id: null, invoiced_pct: 0 })).toEqual([]);
    expect(quoteActions({ status: "superseded", sales_order_id: null, invoiced_pct: 0 })).toEqual([]);
  });
  const inv: DocLine[] = [{ line_no: 1, description: "A", hsn_sac: "902620", qty: "17", uqc: "NOS", rate: "1000.00", discount: "170.00", gst_rate: "18.00" }, { line_no: 2, description: "B", hsn_sac: "9987", qty: "1", uqc: "NOS", rate: "500.00", discount: "0.00", gst_rate: 18 }];
  it("validates credit quantities against what was supplied", () => {
    expect(creditProblem("", "17")).toBeNull();
    expect(creditProblem("0", "17")).toMatch(/above zero/);
    expect(creditProblem("abc", "17")).toMatch(/above zero/);
    expect(creditProblem("17", "17")).toBeNull();
    expect(creditProblem("17.5", "17")).toMatch(/No more than 17/);
    expect(creditReady(inv, {})).toBe(false);
    expect(creditReady(inv, { 1: "2" })).toBe(true);
    expect(creditReady(inv, { 1: "2", 2: "5" })).toBe(false);
  });
  it("credits at the invoice's rate with the discount in proportion", () => {
    const [a] = creditLines(inv, { 1: "2" });
    expect(a).toMatchObject({ qty: "2", rate: "1000.00", gst_rate: 18, discount: "20.00" });          // 170.00 x 2 / 17
    expect(creditLines(inv, { 2: "1" })[0]).toMatchObject({ discount: "0.00" });
    expect(creditLines(inv, { 1: "1" })[0]!.discount).toBe("10.00");
    expect(creditLines(inv, { 1: "  " })).toEqual([]);
  });
});

describe("receipts and reminders", () => {
  const open = [
    { id: "new", number: "NI/KA/9", doc_date: "2026-10-20", due_date: "2026-11-19", balance_due: "5000.00" },
    { id: "old", number: "NI/KA/3", doc_date: "2026-08-01", due_date: "2026-08-31", balance_due: "1200.50" },
  ];
  it("suggests the oldest invoice first and never past a balance", () => {
    expect(suggestAllocation("3000.00", open)).toEqual({ old: "1200.50", new: "1799.50" });
    expect(suggestAllocation("500", open)).toEqual({ old: "500.00" });
    expect(suggestAllocation("99999", open)).toEqual({ old: "1200.50", new: "5000.00" });
    expect(suggestAllocation("", open)).toEqual({});
  });
  it("adds exactly and shows what stays as an advance", () => {
    expect(allocatedTotal({ a: "0.10", b: "0.20" })).toBe("0.30");
    expect(unallocated("1000.00", { a: "250.25" })).toBe("749.75");
    expect(unallocated("100.00", { a: "250.25" })).toBe("0.00");
  });
  it("flags what the server would refuse", () => {
    expect(allocationProblem("3000.00", { old: "1200.50", new: "1799.50" }, open)).toBeNull();
    expect(allocationProblem("3000.00", { old: "1300.00" }, open)).toMatch(/more than the 1200.50/);
    expect(allocationProblem("1000.00", { old: "1200.50" }, open)).toMatch(/more than the receipt/);
    expect(allocationProblem("1000.00", { old: "12x" }, open)).toMatch(/amount like/);
  });
  it("sends only invoices that have an amount", () => {
    expect(allocationBody({ a: "100", b: "", c: "0.00", d: "5.5" })).toEqual([{ invoice_id: "a", amount: "100.00" }, { invoice_id: "d", amount: "5.50" }]);
  });
  it("needs a customer, a date and a positive amount", () => {
    expect(receiptReady({ partyId: "p", date: "2026-11-07", amount: "10" })).toBe(true);
    expect(receiptReady({ partyId: "", date: "2026-11-07", amount: "10" })).toBe(false);
    expect(receiptReady({ partyId: "p", date: "2026-11-07", amount: "0" })).toBe(false);
  });
  it("words the reminder steps", () => {
    expect(offsetLabel(-3)).toBe("3 days before the due date");
    expect(offsetLabel(0)).toBe("On the due date");
    expect(offsetLabel(1)).toBe("1 day after the due date");
    expect(stepSentence({ offset_days: 7, action: "message", channels: ["email", "whatsapp"] })).toBe("7 days after the due date: send a reminder by email and WhatsApp");
    expect(stepSentence({ offset_days: 15, action: "task" })).toBe("15 days after the due date: create a task");
  });
  it("only pauses until a future date", () => {
    const today = new Date(2026, 10, 7);
    expect(pauseReady("2026-11-08", today)).toBe(true);
    expect(pauseReady("2026-11-07", today)).toBe(false);
    expect(pauseReady("", today)).toBe(false);
  });
  it("names the state of a recurring invoice", () => {
    const today = new Date(2026, 10, 7);
    expect(recurringState({ is_active: false, next_run_on: "2026-12-01", ends_on: null }, today).label).toBe("Paused");
    expect(recurringState({ is_active: true, next_run_on: "2026-12-01", ends_on: "2026-10-01" }, today).label).toBe("Ended");
    expect(recurringState({ is_active: true, next_run_on: "2026-11-07", ends_on: null }, today).label).toBe("Due now");
    expect(recurringState({ is_active: true, next_run_on: "2026-12-01", ends_on: null }, today).label).toBe("Active");
  });
});

describe("empty responses", () => {
  it("accepts a 204 and refuses a failure", () => {
    expect(() => unwrapEmpty({ response: new Response(null, { status: 204 }) })).not.toThrow();
    expect(() => unwrapEmpty({ error: { code: "not_found", title: "Not found" }, response: new Response(null, { status: 404 }) })).toThrow(/Not found/);
    expect(() => unwrapEmpty({ response: new Response(null, { status: 500 }) })).toThrow();
  });
});
