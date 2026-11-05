# 2026-11-05 — Blog department: the outsourced-side watch (P6 prerequisite)

**What.** #1378 made "the system knows the days the outsourced side stopped" a condition of the P6 switch. After the
switch, the drafting cron writes nothing without an instruction, and §3 says the system reports that day as a
failure. The host script built for this, `mac-routine-watch.py` (2026-10-16), was never scheduled: no cron, no log.
It also judges from git history (`origin/main` commit times), host logs (`blog-daily-draft.log`, `kw-weekly.log`,
`hermes-notify.log`) and a `git fetch`, and the container has none of these.

Thomas chose two things (2026-11-05):
- "기록만, 알림은 P6에서": record only; the notice is turned on at the P6 switch, as with the other shadows.
- "ops/naver-draft 마운트": mount the Mac draft-saving routine's folder read-only.

**Change.**
- **`blog/watch_rules.py`.** Generated from the server scripts' `mac-routine-watch.py` by keeping the pure rules only:
  - the run-block parser, which skips retries;
  - the routine table and the 36-hour limit;
  - the drafting cron's 12-hour deadline and the weekly Monday 21:00 KST deadline;
  - the Sunday arithmetic;
  - the empty-template line of the weekly interpretation.
- **`blog/watch.py`** (kind `blog_watch`, record `blog_watch` v0.1). It applies those rules to the vault mounted
  read-only. The five checks are:

  | Check | Problem when | Reasons |
  |---|---|---|
  | `RUN_REPORT` | the report of the last day past 12:00 KST is missing, unreadable, has `steps.draft` ≠ 0, or is skipped | `missing`, `unreadable`, `draft_failed`, `skipped` |
  | `MAC_DRAFT_STALE`, `MAC_TREND_STALE` | the last `exit 0` run block is more than 36 h old | `stale`, plus `failed_since` or `cut_since` for what came after |
  | `TREND_SEEDS_EMPTY` | `seeds_trend.txt` has no candidate line | `empty`. While the trend routine is not ok this is `unknown`, so one cause is reported once |
  | `WEEKLY_MISSING` | the statistics note or the snapshot is missing, or the snapshot's "## 해석과 결정" holds only the template | `stats_missing`, `snapshot_missing`, `unfilled` |

  A folder that is not mounted makes a check `unknown`, never ok. A fire records when any state changes, and once a
  KST day as a heartbeat. It notifies nobody.
- **Compose.** scheduler-maint mounts `ops/naver-draft` read-only. The folder also holds the Naver reservation state
  and audits; the watch reads `daily.log` only.
- **`blog_department_week` v0.6.** `stages.watch` holds the recorded days, the latest problems, and the week's
  problem days per check. The weekly report adds a line "[외주 감시 P6 전제]".

**What differs from the script, and why.**
- **The age is measured differently.** The script measures a routine's age from the commit in which its last good
  block first reached the server. Without git history, this reads the block's own header as KST. The Mac writes
  its local time, which has been KST or UTC+7, so the age can be up to two hours too old and never too young. On a
  36-hour limit, that only makes a warning come early.
- **Host plumbing stays on the host.** These checks are not the outsourced work's output, and the container cannot
  see them:
  - vault fetch failures and unpushed server commits;
  - the Hermes send log;
  - the weekly keyword queue's host log.

  The script stays as it is, unscheduled. P8 decides its fate.

**Verified on the real vault (2026-11-05 03:44Z).** All five checks are ok:
- the 11-05 report is clean;
- both routines last succeeded at 11-04 08:04 KST;
- the seeds file has candidate lines;
- the week of 11-01 has its note and a filled snapshot.

**After deploy.** Thomas registers the schedule; it fires every 30 minutes. It is not added automatically.
