"""협상 루프(S4): 문의 → 회신 → 레버·가정·요건 상태 → 재풀이 → 상신(게이트) → 승인 → 확정 계획(Plan of Record).

원칙
  - 모든 상태 변경은 이 서비스의 유스케이스로만 일어나고, 저장(낙관적 락)과 감사 이벤트가 함께 남는다.
  - 회신은 모의(SIMULATED) 또는 사람 입력(HUMAN_INPUT). 회신의 효과는 주제별 규칙으로만 반영(LLM 판단 없음).
  - 가설·요청 중 레버는 후보 계산(브랜치)에만 쓰이고, 확정 계획은 승인된 레버로 다시 풀어 만든다.
  - 승인 주체는 권한표(정책 버전 고정), 확정 파라미터는 레지스트리(사람만 변경).
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from dealcockpit.domain.approval import ApprovalRequest
from dealcockpit.domain.assumption import Assumption, AssumptionStatus, EvidenceTest
from dealcockpit.domain.common import Provenance, Role
from dealcockpit.domain.lever import Lever, LeverStatus, LeverType
from dealcockpit.domain.plan import AuditEvent, PromotionBlocked, promote
from dealcockpit.domain.solution import ApprovalNeed, Scenario, SolverOutput
from dealcockpit.domain.workflow import InfoRequest, Reply, ReplyOutcome, Task, TaskStatus
from dealcockpit.domain.workspace import RequirementReply, Workspace

from .case import BuildParams, Case
from .gate import gate_capacity_assumption
from .ports import RepositoryPort, SolverPort
from .probes import probe_levers
from .scenarios import PRESETS, run_scenarios
from .settings_from_registry import settings_from_registry

CANDIDATE_STATUSES = {LeverStatus.ACCEPTED, LeverStatus.APPROVED, LeverStatus.APPLIED}
BOARD_BUFFER_DAYS = 3

# 주제 -> (문의 대상 역할, 상대방, 연결: 레버 키 | 가정 ID)
TOPICS = {
    "AS-001_capacity_unit": (Role.PRODUCTION, "생산기술팀", "AS-001"),
    "LV_outsourcing_PL-02": (Role.PRODUCTION, "외주사 V-OUT-1(가상)", "outsourcing"),
    "INTERNAL_OVERTIME": (Role.PRODUCTION, "생산(근무형태·인력)", "overtime"),
    "C101_discount_for_delay": (Role.SALES, "C-101 구매담당(가상)", "consent_C-101"),
    "OPT-02_formula_revalidation": (Role.QUALITY, "품질팀", "AS-005"),
    "MB_recontract": (Role.PROCUREMENT, "V-205(M-B 독점 공급사)", "AS-005"),
    "C201_partial_award": (Role.SALES, "C-201 바이어(가상)", "AS-004"),
}
REQ_OF_TOPIC = {"OPT-02_formula_revalidation": "formula_revalidation", "MB_recontract": "M-B_recontract"}


class WorkflowError(ValueError):
    pass


@dataclass(frozen=True)
class SubmitResult:
    submitted: bool
    blockers: tuple[str, ...]
    approval_ids: tuple[str, ...]


def lever_action(lv: Lever) -> str | None:
    t = str(lv.params.get("target", ""))
    if lv.type is LeverType.CAPACITY_DELTA and t.startswith("outsourcing:"):
        return "outsourcing_contract"
    if lv.type is LeverType.CAPACITY_DELTA and t.startswith("overtime:"):
        return "overtime_plan"
    if lv.type is LeverType.TIME_SHIFT:
        return "customer_schedule_change"
    if lv.type is LeverType.PRICE_ADJUST:
        return "price_renegotiation_C-101"
    return None  # 바이어 조건(scope_change) 등은 우리 승인 대상이 아님


def _iso_week(d: date) -> str:
    iso = d.isocalendar()
    return f"{iso.year}-W{iso.week:02d}"


class NegotiationService:
    def __init__(self, repo: RepositoryPort, solver: SolverPort, case: Case, reply_stats: dict[str, tuple[float, float]]):
        self.repo, self.solver, self.case, self.stats = repo, solver, case, reply_stats

    # ── 내부 유틸 ──────────────────────────────────────────────────────────────
    def _load(self, ws_id: str) -> Workspace:
        ws = self.repo.load(ws_id)
        if ws is None:
            raise WorkflowError(f"워크스페이스 {ws_id} 없음")
        return ws

    def _commit(self, ws: Workspace, expected: int, actor: str, role: Role | str, action: str, obj: str,
                input_hash: str | None = None, acting_as: Role | None = None) -> Workspace:
        ws, eid = ws.next_id("EV")
        saved = self.repo.save(ws, expected)
        at = datetime.fromisoformat(ws.today) + timedelta(seconds=saved.counters.get("EV", 0))
        self.repo.append_audit(ws.workspace_id, AuditEvent(
            event_id=eid, at=at, actor=actor, actor_role=role.value if isinstance(role, Role) else role,
            acting_as_role=acting_as.value if acting_as else None,
            action=action, object_ref=obj, input_hash=input_hash))
        return saved

    def params(self, ws: Workspace, candidate: bool = True) -> BuildParams:
        """candidate=True: 수락·승인된 레버로 후보 계산(브랜치). False: 승인된 레버만(확정 계획용)."""
        settings, _ = settings_from_registry(ws.registry)
        allowed = CANDIDATE_STATUSES if candidate else {LeverStatus.APPROVED, LeverStatus.APPLIED}
        levers = tuple(lv for lv in ws.levers.values() if lv.status in allowed)
        req = {k: v.weeks for k, v in ws.requirement_replies.items() if v.status == "ACCEPTED" and v.weeks is not None}
        rejected = {k for k, v in ws.requirement_replies.items() if v.status == "REJECTED"}
        if rejected:  # 요건 하나라도 거절이면 그 옵션은 가용 시점을 말할 수 없다
            req = {k: v for k, v in req.items() if k not in REQ_OF_TOPIC.values()}
        return BuildParams(
            interpretation="CONFIRMED" if ws.confirmed_monthly else "S_MONTH", confirmed_monthly=ws.confirmed_monthly,
            fx_label=ws.fx_label, price_pressure_pct=ws.price_pressure_pct, levers=levers, include_hypothetical=candidate,
            requirement_delays=req, recall_replacement_week=ws.recall_replacement_week, excluded_options=ws.excluded_options,
            settings=settings,
        )

    def solve(self, ws: Workspace, candidate: bool = True) -> SolverOutput:
        return run_scenarios(self.solver, self.case, self.params(ws, candidate), policy=ws.policy)

    # ── 1. 개설 ───────────────────────────────────────────────────────────────
    def open(self, ws_id: str, actor: str, today: str = "2026-07-01", role: Role = Role.SALES) -> Workspace:
        if self.repo.load(ws_id) is not None:
            raise WorkflowError(f"{ws_id} 이미 존재")
        a = {
            "AS-001": Assumption(assumption_id="AS-001", statement="strat_capacity 용량의 기간 단위(월/연)·실효 능력", owner_role=Role.PRODUCTION,
                                 impact_scope=("용량", "수락률")),
            "AS-002": Assumption(assumption_id="AS-002", statement="출하 기록 없는 오래된 수주 598건(누락/취소 불명)은 확정 약속에서 제외",
                                 owner_role=Role.SALES, impact_scope=("기존 약속",)),
            "AS-003": Assumption(assumption_id="AS-003", statement="C-101 신규 발주 감소가 일시적인지 구조적인지(현재 최근 13주 평균 사용)",
                                 owner_role=Role.SALES, impact_scope=("수요",)),
            "AS-004": Assumption(assumption_id="AS-004", statement="C-201 부분 낙찰 허용·공급 개시일(2026-10-05 가정)",
                                 owner_role=Role.SALES, impact_scope=("입찰 조건",)),
            "AS-005": Assumption(assumption_id="AS-005", statement="OPT-02 선행 요건(처방 재검증·M-B 재계약) 소요", owner_role=Role.QUALITY,
                                 impact_scope=("설비 옵션",)),
        }
        ws = Workspace(workspace_id=ws_id, as_of=self.case.derived["as_of"], today=today, assumptions=a)
        a1, _ = gate_capacity_assumption(self.solver, self.case, self.params(ws), a["AS-001"])
        ws = ws.model_copy(update={"assumptions": {**a, "AS-001": a1}})
        return self._commit(ws, 0, actor, role, "open", ws_id)

    # ── 2. 문의 계획: 프로브 가치 순으로 레버·문의·작업 생성 ─────────────────────────────
    def plan_inquiries(self, ws_id: str, actor: str, role: Role = Role.SALES) -> tuple[Workspace, list[str]]:
        ws = self._load(ws_id)
        exp = ws.version
        probes = {p.key: p for p in probe_levers(self.solver, self.case, self.params(ws), self.stats)}
        today = date.fromisoformat(ws.today)
        deadline = date.fromisoformat("2026-07-22") - timedelta(days=BOARD_BUFFER_DAYS)
        d0 = date.fromisoformat(self.case.derived["week0_monday"])
        made: list[tuple[float, str]] = []

        def add_request(ws: Workspace, topic: str, question: str, lever_id: str | None, assumption_id: str | None, score: float):
            days = self.stats.get(topic, (2.0, 0.5))[0]
            ws, rid = ws.next_id("IR")
            to_role, cp, _ = TOPICS[topic]
            ans = min(today + timedelta(days=math.ceil(days) + 2), deadline)
            req = InfoRequest(request_id=rid, to_role=to_role, counterparty=cp, question=question, answer_by=ans,
                              linked_lever_id=lever_id, linked_assumption_id=assumption_id)
            ws, tid = ws.next_id("TK")
            task = Task(task_id=tid, title=f"[{cp}] {question[:40]}", owner_role=to_role, due=ans, linked_request_id=rid)
            made.append((score, rid))
            return ws.model_copy(update={"requests": {**ws.requests, rid: req}, "request_topics": {**ws.request_topics, rid: topic},
                                         "tasks": {**ws.tasks, tid: task}})

        def add_lever(ws: Workspace, key: str, type_: LeverType, params: dict, owner: Role, cp: str, topic: str):
            ws, lid = ws.next_id("LV")
            lv = Lever(lever_id=lid, type=type_, params=params, owner_role=owner, counterparty=cp, confidence=self.stats.get(topic, (2, 0.5))[1],
                       provenance=Provenance.ASSUMPTION)
            return ws.model_copy(update={"levers": {**ws.levers, lid: lv}, "lever_topics": {**ws.lever_topics, lid: topic}}), lid

        # AS-001 은 결정이 해석에 좌우될 때만 문의(게이트가 막고 있음)
        if ws.assumptions["AS-001"].blocks_submission:
            ws = add_request(ws, "AS-001_capacity_unit", "strat_capacity_cv 의 capacity_qty 는 월 능력입니까, 연 능력입니까? L-02 실효 월 능력은?",
                             None, "AS-001", float("inf"))
        p = probes["outsourcing"]
        ws, lid = add_lever(ws, "outsourcing", LeverType.CAPACITY_DELTA, dict(
            target="outsourcing:V-OUT-1", product="PL-02", qty_per_month=50000, available_from_week=_iso_week(d0 + timedelta(weeks=5)),
            unit_cost_multiplier=1.35), Role.PRODUCTION, "외주사 V-OUT-1(가상)", "LV_outsourcing_PL-02")
        ws = add_request(ws, "LV_outsourcing_PL-02", f"더마 월 5만 EA 외주 가능 여부·단가(손익분기 {p.breakeven.get('max_unit_cost_multiplier')}배)", lid, None, p.priority_score)
        p = probes["overtime"]
        ws, lid = add_lever(ws, "overtime", LeverType.CAPACITY_DELTA, dict(
            target="overtime:L-02", product="PL-02", qty_per_month=30000, available_from_week=_iso_week(d0 + timedelta(weeks=3)),
            unit_cost_multiplier=1.5), Role.PRODUCTION, "생산(근무형태·인력)", "INTERNAL_OVERTIME")
        ws = add_request(ws, "INTERNAL_OVERTIME", "L-02 특근 월 3만 EA 가능 여부(근무형태·주 52시간)", lid, None, p.priority_score)
        p = probes["consent_C-101"]
        ws, lid = add_lever(ws, "consent_C-101", LeverType.TIME_SHIFT, dict(target="customer:C-101", shift_days=14),
                            Role.SALES, "C-101 구매담당(가상)", "C101_discount_for_delay")
        ws = add_request(ws, "C101_discount_for_delay", f"C-101 일부 더마 납기 14일 양해 요청(양해 대가 상한 {p.breakeven['max_concession_krw']/1e8:.1f}억)", lid, None, p.priority_score)
        p = probes.get("opt02_replies") or probes.get("opt02_faster")
        ws = add_request(ws, "OPT-02_formula_revalidation", "F2 더마 전환(OPT-02) 처방 재검증 소요·가능 여부", None, "AS-005", p.priority_score)
        ws = add_request(ws, "MB_recontract", "F2 전환용 M-B 재계약·MOQ·위약 조건", None, "AS-005", p.priority_score)
        p = probes["partial_award"]
        ws = add_request(ws, "C201_partial_award", "C-201 더마 부분 낙찰·단계 납품(공급 개시일) 허용 여부", None, "AS-004", p.priority_score)
        ordered = [rid for _, rid in sorted(made, key=lambda x: -x[0])]
        return self._commit(ws, exp, actor, role, "plan_inquiries", ",".join(ordered)), ordered

    # ── 3. 발송 ───────────────────────────────────────────────────────────────
    def send(self, ws_id: str, request_id: str, actor: str, role: Role) -> Workspace:
        ws = self._load(ws_id)
        exp = ws.version
        req = ws.requests[request_id].send()
        levers = dict(ws.levers)
        if req.linked_lever_id:
            levers[req.linked_lever_id] = levers[req.linked_lever_id].transition(LeverStatus.REQUESTED)
        tasks = {k: (t.move(TaskStatus.WAITING_REPLY) if t.linked_request_id == request_id else t) for k, t in ws.tasks.items()}
        ws = ws.model_copy(update={"requests": {**ws.requests, request_id: req}, "levers": levers, "tasks": tasks})
        return self._commit(ws, exp, actor, role, "send_request", request_id)

    # ── 4. 회신 수신 → 주제별 효과 → 재풀이 ──────────────────────────────────────────
    def receive(self, ws_id: str, request_id: str, outcome: ReplyOutcome, terms: dict, raw_text: str, actor: str, role: Role,
                provenance: Provenance = Provenance.SIMULATED) -> tuple[Workspace, SolverOutput]:
        ws = self._load(ws_id)
        exp = ws.version
        ws, rpid = ws.next_id("RP")
        reply = Reply(reply_id=rpid, request_id=request_id, raw_text=raw_text, outcome=outcome, terms=terms, provenance=provenance,
                      human_confirmed=provenance is Provenance.HUMAN_INPUT)
        if not reply.usable_for_replan:
            raise WorkflowError("파싱 신뢰도가 낮은 회신은 사람 확인 후 반영")
        req = ws.requests[request_id].receive(reply)
        ws = ws.model_copy(update={"requests": {**ws.requests, request_id: req}, "replies": {**ws.replies, rpid: reply}})
        topic = ws.request_topics[request_id]
        ws = self._apply_reply(ws, topic, req, reply)
        # 작업 상태: 정보가 확보되면 완료. 레버 역제안은 담당자 결정 대기(막힘), 거절은 대안 검토(막힘).
        if topic in ("C201_partial_award", "AS-001_capacity_unit") or (topic in REQ_OF_TOPIC and outcome is not ReplyOutcome.REJECT):
            done, reason = True, ""
        elif outcome in (ReplyOutcome.ACCEPT, ReplyOutcome.CONDITIONAL):
            done, reason = True, ""
        elif outcome is ReplyOutcome.COUNTER:
            done, reason = False, "역제안 — 담당자가 손익분기선과 비교해 수락/거절 결정"
        else:
            done, reason = False, f"회신 {outcome.value} — 대안 검토 필요"
        tasks = {}
        for k, t in ws.tasks.items():
            if t.linked_request_id == request_id and t.status is TaskStatus.WAITING_REPLY:
                t = t.move(TaskStatus.DONE) if done else t.move(TaskStatus.BLOCKED, reason)
            tasks[k] = t
        ws = ws.model_copy(update={"tasks": tasks})
        ws, out = self._resolve(ws)
        return self._commit(ws, exp, actor, role, f"receive_reply:{outcome.value}", request_id, out.input_hash), out

    def _apply_reply(self, ws: Workspace, topic: str, req: InfoRequest, reply: Reply) -> Workspace:
        o, terms = reply.outcome, reply.terms
        ev = EvidenceTest(name=f"회신 {reply.reply_id}", result=f"{o.value} {terms}", evidence_ref=req.request_id)
        if topic == "AS-001_capacity_unit":
            a = ws.assumptions["AS-001"]
            if o in (ReplyOutcome.ACCEPT, ReplyOutcome.COUNTER):
                cap = self.case.plant.line_for_product("PL-02").capacity_qty
                override = terms.get("line_override_monthly", {}).get("L-02")
                confirmed = float(override) if override else float(cap) if terms.get("period") == "per_month" else None
                if confirmed is None:
                    raise WorkflowError("용량 회신에서 월 능력을 읽을 수 없음 — 사람 확인 필요")
                a = a.propose((*a.tests, ev)).confirm()
                return ws.model_copy(update={"assumptions": {**ws.assumptions, "AS-001": a}, "confirmed_monthly": confirmed})
            return ws
        if topic in REQ_OF_TOPIC:
            key = REQ_OF_TOPIC[topic]
            if o is ReplyOutcome.REJECT:
                rr = RequirementReply(status="REJECTED", note=reply.raw_text[:80])
            elif o in (ReplyOutcome.ACCEPT, ReplyOutcome.CONDITIONAL, ReplyOutcome.COUNTER) and "weeks" in terms:
                rr = RequirementReply(status="ACCEPTED", weeks=int(terms["weeks"]), note=str({k: v for k, v in terms.items() if k != "weeks"}))
            else:
                return ws
            reqs = {**ws.requirement_replies, key: rr}
            a = ws.assumptions["AS-005"]
            a = a.model_copy(update={"tests": (*a.tests, ev)})
            if len(reqs) == 2 and a.status is not AssumptionStatus.CONFIRMED:
                a = a.propose(a.tests).confirm()  # 두 요건 모두 회신(수락/거절) → 가용 여부 확정
            return ws.model_copy(update={"requirement_replies": reqs, "assumptions": {**ws.assumptions, "AS-005": a}})
        if topic == "C201_partial_award":
            a = ws.assumptions["AS-004"]
            a = a.propose((*a.tests, ev)).confirm()
            ws = ws.model_copy(update={"assumptions": {**ws.assumptions, "AS-004": a}})
            if terms.get("partial_award_allowed") is False:  # 바이어 조건(사실): 전량 일괄 → 입찰 수락 하한 100%
                ws, lid = ws.next_id("LV")
                lv = Lever(lever_id=lid, type=LeverType.SCOPE_CHANGE, params=dict(target="bid:C-201", accepted_pct_min=1.0, accepted_pct_max=1.0),
                           owner_role=Role.SALES, counterparty="C-201 바이어", confidence=1.0, provenance=Provenance.SIMULATED)
                for st in (LeverStatus.REQUESTED, LeverStatus.ACCEPTED, LeverStatus.APPROVED):
                    lv = lv.transition(st)
                ws = ws.model_copy(update={"levers": {**ws.levers, lid: lv}})
            return ws
        # 레버 주제
        lid = req.linked_lever_id
        lv = ws.levers[lid]
        prm = dict(lv.params)
        if lv.type is LeverType.CAPACITY_DELTA:
            if "qty_per_month" in terms:
                prm["qty_per_month"] = int(terms["qty_per_month"])
            if "unit_cost_multiplier" in terms:
                prm["unit_cost_multiplier"] = float(terms["unit_cost_multiplier"])
            if "lead_time_weeks" in terms:
                prm["available_from_week"] = _iso_week(date.fromisoformat(ws.today) + timedelta(weeks=int(terms["lead_time_weeks"])))
        elif lv.type is LeverType.TIME_SHIFT and "extends_grace_days" in terms:
            prm["shift_days"] = int(terms["extends_grace_days"])
        target = {ReplyOutcome.ACCEPT: LeverStatus.ACCEPTED, ReplyOutcome.CONDITIONAL: LeverStatus.ACCEPTED,
                  ReplyOutcome.COUNTER: LeverStatus.COUNTERED, ReplyOutcome.REJECT: LeverStatus.REJECTED}.get(o)
        if target is None:
            return ws
        lv = Lever(**{**lv.model_dump(), "params": prm}).transition(target)
        return ws.model_copy(update={"levers": {**ws.levers, lid: lv}})

    def _resolve(self, ws: Workspace) -> tuple[Workspace, SolverOutput]:
        """조건이 바뀔 때마다: 후보 재계산 + AS-001 게이트 재평가 + 확정 계획이 낡았는지 표시."""
        a1, _ = gate_capacity_assumption(self.solver, self.case, self.params(ws), ws.assumptions["AS-001"])
        ws = ws.model_copy(update={"assumptions": {**ws.assumptions, "AS-001": a1}})
        out = self.solve(ws)
        stale = ws.plan_stale
        if ws.plan is not None:
            cur = self.solve(ws, candidate=False)
            sc = next((s for s in cur.scenarios if s.scenario_id == ws.plan.scenario_id), None)
            stale = sc is None or cur.input_hash != ws.plan.input_hash
        return ws.model_copy(update={"last_output_hash": out.input_hash, "plan_stale": stale}), out

    # ── 5. 역제안 수락(사람) · 만료 처리 ──────────────────────────────────────────
    def accept_counter(self, ws_id: str, lever_id: str, actor: str, role: Role, breakeven_multiplier: float | None = None) -> Workspace:
        ws = self._load(ws_id)
        exp = ws.version
        lv = ws.levers[lever_id]
        if role is not lv.owner_role:
            raise PermissionError(f"역제안 수락은 레버 담당({lv.owner_role.value})만")
        mult = lv.params.get("unit_cost_multiplier")
        if breakeven_multiplier is not None and mult is not None and mult > breakeven_multiplier:
            raise WorkflowError(f"역제안 단가 {mult}배가 손익분기 {breakeven_multiplier}배를 넘는다 — 수락하면 손해")
        rids = {rid for rid, r in ws.requests.items() if r.linked_lever_id == lever_id}
        tasks = {k: (t.move(TaskStatus.OPEN).move(TaskStatus.DONE) if t.linked_request_id in rids and t.status is TaskStatus.BLOCKED else t)
                 for k, t in ws.tasks.items()}
        ws = ws.model_copy(update={"levers": {**ws.levers, lever_id: lv.transition(LeverStatus.ACCEPTED)}, "tasks": tasks})
        ws, out = self._resolve(ws)
        return self._commit(ws, exp, actor, role, "accept_counter", lever_id, out.input_hash)

    def advance_clock(self, ws_id: str, today: str, actor: str = "system") -> Workspace:
        ws = self._load(ws_id)
        exp = ws.version
        d = date.fromisoformat(today)
        reqs, levers, tasks = dict(ws.requests), dict(ws.levers), dict(ws.tasks)
        for rid, r in ws.requests.items():
            nr = r.expire_if_due(d)
            if nr.status is not r.status:
                reqs[rid] = nr
                if r.linked_lever_id and levers[r.linked_lever_id].status is LeverStatus.REQUESTED:
                    levers[r.linked_lever_id] = levers[r.linked_lever_id].transition(LeverStatus.EXPIRED)
                for k, t in ws.tasks.items():
                    if t.linked_request_id == rid and t.status is TaskStatus.WAITING_REPLY:
                        tasks[k] = t.move(TaskStatus.BLOCKED, "회신 기한 경과(무응답) — 재문의 또는 대안")
        ws = ws.model_copy(update={"today": today, "requests": reqs, "levers": levers, "tasks": tasks})
        ws, out = self._resolve(ws)
        return self._commit(ws, exp, actor, "system", "advance_clock", today, out.input_hash)

    def reask(self, ws_id: str, request_id: str, answer_by: str, actor: str, role: Role) -> Workspace:
        ws = self._load(ws_id)
        exp = ws.version
        req = ws.requests[request_id].reask(date.fromisoformat(answer_by))
        levers, tasks = dict(ws.levers), {}
        if req.linked_lever_id and levers[req.linked_lever_id].status is LeverStatus.EXPIRED:
            levers[req.linked_lever_id] = levers[req.linked_lever_id].transition(LeverStatus.REQUESTED)
        for k, t in ws.tasks.items():
            if t.linked_request_id == request_id and t.status is TaskStatus.BLOCKED:
                t = t.move(TaskStatus.OPEN).move(TaskStatus.WAITING_REPLY).model_copy(update={"due": req.answer_by})
            tasks[k] = t
        ws = ws.model_copy(update={"requests": {**ws.requests, request_id: req}, "levers": levers, "tasks": tasks})
        return self._commit(ws, exp, actor, role, "reask", request_id)

    # ── 6. 상신(게이트) ─────────────────────────────────────────────────────────
    def _needs(self, ws: Workspace, sc: Scenario) -> tuple[ApprovalNeed, ...]:
        needs = list(sc.approvals_required)
        for lid in sc.used_lever_ids:
            lv = ws.levers.get(lid)
            act = lever_action(lv) if lv else None
            if not act or any(n.action == act for n in needs):
                continue
            if act == "overtime_plan":
                used = sc.kpis.get("overtime_units", 0) > 0
            elif act == "outsourcing_contract":
                used = sc.kpis.get("outsourced_units", 0) > 0
            elif act == "customer_schedule_change":
                # 고객 양해는 양해를 쓰는 시나리오(B 등)에서만 효력이 있다. A 의 지연(예: 회수 과부하)은 양해 승인 대상이 아니다.
                spec = PRESETS.get(sc.scenario_id)
                cust = lv.params["target"].split(":", 1)[1]
                used = bool(spec and spec.use_consent) and any(ci.max_delay_days > 0 for ci in sc.customer_impact if ci.customer_id == cust)
            else:  # 단가 재협상 등: 레버가 시나리오에 들어가 있으면 승인 필요
                used = True
            if used:
                needs.append(ApprovalNeed(action=act, approver=ws.policy.rule_for(act).approver.value))
        return tuple(needs)

    def _gating(self, ws: Workspace, sc: Scenario) -> list[Assumption]:
        g = [ws.assumptions["AS-001"]]
        if sc.accepted_pct.get("PL-02", 0) < 0.999:
            g.append(ws.assumptions["AS-004"])  # 부분 수락이면 바이어의 부분 낙찰 허용 확인이 필요
        if "OPT-02" in sc.capacity_actions:
            g.append(ws.assumptions["AS-005"])
        return g

    def submit(self, ws_id: str, scenario_id: str, actor: str, role: Role, accept_loss: bool = False) -> tuple[Workspace, SubmitResult]:
        """accept_loss=True: 순이익 음수 안을 상신한다는 명시적 결정(영업임원·CEO/이사회만)."""
        if accept_loss and role not in (Role.SALES_EXEC, Role.CEO_BOARD):
            raise PermissionError("손실 감수 상신은 영업임원 또는 CEO/이사회만")
        ws = self._load(ws_id)
        exp = ws.version
        out = self.solve(ws)
        sc = next((s for s in out.scenarios if s.scenario_id == scenario_id), None)
        if sc is None:
            raise WorkflowError(f"시나리오 {scenario_id} 없음")
        sc = sc.model_copy(update={"approvals_required": self._needs(ws, sc)})
        # 상신 단계의 차단 사유: 게이트 가정 미확정·실행 불가능. (레버·승인은 상신 '후'에 갖춰지므로 여기서 보지 않는다)
        blockers = [f"가정 {a.assumption_id} 미확정 — {a.statement}" for a in self._gating(ws, sc) if a.blocks_submission]
        if sc.infeasibility is not None:
            blockers.append("불가능(infeasible) 시나리오")
        elif sc.kpis.get("net_vs_baseline_krw", sc.financials.net_krw) < 0 and sc.accepted_pct.get("PL-02", 0) > 0 and not accept_loss:
            inc = sc.kpis.get("net_vs_baseline_krw", sc.financials.net_krw)
            blockers.append(f"기준 대비 증분 순이익 음수({inc / 1e8:.1f}억, 조치 {list(sc.capacity_actions)}) — 입찰 참여 비권고. "
                            "진행하려면 영업임원·CEO/이사회가 손실 감수를 명시")
        title = f"상신 차단: {scenario_id}"
        prev = next((k for k, t in ws.tasks.items() if t.title == title and t.status is TaskStatus.BLOCKED), None)
        if blockers:
            if prev:  # 같은 안의 차단 작업은 하나만 두고 사유를 갱신
                ws = ws.model_copy(update={"tasks": {**ws.tasks, prev: ws.tasks[prev].model_copy(update={"blocked_reason": "; ".join(blockers)})}})
            else:
                ws, tid = ws.next_id("TK")
                t = Task(task_id=tid, title=title, owner_role=role).move(TaskStatus.BLOCKED, "; ".join(blockers))
                ws = ws.model_copy(update={"tasks": {**ws.tasks, tid: t}})
            return self._commit(ws, exp, actor, role, f"submit_blocked:{scenario_id}", scenario_id, out.input_hash), SubmitResult(False, tuple(blockers), ())
        ids = []
        approvals, tasks = dict(ws.approvals), dict(ws.tasks)
        if prev:
            tasks[prev] = tasks[prev].move(TaskStatus.OPEN).move(TaskStatus.DONE)
        for n in sc.approvals_required:
            ws, aid = ws.next_id("AP")
            ap = ApprovalRequest.request(aid, n.action, scenario_id, policy=ws.policy)
            approvals[aid] = ap
            ws, tid = ws.next_id("TK")
            tasks[tid] = Task(task_id=tid, title=f"승인 요청: {n.action}", owner_role=ap.approver, linked_approval_id=aid)
            ids.append(aid)
        ws = ws.model_copy(update={"approvals": approvals, "tasks": tasks, "submitted_scenario": scenario_id,
                                   "submission_approval_ids": tuple(ids), "submission_input_hash": out.input_hash})
        return self._commit(ws, exp, actor, role, f"submit:{scenario_id}", scenario_id, out.input_hash), SubmitResult(True, (), tuple(ids))

    # ── 7. 승인·거절 ────────────────────────────────────────────────────────────
    def decide(self, ws_id: str, approval_id: str, by: Role, approve: bool, actor: str, note: str | None = None) -> Workspace:
        ws = self._load(ws_id)
        exp = ws.version
        ap = ws.approvals[approval_id].decide(by, approve, note)
        levers = dict(ws.levers)
        for lid, lv in ws.levers.items():
            if lever_action(lv) == ap.action and lv.status is LeverStatus.ACCEPTED:
                levers[lid] = lv.transition(LeverStatus.APPROVED if approve else LeverStatus.REJECTED)
        tasks = {k: (t.move(TaskStatus.DONE) if t.linked_approval_id == approval_id else t) for k, t in ws.tasks.items()}
        upd = {"approvals": {**ws.approvals, approval_id: ap}, "levers": levers, "tasks": tasks}
        if not approve:
            ws2, tid = ws.next_id("TK")
            ws = ws2
            tasks[tid] = Task(task_id=tid, title=f"거절됨: {ap.action} — 대안 재검토·재상신", owner_role=Role.SALES)
            upd["tasks"] = tasks
            upd["submitted_scenario"] = None
            upd["submission_approval_ids"] = ()
            upd["submission_input_hash"] = None
            if ap.action == "capex_investment" and ws.submitted_scenario:
                sc = next(s for s in self.solve(ws).scenarios if s.scenario_id == ws.submitted_scenario)
                upd["excluded_options"] = tuple(sorted({*ws.excluded_options, *(a for a in sc.capacity_actions if a.startswith("OPT-"))}))
        ws = ws.model_copy(update=upd)
        return self._commit(ws, exp, actor, by, f"{'approve' if approve else 'reject'}:{ap.action}", approval_id)

    # ── 8. 확정 계획 승격 ────────────────────────────────────────────────────────
    def promote_plan(self, ws_id: str, actor: str, role: Role, tol_pp: float = 1.0) -> Workspace:
        ws = self._load(ws_id)
        exp = ws.version
        sid = ws.submitted_scenario
        if not sid:
            raise WorkflowError("상신된 시나리오가 없다")
        cand_out = self.solve(ws)
        if cand_out.input_hash != ws.submission_input_hash:
            raise WorkflowError("상신 후 조건·회신이 바뀌었다 — 재상신 필요(승인은 상신 당시 입력에만 유효)")
        cand = next(s for s in cand_out.scenarios if s.scenario_id == sid)
        final_out = self.solve(ws, candidate=False)  # 승인된 레버만
        final = next(s for s in final_out.scenarios if s.scenario_id == sid)
        if abs(final.accepted_pct["PL-02"] - cand.accepted_pct["PL-02"]) * 100 > tol_pp or (final.infeasibility is None) != (cand.infeasibility is None):
            raise WorkflowError(f"승인된 레버만으로는 상신안과 결과가 다르다({cand.accepted_pct['PL-02']:.1%} → {final.accepted_pct['PL-02']:.1%}) — 재상신 필요")
        final = final.model_copy(update={"approvals_required": self._needs(ws, final)})
        try:
            this_submission = [ws.approvals[i] for i in ws.submission_approval_ids]
            plan = promote(final, final_out.input_hash, this_submission, ws.levers, self._gating(ws, final), ws.plan)
        except PromotionBlocked as e:
            raise WorkflowError("확정 불가: " + "; ".join(e.reasons)) from e
        levers = {k: (lv.transition(LeverStatus.APPLIED) if k in final.used_lever_ids and lv.status is LeverStatus.APPROVED else lv)
                  for k, lv in ws.levers.items()}
        ws = ws.model_copy(update={"plan": plan, "plan_stale": False, "levers": levers})
        return self._commit(ws, exp, actor, role, f"promote:v{plan.version}", sid, final_out.input_hash)

    # ── 확정 파라미터·권한표 변경(사람만, 레지스트리/정책이 역할을 검증) ───────────────────────
    def set_parameter(self, ws_id: str, key: str, value: float, actor: str, role: Role, reason: str) -> tuple[Workspace, SolverOutput]:
        ws = self._load(ws_id)
        exp = ws.version
        reg = ws.registry.set(key, value, actor=actor, actor_role=role, actor_kind="human", reason=reason, at=datetime.fromisoformat(ws.today))
        ws, out = self._resolve(ws.model_copy(update={"registry": reg}))
        return self._commit(ws, exp, actor, role, f"set_parameter:{key}={value}", key, out.input_hash), out

    def set_policy_rule(self, ws_id: str, action: str, approver: Role, actor: str, role: Role, reason: str) -> Workspace:
        ws = self._load(ws_id)
        exp = ws.version
        pol = ws.policy.upsert_rule(action, approver, actor=actor, actor_role=role, actor_kind="human", reason=reason, at=datetime.fromisoformat(ws.today))
        return self._commit(ws.model_copy(update={"policy": pol}), exp, actor, role, f"set_policy:{action}->{approver.value}", action)

    # ── 9. 조건 변경(환율·단가·회수 의결) → 재검증 ──────────────────────────────────────
    def change_conditions(self, ws_id: str, actor: str, role: Role, **changes) -> tuple[Workspace, SolverOutput]:
        allowed = {"fx_label", "price_pressure_pct", "recall_replacement_week"}
        if set(changes) - allowed:
            raise WorkflowError(f"변경 불가 항목 {set(changes) - allowed}")
        ws = self._load(ws_id)
        exp = ws.version
        ws = ws.model_copy(update=changes)
        ws, out = self._resolve(ws)
        if ws.plan_stale:
            ws, tid = ws.next_id("TK")
            ws = ws.model_copy(update={"tasks": {**ws.tasks, tid: Task(task_id=tid, title=f"조건 변경 {changes} — 확정 계획 재검토", owner_role=Role.SALES)}})
        return self._commit(ws, exp, actor, role, f"change_conditions:{changes}", ws_id, out.input_hash), out
