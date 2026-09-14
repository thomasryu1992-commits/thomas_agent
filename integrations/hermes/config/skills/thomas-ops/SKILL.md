---
name: thomas-ops
description: "Thomas Agent 런타임 운영 절차 — 브리핑 형식, 이상 판정 기준, 상신 양식, 지표 해석"
version: 1.5.4
author: Thomas
license: MIT
platforms: [linux]
metadata:
  hermes:
    tags: [thomas-agent, trading, operations, briefing, escalation]
---

# Thomas Agent 운영 플레이북

SOUL.md는 **판단 기준**을 담고, 이 스킬은 **절차**를 담는다. 브리핑을 쓰거나, 이상을
판정하거나, 상신을 올릴 때 읽는다.

## 1. 브리핑 절차

호출 순서. 각각 `thomas-read` / `thomas-switch` 도구다.

1. `trading_switch_status` — 런타임이 실행 가능 상태인가
2. `trading_status` — 편중, 마지막 사이클, 게이트 섀도우, **페이퍼** 포지션
3. `trading_readiness` — **실주문이 나갈 수 있는가 / 실제로 무장된 전략이 있는가**
4. `paper_performance` — 순기대값, 승률, 최대낙폭
5. `task_list` — 진행 중 작업

**3번을 빼지 마라.** 1·2·4번 중 어느 것도 "실주문"에 답하지 못한다 — `trading_status`의
포지션 줄은 **페이퍼 북**에서 나오고, `paper_performance`는 이름 그대로 페이퍼다. 3번을
부르지 않고 머니패스 문장(포지션 보유, 라이브 여부, INCIDENT 유무)을 쓰면 그건 근거 없는
문장이다. 실측으로 아침 브리핑 18회 중 3번을 부른 것은 **0회**였고, 그 18회 모두가 포지션을
말했다.

**형식: 달라진 것만 5줄 이내. 변화가 없으면 "이상 없음" 한 줄.**

정상일 때 침묵하는 것이 이 브리핑의 핵심이다. 매일 같은 보고가 오면 Thomas는 읽지 않게 되고,
그러면 진짜 이상이 왔을 때도 읽지 않는다.

### 무음 감시 출력 규칙 — 예외 없음

§2 표의 행 중 **하나라도 실제 도구 응답 줄로 "해당됨"을 인용할 수 있을 때만** 보고한다.
그 외에는 판정표·근거·요약을 **쓰지 말고** `[SILENT]` 네 글자만 출력한다.

**표를 쓰고 그 뒤에 `[SILENT]`를 붙이는 것은 금지다 — 그건 침묵이 아니라 배달이다.**

보고할 때는 첫 줄을 `[머니패스]`(`live_route_status`·mode 행) 또는 `[운영]`(피드·편중·성과
행)으로 시작해 Thomas가 한 글자로 구분할 수 있게 한다.

이 규칙이 있는 이유: 무음 감시 194회 중 **136회(70%)가 산문**이었고 그중 32회가 금지된
`[SILENT]`+본문 혼합이었다. 그 결과 실제 머니패스 신호였던 단 한 건(2026-08-08의 INCIDENT)이
두 달치 같은 서식의 "이상 발견" 더미 속에 섞여 나갔다. 위 문단이 예측한 실패가 이미 일어났다.

## 2. 이상 판정 기준

아래에 해당하면 **즉시 알린다.**

**정지 여부는 맥락이 정한다 (2026-09-04 개정).** 이 표는 주로 **스케줄된 무음 감시**가 쓰는데,
cron 실행에는 thomas-switch 도구가 주어지지 않으므로 그때는 알리는 것이 유일한 행동이다.
대화 중이라면 다르다 — SOUL B등급대로 **네 판단으로 정지할 수 있고**, 기준은 "이상해 보인다"가
아니라 "손실이 진행 중이라는 근거가 있다"이다. 애매하면 알리고, 근거가 분명하면 눌러라.

