"""필수 3케이스 실행 → evidence/*.json. 저장소: 기본 메모리, CAPACT_PG_URL 이 있으면 PostgreSQL."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend" / "src"))

from dealcockpit.adapters.demo_cases import case_change, case_normal, case_reject  # noqa: E402
from dealcockpit.adapters.repo_memory import MemoryRepository  # noqa: E402
from dealcockpit.adapters.solver_lp import LpSolver  # noqa: E402


def main() -> None:
    repo = MemoryRepository()
    if os.environ.get("CAPACT_PG_URL"):
        from dealcockpit.adapters.repo_postgres import PostgresRepository

        repo = PostgresRepository(os.environ["CAPACT_PG_URL"], os.environ.get("CAPACT_DATA_KEY"), migrate=True)
    solver = LpSolver("SCIP")
    out_dir = ROOT / "evidence"
    out_dir.mkdir(exist_ok=True)
    for name, fn in (("case1_normal", case_normal), ("case2_change", case_change), ("case3_info_gap_reject", case_reject)):
        r = fn(repo, solver)
        audit = [e.model_dump(mode="json") for e in repo.list_audit(r.ws_id)]
        (out_dir / f"{name}.json").write_text(json.dumps({"workspace": r.ws_id, "steps": r.log, "audit": audit}, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"\n### {name} ({r.ws_id}) — 감사 이벤트 {len(audit)}건")
        for s in r.log:
            print(" ", json.dumps(s, ensure_ascii=False))


if __name__ == "__main__":
    main()
