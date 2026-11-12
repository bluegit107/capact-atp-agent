"""W05 약속 가능 물량(ATP) 계산 엔진 — 순수 함수(같은 입력 → 같은 결과, AI 호출 없음).

W05 지시서 7장의 식을 그대로 구현한다. 모든 결과는 Metric(값·단위·출신·식·근거)으로 기록해 근거 드로어(3단)로 역추적한다.
라인 능력은 capacity_qty 를 쓰지 않고 2026 상반기 생산 실적 ÷ 26주 ÷ 가동률로 역산한다(가정 A-03).
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date, timedelta

ENGINE_VERSION = "w05-atp-1"
HORIZON_WEEKS = 104
LEDGER_START = date(2026, 6, 29)  # 기본 데이터의 원장 시작 주(참고용). 실제 값은 ledger_start(inp)
# 구성(라인·제품·설비안)은 코드가 아니라 데이터에서 읽는다(topology). 라인·제품·설비안 행이 늘어도 코드 수정 없이 계산된다.
PROTECT_DAYS, EXPECT_DAYS = 77, 60


def topology(inp: dict) -> dict:
    """데이터 기반 구성: 라인(공장·제품·capacity_qty), 제품별 라인 목록, 설비안(대상 라인·제품·선행 요건), 입찰 제품.
    한 제품을 여러 라인이 만들면 실적은 capacity_qty 비율로 나눈다. 전환·신설 설비안은 가상 라인으로 만든다."""
    lines = {r["line_id"]: {"plant": r["plant_id"], "product_id": r["product_id"], "capacity_qty": float(r["capacity_qty"]), "virtual": False} for r in inp["capacity"]}
    capex = {}
    for r in inp["capex"]:
        ln, pid = r.get("target_line_id"), r.get("product_id")
        if ln and ln not in lines:
            lines[ln] = {"plant": ln.split("-")[0], "product_id": pid, "capacity_qty": 0.0, "virtual": True}
        capex[r["option_id"]] = {"line_id": ln, "product_id": pid, "requires": r.get("requires", [])}
    by_product: dict[str, list[str]] = {}
    for ln, m in lines.items():
        by_product.setdefault(m["product_id"], []).append(ln)
    names = inp.get("products", {})
    label = {ln: f"{ln} ({m['plant']} {names.get(m['product_id'], m['product_id'])} 라인{' 신설' if m['virtual'] else ''})" for ln, m in lines.items()}
    return {"lines": lines, "by_product": by_product, "capex": capex, "bid_products": [x["product_id"] for x in inp["seeds"]["rfp_lines"]], "label": label}


def primary_line(inp: dict, product: str) -> str | None:
    """주문이 어느 라인에 실리는지(주문에는 라인이 없음): 그 제품의 실제(가상 아님) 라인 중 첫째."""
    t = topology(inp)
    return next((ln for ln in t["by_product"].get(product, []) if not t["lines"][ln]["virtual"]), None)


def monday(d: date) -> date:
    return d - timedelta(days=d.weekday())


@dataclass
class Metric:
    id: int
    key: str
    value: float
    unit: str
    source_type: str  # ORIGINAL | DERIVED | ASSUMED | MOCK_REPLY
    formula_key: str
    dims: dict = field(default_factory=dict)
    inputs: list = field(default_factory=list)  # [{"calc_result_id": n} | {"assumption_key": k}]
    sources: list = field(default_factory=list)  # [{"file","rows","kind"} | {"file","kind":"aggregate","filter","agg","row_count","sample_rows"}]

    def to_dict(self) -> dict:
        return {"id": self.id, "key": self.key, "value": self.value, "unit": self.unit, "source_type": self.source_type, "formula_key": self.formula_key,
                "dims": self.dims, "lineage": {"inputs": self.inputs, "sources": self.sources}}


class Calc:
    """계산 실행 1회의 결과 모음. id 는 기록 순서(결정적)."""

    def __init__(self, inputs_desc: dict):
        self.inputs_desc = inputs_desc
        self.metrics: list[Metric] = []

    def put(self, key, value, unit, formula_key, source_type="DERIVED", dims=None, inputs=None, sources=None) -> Metric:
        m = Metric(len(self.metrics) + 1, key, value, unit, source_type, formula_key, dims or {}, inputs or [], sources or [])
        self.metrics.append(m)
        return m

    def get(self, key: str, **dims) -> Metric:
        return next(m for m in self.metrics if m.key == key and all(m.dims.get(k) == v for k, v in dims.items()))


# ── 7.1 라인별 주간 능력 ─────────────────────────────────────────────────────────
def line_capacity(inp: dict, calc: Calc) -> dict[str, dict]:
    files = inp["files"]
    out = {}
    topo = topology(inp)
    targets = [(prod, ln) for prod in topo["bid_products"] for ln in topo["by_product"].get(prod, []) if not topo["lines"][ln]["virtual"]]
    for prod, line in targets:
        row = next(r for r in inp["capacity"] if r["line_id"] == line)
        sibl = [x for x in topo["by_product"][prod] if not topo["lines"][x]["virtual"]]
        share = topo["lines"][line]["capacity_qty"] / sum(topo["lines"][x]["capacity_qty"] for x in sibl) if len(sibl) > 1 else 1.0
        util = calc.put("utilization_pct", float(row["utilization_pct"]), "pct", "original_constant", "ORIGINAL", {"line_id": line},
                        sources=[{"file": files["capacity"], "rows": [row["_row"]], "kind": "single"}])
        ph = inp["production_h1"][prod]
        prod_m = calc.put("production_h1", ph["qty"], "EA", "sum_produced_qty_h1", "DERIVED", {"product_id": prod},
                          sources=[{"file": files["batch"], "kind": "aggregate", "filter": {"product_id": prod, "production_date": ["2026-01-01", "2026-06-30"]},
                                    "agg": "sum(produced_qty)", "row_count": ph["row_count"], "sample_rows": ph["sample_rows"]}])
        run = ph["qty"] / 26.0 * share
        full = run / (util.value / 100.0)
        r = calc.put("run_rate", run, "EA/week", "run_rate", "DERIVED", {"line_id": line}, inputs=[{"calc_result_id": prod_m.id}, {"assumption_key": "line_capacity_basis"}])
        f = calc.put("full_capacity", full, "EA/week", "full_capacity", "DERIVED", {"line_id": line}, inputs=[{"calc_result_id": r.id}, {"calc_result_id": util.id}])
        hd = calc.put("line_weekly_headroom", full - run, "EA/week", "headroom", "DERIVED", {"line_id": line}, inputs=[{"calc_result_id": f.id}, {"calc_result_id": r.id}])
        out[line] = {"product_id": prod, "util": util.value, "run_rate": run, "full": full, "headroom": full - run, "ids": {"run": r.id, "full": f.id, "headroom": hd.id, "util": util.id}}
    f1 = calc.put("f1_weighted_util_pct", inp["f1_weighted_util_pct"], "pct", "capacity_weighted_util", "DERIVED", {"plant": "F1"},
                  sources=[{"file": files["capacity"], "kind": "aggregate", "filter": {"plant_id": "F1"}, "agg": "sum(capacity_qty*utilization_pct)/sum(capacity_qty)",
                            "row_count": len([r for r in inp["capacity"] if r["plant_id"] == "F1"]), "sample_rows": [r["_row"] for r in inp["capacity"] if r["plant_id"] == "F1"][:5]}])
    out["_f1_util_id"] = f1.id
    return out


# ── 7.2 기존 약속 ───────────────────────────────────────────────────────────────
def commitments(inp: dict, as_of: date) -> list[dict]:
    out = []
    line_of = {p: primary_line(inp, p) for p in {o["product_id"] for o in inp["open_orders"]}}
    for o in inp["open_orders"]:
        od = date.fromisoformat(o["order_date"])
        status = "STATUS_UNKNOWN" if od + timedelta(days=PROTECT_DAYS) < as_of else "PROTECTED"
        exp = max(od + timedelta(days=EXPECT_DAYS), as_of)
        out.append({**o, "line_id": line_of.get(o["product_id"]), "status": status, "expected_ship_week": monday(exp).isoformat(),
                    "earliest_ship_on": (od + timedelta(days=45)).isoformat(), "latest_ship_on": (od + timedelta(days=PROTECT_DAYS)).isoformat()})
    return out


# ── 7.3 주별 약속 가능 물량 ─────────────────────────────────────────────────────
def capex_options(inp: dict) -> dict[str, dict]:
    topo = topology(inp)
    return {r["option_id"]: {"capex_krw": int(r["capex_krw"]), "lead_time_weeks": int(r["lead_time_weeks"]), "added_capacity_qty": int(r["added_capacity_qty"]),
                             "_row": r["_row"], "note": r["note"], **topo["capex"][r["option_id"]]} for r in inp["capex"]}


def ready_week(opt: dict, decision_date: date) -> date:
    return monday(decision_date + timedelta(days=7 * opt["lead_time_weeks"]))


def _calendar(inp: dict, key: str, default: int) -> int:
    return int(next((c["value"] for c in inp.get("seeds", {}).get("calendar", []) if c.get("key") == key), default))


def as_of(inp: dict) -> date:
    """데이터 기준일. 다른 데이터셋은 입력 파일의 as_of 만 바꾸면 원장 시작, 마감, 결정일이 함께 움직인다."""
    return date.fromisoformat(inp.get("as_of", "2026-07-01"))


def ledger_start(inp: dict) -> date:
    return monday(as_of(inp))


def decision_date(inp: dict) -> date:
    """설비투자 결정일 = 기준일 + 이사회 간격(데이터 사전 'As-of + 5일')."""
    return as_of(inp) + timedelta(days=_calendar(inp, "board_offset_days", 5))


def submission_deadline(inp: dict) -> date:
    """입찰 제출 마감 = 기준일 + 21일(가정 A-01: RFP 는 '마감 3주'만 적음)."""
    return as_of(inp) + timedelta(days=_calendar(inp, "submission_deadline_days", 21))


def ledger(inp: dict, caps: dict, comm: list[dict], capex_ids: tuple[str, ...], decision_date: date, demand_factor: float = 1.0,
           reserved: dict | None = None, c101_confirmed: bool = False) -> dict:
    """라인 × 주 원장. demand_factor 가 1.0 이 아니려면 c101_demand_scenario 가 확정이어야 한다(G7)."""
    if demand_factor != 1.0 and not c101_confirmed:
        raise ValueError("G7: C-101 감소분은 영업 임원이 c101_demand_scenario 를 확정하기 전에는 여력에 넣을 수 없습니다")
    weeks = [ledger_start(inp) + timedelta(weeks=i) for i in range(HORIZON_WEEKS)]
    idx = {w: i for i, w in enumerate(weeks)}
    opts = capex_options(inp)
    reserved = reserved or {}
    rows: dict[str, list[dict]] = {}
    topo = topology(inp)
    for line in [ln for p in topo["bid_products"] for ln in topo["by_product"].get(p, [])]:
        if topo["lines"][line]["virtual"] and not any(opts[o]["line_id"] == line for o in capex_ids):
            continue
        open_ = [0.0] * HORIZON_WEEKS
        if line in caps:
            for c in comm:
                if c["status"] == "PROTECTED" and c["line_id"] == line:
                    i = idx.get(date.fromisoformat(c["expected_ship_week"]))
                    if i is not None:
                        open_[i] += c["qty"]
        out = []
        for i, w in enumerate(weeks):
            full = caps[line]["full"] if line in caps else 0.0
            base = max(caps[line]["run_rate"] * demand_factor, open_[i]) if line in caps else 0.0
            added = sum(opts[o]["added_capacity_qty"] / 52.0 for o in capex_ids if opts[o]["line_id"] == line and w >= ready_week(opts[o], decision_date))
            res = reserved.get((line, w.isoformat()), 0.0)
            out.append({"week": w.isoformat(), "full_capacity": full, "baseline_load": base, "confirmed_open_qty": open_[i], "added_capacity": added,
                        "reserved_qty": res, "atp": max(full + added - base - res, 0.0), "overload": max(base - full - added, 0.0)})
        rows[line] = out
    return {"weeks": [w.isoformat() for w in weeks], "lines": rows, "product_lines": {p: [ln for ln in topo["by_product"].get(p, []) if ln in rows] for p in topo["bid_products"]}}


def product_atp(led: dict, product: str) -> list[float]:
    lines = led.get("product_lines", {}).get(product, [])
    return [sum(led["lines"][ln][i]["atp"] for ln in lines if ln in led["lines"]) for i in range(HORIZON_WEEKS)]


# ── 7.4 C-201 수요와 충족률 ─────────────────────────────────────────────────────
def evaluate_product(led: dict, product: str, offered_qty: float, supply_start: date) -> dict:
    atp = product_atp(led, product)
    i0 = led["weeks"].index(monday(supply_start).isoformat())
    span = range(i0, min(i0 + 52, HORIZON_WEEKS))
    demand = offered_qty / 52.0
    fill = [min(atp[i], demand) for i in span]
    fill_rate = (sum(fill) / (demand * len(fill))) if demand > 0 else 1.0
    max_off = min(atp[i] for i in span) * 52
    first_full = None
    for k in range(len(fill)):
        if all(f >= demand - 1e-6 for f in fill[k:]):
            first_full = led["weeks"][span[k]]
            break
    unmet = [{"week": led["weeks"][i], "qty": demand - f} for i, f in zip(span, fill)]
    return {"fill_rate": fill_rate, "max_offerable": max_off, "first_full_week": first_full, "weekly_demand": demand, "unmet": unmet}


def naive_annual_check(caps: dict, inp: dict, product: str, qty: float, capex_ids: tuple[str, ...]) -> bool:
    """원장 없이 연간 총량만 비교(대조용): 연 여력 + 설비안 추가 능력 ≥ 요구."""
    opts = capex_options(inp)
    head = sum(c["headroom"] for ln, c in caps.items() if not ln.startswith("_") and c["product_id"] == product)
    annual = head * 52 + sum(opts[o]["added_capacity_qty"] for o in capex_ids if opts[o]["product_id"] == product)
    return annual >= qty


# ── 7.5 손익 ───────────────────────────────────────────────────────────────────
def financials(inp: dict, offered: dict[str, float], fx: float, price_cut: float, fx_drop: float, unmet: dict[str, list] | None = None) -> dict:
    lines = {s["product_id"]: s for s in inp["seeds"]["rfp_lines"]}
    rev = sum(q * lines[p]["unit_price_usd"] * (1 - price_cut) * fx * (1 - fx_drop) for p, q in offered.items())
    mat = sum(q * inp["material_cost_q2"][p]["krw_per_ea"] for p, q in offered.items())
    pen_month: dict[str, float] = {}
    for p, us in (unmet or {}).items():
        unit_krw = lines[p]["unit_price_usd"] * (1 - price_cut) * fx * (1 - fx_drop)
        for u in us:
            m = u["week"][:7]  # 주의 월요일이 속한 달에 귀속
            pen_month[m] = pen_month.get(m, 0.0) + u["qty"] * unit_krw * 0.03
    return {"revenue_krw": rev, "material_krw": mat, "margin_krw": rev - mat, "penalty_by_month": pen_month, "penalty_52w_krw": sum(pen_month.values())}


def round_down_10k(q: float) -> int:
    return int(math.floor(q / 10000.0) * 10000)
