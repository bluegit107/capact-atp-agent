"""W05 에이전트(13.5): G2 숫자 검증기 예시표 전부, 금지 도구, 실패 경로(NEEDS_HUMAN·MANUAL_REVIEW), 녹화·재생 결정성·불일치·녹화 없음."""
from __future__ import annotations

import json
from types import SimpleNamespace as NS

import pytest

from dealcockpit.adapters.golden_loader import load_w05_inputs
from dealcockpit.domain.approval import DEFAULT_POLICY
from dealcockpit.domain.common import Role
from dealcockpit.w05.agent import run_agent, validate_text
from dealcockpit.w05.flow import W05Flow


@pytest.mark.parametrize("text,ok", [
    ("더마는 {{calc:12}}가 필요합니다", True), ("OPT-02(F2 더마 전환안)는 M-B 재계약이 필요합니다", True),
    ("대안은 두 가지입니다", True), ("대안은 2가지입니다", True), ("더마는 약 150만 EA가 필요합니다", False), ("단가가 10% 내려가면", False),
    ("3개월 연속 감소", False), ("백팔십억 원이 듭니다", False), ("납품 개시는 2026-10-01입니다", False), ("{{calc:999999}}", False),
    ("C-201, L-02, RF-PROD-01, F1, A-04, G15, S2-b 는 코드명", True)])
def test_g2_validator_examples(text, ok):
    assert (validate_text(text, {12}, set()) is None) is ok


@pytest.fixture()
def ledger():
    inp = load_w05_inputs()
    f = W05Flow(inp, DEFAULT_POLICY)
    L = f.receive_rfp("2026-07-01", "sales", Role.SALES)
    b = next(r for r in L.requests if r.to_external)
    f.send_request(L, b.id, "2026-07-01", "s", Role.SALES)
    f.answer_request(L, b.id, {"supply_start_date": "2027-04-01", "partial_award_allowed": True}, "회신", "2026-07-02", "d", Role.DEMO_ADMIN)
    f.confirm_from_request(L, b.id, "2026-07-02", "s", Role.SALES)
    return inp, L


def fake_client(script):
    """script: 응답마다 [(tool_name, input), ...] 목록."""
    it = iter(script)

    def create(**kw):
        assert kw["model"] and "tool_choice" not in kw and kw["fallbacks"] == "default"
        calls = next(it)
        return NS(stop_reason="tool_use", content=[NS(type="tool_use", id=f"tu{i}", name=n, input=a) for i, (n, a) in enumerate(calls)])
    return lambda: NS(beta=NS(messages=NS(create=create)))


def good_script(L):
    b = next(o for o in L.options if o.label == "B" and o.status == "PROPOSED")
    warn = next(f for f in L.flags if f.option_id == b.id and f.severity == "WARN")
    fid = warn.evidence_ids[0]
    return [
        [("ledger_get_bid_snapshot", {}), ("calc_risk_flags", {})],
        [("risk_flag_explain", {"flag_id": warn.id, "text": "근거 {{calc:%d}} 기준 확인 필요" % fid}),
         ("option_explain", {"option_id": b.id, "text": "기초 일부와 더마 전량을 OPT-01(L-02 더마 증설)로 맞추는 안입니다. 충족률 {{calc:%d}}." % b.metric_ids["fill_PL-02"]})],
        [("finish", {"situation_summary": "대안 세 가지 중 B가 기존 약속을 지키며 충족합니다", "option_ids": [b.id],
                     "ai_opinion": {"preferred_option_id": b.id, "reasoning": "기존 약속 보호와 페널티 노출 기준", "criteria": ["기존 약속 보호"]},
                     "next_actions": [{"actor_role": "영업", "action": "대안 B 상신 검토"}], "open_risks": ["C-101 감소 원인은 확인 필요"], "risk_flag_ids": [warn.id]})]]


def test_live_completes_records_and_replay_reproduces(ledger, tmp_path):
    inp, L = ledger
    r = run_agent(L, inp, "INFO_REQUEST_ANSWERED", 1, mode="live", scenario_id="s1", client_factory=fake_client(good_script(L)), replay_dir=tmp_path)
    assert r.status == "COMPLETED" and r.output["ai_opinion"]["preferred_option_id"]
    rec = tmp_path / "s1" / "INFO_REQUEST_ANSWERED__r1__1.json"
    assert rec.exists() and "api_key" not in rec.read_text(encoding="utf-8").lower()
    b = next(o for o in L.options if o.label == "B" and o.status == "PROPOSED")
    assert b.narrative and "{{calc:" in b.narrative


def _fresh():
    inp = load_w05_inputs()
    f = W05Flow(inp, DEFAULT_POLICY)
    L = f.receive_rfp("2026-07-01", "sales", Role.SALES)
    b = next(r for r in L.requests if r.to_external)
    f.send_request(L, b.id, "2026-07-01", "s", Role.SALES)
    f.answer_request(L, b.id, {"supply_start_date": "2027-04-01", "partial_award_allowed": True}, "회신", "2026-07-02", "d", Role.DEMO_ADMIN)
    f.confirm_from_request(L, b.id, "2026-07-02", "s", Role.SALES)
    return inp, L


