# 블로그 Outcome Integration 설계 — PR 3.3 (V0.1)

**상태:** DRAFT 2026-10-08 — PR 3.3 설계만(Thomas 2026-10-08 DESIGN_ONLY 승인). 발행 글의 성과를 Vault Article ID·Published URL로 묶는 규칙, 실제 Naver 순위·Tistory GSC 수동값 연결, Prompt Version 추적, 결측 어휘, 주간 Feedback Candidate 구조를 제안하고, 구현 항목 I1~I5와 열린 질문 Q1~Q4가 Thomas 결정을 기다린다. 코드·데이터 파일·스케줄 변경 0건.

**요청:** PR 3.0 감사 결정(Thomas 2026-10-08, `BLOG_RUNTIME_PHASE3_BASELINE_AUDIT_V0.1.md`의 '결정' 절)의 PR 3.3 — 아래 여섯 가지만 설계한다: ① Vault Article ID·Published URL 기반 연결, ② 실제 Naver 순위 연결, ③ Tistory GSC 수동 데이터 연결, ④ Prompt Version 추적, ⑤ Missing Data 처리, ⑥ Weekly Feedback Candidate 구조.
**기준 시점:** 2026-10-08. Vault `725f639`, scripts `974b5a0`, skills `53aafcd`.
**표기:** **[확인]** 이 문서를 쓰며 데이터를 직접 읽어 셈. **[문서]** 기존 기록.
**하지 않는 것:** Runtime 코드, 새 수집 작업·스케줄, GSC OAuth·API 자격증명, 기존 데이터 파일 수정, 발행본 수정. Runtime `blog_rank`·`record_published_url`을 이 흐름에 연결하지 않는다. Runtime Package ID는 실제 대응이 확인되지 않으면 UNLINKED이고, 키워드 유사성만으로 연결하지 않는다.

---

## 0. 한 줄

**성과 데이터는 이미 Vault에 있다. 빠진 것은 "같은 글"을 가리키는 키와 결측의 이름이다.** 설계의 중심은 ① 발행 URL을 1순위 키로 삼는 Article Key, ② 순위를 "그 글"의 순위로 귀속하는 규칙(지금은 "내 블로그 글 중 최고 순위"를 기록한다), ③ 0과 "모름"을 구분하는 결측 어휘다. 저장소를 새로 만들지 않고, 기존 파일을 읽어 계산하는 **조인 뷰**를 주간 보고서에 붙이는 것이 기본안이다.

---

## 1. 지금 있는 데이터 [확인]

| 원천 | 위치 | 키 | 무엇 | 비고 |
|---|---|---|---|---|
| 원고 프론트매터 | `content/naver/NN편-*.md`(+`제휴/NN편-*`), `content/tistory/NN-*.md` | 파일 경로, `episode` | `url`, `status`, `published`, `scheduled`, `created`, `keywords`, `cluster`, `form`, Tistory `source: "[[NN편-…]]"`, `origin` | Naver 98편: published 72편 모두 `url` 있음, draft 26편은 빈칸. Tistory 91편 모두 `url` 있음(예약 22편 포함) |
| Naver 순위 이력 | `analytics/keywords/history/YYYY-MM-DD.json`의 `ranks` | `file` + `kw` | `rank`, `prev`, `parsed`, `in_title`, `title_diff`, `published` | 파일 11개(09-18~10-08, 불규칙 — 큐를 다시 만든 날만). 실제 블로그 탭(`search.naver.com … tab.blog.all`) 첫 페이지. SERP 캐시 수명 1일 |
| 유입 검색어 | `analytics/keywords/inflow.tsv` | 주말일 + 플랫폼 + 검색어 | 값, 지표(유입/노출), 도착 글, google 행은 클릭·평균 순위 | 주간 분석이 손으로 덧붙임. google 노출 19행 중 도착 글 `?` 6행 |
| 주간 보고서 | `analytics/YYYY-MM-DD.md` | 글 링크(URL) | 보강 전후·D+14 판정 칸, "GSC 노출/클릭 전: → 후:", "주간 조회:" | 값은 사람이 옮겨 적음. 창이 섞여 있음(주간 / "GSC 3개월 기준") |
| 브랜드 지표 | `analytics/branding.tsv` | 주말일 | 블로그 전체 주간 합계 | 글 단위 아님. 못 구한 값은 "미확인"(빈칸 아님) — 이 설계의 결측 원칙과 같다 |
| 프롬프트 판 | `Prompt/운영/버전.json`, `Prompt/운영/<분류>/버전/*` | 분류(원고-스킬·SEO-스킬·SEO-프롬프트·아침-작업-지시 …) | 판 번호, `written` 날짜, `sha` | 3일 병합 창(MERGE_DAYS) — 판 내용은 그 창의 마지막 상태 |
| 스킬 git | `~/.claude/skills` (2026-10-06부터 로컬 git) | 커밋 | 스킬 정본의 정확한 판 | 크론의 자동 커밋은 원고 작업 **뒤**에 돈다 |
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

