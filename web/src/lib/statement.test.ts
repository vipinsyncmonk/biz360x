import { describe, expect, it } from "vitest";
import { chunk, extractUtr, parseAmount, parseDate, parseStatement, splitCsv } from "./statement";

const HDFC = [
  "Statement of account,,,,,,",
  "Date,Narration,Chq./Ref.No.,Value Dt,Withdrawal Amt.,Deposit Amt.,Closing Balance",
  "01/04/24,NEFT CR-HDFC0000001-ACME TRADERS-INV-2024-0042,HDFCR52024040100123,01/04/24,,\"1,18,000.00\",\"5,18,000.00\"",
  "02/04/24,UPI-SWIGGY-12345@ybl,402211223344,02/04/24,\"1,250.50\",,\"5,16,749.50\"",
  "02/04/24,BANK CHARGES,,02/04/24,0.00,,\"5,16,749.50\"",
  "03/04/24,RTGS DR-SBIN0000002-VENDOR PVT LTD,SBINR52024040300456,03/04/24,\"2,00,000.00\",,\"3,16,749.50\"",
  ",,,,,,",
  "Closing balance,,,,,,\"3,16,749.50\"",
].join("\n");

describe("reading a statement", () => {
  it("reads a typical bank CSV: credits positive, debits negative, notes, balance check passes", () => {
    const s = parseStatement(HDFC);
    expect(s.fatal).toBeNull();
    expect(s.lines.map((l) => [l.txn_date, l.amount, l.balance_after])).toEqual([
      ["2024-04-01", "118000.00", "518000.00"],
      ["2024-04-02", "-1250.50", "516749.50"],
      ["2024-04-03", "-200000.00", "316749.50"],
    ]);
    expect(s.columns).toMatchObject({ date: "Date", debit: "Withdrawal Amt.", credit: "Deposit Amt.", balance: "Closing Balance" });
    expect(s.from).toBe("2024-04-01"); expect(s.to).toBe("2024-04-03");
    expect(s.credits).toBe("118000.00"); expect(s.debits).toBe("201250.50");
    expect(s.balanceBreaks).toBe(0);
    expect(s.problems).toEqual([]);                                  // the footer and the zero charge are skipped quietly
    expect(s.lines[0]!.utr).toBe("HDFCR52024040100123");
  });

  it("flags a balance that does not follow, newest-first files included", () => {
    const broken = HDFC.replace('"3,16,749.50"\n,,', '"3,16,000.00"\n,,');
    expect(parseStatement(broken).balanceBreaks).toBe(1);
    const rows = HDFC.split("\n");
    const newestFirst = [rows[0], rows[1], rows[5], rows[3], rows[2]].join("\n");
    expect(parseStatement(newestFirst).balanceBreaks).toBe(0);
  });

  it("reads a single Amount column with Dr/Cr and semicolons", () => {
    const s = parseStatement("Transaction Date;Description;Amount;Dr/Cr\n05-Apr-2024;Salary April;50000.00;Dr\n06-Apr-2024;Customer receipt;75,000.00;Cr");
    expect(s.lines.map((l) => l.amount)).toEqual(["-50000.00", "75000.00"]);
    expect(s.lines[0]!.balance_after).toBeNull();
  });

  it("uses a stable key: same lines, same keys; repeated identical lines stay distinct", () => {
    const twice = "Date,Narration,Debit,Credit\n01/04/2024,CHARGES,10.00,\n01/04/2024,CHARGES,10.00,\n";
    const a = parseStatement(twice), b = parseStatement(twice);
    expect(a.lines.map((l) => l.external_id)).toEqual(b.lines.map((l) => l.external_id));
    expect(new Set(a.lines.map((l) => l.external_id)).size).toBe(2);
    const other = parseStatement("Date,Narration,Debit,Credit\n01/04/2024,OTHER,10.00,\n");
    expect(other.lines[0]!.external_id).not.toBe(a.lines[0]!.external_id);
  });

  it("reports unreadable rows with their row numbers and keeps the good ones", () => {
    const s = parseStatement("Date,Narration,Debit,Credit\n31/02/2024,Bad date,5.00,\n01/04/2024,Good,5.00,\n02/04/2024,Bad money,abc,\n03/04/2024,Both,5.00,5.00");
    expect(s.lines).toHaveLength(1);
    expect(s.problems.map((p) => p.row)).toEqual([2, 4, 5]);
    expect(s.problems[1]!.message).toMatch(/not an amount/);
    expect(s.problems[2]!.message).toMatch(/both a debit and a credit/);
  });

  it("refuses a file with no header or no lines", () => {
    expect(parseStatement("hello\nworld").fatal).toMatch(/header row/);
    expect(parseStatement("Date,Narration,Debit,Credit\n").fatal).toMatch(/No usable lines/);
  });
});

