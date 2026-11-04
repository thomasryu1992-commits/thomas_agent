# 2026-11-04 — Blog department: every ledger read includes the archive

**What happened.** The 11-04 13:02Z P4a fire replayed `20261030-071008` a second time, although that bundle already
had a record at 02:40Z. A probe on the live ledger showed the same for 10-28, 10-29 and 10-30: each has two
`blog_kw_replay` rows, one of them in the archive only. `LedgerStore.iter_records` reads the active file only, and
`retention` trims that file to the newest 2000 rows. Once a record rotated out, the fire's done-set no longer
contained it, so the fire treated the bundle as new. The store's own docstring says time windows need
`iter_records_with_archive`.

Every reader in the department used the active file:
- the done-sets of `blog_kw_replay`, `blog_kw_direct`, `blog_slot` and `blog_verify`;
- the lookups in `blog_analyze` (latest per week), `blog_feedback` (latest analysis week, last inputs) and
  `blog_instruction` (latest feedback, records per run date);
- the weekly report's `verification_summary`, `analysis_summary` (P3's weeks in a row) and `stages` (#1356).

**What it would have cost.**
- An agreeing bundle replayed again after a regeneration records `generator_stale`. Since the last record per key
  wins, an agreeing day would have turned into a break.
- The weekly report's day counts, first dates and runs shrank to the active file's few days, so P4a's 14 days could
  never have shown.

**Fix.** `blog_department.records(ledger, kind)` reads one kind with archives included, bounded by
`appended_since=LEDGER_SINCE` (2026-10-26, the department's first record), so no older archive is opened. Every
reader above goes through it. The lane already imported the department module function-locally for the vault root,
so this adds no new edge.

**Test.** `test_a_record_rotated_into_the_archive_still_counts_as_done_and_on_the_weekly_line` rotates a real
`LedgerStore` with `retention.rotate_file`. The next fire then replays nothing, and `stages` still counts both
days. Reverting the helper to `iter_records` fails it at the second fire.

**Left as is.**
- The duplicate rows already on the live ledger (10-28/29/30, all `generator_stale` twice) change no state.
- Archives are kept forever, so the read grows by one archive a day from 2026-10-26. That is cheap at the
  department's scale (the archive index skips files with none of the kind). If it ever matters, bound
  `LEDGER_SINCE` by the oldest item each reader can still act on.
