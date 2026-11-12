from __future__ import annotations

from datetime import date

import pytest
from pydantic import ValidationError

from dealcockpit.domain.approval import (
    ApprovalRequest,
    ApprovalStatus,
    PermissionDenied,
    UnknownApprovalAction,
    rule_for,
)
from dealcockpit.domain.assumption import Assumption, AssumptionStatus
from dealcockpit.domain.bid import Bid, BidLine, BidTerms, FxScenario, SupplyStart
from dealcockpit.domain.common import Provenance, Role
from dealcockpit.domain.lever import Lever, LeverStatus, LeverType
from dealcockpit.domain.plan import PlanOfRecord, PromotionBlocked, promote, promotion_blockers
from dealcockpit.domain.plant import CapacityOption, CapacityUnit, Line, OutsourcingPartner, PlantModel, PremiumCosts
from dealcockpit.domain.solution import (
    ApprovalNeed,
    CustomerImpact,
    Financials,
    Infeasibility,
    RelaxationItem,
    Scenario,
    SolverMeta,
    SolverOutput,
    canonical_hash,
)
from dealcockpit.domain.workflow import (
    InfoRequest,
    InfoRequestStatus,
    Reply,
    ReplyOutcome,
    Task,
    TaskStatus,
)


# ── Bid ──────────────────────────────────────────────────────────────────────
def make_bid(**over) -> Bid:
    base = dict(
        bid_id="C-201",
        as_of=date(2026, 7, 1),
        deadline=date(2026, 7, 22),
        supply_start=SupplyStart(assumed=date(2026, 10, 5), assumption_id="AS-004"),
        fx=(FxScenario(label="rfp", usd_krw=1480, provenance=Provenance.SOURCE),),
        lines=(
            BidLine(product_id="PL-01", annual_qty=6_000_000, unit_price_fx=11.80),
            BidLine(product_id="PL-02", annual_qty=1_500_000, unit_price_fx=15.80),
        ),
        terms=BidTerms(currency="USD", late_penalty_pct_per_month=3.0, recall_cost_passthrough=True),
        price_pressure_pct=(0.0, -5.0, -10.0),
    )
    base.update(over)
    return Bid(**base)


def test_bid_separates_deadline_from_supply_start_and_lists_open_questions():
    b = make_bid()
    assert b.days_to_deadline == 21
    assert set(b.open_questions) == {"partial_award_allowed", "supply_start"}


def test_bid_supply_start_unknown_requires_assumption_id():
    with pytest.raises(ValidationError):
        SupplyStart()


def test_bid_supply_start_cannot_precede_deadline():
    with pytest.raises(ValidationError):
        make_bid(supply_start=SupplyStart(assumed=date(2026, 7, 10), assumption_id="AS-004"))


def test_bid_rejects_positive_price_pressure_and_duplicate_products():
    with pytest.raises(ValidationError):
        make_bid(price_pressure_pct=(5.0,))
    with pytest.raises(ValidationError):
        make_bid(lines=(BidLine(product_id="PL-01", annual_qty=1, unit_price_fx=1),) * 2)


def test_bid_answered_partial_award_removes_open_question():
    b = make_bid(terms=BidTerms(currency="USD", late_penalty_pct_per_month=3.0, recall_cost_passthrough=True, partial_award_allowed=True))
    assert b.open_questions == ("supply_start",)


# ── Plant ────────────────────────────────────────────────────────────────────
def make_line(**over) -> Line:
    base = dict(line_id="L-02", plant_id="F1", product_family="PL-02", capacity_qty=1_949_480, utilization_pct=97.0)
    base.update(over)
    return Line(**base)


def test_unconfirmed_capacity_unit_refuses_to_pick_an_interpretation():
    with pytest.raises(ValueError):
        make_line().weekly_capacity()


def test_weekly_capacity_per_interpretation_is_explicit():
    ln = make_line()
    assert ln.weekly_capacity("per_month") == pytest.approx(1_949_480 / 4.333)
    assert ln.weekly_capacity("per_year") == pytest.approx(1_949_480 / 52)
    confirmed = make_line(capacity_unit=CapacityUnit(period="per_month", status="CONFIRMED"))
    assert confirmed.weekly_capacity() == pytest.approx(ln.weekly_capacity("per_month"))


def make_opt02() -> CapacityOption:
    return CapacityOption(
        option_id="OPT-02", kind="f2_conversion", target_line="NEW:F2-DERMA", capex_krw=1_200_000_000,
        lead_time_weeks=7, added_capacity_qty=600_000, requires=("formula_revalidation", "M-B_recontract"),
    )