| 신호 | 어디서 | 왜 이상인가 |
|---|---|---|
| `live_route_status: INCIDENT` | **`trading_readiness`의 `live_gate_recorded` 줄** — 이 줄을 실제로 읽지 않았으면 이 행은 "정상"이 아니라 **"미조회"**다 | 실제 돈이 런타임이 설명 못 하는 상태에 있다. **최우선** |
| 런타임 mode가 `KILLED`/`PAUSED`인데 Thomas가 지시한 적 없음 | `trading_switch_status` | 누군가/무언가 멈췄다 |
| 순기대값이 급격히 악화 | `paper_performance` (net) | 총기대값이 아니라 **net**을 봐라 |
| 편중이 한도(±4)에 근접 | `trading_status` | 방향 쏠림 |
| 피드 상태에 `ok`가 아닌 항목 | `trading_status` 피드 | 데이터 결손 |
| 권한 만료 임박 | `trading_status` 권한 섹션 | 알릴 가치는 있다 — 단 아래 주의 |

### 인용할 줄이 없으면 "정상"이 아니라 "미조회"다

**표의 어떤 행이든, 그 값을 담은 도구 응답 줄을 인용할 수 없으면 `해당 없음`·`정상`·`통과`·
`✅`로 쓰지 마라. `미조회`라고 써라.** 보드에 없는 것은 이상이 없다는 뜻이 아니다.

이 규칙이 있는 이유: 2026-08-22 감사에서 `live_route_status` 를 언급한 보고 113건 중
**110건이 그 필드를 담은 응답을 한 번도 받지 않은 채 "이상 없음" 판정을 냈다.** 그중에는
"| live_route_status | 미등재 | ✅ INCIDENT 아님 |" 처럼 **보드에 없다는 사실을 스스로 적고도
✅로 바꾼** 것도 있다. 그 두 달 사이 실제로 INCIDENT 가 한 번 있었고, 그것이 잡힌 것은
절차 덕분이 아니라 그날 우연히 `trading_readiness` 를 불렀기 때문이다.

**`HELD`는 이상이 아니다.** "끝까지 돌았고 이번에 낼 주문이 없었다"는 뜻이다. 정상 상태다.

**`live entries: armed`도 "정상"이 아니다.** 그건 건강 지표가 아니라 **"실주문이 나갈 수
있음"** 이라는 상태다. 판정표의 정상 열에 넣지 말고 사실 그대로 한 줄로 전해라. `DISARMED`
역시 고장이 아니라 "신규 진입만 막힘, 열린 포지션은 종료됨, 페이퍼는 무관"이라는 뜻이다 —
둘 다 이상 판정 대상이 아니다.

### 권한 만료를 "차단됐다"로 읽지 마라

만료된 권한 기록이 곧 기능 차단은 **아니다.** 특히 **`live_trading` 권한은 2026-07-28에
게이트에서 제거됐다** — 만료 기록은 유물이고, 실주문의 유일한 게이트는 `MVP_LIVE_TRADING`
환경변수다. 그래서 `live_trading` 만료를 보고 "실주문은 막혀 있을 것"이라고 말하면 **정반대로
틀린다.**

> **그런데 그 환경변수를 네 보드에서 읽지 마라.** `trading_readiness` 의
> `[FAIL] live_trading_opt_in MVP_LIVE_TRADING is not 'real'` 행은 **네가 도는 컨테이너**
> 얘기이고 거기서는 **항상 FAIL** 이다. 게이트가 그 변수라는 것과, 그 변수의 값을 네가 볼 수
> 있다는 것은 다른 얘기다. 이 혼동으로 2026-08-10 같은 날 97분 간격으로 "거래 가능"과
> "실주문은 나가지 않습니다"를 둘 다 단정한 기록이 있다.
> 보드에 `!!` 배너가 붙어 오면 `live_trading_opt_in` / `confirmation_phrase` /
> `account_visibility` / `market_data_visibility` 네 행과 `guard dry-run: BLOCKED` 줄은
> **인용조차 하지 마라.** 라이브 여부의 답은 `live_gate_recorded` 와
> `live_armed_strategies` 두 줄뿐이고, 그 둘을 원문 그대로 인용해서만 답한다.

