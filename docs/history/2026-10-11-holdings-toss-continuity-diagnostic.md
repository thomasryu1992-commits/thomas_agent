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
  `cash_flows.py`, `store.py` and `holdings_board.py` to read the ledger. No new reason code is added; a broken
  chain reports `verify`'s `LEDGER_TAMPERED`.
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
  - Do not deploy before that refresh.