describe("pieces", () => {
  it("splits quoted cells, doubled quotes and CRLF", () => {
    expect(splitCsv('a,"b,c","say ""hi"""\r\nd,e,f')).toEqual([["a", "b,c", 'say "hi"'], ["d", "e", "f"]]);
  });
  it("reads Indian dates, day first, and rejects impossible ones", () => {
    expect(parseDate("05/04/2024")).toBe("2024-04-05");
    expect(parseDate("5-Apr-24")).toBe("2024-04-05");
    expect(parseDate("2024-04-05")).toBe("2024-04-05");
    expect(parseDate("30/02/2024")).toBeNull();
    expect(parseDate("13/13/2024")).toBeNull();
    expect(parseDate("hello")).toBeNull();
  });
  it("reads amounts without floats", () => {
    expect(parseAmount("1,23,456.50")).toBe(12345650n);
    expect(parseAmount("₹ 500")).toBe(50000n);
    expect(parseAmount("(250.00)")).toBe(-25000n);
    expect(parseAmount("250.00 Dr")).toBe(-25000n);
    expect(parseAmount("")).toBeNull();
    expect(() => parseAmount("1.234")).toThrow(/decimals/);
    expect(() => parseAmount("12x")).toThrow(/not an amount/);
  });
  it("pulls a UTR from the narration", () => {
    expect(extractUtr("NEFT CR-HDFC0000001-ACME-HDFCR52024040100123")).toBe("HDFCR52024040100123");
    expect(extractUtr("UTR: SBIN12345678901 for rent")).toBe("SBIN12345678901");
    expect(extractUtr("UPI/CR/402211223344/x")).toBe("402211223344");
    expect(extractUtr("cash deposit")).toBeNull();
  });
  it("chunks to the API limit", () => {
    expect(chunk([1, 2, 3, 4, 5], 2)).toEqual([[1, 2], [3, 4], [5]]);
    expect(chunk(Array.from({ length: 2500 }, (_, i) => i)).map((c) => c.length)).toEqual([1000, 1000, 500]);
  });
});

import { accountProblem, bankBody, blankAccount, ledgerBody } from "./statement";
describe("adding a bank account", () => {
  const ok = { bank: " HDFC Bank ", last4: "4412", ifsc: "hdfc0000123", kind: "current" };
  it("checks the form", () => {
    expect(accountProblem(blankAccount)).toMatch(/bank's name/);
    expect(accountProblem({ ...ok, last4: "44" })).toMatch(/last four/);
    expect(accountProblem({ ...ok, ifsc: "XYZ" })).toMatch(/IFSC/);
    expect(accountProblem(ok)).toBeNull();
    expect(accountProblem({ ...ok, ifsc: "" })).toBeNull();
  });
  it("builds the ledger account and the bank account", () => {
    expect(ledgerBody(ok)).toEqual({ code: "BANK-4412", name: "HDFC Bank ····4412", type: "asset", subtype: "bank" });
    expect(bankBody(ok, "L1")).toEqual({ ledger_account_id: "L1", bank_name: "HDFC Bank", account_last4: "4412", ifsc: "HDFC0000123", kind: "current" });
    expect(bankBody({ ...ok, ifsc: "" }, "L1").ifsc).toBeUndefined();
  });
});
