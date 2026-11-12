"""협상 루프(S4): 필수 3케이스 + 게이트·권한·상태 전이·재현성."""
from __future__ import annotations

import pytest

from dealcockpit.adapters.demo_cases import case_change, case_normal, case_reject
from dealcockpit.adapters.golden_loader import load_case, load_reply_stats
from dealcockpit.adapters.repo_memory import MemoryRepository
from dealcockpit.adapters.solver_lp import LpSolver
from dealcockpit.application.negotiation import NegotiationService, WorkflowError
from dealcockpit.application.ports import ConcurrencyConflict
from dealcockpit.domain.approval import PermissionDenied
from dealcockpit.domain.common import Provenance, Role
from dealcockpit.domain.lever import LeverStatus
from dealcockpit.domain.workflow import ReplyOutcome

SCIP = LpSolver("SCIP")


@pytest.fixture(scope="module")
def normal_and_change():
    repo = MemoryRepository()
    n = case_normal(repo, SCIP)
    c = case_change(repo, SCIP)
    return repo, n, c


@pytest.fixture(scope="module")
def rejected():
    repo = MemoryRepository()
    return repo, case_reject(repo, SCIP)


def svc(repo):
    return NegotiationService(repo, SCIP, load_case(), load_reply_stats())


def step(run, prefix):
    return next(s for s in run.log if s["what"].startswith(prefix))


# ── 정상 ───────────────────────────────────────────────────────────────────────
def test_normal_case_reaches_plan_of_record_with_approvals_and_audit(normal_and_change):
    repo, n, _ = normal_and_change
    first = step(n, "개설")
    assert first["as001_blocks"] is True  # 시작 시점엔 용량 단위 미확정 → 차단
    s = step(n, "상신 A")
    assert s["submitted"] and ("bid_participation", "CEO/이사회") in [tuple(x) for x in s["approvals"]]
    plan = step(n, "확정 계획")
    assert plan["plan_version"] == 1 and plan["final"]["feasible"] and plan["final"]["accept_pct"] == 100.0


def test_inquiries_are_ordered_by_probe_priority_with_capacity_first(normal_and_change):
    _, n, _ = normal_and_change
    order = step(n, "문의 계획")["order"]
    assert order[0] == "AS-001_capacity_unit" and len(order) == 7


def test_plan_hash_is_reproducible_and_levers_applied(normal_and_change):
    repo, _, _ = normal_and_change
    s = svc(repo)
    ws = repo.load("WS-NORMAL")
    assert ws.plan.input_hash == s.solve(ws, candidate=False).input_hash and not ws.plan_stale
    for lid in ws.plan.lever_ids:
        assert ws.levers[lid].status is LeverStatus.APPLIED
    assert len(repo.list_audit("WS-NORMAL")) == ws.counters["EV"]


# ── 변경 + 다른 부서 영향 + 승인 거절 ──────────────────────────────────────────────
def test_fx_and_price_change_keeps_decision_but_marks_plan_stale(normal_and_change):
    _, _, c = normal_and_change
    s = step(c, "조건 변경: 환율")
    assert s["plan_stale"] and s["decision_kept"]
    assert s["after"]["net_eok"] < s["before"]["net_eok"]  # 결론은 유지, 수치는 악화


def test_quality_recall_reduces_bid_and_ceo_capex_rejection_produces_alternative(normal_and_change):
    repo, _, c = normal_and_change
    rec = step(c, "품질위원회 회수 의결")
    assert rec["plan_stale"] and rec["after"]["accept_pct"] < 100
    assert ("customer_schedule_change", "영업임원") not in [tuple(x) for x in step(c, "재상신 A")["approvals"]]  # A 는 양해 미사용
    alt = step(c, "투자 거절 반영")
    assert alt["excluded"] == ["OPT-02"] and alt["candidate"]["accept_pct"] < rec["after"]["accept_pct"]
    assert alt["candidate"]["net_vs_baseline_eok"] > 0  # 회수 지연 비용은 입찰과 무관 → 증분은 이익
    final = step(c, "재확정(회수·투자 거절 반영)")
    assert final["plan_version"] == 3 and "OPT-02" not in final["final"]["actions"]


