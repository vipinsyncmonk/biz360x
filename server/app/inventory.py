"""Inventory: the costing maths (pure) and the helpers that move stock inside the caller's transaction.

Valuation is **weighted average cost per item**, across all warehouses. An item's position is a quantity and a value; a receipt adds
both, and an issue leaves at value / quantity as it stands at that moment. The average is worked out in the order movements are
recorded, not re-run by date, so a back-dated bill changes the cost of later sales only. That is enough for a trading company with
one cost per item: it needs no layers, survives returns and write-offs, and is the method most small books already use. FIFO needs
cost layers per receipt and can follow without changing the movements table.

The value of an item's stock is always the sum of its movements' `value`, and every movement with a value is posted to the Inventory
account in the same transaction, so the stock report and the ledger cannot drift apart."""
from __future__ import annotations

import datetime as dt
import uuid
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

from . import ledger
from .core import ApiError

ZERO = Decimal("0")
C2, C4 = Decimal("0.01"), Decimal("0.0001")
OUT_DOCS = ("tax_invoice", "export_invoice", "bill_of_supply")


def r2(x: Decimal) -> Decimal:
    return x.quantize(C2, rounding=ROUND_HALF_UP)


def r4(x: Decimal) -> Decimal:
    return x.quantize(C4, rounding=ROUND_HALF_UP)


# ------------------------------------------------------------------ pure costing
@dataclass(frozen=True)
class Position:
    """What is held of one item: quantity (all warehouses) and what it cost."""
    qty: Decimal = ZERO
    value: Decimal = ZERO

    @property
    def average(self) -> Decimal:
        """The cost one unit will leave at, to four decimals. Nothing on hand has no average."""
        return r4(self.value / self.qty) if self.qty > 0 and self.value > 0 else ZERO


def receive(pos: Position, qty: Decimal, value: Decimal) -> Position:
    """Stock in. `value` is what it cost in all; with qty 0 it is cost added to what is already held (duty on an import)."""
    if qty < 0 or value < 0:
        raise ValueError("a receipt cannot be negative")
    return Position(pos.qty + qty, pos.value + value)


def issue(pos: Position, qty: Decimal, fallback_cost: Decimal = ZERO) -> tuple[Decimal, Decimal, Position]:
    """Stock out at the average. Returns (value taken out, unit cost, the position after).

    Taking everything takes the whole value, so rounding never leaves a few paise behind on an empty shelf. With nothing on hand
    (a company that allows negative stock) the unit cost is `fallback_cost`: the last cost known for the item."""
    if qty <= 0:
        raise ValueError("an issue needs a quantity")
    if pos.qty > 0 and pos.value >= 0:
        value = pos.value if qty == pos.qty else r2(pos.value * qty / pos.qty)
    elif pos.qty < 0 and pos.value < 0:                      # already below zero: carry on at the cost it went negative at
        value = r2(pos.value * qty / pos.qty)
    else:
        value = r2(fallback_cost * qty)
    return value, r4(value / qty), Position(pos.qty - qty, pos.value - value)


def return_value(qty: Decimal, unit_cost: Decimal, original_qty: Decimal | None = None, original_value: Decimal | None = None) -> Decimal:
    """What returned goods come back at: the cost they left at. A full return of a line comes back at exactly what left."""
    if original_qty is not None and original_value is not None and qty == original_qty:
        return original_value
    return r2(qty * unit_cost)


def allocate(total: Decimal, weights: list[Decimal]) -> list[Decimal]:
    """Split `total` over `weights` to the paisa; the last share with a weight takes the rounding."""
    base = sum(weights, ZERO)
    if base <= 0 or not weights:
        return [ZERO for _ in weights]
    out, left = [], total
    last = max(i for i, w in enumerate(weights) if w > 0)
    for i, w in enumerate(weights):
        share = left if i == last else (r2(total * w / base) if w > 0 else ZERO)
        out.append(share)
        left -= share
    return out


def count_difference(on_hand: Decimal, counted: Decimal) -> Decimal:
    """Counted less what the books say: positive is stock found, negative is stock missing."""
    return counted - on_hand


def cogs_lines(value: Decimal) -> list[ledger.Line]:
    return [ledger.Line("cost_of_goods_sold", debit=value), ledger.Line("inventory", credit=value)]


def return_lines(value: Decimal) -> list[ledger.Line]:
    return [ledger.Line("inventory", debit=value), ledger.Line("cost_of_goods_sold", credit=value)]


