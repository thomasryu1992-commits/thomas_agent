# 2026-10-27 — Blog department P3: the system records the week's statistics the Mac collected

**What.** BLOG_DEPARTMENT v0.2 P3, under Thomas 2026-10-26 "승인: BLOG-DEPT v0.2 §7 권고대로" (R3: P1–P5 start
after the P0 deploy). Thomas asked on 2026-10-27 for design → verification → implementation.
- **`runtime/mvp_runtime/blog/analysis.py`** reads the following:
  - `analytics/naver-weekly/<sunday>.json`, its `summary` only: views, visitors, inflow, search inflow, the official
    search share, the home feed, views per post and the top queries;
  - `analytics/weekly-browser/<sunday>.json`: Tistory views and sources, Search Console totals, the per-page table
    and the index count;
  - the week's rows of `analytics/keywords/capture.tsv`: the capture rate, summarised per query class.
- **The comparison.** The same record holds each number the weekly review wrote in that week's
  `analytics/README.md` row beside the system's own, and whether they agree.
- **Scheduler kind `blog_analyze`** (maintenance, daily) records one `blog_analysis` per week whose Sunday falls in
  the last 14 KST days. It records that week again only when what it read changed.
- **The weekly report** adds a "[주간 통계]" line: the latest recorded week, the capture medians, the agreement with
  the review, and how many recorded weeks in a row agreed. That count is P3's switch condition (two).
- **scheduler-maint** now mounts `analytics/` whole, read-only, in place of `analytics/keywords` (still reached at the
  same path).

**Why it is shaped this way.**
- **Collection stays outsourced.** The collectors log in as the owner (Creator Advisor, the Tistory admin, Search
  Console). The capture rate needs the Search Ad API's volumes, which is a network read with a key. The system
  reads the files those runs leave and nothing else, so P3 opens no network read (v0.2 §4: "외부 조회 없음").
- **Daily, not inside the weekly report's fire.** The Mac writes on Monday between about 10:00 and 12:40 KST. The
  browser run is sometimes redone at noon after a lapsed login (10-26), and the clone the container reads syncs
  through git with lag. A daily fire with a look-back catches each file whenever it lands.
- **Re-record on change, keyed on what was read.** `sources_id` is a fingerprint over the parsed numbers, not the file
  bytes. A late browser file or a newly written README row yields a new record; rewording the decision column does
  not. Readers take the latest record per week. The kernel's fingerprint refuses floats (shares, medians), so it is
  taken over the body's canonical JSON text.
- **The comparison lives in the record.** Claude has no docker, so a switch condition checkable only at Thomas's
  terminal would go unchecked. P2 set the precedent of comparing in-record. A number either side lacks is `None`
  and never counts as a disagreement. A cell marked `미확인` gives no number, even when it quotes one.
- **The whole `analytics/` folder.** The README row and the weekly notes sit at its top. A single-file bind keeps
  the inode a git rewrite replaces, the same reason the keyword folder was mounted whole in P2. Nothing in the
  folder is a credential, and the revenue lines were already inside `naver-weekly/`.
- **No titles, no URLs in the record.** A Naver post is its 편 number and a Tistory post its page number. The
  collector's `raw` block (API URLs) is never read.

**Verified before the PR.**
- **Real vault, three weeks:**
  - 10-25: 11 of 11 numbers agree with the README row (2,063 / 1,758 / 89.2% / 6.0% / 181 / 109 / 86 / 22 / 726 /
    183 / 18).
  - 10-18: 4 of 4. The browser run never happened, so the other seven are absent on both sides.
  - 10-11: 2 of 3. The search share is 82.8 against 83.9, a row written before the 10-12 search-inflow formula. It is
    recorded as a difference, not smoothed over.
- **Capture medians** for 10-11 and 10-18 equal the ones the review pasted into those weeks' notes
  (4.8 / 3.1 / 54%, 3.2 / 3.6 / —, unsized 1·5 and 2·14).
- **Mutation testing.** Nine mutations of the module and the summary were all caught by the tests. Three were missed
  at first (the `미확인` rule, the streak's agreement test, the upper-middle median), and tests for them were added.

**Deploy.** It needs a deploy (compose mount change), then a schedule, best added after ~13:00 KST so a Monday's files
are there the same day (the look-back covers it either way):
`docker exec thomas-scheduler python -m runtime.mvp_runtime.scheduler_cli add --kind blog_analyze --interval-seconds 86400 --reason "BLOG_DEPARTMENT v0.2 P3"`.
Before `up -d`, check that `"$THOMAS_BLOG_VAULT_DIR"/analytics/naver-weekly/2026-10-25.json` exists on the host.
