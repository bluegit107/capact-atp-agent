"""솔버 정합성 테스트 (S2.5 재기준화 이후): 계획 기준·하드 제약·교차 솔버·메타모픽·게이트·레버·골든 회귀."""
from __future__ import annotations

import time
from dataclasses import replace

import pytest

from dealcockpit.adapters.golden_loader import load_case
from dealcockpit.adapters.solver_lp import LpSolver
from dealcockpit.application.build_input import build_solver_input, material_cost_per_unit
from dealcockpit.application.case import BuildParams
from dealcockpit.application.economics import breakeven_price_cut_pct, option_paybacks, unit_economics
from dealcockpit.application.report import cum, to_scenario
from dealcockpit.application.scenarios import BASELINE_WEIGHTS, PRESETS, run_scenarios
from dealcockpit.domain.common import Provenance, Role
from dealcockpit.domain.lever import Lever, LeverStatus, LeverType
from dealcockpit.domain.plan import promotion_blockers

HIGHS, SCIP = LpSolver("HIGHS"), LpSolver("SCIP")
W1 = PRESETS["D"].weights
REQ = {"formula_revalidation": 6, "M-B_recontract": 4}


@pytest.fixture(scope="module")
def case():
    return load_case()


def scen(out, sid):
    return next(s for s in out.scenarios if s.scenario_id == sid)


def solve(case, params=None, weights=W1, solver=SCIP, **kw):
    inp = build_solver_input(case, params or BuildParams(), weights, **kw)
    return inp, solver.solve(inp)


def lever(lid="LV-001", status=LeverStatus.HYPOTHESIS, type_=LeverType.CAPACITY_DELTA, params=None):
    params = params or dict(target="outsourcing:V-OUT-1", product="PL-02", qty_per_month=50000, available_from_week="2026-W36", unit_cost_multiplier=1.35)
    return Lever(lever_id=lid, type=type_, status=status, params=params, owner_role=Role.PRODUCTION,
                 counterparty="(가상)", confidence=0.5, provenance=Provenance.SIMULATED)


# ── 골든 회귀: 대회 문서와 정합하는 핵심 결론이 유지되는가 ─────────────────────────────
def test_golden_conclusions_under_monthly_strategic_basis(case):
    out = run_scenarios(SCIP, case, BuildParams())
    a, c = scen(out, "A"), scen(out, "C")
    assert a.accepted_pct["PL-02"] == pytest.approx(0.667, abs=0.02)  # 증설·특근 없이 약 2/3
    assert not a.capacity_actions and a.infeasibility is None
    assert c.infeasibility is not None  # 옵션 회신 전 100% 수락 + 보호는 불가능
    with_opt = run_scenarios(SCIP, case, BuildParams(requirement_delays=REQ))
    a2 = scen(with_opt, "A")
    assert a2.accepted_pct["PL-02"] == pytest.approx(1.0, abs=1e-3) and "OPT-02" in a2.capacity_actions and "OPT-01" not in a2.capacity_actions


def test_annual_basis_is_far_more_restrictive(case):
    a_m = scen(run_scenarios(SCIP, case, BuildParams()), "A").accepted_pct["PL-02"]
    a_y = scen(run_scenarios(SCIP, case, BuildParams(interpretation="S_YEAR")), "A").accepted_pct["PL-02"]
    assert a_y == pytest.approx(0.055, abs=0.02) and a_m > 5 * a_y  # 해석이 결정을 바꾼다 -> AS-001 게이트 근거


# ── 계획 기준·입력 조립 ─────────────────────────────────────────────────────────
def test_strategic_basis_levels_weekly_load_to_capacity_times_utilization(case):
    inp = build_solver_input(case, BuildParams(), W1)
    load = inp.capacity.base_weekly * 0.97
    total = sum(sum(g.weekly_due) for g in inp.groups)
    assert total == pytest.approx(load * inp.horizon_weeks, rel=1e-6)  # 편성 분리는 총량을 바꾸지 않는다(이중 계상 없음)


def test_h3_keeps_order_scale_for_cross_check(case):
    inp = build_solver_input(case, BuildParams(interpretation="H3"), W1)
    raw_total = sum(sum(g["weekly_due"]) for g in case.derived["groups"].values())
    assert sum(sum(g.weekly_due) for g in inp.groups) == pytest.approx(raw_total, rel=1e-6)


