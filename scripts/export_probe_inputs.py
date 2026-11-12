"""solver_probe 용 고정 입력(집계 JSON)을 원본에서 추출한다. 컨테이너는 원본 데이터룸을 갖지 않는다."""
from __future__ import annotations

import json
import statistics
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _rawdata import COREVIA, ROOT, read_pipe_csv  # noqa: E402

PRODUCTS = ["PL-01", "PL-02", "PL-03", "PL-04", "PL-05", "PL-06"]


def main() -> None:
    cap_rows = {r["product_id"]: r for r in read_pipe_csv(COREVIA / "production" / "strat_capacity_cv.csv")}
    batches = read_pipe_csv(COREVIA / "production" / "prod_batch_log_cv.csv")
    orders = read_pipe_csv(COREVIA / "sales" / "sales_orders_cv.csv")

    prod_month = defaultdict(int)
    for b in batches:
        prod_month[(b["product_id"], b["production_date"][:7])] += int(b["produced_qty"])
    months = sorted({ym for _, ym in prod_month})[-6:]

    qty_g, amt_g = defaultdict(int), defaultdict(int)
    for o in orders:
        if o["order_date"] >= "2026-04-01" and o["customer_id"] != "NONE":
            qty_g[(o["customer_id"], o["product_id"])] += int(o["order_qty"])
            amt_g[(o["customer_id"], o["product_id"])] += int(o["order_amount_krw"])
    groups = [
        {"customer_id": c, "product_id": p, "weekly_demand": q / 13.0, "unit_price": amt_g[(c, p)] / q}
        for (c, p), q in sorted(qty_g.items())
    ]

    out = {}
    for mode in ("H1_nameplate_per_month", "H3_calibrated_from_batchlog"):
        cap_week = {}
        for p in PRODUCTS:
            r = cap_rows[p]
            util = float(r["utilization_pct"]) / 100
            if mode.startswith("H1"):
                cap_week[p] = int(r["capacity_qty"]) / 4.333
            else:
                cap_week[p] = statistics.mean(prod_month[(p, m)] for m in months) / util / 4.333
        out[mode] = {"cap_week": cap_week, "groups": groups}

    path = ROOT / "deal-cockpit" / "backend" / "src" / "dealcockpit" / "adapters" / "solver_probe_inputs.json"
    path.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print("wrote", path, "groups:", len(groups))


if __name__ == "__main__":
    main()
