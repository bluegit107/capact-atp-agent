"""시나리오 실행 조율: 기준(Z) → 보호 하한 → A/B/C/D. 보고 계산은 report.py.

시나리오 정의 (보호는 가중치가 아니라 하드 제약)
  Z 기준: 입찰 없이 기존 약속만 최선 수행(Before). 이 계획의 누적 출하 = 기존 서비스 수준.
  A 보호·부분 입찰: 모든 고객 서비스 수준 유지(하드), 수락률 자유.
  B 동의 기반 재조정: 고객 동의(time_shift 레버) 받은 고객만 동의 주수만큼 늦출 수 있음. 동의 없으면 A 와 같다.
  C 100% 수락: 입찰 전량 + 서비스 수준 유지. 불가능하면 부족량 보고.
  D (참고) 무동의 지연: 보호 없이 위약금·위반 위험을 감수 — 실행안이 아니라 '위반 비용' 비교용.
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass, replace

from dealcockpit.domain.approval import ApprovalPolicy, DEFAULT_POLICY
from dealcockpit.domain.solution import SolverMeta, SolverOutput, canonical_hash
from dealcockpit.domain.solver_input import RawSolution, SolverInput, Weights

from .build_input import UnsupportedScenario, build_solver_input, included_levers
from .case import BuildParams, Case
from .ports import SolverPort
from .report import cum, to_scenario


@dataclass(frozen=True)
class ScenarioSpec:
    """대안의 정의. A/B/C/D 는 기본 템플릿이고, 사용자는 같은 형식으로 자기 시나리오를 만든다."""

    label: str
    weights: Weights
    protect_existing: bool  # 기준 계획의 누적 출하 이하로 떨어지지 않게(하드)
    min_accept: float | None = None
    reference_only: bool = False  # 실행안이 아닌 비교용
    use_consent: bool = False  # 고객 동의(time_shift) 레버로 해당 고객의 납기를 늦출 수 있는가


PresetSpec = ScenarioSpec  # 하위 호환
W1 = Weights(w_customer=1.0, w_profit=1.0, w_premium=1.0)
PRESETS: dict[str, ScenarioSpec] = {
    "A": ScenarioSpec("기존 고객 100% 보호 · 부분 입찰", W1, protect_existing=True),
    "B": ScenarioSpec("고객 동의 기반 납기 재조정", W1, protect_existing=True, use_consent=True),
    "C": ScenarioSpec("입찰 100% 수락 · 특근·외주·증설로 소화", W1, protect_existing=True, min_accept=1.0),
    "D": ScenarioSpec("(참고) 무동의 지연 — 위약·위반 위험 감수", W1, protect_existing=False, reference_only=True),
}
BASELINE_WEIGHTS = W1
_ID = re.compile(r"^[A-Y]$")


def run_scenarios(
    solver: SolverPort,
    case: Case,
    params: BuildParams,
    time_limit_s: float = 10.0,
    policy: ApprovalPolicy = DEFAULT_POLICY,
    extra_specs: dict[str, ScenarioSpec] | None = None,
    only: tuple[str, ...] | None = None,
) -> SolverOutput:
    """기준(Z) + 템플릿/사용자 시나리오. `only` 를 주면 그 ID 만 푼다(프로브·화면 부분 갱신용)."""
    specs: dict[str, ScenarioSpec] = dict(PRESETS)
    for sid, spec in (extra_specs or {}).items():
        if not _ID.match(sid) or sid in PRESETS:
            raise ValueError(f"사용자 시나리오 ID 는 A~D 를 제외한 대문자 한 글자(Z 제외): {sid}")
        if spec.min_accept is not None and not (0 <= spec.min_accept <= 1):
            raise ValueError("min_accept 는 0~1")
        specs[sid] = spec
    if only is not None:
        specs = {k: v for k, v in specs.items() if k in only}
    lever_ids = tuple(lv.lever_id for lv in included_levers(params.levers, params.include_hypothetical))
    t0 = time.perf_counter()
    inputs: list[SolverInput] = []
    raws: list[RawSolution] = []

    base_inp = build_solver_input(case, replace(params, with_bid=False), BASELINE_WEIGHTS, apply_consent=False)
    base_raw = solver.solve(base_inp, time_limit_s)
    baseline = to_scenario("Z", "기준(입찰 없음)", base_inp, base_raw, (), policy)
    inputs.append(base_inp)
    raws.append(base_raw)
    floor = {gid: tuple(cum(ship)) for gid, ship in base_raw.ship.items()} if base_raw.ship else {}

    scenarios, warnings = [], []
    for sid, spec in specs.items():
        try:
            inp = build_solver_input(case, params, spec.weights, protect_floor=floor if spec.protect_existing else None,
                                     min_accept_override=spec.min_accept, apply_consent=spec.use_consent)
        except UnsupportedScenario as e:
            warnings.append(f"시나리오 {sid} 불가: {e}")
            continue
        raw = solver.solve(inp, time_limit_s)
        scenarios.append(to_scenario(sid, spec.label, inp, raw, lever_ids, policy))
        inputs.append(inp)
        raws.append(raw)

    # 입찰 판단은 '기준 대비 증분'으로: 기존 고객의 불가피한 지연 비용(예: 회수 과부하)은 입찰 여부와 무관하다
    scenarios = [
        s.model_copy(update={"kpis": {**s.kpis, "net_vs_baseline_krw": float(s.financials.net_krw - baseline.financials.net_krw)}})
        for s in scenarios
    ]
    if "B" in specs and not any(lv.type.value == "time_shift" for lv in included_levers(params.levers, params.include_hypothetical)):
        warnings.append("B: 고객 동의(time_shift) 회신이 없어 A 와 같은 조건 — 영업이 고객별 양해 요청을 보내야 갈라진다")
    sigs: dict = {}
    for s in scenarios:
        if specs[s.scenario_id].reference_only:
            continue
        sig = (round(s.accepted_pct[params.product_id], 2), s.capacity_actions, round(s.financials.net_krw / 1e8))
        sigs.setdefault(sig, []).append(s.scenario_id)
    for ids in sigs.values():
        if len(ids) > 1:
            warnings.append(f"시나리오 {','.join(ids)} 결과가 사실상 동일 — 이 가정에서는 대안이 갈리지 않는다(억지로 차별화하지 않음)")

    statuses = {r.status for r in raws}
    meta_status = "INFEASIBLE" if "INFEASIBLE" in statuses else ("FEASIBLE" if "FEASIBLE" in statuses else ("OPTIMAL" if statuses == {"OPTIMAL"} else "UNKNOWN"))
    return SolverOutput(
        input_hash=canonical_hash([i.model_dump(mode="json") for i in inputs]),
        solver=SolverMeta(name=solver.name, status=meta_status, wall_ms=int((time.perf_counter() - t0) * 1000)),
        scenarios=tuple(scenarios), baseline=baseline,
        trace=(
            *({"scenario": s.scenario_id, "notes": list(i.notes)} for s, i in zip([baseline, *scenarios], inputs)),
            *({"warning": w} for w in warnings),
        ),
    )
