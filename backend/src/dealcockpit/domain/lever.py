from __future__ import annotations

from datetime import date
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import Field, model_validator

from .common import Frozen, Provenance, Role


class LeverType(StrEnum):
    """조합 가능한 7개 프리미티브. 새 거래도 코드 변경 없이 이 중 하나로 표현한다."""

    CAPACITY_DELTA = "capacity_delta"
    TIME_SHIFT = "time_shift"
    PRICE_ADJUST = "price_adjust"
    SCOPE_CHANGE = "scope_change"
    MATERIAL_SUPPLY = "material_supply"
    CLAUSE_AMENDMENT = "clause_amendment"
    DEMAND_SIGNAL = "demand_signal"


class LeverStatus(StrEnum):
    HYPOTHESIS = "HYPOTHESIS"
    REQUESTED = "REQUESTED"
    ACCEPTED = "ACCEPTED"
    COUNTERED = "COUNTERED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"
    APPROVED = "APPROVED"
    APPLIED = "APPLIED"


# 허용 전이. 가설은 확정 계획에 영향을 주지 않으며, 승인(APPROVED) 없이 APPLIED 로 갈 수 없다.
_TRANSITIONS: dict[LeverStatus, frozenset[LeverStatus]] = {
    LeverStatus.HYPOTHESIS: frozenset({LeverStatus.REQUESTED, LeverStatus.REJECTED}),
    LeverStatus.REQUESTED: frozenset(
        {LeverStatus.ACCEPTED, LeverStatus.COUNTERED, LeverStatus.REJECTED, LeverStatus.EXPIRED}
    ),
    LeverStatus.COUNTERED: frozenset(
        {LeverStatus.REQUESTED, LeverStatus.ACCEPTED, LeverStatus.REJECTED, LeverStatus.EXPIRED}
    ),
    LeverStatus.ACCEPTED: frozenset({LeverStatus.APPROVED, LeverStatus.REJECTED}),
    LeverStatus.APPROVED: frozenset({LeverStatus.APPLIED}),
    LeverStatus.REJECTED: frozenset(),
    LeverStatus.EXPIRED: frozenset({LeverStatus.REQUESTED}),  # 재문의
    LeverStatus.APPLIED: frozenset(),
}

# 확정 계획(Plan of Record)에 반영될 수 있는 상태 — 이외 상태는 브랜치(가설) 계산에만 쓴다.
PLAN_ELIGIBLE: frozenset[LeverStatus] = frozenset({LeverStatus.APPROVED, LeverStatus.APPLIED})


class IllegalTransition(ValueError):
    pass


def can_transition(src: LeverStatus, dst: LeverStatus) -> bool:
    return dst in _TRANSITIONS[src]


class CapacityDeltaParams(Frozen):
    target: str = Field(min_length=1)  # 예: "outsourcing:V-OUT-1", "line:L-11", "overtime:L-02"
    product: str
    qty_per_month: int = Field(gt=0)
    available_from_week: str  # ISO 주차 "2026-W36"
    unit_cost_multiplier: float = Field(gt=0, le=10)


class TimeShiftParams(Frozen):
    target: str  # order_id / customer_id / bid
    shift_days: int = Field(ge=-365, le=365)


class PriceAdjustParams(Frozen):
    target: str
    pct: float = Field(ge=-50, le=50)
    waives_penalty: bool = False


class ScopeChangeParams(Frozen):
    target: str
    accepted_pct_min: float = Field(ge=0, le=1)
    accepted_pct_max: float = Field(ge=0, le=1)
    phased: bool = False

    @model_validator(mode="after")
    def _ordered(self) -> "ScopeChangeParams":
        if self.accepted_pct_min > self.accepted_pct_max:
            raise ValueError("accepted_pct_min > accepted_pct_max")
        return self


class MaterialSupplyParams(Frozen):
    material_id: str
    extra_kg: float = Field(gt=0)
    arrives_week: str
    unit_cost_multiplier: float = Field(default=1.0, gt=0, le=10)


class ClauseAmendmentParams(Frozen):
    clause_id: str
    param: str
    new_value: float | int | str | bool


class DemandSignalParams(Frozen):
    customer_id: str
    product: str
    weekly_qty_delta_pct: float = Field(ge=-100, le=100)
    persistent: bool


_PARAM_MODELS: dict[LeverType, type[Frozen]] = {
    LeverType.CAPACITY_DELTA: CapacityDeltaParams,
    LeverType.TIME_SHIFT: TimeShiftParams,
    LeverType.PRICE_ADJUST: PriceAdjustParams,
    LeverType.SCOPE_CHANGE: ScopeChangeParams,
    LeverType.MATERIAL_SUPPLY: MaterialSupplyParams,
    LeverType.CLAUSE_AMENDMENT: ClauseAmendmentParams,
    LeverType.DEMAND_SIGNAL: DemandSignalParams,
}


class ApprovalRequirement(Frozen):
    action: str
    approver: Role


class Lever(Frozen):
    lever_id: str = Field(pattern=r"^LV-\d{3,}$")
    type: LeverType
    params: dict
    status: LeverStatus = LeverStatus.HYPOTHESIS
    owner_role: Role
    counterparty: str
    answer_by: date | None = None
    valid_until: date | None = None
    confidence: float = Field(ge=0, le=1)
    provenance: Literal[Provenance.ASSUMPTION, Provenance.HUMAN_INPUT, Provenance.SIMULATED, Provenance.DERIVED]
    approvals_required: tuple[ApprovalRequirement, ...] = ()

    @model_validator(mode="after")
    def _params_match_type(self) -> "Lever":
        _PARAM_MODELS[self.type].model_validate(self.params)  # kind 별 스키마 강제 (ValidationError 전파)
        return self

    def transition(self, dst: LeverStatus) -> "Lever":
        if not can_transition(self.status, dst):
            raise IllegalTransition(f"{self.lever_id}: {self.status} -> {dst} 불가")
        return self.model_copy(update={"status": dst})

    @property
    def affects_plan_of_record(self) -> bool:
        return self.status in PLAN_ELIGIBLE