`kw_pipeline.rank_track`은 키워드 SERP에서 **내 블로그의 첫 결과**를 순위로 적는다(`r["blog"] == MY_BLOG`). SERP 파서는 logNo를 읽지만 저장할 때 버린다. 10-08 이력의 순위 있는 행 87개를 SERP 캐시(logNo 포함)와 대조하니:

| 결과 | 행 |
|---|---|
| 그 글 자신의 순위 (캐시 logNo = 원고 URL logNo) | 80 |
| **내 블로그의 다른 글** 순위 (예: `01편`의 '배민리뷰' 27위, `29편`의 '배민 리뷰 답글' 19위, `43편`의 '인스타 캡션' 3위) | 3 |
| 원고에 대조할 URL logNo가 없음 | 4 |

다른 글을 그 글의 성과로 세는 것이 3/83(3.6%)이다. 작아 보이지만, 키워드를 공유하는 글(같은 클러스터)일수록 생기므로 **잠식(카니발리제이션) 판단에서 정확히 틀리는 방향**이다.

### 3.2 설계

관측 행 하나(조인 뷰의 한 줄):

| 필드 | 값 |
|---|---|
| `article_key` | §2의 1순위 키(URL) |
| `platform` | `naver` |
| `keyword` | 원고 `keywords` 앞 2개(현재 `rank_track`과 같다) |
| `observation_date` | 이력 파일 날짜. SERP 캐시 수명이 1일이므로 실제 관측은 그 전날일 수 있다 — `observation_tolerance_days: 1`을 같이 적는다 |
| `data_source` | `naver_blog_tab_serp` (실측. API 순위와 섞지 않는다 — Runtime `blog_rank`의 API HUB `sort=sim`은 Proxy이며 이 흐름에 넣지 않는다) |
| `post_rank` | logNo가 이 글의 URL logNo와 같은 결과의 순위 |
| `blog_best_rank` | 내 블로그 첫 결과의 순위(지금 기록하는 값) |
| `attribution` | `VERIFIED`(logNo 일치) · `OTHER_POST`(내 다른 글이 잡힘 — 그 글의 `article_key`를 함께 적음) · `UNVERIFIED`(logNo가 저장되지 않은 과거 행) |
| `state` | §6 어휘 |

- **과거 행(10-08 이전 11개 파일):** logNo가 없으므로 `attribution: UNVERIFIED`. 캐시는 1일 수명이라 과거를 되살리지 못한다. 고치지 않고 표시만 한다.
- **체크포인트:** 이력이 매일 있지 않다(11개 파일, 불규칙). D+7·D+14·D+28은 발행일 기준 **±3일 안의 가장 가까운 관측**을 쓰고, 없으면 `MISSING`. 판정 기준은 기존 인사이트 3(새 글은 D+14부터, 첫 주는 판정하지 않음)을 따른다.
- **앞으로의 수정(I1, 승인 필요):** `rank_track`이 `logno`(잡힌 결과의 logNo)를 행에 함께 저장하게 한다. 기존 행은 다시 쓰지 않는다. `kw_pipeline.py`는 지금 Freeze 대상이라 이 수정은 Freeze 해제 또는 별도 승인 뒤다.

---

## 4. ③ Tistory GSC 수동 데이터 연결

GSC 자동 수집은 보류다(Thomas 2026-10-08, 10-31 Tistory 평가 뒤 재검토). 지금 수동 경로는 두 갈래다 [확인]:

1. **검색어 단위:** `inflow.tsv`의 `google … 노출` 행 — 노출, 클릭, 평균 순위, 도착 글(`/10` 같은 경로 또는 `?`).
2. **글 단위:** 주간 보고서의 자유 서술 — "GSC 노출/클릭 전: → 후:", "(GSC 3개월 기준 전: 노출 123·클릭 2·8.7위)". 창(주간·3개월)이 글마다 다르다.

**연결 규칙:**

