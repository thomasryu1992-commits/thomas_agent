# Build history — one file per delivered increment

**Since 2026-09-25, a delivered increment's history entry is its own file here**, not an insert at
the top of `docs/BUILD_HISTORY.md`. That file is the archive of everything delivered before this date
and takes no new entries.

Why: every PR inserted at the same place — the top of `BUILD_HISTORY.md`'s *Delivered* list. Measured
over the 49 merges of 2026-09-22..25, 23 added to that same hunk, so any two PRs in flight together
conflicted on it at `update-branch`, under a `strict` branch rule that makes every PR update at least
once (system review D6, `docs/proposals/SYSTEM_REVIEW_IMPROVEMENT_PLAN_V0.1.md`). Two new files never
conflict.

## The rule

- **One file per PR that delivers something**, added in that PR:
  `docs/history/YYYY-MM-DD-<slug>.md` — the merge date (or the day you write it), then a short
  lowercase-hyphenated slug. `tests/test_build_history_fragments.py` checks the name and the heading.
- **Start with a `# ` heading** that says what was delivered, in the same voice the archive uses.
  Then the entry itself: what changed, why it is shaped that way, what it deliberately does not do,
  and the numbers it rests on. The archive's entries are the model.
- **Newest first is the file listing reversed:** `ls -r docs/history/`. Nothing aggregates these
  into one file — an aggregate would be the shared hunk again.
- Correcting an entry later edits that entry's file. Nothing in `BUILD_HISTORY.md` is rewritten.
