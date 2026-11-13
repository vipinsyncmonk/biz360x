"""Inventory: the item catalogue, warehouses, stock on hand and its value, movements, adjustments (count, write-off, opening stock),
a move between warehouses and the valuation report.

Stock moves by itself when a purchase bill is booked and when an invoice or a credit note for returned goods is issued
(inventory.py, called from routes_purchases.py and routes_sales.py). What a person records here is everything else. The catalogue
runs under the parties permission, the rest under inventory. Every adjustment posts a journal."""
from __future__ import annotations

import datetime as dt
import uuid
from decimal import Decimal
from typing import Literal

from fastapi import APIRouter, Header, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from . import inventory, ledger
from .core import ApiError, Auth, Principal, audit, emit, idempotency_begin, idempotency_finish, money, require, tx
from .routes_orders import js, offset_of, page

router = APIRouter()
E = "/entities/{entity_id}"
ZERO = Decimal(0)
GST_RATES = {Decimal(x) for x in ("0", "0.1", "0.25", "1.5", "3", "5", "12", "18", "28", "40")}


def _bad(field: str, message: str):
    return ApiError(400, "validation_failed", message, errors=[{"field": field, "message": message}])


def q3(v) -> str:
    return f"{Decimal(v):.3f}"


def c4(v) -> str:
    return f"{Decimal(v):.4f}"


# ================================================================== items
ITEM_SQL = "SELECT id, sku, name, kind, hsn_sac, uqc, gst_rate, cess_rate, sale_rate, purchase_rate, track_inventory, is_active FROM items"


class ItemIn(BaseModel):
    sku: str = Field(min_length=1, max_length=40)
    name: str = Field(min_length=1, max_length=160)
    kind: Literal["goods", "service"] = "goods"
    hsn_sac: str = Field(pattern=r"^[0-9]{4,8}$")
    uqc: str = Field(default="NOS", min_length=2, max_length=8)
    gst_rate: Decimal
    cess_rate: Decimal = Field(default=ZERO, ge=0, le=100)
    sale_rate: Decimal | None = Field(default=None, ge=0, max_digits=18, decimal_places=2)
    purchase_rate: Decimal | None = Field(default=None, ge=0, max_digits=18, decimal_places=2)
    track_inventory: bool = False
    is_active: bool = True


class ItemPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=160)
    hsn_sac: str | None = Field(default=None, pattern=r"^[0-9]{4,8}$")
    uqc: str | None = Field(default=None, min_length=2, max_length=8)
    gst_rate: Decimal | None = None
    sale_rate: Decimal | None = Field(default=None, ge=0, max_digits=18, decimal_places=2)
    purchase_rate: Decimal | None = Field(default=None, ge=0, max_digits=18, decimal_places=2)
    track_inventory: bool | None = None
    is_active: bool | None = None


def _item_out(r: dict) -> dict:
    return {"id": str(r["id"]), "sku": r["sku"], "name": r["name"], "kind": r["kind"], "hsn_sac": r["hsn_sac"], "uqc": r["uqc"], "gst_rate": float(r["gst_rate"]),
            "cess_rate": float(r["cess_rate"]), "sale_rate": money(r["sale_rate"]) if r["sale_rate"] is not None else None,
            "purchase_rate": money(r["purchase_rate"]) if r["purchase_rate"] is not None else None, "track_inventory": r["track_inventory"], "is_active": r["is_active"]}


def _load_item(cur, entity_id, item_id, *, lock=False) -> dict:
    cur.execute(ITEM_SQL + " WHERE id = %s AND entity_id = %s" + (" FOR UPDATE" if lock else ""), (item_id, entity_id))
    r = cur.fetchone()
    if not r:
        raise ApiError(404, "not_found", "Not found")
    return r


def _check_rate(rate: Decimal | None) -> None:
    if rate is not None and rate not in GST_RATES:
        raise _bad("gst_rate", "Choose one of the GST rates: 0, 0.1, 0.25, 1.5, 3, 5, 12, 18, 28 or 40")


