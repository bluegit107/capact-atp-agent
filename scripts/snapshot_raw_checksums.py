"""원본 보존 증명: 데이터룸 전체 파일의 SHA-256 을 기록하고(--write) 이후 변경 여부를 검증한다(기본).

A05: "원본 폴더는 그대로 보존" — 우리가 원본을 건드리지 않았다는 것을 재현 가능하게 보인다.
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

from _rawdata import RAW, ROOT

OUT = ROOT / "deal-cockpit" / "extensions" / "raw_checksums.json"


def snapshot() -> dict[str, str]:
    return {
        p.relative_to(RAW).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(RAW.rglob("*")) if p.is_file()
    }


def main() -> int:
    current = snapshot()
    if "--write" in sys.argv:
        OUT.write_text(json.dumps({"root": "01_참가자_배포용/04_원본데이터", "files": current}, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"기록: {len(current)}개 파일")
        return 0
    recorded = json.loads(OUT.read_text(encoding="utf-8"))["files"]
    changed = sorted(k for k in current if recorded.get(k) != current[k])
    missing = sorted(set(recorded) - set(current))
    print(f"검증: 변경 {len(changed)}건, 삭제 {len(missing)}건, 신규 {len(set(current) - set(recorded))}건")
    for k in (changed + missing)[:10]:
        print("  !", k)
    return 1 if (changed or missing) else 0


if __name__ == "__main__":
    raise SystemExit(main())
