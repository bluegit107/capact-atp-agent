"""W05 업무 흐름: 시나리오 S1·S2·S2-b·S3-a·S3-b(지시서 12장)와 전파·권한(10장·9장)."""
from __future__ import annotations

import pytest

from dealcockpit.adapters.golden_loader import load_w05_inputs
from dealcockpit.domain.approval import DEFAULT_POLICY
from dealcockpit.domain.common import Role
from dealcockpit.w05.flow import FlowError, FlowPermission, W05Flow

R = Role
T0 = "2026-07-01"


@pytest.fixture(scope="module")
def inp():
    return load_w05_inputs()


def flow(inp):
    return W05Flow(inp, DEFAULT_POLICY)


def opt(L, label, live=True):
    return next(o for o in reversed(L.options) if o.label == label and (not live or o.status in ("PROPOSED", "SELECTED")))


def s1_until(inp, step: int):
    f = flow(inp)
    L = f.receive_rfp(T0, "sales", R.SALES)
    buyer = next(r for r in L.requests if r.to_external == "C-201_BUYER")
    if step < 3:
        return f, L
    f.send_request(L, buyer.id, T0, "sales", R.SALES)
    if step < 4:
        return f, L
    f.answer_request(L, buyer.id, {"supply_start_date": "2027-04-01", "partial_award_allowed": True, "split_delivery": True}, "개시 2027-04-01, 부분·분할 허용",
                     "2026-07-03", "demo", R.DEMO_ADMIN)
    if step < 5:
        return f, L
    f.confirm_from_request(L, buyer.id, "2026-07-03", "sales", R.SALES)
    if step < 8:
        return f, L
    f.submit(L, opt(L, "B").id, "2026-07-04", "sales", R.SALES)
    if step < 9:
        return f, L
    for a in [a for a in L.approvals if a.status == "PENDING"]:
        if a.decision_type == "BID_PARTICIPATION":
            f.decide(L, a.id, "CONDITIONAL", "단가 인하 요구가 10%를 넘으면 재상신", "2026-07-06", "ceo", R.CEO_BOARD)
        else:
            f.decide(L, a.id, "APPROVED", None, "2026-07-06", "ceo", R.CEO_BOARD)
    if step < 10:
        return f, L
    t = next(t for t in L.tasks if t.type == "CAPACITY_RESERVATION")
    f.complete_task(L, t.id, "2026-07-07", "production", R.PRODUCTION)
    return f, L


def test_s1_normal(inp):
    f, L = s1_until(inp, 2)
    assert L.status == "WAITING_INFO"
    assert {a.key for a in L.assumptions if a.is_critical and a.status == "UNCONFIRMED"} == {"supply_start_date", "partial_award_allowed"}
    f, L = s1_until(inp, 5)
    assert L.status == "IN_REVIEW"
    feas = {o.label: o.feasibility for o in L.options if o.status == "PROPOSED"}
    assert feas == {"A": "INFEASIBLE", "B": "FEASIBLE", "C": "FEASIBLE"}
    b = opt(L, "B")
    assert b.offered == {"PL-01": 2_730_000, "PL-02": 1_500_000} and b.capex_ids == ["OPT-01"]
    mets = {m["id"]: m for m in L.runs[-1].metrics}
    assert round(mets[b.metric_ids["revenue"]]["value"] / 1e8) == 827 and round(mets[b.metric_ids["margin"]]["value"] / 1e8) == 550
    assert round(mets[b.metric_ids["grid_10/10"]]["value"] / 1e8) == 393
    rules = {x.rule_key for x in L.flags if x.option_id == b.id}
    assert rules == {"RF-PROD-01", "RF-PROD-02", "RF-PROD-03", "RF-FIN-01", "RF-FIN-02", "RF-FIN-03", "RF-SALES-01", "RF-SALES-02", "RF-QUAL-01"}
    f, L = s1_until(inp, 9)
    assert L.status == "APPROVED" and {t.type for t in L.tasks} == {"PROPOSAL_TERMS", "CAPACITY_RESERVATION", "CAPEX_SUBMISSION"}
    assert {r.line_id for r in L.reservations} == {"L-01", "L-02"} and min(r.week_start for r in L.reservations) == "2027-03-29"
    f, L = s1_until(inp, 10)
    assert all(r.status == "CONFIRMED" for r in L.reservations)
    pt = next(t for t in L.tasks if t.type == "PROPOSAL_TERMS")
    with pytest.raises(FlowError):  # 조건부 승인 조건 반영 표시 없이 완료 불가
        f.complete_task(L, pt.id, "2026-07-08", "sales", R.SALES)
    f.complete_task(L, pt.id, "2026-07-08", "sales", R.SALES, acknowledge_conditions=True)
    assert L.status == "READY_TO_SUBMIT"
    f.submit_bid(L, "2026-07-08", "sales", R.SALES)
    assert L.status == "SUBMITTED"


