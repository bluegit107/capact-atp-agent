"""S3: 파라미터 레지스트리 · 결정 불변성 게이트 · 병목 프로브 · 사용자 정의 시나리오."""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime

import pytest
from pydantic import ValidationError

from dealcockpit.adapters.golden_loader import load_case, load_reply_stats
from dealcockpit.adapters.solver_lp import LpSolver
from dealcockpit.application.build_input import build_solver_input
from dealcockpit.application.case import BuildParams, ModelSettings
from dealcockpit.application.gate import check_invariance, gate_capacity_assumption
from dealcockpit.application.probes import probe_levers
from dealcockpit.application.scenarios import PRESETS, ScenarioSpec, W1, run_scenarios
from dealcockpit.application.settings_from_registry import settings_from_registry
from dealcockpit.domain.assumption import Assumption, AssumptionStatus, EvidenceTest
from dealcockpit.domain.common import Provenance, Role
from dealcockpit.domain.lever import Lever, LeverStatus, LeverType
from dealcockpit.domain.parameters import DEFAULT_REGISTRY, ParameterError, ProposedChange

SCIP = LpSolver("SCIP")
AT = datetime(2026, 11, 9, 12, 0)


@pytest.fixture(scope="module")
def case():
    return load_case()


@pytest.fixture(scope="module")
def probes(case):
    return {p.key: p for p in probe_levers(SCIP, case, BuildParams(), load_reply_stats())}


def scen(out, sid):
    return next(s for s in out.scenarios if s.scenario_id == sid)


# ── 파라미터 레지스트리 ─────────────────────────────────────────────────────────
def setp(reg=DEFAULT_REGISTRY, key="recall_replacement_ratio", value=0.6, role=Role.QUALITY, kind="human"):
    return reg.set(key, value, actor="이품질", actor_role=role, actor_kind=kind, reason="시중 재고 40%는 폐기 예정", at=AT)


def test_registry_defaults_equal_model_settings():
    s, notes = settings_from_registry(DEFAULT_REGISTRY)
    assert s == ModelSettings() and notes == ()


def test_owner_human_changes_with_history_and_provenance():
    r2 = setp()
    assert r2.version == 2 and r2.value("recall_replacement_ratio") == 0.6
    assert r2.spec("recall_replacement_ratio").provenance is Provenance.HUMAN_INPUT
    h = r2.history[-1]
    assert (h.key, h.before, h.after, h.actor_role) == ("recall_replacement_ratio", 1.0, 0.6, Role.QUALITY)
    assert DEFAULT_REGISTRY.value("recall_replacement_ratio") == 1.0  # 원본 불변


@pytest.mark.parametrize("kind", ["ai", "system"])
def test_ai_cannot_change_registry(kind):
    with pytest.raises(PermissionError):
        setp(kind=kind)


def test_only_listed_roles_can_change():
    with pytest.raises(PermissionError):
        setp(role=Role.SALES)  # 회수 대체 비율은 품질 소관
    with pytest.raises(PermissionError):
        setp(key="invariance_accept_tol_pp", value=10, role=Role.PRODUCTION)  # 불변성 허용치는 CEO/이사회


def test_range_and_integer_validation():
    with pytest.raises(ParameterError):
        setp(value=1.5)
    with pytest.raises(ParameterError):
        setp(key="termination_buffer_weeks", value=1.5, role=Role.SALES)
    with pytest.raises(ParameterError):
        setp(key="unknown_key")


def test_ai_proposal_needs_human_owner_to_apply():
    prop = ProposedChange(proposal_id="PC-001", key="recall_replacement_ratio", value=0.7, proposer_kind="ai", rationale="클레임 고객 비중 기반 추정")
    with pytest.raises(PermissionError):
        DEFAULT_REGISTRY.apply_proposal(prop, actor="agent", actor_role=Role.QUALITY, actor_kind="ai", at=AT)
    r2 = DEFAULT_REGISTRY.apply_proposal(prop, actor="이품질", actor_role=Role.QUALITY, actor_kind="human", at=AT)
    assert r2.value("recall_replacement_ratio") == 0.7 and r2.history[-1].from_proposal == "PC-001"


