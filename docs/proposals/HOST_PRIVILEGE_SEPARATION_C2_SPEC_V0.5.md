# 기록: Option C Phase C2 승인 검증 인터페이스 명세 v0.5 (C2-FINAL 확정본)

**상태:** DECIDED 2026-10-16 — C2 설계 완료(`승인: C2-FINAL C2 설계 완료 확정, DESIGN_ONLY (Thomas 2026-10-16)`). 승인 결속 필드와 reason code는 닫혔다. 구현은 C3a로 나눠 진행 중이다: C3a-1 #1227·C3a-2 #1228은 MERGED, C3a-3 #1229는 병합 승인 대기다. 설치·계정 분리·운영(C4–C8)은 하나도 승인되지 않았다. 확정 뒤의 결정(D-10a, CR-12 T3 조건부, TYPE-B-KEY K1, D-3 C3a 범위, CR-8)과 구현 중 드러난 차이는 `HOST_PRIVILEGE_SEPARATION_V0.1.md` §4.25에 있다. **C2 개정 1건(Thomas 2026-10-17):** 응답 code `COMPOSE_PROJECT_MISMATCH`(§6.3-7 라벨 불일치)를 추가한다. 같은 날 정의된 actor 응답 `state` 값도 §4.25.3에 있다. 아래 본문은 바꾸지 않았다.

**이 파일에 대하여:**
- 아래 본문은 Thomas가 C2-FINAL로 확정한 scratchpad 원문(`C2_SPEC_V0.5.md`, md5 `5d096745`)을 **한 글자도 바꾸지 않고** 옮긴 것이다(CR-16).
- 본문 안의 "scratchpad 초안", "IMPLEMENTATION_NOT_AUTHORIZED", "D-10·CR-12 DECISION_DEFERRED" 같은 표현은 확정 당시 상태다. 이후 상태는 위 상태 줄과 §4.25가 정본이다.
- 본문이 가리키는 검토 자료(`C2_V0.5_SECURITY_REVIEW.md`, `C2_V0.5_COMPLETION_GATE.md`, v0.1–v0.4)는 저장소에 옮기지 않았다. 바꿀 수 없는 계약은 이 본문과 코드(`runtime/mvp_runtime/deploy_runner/reasons.py`, `schemas/deploy_*.schema.json`)에 있다.
- 바꾸려면 새 C2 개정 결정이 필요하다.

---

# Option C — Phase C2 승인 검증 인터페이스 상세 설계 v0.5 (scratchpad 초안, N1 + GAP 해결)

- 작성 2026-10-16. 기준 main `01d8598b30416a6220dfa5056c9f4c12439f399f`(변경 없음).
- 근거 승인:
  - D-1 대안 C·D-2 R2(DESIGN_ONLY), D-3(C2 명세 범위), D-4(DESIGN_APPROVED), CR-1–CR-5
  - v0.3 조건부 위임 결정(CR-6–CR-20)
  - **Thomas 명시 승인 2026-10-16: N1-1–N1-8 DESIGN_ONLY**(아래)
- v0.1–v0.4와 검토 자료는 보존했다. 이 파일은 v0.4를 바탕으로 GAP-1·GAP-2 해결만 반영한 단독 문서다.
- 검토 기록: `C2_V0.5_SECURITY_REVIEW.md`. 완료 판정: `C2_V0.5_COMPLETION_GATE.md`. (v0.4 결정표는 그대로 유효)
- 모든 명세: **DESIGN_DRAFT · IMPLEMENTATION_NOT_AUTHORIZED · OPERATION_NOT_AUTHORIZED.** 보류 결정은 DECISION_DEFERRED로 적는다.
- 근거 등급: [CODE] main 코드 / [DOC] 설계·운영 문서 / [MAN] 호스트 `man ssh-keygen` / [CI] 이 세션의 CI 기록 / [PROPOSAL] 제안 / [추론] 원문 밖 분석.

### N1 승인 범위 (Thomas 2026-10-16, DESIGN_ONLY)
| ID | 승인 내용 | 미승인으로 남는 것 |
|---|---|---|
| N1-1 | D-1 대안 C 유지. G/X 분리 = 대안 C 내부의 보안 경계 변경 | D-1 운영 권한 확대 |
| N1-2 | G: Docker·Secret·최종 판정 없음. X: 최종 판정, 고정 작업, root 동등 위험 유지 | **X 침해 위험의 운영상 수용** |
| N1-3 | G = 필터. X가 서명·대상·결속·권한·철회를 독립 검증. 최종 판정은 X에만 | — |
| N1-4 | 소비·멱등성·효과 감사·UNKNOWN·LATCH = X 단일 소유. G = 접수 기록만(외부 감사 증거와 동일 취급 금지) | — |
| N1-5 | genesis·`store_id`·소비 해시 체인·감사 교차 대조·자동 복구 금지·손실·불일치 시 STARTUP_BLOCKED·UNKNOWN 재실행 금지 | 해시 체인이 root 침해를 막거나 독립 탐지한다는 주장 |
| N1-6 | CR-17 우선 후보 = RECOVERY_INBOX | 문서 스키마 확정, 수신함 위치, 소유권·권한, 검증 조건 확정, 운영 절차, LATCH 해제 구현 |
| N1-7 | N2 정적 조사·설계 비교 | Docker 실행, 데몬 접근, 호스트 설정 조회, 실제 API 검증, 권한 변경 |
| N1-8 | 이 v0.4를 scratchpad에 작성 | 저장소 반영, C3, 계정 분리, Docker, 운영 |

### v0.5 승인과 범위
- **근거:** `승인: GAP-1A~1C, GAP-2A~2C, C2-COMPLETE DESIGN_ONLY (Thomas 2026-10-16)`.
- **반영한 것:**
  - GAP-1A: 복구 문서를 두 유형으로 분리(§1.7, §2.8, §3.5, §7.6)
  - GAP-1B: 소비 이력을 독립 증거로 확인할 수 없으면 STARTUP_BLOCKED(§3.5)
  - GAP-1C: RECOVERY_INBOX 계약을 설계 후보로(§7.6)
  - GAP-2A–C: G/X 기록 독립 보관, 운영자 사후 대조, X는 G 기록을 읽지 않음(§7.3, §7.9)
  - C2-COMPLETE: 설계 문서 §6 기준 판정(`C2_V0.5_COMPLETION_GATE.md`)
- **v0.5 정리(§5.2):** 응답 code를 통합했다. LATCH 응답은 `RUNNER_LATCHED` 하나, 해제 문서 오류는 `RECOVERY_DOCUMENT_INVALID` 하나다. 응답 code와 감사 전용 code도 구분했다.
- **미승인:** 복구 구현, 실제 복구 문서, 재측정, 재초기화, LATCH 해제, 수신함 위치·소유권·권한·운영 절차, Docker, 운영 권한. D-10·CR-12·X 침해 위험 수용은 보류.

### 결정 상태 (v0.4와 같음, v0.5에서 바뀐 결정 없음)
| ID | 상태 |
|---|---|
| N1 | **DESIGN_ONLY_APPROVED**(위 범위) |
| D-10 | DECISION_DEFERRED(G_ACCOUNT / X_ACCOUNT로 재정의, 부속 B) |
| CR-12 | DECISION_DEFERRED(X 기준 재평가, 부속 B) |
| CR-17 | DECISION_DEFERRED(RECOVERY_INBOX 우선 후보, §7.6) |
| CR-6 | DESIGN_DECISION_ACCEPTED(조건부) — 모델 A를 X에서 수행(§6.7) |
| CR-7, CR-8, CR-9, CR-14, CR-16, CR-18, CR-19, CR-20 | DESIGN_DECISION_ACCEPTED(v0.3과 같음) |
| CR-10, CR-11, CR-13, CR-15 | DECISION_DEFERRED(v0.3과 같음) |
| N2 | 정적 조사만(부속 C). 차단 효과 NOT_VERIFIED |

## 0. 전제와 한계 (모든 명세에 적용)

1. **R2의 의미 (보완 ①).** R2 채택은 승인 증거를 암호학적으로 검증하는 방식을 고른 것이다. 다음은 R2가 주지 않으며 각각 별도 검증 대상이다:
   - 검증기 우회 방지
   - 공개키 신뢰 목록 보호
   - 실행기 변조 방지
   - 운영 권한 분리
2. **현재 상태.** Claude는 root이고 Docker를 쓸 수 있다. 이 설계의 어떤 보장도 지금은 성립하지 않는다. **VERIFIER_BYPASS_PROTECTION = NOT_VERIFIED** (C6 전까지).
3. **root 경계.** root(그리고 docker 그룹, E1이면 실행기 자신)는 아래 모든 파일·검증·감사를 바꿀 수 있다. 이 설계는 그것을 막지 않는다. 탐지 수단은 호스트 밖 증거뿐이다.
4. **Option A.** `validate_candidate`·`promote`·`rollback`은 OPERATIONALLY_BLOCKED다.
5. **단계 용어 (v0.2 신설).** 각 동작·구성요소의 상태를 다음 다섯 칸으로 따로 적는다. 앞 칸이 허용됐다고 뒤 칸이 허용된 것은 아니다.

| 칸 | 의미 | 지금 |
|---|---|---|
| DESIGN_CANDIDATE | 설계 후보로 다룸 | 허용(CR-5) |
| SYNTHETIC_DESIGN_REVIEW | 합성 입력으로 설계를 검토 | 허용(문서상 검토만, 시험 실행은 C3) |
| IMPLEMENTATION | 코드 작성 | 미승인 |
| DOCKER_EXECUTION | 실제 Docker 호출 | 미승인 |
| OPERATIONAL_ACTIVATION | 운영 활성화 | BLOCKED |

6. **해결 표시 기준.**
   - `RESOLVED_IN_DESIGN`은 "명세 사이의 모순이나 누락을 문서 안에서 해소했다"는 뜻이다. 운영 보장이 아니다.
   - 운영에서만 확인되는 것은 NOT_VERIFIED, 결정이 필요한 것은 DECISION_DEFERRED(v0.2의 DECISION_REQUIRED를 v0.3 용어로 바꿈), 선행 조건이 없거나 보안 요구를 못 채우는 것은 BLOCKED로 둔다.

7. **실행기 import 경계: NOT_VERIFIED.** 실행기가 `runtime.mvp_runtime` 모듈을 import하면 그 기본값(`_repo_root()`, `LedgerStore.default` 등)이 상태 루트를 가리킨다 [CODE]. 실행기 import 그래프가 상태 루트와 `.env`를 건드리지 않는다는 것은 C3에서 보여야 한다(N-13, C2X-18).

8. **N1의 한계 (Thomas 추가 보안 조건 1–10).**
   - N1은 G 침해의 피해 범위를 줄인다. **X 침해는 막지 못한다.** X의 Docker 접근은 root 동등 위험을 유지한다. 근거: [DOC `docs/DEPLOYMENT.md:574-580`] docker 소켓 + docker 그룹을 가진 hermes 컨테이너가 "equivalent to host root"였고 `docker exec`로 실주문 키에 닿을 수 있었다는 기록.
   - X의 독립 서명 검증은 X 자체 침해를 막지 않는다.
   - genesis·해시 체인은 로컬 저장소 전체 재작성에 대한 독립 방어 증거가 아니다.
   - 운영에서 X 침해 위험을 수용한다는 결정은 없다.
9. **용어.** v0.3에서 "실행기"라고 쓴 곳은 v0.4에서 G+X 전체를 뜻한다. 그중 **판정·소비·효과·감사·LATCH는 모두 X**다. 아래 재설계 절(명세 03·06·07)은 G와 X를 명시한다.

## 명세 0N. N1 구조 — G(문지기)·X(행위자) (v0.4 신설)

```
Claude (비특권)
  │ runner.sock  — uid 허용 목록 {claude}(리터럴), G가 listen
  ▼
G  — G_ACCOUNT(비특권). docker 그룹 밖. Secret 없음. 판정 권한 없음
  │ actor.sock   — uid 허용 목록 {G_ACCOUNT}(리터럴), X가 listen. 디렉터리 0750, group = G 그룹
  ▼
X  — X_ACCOUNT(D-10 미결정). Docker 접근(= root 동등). `.env`는 validate·promote·rollback에서만
  │ 고정 argv
  ▼
Docker 데몬
```

| # | 항목 | G | X |
|---|---|---|---|
| 1 | 책임 | 요청 수신, 1차 스키마·크기 검사, 1차 서명 검증(빠른 거부), 접수 기록, X 응답 전달 | 최종 판정, 결속 실측, 철회, 소비, 멱등성, 효과, 효과 감사, LATCH |
| 2 | 계정·권한 | G_ACCOUNT: 비특권, docker·sudo·adm 밖, 상태 루트·`/root` 접근 없음 | X_ACCOUNT: Docker 접근(정의상), X 디렉터리 소유. 구성은 D-10 |
| 3 | 소켓 방향 | Claude → G(runner.sock), G → X(actor.sock). **역방향 없음**(X는 G에 연결하지 않음) | actor.sock만 listen |
| 4 | 요청·응답 형식 | Claude 쪽: 명세 6.1 프레임. X 쪽: `thomas.actor_request.v1` | `thomas.actor_reply.v1` |
| 5 | 승인 증거 전달 | 원본 base64를 **재직렬화하지 않고** 그대로 X에 전달 | 원본 바이트로 검증 |
| 6 | 서명 검증 위치 | 1차(필터) | **판정**(명세 2.7) |
| 7 | 승인 소비 소유 | 없음 | 단일 소유(명세 3) |
| 8 | 감사 소유 | G 접수 기록(G 체인) | X 효과 감사(정본, 명세 7) |
| 9 | 멱등성 | 없음(전달만) | 권한 있는 기록(명세 6.7) |
| 10 | LATCH | X 상태를 전달만 | 소유 |
| 11 | Docker | **없음** | 고정 argv |
| 12 | Secret | **없음** | `.env`(validate·promote·rollback 순간만, 설계) |
| 13 | 실패 처리 | 접수 기록 fsync 실패 → 전달하지 않고 거부. X 연결 실패 → `ACTOR_UNAVAILABLE`. X 응답 스키마 위반 → `ACTOR_REPLY_INVALID` | 명세 3.3 CASE A–H |
| 14 | 재시작 복구 | 상태 없음(접수 기록만). 재시작 뒤 X에 재전송으로 결과 회수 | 시작 대조(명세 7.3) 완료 전 listen 금지 |
| 15 | 침해 시 | **G 침해:** 서명 없는 운영 동작 불가(X 자기 검증). 가능: `inspect_candidate` 호출, Claude에게 거짓 응답, 접수 기록 조작. **X 침해:** root 동등(차단 불가) | — |

**`inspect_candidate`의 별도 경계 (D-4):**
- 무승인 조회이므로 G 침해자(또는 G_ACCOUNT를 얻은 자)도 X에 inspect를 요청할 수 있다.
- X의 inspect 경로는 다음을 한다:
  - `.env`를 읽지 않는다.
  - 소비·서명 코드를 거치지 않는다.
  - 허용 필드만 추출한다(D-4 10조건).
- 속도 제한은 X에서 한다(값 DECISION_DEFERRED).
- LATCH 중에는 inspect도 거부한다(CR-18).
- 노출 범위는 이미지 id·태그·컨테이너 이름·health다. 이것을 G 침해 시 노출 가능한 정보로 명시한다.

## 명세 1. 승인 문서 형식 `thomas.deploy_approval.v1`

### 1.1 필드 (닫힌 스키마, `additionalProperties: false`, 모든 문자열은 ASCII)

| 필드 | 형식 | 의미 |
|---|---|---|
| `schema` | 상수 `"thomas.deploy_approval.v1"` | 형식 버전 |
| `approval_id` | `^dap_[a-z2-7]{26}\Z` | 사람이 부르는 승인 식별자. 철회·감사·소비 키 |
| `nonce` | `^[0-9a-f]{32}\Z` (128비트 난수) | 문서마다 다른 값. 같은 내용의 재서명도 다른 문서가 됨 |
| `action` | enum: `prepare_candidate` / `validate_candidate` / `promote` / `rollback` | `inspect_candidate`는 승인 문서를 받지 않음(D-4) |
| `environment` | 객체(아래) | 승인 대상 환경 |
| `target` | 객체(아래) | 승인 대상 코드·이미지 |
| `issued_at` | `^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z\Z` | 발급 시각(UTC) |
| `expires_at` | 같음 | 만료 시각(UTC) |
| `signature_namespace` | 상수(§2.2) | 서명 namespace를 문서 안에도 결속 |

`environment` (Secret 경로·값 없음):

| 필드 | 형식 | 비고 |
|---|---|---|
| `project` | 상수 `"thomas_agent"` | compose 프로젝트 이름 |
| `host_label` | `^[a-z0-9-]{1,32}\Z` | 실행기 설정(root 소유)에 적힌 호스트 라벨. 호스트 이름·IP가 아니라 Thomas가 정한 라벨 |
| `profile` | enum `production` | env 파일 경로는 문서에 넣지 않는다. 실행기가 `profile`→경로를 자기 설정에서 찾는다 |

`target`:

| 필드 | 형식 |
|---|---|
| `pr` | 정수 1–999999 |
| `git_commit_sha` | `^[0-9a-f]{40}\Z` |
| `candidate_tag` | `candidate-<pr>`와 정확히 같아야 함 |
| `candidate_image_id` | `^sha256:[0-9a-f]{64}\Z` 또는 `null` |
| `rollback_tag` | `rollback-pre-<pr>`와 정확히 같아야 함 |
| `rollback_image_id` | `^sha256:[0-9a-f]{64}\Z` |

동작별 `target` 필수값:

| action | `candidate_image_id` | `rollback_image_id`의 의미 |
|---|---|---|
| `prepare_candidate` | `null`(빌드 전) | 지금 실행 중인 이미지 = 만들 rollback 지점 |
| `validate_candidate` | 필수(빌드 결과) | 만든 rollback 지점 |
| `promote` | 필수 | 만든 rollback 지점 |
| `rollback` | 필수 = 지금 실행 중인(되돌릴) 이미지 | 되돌아갈 이미지 |

- 필드 이름은 kernel `integrity.scan_for_secret_bearing_keys`의 금지 키(`token`, `secret`, `*_token` 등)와 겹치지 않는다 [CODE].

### 1.2 정규화와 서명 대상 바이트
- **서명 대상 = 문서의 정규 바이트 그대로.**
  - kernel `integrity.canonical_bytes`와 같은 규칙이다: `sort_keys`, 구분자 `(",", ":")`, `allow_nan=False`, float 금지 [CODE].
  - 모든 값이 ASCII이므로 `ensure_ascii` 여부와 관계없이 바이트가 같다.
  - 끝에 줄바꿈이 없다.
- **검증기는 받은 원본 바이트로 서명을 검증한다.** 그다음 파싱하고 다시 정규화해서 **원본 바이트와 바이트 단위로 같은지** 확인한다. 다르면 `APPROVAL_NOT_CANONICAL`이다.
  - 이렇게 하면 파서마다 해석이 다른 문서(중복 키, 공백, 다른 숫자 표기)가 서명은 통과하고 다르게 해석되는 경로가 닫힌다.
- `approval_sha256` = `integrity.sha256_bytes`(정규 문서 바이트) [CODE 함수]. 서명은 포함하지 않는다. 소비·감사·철회 대조에 쓴다.
- 크기 상한: 문서 2,048바이트, 서명 4,096바이트. 전체 프레임은 `socket_door.MAX_FRAME_BYTES` 8,192 안에 든다 [CODE].

### 1.3 생성·검증 주체

