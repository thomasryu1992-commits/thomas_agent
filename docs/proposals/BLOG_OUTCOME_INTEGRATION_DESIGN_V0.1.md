# 블로그 Outcome Integration 설계 — PR 3.3 (V0.1)

**상태:** DECIDED 2026-10-08 — Design Complete(Thomas 2026-10-08 최종 정합성 확인까지 반영). Q1 ±3일(목표 체크포인트·실제 관측일·오프셋 구분, 실제 관측일을 모르면 관측일·오프셋 모두 `null`, `run_date`는 별도 필드), Q2 비교군당 5편·결측률 30% 이하는 후보 제안의 최소선(인과 확정 아님), Q3 I2 → 독립 검증 → I5, Q4 PR 3.4는 이 조인 뷰 재사용, 과거 UNVERIFIED 순위는 개별 글 성과로 쓰지 않고 잠식 판단은 검증된 `post_rank`만. 남은 것: 구현 항목 I1~I5 — 각각 별도 승인 전에는 만들지 않는다. 코드·데이터 파일·스케줄 변경 0건.

**요청:** PR 3.0 감사 결정(Thomas 2026-10-08, `BLOG_RUNTIME_PHASE3_BASELINE_AUDIT_V0.1.md`의 '결정' 절)의 PR 3.3 — 아래 여섯 가지만 설계한다: ① Vault Article ID·Published URL 기반 연결, ② 실제 Naver 순위 연결, ③ Tistory GSC 수동 데이터 연결, ④ Prompt Version 추적, ⑤ Missing Data 처리, ⑥ Weekly Feedback Candidate 구조.
**기준 시점:** 2026-10-08. Vault `725f639`, scripts `974b5a0`, skills `53aafcd`.
**표기:** **[확인]** 이 문서를 쓰며 데이터를 직접 읽어 셈. **[문서]** 기존 기록.
**하지 않는 것:** Runtime 코드, 새 수집 작업·스케줄, GSC OAuth·API 자격증명, 기존 데이터 파일 수정, 발행본 수정. Runtime `blog_rank`·`record_published_url`을 이 흐름에 연결하지 않는다. Runtime Package ID는 실제 대응이 확인되지 않으면 UNLINKED이고, 키워드 유사성만으로 연결하지 않는다.

---

## 결정 (Thomas 2026-10-08, 설계 검토)

| 항목 | 결정 | 이 문서에 반영한 곳 |
|---|---|---|
| Q1 체크포인트 허용 오차 | **승인 — ±3일.** 목표 체크포인트·실제 관측일·체크포인트 오프셋을 구분한다. 관측 시점이 다른 값을 같은 날짜에 잰 것처럼 적지 않는다 | §3.3 |
| Q2 INSUFFICIENT_EVIDENCE | **조건부 승인 — 비교군당 5편, 결측률 30% 이하**는 개선 후보를 *제안*하기 위한 최소선이다. 5편으로 프롬프트 효과나 검색 성과의 인과를 확정하지 않는다. 표본이 작거나 교란이 크면 INSUFFICIENT_EVIDENCE를 유지한다 | §7 |
| Q3 첫 구현 | **수정 — I2(조인 뷰) → 독립 검증·정확도 확인 → I5(주간 보고서 통합).** 한 묶음이 아니다. I1~I5 모두 실제 구현은 각각 별도 승인 | §9 |
| Q4 Status Board | **승인 — PR 3.4는 이 조인 뷰를 재사용하도록 설계한다.** 별도 Outcome 파싱 엔진을 만들지 않는다. 3.4 구현은 미승인 | §8 |
| 순위 귀속 (I1 요구사항) | 대상 원고 URL의 logNo를 SERP 전체 결과와 비교해 `post_rank`를 계산하고, `blog_best_rank`는 따로 둔다. 첫 번째 내 블로그 결과의 logNo만 저장하는 수정으로 끝내지 않는다. 대상 글이 결과에 없으면 순위를 추측하지 않는다. 과거 데이터는 원본 증거 없이 VERIFIED로 올리지 않는다 | §3 |
| 최종 정합성 확인 — 관측일 | I1 이전 데이터처럼 실제 관측일을 확인할 수 없으면 `actual_observation_date`는 `null`이다. `run_date`는 별도 필드에 적고 관측일로 대신 쓰지 않는다. 실제 관측일이 불명확하면 `checkpoint_offset_days`도 추측하지 않는다(`null`) | §3.3 |
| 최종 정합성 확인 — 과거 귀속 | UNVERIFIED 순위는 개별 글의 확정 성과로 쓰지 않는다. 결측률과 순위 귀속 검증률을 따로 낸다. 잠식 판단에는 귀속이 검증된(`VERIFIED`) `post_rank`만 쓴다 | §3.4, §6, §7 |
| Prompt Version 확실성 | EXACT는 생성 시점에 쓴 판이 **직접** 확인될 때만. 커밋 시각·원고 생성일로 추정한 판은 APPROX, 근거가 없으면 UNKNOWN. 실험 Cutover 기록은 보존하되 실제 적용 증거와 날짜 추정을 구분한다 | §5 |

