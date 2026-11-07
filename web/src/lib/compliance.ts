import { addDays, isoDate, shortDate, sumMoney } from "./format";

export type Tone = "due" | "ok" | "wait" | "prep";

export interface Obl {
  id?: string;
  status?: string;
  due_on?: string | null;
  days_left?: number | null;
  filed_at?: string | null;
  late_fee_accrued?: string | null;
  rule?: { title?: string; authority_code?: string; code?: string } | null;
}

const STATUS: Record<string, { label: string; tone: Tone }> = {
  overdue: { label: "Overdue", tone: "due" },
  awaiting_approval: { label: "Awaiting approval", tone: "due" },
  awaiting_client: { label: "Awaiting client", tone: "due" },
  in_preparation: { label: "In preparation", tone: "prep" },
  upcoming: { label: "Upcoming", tone: "wait" },
  ready_to_file: { label: "Ready to file", tone: "wait" },
  filed: { label: "Filed", tone: "ok" },
  waived: { label: "Waived", tone: "ok" },
  not_applicable: { label: "Not applicable", tone: "prep" },
};

export function statusInfo(status: string | undefined): { label: string; tone: Tone } {
  return STATUS[status ?? ""] ?? { label: status ?? "Unknown", tone: "prep" };
}

/** Statuses that are finished or irrelevant: nothing left to do. */
export const CLOSED = new Set(["filed", "waived", "not_applicable"]);
/** Statuses where a person has to act. */
export const NEEDS_ACTION = new Set(["overdue", "awaiting_approval", "awaiting_client"]);

export const isOpen = (o: Obl) => !CLOSED.has(o.status ?? "");

export function daysLabel(daysLeft: number | null | undefined): string {
  if (daysLeft === null || daysLeft === undefined) return "";
  if (daysLeft === 0) return "Due today";
  if (daysLeft === 1) return "Due tomorrow";
  if (daysLeft === -1) return "1 day overdue";
  if (daysLeft < 0) return `${-daysLeft} days overdue`;
  return `${daysLeft} days left`;
}

export interface Kpis {
  dueSoon: number;
  needsAction: number;
  overdue: number;
  lateFees: string;
}

export function kpis(obls: Obl[], windowDays = 7): Kpis {
  const open = obls.filter(isOpen);
  return {
    dueSoon: open.filter((o) => (o.days_left ?? 999) >= 0 && (o.days_left ?? 999) <= windowDays).length,
    needsAction: open.filter((o) => NEEDS_ACTION.has(o.status ?? "")).length,
    overdue: open.filter((o) => o.status === "overdue" || (o.days_left ?? 0) < 0).length,
    lateFees: sumMoney(obls.map((o) => o.late_fee_accrued)),
  };
}

export interface Standing {
  authority: string;
  open: number;
  lastFiled: string | null;
  tone: Tone;
  label: string;
}

/** One row per authority: open items, the latest filing date, and how worried to be. */
export function standing(obls: Obl[]): Standing[] {
  const by = new Map<string, Obl[]>();
  for (const o of obls) {
    const a = o.rule?.authority_code ?? "Other";
    by.set(a, [...(by.get(a) ?? []), o]);
  }
  return [...by.entries()]
    .map(([authority, list]) => {
      const open = list.filter(isOpen);
      const filed = list.map((o) => o.filed_at).filter((x): x is string => !!x).sort();
      const attention = open.some((o) => NEEDS_ACTION.has(o.status ?? "") || (o.days_left ?? 1) < 0);
      const tone: Tone = attention ? "due" : open.length ? "wait" : "ok";
      return { authority, open: open.length, lastFiled: filed.length ? filed[filed.length - 1]!.slice(0, 10) : null, tone,
               label: attention ? "Action due" : open.length ? "On track" : "Clear" };
    })
    .sort((a, b) => a.authority.localeCompare(b.authority));
}

export interface CalDay { date: string; items: Array<{ obligation_id?: string; title?: string; authority?: string; status?: string }> }

export interface Week { label: string; days: Array<{ date: string; day: string; items: CalDay["items"] }> }

/** Group calendar days into consecutive 7-day weeks starting at `from`; empty weeks are kept so the grid does not jump. */
export function weekBuckets(days: CalDay[], from: Date, weeks = 5): Week[] {
  const out: Week[] = [];
  for (let w = 0; w < weeks; w++) {
    const start = addDays(from, w * 7);
    const end = addDays(start, 6);
    const s = isoDate(start), e = isoDate(end);
    out.push({
      label: `${shortDate(s)} – ${shortDate(e)}`.toUpperCase(),
      days: days.filter((d) => d.date >= s && d.date <= e).map((d) => ({ date: d.date, day: shortDate(d.date), items: d.items })),
    });
  }
  return out;
}

/** A month as a Monday-first grid of weeks; cells outside the month are null. */
export function monthGrid(year: number, month0: number): Array<Array<number | null>> {
  const first = new Date(year, month0, 1);
  const lead = (first.getDay() + 6) % 7;
  const total = new Date(year, month0 + 1, 0).getDate();
  const cells: Array<number | null> = [...Array(lead).fill(null), ...Array.from({ length: total }, (_, i) => i + 1)];
  while (cells.length % 7) cells.push(null);
  const rows: Array<Array<number | null>> = [];
  for (let i = 0; i < cells.length; i += 7) rows.push(cells.slice(i, i + 7));
  return rows;
}
