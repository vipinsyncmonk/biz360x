"""Year-end tax declarations (roles phase 5, migration 42, taxdecl.py).

The employee, signed in to their own page (/self, role biz_self, functions in the schema `self`), chooses a regime and declares deductions, and
sends it to HR. HR verifies each figure or returns the declaration with a note. Payroll deducts TDS on the verified figures only
(routes_payroll._declared). Form 16 Part B is worked out from the year's approved payslips and the verified declaration.

Every section and limit is an Indian law figure from a starter library (tax_deduction_sections) that a CA must review.
"""
from __future__ import annotations

import datetime as dt
import json
import re
import uuid
from decimal import Decimal
from typing import Literal

from fastapi import APIRouter, Query
from pydantic import BaseModel, Field

from . import taxdecl
from .core import ApiError, Auth, Principal, audit, emit, money, require, tx
from .routes_hr import _employee_row
from .routes_orders import js

router = APIRouter()
E = "/entities/{entity_id}"
S = "/self/{employee_id}"
ROLE = "biz_self"
FY = r"^[0-9]{4}-[0-9]{2}$"
TAG = "Tax declarations"


def _bad(field: str, message: str):
    return ApiError(400, "validation_failed", message, errors=[{"field": field, "message": message}])


def _fy(fy: str | None) -> str:
    fy = fy or taxdecl.fy_of(dt.date.today())
    if not re.match(FY, fy) or (int(fy[:4]) + 1) % 100 != int(fy[5:]):
        raise _bad("fy", "A financial year looks like 2026-27")
    return fy


def _need(cur, entity_id, action: str) -> None:
    """Payroll or HR may do this: the person who verifies declarations is HR, and the one who runs payroll needs to see them."""
    try:
        require(cur, entity_id, f"hr:{action}")
    except ApiError as e:
        if e.status != 403:
            raise
        require(cur, entity_id, f"payroll:{action}")


def _out_money(x):
    if isinstance(x, Decimal):
        return money(x)
    if isinstance(x, dict):
        return {k: _out_money(v) for k, v in x.items()}
    if isinstance(x, list):
        return [_out_money(v) for v in x]
    return x


def _item_out(i: dict) -> dict:
    return {"id": str(i["id"]), "section": i["section"], "description": i["description"], "declared_amount": money(i["declared_amount"]), "proof_ref": i["proof_ref"],
            "status": i["status"], "verified_amount": money(i["verified_amount"]) if i["verified_amount"] is not None else None, "hr_note": i["hr_note"]}


def _caps(sections: list[dict]) -> dict:
    return {s["code"]: s["annual_cap"] for s in sections}


def _section_out(s: dict) -> dict:
    return {"code": s["code"], "label": s["label"], "annual_cap": money(s["annual_cap"]) if s["annual_cap"] is not None else None, "old_regime_only": s["old_regime_only"],
            "note": s["note"]}


def _decl_out(d: dict | None, items: list[dict], sections: list[dict], default_regime: str, fy: str) -> dict:
    status = d["status"] if d else "none"
    caps = _caps(sections)
    return {"fy": fy, "regime": d["regime"] if d else default_regime, "status": status, "id": str(d["id"]) if d else None,
            "submitted_at": d["submitted_at"].isoformat() if d and d["submitted_at"] else None, "verified_at": d["verified_at"].isoformat() if d and d["verified_at"] else None,
            "hr_note": d["hr_note"] if d else None, "editable": status in ("none", "draft", "returned"), "sections": [_section_out(s) for s in sections],
            "items": [_item_out(i) for i in items],
            "summary": _out_money([{**r, "cap": r["cap"]} for r in taxdecl.declared_view(items, caps, use_verified=status == "verified")]),
            "note": "Deductions apply to TDS only under the old regime, and only once HR has verified them. The limits are a starter library a CA must review."}


# ================================================================== the employee's own
class ItemIn(BaseModel):
    section: str = Field(pattern=r"^[0-9A-Z]{2,10}$")
    description: str = Field(min_length=1, max_length=200)
    declared_amount: Decimal = Field(ge=0, max_digits=14, decimal_places=2)
    proof_ref: str | None = Field(default=None, max_length=200)


class DeclarationIn(BaseModel):
    regime: Literal["new", "old"]
    items: list[ItemIn] = Field(default_factory=list, max_length=40)


