// Inventory: what the stock screen shows and what its forms send. Quantities and money stay strings; nothing here turns them into floats.
import { toPaise } from "./format";
import { moneyText } from "./assets";

export interface StockLevel { warehouse_id: string; code: string; name: string; qty: string }
export interface StockItem {
  item_id: string; sku: string; name: string; uqc: string; hsn_sac: string; is_active: boolean; sale_rate: string | null;
  qty: string; average_cost: string; value: string; last_moved_on: string | null; levels: StockLevel[];
}
export interface StockList { data: StockItem[]; totals: { value: string; items: number; out_of_stock: number }; allow_negative_stock: boolean; valuation_method: string }
export interface Movement {
  id: number; item_id: string; sku: string; item_name: string; uqc: string; warehouse_code: string; date: string; qty_in: string; qty_out: string; unit_cost: string; value: string;
  source_kind: "purchase_bill" | "sales_document" | "adjustment" | "opening" | "transfer" | "bill_of_entry"; reference: string | null; reason: string | null; note: string | null; entry_no: string | null;
}
export interface Valuation {
  as_of: string; items: Array<{ item_id: string; sku: string; name: string; uqc: string; qty: string; average_cost: string; value: string }>;
  total_value: string; ledger_balance: string; difference: string;
}

export type AdjustKind = "opening" | "count" | "write_off";
export const KINDS: Array<[AdjustKind, string, string]> = [
  ["opening", "Opening stock", "What you held when you started here, at what it cost you."],
  ["count", "Stock count", "Enter what is physically there. The difference is booked at the average cost."],
  ["write_off", "Write-off", "Damaged, lost or given away. It leaves stock at the average cost."],
];
export const BOOKINGS: Array<["" | "post" | "in_opening_balances", string]> = [
  ["", "Decide for me"],
  ["post", "Post it to the books now"],
  ["in_opening_balances", "My opening balances already include it"],
];

const QTY = /^\d{1,12}(\.\d{1,3})?$/;
const COST = /^\d{1,12}(\.\d{1,4})?$/;
const MONEY = /^\d{1,16}(\.\d{1,2})?$/;
export const GST_RATES = ["0", "0.1", "0.25", "1.5", "3", "5", "12", "18", "28", "40"];

/** "12" for 12.000, "2.5" for 2.500, "-3" for -3.000: a quantity as a person writes it. */
export function qtyText(v: string): string {
  const [whole, frac = ""] = v.split(".");
  const f = frac.replace(/0+$/, "");
  return f ? `${whole}.${f}` : whole!;
}

/** "115" for 115.0000, "98.5714" as it is: a unit cost without padding, never fewer than the paise that matter. */
export function costText(v: string): string {
  const [whole, frac = ""] = v.split(".");
  const f = frac.replace(/0+$/, "");
  return f ? `${whole}.${f.padEnd(2, "0")}` : whole!;
}

export const isPositive = (v: string) => /[1-9]/.test(v);
export const outOfStock = (i: StockItem) => !isPositive(i.qty) || i.qty.startsWith("-");

// ------------------------------------------------------------------ a new item
export interface ItemForm { sku: string; name: string; hsn: string; uqc: string; gstRate: string; purchaseRate: string; saleRate: string }
export const blankItem = (): ItemForm => ({ sku: "", name: "", hsn: "", uqc: "NOS", gstRate: "18", purchaseRate: "", saleRate: "" });

export function itemProblem(f: ItemForm): string | null {
  if (!f.sku.trim()) return "Give the item a code (SKU)";
  if (!f.name.trim()) return "Give the item a name";
  if (!/^\d{4,8}$/.test(f.hsn.trim())) return "Enter the HSN code: 4 to 8 digits";
  if (f.hsn.trim().startsWith("99")) return "An HSN starting with 99 is a service; only goods are kept in stock";
  if (!/^[A-Za-z]{2,8}$/.test(f.uqc.trim())) return "Enter the unit, such as NOS, KGS or MTR";
  if (!GST_RATES.includes(f.gstRate)) return "Choose a GST rate";
  for (const [v, what] of [[f.purchaseRate, "purchase"], [f.saleRate, "sale"]] as const) if (v.trim() && !MONEY.test(v.trim())) return `Enter the ${what} rate as an amount, such as 250.00`;
  return null;
}

