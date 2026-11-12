"""HTTP 어댑터(FastAPI). 로그인·RBAC 뒤에서만 업무 API 를 연다. 비즈니스 규칙은 application/domain 에 있다.

보안: 서버 세션(HttpOnly·Secure·SameSite=Strict) + 쓰기 요청 CSRF 헤더, 로그인 시도 제한, 보안 헤더(CSP 등),
      솔버 동시 실행 상한(429), 입력은 Pydantic(extra=forbid), 도메인 오류 → 4xx 매핑. /healthz 는 DB 를 부르지 않는다.
"""
from __future__ import annotations

import json
import os
import threading
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field

from dealcockpit.adapters.auth import LoginThrottle, MemoryAuthStore, PostgresAuthStore, User, seed_accounts, verify_password
from dealcockpit.adapters.golden_loader import (DEFAULT_ROOT, contract_text, load_case, load_data_health, load_history, load_overview,
                                                load_reply_stats, load_w05_inputs, mock_reply)
from dealcockpit.adapters.repo_memory import MemoryRepository
from dealcockpit.adapters.solver_lp import LpSolver
from dealcockpit.application.checkpoints import build_checkpoints
from dealcockpit.application.negotiation import NegotiationService, WorkflowError
from dealcockpit.application.ports import ConcurrencyConflict
from dealcockpit.application.scenarios import W1, ScenarioSpec
from dealcockpit.domain.approval import PermissionDenied, UnknownApprovalAction
from dealcockpit.domain.common import Provenance, Role
from dealcockpit.domain.parameters import ParameterError
from dealcockpit.domain.plan import AuditEvent
from dealcockpit.domain.workflow import ReplyOutcome
from dealcockpit.w05.agent import run_agent
from dealcockpit.w05.flow import AgentRun, BidLedger, FlowError, W05Flow

AGENT_TRIGGERS = {"BID_RECEIVED": "BID_RECEIVED", "ANSWER_ACCEPTED": "INFO_REQUEST_ANSWERED", "INFO_REQUEST_ANSWERED": "INFO_REQUEST_ANSWERED",
                  "ASSUMPTION_SUPERSEDED": "ASSUMPTION_SUPERSEDED", "APPROVAL_DECIDED": "APPROVAL_DECIDED", "DEADLINE_TICK": "DEADLINE_TICK"}
from dealcockpit.w05.view import board, build_view, lineage, week_orders

APP_NAME = "capact"
WS_ID = "WS-C201"  # 첫 검토 회차. 테스트 초기화는 새 회차(WS-C201-R2…)를 열고 이전 회차·감사 기록은 그대로 둔다.
COOKIE = "capact_session"
CSRF_HEADER = "x-capact-csrf"
FRONTEND = DEFAULT_ROOT / "frontend"

_S, _SX, _PR, _Q, _PC, _F, _CEO, _PA = (Role.SALES, Role.SALES_EXEC, Role.PRODUCTION, Role.QUALITY, Role.PROCUREMENT, Role.FINANCE,
                                        Role.CEO_BOARD, Role.POLICY_ADMIN)
_DEMO = Role.DEMO_ADMIN
_BUSINESS = (_S, _SX, _PR, _Q, _PC, _F, _CEO)
# 권한표(단일 진실): 라우트 검사와 화면 표시가 같은 표를 쓴다. 객체 단위 규칙(문의 담당·승인 주체·파라미터 담당)은 서버가 추가로 검사.
ACCESS: dict[str, tuple[Role, ...]] = {
    "plan_inquiries": (_S, _SX),
    "send": _BUSINESS,
    "reply": _BUSINESS,  # + 문의 담당 역할 또는 영업
    "accept_counter": _BUSINESS,
    "submit": (_S, _SX, _CEO),
    "accept_loss": (_SX, _CEO),
    "decide": _BUSINESS,  # + 승인 주체 일치
    "promote": (_S, _SX, _CEO),
    "cond_fx": (_F, _SX, _CEO),
    "cond_recall": (_Q,),
    "registry": _BUSINESS,  # + 파라미터별 담당 역할
    "policy": (_PA,),
    # W05 입찰 건 흐름(객체 단위 규칙 — 수신 역할·권한자·작업 담당 — 은 w05/flow.py 가 추가로 검사)
    "w05_receive": (_S, _SX),
    "w05_send": (_S, _SX),
    "w05_confirm": (_S, _SX),
    "w05_supersede": (_S, _SX),
    "w05_submit": (_S, _SX),
    "w05_submit_bid": (_S, _SX),
    "w05_answer": (*_BUSINESS, _DEMO),  # 외부 회신은 시연 진행자(모의), 내부 회신은 수신 역할
    "w05_decide": (_SX, _CEO),  # 권한표의 권한자(기본 CEO/이사회)
    "w05_task": _BUSINESS,
    "w05_note": (_F,),
    "w05_ack": _BUSINESS,
    "demo_act_as": (_DEMO,),
    # 테스트 도구(모의 입력) — 운영 업무와 분리
    "test_mock_reply": (_DEMO,),  # 시연 도구는 시연 진행자만(운영 계정에는 보이지 않음)
    "test_clock": (_DEMO,),
    "test_reset": (_DEMO, _PA),
    # 열람 범위(관리 정보)
    "audit_view": (_PA, _CEO, Role.REVIEWER, _DEMO),  # 시스템 감사 기록
    "access_view": (_PA, Role.REVIEWER, _DEMO),  # 역할별 권한 매트릭스
    "registry_view_all": (_SX, _CEO, _PA, Role.REVIEWER, _DEMO),  # 모델 파라미터 전체(그 외는 자기 담당만)
    "integrations_view": (_PA, Role.REVIEWER, _DEMO),  # 외부 연결 상태
}


