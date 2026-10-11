# C7 PR-D: a ledger or state every writer refuses is told once, cleared once, and told again if it returns

- **What this PR changes** (C7 §6; Thomas 2026-10-11, review PR only):
  - `holdings/cash_flows.py` adds a BLOCKED judgement and its told record:
    - `blocked_state(state_dir)` is read-only. It verifies the chain, then loads the state, then checks the pending
      pre-H6d import, then looks for a PR-A detection marker. It answers BLOCKED with a reason code, a category
      (`verify`, `load_state`, `legacy`, `marker`, `check`) and a key.
    - `blocked_alert(...)` turns that into one message per failure, none while a told failure stands, one when it
      clears, and one again if it returns.
    - `read_blocked_told` and `mark_blocked_told` keep the told record in a new LOCAL_ONLY file,
      `holdings_cash_flow_blocked_told.json`. It holds `key`, `told_at` and `resolved_told_at`. It is written only
      after a delivery, with the drawdown told file's tmp → replace pattern.
  - `holdings/store.py`:
    - `_reason(exc)` makes the board's `cash_flows.error` carry the reason code instead of `ToolError`, in the
      three places that dropped it.
    - `blocked_check` and `mark_blocked_told` are the scheduler's doors to the above. `blocked_check` never
      raises; a judgement that cannot run is told as BLOCKED.
  - `scheduler.py`: the holdings fire gets a fourth alert after the three it had. It is judged after `refresh`
    whatever `refresh` returned, so its four early returns before the cash-flow block and two after it no longer
    hide a broken ledger. Transport failure and told-write failure are separate status words. The key never
    appears in the status line.
- **The key (R-1, conditionally approved):**
  - Exceptions: `v1:state:<category>:<16-hex digest of the state file>`, or `missing` / `unreadable` in place of
    the digest.
  - A PR-A marker: `v2:ckpt:<lines>:<hash>`, the frozen checkpoint (C7 §6). A state digest would change every fire
    while detect-only writes go on.
  - The digest lives only in the told file and in the token, never in a message, a snapshot or a status line.
- **The key is stable only because of the fire's order:** `summary()` fails before `update_readiness` and
  `mark_fire_verified`, the writers that save the state without verifying the ledger. A test pins that a fire on
  a broken ledger leaves the state's bytes as they were.
- **Recurrence is decided by the told record, not the digest.** A failure told as cleared and seen again is told
  again, even with the same key.
- **Known repeats, not defects:**
  - The chain is judged before the state. A state failure that a chain break then overtakes changes the key,
    so it is told again with no clearing between. It is told once more when only the state failure is left. A
    test pins the count.
  - When the state is missing or unreadable the digest contributes nothing (`…:missing`, `…:unreadable`), so
    such keys are not unique per failure. A recurrence is still told, by the told record's clearing.
- **Behaviour that changes when this is deployed:**
  - Every holdings fire reads the cash-flow ledger and state once more, read-only, through `blocked_state`.
  - The board's `cash_flows.error` carries the reason code.
  - A broken ledger sends one message, and its clearing sends one.
  - Healthy operation sends nothing new.
- **Guard note:** the LOCAL_ONLY guard's by-name rule does not match the new told file's name. Its pattern
  wants `.json` right after `holdings_cash_flow` or `_state`, so the file is covered by the state-directory
  path rule only. That is a guard follow-up, not this PR.
- **What it deliberately does not do:**
  - It creates no checkpoint, writes no marker and decides no PR-A reason code (the §6 family code stands in for
    a marker without one).
  - It adds no write refusal, recovery, backfill or cutover change.
  - It adds no new public reason code and no rate limit. A told record that cannot be written makes the alert
    repeat each fire rather than go quiet. A persistent limit would be a later decision.
  - It drops "days left" (§6). It would need an unverified ledger tail time, which D-3 said not to store unless
    needed.
- **Tests** (synthetic only):
  - `tests/test_mvp_runtime_holdings_c7_blocked.py` (29) covers what is BLOCKED, every early return of `refresh`,
    locks, told/cleared/told-again, delivery and told-write failure, restart, key versions, fire order, isolation,
    nothing identifying leaves, and the PR-A marker.
  - `test_defect_c7_d` flips to `test_c7_d_a_refused_write_is_told_once_by_the_blocked_check`.
  - A mutation removing the scheduler block or the reason preservation fails 11 of the 28 it had then.
