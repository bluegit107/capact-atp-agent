"""병목 프로브: 협상 카드(레버) 하나씩 효과를 넣어 다시 풀고, 그 가치·손익분기선·문의 우선순위를 계산한다.

가치 평가는 항상 결정론 솔버다(LLM 은 쓰지 않는다). 프로브 레버는 비용 0 또는 1에 가까운 배수로 넣어
'그 능력/양해가 공짜라면 얼마의 가치인가'를 재고, 그 가치를 단위당으로 나눠 **협상 한계선(break-even)** 을 만든다.
  - 외주: 단위당 가치 / 전체 단위원가 + 1 = 이익이 남는 최대 외주 단가 배수
  - 특근: 단위당 가치 / 가공비 + 1 = 이익이 남는 최대 특근 할증 배수
  - 고객 양해: 1주당 가치 = 양해 대가(단가 인하·보상)로 줄 수 있는 상한
  - 설비 옵션 요건 회신(품질·구매): 회신을 받는 것의 가치, 1주 빨라질 때의 가치
  - 부분 낙찰 허용 확인(바이어): 전량 일괄만 허용될 때 잃는 가치
문의 우선순위 = 가치 × 성공 가능성(사전값) ÷ 회신 소요일, 그리고 입찰 마감 전에 회신이 가능한지.
성공 가능성·회신 소요일 사전값은 모의 회신 정책표에서 오는 **가정**이다(SIMULATED).
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date, timedelta

from dealcockpit.domain.common import Provenance, Role
from dealcockpit.domain.lever import Lever, LeverStatus, LeverType

from .case import BuildParams, Case
from .ports import SolverPort
from .scenarios import run_scenarios

ASSUMED_REQ_DELAYS = {"formula_revalidation": 6, "M-B_recontract": 4}  # 회신 전 가치 추정용 가정(모의 정책표의 조건부·역제안 값)
BOARD_BUFFER_DAYS = 3  # 회신 후 이사회 상신까지 필요한 여유(가정)
INTERNAL_DEFAULT = (2.0, 0.5)  # 사내(생산) 문의의 회신일·성공 가능성 사전값(가정)


@dataclass(frozen=True)
class LeverProbe:
    key: str
    label: str
    lever_type: str
    owner_role: str
    counterparty: str
    topic: str | None
    delta_net_krw: int
    delta_accept_pp: float
    units_used: int
    value_per_unit_krw: float | None
    breakeven: dict
    answer_days: float
    success_prior: float
    priority_score: float
    answerable_before_deadline: bool
    note: str


def _iso_label(week0: date, idx: int) -> str:
    iso = (week0 + timedelta(weeks=idx)).isocalendar()
    return f"{iso.year}-W{iso.week:02d}"


def _probe_lever(lid: str, type_: LeverType, params: dict) -> Lever:
    return Lever(lever_id=lid, type=type_, status=LeverStatus.HYPOTHESIS, params=params, owner_role=Role.PRODUCTION,
                 counterparty="(프로브)", confidence=0.5, provenance=Provenance.DERIVED)


def _run(solver: SolverPort, case: Case, p: BuildParams, sid: str):
    out = run_scenarios(solver, case, p, only=(sid,))
    s = next(x for x in out.scenarios if x.scenario_id == sid)
    return s


def _net(s) -> int:
    return s.financials.net_krw if s.infeasibility is None else 0  # 불가능 안은 실행할 수 없으므로 가치 0


def probe_levers(
    solver: SolverPort, case: Case, params: BuildParams, reply_stats: dict[str, tuple[float, float]] | None = None
) -> list[LeverProbe]:
    stats = reply_stats or {}
    d = case.derived
    week0 = date.fromisoformat(d["week0_monday"])
    deadline_days = case.bid.days_to_deadline
    base_p = replace(params, include_hypothetical=True)
    a0 = _run(solver, case, base_p, "A")
    net0, acc0 = _net(a0), 100.0 * a0.accepted_pct[params.product_id]
    out: list[LeverProbe] = []

    def stat(topic: str | None) -> tuple[float, float]:
        return stats.get(topic, INTERNAL_DEFAULT) if topic else INTERNAL_DEFAULT

    def add(key, label, ltype, owner, cp, topic, s, units, vpu, be, note, ans=None, prior=None):
        days, pr = stat(topic)
        days = ans if ans is not None else days
        pr = prior if prior is not None else pr
        dnet = _net(s) - net0
        out.append(LeverProbe(
            key, label, ltype, owner, cp, topic, int(dnet), round(100.0 * s.accepted_pct[params.product_id] - acc0, 2), int(units),
            round(vpu, 1) if vpu is not None else None, be, days, pr, round(max(dnet, 0) * pr / max(days, 1.0)),
            days <= deadline_days - BOARD_BUFFER_DAYS, note,
        ))

    # 1) 외주 월 5만 EA(공급사 리드타임 4주 가정) — 공짜라면 얼마의 가치인가 → 최대 외주 단가 배수
    start = _iso_label(week0, params.decision_week + 4)
    lv = _probe_lever("LV-901", LeverType.CAPACITY_DELTA, dict(target="outsourcing:PROBE", product=params.product_id, qty_per_month=50000,
                                                             available_from_week=start, unit_cost_multiplier=1.000001))
    s = _run(solver, case, replace(base_p, levers=(*params.levers, lv)), "A")
    units = s.kpis.get("outsourced_units", 0)
    vpu = (_net(s) - net0) / units if units else None
    be = {"max_unit_cost_multiplier": round(1 + vpu / d["unit_cost_krw"], 3)} if vpu else {}
    add("outsourcing", "외주 월 5만 EA(더마)", "capacity_delta", Role.PRODUCTION.value, "외주사", "LV_outsourcing_PL-02", s, units, vpu, be,
        "단가 배수가 이 값보다 낮으면 외주가 이익. 품질 심사(M-B 처방 이전)·리콜 책임은 별도")

    # 2) 특근 월 3만 EA(2주 뒤부터) → 최대 특근 할증 배수
    lv = _probe_lever("LV-902", LeverType.CAPACITY_DELTA, dict(target="overtime:L-02", product=params.product_id, qty_per_month=30000,
                                                             available_from_week=_iso_label(week0, params.decision_week + 2), unit_cost_multiplier=1.0))
    s = _run(solver, case, replace(base_p, levers=(*params.levers, lv)), "A")
    units = s.kpis.get("overtime_units", 0)
    vpu = (_net(s) - net0) / units if units else None
    be = {"max_overtime_multiplier_on_conversion_cost": round(1 + vpu / d["conversion_cost_per_ea_krw"], 2)} if vpu else {}
    add("overtime", "특근 월 3만 EA(L-02)", "capacity_delta", Role.PRODUCTION.value, "생산(근무형태·인력)", None, s, units, vpu, be,
        "근무형태·주 52시간 한도 확인 필요(데이터 없음)")

    # 3) C-101 납기 양해 14일(B 시나리오에서만 반영) → 1주당 가치 = 양해 대가 상한
    lv = _probe_lever("LV-903", LeverType.TIME_SHIFT, dict(target="customer:C-101", shift_days=14))
    s = _run(solver, case, replace(base_p, levers=(*params.levers, lv)), "B")
    dnet = _net(s) - net0
    add("consent_C-101", "C-101 납기 양해 2주", "time_shift", Role.SALES.value, "C-101", "C101_discount_for_delay", s, 0, None,
        {"value_per_week_krw": int(dnet / 2), "max_concession_krw": int(dnet)},
        "양해 대가(단가 인하·보상)가 이 금액보다 작아야 이익. C-101 발주 감소(이탈 신호) 맥락 고려")

    # 4) 설비 옵션 요건 회신(품질 처방 재검증 + 구매 M-B 재계약)
    have = all(k in params.requirement_delays for k in ASSUMED_REQ_DELAYS)
    req = dict(params.requirement_delays) if have else dict(ASSUMED_REQ_DELAYS)
    with_req = _run(solver, case, replace(base_p, requirement_delays=req), "A")
    faster = {k: max(0, v - 1) for k, v in req.items()}
    s_fast = _run(solver, case, replace(base_p, requirement_delays=faster), "A")
    q_days, q_prior = stat("OPT-02_formula_revalidation")
    m_days, m_prior = stat("MB_recontract")
    per_week = _net(s_fast) - _net(with_req)
    if have:
        add("opt02_faster", "OPT-02 요건 1주 단축(품질·구매)", "requirement_reply", f"{Role.QUALITY.value}·{Role.PROCUREMENT.value}",
            "품질팀·V-205", "OPT-02_formula_revalidation", s_fast, 0, None, {"value_per_week_faster_krw": int(per_week)},
            "요건 회신은 받았음 — 일정 단축의 가치", ans=max(q_days, m_days), prior=q_prior * m_prior)
    else:
        add("opt02_replies", "OPT-02 요건 회신 받기(품질 재검증·M-B 재계약)", "requirement_reply", f"{Role.QUALITY.value}·{Role.PROCUREMENT.value}",
            "품질팀·V-205", "OPT-02_formula_revalidation", with_req, 0, None,
            {"assumed_delays_weeks": req, "value_per_week_faster_krw": int(per_week)},
            f"회신 전 가치 추정은 요건 지연을 {req}주로 가정 — 회신이 오면 이 값으로 대체", ans=max(q_days, m_days), prior=q_prior * m_prior)

    # 5) 부분 낙찰 허용 확인(바이어) — 전량 일괄만 허용되면 잃는 가치
    lv = _probe_lever("LV-905", LeverType.SCOPE_CHANGE, dict(target="bid:C-201", accepted_pct_min=1.0, accepted_pct_max=1.0))
    s = _run(solver, case, replace(base_p, levers=(*params.levers, lv)), "A")
    at_stake = net0 - _net(s)
    add("partial_award", "C-201 부분 낙찰 허용 확인", "scope_change", Role.SALES.value, "C-201 바이어", "C201_partial_award", a0, 0, None,
        {"value_at_stake_krw": int(at_stake), "all_or_nothing_feasible": s.infeasibility is None},
        "전량 일괄만 허용되면 현재 능력으로는 " + ("수행 가능" if s.infeasibility is None else "수행 불가 → 입찰 포기 또는 능력 확보 필요"))
    # 이 프로브의 '가치'는 잃지 않기 위한 확인의 가치다
    last = out[-1]
    days, pr = stat("C201_partial_award")
    out[-1] = replace(last, delta_net_krw=int(at_stake), priority_score=round(max(at_stake, 0) * pr / max(days, 1.0)))

    return sorted(out, key=lambda x: x.priority_score, reverse=True)
