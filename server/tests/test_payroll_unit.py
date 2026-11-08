"""Payroll calculations: pure, no database."""
import datetime as dt
from decimal import Decimal as D

import pytest

from app import ledger, payroll as pr

d = dt.date


# ------------------------------------------------------------------ wage code
def test_statutory_wage_adds_back_allowances_above_half():
    assert pr.statutory_wage(D(21000), D(10000)) == D("10500.0")
    assert pr.statutory_wage(D(21000), D(12000)) == D(12000)
    assert pr.wage_pct(D(21000), D(10000)) == D("47.6") and pr.wage_pct(D(0), D(0)) is None


def test_pf_wage_is_capped():
    assert pr.pf_wage(D(10500)) == D(10500) and pr.pf_wage(D(40000)) == D(15000)


# ------------------------------------------------------------------ PF
def test_pf_capped_wage():
    p = pr.pf(D(40000))
    assert (p["pf_wage"], p["employee"], p["employer_eps"], p["employer_epf"]) == (D(15000), D(1800), D(1250), D(550))


def test_pf_split_always_adds_up_to_twelve_percent():
    for wage in (D(9000), D(10500), D(14999), D(15000), D(30000)):
        p = pr.pf(wage)
        assert p["employer_eps"] + p["employer_epf"] == p["employee"]


def test_pf_not_applicable():
    assert pr.pf(D(20000), applicable=False) == {"pf_wage": 0, "employee": 0, "employer_eps": 0, "employer_epf": 0}


# ------------------------------------------------------------------ ESI
@pytest.mark.parametrize("gross,emp,er", [(21000, 158, 683), (20000, 150, 650), (19500, 147, 634), (15000, 113, 488)])
def test_esi_rounds_up(gross, emp, er):
    e = pr.esi(D(gross))
    assert e["applicable"] and (e["employee"], e["employer"]) == (D(emp), D(er))


def test_esi_ceiling_and_opt_out():
    assert pr.esi(D(21000))["applicable"] and not pr.esi(D(21001))["applicable"]
    assert pr.esi(D(10000), applicable=False) == {"applicable": False, "employee": 0, "employer": 0}


# ------------------------------------------------------------------ professional tax
@pytest.mark.parametrize("state,gross,month,expected", [
    ("29", 24999, 11, 0), ("29", 25000, 11, 200), ("29", 25000, 2, 300), ("27", 7500, 11, 0), ("27", 7501, 11, 175),
    ("27", 10001, 11, 200), ("27", 10001, 2, 300)])
def test_professional_tax_slabs(state, gross, month, expected):
    assert pr.professional_tax(state, D(gross), month) == (D(expected), True)


def test_professional_tax_unsupported_state_is_flagged_not_guessed():
    assert pr.professional_tax("33", D(50000), 11) == (D(0), False)


# ------------------------------------------------------------------ tax
@pytest.mark.parametrize("income,regime,expected", [
    (400000, "new", "0.00"), (1200000, "new", "0.00"), (1210000, "new", "10400.00"), (1275000, "new", "74100.00"), (2000000, "new", "208000.00"),
    (500000, "old", "0.00"), (1000000, "old", "117000.00"), (1200000, "old", "179400.00"), (-5, "new", "0.00")])
def test_annual_tax(income, regime, expected):
    assert pr.annual_tax(D(income), regime) == D(expected)


def test_marginal_relief_never_makes_you_worse_off_than_the_limit():
    for extra in (1, 1000, 10000, 50000):
        income = D(1200000 + extra)
        assert pr.annual_tax(income, "new") <= (income - 1200000) * D("1.04") + D("0.01")


def test_months_before_in_fy():
    assert [pr.months_before_in_fy(d(2026, m, 1)) for m in (4, 11, 3)] == [0, 7, 11]


def test_tds_nil_under_the_rebate():
    assert pr.tds_on_salary(pr.TdsInput("new", D(100000), 0)) == 0                            # 12 lakh less 75,000 standard deduction is within the rebate


def test_first_payslip_catches_up_over_the_remaining_months():
    assert pr.tds_on_salary(pr.TdsInput("new", D(150000), 7)) == D(30160)                    # 1,50,800 annual tax over the 5 months left


def test_known_history_replaces_the_assumption():
    t = pr.tds_on_salary(pr.TdsInput("new", D(150000), 7, prior_gross=D(300000), prior_payslips=2, tds_so_far=D(20000)))
    assert t == D(26160)                                                                       # (1,50,800 - 20,000) / 5


def test_no_tds_in_the_last_month_when_tax_is_covered_and_never_negative():
    assert pr.tds_on_salary(pr.TdsInput("new", D(150000), 11, prior_gross=D(1650000), prior_payslips=11, tds_so_far=D(500000))) == 0
    assert pr.tds_on_salary(pr.TdsInput("new", D(10), 12)) == 0


def test_old_regime_counts_the_employees_pf():
    new = pr.tds_on_salary(pr.TdsInput("old", D(150000), 0, employee_pf_this_month=D(1800)))
    none = pr.tds_on_salary(pr.TdsInput("old", D(150000), 0))
    assert new < none


# ------------------------------------------------------------------ one payslip
NOV = d(2026, 11, 1)


def comps(*rows):
    return [pr.Component(c, D(a), "earning", w) for c, a, w in rows]


SNEHA = comps(("BASIC", 10000, True), ("HRA", 5000, False), ("SPECIAL", 6000, False))


def slip(components, **kw):
    base = dict(month_start=NOV, joined=d(2020, 1, 1), left=None, work_state="29", regime="new", components=components, inputs=pr.Inputs(),
                pf_applicable=True, esi_applicable=True)
    return pr.compute_payslip(**{**base, **kw})


