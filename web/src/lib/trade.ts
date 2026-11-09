/** Exports and imports screen: wording and form rules. Rupee values, the realisation clock and every total come from the server; the preview in the form is a courtesy. */
import type { Tone } from "./compliance";
import { formatInr, shortDate } from "./format";

export interface Realisation { id: string; received_on: string; amount_foreign: string; bank_ref: string | null; firc_no: string | null; amount_inr: string | null; bank_charges: string; booked_inr: string | null; forex_gain: string | null }
export interface ShippingBill {
  id: string; sb_number: string | null; sb_date: string | null; port_code: string | null; currency: string; fob_foreign: string; exchange_rate: string; fob_inr: string; status: string; exported_on: string | null;
  sales_document_id: string | null; invoice_number: string | null; invoice_status: string | null; buyer_name: string | null; rodtep_claimed: string; rodtep_credited: string; drawback_claimed: string; drawback_received: string; brc_ref: string | null;
  realised_foreign: string; realisations: Realisation[]; realisation_state: string; realisation_due_on: string | null; days_left: number | null; outstanding_foreign: string; outstanding_inr: string;
}
export interface BillOfEntry {
  id: string; boe_number: string; boe_date: string; port_code: string; currency: string; cif_foreign: string; exchange_rate: string; cif_inr: string; bcd: string; sws: string; igst: string; other_duty: string;
  duty_total: string; status: string; duty_paid_on: string | null; supplier_name: string | null; igst_credit: string;
}
export interface Summary {
  as_of: string; financial_year_from: string; iec: string | null; lut: { state: string; valid_until: string | null; days_left: number | null };
  exports: { bills: number; value_inr: string; drafts: number }; realisation: { outstanding_bills: number; outstanding_inr: string; overdue_bills: number; overdue_inr: string; awaiting_export: number };
  claims: { rodtep_pending: string; drawback_pending: string }; imports: { bills: number; duty_inr: string; igst_credit_inr: string };
}
export interface ExportInvoice { id: string; number: string | null; doc_date: string; buyer_legal_name: string | null; grand_total: string }

// ---- states
export const REALISATION: Record<string, { label: string; tone: Tone }> = {
  awaiting_export: { label: "Awaiting export", tone: "prep" }, pending: { label: "Proceeds due", tone: "wait" }, overdue: { label: "Overdue", tone: "due" }, realised: { label: "Realised", tone: "ok" },
};
/** "Due 15 Oct, 120 days left" / "Overdue by 12 days" / "Set the date of export to start the clock". */
export function clockText(b: Pick<ShippingBill, "realisation_state" | "realisation_due_on" | "days_left">): string {
  switch (b.realisation_state) {
    case "awaiting_export": return "Record the date of export to start the nine-month clock";
    case "realised": return "All proceeds received";
    case "overdue": return `Was due ${shortDate(b.realisation_due_on)}, overdue by ${Math.abs(b.days_left ?? 0)} ${Math.abs(b.days_left ?? 0) === 1 ? "day" : "days"}`;
    default: return `Due ${shortDate(b.realisation_due_on)}, ${b.days_left} ${b.days_left === 1 ? "day" : "days"} left`;
  }
}
export const SB_STATUS: Array<[string, string]> = [["draft", "Draft"], ["filed", "Filed"], ["let_export", "Let export order"], ["shipped", "Shipped"], ["closed", "Closed"]];
export const BOE_STATUS: Record<string, { label: string; tone: Tone }> = {
  filed: { label: "Filed", tone: "prep" }, assessed: { label: "Assessed", tone: "wait" }, duty_paid: { label: "Duty paid", tone: "ok" }, cleared: { label: "Cleared", tone: "ok" },
};
export const LUT_STATE: Record<string, { label: string; tone: Tone }> = {
  valid: { label: "Valid", tone: "ok" }, expiring: { label: "Expiring soon", tone: "due" }, expired: { label: "Expired", tone: "due" }, none: { label: "None on record", tone: "due" },
};
export function lutText(l: Summary["lut"]): string {
  if (l.state === "none") return "No Letter of Undertaking is on record, so exports would be charged IGST. This screen reads it from your LUT registration.";
  if (l.state === "expired") return `It expired on ${shortDate(l.valid_until)}. File a new one before the next export.`;
  if (l.state === "expiring") return `It ends on ${shortDate(l.valid_until)}, in ${l.days_left} ${l.days_left === 1 ? "day" : "days"}. File the next one before it lapses.`;
  return l.valid_until ? `Valid until ${shortDate(l.valid_until)}.` : "Valid, with no end date recorded.";
}
export const claimsPending = (b: Pick<ShippingBill, "rodtep_claimed" | "rodtep_credited" | "drawback_claimed" | "drawback_received">): boolean =>
  Number(b.rodtep_claimed) > Number(b.rodtep_credited) || Number(b.drawback_claimed) > Number(b.drawback_received);

