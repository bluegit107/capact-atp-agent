"""의사결정 체크포인트 CP1~CP7 화면 데이터 조립(읽기 전용). 각 CP = 확인할 정보 + 조작 가능한 값 + 확정 주체.

계산은 모두 기존 결정론 함수(run_scenarios·gate·probes·economics)를 재사용한다. 화면은 이 결과만 그린다.
"""
from __future__ import annotations

from dataclasses import asdict, replace
from datetime import date

from dealcockpit.domain.workspace import Workspace

from .build_input import build_solver_input, planning_basis
from .economics import breakeven_price_cut_pct, option_paybacks, unit_economics
from .gate import check_invariance
from .negotiation import TOPICS, NegotiationService
from .probes import probe_levers
from .scenarios import PRESETS, W1, ScenarioSpec, run_scenarios

DEADLINE = date(2026, 7, 22)


def _sc(s) -> dict:
    return {
        "id": s.scenario_id, "label": s.label, "accept_pct": round(100 * s.accepted_pct.get("PL-02", 0), 1),
        "net_eok": round(s.financials.net_krw / 1e8, 2), "net_vs_baseline_eok": round(s.kpis.get("net_vs_baseline_krw", 0) / 1e8, 2),
        "otif": s.kpis.get("otif_existing_pct"), "actions": list(s.capacity_actions), "feasible": s.infeasibility is None,
        "shortfall_ea": round(sum(r.slack for r in s.infeasibility.min_relaxation)) if s.infeasibility else 0,
        "approvals": [{"action": a.action, "approver": a.approver} for a in s.approvals_required],
        "kpis": s.kpis, "financials": s.financials.model_dump(),
        "customers": [ci.model_dump() for ci in s.customer_impact],
    }


def _weekly(s, horizon: int, week0: date) -> dict:
    """간트/누적 차트용: 주별 생산(원천별)과 출하(고객별)."""
    from datetime import timedelta

    labels = []
    for t in range(horizon):
        iso = (week0 + timedelta(weeks=t)).isocalendar()
        labels.append(f"{iso.year}-W{iso.week:02d}")
    idx = {w: i for i, w in enumerate(labels)}
    prod = {k: [0] * horizon for k in ("base", "overtime", "option", "outsourced")}
    ship: dict[str, list[int]] = {}
    for e in s.schedule:
        i = idx[e.week]
        if e.customer_id is None:
            prod[e.kind][i] += e.qty
        else:
            ship.setdefault(e.customer_id, [0] * horizon)[i] += e.qty
    return {"weeks": labels, "production": prod, "shipments": ship}