---

## 0. 한 줄

**성과 데이터는 이미 Vault에 있다. 빠진 것은 "같은 글"을 가리키는 키, 실제 관측 시각, 결측의 이름이다.** 설계의 중심은 네 가지다.
1. 발행 URL을 1순위 키로 삼는 Article Key.
2. 순위를 logNo로 "그 글"에 귀속하는 규칙. 지금은 "내 블로그 글 중 최고 순위"를 기록한다.
3. 목표 체크포인트와 실제 관측일을 분리하는 것.
4. 0과 "모름"을 구분하는 결측 어휘.

저장소를 새로 만들지 않고, 기존 파일을 읽어 계산하는 **조인 뷰**가 기본안이다.

---

## 1. 지금 있는 데이터 [확인]

| 원천 | 위치 | 키 | 무엇 | 비고 |
|---|---|---|---|---|
| 원고 프론트매터 | `content/naver/NN편-*.md`(+`제휴/NN편-*`), `content/tistory/NN-*.md` | 파일 경로, `episode` | `url`, `status`, `published`, `scheduled`, `created`, `keywords`, `cluster`, `form`, Tistory `source: "[[NN편-…]]"`, `origin` | Naver 98편: published 72편 모두 `url` 있음, draft 26편은 빈칸. Tistory 91편 모두 `url` 있음(예약 22편 포함) |
| Naver 순위 이력 | `analytics/keywords/history/YYYY-MM-DD.json`의 `ranks` | `file` + `kw` | `rank`, `prev`, `parsed`, `in_title`, `title_diff`, `published` | 파일 11개(09-18~10-08, 불규칙 — 큐를 다시 만든 날만). 실제 블로그 탭(`search.naver.com … tab.blog.all`) 첫 페이지(30건). logNo·가져온 시각은 저장하지 않는다 |
| SERP 캐시 | `~/.cache/blog-kw/serp.json` | 검색어 | `[가져온 시각, 결과 목록(blog, logno, rank, date, title)]` | 수명 1일, 다음 조회 때 덮어쓴다 — 보존되는 기록이 아니다 |
| 유입 검색어 | `analytics/keywords/inflow.tsv` | 주말일 + 플랫폼 + 검색어 | 값, 지표(유입/노출), 도착 글, google 행은 클릭·평균 순위 | 주간 분석이 손으로 덧붙임. google 노출 19행 중 도착 글 `?` 6행 |
| 주간 보고서 | `analytics/YYYY-MM-DD.md` | 글 링크(URL) | 보강 전후·D+14 판정 칸, "GSC 노출/클릭 전: → 후:", "주간 조회:" | 값은 사람이 옮겨 적음. 창이 섞여 있음(주간 / "GSC 3개월 기준") |
| 브랜드 지표 | `analytics/branding.tsv` | 주말일 | 블로그 전체 주간 합계 | 글 단위 아님. 못 구한 값은 "미확인"(빈칸 아님) — 이 설계의 결측 원칙과 같다 |
| 프롬프트 판 | `Prompt/운영/버전.json`, `Prompt/운영/<분류>/버전/*` | 분류(원고-스킬·SEO-스킬·SEO-프롬프트·아침-작업-지시 …) | 판 번호, `written` 날짜, `sha` | 3일 병합 창(MERGE_DAYS) — 판 내용은 그 창의 마지막 상태 |
| 스킬 git | `~/.claude/skills` (2026-10-06부터 로컬 git) | 커밋 | 스킬 정본의 판 | 크론의 자동 커밋은 원고 작업 **뒤**에 돈다 |
| Runtime | Ledger `blog_content_package`, `blog_rank_snapshot` | `bcp_*` | — | 실생산 글과 대응 0건(57/57 UNLINKED), 스냅샷 0행. 이 설계에서 쓰지 않는다 |

