"""원본 데이터룸 읽기 전용 로더. 원본 폴더는 절대 수정하지 않는다."""
from __future__ import annotations

import csv
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RAW = ROOT / "01_참가자_배포용" / "04_원본데이터"
COREVIA = RAW / "01_corevia"


def read_pipe_csv(path: Path) -> list[dict[str, str]]:
    """파이프(|) 구분, UTF-8, 헤더 1행 (data_dictionary 고정 형식)."""
    with path.open(encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f, delimiter="|"))
