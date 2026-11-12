"""W05 입찰 건 원장과 업무 흐름(지시서 6·10장).

- 상태 전이는 이 모듈의 유스케이스에서만 일어난다(표에 없는 전이는 거부).
- 가정은 수정하지 않는다: 값이 바뀌면 새 행을 만들고 이전 행을 SUPERSEDED 로 둔 뒤 10.3 전파를 실행한다.
- 결정(승인·거절)은 권한표의 권한자만(G1). 핵심 가정 미확인 상신 거부(G5), BLOCK 위험 표시 상신 거부(G15).
- 원장은 Workspace.bidflow 에 직렬화돼 같은 저장 단위·낙관적 락·감사 기록을 쓴다.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any

from pydantic import BaseModel, ConfigDict

from dealcockpit.domain.common import Role

from . import engine as E

S, SX, PR, Q, PC, F, CEO, DEMO = (Role.SALES, Role.SALES_EXEC, Role.PRODUCTION, Role.QUALITY, Role.PROCUREMENT, Role.FINANCE, Role.CEO_BOARD,
                                 Role.DEMO_ADMIN)
ALL_ROLES = [r.value for r in Role]
DEADLINE = date(2026, 7, 22)  # 가정 A-01
# 입찰 품목·제품명은 데이터(RFP 시드·제품 마스터)에서 읽는다(W05Flow.rfp_qty / pname).
ROLE_OF = {"구매": Role.PROCUREMENT, "품질": Role.QUALITY, "생산": Role.PRODUCTION, "영업": Role.SALES, "재무": Role.FINANCE}
DECISION_ACTION = {"BID_PARTICIPATION": "bid_participation", "CAPEX": "capex_investment"}  # 승인 권한표(플랫폼 레지스트리) 행위명


class FlowError(ValueError):
    """업무 규칙 위반(409)."""


class FlowPermission(PermissionError):
    """권한 위반(403)."""


class M(BaseModel):
    model_config = ConfigDict(extra="forbid")


class A(M):  # 가정
    id: int
    key: str
    label: str
    value: Any = None
    status: str = "UNCONFIRMED"  # UNCONFIRMED CONFIRMED REFUTED SUPERSEDED
    source_type: str = "ASSUMED"
    rationale: str = ""
    is_critical: bool = False
    owner_role: str
    supersedes_id: int | None = None
    confirmed_by: str | None = None
    code: str = ""  # A-01 …


class IR(M):  # 확인요청
    id: int
    assumption_keys: list[str]
    to_role: str | None = None
    to_external: str | None = None
    question: str
    drafted_by_agent: bool = False
    status: str = "DRAFT"  # DRAFT SENT ANSWERED CANCELLED
    due_on: str
    answer_json: dict | None = None
    answer_text: str | None = None
    answered_by: str | None = None
    is_mock: bool = False


class Msg(M):
    id: int
    party: str
    direction: str
    body: str
    info_request_id: int | None = None
    is_mock: bool = True
    on: str


class Run(M):
    id: int
    revision: int
    engine_version: str
    inputs: dict
    metrics: list[dict]


class Opt(M):
    id: int
    calc_run_id: int
    label: str
    title: str
    offered: dict[str, int]
    capex_ids: list[str]
    supply_start_on: str | None
    feasibility: str
    blocking_reasons: list[str] = []
    narrative: str | None = None
    status: str = "PROPOSED"  # PROPOSED SELECTED REJECTED STALE
    metric_ids: dict[str, int] = {}
    deps: list[str] = []  # 기대는 가정 key


class Flag(M):
    id: int
    calc_run_id: int
    option_id: int | None
    rule_key: str
    lens: str
    severity: str
    evidence_ids: list[int]
    visible_roles: list[str]
    text: str
    explained_by_agent: bool = False
    acknowledged_by: str | None = None


class Dep(M):
    dependent_type: str
    dependent_id: int
    assumption_id: int


class Appr(M):
    id: int
    option_id: int
    decision_type: str
    required_role: str
    policy_version: int
    status: str = "PENDING"  # PENDING APPROVED CONDITIONAL REJECTED VOIDED
    conditions_text: str | None = None
    reject_reason: str | None = None
    decided_by: str | None = None
    decided_at: str | None = None


class Task(M):
    id: int
    approval_id: int | None
    type: str
    owner_role: str
    status: str = "OPEN"  # OPEN IN_PROGRESS DONE BLOCKED CANCELLED
    due_on: str
    payload: dict = {}
    option_id: int | None = None


class Resv(M):
    id: int
    option_id: int
    line_id: str
    week_start: str
    qty: float
    status: str = "TENTATIVE"  # TENTATIVE CONFIRMED RELEASED


class Ev(M):
    id: int
    ts: str
    actor_type: str  # USER AGENT SYSTEM EXTERNAL_MOCK
    actor_id: str
    event_type: str
    entity_type: str
    entity_id: int | None
    before: Any = None
    after: Any = None
    reason: str | None = None
    caused_by: int | None = None
    acting_as: str | None = None


class AgentRun(M):
    id: int
    trigger_event_id: int
    trigger: str
    mode: str
    model: str | None = None
    status: str
    tool_calls: list[dict] = []
    output: dict | None = None
    errors: list[str] = []


class BidLedger(M):
    customer_id: str = "C-201"
    title: str = "C-201 해외 신규 입찰(기초·더마)"
    received_on: str
    submission_deadline: str = DEADLINE.isoformat()
    status: str = "RECEIVED"
    revision: int = 1
    selected_option_id: int | None = None
    capex_rejected: bool = False
    hold_reason: str | None = None
    deadline_ticks: list[str] = []
    assumptions: list[A] = []
    requests: list[IR] = []
    messages: list[Msg] = []
    runs: list[Run] = []
    options: list[Opt] = []
    flags: list[Flag] = []
    deps: list[Dep] = []
    approvals: list[Appr] = []
    tasks: list[Task] = []
    reservations: list[Resv] = []
    events: list[Ev] = []
    agent_runs: list[AgentRun] = []
    notes: list[dict] = []  # 재무 검토 의견 등
    change_banner: dict | None = None
    notices: list[dict] = []  # 외부 추가 통지(모의): 영업이 '가정 교체'로 반영
    counters: dict[str, int] = {}

    def graph_state(self) -> dict:
        """전파 그래프 '변경 전/후' 비교용 상태 사진."""
        return {"assumptions": {a.id: a.status for a in self.assumptions}, "runs": [r.id for r in self.runs],
                "options": {o.id: o.status for o in self.options}, "approvals": {a.id: a.status for a in self.approvals},
                "tasks": {t.id: t.status for t in self.tasks}, "reservations": {r.option_id: r.status for r in self.reservations}}

    def nid(self, kind: str) -> int:
        self.counters[kind] = self.counters.get(kind, 0) + 1
        return self.counters[kind]

    # 조회 도우미
    def cur(self, key: str) -> A | None:
        rows = [a for a in self.assumptions if a.key == key and a.status != "SUPERSEDED"]
        return rows[-1] if rows else None

    def opt(self, oid: int) -> Opt:
        return next(o for o in self.options if o.id == oid)


# ── 가정 등록부(지시서 15.1) ──────────────────────────────────────────────────────
REGISTER = [
    ("A-01", "submission_deadline", "입찰 제출 마감일", DEADLINE.isoformat(), "ASSUMED", False, S, "RFP는 '마감 3주'만 적음 → 기준일 + 21일"),
    ("A-02", "lead_time_days", "리드타임", {"median": 60, "min": 45, "max": 77}, "DERIVED", False, S, "주문별 납기 없음 → 출하 실적 중앙값"),
    ("A-03", "line_capacity_basis", "라인 능력 역산 방식", "2026 상반기 생산량 ÷ 26주 ÷ 가동률", "DERIVED", False, PR, "capacity_qty 단위 미정·실적 불일치"),
    ("A-04", "supply_start_date", "납품 개시일", None, "ASSUMED", True, S, "RFP에 납품 개시일 없음 → 바이어 확인"),
    ("A-05", "demand_profile", "주별 수요 형태", "연 물량 52주 균등", "ASSUMED", False, S, "초도·분할 조건 없음"),
    ("A-06", "capex_decision_date", "설비 착수일", "2026-07-06", "ASSUMED", False, CEO, "이사회 = 기준일 + 5일"),
    ("A-07", "penalty_base", "페널티 기준 금액", "월별 미충족 물량 금액의 3%", "ASSUMED", False, S, "RFP는 '월 3%'만 적음"),
    ("A-08", "cost_scope", "원가 범위", "재료비만", "ASSUMED", False, F, "가공비·인건비·물류비 자료 없음"),
    ("A-09", "c101_demand_scenario", "C-101 수요", "유지(감소분 미반영)", "ASSUMED", False, SX, "감소 원인·지속 여부 미상"),
    ("A-10", "unknown_status_orders", "상태 불명 주문", "598건 부하 제외·위험 물량 표시", "DERIVED", False, S, "최대 리드타임 넘긴 미출하의 상태 컬럼 없음"),
    ("A-11", "offer_rounding", "제안 물량 반올림", "최대 제안 가능량을 만 EA 단위로 내림", "ASSUMED", False, S, "운영 규칙"),
    ("A-12", "partial_award_allowed", "부분 물량 제안 허용", None, "ASSUMED", True, S, "RFP에 없음 → 바이어 확인"),
    ("A-14", "formula_revalidation", "처방 재검증 기간", "OPT-02 리드타임 7주 안에 끝남", "ASSUMED", False, Q, "소요 기간 자료 없음"),
    ("A-15", "masil_derma_supply", "계열(Masil) 더마 공급", "범위 밖", "ASSUMED", False, S, "고객 목록에 없어 식별 불가"),
    ("A-16", "pl01_capex_option", "기초 라인 설비안", "없음(만들지 않음)", "ASSUMED", False, PR, "원본에 L-01 설비안 없음"),
]


class W05Flow:
    """입찰 건 유스케이스. 저장은 호출자(API)가 Workspace.bidflow 로 한다. 모든 메서드는 원장을 제자리에서 바꾼다."""

    def __init__(self, inputs: dict, policy):
        self.inp, self.policy = inputs, policy
        self.topo = E.topology(inputs)
        self.rfp_qty = {x["product_id"]: int(x["annual_qty"]) for x in inputs["seeds"]["rfp_lines"]}
        self.pname = inputs.get("products", {})
        self.line_of = {p: E.primary_line(inputs, p) for p in self.rfp_qty}

    # ── 공통 ─────────────────────────────────────────────────────────────────
    @staticmethod
    def _ev(L: BidLedger, today: str, actor_type: str, actor: str, et: str, ent: str, eid: int | None, before=None, after=None, reason=None,
            caused_by=None, acting_as=None) -> Ev:
        n = L.nid("event")
        ev = Ev(id=n, ts=f"{today}T09:00:{n % 60:02d}", actor_type=actor_type, actor_id=actor, event_type=et, entity_type=ent, entity_id=eid,
                before=before, after=after, reason=reason, caused_by=caused_by, acting_as=acting_as)
        L.events.append(ev)
        return ev

    def _role_of(self, decision_type: str) -> str:
        return self.policy.rule_for(DECISION_ACTION[decision_type]).approver.value

    @staticmethod
    def _need(role: Role, allowed: tuple[Role, ...], what: str) -> None:
        if role not in allowed:
            raise FlowPermission(f"{what}: {' / '.join(r.value for r in allowed)} 권한입니다")

    def _add_assumption(self, L: BidLedger, code, key, label, value, src, crit, owner: Role, why) -> A:
        """초기 가정은 모두 '미확인'으로 시작한다(원본에 없는 값). 핵심(is_critical)만 상신을 막는다."""
        a = A(id=L.nid("assumption"), code=code, key=key, label=label, value=value, source_type=src, is_critical=crit, owner_role=owner.value, rationale=why)
        L.assumptions.append(a)
        return a

    def _critical_open(self, L: BidLedger, keys: list[str] | None = None) -> list[A]:
        out = []
        for a in L.assumptions:
            if a.status == "UNCONFIRMED" and a.is_critical and (keys is None or a.key in keys):
                out.append(a)
        return out

    # ── 1. 접수 ──────────────────────────────────────────────────────────────
    def receive_rfp(self, today: str, actor: str, role: Role) -> BidLedger:
        self._need(role, (S, SX), "RFP 접수")
        L = BidLedger(received_on=today, submission_deadline=E.submission_deadline(self.inp).isoformat())
        ev = self._ev(L, today, "USER", actor, "BID_RECEIVED", "bid", None, after={"status": "RECEIVED"})
        for code, key, label, value, src, crit, owner, why in REGISTER:
            self._add_assumption(L, code, key, label, L.submission_deadline if key == "submission_deadline" else value, src, crit, owner, why)
        self._recalc(L, caused_by=ev.id)
        # 공백 탐지(규칙 기반 — AI 없이도 같은 결과, G11). AI 실행 시 문구만 보강된다.
        due = (date.fromisoformat(today) + timedelta(days=5)).isoformat()
        L.requests.append(IR(id=L.nid("request"), assumption_keys=["supply_start_date", "partial_award_allowed"], to_external="C-201_BUYER",
                             question="C-201 바이어 질의: ① 납품 개시일 ② 요구 물량 일부만 제안해도 되는지 ③ 분할 납품 가능 여부 ④ 월 3% 페널티의 기준 금액",
                             due_on=due))
        # 영업 자신이 확인할 일은 '보낼 질의'가 아니라 바로 내 할 일(발송 단계 없음)
        L.requests.append(IR(id=L.nid("request"), assumption_keys=["unknown_status_orders"], to_role=S.value, status="SENT",
                             question="최대 리드타임(77일)을 넘긴 미출하 주문 598건이 취소인지 미납인지 영업지원 기록으로 확인", due_on=due))
        L.requests.append(IR(id=L.nid("request"), assumption_keys=["line_capacity_basis"], to_role=PR.value,
                             question="라인 능력을 '2026 상반기 생산량 ÷ 26주 ÷ 가동률'로 역산했습니다. 생산관리 기준과 맞는지 확인 부탁드립니다", due_on=due))
        self._ev(L, today, "SYSTEM", "system", "GAPS_DETECTED", "bid", None, after={"requests": [r.id for r in L.requests]}, caused_by=ev.id)
        self._settle_status(L, today, ev.id)
        return L

    # ── 2. 확인요청 발송·회신·가정 확인 ────────────────────────────────────────────
    def send_request(self, L: BidLedger, rid: int, today: str, actor: str, role: Role) -> None:
        self._need(role, (S, SX), "확인요청 발송")
        r = next(x for x in L.requests if x.id == rid)
        if r.status != "DRAFT":
            raise FlowError("초안 상태의 확인요청만 발송할 수 있습니다")
        r.status = "SENT"
        if r.to_external:
            L.messages.append(Msg(id=L.nid("msg"), party=r.to_external, direction="OUT", body=r.question, info_request_id=r.id, on=today))
        self._ev(L, today, "USER", actor, "INFO_REQUEST_SENT", "info_request", rid, after={"status": "SENT"})

    def edit_request(self, L: BidLedger, rid: int, question: str, today: str, actor: str, role: Role) -> None:
        self._need(role, (S, SX), "확인요청 수정")
        r = next(x for x in L.requests if x.id == rid)
        if r.status != "DRAFT":
            raise FlowError("초안만 수정할 수 있습니다")
        before = r.question
        r.question = question
        self._ev(L, today, "USER", actor, "INFO_REQUEST_EDITED", "info_request", rid, before=before, after=question)

    def answer_request(self, L: BidLedger, rid: int, answer: dict, text: str, today: str, actor: str, role: Role) -> None:
        """외부(바이어·V-205) 회신은 시연 진행자가 모의 입력(MOCK_REPLY). 내부 회신은 수신 역할이 입력하고 곧바로 가정을 확인한다."""
        r = next(x for x in L.requests if x.id == rid)
        if r.status != "SENT":
            raise FlowError("발송된 확인요청에만 회신할 수 있습니다")
        if r.to_external:
            self._need(role, (DEMO,), "외부 모의 회신 입력")
            r.is_mock = True
            L.messages.append(Msg(id=L.nid("msg"), party=r.to_external, direction="IN", body=text, info_request_id=rid, on=today))
        elif role.value != r.to_role:
            raise FlowPermission(f"회신은 수신 역할({r.to_role})만 입력합니다")
        r.status, r.answer_json, r.answer_text, r.answered_by = "ANSWERED", answer, text, actor
        ev = self._ev(L, today, "EXTERNAL_MOCK" if r.to_external else "USER", actor, "INFO_REQUEST_ANSWERED", "info_request", rid, after=answer)
        if not r.to_external:
            self._apply_answer(L, r, today, actor, role, ev.id, mock=False)

    def confirm_from_request(self, L: BidLedger, rid: int, today: str, actor: str, role: Role) -> None:
        """'이 회신으로 가정 확인' — 외부 회신은 영업이 사람의 클릭으로만 확정한다(G12)."""
        r = next(x for x in L.requests if x.id == rid)
        if r.status != "ANSWERED":
            raise FlowError("회신 받은 확인요청만 반영할 수 있습니다")
        self._need(role, (S, SX), "회신으로 가정 확인")
        ev = self._ev(L, today, "USER", actor, "ANSWER_ACCEPTED", "info_request", rid)
        self._apply_answer(L, r, today, actor, role, ev.id, mock=r.is_mock)

    def _apply_answer(self, L: BidLedger, r: IR, today, actor, role, cause, mock: bool) -> None:
        ans = r.answer_json or {}
        for key in r.assumption_keys:
            if key not in ans:
                continue
            val = ans[key]
            if key == "mb_recontract":
                ok = bool(val.get("possible")) if isinstance(val, dict) else bool(val)
                self._set_assumption(L, key, val, "CONFIRMED" if ok else "REFUTED", "MOCK_REPLY" if mock else "ASSUMED", today, actor, role, cause)
            else:
                self._set_assumption(L, key, val, "CONFIRMED", "MOCK_REPLY" if mock else "ASSUMED", today, actor, role, cause)

    def supersede(self, L: BidLedger, key: str, value: Any, reason: str, today: str, actor: str, role: Role, mock: bool = True) -> None:
        """가정 값을 새 값으로 교체(바이어 추가 통지 등) → 전파."""
        self._need(role, (S, SX), "가정 교체")
        if L.status in ("SUBMITTED", "ON_HOLD"):
            raise FlowError(f"'{L.status}' 상태에서는 가정을 바꿀 수 없습니다(새 검토 회차에서 진행)")
        ev = self._ev(L, today, "USER", actor, "ASSUMPTION_CHANGE_REQUESTED", "assumption", None, after={key: value}, reason=reason)
        self._set_assumption(L, key, value, "CONFIRMED", "MOCK_REPLY" if mock else "ASSUMED", today, actor, role, ev.id)

    def _set_assumption(self, L: BidLedger, key, value, status, src, today, actor, role, cause) -> None:
        old = L.cur(key)
        if old is None:
            raise FlowError(f"가정 {key} 없음")
        if old.status == "UNCONFIRMED":  # 처음 확인: 같은 행을 확정(값 채움)하는 대신 새 행으로 남겨 이력 보존
            new = old.model_copy(update={"id": L.nid("assumption"), "value": value, "status": status, "source_type": src, "supersedes_id": old.id,
                                         "confirmed_by": actor})
            old.status = "SUPERSEDED"
            L.assumptions.append(new)
            self._move_deps(L, old.id, new.id)
            ev = self._ev(L, today, "USER", actor, f"ASSUMPTION_{status}", "assumption", new.id, before=old.value, after=value, caused_by=cause)
            self._refresh(L, today, ev.id)  # 처음 확인·반박: 가능 여부 재평가(반박이면 해당 대안은 '불가')
            return
        if old.value == value and old.status == status:
            return
        new = old.model_copy(update={"id": L.nid("assumption"), "value": value, "status": status, "source_type": src, "supersedes_id": old.id,
                                     "confirmed_by": actor})
        old.status = "SUPERSEDED"
        L.assumptions.append(new)
        ev = self._ev(L, today, "USER", actor, "ASSUMPTION_SUPERSEDED", "assumption", new.id, before={"id": old.id, "value": old.value},
                      after={"id": new.id, "value": value}, caused_by=cause)
        self._propagate(L, new, today, ev.id, old=old)

    @staticmethod
    def _move_deps(L: BidLedger, old_id: int, new_id: int) -> None:
        for d in L.deps:
            if d.assumption_id == old_id:
                d.assumption_id = new_id

    # ── 3. 계산·대안 구성 ──────────────────────────────────────────────────────
    def _ctx(self, L: BidLedger):
        today = date.fromisoformat(self.inp["as_of"])
        calc = E.Calc({})
        caps = E.line_capacity(self.inp, calc)
        comm = E.commitments(self.inp, today)
        return calc, caps, comm

    def _supply_start(self, L: BidLedger) -> date | None:
        a = L.cur("supply_start_date")
        return date.fromisoformat(a.value) if a and a.value and a.status == "CONFIRMED" else None

    def _recalc(self, L: BidLedger, caused_by: int | None = None, compose: bool = False) -> Run:
        calc, caps, comm = self._ctx(L)
        files = self.inp["files"]
        seeds = self.inp["seeds"]
        # 원본 상수(AI는 이것도 {{calc:id}}로만 인용)
        calc.put("rfp_late_penalty_pct", 3, "pct", "original_constant", "ORIGINAL", {"term": "late_penalty"},
                 sources=[{"file": "01_corevia/docs/rfp_c201.md", "rows": [7], "kind": "doc_line"}])
        for o, row in E.capex_options(self.inp).items():
            calc.put("capex_krw", row["capex_krw"], "KRW", "original_constant", "ORIGINAL", {"option": o}, sources=[{"file": files["capex"], "rows": [row["_row"]], "kind": "single"}])
            calc.put("capex_lead_weeks", row["lead_time_weeks"], "weeks", "original_constant", "ORIGINAL", {"option": o}, sources=[{"file": files["capex"], "rows": [row["_row"]], "kind": "single"}])
        for ln in seeds["rfp_lines"]:
            calc.put("rfp_annual_qty", ln["annual_qty"], "EA", "original_constant", "ORIGINAL", {"product_id": ln["product_id"]},
                     sources=[{"file": ln["source_ref"].rsplit(":", 1)[0], "rows": [int(ln["source_ref"].rsplit(":", 1)[1])], "kind": "doc_line"}]
                     if ln["source_ref"].rsplit(":", 1)[-1].isdigit() else [])
        calc.put("group_cash_krw", 30_000_000_000, "KRW", "original_constant", "ORIGINAL", {}, sources=[{"file": "03_aurora_group/funding/grp_available_cash.md", "rows": [3], "kind": "doc_line"}])
        fxr = self.inp["fx_close"]["row"]
        calc.put("fx_close", float(fxr["usdkrw_close"]), "KRW/USD", "original_constant", "ORIGINAL", {"date": "2026-06-30"}, sources=[{"file": files["fx"], "rows": [fxr["_row"]], "kind": "single"}])
        prot = [c for c in comm if c["status"] == "PROTECTED"]
        unk = [c for c in comm if c["status"] != "PROTECTED"]
        calc.put("protected_orders", len(prot), "건", "commitments", "DERIVED", {}, inputs=[{"assumption_key": "lead_time_days"}],
                 sources=[{"file": files["orders"], "kind": "aggregate", "filter": {"unshipped": True, "order_date+77 >= as_of": True}, "agg": "count", "row_count": len(prot),
                           "sample_rows": [c["_row"] for c in prot[:5]]}])
        calc.put("unknown_orders", len(unk), "건", "commitments", "DERIVED", {}, inputs=[{"assumption_key": "unknown_status_orders"}],
                 sources=[{"file": files["orders"], "kind": "aggregate", "filter": {"unshipped": True, "order_date+77 < as_of": True}, "agg": "count", "row_count": len(unk),
                           "sample_rows": [c["_row"] for c in unk[:5]]}])
        calc.put("unknown_qty", sum(c["qty"] for c in unk), "EA", "commitments", "DERIVED", {}, inputs=[{"assumption_key": "unknown_status_orders"}])
        led0 = E.ledger(self.inp, caps, comm, (), E.decision_date(self.inp))
        for pid, ln in self.line_of.items():
            if ln not in led0["lines"]:
                continue
            ow = [r for r in led0["lines"][ln] if r["overload"] > 0]
            calc.put("overload_weeks", len(ow), "주", "overload", "DERIVED", {"line_id": ln}, inputs=[{"calc_result_id": caps[ln]["ids"]["full"]}],
                     sources=[{"file": files["orders"], "kind": "aggregate", "filter": {"product_id": pid, "status": "PROTECTED"}, "agg": "sum(qty) by expected_ship_week",
                               "row_count": len([c for c in prot if c["product_id"] == pid]), "sample_rows": [c["_row"] for c in prot if c["product_id"] == pid][:5]}])
        for y in self.inp["c101_yoy"]:
            calc.put("c101_booking_yoy_pct", float(y["booking_yoy_pct"]), "pct", "original_constant", "ORIGINAL", {"month": y["month"]},
                     sources=[{"file": files["customer_summary"], "rows": [y["_row"]], "kind": "single"}])
        for pid in self.rfp_qty:
            d = self.inp.get("defects_by_product", {}).get(pid)
            if d:
                calc.put("confirmed_defects", d["count"], "건", "count_confirmed_claims", "DERIVED", {"product_id": pid},
                         sources=[{"file": files["claims"], "kind": "aggregate", "filter": {"product_id": pid, "defect_confirmed": "True"}, "agg": "count", "row_count": d["count"], "sample_rows": d["sample_rows"]}])
        run = Run(id=L.nid("run"), revision=L.revision, engine_version=E.ENGINE_VERSION,
                  inputs={"as_of": self.inp["as_of"], "decision_date": "2026-07-06", "demand_factor": 1.0, "supply_start": (self._supply_start(L) or "").__str__(),
                          "capex_rejected": L.capex_rejected}, metrics=[m.to_dict() for m in calc.metrics])
        L.runs.append(run)
        self._calc_cache = (calc, caps, comm)
        if compose:
            self._compose(L, run, calc, caps, comm, caused_by)
        return run

    def _evaluate(self, L: BidLedger, calc, caps, comm, offered: dict[str, int], capex: tuple[str, ...], start: date, label: str) -> dict:
        led = E.ledger(self.inp, caps, comm, capex, E.decision_date(self.inp))
        res, ids, unmet = {}, {}, {}
        for p, q in offered.items():
            ev = E.evaluate_product(led, p, q, start)
            line = self.line_of[p]
            m = calc.put("fill_rate_52w", round(100 * ev["fill_rate"], 1), "pct", "fill_rate_52w", "DERIVED", {"option": label, "product_id": p},
                         inputs=[{"calc_result_id": caps[line]["ids"]["full"]}, {"calc_result_id": caps[line]["ids"]["run"]}, {"assumption_key": "supply_start_date"},
                                 {"assumption_key": "demand_profile"}] + [{"calc_result_id": calc.get("capex_lead_weeks", option=o).id} for o in capex])
            ids[f"fill_{p}"] = m.id
            res[p] = ev
            unmet[p] = ev["unmet"]
            if ev["first_full_week"]:
                ids[f"first_full_{p}"] = calc.put("first_full_week", ev["first_full_week"], "date", "first_full_week", "DERIVED", {"option": label, "product_id": p}, inputs=[{"calc_result_id": m.id}]).id
        fx = calc.get("fx_close").value
        fin = E.financials(self.inp, offered, fx, 0, 0, unmet)
        ids["revenue"] = calc.put("revenue_krw", round(fin["revenue_krw"]), "KRW", "revenue", "DERIVED", {"option": label}, inputs=[{"calc_result_id": calc.get("fx_close").id}]).id
        ids["margin"] = calc.put("material_margin_krw", round(fin["margin_krw"]), "KRW", "material_margin", "DERIVED", {"option": label, "cell": "0/0"},
                                 inputs=[{"calc_result_id": ids["revenue"]}, {"assumption_key": "cost_scope"}],
                                 sources=[{"file": self.inp["files"]["consumption"], "kind": "aggregate", "filter": {"consumption_date": ["2026-04-01", "2026-06-30"]},
                                           "agg": "sum(consumed_qty*applied_unit_cost_krw)/sum(produced_qty)", "row_count": sum(v["consumption_rows"] for v in self.inp["material_cost_q2"].values()),
                                           "sample_rows": next(iter(self.inp["material_cost_q2"].values()))["sample_rows"]}]).id
        grid = {}
        for cut in (0, 5, 10):
            for drop in (0, 5, 10):
                g = E.financials(self.inp, offered, fx, cut / 100, drop / 100)
                grid[f"{cut}/{drop}"] = calc.put("material_margin_krw", round(g["margin_krw"]), "KRW", "material_margin", "DERIVED", {"option": label, "cell": f"{cut}/{drop}"},
                                                 inputs=[{"calc_result_id": ids["revenue"]}]).id
        ids["grid"] = grid
        ids["penalty"] = calc.put("penalty_52w_krw", round(fin["penalty_52w_krw"]), "KRW", "penalty_52w", "DERIVED", {"option": label},
                                  inputs=[{"calc_result_id": calc.get("rfp_late_penalty_pct").id}, {"assumption_key": "penalty_base"}]).id
        if capex:
            base = E.ledger(self.inp, caps, comm, (), E.decision_date(self.inp))
            opts = E.capex_options(self.inp)
            # 설비로 늘어난 물량 = 설비안 대상 제품마다 (설비 있음 − 없음) 충족 물량, 이익은 제품별 EA당 재료비 차감 이익
            gain_krw = sum((res[q]["fill_rate"] - E.evaluate_product(base, q, offered[q], start)["fill_rate"]) * offered[q] * self._margin_per_ea(q, fx)
                           for q in {opts[o]["product_id"] for o in capex} if q in offered)
            per_ea = None
            cap_sum = sum(opts[o]["capex_krw"] for o in capex)
            pb = cap_sum / gain_krw if gain_krw > 0 else None
            ids["payback"] = calc.put("capex_payback_years", round(pb, 2) if pb else None, "years", "capex_payback", "DERIVED", {"option": label},
                                      inputs=[{"calc_result_id": calc.get("capex_krw", option=o).id} for o in capex] + [{"assumption_key": "cost_scope"}]).id
            ids["capex_sum"] = calc.put("capex_sum_krw", cap_sum, "KRW", "sum", "DERIVED", {"option": label}, inputs=[{"calc_result_id": calc.get("capex_krw", option=o).id} for o in capex]).id
            del per_ea
        return {"res": res, "ids": ids, "fin": fin, "led": led}

    def _margin_per_ea(self, p: str, fx: float) -> float:
        ln = next(x for x in self.inp["seeds"]["rfp_lines"] if x["product_id"] == p)
        return ln["unit_price_usd"] * fx - self.inp["material_cost_q2"][p]["krw_per_ea"]

    def _compose(self, L: BidLedger, run: Run, calc, caps, comm, caused_by) -> None:
        """대안 구성(결정론 규칙). 2~3개: ① 전량 ② 최대 제안(부분) ③ 더마만 또는 기초만. 원본에 없는 설비안·공급사는 만들지 않는다."""
        start = self._supply_start(L)
        if start is None:
            return
        for o in L.options:
            if o.status == "PROPOSED":
                o.status = "STALE"
        opts = E.capex_options(self.inp)
        allowed = [] if L.capex_rejected else sorted(opts)
        sets = sorted({tuple(sorted(c)) for n in range(len(allowed) + 1) for c in __import__("itertools").combinations(allowed, n)}, key=lambda t: (len(t), t))
        cost = lambda st: sum(opts[o]["capex_krw"] for o in st)  # noqa: E731
        led_of = {st: E.ledger(self.inp, caps, comm, st, E.decision_date(self.inp)) for st in sets}
        prods = list(self.rfp_qty)
        ev = {st: {p: E.evaluate_product(led_of[st], p, self.rfp_qty[p], start) for p in prods} for st in sets}
        capex_prods = [p for p in prods if any(v["product_id"] == p for v in opts.values())]  # 설비안이 겨냥하는 제품(거절 여부와 무관)
        other = [p for p in prods if p not in capex_prods]
        filled = lambda st: sum(ev[st][p]["fill_rate"] * self.rfp_qty[p] for p in prods)  # noqa: E731
        best = max(filled(st) for st in sets)
        full_set = min((st for st in sets if filled(st) >= best - 1e-6), key=lambda st: (cost(st), len(st)))
        target = {p: min(self.rfp_qty[p], E.round_down_10k(max(ev[st][p]["max_offerable"] for st in sets))) for p in prods}
        part_set = min((st for st in sets if all(E.round_down_10k(ev[st][p]["max_offerable"]) >= target[p] for p in prods)), key=lambda st: (cost(st), len(st)))
        nm = lambda ps: ", ".join(self.pname.get(p, p) for p in ps)  # noqa: E731
        cands = [("A", "전량 제안", dict(self.rfp_qty), full_set), ("B", "최대 제안", dict(target), part_set)]
        if capex_prods and all(ev[full_set][p]["fill_rate"] >= 1 - 1e-9 for p in capex_prods):
            cands.append(("C", f"{nm(capex_prods)} 전량만", {p: self.rfp_qty[p] for p in capex_prods}, full_set))
        elif other:
            cands.append(("C", f"{nm(other)}만(설비안 없음)", {p: target[p] for p in other}, ()))
        if L.capex_rejected:  # 설비투자 거절 뒤 재상신안: 설비안 없는 최대 제안 + 설비 무관 제품만
            no = {p: E.round_down_10k(ev[()][p]["max_offerable"]) for p in prods}
            cands = [("D", "설비안 없는 최대 제안", {p: q for p, q in no.items() if q > 0}, ())] + ([("E", f"{nm(other)}만", {p: no[p] for p in other}, ())] if other else [])
        used = sorted({o for c in cands for o in c[3]})
        for o in used:
            for req in opts[o]["requires"]:
                if req.get("blocking", True) and L.cur(req["key"]) is None:
                    owner = ROLE_OF.get(req["owner"], PC)
                    a = self._add_assumption(L, "A-13", req["key"], req["label"], None, "ASSUMED", True, owner, f"{o} 선행 요건, 원본에 가능 여부와 조건 없음")
                    who = f"공급사 {req.get('supplier')}와 " if req.get("supplier") else ""
                    L.requests.append(IR(id=L.nid("request"), assumption_keys=[req["key"]], to_role=owner.value,
                                         question=f"{o} 선택 시 {req.get('material', '')} {who}재계약이 가능한지, 조건(최소 주문 수량 등)을 알려 주세요".replace("  ", " "),
                                         due_on=(date.fromisoformat(L.received_on) + timedelta(days=14)).isoformat()))
                    self._ev(L, L.received_on, "SYSTEM", "system", "ASSUMPTION_PROPOSED", "assumption", a.id, after={"key": req["key"]}, caused_by=caused_by)
        for label, title, offered, capex in cands:
            ev = self._evaluate(L, calc, caps, comm, offered, capex, start, label)
            deps = ["supply_start_date", "line_capacity_basis", "demand_profile"]
            if any(q < self.rfp_qty[p] for p, q in offered.items()) or len(offered) < len(self.rfp_qty):
                deps.append("partial_award_allowed")
            for oid in capex:
                deps += [r["key"] for r in opts[oid]["requires"] if r.get("blocking", True) and r["key"] not in deps]
            o = Opt(id=L.nid("option"), calc_run_id=run.id, label=label, title=title, offered={k: int(v) for k, v in offered.items()}, capex_ids=list(capex),
                    supply_start_on=start.isoformat(), feasibility="", metric_ids={k: v for k, v in ev["ids"].items() if k != "grid"} | {f"grid_{k}": v for k, v in ev["ids"]["grid"].items()},
                    deps=deps)
            o.feasibility, o.blocking_reasons = self._feasibility(L, o, ev["res"])
            L.options.append(o)
            for key in deps:
                a = L.cur(key)
                if a:
                    L.deps.append(Dep(dependent_type="bid_option", dependent_id=o.id, assumption_id=a.id))
            self._ev(L, L.received_on, "SYSTEM", "system", "OPTION_PROPOSED", "bid_option", o.id, after={"label": label, "feasibility": o.feasibility}, caused_by=caused_by)
        run.metrics = [m.to_dict() for m in calc.metrics]
        self._flags(L, run, calc, caps)

    def _feasibility(self, L: BidLedger, o: Opt, res: dict) -> tuple[str, list[str]]:
        reasons = []
        for p, r in res.items():
            if r["fill_rate"] < 1 - 1e-9:
                reasons.append(f"{self.pname.get(p, p)}({p}) 충족률 100% 미만")
        for key in o.deps:
            a = L.cur(key)
            if a is None:
                continue
            if a.status == "REFUTED" or (key == "partial_award_allowed" and a.status == "CONFIRMED" and a.value is False):
                reasons.append(f"가정 '{a.label}' 반박됨")
        if reasons:
            return "INFEASIBLE", reasons
        unconf = [L.cur(k) for k in o.deps if L.cur(k) and L.cur(k).status == "UNCONFIRMED" and L.cur(k).is_critical]
        if unconf:
            return "CONDITIONAL", [f"핵심 가정 '{a.label}' 미확인" for a in unconf]
        return "FEASIBLE", []

    def _refresh(self, L: BidLedger, today: str, cause: int) -> None:
        """가정이 처음 확인되면: 대안 가능 여부·위험 표시 재평가, 핵심 가정이 다 풀리면 대안 구성."""
        if L.status in ("WAITING_INFO", "RECEIVED", "REVIEW_REQUIRED") and not self._critical_open(L, ["supply_start_date", "partial_award_allowed"]) \
                and not any(o.status == "PROPOSED" for o in L.options):
            self._recalc(L, cause, compose=True)
        else:
            calc, caps, comm = self._ctx(L)
            run = L.runs[-1]
            for o in L.options:
                if o.status in ("PROPOSED", "SELECTED"):
                    start = date.fromisoformat(o.supply_start_on)
                    led = E.ledger(self.inp, caps, comm, tuple(o.capex_ids), E.decision_date(self.inp))
                    res = {p: E.evaluate_product(led, p, q, start) for p, q in o.offered.items()}
                    o.feasibility, o.blocking_reasons = self._feasibility(L, o, res)
            self._flags_from_run(L, run)
        self._settle_status(L, today, cause)

    def _settle_status(self, L: BidLedger, today: str, cause: int | None) -> None:
        before = L.status
        if L.status in ("RECEIVED", "WAITING_INFO", "IN_REVIEW", "REVIEW_REQUIRED"):
            crit = self._critical_open(L)
            L.status = "WAITING_INFO" if crit else "IN_REVIEW"
        if before != L.status:
            self._ev(L, today, "SYSTEM", "system", "BID_STATUS_CHANGED", "bid", None, before=before, after=L.status, caused_by=cause)

    # ── 4. 위험 표시(지시서 7.6) ─────────────────────────────────────────────────
    def _flags_from_run(self, L: BidLedger, run: Run) -> None:
        calc = E.Calc({})
        calc.metrics = [E.Metric(m["id"], m["key"], m["value"], m["unit"], m["source_type"], m["formula_key"], m["dims"], m["lineage"]["inputs"], m["lineage"]["sources"])
                        for m in run.metrics]
        _, caps, _ = self._ctx(L)
        self._flags(L, run, calc, caps)

    def _flags(self, L: BidLedger, run: Run, calc, caps) -> None:
        L.flags = [f for f in L.flags if f.calc_run_id != run.id]
        allr, fin, sales, prodsales = ALL_ROLES, [F.value, CEO.value], [S.value, SX.value, CEO.value], [S.value, PR.value, CEO.value]

        def add(o, rule, lens, sev, ev_ids, roles, text):
            L.flags.append(Flag(id=L.nid("flag"), calc_run_id=run.id, option_id=o.id if o else None, rule_key=rule, lens=lens, severity=sev,
                                evidence_ids=ev_ids, visible_roles=roles, text=text))

        def gid(key, **dims):
            try:
                return calc.get(key, **dims).id
            except StopIteration:
                return None
        f1 = gid("f1_weighted_util_pct")
        opts = E.capex_options(self.inp)
        caprow = {r["line_id"]: r for r in self.inp["capacity"]}

        def plant_avg(plant):
            rows = [r for r in self.inp["capacity"] if r["plant_id"] == plant]
            return sum(float(r["capacity_qty"]) * float(r["utilization_pct"]) for r in rows) / sum(float(r["capacity_qty"]) for r in rows)
        for o in [x for x in L.options if x.calc_run_id == run.id]:
            for pid in o.offered:
                ln = self.line_of.get(pid)
                if not ln or ln not in caps:
                    continue
                name = self.topo["label"].get(ln, ln)
                plant = caprow[ln]["plant_id"]
                if caps[ln]["util"] - plant_avg(plant) >= 5:
                    ev = [gid("utilization_pct", line_id=ln)] + ([f1] if plant == "F1" else [])
                    add(o, "RF-PROD-01", "PRODUCTION", "INFO", ev, allr, "공장 평균 가동률보다 %s 가동률 {{calc:%d}}가 높습니다. 평균만 보면 병목이 가려집니다." % (name, ev[0]))
                ow = gid("overload_weeks", line_id=ln)
                if ow and calc.get("overload_weeks", line_id=ln).value > 0:
                    add(o, "RF-PROD-02", "PRODUCTION", "WARN", [ow], allr, "기존 확정 주문만으로 %s 능력을 넘는 주가 {{calc:%d}} 있습니다." % (name, ow))
                dq = gid("confirmed_defects", product_id=pid)
                if dq and calc.get("confirmed_defects", product_id=pid).value >= 1:
                    add(o, "RF-QUAL-01", "QUALITY", "WARN", [dq], allr, "%s(%s) 규격이탈 확정 클레임 {{calc:%%d}}. RFP는 리콜 비용을 공급자에게 지웁니다." % (self.pname.get(pid, pid), pid) % dq)
            for c in o.capex_ids:
                rw = E.ready_week(opts[c], E.decision_date(self.inp))
                if o.supply_start_on and (date.fromisoformat(o.supply_start_on) - rw).days <= 28:
                    add(o, "RF-PROD-03", "PRODUCTION", "WARN", [gid("capex_lead_weeks", option=c)], allr, f"{c} 준비 주와 납품 개시 주의 차이가 4주 이하입니다. 설비 일정이 밀리면 바로 미충족이 됩니다.")
                for req in opts[c]["requires"]:
                    a = L.cur(req["key"])
                    if req.get("blocking", True) and (a is None or a.status != "CONFIRMED"):
                        sup = f" 공급사 {req['supplier']}" if req.get("supplier") else ""
                        add(o, "RF-PROC-01", "PROCUREMENT", "BLOCK", [gid("capex_krw", option=c)], allr, f"{c}는 {req['label'].replace(' 가능 여부', '')}이 필요합니다.{sup} 회신이 있어야 상신할 수 있습니다.")
            cs = o.metric_ids.get("capex_sum")
            if cs and calc.get("capex_sum_krw", option=o.label).value >= 0.5 * 30_000_000_000:
                add(o, "RF-FIN-01", "FINANCE", "WARN", [cs, gid("group_cash_krw")], fin, "설비투자 {{calc:%d}}가 그룹 가용자금 {{calc:%d}}의 절반 이상입니다." % (cs, gid("group_cash_krw")))
            w = o.metric_ids.get("grid_10/10")
            if w:
                add(o, "RF-FIN-02", "FINANCE", "INFO", [w, o.metric_ids.get("grid_0/0")], fin, "최악 칸(단가 -10%%, 환율 -10%%) 재료비 차감 이익 {{calc:%d}}, 기준 칸 {{calc:%d}}." % (w, o.metric_ids.get("grid_0/0")))
            add(o, "RF-FIN-03", "FINANCE", "INFO", [o.metric_ids.get("margin")], fin, "손익은 재료비만 뺀 값입니다(가정 A-08). 실제 이익보다 크고 회수기간은 짧게 나옵니다.")
            yoys = [m for m in calc.metrics if m.key == "c101_booking_yoy_pct"]
            c101 = L.cur("c101_demand_scenario")
            if yoys and all(m.value < 0 for m in yoys) and (c101 is None or c101.status != "CONFIRMED"):
                add(o, "RF-SALES-01", "SALES", "WARN", [m.id for m in yoys], sales, "C-101 최근 3개월 전년 대비 수주가 모두 감소했습니다. 원인 미확정, 영업 임원 확인 대상.")
            uo = gid("unknown_orders")
            if uo and calc.get("unknown_orders").value >= 1:
                add(o, "RF-SALES-02", "SALES", "WARN", [uo], prodsales, "상태 불명 주문 {{calc:%d}}이 부하에서 빠져 있습니다. 실제 미납이면 여력이 줄어듭니다." % uo)

    # ── 5. 상신·승인 ─────────────────────────────────────────────────────────
    def submit(self, L: BidLedger, option_id: int | None, today: str, actor: str, role: Role) -> None:
        self._need(role, (S, SX), "상신")
        crit = self._critical_open(L, ["supply_start_date", "partial_award_allowed"])
        if crit:
            raise FlowError("G5: 핵심 가정 " + ", ".join(f"'{a.label}'" for a in crit) + "이(가) 미확인입니다")
        if L.status not in ("IN_REVIEW",):
            raise FlowError(f"상신은 '검토 중' 상태에서만 가능합니다(현재 {L.status})")
        o = L.opt(option_id)
        if o.status != "PROPOSED":
            raise FlowError("제시 상태의 대안만 상신할 수 있습니다")
        crit2 = [L.cur(k) for k in o.deps if L.cur(k) and L.cur(k).is_critical and L.cur(k).status == "UNCONFIRMED"]
        if crit2:
            raise FlowError("G5: 핵심 가정 " + ", ".join(f"'{a.label}'" for a in crit2) + "이(가) 미확인입니다")
        blocks = [f for f in L.flags if f.option_id == o.id and f.severity == "BLOCK" and f.calc_run_id == L.runs[-1].id]
        if blocks:
            raise FlowError("G15: 차단 표시가 남아 있습니다 — " + ", ".join(f"{f.rule_key}({f.lens})" for f in blocks))
        if o.feasibility == "INFEASIBLE":
            raise FlowError("불가 판정 대안은 상신할 수 없습니다: " + " / ".join(o.blocking_reasons))
        ev = self._ev(L, today, "USER", actor, "OPTION_SUBMITTED", "bid_option", o.id)
        kinds = ["BID_PARTICIPATION"] + (["CAPEX"] if o.capex_ids else [])
        for k in kinds:
            ap = Appr(id=L.nid("approval"), option_id=o.id, decision_type=k, required_role=self._role_of(k), policy_version=self.policy.version)
            L.approvals.append(ap)
            for key in o.deps:
                a = L.cur(key)
                if a:
                    L.deps.append(Dep(dependent_type="approval", dependent_id=ap.id, assumption_id=a.id))
            self._ev(L, today, "SYSTEM", "system", "APPROVAL_REQUESTED", "approval", ap.id, after={"type": k, "role": ap.required_role}, caused_by=ev.id)
        before = L.status
        L.status = "PENDING_APPROVAL"
        self._ev(L, today, "SYSTEM", "system", "BID_STATUS_CHANGED", "bid", None, before=before, after=L.status, caused_by=ev.id)

    def decide(self, L: BidLedger, aid: int, decision: str, text: str | None, today: str, actor: str, role: Role) -> None:
        ap = next(a for a in L.approvals if a.id == aid)
        if role.value != ap.required_role:
            raise FlowPermission(f"G1: 이 결정은 {ap.required_role} 권한입니다")
        if ap.status != "PENDING":
            raise FlowError("대기 중인 승인만 결정할 수 있습니다")
        if decision == "CONDITIONAL" and not (text or "").strip():
            raise FlowError("조건부 승인은 조건을 입력해야 합니다")
        if decision == "REJECTED" and not (text or "").strip():
            raise FlowError("거절은 사유를 입력해야 합니다")
        ap.status, ap.decided_by, ap.decided_at = decision, actor, today
        if decision == "CONDITIONAL":
            ap.conditions_text = text
        if decision == "REJECTED":
            ap.reject_reason = text
        ev = self._ev(L, today, "USER", actor, "APPROVAL_DECIDED", "approval", aid, after={"status": decision, "text": text})
        o = L.opt(ap.option_id)
        mine = [a for a in L.approvals if a.option_id == o.id and a.status != "VOIDED"]
        if decision == "REJECTED" and ap.decision_type == "BID_PARTICIPATION":
            self._cancel_open(L, today, ev.id)
            L.status, L.hold_reason = "ON_HOLD", f"입찰 참여 거절: {text}"
            o.status = "REJECTED"
            self._ev(L, today, "SYSTEM", "system", "BID_STATUS_CHANGED", "bid", None, after="ON_HOLD", reason=L.hold_reason, caused_by=ev.id)
            return
        if decision == "REJECTED" and ap.decision_type == "CAPEX":
            o.status = "REJECTED"
            for a in mine:
                if a.status == "PENDING":
                    a.status = "VOIDED"
            L.capex_rejected = True
            L.status = "REVIEW_REQUIRED"
            L.revision += 1
            self._ev(L, today, "SYSTEM", "system", "BID_STATUS_CHANGED", "bid", None, after="REVIEW_REQUIRED", reason="설비투자 거절", caused_by=ev.id)
            L.tasks.append(Task(id=L.nid("task"), approval_id=aid, type="RESUBMIT", owner_role=S.value, due_on=L.submission_deadline,
                                payload={"reason": text, "note": "설비안 없는 재상신안 검토"}))
            self._recalc(L, ev.id, compose=True)
            self._settle_status(L, today, ev.id)
            return
        if all(a.status in ("APPROVED", "CONDITIONAL") for a in mine):
            self._approve_option(L, o, mine, today, ev.id)

    def _approve_option(self, L: BidLedger, o: Opt, apprs: list[Appr], today: str, cause: int) -> None:
        o.status = "SELECTED"
        L.selected_option_id = o.id
        conds = [a.conditions_text for a in apprs if a.conditions_text]
        bid_ap = next(a for a in apprs if a.decision_type == "BID_PARTICIPATION")
        mb = L.cur("mb_recontract")
        specs = [("PROPOSAL_TERMS", S, {"offered": o.offered, "supply_start": o.supply_start_on, "split": "분할 납품(주 단위 균등)", "conditions": conds,
                                        "conditions_acknowledged": not conds}),
                 ("CAPACITY_RESERVATION", PR, {"note": "가예약 → 확정"})]
        if o.capex_ids:
            specs.append(("CAPEX_SUBMISSION", F, {"capex": o.capex_ids, "note": "그룹 설비투자 상신 요약(모의)"}))
        if "OPT-02" in o.capex_ids:
            specs.append(("PROCUREMENT_RECONTRACT", PC, {"supplier": "V-205", "material": "M-B", "conditions": (mb.value or {}).get("conditions") if mb and isinstance(mb.value, dict) else None}))
        for t, r, payload in specs:
            tk = Task(id=L.nid("task"), approval_id=bid_ap.id, type=t, owner_role=r.value, due_on=L.submission_deadline, payload=payload, option_id=o.id)
            L.tasks.append(tk)
            for key in o.deps:
                a = L.cur(key)
                if a:
                    L.deps.append(Dep(dependent_type="task", dependent_id=tk.id, assumption_id=a.id))
        calc, caps, comm = self._ctx(L)
        led = E.ledger(self.inp, caps, comm, tuple(o.capex_ids), E.decision_date(self.inp))
        start = date.fromisoformat(o.supply_start_on)
        i0 = led["weeks"].index(E.monday(start).isoformat())
        for p, q in o.offered.items():
            dem = q / 52.0
            for i in range(i0, min(i0 + 52, E.HORIZON_WEEKS)):
                left = dem
                for ln in led["product_lines"].get(p, []):
                    if ln not in led["lines"] or left <= 0:
                        continue
                    take = min(left, led["lines"][ln][i]["atp"])
                    if take > 0:
                        rv = Resv(id=L.nid("resv"), option_id=o.id, line_id=ln, week_start=led["weeks"][i], qty=round(take, 1))
                        L.reservations.append(rv)
                        left -= take
        for key in o.deps:
            a = L.cur(key)
            if a:
                L.deps.append(Dep(dependent_type="capacity_reservation", dependent_id=o.id, assumption_id=a.id))
        before = L.status
        L.status = "APPROVED"
        self._ev(L, today, "SYSTEM", "system", "BID_STATUS_CHANGED", "bid", None, before=before, after="APPROVED", caused_by=cause)

    def complete_task(self, L: BidLedger, tid: int, today: str, actor: str, role: Role, acknowledge_conditions: bool = False) -> None:
        tk = next(t for t in L.tasks if t.id == tid)
        if role.value != tk.owner_role:
            raise FlowPermission(f"작업 완료는 담당({tk.owner_role})만 합니다")
        if tk.status not in ("OPEN", "IN_PROGRESS"):
            raise FlowError("열린 작업만 완료할 수 있습니다")
        if tk.type == "PROPOSAL_TERMS" and tk.payload.get("conditions") and not (acknowledge_conditions or tk.payload.get("conditions_acknowledged")):
            raise FlowError("조건부 승인의 조건을 반영했다고 표시해야 완료할 수 있습니다")
        if tk.type == "PROPOSAL_TERMS":
            tk.payload = {**tk.payload, "conditions_acknowledged": True}
        tk.status = "DONE"
        ev = self._ev(L, today, "USER", actor, "TASK_DONE", "task", tid)
        if tk.type == "CAPACITY_RESERVATION":
            for r in L.reservations:
                if r.option_id == tk.option_id and r.status == "TENTATIVE":
                    r.status = "CONFIRMED"
            self._ev(L, today, "SYSTEM", "system", "RESERVATION_CONFIRMED", "bid_option", tk.option_id, caused_by=ev.id)
        if tk.type == "PROCUREMENT_RECONTRACT":
            L.messages.append(Msg(id=L.nid("msg"), party="V-205", direction="OUT", body="M-B 재계약 추진 요청(모의)", on=today))
        live = [t for t in L.tasks if t.option_id == L.selected_option_id]
        if L.status == "APPROVED" and all(any(t.type == k and t.status == "DONE" for t in live) for k in ("PROPOSAL_TERMS", "CAPACITY_RESERVATION")):
            L.status = "READY_TO_SUBMIT"
            self._ev(L, today, "SYSTEM", "system", "BID_STATUS_CHANGED", "bid", None, before="APPROVED", after="READY_TO_SUBMIT", caused_by=ev.id)

    def submit_bid(self, L: BidLedger, today: str, actor: str, role: Role) -> None:
        self._need(role, (S, SX), "입찰 제출")
        if L.status != "READY_TO_SUBMIT":
            raise FlowError("제출 준비 완료 상태에서만 제출할 수 있습니다")
        if date.fromisoformat(today) > date.fromisoformat(L.submission_deadline):
            raise FlowError("마감일이 지났습니다")
        L.status = "SUBMITTED"
        L.messages.append(Msg(id=L.nid("msg"), party="C-201_BUYER", direction="OUT", body="제안서 제출(모의)", on=today))
        self._ev(L, today, "USER", actor, "BID_SUBMITTED", "bid", None, after="SUBMITTED")

    def external_notice(self, L: BidLedger, party: str, text: str, key: str | None, value, today: str, actor: str, role: Role) -> None:
        """외부 추가 통지(모의, 시연 진행자 입력). 가정은 바꾸지 않는다 — 영업이 검토 후 '가정 교체'로 반영(G12)."""
        self._need(role, (DEMO,), "외부 통지 입력")
        if party not in ("C-201_BUYER", "V-205"):
            raise FlowError("상대는 C-201 바이어 또는 V-205")
        L.messages.append(Msg(id=L.nid("msg"), party=party, direction="IN", body=text, on=today))
        L.notices.append({"id": L.nid("notice"), "party": party, "text": text, "key": key, "value": value, "status": "OPEN", "on": today})
        self._ev(L, today, "EXTERNAL_MOCK", actor, "EXTERNAL_NOTICE", "bid", None, after={"key": key, "value": value}, reason=text)

    def close_notice(self, L: BidLedger, nid: int) -> None:
        for n in L.notices:
            if n["id"] == nid:
                n["status"] = "APPLIED"

    def add_note(self, L: BidLedger, option_id: int, text: str, today: str, actor: str, role: Role) -> None:
        self._need(role, (F,), "재무 검토 의견")
        L.notes.append({"option_id": option_id, "by": actor, "role": role.value, "text": text, "on": today})
        self._ev(L, today, "USER", actor, "FINANCE_NOTE", "bid_option", option_id, after=text)

    def acknowledge_flag(self, L: BidLedger, fid: int, today: str, actor: str, role: Role) -> None:
        f = next(x for x in L.flags if x.id == fid)
        lens_role = {"PRODUCTION": PR, "FINANCE": F, "SALES": S, "PROCUREMENT": PC, "QUALITY": Q}[f.lens]
        if f.severity != "WARN":
            raise FlowError("주의(WARN) 표시만 확인 처리합니다. 차단은 원인이 풀려야 사라집니다")
        if role not in (lens_role, SX) or (f.lens != "SALES" and role is SX):
            raise FlowPermission(f"{lens_role.value} 역할만 확인할 수 있습니다")
        f.acknowledged_by = f"{actor}({role.value})"
        self._ev(L, today, "USER", actor, "RISK_FLAG_ACKNOWLEDGED", "risk_flag", fid)

    # ── 6. 전파(지시서 10.3) ──────────────────────────────────────────────────────
    def _propagate(self, L: BidLedger, new: A, today: str, cause: int, old: A | None = None, refuted: bool = False) -> None:
        old_id = old.id if old else new.supersedes_id
        before_graph = L.graph_state()
        before_graph["assumptions"][old_id] = "CONFIRMED"
        dependents = [d for d in L.deps if d.assumption_id == old_id]
        sel = L.opt(L.selected_option_id) if L.selected_option_id else None
        if sel and not refuted and new.key in sel.deps:
            calc, caps, comm = self._ctx(L)
            start = date.fromisoformat(new.value) if new.key == "supply_start_date" else date.fromisoformat(sel.supply_start_on)
            led = E.ledger(self.inp, caps, comm, tuple(sel.capex_ids), E.decision_date(self.inp))
            res = {p: E.evaluate_product(led, p, q, start) for p, q in sel.offered.items()}
            prev = {p: E.evaluate_product(led, p, q, date.fromisoformat(sel.supply_start_on)) for p, q in sel.offered.items()}
            if all(r["fill_rate"] >= 1 - 1e-9 for r in res.values()) and all(res[p]["fill_rate"] >= prev[p]["fill_rate"] - 1e-9 for p in res):
                self._move_deps(L, old_id, new.id)
                self._ev(L, today, "SYSTEM", "system", "REVALIDATED_NO_CHANGE", "bid_option", sel.id,
                         before={p: round(100 * prev[p]["fill_rate"], 1) for p in prev}, after={p: round(100 * res[p]["fill_rate"], 1) for p in res}, caused_by=cause)
                L.change_banner = {"key": new.key, "label": new.label, "before": old.value if old else None, "after": new.value, "kept": True,
                                   "metrics": {p: [round(100 * prev[p]["fill_rate"], 1), round(100 * res[p]["fill_rate"], 1)] for p in res}, "event_id": cause,
                                   "before_graph": before_graph}
                if new.key == "supply_start_date":
                    sel.supply_start_on = new.value
                return
            changed_metrics = {p: [round(100 * prev[p]["fill_rate"], 1), round(100 * res[p]["fill_rate"], 1)] for p in res}
        else:
            changed_metrics = {}
        if not dependents and not sel and not refuted:
            self._move_deps(L, old_id, new.id)
            self._recalc(L, cause, compose=True)
            self._settle_status(L, today, cause)
            return
        counts = {"stale": 0, "voided": 0, "blocked": 0, "released": 0, "resubmit": 0}
        dep_opts = {d.dependent_id for d in dependents if d.dependent_type == "bid_option"} | ({sel.id} if sel else set())
        for o in L.options:
            if o.id in dep_opts and o.status in ("PROPOSED", "SELECTED"):
                o.status = "STALE"
                counts["stale"] += 1
                self._ev(L, today, "SYSTEM", "system", "OPTION_STALE", "bid_option", o.id, caused_by=cause)
        for a in L.approvals:
            if a.option_id in dep_opts and a.status in ("PENDING", "APPROVED", "CONDITIONAL"):
                a.status = "VOIDED"
                counts["voided"] += 1
                self._ev(L, today, "SYSTEM", "system", "APPROVAL_VOIDED", "approval", a.id, caused_by=cause)
        for t in list(L.tasks):
            if t.option_id in dep_opts:
                if t.status in ("OPEN", "IN_PROGRESS"):
                    t.status = "BLOCKED"
                    counts["blocked"] += 1
                    self._ev(L, today, "SYSTEM", "system", "TASK_BLOCKED", "task", t.id, caused_by=cause)
                elif t.status == "DONE":
                    nt = Task(id=L.nid("task"), approval_id=t.approval_id, type="RESUBMIT", owner_role=t.owner_role, due_on=L.submission_deadline,
                              payload={"replaces": t.id, "reason": f"전제 변경: {new.label}"})
                    L.tasks.append(nt)
                    counts["resubmit"] += 1
                    self._ev(L, today, "SYSTEM", "system", "TASK_CREATED", "task", nt.id, after="RESUBMIT", caused_by=cause)
        rel_lines = set()
        for r in L.reservations:
            if r.option_id in dep_opts and r.status in ("TENTATIVE", "CONFIRMED"):
                r.status = "RELEASED"
                rel_lines.add((r.option_id, r.line_id))
                counts["released_weeks"] = counts.get("released_weeks", 0) + 1
        counts["released"] = len(rel_lines)  # 예약 건수 = 대안×라인(주별 행 수는 released_weeks)
        if counts["released"]:
            self._ev(L, today, "SYSTEM", "system", "RESERVATIONS_RELEASED", "bid_option", next(iter(dep_opts)), after=counts["released"], caused_by=cause)
        before = L.status
        L.status = "REVIEW_REQUIRED" if before not in ("RECEIVED", "WAITING_INFO", "IN_REVIEW") else before
        L.revision += 1
        L.selected_option_id = None
        self._ev(L, today, "SYSTEM", "system", "BID_STATUS_CHANGED", "bid", None, before=before, after=L.status, reason=f"가정 변경: {new.label}", caused_by=cause)
        L.change_banner = {"key": new.key, "label": new.label, "before": old.value if old else None, "after": new.value, "kept": False, "counts": counts,
                           "metrics": changed_metrics, "event_id": cause, "revision": L.revision, "before_graph": before_graph}
        self._move_deps(L, old_id, new.id)
        self._recalc(L, cause, compose=True)
        self._settle_status(L, today, cause)

    def _cancel_open(self, L: BidLedger, today: str, cause: int) -> None:
        for t in L.tasks:
            if t.status in ("OPEN", "IN_PROGRESS", "BLOCKED"):
                t.status = "CANCELLED"
        for r in L.reservations:
            if r.status != "RELEASED":
                r.status = "RELEASED"
        self._ev(L, today, "SYSTEM", "system", "OPEN_WORK_CANCELLED", "bid", None, caused_by=cause)

    # ── 7. 가상 시계·마감(G10) ───────────────────────────────────────────────────
    def tick(self, L: BidLedger, today: str, actor: str) -> None:
        d, dl = date.fromisoformat(today), date.fromisoformat(L.submission_deadline)
        for n in (7, 3):
            mark = (dl - timedelta(days=n)).isoformat()
            if d >= dl - timedelta(days=n) and mark not in L.deadline_ticks and L.status not in ("SUBMITTED", "ON_HOLD"):
                L.deadline_ticks.append(mark)
                ev = self._ev(L, today, "SYSTEM", "clock", "DEADLINE_TICK", "bid", None, after={"days_left": n})
                open_reqs = [r for r in L.requests if r.status in ("DRAFT", "SENT")]
                if open_reqs:
                    L.tasks.append(Task(id=L.nid("task"), approval_id=None, type="INFO_FOLLOWUP", owner_role=S.value, due_on=L.submission_deadline,
                                        payload={"requests": [r.id for r in open_reqs], "draft": f"마감 {n}일 전: 회신이 필요한 확인요청 {len(open_reqs)}건 독촉"}))
                    self._ev(L, today, "SYSTEM", "system", "TASK_CREATED", "task", L.tasks[-1].id, after="INFO_FOLLOWUP", caused_by=ev.id)
        if d >= dl and L.status not in ("APPROVED", "READY_TO_SUBMIT", "SUBMITTED", "ON_HOLD"):
            before = L.status
            L.status, L.hold_reason = "ON_HOLD", "마감 경과"
            self._cancel_open(L, today, None)
            self._ev(L, today, "SYSTEM", "clock", "BID_STATUS_CHANGED", "bid", None, before=before, after="ON_HOLD", reason="마감 경과")


def now_iso() -> str:  # 감사 시각(실제 시계는 화면 표시용으로만)
    return datetime.utcnow().isoformat()