---

## 2. ① Article Key — Vault Article ID와 Published URL

**키 규칙 (강한 것부터, 아래 단계로 내려가지 않으면 연결하지 않는다):**

| 순위 | 키 | 언제 | 근거 |
|---|---|---|---|
| 1 | Published URL — Naver `blog.naver.com/thomasai/<logNo>`, Tistory `thomasai.tistory.com/<N>` | 발행(또는 Tistory 예약)으로 주소가 정해진 뒤 | 플랫폼이 준 불변 ID. 파일 이름이 바뀌어도 유지 |
| 2 | Vault Article ID = `platform` + `episode` (Naver `NN편`, Tistory `NN`) | URL이 없을 때(draft) | 번호 체계 하나(제휴 배치본 포함). 파일 경로보다 이름 변경에 강함 |
| 3 | 파일 경로 | 순위 이력처럼 원천이 경로로만 남긴 경우 | 경로 → 원고 → URL로 한 번 더 올라가 1순위 키로 바꾼다 |
| — | 제목·키워드 유사도 | **쓰지 않는다** | 명세 2.9. 같은 `queue.md`를 읽는 두 시스템은 키워드가 우연히 같다(감사 §D: 8건) |

- **Naver ↔ Tistory 짝:** Tistory `source: "[[NN편-…]]"`가 있으면 짝으로 묶는다. `origin: tistory-only`면 짝이 없다(NOT_APPLICABLE).
- **effective status:** Vault `status`와 RSS 대조로 승격된 값을 쓴다(published로 승격만 하고 강등하지 않는다 — 기존 규칙). draft는 성과 대상이 아니다(NOT_APPLICABLE).
- **Runtime Package ID:** 필드는 두지 않는다. 대응이 확인된 매핑이 생기면 그때 `runtime_package_id` + `identity_match_source`를 더한다. 지금은 모든 Runtime 패키지가 UNLINKED다.

---

## 3. ② 실제 Naver 순위 연결

### 3.1 귀속 문제 — 지금 기록은 "그 글"의 순위가 아니다 [확인]

`kw_pipeline.rank_track`은 키워드 SERP에서 **내 블로그의 첫 결과**를 순위로 적는다(`r["blog"] == MY_BLOG`). SERP 파서는 결과마다 logNo를 읽지만 저장할 때 버린다.

10-08 이력의 순위 있는 행 87개를 SERP 캐시와 대조했다. 캐시의 그 검색어 결과는 같은 실행에서 쓴 것이다(순위 일치).

| 결과 | 행 |
|---|---|
| 그 글 자신의 순위 (잡힌 결과의 logNo = 원고 URL logNo) | 80 |
| **내 블로그의 다른 글** 순위 | 3 |
| 원고에 대조할 URL logNo가 없음 | 4 |

틀린 3건의 실제 모습:

| 원고 | 검색어 | 기록된 순위 | 그 순위의 실제 글 | 대상 글 자신 |
|---|---|---|---|---|
| 01편 | 배민리뷰 | 27 | 12편 | 첫 페이지(30건)에 없음 |
| 29편 | 배민 리뷰 답글 | 19 | 12편 | 첫 페이지에 없음 |
| 43편 | 인스타 캡션 | 3 | 06편 | **7위** |