def caps_for(role: Role) -> list[str]:
    return sorted(k for k, v in ACCESS.items() if role in v)


class Body(BaseModel):
    model_config = ConfigDict(extra="forbid")


class LoginIn(Body):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=256)


class ReplyIn(Body):
    variant: str | None = Field(default=None, max_length=64)  # 모의 회신 정책표 변형
    outcome: ReplyOutcome | None = None  # 사람 입력 회신
    terms: dict = {}
    text: str = Field(default="", max_length=2000)


class ReaskIn(Body):
    answer_by: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")


class ClockIn(Body):
    today: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")


class SubmitIn(Body):
    scenario: str = Field(pattern=r"^[A-Y]$")
    accept_loss: bool = False


class DecideIn(Body):
    approve: bool
    note: str | None = Field(default=None, max_length=500)


class CounterIn(Body):
    breakeven_multiplier: float | None = Field(default=None, gt=1, le=10)


class ConditionsIn(Body):
    fx_label: str | None = Field(default=None, pattern=r"^(rfp|as_of|downside)$")
    price_pressure_pct: float | None = Field(default=None, ge=-30, le=0)
    recall_replacement_week: int | None = Field(default=None, ge=0, le=51)


class ParamIn(Body):
    value: float
    reason: str = Field(min_length=5, max_length=500)


class PolicyIn(Body):
    approver: Role
    reason: str = Field(min_length=5, max_length=500)


class SimSpecIn(Body):
    label: str = Field(min_length=1, max_length=60)
    protect_existing: bool = True
    use_consent: bool = False
    min_accept: float | None = Field(default=None, ge=0, le=1)


class SimulateIn(Body):
    what_if: dict[str, float | str] = {}
    specs: dict[str, SimSpecIn] = {}


class ActAsIn(Body):
    role: Role | None = None

class W05Answer(Body):
    answer: dict = {}
    text: str = Field(default="", max_length=2000)

class W05Edit(Body):
    question: str = Field(min_length=5, max_length=1000)

class W05Supersede(Body):
    value: str | bool | float | int | dict | None
    reason: str = Field(min_length=2, max_length=300)
    notice_id: int | None = None


class W05Notice(Body):
    party: str = Field(pattern=r"^(C-201_BUYER|V-205)$")
    text: str = Field(min_length=2, max_length=1000)
    key: str | None = Field(default=None, pattern=r"^(supply_start_date|partial_award_allowed)$")
    value: str | bool | None = None

class W05Submit(Body):
    option_id: int | None = None

class W05Decide(Body):
    decision: str = Field(pattern=r"^(APPROVED|CONDITIONAL|REJECTED)$")
    text: str | None = Field(default=None, max_length=500)

class W05Task(Body):
    acknowledge_conditions: bool = False

class W05Note(Body):
    text: str = Field(min_length=2, max_length=1000)


class PrepareIn(Body):
    scenario: str = Field(pattern=r"^(s2|s3b)$")


class Ctx:
    """앱 단위 의존성 묶음(테스트에서 메모리 구현으로 교체)."""

    def __init__(self, repo, auth, solver, cookie_secure: bool):
        self.repo, self.auth, self.solver, self.cookie_secure = repo, auth, solver, cookie_secure
        self.svc = NegotiationService(repo, solver, load_case(), load_reply_stats())
        self.health = load_data_health()
        self.overview = load_overview()
        self.history = load_history()
        self.test_tools = os.environ.get("CAPACT_TEST_TOOLS", "1") == "1"
        self._ws_id: str | None = None
        self.w05_inputs = load_w05_inputs()
        self.acting: dict[str, Role] = {}  # 세션 토큰 → 시연 진행자가 대리 수행 중인 역할(감사에 기록)
        self.throttle = LoginThrottle()
        self.solver_slots = threading.BoundedSemaphore(2)
        self.cache: dict[tuple, dict] = {}
        self.init_lock = threading.Lock()

    def ws_id(self) -> str:
        """현재 검토 회차(가장 최근에 열린 회차)."""
        if self._ws_id is None:
            cur, n = WS_ID, 2
            while self.repo.load(f"{WS_ID}-R{n}") is not None:
                cur, n = f"{WS_ID}-R{n}", n + 1
            self._ws_id = cur
        return self._ws_id

    def ensure_workspace(self) -> None:
        with self.init_lock:
            if self.repo.load(self.ws_id()) is None:
                with self.solving():
                    self.svc.open(self.ws_id(), "system")

    def reset(self, actor: str, role: Role) -> str:
        """테스트 초기화: 새 회차를 열고 이전 회차에 이관 기록을 남긴다(삭제 없음)."""
        from datetime import datetime, timezone

        with self.init_lock:
            old = self.ws_id()
            n = int(old.rsplit("-R", 1)[1]) + 1 if "-R" in old else 2
            new = f"{WS_ID}-R{n}"
            with self.solving():
                self.svc.open(new, actor, role=role)
            self.repo.append_audit(old, AuditEvent(event_id=f"RESET-{n}", at=datetime.now(timezone.utc), actor=actor, actor_role=role.value,
                                                   action="test_reset", object_ref=new))
            self._ws_id = new
            self.cache.clear()
            return new

    def solving(self):
        ctx = self

        class _Slot:
            def __enter__(self_):
                if not ctx.solver_slots.acquire(timeout=20):
                    raise HTTPException(429, "계산 요청이 몰려 있습니다. 잠시 후 다시 시도하세요.")

            def __exit__(self_, *a):
                ctx.solver_slots.release()

        return _Slot()


