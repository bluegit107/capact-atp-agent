"""승인 권한 레지스트리: 사람(정책관리자)만 변경, 버전·이력, 요청은 정책 버전에 묶임."""
from __future__ import annotations

from datetime import datetime

import pytest
from pydantic import ValidationError

from dealcockpit.domain.approval import (
    DEFAULT_POLICY,
    ApprovalPolicy,
    ApprovalRequest,
    ApprovalStatus,
    PermissionDenied,
    UnknownApprovalAction,
)
from dealcockpit.domain.common import Provenance, Role

AT = datetime(2026, 11, 9, 10, 0)


def change(policy=DEFAULT_POLICY, action="outsourcing_contract", approver=Role.SALES_EXEC, **over):
    kw = dict(actor="김정책", actor_role=Role.POLICY_ADMIN, actor_kind="human", reason="생산 임원 전결로 위임 결정", at=AT)
    kw.update(over)
    return policy.upsert_rule(action, approver, **kw)


def test_default_policy_reflects_user_decision_for_outsourcing():
    r = DEFAULT_POLICY.rule_for("outsourcing_contract")
    assert r.approver is Role.CEO_BOARD and r.provenance is Provenance.HUMAN_INPUT
    assert DEFAULT_POLICY.rule_for("bid_participation").provenance is Provenance.SOURCE


def test_policy_admin_human_can_change_and_history_is_recorded():
    p2 = change()
    assert p2.version == 2 and p2.rule_for("outsourcing_contract").approver is Role.SALES_EXEC
    h = p2.history[-1]
    assert (h.version, h.before, h.after, h.actor, h.actor_kind) == (2, Role.CEO_BOARD, Role.SALES_EXEC, "김정책", "human")
    assert DEFAULT_POLICY.rule_for("outsourcing_contract").approver is Role.CEO_BOARD  # 원본 정책 불변


@pytest.mark.parametrize("kind", ["ai", "system", "llm"])
def test_ai_or_automation_can_never_change_policy_even_with_admin_role(kind):
    with pytest.raises(PermissionDenied):
        change(actor_kind=kind)


@pytest.mark.parametrize("role", [Role.CEO_BOARD, Role.SALES_EXEC, Role.REVIEWER, Role.SALES])
def test_only_policy_admin_role_can_change(role):
    with pytest.raises(PermissionDenied):
        change(actor_role=role)


def test_change_requires_reason_and_real_difference():
    with pytest.raises(ValidationError):
        change(reason="x")
    with pytest.raises(ValueError):
        change(approver=Role.CEO_BOARD)  # 변경 없음


def test_new_action_can_be_added_and_versions_chain():
    p2 = change(action="line_transfer_PL-01_to_F2", approver=Role.PRODUCTION, reason="기초 F2 이전 승인 주체 신설")
    p3 = change(p2, action="line_transfer_PL-01_to_F2", approver=Role.QUALITY, reason="품질 검증 우선으로 변경")
    assert [h.version for h in p3.history] == [2, 3]
    assert p3.history[0].before is None and p3.history[1].before is Role.PRODUCTION


def test_non_approver_roles_cannot_be_assigned_as_approver():
    for bad in (Role.REVIEWER, Role.POLICY_ADMIN):
        with pytest.raises(ValidationError):
            change(approver=bad)


def test_requests_bind_to_policy_version_and_are_not_retroactively_changed():
    old = ApprovalRequest.request("AP-001", "outsourcing_contract")
    p2 = change()
    new = ApprovalRequest.request("AP-002", "outsourcing_contract", policy=p2)
    assert (old.approver, old.policy_version) == (Role.CEO_BOARD, 1)
    assert (new.approver, new.policy_version) == (Role.SALES_EXEC, 2)
    with pytest.raises(PermissionDenied):
        old.decide(Role.SALES_EXEC, True)  # 정책이 바뀌어도 기존 요청은 v1 주체만 결정


def test_reroute_is_explicit_human_admin_action_only():
    old = ApprovalRequest.request("AP-001", "outsourcing_contract")
    p2 = change()
    with pytest.raises(PermissionDenied):
        old.reroute(p2, actor_role=Role.POLICY_ADMIN, actor_kind="ai")
    with pytest.raises(PermissionDenied):
        old.reroute(p2, actor_role=Role.CEO_BOARD, actor_kind="human")
    moved = old.reroute(p2, actor_role=Role.POLICY_ADMIN, actor_kind="human")
    assert (moved.approver, moved.policy_version) == (Role.SALES_EXEC, 2)
    decided = old.decide(Role.CEO_BOARD, True)
    with pytest.raises(ValueError):
        decided.reroute(p2, actor_role=Role.POLICY_ADMIN, actor_kind="human")
    assert decided.status is ApprovalStatus.APPROVED


def test_policy_admin_cannot_approve_business_actions():
    ap = ApprovalRequest.request("AP-003", "bid_participation")
    with pytest.raises(PermissionDenied):
        ap.decide(Role.POLICY_ADMIN, True)


def test_unknown_action_still_refused_with_version_in_message():
    with pytest.raises(UnknownApprovalAction, match="v1"):
        DEFAULT_POLICY.rule_for("buy_a_yacht")


def test_tampered_policy_object_is_rejected():
    with pytest.raises(ValidationError):
        ApprovalPolicy(version=1, rules=DEFAULT_POLICY.rules * 2)  # 중복 행위
    p2 = change()
    with pytest.raises(ValidationError):
        ApprovalPolicy(version=5, rules=p2.rules, history=p2.history)  # 버전·이력 불일치
