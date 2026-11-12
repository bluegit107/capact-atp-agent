from __future__ import annotations

from datetime import date
from enum import StrEnum

from pydantic import Field

from .common import Frozen, Role


class AssumptionStatus(StrEnum):
    UNCONFIRMED = "UNCONFIRMED"  # 미확인
    PROPOSED = "PROPOSED"        # 증거 기반 제안됨, 담당자 확정 전
    CONFIRMED = "CONFIRMED"
    REJECTED = "REJECTED"


class EvidenceTest(Frozen):
    name: str
    result: str
    evidence_ref: str


class Assumption(Frozen):
    assumption_id: str = Field(pattern=r"^AS-\d{3}$")
    statement: str
    status: AssumptionStatus = AssumptionStatus.UNCONFIRMED
    owner_role: Role
    due: date | None = None
    tests: tuple[EvidenceTest, ...] = ()
    impact_scope: tuple[str, ...] = ()
    # 가능한 모든 해석으로 풀어 추천안이 같으면 True (확정 없이도 진행), 다르면 False (상신 차단)
    decision_invariant: bool | None = None

    def propose(self, tests: tuple[EvidenceTest, ...]) -> "Assumption":
        return self.model_copy(update={"status": AssumptionStatus.PROPOSED, "tests": tests})

    def confirm(self) -> "Assumption":
        if self.status is not AssumptionStatus.PROPOSED:
            raise ValueError("증거 제안 없이 확정할 수 없다 (먼저 propose)")
        return self.model_copy(update={"status": AssumptionStatus.CONFIRMED})

    def reject(self) -> "Assumption":
        return self.model_copy(update={"status": AssumptionStatus.REJECTED})

    @property
    def blocks_submission(self) -> bool:
        """결정 불변성 게이트: 확정 전이고 결정이 해석에 좌우되면(또는 아직 모르면) 상신 차단."""
        if self.status is AssumptionStatus.CONFIRMED:
            return False
        return self.decision_invariant is not True