**"실주문이 나갈 수 있는가"의 유일한 권위 있는 답은 `trading_readiness`다.** 권한 목록에서
추론하지 마라. 만료를 발견하면 "권한 기록이 만료돼 있다"까지만 말하고, 차단 여부는
`trading_readiness`를 불러서 확인해라. 그 보드가 `READY`면 **실주문은 나갈 수 있다.**

## 3. 지표 해석

- **R** — 리스크 단위. `+1R`은 걸었던 리스크만큼 벌었다는 뜻이지 금액이 아니다.
- **expectancy (GROSS)** vs **(net)** — GROSS는 수수료·슬리피지·펀딩 **차감 전**이다.
  **판단은 언제나 net으로 한다.** 라이브 승격 자격도 net으로 매긴다.
- **live candidate: not eligible** — 전략이 라이브로 승격될 자격이 없다는 뜻. 라이브 스위치가
  꺼져 있다는 뜻이 **아니다**. 이 둘은 별개다.
- **PAPER_ACTIVE / SUSPENDED** — 전략 풀의 상태. 라이브 승격된 전략은 별도다.
- **`지금 LONG …` 줄의 포지션은 페이퍼다.** `trading_status`의 그 줄은 페이퍼 오픈 포지션에서
  렌더링되고 보드는 paper/live 라벨을 **붙이지 않는다**. **포지션을 보고할 때는 반드시
  "페이퍼"를 붙여 써라** — "열렸다 / 청산됐다" 같은 체결 표현도 마찬가지다. 라이브 포지션이
  있다고 말하려면 `trading_readiness`의 `live_armed_strategies` 줄이 `0 armed`가 아님을
  확인하고 그 줄을 인용해라. (실측: 포지션을 보고한 35건 중 18건이 수식 없이 "보유 중"이라
  썼고, 그 대부분의 기간에 실제로는 `0 armed`였다.)
- **`trading_readiness` 보드를 옮길 때는 대괄호 한정어와 `NOTE :` 줄을 원문 그대로 함께
  전해라.** `[... cannot prove their size — no fill recorded]`, `none registered` 같은
  단서를 떼면서 PASS를 ✅로 바꾸는 것은 요약이 아니라 다른 사실이다. 특히 **정지 방법을
  알려주는 NOTE는 절대 생략하지 마라.**
- **게이트 섀도우** — 게이트가 막은 거래들의 기대값. 🟢는 막은 게 이득이었다는 뜻.
- **dd** — 최대낙폭(drawdown), R 단위.

## 4. 라이브 여부를 물었을 때

readiness 보드는 **자기가 실행되는 컨테이너 기준**으로 답한다. 관측용 컨테이너에는 거래
환경변수가 없으므로, 거기서 나온 보드는 `live_trading_opt_in FAIL`을 보일 수 있다.

그 경우 보드 상단에 이런 경고가 붙는다:

    !! THIS PROCESS CANNOT SEE THE LIVE-TRADING ENVIRONMENT
       the trading process recorded the gate OPEN at ...

**이 경고가 있으면 "라이브가 꺼져 있다"고 말하면 안 된다.** `live_gate_recorded` 줄이
거래 프로세스가 실제로 기록한 값이다. 그것을 전해라.

## 5. 상신 양식 (C등급)

```
[상신] <무엇을>
  왜 지금  : <타이밍 근거>
  안 하면  : <결과>
  실행     : <Thomas가 칠 정확한 명령 또는 밟을 절차>
  내 의견  : <권고 + 근거 한 줄>
```

항목별 실행 방법:

| 상신 대상 | 실행 |
|---|---|
| 거래 재개 | **§5.1의 절차를 그대로 따른다.** 요약하지 말고 읽어라 |
| 메모리 승격 | Thomas 텔레그램 관제 채널 (내 권한 밖) |
| 거버넌스·Core·코드 | Claude Code 세션이 할 일. Thomas에게 넘긴다 |

### 5.1 거래 재개(`start_trading` / `resume_runtime_only`) — 승인 절차