def adjustment_lines(value: Decimal, *, gain: bool) -> list[ledger.Line]:
    """Stock found: Dr Inventory, Cr Stock adjustments. Stock lost or written off: the reverse."""
    if gain:
        return [ledger.Line("inventory", debit=value), ledger.Line("stock_adjustments", credit=value)]
    return [ledger.Line("stock_adjustments", debit=value), ledger.Line("inventory", credit=value)]


def opening_lines(value: Decimal) -> list[ledger.Line]:
    return [ledger.Line("inventory", debit=value), ledger.Line("opening_balance_equity", credit=value)]


# ------------------------------------------------------------------ database helpers
def default_warehouse(cur, entity_id) -> uuid.UUID:
    """The warehouse bills receive into and invoices ship from; created the first time stock moves."""
    cur.execute("SELECT id FROM warehouses WHERE entity_id = %s AND is_default", (entity_id,))
    r = cur.fetchone()
    if r:
        return r["id"]
    cur.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (f"warehouses:{entity_id}",))
    cur.execute("SELECT id FROM warehouses WHERE entity_id = %s AND is_default", (entity_id,))
    r = cur.fetchone()
    if r:
        return r["id"]
    cur.execute("INSERT INTO warehouses (entity_id, code, name, is_default) VALUES (%s, 'MAIN', 'Main store', true) RETURNING id", (entity_id,))
    return cur.fetchone()["id"]


def tracked(cur, entity_id, item_ids) -> dict:
    """The items among `item_ids` that are tracked goods, by id."""
    ids = [i for i in set(item_ids) if i]
    if not ids:
        return {}
    cur.execute("SELECT id, sku, name, uqc, purchase_rate FROM items WHERE entity_id = %s AND id = ANY(%s) AND track_inventory AND kind = 'goods'", (entity_id, ids))
    return {r["id"]: r for r in cur.fetchall()}


def position(cur, entity_id, item_id, *, lock: bool = True) -> Position:
    """An item's quantity and value now. Locks the item so two writers work out the average one after the other."""
    if lock:
        cur.execute("SELECT 1 FROM items WHERE id = %s AND entity_id = %s FOR UPDATE", (item_id, entity_id))
    cur.execute("""SELECT coalesce((SELECT sum(qty) FROM stock_levels WHERE item_id = %s AND entity_id = %s), 0) AS qty,
                          coalesce((SELECT sum(value) FROM stock_movements WHERE item_id = %s AND entity_id = %s), 0) AS value""",
                (item_id, entity_id, item_id, entity_id))
    r = cur.fetchone()
    return Position(r["qty"], r["value"])


def level(cur, entity_id, item_id, warehouse_id) -> Decimal:
    cur.execute("SELECT qty FROM stock_levels WHERE item_id = %s AND warehouse_id = %s AND entity_id = %s", (item_id, warehouse_id, entity_id))
    r = cur.fetchone()
    return r["qty"] if r else ZERO


def last_cost(cur, entity_id, item: dict) -> Decimal:
    """The cost to use when nothing is on hand: the last receipt's, else the item's purchase rate."""
    cur.execute("SELECT unit_cost FROM stock_movements WHERE item_id = %s AND entity_id = %s AND qty_in > 0 ORDER BY id DESC LIMIT 1", (item["id"], entity_id))
    r = cur.fetchone()
    return r["unit_cost"] if r else (item.get("purchase_rate") or ZERO)


def negative_allowed(cur, entity_id) -> bool:
    cur.execute("SELECT allow_negative_stock FROM entities WHERE id = %s", (entity_id,))
    r = cur.fetchone()
    return bool(r and r["allow_negative_stock"])


def insufficient(item: dict, on_hand: Decimal, needed: Decimal) -> ApiError:
    return ApiError(409, "insufficient_stock", "Not enough stock",
                    f"{item['name']} ({item['sku']}): {on_hand.normalize():f} {item['uqc']} on hand, {needed.normalize():f} needed. "
                    "Book the purchase bill or record the stock first, or allow negative stock in the inventory settings.",
                    errors=[{"field": "item_id", "message": str(item["id"])}])


def record(cur, entity_id, *, item_id, warehouse_id, date: dt.date, qty_in: Decimal = ZERO, qty_out: Decimal = ZERO, unit_cost: Decimal, value: Decimal,
           source_kind: str, source_id=None, source_line_id=None, reason: str | None = None, note: str | None = None, journal_entry_id=None) -> int:
    """Write one movement. The database trigger updates the quantity on hand, refuses a locked period and (unless the company allows
    it) a quantity below zero."""
    cur.execute("""INSERT INTO stock_movements (entity_id, item_id, warehouse_id, movement_date, qty_in, qty_out, unit_cost, value, source_kind, source_id,
                                                source_line_id, reason, note, journal_entry_id, created_by)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s, app.current_user_id()) RETURNING id""",
                (entity_id, item_id, warehouse_id, date, qty_in, qty_out, unit_cost, value, source_kind, source_id, source_line_id, reason, note, journal_entry_id))
    return cur.fetchone()["id"]