@router.get(E + "/items", tags=["Parties & items"])
def list_items(entity_id: uuid.UUID, p: Principal = Auth, q: str | None = None, limit: int = Query(default=50, ge=1, le=200), cursor: str | None = None):
    off = offset_of(cursor)
    like = "%" + (q or "").replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
    with tx(p.user_id) as cur:
        require(cur, entity_id, "parties:read")
        cur.execute(ITEM_SQL + " WHERE entity_id = %s AND (sku ILIKE %s OR name ILIKE %s) ORDER BY sku LIMIT %s OFFSET %s", (entity_id, like, like, limit + 1, off))
        return page([_item_out(r) for r in cur.fetchall()], limit, off)


@router.post(E + "/items", status_code=201, tags=["Parties & items"])
def create_item(entity_id: uuid.UUID, body: ItemIn, p: Principal = Auth):
    _check_rate(body.gst_rate)
    if body.track_inventory and body.kind != "goods":
        raise _bad("track_inventory", "Only goods are kept in stock")
    with tx(p.user_id) as cur:
        require(cur, entity_id, "parties:write")
        cur.execute("""INSERT INTO items (entity_id, sku, name, kind, hsn_sac, uqc, gst_rate, cess_rate, sale_rate, purchase_rate, track_inventory, is_active)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id""",
                    (entity_id, body.sku.strip(), body.name.strip(), body.kind, body.hsn_sac, body.uqc.upper(), body.gst_rate, body.cess_rate, body.sale_rate,
                     body.purchase_rate, body.track_inventory, body.is_active))
        iid = cur.fetchone()["id"]
        out = _item_out(_load_item(cur, entity_id, iid))
        audit(cur, entity_id, "item.created", "items", iid, after=out)
        return out


@router.get(E + "/items/{item_id}", tags=["Parties & items"])
def get_item(entity_id: uuid.UUID, item_id: uuid.UUID, p: Principal = Auth):
    with tx(p.user_id) as cur:
        require(cur, entity_id, "parties:read")
        return _item_out(_load_item(cur, entity_id, item_id))


@router.patch(E + "/items/{item_id}", tags=["Parties & items"])
def update_item(entity_id: uuid.UUID, item_id: uuid.UUID, body: ItemPatch, p: Principal = Auth):
    changes = body.model_dump(exclude_unset=True)
    _check_rate(changes.get("gst_rate"))
    for k in ("name", "hsn_sac", "uqc", "gst_rate", "track_inventory", "is_active"):
        if k in changes and changes[k] is None:
            raise _bad(k, f"{k} cannot be empty")
    with tx(p.user_id) as cur:
        require(cur, entity_id, "parties:write")
        it = _load_item(cur, entity_id, item_id, lock=True)
        if not changes:
            raise ApiError(400, "validation_failed", "Nothing to change")
        if changes.get("track_inventory") and it["kind"] != "goods":
            raise _bad("track_inventory", "Only goods are kept in stock")
        if changes.get("track_inventory") is False and it["track_inventory"]:
            pos = inventory.position(cur, entity_id, item_id, lock=False)
            if pos.qty != 0 or pos.value != 0:
                raise ApiError(409, "conflict", "This item still has stock", "Write the stock off or sell it before you stop tracking the item.")
        if "uqc" in changes:
            changes["uqc"] = changes["uqc"].upper()
        sets = ", ".join(f"{k} = %s" for k in changes)
        cur.execute(f"UPDATE items SET {sets} WHERE id = %s", (*changes.values(), item_id))
        out = _item_out(_load_item(cur, entity_id, item_id))
        audit(cur, entity_id, "item.updated", "items", item_id, before=_item_out(it), after=out)
        return out


# ================================================================== warehouses and settings
class WarehouseIn(BaseModel):
    code: str = Field(pattern=r"^[A-Za-z0-9-]{1,12}$")
    name: str = Field(min_length=1, max_length=80)
    is_default: bool = False


class InventorySettingsIn(BaseModel):
    allow_negative_stock: bool


@router.get(E + "/warehouses", tags=["Inventory"])
def list_warehouses(entity_id: uuid.UUID, p: Principal = Auth):
    with tx(p.user_id) as cur:
        require(cur, entity_id, "inventory:read")
        cur.execute("SELECT id, code, name, is_default, is_active FROM warehouses WHERE entity_id = %s ORDER BY is_default DESC, code", (entity_id,))
        return {"data": js(cur.fetchall())}