def _mine(cur, employee_id, fy: str):
    cur.execute("SELECT * FROM self.profile(%s)", (employee_id,))
    e = cur.fetchone()
    if not e:
        raise ApiError(404, "not_found", "Not found")
    cur.execute("SELECT * FROM self.tax_sections(%s, %s)", (employee_id, fy))
    sections = cur.fetchall()
    if not sections:
        raise ApiError(404, "not_found", f"No tax sections are set up for {fy}")
    cur.execute("SELECT * FROM self.tax_declaration(%s, %s)", (employee_id, fy))
    d = cur.fetchone()
    cur.execute("SELECT * FROM self.tax_declaration_items(%s, %s)", (employee_id, fy))
    return e, sections, d, cur.fetchall()


@router.get(S + "/tax-declaration", tags=[TAG])
def my_declaration(employee_id: uuid.UUID, p: Principal = Auth, fy: str | None = Query(default=None, pattern=FY)):
    """My declaration for a financial year (this one by default): the regime, the deductions, where it stands with HR, and the sections I can claim under."""
    fy = _fy(fy)
    with tx(p.user_id, role=ROLE) as cur:
        e, sections, d, items = _mine(cur, employee_id, fy)
        return _decl_out(d, items, sections, e["tax_regime"] or "new", fy)


@router.put(S + "/tax-declaration", tags=[TAG])
def save_my_declaration(employee_id: uuid.UUID, body: DeclarationIn, p: Principal = Auth, fy: str | None = Query(default=None, pattern=FY)):
    """Save my declaration as a draft: the regime and the whole list of deductions. It cannot change while it waits with HR or once verified."""
    fy = _fy(fy)
    with tx(p.user_id, role=ROLE) as cur:
        e, sections, d, items = _mine(cur, employee_id, fy)
        codes = {s["code"] for s in sections}
        for n, it in enumerate(body.items):
            if it.section not in codes:
                raise _bad(f"items.{n}.section", f"{it.section} is not a section you can claim under for {fy}")
        if d and d["status"] in ("submitted", "verified"):
            raise ApiError(409, "conflict", "Your declaration is with HR", "Ask HR to return it if something has to change.")
        cur.execute("SELECT self.save_tax_declaration(%s, %s, %s, %s::jsonb)",
                    (employee_id, fy, body.regime, json.dumps([{"section": i.section, "description": i.description, "declared_amount": str(i.declared_amount),
                                                                                  "proof_ref": i.proof_ref} for i in body.items])))
        e, sections, d, items = _mine(cur, employee_id, fy)
        return _decl_out(d, items, sections, e["tax_regime"] or "new", fy)


@router.post(S + "/tax-declaration/submit", tags=[TAG])
def submit_my_declaration(employee_id: uuid.UUID, p: Principal = Auth, fy: str | None = Query(default=None, pattern=FY)):
    """Send my saved declaration to HR to verify. Sending it twice changes nothing."""
    fy = _fy(fy)
    with tx(p.user_id, role=ROLE) as cur:
        e, sections, d, items = _mine(cur, employee_id, fy)
        if not d:
            raise ApiError(409, "conflict", "Save your declaration first")
        cur.execute("SELECT self.submit_tax_declaration(%s, %s)", (employee_id, fy))
        e, sections, d, items = _mine(cur, employee_id, fy)
        return _decl_out(d, items, sections, e["tax_regime"] or "new", fy)


@router.get(S + "/form16", tags=[TAG])
def my_form16(employee_id: uuid.UUID, p: Principal = Auth, fy: str | None = Query(default=None, pattern=FY)):
    """Form 16 Part B for a year, worked out from my approved payslips and my verified declaration. Part A comes from TRACES and is not here."""
    fy = _fy(fy)
    with tx(p.user_id, role=ROLE) as cur:
        e, sections, d, items = _mine(cur, employee_id, fy)
        cur.execute("SELECT * FROM self.tax_year_payslips(%s, %s)", (employee_id, fy))
        slips = cur.fetchall()
        regime = d["regime"] if d and d["status"] in ("submitted", "verified") else (e["tax_regime"] or "new")
        return _form16_out(e["full_name"], fy, regime, slips, items, _caps(sections), bool(d and d["status"] == "verified"))


def _form16_out(name: str, fy: str, regime: str, slips: list[dict], items: list[dict], caps: dict, verified: bool) -> dict:
    out = _out_money(taxdecl.part_b(fy=fy, regime=regime, slips=slips, items=items, caps=caps, verified=verified))
    out["employee_name"] = name
    out["note"] = ("Part B only. Part A (the certificate of TDS deposited) comes from TRACES. Salary components marked not taxable are not separated from the gross. "
                   "Deductions count only once HR has verified them. Not modelled: HRA, income from a previous employer, other income, surcharge. A CA must review before this is issued.")
    return out


