# Build history moves to one file per increment

- **The defect:** every PR inserted its history entry at the top of `docs/BUILD_HISTORY.md`. 23 of the
  49 merges of 2026-09-22..25 added to that one hunk, so two PRs in flight together conflicted on it
  at `update-branch`. The branch rule is `strict`, so every PR updates at least once.
- **What changed:** new entries are files in `docs/history/` (`YYYY-MM-DD-<slug>.md`, heading first;
  the rule is `docs/history/README.md`). `BUILD_HISTORY.md` becomes the archive up to 2026-09-25 and
  says so in its header. `CLAUDE.md`'s authority row for "why is it shaped this way" names both.
- **Deliberately not done:** no generated aggregate. Rendering the fragments into one file, with a
  freshness test, would rebuild the shared hunk under another name. Nothing old is moved or rewritten.
- **Checked by** `tests/test_build_history_fragments.py`: names sort by date, and each file opens with a
  heading.
