"""결정 불변성 게이트(AS-001): 가능한 계획 기준마다 같은 시나리오를 풀어 '결정'이 같은지 본다.

같으면 → 가정이 미확정이어도 상신 가능(근거: 불변). 다르면 → 담당자 확정 전까지 상신 차단(`Assumption.blocks_submission`).
결정 = (수락률, 설비·외주 조치 집합, 실행 가능 여부). 수락률은 `invariance_accept_tol_pp` 안의 차이를 같은 결정으로 본다.
"""
from __future__ import annotations

from dataclasses import dataclass, replace

from dealcockpit.domain.assumption import Assumption, AssumptionStatus, EvidenceTest
from dealcockpit.domain.solution import SolverOutput

from .case import BuildParams, Case
from .ports import SolverPort
from .scenarios import run_scenarios

DEFAULT_INTERPRETATIONS = ("S_MONTH", "S_YEAR", "H3")


@dataclass(frozen=True)
class DecisionSignature:
    interpretation: str
    accept_pct: float
    actions: tuple[str, ...]
    feasible: bool


@dataclass(frozen=True)
class InvarianceResult:
    scenario_id: str
    signatures: tuple[DecisionSignature, ...]
    invariant: bool
    reasons: tuple[str, ...]
    tolerance_pp: float


def decision_signature(out: SolverOutput, scenario_id: str, interpretation: str, product_id: str) -> DecisionSignature:
    s = next(x for x in out.scenarios if x.scenario_id == scenario_id)
    return DecisionSignature(interpretation, round(100.0 * s.accepted_pct[product_id], 2), tuple(sorted(s.capacity_actions)), s.infeasibility is None)


def check_invariance(
    solver: SolverPort, case: Case, params: BuildParams, interpretations: tuple[str, ...] = DEFAULT_INTERPRETATIONS, scenario_id: str = "A"
) -> InvarianceResult:
    interps = tuple(interpretations) + (("CONFIRMED",) if params.confirmed_monthly and "CONFIRMED" not in interpretations else ())
    sigs = tuple(
        decision_signature(run_scenarios(solver, case, replace(params, interpretation=i), only=(scenario_id,)), scenario_id, i, params.product_id)
        for i in interps
    )
    tol = params.settings.invariance_accept_tol_pp
    ref = sigs[0]
    reasons = []
    for s in sigs[1:]:
        if s.feasible != ref.feasible:
            reasons.append(f"{ref.interpretation}↔{s.interpretation}: 실행 가능 여부가 다름")
        if s.actions != ref.actions:
            reasons.append(f"{ref.interpretation}↔{s.interpretation}: 조치 {list(ref.actions)} vs {list(s.actions)}")
        if abs(s.accept_pct - ref.accept_pct) > tol:
            reasons.append(f"{ref.interpretation}↔{s.interpretation}: 수락률 {ref.accept_pct:.1f}% vs {s.accept_pct:.1f}% (허용 ±{tol}%p)")
    return InvarianceResult(scenario_id, sigs, not reasons, tuple(reasons), tol)


def gate_capacity_assumption(
    solver: SolverPort, case: Case, params: BuildParams, assumption: Assumption,
    interpretations: tuple[str, ...] = DEFAULT_INTERPRETATIONS, scenario_id: str = "A",
) -> tuple[Assumption, InvarianceResult | None]:
    """확정된 가정은 그대로 둔다(차단 없음). 미확정이면 불변성을 계산해 증거와 함께 기록한다."""
    if assumption.status is AssumptionStatus.CONFIRMED:
        return assumption, None
    res = check_invariance(solver, case, params, interpretations, scenario_id)
    tests = tuple(
        EvidenceTest(
            name=f"결정@{s.interpretation}",
            result=f"수락 {s.accept_pct:.1f}% · 조치 {list(s.actions) or '없음'} · {'실행 가능' if s.feasible else '불가능'}",
            evidence_ref=f"run_scenarios(scenario={scenario_id})",
        )
        for s in res.signatures
    )
    return assumption.model_copy(update={"decision_invariant": res.invariant, "tests": (*assumption.tests, *tests)}), res
