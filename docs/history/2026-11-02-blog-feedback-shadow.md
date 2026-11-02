# 2026-11-02 — Blog department P5: a keyword-priority guideline from the statistics, applied in shadow

**What.** BLOG_DEPARTMENT v0.2 P5, under Thomas 2026-10-26 "승인: BLOG-DEPT v0.2 §7 권고대로" (R3: P4–P5 shadows
start together; L3: the loop may change keyword priority on its own, nothing else). Thomas asked on 2026-11-02 to
start P5 directly.
- **`runtime/mvp_runtime/blog/feedback.py`** judges each Naver cluster on the latest recorded statistics week
  (P3's `blog_analysis`) and sets a bounded factor on the score of that cluster's candidates. The candidates come
  from the latest `analytics/keywords/history/<date>.json`, the file the production keyword run wrote.
- **Scheduler kind `blog_feedback`** (maintenance, daily) records `blog_feedback`, in `mode: shadow`, when what it
  read changed. The record holds the following:
  - the guideline: per cluster the post count, measured / below-table / unknown counts, missing rate, median,
    ratio to all posts, verdict and factor;
  - every candidate's score and lane rank before and after;
  - the top `TOP_PER_LANE` per lane before and after, and which keywords entered and left it.
- **Nothing reads the record.** P6 is where a guideline reaches the instruction (`blog_instruction`). P5's switch
  condition is two weeks of these records (v0.2 §4).

**The rule.** These are #1183's evidence rules (§6–§7, Thomas 2026-10-08), applied to views per post:
- **Which posts count.** A post counts once D+14 has passed by the week's Sunday. A cluster is judged only with at
  least 5 posts and at most 30% unknown, against all Naver posts of the same week. Otherwise it is
  `INSUFFICIENT_EVIDENCE` with factor 1.
- **The factor.** A cluster median at or above 2× the week's median gives 1.2. At or below 0.5×, or below the
  table, it gives 0.8. In between it is `NO_DIFFERENCE` with factor 1. Verdicts only ever propose; nothing is
  enforced.
- **No rank is read.** The history file's `ranks` table is never read. #1183 marks those ranks UNVERIFIED (3 of 83
  belonged to another post of the blog), and a test pins that removing the table changes nothing.

**Why it is shaped this way.**
- **Below the table is its own state.** The collector's per-post table (`글별`) is the week's top 100 by views,
  while there are 137 published posts.
  - A post the table left out is neither 0 (which §6 forbids) nor unknown. Calling it unknown would put most
    clusters over the 30% ceiling.
  - Its views are at most the table's lowest, so the median sorts it to the bottom and is either a measured number
    or `below_table`. It is never a number made up for the left-out posts.
- **When a missing post is unknown.** The collector matches a row to a post by the front matter `url:` logNo
  (`naver_stats_weekly.py`). A post with a `url:` that is absent was therefore really below the table. Only a post
  without one, in a week whose table has rows the collector could not match, is unknown. On 11-01 there were four
  such rows, all posts published that week.
- **P3's record, not the files.** The views come from the ledger record P3 already validated (reuse first). The
  record dropped the publish date, so the D+14 cutoff reads `published:` from the front matter instead of widening
  P3.
- **The production history, not the replay.** P5 stays independent of P4a's deploy state, and the shadow re-ranks
  what the morning's drafting actually saw.
- **The cluster is the unit.** Clusters are the registry's unit (`clusters.tsv`), carried both on the posts' front
  matter and on each candidate's `decision.cluster`.
  - Lanes are too coarse to steer: four lanes, each with dozens of posts.
  - Query classes (P3's capture medians) do not map onto candidates without the Search Ad volumes.
  - A candidate whose cluster is `unresolved` (46 of 109 on 11-02) keeps its score, and the record counts it.

**Verified before the PR.**
- **On the real vault**, with the 2026-11-01 week through P3's own reader and the 11-02 history:
  - 101 posts were past D+14: 63 measured, 38 below the table, 0 unknown. The week's median is 7.
  - Of 33 clusters, 5 cleared the floor. `ops-wage` and `ops-evidence` have medians below the table and get 0.8.
    The other three show no difference (`ops-food-duty` at 1.86× falls short of 2×).
  - Four candidates were adjusted, and 16 lane ranks moved. One keyword entered a lane's top ten and one left it.
  - The record (about 28 KB) validates against its closed schema.
- **Tests** (`tests/test_mvp_runtime_blog_feedback.py`) cover:
  - each verdict and its reason, the D+14 cutoff and drafts;
  - the below-table/unknown split, with and without unmatched rows;
  - the scores and ranks that move, and that unresolved clusters, clusters with no guideline and Tistory stay put;
  - that the rank table is unread, and that no title or URL appears in the record;
  - re-recording only on change, and tomorrow's file going unread;
  - the named empty states and the refusal of an absent vault;
  - the scheduler path.
- **Mutation testing.** Twelve mutations of the module were all caught. Two were missed at first (the
  below-table-lowest sort and the upper-middle median), and a direct median test was added for them.
- **The full local suite** (Python 3.14): 10,993 passed and 307 skipped. 47 socket tests failed on `AF_UNIX path too
  long` under a long `--basetemp`; those six files pass (233) with a short one.

**Deploy.** No compose change: scheduler-maint already mounts `content/naver` and `analytics/` read-only. Deploy, then
add the schedule:
`docker exec thomas-scheduler python -m runtime.mvp_runtime.scheduler_cli add --kind blog_feedback --interval-seconds 86400 --reason "BLOG_DEPARTMENT v0.2 P5"`.
The first fire records the latest analysed week against that morning's history file.
