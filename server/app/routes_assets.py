"""Fixed assets: the register, monthly depreciation posted to the ledger, and the sale or write-off of an asset.

Registering an asset moves its cost out of expenses (`reclassify`), or just records it when the bill was already booked to an asset account
(`already_booked`) or when it is an opening balance (`opening`). Depreciation is a journal a person runs for a month; the schedule itself
is in assets.py. Everything runs under the finance permission."""
from __future__ import annotations

import datetime as dt
import uuid
from decimal import Decimal
from typing import Literal

from fastapi import APIRouter, Header, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from . import assets, ledger, tax
from .core import ApiError, Auth, Principal, audit, emit, idempotency_begin, idempotency_finish, require, tx
from .routes_orders import js, next_number

router = APIRouter()
E = "/entities/{entity_id}"
Category = Literal["building", "plant_machinery", "furniture", "office_equipment", "computers", "servers_networks", "vehicles", "electrical", "software", "other"]
ZERO = Decimal(0)

ASSET_SQL = """SELECT a.id, a.asset_no, a.name, a.category, a.location, a.serial_no, a.party_id, a.purchase_bill_id, a.bill_of_entry_id, a.document_id,
                      a.acquired_on, a.put_to_use_on, a.cost, a.residual_value, a.method, a.life_months, a.rate_pct, a.booking, a.opening_accumulated,
                      a.opening_through, a.status, a.disposed_on, a.disposal_proceeds,
                      a.opening_accumulated * (a.booking = 'opening')::int + coalesce((SELECT sum(d.amount) FROM asset_depreciation d WHERE d.asset_id = a.id), 0) AS accumulated_depreciation
                 FROM fixed_assets a"""


class AssetIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    category: Category
    location: str | None = Field(default=None, max_length=80)
    serial_no: str | None = Field(default=None, max_length=60)
    party_id: uuid.UUID | None = None
    purchase_bill_id: uuid.UUID | None = None
    bill_of_entry_id: uuid.UUID | None = None
    document_id: uuid.UUID | None = None
    acquired_on: dt.date
    put_to_use_on: dt.date | None = None
    cost: Decimal = Field(gt=0, max_digits=18, decimal_places=2)
    residual_value: Decimal | None = Field(default=None, ge=0, max_digits=18, decimal_places=2)
    method: Literal["slm", "wdv"] = "slm"
    life_months: int | None = Field(default=None, ge=1, le=1200)
    rate_pct: Decimal | None = Field(default=None, gt=0, lt=100, max_digits=6, decimal_places=2)
    booking: Literal["reclassify", "already_booked", "opening"] = "reclassify"
    opening_accumulated: Decimal | None = Field(default=None, ge=0, max_digits=18, decimal_places=2)
    opening_through: dt.date | None = None


class DepreciationRunIn(BaseModel):
    period: str = Field(pattern=r"^[0-9]{4}-(0[1-9]|1[0-2])$")


class DisposeIn(BaseModel):
    disposed_on: dt.date
    proceeds: Decimal = Field(ge=0, max_digits=18, decimal_places=2)
    proceeds_account_id: uuid.UUID | None = None


def _bad(field: str, message: str):
    return ApiError(400, "validation_failed", message, errors=[{"field": field, "message": message}])


def _period(s: str) -> dt.date:
    return dt.date(int(s[:4]), int(s[5:7]), 1)


def _check_period(m: dt.date) -> None:
    if m > assets.month_start(dt.date.today()):
        raise ApiError(400, "validation_failed", "Depreciation cannot be run for a month that has not started", errors=[{"field": "period", "message": "choose this month or an earlier one"}])


def _posted(cur, asset_ids: list) -> dict:
    cur.execute("SELECT asset_id, period_month, amount FROM asset_depreciation WHERE asset_id = ANY(%s)", (asset_ids,))
    out: dict = {}
    for r in cur.fetchall():
        out.setdefault(r["asset_id"], {})[r["period_month"]] = r["amount"]
    return out


def _load(cur, entity_id, aid, *, lock=False) -> dict:
    cur.execute(ASSET_SQL + " WHERE a.id = %s AND a.entity_id = %s" + (" FOR UPDATE OF a" if lock else ""), (aid, entity_id))
    a = cur.fetchone()
    if not a:
        raise ApiError(404, "not_found", "Not found")
    return a


