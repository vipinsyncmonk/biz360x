/** Group view actions: adding a client and chasing clients whose filings wait on them. */
import type { Standing } from "./settings";

export interface ChaseResult {
  reminded: Array<{ entity_id: string; legal_name: string; items: number; recipients: number }>;
  skipped: Array<{ entity_id: string; legal_name: string; reason: string }>;
}

export const SKIP_REASON: Record<string, string> = {
  nothing_waiting: "Nothing is waiting on them now",
  no_client_contact: "No one at the client has accepted an invitation yet. Invite one from the client's Settings",
  reminded_recently: "Already reminded in the last 24 hours",
};

/** The clients to chase: every client with a filing waiting on them. */
export const chaseTargets = (rows: Standing[]): string[] => rows.filter((r) => r.waiting_filings > 0).map((r) => r.entity_id);

export const chaseLabel = (n: number) => (n === 0 ? "No one to chase" : `Chase ${n} ${n === 1 ? "client" : "clients"}`);

/** One line about what happened: "Reminded 2 clients. 1 skipped." */
export function chaseSummary(r: ChaseResult): string {
  const a = r.reminded.length, s = r.skipped.length;
  const done = a === 0 ? "No reminders were queued." : `Queued reminders for ${a} ${a === 1 ? "client" : "clients"}.`;
  return s === 0 ? done : `${done} ${s} skipped.`;
}

/** Where the add-client flow sits: the same onboarding, filed under the firm. */
export const addClientHref = (orgId: string) => `/onboarding?org=${encodeURIComponent(orgId)}`;
export const orgFromSearch = (search: string): string | null => {
  const v = new URLSearchParams(search).get("org");
  return v && /^[0-9a-f-]{36}$/i.test(v) ? v : null;
};
