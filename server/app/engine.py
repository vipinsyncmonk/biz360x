"""Compliance engine evaluator: compliance_rules x entity facts -> obligations.

Layers
  1. Pure functions (no database): applicability matching, due-date rules,
     period generation, late-fee maths, candidate evaluation. Unit-tested on
     their own.
  2. Database layer: load an entity's context, upsert obligations, write
     reason_snapshot / obligation_events / engine_runs / audit / events.

The two JSON languages are documented at the top of db/10_seed_reference.sql:
  applicability: {"all"|"any": [ ... ]} of {"fact": k, "op": "="|"!="|">="|">"|"<="|"<"|"in", "value": v}
  due_rule:      day_of_following_month | fixed_dates | days_after_event | per_document

Indian-law figures in the rules are a starter library (CA/CS review required);
the engine only interprets them.
"""
from __future__ import annotations

import calendar
import datetime as dt
import json
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from .core import audit, emit, jsonable

ZERO_UUID = "00000000-0000-0000-0000-000000000000"
SCHEDULED_FREQUENCIES = ("monthly", "quarterly", "half_yearly", "annual")


class RuleError(ValueError):
    """A rule's JSON is malformed. Raised, never swallowed: a bad rule must be loud."""


# ===================================================================== 1. pure logic
# ---------------------------------------------------------------- applicability
def _is_num(v) -> bool:
    return isinstance(v, (int, float, Decimal)) and not isinstance(v, bool)


def _eq(a, b) -> bool:
    if isinstance(a, bool) or isinstance(b, bool):
        return isinstance(a, bool) and isinstance(b, bool) and a == b
    return a == b


def _leaf(node: dict, facts: dict) -> dict:
    key, op, want = node.get("fact"), node.get("op"), node.get("value")
    if not key or op is None or "value" not in node:
        raise RuleError(f"leaf needs fact, op and value: {node!r}")
    if op not in ("=", "!=", ">=", ">", "<=", "<", "in"):
        raise RuleError(f"unknown operator {op!r}")
    if op == "in" and not isinstance(want, list):
        raise RuleError("'in' needs a list value")
    if op in (">=", ">", "<=", "<") and not _is_num(want):
        raise RuleError(f"{op} needs a numeric value, got {want!r}")
    out = {"fact": key, "op": op, "value": want, "actual": None, "missing": key not in facts, "result": False}
    if out["missing"]:
        return out                                  # unknown fact: the clause cannot be satisfied
    actual = facts[key]
    out["actual"] = actual
    if op == "=":
        out["result"] = _eq(actual, want)
    elif op == "!=":
        out["result"] = not _eq(actual, want)
    elif op == "in":
        out["result"] = any(_eq(actual, w) for w in want)
    elif _is_num(actual):
        out["result"] = {">=": actual >= want, ">": actual > want, "<=": actual <= want, "<": actual < want}[op]
    return out


def evaluate_applicability(node: dict, facts: dict) -> tuple[bool, list[dict]]:
    """Returns (applies, clauses). `clauses` are the evaluated leaves, kept for reason_snapshot."""
    if not isinstance(node, dict):
        raise RuleError(f"applicability must be an object, got {node!r}")
    group = [k for k in ("all", "any") if k in node]
    if group and "fact" in node or len(group) > 1:
        raise RuleError(f"ambiguous node: {node!r}")
    if not group:
        leaf = _leaf(node, facts)
        return leaf["result"], [leaf]
    children = node[group[0]]
    if not isinstance(children, list):
        raise RuleError(f"{group[0]!r} needs a list")
    results, clauses = [], []
    for child in children:
        ok, cl = evaluate_applicability(child, facts)
        results.append(ok)
        clauses += cl
    return (all(results) if group[0] == "all" else any(results)), clauses


# --------------------------------------------------------------------- calendar
def fy_start_year(d: dt.date) -> int:
    return d.year if d.month >= 4 else d.year - 1


def fy_label(start_year: int) -> str:
    return f"{start_year}-{(start_year + 1) % 100:02d}"


def _short(start_year: int) -> str:
    return f"FY{start_year % 100:02d}-{(start_year + 1) % 100:02d}"


def _clamp(year: int, month: int, day: int) -> dt.date:
    return dt.date(year, month, min(day, calendar.monthrange(year, month)[1]))


def _add_months(y: int, m: int, n: int) -> tuple[int, int]:
    t = y * 12 + (m - 1) + n
    return t // 12, t % 12 + 1


