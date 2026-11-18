// Fixed assets: what the register shows and what the forms send. Money stays a string; nothing here turns it into a float.
import { toPaise } from "./format";

export interface Asset {
  id: string; asset_no: string; name: string; category: string; location: string | null; serial_no: string | null;
  acquired_on: string; put_to_use_on: string | null; cost: string; residual_value: string; method: "slm" | "wdv"; life_months: number | null; rate_pct: string | null;
  purchase_bill_id: string | null; purchase_bill_line_no: number | null; bill_of_entry_id: string | null;
  booking: "reclassify" | "already_booked" | "opening"; status: "in_use" | "disposed"; disposed_on: string | null; disposal_proceeds: string | null;
  accumulated_depreciation: string; book_value: string; gain_or_loss?: string | null; schedule?: ScheduleRow[];
}
export interface ScheduleRow { month: string; amount: string; book_value_after: string; posted: boolean }
export interface AssetCategory { category: string; label: string; life_months: number; income_tax_rate_pct: number; residual_pct: string }
export interface CapLine { line_no: number; description: string; hsn_sac: string | null; qty: string; capitalisable: string; already_registered: string; remaining: string; booked_to_asset_account: boolean }
export interface CapBill { bill_id: string; bill_no: string; bill_date: string; party_id: string; supplier_name: string; document_id: string | null; grand_total: string; lines: CapLine[] }
export interface BoeSource { id: string; boe_number: string; boe_date: string; cif_inr: string; bcd: string; sws: string; other_duty: string; supplier_name: string | null; supplier_party_id: string | null }
export interface Preview { period: string; total: string; months: Array<{ month: string; total: string; assets: Array<{ asset_id: string; asset_no: string; name: string; amount: string; book_value_after: string }> }> }

export const BOOKING: Array<[Asset["booking"], string, string]> = [
  ["reclassify", "Bought this year: move the cost out of expenses", "The cost was booked to Purchases and expenses. Registering moves it to Fixed assets."],
  ["already_booked", "Already booked to an asset account", "The bill was booked straight to a fixed-asset account. This only records the asset."],
  ["opening", "From earlier books", "An asset you already had. Enter the depreciation charged so far; the opening balances hold the rest."],
];

export interface AssetForm {
  name: string; category: string; acquiredOn: string; putToUseOn: string; cost: string; residual: string;
  method: "slm" | "wdv"; lifeMonths: string; ratePct: string; booking: Asset["booking"]; openingAccumulated: string; openingThrough: string; location: string; serialNo: string;
  billId: string; billLine: string; boeId: string; partyId: string;                 // where the cost came from, when it came from a bill
}

export const blankAsset = (): AssetForm => ({ name: "", category: "computers", acquiredOn: "", putToUseOn: "", cost: "", residual: "", method: "slm", lifeMonths: "", ratePct: "", booking: "reclassify", openingAccumulated: "", openingThrough: "", location: "", serialNo: "", billId: "", billLine: "", boeId: "", partyId: "" });

const MONEY = /^\d{1,16}(\.\d{1,2})?$/;

/** Two decimals, from what was typed, without going through a float. */
export function moneyText(v: string): string {
  const p = toPaise(v.trim());
  return `${p / 100n}.${(p % 100n).toString().padStart(2, "0")}`;
}

export function assetProblem(f: AssetForm, today: string): string | null {
  if (!f.name.trim()) return "Give the asset a name";
  if (!f.acquiredOn) return "Enter the date it was acquired";
  if (f.acquiredOn > today) return "The acquisition date is in the future";
  if (f.billId && !f.billLine) return "Choose the line of the bill this asset was bought on";
  if (f.putToUseOn && f.putToUseOn < f.acquiredOn) return "It cannot be put to use before it is acquired";
  if (!MONEY.test(f.cost.trim()) || toPaise(f.cost.trim()) <= 0n) return "Enter the cost, such as 120000.00";
  if (f.residual.trim() && (!MONEY.test(f.residual.trim()) || toPaise(f.residual.trim()) >= toPaise(f.cost.trim()))) return "The residual value must be less than the cost";
  if (f.method === "slm" && f.lifeMonths.trim() && !/^[1-9]\d{0,3}$/.test(f.lifeMonths.trim())) return "The life is a whole number of months";
  if (f.method === "wdv" && !(/^\d{1,2}(\.\d{1,2})?$/.test(f.ratePct.trim()) && Number(f.ratePct) > 0)) return "Enter the yearly rate, such as 15 or 25.89";
  if (f.booking === "opening") {
    if (!f.openingThrough) return "Enter the date the opening depreciation is as of";
    const d = new Date(`${f.openingThrough}T00:00:00Z`), next = new Date(d.getTime() + 86_400_000);
    if (Number.isNaN(d.getTime()) || next.getUTCDate() !== 1) return "That date must be the last day of a month";
    if (!MONEY.test(f.openingAccumulated.trim())) return "Enter the depreciation charged so far (0 if none)";
  }
  return null;
}

