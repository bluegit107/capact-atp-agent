"""솔버 성능 프로브(P0 스파이크의 패키지판). 제품 모델이 아니라 '규모와 시간' 측정용이다.

수치 일부는 스파이크용 대략값이며 제품 모델에서는 SOURCE/ASSUMPTION provenance 로 대체된다.
입력은 원본에서 뽑은 집계 JSON(solver_probe_inputs.json)이라 컨테이너에서도 동일하게 재현된다.
"""
from __future__ import annotations

import json
import statistics
import time
from pathlib import Path

from ortools.linear_solver import pywraplp

WEEKS = 52
FX = 1480.0
PRODUCTS = ["PL-01", "PL-02", "PL-03", "PL-04", "PL-05", "PL-06"]
BID = {"PL-01": (6_000_000, 11.80), "PL-02": (1_500_000, 15.80)}
GPM = {"PL-01": 0.28, "PL-02": 0.32, "PL-03": 0.30, "PL-04": 0.30, "PL-05": 0.25, "PL-06": 0.25}
PRESETS = {
    "A_protect_customers": {"w_customer": 10.0, "w_profit": 1.0, "w_premium": 1.0},
    "B_max_profit": {"w_customer": 1.0, "w_profit": 1.0, "w_premium": 1.0},
    "C_all_in_capacity": {"w_customer": 3.0, "w_profit": 1.0, "w_premium": 0.2},
}
INPUTS_PATH = Path(__file__).with_name("solver_probe_inputs.json")


def load_inputs(mode: str) -> dict:
    raw = json.loads(INPUTS_PATH.read_text(encoding="utf-8"))[mode]
    groups = {(g["customer_id"], g["product_id"]): {"weekly_demand": g["weekly_demand"], "unit_price": g["unit_price"]} for g in raw["groups"]}
    return {"cap_week": raw["cap_week"], "groups": groups}


