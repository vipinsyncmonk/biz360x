"""Who does what: the kinds of work a company assigns, the steps each has, and what a preset takes on. Pure, no database.

The matrix has two steps because only two change anything: `prepare` (who owns a filing) and `approve` (who is asked to decide).
Payroll runs, vendor payments and leave have only an approver: whoever starts them is the preparer. The routing itself is in
database triggers (migration 38), so every path that creates work follows the same rule. See docs/ROLES_DESIGN.md, phase 2.
"""
from __future__ import annotations

import datetime as dt

STEPS = ("prepare", "approve")
# key -> (label, what it covers, the steps it has)
KINDS: dict[str, tuple[str, str, tuple[str, ...]]] = {
    "gst": ("GST returns", "GSTR-1, GSTR-3B, the yearly LUT and other GST filings", STEPS),
    "tds": ("TDS", "Monthly deposits and quarterly returns", STEPS),
    "pf_esi": ("PF and ESI", "The monthly ECR and ESI contribution", STEPS),
    "professional_tax": ("Professional tax", "State professional tax payments and returns", STEPS),
    "mca": ("Company filings (MCA)", "AOC-4, MGT-7, MSME-1 and event-based forms", STEPS),
    "income_tax": ("Income tax", "Advance tax and the company's return", STEPS),
    "other_filing": ("Other filings", "Anything the lines above do not cover", STEPS),
    "payroll_run": ("Payroll run", "Approving the month's payroll before it is paid", ("approve",)),
    "vendor_payment": ("Vendor payments", "Approving a payment before the bank is asked to send it", ("approve",)),
    "leave": ("Leave", "Deciding leave requests", ("approve",)),
}
# what each preset takes on when the slot is free; the owner can change every line
PRESET_WORK: dict[str, list[tuple[str, str]]] = {
    "accountant": [("gst", "prepare"), ("tds", "prepare"), ("income_tax", "prepare"), ("other_filing", "prepare")],
    "cfo": [("gst", "approve"), ("tds", "approve"), ("income_tax", "approve"), ("other_filing", "approve"), ("vendor_payment", "approve"), ("payroll_run", "approve")],
    "hr_manager": [("pf_esi", "prepare"), ("professional_tax", "prepare"), ("leave", "approve")],
    "coo": [("mca", "approve")],
}
# to be named the approver of these, a person must be able to approve in the module the decision is made in
APPROVE_MODULE = {"leave": "hr", "payroll_run": "payroll", "vendor_payment": "purchases"}
ITEM_KINDS = ("approval", "filing", "task", "leave", "document_request")


def valid_slot(kind: str, step: str) -> bool:
    return kind in KINDS and step in KINDS[kind][2]


def approve_module(kind: str) -> str:
    """Where the decision is made: the module named above, else approvals (a filing is approved from the approvals inbox)."""
    return APPROVE_MODULE.get(kind, "approvals")


def kinds_out() -> list[dict]:
    return [{"key": k, "label": v[0], "about": v[1], "steps": list(v[2])} for k, v in KINDS.items()]


def preset_slots(preset: str | None, taken: set[tuple[str, str]]) -> list[tuple[str, str]]:
    """The slots a newly invited person fills from their preset: the preset's, less the ones somebody already holds."""
    return [s for s in PRESET_WORK.get(preset or "", []) if s not in taken]


def sort_work(items: list[dict], today: dt.date) -> list[dict]:
    """The queue in the order to work it: overdue first, then by due date, things with no date last; approvals before the rest on a tie,
    because someone else is waiting on them."""
    for i in items:
        i["overdue"] = bool(i.get("due_on")) and i["due_on"] < today
    rank = {k: n for n, k in enumerate(ITEM_KINDS)}
    return sorted(items, key=lambda i: (i["due_on"] is None, i["due_on"] or dt.date.max, rank.get(i["kind"], 9), i["title"]))
