# 2026-11-03 — Blog department P6, built ahead: the instruction shadow

**What.** After P7 no stage could start: P6 waits on P4a's and P4b's 14 days and P5's two weeks, and P8 on a switch.
Thomas chose the recommended default, "P6 지시 그림자 미리": build the instruction now, record it every morning and
leave it unread, the way P4b was built ahead.

- **`blog/instruction.py`, scheduler kind `blog_instruction`.** Two records, both shadow:
  - `blog_instruction` (one per KST day): what the drafting cron chooses from that morning, each list in the order
    the cron reads it.
    - The weekly review's open follow-ups (`next_topics.tsv`, state 열림).
    - Per lane, the production history's `create_new` candidates no vault post covers yet, ranked after the latest
      `blog_feedback` guideline, with the unadjusted rank and the days to a season peak.
    - The cron's own Tistory list (`tistory-pick --rank`, the port's `tistory_rank`).
  - `blog_instruction_check` (one per run report): each reported draft's source.
    - `next_topics`, `queue` (lane, rank) or `tistory_list` (rank) when it was in the instruction.
    - `history_filtered` when the history had it and the instruction left it out.
    - `outside` when neither had it, or `no_keyword`.
    - Plus `timing`: was the instruction recorded before the run started.
    - A report on or after the first instruction's day with none is `no_instruction`.
- **The moment.**
  - kw-daily starts at 22:10 UTC and names its history by the UTC date; the drafting cron starts at 23:02 UTC.
  - The instruction is built at the first fire in that gap once the day's history exists.
  - If none appears by 07:50 KST (the run reused its queue), it is built from the newest older history, marked
    `history_today: false`.
  - Nothing is built after 08:02 KST, when the vault already holds the cron's own drafts.
  - The kind fires every 600 s so that one fire lands in the gap. Outside it, a fire with no report to check returns
    `blog_instruction_nothing_new` and reads nothing.
- **No URL.** The port runs with no transport. Its RSS read fails and it falls back to the vault's status, as on any
  failed read. A test refuses every socket connect.
- **No slots.** P7 already checks each reported slot against the rule. A Tistory slot depends on a file name the
  writer has not chosen yet.
- **No door read.** §3-1 names `blog_instruction` as a `claude-read-bridge` read. Registering it now would widen
  Claude's door with no reader. Opening it is P6's act.

**Verified.**
- **On the real vault (Claude's clone, 2026-11-03):**
  - The fire built the 11-04 instruction in 21 s: 1 follow-up, 99 queue entries, 321 Tistory rows, 16 KB.
  - Against six past reports, rebuilt with each run's own drafts removed and the history before its start:
    - 11-03: 5 of 6 drafts in the instruction.
    - 10-29 to 11-02: 10 of 15 Naver drafts in the queue, at lane ranks 2–12.
    - 10-29 to 11-02: none of 15 Tistory drafts in the list.
  - The Tistory list for past days is rebuilt from today's reject log and candidate file, so it may differ from the
    list the cron saw. The live shadow does not have that gap.
- **Tests:** `tests/test_mvp_runtime_blog_instruction.py` (10).

**What this shows for P6.** After P6 the cron writes only what the instruction holds. The `outside` picks are the
sources P6 must absorb or close. The largest is Tistory's 1-C discovery (new questions from official help pages, a
network read). The check records give that decision its numbers.
