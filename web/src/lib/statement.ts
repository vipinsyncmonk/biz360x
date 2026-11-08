/** Reads a bank statement saved as CSV into the lines the API accepts. Nothing here touches the network and money is never a float:
 * amounts are read as text and turned into two-decimal strings through paise (BigInt). Everything is checked before anything is sent. */
import { toPaise } from "./format";

export interface StatementLine {
  external_id: string;
  txn_date: string;
  amount: string;
  description: string;
  utr: string | null;
  balance_after: string | null;
}
export interface RowProblem { row: number; message: string }
export interface Statement {
  lines: StatementLine[];
  problems: RowProblem[];
  /** Which header each field was read from, so a person can see the guess was right. */
  columns: Record<string, string>;
  from: string | null;
  to: string | null;
  credits: string;
  debits: string;
  /** Lines whose running balance does not follow from the one before it (only when the file has a balance column). */
  balanceBreaks: number;
  fatal: string | null;
}

const money = (p: bigint) => `${p < 0n ? "-" : ""}${(p < 0n ? -p : p) / 100n}.${((p < 0n ? -p : p) % 100n).toString().padStart(2, "0")}`;

/** Split CSV text into rows of cells: quotes, doubled quotes and the delimiter (comma, semicolon or tab, chosen from the first lines). */
export function splitCsv(text: string): string[][] {
  const src = text.replace(/^﻿/, "");
  const head = src.split(/\r?\n/).slice(0, 8).join("\n");
  const count = (c: string) => (head.match(new RegExp(c === "\t" ? "\t" : `\\${c}`, "g")) ?? []).length;
  const delim = [",", ";", "\t"].reduce((best, c) => (count(c) > count(best) ? c : best), ",");
  const rows: string[][] = [];
  let row: string[] = [], cell = "", quoted = false;
  for (let i = 0; i < src.length; i++) {
    const ch = src[i]!;
    if (quoted) {
      if (ch === '"') { if (src[i + 1] === '"') { cell += '"'; i++; } else quoted = false; } else cell += ch;
    } else if (ch === '"') quoted = true;
    else if (ch === delim) { row.push(cell); cell = ""; }
    else if (ch === "\n" || ch === "\r") {
      if (ch === "\r" && src[i + 1] === "\n") i++;
      row.push(cell); cell = "";
      rows.push(row); row = [];
    } else cell += ch;
  }
  if (cell !== "" || row.length) { row.push(cell); rows.push(row); }
  return rows.filter((r) => r.some((c) => c.trim() !== ""));
}

const norm = (s: string) => s.toLowerCase().replace(/[^a-z0-9]+/g, " ").trim();
const HEADERS: Record<string, RegExp> = {
  date: /^(txn date|transaction date|tran date|trans date|post date|posting date|date|booking date)$/,
  value_date: /^(value date|value dt|val date)$/,
  description: /^(narration|particulars|description|transaction remarks|remarks|transaction details|details|transaction description)$/,
  ref: /^(chq ref no|chq no|cheque no|cheque number|ref no|reference no|reference number|ref no cheque no|chq ref number|utr no|utr|transaction id|tran id)$/,
  debit: /^(withdrawal amt|withdrawal amount|withdrawals|withdrawal|debit|debit amount|dr|dr amount|debits|amount debited)$/,
  credit: /^(deposit amt|deposit amount|deposits|deposit|credit|credit amount|cr|cr amount|credits|amount credited)$/,
  amount: /^(amount|transaction amount|txn amount|amount inr)$/,
  drcr: /^(dr cr|cr dr|type|transaction type|debit credit|dr\/cr)$/,
  balance: /^(balance|closing balance|running balance|balance amt|available balance|balance inr)$/,
};

function findHeader(rows: string[][]): { at: number; map: Record<string, number> } | null {
  for (let i = 0; i < Math.min(rows.length, 40); i++) {
    const map: Record<string, number> = {};
    rows[i]!.forEach((c, j) => {
      const n = norm(c);
      for (const [k, rx] of Object.entries(HEADERS)) if (!(k in map) && rx.test(n)) map[k] = j;
    });
    if ("date" in map && ("debit" in map || "credit" in map || "amount" in map)) return { at: i, map };
  }
  return null;
}

const MON = ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"];

