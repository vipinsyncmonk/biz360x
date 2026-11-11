/** Pure helpers for the billing screens. The server computes every tax figure; nothing here does GST maths. */
import { isoDate, toPaise } from "./format";
import type { Tone } from "./compliance";

export interface DocRow {
  id: string; doc_type: string; number: string | null; doc_date: string; due_date: string | null; party_id: string; buyer_legal_name: string; buyer_gstin: string | null;
  taxable_total: string; grand_total: string; amount_settled: string; balance_due: string; status: string; payment_status: string; irn_status: string;
  original_document_id: string | null; days_overdue: number;
}

export type TabKey = "all" | "draft" | "unpaid" | "overdue" | "paid";
export const TABS: Array<[TabKey, string]> = [["all", "All"], ["draft", "Drafts"], ["unpaid", "Unpaid"], ["overdue", "Overdue"], ["paid", "Paid"]];

/** The query a tab sends to GET /sales-documents. */
export function tabQuery(tab: TabKey): Record<string, string> {
  switch (tab) {
    case "draft": return { status: "draft" };
    case "unpaid": return { status: "issued", payment_status: "unpaid" };
    case "overdue": return { overdue: "true" };
    case "paid": return { payment_status: "paid" };
    default: return {};
  }
}

export const DOC_TYPE_LABEL: Record<string, string> = {
  tax_invoice: "Tax invoice", credit_note: "Credit note", debit_note: "Debit note", bill_of_supply: "Bill of supply", export_invoice: "Export invoice", proforma: "Proforma",
};

export function docStatus(d: Pick<DocRow, "status" | "payment_status" | "days_overdue" | "irn_status">): { label: string; tone: Tone } {
  if (d.status === "draft") return { label: "Draft", tone: "prep" };
  if (d.status === "cancelled") return { label: "Cancelled", tone: "prep" };
  if (d.payment_status === "paid") return { label: "Paid", tone: "ok" };
  if (d.days_overdue > 0) return { label: "Overdue", tone: "due" };
  if (d.payment_status === "partially_paid") return { label: "Part paid", tone: "wait" };
  return { label: "Unpaid", tone: "wait" };
}

export function einvoiceLabel(irnStatus: string, status: string): string {
  if (status === "draft") return "Not issued";
  return ({ generated: "IRN generated", pending: "IRN pending", failed: "IRN failed", cancelled: "IRN cancelled", not_applicable: "No e-invoice", not_required: "E-invoice not required" } as Record<string, string>)[irnStatus] ?? irnStatus;
}

export function ageLabel(daysOverdue: number, dueDate: string | null, today: Date = new Date()): string {
  if (!dueDate) return "";
  if (daysOverdue > 0) return `${daysOverdue} ${daysOverdue === 1 ? "day" : "days"} overdue`;
  const days = Math.round((Date.parse(dueDate) - Date.parse(isoDate(today))) / 86_400_000);
  return days === 0 ? "Due today" : days > 0 ? `Due in ${days} ${days === 1 ? "day" : "days"}` : "";
}

// ---- the editor
export interface LineDraft { description: string; hsn_sac: string; qty: string; rate: string; gst_rate: string; discount: string }
export const GST_RATES = ["0", "5", "12", "18", "28"];
export const blankLine = (): LineDraft => ({ description: "", hsn_sac: "", qty: "1", rate: "", gst_rate: "18", discount: "0" });

const DEC = /^\d+(\.\d{1,4})?$/;

/** Why a line cannot be sent yet, or null when it is complete. Field-level hints are the server's job; this only avoids useless calls. */
export function lineProblem(l: LineDraft): string | null {
  if (!l.description.trim()) return "Describe the item";
  if (!/^[0-9]{4,8}$/.test(l.hsn_sac)) return "HSN or SAC must be 4 to 8 digits";
  if (!DEC.test(l.qty) || Number(l.qty) <= 0) return "Quantity must be more than zero";
  if (!DEC.test(l.rate)) return "Enter a rate";
  if (!DEC.test(l.discount || "0")) return "Discount must be an amount";
  if (!GST_RATES.includes(l.gst_rate)) return "Choose a GST rate";
  return null;
}

export const linesComplete = (ls: LineDraft[]) => ls.length > 0 && ls.every((l) => lineProblem(l) === null);

export function linesBody(ls: LineDraft[]) {
  return ls.map((l) => ({ description: l.description.trim(), hsn_sac: l.hsn_sac, qty: l.qty, rate: l.rate, gst_rate: Number(l.gst_rate), discount: l.discount || "0",
                          uqc: "NOS" }));
}

export interface DocForm { registrationId: string; partyId: string; partyGstId: string; docDate: string; terms: string; po: string; lines: LineDraft[]; currency?: string; rate?: string;
  /** An invoice to a customer abroad: "foreign" = the rates typed are in their currency (the invoice is in it), "inr" = the rates are rupees. */
  priceIn?: "foreign" | "inr" }

export function docBody(f: DocForm) {
  return {
    doc_type: "tax_invoice" as const, gst_adjusted: true, doc_date: f.docDate, registration_id: f.registrationId, party_id: f.partyId,
    ...(f.partyGstId ? { party_gst_id: f.partyGstId } : {}),
    ...(f.terms !== "" ? { payment_terms_days: Number(f.terms) } : {}),
    ...(f.po.trim() ? { customer_po_ref: f.po.trim() } : {}),
    ...(f.currency?.trim() ? { currency: f.currency.trim().toUpperCase() } : {}),          // an invoice to a customer outside India: what they pay in, and the rate
    ...(f.rate?.trim() ? { exchange_rate: f.rate.trim() } : {}),
    ...(f.currency?.trim() && f.priceIn === "foreign" ? { priced_in_foreign: true } : {}),
    lines: linesBody(f.lines),
  };
}

