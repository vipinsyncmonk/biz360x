import { describe, expect, it } from "vitest";
import { addDays, formatInr, greeting, initials, isoDate, shortDate, sumMoney, toPaise } from "./format";
import { clauseText, dueRuleText, daysLabel, factLabel, kpis, monthGrid, reasonLines, standing, statusInfo, weekBuckets } from "./compliance";
import { decodeExp, needsRefresh } from "./tokens";
import { unwrapEmpty } from "./api";
import { actionItems, actionResult, actionState, actionTitle, answerBlocks, askReady, canRequestApproval, citationHref, groupThreads } from "./copilot";
import { DEPOSIT_RULE, depositNote, depositOptions, plainError, blankChallan, canPay, canValidate, challanBody, challanProblem, deductionTotals, fyChoices, fyOf, instalmentState, nextInstalment, unmatched, validationNote } from "./tds";
import { ACTION_LABEL, canCompute, canFile, fileBody, isOpen, lastMonth, matchActions, periodOk, remindLabel, remindable, resolveBody, syncBody, taxNote } from "./gst";
import { SKIP_REASON, addClientHref, chaseLabel, chaseSummary, chaseTargets, orgFromSearch } from "./group";
import { badEmails, blankRoom, expiryNote, parseEmails, roomBody, roomForm, roomProblem, viewerNote } from "./rooms";
import { AUTHORITY_LABEL, blankNotice, canDraft, deskTotals, noticeBody, noticeMoves, noticeProblem, replyState } from "./notices";
import { MAX_UPLOAD_BYTES, editBody, editProblem, mimeOf, registerBody, sameOriginUrl, sha256Hex, shareable, titleFromFile, toBase64, toHex, uploadProblem } from "./vault";
import { blankContract, blankMeeting, blankResolution, contractBody, contractProblem, gapWarning, heldProblem, istDate, istTime, meetingBody, meetingReady, meetingState, nextResolutionNumber, resolutionActions, resolutionBody, resolutionReady, termKeyFrom, termKeyOk, termReady, CONTRACT_NEXT } from "./board";
import { blankConn, blankHook, canRenew, connBody, connReady, deliveryState, eventsLabel, hookBody, hookProblem, jobStage, otpReady } from "./integrations";
import { annualOf, blankEmployee, defaultComponents, draftFromRows, employeeBody, employeeProblem, inputProblem, inputsBody, inputsProblem, monthlyEarnings, structureBody, structureProblem } from "./payroll";
import { orderMethods, overdueLabel, pageState, queryReady } from "./pay";
import { blankDsc, blankGrant, blankInvite, dscBody, dscReady, emailOk, grantBody, grantReady, inviteBody, inviteReady, keyPermissions, keyReady, permissionLabel, scopesBody } from "./settings";
import { NAV, TABS, activeTab } from "../components/nav";
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
  const s = (name: string, cells: Record<string, string>): Standing => ({ entity_id: name, legal_name: name, constitution: "private_limited", owner_staff: null, waiting_filings: Object.values(cells).includes("client") ? 1 : 0, cells, open_items: [] });
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

describe("phone tabs", () => {
  it("highlights the tab a screen belongs to, and More for the rest", () => {
    expect(activeTab("/")).toBe("today");
    expect(activeTab("/compliance")).toBe("compliance");
    expect(activeTab("/compliance/abc")).toBe("compliance");
    expect(activeTab("/copilot")).toBe("copilot");
    expect(activeTab("/billing/receivables")).toBe("more");
    expect(activeTab("/more")).toBe("more");
    expect(activeTab("/m/startup")).toBe("more");
  });
  it("every tab leads somewhere", () => {
    const known = new Set([...NAV.flatMap((g) => g.items.map((i) => i.href)), "/more"]);
    for (const t of TABS) expect(known.has(t.href)).toBe(true);
  });
});

describe("customer payment page", () => {
  it("lists the allowed methods with bank transfer first and drops unknown ones", () => {
    expect(orderMethods(["card", "upi", "bank_transfer"])).toEqual(["bank_transfer", "upi", "card"]);
    expect(orderMethods(["wire"])).toEqual([]);
    expect(orderMethods(undefined)).toEqual([]);
  });
  it("words lateness and picks the view", () => {
    expect(overdueLabel(0)).toBeNull();
    expect(overdueLabel(1)).toBe("1 day overdue");
    expect(overdueLabel(27)).toBe("27 days overdue");
    expect(pageState({ status: "due" })).toBe("due");
    expect(pageState({ status: "paid" })).toBe("paid");
    expect(pageState({ status: "cancelled" })).toBe("closed");
  });
  it("needs a real email and a message for a query", () => {
    expect(queryReady({ email: "a@b.in", message: "Wrong GSTIN" })).toBe(true);
    expect(queryReady({ email: "a@b", message: "x" })).toBe(false);
    expect(queryReady({ email: "a@b.in", message: "   " })).toBe(false);
  });
});

describe("settings write actions", () => {
  it("checks an email loosely and the invite fully", () => {
    expect(emailOk("a@b.in")).toBe(true);
    expect(emailOk("a b@c.in")).toBe(false);
    expect(inviteReady({ ...blankInvite(), email: "x@y.in" })).toBe(true);
    expect(inviteReady({ ...blankInvite(), email: "x" })).toBe(false);
  });
  it("sends every module when none is ticked", () => {
    expect(inviteBody({ ...blankInvite(), email: " x@y.in ", name: " Nina " })).toEqual({ email: "x@y.in", role: "maker", modules: ["*"], full_name: "Nina" });
    expect(inviteBody({ ...blankInvite(), email: "x@y.in", modules: ["gst"] }).modules).toEqual(["gst"]);
  });
  it("needs a name, at least one scope and a future end date for an advisor", () => {
    const g = { ...blankGrant(), name: "Rao and Co", levels: { gst: "prepare", finance: "" } };
    expect(scopesBody(g.levels)).toEqual({ gst: "prepare" });
    expect(grantReady(g, "2026-11-07")).toBe(true);
    expect(grantReady({ ...g, expires: "2026-11-07" }, "2026-11-07")).toBe(false);
    expect(grantReady({ ...g, expires: "2026-12-01" }, "2026-11-07")).toBe(true);
    expect(grantReady({ ...g, levels: { gst: "" } }, "2026-11-07")).toBe(false);
    expect(grantReady({ ...g, email: "nope" }, "2026-11-07")).toBe(false);
    expect(grantBody({ ...g, email: "ca@rao.in", expires: "2026-12-01" })).toEqual({ advisor_name: "Rao and Co", advisor_kind: "chartered_accountant", scopes: { gst: "prepare" }, invite_email: "ca@rao.in", expires_on: "2026-12-01" });
  });
  it("needs dates in order and a serial for a certificate", () => {
    const d = { ...blankDsc(), person: "Director", serial: "5A3F", from: "2026-01-01", until: "2028-01-01" };
    expect(dscReady(d)).toBe(true);
    expect(dscReady({ ...d, until: "2026-01-01" })).toBe(false);
    expect(dscReady({ ...d, serial: "5A" })).toBe(false);
    expect(dscBody(d)).toMatchObject({ person_name: "Director", valid_until: "2028-01-01", serial: "5A3F", registered_on: [] });
  });
  it("turns access choices into key permissions and never offers approve, file or pay", () => {
    expect(keyPermissions({ sales: "write", gst: "read", payroll: "" })).toEqual(["gst:read", "sales:read", "sales:write"]);
    expect(keyReady("Zapier", { sales: "read" })).toBe(true);
    expect(keyReady("", { sales: "read" })).toBe(false);
    expect(keyReady("Zapier", {})).toBe(false);
    expect(permissionLabel(["sales:read", "sales:write", "gst:read"])).toBe("Sales & billing read/write, GST read");
  });
});

