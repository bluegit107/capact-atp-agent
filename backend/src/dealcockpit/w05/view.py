"""W05 화면 데이터 조립과 역할별 열람 제한(지시서 11.3·G16). 볼 수 없는 값은 화면이 아니라 응답에서 뺀다."""
from __future__ import annotations

import re
from datetime import date

from dealcockpit.domain.common import Role

from . import engine as E
from .flow import BidLedger

# 역할별로 응답에서 제외하는 지표(단가·손익·재료비)
HIDDEN_METRICS = {
    Role.SALES.value: {"material_margin_krw", "capex_payback_years"},
    Role.PRODUCTION.value: {"revenue_krw", "material_margin_krw", "penalty_52w_krw", "capex_payback_years", "fx_close"},
    Role.PROCUREMENT.value: {"revenue_krw", "material_margin_krw", "penalty_52w_krw", "capex_payback_years", "fx_close"},
}
HIDE_UNIT_PRICE = {Role.PRODUCTION.value, Role.PROCUREMENT.value}

GLOSSARY = {
    "C-201": "해외 신규 입찰 고객", "C-101": "뷰티메가리테일(최대 고객)", "F1": "국내 공장", "F2": "해외 공장",
    "L-01": "F1 기초 라인", "L-02": "F1 더마 라인", "L-03": "F1 색조 라인", "F2-DERMA-NEW": "F2 신설 더마 라인",
    "PL-01": "기초", "PL-02": "더마", "M-B": "더마 핵심 원료(독점)", "V-205": "M-B 유일 공급사",
    "OPT-01": "L-02 더마 증설", "OPT-02": "F2 더마 전환",
}
TERMS = {
    "ATP": "새로 약속할 수 있는 물량 = 능력 − 이미 잡힌 물량", "백로그": "받았지만 아직 출하하지 않은 주문", "리드타임": "수주부터 출하까지 걸리는 기간",
    "가동률": "최대 능력 대비 실제 사용 비율", "CAPEX": "설비투자비", "처방 재검증": "다른 공장에서 같은 품질이 나오는지 다시 검사", "MOQ": "최소 주문 수량",
    "충족률": "52주 동안 요청 물량 중 채울 수 있는 비율", "재료비 차감 이익": "매출 − 재료비(가공비·인건비·물류비 미포함)", "상신": "상위 권한자에게 결정을 올림",
    "페널티": "납기를 어기면 내는 벌금(C-201: 월 3%)",
}
STATUS_KO = {"RECEIVED": "접수", "IN_REVIEW": "검토 중", "WAITING_INFO": "확인 대기", "PENDING_APPROVAL": "승인 대기", "APPROVED": "승인됨",
             "READY_TO_SUBMIT": "제출 준비 완료", "SUBMITTED": "제출(모의)", "REVIEW_REQUIRED": "재검토 필요", "ON_HOLD": "참여 보류"}


def _metric_index(L: BidLedger) -> dict[int, dict]:
    idx: dict[int, dict] = {}
    for run in L.runs:  # 같은 id 가 실행마다 다시 매겨지므로 (run, id) 로 구분 — 화면은 최신 실행 기준
        for m in run.metrics:
            idx[m["id"]] = {**m, "run_id": run.id, "revision": run.revision}
    return idx


def visible_flags(L: BidLedger, role: str) -> list[dict]:
    return [f.model_dump() for f in L.flags if role in f.visible_roles or role == Role.REVIEWER.value]


def metric_value(m: dict, role: str):
    return None if m["key"] in HIDDEN_METRICS.get(role, set()) else m["value"]


def render_text(text: str | None, L: BidLedger, role: str) -> str | None:
    """자리표시자 {{calc:id}} 를 값·단위로 치환(서버 측). 볼 수 없는 값은 '열람 제한'."""
    if not text:
        return text
    run = L.runs[-1] if L.runs else None
    ms = {m["id"]: m for m in (run.metrics if run else [])}

    def sub(mt):
        kind, i = mt.group(1), int(mt.group(2))
        if kind == "assume":
            a = next((x for x in L.assumptions if x.id == i), None)
            return f"{a.label}={a.value}" if a else "?"
        m = ms.get(i)
        if m is None:
            return "?"
        v = metric_value(m, role)
        return "열람 제한" if v is None else fmt_value(v, m["unit"])
    return re.sub(r"\{\{(calc|assume):(\d+)\}\}", sub, text)