| 역할 | 주체 | 위치 |
|---|---|---|
| 문서 작성 | Thomas의 Mac 도구(C3 산출물). Thomas가 GitHub에서 SHA를 직접 확인한 뒤 작성 | Mac |
| 서명 | Thomas: `ssh-keygen -Y sign -f <키> -n <namespace> <문서>` [MAN] | Mac. 개인키는 호스트에 오지 않음 |
| 전달 | **CR-9 DESIGN_DECISION_ACCEPTED(§1.3a): (i) 채택.** (i) 요청 프레임에 실려 옴 — Claude가 전달해도 된다. 신뢰는 서명에서 나옴. (ii) Thomas가 자기 SSH로 root 소유 수신함(예: `/var/lib/thomas-deploy/inbox/<approval_id>`)에 넣고, 요청은 `approval_id`만 가리킴 — Claude가 승인 경로에 없음. 제안: (i). 이유: 서명이 위조·변조를 막고, (ii)는 Thomas의 호스트 작업을 매번 늘림. (ii)는 Claude가 승인 문서를 보지도 못하게 하려는 경우의 선택지 | — |
| 검증 | 실행기(§2) | 호스트 |

- **Claude가 전달하는 것의 한계:**
  - Claude는 승인을 위조할 수 없다(서명).
  - 보류(전달하지 않기)나 늦은 전달은 할 수 있지만, 만료로 막힌다.
  - 재전송은 할 수 있지만, nonce로 막힌다.
  - 이미지 id는 Claude가 `inspect_candidate` 결과를 Thomas에게 전하는 값이다. Claude가 값을 바꿔 전하면, 실행 시점의 Docker 실측과 맞지 않아 거부된다.

### 1.3a CR-9 승인 전달 경로 — DESIGN_DECISION_ACCEPTED (조건부)
승인 문서와 서명은 요청 프레임으로 전달된다(위 표의 (i)).

| # | 필수 조건 | 명세 위치 |
|---|---|---|
| 1 | 요청 프레임은 비신뢰 입력 | §2.4 순서: 서명 검증 전에는 프레임 내용으로 분기하지 않음 |
| 2 | 전달자에게 승인 권한 없음 | 승인 주체는 서명 키(principal)다. `caller_uid`·`client_id`는 귀속 기록일 뿐 판단에 쓰지 않음 [CODE `socket_door` 주석과 같은 원칙] |
| 3 | 서명 검증 전 효과 금지 | §2.4: 11단계 중 효과는 소비(11) 뒤 |
| 4 | 서명 대상 원본 바이트 보존 | §1.2: base64 엄격 디코드한 원본 바이트로 검증, 그 뒤 정규화 비교 |
| 5 | 요청 크기 상한 | 문서 2,048 / 서명 4,096 / 프레임 8,192바이트 [CODE `MAX_FRAME_BYTES`] |
| 6 | 닫힌 입력 스키마 | §6.1 최상위 키 닫힘, 문서 `additionalProperties: false` |
| 7 | 승인 대상과 요청 대상의 완전한 결속 | §1.1 동작별 필수값 = §6 요청 `args`. 실측값과 3자 비교 |
| 8 | 서명 실패 시 STOP | `SIGNATURE_INVALID`, 소비 없음 |
| 9 | 실패 원문 비노출 | 명세 5: code별 고정 문구, `ssh-keygen` 출력 폐기 |
| 10 | 재사용 방지 | 명세 3: `approval_id`·`nonce` 소비 |

- **판정:** 여섯 검토 조건을 모두 충족한다(검토 기록 §2).
- **남은 위험:** Claude는 승인을 보류·지연·재전송할 수 있다. 보류는 가용성 문제이고, 지연은 만료로, 재전송은 nonce로 막힌다. 또한 Claude가 승인 문서 내용을 볼 수 있다(문서에 Secret 없음).

### 1.4 거부 조건 (모두 STOP, 소비 없음)

| 조건 | reason_code |
|---|---|
| 필드 누락 | `APPROVAL_FIELD_MISSING` |
| 알 수 없는 필드 | `APPROVAL_UNKNOWN_FIELD` |
| 형식 위반(정규식·enum·타입) | `APPROVAL_MALFORMED` |
| 지원하지 않는 `schema` | `APPROVAL_SCHEMA_UNSUPPORTED` |
| 비정규 바이트 | `APPROVAL_NOT_CANONICAL` |
| 서명 오류·변조·허용되지 않은 서명자 | `SIGNATURE_INVALID` |
| 만료 / 아직 유효하지 않음 / 수명 초과 | `APPROVAL_EXPIRED` / `APPROVAL_NOT_YET_VALID` / `APPROVAL_LIFETIME_EXCEEDS_POLICY` |
| 요청 동작과 다름 | `APPROVAL_ACTION_MISMATCH` |
| 환경 불일치 | `APPROVAL_ENVIRONMENT_MISMATCH` |
| SHA·태그·이미지 id 불일치 | `APPROVAL_BINDING_MISMATCH` |

**DECISION_DEFERRED:** 최대 수명·시계 허용 오차는 §1.5(CR-10 PROPOSED_DEFAULT 15분 — `GOVERNANCE_POLICY.yaml`의 `RELEASE_DEPLOYMENT: 15`와 맞춤 [CODE], CR-11 PROPOSED_DEFAULT 60초). `host_label` 값.

---

### 1.5 시각 계약 (CR-10·CR-11 — DECISION_DEFERRED)
**시각 정의:**

| 시각 | 출처 | 쓰임 |
|---|---|---|
| `issued_at` / `expires_at` | 서명된 문서(Thomas의 Mac 시계) | 유효 구간 |
| 검증 시각 `t_verify` | 실행기 wall clock | 2.4-7 |
| 효과 시작 시각 `t_start` | 실행기 wall clock | 시작 허용 판정 |
| 마감 | 실행기 **monotonic** clock | 동작별 실행 시간 상한(§3.7) |

**수명 규칙 (PROPOSED_DEFAULT: 최대 수명 15분, DECISION_DEFERRED):**
1. 만료된 승인은 새로운 외부 효과를 시작하지 않는다. `t_verify < expires_at`와 효과 직전 `t_start < expires_at` 둘 다 필요하다.
2. **시작 조건 = 남은 유효 시간 ≥ 동작 마감 시간.**
   - `t_start + deadline(action) ≤ expires_at`이 아니면 시작하지 않는다(`APPROVAL_TOO_SHORT_FOR_ACTION`, 소비 전 판정 → CASE A).
   - 이렇게 하면 "효과 도중 만료"가 생기지 않는다. 한 동작은 처음부터 끝까지 유효 구간 안에서 실행된다.
   - 대가: 최대 수명은 가장 긴 동작의 마감 시간보다 길어야 한다. 그래서 15분은 마감 시간(CR-19 값)이 정해진 뒤 다시 본다.
3. 한 동작 안의 여러 Docker 단계(예: promote의 태그 → compose up → 사후 확인)는 하나의 효과 단위다. 단계 사이에 만료를 다시 검사해 중간에 끊지 않는다. 반쪽 적용 상태를 만들지 않기 위해서다. 단, 마감 초과(monotonic)는 CASE C다.
4. 긴 작업이 끝나는 시각은 수명과 관계없다. 위 규칙 2로 마감 안에 끝나거나 CASE C가 된다.
5. 재시작: 소비된 승인은 재시작 뒤 다시 실행되지 않는다(UNKNOWN, 명세 3). 소비 전에 재시작하면 처음부터 다시 검증한다(만료 재판정).

**시계 신뢰 규칙 (PROPOSED_DEFAULT: 발급 시각 허용 오차 60초, 만료 오차 0, DECISION_DEFERRED):**
1. **역행 탐지:** 실행기는 `last_seen_wall`을 자기 상태 파일에 fsync로 남긴다. `now_wall < last_seen_wall − tolerance`이면 `CLOCK_UNTRUSTED` → 새 효과 시작 금지.
2. **점프 탐지:** 시작할 때 (wall, monotonic) 쌍을 기록한다. 매 판정에서 |Δwall − Δmono| > threshold면 `CLOCK_UNTRUSTED`.
3. **동기화 상태:** 시계 동기화가 확인되지 않으면 `CLOCK_UNTRUSTED`. 동기화 상태를 읽을 출처(예: systemd-timesyncd 표지)는 호스트 설정이라 NOT_VERIFIED이고, C3 전 결정 대상이다.
4. **시계와 무관한 방벽:** 재사용 방지는 시계와 무관하다(nonce 소비). 시계가 틀어져도 같은 승인은 두 번 쓰이지 않는다. 시계 오류가 여는 것은 "만료된 승인의 첫 사용"뿐이고, 1–3이 그것을 막는다.
5. **재시작 후:** `last_seen_wall`과 비교하는 1번 규칙을 시작 대조(§7.3)에 넣는다.
6. **한계:** 호스트 시계는 root가 바꿀 수 있다. root에 대해서는 막지 못한다.

### 1.6 N1 확장 (v0.4)
- 승인 문서 `thomas.deploy_approval.v1`의 필드와 정규화(1.1–1.2)는 **바뀌지 않는다.**
- **검증 주체 변경(1.3):** 검증은 X가 판정한다. G의 1차 검증은 필터다. G의 성공은 X의 실행 허가 근거가 아니다.
- **전달 경로(1.3a CR-9):** Claude → G → X. G는 `approval_document_b64`·`approval_signature`를 **바이트 그대로** 옮긴다(디코드·재인코드·정규화 금지). X는 G가 바꿨는지와 관계없이 자기 검증으로 판정한다.
- **새 문서(설계 후보, DECISION_DEFERRED):** `thomas.latch_resolution.v1`(명세 7.6). 승인 문서와 namespace가 다르다. 스키마 확정은 D-3 범위 결정 대상이다.
- **봉투:** G↔X 메시지 봉투는 명세 6.1b에 있다. 승인 문서 자체에는 포함되지 않는다.

### 1.7 복구 문서 스키마 (v0.5, GAP-1A) — 닫힌 스키마, ASCII, 정규 바이트 = 서명 대상
두 문서는 승인 문서(1.1)와 같은 정규화 규칙(1.2)을 쓴다. 필드 밖 입력은 `additionalProperties: false`로 거부한다.

**TYPE A `thomas.latch_resolution.v1`**
| 필드 | 형식 | 필수 | 이유 |
|---|---|---|---|
| `schema` | 상수 | 예 | 유형 혼동 방지 |
| `resolution_id` | `^lres_[a-z2-7]{26}\Z` | 예 | 감사·replay 키 |
| `nonce` | `^[0-9a-f]{32}\Z` | 예 | 문서 유일성 |
| `latch_id` | X의 LATCH 식별자 형식 | 예 | 대상 LATCH 결속 |
| `store_id` | `^[0-9a-f]{32}\Z` | 예 | 현재(정상) 소비 저장소 결속 |
| `decision` | enum `EFFECT_CONFIRMED_COMPLETE` / `EFFECT_CONFIRMED_ABSENT` / `EFFECT_CONFIRMED_PARTIAL` | 예 | 판단 |
| `evidence` | `{measured_images: {<서비스 enum>: image_id}, tags: {latest, candidate, rollback_pre: image_id\|null}, measured_at, operator_procedure_ref}` | 예 | X 재측정과 대조. `operator_procedure_ref`(`^[A-Za-z0-9._-]{1,64}\Z`)는 기록용이고 판정에 쓰지 않음 |
| `issued_at`, `expires_at` | UTC 초 | 예 | 유효 구간 |
| `signature_namespace` | 상수 `latch-resolution.v1@thomas-agent` | 예 | 서명 용도 결속 |
- **금지:** `new_store_id`, `cutoff_at`, `approval_*`, 경로, 자유 텍스트.

**TYPE B `thomas.store_reinitialization.v1`**
| 필드 | 형식 | 필수 | 이유 |
|---|---|---|---|
| `schema` | 상수 | 예 | 유형 혼동 방지 |
| `reinit_id` | `^srei_[a-z2-7]{26}\Z` | 예 | 감사·replay 키 |
| `nonce` | 32 hex | 예 | 문서 유일성 |
| `old_store_id` | 32 hex | 예 | 끊는 저장소 결속 |
| `new_store_id` | 32 hex(≠ old) | 예 | 새 저장소 결속(설치 절차가 만든 genesis와 일치해야 함) |
| `old_store_sha256` | `sha256:` + 64 hex | 예 | 보존된 옛 파일 결속 |
| `audit_tip_seq`, `audit_tip_sha256` | 정수, sha256 | 예 | 이력 재구성 기준점 |
| `external_anchor` | `{anchor_id, tip_seq, tip_sha256, recorded_at}` | 예 | 독립 증거(X1) |
| `imported_consumption_count` | 정수 ≥ 0 | 예 | Thomas가 확인한 이관 행 수. X 계산과 일치해야 함 |
| `cutoff_at` | UTC 초 | 예 | 이 시각 이전 발급 승인 일괄 거부 |
| `issued_at`, `expires_at` | UTC 초 | 예 | 유효 구간(TYPE A 이하) |
| `signature_namespace` | 상수 `store-reinit.v1@thomas-agent` | 예 | 서명 용도 결속 |
- **금지:** `latch_id`, `decision`, `evidence`, 경로, 자유 텍스트.

- **철회:** 처리 전의 복구 문서는 Thomas가 수신함에서 빼는 것으로 철회한다(수신함은 root 소유). 처리한 문서는 replay 규칙이 막는다.

## 명세 2. SSH 서명 검증 계약

### 2.1 호출 (argv만, 셸 없음)
```
ssh-keygen -Y verify -f <allowed_signers 사본> -I <principal> -n <namespace> -s <서명 파일> -r <revoked_keys 사본>
stdin = 문서 원본 바이트
```
- 성공 판정은 **종료 코드 0뿐**이다 [MAN: "Successful verification … signalled by … zero exit status"]. stdout·stderr는 응답·감사에 넣지 않고 버린다.
- 실행 파일은 실행기 설정에 적힌 절대 경로다. 실행기는 `shutil.which`로 찾지 않는다. 시작할 때 그 경로가 root 소유이고 쓰기 불가인지 확인한다.
- 실행 환경: 최소 env(`PATH`·`LANG`만), 고정 cwd, 마감 시간 10초. 시간 초과는 `VERIFIER_UNAVAILABLE`이다.
- 서명 파일과 신뢰 파일 사본은 실행기 `PrivateTmp`에 0600으로 쓰고 끝나면 지운다.

### 2.2 namespace와 principal
- namespace(제안): `deploy-approval.v1@thomas-agent`. man 권고는 `NAMESPACE@YOUR.DOMAIN` 꼴이다 [MAN].
  - 다른 용도의 서명(git 커밋 서명, `file`)을 배포 승인으로 쓰는 것을 막는다.
  - 문서 안의 `signature_namespace`도 같은 값이어야 한다.
- principal(제안): `thomas-deploy-approver`. 실행기 설정의 상수이고, 요청에서 받지 않는다.

### 2.3 허용 서명자 파일 `allowed_signers` (신뢰 목록)
- 위치(제안): `/etc/thomas-deploy/allowed_signers`. root 소유 0644, 상위 디렉터리도 root 소유이고 group·other 쓰기가 없다.
- 형식 제한 (실행기가 시작할 때와 검증할 때마다 검사):
  - 줄마다 `thomas-deploy-approver namespaces="deploy-approval.v1@thomas-agent" valid-after=… [valid-before=…] <키 종류> <공개키>`
  - `namespaces=`가 없는 줄, `cert-authority` 줄, 다른 principal은 `TRUST_CONFIG_INSECURE`로 거부한다(옵션 의미는 [MAN]).
  - 키 종류는 `ssh-ed25519`만 받는다(제안).
- **읽기 방식(T3 대응):**
  - 실행기가 파일을 `O_NOFOLLOW`로 열고 descriptor를 검사한다(root 소유, 일반 파일, 링크 1, group·other 쓰기 없음).
  - 읽은 내용을 PrivateTmp 사본으로 쓰고 `ssh-keygen`에는 그 사본을 넘긴다.
  - 사본의 sha256을 감사에 남긴다(`trust_config_sha256`). 이렇게 해서 검증에 실제로 쓰인 신뢰 목록이 기록된다.
- `revoked_keys`(키 폐기 목록, `-r` [MAN])도 같은 방식으로 다룬다. 파일이 없으면 `TRUST_CONFIG_UNAVAILABLE`이다(fail-closed).
- **NOT_VERIFIED:** `ssh-keygen -Y verify -r`에 빈 파일을 넘겼을 때 정상 동작하는지는 확인하지 않았다(서명 실행 금지). N-8 합성 시험(tmp 합성 키)으로 확인할 항목이다. 빈 파일이 거부되면 '폐기 키 없음'을 나타내는 형식을 따로 정한다.

### 2.4 검증 순서 (앞 단계가 PASS일 때만 다음으로)
1. 프레임: 크기, JSON, envelope, 닫힌 키 → 전송 계열 code
2. 호출자 uid(`SO_PEERCRED`, unit 리터럴 허용 목록) → `PEER_NOT_PERMITTED`
3. `approval_document`(base64 엄격 디코드)·`approval_signature`(armored) 크기·형식 → `APPROVAL_MALFORMED`
4. 신뢰 파일 열기·검사·사본 → `TRUST_CONFIG_*`
5. `ssh-keygen -Y verify` → `SIGNATURE_INVALID` / `VERIFIER_UNAVAILABLE`
6. 파싱, 닫힌 스키마, 정규 바이트 일치 → §1.4
7. 의미 검사: `action` = 요청 verb, `environment` = 실행기 설정, 시각·수명 → §1.4
8. 철회 목록 → `APPROVAL_REVOKED` / `REVOCATION_LIST_UNAVAILABLE`
9. 소비 기록 조회 → `APPROVAL_ALREADY_CONSUMED` / `CONSUMPTION_STORE_UNAVAILABLE`
10. 결속 실측(자기 clone HEAD, Docker 이미지 id) → `APPROVAL_BINDING_MISMATCH` / 측정 계열 code
11. 소비(명세 3) → 그다음에만 효과

- 서명을 의미 검사(7)보다 먼저 검증한다. 서명되지 않은 내용으로는 분기하지 않는다(메모리 "record data로 보안 검사를 분기하지 않는다"와 같은 원리).

**DECISION_DEFERRED:** namespace·principal 문자열, 키 종류 제한(ed25519만), `allowed_signers` 위치.

---

### 2.5 신뢰 경계의 네 층 (v0.2 신설)

| 층 | 질문 | 무엇이 지키나 | 지금 |
|---|---|---|---|
| A. 서명 위조 저항 | Thomas의 개인키 없이 Thomas의 서명을 만들 수 있나 | 암호(ed25519), 개인키가 Mac에만 있음 | 설계상 강함. 호스트 root도 못 함 |
| B. 신뢰 목록 무결성 | 검증기가 "누구의 서명을 받을지"를 누가 바꿀 수 있나 | `allowed_signers`·`revoked_keys`의 파일 권한 | NOT_VERIFIED |
| C. 검증기 실행 경로 무결성 | 검증 코드·`ssh-keygen`·unit을 누가 바꿀 수 있나 | 설치본·실행 파일·unit의 파일 권한, `python -I` | NOT_VERIFIED |
| D. 검증 우회 | 검증을 거치지 않고 Docker를 부를 수 있나 | Claude가 docker 그룹·sudo·root 밖 | NOT_VERIFIED(지금은 불성립) |