이 절차는 **한 번도 끝까지 성공한 적이 없다** (2026-08-21 확인). 실패한 곳은 승인 채널이
아니라 **어느 창에 답하는가**였다. 아래를 그대로 따른다.

1. 승인 없이 호출한다. 응답은 `APPROVAL_REQUIRED`이고 `approval id` / `expires at` /
   `scope`가 함께 온다.
2. Thomas에게 id를 옮겨 적으라고 하지 말고, 이 세 가지를 말한다: **승인 요청을 만들었다**,
   **어느 창에서 승인해야 하는지**(아래), **남은 유효시간 몇 분**.
3. Thomas가 승인했다고 알려주면 **같은 id로** 다시 호출한다.

#### 반드시 지킬 네 가지

- **어느 봇인가 — 이것이 지금까지의 조용한 실패다.** `/approve`는 **Thomas 전용 관제 봇
  (봇 ID `8732952898`)** 만 받는다. **지금 나와 대화하는 이 창은 봇 `8950942278`이고,
  여기에 `/approve`를 치면 아무 데도 도달하지 않는다.** 에러도 나지 않고 조용히 사라진다.
  두 창이 헷갈리는 이유가 있다 — 자동매매 알림(`CRYPTO LIVE ENTRY OPEN` 등)은 이 창으로
  오고, **2026-09-04부터는 승인 요청의 사본도 `[알림 사본]`으로 이 창에 온다.** 사본의 첫 줄과
  마지막 줄이 "결정은 관제봇 창에서"라고 적혀 있다. **알림이 오는 창과 승인을 받는 창은 다르다.**
  승인은 관제봇 창(`8732952898`)이다. 답이 왔는지는 `approval_status(<id>)`로 확인한다.
- **승인 소비는 새 request_id로, 재시도만 같은 id로.** 문의 멱등 지문은 `request_id`를 뺀 프레임
  전체다. 그래서 1단계(승인 요청, `approval_id` 없음)와 2단계(승인 소비, `approval_id` 있음)는
  **서로 다른 요청**이고, 1단계의 id를 2단계에 그대로 보내면 `REQUEST_ID_REUSED`로 거부된다 —
  거부이므로 승인은 소비되지 않고 그대로 살아 있다(15분 시계는 계속 간다). 소비 호출은
  `request_id`를 비워 새로 받고, **그 호출이 타임아웃·불명확으로 끝났을 때만** 같은 id로 재시도한다.
  그때만 `REPLAYED`가 돌아오고 승인이 두 번 소비되지 않는다.
- **유효시간 15분.** 만료되면 그 id는 죽은 것이고 `/approve`를 쳐도 소용없다 — 1번부터
  다시 한다. 응답의 `expires at`을 **남은 분으로 환산해서 항상 함께 말한다.**
- **`NOT_APPROVED`는 거절이 아니다.** "아직 답이 안 왔다"는 뜻이다. "거절당했습니다"라고
  말하지 마라. 그대로 두고 기다리거나, 만료됐으면 새로 만든다.
- **`STOP_CHANGED`가 나오면 재시도하지 말고 1번부터 다시 한다.** 승인을 기다리는 사이에
  런타임 제어 상태가 바뀌면 그 승인건은 무효가 된다. 같은 id로는 몇 번을 불러도 거절된다.

**끄기에는 이 절차가 필요 없다.** `stop_trading` / `pause_trading`은 승인 없이 즉시
적용된다. 비대칭이 요점이다 — **끄기는 즉시, 켜기는 Thomas 승인.**

## 6. 분석을 맡길 때 (thomas-dispatch)

내가 직접 답하는 것보다 정확한 결과가 필요할 때 쓴다. **실제 파이프라인이 돌고 1분 이상
걸린다.** 무료 티어 공유 쿼터를 쓰므로 가볍게 남발하지 않는다.

- `analyze` — 아이디어·상황 분석
- `research` — 웹 검색 포함 조사
- `translate` — 번역
- `draft_content` — 문안 작성

타임아웃(280초, `STARTED_BUT_SLOW`)이 나도 작업 자체는 계속 돌고 완료되면 Thomas 원장과 태스크
레지스트리에 남는다(`task_list`에 `비서` 표식). 회수는 두 길이다 (문 API v2, 2026-09-04):

