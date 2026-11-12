"""저장소 계약 테스트: 메모리·PostgreSQL 이 같은 계약(낙관적 락·추가 전용 감사)을 지키는가 + Postgres 보안 속성.

PostgreSQL 테스트는 CAPACT_TEST_PG_URL(관리자 접속 URL)이 있을 때만 돈다. 테스트마다 임시 DB 를 만들고 지운다.
"""
from __future__ import annotations

import base64
import os
import uuid
from datetime import datetime

import pytest

from dealcockpit.adapters.repo_memory import MemoryRepository
from dealcockpit.application.ports import ConcurrencyConflict
from dealcockpit.domain.common import Role
from dealcockpit.domain.lever import Lever, LeverType
from dealcockpit.domain.common import Provenance
from dealcockpit.domain.plan import AuditEvent
from dealcockpit.domain.workspace import Workspace

PG_ADMIN = os.environ.get("CAPACT_TEST_PG_URL")
KEY = base64.b64encode(b"k" * 32).decode()


def _pg_available() -> bool:
    if not PG_ADMIN:
        return False
    try:
        import psycopg

        with psycopg.connect(PG_ADMIN, connect_timeout=3):
            return True
    except Exception:
        return False


PG = _pg_available()


@pytest.fixture
def pg_url():
    if not PG:
        pytest.skip("CAPACT_TEST_PG_URL 없음/접속 불가 — PostgreSQL 테스트 건너뜀")
    import psycopg

    name = f"capact_test_{uuid.uuid4().hex[:8]}"
    with psycopg.connect(PG_ADMIN, autocommit=True) as c:
        c.execute(f'CREATE DATABASE "{name}"')
    url = PG_ADMIN.rsplit("/", 1)[0] + f"/{name}"
    yield url
    with psycopg.connect(PG_ADMIN, autocommit=True) as c:
        c.execute(f'DROP DATABASE "{name}" WITH (FORCE)')


def make_repo(kind: str, url: str | None):
    if kind == "memory":
        return MemoryRepository()
    from dealcockpit.adapters.repo_postgres import PostgresRepository

    return PostgresRepository(url, KEY, migrate=True)


def ws(**over) -> Workspace:
    lv = Lever(lever_id="LV-001", type=LeverType.TIME_SHIFT, params=dict(target="customer:C-101", shift_days=14), owner_role=Role.SALES,
               counterparty="C-101 비밀 협상", confidence=0.5, provenance=Provenance.SIMULATED)
    base = dict(workspace_id="WS-T1", as_of="2026-07-01", today="2026-07-01", levers={"LV-001": lv}, confirmed_monthly=1949480.0)
    base.update(over)
    return Workspace(**base)


def ev(n: int) -> AuditEvent:
    return AuditEvent(event_id=f"EV-{n:03d}", at=datetime(2026, 7, 1, 9, 0, n), actor="김", actor_role="영업", action="x", object_ref="WS-T1")


@pytest.fixture(params=["memory", "postgres"])
def repo(request):
    if request.param == "postgres":
        url = request.getfixturevalue("pg_url")
        return make_repo("postgres", url)
    return make_repo("memory", None)


# ── 공통 계약 ──────────────────────────────────────────────────────────────────
def test_round_trip_and_version_increment(repo):
    saved = repo.save(ws(), 0)
    assert saved.version == 1
    loaded = repo.load("WS-T1")
    assert loaded == saved and loaded.levers["LV-001"].params["shift_days"] == 14
    assert repo.load("WS-NONE") is None


def test_optimistic_lock_rejects_stale_writer(repo):
    v1 = repo.save(ws(), 0)
    repo.save(v1.model_copy(update={"today": "2026-07-02"}), 1)  # 사용자 A
    with pytest.raises(ConcurrencyConflict):
        repo.save(v1.model_copy(update={"today": "2026-07-03"}), 1)  # 사용자 B(낡은 버전 기반)
    with pytest.raises(ConcurrencyConflict):
        repo.save(ws(), 0)  # 이미 있는 ID 를 새로 만들 수 없다
    assert repo.load("WS-T1").today == "2026-07-02"


def test_audit_is_ordered(repo):
    for i in (1, 2, 3):
        repo.append_audit("WS-T1", ev(i))
    assert [e.event_id for e in repo.list_audit("WS-T1")] == ["EV-001", "EV-002", "EV-003"]


# ── PostgreSQL 보안·무결성 ──────────────────────────────────────────────────────
def test_pg_ciphertext_hides_sensitive_values(pg_url):
    import psycopg

    r = make_repo("postgres", pg_url)
    r.save(ws(), 0)
    with psycopg.connect(pg_url) as c:
        ct = bytes(c.execute("SELECT ciphertext FROM workspaces").fetchone()[0])
    assert b"C-101" not in ct and "비밀 협상".encode() not in ct and b"1949480" not in ct


def test_pg_wrong_key_or_swapped_rows_cannot_be_read(pg_url):
    import psycopg

    from dealcockpit.adapters.repo_postgres import EncryptionKeyMissing, PostgresRepository

    r = make_repo("postgres", pg_url)
    r.save(ws(), 0)
    r.save(ws(workspace_id="WS-T2"), 0)
    other = PostgresRepository(pg_url, base64.b64encode(b"z" * 32).decode())
    with pytest.raises(EncryptionKeyMissing):
        other.load("WS-T1")
    with psycopg.connect(pg_url) as c:  # 공격: 두 행의 암호문을 맞바꿈
        a = c.execute("SELECT nonce, ciphertext FROM workspaces WHERE workspace_id='WS-T1'").fetchone()
        b = c.execute("SELECT nonce, ciphertext FROM workspaces WHERE workspace_id='WS-T2'").fetchone()
        c.execute("UPDATE workspaces SET nonce=%s, ciphertext=%s WHERE workspace_id='WS-T1'", (b[0], b[1]))
        c.commit()
    with pytest.raises(Exception):
        r.load("WS-T1")  # AAD(행 ID·버전) 불일치 → 복호화 실패


def test_pg_audit_is_append_only(pg_url):
    import psycopg

    r = make_repo("postgres", pg_url)
    r.append_audit("WS-T1", ev(1))
    for sql in ("UPDATE audit_events SET actor='위조'", "DELETE FROM audit_events", "TRUNCATE audit_events"):
        with psycopg.connect(pg_url) as c, pytest.raises(psycopg.errors.RaiseException):
            c.execute(sql)
    assert [e.actor for e in r.list_audit("WS-T1")] == ["김"]


def test_pg_migrations_are_idempotent_and_key_is_required(pg_url):
    from dealcockpit.adapters.repo_postgres import EncryptionKeyMissing, PostgresRepository

    r = make_repo("postgres", pg_url)
    assert r.migrate(pg_url) == []  # 두 번째 실행은 할 일 없음
    with pytest.raises(EncryptionKeyMissing):
        PostgresRepository(pg_url, "")  # 키 없이 운영 저장 불가(빈 문자열 = 미설정)


def test_pg_full_negotiation_case_persists(pg_url):
    from dealcockpit.adapters.demo_cases import case_normal
    from dealcockpit.adapters.solver_lp import LpSolver

    r = make_repo("postgres", pg_url)
    run = case_normal(r, LpSolver("SCIP"), "WS-PG")
    w = r.load("WS-PG")
    assert w.plan is not None and w.plan.version == 1
    assert len(r.list_audit("WS-PG")) == w.counters["EV"]  # 상태 저장 횟수 = 감사 이벤트 수
    assert run.log[-1]["plan_version"] == 1
