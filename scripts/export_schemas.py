"""Pydantic(단일 진실) -> JSON Schema 내보내기. 프런트 TS 타입 생성과 계약 테스트의 입력이 된다."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend" / "src"))

from dealcockpit.domain.approval import ApprovalPolicy, ApprovalRequest  # noqa: E402
from dealcockpit.domain.assumption import Assumption  # noqa: E402
from dealcockpit.domain.bid import Bid  # noqa: E402
from dealcockpit.domain.clause import Clause  # noqa: E402
from dealcockpit.domain.lever import Lever  # noqa: E402
from dealcockpit.domain.parameters import ParameterRegistry, ProposedChange  # noqa: E402
from dealcockpit.domain.plan import AuditEvent, PlanOfRecord  # noqa: E402
from dealcockpit.domain.plant import PlantModel  # noqa: E402
from dealcockpit.domain.solution import SolverOutput  # noqa: E402
from dealcockpit.domain.workflow import InfoRequest, Reply, Task  # noqa: E402

MODELS = {
    "lever": Lever, "assumption": Assumption, "clause": Clause, "bid": Bid, "plant_model": PlantModel,
    "solver_output": SolverOutput, "info_request": InfoRequest, "reply": Reply, "task": Task,
    "approval_request": ApprovalRequest, "approval_policy": ApprovalPolicy, "plan_of_record": PlanOfRecord, "audit_event": AuditEvent,
    "parameter_registry": ParameterRegistry, "proposed_change": ProposedChange,
}


def main() -> None:
    out_dir = ROOT / "schemas"
    out_dir.mkdir(exist_ok=True)
    for name, model in MODELS.items():
        path = out_dir / f"{name}.schema.json"
        path.write_text(json.dumps(model.model_json_schema(), ensure_ascii=False, indent=2), encoding="utf-8")
        print("wrote", path.relative_to(ROOT))


if __name__ == "__main__":
    main()
