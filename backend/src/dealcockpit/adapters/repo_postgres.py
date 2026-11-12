"""PostgreSQL 저장소(AXHub DB). 보안·무결성 설계(REVIEW §6):

- 업무 상태(L3 민감: 계약 조항·단가·고객별 위약)는 **앱 단 AES-GCM 암호화** 후 저장. 키는 환경변수(CAPACT_DATA_KEY, base64 32바이트)에만.
  AXHub DB 연결은 sslmode=disable(내부망)이므로 평문을 흘리지 않는다. AAD = workspace_id|version → 행 바꿔치기·재사용 탐지.
- 낙관적 락: UPDATE ... WHERE version = 기대값. 실패하면 ConcurrencyConflict.
- 감사: 추가 전용 테이블. UPDATE·DELETE·TRUNCATE 는 트리거가 거부. 감사에는 민감 값 대신 ID·해시만.
- 트랜잭션 풀러(DATABASE_URL) 호환: 준비문 캐시 끔(prepare_threshold=None), 세션 상태 미사용. 마이그레이션은 직접 연결
  (DIRECT_DATABASE_URL)에서 advisory lock 으로 한 번만.
"""
from __future__ import annotations

import base64
import hashlib
import os

import psycopg
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from dealcockpit.application.ports import ConcurrencyConflict
from dealcockpit.domain.plan import AuditEvent
from dealcockpit.domain.workspace import Workspace

MIGRATIONS: list[tuple[int, str]] = [
    (1, """
    CREATE TABLE IF NOT EXISTS workspaces (
        workspace_id TEXT PRIMARY KEY,
        version      INTEGER NOT NULL CHECK (version >= 1),
        key_id       TEXT NOT NULL,
        nonce        BYTEA NOT NULL,
        ciphertext   BYTEA NOT NULL,
        updated_at   TIMESTAMPTZ NOT NULL DEFAULT now()
    );
    CREATE TABLE IF NOT EXISTS audit_events (
        seq            BIGSERIAL PRIMARY KEY,
        workspace_id   TEXT NOT NULL,
        event_id       TEXT NOT NULL,
        at             TIMESTAMPTZ NOT NULL,
        actor          TEXT NOT NULL,
        actor_role     TEXT NOT NULL,
        acting_as_role TEXT,
        action         TEXT NOT NULL,
        object_ref     TEXT NOT NULL,
        input_hash     TEXT,
        recorded_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
        UNIQUE (workspace_id, event_id)
    );
    CREATE OR REPLACE FUNCTION audit_append_only() RETURNS trigger AS $$
    BEGIN
        RAISE EXCEPTION 'audit_events is append-only (%)', TG_OP;
    END; $$ LANGUAGE plpgsql;
    DROP TRIGGER IF EXISTS audit_no_update ON audit_events;
    CREATE TRIGGER audit_no_update BEFORE UPDATE OR DELETE ON audit_events FOR EACH ROW EXECUTE FUNCTION audit_append_only();
    DROP TRIGGER IF EXISTS audit_no_truncate ON audit_events;
    CREATE TRIGGER audit_no_truncate BEFORE TRUNCATE ON audit_events FOR EACH STATEMENT EXECUTE FUNCTION audit_append_only();
    """),
    (2, """
    CREATE TABLE IF NOT EXISTS users (
        username TEXT PRIMARY KEY,
        role     TEXT NOT NULL,
        pw_hash  TEXT NOT NULL CHECK (pw_hash LIKE 'scrypt$%')
    );
    CREATE TABLE IF NOT EXISTS sessions (
        token_hash TEXT PRIMARY KEY,
        username   TEXT NOT NULL REFERENCES users(username) ON DELETE CASCADE,
        expires_at TIMESTAMPTZ NOT NULL
    );
    CREATE INDEX IF NOT EXISTS sessions_expires ON sessions(expires_at);
    """),
]
_LOCK_KEY = 7_412_009  # advisory lock id(마이그레이션 직렬화)


class EncryptionKeyMissing(RuntimeError):
    pass


def _key_from_env(raw: str | None, allow_plaintext: bool) -> bytes | None:
    if raw:
        key = base64.b64decode(raw)
        if len(key) != 32:
            raise EncryptionKeyMissing("CAPACT_DATA_KEY 는 base64 32바이트(AES-256)여야 한다")
        return key
    if allow_plaintext:
        return None
    raise EncryptionKeyMissing("CAPACT_DATA_KEY 미설정 — 민감 데이터를 평문으로 저장하지 않는다(개발용은 allow_plaintext=True)")


