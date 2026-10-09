# H6b-hardening: an explicit cutover, holds on both sides, numeric amounts, transfers first, and a readiness signal

- **What this PR changes:**
  - `holdings/cash_flows.py`:
    - **Cutover.** It is recorded once, at activation, by `holdings_board --cash-flow-cutover AT`
      (Thomas's correction: a fire-time cutover would misfile a flow between a deploy and the first
      fire).
      - It goes into the state file and a `cutover_set` line.
      - A second set is refused. Moving it needs `--migrate --reason` and writes a `cutover_migrated`
        line.
      - Events before it are `PRE_CUTOVER`, and events with no cutover yet are `NO_CUTOVER`; both are
        `OBSERVED_ONLY`, so H6c cannot account the 7-day look-back.
      - `shadow_started_at` and `first_verified_fire_at` are kept beside it.
    - **Holds on both sides.** A cross-source look-alike writes a `duplicate_linked` line.
      `effective_status` then holds the older READY event too, without editing its line.
    - **Numeric amounts.** Look-alikes compare amounts as Decimals; the ledger keeps the venue's strings.
    - **Transfers first.** The two transfer histories are read first and outside the 20 s shadow
      budget, because coherence depends on them.
    - **Toss evidence.** Toss fills and settlements accumulate as evidence for D-H6-7.
    - **Readiness.** `readiness` reports what the next step waits on.
  - `holdings/store.py` and `scheduler.py`: the stored snapshot carries the readiness checks.
    - When they turn all PASS (false→true, a new epoch), one Telegram message goes out, recorded only
      after delivery. A failed send is retried next fire, and a readiness that breaks and returns is told
      again.
    - The Toss shadow pass is now separate from the summary, so a Toss failure no longer hides the
      readiness.
  - `holdings/board.py` shows the next step's state; "action required: none" while it waits.
  - Tests: the cash-flow file grows to 31 tests, plus a scheduler test that the readiness message goes
    out once and only after delivery.
- **Why:** Thomas's review of #1194 (2026-10-09) found four things to fix before a real event arrives:
  - no explicit cutover;
  - holds on one side only;
  - string amount comparison;
  - transfer reads that a slow day could crowd out, failing coherence for nothing.

  He also asked that the system wait for natural events by itself and call him only when the evidence
  is in.
- **What it does not do:** it enables no unit and no NAV per unit. The readiness message asks for
  approval; nothing turns on by itself.