다른 글을 그 글의 성과로 세는 것이 3/83(3.6%)이다. 키워드를 공유하는 글(같은 클러스터)일수록 생기므로, **잠식(카니발리제이션) 판단에서 정확히 틀리는 방향**이다. 실제로 01편과 29편은 순위가 있는 것처럼 보였지만, 첫 페이지에 없었다.

### 3.2 I1 요구사항 — 귀속 계산 (향후 구현, 별도 승인)

한 검색어의 SERP 결과 전체(첫 페이지 30건)에 대해:

1. 대상 원고 URL에서 logNo를 읽는다. URL이 없으면 귀속하지 않는다(`attribution: NO_TARGET_URL`).
2. 결과 목록의 **모든** 항목 logNo와 비교한다.
3. `post_rank` = 대상 logNo와 일치하는 결과의 순위. 일치하는 결과가 없으면 `post_rank: null`, 상태 `NOT_IN_FIRST_PAGE`. **순위를 추측하지 않는다** — 내 블로그의 다른 결과로 대신하지 않는다.
4. `blog_best_rank` = 내 블로그 첫 결과의 순위. 그 결과의 `logno`와 그 logNo의 `article_key`를 함께 적는다. 이 값은 블로그 단위 지표이며 `post_rank` 자리에 쓰지 않는다.
5. 같은 블로그의 다른 글이 함께 잡히면 그 글들의 logNo·순위도 남긴다(`other_own_results`). 잠식 판단의 원자료다.
6. 파싱이 실패하면(`parsed: false`) 상태는 `UNKNOWN`이다. `post_rank`·`blog_best_rank` 모두 `null`.

"첫 번째 내 블로그 결과의 logNo만 저장"하는 수정은 이 요구사항을 채우지 못한다. 그것만으로는 대상 글이 더 아래에 있을 때(43편 7위)도, 아예 없을 때(01편·29편)도 구분할 수 없다.

### 3.3 관측 행과 날짜 (Q1 반영)

관측 행 하나(조인 뷰의 한 줄):

| 필드 | 값 |
|---|---|
| `article_key` | §2의 1순위 키(URL) |
| `platform` | `naver` |
| `keyword` | 원고 `keywords` 앞 2개(현재 `rank_track`과 같다) |
| `data_source` | `naver_blog_tab_serp` (실측. Runtime `blog_rank`의 API HUB `sort=sim`은 Proxy이며 이 흐름에 넣지 않는다) |
| `actual_observation_at` | SERP를 실제로 가져온 시각(I1 이후: 캐시의 저장 시각을 행에 저장). 확인할 수 없으면(I1 이전 행 전부) `null` — 다른 날짜로 채우지 않는다 |
| `run_date` | 그 행을 쓴 실행의 이력 파일 날짜. **별도 필드이며 관측 시각이 아니다** — `actual_observation_at`이 `null`이어도 그 자리를 대신하지 않는다 |
| `observation_date_basis` | `serp_fetch_time`(I1 이후) 또는 `run_date_only`(I1 이전: 실제 관측은 `run_date` 이전 최대 1일, 정확한 시각 모름) |
| `post_rank`, `blog_best_rank`, `attribution`, `state` | §3.2, §6 |

**실측 [확인]:** 10-08 이력의 순위는 10-07 02:24Z 무렵 가져온 SERP를 캐시 수명(1일) 안에서 재사용한 값이다(위 세 검색어 모두 10-07 02:24~02:27Z). `run_date`(10-08)와 실제 관측 사이가 약 21시간이다. 그래서 `run_date`를 관측일로 표기하지 않는다.

**체크포인트 (±3일 승인):** 체크포인트 판정 하나는 아래 세 값을 따로 적는다.

