"""AS-001 증거 점검: strat_capacity_cv.capacity_qty 의 기간 단위(월/연)를 단정하지 않고 증거로 제안한다.

가설
  H_MONTH: capacity_qty 는 월 생산능력  -> 기대 월 생산량 = capacity * utilization
  H_YEAR : capacity_qty 는 연 생산능력  -> 기대 월 생산량 = capacity * utilization / 12
관측: prod_batch_log_cv 의 제품별 월 생산량(최근 6개월 평균). 라인과 제품은 strat_capacity 의 product_id 로 연결.
출력: 라인별 상대오차와 '제안' 단위. 라인마다 결과가 다르면 단위가 일관되지 않다는 증거 그 자체다.
"""
from __future__ import annotations

import json
import statistics
from collections import defaultdict

from _rawdata import COREVIA, ROOT, read_pipe_csv

LAST_MONTHS = 6


def main() -> dict:
    cap = read_pipe_csv(COREVIA / "production" / "strat_capacity_cv.csv")
    batches = read_pipe_csv(COREVIA / "production" / "prod_batch_log_cv.csv")
    orders = read_pipe_csv(COREVIA / "sales" / "sales_orders_cv.csv")

    prod_month: dict[tuple[str, str], int] = defaultdict(int)
    for b in batches:
        prod_month[(b["product_id"], b["production_date"][:7])] += int(b["produced_qty"])
    order_month: dict[tuple[str, str], int] = defaultdict(int)
    for o in orders:
        order_month[(o["product_id"], o["order_date"][:7])] += int(o["order_qty"])

    months = sorted({ym for _, ym in prod_month})[-LAST_MONTHS:]
    lines = []
    for r in cap:
        pid = r["product_id"]
        capacity = int(r["capacity_qty"])
        util = float(r["utilization_pct"]) / 100
        observed = statistics.mean(prod_month[(pid, ym)] for ym in months)
        ordered = statistics.mean(order_month[(pid, ym)] for ym in months)
        exp_month = capacity * util
        exp_year = capacity * util / 12
        err_month = abs(observed - exp_month) / exp_month
        err_year = abs(observed - exp_year) / exp_year
        best = "per_month" if err_month < err_year else "per_year"
        confident = min(err_month, err_year) < 0.25
        # H3(보정): 실측 월 생산 ÷ 가동률 = 월 환산 능력. capacity_qty 와 다르면 단위가 아니라 값 자체가 불일치.
        implied_monthly_capacity = observed / util
        lines.append(
            {
                "line_id": r["line_id"],
                "product_id": pid,
                "capacity_qty": capacity,
                "utilization_pct": float(r["utilization_pct"]),
                "observed_monthly_production_avg": round(observed),
                "observed_monthly_orders_avg": round(ordered),
                "rel_error_if_per_month": round(err_month, 3),
                "rel_error_if_per_year": round(err_year, 3),
                "evidence_suggests": best if confident else "inconclusive",
                "h3_implied_monthly_capacity_from_observed": round(implied_monthly_capacity),
                "h3_ratio_to_capacity_qty": round(implied_monthly_capacity / capacity, 3),
                "provenance": "DERIVED",
                "source_ref": [
                    "strat_capacity_cv.csv",
                    "prod_batch_log_cv.csv",
                    "sales_orders_cv.csv",
                ],
            }
        )

    verdicts = {l["evidence_suggests"] for l in lines}
    result = {
        "assumption_id": "AS-001",
        "observation_window_months": months,
        "lines": lines,
        "consistent_across_lines": len(verdicts) == 1 and "inconclusive" not in verdicts,
        "status": "PROPOSED_NEEDS_OWNER_CONFIRMATION",
        "owner_role": "생산기술",
        "note": (
            "라인별 증거가 한 방향으로 모이지 않으면 시스템은 단위를 단정하지 않는다. "
            "확인 요청을 생성하고, 해석별 해를 비교해 추천안이 같으면 진행 / 다르면 상신 차단한다."
        ),
    }
    out = ROOT / "deal-cockpit" / "extensions" / "derived" / "as001_capacity_unit_evidence.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


if __name__ == "__main__":
    res = main()
    print(f"{'line':5} {'prod':6} {'capacity':>10} {'util%':>6} {'obs/mo':>9} {'err(mo)':>8} {'err(yr)':>8}  suggests")
    for l in res["lines"]:
        print(
            f"{l['line_id']:5} {l['product_id']:6} {l['capacity_qty']:>10,} {l['utilization_pct']:>6} "
            f"{l['observed_monthly_production_avg']:>9,} {l['rel_error_if_per_month']:>8} "
            f"{l['rel_error_if_per_year']:>8}  {l['evidence_suggests']}"
        )
    print("consistent_across_lines:", res["consistent_across_lines"])
