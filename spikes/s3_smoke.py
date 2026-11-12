"""S3 스모크: 결정 불변성 게이트 + 병목 프로브(레버 가치·손익분기·문의 우선순위)."""
from __future__ import annotations

from dealcockpit.adapters.golden_loader import load_case, load_reply_stats
from dealcockpit.adapters.solver_lp import LpSolver
from dealcockpit.application.case import BuildParams
from dealcockpit.application.gate import check_invariance, gate_capacity_assumption
from dealcockpit.application.probes import probe_levers
from dealcockpit.domain.assumption import Assumption
from dealcockpit.domain.common import Role

case, solver = load_case(), LpSolver("SCIP")
res = check_invariance(solver, case, BuildParams())
print("== 결정 불변성(A):", "불변" if res.invariant else "결정이 기준에 좌우됨")
for s in res.signatures:
    print(f"   {s.interpretation:8} 수락 {s.accept_pct:5.1f}% 조치 {list(s.actions)} {'가능' if s.feasible else '불가능'}")
for r in res.reasons:
    print("   ·", r)
a, _ = gate_capacity_assumption(solver, case, BuildParams(), Assumption(assumption_id="AS-001", statement="용량 단위", owner_role=Role.PRODUCTION))
print("   상신 차단:", a.blocks_submission)

print("\n== 병목 프로브 (A 기준, 문의 우선순위 순)")
for p in probe_levers(solver, case, BuildParams(), load_reply_stats()):
    print(f" [{p.priority_score/1e8:6.2f}] {p.label:34} Δ순이익 {p.delta_net_krw/1e8:7.2f}억 Δ수락 {p.delta_accept_pp:+6.1f}%p 회신 {p.answer_days:.0f}일 "
          f"성공 {p.success_prior:.2f} 마감 전 {'O' if p.answerable_before_deadline else 'X'} 한계선 {p.breakeven}")