@router.post(E + "/warehouses", status_code=201, tags=["Inventory"])
def create_warehouse(entity_id: uuid.UUID, body: WarehouseIn, p: Principal = Auth):
    with tx(p.user_id) as cur:
        require(cur, entity_id, "inventory:write")
        cur.execute("SELECT count(*) AS n FROM warehouses WHERE entity_id = %s", (entity_id,))
        first = cur.fetchone()["n"] == 0
        if body.is_default:
            cur.execute("UPDATE warehouses SET is_default = false WHERE entity_id = %s AND is_default", (entity_id,))
        cur.execute("INSERT INTO warehouses (entity_id, code, name, is_default) VALUES (%s, %s, %s, %s) RETURNING id, code, name, is_default, is_active",
                    (entity_id, body.code.upper(), body.name.strip(), body.is_default or first))
        out = js(cur.fetchone())
        audit(cur, entity_id, "warehouse.created", "warehouses", out["id"], after=out)
        return out


def _settings(cur, entity_id) -> dict:
    return {"allow_negative_stock": inventory.negative_allowed(cur, entity_id), "valuation_method": "weighted_average"}


@router.get(E + "/inventory/settings", tags=["Inventory"])
def get_settings(entity_id: uuid.UUID, p: Principal = Auth):
    with tx(p.user_id) as cur:
        require(cur, entity_id, "inventory:read")
        return _settings(cur, entity_id)


@router.put(E + "/inventory/settings", tags=["Inventory"])
def put_settings(entity_id: uuid.UUID, body: InventorySettingsIn, p: Principal = Auth):
    """Whether a sale may take stock below zero. Off by default; turning it on makes the cost of those sales an estimate until the
    purchase is booked."""
    with tx(p.user_id) as cur:
        require(cur, entity_id, "inventory:approve")
        before = _settings(cur, entity_id)
        cur.execute("UPDATE entities SET allow_negative_stock = %s WHERE id = %s", (body.allow_negative_stock, entity_id))
        after = _settings(cur, entity_id)
        if before != after:
            audit(cur, entity_id, "inventory.settings_changed", "entities", entity_id, before=before, after=after)
        return after


# ================================================================== stock on hand
def _stock_rows(cur, entity_id, item_id=None, q: str | None = None) -> list[dict]:
    like = "%" + (q or "").replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
    cur.execute("""SELECT i.id AS item_id, i.sku, i.name, i.uqc, i.hsn_sac, i.sale_rate, i.is_active,
                          coalesce((SELECT sum(s.qty) FROM stock_levels s WHERE s.item_id = i.id), 0) AS qty,
                          coalesce((SELECT sum(m.value) FROM stock_movements m WHERE m.item_id = i.id), 0) AS value,
                          (SELECT max(m.movement_date) FROM stock_movements m WHERE m.item_id = i.id) AS last_moved_on
                     FROM items i WHERE i.entity_id = %s AND i.track_inventory AND i.kind = 'goods' AND (%s::uuid IS NULL OR i.id = %s)
                      AND (i.sku ILIKE %s OR i.name ILIKE %s) ORDER BY i.sku""", (entity_id, item_id, item_id, like, like))
    rows = cur.fetchall()
    cur.execute("""SELECT s.item_id, s.warehouse_id, w.code, w.name, s.qty FROM stock_levels s JOIN warehouses w ON w.id = s.warehouse_id
                    WHERE s.entity_id = %s AND s.qty <> 0 ORDER BY w.is_default DESC, w.code""", (entity_id,))
    by_item: dict = {}
    for l in cur.fetchall():
        by_item.setdefault(l["item_id"], []).append({"warehouse_id": str(l["warehouse_id"]), "code": l["code"], "name": l["name"], "qty": q3(l["qty"])})
    out = []
    for r in rows:
        pos = inventory.Position(r["qty"], r["value"])
        out.append({"item_id": str(r["item_id"]), "sku": r["sku"], "name": r["name"], "uqc": r["uqc"], "hsn_sac": r["hsn_sac"], "is_active": r["is_active"],
                    "sale_rate": money(r["sale_rate"]) if r["sale_rate"] is not None else None, "qty": q3(r["qty"]), "average_cost": c4(pos.average),
                    "value": money(r["value"]), "last_moved_on": r["last_moved_on"].isoformat() if r["last_moved_on"] else None,
                    "levels": by_item.get(r["item_id"], [])})
    return out