# ================================================================== HR
@router.get(E + "/tax-declarations", tags=[TAG])
def list_declarations(entity_id: uuid.UUID, p: Principal = Auth, fy: str | None = Query(default=None, pattern=FY),
                      status: Literal["draft", "submitted", "verified", "returned"] | None = None):
    """Declarations for a financial year, with who made them and where each stands. Those waiting for HR come first."""
    fy = _fy(fy)
    with tx(p.user_id) as cur:
        _need(cur, entity_id, "read")
        cur.execute("""SELECT d.id, d.employee_id, e.employee_code, e.full_name, d.regime, d.status, d.submitted_at, d.verified_at,
                              (SELECT count(*) FROM tax_declaration_items i WHERE i.declaration_id = d.id) AS items,
                              (SELECT coalesce(sum(i.declared_amount), 0) FROM tax_declaration_items i WHERE i.declaration_id = d.id) AS declared
                         FROM tax_declarations d JOIN employees e ON e.id = d.employee_id
                        WHERE d.entity_id = %s AND d.fy = %s AND (%s::text IS NULL OR d.status = %s)
                        ORDER BY (d.status = 'submitted') DESC, e.employee_code""", (entity_id, fy, status, status))
        return {"fy": fy, "data": [{"id": str(r["id"]), "employee_id": str(r["employee_id"]), "employee_code": r["employee_code"], "employee_name": r["full_name"],
                                    "regime": r["regime"], "status": r["status"], "items": r["items"], "declared": money(r["declared"]),
                                    "submitted_at": r["submitted_at"].isoformat() if r["submitted_at"] else None,
                                    "verified_at": r["verified_at"].isoformat() if r["verified_at"] else None} for r in cur.fetchall()]}


def _load(cur, entity_id, declaration_id, *, lock: bool = False):
    cur.execute("SELECT d.*, e.full_name, e.employee_code FROM tax_declarations d JOIN employees e ON e.id = d.employee_id WHERE d.id = %s AND d.entity_id = %s"
                + (" FOR UPDATE OF d" if lock else ""), (declaration_id, entity_id))
    d = cur.fetchone()
    if not d:
        raise ApiError(404, "not_found", "Not found")
    cur.execute("SELECT * FROM tax_declaration_items WHERE declaration_id = %s ORDER BY section, description, id", (declaration_id,))
    items = cur.fetchall()
    cur.execute("SELECT code, label, annual_cap, old_regime_only, note FROM tax_deduction_sections WHERE fy = %s ORDER BY code", (d["fy"],))
    return d, items, cur.fetchall()


def _hr_out(d: dict, items: list[dict], sections: list[dict]) -> dict:
    out = _decl_out(d, items, sections, d["regime"], d["fy"])
    out.update({"employee_id": str(d["employee_id"]), "employee_code": d["employee_code"], "employee_name": d["full_name"]})
    return out


@router.get(E + "/tax-declarations/{declaration_id}", tags=[TAG])
def get_declaration(entity_id: uuid.UUID, declaration_id: uuid.UUID, p: Principal = Auth):
    with tx(p.user_id) as cur:
        _need(cur, entity_id, "read")
        return _hr_out(*_load(cur, entity_id, declaration_id))


class Decision(BaseModel):
    id: uuid.UUID
    status: Literal["verified", "rejected"] = "verified"
    verified_amount: Decimal | None = Field(default=None, ge=0, max_digits=14, decimal_places=2)
    note: str | None = Field(default=None, max_length=300)


class VerifyIn(BaseModel):
    items: list[Decision] = Field(default_factory=list, max_length=40)       # an item not listed is verified at the amount declared
    note: str | None = Field(default=None, max_length=300)


