# 2026-11-03 — Blog department: the weekly report carries each stage's progress towards its switch

**What.** Every v0.2 stage up to P7 is built. What is left (the P6 switch, P8) waits on §4's conditions: P4a's 14
days, P4b's 14 days after it is turned on, P5's two weeks, then Thomas's approval. Until now those conditions were
read off memory notes and one-off scripts. Thomas chose the recommended default, "조건 진행표": the weekly report
shows each stage against its condition, and the department's weekly record keeps the same numbers.

- **`blog_department.stages(ledger, now=…)`.** It reads each shadow's own records by kind name, as the module already
  did for `blog_verification` and `blog_analysis`, so the core still imports no lane module. For each record kind,
  the last record per key wins, so a re-run supersedes.
  - P4a `blog_kw_replay` and P4b `blog_kw_direct`, keyed by bundle:
    - this week's bundles counted as agreed / differed / stale generator / other;
    - the bundle days agreeing in a row, newest first.

    A day is good only when every bundle that day was compared and agreed. Any other state breaks the run, because
    §4 counts only after a regeneration. A day with no bundle neither counts nor breaks. The day is the bundle
    name's date, as root's cron wrote it.
  - P5 `blog_feedback`, keyed by history date: the days on record, plus this week's days with a moved rank, the
    moved ranks and the adjusted candidates.
  - P6 `blog_instruction` and `blog_instruction_check`, keyed by run date:
    - the mornings an instruction was built, and how many reused an older history;
    - the days checked, without an instruction, or late;
    - where the week's drafts sat: follow-up, queue, Tistory list, filtered from the history, outside, no keyword.

    `outside` is split by platform, because the switch must absorb or close those sources first (Tistory's 1-C).
    Keywords are counted, never carried.
  - P7 `blog_slot`: the week's Naver comparisons and Tistory rule checks. A v0.1 record counted Tistory drafts
    without checking them, so they count as not checked.
  - A stage with no record is `None`, never zero. P4b stays `None` while its opt-ins are off, because it then writes
    nothing.
- **The message.** Five lines go under 블로그, after P2 and P3: P4a, P4b, P5, P6 (two lines), P7 (two lines).
- **The record.** `blog_department_week.v0.3` adds a closed `stages` block. It holds P2 and P3 as the message reads
  them (counts only; the rule codes and the statistics stay in the message) beside the five shadows. Until v0.3 the
  record carried neither P2 nor P3. Every field is an integer, a date or null, so the fingerprint takes the body
  unchanged. v0.2 stays.

**Verified.**
- 4 new tests: none-not-zero, the run and its break, the latest-record rule, the record and the rendered lines.
- Mutation: making the run never break fails 2 of them.
- The in-image assert builds a v0.3 record from synthetic records and checks the lines.
- The live ledger is not readable as `claude`. After the deploy, a read-only script in the scheduler prints what the
  next report will carry.
