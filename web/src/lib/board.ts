/** Form rules and wording for contracts, board meetings and resolutions. The server checks every rule; these only stop useless calls. */
import type { Tone } from "./compliance";
import { toPaise } from "./format";

// ---- contracts
export const CONTRACT_KINDS: Array<[string, string]> = [
  ["customer", "Customer"], ["vendor", "Vendor"], ["property", "Property"], ["employment", "Employment"], ["nda", "NDA"], ["consultant", "Consultant"], ["licence", "Licence"], ["loan", "Loan"], ["shareholder", "Shareholder"], ["other", "Other"],
];
export const VALUE_PERIODS: Array<[string, string]> = [["one_time", "One time"], ["monthly", "Per month"], ["annual", "Per year"], ["rate_card", "Rate card"]];

export interface ContractForm {
  title: string; counterparty: string; kind: string; start: string; end: string; autoRenews: boolean; renewalMonths: string; noticeDays: string;
  value: string; period: string; terms: string; law: string;
}
export const blankContract = (): ContractForm => ({ title: "", counterparty: "", kind: "customer", start: "", end: "", autoRenews: false, renewalMonths: "12", noticeDays: "60", value: "", period: "annual", terms: "", law: "India" });

const isAmount = (v: string) => /^\d+(\.\d{1,2})?$/.test(v.trim());
const isDays = (v: string, max: number) => /^\d+$/.test(v.trim()) && Number(v) <= max;

export function contractProblem(f: ContractForm): string | null {
  if (!f.title.trim()) return "Give the contract a title";
  if (f.start && f.end && f.end < f.start) return "The end date is before the start date";
  if (f.autoRenews && !f.end) return "An auto-renewing contract needs an end date";
  if (f.autoRenews && !isDays(f.noticeDays, 365)) return "Give the notice period in days (up to 365)";
  if (f.autoRenews && f.renewalMonths && !(isDays(f.renewalMonths, 120) && Number(f.renewalMonths) >= 1)) return "Renewal is 1 to 120 months";
  if (f.value.trim() && !isAmount(f.value)) return "Value is an amount like 250000";
  if (f.value.trim() && !f.period) return "Say what the value covers";
  if (f.terms.trim() && !isDays(f.terms, 365)) return "Payment terms are a number of days";
  return null;
}
export const contractBody = (f: ContractForm) => ({
  title: f.title.trim(), kind: f.kind, status: "draft",
  ...(f.counterparty.trim() ? { counterparty_name: f.counterparty.trim() } : {}),
  ...(f.start ? { start_date: f.start } : {}), ...(f.end ? { end_date: f.end } : {}),
  auto_renews: f.autoRenews,
  ...(f.autoRenews ? { notice_days: Number(f.noticeDays), ...(f.renewalMonths ? { renewal_months: Number(f.renewalMonths) } : {}) } : {}),
  ...(f.value.trim() ? { value_amount: `${toPaise(f.value) / 100n}.${(toPaise(f.value) % 100n).toString().padStart(2, "0")}`, value_period: f.period } : {}),
  ...(f.terms.trim() ? { payment_terms_days: Number(f.terms) } : {}), ...(f.law.trim() ? { governing_law: f.law.trim() } : {}),
});

/** Where a contract can go next, in the order a person usually takes it. */
export const CONTRACT_NEXT: Record<string, Array<[string, string]>> = {
  draft: [["negotiating", "Start negotiating"], ["out_for_signature", "Mark out for signature"], ["active", "Mark active"]],
  negotiating: [["out_for_signature", "Mark out for signature"], ["active", "Mark active"], ["terminated", "Terminate"]],
  out_for_signature: [["active", "Mark signed and active"], ["negotiating", "Back to negotiating"]],
  active: [["renewed", "Mark renewed"], ["expired", "Mark expired"], ["terminated", "Terminate"]],
  expired: [["renewed", "Mark renewed"]], terminated: [], renewed: [],
};

export const termKeyOk = (k: string) => /^[a-z][a-z0-9_]{1,39}$/.test(k);
/** "Late payment interest" -> "late_payment_interest" */
export const termKeyFrom = (label: string) => label.trim().toLowerCase().replace(/[^a-z0-9]+/g, "_").replace(/^[0-9_]+/, "").slice(0, 40).replace(/_+$/g, "");
export const termReady = (label: string, value: string) => termKeyOk(termKeyFrom(label)) && value.trim().length > 0 && value.length <= 2000;

// ---- meetings
export const MEETING_KINDS: Array<[string, string]> = [["board", "Board meeting"], ["agm", "Annual general meeting"], ["egm", "Extraordinary general meeting"], ["committee", "Committee meeting"], ["partners", "Partners meeting"]];
export const MEETING_LABEL: Record<string, string> = Object.fromEntries(MEETING_KINDS);