@router.get(E + "/stock-items", tags=["Inventory"])
def list_stock(entity_id: uuid.UUID, p: Principal = Auth, q: str | None = None):
    """Every tracked item with what is on hand (all warehouses), its average cost and its value, and the total value of stock."""
    with tx(p.user_id) as cur:
        require(cur, entity_id, "inventory:read")
        rows = _stock_rows(cur, entity_id, q=q)
        settings = _settings(cur, entity_id)
    return {"data": rows, "totals": {"value": money(sum((Decimal(r["value"]) for r in rows), ZERO)), "items": len(rows),
                                     "out_of_stock": sum(1 for r in rows if Decimal(r["qty"]) <= 0)}, **settings}


MOVE_SQL = """SELECT m.id, m.item_id, i.sku, i.name AS item_name, i.uqc, m.warehouse_id, w.code AS warehouse_code, m.movement_date, m.qty_in, m.qty_out, m.unit_cost, m.value,
                     m.source_kind, m.source_id, m.reason, m.note, m.journal_entry_id, j.entry_no, m.created_at,
                     coalesce(pb.bill_no, sd.number, be.boe_number) AS reference
                FROM stock_movements m JOIN items i ON i.id = m.item_id JOIN warehouses w ON w.id = m.warehouse_id
                LEFT JOIN journal_entries j ON j.id = m.journal_entry_id
                LEFT JOIN purchase_bills pb ON m.source_kind = 'purchase_bill' AND pb.id = m.source_id
                LEFT JOIN sales_documents sd ON m.source_kind = 'sales_document' AND sd.id = m.source_id
                LEFT JOIN bills_of_entry be ON m.source_kind = 'bill_of_entry' AND be.id = m.source_id"""


def _move_out(r: dict) -> dict:
    return {"id": r["id"], "item_id": str(r["item_id"]), "sku": r["sku"], "item_name": r["item_name"], "uqc": r["uqc"], "warehouse_id": str(r["warehouse_id"]),
            "warehouse_code": r["warehouse_code"], "date": r["movement_date"].isoformat(), "qty_in": q3(r["qty_in"]), "qty_out": q3(r["qty_out"]),
            "unit_cost": c4(r["unit_cost"]), "value": money(r["value"]), "source_kind": r["source_kind"], "source_id": str(r["source_id"]) if r["source_id"] else None,
            "reference": r["reference"], "reason": r["reason"], "note": r["note"], "journal_entry_id": str(r["journal_entry_id"]) if r["journal_entry_id"] else None,
            "entry_no": r["entry_no"], "created_at": r["created_at"].isoformat()}


@router.get(E + "/stock-movements", tags=["Inventory"])
def list_movements(entity_id: uuid.UUID, p: Principal = Auth, item_id: uuid.UUID | None = None, warehouse_id: uuid.UUID | None = None,
                   source_kind: Literal["purchase_bill", "sales_document", "adjustment", "opening", "transfer", "bill_of_entry"] | None = None,
                   from_: dt.date | None = Query(default=None, alias="from"), to: dt.date | None = None,
                   limit: int = Query(default=50, ge=1, le=200), cursor: str | None = None):
    """Movements, newest first: what came in or went out, at what cost, and the bill, document or adjustment behind it."""
    off = offset_of(cursor)
    with tx(p.user_id) as cur:
        require(cur, entity_id, "inventory:read")
        cur.execute(MOVE_SQL + """ WHERE m.entity_id = %s AND (%s::uuid IS NULL OR m.item_id = %s) AND (%s::uuid IS NULL OR m.warehouse_id = %s)
                                    AND (%s::text IS NULL OR m.source_kind = %s) AND (%s::date IS NULL OR m.movement_date >= %s) AND (%s::date IS NULL OR m.movement_date <= %s)
                                  ORDER BY m.id DESC LIMIT %s OFFSET %s""",
                    (entity_id, item_id, item_id, warehouse_id, warehouse_id, source_kind, source_kind, from_, from_, to, to, limit + 1, off))
        return page([_move_out(r) for r in cur.fetchall()], limit, off)


