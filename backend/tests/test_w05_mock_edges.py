"""모의 AI(MockClaude) 경계 사례: 시연 3개 시나리오 밖의 거절·무응답·보류·전부 불가에서도
실제와 같은 실행 루프, 도구 권한, G2 숫자 검증기를 통과하고, 막힌 대안을 선호하지 않는지 확인한다.

모의 모델은 숫자를 쓰지 않는다. 판단 근거는 도구 결과(원장 사진, 위험 표시, 가정 목록)뿐이다.
"""
from __future__ import annotations

import pytest

from dealcockpit.domain.common import Role
from dealcockpit.w05.agent import run_agent
from tests.test_w05_flow import inp, opt, s1_until  # noqa: F401  (fixture 재사용)

R = Role


def _ok(r):
    assert r.status == "COMPLETED", r.errors
    assert r.model.startswith("mock")
    return r.output


def _pref(L, out):
    pid = (out.get("ai_opinion") or {}).get("preferred_option_id")
    return next((o for o in L.options if o.id == pid), None)


def _no_blocked_pref(L, out):
    p = _pref(L, out)
    if p is not None:
        assert p.feasibility == "FEASIBLE" and p.status == "PROPOSED"
        assert not any(f.severity == "BLOCK" for f in L.flags if f.option_id == p.id)


def test_info_gap_has_no_opinion(inp):
    """S3-a: 핵심 정보 없이 대안이 없으면 선호 대안을 만들지 않고 확인요청을 다음 행동으로."""
    f, L = s1_until(inp, 3)
    out = _ok(run_agent(L, inp, "BID_RECEIVED", 1, mode="mock"))
    assert out["ai_opinion"] is None and out["next_actions"][0]["actor_role"] == "영업"


def test_deadline_tick_before_and_after_hold(inp):
    """마감 7일 전 독촉, 마감 경과 후 보류 상태에서도 정상 종료."""
    f, L = s1_until(inp, 3)
    f.tick(L, "2026-07-15", "demo")
    out = _ok(run_agent(L, inp, "DEADLINE_TICK", 2, mode="mock"))
    assert out["next_actions"][0]["action"] == "미회신 확인요청 독촉"
    f.tick(L, "2026-07-22", "demo")
    assert L.status == "ON_HOLD"
    _ok(run_agent(L, inp, "DEADLINE_TICK", 3, mode="mock"))


def test_conditional_blocked_option_is_never_preferred(inp):
    """S2: 납품 개시 앞당김 후 B는 원료 재계약 회신 전이라 BLOCK. 모의 AI는 B를 고르지 않는다."""
    f, L = s1_until(inp, 10)
    f.supersede(L, "supply_start_date", "2026-10-01", "앞당김", "2026-07-08", "sales", R.SALES)
    out = _ok(run_agent(L, inp, "ASSUMPTION_SUPERSEDED", 4, mode="mock"))
    assert out["impact_of_change"]
    _no_blocked_pref(L, out)
    assert _pref(L, out).label != "B"


def test_procurement_refusal(inp):
    """S2-b: 구매가 재계약 불가로 회신. B 불가, 남은 가능한 대안을 고른다."""
    f, L = s1_until(inp, 10)
    f.supersede(L, "supply_start_date", "2026-10-01", "앞당김", "2026-07-08", "sales", R.SALES)
    proc = next(r for r in L.requests if r.assumption_keys == ["mb_recontract"])
    f.send_request(L, proc.id, "2026-07-09", "sales", R.SALES)
    f.answer_request(L, proc.id, {"mb_recontract": {"possible": False}}, "재계약 불가", "2026-07-10", "proc", R.PROCUREMENT)
    out = _ok(run_agent(L, inp, "INFO_REQUEST_ANSWERED", 5, mode="mock"))
    _no_blocked_pref(L, out)
    assert _pref(L, out).label == "C"


def test_capex_rejection_recomposes(inp):
    """S3-b: 설비투자 거절 후 D, E 중 하나를 고르고, 설비안이 있는 대안은 고르지 않는다."""
    f, L = s1_until(inp, 8)
    bidap = next(a for a in L.approvals if a.decision_type == "BID_PARTICIPATION")
    cap = next(a for a in L.approvals if a.decision_type == "CAPEX")
    f.decide(L, bidap.id, "APPROVED", None, "2026-07-06", "ceo", R.CEO_BOARD)
    f.decide(L, cap.id, "REJECTED", "그룹 가용자금 부족", "2026-07-06", "ceo", R.CEO_BOARD)
    out = _ok(run_agent(L, inp, "APPROVAL_DECIDED", 6, mode="mock"))
    p = _pref(L, out)
    assert p.label in ("D", "E") and p.capex_ids == []
    assert out["situation_summary"].startswith("승인 결정에 따라")


def test_bid_participation_rejected_on_hold(inp):
    """입찰 참여 자체가 거절되어 보류된 건에서도 실행이 깨지지 않는다."""
    f, L = s1_until(inp, 8)
    bidap = next(a for a in L.approvals if a.decision_type == "BID_PARTICIPATION")
    f.decide(L, bidap.id, "REJECTED", "전략 우선순위 아님", "2026-07-06", "ceo", R.CEO_BOARD)
    assert L.status == "ON_HOLD"
    out = _ok(run_agent(L, inp, "APPROVAL_DECIDED", 7, mode="mock"))
    _no_blocked_pref(L, out)


@pytest.mark.parametrize("start", ["2026-08-03", "2026-09-01"])
def test_extreme_supply_start_has_no_fake_preference(inp, start):
    """납품 개시를 극단적으로 앞당겨 가능한 대안이 줄어도, 불가 대안을 선호로 내세우지 않는다."""
    f, L = s1_until(inp, 5)
    f.supersede(L, "supply_start_date", start, "극단 앞당김", "2026-07-04", "sales", R.SALES)
    out = _ok(run_agent(L, inp, "ASSUMPTION_SUPERSEDED", 8, mode="mock"))
    _no_blocked_pref(L, out)
    if not any(o.feasibility == "FEASIBLE" and o.status == "PROPOSED" for o in L.options):
        assert out["ai_opinion"] is None or out["ai_opinion"]["preferred_option_id"] is None


def test_buyer_refuses_partial_award(inp):
    """바이어가 부분 물량 제안을 불허. 전량 대안만 남고, 모의 AI는 불가 대안을 고르지 않는다."""
    f, L = s1_until(inp, 3)
    buyer = next(r for r in L.requests if r.to_external == "C-201_BUYER")
    f.answer_request(L, buyer.id, {"supply_start_date": "2027-04-01", "partial_award_allowed": False}, "부분 물량 불가, 전량만", "2026-07-03", "demo", R.DEMO_ADMIN)
    f.confirm_from_request(L, buyer.id, "2026-07-03", "sales", R.SALES)
    out = _ok(run_agent(L, inp, "INFO_REQUEST_ANSWERED", 9, mode="mock"))
    _no_blocked_pref(L, out)
    live = [o for o in L.options if o.status == "PROPOSED"]
    assert live and all(o.feasibility == "INFEASIBLE" for o in live)  # 전량 조건이면 기존 약속과 함께 못 채움
    assert out["ai_opinion"]["preferred_option_id"] is None
    assert "실행 가능한 대안이 없습니다" in out["situation_summary"]