export interface Resolution { id: string; meeting_id: string | null; number: string; title: string; kind: string; passed_on: string | null; needs_mgt14: boolean; mgt14_obligation_id: string | null; status: string; signature_request_id: string | null }
export interface Meeting { id: string; kind: string; scheduled_for: string; held_on: string | null; notice_sent_on: string | null; quorum_met: boolean | null; minutes_document_id: string | null; resolutions: Resolution[]; days_since_last_board: number | null }

export interface MeetingForm { kind: string; date: string; time: string }
export const blankMeeting = (): MeetingForm => ({ kind: "board", date: "", time: "11:00" });
export const meetingReady = (f: MeetingForm) => /^\d{4}-\d{2}-\d{2}$/.test(f.date) && /^\d{2}:\d{2}$/.test(f.time);
/** A local date and time, sent as written with the +05:30 offset the business works in. */
export const meetingBody = (f: MeetingForm) => ({ kind: f.kind, scheduled_for: `${f.date}T${f.time}:00+05:30` });

const IST = { timeZone: "Asia/Kolkata", hourCycle: "h23" } as const;
/** The calendar date of a moment in India, "2026-10-15". The server stores meeting times in UTC. */
export const istDate = (iso: string): string => new Intl.DateTimeFormat("en-CA", { ...IST, year: "numeric", month: "2-digit", day: "2-digit" }).format(new Date(iso));
/** The clock time of a moment in India, "11:00". */
export const istTime = (iso: string): string => new Intl.DateTimeFormat("en-GB", { ...IST, hour: "2-digit", minute: "2-digit" }).format(new Date(iso));

export function heldProblem(heldOn: string, today: string): string | null {
  if (!heldOn) return "Choose the date it was held";
  if (heldOn > today) return "A meeting cannot have been held in the future";
  return null;
}

export function meetingState(m: Pick<Meeting, "held_on" | "quorum_met" | "scheduled_for">, today: string): { label: string; tone: Tone } {
  if (m.held_on) return m.quorum_met ? { label: "Held, quorum met", tone: "ok" } : m.quorum_met === false ? { label: "Held, no quorum", tone: "due" } : { label: "Held, quorum not recorded", tone: "wait" };
  return istDate(m.scheduled_for) < today ? { label: "Date passed, not marked held", tone: "wait" } : { label: "Scheduled", tone: "prep" };
}

/** How long since the previous board meeting, flagged past the 120-day interval the Companies Act sets (starter figure: have the company secretary confirm). */
export const BOARD_GAP_DAYS = 120;
export const gapWarning = (m: Pick<Meeting, "kind" | "days_since_last_board">) => m.kind === "board" && m.days_since_last_board !== null && m.days_since_last_board > BOARD_GAP_DAYS;

// ---- resolutions
export const RES_KINDS: Array<[string, string]> = [["board", "Board"], ["ordinary", "Ordinary"], ["special", "Special"], ["circular", "By circulation"]];
export const RES_STATUS: Record<string, { label: string; tone: Tone }> = {
  draft: { label: "Draft", tone: "prep" }, circulated: { label: "Circulated", tone: "wait" }, passed: { label: "Passed", tone: "ok" }, failed: { label: "Failed", tone: "due" }, withdrawn: { label: "Withdrawn", tone: "prep" },
};
/** What can be done to a resolution now. Mirrors the server's allowed moves. */
export function resolutionActions(status: string): Array<[string, string]> {
  switch (status) {
    case "draft": return [["circulated", "Circulate"], ["passed", "Mark passed"], ["withdrawn", "Withdraw"]];
    case "circulated": return [["passed", "Mark passed"], ["failed", "Mark failed"], ["withdrawn", "Withdraw"]];
    default: return [];
  }
}
export interface ResolutionForm { number: string; title: string; kind: string; meetingId: string }
export const blankResolution = (meetingId = ""): ResolutionForm => ({ number: "", title: "", kind: "board", meetingId });
export const resolutionReady = (f: ResolutionForm) => f.number.trim().length > 0 && f.number.length <= 40 && f.title.trim().length > 0 && f.title.length <= 300;
export const resolutionBody = (f: ResolutionForm) => ({ number: f.number.trim(), title: f.title.trim(), kind: f.kind, ...(f.meetingId ? { meeting_id: f.meetingId } : {}) });
/** A suggested number: the year and the next serial among those with the same prefix. */
export function nextResolutionNumber(existing: string[], year: number): string {
  const prefix = `${year}/`;
  const n = existing.filter((x) => x.startsWith(prefix)).map((x) => Number(x.slice(prefix.length))).filter((x) => Number.isInteger(x));
  return `${prefix}${String(Math.max(0, ...n) + 1).padStart(3, "0")}`;
}
