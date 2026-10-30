# The transcripts backup reads the Claude home, and the root-era sessions get a set of their own

- **Why:** since C6b (2026-10-20) Claude runs as `claude`. Its sessions, the prompt collector's state and
  the user config live under `/home/claude/.claude`. `harness_backup.sh` still read `HOST_ROOT=/root`,
  whose `.claude` stopped at the switch.
  - Unless the installed copy or its crontab overrode the root (NOT_VERIFIED: `/root/backups` is root-only),
    the daily transcripts archive held root's frozen sessions for ten days and none of Claude's.
  - The watch's check 7 stayed green the whole time, because it read only the archive's age and the log
    line's `OK … enc=age`. The same move made the collector check go stale (#1341).
  - Thomas 2026-10-30: back up the Claude home daily. Keep the root-era sessions as a one-off set, and
    take the core archive's Claude config from the Claude home too.
- **What changed:**
  - `harness_backup.sh` has `CLAUDE_ROOT="${HARNESS_BACKUP_CLAUDE_ROOT:-/home/claude}"`, the variable named
    in `HOST_PRIVILEGE_SEPARATION_V0.1.md` §4.15.3, defaulting to the account that exists now.
    - `transcripts`, `transcripts-trial` and `transcripts-fullzst` read it.
    - core's four user-config members (`.claude/settings.json`, `settings.local.json`, `CLAUDE.md`,
      `skills`) come from it. `thomas_agent/.claude/settings.local.json` stays under `HOST_ROOT` with the
      checkout. `claude-config=N/5` counts both.
  - The transcripts OK line ends `root=<dir>`.
  - **A new root starts a new full.** An inc carries only what changed since the archive before it. Built
    on another root's chain, it would leave out every older file of the new one until the next weekly full.
    So a run is an inc only when the last OK line of its mode names the same root. A line without `root=`
    (every line before this change) counts as another root.
  - `transcripts-rootera`, by hand, once: an archive of `HOST_ROOT`'s `.claude` as `govstate-rootera-*`.
    No daily run prunes that set. It does not yield to the trial's disk floor.
    `restore_transcripts.sh --set rootera` restores it.
  - `backup_watch.sh` check 7 reports a last OK line that names another root than its own Claude home
    (the same variable, set by #1341), or no root at all, which means the installed script is older.
- **Untouched:** the archive format and manifest, the chain walk, member paths (`.claude/…`, so
  `restore_transcripts.sh` restores either set the same way), retention of the daily set, and the crontab.
- **Checked:**
  - The daily set holds the Claude home's files and not the host root's, and the line names the Claude home.
  - The same root continues the chain, and another root starts a full.
  - A line without `root=` starts a full.
  - The root-era set survives three daily fulls and restores with `--set rootera`.
  - Core takes the user config from the Claude home and the repository's file from the host, and carries
    no `.credentials.json`.
  - The watch reports another root and a missing root, and follows the variable.
  - An autouse fixture points the variable at a missing directory, so a test that forgets it fails
    instead of archiving this host's real `/home/claude`.
  - Four mutations were each caught: no root check on inc, the daily set from the host root, the watch
    accepting another root, and the user config from the host root.
- **Takes effect only after Thomas reinstalls both scripts** (`install -m 700 …`,
  `docs/RUNBOOK_HARNESS_BACKUP_RESTORE.md`) and runs `transcripts-rootera` once. Until the next 07:55Z run
  after the reinstall, the watch reports `root= 표기가 없습니다`. That is the truth about the installed copy.