| 필드 | 뜻 |
|---|---|
| `target_checkpoint` | `D+7` / `D+14` / `D+28`과 그 목표 날짜(발행일 + N일) |
| `actual_observation_date` | 쓴 관측의 실제 날짜(`actual_observation_at`의 날짜). 실제 관측일을 확인할 수 없으면 **`null`** — `run_date`로 바꿔 넣지 않는다 |
| `checkpoint_offset_days` | 실제 관측일 − 목표 날짜 (부호 포함, 예: `-2`, `+1`). `actual_observation_date`가 `null`이면 **`null`** — 추측하지 않는다 |
| `run_date` | 참고용 별도 필드(§3.3 관측 행과 같은 뜻). 체크포인트 오프셋 계산에 쓰지 않는다 |

- ±3일 안의 가장 가까운 관측을 쓴다. 이 거리는 `actual_observation_date`로만 잰다. 없으면 `MISSING`이며 다른 날의 값을 끌어오지 않는다.
- 실제 관측일이 `null`인 행(I1 이전 전부)은 체크포인트를 채우지 못한다. 그 체크포인트는 `MISSING`(사유 `observation_date_unknown`)이고, 행 자체는 `run_date`와 함께 참고로만 보인다.
- 오프셋이 다른 값끼리 비교할 때는 오프셋을 함께 보인다. "D+14 순위 7"이 아니라 "D+14 목표, 실제 D+12 관측(−2), 순위 7"처럼 적는다.
- 판정은 기존 인사이트 3을 따른다. 새 글은 D+14부터 보고, 첫 주에는 판정하지 않는다.

### 3.4 과거 데이터

- 10-08 이전을 포함한 기존 이력 11개 파일의 순위 행은 모두 `attribution: UNVERIFIED`, `observation_date_basis: run_date_only`, `actual_observation_at: null`로 둔다.
- **UNVERIFIED 순위는 개별 글의 확정된 성과로 쓰지 않는다.** 그 글의 순위 표·체크포인트·후보 근거에 들어가지 않는다. 보이더라도 "내 블로그 최고 순위(귀속 미확인)"인 `blog_best_rank`로만 보인다.
- 이 문서의 80/3/4 대조는 한 번 해 본 측정이며, 과거 행을 VERIFIED로 올리는 근거가 아니다. 캐시는 덮어쓰이는 임시 파일이라 보존된 원본 증거가 아니다.
- 기존 이력 파일은 다시 쓰지 않는다.

---

## 4. ③ Tistory GSC 수동 데이터 연결

GSC 자동 수집은 보류다(Thomas 2026-10-08, 10-31 Tistory 평가 뒤 재검토). 지금 수동 경로는 두 갈래다 [확인]:

1. **검색어 단위:** `inflow.tsv`의 `google … 노출` 행. 노출, 클릭, 평균 순위, 도착 글(`/10` 같은 경로 또는 `?`)이 있다.
2. **글 단위:** 주간 보고서의 자유 서술. "GSC 노출/클릭 전: → 후:", "(GSC 3개월 기준 전: 노출 123·클릭 2·8.7위)" 같은 형태이며, 창(주간·3개월)이 글마다 다르다.

**연결 규칙:**

| 필드 | 규칙 |
|---|---|
| `article_key` | 도착 글 경로 `/N` → `thomasai.tistory.com/N`. `?`면 그 행은 검색어 단위로만 남고 글에는 붙지 않는다(`identity: UNLINKED`) |
| `data_source` | `gsc_manual` (사람이 옮긴 값임을 숨기지 않는다) |
| `observation_window` | **필수.** `week:YYYY-MM-DD`(주말일) 또는 `range:시작~끝`. 창이 다른 값끼리는 비교하지 않는다 |
| `impressions`, `clicks`, `avg_position` | 적힌 그대로. CTR은 저장하지 않고 계산한다(클릭/노출, 노출 0이면 `null`) |
| 빈칸 | 0이 아니라 `MISSING` |

- **자유 서술은 파싱하지 않는다.** 문장 속 숫자는 창·기준이 섞여 있어, 기계로 읽으면 틀린 값이 조용히 들어온다.
  - 글 단위 GSC 값이 필요해지면 **새 수동 표(I3, 승인 필요)**를 쓴다. 한 줄 형식: `주말일 · URL · 창 · 노출 · 클릭 · 평균 순위 · 메모`.
  - 기존 보고서와 `inflow.tsv`는 그대로 둔다.
