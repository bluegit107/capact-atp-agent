"""기존 고객 '약속'의 원본 근거 도출: 수주(order_date) vs 출하(ship_date).

- 암묵 납기 = 수주일 + 관측 리드타임 분포(45~77일). 계약서 납기 필드는 원본에 없다.
- 출하 미연결 수주 중 '리드타임 상한 이내(정상 진행)'만 확정 백로그(BACKLOG).
- 그 외(리드타임 상한을 넘겨도 출하 없음)는 누락/취소 구분 불가 -> STALE_UNKNOWN, AS-002 확인 요청.
임의 삭제 금지. 기준일(as-of) 2026-07-01.
"""
from __future__ import annotations

import datetime as dt
import json
import statistics
from collections import defaultdict

from _rawdata import COREVIA, ROOT, read_pipe_csv

AS_OF = dt.date(2026, 7, 1)


def main() -> dict:
    orders = read_pipe_csv(COREVIA / "sales" / "sales_orders_cv.csv")
    ships = {r["order_id"]: r for r in read_pipe_csv(COREVIA / "sales" / "sales_shipments_cv.csv")}

    lead = [
        (dt.date.fromisoformat(ships[o["order_id"]]["ship_date"]) - dt.date.fromisoformat(o["order_date"])).days
        for o in orders
        if o["order_id"] in ships
    ]
    lead_min, lead_med, lead_max = min(lead), statistics.median(lead), max(lead)

    backlog = defaultdict(lambda: {"orders": 0, "qty": 0, "amount_krw": 0})
    stale = defaultdict(lambda: {"orders": 0, "qty": 0, "amount_krw": 0})
    for o in orders:
        if o["order_id"] in ships:
            continue
        age = (AS_OF - dt.date.fromisoformat(o["order_date"])).days
        bucket = backlog if age <= lead_max else stale
        key = (o["customer_id"], o["product_id"])
        bucket[key]["orders"] += 1
        bucket[key]["qty"] += int(o["order_qty"])
        bucket[key]["amount_krw"] += int(o["order_amount_krw"])

    def pack(d: dict) -> list[dict]:
        return [
            {"customer_id": c, "product_id": p, **v}
            for (c, p), v in sorted(d.items())
        ]

    def total(d: dict) -> dict:
        return {k: sum(v[k] for v in d.values()) for k in ("orders", "qty", "amount_krw")}

    result = {
        "as_of": AS_OF.isoformat(),
        "lead_time_days": {"min": lead_min, "median": lead_med, "max": lead_max, "shipped_orders": len(lead)},
        "backlog_confirmed": {"status": "BACKLOG", "total": total(backlog), "rows": pack(backlog)},
        "stale_unknown": {
            "status": "STALE_UNKNOWN",
            "assumption_id": "AS-002",
            "owner_role": "영업지원",
            "total": total(stale),
            "rows": pack(stale),
        },
        "provenance": "DERIVED",
        "source_ref": ["sales_orders_cv.csv", "sales_shipments_cv.csv"],
    }
    out = ROOT / "deal-cockpit" / "extensions" / "derived" / "as002_backlog_and_stale_orders.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


if __name__ == "__main__":
    r = main()
    print("lead time (days):", r["lead_time_days"])
    print("확정 백로그:", r["backlog_confirmed"]["total"])
    print("STALE_UNKNOWN:", r["stale_unknown"]["total"])
    by_prod: dict[str, int] = defaultdict(int)
    for row in r["backlog_confirmed"]["rows"]:
        by_prod[row["product_id"]] += row["qty"]
    print("백로그 수량(제품):", dict(by_prod))