def fmt_value(v, unit: str) -> str:
    if v is None:
        return "—"
    if unit == "KRW":
        return f"{v / 1e8:,.1f}억원"
    if unit == "pct":
        return f"{v:g}%"
    if unit in ("EA", "EA/week"):
        return f"{v:,.0f} {'EA/주' if unit == 'EA/week' else 'EA'}"
    if unit == "date":
        return str(v)
    return f"{v:,}{unit}" if isinstance(v, (int, float)) else f"{v}{unit}"


def build_view(L: BidLedger | None, role: str, today: str, inp: dict) -> dict:
    if L is None:
        return {"bid": None, "glossary": GLOSSARY, "terms": TERMS}
    idx = {m["id"]: m for m in (L.runs[-1].metrics if L.runs else [])}

    def mv(mid):
        m = idx.get(mid)
        if not m:
            return None
        return {"id": mid, "value": metric_value(m, role), "unit": m["unit"], "source_type": m["source_type"], "hidden": metric_value(m, role) is None and m["value"] is not None}

    flags = visible_flags(L, role)
    for f in flags:
        f["text"] = render_text(f["text"], L, role)
    opts = []
    for o in L.options:
        d = o.model_dump()
        d["metrics"] = {k: mv(v) for k, v in o.metric_ids.items()} if o.calc_run_id == (L.runs[-1].id if L.runs else None) else {}
        d["flags"] = [f for f in flags if f["option_id"] == o.id]
        d["narrative"] = render_text(o.narrative, L, role)
        d["unconfirmed_deps"] = [L.cur(k).label for k in o.deps if L.cur(k) and L.cur(k).status == "UNCONFIRMED"]
        d["unconfirmed_critical"] = [L.cur(k).label for k in o.deps if L.cur(k) and L.cur(k).status == "UNCONFIRMED" and L.cur(k).is_critical]
        opts.append(d)
    calc0 = E.Calc({})
    caps0 = E.line_capacity(inp, calc0)
    for d in opts:  # 원장 미사용 비교(연간 총량만): 화면의 '원장 사용 안 함' 토글
        d["naive_annual"] = {p: E.naive_annual_check(caps0, inp, p, q, tuple(d["capex_ids"])) for p, q in d["offered"].items()}
    lines = [{**l, "unit_price_usd": None} if role in HIDE_UNIT_PRICE else l for l in inp["seeds"]["rfp_lines"]]
    dl = date.fromisoformat(L.submission_deadline)
    agent = L.agent_runs[-1].model_dump() if L.agent_runs else None
    if agent and agent.get("output"):
        out = dict(agent["output"])
        for k in ("situation_summary",):
            out[k] = render_text(out.get(k), L, role)
        if out.get("ai_opinion"):
            out["ai_opinion"] = {**out["ai_opinion"], "reasoning": render_text(out["ai_opinion"].get("reasoning"), L, role)}
        if out.get("impact_of_change"):
            out["impact_of_change"] = {**out["impact_of_change"], "text": render_text(out["impact_of_change"].get("text"), L, role)}
        out["next_actions"] = [{**a, "action": render_text(a.get("action"), L, role)} for a in out.get("next_actions", [])]
        out["open_risks"] = [render_text(x, L, role) for x in out.get("open_risks", [])]
        out["gaps"] = [{**g, "why_needed": render_text(g.get("why_needed"), L, role), "proposed_action": render_text(g.get("proposed_action"), L, role)} for g in out.get("gaps", [])]
        agent["output"] = out
    return {
        "bid": {"customer_id": L.customer_id, "title": L.title, "status": L.status, "status_ko": STATUS_KO.get(L.status, L.status), "revision": L.revision,
                "received_on": L.received_on, "deadline": L.submission_deadline, "days_left": (dl - date.fromisoformat(today)).days,
                "selected_option_id": L.selected_option_id, "hold_reason": L.hold_reason, "capex_rejected": L.capex_rejected,
                "terms": {"currency": "USD", "penalty": "월 3%", "recall": "공급자 부담"}},
        "lines": lines,
        "assumptions": [a.model_dump() for a in L.assumptions],
        # 부서 담당자는 자기 앞으로 온 확인요청만(조율하는 영업, 결정권자, 심사·진행자는 전체)
        "requests": [r.model_dump() for r in L.requests if role in COORD_ROLES or r.to_role == role],
        "messages": [m.model_dump() for m in L.messages] if role in COORD_ROLES else [],
        "options": opts,
        "flags": flags,
        "approvals": [a.model_dump() for a in L.approvals],
        "tasks": [t.model_dump() for t in L.tasks],
        "reservations": _resv_summary(L),
        "events": [_redact_event(e.model_dump(), role) for e in L.events][-300:],
        "change_banner": L.change_banner,
        "graph_now": L.graph_state(),
        "deps": [d.model_dump() for d in L.deps],
        "notices": L.notices,
        "agent": agent,
        "notes": L.notes if role not in HIDE_UNIT_PRICE else [],
        "run": {"id": L.runs[-1].id, "revision": L.runs[-1].revision, "engine": L.runs[-1].engine_version} if L.runs else None,
        "glossary": GLOSSARY, "terms": TERMS,
    }