def test_overtime_is_zero_by_default_and_needs_a_lever(case):
    inp = build_solver_input(case, BuildParams(), W1)
    assert inp.capacity.overtime_cap_pct == 0 and any("특근 0" in n for n in inp.notes)
    ot = lever("LV-005", LeverStatus.APPROVED, params=dict(target="overtime:L-02", product="PL-02", qty_per_month=60000, available_from_week="2026-W30", unit_cost_multiplier=1.5))
    inp2 = build_solver_input(case, BuildParams(levers=(ot,)), W1)
    assert inp2.capacity.overtime_cap_pct == pytest.approx(100 * 60000 / 4.333 / inp2.capacity.base_weekly)
    assert inp2.capacity.overtime_start_week == 3  # 2026-W30 월요일(7/20) = 기준 주(6/29)부터 3주
    hyp = build_solver_input(case, BuildParams(levers=(ot.model_copy(update={"status": LeverStatus.HYPOTHESIS}),)), W1)
    assert hyp.capacity.overtime_cap_pct == 0  # 가설 특근은 확정 계획에 안 들어간다


def test_bid_cost_is_material_based_and_fx_linked(case):
    inp_as = build_solver_input(case, BuildParams(), W1)
    inp_dn = build_solver_input(case, BuildParams(fx_label="downside"), W1)
    m = case.derived["material"]
    assert inp_as.bid.unit_cost_krw == pytest.approx(m["kg_per_ea_2026h1"] * m["usd_per_kg_latest"] * 1478.29)
    assert inp_dn.bid.unit_cost_krw / inp_as.bid.unit_cost_krw == pytest.approx(1420 / 1478.29)  # USD 원료는 환율과 함께 움직임
    assert abs(material_cost_per_unit(case, 1456.645) / m["cost_per_ea_krw_2026h1"] - 1) < 0.03  # 실제 투입원가와 분해식 일치


def test_recall_decision_adds_replacement_load_and_cuts_acceptance(case):
    base = scen(run_scenarios(SCIP, case, BuildParams(requirement_delays=REQ)), "A").accepted_pct["PL-02"]
    out = run_scenarios(SCIP, case, BuildParams(requirement_delays=REQ, recall_replacement_week=2))
    inp = build_solver_input(case, BuildParams(recall_replacement_week=2), W1)
    assert any(g.group_id == "RECALL-REPL" for g in inp.groups)
    assert scen(out, "A").accepted_pct["PL-02"] < base  # 회수 대체 생산이 입찰 여력을 잠식(다른 부서 영향)


# ── 물리적 정합 ────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("basis,ot", [("S_MONTH", None), ("S_YEAR", None), ("H3", None), ("S_MONTH", 8.0)])
def test_solution_respects_physics_for_every_preset(case, basis, ot):
    p = BuildParams(interpretation=basis, overtime_cap_pct=ot, requirement_delays=REQ)
    _, base_raw = solve(case, replace(p, with_bid=False), BASELINE_WEIGHTS)
    floor = {g: tuple(cum(s)) for g, s in base_raw.ship.items()}
    for sid, spec in PRESETS.items():
        inp = build_solver_input(case, p, spec.weights, protect_floor=floor if spec.protect_existing else None, min_accept_override=spec.min_accept)
        raw = SCIP.solve(inp)
        H, cap = inp.horizon_weeks, inp.capacity
        prod = [raw.base_use[t] + raw.overtime[t] + raw.option_use[t] + raw.outsourced[t] for t in range(H)]
        shipped = sum(sum(v) for v in raw.ship.values()) + sum(raw.bid_ship)
        assert sum(prod) == pytest.approx(shipped + raw.inventory[-1], rel=1e-7, abs=1e-2), sid
        for t in range(H):
            assert raw.base_use[t] <= cap.base_weekly + 1e-6
            assert raw.overtime[t] <= (cap.overtime_cap_pct / 100 * cap.base_weekly if t >= cap.overtime_start_week else 0) + 1e-6
            assert raw.inventory[t] >= -1e-6
        for g in inp.groups:
            S, D = cum(raw.ship[g.group_id]), cum(g.weekly_due)
            assert all(S[t] <= D[t] + 1e-6 for t in range(H))
            if g.max_late_weeks is not None and not raw.relaxations:
                for t in range(g.max_late_weeks, H):
                    assert S[t] >= D[t - g.max_late_weeks] - 1.0, f"{sid}/{g.group_id} W{t}"


def test_fixed_slot_ships_exactly_on_its_week(case):
    inp, raw = solve(case)
    slot_g = next(g for g in inp.groups if g.group_id == "C-102-SLOT")
    slot = raw.ship["C-102-SLOT"]
    assert slot[10] == pytest.approx(slot_g.weekly_due[10], abs=1.0) and sum(slot[:10]) < 1.0


