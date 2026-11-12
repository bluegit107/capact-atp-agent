"""W05 원본 대사(13.1 R01~R18)와 계산 엔진(13.2) 기준값. 기준값은 원본에서 직접 계산한 값 — 고치지 말고 원인을 찾는다."""
from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from dealcockpit.adapters.golden_loader import load_w05_inputs
from dealcockpit.w05 import engine as E

RAW = Path(__file__).resolve().parents[3] / "01_참가자_배포용" / "04_원본데이터"
D = date(2026, 7, 6)
S1, S2 = date(2027, 3, 29), date(2026, 9, 28)


@pytest.fixture(scope="module")
def inp():
    return load_w05_inputs()


@pytest.fixture(scope="module")
def base(inp):
    calc = E.Calc({})
    caps = E.line_capacity(inp, calc)
    comm = E.commitments(inp, date(2026, 7, 1))
    return calc, caps, comm


def near(a, b, tol=0.01):
    return abs(a - b) <= abs(b) * tol


# ── 13.1 원본 대사 ─────────────────────────────────────────────────────────────
def test_r01_r03_counts_and_lead_time(inp):
    assert (inp["counts"]["orders"], inp["counts"]["shipments"], inp["counts"]["batches"]) == (19801, 17987, 480)
    assert inp["counts"]["max_shipments_per_order"] == 1 and inp["counts"]["shipped_qty_equals_order_qty"] is True
    assert inp["lead_time_days"] == {"min": 45, "median": 60, "max": 77}


def test_r04_r07_commitments(base):
    _, _, comm = base
    assert (len(comm), sum(c["qty"] for c in comm)) == (1814, 7_335_102)
    p = [c for c in comm if c["status"] == "PROTECTED"]
    u = [c for c in comm if c["status"] == "STATUS_UNKNOWN"]
    assert (len(p), sum(c["qty"] for c in p)) == (1216, 6_685_337)
    p2 = [c for c in p if c["product_id"] == "PL-02"]
    assert (len(p2), sum(c["qty"] for c in p2)) == (228, 864_769)
    assert (len(u), sum(c["qty"] for c in u)) == (598, 649_765)


def test_r08_r12_production_util_fx_c101_defects(inp):
    assert inp["production_h1"]["PL-01"]["qty"] == 7_742_980 and inp["production_h1"]["PL-02"]["qty"] == 2_873_915
    assert inp["production_h1"]["PL-02"]["row_count"] == 18
    assert round(inp["f1_weighted_util_pct"], 1) == 88.4
    assert inp["fx_close"]["row"]["usdkrw_close"] == "1478.29"
    assert [float(x["booking_yoy_pct"]) for x in inp["c101_yoy"]] == [-14.3, -22.0, -33.7]
    d = inp["defects"]
    assert (d["confirmed_total"], d["pl02_count"], d["pl02_batches"], d["pl02_qty"]) == (64, 50, 7, 36567)


def test_r13_r14_capex_and_rfp(inp):
    o = E.capex_options(inp)
    assert (o["OPT-01"]["capex_krw"], o["OPT-01"]["lead_time_weeks"], o["OPT-01"]["added_capacity_qty"]) == (18_000_000_000, 36, 1_600_000)
    assert (o["OPT-02"]["capex_krw"], o["OPT-02"]["lead_time_weeks"], o["OPT-02"]["added_capacity_qty"]) == (1_200_000_000, 7, 600_000)
    rev = {l["product_id"]: l["annual_qty"] * l["unit_price_usd"] * 1480 / 1e8 for l in inp["seeds"]["rfp_lines"]}
    assert round(rev["PL-01"]) == 1048 and round(rev["PL-02"]) == 351 and round(sum(rev.values())) == 1399


