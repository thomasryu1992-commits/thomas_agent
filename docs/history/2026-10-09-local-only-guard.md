# A tripwire on the holdings lane's LOCAL_ONLY files (PR-A of the H6 privacy work)

- **Why:**
  - On 2026-10-09 a Claude session doing a portfolio review read the classification entries and the
    local board straight from `.runtime_governance_state/holdings/`. Its report then derived one
    instrument's share of the scope from the local-only breakdowns.
  - That broke three things: `classification.py` (the entries "never reach the repository or a
    model"), the H3 one-table invariant (`disclosure.py`), and Appendix B's no-third-party rule for
    the Toss data.
  - Nothing stopped it. Claude runs as root, the state directory's 0700 owner does not bind root,
    `permissions.deny` was empty, and the only hooks guarded commits.
  - Thomas approved this PR on 2026-10-09 ("PR-A 구현 승인").
- **What this PR changes:**
  - `CLAUDE.md` gains a guardrail. Claude reads the lane only through its one table:
    `holdings_board`, or `holdings_board --json`. The terminal-only modes are Thomas's.
  - `.claude/settings.json`:
    - `Read` deny rules, project-anchored (`/…`) and absolute (`//root/thomas_agent/…`). The
      directory and its contents are listed separately, because the documentation does not say
      whether `dir/**` covers the directory itself. Per the permissions docs, Read deny applies
      best-effort to Grep and Glob, and to the file commands it recognises in Bash (`cat`, `head`,
      …).
    - A second `PreToolUse` entry for `Bash|Read|Grep|Glob` runs `.claude/hooks/guard-local-only.sh`.
      When the guard is missing or fails, the wrapper refuses any holdings reference itself. A
      broken hook is otherwise a pass: Claude Code lets a non-zero exit or a timeout through.
  - The guard reads command text only and opens no file. It refuses:
    - a path into the holdings state directory, whatever comes before it;
    - a state file by name;
    - a glob under the state root;
    - a recursive read of the state root;
    - the terminal-only `holdings_board` modes, including H6d's planned `--flows`, `--resolve` and
      `--semantics-epoch`, so the two PRs do not depend on each other;
    - inline Python that imports the holdings package.
  - `tests/test_claude_local_only_guard.py` has 55 cases on synthetic payloads: 29 refused, 10
    allowed, 7 file-tool cases, plus the wiring and the missing-guard fallback.
  - `tests/skip_ceiling.json`: win32 134 → 187, for the 53 bash cases that skip on Windows like
    their neighbours.
- **What this does not do — the limits, stated plainly:**
  - It is a tripwire, not a boundary. Any of these get past it:
    - a path assembled at run time;
    - a script written to disk and then run;
    - a subprocess that opens files itself;
    - any user in the `docker` group, root or not (`docker exec … cat` reaches every mount).
  - A committed `.claude/` covers only sessions whose project directory holds it. Worktrees cut from
    an older commit lack it. Covering every session is the user-level `/root/.claude/settings.json`,
    which is Thomas's to edit and is untouched here.
  - The boundary that would hold is OS separation, a decision of its own. It goes in four steps:
    1. inventory every docker or root action Claude performs (deploy, module CLIs, `chown`);
    2. write sudo-allowlisted wrappers for exactly those;
    3. run Claude as a non-root user outside the docker group;
    4. after that, Claude can no longer deploy or repair state directly.
  - It changes no runtime code, image or service. The runtime reads its state as uid 10001, and
    Thomas's terminal is unaffected.
- **Rollout:** this is harness configuration. Nothing is deployed. The primary checkout follows main,
  so the merge is the moment it applies on the host.
