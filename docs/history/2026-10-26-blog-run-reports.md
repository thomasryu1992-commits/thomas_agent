# 2026-10-26 — Blog department P1: the drafting cron reports each run, and the week counts the reports

**What.** BLOG_DEPARTMENT v0.2 P1, under Thomas 2026-10-26 "승인: BLOG-DEPT v0.2 §7 권고대로".
- **On the host (outside this repo), server scripts git `1995683`:** `blog-run-report.py` writes
  `ops/blog-runs/<KST date>.json` in the vault at the end of each `blog-daily-draft.sh` run. Each report records:
  - today's drafts, with path, platform, status, cluster, first keyword and slot;
  - a re-run of `blog-preflight` (draft mode) and `blog_variety` for each draft;
  - the step exit codes;
  - `instruction_id: null` until P6.

  The first report (2026-10-26, 5 drafts) is vault commit `7241016b`, so the folder exists and is owned by the
  `claude` account before any deploy mounts it.
- **Here:**
  - `blog_department.read_runs` counts the reports dated in the week. The record moves to
    `blog_department_week.v0.2`, which adds a closed `runs` block. v0.1 stays, so any v0.1 rows still validate.
  - The weekly report adds a "[원고 실행 보고]" line.
  - scheduler-maint mounts `ops/blog-runs` read-only.

**Why it is shaped this way.**
- **Drafting stays outsourced (v0.2 §3).** The system's only view of it until now was the front matter. The report
  is the outsourced side's half of the interface in §3-1. It is a file in the vault, read through the existing
  mount pattern, so the runtime gets no write path and no new door.
- **Counts only.** Keywords are counted (distinct), never carried onto the ledger, like titles in v0.1.
- **Absent is not zero.** An unmounted report folder makes `runs` null, and the line says the folder is not mounted.
  A report that does not parse as `blog_run_report.v1` is counted as unreadable and adds nothing else.
- **The folder must exist before `up -d`.** If it does not, Docker creates the bind source root-owned inside the
  vault, and the cron (uid 2101) can no longer write there. The first real report was committed for that reason.

**P1's switch condition (v0.2 §4):** seven days in a row with a report, and the reported draft count equal to the
front matter's new drafts. The weekly line shows both numbers next to each other.

**Verified.**
- The report script: 9 cases. All server-script tests: rc=0.
- The runtime rules: 5 mutations, each caught by the suite.
- On the real vault, observing up to 2026-10-27: 1 report, 5 drafts, 0 check failures, and a valid v0.2 record.