# ------------------------------------------------------------------ purchase bills
def bill_stock_lines(cur, entity_id, bill: dict) -> list[dict]:
    """The lines of a bill that bring stock in, each with the cost it comes in at: the taxable value, plus the line's GST when the
    bill's credit cannot be claimed."""
    cur.execute("""SELECT id, item_id, qty, taxable_value, cgst_amount + sgst_amount + igst_amount AS tax FROM purchase_bill_lines
                    WHERE bill_id = %s AND item_id IS NOT NULL ORDER BY line_no""", (bill["id"],))
    rows = cur.fetchall()
    items = tracked(cur, entity_id, [r["item_id"] for r in rows])
    eligible = bill["itc_eligibility"] == "eligible"
    return [{**r, "cost": r["taxable_value"] + (ZERO if eligible else r["tax"])} for r in rows if r["item_id"] in items]


def receive_bill(cur, entity_id, bill: dict, stock_lines: list[dict], journal_id) -> None:
    """Movements for a booked bill; its journal has already debited Inventory for these lines."""
    if not stock_lines:
        return
    wh = default_warehouse(cur, entity_id)
    for l in stock_lines:
        position(cur, entity_id, l["item_id"])                                     # serialise with other writers of the item
        record(cur, entity_id, item_id=l["item_id"], warehouse_id=wh, date=bill["bill_date"], qty_in=l["qty"], unit_cost=r4(l["cost"] / l["qty"]),
               value=l["cost"], source_kind="purchase_bill", source_id=bill["id"], source_line_id=l["id"], journal_entry_id=journal_id)
    cur.execute("SELECT id FROM bills_of_entry WHERE purchase_bill_id = %s AND entity_id = %s ORDER BY boe_date", (bill["id"], entity_id))
    for b in cur.fetchall():
        apply_boe_duty(cur, entity_id, b["id"])


def apply_boe_duty(cur, entity_id, boe_id):
    """Add the customs duty of a bill of entry to the stock its purchase bill brought in. Basic duty, surcharge and other duty are a cost
    of the goods (IGST is a credit, not a cost). The share for the bill's stock lines, by taxable value, raises the value of what is
    still on hand; the share of an item that is already sold out goes to cost of goods sold. Posted once per bill of entry:
    Dr Inventory (and Cost of goods sold), Cr Customs duty payable. Returns the journal id, or None when there is nothing to do."""
    cur.execute("""SELECT b.id, b.boe_number, b.boe_date, b.bcd + b.sws + b.other_duty AS duty, b.purchase_bill_id, p.bill_date, p.taxable_total, p.journal_entry_id
                     FROM bills_of_entry b JOIN purchase_bills p ON p.id = b.purchase_bill_id WHERE b.id = %s AND b.entity_id = %s""", (boe_id, entity_id))
    b = cur.fetchone()
    if not b or b["duty"] <= 0 or not b["journal_entry_id"] or b["taxable_total"] <= 0:
        return None
    cur.execute("SELECT 1 FROM journal_entries WHERE entity_id = %s AND source = 'inventory' AND source_id = %s", (entity_id, boe_id))
    if cur.fetchone():
        return None
    cur.execute("""SELECT m.item_id, m.warehouse_id, m.source_line_id, m.qty_in, l.taxable_value FROM stock_movements m JOIN purchase_bill_lines l ON l.id = m.source_line_id
                    WHERE m.entity_id = %s AND m.source_kind = 'purchase_bill' AND m.source_id = %s ORDER BY m.id""", (entity_id, b["purchase_bill_id"]))
    lines = cur.fetchall()
    if not lines:
        return None
    stock_taxable = sum((l["taxable_value"] for l in lines), ZERO)
    shares = allocate(r2(b["duty"] * stock_taxable / b["taxable_total"]), [l["taxable_value"] for l in lines])
    date = max(b["boe_date"], b["bill_date"])
    capitalised, expensed, moves = ZERO, ZERO, []
    for l, share in zip(lines, shares):
        if share <= 0:
            continue
        if position(cur, entity_id, l["item_id"]).qty > 0:
            capitalised += share
            moves.append((l, share))
        else:
            expensed += share
    if capitalised + expensed <= 0:
        return None
    out = []
    if capitalised:
        out.append(ledger.Line("inventory", debit=capitalised, memo=f"Duty on bill of entry {b['boe_number']}"))
    if expensed:
        out.append(ledger.Line("cost_of_goods_sold", debit=expensed, memo=f"Duty on goods already sold, bill of entry {b['boe_number']}"))
    out.append(ledger.Line("customs_duty_payable", credit=capitalised + expensed))
    jid = ledger.post_journal(cur, entity_id, date, "inventory", boe_id, f"Customs duty on bill of entry {b['boe_number']}", out)
    for l, share in moves:
        record(cur, entity_id, item_id=l["item_id"], warehouse_id=l["warehouse_id"], date=date, unit_cost=r4(share / l["qty_in"]), value=share,
               source_kind="bill_of_entry", source_id=boe_id, source_line_id=l["source_line_id"], journal_entry_id=jid)
    return jid


