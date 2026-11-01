# The backup watch reads the prompt collector's status under the account it runs as

- **Why:** on 2026-10-28 08:00Z the watch alerted that the prompt collector's last success was 200 h ago.
  The collector had run every night. Its own status file said `last_success` 2026-10-27T23:50Z.
  - Since C6b (2026-10-20) the collector runs as `claude` and writes
    `/home/claude/.claude/prompt-collector/status.json`.
  - The watch still read `/root/.claude/prompt-collector/status.json`. That copy stopped at the switch,
    so it aged by an hour every hour, and the alert went to the wrong cause.
- **What changed** (`scripts/ops/backup_watch.sh`, check 8):
  - The default path is `$HARNESS_BACKUP_CLAUDE_ROOT/.claude/prompt-collector/status.json`, and the root
    defaults to `/home/claude`. `PROMPT_COLLECTOR_STATUS` still overrides the whole path.
  - The variable name is the one `HOST_PRIVILEGE_SEPARATION_V0.1.md` §4.15.3 (W-30) gives the backup script, so the
    backup and its watch can name Claude's home with one variable.
- **Not changed, and still open:** `harness_backup.sh` archives transcripts and the collector state from
  `HOST_ROOT` (`/root`). Unless the installed copy or its crontab sets `HARNESS_BACKUP_HOST_ROOT`, which this
  account cannot read (NOT_VERIFIED), the transcripts archive since 2026-10-20 holds root's frozen
  transcripts, not Claude's. Check 7 stays green either way, because it reads the log line and the file's
  age, not what is in it. Moving that root needs a fresh FULL archive: an inc carries only files newer
  than the last archive, so Claude's older files would be missing from the chain until the next weekly full.
- **Takes effect only after Thomas reinstalls the watch on the host**
  (`install -m 700 scripts/ops/backup_watch.sh /root/backups/backup-watch.sh`, from
  `docs/RUNBOOK_HARNESS_BACKUP_RESTORE.md`). Until then the alert repeats every morning.
- **Checked:** with `PROMPT_COLLECTOR_STATUS` unset, a stale status under the claude root is reported and
  a fresh one is silent.
