# 제안: 가설 트라이얼(`hypothesis_trial`) — §I 결정 요청의 갱신 (DRAFT v0.1)

**상태:** IMPLEMENTED 2026-09-24 — C안 결정, PR1–PR4 구현(#977·#978·#979·#981). 선언형 family는 결정대로 만들지
않았다.
**참고:** 이 문서의 나머지는 결정 당시의 근거로 남긴다.
**성격:** 새 설계가 아니다. 외부 "Crypto Live Trading Follow-up Fix Plan" §10~13(Hypothesis Trial
Layer)은 `docs/REMAINING_WORK.md` §I2(2026-08-05, "trial rotation slots", `trial_family`)와 같은
안이고, §I는 08-05부터 결정을 기다리고 있다. 이 문서는 그 결정에 필요한 것 중 **08-05 이후에
바뀐 것**과 **실측한 proposer 상태**를 더하고, 선택지와 결정 항목을 다시 적는다. §I3의 가드레일
판단(선언형 family = "family가 무엇인가"의 두 번째 권위)은 여기서 풀지 않는다. 그대로 묻는다.
**측정 시각:** 2026-09-24, 운영 `.runtime_governance_state/`(원장 archive 포함), main `ec37089b`.
**근거:** `crypto/proposer.py`(`evaluate_proposal`, `MAX_UNREVIEWED_BACKLOG=12`, 30일 창),
`scheduler.py`의 `KIND_PROPOSER` 분기, `factory.build_replay_frame`,
`pool_state.DERIVATION_TYPES`, `pool_admission.PROMOTABLE_DERIVATION_TYPES`(#545),
`forward_cohort.py`의 시더 필터, `forward_cohort_null.py`(#960·#961),
`docs/proposals/FORWARD_COHORT_OFF_POOL_V0.1.md`.

---

## 0. 한 줄

트라이얼 층이 먹을 입력은 지금 proposer뿐인데, proposer는 **39개 family를 채택하고 하나도 설치되지
않은 채 14일째 멈춰 있고**, 채택 근거는 **전부 FRAGILE·33개가 거래 0건인, 1h 봉 120개(5일) 창의 백테스트**다(19개는 자기
타임프레임도 아닌 봉으로 채점됐다). 트라이얼 층을 짓기 전에 그 입력부터 고쳐야 하며(§5, 결정
불필요), 층 자체는 §I의 선택지에 08-05 이후 생긴 C안(§4)을 더해 Thomas가 고른다.

---

## 1. 측정: proposer (2026-07-26 ~ 09-24)

| 항목 | 값 |
|---|---|
| 예약 발화(매일 06:18Z) | 61회 — 제안 생성 21회, `skipped_backlog_full` **40회** |
| 마지막 생성 | 09-10. **09-11부터 14일 연속 건너뜀**(백로그 13 ≥ 12) |
| 제안 / 채택 / 서로 다른 채택 family | 47 / 40 / **39** |
| `factory.TEMPLATES`에 설치된 것 | **0** (TEMPLATES 44개 중 제안 출신 없음) |
| 채택 family의 robustness 판정 | **39/39 FRAGILE** |
| 백테스트 거래 0건 | **33/39** (최대 8건) |
| spec 타임프레임 ≠ 채점 스냅샷(1h) | **19/39** (4h 8, 15m 7, 1d 4), 이 중 15개가 거래 0건 |
| 채점 창 | 21회 모두 **120봉**(1h ≈ 5일). 자기 타임프레임(1h)으로 채점된 20개 중에서도 **18개가 거래 0건** |

- **채점 스냅샷은 항상 BTCUSDT 1h 하나**다(`schedule.request` 기본값, `collect_market_data`의 기본
  창). `_spec_dict`는 제안의 타임프레임을 spec에 그대로 싣지만, `build_replay_frame`은 스냅샷의
  타임프레임만 읽고 둘을 비교하는 곳이 없다. 그래서 4h 제안 `funding_spike_short`의 `max_holding_bars=48`은
  8일이 아니라 1h 봉 48개(2일)로 재생되고, 그 결과가 "채택"으로 기록된다. **이것은 결함이다**(§5 D-0).
- 거래 0건의 원인 중 하나(런타임이 열을 공급하지 않던 것)는 이미 고쳐졌다(`scheduler.py`
  proposer 분기의 `attach_mining_legs` 주석: 당시 22개 중 18개). 남은 주 원인은 **창 깊이**다. 120봉은 `robustness.MIN_HOLDOUT_TRADES=25`는커녕 거래 한 건도
  담기 어렵고, 타임프레임 불일치는 그 위에 얹힌 두 번째 원인이다. 즉 D-0만 고쳐서는 "채택"이
  여전히 아무것도 뜻하지 않는다. C안이 "팩토리 깊이로 채점"을 포함하는 이유다.
- 백로그는 30일 창이라 **09-29에 08-30분 3개가 빠지면 10으로 내려가 한 번 발화**하고, 채택이
  3개 이상이면 다시 막힌다. 설치가 0인 한 이 주기가 반복된다.

**§I1이 예견한 것이 그대로 측정됐다.** family 설치는 이 시스템에서 유일하게 표본 밖 증거 없이
내리는 결정이고, 그 결정의 근거로 쌓인 것은 "대부분 거래가 없고, 거의 절반은 다른 봉 길이로
잰" 백테스트다. 설치가 0인 것은 게으름이 아니라 판단할 근거가 없어서다.

맥락(같은 날 운영 수치): 전략 퍼널 3,244계보 중 promotable 1, 탈락 대부분이 holdout.
forward cohort 115명 전원 탐색 단계, null 대조 `4h 반박 실제 4 vs null 7`.

## 2. 08-05 이후 바뀐 것

1. **단일 spec의 forward 증거는 이제 쌓인다.** §I1은 "채택된 제안을 후보로 민팅해 증거를 쌓게
   하자"를 *안 되는 안*으로 기록했다. 후보의 증거는 민팅 때 한 번 계산되고 끝나기 때문이다.
   forward cohort(#945~#951, `FORWARD_COHORT_OFF_POOL_V0.1.md`)는 풀 밖 후보를 `promoted_at`부터
   걸어 forward 결과를 계속 쌓는다. **바뀌지 않은 것:** family 수준 증거(파라미터 공간을 여러
   컨텍스트·세대에 걸쳐 민팅해서 생기는 것)는 여전히 rotation에 들어가야만 생긴다.
2. **"아무것도 확정 못 할 것"의 기준선이 생겼다.** §I3은 "거의 확실히 아무것도 확정하지 못한다,
   무작위 진입이 여기서 −0.13R"이라고 적었다. null arm(#960·#961)은 멤버마다 같은 빈도의 동전
   쌍둥이를 같은 방식으로 걸어, 타임프레임별 확정·반박 비율을 나란히 보여 준다. 트라이얼도 0이
   아니라 동전과 비교할 수 있다.
3. **격리 문은 그대로 서 있고, 하나가 더 있다.** `pool_admission.PROMOTABLE_DERIVATION_TYPES`는
   허용 목록이라 네 번째 파생 유형은 빠뜨리는 것만으로 승격에서 막힌다(#545, 오늘도 거부 0).
   그런데 **cohort 시더도 같은 목록으로 거른다**(`forward_cohort.py`의 `derivation_type not in
   PROMOTABLE_DERIVATION_TYPES → continue`). 즉 트라이얼 행은 LIVE뿐 아니라 **forward cohort에서도
   빠진다.** 외부 계획 §12의 `can_run_shadow_forward=true`는 지금 코드에서 거짓이다(§6 Q3).
4. 풀 분할(#935~#940) 뒤로 `DERIVATION_TYPES`와 `_PARENT_COUNT_RULES`는 `pool_state.py`에 있다.
   §I2의 `pool.DERIVATION_TYPES` 표기는 옛 위치다.

## 3. 외부 계획 §12 권한 → 현재 코드

| 계획의 권한 | 지금 | 트라이얼에 필요한 것 |
|---|---|---|
| `can_submit_orders=false`, 실행 권한 없음 | **이미 불변식.** spec 파서가 `can_submit_orders`·`can_modify_runtime`이 true면 거부, 직렬화는 항상 false(`strategy.py`) | 없음 |
| `can_enter_live_pool=false` 외 LIVE 셋 | **이미 성립.** 허용 목록에서 빠지면 승격 문이 `CANDIDATE_DERIVATION_NOT_PROMOTABLE`로 거부, 백로그 `derivation` 축이 보드에 광고하지 않음 | 목록에 **넣지 않는 것**(편집 0) |
| `can_backtest=true` | 저장소가 행을 받기만 하면 됨 | `DERIVATION_TYPES`에 추가, 부모 규칙 `(0, 0)` |
| `can_run_shadow_forward=true` | **거짓**(시더 필터) | Q3의 선택 |
| `can_create_research_evidence=true` | 퍼널·보드는 후보 저장소를 읽음 | 퍼널에 트라이얼 줄(선택) |

계획의 권한 플래그를 **새 필드로 만들 필요는 없다.** 권한은 파생 유형 하나에서 모두 나오고,
플래그를 따로 두면 같은 사실의 권위가 둘이 된다.

## 4. 선택지

- **A. §I2 그대로:** 선언형 family(조건 패턴 데이터) + 해시 승인으로 적재 + rotation 슬롯 상한 4.
  family 수준 증거까지 생긴다. 비용은 §I3: family 정의의 두 번째 권위, 모델이 *무엇을 채굴할지*에
  닿음. 가장 크다.
- **B. §I4:** proposer가 채택 제안을 `StrategyTemplate` 코드+테스트 PR로 렌더링하고 Thomas가
  diff를 검토한다. 새 권위 없음. 결정은 여전히 한 번의 백테스트 위에서 제안마다 내려진다.
- **C. (08-05 이후 가능해진 안) 트라이얼 행 + 트라이얼 forward 트랙:** 채택 제안 하나를
  **자기 타임프레임·팩토리 깊이·5심볼 풀드**로 채점해 `derivation_type: hypothesis_trial` 후보로
  한 번 민팅한다(파라미터 탐색 없음, rotation 밖). 그리고 null arm과 같은 방식의 별도 트랙(자기
  저장소, `walk_track` 재사용)과 그 null 쌍둥이로 forward를 건다. 선언형 family가 없으니 §I3의
  두 번째 권위도 없다. spec은 이미 저장소가 담는 데이터다. 약점: family 수준 증거가 없고, 제안
  하나당 파라미터 한 점만 본다.
- **D. 보류:** 지금 상태. proposer는 창 주기대로 가끔 한 번씩 발화하고 설치 0이 계속된다.

**권고(결정은 Thomas):** 어느 안이든 §5 D-0이 먼저다. 그다음 C가 가드레일 비용 없이 "제안이
동전보다 나은가"에 답하는 가장 작은 안이다. C에서 null보다 나은 트라이얼이 나오면 그때 A(family
수준) 또는 B(코드로 설치)를 고를 근거가 생긴다. C는 §I1의 "결정에 증거가 없다"를 행 수준에서만
풀고 family 수준에서는 풀지 않는다는 점을 알고 고를 것.

## 5. 결정 없이 먼저 할 것

- **D-0. proposer 채점 결함(별도 PR):** spec 타임프레임이 스냅샷과 다르면 채택하지 않는다
  (`reject_reason: "timeframe"`, fail-closed). 또는 제안 프롬프트를 스냅샷 타임프레임으로 묶는다.
  자기 타임프레임·팩토리 깊이로 다시 수집해 채점하는 것은 발화당 요청 수를 늘리므로 C안의
  일부로 둔다. **D-0은 잘못된 채택을 막을 뿐, 채택에 증거를 주지는 않는다**(창 깊이, §1).
  이미 기록된 19건은 원장 원본이라 고치지 않는다. 백로그 계산에서 뺄지는 D-0 PR에서 정한다.

## 6. 결정 항목 (Thomas)

- **Q1. 경로:** A / B / C / D.
- **Q2. (A일 때만) §I3 가드레일:** 선언형 family를 상한 있는 대기 구역의 두 번째 권위로 허용하는가.
- **Q3. 트라이얼 forward:** (a) 별도 트랙(권고: 동결된 cohort 115명과 그 null 대조가 바뀌지 않음)
  (b) cohort 시더의 필터를 넓힘(Option A라 cohort 증거는 LIVE 문에 닿지 않지만, 다음 동결부터
  cohort와 null 대조의 모집단이 섞인다).
- **Q4. 이름:** 외부 계획은 `hypothesis_trial`, §I2와 `pool_admission.py` 주석은 `trial_family`.
  권고 `hypothesis_trial`(C안은 family가 아니라 행 하나다). A안이면 `trial_family`가 맞다.
- **Q5. 졸업:** 새 문턱을 만들지 않는다. `TEMPLATES` 설치(Thomas PR)는 기존 holdout 게이트
  CONFIRMED를 요구하고, forward·null 대조는 참고로만 읽는다. 자동 승격 없음(계획 §13과 같음).
- **Q6. 상한:** 동시 트라이얼 수(권고 4, §I2와 같음). 백로그 상한 12는 설치가 아니라 트라이얼
  편입을 "검토"로 세도록 바꿀지.

## 7. 구현 비용 (선택별 편집)

| 편집 | A | B | C |
|---|---|---|---|
| `pool_state.DERIVATION_TYPES` + `_PARENT_COUNT_RULES` `(0, 0)` | ✓ | | ✓ |
| `PROMOTABLE_DERIVATION_TYPES` | **건드리지 않음** | | **건드리지 않음** |
| 격리 증명 테스트(승격 거부·백로그 축·cohort 시더 제외) | ✓ | | ✓ |
| 조건 패턴 데이터형 + 렌더러 + 해시 승인 적재 | ✓ | | |
| rotation 슬롯 | ✓ | | |
| proposer의 코드 렌더링 + PR 생성 | | ✓ | |
| 자기 tf·팩토리 깊이·풀드 채점 + 민팅 | | | ✓ |
| 트라이얼 forward 트랙 + null 쌍둥이(`walk_track` 재사용) | Q3 | | ✓ |
| 보드·퍼널 트라이얼 줄 | ✓ | | ✓ |

## 8. 범위 밖

외부 계획의 가설 생성기(§14~15), novelty(§16~17), 탐색 예산(§18~19), family 소진(§20~21),
연구 에포크(§30~31)는 각자 제안서가 필요하다. 다만 생성기가 없으면 트라이얼 층의 입력은
지금의 proposer뿐이고, 그 입력의 상태가 §1이다. 에포크는 트라이얼이 늘리는 다중 검정을 묶는
장치라, A를 고르면 함께 봐야 한다.

## 결정 (Thomas 2026-09-24, 권고대로)

- **Q1 경로: C.** 트라이얼 행 + 별도 forward 트랙 + null 쌍둥이. A(선언형 family), B(코드 PR), D(보류)는 채택하지 않는다.
- **Q2:** C에서는 해당 없음. 선언형 family는 만들지 않는다.
- **Q3 트라이얼 forward: (a) 별도 트랙.** 동결된 cohort와 그 null 대조는 바꾸지 않는다.
- **Q4 이름: `hypothesis_trial`.**
- **Q5 졸업:** 새 문턱은 없다. `TEMPLATES` 설치는 Thomas PR이며 기존 holdout CONFIRMED를 요구한다. forward와 null 대조는 참고로만 읽고, 자동 승격은 없다.
- **Q6 상한: 동시 트라이얼 4.** 백로그 상한은 트라이얼 편입을 검토로 세는 방식으로 바꾸는 것을 구현 PR에서 정한다.
- 선행 조건 D-0은 #963으로 머지·배포했다.

## 구현 (Thomas 2026-09-25, 권고대로)

- PR1 #977: 스토어가 `hypothesis_trial`을 받고, 승격 문·backlog·코호트·교배 부모·재채점이 거부하거나 태그를 보존한다.
- PR2: 코호트 factory 발화가 자기 타임프레임의 미검토 채택 제안을 오래된 순으로 최대 `MAX_TRIAL_SCREENS_PER_FIRE`(3)개 심사하고, 처음 통과한 하나를 트라이얼 행으로 민팅한다(5심볼 풀드, 1h 포함, 동시 상한 `MAX_OPEN_TRIALS` 4). 민팅이든 거부든 심사된 제안은 backlog에서 빠진다. 거부 사유: `parse`·`known_family`(설치·은퇴 family)·`validator`·`duplicate_rule_hash`·`unsuppliable_feature`·ablation 거부·`no_trades`. 트라이얼은 rotation 커서와 null_control 측정에서 빠지고, 채점한 풀드 컨텍스트의 attempts에는 계산된다.
- PR3: 트라이얼과 그 동전 쌍둥이를 cohort walker(`forward_cohort.walk_track`)로 민팅 시각부터 걷는다(`crypto/forward_trial.py`). 각자 positions·outcomes 저장소(`mvp_forward_trial`, `mvp_forward_trial_null`)를 가져 cohort·null arm·forward book과 섞이지 않는다. 멤버십은 후보 저장소의 트라이얼 행이고 쌍둥이는 봉인된 행의 순수 함수라 별도 동결 기록이 없다. 쌍둥이 속도는 레그당(`closed_count / (bars_replayed × symbols_replayed)`). 매일 forward_cohort 발화에서 cohort·null arm 다음에 걷고, 실패는 줄에 이름만 남기고 발화를 실패시키지 않는다.
- PR4: 보드 줄 `트라이얼 열림 n/4 · 종료 m · 기록 k · 확정·반박/계보 <tf> 실제 vs null`(+누적), 퍼널 HYPOTHESIS TRIALS 절(트라이얼별 줄·쌍둥이·독립 베팅), `scripts/hypothesis_trial.py list|close`. close는 봉인된 `hypothesis_trial_closes.jsonl`에 남고(종료 시점 forward 기록 포함) 슬롯을 비우며 트라이얼·쌍둥이 walk를 멈춘다. 제안은 다시 대기열에 들어가지 않는다. `--graduate`는 트라이얼 자신의 holdout이 CONFIRMED일 때만(Q5), 설치는 여전히 `factory.TEMPLATES` PR.
- C안 구현 완료(PR1~4).
