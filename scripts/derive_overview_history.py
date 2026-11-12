"""대시보드·거래처 이력용 도출 자료(DERIVED). 원본은 읽기 전용, 결과는 extensions/derived/ 에 저장.

- company_overview.json: 월별 생산(제품별), 라인 용량·가동률, 월별 수주·출하 매출(전사).
- customer_history.json: 거래처별 기본 조건(마스터)·월별 수주/출하·주문 이력(최근 30건)·리드타임·
  백로그/상태 불명·품질 클레임·미수금·계약서 원문 유무.
기준일(as-of) 2026-07-01. 백로그 판정은 derive_backlog.py 와 같은 규칙(관측 리드타임 상한 이내 미출하).
"""
from __future__ import annotations

import datetime as dt
import json
import statistics
from collections import defaultdict

from _rawdata import COREVIA, ROOT, read_pipe_csv

AS_OF = dt.date(2026, 7, 1)
OUT = ROOT / "deal-cockpit" / "extensions" / "derived"
CONTRACT_DOCS = {"C-101": "CT-C101", "C-102": "CT-C102"}  # 합성 계약서(SYNTHETIC) — 나머지 고객은 원문 없음


def ym(d: str) -> str:
    return d[:7]


def main() -> None:
    cust = read_pipe_csv(COREVIA / "master" / "m_customer_cv.csv")
    prods = {r["product_id"]: r["product_name"] for r in read_pipe_csv(COREVIA / "master" / "m_product_cv.csv")}
    orders = read_pipe_csv(COREVIA / "sales" / "sales_orders_cv.csv")
    ships = {r["order_id"]: r for r in read_pipe_csv(COREVIA / "sales" / "sales_shipments_cv.csv")}
    summary = read_pipe_csv(COREVIA / "sales" / "sales_customer_summary_cv.csv")
    claims = read_pipe_csv(COREVIA / "quality" / "qc_claims_cv.csv")
    ar = read_pipe_csv(COREVIA / "financials" / "fin_ar_aging_cv.csv")
    batches = read_pipe_csv(COREVIA / "production" / "prod_batch_log_cv.csv")
    cap = read_pipe_csv(COREVIA / "production" / "strat_capacity_cv.csv")

    leads = [(dt.date.fromisoformat(ships[o["order_id"]]["ship_date"]) - dt.date.fromisoformat(o["order_date"])).days
             for o in orders if o["order_id"] in ships]
    lead_max = max(leads)

    # ── 전사 개요 ───────────────────────────────────────────────────────────────
    prod_m: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for b in batches:
        prod_m[ym(b["production_date"])][b["product_id"]] += int(b["produced_qty"])
    sales_m: dict[str, dict[str, int]] = defaultdict(lambda: {"booking_krw": 0, "shipment_krw": 0})
    for r in summary:
        sales_m[r["period_ym"]]["booking_krw"] += int(r["booking_amount_krw"])
        sales_m[r["period_ym"]]["shipment_krw"] += int(r["shipment_revenue_krw"])
    overview = {
        "as_of": AS_OF.isoformat(), "provenance": "DERIVED",
        "sources": ["prod_batch_log_cv.csv", "strat_capacity_cv.csv", "sales_customer_summary_cv.csv", "m_product_cv.csv"],
        "products": prods,
        "production_monthly": [{"month": m, **{p: prod_m[m].get(p, 0) for p in prods}} for m in sorted(prod_m)],
        "lines": [{"plant": r["plant_id"], "line_id": r["line_id"], "product_id": r["product_id"], "capacity_qty": int(r["capacity_qty"]),
                   "utilization_pct": float(r["utilization_pct"]), "snapshot": r["snapshot_date"]} for r in cap],
        "sales_monthly": [{"month": m, **v} for m, v in sorted(sales_m.items())],
        "lead_time_days": {"min": min(leads), "median": statistics.median(leads), "max": lead_max},
    }

    # ── 거래처 이력 ─────────────────────────────────────────────────────────────
    by_c: dict[str, list[dict]] = defaultdict(list)
    for o in orders:
        by_c[o["customer_id"]].append(o)
    customers = []
    for c in cust:
        cid = c["customer_id"]
        os_ = sorted(by_c[cid], key=lambda o: o["order_date"])
        lt, backlog, stale, by_prod = [], {"orders": 0, "qty": 0, "amount_krw": 0}, {"orders": 0, "qty": 0, "amount_krw": 0}, defaultdict(lambda: {"orders": 0, "qty": 0, "amount_krw": 0})
        recent = []
        for o in os_:
            q, amt = int(o["order_qty"]), int(o["order_amount_krw"])
            bp = by_prod[o["product_id"]]
            bp["orders"] += 1; bp["qty"] += q; bp["amount_krw"] += amt
            s = ships.get(o["order_id"])
            if s:
                d = (dt.date.fromisoformat(s["ship_date"]) - dt.date.fromisoformat(o["order_date"])).days
                lt.append(d)
                status, ship_date, lead = "출하", s["ship_date"], d
            else:
                age = (AS_OF - dt.date.fromisoformat(o["order_date"])).days
                bucket = backlog if age <= lead_max else stale
                bucket["orders"] += 1; bucket["qty"] += q; bucket["amount_krw"] += amt
                status, ship_date, lead = ("진행" if age <= lead_max else "상태 불명"), None, None
            recent.append({"order_id": o["order_id"], "date": o["order_date"], "product_id": o["product_id"], "qty": q, "amount_krw": amt,
                           "status": status, "ship_date": ship_date, "lead_days": lead, "team": o["sales_team"]})
        monthly = sorted(({"month": r["period_ym"], "booking_krw": int(r["booking_amount_krw"]), "shipment_krw": int(r["shipment_revenue_krw"])}
                          for r in summary if r["customer_id"] == cid), key=lambda x: x["month"])
        last3 = sum(m["booking_krw"] for m in monthly[-3:])
        prev3 = sum(m["booking_krw"] for m in monthly[-6:-3])
        yoy3 = sum(m["booking_krw"] for m in monthly[-15:-12])
        cl = [{"claim_id": r["claim_id"], "date": r["claim_date"], "product_id": r["product_id"], "batch_id": r["batch_id"], "lot_id": r["lot_id"],
               "type": r["claim_type"], "confirmed": r["defect_confirmed"] == "True", "qty": int(r["qty_affected"]), "status": r["status"]}
              for r in claims if r["customer_id"] == cid]
        ar_c = [{"ar_id": r["ar_id"], "invoice_date": r["invoice_date"], "due_date": r["due_date"], "amount_krw": int(r["amount_krw"]),
                 "outstanding_krw": int(r["outstanding_krw"]), "aging_days": int(r["aging_days"])} for r in ar if r["customer_id"] == cid]
        customers.append({
            "customer_id": cid, "name": c["customer_name"], "channel": c["channel_type"],
            "terms": {"cost_linked_clause": c["cost_linked_clause"] == "True", "payment_terms_days": int(c["payment_terms_days"]),
                      "revenue_share_pct": float(c["revenue_share_pct"]), "effective_date": c["effective_date"]},
            "contract_doc": CONTRACT_DOCS.get(cid),
            "orders_total": {"orders": len(os_), "qty": sum(int(o["order_qty"]) for o in os_), "amount_krw": sum(int(o["order_amount_krw"]) for o in os_),
                             "first": os_[0]["order_date"] if os_ else None, "last": os_[-1]["order_date"] if os_ else None},
            "by_product": {p: v for p, v in sorted(by_prod.items())},
            "lead_time_days": {"median": statistics.median(lt) if lt else None, "max": max(lt) if lt else None, "n": len(lt)},
            "backlog": backlog, "stale": stale,
            "booking_trend": {"last3_krw": last3, "prev3_krw": prev3, "yoy3_krw": yoy3,
                              "vs_prev3_pct": round(100 * (last3 / prev3 - 1), 1) if prev3 else None,
                              "vs_yoy_pct": round(100 * (last3 / yoy3 - 1), 1) if yoy3 else None},
            "monthly": monthly,
            "recent_orders": recent[-30:][::-1],
            "claims": sorted(cl, key=lambda x: x["date"], reverse=True),
            "ar": sorted(ar_c, key=lambda x: x["invoice_date"], reverse=True),
            "ar_outstanding_krw": sum(x["outstanding_krw"] for x in ar_c),
            "ar_max_aging_days": max((x["aging_days"] for x in ar_c if x["outstanding_krw"] > 0), default=0),
        })
    # ── 대리점(간접 거래) ─ 대표자·사업자번호·주소·계좌는 개인정보 성격이라 싣지 않는다 ─────────
    agencies = read_pipe_csv(COREVIA / "master" / "m_agency_cv.csv")
    rebates = read_pipe_csv(COREVIA / "sales" / "sales_rebate_payment_cv.csv")
    by_a: dict[str, list[dict]] = defaultdict(list)
    for o in orders:
        if o["agency_id"] != "NONE":
            by_a[o["agency_id"]].append(o)
    agency_rows = []
    for a in agencies:
        aid = a["agency_id"]
        os_ = sorted(by_a[aid], key=lambda o: o["order_date"])
        backlog_a, stale_a, recent = {"orders": 0, "qty": 0, "amount_krw": 0}, {"orders": 0, "qty": 0, "amount_krw": 0}, []
        for o in os_:
            q, amt = int(o["order_qty"]), int(o["order_amount_krw"])
            s_ = ships.get(o["order_id"])
            if s_:
                status, ship_date = "출하", s_["ship_date"]
            else:
                age = (AS_OF - dt.date.fromisoformat(o["order_date"])).days
                b = backlog_a if age <= lead_max else stale_a
                b["orders"] += 1; b["qty"] += q; b["amount_krw"] += amt
                status, ship_date = ("진행" if age <= lead_max else "상태 불명"), None
            recent.append({"order_id": o["order_id"], "date": o["order_date"], "product_id": o["product_id"], "qty": q, "amount_krw": amt,
                           "status": status, "ship_date": ship_date})
        rb = [{"rebate_id": r["rebate_id"], "period": r["period_ym"], "sales_krw": int(r["sales_performance_krw"]), "rebate_krw": int(r["rebate_amount_krw"]),
               "status": r["approval_status"], "approver": r["approver"], "paid": r["payment_date"] if r["payment_date"] != "NONE" else None}
              for r in rebates if r["agency_id"] == aid]
        ar_a = [{"ar_id": r["ar_id"], "invoice_date": r["invoice_date"], "due_date": r["due_date"], "amount_krw": int(r["amount_krw"]),
                 "outstanding_krw": int(r["outstanding_krw"]), "aging_days": int(r["aging_days"])} for r in ar if r["agency_id"] == aid]
        agency_rows.append({
            "agency_id": aid, "name": a["agency_name"], "rebate_eligible": a["rebate_eligible"] == "True", "registered_date": a["registered_date"],
            "orders_total": {"orders": len(os_), "qty": sum(int(o["order_qty"]) for o in os_), "amount_krw": sum(int(o["order_amount_krw"]) for o in os_),
                             "first": os_[0]["order_date"] if os_ else None, "last": os_[-1]["order_date"] if os_ else None},
            "backlog": backlog_a, "stale": stale_a, "recent_orders": recent[-30:][::-1],
            "rebates": sorted(rb, key=lambda x: x["period"], reverse=True), "ar": sorted(ar_a, key=lambda x: x["invoice_date"], reverse=True),
            "ar_outstanding_krw": sum(x["outstanding_krw"] for x in ar_a),
        })

    history = {"as_of": AS_OF.isoformat(), "provenance": "DERIVED", "lead_max_days": lead_max,
               "sources": ["m_customer_cv.csv", "sales_orders_cv.csv", "sales_shipments_cv.csv", "sales_customer_summary_cv.csv",
                           "qc_claims_cv.csv", "fin_ar_aging_cv.csv"],
               "note": "계약서 원문은 원본에 없음. C-101·C-102 만 합성 계약서(SYNTHETIC)가 있다.",
               "customers": customers, "agencies": agency_rows,
               "excluded_fields": ["m_agency_cv.representative_name", "business_reg_no", "address", "bank_account_no"]}

    for name, obj in (("company_overview.json", overview), ("customer_history.json", history)):
        (OUT / name).write_text(json.dumps(obj, ensure_ascii=False, indent=1), encoding="utf-8")
        print(name, (OUT / name).stat().st_size, "bytes")


if __name__ == "__main__":
    main()