export function itemBody(f: ItemForm) {
  const rate = (v: string) => (v.trim() ? moneyText(v) : null);
  return { sku: f.sku.trim(), name: f.name.trim(), kind: "goods", hsn_sac: f.hsn.trim(), uqc: f.uqc.trim().toUpperCase(), gst_rate: Number(f.gstRate),
    purchase_rate: rate(f.purchaseRate), sale_rate: rate(f.saleRate), track_inventory: true };
}

// ------------------------------------------------------------------ an adjustment
export interface AdjustForm { itemId: string; kind: AdjustKind; date: string; qty: string; counted: string; unitCost: string; booking: "" | "post" | "in_opening_balances"; reason: string }
export const blankAdjust = (itemId = "", kind: AdjustKind = "count"): AdjustForm => ({ itemId, kind, date: "", qty: "", counted: "", unitCost: "", booking: "", reason: "" });

export function adjustProblem(f: AdjustForm, today: string): string | null {
  if (!f.itemId) return "Choose the item";
  if (!f.date) return "Enter the date";
  if (f.date > today) return "The date is in the future";
  if (f.kind === "count") {
    if (!QTY.test(f.counted.trim())) return "Enter the quantity you counted, 0 if none";
  } else if (!QTY.test(f.qty.trim()) || !isPositive(f.qty)) return "Enter the quantity, such as 10 or 2.5";
  if (f.kind === "opening") {
    if (!COST.test(f.unitCost.trim())) return "Enter what one unit cost, such as 115.50";
  } else {
    if (f.unitCost.trim() && (f.kind === "write_off" || !COST.test(f.unitCost.trim()))) return f.kind === "write_off" ? "A write-off leaves at the average cost" : "Enter the cost of one unit, or leave it empty";
    if (!f.reason.trim()) return "Say why the stock is being adjusted";
  }
  return null;
}

export function adjustBody(f: AdjustForm) {
  const t = (s: string) => (s.trim() ? s.trim() : null);
  return {
    item_id: f.itemId, kind: f.kind, date: f.date,
    qty: f.kind === "count" ? null : f.qty.trim(), counted_qty: f.kind === "count" ? f.counted.trim() : null,
    unit_cost: f.kind === "write_off" ? null : t(f.unitCost), booking: f.kind === "opening" && f.booking ? f.booking : null,
    reason: f.kind === "opening" ? null : f.reason.trim(),
  };
}

/** What a count would do, before it is saved: "3 fewer than the books" or "2 more than the books". Null when it matches or cannot be read. */
export function countNote(onHand: string, counted: string, uqc: string): string | null {
  if (!QTY.test(counted.trim())) return null;
  const milli = (v: string) => { const neg = v.startsWith("-"); const [w, f = ""] = v.replace("-", "").split("."); const n = BigInt(w!) * 1000n + BigInt(f.padEnd(3, "0").slice(0, 3)); return neg ? -n : n; };
  const d = milli(counted.trim()) - milli(onHand);
  if (d === 0n) return "That matches the books: there is nothing to adjust";
  const abs = d < 0n ? -d : d;
  const text = qtyText(`${abs / 1000n}.${(abs % 1000n).toString().padStart(3, "0")}`);
  return d < 0n ? `${text} ${uqc} fewer than the books: they will be written off` : `${text} ${uqc} more than the books: they will be added`;
}

const SOURCE: Record<Movement["source_kind"], string> = { purchase_bill: "Purchase bill", sales_document: "Sale", adjustment: "Adjustment", opening: "Opening stock", transfer: "Move", bill_of_entry: "Customs duty" };
const REASON: Record<string, string> = { count: "Stock count", write_off: "Write-off", opening: "Opening stock" };

/** "Purchase bill PS/001", "Return SC/0001", "Stock count: year-end", "Customs duty 7654321". */
export function movementLabel(m: Movement): string {
  if (m.source_kind === "adjustment") return `${REASON[m.reason ?? ""] ?? "Adjustment"}${m.note ? `: ${m.note}` : ""}`;
  if (m.source_kind === "sales_document" && isPositive(m.qty_in)) return `Return ${m.reference ?? ""}`.trim();
  return `${SOURCE[m.source_kind]}${m.reference ? ` ${m.reference}` : ""}`;
}

/** "+10", "−4", or "" for a movement that only changed the value (duty on an import). */
export function movementQty(m: Movement): string {
  if (isPositive(m.qty_in)) return `+${qtyText(m.qty_in)}`;
  if (isPositive(m.qty_out)) return `−${qtyText(m.qty_out)}`;
  return "";
}

export const hasDifference = (v: Valuation) => toPaise(v.difference) !== 0n;