def test_option_start_needs_requirement_answers_and_uses_worst_delay():
    opt = make_opt02()
    with pytest.raises(ValueError, match="미확인"):
        opt.earliest_start(date(2026, 7, 6))  # 품질 회신 전에는 가용 시점을 말할 수 없다
    assert opt.earliest_start(date(2026, 7, 6), {"formula_revalidation": 6, "M-B_recontract": 3}) == 13
    assert opt.earliest_start(date(2026, 7, 6), {"formula_revalidation": 2, "M-B_recontract": 3}) == 10


def test_plant_model_validates_lines_and_option_targets():
    premium = PremiumCosts(overtime_multiplier=1.5, overtime_max_hours_per_week=16)
    pm = PlantModel(as_of=date(2026, 7, 1), horizon_weeks=52, lines=(make_line(),), options=(make_opt02(),), premium=premium)
    assert pm.line_for_product("PL-02").line_id == "L-02"
    with pytest.raises(ValidationError):
        PlantModel(as_of=date(2026, 7, 1), horizon_weeks=52, lines=(make_line(), make_line()), premium=premium)
    bad = make_opt02().model_copy(update={"target_line": "L-99"})
    with pytest.raises(ValidationError):
        PlantModel(as_of=date(2026, 7, 1), horizon_weeks=52, lines=(make_line(),), options=(bad,), premium=premium)


def test_outsourcing_partner_cannot_claim_to_be_source_data():
    with pytest.raises(ValidationError):
        OutsourcingPartner(partner_id="V-OUT-1", product_id="PL-02", max_qty_per_month=50000, lead_time_weeks=4,
                           unit_cost_multiplier=1.35, min_term_months=3, quality_risk_note="x", provenance=Provenance.SOURCE)


# ── Approval ─────────────────────────────────────────────────────────────────
def test_approval_routes_to_policy_owner_and_blocks_wrong_roles():
    ap = ApprovalRequest.request("AP-001", "bid_participation")
    assert ap.approver is Role.CEO_BOARD
    for wrong in (Role.SALES, Role.SALES_EXEC, Role.REVIEWER, Role.FINANCE):
        with pytest.raises(PermissionDenied):
            ap.decide(wrong, True)
    assert ap.decide(Role.CEO_BOARD, True).status is ApprovalStatus.APPROVED


def test_c101_renegotiation_needs_sales_exec_not_ceo():
    ap = ApprovalRequest.request("AP-002", "price_renegotiation_C-101")
    with pytest.raises(PermissionDenied):
        ap.decide(Role.CEO_BOARD, True)
    assert ap.decide(Role.SALES_EXEC, False, note="재협상 거절").status is ApprovalStatus.REJECTED


def test_unknown_action_is_refused_and_assumed_rules_are_flagged():
    with pytest.raises(UnknownApprovalAction):
        ApprovalRequest.request("AP-003", "buy_a_yacht")
    assert rule_for("outsourcing_contract").provenance is Provenance.HUMAN_INPUT  # 사용자 결정(2026-11-09)
    assert rule_for("bid_participation").provenance is Provenance.SOURCE


def test_decided_approval_cannot_be_decided_twice():
    ap = ApprovalRequest.request("AP-004", "capex_investment").decide(Role.CEO_BOARD, False)
    with pytest.raises(ValueError):
        ap.decide(Role.CEO_BOARD, True)


# ── Workflow: 문의·회신·작업 ──────────────────────────────────────────────────
def make_request(**over) -> InfoRequest:
    base = dict(request_id="IR-001", to_role=Role.QUALITY, counterparty="품질팀", question="OPT-02 처방 재검증 소요?",
                answer_by=date(2026, 7, 10), linked_lever_id="LV-001")
    base.update(over)
    return InfoRequest(**base)


def make_reply(outcome=ReplyOutcome.ACCEPT, **over) -> Reply:
    base = dict(reply_id="RP-001", request_id="IR-001", raw_text="…", outcome=outcome, provenance=Provenance.SIMULATED)
    base.update(over)
    return Reply(**base)


def test_request_must_serve_an_assumption_or_lever():
    with pytest.raises(ValidationError):
        make_request(linked_lever_id=None)


def test_request_lifecycle_send_receive_close():
    r = make_request()
    with pytest.raises(ValueError):
        r.receive(make_reply())  # 발송 전 회신 불가
    r = r.send().receive(make_reply())
    assert r.status is InfoRequestStatus.REPLIED
    assert r.close().status is InfoRequestStatus.CLOSED


