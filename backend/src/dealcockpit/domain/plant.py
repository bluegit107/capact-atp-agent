from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import Field, model_validator

from .common import Frozen, Provenance, SourceRef

WEEKS_PER_MONTH = 4.333


class CapacityUnit(Frozen):
    """AS-001. 확정 전에는 단정하지 않는다."""

    period: Literal["per_month", "per_year", "UNKNOWN"] = "UNKNOWN"
    status: Literal["UNCONFIRMED", "CONFIRMED"] = "UNCONFIRMED"
    assumption_id: str = Field(default="AS-001", pattern=r"^AS-\d{3}$")


class Line(Frozen):
    line_id: str
    plant_id: str
    product_family: str
    capacity_qty: int = Field(gt=0)
    utilization_pct: float = Field(ge=0, le=100)
    capacity_unit: CapacityUnit = CapacityUnit()
    overtime_cap_pct: float = Field(default=0, ge=0, le=50)
    provenance: Provenance = Provenance.SOURCE
    source_ref: SourceRef | None = None

    def weekly_capacity(self, period: Literal["per_month", "per_year"] | None = None) -> float:
        """주 단위 용량. 단위가 미확정이면 명시적으로 해석을 넘겨야 한다(해석별 재풀이용)."""
        p = period or self.capacity_unit.period
        if p == "UNKNOWN":
            raise ValueError(f"{self.line_id}: 용량 단위 미확정 — 해석(period)을 명시하라")
        return self.capacity_qty / (WEEKS_PER_MONTH if p == "per_month" else 52.0)


class Material(Frozen):
    material_id: str
    supplier_id: str
    exclusive: bool
    lead_time_weeks: int = Field(ge=0)
    moq_kg: float = Field(ge=0)
    kg_per_unit: float | None = Field(default=None, ge=0)  # 미확인이면 None (BOM 없음)
    provenance: Provenance


class CapacityOption(Frozen):
    option_id: str
    kind: Literal["expansion", "f2_conversion"]
    target_line: str
    capex_krw: int = Field(ge=0)
    lead_time_weeks: int = Field(gt=0)
    added_capacity_qty: int = Field(gt=0)  # 원본: '연간' 생산능력(data_dictionary)
    requires: tuple[str, ...] = ()  # 예: formula_revalidation, M-B_recontract
    provenance: Provenance = Provenance.SOURCE
    source_ref: SourceRef | None = None

    def earliest_start(self, decided_on: date, requirement_delay_weeks: dict[str, int] | None = None) -> int:
        """결정일 기준 가용까지 걸리는 주수. 선행 요건(품질 재검증 등)의 지연은 회신으로 들어온다.

        요건은 리드타임과 병렬로 진행된다고 가정하지 않는다(보수적): 지연 중 최댓값을 더한다.
        """
        delays = requirement_delay_weeks or {}
        missing = [r for r in self.requires if r not in delays]
        if missing:
            raise ValueError(f"{self.option_id}: 선행 요건 미확인 {missing} — 확인 요청 필요")
        return self.lead_time_weeks + (max(delays[r] for r in self.requires) if self.requires else 0)


class OutsourcingPartner(Frozen):
    partner_id: str
    product_id: str
    max_qty_per_month: int = Field(gt=0)
    lead_time_weeks: int = Field(ge=0)
    unit_cost_multiplier: float = Field(gt=0, le=10)
    min_term_months: int = Field(ge=1)
    quality_risk_note: str
    provenance: Literal[Provenance.SYNTHETIC, Provenance.ASSUMPTION]


class PremiumCosts(Frozen):
    overtime_multiplier: float = Field(ge=1, le=5)
    overtime_max_hours_per_week: int = Field(ge=0, le=60)
    provenance: Provenance = Provenance.ASSUMPTION
    partners: tuple[OutsourcingPartner, ...] = ()


class PlantModel(Frozen):
    as_of: date
    horizon_weeks: int = Field(gt=0, le=104)
    lines: tuple[Line, ...] = Field(min_length=1)
    materials: tuple[Material, ...] = ()
    options: tuple[CapacityOption, ...] = ()
    premium: PremiumCosts
    # "PL-01>PL-04": 시간. 순서 의존 전환시간을 제품군 단위로 단순화(SYNTHETIC)
    changeover_hours: dict[str, float] = {}

    @model_validator(mode="after")
    def _unique_and_consistent(self) -> "PlantModel":
        ids = [l.line_id for l in self.lines]
        if len(set(ids)) != len(ids):
            raise ValueError("line_id 중복")
        line_set = set(ids)
        for o in self.options:
            if o.target_line not in line_set and not o.target_line.startswith("NEW:"):
                raise ValueError(f"{o.option_id}: 알 수 없는 대상 라인 {o.target_line}")
        return self

    def line_for_product(self, product_id: str) -> Line:
        matches = [l for l in self.lines if l.product_family == product_id]
        if len(matches) != 1:
            raise ValueError(f"{product_id}: 라인 {len(matches)}개 (제품당 1라인 가정)")
        return matches[0]
