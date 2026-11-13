"""Who may do what: the modules, the scale of levels, and the presets a person is invited with. Pure, no database.

A member has a level for each module (`scopes`), on one scale:

    read < comment < write < approve < file          "*" stands for every module not named

A title (CEO, CFO, Accountant) is not a permission. It is a **preset**: a named starting point that fills the scopes and the home
screen, which the owner can change line by line. Practitioners (a CA firm, a consultant) are granted the same modules through
advisor grants, in their own words (read, comment, prepare, certify); their presets are here too so the grant form can offer them.
See docs/ROLES_DESIGN.md.
"""
from __future__ import annotations

MODULES = ("settings", "compliance", "approvals", "tasks", "trade", "parties", "sales", "purchases", "inventory", "gst", "tds", "finance", "payroll", "hr",
           "legal", "capital", "schemes", "documents", "copilot", "audit")
MODULE_LABELS = {"settings": "Settings", "compliance": "Compliance", "approvals": "Approvals", "tasks": "Tasks", "trade": "Exports and imports", "parties": "Customers, vendors and items",
                 "sales": "Sales and billing", "purchases": "Purchases", "inventory": "Inventory", "gst": "GST", "tds": "TDS and advance tax", "finance": "Finance and books",
                 "payroll": "Payroll and salaries", "hr": "HR: onboarding, leave and policies", "legal": "Legal, contracts and board", "capital": "Cap table and funding",
                 "schemes": "Schemes and credit", "documents": "Document vault", "copilot": "Copilot", "audit": "Audit log"}
LEVELS = ("read", "comment", "write", "approve", "file")
RANK = {level: i for i, level in enumerate(LEVELS, 1)}
# what each level lets a member do; `use` is the copilot and similar tools, `pay` goes with `file`
LEVEL_ACTIONS = {"read": {"read"}, "comment": {"read"}, "write": {"read", "write", "use"}, "approve": {"read", "write", "use", "approve"},
                 "file": {"read", "write", "use", "approve", "file", "pay"}}
HOMES = ("leadership", "finance", "people", "general")
SHARED = ("approvals", "tasks")                       # everyone works with these at their highest level; they are never granted on their own


class ScopeError(ValueError):
    pass


def level_for(scopes: dict, module: str) -> str | None:
    """The level a member holds on a module: the one named for it, else the one for `*`. Approvals and tasks follow the member's
    highest level, as they always have."""
    if module in SHARED:
        return top_level(scopes)
    return scopes.get(module) or scopes.get("*")


def top_level(scopes: dict) -> str | None:
    return max(scopes.values(), key=lambda v: RANK.get(v, 0), default=None)


def allows(scopes: dict, module: str, action: str) -> bool:
    level = level_for(scopes, module)
    return bool(level) and action in LEVEL_ACTIONS.get(level, set())


def clean_scopes(scopes: dict) -> dict:
    """Check a scopes object: known modules (or `*`), known levels, at least one. Returns it with a module that only repeats the `*`
    level dropped, so two ways of saying the same thing are stored the same way."""
    if not isinstance(scopes, dict) or not scopes:
        raise ScopeError("Give at least one module")
    out = {}
    for module, level in scopes.items():
        if module != "*" and module not in MODULES:
            raise ScopeError(f"'{module}' is not a module")
        if module in SHARED:
            raise ScopeError(f"'{module}' follows the person's highest level and is not granted on its own")
        if level not in LEVELS:
            raise ScopeError(f"'{level}' is not a level; use read, comment, write, approve or file")
        out[module] = level
    star = out.get("*")
    return {m: l for m, l in out.items() if m == "*" or l != star}


def role_for(scopes: dict, owner: bool = False) -> str:
    """The one-word level the rest of the schema reads: owner, or the highest level the scopes hold."""
    if owner:
        return "owner"
    top = RANK.get(top_level(scopes) or "", 0)
    return "approver" if top >= RANK["approve"] else "maker" if top == RANK["write"] else "viewer"


def default_home(scopes: dict, owner: bool = False) -> str:
    """Where a member lands when nobody chose: leadership for an owner, finance or people for someone who prepares that work."""
    if owner:
        return "leadership"
    writes = lambda m: RANK.get(level_for(scopes, m) or "", 0) >= RANK["write"]
    if writes("finance"):
        return "finance"
    if writes("hr") or writes("payroll"):
        return "people"
    return "general"


