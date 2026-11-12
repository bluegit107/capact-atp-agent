"""인증: scrypt 비밀번호 해시, 서버 저장 세션(토큰은 SHA-256 해시로만 보관), 로그인 시도 제한.

계정은 회원가입이 없고 운영자가 환경변수(CAPACT_ACCOUNTS: {"user": {"role": "...", "hash": "scrypt$..."}})로 주입한다.
비밀번호 원문은 저장소·로그·환경변수 어디에도 두지 않는다(해시만).
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import threading
import time
from dataclasses import dataclass

from dealcockpit.domain.common import Role

SESSION_TTL_S = 8 * 3600
_N, _R, _P = 2**14, 8, 1


def hash_password(password: str, salt: bytes | None = None) -> str:
    salt = salt or os.urandom(16)
    dk = hashlib.scrypt(password.encode(), salt=salt, n=_N, r=_R, p=_P, dklen=32)
    return f"scrypt${_N}${_R}${_P}${base64.b64encode(salt).decode()}${base64.b64encode(dk).decode()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, n, r, p, salt_b64, dk_b64 = stored.split("$")
        if algo != "scrypt":
            return False
        dk = hashlib.scrypt(password.encode(), salt=base64.b64decode(salt_b64), n=int(n), r=int(r), p=int(p), dklen=32)
        return hmac.compare_digest(dk, base64.b64decode(dk_b64))
    except Exception:
        return False


@dataclass(frozen=True)
class User:
    username: str
    role: Role


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


class MemoryAuthStore:
    def __init__(self) -> None:
        self.users: dict[str, tuple[Role, str]] = {}
        self.sessions: dict[str, tuple[str, float]] = {}
        self._lock = threading.Lock()

    def upsert_user(self, username: str, role: Role, pw_hash: str) -> None:
        self.users[username] = (role, pw_hash)

    def get_user(self, username: str) -> tuple[Role, str] | None:
        return self.users.get(username)

    def create_session(self, username: str) -> str:
        tok = secrets.token_urlsafe(32)
        with self._lock:
            self.sessions[_token_hash(tok)] = (username, time.time() + SESSION_TTL_S)
        return tok

    def get_session(self, token: str) -> str | None:
        rec = self.sessions.get(_token_hash(token))
        if not rec or rec[1] < time.time():
            return None
        return rec[0]

    def delete_session(self, token: str) -> None:
        self.sessions.pop(_token_hash(token), None)


class PostgresAuthStore:
    """users·sessions 테이블(repo_postgres 마이그레이션 v2). 세션은 다중 레플리카에서도 유효."""

    def __init__(self, url: str):
        self.url = url

    def _conn(self):
        import psycopg

        return psycopg.connect(self.url, prepare_threshold=None, connect_timeout=10)

    def upsert_user(self, username: str, role: Role, pw_hash: str) -> None:
        with self._conn() as c:
            c.execute("INSERT INTO users(username, role, pw_hash) VALUES (%s,%s,%s) ON CONFLICT (username) DO UPDATE "
                      "SET role = EXCLUDED.role, pw_hash = EXCLUDED.pw_hash", (username, role.value, pw_hash))
            c.commit()

    def get_user(self, username: str) -> tuple[Role, str] | None:
        with self._conn() as c:
            row = c.execute("SELECT role, pw_hash FROM users WHERE username = %s", (username,)).fetchone()
        return (Role(row[0]), row[1]) if row else None

    def create_session(self, username: str) -> str:
        tok = secrets.token_urlsafe(32)
        with self._conn() as c:
            c.execute("INSERT INTO sessions(token_hash, username, expires_at) VALUES (%s,%s, now() + make_interval(secs => %s))",
                      (_token_hash(tok), username, SESSION_TTL_S))
            c.execute("DELETE FROM sessions WHERE expires_at < now()")
            c.commit()
        return tok

    def get_session(self, token: str) -> str | None:
        with self._conn() as c:
            row = c.execute("SELECT username FROM sessions WHERE token_hash = %s AND expires_at > now()", (_token_hash(token),)).fetchone()
        return row[0] if row else None

    def delete_session(self, token: str) -> None:
        with self._conn() as c:
            c.execute("DELETE FROM sessions WHERE token_hash = %s", (_token_hash(token),))
            c.commit()


class LoginThrottle:
    """사용자·IP 별 실패 5회/10분 이후 잠금(메모리; 레플리카 1 전제, 다중이면 DB 로 이전)."""

    def __init__(self, limit: int = 5, window_s: int = 600):
        self.limit, self.window = limit, window_s
        self.fails: dict[str, list[float]] = {}
        self._lock = threading.Lock()

    def blocked(self, key: str) -> bool:
        now = time.time()
        with self._lock:
            xs = [t for t in self.fails.get(key, []) if now - t < self.window]
            self.fails[key] = xs
            return len(xs) >= self.limit

    def fail(self, key: str) -> None:
        with self._lock:
            self.fails.setdefault(key, []).append(time.time())

    def reset(self, key: str) -> None:
        with self._lock:
            self.fails.pop(key, None)


def seed_accounts(store, raw_json: str | None) -> int:
    """CAPACT_ACCOUNTS(해시만 포함)로 계정 생성·갱신. 원문 비밀번호는 받지 않는다."""
    if not raw_json:
        return 0
    data = json.loads(raw_json)
    for username, spec in data.items():
        if not str(spec["hash"]).startswith("scrypt$"):
            raise ValueError("CAPACT_ACCOUNTS 에는 scrypt 해시만 넣는다(원문 비밀번호 금지)")
        store.upsert_user(username, Role(spec["role"]), spec["hash"])
    return len(data)
