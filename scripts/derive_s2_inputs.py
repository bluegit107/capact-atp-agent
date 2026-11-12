"""S2 솔버 입력(PL-02) 도출: 원본(수주·출하·재무·생산) -> 주 단위 납기 도래 수량, 단가, 원가, 용량 수치.

모두 DERIVED. 컨테이너는 원본 데이터룸을 갖지 않으므로 결과를 JSON 으로 고정해 둔다.
  - 백로그 납기 = 수주일 + 60일(관측 리드타임 중앙값). 이미 지난 납기는 주 0 에 '기지연'으로 합산(기지연 수량 별도 기록).
  - 신규 수주 예측 = 최근 13주(2026-04-01~) 평균 주간 수주, 수주 후 60일(=9주) 뒤 납기.
  - 단가 = 최근 3개월 금액/수량, 원가 = 2026-01~06 COGS/출하수량(재무제표 대비 출하 대사).
"""
from __future__ import annotations

import datetime as dt
import json
import math
import statistics
from collections import defaultdict

from _rawdata import COREVIA, ROOT, read_pipe_csv

AS_OF = dt.date(2026, 7, 1)
WEEK0 = dt.date(2026, 6, 29)  # as-of 가 속한 주의 월요일
HORIZON = 52
LEAD_DAYS = 60
LEAD_WEEKS = math.ceil(LEAD_DAYS / 7)  # 9
MAX_LEAD = 77
PRODUCT = "PL-02"
NAMED = ("C-101", "C-102")


def group_of(customer: str) -> str:
    return customer if customer in NAMED else "OTHERS"


def week_index(d: dt.date) -> int:
    return (d - WEEK0).days // 7