# ================================================================== adjustments
class AdjustmentIn(BaseModel):
    item_id: uuid.UUID
    warehouse_id: uuid.UUID | None = None
    kind: Literal["opening", "count", "write_off"]
    date: dt.date
    qty: Decimal | None = Field(default=None, gt=0, max_digits=18, decimal_places=3)              # opening, write_off
    counted_qty: Decimal | None = Field(default=None, ge=0, max_digits=18, decimal_places=3)      # count: what is physically there
    unit_cost: Decimal | None = Field(default=None, ge=0, max_digits=18, decimal_places=4)        # opening; a count that finds stock with no cost on record
    booking: Literal["post", "in_opening_balances"] | None = None                                # opening only
    reason: str | None = Field(default=None, max_length=200)
    note: str | None = Field(default=None, max_length=500)


def _warehouse(cur, entity_id, wid) -> uuid.UUID:
    if wid is None:
        return inventory.default_warehouse(cur, entity_id)
    cur.execute("SELECT 1 FROM warehouses WHERE id = %s AND entity_id = %s AND is_active", (wid, entity_id))
    if not cur.fetchone():
        raise _bad("warehouse_id", "warehouse_id is not one of this company's warehouses")
    return wid


def _tracked_item(cur, entity_id, item_id) -> dict:
    it = inventory.tracked(cur, entity_id, [item_id]).get(item_id)
    if not it:
        cur.execute("SELECT 1 FROM items WHERE id = %s AND entity_id = %s", (item_id, entity_id))
        if cur.fetchone():
            raise ApiError(409, "item_not_tracked", "This item is not kept in stock", "Turn on stock tracking for the item first. Only goods can be tracked.")
        raise _bad("item_id", "item_id is not one of this company's items")
    return it


def _after(cur, entity_id, item_id, move_ids: list[int], extra: dict | None = None) -> dict:
    cur.execute(MOVE_SQL + " WHERE m.id = ANY(%s) ORDER BY m.id", (move_ids,))
    moves = [_move_out(r) for r in cur.fetchall()]
    return {"movements": moves, "item": _stock_rows(cur, entity_id, item_id=item_id)[0], **(extra or {})}


