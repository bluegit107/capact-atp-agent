"""RawSolution -> Scenario(재무·고객 영향·간트 일정·필요 승인·KPI). 순수 계산.

재무는 솔버 목적값이 아니라 **실제 비용으로 재계산**한다(가중치는 선택에만 쓰고 보고 수치를 왜곡하지 않는다).
"""
from __future__ import annotations

from datetime import date, timedelta

from dealcockpit.domain.approval import ApprovalPolicy, DEFAULT_POLICY
from dealcockpit.domain.solution import (
    ApprovalNeed,
    CustomerImpact,
    Financials,
    Infeasibility,
    RelaxationItem,
    Scenario,
    ScheduleEntry,
)
from dealcockpit.domain.solver_input import DemandGroupInput, RawSolution, SolverInput


def cum(v) -> list[float]:
    out, s = [], 0.0
    for x in v:
        s += x
        out.append(s)
    return out


def week_label(week0: date, t: int) -> str:
    iso = (week0 + timedelta(weeks=t)).isocalendar()
    return f"{iso.year}-W{iso.week:02d}"


def group_delay(g: DemandGroupInput, ship: tuple[float, ...]) -> tuple[int, int, float, float]:
    """FIFO 코호트별 지연 -> (지연 코호트 수, 최대 지연 주수, 정시 수량, 전체 수량)."""
    D, S = cum(g.weekly_due), cum(ship)
    delayed, worst, ontime, total = 0, 0, 0.0, 0.0
    for tau, due in enumerate(g.weekly_due):
        if due <= 1e-6:
            continue
        total += due
        done = next((t for t in range(tau, len(S)) if S[t] >= D[tau] - 1.0), None)
        wk = (done - tau) if done is not None else (len(S) - tau)
        if wk > 0:
            delayed += 1
            worst = max(worst, wk)
        else:
            ontime += due
    return delayed, worst, ontime, total


def penalty_krw(g: DemandGroupInput, ship: tuple[float, ...], relaxed: bool) -> float:
    D, S = cum(g.weekly_due), cum(ship)
    grace = g.waiver.grace_weeks if g.waiver else 0
    rate = g.late_rate_per_day_pct / 100.0 * 7.0 * g.unit_price_krw
    late = sum(max(0.0, (D[t - grace] if t - grace >= 0 else 0.0) - S[t]) for t in range(len(S))) * rate
    disc = 0.0
    if g.waiver and g.waiver.discount_pct > 0:
        disc = sum(max(0.0, g.weekly_due[t] - ship[t]) for t in range(len(S))) * g.waiver.discount_pct / 100.0 * g.unit_price_krw
    comp = float(g.fixed_slot_compensation_krw or 0) if (relaxed and g.fixed_slot_compensation_krw) else 0.0
    return late + disc + comp


