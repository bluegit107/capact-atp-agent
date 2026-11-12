"""BuildModel: 도메인 객체(계약 조항·입찰·설비·레버·가정 해석) -> 솔버 입력. 순수 함수.

원칙
  - **계획 기준은 하나로**: 용량과 부하를 같은 출처(strat_capacity)에서 가져온다. 수주 데이터는 고객 구성비·납기 시점
    패턴에만 쓰고, 규모는 '규모 대응비'(= 용량×가동률 ÷ 수주 기준 주간 수요)로 맞춘다(A05 §3: 대응표·규모 가정 명시).
  - 인간 확정 전 조항은 솔버에 넣지 않는다. 레버는 승인/적용 상태만 기본 포함, 가설은 브랜치(include_hypothetical)에서만.
  - 특근은 기본 0(근무형태 데이터 없음). 특근 레버 회신·승인이 있어야 생긴다.
  - 무시한 레버·가정·규모 대응은 notes 로 드러낸다.
"""
from __future__ import annotations

import math
from datetime import date

from dealcockpit.domain.common import Provenance
from dealcockpit.domain.lever import Lever, LeverStatus, LeverType
from dealcockpit.domain.plant import WEEKS_PER_MONTH
from dealcockpit.domain.solver_input import (
    BidInput,
    CapacityInput,
    DemandGroupInput,
    OptionInput,
    OutsourceInput,
    SolverInput,
    WaiverInput,
    Weights,
)

from .case import BuildParams, Case

INTERPRETATIONS = ("S_MONTH", "S_YEAR", "H3", "CONFIRMED")


class UnsupportedScenario(ValueError):
    pass


def _week_index(week0: date, d: date) -> int:
    return (d - week0).days // 7


def _iso_week_to_index(week0: date, label: str) -> int:
    year, w = label.split("-W")
    return _week_index(week0, date.fromisocalendar(int(year), int(w), 1))


def included_levers(levers: tuple[Lever, ...], include_hypothetical: bool) -> list[Lever]:
    keep = []
    for lv in levers:
        if lv.affects_plan_of_record:
            keep.append(lv)
        elif include_hypothetical and lv.status not in (LeverStatus.REJECTED, LeverStatus.EXPIRED):
            keep.append(lv)
    return keep


def planning_basis(case: Case, p: BuildParams) -> tuple[float, float, str]:
    """(주 기본 능력, 규모 대응비, 설명). 규모 대응비 = 계획 기준 부하 ÷ 수주 기준 주간 수요."""
    line = case.plant.line_for_product(p.product_id)
    d = case.derived
    util = line.utilization_pct / 100.0
    steady = d["orders_weekly_steady"]
    if p.interpretation == "S_MONTH":
        base, note = line.capacity_qty / WEEKS_PER_MONTH, "S_MONTH: strat_capacity 월 능력 × 가동률 = 부하"
    elif p.interpretation == "S_YEAR":
        base, note = line.capacity_qty / 52.0, "S_YEAR: strat_capacity 연 능력 × 가동률 = 부하"
    elif p.interpretation == "H3":
        return d["capacity"]["h3_implied_monthly_capacity"] / WEEKS_PER_MONTH, 1.0, "H3: 실측(배치 로그) 보정 능력, 수주 규모 그대로(교차검증용)"
    elif p.interpretation == "CONFIRMED":
        if not p.confirmed_monthly:
            raise UnsupportedScenario("CONFIRMED 기준에는 confirmed_monthly 가 필요하다")
        base, note = p.confirmed_monthly / WEEKS_PER_MONTH, "CONFIRMED: 담당자 확정 월 능력 × 가동률 = 부하"
    else:
        raise UnsupportedScenario(f"알 수 없는 계획 기준 {p.interpretation} (가능: {INTERPRETATIONS})")
    return base, base * util / steady, note


