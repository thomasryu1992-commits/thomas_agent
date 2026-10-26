# 제안: 블로그 부서 — 지금의 생산 로직을 정본으로 두고 조직의 루프에 녹인다 (V0.1)

**상태:** PARTIALLY DECIDED 2026-10-26 — 생산은 지금의 볼트 경로를 정본으로 두고, 런타임 블로그 레인(`blog_*` 10개 모듈)은 통째로 지운다. 조직에는 1단계(주간 경영 보고의 블로그 절)와 2단계(원장 기록)로 먼저 녹인다(Thomas 2026-10-26). 3단계(지침·발행 요청 루프)와 리팩토링·모듈화 트랙의 일부는 §7의 결정을 기다린다.
**요청:** Thomas 2026-10-26.
- "지금 현재 네이버/티스토리 생산 중인 로직으로 갈 것이고, 기존에 있던 것들은 삭제 및 정리하고 회사형 조직 내에 기존 것을 녹이는게 맞는 것 같은데"
- "그렇게 진행해주고, 해당 로직에 대해서도 기록을 해놔줘. 원고 발행만 해당 시스템에서 mac으로 요청을 하는 형태로 … 나머지 분석을 통한 피드백 등은 시스템 내부에서 루프 형태"
- "리팩토링 및 모듈화까지 진행하여 최적화한 뒤 녹아들게 할 수 있을까?"

