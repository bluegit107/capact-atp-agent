from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import Field, field_validator, model_validator

from .common import Frozen, Provenance, SourceRef


class ClauseKind(StrEnum):
    """닫힌 enum. 목록에 없는 조항은 솔버에 넣지 않고 UNMAPPED 로 사람에게 올린다."""

    LATE_PENALTY = "late_penalty"
    TERMINATION_TRIGGER = "termination_trigger"
    PARTIAL_SHIPMENT = "partial_shipment"
    PRICE_DISCOUNT_WAIVER = "price_discount_waiver"
    FIXED_DELIVERY_SLOT = "fixed_delivery_slot"
    COST_LINKED_PRICING = "cost_linked_pricing"


class LatePenaltyParams(Frozen):
    rate_per_day_pct: float = Field(gt=0, le=5)
    cap_pct_of_order: float = Field(gt=0, le=100)


class TerminationTriggerParams(Frozen):
    delay_days_gt: int = Field(gt=0, le=365)
    scope: Literal["any_single_order", "cumulative"] = "any_single_order"


class PartialShipmentParams(Frozen):
    allowed: bool
    min_first_shipment_pct: float = Field(default=0, ge=0, le=100)


class PriceDiscountWaiverParams(Frozen):
    discount_pct: float = Field(gt=0, le=50)
    extends_grace_days: int = Field(ge=0, le=180)
    waives_penalty: bool = False


class FixedDeliverySlotParams(Frozen):
    slot_date: str  # 납기일(방송 3일 전 고정), ISO date
    cancellation_compensation_krw: int = Field(ge=0)
    product_id: str | None = None
    qty: int | None = Field(default=None, gt=0)


class CostLinkedPricingParams(Frozen):
    enabled: bool


PARAM_MODELS: dict[ClauseKind, type[Frozen]] = {
    ClauseKind.LATE_PENALTY: LatePenaltyParams,
    ClauseKind.TERMINATION_TRIGGER: TerminationTriggerParams,
    ClauseKind.PARTIAL_SHIPMENT: PartialShipmentParams,
    ClauseKind.PRICE_DISCOUNT_WAIVER: PriceDiscountWaiverParams,
    ClauseKind.FIXED_DELIVERY_SLOT: FixedDeliverySlotParams,
    ClauseKind.COST_LINKED_PRICING: CostLinkedPricingParams,
}


class Extraction(Frozen):
    model: str
    confidence: float = Field(ge=0, le=1)
    human_confirmed: bool = False
    confirmed_by: str | None = None


class Clause(Frozen):
    clause_id: str = Field(pattern=r"^CL-[A-Z0-9]+-\d{2}$")
    customer_id: str
    kind: ClauseKind
    params: dict
    hard: bool
    priority: int = Field(default=1, ge=1)
    supersedes: tuple[str, ...] = ()
    evidence: SourceRef
    extraction: Extraction
    provenance: Literal[Provenance.SYNTHETIC, Provenance.SOURCE] = Provenance.SYNTHETIC

    @field_validator("evidence")
    @classmethod
    def _evidence_required(cls, ev: SourceRef) -> SourceRef:
        # 근거 추적성: 원문 인용·위치 없는 조항은 거부 (LLM 오추출 방어 E7)
        if not (ev.doc_id and ev.page and ev.clause and ev.quote):
            raise ValueError("조항은 doc_id/page/clause/quote 근거가 필수")
        return ev

    @model_validator(mode="after")
    def _params_match_kind(self) -> "Clause":
        PARAM_MODELS[self.kind].model_validate(self.params)
        return self

    @property
    def usable_by_solver(self) -> bool:
        """인간 확정 전 조항은 솔버 입력에 넣지 않는다."""
        return self.extraction.human_confirmed
