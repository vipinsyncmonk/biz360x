// A person's work queue and the company's "who does what". The server decides what is whose; this only words it and shapes the forms.
import type { Tone } from "./compliance";

export type WorkKind = "approval" | "filing" | "task" | "leave" | "document_request" | "holiday";
export interface WorkItem { kind: WorkKind; id: string | null; entity_id: string; entity_name: string; title: string; detail: string | null; due_on: string | null; overdue: boolean; href: string }
export interface MyWork { data: WorkItem[]; total: number; overdue: number; counts: Record<string, number> }

export const KIND_LABEL: Record<WorkKind, string> = { approval: "To approve", filing: "Filing", task: "Task", leave: "Leave to decide", document_request: "Documents asked for", holiday: "Holiday" };
export const KIND_TONE: Record<WorkKind, Tone> = { approval: "wait", filing: "prep", task: "prep", leave: "wait", document_request: "prep", holiday: "ok" };

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const dayNumber = (iso: string) => Math.round(Date.UTC(Number(iso.slice(0, 4)), Number(iso.slice(5, 7)) - 1, Number(iso.slice(8, 10))) / 86_400_000);

/** "Overdue by 3 days", "Due today", "Due tomorrow", "Due 14 Nov", or "No date". `today` is YYYY-MM-DD. */
export function dueText(due: string | null, today: string): string {
  if (!due) return "No date";
  const d = dayNumber(due) - dayNumber(today);
  if (d < 0) return `Overdue by ${-d} ${d === -1 ? "day" : "days"}`;
  if (d === 0) return "Due today";
  if (d === 1) return "Due tomorrow";
  return `Due ${Number(due.slice(8, 10))} ${MONTHS[Number(due.slice(5, 7)) - 1]}`;
}

/** The queue for the company on screen, and what waits in the person's other companies. Approvals of this company are left out of `here`
 *  because they have their own card with the buttons to decide them. */
export function splitWork(items: WorkItem[], entityId: string | null): { here: WorkItem[]; elsewhere: WorkItem[] } {
  return { here: items.filter((i) => i.entity_id === entityId && i.kind !== "approval"), elsewhere: items.filter((i) => i.entity_id !== entityId) };
}

/** "3 things in Acme Pvt Ltd, 1 in Beta LLP" for the companies not on screen. */
export function elsewhereText(items: WorkItem[]): string {
  const by = new Map<string, number>();
  for (const i of items) by.set(i.entity_name, (by.get(i.entity_name) ?? 0) + 1);
  return [...by.entries()].map(([name, n], idx) => (idx === 0 ? `${n} ${n === 1 ? "thing" : "things"} in ${name}` : `${n} in ${name}`)).join(", ");
}

export function queueLine(here: WorkItem[]): string {
  if (here.length === 0) return "Nothing is waiting on you here.";
  const late = here.filter((i) => i.overdue).length;
  return `${here.length} ${here.length === 1 ? "thing" : "things"} to do${late ? `, ${late} overdue` : ""}`;
}

// ------------------------------------------------------------------ who does what
export interface Slot { work_kind: string; step: "prepare" | "approve"; user_id: string; name?: string }
export interface Kind { key: string; label: string; about: string; steps: string[] }
export interface Person { user_id: string; name: string; title: string | null; approves: string[] }
export interface Matrix { data: Slot[]; kinds: Kind[]; people: Person[]; filings_reassigned?: number }

export const slotUser = (data: Slot[], kind: string, step: string) => data.find((s) => s.work_kind === kind && s.step === step)?.user_id ?? "";

/** Name a person for a step, or clear it with an empty id. */
export function setSlot(data: Slot[], kind: string, step: "prepare" | "approve", userId: string): Slot[] {
  const rest = data.filter((s) => !(s.work_kind === kind && s.step === step));
  return userId ? [...rest, { work_kind: kind, step, user_id: userId }] : rest;
}

/** Who can be chosen: anyone to prepare; to approve, only people who can approve that kind of work. */
export const choices = (people: Person[], kind: string, step: string) => (step === "prepare" ? people : people.filter((p) => p.approves.includes(kind)));

export const matrixBody = (data: Slot[]) => ({ data: data.map((s) => ({ work_kind: s.work_kind, step: s.step, user_id: s.user_id })) });

export const sameMatrix = (a: Slot[], b: Slot[]) => {
  const key = (d: Slot[]) => d.map((s) => `${s.work_kind}|${s.step}|${s.user_id}`).sort().join(",");
  return key(a) === key(b);
};

/** "Anil Accounts · Accountant" in a list of people. */
export const personLabel = (p: Person) => (p.title ? `${p.name} · ${p.title}` : p.name);

// ------------------------------------------------------------------ a new task
export interface TaskForm { title: string; assignee: string; due: string }
export const blankTask = (): TaskForm => ({ title: "", assignee: "", due: "" });
export const taskProblem = (f: TaskForm) => (!f.title.trim() ? "Say what needs doing" : f.title.length > 200 ? "Keep it under 200 characters" : null);
export const taskBody = (f: TaskForm) => ({ title: f.title.trim(), assignee_id: f.assignee || null, due_on: f.due || null });