def _out(a: dict) -> dict:
    row = dict(a)
    row["book_value"] = a["cost"] - a["accumulated_depreciation"]
    return js(row)


def _check_links(cur, entity_id, b: AssetIn) -> None:
    for table, value, field in (("purchase_bills", b.purchase_bill_id, "purchase_bill_id"), ("bills_of_entry", b.bill_of_entry_id, "bill_of_entry_id"),
                                ("parties", b.party_id, "party_id"), ("documents", b.document_id, "document_id")):
        if value:
            cur.execute(f"SELECT 1 FROM {table} WHERE id = %s AND entity_id = %s", (value, entity_id))
            if not cur.fetchone():
                raise _bad(field, f"{field} is not one of this company's records")


@router.get(E + "/fixed-asset-categories", tags=["Fixed assets"])
def categories(entity_id: uuid.UUID, p: Principal = Auth):
    """The starter list: label, useful life under Schedule II and the income-tax block rate for reference. Needs a CA's review."""
    with tx(p.user_id) as cur:
        require(cur, entity_id, "finance:read")
    return {"data": [{"category": k, "label": v[0], "life_months": v[1] * 12, "income_tax_rate_pct": v[2],
                      "residual_pct": "0.00" if k == "software" else str(assets.DEFAULT_RESIDUAL_PCT) + ".00"} for k, v in assets.CATEGORIES.items()]}


@router.get(E + "/fixed-assets", tags=["Fixed assets"])
def list_assets(entity_id: uuid.UUID, status: Literal["in_use", "disposed"] | None = None, p: Principal = Auth):
    with tx(p.user_id) as cur:
        require(cur, entity_id, "finance:read")
        cur.execute(ASSET_SQL + " WHERE a.entity_id = %s AND (%s::text IS NULL OR a.status = %s) ORDER BY a.asset_no", (entity_id, status, status))
        rows = [_out(a) for a in cur.fetchall()]
    live = [r for r in rows if r["status"] == "in_use"]
    return {"data": rows, "totals": js({"cost": sum((Decimal(r["cost"]) for r in live), ZERO),
                                         "accumulated_depreciation": sum((Decimal(r["accumulated_depreciation"]) for r in live), ZERO),
                                         "book_value": sum((Decimal(r["book_value"]) for r in live), ZERO)})}


