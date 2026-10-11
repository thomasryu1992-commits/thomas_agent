# A terminal-only diagnostic says whether the Toss windows tile the stretch since the cutover

- **What this PR adds:**
  - `holdings/cash_flows.py`: `toss_continuity(rows, now)` and `toss_continuity_view(state_dir, now)`, next to
    `flows_view` and read-only like it. They read the verified ledger and judge it worst first:
    - `LEDGER_INVALID`: the chain does not verify;
    - `UNCHECKED`: no ledger, or no cutover;
    - `UNEXPLAINED_GAP`: a stretch no window covers;
    - `INCOMPLETE_EVIDENCE`: no window before the first one, a strict overlap, a duplicate window, a truncated
      order list, an unreadable bound, an amendment with no window, or no window at all;
    - `CONTINUOUS`.

    Each verdict comes with counts and the gap bounds, as times only.
  - `scripts/holdings_board.py`: the `--toss-continuity` mode. The verdict is printed on the first line.
    `EXIT_BLOCKED` on `LEDGER_INVALID`, otherwise `EXIT_OK`.
  - `.claude/hooks/guard-local-only.sh`: the mode joins the terminal-only list. The guard test's
    `TERMINAL_ONLY` list carries it too.
- **Why (Thomas 2026-10-11, C7 D2):** option A keeps the cutover only if Toss has no unexplained past gap
  (`HOLDINGS_LEDGER_CHECKPOINT_V0.1.md` §12.3 in #1211). That fact sits in the LOCAL_ONLY ledger, so it needs a
  check Thomas runs and passes on as one line.
- **How it reads the windows:**
  - Each window runs from the previous fire's cash point to this fire. A downtime is therefore one long
    window, and a gap is a fire that recorded no window. A gap is not proof that a record was lost.
  - No ledger line can explain a gap yet: option (a), the skipped-window line, is not built, and this PR does
    not pre-recognize an event that no writer produces. So every gap counts as unexplained.
  - `toss_window_observed` arrived with H6d-min, after the cutover was set. So a ledger whose cutover precedes
    its first window reads `INCOMPLETE_EVIDENCE` (`no_window_before_first`) from this check alone. This is a
    property of the code; the real file was not read.
- **Placement:** the code lives in `cash_flows.py` because `test_no_door_module_reads_the_ledger` allows only
  `cash_flows.py`, `store.py` and `holdings_board.py` to read the ledger. A broken chain reports `verify`'s
  `LEDGER_TAMPERED`.
  - `LEDGER_INVALID` is a finding to investigate. A re-run that comes back clean does not clear it.
- **Pre-merge hardening (Thomas 2026-10-11):**
  - **Terminal only:** the check is refused before the ledger is opened in two cases:
    - stdin or stdout is not a terminal: `HOLDINGS_CASH_FLOW_TOSS_CONTINUITY_REFUSED`, the one new code;
    - a host-side root run: `STATE_FOREIGN_ROOT_RUN`.

    This is the resolve and epoch pattern. Both checks are slip guards against a pipe, a model's shell, or a
    `docker exec` without `-it`. They are not a boundary: root and the docker group can read the file.
  - **Guard list consistency:** `test_every_holdings_board_mode_but_the_one_table_is_terminal_only` reads
    every `holdings_board` mode from the source and requires each one except `--json` to be in the guard
    test's `TERMINAL_ONLY` list. Each flag in that list is run through the real guard. The test is plain
    Python, so it runs on Windows too.
  - **Windows skip ceiling 254 → 256:** the two new skips are the Bash and Monitor cases of
    `--toss-continuity` in the `@posix_only` guard test ("the guard is a bash script"). The test has 20
    items on main and 22 here; the new test file has no skip.
- **What it deliberately does not do:**
  - It changes none of these: the readiness, `history_completeness` (which does not exist yet), the cutover,
    the epoch, the ledger format, the Binance collection, Toss calls, the schedule, the outward summary.
  - It writes nothing.
  - It does not run on any real ledger in this PR; the synthetic ledgers are written through `_commit`.
- **Guard reach on this host (2026-10-11):**
  - The branch's project guard refuses the mode.
  - The installed global guard (`/root/.claude/hooks/`) does not refuse it until Thomas refreshes it from main
    after the merge (`global-guard-kit/README.md`, "Update after the repository's guard changes").
  - The stale primary checkout has no project guard.
  - Do not deploy, and do not build a tool image, before that refresh.
- **How Thomas runs it (after the merge, the refresh, and a separate approval):** a one-off tool image, not a
  deploy (CLAUDE.md, the deploy skill's one-off section).
  - Build `thomas-agent-runtime:tool-1212` from a clean origin/main tree. `latest` and the running
    containers are untouched.
  - Run it with `docker run --rm -it --network none` as the scheduler's user, read off
    `docker inspect thomas-scheduler` (`thomas` on 2026-10-11).
  - Mount the state directory read-only (`:ro`), and pass no env file: the check needs no key and no
    network.
  - `docker exec` into a running container works only after an ordinary deploy of this code, which is not
    planned.
