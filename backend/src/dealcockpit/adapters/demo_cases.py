"""대회 필수 3케이스(정상 · 변경 · 정보부족/거절)를 실제 서비스 호출로 실행한다. 회신은 모두 모의(SIMULATED) 또는 사람 입력.

각 케이스는 (워크스페이스, 단계 로그)를 돌려준다. 로그는 증거 파일과 테스트가 같이 쓴다.
"""
from __future__ import annotations

from dealcockpit.application.negotiation import NegotiationService, WorkflowError
from dealcockpit.application.ports import RepositoryPort, SolverPort
from dealcockpit.domain.common import Provenance, Role
from dealcockpit.domain.workflow import ReplyOutcome

from .golden_loader import load_case, load_reply_stats, mock_reply

CEO, SALES, PROD, EXEC = Role.CEO_BOARD, Role.SALES, Role.PRODUCTION, Role.SALES_EXEC


def _svc(repo: RepositoryPort, solver: SolverPort) -> NegotiationService:
    return NegotiationService(repo, solver, load_case(), load_reply_stats())


def _summ(out, sid="A"):
    s = next(x for x in out.scenarios if x.scenario_id == sid)
    return {"scenario": sid, "accept_pct": round(100 * s.accepted_pct["PL-02"], 1), "net_eok": round(s.financials.net_krw / 1e8, 2),
            "net_vs_baseline_eok": round(s.kpis.get("net_vs_baseline_krw", 0) / 1e8, 2),
            "actions": list(s.capacity_actions), "otif": s.kpis["otif_existing_pct"], "feasible": s.infeasibility is None}


class Runner:
    def __init__(self, svc: NegotiationService, ws_id: str):
        self.svc, self.ws_id, self.log = svc, ws_id, []

    def step(self, what: str, **data):
        self.log.append({"step": len(self.log) + 1, "what": what, **data})

    def rid(self, topic: str) -> str:
        ws = self.svc._load(self.ws_id)
        return next(r for r, t in ws.request_topics.items() if t == topic)

    def reply(self, topic: str, variant: str | None = None, *, outcome=None, terms=None, text="", human=False):
        if variant:
            o, terms, text = mock_reply(topic, variant)
            outcome = ReplyOutcome(o)
        ws, out = self.svc.receive(self.ws_id, self.rid(topic), outcome, terms or {}, text, actor="담당자", role=SALES,
                                   provenance=Provenance.HUMAN_INPUT if human else Provenance.SIMULATED)
        self.step(f"회신 {topic}:{variant or outcome.value}", candidate=_summ(out), as001_blocks=ws.assumptions["AS-001"].blocks_submission)
        return ws, out

    def approve_all(self, approve=True):
        ws = self.svc._load(self.ws_id)
        for aid, ap in ws.approvals.items():
            if ap.status.value == "PENDING":
                ws = self.svc.decide(self.ws_id, aid, ap.approver, approve, actor=f"{ap.approver.value} 담당")
                self.step(f"{'승인' if approve else '거절'} {ap.action}", by=ap.approver.value)
        return ws


