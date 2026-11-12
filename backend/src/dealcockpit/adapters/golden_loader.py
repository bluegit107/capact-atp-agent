"""골든 케이스 로더(파일 IO). 원본 도출값·합성 계약 라벨·가정 프로필을 도메인 객체로 만든다."""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from dealcockpit.application.case import Case
from dealcockpit.domain.bid import Bid, BidLine, BidTerms, FxScenario, SupplyStart
from dealcockpit.domain.clause import Clause
from dealcockpit.domain.common import Provenance, SourceRef
from dealcockpit.domain.plant import CapacityOption, Line, PlantModel, PremiumCosts

DEFAULT_ROOT = Path(__file__).resolve().parents[4]  # deal-cockpit/


def _load(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def load_clauses(root: Path, doc_ids=("CT-C101", "CT-C102"), confirmed: bool = True) -> tuple[Clause, ...]:
    out = []
    for doc in doc_ids:
        for c in _load(root / "extensions" / "contracts" / "labels" / f"{doc}.labels.json")["clauses"]:
            rec = {k: v for k, v in c.items() if k != "extra_evidence"}
            if not confirmed:
                rec["extraction"] = {**rec["extraction"], "human_confirmed": False, "confirmed_by": None}
            out.append(Clause.model_validate(rec))
    return tuple(out)


def load_reply_stats(root: Path = DEFAULT_ROOT) -> dict[str, tuple[float, float]]:
    """모의 회신 정책표 → 주제별 (회신 소요일 중앙값, 유리한 회신 비율). 문의 우선순위의 사전값(SIMULATED 가정)."""
    import statistics

    pol = _load(root / "extensions" / "synthetic" / "mock_reply_policy.json")
    out = {}
    for topic, t in pol["topics"].items():
        days = [v["delay_days"] for v in t["variants"] if v["delay_days"] is not None]
        fav = sum(1 for v in t["variants"] if v["favorable"]) / len(t["variants"])
        out[topic] = (float(statistics.median(days)) if days else 7.0, round(fav, 3))
    return out


def load_data_health(root: Path = DEFAULT_ROOT) -> dict:
    """CP1 데이터 정합: 용량 단위 증거(AS-001)·백로그/상태 불명 주문(AS-002) 요약."""
    a1 = _load(root / "extensions" / "derived" / "as001_capacity_unit_evidence.json")
    a2 = _load(root / "extensions" / "derived" / "as002_backlog_and_stale_orders.json")
    return {
        "capacity_lines": [{k: l[k] for k in ("line_id", "product_id", "capacity_qty", "utilization_pct", "observed_monthly_production_avg",
                                               "rel_error_if_per_month", "rel_error_if_per_year", "evidence_suggests")} for l in a1["lines"]],
        "capacity_consistent": a1["consistent_across_lines"],
        "lead_time_days": a2["lead_time_days"],
        "backlog": a2["backlog_confirmed"]["total"],
        "stale": a2["stale_unknown"]["total"],
    }


def load_overview(root: Path = DEFAULT_ROOT) -> dict:
    """대시보드용 전사 개요(DERIVED, scripts/derive_overview_history.py)."""
    return _load(root / "extensions" / "derived" / "company_overview.json")


def load_history(root: Path = DEFAULT_ROOT) -> dict:
    """거래처(고객·대리점) 이력(DERIVED). 대리점 개인정보 성격 필드는 도출 단계에서 제외됨."""
    return _load(root / "extensions" / "derived" / "customer_history.json")


def load_w05_inputs(root: Path = DEFAULT_ROOT) -> dict:
    """W05 ATP 엔진 입력(DERIVED, scripts/derive_w05_inputs.py). 원본 행 번호 포함."""
    return _load(root / "extensions" / "derived" / "w05_engine_inputs.json")


def contract_text(doc_id: str, root: Path = DEFAULT_ROOT) -> str:
    if doc_id not in ("CT-C101", "CT-C102"):
        raise KeyError(doc_id)  # 화이트리스트(경로 조작 방지)
    return (root / "extensions" / "contracts" / f"{doc_id}.md").read_text(encoding="utf-8")


def reply_variants(root: Path = DEFAULT_ROOT) -> dict:
    pol = _load(root / "extensions" / "synthetic" / "mock_reply_policy.json")
    return {t: [{"variant": v["variant"], "outcome": v["outcome"], "favorable": v["favorable"], "text": v["raw_text"], "terms": v.get("terms", {})}
                for v in x["variants"]]
            for t, x in pol["topics"].items()}


def mock_reply(topic: str, variant: str, root: Path = DEFAULT_ROOT) -> tuple[str, dict, str]:
    """모의 회신 정책표에서 (outcome, terms, raw_text). 모든 회신은 SIMULATED 가상 입력이다."""
    pol = _load(root / "extensions" / "synthetic" / "mock_reply_policy.json")
    v = next(x for x in pol["topics"][topic]["variants"] if x["variant"] == variant)
    return v["outcome"], v["terms"], v["raw_text"]


def load_case(root: Path = DEFAULT_ROOT, clauses_confirmed: bool = True) -> Case:
    derived = _load(root / "extensions" / "derived" / "s2_pl02_commitments.json")
    prof = _load(root / "extensions" / "synthetic" / "outsourcing_profile.json")
    cap = derived["capacity"]

    line = Line(
        line_id=cap["line_id"], plant_id="F1", product_family="PL-02", capacity_qty=cap["capacity_qty"],
        utilization_pct=cap["utilization_pct"], overtime_cap_pct=0.0,  # 근무형태 데이터 없음 -> 특근은 레버(생산 회신)로만
        provenance=Provenance.SOURCE, source_ref=SourceRef(file="strat_capacity_cv.csv", row="L-02"),
    )
    options = (
        CapacityOption(
            option_id="OPT-01", kind="expansion", target_line="L-02", capex_krw=18_000_000_000, lead_time_weeks=36,
            added_capacity_qty=1_600_000, requires=(), source_ref=SourceRef(file="strat_capex_plan_cv.csv", row="OPT-01"),
        ),
        CapacityOption(
            option_id="OPT-02", kind="f2_conversion", target_line="NEW:F2-DERMA", capex_krw=1_200_000_000, lead_time_weeks=7,
            added_capacity_qty=600_000, requires=("formula_revalidation", "M-B_recontract"),
            source_ref=SourceRef(file="strat_capex_plan_cv.csv", row="OPT-02"),
        ),
    )
    plant = PlantModel(
        as_of=date.fromisoformat(derived["as_of"]), horizon_weeks=derived["horizon_weeks"], lines=(line,), options=options,
        premium=PremiumCosts.model_validate(prof["premium"]), changeover_hours=prof["changeover_hours"],
    )
    bid = Bid(
        bid_id="C-201", as_of=date(2026, 7, 1), deadline=date(2026, 7, 22),
        supply_start=SupplyStart(assumed=date(2026, 10, 5), assumption_id="AS-004"),
        fx=(
            FxScenario(label="rfp", usd_krw=1480.0, provenance=Provenance.SOURCE),
            FxScenario(label="as_of", usd_krw=1478.29, provenance=Provenance.SOURCE),
            FxScenario(label="downside", usd_krw=1420.0, provenance=Provenance.ASSUMPTION),
        ),
        lines=(
            BidLine(product_id="PL-01", annual_qty=6_000_000, unit_price_fx=11.80, source_ref=SourceRef(file="rfp_c201.md")),
            BidLine(product_id="PL-02", annual_qty=1_500_000, unit_price_fx=15.80, source_ref=SourceRef(file="rfp_c201.md")),
        ),
        terms=BidTerms(currency="USD", late_penalty_pct_per_month=3.0, recall_cost_passthrough=True),
        price_pressure_pct=(0.0, -5.0, -10.0),
    )
    return Case(derived=derived, plant=plant, bid=bid, clauses=load_clauses(root, confirmed=clauses_confirmed))