def material_cost_per_unit(case: Case, usd_krw: float) -> float:
    m = case.derived["material"]
    if m["currency"] == "USD":
        return m["kg_per_ea_2026h1"] * m["usd_per_kg_latest"] * usd_krw
    return m["cost_per_ea_krw_2026h1"]


def build_solver_input(
    case: Case,
    p: BuildParams,
    weights: Weights,
    protect_floor: dict[str, tuple[float, ...]] | None = None,
    min_accept_override: float | None = None,
    apply_consent: bool = True,  # False = 고객 동의 레버를 쓰지 않는 시나리오(A·C: 기존 약속 그대로)
) -> SolverInput:
    notes: list[str] = []
    s = p.settings
    d = case.derived
    horizon = d["horizon_weeks"]
    week0 = date.fromisoformat(d["week0_monday"])
    levers = included_levers(p.levers, p.include_hypothetical)
    clauses = {c.clause_id: c for c in case.clauses if c.usable_by_solver}
    for c in case.clauses:
        if not c.usable_by_solver:
            notes.append(f"조항 {c.clause_id} 인간 확정 전 — 솔버 입력에서 제외")

    base_weekly, scale, basis_note = planning_basis(case, p)
    notes.append(f"계획 기준 {basis_note}; 규모 대응비 {scale:.3f}(수주·배치 규모 수량에 곱함 — 시뮬레이션 결과)")
    line = case.plant.line_for_product(p.product_id)

    # ── 레버 해석 ─────────────────────────────────────────────────────────────
    active_waiver: dict[str, float] = {}
    consent_weeks: dict[str, tuple[int, str]] = {}
    outsourcing: list[OutsourceInput] = []
    ot_cap_pct, ot_mult, ot_start = 0.0, case.plant.premium.overtime_multiplier, 0
    min_accept, max_accept = 0.0, 1.0
    for lv in levers:
        prm = lv.params
        target = str(prm.get("target", ""))
        if lv.type is LeverType.PRICE_ADJUST and target.startswith("clause:"):
            cid = target.split(":", 1)[1]
            if cid in clauses and clauses[cid].kind.value == "price_discount_waiver":
                active_waiver[cid] = abs(float(prm["pct"]))
            else:
                notes.append(f"레버 {lv.lever_id}: 대상 조항 {cid} 없음/미확정 — 무시")
        elif lv.type is LeverType.CAPACITY_DELTA and target.startswith("outsourcing:"):
            if prm.get("product") != p.product_id:
                notes.append(f"레버 {lv.lever_id}: 제품 {prm.get('product')} 는 이번 범위 밖 — 무시")
                continue
            start = max(_iso_week_to_index(week0, prm["available_from_week"]), p.decision_week)
            outsourcing.append(OutsourceInput(
                partner_id=target.split(":", 1)[1], lever_id=lv.lever_id, start_week=start,
                qty_per_week=prm["qty_per_month"] / WEEKS_PER_MONTH, cost_multiplier=prm["unit_cost_multiplier"],
            ))
        elif lv.type is LeverType.CAPACITY_DELTA and target.startswith("overtime:"):
            ot_cap_pct = min(50.0, 100.0 * prm["qty_per_month"] / WEEKS_PER_MONTH / base_weekly)
            ot_mult = prm["unit_cost_multiplier"]
            ot_start = max(_iso_week_to_index(week0, prm["available_from_week"]), p.decision_week)
            notes.append(f"특근 레버 {lv.lever_id}: 월 {prm['qty_per_month']:,} EA(능력의 {ot_cap_pct:.1f}%), W{ot_start:02d}부터, 가공비 ×{ot_mult}")
        elif lv.type is LeverType.TIME_SHIFT and target.startswith("customer:"):
            if apply_consent:
                consent_weeks[target.split(":", 1)[1]] = (max(0, prm["shift_days"] // 7), lv.lever_id)
            else:
                notes.append(f"고객 동의 레버 {lv.lever_id}: 이 시나리오는 기존 약속을 바꾸지 않으므로 사용하지 않음")
        elif lv.type is LeverType.SCOPE_CHANGE and target.startswith("bid:"):
            min_accept = max(min_accept, prm["accepted_pct_min"])
            max_accept = min(max_accept, prm["accepted_pct_max"])
        else:
            notes.append(f"레버 {lv.lever_id}({lv.type.value}): 미지원 — 무시")
    if p.overtime_cap_pct is not None:
        ot_cap_pct, ot_start = p.overtime_cap_pct, 0
        notes.append(f"what-if: 특근 상한 {p.overtime_cap_pct}% 직접 지정(확정 계획 아님 — 생산 회신 필요)")
    elif ot_cap_pct == 0:
        notes.append("특근 0(근무형태 데이터 없음 — 특근 레버 회신이 있어야 반영)")

    # ── 수요 그룹 ─────────────────────────────────────────────────────────────
    # S 기준: 주별 기존 부하를 용량×가동률로 평준화하고 고객 구성비는 그 주의 수주 패턴을 따른다.
    #   (수주일+60일 납기 패턴은 이미 출하된 주문이 빠져 앞쪽 주가 비어 보인다 → 그대로 쓰면 가짜 여유 능력이 생긴다)
    # H3: 수주 규모·패턴 그대로(교차검증용).
    raw_due = {gid: list(g["weekly_due"]) for gid, g in d["groups"].items()}
    if p.interpretation == "H3":
        unit_scale = 1.0
    else:
        unit_scale = scale
        load = base_weekly * line.utilization_pct / 100.0
        for t in range(horizon):
            tot = sum(raw_due[gid][t] for gid in raw_due)
            f = load / tot if tot > 0 else 0.0
            for gid in raw_due:
                raw_due[gid][t] *= f
        notes.append(f"주별 기존 부하 = {load:,.0f} EA(용량×가동률)로 평준화, 고객 구성비는 주별 수주 패턴 유지")
    slot_specs = []
    for c in clauses.values():
        if c.kind.value == "fixed_delivery_slot" and c.params.get("product_id") == p.product_id and c.customer_id in raw_due:
            w = _week_index(week0, date.fromisoformat(c.params["slot_date"]))
            window = range(max(0, w - s.slot_window_weeks + 1), w + 1)
            pool = sum(raw_due[c.customer_id][t] for t in window)
            take = min(float(c.params["qty"]) * unit_scale, pool)
            for t in window:  # 편성 물량은 기존 수주의 일부: 직전 납기분에서 비례 차감(이중 계상 방지)
                raw_due[c.customer_id][t] -= take * (raw_due[c.customer_id][t] / pool) if pool else 0.0
            slot_specs.append((c, w, take))
            notes.append(f"{c.customer_id} 편성 물량 {c.params['qty']:,} EA(수주 규모, 계획 단위 {take:,.0f})를 W{w:02d} 고정 납기로 분리 — 직전 {s.slot_window_weeks}주 납기분에서 차감(합성 가정)")

    groups: list[DemandGroupInput] = []
    for gid, g in d["groups"].items():
        cust = [c for c in clauses.values() if c.customer_id == gid]
        late = next((c for c in cust if c.kind.value == "late_penalty"), None)
        term = next((c for c in cust if c.kind.value == "termination_trigger"), None)
        wv = next((c for c in cust if c.kind.value == "price_discount_waiver" and c.clause_id in active_waiver), None)
        if late:
            rate, cap_pct, prov, late_id = late.params["rate_per_day_pct"], late.params["cap_pct_of_order"], late.provenance, late.clause_id
        else:
            rate, cap_pct, prov, late_id = s.default_late_rate_pct_per_day, None, Provenance.ASSUMPTION, None
            notes.append(f"{gid}: 확정된 지체상금 조항 없음 — 기본 {s.default_late_rate_pct_per_day}%/일 가정")
        limits: list[int] = []
        if term:
            tw = max(0, math.floor(term.params["delay_days_gt"] / 7) - s.termination_buffer_weeks)
            limits.append(tw)
            notes.append(f"{gid}: 해지 트리거 {term.params['delay_days_gt']}일 -> 지연 허용 {tw}주(안전 여유 {s.termination_buffer_weeks}주)")
        if late and rate > 0:
            cw = math.floor(cap_pct / rate / 7)
            limits.append(cw)
            notes.append(f"{gid}: 지체상금 상한 {cap_pct}% 는 {cw}주 지연에서 소진 -> 그 안쪽에서만 지연 허용")
        if not term:
            limits.append(s.default_max_late_weeks)
            notes.append(f"{gid}: 해지 조항 없음 -> 기본 하드 지연 한도 {s.default_max_late_weeks}주 가정")
        max_weeks = min(limits)
        waiver = None
        if wv:
            waiver = WaiverInput(clause_id=wv.clause_id, discount_pct=active_waiver[wv.clause_id], grace_weeks=wv.params["extends_grace_days"] // 7)
        if gid in consent_weeks:
            cw_, lid = consent_weeks[gid]
            if waiver is None or cw_ > waiver.grace_weeks:
                waiver = WaiverInput(clause_id=lid, discount_pct=waiver.discount_pct if waiver else 0.0, grace_weeks=cw_)
            max_weeks = max(max_weeks, cw_)
            notes.append(f"{gid}: 고객 동의 레버 {lid} — 최대 {cw_}주 납기 조정 허용(동의 구간 지체상금 없음)")
        groups.append(DemandGroupInput(
            group_id=gid, customer_id=gid, weekly_due=tuple(raw_due[gid]), unit_price_krw=g["unit_price_krw"],
            late_rate_per_day_pct=rate, late_cap_pct=cap_pct, max_late_weeks=max_weeks, penalty_clause_id=late_id,
            hard_clause_id=term.clause_id if term else None, waiver=waiver, penalty_provenance=prov,
        ))
    for c, w, take in slot_specs:
        due = [0.0] * horizon
        due[w] = take
        base = next(gr for gr in groups if gr.customer_id == c.customer_id)
        groups.append(DemandGroupInput(
            group_id=f"{c.customer_id}-SLOT", customer_id=c.customer_id, weekly_due=tuple(due), unit_price_krw=base.unit_price_krw,
            late_rate_per_day_pct=0.0, max_late_weeks=0, hard_clause_id=c.clause_id,
            fixed_slot_compensation_krw=c.params["cancellation_compensation_krw"], penalty_provenance=c.provenance,
        ))

    # 품질위원회가 회수를 의결한 경우: 회수 대상 배치 물량의 대체 생산(같은 라인)
    if p.recall_replacement_week is not None:
        q = d["quality"]
        due = [0.0] * horizon
        units = q["recall_eligible_units"] * unit_scale * s.recall_replacement_ratio
        for t in range(p.recall_replacement_week, min(horizon, p.recall_replacement_week + s.recall_spread_weeks)):
            due[t] = units / s.recall_spread_weeks
        avg_price = sum(g.unit_price_krw for g in groups) / len(groups)
        groups.append(DemandGroupInput(
            group_id="RECALL-REPL", customer_id="회수대체", weekly_due=tuple(due), unit_price_krw=avg_price,
            late_rate_per_day_pct=s.default_late_rate_pct_per_day, max_late_weeks=s.recall_max_late_weeks, penalty_provenance=Provenance.ASSUMPTION,
        ))
        notes.append(f"회수 대체 생산 {units:,.0f} EA(배치 {', '.join(b['batch_id'] for b in q['recall_eligible_batches'])}) W{p.recall_replacement_week:02d}부터 {s.recall_spread_weeks}주 — 품질위원회 의결 전제")

    # ── 설비 옵션: 선행 요건 회신 없으면 가용 시점을 말할 수 없으므로 제외 ─────────────────
    options: list[OptionInput] = []
    for o in case.plant.options:
        if o.option_id in p.excluded_options:
            notes.append(f"{o.option_id}: 투자 승인 거절 — 계산에서 제외")
            continue
        try:
            weeks = o.earliest_start(date.fromisoformat(d["as_of"]), p.requirement_delays)
        except ValueError as e:
            notes.append(f"{o.option_id}: {e}")
            continue
        options.append(OptionInput(option_id=o.option_id, start_week=p.decision_week + weeks, added_weekly=o.added_capacity_qty / 52.0, capex_krw=o.capex_krw))

    capacity = CapacityInput(
        line_id=line.line_id, base_weekly=base_weekly, overtime_cap_pct=ot_cap_pct, overtime_multiplier=ot_mult,
        overtime_start_week=ot_start, unit_cost_krw=d["unit_cost_krw"], conversion_cost_krw=d["conversion_cost_per_ea_krw"],
        # 기준 계획(입찰 없음)도 같은 추가 능력(옵션·외주·특근)을 쓸 수 있다: 추가 능력은 기존 약속이 먼저 쓴다
        # (W05 "매출 기회보다 먼저 확인할 공급 약속"). 그래야 보호 하한이 '가능한 최선의 서비스 수준'이 된다.
        options=tuple(options), outsourcing=tuple(outsourcing),
    )

    if min_accept_override is not None:
        min_accept = max(min_accept, min_accept_override)
        if min_accept > max_accept:
            raise UnsupportedScenario("입찰 최소 수락률이 레버가 허용한 최대 수락률을 넘는다")
    bid_input = None
    if p.with_bid:
        bl = next(l for l in case.bid.lines if l.product_id == p.product_id)
        fx = next(f for f in case.bid.fx if f.label == p.fx_label)
        price = bl.unit_price_fx * fx.usd_krw * (1 + p.price_pressure_pct / 100.0)
        mat = material_cost_per_unit(case, fx.usd_krw)
        recall = p.recall_cost_per_unit_krw
        if recall is None:
            recall = d["quality"]["confirmed_defect_rate_12m"] * price
            notes.append(f"기대 리콜 비용 {recall:,.0f}원/EA = 12개월 확정 결함률 {d['quality']['confirmed_defect_rate_12m']:.2%} × 입찰 단가(가정, 품질이 조정)")
        bid_input = BidInput(
            bid_id=case.bid.bid_id, product_id=p.product_id, weekly_requested=bl.annual_qty / 52.0,
            start_week=_week_index(week0, case.bid.supply_start.effective), ramp_weeks=s.bid_ramp_weeks,
            unit_price_krw=price, unit_cost_krw=mat, recall_cost_per_unit_krw=recall, min_accept=min_accept, max_accept=max_accept,
        )
        notes.append(f"입찰 원가 = 원료 투입 원가(RFP 기준) {mat:,.0f}원/EA = {d['material']['kg_per_ea_2026h1']*1000:.1f}g × {d['material']['usd_per_kg_latest']} USD/kg × 환율 {fx.usd_krw}")
        notes.append("입찰 PL-01(기초) 물량은 이번 범위 밖 — PL-02(더마)만 계산")
    notes.append("완제품 시작 재고 0(재고 데이터 없음, 가정)")

    # 고객 동의가 있는 그룹은 보호 하한을 동의한 주수만큼 늦춘다(동의 범위 안의 지연만 허용)
    floor = None
    if protect_floor is not None:
        floor = {}
        for gid, fl in protect_floor.items():
            wks = consent_weeks.get(gid, (0, None))[0]
            floor[gid] = tuple(fl[t - wks] if t - wks >= 0 else 0.0 for t in range(horizon)) if wks else fl

    return SolverInput(
        horizon_weeks=horizon, week0_monday=d["week0_monday"], product_id=p.product_id, groups=tuple(groups),
        capacity=capacity, bid=bid_input, weights=weights, protect_floor=floor or {}, notes=tuple(notes),
    )