def test_r16_boundary_dates(inp):
    syn = {**inp, "open_orders": [{"order_id": "X1", "customer_id": "C-1", "product_id": "PL-02", "qty": 1, "order_date": "2026-04-14", "_row": 0},
                                  {"order_id": "X2", "customer_id": "C-1", "product_id": "PL-02", "qty": 1, "order_date": "2026-04-15", "_row": 0}]}
    st = {c["order_id"]: c["status"] for c in E.commitments(syn, date(2026, 7, 1))}
    assert st == {"X1": "STATUS_UNKNOWN", "X2": "PROTECTED"}


def test_r17_seeds_match_source_lines(inp):
    for group in inp["seeds"].values():
        for row in group:
            ref = row["source_ref"]
            if ":" not in ref or not row.get("match"):
                continue
            rel, n = ref.rsplit(":", 1)
            line = (RAW / rel).read_text(encoding="utf-8").splitlines()[int(n) - 1]
            for token in row["match"]:
                assert token in line, (ref, token, line)


def test_r18_source_rows_point_to_the_right_raw_row(inp):
    l02 = next(r for r in inp["capacity"] if r["line_id"] == "L-02")
    raw = (RAW / inp["files"]["capacity"]).read_text(encoding="utf-8").splitlines()
    assert raw[l02["_row"]].split("|")[1:5] == ["L-02", "PL-02", l02["capacity_qty"], "97.0"]


# ── 13.2 계산 엔진 ─────────────────────────────────────────────────────────────
def test_71_line_capacity(base):
    _, caps, _ = base
    assert near(caps["L-01"]["run_rate"], 297_807) and near(caps["L-01"]["full"], 350_361) and near(caps["L-01"]["headroom"], 52_554)
    assert near(caps["L-02"]["run_rate"], 110_535) and near(caps["L-02"]["full"], 113_954) and near(caps["L-02"]["headroom"], 3_419)


def test_73_overload_weeks_and_ready_weeks(inp, base):
    _, caps, comm = base
    led = E.ledger(inp, caps, comm, (), D)
    ow = {r["week"]: round(r["confirmed_open_qty"]) for r in led["lines"]["L-02"] if r["overload"] > 0}
    assert ow == {"2026-07-06": 135_745, "2026-08-03": 117_189, "2026-08-10": 132_305}
    o = E.capex_options(inp)
    assert E.ready_week(o["OPT-01"], D).isoformat() == "2027-03-15" and E.ready_week(o["OPT-02"], D).isoformat() == "2026-08-24"


@pytest.mark.parametrize("start,prod,qty,capex,expected", [
    (S1, "PL-02", 1_500_000, (), 11.9), (S1, "PL-02", 1_500_000, ("OPT-01",), 100.0), (S1, "PL-02", 1_500_000, ("OPT-02",), 51.9),
    (S1, "PL-01", 6_000_000, (), 45.5), (S2, "PL-02", 1_500_000, ("OPT-01",), 59.3), (S2, "PL-02", 1_500_000, ("OPT-02",), 51.9),
    (S2, "PL-02", 1_500_000, ("OPT-01", "OPT-02"), 77.8)])
def test_74_fill_rates(inp, base, start, prod, qty, capex, expected):
    _, caps, comm = base
    led = E.ledger(inp, caps, comm, capex, D)
    assert round(100 * E.evaluate_product(led, prod, qty, start)["fill_rate"], 1) == expected


def test_74_max_offerable_pl01(inp, base):
    _, caps, comm = base
    led = E.ledger(inp, caps, comm, (), D)
    assert near(E.evaluate_product(led, "PL-01", 6_000_000, S1)["max_offerable"], 2_732_817) and E.round_down_10k(2_732_816) == 2_730_000


