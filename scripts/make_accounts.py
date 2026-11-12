"""테스트 계정 생성: 역할별 1개. 비밀번호는 무작위로 만들어 docs/TEST_ACCOUNTS.md(저장소 제외)에만 적고,
배포용 환경변수 값(해시만)은 extensions 밖 임시 파일로 출력한다. 비밀번호 원문은 화면에 출력하지 않는다.

사용: python scripts/make_accounts.py <env 출력 파일 경로>
"""
from __future__ import annotations

import json
import secrets
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend" / "src"))

from dealcockpit.adapters.auth import hash_password  # noqa: E402

ACCOUNTS = [
    ("sales", "영업", "입찰 검토 개설·문의 발송·상신"),
    ("sales_exec", "영업임원", "고객 납기 변경 승인·손실 감수 상신"),
    ("production", "생산", "용량 회신·특근 승인·외주 역제안 판단"),
    ("quality", "품질", "처방 재검증 회신·회수 의결 입력·회수 파라미터"),
    ("procurement", "구매", "M-B 재계약 회신"),
    ("finance", "재무", "환율·단가 조건 변경"),
    ("ceo", "CEO/이사회", "입찰 참여·투자·외주 승인"),
    ("policy_admin", "정책관리자", "승인 권한표 관리(업무 승인 권한 없음)"),
    ("reviewer", "심사자", "읽기 전용 열람"),
    ("demo", "시연 진행자", "외부(바이어·V-205) 모의 회신·가상 시계·역할 전환(대리 수행 기록)"),
]


def add(env_out: Path, users: list[str]) -> None:
    """기존 계정·비밀번호는 그대로 두고 지정 계정만 추가(사용: make_accounts.py <env 파일> --add demo)."""
    env = json.loads(env_out.read_text(encoding="utf-8"))
    doc = ROOT / "docs" / "TEST_ACCOUNTS.md"
    lines = doc.read_text(encoding="utf-8").rstrip("\n").split("\n")
    for user, role, desc in ACCOUNTS:
        if user in users and user not in env:
            pw = secrets.token_urlsafe(12)
            env[user] = {"role": role, "hash": hash_password(pw)}
            lines.append(f"| `{user}` | `{pw}` | {role} | {desc} |")
    doc.write_text("\n".join(lines) + "\n", encoding="utf-8")
    env_out.write_text(json.dumps(env, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"추가: {users} → {doc.relative_to(ROOT)} (git 제외), 해시 env 값 → {env_out}")


def main() -> None:
    env_out = Path(sys.argv[1])
    if "--add" in sys.argv:
        return add(env_out, sys.argv[sys.argv.index("--add") + 1:])
    rows, env = [], {}
    for user, role, desc in ACCOUNTS:
        pw = secrets.token_urlsafe(12)
        env[user] = {"role": role, "hash": hash_password(pw)}
        rows.append(f"| `{user}` | `{pw}` | {role} | {desc} |")
    doc = ROOT / "docs" / "TEST_ACCOUNTS.md"
    doc.write_text("# 테스트 계정 (저장소 제외 — 제출 문서에만 기재)\n\n| 아이디 | 비밀번호 | 역할 | 할 수 있는 일 |\n|---|---|---|---|\n"
                   + "\n".join(rows) + "\n", encoding="utf-8")
    env_out.write_text(json.dumps(env, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"계정 {len(ACCOUNTS)}개: 비밀번호 → {doc.relative_to(ROOT)} (git 제외), 해시 env 값 → {env_out}")


if __name__ == "__main__":
    main()