@dataclass(frozen=True)
class Period:
    start: dt.date
    end: dt.date
    label: str
    fy: str
    index: int          # 0-based position inside the financial year


def periods_for_fy(frequency: str, start_year: int) -> list[Period]:
    fy = fy_label(start_year)
    out: list[Period] = []
    if frequency == "annual":
        out.append(Period(dt.date(start_year, 4, 1), dt.date(start_year + 1, 3, 31), f"FY {fy.replace('-', '–')}", fy, 0))
        return out
    months = {"monthly": 1, "quarterly": 3, "half_yearly": 6}.get(frequency)
    if months is None:
        raise RuleError(f"frequency {frequency!r} has no schedule")
    prefix = {"monthly": "", "quarterly": "Q", "half_yearly": "H"}[frequency]
    y, m = start_year, 4
    for i in range(12 // months):
        ey, em = _add_months(y, m, months - 1)
        start, end = dt.date(y, m, 1), _clamp(ey, em, 31)
        label = start.strftime("%b %Y") if frequency == "monthly" else f"{prefix}{i + 1} {_short(start_year)}"
        out.append(Period(start, end, label, fy, i))
        y, m = _add_months(y, m, months)
    return out


# --------------------------------------------------------------------- due dates
def _first_on_or_after(start: dt.date, mm_dd: str) -> dt.date:
    try:
        mm, dd = (int(x) for x in mm_dd.split("-"))
    except ValueError:
        raise RuleError(f"bad MM-DD {mm_dd!r}")
    d = _clamp(start.year, mm, dd)
    return d if d >= start else _clamp(start.year + 1, mm, dd)


def due_date(rule: dict, period: Period, events: dict[str, list[dt.date]]) -> tuple[dt.date, bool] | None:
    """Returns (due_date, provisional) or None when the rule has no scheduled due date."""
    kind = rule.get("type")
    if kind == "per_document":
        return None
    if kind == "day_of_following_month":
        offset, day = rule.get("month_offset", 1), rule.get("day")
        exc = (rule.get("exceptions") or {}).get(f"{period.end.month:02d}")
        if exc:
            offset, day = exc.get("month_offset", offset), exc.get("day", day)
        if not isinstance(day, int):
            raise RuleError("day_of_following_month needs an integer day")
        y, m = _add_months(period.end.year, period.end.month, offset)
        return _clamp(y, m, day), False
    if kind == "fixed_dates":
        dates = list((rule.get("dates") or {}).values())
        if not dates:
            raise RuleError("fixed_dates needs dates")
        if period.index >= len(dates):
            raise RuleError(f"fixed_dates has {len(dates)} entries but the period is #{period.index + 1}")
        return _first_on_or_after(period.start, dates[period.index]), False
    if kind == "days_after_event":
        name, days = rule.get("event"), rule.get("days")
        if not name or not isinstance(days, int):
            raise RuleError("days_after_event needs event and days")
        held = sorted(d for d in events.get(name, []) if period.end < d <= period.end + dt.timedelta(days=366))
        if held:
            return held[-1] + dt.timedelta(days=days), False
        if name == "agm":                                   # Companies Act s.96: AGM within 6 months of year end
            return _add_months_date(period.end, 6) + dt.timedelta(days=days), True
        return None
    raise RuleError(f"unknown due_rule type {kind!r}")


def _add_months_date(d: dt.date, n: int) -> dt.date:
    y, m = _add_months(d.year, d.month, n)
    return _clamp(y, m, d.day)


# --------------------------------------------------------------------- late fees
def late_fee(penalty: dict | None, due: dt.date, until: dt.date, facts: dict) -> tuple[Decimal, str | None]:
    """Per-day late fee between `due` and `until`. Returns (amount, note). Interest needs a tax
    base the engine does not know yet, so it is not accrued here."""
    if not penalty or until <= due:
        return Decimal(0), None
    per_day = penalty.get("late_fee_per_day", penalty.get("additional_fee_per_day"))
    if per_day is None:
        return Decimal(0), None
    amount = Decimal(str(per_day)) * (until - due).days
    cap = penalty.get("cap")
    if "cap_by_aato" in penalty:
        aato = facts.get("aato")
        if aato is None:
            return amount, "cap by turnover unknown: aato fact missing"
        for limit, c in penalty["cap_by_aato"]:
            if limit is None or Decimal(str(aato)) <= Decimal(str(limit)):
                cap = c
                break
    if cap is not None:
        if _is_num(cap):
            amount = min(amount, Decimal(str(cap)))
        else:
            return amount, f"cap '{cap}' needs data the engine does not hold; shown uncapped"
    return amount, None


# --------------------------------------------------------------- candidate build
@dataclass
class Reg:
    id: str | None
    kind: str
    number: str | None = None
    label: str | None = None
    state_code: str | None = None
    gst_filing_frequency: str | None = None
    valid_from: dt.date | None = None
    valid_until: dt.date | None = None


@dataclass
class Context:
    facts: dict[str, Any]
    registrations: list[Reg] = field(default_factory=list)
    events: dict[str, list[dt.date]] = field(default_factory=dict)
    incorporated_on: dt.date | None = None


@dataclass
class Candidate:
    rule: dict
    reg: Reg | None
    period: Period
    due: dt.date
    provisional: bool
    applies: bool
    reason: dict


def derived_facts(facts: dict, regs: list[Reg]) -> dict:
    """Facts implied by what the entity is registered for. Explicit facts win."""
    out: dict[str, Any] = {}
    for r in regs:
        out[f"has_{r.kind}"] = True
        if r.kind == "professional_tax" and r.state_code:
            out[f"employs_in_state_{r.state_code}"] = True
    out.update(facts)
    return out


def rule_in_force(rule: dict, on: dt.date) -> bool:
    eff = rule["effective"]
    lo, hi = eff.lower, eff.upper
    return (lo is None or lo <= on) and (hi is None or on < hi)


def evaluate_rules(rules: list[dict], ctx: Context, as_of: dt.date, *, horizon_days: int = 180,
                   lookback_days: int = 45) -> tuple[list[Candidate], int]:
    """All (rule, registration, period) instances that fall in the window, applicable or not.
    Returns (candidates, number_of_rules_evaluated)."""
    base_facts = derived_facts(ctx.facts, ctx.registrations)
    first_year = fy_start_year(as_of) - 1
    out: list[Candidate] = []
    evaluated = set()
    for rule in rules:
        if rule["frequency"] not in SCHEDULED_FREQUENCIES:
            continue                                            # per-event / per-document / one-time: no schedule
        scopes: list[Reg | None]
        if rule["per_registration"]:
            scopes = [r for r in ctx.registrations if r.kind == rule["per_registration"]]
        else:
            scopes = [None]
        for year in range(first_year, first_year + 3):
            for period in periods_for_fy(rule["frequency"], year):
                if not rule_in_force(rule, period.end):
                    continue
                if ctx.incorporated_on and period.end < ctx.incorporated_on:
                    continue
                evaluated.add(rule["id"])
                try:
                    res = due_date(rule["due_rule"], period, ctx.events)
                except RuleError as e:
                    raise RuleError(f"{rule['code']} v{rule['version']}: {e}") from e
                if res is None:
                    continue
                due, provisional = res
                for reg in scopes:
                    if reg and ((reg.valid_from and period.end < reg.valid_from) or (reg.valid_until and period.start > reg.valid_until)):
                        continue
                    facts = dict(base_facts)
                    if reg and reg.gst_filing_frequency:
                        facts["gst_frequency"] = reg.gst_filing_frequency
                    try:
                        applies, clauses = evaluate_applicability(rule["applicability"], facts)
                    except RuleError as e:
                        raise RuleError(f"{rule['code']} v{rule['version']}: {e}") from e
                    reason = {"rule": {"code": rule["code"], "version": rule["version"], "statute_ref": rule["statute_ref"]},
                              "applicability": clauses, "due_rule": rule["due_rule"]}
                    if provisional:
                        reason["provisional"] = "due date assumes the event happens at its latest legal date"
                    if reg:
                        reason["registration"] = {"kind": reg.kind, "number": reg.number}
                    out.append(Candidate(rule, reg, period, due, provisional, applies, reason))
    return out, len(evaluated)


def in_create_window(due: dt.date, as_of: dt.date, horizon_days: int, lookback_days: int) -> bool:
    return as_of - dt.timedelta(days=lookback_days) <= due <= as_of + dt.timedelta(days=horizon_days)


# ================================================================ 2. database layer
def _facts_from_rows(rows) -> dict:
    out = {}
    for r in rows:
        v = r["value_num"] if r["value_num"] is not None else r["value_text"] if r["value_text"] is not None else r["value_bool"]
        out[r["fact_key"]] = v
    return out


def load_rules(cur) -> list[dict]:
    cur.execute("SELECT * FROM compliance_rules ORDER BY code, version")
    return cur.fetchall()


def load_context(cur, entity_id) -> Context:
    cur.execute("SELECT constitution::text, track::text, udyam_class::text, incorporated_on FROM entities WHERE id = %s", (entity_id,))
    e = cur.fetchone()
    cur.execute("SELECT fact_key, value_num, value_text, value_bool FROM entity_facts_current WHERE entity_id = %s", (entity_id,))
    facts = _facts_from_rows(cur.fetchall())
    for k in ("constitution", "track", "udyam_class"):
        if e[k] is not None:
            facts.setdefault(k, e[k])
    cur.execute("""SELECT id::text, kind::text, number, label, state_code, gst_filing_frequency, valid_from, valid_until
                     FROM entity_registrations WHERE entity_id = %s AND status = 'active' ORDER BY kind, number""", (entity_id,))
    regs = [Reg(**r) for r in cur.fetchall()]
    cur.execute("SELECT held_on FROM meetings WHERE entity_id = %s AND kind = 'agm' AND held_on IS NOT NULL", (entity_id,))
    return Context(facts, regs, {"agm": [r["held_on"] for r in cur.fetchall()]}, e["incorporated_on"])


def _same(a, b) -> bool:
    return json.dumps(a, sort_keys=True, default=jsonable) == json.dumps(b, sort_keys=True, default=jsonable)


def _event(cur, oid, kind, from_status=None, to_status=None, note=None):
    cur.execute("""INSERT INTO obligation_events (obligation_id, actor_user_id, kind, from_status, to_status, note)
                   VALUES (%s, NULL, %s, %s::obligation_status, %s::obligation_status, %s)""", (oid, kind, from_status, to_status, note))


def _failing(reason: dict) -> str:
    bad = [c for c in reason["applicability"] if not c["result"]]
    return "; ".join(f"{c['fact']} {c['op']} {c['value']} (is {c['actual'] if not c['missing'] else 'unknown'})" for c in bad) or "no clause matched"


def run_engine(cur, entity_id, trigger: str, as_of: dt.date | None = None, *, horizon_days: int = 180,
               lookback_days: int = 45) -> dict:
    """Evaluate every rule for one entity and bring `obligations` in line. Idempotent."""
    as_of = as_of or dt.date.today()
    cur.execute("INSERT INTO engine_runs (entity_id, trigger) VALUES (%s, %s) RETURNING id", (entity_id, trigger))
    run_id = cur.fetchone()["id"]
    ctx = load_context(cur, entity_id)
    cands, n_rules = evaluate_rules(load_rules(cur), ctx, as_of, horizon_days=horizon_days, lookback_days=lookback_days)
    created = changed = 0
    newly_overdue: list = []
    for c in cands:
        reg_id = c.reg.id if c.reg else None
        cur.execute("""SELECT o.id, o.rule_id, o.due_date, o.extended_due_date, o.status::text, o.reason_snapshot, o.late_fee_accrued,
                              o.filed_at
                         FROM obligations o JOIN compliance_rules r ON r.id = o.rule_id
                        WHERE o.entity_id = %s AND r.code = %s AND o.period_start = %s
                          AND coalesce(o.registration_id, %s::uuid) = coalesce(%s::uuid, %s::uuid)
                        FOR UPDATE OF o""", (entity_id, c.rule["code"], c.period.start, ZERO_UUID, reg_id, ZERO_UUID))
        ex = cur.fetchone()
        eff_due = (ex["extended_due_date"] or c.due) if ex else c.due
        if not ex:
            if not c.applies or not in_create_window(c.due, as_of, horizon_days, lookback_days):
                continue
            status = "overdue" if eff_due < as_of else "upcoming"
            fee, _ = late_fee(c.rule["penalty_rule"], eff_due, as_of, ctx.facts)
            cur.execute("""INSERT INTO obligations (entity_id, rule_id, registration_id, fy, period_start, period_end, period_label,
                                                    due_date, status, late_fee_accrued, reason_snapshot, engine_run_id)
                           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id""",
                        (entity_id, c.rule["id"], reg_id, c.period.fy, c.period.start, c.period.end, c.period.label, c.due,
                         status, fee, json.dumps(c.reason, default=jsonable), run_id))
            oid = cur.fetchone()["id"]
            _event(cur, oid, "created", None, status, f"{c.rule['code']} v{c.rule['version']}")
            emit(cur, entity_id, "obligation.created", "obligations", oid, {"rule": c.rule["code"], "period": c.period.label})
            created += 1
            if status == "overdue":
                newly_overdue.append(oid)
            continue
        if ex["status"] in ("filed", "waived"):
            continue                                            # history is never rewritten
        updates: dict[str, Any] = {}
        status = ex["status"]
        if not c.applies:
            if status in ("upcoming", "overdue"):
                updates["status"] = "not_applicable"
                _event(cur, ex["id"], "status_changed", status, "not_applicable", "No longer applies: " + _failing(c.reason))
        else:
            if status == "not_applicable":
                status = updates["status"] = "overdue" if eff_due < as_of else "upcoming"
                _event(cur, ex["id"], "status_changed", "not_applicable", status, "Applies again")
            if ex["rule_id"] != c.rule["id"]:
                updates["rule_id"] = c.rule["id"]
            if ex["due_date"] != c.due:
                updates["due_date"] = c.due
                _event(cur, ex["id"], "due_changed", None, None, f"{ex['due_date']} -> {c.due} ({c.rule['code']} v{c.rule['version']})")
                eff_due = ex["extended_due_date"] or c.due
            if not _same(ex["reason_snapshot"], c.reason):
                updates["reason_snapshot"] = json.dumps(c.reason, default=jsonable)
            if status == "upcoming" and eff_due < as_of:
                updates["status"] = "overdue"
                _event(cur, ex["id"], "status_changed", "upcoming", "overdue", "Passed its due date unfiled")
                newly_overdue.append(ex["id"])
            elif status == "overdue" and eff_due >= as_of:
                updates["status"] = "upcoming"
                _event(cur, ex["id"], "status_changed", "overdue", "upcoming", "Due date moved out")
            fee, _ = late_fee(c.rule["penalty_rule"], eff_due, as_of, ctx.facts)
            if fee != ex["late_fee_accrued"]:
                updates["late_fee_accrued"] = fee
        if updates:
            cols = ", ".join(f"{k} = %s" + ("::obligation_status" if k == "status" else "") for k in updates)
            cur.execute(f"UPDATE obligations SET {cols}, engine_run_id = %s WHERE id = %s", [*updates.values(), run_id, ex["id"]])
            changed += 1
    for oid in newly_overdue:
        emit(cur, entity_id, "obligation.overdue", "obligations", oid, {})
    alerts = check_thresholds(cur, entity_id, ctx.facts)
    cur.execute("""UPDATE engine_runs SET finished_at = now(), rules_evaluated = %s, obligations_created = %s, obligations_changed = %s
                    WHERE id = %s RETURNING *""", (n_rules, created, changed, run_id))
    run = cur.fetchone()
    audit(cur, entity_id, "engine.run", "engine_runs", run_id, actor_kind="user" if trigger in ("manual", "onboarding") else "engine",
          after={"trigger": trigger, "created": created, "changed": changed, "threshold_alerts": alerts})
    return run


# ---------------------------------------------------------------- thresholds
def threshold_rows(cur, entity_id, facts: dict | None = None) -> list[dict]:
    if facts is None:
        facts = load_context(cur, entity_id).facts
    cur.execute("""SELECT w.rule_code, coalesce(r.title, w.rule_code) AS title, w.fact_key, w.threshold, w.direction,
                          w.alert_at_pct, w.note
                     FROM threshold_watches w
                     LEFT JOIN LATERAL (SELECT title FROM compliance_rules WHERE code = w.rule_code ORDER BY version DESC LIMIT 1) r ON true
                    WHERE w.entity_id = %s ORDER BY w.rule_code""", (entity_id,))
    out = []
    for w in cur.fetchall():
        cur_v = facts.get(w["fact_key"])
        cur_v = Decimal(str(cur_v)) if _is_num(cur_v) else None
        pct = None
        if cur_v is not None and w["threshold"]:
            pct = (cur_v / w["threshold"] * 100) if w["direction"] == "above" else (w["threshold"] / cur_v * 100 if cur_v else None)
        crossed = cur_v is not None and (cur_v > w["threshold"] if w["direction"] == "above" else cur_v < w["threshold"])
        out.append({**w, "current_value": cur_v, "pct": None if pct is None else round(pct, 1), "crossed": crossed,
                    "alert": pct is not None and pct >= w["alert_at_pct"]})
    return out


def check_thresholds(cur, entity_id, facts: dict) -> list[str]:
    """Create one open task per watch that has reached its alert level and emit threshold.crossed."""
    raised = []
    for w in threshold_rows(cur, entity_id, facts):
        if not w["alert"]:
            continue
        title = f"{w['title']}: {w['fact_key']} at {w['pct']}% of {w['threshold']}" + (" - threshold crossed" if w["crossed"] else "")
        cur.execute("SELECT 1 FROM tasks WHERE entity_id = %s AND source = 'engine' AND status = 'open' AND title LIKE %s",
                    (entity_id, f"{w['title']}: {w['fact_key']} at %"))
        if cur.fetchone():
            continue
        cur.execute("INSERT INTO tasks (entity_id, title, source) VALUES (%s, %s, 'engine')", (entity_id, title))
        emit(cur, entity_id, "threshold.crossed", "entities", entity_id, {"rule_code": w["rule_code"], "fact_key": w["fact_key"],
                                                                         "pct": w["pct"], "crossed": w["crossed"]})
        raised.append(w["rule_code"])
    return raised


# ------------------------------------------------------------------- dry run
BAND_HEADCOUNT = {"1-9": 5, "10-19": 15, "20-49": 30, "50+": 60}
BAND_TURNOVER = {"<1.5cr": Decimal(10_000_000), "1.5-5cr": Decimal(30_000_000), "5-10cr": Decimal(75_000_000), ">10cr": Decimal(150_000_000)}
# gst_state_code for the registered office is not part of the answers, so PT is derived from the GSTIN states
_PT_STATES = {"29"}


def preview(cur, answers: dict, as_of: dt.date | None = None, *, constitution: str = "private_limited",
            incorporated_on: dt.date | None = None) -> dict:
    """Evaluate the rules for onboarding answers without writing anything."""
    as_of = as_of or dt.date.today()
    facts: dict[str, Any] = {"constitution": constitution, "track": answers.get("track", "business")}
    if answers.get("headcount_band"):
        facts["headcount"] = BAND_HEADCOUNT[answers["headcount_band"]]
    if answers.get("turnover_band"):
        facts["aato"] = BAND_TURNOVER[answers["turnover_band"]]
    facts["has_foreign_investor"] = bool(answers.get("has_foreign_shareholder"))
    if answers.get("buys_from_mse") in ("yes", "no"):
        facts["has_mse_dues_over_45_days"] = answers["buys_from_mse"] == "yes"
    regs = [Reg(None, "gstin", g, None, g[:2], "monthly") for g in answers.get("gstins", [])]
    regs += [Reg(None, k, None, None, None, None) for k in ("tan", "epf", "esic")]       # duties are gated by applicability, the registration is a to-do
    for state in sorted({g[:2] for g in answers.get("gstins", [])} & _PT_STATES):
        regs.append(Reg(None, "professional_tax", None, None, state, None))
    ctx = Context(facts, regs, {}, incorporated_on)
    cands, _ = evaluate_rules(load_rules(cur), ctx, as_of)
    live = [c for c in cands if c.applies and in_create_window(c.due, as_of, 180, 45)]
    # the graph counts distinct duties (rule x registration), not every monthly instance
    seen: set = set()
    by_auth: dict[str, int] = {}
    codes = {}
    for c in cands:
        key = (c.rule["code"], c.reg.number if c.reg else None)
        if c.applies and key not in seen:
            seen.add(key)
            by_auth[c.rule["authority_code"]] = by_auth.get(c.rule["authority_code"], 0) + 1
            codes[c.rule["code"]] = c.rule
    triggers = [{"why": _why(r), "what": r["form_code"] or r["title"]} for r in codes.values()]
    first = sorted(live, key=lambda c: c.due)[:5]
    return {"total": sum(by_auth.values()),
            "by_authority": [{"authority": a, "count": n} for a, n in sorted(by_auth.items())],
            "triggers": triggers,
            "first_due": [{"title": c.rule["title"], "authority": c.rule["authority_code"], "due_date": c.due} for c in first]}


def _why(rule: dict) -> str:
    def walk(n):
        if "fact" in n:
            return [f"{n['fact']} {n['op']} {n['value']}"]
        return [x for ch in n.get("all", n.get("any", [])) for x in walk(ch)]
    return ", ".join(walk(rule["applicability"]))