| 필드 | 규칙 |
|---|---|
| `article_key` | 도착 글 경로 `/N` → `thomasai.tistory.com/N`. `?`면 그 행은 검색어 단위로만 남고 글에는 붙지 않는다(`identity: UNLINKED`) |
| `data_source` | `gsc_manual` (사람이 옮긴 값임을 숨기지 않는다) |
| `observation_window` | **필수.** `week:YYYY-MM-DD`(주말일) 또는 `range:시작~끝`. 창이 다른 값끼리는 비교하지 않는다 |
| `impressions`, `clicks`, `avg_position` | 적힌 그대로. CTR은 저장하지 않고 계산한다(클릭/노출, 노출 0이면 `null`) |
| 빈칸 | 0이 아니라 `MISSING` |

- **자유 서술 파싱은 하지 않는다.** 문장 속 숫자는 창·기준이 섞여 있어 기계로 읽으면 틀린 값이 조용히 들어온다. 앞으로 글 단위 GSC 값이 필요하면 **새 수동 표(I3, 승인 필요)** 한 줄 형식을 쓴다: `주말일 · URL · 창 · 노출 · 클릭 · 평균 순위 · 메모`. 기존 보고서·`inflow.tsv`는 그대로 둔다.
- 순위(Naver 실측)와 GSC 평균 순위(Google, 노출 가중 평균)는 다른 지표다. 같은 열에 넣지 않는다.

---

## 5. ④ Prompt Version 추적

원고에는 지금 프롬프트 판 필드가 없다. 판은 원고의 `created` 날짜로 **조인 시점에 추정**하고, 추정의 확실성을 같이 적는다:

| 기간 | 출처 | 방법 | `prompt_version_confidence` |
|---|---|---|---|
| 2026-10-06 이후 | skills git | 그 원고를 만든 크론 시각(전날 23:02Z)에 유효한 HEAD 커밋 | `EXACT` — 단, 그 시각 스킬 폴더에 **미커밋 변경**이 있었으면 `APPROX` (크론의 자동 커밋은 원고 작업 뒤에 돈다) |
| 2026-10-06 이전 | `Prompt/운영/버전.json` | `written` ≤ 원고 날짜인 마지막 판 | `APPROX` — 3일 병합 창 때문에 판 내용과 실제 사용본이 다를 수 있다 |
| 실험 판 | 실험 도구의 CUTOVER (예: 패턴 15 H판, Tistory 92편부터) | 편 번호 | `EXACT`(그 실험 범위 안) |
| 그 밖 | — | — | `UNKNOWN` |

- 기록 대상 분류: Naver는 `원고-스킬`·`아침-작업-지시`, Tistory는 `SEO-스킬`·`SEO-프롬프트`. 한 글에 여러 분류의 판을 함께 적는다.
- **앞으로의 수정(I4, 승인 필요):** 크론이 원고를 저장할 때 `prompt_rev:`(skills HEAD + dirty 여부)를 프론트매터에 적게 한다. 일일 프롬프트·스킬은 지금 Freeze 대상이므로 Freeze가 끝난 뒤에 다룬다.

---

## 6. ⑤ Missing Data 처리

**값이 없을 때 0을 쓰지 않는다.** 모든 관측 칸은 아래 상태 하나를 가진다:

| 상태 | 뜻 | 예 |
|---|---|---|
| `MEASURED` | 관측했고 값이 있다 | 순위 7, 노출 123 |
| `NOT_IN_FIRST_PAGE` | 관측은 성공했지만 첫 페이지에 이 글이 없다 | `parsed: true`, 순위 없음 (10-08: 61행) |
| `UNKNOWN` | 관측을 시도했지만 실패했다 | `parsed: false` (09-18~10-08 전체 3행) |
| `MISSING` | 그 창에 관측이 없다 | 체크포인트 ±3일 안에 이력 파일 없음, GSC 칸 빈칸 |
| `NOT_APPLICABLE` | 대상이 아니다 | draft, 짝 없는 Tistory 전용 글의 Naver 칸 |
| `UNLINKED` | 값은 있지만 어느 글인지 모른다 | GSC 도착 글 `?`, Runtime 패키지 |

- `NOT_IN_FIRST_PAGE`는 "0위"도 "모름"도 아니다. 평균을 낼 때 빼고, 개수는 따로 센다.
- 집계마다 **결측률**(`MISSING`+`UNKNOWN`+`UNLINKED` / 전체)을 함께 낸다.

---

## 7. ⑥ Weekly Feedback Candidate 구조

성과는 **후보**만 만든다. Prompt·스케줄·품질 기준·발행·기존 글은 자동으로 바뀌지 않는다(명세 7.7). 후보는 주간 보고서의 한 절로 나가고, 채택 여부는 Thomas가 정한다.