// ---- numbers
export const CURRENCIES = ["USD", "EUR", "GBP", "AED", "SGD", "JPY", "CNY", "AUD", "CAD", "CHF"];
const AMOUNT = /^\d{1,13}(\.\d{1,2})?$/;
const RATE = /^\d{1,8}(\.\d{1,4})?$/;
const positive = (v: string) => /[1-9]/.test(v);
/** Foreign amount times exchange rate in whole paise, rounded half up: only a preview of what the server will store. */
export function previewInr(foreign: string, rate: string): string | null {
  if (!AMOUNT.test(foreign.trim()) || !RATE.test(rate.trim()) || !positive(foreign) || !positive(rate)) return null;
  const [fi, ff = ""] = foreign.trim().split("."), [ri, rf = ""] = rate.trim().split(".");
  const f = BigInt(fi + ff.padEnd(2, "0")), r = BigInt(ri + rf.padEnd(4, "0"));
  const paise = (f * r + 5000n) / 10000n;
  return `${paise / 100n}.${(paise % 100n).toString().padStart(2, "0")}`;
}
export const previewText = (foreign: string, rate: string) => { const v = previewInr(foreign, rate); return v === null ? null : `About ${formatInr(v)}`; };

// ---- shipping bill form
export interface SbForm { number: string; date: string; port: string; currency: string; fob: string; rate: string; invoiceId: string; exportedOn: string }
export const blankSb = (): SbForm => ({ number: "", date: "", port: "", currency: "USD", fob: "", rate: "", invoiceId: "", exportedOn: "" });
export function sbProblem(f: SbForm, today: string): string | null {
  if (!/^[0-9A-Za-z/-]{1,20}$/.test(f.number.trim())) return "Enter the shipping bill number as printed, for example 7712345";
  if (!f.date) return "Enter the shipping bill date";
  if (f.date > today) return "A shipping bill cannot be dated in the future";
  if (!/^[A-Za-z]{2}[A-Za-z0-9]{4}$/.test(f.port.trim())) return "Enter the port code, such as INMAA1";
  if (!/^[A-Za-z]{3}$/.test(f.currency.trim())) return "Use a three-letter currency code, such as USD";
  if (!AMOUNT.test(f.fob.trim()) || !positive(f.fob)) return "Enter the FOB value in the foreign currency, such as 10000.00";
  if (!RATE.test(f.rate.trim()) || !positive(f.rate)) return "Enter the customs exchange rate, such as 83.2150";
  if (f.exportedOn && f.exportedOn > today) return "The date of export cannot be in the future";
  if (f.exportedOn && f.exportedOn < f.date) return "The date of export cannot be before the shipping bill date";
  return null;
}
export const sbBody = (f: SbForm) => ({
  sb_number: f.number.trim().toUpperCase(), sb_date: f.date, port_code: f.port.trim().toUpperCase(), currency: f.currency.trim().toUpperCase(), fob_foreign: f.fob.trim(), exchange_rate: f.rate.trim(),
  ...(f.invoiceId ? { sales_document_id: f.invoiceId } : {}), ...(f.exportedOn ? { exported_on: f.exportedOn } : {}),
});

// ---- realisation form
export interface RealisationForm { date: string; amount: string; bankRef: string; firc: string; inr: string; charges: string }
export const blankRealisation = (outstanding?: string): RealisationForm => ({ date: "", amount: outstanding && Number(outstanding) > 0 ? outstanding : "", bankRef: "", firc: "", inr: "", charges: "" });
export function realisationProblem(f: RealisationForm, outstanding: string, today: string): string | null {
  if (!f.date) return "Say when the money arrived";
  if (f.date > today) return "Money cannot have arrived in the future";
  if (!AMOUNT.test(f.amount.trim()) || !positive(f.amount)) return "Enter the amount received in the foreign currency";
  if (Number(f.amount) > Number(outstanding) + 0.01) return `Only ${outstanding} is still to be realised`;
  if (f.inr.trim() && (!AMOUNT.test(f.inr.trim()) || !positive(f.inr))) return "Enter the rupees the bank credited, such as 82950.00";
  if (f.charges.trim() && !f.inr.trim()) return "Bank charges go with the rupees the bank credited";
  if (f.charges.trim() && !AMOUNT.test(f.charges.trim())) return "Enter the bank's charges in rupees, such as 450.00";
  return null;
}
export const realisationBody = (f: RealisationForm) => ({
  received_on: f.date, amount_foreign: f.amount.trim(), ...(f.bankRef.trim() ? { bank_ref: f.bankRef.trim() } : {}), ...(f.firc.trim() ? { firc_no: f.firc.trim() } : {}),
  ...(f.inr.trim() ? { amount_inr: f.inr.trim() } : {}), ...(f.inr.trim() && f.charges.trim() ? { bank_charges: f.charges.trim() } : {}),
});

