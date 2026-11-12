# 외부 연결 안내 (모의 → 실제 전환)

대회 환경에서는 온프레미스 배포와 개인 API 키 등록이 어려워, **모든 외부 연결을 같은 인터페이스의 모의 구현으로 끼워 두었다.**
실제 연결은 코드 수정 없이(또는 어댑터 1개 교체로) 바꾼다. 현재 상태는 관리 계정으로 `GET /api/integrations`(화면: 설정 → 외부 연결)에서 확인한다.

## 1. 한눈에

| 연결 | 지금(모의) | 실제로 바꾸는 법 | 바꾸는 자리 |
|---|---|---|---|
| AI 에이전트 | `AGENT_MODE=mock` 시나리오 규칙 모델(`w05/mock_llm.py`) | `AGENT_MODE=live`, `ANTHROPIC_API_KEY`, 선택 `ANTHROPIC_MODEL` | 환경변수만 |
| 녹화 재생 | `AGENT_MODE=replay` (`agent_replay/`) | live 1회 실행 시 자동 녹화 | 환경변수만 |
| 부서 데이터(ERP, MES) | 원본에서 도출한 파일 `extensions/derived/w05_engine_inputs.json` | 같은 JSON 형식을 내는 커넥터 | `adapters/golden_loader.load_w05_inputs` |
| 외부 발송(바이어, 공급사) | 외부 수신함에 모의 기록(`Msg`, `is_mock=true`) | 메일·메신저 어댑터가 `direction=OUT` 메시지를 발송 | `w05/flow.py` 의 `send_request`, `PROCUREMENT_RECONTRACT` 완료 |
| 저장소 | PostgreSQL(배포) / 메모리(로컬) | `DATABASE_URL` | 환경변수만 |
| 보조 최적화 | OR-Tools LP(SCIP) | 그대로 | `adapters/solver_lp.py` |

## 2. AI 에이전트 계약

- 실행 루프: `w05/agent.py` `run_agent()` — 수동 루프(서버가 도구 실행, 12회·60초 한도, `finish` 검증, 1회 재시도, 실패 시 사람 확인 작업).
- 요청 형식: `request_params()` 하나로 고정(모의·실제 같은 값).
  - `POST /v1/messages`(beta), 모델 `claude-opus-5-5`, `max_tokens 16000`, `output_config.effort=medium`
  - 거절 대비 `betas=["server-side-fallback-2026-07-01"]`, `fallbacks="default"`
  - `tool_choice` 는 쓰지 않음(현재 모델은 강제 도구 호출 미지원). `finish` 호출은 프롬프트로 요구.
- 도구 15개(`TOOLS`): 조회 10, 쓰기 제안 4(`assumption_propose`, `info_request_draft`, `option_explain`, `risk_flag_explain`), 종료 `finish`.
  승인·가정 확정·발송·작업 완료 도구는 **없다**(결정은 사람, G1).
- 숫자 규칙(G2): 모든 문장은 `{{calc:id}}`, `{{assume:id}}` 자리표시자만. `validate_text()` 가 서버에서 검사.
- 모의 모델(`MockClaude`)은 Anthropic SDK 와 같은 모양(`client.beta.messages.create(**kw)` → `content`, `stop_reason`)이라 루프·도구·검증기가 실제와 똑같이 돈다.

### 실제 모델 연결 점검 순서
1. 환경변수 `AGENT_MODE=live`, `ANTHROPIC_API_KEY` 등록(저장소에 넣지 않음).
2. 관리 계정으로 `GET /api/integrations` → `llm.status = ok`, `api_key_present = true` 확인.
3. 시연 계정으로 S1 을 한 번 진행 → 입찰 요약의 `AI 작성` 배지, 도구 호출 목록 확인.
4. 녹화 파일 `agent_replay/manual/*.json` 생성 확인 → 저장소에 올리면 키 없이 `AGENT_MODE=replay` 로 재현.
5. 회귀: `python -m pytest tests/test_w05_agent.py` (모의·녹화·불일치·한도 시험).

## 3. 부서 데이터 커넥터 계약

`load_w05_inputs()` 가 돌려주는 JSON 키(필수): `capacity`(라인·제품·capacity_qty·가동률), `production_h1`, `open_orders`, `capex`(대상 라인·제품·선행 요건),
`material_cost_q2`, `seeds.rfp_lines`, `fx_close`, `c101_yoy`, `defects_by_product`, `products`, `files`(근거 드로어용 원본 경로).
모든 행에 원본 행 번호 `_row` 를 유지하면 근거 드로어 3단이 그대로 동작한다.

## 4. 다른 데이터셋을 넣을 때: 어디까지 실데이터이고 어디부터 모의인가

| 층 | 내용 | 다른 데이터셋에서 |
|---|---|---|
| 1. 원본 파일 | `01_참가자_배포용` CSV·문서, 읽기 전용, 체크섬 고정 | 같은 파일 형식이면 그대로 |
| 2. 도출 스크립트 | `scripts/derive_w05_inputs.py` 가 수주·출하·배치·용량·설비안·원료·클레임·환율을 계산 | 같은 열 이름이면 그대로. RFP 조건, 권한표, 그룹 현금은 문서 상수로 손 입력(출처 줄과 원문 일치 검사 포함) |
| 3. 입력 파일 계약 | `extensions/derived/w05_engine_inputs.json` (ERP, MES 커넥터가 대신할 자리) | 이 JSON 만 맞추면 엔진은 그대로 |
| 4. 엔진 | 라인, 제품, 설비안, 거래처, 기준일(`as_of`)에서 원장 시작 주, 제출 마감(`submission_deadline_days`, 기본 21일), 설비 결정일(`board_offset_days`, 기본 5일) | 데이터로 흡수(`test_w05_extensibility.py`) |
| 5. 아직 고정 | 입찰 1건(C-201), 외부 상대(C-201 바이어, V-205), C-101 수요 규칙(G7), 위험 표시 규칙 문구, 시연 준비 날짜(`scenarios.py`) | 코드 수정 필요 |
| 모의 | AI 모델(MockClaude), 바이어·공급사 회신, 외부 발송, 보조 분석 화면의 C-101·C-102 계약서(합성) | 실제 연결로 교체 |

## 5. 확장성(데이터 구성 변화)

라인·제품·설비안·거래처는 **코드가 아니라 데이터에서 읽는다**(`engine.topology()`).
- 라인 증설·신설: `capacity` 행 추가 → 원장·보드·트윈에 자동 반영. 같은 제품을 여러 라인이 만들면 실적을 capacity_qty 비율로 나눔.
- 새 입찰 품목: `seeds.rfp_lines` 행 추가 → 대안 구성(전량·최대·설비 대상만/설비 무관만)이 그 제품까지 포함.
- 새 설비안: `capex` 행 추가 → 대상 라인·제품·선행 요건을 규칙으로 도출(`scripts/derive_w05_inputs.py` `enrich_capex`), 선행 요건은 핵심 가정·확인요청·차단 표시로 자동 연결.
- 새 거래처: 주문·출하 행만 추가 → 기존 약속·거래처 이력에 자동 포함.
- 시험: `tests/test_w05_extensibility.py`(F3 공장 L-21 라인, 새 제품 PL-07, 증설안 OPT-03, 전환안 OPT-04, 새 고객 C-113).
- 아직 데이터화하지 않은 것: 승인 생성 규칙의 결정 종류(입찰 참여, 설비투자), C-101 전용 수요 규칙(G7), 위험 표시 규칙 표 자체.
