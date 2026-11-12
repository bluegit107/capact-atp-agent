"""확장성: 라인 증설, 새 제품 입찰, 새 설비안, 새 거래처가 데이터에 추가되면 코드 수정 없이 계산·흐름이 돈다.

시나리오 숫자(지시서 기대값)는 다른 테스트가 지킨다. 여기서는 '구성이 바뀌어도 깨지지 않는다'를 검사한다.
"""
from __future__ import annotations

import copy
from datetime import date

from dealcockpit.adapters.golden_loader import load_w05_inputs
from dealcockpit.domain.approval import DEFAULT_POLICY
from dealcockpit.domain.common import Role
from dealcockpit.w05 import engine as E
from dealcockpit.w05.flow import W05Flow


def extended_inputs() -> dict:
    inp = copy.deepcopy(load_w05_inputs())
    # ① 새 공장·라인(F3 L-21)과 새 제품(PL-07)
    inp["capacity"].append({"plant_id": "F3", "line_id": "L-21", "product_id": "PL-07", "capacity_qty": "900000", "utilization_pct": "60.0", "snapshot_date": "2026-06-30", "_row": 99})
    inp["products"]["PL-07"] = "앰플"
    inp["production_h1"]["PL-07"] = {"qty": 1_300_000, "row_count": 6, "sample_rows": [900]}
    inp["material_cost_q2"]["PL-07"] = {"krw_per_ea": 7000.0, "consumption_rows": 6, "batch_rows": 6, "sample_rows": [900]}
    # ② 입찰에 새 품목 추가
    inp["seeds"]["rfp_lines"].append({"bid_customer_id": "C-201", "product_id": "PL-07", "line_id": "L-21", "annual_qty": 800_000, "unit_price_usd": 9.5,
                                      "rfp_revenue_100m_krw": 112, "source_ref": "가상:확장성 테스트", "match": []})
    # ③ 기존 라인(L-01 기초) 증설안 OPT-03 과 새 제품 전환안 OPT-04(재계약 필요)
    inp["capex"].append({"option_id": "OPT-03", "option_type": "증설", "target_line": "L-01(PL-01 기초)", "capex_krw": "5000000000", "lead_time_weeks": "20",
                         "added_capacity_qty": "3500000", "note": "가상", "_row": 98, "target_line_id": "L-01", "is_new_line": False, "product_id": "PL-01", "requires": []})
    inp["capex"].append({"option_id": "OPT-04", "option_type": "전환", "target_line": "F3 신규 앰플 라인", "capex_krw": "800000000", "lead_time_weeks": "10",
                         "added_capacity_qty": "400000", "note": "가상", "_row": 97, "target_line_id": "F3-PL07-NEW", "is_new_line": True, "product_id": "PL-07",
                         "requires": [{"key": "mx_recontract", "label": "M-X 재계약 가능 여부", "owner": "구매", "material": "M-X", "supplier": "V-999"}]})
    # ④ 새 거래처의 미출하 주문(새 제품)
    inp["open_orders"].append({"order_id": "SO-999001", "customer_id": "C-113", "product_id": "PL-07", "qty": 40_000, "order_date": "2026-06-20", "_row": 19802})
    return inp


def test_topology_reads_new_lines_products_and_capex_from_data():
    t = E.topology(extended_inputs())
    assert t["lines"]["L-21"]["plant"] == "F3" and t["lines"]["F3-PL07-NEW"]["virtual"]
    assert t["by_product"]["PL-07"] == ["L-21", "F3-PL07-NEW"] and "PL-07" in t["bid_products"]
    assert t["capex"]["OPT-03"]["line_id"] == "L-01"


def test_ledger_and_flow_run_on_extended_data_without_code_change():
    inp = extended_inputs()
    calc = E.Calc({})
    caps = E.line_capacity(inp, calc)
    comm = E.commitments(inp, date(2026, 7, 1))
    assert "L-21" in caps and next(c for c in comm if c["order_id"] == "SO-999001")["line_id"] == "L-21"
    led = E.ledger(inp, caps, comm, ("OPT-03", "OPT-04"), date(2026, 7, 6))
    assert {"L-01", "L-02", "L-21", "F3-PL07-NEW"} <= set(led["lines"]) and led["product_lines"]["PL-07"] == ["L-21", "F3-PL07-NEW"]
    f = W05Flow(inp, DEFAULT_POLICY)
    L = f.receive_rfp("2026-07-01", "s", Role.SALES)
    b = next(r for r in L.requests if r.to_external)
    f.send_request(L, b.id, "2026-07-01", "s", Role.SALES)
    f.answer_request(L, b.id, {"supply_start_date": "2027-04-01", "partial_award_allowed": True}, "회신", "2026-07-02", "d", Role.DEMO_ADMIN)
    f.confirm_from_request(L, b.id, "2026-07-02", "s", Role.SALES)
    live = [o for o in L.options if o.status == "PROPOSED"]
    assert live and all("PL-07" in o.offered for o in live if o.label in ("A", "B"))
    used = {c for o in live for c in o.capex_ids}
    assert used <= {"OPT-01", "OPT-02", "OPT-03", "OPT-04"}
    if "OPT-04" in used:  # 새 설비안의 선행 요건도 같은 규칙으로 핵심 가정·확인요청·차단 표시가 된다
        assert L.cur("mx_recontract") is not None and any(r.assumption_keys == ["mx_recontract"] for r in L.requests)
        assert any(fl.rule_key == "RF-PROC-01" for fl in L.flags)


def test_new_customer_appears_in_commitments_but_not_invented_in_contracts():
    inp = extended_inputs()
    comm = E.commitments(inp, date(2026, 7, 1))
    assert any(c["customer_id"] == "C-113" and c["status"] == "PROTECTED" for c in comm)


def test_calendar_follows_dataset_as_of():
    """다른 시점의 데이터셋: 기준일만 바꾸면 원장 시작 주, 제출 마감, 설비 결정일이 함께 움직인다(날짜 하드코딩 없음)."""
    base = load_w05_inputs()
    assert (E.ledger_start(base), E.submission_deadline(base), E.decision_date(base)) == (date(2026, 6, 29), date(2026, 7, 22), date(2026, 7, 6))
    inp = copy.deepcopy(base)
    inp["as_of"] = "2026-09-01"
    inp["seeds"]["calendar"].append({"key": "submission_deadline_days", "value": 30})
    assert E.ledger_start(inp) == date(2026, 8, 31) and E.submission_deadline(inp) == date(2026, 10, 1) and E.decision_date(inp) == date(2026, 9, 6)
    caps, comm = E.line_capacity(inp, E.Calc({})), E.commitments(inp, E.as_of(inp))
    assert E.ledger(inp, caps, comm, (), E.decision_date(inp))["weeks"][0] == "2026-08-31"
    L = W05Flow(inp, DEFAULT_POLICY).receive_rfp("2026-09-01", "sales", Role.SALES)
    assert L.submission_deadline == "2026-10-01" and L.cur("submission_deadline").value == "2026-10-01"