describe("payroll employees and inputs", () => {
  const ok = { ...blankEmployee(), code: "EMP-1", name: "Meera Iyer", joined: "2026-04-01", state: "29" };
  it("checks an employee form the way the server does", () => {
    expect(employeeProblem(ok)).toBeNull();
    expect(employeeProblem({ ...ok, code: " " })).toMatch(/code/);
    expect(employeeProblem({ ...ok, state: "Karnataka" })).toMatch(/state code/);
    expect(employeeProblem({ ...ok, pan: "abcde1234f" })).toBeNull();
    expect(employeeProblem({ ...ok, pan: "ABC" })).toMatch(/PAN/);
    expect(employeeProblem({ ...ok, uan: "123" })).toMatch(/UAN/);
    expect(employeeProblem({ ...ok, account: "123456789" })).toMatch(/both/);
    expect(employeeProblem({ ...ok, account: "123456789", ifsc: "hdfc0001234" })).toBeNull();
    expect(employeeProblem({ ...ok, account: "12", ifsc: "HDFC0001234" })).toMatch(/6 to 18/);
  });
  it("sends only what was filled, upper-casing PAN and IFSC", () => {
    expect(employeeBody({ ...ok, pan: "abcde1234f", account: "123456789", ifsc: "hdfc0001234" })).toEqual({
      employee_code: "EMP-1", full_name: "Meera Iyer", date_of_joining: "2026-04-01", work_state: "29", tax_regime: "new", employment_type: "permanent",
      pan: "ABCDE1234F", bank_account: { account_no: "123456789", ifsc: "HDFC0001234" },
    });
  });
  const comps = () => defaultComponents().map((c, i) => ({ ...c, amount: ["25000", "10000", "5000.50"][i]! }));
  it("adds a salary structure exactly and demands basic pay", () => {
    expect(annualOf(comps())).toBe("480006.00");
    expect(structureProblem("2026-11-01", comps())).toBeNull();
    expect(structureProblem("", comps())).toMatch(/starts/);
    expect(structureProblem("2026-11-01", comps().map((c) => ({ ...c, in_wages: false })))).toMatch(/Basic pay/);
    expect(structureProblem("2026-11-01", [...comps(), { ...comps()[0]!, code: "basic" }])).toMatch(/twice/);
    expect(structureProblem("2026-11-01", comps().map((c, i) => (i === 1 ? { ...c, amount: "abc" } : c)))).toMatch(/monthly amount/);
    expect(structureBody("2026-11-01", comps())).toMatchObject({ effective_from: "2026-11-01", ctc_annual: "480006.00" });
    expect(structureBody("2026-11-01", comps()).components[2]).toEqual({ code: "SPECIAL", monthly_amount: "5000.50", kind: "earning", in_wages: false, taxable: true });
    expect(monthlyEarnings(structureBody("2026-11-01", comps()).components)).toBe("40000.50");
  });
  it("validates inputs and drops blanks and zeros", () => {
    expect(inputProblem("lop_days", "3")).toBeNull();
    expect(inputProblem("lop_days", "32")).toMatch(/0 to 31/);
    expect(inputProblem("lop_days", "1.5")).toBeNull();
    expect(inputProblem("overtime", "2,000")).toMatch(/amount/);
    expect(inputProblem("overtime", "")).toBeNull();
    expect(inputsProblem({ a: { overtime: "x" } })).toBe(true);
    expect(inputsProblem({ a: { overtime: "100" } })).toBe(false);
    expect(inputsBody({ a: { lop_days: "3", overtime: "" }, b: { bonus: "0" }, c: { overtime: "2000", other_deductions: "500.5" } })).toEqual([
      { employee_id: "a", lop_days: "3", overtime: "0", bonus: "0", reimbursements: "0", other_deductions: "0" },
      { employee_id: "c", lop_days: "0", overtime: "2000.00", bonus: "0", reimbursements: "0", other_deductions: "500.50" },
    ]);
  });
  it("shows saved inputs without the zeros", () => {
    expect(draftFromRows([{ employee_id: "a", lop_days: "3", overtime: "0.00", bonus: "0.00", reimbursements: "0.00", other_deductions: "500.00" }])).toEqual({
      a: { lop_days: "3", overtime: "", bonus: "", reimbursements: "", other_deductions: "500.00" },
    });
  });
});

