/** Startup track screen: wording and form rules. The cap table, round steps, vesting and eligibility are all computed by the server. */
import type { Tone } from "./compliance";
import { formatInr } from "./format";

export interface CapRow { holder: string; class_name: string; fd_shares: number; fd_pct: number }
export interface CapTable { as_of: string; scenario: string; rows: CapRow[]; total_fd_shares: number; round?: { name: string; new_shares: number; price_per_share: string; post_money: string } }
export interface RoundStep { key: string; title: string; status: "done" | "in_progress" | "next"; detail: string }
export interface Round {
  id: string; name: string; share_class_id: string | null; route: string; target_amount: string | null; pre_money_valuation: string | null; term_sheet_signed_on: string | null;
  shareholder_approval_on: string | null; closed_on: string | null; status: string; steps: RoundStep[];
}
export interface ShareClass { id: string; name: string; instrument: string; face_value: string; conversion_ratio: string }
export interface Holder { id: string; name: string; kind: string; is_non_resident: boolean }
export interface Pool { id: string; name: string; approved_on: string; size_options: number; granted: number; free: number }
export interface Grant { id: string; pool_id: string; employee_id: string; employee_name: string; granted_on: string; options: number; strike_price: string; vested_to_date: number; next_vest: { date: string; options: number } | null; status: string }
export interface Benefit { scheme_code: string; name: string; kind: string; status: string; reason: string; reference_no: string | null; valid_until: string | null; next_action: string | null; info_url: string | null }

// ---- numbers
/** Whole numbers in the Indian grouping: 1234567 -> "12,34,567". */
export function groupIndian(n: number): string {
  const s = String(Math.round(Math.abs(n)));
  const head = s.slice(0, -3), tail = s.slice(-3);
  const groups: string[] = [];
  let h = head;
  while (h.length > 2) { groups.unshift(h.slice(-2)); h = h.slice(0, -2); }
  if (h) groups.unshift(h);
  return `${n < 0 ? "-" : ""}${[...groups, tail].filter(Boolean).join(",")}`;
}
export const pctText = (p: number) => `${p.toFixed(1)}%`;
/** Bar widths scaled so the biggest holder fills the bar. */
export function barWidths(rows: Array<{ fd_pct: number }>): number[] {
  const max = Math.max(...rows.map((r) => r.fd_pct), 0);
  return rows.map((r) => (max > 0 ? Math.round((r.fd_pct / max) * 100) : 0));
}

// ---- rounds
export const ROUND_STATUS: Array<[string, string]> = [["planning", "Planning"], ["term_sheet", "Term sheet"], ["diligence", "Due diligence"], ["documentation", "Documentation"], ["allotment", "Allotment"], ["closed", "Closed"]];
export const STATUS_LABEL: Record<string, string> = { ...Object.fromEntries(ROUND_STATUS), abandoned: "Abandoned" };
export const ROUTE_LABEL: Record<string, string> = { private_placement: "Private placement", rights: "Rights issue", preferential: "Preferential allotment", bonus: "Bonus issue" };
export const isOpenRound = (r: Pick<Round, "status">) => r.status !== "closed" && r.status !== "abandoned";
/** The round to follow: the open one most recently started. */
export const currentRound = (rounds: Round[]): Round | null => rounds.find(isOpenRound) ?? null;
export function nextStatus(status: string): [string, string] | null {
  const i = ROUND_STATUS.findIndex(([k]) => k === status);
  return i >= 0 && i < ROUND_STATUS.length - 1 ? ROUND_STATUS[i + 1]! : null;
}
export const stepsLeft = (r: Pick<Round, "steps">) => r.steps.filter((s) => s.status !== "done").length;
export function roundHeadline(r: Round | null): string {
  if (!r) return "Your cap table, benefits and investor updates in one place.";
  const n = stepsLeft(r);
  return n === 0 ? `${r.name} is complete.` : `${r.name} is ${n} ${n === 1 ? "step" : "steps"} from done.`;
}
export function roundSummary(r: Round): string {
  const parts = [ROUTE_LABEL[r.route] ?? r.route];
  if (r.target_amount) parts.push(`${formatInr(r.target_amount, { decimals: false })} target`);
  if (r.pre_money_valuation) parts.push(`${formatInr(r.pre_money_valuation, { decimals: false })} pre-money`);
  return parts.join(" · ");
}

// ---- ESOP
export interface EsopSummary { size: number; granted: number; free: number; pctOfFd: number | null }
export function esopSummary(pools: Pool[], totalFd: number): EsopSummary {
  const size = pools.reduce((a, p) => a + p.size_options, 0), granted = pools.reduce((a, p) => a + p.granted, 0);
  return { size, granted, free: size - granted, pctOfFd: totalFd > 0 && size > 0 ? (size / totalFd) * 100 : null };
}

