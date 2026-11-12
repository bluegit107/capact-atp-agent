"""P0 스파이크(로컬 실행기). 모델은 dealcockpit.adapters.solver_probe 로 이동했다(중복 제거).

사용: PYTHONPATH=backend/src python spikes/solver_spike.py
axhub 서버(XS) 실측은 임시 엔드포인트로 1회 수행했고(결과: solver_spike_report_axhub_xs.json) 엔드포인트는 제거했다.
"""
from __future__ import annotations

import json
from pathlib import Path

from dealcockpit.adapters.solver_probe import run_suite

if __name__ == "__main__":
    report = run_suite()
    out = Path(__file__).with_name("solver_spike_report_local.json")
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