export function assetBody(f: AssetForm) {
  const t = (s: string) => (s.trim() ? s.trim() : null);
  return {
    name: f.name.trim(), category: f.category, acquired_on: f.acquiredOn, put_to_use_on: f.putToUseOn || null, cost: moneyText(f.cost),
    residual_value: t(f.residual) ? moneyText(f.residual) : null, method: f.method,
    life_months: f.method === "slm" && t(f.lifeMonths) ? Number(f.lifeMonths) : null, rate_pct: f.method === "wdv" ? moneyText(f.ratePct) : null,
    booking: f.booking, opening_accumulated: f.booking === "opening" ? moneyText(f.openingAccumulated) : null, opening_through: f.booking === "opening" ? f.openingThrough : null,
    location: t(f.location), serial_no: t(f.serialNo),
    party_id: f.partyId || null, purchase_bill_id: f.billId || null, purchase_bill_line_no: f.billId && f.billLine ? Number(f.billLine) : null, bill_of_entry_id: f.boeId || null,
  };
}

export function disposeProblem(date: string, proceeds: string, asset: Asset, today: string): string | null {
  if (!date) return "Enter the date of sale";
  if (date > today) return "The date of sale is in the future";
  if (date < (asset.put_to_use_on ?? asset.acquired_on)) return "It cannot be sold before it was put to use";
  if (!MONEY.test(proceeds.trim())) return "Enter what was received, 0 for a write-off";
  return null;
}

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
export const monthLabel = (iso: string) => `${MONTHS[Number(iso.slice(5, 7)) - 1]} ${iso.slice(0, 4)}`;
export const currentPeriod = (d: Date = new Date()) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}`;

/** "3 years" for 36 months, "1 year 6 months", "8 months". */
export function lifeText(months: number | null): string {
  if (!months) return "";
  const y = Math.floor(months / 12), m = months % 12;
  return [y ? `${y} ${y === 1 ? "year" : "years"}` : "", m ? `${m} ${m === 1 ? "month" : "months"}` : ""].filter(Boolean).join(" ");
}

export const methodText = (a: Asset) => (a.method === "slm" ? `Straight line · ${lifeText(a.life_months)}` : `Written down value · ${a.rate_pct}% a year`);

/** What a purchase bill line fills in: its description, the bill date, what is left of its cost, and where it was booked. The person can change any of it. */
export function fromBillLine(b: CapBill, l: CapLine): Partial<AssetForm> {
  return { name: l.description, acquiredOn: b.bill_date, cost: l.remaining, booking: l.booked_to_asset_account ? "already_booked" : "reclassify", billId: b.bill_id, billLine: String(l.line_no), boeId: "", partyId: b.party_id };
}

/** A bill of entry fills in the goods value (the rupee CIF) and the supplier. Customs duty is not posted to the books by this app yet, so it is not in the figure. */
export function fromBoe(e: BoeSource): Partial<AssetForm> {
  return { acquiredOn: e.boe_date, cost: e.cif_inr, booking: "already_booked", boeId: e.id, partyId: e.supplier_party_id ?? "", billId: "", billLine: "" };
}

/** The duty that is a cost of the asset (everything except the IGST that comes back as credit). */
export function boeDutyForCost(e: BoeSource): string {
  const p = toPaise(e.bcd) + toPaise(e.sws) + toPaise(e.other_duty);
  return `${p / 100n}.${(p % 100n).toString().padStart(2, "0")}`;
}