/** Indian statements put the day first: 05/04/2024 is 5 April. Also 05-Apr-2024, 05 Apr 24 and 2024-04-05. */
export function parseDate(raw: string): string | null {
  const s = raw.trim();
  let y: number, m: number, d: number;
  let x = /^(\d{4})-(\d{1,2})-(\d{1,2})/.exec(s);
  if (x) { y = +x[1]!; m = +x[2]!; d = +x[3]!; }
  else if ((x = /^(\d{1,2})[/\-. ](\d{1,2})[/\-. ](\d{2}|\d{4})$/.exec(s))) { d = +x[1]!; m = +x[2]!; y = +x[3]!; if (x[3]!.length === 2) y += 2000; }
  else if ((x = /^(\d{1,2})[/\-. ]([A-Za-z]{3})[a-z]*[/\-. ,]*(\d{2}|\d{4})$/.exec(s))) {
    d = +x[1]!; m = MON.indexOf(x[2]!.toLowerCase()) + 1; y = +x[3]!; if (x[3]!.length === 2) y += 2000;
  } else return null;
  if (m < 1 || m > 12 || d < 1 || y < 1990 || y > 2100) return null;
  const dim = new Date(Date.UTC(y, m, 0)).getUTCDate();
  if (d > dim) return null;
  return `${y}-${String(m).padStart(2, "0")}-${String(d).padStart(2, "0")}`;
}

/** "1,23,456.50", "₹ 500", "(250.00)", "250.00 Dr" -> paise (negative for brackets or Dr), or null when empty; throws on junk. */
export function parseAmount(raw: string): bigint | null {
  let s = raw.trim().replace(/[₹\s]/g, "").replace(/^(INR|Rs\.?)/i, "");
  if (s === "" || s === "-") return null;
  let neg = false;
  if (/^\(.*\)$/.test(s)) { neg = true; s = s.slice(1, -1); }
  const t = /(dr|cr)\.?$/i.exec(s);
  if (t) { if (t[1]!.toLowerCase() === "dr") neg = !neg; s = s.slice(0, -t[0].length); }
  if (s.startsWith("-")) { neg = !neg; s = s.slice(1); }
  s = s.replace(/,/g, "");
  if (!/^\d+(\.\d+)?$/.test(s)) throw new Error(`not an amount: ${raw.trim()}`);
  if (/\.\d{3,}/.test(s) && !/\.\d{2}0*$/.test(s)) throw new Error(`more than two decimals: ${raw.trim()}`);
  const p = toPaise(s);
  return neg ? -p : p;
}

/** A short stable fingerprint of the narration, so the same line in two overlapping statements gets the same key. */
export function fingerprint(s: string): string {
  let h = 0x811c9dc5;
  for (const ch of s.toLowerCase().replace(/\s+/g, " ").trim()) { h ^= ch.codePointAt(0)!; h = Math.imul(h, 0x01000193) >>> 0; }
  return h.toString(16).padStart(8, "0");
}