export const formReady = (f: DocForm) => !!f.registrationId && !!f.partyId && !!f.docDate && linesComplete(f.lines) && (f.terms === "" || /^\d{1,3}$/.test(f.terms));

export type CheckLevel = "ok" | "info" | "warn" | "block";
export const hasBlock = (checks: Array<{ level: string }>) => checks.some((c) => c.level === "block");

/** The issue call's defaults, spelled out because the generated type marks them required. Same as the server's own defaults. */
export const ISSUE_BODY = { create_payment_link: true, generate_eway_bill: true };

// ---- quotes
export interface QuoteForm { registrationId: string; partyId: string; partyGstId: string; title: string; validUntil: string; terms: string; advance: string; lines: LineDraft[] }

export function quoteBody(f: QuoteForm) {
  return {
    registration_id: f.registrationId, party_id: f.partyId, lines: linesBody(f.lines),
    ...(f.title.trim() ? { title: f.title.trim() } : {}),
    ...(f.validUntil ? { valid_until: f.validUntil } : {}),
    ...(f.terms !== "" ? { payment_terms_days: Number(f.terms) } : {}),
    ...(f.advance !== "" ? { advance_pct: Number(f.advance) } : {}),
  };
}

export const quoteReady = (f: QuoteForm) => !!f.registrationId && !!f.partyId && linesComplete(f.lines)
  && (f.terms === "" || /^\d{1,3}$/.test(f.terms)) && (f.advance === "" || (/^\d{1,3}(\.\d{1,2})?$/.test(f.advance) && Number(f.advance) <= 100));

/** The tax preview for a quote is the invoice preview for the same lines: one function computes both. */
export const quotePreviewBody = (f: QuoteForm, today: string) => ({
  doc_type: "tax_invoice" as const, gst_adjusted: true, doc_date: today, registration_id: f.registrationId, party_id: f.partyId,
  ...(f.partyGstId ? { party_gst_id: f.partyGstId } : {}), ...(f.terms !== "" ? { payment_terms_days: Number(f.terms) } : {}), lines: linesBody(f.lines),
});

export type QuoteAction = "send" | "revise" | "accept" | "lost" | "invoice";

/** What can be done to a quote now. Superseded versions and closed quotes have nothing. */
export function quoteActions(q: { status: string; sales_order_id: string | null; invoiced_pct: number }): QuoteAction[] {
  switch (q.status) {
    case "draft": return ["send", "revise", "lost"];
    case "sent": case "viewed": return ["send", "accept", "revise", "lost"];
    case "negotiating": return ["accept", "lost"];
    case "accepted": return q.sales_order_id && q.invoiced_pct < 100 ? ["invoice"] : [];
    default: return [];
  }
}

export const QUOTE_ACTION_LABEL: Record<QuoteAction, string> = { send: "Send", revise: "New version", accept: "Mark accepted", lost: "Mark lost", invoice: "Create invoice" };

// ---- credit notes
export const CN_REASONS: Array<[string, string, string]> = [
  ["sales_return", "Goods returned", "The customer sent items back."],
  ["post_sale_discount", "Discount after the sale", "A discount agreed after the invoice was issued."],
  ["rate_revision", "Rate revised", "The price was corrected upwards or downwards."],
  ["deficiency_in_service", "Service fell short", "A reduction for a deficiency in service."],
  ["correction", "Correction", "The invoice had an error."],
  ["other", "Other", "Anything else; add the detail in your records."],
];

export interface DocLine { line_no: number; description: string; hsn_sac: string; qty: string; uqc: string; rate: string; discount: string; gst_rate: string | number }

const QSCALE = 10_000n;
function scaledQty(q: string): bigint {
  const m = /^(\d+)(?:\.(\d{1,4}))?\d*$/.exec(q.trim());
  return m ? BigInt(m[1]!) * QSCALE + BigInt((m[2] ?? "").padEnd(4, "0")) : -1n;
}

/** Why a credit quantity cannot be used, or null when it is fine. Blank means this line is not credited. */
export function creditProblem(entered: string, supplied: string): string | null {
  if (entered.trim() === "") return null;
  const q = scaledQty(entered);
  if (q <= 0n) return "Enter a quantity above zero";
  if (q > scaledQty(supplied)) return `No more than ${supplied} was supplied`;
  return null;
}

/** The line to credit: the invoice's own rate and tax rate, the quantity coming back, and the discount in the same proportion. */
export function creditLine(l: DocLine, entered: string) {
  const q = scaledQty(entered), full = scaledQty(l.qty);
  const disc = toPaise(l.discount);
  const share = full > 0n ? (disc * q * 2n + full) / (full * 2n) : 0n;                  // rounded half up, in paise
  const sign = share < 0n ? "-" : "";
  const abs = share < 0n ? -share : share;
  return { description: l.description, hsn_sac: l.hsn_sac, qty: entered.trim(), rate: l.rate, gst_rate: Number(l.gst_rate), uqc: l.uqc, discount: `${sign}${abs / 100n}.${(abs % 100n).toString().padStart(2, "0")}` };
}

export function creditLines(lines: DocLine[], entered: Record<number, string>) {
  return lines.filter((l) => (entered[l.line_no] ?? "").trim() !== "").map((l) => creditLine(l, entered[l.line_no]!));
}

export const creditReady = (lines: DocLine[], entered: Record<number, string>) =>
  lines.every((l) => creditProblem(entered[l.line_no] ?? "", l.qty) === null) && creditLines(lines, entered).length > 0;