@router.post(E + "/fixed-assets", status_code=201, tags=["Fixed assets"])
def create_asset(entity_id: uuid.UUID, body: AssetIn, p: Principal = Auth, idempotency_key: str | None = Header(default=None, alias="Idempotency-Key")):
    """Register an asset. `reclassify` moves the cost from Purchases and expenses to Fixed assets; `already_booked` only records it;
    `opening` is an asset from earlier books, with the depreciation charged so far."""
    with tx(p.user_id) as cur:
        require(cur, entity_id, "finance:write")
        replay = idempotency_begin(cur, idempotency_key, "POST", f"/entities/{entity_id}/fixed-assets", body.model_dump(mode="json"))
        if replay:
            return JSONResponse(replay["body"], status_code=replay["status"])
        use_on = body.put_to_use_on or body.acquired_on
        if use_on < body.acquired_on:
            raise _bad("put_to_use_on", "An asset cannot be put to use before it is acquired")
        if body.method == "slm":
            if body.rate_pct is not None:
                raise _bad("rate_pct", "A yearly rate goes with the written down value method")
            life = body.life_months or assets.default_life_months(body.category)
            rate = None
        else:
            if body.rate_pct is None:
                raise _bad("rate_pct", "The written down value method needs a yearly rate")
            if body.life_months is not None:
                raise _bad("life_months", "A life in months goes with the straight line method")
            life, rate = None, body.rate_pct
        residual = body.residual_value
        if residual is None:
            residual = ZERO if body.category == "software" else tax.r2(body.cost * assets.DEFAULT_RESIDUAL_PCT / 100)
        if residual >= body.cost:
            raise _bad("residual_value", "The residual value must be less than the cost")
        if body.booking == "opening":
            if body.opening_through is None or body.opening_accumulated is None:
                raise _bad("opening_through", "An opening asset needs the depreciation charged so far and the date it is as of")
            if body.purchase_bill_id or body.bill_of_entry_id:
                raise _bad("booking", "An opening asset comes from earlier books, not from a bill")
            if body.opening_accumulated > body.cost - residual:
                raise _bad("opening_accumulated", "The depreciation so far is more than the cost less the residual value")
            if body.opening_through != assets.month_end(body.opening_through):
                raise _bad("opening_through", "Give the last day of a month")
        elif body.opening_through is not None or body.opening_accumulated is not None:
            raise _bad("opening_through", "Depreciation so far goes only with an opening asset")
        _check_links(cur, entity_id, body)
        no = next_number(cur, entity_id, "fixed_assets", "asset_no", "FA-", 4)
        jid = None
        if body.booking == "reclassify":
            jid = ledger.post_journal(cur, entity_id, body.acquired_on, "asset", None, f"Capitalised {no} {body.name}",
                                      [ledger.Line("fixed_assets", debit=body.cost, memo=body.name), ledger.Line("purchases", credit=body.cost, memo=body.name)])
        cur.execute("""INSERT INTO fixed_assets (entity_id, asset_no, name, category, location, serial_no, party_id, purchase_bill_id, bill_of_entry_id, document_id,
                                                 acquired_on, put_to_use_on, cost, residual_value, method, life_months, rate_pct, booking, opening_accumulated,
                                                 opening_through, acquisition_journal_id, created_by)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,app.current_user_id()) RETURNING id""",
                    (entity_id, no, body.name.strip(), body.category, body.location, body.serial_no, body.party_id, body.purchase_bill_id, body.bill_of_entry_id,
                     body.document_id, body.acquired_on, use_on, body.cost, residual, body.method, life, rate, body.booking, body.opening_accumulated or ZERO,
                     body.opening_through, jid))
        aid = cur.fetchone()["id"]
        out = _out(_load(cur, entity_id, aid))
        audit(cur, entity_id, "asset.registered", "fixed_assets", aid, after=out)
        emit(cur, entity_id, "asset.registered", "fixed_assets", aid, {"asset_no": no, "cost": str(body.cost)})
        idempotency_finish(cur, idempotency_key, 201, out)
        return out


def _rows_due(cur, entity_id, upto: dt.date, only=None) -> tuple[list[dict], dict]:
    """Every unposted month up to `upto` for each asset still in use, with the amount; and the assets by id."""
    cur.execute(ASSET_SQL + " WHERE a.entity_id = %s AND a.status = 'in_use' " + ("AND a.id = %s " if only else "") + "ORDER BY a.asset_no FOR UPDATE OF a",
                (entity_id, only) if only else (entity_id,))
    found = {a["id"]: a for a in cur.fetchall()}
    posted = _posted(cur, list(found)) if found else {}
    due = []
    for aid, a in found.items():
        for r in assets.schedule(a, posted.get(aid, {}), upto):
            if not r["posted"] and r["amount"] > 0:
                due.append({"asset_id": aid, "asset_no": a["asset_no"], "name": a["name"], **r})
    return sorted(due, key=lambda r: (r["month"], r["asset_no"])), found


@router.get(E + "/depreciation/preview", tags=["Fixed assets"])
def preview(entity_id: uuid.UUID, period: str = Query(pattern=r"^[0-9]{4}-(0[1-9]|1[0-2])$"), p: Principal = Auth):
    """What running depreciation up to the month would post, month by month. Posts nothing."""
    m = _period(period)
    _check_period(m)
    with tx(p.user_id) as cur:
        require(cur, entity_id, "finance:read")
        due, _ = _rows_due(cur, entity_id, m)
    months: dict = {}
    for r in due:
        months.setdefault(r["month"], []).append(r)
    return js({"period": period, "total": sum((r["amount"] for r in due), ZERO),
               "months": [{"month": k, "total": sum((r["amount"] for r in v), ZERO), "assets": [{"asset_id": r["asset_id"], "asset_no": r["asset_no"], "name": r["name"],
                           "amount": r["amount"], "book_value_after": r["book_value_after"]} for r in v]} for k, v in months.items()]})


