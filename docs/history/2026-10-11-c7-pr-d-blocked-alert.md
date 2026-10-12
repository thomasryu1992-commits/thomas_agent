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
      after a delivery, through a tmp file of its own and a replace (review 2026-10-12, below).
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
- **Guard note (review 2026-10-12: GUARD_COVERAGE_PARTIAL, read-only analysis):**
  - The path rules cover the file where it lives. `runtime_governance_state/+holdings` matches any path into
    the state directory (repo, worktree, `/app`, `docker cp`). `holdings/+holdings_` matches a reference
    relative to the state root.
  - The by-name rule does not match it. The pattern wants `.json` right after `holdings_cash_flow` or
    `_state`, so a bare name with no directory passes. So does a copy at another path. The drawdown told
    file and the peak file have the same gap today.
  - The global copy installed on this host has the same sha256 as the repo hook (`865e9eec…`), so this
    analysis applies to both.
  - The risk is low. The file holds a key (a 16-hex prefix of a sha256 over the state bytes, not
    reversible), `told_at` and `resolved_told_at`. It holds no amount, asset or account. A leak would tell
    when a failure was told, nothing financial. That is why it does not hold the merge.
  - The follow-up is a guard change, which needs its own approval. Add the narrow name
    (`holdings_cash_flow_blocked_told\.json`, or `holdings_[a-z_]*_told\.json` to take the drawdown told
    file too). Never use a wide `holdings_[a-z_]+\.json`, which would also refuse `holdings_snapshot.json`,
    the one table allowed out.
- **What it deliberately does not do:**
  - It creates no checkpoint, writes no marker and decides no PR-A reason code (the §6 family code stands in for
    a marker without one).
  - It adds no write refusal, recovery, backfill or cutover change.
  - It adds no new public reason code and no rate limit. A told record that cannot be written makes the alert
    repeat each fire rather than go quiet. A persistent limit would be a later decision.
  - It drops "days left" (§6). It would need an unverified ledger tail time, which D-3 said not to store unless
    needed.
- **Review corrections (Thomas 2026-10-12):**
  - **P0: nothing to verify is not a verification.** `blocked_state` answered not-BLOCKED both when it
    re-derived a chain and when there was none: no ledger and no state (`{"blocked": False}` before any
    check), or no ledger beside a state (a missing ledger verifies as an empty chain). `blocked_alert` read
    either as the clearing. Reproduced: break the chain, tell it, delete both files, fire. Refresh rebuilt
    a state, and the clearing went out. If the next write starts a new chain, that one verifies too.
    - Now a not-BLOCKED answer carries `verified_since`, the `at` of the first line of a chain re-derived
      in that check, or None.
    - A told failure clears only when that line is no newer than its `told_at`, so the ledger that failed
      verifies again. Otherwise the record stays standing, nothing is sent, and the status line says
      `clearing withheld (nothing verified)` or `(the ledger began after the failure was told)`.
    - Unchanged:
      - A first run with no told record sends nothing.
      - C7-11 (a verified ledger, the state missing) still clears.
      - A check that raises is BLOCKED (`check`), a known repeat, never a clearing.
    - Limits:
      - A ledger truncated but still starting at its first line reads as repaired. Telling it from a repair
        is PR-A's checkpoint.
      - A failure told while no ledger existed (a pre-H6d refusal before the first write) cannot clear by
        this rule, because the first ledger is written after it. It stays withheld until a later decision
        (C7 adopt, PR-C).
      - The production lane has a ledger, so this limit does not reach it today.
      - A louder message for a vanished ledger is not added: this PR changes only whether a clearing is told.
  - **P1: the told record under overlapping fires.**
    - Facts:
      - `holdings_refresh` is a maintenance kind, and compose runs one `tick --lane maintenance`.
      - `run_due` fires serially.
      - `claim_due` takes each occurrence at most once, under the store lock.
      - Only `scheduler.py` calls `mark_blocked_told`.
      - So fires overlap only when an operator runs a manual `tick` (`--lane all`) while the service runs.
        Nothing excludes that.
    - Three fixes:
      - The tmp file is unique per write (pid + uuid) and removed if the replace fails. A fixed `.tmp` let
        two writers tear one file.
      - `mark_blocked_told` refuses a stale token and returns False:
        - a `now` older than the record's latest time;
        - a clearing for a key that is not the standing one.
      - The read, the check and the replace run as one step under the state lock (`_state_lock`, the
        existing `filelock.locked` sidecar every state write takes first; Thomas 2026-10-12, second review).
        No new lock file and no new store. The judgement still takes no lock; only the told write does.
        A lock it cannot get raises STATE_LOCKED: not recorded, and offered again by the next fire.
    - The fire's status says `told not recorded:stale` for a refused token, never `told`.
    - Interleavings, each pinned by a test:
      - A judges a clearing, then B records a newer failure, then A's write lands: refused, and B's failure
        stays standing.
      - A stale BLOCKED write lands after the clearing was recorded: refused, so a recurrence is still told.
      - Both fires deliver the same failure: two messages.
      - A stale BLOCKED writer passes its check and stalls before its replace, while a newer clearing or a
        newer failure is written. A synthetic test forces this interleaving. Without the lock the stale
        record replaced the newer one: the clearing was erased in one test, the newer failure overwritten
        in the other. Both reproduced on `89aedde3`. With the lock the newer writer waits; the stale write
        lands first and the newer one is checked against it and written over it.
      - Before the lock, Windows CI showed that a replace onto a file another writer is moving raises
        `PermissionError`. Writers no longer overlap. A reader holding the file open can still make a
        replace fail there; that write raises and is not recorded.
    - **At-least-once, not exactly-once.** A send and its record are two steps, so a crash or an overlap
      between them repeats a message. Silence is not the failure mode. The lock closes the window in which
      a stale record could overwrite a newer one. It does not make the send and the record one step.
- **Tests** (synthetic only):
  - `tests/test_mvp_runtime_holdings_c7_blocked.py` (47) covers:
    - what is BLOCKED, every early return of `refresh`, and locks;
    - told / cleared / told again, delivery and told-write failure, and restart;
    - key versions, fire order and isolation;
    - that nothing identifying leaves, and the PR-A marker;
    - the review's Cases A–E (first run, both files gone, a ledger begun after, a ledger gone beside a state,
      a real repair cleared exactly once, a check that raises, C7-11 with and without a verified ledger);
    - stale and overlapping told writes, a failed replace, and the stale status word;
    - forced races of a stale BLOCKED write against a clearing and a newer failure, eight writers ending on
      the newest, and a busy lock that is not recorded and is told again.
  - `test_defect_c7_d` flips to `test_c7_d_a_refused_write_is_told_once_by_the_blocked_check`.
  - Mutations:
    - Removing the scheduler block or the reason preservation fails 11 of the 28 tests it had then.
    - Removing the clearing rule fails 4 (Cases B and E).
    - Removing the lock fails 3: the two forced races and the busy lock.
    - Removing only the continuity half fails 1.
    - Removing the stale-token checks fails 4 (3 then; the eight-writer test now expects the newest to win).
  - The synthetic lane pins the cutover line's wall-clock stamp to its synthetic day, as a real lane's first
    line is older than any told record.
