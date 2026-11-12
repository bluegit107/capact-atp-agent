"""W05 API: 역할별 열람 제한(G16·11.3), 근거 드로어, 시연 진행자 역할 전환, 권한(G1)·G5 를 HTTP 로."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from dealcockpit.adapters.api import Ctx, create_app
from dealcockpit.adapters.auth import MemoryAuthStore, hash_password
from dealcockpit.adapters.repo_memory import MemoryRepository
from dealcockpit.adapters.solver_lp import LpSolver
from dealcockpit.domain.common import Role

PW = "test-pass-123"
H = {"X-Capact-CSRF": "1"}
USERS = {"sales": Role.SALES, "ceo": Role.CEO_BOARD, "production": Role.PRODUCTION, "procurement": Role.PROCUREMENT, "finance": Role.FINANCE,
         "demo": Role.DEMO_ADMIN, "reviewer": Role.REVIEWER, "sales_exec": Role.SALES_EXEC}


@pytest.fixture(scope="module")
def app():
    auth = MemoryAuthStore()
    h = hash_password(PW)
    for u, r in USERS.items():
        auth.upsert_user(u, r, h)
    return create_app(Ctx(MemoryRepository(), auth, LpSolver("SCIP"), cookie_secure=False))


def cli(app, user):
    c = TestClient(app)
    assert c.post("/api/login", json={"username": user, "password": PW}).status_code == 200
    return c


def test_s1_over_http_with_role_visibility(app):
    s, demo, ceo, prod, fin = (cli(app, u) for u in ("sales", "demo", "ceo", "production", "finance"))
    assert s.get("/api/w05").json()["bid"] is None
    assert prod.post("/api/w05/receive", headers=H).status_code == 403
    assert s.post("/api/w05/receive", headers=H).status_code == 200
    v = s.get("/api/w05").json()
    assert v["bid"]["status"] == "WAITING_INFO" and v["bid"]["days_left"] == 21
    assert s.post("/api/w05/submit", json={"option_id": None}, headers=H).status_code == 409  # G5
    buyer = next(r for r in v["requests"] if r["to_external"] == "C-201_BUYER")
    assert s.post(f"/api/w05/requests/{buyer['id']}/send", headers=H).status_code == 200
    assert s.post(f"/api/w05/requests/{buyer['id']}/answer", json={"answer": {"supply_start_date": "2027-04-01"}}, headers=H).status_code == 403  # 외부 회신은 진행자만
    r = demo.post(f"/api/w05/requests/{buyer['id']}/answer", json={"answer": {"supply_start_date": "2027-04-01", "partial_award_allowed": True}, "text": "회신"}, headers=H)
    assert r.status_code == 200
    assert s.post(f"/api/w05/requests/{buyer['id']}/confirm", headers=H).status_code == 200
    v = s.get("/api/w05").json()
    b = next(o for o in v["options"] if o["label"] == "B" and o["status"] == "PROPOSED")
    assert b["feasibility"] == "FEASIBLE"
    # 영업: 매출은 보이고 재료비 차감 이익은 응답에서 빠진다 / 생산: 매출·손익·단가 모두 빠진다 / 재무: 다 보인다
    assert b["metrics"]["revenue"]["value"] > 0 and b["metrics"]["margin"]["value"] is None and b["metrics"]["margin"]["hidden"]
    vp = prod.get("/api/w05").json()
    bp = next(o for o in vp["options"] if o["id"] == b["id"])
    assert bp["metrics"]["revenue"]["value"] is None and all(l["unit_price_usd"] is None for l in vp["lines"])
    assert not any(f["rule_key"].startswith("RF-FIN") for f in vp["flags"])  # G16
    vf = fin.get("/api/w05").json()
    assert next(o for o in vf["options"] if o["id"] == b["id"])["metrics"]["margin"]["value"] > 0
    assert any(f["rule_key"] == "RF-FIN-01" for f in vf["flags"])
    # 근거 드로어 3단
    fid = b["metrics"]["fill_PL-02"]["id"]
    d = s.get(f"/api/w05/metric/{fid}").json()
    assert d["formula_key"] == "fill_rate_52w" and any(i["kind"] == "assumption" and i["key"] == "supply_start_date" and i["source_type"] == "MOCK_REPLY" for i in d["inputs"])
    full = next(i for i in d["inputs"] if i["kind"] == "metric" and i["key"] == "full_capacity")
    d2 = s.get(f"/api/w05/metric/{full['id']}").json()
    util = next(i for i in d2["inputs"] if i["key"] == "utilization_pct")
    d3 = s.get(f"/api/w05/metric/{util['id']}").json()
    assert d3["sources"][0]["kind"] == "single" and d3["sources"][0]["row_values"][0]["line_id"] == "L-02"  # 세 번의 클릭으로 원본 행
    mid = b["metrics"]["margin"]["id"]
    assert prod.get(f"/api/w05/metric/{mid}").json()["metric"]["value"] is None
    # 상신 → 결정은 권한자만(G1)
    assert prod.post("/api/w05/submit", json={"option_id": b["id"]}, headers=H).status_code == 403
    assert s.post("/api/w05/submit", json={"option_id": b["id"]}, headers=H).status_code == 200
    ap = [a for a in s.get("/api/w05").json()["approvals"] if a["status"] == "PENDING"]
    assert s.post(f"/api/w05/approvals/{ap[0]['id']}/decide", json={"decision": "APPROVED"}, headers=H).status_code == 403
    for a in ap:
        assert ceo.post(f"/api/w05/approvals/{a['id']}/decide", json={"decision": "APPROVED"}, headers=H).status_code == 200
    v = s.get("/api/w05").json()
    assert v["bid"]["status"] == "APPROVED" and len(v["reservations"]) == 2
    bd = s.get("/api/w05/board?capex=OPT-01").json()
    assert bd["line_util"]["L-02"] == 97.0 and any(r["reserved_qty"] > 0 for r in bd["lines"]["L-02"])


def test_demo_presenter_can_act_as_and_is_recorded(app):
    demo, sales = cli(app, "demo"), cli(app, "sales")
    assert sales.post("/api/demo/act-as", json={"role": "CEO/이사회"}, headers=H).status_code == 403
    assert demo.post("/api/demo/act-as", json={"role": "정책관리자"}, headers=H).status_code == 403
    assert demo.post("/api/demo/act-as", json={"role": "생산"}, headers=H).json()["role"] == "생산"
    me = demo.get("/api/me").json()
    assert me["role"] == "생산" and me["real_role"] == "시연 진행자" and me["acting"] and "demo_act_as" in me["can"]
    v = demo.get("/api/w05").json()
    task = next(t for t in v["tasks"] if t["type"] == "CAPACITY_RESERVATION" and t["status"] == "OPEN")
    assert demo.post(f"/api/w05/tasks/{task['id']}/complete", json={}, headers=H).status_code == 200
    v = demo.get("/api/w05").json()
    ev = next(e for e in reversed(v["events"]) if e["event_type"] == "TASK_DONE")
    assert ev["actor_id"] == "demo" and ev["acting_as"] == "시연 진행자 → 생산"
    assert demo.get("/api/audit").status_code == 403  # 생산으로 대리 중에는 생산 권한(감사 기록 열람 불가)
    assert demo.post("/api/demo/act-as", json={"role": None}, headers=H).json()["role"] == "시연 진행자"
    audit = demo.get("/api/audit").json()
    assert audit[-1]["acting_as_role"] == "생산" and audit[-1]["actor_role"] == "시연 진행자"


def test_metric_and_board_inputs_are_validated(app):
    s = cli(app, "sales")
    assert s.get("/api/w05/metric/999999").status_code == 404
    assert s.get("/api/w05/board/orders?line=L-09&week=2026-07-06").status_code == 422
    rows = s.get("/api/w05/board/orders?line=L-02&week=2026-07-06").json()
    assert sum(r["qty"] for r in rows) == 135745 and all("source_row" in r for r in rows)


def test_integrations_status_is_admin_only_and_hides_secrets(app, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-should-not-leak")
    assert cli(app, "sales").get("/api/integrations").status_code == 403
    r = cli(app, "reviewer").get("/api/integrations")
    assert r.status_code == 200 and "sk-test" not in r.text
    llm = next(i for i in r.json()["items"] if i["id"] == "llm")
    assert llm["detail"]["api_key_present"] is True and llm["mode"] == "mock" and "finish" in llm["detail"]["tools"]


def test_demo_prepare_builds_scenario_start_states(app):
    d = cli(app, "demo")
    assert cli(app, "sales").post("/api/test/prepare", json={"scenario": "s2"}, headers=H).status_code == 403
    r = d.post("/api/test/prepare", json={"scenario": "s2"}, headers=H)
    assert r.status_code == 200 and r.json()["status"] == "APPROVED" and r.json()["today"] == "2026-07-07"
    v = d.get("/api/w05").json()
    assert v["today"] == "2026-07-07" and all(x["status"] == "CONFIRMED" for x in v["reservations"])
    r = d.post("/api/test/prepare", json={"scenario": "s3b"}, headers=H)
    assert r.json()["status"] == "PENDING_APPROVAL" and len([a for a in d.get("/api/w05").json()["approvals"] if a["status"] == "PENDING"]) == 2


def test_presenter_tools_work_while_acting_as_another_role(app):
    """회귀: 시연 진행자가 '영업'으로 대리 중일 때 초기화·준비·시계·모의 입력이 403 이던 문제."""
    d = cli(app, "demo")
    assert d.post("/api/demo/act-as", json={"role": "영업"}, headers=H).status_code == 200
    me = d.get("/api/me").json()
    assert me["role"] == "영업" and {"test_reset", "test_clock", "test_mock_reply"} <= set(me["can"])
    assert d.post("/api/test/prepare", json={"scenario": "s2"}, headers=H).status_code == 200
    assert d.post("/api/w05/clock", json={"today": "2026-07-08"}, headers=H).status_code == 200
    assert d.post("/api/w05/notice", json={"party": "C-201_BUYER", "text": "앞당김", "key": "supply_start_date", "value": "2026-10-01"}, headers=H).status_code == 200
    r = d.post("/api/test/reset", headers=H)
    assert r.status_code == 200
    v = d.get("/api/w05").json()
    assert v["bid"] is None and v["today"] == "2026-07-01"  # 새 회차는 빈 상태에서 다시 시작
    assert d.post("/api/w05/receive", headers=H).status_code == 200  # 대리 역할(영업)로 업무 진행
    b = next(x for x in d.get("/api/w05").json()["requests"] if x["to_external"])
    d.post(f"/api/w05/requests/{b['id']}/send", headers=H)
    assert d.post(f"/api/w05/requests/{b['id']}/answer", json={"answer": {"supply_start_date": "2027-04-01"}, "text": "x"}, headers=H).status_code == 200  # 외부 모의 회신
    d.post("/api/demo/act-as", json={"role": None}, headers=H)
    assert cli(app, "sales").post("/api/test/reset", headers=H).status_code == 403  # 운영 계정은 여전히 불가
