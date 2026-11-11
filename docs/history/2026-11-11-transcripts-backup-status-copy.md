# The transcripts backup leaves a copy of its log lines where the Claude account can read them

- **Why:** since C6b (2026-10-20) the prompt collector runs as `claude`. Its "원본 암호화 백업" check
  reads `/root/backups/governance-state/backup.log`, and `/root` is 0700, so the check said UNKNOWN
  ("백업 로그 없음") on every run. It was a blind spot, not a failure. Nothing on the Claude side could
  tell whether the daily transcripts archive ran, or whether #1342 (the Claude home as its root) was ever
  installed (NOT_VERIFIED there for the same reason).
- **What changed:**
  - `harness_backup.sh`'s `log` also appends every `transcripts*` line to
    `$HARNESS_BACKUP_STATUS_DIR/backup.log` (default `/var/lib/thomas-measure/transcripts-backup`), 0644.
    The directory is root:claude 0750, the convention of the other readings under `/var/lib/thomas-measure`.
  - Only the transcripts modes. Core's and the candles' lines stay in `/root`.
  - Best effort: an absent directory copies nothing, a symlinked or unwritable copy is skipped, and
    neither changes the run's outcome or exit code.
  - The collector (vault `tools/prompt_conversations.py`) reads the copy first. It also reports a last OK
    line whose `root=` is not the Claude home, which says the installed script predates #1342.
- **Untouched:** `/root/backups/governance-state/backup.log`, the archives, `backup_watch.sh`, the crontab.
- **Install (Thomas, root):** the directory, the seed and the reinstall, in
  `docs/RUNBOOK_HARNESS_BACKUP_RESTORE.md` §2.5 "The status copy".
- **Checked:** the copy carries the transcripts OK and FAILED lines byte for byte at 0644; a core run
  writes no copy; a symlinked copy is not followed and an unwritable directory leaves the run OK. The
  copying test fails against the script before this change. An autouse fixture points the directory at
  a missing path, so no test appends to the host's real copy.