describe("portals and webhooks", () => {
  it("sends credentials only when both parts are given", () => {
    const f = blankConn();
    expect(connReady(f)).toBe(true);
    expect(connReady({ ...f, username: "m6.gst" })).toBe(false);
    expect(connReady({ ...f, password: "x" })).toBe(false);
    expect(connBody(f)).toEqual({ portal: "gstn" });
    expect(connBody({ ...f, via: "GSP", username: " m6.gst ", password: "pw" })).toEqual({ portal: "gstn", via: "GSP", credentials: { username: "m6.gst", password: "pw" } });
    expect(connReady({ ...f, portal: "nope" })).toBe(false);
  });
  it("offers renewal only for OTP portals that are connected", () => {
    expect(canRenew({ portal: "gstn", status: "expiring" })).toBe(true);
    expect(canRenew({ portal: "gstn", status: "not_connected" })).toBe(false);
    expect(canRenew({ portal: "gstn", status: "revoked" })).toBe(false);
    expect(canRenew({ portal: "udyam", status: "connected" })).toBe(false);
  });
  it("describes a renewal job", () => {
    expect(jobStage({ status: "queued", error: null }).finished).toBe(false);
    expect(jobStage({ status: "waiting_for_otp", error: null })).toMatchObject({ needsOtp: true, finished: false });
    expect(jobStage({ status: "succeeded", error: null })).toMatchObject({ label: "Renewed", finished: true });
    expect(jobStage({ status: "failed", error: "OTP expired" }).label).toBe("Failed: OTP expired");
    expect(jobStage({ status: "failed", error: { code: "otp_invalid", title: "OTP incorrect", detail: "The OTP is incorrect" } }).label).toBe("Failed: The OTP is incorrect");
    expect(jobStage({ status: "failed", error: null }).label).toBe("Failed");
    expect(otpReady("123456")).toBe(true);
    expect(otpReady("12345")).toBe(false);
    expect(otpReady("12345a")).toBe(false);
  });
  it("needs an https address and some events", () => {
    const f = { ...blankHook(), url: "https://example.in/hook", events: ["sales_document.paid"] };
    expect(hookProblem(f)).toBeNull();
    expect(hookProblem({ ...f, url: "http://example.in/hook" })).toMatch(/https/);
    expect(hookProblem({ ...f, url: "not a url" })).toMatch(/full address/);
    expect(hookProblem({ ...f, url: "https://u:p@example.in/" })).toMatch(/user name/);
    expect(hookProblem({ ...f, events: [] })).toMatch(/event/);
    expect(hookProblem({ ...f, events: [], all: true })).toBeNull();
    expect(hookBody({ ...f, events: ["b.x", "a.y"] })).toEqual({ url: "https://example.in/hook", events: ["a.y", "b.x"] });
    expect(hookBody({ ...f, all: true }).events).toEqual(["*"]);
    expect(eventsLabel(["*"])).toBe("All events");
    expect(eventsLabel(["a.b", "c.d"])).toBe("a.b, c.d");
    expect(eventsLabel(["a.b", "c.d", "e.f"])).toBe("3 events");
  });
  it("describes a delivery attempt", () => {
    expect(deliveryState({ delivered_at: "2026-11-07T10:00:00Z", status_code: 200, next_retry_at: null, error: null })).toMatchObject({ label: "Delivered (200)", tone: "ok" });
    expect(deliveryState({ delivered_at: null, status_code: 500, next_retry_at: "2026-11-07T11:00:00Z", error: null }).label).toBe("Will retry after 500");
    expect(deliveryState({ delivered_at: null, status_code: 404, next_retry_at: null, error: null }).tone).toBe("due");
    expect(deliveryState({ delivered_at: null, status_code: null, next_retry_at: null, error: null }).label).toBe("Pending");
  });
});

describe("contracts, meetings and resolutions", () => {
  const c = { ...blankContract(), title: "AMC with Coastal Grid" };
  it("checks a contract form the way the server does", () => {
    expect(contractProblem(c)).toBeNull();
    expect(contractProblem({ ...c, title: " " })).toMatch(/title/);
    expect(contractProblem({ ...c, start: "2026-05-01", end: "2026-04-01" })).toMatch(/before/);
    expect(contractProblem({ ...c, autoRenews: true })).toMatch(/end date/);
    expect(contractProblem({ ...c, autoRenews: true, end: "2027-03-31", noticeDays: "400" })).toMatch(/notice/);
    expect(contractProblem({ ...c, autoRenews: true, end: "2027-03-31", renewalMonths: "0" })).toMatch(/Renewal/);
    expect(contractProblem({ ...c, value: "12,000" })).toMatch(/amount/);
    expect(contractProblem({ ...c, terms: "abc" })).toMatch(/days/);
  });
  it("sends notice and renewal only for auto-renewing contracts", () => {
    expect(contractBody(c)).toEqual({ title: "AMC with Coastal Grid", kind: "customer", status: "draft", auto_renews: false, governing_law: "India" });
    expect(contractBody({ ...c, autoRenews: true, end: "2027-03-31", value: "250000.5", terms: "30", counterparty: " Coastal " })).toMatchObject({
      auto_renews: true, notice_days: 60, renewal_months: 12, value_amount: "250000.50", value_period: "annual", payment_terms_days: 30, counterparty_name: "Coastal", end_date: "2027-03-31",
    });
  });
  it("offers the next steps for each status", () => {
    expect(CONTRACT_NEXT.draft!.map(([k]) => k)).toContain("out_for_signature");
    expect(CONTRACT_NEXT.terminated).toEqual([]);
    expect(CONTRACT_NEXT.active!.map(([k]) => k)).toEqual(["renewed", "expired", "terminated"]);
  });
  it("turns a label into a term key", () => {
    expect(termKeyFrom("Late payment interest")).toBe("late_payment_interest");
    expect(termKeyFrom("  Liability cap (INR)! ")).toBe("liability_cap_inr");
    expect(termKeyFrom("12 months")).toBe("months");
    expect(termKeyOk("late_payment_interest")).toBe(true);
    expect(termKeyOk("x")).toBe(false);
    expect(termReady("Late payment interest", "18% a year")).toBe(true);
    expect(termReady("Late payment interest", " ")).toBe(false);
    expect(termReady("!!", "x")).toBe(false);
  });
  it("sends a meeting in IST and describes where it stands", () => {
    expect(meetingReady(blankMeeting())).toBe(false);
    const f = { ...blankMeeting(), date: "2026-12-05" };
    expect(meetingReady(f)).toBe(true);
    expect(meetingBody(f)).toEqual({ kind: "board", scheduled_for: "2026-12-05T11:00:00+05:30" });
    expect(meetingState({ held_on: null, quorum_met: null, scheduled_for: "2026-12-05T11:00:00+05:30" }, "2026-11-07").label).toBe("Scheduled");
    expect(meetingState({ held_on: null, quorum_met: null, scheduled_for: "2026-10-05T11:00:00+05:30" }, "2026-11-07").label).toBe("Date passed, not marked held");
    expect(meetingState({ held_on: "2026-10-05", quorum_met: true, scheduled_for: "2026-10-05T11:00:00+05:30" }, "2026-11-07").label).toBe("Held, quorum met");
    expect(meetingState({ held_on: "2026-10-05", quorum_met: false, scheduled_for: "2026-10-05T11:00:00+05:30" }, "2026-11-07").tone).toBe("due");
    expect(meetingState({ held_on: "2026-10-05", quorum_met: null, scheduled_for: "2026-10-05T11:00:00+05:30" }, "2026-11-07").label).toBe("Held, quorum not recorded");
    expect(heldProblem("", "2026-11-07")).toMatch(/date/);
    expect(heldProblem("2026-11-08", "2026-11-07")).toMatch(/future/);
    expect(heldProblem("2026-11-07", "2026-11-07")).toBeNull();
  });
  it("shows a meeting time in India even though the server sends UTC", () => {
    expect(istTime("2026-12-05T05:30:00+00:00")).toBe("11:00");
    expect(istDate("2026-12-05T05:30:00+00:00")).toBe("2026-12-05");
    expect(istDate("2026-12-05T20:00:00Z")).toBe("2026-12-06");         // 01:30 next morning in India
    expect(istTime("2026-12-05T20:00:00Z")).toBe("01:30");
    expect(meetingState({ held_on: null, quorum_met: null, scheduled_for: "2026-11-06T20:00:00Z" }, "2026-11-07").label).toBe("Scheduled");   // still today in India
  });
  it("flags a long gap between board meetings only for board meetings", () => {
    expect(gapWarning({ kind: "board", days_since_last_board: 121 })).toBe(true);
    expect(gapWarning({ kind: "board", days_since_last_board: 120 })).toBe(false);
    expect(gapWarning({ kind: "board", days_since_last_board: null })).toBe(false);
    expect(gapWarning({ kind: "agm", days_since_last_board: 400 })).toBe(false);
  });
  it("moves resolutions only the way the server allows", () => {
    expect(resolutionActions("draft").map(([k]) => k)).toEqual(["circulated", "passed", "withdrawn"]);
    expect(resolutionActions("circulated").map(([k]) => k)).toEqual(["passed", "failed", "withdrawn"]);
    expect(resolutionActions("passed")).toEqual([]);
    expect(resolutionActions("withdrawn")).toEqual([]);
  });
  it("suggests the next resolution number and builds the body", () => {
    expect(nextResolutionNumber([], 2026)).toBe("2026/001");
    expect(nextResolutionNumber(["2026/001", "2026/007", "2025/030", "ABC"], 2026)).toBe("2026/008");
    expect(resolutionReady(blankResolution())).toBe(false);
    const r = { ...blankResolution("m1"), number: " 2026/001 ", title: " Approve the budget " };
    expect(resolutionReady(r)).toBe(true);
    expect(resolutionBody(r)).toEqual({ number: "2026/001", title: "Approve the budget", kind: "board", meeting_id: "m1" });
    expect(resolutionBody({ ...r, meetingId: "" })).not.toHaveProperty("meeting_id");
  });
});