def _ctx_from_env() -> Ctx:
    db = os.environ.get("DATABASE_URL")
    if db:
        from dealcockpit.adapters.repo_postgres import PostgresRepository

        repo = PostgresRepository(db, migrate=True, migrate_url=os.environ.get("DIRECT_DATABASE_URL") or db)
        auth = PostgresAuthStore(db)
    else:
        if os.environ.get("CAPACT_ENV") == "production":
            raise RuntimeError("운영 환경에는 DATABASE_URL 이 필요하다(메모리 저장 금지)")
        repo, auth = MemoryRepository(), MemoryAuthStore()
    seed_accounts(auth, os.environ.get("CAPACT_ACCOUNTS"))
    return Ctx(repo, auth, LpSolver("SCIP"), cookie_secure=os.environ.get("CAPACT_COOKIE_SECURE", "1") == "1")


def create_app(ctx: Ctx | None = None) -> FastAPI:
    state: dict = {"ctx": ctx}

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if state["ctx"] is None and os.environ.get("CAPACT_LAZY_INIT") != "1":
            state["ctx"] = _ctx_from_env()  # 마이그레이션·계정 시드(가벼운 작업만, 60초 기동 예산 안)
        yield

    app = FastAPI(title="capact", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)

    def C() -> Ctx:
        if state["ctx"] is None:
            state["ctx"] = _ctx_from_env()
        return state["ctx"]

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        if request.method not in ("GET", "HEAD") and request.url.path.startswith("/api/") and request.url.path != "/api/login":
            if request.headers.get(CSRF_HEADER) != "1":
                return JSONResponse({"detail": "CSRF 헤더 누락"}, status_code=403)
        resp = await call_next(request)
        resp.headers["Content-Security-Policy"] = ("default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; "
                                                   "connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["X-Frame-Options"] = "DENY"
        resp.headers["Referrer-Policy"] = "no-referrer"
        if request.url.path.startswith("/static/") and not request.url.path.startswith("/static/fonts/"):
            resp.headers["Cache-Control"] = "no-cache"  # 배포 직후 이전 화면 코드가 남지 않게(ETag 로 재검증만)
        if request.url.path.startswith("/api/"):
            resp.headers["Cache-Control"] = "no-store"
        return resp

    @app.exception_handler(WorkflowError)
    async def _wf(_, e):
        return JSONResponse({"detail": str(e)}, status_code=409)

    @app.exception_handler(ConcurrencyConflict)
    async def _cc(_, e):
        return JSONResponse({"detail": f"다른 사용자가 먼저 변경했습니다. 새로고침 후 다시 시도하세요. ({e})"}, status_code=409)

    @app.exception_handler(PermissionError)
    async def _pe(_, e):
        return JSONResponse({"detail": str(e)}, status_code=403)

    @app.exception_handler(PermissionDenied)
    async def _pd(_, e):
        return JSONResponse({"detail": str(e)}, status_code=403)

    @app.exception_handler(ParameterError)
    async def _par(_, e):
        return JSONResponse({"detail": str(e)}, status_code=422)

    @app.exception_handler(UnknownApprovalAction)
    async def _ua(_, e):
        return JSONResponse({"detail": str(e)}, status_code=422)

    @app.exception_handler(FlowError)
    async def _fe(_, e):
        return JSONResponse({"detail": str(e)}, status_code=409)

    @app.exception_handler(ValueError)
    async def _ve(_, e):
        return JSONResponse({"detail": str(e)}, status_code=422)

    # ── 공개 ───────────────────────────────────────────────────────────────────
    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok", "app": APP_NAME}

    @app.get("/")
    def index():
        return FileResponse(FRONTEND / "index.html")

    if FRONTEND.exists():
        app.mount("/static", StaticFiles(directory=FRONTEND), name="static")

    # ── 인증 ───────────────────────────────────────────────────────────────────
    def current_user(request: Request) -> User:
        tok = request.cookies.get(COOKIE)
        name = C().auth.get_session(tok) if tok else None
        if not name:
            raise HTTPException(401, "로그인이 필요합니다")
        rec = C().auth.get_user(name)
        if not rec:
            raise HTTPException(401, "계정이 없습니다")
        request.state.real_role = rec[0]
        acting = C().acting.get(tok) if rec[0] is Role.DEMO_ADMIN else None
        request.state.acting = acting
        return User(name, acting or rec[0])

    def presenter(cap: str):
        """시연 도구는 '로그인한 실제 역할' 기준. 시연 진행자가 다른 역할로 대리 중이어도 초기화, 시계, 모의 입력은 쓸 수 있어야 한다."""
        def dep(request: Request, u: User = Depends(current_user)) -> User:
            if u.role in ACCESS[cap] or request.state.real_role in ACCESS[cap]:
                return u
            raise HTTPException(403, f"권한 없음: 시연 도구는 {', '.join(r.value for r in ACCESS[cap])} 만")
        return dep

    def as_presenter(request: Request, u: User) -> Role:
        return Role.DEMO_ADMIN if request.state.real_role is Role.DEMO_ADMIN else u.role

    def roles(*allowed: Role):
        def dep(u: User = Depends(current_user)) -> User:
            if u.role not in allowed:
                raise HTTPException(403, f"권한 없음: {u.role.value} (필요: {', '.join(r.value for r in allowed)})")
            return u
        return dep

    @app.post("/api/login")
    def login(body: LoginIn, request: Request, response: Response):
        ctx = C()
        key = f"{body.username}|{request.client.host if request.client else '-'}"
        if ctx.throttle.blocked(key):
            raise HTTPException(429, "로그인 시도가 너무 많습니다. 10분 후 다시 시도하세요.")
        rec = ctx.auth.get_user(body.username)
        if not rec or not verify_password(body.password, rec[1]):
            ctx.throttle.fail(key)
            raise HTTPException(401, "아이디 또는 비밀번호가 올바르지 않습니다")
        ctx.throttle.reset(key)
        tok = ctx.auth.create_session(body.username)
        response.set_cookie(COOKIE, tok, httponly=True, secure=ctx.cookie_secure, samesite="strict", max_age=8 * 3600, path="/")
        return {"username": body.username, "role": rec[0].value}

    @app.post("/api/logout")
    def logout(request: Request, response: Response):
        tok = request.cookies.get(COOKIE)
        if tok:
            C().auth.delete_session(tok)
        response.delete_cookie(COOKIE, path="/")
        return {"ok": True}

    @app.get("/api/me")
    def me(request: Request, u: User = Depends(current_user)):
        ctx = C()
        real = request.state.real_role
        return {"username": u.username, "role": u.role.value, "real_role": real.value, "acting": request.state.acting is not None,
                "can": sorted(set(caps_for(u.role)) | ({c for c in caps_for(real) if c.startswith("test_")} | {"demo_act_as"} if real is Role.DEMO_ADMIN else set())),
                "test_tools": ctx.test_tools and real is Role.DEMO_ADMIN, "workspace": ctx.ws_id()}


    @app.post("/api/demo/act-as")
    def act_as(body: ActAsIn, request: Request, u: User = Depends(current_user)):
        """시연 진행자 전용 역할 전환. 서버가 대리 역할로 권한을 검사하고, 기록에는 실제 사용자와 대리 역할을 함께 남긴다."""
        if request.state.real_role is not Role.DEMO_ADMIN:
            raise HTTPException(403, "역할 전환은 시연 진행자 계정만 가능합니다")
        tok = request.cookies.get(COOKIE)
        if body.role is None or body.role is Role.DEMO_ADMIN:
            C().acting.pop(tok, None)
        else:
            if body.role in (Role.POLICY_ADMIN,):
                raise HTTPException(403, "정책관리자로는 전환할 수 없습니다")
            C().acting[tok] = body.role
        return {"ok": True, "role": (C().acting.get(tok) or Role.DEMO_ADMIN).value}

    @app.get("/api/access")
    def access(u: User = Depends(current_user)):
        if u.role not in ACCESS["access_view"]:
            # 일반 계정은 자기 권한만(전체 매트릭스는 관리 정보)
            return {"roles": [u.role.value], "access": {k: [u.role.value] for k, v in ACCESS.items() if u.role in v}}
        """역할별 권한표(읽기 전용). 화면의 버튼 표시와 서버 검사가 같은 표를 쓴다."""
        return {"roles": [r.value for r in Role], "access": {k: [r.value for r in v] for k, v in ACCESS.items()}}

    # ── 읽기 ───────────────────────────────────────────────────────────────────
    def _cp(ctx: Ctx, what_if: dict | None = None, specs: dict | None = None) -> dict:
        ctx.ensure_workspace()
        ws = ctx.repo.load(ctx.ws_id())
        key = (ws.workspace_id, ws.version, json.dumps(what_if or {}, sort_keys=True), json.dumps({k: v.model_dump() for k, v in (specs or {}).items()}, sort_keys=True))
        if key not in ctx.cache:
            extra = {k: ScenarioSpec(v.label, W1, protect_existing=v.protect_existing, use_consent=v.use_consent, min_accept=v.min_accept)
                     for k, v in (specs or {}).items()}
            with ctx.solving():
                ctx.cache[key] = build_checkpoints(ctx.svc, ws, ctx.health, extra or None, what_if or None)
            if len(ctx.cache) > 32:
                ctx.cache.pop(next(iter(ctx.cache)))
        return ctx.cache[key]

    @app.get("/api/checkpoints")
    def checkpoints(u: User = Depends(current_user)):
        return _cp(C())

    @app.post("/api/simulate")
    def simulate(body: SimulateIn, u: User = Depends(current_user)):
        """세션 what-if(확정 아님): 상태를 바꾸지 않는다. 모든 로그인 사용자 가능."""
        return _cp(C(), dict(body.what_if), body.specs)

    @app.get("/api/contracts/{doc_id}")
    def contract(doc_id: str, u: User = Depends(current_user)):
        try:
            return {"doc_id": doc_id, "text": contract_text(doc_id)}
        except KeyError:
            raise HTTPException(404, "문서 없음")

    @app.get("/api/reply-variants")
    def variants(u: User = Depends(current_user)):
        from dealcockpit.adapters.golden_loader import reply_variants

        return reply_variants()

    @app.get("/api/audit")
    def audit(u: User = Depends(roles(*ACCESS["audit_view"]))):
        return [e.model_dump(mode="json") for e in C().repo.list_audit(C().ws_id())][-200:]

    @app.get("/api/integrations")
    def integrations(u: User = Depends(roles(*ACCESS["integrations_view"]))):
        """외부 연결 상태(관리 계정). 비밀값은 내보내지 않고 '있음/없음'만. 연결 방법은 docs/INTEGRATIONS.md."""
        import hashlib

        from dealcockpit.w05.agent import DEFAULT_MODEL, TOOLS, request_params

        ctx = C()
        mode = os.environ.get("AGENT_MODE", "mock")
        key = bool(os.environ.get("ANTHROPIC_API_KEY"))
        rp = request_params(os.environ.get("ANTHROPIC_MODEL", DEFAULT_MODEL), [])
        derived = DEFAULT_ROOT / "extensions" / "derived" / "w05_engine_inputs.json"
        return {"items": [
            {"id": "llm", "name": "AI 에이전트(Claude Messages API)", "mode": mode, "status": "ok" if mode != "live" or key else "missing_key",
             "detail": {"model": rp["model"] if mode == "live" else "mock-claude(시나리오 규칙)", "api_key_present": key, "endpoint": "POST " + os.environ.get("ANTHROPIC_BASE_URL", "https://api.anthropic.com") + "/v1/messages (beta)",
                        "betas": rp.get("betas"), "fallbacks": rp.get("fallbacks"), "effort": rp["output_config"]["effort"], "tools": [t["name"] for t in TOOLS],
                        "switch": "AGENT_MODE=live + ANTHROPIC_API_KEY (선택 ANTHROPIC_MODEL)"}},
            {"id": "storage", "name": "업무 원장 저장소", "mode": type(ctx.repo).__name__, "status": "ok",
             "detail": {"encryption": "AES-256-GCM(앱 단)" if type(ctx.repo).__name__ == "PostgresRepository" else "메모리(개발용)"}},
            {"id": "sources", "name": "부서 데이터(ERP, MES 대역)", "mode": "file", "status": "ok",
             "detail": {"file": "extensions/derived/w05_engine_inputs.json", "sha256": hashlib.sha256(derived.read_bytes()).hexdigest()[:16],
                        "as_of": ctx.w05_inputs["as_of"], "switch": "golden_loader.load_w05_inputs 자리에 ERP 커넥터(같은 JSON 형식)"}},
            {"id": "messaging", "name": "외부 발송(바이어, 공급사)", "mode": "mock", "status": "ok",
             "detail": {"note": "발송·수신은 외부 수신함에 모의 기록. 실제 메일 연결 시 Msg OUT 이벤트를 메일 어댑터로"}},
            {"id": "solver", "name": "보조 최적화(협상 분석)", "mode": "OR-Tools LP", "status": "ok", "detail": {"backend": "SCIP"}},
        ]}

    @app.get("/api/overview")
    def overview(u: User = Depends(current_user)):
        return C().overview

    @app.get("/api/partners")
    def partners(u: User = Depends(current_user)):
        h = C().history
        cust = [{"id": c["customer_id"], "kind": "고객", "name": c["name"], "channel": c["channel"], "share_pct": c["terms"]["revenue_share_pct"],
                 "orders": c["orders_total"]["orders"], "amount_krw": c["orders_total"]["amount_krw"], "last_order": c["orders_total"]["last"],
                 "backlog": c["backlog"]["orders"], "stale": c["stale"]["orders"], "claims": len(c["claims"]),
                 "claims_confirmed": sum(1 for x in c["claims"] if x["confirmed"]), "ar_outstanding_krw": c["ar_outstanding_krw"],
                 "trend_yoy_pct": c["booking_trend"]["vs_yoy_pct"], "contract_doc": c["contract_doc"]} for c in h["customers"]]
        ag = [{"id": a["agency_id"], "kind": "대리점", "name": a["name"], "channel": "대리점", "share_pct": None,
               "orders": a["orders_total"]["orders"], "amount_krw": a["orders_total"]["amount_krw"], "last_order": a["orders_total"]["last"],
               "backlog": a["backlog"]["orders"], "stale": a["stale"]["orders"], "claims": 0, "claims_confirmed": 0,
               "ar_outstanding_krw": a["ar_outstanding_krw"], "trend_yoy_pct": None, "contract_doc": None} for a in h["agencies"]]
        return {"as_of": h["as_of"], "note": h["note"], "items": cust + ag}

    @app.get("/api/partners/{pid}")
    def partner(pid: str, u: User = Depends(current_user)):
        ctx = C()
        h = ctx.history
        rec = next((c for c in h["customers"] if c["customer_id"] == pid), None) or next((a for a in h["agencies"] if a["agency_id"] == pid), None)
        if rec is None:
            raise HTTPException(404, "거래처 없음")
        clauses = [{"id": c.clause_id, "kind": c.kind.value, "params": c.params, "hard": c.hard, "doc": c.evidence.doc_id, "clause": c.evidence.clause,
                    "quote": c.evidence.quote, "span": list(c.evidence.char_span or ())} for c in ctx.svc.case.clauses if c.customer_id == pid]
        ctx.ensure_workspace()
        ws = ctx.repo.load(ctx.ws_id())
        related = [{"id": k, "kind": "문의", "text": r.question, "status": r.status.value, "date": r.answer_by.isoformat()}
                   for k, r in ws.requests.items() if pid in (r.counterparty or "") or pid in r.question]
        related += [{"id": k, "kind": "협상 카드", "text": f"{lv.type.value} {json.dumps(lv.params, ensure_ascii=False)}", "status": lv.status.value, "date": None}
                    for k, lv in ws.levers.items() if pid in (lv.counterparty or "") or pid in json.dumps(lv.params)]
        return {**rec, "clauses": clauses, "related": related}

    # ── 쓰기(역할별) ─────────────────────────────────────────────────────────────
    A = ACCESS

    def _run(fn):
        ctx = C()
        ctx.ensure_workspace()
        with ctx.solving():
            r = fn(ctx.svc, ctx.ws_id())
        ws = r[0] if isinstance(r, tuple) else r
        return {"ok": True, "version": ws.version, **({"result": r[1].__dict__} if isinstance(r, tuple) and hasattr(r[1], "submitted") else {})}

    @app.post("/api/inquiries/plan")
    def plan_inquiries(u: User = Depends(roles(*A["plan_inquiries"]))):
        return _run(lambda s, w: s.plan_inquiries(w, u.username, u.role))

    @app.post("/api/requests/{rid}/send")
    def send(rid: str, u: User = Depends(roles(*A["send"]))):
        return _run(lambda s, w: s.send(w, rid, u.username, u.role))

    @app.post("/api/requests/{rid}/reply")
    def reply(rid: str, body: ReplyIn, request: Request, u: User = Depends(roles(*A["reply"], *A["test_mock_reply"]))):
        ctx = C()
        ctx.ensure_workspace()
        ws = ctx.repo.load(ctx.ws_id())
        if rid not in ws.requests:
            raise HTTPException(404, "문의 없음")
        req, topic = ws.requests[rid], ws.request_topics[rid]
        if body.variant and u.role not in A["test_mock_reply"] and request.state.real_role not in A["test_mock_reply"]:
            raise PermissionError("모의 회신은 시연 진행자만 입력합니다")
        if not body.variant and u.role is not req.to_role and u.role is not Role.SALES:
            raise PermissionError(f"회신 입력은 문의 담당({req.to_role.value}) 또는 영업만")
        if body.variant:  # 모의 회신(테스트 도구)
            _test_tools_on()
            o, terms, text = mock_reply(topic, body.variant)
            outcome, prov = ReplyOutcome(o), Provenance.SIMULATED
        elif body.outcome:
            outcome, terms, text, prov = body.outcome, body.terms, body.text, Provenance.HUMAN_INPUT
        else:
            raise HTTPException(422, "variant 또는 outcome 필요")
        return _run(lambda s, w: s.receive(w, rid, outcome, terms, text, u.username, u.role, provenance=prov))

    @app.post("/api/requests/{rid}/reask")
    def reask(rid: str, body: ReaskIn, u: User = Depends(roles(*A["send"]))):
        return _run(lambda s, w: s.reask(w, rid, body.answer_by, u.username, u.role))

    @app.post("/api/levers/{lid}/accept-counter")
    def accept_counter(lid: str, body: CounterIn, u: User = Depends(roles(*A["accept_counter"]))):
        return _run(lambda s, w: s.accept_counter(w, lid, u.username, u.role, body.breakeven_multiplier))

    @app.post("/api/clock")
    def clock(body: ClockIn, u: User = Depends(presenter("test_clock"))):
        _test_tools_on()
        return _run(lambda s, w: s.advance_clock(w, body.today, u.username))

    @app.post("/api/submit")
    def submit(body: SubmitIn, u: User = Depends(roles(*A["submit"]))):
        return _run(lambda s, w: s.submit(w, body.scenario, u.username, u.role, accept_loss=body.accept_loss))

    @app.post("/api/approvals/{aid}/decide")
    def decide(aid: str, body: DecideIn, u: User = Depends(roles(*A["decide"]))):
        return _run(lambda s, w: s.decide(w, aid, u.role, body.approve, u.username, body.note))

    @app.post("/api/promote")
    def promote(u: User = Depends(roles(*A["promote"]))):
        return _run(lambda s, w: s.promote_plan(w, u.username, u.role))

    @app.post("/api/conditions")
    def conditions(body: ConditionsIn, u: User = Depends(roles(*A["cond_fx"], *A["cond_recall"]))):
        changes = {k: v for k, v in body.model_dump().items() if v is not None}
        if "recall_replacement_week" in changes and u.role not in A["cond_recall"]:
            raise PermissionError("회수 의결 입력은 품질(품질위원회 결과 기록)만")
        if ({"fx_label", "price_pressure_pct"} & set(changes)) and u.role not in A["cond_fx"]:
            raise PermissionError("환율·단가 조건은 재무·영업임원·CEO 만")
        if not changes:
            raise HTTPException(422, "변경 항목 없음")
        return _run(lambda s, w: s.change_conditions(w, u.username, u.role, **changes))

    @app.post("/api/registry/{key}")
    def set_param(key: str, body: ParamIn, u: User = Depends(roles(*A["registry"]))):
        return _run(lambda s, w: s.set_parameter(w, key, body.value, u.username, u.role, body.reason))

    @app.post("/api/policy/{action}")
    def set_policy(action: str, body: PolicyIn, u: User = Depends(roles(*A["policy"]))):
        return _run(lambda s, w: s.set_policy_rule(w, action, body.approver, u.username, u.role, body.reason))

    # ── W05 입찰 건(약속 가능 물량 원장) ─────────────────────────────────────────────
    def _ledger(ctx: Ctx):
        ctx.ensure_workspace()
        ws = ctx.repo.load(ctx.ws_id())
        return ws, (BidLedger.model_validate(ws.bidflow) if ws.bidflow else None)

    def _w05(request: Request, u: User, action: str, fn, obj: str = "bid"):
        ctx = C()
        ws, L = _ledger(ctx)
        flow = W05Flow(ctx.w05_inputs, ws.policy)
        n0 = len(L.events) if L else 0
        with ctx.solving():
            res = fn(flow, L, ws)
        L = res if isinstance(res, BidLedger) else L
        if L is None:
            return {"ok": True, "version": ws.version, "status": None}
        trig = [e for e in L.events[n0:] if e.event_type in AGENT_TRIGGERS and not (e.event_type == "APPROVAL_DECIDED" and (e.after or {}).get("status") == "APPROVED")]
        if trig:  # 사건마다 에이전트 1회(제안만 남김). 실패해도 업무는 계속(G11)
            try:
                run_agent(L, ctx.w05_inputs, AGENT_TRIGGERS[trig[-1].event_type], trig[-1].id)
            except Exception as ex:  # noqa: BLE001 — AI 장애가 업무 처리를 막지 않게
                L.agent_runs.append(AgentRun(id=L.nid("agent_run"), trigger_event_id=trig[-1].id, trigger=AGENT_TRIGGERS[trig[-1].event_type], mode="live",
                                             status="FAILED", errors=[f"AI 호출 실패: {type(ex).__name__}"]))
        acting = request.state.acting
        if acting:
            for e in L.events[n0:]:
                if e.actor_type == "USER":
                    e.acting_as = f"{request.state.real_role.value} → {acting.value}"
        ws2 = ws.model_copy(update={"bidflow": L.model_dump(mode="json")})
        saved = ctx.svc._commit(ws2, ws.version, u.username, request.state.real_role, f"w05:{action}", obj, acting_as=acting)
        ctx.cache.clear()
        return {"ok": True, "version": saved.version, "status": L.status}

    @app.get("/api/w05")
    def w05_view(u: User = Depends(current_user)):
        ctx = C()
        ws, L = _ledger(ctx)
        return {**build_view(L, u.role.value, ws.today, ctx.w05_inputs), "today": ws.today, "version": ws.version}

    @app.get("/api/w05/board")
    def w05_board(capex: str = "", u: User = Depends(current_user)):
        ids = tuple(x for x in capex.split(",") if x in ("OPT-01", "OPT-02"))
        _, L = _ledger(C())
        return board(L, C().w05_inputs, ids)

    @app.get("/api/w05/board/orders")
    def w05_board_orders(line: str, week: str, u: User = Depends(current_user)):
        if line not in ("L-01", "L-02") or len(week) != 10:
            raise HTTPException(422, "라인·주 확인")
        rows = week_orders(C().w05_inputs, line, week)
        return [{k: v for k, v in r.items() if k != "_row"} | {"source_row": r["_row"]} for r in rows]

    @app.get("/api/w05/metric/{mid}")
    def w05_metric(mid: int, u: User = Depends(current_user)):
        _, L = _ledger(C())
        if L is None:
            raise HTTPException(404, "입찰 건 없음")
        try:
            return lineage(L, mid, u.role.value, C().w05_inputs)
        except KeyError:
            raise HTTPException(404, "지표 없음")








    def _need_bid(L):
        if L is None:
            raise FlowError("입찰 건이 없습니다. 영업이 RFP를 먼저 접수하세요")
        return L

    @app.post("/api/w05/receive")
    def w05_receive(request: Request, u: User = Depends(roles(*A["w05_receive"]))):
        def fn(f, L, ws):
            if L is not None and L.status not in ("ON_HOLD",):
                raise FlowError("이미 접수된 입찰 건이 있습니다")
            return f.receive_rfp(ws.today, u.username, u.role)
        return _w05(request, u, "receive", fn)

    @app.post("/api/w05/requests/{rid}/send")
    def w05_send(rid: int, request: Request, u: User = Depends(roles(*A["w05_send"]))):
        return _w05(request, u, "send", lambda f, L, ws: f.send_request(_need_bid(L), rid, ws.today, u.username, u.role), f"IR-{rid}")

    @app.post("/api/w05/requests/{rid}/edit")
    def w05_edit(rid: int, body: W05Edit, request: Request, u: User = Depends(roles(*A["w05_send"]))):
        return _w05(request, u, "edit", lambda f, L, ws: f.edit_request(_need_bid(L), rid, body.question, ws.today, u.username, u.role), f"IR-{rid}")

    @app.post("/api/w05/requests/{rid}/answer")
    def w05_answer(rid: int, body: W05Answer, request: Request, u: User = Depends(roles(*A["w05_answer"]))):
        def fn(f, L, ws):
            r = next((x for x in _need_bid(L).requests if x.id == rid), None)
            role = as_presenter(request, u) if r is not None and r.to_external else u.role  # 외부 모의 회신은 진행자 자격으로
            f.answer_request(L, rid, body.answer, body.text, ws.today, u.username, role)
        return _w05(request, u, "answer", fn, f"IR-{rid}")

    @app.post("/api/w05/requests/{rid}/confirm")
    def w05_confirm(rid: int, request: Request, u: User = Depends(roles(*A["w05_confirm"]))):
        return _w05(request, u, "confirm", lambda f, L, ws: f.confirm_from_request(_need_bid(L), rid, ws.today, u.username, u.role), f"IR-{rid}")

    @app.post("/api/w05/assumptions/{key}/supersede")
    def w05_supersede(key: str, body: W05Supersede, request: Request, u: User = Depends(roles(*A["w05_supersede"]))):
        def fn(f, L, ws):
            f.supersede(_need_bid(L), key, body.value, body.reason, ws.today, u.username, u.role)
            if body.notice_id:
                f.close_notice(L, body.notice_id)
        return _w05(request, u, "supersede", fn, key)

    @app.post("/api/w05/notice")
    def w05_notice(body: W05Notice, request: Request, u: User = Depends(presenter("test_mock_reply"))):
        """외부 추가 통지(모의). 시연 진행자만 — 흐름이 역할을 다시 검사한다."""
        _test_tools_on()
        return _w05(request, u, "notice", lambda f, L, ws: f.external_notice(_need_bid(L), body.party, body.text, body.key, body.value, ws.today, u.username, as_presenter(request, u)))

    @app.post("/api/w05/submit")
    def w05_submit(body: W05Submit, request: Request, u: User = Depends(roles(*A["w05_submit"]))):
        return _w05(request, u, "submit", lambda f, L, ws: f.submit(_need_bid(L), body.option_id, ws.today, u.username, u.role), f"OPT-{body.option_id}")

    @app.post("/api/w05/approvals/{aid}/decide")
    def w05_decide(aid: int, body: W05Decide, request: Request, u: User = Depends(current_user)):
        return _w05(request, u, "decide", lambda f, L, ws: f.decide(_need_bid(L), aid, body.decision, body.text, ws.today, u.username, u.role), f"AP-{aid}")

    @app.post("/api/w05/tasks/{tid}/complete")
    def w05_task(tid: int, body: W05Task, request: Request, u: User = Depends(roles(*A["w05_task"]))):
        return _w05(request, u, "task", lambda f, L, ws: f.complete_task(_need_bid(L), tid, ws.today, u.username, u.role, body.acknowledge_conditions), f"TK-{tid}")

    @app.post("/api/w05/submit-bid")
    def w05_submit_bid(request: Request, u: User = Depends(roles(*A["w05_submit_bid"]))):
        return _w05(request, u, "submit_bid", lambda f, L, ws: f.submit_bid(_need_bid(L), ws.today, u.username, u.role))

    @app.post("/api/w05/options/{oid}/note")
    def w05_note(oid: int, body: W05Note, request: Request, u: User = Depends(roles(*A["w05_note"]))):
        return _w05(request, u, "note", lambda f, L, ws: f.add_note(_need_bid(L), oid, body.text, ws.today, u.username, u.role), f"OPT-{oid}")

    @app.post("/api/w05/flags/{fid}/ack")
    def w05_ack(fid: int, request: Request, u: User = Depends(roles(*A["w05_ack"]))):
        return _w05(request, u, "ack", lambda f, L, ws: f.acknowledge_flag(_need_bid(L), fid, ws.today, u.username, u.role), f"RF-{fid}")

    @app.post("/api/w05/clock")
    def w05_clock(body: ClockIn, request: Request, u: User = Depends(presenter("test_clock"))):
        """가상 시계 이동: 업무 시계 + 마감 처리(DEADLINE_TICK·마감 경과 시 참여 보류)."""
        _test_tools_on()
        ctx = C()
        ctx.ensure_workspace()
        with ctx.solving():
            ctx.svc.advance_clock(ctx.ws_id(), body.today, u.username)
        return _w05(request, u, "clock", lambda f, L, ws: f.tick(L, body.today, u.username) if L else None)

    def _test_tools_on() -> None:
        if not C().test_tools:
            raise HTTPException(404, "테스트 도구가 꺼져 있습니다")

    @app.post("/api/test/prepare")
    def test_prepare(body: PrepareIn, request: Request, u: User = Depends(presenter("test_reset"))):
        """녹화용 시작 상태: 새 회차를 열고 실제 흐름 함수로 시나리오 시작 지점까지 진행(삭제 없음, 이력에 '시연 준비')."""
        _test_tools_on()
        from dealcockpit.w05.scenarios import prepare

        ctx = C()
        new = ctx.reset(u.username, request.state.real_role)
        ws = ctx.repo.load(new)
        L, today = prepare(W05Flow(ctx.w05_inputs, ws.policy), body.scenario)
        with ctx.solving():
            ctx.svc.advance_clock(new, today, u.username)
        ws = ctx.repo.load(new)
        ctx.svc._commit(ws.model_copy(update={"bidflow": L.model_dump(mode="json")}), ws.version, u.username, request.state.real_role, f"w05:prepare:{body.scenario}", "bid")
        ctx.cache.clear()
        return {"ok": True, "workspace": new, "today": today, "status": L.status}

    @app.post("/api/test/reset")
    def test_reset(request: Request, u: User = Depends(presenter("test_reset"))):
        _test_tools_on()
        return {"ok": True, "workspace": C().reset(u.username, request.state.real_role)}

    return app


app = create_app()


def main() -> None:  # pragma: no cover
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", "8080")))


if __name__ == "__main__":  # pragma: no cover
    main()
