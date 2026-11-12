"""솔버 입력/원시 결과 (순수 데이터). 솔버 구현은 adapters 에 있고 이 계층은 어떤 솔버도 모른다.

S2 범위: PL-02(더마) 1개 제품·1개 라인(L-02). 시간 버킷은 주 단위, 수량은 EA.
"""
from __future__ import annotations

from pydantic import Field, model_validator

from .common import Frozen, Provenance


class WaiverInput(Frozen):
    """납기 연장 구간(지체상금 면제). C-101 제10조형(단가 인하와 교환) 또는 고객 동의 회신(인하 0%)."""

    clause_id: str  # 근거: 조항 ID 또는 동의 레버 ID
    discount_pct: float = Field(ge=0, le=50)
    grace_weeks: int = Field(ge=0, le=26)


class DemandGroupInput(Frozen):
    group_id: str
    customer_id: str  # 묶음 고객은 "OTHERS"
    weekly_due: tuple[float, ...]  # 주별 납기 도래 수량(EA)
    unit_price_krw: float = Field(gt=0)
    late_rate_per_day_pct: float = Field(ge=0, le=5)
    late_cap_pct: float | None = Field(default=None, gt=0, le=100)
    max_late_weeks: int | None = Field(default=None, ge=0)  # 하드 한도(해지 트리거·고정 납기). 0 = 납기일 고정
    penalty_clause_id: str | None = None
    hard_clause_id: str | None = None
    waiver: WaiverInput | None = None
    fixed_slot_compensation_krw: int | None = None  # 고정 납기 위반 시 보상금(보고용)
    penalty_provenance: Provenance = Provenance.ASSUMPTION  # 계약 근거 없으면 가정 표시

    @model_validator(mode="after")
    def _nonneg(self) -> "DemandGroupInput":
        if any(v < 0 for v in self.weekly_due):
            raise ValueError("weekly_due 는 음수일 수 없다")
        return self


class OptionInput(Frozen):
    option_id: str
    start_week: int = Field(ge=0)  # 이 주부터 가용 (선행 요건이 미확인이면 입력 자체를 만들지 않는다)
    added_weekly: float = Field(gt=0)
    capex_krw: int = Field(ge=0)


class OutsourceInput(Frozen):
    partner_id: str
    lever_id: str | None
    start_week: int = Field(ge=0)
    qty_per_week: float = Field(gt=0)
    cost_multiplier: float = Field(gt=1, le=10)


class CapacityInput(Frozen):
    line_id: str
    base_weekly: float = Field(gt=0)
    overtime_cap_pct: float = Field(ge=0, le=50)
    overtime_multiplier: float = Field(ge=1, le=5)
    overtime_start_week: int = Field(default=0, ge=0)
    unit_cost_krw: float = Field(gt=0)  # 전체 단위원가(COGS/EA): 외주 단가 배수의 기준
    conversion_cost_krw: float = Field(gt=0)  # 가공비/EA(COGS - 원료): 특근 할증의 기준
    options: tuple[OptionInput, ...] = ()
    outsourcing: tuple[OutsourceInput, ...] = ()
    starting_inventory: float = Field(default=0, ge=0)  # 완제품 재고 데이터 없음 -> 0 (가정)


class BidInput(Frozen):
    bid_id: str
    product_id: str
    weekly_requested: float = Field(ge=0)
    start_week: int = Field(ge=0)
    ramp_weeks: int = Field(ge=1)
    unit_price_krw: float = Field(gt=0)  # 환율·단가 압력 반영 후
    unit_cost_krw: float = Field(gt=0)  # RFP 기준: 원료 투입 원가/EA(환율 연동)
    recall_cost_per_unit_krw: float = Field(default=0, ge=0)  # 기대 리콜 비용/EA(리콜 비용 공급자 전가 조건)
    min_accept: float = Field(default=0, ge=0, le=1)
    max_accept: float = Field(default=1, ge=0, le=1)

    @model_validator(mode="after")
    def _order(self) -> "BidInput":
        if self.min_accept > self.max_accept:
            raise ValueError("min_accept > max_accept")
        return self


class Weights(Frozen):
    w_customer: float = Field(ge=0)
    w_profit: float = Field(ge=0)
    w_premium: float = Field(ge=0)


class SolverInput(Frozen):
    horizon_weeks: int = Field(gt=0, le=104)
    week0_monday: str
    product_id: str
    groups: tuple[DemandGroupInput, ...] = Field(min_length=1)
    capacity: CapacityInput
    bid: BidInput | None  # None = 기준(입찰 없음)
    weights: Weights
    # 기존 서비스 수준 보호: 그룹별 누적 출하 하한(기준 계획의 누적 출하). 비어 있으면 보호 없음.
    protect_floor: dict[str, tuple[float, ...]] = {}
    notes: tuple[str, ...] = ()  # 입력 조립 중 발생한 가정·제외 사항(무시한 레버 등)

    @model_validator(mode="after")
    def _lengths(self) -> "SolverInput":
        for g in self.groups:
            if len(g.weekly_due) != self.horizon_weeks:
                raise ValueError(f"{g.group_id}: weekly_due 길이 {len(g.weekly_due)} != horizon {self.horizon_weeks}")
        ids = {g.group_id for g in self.groups}
        for gid, floor in self.protect_floor.items():
            if gid not in ids or len(floor) != self.horizon_weeks:
                raise ValueError(f"protect_floor[{gid}] 가 그룹/길이와 맞지 않음")
        return self


class RawSolution(Frozen):
    status: str  # OPTIMAL / INFEASIBLE / FEASIBLE / UNKNOWN
    wall_ms: int
    solver_name: str
    accept: float
    options_selected: tuple[str, ...]
    base_use: tuple[float, ...]
    overtime: tuple[float, ...]
    option_use: tuple[float, ...]
    outsourced: tuple[float, ...]
    inventory: tuple[float, ...]
    bid_ship: tuple[float, ...]
    ship: dict[str, tuple[float, ...]]
    relaxations: dict[str, float]  # 하드 제약 위반량(EA). 비어 있지 않으면 INFEASIBLE
    objective: float
