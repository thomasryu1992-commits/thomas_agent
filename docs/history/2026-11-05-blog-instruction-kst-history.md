# 2026-11-05 — Blog department: the instruction reads the KST day's keyword history

**What.** The P6 instruction shadow (`blog/instruction.py`, built 2026-11-03) looked up the morning's keyword history
by the UTC date. The production keyword run names its file by its own KST clock (`kw_pipeline.py`:
`datetime.now(KST)`), so the file the 07:10 KST run writes is the KST day's. The 11-05 file was committed at
2026-11-04T22:13Z, and the 10-30 file at 2026-10-29T22:37Z. Inside the morning gap (22:10–23:02 UTC), the UTC date is
the day before, so the build took yesterday's history:

- **Today's file and yesterday's file both present:** the first fire at 07:10 KST built from yesterday's history and
  marked it `history_today: true`.
- **Yesterday's file missing:** the build waited until `LATE_FROM` and took an older file, although the day's own file
  had already been written.

**Found by.** The P6 `outside` packet Thomas asked for (2026-11-05, "P6 outside 결정 자료"). Each report from 10-29 to
11-05 was replayed against the vault commit just before that morning's drafting cron started, using the generated
port with no transport, as `fire()` does:

| History looked up by | Naver drafts in the instruction | Tistory drafts in the list |
|---|---|---|
| UTC date (the shipped code) | 17 of 23 | 10 of 24 |
| KST date (this fix) | 23 of 23 | 10 of 24 |

The six Naver drafts that moved (181, 182, 188, 189, 191, 197) were all in that morning's `queue.md`, which is built
from the KST day's history. The Tistory list does not read the history, so its count did not move.

**Change.**
- `fire()` looks the history up by the KST run date, and `history_today` compares against it.
- Documentation: the module docstring and `history_for`'s argument now say KST.
- Tests: the fixture's history is re-dated to its KST day. A new case puts yesterday's file next to today's and
  asserts that today's is read; the shipped code fails it.

**What the live records already hold.** `fire()` never rebuilds a day once the drafting cron has started, so
instructions built before this deploy stand as recorded. Claude cannot read the live ledger, so the following is what
the shipped code does on that day's vault: the 11-04 instruction is built at 07:50 KST from the 11-02 history, although
the 11-04 file was committed at 07:38, and its check counts 197편 as `outside`. The weekly
record of 2026-11-09 (`blog_department_week` v0.6) includes those days in the instruction stage. Read the stage's
`outside` counts for the days before this deploy with that in mind. The deploy date is the boundary, and no recompute is
built.

**Also corrected.** The 2026-11-03 retrospective in the proposal's §4 P6 row (Naver 10 of 15, Tistory 0 of 15) came from
the same UTC lookup, and from Tistory rejects read at that time instead of on each day. The replay above replaces it.

**The packet.** With the fix, `outside` is Tistory only. Of its 14 drafts, 13 came from 1-C discovery, and one was a list
pick (#4, '네이버 스마트 플레이스 예약 수수료') written down as '네이버 예약 수수료'. The proposal's §7 O1 puts the choice to
Thomas:
- (a) the recommended default: name discovery as a permitted channel, with each draft's origin in the report;
- (b) close discovery;
- (c) bring discovery inside, which is not recommended.

Closing discovery conflicts with the 2026-10-27 decision to keep discovering until three Tistory posts pass. The list
alone gave 1.25 a day in this window.

**Checked.**
- `blog/feedback.py` already reads the history by the KST date (`now + 9h`).
- `blog/watch.py` dates everything in KST.
- Neither changes.
