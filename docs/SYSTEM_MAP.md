# Thomas Agent 시스템 지도

**상태:** 안내 문서(Navigation only). 2026-10-26, main `c8a77799` 기준.
**권위:** 없음. 이 문서는 무엇이 어디 있는지만 가리킨다. 규칙은 `CLAUDE.md`, 권한은
`governance/GOVERNANCE_POLICY.yaml`, 코드 소유권은 [`ACTIVE_ARCHITECTURE.md`](ACTIVE_ARCHITECTURE.md)에 있다.
이 문서와 그 문서들이 다르면 그 문서들이 맞다.
**독자:** Thomas, 그리고 이 저장소에서 처음 일을 시작하는 Claude 세션. 처음이라면 이 문서 → `CLAUDE.md` →
일할 영역의 소유 문서 순서로 읽는다.
**갱신:** 서비스, 레인, 채널, 저장소 밖 부품이 생기거나 없어질 때 같은 PR에서 고친다. 운영 수치(보유자산,
전략 성과)는 이 문서에 적지 않는다.

---

## 1. 한 단락 요약

Thomas 한 사람이 한 서버에서 돌리는 **거버넌스 우선 자율 에이전트**다.
- **원칙:** 행동은 계약(YAML/Markdown + 닫힌 JSON Schema)이 정한다. 런타임은 검증된 입력을 순서대로 실행할 뿐이고, 승인으로 켜기 전에는 아무것도 동작하지 않는다.
- **지금 하는 일:** 사업 아이디어 분석과 콘텐츠 작성, 크립토 전략 연구와 페이퍼 트레이딩, 보유자산 보드, 블로그 운영 지원, 주간 경영 보고.
- **실행 단계:** 크립토는 PAPER다. 실주문은 단계와 라이브 게이트가 막고 있다.

## 2. 목표와 지금

| | 무엇 | 소유 문서 |
|---|---|---|
| 조직 목표 | 회사형 구조. Thomas Core → Prime(조정자) → 작업별 전문 역할 → 검증 → 기억. 필요한 최소 역할만 그때그때 켠다 | [`THOMAS_AUTONOMOUS_ORGANIZATION_ARCHITECTURE.md`](THOMAS_AUTONOMOUS_ORGANIZATION_ARCHITECTURE.md) |
| 투자 목표 | 7계층(자산 파악 → 시장 → 전략 → 위험 → 실행 → 재배분 → 피드백)과 증거 관문 | [`proposals/THOMAS_AGENT_TARGET_ARCHITECTURE_V0.1.md`](proposals/THOMAS_AGENT_TARGET_ARCHITECTURE_V0.1.md) (DRAFT) |
| 지금 | 단일 작업 흐름(요청 → Prime → 전문 역할 → 검증 → 감사 → 기억)은 지어져 있다. 크립토는 PAPER | 이 문서 §4–§7 |

정해진 날짜와 관문:
- 첫 forward cohort 마감과 연구 에포크 경계는 **2027-03-22**다. 판정 규칙은 이때만 완화된다(`CLAUDE.md`).
- LIVE_AUTONOMOUS는 **첫 FORWARD_CONFIRMED 계보가 생길 때까지** 올리지 않는다(D5).
- 비크립토 레인의 증거 기한(D8)은 **2026-11-25**다(`proposals/SYSTEM_REVIEW_IMPROVEMENT_PLAN_V0.1.md`).

**2026-10-25 점검에서 빈 곳으로 확인된 것** (회사형 설계 대비):
- 작업을 `THOMAS_GOALS.yaml`과 잇는 단계가 없다.
- 피드백은 기록만 되고 다시 읽히지 않는다.
- 스케줄로 돈 분석은 결과를 보내지 않는다.

정기 경영 보고는 같은 날 `management_report`로 채웠다(§5). 나머지 둘은 `REMAINING_WORK.md` §E에 있다.

## 3. 한 장 그림

