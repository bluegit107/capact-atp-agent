"""시나리오 규칙 기반 모의 모델(MockClaude). API 키 없이 에이전트 전체 경로를 돌리기 위한 대역.

- Anthropic SDK 와 같은 모양(`client.beta.messages.create(...)` → `content`, `stop_reason`)으로 응답한다.
  그래서 실행 루프, 도구 계층, G2 숫자 검증기, 권한·가드레일이 실제 모델일 때와 똑같이 적용된다.
- 내용은 도구 결과(원장 사진·위험 표시)만 보고 정해진 문형으로 만든다. 숫자는 쓰지 않고 {{calc:id}} 자리표시자만.
- 실제 모델로 바꾸려면 AGENT_MODE=live 와 ANTHROPIC_API_KEY 만 설정하면 된다(docs/INTEGRATIONS.md).
"""
from __future__ import annotations

import json
from types import SimpleNamespace as NS

MOCK_MODEL = "mock-claude(시나리오 규칙)"
SEV_TEXT = {
    "RF-PROD-01": "평균 가동률에 가려진 병목입니다. 생산은 라인 단위 여력으로 판단해야 합니다.",
    "RF-PROD-02": "기존 확정 주문만으로 넘치는 주가 있습니다. 승인권자는 해당 주의 출하 조정 가능성을 확인해야 합니다.",
    "RF-PROD-03": "설비 준비와 납품 개시 사이 여유가 짧습니다. 설비 일정 지연 시 바로 미충족이 됩니다.",
    "RF-PROC-01": "원료 재계약 회신 전에는 이 대안을 상신할 수 없습니다. 조달 회신이 선행 조건입니다.",
    "RF-FIN-01": "설비투자가 그룹 가용자금의 큰 몫을 차지합니다. 재무는 다른 배분안과 함께 검토해야 합니다.",
    "RF-SALES-01": "최대 고객의 수주 감소 원인이 확인되지 않았습니다. 여력으로 계산하지 않으며 영업 임원 확인 대상입니다.",
    "RF-SALES-02": "상태 불명 주문이 실제 미납이면 여력이 줄어듭니다. 영업 확인이 필요합니다.",
    "RF-QUAL-01": "확정 클레임 이력이 있는 제품입니다. 리콜 비용이 공급자 부담이므로 품질 확인이 필요합니다.",
}


def _block(i, name, inp):
    return NS(type="tool_use", id=f"mock_{name}_{i}", name=name, input=inp)