**신뢰 목록 교체 공격 (명시).**
- 공격자가 `allowed_signers`에 쓸 수 있으면, 자기 공개키를 principal `thomas-deploy-approver`로 등록하고 자기 키로 서명한 문서를 낼 수 있다. 검증기는 그 문서를 PASS로 판정한다.
- 이것은 Thomas의 서명을 위조한 것이 아니므로 A는 깨지지 않는다. 그러나 결과는 무승인 실행이다.
- 따라서 **A가 강해도 B가 무너지면 승인 체계 전체가 무너진다.** R2의 보장은 B·C·D가 성립할 때만 의미가 있다.

**10개 검토 항목:**

| # | 항목 | 설계 |
|---|---|---|
| 1 | 소유권 | `allowed_signers`·`revoked_keys`·`revoked_approvals`: root 소유 0644, 상위 디렉터리 root 0755. 실행기 계정은 읽기만 |
| 2 | 변경 권한 | root만. 실행기 코드에 신뢰 파일 쓰기 경로가 없음(C3 시험: 실행기 모듈에 해당 경로 쓰기 호출 0) |
| 3 | 실행기와의 분리 | E2·E3이면 실행기는 신뢰 파일을 직접 못 바꾼다(단 docker → root 우회 가능). **E1이면 실행기 자신이 바꿀 수 있다** — D-10과 결합된 위험 |
| 4 | 검증 시 고정 | 검증 한 번마다 descriptor로 읽어 사본을 만들고(§2.3), 그 사본으로만 `ssh-keygen`을 부른다. 검증 도중 파일이 바뀌어도 이번 판정에는 영향 없음 |
| 5 | 승인별 해시 | 감사 행마다 `trust_config_sha256`(두 파일 사본의 해시)과 `signer_key_fingerprint`를 남긴다 |
| 6 | Thomas의 독립 대조 | Thomas가 Mac에 신뢰 목록 원본과 그 해시, 자기 키 지문을 보관한다. 감사 사본(또는 외부 앵커, 명세 7)에서 해시·지문을 대조한다 |
| 7 | 키 추가·제거 승인 | v0.3: 기존 T1(사후 탐지만)은 불충분. 대안 T2/T3/T4와 상태는 §2.6(CR-12 DECISION_DEFERRED) |
| 8 | 변경 이력 보호 | 감사의 해시 변화가 이력이다. 감사도 root가 다시 쓸 수 있으므로 외부 앵커 없이는 보호되지 않음 |
| 9 | 검증기 코드 변경 차단 | 설치본 `/opt/thomas-deploy/<sha>/` root 소유, `ssh-keygen` 절대 경로 root 소유 확인, unit root 소유, `python -I`, 고정 cwd. N-4·N-13·N-14 |
| 10 | root 침해 시 | 1–9 모두 무력. 신뢰 목록·검증기·감사를 같이 바꿀 수 있다 |

- **`trust_config_sha256`의 한계:**
  - 사후 비교 증거일 뿐이다. 신뢰 목록 변경을 사전에 막지 않는다.
  - 감사 파일과 신뢰 목록을 같은 공격자(root, E1의 실행기)가 바꿀 수 있으면, 로컬 해시만으로는 변조를 증명할 수 없다. 호스트 밖에 남은 값(Thomas의 Mac 사본, 외부 앵커)과 대조할 때만 증거가 된다.
- **T2의 효과와 한계 [추론]:**
  - 신뢰 파일에만 쓸 수 있고 설치본에는 못 쓰는 부분 침해(설정 관리 실수 등)를 막는다.
  - root나 E1 실행기는 설치본도 바꿀 수 있어 막지 못한다.
  - 대가: 첫 키 등록 절차와 키 분실 시 복구 절차가 복잡해진다.

### 2.6 CR-12 신뢰 목록 보호 — DECISION_DEFERRED
- **기존 권고 T1(사후 탐지만)은 불충분하다.** `allowed_signers`를 바꿀 수 있는 공격자는 자기 키를 등록하고 자기 승인을 통과시킨다. T1은 이것을 실행 전에 막지 못한다. `trust_config_sha256`은 사후 비교 증거일 뿐이다.
- 유지하는 것: R2, ed25519(설계 후보), 명시적 신뢰 목록, 키 교체·철회 계약, 신뢰 목록 변경 감사.

**대안:**

| 대안 | 방식 | 실행 전 차단 대상 | 못 막는 것 | 비용 |
|---|---|---|---|---|
| T1 (기존) | root SSH로 교체, 감사 해시로 사후 탐지 | 없음 | 신뢰 목록 쓰기 권한자 전부 | 낮음 |
| T2 서명된 변경 | 새 신뢰 목록에 **현재 신뢰 키**의 서명이 있어야 실행기가 받아들임. 실행기는 마지막으로 받아들인 목록의 해시를 자기 상태에 둠 | `/etc`에만 쓸 수 있는 행위자 | 실행기 상태까지 바꾸는 행위자(root, E1 실행기). 첫 키 등록(신뢰의 시작점)은 서명할 키가 없음 | 중간. 키 분실 시 복구 절차 필요 |
| T3 설치본 고정 앵커 | 허용 서명자(principal + 키 지문)를 **Thomas가 검토한 실행기 설치본**(`/opt/thomas-deploy/<sha>/trust_anchor`)에 둔다. `/etc/…/allowed_signers`가 앵커와 다르면 시작·검증 거부(`TRUST_ANCHOR_MISMATCH`). 키 변경 = 새 설치본 = 별도 Thomas 승인 + Mac에서 설치 해시 대조 | `/etc`에만 쓸 수 있는 행위자. E2·E3이면 실행기 자신도 | root, E1 실행기(설치본도 바꿀 수 있음). 저장소에 앵커를 두면 병합 권한자가 바꿀 수 있음 → 설치 검토에서 앵커 diff를 따로 확인해야 함(D-7) | 중간. 키 교체마다 재설치 |
| T4 = T3 + T2 | 앵커는 설치본에, 변경 이력은 서명된 변경 기록으로 | T3와 같음 + 이력 위조 | root | 높음 |

**10개 요구에 대한 대응 (T3 기준, T4는 +이력):**

| # | 요구 | T3 대응 | 상태 |
|---|---|---|---|
| 1 | 변경 권한 분리 | 신뢰 목록 변경 = 설치본 변경. 설치는 Thomas 작업 | 설계 |
| 2 | 변경에 대한 별도 Thomas 승인 | 설치본 SHA 검토 + 설치 승인 | 설계 |
| 3 | 신뢰 목록·실행기 코드의 변조 저항 검증 | N-4·N-14 + C2X-01/02/13 | NOT_VERIFIED(C4·C6) |
| 4 | 독립 기준과 대조 | Thomas의 Mac에 있는 앵커 원본·설치 해시 | 절차(CR-14 X1과 같은 계열) |
| 5 | 변조 시 실행 차단 | `TRUST_ANCHOR_MISMATCH` → 시작 거부, 검증 거부 | 설계 |
| 6 | 키 추가·제거 이력 보존 | git 이력(앵커가 저장소에 있을 때) + 감사 `RUNNER_START` 행의 앵커 해시. T4면 서명된 변경 기록 | 설계(root 재작성 한계) |
| 7 | 허용 서명자 identity 고정 | principal 상수 + 키 지문 목록 고정 | 설계 |
| 8 | 공격자 키 등록 부정 시험 | C2X-02(기대 결과를 STOP으로 변경), C2X-13 | 시험 후보 |
| 9 | C6 전 우회 방지 | NOT_VERIFIED 유지 | 유지 |
| 10 | root 침해 시 한계 | 모든 대안이 무력 | 명시 |

- **권고(결정 아님):** T3, 이력이 필요하면 T4.
- **D-10과의 결합:** E1이면 실행기가 설치본도 바꿀 수 있어 T3의 "실행기 자신도 막음" 효과가 사라진다.
- **최종 상태:** CR-12 = DECISION_DEFERRED. T1을 고르면 운영 승인 경계는 불충분한 것으로 남는다.

### 2.7 N1: 검증 위치와 신뢰 목록 (v0.4)
| 항목 | G | X |
|---|---|---|
| `ssh-keygen -Y verify` | 1차(선택, 빠른 거부) | **필수, 판정** |
| 신뢰 목록 | G 전용 사본(필터용). **X와 공유하지 않음** | X 전용. 보호 방식(T2/T3/T4)은 CR-12 DECISION_DEFERRED |
| 정규 바이트 비교 | 선택 | 필수 |
| 결속(환경·SHA·이미지 id) | 하지 않음(Docker 없음) | 필수, 실측 |
| 대상 동작·nonce·유효기간·철회·중복 | 형식만 | 필수 |

- G 쪽 신뢰 목록이 바뀌어도 X 판정에 영향이 없다. 그것이 G 침해에 대한 경계다.
- **X가 자기 신뢰 목록이나 검증기를 바꿀 수 있으면 실행 승인 경계는 안전하지 않다.** 이것은 D-10(X_ACCOUNT의 설치본 쓰기 가능 여부)과 CR-12(T 선택)에 달려 있고, 둘 다 DECISION_DEFERRED다.
- 두 번 검증한다고 독립 경계가 둘 생기는 것은 아니다. 경계는 X 검증 하나다.

### 2.8 서명 namespace와 선택 규칙 (v0.5)
| 문서 | namespace | 받을 수 있는 경로 |
|---|---|---|
| 배포 승인 `thomas.deploy_approval.v1` | `deploy-approval.v1@thomas-agent` | 소켓(Claude → G → X) |
| TYPE A | `latch-resolution.v1@thomas-agent` | RECOVERY_INBOX만, 소비 저장소 **정상** + LATCH 있음 |
| TYPE B | `store-reinit.v1@thomas-agent` | RECOVERY_INBOX만, 소비 저장소 **비정상**(STARTUP_BLOCKED) |

- **X는 검증에 쓸 namespace를 문서 내용이 아니라 자기 상태로 고른다:** 소켓이면 배포 승인, 수신함 + 저장소 정상이면 A, 수신함 + 저장소 비정상이면 B.
  - 서명되지 않은 문서 필드(`schema` 등)로 검증 경로를 정하지 않는다.
  - 다른 유형의 문서는 namespace가 맞지 않아 `ssh-keygen -Y verify`에서 실패한다 [MAN].
- 서명이 통과한 뒤에는 `schema`·`signature_namespace` 필드가 기대값과 같은지 다시 확인한다.
- `allowed_signers`의 `namespaces=` 옵션으로 키마다 허용 namespace를 제한할 수 있다 [MAN]. TYPE B 전용 키를 둘지는 CR-12와 함께 DECISION_DEFERRED.

## 명세 3. 승인 소비·멱등성·장애 복구 (v0.4 N1 재설계)

### 3.1 저장소 (모두 X 소유, 상태 루트 밖, G는 쓰기·읽기 모두 불가)
| 파일(제안) | 생성 주체 | 내용 |
|---|---|---|
| `X_DIR/consumed.jsonl` | **설치 절차(Thomas)**. X는 만들지 않음 | 첫 행 `{type: "GENESIS", store_id, install_sha, created_at, seq: 0, prev_sha256: null}`. 이후 행 `{seq, prev_sha256, approval_id, nonce, approval_sha256, action, request_id, consumed_at, row_sha256}` |
| `X_DIR/requests.jsonl` | X | `{request_id, request_sha256, state, recorded_at, result?}`. state: `claimed` → `completed` / `refused_before_consume` / `unknown` / `partial` |
| `X_DIR/LATCH` | X(`O_CREAT|O_EXCL`) | `{latch_id, reason_code, request_id, created_at}` |
| `/etc/thomas-deploy/revoked_approvals` | Thomas(root) | 줄마다 `approval_id` |
| `G_DIR/intake.jsonl` | G | G 접수 기록(명세 7.1b). **소비·판정 정보 없음** |

- `X_DIR`, `G_DIR`의 실제 경로·소유권·모드는 D-10과 함께 정한다(DECISION_DEFERRED).
- `bridge_idempotency`의 TTL 재claim 의미는 재사용하지 않는다(v0.3 유지). X 전용 상태 기계를 둔다.

### 3.1b 소비 저장소 행 유형 (v0.5)
| `type` | 쓰는 때 | 필드(공통 `seq`, `prev_sha256`, `row_sha256`, `type` 외) |
|---|---|---|
| `GENESIS` | 설치 절차 | `store_id`, `install_sha`, `created_at` |
| `APPROVAL` | 배포 승인 소비 | `approval_id`, `nonce`, `approval_sha256`, `action`, `request_id`, `consumed_at` |
| `RESOLUTION` | TYPE A 소비 | `resolution_id`, `nonce`, `document_sha256`, `latch_id`, `consumed_at` |
| `REINIT_GENESIS` | TYPE B 뒤 새 저장소 첫 행(설치 절차가 만듦) | `store_id`(= `new_store_id`), `previous_store_id`, `install_sha`, `created_at` |
| `REINIT` | TYPE B 소비(새 저장소) | `reinit_id`, `nonce`, `document_sha256`, `old_store_id`, `cutoff_at`, `consumed_at` |
| `IMPORTED` | TYPE B 이관(새 저장소) | `approval_id`, `nonce`, `source_audit_seq` |
- 필드 밖 입력은 거부한다. `approval_id`·`nonce` 중복 판정은 `APPROVAL`과 `IMPORTED` 행 전체를 대상으로 한다.

### 3.1a 기록 순서 (X)
1. `requests.jsonl` claim(지문 = X가 자기 메시지에서 계산한 `request_sha256`) → fsync
2. 검증(명세 2.4 1–10, 판정은 X)
   - 실패하면 `refused_before_consume` → fsync.
   - 그 `request_id`는 끝난 것이다. 고친 요청은 **새 id**로 보낸다.
3. 소비 행 추가(체인) → fsync
4. 감사 CONSUMED 행 → fsync
5. 효과(전역 운영 잠금 안)
6. 감사 끝 행 → fsync
7. `requests.jsonl` 끝 상태(결과) → fsync
8. G에 응답

- 6·7 중 하나라도 실패하면 CASE E다.
- G는 1–8 어디에도 관여하지 않는다. G의 접수 기록은 X에 메시지를 보내기 **전에** fsync된다.

### 3.2 소비 일회성 조건 (X)
1. **단일 인스턴스:** X 시작 시 `X_DIR/instance.lock` 배타 `flock`. 실패하면 시작하지 않는다.
2. **확인-추가 원자성:** 소비 파일 fd에 배타 `flock` → 전체 읽기 → 체인 검증 → `approval_id`·`nonce` 확인 → 추가 → fsync → 해제.
3. **로컬 fs:** 네트워크 fs 금지.
4. **읽기 실패는 거부:** 파싱·체인 오류 → `CONSUMPTION_STORE_UNAVAILABLE`, STOP.
5. **모호한 쓰기는 소비로 본다.**
6. **삭제·회전 없음.**
7. **이중 방벽:** 요청 기록이 손실돼도 소비 기록이 막는다. 소비 기록 손실은 §3.4 genesis 규칙으로 STARTUP_BLOCKED.

### 3.3 장애 상황별 계약 (G/X)
| CASE | 상황 | 소비 | X 상태 | 재실행 | 결과 조회 | 감사 | 복구 | 새 승인 |
|---|---|---|---|---|---|---|---|---|
| A | 소비 전 거부(검증·결속·철회·저장소 읽기) | 안 됨 | `refused_before_consume` | 같은 승인·**새 id** 가능(만료 전) | 같은 id 재전송 → 기록된 거부 재생 | X: RECEIVED→REFUSED. G: FORWARDED·REPLY | 없음 | 불필요 |
| B | 소비 후 효과 전 실패 | 됨 | `FAILED_NOT_STARTED`(효과 0 확정) | 같은 승인 불가 | 재전송 → 기록된 거부 | CONSUMED→FAILED | 없음 | 필요 |
| C | 효과 도중 실패·마감 초과 | 됨 | 재측정 성공 → `partial`, 실패 → `unknown` | **불가** | 재전송 → PARTIAL/UNKNOWN | EXECUTING→… | LATCH, §7.6 | 필요(LATCH 해제 후) |
| D | 효과 완료 후 X→G 응답 유실 | 됨 | `completed` | 불가 | 재전송 → 재생 | COMPLETED | 없음 | 불필요 |
| E | 효과 완료 후 X 감사·요청 끝 기록 실패 | 됨 | 기록상 불명확 = `unknown` | 불가 | `RUNNER_LATCHED` | 끝 행 없음 | LATCH(파일 실패 시 X 종료 → 시작 대조가 재LATCH) | 필요 |
| F | X 강제 종료 | 됨(CONSUMED 이후) | 시작 대조 → `unknown` | 불가 | `EFFECT_STATE_UNKNOWN` | `RECONCILED_UNKNOWN` | LATCH | 필요 |
| G | 같은 `request_id` 동시 | 먼저 claim한 쪽만 | 다른 쪽 `REQUEST_IN_FLIGHT` | — | 끝난 뒤 재생 | REFUSED | — | — |
| H | 다른 id로 같은 승인 | 첫 소비만 | `APPROVAL_ALREADY_CONSUMED` | — | — | REFUSED | — | — |
| **N1-a** | G 접수 기록 성공, X 기록 없음 | 안 됨(X claim 전이면 효과 0) | 없음 | 같은 메시지 재전송 → X가 처음 받는 요청으로 처리 | `ACTOR_UNAVAILABLE` 후 재전송 | G: FORWARDED, REPLY 없음 | 없음 | 불필요 |
| **N1-b** | X 효과 시작 후 G 장애 | 됨 | X는 끝까지 실행·기록 | 불가 | 새 G가 같은 메시지 재전송 → X 재생 | X 정상 | 없음 | 불필요 |
| **N1-c** | G·X 기록 불일치(G FORWARDED인데 X 기록 없음 / X에만 있음) | — | **성공으로 추정하지 않음** | 불가 | X 기록이 정본 | X 시작 대조가 G 기록을 **참고**로 대조. 불일치는 경보 + 운영자 판단(LATCH 여부는 X 기록 기준) | §7.6 | — |
| **N1-d** | X 기록 일부 손실 | — | §3.4 STARTUP_BLOCKED | 불가 | — | — | 운영자 절차 + 서명 문서(§3.5) | — |

- 효과가 났을 가능성이 있으면 `unknown` 또는 `partial`이다. 성공으로 바꾸지 않는다.
- LATCH 중에는 새 승인을 포함한 모든 요청을 거부한다(CR-18).

### 3.4 소비 저장소 genesis·체인·교차 대조 (N1-5)
| # | 규칙 |
|---|---|
| 1 | genesis는 설치 절차가 만든다. `store_id` = 128비트 난수 |
| 2 | 파일 없음 / 첫 행이 GENESIS 아님 / `store_id`가 X 감사 `RUNNER_START`의 값과 다름 → `RUNNER_STARTUP_BLOCKED` |
| 3 | 영속: 행마다 fsync, 덧붙이기만 |
| 4 | 체인: `row_sha256 = sha256(정규 행 − row_sha256)`, `prev_sha256` = 직전 `row_sha256` |
| 5·6 | `approval_id`·`nonce` 각각 중복 거부 |
| 7 | 교차 대조(시작 시): 감사 CONSUMED 행 ↔ 소비 행 `seq`·`row_sha256` 일대일. 감사가 참조한 최대 `seq` ≤ 소비 마지막 `seq` |
| 8 | 효과 전 fsync(§3.1a) |
| 9 | 효과 불명확 → LATCH |
| 10 | 자동 재실행 금지 |
| 11 | **자동 재생성 금지:** X는 소비 저장소를 만들거나 고치거나 잘라내지 않는다 |
| 12 | 재시작 전(listen 전) 기록 검증 |
| 13 | 감사 연속성 검증(명세 7.3) |

