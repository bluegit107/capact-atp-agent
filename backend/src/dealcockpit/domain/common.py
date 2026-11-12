from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class Provenance(StrEnum):
    """정보 성격 구분 (A05 §6: 원본·부서 주장·가정·합성·모의·계산결과를 섞지 않는다)."""

    SOURCE = "SOURCE"            # 원본 데이터룸 기록
    DEPT_CLAIM = "DEPT_CLAIM"    # 원본에 포함된 부서의 주장·분석 (검증 전까지 사실 아님)
    ASSUMPTION = "ASSUMPTION"    # 참가자의 명시적 가정
    SYNTHETIC = "SYNTHETIC"      # 합성 데이터 (extensions/)
    SIMULATED = "SIMULATED"      # 모의 회신·모의 승인 (가상 입력)
    HUMAN_INPUT = "HUMAN_INPUT"  # 사람이 직접 입력/확정
    DERIVED = "DERIVED"          # 원본에서 계산·도출


class Maturity(StrEnum):
    """A04: 실제 작동 / 모의 처리 / 설계 단계 구분."""

    WORKING = "WORKING"
    SIMULATED = "SIMULATED"
    DESIGN = "DESIGN"


class Role(StrEnum):
    """RBAC 역할 (approval_authority_policy.md 의 승인 주체와 정렬)."""

    SALES = "영업"
    PRODUCTION = "생산"
    PROCUREMENT = "구매"
    QUALITY = "품질"
    FINANCE = "재무"
    SALES_EXEC = "영업임원"
    CEO_BOARD = "CEO/이사회"
    REVIEWER = "심사자"
    POLICY_ADMIN = "정책관리자"  # 승인 권한표를 관리하는 사람(업무 승인 권한은 없음)
    DEMO_ADMIN = "시연 진행자"  # 외부(바이어·V-205) 모의 회신 입력, 가상 시계, 역할 전환(대리 수행은 감사에 기록)


class Frozen(BaseModel):
    """모든 스키마의 기반: 알 수 없는 필드 거부(오타·주입 방지), 불변."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class SourceRef(Frozen):
    """원본 행/파일 또는 계약서 원문까지 추적하는 근거 참조."""

    file: str | None = None
    row: str | None = None
    doc_id: str | None = None
    page: int | None = Field(default=None, ge=1)
    clause: str | None = None
    char_span: tuple[int, int] | None = None
    quote: str | None = None