def test_existing_service_level_protected_in_A_B_C(case):
    out = run_scenarios(SCIP, case, BuildParams(requirement_delays=REQ))
    base = {ci.customer_id: ci for ci in out.baseline.customer_impact}
    for sid in ("A", "B", "C"):
        sc = scen(out, sid)
        if sc.infeasibility:
            continue
        for ci in sc.customer_impact:
            assert ci.penalty_krw <= base[ci.customer_id].penalty_krw + 10_000, f"{sid}/{ci.customer_id}"
            assert ci.max_delay_days <= base[ci.customer_id].max_delay_days


def test_reference_D_never_reaches_termination_trigger(case):
    d = scen(run_scenarios(SCIP, case, BuildParams()), "D")
    c101 = next(ci for ci in d.customer_impact if ci.customer_id == "C-101")
    assert c101.max_delay_days <= 21 < 30
    assert d.kpis["otif_existing_pct"] < 100  # 무동의 지연은 서비스 수준을 깎는다(참고용)


# ── 고객 동의(B) ───────────────────────────────────────────────────────────────
def test_consent_lever_lets_B_shift_only_that_customer(case):
    consent = lever("LV-006", LeverStatus.HYPOTHESIS, LeverType.TIME_SHIFT, dict(target="customer:C-101", shift_days=14))
    out = run_scenarios(SCIP, case, BuildParams(levers=(consent,), include_hypothetical=True))
    a, b = scen(out, "A"), scen(out, "B")
    a_plain = scen(run_scenarios(SCIP, case, BuildParams()), "A")
    assert a.financials == a_plain.financials  # A 는 동의 레버를 쓰지 않는다(기존 약속 그대로)
    assert b.accepted_pct["PL-02"] > a.accepted_pct["PL-02"] + 0.05  # 동의가 실제로 입찰 여력을 만든다
    assert b.financials.net_krw > a.financials.net_krw
    imp = {ci.customer_id: ci for ci in b.customer_impact}
    base = {ci.customer_id: ci for ci in out.baseline.customer_impact}
    assert imp["C-101"].max_delay_days <= 14
    for other in ("C-102", "OTHERS"):
        assert imp[other].max_delay_days <= base[other].max_delay_days  # 동의 안 한 고객은 보호 유지
    assert not any("동의(time_shift) 회신이 없어" in t.get("warning", "") for t in out.trace)


def test_without_consent_B_equals_A_and_is_flagged(case):
    out = run_scenarios(SCIP, case, BuildParams())
    assert scen(out, "A").financials == scen(out, "B").financials
    assert any("동의(time_shift) 회신이 없어" in t.get("warning", "") for t in out.trace)


# ── 교차 솔버·결정성 ────────────────────────────────────────────────────────────
@pytest.mark.parametrize("params", [
    BuildParams(), BuildParams(interpretation="S_YEAR"), BuildParams(interpretation="H3"),
    BuildParams(requirement_delays=REQ, fx_label="downside", price_pressure_pct=-10.0),
], ids=["S_MONTH", "S_YEAR", "H3", "S_MONTH-opt-fx-price"])
def test_highs_and_scip_agree(case, params):
    a, b = run_scenarios(HIGHS, case, params), run_scenarios(SCIP, case, params)
    for sa, sb in zip([a.baseline, *a.scenarios], [b.baseline, *b.scenarios]):
        assert (sa.infeasibility is None) == (sb.infeasibility is None), sa.scenario_id
        if sa.infeasibility is None:
            assert sa.financials.net_krw == pytest.approx(sb.financials.net_krw, rel=1e-4, abs=5e6), sa.scenario_id
        else:
            # 불가능 해: 위반량을 어느 고객에 배분할지는 동가 최적해가 여럿이라 재무가 다를 수 있다.
            # 비교 대상은 '최소 완화량 합계'(솔버가 최소화하는 양)다.
            ta = sum(r.slack for r in sa.infeasibility.min_relaxation)
            tb = sum(r.slack for r in sb.infeasibility.min_relaxation)
            assert ta == pytest.approx(tb, rel=1e-4, abs=5.0), sa.scenario_id


def test_same_input_same_hash_and_result(case):
    a, b = run_scenarios(SCIP, case, BuildParams()), run_scenarios(SCIP, case, BuildParams())
    assert a.input_hash == b.input_hash and [s.financials for s in a.scenarios] == [s.financials for s in b.scenarios]
    assert run_scenarios(SCIP, case, BuildParams(price_pressure_pct=-5.0)).input_hash != a.input_hash


