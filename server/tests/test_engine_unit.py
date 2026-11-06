"""Compliance engine: pure-function tests (no database)."""
import datetime as dt
from decimal import Decimal

import pytest
from psycopg.types.range import Range

from app import engine as eng

D = dt.date


# ------------------------------------------------------------------ applicability
def ok(node, facts):
    return eng.evaluate_applicability(node, facts)[0]


def leaf(fact, op, value):
    return {"fact": fact, "op": op, "value": value}


@pytest.mark.parametrize("op,value,actual,expected", [
    ("=", 20, 20, True), ("=", 20, 21, False), ("!=", 20, 21, True), ("!=", 20, 20, False),
    (">=", 20, 20, True), (">=", 20, 19, False), (">", 20, 20, False), (">", 20, 21, True),
    ("<=", 20, 20, True), ("<=", 20, 21, False), ("<", 20, 19, True), ("<", 20, 20, False),
])
def test_numeric_operators(op, value, actual, expected):
    assert ok(leaf("headcount", op, value), {"headcount": actual}) is expected
    assert ok(leaf("headcount", op, value), {"headcount": Decimal(actual)}) is expected      # facts arrive as Decimal


def test_in_and_text_and_bool():
    assert ok(leaf("constitution", "in", ["private_limited", "opc"]), {"constitution": "opc"})
    assert not ok(leaf("constitution", "in", ["private_limited"]), {"constitution": "llp"})
    assert ok(leaf("has_tan", "=", True), {"has_tan": True})
    assert not ok(leaf("has_tan", "=", True), {"has_tan": False})


def test_bool_is_not_a_number():
    assert not ok(leaf("has_tan", "=", 1), {"has_tan": True})          # True == 1 in Python; here it must not match
    assert not ok(leaf("headcount", ">=", 1), {"headcount": True})


def test_missing_fact_is_false_and_flagged():
    applies, clauses = eng.evaluate_applicability(leaf("headcount", ">=", 20), {})
    assert applies is False and clauses[0]["missing"] is True
    assert ok(leaf("headcount", "!=", 20), {}) is False                # unknown is not "different"


def test_groups_and_nesting():
    node = {"all": [leaf("a", "=", 1), {"any": [leaf("b", "=", 1), leaf("c", "=", 1)]}]}
    assert ok(node, {"a": 1, "c": 1})
    assert not ok(node, {"a": 1})
    assert not ok(node, {"b": 1, "c": 1})
    assert ok({"all": []}, {}) is True and ok({"any": []}, {}) is False


def test_clauses_are_kept_for_the_reason_snapshot():
    _, clauses = eng.evaluate_applicability({"all": [leaf("headcount", ">=", 20), leaf("has_tan", "=", True)]},
                                            {"headcount": 38, "has_tan": True})
    assert [(c["fact"], c["actual"], c["result"]) for c in clauses] == [("headcount", 38, True), ("has_tan", True, True)]


@pytest.mark.parametrize("bad", [
    {"fact": "x", "op": "~", "value": 1}, {"fact": "x", "op": "in", "value": 3}, {"fact": "x", "op": ">", "value": "a"},
    {"fact": "x", "op": "="}, {"all": 3}, {"all": [], "any": []}, [], {"fact": "x", "all": []},
])
def test_malformed_rules_raise(bad):
    with pytest.raises(eng.RuleError):
        eng.evaluate_applicability(bad, {"x": 1})


# ------------------------------------------------------------------ periods
def test_monthly_periods_follow_the_indian_fy():
    ps = eng.periods_for_fy("monthly", 2026)
    assert len(ps) == 12 and ps[0].start == D(2026, 4, 1) and ps[-1].end == D(2027, 3, 31)
    assert ps[0].label == "Apr 2026" and ps[9].label == "Jan 2027" and {p.fy for p in ps} == {"2026-27"}
    assert ps[1].end == D(2026, 5, 31) and ps[10].end == D(2027, 2, 28)


def test_quarterly_half_yearly_annual():
    q = eng.periods_for_fy("quarterly", 2026)
    assert [p.label for p in q] == ["Q1 FY26-27", "Q2 FY26-27", "Q3 FY26-27", "Q4 FY26-27"]
    assert q[2].start == D(2026, 10, 1) and q[2].end == D(2026, 12, 31)
    h = eng.periods_for_fy("half_yearly", 2026)
    assert [(p.start, p.end) for p in h] == [(D(2026, 4, 1), D(2026, 9, 30)), (D(2026, 10, 1), D(2027, 3, 31))]
    (a,) = eng.periods_for_fy("annual", 2025)
    assert a.fy == "2025-26" and a.start == D(2025, 4, 1) and a.end == D(2026, 3, 31)
    with pytest.raises(eng.RuleError):
        eng.periods_for_fy("per_document", 2026)