describe("vault upload", () => {
  it("hashes bytes the way the server does", async () => {
    const bytes = new TextEncoder().encode("abc");
    expect(await sha256Hex(bytes.buffer as ArrayBuffer)).toBe("ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad");
    expect(toHex(new Uint8Array([0, 15, 255]))).toBe("000fff");
  });
  it("sends a file inside JSON as base64, including one larger than a single chunk", () => {
    expect(toBase64(new TextEncoder().encode("%PDF-1.4").buffer as ArrayBuffer)).toBe("JVBERi0xLjQ=");
    const big = new Uint8Array(100_000).map((_, i) => i % 251);
    expect(Uint8Array.from(atob(toBase64(big.buffer as ArrayBuffer)), (c) => c.charCodeAt(0))).toEqual(big);
  });
  it("knows a file type when the browser does not", () => {
    expect(mimeOf("a.pdf", "")).toBe("application/pdf");
    expect(mimeOf("A.XLSX", "")).toBe("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet");
    expect(mimeOf("a.xyz", "")).toBe("application/octet-stream");
    expect(mimeOf("noext", "")).toBe("application/octet-stream");
    expect(mimeOf("a.pdf", "text/plain")).toBe("text/plain");
  });
  it("makes a readable title from a file name", () => {
    expect(titleFromFile("Board_minutes-Oct.pdf")).toBe("Board minutes Oct");
    expect(titleFromFile("noext")).toBe("noext");
    expect(titleFromFile("a.b.c.txt")).toBe("a.b.c");
  });
  it("refuses what the server would refuse", () => {
    expect(uploadProblem(null, "x")).toMatch(/Choose/);
    expect(uploadProblem({ name: "a.pdf", size: 0 }, "x")).toMatch(/empty/);
    expect(uploadProblem({ name: "a.pdf", size: MAX_UPLOAD_BYTES + 1 }, "x")).toMatch(/50 MB/);
    expect(uploadProblem({ name: "a.pdf", size: 10 }, "  ")).toMatch(/title/);
    expect(uploadProblem({ name: "a.pdf", size: 10 }, "Minutes")).toBeNull();
  });
  it("registers only what was filled", () => {
    const f = { name: "a.pdf", size: 10 };
    expect(registerBody(f, "application/pdf", "ab".repeat(32), { title: " Minutes ", folder: "board", period: "", validUntil: "" })).toEqual({
      folder: "board", title: "Minutes", file_name: "a.pdf", mime_type: "application/pdf", size_bytes: 10, sha256: "ab".repeat(32),
    });
    expect(registerBody(f, "application/pdf", "ab".repeat(32), { title: "T", folder: "tax", period: " FY 2025-26 ", validUntil: "2027-03-31" })).toMatchObject({ period_label: "FY 2025-26", valid_until: "2027-03-31" });
  });
  it("sends the bytes through the app's own origin", () => {
    expect(sameOriginUrl("http://localhost:8000/v1/storage/tok.en-1")).toBe("/api/v1/storage/tok.en-1");
    expect(sameOriginUrl("https://api.example.in/v1/storage/abc?x=1")).toBe("/api/v1/storage/abc?x=1");
    expect(sameOriginUrl(null)).toBeNull();
    expect(sameOriginUrl("https://cdn.example.in/file.pdf")).toBe("https://cdn.example.in/file.pdf");
  });
  it("edits a document and shares only uploaded ones", () => {
    expect(editProblem({ title: " ", folder: "tax", period: "", validUntil: "" })).toMatch(/title/);
    expect(editBody({ title: " Minutes ", folder: "board", period: " ", validUntil: "" })).toEqual({ title: "Minutes", folder: "board", period_label: null, valid_until: null });
    expect(shareable([{ download_url: "u" }, { download_url: null }])).toEqual([{ download_url: "u" }]);
  });
});