# ── 메타모픽 ───────────────────────────────────────────────────────────────────
def test_metamorphic_capacity_relaxation_tightening_order(case):
    inp, raw = solve(case)
    more = inp.model_copy(update={"capacity": inp.capacity.model_copy(update={"base_weekly": inp.capacity.base_weekly * 1.05})})
    assert SCIP.solve(more).objective >= raw.objective - 1.0
    ot = inp.model_copy(update={"capacity": inp.capacity.model_copy(update={"overtime_cap_pct": 10.0})})
    assert SCIP.solve(ot).objective >= raw.objective - 1.0
    loose = tuple(g.model_copy(update={"max_late_weeks": None}) if g.group_id == "OTHERS" else g for g in inp.groups)
    assert SCIP.solve(inp.model_copy(update={"groups": loose})).objective >= raw.objective - 1.0
    tight = inp.model_copy(update={"capacity": inp.capacity.model_copy(update={"base_weekly": inp.capacity.base_weekly * 0.95})})
    assert SCIP.solve(tight).objective <= raw.objective + 1.0
    rev = inp.model_copy(update={"groups": tuple(reversed(inp.groups))})
    assert SCIP.solve(rev).objective == pytest.approx(raw.objective, rel=1e-7, abs=1.0)


def test_quantity_scale_invariance_without_fixed_costs(case):
    inp, _ = solve(case)
    inp = inp.model_copy(update={"capacity": inp.capacity.model_copy(update={"options": ()})})
    raw = SCIP.solve(inp)
    k = 3.0
    scaled = inp.model_copy(update={
        "groups": tuple(g.model_copy(update={"weekly_due": tuple(v * k for v in g.weekly_due)}) for g in inp.groups),
        "capacity": inp.capacity.model_copy(update={"base_weekly": inp.capacity.base_weekly * k}),
        "bid": inp.bid.model_copy(update={"weekly_requested": inp.bid.weekly_requested * k}),
    })
    r2 = SCIP.solve(scaled)
    assert r2.accept == pytest.approx(raw.accept, abs=1e-5) and r2.objective == pytest.approx(k * raw.objective, rel=1e-5)


def test_lp_objective_matches_reported_financials(case):
    inp, raw = solve(case)
    assert raw.objective == pytest.approx(to_scenario("D", "x", inp, raw, ()).financials.net_krw, rel=2e-3)


# ── 불가능·게이트·레버 ─────────────────────────────────────────────────────────────
def test_infeasible_C_reports_relaxation_and_blocks_promotion(case):
    out = run_scenarios(SCIP, case, BuildParams())
    c = scen(out, "C")
    assert c.infeasibility and all(r.slack > 0 for r in c.infeasibility.min_relaxation)
    assert "불가능(infeasible) 시나리오" in promotion_blockers(c, [], {}, [])


def test_unconfirmed_clauses_are_excluded_and_reported():
    inp = build_solver_input(load_case(clauses_confirmed=False), BuildParams(), W1)
    c101 = next(g for g in inp.groups if g.group_id == "C-101")
    assert c101.penalty_clause_id is None and c101.penalty_provenance is Provenance.ASSUMPTION
    assert any("인간 확정 전" in n for n in inp.notes) and not any(g.group_id.endswith("-SLOT") for g in inp.groups)


def test_confirmed_clauses_shape_the_limits(case):
    g = {x.group_id: x for x in build_solver_input(case, BuildParams(), W1).groups}
    assert (g["C-101"].max_late_weeks, g["C-102"].max_late_weeks, g["C-102-SLOT"].max_late_weeks, g["OTHERS"].max_late_weeks) == (3, 5, 0, 8)


def test_hypothetical_rejected_expired_levers(case):
    assert not build_solver_input(case, BuildParams(levers=(lever(),)), W1).capacity.outsourcing
    assert build_solver_input(case, BuildParams(levers=(lever(),), include_hypothetical=True), W1).capacity.outsourcing
    for st in (LeverStatus.REJECTED, LeverStatus.EXPIRED):
        assert not build_solver_input(case, BuildParams(levers=(lever(status=st),), include_hypothetical=True), W1).capacity.outsourcing