**후보 한 건의 필드:**

| 필드 | 내용 |
|---|---|
| `candidate_id` | `주말일-유형-번호` |
| `type` | `keyword_strategy` · `prompt` · `content_format` · `editorial_priority` · `refresh`(보강) |
| `claim` | 한 문장 (예: "판정형 H판 Tistory 글이 이전 판보다 D+14 GSC 노출이 높다") |
| `evidence` | 관측 행 목록(`article_key` + 체크포인트 + 상태) |
| `n` | 비교군별 글 수 |
| `window` | 관측 창 |
| `missing_rate` | §6 |
| `confounders` | 키워드 수요 차이(월 검색량), 경쟁도, 발행 시기, 프롬프트 판 차이, 플랫폼 차이 |
| `verdict` | `CANDIDATE` 또는 `INSUFFICIENT_EVIDENCE` |
| `proposed_action` | 사람이 할 일(제안문). 실행 경로는 없다 |
| `decision` | Thomas가 적는다: 채택 / 보류 / 기각 + 날짜 |

**`INSUFFICIENT_EVIDENCE` 기준 (보수적 기본값, Q2에서 확정):**

- 비교군마다 D+14가 지난 글이 **5편 미만**이면
- 결측률이 **30%를 넘으면**
- 비교군의 프롬프트 판이 섞여 `UNKNOWN`이 절반을 넘으면
- 플랫폼이 다른 값끼리 비교하면 (항상 불충분)

**후보를 만드는 질문(처음엔 이 셋만):** ① 같은 클러스터의 새 글이 기존 글의 순위·유입을 깎았나(잠식 — §3.1의 `post_rank`가 있어야 판단 가능), ② 프롬프트 판이 바뀐 앞뒤로 D+14 성과가 달라졌나, ③ 보강(`updated:`) 앞뒤로 순위가 움직였나(기존 '보강 전후' 절을 그대로 쓴다).

---

## 8. 어디서 돌리나 (구현 시, 승인 필요)

- **기본안: 계산된 조인 뷰.** Vault 도구가 위 원천을 **읽기만** 해서 관측 행과 후보를 계산하고, `blog-weekly.py`가 만드는 주간 보고서에 한 절로 붙인다. 새 Source of Truth를 만들지 않는다(제휴 READY를 저장하지 않고 매번 계산하는 것과 같은 원칙).
- **영속 저장은 두 군데만**, 각각 승인 뒤에: 순위 행의 `logno`(I1, 기존 writer에 칸 하나), 글 단위 GSC 수동 표(I3, 새 파일).
- **Runtime:** 이 흐름에 쓰지 않는다. `blog_rank`·`record_published_url`·`blog_rank_snapshot`은 D8(11-25) 존폐 판정까지 그대로 둔다.

## 9. 구현 항목 (모두 미승인)

| # | 항목 | 바꾸는 것 | 선행 조건 |
|---|---|---|---|
| I1 | `rank_track`이 잡힌 결과의 `logno` 저장 | `kw_pipeline.py` 한 함수, 이력 JSON 새 행에 칸 하나 | Vault Keyword Pipeline Freeze 해제 또는 별도 승인 |
| I2 | 조인 뷰 + 후보 계산(읽기 전용) | Vault `tools/`에 새 모듈 하나 | 승인 |
| I3 | 글 단위 GSC 수동 표 | 새 TSV 하나 | 승인. GSC API는 10-31 뒤 별도 |
| I4 | 원고 저장 때 `prompt_rev:` | 일일 프롬프트/스킬 | 패턴 15 5+5 관찰과 Freeze가 끝난 뒤 |
| I5 | 주간 보고서에 '성과 조인·후보' 절 | `blog-weekly.py` | I2 뒤 |

## 10. 열린 질문 (기본값 포함)

- **Q1.** 체크포인트 허용 오차 — 기본값 **±3일**. 이력이 큐를 다시 만든 날에만 생기므로, 더 좁히면 대부분 `MISSING`이 된다.
- **Q2.** `INSUFFICIENT_EVIDENCE` 기준 — 기본값 **비교군당 5편, 결측률 30%**.
- **Q3.** 첫 구현 묶음 — 기본값 **I2+I5만**(읽기 전용, Freeze와 무관). I1·I4는 Freeze가 끝난 뒤.
- **Q4.** 3.4 Read-only Status 설계를 이 조인 뷰 위에 얹을지 — 기본값 **얹는다**(같은 원천을 두 번 읽는 도구를 만들지 않는다).