def case_normal(repo: RepositoryPort, solver: SolverPort, ws_id: str = "WS-NORMAL") -> Runner:
    svc = _svc(repo, solver)
    r = Runner(svc, ws_id)
    ws = svc.open(ws_id, "영업 김")
    r.step("개설", as001_blocks=ws.assumptions["AS-001"].blocks_submission, candidate=_summ(svc.solve(ws)))
    ws, order = svc.plan_inquiries(ws_id, "영업 김")
    r.step("문의 계획(프로브 가치 순)", order=[ws.request_topics[i] for i in order])
    for rid in order:
        svc.send(ws_id, rid, "영업 김", SALES)
    r.step("문의 발송", count=len(order))
    r.reply("AS-001_capacity_unit", "confirm_month")
    r.reply("OPT-02_formula_revalidation", "slow_conditional")
    r.reply("MB_recontract", "counter_moq")
    r.reply("C201_partial_award", "partial_ok")
    r.reply("LV_outsourcing_PL-02", "accept")
    r.reply("INTERNAL_OVERTIME", outcome=ReplyOutcome.ACCEPT, terms={"qty_per_month": 30000}, text="주말 1교대 추가로 월 3만 EA 가능", human=True)
    r.reply("C101_discount_for_delay", "accept")
    ws, res = svc.submit(ws_id, "A", "영업 김", SALES)
    r.step("상신 A", submitted=res.submitted, blockers=list(res.blockers),
           approvals=[(ws.approvals[a].action, ws.approvals[a].approver.value) for a in res.approval_ids])
    r.approve_all(True)
    ws = svc.promote_plan(ws_id, "영업 김", SALES)
    r.step("확정 계획", plan_version=ws.plan.version, scenario=ws.plan.scenario_id, levers=list(ws.plan.lever_ids),
           approvals=list(ws.plan.approved_by), final=_summ(svc.solve(ws, candidate=False)))
    return r


def case_change(repo: RepositoryPort, solver: SolverPort, ws_id: str = "WS-NORMAL") -> Runner:
    """정상 케이스의 확정 계획 위에서 조건 변경: ① 환율 하락+단가 −10%(결론 유지 확인)
    ② 품질위원회 회수 의결(다른 부서 영향) → 재상신 → CEO 가 F2 전환 투자 거절 → 옵션 제외 대안 → 재승인 → 재확정."""
    svc = _svc(repo, solver)
    r = Runner(svc, ws_id)
    before = _summ(svc.solve(svc._load(ws_id), candidate=False))
    ws, out = svc.change_conditions(ws_id, "재무 박", Role.FINANCE, fx_label="downside", price_pressure_pct=-10.0)
    after = _summ(out)
    r.step("조건 변경: 환율 1,420 + 단가 −10%", before=before, after=after, plan_stale=ws.plan_stale,
           decision_kept=before["accept_pct"] == after["accept_pct"] and before["actions"] == after["actions"])
    svc.submit(ws_id, "A", "영업 김", SALES)
    r.approve_all(True)
    ws = svc.promote_plan(ws_id, "영업 김", SALES)
    r.step("재확정(결론 유지, 수치 갱신)", plan_version=ws.plan.version, final=_summ(svc.solve(ws, candidate=False)))

    ws, out = svc.change_conditions(ws_id, "품질위원회", Role.QUALITY, recall_replacement_week=3)
    r.step("품질위원회 회수 의결(W03 대체 생산)", plan_stale=ws.plan_stale, after=_summ(out), baseline_otif=out.baseline.kpis["otif_existing_pct"])
    ws, res = svc.submit(ws_id, "A", "영업 김", SALES)
    r.step("재상신 A", submitted=res.submitted, blockers=list(res.blockers),
           approvals=[(ws.approvals[a].action, ws.approvals[a].approver.value) for a in res.approval_ids])
    for aid in res.approval_ids:
        ap = svc._load(ws_id).approvals[aid]
        reject = ap.action == "capex_investment"
        svc.decide(ws_id, aid, ap.approver, not reject, actor=f"{ap.approver.value} 담당", note="회수 사고 중 F2 전환 투자 보류" if reject else None)
        r.step(f"{'거절' if reject else '승인'} {ap.action}", by=ap.approver.value)
    ws = svc._load(ws_id)
    r.step("투자 거절 반영 → 옵션 제외 대안 재계산", excluded=list(ws.excluded_options), candidate=_summ(svc.solve(ws)),
           plan_still=ws.plan.version, submitted=ws.submitted_scenario)
    ws, res = svc.submit(ws_id, "A", "영업 김", SALES)
    r.step("재상신 A(F2 전환 없이)", submitted=res.submitted, blockers=list(res.blockers),
           approvals=[(ws.approvals[a].action, ws.approvals[a].approver.value) for a in res.approval_ids])
    if res.submitted:
        r.approve_all(True)
        ws = svc.promote_plan(ws_id, "영업 김", SALES)
        r.step("재확정(회수·투자 거절 반영)", plan_version=ws.plan.version, final=_summ(svc.solve(ws, candidate=False)))
    return r


