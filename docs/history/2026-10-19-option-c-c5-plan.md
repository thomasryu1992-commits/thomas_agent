# Option C C5: the production host change plan (C6a runner install, C6b Claude account move)

**What changed** (documents only):
- new `docs/proposals/HOST_PRIVILEGE_SEPARATION_C5_PLAN_V0.1.md` (DRAFT);
- `HOST_PRIVILEGE_SEPARATION_V0.1.md`:
  - the status line;
  - one decision row in §4.25.1;
  - the §8 statuses of D-5, D-6, D-7, D-9, D-10b, D-12, D-13, D-14, D-15 and D-16;
- `STATUS.md`, regenerated.

**Decision and approval** (Thomas 2026-10-19): the C5 decision line and the plan-writing approval are
quoted verbatim in the plan's §0. The plan is the C5 deliverable; its approval is the C5 completion. The
host changes (C6a, C6b) are separate approvals.

**How the plan was written.**
- Host facts are metadata read on 2026-10-19:
  - accounts, groups, sudoers file names;
  - the scripts root's crontab runs (paths only, no arguments or contents);
  - systemd unit names, tool versions;
  - file names, modes and owners of `.env` and its copies (no contents);
  - SSH key count.
- The managed-settings facts behind D-13 come from Claude Code's documentation (managed-settings,
  settings, hooks pages):
  - `/etc/claude-code/managed-settings.json` is the Linux path;
  - managed hooks cannot be removed by user or project settings;
  - keeping the file out of the session user's reach is the OS's job.
- The C6a commands follow the order and the tool steps the C4 runner ran green (#1241 run 39046415908).
  They are re-checked before C6a runs.

**Findings recorded.**
- D-6 named three `.env` copies; there are five.
- No login account exists besides root, and the docker group has no members.
- C6b needs three things first, each its own decision:
  - Claude's own read door in production (D-11 a1);
  - the change of who deploys (Thomas, until the runner's `promote` exists);
  - the classification of the 15 cron lines by their contents.
- The only irreversible step in either stage is D-6's deletion of the `.env` copies. It goes last and is
  confirmed separately.

**Not done.** No host change of any kind, no install, no key, no Docker command. No `.env`, state, holdings
or cron script content was read.