describe("notice desk", () => {
  const today = new Date(2026, 10, 7);
  const n = (over: object) => ({ id: "n", authority_code: "GSTN", registration_id: null, form_code: "ASMT-10", reference_no: null, period_label: null, received_on: "2026-11-01", reply_due_on: null,
    hearing_on: null, demand_amount: null, status: "open", summary: null, linked_obligation_id: null, document_ids: [], ...over });
  it("words how close a reply is", () => {
    expect(replyState(n({ reply_due_on: "2026-11-05" }), today)).toEqual({ label: "Reply overdue by 2 days", tone: "due" });
    expect(replyState(n({ reply_due_on: "2026-11-06" }), today)?.label).toBe("Reply overdue by 1 day");
    expect(replyState(n({ reply_due_on: "2026-11-07" }), today)?.label).toBe("Reply due today");
    expect(replyState(n({ reply_due_on: "2026-11-14" }), today)).toEqual({ label: "Reply due in 7 days", tone: "due" });
    expect(replyState(n({ reply_due_on: "2026-11-30" }), today)).toEqual({ label: "Reply due in 23 days", tone: "wait" });
    expect(replyState(n({ reply_due_on: null }), today)).toBeNull();
    expect(replyState(n({ reply_due_on: "2026-11-01", status: "replied" }), today)).toBeNull();
    expect(replyState(n({ reply_due_on: "2026-11-01", status: "closed" }), today)).toBeNull();
  });
  it("totals the desk without counting closed notices", () => {
    const list = [n({ reply_due_on: "2026-11-05", demand_amount: "1000.50" }), n({ reply_due_on: "2026-11-10", demand_amount: "250.00" }), n({ reply_due_on: "2026-12-30" }),
      n({ status: "closed", demand_amount: "99999.00", reply_due_on: "2026-11-01" }), n({ status: "replied", reply_due_on: "2026-11-01" })];
    expect(deskTotals(list as never, today)).toEqual({ open: 4, dueSoon: 1, overdue: 1, demand: "1250.50" });
    expect(deskTotals([], today)).toEqual({ open: 0, dueSoon: 0, overdue: 0, demand: "0.00" });
  });
  it("offers the next steps for each status", () => {
    expect(noticeMoves("open").map(([k]) => k)).toEqual(["replied", "hearing", "closed"]);
    expect(noticeMoves("appealed").map(([k]) => k)).toEqual(["closed"]);
    expect(noticeMoves("closed")).toEqual([]);
    expect(canDraft({ status: "open" })).toBe(true);
    expect(canDraft({ status: "draft_reply" })).toBe(true);
    expect(canDraft({ status: "replied" })).toBe(false);
    expect(canDraft({ status: "closed" })).toBe(false);
  });
  it("checks a notice form and sends only what was filled", () => {
    const f = { ...blankNotice("2026-11-07"), form: "ASMT-10" };
    expect(noticeProblem(f)).toBeNull();
    expect(noticeProblem({ ...f, form: " " })).toMatch(/code/);
    expect(noticeProblem({ ...f, received: "" })).toMatch(/received/);
    expect(noticeProblem({ ...f, replyDue: "2026-11-01" })).toMatch(/reply date/);
    expect(noticeProblem({ ...f, hearing: "2026-11-01" })).toMatch(/hearing/);
    expect(noticeProblem({ ...f, demand: "1,000" })).toMatch(/amount/);
    expect(noticeBody(f)).toEqual({ authority_code: "GSTN", form_code: "ASMT-10", received_on: "2026-11-07" });
    expect(noticeBody({ ...f, reference: " ZD2911 ", replyDue: "2026-11-30", demand: "125000.5", summary: " Mismatch " })).toEqual({
      authority_code: "GSTN", form_code: "ASMT-10", received_on: "2026-11-07", reference_no: "ZD2911", reply_due_on: "2026-11-30", demand_amount: "125000.50", summary: "Mismatch",
    });
    expect(AUTHORITY_LABEL.CBDT).toBe("Income Tax");
  });
  it("labels a finished job with the caller's words", () => {
    expect(jobStage({ status: "succeeded", error: null }, "Draft ready").label).toBe("Draft ready");
    expect(jobStage({ status: "succeeded", error: null }).label).toBe("Renewed");
  });
});

describe("data rooms", () => {
  const f = { ...blankRoom(), name: "Series A", documentIds: ["d1"] };
  it("reads a list of people however it was typed", () => {
    expect(parseEmails("A@x.in, b@x.in;\n a@x.in  c@x.in")).toEqual(["a@x.in", "b@x.in", "c@x.in"]);
    expect(parseEmails("  ")).toEqual([]);
    expect(badEmails("a@x.in nope b@")).toEqual(["nope", "b@"]);
  });
  it("checks the form", () => {
    expect(roomProblem(f, "2026-11-08")).toBeNull();
    expect(roomProblem({ ...f, name: " " }, "2026-11-08")).toMatch(/name/);
    expect(roomProblem({ ...f, emails: "a@x.in, nope" }, "2026-11-08")).toMatch(/nope/);
    expect(roomProblem({ ...f, expires: "2026-11-08" }, "2026-11-08")).toMatch(/after today/);
    expect(roomProblem({ ...f, documentIds: [] }, "2026-11-08")).toMatch(/document/);
  });
  it("builds the request and reads a room back", () => {
    const body = roomBody({ ...f, expires: "2026-12-31", emails: "Inv@Fund.in" });
    expect(body).toEqual({ name: "Series A", purpose: "fundraise", expires_at: "2026-12-31T23:59:59+05:30", watermark: true, allow_download: false, document_ids: ["d1"], access: [{ email: "inv@fund.in" }] });
    expect(roomBody(f).expires_at).toBeNull();
    const room = { id: "r", name: "Series A", purpose: "lender", expires_at: "2026-12-31T18:29:59Z", watermark: false, allow_download: true, document_ids: ["d1", "d2"], access: [{ email: "a@x.in", last_viewed_at: null }] };
    expect(roomForm(room)).toEqual({ name: "Series A", purpose: "lender", expires: "2026-12-31", watermark: false, allowDownload: true, emails: "a@x.in", documentIds: ["d1", "d2"] });
  });
  it("words expiry and viewers", () => {
    const now = new Date("2026-11-08T00:00:00Z");
    expect(expiryNote({ expires_at: null }, now)).toEqual({ label: "No expiry", expired: false });
    expect(expiryNote({ expires_at: "2026-11-30T18:29:59Z" }, now)).toEqual({ label: "Expires 30 Nov", expired: false });
    expect(expiryNote({ expires_at: "2026-10-30T18:29:59Z" }, now)).toEqual({ label: "Expired 30 Oct", expired: true });
    expect(viewerNote([])).toBe("No viewers yet");
    expect(viewerNote([{ email: "a", last_viewed_at: null }])).toBe("1 viewer, none has opened it");
    expect(viewerNote([{ email: "a", last_viewed_at: "x" }, { email: "b", last_viewed_at: null }])).toBe("2 viewers, 1 has opened it");
  });
});

