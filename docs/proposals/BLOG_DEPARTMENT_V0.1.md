# 제안: 블로그 부서 — 지금의 생산 로직을 정본으로 두고 조직의 루프에 녹인다 (V0.2)

**상태:** PARTIALLY DECIDED 2026-10-26 — 생산은 지금의 볼트 경로에서 시작하고, 런타임 블로그 레인(`blog_*` 10개 모듈)은 지웠다(#1319). v0.2(Thomas 2026-10-26): 통합할 수 있는 단계(키워드, 검증, 발행 계획, 분석, 피드백)는 시스템 안으로 옮기고, 원고 생성·발행·통계 수집만 외주로 둔다(§3). §7의 N1·S1·L2·L3·R1–R3은 권고대로 정했다(Thomas 2026-10-26). P0 관측(#1321)은 머지됐고, 배포(#1320 포함 여부)가 결정을 기다린다.
**요청:** Thomas 2026-10-26.
- "지금 현재 네이버/티스토리 생산 중인 로직으로 갈 것이고, 기존에 있던 것들은 삭제 및 정리하고 회사형 조직 내에 기존 것을 녹이는게 맞는 것 같은데"
- "그렇게 진행해주고, 해당 로직에 대해서도 기록을 해놔줘. 원고 발행만 해당 시스템에서 mac으로 요청을 하는 형태로 … 나머지 분석을 통한 피드백 등은 시스템 내부에서 루프 형태"
- "리팩토링 및 모듈화까지 진행하여 최적화한 뒤 녹아들게 할 수 있을까?"
- (v0.2) "해당 볼트가 지금 시스템 바깥에서 돌고 있으니 그걸 시스템 내부에 통합시키는 것을 목표로 하는 거 아니었어?"
- (v0.2) "시스템 내부로 통합할 수 있는 것은 통합하고 못하는 것들은 외부에 놔두고 외주 형식으로 하자"

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
- **호스트(Thomas):** 꺼진 `content_ideation` 스케줄 행 3개를 지운다. 2026-10-26 완료(`scheduler_cli remove`, 남은 행 0).

## 3. 목표: 통합할 수 있는 단계는 시스템 안으로, 나머지는 외주 (v0.2)

**원칙 (Thomas 2026-10-26):** "시스템 내부로 통합할 수 있는 것은 통합하고 못하는 것들은 외부에 놔두고 외주 형식으로". 외주 단계도 시스템이 지시하고 보고를 받는다. v0.1의 §3은 실행을 모두 바깥에 두고 시스템은 관측·피드백만 맡겼다. v0.2는 그 설계를 이 원칙으로 바꾼다.

**판정 기준.** 그 단계를 컨테이너 안에서 돌릴 수 있는가. 막는 것은 세 가지다.
- 구독 로그인이 필요한 모델: claude 계정의 Claude Code CLI와 스킬.
- 로그인된 브라우저: Mac Chrome.
- 사람의 확인: 네이버 저장 글 검토.

외부 조회(HTTP)는 막는 이유가 아니다. 다만 env 게이트에 이름을 올려야 한다(§7 N1).

| 단계 | 구분 | 이유 | 시스템 쪽 형태 |
|---|---|---|---|
| ① 키워드 수집·채점 | 통합 | 규칙 코드다. 외부 조회는 게이트를 거친다 | 블로그 레인이 매일 후보를 모아 채점하고 오늘의 주제를 정한다. 검색량은 기존 `naver_research.SearchAdKeywordTool`을 재사용한다 |
| ② 원고 생성 | **외주** | 구독 CLI와 스킬이 필요하다 | 시스템이 지시를 내리고(①의 결과), 외주 실행기(claude 크론)가 받아 쓰고 보고한다 |
| ③ 검증 | 통합 | 규칙 검사다 | 외주 쪽 검수는 그대로 두고, 시스템이 새 원고에 같은 검사를 다시 돌려 그 판정을 정본으로 기록한다 |
| ④ 발행 계획(슬롯·순서) | 통합 | 규칙 코드다(`naver_slot`, `tistory_queue`의 슬롯 계산) | 지시에 예약 슬롯을 실어 보낸다 |
| ⑤ 발행 실행 | **외주** | Mac 브라우저와 Thomas의 확인이 필요하다 | 지금처럼 원고 front matter의 `scheduled:`가 발행 요청이다. Mac 쪽은 바뀌지 않는다 |
| ⑥ 통계 수집 | **외주** | 네이버 크리에이터 어드바이저와 GSC에 Mac 로그인이 필요하다 | Mac이 볼트 `analytics/`에 쓴 것을 시스템이 읽는다 |
| ⑦ 분석·집계 | 통합 | 수집된 파일만 있으면 계산할 수 있다 | 주간 분석 기록(글별 조회, 유입, 포착률)을 원장에 남긴다 |
| ⑧ 피드백 | 통합 | ⑦과 ①이 모두 안에 있다 | ⑦의 결과가 ①의 키워드 우선순위에 들어간다. 자동으로 바꾸는 것은 우선순위뿐이다(L3) |

이렇게 하면 루프(①→③→④→⑦→⑧→①)가 시스템 안에서 닫히고, 바깥에는 원고 쓰기, 발행, 통계 수집 세 가지 외주만 남는다.

### 3-1. 시스템과 외주 사이의 형식

| 방향 | 통로 | 담는 것 | 새로 생기는 것 |
|---|---|---|---|
| 지시 (시스템 → 원고 외주) | `claude-read-bridge`의 읽기 하나(`blog_instruction`). 원고 크론이 시작할 때 끌어온다 | 지시 id, 날짜, 플랫폼별 주제(키워드·클러스터·예약 슬롯), 지침 판(⑧) | 문의 읽기 1개. 런타임이 볼트에 쓰는 경로는 생기지 않는다 |
| 보고 (원고 외주 → 시스템) | 볼트 `ops/blog-runs/<KST 날짜>.json`. 크론이 끝날 때 쓰고, 시스템이 읽기 전용 마운트로 읽는다 | 소비한 지시 id(그림자 기간은 null), 시작·종료 시각, 쓴 키워드, 만든 원고(경로·플랫폼), 원고별 preflight·variety 판정, 단계별 종료 코드, 건너뛴 이유 | 크론 스크립트가 쓰는 파일 1종, scheduler-maint 마운트 1줄 |
| 발행 요청 (시스템 → Mac) | 원고 front matter `scheduled:`(지시의 슬롯을 외주가 그대로 적는다) | 예약 시각 | 없음 |
| 통계 (Mac → 시스템) | 볼트 `analytics/naver-weekly/`, `naver-trend/`, `weekly-browser/`, `keywords/inflow.tsv`, 읽기 전용 마운트 | Mac이 이미 모으는 JSON·TSV | 마운트 줄 |

### 3-2. 부록 A 파일의 배정

| 단계 | 통합(레인으로 옮김) | 외주에 남음 |
|---|---|---|
| ① 키워드 | `kw_pipeline.py`, `kw_pool.py`, `kw-check.py`, `blog_taxonomy.py`, `ai_briefing_sources.py` | — (root 크론 `kw-daily.sh`는 P6에서 내린다) |
| ② 원고 | — | `blog-daily-draft.sh`·`.prompt.md`, 스킬 `naver-blog-draft`·`naver-to-google-seo`, `blog-capture.py`(캡처 문을 거친다), 제휴 클립 3종(`affiliate-clips`, `affiliate_fetch`, `affiliate_link`) |
| ③ 검증 | `blog-preflight.py`(원고 검사), `blog_variety.py`, `blog-tone.py`, `img-preflight.py`·`img_helpers.py`·`blogkit.py`, `source_status.py` | `blog-preflight`의 URL 생존 검사, `tistory_browser_check.py`·`tistory_verify.py`(Chrome) |
| ④ 발행 계획 | `naver_slot.py`, `tistory_queue.py`의 슬롯 계산, `blog-affiliate-place.py` | — |
| ⑤ 발행 실행 | — | `naver_draft*`, `mac_ready.sh`, `naver_reserve.py`, `naver_version_gate.py`, `naver_patch.py`, `tistory_reserve_gate.py`, `md2paste.py`, `tistory_bold.py`, `tistory_backlinks.py`, `tistory_index_todo.py`, `tistory_version_gate.py`, `tistory_live2md.py`, `tistory-autopost-procedure.md`, `revision_log.py`(발행 후 수정 이력) |
| ⑥ 통계 수집 | `blog-weekly.py`(로그인 없이 모으는 것) | `naver_stats_weekly.*`, `naver_trend_daily.py`, GSC 주간 수집(Mac 루틴) |
| ⑦ 분석 | `kw_pipeline` `capture`(포착률), 주간 리뷰의 집계 부분 | 주간 리뷰의 해석·판단(스킬 `blog-weekly-review`) |
| 감시 | `mac-routine-watch.py`, `weekly-snapshot-check.py` | — |
| 블로그 아님 | 옮기지 않는다: 볼트·세션 운영, 프롬프트 라이브러리, 여행 | |

`tistory_variety_asdraft.py`와 `tistory-engine-compare.py`는 일회성 도구라 옮기지 않는다. 알림(`notify_hermes.sh`, `claude_retry.sh`)은 외주 쪽의 실패 알림이라 남는다.

**지시가 없을 때(fail-closed).**
- 그림자 기간(§4의 P4–P5): 외주는 지금처럼 자기 파이프라인으로 돈다. 시스템의 지시는 비교용이다.
- 전환 뒤(P6): 지시가 없거나 읽을 수 없으면 원고를 쓰지 않는다. 보고 파일에 `skipped: NO_INSTRUCTION`을 남기고, 시스템은 그날을 실패로 알린다. 외주가 스스로 주제를 고르는 길은 닫힌다.

## 4. 옮기는 순서 (리팩토링·모듈화 = 옮기기)

v0.2에서 리팩토링은 통합 단계의 코드를 thomas_agent 저장소로 **옮기는 일**이다(R2). 새로 짓지 않는다. 은퇴한 런타임 레인이 실패한 이유는 실제 생산과 동떨어진 새 구현이었다. 그래서 옮기는 코드는 지금 돌고 있는 볼트 코드에서 시작하고, 같은 입력에 같은 출력을 내는지 그림자로 확인한 뒤에야 전환한다(`kw_pool` 그림자가 선례다).

새 코드는 도메인 패키지 하나(`runtime/mvp_runtime/blog/`)에 모은다. 레인 규칙을 따른다. 코어는 이 패키지를 모듈 수준에서 불러오지 않고, 스케줄러의 발화 지점에서만 함수 안에서 불러온다. 통째로 지울 수 있는 단위로 둔다.

| 단계 | 하는 일 | 외부 조회 | 전환 조건 |
|---|---|---|---|
| P0 관측 | #1321(주간 보고 블로그 절, 원장 기록). **머지됨, 배포 전** | 없음 | — |
| P1 보고 형식 | 원고 크론이 `ops/blog-runs/*.json`을 쓴다. 시스템이 읽어 하루 기록(원고 수, 검수 판정, 키워드)을 남긴다. 통계 폴더도 마운트한다 | 없음 | 7일 연속 보고 파일이 있고, 그 원고 수가 볼트 front matter 집계와 같다 |
| P2 검증 통합 | `blog-preflight`(네트워크 없는 검사)와 `blog_variety`를 레인으로 옮긴다. 새 원고마다 시스템 판정을 기록한다 | 없음(URL 생존 검사는 외주 쪽에 남는다, §7 N1) | 2주 동안 시스템 판정과 외주 보고 판정이 원고마다 같다. 다르면 원인을 기록한다 |
| P3 분석 통합 | Mac이 모은 통계를 읽어 주간 분석 기록(글별 조회·유입, 포착률)을 남긴다 | 없음 | 2주 연속 기록되고, 주간 리뷰 스킬이 낸 수와 같다 |
| P4 키워드 그림자 | `kw_pipeline` 채점·선정을 레인으로 옮겨 컨테이너에서 매일 그림자로 돌린다. 볼트 파이프라인 출력(`queue.md`)과 매일 비교한다 | §7 N1의 호스트 | 14일 동안 상위 후보 집합이 같다. 다른 날은 원인이 설명된다 |
| P5 피드백 그림자 | P3 결과로 키워드 우선순위를 조정하는 지침을 만들어 P4 선정에 반영한다(그림자). 증거 기준은 #1183(비교군 5편 이상, 결측 30% 이하, 미검증 순위 미사용) | 없음 | 2주 그림자 기록. 지침이 바꾼 순위가 기록에 남는다 |
| P6 전환 | `blog_instruction` 읽기를 연다. 원고 프롬프트·스킬이 볼트 큐 대신 지시를 읽는다. root 크론 `kw-daily.sh`를 내린다 | — | P4·P5 조건 충족, 고정 중인 줄(§6)의 고정이 풀림, Thomas 승인 |
| P7 슬롯 통합 | 슬롯 계산을 지시에 싣는다. `naver_slot`·`tistory_queue`의 계산은 레인으로, 예약 실행 도구는 Mac·SSH 쪽에 남는다 | 없음 | P6 뒤 2주 동안 지시 슬롯과 실제 예약이 같다 |
| P8 정리 | 전환된 단계의 볼트·서버 스크립트 사본을 내린다. Mac 전용 도구만 볼트에 남는다 | — | 각 단계 전환 뒤 |

**결정 없이 지금 할 수 있는 것:** 없다. P1은 크론 스크립트와 마운트를, P2 이후는 새 레인과 그 스케줄 종류를 만든다. 그래서 §7의 결정과 P0 배포가 먼저다.

## 5. 진행 순서와 검증 기준 (Thomas 2026-10-26)

Thomas가 정한 순서는 **설계 문서 → 검증 → 최적화 → 검증 → 리팩토링·모듈화 → 검증**이다. v0.2에서는 이 순서를 §4의 단계마다 다시 돈다. 각 단계는 설계(이 문서의 해당 행), 구현 PR, 전환 조건 검증 순서로 간다.

| 단계 | 검증 기준 |
|---|---|
| 설계 문서 v0.2 | §3의 단계 구분이 부록 A(구성 목록)의 파일과 하나씩 대응한다. 외부 조회 목록(§7 N1)이 코드의 URL과 같다 |
| 각 P 단계 | 전체 테스트, 릴리스 게이트, 필수 체크 5개, 그리고 §4 표의 전환 조건 |
| 옮기기(리팩토링) | 그림자 동등성. 옮긴 코드가 같은 입력에 같은 출력을 낸다. 볼트 도구 테스트와 서버 `run-tests.sh`는 전환 전까지 결과가 바뀌지 않는다 |

## 6. 하지 않는 것

- 원고 생성을 무료 API 체인으로 옮기지 않는다. 외주 실행기가 지금의 CLI와 스킬로 쓴다.
- 런타임에 외부 게시 권한을 주지 않는다. 발행은 Mac 쪽이 한다.
- 런타임이 볼트에 쓰는 경로를 만들지 않는다(§7 S1). 시스템은 볼트를 읽기 전용으로 읽고, 외주는 문으로 끌어온다.
- 고정 중인 프롬프트·스킬 줄(티스토리 H판 ② 등)을 이 작업으로 바꾸지 않는다. 스킬이 지시를 읽게 바꾸는 것은 P6에서 고정이 풀린 뒤에 한다.
- 그림자 비교 없이 전환하지 않는다.

## 7. 결정 (Thomas)

**정해진 것**
- **원칙 (Thomas 2026-10-26):** 통합할 수 있으면 통합하고, 아니면 외주로 둔다(§3).

**정해진 것 (Thomas 2026-10-26, "승인: BLOG-DEPT v0.2 §7 권고대로") — 아래가 결정이다.**
- **N1. 컨테이너에서 여는 외부 조회.** 하나하나가 env 게이트에 이름을 올리는 네트워크 권한이다. 지금 `kw_pipeline.py`가 닿는 곳은 다음과 같다.
  - `api.searchad.naver.com`(검색량): **이미 게이트에 있다.** `naver_research.SearchAdKeywordTool`을 재사용하고, 두 번째 클라이언트를 만들지 않는다.
  - `ac.search.naver.com`(네이버 자동완성), `suggestqueries.google.com`(구글 자동완성)
  - `search.naver.com`(검색 결과 페이지 읽기), `blog.naver.com/PostView.naver`(상위 글 읽기): 페이지 긁기라 거버넌스 부담이 가장 크다
  - `m.blog.naver.com/api/blogs/…`(상위 블로그 정보), `rss.blog.naver.com`(내 블로그 RSS)
  - 키워드 밖에서도 같은 호스트를 쓴다. `blog_taxonomy.py`는 내 글 본문을 `PostView`로 읽고, `blog-preflight` 발행 후 검사는 `m.blog.naver.com`을 읽는다. `blog-weekly.py`는 `blog.like.naver.com`(공감 수)과 `thomasai.tistory.com`(내 티스토리)을 읽는다.
  - `blog-preflight`의 URL 생존 검사는 주소가 원고마다 달라 목록으로 묶을 수 없다. 결정: 외주 쪽에 남긴다.
  - 결정: 네이버·구글 조회 호스트(검색광고 API 제외)를 하나의 env opt-in으로, 내 블로그 읽기(`rss`, `blog.like`, 내 티스토리)를 다른 하나로 묶는다. opt-in 변수 이름은 P4 구현 PR에서 정하고, 그 PR이 env 게이트 열거 시험에 올린다.
- **S1. 키워드 상태의 집 (v0.1 L1의 구체형).** 지금은 볼트 `analytics/keywords/`에 49개 파일이 있다. `kw_pipeline`이 쓰는 것과 사람·주간 리뷰가 쓰는 것(`seeds.txt`, `next_topics.tsv`, `inflow.tsv`)이 섞여 있다. 결정: 파이프라인이 쓰는 것은 런타임 상태로 옮기고, 사람이 쓰는 입력은 볼트에 둔 채 읽기 전용으로 읽는다. 외주는 문(`blog_instruction`)으로 결과를 받는다. 대안이던 컨테이너의 볼트 쓰기 경로는 열지 않는다.
- **L2. 발행 승인 지점:** 지금처럼 네이버 저장 글에서 Thomas가 확인한다(대안이던 관제봇 `/approve` 이전은 하지 않는다).
- **L3. 루프가 자동으로 바꿀 수 있는 범위:** 키워드 우선순위만. 유형 배분, 프롬프트, 스킬, 검수 규칙은 사람이 바꾼다.
- **R1. 목표:** 유지보수성(한 곳에서 이해·수정·시험). 비용·속도·품질은 이 작업의 목표가 아니다.
- **R2. 코드의 집: thomas_agent 저장소.** v0.1은 볼트 `tools/`를 권고했다. 통합이 목표가 되면서(Thomas 2026-10-26) 통합 단계의 코드는 PR·CI·게이트를 받는 저장소로 가는 것이 맞다. Mac 전용 도구(발행·통계 수집)는 볼트에 남는다.
- **R3. 시작 시점:** P0 배포 직후 P1–P3을 시작하고, P4–P5 그림자도 함께 시작한다. P6 전환은 관측 기준선 2주와 그림자 조건을 채운 뒤에 한다.

**정할 것**
- **배포 (P0):** #1321은 머지됐지만 배포되지 않았다. 2026-10-26 배포 시도 중 main에 #1320(리밸런싱 계산기)이 들어와 2차 점검이 STOP했다. #1320을 포함한 main으로 배포할지 Thomas가 정한다.

## 부록 A. 구성 목록 (v0.1 §4-1, 2026-10-26 조사)

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
| | `naver_stats_weekly.py`/`.sh`, `naver_trend_daily.py` | T | Mac 예약(파일 머리말 기준 월 10:00, 매일; Mac 쪽 등록은 확인 못 함) |
| | `revision_log.py` | T | `naver-to-google-seo` |
| | `mac-routine-watch.py`, `weekly-snapshot-check.py` | S | 서로 |
| 블로그 아님: 볼트·세션 운영 | `vault.sh`, `vault-autopush.sh`, `vault-guard.sh`, `vault-push-gate.py`/`.allow`, `session-start-check.sh`, `pgrep-wait-guard.sh`, `memedit.py`, `server_scripts_backup.py`, `run-tests.sh`, `SCRIPTS.md` | S / T | 훅(`settings.json`), 매일 원고 작업 |
| 블로그 아님: 프롬프트 라이브러리 | `prompt_collect.py`, `prompt_conversations.py`, `prompt_history.py`, `prompt_intel.py`, `prompt_score.py`, `prompt_similar.py`, `memory_ledger.py`, `retention_plan.py` | T | 매일 원고 작업 끝, 서로 |
| 블로그 아님: 여행 | `travel-preflight.py`, `strip-gps.py` | S | (손) |

조사에서 나온 것(고치지 않고 적어 둔다):
- **정본이 두 곳이다.** 키워드·검수·분류 다섯 개는 볼트가 정본이고 서버 쪽은 링크다. 나머지 서버 스크립트는 서버 git이 정본이고 볼트에는 매일 사본(`ops/server-scripts/`)만 간다. R2(코드 집)가 이 둘을 하나로 할지 정한다.
- **호출처 없는 검수 도구 셋**(`blog-tone.py`, `source_status.py`, `tistory_variety_asdraft.py`)은 손으로 돌리는 것인지 버려진 것인지 이 조사로 가리지 못했다. `source_status.py`의 테스트는 정리 전부터 실패한다(아래).
- **`notify_hermes.sh`는 이름만 옛것이다.** Hermes 컨테이너 은퇴(2026-10-25) 때 `HERMES_BOT_TOKEN`과 서버 `hermes-notify.sh`는 발신 전용 창으로 일부러 남겼으므로 Mac 알림은 계속 간다.
- 볼트 `tools/`의 3분의 1가량은 블로그가 아니다(프롬프트 라이브러리, 볼트 운영). 블로그 부서의 경계는 위 표의 "블로그 아님" 행을 빼고 긋는다.

## 부록 B. v0.1 리팩토링 단계 검증 기록 (2026-10-26)

- **`.bak` 정리(§4-2).** 볼트 `tools/`에서 git이 추적하던 `.bak-*` 15개(`kw_pipeline.py.bak-*`, `tistory-autopost-procedure.md.bak-*`)를 지웠다(볼트 커밋 `b70af8ea`). 되살리기: `git show b70af8ea~1:tools/<파일>`로 확인했다. 서버 스크립트의 `.bak` 13개는 git이 추적하지 않는 파일이라 지우면 되살릴 수 없고, 그중 5개는 10-25·26 날짜라 다른 세션의 되돌림 지점일 수 있어 남겼다.
- **전후 비교.** 서버 `run-tests.sh`는 정리 전후 모두 rc=0, 출력 차이는 기존 traceback 줄 속 임시 디렉터리 이름뿐이다. 볼트 도구 테스트 29개는 전후 결과가 같다. 그중 2개는 정리 전부터 실패한다: `source_status.test.py`(JSONDecodeError), `tistory_reserve_gate.test.py`(IndexError). 이 작업이 만든 실패가 아니며 고치지 않았다.