def build_and_solve(inputs: dict, backend: str, weights: dict, integer_batches: bool, time_limit_s: float) -> dict:
    solver = pywraplp.Solver.CreateSolver(backend)
    if solver is None:
        return {"error": f"backend {backend} unavailable"}
    solver.SetNumThreads(1)
    solver.SetTimeLimit(int(time_limit_s * 1000))
    inf = solver.infinity()
    G = list(inputs["groups"].keys())
    LATE_WEEKS = 4

    x, B = {}, {}
    for g in G:
        for t in range(WEEKS):
            x[g, t] = solver.NumVar(0, inf, f"x_{g}_{t}")
            B[g, t] = solver.NumVar(0, inf, f"B_{g}_{t}")
    prod, ot, out, inv = {}, {}, {}, {}
    a = {p: solver.NumVar(0, 1, f"a_{p}") for p in BID}
    use02 = solver.BoolVar("use_opt02")
    use01 = solver.BoolVar("use_opt01")
    BATCH = 20000.0

    ramp = [0.0 if t < 14 else min(1.0, (t - 13) / 4.0) for t in range(WEEKS)]
    for p in PRODUCTS:
        for t in range(WEEKS):
            prod[p, t] = solver.NumVar(0, inf, f"prod_{p}_{t}")
            ot[p, t] = solver.NumVar(0, 0.15 * inputs["cap_week"][p], f"ot_{p}_{t}")
            out[p, t] = solver.NumVar(0, 12000 if (p == "PL-02" and t >= 8) else 0, f"out_{p}_{t}")
            inv[p, t] = solver.NumVar(0, inf, f"I_{p}_{t}")
            if integer_batches:
                n = solver.IntVar(0, 400, f"n_{p}_{t}")
                solver.Add(prod[p, t] == BATCH * n)

    for g in G:
        d = inputs["groups"][g]["weekly_demand"]
        init_backlog = 2.0 * d
        for t in range(WEEKS):
            prev = init_backlog if t == 0 else B[g, t - 1]
            solver.Add(B[g, t] == prev + d - x[g, t])
            if t >= LATE_WEEKS:
                solver.Add(B[g, t] <= d * LATE_WEEKS)

    for p in PRODUCTS:
        gp = [g for g in G if g[1] == p]
        for t in range(WEEKS):
            ship_existing = solver.Sum(x[g, t] for g in gp)
            bid_qty = (a[p] * (BID[p][0] / 52.0) * ramp[t]) if p in BID else 0
            prev_inv = 0 if t == 0 else inv[p, t - 1]
            solver.Add(inv[p, t] == prev_inv + prod[p, t] - ship_existing - bid_qty)
            extra = 0
            if p == "PL-02":
                if t >= 7:
                    extra = extra + 600000 / 52.0 * use02
                if t >= 36:
                    extra = extra + 1600000 / 52.0 * use01
            solver.Add(prod[p, t] <= inputs["cap_week"][p] + ot[p, t] + out[p, t] + extra)

    obj = solver.Objective()
    for p, (annual, usd) in BID.items():
        unit_rev = usd * FX
        unit_cost = unit_rev * (1 - GPM[p])
        obj.SetCoefficient(a[p], weights["w_profit"] * annual * (unit_rev - unit_cost) * (1 - 14 / 52.0))
    for p in PRODUCTS:
        base_price = statistics.mean(v["unit_price"] for (c, pp), v in inputs["groups"].items() if pp == p)
        unit_cost = base_price * (1 - GPM[p])
        for t in range(WEEKS):
            obj.SetCoefficient(ot[p, t], -weights["w_premium"] * 0.5 * unit_cost)
            obj.SetCoefficient(out[p, t], -weights["w_premium"] * 0.35 * unit_cost)
    for g in G:
        price = inputs["groups"][g]["unit_price"]
        for t in range(WEEKS):
            obj.SetCoefficient(B[g, t], -weights["w_customer"] * 0.007 * price * 7)
    obj.SetCoefficient(use02, -weights["w_premium"] * 1_200_000_000)
    obj.SetCoefficient(use01, -weights["w_premium"] * 18_000_000_000)
    obj.SetMaximization()

    t0 = time.perf_counter()
    status = solver.Solve()
    wall = time.perf_counter() - t0
    names = {pywraplp.Solver.OPTIMAL: "OPTIMAL", pywraplp.Solver.FEASIBLE: "FEASIBLE", pywraplp.Solver.INFEASIBLE: "INFEASIBLE"}
    res = {
        "backend": backend,
        "status": names.get(status, str(status)),
        "wall_s": round(wall, 3),
        "vars": solver.NumVariables(),
        "constraints": solver.NumConstraints(),
        "integer_batches": integer_batches,
    }
    if status in (pywraplp.Solver.OPTIMAL, pywraplp.Solver.FEASIBLE):
        res["objective_krw"] = round(solver.Objective().Value())
        res["accept"] = {p: round(a[p].solution_value(), 3) for p in BID}
        res["opt02"] = int(round(use02.solution_value()))
        res["opt01"] = int(round(use01.solution_value()))
    return res


def cpu_benchmark_s() -> float:
    """고정 순수 파이썬 연산 시간: 로컬 대비 서버 CPU 속도 비율 추정용."""
    t0 = time.perf_counter()
    s = 0
    for i in range(3_000_000):
        s += i * i % 7
    return round(time.perf_counter() - t0, 3)


def container_limits() -> dict:
    def read(p: str) -> str | None:
        try:
            return Path(p).read_text().strip()
        except OSError:
            return None

    import os

    return {
        "os_cpu_count": os.cpu_count(),
        "cgroup_cpu_max": read("/sys/fs/cgroup/cpu.max"),
        "cgroup_memory_max": read("/sys/fs/cgroup/memory.max"),
    }


def run_suite(backends=("HIGHS", "SCIP"), int_time_limit_s: float = 10.0) -> dict:
    report = {"limits": container_limits(), "cpu_benchmark_s": cpu_benchmark_s(), "runs": []}
    for mode in ("H1_nameplate_per_month", "H3_calibrated_from_batchlog"):
        inputs = load_inputs(mode)
        for backend in backends:
            for preset in ("A_protect_customers", "B_max_profit"):
                r = build_and_solve(inputs, backend, PRESETS[preset], False, 30.0)
                r.update({"capacity_mode": mode, "preset": preset})
                report["runs"].append(r)
    inputs = load_inputs("H1_nameplate_per_month")
    for backend in backends:
        r = build_and_solve(inputs, backend, PRESETS["B_max_profit"], True, int_time_limit_s)
        r.update({"capacity_mode": "H1_nameplate_per_month", "preset": "B_max_profit"})
        report["runs"].append(r)
    return report
