# 제안: "Phase 7.14 정렬·안전 리팩터" 계획 — 저장소 대조 감사 (v0.1)

**상태:** DRAFT 2026-10-05 — 계획의 PR-1·2·3은 Thomas 결정 세 건(09-29 사다리, 08-10 env 게이트, 자격증명 평면)을 뒤집어야 지을 수 있고, PR-4·5는 D3에 걸린다. §5의 Q1–Q4 답을 받기 전에는 코드를 바꾸지 않는다. 코드·스키마·정책 변경 없음.

**대조 기준:** origin/main `d6c2f10f`(#1121), 2026-10-05. 호스트 단계는 읽기 전용으로 확인했다
(`register_execution_stage --show`): **PAPER**, 정책 1.6.1, 원장 1행.
**입력:** Thomas가 붙여 준 "Crypto_AI_System — Phase 7.14 Roadmap Alignment & Safety Refactor"(이하 "계획").
절 번호 §1–§21은 계획의 번호다.
**선행 문서:** 같은 계열의 외부 개선안이 이미 두 번 들어왔다.
`docs/history/2026-09-29-no-canary-rung-reaffirmed.md`(09-28 안),
`CRYPTO_SYSTEM_IMPROVEMENT_GAP_ANALYSIS_V0.1.md`(09-30 안, 16개 절 분류). 이 문서는 그 둘이 다룬 것을 다시 풀지
않고, 이번 계획에서 새로 생긴 요구만 분류한다.
**한계:** 라이브 돈 경로·키·거래소는 코드를 읽기만 했다. 표기는 09-30 분석을 따른다: **[확인]** 코드·명령으로
직접 확인, **[문서]** 저장소 문서의 기록, **[추정]** 추론.

---

## 0. 한 줄

**계획이 말하는 drift는 코드와 정책 사이가 아니라, 계획의 전제와 저장소의 결정 기록 사이에 있다.**

1. **"Phase 7 / review-only가 승인된 상태"라는 전제.** 저장소에는 Phase 번호 체계가 없다 [확인: `7.14`, `PHASE_7`
   grep 0건]. "지금 무엇이 승인됐는가"의 소유자는 실행 단계 원장이고, 호스트는 **PAPER**다 [확인]. 계획이 원하는
   "테스트넷·라이브 주문 불가"는 지금 이미 참이다 — 상수 때문이 아니라 단계 때문이다(§A).
2. **`LIVE_CANARY`.** 2026-09-15에 결정되고 2026-09-29에 재확인된 사항의 반대다: "카나리 rung 없음, 마이그레이션
   없음" [문서: `EXECUTION_STAGE_V0.1.md` 머리말]. 계획은 "승인된 로드맵과 더 이상 맞지 않는다"고 쓰지만, 09-29
   이후 그 결정을 바꾼 기록은 저장소에 없다. 바뀌었다면 Thomas만 확인할 수 있다(Q1).
3. **"환경 opt-in 위의 권위 있는 게이트".** 이미 있다: 소비된 Thomas 승인이 증인인 단계 원장 + 앵커(§A). 계획이
   제안하는 `SIGNED_TESTNET_ORDER_SUBMISSION_ALLOWED = false` 같은 코드 상수는 같은 개념의 두 번째 권위가 된다
   (가드레일 "한 개념 = 한 권위"). 그리고 그것을 풀려면 코드 배포가 필요해지므로 08-10 "환경이 게이트다" 결정의
   변경이다(Q2).

---

## A. 현재 구조 — 코드에 실제로 있는 것

**새 라이브 진입 하나가 나가려면 아래가 모두 참이어야 한다.** 계획 §4의 여섯 층과 대응시켰다.

| 계획 §4의 층 | 저장소의 소유자 | 현재 호스트 값 |
|---|---|---|
| Environment Opt-In | `safety_gate.select_env_gated` (`live_order.py:700–726`), 확인 문구 env | `MVP_LIVE_TRADING=real`, 문구 일치 [확인] |
| Execution Stage | `execution_stage.py` 원장·앵커·증인. `_REQUIRED_STAGE`: probe·autonomous·live_arm → `LIVE_AUTONOMOUS`, signed_testnet → `SIGNED_TESTNET` (:141–146) | **PAPER** → 진입 BLOCK [확인] |
| Approved Capability State | 위 단계 기록 자체. 단계 상승은 `scripts/register_execution_stage.py`만 쓰고, 검증 채널에서 Thomas가 결정해 **소비된** 승인을 증인으로 요구한다 | 증인 `approval_bc8c07eb…`(PAPER BOOTSTRAP) |
| Approval Binding | 전략별 LIVE 무장(`promote_strategy_candidates.py --live-tier LIVE`), 등록 예산, 리스크 한도 기록 | **무장 0 / 15** [확인] |
| Hot-Path Risk Gate | `pre_order_gate.py`: 재검증 → 스냅샷 봉인 → 영속화 → 주문. `CHECK_LINEAGE`가 목적별 필수 id를 요구(:123–131) | 스냅샷 0건(게이트를 지난 주문 없음) [확인] |
| Venue Guard | `venue_contract`, 테스트넷/메인넷 provider id 분리(`testnet_execution.py:87`) | venue contract PASS [확인] |

`live_readiness`의 판정: **LIVE ENTRY POSSIBLE: NO — blocked by execution_stage (STAGE_PAPER), armed_strategy_count
(NONE_ARMED)** [확인]. 이 보드는 `docker exec`로 띄운 새 프로세스의 자체 점검이다. 스케줄러 프로세스의 값은 아니지만,
단계는 매 진입마다 원장을 다시 읽으므로 같은 원장을 본다.

**단계 사다리의 불변식은 테스트로 고정돼 있다** [확인]: 건너뛰기 거부
(`test_a_skip_is_refused_before_anything_is_asked`), LIVE는 테스트넷 증거를 기다림
(`test_live_waits_for_signed_testnet_evidence`), 승인 우회 없음(`test_there_is_no_escape_hatch_around_the_approval`),
위조 강등이 라이브 단계를 만들지 못함(`test_a_forged_demotion_cannot_mint_a_live_stage`), 테스트넷 권한이 라이브를 열지
못함(`test_a_testnet_authorization_opens_nothing_live`), 단계가 낮으면 거래소 전에 거부
(`test_the_door_refuses_before_the_venue_when_the_stage_is_too_low`), env 게이트 호출 지점의 정확한 열거
(`test_the_env_only_gate_has_exactly_the_capabilities_thomas_named`).

**피드백은 행동하지 않는다** [확인]: `crypto/feedback.py` 머리말 "this reports, it never acts", 측정하지 않는
텔레메트리(슬리피지·지연·거부율·API 오류율·조정 불일치)는 0이 아니라 **필드에서 뺀다**. 계획 §9의 "missing을 0으로
바꾸지 말 것"과 같다.

**에이전트 계약은 권한을 줄 수 없다** [확인]: `crypto/strategy.py:347, 431`이 `can_submit_orders`·
`can_modify_runtime`을 false로 강제한다.

---

## B. 계획의 요구 vs 현재 구현

| § | 요구 | 현재 | 분류 | 막는 것 |
|---|---|---|---|---|
| 1, 2-A | 사다리를 `…→LIVE_CANARY→LIVE_SCALED`로 | `…→LIVE_AUTONOMOUS→LIVE_SCALED`, 카나리 rung 없음 | **DECLINED (기존 결정)** | 09-15·09-29 결정. Q1 |
| 3 | 테스트넷 서명 주문을 코드 수준에서 불가능하게 | 구현됨, `SIGNED_TESTNET` 단계에서만 열림. PR1d-1이 Thomas 결정(2026-09-16)으로 만든 **LIVE 상승의 증거 경로**다 | IMPLEMENTED_BUT_STAGE_BLOCKED | 상수를 두면 증거 경로가 사라지고 사다리가 순환한다(`execution_stage.py:135–139`). Q2 |
| 4 | env 위에 권위 있는 fail-closed 게이트 | 단계 원장(§A) | **ALREADY_IMPLEMENTED** | — |
| 5 | Phase 7 동안 키 값을 읽지 말 것 | 어댑터가 서명 시점에 env에서 읽는다: 라이브 주문 `live_execution.py:510`, 테스트넷 `testnet_execution.py:197`, 계정 `account.py:343`. 감사·로그에는 메타데이터만 [문서: 가드레일] | 의도된 설계 | 읽기를 빼면 계정 조회·close 경로·테스트넷이 꺼진다. 리팩터가 아니라 기능 제거다. Q3 |
| 6 | ResearchSignal v2 계층 | 없음 [확인: grep 0건] | DEFER | D3(첫 cohort 판정까지 `crypto/`에 새 연구 장치 금지) + 09-30 분석 §7 보류. Q4 |
| 7 | 13단계 ID 체인 검증기 | `pre_order_gate.CHECK_LINEAGE`가 목적별 필수 id를 요구하고 봉인한다 | PARTIALLY_IMPLEMENTED | 계획의 id 중 `data_snapshot_id`·`feature_snapshot_id`·`research_signal_id`·`approval_intake_id`·`feedback_cycle_id`는 **어느 기록도 만들지 않는다**. 검증기를 먼저 지으면 존재하지 않는 구조를 지어내게 된다. §6과 함께 DEFER |
| 8 | PreOrderRiskGate 보존 | 그대로 | ALREADY_IMPLEMENTED | — |
| 9 | 피드백은 행동하지 않음, missing ≠ 0 | 그대로(§A) | ALREADY_IMPLEMENTED | 지표 추가는 09-30 분석 §9(gap KPI) DEFER |
| 10 | 에이전트 권한 경계 | 그대로(§A) | ALREADY_IMPLEMENTED | — |
| 11 | 핫스팟 분해 | §G가 2026-08-09에 비용을 따져 재구성을 거절했다. 예외는 crypto 분할 작업(`CRYPTO_REFACTOR_AND_MODULARIZATION_PLAN_V0.1.md`, 최근 #1086·#1087·#1090) | DECLINED (기존 결정) / PR7은 진행 중 | `docs/REMAINING_WORK.md` §G |
| 12 | 집중 회귀 lane | `mvp-runtime-tests.yml`은 단일 `pytest tests/ -n auto`. 필수 체크 5개는 Q14 결정 | 짓기 가능(아래 D) | 필수 체크 구성 변경은 Q14 재결정이다. 비필수 lane 추가는 결정 불필요 |
| 13 | 안전 회귀 테스트 20개 | 단계·승인·테스트넷 관련은 대부분 있다(§A 목록) | PARTIALLY_IMPLEMENTED | 없는 것: §D의 P2 목록 |
| 15 | Phase 7.15–15 로드맵 | 저장소의 경로는 단계 사다리 + 승인 + 테스트넷 1사이클 + D5(브레이커 값은 슬리피지 실측 뒤) | 해당 없음 | 계획의 Phase 번호는 저장소의 어떤 기록과도 대응하지 않는다 |

---

## C. 능력 지도

| 능력 | 분류 | 근거 |
|---|---|---|
| 라이브 신규 진입(자율 leg) | IMPLEMENTED, **단계로 차단** | 단계 PAPER, 무장 0. 자율 라우팅은 WIRED [확인] |
| 슬리피지 프로브 진입 | IMPLEMENTED, **단계로 차단** | probe → `LIVE_AUTONOMOUS` 필요 |
| 테스트넷 서명 주문 | IMPLEMENTED, **단계로 차단** | `SIGNED_TESTNET` 필요, 현재 PAPER. 증거 0건 [확인] |
| 청산·보호 주문(reduceOnly, bracket 재확인, 시간 청산, 조정) | **CURRENTLY_CAPABLE (의도)** | "closing is never gated by the stage" — 단계와 무관하게 열려 있어야 노출을 줄일 수 있다 [문서] |
| 주문 취소 | CURRENTLY_CAPABLE (close 경로 일부) | 위와 같음 |
| 시크릿 읽기 | **CURRENTLY_ENABLED (의도)** | 계정 조회는 지금도 서명 호출을 한다(`account_visibility` PASS) |
| 단계 상승 | REVIEW_ONLY | 소비된 Thomas 승인 없이는 불가. 리포트·테스트·CI·paper 성과는 증인이 될 수 없다 |
| 전략 LIVE 무장 | REVIEW_ONLY | 운영자 승격 문, `LIVE_AUTONOMOUS` 필요 |
| 피드백 → 런타임 변경 | 없음 | §A |

계획 §1의 "비활성이어야 할 능력" 목록과 대조하면, 단계 PAPER에서 **새 노출을 만드는 경로는 모두 닫혀 있다.**
열려 있는 것은 청산·보호·조정·계정 조회뿐이고, 이것은 의도된 것이다. 계획이 이것까지 끄라고 읽힌다면, 그건 열린
포지션을 관리할 수 없게 만드는 변경이다(지금 열린 라이브 포지션 유무는 이 감사에서 확인하지 않았다).

---

## D. 리팩터 지도

| 등급 | 항목 | 판단 |
|---|---|---|
| P0 (안전) | 계획의 PR-1 동결, PR-2 카나리, PR-3 시크릿 제거 | **REQUIRES_DECISION.** 셋 모두 기록된 Thomas 결정을 뒤집고 라이브 돈 경로를 건드린다. 가드레일상 Claude가 결정 없이 지을 수 없다 |
| P1 (구조) | PR-4 ResearchSignal v2, PR-5 ID 체인 | **DEFER.** D3. PR-5는 PR-4가 만드는 id에 의존한다 |
| P1 (구조) | PR-7+ 핫스팟 분해 | §G 거절 유지. PR7 분할만 계속 |
| P2 (테스트) | 계획 §13 중 저장소에 아직 직접 고정되지 않은 것: ① paper 실행이 주문 엔드포인트를 부르지 않음, ② 보고서·CI·paper 성과가 단계 증인이 될 수 없음(지금은 승인 소비 규칙이 간접적으로 보장), ③ 단계 테스트만 따로 돌리는 비필수 lane | **결정 없이 지을 수 있다.** 기존 동작을 고정할 뿐 권한을 만들거나 줄이지 않는다. 단 D3 범위인지(테스트는 연구 장치가 아니라고 본다 [추정])는 Q4에서 함께 확인한다 |

---

## 5. Thomas 결정 항목

- **Q1. 09-29 "카나리 rung 없음"을 뒤집는가?** 권고: 유지. 뒤집는다면 별도 제안 문서(스키마 v0.2, 원장 마이그레이션,
  REBIND 설계)가 먼저다. 계획 §2도 "단순 rename 금지, 자동 마이그레이션 금지"라고 한다.
- **Q2. 단계 원장 위에 코드 상수로 된 두 번째 권위를 두는가?** 권고: 두지 않는다. 지금 PAPER가 계획이 원하는 상태를
  이미 보장한다. 상수는 08-10 결정을 바꾸고, 테스트넷 증거 경로를 막아 사다리를 순환시킨다.
- **Q3. 어댑터의 키 읽기를 제거하는가?** 권고: 하지 않는다. 계정 조회·청산 경로가 꺼진다. 메타데이터만 감사에 남는
  현재 규칙이 계획 §5의 의도(값을 저장·로그·감사하지 않음)를 이미 만족한다.
- **Q4. D3 중 ResearchSignal v2 / ID 체인을 예외로 하는가?** 권고: 예외로 하지 않는다(첫 cohort 판정 뒤 재검토).
  §D의 P2 테스트는 D3 밖으로 보고 진행해도 되는지 함께 답을 받는다.

Q1–Q4 답이 오면 그에 맞춰 `CHANGE_PLAN`을 쓴다. 지금 쓰면 결정을 앞질러 계획하게 된다.
