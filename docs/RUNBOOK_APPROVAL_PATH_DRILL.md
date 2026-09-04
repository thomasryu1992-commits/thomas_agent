# 절차서 — 승인 경로 드릴 (RUNBOOK)

**성격:** 분기 1회 실행하는 무위험 실증 절차. 코드 변경 없음. **이 문서는 권한을 부여하지
않는다** — 드릴이 만드는 승인건은 Thomas가 서명하는 것이고, 그 서명은 언제나 그의 것이다.
**왜 있는가:** 스위치 문의 `enable` 체인은 **이 드릴 전까지 배포 이후 한 번도 끝까지 성공한
적이 없었다**(2026-08-21 확인). 유일한 실제 시도(`approval_cb281753…`, 2026-07-31 03:25:56Z
발행)는 03:40:56Z에 답을 못 받고 만료됐다. 즉 "끄기는 즉시, 켜기는 승인"이라는 이 시스템의
비대칭 중 **켜기 쪽은 단위 테스트로만 증명돼 있었다.** 이 드릴은 그것을 실물로 증명한다 —
2026-08-22 첫 실행이 46초 만에 완주해 실제로 증명했고, 그 과정에서 발견 2건이 나왔다(§7, §5).
**검증:** §2의 읽기 전용 확인은 2026-08-21에, §3의 드릴은 2026-08-22에 실행했다. 이 문서의
절차는 전부 실물로 한 번씩 지나갔다.
**2026-09-04 두 번째 실행 (61초, door API v2):** Thomas의 지시로 어시스턴트 쪽 1·4번을 hermes
컨테이너(uid 10000)의 shim v2가 실행했고, 3번은 Thomas가 관제봇에서 승인했다. 같은 드릴에서 두 가지가
추가로 증명됐다 — 승인 요청 사본이 Hermes 창에도 도착하는 **미러링**(PR11, operator 로그 `announced approval`,
`not mirrored` 없음)과, 소비 호출을 같은 `request_id`로 재전송하면 승인이 두 번 소비되지 않고 `replayed`로
답하는 **멱등 replay**(PR9). 최종 상태 CONSUMED, 런타임 ACTIVE 그대로.

---

## 0. 한 줄

**돈에 닿지 않고 승인 체인 전체를 지나가는 길이 하나 있다** — 런타임이 ACTIVE일 때
`scope: "runtime"` resume을 요청하는 것. 같은 문, 같은 승인 저장소, 같은 텔레그램 채널,
같은 지문 검사를 전부 통과하면서 `mode`도 `trading_armed`도 바꾸지 않는다.

---

## 1. 왜 지금까지 실패했는가 — 봇이 두 개다

이것이 근본 원인이고, 드릴은 이것부터 검사한다.

| 봇 ID | 무엇인가 | `/approve` |
|---|---|---|
| `8732952898` | Thomas 전용 관제 봇. `thomas-operator`가 **유일하게 폴링**한다 | **여기만 받는다** |
| `8950942278` | Hermes 봇. Thomas가 실제로 대화하는 창 | 쳐도 아무 데도 안 간다 |

`docker-compose.yml`의 `scheduler` 블록이 이 상황을 스스로 적어 두었다 — 2026-08-01에
스케줄러 알림이 Hermes 봇 쪽으로 옮겨졌고, 그 이유는 *"operator Telegram이 Hermes로
옮겨가서 원래 봇은 아무도 안 읽는 대화가 됐다"*였다.

**결과:** Thomas가 사는 창에는 `CRYPTO LIVE ENTRY OPEN` 같은 알림이 오는데, `/approve`는
알림이 오지 않는 반대쪽 창에 쳐야 한다. 조용히 실패하고 에러도 나지 않는다.

---

## 2. 전제조건 — 전부 읽기 전용

세 가지를 확인한다. 하나라도 어긋나면 드릴을 하지 않는다.