const UTR_RX = [/\bUTR[\s:.\-#]*([A-Z0-9]{10,22})\b/i, /\b(?:NEFT|RTGS|IMPS)[\s\-/:]*(?:CR|DR)?[\s\-/:]*([A-Z]{4}[A-Z0-9]{8,18})\b/i, /\b([A-Z]{4}[RN]\d{10,14})\b/, /\bUPI[\s/\-]*(?:CR|DR)?[\s/\-]*(\d{12})\b/i];
export function extractUtr(text: string): string | null {
  for (const rx of UTR_RX) { const m = rx.exec(text); if (m) return m[1]!.toUpperCase(); }
  return null;
}

const EMPTY: Statement = { lines: [], problems: [], columns: {}, from: null, to: null, credits: "0.00", debits: "0.00", balanceBreaks: 0, fatal: null };

export function parseStatement(text: string): Statement {
  const rows = splitCsv(text);
  const h = findHeader(rows);
  if (!h) return { ...EMPTY, fatal: "I could not find the header row. The file needs a Date column and either Debit and Credit columns or an Amount column." };
  const { at, map } = h;
  const names = Object.fromEntries(Object.entries(map).map(([k, j]) => [k, rows[at]![j]!.trim()]));
  const lines: StatementLine[] = [];
  const problems: RowProblem[] = [];
  const seen = new Map<string, number>();
  for (let i = at + 1; i < rows.length; i++) {
    const r = rows[i]!;
    const cell = (k: string) => (k in map ? (r[map[k]!] ?? "").trim() : "");
    const dateText = cell("date");
    const rowNo = i + 1;
    if (!parseDate(dateText)) {
      if (dateText && !/^(opening|closing|total|statement|page|\*)/i.test(dateText) && !/^[-*=_ ]+$/.test(dateText)) problems.push({ row: rowNo, message: `Date ${dateText} is not a date I can read` });
      continue;                                              // footers, totals and page headers are skipped
    }
    try {
      const date = parseDate(dateText)!;
      let amount: bigint;
      if ("debit" in map || "credit" in map) {
        const dr = parseAmount(cell("debit")) ?? 0n, cr = parseAmount(cell("credit")) ?? 0n;
        if (dr !== 0n && cr !== 0n) throw new Error("both a debit and a credit are filled");
        amount = cr !== 0n ? cr : dr > 0n ? -dr : dr;
      } else {
        const a = parseAmount(cell("amount"));
        if (a === null) throw new Error("no amount");
        const t = cell("drcr").toLowerCase();
        amount = /^(dr|d|debit|withdrawal)/.test(t) && a > 0n ? -a : a;
      }
      if (amount === 0n) continue;                            // a zero line carries nothing (some banks print charges of 0.00)
      const balance = "balance" in map && cell("balance") !== "" ? parseAmount(cell("balance")) : null;
      const description = cell("description") || cell("ref") || "(no narration)";
      const utr = extractUtr(`${cell("ref")} ${description}`);
      const key = `${date}|${money(amount)}|${balance === null ? "-" : money(balance)}|${fingerprint(description)}`;
      const n = (seen.get(key) ?? 0) + 1;
      seen.set(key, n);
      lines.push({ external_id: `csv:${key}:${n}`, txn_date: date, amount: money(amount), description: description.slice(0, 500), utr, balance_after: balance === null ? null : money(balance) });
    } catch (e) {
      problems.push({ row: rowNo, message: (e as Error).message });
    }
  }
  if (lines.length === 0) return { ...EMPTY, problems, columns: names, fatal: "No usable lines were found under the header." };

  const dates = lines.map((l) => l.txn_date).sort();
  let credits = 0n, debits = 0n;
  for (const l of lines) { const p = toPaise(l.amount); if (p > 0n) credits += p; else debits += -p; }

  // Running-balance check. Statements come oldest first or newest first; follow the order that fits the dates.
  let breaks = 0;
  if (lines.every((l) => l.balance_after !== null) && lines.length > 1) {
    const desc = lines[0]!.txn_date > lines[lines.length - 1]!.txn_date;
    const ordered = desc ? [...lines].reverse() : lines;
    for (let i = 1; i < ordered.length; i++) if (toPaise(ordered[i - 1]!.balance_after) + toPaise(ordered[i]!.amount) !== toPaise(ordered[i]!.balance_after)) breaks++;
  }
  return { lines, problems, columns: names, from: dates[0]!, to: dates[dates.length - 1]!, credits: money(credits), debits: money(debits), balanceBreaks: breaks, fatal: null };
}

/** The API takes up to 1000 lines a call. */
export function chunk<T>(items: T[], size = 1000): T[][] {
  const out: T[][] = [];
  for (let i = 0; i < items.length; i += size) out.push(items.slice(i, i + size));
  return out;
}

/** A bank account the company holds. A new workspace has none, and each needs a ledger account of its own to post against. */
export interface AccountForm { bank: string; last4: string; ifsc: string; kind: string }
export const blankAccount: AccountForm = { bank: "", last4: "", ifsc: "", kind: "current" };
export function accountProblem(f: AccountForm): string | null {
  if (!f.bank.trim()) return "Enter the bank's name.";
  if (!/^\d{4}$/.test(f.last4.trim())) return "Enter the last four digits of the account number.";
  if (f.ifsc.trim() && !/^[A-Z]{4}0[A-Z0-9]{6}$/.test(f.ifsc.trim().toUpperCase())) return "The IFSC should look like HDFC0001234.";
  return null;
}
export const ledgerCode = (f: AccountForm) => `BANK-${f.last4.trim()}`;
export function ledgerBody(f: AccountForm) {
  return { code: ledgerCode(f), name: `${f.bank.trim()} ····${f.last4.trim()}`, type: "asset" as const, subtype: "bank" };
}
export function bankBody(f: AccountForm, ledgerAccountId: string) {
  return { ledger_account_id: ledgerAccountId, bank_name: f.bank.trim(), account_last4: f.last4.trim(), ifsc: f.ifsc.trim() ? f.ifsc.trim().toUpperCase() : undefined, kind: f.kind as "current" };
}
