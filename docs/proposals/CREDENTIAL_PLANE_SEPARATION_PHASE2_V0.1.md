# 제안: 평면 분리 Phase ② — 스케줄러의 모델 소비자

상태: 제안 (Thomas 검토 전). 작성 2026-08-10, 기준 `main` = `fe1f11a`.
선행: `CREDENTIAL_PLANE_SEPARATION_V0.1.md` §7 (D3 "나머지를 옮긴다" 방향 승인, 2026-08-10).
그 문서가 "상세는 별도 상신에서"로 미룬 세 질문(위임 작업의 귀속, pass budget과의 상호작용,
`content_ideation`의 실행 위치)에 답한다.

---

## 0. 한 줄 — 방향은 맞고, 헤드라인은 절반만 가능하다

D3의 문장은 "스케줄러가 모델·검색 키를 내려놓는다"였다. 코드와 이 호스트를 읽은 결과
**그 절반은 오늘 공짜에 가깝고, 나머지 절반은 값이 비싸다.** 죽어 있는 소비자 하나가
env 6개(**Hermes와 공유하는 OpenRouter 키 포함**)를 붙잡고 있고, 살아 있는 소비자 둘은
둘 다 crypto LLM kind다. 그래서 이 상신은 D3를 한 번에 이행하지 않고 **증분 하나를 지금,
하나를 조건부로, 하나를 명시적 보류로** 나눈다.

---

## 1. 측정 — 스케줄러의 모델 소비자는 셋이고, 살아 있는 것은 둘이다

`scheduler.py`가 모델을 호출하는 자리는 정확히 세 곳이다.

| 소비자 | 호출 | 무엇을 읽는가 | 이 호스트의 스케줄 |
|---|---|---|---|
| `analysis_task` (KIND_TASK) | `run_task(request, provider, search_tool, …)` (1352) | 텍스트 요청 | **0개** |
| `crypto_propose` | `select_validator_provider()` → `propose_strategy_families(snapshot, …)` (1206) | **라이브 시장 프레임** (5개 leg 부착) | 1개 (enabled) |
| `crypto_data_review` | `select_validator_provider()` → `review_data_gaps(inventory, …)` (1249) | 원장에서 만든 인벤토리 | 1개 (enabled) |

등록된 스케줄 46개(enabled 25개) 중 `analysis_task`는 **하나도 없다**. 즉 파이프라인
전체를 도는 유일한 kind는 이 호스트에서 한 번도 발화하지 않는다.

env 매핑은 이렇게 갈린다:

| env | 소비자 | 오늘 |
|---|---|---|
| `MVP_HOSTED_PROVIDER`, `OPENROUTER_API_KEY`, `GOOGLE_AI_STUDIO_API_KEY`, `MVP_OPENROUTER_MODEL` | `select_provider()` → **KIND_TASK 전용** | **죽어 있다** |
| `MVP_SEARCH_TOOL`, `TAVILY_API_KEY` | **KIND_TASK 전용** (1352가 유일한 사용처) | **죽어 있다** |
| `MVP_VALIDATOR_PROVIDER=groq`, `GROQ_API_KEY` | proposer + data_review | **살아 있다** |

`select_validator_provider()`가 `MVP_VALIDATOR_PROVIDER`만 읽고 hosted 체인으로
폴백하지 않는다는 점이 이 분리를 가능하게 한다 — 확인함(`providers.py`). 그래서 **hosted
체인 4개와 검색 2개를 떼도 살아 있는 두 소비자는 영향을 받지 않는다.**

---

## 2. 발견 1 — 죽은 env 6개가 돈 컨테이너에 있고, 그중 하나가 OpenRouter 키다

가장 값이 큰 한 줄은 `OPENROUTER_API_KEY`다. 이 호스트의 Hermes 컨테이너와 **같은 키**를
공유하고(그래서 어시스턴트의 지출과 런타임의 지출이 한 quota를 쓴다), 지금 **Binance 주문
비밀과 같은 주소공간에** 있으며, **이 호스트의 어떤 스케줄도 쓰지 않는다.**