describe("group actions", () => {
  const row = (id: string, cells: Record<string, string>, waiting = Object.values(cells).includes("client") ? 1 : 0) => ({ entity_id: id, legal_name: id, constitution: "private_limited", owner_staff: null, waiting_filings: waiting, cells, open_items: [] });
  it("picks the clients to chase", () => {
    const rows = [row("a", { GST: "client", MCA: "clear" }), row("b", { GST: "overdue" }), row("c", { TDS: "client" }), row("d", { GST: "overdue" }, 2)];   // d: the waiting filing is also late
    expect(chaseTargets(rows as never)).toEqual(["a", "c", "d"]);
    expect(chaseTargets([])).toEqual([]);
    expect(chaseLabel(0)).toBe("No one to chase");
    expect(chaseLabel(1)).toBe("Chase 1 client");
    expect(chaseLabel(9)).toBe("Chase 9 clients");
  });
  it("sums up a chase", () => {
    expect(chaseSummary({ reminded: [], skipped: [] })).toBe("No reminders were queued.");
    expect(chaseSummary({ reminded: [{ entity_id: "a", legal_name: "A", items: 2, recipients: 1 }], skipped: [] })).toBe("Queued reminders for 1 client.");
    expect(chaseSummary({ reminded: [{ entity_id: "a", legal_name: "A", items: 2, recipients: 1 }, { entity_id: "b", legal_name: "B", items: 1, recipients: 2 }], skipped: [{ entity_id: "c", legal_name: "C", reason: "no_client_contact" }] })).toBe("Queued reminders for 2 clients. 1 skipped.");
    expect(SKIP_REASON.reminded_recently).toMatch(/24 hours/);
  });
  it("hands the firm to the onboarding flow and back", () => {
    const id = "00000000-0000-0000-0000-000000000600";
    expect(addClientHref(id)).toBe(`/onboarding?org=${id}`);
    expect(orgFromSearch(`?org=${id}`)).toBe(id);
    expect(orgFromSearch("?org=drop table")).toBeNull();
    expect(orgFromSearch("")).toBeNull();
    expect(answersBody("L1", "business", [], DEFAULT_ANSWERS, id).organization_id).toBe(id);
    expect("organization_id" in answersBody("L1", "business", [], DEFAULT_ANSWERS)).toBe(false);
  });
});

describe("GST actions", () => {
  const m = (over: object) => ({ id: "m", status: "not_in_portal", supplier_gstin: "29AAAAA0000A1Z5", supplier_name: "Acme", purchase_bill_id: "b", books_tax: "1800.00", portal_tax: null, tax_difference: "1800.00", finding: "", suggested_action: "remind_supplier", action: null, action_taken_at: null, ...over });
  it("offers only what the server allows for each kind of difference", () => {
    expect(matchActions("matched").map(([k]) => k)).toEqual(["claim", "defer"]);
    expect(matchActions("amount_differs").map(([k]) => k)).toEqual(["claim_lower", "ask_to_amend", "investigate", "defer"]);
    expect(matchActions("not_in_portal").map(([k]) => k)).toEqual(["remind_supplier", "defer", "reject"]);
    expect(matchActions("not_in_books").map(([k]) => k)).toEqual(["book_bill", "investigate", "reject"]);
    expect(matchActions("unknown")).toEqual([]);
    expect(ACTION_LABEL.claim_lower).toBe("Claim the lower amount");
    expect(resolveBody("defer", "  ")).toEqual({ action: "defer" });
    expect(resolveBody("defer", " wait for Nov ")).toEqual({ action: "defer", note: "wait for Nov" });
  });
  it("counts what can be reminded", () => {
    const list = [m({}), m({ id: "2" }), m({ id: "3", status: "matched" }), m({ id: "4", action_taken_at: "2026-11-01T00:00:00Z", action: "remind_supplier" })];
    expect(list.filter(isOpen)).toHaveLength(3);
    expect(remindable(list as never)).toBe(2);
    expect(remindLabel(0)).toBe("No supplier to remind");
    expect(remindLabel(1)).toBe("Remind suppliers (1 invoice)");
    expect(remindLabel(2)).toBe("Remind suppliers (2 invoices)");
  });
  it("proposes last month and checks the month", () => {
    expect(lastMonth(new Date(2026, 10, 8))).toBe("2026-10");
    expect(lastMonth(new Date(2027, 0, 3))).toBe("2026-12");
    expect(periodOk("2026-10")).toBe(true);
    expect(periodOk("2026-13")).toBe(false);
    expect(periodOk("Oct 2026")).toBe(false);
    expect(syncBody("2026-10", "2B", "")).toEqual({ period: "2026-10", source: "2B" });
    expect(syncBody("2026-10", "2A", "r1")).toEqual({ period: "2026-10", source: "2A", registration_ids: ["r1"] });
  });
  it("lets a return be recomputed until it is submitted, and filed once ready", () => {
    expect([canCompute("drafting"), canCompute("ready"), canCompute("submitted"), canCompute("filed")]).toEqual([true, true, false, false]);
    expect([canFile("drafting"), canFile("ready"), canFile("submitted"), canFile("filed")]).toEqual([false, true, false, false]);
    expect(fileBody("dsc")).toEqual({ signing: "dsc" });
  });
  it("words the two sides of a difference", () => {
    const fmt = (v: string) => `Rs ${v}`;
    expect(taxNote({ books_tax: "1800.00", portal_tax: "1500.00" }, fmt)).toBe("Books Rs 1800.00, Portal Rs 1500.00");
    expect(taxNote({ books_tax: null, portal_tax: "90.00" }, fmt)).toBe("Portal Rs 90.00");
  });
});

