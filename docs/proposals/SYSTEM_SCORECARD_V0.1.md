# 제안: 시스템 전체 점수 — 무엇이 지키고, 무엇이 아직 가치를 못 내는가 (v0.1)

**상태:** IMPLEMENTED 2026-10-06 — Q1–Q6 모두 결정(Thomas). Q1: 코어 백업 age 암호화 가동(#1024, 2026-10-06; 첫 키 노출로 같은 날 키 교체). Q2 (a): 체인 `google_ai_studio,openrouter,groq`. Q3: §3 처리안 권고대로. Q4 (a): D8 범위에서 `general.specialist` 제외. Q5: 에포크 경계는 cohort 마감마다, 첫 경계 2027-03-22. Q6 (c): D5는 첫 cohort 판정 때 묻는다.

**기준 시점:** origin/main `222b50a2`(#1128), 2026-10-05. 운영 상태는 호스트에서 읽기 전용으로 쟀다.
**선례:** `SYSTEM_REVIEW_IMPROVEMENT_PLAN_V0.1.md`(09-26 전수 점검, 점수 없음),
`CRYPTO_STRATEGY_EDGE_ORDER_V0.1.md`(10-01, 크립토 전략 4/10). 이 문서는 둘을 시스템 전체로 넓혀 점수를 매기고,
크립토 전략 점수는 10-01 이후 움직인 것만 적는다.
**표기:** **[확인]** 이 문서를 쓰며 직접 잼, **[문서]** 저장소 기록, **[추정]** 추론.
**한계:** 라이브 돈 경로·키는 건드리지 않았다. 시크릿은 이름과 지문(sha256 앞 8자)만 봤고 값은 읽지 않았다. 백업
아카이브는 멤버 이름만 나열했다. 블로그의 트래픽·수익 성과는 재지 않았다.

---

## 0. 한 줄

**지키는 장치는 9점, 지켜지는 가치는 4–5점이다.** 안전·거버넌스와 코어 신뢰성은 튼튼하다. 약한 곳은 세 군데다.
첫째, 크립토 엣지는 아직 없다. 둘째, 매일 호스트를 떠나는 백업에 시크릿이 평문으로 들어 있다. 셋째, 결정 대기열이
쌓여 있다. 셋 중 둘은 코드가 아니라 Thomas의 결정이나 행동으로 풀린다.

## 1. 점수 (2026-10-05)

| 영역 | 점수 | 근거 |
|---|---|---|
| 안전·거버넌스 | **9** | 실행 단계 PAPER, LIVE 무장 0/15, `LIVE ENTRY POSSIBLE: NO`(execution_stage, armed_strategy_count). 테스트넷 opt-in·키 모두 비어 있다 [확인]. 단계 사다리 불변식은 테스트로 고정돼 있고, 10-05에 생긴 `Safety invariants lane`은 main에서 6회 모두 녹색이다 [확인] |
| 코어 신뢰성 | **8** | 09-26 점검의 A 항목 두 개가 닫혔다: 잠금 타임아웃(`filelock.py:74`), risk 감시견(#1028·#1029). 컨테이너 8개 모두 healthy [확인]. main CI는 09-28 이후 녹색이다. 감점은 Windows 전용 flaky 테스트 3개(§4.3) |
| 운영·시크릿 | **5** | 오늘 07:45 코어 백업 `govstate-20261005-0745.tar.gz`(0600)에 `thomas_agent/.env`와 `hermes-trial/data/`가 평문으로 들어 있다 [확인: 멤버 이름만]. 이 아카이브는 매일 Mac으로 간다 [문서: #1024]. age 암호화는 09-29에 결정됐고 #1024가 Thomas의 공개키를 기다린다. `/root/backups/age-recipients.txt` 없음 [확인]. 백업 감시는 매일 `OK checks=6` [확인] |
| 분석·콘텐츠 레인 | **7** | 7일: content.general 104회 중 전달 93%, research.general 56회 중 100%, 미검증 전달 0 [확인]. 체인 첫 멤버 OpenRouter가 content 97/104, research 54/56에서 페일오버한다. content p95 40.6초(§4.1) |
| 크립토 전략 엣지 | **4** (10-01과 같음) | cohort 2개(09-23 K=115, 10-01 K=82), 197명 중 확정 0, 반박 9, UNDERPOWERED 4, 나머지 184는 표본 부족 [확인]. 실제−대조군(방향만, 거래당 R): 1d −0.241 → **−0.441**, 4h −0.112 → −0.105, 1h +0.115 → +0.162 [확인]. 오늘 값은 두 cohort를 합친 것이라 방향만 읽는다 |
| 제품 가치·사용 | **5** | 실제로 쓰이는 것은 블로그 엔진(content 104회)과 Hermes를 거친 조사(research 56회)다. 잠긴 MVP 용도 "사업 아이디어 분석"(`general.specialist`)은 7일간 1회다 [확인]. 블로그 성과는 재지 않았다 [추정]. D8 기한 2026-11-25(§4.2) |
| 개발 프로세스 | **6** | 속도와 기록 규율은 높다(10-01 이후 머지 약 40건, 증분마다 `docs/history/`). 병목은 Thomas 결정 대기 제안 13건이다(가장 오래된 것 07-25, §3). 호스트 worktree 30개 |
| **종합** | **6** | 일곱 영역의 단순 평균 6.3 |

## 2. 점수를 올리는 순서

| 순서 | 할 일 | 담당 | 영향 |
|---|---|---|---|
| 1 | Mac에서 만든 `age1…` 공개키를 넘기고 #1024 롤아웃(PR 본문 "Rollout" 4단계) | **Thomas**(Q1) | 운영 5 → 8 |
| 2 | 런타임 체인의 OpenRouter 처리 결정(§4.1) | **Thomas**(Q2) | 레인 7 → 8, 호출마다 버리는 대기 제거 |
| 3 | 결정 대기 13건 처리(§3). 그중 에포크 경계(Q5)와 D5 순환(Q6)이 가장 크다 | **Thomas**(Q3·Q5·Q6) | 프로세스 6 → 7 |
| 4 | `general.specialist`와 D8 기한의 관계 정리(§4.2) | **Thomas**(Q4) | 제품 가치 판단 기준 |
| 5 | Windows flaky 테스트 3개 수정(§4.3) | Claude | 결정 불필요. 신뢰성 8 유지, 재실행 비용 제거 |
| — | 크립토 엣지: 코드로 올릴 레버 없음(D3). 10-29 3차 동결, 1차 cohort 마감 2027-03-22 | 기다림 | — |

## 3. 결정 대기 13건

`STATUS.md`의 "Thomas 결정 대기" 13건을 하나씩 읽고, 이후 기록(`docs/history/`, `REMAINING_WORK.md`, 커밋)과
대조했다 [문서]. 셋은 직접 다시 확인했다 [확인]: K3은 `operator.PEEKABLE_HALT_VERBS`로 구현돼 있다. 프로브는
`LIVE_AUTONOMOUS`가 필요하다(`execution_stage._REQUIRED_STAGE`). RESEARCH_EPOCH Q3의 경계 주기는 "정하지 않았다"로
남아 있다.

**한 줄.** Thomas의 답만으로 닫히는 것이 절반쯤이다. 나머지는 관문을 기다리거나 상태 줄이 지난 사건을 따라가지 못했다.
그리고 둘은 기다려도 풀리지 않는다(§5 Q5·Q6).

| 제안서 | 남은 결정 | 막는 것 | 제안 처리 |
|---|---|---|---|
| CONVERSATIONAL_ORCHESTRATION_FRONT | D4(standing grant), D5(D4에 종속) | Thomas 답. 필요가 확인된 기록이 없다 | **지금 결정(권고대로):** "필요 확인 전 안 함"으로 닫고 APPROVAL V4와 합친다 |
| APPROVAL_CONVERSATION | V1–V4 | 전제가 약해졌다. 대화 창이 Hermes로 옮겼고, 승인 푸시는 07-17부터 `/approve <id>`를 보여 준다 | **폐기 검토:** V2·V3 기각, V4는 D4로 합친다 |
| CONTROL_LANE_SEPARATION | K1, K3, K4, K5 | K3은 이미 구현됨 [확인]. K1은 절반만(`/kill` 응답이 실행 중 분석이 끝까지 돈다는 말을 안 한다) | **지금 결정(권고대로):** K1 채택, K3 종결, K4 보류, K5 소멸 |
| EVALUATION_CANNOT_ACT_PER_STRATEGY | §8 D(연속손실 래치), §8 B, R값 없는 라이브 손실, allowance의 net R 정렬 | D는 Thomas 답. 나머지는 LIVE 무장이 0이라 급하지 않다 | **D는 지금 결정(권고: 래치를 의도로 문서화)**, 나머지는 첫 LIVE 무장 때 |
| AUTOMATIC_SELECTION_NEEDS_A_LIVE_DOOR | Part 2(관찰 티어 자동 설치), A–C | D3. forward cohort가 이미 자격자 전원에게 forward 시계를 준다 [추정] | **폐기 검토**(전제 소멸, 추정) |
| WALK_FORWARD_TEMPORAL_STABILITY | PR-2(판정 활성화) | Thomas가 아니라 사전 등록한 §4 보고서가 한 번도 돌지 않았다. LIVE 문이 forward 전용이 된 뒤로 실익이 작다 | **관문 대기:** 상태 줄에 "§4 보고서 대기" 명시. 판별력이 없으면 폐기 |
| TEMPLATE_RSI_DRAW_MASS | 선택지 0/A/B/C | 재독 기록 없음. B는 D3이 금지한 family 재배분 | **지금 결정(권고대로):** 0 확정, 재독은 D3 종료 때 |
| COST_GATE_RESET_THE_RECORD | §6-1(오염된 폐기 판정 11개), §6-3 | §6-1은 Thomas 답. §6-3은 읽기 전용 측정이라 결정이 필요 없다 | **§6-1 지금 결정**(제안: 그대로 둔다). §6-3은 상태 줄만 갱신 |
| RESEARCH_EPOCH | Q3 경계 주기, Q4 | Q4는 #972 이후 동결 기록에 지문이 찍히므로 사실상 끝 | **Q3 지금 결정(§5 Q5)**. Q4는 상태 줄 갱신 |
| RISK_BREAKER_UNIT_RESTATEMENT | (a) 자본 문턱, (b) k와 상한, (d) 표본 무게 | **순환**(§5 Q6) | **Thomas 결정 필요** |
| SYSTEM_REVIEW_IMPROVEMENT_PLAN | D5(= 위 (a)(b)(d)) | 위와 같다 | RISK_BREAKER와 하나로 추적 |
| RESEARCH_FORWARD_THROUGHPUT_ANALYSIS | P0-2·3, P1-1~4, P2 | P1-1은 구현됨(#1096·#1100). P0·P1-2·3은 측정·표시라 D3이 허용한다. P1-4·P2는 2027-03-22 | **상태 줄 갱신.** 결정이 남은 것은 관문이 있는 P1-4·P2뿐 |
| MULTI_ASSET_EXPANSION | ① KIS 권한 화면 확인, ② §6 D2 비교 기준, ③ IV–RV 측정이 D3 예외인가 | ①은 결정이 아니라 Thomas 로그인 확인. ②는 P4 관문 | **관문 대기:** 상태 줄에 "D2는 P4 때", "③ 미결" 명시 |

**한 번에 묶어 결정할 수 있는 것:** CONVERSATIONAL D4·APPROVAL V1–V4, CONTROL K1/K3/K4/K5, EVALUATION §8 D, RSI 0,
COST §6-1, RESEARCH_EPOCH Q3. **상태 줄만 고치면 되는 것:** RESEARCH_EPOCH Q4, THROUGHPUT P1-1, CONTROL K3,
COST §6-3. 상태 줄 갱신은 이 문서가 받아들여진 뒤 별도 PR로 한다(결정 기록을 이 문서가 대신 쓰지 않는다).

## 4. 상세

### 4.1 OpenRouter — 체인 첫 멤버가 거의 항상 실패한다

- **무엇.** 파이프라인 워커의 체인은 `MVP_HOSTED_PROVIDER=openrouter,google_ai_studio,groq`이다. OpenRouter 모델은
  `MVP_OPENROUTER_MODEL=google/gemma-4-26b-a4b-it:free`, 블로그는 `MVP_BLOG_OPENROUTER_MODEL=qwen/qwen3.8-27b:free`로
  둘 다 무료 변형이다 [확인].
- **얼마나.** 7일(페일오버 기록이 있는 기간 전체) [확인]:

  | 레인 | 실행 | OpenRouter 응답 | OpenRouter 페일오버 |
  |---|---|---|---|
  | content.general | 104 | 3 | unavailable 74, malformed 20, transport 3 |
  | research.general | 56 | 2 | unavailable 54 |

  `unavailable`은 자체 재시도 뒤에도 429/503이라는 뜻이다(`providers.FAILOVER_UNAVAILABLE`). `malformed` 20건은 블로그
  모델(qwen)이 형식에 맞지 않는 답을 낸 것이다.
- **비용.** 답은 거의 다 2번 멤버(Google AI Studio)가 낸다. 그런데 매 호출이 먼저 OpenRouter의 재시도와 예산 몫을 쓴다.
  content의 모델 지연은 p50 12.0초, p95 40.6초다 [확인].
- **원인 후보.** Hermes도 **같은 OpenRouter 키**를 쓴다 [확인: 두 컨테이너의 키 지문이 같음]. Hermes 모델은
  `google/gemini-2.5-pro`(유료)다. 무료 변형의 429는 계정 일일 한도이거나, 무료 공유 풀의 업스트림 혼잡일 수 있다.
  어느 쪽인지는 계정 대시보드에서만 확인된다 [추정].
- **선택지(Q2).** 체인 순서는 잠긴 결정(Thomas 2026-07-24 "openrouter prepended")이라 env 변경이라도 결정이 필요하다.
  - (a) 순서를 `google_ai_studio,openrouter,groq`로 바꾼다. env 한 줄 + 재시작이고, 코드 변경은 없다. **권고.**
  - (b) OpenRouter 모델을 유료 슬러그로 바꾼다. 무료 체인이라는 전제가 바뀌므로 비용 결정이다.
  - (c) 런타임 전용 OpenRouter 키를 따로 둔다. 원인이 Hermes와의 한도 공유일 때만 효과가 있다.
  - (d) 유지. 지연을 감수한다.

### 4.2 `general.specialist`와 D8 기한

- **D8(Thomas 2026-09-26).** 다이제스트(#986)가 생긴 날부터 60일, 곧 2026-11-25까지 사용·품질 증거가 없는 비크립토
  레인은 "레인은 통째로 제거한다" 규칙(`predmarket/` 선례)에 따라 제거를 검토한다.
- **지금 증거.** content.general과 research.general은 증거가 충분하다. 실행 수가 많고, 전달률과 보류 사유가 기록된다.
  `general.specialist`는 7일간 1회다 [확인].
- **그런데 `general.specialist`는 레인이 아니라 코어의 MVP 역할이다.** CLAUDE.md "Locked decisions"가 MVP 용도("이
  사업 아이디어를 분석해줘")와 역할을 잠갔고, CLI 기본 경로가 이 역할로 간다. 제거할 수 있는 단위가 아니다.
- **질문(Q4).** 11-25에 무엇을 판단하는가.
  - (a) `general.specialist`는 D8 대상에서 뺀다. 잠긴 MVP 용도는 그대로 두고, D8은 content·research·knowledge·Hermes
    레인에만 적용한다. **권고.** 코어 역할을 지울 이익이 없다.
  - (b) 실제 사용(블로그·조사)에 맞춰 잠긴 MVP 용도를 다시 정의한다. 거버넌스 문서 변경이다.
  - 어느 쪽이든 블로그 레인의 "품질 증거"는 실행 수가 아니라 성과(노출·클릭)여야 한다. Tistory 판정 10-31 [문서]이
    그 첫 읽기다.

### 4.3 Windows flaky 테스트

09-21 이후 `MVP Runtime Tests` 639회 중 실패 잡을 모두 읽었다 [확인]. ubuntu에서도 함께 실패한 것은 PR 자체 결함이었고
머지 전에 고쳐졌다. **Windows에서만 실패한 것은 3개 테스트, 7회(약 1.1%)다.** 셋 다 벽시계 시간에 기대는 단언이다.

| 테스트 | 실패 | 메시지 | 원인 |
|---|---|---|---|
| `test_mvp_runtime_workflow_load.py::test_a_hundred_submissions_…` | 4 | `p95 accept latency` 2.06–3.43초 (한도 `< 2.0`) | 공유 러너에서 `-n auto`로 도는 성능 단언에 여유가 없다 |
| `test_mvp_runtime_providers.py::test_a_throttle_retry_that_cannot_fit_…` | 2 | `PROVIDER_TRANSPORT == PROVIDER_UNAVAILABLE`, `1 == 2` | 실제 소켓 서버와 예산 계산(8 − 5초 백오프)이 Windows 소켓 지연에 걸린다 |
| `test_mvp_runtime_fire_watchdog.py::test_a_fire_past_its_deadline_…` | 1 | `assert False` | 0.2초 마감과 스택 캡처 타이밍 |

main에서 실패한 2건(#1099·#1116 머지 커밋)이 이 중 둘이다. 제품 결함의 증거는 없다. `strict`라서 flaky 한 번이
PR 하나를 약 5분 늦춘다. 수정은 테스트만 바꾼다(성능 단언의 Windows 여유, 소켓 대신 결정적 시계). 결정이 필요 없다.

## 5. Thomas 결정 항목

- **Q1. age 공개키를 넘기는가?** 결정은 09-29에 이미 났고, 남은 것은 키 전달과 롤아웃이다. 그때까지 매일 `.env`가
  평문으로 Mac에 간다. 권고: 이번 주.
  - **완료 (2026-10-06).** Thomas가 Mac에서 키 쌍을 만들고 공개키를 넘겼다. 서버: `/root/backups/age-recipients.txt`(0600, 공개키만),
    #1024 머지, `backup-governance-state.sh`·`backup-watch.sh` 설치(이전 판은 `.pre-age-20261006`), 수동 core 1회. Mac: 새 pull
    스크립트, 기존 launchd 등록(`com.thomas.govstate-pull`) 확인, 새 키로 복호화·목록 읽기 `OK`.
  - **키 교체.** 첫 키 쌍의 개인키가 설정 확인 중 스크린샷으로 대화에 노출됐다. 그 키로 잠긴 것은 시험 백업 1개(05:07Z)뿐이었고,
    정기 백업이 처음 암호화되기 전에 새 키로 바꿨다. 노출된 공개키와 그 시험 백업은 서버·Mac 양쪽에서 지웠다. 지금 recipient는
    `age1u7hf6hkw…`이고 첫 정기 암호화 백업은 2026-10-06 07:45Z다.
  - **남은 평문:** 서버의 평문 core 7개는 암호화본 7개가 쌓이면 스크립트가 지운다. Mac에 이미 받은 평문 사본은 Thomas 판단이다.
- **Q2. OpenRouter를 체인 맨 앞에서 빼는가?** 권고: (a) `google_ai_studio,openrouter,groq`.
  - **결정 (Thomas 2026-10-05): (a).** 같은 날 적용했다. 호스트 `.env` 한 줄을 바꿨고(백업
    `/root/backups/thomas-agent.env.pre-chain-order-20261005`), 이 변수를 읽는 두 서비스(pipeline-worker, operator)만
    같은 이미지로 재생성했다. 확인: 두 컨테이너가 새 순서를 읽고 healthy다. 실제 분석 요청 1건은 Google이 페일오버
    없이 5.9초에 답했다 [확인]. OpenRouter의 블로그 모델(`MVP_BLOG_OPENROUTER_MODEL`)은 이제 페일오버 때만 쓰인다.
    바꾸기 전에도 104회 중 3회만 답했으므로 초안을 쓰는 모델은 사실상 그대로다. 되돌리려면 `.env`의 그 줄을
    되돌리고 두 서비스를 같은 방식으로 재생성한다.
- **Q3. §3의 처리안을 받는가?** 항목별 권고는 §3 표의 "제안 처리".
  - **결정 (Thomas 2026-10-06): 권고대로.** 각 제안서의 상태 줄과 "결정 (Thomas 2026-10-06, …Q3…)" 절에 반영했다. 결과:
    - 닫힘(지을 것 없음): CONVERSATIONAL(D4 하지 않음), APPROVAL(V1–V4 하지 않음), AUTOMATIC_SELECTION(Part 2 하지 않음),
      TEMPLATE_RSI(선택지 0).
    - 결정됨, 구현 남음: CONTROL(K1 문구), COST(§6-3 측정), THROUGHPUT(P0-2·P0-3·P1-2·P1-3).
    - 일부 결정: EVALUATION(§8 D (i), 남은 것은 첫 LIVE 무장 때), RESEARCH_EPOCH(Q4 정리, Q3 주기는 Q5), MULTI_ASSET(D2 비준은 P4 때).
    - 관문만 바로 적음: WALK_FORWARD(§4 보고서 실행 대기), RISK_BREAKER·SYSTEM_REVIEW D5(Q6로 추적).
    - 처리안을 쓰며 바꾼 것 하나: APPROVAL V1은 §3 표가 적지 않았다. 전제 소멸(대화 창이 Hermes, 승인 푸시가 이미 명령을
      보여 줌)에 따라 V2·V3와 함께 짓지 않는 것으로 기록했다.
- **Q4. D8 대상에서 `general.specialist`를 빼는가?** 권고: (a) 뺀다.
  - **결정 (Thomas 2026-10-06): (a).** `SYSTEM_REVIEW_IMPROVEMENT_PLAN_V0.1.md`의 같은 날 결정 절에 기록했다. 2026-11-25 검토는
    content·research·knowledge·Hermes 레인을 보고, 블로그 레인은 실행 수가 아니라 노출·클릭으로 판단한다(첫 읽기: 티스토리 10-31).
- **Q5. 에포크 경계를 언제로 정하는가?** RESEARCH_EPOCH Q3(B)은 판정 완화를 경계에서만 하게 했지만 주기는 정하지
  않았다. SELECTION_MULTIPLICITY D4, STRATEGY_EDGE S2, GAP_ANALYSIS의 보류 항목, 판정 상수 완화가 모두 이 경계를
  관문으로 인용한다. 아무도 열 수 없는 관문이다. 권고: 첫 경계 = 1차 cohort 마감(2027-03-22), 이후 cohort 마감마다.
  - **결정 (Thomas 2026-10-06): 권고대로.** `RESEARCH_EPOCH_V0.1.md`의 결정 절에 기록했다. 동결이 28일마다라서 2027-03-22 뒤의
    경계도 약 28일 간격이다(2차 마감 2027-03-30, 3차 2027-04-27). 경계는 완화를 결정할 수 있는 시점이지, 매번 바꾸라는 뜻이 아니다.
- **Q6. D5(손실 브레이커 단위)의 순환을 어떻게 끊는가?** D5 결정(09-26)은 "슬리피지 실측 뒤에 값을 정하고, 그때까지
  LIVE_AUTONOMOUS로 올리지 않는다"이다. 그런데 표본을 늘리는 프로브는 `LIVE_AUTONOMOUS`가 필요하고 [확인], PAPER에서는
  fill이 쌓이지 않으며, 카나리 rung은 없다. 표본은 stop n=12, 진입 n=3에 멈춰 있다 [문서: 09-28 §F8 재실행].
  기다려서는 풀리지 않는다. 선택지:
  - (a) 지금 표본(n=12)으로 잠정값을 정하고, LIVE_AUTONOMOUS 뒤 표본이 차면 다시 정한다.
  - (b) 단계와 별개로 프로브만 여는 예외를 둔다. 카나리 rung을 두 번 거절한 결정(09-15·09-29)과 충돌하므로 권고하지 않는다.
  - (c) 크립토 라이브를 첫 cohort 판정(2027-03-22)까지 올리지 않기로 하고, D5를 그때 묻는다. LIVE 무장 후보가 없는
    지금은 사실상 이것과 같다.
  권고: (c). LIVE 문은 FORWARD_CONFIRMED만 받고, 그런 멤버는 0명이다. 막혀 있어도 지금 잃는 것이 없다. 다만 "실측 뒤"가
  아니라 "첫 판정 때"로 관문을 다시 적어 순환을 기록에서 없앤다.
  - **결정 (Thomas 2026-10-06): (c).** `RISK_BREAKER_UNIT_RESTATEMENT_V0.1.md`와 `SYSTEM_REVIEW_IMPROVEMENT_PLAN_V0.1.md`에 기록했다.
    PAPER에서는 표본이 늘지 않으므로, 2027-03-22에 실제로 고를 것은 "지금 표본으로 잠정값" 또는 "계속 올리지 않음"이다.