// ---- benefits
export const BENEFIT_STATUS: Record<string, { label: string; tone: Tone }> = {
  not_checked: { label: "Not checked", tone: "prep" }, not_eligible: { label: "Not yet", tone: "prep" }, eligible: { label: "Eligible", tone: "wait" }, applied: { label: "Applied", tone: "wait" },
  in_progress: { label: "In progress", tone: "wait" }, granted: { label: "Granted", tone: "ok" }, rejected: { label: "Rejected", tone: "due" }, expired: { label: "Expired", tone: "due" },
};
export const RECORDABLE: Array<[string, string]> = [["applied", "Applied"], ["in_progress", "In progress"], ["granted", "Granted"], ["rejected", "Rejected"], ["expired", "Expired"]];
export interface BenefitForm { status: string; reference: string; validUntil: string }
export const blankBenefit = (b: Pick<Benefit, "status" | "reference_no" | "valid_until">): BenefitForm => ({ status: RECORDABLE.some(([k]) => k === b.status) ? b.status : "applied", reference: b.reference_no ?? "", validUntil: b.valid_until ?? "" });
export const benefitBody = (f: BenefitForm) => ({ status: f.status, reference_no: f.reference.trim() || null, valid_until: f.validUntil || null });
export const benefitsChecked = (list: Benefit[]) => list.length > 0 && list.every((b) => b.status !== "not_checked");

// ---- forms
const PRICE = /^\d{1,10}(\.\d{1,4})?$/;
const AMOUNT = /^\d{1,13}(\.\d{1,2})?$/;
const WHOLE = /^[1-9]\d{0,11}$/;
const positive = (v: string) => /[1-9]/.test(v);

export interface ClassForm { name: string; instrument: string; face: string; ratio: string }
export const INSTRUMENTS: Array<[string, string]> = [["equity", "Equity"], ["ccps", "CCPS"], ["ocps", "OCPS"], ["ccd", "CCD"], ["safe", "SAFE"], ["warrant", "Warrant"]];
export const blankClass = (): ClassForm => ({ name: "", instrument: "equity", face: "10", ratio: "1" });
export function classProblem(f: ClassForm): string | null {
  if (!f.name.trim()) return "Name the class, for example Equity or Series Seed CCPS";
  if (!PRICE.test(f.face.trim()) || !positive(f.face)) return "Face value must be a number above zero, such as 10";
  if (!PRICE.test(f.ratio.trim()) || !positive(f.ratio)) return "Conversion ratio must be a number above zero, such as 1";
  return null;
}
export const classBody = (f: ClassForm) => ({ name: f.name.trim(), instrument: f.instrument, face_value: f.face.trim(), conversion_ratio: f.ratio.trim() });

export interface HolderForm { name: string; kind: string; pan: string; foreign: boolean; country: string }
export const HOLDER_KINDS: Array<[string, string]> = [["founder", "Founder"], ["investor", "Investor"], ["angel", "Angel"], ["employee_trust", "Employee trust"], ["company", "Company"], ["other", "Other"]];
export const blankHolder = (): HolderForm => ({ name: "", kind: "founder", pan: "", foreign: false, country: "IN" });
export function holderProblem(f: HolderForm): string | null {
  if (!f.name.trim()) return "Give the holder's name";
  if (f.pan.trim() && !/^[A-Z]{5}[0-9]{4}[A-Z]$/.test(f.pan.trim().toUpperCase())) return "A PAN has five letters, four digits and a letter";
  if (!/^[A-Za-z]{2}$/.test(f.country.trim())) return "Use a two-letter country code, such as IN or SG";
  return null;
}
export const holderBody = (f: HolderForm) => ({
  name: f.name.trim(), kind: f.kind, ...(f.pan.trim() ? { pan: f.pan.trim().toUpperCase() } : {}), is_non_resident: f.foreign || f.country.trim().toUpperCase() !== "IN", country: f.country.trim().toUpperCase(),
});

export interface AllotmentForm { holderId: string; classId: string; roundId: string; quantity: string; price: string; received: string; allotted: string }
export const blankAllotment = (): AllotmentForm => ({ holderId: "", classId: "", roundId: "", quantity: "", price: "", received: "", allotted: "" });
export function allotmentProblem(f: AllotmentForm, today: string): string | null {
  if (!f.holderId) return "Choose who the shares are for";
  if (!f.classId) return "Choose the share class";
  if (!WHOLE.test(f.quantity.trim())) return "Enter the number of shares as a whole number";
  if (!PRICE.test(f.price.trim()) || !positive(f.price)) return "Enter the issue price per share, such as 2000 or 12.3456";
  if (f.received && f.received > today) return "Money cannot have been received in the future";
  if (f.allotted && f.allotted > today) return "Shares cannot have been allotted in the future";
  if (f.received && f.allotted) {
    const days = (Date.parse(f.allotted) - Date.parse(f.received)) / 86_400_000;
    if (days > 60) return "Shares must be allotted within 60 days of receiving the money";
    if (days < 0) return "The allotment cannot be before the money arrived";
  }
  return null;
}
export const allotmentBody = (f: AllotmentForm) => ({
  shareholder_id: f.holderId, share_class_id: f.classId, ...(f.roundId ? { round_id: f.roundId } : {}), quantity: Number(f.quantity), issue_price: f.price.trim(),
  ...(f.received ? { money_received_on: f.received } : {}), ...(f.allotted ? { allotted_on: f.allotted } : {}),
});

