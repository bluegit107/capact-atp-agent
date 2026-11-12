from __future__ import annotations

import hashlib
import json
from typing import Literal

from pydantic import Field, model_validator

from .common import Frozen


def canonical_hash(obj: object) -> str:
    """같은 입력 -> 같은 해시. 키 정렬·공백 제거한 JSON 의 SHA-256."""
    blob = json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)
    return "sha256:" + hashlib.sha256(blob.encode("utf-8")).hexdigest()


class Financials(Frozen):
    incremental_revenue_krw: int
    contribution_krw: int
    penalty_krw: int = Field(ge=0)
    premium_cost_krw: int = Field(ge=0)
    capex_krw: int = Field(ge=0)

    @property
    def net_krw(self) -> int:
        return self.contribution_krw - self.penalty_krw - self.premium_cost_krw - self.capex_krw


class CustomerImpact(Frozen):
    customer_id: str
    orders_delayed: int = Field(ge=0)
    max_delay_days: int = Field(ge=0)
    penalty_krw: int = Field(ge=0)
    evidence_clause_ids: tuple[str, ...] = ()
    termination_risk: bool = False

    @model_validator(mode="after")
    def _traceable(self) -> "CustomerImpact":
        # 근거 추적성: 지연/위험이 있는데 근거 조항이 없으면 산출물로 인정하지 않는다
        if (self.orders_delayed > 0 or self.termination_risk) and not self.evidence_clause_ids:
            raise ValueError(f"{self.customer_id}: 지연/해지 위험에는 evidence_clause_ids 가 필요하다")
        return self


class ScheduleEntry(Frozen):
    week: str = Field(pattern=r"^\d{4}-W\d{2}$")
    line_id: str
    product_id: str
    customer_id: str | None = None  # None = 재고/입찰 물량
    qty: int = Field(ge=0)
    kind: Literal["base", "overtime", "outsourced", "option"]


class RelaxationItem(Frozen):
    constraint: str
    slack: float = Field(gt=0)


class Infeasibility(Frozen):
    min_relaxation: tuple[RelaxationItem, ...] = Field(min_length=1)
    suggested_lever_ids: tuple[str, ...] = ()


class ApprovalNeed(Frozen):
    action: str
    approver: str


class Scenario(Frozen):
    scenario_id: str = Field(pattern=r"^[A-Z]$")
    label: str
    weights: dict[str, float]
    accepted_pct: dict[str, float]
    capacity_actions: tuple[str, ...] = ()
    financials: Financials
    customer_impact: tuple[CustomerImpact, ...] = ()
    schedule: tuple[ScheduleEntry, ...] = ()
    approvals_required: tuple[ApprovalNeed, ...] = ()
    used_lever_ids: tuple[str, ...] = ()
    infeasibility: Infeasibility | None = None
    kpis: dict[str, float] = {}  # 체크포인트 지표(OTIF·해지 여유·입찰 충족률 등)

    @model_validator(mode="after")
    def _pct_range(self) -> "Scenario":
        if any(not (0 <= v <= 1) for v in self.accepted_pct.values()):
            raise ValueError("accepted_pct 는 0~1")
        return self


class SolverMeta(Frozen):
    name: str
    status: Literal["OPTIMAL", "FEASIBLE", "INFEASIBLE", "UNKNOWN"]
    wall_ms: int = Field(ge=0)
    mip_gap: float | None = None


class SolverOutput(Frozen):
    schema_version: Literal["solution/v1"] = "solution/v1"
    input_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    solver: SolverMeta
    scenarios: tuple[Scenario, ...] = Field(min_length=1)
    baseline: Scenario | None = None  # Before: 입찰 없이 기존 약속만 수행한 기준 계획(Z)
    sensitivity: dict[str, float] = {}
    trace: tuple[dict, ...] = ()

    @model_validator(mode="after")
    def _unique_ids(self) -> "SolverOutput":
        ids = [s.scenario_id for s in self.scenarios]
        if len(set(ids)) != len(ids):
            raise ValueError("scenario_id 중복")
        return self