# ------------------------------------------------------------------ sales documents
def on_document_issued(cur, entity_id, doc_id):
    """An issued invoice takes its tracked goods out of stock at the average cost and posts Dr Cost of goods sold, Cr Inventory. A credit
    note for returned goods (`cn_reason` sales_return) puts them back at the cost they left at. Runs in the transaction that issues the
    document. Returns the journal id, or None when the document moves no stock."""
    cur.execute("SELECT id, doc_type::text AS doc_type, number, doc_date, original_document_id, cn_reason::text AS cn_reason FROM sales_documents WHERE id = %s AND entity_id = %s",
                (doc_id, entity_id))
    d = cur.fetchone()
    returning = d["doc_type"] == "credit_note" and d["cn_reason"] == "sales_return"
    if d["doc_type"] not in OUT_DOCS and not returning:
        return None
    cur.execute("SELECT id, item_id, original_line_id, qty FROM sales_document_lines WHERE document_id = %s AND item_id IS NOT NULL AND NOT is_service ORDER BY line_no", (doc_id,))
    lines = cur.fetchall()
    items = tracked(cur, entity_id, [l["item_id"] for l in lines])
    lines = [l for l in lines if l["item_id"] in items]
    if not lines:
        return None
    wh = default_warehouse(cur, entity_id)
    moves, total = [], ZERO
    if returning:
        for l in lines:
            position(cur, entity_id, l["item_id"])
            cur.execute("""SELECT qty_out, unit_cost, -value AS value FROM stock_movements
                            WHERE entity_id = %s AND source_kind = 'sales_document' AND source_id = %s AND item_id = %s AND qty_out > 0
                            ORDER BY (source_line_id = %s) DESC NULLS LAST, id LIMIT 1""", (entity_id, d["original_document_id"], l["item_id"], l["original_line_id"]))
            o = cur.fetchone()
            if o:
                unit, value = o["unit_cost"], return_value(l["qty"], o["unit_cost"], o["qty_out"], o["value"])
            else:                                                   # the invoice moved no stock (it was issued before the item was tracked): today's cost
                pos = position(cur, entity_id, l["item_id"], lock=False)
                unit = pos.average or last_cost(cur, entity_id, items[l["item_id"]])
                value = r2(l["qty"] * unit)
            moves.append({"item_id": l["item_id"], "line": l["id"], "qty_in": l["qty"], "qty_out": ZERO, "unit": unit, "value": value})
            total += value
    else:
        allowed = negative_allowed(cur, entity_id)
        held: dict = {}                                              # an item on two lines: the second sees what the first left
        for l in lines:
            item = items[l["item_id"]]
            if l["item_id"] not in held:
                held[l["item_id"]] = (position(cur, entity_id, l["item_id"]), level(cur, entity_id, l["item_id"], wh))
            pos, here = held[l["item_id"]]
            if not allowed and here < l["qty"]:
                raise insufficient(item, here, l["qty"])
            value, unit, after = issue(pos, l["qty"], last_cost(cur, entity_id, item) if pos.qty <= 0 else ZERO)
            held[l["item_id"]] = (after, here - l["qty"])
            moves.append({"item_id": l["item_id"], "line": l["id"], "qty_in": ZERO, "qty_out": l["qty"], "unit": unit, "value": -value})
            total += value
    jid = None
    if total > 0:
        label = f"{'Goods returned on' if returning else 'Cost of goods sold,'} {d['doc_type'].replace('_', ' ')} {d['number']}"
        jid = ledger.post_journal(cur, entity_id, d["doc_date"], "inventory", doc_id, label, return_lines(total) if returning else cogs_lines(total))
    for m in moves:
        record(cur, entity_id, item_id=m["item_id"], warehouse_id=wh, date=d["doc_date"], qty_in=m["qty_in"], qty_out=m["qty_out"], unit_cost=m["unit"], value=m["value"],
               source_kind="sales_document", source_id=doc_id, source_line_id=m["line"], journal_entry_id=jid)
    return jid