def test_75_financials_and_penalty_month(inp, base):
    assert near(inp["material_cost_q2"]["PL-01"]["krw_per_ea"], 4293) and near(inp["material_cost_q2"]["PL-02"]["krw_per_ea"], 10635)
    assert near(inp["order_price_h1"]["PL-01"]["krw_per_ea"], 9380) and near(inp["order_price_h1"]["PL-02"]["krw_per_ea"], 16759)
    f = E.financials(inp, {"PL-02": 1_500_000, "PL-01": 2_730_000}, 1478.29, 0, 0)
    assert round(f["revenue_krw"] / 1e8) == 827 and round(f["margin_krw"] / 1e8) == 550
    unmet = {"PL-02": [{"week": "2026-09-28", "qty": 100}, {"week": "2026-06-29", "qty": 10}]}
    g = E.financials(inp, {"PL-02": 1}, 1000, 0, 0, unmet)
    assert set(g["penalty_by_month"]) == {"2026-09", "2026-06"}
    assert near(g["penalty_52w_krw"], 110 * 15.80 * 1000 * 0.03)


def test_properties_atp_nonneg_capex_monotone_offer_monotone(inp, base):
    _, caps, comm = base
    a0, a1 = E.ledger(inp, caps, comm, (), D), E.ledger(inp, caps, comm, ("OPT-01", "OPT-02"), D)
    assert all(r["atp"] >= 0 for ln in a0["lines"].values() for r in ln)
    assert all(x1 >= x0 - 1e-9 for x0, x1 in zip(E.product_atp(a0, "PL-02"), E.product_atp(a1, "PL-02")))
    big = E.evaluate_product(a0, "PL-02", 1_500_000, S1)["fill_rate"]
    small = E.evaluate_product(a0, "PL-02", 500_000, S1)["fill_rate"]
    assert small >= big
    mo = E.evaluate_product(a0, "PL-02", 1_500_000, S1)["max_offerable"]
    assert E.evaluate_product(a0, "PL-02", mo, S1)["fill_rate"] == pytest.approx(1.0)


def test_determinism(inp):
    def run():
        c = E.Calc({})
        caps = E.line_capacity(inp, c)
        comm = E.commitments(inp, date(2026, 7, 1))
        return [m.to_dict() for m in c.metrics], E.ledger(inp, caps, comm, ("OPT-01",), D)
    assert run() == run()


def test_g7_c101_demand_factor_requires_confirmation(inp, base):
    _, caps, comm = base
    with pytest.raises(ValueError, match="G7"):
        E.ledger(inp, caps, comm, (), D, demand_factor=0.9)
    E.ledger(inp, caps, comm, (), D, demand_factor=0.9, c101_confirmed=True)


def test_naive_annual_check_vs_ledger(inp, base):
    _, caps, comm = base
    assert E.naive_annual_check(caps, inp, "PL-02", 1_500_000, ("OPT-01",)) is True  # 총량 비교는 '가능'
    led = E.ledger(inp, caps, comm, ("OPT-01",), D)
    assert round(100 * E.evaluate_product(led, "PL-02", 1_500_000, S2)["fill_rate"], 1) == 59.3  # 원장은 59.3%


def test_lineage_format_and_original_constants(inp):
    from dealcockpit.domain.approval import DEFAULT_POLICY
    from dealcockpit.w05.flow import W05Flow

    L = W05Flow(inp, DEFAULT_POLICY).receive_rfp("2026-07-01", "s", __import__("dealcockpit.domain.common", fromlist=["Role"]).Role.SALES)
    ms = L.runs[-1].metrics
    pen = next(m for m in ms if m["key"] == "rfp_late_penalty_pct")
    assert pen["source_type"] == "ORIGINAL" and pen["formula_key"] == "original_constant" and pen["lineage"]["sources"][0]["rows"] == [7]
    cap = next(m for m in ms if m["key"] == "capex_krw" and m["dims"]["option"] == "OPT-01")
    assert cap["value"] == 18_000_000_000 and cap["source_type"] == "ORIGINAL"
    prod = next(m for m in ms if m["key"] == "production_h1" and m["dims"]["product_id"] == "PL-02")
    src = prod["lineage"]["sources"][0]
    assert src["kind"] == "aggregate" and src["row_count"] == 18 and len(src["sample_rows"]) <= 5
    for m in ms:
        assert m["source_type"] in ("ORIGINAL", "DERIVED", "ASSUMED", "MOCK_REPLY")
        assert set(m["lineage"]) == {"inputs", "sources"}