def test_what_if_does_not_touch_registry_and_is_labeled():
    s, notes = settings_from_registry(DEFAULT_REGISTRY, {"termination_buffer_weeks": 0})
    assert s.termination_buffer_weeks == 0 and DEFAULT_REGISTRY.value("termination_buffer_weeks") == 1
    assert notes and "확정 계획 아님" in notes[0]
    with pytest.raises(ParameterError):
        settings_from_registry(DEFAULT_REGISTRY, {"bid_ramp_weeks": 0})


def test_settings_flow_into_the_model(case):
    s0, _ = settings_from_registry(DEFAULT_REGISTRY, {"termination_buffer_weeks": 0})
    c101 = next(g for g in build_solver_input(case, BuildParams(settings=s0), W1).groups if g.group_id == "C-101")
    assert c101.max_late_weeks == 4  # 여유 0이면 30일 -> 4주
    full = build_solver_input(case, BuildParams(recall_replacement_week=2), W1)
    half_s, _ = settings_from_registry(setp(value=0.5))
    half = build_solver_input(case, BuildParams(recall_replacement_week=2, settings=half_s), W1)
    tot = lambda inp: sum(next(g for g in inp.groups if g.group_id == "RECALL-REPL").weekly_due)
    assert tot(half) == pytest.approx(tot(full) * 0.5)


def test_tampered_registry_rejected():
    with pytest.raises(ValidationError):
        type(DEFAULT_REGISTRY)(version=1, specs=DEFAULT_REGISTRY.specs * 2)
    with pytest.raises(ValidationError):
        DEFAULT_REGISTRY.specs[0].model_copy(update={}).__class__(**{**DEFAULT_REGISTRY.specs[0].model_dump(), "value": 99})


# ── 결정 불변성 게이트 ─────────────────────────────────────────────────────────
def test_default_capacity_bases_disagree_and_block_submission(case):
    res = check_invariance(SCIP, case, BuildParams())
    assert not res.invariant and any("S_YEAR" in r for r in res.reasons)
    a, r = gate_capacity_assumption(SCIP, case, BuildParams(), Assumption(assumption_id="AS-001", statement="용량 단위", owner_role=Role.PRODUCTION))
    assert a.decision_invariant is False and a.blocks_submission
    assert [t.name for t in a.tests] == ["결정@S_MONTH", "결정@S_YEAR", "결정@H3"]


def test_confirmed_assumption_is_not_regated(case):
    conf = Assumption(assumption_id="AS-001", statement="x", owner_role=Role.PRODUCTION, status=AssumptionStatus.CONFIRMED)
    a, r = gate_capacity_assumption(SCIP, case, BuildParams(), conf)
    assert r is None and a == conf and not a.blocks_submission


def test_invariant_when_remaining_interpretations_agree(case):
    cap = case.plant.line_for_product("PL-02").capacity_qty
    p = BuildParams(confirmed_monthly=float(cap))  # 생산기술이 '월 명목 능력'이라고 회신 = S_MONTH 와 같은 결정
    res = check_invariance(SCIP, case, p, interpretations=("S_MONTH",))
    assert res.invariant and [s.interpretation for s in res.signatures] == ["S_MONTH", "CONFIRMED"]
    prop = Assumption(assumption_id="AS-001", statement="x", owner_role=Role.PRODUCTION).propose((EvidenceTest(name="회신", result="월", evidence_ref="IR-001"),))
    a, _ = gate_capacity_assumption(SCIP, case, p, prop, interpretations=("S_MONTH",))
    assert a.decision_invariant is True and not a.blocks_submission


def test_tolerance_from_settings_is_honored(case):
    wide = replace(ModelSettings(), invariance_accept_tol_pp=40.0)
    res = check_invariance(SCIP, case, BuildParams(settings=wide), interpretations=("S_MONTH", "H3"))
    assert res.invariant  # 66.7% vs 39.3% 는 ±40%p 안
    assert not check_invariance(SCIP, case, BuildParams(), interpretations=("S_MONTH", "H3")).invariant