COORD_ROLES = {Role.SALES.value, Role.SALES_EXEC.value, Role.CEO_BOARD.value, Role.REVIEWER.value, Role.DEMO_ADMIN.value, Role.POLICY_ADMIN.value}


def _redact_event(e: dict, role: str) -> dict:
    """이력도 열람 제한을 따른다: 재무 의견 본문은 단가·손익을 못 보는 역할에게 숨김."""
    if e["event_type"] == "FINANCE_NOTE" and role in HIDE_UNIT_PRICE | {Role.SALES.value}:
        return {**e, "after": "열람 제한"}
    return e


def _resv_summary(L: BidLedger) -> list[dict]:
    agg: dict[tuple, dict] = {}
    for r in L.reservations:
        k = (r.option_id, r.line_id, r.status)
        a = agg.setdefault(k, {"option_id": r.option_id, "line_id": r.line_id, "status": r.status, "weeks": 0, "qty": 0.0, "from": r.week_start, "to": r.week_start})
        a["weeks"] += 1
        a["qty"] += r.qty
        a["from"], a["to"] = min(a["from"], r.week_start), max(a["to"], r.week_start)
    return list(agg.values())


def board(L: BidLedger | None, inp: dict, capex: tuple[str, ...]) -> dict:
    """여력·약속 보드: 라인 × 주(앞 52주) — 기준 부하·확정 미출하·추가 능력·예약·ATP·초과."""
    calc = E.Calc({})
    caps = E.line_capacity(inp, calc)
    comm = E.commitments(inp, E.as_of(inp))
    reserved = {}
    if L:
        for r in L.reservations:
            if r.status in ("TENTATIVE", "CONFIRMED"):
                reserved[(r.line_id, r.week_start)] = reserved.get((r.line_id, r.week_start), 0) + r.qty
    led = E.ledger(inp, caps, comm, capex, E.decision_date(inp), reserved=reserved)
    unknown = [c for c in comm if c["status"] == "STATUS_UNKNOWN" and c["line_id"]]
    topo = E.topology(inp)
    bid_lines = [ln for ln in caps if not ln.startswith("_")]
    return {"weeks": led["weeks"][:78], "lines": {k: v[:78] for k, v in led["lines"].items()}, "capex": list(capex),
            "line_util": {ln: caps[ln]["util"] for ln in bid_lines}, "bid_lines": bid_lines, "line_product": {ln: m["product_id"] for ln, m in topo["lines"].items()},
            "f1_util": inp["f1_weighted_util_pct"],
            "unknown": {ln: {"orders": len([c for c in unknown if c["line_id"] == ln]), "qty": sum(c["qty"] for c in unknown if c["line_id"] == ln)} for ln in bid_lines},
            "unknown_total": {"orders": len([c for c in comm if c["status"] == "STATUS_UNKNOWN"]), "qty": sum(c["qty"] for c in comm if c["status"] == "STATUS_UNKNOWN")},
            "names": topo["label"],
            "capex_lines": {o: c["line_id"] for o, c in topo["capex"].items()},
            "capex_info": {o: {"line_id": c["line_id"], "product_id": c["product_id"], "capex_krw": c["capex_krw"], "lead_time_weeks": c["lead_time_weeks"],
                               "added_capacity_qty": c["added_capacity_qty"], "requires": [r["label"] for r in c["requires"]], "new_line": topo["lines"][c["line_id"]]["virtual"]}
                           for o, c in E.capex_options(inp).items()}}


