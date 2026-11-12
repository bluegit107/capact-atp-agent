"""워크스페이스: 한 의사결정 건(C-201 검토)의 업무 상태 전체. 저장 단위이자 낙관적 락 단위.

상태 전이는 application/negotiation.py 의 유스케이스만 수행하고, 매 전이마다 감사 이벤트를 남긴다.
"""
from __future__ import annotations

from typing import Literal

from pydantic import Field

from .approval import DEFAULT_POLICY, ApprovalPolicy, ApprovalRequest
from .assumption import Assumption
from .common import Frozen
from .lever import Lever
from .parameters import DEFAULT_REGISTRY, ParameterRegistry
from .plan import PlanOfRecord
from .workflow import InfoRequest, Reply, Task


class RequirementReply(Frozen):
    """설비 옵션 선행 요건(품질 처방 재검증·구매 M-B 재계약)의 회신 상태."""

    status: Literal["ACCEPTED", "REJECTED"]
    weeks: int | None = None
    note: str = ""


class Workspace(Frozen):
    workspace_id: str = Field(pattern=r"^WS-[A-Za-z0-9\-]+$")
    version: int = Field(default=0, ge=0)  # 저장소가 관리(낙관적 락)
    as_of: str
    today: str  # 업무 시계(모의). 문의 만료 판정에 사용
    levers: dict[str, Lever] = {}
    lever_topics: dict[str, str] = {}  # lever_id -> 회신 정책 주제
    assumptions: dict[str, Assumption] = {}
    requests: dict[str, InfoRequest] = {}
    request_topics: dict[str, str] = {}  # request_id -> 주제
    replies: dict[str, Reply] = {}
    approvals: dict[str, ApprovalRequest] = {}
    tasks: dict[str, Task] = {}
    plan: PlanOfRecord | None = None
    plan_stale: bool = False  # 조건 변경으로 확정 계획 재검토 필요
    policy: ApprovalPolicy = DEFAULT_POLICY
    registry: ParameterRegistry = DEFAULT_REGISTRY
    requirement_replies: dict[str, RequirementReply] = {}
    confirmed_monthly: float | None = None  # AS-001 확정값(생산기술 회신)
    fx_label: str = "as_of"
    price_pressure_pct: float = 0.0
    recall_replacement_week: int | None = None
    submitted_scenario: str | None = None
    # 승인은 '이 상신 건'에만 유효하다: 상신 시점의 승인 요청 ID 와 입력 해시에 묶는다(이전 상신의 승인 재사용 금지)
    submission_approval_ids: tuple[str, ...] = ()
    submission_input_hash: str | None = None
    excluded_options: tuple[str, ...] = ()  # 투자 승인이 거절된 설비 옵션(다시 승인되기 전까지 계산에서 제외)
    last_output_hash: str | None = None
    counters: dict[str, int] = {}
    bidflow: dict | None = None  # W05 입찰 건 원장(dealcockpit.w05.flow.BidLedger 직렬화). 같은 저장 단위·낙관적 락을 쓴다

    def next_id(self, prefix: str) -> tuple["Workspace", str]:
        n = self.counters.get(prefix, 0) + 1
        return self.model_copy(update={"counters": {**self.counters, prefix: n}}), f"{prefix}-{n:03d}"
