/** Reads the opening position out of a spreadsheet saved as CSV: the opening trial balance, the invoices still open and the bills still open.
 * Column names are matched loosely (Tally, Zoho, Excel exports differ); every cell is turned into the exact text the API checks. Nothing here saves anything. */
import { parseAmount, parseDate, splitCsv } from "./statement";

export type Kind = "opening_balances" | "open_invoices" | "open_bills";
export interface Spec { label: string; fields: Array<{ key: string; names: RegExp; required?: boolean; type?: "date" | "money" | "gstin" | "text" }>; template: string[][]; blurb: string }

const n = (re: string) => new RegExp(`^(${re})$`);
const MONEY = "money" as const, DATE = "date" as const;

export const SPECS: Record<Kind, Spec> = {
  opening_balances: {
    label: "Opening trial balance",
    blurb: "One line per ledger account on the cut-off date: its code, name, type (asset, liability, equity, income or expense) and the debit or credit balance. Debits and credits must be equal.",
    fields: [
      { key: "account_code", names: n("account code|code|ledger code|a c code|account no|account number"), required: true },
      { key: "account_name", names: n("account name|ledger name|ledger|particulars|name|account|account head"), required: true },
      { key: "type", names: n("type|account type|nature|group type|category"), required: true },
      { key: "subtype", names: n("subtype|sub type|account subtype") },
      { key: "debit", names: n("debit|dr|debit amount|debit balance"), type: MONEY },
      { key: "credit", names: n("credit|cr|credit amount|credit balance"), type: MONEY },
    ],
    template: [["Account code", "Account name", "Type", "Subtype", "Debit", "Credit"],
      ["1100", "ICICI current account", "asset", "bank", "500000.00", ""], ["1200", "Trade receivables", "asset", "receivable", "236000.00", ""],
      ["2100", "Trade payables", "liability", "payable", "", "118000.00"], ["3000", "Share capital", "equity", "", "", "618000.00"]],
  },
  open_invoices: {
    label: "Open sales invoices",
    blurb: "The invoices not yet fully paid on the cut-off date. Give the amount still outstanding when part has been paid. Tax must be IGST for another state, or CGST and SGST for the same state.",
    fields: [
      { key: "number", names: n("invoice no|invoice number|invoice no |number|inv no|document no|doc no|voucher no|bill no"), required: true },
      { key: "date", names: n("invoice date|date|doc date|voucher date"), required: true, type: DATE },
      { key: "customer_name", names: n("customer|customer name|party|party name|buyer|name of customer|particulars"), required: true },
      { key: "customer_gstin", names: n("customer gstin|gstin|party gstin|gstin of customer|buyer gstin|gstin uin"), type: "gstin" },
      { key: "place_of_supply", names: n("place of supply|pos|state code|place of supply code") },
      { key: "taxable", names: n("taxable value|taxable|taxable amount|assessable value"), required: true, type: MONEY },
      { key: "cgst", names: n("cgst|central tax|cgst amount"), type: MONEY },
      { key: "sgst", names: n("sgst|state tax|sgst amount|sgst utgst|sgst utgst amount"), type: MONEY },
      { key: "igst", names: n("igst|integrated tax|igst amount"), type: MONEY },
      { key: "total", names: n("invoice value|total|grand total|invoice total|gross total|total amount|amount"), required: true, type: MONEY },
      { key: "outstanding", names: n("outstanding|balance|pending|balance due|amount due|pending amount|outstanding amount"), type: MONEY },
      { key: "due_date", names: n("due date|payment due date"), type: DATE },
    ],
    template: [["Invoice no", "Date", "Customer", "Customer GSTIN", "Place of supply", "Taxable value", "CGST", "SGST", "IGST", "Total", "Outstanding", "Due date"],
      ["INV/25-26/001", "10/02/2026", "Coastal Grid Utilities", "32AABCX9999K1ZP", "32", "100000.00", "", "", "18000.00", "118000.00", "118000.00", "12/03/2026"]],
  },
  open_bills: {
    label: "Open purchase bills",
    blurb: "The supplier bills not yet fully paid on the cut-off date. Their input tax credit is recorded as already claimed in the month of the bill. Give TDS deducted, and the amount still outstanding when part has been paid.",
    fields: [
      { key: "bill_no", names: n("bill no|bill number|supplier invoice no|supplier invoice number|invoice no|invoice number|ref no|voucher no"), required: true },
      { key: "date", names: n("bill date|invoice date|date|voucher date"), required: true, type: DATE },
      { key: "supplier_name", names: n("supplier|supplier name|vendor|vendor name|party|party name|particulars"), required: true },
      { key: "supplier_gstin", names: n("supplier gstin|gstin|vendor gstin|party gstin|gstin of supplier|gstin uin"), type: "gstin" },
      { key: "supplier_state", names: n("supplier state|state code|state|place of supply of supplier") },
      { key: "taxable", names: n("taxable value|taxable|taxable amount|assessable value"), required: true, type: MONEY },
      { key: "cgst", names: n("cgst|central tax|cgst amount"), type: MONEY },
      { key: "sgst", names: n("sgst|state tax|sgst amount|sgst utgst|sgst utgst amount"), type: MONEY },
      { key: "igst", names: n("igst|integrated tax|igst amount"), type: MONEY },
      { key: "total", names: n("bill value|invoice value|total|grand total|bill total|gross total|total amount|amount"), required: true, type: MONEY },
      { key: "tds", names: n("tds|tds deducted|tds amount"), type: MONEY },
      { key: "outstanding", names: n("outstanding|balance|pending|balance due|amount due|pending amount|outstanding amount"), type: MONEY },
      { key: "due_date", names: n("due date|payment due date"), type: DATE },
    ],
    template: [["Bill no", "Date", "Supplier", "Supplier GSTIN", "Supplier state", "Taxable value", "CGST", "SGST", "IGST", "Total", "TDS", "Outstanding", "Due date"],
      ["SC-778", "01/03/2026", "Shakti Castings", "29AAECS5555M1Z3", "29", "100000.00", "9000.00", "9000.00", "", "118000.00", "", "118000.00", "31/03/2026"]],
  },
};