```bash
# (1) 런타임이 ACTIVE 인가. ACTIVE 가 아니면 이건 드릴이 아니라 진짜 재개다 — 중단하라.
docker exec thomas-scheduler cat /app/.runtime_governance_state/operator_control_state.json
```

```bash
# (2) 살아 있는(=미만료) 미결 switch 승인건이 없는가.
#     드릴의 resume 이 control state 의 updated_at 을 다시 쓰면 그런 grant 의 stop_ref 가
#     무효화되어 STOP_CHANGED 로 죽는다. 만료된 것은 어차피 못 쓰므로 상관없다.
#
#     주의: approvals.jsonl 은 append-only 라 한 승인건이 여러 줄로 나타난다. 반드시
#     approval_id 별 **마지막 줄**로 접어야 한다 — 접지 않으면 이미 APPROVED/CONSUMED 된
#     건의 옛 PENDING 줄까지 세어 없는 blocker 를 만들어 낸다.
docker exec thomas-scheduler python -c "
import json, datetime
now = datetime.datetime.now(datetime.UTC).strftime('%Y-%m-%dT%H:%M:%SZ')
rows = {}
for l in open('/app/.runtime_governance_state/approvals/approvals.jsonl'):
    d = json.loads(l)
    rows[d.get('approval_id')] = d          # 마지막 줄이 이긴다
blockers = []
for k, d in rows.items():
    if d.get('status') != 'PENDING':
        continue
    ref = (d.get('approved_action_snapshot') or {}).get('target_ref') or ''
    if not ref.startswith(('trading_switch:', 'runtime_resume:')):
        continue                             # 다른 스코프는 stop_ref 를 안 쓴다
    exp = (d.get('validity') or {}).get('expires_at') or ''
    if exp > now:
        blockers.append((k, ref, exp))
print('BLOCKERS:', blockers or 'none — 드릴 진행 가능')
"
```

```bash
# (3) 문이 살아 있고 피어 게이트가 켜져 있는가.
docker logs thomas-switch-bridge 2>&1 | grep -o 'uids=[^)]*' | tail -1   # → uids=[10000]
```

---

## 3. 절차

**Thomas가 실행한다. 어시스턴트는 1·4번만 대신할 수 있다.**

1. **Hermes에게 요청한다.** 텔레그램(봇 `8950942278`)에서:

   > 승인 경로 드릴이다. `resume_runtime_only` 를 승인 없이 호출해서 승인 요청만 만들어라.

   Hermes는 `resume_runtime_only(reason="quarterly approval-path drill")` 을 부른다.
   문은 `{"command":"enable","reason":…,"domain":"crypto","scope":"runtime"}` 를 받고
   `APPROVAL_REQUIRED` 와 함께 `approval id` / `expires at` 을 돌려준다. **아무것도
   바뀌지 않았다.**

2. **승인문을 읽는다.** 런타임이 ACTIVE이므로 `risk_reason` 에 이렇게 적혀 있어야 한다:

   > no stop — the runtime is already ACTIVE, so this grant would resume nothing
   > (it stays spendable only while that remains true)

   **이 문장이 이 드릴이 무위험인 이유다.** 서명하는 문서가 스스로 "아무것도 재개하지
   않는다"고 말한다. 이 문장이 안 보이면 런타임이 ACTIVE가 아니라는 뜻이므로 중단하라.

3. **승인한다 — 봇 `8732952898` 창에서.** Hermes와 대화하는 창이 아니다.

   ```
   /approve <approval id>
   ```

   **유효시간 15분**(`RUNTIME_GOVERNANCE` 스코프). 만료되면 그 id는 죽은 것이다.

4. **소비한다.** Hermes에게 승인했다고 알리면 같은 id로 다시 호출한다. 응답이
   `DONE … mode ACTIVE` 이면 체인이 끝까지 돈 것이다.

   > **`Live entries stay DISARMED` 줄을 기대하지 말 것 — 이 호스트에서는 안 나온다.**
   > 그 줄은 `control.py` 의 CMD_RESUME 분기에서 `not armed` 일 때만 붙는다. 이
   > 배포의 `trading_armed` 는 `true` 이고(아래 §4), runtime 스코프 resume 은 그것을
   > **보존**하므로 결과도 `armed` 다. 즉 이 드릴은 "재무장하지 않음"을 증명하지
   > **못한다** — 증명하는 것은 승인 체인이 돈다는 것이다. 줄이 없다고 실패로 읽지 말 것.