def test_request_expires_after_deadline_only_when_sent():
    r = make_request()
    assert r.expire_if_due(date(2026, 7, 20)).status is InfoRequestStatus.DRAFT
    sent = r.send()
    assert sent.expire_if_due(date(2026, 7, 10)).status is InfoRequestStatus.SENT  # 기한 당일까지는 유효
    assert sent.expire_if_due(date(2026, 7, 11)).status is InfoRequestStatus.EXPIRED


def test_reply_to_wrong_request_is_refused():
    with pytest.raises(ValueError):
        make_request().send().receive(make_reply(request_id="IR-002"))


def test_counter_and_conditional_replies_need_terms():
    with pytest.raises(ValidationError):
        make_reply(ReplyOutcome.COUNTER)
    assert make_reply(ReplyOutcome.COUNTER, terms={"unit_cost_multiplier": 1.6, "lead_time_weeks": 6}).terms
    with pytest.raises(ValidationError):
        make_reply(ReplyOutcome.CONDITIONAL)
    assert make_reply(ReplyOutcome.REJECT).outcome is ReplyOutcome.REJECT  # 거절은 수치 없이도 유효


def test_low_confidence_unconfirmed_human_reply_is_not_usable_for_replan():
    r = make_reply(provenance=Provenance.HUMAN_INPUT, parse_confidence=0.6)
    assert not r.usable_for_replan
    assert r.model_copy(update={"human_confirmed": True}).usable_for_replan


def test_task_transitions_and_blocked_requires_reason():
    t = Task(task_id="TK-001", title="품질 회신 대기", owner_role=Role.QUALITY)
    with pytest.raises(ValueError):
        t.move(TaskStatus.BLOCKED)
    b = t.move(TaskStatus.BLOCKED, "거절")
    assert b.blocked_reason == "거절" and b.move(TaskStatus.OPEN).blocked_reason is None
    done = t.move(TaskStatus.DONE)
    with pytest.raises(ValueError):
        done.move(TaskStatus.OPEN)


# ── Solution: 근거 추적성·해시 ─────────────────────────────────────────────────
def test_delay_without_evidence_clause_is_not_a_valid_output():
    with pytest.raises(ValidationError):
        CustomerImpact(customer_id="C-103", orders_delayed=2, max_delay_days=14, penalty_krw=1000)
    assert CustomerImpact(customer_id="C-103", orders_delayed=2, max_delay_days=14, penalty_krw=1000, evidence_clause_ids=("CL-C103-04",))
    assert CustomerImpact(customer_id="C-103", orders_delayed=0, max_delay_days=0, penalty_krw=0)  # 지연 없으면 근거 불필요


def test_canonical_hash_is_order_independent_and_sensitive():
    a = canonical_hash({"x": 1, "y": [1, 2], "z": {"p": 1, "q": 2}})
    b = canonical_hash({"z": {"q": 2, "p": 1}, "y": [1, 2], "x": 1})
    assert a == b and a.startswith("sha256:") and len(a) == 71
    assert a != canonical_hash({"x": 1, "y": [2, 1], "z": {"p": 1, "q": 2}})


def make_scenario(**over) -> Scenario:
    base = dict(
        scenario_id="A", label="보호", weights={"w_customer": 10},
        accepted_pct={"PL-02": 0.4},
        financials=Financials(incremental_revenue_krw=100, contribution_krw=30, penalty_krw=5, premium_cost_krw=3, capex_krw=2),
    )
    base.update(over)
    return Scenario(**base)


def test_scenario_net_and_pct_range():
    assert make_scenario().financials.net_krw == 20
    with pytest.raises(ValidationError):
        make_scenario(accepted_pct={"PL-02": 1.2})


def test_solver_output_requires_hash_format_and_unique_scenarios():
    meta = SolverMeta(name="highs", status="OPTIMAL", wall_ms=1000)
    h = canonical_hash({"a": 1})
    SolverOutput(input_hash=h, solver=meta, scenarios=(make_scenario(),))
    with pytest.raises(ValidationError):
        SolverOutput(input_hash="md5:abc", solver=meta, scenarios=(make_scenario(),))
    with pytest.raises(ValidationError):
        SolverOutput(input_hash=h, solver=meta, scenarios=(make_scenario(), make_scenario()))


def test_infeasibility_must_list_relaxation():
    with pytest.raises(ValidationError):
        Infeasibility(min_relaxation=())
    assert Infeasibility(min_relaxation=(RelaxationItem(constraint="L-02 W30 capacity", slack=12000.0),))


