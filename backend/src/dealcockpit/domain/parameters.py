"""파라미터 레지스트리: 확정 계획에 쓰이는 조작 가능한 가정값의 단일 출처.

조작 자유도와 통제를 같은 장치로 보장한다.
  - 값마다 단위·범위·출처·담당 역할·체크포인트(CP1~CP7)·근거가 있다.
  - **확정값 변경은 사람만**, 그리고 `editable_by` 역할만 한다. 변경마다 버전·이력(사유·변경자·시각)을 남긴다.
  - AI·자동화는 `ProposedChange`(제안)만 만들 수 있다. 제안은 사람이 승인해야 적용된다.
  - 확정값과 별개로, 사용자는 세션 안에서 what-if 값을 자유롭게 바꿔 볼 수 있다(범위 검증, 결과에 'what-if' 표시).
"""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import Field, model_validator

from .common import Frozen, Provenance, Role

Checkpoint = Literal["CP1", "CP2", "CP3", "CP4", "CP5", "CP6", "CP7"]


class ParameterError(ValueError):
    pass


class ParamSpec(Frozen):
    key: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    label: str
    unit: str
    value: float
    min_value: float
    max_value: float
    integer: bool = False
    provenance: Provenance
    editable_by: tuple[Role, ...] = Field(min_length=1)
    checkpoint: Checkpoint
    basis: str = Field(min_length=1)

    @model_validator(mode="after")
    def _in_range(self) -> "ParamSpec":
        validate_value(self, self.value)
        return self


def validate_value(spec: ParamSpec, value: float) -> float:
    if not (spec.min_value <= value <= spec.max_value):
        raise ParameterError(f"{spec.key}: {value} 는 허용 범위 [{spec.min_value}, {spec.max_value}] {spec.unit} 밖")
    if spec.integer and float(value) != int(value):
        raise ParameterError(f"{spec.key}: 정수만 허용")
    return value


class ParamChange(Frozen):
    version: int = Field(ge=2)
    key: str
    before: float
    after: float
    actor: str
    actor_role: Role
    actor_kind: Literal["human"]
    reason: str = Field(min_length=5)
    at: datetime
    from_proposal: str | None = None  # AI 제안을 사람이 승인해 적용한 경우 제안 ID


class ProposedChange(Frozen):
    """AI·자동화·사람 누구나 만들 수 있는 제안. 적용은 사람이 `ParameterRegistry.apply_proposal` 로만."""

    proposal_id: str = Field(pattern=r"^PC-\d{3,}$")
    key: str
    value: float
    proposer_kind: Literal["ai", "system", "human"]
    rationale: str = Field(min_length=5)


class ParameterRegistry(Frozen):
    version: int = Field(ge=1)
    specs: tuple[ParamSpec, ...]
    history: tuple[ParamChange, ...] = ()

    @model_validator(mode="after")
    def _consistent(self) -> "ParameterRegistry":
        keys = [s.key for s in self.specs]
        if len(set(keys)) != len(keys):
            raise ValueError("파라미터 키 중복")
        if self.history and self.history[-1].version != self.version:
            raise ValueError("레지스트리 버전과 마지막 변경 이력이 맞지 않는다")
        return self

    def spec(self, key: str) -> ParamSpec:
        for s in self.specs:
            if s.key == key:
                return s
        raise ParameterError(f"알 수 없는 파라미터 {key}")

    def value(self, key: str) -> float:
        return self.spec(key).value

    def set(self, key: str, value: float, *, actor: str, actor_role: Role, actor_kind: str, reason: str, at: datetime,
            from_proposal: str | None = None) -> "ParameterRegistry":
        if actor_kind != "human":
            raise PermissionError("확정 파라미터는 사람만 변경할 수 있다 (AI·자동화는 제안만 가능)")
        sp = self.spec(key)
        if actor_role not in sp.editable_by:
            raise PermissionError(f"{key} 변경 권한 없음: {actor_role} (가능: {', '.join(r.value for r in sp.editable_by)})")
        validate_value(sp, value)
        if float(value) == sp.value:
            raise ParameterError("변경 사항 없음")
        new = sp.model_copy(update={"value": float(value), "provenance": Provenance.HUMAN_INPUT})
        change = ParamChange(version=self.version + 1, key=key, before=sp.value, after=float(value), actor=actor,
                             actor_role=actor_role, actor_kind="human", reason=reason, at=at, from_proposal=from_proposal)
        return ParameterRegistry(
            version=self.version + 1, specs=tuple(new if s.key == key else s for s in self.specs), history=(*self.history, change)
        )

    def apply_proposal(self, proposal: ProposedChange, *, actor: str, actor_role: Role, actor_kind: str, at: datetime,
                       reason: str | None = None) -> "ParameterRegistry":
        """제안의 출처와 무관하게 적용 주체는 권한 있는 사람이다."""
        return self.set(proposal.key, proposal.value, actor=actor, actor_role=actor_role, actor_kind=actor_kind,
                        reason=reason or f"제안 {proposal.proposal_id} 승인: {proposal.rationale}", at=at, from_proposal=proposal.proposal_id)

    def what_if(self, overrides: dict[str, float]) -> dict[str, float]:
        """세션 what-if: 범위만 검증하고 레지스트리는 바꾸지 않는다."""
        out = {s.key: s.value for s in self.specs}
        for k, v in overrides.items():
            out[k] = validate_value(self.spec(k), v)
        return out