# ── 병목 프로브 ────────────────────────────────────────────────────────────────
def test_probe_set_is_complete_and_sorted(probes):
    assert set(probes) == {"outsourcing", "overtime", "consent_C-101", "opt02_replies", "partial_award"}
    scores = [p.priority_score for p in sorted(probes.values(), key=lambda x: -x.priority_score)]
    assert scores == sorted(scores, reverse=True)
    assert all(p.owner_role and p.counterparty for p in probes.values())


def test_probe_values_match_direct_scenario_runs(case, probes):
    """프로브 값은 같은 조건을 직접 풀었을 때의 차이와 같아야 한다(별도 계산 경로 간 교차검증)."""
    a = scen(run_scenarios(SCIP, case, BuildParams()), "A")
    consent = Lever(lever_id="LV-006", type=LeverType.TIME_SHIFT, status=LeverStatus.HYPOTHESIS, params=dict(target="customer:C-101", shift_days=14),
                    owner_role=Role.SALES, counterparty="C-101", confidence=0.5, provenance=Provenance.SIMULATED)
    b = scen(run_scenarios(SCIP, case, BuildParams(levers=(consent,), include_hypothetical=True)), "B")
    assert probes["consent_C-101"].delta_net_krw == pytest.approx(b.financials.net_krw - a.financials.net_krw, abs=2)
    opt = scen(run_scenarios(SCIP, case, BuildParams(requirement_delays={"formula_revalidation": 6, "M-B_recontract": 4})), "A")
    assert probes["opt02_replies"].delta_net_krw == pytest.approx(opt.financials.net_krw - a.financials.net_krw, abs=2)
    assert probes["partial_award"].breakeven["value_at_stake_krw"] == a.financials.net_krw


def test_breakevens_are_economically_consistent(case, probes):
    o, t = probes["outsourcing"], probes["overtime"]
    assert o.breakeven["max_unit_cost_multiplier"] == pytest.approx(1 + o.value_per_unit_krw / case.derived["unit_cost_krw"], abs=1e-3)
    assert 1.0 < o.breakeven["max_unit_cost_multiplier"] < 3.0
    assert t.breakeven["max_overtime_multiplier_on_conversion_cost"] > 1.5  # 가공비가 작아 특근 할증 여유가 크다
    assert probes["consent_C-101"].breakeven["value_per_week_krw"] > 0


def test_probes_are_side_effect_free(case):
    p = BuildParams()
    before = scen(run_scenarios(SCIP, case, p), "A").financials
    probe_levers(SCIP, case, p, load_reply_stats())
    assert p.levers == () and scen(run_scenarios(SCIP, case, p), "A").financials == before


def test_priority_uses_reply_priors_and_deadline(case, probes):
    stats = load_reply_stats()
    ob = probes["outsourcing"]
    assert (ob.answer_days, ob.success_prior) == stats["LV_outsourcing_PL-02"]
    assert ob.priority_score == round(max(ob.delta_net_krw, 0) * ob.success_prior / max(ob.answer_days, 1))
    slow = {k: (30.0, v[1]) for k, v in stats.items()}  # 회신이 30일 걸리면 마감(21일) 전 불가
    late = {p.key: p for p in probe_levers(SCIP, case, BuildParams(), slow)}
    assert not late["outsourcing"].answerable_before_deadline and late["overtime"].answerable_before_deadline


# ── 사용자 정의 시나리오 ─────────────────────────────────────────────────────────
def test_custom_scenario_spec_runs_and_ids_are_validated(case):
    half = ScenarioSpec("보호 + 최소 50% 수락", W1, protect_existing=True, min_accept=0.5)
    out = run_scenarios(SCIP, case, BuildParams(), extra_specs={"E": half})
    e = scen(out, "E")
    assert e.accepted_pct["PL-02"] >= 0.5 - 1e-6 and {s.scenario_id for s in out.scenarios} == {"A", "B", "C", "D", "E"}
    for bad in ("A", "Z", "EE", "e"):
        with pytest.raises(ValueError):
            run_scenarios(SCIP, case, BuildParams(), extra_specs={bad: half})
    only = run_scenarios(SCIP, case, BuildParams(), only=("A",))
    assert [s.scenario_id for s in only.scenarios] == ["A"]
    assert PRESETS["A"].protect_existing and not PRESETS["A"].use_consent