def week_orders(inp: dict, line: str, week: str) -> list[dict]:
    comm = E.commitments(inp, E.as_of(inp))
    return [c for c in comm if c["line_id"] == line and c["expected_ship_week"] == week and c["status"] == "PROTECTED"]


def lineage(L: BidLedger, mid: int, role: str, inp: dict) -> dict:
    """근거 드로어 3단: ① 지표 ② 식·입력·기댄 가정 ③ 원본 행(단일) 또는 필터 요약(집계)."""
    run = L.runs[-1]
    ms = {m["id"]: m for m in run.metrics}
    m = ms.get(mid)
    if m is None:
        raise KeyError(mid)
    files = inp["files"]
    rev = {v: k for k, v in files.items()}

    def row_of(file: str, row: int):
        k = rev.get(file)
        if k == "capacity":
            r = next((x for x in inp["capacity"] if x["_row"] == row), None)
        elif k == "capex":
            r = next((x for x in inp["capex"] if x["_row"] == row), None)
        elif k == "fx":
            r = inp["fx_close"]["row"] if inp["fx_close"]["row"]["_row"] == row else None
        elif k == "customer_summary":
            r = next((x["row"] for x in inp["c101_yoy"] if x["_row"] == row), None)
        elif k == "orders":
            r = next((x for x in inp["open_orders"] if x["_row"] == row), None)
        else:
            r = None
        return {c: v for c, v in r.items() if c != "_row"} if r else None

    sources = []
    for s in m["lineage"]["sources"]:
        s2 = dict(s)
        if s["kind"] == "single":
            s2["row_values"] = [row_of(s["file"], r) for r in s["rows"]]
        if s["kind"] == "doc_line":
            s2["lines"] = {r: inp["doc_lines"].get(s["file"], {}).get(str(r)) for r in s["rows"]}
        sources.append(s2)
    inputs = []
    for i in m["lineage"]["inputs"]:
        if "calc_result_id" in i:
            x = ms.get(i["calc_result_id"])
            if x:
                inputs.append({"kind": "metric", "id": x["id"], "key": x["key"], "value": metric_value(x, role), "unit": x["unit"], "source_type": x["source_type"],
                               "formula_key": x["formula_key"], "dims": x["dims"]})
        else:
            a = L.cur(i["assumption_key"])
            if a:
                inputs.append({"kind": "assumption", "key": a.key, "code": a.code, "label": a.label, "value": a.value, "status": a.status, "source_type": a.source_type,
                               "confirmed_by": a.confirmed_by, "rationale": a.rationale})
    return {"metric": {"id": m["id"], "key": m["key"], "value": metric_value(m, role), "unit": m["unit"], "source_type": m["source_type"], "dims": m["dims"],
                       "run_id": run.id, "revision": run.revision}, "formula_key": m["formula_key"], "formula": FORMULAS.get(m["formula_key"], m["formula_key"]),
            "inputs": inputs, "sources": sources}


FORMULAS = {
    "run_rate": "2026 상반기 생산량 ÷ 26주", "full_capacity": "run_rate ÷ (가동률/100)", "headroom": "full_capacity − run_rate",
    "fill_rate_52w": "Σ min(ATP[주], 주 수요) ÷ Σ 주 수요 (납품 개시 주부터 52주)", "first_full_week": "이후 계속 전량 납품 가능한 첫 주",
    "revenue": "물량 × 달러 단가 × (1−인하율) × 환율 × (1−환율 하락률)", "material_margin": "원화 매출 − 물량 × EA당 재료비(2분기)",
    "penalty_52w": "Σ 월별 미충족 물량 × 원화 단가 × 3% (주의 월요일이 속한 달에 귀속)", "capex_payback": "설비투자 ÷ (설비로 늘어난 물량 × EA당 재료비 차감 이익) — 재료비만 뺀 값이라 실제보다 짧음",
    "original_constant": "원본 값 그대로", "commitments": "미출하 수주 중 수주일+77일 ≥ 기준일 → 보호 / 미만 → 상태 불명", "overload": "확정 미출하 > 라인 최대 능력인 주의 수",
    "sum_produced_qty_h1": "제품군 배치 생산량 합(2026-01-01~06-30, 양 끝 포함)", "capacity_weighted_util": "Σ(capacity_qty × 가동률) ÷ Σ capacity_qty",
    "count_confirmed_claims": "규격이탈 확정 클레임 건수", "sum": "합계",
}