@router.post(E + "/stock-adjustments", status_code=201, tags=["Inventory"])
def adjust_stock(entity_id: uuid.UUID, body: AdjustmentIn, p: Principal = Auth, idempotency_key: str | None = Header(default=None, alias="Idempotency-Key")):
    """Record what a person found: opening stock, a stock count, or a write-off. Each posts its journal in the same transaction.

    `opening` brings in a quantity at a unit cost. With booking `post` it posts Dr Inventory, Cr Opening balance equity; with
    `in_opening_balances` it only records the stock, because the opening trial balance already carries its value (the default when one
    has been imported). `count` takes the quantity physically there and books the difference at the average cost. `write_off` takes a
    quantity out at the average cost."""
    if body.date > dt.date.today():
        raise _bad("date", "Stock cannot be adjusted on a date that has not come")
    with tx(p.user_id) as cur:
        require(cur, entity_id, "inventory:write")
        replay = idempotency_begin(cur, idempotency_key, "POST", f"/entities/{entity_id}/stock-adjustments", body.model_dump(mode="json"))
        if replay:
            return JSONResponse(replay["body"], status_code=replay["status"])
        item = _tracked_item(cur, entity_id, body.item_id)
        wh = _warehouse(cur, entity_id, body.warehouse_id)
        pos = inventory.position(cur, entity_id, body.item_id)
        here = inventory.level(cur, entity_id, body.item_id, wh)
        allowed = inventory.negative_allowed(cur, entity_id)
        label = f"{item['sku']} {item['name']}"
        extra: dict = {}
        if body.kind == "opening":
            if body.qty is None or body.unit_cost is None:
                raise _bad("unit_cost", "Opening stock needs a quantity and what one unit cost")
            if body.counted_qty is not None:
                raise _bad("counted_qty", "A counted quantity goes with a stock count")
            cur.execute("SELECT 1 FROM stock_movements WHERE item_id = %s AND warehouse_id = %s AND source_kind = 'opening'", (body.item_id, wh))
            if cur.fetchone():
                raise ApiError(409, "conflict", "Opening stock is already recorded for this item here", "Correct it with a stock count instead.")
            booking = body.booking
            if booking is None:
                cur.execute("SELECT 1 FROM import_batches WHERE entity_id = %s AND kind = 'opening_balances' AND undone_at IS NULL", (entity_id,))
                booking = "in_opening_balances" if cur.fetchone() else "post"
            value = inventory.r2(body.qty * body.unit_cost)
            jid = None
            if booking == "post" and value > 0:
                jid = ledger.post_journal(cur, entity_id, body.date, "inventory", None, f"Opening stock {label}", inventory.opening_lines(value))
            ids = [inventory.record(cur, entity_id, item_id=body.item_id, warehouse_id=wh, date=body.date, qty_in=body.qty, unit_cost=body.unit_cost, value=value,
                                    source_kind="opening", reason="opening", note=body.note, journal_entry_id=jid)]
            extra["booking"] = booking
        else:
            if body.booking is not None:
                raise _bad("booking", "booking goes with opening stock")
            if not (body.reason or "").strip():
                raise _bad("reason", "Say why the stock is being adjusted")
            if body.kind == "count":
                if body.counted_qty is None or body.qty is not None:
                    raise _bad("counted_qty", "A stock count needs the quantity counted, and nothing else")
                diff = inventory.count_difference(here, body.counted_qty)
                if diff == 0:
                    raise ApiError(409, "conflict", "The count matches the books", f"{label}: {here.normalize():f} {item['uqc']} on hand. There is nothing to adjust.")
            else:
                if body.qty is None or body.counted_qty is not None:
                    raise _bad("qty", "A write-off needs the quantity to take out")
                if body.unit_cost is not None:
                    raise _bad("unit_cost", "A write-off leaves at the average cost")
                if not allowed and here < body.qty:
                    raise inventory.insufficient(item, here, body.qty)
                diff = -body.qty
            if diff > 0:                                             # stock found
                unit = body.unit_cost if body.unit_cost is not None else (pos.average or inventory.last_cost(cur, entity_id, item))
                if unit <= 0:
                    raise _bad("unit_cost", "There is no cost on record for this item: give what one unit cost")
                value = inventory.r2(diff * unit)
                jid = ledger.post_journal(cur, entity_id, body.date, "inventory", None, f"Stock count {label}: {body.reason.strip()}", inventory.adjustment_lines(value, gain=True)) if value > 0 else None
                ids = [inventory.record(cur, entity_id, item_id=body.item_id, warehouse_id=wh, date=body.date, qty_in=diff, unit_cost=unit, value=value,
                                        source_kind="adjustment", reason=body.kind, note=body.reason.strip(), journal_entry_id=jid)]
            else:
                if body.kind == "count" and body.unit_cost is not None:
                    raise _bad("unit_cost", "Missing stock leaves at the average cost")
                value, unit, _ = inventory.issue(pos, -diff, inventory.last_cost(cur, entity_id, item) if pos.qty <= 0 else ZERO)
                what = "Stock count" if body.kind == "count" else "Stock written off"
                jid = ledger.post_journal(cur, entity_id, body.date, "inventory", None, f"{what} {label}: {body.reason.strip()}", inventory.adjustment_lines(value, gain=False)) if value > 0 else None
                ids = [inventory.record(cur, entity_id, item_id=body.item_id, warehouse_id=wh, date=body.date, qty_out=-diff, unit_cost=unit, value=-value,
                                        source_kind="adjustment", reason=body.kind, note=body.reason.strip(), journal_entry_id=jid)]
        out = _after(cur, entity_id, body.item_id, ids, extra)
        audit(cur, entity_id, "stock.adjusted", "items", body.item_id, after={"kind": body.kind, "movement": out["movements"][0]})
        emit(cur, entity_id, "stock.adjusted", "items", body.item_id, {"kind": body.kind, "sku": item["sku"], "value": out["movements"][0]["value"]})
        idempotency_finish(cur, idempotency_key, 201, out)
        return out


class TransferIn(BaseModel):
    item_id: uuid.UUID
    from_warehouse_id: uuid.UUID
    to_warehouse_id: uuid.UUID
    date: dt.date
    qty: Decimal = Field(gt=0, max_digits=18, decimal_places=3)
    note: str | None = Field(default=None, max_length=500)