class _Messages:
    def create(self, **kw):
        msgs = kw["messages"]
        results = {}
        for m in msgs:
            if m["role"] == "user" and isinstance(m["content"], list):
                for r in m["content"]:
                    if r.get("type") == "tool_result":
                        results[r["tool_use_id"]] = r["content"]
        trigger = msgs[0]["content"].split("\n", 1)[0].replace("계기 사건: ", "")
        step = sum(1 for m in msgs if m["role"] == "assistant")
        if step == 0:
            return NS(stop_reason="tool_use", content=[_block(0, "ledger_get_bid_snapshot", {}), _block(1, "calc_risk_flags", {}), _block(2, "ledger_list_assumptions", {})])
        snap = json.loads(results.get("mock_ledger_get_bid_snapshot_0", "{}"))
        flags = json.loads(results.get("mock_calc_risk_flags_1", "[]"))
        assum = json.loads(results.get("mock_ledger_list_assumptions_2", "[]"))
        opts = snap.get("options", [])
        if step == 1:
            calls = []
            pick, seen = [], set()  # 도구 호출 한도(12회) 안에서: 차단 먼저, 규칙별 하나씩, 최대 다섯
            for f in sorted([f for f in flags if f["severity"] in ("WARN", "BLOCK")], key=lambda f: (f["severity"] != "BLOCK", f["id"])):
                if f["rule"] not in seen and len(pick) < 5:
                    pick.append(f); seen.add(f["rule"])
            for i, f in enumerate(pick):
                ev = [e for e in f["evidence"] if e]
                text = SEV_TEXT.get(f["rule"], "확인이 필요합니다.") + (" 근거 {{calc:%d}}." % ev[0] if ev else "")
                calls.append(_block(i, "risk_flag_explain", {"flag_id": f["id"], "text": text}))
            for i, o in enumerate(opts[:3]):
                fills = [v for k, v in o["metric_ids"].items() if k.startswith("fill_")]
                cap = ", ".join(o["capex"]) if o["capex"] else "설비안 없이"
                txt = f"대안 {o['label']}은 {cap} 기준이며 충족률은 " + ", ".join("{{calc:%d}}" % m for m in fills) + "입니다."
                if o["blocking"]:
                    txt += " 막는 조건이 있어 지금은 상신할 수 없습니다."
                calls.append(_block(100 + i, "option_explain", {"option_id": o["id"], "text": txt}))
            if calls:
                return NS(stop_reason="tool_use", content=calls)
        crit = [a for a in assum if a["critical"] and a["status"] == "UNCONFIRMED"]
        feasible = [o for o in opts if o["feasibility"] == "FEASIBLE" and o["status"] == "PROPOSED"]
        pref = max(feasible, key=lambda o: (len(o["metric_ids"]), -len(o["capex"])), default=None)
        if not opts:
            summary = "판단에 필요한데 원본에 없는 정보를 찾아 확인요청을 만들었습니다. 핵심 정보가 확인되면 대안을 계산합니다."
            nxt = [{"actor_role": "영업", "action": "확인요청 초안 검토 후 발송"}]
        elif not feasible and not [o for o in opts if o["feasibility"] == "CONDITIONAL"]:
            summary = "현재 조건에서는 기존 약속을 지키면서 실행 가능한 대안이 없습니다. 참여 비권고 또는 입찰 조건 재협상을 검토해야 합니다."
            nxt = [{"actor_role": "영업", "action": "입찰 조건 재협상 또는 참여 비권고 검토"}]
        elif crit:
            summary = "대안을 계산했지만 핵심 정보가 아직 확인되지 않아 일부 대안은 조건부입니다."
            nxt = [{"actor_role": a["owner"], "action": f"{a['label']} 회신"} for a in crit]
        else:
            summary = "핵심 정보가 확인되어 대안을 계산했습니다. 주 단위 원장 기준으로 기존 약속을 지키면서 채울 수 있는 물량만 제안합니다."
            nxt = [{"actor_role": "영업", "action": f"대안 {pref['label']} 검토 후 상신" if pref else "상신 가능한 대안 없음, 조건 재협상 검토"}]
        if trigger == "ASSUMPTION_SUPERSEDED":
            summary = "전제가 바뀌어 이전 승인과 예약을 되돌리고 대안을 다시 계산했습니다. " + summary
        if trigger == "APPROVAL_DECIDED":
            summary = "승인 결정에 따라 남은 선택지를 다시 계산했습니다. " + summary
        if trigger == "DEADLINE_TICK":
            summary = "마감이 가까워 회신이 없는 확인요청을 정리했습니다. " + summary
            nxt.insert(0, {"actor_role": "영업", "action": "미회신 확인요청 독촉"})
        out = {"situation_summary": summary, "gaps": [{"key": a["key"], "why_needed": f"{a['label']} 없이는 상신할 수 없습니다", "proposed_action": "info_request"} for a in crit],
               "option_ids": [o["id"] for o in opts],
               "ai_opinion": {"preferred_option_id": pref["id"] if pref else None,
                              "reasoning": (f"대안 {pref['label']}이 기존 약속을 지키면서 막는 조건 없이 채울 수 있습니다." if pref else "가능한 대안이 없어 선호를 정하지 않습니다."),
                              "criteria": ["기존 약속 보호", "막는 조건 없음", "설비투자 최소"]} if opts else None,
               "next_actions": nxt, "open_risks": ["C-101 감소 원인은 확인 필요"], "risk_flag_ids": [f["id"] for f in flags if f["severity"] in ("WARN", "BLOCK")][:8]}
        if trigger == "ASSUMPTION_SUPERSEDED":
            out["impact_of_change"] = {"text": "이전 대안, 승인, 작업, 예약은 무효가 되었고 새 전제로 다시 계산했습니다."}
        return NS(stop_reason="tool_use", content=[_block(900, "finish", out)])


class MockClaude:
    """`anthropic.Anthropic()` 자리에 끼우는 대역. 인터페이스: client.beta.messages.create(**kwargs)."""

    def __init__(self):
        self.beta = NS(messages=_Messages())