이건 Phase ①이 dispatch 문에서 없앤 것과 같은 모양의 노출이다. 차이는 이쪽이 더 싸다는
것뿐이다 — 옮길 실행이 없다. 옮길 게 없는 이유는 소비자가 죽어 있어서다.

---

## 3. 발견 2 — 그렇다고 지우기만 하면 안 된다 (이 저장소가 두 번 겪은 실패)

"소비자가 없으니 env를 지운다"는 **`schedules.jsonl`이 per-machine**이라는 사실 앞에서
위험해진다. 오늘 0개인 것은 설계가 아니라 이 머신의 상태다. 내일 누가
`scheduler_cli add --kind analysis_task`를 하면, env가 없는 스케줄러는 **조용히 mock
파이프라인으로 돈다** — 에러 없이, "설정된 것처럼 읽히면서".

그것이 정확히 `#512`(candle archive)와 `#650`(Naver lane)이 닫은 실패 모양이고,
`test_deployment_env_passthrough.py`가 존재하는 이유다. 그래서 이 상신은 순서를 뒤집는다:

> **먼저 위임을 붙이고, 그 다음에 env를 뗀다.** 그러면 6개 제거는 *능력 상실*이 아니라
> *이전*이 된다 — 내일 등록될 `analysis_task`는 오늘보다 잘 돈다(키가 있는 곳에서 돈다).

부수 효과 하나가 이것만으로 값을 한다: 블로그 레인 Phase 3의 `content_ideation` 스케줄
(미착공)이 **구조적으로 워커에서 실행된다.** 스케줄러에 모델 소비자를 다시 만들지 않는
것 — Phase ①이 산 것을 Phase 3이 도로 파는 일을 막는다. (§7의 열린 질문 3에 대한 답이다.)

---

## 4. 발견 3 — 워커의 귀속이 하드코딩이라 두 번째 호출자를 받을 수 없다

`pipeline_worker.apply_work`는 `requester_id=ASSISTANT_ACTOR`,
`requester_type="agent"`, `source_ref=f"{ASSISTANT_ACTOR}:dispatch: …"`를 **상수로**
쓴다. Phase ①에서는 호출자가 하나였으니 옳았다.

스케줄러가 그대로 위임하면 **스케줄 작업이 원장에 어시스턴트 작업으로 기록된다.**
오늘 KIND_TASK는 `requester_id="mvp.scheduler"`, `requester_type="scheduler"`,
`channel="scheduler"`, `source_ref=f"scheduler:{schedule_id}"`로 남는다. 이걸 잃으면
"어시스턴트가 시작한 일과 운영자/스케줄러의 일을 원장이 구분한다"는 성질이 깨진다 —
`dispatch_bridge`의 docstring이 명시적으로 지키는 성질이다.

**설계: 귀속은 호출자가 선언하고, 프레임은 고를 수 없다.**

- 워커는 닫힌 집합의 귀속 프로필을 갖는다 (`assistant_bridge` | `mvp.scheduler`).
- **문은 자기 것을 하드코딩한다** — `dispatch_bridge._ALLOWED_KEYS`가 닫혀 있어서
  어시스턴트가 프레임에 귀속 필드를 실을 수 없다(`ARGUMENT_NOT_ACCEPTED`). 즉 어시스턴트는
  `mvp.scheduler`를 **표현할 수 없다.**
- 스케줄러도 자기 것을 하드코딩한다.

"보안 검사를 레코드가 고르게 하지 말라"는 이 저장소의 규칙(`switch_bridge`가 도메인을
요청이 아니라 승인 스냅샷에서 읽는 이유)과의 관계를 분명히 해 둔다:
이건 보안 검사를 레코드 데이터로 분기하는 것이 **아니다.** 귀속은 권한을 결정하지 않고
(양쪽 다 P3 상한·같은 kind 집합), 두 호출자는 OS 수준에서 구분되지 않는다(둘 다 uid
10001이라 `SO_PEERCRED`로 나눌 수 없다 — 이건 사실로 적어 둔다). 성립하는 이유는
**어시스턴트가 어느 쪽 선언에도 도달할 수 없다**는 것 하나다. 소켓 하나를 더 파서
분리하는 방법은 uid가 같으므로 아무것도 사지 못한다.

