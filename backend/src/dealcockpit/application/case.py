from __future__ import annotations

from dataclasses import dataclass, field

from dealcockpit.domain.bid import Bid
from dealcockpit.domain.clause import Clause
from dealcockpit.domain.lever import Lever
from dealcockpit.domain.plant import PlantModel


@dataclass(frozen=True)
class Case:
    """한 번의 분석에 필요한 원재료(순수 데이터). 파일 읽기는 어댑터(golden_loader)가 한다."""

    derived: dict  # extensions/derived/s2_pl02_commitments.json (DERIVED)
    plant: PlantModel
    bid: Bid
    clauses: tuple[Clause, ...]


@dataclass(frozen=True)
class ModelSettings:
    """모델의 조작 가능한 가정값. 확정 계획용 값은 파라미터 레지스트리(사람만 변경)에서 만들어진다."""

    default_late_rate_pct_per_day: float = 0.1  # 계약 근거 없는 고객의 지체율(가정)
    default_max_late_weeks: int = 8  # 해지 조항 없는 고객의 하드 지연 한도(가정)
    termination_buffer_weeks: int = 1  # 해지 트리거 안쪽 안전 여유(가정)
    bid_ramp_weeks: int = 4  # 입찰 공급 램프업(가정)
    slot_window_weeks: int = 4  # 편성 물량을 떼어낼 직전 납기 구간(가정)
    recall_spread_weeks: int = 4  # 회수 대체 생산 분산 기간(가정)
    recall_max_late_weeks: int = 4  # 회수 대체품 지연 한도(가정)
    recall_replacement_ratio: float = 1.0  # 회수 물량 중 대체 생산 비율(가정, 품질·영업 조정)
    invariance_accept_tol_pp: float = 5.0  # 결정 불변성 판정: 수락률 허용 차이(%p)


@dataclass(frozen=True)
class BuildParams:
    # 계획 기준(AS-001). S_* = strat_capacity(부하=용량×가동률, 수주는 구성비·시점만, 규모 대응비 적용).
    #   S_MONTH(기본, 대회 문서와 정합) / S_YEAR(대안) / H3(실측 보정, 수주 규모 그대로 — 교차검증용) / CONFIRMED
    interpretation: str = "S_MONTH"
    confirmed_monthly: float | None = None
    fx_label: str = "as_of"
    price_pressure_pct: float = 0.0
    levers: tuple[Lever, ...] = ()
    include_hypothetical: bool = False  # True 면 가설·요청 중 레버도 브랜치 계산에 포함
    requirement_delays: dict[str, int] = field(default_factory=dict)  # 예: {"formula_revalidation": 6, "M-B_recontract": 3}
    decision_week: int = 1  # 이사회(7/6)가 속한 주
    with_bid: bool = True
    product_id: str = "PL-02"
    # what-if 조건(확정 계획 아님): 특근 상한 %를 직접 지정. 기본은 0이고 확정 계획은 특근 레버 회신으로만 바뀐다.
    overtime_cap_pct: float | None = None
    recall_cost_per_unit_krw: float | None = None  # None = 12개월 확정 결함률 × 입찰 단가(가정, 품질이 조정)
    recall_replacement_week: int | None = None  # 품질위원회가 회수를 의결한 경우 대체 생산 시작 주
    excluded_options: tuple[str, ...] = ()  # 투자 승인 거절 등으로 쓸 수 없는 설비 옵션
    settings: ModelSettings = field(default_factory=ModelSettings)
