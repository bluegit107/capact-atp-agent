# capact

대형 입찰을 받아도 기존 고객 납품 약속을 지킬 수 있는지 판단하는 업무 도구입니다. AX 인재전쟁 과제로 만들었습니다.

라인별, 주별 약속 가능 물량(ATP) 원장으로 입찰 물량이 기존 주문과 함께 들어가는지 계산하고, 바이어 통지나 부서 회신으로 가정이 바뀌면 대안, 승인, 작업, 생산 예약을 다시 맞춥니다.

## 기능
- 라인 x 주 ATP 원장으로 대안별 충족률과 손익 계산. 손익은 단가 인하율 3단계 x 환율 하락률 3단계, 총 9칸의 재료비 차감 이익으로 봅니다.
- 부족한 정보를 확인요청으로 만들고, 회신을 사람이 확인해 가정으로 반영
- 가정이 바뀌면 대안, 승인, 작업, 생산 예약까지 변경 전파와 변경 전후 비교
- 위험 표시 10종, 지표에서 계산식과 원본 행까지 3단 근거 추적
- 역할별 화면과 권한(영업, 영업임원, 생산, 구매, 품질, 재무, CEO/이사회 등), 감사 이력
- 입찰 마감 추적과 독촉, 참여 보류 처리
- AI는 상황 요약과 설명만 작성. 숫자는 `{{calc:id}}` 자리표시자로만 쓰고 서버가 검증하며, 승인, 발송, 확정 도구는 없음

## 구조
```
backend/src/dealcockpit
  w05/          ATP 원장(engine), 업무 흐름(flow), 화면 데이터(view), 에이전트(agent), 모의 모델(mock_llm)
  domain/       권한표, 가정, 파라미터 레지스트리
  application/  협상 분석(LP 솔버)
  adapters/     FastAPI, 인증, 저장소(메모리, PostgreSQL), OR-Tools
frontend/       바닐라 JS, 빌드 없음
scripts/        입력 파일 도출, 계정 생성
docs/           SOLVER_MODEL, WORKFLOW_AND_DATA, INTEGRATIONS
```

## 실행
Python 3.12가 필요합니다.
```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install -e "backend[dev]"
.venv/bin/python scripts/make_accounts.py ../accounts_env.json
export PYTHONPATH=backend/src CAPACT_COOKIE_SECURE=0 PORT=8099
export CAPACT_ACCOUNTS="$(cat ../accounts_env.json)"
.venv/bin/python -m dealcockpit.adapters.api
```
Windows는 `.venv\Scripts\python.exe`를 쓰고 환경변수는 PowerShell로 지정합니다.

http://localhost:8099 에서 `demo` 계정으로 로그인하면 왼쪽 아래 `시연` 메뉴에서 시나리오 시작 상태와 가상 시계를 고를 수 있습니다. 비밀번호는 계정 생성 때 무작위로 만들어져 `docs/TEST_ACCOUNTS.md`에 적힙니다.
`DATABASE_URL`이 없으면 메모리 저장소를 쓰며 재시작하면 초기화됩니다.

계산에 쓰는 입력 파일(`extensions/derived/`)은 대회 제공 데이터에서 `scripts/derive_*.py`로 만들며 이 저장소에는 없습니다.

## 시험
```bash
cd backend && ../.venv/bin/python -m pytest -q
```
입력 파일이 없으면 일부 시험은 실패합니다. PostgreSQL 시험은 `CAPACT_TEST_PG_URL`이 있을 때만 돕니다.

## AI 연결
기본은 같은 인터페이스를 가진 규칙 기반 모의 모델(`MockClaude`)입니다. `AGENT_MODE=live`와 `ANTHROPIC_API_KEY`를 지정하면 Claude API를 호출합니다. 자세한 내용은 `docs/INTEGRATIONS.md`, `docs/LIVE_OPENROUTER_NOTES.md`를 보세요.

실제로 동작하는 것은 ATP 원장, 손익 계산, 위험 표시, 근거 추적, 상태 전이, 권한, 변경 전파, 마감 추적, 감사 이력입니다. 바이어와 공급사 회신, 외부 발송, ERP와 MES 연동은 모의 또는 파일로 대신합니다.

## 라이선스
글꼴은 Pretendard Variable(SIL OFL 1.1)이며 `frontend/fonts/LICENSE-Pretendard.txt`에 있습니다. 시나리오의 회사, 고객, 수치는 가상입니다.
