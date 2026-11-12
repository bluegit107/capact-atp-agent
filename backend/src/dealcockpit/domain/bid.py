from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import Field, model_validator

from .common import Frozen, Provenance, SourceRef


class FxScenario(Frozen):
    label: str
    usd_krw: float = Field(gt=0)
    provenance: Provenance


class BidLine(Frozen):
    product_id: str
    annual_qty: int = Field(gt=0)  # 입찰서 기준은 연간(원본 rfp_c201.md)
    unit_price_fx: float = Field(gt=0)
    min_accept_pct: float = Field(default=0, ge=0, le=1)
    provenance: Provenance = Provenance.SOURCE
    source_ref: SourceRef | None = None


class SupplyStart(Frozen):
    """입찰 제출 마감과 공급 개시일은 다른 정보다(A05 §4). 모르면 가정으로 명시하고 가정 ID에 묶는다."""

    requested: date | None = None
    assumed: date | None = None
    assumption_id: str | None = Field(default=None, pattern=r"^AS-\d{3}$")

    @model_validator(mode="after")
    def _unknown_requires_assumption(self) -> "SupplyStart":
        if self.requested is None and not (self.assumed and self.assumption_id):
            raise ValueError("요청 공급 개시일이 없으면 assumed 와 assumption_id 가 필요하다")
        return self

    @property
    def effective(self) -> date:
        return self.requested or self.assumed  # type: ignore[return-value]


class BidTerms(Frozen):
    currency: Literal["USD"]
    late_penalty_pct_per_month: float = Field(ge=0, le=100)
    recall_cost_passthrough: bool
    partial_award_allowed: bool | None = None  # None = 미확인 -> 확인 요청 대상


class Bid(Frozen):
    bid_id: str
    as_of: date
    deadline: date
    supply_start: SupplyStart
    fx: tuple[FxScenario, ...] = Field(min_length=1)
    lines: tuple[BidLine, ...] = Field(min_length=1)
    terms: BidTerms
    price_pressure_pct: tuple[float, ...] = (0.0,)

    @model_validator(mode="after")
    def _checks(self) -> "Bid":
        if self.deadline <= self.as_of:
            raise ValueError("deadline 은 as_of 이후여야 한다")
        if self.supply_start.effective < self.deadline:
            raise ValueError("공급 개시일이 입찰 마감보다 빠를 수 없다")
        if any(not (-30 <= p <= 0) for p in self.price_pressure_pct):
            raise ValueError("단가 압력은 -30%~0% 범위")
        if len({l.product_id for l in self.lines}) != len(self.lines):
            raise ValueError("입찰 품목 중복")
        return self

    @property
    def days_to_deadline(self) -> int:
        return (self.deadline - self.as_of).days

    @property
    def open_questions(self) -> tuple[str, ...]:
        """바이어에게 확인해야 할 미확인 항목 (정보 부족 케이스의 출발점)."""
        qs = []
        if self.terms.partial_award_allowed is None:
            qs.append("partial_award_allowed")
        if self.supply_start.requested is None:
            qs.append("supply_start")
        return tuple(qs)
