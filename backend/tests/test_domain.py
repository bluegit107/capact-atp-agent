from __future__ import annotations

import itertools
import pathlib
import re

import pytest
from pydantic import ValidationError

from dealcockpit.domain.assumption import Assumption, AssumptionStatus, EvidenceTest
from dealcockpit.domain.clause import Clause, ClauseKind, Extraction
from dealcockpit.domain.common import Provenance, Role, SourceRef
from dealcockpit.domain.lever import (
    IllegalTransition,
    Lever,
    LeverStatus,
    LeverType,
    can_transition,
)


def make_lever(**over) -> Lever:
    base = dict(
        lever_id="LV-001",
        type=LeverType.CAPACITY_DELTA,
        params=dict(
            target="outsourcing:V-OUT-1",
            product="PL-02",
            qty_per_month=50000,
            available_from_week="2026-W36",
            unit_cost_multiplier=1.35,
        ),
        owner_role=Role.PRODUCTION,
        counterparty="외주사(가상)",
        confidence=0.5,
        provenance=Provenance.ASSUMPTION,
    )
    base.update(over)
    return Lever(**base)


# ── Lever ────────────────────────────────────────────────────────────────────
def test_new_lever_is_hypothesis_and_does_not_touch_plan_of_record():
    lv = make_lever()
    assert lv.status is LeverStatus.HYPOTHESIS
    assert not lv.affects_plan_of_record


def test_cannot_skip_approval():
    lv = make_lever().transition(LeverStatus.REQUESTED).transition(LeverStatus.ACCEPTED)
    assert not lv.affects_plan_of_record  # 수락만으로는 확정 계획에 반영 불가
    with pytest.raises(IllegalTransition):
        lv.transition(LeverStatus.APPLIED)
    assert lv.transition(LeverStatus.APPROVED).affects_plan_of_record


def test_no_state_reaches_applied_except_through_approved():
    for src in LeverStatus:
        if src is LeverStatus.APPROVED:
            continue
        assert not can_transition(src, LeverStatus.APPLIED)


def test_terminal_states_have_no_exit():
    for terminal in (LeverStatus.REJECTED, LeverStatus.APPLIED):
        assert all(not can_transition(terminal, dst) for dst in LeverStatus)


def test_counter_can_be_renegotiated_and_expired_can_be_reasked():
    lv = make_lever().transition(LeverStatus.REQUESTED).transition(LeverStatus.COUNTERED)
    assert lv.transition(LeverStatus.REQUESTED).status is LeverStatus.REQUESTED
    ex = make_lever().transition(LeverStatus.REQUESTED).transition(LeverStatus.EXPIRED)
    assert ex.transition(LeverStatus.REQUESTED).status is LeverStatus.REQUESTED


def test_lever_params_validated_per_type():
    with pytest.raises(ValidationError):
        make_lever(params=dict(target="x"))  # 필수 필드 누락
    with pytest.raises(ValidationError):
        make_lever(type=LeverType.PRICE_ADJUST, params=dict(target="C-101", pct=-80))  # 범위 밖
    with pytest.raises(ValidationError):
        make_lever(params={**make_lever().params, "free_text_injection": "ignore previous"})  # 모르는 필드 거부


def test_lever_rejects_source_provenance():
    # 레버는 원본 사실이 될 수 없다 (가설/모의/사람 입력/도출만)
    with pytest.raises(ValidationError):
        make_lever(provenance=Provenance.SOURCE)


# ── Assumption: 결정 불변성 게이트 ───────────────────────────────────────────
def make_assumption(**over) -> Assumption:
    base = dict(assumption_id="AS-001", statement="capacity_qty 의 기간 단위", owner_role=Role.PRODUCTION)
    base.update(over)
    return Assumption(**base)


def test_unconfirmed_and_unknown_invariance_blocks_submission():
    assert make_assumption().blocks_submission


def test_invariant_decision_does_not_block_even_if_unconfirmed():
    assert not make_assumption(decision_invariant=True).blocks_submission


def test_variant_decision_blocks_until_confirmed():
    a = make_assumption(decision_invariant=False)
    assert a.blocks_submission
    a = a.propose((EvidenceTest(name="batch_log", result="L-01 월 단위 일치", evidence_ref="as001"),))
    assert a.status is AssumptionStatus.PROPOSED and a.blocks_submission
    assert not a.confirm().blocks_submission


def test_cannot_confirm_without_evidence_proposal():
    with pytest.raises(ValueError):
        make_assumption().confirm()


# ── Clause: 근거 추적성·닫힌 enum·인간 확정 ──────────────────────────────────
def make_clause(**over) -> Clause:
    base = dict(
        clause_id="CL-C101-12",
        customer_id="C-101",
        kind=ClauseKind.TERMINATION_TRIGGER,
        params=dict(delay_days_gt=30),
        hard=True,
        evidence=SourceRef(doc_id="CT-C101", page=9, clause="제12조", quote="…30일을 초과하여 지연될 경우…"),
        extraction=Extraction(model="fake", confidence=0.9),
    )
    base.update(over)
    return Clause(**base)


def test_clause_without_quote_is_rejected():
    with pytest.raises(ValidationError):
        make_clause(evidence=SourceRef(doc_id="CT-C101", page=9, clause="제12조"))


def test_clause_unknown_kind_is_rejected():
    with pytest.raises(ValidationError):
        make_clause(kind="make_up_new_kind")


def test_clause_params_must_match_kind():
    with pytest.raises(ValidationError):
        make_clause(params=dict(rate_per_day_pct=0.1, cap_pct_of_order=10))  # late_penalty 파라미터를 해지 조항에


def test_unconfirmed_clause_is_not_usable_by_solver():
    assert not make_clause().usable_by_solver
    confirmed = make_clause(extraction=Extraction(model="fake", confidence=0.9, human_confirmed=True, confirmed_by="영업"))
    assert confirmed.usable_by_solver


# ── 아키텍처 규칙: domain 은 외부 프레임워크에 의존하지 않는다 ────────────────
DOMAIN_DIR = pathlib.Path(__file__).resolve().parents[1] / "src" / "dealcockpit" / "domain"
FORBIDDEN = {"ortools", "fastapi", "uvicorn", "sqlalchemy", "anthropic", "highspy", "requests", "httpx"}


def test_domain_layer_imports_no_infrastructure():
    offenders = []
    for py in DOMAIN_DIR.glob("*.py"):
        for line in py.read_text(encoding="utf-8").splitlines():
            m = re.match(r"\s*(?:from|import)\s+([A-Za-z_][\w]*)", line)
            if m and m.group(1) in FORBIDDEN:
                offenders.append((py.name, line.strip()))
    assert not offenders, offenders


def test_all_state_pairs_are_deterministic():
    # 전이 표가 모든 (src,dst) 쌍에 대해 bool 을 돌려주는지(키 누락 방지)
    for src, dst in itertools.product(LeverStatus, LeverStatus):
        assert isinstance(can_transition(src, dst), bool)