def case_reject(repo: RepositoryPort, solver: SolverPort, ws_id: str = "WS-REJECT") -> Runner:
    """정보 부족과 거절의 연쇄: 품질·특근·양해 거절 / 외주 불리 역제안 수락 / 무응답 만료→상신 차단→재문의
    / 바이어 전량 일괄 조건 → 손실 안만 남음 → 상신 차단(참여 비권고)."""
    svc = _svc(repo, solver)
    r = Runner(svc, ws_id)
    svc.open(ws_id, "영업 김")
    ws, order = svc.plan_inquiries(ws_id, "영업 김")
    for rid in order:
        svc.send(ws_id, rid, "영업 김", SALES)
    r.step("문의 발송", count=len(order))
    r.reply("OPT-02_formula_revalidation", "reject")
    r.reply("MB_recontract", "counter_moq")
    ws, out = r.reply("LV_outsourcing_PL-02", "counter_worse")
    lid = next(k for k, v in ws.lever_topics.items() if v == "LV_outsourcing_PL-02")
    try:
        svc.accept_counter(ws_id, lid, "생산 최", PROD, breakeven_multiplier=1.967)
        r.step("외주 역제안(1.6배·월 3만·6주) 수락 — 손익분기 1.97배 이하", candidate=_summ(svc.solve(svc._load(ws_id))))
    except WorkflowError as e:
        r.step("외주 역제안 거부", reason=str(e))
    r.reply("INTERNAL_OVERTIME", outcome=ReplyOutcome.REJECT, text="주 52시간 한도와 교대 인력 부족으로 특근 불가", human=True)
    r.reply("C101_discount_for_delay", "reject")

    ws = svc.advance_clock(ws_id, "2026-07-12")  # 생산기술·바이어 무응답 → 만료
    r.step("기한 경과(7/12): 무응답 만료", expired=[ws.request_topics[k] for k, v in ws.requests.items() if v.status.value == "EXPIRED"],
           blocked_tasks=[t.title[:30] for t in ws.tasks.values() if t.status.value == "BLOCKED"])
    ws, res = svc.submit(ws_id, "A", "영업 김", SALES)
    r.step("상신 시도 A — 정보 부족으로 차단", submitted=res.submitted, blockers=list(res.blockers))
    svc.reask(ws_id, r.rid("AS-001_capacity_unit"), "2026-07-16", "영업 김", SALES)
    r.reply("AS-001_capacity_unit", outcome=ReplyOutcome.ACCEPT, terms={"period": "per_month"}, text="(재문의 회신) 월 명목 능력이 맞습니다", human=True)
    ws, res = svc.submit(ws_id, "A", "영업 김", SALES)
    r.step("재상신 A — 바이어 부분 낙찰 미확인으로 차단", submitted=res.submitted, blockers=list(res.blockers))
    svc.reask(ws_id, r.rid("C201_partial_award"), "2026-07-18", "영업 김", SALES)
    r.reply("C201_partial_award", "all_or_nothing")
    ws, res = svc.submit(ws_id, "A", "영업 김", SALES)
    r.step("재상신 A(전량 조건) — 손실 안만 남음", submitted=res.submitted, blockers=list(res.blockers))
    ws = svc._load(ws_id)
    r.step("최종 상태: 입찰 참여 비권고 — 바이어와 부분 낙찰 재협상 또는 불참",
           open_tasks=[(t.title[:40], t.status.value) for t in ws.tasks.values() if t.status.value in ("OPEN", "BLOCKED")], plan=ws.plan)
    return r