def test_fy_helpers():
    assert eng.fy_start_year(D(2027, 3, 31)) == 2026 and eng.fy_start_year(D(2027, 4, 1)) == 2027
    assert eng.fy_label(2099) == "2099-00"


# ------------------------------------------------------------------ due dates
def due(rule, period, events=None):
    return eng.due_date(rule, period, events or {})


def month(y, m):
    return next(p for p in eng.periods_for_fy("monthly", y if m >= 4 else y - 1) if p.start.month == m and p.start.year == y)


def test_day_of_following_month():
    r = {"type": "day_of_following_month", "day": 11}
    assert due(r, month(2026, 10)) == (D(2026, 11, 11), False)
    assert due(r, month(2026, 12)) == (D(2027, 1, 11), False)          # rolls into the next calendar year
    assert due({"type": "day_of_following_month", "day": 30}, month(2027, 1)) == (D(2027, 2, 28), False)   # clamps to month end


def test_tds_march_exception():
    r = {"type": "day_of_following_month", "day": 7, "exceptions": {"03": {"month_offset": 1, "day": 30}}}
    assert due(r, month(2027, 2))[0] == D(2027, 3, 7)
    assert due(r, month(2027, 3))[0] == D(2027, 4, 30)


def test_fixed_dates_quarterly_returns():
    r = {"type": "fixed_dates", "dates": {"Q1": "07-31", "Q2": "10-31", "Q3": "01-31", "Q4": "05-31"}}
    got = [due(r, p)[0] for p in eng.periods_for_fy("quarterly", 2026)]
    assert got == [D(2026, 7, 31), D(2026, 10, 31), D(2027, 1, 31), D(2027, 5, 31)]


def test_fixed_dates_advance_tax_falls_inside_the_period():
    r = {"type": "fixed_dates", "dates": {"I1": "06-15", "I2": "09-15", "I3": "12-15", "I4": "03-15"}}
    got = [due(r, p)[0] for p in eng.periods_for_fy("quarterly", 2026)]
    assert got == [D(2026, 6, 15), D(2026, 9, 15), D(2026, 12, 15), D(2027, 3, 15)]


def test_fixed_dates_half_yearly():
    r = {"type": "fixed_dates", "dates": {"H1": "10-31", "H2": "04-30"}}
    assert [due(r, p)[0] for p in eng.periods_for_fy("half_yearly", 2026)] == [D(2026, 10, 31), D(2027, 4, 30)]


def test_days_after_event_uses_the_agm_when_held():
    (fy2526,) = eng.periods_for_fy("annual", 2025)
    r = {"type": "days_after_event", "event": "agm", "days": 30}
    assert due(r, fy2526, {"agm": [D(2026, 9, 30)]}) == (D(2026, 10, 30), False)
    # an AGM for a different year is ignored
    assert due(r, fy2526, {"agm": [D(2025, 9, 30)]})[1] is True


def test_days_after_event_without_agm_is_provisional_at_the_legal_latest():
    (fy2526,) = eng.periods_for_fy("annual", 2025)
    assert due({"type": "days_after_event", "event": "agm", "days": 60}, fy2526) == (D(2026, 11, 29), True)
    assert due({"type": "days_after_event", "event": "board_meeting", "days": 5}, fy2526) is None


def test_per_document_has_no_schedule_and_unknown_types_raise():
    assert due({"type": "per_document"}, month(2026, 10)) is None
    with pytest.raises(eng.RuleError):
        due({"type": "whenever"}, month(2026, 10))
    with pytest.raises(eng.RuleError):
        due({"type": "fixed_dates", "dates": {"Q1": "07-31"}}, eng.periods_for_fy("quarterly", 2026)[2])


# ------------------------------------------------------------------ late fees
GST = {"late_fee_per_day": 50, "cap_by_aato": [[15000000, 2000], [50000000, 5000], [None, 10000]]}


def test_late_fee_per_day_and_not_late():
    assert eng.late_fee(GST, D(2026, 10, 11), D(2026, 11, 6), {"aato": 30000000}) == (Decimal(1300), None)
    assert eng.late_fee(GST, D(2026, 10, 11), D(2026, 10, 11), {"aato": 1})[0] == 0
    assert eng.late_fee(None, D(2026, 10, 11), D(2026, 11, 6), {})[0] == 0


def test_late_fee_caps_by_turnover_band():
    far = D(2027, 10, 11)
    assert eng.late_fee(GST, D(2026, 10, 11), far, {"aato": 10_000_000})[0] == 2000
    assert eng.late_fee(GST, D(2026, 10, 11), far, {"aato": 30_000_000})[0] == 5000
    assert eng.late_fee(GST, D(2026, 10, 11), far, {"aato": 134_000_000})[0] == 10000


