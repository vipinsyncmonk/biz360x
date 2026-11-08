/** Copilot: wording and small rules for the conversation screen. The answers, citations and actions all come from the server. */
import type { Tone } from "./compliance";

export interface Citation { ordinal: number; subject: string; subject_id: string | null; label: string }
export interface CopilotAction { id: string; kind: string; payload: Record<string, unknown>; status: string; approval_request_id: string | null }
export interface CopilotMessage { id: string; role: "user" | "assistant"; content: string; created_at: string; citations: Citation[]; actions: CopilotAction[] }
export interface CopilotThread { id: string; title: string; module: string | null; created_at: string; messages?: CopilotMessage[] }

export const MAX_QUESTION = 4000;
export const askReady = (text: string) => text.trim().length > 0 && text.length <= MAX_QUESTION;

export const SUGGESTIONS = [
  "What filings are due or overdue this week?",
  "Which suppliers are holding up my GST credit?",
  "Who owes me money?",
];

export const DEFAULT_TITLE = "New conversation";

// ---- answer text with [n] markers
export type Piece = { type: "text"; text: string } | { type: "cite"; n: number };
export interface Block { bullet: boolean; pieces: Piece[] }

/** Split an answer into paragraphs and bullet lines, and each line into text and citation markers like [2]. Only markers the answer really has a source for become citations. */
export function answerBlocks(content: string, citationCount: number): Block[] {
  const blocks: Block[] = [];
  for (const raw of content.split("\n")) {
    if (!raw.trim()) continue;
    const bullet = raw.startsWith("- ");
    const line = bullet ? raw.slice(2) : raw;
    const pieces: Piece[] = [];
    let last = 0;
    for (const m of line.matchAll(/\[(\d{1,3})\]/g)) {
      const n = Number(m[1]);
      if (n < 1 || n > citationCount) continue;
      if (m.index! > last) pieces.push({ type: "text", text: line.slice(last, m.index) });
      pieces.push({ type: "cite", n });
      last = m.index! + m[0].length;
    }
    if (last < line.length) pieces.push({ type: "text", text: line.slice(last) });
    blocks.push({ bullet, pieces });
  }
  return blocks;
}

// ---- where a citation points
export function citationHref(c: Pick<Citation, "subject" | "subject_id">): string | null {
  switch (c.subject) {
    case "obligations": return c.subject_id ? `/compliance?o=${c.subject_id}` : "/compliance";
    case "sales_documents": return c.subject_id ? `/billing/${c.subject_id}` : "/billing";
    case "itc_matches": return "/gst";
    case "notices": return "/notices";
    case "contracts": return "/legal";
    default: return null;
  }
}
export const citationCta = (subject: string): string => ({ obligations: "Open the filing", sales_documents: "Open the invoice", itc_matches: "See the differences", notices: "Open the notice", contracts: "Open the contract" } as Record<string, string>)[subject] ?? "";

// ---- proposed actions
export const ACTION_STATE: Record<string, { label: string; tone: Tone }> = {
  proposed: { label: "Proposed", tone: "prep" },
  approved: { label: "Approved", tone: "wait" },
  executed: { label: "Done", tone: "ok" },
  rejected: { label: "Rejected", tone: "due" },
  failed: { label: "Approved, but could not run", tone: "due" },
};
export function actionState(a: Pick<CopilotAction, "status" | "approval_request_id">): { label: string; tone: Tone } {
  if (a.status === "proposed" && a.approval_request_id) return { label: "Waiting for approval", tone: "wait" };
  return ACTION_STATE[a.status] ?? { label: a.status, tone: "prep" };
}
export const canRequestApproval = (a: Pick<CopilotAction, "status" | "approval_request_id">) => a.status === "proposed" && !a.approval_request_id;

export function actionTitle(a: Pick<CopilotAction, "kind" | "payload">): string {
  if (a.kind === "send_supplier_reminders") {
    const n = ((a.payload.suppliers as unknown[]) ?? (a.payload.party_ids as unknown[]) ?? []).length;
    return `Remind ${n} ${n === 1 ? "supplier" : "suppliers"} about invoices missing from GSTR-2A`;
  }
  if (a.kind === "create_task") return String(a.payload.title ?? "Add a follow-up task");
  return a.kind.replace(/_/g, " ");
}
export interface ActionItem { name: string; detail: string }
/** The rows to show under an action, so the person sees exactly who or what it touches. */
export function actionItems(a: Pick<CopilotAction, "kind" | "payload">, fmt: (v: string) => string): ActionItem[] {
  if (a.kind !== "send_supplier_reminders") return [];
  return ((a.payload.suppliers as Array<{ name: string; invoices: number; tax: string }>) ?? []).map((s) => ({ name: s.name, detail: `${s.invoices} ${s.invoices === 1 ? "invoice" : "invoices"} · ${fmt(s.tax)}` }));
}
/** What happened, in one line, once the action has run. */
export function actionResult(a: Pick<CopilotAction, "kind" | "payload" | "status">): string {
  const r = a.payload.result as Record<string, unknown> | undefined;
  if (a.status === "failed") return `It could not run: ${String(a.payload.error ?? "unknown error")}`;
  if (a.status !== "executed" || !r) return "";
  if (a.kind === "send_supplier_reminders") return `Follow-up tasks added for ${r.suppliers} ${r.suppliers === 1 ? "supplier" : "suppliers"} (${r.invoices} ${r.invoices === 1 ? "invoice" : "invoices"}).`;
  if (a.kind === "create_task") return "The task was added to your list.";
  return "";
}

// ---- the thread list
export interface ThreadGroup { heading: string; items: CopilotThread[] }
/** Today's conversations first, then everything earlier, newest first. Days are calendar days in the browser's zone. */
export function groupThreads(threads: CopilotThread[], now: Date = new Date()): ThreadGroup[] {
  const day = (d: Date) => `${d.getFullYear()}-${d.getMonth()}-${d.getDate()}`;
  const sorted = [...threads].sort((a, b) => b.created_at.localeCompare(a.created_at));
  const today = sorted.filter((t) => day(new Date(t.created_at)) === day(now));
  const earlier = sorted.filter((t) => day(new Date(t.created_at)) !== day(now));
  return [{ heading: "TODAY", items: today }, { heading: "EARLIER", items: earlier }].filter((g) => g.items.length > 0);
}