- 순위(Naver 실측)와 GSC 평균 순위(Google, 노출 가중 평균)는 다른 지표다. 같은 열에 넣지 않는다.

---

## 5. ④ Prompt Version 추적

원고에는 지금 프롬프트 판 필드가 없다. 그래서 조인 시점에 판을 찾고, 그 근거의 종류에 따라 확실성을 적는다.

| 확실성 | 조건 | 지금 해당하는 경우 |
|---|---|---|
| `EXACT` | 생성 시점에 쓴 판이 **직접** 기록돼 있다 — 원고를 쓴 실행이 그 판의 식별자를 남겼다 | **없음.** I4(원고 저장 때 `prompt_rev:`)가 생기면 그 이후 원고만 |
| `APPROX` | 날짜로 추정했다: 크론 시각(전날 23:02Z)에 유효한 skills git 커밋, 또는 `Prompt/운영/버전.json`에서 `written` ≤ 원고 날짜인 마지막 판 | 2026-09-14 이후 대부분. skills git은 원고 작업 시점의 미커밋 변경을 보지 못하고(자동 커밋은 작업 뒤), `버전.json`은 3일 병합 창이 있다 |
| `UNKNOWN` | 추정할 근거가 없다 | `버전.json` 첫 판(09-14) 이전 원고 |

- 판 값은 `{분류: 판}` 묶음이다. Naver는 `원고-스킬`·`아침-작업-지시`, Tistory는 `SEO-스킬`·`SEO-프롬프트`. 확실성은 분류마다 따로 적는다.
- **실험 Cutover는 보존하되 증거 종류를 나눈다.** 예: 패턴 15 H판, Tistory 92편부터, skills `7ac7069`.
  - `cutover_record`: 실험 도구가 정한 경계(편 번호와 반영 커밋). 그대로 보존한다.
  - `applied_evidence`: 그 원고를 쓴 실행이 실제로 그 판을 읽었다는 직접 기록. 지금은 없다.
  - 경계 이후 번호라는 사실만으로는 `APPROX`다. 실제 적용 증거가 생겨야 `EXACT`다.
- **앞으로의 수정(I4, 승인 필요):** 크론이 원고를 저장할 때 `prompt_rev:`(skills HEAD + 미커밋 여부)를 프론트매터에 적게 한다. 미커밋 변경이 있었으면 그 원고는 `EXACT`가 아니라 `APPROX`로 남는다. 일일 프롬프트·스킬은 지금 Freeze 대상이므로 Freeze가 끝난 뒤에 다룬다.

---

## 6. ⑤ Missing Data 처리

**값이 없을 때 0을 쓰지 않는다.** 모든 관측 칸은 아래 상태 하나를 가진다:

| 상태 | 뜻 | 예 |
|---|---|---|
| `MEASURED` | 관측했고 값이 있다 | `post_rank` 7, 노출 123 |
| `NOT_IN_FIRST_PAGE` | 관측은 성공했지만 첫 페이지에 **대상 글**이 없다 | 01편 '배민리뷰'(내 다른 글 12편은 27위) |
| `UNKNOWN` | 관측을 시도했지만 실패했다 | `parsed: false` (09-18~10-08 전체 3행) |
| `MISSING` | 그 창에 관측이 없다 | 체크포인트 ±3일 안에 이력 파일 없음, GSC 칸 빈칸 |
| `NOT_APPLICABLE` | 대상이 아니다 | draft, 짝 없는 Tistory 전용 글의 Naver 칸 |
| `UNLINKED` | 값은 있지만 어느 글인지 모른다 | GSC 도착 글 `?`, Runtime 패키지 |