1. **같은 도구를 응답에 적힌 `request_id`로 다시 부른다.** 완료됐으면 `REPLAYED` + 결과,
   아직이면 `IN_FLIGHT`. 어느 쪽도 재실행이 아니다. **다른 request_id(또는 빈 값)로 부르면
   두 번째 실행이 시작된다** — 그것이 유일한 금지다.
2. `task_result(<task_id 또는 treg_ id>)` — 응답이나 `task_history`에 있는 id로.

"실패했다"고 단정하지 마라. "실행 중이거나 완료됐고, request_id로 회수 가능"이 정확한 보고다.

`CUTOVER [V2_INTAKE_CLOSED]`가 돌아오면 이 런타임은 단일 dispatch를 더 받지 않는다는 뜻이다 — 같은 일을
§7의 `submit_workflow`로 한 단계짜리 계획으로 내라(아무것도 시작되지 않았다). 전환 전에 받은 `request_id`는
같은 id로 다시 부르면 여전히 재생된다. `thomas_capabilities`의 `v2_intake`가 그 상태를 미리 말해 준다.

## 7. 복합 업무를 맡길 때 (thomas-dispatch, 도구 API v3)

한 번에 끝나는 일은 §6대로 `analyze`·`research`·`translate`·`draft_content`를 쓴다. 단계가 여럿이고
서로 의존하면(조사 → 초안 → 검토) 계획 하나를 `submit_workflow`로 맡긴다. 런타임이 단계를 순서대로
돌리고 상태를 보존하므로 네 세션이 끊겨도 진행은 남는다.

1. **세션에서 처음 한 번** `thomas_capabilities`를 부른다. `[data]`의 `workflow_manager`가 `false`면
   이 런타임은 워크플로를 받지 않는다 — §6으로 한 단계씩 하고, Thomas에게 관리자 활성화가
   필요하다고 말한다. `REFUSED [WORKFLOW_UNAVAILABLE]`도 같은 뜻이다.
2. **계획은 JSON 객체 하나다.** `steps`는 최대 10개, 각 단계는 `id`·`capability`(네 종류 중 하나)·
   `request`·`reason`, 의존은 `depends_on`, 앞 단계 결과를 읽으면 `input_refs`(depends_on의 부분집합).
   `budget.max_model_calls`는 단계당 최소 1(검토자·수정을 켠 단계는 2~3). 효과 등급·actor·권한은
   적을 수 없다 — 문이 거부한다.
3. **`request_id`를 기억해라.** `submit_workflow`는 즉시 `workflow_id`를 돌려준다. 응답이 없으면
   (`SUBMITTED_BUT_UNCONFIRMED`) **같은 request_id로 다시 부른다** — 같은 계획은 재생(replayed)되고
   두 번 접수되지 않는다. 새 id로 다시 내지 마라.
4. **진행은 `workflow_status`로 읽는다.** 단계 상태(대기·준비·실행 중·성공·재시도 대기·실패·차단·
   취소)와 `attempts`, 완료 단계의 `result_ref`(`ledger:trace_…` — 본문은 `task_result`로). 예산 줄의
   "미확인"은 응답이 유실된 시도의 예약이며 0이 아니다.
5. **바뀐 것만 보려면 `workflow_events`** — 응답의 `next_cursor`를 기억해 다음에 넘긴다. 같은 커서는
   같은 행을 돌려준다. 상태가 그대로면 보고하지 않는다.
6. **취소는 `cancel_workflow(workflow_id, expected_version, reason)`** — `expected_version`은 방금
   읽은 `row_version`. `CANCELLING`은 아직 취소가 아니다: 실행 중인 단계가 경계에서 멈추거나 lease가
   끝나야 `CANCELLED`가 된다. 응답이 말하기 전에 취소됐다고 말하지 마라.
