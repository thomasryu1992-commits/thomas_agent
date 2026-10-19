# 제안: Option C C6b 선행 조건 1 — Claude 전용 read door의 운영화 설계 (DRAFT v0.1)

**상태:** DECIDED 2026-10-19 — RD-1–RD-4가 권고안 A로 정해졌다(아래 결정). 다음 단계는 RD-4 순서다. ① 코드 PR(클라이언트·compose 서비스·시험)은 같은 날 구현 승인을 받았다. ② 호스트 디렉터리, ③ 배포 1회, ④ O-1–O-9 검증은 각각 별도 승인이다. compose·배포·호스트 변경은 이 문서의 범위가 아니다. **원래 상태(작성 당시):** DRAFT 2026-10-19 — 결정 4건(RD-1–RD-4)이 Thomas를 기다렸다.

**결정(2026-10-19):** `결정: C6b read door RD-1=A(읽기 2개로 운영화, holdings·readiness는 C6b 뒤 Thomas가 전달, 보류 4개는 계약 구현 뒤 각각 승인), RD-2=A(/var/lib/thomas-claude-door/{sock,log}), RD-3=A(로그 보존은 b8에서 크기 확인 뒤), RD-4=A(코드 PR → 호스트 디렉터리 → 배포 1회 → O-1~O-9, 단계별 승인) (Thomas 2026-10-19)`

**승인:** `승인: C6b 선행 1 Claude 전용 read door 운영화 설계 패킷 작성(문서만, compose·배포 제외) (Thomas 2026-10-19)`

**기준:** origin/main `1bad6dc1`, 2026-10-19.
- **설계 권위:** `HOST_PRIVILEGE_SEPARATION_V0.1.md`.
  - D-11 a1·c1·d2·f2: §4.14 결정 패킷, §4.14.3 운영 체크리스트 O-1–O-9;
  - 보류 읽기 4개의 응답 계약: §4.19.2;
  - 코드: #1223(`8979e702`).
- C6a는 같은 날 설치됐다. `claude` 계정은 uid·gid 2101이다.
- 코드와 compose 파일만 읽었다. 호스트·운영 파일·`.env`는 보지 않았다.

---

## 0. 한 줄

코드는 main에 있다. 프로필 `claude`, 투영 응답, 요청 로그, uid 허용 목록이 그 코드다. 남은 것은 다음 세 가지다.
- 그 코드를 띄울 compose 서비스 하나;
- Claude가 호스트에서 부를 작은 클라이언트;
- 호스트 디렉터리 두 개.

다만 이 door가 지금 서비스하는 읽기는 `schedules`·`scheduler_events` 2개뿐이다. Claude가 지금 `docker exec`로 하는 holdings·readiness 읽기는 C6b 뒤 이 door로 옮겨지지 않는다. 그것이 RD-1이다.

## 1. 이미 있는 것 (main, 코드에서 확인)

| 항목 | 내용 | 위치 |
|---|---|---|
| 프로필 | `--profile claude`: 읽기 `schedules`·`scheduler_events`, 투영 계약 v1(닫힌 스키마, 결함이면 응답 전체 거부) | `read_bridge.py` `PROFILES` |
| 보류 읽기 | `runtime_status`·`heartbeat`·`crypto_readiness`·`holdings_status`. 원문을 그대로 답하므로 프로필에 넣지 않았다. 응답 계약은 §4.19.2에 설계만 있다(SCRATCH) | `read_bridge.py` `_PROFILE_HELD_READS` |
| 기동 조건 | 다음 둘 중 하나라도 없으면 listen하지 않는다(끄는 옵션 없음). 요청 로그 파일은 링크를 따라가지 않고 연다. | `read_bridge_cli.py`, history `2026-10-16-claude-read-door-profile.md` |
| | ① 비어 있지 않은 `MVP_BRIDGE_CLIENT_UID`(bind 뒤 재확인) | |
| | ② 쓸 수 있는 요청 로그(`--request-log`) | |
| 감사 | 응답마다 `{at, peer_uid, read, outcome}` 한 줄을 응답 전에 쓴다. 쓰지 못하면 `REQUEST_LOG_UNAVAILABLE` | 같은 곳 |
| 접근 | door가 소켓(0660)과 부모 디렉터리(0750)의 그룹을 `MVP_BRIDGE_CLIENT_GID`로 바꾼다. 이를 위해 그 gid가 `group_add`에 있어야 한다 | `socket_door.grant_client_access` |
| 클라이언트 함수 | `socket_door.call_door(path, request, deadline_seconds=…)` | `socket_door.py` |
| 이미지 | 비root uid 10001(`thomas`) | `Dockerfile` |

## 2. 운영화에 필요한 것

**2.1 compose 서비스 `claude-read-bridge`** (컨테이너 `thomas-claude-read-bridge`)
- **명령:** `python -m runtime.mvp_runtime.read_bridge_cli --profile claude --socket /claude-door/read.sock --request-log /claude-door-log/requests.jsonl`.
- **마운트:**
  - 상태 루트 `:ro`(f2);
  - 소켓 디렉터리 `/var/lib/thomas-claude-door/sock:/claude-door`;
  - 로그 디렉터리 `/var/lib/thomas-claude-door/log:/claude-door-log`.
  - 소켓은 `bridge/` 밖에 둔다. hermes 컨테이너는 `bridge/`만 마운트하므로 이 소켓에 닿지 못한다(O-3).
- **uid·gid는 리터럴:** `MVP_BRIDGE_CLIENT_UID: "2101"`, `MVP_BRIDGE_CLIENT_GID: "2101"`, `group_add: ["2101"]`.
  - 다른 door가 공유하는 `${MVP_BRIDGE_CLIENT_UID}` 변수는 쓰지 않는다(a1).
  - 그래서 Claude uid는 다른 door에 들어가지 않고(O-2), hermes uid(10000)는 이 door에 들어오지 않는다.