describe("TDS screen", () => {
  it("finds the financial year, April to March", () => {
    expect(fyOf(new Date(2026, 10, 8))).toBe("2026-27");
    expect(fyOf(new Date(2027, 2, 31))).toBe("2026-27");
    expect(fyOf(new Date(2027, 3, 1))).toBe("2027-28");
    expect(fyOf(new Date(2099, 11, 1))).toBe("2099-00");
    expect(fyChoices(new Date(2026, 10, 8))).toEqual(["2026-27", "2025-26", "2024-25"]);
  });
  it("checks a challan and sends exact amounts", () => {
    const f = { ...blankChallan(), period: "Oct 2026", amount: "12500.5" };
    expect(challanProblem(f)).toBeNull();
    expect(challanProblem({ ...f, period: " " })).toMatch(/period/);
    expect(challanProblem({ ...f, amount: "0" })).toMatch(/above zero/);
    expect(challanProblem({ ...f, amount: "1,000" })).toMatch(/amount/);
    expect(challanProblem({ ...f, amount: "12.345" })).toMatch(/amount/);
    expect(challanProblem({ ...f, interest: "abc" })).toMatch(/Interest/);
    expect(challanProblem({ ...f, penalty: "-1" })).toMatch(/Penalty/);
    expect(challanBody(f)).toEqual({ kind: "itns281_tds", period_label: "Oct 2026", amount: "12500.50" });
    expect(challanBody({ ...f, interest: "120", penalty: "200.5" })).toEqual({ kind: "itns281_tds", period_label: "Oct 2026", amount: "12500.50", interest: "120.00", penalty: "200.50" });
  });
  it("totals deductions that are not on a challan", () => {
    const d = (tds: string, challan: string | null) => ({ id: tds, deductee_pan: "AAAPA1234A", section: "194C", deducted_on: "2026-10-05", base_amount: "10000.00", rate_pct: 1, tds_amount: tds, challan_id: challan, tds_return_id: null });
    expect(deductionTotals([d("100.10", null), d("250.20", "c1"), d("0.30", null)])).toEqual({ deducted: "350.60", unpaid: "100.40", unpaidCount: 2 });
    expect(deductionTotals([])).toEqual({ deducted: "0.00", unpaid: "0.00", unpaidCount: 0 });
  });
  it("tells which advance tax instalment is next", () => {
    const i = (n: number, due: string, est: string, paid: string) => ({ instalment_no: n, due_date: due, cumulative_pct: 15, estimated_tax: est, paid, challan_id: null });
    const rows = [i(1, "2026-06-15", "15000.00", "15000.00"), i(2, "2026-09-15", "45000.00", "0.00"), i(3, "2026-12-15", "75000.00", "0.00")];
    expect(instalmentState(rows[0]!, "2026-11-08")).toEqual({ label: "Paid", tone: "ok" });
    expect(instalmentState(rows[1]!, "2026-11-08")).toEqual({ label: "Past due", tone: "due" });
    expect(instalmentState(rows[2]!, "2026-11-08")).toEqual({ label: "Upcoming", tone: "wait" });
    expect(nextInstalment(rows, "2026-11-08")?.instalment_no).toBe(2);
    expect(nextInstalment([i(1, "2026-06-15", "0.00", "0.00")], "2026-11-08")).toBeNull();
  });
  it("words a validation result and the buttons each state allows", () => {
    expect(validationNote({ valid: true, token: "FVU123" })).toEqual({ ok: true, lines: ["Validated. Token FVU123"] });
    expect(validationNote({ valid: false, errors: ["The return has no deductions"] })).toEqual({ ok: false, lines: ["The return has no deductions"] });
    expect(validationNote({ valid: false })).toEqual({ ok: false, lines: ["The return did not validate."] });
    expect(validationNote(null)).toEqual({ ok: false, lines: [] });
    expect([canValidate("drafting"), canValidate("ready"), canValidate("filed")]).toEqual([true, true, false]);
    expect([canPay("scheduled"), canPay("failed"), canPay("paid")]).toEqual([true, true, false]);
    expect(unmatched([{ id: "1", section: "194C", amount: "1.00", fy: "2026-27", quarter: 2, seen_in_26as_at: null }, { id: "2", section: "194C", amount: "1.00", fy: "2026-27", quarter: 2, seen_in_26as_at: "2026-10-01T00:00:00Z" }])).toHaveLength(1);
  });
});

describe("TDS deposit", () => {
  it("lists the months that can still be prepared", () => {
    const o = (id: string, status: string, period: string, due: string, code = DEPOSIT_RULE) => ({ id, status, period_label: period, due_date: due, rule: { code } });
    expect(depositOptions([o("b", "upcoming", "Nov 2026", "2026-12-07"), o("a", "overdue", "Oct 2026", "2026-11-07"), o("x", "filed", "Sep 2026", "2026-10-07"), o("g", "upcoming", "Oct 2026", "2026-11-11", "GST.GSTR1.MONTHLY")]))
      .toEqual([{ id: "a", label: "Oct 2026 · due 07/11" }, { id: "b", label: "Nov 2026 · due 07/12" }]);
    expect(depositOptions([])).toEqual([]);
  });
  it("words what preparing did", () => {
    const fmt = (v: string) => `Rs ${v}`;
    expect(depositNote({ challan_id: null, note: "No TDS was deducted in this period" }, fmt)).toBe("No TDS was deducted in this period");
    expect(depositNote({ challan_id: "c", amount: "6200.00", interest: "0.00", deductions: 3 }, fmt)).toBe("Challan ready for Rs 6200.00, covering 3 deductions.");
    expect(depositNote({ challan_id: "c", amount: "100.00", interest: "2.00", deductions: 1 }, fmt)).toBe("Challan ready for Rs 100.00 plus Rs 2.00 late interest, covering 1 deduction.");
    expect(depositNote(null, fmt)).toBe("Done.");
  });
  it("hides ids in what the server says", () => {
    expect(plainError("Deduction 49f4a4ab-5694-4fa5-b51e-32dc36dd9cdc (194C) has no challan")).toBe("A 194C deduction has no challan");
    expect(plainError("The return has no deductions")).toBe("The return has no deductions");
    expect(validationNote({ valid: false, errors: ["Deduction 49f4a4ab-5694-4fa5-b51e-32dc36dd9cdc (194J) is on a challan that is not paid"] }).lines).toEqual(["A 194J deduction is on a challan that is not paid"]);
  });
});

