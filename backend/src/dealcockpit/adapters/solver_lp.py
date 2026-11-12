"""PL-02 주 단위 LP/MIP 솔버 어댑터 (OR-Tools pywraplp, 백엔드 HIGHS/SCIP 교체 가능).

모델 요약 (자세한 근거는 docs/SOLVER_MODEL.md)
  x[g,t]      그룹 g 의 주 t 출하량.  S[g,t] = 누적 출하,  D[g,t] = 누적 납기 도래(상수)
  S <= D      납기 전 선출하 금지(발주된 수량만 출하)
  S + sh >= D[t-H]   하드 지연 한도 H 주(해지 트리거·고정 납기). sh 는 위반 슬랙(매우 비싼 비용) -> 위반량 보고
  Lp >= D[t-grace] - S   지체 수량(주). 비용 = 일 지체율 x 7 x 단가 x Lp
  nl >= due[t] - x[t]    (면책 활성 시) 신규 지연 수량 -> 단가 인하 비용 근사
  I[t] = I[t-1] + prod[t] - sum x - bid[t]
  prod = base + overtime + option + outsourced
  이진: 증설/전환 옵션 선택(선행 요건이 확인된 옵션만 입력에 존재)
하드 제약은 슬랙으로 완화해 해를 항상 만들고, 슬랙 > 0 이면 INFEASIBLE 로 판정하며 최소 완화량을 보고한다(E8).
"""
from __future__ import annotations

import time

from ortools.linear_solver import pywraplp

from dealcockpit.domain.solver_input import DemandGroupInput, RawSolution, SolverInput

BIG = 1e8  # 슬랙 1 EA 당 비용(KRW). 어떤 실제 비용보다 크게 둔다.
EPS_UNITS = 0.5  # 이보다 작은 슬랙은 수치 잡음으로 본다


def _cum(values: tuple[float, ...]) -> list[float]:
    out, s = [], 0.0
    for v in values:
        s += v
        out.append(s)
    return out