def main() -> dict:
    orders = [o for o in read_pipe_csv(COREVIA / "sales" / "sales_orders_cv.csv") if o["product_id"] == PRODUCT and o["customer_id"] != "NONE"]
    ships = {r["order_id"]: r for r in read_pipe_csv(COREVIA / "sales" / "sales_shipments_cv.csv")}

    due = defaultdict(lambda: [0.0] * HORIZON)
    overdue = defaultdict(float)
    backlog_units = defaultdict(float)
    for o in orders:
        if o["order_id"] in ships:
            continue
        age = (AS_OF - dt.date.fromisoformat(o["order_date"])).days
        if age > MAX_LEAD:
            continue  # STALE_UNKNOWN (AS-002) — 확정 약속 아님
        g = group_of(o["customer_id"])
        d = dt.date.fromisoformat(o["order_date"]) + dt.timedelta(days=LEAD_DAYS)
        w = week_index(d)
        q = int(o["order_qty"])
        backlog_units[g] += q
        if w < 0:
            overdue[g] += q
            w = 0
        due[g][min(w, HORIZON - 1)] += q

    recent = [o for o in orders if o["order_date"] >= "2026-04-01"]
    rate = defaultdict(float)
    amt = defaultdict(float)
    qty = defaultdict(float)
    for o in recent:
        g = group_of(o["customer_id"])
        rate[g] += int(o["order_qty"]) / 13.0
        amt[g] += int(o["order_amount_krw"])
        qty[g] += int(o["order_qty"])
    for g in rate:
        for t in range(LEAD_WEEKS, HORIZON):
            due[g][t] += rate[g]

    # 원가: 재무제표 COGS / 출하수량 (2026-01~06)
    inc = [r for r in read_pipe_csv(COREVIA / "financials" / "fin_income_statement_cv.csv") if r["product_id"] == PRODUCT and "2026-01" <= r["period_ym"] <= "2026-06"]
    cogs = sum(int(r["cogs_krw"]) for r in inc)
    rev = sum(int(r["revenue_krw"]) for r in inc)
    shipped = sum(
        int(s["shipped_qty"]) for s in ships.values() if s["product_id"] == PRODUCT and "2026-01" <= s["ship_date"][:7] <= "2026-06"
    )

    cap = next(r for r in read_pipe_csv(COREVIA / "production" / "strat_capacity_cv.csv") if r["product_id"] == PRODUCT)
    batches = read_pipe_csv(COREVIA / "production" / "prod_batch_log_cv.csv")
    batch_by_id = {b["batch_id"]: b for b in batches}

    # RFP 원가 기준: 원료 투입 원가(applied_unit_cost_krw) / 생산수량. 원료가 USD 면 환율 연동 단가로 분해.
    cons = [c for c in read_pipe_csv(COREVIA / "production" / "production_material_consumption_cv.csv")
            if c["product_id"] == PRODUCT and "2026-01" <= c["consumption_date"][:7] <= "2026-06"]
    mat_cost = sum(float(c["consumed_qty"]) * int(c["applied_unit_cost_krw"]) for c in cons)
    mat_kg = sum(float(c["consumed_qty"]) for c in cons)
    mat_units = sum(int(batch_by_id[b]["produced_qty"]) for b in {c["batch_id"] for c in cons})
    material_id = cons[0]["material_id"]
    mm = next(m for m in read_pipe_csv(COREVIA / "master" / "m_material_cv.csv") if m["material_id"] == material_id)
    pos = sorted((p for p in read_pipe_csv(COREVIA / "procurement" / "procurement_material_purchase_cv.csv") if p["material_id"] == material_id),
                 key=lambda p: p["purchase_date"])
    h1 = [p for p in pos if "2026-01" <= p["purchase_date"][:7] <= "2026-06"]

    # 품질: 확정 결함과 회수 기준(배치 확정 불량률 >= 10%, quality_policy_cv.md) 해당 배치
    claims = [q for q in read_pipe_csv(COREVIA / "quality" / "qc_claims_cv.csv") if q["product_id"] == PRODUCT and q["defect_confirmed"] == "True"]
    per_batch = defaultdict(int)
    for q in claims:
        per_batch[q["batch_id"]] += int(q["qty_affected"])
    recall_batches = [
        {"batch_id": b, "confirmed_defect_qty": n, "produced_qty": int(batch_by_id[b]["produced_qty"]),
         "defect_rate": round(n / int(batch_by_id[b]["produced_qty"]), 4), "input_lot_ids": batch_by_id[b]["input_lot_ids"],
         "qc_result": batch_by_id[b]["qc_result"]}
        for b, n in sorted(per_batch.items()) if b in batch_by_id and n / int(batch_by_id[b]["produced_qty"]) >= 0.10
    ]
    prod_12m = sum(int(b["produced_qty"]) for b in batches if b["product_id"] == PRODUCT and b["production_date"] >= "2025-07")
    conf_12m = sum(int(q["qty_affected"]) for q in claims if q["claim_date"] >= "2025-07")
    pm = defaultdict(int)
    for b in batches:
        if b["product_id"] == PRODUCT:
            pm[b["production_date"][:7]] += int(b["produced_qty"])
    months = sorted(pm)[-6:]
    observed_month = statistics.mean(pm[m] for m in months)
    util = float(cap["utilization_pct"]) / 100

    out = {
        "provenance": "DERIVED",
        "product_id": PRODUCT,
        "as_of": AS_OF.isoformat(),
        "week0_monday": WEEK0.isoformat(),
        "horizon_weeks": HORIZON,
        "lead_days": LEAD_DAYS,
        "groups": {
            g: {
                "weekly_due": [round(x, 1) for x in due[g]],
                "backlog_units_confirmed": backlog_units[g],
                "overdue_at_asof_units": overdue[g],
                "forecast_weekly_orders": round(rate[g], 1),
                "unit_price_krw": round(amt[g] / qty[g], 1),
            }
            for g in sorted(due)
        },
        "orders_weekly_steady": round(sum(rate.values()), 1),  # 규모 대응비 분모(수주 기준 주간 신규 수요)
        "material": {
            "material_id": material_id, "currency": mm["currency"], "supplier_id": mm["supplier_id"],
            "exclusive": mm["is_exclusive"] == "True", "contract_type": mm["contract_type"],
            "cost_per_ea_krw_2026h1": round(mat_cost / mat_units, 1),
            "kg_per_ea_2026h1": round(mat_kg / mat_units, 6),
            "usd_per_kg_2026h1_avg": round(sum(float(p["unit_price_fx"]) for p in h1) / len(h1), 4),
            "usd_per_kg_latest": float(pos[-1]["unit_price_fx"]), "latest_purchase_date": pos[-1]["purchase_date"],
            "basis": "rfp_c201.md: 원가 기준 = production_material_consumption_cv 의 applied_unit_cost_krw",
        },
        "conversion_cost_per_ea_krw": round(cogs / shipped - mat_cost / mat_units, 1),  # COGS/EA - 원료/EA (가공비 근사)
        "quality": {
            "confirmed_defect_rate_12m": round(conf_12m / prod_12m, 5),
            "recall_threshold": 0.10,
            "recall_eligible_batches": recall_batches,
            "recall_eligible_units": sum(b["produced_qty"] for b in recall_batches),
            "decision_owner": "품질위원회(approval_authority_policy.md)",
        },
        "unit_cost_krw": round(cogs / shipped, 1),
        "unit_cost_basis": {"cogs_krw": cogs, "shipped_qty": shipped, "revenue_krw": rev, "period": "2026-01..2026-06", "gpm_pct": round(100 * (1 - cogs / rev), 1)},
        "capacity": {
            "line_id": cap["line_id"],
            "capacity_qty": int(cap["capacity_qty"]),
            "utilization_pct": float(cap["utilization_pct"]),
            "observed_monthly_production_avg": round(observed_month),
            "h3_implied_monthly_capacity": round(observed_month / util),
            "observed_weekly_demand_orders": round(sum(rate.values()), 1),
        },
        "source_ref": ["sales_orders_cv.csv", "sales_shipments_cv.csv", "fin_income_statement_cv.csv", "strat_capacity_cv.csv", "prod_batch_log_cv.csv"],
    }
    path = ROOT / "deal-cockpit" / "extensions" / "derived" / "s2_pl02_commitments.json"
    path.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    return out


if __name__ == "__main__":
    r = main()
    for g, v in r["groups"].items():
        print(f"{g:7} backlog={v['backlog_units_confirmed']:>9,.0f} overdue={v['overdue_at_asof_units']:>8,.0f} fc/wk={v['forecast_weekly_orders']:>9,.1f} price={v['unit_price_krw']:>9,.1f} due[0..11]={[round(x/1000) for x in v['weekly_due'][:12]]}k")
    print("unit_cost", r["unit_cost_krw"], r["unit_cost_basis"])
    print("capacity", r["capacity"])
    wk = sum(r["groups"][g]["forecast_weekly_orders"] for g in r["groups"])
    print("총 신규 주간 수요", round(wk), " / H3 주간 능력", round(r["capacity"]["h3_implied_monthly_capacity"] / 4.333), " / 명목(H1) 주간", round(r["capacity"]["capacity_qty"] / 4.333))