@router.post(E + "/depreciation/run", tags=["Fixed assets"])
def run_depreciation(entity_id: uuid.UUID, body: DepreciationRunIn, p: Principal = Auth, idempotency_key: str | None = Header(default=None, alias="Idempotency-Key")):
    """Post depreciation for every month up to the one given that has not been posted: one journal per month, covering all assets."""
    m = _period(body.period)
    _check_period(m)
    with tx(p.user_id) as cur:
        require(cur, entity_id, "finance:write")
        replay = idempotency_begin(cur, idempotency_key, "POST", f"/entities/{entity_id}/depreciation/run", body.model_dump(mode="json"))
        if replay:
            return JSONResponse(replay["body"], status_code=replay["status"])
        due, _ = _rows_due(cur, entity_id, m)
        journals = []
        for month in sorted({r["month"] for r in due}):
            rows = [r for r in due if r["month"] == month]
            total = sum((r["amount"] for r in rows), ZERO)
            label = month.strftime("%b %Y")
            jid = ledger.post_journal(cur, entity_id, assets.month_end(month), "asset", None, f"Depreciation for {label}",
                                      [ledger.Line("depreciation_expense", debit=total, memo=f"{len(rows)} assets"), ledger.Line("accumulated_depreciation", credit=total)])
            for r in rows:
                cur.execute("INSERT INTO asset_depreciation (entity_id, asset_id, period_month, amount, book_value_after, journal_entry_id) VALUES (%s,%s,%s,%s,%s,%s)",
                            (entity_id, r["asset_id"], month, r["amount"], r["book_value_after"], jid))
            cur.execute("SELECT entry_no FROM journal_entries WHERE id = %s", (jid,))
            journals.append({"month": month, "journal_entry_id": jid, "entry_no": cur.fetchone()["entry_no"], "total": total, "assets": len(rows)})
            audit(cur, entity_id, "asset.depreciation_posted", "journal_entries", jid, after={"month": month.isoformat(), "total": str(total), "assets": len(rows)})
            emit(cur, entity_id, "asset.depreciation_posted", "journal_entries", jid, {"month": month.isoformat(), "total": str(total)})
        out = js({"period": body.period, "journals": journals, "total": sum((j["total"] for j in journals), ZERO)})
        idempotency_finish(cur, idempotency_key, 200, out)
        return out


@router.get(E + "/fixed-assets/{asset_id}", tags=["Fixed assets"])
def get_asset(entity_id: uuid.UUID, asset_id: uuid.UUID, through: dt.date | None = None, p: Principal = Auth):
    """The asset and its depreciation month by month: what was posted, and what the coming months will charge (to the end of this financial year, or `through`)."""
    with tx(p.user_id) as cur:
        require(cur, entity_id, "finance:read")
        a = _load(cur, entity_id, asset_id)
        posted = _posted(cur, [asset_id]).get(asset_id, {})
    today = dt.date.today()
    horizon = through or dt.date(assets.fy_start(today).year + 1, 3, 31)
    rows = assets.schedule(a, posted, horizon) if a["status"] == "in_use" or posted else []
    out = _out(a)
    out["schedule"] = js([{"month": r["month"], "amount": r["amount"], "book_value_after": r["book_value_after"], "posted": r["posted"]} for r in rows])
    return out


