"""API(S5): 인증·CSRF·보안 헤더·RBAC·경로 조작 방지·what-if 무상태·전체 흐름."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from dealcockpit.adapters.api import Ctx, create_app
from dealcockpit.adapters.auth import MemoryAuthStore, hash_password
from dealcockpit.adapters.repo_memory import MemoryRepository
from dealcockpit.adapters.solver_lp import LpSolver
from dealcockpit.domain.common import Role

PW = "test-pass-123"
USERS = {"sales": Role.SALES, "ceo": Role.CEO_BOARD, "production": Role.PRODUCTION, "quality": Role.QUALITY, "reviewer": Role.REVIEWER,
         "policy_admin": Role.POLICY_ADMIN, "finance": Role.FINANCE, "procurement": Role.PROCUREMENT, "sales_exec": Role.SALES_EXEC, "demo": Role.DEMO_ADMIN}
H = {"X-Capact-CSRF": "1"}


@pytest.fixture(scope="module")
def ctx():
    auth = MemoryAuthStore()
    h = hash_password(PW)
    for u, r in USERS.items():
        auth.upsert_user(u, r, h)
    auth.upsert_user("lock_target", Role.REVIEWER, h)  # 로그인 잠금 시험 전용(다른 시험 계정을 잠그지 않게)
    return Ctx(MemoryRepository(), auth, LpSolver("SCIP"), cookie_secure=False)


@pytest.fixture(scope="module")
def app(ctx):
    return create_app(ctx)


def client(app, user=None):
    c = TestClient(app)
    if user:
        r = c.post("/api/login", json={"username": user, "password": PW})
        assert r.status_code == 200, r.text
    return c


def test_healthz_is_public_and_cheap(app):
    r = TestClient(app).get("/healthz")
    assert r.status_code == 200 and r.json()["status"] == "ok"


def test_index_serves_html_and_security_headers(app):
    r = TestClient(app).get("/")
    assert r.status_code == 200 and 'id="login"' in r.text and "/static/fonts/PretendardVariable.woff2" in r.text
    assert "frame-ancestors 'none'" in r.headers["content-security-policy"] and "script-src 'self'" in r.headers["content-security-policy"]
    assert r.headers["x-frame-options"] == "DENY" and r.headers["x-content-type-options"] == "nosniff"


def test_public_api_docs_and_probe_are_disabled(app):
    c = TestClient(app)
    assert c.get("/docs").status_code == 404 and c.get("/openapi.json").status_code == 404
    assert c.get("/_probe/solver").status_code == 404


def test_api_requires_login(app):
    c = TestClient(app)
    assert c.get("/api/checkpoints").status_code == 401
    assert c.get("/api/audit").status_code == 401


def test_login_cookie_flags_and_wrong_password_throttle(app):
    c = TestClient(app)
    r = c.post("/api/login", json={"username": "sales", "password": PW})
    cookie = r.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=strict" in cookie
    bad = TestClient(app)
    codes = [bad.post("/api/login", json={"username": "lock_target", "password": "nope"}).status_code for _ in range(6)]
    assert codes[:5] == [401] * 5 and codes[5] == 429  # 5회 실패 후 잠금
    assert bad.post("/api/login", json={"username": "lock_target", "password": PW}).status_code == 429  # 잠금 중엔 맞는 비밀번호도 거부


def test_unknown_fields_rejected(app):
    c = TestClient(app)
    assert c.post("/api/login", json={"username": "sales", "password": PW, "role": "CEO/이사회"}).status_code == 422


def test_csrf_header_required_for_writes(app):
    c = client(app, "sales")
    assert c.post("/api/inquiries/plan").status_code == 403  # 헤더 없음
    assert c.post("/api/inquiries/plan", headers={"X-Capact-CSRF": "0"}).status_code == 403


def test_rbac_reviewer_reads_but_cannot_write(app):
    c = client(app, "reviewer")
    assert c.get("/api/checkpoints").status_code == 200
    assert c.post("/api/inquiries/plan", headers=H).status_code == 403
    assert c.post("/api/submit", json={"scenario": "A"}, headers=H).status_code == 403
    assert c.post("/api/policy/outsourcing_contract", json={"approver": "영업임원", "reason": "테스트 사유"}, headers=H).status_code == 403


def test_contract_path_traversal_blocked(app):
    c = client(app, "reviewer")
    assert c.get("/api/contracts/CT-C101").status_code == 200
    assert c.get("/api/contracts/..%2F..%2Fdocs%2FTEST_ACCOUNTS").status_code == 404
    assert c.get("/api/contracts/CT-ADV-01").status_code == 404  # 평가용 문서는 노출 안 함


def test_what_if_does_not_change_state(app):
    c = client(app, "reviewer")
    v0 = c.get("/api/checkpoints").json()["version"]
    r = c.post("/api/simulate", json={"what_if": {"fx_label": "downside", "price_pressure_pct": -10}}, headers=H)
    assert r.status_code == 200 and r.json()["cp6"]["what_if"]["fx_label"] == "downside"
    assert c.get("/api/checkpoints").json()["version"] == v0
    assert c.post("/api/simulate", json={"what_if": {"bid_ramp_weeks": 0}}, headers=H).status_code == 422  # 범위 밖


def test_end_to_end_flow_through_api(app):
    sales, ceo, prod, demo = client(app, "sales"), client(app, "ceo"), client(app, "production"), client(app, "demo")
    assert sales.post("/api/inquiries/plan", headers=H).status_code == 200
    cp = sales.get("/api/checkpoints").json()
    reqs = {r["topic"]: r["id"] for r in cp["cp7"]["requests"]}
    for rid in reqs.values():
        assert sales.post(f"/api/requests/{rid}/send", headers=H).status_code == 200
    plan = {"AS-001_capacity_unit": "confirm_month", "OPT-02_formula_revalidation": "slow_conditional", "MB_recontract": "counter_moq",
            "C201_partial_award": "partial_ok", "LV_outsourcing_PL-02": "accept", "C101_discount_for_delay": "accept"}
    for topic, variant in plan.items():
        assert sales.post(f"/api/requests/{reqs[topic]}/reply", json={"variant": variant}, headers=H).status_code == 403  # 모의 회신은 시연 진행자만
        assert demo.post(f"/api/requests/{reqs[topic]}/reply", json={"variant": variant}, headers=H).status_code == 200, topic
    r = prod.post(f"/api/requests/{reqs['INTERNAL_OVERTIME']}/reply", json={"outcome": "ACCEPT", "terms": {"qty_per_month": 30000}, "text": "가능"}, headers=H)
    assert r.status_code == 200
    r = sales.post("/api/submit", json={"scenario": "A"}, headers=H)
    assert r.status_code == 200 and r.json()["result"]["submitted"] is True
    cp = sales.get("/api/checkpoints").json()
    pending = [a for a in cp["cp7"]["approvals"] if a["status"] == "PENDING"]
    assert sales.post(f"/api/approvals/{pending[0]['approval_id']}/decide", json={"approve": True}, headers=H).status_code == 403  # 영업은 승인 주체 아님
    for a in pending:
        who = {"CEO/이사회": ceo, "생산": prod}[a["approver"]]
        assert who.post(f"/api/approvals/{a['approval_id']}/decide", json={"approve": True}, headers=H).status_code == 200
    assert sales.post("/api/promote", headers=H).status_code == 200
    cp = sales.get("/api/checkpoints").json()
    assert cp["cp7"]["plan"]["version"] == 1 and not cp["cp7"]["plan_stale"]
    assert sales.get("/api/audit").status_code == 403  # 감사 기록은 관리 계정만
    audit = ceo.get("/api/audit").json()
    assert any(e["action"].startswith("promote") for e in audit) and all("password" not in str(e) for e in audit)


def test_registry_and_policy_changes_are_role_bound(app):
    q, s, pa = client(app, "quality"), client(app, "sales"), client(app, "policy_admin")
    assert s.post("/api/registry/recall_replacement_ratio", json={"value": 0.6, "reason": "시중 재고 일부 폐기"}, headers=H).status_code == 403
    assert q.post("/api/registry/recall_replacement_ratio", json={"value": 0.6, "reason": "시중 재고 일부 폐기"}, headers=H).status_code == 200
    assert q.post("/api/registry/recall_replacement_ratio", json={"value": 3, "reason": "범위 밖 값 시험"}, headers=H).status_code == 422
    assert pa.post("/api/policy/outsourcing_contract", json={"approver": "영업임원", "reason": "위임 결정 반영"}, headers=H).status_code == 200
    assert pa.post("/api/policy/outsourcing_contract", json={"approver": "정책관리자", "reason": "자기 자신 지정 시도"}, headers=H).status_code in (403, 422)
    cp = s.get("/api/checkpoints").json()
    assert next(r for r in cp["cp7"]["policy"]["rules"] if r["action"] == "outsourcing_contract")["approver"] == "영업임원"


def test_conditions_role_rules(app):
    f, q = client(app, "finance"), client(app, "quality")
    assert f.post("/api/conditions", json={"recall_replacement_week": 3}, headers=H).status_code == 403  # 회수는 품질만
    assert q.post("/api/conditions", json={"fx_label": "downside"}, headers=H).status_code == 403  # 환율은 재무 등
    assert f.post("/api/conditions", json={"fx_label": "bogus"}, headers=H).status_code == 422


# ── S6: 권한 표시·대시보드·거래처 이력·테스트 도구 분리 ─────────────────────────────
def test_me_exposes_capabilities_from_the_same_access_table(app):
    from dealcockpit.adapters.api import ACCESS

    for user, role in USERS.items():
        me = client(app, user).get("/api/me").json()
        assert me["role"] == role.value
        assert set(me["can"]) == {k for k, v in ACCESS.items() if role in v}
    assert [c for c in client(app, "reviewer").get("/api/me").json()["can"] if not c.endswith(("_view", "_view_all"))] == []  # 심사자: 열람만
    assert "policy" in client(app, "policy_admin").get("/api/me").json()["can"]
    assert "submit" not in client(app, "production").get("/api/me").json()["can"]


def test_simulate_accepts_price_cut_off_the_rfp_grid(app):
    # 회귀: 격자(0/-5/-10) 밖 인하율이 StopIteration → 500 이었다(배포 후 발견)
    r = client(app, "reviewer").post("/api/simulate", json={"what_if": {"price_pressure_pct": -7}}, headers=H)
    assert r.status_code == 200 and r.json()["cp5"]["current"]["price_pressure_pct"] == -7


def test_overview_and_partner_history(app):
    c = client(app, "reviewer")
    ov = c.get("/api/overview").json()
    assert ov["production_monthly"][-1]["month"] == "2026-06" and len(ov["lines"]) == 6
    items = c.get("/api/partners").json()["items"]
    assert {i["kind"] for i in items} == {"고객", "대리점"}
    assert sum(i["backlog"] for i in items) == 1216 and sum(i["stale"] for i in items) == 598  # DER-AS002 와 대사
    d = c.get("/api/partners/C-101").json()
    assert d["contract_doc"] == "CT-C101" and d["clauses"] and d["recent_orders"] and d["monthly"]
    ag = c.get("/api/partners/D-001").json()
    assert not ({"representative_name", "business_reg_no", "bank_account_no", "address"} & set(ag))
    assert c.get("/api/partners/..%2Fx").status_code == 404 and c.get("/api/partners/C-999").status_code == 404


def test_test_tools_can_be_switched_off(monkeypatch):
    monkeypatch.setenv("CAPACT_TEST_TOOLS", "0")
    auth = MemoryAuthStore()
    auth.upsert_user("sales", Role.SALES, hash_password(PW))
    auth.upsert_user("demo", Role.DEMO_ADMIN, hash_password(PW))
    ap = create_app(Ctx(MemoryRepository(), auth, LpSolver("SCIP"), cookie_secure=False))
    s, d = client(ap, "sales"), client(ap, "demo")
    assert s.get("/api/me").json()["test_tools"] is False and d.get("/api/me").json()["test_tools"] is False
    assert d.post("/api/clock", json={"today": "2026-07-05"}, headers=H).status_code == 404
    s.post("/api/inquiries/plan", headers=H)
    rid = s.get("/api/checkpoints").json()["cp7"]["requests"][0]["id"]
    s.post(f"/api/requests/{rid}/send", headers=H)
    assert d.post(f"/api/requests/{rid}/reply", json={"variant": "confirm_month"}, headers=H).status_code == 404  # 모의 회신 차단
    assert s.post(f"/api/requests/{rid}/reply", json={"outcome": "ACCEPT", "terms": {"period": "per_month"}, "text": "확인"}, headers=H).status_code == 200


def test_admin_information_is_role_limited(app):
    s, pa, rv = client(app, "sales"), client(app, "policy_admin"), client(app, "reviewer")
    mine = s.get("/api/access").json()
    assert mine["roles"] == ["영업"] and all(v == ["영업"] for v in mine["access"].values())  # 자기 권한만
    full = pa.get("/api/access").json()
    assert len(full["roles"]) > 5 and "test_reset" in full["access"]
    assert rv.get("/api/audit").status_code == 200 and s.get("/api/audit").status_code == 403
    assert s.get("/api/me").json()["test_tools"] is False  # 운영 계정에는 시연 도구 없음
    assert client(app, "demo").get("/api/me").json()["test_tools"] is True


def test_reset_opens_new_round_and_keeps_old_audit(app):  # 상태를 바꾸므로 이 파일 마지막
    assert client(app, "sales").post("/api/test/reset", headers=H).status_code == 403
    assert client(app, "sales_exec").post("/api/test/reset", headers=H).status_code == 403
    sx = client(app, "demo")
    old = sx.get("/api/me").json()["workspace"]
    old_audit = len(sx.get("/api/audit").json())
    r = sx.post("/api/test/reset", headers=H)
    assert r.status_code == 200 and r.json()["workspace"] != old
    cp = sx.get("/api/checkpoints").json()
    assert cp["workspace"] == r.json()["workspace"] and cp["cp7"]["requests"] == [] and cp["cp7"]["plan"] is None
    assert old_audit > 0  # 이전 회차 기록은 삭제하지 않는다(새 회차는 open 1건부터)
    assert [e["action"] for e in sx.get("/api/audit").json()] == ["open"]
