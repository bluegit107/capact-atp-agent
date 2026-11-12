"""경제성 지표(CP5): RFP 가 요구한 '단가 인하 시나리오별 공헌이익'과 '증설 회수기간'. 솔버와 독립적인 결정론 계산."""
from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date

from .build_input import material_cost_per_unit, planning_basis
from .case import BuildParams, Case

WEEKS_PER_MONTH = 4.333


@dataclass(frozen=True)
class UnitEconomics:
    fx_label: str
    usd_krw: float
    price_cut_pct: float
    price_krw: float
    material_krw: float
    recall_krw: float
    contribution_krw: float
    margin_pct: float


def unit_economics_at(case: Case, params: BuildParams, fx_label: str, cut: float, recall_rate: float | None = None) -> UnitEconomics:
    """한 점(환율 라벨 × 단가 인하율)의 단위 경제성. USD 원료(M-B)는 원가도 환율에 연동된다."""
    bl = next(l for l in case.bid.lines if l.product_id == params.product_id)
    fx = next((f for f in case.bid.fx if f.label == fx_label), None)
    if fx is None:
        raise ValueError(f"알 수 없는 환율 라벨: {fx_label}")
    rate = case.derived["quality"]["confirmed_defect_rate_12m"] if recall_rate is None else recall_rate
    price = bl.unit_price_fx * fx.usd_krw * (1 + cut / 100.0)
    mat = material_cost_per_unit(case, fx.usd_krw)
    rec = rate * price
    c = price - mat - rec
    return UnitEconomics(fx.label, fx.usd_krw, cut, round(price, 1), round(mat, 1), round(rec, 1), round(c, 1), round(100 * c / price, 2))


def unit_economics(case: Case, params: BuildParams, recall_rate: float | None = None) -> list[UnitEconomics]:
    """환율 × 단가 인하 격자(RFP 제시 인하율). 슬라이더의 임의 인하율은 unit_economics_at 으로 계산한다."""
    return [unit_economics_at(case, params, fx.label, cut, recall_rate) for fx in case.bid.fx for cut in case.bid.price_pressure_pct]


def breakeven_price_cut_pct(case: Case, params: BuildParams, fx_label: str = "as_of") -> float:
    """공헌이익이 0 이 되는 단가 인하율(%). 원료원가·리콜 기대비용 기준."""
    bl = next(l for l in case.bid.lines if l.product_id == params.product_id)
    fx = next(f for f in case.bid.fx if f.label == fx_label)
    rate = case.derived["quality"]["confirmed_defect_rate_12m"]
    p0 = bl.unit_price_fx * fx.usd_krw
    mat = material_cost_per_unit(case, fx.usd_krw)
    # p0(1+x)(1-rate) = mat  ->  x = mat / (p0 (1-rate)) - 1
    return round(100.0 * (mat / (p0 * (1 - rate)) - 1.0), 2)


@dataclass(frozen=True)
class OptionPayback:
    option_id: str
    capex_krw: int
    start_week: int | None  # None = 선행 요건 미확인
    usable_monthly_units: float
    monthly_contribution_krw: float
    payback_months_after_start: float | None
    payback_months_from_decision: float | None
    note: str


def option_paybacks(case: Case, params: BuildParams) -> list[OptionPayback]:
    """옵션별 회수기간. 사용 가능량 = min(추가 능력, 입찰 더마 부족분) — 부족분을 넘는 능력은 회수에 기여하지 않는다.
    가용 시점이 빠른 옵션부터 부족분을 채운다(단계 병행 가정)."""
    p = replace(params, with_bid=True)
    base_weekly, _, _ = planning_basis(case, p)
    line = case.plant.line_for_product(p.product_id)
    free_monthly = base_weekly * (1 - line.utilization_pct / 100.0) * WEEKS_PER_MONTH
    bl = next(l for l in case.bid.lines if l.product_id == p.product_id)
    shortage = max(0.0, bl.annual_qty / 12.0 - free_monthly)
    ue = unit_economics_at(case, p, p.fx_label, p.price_pressure_pct)  # 격자 밖 인하율(슬라이더)도 계산
    starts = {}
    for o in case.plant.options:
        try:
            starts[o.option_id] = p.decision_week + o.earliest_start(date.fromisoformat(case.derived["as_of"]), p.requirement_delays)
        except ValueError:
            starts[o.option_id] = None
    order = sorted(case.plant.options, key=lambda o: (starts[o.option_id] is None, starts[o.option_id] or 0, o.lead_time_weeks))
    out, remaining = [], shortage
    for o in order:
        added = o.added_capacity_qty / 12.0
        usable = min(added, remaining)
        remaining -= usable
        monthly = usable * ue.contribution_krw
        after = (o.capex_krw / monthly) if monthly > 0 else None
        s = starts[o.option_id]
        from_dec = (after + (s - p.decision_week) / WEEKS_PER_MONTH) if (after is not None and s is not None) else None
        note = "선행 요건 회신 전 — 가용 시점 미정" if s is None else ("부족분을 넘는 능력은 회수에 기여 안 함" if usable < added else "")
        out.append(OptionPayback(o.option_id, o.capex_krw, s, round(usable), round(monthly), round(after, 1) if after else None,
                                 round(from_dec, 1) if from_dec else None, note))
    return out
