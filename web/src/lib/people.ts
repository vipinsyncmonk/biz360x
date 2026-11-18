// People files: an employee's address and emergency contact, proofs, tasks, and the team's leave. The server decides everything
// (whose record, what is allowed); this words it and stops useless calls.
import { ApiProblem, toProblem } from "./api";
import type { Tone } from "./compliance";

// ------------------------------------------------------------------ address and emergency contact
export interface Contact {
  address_line1: string | null; address_line2: string | null; city: string | null; state_code: string | null; pincode: string | null;
  emergency_name: string | null; emergency_relation: string | null; emergency_mobile: string | null; updated_at: string | null;
}
export interface ContactForm { line1: string; line2: string; city: string; state: string; pincode: string; name: string; relation: string; mobile: string }

export const blankContact = (): ContactForm => ({ line1: "", line2: "", city: "", state: "", pincode: "", name: "", relation: "", mobile: "" });
export const contactForm = (c: Contact | null | undefined): ContactForm => !c ? blankContact() : ({
  line1: c.address_line1 ?? "", line2: c.address_line2 ?? "", city: c.city ?? "", state: c.state_code ?? "", pincode: c.pincode ?? "",
  name: c.emergency_name ?? "", relation: c.emergency_relation ?? "", mobile: c.emergency_mobile ?? "",
});

/** Why the form cannot be saved, or null. The address is whole or empty; so is the emergency contact. */
export function contactProblem(f: ContactForm): string | null {
  const addr = [f.line1, f.city, f.state, f.pincode].map((v) => v.trim());
  if (addr.some(Boolean) && !addr.every(Boolean)) return "Give the whole address (line 1, city, state and PIN code), or leave it empty";
  if (f.pincode.trim() && !/^[1-9][0-9]{5}$/.test(f.pincode.trim())) return "A PIN code is six digits";
  const em = [f.name, f.relation, f.mobile].map((v) => v.trim());
  if (em.some(Boolean) && !em.every(Boolean)) return "Give the emergency contact's name, relation and mobile, or leave them empty";
  if (f.mobile.trim() && !/^\+[1-9][0-9]{7,14}$/.test(f.mobile.trim())) return "Put the country code on the mobile number, like +919812345678";
  return null;
}
const orNull = (v: string) => v.trim() || null;
export const contactBody = (f: ContactForm) => ({
  address_line1: orNull(f.line1), address_line2: orNull(f.line2), city: orNull(f.city), state_code: orNull(f.state), pincode: orNull(f.pincode),
  emergency_name: orNull(f.name), emergency_relation: orNull(f.relation), emergency_mobile: orNull(f.mobile),
});

// ------------------------------------------------------------------ proofs
export type ProofKind = "identity" | "address" | "education" | "bank" | "tax" | "other";
export const PROOF_KINDS: Array<{ value: ProofKind; label: string }> = [
  { value: "identity", label: "Identity" }, { value: "address", label: "Address" }, { value: "education", label: "Education" },
  { value: "bank", label: "Bank" }, { value: "tax", label: "Tax" }, { value: "other", label: "Other" },
];
export const PROOF_MAX_BYTES = 5 * 1024 * 1024;
export interface Proof { id: string; kind: ProofKind; label: string; file_name: string; mime_type: string; size_bytes: number; status: "pending" | "accepted" | "rejected"; uploaded_at: string; decided_at: string | null; hr_note: string | null }
export const PROOF_STATUS: Record<Proof["status"], { label: string; tone: Tone }> = {
  pending: { label: "With HR", tone: "wait" }, accepted: { label: "Accepted", tone: "ok" }, rejected: { label: "Not accepted", tone: "due" },
};
export function proofProblem(file: { name: string; size: number } | null, label: string): string | null {
  if (!file) return "Choose a file";
  if (file.size === 0) return "That file is empty";
  if (file.size > PROOF_MAX_BYTES) return "Files are limited to 5 MB";
  if (!/\.(pdf|jpe?g|png)$/i.test(file.name)) return "Upload a PDF, JPEG or PNG";
  if (!label.trim()) return "Say what it is, like “Aadhaar card”";
  return null;
}
export const fileSize = (n: number) => n >= 1024 * 1024 ? `${(n / 1024 / 1024).toFixed(1)} MB` : `${Math.max(1, Math.round(n / 1024))} KB`;
export const kindLabel = (k: string) => PROOF_KINDS.find((x) => x.value === k)?.label ?? k;

/** Open a file in a new tab. A plain link cannot carry the session token, so it is fetched as a blob first. */
export async function openFile(fetcher: () => Promise<{ data?: unknown; error?: unknown; response: Response }>): Promise<void> {
  const tab = window.open("", "_blank");
  try {
    const { data, error, response } = await fetcher();
    if (error !== undefined || !data) throw response ? await toProblem(response) : new ApiProblem(0, "network", "Could not reach the server");
    const url = URL.createObjectURL(data as Blob);
    if (tab) tab.location.href = url; else window.location.href = url;
    setTimeout(() => URL.revokeObjectURL(url), 5 * 60_000);
  } catch (e) {
    tab?.close();
    throw e;
  }
}

// ------------------------------------------------------------------ tasks
export interface MyTask { id: string; title: string; detail: string | null; due_on: string | null; status: "open" | "done"; created_at: string; done_at: string | null }
/** When a task is due, and whether that is a problem. `today` is an ISO date. */
export function dueLine(t: Pick<MyTask, "due_on" | "status">, today: string, shortDate: (iso: string) => string): { text: string; late: boolean } {
  if (t.status === "done") return { text: "Done", late: false };
  if (!t.due_on) return { text: "No date", late: false };
  if (t.due_on < today) return { text: `Was due ${shortDate(t.due_on)}`, late: true };
  return { text: t.due_on === today ? "Due today" : `Due ${shortDate(t.due_on)}`, late: false };
}
export const taskProblem = (title: string, employeeId: string) => !employeeId ? "Choose a person" : !title.trim() ? "Say what they should do" : null;

// ------------------------------------------------------------------ the team
export interface TeamLeave {
  id: string; employee_id: string; employee_name: string; leave_type_name: string; from: string; to: string; days: string; lop_days: string; half_day: boolean; reason: string | null;
  status: string; decided_at: string | null; decision_note: string | null; decided_by_me: boolean; mine_to_approve: boolean;
}
export interface MyTeam { manager: { id: string; full_name: string; designation: string | null } | null; reports: Array<{ id: string; employee_code: string; full_name: string; designation: string | null }>; leave: TeamLeave[]; pending: number }
export const hasTeam = (t: Pick<MyTeam, "manager" | "reports"> | null | undefined) => !!t && (t.reports.length > 0 || !!t.manager);
/** Why a manager cannot approve a request, or null when they can. Rejecting is always possible. */
export const cannotApprove = (r: Pick<TeamLeave, "mine_to_approve" | "status">): string | null =>
  r.status !== "pending" ? null : r.mine_to_approve ? null : "Has unpaid days: HR decides it";