def test_s2_change_propagates_and_procurement_unblocks(inp):
    f, L = s1_until(inp, 10)
    rev0 = L.revision
    f.supersede(L, "supply_start_date", "2026-10-01", "바이어 추가 통지: 납품 개시 앞당김", "2026-07-08", "sales", R.SALES)
    cb = L.change_banner
    assert cb["kept"] is False and cb["metrics"]["PL-02"] == [100.0, 59.3]
    assert cb["counts"]["voided"] == 2 and cb["counts"]["blocked"] >= 1 and cb["counts"]["resubmit"] == 1 and cb["counts"]["released"] > 0
    assert L.revision == rev0 + 1 and L.selected_option_id is None
    assert all(a.status == "VOIDED" for a in L.approvals) and all(r.status == "RELEASED" for r in L.reservations)
    feas = {o.label: (o.feasibility, o.capex_ids, o.offered) for o in L.options if o.status == "PROPOSED"}
    assert feas["A"][0] == "INFEASIBLE" and sorted(feas["A"][1]) == ["OPT-01", "OPT-02"]
    assert feas["B"] == ("CONDITIONAL", ["OPT-02"], {"PL-01": 2_730_000, "PL-02": 770_000})
    assert feas["C"][0] == "FEASIBLE" and feas["C"][2] == {"PL-01": 2_730_000}
    assert L.status == "WAITING_INFO"
    b = opt(L, "B")
    assert any(x.rule_key == "RF-PROC-01" and x.severity == "BLOCK" for x in L.flags if x.option_id == b.id)
    proc = next(r for r in L.requests if r.assumption_keys == ["mb_recontract"])
    f.send_request(L, proc.id, "2026-07-09", "sales", R.SALES)
    with pytest.raises(FlowPermission):
        f.answer_request(L, proc.id, {"mb_recontract": {"possible": True}}, "가능", "2026-07-10", "sales", R.SALES)
    f.answer_request(L, proc.id, {"mb_recontract": {"possible": True, "conditions": "V-205 최소 주문 수량 상향"}}, "조건부 가능", "2026-07-10", "proc", R.PROCUREMENT)
    assert opt(L, "B").feasibility == "FEASIBLE" and L.status == "IN_REVIEW"
    assert not any(x.rule_key == "RF-PROC-01" for x in L.flags if x.option_id == b.id)
    f.submit(L, b.id, "2026-07-10", "sales", R.SALES)
    for a in [a for a in L.approvals if a.status == "PENDING"]:
        f.decide(L, a.id, "APPROVED", None, "2026-07-11", "ceo", R.CEO_BOARD)
    assert L.status == "APPROVED"
    rc = next(t for t in L.tasks if t.type == "PROCUREMENT_RECONTRACT" and t.status == "OPEN")
    assert rc.payload["conditions"] == "V-205 최소 주문 수량 상향" and len([t for t in L.tasks if t.status == "OPEN" and t.option_id == b.id]) == 4


def test_s2b_procurement_refuses(inp):
    f, L = s1_until(inp, 10)
    f.supersede(L, "supply_start_date", "2026-10-01", "앞당김", "2026-07-08", "sales", R.SALES)
    proc = next(r for r in L.requests if r.assumption_keys == ["mb_recontract"])
    f.send_request(L, proc.id, "2026-07-09", "sales", R.SALES)
    f.answer_request(L, proc.id, {"mb_recontract": {"possible": False}}, "재계약 불가", "2026-07-10", "proc", R.PROCUREMENT)
    assert opt(L, "B").feasibility == "INFEASIBLE" and opt(L, "C").feasibility == "FEASIBLE"


def test_revalidated_no_change(inp):
    f, L = s1_until(inp, 10)
    f.supersede(L, "supply_start_date", "2027-04-08", "1주 늦춤", "2026-07-08", "sales", R.SALES)
    assert L.change_banner["kept"] is True and L.status == "APPROVED"
    assert any(e.event_type == "REVALIDATED_NO_CHANGE" for e in L.events)
    assert not any(a.status == "VOIDED" for a in L.approvals) and all(r.status == "CONFIRMED" for r in L.reservations)


def test_s3a_info_gap_and_deadline(inp):
    f, L = s1_until(inp, 3)
    with pytest.raises(FlowError, match="G5"):
        f.submit(L, None, "2026-07-05", "sales", R.SALES)
    assert L.status == "WAITING_INFO"
    f.tick(L, "2026-07-15", "demo")
    assert any(t.type == "INFO_FOLLOWUP" for t in L.tasks) and any(e.event_type == "DEADLINE_TICK" for e in L.events)
    f.tick(L, "2026-07-22", "demo")
    assert L.status == "ON_HOLD" and L.hold_reason == "마감 경과"


