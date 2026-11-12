"""S2.5 스모크: 계획 기준별 시나리오 요약 + 경제성 지표."""
from __future__ import annotations

import sys

from dealcockpit.adapters.golden_loader import load_case
from dealcockpit.adapters.solver_lp import LpSolver
from dealcockpit.application.case import BuildParams
from dealcockpit.application.economics import breakeven_price_cut_pct, option_paybacks, unit_economics
from dealcockpit.application.scenarios import run_scenarios


def show(out, title):
    print(f"\n=== {title}  [{out.solver.name} {out.solver.status} {out.solver.wall_ms}ms]")
    for s in ([out.baseline] if out.baseline else []) + list(out.scenarios):
        f, k = s.financials, s.kpis
        flag = " [불가능]" if s.infeasibility else ""
        print(f" [{s.scenario_id}] {s.label[:22]:22} 수락 {k['bid_accept_pct']:5.1f}% 순 {f.net_krw/1e8:7.1f}억 OTIF {k['otif_existing_pct']:5.1f}% "
              f"조치={list(s.capacity_actions)}{flag}")
        if s.infeasibility:
            tot = sum(r.slack for r in s.infeasibility.min_relaxation)
            print(f"      ! 완화 필요 합계 {tot:,.0f} EA (항목 {len(s.infeasibility.min_relaxation)})")
    for t in out.trace:
        if "warning" in t:
            print("   ⚠", t["warning"])


if __name__ == "__main__":
    case = load_case()
    solver = LpSolver(sys.argv[1] if len(sys.argv) > 1 else "SCIP")
    REQ = {"formula_revalidation": 6, "M-B_recontract": 4}
    for basis in ("S_MONTH", "S_YEAR", "H3"):
        show(run_scenarios(solver, case, BuildParams(interpretation=basis)), f"{basis}, 레버 없음, 옵션 요건 미확인")
    show(run_scenarios(solver, case, BuildParams(requirement_delays=REQ)), "S_MONTH + OPT-02 요건 회신(6주/4주)")
    show(run_scenarios(solver, case, BuildParams(requirement_delays=REQ, recall_replacement_week=2)), "S_MONTH + OPT-02 회신 + 품질위원회 회수 의결(W02)")
    print("\n=== 경제성(CP5)")
    for u in unit_economics(case, BuildParams()):
        print(f" {u.fx_label:8} {u.usd_krw:8.2f} 인하 {u.price_cut_pct:5.1f}%  단가 {u.price_krw:8,.0f}  원료 {u.material_krw:7,.0f}  리콜 {u.recall_krw:5,.0f}  공헌 {u.contribution_krw:7,.0f} ({u.margin_pct:.1f}%)")
    print(" 손익분기 단가 인하율:", breakeven_price_cut_pct(case, BuildParams()), "%")
    for o in option_paybacks(case, BuildParams(requirement_delays=REQ)):
        print(f" {o.option_id}: capex {o.capex_krw/1e8:.0f}억 가동 W{o.start_week} 사용 {o.usable_monthly_units:,.0f}/월 공헌 {o.monthly_contribution_krw/1e8:.1f}억/월 "
              f"회수 {o.payback_months_after_start}개월(결정부터 {o.payback_months_from_decision}) {o.note}")