def test_outsourcing_lever_never_hurts_and_needs_approval_when_used(case):
    early = lever(params=dict(target="outsourcing:V-OUT-1", product="PL-02", qty_per_month=50000, available_from_week="2026-W30", unit_cost_multiplier=1.35))
    without, with_lv = run_scenarios(SCIP, case, BuildParams()), run_scenarios(SCIP, case, BuildParams(levers=(early,), include_hypothetical=True))
    a0, a1 = scen(without, "A"), scen(with_lv, "A")
    assert a1.financials.net_krw >= a0.financials.net_krw - 1 and a1.accepted_pct["PL-02"] >= a0.accepted_pct["PL-02"] - 1e-6
    if a1.kpis["outsourced_units"] > 0:
        assert any(n.action == "outsourcing_contract" and n.approver == Role.CEO_BOARD.value for n in a1.approvals_required)


def test_waiver_lever_needs_sales_exec(case):
    wl = lever("LV-002", LeverStatus.APPROVED, LeverType.PRICE_ADJUST, dict(target="clause:CL-C101-10", pct=-3.0, waives_penalty=True))
    c101 = next(g for g in build_solver_input(case, BuildParams(levers=(wl,)), W1).groups if g.group_id == "C-101")
    assert c101.waiver.discount_pct == 3.0 and c101.waiver.grace_weeks == 2
    d = scen(run_scenarios(SCIP, case, BuildParams(levers=(wl,))), "D")
    assert next(n for n in d.approvals_required if n.action == "price_renegotiation_C-101").approver == Role.SALES_EXEC.value


def test_unsupported_lever_reported_and_scope_change_excludes_C(case):
    dl = lever("LV-003", LeverStatus.APPROVED, LeverType.DEMAND_SIGNAL, dict(customer_id="C-101", product="PL-02", weekly_qty_delta_pct=-20, persistent=True))
    assert any("LV-003" in n and "미지원" in n for n in build_solver_input(case, BuildParams(levers=(dl,)), W1).notes)
    sl = lever("LV-004", LeverStatus.APPROVED, LeverType.SCOPE_CHANGE, dict(target="bid:C-201", accepted_pct_min=0.0, accepted_pct_max=0.4))
    out = run_scenarios(SCIP, case, BuildParams(levers=(sl,)))
    assert {s.scenario_id for s in out.scenarios} == {"A", "B", "D"}
    assert all(s.accepted_pct["PL-02"] <= 0.4 + 1e-6 for s in out.scenarios)


def test_option_requirements_gate_and_timing(case):
    assert [o.option_id for o in build_solver_input(case, BuildParams(), W1).capacity.options] == ["OPT-01"]
    o2 = next(o for o in build_solver_input(case, BuildParams(requirement_delays=REQ), W1).capacity.options if o.option_id == "OPT-02")
    assert o2.start_week == 1 + 7 + 6


# ── 경제성(CP5) ───────────────────────────────────────────────────────────────
def test_unit_economics_grid_and_breakeven(case):
    grid = unit_economics(case, BuildParams())
    assert len(grid) == 9
    rfp0 = next(u for u in grid if u.fx_label == "rfp" and u.price_cut_pct == 0)
    rfp10 = next(u for u in grid if u.fx_label == "rfp" and u.price_cut_pct == -10)
    assert rfp10.contribution_krw < rfp0.contribution_krw and rfp0.margin_pct > 40
    be = breakeven_price_cut_pct(case, BuildParams())
    assert -60 < be < -30  # 원료원가 기준 공헌이익은 단가 30~60% 인하까지 버팀(고정비 제외)


def test_option_payback_respects_shortage_and_orders_options(case):
    pb = {o.option_id: o for o in option_paybacks(case, BuildParams(requirement_delays=REQ))}
    assert pb["OPT-02"].payback_months_after_start < pb["OPT-01"].payback_months_after_start
    assert pb["OPT-01"].usable_monthly_units < 1_600_000 / 12  # 부족분을 넘는 능력은 회수에 기여하지 않음
    no_reply = {o.option_id: o for o in option_paybacks(case, BuildParams())}
    assert no_reply["OPT-02"].start_week is None and no_reply["OPT-02"].payback_months_from_decision is None


# ── 산출물·성능 ─────────────────────────────────────────────────────────────────
def test_kpis_and_evidence_present(case):
    out = run_scenarios(SCIP, case, BuildParams())
    for s in [out.baseline, *out.scenarios]:
        for key in ("otif_existing_pct", "bid_accept_pct", "base_utilization_pct"):
            assert key in s.kpis
        for ci in s.customer_impact:
            if ci.orders_delayed > 0:
                assert ci.evidence_clause_ids
    assert out.baseline.accepted_pct["PL-02"] == 0 and not out.baseline.approvals_required


def test_full_run_is_fast(case):
    t0 = time.perf_counter()
    run_scenarios(SCIP, case, BuildParams(requirement_delays=REQ))
    assert time.perf_counter() - t0 < 3.0