---

## 4. 무엇이 바뀌고 무엇이 안 바뀌는가

`control.py` 의 `CMD_RESUME` 분기에서 `armed = True if resume_arms else current.trading_armed`
이고, `runtime` 스코프는 `resume_arms=False`(`switch_bridge._TARGET_PREFIX_ARMS`)다.

| | |
|---|---|
| `mode` | **안 바뀐다** (ACTIVE → ACTIVE) |
| `trading_armed` | **안 바뀐다** (스톱이 둔 자리 그대로 — 이 호스트에서는 `true`, 아래 참조) |
| 라이브 진입 재무장 | **없다** — 이 경로는 구조적으로 못 한다 |
| 열린 포지션 | 영향 없음 |
| 페이퍼·스케줄 | 영향 없음 |
| `updated_by` | `local_console` → **`assistant_bridge`** 로 바뀐다 |
| `updated_at` / `reason` | **다시 쓰인다** (드릴 시각 / 드릴 사유) |
| 원장 | 제어 이벤트 1건 |
| 승인 저장소 | 1건이 PENDING → APPROVED → CONSUMED |

**따라서 드릴 후 `trading_switch_status` 보드의 `updated_by` 는 `assistant_bridge`,
`reason` 은 드릴 사유가 된다.** 이것은 고장이 아니다. 다음 드릴까지 그대로 남는다.
헷갈리지 않도록 `reason` 에 반드시 "drill"이라는 말을 넣는다.

> **`trading_armed` 가 파일에 없던 것이 있는 것으로 바뀐다 — 값이 바뀌는 것이 아니다.**
> 2026-08-22 첫 드릴에서 관측됐다. 드릴 전 `operator_control_state.json`(2026-07-18
> `local_console` 기록)에는 `trading_armed` 키가 **아예 없었고**, `control.py` 의 로더가
> `data.get("trading_armed", True)` 로 없는 키를 `True` 로 읽는다. runtime 스코프 resume 은
> `armed = current.trading_armed` 로 그 값을 그대로 보존해 파일에 명시적으로 써 넣는다.
> 즉 실효값은 처음부터 `armed` 였고 드릴이 바꾼 것은 **표현뿐이다.** 이 필드가 도입되기
> 전에 쓰인 상태 파일을 가진 배포라면 어디서나 같은 일이 일어난다.

---

## 5. 실패 지점별 판독 — 어디서 멈췄는지가 곧 진단이다

| 증상 | 무슨 뜻인가 |
|---|---|
| 1번에서 `APPROVAL_REQUIRED` 가 안 나옴 | 문이 죽었거나 피어 게이트가 Hermes를 막는다. §2(3) 확인 |
| 3번 후 4번이 `NOT_APPROVED` | **거절이 아니다.** 승인이 도달하지 않았다는 뜻 — 십중팔구 엉뚱한 봇 창. §1 |
| 4번이 `STOP_CHANGED` | 승인을 기다리는 사이 제어 상태가 바뀌었다. 재시도하지 말고 1번부터 |
| 4번이 `ALREADY_CONSUMED` | 이미 성공했다. 1회용이다 |
| 승인건이 답 없이 만료 | **이것이 지금까지의 실패 모드다.** 15분 안에 못 봤다는 뜻 |

특히 **`NOT_APPROVED` 로 끝나면 드릴은 실패한 것이 아니라 성공한 것이다** — 채널이
끊겨 있다는 사실을 15분 만에 알아낸 것이고, 그게 이 드릴의 목적이다.

### 문이 한 말과 Hermes가 옮긴 말을 대조하라 — 드릴의 두 번째 산출물

