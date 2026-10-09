# Transcripts restore proves its chain, and a selective zstd set runs in parallel as a trial

- **Why (Thomas, second review 2026-10-09):**
  - #1198's restore checked only that the files the last manifest listed existed. FULL + INC-A + INC-C
    without INC-B passed when every file happened to be present.
  - A copy older than the backup recorded was not noticed.
  - Thomas approved a parallel trial of the cheaper candidate measured earlier that day (human sessions
    only, zstd -3: 452 MB against 731 MB). Switching, deletion and retention stay on hold.
- **What this PR changes:**
  - `harness_backup.sh`:
    - The transcripts code is one function, `transcripts_run`, used by both `transcripts` (all files,
      gzip, unchanged names) and `transcripts-trial` (human sessions, zstd -3, `govstate-trialzst-*`).
    - **Manifest header.** It now carries `set`, `kind`, `stamp`, `chain` (the full it builds on) and
      `prev` (the archive just before).
    - **Manifest lines.** Each is `path<TAB>size<TAB>mtime`.
    - **Log line.** It names `chain`, `prev` and `sel`.
    - **Disk floor.** The trial is skipped, not failed, under `HARNESS_TRIAL_MIN_FREE_GB` (default 10).
  - `restore_transcripts.sh`:
    - **Chain.** It walks the chain and refuses a gap or an out-of-order set (exit 5).
    - **Tail.** `--expect-head` checks the tail against the host's log.
    - **Stale copies.** A copy older than its manifest line fails (exit 6). A newer one is counted as
      changed during the backup.
    - **Legacy archives.** One without the chain headers ends UNVERIFIED (exit 7).
    - **Trial set.** `--set trialzst` restores the trial set and needs `zstd`.
  - Runbook §2.5 is rewritten around these checks and the trial.
  - Tests:
    - Ten new tests:
      - chain headers;
      - a verified chain;
      - a missing middle inc;
      - a missing last inc, caught only with `--expect-head`;
      - an archive renamed out of order;
      - a stale copy;
      - a file changed during the backup;
      - a legacy archive;
      - the trial's selection, zstd and restore;
      - the trial skipped on a short disk.
    - Three mutations were each caught: the prev check, the stale check, the tail check.
    - win32 skip ceiling 134 → 144.
- **Not covered here:**
  - Checksums: size and mtime only; the transcripts are append-only.
  - A restore on the Mac itself.
  - The Mac pull fetching `.tar.zst.age`. That needs the vault's updated pull script reinstalled on the Mac.
