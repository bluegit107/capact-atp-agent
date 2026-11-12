"""승인 권한 레지스트리와 승인 요청.

원칙 (사용자 결정 2026-11-09)
  - 권한표는 플랫폼 데이터다. **정책관리자 역할의 사람만** 추가·수정한다. AI·자동화(actor_kind != human)는 변경 경로가 없다.
  - 모든 변경은 버전을 올리고 사유·변경자·시각을 남긴다(변경 이력은 지워지지 않는다).
  - 승인 요청은 **요청 시점의 정책 버전에 묶인다**. 정책을 바꿔도 이미 만든 요청의 승인 주체는 소급 변경되지 않는다.
"""
from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import Field, model_validator

from .common import Frozen, Provenance, Role


class UnknownApprovalAction(KeyError):
    pass


class PermissionDenied(PermissionError):
    pass


class ApprovalRule(Frozen):
    action: str = Field(min_length=1, pattern=r"^[A-Za-z0-9_\-]+$")
    approver: Role
    provenance: Provenance
    basis: str = Field(min_length=1)  # 근거 문서/결정 설명


class PolicyChange(Frozen):
    version: int = Field(ge=2)  # 이 변경으로 만들어진 정책 버전
    action: str
    before: Role | None  # None = 신규 추가
    after: Role
    actor: str = Field(min_length=1)
    actor_role: Role
    actor_kind: Literal["human"]
    reason: str = Field(min_length=5)
    at: datetime


# 업무 승인 주체가 될 수 없는 역할: 심사자(데모 열람)·정책관리자(권한표 관리만)
_NON_APPROVER_ROLES = frozenset({Role.REVIEWER, Role.POLICY_ADMIN})


class ApprovalPolicy(Frozen):
    version: int = Field(ge=1)
    rules: tuple[ApprovalRule, ...]
    history: tuple[PolicyChange, ...] = ()

    @model_validator(mode="after")
    def _consistent(self) -> "ApprovalPolicy":
        actions = [r.action for r in self.rules]
        if len(set(actions)) != len(actions):
            raise ValueError("승인 행위 중복")
        if any(r.approver in _NON_APPROVER_ROLES for r in self.rules):
            raise ValueError("심사자·정책관리자는 업무 승인 주체가 될 수 없다")
        if self.history and self.history[-1].version != self.version:
            raise ValueError("정책 버전과 마지막 변경 이력이 맞지 않는다")
        return self

    def rule_for(self, action: str) -> ApprovalRule:
        for r in self.rules:
            if r.action == action:
                return r
        raise UnknownApprovalAction(f"정의되지 않은 승인 행위: {action} (정책 v{self.version})")

    def upsert_rule(
        self,
        action: str,
        approver: Role,
        *,
        actor: str,
        actor_role: Role,
        actor_kind: str,
        reason: str,
        at: datetime,
        basis: str | None = None,
    ) -> "ApprovalPolicy":
        """사람(정책관리자)만 권한표를 바꾼다. 새 버전의 정책을 돌려준다(원본 불변)."""
        if actor_kind != "human":
            raise PermissionDenied("권한표는 사람만 변경할 수 있다 (AI·자동화 불가)")
        if actor_role is not Role.POLICY_ADMIN:
            raise PermissionDenied(f"권한표 변경은 {Role.POLICY_ADMIN} 역할만 가능 (요청자 {actor_role})")
        before = next((r for r in self.rules if r.action == action), None)
        if before is not None and before.approver is approver:
            raise ValueError("변경 사항 없음")
        new_rule = ApprovalRule(action=action, approver=approver, provenance=Provenance.HUMAN_INPUT,
                                basis=basis or f"정책관리자 {actor} 변경: {reason}")
        rules = tuple(new_rule if r.action == action else r for r in self.rules) if before else (*self.rules, new_rule)
        change = PolicyChange(version=self.version + 1, action=action, before=before.approver if before else None,
                              after=approver, actor=actor, actor_role=actor_role, actor_kind="human", reason=reason, at=at)
        return ApprovalPolicy(version=self.version + 1, rules=rules, history=(*self.history, change))