class PostgresRepository:
    def __init__(self, url: str, data_key_b64: str | None = None, *, migrate: bool = False, migrate_url: str | None = None,
                 allow_plaintext: bool = False):
        self.url = url
        self._key = _key_from_env(data_key_b64 if data_key_b64 is not None else os.environ.get("CAPACT_DATA_KEY"), allow_plaintext)
        self.key_id = hashlib.sha256(self._key).hexdigest()[:12] if self._key else "plaintext-dev"
        if migrate:
            self.migrate(migrate_url or url)

    def _conn(self, url: str | None = None) -> psycopg.Connection:
        return psycopg.connect(url or self.url, prepare_threshold=None, connect_timeout=10)

    # ── 마이그레이션 ────────────────────────────────────────────────────────────
    def migrate(self, url: str) -> list[int]:
        applied = []
        with self._conn(url) as c:
            c.execute("SELECT pg_advisory_lock(%s)", (_LOCK_KEY,))
            try:
                c.execute("CREATE TABLE IF NOT EXISTS schema_migrations (version INTEGER PRIMARY KEY, applied_at TIMESTAMPTZ NOT NULL DEFAULT now())")
                done = {r[0] for r in c.execute("SELECT version FROM schema_migrations").fetchall()}
                for v, sql in MIGRATIONS:
                    if v in done:
                        continue
                    with c.transaction():
                        c.execute(sql)
                        c.execute("INSERT INTO schema_migrations(version) VALUES (%s)", (v,))
                    applied.append(v)
                c.commit()
            finally:
                c.execute("SELECT pg_advisory_unlock(%s)", (_LOCK_KEY,))
                c.commit()
        return applied

    # ── 암호화 ────────────────────────────────────────────────────────────────
    def _aad(self, ws_id: str, version: int) -> bytes:
        return f"{ws_id}|{version}".encode()

    def _seal(self, ws: Workspace) -> tuple[bytes, bytes]:
        plain = ws.model_dump_json().encode("utf-8")
        if self._key is None:
            return b"", plain
        nonce = os.urandom(12)
        return nonce, AESGCM(self._key).encrypt(nonce, plain, self._aad(ws.workspace_id, ws.version))

    def _open(self, ws_id: str, version: int, key_id: str, nonce: bytes, ct: bytes) -> Workspace:
        if key_id != self.key_id:
            raise EncryptionKeyMissing(f"{ws_id}: 다른 키({key_id})로 암호화된 행 — 키 회전 절차 필요")
        plain = ct if self._key is None else AESGCM(self._key).decrypt(nonce, ct, self._aad(ws_id, version))
        ws = Workspace.model_validate_json(plain)
        if ws.workspace_id != ws_id or ws.version != version:
            raise ValueError("행 메타데이터와 내용 불일치")
        return ws

    # ── 포트 구현 ───────────────────────────────────────────────────────────────
    def load(self, workspace_id: str) -> Workspace | None:
        with self._conn() as c:
            row = c.execute("SELECT version, key_id, nonce, ciphertext FROM workspaces WHERE workspace_id = %s", (workspace_id,)).fetchone()
        if row is None:
            return None
        return self._open(workspace_id, row[0], row[1], bytes(row[2]), bytes(row[3]))

    def save(self, ws: Workspace, expected_version: int) -> Workspace:
        new = ws.model_copy(update={"version": expected_version + 1})
        nonce, ct = self._seal(new)
        with self._conn() as c:
            if expected_version == 0:
                cur = c.execute(
                    "INSERT INTO workspaces(workspace_id, version, key_id, nonce, ciphertext) VALUES (%s, %s, %s, %s, %s) "
                    "ON CONFLICT (workspace_id) DO NOTHING", (new.workspace_id, new.version, self.key_id, nonce, ct))
            else:
                cur = c.execute(
                    "UPDATE workspaces SET version = %s, key_id = %s, nonce = %s, ciphertext = %s, updated_at = now() "
                    "WHERE workspace_id = %s AND version = %s", (new.version, self.key_id, nonce, ct, new.workspace_id, expected_version))
            if cur.rowcount != 1:
                c.rollback()
                raise ConcurrencyConflict(f"{ws.workspace_id}: 기대 버전 {expected_version} 이 저장된 버전과 다르다")
            c.commit()
        return new

    def append_audit(self, workspace_id: str, event: AuditEvent) -> None:
        with self._conn() as c:
            c.execute(
                "INSERT INTO audit_events(workspace_id, event_id, at, actor, actor_role, acting_as_role, action, object_ref, input_hash) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (workspace_id, event.event_id, event.at, event.actor, event.actor_role, event.acting_as_role, event.action, event.object_ref,
                 event.input_hash))
            c.commit()

    def list_audit(self, workspace_id: str) -> list[AuditEvent]:
        with self._conn() as c:
            rows = c.execute("SELECT event_id, at, actor, actor_role, acting_as_role, action, object_ref, input_hash FROM audit_events "
                             "WHERE workspace_id = %s ORDER BY seq", (workspace_id,)).fetchall()
        return [AuditEvent(event_id=r[0], at=r[1].replace(tzinfo=None), actor=r[2], actor_role=r[3], acting_as_role=r[4], action=r[5],
                           object_ref=r[6], input_hash=r[7]) for r in rows]
