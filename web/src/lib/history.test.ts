import { describe, expect, it } from "vitest";
import { SPECS, readRows, templateCsv, type Kind } from "./history";

describe("reading the opening position", () => {
  it("reads an opening trial balance", () => {
    const r = readRows("opening_balances", "Account code,Account name,Type,Debit,Credit\n1100,ICICI,asset,\"5,00,000.00\",\n3000,Capital,equity,,500000\n,,,,\n");
    expect(r.fatal).toBeNull();
    expect(r.rows).toEqual([{ account_code: "1100", account_name: "ICICI", type: "asset", debit: "500000.00" }, { account_code: "3000", account_name: "Capital", type: "equity", credit: "500000.00" }]);
    expect(r.sheetRows).toEqual([2, 3]);
  });

  it("reads open invoices with Indian dates and loose headers", () => {
    const csv = "Report title\nInvoice Number,Invoice Date,Party Name,GSTIN/UIN,Taxable Value,Central Tax,State Tax,IGST,Invoice Value,Pending Amount\n"
      + "INV/1,10/02/2026,Acme,29aabcl1234f1z5,\"1,00,000.00\",9000,9000,,\"1,18,000.00\",58000\n";
    const r = readRows("open_invoices", csv);
    expect(r.fatal).toBeNull();
    expect(r.rows[0]).toEqual({ number: "INV/1", date: "2026-02-10", customer_name: "Acme", customer_gstin: "29AABCL1234F1Z5", taxable: "100000.00", cgst: "9000.00", sgst: "9000.00",
      total: "118000.00", outstanding: "58000.00" });
    expect(r.columns).toMatchObject({ number: "Invoice Number", customer_gstin: "GSTIN/UIN", outstanding: "Pending Amount" });
  });

  it("reads open bills with TDS", () => {
    const r = readRows("open_bills", "Bill No,Date,Supplier,Taxable,CGST,SGST,Total,TDS\nSC-1,01-Mar-2026,Shakti,100000,9000,9000,118000,1000\n");
    expect(r.rows[0]).toMatchObject({ bill_no: "SC-1", date: "2026-03-01", supplier_name: "Shakti", tds: "1000.00", total: "118000.00" });
  });

  it("reports unreadable cells with their sheet row and keeps going", () => {
    const r = readRows("open_invoices", "Invoice no,Date,Customer,Taxable,Total\nA,31/02/2026,X,100,118\nB,10/02/2026,Y,abc,118\n");
    expect(r.rows).toHaveLength(2);
    expect(r.problems.map((p) => [p.row, p.message.split(":")[0]])).toEqual([[2, "date"], [3, "taxable"]]);
  });

  it("says what is missing when there is no header", () => {
    const r = readRows("open_bills", "just,some,words\n1,2,3");
    expect(r.fatal).toMatch(/bill no, date, supplier name, taxable, total/);
  });

  it("every template reads back as itself", () => {
    for (const kind of Object.keys(SPECS) as Kind[]) {
      const r = readRows(kind, templateCsv(kind));
      expect(r.fatal, kind).toBeNull();
      expect(r.problems, kind).toEqual([]);
      expect(r.rows.length, kind).toBeGreaterThan(0);
    }
  });
});