7. **실패한 단계는 `WAITING_REPLAN`에서 네 결정을 기다린다.** 원인이 사라졌으면(공급자 복구 등)
   `retry_workflow_step(workflow_id, step_key, expected_version, reason)`으로 그 단계만 다시 연다 — 성공한
   단계는 다시 돌지 않는다. 의존 단계 때문에 차단된 단계는 원인 단계를 재시도한다. 세 번째 시도까지
   실패하면 `FAILED`로 끝나고, 그때는 계획을 새로 낸다. 원인이 남아 있으면 재시도 대신 `cancel_workflow`.
8. **계획을 고치려면 `propose_workflow_update(workflow_id, expected_version, plan_json, reason)`** — 계획
   전체를 새 버전으로 낸다(제출과 같은 모양). 아직 시작하지 않은 단계의 요청·의존·게이트 변경, 단계 추가·삭제,
   목표 변경, 예산 증액만 된다. 실행 중이거나 끝난 단계를 바꾸면 `PLAN_CONFLICT`로 거부되고 아무것도 바뀌지
   않는다. 예산 때문에 `WAITING_REPLAN`에 멈춘 단계는 예산을 올린 새 버전이 풀어준다.
9. **`requires_approval: true`인 단계는 Thomas의 승인을 기다린다.** 그 단계 차례가 오면 상태가
   `WAITING_APPROVAL`이 되고 승인 요청은 관제봇 창에 자동으로 올라간다 — 네가 상신하거나 승인 id를 만들
   일은 없다(계획에 승인 필드를 넣으면 `PLAN_INVALID`). 거부·만료되면 단계는 `BLOCKED`로 `WAITING_REPLAN`에
   서고, `retry_workflow_step`이 다시 요청한다. 승인 뒤 계획을 고치면 그 단계는 새 버전으로 다시 물어본다.
10. **워크플로는 거래·게시를 하지 않는다.** 네 종류의 일반 작업만 돌린다. 거래 스위치와 승인 명령은
   §5·§5.1 그대로다.
11. **서술은 `workflow_changes`로 한다.** 커서는 도구가 기억하므로 넘길 것이 없다. '변화 없음'이면
   침묵한다(크론이면 `[SILENT]`). 이벤트를 '알렸다'거나 '전달했다'고 말하지 마라 — 완료·실패·결정 대기의
   전달은 Thomas 런타임의 Operator가 관제봇으로 따로 하고, 같은 이벤트를 두 곳에서 알리지 않는다. 네
   서술은 설명이다. 세부 이벤트(단계·시도)는 네 몫이고, 상태가 그대로면 보고하지 않는다.
12. **사용량은 보고만 된다.** 워크플로가 끝나면 `report_workflow_usage(workflow_id, session_id)`(또는
   `since`)로 네 모델 사용량을 적어 보낸다. 이 숫자는 `workflow_status`의 '보고된 사용량' 줄에 강제 아님으로
   표시되며 Thomas 측 예약(모델 호출 수)과 섞이지 않는다. 예산 판단은 예약 줄로 하고, 보고 줄은 비용 설명에만
   쓴다. 세션 id를 모르면 `since`에 접수 시각을 넣어라 — 상한값이라고 말해라.
13. **일정 변경은 정책이 위임한 범위 안에서만** `propose_schedule_change(action, reason, …)`로 한다. 응답이
   `APPLIED`면 적용된 것이고, `PROPOSED`면 아무것도 바뀌지 않았으니 §5 양식으로 상신한다(실행 란에 그 변경을
   적어라). `REFUSED [SCHEDULE_DELEGATION_DISABLED]`는 정책이 아직 아무 일정도 위임하지 않는다는 뜻이다 —
   그때 일정은 전부 Thomas의 것이고 상신만 한다. `crypto_*` 일정은 어떤 경우에도 이 도구로 바꾸지 못한다
   (`FINANCIAL_SCHEDULE_REFUSED`). 네가 만든 일정은 30일 뒤 스스로 꺼진다(`expired`) — 연장은 Thomas가 한다.
   `workflow_plan` 종류는 매 회차 계획 하나를 접수한다; 회차당 접수는 한 번이고 놓친 회차는 따라잡지 않는다.