def test_late_fee_flags_what_it_cannot_cap():
    amount, note = eng.late_fee(GST, D(2026, 10, 11), D(2026, 11, 6), {})
    assert amount == 1300 and "aato" in note
    amount, note = eng.late_fee({"late_fee_per_day": 200, "cap": "tds_amount"}, D(2026, 10, 31), D(2026, 11, 6), {})
    assert amount == 1200 and "tds_amount" in note
    assert eng.late_fee({"additional_fee_per_day": 100}, D(2026, 10, 30), D(2026, 11, 6), {})[0] == 700


# ------------------------------------------------------------------ candidate evaluation
def rule(code, frequency, applicability, due_rule, *, version=1, per_registration=None, effective=None, penalty=None):
    return {"id": f"{code}-v{version}", "code": code, "version": version, "title": code, "form_code": code, "authority_code": "X",
            "statute_ref": "test", "frequency": frequency, "per_registration": per_registration, "applicability": applicability,
            "due_rule": due_rule, "penalty_rule": penalty, "effective": effective or Range(D(2020, 1, 1), None)}


GSTR1 = rule("GSTR1", "monthly", {"all": [leaf("gst_frequency", "=", "monthly")]}, {"type": "day_of_following_month", "day": 11},
             per_registration="gstin")
AS_OF = D(2026, 11, 6)


def test_one_candidate_per_registration_and_frequency_overlay():
    ctx = eng.Context({}, [eng.Reg("r1", "gstin", "29AAA", gst_filing_frequency="monthly"),
                           eng.Reg("r2", "gstin", "33AAA", gst_filing_frequency="qrmp"),
                           eng.Reg("r3", "tan", "TAN1")])
    cands, n = eng.evaluate_rules([GSTR1], ctx, AS_OF)
    assert n == 1
    monthly = [c for c in cands if c.applies]
    assert {c.reg.id for c in monthly} == {"r1"}                       # qrmp GSTIN does not get the monthly return
    assert any(c.reg.id == "r2" and not c.applies for c in cands)
    assert all(c.reg.kind == "gstin" for c in cands)                  # the TAN is not a GSTIN


def test_rule_without_registrations_yields_nothing():
    cands, _ = eng.evaluate_rules([GSTR1], eng.Context({}, []), AS_OF)
    assert cands == []


def test_derived_facts_and_explicit_override():
    regs = [eng.Reg("t", "tan", "T"), eng.Reg("p", "professional_tax", "P", state_code="29")]
    f = eng.derived_facts({"has_tan": False}, regs)
    assert f["has_tan"] is False and f["employs_in_state_29"] is True and f["has_professional_tax"] is True


def test_version_is_picked_by_effective_range():
    v1 = rule("R", "monthly", {"all": []}, {"type": "day_of_following_month", "day": 11}, version=1,
              effective=Range(D(2020, 1, 1), D(2026, 12, 1)))
    v2 = rule("R", "monthly", {"all": []}, {"type": "day_of_following_month", "day": 12}, version=2, effective=Range(D(2026, 12, 1), None))
    cands, _ = eng.evaluate_rules([v1, v2], eng.Context({}, []), AS_OF)
    by_period = {c.period.label: c for c in cands}
    assert by_period["Nov 2026"].rule["version"] == 1 and by_period["Nov 2026"].due == D(2026, 12, 11)
    assert by_period["Dec 2026"].rule["version"] == 2 and by_period["Dec 2026"].due == D(2027, 1, 12)
    assert len([c for c in cands if c.period.label == "Dec 2026"]) == 1             # never both versions


def test_unscheduled_frequencies_are_skipped_and_incorporation_respected():
    ev = rule("EV", "per_document", {"all": []}, {"type": "per_document"})
    one = rule("ONE", "one_time", {"all": []}, {"type": "per_document"})
    m = rule("M", "monthly", {"all": []}, {"type": "day_of_following_month", "day": 5})
    cands, n = eng.evaluate_rules([ev, one, m], eng.Context({}, [], incorporated_on=D(2026, 9, 15)), AS_OF)
    assert n == 1 and min(c.period.start for c in cands) == D(2026, 9, 1)   # nothing before the company existed


def test_a_bad_rule_names_itself():
    bad = rule("BAD.RULE", "monthly", {"fact": "x", "op": "??", "value": 1}, {"type": "day_of_following_month", "day": 5})
    with pytest.raises(eng.RuleError, match="BAD.RULE v1"):
        eng.evaluate_rules([bad], eng.Context({}, []), AS_OF)


def test_create_window():
    assert eng.in_create_window(D(2026, 10, 1), AS_OF, 180, 45)
    assert not eng.in_create_window(D(2026, 9, 1), AS_OF, 180, 45)
    assert not eng.in_create_window(D(2027, 6, 1), AS_OF, 180, 45)