@router.post(E + "/stock-transfers", status_code=201, tags=["Inventory"])
def transfer_stock(entity_id: uuid.UUID, body: TransferIn, p: Principal = Auth):
    """Move a quantity of an item from one warehouse to another. The cost moves with it and the value of stock does not change, so
    nothing is posted to the ledger."""
    if body.date > dt.date.today():
        raise _bad("date", "Stock cannot be moved on a date that has not come")
    if body.from_warehouse_id == body.to_warehouse_id:
        raise _bad("to_warehouse_id", "Choose a different warehouse to move the stock to")
    with tx(p.user_id) as cur:
        require(cur, entity_id, "inventory:write")
        item = _tracked_item(cur, entity_id, body.item_id)
        src, dst = _warehouse(cur, entity_id, body.from_warehouse_id), _warehouse(cur, entity_id, body.to_warehouse_id)
        pos = inventory.position(cur, entity_id, body.item_id)
        here = inventory.level(cur, entity_id, body.item_id, src)
        if here < body.qty and not inventory.negative_allowed(cur, entity_id):
            raise inventory.insufficient(item, here, body.qty)
        unit = pos.average or inventory.last_cost(cur, entity_id, item)
        value = inventory.r2(body.qty * unit)
        ref = uuid.uuid4()
        ids = [inventory.record(cur, entity_id, item_id=body.item_id, warehouse_id=src, date=body.date, qty_out=body.qty, unit_cost=unit, value=-value,
                                source_kind="transfer", source_id=ref, note=body.note),
               inventory.record(cur, entity_id, item_id=body.item_id, warehouse_id=dst, date=body.date, qty_in=body.qty, unit_cost=unit, value=value,
                                source_kind="transfer", source_id=ref, note=body.note)]
        out = _after(cur, entity_id, body.item_id, ids)
        audit(cur, entity_id, "stock.transferred", "items", body.item_id, after={"qty": q3(body.qty), "from": str(src), "to": str(dst)})
        return out


# ================================================================== valuation
@router.get(E + "/stock-valuation", tags=["Inventory"])
def stock_valuation(entity_id: uuid.UUID, p: Principal = Auth, as_of: dt.date | None = None):
    """Stock as of a date: each item's quantity and value from the movements dated up to it, the total, and the balance of the
    Inventory account on the same date. The two agree unless stock was booked to another account or journals were posted to
    Inventory by hand; `difference` shows it."""
    as_of = as_of or dt.date.today()
    with tx(p.user_id) as cur:
        require(cur, entity_id, "inventory:read")
        cur.execute("""SELECT i.id AS item_id, i.sku, i.name, i.uqc, sum(m.qty_in - m.qty_out) AS qty, sum(m.value) AS value
                         FROM stock_movements m JOIN items i ON i.id = m.item_id
                        WHERE m.entity_id = %s AND m.movement_date <= %s GROUP BY i.id, i.sku, i.name, i.uqc
                       HAVING sum(m.qty_in - m.qty_out) <> 0 OR sum(m.value) <> 0 ORDER BY i.sku""", (entity_id, as_of))
        rows = cur.fetchall()
        cur.execute("""SELECT coalesce(sum(l.debit - l.credit), 0) AS b FROM journal_lines l JOIN journal_entries j ON j.id = l.entry_id
                         JOIN ledger_accounts a ON a.id = l.account_id
                        WHERE j.entity_id = %s AND j.status IN ('posted', 'reversed') AND j.entry_date <= %s AND a.subtype = 'inventory'""", (entity_id, as_of))
        ledger_balance = cur.fetchone()["b"]
    total = sum((r["value"] for r in rows), ZERO)
    return {"as_of": as_of.isoformat(), "valuation_method": "weighted_average",
            "items": [{"item_id": str(r["item_id"]), "sku": r["sku"], "name": r["name"], "uqc": r["uqc"], "qty": q3(r["qty"]),
                       "average_cost": c4(inventory.Position(r["qty"], r["value"]).average), "value": money(r["value"])} for r in rows],
            "total_value": money(total), "ledger_balance": money(ledger_balance), "difference": money(ledger_balance - total)}
