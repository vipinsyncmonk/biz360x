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

import { blankClassify, classifyBody, classifyProblem, ruleText, similarLines, type BankRule, type BankTxn } from "./finance";

const line = (over: Partial<BankTxn>): BankTxn => ({ id: "t", txn_date: "2026-04-02", amount: "-2500.00", description: "UPI/1/UPI Pay/8792003390@ibl//x/", counterparty_name: null, match_status: "unmatched", suggestions: [], counterparty_key: "8792003390@ibl", ...over });

describe("booking a bank line", () => {
  it("finds the other open lines to the same counterparty moving money the same way", () => {
    const a = line({ id: "a" });
    const rows = [a, line({ id: "b" }), line({ id: "c", counterparty_key: "someone@upi" }), line({ id: "d", match_status: "matched" }), line({ id: "e", amount: "900.00" }), line({ id: "f", match_status: "suggested" })];
    expect(similarLines(rows, a).map((x) => x.id)).toEqual(["b", "f"]);
    expect(similarLines(rows, line({ counterparty_key: null }))).toEqual([]);
  });
  it("needs an account, and a counterparty or a word that is really in the narration to remember", () => {
    const t = line({});
    expect(classifyProblem(blankClassify(), t)).toMatch(/account/);
    expect(classifyProblem(blankClassify("acc"), t)).toBeNull();
    expect(classifyProblem({ ...blankClassify("acc"), remember: "counterparty" }, line({ counterparty_key: null }))).toMatch(/no counterparty/);
    expect(classifyProblem({ ...blankClassify("acc"), remember: "keyword" }, t)).toMatch(/word/);
    expect(classifyProblem({ ...blankClassify("acc"), remember: "keyword", keyword: "bescom" }, t)).toMatch(/not in this line/);
    expect(classifyProblem({ ...blankClassify("acc"), remember: "keyword", keyword: "UPI Pay" }, t)).toBeNull();
  });
  it("sends only what was chosen", () => {
    expect(classifyBody(blankClassify("acc"))).toEqual({ account_id: "acc" });
    expect(classifyBody({ ...blankClassify("acc"), memo: " welfare ", remember: "counterparty", applySimilar: true })).toEqual({ account_id: "acc", memo: "welfare", remember: "counterparty", apply_to_similar: true });
    expect(classifyBody({ ...blankClassify("acc"), remember: "keyword", keyword: " rent " })).toEqual({ account_id: "acc", remember: "keyword", keyword: "rent" });
  });
  it("describes a rule in words", () => {
    const r: BankRule = { id: "r", direction: "debit", counterparty_key: "8792003390@ibl", keyword: null, mode: null, account_id: "a", account_code: "5500", account_name: "Staff welfare", hits: 2 };
    expect(ruleText(r)).toBe("Payments to or from 8792003390@ibl");
    expect(ruleText({ ...r, direction: "any", counterparty_key: null, keyword: "rent", mode: "neft" })).toBe("Lines mentioning “rent” by neft");
  });
});
