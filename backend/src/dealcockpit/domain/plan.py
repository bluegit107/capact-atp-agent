"""Plan of Record 승격 게이트: 가설이 확정 계획으로 새어 들어가는 경로를 하나로 막는다."""
from __future__ import annotations

from datetime import datetime

from pydantic import Field

from .approval import ApprovalRequest, ApprovalStatus
from .assumption import Assumption
from .common import Frozen
from .lever import Lever
from .solution import Scenario


class PromotionBlocked(Exception):
    def __init__(self, reasons: list[str]):
        super().__init__("; ".join(reasons))
        self.reasons = reasons


class AuditEvent(Frozen):
    """불변 추가형 감사 기록. 역할 전환(대리 수행)은 acting_as_role 로 남긴다."""

    event_id: str
    at: datetime
    actor: str
    actor_role: str
    acting_as_role: str | None = None
    action: str
    object_ref: str
    input_hash: str | None = None


class PlanOfRecord(Frozen):
    version: int = Field(ge=1)
    scenario_id: str
    input_hash: str
    approved_by: tuple[str, ...]  # approval_id 들
    lever_ids: tuple[str, ...] = ()


def promotion_blockers(
    scenario: Scenario,
    approvals: list[ApprovalRequest],
    levers: dict[str, Lever],
    assumptions: list[Assumption],
) -> list[str]:
    reasons: list[str] = []

    # 1) 가정 게이트(결정 불변성): 확정 전이고 결정이 해석에 좌우되면 차단
    for a in assumptions:
        if a.blocks_submission:
            reasons.append(f"가정 {a.assumption_id} 미확정 — 결정이 해석에 좌우될 수 있음")

    # 2) 시나리오가 쓰는 레버는 모두 승인(APPROVED/APPLIED) 상태여야 한다 (가설·요청중 레버 금지)
    for lid in scenario.used_lever_ids:
        lv = levers.get(lid)
        if lv is None:
            reasons.append(f"알 수 없는 레버 {lid}")
        elif not lv.affects_plan_of_record:
            reasons.append(f"레버 {lid} 상태 {lv.status} — 확정 계획에 반영 불가")

    # 3) 필요한 승인이 모두 올바른 주체로 승인되었는가
    for need in scenario.approvals_required:
        matching = [a for a in approvals if a.action == need.action and a.scenario_id == scenario.scenario_id]
        if not matching:
            reasons.append(f"승인 요청 없음: {need.action} ({need.approver})")
        elif not any(a.status is ApprovalStatus.APPROVED for a in matching):
            reasons.append(f"승인 대기/거절: {need.action} ({need.approver})")

    # 4) 불가능 시나리오는 승격 불가
    if scenario.infeasibility is not None:
        reasons.append("불가능(infeasible) 시나리오")
    return reasons


def promote(
    scenario: Scenario,
    input_hash: str,
    approvals: list[ApprovalRequest],
    levers: dict[str, Lever],
    assumptions: list[Assumption],
    current: PlanOfRecord | None = None,
) -> PlanOfRecord:
    reasons = promotion_blockers(scenario, approvals, levers, assumptions)
    if reasons:
        raise PromotionBlocked(reasons)
    approved_ids = tuple(
        a.approval_id for a in approvals if a.scenario_id == scenario.scenario_id and a.status is ApprovalStatus.APPROVED
    )
    return PlanOfRecord(
        version=(current.version + 1) if current else 1,
        scenario_id=scenario.scenario_id,
        input_hash=input_hash,
        approved_by=approved_ids,
        lever_ids=scenario.used_lever_ids,
    )
