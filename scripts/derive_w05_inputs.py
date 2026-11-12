"""W05 약속 가능 물량(ATP) 엔진 입력 도출 — 원본 행 번호를 보존한다(근거 드로어 3단).

원본은 읽기 전용. 결과: extensions/derived/w05_engine_inputs.json (DERIVED)
- 행 번호 `_row` = 헤더 다음 행이 1 (W05 지시서 5장 적재 규칙).
- 문서(.md) 상수는 파서 없이 시드로 넣고 source_ref(파일:줄)를 단다. test_w05_seeds 가 원본 줄과 대조한다.
"""
from __future__ import annotations

import datetime as dt
import json
import statistics
from collections import defaultdict

from _rawdata import ROOT, read_pipe_csv

RAW = ROOT / "01_참가자_배포용" / "04_원본데이터"
CV = "01_corevia"
AS_OF = dt.date(2026, 7, 1)
H1 = ("2026-01-01", "2026-06-30")
Q2 = ("2026-04-01", "2026-06-30")
OUT = ROOT / "deal-cockpit" / "extensions" / "derived" / "w05_engine_inputs.json"


def rows(rel: str) -> list[dict]:
    out = read_pipe_csv(RAW / rel)
    for i, r in enumerate(out, start=1):
        r["_row"] = i
    return out


def between(d: str, rng: tuple[str, str]) -> bool:
    return rng[0] <= d <= rng[1]  # 양 끝 포함


def enrich_capex(r: dict, cap: list[dict], prods: list[dict], mat: list[dict]) -> dict:
    """설비안 행에 대상 라인·제품·선행 요건을 규칙으로 붙인다(원본 문구에서 도출, 새 설비안 행이 추가돼도 같은 규칙).
    - target_line 이 'L-xx' 로 시작하면 기존 라인 증설, 아니면 '<공장>-<제품>-NEW' 가상 라인(전환·신설).
    - 제품은 target_line/note 에 들어 있는 제품 코드 또는 제품명으로 찾는다.
    - note 에 '재계약' 이 있으면 그 제품 주원료의 재계약, '재검증' 이 있으면 처방 재검증을 선행 요건으로."""
    import re
    text = f"{r['target_line']} {r['note']}"
    pid = next((m for m in re.findall(r"PL-\d+", text)), None) or next((p["product_id"] for p in prods if p["product_name"] in text), None)
    m = re.match(r"(L-\d+)", r["target_line"])
    plant = (re.search(r"(F\d)", text) or re.search(r"(F\d)", text))
    eng = {"PL-02": "DERMA"}.get(pid, (pid or "X").replace("-", ""))
    line_id = m.group(1) if m else f"{plant.group(1) if plant else 'NEW'}-{eng}-NEW"
    main_mat = next((p["main_material_id"] for p in prods if p["product_id"] == pid), None)
    req = []
    needs = lambda w: bool(re.search(w + r"[^.]*필요", r["note"])) and not re.search(w + r"[^.]*불필요", r["note"])  # noqa: E731  '불필요'는 요건 아님
    if needs("재계약") and main_mat:
        req.append({"key": main_mat.replace("-", "").lower() + "_recontract", "label": f"{main_mat} 재계약 가능 여부", "owner": "구매", "material": main_mat,
                    "supplier": next((x["supplier_id"] for x in mat if x["material_id"] == main_mat), None)})
    if needs("재검증"):
        req.append({"key": "formula_revalidation", "label": "처방 재검증", "owner": "품질", "blocking": False})
    return {**r, "target_line_id": line_id, "is_new_line": m is None, "product_id": pid, "requires": req}