def build_checkpoints(svc: NegotiationService, ws: Workspace, data_health: dict, extra_specs: dict[str, ScenarioSpec] | None = None,
                      what_if: dict | None = None) -> dict:
    case = svc.case
    p = svc.params(ws)
    if what_if:  # 세션 what-if(확정 아님): 레지스트리 범위 검증 후 적용
        from .settings_from_registry import settings_from_registry

        s, notes = settings_from_registry(ws.registry, {k: v for k, v in what_if.items() if k in {x.key for x in ws.registry.specs}})
        p = replace(p, settings=s, fx_label=what_if.get("fx_label", p.fx_label), price_pressure_pct=float(what_if.get("price_pressure_pct", p.price_pressure_pct)))
    out = run_scenarios(svc.solver, case, p, policy=ws.policy, extra_specs=extra_specs)
    a = next(s for s in out.scenarios if s.scenario_id == "A")
    inv = check_invariance(svc.solver, case, p) if not ws.confirmed_monthly else None
    base_weekly, scale, basis_note = planning_basis(case, p)
    inp = build_solver_input(case, p, W1)
    line = case.plant.line_for_product("PL-02")
    week0 = date.fromisoformat(case.derived["week0_monday"])
    today = date.fromisoformat(ws.today)

    cp1 = {
        "basis": p.interpretation, "basis_note": basis_note, "scale_factor": round(scale, 3),
        "load_weekly": round(base_weekly * line.utilization_pct / 100), "base_weekly": round(base_weekly),
        "invariance": None if inv is None else {"invariant": inv.invariant, "reasons": list(inv.reasons),
                                                 "signatures": [asdict(s) for s in inv.signatures], "tolerance_pp": inv.tolerance_pp},
        "assumption_AS001": ws.assumptions["AS-001"].model_dump(mode="json"),
        **data_health,
        "material": case.derived["material"], "quality": case.derived["quality"],
        "unit_cost": {"cogs_per_ea": case.derived["unit_cost_krw"], "conversion_per_ea": case.derived["conversion_cost_per_ea_krw"]},
        "owner": "생산기술(용량 단위)·영업지원(상태 불명 주문)",
    }
    cp2 = {
        "clauses": [{"id": c.clause_id, "customer": c.customer_id, "kind": c.kind.value, "params": c.params, "hard": c.hard,
                     "doc": c.evidence.doc_id, "page": c.evidence.page, "clause": c.evidence.clause, "quote": c.evidence.quote,
                     "span": list(c.evidence.char_span or ()), "confirmed": c.usable_by_solver} for c in case.clauses],
        "limits": [{"group": g.group_id, "max_late_weeks": g.max_late_weeks, "late_rate": g.late_rate_per_day_pct,
                    "penalty_clause": g.penalty_clause_id, "hard_clause": g.hard_clause_id, "provenance": g.penalty_provenance.value}
                   for g in inp.groups],
        "owner": "영업(법무 확인)",
    }
    cap = inp.capacity
    cp3 = {
        "line": line.line_id, "base_weekly": round(cap.base_weekly), "utilization_pct": line.utilization_pct,
        "free_weekly": round(cap.base_weekly * (1 - line.utilization_pct / 100)),
        "overtime_cap_pct": round(cap.overtime_cap_pct, 2), "options": [o.model_dump() for o in cap.options],
        "outsourcing": [o.model_dump() for o in cap.outsourcing], "excluded_options": list(ws.excluded_options),
        "requirement_replies": {k: v.model_dump() for k, v in ws.requirement_replies.items()},
        "weekly_A": _weekly(a, inp.horizon_weeks, week0), "notes": list(inp.notes),
        "owner": "생산관리·품질·구매",
    }
    cp4 = {
        "bid_weekly": round(inp.bid.weekly_requested) if inp.bid else 0, "bid_start_week": inp.bid.start_week if inp.bid else None,
        "capacity_weekly": round(cap.base_weekly), "load_weekly": round(base_weekly * line.utilization_pct / 100),
        "scenario_A": _sc(a), "baseline": _sc(out.baseline), "owner": "영업",
    }
    cp5 = {
        "grid": [asdict(u) for u in unit_economics(case, p)], "breakeven_price_cut_pct": breakeven_price_cut_pct(case, p, p.fx_label),
        "paybacks": [asdict(o) for o in option_paybacks(case, p)], "fx": [f.model_dump() for f in case.bid.fx],
        "current": {"fx_label": p.fx_label, "price_pressure_pct": p.price_pressure_pct}, "owner": "재무·품질",
    }
    probes = probe_levers(svc.solver, case, p, svc.stats)
    cp6 = {
        "scenarios": [_sc(s) for s in out.scenarios], "baseline": _sc(out.baseline),
        "specs": {k: {"label": v.label, "protect": v.protect_existing, "consent": v.use_consent, "min_accept": v.min_accept,
                      "reference_only": v.reference_only} for k, v in {**PRESETS, **(extra_specs or {})}.items()},
        "warnings": [t["warning"] for t in out.trace if "warning" in t],
        "probes": [asdict(x) for x in probes], "input_hash": out.input_hash, "solver": out.solver.model_dump(),
        "what_if": what_if or {}, "owner": "영업임원 → CEO/이사회",
    }
    cp7 = {
        "today": ws.today, "deadline": DEADLINE.isoformat(), "days_left": (DEADLINE - today).days,
        "requests": [{"id": k, "topic": ws.request_topics.get(k), "to_role": r.to_role.value, "counterparty": r.counterparty, "question": r.question,
                      "status": r.status.value, "answer_by": r.answer_by.isoformat(), "days_left": (r.answer_by - today).days,
                      "lever": r.linked_lever_id, "assumption": r.linked_assumption_id} for k, r in ws.requests.items()],
        "levers": [{"id": k, "type": lv.type.value, "status": lv.status.value, "params": lv.params, "owner": lv.owner_role.value,
                    "counterparty": lv.counterparty, "topic": ws.lever_topics.get(k)} for k, lv in ws.levers.items()],
        "tasks": [t.model_dump(mode="json") for t in ws.tasks.values()],
        "approvals": [a_.model_dump(mode="json") for a_ in ws.approvals.values()],
        "assumptions": [{**a_.model_dump(mode="json"), "blocks": a_.blocks_submission} for a_ in ws.assumptions.values()],
        "plan": ws.plan.model_dump(mode="json") if ws.plan else None, "plan_stale": ws.plan_stale,
        "submitted": ws.submitted_scenario, "registry": ws.registry.model_dump(mode="json"), "policy": ws.policy.model_dump(mode="json"),
        "topics": {k: {"to_role": v[0].value, "counterparty": v[1]} for k, v in TOPICS.items()},
        "owner": "역할별",
    }
    return {"workspace": ws.workspace_id, "version": ws.version, "cp1": cp1, "cp2": cp2, "cp3": cp3, "cp4": cp4, "cp5": cp5, "cp6": cp6, "cp7": cp7}