# ── Plan of Record 승격 게이트 ─────────────────────────────────────────────────
def lever(status: LeverStatus, lid="LV-001") -> Lever:
    return Lever(
        lever_id=lid, type=LeverType.CAPACITY_DELTA, status=status,
        params=dict(target="outsourcing:V-OUT-1", product="PL-02", qty_per_month=50000, available_from_week="2026-W36", unit_cost_multiplier=1.35),
        owner_role=Role.PRODUCTION, counterparty="외주사(가상)", confidence=0.5, provenance=Provenance.SIMULATED,
    )


def gate_assumption(**over) -> Assumption:
    base = dict(assumption_id="AS-001", statement="용량 단위", owner_role=Role.PRODUCTION)
    base.update(over)
    return Assumption(**base)


def approved(action: str, scenario_id="A", by=None) -> ApprovalRequest:
    ap = ApprovalRequest.request("AP-010", action, scenario_id)
    return ap.decide(by or ap.approver, True)


def scenario_with_needs(**over) -> Scenario:
    return make_scenario(
        approvals_required=(ApprovalNeed(action="bid_participation", approver="CEO/이사회"),),
        used_lever_ids=("LV-001",), **over,
    )


def test_promotion_blocked_by_unconfirmed_assumption_hypothesis_lever_and_missing_approval():
    sc = scenario_with_needs()
    reasons = promotion_blockers(sc, [], {"LV-001": lever(LeverStatus.HYPOTHESIS)}, [gate_assumption()])
    joined = " | ".join(reasons)
    assert "AS-001" in joined and "LV-001" in joined and "bid_participation" in joined
    with pytest.raises(PromotionBlocked) as ei:
        promote(sc, canonical_hash({}), [], {"LV-001": lever(LeverStatus.HYPOTHESIS)}, [gate_assumption()])
    assert len(ei.value.reasons) == 3


def test_accepted_but_not_approved_lever_still_blocks():
    sc = scenario_with_needs()
    r = promotion_blockers(sc, [approved("bid_participation")], {"LV-001": lever(LeverStatus.ACCEPTED)}, [gate_assumption(status=AssumptionStatus.CONFIRMED)])
    assert any("LV-001" in x for x in r)


def test_pending_or_rejected_approval_blocks_and_rejected_levers_cannot_ride_along():
    sc = scenario_with_needs()
    pending = ApprovalRequest.request("AP-011", "bid_participation", "A")
    assert any("승인 대기" in x for x in promotion_blockers(sc, [pending], {"LV-001": lever(LeverStatus.APPROVED)}, []))
    rejected = pending.decide(Role.CEO_BOARD, False)
    assert any("승인 대기/거절" in x for x in promotion_blockers(sc, [rejected], {"LV-001": lever(LeverStatus.APPROVED)}, []))
    assert any("LV-001" in x for x in promotion_blockers(sc, [approved("bid_participation")], {"LV-001": lever(LeverStatus.REJECTED)}, []))


def test_invariant_assumption_does_not_block_promotion():
    sc = scenario_with_needs()
    ok = promote(sc, canonical_hash({"k": 1}), [approved("bid_participation")], {"LV-001": lever(LeverStatus.APPROVED)}, [gate_assumption(decision_invariant=True)])
    assert ok.version == 1 and ok.scenario_id == "A" and ok.lever_ids == ("LV-001",)


def test_promotion_increments_version_and_records_approvals():
    sc = scenario_with_needs()
    args = (sc, canonical_hash({"k": 1}), [approved("bid_participation")], {"LV-001": lever(LeverStatus.APPLIED)}, [gate_assumption(status=AssumptionStatus.CONFIRMED)])
    v1 = promote(*args)
    v2 = promote(*args, current=v1)
    assert (v1.version, v2.version) == (1, 2) and v2.approved_by == ("AP-010",)


def test_infeasible_scenario_never_promotes():
    sc = make_scenario(infeasibility=Infeasibility(min_relaxation=(RelaxationItem(constraint="x", slack=1.0),)))
    assert "불가능(infeasible) 시나리오" in promotion_blockers(sc, [], {}, [])


def test_plan_of_record_is_immutable_value():
    p = PlanOfRecord(version=1, scenario_id="A", input_hash="sha256:" + "0" * 64, approved_by=("AP-1",))
    with pytest.raises(ValidationError):
        p.version = 2  # type: ignore[misc]