@router.post(E + "/fixed-assets/{asset_id}/dispose", tags=["Fixed assets"])
def dispose_asset(entity_id: uuid.UUID, asset_id: uuid.UUID, body: DisposeIn, p: Principal = Auth,
                  idempotency_key: str | None = Header(default=None, alias="Idempotency-Key")):
    """Sell or write off an asset. Depreciation up to the month before must already be posted; the part month is charged here (or, when the
    month was already run in full, the unused part is given back), and the gain or loss is the difference between the proceeds and the book
    value on the day. GST on the sale is not posted."""
    with tx(p.user_id) as cur:
        require(cur, entity_id, "finance:write")
        replay = idempotency_begin(cur, idempotency_key, "POST", f"/entities/{entity_id}/fixed-assets/{asset_id}/dispose", body.model_dump(mode="json"))
        if replay:
            return JSONResponse(replay["body"], status_code=replay["status"])
        a = _load(cur, entity_id, asset_id, lock=True)
        if a["status"] != "in_use":
            raise ApiError(409, "conflict", "This asset has already been disposed of")
        if body.disposed_on < a["put_to_use_on"]:
            raise _bad("disposed_on", "An asset cannot be disposed of before it was put to use")
        if assets.month_start(body.disposed_on) < assets.first_month(a):
            raise _bad("disposed_on", "Depreciation was charged up to the opening balance date; dispose of it after that")
        dispose_month = assets.month_start(body.disposed_on)
        posted = _posted(cur, [asset_id]).get(asset_id, {})
        if any(m > dispose_month for m in posted):
            raise ApiError(409, "conflict", "Depreciation has been posted for months after the date of sale", errors=[{"field": "disposed_on", "message": "choose a date in the latest month that is posted, or later"}])
        full_month = posted.get(dispose_month)                          # the month was run in full before the sale: the unused part is given back below
        sched = assets.schedule({**a, "disposed_on": body.disposed_on}, {m: v for m, v in posted.items() if m != dispose_month}, body.disposed_on)
        behind = [r for r in sched if r["month"] < dispose_month and not r["posted"] and r["amount"] > 0]
        if behind:
            raise ApiError(409, "depreciation_pending", "Run depreciation up to the month before the sale first", errors=[{"field": "disposed_on", "message": f"{behind[0]['month']:%b %Y} is not posted yet"}])
        last = next((r for r in sched if r["month"] == dispose_month), None)
        charge = last["amount"] if last else ZERO                        # what the month should carry, up to the day of sale
        refund = max(ZERO, full_month - charge) if full_month is not None else ZERO
        accumulated_before = assets.accumulated(a, posted)               # includes the full month when it was posted
        res = assets.disposal_result(cost=a["cost"], accumulated_before=accumulated_before - refund, last_month_charge=ZERO if full_month is not None else charge, proceeds=body.proceeds)
        if body.proceeds_account_id:
            cur.execute("SELECT 1 FROM ledger_accounts WHERE id = %s AND entity_id = %s", (body.proceeds_account_id, entity_id))
            if not cur.fetchone():
                raise _bad("proceeds_account_id", "proceeds_account_id is not an account of this company")
        lines = []
        if body.proceeds:
            lines.append(ledger.Line(f"account:{body.proceeds_account_id}" if body.proceeds_account_id else "bank", debit=body.proceeds, memo=f"Sale of {a['asset_no']}"))
        if accumulated_before:
            lines.append(ledger.Line("accumulated_depreciation", debit=accumulated_before))
        if charge and full_month is None:
            lines.append(ledger.Line("depreciation_expense", debit=charge, memo=f"{a['asset_no']} to the day of sale"))
        if refund:
            lines.append(ledger.Line("depreciation_expense", credit=refund, memo=f"{a['asset_no']}: the part of the month after the sale"))
        lines.append(ledger.Line("fixed_assets", credit=a["cost"], memo=a["name"]))
        if res["gain"] > 0:
            lines.append(ledger.Line("asset_disposal_gain_loss", credit=res["gain"]))
        elif res["gain"] < 0:
            lines.append(ledger.Line("asset_disposal_gain_loss", debit=-res["gain"]))
        jid = ledger.post_journal(cur, entity_id, body.disposed_on, "asset", asset_id, f"Disposal of {a['asset_no']} {a['name']}", lines)
        if full_month is not None:
            cur.execute("UPDATE asset_depreciation SET amount = %s, book_value_after = %s WHERE asset_id = %s AND period_month = %s", (charge, res["book_value"], asset_id, dispose_month))
        elif charge:
            cur.execute("INSERT INTO asset_depreciation (entity_id, asset_id, period_month, amount, book_value_after, journal_entry_id) VALUES (%s,%s,%s,%s,%s,%s)",
                        (entity_id, asset_id, dispose_month, charge, res["book_value"], jid))
        cur.execute("UPDATE fixed_assets SET status = 'disposed', disposed_on = %s, disposal_proceeds = %s, disposal_journal_id = %s WHERE id = %s",
                    (body.disposed_on, body.proceeds, jid, asset_id))
        out = _out(_load(cur, entity_id, asset_id))
        out["gain_or_loss"] = js(res["gain"])
        audit(cur, entity_id, "asset.disposed", "fixed_assets", asset_id, after=out)
        emit(cur, entity_id, "asset.disposed", "fixed_assets", asset_id, {"asset_no": a["asset_no"], "gain_or_loss": str(res["gain"])})
        idempotency_finish(cur, idempotency_key, 200, out)
        return out
