# 2026-11-05 — Blog department: P4a/P4b's "14 days" is a calendar span

**What happened.** The weekly report's P4a line counted bundle days in a row against the condition "14일" (#1356).
Root's keyword cron runs `kw-pipeline run --max-age 2`. It reuses `queue.md` while the file is younger than two
days, and only a full run leaves a bundle. The live bundles were 10-28, 10-29, 10-30, 10-31, 11-02 and 11-04. Counted
as bundle days, the condition would have needed about four weeks of calendar. Counted as §4 words it ("14일 동안 받은
묶음마다 compared이고 모두 같다"), it needs two. Thomas chose the calendar reading (2026-11-05, "달력 기간으로").

**Change.** `blog_department_week` v0.4 replaces `days_agreeing_in_a_row` in `stages.kw_replay` and
`stages.kw_direct` with the newest agreeing run:
- `run_first` and `run_last` are its first and last bundle days, or null when the newest bundle day did not agree;
- `run_days` is the calendar span, with both ends included;
- `run_bundle_days` is how many bundle days the span holds.

The report reads "연속 일치 3일(11-02부터, 묶음 2일, 조건 14일)".

**The gap rule.** A day without a bundle neither counts nor breaks the run, but only up to `BUNDLE_GAP_DAYS = 3`. A
full run stamps `queue.md` when it ends, about half an hour after the 07:10 KST start. So two mornings later the file
is still a little under two days old and may be reused, and a healthy cron leaves a bundle at least every third day.
A longer gap is a run that never came and was never checked, so the run restarts after it. The recommendation put to
Thomas said "more than 2 days". Reading the reuse check (`os.path.getmtime(queue.md)`) showed that a 3-day gap is
still a healthy cron, so the bound is 3.

**Why a new schema version.** No v0.3 record exists yet: the first weekly fire after #1356's deploy is 2026-11-09.
The field still changes meaning in a closed schema, and v0.3 stays valid for anyone who wrote one by hand.

**Left as is.**
- The P3 weekly run (`weeks_agreeing_in_a_row`) already counts calendar weeks.
- P5's "기록 N일" counts history days with a record, and the feedback fire runs every day, so it needs no span.