def test_s3b_capex_rejected_resubmit(inp):
    f, L = s1_until(inp, 8)
    cap = next(a for a in L.approvals if a.decision_type == "CAPEX")
    bidap = next(a for a in L.approvals if a.decision_type == "BID_PARTICIPATION")
    with pytest.raises(FlowPermission, match="G1"):
        f.decide(L, cap.id, "APPROVED", None, "2026-07-06", "sx", R.SALES_EXEC)
    with pytest.raises(FlowError):
        f.decide(L, cap.id, "REJECTED", "", "2026-07-06", "ceo", R.CEO_BOARD)  # 사유 필수
    f.decide(L, bidap.id, "APPROVED", None, "2026-07-06", "ceo", R.CEO_BOARD)
    f.decide(L, cap.id, "REJECTED", "180억원은 그룹 가용자금 300억원의 60%", "2026-07-06", "ceo", R.CEO_BOARD)
    live = {o.label: (o.feasibility, o.offered, o.capex_ids) for o in L.options if o.status == "PROPOSED"}
    assert live == {"D": ("FEASIBLE", {"PL-01": 2_730_000, "PL-02": 170_000}, []), "E": ("FEASIBLE", {"PL-01": 2_730_000}, [])}
    assert any(t.type == "RESUBMIT" for t in L.tasks) and L.status == "IN_REVIEW"
    f.submit(L, opt(L, "D").id, "2026-07-07", "sales", R.SALES)
    assert [a.decision_type for a in L.approvals if a.status == "PENDING"] == ["BID_PARTICIPATION"]
    f.decide(L, next(a for a in L.approvals if a.status == "PENDING").id, "APPROVED", None, "2026-07-08", "ceo", R.CEO_BOARD)
    assert L.status == "APPROVED" and len([t for t in L.tasks if t.status == "OPEN" and t.type != "RESUBMIT"]) == 2


def test_bid_participation_rejected_holds(inp):
    f, L = s1_until(inp, 8)
    bidap = next(a for a in L.approvals if a.decision_type == "BID_PARTICIPATION")
    f.decide(L, bidap.id, "REJECTED", "전략 우선순위 아님", "2026-07-06", "ceo", R.CEO_BOARD)
    assert L.status == "ON_HOLD"


def test_block_flag_prevents_submit(inp):
    f, L = s1_until(inp, 10)
    f.supersede(L, "supply_start_date", "2026-10-01", "앞당김", "2026-07-08", "sales", R.SALES)
    L.status = "IN_REVIEW"  # 핵심 가정 검사와 별개로 BLOCK 표시만으로도 거부되는지(G15)
    b = opt(L, "B")
    b.deps = [d for d in b.deps if d != "mb_recontract"]
    with pytest.raises(FlowError, match="G15"):
        f.submit(L, b.id, "2026-07-09", "sales", R.SALES)


def test_external_reply_only_by_demo_and_confirm_by_human(inp):
    f, L = s1_until(inp, 3)
    buyer = next(r for r in L.requests if r.to_external)
    with pytest.raises(FlowPermission):
        f.answer_request(L, buyer.id, {"supply_start_date": "2027-04-01"}, "x", T0, "sales", R.SALES)
    f.answer_request(L, buyer.id, {"supply_start_date": "2027-04-01", "partial_award_allowed": True}, "x", T0, "demo", R.DEMO_ADMIN)
    assert L.cur("supply_start_date").status == "UNCONFIRMED"  # 외부 문장은 지시가 아님: 사람 클릭 전 미반영(G12)
    with pytest.raises(FlowPermission):
        f.confirm_from_request(L, buyer.id, T0, "prod", R.PRODUCTION)
    f.confirm_from_request(L, buyer.id, T0, "sales", R.SALES)
    a = L.cur("supply_start_date")
    assert a.status == "CONFIRMED" and a.source_type == "MOCK_REPLY" and a.supersedes_id is not None


def test_events_are_linked_to_causes(inp):
    f, L = s1_until(inp, 10)
    f.supersede(L, "supply_start_date", "2026-10-01", "앞당김", "2026-07-08", "sales", R.SALES)
    prop = [e for e in L.events if e.event_type in ("OPTION_STALE", "APPROVAL_VOIDED", "TASK_BLOCKED")]
    assert prop and all(e.caused_by is not None for e in prop)


def test_ledger_round_trips_through_json(inp):
    from dealcockpit.w05.flow import BidLedger

    f, L = s1_until(inp, 10)
    assert BidLedger.model_validate_json(L.model_dump_json()) == L