- `NOT_IN_FIRST_PAGE`는 "0위"도 "모름"도 아니다. 평균을 낼 때 빼고, 개수는 따로 센다.
- I1 이전 행에서 "순위 없음"(10-08: 61행)은 **내 블로그 결과가 하나도 없었다**는 뜻이라 `NOT_IN_FIRST_PAGE`로 볼 수 있다. 반면 "순위 있음"은 `attribution: UNVERIFIED`라 그 글의 순위로 확정하지 않는다.
- 집계마다 두 비율을 **따로** 낸다. 하나로 합치지 않는다.
  - **결측률** = (`MISSING`+`UNKNOWN`+`UNLINKED`) / 전체 — 값이 없는 비율.
  - **순위 귀속 검증률** = `attribution: VERIFIED`인 순위 행 / 순위가 잡힌 행 전체 — 값은 있지만 그 글의 것인지 확인된 비율. I1 이전 데이터는 0%다.
  - 결측이 0%여도 귀속 검증률이 낮으면, 그 순위는 개별 글 성과로 쓰지 못한다.

---

## 7. ⑥ Weekly Feedback Candidate 구조

성과는 **후보**만 만든다. Prompt·스케줄·품질 기준·발행·기존 글은 자동으로 바뀌지 않는다(명세 7.7). 후보는 주간 보고서의 한 절로 나가고, 채택 여부는 Thomas가 정한다.

**후보 한 건의 필드:**

| 필드 | 내용 |
|---|---|
| `candidate_id` | `주말일-유형-번호` |
| `type` | `keyword_strategy` · `prompt` · `content_format` · `editorial_priority` · `refresh`(보강) |
| `claim` | 관찰을 적는 한 문장 — **"차이가 관찰됐다"**까지만. "개선했다"·"효과가 있다"처럼 인과를 단정하지 않는다 |
| `evidence` | 관측 행 목록(`article_key` + `target_checkpoint` + `checkpoint_offset_days` + 상태) |
| `n` | 비교군별 글 수 |
| `window` | 관측 창 |
| `missing_rate` | §6 |
| `attribution_verified_rate` | §6 — 순위를 쓰는 후보라면 필수 |
| `confounders` | 키워드 수요 차이(월 검색량), 경쟁도, 발행 시기, 프롬프트 판 차이, 플랫폼 차이, 체크포인트 오프셋 차이 |
| `verdict` | `CANDIDATE` 또는 `INSUFFICIENT_EVIDENCE` |
| `proposed_action` | 사람이 할 일(제안문). 실행 경로는 없다 |
| `decision` | Thomas가 적는다: 채택 / 보류 / 기각 + 날짜 |

**`CANDIDATE`가 되기 위한 최소선 (Q2, 조건부 승인):** 아래를 모두 만족해야 후보로 *제안*할 수 있다. 만족해도 인과는 확정되지 않는다.

- 비교군마다 D+14가 지난 글이 **5편 이상**
- 결측률 **30% 이하**
- 같은 플랫폼끼리 비교 (플랫폼이 다르면 항상 `INSUFFICIENT_EVIDENCE`)
- 프롬프트 판 비교라면 비교군의 판 확실성이 `UNKNOWN`인 글이 절반 미만

**최소선을 넘어도 `INSUFFICIENT_EVIDENCE`를 유지하는 경우:**

- 교란 요인이 크다. 예: 두 비교군의 월 검색량 중앙값이 크게 다르거나, 발행 시기가 시즌 앞뒤로 갈리거나, 체크포인트 오프셋이 비교군마다 한쪽으로 치우쳤다.
- 차이가 한두 편에 몰려 있다.

판단 근거는 후보의 `confounders`에 적는다.

**후보를 만드는 질문 (처음엔 이 셋만):**

1. 같은 클러스터의 새 글이 기존 글의 순위·유입을 깎았나 (잠식). **귀속이 검증된(`VERIFIED`) `post_rank`만 쓴다.** UNVERIFIED 순위와 `blog_best_rank`는 쓰지 않으므로, I1 이전 데이터로는 판단하지 않는다.
2. 프롬프트 판이 바뀐 앞뒤로 D+14 성과가 달라졌나. 판 확실성이 대부분 `APPROX`이므로 결과는 "관찰"로만 적는다.
3. 보강(`updated:`) 앞뒤로 순위가 움직였나. 기존 '보강 전후' 절을 그대로 쓴다.