@router.post(E + "/tax-declarations/{declaration_id}/verify", tags=[TAG])
def verify_declaration(entity_id: uuid.UUID, declaration_id: uuid.UUID, body: VerifyIn, p: Principal = Auth):
    """Verify a submitted declaration. Each item is verified at the amount declared unless you give a lower one or reject it. From the next
    payroll computation TDS uses the verified figures (and the regime the employee chose)."""
    with tx(p.user_id) as cur:
        _need(cur, entity_id, "write")
        d, items, sections = _load(cur, entity_id, declaration_id, lock=True)
        if d["status"] != "submitted":
            raise ApiError(409, "conflict", "Only a submitted declaration can be verified", f"This one is {d['status']}.")
        by_id = {i["id"]: i for i in items}
        decisions = {}
        for n, dec in enumerate(body.items):
            if dec.id not in by_id:
                raise _bad(f"items.{n}.id", "Not an item of this declaration")
            if dec.id in decisions:
                raise _bad(f"items.{n}.id", "Decided twice")
            if dec.verified_amount is not None and dec.verified_amount > by_id[dec.id]["declared_amount"]:
                raise _bad(f"items.{n}.verified_amount", "Cannot be more than the amount declared")
            decisions[dec.id] = dec
        for i in items:
            dec = decisions.get(i["id"])
            rejected = bool(dec and dec.status == "rejected")
            amount = None if rejected else (dec.verified_amount if dec and dec.verified_amount is not None else i["declared_amount"])
            cur.execute("UPDATE tax_declaration_items SET status = %s, verified_amount = %s, hr_note = %s WHERE id = %s",
                        ("rejected" if rejected else "verified", amount, dec.note if dec else None, i["id"]))
        cur.execute("""UPDATE tax_declarations SET status = 'verified', verified_at = now(), verified_by = app.current_user_id(), hr_note = %s, updated_at = now() WHERE id = %s""",
                    (body.note, declaration_id))
        audit(cur, entity_id, "tax_declaration.verified", "tax_declarations", declaration_id,
              after={"employee_id": str(d["employee_id"]), "fy": d["fy"], "regime": d["regime"], "rejected": sum(1 for x in decisions.values() if x.status == "rejected")})
        emit(cur, entity_id, "tax_declaration.verified", "tax_declarations", declaration_id, {"employee_id": str(d["employee_id"]), "fy": d["fy"]})
        return _hr_out(*_load(cur, entity_id, declaration_id))


class ReturnIn(BaseModel):
    note: str = Field(min_length=1, max_length=300)


@router.post(E + "/tax-declarations/{declaration_id}/return", tags=[TAG])
def return_declaration(entity_id: uuid.UUID, declaration_id: uuid.UUID, body: ReturnIn, p: Principal = Auth):
    """Send a declaration back to the employee with a note, so they can change it and send it again. A verified one can be returned too; its
    deductions stop counting from the next payroll computation."""
    with tx(p.user_id) as cur:
        _need(cur, entity_id, "write")
        d, items, sections = _load(cur, entity_id, declaration_id, lock=True)
        if d["status"] not in ("submitted", "verified"):
            raise ApiError(409, "conflict", "Only a declaration that has been sent can be returned", f"This one is {d['status']}.")
        cur.execute("UPDATE tax_declaration_items SET status = 'pending', verified_amount = NULL WHERE declaration_id = %s", (declaration_id,))
        cur.execute("""UPDATE tax_declarations SET status = 'returned', verified_at = NULL, verified_by = NULL, hr_note = %s, updated_at = now() WHERE id = %s""", (body.note, declaration_id))
        audit(cur, entity_id, "tax_declaration.returned", "tax_declarations", declaration_id, after={"employee_id": str(d["employee_id"]), "fy": d["fy"], "note": body.note})
        return _hr_out(*_load(cur, entity_id, declaration_id))


@router.get(E + "/employees/{employee_id}/form16", tags=[TAG])
def employee_form16(entity_id: uuid.UUID, employee_id: uuid.UUID, p: Principal = Auth, fy: str | None = Query(default=None, pattern=FY)):
    """Form 16 Part B for an employee and a year, from the approved payslips and the verified declaration."""
    fy = _fy(fy)
    with tx(p.user_id) as cur:
        _need(cur, entity_id, "read")
        e = _employee_row(cur, entity_id, employee_id)
        cur.execute("""SELECT r.period_month, s.gross, coalesce((SELECT sum(l.amount) FROM payslip_lines l WHERE l.payslip_id = s.id AND l.code = 'REIMBURSEMENT'), 0) AS reimbursements,
                              s.pf_employee, s.tds
                         FROM payslips s JOIN payroll_runs r ON r.id = s.run_id
                        WHERE s.employee_id = %s AND r.entity_id = %s AND app.fy_of(r.period_month) = %s
                          AND r.status IN ('approved', 'bank_file_sent', 'paid', 'statutory_done') ORDER BY r.period_month""", (employee_id, entity_id, fy))
        slips = cur.fetchall()
        cur.execute("SELECT * FROM tax_declarations WHERE employee_id = %s AND fy = %s", (employee_id, fy))
        d = cur.fetchone()
        items = []
        if d:
            cur.execute("SELECT * FROM tax_declaration_items WHERE declaration_id = %s", (d["id"],))
            items = cur.fetchall()
        cur.execute("SELECT code, annual_cap FROM tax_deduction_sections WHERE fy = %s", (fy,))
        caps = {r["code"]: r["annual_cap"] for r in cur.fetchall()}
        regime = d["regime"] if d and d["status"] in ("submitted", "verified") else (e["tax_regime"] or "new")
        return _form16_out(e["full_name"], fy, regime, slips, items, caps, bool(d and d["status"] == "verified"))