```text
                              Thomas
               ┌────────────────┼─────────────────────┐
         관제봇(명령·승인)   알림봇(알림만)        Thomas 터미널(root)
               │                │                     │ 배포·REBIND·보유자산 표
               ▼                │                     ▼
        ┌─ operator ─┐          │              docker compose (5 서비스)
        │ /approve 등 │◀── 주간 보고(관제봇, scheduler-maint가 보냄)
        └─────┬──────┘          │
              │ 분석 요청        │ 실패·복구·감시 알림
              ▼                 │
   pipeline (Prime → 역할 → 검증 → 감사 → 기억)
              ▲                 │
   scheduler(risk) ── scheduler-maint ──▶ pipeline-worker (모델·검색·Naver 키)
     크립토 사이클        팩토리·보고·보유자산·정리
              │
     .runtime_governance_state/  (원장·승인·스케줄·제어 상태 — 서비스가 함께 마운트)
              │
   claude-read-bridge ──▶ Claude(uid 2101): 투영된 읽기 두 가지만

   저장소 밖: 볼트(obsidian-thomas) · 서버 스크립트(~/.claude/scripts) · Mac 루틴 · /root/backups · 배포 러너(C6a)
```

## 4. 조직: 역할

정본은 `03_ROLE_CONTRACTS/ROLE_REGISTRY.yaml`이다.

| 역할 | 상태 | 하는 일 |
|---|---|---|
| `thomas.prime` | active, 라우팅 대상 아님 | 계획·역할 선택·통합(`prime.py`, `planner.py`) |
| `conversation.frontdesk` | active, 라우팅 대상 아님 | 텔레그램 대화 창구(`frontdesk.py`) |
| `general.specialist` | active | MVP 기본 역할: 사업 아이디어 분석 |
| `research.general` · `translation.general` · `content.general` · `development.general` | active | 전문 역할 |
| `validation.independent` | active | 독립 검증 |
| `business.analysis` · `execution.live_trader` | candidate | 켜지 않음 |

새 역할과 부서는 조직 설계 §12–§16의 기준을 넘어야 생긴다.

## 5. 런타임: 서비스 5개

모두 `thomas-agent-runtime` 한 이미지에서 돈다. 정본은 `docker-compose.yml`이고, 서비스별 비밀 경계는 [`DEPLOYMENT.md`](DEPLOYMENT.md)
"Secret boundary" 표가 맡는다.

| 서비스 | 하는 일 | 쥔 것 |
|---|---|---|
| `operator` | 관제봇을 폴링하고, 등록된 운영자를 확인하고, 분석 요청을 처리한다. `/approve`·`/kill` 등 명령, 승인 요청 알림 | 관제봇 토큰, 모델 키 |
| `scheduler` (risk) | 크립토 사이클, 브레이커·경로 감시. **돈 경로** | 거래소 키, 라이브 변수 |
| `scheduler-maint` (maintenance) | 팩토리, null control, 캔들 아카이브, 리포트, 데이터 리뷰, 보유자산 새로고침, 정리·회전, 주간 경영 보고 | 읽기 전용 거래소·증권 키, 시장 데이터 키, 관제봇 토큰(보고 전용 이름) |
| `pipeline-worker` | 위임받은 분석과 job(데이터 리뷰, 전략 제안, 콘텐츠 아이디에이션) 실행 | 모델·검색·Naver 키 |
| `claude-read-bridge` | Claude 계정의 읽기 전용 문(`claude` 프로필) | 없음(상태 루트를 읽기 전용으로 마운트) |

**스케줄 종류 16개**(`scheduler.KINDS`):
- **risk 3개:** `crypto_pipeline`, `crypto_breaker_watch`, `crypto_route_watch`
- **maintenance 13개:**
  - 크립토: `crypto_factory`, `crypto_null_control`, `crypto_forward_cohort`, `crypto_propose`, `crypto_data_review`, `crypto_report`, `candle_archive`
  - 보유자산: `holdings_refresh`
  - 분석·콘텐츠: `analysis_task`, `content_ideation`
  - 정리: `memory_prune`, `ledger_rotate`
  - 보고: `management_report`

종류가 어느 레인에 속하는지는 테스트가 강제한다.

## 6. 레인

