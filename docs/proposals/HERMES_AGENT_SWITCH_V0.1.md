# 제안: Hermes 스위치 문 — S1·S3 채택 후 출시, S2 거부, S4 미착수

> **상태 (2026-08-03 갱신): 더 이상 결정을 기다리는 문서가 아니다.** §8의 결정은 내려졌고,
> 채택된 것은 2026-07-31 [#387](https://github.com/thomasryu1992-commits/thomas_agent/pull/387)에서
> 구현되어 이 호스트에서 `thomas-switch-bridge`로 가동 중이다.
>
> | 결정 | 결과 |
> |---|---|
> | **S1** 스위치 문을 만든다 | **채택 · 출시** — #387 (2026-07-31) |
> | **S2** `enable`의 라이브 게이트 조건 분기 | **거부** — `enable`은 게이트 상태와 무관하게 **항상** 승인을 요구한다. fail-closed 쪽 읽기가 채택됐다 |
> | **S3** halt 문을 `disable`로 흡수, 문 3개 유지 | **채택 · 출시** — #387 |
> | **S4** 자금 스냅샷을 별건으로 | **미착수** — `read_bridge`의 명령 집합에 해당 verb 없음 (2026-08-03 확인) |
>
> **§9의 정책 diff는 적용되지 않았고, 적용될 일도 없다.** 조문이 필요했던 것은 S2뿐이었고
> S2가 거부되면서 근거가 사라졌다 — #387은 `governance/GOVERNANCE_POLICY.yaml`을 한 줄도
> 바꾸지 않는다. §9는 **채택되지 않은 초안**으로 남는다.
>
> **[갱신 2026-08-30] 위 결정은 §9에 대해서는 그대로 서 있고, 레인 자체는 policy 1.4.0에
> 조문화됐다** (`control_channel.assistant_switch`). 근거가 바뀐 경위: 1.3.0이 dispatch 레인을
> 정책에 등재하자, 정책만 읽는 독자는 dispatch가 어시스턴트의 유일한 레인이라고 결론짓게
> 됐다 — 등재 안 된 쪽이 하필 라이브 재무장이 걸린 레인이라 위험한 방향의 오독이다. 1.4.0의
> 블록은 §9의 분기(S2)가 아니라 **무조건 승인**을 조문화하며, 새 권한을 하나도 만들지 않는다
> (disable=halt 문, enable=기존 승인 기구 — 이 문서의 원래 논리 그대로).
>
> **본문(§0–§7, §9)은 제안 원문 그대로 둔다.** 무엇을 제안했고 무엇이 그 모양대로 지어졌는지의
> 대조가 이 문서에 남은 값이기 때문이다. 특히 §3–§5의 **버려진 두 인증 설계와 그 이유** —
> TOTP(승인 개념이 둘이 되고, Thomas가 인증 앱을 불편하다고 판단)와 어시스턴트가 승인을 실어
> 나르는 방식(`other_user` + `ambiguous_expression`으로 `invalid_approval_sources`에 걸림) —
> 는 코드 어디에도 남지 않는 판단이라 여기서만 읽을 수 있다. 따라서 **본문의 미래시제 문장은
> 현재 사실의 진술이 아니다.**
>
> **이 문서는 어떤 것의 authority도 아니다.** 구현 사실은 `docs/ACTIVE_ARCHITECTURE.md`,
> 권한·효과는 `governance/GOVERNANCE_POLICY.yaml`, 왜 그 모양인지는 `docs/BUILD_HISTORY.md`.
>
> 상태 확인 기준: `main` = `38e9c50` (2026-08-03). 위 표의 상태는 #387의 본문과 diff,
> `read_bridge.py`의 명령 집합, 그리고 이 호스트의 실제 컨테이너에서 직접 확인했다.
>
> ---
>
> **갱신 (2026-08-22): 이 문이 지어진 뒤 3주 동안 그 핵심 경로는 한 번도 끝까지 돈 적이
> 없었다.**
>
> S1이 출시한 `enable` 체인 — 민팅 → Thomas 승인 → 1회 소비 — 은 2026-08-22 이전에
> **프로덕션에서 단 한 번도 완주하지 못했다.** 유일한 실제 시도(`approval_cb281753`,
> 2026-07-31 03:25:56Z)는 15분 뒤 답 없이 만료됐다. 원인은 이 문서가 다룬 어떤 것도 아니었다:
>
> - **아무도 ask를 전달하지 않았다.** `approval.py`에는 notify 경로가 없고(설계상 순수 기록
>   모듈), 다른 모든 승인은 Thomas가 직접 스크립트를 돌려 자기 화면에서 id를 읽는 동기 흐름이다.
>   스위치 문의 ask만 에이전트가 비동기로 만들고 어디로도 가지 않았다.
> - **`/approve`를 칠 창이 알림이 오는 창과 달랐다.** 이 호스트는 봇이 둘이고(`docker-compose.yml`의
>   `scheduler` 블록이 그 교체와 이유를 기록한다), 어느 쪽인지 말해 주는 문서가 없었다.
> - **그리고 ask가 다른 결정을 설명하고 있었다.** `format_request`는 분기가 둘뿐이라 스위치
>   ask가 "되돌릴 수 있는가: 아니오 — validated memory는 지속됩니다"를 달고 나갔다. 실제로는
>   끄기가 승인 없이 즉시 되므로 **정반대**다 — 이 문의 비대칭 그 자체를 거꾸로 적은 셈이다.
>
> 세 가지 모두 2026-08-22에 고쳐졌고(전달은 [#750](https://github.com/thomasryu1992-commits/thomas_agent/pull/750),
> 봇 명시는 Hermes 쪽 스킬·MCP 셤), 같은 날 첫 완주가 기록됐다 —
> `approval_3680ef714b828ace466e`, `runtime_resume:crypto`, 발행에서 소비까지 46초. 재발
> 방지는 분기 1회 드릴이다: [`RUNBOOK_APPROVAL_PATH_DRILL.md`](../RUNBOOK_APPROVAL_PATH_DRILL.md).
>
> **§4-3의 불변식 하나는 이 기간 동안 구조적으로 강제되지 않았다.** 2026-07-31부터
> 2026-08-21까지 Hermes 컨테이너는 호스트 도커 소켓을 마운트했고, 그 경로는 이 문서의 세 문이
> 강제하던 제약(승인·P3·kind allowlist)을 전부 우회한다. 실제로 37회 사용됐다(전부 07-30~07-31,
> 마지막 07-31 07:29:58Z). 마운트는 Thomas 결정으로 **유지**되며, 사실은
> `docs/ACTIVE_ARCHITECTURE.md`와 Hermes 쪽 compose 헤더에 기록돼 있다. 또한 2026-08-21까지
> 문들은 gid만 검사했으므로 원장의 `assistant_bridge`는 Hermes를 식별하지 못했다 —
> `MVP_BRIDGE_CLIENT_UID=10000` 이후 구성상 참이 됐다.
>
> **S4(현재 자금)는 여전히 미착수다.** 설계는 §7 그대로 유효하다: 스케줄러가 잔고를 스냅샷하고
> read 문은 그 파일만 읽는다. 2026-08-22 재확인 — `read_bridge._READS`에 자금 verb 없음,
> `.runtime_governance_state`에 account/balance 스냅샷 파일 없음. Thomas 목표 3요소(포지션·자금·
> 수익률) 중 이것 하나만 남았다.

---

## 0. 한 줄

Hermes가 비서로서 자동매매를 보고하고 조작하게 하려면, **끄기는 이미 되고, 켜기는 새 인증
수단이 아니라 이미 존재하는 승인 경로(`/approve <id>`)의 재사용으로 성립한다.** 새로 만들
것은 문 하나이지 인증 체계가 아니다.

---

## 1. 목표와 현재 격차

목표는 Thomas가 정한 것이다: *"Hermes가 비서로서, 내가 요청하면 자동매매의 현재 포지션·현재
자금·수익률을 알려주고, 자동매매 스위치를 켜고 끌 수 있어야 한다. Prediction도 고도화되면
마찬가지."*

| 요구 | 현재 | 격차 |
|---|---|---|
| 현재 포지션 | `crypto_status` (read 문) | 없음 |
| 수익률 | `crypto_paper` (read 문) | 없음 |
| **현재 자금** | `dashboard --account`로만 — 거래소 네트워크 콜 | read 문이 쓰지 않음 (§7) |
| **끄기** | halt 문 (`kill`/`pause`) | 없음 |
| **켜기** | `RESUME_NEVER_PERMITTED`로 구조적 거부 | **이 제안의 본체** |
| Prediction | `pred_report` 조회만 | 스위치 없음 (§6.3) |

---

## 2. 사실 정정 — `resume`은 금지되어 있지 않다

이 제안을 쓰기 시작할 때의 전제는 "`resume`은 정책이 허용하지 않는다"였다. 정책 원문은 그
반대다:

```yaml
control_channel:
  local_operator_console:
    emergency_controls_allowed:
      - pause
      - stop_task
      - kill
      - status
      - audit
      - recovery
      # resume: explicit Thomas decision (2026-07-19 QA wave 6b). ... physical/SSH access
      # to the host IS the operator authentication for the emergency path ...
      - resume
```

`resume`은 **로컬 운영자 콘솔에 대해 이미 허용된 동사**다. 근거는 호스트/SSH 접근 자체가
운영자 인증이라는 것이다. 막혀 있는 것은 오직 **어시스턴트를 경유한 resume**이며, 그것은
`halt_bridge.py`가 자기 코드로 거부한다 — 정책이 아니라 문의 구성이다.

그래서 이 제안의 범위는 **"resume을 여는 것"이 아니라 "어시스턴트가 개시한 resume에 어떤
권위가 필요한가"**이다. 정책에 새 권한을 만드는 일이 아니라, 이미 있는 권한에 도달하는
경로를 하나 정의하는 일이다. 범위가 좁아진 만큼 심사도 좁다.

---

## 3. 스위치는 두 층이다 — 어느 층을 비서에게 주는가

"자동매매 스위치"라는 하나의 말이 서로 다른 두 개를 가리킨다.

| | 층 1 — 런타임 킬스위치 | 층 2 — 라이브 게이트 |
|---|---|---|
| 실체 | `control.py`의 `kill`/`pause`/`resume` | `MVP_LIVE_TRADING` 환경변수 |
| 반영 | 돌고 있는 서비스에 **즉시** | **재시작 필요** |
| 끌 때 | 신규 진입만 차단, **열린 포지션은 정상 종료됨** | **종료 경로까지 닫혀 포지션이 고립된다** |
| 성격 | 운영 동작 | 배포 동작 |

`docker-compose.yml`의 스케줄러 블록이 이 구분을 이미 못 박아 두었다:

> To halt a scheduler that is trading: `console_cli kill`. Do NOT clear `MVP_LIVE_TRADING` —
> it only takes effect on restart, and the close guard needs it, so it strands positions.

**따라서 비서에게 주는 스위치는 층 1뿐이다.** 층 2는 문이 절대 건드리지 않는다 — 읽기만
하고, 조건 판정에만 쓴다(§6.2). 이것은 편의상의 선택이 아니라, 층 2를 끄는 것이 "안전한
방향"으로 보이면서 실제로는 열린 실포지션을 고립시키는 동작이기 때문이다.

---

## 4. 무엇을 포기하지 않아야 하는가 (불변식)

1. **어시스턴트는 안전한 방향으로 마찰 없이, 위험한 방향으로 권위 없이.** 멈추는 데 승인을
   요구하면 급할 때 못 멈춘다. 켜는 데 승인을 안 받으면 문이 있으나 마나다.
2. **금융 실행의 승인은 명시적 `approval_id`, 단발 사용, 실행 직전 재검증.** 자연어 단독으로
   주문이 나가는 경로는 만들지 않는다. — `APPROVAL_CONVERSATION_V0.1.md` §6이 "어느 결정에서도
   유지되어야 하는 것"으로 이미 고정한 불변식이다. 이 제안은 그것을 재확인할 뿐 예외를
   요구하지 않는다.
3. **문은 권위를 갖지 않는다.** Hermes 쪽 시임은 프레임을 소켓에 넘길 뿐이고, 판정은 반대편에
   있다. halt/read/dispatch 세 문이 이미 이 형태다.
4. **돈 경로 env는 어시스턴트가 닿는 컨테이너에 두지 않는다.** `#382`가 이 원칙 때문에 생긴
   오보를 고쳤지, 원칙을 완화해서 고친 것이 아니다.
5. **fail-closed.** 모르는 상태·읽기 실패·권위 충돌은 BLOCK이며 추측하지 않는다.

---

## 5. 핵심 통찰 — 승인 기계를 새로 만들 이유가 없다

이 제안을 검토하면서 두 가지 인증 수단을 먼저 설계했고, 둘 다 폐기했다.

**TOTP(인증앱).** Hermes가 위조할 수 없다는 성질은 만족하지만, Thomas가 앱을 열어야 한다.
그리고 더 결정적으로, 런타임에는 **이미** 단발·TTL·명시적 표현을 강제하는 승인 레코드가 있다.
두 번째 승인 개념을 만드는 것은 "하나의 개념 = 하나의 authority" 가드레일 위반이다.

**어시스턴트 운반 승인(Thomas가 Hermes에게 "승인"이라고 말하면 Hermes가 서명을 나른다).**
Thomas가 처음 선택한 형태이지만, 정책과 정면 충돌한다:

```yaml
invalid_approval_sources:
  - other_user              # ← 어시스턴트 중계가 여기 걸린다
  - ambiguous_expression    # ← 자연어 "승인"이 여기 걸린다
  - different_action
```

Hermes를 경유한 승인은 정의상 `other_user`이고, 말로 하는 승인은 `ambiguous_expression`이다.
이걸 통과시키려면 금융 승인에 정책 예외를 뚫어야 하는데, 그것은 §4의 불변식 2를 깨는 일이고
`APPROVAL_CONVERSATION_V0.1.md`가 이미 "하지 말자"로 권고한 옵션 C보다도 넓다 — 그 문서는
비금융에 한정해서도 권고하지 않았다.

**이미 있는 것:** `operator.py` R9의 `/approve <id>` / `/reject <id>`, `approval_store`,
`TELEGRAM_PRIVATE_1_TO_1` 채널 게이트, 30분 TTL, 단발 사용. **켜기에 필요한 인증은 전부
존재한다.** 없는 것은 "스위치를 켜달라는 승인 건을 만들어 내는 경로" 하나뿐이다.

이 관찰이 제안 전체를 바꾼다. 이것은 인증 설계가 아니라 **배선(wiring)** 이다.

---

## 6. 메커니즘

### 6.1 켜기 — 요청은 Hermes, 승인은 Thomas의 인증 채널

```
Thomas ──"자동매매 켜줘"──▶ Hermes
                             │
                             ▼
                        switch.sock ──▶ thomas-switch-bridge
                             │            │ ① 킬스위치 상태 확인
                             │            │ ② 라이브 게이트 상태 확인 (읽기만)
                             │            │ ③ 승인 건 생성 (P5, 단발, TTL 30분)
                             ◀────────────┘
              APPROVAL_REQUIRED + approval_id + 무엇을 켜는지 요약
                             │
Hermes ──"켜려면 승인 필요합니다. 텔레그램에서:
          /approve appr_1a2b3c   (예산: 주문≤75 / 2회일 / 30분 내)"──▶ Thomas
                             │
                             ▼
        Thomas의 인증된 1:1 텔레그램에서 직접 /approve appr_1a2b3c
                             │
                             ▼
              승인 소비 → 실행 직전 재검증 → resume + 감사기록
```

Hermes가 나르는 것은 **요청과 id**이지 서명이 아니다. 서명은 Thomas가 인증된 채널에서 직접
찍는다. 이 형태는 `APPROVAL_CONVERSATION_V0.1.md`가 권고한 **옵션 B(승인 명령을 제안한다)**를
스위치에 적용한 것이며, 그 문서가 이미 "거버넌스 변경 없음 — 구현 승인만"으로 분류한 패턴이다.

Thomas 입장의 마찰: 텔레그램에서 한 줄 붙여넣기. 앱 설치 없음, 코드 암기 없음.

### 6.2 끄기 — 승인 없음

`disable`은 승인을 요구하지 않는다. 안전한 방향으로 가는 동작에 마찰을 걸면 급할 때 못
멈춘다(§4 불변식 1). 이것은 halt 문이 이미 하는 일이고, 스위치 문은 그 동사를 흡수한다 —
문을 늘리지 않기 위해서다.

**라이브 게이트에 따른 조건 분기.** `enable`은 서버 측에서 층 2 상태를 읽고 갈라진다:

| 라이브 게이트 | `enable` |
|---|---|
| 닫힘 | 승인 불요 — 재개되는 것은 페이퍼 거래이고 잃을 돈이 없다 |
| 열림 | **승인 필요** — 재개되는 것이 실주문이다 |

분기 근거가 요청 레코드가 아니라 **서버가 직접 읽는 상태**라는 점이 중요하다. Hermes도,
Hermes에 주입된 텍스트도 "나는 페이퍼입니다"라고 주장할 수 없다.

> **오늘 이 호스트의 게이트는 열려 있다.** 2026-07-31 기준 스케줄러는 `MVP_LIVE_TRADING=real`,
> confirmation phrase 정상, readiness 전 항목 PASS다. 즉 **지금 설정에서 모든 `enable`은
> 승인 경로를 탄다.** 조건 분기는 게이트가 닫힌 구간을 위한 일반 규칙이지, 현재의 편의가
> 아니다.

### 6.3 동사 — 도메인은 인자로 받는다

```
status(domain)            → 스위치 상태, 마지막 변경자·시각.        승인 불요
enable(domain, reason)    → 켜기.                                   §6.2의 조건
disable(domain, reason)   → 끄기 (kill) / 일시중지 (pause).          승인 불요
```

`domain`을 처음부터 인자로 받는 이유는 Prediction 때문이다. PM1이 고도화되면 문을 다시 만들지
않고 도메인만 추가한다. **문은 3개로 고정하고 도메인을 늘린다:**

| 문 | 역할 | 승인 |
|---|---|---|
| read | 조회 (포지션·자금·수익률·예측) | 없음 |
| dispatch | 분석·조사·번역·문안 (P3) | 없음 |
| **switch** | 켜기 / 끄기 / 상태 — halt 문을 흡수 | `enable`만, 조건부 |

### 6.4 구현 형태

기존 세 문과 같다: `runtime/mvp_runtime/switch_bridge.py` + `switch_bridge_cli.py`,
`socket_door` 재사용, 자기 컨테이너(`thomas-switch-bridge`, uid 10001), `switch.sock`.
`assistant_bridge`로 감사. **돈 경로 env는 넣지 않는다** — 층 2는 읽기만 하는데, 그 읽기는
`#382`가 만든 `recorded_gate` 경로(원장의 최신 `crypto_cycle`)로 하면 env 없이 가능하다.

---

## 7. 곁가지 — "현재 자금"

`dashboard --account`는 거래소로 소켓을 연다. 그 플래그가 opt-in인 이유는 **채팅 동사가
거래소로 소켓을 여는 것이 되면 안 되기 때문**이고(`domain_console` 주석이 명시), 그 판단은
이 제안이 뒤집을 것이 아니다.

**권고: 스케줄러가 자기 주기로 잔고를 스냅샷하고, read 문은 스냅샷을 읽는다.** 소켓은 Hermes가
아니라 런타임이 연다. 조회 결과에 스냅샷 시각을 함께 렌더해 신선도를 보이고, 오래되면
`#382`의 `recorded_gate`와 같은 방식으로 "지금에 대한 진술이 아님"을 명시한다.

스위치 문과 독립적인 작업이므로 **별도 PR**이며, 이 제안의 승인 대상이 아니다. 여기 적는
이유는 Thomas의 요구사항 세 개 중 하나가 이것이고, 빠뜨렸다는 인상을 남기지 않기 위해서다.

---

## 8. Thomas 결정 — 현재 상태 (2026-08-03)

| # | 결정 | 성격 | 상태 |
|---|---|---|---|
| **S1** | 스위치 문을 만든다 (§6) — 요청은 Hermes, 승인은 인증 채널의 `/approve <id>` | 정책 변경 **없음** — 구현 승인 | **채택 · 출시** (#387, 2026-07-31) |
| **S2** | `enable`의 라이브 게이트 조건 분기(§6.2)를 채택한다 | 정책 조문 (§9) | ~~채택~~ **거부** — `enable`은 무조건 승인을 요구한다 |
| **S3** | halt 문을 스위치 문의 `disable`로 흡수하고 문을 3개로 유지한다 | 구조 결정 | **채택 · 출시** (#387) |
| **S4** | 자금 스냅샷(§7)을 별도 PR로 진행한다 | 별건 승인 | **미착수** |

당시 권고는 "S1·S3는 지금, S2는 §9 조문과 함께, S4는 별건"이었고, 결정은 그보다 좁게
내려졌다: S2가 거부되면서 §9는 채택되지 않았다.

**S2가 거부된 결과가 이 제안보다 낫다는 점은 적어 둘 값이 있다.** 조건 분기는 "라이브 게이트가
이미 열려 있으면 승인 없이 `enable`" 을 허용하는 문이었고, 그것은 승인 요건을 런타임 상태에
의존하게 만든다 — 상태를 읽는 쪽이 틀리면 문이 열린다. 무조건 승인은 읽을 상태가 없다.
`enable`이 언제나 Thomas의 인증 채널에서 끝난다는 §8 말미의 불변식이, 조문 없이 구조로
성립한 셈이다.

S1이 정책 변경이 아닌 이유를 다시 확인해 둔다: `resume`은 이미 허용된 동사이고(§2), 승인
기계도 이미 있으며(§5), 새로 생기는 것은 "승인 건을 만들어 내는 경로"뿐이다. 정책에 적어야
하는 것은 그 경로가 **누구를 대신해 무엇을 요구할 수 있는지의 경계**이며 그것이 §9다.

**어느 결정에서도 유지되어야 하는 것:** 어시스턴트는 자기 판단으로 거래를 시작할 수 없다.
`enable`은 언제나 Thomas의 지시로 시작해 Thomas의 인증 채널에서 끝난다. 어시스턴트가 스스로
스위치를 켜는 경로는 이 제안 어디에도 없고, 만들지 않는다.

---

## 9. 제안 거버넌스 정책 diff (초안 — **미적용**)

```yaml
control_channel:
  # (기존 블록 유지)

  # NEW — 어시스턴트가 개시하는 제어 요청의 경계.
  assistant_switch:
    # 어시스턴트는 요청만 만든다. 승인은 언제나 primary_channel 에서 소비된다.
    request_only: true
    approval_source: TELEGRAM_PRIVATE_1_TO_1
    # 안전한 방향은 승인 없이. 위험한 방향은 승인 없이는 불가.
    verbs_without_approval:
      - status
      - disable          # kill / pause
    verbs_requiring_approval:
      - enable           # 라이브 게이트가 열려 있을 때 (아래 조건)
    approval_required_when:
      live_gate_open: true
    # 판정은 서버가 직접 읽는 상태로만 한다. 요청 레코드의 값으로 분기하지 않는다.
    gate_state_source: recorded_cycle_route_status
    domains_allowed:
      - crypto
    # 어시스턴트는 배포 동작을 갖지 않는다. 층 2는 읽기 전용이다.
    environment_mutation_allowed: false
    self_initiated_enable_allowed: false
```

`authority` 블록에 대응 게이트 1건:

```yaml
authority:
  assistant_switch_gate: thomas.switch.assistant_gate
```

**적용 시 `policy_version` 1.2.0 → 1.3.0.** 그 버전은
`validate_permission_approval_contracts.py`, 거버넌스 바인딩,
`read_only_kernel/preflight.py`, `test_architecture_slimming.py`에 하드핀되어 있어 **함께
움직여야 한다.** dispatch 문 때 별도 PR로 미뤄둔 그 번프와 동일한 작업이므로, 두 건을 한
PR에서 처리하는 편이 낫다.

---

## 부록 — 이 설계가 지키는 것들 (자기 점검)

| 가드레일 | 어떻게 지키는가 |
|---|---|
| 재사용 우선 | 새 승인 개념 0개. `/approve <id>`·`approval_store`·`socket_door`·`recorded_gate` 전부 기존 것 |
| 하나의 개념 = 하나의 authority | 승인의 authority는 여전히 `approval_store` 하나. 문은 승인을 만들 뿐 판정하지 않는다 |
| fail-closed | 게이트 상태 불명 → 승인 요구. 승인 만료·소비됨·동작 불일치 → BLOCK |
| 문은 권위를 갖지 않는다 | Hermes 시임은 프레임 전달만. 판정은 소켓 반대편 |
| 돈 경로 격리 | 스위치 문에 `MVP_LIVE_*`·`BINANCE_*` 없음. 층 2는 원장 기록으로 읽는다 |
| Claude는 라이브 머니 패스를 만지지 않는다 | 이 문서는 제안이다. 문을 만드는 것도, 켜는 것도 Thomas의 결정과 손이다 |
| 문 개수를 늘리지 않는다 | halt 흡수로 3개 유지. Prediction은 `domain` 인자로 확장 |

**이 설계가 막지 못하는 것 (정직하게):** Thomas가 승인을 습관적으로 눌러 주면 승인은 마찰일
뿐 통제가 아니게 된다. 승인 화면에 **무엇을 켜는지와 예산 상한을 항상 함께** 띄우도록 한 것은
그 때문이지만, 그것으로 충분하다고 주장하지는 않는다. 이 실패 모드는 설계가 아니라 운용의
문제이며, 여기서 해결되지 않는다는 것을 적어 둔다.

---

*작성 2026-07-31. 근거: `governance/GOVERNANCE_POLICY.yaml` (`control_channel`,
`emergency_controls_allowed`, `approval_lifetime`), `runtime/mvp_runtime/operator.py` (R9
`/approve`), `runtime/mvp_runtime/halt_bridge.py` (`RESUME_NEVER_PERMITTED`),
`runtime/mvp_runtime/crypto/live_readiness.py` (`recorded_gate`, #382),
`runtime/mvp_runtime/crypto/live_route.py` (`ROUTE_DISABLED`), `docker-compose.yml` (스케줄러
블록의 층 2 경고), `docs/proposals/APPROVAL_CONVERSATION_V0.1.md` §5–6,
`docs/proposals/HERMES_AGENT_DISPATCH_V0.1.md` (문 패턴).*
