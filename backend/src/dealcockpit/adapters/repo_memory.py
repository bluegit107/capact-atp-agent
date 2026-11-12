"""메모리 저장소(테스트·로컬 시연). 운영은 repo_postgres. 포트 계약(낙관적 락·추가 전용 감사)은 동일."""
from __future__ import annotations

import threading

from dealcockpit.application.ports import ConcurrencyConflict
from dealcockpit.domain.plan import AuditEvent
from dealcockpit.domain.workspace import Workspace


class MemoryRepository:
    def __init__(self) -> None:
        self._ws: dict[str, str] = {}  # JSON 으로 보관(직렬화 경로를 운영과 같게)
        self._audit: dict[str, list[str]] = {}
        self._lock = threading.Lock()

    def load(self, workspace_id: str) -> Workspace | None:
        raw = self._ws.get(workspace_id)
        return Workspace.model_validate_json(raw) if raw else None

    def save(self, ws: Workspace, expected_version: int) -> Workspace:
        with self._lock:
            cur = self._ws.get(ws.workspace_id)
            cur_version = Workspace.model_validate_json(cur).version if cur else 0
            if cur_version != expected_version:
                raise ConcurrencyConflict(f"{ws.workspace_id}: 저장된 버전 {cur_version} != 기대 {expected_version}")
            new = ws.model_copy(update={"version": expected_version + 1})
            self._ws[ws.workspace_id] = new.model_dump_json()
            return new

    def append_audit(self, workspace_id: str, event: AuditEvent) -> None:
        with self._lock:
            self._audit.setdefault(workspace_id, []).append(event.model_dump_json())

    def list_audit(self, workspace_id: str) -> list[AuditEvent]:
        return [AuditEvent.model_validate_json(e) for e in self._audit.get(workspace_id, [])]
