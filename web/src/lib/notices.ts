/** Helpers for the notice desk. Dates, drafts and reminders are the server's; these only word things and stop useless calls. */
import type { Tone } from "./compliance";
import { toPaise } from "./format";
import { daysUntil } from "./legal";

export interface Notice {
  id: string; authority_code: string; registration_id: string | null; form_code: string; reference_no: string | null; period_label: string | null; received_on: string;
  reply_due_on: string | null; hearing_on: string | null; demand_amount: string | null; status: string; summary: string | null; linked_obligation_id: string | null; document_ids: string[];
}

export const AUTHORITIES: Array<[string, string]> = [
  ["GSTN", "GST (GSTN / CBIC)"], ["CBDT", "Income Tax"], ["MCA", "Corporate Affairs (MCA)"], ["EPFO", "Provident Fund (EPFO)"], ["ESIC", "State Insurance (ESIC)"],
  ["RBI", "Reserve Bank of India"], ["DPIIT", "DPIIT"], ["MSME", "Ministry of MSME"], ["DGFT", "Foreign Trade (DGFT)"],
  ["PT-KA", "Professional tax, Karnataka"], ["PT-MH", "Professional tax, Maharashtra"], ["PT-TN", "Professional tax, Tamil Nadu"], ["LAB-KA", "Labour department, Karnataka"],
];
export const AUTHORITY_LABEL: Record<string, string> = Object.fromEntries(AUTHORITIES);

export const NOTICE_STATUS: Record<string, { label: string; tone: Tone }> = {
  open: { label: "Open", tone: "due" }, draft_reply: { label: "Reply drafted", tone: "wait" }, replied: { label: "Replied", tone: "ok" }, hearing: { label: "Hearing", tone: "wait" },
  appealed: { label: "Appealed", tone: "wait" }, closed: { label: "Closed", tone: "prep" },
};

/** What can be done to a notice next. The first entry of a pair is the status to move to. */
export function noticeMoves(status: string): Array<[string, string]> {
  switch (status) {
    case "open": case "draft_reply": return [["replied", "Mark replied"], ["hearing", "A hearing is set"], ["closed", "Close"]];
    case "replied": return [["hearing", "A hearing is set"], ["appealed", "Appealed"], ["closed", "Close"]];
    case "hearing": return [["replied", "Mark replied"], ["appealed", "Appealed"], ["closed", "Close"]];
    case "appealed": return [["closed", "Close"]];
    default: return [];
  }
}
export const isOpenNotice = (n: Pick<Notice, "status">) => n.status !== "closed";
export const canDraft = (n: Pick<Notice, "status">) => !["closed", "replied", "appealed"].includes(n.status);

/** How close the reply is, in words. Null when there is no reply date or the notice is closed or answered. */
export function replyState(n: Pick<Notice, "status" | "reply_due_on">, today: Date = new Date()): { label: string; tone: Tone } | null {
  if (!n.reply_due_on || ["closed", "replied", "appealed"].includes(n.status)) return null;
  const d = daysUntil(n.reply_due_on, today);
  if (d < 0) return { label: `Reply overdue by ${-d} ${-d === 1 ? "day" : "days"}`, tone: "due" };
  if (d === 0) return { label: "Reply due today", tone: "due" };
  if (d <= 7) return { label: `Reply due in ${d} ${d === 1 ? "day" : "days"}`, tone: "due" };
  return { label: `Reply due in ${d} days`, tone: "wait" };
}

export interface DeskTotals { open: number; dueSoon: number; overdue: number; demand: string }
export function deskTotals(list: Notice[], today: Date = new Date()): DeskTotals {
  const open = list.filter(isOpenNotice);
  let demand = 0n, dueSoon = 0, overdue = 0;
  for (const n of open) {
    if (n.demand_amount) demand += toPaise(n.demand_amount);
    const s = replyState(n, today);
    if (!s) continue;
    if (s.label.startsWith("Reply overdue")) overdue++;
    else if (s.tone === "due") dueSoon++;
  }
  return { open: open.length, dueSoon, overdue, demand: `${demand / 100n}.${(demand % 100n).toString().padStart(2, "0")}` };
}

export interface NoticeForm { authority: string; form: string; reference: string; period: string; received: string; replyDue: string; hearing: string; demand: string; summary: string }
export const blankNotice = (today: string): NoticeForm => ({ authority: "GSTN", form: "", reference: "", period: "", received: today, replyDue: "", hearing: "", demand: "", summary: "" });
export function noticeProblem(f: NoticeForm): string | null {
  if (!f.form.trim()) return "Give the form or notice code, like ASMT-10 or DRC-01";
  if (!f.received) return "Choose the date it was received";
  if (f.replyDue && f.replyDue < f.received) return "The reply date is before the date received";
  if (f.hearing && f.hearing < f.received) return "The hearing date is before the date received";
  if (f.demand.trim() && !/^\d+(\.\d{1,2})?$/.test(f.demand.trim())) return "The demand is an amount like 125000";
  return null;
}
export const noticeBody = (f: NoticeForm) => ({
  authority_code: f.authority, form_code: f.form.trim(), received_on: f.received,
  ...(f.reference.trim() ? { reference_no: f.reference.trim() } : {}), ...(f.period.trim() ? { period_label: f.period.trim() } : {}),
  ...(f.replyDue ? { reply_due_on: f.replyDue } : {}), ...(f.hearing ? { hearing_on: f.hearing } : {}),
  ...(f.demand.trim() ? { demand_amount: `${toPaise(f.demand) / 100n}.${(toPaise(f.demand) % 100n).toString().padStart(2, "0")}` } : {}), ...(f.summary.trim() ? { summary: f.summary.trim() } : {}),
});