- **한계 (N1-5 조건):**
  - 소비 저장소와 감사를 같은 행위자(X 침해, root)가 함께 다시 쓰면 로컬 해시로는 탐지할 수 없다.
  - G 접수 기록은 X 계정 수준의 제한 결함에 대해서만 참고가 되고, 외부 감사 증거가 아니다.
  - 외부 앵커(CR-14 X1)와 대조할 때만 증거가 된다.

### 3.5 TYPE B — 소비 저장소 재초기화 (v0.5, GAP-1A·1B. 설계 후보, 구현·실행 미승인)
- **원칙:** 기존 소비 이력을 독립 증거로 확인할 수 없으면 재초기화하지 않는다. **STARTUP_BLOCKED를 유지한다.** 새 genesis만으로는 운영을 재개하지 않는다.

| # | 필수 조건 | 검증 | 실패 시 |
|---|---|---|---|
| 1 | 기존 소비 이력의 신뢰성 | 옛 저장소를 보존하고 그 해시 = `old_store_sha256`. 체인 단절 지점을 기록 | `REINIT_EVIDENCE_INSUFFICIENT` |
| 2 | 감사 정합성 | X 감사 체인 정상, `audit_tip_seq`·`audit_tip_sha256` 일치 | 같음 |
| 3 | 독립 외부 증거 | `external_anchor.tip_sha256` = 감사의 그 `seq` 행 해시(CR-14 X1). 앵커가 없거나 다르면 거부 | 같음 |
| 4 | 이전 승인 재사용 차단 | 감사 CONSUMED 행 전체를 `IMPORTED`로 이관, 개수 = `imported_consumption_count`. 그리고 `issued_at` < `cutoff_at` 승인 일괄 거부(§2.4 7단계 시각 검사에서 판정. 소비 검사보다 먼저) | `REINIT_EVIDENCE_INSUFFICIENT` / 이후 `APPROVAL_BEFORE_CUTOFF` |
| 5 | `store_id`·genesis 결속 | 새 저장소 첫 행 `REINIT_GENESIS.store_id` = `new_store_id`, `previous_store_id` = `old_store_id` | `REINIT_EVIDENCE_INSUFFICIENT` |
| 6 | 미해결 UNKNOWN 없음 | 감사에 해제 행 없는 UNKNOWN·PARTIAL이 없을 것(있으면 먼저 TYPE A — 단 TYPE A는 저장소 정상일 때만 받으므로, 저장소 손실 + 미해결 UNKNOWN이면 **복구 경로 없음 → BLOCKED**) | 같음 |
| 7 | 별도 서명 승인 | namespace `store-reinit.v1@thomas-agent` | `RECOVERY_DOCUMENT_INVALID` |
| 8 | 복구 증거 영속 기록 | 감사 `STORE_REINITIALIZED`(§7.6 #13) fsync 후에만 새 저장소 사용 | 감사 쓰기 실패 → STARTUP_BLOCKED 유지 |
| 9 | 자동 실행 금지 | 재초기화 뒤 어떤 운영 동작도 시작하지 않음. 다음 동작은 새 승인(컷오프 이후 발급분) | — |
| 10 | 실패 시 | 어느 조건이든 실패 → STARTUP_BLOCKED 유지, `RECOVERY_DOCUMENT_REJECTED` 감사 행 | — |
| + | 1회 | 같은 `old_store_id`로 두 번째 TYPE B → 거부 | `REINIT_ALREADY_PERFORMED` |

- **BLOCKED로 남는 경우(명시):** 감사도 손상됐거나, 외부 앵커가 없거나 맞지 않거나, 저장소 손실과 미해결 UNKNOWN이 겹친 경우. 이때는 이 설계 안에서 복구 경로가 없다. 운영자 판단과 새로운 결정이 필요하다.
- **한계:** root·X 침해는 옛 저장소·감사·앵커 사본을 함께 조작할 수 있다. 외부 앵커가 실제로 호스트 밖에 있을 때만 의미가 있다(OPERATIONAL_AUDIT_INTEGRITY = NOT_VERIFIED).

### 3.6 CR-6 조건 대응 (v0.3 유지, X에서 수행)
8개 조건 모두 X에서 충족한다. 근거는 §3.1a, §3.2, §3.3, 명세 6.7이다. "결과는 실행기 전용 영속 상태에서 조회"의 실행기는 X다.

### 3.7 CR-19 마감 시간 원칙 (v0.3 유지)
- 모든 운영 동작은 monotonic 마감 시간을 갖는다. 초과하면 CASE C → LATCH. 자동 재실행은 없다.
- 수명 ≥ 가장 긴 마감(§1.5). 값은 DECISION_DEFERRED.

### 3.8 철회 (v0.3 유지, X)
- X가 검증 시와 효과 직전에 두 번 확인한다.
- 철회 목록이 없거나 읽을 수 없으면 STOP한다.

## 명세 4. 공개키 교체·폐기 계약
- **v0.4(N1):** 키 교체·신뢰 목록 보호는 **X의 신뢰 목록** 기준으로 정한다. G 사본은 필터용이다. CR-12 = DECISION_DEFERRED(부속 B).
- **v0.3:** CR-12 = DECISION_DEFERRED. 아래 절차는 T1 기준이고, T1은 실행 전 차단이 없어 불충분하다(§2.6). T3를 고르면 키 변경 = 새 설치본(별도 Thomas 승인)이 된다.

설계 문서에는 이 절이 없다. 이번에 새로 제안한다.

| 상황 | 절차(모두 Thomas의 root SSH 작업, 실행기는 하지 않음) | 남은 승인의 처리 |
|---|---|---|
| 정기 교체 | ① 새 키 줄 추가(`valid-after`=지금) ② 옛 키 줄에 `valid-before`=교체 시각 + 최대 승인 수명 ③ 그 시각 뒤 옛 줄 삭제 | 옛 키로 서명한 미사용 승인은 옛 줄의 `valid-before`까지만 유효 |
| 키 침해 의심 | ① 옛 공개키를 `revoked_keys`에 추가 ② 옛 줄 삭제 ③ 새 키 추가 | 옛 키 서명 전부 즉시 `SIGNATURE_INVALID` |
| 승인 하나만 무효화 | `revoked_approvals`에 `approval_id` 추가 | 그 승인만 `APPROVAL_REVOKED` |

- **교체 감사:** 실행기는 매 검증의 감사 행에 `trust_config_sha256`(allowed_signers + revoked_keys 사본 해시)를 남긴다. 값이 바뀐 첫 행이 교체 시점이다. Thomas는 Mac에 보관한 신뢰 목록의 해시와 대조한다. 이것이 신뢰 목록 변조의 유일한 탐지 수단이다(root는 파일과 감사를 모두 바꿀 수 있음).
- 실행기는 신뢰 파일을 쓰지 않는다. 쓰기 경로가 코드에 없어야 한다(시험: 실행기 모듈에 신뢰 파일 쓰기 호출이 없음).
- 개인키 분실 시: 새 키를 추가할 때까지 승인을 만들 수 없다(가용성 손실, 안전 방향).

**DECISION_DEFERRED:** 교체 주기(제안: 없음, 사건 기반), 하드웨어 키(`sk-ssh-ed25519`) 사용 여부, 신뢰 목록 해시를 Mac에 보관하는 방법.

---

## 명세 5. reason_code 닫힌 목록 (응답은 code + 코드별 고정 문구만)
- 실행기 응답의 `reason`은 code마다 하나뿐인 고정 문구다. PR-B `PROFILE_ERROR_TEXT` 방식이다 [CODE].
- 기존 `socket_door.internal_error_payload`는 `MvpRuntimeError` 메시지를 원문 그대로 돌려주므로 [CODE], 실행기는 그것을 쓰지 않고 code→문구 표로 다시 만든다.
- 목록에 없는 code는 `RUNNER_ERROR`로 바꾼다. 이때 `error_ref`만 붙이고, 상세는 실행기 로그(Claude 읽기 불가)에 남긴다.

| 군 | code |
|---|---|
| 전송·요청 | `MALFORMED_REQUEST`, `FRAME_TOO_LARGE`, `PEER_NOT_PERMITTED`, `CREDENTIALS_UNAVAILABLE`, `VERB_NOT_PERMITTED`, `ARGUMENT_NOT_ACCEPTED`, `ARGUMENT_INVALID`, `REQUEST_ID_REQUIRED`, `REQUEST_ID_REUSED`, `REQUEST_IN_FLIGHT`, `RUNNER_BUSY` |
| 승인 형식 | `APPROVAL_REQUIRED`, `APPROVAL_MALFORMED`, `APPROVAL_FIELD_MISSING`, `APPROVAL_UNKNOWN_FIELD`, `APPROVAL_SCHEMA_UNSUPPORTED`, `APPROVAL_NOT_CANONICAL` |
| 서명·신뢰 | `SIGNATURE_INVALID`, `VERIFIER_UNAVAILABLE`, `TRUST_CONFIG_UNAVAILABLE`, `TRUST_CONFIG_INSECURE` |
| 승인 의미 | `APPROVAL_NOT_YET_VALID`, `APPROVAL_EXPIRED`, `APPROVAL_LIFETIME_EXCEEDS_POLICY`, `APPROVAL_ACTION_MISMATCH`, `APPROVAL_ENVIRONMENT_MISMATCH`, `APPROVAL_BINDING_MISMATCH`, `APPROVAL_REVOKED`, `REVOCATION_LIST_UNAVAILABLE` |
| 소비 | `APPROVAL_ALREADY_CONSUMED`, `CONSUMPTION_STORE_UNAVAILABLE`, `CONSUMPTION_WRITE_FAILED` |
| 측정 | `TREE_UNAVAILABLE`, `TREE_NOT_CLEAN`, `TREE_NOT_ORIGIN_MAIN`, `ORIGIN_FETCH_FAILED`, `IMAGE_STATE_UNAVAILABLE`, `RUNNING_IMAGES_INCONSISTENT`, `TAG_ALREADY_EXISTS`, `ROLLBACK_POINT_MISSING` |
| 정책 | `OPERATION_BLOCKED_BY_POLICY`(Option A 아래의 validate·promote·rollback) |
| 실행 | `DOCKER_COMMAND_FAILED`, `DOCKER_TIMEOUT`, `BUILD_FAILED`, `DEADLINE_EXCEEDED`, `POST_CHECK_FAILED` |
| 감사·내부 | `AUDIT_WRITE_FAILED`, `RUNNER_LATCHED`, `REPLY_SCHEMA_INVALID`, `RUNNER_ERROR` |

- 시험(C3): 모든 code에 고정 문구가 있음, 문구에 경로·숫자·Docker 출력이 없음, 응답 스키마의 `reason_code` enum = 이 목록.
- `DIAGNOSTIC_CODE_INDEX.md`에 이 code들이 들어가는 것은 C3 PR의 일이다.

---

### 5.1 v0.2에서 더한 code
| 군 | code | 쓰는 곳 |
|---|---|---|
| 장애 | `EFFECT_STATE_UNKNOWN`, `FAILED_EFFECT_PARTIAL`, `RUNNER_LATCHED` | 명세 3.3 C·E·F |
| 시작 | `INSTANCE_LOCK_HELD`, `RUNNER_STARTUP_BLOCKED`, `AUDIT_CHAIN_BROKEN`, `RECONCILIATION_MISMATCH` | 명세 3.2, 7.3 |
| 신뢰 | `TRUST_CONFIG_UNAUTHORIZED_CHANGE`(T2를 고를 때만) | 명세 2.5 |
| 데이터 배치(scheduler 쪽, 실행기 아님) | `SCHEDULE_LAYOUT_MISSING`, `SCHEDULE_LAYOUT_CONFLICT`, `SCHEDULE_LAYOUT_UNKNOWN` | 명세 10 |
| 환경 | `RUNNER_ENVIRONMENT_MISMATCH` | 명세 9 |
| 시각(v0.3) | `APPROVAL_TOO_SHORT_FOR_ACTION`, `CLOCK_UNTRUSTED` | §1.5 |
| 신뢰(v0.3, T3 선택 시) | `TRUST_ANCHOR_MISMATCH` | §2.6 |
| LATCH 해제(v0.3, CR-17 채택 시) | `RECOVERY_DOCUMENT_INVALID` | §7.6 |
| N1 전송(v0.4) | `ACTOR_UNAVAILABLE`(G), `ACTOR_REPLY_INVALID`(G), `ACTOR_PEER_NOT_PERMITTED`(X) | 명세 6.1b |
| N1 저장소(v0.4) | `STORE_GENESIS_MISSING`, `STORE_ID_MISMATCH`, `STORE_CHAIN_BROKEN` (모두 `RUNNER_STARTUP_BLOCKED`의 세부 code) | 명세 3.4 |
| N1 복구(v0.4, CR-17 후보) | `RECOVERY_DOCUMENT_INVALID`, `RECOVERY_DOCUMENT_REPLAYED`, `RECOVERY_EVIDENCE_MISMATCH`, `RECOVERY_INBOX_UNSAFE` | 명세 7.6 |
| N1 inspect(v0.4) | `INSPECT_RATE_LIMITED` | 명세 0N |

- `FAILED_EFFECT_PARTIAL`은 실행 상태 값이면서 응답 code다. 응답 `data`에 측정된 이미지 id·태그만 담는다.

### 5.2 v0.5 code 정리 — 응답 code와 감사 전용 code (닫힘)
**통합(하나의 실패 = 하나의 code):**
| 이전 | v0.5 | 이유 |
|---|---|---|
| `AUDIT_INCOMPLETE_LATCHED`, `PRIOR_EFFECT_UNRESOLVED` | **`RUNNER_LATCHED`** | 둘 다 "LATCH 중 요청 거부"였다. LATCH 원인은 감사 불완전만이 아니다(UNKNOWN, 대조 불일치). 원인 code는 감사 전용 |
| `LATCH_RESOLUTION_INVALID`(v0.3) | **`RECOVERY_DOCUMENT_INVALID`** | v0.4와 중복 |
| `RECOVERY_TYPE_STATE_MISMATCH`(GAP 검토 후보) | 채택 안 함 | X가 상태로 namespace를 고르므로(§2.8) 유형 불일치는 서명 실패로 나타남 → `RECOVERY_DOCUMENT_INVALID` |

**응답 code(소켓으로 나가는 것):**
- 전송·요청: `MALFORMED_REQUEST`, `FRAME_TOO_LARGE`, `PEER_NOT_PERMITTED`, `CREDENTIALS_UNAVAILABLE`, `VERB_NOT_PERMITTED`, `ARGUMENT_NOT_ACCEPTED`, `ARGUMENT_INVALID`, `REQUEST_ID_REQUIRED`, `REQUEST_ID_REUSED`, `REQUEST_IN_FLIGHT`, `RUNNER_BUSY`, `INSPECT_RATE_LIMITED`
- N1 전송: `ACTOR_UNAVAILABLE`, `ACTOR_REPLY_INVALID`, `ACTOR_PEER_NOT_PERMITTED`
- 승인: `APPROVAL_REQUIRED`, `APPROVAL_MALFORMED`, `APPROVAL_FIELD_MISSING`, `APPROVAL_UNKNOWN_FIELD`, `APPROVAL_SCHEMA_UNSUPPORTED`, `APPROVAL_NOT_CANONICAL`, `SIGNATURE_INVALID`, `VERIFIER_UNAVAILABLE`, `TRUST_CONFIG_UNAVAILABLE`, `TRUST_CONFIG_INSECURE`, `TRUST_ANCHOR_MISMATCH`(CR-12 T3일 때 도달), `TRUST_CONFIG_UNAUTHORIZED_CHANGE`(T2일 때 도달), `APPROVAL_NOT_YET_VALID`, `APPROVAL_EXPIRED`, `APPROVAL_LIFETIME_EXCEEDS_POLICY`, `APPROVAL_TOO_SHORT_FOR_ACTION`, `APPROVAL_BEFORE_CUTOFF`, `APPROVAL_ACTION_MISMATCH`, `APPROVAL_ENVIRONMENT_MISMATCH`, `APPROVAL_BINDING_MISMATCH`, `APPROVAL_REVOKED`, `REVOCATION_LIST_UNAVAILABLE`, `APPROVAL_ALREADY_CONSUMED`, `CONSUMPTION_STORE_UNAVAILABLE`, `CONSUMPTION_WRITE_FAILED`, `CLOCK_UNTRUSTED`
- 측정: `TREE_UNAVAILABLE`, `TREE_NOT_CLEAN`, `TREE_NOT_ORIGIN_MAIN`, `ORIGIN_FETCH_FAILED`, `IMAGE_STATE_UNAVAILABLE`, `RUNNING_IMAGES_INCONSISTENT`, `TAG_ALREADY_EXISTS`, `ROLLBACK_POINT_MISSING`
- 정책: `OPERATION_BLOCKED_BY_POLICY`
- 실행: `DOCKER_COMMAND_FAILED`, `DOCKER_TIMEOUT`, `BUILD_FAILED`, `DEADLINE_EXCEEDED`, `POST_CHECK_FAILED`, `FAILED_EFFECT_PARTIAL`, `EFFECT_STATE_UNKNOWN`
- 상태: `RUNNER_STARTUP_BLOCKED`, `RUNNER_LATCHED`, `INSTANCE_LOCK_HELD`(시작 실패, 응답 불가 시 로그만), `RUNNER_ENVIRONMENT_MISMATCH`(시작 거부)
- 감사·내부: `AUDIT_WRITE_FAILED`, `REPLY_SCHEMA_INVALID`, `RUNNER_ERROR`(목록 밖 모든 오류, `error_ref`만)

**감사 전용 code(응답에 나가지 않음. 상태가 막혀 있으면 응답은 `RUNNER_STARTUP_BLOCKED`/`RUNNER_LATCHED`):**
- 저장소: `STORE_GENESIS_MISSING`, `STORE_ID_MISMATCH`, `STORE_CHAIN_BROKEN`
- 기록: `AUDIT_CHAIN_BROKEN`, `RECONCILIATION_MISMATCH`, `RECONCILED_UNKNOWN`
- 복구(수신함은 시작 시에만 처리되고 응답 경로가 없음): `RECOVERY_DOCUMENT_INVALID`, `RECOVERY_DOCUMENT_REPLAYED`, `RECOVERY_EVIDENCE_MISMATCH`, `RECOVERY_INBOX_UNSAFE`, `REINIT_EVIDENCE_INSUFFICIENT`, `REINIT_ALREADY_PERFORMED`

**scheduler 쪽(실행기 아님, 명세 10):** `SCHEDULE_LAYOUT_MISSING`, `SCHEDULE_LAYOUT_CONFLICT`, `SCHEDULE_LAYOUT_UNKNOWN`.

**규칙:**
- 응답은 code별 고정 문구만 담는다. 원본 예외, Secret, traceback, Docker 출력, `ssh-keygen` 출력은 넣지 않는다.
- 목록 밖 code는 `RUNNER_ERROR`로 바꾼다.
- 응답 스키마의 `reason_code` enum = 응답 code 목록이다(C3 시험).
- 기존 `DIAGNOSTIC_CODE_INDEX.md`와의 충돌 여부는 C3 PR에서 생성기로 확인한다(이름 공간이 새 모듈 안이라 충돌 가능성은 낮음 [추론]).

## 명세 6. 실행기 요청·응답 스키마 (CR-1)

### 6.1 공통 (v0.4 N1)
**6.1a Claude → G (`runner.sock`)**
- 전송: `socket_door`, uid 허용 목록 = {claude}(G unit 리터럴. 공유 env `MVP_BRIDGE_CLIENT_UID`는 쓰지 않음 — 비면 검사 없음 [CODE `socket_door.py:462`]). 비면 listen 거부.
- 요청 최상위(닫힘): `proto`(=2), `client_id`(귀속만), `request_id`, `verb`, `args`. 운영 동작은 `approval_document`, `approval_signature`를 더한다.
- 금지: 셸 문자열, 경로, 컨테이너·이미지 이름, Docker 인자, env.
- 응답(닫힘): `ok`, `reason_code`, `reason`(고정 문구), `request_id`, `data`, `error_ref`, `replayed`.
  - G는 X의 응답을 스키마 검증한 뒤 전달한다.
  - 기존 `refusal_payload`·`internal_error_payload`는 메시지 원문을 내보내므로 [CODE] 쓰지 않는다. code→고정 문구로 다시 만든다.

**6.1b G → X (`actor.sock`)**
| 항목 | 계약 |
|---|---|
| 인증 | `SO_PEERCRED` uid ∈ {G_ACCOUNT}(X unit 리터럴). 아니면 `ACTOR_PEER_NOT_PERMITTED`. **uid는 송신자만 증명하고 요청의 정당성은 증명하지 않는다** |
| 메시지 | `thomas.actor_request.v1`(닫힘): `{schema, request_id, verb, args, approval_document_b64?, approval_signature?, gate_intake_seq}`. 메시지 타입은 하나다. 상태 조회 전용 타입은 없다(같은 메시지 재전송 = 조회) |
| `gate_intake_seq` | G 접수 기록의 행 번호. **X는 판정에 쓰지 않는다.** 감사 상관용(N1-c 대조)일 뿐이다 |
| 지문 | X가 메시지에서 `gate_intake_seq`를 뺀 나머지로 `request_sha256`을 계산한다(G가 보낸 지문은 받지 않음) |
| 응답 | `thomas.actor_reply.v1`(닫힘): `{ok, reason_code, request_id, state, data, replayed}` |
| 크기 | 프레임 ≤ 8,192바이트 [CODE `MAX_FRAME_BYTES`] |
| timeout | 연결은 효과가 끝날 때까지 유지. 먼저 끊겨도 X는 계속한다 |
| 직접 호출 | Claude는 `actor.sock` 디렉터리 그룹 밖. 운영 증거는 C6 전까지 NOT_VERIFIED. 닿더라도 X의 자기 검증 → A6과 같은 결과 |

### 6.2 `inspect_candidate` (D-4 DESIGN_APPROVED 조건)
| 항목 | 명세 |
|---|---|
| 요청 | `args: {pr}` (정수 1–999999). `request_id` 선택 |
| 승인 | 없음 |
| 결속 | 없음(읽기). 결과는 Thomas가 승인 문서를 만들 때 참고하는 값일 뿐 |
| Docker | 고정 argv: 실행기 고정 목록의 `thomas-` 컨테이너 inspect, `thomas-agent-runtime:{latest, candidate-<pr>, rollback-pre-<pr>}` image inspect. 결과에서 허용 필드만 추출(allowlist 추출) |
| 응답 `data` | `{pr, running: {<서비스>: image_id|null}, tags: {latest, candidate, rollback_pre: image_id|null}, containers: [{name, state, health}], measured_at}` — `state` enum(running, exited, restarting, paused, created, dead), `health` enum(healthy, unhealthy, starting, none) |
| 제외 | Env, Mounts, Config, Labels, Args, Cmd, NetworkSettings, LogPath, 로그 |
| 감사 | `INSPECT` 행(결과 code만, 응답 내용 없음) |
| 멱등·재시도 | 읽기라 자연 멱등. 실행기는 재시도하지 않음 |
| 실패 | `IMAGE_STATE_UNAVAILABLE`, `DOCKER_TIMEOUT` |
| 운영 | OPERATION_NOT_AUTHORIZED(실행 금지). Option A 비저촉(Secret 미처리) |

### 6.3 `prepare_candidate` (v0.2 개정)

| 칸 | 상태 |
|---|---|
| DESIGN_CANDIDATE | 허용 |
| SYNTHETIC_DESIGN_REVIEW | 허용 |
| IMPLEMENTATION | 미승인 |
| DOCKER_EXECUTION | 미승인 |
| OPERATIONAL_ACTIVATION | **BLOCKED** |

- 3-2에 해당하는 호출(`docker compose config`)을 뺀 것은 설계상의 정리다. **운영 실행이 승인됐다는 뜻이 아니다.**

**요청·결속 (v0.1과 같음):**
- `args: {pr, git_commit_sha}` + `request_id` + 승인(`action=prepare_candidate`, `candidate_image_id=null`).
- 결속: 승인 SHA = 자기 clone의 `origin/main` = HEAD, 승인 `rollback_image_id` = 실행 중 이미지(여덟 서비스 동일).

**정적 검토 10항목:**

| # | 항목 | 판단 | 근거 | 상태 |
|---|---|---|---|---|
| 1 | 빌드 경로의 Secret 노출 | 빌드 컨텍스트는 실행기가 새로 받은 자기 clone이다. `.env`는 추적되지 않으므로 clone에 없다. `.dockerignore`가 `**/.env*`·`**/*.key*`·`**/*.pem*`·상태 루트·`THOMAS_CORE/approvals/` 등을 제외하고 [CODE], Dockerfile은 정해진 디렉터리만 `COPY`한다 [CODE] | 추적 파일 자체에 Secret이 있으면 이미지에 들어간다. 저장소 전체에 Secret이 없다는 것은 이번에 증명하지 않았다 | NOT_VERIFIED(추적 파일 내용) |
| 2 | 빌드 컨텍스트 제한 | 실행기가 PR 번호로 만든 자기 트리 경로만 쓴다. 요청은 경로를 받지 않는다. `compose build`는 쓰지 않는다. compose의 `build: .`는 compose 파일 디렉터리를 컨텍스트로 쓰기 때문이다 [CODE] | — | 설계 |
| 3 | 환경변수 전달 | `docker build`는 `.env`를 읽지 않는다. 실행기는 최소 env로 `docker` CLI를 부른다. Docker 데몬 설정이 프록시 등 환경을 빌드에 넣는지는 확인하지 않았다(호스트 설정 미열람) | — | NOT_VERIFIED(데몬 설정) |
| 4 | build argument | Dockerfile에 `ARG`가 없다 [CODE]. 실행기는 `--build-arg`·`--secret`을 넘기지 않고, 요청에 그런 필드가 없다 | — | 설계 |
| 5 | 이미지 metadata | 빌드 이력에 RUN·ENV·COPY 명령이 남는다. ENV 값은 `PYTHONUNBUFFERED=1` 등 상수다 [CODE]. Dockerfile에 LABEL은 없다. compose label은 실행 시점에 붙는다 | `apt-get install poppler-utils`가 버전 고정이 아니라 [CODE] **이미지는 재현되지 않는다** → candidate id는 SHA에서 유도할 수 없고 빌드 결과를 측정해야 한다 | 설계 + 사실 |
| 6 | rollback 태그의 운영 영향 | 태그는 이미지 참조만 늘리고 실행 중 컨테이너는 바꾸지 않는다. `rollback-pre-<pr>`가 이미 있으면 다른 세션이 이미 배포했다는 신호다(CLAUDE.md 배포 규칙) → `TAG_ALREADY_EXISTS`, STOP | 태그는 이미지 GC를 막는다(디스크) | 설계 |
| 7 | 기존 preflight와 분리된 경계 | prepare의 사전 검사는 git(clean·HEAD), 태그 비어 있음, rollback 지점, compose label 대조뿐이다. compose config(bind source 검사)는 validate로 옮긴다. label 중 `environment_file`은 경로 문자열이고 값이 아니다. 실행기는 그것을 자기 설정과 비교만 하고 응답하지 않는다 | 기존 preflight의 `fires` 검사(scheduler 파일 읽기)는 빌드가 실행 중 서비스를 바꾸지 않으므로 prepare에서 뺄 수 있다. 빌드 부하는 남는다 [추론] | 설계 |
| 8 | 실행 전 승인 | 필요(Docker 쓰기, 자원 사용, 태그 생성) | — | 설계 |
| 9 | 실패 시 상태 불확실성 | rollback 태그 후 빌드 실패 → `FAILED_EFFECT_PARTIAL`(태그 있음, candidate 없음). 빌드 도중 종료 → 재측정 실패 시 `EFFECT_STATE_UNKNOWN`. 둘 다 LATCH | 같은 PR 재준비는 운영자가 태그를 판단한 뒤에만 | 설계 |
| 10 | Option A와의 충돌 | 3-1(`.env`로 compose 실행) 없음, 3-2(보간된 compose config) 없음 → 설계상 비저촉 | 그러나 NO_APPROVED_EXECUTION_PATH·실행기 미구현 → 활성화는 BLOCKED | 설계 |

- **설계 문서 §4.3과의 차이:**
  - §4.3 표는 `prepare_candidate`가 "자기 트리 생성, 첫 preflight, `rollback-pre-<pr>` 태그, `candidate-<pr>` 빌드"를 한다고 적는다.
  - v0.2는 "첫 preflight" 중 compose config 단계를 빼고, `fires` 검사를 생략할 수 있다고 본다.
  - 문서 정정은 별도 PR 승인 대상이다.

**응답 `data`:** `{pr, git_commit_sha, rollback_image_id, candidate_image_id}`. 부분 실패면 측정된 것만 담고 나머지는 null이다.

### 6.4 `validate_candidate` — OPERATIONALLY_BLOCKED
| 항목 | 명세 |
|---|---|
| 요청 | `args: {pr, git_commit_sha, candidate_image_id}` + `request_id` + 승인 |
| 결속 | SHA, candidate·rollback id 실측 일치 |
| 효과 | 등록된 자가검증만(이름 목록은 실행기 코드 상수). `--promote --tree` preflight 상당(compose config 포함) |
| 응답 `data` | `{pr, verdict: PASS|STOP, checks: [{check, level, reason_code}]}` — check 이름·level enum 닫힘, 자유 텍스트 없음 |
| Option A | **3-2 해당(보간된 compose config 처리)** → `OPERATION_BLOCKED_BY_POLICY` |
| 설계·시험 | 가짜 `run`으로 C3 합성 가능 |

### 6.5 `promote` — OPERATIONALLY_BLOCKED
| 항목 | 명세 |
|---|---|
| 요청 | `args: {pr, git_commit_sha, candidate_image_id, rollback_image_id}` + `request_id` + 승인 |
| 결속 | 승인·HEAD·Docker 실측 3자 일치, `environment` 일치 |
| 효과 | 직전 preflight 재실행 STOP 0 → `latest`를 candidate **id**로 태그 → `compose -p thomas_agent --env-file <profile 경로> up -d` → 사후 확인(여덟 서비스 id = candidate id, health) |
| 실패 | 마감 `DEADLINE_EXCEEDED`, 사후 확인 `POST_CHECK_FAILED`. **자동 rollback 없음**(rollback은 별도 승인) |
| Option A | **3-1 해당 + DEPLOYMENT_HOLD** → `OPERATION_BLOCKED_BY_POLICY` |

### 6.6 `rollback` — OPERATIONALLY_BLOCKED
| 항목 | 명세 |
|---|---|
| 요청 | `args: {pr, candidate_image_id(지금 실행 중), rollback_image_id}` + `request_id` + **promote와 다른 별도 승인** |
| 효과 | `latest`를 rollback **id**로 태그 → 같은 compose → 사후 확인 |
| Option A | **3-1 + Rollback emergency NOT_GRANTED** → `OPERATION_BLOCKED_BY_POLICY` |

### 6.6a 실패 처리의 공통 규칙 (v0.2)
- 6.3–6.6의 실패는 모두 명세 3.3 CASE A–H를 따른다.
- `POST_CHECK_FAILED`·`DEADLINE_EXCEEDED`·`DOCKER_COMMAND_FAILED`는 효과가 시작된 뒤라면 `FAILED_EFFECT_PARTIAL` 또는 `EFFECT_STATE_UNKNOWN`으로 기록하고 LATCH를 건다.
- 자동 rollback·자동 재시도는 없다.

### 6.7 긴 작업 응답 모델 — CR-6 모델 A를 X에서 수행 (v0.4)
| 구분 | Claude가 보는 것 | 경로 |
|---|---|---|
| 최초 실행 요청 | 진행 → 결과 | Claude→G→X, X §3.1a |
| 처리 중 상태 조회 | `REQUEST_IN_FLIGHT` | 같은 프레임 재전송 → G → X(요청 기록 `claimed`) |
| 완료 결과 조회 | 기록된 결과 재생(`replayed: true`) | 같은 프레임 재전송 → X(`completed`) |
| 연결 종료 후 조회 | 같음 | X는 연결과 무관하게 계속·기록 |
| 응답 유실(X→G 또는 G→Claude) | 같음 | 재전송 |
| 효과 불명확 | `EFFECT_STATE_UNKNOWN` / `FAILED_EFFECT_PARTIAL` | X 기록 `unknown`/`partial`, LATCH |
| 재시작 이후 조회 | 시작 대조가 끝난 뒤 위와 같음. LATCH면 `RUNNER_LATCHED` | X §7.3 |
| 소비 기록 손실 | 모든 요청 거부(`RUNNER_STARTUP_BLOCKED`) | X §3.4 |
| 지문 다른 같은 id | `REQUEST_ID_REUSED` | X |
| 같은 승인, 다른 id | `APPROVAL_ALREADY_CONSUMED` | X |

- 조회가 실행이 되지 않는 근거:
  1. X는 `request_id` 기록이 있으면 검증·소비 단계에 들어가지 않는다.
  2. 같은 승인의 두 번째 소비는 소비 저장소가 막는다.
- TTL 재claim은 없다. 결과 없는 claim은 시작 대조에서 `unknown`이 된다.
- G는 멱등성 상태를 갖지 않는다. G가 재시작돼도 조회는 X 기록으로 답한다.

### 6.8 CR-7 Option A 게이트 — DESIGN_DECISION_ACCEPTED
validate·promote·rollback의 활성화 여부는 **실행기 코드의 상수**다(예: `OPERATIONAL_VERBS_ENABLED = frozenset()`).

| 필수 조건 | 설계 |
|---|---|
| 기본값 BLOCKED | 상수의 기본값이 빈 집합 → `OPERATION_BLOCKED_BY_POLICY` |
| 런타임 환경변수로 해제 불가 | 게이트는 env를 읽지 않는다 |
| 요청 파라미터로 해제 불가 | 요청 스키마에 게이트 관련 필드가 없다 |
| Claude가 고칠 수 있는 설정으로 해제 불가 | 설정 파일을 읽지 않는다. 설치본은 root 소유(운영 검증은 N-4) |
| 자동 승격 금지 | 상태·시간·성공 횟수로 바뀌는 경로가 없다 |
| CI PASS로 자동 해제 금지 | CI는 설치본을 바꾸지 않는다. 설치는 Thomas 작업 |
| 운영 단계 변경은 별도 승인 | 상수 변경 = 코드 PR + 검토 + 병합 + 설치(각각 별도 승인) |

- 코드 상수도 앞으로 승인된 코드 변경으로 바뀔 수 있다. 이것은 의도된 해제 경로이고, 다른 해제 경로는 없다.
- CLAUDE.md의 "게이트 능력은 env opt-in으로 연다"는 규칙과 형식이 다르다. 이 실행기 능력이 그 규칙의 범주인지는 C3 PR에서 거버넌스 판단으로 기록한다(남은 위험).
- 이번 C2에서 게이트를 구현하거나 바꾸지 않는다.

**기타 DECISION_DEFERRED:** `inspect_candidate`의 `request_id` 필수 여부(제안: 선택).

### 6.9 N1에서 동작별 변화 요약 (v0.4)
- 6.2–6.6의 동작 정의·결속·Option A 상태는 그대로다. 수행 주체만 X다.
- `inspect_candidate`의 G 침해 시 경계는 명세 0N에 있다.
- CR-1의 5개 동작은 늘지 않는다. G↔X 메시지 타입은 하나이고, RECOVERY_INBOX는 소켓 동작이 아니다(§7.6).

## 명세 7. 감사 무결성·LATCH·복구 (v0.4 N1 재설계)

### 7.1 감사 기록 둘
**7.1a X 효과 감사 `thomas.deploy_audit.v1` — 정본**
- v0.3 필드 그대로다(`seq`, `event_id`, `recorded_at`, `request_id`, `action`, `caller_uid`(= G_ACCOUNT), `client_id`, `approval_ref`, `trust_config_sha256`, `signer_key_fingerprint`, `target`, `validation_result`, `reason_code`, `execution_state`, `runner_install_sha`, `latch_id`, `generation`, `previous_event_sha256`, `event_sha256`).
- 추가 필드:
  - `gate_intake_seq`(상관용)
  - `consumption_seq`·`consumption_row_sha256`(CONSUMED 행)
  - `store_id`(`RUNNER_START` 행)
- `execution_state` 추가 값: `STORE_REINITIALIZED`, `RECOVERY_DOCUMENT_REJECTED`, `LATCH_RESOLVED`.

**7.1b G 접수 기록 `thomas.gate_intake.v1`**
- `{seq, recorded_at, peer_uid, request_id, verb, request_sha256(G 계산), outcome(REFUSED_AT_GATE / FORWARDED / REPLY_FROM_ACTOR / ACTOR_UNAVAILABLE), actor_reason_code?, prev_sha256, row_sha256}`.
- 승인 원문·서명·Secret은 담지 않는다.
- **외부 감사 증거가 아니다**(N1-4). X 기록과 다를 때 성공을 추정하는 근거로 쓰지 않는다.

### 7.2 쓰기 프로토콜 (X)
- 행마다 `flock` → 직전 해시 확인 → 쓰기 → fsync. 부분 쓰기는 잘라 되돌리고, 실패하면 LATCH.
- 효과 전 필수: RECEIVED, CONSUMED. 응답 전 필수: 끝 행.
- G 접수 기록도 같은 쓰기 규칙을 따른다. G는 FORWARDED 행이 fsync되지 않으면 X에 보내지 않는다.

### 7.3 X 시작 대조 (listen 전)
1. 인스턴스 잠금
2. 소비 저장소 genesis·`store_id`·체인(§3.4) → 실패 시 `RUNNER_STARTUP_BLOCKED`
3. 감사 체인·`seq` → 실패 시 `AUDIT_CHAIN_BROKEN` → LATCH
4. 감사 CONSUMED ↔ 소비 행 교차 대조 → 불일치 → `RECONCILIATION_MISMATCH` → LATCH
5. 요청 기록: `claimed`인데 끝이 없음 → `unknown` + `RECONCILED_UNKNOWN` + LATCH
6. 미해결 UNKNOWN·PARTIAL마다 `LATCH_RESOLVED` 행이 있는지 확인. 없으면 LATCH(LATCH 파일 유무와 무관)
7. RECOVERY_INBOX 처리(§7.6, CR-17 후보. 미승인 부분은 구현되지 않음)
8. 시계 검사(§1.5)
9. `RUNNER_START` 행(설치본 SHA, `store_id`, `trust_config_sha256`, 환경 지문)
10. **G 접수 기록은 읽지 않는다(GAP-2C).** X는 G 디렉터리에 접근 권한이 없고, G 기록은 X 판정의 입력이 아니다. G/X 대조는 실행기 밖 운영자 절차(§7.9)다.

### 7.3a CR-18 LATCH 중 요청 처리 (v0.3 유지)
- LATCH가 있거나, LATCH 상태를 확인할 수 없거나, 대조가 끝나지 않았으면 X는 **모든 요청(inspect 포함)**을 거부한다.
- G는 그 code를 그대로 전달한다.

### 7.4 10개 상황 (v0.3 표 유지, 판정 주체 X)
| # | 상황 | 탐지 | 처리 |
|---|---|---|---|
| 1 | 기록 도중 종료 | 마지막 줄 파싱 실패 | 재시작 때 `AUDIT_CHAIN_BROKEN` → LATCH. 자동 잘라내기 없음(무엇이 잘렸는지 운영자가 판단) |
| 2 | 쓰기 후 fsync 전 종료 | 마지막 행이 사라질 수 있음 → 소비 저장소와 대조 | 소비 행은 있는데 감사 행이 없으면 `RECONCILIATION_MISMATCH` → LATCH |
| 3 | 실행 도중 감사 실패 | 쓰기 오류 | CASE E. 효과 전이면 CASE B |
| 4 | AUDIT_INCOMPLETE | LATCH 파일 | 운영 동작 거부 |
| 5 | 재시작 | §7.3 | LATCH가 메모리에만 있지 않다. 파일로 남고, 파일이 없더라도 대조가 다시 만든다 |
| 6 | 감사 파일 손상 | 체인 검사 | LATCH |
| 7 | 일부 삭제 | 중간 삭제: `seq` 공백·체인 단절. **끝부분 삭제: 로컬로는 탐지 불가.** 소비 저장소 대조와 외부 앵커로만 탐지 | LATCH(탐지된 경우) |
| 8 | 전체 재작성 | 로컬로는 탐지 불가(체인 재계산 가능) | 외부 앵커와 대조할 때만 |
| 9 | 로그 회전 | 세대 시작 행이 이전 세대의 마지막 `event_sha256`·`seq`·파일 sha256을 담음 | 회전은 실행기 자신이 시작할 때만, 열린 요청 0·LATCH 없음일 때만 |
| 10 | 세대 삭제 | 세대 목록 파일(`generations.jsonl`: 세대별 첫·끝 해시) | 삭제 전에 해당 세대의 끝 해시가 외부 앵커에 있어야 함(절차). root는 목록도 다시 쓸 수 있음 |
- 추가: "소비 저장소와 감사가 함께 끝부분 삭제·재작성됨" → 로컬 탐지 불가. 외부 앵커와 대조할 때만 탐지된다.

### 7.5 신뢰 경계 (유지)
- 로컬 해시 체인(감사·소비·G 접수)은 X 침해나 root의 전체 재작성을 막지도, 독립적으로 탐지하지도 못한다.
- OPERATIONAL_AUDIT_INTEGRITY = NOT_VERIFIED.

### 7.6 RECOVERY_INBOX — 두 유형 (v0.5, GAP-1C. 설계 후보. 위치·소유권·권한·운영 절차·구현 미승인)
- 기존 5개 동작에 추가하지 않는다(소켓 동작 아님, 시작 대조 중에만 처리). G는 관여하지 않는다.
- 운영 복구 승인은 일반 배포 승인과 별개다.
- **서명은 필요조건이다.** 유형별 필수 검증(재측정·기록 정합성·외부 앵커)까지 모두 PASS여야 한다.

| # | 계약 | TYPE A | TYPE B |
|---|---|---|---|
| 1 | 유형 구분 | 파일명이 아니라 X 상태(§2.8)로 기대 유형을 정하고, 서명 뒤 `schema`로 재확인 | 같음 |
| 2 | namespace | `latch-resolution.v1@…` | `store-reinit.v1@…` |
| 3 | 허용 서명자 | X 신뢰 목록(CR-12 DEFERRED). B 전용 키 여부 DEFERRED | 같음 |
| 4 | 원본 바이트 검증 | 파일 바이트 그대로 `ssh-keygen -Y verify`, 그 뒤 정규 바이트 비교 | 같음 |
| 5 | 결속 | `latch_id` + 현재 `store_id` | `old_store_id`·`new_store_id`·`old_store_sha256`·감사 tip·외부 앵커 |
| 6 | 유효기간 | 짧게(값 DEFERRED) | A 이하 |
| 7 | replay | 현재 저장소 `RESOLUTION` 행 | 새 저장소 `REINIT` 행 + `old_store_id`당 1회 |
| 8 | 경로 교체·symlink | 수신함 디렉터리 `O_DIRECTORY\|O_NOFOLLOW`, 파일 `dir_fd` + `O_NOFOLLOW\|O_NONBLOCK`. PR-C `_open_private`의 방식을 정적 참고로 쓴다 [CODE]. 코드 재사용 가능성이 운영 보안을 입증하지는 않음 | 같음 |
| 9 | 소유권 | 열린 descriptor 기준: 일반 파일, 링크 1, root 소유, group·other 쓰기 없음, 크기 상한 | 같음 |
| 10 | 시작 시 순서 | 잠금 → 저장소 판정(정상) → 감사 → 교차 대조 → 수신함(A만) → 문서별 서명·스키마·결속·재측정 → 소비 → 감사 fsync → LATCH 해제 판단 → 시계 → `RUNNER_START` → listen | 잠금 → 저장소 판정(비정상) → 감사 → 수신함(B만) → 서명·스키마·결속 → 조건 1–6 → 새 저장소 genesis 확인 → `IMPORTED` 이관 → `REINIT` 소비 → 감사 fsync → 시계 → `RUNNER_START` → listen(운영 동작은 새 승인 전까지 없음) |
| 11 | 소비·감사 순서 | 소비 fsync → 감사 `LATCH_RESOLVED` fsync → LATCH 파일 제거(X) | 이관·소비 fsync → 감사 `STORE_REINITIALIZED` fsync → 사용 시작 |
| 12 | 실패 응답 | 수신함 처리는 응답 경로가 없다. 감사 `RECOVERY_DOCUMENT_REJECTED{type, code}`. 소켓 요청은 `RUNNER_LATCHED` | 같음. 소켓 요청은 `RUNNER_STARTUP_BLOCKED` |
| 13 | 감사 기록 | `LATCH_RESOLVED{latch_id, resolution_id, decision, evidence_sha256}` | `STORE_REINITIALIZED{old_store_id, new_store_id, old_store_sha256, imported_count, cutoff_at, audit_tip_sha256, external_anchor}` |
| 14 | 재시작 후 | 해제 행이 있으면 대조가 LATCH를 재생성하지 않음. 문서가 수신함에 남아 있어도 replay로 거부 | 새 `store_id`가 감사 `RUNNER_START`와 맞음. 같은 문서 재투입은 `REINIT_ALREADY_PERFORMED` |
| 15 | 자동 복구 금지 | 해제는 재실행이 아니다. 새 승인 필요 | 재초기화 뒤 운영 동작 자동 시작 없음 |

### 7.6a 복구 판단 절차 (v0.3 유지, 실행 주체를 N1에 맞춤)
- 판단은 Thomas가 하고, 효과 확인은 실행기(G·X) 밖 운영자 절차로 한다.
- 기록·검증은 X가 하고, 문서 전달은 RECOVERY_INBOX(§7.6)로 한다.

| # | 단계 | 주체 | 산출 |
|---|---|---|---|
| 1 | LATCH 원인 확인 | Thomas | `latch_id`, reason_code, 관련 `request_id` |
| 2 | 이전 효과의 실행 여부 확인 | Thomas, **실행기 밖** 운영자 절차(Claude 경로 아님) | 측정 기록 |
| 3 | 실제 결과의 독립 증거 | Thomas | 이미지 id·태그·컨테이너 상태의 측정값과 그 해시 |
| 4 | 승인 소비 기록 대조 | X 저장소의 읽기 전용 검사 도구(C3 후보, 실행기 밖에서 Thomas가 실행) 또는 Thomas | 일치·불일치 |
| 5 | 감사 정합성 검증 | 같음 + 외부 앵커(X1) | 체인 검사 결과 |
| 6 | UNKNOWN·PARTIAL 해소 판단 | Thomas | `EFFECT_CONFIRMED_COMPLETE` / `EFFECT_CONFIRMED_ABSENT` / `EFFECT_CONFIRMED_PARTIAL` / `UNRESOLVABLE` |
| 7 | 명시 승인 | Thomas | **서명된 해제 문서** `thomas.latch_resolution.v1`(별도 namespace, 예: `latch-resolution.v1@thomas-agent`): `latch_id`, 판단, 증거 해시, 발급·만료, nonce |
| 8 | 기록 | **X**(시작 대조 중, RECOVERY_INBOX) | 해제 문서를 검증한 뒤 `LATCH_RESOLVED` 감사 행(판단·증거 해시). 원래 UNKNOWN 기록은 고치지 않고 해제 행으로 연결 |
| 9 | 재실행이 필요하면 새 승인 | Thomas | 새 승인 문서(이전 것은 소비된 상태 그대로) |
| 10 | 복구 뒤 검증 | — | 합성 검증(C3 시험)과 운영 검증(C6)을 따로 |

### 7.7 외부 앵커 (v0.3 유지)
- CR-14 X1 채택. OPERATIONAL_AUDIT_INTEGRITY = NOT_VERIFIED.
- 앵커 대상에 X 감사 tip, 소비 저장소 tip·`store_id`, G 접수 tip을 포함한다.

### 7.8 R-08과의 정합성 (v0.3 유지)
- 실행기 감사·소비 저장소는 R-08 범위 밖이다.
- 소비 저장소는 회전하지 않는다. 감사 회전은 §7.4-9.

### 7.9 G/X 운영자 사후 대조 (v0.5, GAP-2A·2B)
- **보관:** G 접수 기록(G 계정)과 X 효과 감사(X 계정)는 서로 읽지도 쓰지도 않는다(GAP-2A·2C).
- **대조 주체:** Thomas가 실행기 밖에서 실행하는 읽기 전용 도구(C3 후보, 선택). 두 기록의 **사본**을 입력으로 받는다.

| 대조 항목 | G 쪽 | X 쪽 |
|---|---|---|
| `request_id` | intake 행 | 감사 행 |
| `approval_id` | (G는 1차 파싱 결과만 — 판정 아님) | `approval_ref.approval_id` |
| action | `verb` | `action` |
| target reference | `request_sha256`(G 계산) | `target` 측정값, X `request_sha256` |
| timestamp | `recorded_at` | `recorded_at` |
| G receipt reference | `seq`, `row_sha256` | `gate_intake_seq`(상관용) |
| X decision reference | — | `validation_result`, `execution_state`, `reason_code` |
| audit sequence | G `seq` | X `seq` |
| audit hash reference | G `row_sha256` tip | X `event_sha256` tip |

- **규칙:**
  - G의 `FORWARDED`마다 같은 `request_id`의 X 행(RECEIVED 이상)이 시간 창 안에 있어야 한다.
  - X의 `caller_uid`=G 행마다 G에 `FORWARDED`가 있어야 한다.
  - action·request_id가 같아야 한다.
- **출력:** 일치·불일치 목록(code와 참조만, 원문 없음). 불일치는 **보고만 한다.**
- **하지 않는 것:** 실행기 동작을 바꾸거나 LATCH를 해제하거나 성공을 판정하지 않는다. 불일치의 처리는 Thomas 판단이다(필요하면 TYPE A).
- **한계:** 사후 탐지다. 실시간 방어가 아니고 root 침해 방지도 아니다. 두 기록의 tip을 외부 앵커(X1)와 함께 보관할 때만 root의 전체 재작성 탐지에 쓸 수 있다. 그 전에는 NOT_VERIFIED다.

## 부속 A. R-08 회전·보존 계약 (CR-3 조건부 채택 범위)
요청 감사 로그와 진단 파일은 **실패 계약이 다르다**.
- **요청 감사 로그:** 기록 실패 → fail-closed(응답하지 않음). 기존 `REQUEST_LOG_UNAVAILABLE` [CODE].
- **진단 파일:** 기록 실패 → 행을 버리고 고정 코드 `FACTORY_CHILD_DIAGNOSTICS_UNAVAILABLE`만 남긴다. **원본 예외 메시지를 노출하지 않는다**(E-02 계약 유지) [CODE].
- **회전 중 무손실과 세대 사이 연속성:** 검증 전까지 **NOT_VERIFIED**다(보완 ②). 아래는 설계 목표이지 보장이 아니다.

| # | 조건 | 요청 감사 로그 | 진단 파일 |
|---|---|---|---|
| 1 | 동시 쓰기 | 행마다 열고 `flock`(로그 fd) [CODE] | 잠금 없음. 단일 writer 전제 [CODE]. 전제가 깨지면 행이 섞일 수 있음(NOT_VERIFIED) |
| 2 | 회전 경쟁 | 회전기가 현재 파일에 `flock` 배타 → rename → 해제. 잠금 대기 중이던 writer는 이미 연 옛 inode에 씀 → 옛 세대 끝에 들어감(목표: 유실 없음, NOT_VERIFIED) | 잠금이 없어 rename과 쓰기가 겹칠 수 있음. 옛 fd의 쓰기는 옛 세대로 감(목표) |
| 3 | 기록 누락 | 목표 0. 회전 뒤 새 파일 생성 실패 시 그 요청은 거부(감사 없이 응답하지 않음) | 행 손실 허용(고정 코드) |
| 4 | 중복 기록 | 회전기는 내용을 복사하지 않음(rename만). 중복 경로 없음 | 같음 |
| 5 | 부분 쓰기 | 시작 위치로 잘라 되돌림 [CODE] | 같음 [CODE] |
| 6 | ENOSPC | 응답하지 않음 | 행 버림 + 고정 코드 |
| 7 | 파일 교체 | `O_NOFOLLOW` + 링크 수·소유자·모드 검사 [CODE] | `dir_fd` + 같은 검사 [CODE] |
| 8 | 소유자·권한 불일치 | 거부(응답 안 함). 회전기는 **파일 소유 uid로** 실행(root로 만들면 다음 쓰기가 거부됨) | 거부(고정 코드). 회전기는 10001 |
| 9 | 세대 사이 연결 | 지금 없음(해시 체인 없음). 제안: 세대의 첫 행에 직전 세대 마지막 행의 sha256(코드 변경, 별도 승인) | 감사 대상 아님 |
| 10 | 회전 실패 | 현재 파일에 계속 기록, 경보. 하드 상한 초과 시 응답 거부(fail-closed) — 상한값은 DECISION_DEFERRED | 계속 기록, 하드 상한 초과 시 행 버림 + 고정 코드 |

- **보존·폐기:**
  - 요청 로그는 감사 요건에 따른다.
  - 진단 파일은 Secret에 준하므로 짧게 둔다. 삭제는 unlink다. overlay·SSD에서 덮어쓰기 삭제는 보장할 수 없다(명시). 그래서 보존을 짧게 하고 백업에서 제외하는 것이 실제 통제다.
  - 백업 포함 여부: NOT_VERIFIED.
- **제안 수치(승인 전까지 제안값):** 요청 로그 10MB·5세대, 진단 5MB·3세대·30일.
- **DECISION_DEFERRED:** 수치, 하드 상한 동작, 세대 연결 코드, 백업 제외, 회전기 실행 방식(cron/systemd timer, 소유 uid).

- **v0.2 정합성 메모:**
  - 실행기 감사(명세 7)와 요청 로그는 실패 계약이 같다(기록 실패 → 응답 안 함).
  - 실행기 감사만 해시 체인과 세대 연결을 갖는다.
  - 진단 파일은 감사가 아니므로 체인 대상이 아니다.
  - 세 파일의 회전 주체는 각 파일의 소유 uid다(요청 로그 = door uid, 진단 = 10001, 실행기 감사 = 실행기 자신).

## 부속 B. D-10·CR-12 재정의 (v0.4, 둘 다 DECISION_DEFERRED)

**D-10의 의미 변화:**
- 기존 D-10(설계 문서 §4.2)은 "단일 실행기가 `.env`·상태 파일에 닿는 방법(E1/E2/E3)"이다.
- N1 이후 이 질문은 **X_ACCOUNT**에 대한 것이 된다(Docker·`.env`가 X에만 있음).
- 새로 **G_ACCOUNT**(비특권, Docker·Secret 없음) 결정이 생긴다.
- D-10 번호와 정의는 바꾸지 않는다. 아래는 D-10을 결정할 때 쓸 재정의 자료다.

| | G_ACCOUNT | X_ACCOUNT |
|---|---|---|
| 필요 권한 | 소켓 2개(runner.sock listen, actor.sock connect), 자기 디렉터리 | Docker, `.env` 읽기(동작 순간), 자기 디렉터리, 자기 clone |
| E1/E2/E3 적용 | 해당 없음(항상 비특권) | E1(root) / E2(전용 계정 + ACL) / E3(전용 계정 + exec + `.env` ACL) |
| 침해 시 | 서명 없는 운영 동작 불가 | root 동등(모든 E) |
| 결정 | 계정 이름·uid·gid·그룹(미정) | E1/E2/E3 + 설치본·신뢰 목록 쓰기 가능 여부 |

- uid·gid·실제 권한은 결정하지 않는다.
- **X에 대한 E 비교의 요지(v0.3 공동 검토 유지):**
  - 모든 E에서 X RCE = root다.
  - E2·E3은 X의 제한 결함(A3b)이 설치본·신뢰 목록·소비 기록에 닿지 않게 할 수 있다. 단 소비·감사는 X 소유라 X 계정이 쓸 수 있다.
  - E1은 그것도 못 한다.

**CR-12의 X 기준 재평가:**
| 항목 | 내용 |
|---|---|
| 신뢰 목록 소유권 | root. X는 읽기만(E2·E3). E1이면 X가 쓸 수 있음 |
| 검증기 소유권 | X 설치본 = root 소유(E2·E3). E1이면 X가 변경 가능 |
| 설치본 무결성 | Thomas 설치 + Mac 해시 대조(탐지). 운영 증거 N-4·N-13 |
| 공개키 교체 | T2(서명된 변경) / T3(설치본 앵커, 권고 후보) / T4 |
| X 침해 시 | 모든 T 무력(Docker → root) |
| root 침해 시 | 모든 T 무력 |
| G 침해 시 | G 사본이 바뀌어도 X 판정 무관(N1의 실제 이득) |

## 부속 C. X 침해 위험과 N2 정적 비교 (v0.4)

### C.1 X 침해 시 항목별 분류
| 항목 | 분류 | 근거 |
|---|---|---|
| Docker 소켓 직접 사용 | **NOT_PROTECTED** | X는 정의상 Docker 접근 [DOC DEPLOYMENT.md] |
| 임의 컨테이너 실행 | **NOT_PROTECTED** | 같음. N2로 일부 제한 가능성 NOT_VERIFIED |
| 호스트 경로 마운트 | **NOT_PROTECTED** | `-v /:/host` 가능. N2로 일부 거부 가능성 NOT_VERIFIED |
| Secret 접근 | **NOT_PROTECTED** | X는 `.env`를 읽음. Docker로 컨테이너 env도 |
| 검증기 변경 | **DESIGN_DEPENDENT** | 제한 결함은 D-10(E2·E3 + root 소유 설치본)에 따라 보호, RCE는 NOT_PROTECTED |
| 신뢰 목록 변경 | **DESIGN_DEPENDENT** | 같음 + CR-12 |
| 소비 기록 변경 | **NOT_PROTECTED**(X 소유) | 체인·외부 앵커로 탐지만(NOT_VERIFIED) |
| 감사 파일 변경 | **NOT_PROTECTED**(X 소유) | 같음 |
| 승인 검증 생략 | **NOT_PROTECTED** | 검증과 실행이 같은 프로세스 |
| 운영 상태 변조 | **NOT_PROTECTED** | Docker로 상태 루트 mount 가능 |
| (대조) G 침해 시 서명 없는 운영 동작 | **N1_PROTECTED**(설계) | X 자기 검증. 운영 증거 C4·C6 → NOT_VERIFIED |

### C.2 N2 정적 검토 (Docker 실행·데몬 접근 없음)
- **저장소 사실 [CODE `docker-compose.yml`]:**
  - 상태 루트 bind 참조 14곳, `environment:` 블록 9개, `env_file` 0개.
  - privileged·`cap_add`·docker.sock·host network는 없다.
  - 그래서 X가 `compose up`을 하려면 **상태 루트 bind mount와 `.env` 값으로 보간된 환경을 가진 컨테이너 생성**을 허용해야 한다.
- **N2가 줄일 수 있는 것 [추론, 미검증]:** privileged, 임의 호스트 경로(`/` 등) mount, host 네임스페이스. 이것들은 compose 파일이 쓰지 않으므로 거부 규칙과 정상 동작이 충돌하지 않을 수 있다.
- **N2가 못 막는 것 [추론]:**
  - 허용된 mount(상태 루트)와 허용된 env를 가진 **공격자 이미지 컨테이너.** build 허용으로 이미지를 만들 수 있다.
  - 그 결과 Secret·운영 상태 노출과 변조가 가능하다.
  - 제한 계층 자체의 결함, 계층 설정의 소유자(root) 문제가 남는다.
- **판정:** N2는 "호스트 root로 가는 일부 경로 감소" 후보다. **X 침해 시 Secret·운영 상태 보호는 제공하지 않는다.** 효과·우회 가능성은 NOT_VERIFIED.

### C.3 추가 후보 정적 비교 (자동 승인 아님)
| 후보 | 내용 | X 침해 시 효과 | 비용 | D-1 |
|---|---|---|---|---|
| X 내부 최소화 | X 코드가 `runtime/`을 최소만 import, 입력은 actor 메시지 하나, 파서 최소 | RCE 가능성 감소(차단 아님) | 낮음 | 변경 없음 |
| 기능별 특권 helper 분리 | X를 `inspect`(읽기)·`build`·`deploy`(compose up)로 나눔. 각 helper는 자기 동작용 서명만 수락 | inspect helper 침해는 읽기 수준(Docker 읽기도 root 동등이라 소켓 접근 자체가 문제 → **이득 미미**). deploy helper는 여전히 root 동등 | 높음 | 대안 C 내부 변경(N1과 같은 성격) — 별도 승인 |
| N2 Docker API 제한 | C.2 | 일부 경로 감소 | 중간~높음 | 변경 없음(운영 설정) |
| 별도 호스트 배치(N4) | X·Docker를 다른 호스트로 | 이 호스트 Claude와 분리. 그 호스트의 X 침해는 여전히 그 호스트 root | 높음 | **D-1 변경 필요(대안 D) — 별도 결정** |
| rootless Docker(N3) | 런타임 전체 rootless | Docker 접근 ≠ 호스트 root. 같은 사용자의 데이터·Secret은 노출 | 운영 스택 전체 변경 | 운영 설계 변경, 별도 결정 |

- 어떤 후보도 "X 침해 시 Secret 보호"를 단독으로 제공하지 않는다. X가 `.env`를 읽고 Docker를 쓰는 한 그렇다.

## 명세 8. N-1–N-15 검증 계획 (CR-2)
단계: C3 합성 / C4 비운영 격리 / C6 운영 분리 후. 합성 PASS는 운영 격리 증거가 아니다.

| N | 위협 | 전제조건 | 합성 입력 | 예상 결과 | 검증 방법 | 증거 | 필요 권한 | 단계 |
|---|---|---|---|---|---|---|---|---|
| N-1 | Claude가 `.env`·사본 읽기 | `claude` 계정 | 가짜 `.env`(C4) | `PermissionError` | `os.access`·`open`, 성공 시 즉시 닫고 FAIL | 예외 종류만 | 비root 계정 | C4 → C6 |
| N-2 | Docker 소켓 | 같음 | — | `PermissionError` | `connect()`만 | 같음 | 같음 | C4 → C6 |
| N-3 | sudo | 같음 | — | 비0 종료, 프롬프트 없음 | `sudo -n true` | 종료 코드 | 같음 | C4 → C6 |
| N-4 | 실행기 코드·트리·감사 변경 | 실행기 설치, D-10 | — | `PermissionError` | 쓰기 시도 | 같음 | 같음 | C4 → C6 (D-10 대기) |
| N-5 | 실행기 프로세스 접근 | 같음 | — | 거부 | `/proc/<pid>/environ`, ptrace | 같음 | 같음 | C4 → C6 (D-10 대기) |
| N-6 | 상태 루트 쓰기·holdings·진단 접근 | 계정 | — | 거부 | 쓰기, `os.access`. **진단 디렉터리·Claude door 마운트 범위 확장(명세 10)** | 같음 | 같음 | C4 → C6 |
| N-7 | 임의 동작·키·경로 | 없음 | `{"verb":"exec"}`, `promote`에 `tree`, PR에 경로 | `VERB_NOT_PERMITTED`, `ARGUMENT_NOT_ACCEPTED`, `ARGUMENT_INVALID` | pytest, 가짜 `run` 호출 0 | 시험 결과 | 없음 | C3 → C4 |
| N-8 | 무승인 운영 동작 | 명세 1–3 | 승인 없음/서명 없음 | `APPROVAL_REQUIRED`/`SIGNATURE_INVALID`, Docker 0 | pytest. **합성 키는 tmp에서만 생성, 저장소·호스트 신뢰 목록에 넣지 않음** | 같음 | 없음 | C3 → C4 |
| N-9 | 만료 | 같음 | 지난 `expires_at`, 수명 초과 | `APPROVAL_EXPIRED`/`…LIFETIME…`, Docker 0 | 시계 주입 | 같음 | 없음 | C3 → C4 |
| N-10 | 결속 불일치 | 같음 | SHA·id·환경 하나씩 변경 | `APPROVAL_BINDING_MISMATCH`/`…ENVIRONMENT…`, Docker 0 | 가짜 측정값 | 같음 | 없음 | C3 → C4 |
| N-11 | 재사용 | 같음 | 같은 승인 두 번, 동시 두 요청 | `APPROVAL_ALREADY_CONSUMED`, 효과 1회 | 동시성 시험 | 같음 | 없음(C3), 소비 파일 권한(C4·C6) | C3 → C4 → C6 |
| N-12 | 실패 시 거부 | 없음 | Docker 실패·시간 초과·응답 스키마 위반·저장소 읽기 실패·감사 쓰기 실패 주입 | 모두 STOP, 다음 명령 0, 응답에 원문 없음 | 결함 주입 | 같음 | 없음 | C3 → C4 |
| N-13 | import 경로 오염 | 실행기 설치 | `claude` 쓰기 가능 경로에 같은 이름 모듈 | 실행기가 설치본 밖 import 없음 | unit의 `python -I`, cwd, `sys.path` 확인 | 설정 사본 | 비root + 설치 | C4 → C6 |
| N-14 | 검증 설정 변경 | 실행기 설치, D-2 설계 | — | `PermissionError` | `allowed_signers`·`revoked_*`·`consumed.jsonl`·unit 쓰기 시도 | 같음 | 같음 | C4 → C6 |
| N-15 | Guard 관리 설정 무력화 | D-13(위치) | — | 거부 | 관리 설정 쓰기 시도 | 같음 | 같음 | C5 이후 → C6 (D-13 대기) |

- **추가 합성 시험(C3, N 번호 아님):**
  - 합성 `.env` 값과 합성 Docker 출력이 응답·감사·예외에 나타나지 않음(`test_ops_deploy_preflight.py` 누출 검사 방식 [DOC]).
  - 모든 reason_code에 고정 문구가 있음.
  - 비정규 문서 거부.
  - `allowed_signers` 형식 위반 거부.
- **문서 번호 불일치(수정 금지, 위치만):**
  - §4.6 서두: "호스트 시험(N-1–N-6)… 실행기 시험(N-7–N-12)" — N-13–N-15를 빠뜨림.
  - §6 C4행: "N-1–N-12" 두 곳.
  - §6 C6행: "N-1–N-6"(범위)와 "N-1–N-12"(테스트).
  - §7 첫 체크박스: "N-1–N-12".

### 8.1 C2 확장 시험 후보 (v0.2 신설, N 번호와 별개, 정식 등록 아님)
라벨 `C2X-`는 설계 문서에 없는 접두어다. N-1–N-15는 그대로 둔다.

| ID | 위협 | 전제조건 | 합성 입력 | 기대 결과 | 실패 code | 증거 | 필요한 결정 | 단계 |
|---|---|---|---|---|---|---|---|---|
| C2X-01 | 신뢰 목록에 공격자 키 추가 | 명세 2 구현 | tmp 신뢰 목록에 다른 principal·`namespaces=` 없는 줄·`cert-authority` 줄 | 실행기 거부 | `TRUST_CONFIG_INSECURE` | pytest | — | C3 |
| C2X-02 | 같은 principal로 공격자 키 등록 | 명세 2 | tmp 합성 키 2개, 둘째 키를 같은 principal로 `allowed_signers`에 추가 | **v0.3: T2/T3이면 실행 전 거부.** T1이면 통과(=불충분의 증거)하고 감사 `signer_key_fingerprint`만 다름 | `TRUST_ANCHOR_MISMATCH`(T3) / `TRUST_CONFIG_UNAUTHORIZED_CHANGE`(T2) | pytest | CR-12 | C3, 파일 권한은 C4·C6 |
| C2X-03 | 신뢰 목록 해시 불일치 | 명세 2·7 | 검증 사이에 tmp 신뢰 목록 교체 | 두 감사 행의 `trust_config_sha256`이 다름, 각 판정은 자기 사본 기준 | — | pytest | — | C3 |
| C2X-04 | nonce 동시 소비 | 명세 3.2 | 같은 승인 N개 동시 요청(스레드·프로세스) | 정확히 1개 소비, 나머지 `APPROVAL_ALREADY_CONSUMED`, 가짜 `run` 효과 1회 | 같음 | pytest | — | C3 → C4 |
| C2X-05 | 효과 불명확 | 명세 3.3 C | 가짜 `run`이 효과 도중 예외, 재측정도 실패 | `EFFECT_STATE_UNKNOWN`, LATCH, 재전송도 UNKNOWN | `EFFECT_STATE_UNKNOWN` | pytest | 마감 시간 | C3 |
| C2X-06 | 소비 후 프로세스 종료 | 명세 3.3 F | CONSUMED 행 뒤 강제 종료를 흉내 내는 fixture(파일 상태 구성) → 재시작 | `RECONCILED_UNKNOWN`, LATCH, 운영 동작 거부 | `RUNNER_LATCHED` | pytest | LATCH 해제 방식 | C3 → C4 |
| C2X-07 | LATCH 후 재시작 | 명세 7.3 | LATCH 파일 있는 상태로 시작 / LATCH 파일 없이 대조 불일치 상태로 시작 | 둘 다 운영 동작 거부 | `RUNNER_LATCHED` / `RECONCILIATION_MISMATCH` | pytest | listen 거부 여부 | C3 |
| C2X-08 | 로그 회전 중 기록 실패 | 부속 A | 회전과 동시에 쓰기, 새 파일 생성 실패 주입 | 요청 로그: 응답 안 함. 진단: 고정 code. 행 유실 여부 측정 | `REQUEST_LOG_UNAVAILABLE` / `FACTORY_CHILD_DIAGNOSTICS_UNAVAILABLE` | pytest | R-08 수치 | C3(무손실은 NOT_VERIFIED 유지) |
| C2X-09 | 결과 조회 중복 | 명세 6.7 A | 같은 프레임 재전송 / 같은 id 다른 프레임 / 같은 승인 다른 id | 재생 응답 / `REQUEST_ID_REUSED` / `APPROVAL_ALREADY_CONSUMED`, 효과 1회 | 같음 | pytest | 응답 모델 | C3 |
| C2X-10 | 새 일정 경로 누락 | 명세 10 | 표지 있음 + 새 파일 없음 / 옛·새 둘 다 / 표지 없음 + 새 파일만 | scheduler 시작 거부 | `SCHEDULE_LAYOUT_MISSING` / `…CONFLICT` / `…UNKNOWN` | pytest | P1/P2/P3 | C3 → C4 |
| C2X-11 | `os.replace` 뒤 권한 변경 | 명세 10 | umask 077·000에서 교체 쓰기 | 교체 뒤 모드·그룹이 정책값 | — | pytest(POSIX) | P1 | C3 → C4 |
| C2X-12 | 잘못된 인터프리터·의존성 해시 | 명세 9 | 다른 버전 문자열 / 해시가 다른 wheel | 실행기 시작 거부 / 설치 거부 | `RUNNER_ENVIRONMENT_MISMATCH` / pip 해시 오류 | pytest + 설치 절차 시험 | Python 대안 | C3(코드) → C4(설치) |
| C2X-13 | 신뢰 목록이 설치본 앵커와 다름 | CR-12 T3 선택 시 | tmp 앵커와 다른 `allowed_signers` | 시작·검증 거부 | `TRUST_ANCHOR_MISMATCH` | pytest | CR-12 | C3 → C4 |
| C2X-14 | 승인 수명이 동작 마감보다 짧음 | §1.5 | `expires_at − now < deadline` | 소비 전 거부(CASE A) | `APPROVAL_TOO_SHORT_FOR_ACTION` | pytest(시계 주입) | CR-10·CR-19 | C3 |
| C2X-15 | 시계 역행·점프 | §1.5 | `last_seen_wall`보다 이른 wall, wall·mono 차이 주입 | 새 효과 거부 | `CLOCK_UNTRUSTED` | pytest | CR-11 | C3 |
| C2X-16 | LATCH 파일 삭제만으로 복구 시도 | §7.6 | UNKNOWN 기록 + LATCH 파일 없음 + 해제 행 없음 | 재시작 대조가 LATCH 재생성, 모든 요청 거부 | `RUNNER_LATCHED` | pytest | CR-17 | C3 |
| C2X-17 | 위조·불일치 해제 문서 | §7.6 | 다른 키로 서명 / 다른 `latch_id` / 만료 | 거부, LATCH 유지 | `SIGNATURE_INVALID` / `APPROVAL_BINDING_MISMATCH` / `APPROVAL_EXPIRED` | pytest | CR-17 | C3 |
| C2X-18 | 실행기 import가 상태 루트를 건드림 | §0-7 | 상태 루트 경로 접근을 가로채는 fixture | 실행기 import·시작 중 접근 0 | — | pytest | — | C3(N-13은 C4·C6) |
| C2X-19 | LATCH 상태 확인 불가 | §7.3a | LATCH 경로 stat 오류 주입(EACCES 등) | 모든 요청 거부 | `RUNNER_STARTUP_BLOCKED` | pytest | — | C3 |

- C3 합성 PASS는 C4·C6 증거를 대신하지 않는다. 파일 권한·계정·실제 Docker 관련 결론은 C4·C6에서만 나온다.

### 8.2 N1 확장 시험 후보 (v0.4, 계획만. N 번호·C2X와 별개 라벨 `N1X-`)
| ID | 위협 | 전제 | 합성 입력 | 기대 결과 | code | 단계 |
|---|---|---|---|---|---|---|
| N1X-01 | G 침해 → 서명 없는 운영 요청 | X 구현 | G uid로 승인 없는 promote | 거부, Docker 0 | `APPROVAL_REQUIRED` | C3 |
| N1X-02 | G uid 탈취 → 위조·변조 메시지 | 같음 | args 변경, 다른 문서 바이트, 지문 조작 | X 자기 검증으로 거부 | `APPROVAL_BINDING_MISMATCH`/`SIGNATURE_INVALID` | C3 |
| N1X-03 | Claude → X 직접 호출 | 두 uid | Claude uid로 actor.sock | 거부 | `ACTOR_PEER_NOT_PERMITTED` | C3(uid 흉내) → C4 → C6 |
| N1X-04 | 승인 재사용 | 같음 | 같은 승인 두 번, 다른 id | 1회만 | `APPROVAL_ALREADY_CONSUMED` | C3 |
| N1X-05 | genesis 누락 | 소비 저장소 | 파일 없음, 첫 행 없음 | 시작 거부 | `STORE_GENESIS_MISSING` | C3 |
| N1X-06 | `store_id` 불일치 | 같음 | 감사와 다른 `store_id` | 시작 거부 | `STORE_ID_MISMATCH` | C3 |
| N1X-07 | 소비 기록 일부 손실 | 같음 | 중간 행 삭제·끝 행 삭제 | 체인·교차 대조로 거부. 끝 행+감사 동시 삭제는 탐지 불가(기대: 문서화된 한계) | `STORE_CHAIN_BROKEN`/`RECONCILIATION_MISMATCH` | C3 |
| N1X-08 | 감사 일부 손실 | 같음 | 감사 행 삭제 | LATCH | `AUDIT_CHAIN_BROKEN` | C3 |
| N1X-09 | G/X 기록 불일치 | 같음 | G FORWARDED만 / X만 | 성공 추정 없음, 경보 | — | C3 |
| N1X-10 | G 응답 유실 | 같음 | X 완료 후 G→Claude 끊김 | 재전송 → 재생 | — | C3 |
| N1X-11 | X 효과 후 G 장애 | 같음 | 효과 중 G 종료 | X 완료·기록, 재전송 재생 | — | C3 |
| N1X-12 | X 재시작 | 같음 | CONSUMED 뒤 종료 상태 파일 | UNKNOWN + LATCH | `EFFECT_STATE_UNKNOWN` | C3 → C4 |
| N1X-13 | LATCH 중 소켓으로 복구 문서 | 같음 | LATCH + 소켓 요청에 해제 문서 | 거부(소켓은 복구 경로 아님) | `RUNNER_LATCHED` | C3 |
| N1X-14 | 복구 서명 위조 | §7.6 | 다른 키·승인 namespace로 서명 | 거부, LATCH 유지 | `RECOVERY_DOCUMENT_INVALID` | C3 |
| N1X-15 | 복구 문서 replay | §7.6 | 처리된 문서 재투입, 다른 `latch_id` | 거부 | `RECOVERY_DOCUMENT_REPLAYED` | C3 |
| N1X-16 | 신뢰 목록 변조 | CR-12 | G 사본 변조 / X 목록 변조 | G 쪽: X 판정 무관. X 쪽: CR-12 선택에 따름 | `TRUST_ANCHOR_MISMATCH`(T3) | C3 → C4·C6 |
| N1X-17 | X 설치본 변경 | D-10 | X 계정으로 설치본 쓰기 | E2·E3: 거부. E1: 가능(기대: 문서화된 한계) | — | C4 → C6 |
| N1X-18 | X 침해 시 Docker 권한 상승 | — | (합성 불가) | **NOT_PROTECTED 확인용 문서화.** 시험으로 "안전"을 증명하지 않는다 | — | C4(비운영 Docker에서 위험 시연만, 별도 승인) |
| N1X-19 | 수신함 symlink·교체 | §7.6 | symlink 문서, hard link, 다른 소유자 | 처리 안 함 | `RECOVERY_INBOX_UNSAFE` | C3 |
| N1X-20 | 복구 증거 불일치 | §7.6 | COMPLETE인데 실측 이미지 다름 | 거부, LATCH 유지 | `RECOVERY_EVIDENCE_MISMATCH` | C3 |

- C3 합성 PASS는 C4·C6 권한 분리 증거를 대신하지 않는다. N-1–N-15는 그대로다. N-2·N-4·N-5·N-13·N-14는 G·X 각각에 대해 실행한다.

### 8.3 v0.5 GAP 시험 후보 (계획만)
| ID | 입력 | 기대 결과 | code(응답 / 감사) | 단계 |
|---|---|---|---|---|
| GAPX-01 | 저장소 정상 상태에서 TYPE B 문서 투입 | 처리 안 함(A namespace로 검증 → 실패) | — / `RECOVERY_DOCUMENT_INVALID` | C3 |
| GAPX-02 | 저장소 비정상 상태에서 TYPE A 문서 투입 | 처리 안 함, STARTUP_BLOCKED 유지 | `RUNNER_STARTUP_BLOCKED` / `RECOVERY_DOCUMENT_INVALID` | C3 |
| GAPX-03 | TYPE B, 외부 앵커 없음·불일치 | 거부 | — / `REINIT_EVIDENCE_INSUFFICIENT` | C3 |
| GAPX-04 | TYPE B, 감사 체인 손상 | 거부 | 같음 | C3 |
| GAPX-05 | TYPE B, `imported_consumption_count` 불일치 | 거부 | 같음 | C3 |
| GAPX-06 | TYPE B, 저장소 손실 + 미해결 UNKNOWN | 거부(복구 경로 없음) | 같음 | C3 |
| GAPX-07 | 재초기화 뒤 컷오프 이전 승인 | 거부 | `APPROVAL_BEFORE_CUTOFF` | C3 |
| GAPX-08 | 같은 `old_store_id`로 두 번째 TYPE B | 거부 | — / `REINIT_ALREADY_PERFORMED` | C3 |
| GAPX-09 | 재초기화 뒤 이관된 옛 승인 재사용 | 거부. 컷오프 검사(§2.4 7단계)가 소비 검사(9단계)보다 먼저라 code는 하나 | `APPROVAL_BEFORE_CUTOFF` | C3 |
| GAPX-10 | G 접수 기록 조작(FORWARDED 삭제·추가) | 대조 도구가 불일치 보고, X 판정 무변화 | — | C3(도구 채택 시) |
| GAPX-11 | X가 G 디렉터리 읽기 시도 | 권한 거부 | — | C4 → C6 |
| GAPX-12 | LATCH 중 모든 소켓 요청(inspect 포함) | 거부 | `RUNNER_LATCHED` | C3 |

## 명세 9. Python 실행 환경 (v0.2 개정)

### 9.1 사실
- 런타임 이미지는 `python:3.12-slim@sha256:57cd7c3a…`로 digest 고정이다 [CODE]. 그 digest의 정확한 패치 버전은 이번에 확인하지 않았다.
- CI는 `python-version: "3.12"`(마이너만)이다 [CODE]. 이 세션에서 읽은 CI 기록은 linux 3.12.15, windows 3.12.10이었다 [CI]. 즉 **CI는 패치 버전을 고정하지 않는다.**
- 의존성: `requirements-runtime.txt`·`requirements-validation.lock`은 `==`로 고정돼 있지만 `--hash`는 0건이다 [CODE].
- 호스트 venv는 3.14다(CLAUDE.md). 호스트의 다른 Python 설치 여부는 이번에 확인하지 않았다.
- 결론: **CI가 3.12라는 사실만으로 미래 실행기 설치 환경이 검증됐다고 할 수 없다.**

### 9.2 A+C를 유지하기 위한 10개 조건
| # | 조건 | 지금 충족? |
|---|---|---|
| 1 | 정확한 버전 고정(3.12.N) | 아니오. CI·설치 모두 정해야 함 |
| 2 | 의존성 버전 고정 | 예(`==`) |
| 3 | 의존성 해시 검증 | **아니오.** 해시 잠금 파일 + `pip --require-hashes` 필요(새 파일) |
| 4 | 설치 재현성 | 인터프리터 출처·빌드 방식에 달림. NOT_VERIFIED |
| 5 | 3.12 CI 검증 | 마이너 단위로만 |
| 6 | 설치 환경과 CI 일치 | 아니오. 실행기 시험 job은 설치와 같은 3.12.N을 써야 함(`python-version: "3.12.N"`) |
| 7 | 보안 업데이트 | 3.12는 보안 수정만 받는 단계라 패치 고정과 갱신 주기가 충돌한다 → 갱신 절차(아래 #8) 필요 [추론, 지원 일정 미확인] |
| 8 | 환경 변경 시 재검증 | 인터프리터·의존성 변경 = 새 설치본 = 새 SHA 검토 + C3 시험 재실행 |
| 9 | 권한·설치 경로 분리 | 인터프리터·venv는 `/opt/thomas-deploy/<sha>/` 아래 root 소유. 실행기 계정은 쓰기 불가 |
| 10 | rollback 호환 | 설치본이 SHA별 디렉터리이므로 이전 설치본으로 unit을 되돌림. 소비·감사 파일 형식은 버전 간 호환이 필요(형식 버전 필드) |

### 9.3 대안
| 대안 | 내용 | 장점 | 위험 |
|---|---|---|---|
| A+C(권고 유지) | 설치본에 3.12.N 인터프리터 + 해시 잠금 venv | 호스트 Python과 분리, CI와 같은 마이너 | 인터프리터 출처 결정, 해시 잠금 새 파일 |
| B | 호스트 3.14 + CI 3.14 job 추가 | 설치가 쉬움 | 두 버전 유지, 호스트 업그레이드에 끌려감 |
| C-alt [추론] | 실행기를 digest 고정 이미지(런타임 base와 같은 계열)로 묶어 systemd가 실행 | 런타임 이미지와 같은 Python, 재현성 | docker.sock 마운트 = root 상당(E1과 같음), `.env`를 컨테이너에 넣어야 함, 실행기 이미지를 무엇으로 빌드할지 순환 문제 |

- 실행기는 시작할 때 자기 인터프리터 버전과 venv 해시 목록 지문을 설치본 기대값과 비교하고, 다르면 `RUNNER_ENVIRONMENT_MISMATCH`로 시작을 거부한다. `RUNNER_START` 감사 행에도 남긴다.
- **최소 CI 매트릭스(제안):**
  - 기존 필수 5개 그대로
  - + 실행기 시험을 설치와 같은 `3.12.N`으로 도는 ubuntu job(필수 여부는 Thomas 결정. 필수로 하면 Q14의 필수 체크 목록이 바뀜)
  - Windows skip 상한 영향은 C3 PR에서 실측한다.

### 9.4 CR-8·CR-20 결정 — DESIGN_DECISION_ACCEPTED (조건부)
**CR-8(A+C) 조건:**
1. 정확한 패치 버전 3.12.N 지정(값은 C3 전 결정)
2. 의존성 `==` 고정(현재 충족)
3. 해시 검증(`--require-hashes`용 잠금 파일. 현재 `--hash` 0건 [CODE] → 새로 필요)
4. 재현 가능한 설치 절차(인터프리터 출처 포함, C3 전 결정)
5. CI와 실행 환경의 버전 일치(실행기 시험 job = 같은 3.12.N)
6. 시작 시 환경 지문 확인(`RUNNER_ENVIRONMENT_MISMATCH`)
7. 보안 업데이트 시 재검증(인터프리터·의존성 변경 = 새 설치본 = 재검토·재시험)
8. Claude가 설치본을 바꾸지 못하는 구조(root 소유 `/opt/thomas-deploy/<sha>/`, 운영 검증 N-4)

- 현재 CI의 3.12 통과는 미래 실행기 환경의 검증이 아니다.
- 정확한 버전·설치 방법: DECISION_DEFERRED(C3 전).

**CR-20(CI 범위) 조건:**
- 실행기 시험은 **실제 운영 실행 버전과 같은 버전**의 CI에서 필수로 돈다. 필수 체크 목록(Q14) 변경은 별도 결정이다.
- 실행기가 3.12로 고정되면 3.14 CI는 비필수 호환성 검사 후보다.
- 운영 실행기가 3.14를 쓰게 되면 3.14 검증은 필수다.
- 이번에 CI workflow를 바꾸지 않는다.
- **v0.2와의 차이:** v0.2는 "실행기 시험 job 비필수로 시작"을 권고했다. v0.3은 Thomas 조건에 따라 "운영 실행 버전과 같은 버전의 실행기 시험은 필수"로 고쳤다.

### 9.5 N1: G·X 실행 환경 (v0.4)
- CR-8 조건(정확한 3.12.N, 해시 잠금, CI 일치, 환경 지문, 설치본 보호)은 **G와 X 모두**에 적용한다.
- 같은 설치본·같은 인터프리터를 쓰는 것을 기본 후보로 둔다. 각자 환경 지문을 검사한다.
- X는 import를 최소화한다: 승인 검증·소비·감사·고정 argv 외의 `runtime.mvp_runtime` 모듈을 피한다. 상태 루트 기본값을 건드리지 않음을 증명한다(C2X-18 확장: G·X 각각).
- Python 버전·설치 방법은 DECISION_DEFERRED(v0.3과 같음).

## 명세 10. Claude door 데이터 격리 (v0.2 개정, 보완 ③)

- **CR-15 = DECISION_DEFERRED (v0.3).** P1은 우선 설계 후보로 유지하고, 최종 채택은 보류한다. 아래 필수 조건은 어느 방식을 고르든 설계에 남는다:
  - 이전 상태 표지
  - 누락 파일 STARTUP_BLOCKED
  - 정상 빈 일정과 이전 실패의 구분
  - 원자적 교체, 명시적 파일 모드, 디렉터리 fsync
  - 롤백 호환성, 두 단계 이전
- 파일 이동, compose 변경, f2 변경은 승인되지 않았다.

- **상태:** P1(전용 데이터 디렉터리)은 **우선 설계 후보**다. 최종 확정이 아니다. P2(공개 사본)·P3(현행 + 전용 uid)도 비교 대상으로 남긴다.
- 파일 이동, 코드 변경, compose 변경, f2 변경, 권한 변경은 승인되지 않았다. f2 변경은 별도 거버넌스 승인 대상이다.

### 10.1 사실 [CODE]
- `schedules` → 상태 루트 바로 아래 `schedules.jsonl`. 쓰기는 `ScheduleStore._save` → `jsonl.write_objects`다.
  - 같은 디렉터리의 `schedules.jsonl.tmp`를 `open("w")`로 만든다 → 모드는 umask를 따른다 → `os.replace`.
  - **`fsync`가 없다.**
  - 잠금 파일은 `.schedules.lock`이다.
- `jsonl.read_objects`는 파일이 없으면 `[]`를 돌려준다. 따라서 경로가 틀리면 "일정 없음"으로 조용히 읽힌다.
- `scheduler_events` → `runtime_ledger/scheduler_events.jsonl`. tail·count만 읽는다. 원장 회전은 `retention.py`가 `os.replace`와 `archive/`로 한다.

### 10.2 전환 상태 정의 (P1 기준, v0.2 신설)
P1은 표지 파일 `door_public/LAYOUT`(내용: `{"layout": "door_public.v1", "migrated_at": …}`)으로 상태를 구분한다.

| 상태 | 옛 `schedules.jsonl` | 표지 | 새 `door_public/schedules.jsonl` | 판정 | scheduler |
|---|---|---|---|---|---|
| L-legacy | 있음 | 없음 | 없음 | 이전 전 | 옛 배치 코드만 시작. 새 배치 코드는 `SCHEDULE_LAYOUT_UNKNOWN`으로 거부 |
| L-fresh | 없음 | 없음 | 없음 | 최초 초기화 | 초기화 절차만이 표지와 빈 파일을 **명시적으로** 만든다. 일반 시작은 거부(`SCHEDULE_LAYOUT_MISSING`) |
| L-migrated | 없음 | 있음 | 있음(비어 있어도 됨) | 정상 | 시작. 빈 파일 = 정말로 일정 없음 |
| L-broken-1 | 없음 | 있음 | **없음** | 이전 실패 | **STARTUP_BLOCKED**(`SCHEDULE_LAYOUT_MISSING`) — 빈 목록으로 읽지 않음 |
| L-conflict | 있음 | 있음 또는 없음 | 있음 | 두 진실 | STARTUP_BLOCKED(`SCHEDULE_LAYOUT_CONFLICT`) |
| L-broken-2 | 없음 | 없음 | 있음 | 표지 누락 | STARTUP_BLOCKED(`SCHEDULE_LAYOUT_UNKNOWN`) |

- 핵심 규칙: 새 배치 코드에서 "파일 없음"은 L-migrated 안에서만 오류이고 빈 목록이 아니다. 빈 일정은 **빈 파일**로만 표현한다.
- `scheduler_events.jsonl`도 같은 표지 아래 같은 규칙을 따른다. 이벤트는 없을 수 있으므로, 표지가 있으면 빈 파일로 시작한다.

### 10.3 10개 계약
| # | 계약 | 설계 |
|---|---|---|
| 1 | 새 경로 존재 | 표 10.2. 표지가 있으면 새 파일은 반드시 있어야 함 |
| 2 | 이전 완료 여부 | 표지 = 완료. 이전 절차의 마지막 단계가 표지 쓰기(fsync, 디렉터리 fsync) |
| 3 | 옛·새 충돌 | L-conflict → 거부. 자동 병합 없음 |
| 4 | 새 경로 누락 | L-broken-1 → STARTUP_BLOCKED |
| 5 | 이전 실패 | 이전 절차는 scheduler 정지 상태에서만. 중간 실패 = L-broken·L-conflict 중 하나 → 시작 거부 → 운영자 판단 |
| 6 | 기존 scheduler 호환 | **두 단계 배포 필요:** 릴리스 N = 표지를 알지만 옛 경로를 읽고, 표지가 있으면 시작 거부. 릴리스 N+1 = 이전 + 새 경로. 이렇게 해야 N+1 → N 롤백 때 옛 코드가 "파일 없음 = 빈 일정"으로 조용히 정지하지 않는다. **지금 운영 이미지(이전 코드)는 표지를 모르므로, N을 먼저 배포하지 않고 이전하면 롤백이 안전하지 않다** |
| 7 | 읽기 최신성 | 디렉터리 bind mount 안의 `os.replace`는 새 inode를 보여 준다(파일 단위 mount는 금지) |
| 8 | `os.replace` 뒤 inode | 읽는 쪽은 경로로 다시 연다(매 요청). 열린 fd를 오래 들고 있지 않는다 |
| 9 | 교체 중 읽기 일관성 | `os.replace`는 원자적이므로 읽는 쪽은 옛 파일 전체 또는 새 파일 전체를 본다. tail 읽기는 연 fd 기준으로 일관된다 |
| 10 | rollback 경로 일관성 | #6의 두 단계 + 롤백 절차에 "표지 상태 확인"을 넣음. 옛 배치로 되돌리려면 역이전 절차(새 → 옛, 표지 삭제)가 별도로 필요 |

### 10.4 원자적 교체 절차 요구사항 (새 배치에서 `write_objects` 대체 또는 보강)
1. 임시 파일: `O_CREAT|O_EXCL|O_NOFOLLOW|O_WRONLY`, **명시 모드 0640**(umask 영향 제거를 위해 생성 뒤 `fchmod`).
2. 그룹: 디렉터리 setgid + door 전용 gid. 생성 뒤 `fstat`으로 gid 확인, 다르면 거부.
3. 내용 쓰기 → `fsync(tmp)` → `os.replace` → `fsync(디렉터리)`.
4. 교체 뒤 `fstat`(경로 기준 재오픈)으로 모드·소유·그룹 확인. 다르면 운영 경보(쓰기는 이미 끝남).
5. 실패 시 임시 파일 삭제, 원본 유지.
- 이 요구는 현재 `write_objects`(fsync 없음, umask 의존)보다 강하다. 바꾸면 그 함수를 쓰는 다른 저장소에도 영향이 있다. 영향 범위는 C3에서 조사한다.

### 10.5 격리 범위
- **door 마운트:** `door_public/`만 `:ro`.
- **마운트 밖:** `diagnostics/`, holdings, `runtime_ledger/`의 다른 원장·`archive/`, 상태 루트의 나머지.
- **원장 회전과의 관계:** `scheduler_events.jsonl`의 원장 회전 archive는 `door_public/` 밖에 둔다. retention이 디렉터리를 건너 rename해야 하므로 같은 파일시스템이어야 한다.
- **전용 uid:** door 프로세스는 10001이 아닌 uid, 그룹은 door gid. 0600 파일(진단 등)이 실수로 마운트돼도 열리지 않는다.
- **read 범위:** 허용 2개(`schedules`, `scheduler_events`) 유지. 보류 4개 활성화 금지.
- **비운영 부정 시험(C4):** door 안에서 `diagnostics/`·holdings·다른 원장에 대한 `os.access`가 거부됨. 두 read는 `ok`. 교체 쓰기 뒤 최신 값을 봄. 전환 상태 6가지에서 scheduler 시작 판정.

### 10.6 P1/P2/P3 비교 (v0.2)
| | P1 전용 디렉터리 | P2 공개 사본 | P3 현행 + uid |
|---|---|---|---|
| 노출 | 두 파일만 | 투영 사본만(가장 작음) | 상태 루트 전체가 파일시스템에 보임(모드로만 보호) |
| 코드 변경 | 경로 2개, 전환 상태 검사, 원자적 교체 보강, 두 단계 배포 | 새 writer(Reuse-first 판단), 사본 일관성 | 없음 |
| 실패 위험 | 이전 실패 → 일정 정지(10.2로 시작 거부로 바꿈) | 사본 지연·불일치 | 모드 실수 하나로 노출 |
| 롤백 | 두 단계 필요 | 사본만 지우면 됨 | 해당 없음 |
- 권고: P1을 우선 설계 후보로 유지한다. 다만 두 단계 배포 비용이 확인됐으므로, P2와의 최종 비교는 C3 전 결정 항목으로 둔다.

## 미결정 항목 (v0.5)
- **C3 전 결정(C2 완료와 별개):**
  - D-10, CR-12(TYPE B 전용 키 포함)
  - D-3 C3 범위
  - CR-8, CR-10·11·19 값(복구 문서 유효기간 포함)
  - CR-13, CR-15
  - CR-17 실제 복구 인터페이스(위치·소유권·권한·운영 절차·구현)
  - N2
  - 대조 도구 채택 여부, 시계 동기화 출처
- **운영 판단:** X 침해 위험 수용 여부(보류).
- **설정 값(스키마는 닫힘, 값은 C3 전 확정):** namespace 문자열 3종, principal, `host_label`, 유효기간·오차·마감, inspect 속도 제한.

## 명세 간 정합성 (v0.5)
| 점검 | 결과 |
|---|---|
| N1 G/X 분리, G에 Docker·Secret·판정 없음 | 유지 |
| X 독립 검증·소비 단일 소유 | 유지 |
| 저장소 손실 → STARTUP_BLOCKED, 새 genesis만으로 재개 금지 | 유지·강화(§3.5) |
| UNKNOWN 자동 재실행 금지, LATCH 단순 삭제 해제 금지 | 유지 |
| TYPE A/B 권한 분리 | 반영(§1.7, §2.8, §3.5, §7.6) |
| G/X 기록 독립 보관, X는 G 기록 불가 | 반영(§7.3-10, §7.9) |
| 외부 감사 증거 한계 | 유지(NOT_VERIFIED) |
| X 침해 = root 동등 | 유지(NOT_PROTECTED, 수용 안 됨) |
| D-10·CR-12 보류, Option A, read-bridge BLOCKED, C3 미승인, HOLD | 유지 |
| 응답 code 하나의 실패 = 하나의 code | 반영(§5.2 통합) |
| 남은 DESIGN_GAP | **없음**(판정은 `C2_V0.5_COMPLETION_GATE.md`) |
