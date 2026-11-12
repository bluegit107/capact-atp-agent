from __future__ import annotations

from typing import Protocol

from dealcockpit.domain.plan import AuditEvent
from dealcockpit.domain.solver_input import RawSolution, SolverInput
from dealcockpit.domain.workspace import Workspace


class ConcurrencyConflict(RuntimeError):
    """다른 사용자가 먼저 저장했다(낙관적 락). 다시 읽고 재시도해야 한다."""


class RepositoryPort(Protocol):
    """업무 상태 저장소. 구현: 메모리(테스트), PostgreSQL(AXHub)."""

    def load(self, workspace_id: str) -> Workspace | None: ...

    def save(self, ws: Workspace, expected_version: int) -> Workspace:
        """expected_version 이 저장된 버전과 다르면 ConcurrencyConflict. 성공 시 version+1 된 상태를 돌려준다."""
        ...

    def append_audit(self, workspace_id: str, event: AuditEvent) -> None: ...

    def list_audit(self, workspace_id: str) -> list[AuditEvent]: ...


class SolverPort(Protocol):
    """수리 최적화 엔진. 구현체는 adapters 에 둔다(OR-Tools/HiGHS/SCIP 교체 가능)."""

    name: str

    def solve(self, inp: SolverInput, time_limit_s: float = 10.0) -> RawSolution: ...
