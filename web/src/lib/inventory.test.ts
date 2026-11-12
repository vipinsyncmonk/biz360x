import { describe, expect, it } from "vitest";
import { adjustBody, adjustProblem, blankAdjust, blankItem, costText, countNote, hasDifference, itemBody, itemProblem, movementLabel, movementQty, outOfStock, qtyText,
  type AdjustForm, type ItemForm, type Movement, type StockItem, type Valuation } from "./inventory";

const TODAY = "2026-11-12";
const item = (p: Partial<ItemForm> = {}): ItemForm => ({ ...blankItem(), sku: "WID-1", name: "Widget", hsn: "8481", ...p });
const adj = (p: Partial<AdjustForm> = {}): AdjustForm => ({ ...blankAdjust("item-1", "count"), date: "2026-11-10", counted: "20", reason: "Year-end count", ...p });
const move = (p: Partial<Movement>): Movement => ({ id: 1, item_id: "i", sku: "WID-1", item_name: "Widget", uqc: "NOS", warehouse_code: "MAIN", date: "2026-11-10", qty_in: "0.000", qty_out: "0.000",
  unit_cost: "115.0000", value: "0.00", source_kind: "adjustment", reference: null, reason: null, note: null, entry_no: null, ...p });

describe("quantities and costs as text", () => {
  it("drops the padding a person would not write, without going through a float", () => {
    expect(qtyText("12.000")).toBe("12");
    expect(qtyText("2.500")).toBe("2.5");
    expect(qtyText("-3.000")).toBe("-3");
    expect(qtyText("0.125")).toBe("0.125");
    expect(costText("115.0000")).toBe("115");
    expect(costText("98.5714")).toBe("98.5714");
    expect(costText("131.5000")).toBe("131.50");
  });
  it("knows when nothing is on hand", () => {
    const s = (qty: string) => ({ qty } as StockItem);
    expect(outOfStock(s("0.000"))).toBe(true);
    expect(outOfStock(s("-2.000"))).toBe(true);
    expect(outOfStock(s("0.500"))).toBe(false);
  });
});

describe("adding an item", () => {
  it("accepts goods and sends them as tracked, with money in two decimals", () => {
    expect(itemProblem(item())).toBeNull();
    expect(itemBody(item({ uqc: "kgs", purchaseRate: "100", saleRate: "" }))).toEqual({ sku: "WID-1", name: "Widget", kind: "goods", hsn_sac: "8481", uqc: "KGS", gst_rate: 18,
      purchase_rate: "100.00", sale_rate: null, track_inventory: true });
  });
  it("explains what is wrong, one thing at a time", () => {
    expect(itemProblem(item({ sku: " " }))).toMatch(/code/);
    expect(itemProblem(item({ name: "" }))).toMatch(/name/);
    expect(itemProblem(item({ hsn: "84" }))).toMatch(/HSN/);
    expect(itemProblem(item({ hsn: "998314" }))).toMatch(/service/);
    expect(itemProblem(item({ uqc: "1" }))).toMatch(/unit/);
    expect(itemProblem(item({ gstRate: "17" }))).toMatch(/GST/);
    expect(itemProblem(item({ saleRate: "1,000" }))).toMatch(/sale rate/);
  });
});

describe("adjusting stock", () => {
  it("a count sends what was counted and the reason, nothing else", () => {
    expect(adjustProblem(adj(), TODAY)).toBeNull();
    expect(adjustBody(adj({ qty: "5" }))).toEqual({ item_id: "item-1", kind: "count", date: "2026-11-10", qty: null, counted_qty: "20", unit_cost: null, booking: null, reason: "Year-end count" });
    expect(adjustProblem(adj({ counted: "0" }), TODAY)).toBeNull();                         // counting none is a count
  });
  it("opening stock needs a quantity and a cost, and sends the booking only when one was chosen", () => {
    const o = adj({ kind: "opening", qty: "10", unitCost: "100", counted: "", reason: "" });
    expect(adjustProblem(o, TODAY)).toBeNull();
    expect(adjustBody(o)).toMatchObject({ kind: "opening", qty: "10", counted_qty: null, unit_cost: "100", booking: null, reason: null });
    expect(adjustBody({ ...o, booking: "in_opening_balances" }).booking).toBe("in_opening_balances");
    expect(adjustProblem({ ...o, unitCost: "" }, TODAY)).toMatch(/cost/);
    expect(adjustProblem({ ...o, qty: "0" }, TODAY)).toMatch(/quantity/);
  });
  it("a write-off needs a quantity and a reason and never a cost", () => {
    const w = adj({ kind: "write_off", qty: "2", counted: "", reason: "Damaged" });
    expect(adjustProblem(w, TODAY)).toBeNull();
    expect(adjustBody({ ...w, unitCost: "5" }).unit_cost).toBeNull();
    expect(adjustProblem({ ...w, unitCost: "5" }, TODAY)).toMatch(/average cost/);
    expect(adjustProblem({ ...w, reason: " " }, TODAY)).toMatch(/why/);
    expect(adjustProblem({ ...w, qty: "2.5555" }, TODAY)).toMatch(/quantity/);
  });
  it("refuses a missing item, a missing date and a date that has not come", () => {
    expect(adjustProblem(adj({ itemId: "" }), TODAY)).toMatch(/item/);
    expect(adjustProblem(adj({ date: "" }), TODAY)).toMatch(/date/);
    expect(adjustProblem(adj({ date: "2026-11-13" }), TODAY)).toMatch(/future/);
    expect(adjustProblem(adj({ counted: "many" }), TODAY)).toMatch(/counted/);
  });
  it("says what a count will do before it is saved", () => {
    expect(countNote("21.000", "20", "NOS")).toBe("1 NOS fewer than the books: they will be written off");
    expect(countNote("18.000", "20.5", "KGS")).toBe("2.5 KGS more than the books: they will be added");
    expect(countNote("-2.000", "0", "NOS")).toBe("2 NOS more than the books: they will be added");
    expect(countNote("20.000", "20", "NOS")).toMatch(/matches/);
    expect(countNote("20.000", "", "NOS")).toBeNull();
  });
});

describe("movements", () => {
  it("names what moved the stock", () => {
    expect(movementLabel(move({ source_kind: "purchase_bill", reference: "PS/001", qty_in: "10.000" }))).toBe("Purchase bill PS/001");
    expect(movementLabel(move({ source_kind: "sales_document", reference: "ST/0001", qty_out: "4.000" }))).toBe("Sale ST/0001");
    expect(movementLabel(move({ source_kind: "sales_document", reference: "SC/0001", qty_in: "1.000" }))).toBe("Return SC/0001");
    expect(movementLabel(move({ reason: "count", note: "Year-end count", qty_out: "1.000" }))).toBe("Stock count: Year-end count");
    expect(movementLabel(move({ source_kind: "opening", qty_in: "10.000" }))).toBe("Opening stock");
    expect(movementLabel(move({ source_kind: "bill_of_entry", reference: "7654321" }))).toBe("Customs duty 7654321");
  });
  it("signs the quantity and leaves it out when only the value changed", () => {
    expect(movementQty(move({ qty_in: "10.000" }))).toBe("+10");
    expect(movementQty(move({ qty_out: "2.500" }))).toBe("−2.5");
    expect(movementQty(move({}))).toBe("");
  });
  it("sees a difference between the stock and the ledger to the paisa", () => {
    expect(hasDifference({ difference: "0.00" } as Valuation)).toBe(false);
    expect(hasDifference({ difference: "-0.01" } as Valuation)).toBe(true);
  });
});