2026-08-22 첫 드릴에서 Hermes는 완료를 이렇게 보고했다:

> "crypto 도메인의 런타임이 ACTIVE 상태로 전환되었습니다. **이제 실시간 거래를 포함한**
> 모든 작업 요청을 처리할 수 있습니다."

문이 돌려준 문장은 `control.py` 기준 *"Resumed. The runtime is ACTIVE and will accept
task requests again."* 이다. **"실시간 거래를 포함한"은 문이 하지 않은 말이다.** 이번에는
우연히 참이었지만(§4의 `trading_armed` 사정) 근거가 있어서 참이었던 것이 아니다. runtime
스코프 resume 은 재무장을 **할 수 없는** 경로이므로, 무장이 꺼져 있던 배포에서 같은 문장이
나왔다면 머니 패스에 대한 명백한 거짓이 된다.

`switch_bridge_mcp.resume_runtime_only` 의 docstring 이 이 실패를 미리 지목해 두었다 —
*"do not soften it into 'the runtime is back up' without saying trading is still stopped."*

**드릴할 때마다 이 대조를 하라.** 문의 원문과 어시스턴트의 요약이 머니 패스에 대해 같은
말을 하는지가, 승인 체인이 도는지만큼 중요하다.

---

## 6. 하지 말 것

- **`disable` 로 먼저 멈추고 `enable` 로 되살리는 구성으로 드릴하지 말 것.** `PAUSED` 와
  `KILLED` 는 둘 다 `execution_allowed=False`(`control.py`)라 실거래 사이클이 선다.
  승인이 안 오면 런타임이 멈춘 채로 남는다. ACTIVE 상태에서 도는 것이 요점이다.
- **`start_trading`(= `scope: trading`)으로 드릴하지 말 것.** 그쪽은 `resume_arms=True`라
  실제로 라이브 진입을 재무장한다. 드릴이 아니다.
- **살아 있는 미결 switch 승인건이 있을 때 하지 말 것.** §2(2).
- **어시스턴트가 승인을 대신 실어 나르게 하지 말 것.** 그 설계는
  `HERMES_AGENT_SWITCH_V0.1.md` 에서 `other_user` + `ambiguous_expression` 으로
  `invalid_approval_sources` 에 걸려 이미 한 번 기각됐다.

---

## 7. 기록

실행할 때마다 한 줄 append 한다. 실패도 적는다 — 실패가 이 문서의 산출물이다.

| 날짜 | 1→2 도달 | 승인까지 걸린 시간 | 4번 결과 | 멈춘 지점 / 비고 |
|---|---|---|---|---|
| 2026-08-22 | 도달 | **29초** (05:36:39 발행 → 05:37:08 승인) | CONSUMED 05:37:25Z, 총 46초 | **완주.** `approval_3680ef714b828ace466e`, `runtime_resume:crypto`, `VERIFIED` / `telegram_private_control_channel`, 지문 3줄 동일, TTL 15분 중 3% 사용. 배포 이후 이 체인의 **첫 성공**. 발견 2건: (a) Hermes 가 완료 보고에 "실시간 거래를 포함한"을 덧붙였는데 문은 그런 말을 하지 않았다 — §5 참조, (b) `trading_armed` 가 키 부재→명시 `true` 로 바뀌었다(값 변화 아님, §4) |

---

## 8. 관련 문서

- `docs/proposals/HERMES_AGENT_SWITCH_V0.1.md` — 이 문이 왜 이 모양인지, 그리고 기각된 두 인증 설계
- `runtime/mvp_runtime/switch_bridge.py` — 동사 집합, `stop_ref`, `_TARGET_PREFIX_ARMS`
- `governance/GOVERNANCE_POLICY.yaml` — `control_channel`, `scope_max_ttl_minutes.RUNTIME_GOVERNANCE`
- `/root/hermes-trial/data/skills/thomas-ops/SKILL.md` §5.1 — Hermes 쪽 절차