---

## 8. 어디서 돌리나 (구현 시, 각각 승인 필요)

- **기본안: 계산된 조인 뷰(I2).** Vault 도구가 §1의 원천을 **읽기만** 해서 관측 행과 후보를 계산한다. 새 Source of Truth를 만들지 않는다. 제휴 READY를 저장하지 않고 매번 계산하는 것과 같은 원칙이다.
- **영속 저장은 두 군데만**, 각각 승인 뒤에 둔다:
  - 순위 행의 귀속 칸(I1): `post_rank`, `blog_best_rank`, logNo들, `actual_observation_at`
  - 글 단위 GSC 수동 표(I3)
- **PR 3.4 Status Board (Q4):** 성과·결측·체크포인트 표시를 I2 조인 뷰의 출력에서 가져온다. 원천 파일을 따로 읽는 Outcome 파싱 엔진을 3.4에 만들지 않는다. 3.4 구현은 미승인이다.
- **Runtime:** 이 흐름에 쓰지 않는다. `blog_rank`·`record_published_url`·`blog_rank_snapshot`은 D8(11-25) 존폐 판정까지 그대로 둔다.

## 9. 구현 항목과 순서 (모두 미승인, 각각 별도 승인)

**권장 순서 (Q3 수정):** I2 → 독립 검증·정확도 확인 → I5. I2와 I5는 한 묶음이 아니다. I2의 검증 결과를 보고 I5를 따로 승인받는다.

| # | 항목 | 바꾸는 것 | 선행 조건 |
|---|---|---|---|
| I2 | 조인 뷰 + 후보 계산(읽기 전용) | Vault `tools/`에 새 모듈 하나 | 별도 승인 |
| — | **I2 독립 검증** | 없음(검증만) | I2 뒤. 기준은 아래 |
| I5 | 주간 보고서에 '성과 조인·후보' 절 | `blog-weekly.py` | I2 검증 통과 + 별도 승인 |
| I1 | `rank_track` 귀속 계산(§3.2 전체)과 실제 관측 시각 저장 | `kw_pipeline.py` 한 함수, 이력 JSON 새 행에 칸 추가(기존 행 유지) | Vault Keyword Pipeline Freeze 해제 또는 별도 승인 |
| I3 | 글 단위 GSC 수동 표 | 새 TSV 하나 | 별도 승인. GSC API는 10-31 뒤 별도 |
| I4 | 원고 저장 때 `prompt_rev:` | 일일 프롬프트/스킬 | 패턴 15 5+5 관찰과 Freeze가 끝난 뒤 별도 승인 |

**I2 독립 검증 기준 (I5 승인 요청 전에 보고):**

- **Article Key 정확도:** 무작위 20편의 조인 결과(URL·짝·effective status)를 원고 프론트매터와 라이브 URL로 손으로 대조한다. 불일치 0건이어야 한다.
- **결측 상태:** 6상태별로 최소 1건씩 원천 행을 짚어 상태가 맞는지 확인한다. 0으로 바뀐 결측이 0건이어야 한다.
- **날짜:** 체크포인트 행 전부에서 `actual_observation_date`와 `checkpoint_offset_days`가 원천과 일치해야 한다. 실제 관측일을 모르는 행에서 두 값 중 하나라도 채워진 경우가 0건이어야 한다(`run_date`로 채운 경우 포함).
- **귀속:** UNVERIFIED 순위가 개별 글의 성과·체크포인트·잠식 근거로 쓰인 경우가 0건이어야 한다. 결측률과 귀속 검증률이 따로 나와야 한다.
- **UNLINKED:** 키워드·제목만으로 연결된 행이 0건이어야 한다.
- **재현성:** 같은 입력으로 두 번 돌린 출력이 같아야 하고, 실행 전후 Vault `git status`가 같아야 한다(쓰기 0).

## 10. 열린 질문

Q1~Q4는 모두 결정됐다(위 '결정' 절). 남은 결정은 I1~I5의 구현 승인뿐이다.