---

## 5. 발견 4 — 전송은 이미 있다 (검증함)

스케줄러 컨테이너에서 워커 소켓이 보이고, 왕복이 되고, 금지 kind에 typed 거부가 온다:

```
socket visible from scheduler: True
reply: KIND_NOT_PERMITTED          # 2026-08-10, 라이브 컨테이너에서 실행
```

같은 상태 볼륨을 마운트하고 같은 uid로 돌기 때문이다. **새 마운트도, 새 env도, 소켓
설정 변경도 필요 없다.** Phase ②의 배관은 Phase ①이 이미 깔아 두었다.

---

## 6. 나머지 두 소비자 — 하나는 가능하고, 하나는 지금 하면 안 된다

### `crypto_data_review` — 위임 가능

인벤토리는 원장 행 + outcomes + pool contexts로 만들어지고, **워커는 같은 상태 볼륨을
rw로 마운트한다.** 그래서 프레임을 실어 보낼 필요 없이 워커가 자기 쪽에서 인벤토리를
다시 만들 수 있다(구현 시 읽기 경로가 정말 상태 볼륨만 타는지 확인 필요 — 호출부만 읽고
전체 경로는 추적하지 않았다).

단, **위임 단위는 "fire 전체"가 아니라 "리뷰 레코드 생성"이다.** 이 fire는 끝에
`notify_operator`로 운영자에게 알리는데, 그건 Telegram env를 필요로 한다. 워커에 그걸
주는 것은 또 한 번의 확장이므로: 워커는 레코드를 돌려주고, **원장 append와 알림은
스케줄러가 유지한다.**

### `crypto_propose` — 보류를 권고한다

프레임이 다르다. proposer는 `collect_market_data` + `attach_mining_legs`로 만든
**라이브 시장 스냅샷**을 필요로 한다. 위임하려면 둘 중 하나인데 둘 다 나쁘다:

1. **워커가 직접 수집** → 워커에 `MVP_MARKET_DATA`·`COINALYZE_API_KEY` 복제. 돈 키는
   아니지만, 같은 규칙을 평가하는 **두 번째 수집 경로**가 생긴다 — C9의 "backtest와
   live가 한 feature source"(source rule)가 지키려는 바로 그 지점이다.
2. **스냅샷을 소켓으로 전송** → 120+ 바 × 5 leg의 JSON. 전송은 가능하지만(문의 응답
   상한이 이미 1MiB) 새 스키마 표면이 생기고, 프레임이 직렬화를 왕복하며 같은 것이라는
   보장을 따로 만들어야 한다.

그래서 **proposer는 스케줄러에 남긴다.** 결과적으로 `MVP_VALIDATOR_PROVIDER=groq` +
`GROQ_API_KEY`도 남는다 — **D3의 헤드라인이 오늘 완전히 이행되지 않는 이유가 이것이고,
숨기지 않고 적어 둔다.**

**언제 다시 볼 것인가 (조건):** proposer가 자기 프레임을 원장에서 재구성할 수 있게 되거나
(cycle 레코드가 이미 스냅샷을 담게 되는 변경), 시장데이터 수집이 별도 서비스로 분리되면.
둘 중 어느 것도 이 상신이 만들지 않는다.

---

## 7. 증분

### PR-C — `analysis_task`를 워커로 위임하고, 죽은 env 6개를 뗀다 (권고: 지금)

- `scheduler.py`의 KIND_TASK 분기가 `run_task` 대신 워커로 forward. 기존 `executor`
  주입점(`run_due(executor=…)`)이 이미 있으므로 테스트 이음매는 그대로.
- 워커: 귀속 프로필 2개(§4). `source_ref`는 `scheduler:<schedule_id>`를 보존.
- compose: 스케줄러에서 `MVP_HOSTED_PROVIDER`, `OPENROUTER_API_KEY`,
  `GOOGLE_AI_STUDIO_API_KEY`, `MVP_OPENROUTER_MODEL`, `MVP_SEARCH_TOOL`,
  `TAVILY_API_KEY` 제거 + 왜 없는지 묘비 주석.