describe("why it applies", () => {
  it("spells out fact names", () => {
    expect(factLabel("gst_frequency")).toBe("GST filing frequency");
    expect(factLabel("has_foreign_investor")).toBe("Has foreign investor");
    expect(factLabel("tds_threshold_pf")).toBe("TDS threshold PF");
    expect(factLabel("")).toBe("");
  });
  it("words one test the engine ran", () => {
    expect(clauseText({ fact: "gst_frequency", op: "=", value: "monthly", actual: "monthly", result: true, missing: false })).toEqual({ text: "GST filing frequency is monthly", met: true, note: "met" });
    expect(clauseText({ fact: "headcount", op: ">=", value: 20, actual: 12, result: false, missing: false })).toEqual({ text: "Headcount is at least 20", met: false, note: "not met (yours: 12)" });
    expect(clauseText({ fact: "has_tan", op: "=", value: true, actual: null, result: false, missing: true })).toEqual({ text: "Has a TAN: yes", met: false, note: "not known yet" });
    expect(clauseText({ fact: "udyam_class", op: "in", value: ["micro", "small"], actual: "medium", result: false, missing: false }).text).toBe("Udyam class is one of micro, small");
  });
  it("words the due rule", () => {
    expect(dueRuleText({ type: "day_of_following_month", day: 20 })).toBe("The 20th of the month after the period");
    expect(dueRuleText({ type: "day_of_following_month", day: 7, exceptions: { "03": {} } })).toBe("The 7th of the month after the period, with exceptions for some months");
    expect(dueRuleText({ type: "day_of_following_month", day: 11 })).toBe("The 11th of the month after the period");
    expect(dueRuleText({ type: "day_of_following_month", day: 22 })).toBe("The 22nd of the month after the period");
    expect(dueRuleText({ type: "fixed_dates", dates: { Q1: "07-31", Q2: "10-31" } })).toBe("Fixed dates: Q1 by 31 July, Q2 by 31 October");
    expect(dueRuleText({ type: "days_after_event", event: "agm", days: 30 })).toBe("30 days after AGM");
    expect(dueRuleText({ type: "per_document", when: "before_issue" })).toBe("Each time a document is issued");
    expect(dueRuleText({ type: "something_new" })).toBe('{"type":"something_new"}');
    expect(dueRuleText(null)).toBe("");
  });
  it("lines up the rule, due rule and registration", () => {
    const lines = reasonLines({ rule: { code: "GST.GSTR3B.MONTHLY", version: 1, statute_ref: "CGST Act s.39; r.61" }, due_rule: { type: "day_of_following_month", day: 20 }, registration: { kind: "gstin", number: "27AAKCO5678M1Z2" }, applicability: [] });
    expect(lines).toEqual([
      { label: "Rule", text: "GST.GSTR3B.MONTHLY · version 1 · CGST Act s.39; r.61" },
      { label: "When it is due", text: "The 20th of the month after the period" },
      { label: "Registration", text: "GSTIN 27AAKCO5678M1Z2" },
    ]);
    expect(reasonLines({ provisional: "assumes the latest date" })).toEqual([{ label: "Note", text: "assumes the latest date" }]);
    expect(reasonLines({})).toEqual([]);
  });
});

describe("copilot", () => {
  it("only sends a real question", () => {
    expect(askReady("  ")).toBe(false);
    expect(askReady("Who owes me money?")).toBe(true);
    expect(askReady("x".repeat(4001))).toBe(false);
  });
  it("splits an answer into lines and citation markers", () => {
    const blocks = answerBlocks("Two suppliers owe you papers.\n- Shakti: 2 invoices [1]\n- Vega: 1 invoice [2] and more [9]", 2);
    expect(blocks).toEqual([
      { bullet: false, pieces: [{ type: "text", text: "Two suppliers owe you papers." }] },
      { bullet: true, pieces: [{ type: "text", text: "Shakti: 2 invoices " }, { type: "cite", n: 1 }] },
      { bullet: true, pieces: [{ type: "text", text: "Vega: 1 invoice " }, { type: "cite", n: 2 }, { type: "text", text: " and more [9]" }] },   // a marker with no source stays plain text
    ]);
    expect(answerBlocks("\n\n", 0)).toEqual([]);
  });
  it("points each source at the screen that shows it", () => {
    expect(citationHref({ subject: "obligations", subject_id: "o1" })).toBe("/compliance?o=o1");
    expect(citationHref({ subject: "sales_documents", subject_id: "d1" })).toBe("/billing/d1");
    expect(citationHref({ subject: "itc_matches", subject_id: "m1" })).toBe("/gst");
    expect(citationHref({ subject: "mystery", subject_id: null })).toBeNull();
  });
  it("shows where an action stands", () => {
    expect(actionState({ status: "proposed", approval_request_id: null })).toEqual({ label: "Proposed", tone: "prep" });
    expect(actionState({ status: "proposed", approval_request_id: "a" })).toEqual({ label: "Waiting for approval", tone: "wait" });
    expect(actionState({ status: "executed", approval_request_id: "a" }).label).toBe("Done");
    expect(actionState({ status: "failed", approval_request_id: "a" }).label).toBe("Approved, but could not run");
    expect(canRequestApproval({ status: "proposed", approval_request_id: null })).toBe(true);
    expect(canRequestApproval({ status: "proposed", approval_request_id: "a" })).toBe(false);
    expect(canRequestApproval({ status: "rejected", approval_request_id: "a" })).toBe(false);
  });
  it("words an action and lists who it touches", () => {
    const remind = { kind: "send_supplier_reminders", payload: { party_ids: ["p1", "p2"], suppliers: [{ name: "Shakti", invoices: 2, tax: "74300.00" }, { name: "Vega", invoices: 1, tax: "100.00" }] } };
    expect(actionTitle(remind)).toBe("Remind 2 suppliers about invoices missing from GSTR-2A");
    expect(actionItems(remind, (v) => `Rs ${v}`)).toEqual([{ name: "Shakti", detail: "2 invoices · Rs 74300.00" }, { name: "Vega", detail: "1 invoice · Rs 100.00" }]);
    expect(actionTitle({ kind: "create_task", payload: { title: "Follow up with Delta" } })).toBe("Follow up with Delta");
    expect(actionItems({ kind: "create_task", payload: { title: "x" } }, String)).toEqual([]);
    expect(actionResult({ ...remind, status: "executed", payload: { ...remind.payload, result: { suppliers: 1, invoices: 3 } } })).toBe("Follow-up tasks added for 1 supplier (3 invoices).");
    expect(actionResult({ kind: "create_task", status: "failed", payload: { error: "boom" } })).toBe("It could not run: boom");
    expect(actionResult({ ...remind, status: "proposed" })).toBe("");
  });
  it("groups conversations into today and earlier", () => {
    const t = (id: string, at: string) => ({ id, title: id, module: null, created_at: at });
    const now = new Date(2026, 10, 8, 12, 0, 0);
    const groups = groupThreads([t("old", new Date(2026, 10, 1, 9).toISOString()), t("new", new Date(2026, 10, 8, 9).toISOString()), t("newer", new Date(2026, 10, 8, 11).toISOString())], now);
    expect(groups.map((g) => [g.heading, g.items.map((x) => x.id)])).toEqual([["TODAY", ["newer", "new"]], ["EARLIER", ["old"]]]);
    expect(groupThreads([], now)).toEqual([]);
  });
});