# key -> preset. `kind` member: scopes on the scale above. `kind` practitioner: an advisor grant's scopes (read, comment, prepare, certify).
PRESETS: dict[str, dict] = {
    "ceo": {"kind": "member", "title": "Founder / CEO", "owner": True, "home": "leadership", "scopes": {"*": "file"},
            "about": "Everything, including managing people and settings."},
    "coo": {"kind": "member", "title": "COO", "owner": False, "home": "leadership",
            "scopes": {"compliance": "approve", "sales": "approve", "purchases": "approve", "inventory": "approve", "trade": "approve", "parties": "approve", "hr": "approve",
                       "legal": "approve", "documents": "approve", "finance": "read", "payroll": "read", "gst": "read", "tds": "read", "copilot": "write"},
            "about": "Runs operations: approves sales, purchases, stock, trade, HR and contracts; reads the books and payroll."},
    "cfo": {"kind": "member", "title": "CFO", "owner": False, "home": "finance",
            "scopes": {"finance": "file", "gst": "file", "tds": "file", "purchases": "file", "sales": "file", "trade": "file", "inventory": "file", "parties": "file", "capital": "file",
                       "payroll": "approve", "compliance": "read", "documents": "write", "legal": "read", "hr": "read", "schemes": "write", "audit": "read", "copilot": "write"},
            "about": "Owns the books and the tax filings, approves payroll and payments."},
    "accountant": {"kind": "member", "title": "Accountant", "owner": False, "home": "finance",
                   "scopes": {"finance": "write", "sales": "write", "purchases": "write", "parties": "write", "inventory": "write", "gst": "write", "tds": "write", "trade": "write",
                              "documents": "write", "compliance": "read", "copilot": "write"},
                   "about": "Prepares invoices, bills, bank entries and returns. Does not approve, file or see payroll."},
    "hr_manager": {"kind": "member", "title": "HR Manager", "owner": False, "home": "people",
                   "scopes": {"hr": "approve", "payroll": "write", "documents": "write", "compliance": "read", "copilot": "write"},
                   "about": "Onboards people, decides leave, keeps policies, and prepares the payroll run for someone else to approve."},
    "hr_executive": {"kind": "member", "title": "HR Executive", "owner": False, "home": "people", "scopes": {"hr": "write", "documents": "read"},
                     "about": "Records leave, runs onboarding checklists and policies. Does not see salaries."},
    "viewer": {"kind": "member", "title": "Observer", "owner": False, "home": "general", "scopes": {"*": "read"},
               "about": "Reads everything, changes nothing: an investor or a board observer."},
    "chartered_accountant": {"kind": "practitioner", "title": "Chartered accountant", "advisor_kind": "chartered_accountant",
                             "scopes": {"finance": "certify", "gst": "certify", "tds": "certify", "sales": "read", "purchases": "read", "inventory": "read", "payroll": "read", "documents": "read"},
                             "about": "Keeps or reviews the books and files GST and TDS."},
    "company_secretary": {"kind": "practitioner", "title": "Company secretary", "advisor_kind": "company_secretary",
                          "scopes": {"legal": "certify", "capital": "certify", "compliance": "certify", "documents": "prepare"},
                          "about": "Board and shareholder records, MCA filings, the cap table."},
    "gst_consultant": {"kind": "practitioner", "title": "GST consultant", "advisor_kind": "chartered_accountant",
                       "scopes": {"gst": "certify", "sales": "read", "purchases": "read", "parties": "read"}, "about": "Prepares and files GST returns from the sales and purchase registers."},
    "pf_esi_consultant": {"kind": "practitioner", "title": "PF / ESI consultant", "advisor_kind": "chartered_accountant",
                          "scopes": {"payroll": "read", "compliance": "certify"}, "about": "Files PF and ESI from the payroll run. Sees wages, because the work needs them."},
    "tds_consultant": {"kind": "practitioner", "title": "TDS consultant", "advisor_kind": "chartered_accountant",
                       "scopes": {"tds": "certify", "purchases": "read", "payroll": "read"}, "about": "Deposits and returns for TDS on bills and salaries."},
    "trade_consultant": {"kind": "practitioner", "title": "Export–import consultant", "advisor_kind": "chartered_accountant",
                         "scopes": {"trade": "prepare", "sales": "read", "purchases": "read"}, "about": "Shipping bills, bills of entry, LUT and export incentives."},
    "statutory_auditor": {"kind": "practitioner", "title": "Statutory auditor", "advisor_kind": "auditor",
                          "scopes": {"finance": "comment", "sales": "read", "purchases": "read", "inventory": "read", "gst": "read", "tds": "read", "payroll": "read", "legal": "read",
                                     "capital": "read", "documents": "read", "audit": "read"},
                          "about": "Reads the books and records and raises queries. Never prepares or files."},
}


def presets_out() -> list[dict]:
    return [{"key": k, "kind": p["kind"], "title": p["title"], "about": p["about"], "owner": p.get("owner", False), "home": p.get("home"),
             "advisor_kind": p.get("advisor_kind"), "scopes": p["scopes"]} for k, p in PRESETS.items()]