_PLAN = (Role.PRODUCTION,)
_SALES = (Role.SALES, Role.SALES_EXEC)
_QUAL = (Role.QUALITY,)
DEFAULT_REGISTRY = ParameterRegistry(
    version=1,
    specs=(
        ParamSpec(key="default_late_rate_pct_per_day", label="계약 근거 없는 고객 지체율", unit="%/일", value=0.1, min_value=0.0, max_value=1.0,
                  provenance=Provenance.ASSUMPTION, editable_by=_SALES, checkpoint="CP4", basis="계약서 없는 고객(OTHERS)의 지체상금 가정"),
        ParamSpec(key="default_max_late_weeks", label="해지 조항 없는 고객 지연 한도", unit="주", value=8, min_value=0, max_value=26, integer=True,
                  provenance=Provenance.ASSUMPTION, editable_by=_SALES, checkpoint="CP4", basis="관계 보호를 위한 하드 한도(가정)"),
        ParamSpec(key="termination_buffer_weeks", label="해지 트리거 안전 여유", unit="주", value=1, min_value=0, max_value=4, integer=True,
                  provenance=Provenance.ASSUMPTION, editable_by=_SALES, checkpoint="CP4", basis="주 해상도·일정 변동 대비(가정)"),
        ParamSpec(key="bid_ramp_weeks", label="입찰 공급 램프업", unit="주", value=4, min_value=1, max_value=12, integer=True,
                  provenance=Provenance.ASSUMPTION, editable_by=_PLAN, checkpoint="CP3", basis="신규 품목 생산 안정화 기간(가정)"),
        ParamSpec(key="slot_window_weeks", label="편성 물량 차감 구간", unit="주", value=4, min_value=1, max_value=8, integer=True,
                  provenance=Provenance.ASSUMPTION, editable_by=_SALES, checkpoint="CP4", basis="편성 물량은 직전 N주 납기분의 일부(이중 계상 방지)"),
        ParamSpec(key="recall_spread_weeks", label="회수 대체 생산 분산 기간", unit="주", value=4, min_value=1, max_value=26, integer=True,
                  provenance=Provenance.ASSUMPTION, editable_by=_QUAL, checkpoint="CP3", basis="회수 의결 후 대체 생산 일정(가정)"),
        ParamSpec(key="recall_max_late_weeks", label="회수 대체품 지연 한도", unit="주", value=4, min_value=0, max_value=26, integer=True,
                  provenance=Provenance.ASSUMPTION, editable_by=_QUAL, checkpoint="CP3", basis="회수 대체품 납기 한도(가정)"),
        ParamSpec(key="recall_replacement_ratio", label="회수 물량 중 대체 생산 비율", unit="비율", value=1.0, min_value=0.0, max_value=1.0,
                  provenance=Provenance.ASSUMPTION, editable_by=_QUAL, checkpoint="CP3", basis="시중 재고·폐기분을 제외한 대체 필요분(가정)"),
        ParamSpec(key="invariance_accept_tol_pp", label="결정 불변성 허용 차이", unit="%p", value=5.0, min_value=0.0, max_value=50.0,
                  provenance=Provenance.ASSUMPTION, editable_by=(Role.CEO_BOARD,), checkpoint="CP1",
                  basis="용량 기준이 달라도 수락률 차이가 이 안이면 같은 결정으로 본다"),
    ),
)