class LpSolver:
    def __init__(self, backend: str = "HIGHS", threads: int = 1):
        self.backend = backend
        self.name = f"ortools-{backend.lower()}"
        self.threads = threads

    def solve(self, inp: SolverInput, time_limit_s: float = 10.0) -> RawSolution:
        solver = pywraplp.Solver.CreateSolver(self.backend)
        if solver is None:
            raise RuntimeError(f"solver backend {self.backend} unavailable")
        solver.SetNumThreads(self.threads)
        solver.SetTimeLimit(int(time_limit_s * 1000))
        inf = solver.infinity()
        H = inp.horizon_weeks
        cap = inp.capacity
        w = inp.weights
        obj = solver.Objective()

        x, S, sh, shp, Lp, nl = {}, {}, {}, {}, {}, {}
        Dcum = {g.group_id: _cum(g.weekly_due) for g in inp.groups}

        def d_at(g: DemandGroupInput, t: int) -> float:
            return Dcum[g.group_id][t] if t >= 0 else 0.0

        for g in inp.groups:
            gid = g.group_id
            rate_cost = g.late_rate_per_day_pct / 100.0 * 7.0 * g.unit_price_krw  # KRW / (EA·주)
            grace = g.waiver.grace_weeks if g.waiver else 0
            for t in range(H):
                x[gid, t] = solver.NumVar(0, inf, f"x_{gid}_{t}")
                S[gid, t] = solver.NumVar(0, inf, f"S_{gid}_{t}")
                prev = S[gid, t - 1] if t > 0 else 0
                solver.Add(S[gid, t] == prev + x[gid, t])
                solver.Add(S[gid, t] <= Dcum[gid][t])
                if g.max_late_weeks is not None and t - g.max_late_weeks >= 0:
                    sh[gid, t] = solver.NumVar(0, inf, f"sh_{gid}_{t}")
                    solver.Add(S[gid, t] + sh[gid, t] >= d_at(g, t - g.max_late_weeks))
                    obj.SetCoefficient(sh[gid, t], -BIG)
                if gid in inp.protect_floor:
                    shp[gid, t] = solver.NumVar(0, inf, f"shp_{gid}_{t}")
                    solver.Add(S[gid, t] + shp[gid, t] >= inp.protect_floor[gid][t] - 0.5)
                    obj.SetCoefficient(shp[gid, t], -BIG)
                if rate_cost > 0 and t - grace >= 0:
                    Lp[gid, t] = solver.NumVar(0, inf, f"Lp_{gid}_{t}")
                    solver.Add(Lp[gid, t] + S[gid, t] >= d_at(g, t - grace))
                    obj.SetCoefficient(Lp[gid, t], -w.w_customer * rate_cost)
                if g.waiver:
                    nl[gid, t] = solver.NumVar(0, inf, f"nl_{gid}_{t}")
                    solver.Add(nl[gid, t] >= g.weekly_due[t] - x[gid, t])
                    obj.SetCoefficient(nl[gid, t], -w.w_customer * g.waiver.discount_pct / 100.0 * g.unit_price_krw)

        # 입찰 물량(수락률 a)
        a = solver.NumVar(0, 0, "a") if inp.bid is None else solver.NumVar(inp.bid.min_accept, inp.bid.max_accept, "a")
        ramp = [0.0] * H
        if inp.bid is not None:
            for t in range(inp.bid.start_week, H):
                ramp[t] = min(1.0, (t - inp.bid.start_week + 1) / inp.bid.ramp_weeks)
            unit_margin = inp.bid.unit_price_krw - inp.bid.unit_cost_krw - inp.bid.recall_cost_per_unit_krw
            margin_total = sum(inp.bid.weekly_requested * r * unit_margin for r in ramp)
            obj.SetCoefficient(a, w.w_profit * margin_total)

        # 옵션(이진)과 외주
        z = {o.option_id: solver.BoolVar(f"z_{o.option_id}") for o in cap.options}
        for o in cap.options:
            obj.SetCoefficient(z[o.option_id], -float(o.capex_krw))

        base_use, ot, opt_use, out, inv = {}, {}, {}, {}, {}
        ot_cap = cap.overtime_cap_pct / 100.0 * cap.base_weekly
        for t in range(H):
            base_use[t] = solver.NumVar(0, cap.base_weekly, f"base_{t}")
            ot[t] = solver.NumVar(0, ot_cap if t >= cap.overtime_start_week else 0.0, f"ot_{t}")
            opt_use[t] = solver.NumVar(0, inf, f"opt_{t}")
            out[t] = solver.NumVar(0, inf, f"out_{t}")
            inv[t] = solver.NumVar(0, inf, f"I_{t}")
            avail = [o.added_weekly * z[o.option_id] for o in cap.options if t >= o.start_week]
            solver.Add(opt_use[t] <= solver.Sum(avail) if avail else opt_use[t] <= 0)
            out_cap = sum(p.qty_per_week for p in cap.outsourcing if t >= p.start_week)
            solver.Add(out[t] <= out_cap)
            prod = base_use[t] + ot[t] + opt_use[t] + out[t]
            ship_t = solver.Sum([x[g.group_id, t] for g in inp.groups])
            bid_t = (a * (inp.bid.weekly_requested * ramp[t])) if inp.bid is not None else 0
            prev = cap.starting_inventory if t == 0 else inv[t - 1]
            solver.Add(inv[t] == prev + prod - ship_t - bid_t)
            # 특근·외주 할증(가중): 단위원가 대비 추가분
            obj.SetCoefficient(ot[t], -w.w_premium * (cap.overtime_multiplier - 1.0) * cap.conversion_cost_krw)
            if cap.outsourcing:
                mult = max(p.cost_multiplier for p in cap.outsourcing if t >= p.start_week) if out_cap > 0 else 1.0
                obj.SetCoefficient(out[t], -w.w_premium * (mult - 1.0) * cap.unit_cost_krw)
        obj.SetMaximization()

        t0 = time.perf_counter()
        status = solver.Solve()
        wall_ms = int((time.perf_counter() - t0) * 1000)

        names = {pywraplp.Solver.OPTIMAL: "OPTIMAL", pywraplp.Solver.FEASIBLE: "FEASIBLE", pywraplp.Solver.INFEASIBLE: "INFEASIBLE"}
        if status not in (pywraplp.Solver.OPTIMAL, pywraplp.Solver.FEASIBLE):
            return RawSolution(
                status=names.get(status, "UNKNOWN"), wall_ms=wall_ms, solver_name=self.name, accept=0.0, options_selected=(),
                base_use=(), overtime=(), option_use=(), outsourced=(), inventory=(), bid_ship=(), ship={}, relaxations={}, objective=0.0,
            )

        relax: dict[str, float] = {}
        for (gid, t), var in sh.items():
            v = var.solution_value()
            if v > EPS_UNITS:
                g = next(gg for gg in inp.groups if gg.group_id == gid)
                relax[f"{gid}: {g.max_late_weeks}주 한도 초과 W{t:02d}"] = round(v, 1)
        for (gid, t), var in shp.items():
            v = var.solution_value()
            if v > EPS_UNITS:
                relax[f"{gid}: 기존 서비스 수준 보호 위반 W{t:02d}"] = round(v, 1)
        sol_status = names[status]
        if relax:
            sol_status = "INFEASIBLE"  # 하드 제약을 지키는 해가 없다 (슬랙 = 최소 완화량)
        av = a.solution_value()
        return RawSolution(
            status=sol_status,
            wall_ms=wall_ms,
            solver_name=self.name,
            accept=round(av, 6),
            options_selected=tuple(o.option_id for o in cap.options if z[o.option_id].solution_value() > 0.5),
            base_use=tuple(base_use[t].solution_value() for t in range(H)),
            overtime=tuple(ot[t].solution_value() for t in range(H)),
            option_use=tuple(opt_use[t].solution_value() for t in range(H)),
            outsourced=tuple(out[t].solution_value() for t in range(H)),
            inventory=tuple(inv[t].solution_value() for t in range(H)),
            bid_ship=tuple(av * (inp.bid.weekly_requested * ramp[t]) if inp.bid else 0.0 for t in range(H)),
            ship={g.group_id: tuple(x[g.group_id, t].solution_value() for t in range(H)) for g in inp.groups},
            relaxations=relax,
            objective=solver.Objective().Value(),
        )