레인은 코어의 관문(PermissionDecision, Safety-Flag Gate, 감사 체인)을 쓰는 응용이다. 증거가 없으면 통째로 제거한다
(`predmarket/` 2026-08-02, `knowledge/` 2026-10-25).

- **크립토** (`crypto/`)
  - **흐름:** 연구 팩토리(가설 → 생성 → holdout) → forward cohort(동결 후 180일에 판정, `COHORT_LIFETIME_DAYS`) → 승격 문 → 페이퍼 → (관문 뒤) 라이브.
  - **실행 단계:** `python -m scripts.register_execution_stage --show`로 본다. 2026-10-26 기준 PAPER, 정책 1.7.0에 묶여 있다.
  - **라이브 준비 상태:** `live_readiness`로 본다.
  - **규칙:** Claude는 돈 경로를 실행하지 않는다.
  - **소유 문서:** `docs/runtime-contracts/CRYPTO_*`, `EXECUTION_STAGE_V0.1.md`, `REMAINING_WORK.md` §C·§F·§L.
- **보유자산** (`holdings/`)
  - **흐름:** 토스·바이낸스를 읽기 전용으로 조회해 보드를 만든다.
  - **공개 경계:** 프로세스 밖으로 나가는 것은 H3의 표 하나다(`holdings/disclosure.py`). Claude는 `holdings_board` 기본 렌더로만 읽는다(`CLAUDE.md`).
  - **소유 문서:** `proposals/MULTI_ASSET_EXPANSION_V0.1.md`, `HOLDINGS_LEDGER_CHECKPOINT_V0.1.md`.
- **분석·콘텐츠**
  - **분석:** 사업 아이디어 분석(MVP 사용 사례)과 역할별 작업. Naver 키워드 근거는 `--naver-keywords`로 붙이고, worker에서 돈다.
  - **블로그 런타임 모듈:** `blog_*.py`, `content_ideation`. 블로그 **생산 경로는 런타임이 아니라 볼트와 서버 cron**이다(§8).
- **주간 경영 보고** (`management_report.py`, WMR-1)
  - **내용:** 레인 요약, 대기 승인, 실패 스케줄, 피드백 통계.
  - **방식:** 매주 관제봇으로 보낸다. 첫 보고는 2026-11-02 12:41 KST다.

## 7. 거버넌스 척추

- **Core:** `THOMAS_CORE/MVP_ACTIVE_CORE.yaml`(정체성·가치·목표·결정 모델). 작업마다 Core에 묶인다(`binding.py`).
- **정책:** `governance/GOVERNANCE_POLICY.yaml` 1.7.0.
  - 권한 단계는 P0–P6, 처분은 ALLOW…BLOCK이다. `runtime_effect.mode: REVIEW_ONLY`는 정책이 자동으로 주는 권한이 없다는 뜻이다.
  - 정책 버전이나 안전 섹션이 바뀌면 실행 단계가 READ_ONLY로 떨어지고, Thomas가 REBIND를 승인해야 돌아온다.
- **승인:** 요청은 `approval.py`가 만든다. 결정은 **관제봇의 `/approve`·`/reject`만** 한다. 실행 단계와 긴급청산 승인은 operator가 알린다.
- **Safety-Flag Gate:** 모델 호출과 네트워크 접근은 **환경 변수 opt-in 하나**로만 열린다(`safety_gate.select_env_gated`). 그 호출 지점 목록은 테스트로 고정돼 있다.
- **정지:** `/kill`·`/pause`·`/halt_trading [soft|hard]`·`/resume`(관제봇). 긴급청산 요청은 `scripts.emergency_close --request`(Thomas 터미널).
- **감사:** 해시 체인 원장(`store.py`, `audit.py`). 기록은 닫힌 스키마를 만족해야 한다.

## 8. 저장소 밖 부품