# 초기 권한표. SOURCE 는 approval_authority_policy.md 에서 도출, HUMAN_INPUT 은 사용자 결정.
DEFAULT_POLICY = ApprovalPolicy(
    version=1,
    rules=(
        ApprovalRule(action="bid_participation", approver=Role.CEO_BOARD, provenance=Provenance.SOURCE,
                     basis="approval_authority_policy.md: 입찰 참여·투자 승인 = CEO/이사회"),
        ApprovalRule(action="capex_investment", approver=Role.CEO_BOARD, provenance=Provenance.SOURCE,
                     basis="approval_authority_policy.md: 입찰 참여·투자 승인 = CEO/이사회"),
        ApprovalRule(action="price_renegotiation_C-101", approver=Role.SALES_EXEC, provenance=Provenance.SOURCE,
                     basis="approval_authority_policy.md: C-101 재협상/판가 = 영업 임원"),
        ApprovalRule(action="quality_recall", approver=Role.QUALITY, provenance=Provenance.SOURCE,
                     basis="approval_authority_policy.md: 품질 회수 결정 = 품질위원회"),
        ApprovalRule(action="outsourcing_contract", approver=Role.CEO_BOARD, provenance=Provenance.HUMAN_INPUT,
                     basis="원본 정책에 외주 계약 규정 없음(전수 확인) -> 사용자 결정(2026-11-09): 플랫폼 권한표 기본값 CEO/이사회, 정책관리자가 언제든 변경"),
        ApprovalRule(action="customer_schedule_change", approver=Role.SALES_EXEC, provenance=Provenance.ASSUMPTION,
                     basis="기존 고객 납기 변경(고객 동의 기반) — 'C-101 재협상/판가 = 영업 임원'에 준함(가정, 정책관리자 변경 가능)"),
        ApprovalRule(action="overtime_plan", approver=Role.PRODUCTION, provenance=Provenance.ASSUMPTION,
                     basis="원본 정책에 특근 규정 없음 — 생산 책임자 승인(가정, 정책관리자 변경 가능)"),
    ),
)

# 하위 호환: S4 에서 DB 에 저장된 현재 정책을 주입하도록 바꾼다.
APPROVAL_RULES = DEFAULT_POLICY.rules


def rule_for(action: str, policy: ApprovalPolicy = DEFAULT_POLICY) -> ApprovalRule:
    return policy.rule_for(action)


class ApprovalStatus(StrEnum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"


class ApprovalRequest(Frozen):
    approval_id: str = Field(pattern=r"^AP-\d{3,}$")
    action: str
    approver: Role
    policy_version: int = Field(default=1, ge=1)  # 요청 시점의 정책 버전(소급 변경 없음)
    scenario_id: str | None = None
    status: ApprovalStatus = ApprovalStatus.PENDING
    decided_by: Role | None = None
    note: str | None = None

    @classmethod
    def request(
        cls, approval_id: str, action: str, scenario_id: str | None = None, policy: ApprovalPolicy = DEFAULT_POLICY
    ) -> "ApprovalRequest":
        return cls(approval_id=approval_id, action=action, approver=policy.rule_for(action).approver,
                   policy_version=policy.version, scenario_id=scenario_id)

    def decide(self, by: Role, approve: bool, note: str | None = None) -> "ApprovalRequest":
        """심사자(REVIEWER)의 역할 전환은 승인 권한이 아니다. 승인 주체 역할만 결정할 수 있다."""
        if self.status is not ApprovalStatus.PENDING:
            raise ValueError(f"{self.approval_id}: 이미 {self.status} 상태")
        if by is not self.approver:
            raise PermissionDenied(f"{self.action}: 승인 주체는 {self.approver} (요청자 {by})")
        return self.model_copy(
            update={"status": ApprovalStatus.APPROVED if approve else ApprovalStatus.REJECTED, "decided_by": by, "note": note}
        )

    def reroute(self, policy: ApprovalPolicy, *, actor_role: Role, actor_kind: str) -> "ApprovalRequest":
        """대기 중 요청을 새 정책으로 재지정하는 것은 정책관리자의 명시적 조작으로만 한다."""
        if actor_kind != "human" or actor_role is not Role.POLICY_ADMIN:
            raise PermissionDenied("재지정은 정책관리자(사람)만 가능")
        if self.status is not ApprovalStatus.PENDING:
            raise ValueError("결정된 요청은 재지정할 수 없다")
        return self.model_copy(update={"approver": policy.rule_for(self.action).approver, "policy_version": policy.version})
