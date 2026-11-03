# 2026-11-03 — Blog department P7: the Naver slot rule in the lane, as a shadow

**What.** BLOG_DEPARTMENT v0.2 P7 starts ahead of P6, as a shadow. P6, the switch, waits on P4a and P4b (14 days
each, in sequence), on P5 (two weeks), and on the frozen prompt lines. On 2026-11-03 Thomas chose to build the P7
slot shadow now, and to compare each reported draft once (the recommended default).

- **`scripts/blog_vendor.py` generates `blog/naver_slot.py` from the vault's `tools/naver_slot.py`**, the same way it
  cuts the P2 checkers. Every other line is the vault's, unchanged. What is cut:
  - The path comes from `configure(vault_root)`.
  - The writer (`apply_fix`) and the CLI go.
  - `reserved` and `plan_fix` go too. They read `ops/naver-draft/saved.json`, which is not mounted, so a port that
    kept them would answer as if nothing were reserved on Naver.
- **`blog/slot.py` and the maintenance kind `blog_slot`.** Once a day, for each run report of the last three KST days
  that has no record yet, every Naver draft's slot is recomputed and compared with the `scheduled` the report
  recorded. Each report gets one record (closed schema `blog_slot.v0.1`), and a draft appears in it by its 편 number
  only.

**The moment is rebuilt, not recorded.**
- **What the rule reads.** `next_slot` reads only the other Naver drafts' `status` and `scheduled`, plus the draft's
  own file name. The drafts written after this one carry higher 편 numbers. The day the rule looks at is the latest
  scheduled day, which lies about two weeks ahead, so publications in between do not touch it. The moment is
  therefore the vault's drafts now, minus every draft numbered above the one compared.
- **What is actually compared.** The drafting shell runs `naver_slot.py fix` before `blog-run-report.py`, so the
  reported value already has the rule's minutes. The comparison measures the writer's choice of day and window.
- **What the record keeps.** Besides the reported value it keeps the file's `scheduled` now (`moved`, for a slot moved
  by hand after the report) and what the rule decided from (`tail`: the latest day, its windows, the drafts counted).
  A difference can be explained from the record alone.

**Why a new kind, not a column on `blog_verify`.** `blog_verify` already walks the same reports every day. P2 counts
two weeks of equal verdicts, though, and a slot difference is not a checker difference. Folding it in would change
what P2's count means.

**Tistory is counted, not compared.** `tools/tistory_queue.py` cannot be rebuilt from the vault's state:
- it reads the clock (a 30-minute cutoff);
- it re-checks the whole corpus with `blog_variety` in subprocesses and re-runs itself with exclusions;
- it reads image files and the Mac-maintained `browser_check`.

The reports show the consequence. The 11-02 run's 163–165 landed in evening gaps on 11-05, 11-06 and 11-07, while the
11-01 run's drafts went to 11-08 and 11-09: the plan reshuffles as holds change. A recomputation hours later would
mostly measure that drift. It is also script code at module level, so the generator could only rewrite it, not cut it
(R2). It needs a recording step first, as P4a did.

**Found for P7 proper.** `pick()` hashes the file name. For the instruction to carry a slot before the draft exists,
the file name (편 number and slug) has to be decided in the instruction too.

**Verified before the PR.**
- **The real vault, through the lane's own fire** (temporary ledger, look-back widened to all reports): 9 reports
  (2026-10-26 to 11-03), 27 Naver drafts, 27 compared, 27 agree, 0 moved. Records average about 1 KB, with no path or
  title.
- **Tests** (`tests/test_mvp_runtime_blog_slot.py`):
  - the port opens no URL, writes no file, and has no reservation reader;
  - a run's three drafts each agree with the slot they were given in turn;
  - a day chosen by hand differs, and the record shows what the rule saw;
  - a moved slot is marked;
  - a report path cannot leave `content/naver`;
  - one record per report, once, with no path;
  - the scheduler path.
- **Mutation check:** with the "numbered above" filter removed, three of those tests fail.

**Turning it on (Thomas, after the deploy).**

```
docker exec thomas-scheduler python -m runtime.mvp_runtime.scheduler_cli add --kind blog_slot --interval-seconds 86400 --reason "BLOG_DEPARTMENT v0.2 P7 shadow"
```

A vault change to `tools/naver_slot.py` makes `blog_vendor.py --check` report it stale. Regenerate in a PR, as for the
P2 checkers. No bundle check ties this port to a vault commit, so a deploy needs no timing.
