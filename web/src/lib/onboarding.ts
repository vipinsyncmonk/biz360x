/** Onboarding helpers: the identifier formats the registry accepts, the answer bands, and the short explanation under each answer.
 *  The API is the authority on all of it; these only save a round trip and give the person a reason in plain words. */

export type IdKind = "cin" | "llpin" | "gstin" | "pan";

const KINDS: Array<[IdKind, RegExp]> = [
  ["cin", /^[LU][0-9]{5}[A-Z]{2}[0-9]{4}[A-Z]{3}[0-9]{6}$/],
  ["llpin", /^[A-Z]{3}-[0-9]{4}$/],
  ["gstin", /^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][1-9A-Z]Z[0-9A-Z]$/],
  ["pan", /^[A-Z]{5}[0-9]{4}[A-Z]$/],
];

export const normaliseIdentifier = (s: string) => s.replace(/\s+/g, "").toUpperCase();

export function identifierKind(raw: string): IdKind | null {
  const s = normaliseIdentifier(raw);
  return KINDS.find(([, rx]) => rx.test(s))?.[0] ?? null;
}

export type Headcount = "1-9" | "10-19" | "20-49" | "50+";
export type Turnover = "<1.5cr" | "1.5-5cr" | "5-10cr" | ">10cr";
export type Mse = "yes" | "no" | "unsure";
export type Track = "business" | "startup" | "msme";

export interface Answers { headcount: Headcount; turnover: Turnover; foreign: boolean; mse: Mse }

export const DEFAULT_ANSWERS: Answers = { headcount: "20-49", turnover: "1.5-5cr", foreign: false, mse: "unsure" };

export const HEADCOUNT: Array<[Headcount, string]> = [["1-9", "1–9"], ["10-19", "10–19"], ["20-49", "20–49"], ["50+", "50+"]];
export const TURNOVER: Array<[Turnover, string]> = [["<1.5cr", "Under ₹1.5 Cr"], ["1.5-5cr", "₹1.5–5 Cr"], ["5-10cr", "₹5–10 Cr"], [">10cr", "Over ₹10 Cr"]];
export const MSE: Array<[Mse, string]> = [["yes", "Yes"], ["no", "No"], ["unsure", "Not sure"]];

export const hints = {
  headcount: (v: Headcount) => v === "1-9" ? "Below the usual ESI and EPF thresholds. We will watch for 10 and 20." : v === "10-19" ? "ESI usually applies at 10 or more. EPF at 20." : "EPF (20 or more) and ESI (10 or more) usually both apply.",
  turnover: (v: Turnover) => v === ">10cr" ? "E-invoicing applies, and a tax audit under Sec 44AB may." : v === "5-10cr" ? "E-invoicing applies above ₹5 Cr. We will track the tax-audit threshold." : "Below the e-invoicing threshold for now. We will watch it as sales grow.",
  foreign: (v: boolean) => v ? "Adds FEMA reporting: FC-GPR on allotment and the annual FLA return." : "No FEMA reporting needed.",
  mse: (v: Mse) => v === "yes" ? "We will track the 45-day payment limit and file MSME-1." : v === "no" ? "MSME-1 stays off. We will re-check as vendors are added." : "We will check vendor Udyam numbers when you connect your books.",
};

/** The request body shared by the preview and the create call. */
export function answersBody(lookupId: string, track: Track, gstins: string[], a: Answers) {
  return { lookup_id: lookupId, track, gstins, headcount_band: a.headcount, turnover_band: a.turnover, has_foreign_shareholder: a.foreign, buys_from_mse: a.mse };
}

export const LOOKUP_POLL_MS = 1000;
export const LOOKUP_GIVE_UP_MS = 60_000;

export const isLookupFinished = (status: string | undefined) => status === "found" || status === "not_found" || status === "failed";

export const TRACK_KEY = "bzx.track";
export function isTrack(v: unknown): v is Track { return v === "business" || v === "startup" || v === "msme"; }