/** One line for money received: the foreign amount, the date, and the gain or loss on exchange when the rupees were recorded. */
export function receivedText(currency: string, r: Realisation, date: string): string {
  const gain = r.forex_gain == null ? 0 : Number(r.forex_gain);
  const fx = r.forex_gain == null ? "" : gain === 0 ? " · no exchange difference" : ` · exchange ${gain > 0 ? "gain" : "loss"} ₹${Math.abs(gain).toLocaleString("en-IN", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
  return `${currency} ${r.amount_foreign} on ${date}${r.firc_no ? ` (FIRC ${r.firc_no})` : ""}${fx}`;
}

// ---- schemes on a shipping bill
export interface ClaimsForm { rodtepClaimed: string; rodtepCredited: string; drawbackClaimed: string; drawbackReceived: string; brc: string }
export const claimsForm = (b: ShippingBill): ClaimsForm => ({ rodtepClaimed: b.rodtep_claimed, rodtepCredited: b.rodtep_credited, drawbackClaimed: b.drawback_claimed, drawbackReceived: b.drawback_received, brc: b.brc_ref ?? "" });
export function claimsProblem(f: ClaimsForm): string | null {
  for (const [v, l] of [[f.rodtepClaimed, "RoDTEP claimed"], [f.rodtepCredited, "RoDTEP credited"], [f.drawbackClaimed, "Drawback claimed"], [f.drawbackReceived, "Drawback received"]] as const) {
    if (!/^\d{1,13}(\.\d{1,2})?$/.test(v.trim())) return `${l} must be an amount, such as 12000.50 (0 is allowed)`;
  }
  if (Number(f.rodtepCredited) > Number(f.rodtepClaimed)) return "More RoDTEP is credited than was claimed";
  if (Number(f.drawbackReceived) > Number(f.drawbackClaimed)) return "More drawback is received than was claimed";
  return null;
}
export const claimsBody = (f: ClaimsForm) => ({
  rodtep_claimed: f.rodtepClaimed.trim(), rodtep_credited: f.rodtepCredited.trim(), drawback_claimed: f.drawbackClaimed.trim(), drawback_received: f.drawbackReceived.trim(), brc_ref: f.brc.trim() || null,
});

// ---- bill of entry form
export interface BoeForm { number: string; date: string; port: string; currency: string; cif: string; rate: string; bcd: string; sws: string; igst: string; other: string; paidOn: string }
export const blankBoe = (): BoeForm => ({ number: "", date: "", port: "", currency: "USD", cif: "", rate: "", bcd: "0", sws: "0", igst: "0", other: "0", paidOn: "" });
export function boeProblem(f: BoeForm, today: string): string | null {
  if (!/^[0-9A-Za-z/-]{1,20}$/.test(f.number.trim())) return "Enter the bill of entry number as printed";
  if (!f.date) return "Enter the bill of entry date";
  if (f.date > today) return "A bill of entry cannot be dated in the future";
  if (!/^[A-Za-z]{2}[A-Za-z0-9]{4}$/.test(f.port.trim())) return "Enter the port code, such as INBLR4";
  if (!/^[A-Za-z]{3}$/.test(f.currency.trim())) return "Use a three-letter currency code, such as USD";
  if (!AMOUNT.test(f.cif.trim()) || !positive(f.cif)) return "Enter the CIF value in the foreign currency";
  if (!RATE.test(f.rate.trim()) || !positive(f.rate)) return "Enter the customs exchange rate";
  for (const [v, l] of [[f.bcd, "Basic customs duty"], [f.sws, "Social welfare surcharge"], [f.igst, "IGST"], [f.other, "Other duty"]] as const) if (!AMOUNT.test(v.trim())) return `${l} must be an amount (0 is allowed)`;
  if (f.paidOn && f.paidOn > today) return "Duty cannot have been paid in the future";
  return null;
}
export const boeBody = (f: BoeForm) => ({
  boe_number: f.number.trim().toUpperCase(), boe_date: f.date, port_code: f.port.trim().toUpperCase(), currency: f.currency.trim().toUpperCase(), cif_foreign: f.cif.trim(), exchange_rate: f.rate.trim(),
  bcd: f.bcd.trim(), sws: f.sws.trim(), igst: f.igst.trim(), other_duty: f.other.trim(), ...(f.paidOn ? { status: "duty_paid", duty_paid_on: f.paidOn } : {}),
});