| 부품 | 어디 | 하는 일 | 정본·안내 |
|---|---|---|---|
| 볼트 | `/home/claude/obsidian-thomas` (git, 변경이 몇 분 안에 자동 푸시됨) | 블로그 원고·키워드·분석 기록, 공용 도구(`tools/`) | 볼트 자체 |
| 서버 스크립트 | `/home/claude/.claude/scripts` (로컬 git) | 블로그 일일 초안(`blog-daily-draft.sh`, cron 23:02 UTC), 검수기, 캡처, 알림(`hermes-notify.sh`) | `SCRIPTS.md` |
| 스킬 | `.claude/skills` (저장소: `deploy`, `verify`), `~/.claude/skills` (블로그 스킬) | 반복 절차 | 각 `SKILL.md` |
| Mac 루틴 | Thomas의 Mac | 일일 블로그 루틴, 월요일 주간 수집·리포트, 백업 내려받기(`com.thomas.govstate-pull`) | 볼트 운영 문서 |
| 백업·감시 | `/root/backups` (root cron) | core 백업 07:45 UTC(age 암호화), 대화 기록, 백업 감시, health watch 10분, 캔들 주간 | [`RUNBOOK_HARNESS_BACKUP_RESTORE.md`](RUNBOOK_HARNESS_BACKUP_RESTORE.md) |
| 배포 러너(C6a) | 운영 호스트 systemd | 승인에 묶인 고정 동작만 수행하는 배포 실행기 | `proposals/HOST_PRIVILEGE_SEPARATION_V0.1.md` |
| 텔레그램 봇 2개 | — | 관제봇: 명령·승인·주간 보고. 알림봇: 레인 알림·Mac 루틴·블로그 리포트, 보내기만 함 | `docker-compose.yml` 주석 |

**권한 분리(Option C, C6b 2026-10-20):**
- Claude는 `claude` 계정(uid 2101)으로 돌고, docker·`/root`·`.env`에 닿지 않는다.
- 배포, 보유자산 세부, 라이브 상태 확인은 Thomas의 터미널이 한다.

## 9. 개발과 운영 절차

- **변경:** 브랜치 → PR → 필수 체크 5개 → 머지. 필수 체크는 Active Architecture Gate 두 개, MVP runtime pytest(ubuntu, windows), Docker build + fail-closed smoke다. main에 직접 커밋하지 않는다.
  - workflow 파일(`.github/workflows/`)은 에이전트 토큰으로 푸시할 수 없다.
- **테스트:** `scripts/ops/test_run.sh --gate`(전체 스위트 + 릴리스 게이트). CI는 Python 3.12다.
- **배포:** `deploy` 스킬 절차(candidate 태그, 롤백 지점, 반영 직전 재확인). Thomas가 root로 실행한다.
  - 일회성 스크립트는 배포 없이 `tool-<PR#>` 태그로 돌린다.
- **기록:** PR마다 `docs/history/<날짜>-<이름>.md` 하나. 제안서 상태는 `proposals/STATUS.md`(자동 생성).

## 10. 은퇴한 것

| 무엇 | 언제 | 기록 |
|---|---|---|
| Hermes 컨테이너(대화 창·오케스트라) | 2026-10-25 | `history/2026-10-25-retire-hermes-container.md`, `HERMES_ORCHESTRATOR_ARCHITECTURE_V0.2.md`(결정 기록) |
| 문 4개(read·switch·dispatch·knowledge), workflow manager, knowledge 레인, 정책 조항 5개 | 2026-10-25 | `history/2026-10-25-retire-assistant-doors.md`, `runtime-contracts/POLICY_1_7_0_DRAFT.md` |
| predmarket 레인 | 2026-08-02 | `REMAINING_WORK.md` §A |
| 기기별 Safety-Flag grant | 2026-08-10 | `BUILD_HISTORY.md` |

## 11. 어디서 무엇을 찾나

- **권위가 어디 있나:** `CLAUDE.md`의 "Where authority lives" 표가 정본이다.
- **남은 일:** [`REMAINING_WORK.md`](REMAINING_WORK.md).
- **결정을 기다리는 것:** [`proposals/STATUS.md`](proposals/STATUS.md).
- **왜 이렇게 지었나:** [`history/`](history/), 그 이전은 [`BUILD_HISTORY.md`](BUILD_HISTORY.md).
- **진단 코드:** [`DIAGNOSTIC_CODE_INDEX.md`](DIAGNOSTIC_CODE_INDEX.md).
- **문서 목록 전체:** [`README.md`](README.md).