export interface ReadProblem { row: number; message: string }
export interface Read { rows: Array<Record<string, string>>; sheetRows: number[]; problems: ReadProblem[]; columns: Record<string, string>; fatal: string | null }

const norm = (s: string) => s.toLowerCase().replace(/[^a-z0-9]+/g, " ").trim();

/** Each cell becomes exactly what the API expects: ISO dates, two-decimal money, upper-case GSTINs. A cell that cannot be read is reported with its row. */
export function readRows(kind: Kind, text: string): Read {
  const spec = SPECS[kind];
  const table = splitCsv(text);
  const need = spec.fields.filter((f) => f.required);
  let at = -1, map: Record<string, number> = {};
  for (let i = 0; i < Math.min(table.length, 30) && at < 0; i++) {
    const m: Record<string, number> = {};
    table[i]!.forEach((c, j) => { for (const f of spec.fields) if (!(f.key in m) && f.names.test(norm(c)) && !Object.values(m).includes(j)) { m[f.key] = j; break; } });
    if (need.every((f) => f.key in m)) { at = i; map = m; }
  }
  if (at < 0) {
    const missing = need.map((f) => f.key.replace("_", " ")).join(", ");
    return { rows: [], sheetRows: [], problems: [], columns: {}, fatal: `I could not find the header row. This file needs columns for: ${missing}. Download the template to see the layout.` };
  }
  const columns = Object.fromEntries(Object.entries(map).map(([k, j]) => [k, table[at]![j]!.trim()]));
  const rows: Array<Record<string, string>> = [];
  const sheetRows: number[] = [];
  const problems: ReadProblem[] = [];
  for (let i = at + 1; i < table.length; i++) {
    const r = table[i]!;
    const cell = (k: string) => (k in map ? (r[map[k]!] ?? "").trim() : "");
    if (spec.fields.filter((f) => f.required).every((f) => cell(f.key) === "")) continue;       // blank or total lines
    const out: Record<string, string> = {};
    for (const f of spec.fields) {
      const v = cell(f.key);
      if (v === "") continue;
      try {
        if (f.type === "date") { const d = parseDate(v); if (!d) throw new Error(`${v} is not a date`); out[f.key] = d; }
        else if (f.type === "money") { const p = parseAmount(v); if (p === null) continue; const a = p < 0n ? -p : p; out[f.key] = `${p < 0n ? "-" : ""}${a / 100n}.${(a % 100n).toString().padStart(2, "0")}`; }
        else if (f.type === "gstin") out[f.key] = v.toUpperCase().replace(/\s/g, "");
        else out[f.key] = v;
      } catch (e) { problems.push({ row: i + 1, message: `${f.key.replace("_", " ")}: ${(e as Error).message}` }); }
    }
    rows.push(out);
    sheetRows.push(i + 1);
  }
  return { rows, sheetRows, problems, columns, fatal: rows.length === 0 ? "No rows were found under the header." : null };
}

/** The template as CSV text, quoting cells that need it. */
export function templateCsv(kind: Kind): string {
  return SPECS[kind].template.map((r) => r.map((c) => (/[",\n]/.test(c) ? `"${c.replace(/"/g, '""')}"` : c)).join(",")).join("\r\n") + "\r\n";
}

export interface Batch { id: string; kind: Kind; label: string | null; as_of: string; row_count: number; summary: Record<string, string | number>; created_at: string; undone_at: string | null }
