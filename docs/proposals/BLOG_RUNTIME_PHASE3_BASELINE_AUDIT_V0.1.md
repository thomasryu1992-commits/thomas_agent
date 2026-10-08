# 블로그 Runtime Phase 3 — PR 3.0 Baseline & Ownership Audit (V0.1)

**상태:** DRAFT 2026-10-08 — PR 3.0 읽기 전용 감사. 블로그 실생산 경로는 Vault 크론이며 Runtime 블로그 레인은 생산에 쓰이지 않는다는 근거와, PR 3.1~3.5 권고(3.1 DEFER · 3.2 NOT_NEEDED · 3.3 DESIGN_ONLY · 3.4 DESIGN_ONLY · 3.5 DEFER)가 Thomas 결정을 기다린다. 코드·스키마·크론·프롬프트·설정 변경 0건.

**요청:** "Blog Runtime Quality & Outcome Phase 3" 명세(Thomas 2026-10-08)의 PR 3.0만 — 소유권·실행 경로·준비 상태 어휘·ID 연결·운영 Freeze를 확인하고 후속 PR별 권고를 낸다. 이 Phase 3는 Vault 운영의 *Affiliate Phase 3 (Topic-aware Placement)* 와 다른 작업이다.
**기준 시점:** 2026-10-08 04:30Z 무렵. `origin/main` = `0ce86493` (#1180, 명세의 리뷰 기준 커밋과 같다). 운영 이미지 `candidate-1180`(`thomas-scheduler`).
**표기:** **[확인]** 이 문서를 쓰며 코드·상태·로그를 직접 읽거나 실행함. **[문서]** 저장소·Vault 기록. **[미확인]** 근거를 찾지 못함.
**한계:** 라이브 돈 경로·키는 열지 않았다. 외부 플랫폼(네이버·티스토리·GSC)에 로그인하거나 새로 수집하지 않았다. 2026-10-09 Canary는 아직 실행 전이다.

---

## 0. 한 줄

**실제 블로그 글은 Thomas Agent Runtime이 아니라 Vault 크론(`blog-daily-draft.sh` → headless Claude + 두 스킬 + Vault 도구)이 만든다.** Runtime 블로그 레인은 코드·테스트·배포는 살아 있지만 정기 실행 행 3개가 모두 꺼져 있고, 패키지 57개는 전부 수동 시험 발사이며, 발행·순위로 이어진 것이 0건이다 **[확인]**. 그래서 Runtime의 품질 Gate·우선순위·성과 코드를 바꾸면 **오늘 생산 글 0편이 달라진다**. 명세가 원하는 질문(검토 가능한가·중복인가·지금 써야 하나·발행됐나·성과는)의 실제 소유자는 대부분 Vault 쪽이며, 후속 PR은 그 사실에서 출발해야 한다.

---

## A. Ownership Matrix

| Capability | Owner (confirmed / unverified) | Source of Truth | File / Service | Runtime used? | Evidence | 수정 권한 |
|---|---|---|---|---|---|---|
| 키워드 후보·수요·Queue | **Vault — confirmed** | `analytics/keywords/queue.md` (머리말 `2026-10-08`) | `obsidian-thomas/tools/kw_pipeline.py` (`~/.claude/scripts/kw-pipeline.py` 심링크), 크론이 매일 `run --max-age 7` | Runtime은 같은 `queue.md`를 **읽기 전용 마운트**로 읽을 수 있음(`VaultKeywordQueue`, `source=queue`) — 그 행은 꺼짐 | crontab `2 23 * * *`; compose `analytics/keywords:ro` (pipeline-worker) **[확인]** | Vault 소유자(Thomas). Runtime 쓰기 경로 없음 |
| Lane/Cluster/Facet/Intent/Entity | **Vault — confirmed** | `clusters.tsv`·`entities.tsv` (Vault git `725f639`) | `tools/blog_taxonomy.py` | **아니오** — Runtime에 대응 개념 없음(키워드 lexicon `blog_overlap.INTENT_MARKERS`만 있음) | grep: 외부 스크립트에 `mvp_runtime` import 0건 **[확인]** | Vault |
| 중복·보강 판정 | **Vault — confirmed** (V16, `DECISION_MODE = "canary"` `kw_pipeline.py:1020`) + 레거시 `covered_by`가 여전히 Gate | `update_candidates.tsv`·`rejected.tsv`·`decision_report.md` | `blog_taxonomy decide`, `kw_pipeline decision_pass`, `blog-variety` V15/V16 | Runtime에 **별도 엔진** `blog_overlap`(lexicon, 같은 플랫폼 `cannibalization_risk`→BLOCK) — 생산에 안 쓰임 | §D | Vault. Runtime 엔진은 휴면 |
| 내부 링크 추천 | **Vault — confirmed** (P1~P5 Link Engine) | `link_report.md`, `kw-pipeline.py check`의 '내부 링크 후보' | `blog_taxonomy link_candidates` | Runtime은 Tistory 패키지에 자체 `internal_links` 검사만(advisory) | — | Vault. 발행본 자동 삽입 없음 |
| Slot C Shadow | **Vault — confirmed** | `slot_c_shadow.tsv`, `slot_c_report.md` | `kw-pipeline.py slotc` (크론 원고 작업 앞뒤) | 아니오 | `slotc --stats`: valid_cron 1 · manual/rehearsal 1 · skipped 0 **[확인]** | Vault, 14회 동안 규칙 Freeze |
| 제휴 상품·증거·배치 READY | **Vault + scripts — confirmed** | `affiliate_stock.json`, `제휴/키워드/*.md`, 클립 | `blog-affiliate-place.py`(READY 매번 계산), `affiliate-clips.py`, `blog_taxonomy.affiliate_bridge`, `blog-variety` V11 | 아니오 | `--list`: 배치 가능 3 · 보류 1(반찬냉장고 `missing_story`) · `다음 배치: 제휴-라벨-프린터기.md` **[확인]** | Vault, Canary 전 Freeze |
| 원고 생성 | **Vault 크론 — confirmed** (headless Claude, 운영 모델 Opus 5.5) | Vault `content/naver/`, `content/tistory/` | `~/.claude/scripts/blog-daily-draft.sh` + `.prompt.md` + skills `naver-blog-draft`·`naver-to-google-seo` | **아니오.** 일일 프롬프트가 "thomas_agent 저장소와 그 스케줄은 건드리지 않는다"고 명시 | `blog-daily-draft.prompt.md:12` **[확인]**; Tistory 생산 경로 결정 (EXPANSION_READINESS Q3, Thomas 2026-10-06) **[문서]** | Vault/skills |
| 품질 평가 (생산) | **Vault — confirmed** | preflight FAIL 0이어야 저장, variety WARN(V13·V14·V15)·R1은 고친 뒤 저장 | `blog-preflight.py`, `blog-variety.py` | 아니오 | 일일 프롬프트 '반드시 지킬 것' **[확인]** | Vault/scripts |
| 품질 평가 (Runtime) | Runtime — confirmed (시험 발사에만) | Ledger `blog_content_package.quality` | `blog_content.quality_record`, `blog_quality.layers`, `blog_draft_score` | 수동 발사 57건에서만 | §C, §E **[확인]** | Runtime(PR + 승인) |
| Runtime Package·Ledger·Scheduler | Runtime — confirmed | `runtime_ledger/records.jsonl`, `schedules.jsonl` | `blog_content.py`, `scheduler.py` (`content_ideation`) | 행 3개 모두 `enabled: False` | §B **[확인]** | Runtime(PR + 승인) |
| 순위 추적 | **Vault — confirmed** (실제 네이버 블로그 탭 스크랩) | `analytics/keywords/history/*.json` `ranks` (10-08: 148행) | `kw_pipeline.rank_track` → `serp()` = `search.naver.com … tab.blog.all` | Runtime `blog_rank`는 API HUB `sort=sim`(Proxy) — **스냅샷 0행** | §E **[확인]** | Vault |
| 주간 분석·GSC | **Vault — confirmed** (GSC는 사람이 옮겨 적음) | `analytics/YYYY-MM-DD.md`, `inflow.tsv`, `branding.tsv` | `blog-weekly.py` (로그인 없는 값만 자동; 조회·유입·GSC는 손으로) | 아니오 | `blog-weekly.py` docstring **[확인]** | Vault. GSC 자동 수집은 새 자격증명 → 별도 승인 |
| Tistory Pattern 15 | **skills — confirmed** | `naver-to-google-seo/assets/prompt.txt` ② 두 줄 (skills `7ac7069`) | 측정: `Prompt/채점/원본/틀반복-2026-10-08/도구/track_prod.py` (CUTOVER=92, N=5) | 아니오 | Tistory 최신 91편 → **관찰 창이 아직 안 열림(0/5+5)** **[확인]** | skills, 5+5 동안 Freeze |
| 발행 (Naver) | **사람 — confirmed** | 라이브 블로그, Vault `status`(RSS로 effective published 승격) | Thomas 수동 업로드 (skill '일일 작업' F) | 아니오 | 일일 프롬프트 "네이버도 발행하지 않는다" **[확인]** | Thomas |
| 발행 (Tistory) | **운영자 시작 예약 → 플랫폼 자동 공개 — confirmed** | 티스토리 예약 목록 | `tistory_queue.py` + SSH 세션 브라우저 예약(Mac 루틴은 10-05부터 tistory.com 차단) | 아니오 | 메모·`tools/tistory-autopost-procedure.md` §0-1 **[문서]** | Thomas |

### 저장소 상태 (각각 따로)

| 저장소 | Revision | 미커밋 |
|---|---|---|
| thomas_agent `origin/main` | `0ce86493` (#1180) | — (이 감사는 별도 worktree `docs/blog-phase3-baseline-audit`) |
| thomas_agent 호스트 체크아웃 | `f8efbe2f` detached, main보다 16커밋 뒤 — **블로그 파일은 main과 동일** | 0 |
| 운영 이미지 `thomas-scheduler` | `candidate-1180` — 블로그 모듈 8개 sha256이 `origin/main`과 동일 | — |
| Vault `/root/obsidian-thomas` | `725f639` | 0 |
| scripts `~/.claude/scripts` | `974b5a0` | 0 |
| skills `~/.claude/skills` | `53aafcd` | 0 |

---

## B. End-to-End Execution Map

### Naver (실생산)

| 단계 | 실제 경로 | 상태 |
|---|---|---|
| 키워드 | 크론 셸이 `kw-pipeline.py run --max-age 7` → `queue.md` (+ decision_pass canary, rank_track) | ACTIVE |
| 선정 | headless Claude가 스킬 STEP 0 순서(시즌 임박 → 회전 규칙 → 변형)로 2개, 제휴 차례면 `blog-affiliate-place.py`가 재고 1편을 배치하고 일반 글 1편만 | ACTIVE |
| 원고 생성 | headless Claude (`naver-blog-draft`) | ACTIVE |
| 품질 평가 | `blog-preflight.py` FAIL 0 필수, `blog-variety.py` (V11 FAIL·V13/V14/V15 WARN 해소, V16 WARN) | ACTIVE |
| 보관 | Vault `content/naver/NN편-*.md`, `status: draft`, `scheduled:` = 올릴 날 | ACTIVE |
| 검토·발행 | Thomas가 직접 업로드 | ACTIVE (사람) |
| URL 기록 | Vault 프론트매터 `url:` (98편 중 97편 채워짐); effective published는 RSS 대조로 승격만 | ACTIVE |
| 성과 | `rank_track`(실제 블로그 탭), `blog-weekly.py`(RSS·공감·댓글), 조회수는 사람이 옮김 | ACTIVE |
| Slot C | 원고 작업 앞뒤 스냅샷 → `slotc --before … --run-source cron` 그림자 기록 | ACTIVE (shadow) |

### Tistory (실생산)

| 단계 | 실제 경로 | 상태 |
|---|---|---|
| 키워드·선정 | 네이버 글 변환, 또는 Google 자리 "없음"이면 `kw-pipeline.py tistory-pick`으로 AI 도구 전용 글 (skill 1-D) | ACTIVE |
| 원고·품질 | headless Claude (`naver-to-google-seo`) + `blog-preflight.py draft`(X1/X2/X3/K4/K5/G1/Q1/AF1) | ACTIVE |
| 보관 | Vault `content/tistory/NN-slug.md` (`source: "[[NN편-…]]"`로 네이버 원본과 연결) | ACTIVE |
| 발행 | 운영자가 시작한 세션이 예약 → 예약 시각에 티스토리가 공개 (`status: scheduled` 22편) | ACTIVE (사람이 예약) |
| 성과 | GSC 페이지별 노출·클릭을 주간 보고서·`inflow.tsv`에 손으로 옮김 | ACTIVE (수동) |

### Runtime 블로그 레인

| 단계 | 경로 | 상태 |
|---|---|---|
| 정기 발사 | `content_ideation` 행 3개: `1d25e2…`(고정 시드, 3회 발사 모두 `NO_ELIGIBLE_KEYWORD`, 패키지 0) · `876d53…`(`source=queue`, 09-30 disabled, **발사 0회**) · `d7972f…`(Tistory, 10-05 enabled→10-06 disabled, **발사 0회**) | IMPLEMENTED_BUT_NOT_ACTIVE |
| 수동 발사 | CLI/디스패치로 57건 (09-28 3 · 09-29 14 · 09-30 32 · 10-05 6 · 10-06 2) — 모두 시험·비교용 | 시험용으로만 실행됨 |
| 선정 | `select_target_keyword` + `VaultKeywordQueue` + `blog_overlap` | IMPLEMENTED_BUT_NOT_ACTIVE |
| 품질 | `quality_record` (구조 실패만 Gate) + `blog_quality.layers` (advisory) | 시험 발사에만 |
| 보관 | Ledger 행 + `workspace/blog/…/POST.md`·`PASTE.*` (R8 controlled write) | 시험 발사에만 |
| URL 기록 | `scripts/record_published_url.py` | **사용 0회** (published 패키지 0) |
| 성과 | `blog_rank` / `scripts/track_blog_rank.py` | **스냅샷 0행** |

**교차 결합 (실제로 있는 것만):** ① Runtime pipeline-worker가 Vault `content/naver`·`content/tistory`·`analytics/keywords`를 `:ro`로 마운트한다 (Runtime ← Vault 단방향 읽기). ② Vault `kw-pipeline.py`가 검색광고 자격증명을 `/root/thomas_agent/.env`에서 **이름으로** 읽는다(값 복사 없음) — `.env` 키 이름·권한 변경이 크론에 영향을 준다. ③ `tistory-engine-compare.py`가 비교용으로 `workspace/blog/tistory/`를 읽는다. 그 밖의 호출은 없다 **[확인]**.

---

## C. Readiness Vocabulary Map

| 명세의 상태 | Runtime 대응 | 외부(실생산) 대응 | 권한 |
|---|---|---|---|
| `quality_review_ready` | `quality.quality_state` = `ready_for_review` / `needs_edit` — `final["failures"]`(구조·critical 표준·제목 3개·structured output)만으로 결정 (`blog_content.py:892`). `layers`(semantic·evidence·platform_fit)는 advisory, 상태를 바꾸지 않음 | **저장 Gate**: preflight FAIL 0 (못 고치면 저장하지 않음) + variety WARN 해소. 별도 필드 없음 — "Vault에 draft로 저장됨" 자체가 통과 | 자동 |
| `affiliate_stock_ready` | 없음 | `blog-affiliate-place.py` 계산값 `배치 가능 / 보류(NEEDS_REVIEW: 사유) / 링크 미준비` — 파일에 저장하지 않음 | 자동 계산, 순서는 Thomas(`stock_order`) |
| `editorial_recommended` | 없음 (`selection_evidence`는 선정 근거 기록일 뿐) | 단일 필드 없음: Queue 순서 + 스킬 STEP 0 규칙 + V11 제휴 차례 + V16 canary. Slot C는 **기록만**(manual GOOD/BORDERLINE/BAD는 사람) | 자동 제안 + 크론 선택 |
| `publication_approved` | `publish_state` `draft`→`published`는 `record_published_url.py`(사람이 URL을 줄 때만) | Naver: Thomas 업로드. Tistory: Thomas가 시작한 예약. Vault `status`: draft → scheduled → published(RSS 승격, 강등 없음) | 사람 |

**어휘 충돌 주의:** Runtime `ready_for_review`, 제휴 `배치 가능(READY)`, V16 `CREATE_NEW`, Vault `scheduled`는 서로 다른 질문의 답이다. 어느 하나도 다른 것이나 발행을 함의하지 않는다는 명세 2.5와 현재 구현은 이미 맞다 — 섞는 코드가 없다 **[확인]**.

**PR 3.1의 전제 실측:** Ledger 패키지 57개 중 `layers`가 있는 것은 v0.3(Tistory) 8개뿐이다. 그 8개 중 `platform_fit=fail` 2건은 이미 `needs_edit`였고, `ready_for_review` 2건은 둘 다 `evidence=ok`였다. **"구조는 통과했지만 근거 부족·플랫폼 실패인데 ready_for_review"인 실제 사례는 0건**이다. v0.2 49건은 layers가 없어 판단할 수 없다 **[확인]**.

---

## D. Duplicate / Link / Editorial Ownership

| 기능 | Vault | Runtime | 연결 |
|---|---|---|---|
| 중복 판정 | V16 (`CREATE_NEW/UPDATE_EXISTING/REJECT` + confidence + lifecycle authority + variant relation), mode **canary**, `covered_by` 여전히 Gate | `blog_overlap` lexicon 5분류 → `block/rewrite_required/review/allow/operator_override`. Naver는 같은 키워드면 어느 플랫폼이든 BLOCK, 같은 플랫폼 `cannibalization_risk`도 BLOCK | **없음.** 두 엔진은 독립. Runtime 쪽은 "같은 주제+같은 의도 → BLOCK"이라 명세 2.8(같은 cluster·facet만으로 차단 금지)보다 엄격하다. 휴면이라 지금 영향 없음 |
| 내부 링크 | P1~P5 추천(검토용, 자동 삽입 없음) | Tistory `internal_links` 검사(advisory) | 없음 |
| 편집 우선순위 | Queue + STEP 0 + Slot C shadow(14회 평가 중) + temporal(랭킹 신호만) | 없음 | 없음 |
| 이미 쓴 키워드 제외 | 볼트 원고 전체(published·scheduled·draft) | Vault 마운트로 같은 원고를 읽어 제외(#1012/#1013) + 자기 Ledger | Runtime → Vault 단방향 |

**키워드 우연 일치:** Runtime 패키지 57개 중 8개의 target_keyword가 Vault 원고의 첫 키워드와 같다(예: `퍼플렉시티 무료` ↔ `naver/98편`·`tistory/90`, `캡컷 유료` ↔ `tistory/91`). 둘 다 같은 `queue.md`를 읽기 때문에 생긴 **우연 일치이며 동일 글이라는 증거가 아니다**(명세 2.9). Vault는 Runtime Ledger를 읽지 않으므로 반대 방향 중복 확인은 없다. Runtime 패키지가 발행되지 않는 한 문제는 없다.

---

## E. Data Identity Map

| 식별자 | 어디에 | 상태 |
|---|---|---|
| Vault Article ID | 파일 경로 `content/naver/NN편-slug.md`(제휴 배치본은 `제휴/NN편-…`, 같은 번호 체계), `content/tistory/NN-slug.md` + `episode` | 존재 |
| Published URL | Vault 프론트매터 `url:` — Naver 98편 중 97, Tistory 91편 중 91 | 존재 (강한 식별자: Naver logNo, Tistory `/N`) |
| Tistory ↔ Naver | Tistory `source: "[[NN편-…]]"` | 존재 |
| Runtime Package ID | `bcp_*` 57개 (Ledger) | **57/57 UNLINKED** — Vault 원고에 `bcp_` 언급 0, Runtime에 published_url 0 |
| Rank Snapshot | Vault `history/*.json` (키: 원고 파일 + 키워드, 실제 블로그 탭) / Runtime `blog_rank_snapshot` | Vault 존재 · Runtime 0행 |
| GSC (Tistory) | 주간 보고서·`inflow.tsv`에 사람이 옮김(도착 글 모르면 `?`) | 수동, 일부 UNKNOWN |
| Prompt lineage | Runtime `lineage.prompt` (패키지마다) / Vault: 스킬 판 이력은 `Prompt/운영/`(prompt_history.py), 원고별 prompt 판 필드는 **없음** — H판 같은 판 경계는 편 번호(CUTOVER)로 추적 | Vault 원고 ↔ 프롬프트 판: UNLINKED (번호로 간접) |

**결론:** 발행·성과의 강한 식별자(URL, 파일 경로)는 이미 Vault에 있고, Runtime Package ID는 실생산 글과 대응하는 것이 하나도 없다. 성과를 연결하는 중심 키는 Runtime `package_id`가 아니라 Vault 원고 경로 + URL이어야 한다. 가상의 package_id는 만들지 않는다.

---

## F. Frozen Operations / Approval Gates

| 관찰 | 현재 상태 (2026-10-08 확인) | 영향을 줄 수 있는 파일·크론 | 변경 전 필요 |
|---|---|---|---|
| Slot C 14회 shadow | valid_cron 1/14 (10-08), 10-07은 rehearsal. **제휴일 예외:** 제휴 배치일은 새 일반 원고가 1편이라 `SKIPPED: ambiguous_ab(new=1)` — 설계대로이며 14회에 세지 않음 | `kw_pipeline.py`(SLOTC_W 등), `blog-daily-draft.sh` 스냅샷 단계 | Thomas 승인 (명백한 운영 버그 제외) |
| Tistory Pattern 15 H판 | ② 두 줄 반영(skills `7ac7069`), 관찰 대상 92편부터 — 최신 91편이라 **0/5+5** | `naver-to-google-seo/assets/prompt.txt` | 5+5 측정 뒤 Thomas |
| Affiliate Phase 2 Canary | **PENDING** — 2026-10-09 08:02 KST(10-08 23:02Z) 실행 예정. 직전 제휴 93편, 94~98편 일반 → 99편이 제휴 차례로 예상(미검증). 현재 `다음 배치: 제휴-라벨-프린터기.md`, 반찬냉장고 보류 | `blog-affiliate-place.py`, `affiliate-clips.py`, skill `naver-blog-draft`, `affiliate_stock.json` | 결과 확인 전 READY·배치 크론 변경 금지 |
| V16 / covered_by | `DECISION_MODE = "canary"`, `covered_by` 유지 | `kw_pipeline.py`, `blog_taxonomy.py` | enforce 전환·covered_by 제거는 별도 승인 |
| 내부 링크 | 추천만 | `blog_taxonomy.py` | 발행본 자동 삽입 금지 |
| 일일 슬롯 | 일반 2자리, 제휴일엔 제휴 1편이 한 자리 대체 | 일일 프롬프트, skill 'E' | Thomas |
| 제휴 비율 | V11 = 5편 중 1편 **상한** (`blog-variety.py:358-368`) | `blog-variety.py` | Thomas (Affiliate Phase 3는 별개 승인) |
| Tistory 쿠팡 | AdSense 전 비활성 | skill | Thomas |
| Runtime 블로그 행 | 3행 모두 disabled. 10-31 Tistory 프로파일 존치 판정, 11-25(D8) `blog_*` 제거/부활 판정 | `schedules.jsonl` (`scheduler_cli`) | Thomas |

**이 감사가 지킨 것:** Vault·scripts·skills 쓰기 0건(외부 테스트 실행 전후 Vault `git status` 0→0), Runtime 상태 파일 쓰기 0건(호스트 체크아웃에서 pytest를 시도했으나 `state_guard`가 실행 전에 거부 → worktree에서 실행), 코드·스키마·크론·프롬프트·설정 변경 0건.

---

## G. PR Recommendation (PROPOSED / NOT_APPROVED)

모든 후속 PR을 가르는 조건은 하나다: **Runtime은 생산 콘텐츠 경로가 아니다.** Tistory 경로는 10-06에 크론으로 확정됐고, Naver 행은 꺼져 있으며, `blog_*`의 존폐는 D8(11-25)에서 정한다.

| PR | 권고 | 근거 | 다시 열 조건 |
|---|---|---|---|
| **3.1 Review Gate v2** | **DEFER** | 생산 글에 대한 효과 0. 실측 사례 0건(§C). 실생산의 검토 Gate는 preflight FAIL 0 + variety + 사람 업로드로 이미 분리돼 있음 | D8에서 Runtime 레인이 부활해 생산 역할을 맡을 때. 그때 근거는 layers가 있는 패키지로 다시 잰다 |
| **3.2 Editorial Priority** | **NOT_NEEDED (Runtime)** | Vault가 소유(Queue·V16 canary·Slot C·temporal·제휴 차례). Runtime에 만들면 명세 2.4가 금지한 중복 엔진 | 개선이 필요하면 Slot C 14회 평가가 끝난 뒤 **Vault 소유자 쪽 제안**으로 |
| **3.3 Outcome Foundation** | **DESIGN_ONLY** | 실제 성과 데이터(블로그 탭 순위, RSS 반응, GSC 수동값)와 강한 ID(URL)는 Vault에 있음. Runtime `blog_rank`(API Proxy)·`record_published_url`은 사용 0회. 실제 빈칸: ① 원고 ↔ 프롬프트 판 필드 없음(편 번호로 간접 추적), ② GSC 자동 수집 경로 없음, ③ 결측을 0이 아닌 빈칸·`?`로 두는 규칙은 이미 있음 | 설계 문서만: Vault 원고 경로+URL을 키로 한 outcome 행 정의, 원고별 prompt 판 기록 방안. **GSC API는 새 자격증명 → 별도 승인 항목** |
| **3.4 Read-only Status** | **DESIGN_ONLY** | Runtime만 읽는 보드는 "꺼짐·0건"만 보여 줌. 운영자에게 필요한 상태(크론 로그, Queue 날짜, draft 수, Slot C stats, 제휴 `--list`, 순위 이력)는 Vault 쪽 기존 명령의 출력 | 기존 명령 출력을 묶는 읽기 전용 요약의 설계만. 소유자는 Vault scripts. 알 수 없는 값은 UNKNOWN |
| **3.5 Platform Search Signal** | **DEFER** | Naver 신호(검색광고 수요, 실제 블로그 탭)와 Google 자리(`google_slots.tsv`, `slots`, `tistory-pick`)는 이미 Vault가 분리해 소유. 10-31 Tistory 판정이 Google 신호의 쓸모를 정함 | 10-31 판정 뒤 설계 문서 |

### Evidence Before Complexity (명세 12.5) — 3.1을 예로

1. 현재 문제: Runtime 패키지에서 관찰된 "근거 부족인데 ready"는 0건. 2. 실패 사례: 없음. 3. 기존 코드: 생산 쪽 preflight가 FAIL 0 저장 규칙으로 이미 막는다. 4. 새 기능이 푸는 문제: 생산에 없는 경로의 문제. 5. 측정: 불가(생산 표본 0). 6. 되돌림: 해당 없음. → DEFER.

---

## 발견한 문제·위험 (변경하지 않음, 보고만)

1. **문서와 실제 상태 불일치.** `docs/REMAINING_WORK.md` §J 첫 단락은 `schedule_876d53b39de29b2af417`을 "enabled"로 적는다. 실제로는 2026-09-30T17:40:31Z에 disabled됐고 한 번도 발사하지 않았다(`last_run_at: null`). disable 이벤트는 CLI 특성상 생성 사유를 그대로 싣고 있어 **끈 이유가 어디에도 기록돼 있지 않다**. EXPANSION_READINESS Q3(10-06)는 "3행 모두 꺼진 그대로"라고 맞게 적는다. → §J 정정은 이유를 아는 Thomas 확인 후 별도 문서 PR로 하는 것을 제안한다.
2. **Tistory 공개는 "예약 → 플랫폼 자동 공개"다.** 사람이 예약을 시작하지만 공개 순간은 자동이다. 자리표시가 그대로 나가지 않게 AF1(FAIL)이 막는다. 이번 Phase는 이 흐름을 바꾸지 않는다. 명세의 "Manual Publication"은 Tistory에서는 "운영자가 시작한 예약"으로 읽어야 한다.
3. **중복 엔진 두 개.** Runtime `blog_overlap`은 V16보다 엄격하게 차단한다(§D). 휴면이라 지금 영향은 없다. D8에서 부활을 택하면 V16 결과를 읽는 쪽으로 정리해야 한다(새 엔진이 아니다).
4. **자격증명 결합.** Vault 크론이 `/root/thomas_agent/.env`의 검색광고 키를 이름으로 읽는다. `.env` 키 이름이나 권한이 바뀌면 다음 날 08:02 큐 생성이 실패한다(크론은 7일 이내 큐면 계속 진행).
5. **Slot C 제휴일 SKIPPED.** 제휴 차례마다 14회 평가가 하루씩 밀린다. 알려진 구조적 예외이며, 이번 Phase에서 외부 슬롯 시스템을 고치지 않는다.
6. **크론의 scripts·skills 자동 커밋(`git add -A`).** 다른 세션의 미커밋 작업도 08:02 자동 커밋에 실린다(기존 운영 특성). 이 감사 시점의 미커밋은 0.

---

## 테스트 Baseline

| 범위 | 명령 | 결과 |
|---|---|---|
| Runtime 블로그 (14개 파일: `test_blog_content_package_schema`, `test_mvp_runtime_blog_{budget,content,draft_model,overlap,platform,queue_seeds,rank,selection,structured,tistory}`, `test_mvp_runtime_naver_research`, `test_record_published_url`, `test_score_blog_draft`) | worktree에서 `/root/thomas_agent/.venv/bin/python -m pytest -q -p no:cacheprovider --basetemp=<scratch> <파일들>` | **463 passed, 5 skipped** (skip 5 = `no local Core activation`, 기존 Core-gated). 명세의 참고값 365보다 많다 — 이후 테스트가 늘었다 |
| 외부 7종 (`blog-affiliate`, `blog-preflight`, `blog-taxonomy`, `blog-weekly`, `decision-gold`, `kw-decision`, `travel-preflight` `.test.py`) | `python3 ~/.claude/scripts/<t>.test.py` | **7/7 ALL PASS**, Vault 미커밋 0→0 |
| CI | 이 PR은 문서만 바꾼다 | 필수 5개 체크로 확인 |

Python 3.14(호스트) 결과이며 CI 3.12와 동등성은 PR 체크가 확인한다.

---

## 2026-10-09 Canary 확인 목록 (PENDING — 실행 후에만 판정)

| 확인 | 근거 |
|---|---|
| READY Gate가 라벨 프린터를 통과시키는가 | cron 로그의 배치 줄, `99편-라벨-프린터기.md` 생성 |
| 반찬냉장고가 건너뛰어지는가 | `제휴/제휴-업소용-반찬냉장고.md`가 그대로 남음 |
| 일반 원고가 1편만 생기는가 | 그날 새 일반 원고 수 = 1 (Slot C는 `SKIPPED: ambiguous_ab(new=1)` 예상) |
| V11 정상 | 배치 뒤 `blog-variety.py`에 V11 FAIL 없음 |
| AI 연결·다리가 자연스러운가 | 배치 원고의 제목·H2·AI 활용 절차, 다리 16편·35편을 사람이 읽음 |
| 생성과 발행 판단이 분리되는가 | 원고는 `status: draft`로만 저장, 업로드는 Thomas |

---

## STEP 10 요약

- **Current Architecture:** 생산 = Vault 크론 + skills + Vault 도구 + 사람 발행. Runtime 블로그 레인 = 배포됐지만 비활성(행 3개 off, 시험 패키지 57, 발행·순위 0).
- **Implemented Changes:** 없음. 이 문서 하나와 `STATUS.md` 재생성뿐이다.
- **Deferred Changes:** PR 3.1 · 3.5 DEFER, 3.2 NOT_NEEDED (Runtime), 3.3 · 3.4 DESIGN_ONLY.
- **Test Results:** Runtime 블로그 463 pass / 5 skip(기존), 외부 7/7 ALL PASS.
- **Remaining Risks:** 위 '발견한 문제·위험' 1~6.
- **Expected Benefits:** Runtime에 중복 엔진을 만들지 않는다. 실제 개선 대상(Vault 쪽 성과·프롬프트 판 연결)을 짚었다.
- **Next Recommended Phase:** ① 10-09 Canary 확인 → ② Thomas가 G표 결정 → ③ 승인되면 3.3 설계 문서(Vault 키 기준)부터.
- **Operator Approval Required (명시):**
  1. G표의 PR별 권고 수락/수정.
  2. 3.3을 설계로 진행할 때 설계 문서를 둘 곳 — Vault(성과 데이터 소유자) 또는 이 저장소 proposals.
  3. GSC 자동 수집(새 자격증명·네트워크 읽기)을 검토 대상으로 둘지 여부 — 둔다면 env-only gate의 별도 승인 항목이다.
  4. REMAINING_WORK §J의 "enabled" 서술 정정, 그리고 876 행을 끈 이유(아는 경우).
