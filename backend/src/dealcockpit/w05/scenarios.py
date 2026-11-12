"""시연 시작 상태 준비(녹화용). 지시서 12장 시나리오의 '시작 상태'를 실제 업무 흐름 함수로 차례로 만든다(직접 상태를 쓰지 않음).

준비 단계의 기록은 실제 역할 이름과 함께 '시연 준비' 사유로 이력에 남는다. 결과 날짜(가상 오늘)를 함께 돌려준다.
"""
from __future__ import annotations

from dealcockpit.domain.common import Role

from .flow import BidLedger, W05Flow

R = Role
WHO = "시연 준비"


def _s1_until(f: W05Flow, step: int) -> tuple[BidLedger, str]:
    L = f.receive_rfp("2026-07-01", WHO, R.SALES)
    buyer = next(r for r in L.requests if r.to_external == "C-201_BUYER")
    f.send_request(L, buyer.id, "2026-07-01", WHO, R.SALES)
    for r in [r for r in L.requests if r.status == "DRAFT"]:
        f.send_request(L, r.id, "2026-07-01", WHO, R.SALES)
    f.answer_request(L, buyer.id, {"supply_start_date": "2027-04-01", "partial_award_allowed": True, "split_delivery": True},
                     "납품 개시 2027-04-01, 부분 물량 제안 허용, 분할 납품 허용", "2026-07-03", WHO, R.DEMO_ADMIN)
    f.confirm_from_request(L, buyer.id, "2026-07-03", WHO, R.SALES)
    b = next(o for o in L.options if o.label == "B" and o.status == "PROPOSED")
    f.submit(L, b.id, "2026-07-04", WHO, R.SALES)
    if step <= 8:
        return L, "2026-07-04"
    for a in [a for a in L.approvals if a.status == "PENDING"]:
        if a.decision_type == "BID_PARTICIPATION":
            f.decide(L, a.id, "CONDITIONAL", "단가 인하 요구가 10%를 넘으면 재상신", "2026-07-06", WHO, R.CEO_BOARD)
        else:
            f.decide(L, a.id, "APPROVED", None, "2026-07-06", WHO, R.CEO_BOARD)
    t = next(t for t in L.tasks if t.type == "CAPACITY_RESERVATION")
    f.complete_task(L, t.id, "2026-07-07", WHO, R.PRODUCTION)
    return L, "2026-07-07"


PREPARE = {
    "s2": ("S2 시작: S1 10단계까지(승인, 생산 예약 확정)", lambda f: _s1_until(f, 10)),
    "s3b": ("S3-b 시작: S1 8단계까지(대안 B 상신, 승인 대기)", lambda f: _s1_until(f, 8)),
}


def prepare(f: W05Flow, scenario: str) -> tuple[BidLedger, str]:
    if scenario not in PREPARE:
        raise ValueError(f"알 수 없는 시나리오: {scenario}")
    return PREPARE[scenario][1](f)
