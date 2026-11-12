# live(OpenRouter) 녹화 때 바뀐 점 (2026-11-12)

기본은 mock(`AGENT_MODE` 미설정)입니다. 아래는 OpenRouter 로 실제 모델을 연결해 돌릴 때를 위한 변경이며 mock 동작은 바뀌지 않습니다.

| 파일 | 변경 | mock 영향 |
|---|---|---|
| `backend/src/dealcockpit/w05/agent.py` | `ANTHROPIC_BASE_URL` 에 `openrouter` 가 있으면 `betas`·`fallbacks` 제외. 코드 뒤 조사(`L-02는`)를 코드로 인식(숫자 검증기). 시스템 프롬프트에 호출 예산·위험표시 대표 5건·`확인 필요` 중복 금지 추가 | 없음(프롬프트는 live 에서만 쓰임, 검증기는 완화만) |
| `backend/src/dealcockpit/w05/view.py` | 확인요청 항목(`gaps`)의 `{{calc:id}}` 도 화면 값으로 치환 | 없음 |
| `backend/src/dealcockpit/adapters/api.py` | `/api/integrations` 에서 `betas`·`fallbacks` 없어도 동작, 엔드포인트 표시에 `ANTHROPIC_BASE_URL` 반영 | 없음 |
| `frontend/w05.js` | 이력의 AI 표기: mock 이면 `AI(모의)`, 아니면 `AI` | 없음 |
| `frontend/app.js` | 설정의 외부 연결 안내 문구: AI 가 live 일 때만 다른 문장 | 없음 |

## live 로 다시 돌리려면
```
export AGENT_MODE=live ANTHROPIC_BASE_URL=https://openrouter.ai/api ANTHROPIC_MODEL=anthropic/claude-sonnet-5.5
export ANTHROPIC_API_KEY=<OpenRouter 키>   # 저장소에 넣지 않는다
```
- live 실행은 `agent_replay/manual/` 에 녹화 JSON 을 쓴다(배포 압축본에는 넣지 않는다).
- 요청 하나가 AI 호출(10~25초) 동안 열려 있어, 그 사이 다른 쓰기 요청이 겹치면 낙관적 락 충돌(409)이 난다. 시연 때는 한 번에 하나씩 누른다.