**근거:**
- `NAVER_BLOG_CONTENT_LANE_V0.1.md`: 런타임 레인은 09-27에 보류됐다. 세 번 발화했고 모두 `NO_ELIGIBLE_KEYWORD`였다.
- 10-08 Phase 3 점검(#1181): 실생산은 볼트 cron과 사람 발행이고, 런타임 패키지 57개는 실제 글과 대응이 0이다.
- 2026-10-25 회사형 설계 점검: 블로그는 조직의 원장·보고 밖에 있다.

**권위:** 생산 절차의 정본은 볼트와 서버 스크립트에 있다. §1은 2026-10-26에 읽은 모습이며, 다르면 그쪽이 맞다.

---

## 1. 지금의 생산 로직 (2026-10-26 확인)

| 단계 | 어디서·누가 | 무엇을 | 기록 |
|---|---|---|---|
| ① 키워드 | root cron `kw-daily.sh` (22:10 UTC) → 볼트 `tools/kw_pipeline.py` | 후보 수집·채점, 큐 갱신 | 볼트 `analytics/keywords/queue.md`·`history/`, `/var/lib/thomas-measure/kw-pipeline/` |
| ② 원고 | claude cron `blog-daily-draft.sh` (23:02 UTC = 08:02 KST). 헤드리스 Claude Code + `blog-daily-draft.prompt.md` + 스킬 `naver-blog-draft`·`naver-to-google-seo` | 큐에서 주제를 골라 네이버 하루 3편(10-25부터), 티스토리 전용 0–3편 | 볼트 `content/naver/`·`content/tistory/`에 `status: draft`, 예약일·슬롯 배정, 볼트 push |
| ③ 검수 | ②의 안에서 | `blog-preflight.py`·`blog-variety.py`가 FAIL 0이어야 저장한다(cron 프롬프트). 이미지 검사 `img-preflight.py`는 스킬 절차 안에 있다. 네이버 Y1–Y3 관문(10-16)도 있다 | 원고와 함께 |
| ④ 네이버 저장·예약 | Mac launchd `com.thomas.naver-draft` → 볼트 `tools/naver_draft_daily.sh` | 아직 올라가지 않은 draft를 하루 3편까지 네이버 "저장 글"에 임시저장하고 알림을 보낸다 | `ops/naver-draft/daily.log`, 알림(Mac + 텔레그램 알림봇) |
| ⑤ 네이버 발행 | **Thomas** (도구: `naver_reserve.py`) | 저장 글을 확인한 뒤 예약표대로 예약한다. 최신본 관문과 시각 관문이 있다 | 볼트 `scheduled:`, `saved.json` |
| ⑥ 티스토리 예약 | `tools/tistory_queue.py`가 슬롯(13:30·16:30·19:30, 10-10부터 하루 세 칸)을 잡는다. Mac 예약 작업 `tistory-daily-post`는 10-05부터 tistory.com 접근이 막혀 있어(10-06 기록 기준, 이후 해소 여부 미확인), 서버의 SSH 세션이 `tools/tistory-autopost-procedure.md` §0-1대로 예약한다 | Mac Chrome으로 예약 발행 | `analytics/tistory-autopost.log` |
| ⑦ 성과 | 볼트 도구(`kw_pipeline` 순위 추적, `naver_stats_weekly`), Tistory GSC(수동), Mac 주간 수집(월 10:00)·주간 리포트(월 10:30, 스킬 `blog-weekly-review`) | 순위·노출·클릭, 주간 판단 | `analytics/` 아래, `revision-log.tsv`. 순위 귀속에는 미검증(UNVERIFIED) 행이 있다(#1183) |

**2026-10-26 원고 상태:** draft 38, hold 5, scheduled 23, published 242. 파일은 네이버 165개, 티스토리 145개다.

**검증 기록 (2026-10-26, 행마다 대조한 원본).**
- ①: `blog-daily-draft.sh` 13행 주석(C6b P-1, root cron `kw-daily.sh` 22:10 UTC). root crontab은 claude 계정이 읽을 수 없어 직접 보지 못했다.
- ②: claude 계정 crontab `2 23 * * *`, 서버 시간대 UTC. 프롬프트 22·26행(네이버 3편, 티스토리 0–3편, 10-25).
- ③: 프롬프트 5–11행, 스킬 `naver-blog-draft` 348행·`naver-to-google-seo` 411행(`img-preflight`).
- ④: `tools/naver_draft_daily.sh` 머리말, `tools/naver_draft.py` `PENDING_MAX = 3`. `ops/naver-draft/daily.log`의 2026-10-26 "임시저장 완료 3"이 실제로 돈 증거다.
- ⑤: `tools/naver_reserve.py` 머리말.
- ⑥: `tools/tistory_queue.py` `SLOTS`. Mac 차단은 2026-10-06 기록이다.
- ⑦: `analytics/tistory-autopost.log`의 2026-10-26 "주간 수집 … (맥 루틴)" 줄. #1183(순위 귀속)도 대조했다.

**실행 위치가 갈리는 이유.**
- ①–③은 서버에서 돈다.
- ④–⑥은 로그인된 브라우저가 있어야 해서 Mac 쪽(또는 Mac Chrome을 빌린 SSH 세션)에서 돈다. 서버의 헤드리스 Claude에는 Chrome 도구가 없다.
- 런타임은 외부 게시 권한을 갖지 않는다.

## 2. 런타임 블로그 레인 은퇴 (결정, 2026-10-26)

- **지운다:**
  - 모듈 10개: `blog_content`·`blog_draft`·`blog_draft_score`·`blog_naver`·`blog_overlap`·`blog_platform`·`blog_prompt`·`blog_quality`·`blog_rank`·`blog_tistory`
  - 스케줄 종류 `content_ideation`, worker job `content_ideation`
  - 스크립트 `record_published_url`·`score_blog_draft`·`track_blog_rank`와 그 테스트
- **남긴다:**
  - `naver_research.py`: 분석 CLI의 `--naver-keywords`(키워드 근거)가 쓴다. 블로그 생산과 무관하다.
  - 원장에 이미 있는 `blog_content_package` 등 과거 기록: 읽히게 둔다.
- **호스트(Thomas):** 꺼진 `content_ideation` 스케줄 행 3개를 지운다.

## 3. 목표: 블로그 부서와 조직의 루프

```text
   ┌──────────── 조직(런타임) ─────────────┐
   │ 관측 → 분석 → 지침(다음 주 키워드 순위·유형 배분·재작성 후보) │
   └──▲──────────────────────────────┬───┘
      │ 볼트 읽기(읽기 전용)           │ 지침 전달(3단계)
      │                               ▼
   성과(순위·GSC·발행) ◀── 발행(Mac) ◀── 발행 요청 ◀── 원고·검수(서버 cron) ◀── 키워드
```

**실행은 블로그 부서(서버 cron, Mac)가 하고, 판단의 기록과 피드백은 조직이 맡는다.** 발행만 Mac에 요청하고, 분석 → 피드백 → 다음 방향은 시스템 안에서 돈다.

| 단계 | 무엇 | 상태 |
|---|---|---|
| 1. 보고 | 주간 경영 보고(`management_report`)에 블로그 절. 볼트를 읽기 전용으로 읽어 지난 7일의 새 원고, 발행, 예약 대기, 보류, 미발행 draft를 보인다 | 결정, 구현 중 |
| 2. 기록 | 같은 주간 관측을 런타임 원장에 기록 한 줄로 남긴다. D8(11-25) 증거가 런타임 안에 생긴다 | 결정, 구현 중 |
| 3. 지침·발행 요청 | 조직이 주간 지침을 만들고, 블로그 부서가 그것을 읽어 키워드 선택에 반영한다. 발행할 글을 정해 Mac에 요청한다 | §7 결정 대기 |

**3단계의 기본 설계(권고).**
- **지침 전달은 끌어오기(pull)로 한다.** 원고 cron은 이미 `claude` 계정으로 돌고, 그 계정은 `claude-read-bridge`로 런타임을 읽을 수 있다. 그 문에 읽기 하나(`blog_guidance`)를 더하면, cron이 지침을 읽어 볼트 큐 순서에 반영한다. 런타임이 볼트에 쓰는 새 쓰기 경로는 생기지 않는다.
- **발행 요청도 볼트를 거친다.** cron이 "발행 대상" 표시를 볼트에 쓰면 Mac 루틴이 그것만 저장·예약한다. 런타임이 Mac에 직접 닿는 길은 만들지 않는다.
- **루프가 자동으로 바꿀 수 있는 것은 키워드 우선순위뿐이다.** 프롬프트, 스킬, 검수 규칙은 사람이 바꾼다.
- **증거 기준은 #1183을 따른다.** 비교군 5편 이상, 결측 30% 이하여야 하고, 미검증 순위는 쓰지 않는다.

## 4. 리팩토링·모듈화 트랙

지금 블로그 생산 코드는 네 곳에 흩어져 있다.
- 서버 스크립트 git: 50개 + `.bak` 13개
- 볼트 `tools/`: 74개 + `.bak` 15개
- 스킬 git: 스킬 3개
- Mac에만 있는 예약 작업 프롬프트

같은 검수 규칙이 둘 이상의 곳에 있고, 운영 값(하루 편수 등)이 프롬프트와 스크립트에 흩어져 있다.

**순서: 기준선 먼저, 리팩토링은 그다음.** 1·2단계의 주간 관측(생산 수, 검수 실패, 발행, 보류)이 리팩토링 전 기준선이다. 리팩토링은 이 수치가 나빠지지 않음을 보이면서 단계별로 한다. 런타임 레인이 실패한 이유가 "실제 생산과 동떨어진 새 구현"이었으므로, 리팩토링은 지금 돌고 있는 것을 다듬는 것이지 다시 짓는 것이 아니다.

후보 작업(결정 뒤 순서를 정한다):
1. 구성 목록 하나: 각 파일이 어느 단계의 무엇인지, 정본은 어디이고 사본은 어디인지.
2. `.bak-*` 정리(이력은 각 git에 있다).
3. 운영 값을 설정 파일 하나로 모은다(편수, 슬롯, 상한).
4. 겹치는 검수 규칙을 한 모듈로 모은다.
5. 테스트를 한 명령으로 돌린다.
6. Mac 예약 작업 프롬프트의 정본 위치를 정한다.

## 5. 진행 순서와 검증 기준 (Thomas 2026-10-26)

Thomas가 정한 순서: **설계 문서 → 검증 → 최적화 → 검증 → 리팩토링·모듈화 → 검증.** 결정이 필요한 항목(§7)을 빼고 끝까지 진행한 뒤 보고한다.

| 단계 | 하는 일 | 검증 기준 |
|---|---|---|
| 설계 문서 | 이 문서 | — |
| 검증 | §1의 모든 행을 원본(크론 줄, 프롬프트, 스킬, 볼트 도구, 로그)과 대조한다 | 대조한 원본을 행마다 댈 수 있다. 틀린 행은 고친다 |
| 최적화 | 런타임 쪽: 쓰이지 않는 블로그 레인 제거(§2), 1·2단계 관측(§3). 생산 쪽의 비용·속도·품질 최적화는 R1 결정 뒤 | 전체 테스트·릴리스 게이트·필수 체크 5개. 관측 절이 실제 볼트에서 §1의 상태 수와 같은 수를 낸다 |
| 리팩토링·모듈화 | 결정 없이 할 수 있는 것: 구성 목록(§4-1), `.bak` 정리(§4-2). 코드 이동·설정 통합은 R2 결정 뒤 | 서버 스크립트 `run-tests.sh`와 볼트 도구 테스트가 정리 전후 같은 결과를 낸다. 지운 파일은 git 이력이나 보관본으로 되살릴 수 있다 |

## 6. 하지 않는 것

- 원고 생성이나 키워드 선정을 런타임(Prime)이 직접 하게 옮기지 않는다(§2의 결정과 반대).
- 런타임에 외부 게시 권한을 주지 않는다. 발행은 브라우저가 있는 쪽이 한다.
- 고정(Freeze) 중인 프롬프트·스킬 줄을 이 작업으로 바꾸지 않는다.

## 7. 결정 (Thomas)

**3단계**
- **L1. 지침 전달:** (권고) 읽기 문으로 끌어오기 / 런타임이 볼트에 쓰기(새 쓰기 경로, 거버넌스 변경).
- **L2. 발행 승인 지점:** (권고) 지금처럼 네이버 저장 글에서 Thomas가 확인 / 관제봇 `/approve`로 옮기기.
- **L3. 루프가 자동으로 바꿀 수 있는 범위:** (권고) 키워드 우선순위만 / 유형 배분까지.

**리팩토링**
- **R1. 목표:** (권고) 유지보수성(한 곳에서 이해·수정) / 비용(모델 호출) / 속도 / 원고 품질.
- **R2. 코드의 집:** (권고) 볼트 `tools/`를 정본으로(Mac이 git으로 받는다), 서버 스크립트는 얇은 진입점만 / thomas_agent 저장소로 이전(PR·CI를 받지만 Mac이 이 저장소를 받아야 한다).
- **R3. 시작 시점:** (권고) 1·2단계 배포 후 기준선 2주 / 바로.

## 부록 A. 구성 목록 (§4-1, 2026-10-26 조사)

`S` = 서버 스크립트 git(`/home/claude/.claude/scripts`), `T` = 볼트 `tools/`, `S→T` = 서버 쪽은 볼트 파일을 가리키는 심볼릭 링크(정본은 볼트). "호출처"는 크론 작업·스킬·Mac 루틴·`tistory-autopost-procedure.md`·다른 스크립트에서 이름이 불리는 곳을 grep으로 찾은 것이다. 비어 있으면 그 진입점들에서 불리지 않는다는 뜻이고, 손으로 돌리는 도구이거나 쓰이지 않는 것이다(어느 쪽인지는 이 조사로 가리지 못했다). 테스트 파일(`*.test.py`)과 `fixtures/`·`testdata/`는 빠져 있다.

| 단계 | 파일 | 위치 | 호출처 |
|---|---|---|---|
| 키워드 | `kw_pipeline.py` | S→T | root 크론 `kw-daily.sh`, 매일 원고 작업, 스킬 셋, 티스토리 절차 |
| | `kw_pool.py` | T | 매일 원고 작업 |
| | `kw-check.py` | S | 원고 스킬 둘 |
| | `blog_taxonomy.py` | S→T | `naver-blog-draft` |
| | `ai_briefing_sources.py` | T | (없음) |
| 원고 | `blog-daily-draft.sh` + `.prompt.md` | S | claude 크론 23:02 UTC |
| | `naver_slot.py` | T | 매일 원고 작업, `naver-blog-draft` |
| | `blog-affiliate-place.py`, `affiliate-clips.py` | S | 매일 원고 작업 / `naver-blog-draft` |
| | `affiliate_fetch.py`, `affiliate_link.py` | T | `naver-blog-draft` / Mac 아침 루틴 |
| 검수 | `blog-preflight.py`, `blog_variety.py` | S→T | 스킬 셋, 매일 원고 작업, 티스토리 절차 |
| | `img-preflight.py`, `img_helpers.py`, `blogkit.py` | S | 원고 스킬 둘(`blogkit`·`img_helpers`는 스킬과 `img-preflight`가 가져다 씀) |
| | `blog-capture.py` | S | 매일 원고 작업, `naver-blog-draft` |
| | `tistory_browser_check.py`, `tistory_verify.py` | T | 티스토리 절차 |
| | `blog-tone.py`, `source_status.py`, `tistory_variety_asdraft.py` | S / T / T | (없음) |
| | `tistory-engine-compare.py` | S | (없음, 2026-10 엔진 비교용 일회성) |
| 발행(Mac·SSH) | `naver_draft.py`, `naver_draft_daily.sh`, `naver_draft_install.sh`, `mac_ready.sh` | T | Mac launchd `com.thomas.naver-draft` |
| | `naver_reserve.py`, `naver_version_gate.py`, `naver_patch.py` | T | `naver_draft.py`·`naver_reserve.py`에서 / Thomas 손 |
| | `tistory_queue.py`, `tistory_reserve_gate.py`, `md2paste.py`, `tistory_bold.py`, `tistory_backlinks.py`, `tistory_index_todo.py` | T(`md2paste` S→T) | 티스토리 절차 |
| | `tistory_version_gate.py`, `tistory_live2md.py` | T | (없음) |
| | `notify_hermes.sh`, `claude_retry.sh` | T | Mac 아침 루틴·주간 통계 |
| 측정·피드백 | `blog-weekly.py` | S | 스킬 셋 |
| | `naver_stats_weekly.py`/`.sh`, `naver_trend_daily.py` | T | Mac launchd(월 10:00, 매일) |
| | `revision_log.py` | T | `naver-to-google-seo` |
| | `mac-routine-watch.py`, `weekly-snapshot-check.py` | S | 서로, 날짜 지정 `at` 작업 |
| 블로그 아님: 볼트·세션 운영 | `vault.sh`, `vault-autopush.sh`, `vault-guard.sh`, `vault-push-gate.py`/`.allow`, `session-start-check.sh`, `pgrep-wait-guard.sh`, `memedit.py`, `server_scripts_backup.py`, `run-tests.sh`, `SCRIPTS.md` | S / T | 훅(`settings.json`), 매일 원고 작업 |
| 블로그 아님: 프롬프트 라이브러리 | `prompt_collect.py`, `prompt_conversations.py`, `prompt_history.py`, `prompt_intel.py`, `prompt_score.py`, `prompt_similar.py`, `memory_ledger.py`, `retention_plan.py` | T | 매일 원고 작업 끝, 서로 |
| 블로그 아님: 여행 | `travel-preflight.py`, `strip-gps.py` | S | (손) |

조사에서 나온 것(고치지 않고 적어 둔다):
- **정본이 두 곳이다.** 키워드·검수·분류 다섯 개는 볼트가 정본이고 서버 쪽은 링크다. 나머지 서버 스크립트는 서버 git이 정본이고 볼트에는 매일 사본(`ops/server-scripts/`)만 간다. R2(코드 집)가 이 둘을 하나로 할지 정한다.
- **호출처 없는 검수 도구 셋**(`blog-tone.py`, `source_status.py`, `tistory_variety_asdraft.py`)은 손으로 돌리는 것인지 버려진 것인지 이 조사로 가리지 못했다. `source_status.py`의 테스트는 정리 전부터 실패한다(아래).
- **`notify_hermes.sh`는 이름만 옛것이다.** Hermes 컨테이너 은퇴(2026-10-25) 때 `HERMES_BOT_TOKEN`과 서버 `hermes-notify.sh`는 발신 전용 창으로 일부러 남겼으므로 Mac 알림은 계속 간다.
- 볼트 `tools/`의 3분의 1가량은 블로그가 아니다(프롬프트 라이브러리, 볼트 운영). 블로그 부서의 경계는 위 표의 "블로그 아님" 행을 빼고 긋는다.

## 부록 B. 리팩토링 단계 검증 기록 (2026-10-26)

- **`.bak` 정리(§4-2).** 볼트 `tools/`에서 git이 추적하던 `.bak-*` 15개(`kw_pipeline.py.bak-*`, `tistory-autopost-procedure.md.bak-*`)를 지웠다(볼트 커밋 `b70af8ea`). 되살리기: `git show b70af8ea~1:tools/<파일>`로 확인했다. 서버 스크립트의 `.bak` 13개는 git이 추적하지 않는 파일이라 지우면 되살릴 수 없고, 그중 5개는 10-25·26 날짜라 다른 세션의 되돌림 지점일 수 있어 남겼다.
- **전후 비교.** 서버 `run-tests.sh`는 정리 전후 모두 rc=0, 출력 차이는 기존 traceback 줄 속 임시 디렉터리 이름뿐이다. 볼트 도구 테스트 29개는 전후 결과가 같다. 그중 2개는 정리 전부터 실패한다: `source_status.test.py`(JSONDecodeError), `tistory_reserve_gate.test.py`(IndexError). 이 작업이 만든 실패가 아니며 고치지 않았다.