- 테스트: `test_deployment_env_passthrough`에 "스케줄러는 hosted 체인/검색을 받지
  않는다"를 변수별로 고정 + 워커의 귀속이 호출자별로 갈리는 것을 고정 + 문이
  `mvp.scheduler`를 **표현할 수 없음**을 고정(어시스턴트 쪽 회귀 방지).
- 워커 불달 시: 오늘의 fire 실패 경로 그대로(`failed` 스케줄러 이벤트 + `last_status`).
  폴백으로 로컬 mock을 돌리지 **않는다** — 조용한 성공이 정확히 §3의 실패 모양이다.

### PR-D — `crypto_data_review` 위임 (선택, 별건)

`MVP_VALIDATOR_PROVIDER`는 proposer 때문에 어차피 남으므로 **키를 하나도 없애지
못한다.** 사는 것은 "돈 키를 든 프로세스에서 모델 응답을 파싱하는 횟수"가 둘에서 하나로
주는 것뿐이다. 값이 작으므로 PR-C와 묶지 않고, Thomas가 원할 때 한다.

### 보류 — `crypto_propose` (§6의 조건이 성립할 때까지)

---

## 8. Thomas 결정

| # | 결정 | 권고 |
|---|---|---|
| **D4** | PR-C: `analysis_task`를 워커로 위임하고 죽은 env 6개(OpenRouter 키 포함)를 스케줄러에서 뗀다 | **채택** — 라이브 소비자 0개, 배관 존재, 블로그 Phase 3의 회귀를 구조적으로 막는다 |
| **D5** | PR-D(`data_review` 위임)를 지금 할지 | **하지 않는다** — 키를 못 없애고 값이 작다. 나중에 언제든 가능 |
| **D6** | `crypto_propose` 보류와 §6의 재검토 조건을 받아들인다 | **채택** — 대안 둘 다 source rule 또는 새 스키마 표면을 산다 |

**정책 diff 0.** 새 verb·새 권한·kind 집합 변경 없음. `run_task`의 인자와 원장 귀속은
보존된다. `docs/ACTIVE_ARCHITECTURE.md`에 스케줄러→워커 경로 한 줄이 추가된다.

---

## 9. 이 상신이 고치지 않는 것

- **스케줄러는 여전히 모델 키를 하나 든다** (`groq`). proposer가 남기 때문이고, D3의
  헤드라인은 그만큼 미이행이다.
- **상태 볼륨은 여전히 전 서비스 rw 공유다.** Phase ①과 같은 한계 그대로 — env 반경만
  자르지 상태 반경은 못 자른다.
- **pass budget은 바뀌지 않는다** (§7의 열린 질문 2에 대한 답): 위임은 동기 forward라
  fire가 tick을 붙잡는 시간은 오늘과 같고, `KIND_TASK`는 이미 `MAINTENANCE_KINDS`라
  예산에 묶여 있다. 지연/at-most-once 계약에 변화 없음.
- **두 호출자는 OS 수준에서 구분되지 않는다** (§4). 소켓을 나눠도 uid가 같아 사는 것이
  없고, 성립 근거는 "어시스턴트가 어느 선언에도 도달 못 한다" 하나다.

---

*근거: `runtime/mvp_runtime/scheduler.py` (세 소비자 호출부 1206·1249·1352,
`run_due(executor=…)`, `MAINTENANCE_KINDS`), `runtime/mvp_runtime/providers.py`
(`select_validator_provider`가 `MVP_VALIDATOR_PROVIDER`만 읽고 폴백하지 않음),
`runtime/mvp_runtime/pipeline_worker.py` (하드코딩된 귀속), `runtime/mvp_runtime/
dispatch_bridge.py` (`_ALLOWED_KEYS`), 라이브 호스트 2026-08-10 (스케줄 46개 중
analysis_task 0개; 스케줄러→워커 소켓 왕복 성공; 스케줄러 env 실측),
`CREDENTIAL_PLANE_SEPARATION_V0.1.md` §7, `docs/proposals/NAVER_BLOG_CONTENT_LANE_V0.1.md`
§5 (Phase 3).*