def main() -> None:
    f_orders, f_ships = f"{CV}/sales/sales_orders_cv.csv", f"{CV}/sales/sales_shipments_cv.csv"
    f_batch, f_cap, f_capex = f"{CV}/production/prod_batch_log_cv.csv", f"{CV}/production/strat_capacity_cv.csv", f"{CV}/production/strat_capex_plan_cv.csv"
    f_cons, f_mat, f_claim = f"{CV}/production/production_material_consumption_cv.csv", f"{CV}/master/m_material_cv.csv", f"{CV}/quality/qc_claims_cv.csv"
    f_sum, f_fx, f_prod = f"{CV}/sales/sales_customer_summary_cv.csv", "05_external/fx/ext_fx_daily.csv", f"{CV}/master/m_product_cv.csv"

    orders, ships, batches = rows(f_orders), rows(f_ships), rows(f_batch)
    cap, capex, cons, mat, claims, summ, fx, prods = (rows(f_cap), rows(f_capex), rows(f_cons), rows(f_mat), rows(f_claim), rows(f_sum),
                                                     rows(f_fx), rows(f_prod))
    ship_by_order: dict[str, list[dict]] = defaultdict(list)
    for s in ships:
        ship_by_order[s["order_id"]].append(s)
    o_by_id = {o["order_id"]: o for o in orders}
    leads = [(dt.date.fromisoformat(ship_by_order[o["order_id"]][0]["ship_date"]) - dt.date.fromisoformat(o["order_date"])).days
             for o in orders if o["order_id"] in ship_by_order]
    qty_equal = all(int(s["shipped_qty"]) == int(o_by_id[s["order_id"]]["order_qty"]) for s in ships)

    open_orders = [{"order_id": o["order_id"], "customer_id": o["customer_id"], "product_id": o["product_id"], "qty": int(o["order_qty"]),
                    "order_date": o["order_date"], "_row": o["_row"]} for o in orders if o["order_id"] not in ship_by_order]

    # 생산 실적(제품군별, 2026 상반기)
    prod_h1: dict[str, dict] = {}
    for p in sorted({b["product_id"] for b in batches}):
        sel = [b for b in batches if b["product_id"] == p and between(b["production_date"], H1)]
        prod_h1[p] = {"qty": sum(int(b["produced_qty"]) for b in sel), "row_count": len(sel), "sample_rows": [b["_row"] for b in sel[:5]]}

    # EA당 재료비(2분기): sum(consumed*cost)/sum(produced) — 생산은 같은 기간 배치 기준
    mat_cost: dict[str, dict] = {}
    for p in ("PL-01", "PL-02"):
        cs = [c for c in cons if c["product_id"] == p and between(c["consumption_date"], Q2)]
        bs = [b for b in batches if b["product_id"] == p and between(b["production_date"], Q2)]
        spend = sum(float(c["consumed_qty"]) * float(c["applied_unit_cost_krw"]) for c in cs)
        made = sum(int(b["produced_qty"]) for b in bs)
        mat_cost[p] = {"krw_per_ea": round(spend / made, 2), "consumption_rows": len(cs), "batch_rows": len(bs),
                       "sample_rows": [c["_row"] for c in cs[:5]]}

    # 2026 상반기 평균 수주 단가
    price: dict[str, dict] = {}
    for p in ("PL-01", "PL-02"):
        sel = [o for o in orders if o["product_id"] == p and between(o["order_date"], H1)]
        price[p] = {"krw_per_ea": round(sum(int(o["order_amount_krw"]) for o in sel) / sum(int(o["order_qty"]) for o in sel), 1), "row_count": len(sel)}

    # C-101 비중(최근 12개월 수주 수량, 제품군별)과 전년 대비 수주 증감률
    last12 = ("2025-07-01", "2026-06-30")
    share = {}
    for p in ("PL-01", "PL-02"):
        sel = [o for o in orders if o["product_id"] == p and between(o["order_date"], last12)]
        tot = sum(int(o["order_qty"]) for o in sel)
        c101 = sum(int(o["order_qty"]) for o in sel if o["customer_id"] == "C-101")
        share[p] = round(c101 / tot, 4)
    c101_yoy = [{"month": r["period_ym"], "booking_yoy_pct": r["booking_yoy_pct"], "_row": r["_row"], "row": dict(r)}
                for r in summ if r["customer_id"] == "C-101" and r["period_ym"] in ("2026-04", "2026-05", "2026-06")]

    conf = [c for c in claims if c["defect_confirmed"] == "True"]
    conf2 = [c for c in conf if c["product_id"] == "PL-02"]
    fx_row = next(r for r in fx if r.get("date", r.get(list(r)[0])) == "2026-06-30")
    f1 = [r for r in cap if r["plant_id"] == "F1"]
    f1_util = sum(float(r["capacity_qty"]) * float(r["utilization_pct"]) for r in f1) / sum(float(r["capacity_qty"]) for r in f1)

    keep_cols = lambda r: {k: v for k, v in r.items()}  # noqa: E731
    out = {
        "_doc": "W05 ATP 엔진 입력(DERIVED). scripts/derive_w05_inputs.py. 원본 행 번호 _row 는 헤더 다음 행이 1.",
        "as_of": AS_OF.isoformat(), "provenance": "DERIVED",
        "files": {"orders": f_orders, "shipments": f_ships, "batch": f_batch, "capacity": f_cap, "capex": f_capex, "consumption": f_cons,
                  "material": f_mat, "claims": f_claim, "customer_summary": f_sum, "fx": f_fx, "product": f_prod},
        "counts": {"orders": len(orders), "shipments": len(ships), "batches": len(batches),
                   "max_shipments_per_order": max(len(v) for v in ship_by_order.values()), "shipped_qty_equals_order_qty": qty_equal},
        "lead_time_days": {"min": min(leads), "median": statistics.median(leads), "max": max(leads)},
        "open_orders": open_orders,
        "production_h1": prod_h1,
        "capacity": [keep_cols(r) for r in cap],
        "capex": [enrich_capex(keep_cols(r), cap, prods, mat) for r in capex],
        "material_cost_q2": mat_cost,
        "order_price_h1": price,
        "c101_share_12m": share,
        "c101_yoy": c101_yoy,
        "defects": {"confirmed_total": len(conf), "pl02_count": len(conf2), "pl02_batches": len({c["batch_id"] for c in conf2}),
                    "pl02_qty": sum(int(c["qty_affected"]) for c in conf2), "sample_rows": [c["_row"] for c in conf2[:5]]},
        "material_mb": keep_cols(next(r for r in mat if r["material_id"] == "M-B")),
        "fx_close": {"date": "2026-06-30", "row": keep_cols(fx_row)},
        "f1_weighted_util_pct": round(f1_util, 2),
        "products": {r["product_id"]: r["product_name"] for r in prods},
        "product_master": {r["product_id"]: {"name": r["product_name"], "main_material_id": r["main_material_id"], "supplier_id": r["primary_supplier_id"],
                                             "plant": r["production_line"], "_row": r["_row"]} for r in prods},
        "defects_by_product": {p: {"count": len(v), "batches": len({c["batch_id"] for c in v}), "qty": sum(int(c["qty_affected"]) for c in v),
                                   "sample_rows": [c["_row"] for c in v[:5]]} for p in sorted({c["product_id"] for c in conf})
                               for v in [[c for c in conf if c["product_id"] == p]]},
        # 근거 드로어 3단: 문서 상수의 원문 줄(파일 → 줄 번호 → 내용)
        "doc_lines": {rel: {i: line for i, line in enumerate((RAW / rel).read_text(encoding="utf-8").splitlines(), start=1) if line.strip()}
                      for rel in ("01_corevia/docs/rfp_c201.md", "00_common/approval_authority_policy.md", "03_aurora_group/funding/grp_available_cash.md")},
        "headers": {k: list(read_pipe_csv(RAW / v)[0].keys()) for k, v in {"capacity": f_cap, "capex": f_capex, "fx": f_fx, "customer_summary": f_sum}.items()},
        # 문서 상수(파서 없이 시드). source_ref = 04_원본데이터 기준 상대 경로:줄
        "seeds": {
            "rfp_lines": [
                {"bid_customer_id": "C-201", "product_id": "PL-01", "line_id": "L-01", "annual_qty": 6000000, "unit_price_usd": 11.80, "rfp_revenue_100m_krw": 1048,
                 "source_ref": "01_corevia/docs/rfp_c201.md:17", "match": ["6,000,000", "11.80", "1,048"]},
                {"bid_customer_id": "C-201", "product_id": "PL-02", "line_id": "L-02", "annual_qty": 1500000, "unit_price_usd": 15.80, "rfp_revenue_100m_krw": 351,
                 "source_ref": "01_corevia/docs/rfp_c201.md:18", "match": ["1,500,000", "15.80", "351"]}],
            "rfp_terms": [
                {"key": "currency", "value": "USD", "source_ref": "01_corevia/docs/rfp_c201.md:6", "match": ["USD"]},
                {"key": "late_penalty_pct_per_month", "value": 3, "source_ref": "01_corevia/docs/rfp_c201.md:7", "match": ["월 3%"]},
                {"key": "recall_cost_bearer", "value": "SUPPLIER", "source_ref": "01_corevia/docs/rfp_c201.md:8", "match": ["공급자 전가"]},
                {"key": "deadline_text", "value": "마감 3주", "source_ref": "01_corevia/docs/rfp_c201.md:9", "match": ["마감 **3주"]},
                {"key": "rfp_fx_rate", "value": 1480, "source_ref": "01_corevia/docs/rfp_c201.md:15", "match": ["1,480"]},
                {"key": "price_cut_risk_pct", "value": [5, 10], "source_ref": "01_corevia/docs/rfp_c201.md:23", "match": ["−5~10%"]},
                {"key": "derma_line_only", "value": "L-02", "source_ref": "01_corevia/docs/rfp_c201.md:25", "match": ["더마 전용 라인"]}],
            "policy_rules": [
                {"decision_type": "BID_PARTICIPATION", "required_role": "CEO/이사회", "source_type": "ORIGINAL", "source_ref": "00_common/approval_authority_policy.md:12", "match": ["입찰 참여·투자 승인", "CEO/이사회"]},
                {"decision_type": "CAPEX", "required_role": "CEO/이사회", "source_type": "ORIGINAL", "source_ref": "00_common/approval_authority_policy.md:12", "match": ["투자 승인"]},
                {"decision_type": "C101_TERMS", "required_role": "영업임원", "source_type": "ORIGINAL", "source_ref": "00_common/approval_authority_policy.md:8", "match": ["C-101 재협상/판가", "영업 임원"]},
                {"decision_type": "CAPACITY_RESERVATION", "required_role": "생산", "source_type": "ASSUMED", "source_ref": "교육용 가정(W05 지시서 6.6)", "match": []}],
            "group_cash": [
                {"key": "available_krw", "value": 30000000000, "source_ref": "03_aurora_group/funding/grp_available_cash.md:3", "match": ["300억원"]},
                {"key": "unallocated_krw", "value": 30000000000, "source_ref": "03_aurora_group/funding/grp_available_cash.md:5", "match": ["미배정"]},
                {"key": "horizon_months", "value": 6, "source_ref": "03_aurora_group/funding/grp_available_cash.md:6", "match": ["6개월"]}],
            "calendar": [{"key": "board_offset_days", "value": 5, "source_ref": "00_common/data_dictionary.md:10", "match": ["As-of + 5일"]}],
        },
    }
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(OUT.name, OUT.stat().st_size, "bytes")
    print("counts", out["counts"], "lead", out["lead_time_days"], "open", len(open_orders), sum(o["qty"] for o in open_orders))
    print("prod_h1", {k: v["qty"] for k, v in prod_h1.items()}, "rows PL-02", prod_h1["PL-02"]["row_count"])
    print("mat_cost", {k: v["krw_per_ea"] for k, v in mat_cost.items()}, "price", {k: v["krw_per_ea"] for k, v in price.items()})
    print("c101 share", share, "yoy", [(x["month"], x["booking_yoy_pct"]) for x in c101_yoy])
    print("defects", out["defects"], "fx", fx_row, "f1util", out["f1_weighted_util_pct"])


if __name__ == "__main__":
    main()