export interface RoundForm { name: string; classId: string; target: string; preMoney: string; termSheet: string }
export const blankRound = (): RoundForm => ({ name: "", classId: "", target: "", preMoney: "", termSheet: "" });
export function roundProblem(f: RoundForm): string | null {
  if (!f.name.trim()) return "Name the round, for example Seed extension";
  if (f.target.trim() && (!AMOUNT.test(f.target.trim()) || !positive(f.target))) return "The target amount must be a number above zero, such as 5000000";
  if (f.preMoney.trim() && (!AMOUNT.test(f.preMoney.trim()) || !positive(f.preMoney))) return "The pre-money valuation must be a number above zero";
  return null;
}
export const roundBody = (f: RoundForm) => ({
  name: f.name.trim(), ...(f.classId ? { share_class_id: f.classId } : {}), ...(f.target.trim() ? { target_amount: f.target.trim() } : {}),
  ...(f.preMoney.trim() ? { pre_money_valuation: f.preMoney.trim() } : {}), ...(f.termSheet ? { term_sheet_signed_on: f.termSheet } : {}),
});

export interface PoolForm { name: string; approved: string; size: string }
export const blankPool = (): PoolForm => ({ name: "", approved: "", size: "" });
export function poolProblem(f: PoolForm): string | null {
  if (!f.name.trim()) return "Name the pool, for example ESOP 2025";
  if (!f.approved) return "Say when the shareholders approved it";
  if (!WHOLE.test(f.size.trim())) return "Enter the pool size in options, as a whole number";
  return null;
}
export const poolBody = (f: PoolForm) => ({ name: f.name.trim(), approved_on: f.approved, size_options: Number(f.size) });

export interface GrantForm { poolId: string; employeeId: string; grantedOn: string; options: string; strike: string; cliff: string; vesting: string; frequency: string }
export const blankGrant = (): GrantForm => ({ poolId: "", employeeId: "", grantedOn: "", options: "", strike: "", cliff: "12", vesting: "48", frequency: "monthly" });
export function grantProblem(f: GrantForm, free: number | null): string | null {
  if (!f.poolId) return "Choose the pool";
  if (!f.employeeId) return "Choose the employee";
  if (!f.grantedOn) return "Say when the options were granted";
  if (!WHOLE.test(f.options.trim())) return "Enter the number of options as a whole number";
  if (free !== null && Number(f.options) > free) return `Only ${groupIndian(free)} options are left in this pool`;
  if (!PRICE.test(f.strike.trim())) return "Enter the strike price per option, such as 10 (0 is allowed)";
  if (!/^\d{1,3}$/.test(f.cliff.trim()) || !/^[1-9]\d{0,2}$/.test(f.vesting.trim())) return "Cliff and vesting are whole months";
  if (Number(f.cliff) > Number(f.vesting)) return "The cliff cannot be longer than the vesting period";
  return null;
}
export const grantBody = (f: GrantForm) => ({
  pool_id: f.poolId, employee_id: f.employeeId, granted_on: f.grantedOn, options: Number(f.options), strike_price: f.strike.trim(),
  cliff_months: Number(f.cliff), vesting_months: Number(f.vesting), vesting_frequency: f.frequency,
});

// ---- investor update
export interface UpdateInput { company: string; month: string; cash: string | null; burn: string | null; runway: number | null; revenue: string | null; team: number | null; roundLine: string | null; note: string }
/** A plain-text update drafted from the books. It says "not available" rather than guessing a number. */
export function investorUpdate(u: UpdateInput): string {
  const money = (v: string | null) => (v === null ? "not available" : formatInr(v, { decimals: false }));
  const lines = [
    `${u.company}: investor update, ${u.month}`, "",
    "Numbers from our books",
    `- Cash in bank: ${money(u.cash)}`,
    `- Net burn per month: ${u.burn === null ? "none (cash did not fall)" : money(u.burn)}`,
    `- Runway: ${u.runway === null ? "not available" : `${u.runway} months`}`,
    `- Revenue this year so far: ${money(u.revenue)}`,
    `- Team: ${u.team === null ? "not available" : u.team}`,
  ];
  if (u.roundLine) lines.push("", "Fundraise", `- ${u.roundLine}`);
  lines.push("", "From the founder", u.note.trim() || "[Highlights, asks and hires. Written by you.]");
  return lines.join("\n");
}
