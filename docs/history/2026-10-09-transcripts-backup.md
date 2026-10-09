# Conversation transcripts join the encrypted backup, and the backup watch reads them and the prompt collector

- **Why:** every prompt Thomas typed or pasted into a Claude Code session lived only in
  `/root/.claude/projects/*.jsonl` on this disk (2.20 GB, 12,786 files on 2026-10-09). No archive carried
  them, so a lost disk lost them. Thomas chose a separate mode over growing `core` (2026-10-09).
- **What this PR changes:**
  - `scripts/ops/harness_backup.sh` gains `transcripts`:
    - **Encryption.** It uses the same age recipients and the same refusal rules as core. No new key.
    - **Archives.** A full archive is written weekly (`HARNESS_TRANSCRIPTS_FULL_DAYS`, default 7), and
      an archive of the files changed since the last one on the other days, with one hour of overlap.
      Two fulls are kept, and the incs that sit on them.
    - **Names.** The files are `govstate-transcripts-{full,inc}-<stamp>.tar.gz.age`. The Mac pull
      fetches them unchanged, and the core and candle prune globs do not match them.
    - **tar exit 1.** A session written while tar reads it makes tar exit 1. That is a valid snapshot
      of the lines written so far, as for core.
  - `scripts/ops/backup_watch.sh` gains two checks:
    - **Check 7.** A transcripts archive is younger than 26 h, a full one younger than 8 days, and
      the last `mode=transcripts` line is OK with `enc=age`.
    - **Check 8.** The prompt collector's `status.json`, when present, is not FAIL and succeeded
      within 50 h. A missing file is not a problem: the collector is not installed there.
  - The runbook gets §2.5 (restore order: newest full, then the later incs).
  - Tests:
    - Five new tests in `tests/test_ops_harness_backup.py`: full, inc, retention, refusal, and core
      unaffected.
    - Seven new tests in `tests/test_ops_backup_watch.py`.
    - The watch tests now point the collector status at a scratch path, so they never read the host's file.
    - Three mutations were each caught: no mtime filter, wrong retention, collector root dropped.
    - `tests/skip_ceiling.json` win32 115 → 129: the 14 new tests run bash scripts and skip on Windows
      like their neighbours (5 backup + 9 watch cases; CI read `Skipped tests on win32: 129`).
- **Sizes measured 2026-10-09:** a full archive is about 0.8 GB compressed (`gzip -1` estimate). One
  day's changed files were 178 MB raw. The Mac keeps 90 days, so it will hold about 13 fulls plus the
  incs.
- **Not in this PR:** the prompt collector itself. It lives in the vault (`tools/prompt_conversations.py`)
  and runs in the morning job.