def test_a_low_basic_employee():
    s = slip(SNEHA)
    assert (s.gross, s.wages, s.pf_wage, s.pf_employee, s.pf_employer_eps, s.pf_employer_epf) == (D("21000.00"), D("10500.00"), D("10500.00"), D(1260), D(875), D(385))
    assert (s.esi_applicable, s.esi_employee, s.esi_employer, s.professional_tax, s.tds, s.net_pay) == (True, D(158), D(683), 0, 0, D("19582.00"))
    assert s.flags == ["wage_below_50pct"]


def test_net_pay_identity_holds_for_awkward_inputs():
    s = slip(comps(("BASIC", 33333.33, True), ("HRA", 16666.67, False)), inputs=pr.Inputs(lop_days=D("2.5"), overtime=D("1234.56"), bonus=D(500), other_deductions=D("99.99")))
    assert s.net_pay == s.gross - s.pf_employee - s.esi_employee - s.professional_tax - s.tds - s.other_deductions


def test_loss_of_pay_prorates_earnings():
    s = slip(comps(("BASIC", 30000, True)), inputs=pr.Inputs(lop_days=D(3)))
    assert (s.paid_days, s.lop_days, s.gross) == (D(27), D(3), D("27000.00"))


def test_joining_and_leaving_mid_month():
    assert pr.service_days(NOV, d(2026, 11, 16), None) == 15 and pr.service_days(NOV, d(2020, 1, 1), d(2026, 11, 10)) == 10
    s = slip(comps(("BASIC", 30000, True)), joined=d(2026, 11, 16))
    assert s.paid_days == D(15) and s.gross == D("15000.00")
    with pytest.raises(ValueError):
        slip(comps(("BASIC", 30000, True)), joined=d(2026, 12, 1))
    assert pr.service_days(NOV, d(2020, 1, 1), d(2026, 10, 31)) == 0


def test_lop_cannot_exceed_days_in_service():
    s = slip(comps(("BASIC", 30000, True)), joined=d(2026, 11, 21), inputs=pr.Inputs(lop_days=D(31)))
    assert s.paid_days == 0 and s.gross == 0


def test_one_offs_and_reimbursements_and_non_taxable_earnings():
    comps_ = [pr.Component("BASIC", D(60000), "earning", True), pr.Component("MEDICAL", D(10000), "earning", False, taxable=False),
              pr.Component("FUEL", D(3000), "reimbursement", False)]
    s = slip(comps_, inputs=pr.Inputs(overtime=D(1000), bonus=D(2000), reimbursements=D(500)))
    assert s.gross == D("76500.00")                                                           # 70 000 earnings + 3 000 + 500 reimbursements + 3 000 one-offs
    assert {l[0] for l in s.lines} >= {"BASIC", "MEDICAL", "OVERTIME", "BONUS", "REIMBURSEMENT", "PF_EMPLOYEE", "PT"}
    assert [l for l in s.lines if l[0] == "REIMBURSEMENT"][0][3] == D("3500.00")


def test_unconfigured_state_and_negative_net_are_flagged():
    s = slip(comps(("BASIC", 30000, True)), work_state="33")
    assert "pt_state_not_configured" in s.flags
    s = slip(comps(("BASIC", 10000, True)), inputs=pr.Inputs(other_deductions=D(50000)))
    assert "negative_net_pay" in s.flags


def test_not_covered_when_the_entity_has_no_pf_or_esi_registration():
    s = slip(SNEHA, pf_applicable=False, esi_applicable=False)
    assert (s.pf_employee, s.esi_employee, s.esi_applicable) == (0, 0, False)


def test_feb_professional_tax():
    assert slip(comps(("BASIC", 40000, True)), month_start=d(2027, 2, 1)).professional_tax == D(300)


# ------------------------------------------------------------------ gratuity and bonus
def test_completed_years_round_up_after_six_months():
    assert pr.completed_years(d(2020, 1, 15), d(2026, 11, 7)) == 7                          # 6 years 9 months
    assert pr.completed_years(d(2026, 6, 1), d(2026, 11, 7)) == 0 and pr.completed_years(d(2021, 5, 1), d(2026, 12, 1)) == 6


def test_gratuity_provision():
    assert pr.gratuity_provision(D(26000), d(2020, 1, 15), d(2026, 11, 7)) == D("105000.00")
    assert pr.gratuity_provision(D(26000), d(2026, 6, 1), d(2026, 11, 7)) == 0


@pytest.mark.parametrize("wages,months,expected", [(7000, 12, "6997.20"), (21000, 12, "6997.20"), (21001, 12, "0.00"), (5000, 6, "2499.00"), (7000, 0, "0.00"), (7000, 20, "6997.20")])
def test_statutory_bonus(wages, months, expected):
    assert pr.bonus_for_year(D(wages), months) == D(expected)


# ------------------------------------------------------------------ the payroll journal
def totals(**kw):
    base = {"gross": D(100000), "net": D(85000), "pf_employee": D(5000), "pf_employer": D(5000), "esi_employee": D(0), "esi_employer": D(0), "pt": D(200), "tds": D(9800), "other": D(0)}
    return {**base, **kw}


def test_payroll_journal_balances():
    lines = ledger.payroll_run_lines(totals())
    keys = {l.key: (l.debit, l.credit) for l in lines}
    assert ledger.balanced(lines)
    assert keys["salary_expense"] == (D(100000), 0) and keys["employer_contrib_expense"] == (D(5000), 0)
    assert keys["salary_payable"] == (0, D(85000)) and keys["pf_payable"] == (0, D(10000)) and keys["tds_payable"] == (0, D(9800)) and "esi_payable" not in keys


def test_payroll_journal_refuses_a_run_that_does_not_add_up():
    with pytest.raises(ledger.PostingError):
        ledger.payroll_run_lines(totals(net=D(90000)))
