# 2026-11-03 — Blog department P7: Tistory slots checked against the queue's rules

**What.** The P7 slot shadow (`blog_slot`, #1350) compared Naver slots and only counted Tistory drafts, because
`tools/tistory_queue.py` cannot be re-planned from vault state. It reads the clock, re-checks the whole corpus with
`blog_variety` in subprocesses, and reads image files and the Mac-maintained `browser_check`. On 2026-11-03 Thomas
chose the recommended default, "규칙 검사": check each reported Tistory slot against the queue's rules instead of
re-planning, and add no recording step.

- **`scripts/blog_vendor.py` generates `blog/tistory_queue.py` by keeping, not cutting.** The vault file is a script
  whose planning runs at import, so removing the parts the runtime may not run would leave almost nothing to
  remove from. The generator keeps only the named top-level constants and functions (`KEEP`: `KST`, `MARGIN_MIN`,
  `SLOTS`, `NEW_SINCE`, `NEW_PER_DAY`, `ONLY_PER_DAY`, `fm`, `kind`, `key`, `jitter`, `is_new`), each byte-for-byte,
  plus the import statements made only of standard modules in `KEEP_IMPORTS`. A kept name missing from the source
  stops the generator. `--check` covers the file like the others.
- **`blog/slot.py` checks each reported Tistory draft.** Five checks, recorded by name:
  - `minute`: the reported minute is `jitter(slot, file name)` for the slot it folds to.
  - `unique`: no other post's current date takes that slot. A date-only date takes the whole day, as `taken` does.
  - `only_cap` / `new_cap`: the day holds at most `ONLY_PER_DAY` Tistory-only posts and `NEW_PER_DAY` new ones,
    counting this draft at its reported slot. Null when the cap does not apply (a conversion, an old post).
  - `after_run`: the slot lies at least `MARGIN_MIN` minutes after the report's `started_at`. The queue's cutoff
    was the plan time plus `MARGIN_MIN`, and the plan time is after the start. So this is a necessary condition,
    not the queue's exact cutoff. Null when the report has no start.
  - Not checked: the same-day 1-C conflict, `variety` and `browser_check`.
- **"Current date" of the other posts:** `published` for a published post, otherwise `scheduled` for a scheduled
  post or a draft that carries one. Holds and undated drafts are left out, as the queue leaves them out.
- **Paths.** A Tistory path from a report must sit in `content/tistory/` or one folder down, where the queue globs.
  Anything else is `outside`, unopened. The record names a post by its file number.
- **Schema `blog_slot.v0.2`.** It adds `tistory` (rows) and six `tistory_*` summary counts, and drops
  `tistory_not_compared`. v0.1 stays for the records written before this ships (#1350 can deploy first).

**Verified.**
- **On the real vault**, through the lane's fire with the lookback widened to 30 days: 9 reports (10-26 to 11-03),
  25 Tistory drafts. 23 were checked and all 23 pass every check, each of the five evaluated (none null). The 2 on
  10-26 are `no_slot`, because that report gave a date without a time. The Naver side was unchanged: 27 compared,
  27 agree.
- **Mutation:** counting the draft itself among the other posts fails 3 tests.

**Why not a recording step.** Re-planning needs the corpus and the images as they were at the Mac's reservation
time, which means a bundle carrying the whole content tree and a change to the Mac routine. Most of what it would
measure is the corpus moving between the draft and the reservation. The rules are what the instruction will have
to obey after P6, and they are a function of state the vault keeps.