def test_replay_is_deterministic_and_detects_mismatch(tmp_path):
    inp, L = _fresh()
    run_agent(L, inp, "INFO_REQUEST_ANSWERED", 1, mode="live", scenario_id="s1", client_factory=fake_client(good_script(L)), replay_dir=tmp_path)
    inpA, A_ = _fresh()
    inpB, B_ = _fresh()
    ra = run_agent(A_, inpA, "INFO_REQUEST_ANSWERED", 1, mode="replay", scenario_id="s1", replay_dir=tmp_path)
    rb = run_agent(B_, inpB, "INFO_REQUEST_ANSWERED", 1, mode="replay", scenario_id="s1", replay_dir=tmp_path)
    assert ra.status == rb.status == "COMPLETED" and A_.model_dump(exclude={"agent_runs"}) == B_.model_dump(exclude={"agent_runs"})
    assert A_.model_dump(exclude={"agent_runs"}) == L.model_dump(exclude={"agent_runs"})
    p = tmp_path / "s1" / "INFO_REQUEST_ANSWERED__r1__1.json"
    rec = json.loads(p.read_text(encoding="utf-8"))
    rec["tool_calls"][0]["output"]["status"] = "CHANGED"
    p.write_text(json.dumps(rec, ensure_ascii=False), encoding="utf-8")
    inpC, C_ = _fresh()
    rc = run_agent(C_, inpC, "INFO_REQUEST_ANSWERED", 1, mode="replay", scenario_id="s1", replay_dir=tmp_path)
    assert rc.status == "FAILED" and "REPLAY_MISMATCH" in rc.errors[0] and any(t.type == "MANUAL_REVIEW" for t in C_.tasks)


def test_missing_recording_needs_human(tmp_path):
    inp, L = _fresh()
    r = run_agent(L, inp, "BID_RECEIVED", 1, mode="replay", scenario_id="manual", replay_dir=tmp_path)
    assert r.status == "NEEDS_HUMAN" and "녹화 없음" in r.errors[0]


def test_forbidden_tool_is_not_executed_and_bad_output_fails_twice(tmp_path):
    inp, L = _fresh()
    bad = {"situation_summary": "더마는 약 150만 EA가 필요합니다", "next_actions": [{"actor_role": "영업", "action": "x"}]}
    script = [[("approval_decide", {"id": 1}), ("finish", bad)], [("finish", bad)]]
    r = run_agent(L, inp, "INFO_REQUEST_ANSWERED", 1, mode="live", scenario_id="x", client_factory=fake_client(script), replay_dir=tmp_path)
    assert any(c["name"] == "approval_decide" and c["allowed"] is False for c in r.tool_calls)
    assert all(a.status != "APPROVED" for a in L.approvals)
    assert r.status == "NEEDS_HUMAN" and any(t.type == "MANUAL_REVIEW" for t in L.tasks)


def test_flag_explain_rejects_values_outside_its_evidence(tmp_path):
    inp, L = _fresh()
    f = next(x for x in L.flags if x.severity == "WARN")
    other = next(m["id"] for m in L.runs[-1].metrics if m["id"] not in f.evidence_ids)
    from dealcockpit.w05.agent import Tools

    out = Tools(L, inp).call("risk_flag_explain", {"flag_id": f.id, "text": "{{calc:%d}}" % other})
    assert "G16" in out["error"]


def test_mock_model_runs_the_same_loop_and_passes_g2_at_each_trigger():
    """API 키 없이: 모의 모델이 실제와 같은 실행 루프·도구·숫자 검증기를 통과한다(접수, 대안 구성, 가정 교체)."""
    inp = load_w05_inputs()
    f = W05Flow(inp, DEFAULT_POLICY)
    L = f.receive_rfp("2026-07-01", "sales", Role.SALES)
    r0 = run_agent(L, inp, "BID_RECEIVED", 1, mode="mock")
    assert r0.status == "COMPLETED" and r0.model.startswith("mock") and r0.output["ai_opinion"] is None
    b = next(r for r in L.requests if r.to_external)
    f.send_request(L, b.id, "2026-07-01", "s", Role.SALES)
    f.answer_request(L, b.id, {"supply_start_date": "2027-04-01", "partial_award_allowed": True}, "회신", "2026-07-02", "d", Role.DEMO_ADMIN)
    f.confirm_from_request(L, b.id, "2026-07-02", "s", Role.SALES)
    r1 = run_agent(L, inp, "INFO_REQUEST_ANSWERED", 2, mode="mock")
    assert r1.status == "COMPLETED", r1.errors
    pref = next(o for o in L.options if o.id == r1.output["ai_opinion"]["preferred_option_id"])
    assert pref.feasibility == "FEASIBLE" and all(o.narrative for o in L.options if o.status == "PROPOSED")
    assert any(fl.explained_by_agent for fl in L.flags)
    f.submit(L, pref.id, "2026-07-04", "s", Role.SALES)
    for a in [a for a in L.approvals if a.status == "PENDING"]:
        f.decide(L, a.id, "APPROVED", None, "2026-07-06", "ceo", Role.CEO_BOARD)
    f.supersede(L, "supply_start_date", "2026-10-01", "앞당김", "2026-07-08", "s", Role.SALES)
    r2 = run_agent(L, inp, "ASSUMPTION_SUPERSEDED", 3, mode="mock")
    assert r2.status == "COMPLETED" and r2.output["impact_of_change"]
