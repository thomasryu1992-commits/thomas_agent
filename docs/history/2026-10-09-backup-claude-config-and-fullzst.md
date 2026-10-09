# Core carries Claude Code's configuration; a full zstd transcripts set exists as an unscheduled switch candidate

- **Why (Thomas, TA-PROMPT-RETENTION-01, 2026-10-09):** the retention review found Claude Code's own
  configuration in no backup: user settings (hooks, permissions), the global CLAUDE.md, this repository's
  untracked `.claude/settings.local.json`, and the author's skills' git history. Thomas also named "all
  conversations + zstd -3 + age" as the long-term candidate, because nobody has yet shown that every
  important session was extracted into long-term memory. Selective backup stays a trial.
- **Classification of what was missing (metadata only, no value read):**
  - **Added to core:**
    - `~/.claude/settings.json`, `settings.local.json`, `CLAUDE.md`: needed to restore the system, no other copy.
    - `thomas_agent/.claude/settings.local.json`: untracked by git.
    - `~/.claude/skills` without `synced/`: the skills and their assets are already copied into the vault by
      `prompt_history`, but the git history is local only.
  - **Left out:**
    - `skills/synced/` (213 of the 221 non-SKILL.md files): Anthropic and organisation skills, re-synced from
      claude.ai.
    - `*.bak-*`: superseded copies that git already holds.
    - caches, telemetry, `file-history`, `remote/`, `plugins/`, `sessions/`, `shell-snapshots/`: recreated by the app.
    - `.credentials.json` and `~/.claude.json`: login and account state. A login is recreated, not restored,
      and a live token must not sit in an archive that leaves the host daily.
- **What this PR changes:**
  - `harness_backup.sh` core:
    - five optional members, counted as `claude-config=N/5`;
    - excludes for `skills/synced` and `.bak-*`;
    - the anchor check also refuses an archive whose member list names `.credentials.json` or `.claude.json`
      (`reason=credential-in-archive`).
    - Measured on this host: +0.65 MB, 130 files.
  - `transcripts-fullzst`: set `fullzst`, everything the gzip set carries, `zstd -3`. It is not in cron and the
    runbook says to run it only into a separate destination. The disk floor now applies to every mode except the
    real `transcripts`. `restore_transcripts.sh` accepts `--set fullzst`.
  - Tests:
    - five new: config carried and login refused, absent config counted, a slipped-in credential refused,
      fullzst full+inc restore, fullzst disk floor;
    - one narrowed: core still carries no transcripts;
    - win32 skip ceiling 201 → 206 (main moved to 201 with #1200).
- **Validated on real data in a scratch destination with a throwaway age key:**
  - full 518 MB in 16 s, inc 19 MB;
  - `CONTENT_VERIFIED … archives=2 tail=ok core-hash=ok:161,bad:0`;
  - without the last inc: `BROKEN tail=MISSING`, exit 5.
  - Everything was deleted afterwards.
- **Not changed:** the gzip set, the trial, cron, retention, the Mac pull, and the watch.