# ── 정보 부족·거절 ──────────────────────────────────────────────────────────────
def test_info_gap_blocks_then_loss_blocks_and_no_plan(rejected):
    repo, r = rejected
    assert set(step(r, "기한 경과")["expired"]) == {"AS-001_capacity_unit", "C201_partial_award"}
    b1 = step(r, "상신 시도 A")["blockers"]
    assert any("AS-001" in x for x in b1) and any("AS-004" in x for x in b1)
    assert any("AS-004" in x for x in step(r, "재상신 A — 바이어")["blockers"])
    assert any("증분 순이익 음수" in x for x in step(r, "재상신 A(전량 조건)")["blockers"])
    ws = repo.load("WS-REJECT")
    assert ws.plan is None
    assert sum(1 for t in ws.tasks.values() if t.title == "상신 차단: A") == 1  # 차단 작업은 하나로 갱신


def test_counter_offer_above_breakeven_is_refused_and_only_owner_can_accept(rejected):
    repo, _ = rejected
    s = svc(repo)
    s.open("WS-CTR", "김")
    ws, order = s.plan_inquiries("WS-CTR", "김")
    rid = next(r for r, t in ws.request_topics.items() if t == "LV_outsourcing_PL-02")
    s.send("WS-CTR", rid, "김", Role.SALES)
    ws, _ = s.receive("WS-CTR", rid, ReplyOutcome.COUNTER, {"qty_per_month": 30000, "unit_cost_multiplier": 2.2, "lead_time_weeks": 6}, "2.2배", "김", Role.SALES)
    lid = ws.requests[rid].linked_lever_id
    with pytest.raises(PermissionError):
        s.accept_counter("WS-CTR", lid, "영업", Role.SALES, breakeven_multiplier=1.967)
    with pytest.raises(WorkflowError, match="손익분기"):
        s.accept_counter("WS-CTR", lid, "생산", Role.PRODUCTION, breakeven_multiplier=1.967)
    assert repo.load("WS-CTR").levers[lid].status is LeverStatus.COUNTERED


# ── 권한·상태 전이·동시성 ────────────────────────────────────────────────────────
def test_wrong_approver_and_loss_acceptance_role_are_rejected():
    repo = MemoryRepository()
    s = svc(repo)
    case_normal(repo, SCIP, "WS-P")
    s.change_conditions("WS-P", "재무", Role.FINANCE, price_pressure_pct=-5.0)
    ws, res = s.submit("WS-P", "A", "김", Role.SALES)
    aid = res.approval_ids[0]
    with pytest.raises(PermissionDenied):
        s.decide("WS-P", aid, Role.SALES, True, "김")
    with pytest.raises(PermissionError):
        s.submit("WS-P", "A", "김", Role.SALES, accept_loss=True)
    with pytest.raises(WorkflowError, match="확정 불가"):
        s.promote_plan("WS-P", "김", Role.SALES)  # 이번 상신의 승인 전에는 확정 불가(이전 상신의 승인 재사용 금지)
    for a in res.approval_ids:
        ap = repo.load("WS-P").approvals[a]
        s.decide("WS-P", a, ap.approver, True, "승인자")
    s.change_conditions("WS-P", "재무", Role.FINANCE, fx_label="downside")  # 승인 후 조건 변경
    with pytest.raises(WorkflowError, match="재상신"):
        s.promote_plan("WS-P", "김", Role.SALES)  # 승인은 상신 당시 입력에만 유효


def test_reply_requires_sent_request_and_low_confidence_needs_human():
    repo = MemoryRepository()
    s = svc(repo)
    s.open("WS-R", "김")
    ws, order = s.plan_inquiries("WS-R", "김")
    with pytest.raises(ValueError):
        s.receive("WS-R", order[0], ReplyOutcome.ACCEPT, {"period": "per_month"}, "x", "김", Role.SALES)  # 발송 전


def test_stale_writer_gets_conflict():
    repo = MemoryRepository()
    s = svc(repo)
    s.open("WS-C", "김")
    stale = repo.load("WS-C")
    s.plan_inquiries("WS-C", "김")  # 다른 사용자가 먼저 저장
    with pytest.raises(ConcurrencyConflict):
        repo.save(stale.model_copy(update={"today": "2026-07-09"}), stale.version)


def test_submission_blocked_until_capacity_unit_confirmed():
    repo = MemoryRepository()
    s = svc(repo)
    s.open("WS-G", "김")
    ws, res = s.submit("WS-G", "A", "김", Role.SALES)
    assert not res.submitted and any("AS-001" in b for b in res.blockers)
    assert not ws.approvals  # 차단되면 승인 요청이 만들어지지 않는다