- **헬스체크:** `test -S /claude-door/read.sock`.
- **나머지:** 로그 드라이버 등은 `read-bridge`와 같게 둔다. `depends_on`은 두지 않는다.

**2.2 호스트 디렉터리** (Thomas, root. C6b 전, 배포 전에 만든다)
- `/var/lib/thomas-claude-door/`: root:root 0755.
- `sock/`: 소유 10001, 그룹 2101, 0750.
  - door가 기동 때 그룹과 모드를 다시 맞춘다.
  - `claude`는 디렉터리를 지나 소켓에 연결만 할 수 있고, 쓸 수 없다.
- `log/`: 10001:10001 0700.
  - `claude`는 읽지도 쓰지도 못한다(d2, O-7).
  - 보존 정책(R-08)은 아직 없다. 지금 상태로는 로그가 계속 자란다.

**2.3 Claude 쪽 클라이언트** (작은 코드 PR)
- `scripts/claude_read.py <read>` 하나를 둔다. `socket_door.call_door`로 `/var/lib/thomas-claude-door/sock/read.sock`에 요청 한 번을 보내고 응답 JSON을 그대로 출력한다.
- 읽기 이름은 프로필의 두 개만 받는다. 그 밖의 이름은 door가 거부하지만, 클라이언트에서도 먼저 막는다.
- `claude` 계정의 저장소 사본(b4)에서 시스템 `python3`로 돈다. `socket_door`는 표준 라이브러리와 같은 패키지의 `errors`만 import한다(확인함).

**2.4 시험** (같은 코드 PR)
- compose 정의를 고정하는 시험. 다음을 확인한다.
  - 리터럴 uid·gid 2101;
  - `--profile claude`, `--request-log`;
  - 상태 루트 `:ro`;
  - 소켓 경로가 `bridge/` 밖;
  - hermes 서비스 블록 무변경.
- 기존 `test_deployment_env_passthrough.py`의 hermes 경계 고정은 그대로 둔다.
- 클라이언트 시험:
  - 프로필 밖 읽기 거부;
  - door 미기동 시 `DOOR_UNREACHABLE`;
  - 출력이 응답 그대로.

**2.5 배포와 검증**
- compose 서비스 추가는 배포다. Option A의 DEPLOYMENT_HOLD가 유효하므로 배포 1회 승인이 따로 필요하다.
- 선행 조건 2에 따라 C6b 뒤 배포는 Thomas가 한다. 이 배포는 C6b 전에 하므로 지금처럼 deploy 스킬로 한다.
- 검증은 §4.14.3 O-1–O-9다.
  - O-2·O-6(Claude 계정에서 연결)은 C6a로 `claude` 계정이 생겨 지금도 할 수 있다. `setpriv`로 2101이 되어 시험한다(C4 하네스와 같은 방식).
  - O-4의 배너는 `reads=[2개]`로 기대한다. §4.14.3의 "6개"는 2026-10-15 이전 기준이다.

## 3. 결정할 것

**RD-1. C6b 뒤 Claude가 읽는 범위.** 지금 Claude가 `docker exec`로 읽는 것은 holdings 집계 보드, `live_readiness`, 실행 단계 등이다. 이 door로는 일정과 스케줄러 이벤트만 읽는다.
- **A(권고):** 2개로 운영화하고 C6b를 진행한다. holdings·readiness는 C6b 뒤 Thomas가 읽어 전달한다(D-9 "읽기는 read-bridge에 있는 것만").
  - 보류 4개는 각각 응답 계약을 구현한 뒤 별도 승인으로 넣는다.
  - 순서는 `crypto_readiness`, `holdings_status`가 먼저다.
  - `holdings_status` 계약은 신선도만 담는다(금액·비중 없음, §4.19.2). 그래서 계약이 들어가도 집계 보드의 숫자는 Claude에게 오지 않는다.
- B: 보류 4개 중 `crypto_readiness`·`holdings_status`의 계약을 먼저 구현하고 넣은 뒤 C6b를 한다. C6b가 코드 PR 하나와 그 배포만큼 늦어진다.

**RD-2. 소켓·로그 위치.**
- **A(권고):** §2.2의 `/var/lib/thomas-claude-door/{sock,log}`. 재부팅에도 남고, C6a의 `/var/lib/thomas-deploy`와 나란하다.
- B: `/run/thomas-claude-door/`. tmpfs라 부팅마다 만들어야 한다(tmpfiles.d 추가).

**RD-3. 요청 로그 보존(R-08).**
- **A(권고):** 이번 범위에서는 정하지 않는다. 크기만 C6b 시험(b8) 때 확인한다. 응답 한 줄은 수십 바이트이고, 호출은 Claude 세션이 할 때만 생긴다.
- B: 회전을 지금 넣는다. root cron 한 줄이 추가된다(C6b cron 분류 K-4 쪽).

**RD-4. 진행 단위.**
- **A(권고):** ① 코드 PR(클라이언트, compose 서비스, 시험; 병합은 승인) → ② 호스트 디렉터리(Thomas) → ③ 배포 1회(승인, deploy 스킬) → ④ O-1–O-9 검증. 각 단계를 따로 승인한다.
- B: ①–④를 승인 하나로 묶는다.

## 4. 이 문서가 하지 않은 것

- compose·코드·시험을 바꾸지 않았다.
- 호스트 디렉터리를 만들지 않았고, 배포하지 않았다.
- 보류 읽기 4개의 계약 구현은 RD-1 뒤의 일이다.
- 진단 파일 격리와 무응답 연결의 감사 공백(§4.19.4)은 손대지 않았다.
