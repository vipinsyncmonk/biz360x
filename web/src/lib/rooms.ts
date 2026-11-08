/** Data rooms: a chosen set of vault documents shared with named outside viewers (investors, lenders, auditors). */
import { istDate } from "./board";

export interface RoomAccess { email: string; last_viewed_at: string | null }
export interface Room { id: string; name: string; purpose: string; expires_at: string | null; watermark: boolean; allow_download: boolean; document_ids: string[]; access: RoomAccess[] }

export const PURPOSES: Array<[string, string]> = [["fundraise", "Fundraise"], ["lender", "Lender"], ["audit", "Audit"], ["acquisition", "Acquisition"], ["other", "Other"]];
export const PURPOSE_LABEL: Record<string, string> = Object.fromEntries(PURPOSES);

export interface RoomForm { name: string; purpose: string; expires: string; watermark: boolean; allowDownload: boolean; emails: string; documentIds: string[] }
export const blankRoom = (): RoomForm => ({ name: "", purpose: "fundraise", expires: "", watermark: true, allowDownload: false, emails: "", documentIds: [] });
export const roomForm = (r: Room): RoomForm => ({
  name: r.name, purpose: r.purpose, expires: r.expires_at ? istDate(r.expires_at) : "", watermark: r.watermark, allowDownload: r.allow_download,
  emails: r.access.map((a) => a.email).join("\n"), documentIds: [...r.document_ids],
});

const EMAIL = /^[^@\s]+@[^@\s]+\.[^@\s]+$/;
/** People can be separated by commas, semicolons, spaces or new lines. Lower-cased, no repeats, order kept. */
export function parseEmails(text: string): string[] {
  const seen = new Set<string>();
  for (const part of text.split(/[\s,;]+/)) { const e = part.trim().toLowerCase(); if (e) seen.add(e); }
  return [...seen];
}
export const badEmails = (text: string): string[] => parseEmails(text).filter((e) => !EMAIL.test(e));

export function roomProblem(f: RoomForm, today: string): string | null {
  if (!f.name.trim()) return "Give the room a name";
  if (f.name.trim().length > 120) return "The name is too long";
  const bad = badEmails(f.emails);
  if (bad.length) return `${bad[0]} is not a valid email address`;
  if (f.expires && f.expires <= today) return "Choose an expiry date after today";
  if (f.documentIds.length === 0) return "Choose at least one document";
  return null;
}

/** Access ends at the close of the chosen day in India. */
export const roomBody = (f: RoomForm) => ({
  name: f.name.trim(), purpose: f.purpose, expires_at: f.expires ? `${f.expires}T23:59:59+05:30` : null, watermark: f.watermark, allow_download: f.allowDownload,
  document_ids: f.documentIds, access: parseEmails(f.emails).map((email) => ({ email })),
});

/** "Expires 30 Nov", "Expired 30 Oct" or "No expiry". */
export function expiryNote(r: Pick<Room, "expires_at">, now: Date = new Date()): { label: string; expired: boolean } {
  if (!r.expires_at) return { label: "No expiry", expired: false };
  const expired = new Date(r.expires_at).getTime() < now.getTime();
  const [, m, d] = istDate(r.expires_at).split("-");
  const mon = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"][Number(m) - 1];
  return { label: `${expired ? "Expired" : "Expires"} ${Number(d)} ${mon}`, expired };
}

/** "3 viewers, 1 has opened it" */
export function viewerNote(access: RoomAccess[]): string {
  if (access.length === 0) return "No viewers yet";
  const seen = access.filter((a) => a.last_viewed_at).length;
  const n = `${access.length} viewer${access.length === 1 ? "" : "s"}`;
  return seen === 0 ? `${n}, none has opened it` : `${n}, ${seen} ${seen === 1 ? "has" : "have"} opened it`;
}