def to_scenario(
    sid: str, label: str, inp: SolverInput, raw: RawSolution, used_levers: tuple[str, ...], policy: ApprovalPolicy = DEFAULT_POLICY
) -> Scenario:
    week0 = date.fromisoformat(inp.week0_monday)
    H = inp.horizon_weeks
    cap = inp.capacity

    revenue = contribution = recall = 0.0
    bid_units = sum(raw.bid_ship) if raw.bid_ship else 0.0
    if inp.bid is not None:
        revenue = bid_units * inp.bid.unit_price_krw
        recall = bid_units * inp.bid.recall_cost_per_unit_krw
        contribution = bid_units * (inp.bid.unit_price_krw - inp.bid.unit_cost_krw)
    premium = sum(raw.overtime) * (cap.overtime_multiplier - 1.0) * cap.conversion_cost_krw if raw.overtime else 0.0
    if cap.outsourcing and raw.outsourced:
        for t in range(H):
            active = [p.cost_multiplier for p in cap.outsourcing if t >= p.start_week]
            if active:
                premium += raw.outsourced[t] * (max(active) - 1.0) * cap.unit_cost_krw
    capex = sum(o.capex_krw for o in cap.options if o.option_id in raw.options_selected)

    impacts: list[CustomerImpact] = []
    penalty_total = recall
    relaxed_groups = {k.split(":", 1)[0] for k in raw.relaxations}
    ontime_all = total_all = 0.0
    min_term_margin = None
    for g in inp.groups:
        ship = raw.ship.get(g.group_id, tuple(0.0 for _ in range(H)))
        delayed, worst_w, ontime, total = group_delay(g, ship)
        ontime_all += ontime
        total_all += total
        pen = penalty_krw(g, ship, g.group_id in relaxed_groups)
        penalty_total += pen
        evidence = [c for c in (g.penalty_clause_id, g.hard_clause_id, g.waiver.clause_id if g.waiver else None) if c]
        if delayed > 0 and not evidence:
            evidence = ["ASSUMPTION:default_late_penalty"]
        risk = g.max_late_weeks is not None and (worst_w >= g.max_late_weeks if g.max_late_weeks > 0 else worst_w > 0)
        if g.hard_clause_id and g.max_late_weeks:
            margin = (g.max_late_weeks + 1) * 7 + 2 - worst_w * 7  # 해지 트리거(예: 30일)까지 남은 일수 근사
            min_term_margin = margin if min_term_margin is None else min(min_term_margin, margin)
        impacts.append(CustomerImpact(
            customer_id=g.group_id, orders_delayed=delayed, max_delay_days=worst_w * 7, penalty_krw=int(round(pen)),
            evidence_clause_ids=tuple(evidence), termination_risk=bool(risk and g.hard_clause_id),
        ))

    schedule: list[ScheduleEntry] = []
    if raw.base_use:
        for t in range(H):
            wk = week_label(week0, t)
            for kind, arr in (("base", raw.base_use), ("overtime", raw.overtime), ("option", raw.option_use), ("outsourced", raw.outsourced)):
                q = int(round(arr[t]))
                if q > 0:
                    schedule.append(ScheduleEntry(week=wk, line_id=cap.line_id, product_id=inp.product_id, customer_id=None, qty=q, kind=kind))
            for g in inp.groups:
                q = int(round(raw.ship[g.group_id][t]))
                if q > 0:
                    schedule.append(ScheduleEntry(week=wk, line_id=cap.line_id, product_id=inp.product_id, customer_id=g.group_id, qty=q, kind="base"))
            if inp.bid and raw.bid_ship[t] > 0.5:
                schedule.append(ScheduleEntry(week=wk, line_id=cap.line_id, product_id=inp.product_id, customer_id=inp.bid.bid_id, qty=int(round(raw.bid_ship[t])), kind="base"))

    actions = list(raw.options_selected)
    if raw.overtime and sum(raw.overtime) > 1:
        actions.append("overtime")
    if raw.outsourced and sum(raw.outsourced) > 1:
        actions.extend(f"outsourcing:{p.partner_id}" for p in cap.outsourcing)

    needs: list[ApprovalNeed] = []

    def need(action: str) -> None:
        r = policy.rule_for(action)
        if all(n.action != action for n in needs):
            needs.append(ApprovalNeed(action=action, approver=r.approver.value))

    if raw.accept > 1e-6:
        need("bid_participation")
    if raw.options_selected:
        need("capex_investment")
    if raw.outsourced and sum(raw.outsourced) > 1:
        need("outsourcing_contract")
    if any(g.waiver and g.waiver.discount_pct > 0 for g in inp.groups):
        need("price_renegotiation_C-101")

    infeas = None
    if raw.relaxations:
        infeas = Infeasibility(min_relaxation=tuple(RelaxationItem(constraint=k, slack=v) for k, v in sorted(raw.relaxations.items())))

    requested = sum(inp.bid.weekly_requested * min(1.0, max(0.0, (t - inp.bid.start_week + 1) / inp.bid.ramp_weeks)) for t in range(H) if t >= inp.bid.start_week) if inp.bid else 0.0
    prod = sum(raw.base_use) + sum(raw.overtime) + sum(raw.option_use) + sum(raw.outsourced) if raw.base_use else 0.0
    kpis = {
        "otif_existing_pct": round(100.0 * ontime_all / total_all, 2) if total_all else 100.0,
        "bid_accept_pct": round(100.0 * raw.accept, 2),
        "bid_units_in_horizon": round(bid_units),
        "bid_units_requested_in_horizon": round(requested),
        "base_utilization_pct": round(100.0 * sum(raw.base_use) / (cap.base_weekly * H), 2) if raw.base_use else 0.0,
        "overtime_units": round(sum(raw.overtime)) if raw.overtime else 0,
        "option_units": round(sum(raw.option_use)) if raw.option_use else 0,
        "outsourced_units": round(sum(raw.outsourced)) if raw.outsourced else 0,
        "recall_expected_krw": round(recall),
        "total_production_units": round(prod),
    }
    if min_term_margin is not None:
        kpis["min_days_to_termination_trigger"] = float(min_term_margin)

    return Scenario(
        scenario_id=sid, label=label,
        weights={"w_customer": inp.weights.w_customer, "w_profit": inp.weights.w_profit, "w_premium": inp.weights.w_premium},
        accepted_pct={inp.product_id: round(raw.accept, 4)}, capacity_actions=tuple(actions),
        financials=Financials(
            incremental_revenue_krw=int(round(revenue)), contribution_krw=int(round(contribution)),
            penalty_krw=int(round(penalty_total)), premium_cost_krw=int(round(premium)), capex_krw=int(capex),
        ),
        customer_impact=tuple(impacts), schedule=tuple(schedule), approvals_required=tuple(needs),
        used_lever_ids=used_levers, infeasibility=infeas, kpis=kpis,
    )
