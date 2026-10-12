# C7 PR-D: a self-test door for the candidate image, and Gate 2 accepted

- **Why** (Thomas 2026-10-12):
  - The deploy skill's step 5 checks a candidate by importing the holdings package in an inline
    `python -c`, which the LOCAL_ONLY guard refuses.
  - `HOLDINGS_C7_PR_D_DEPLOY_VALIDATION_DESIGN_V0.1.md` §2 set ten criteria (D-1–D-10) under which a committed
    entry point would be a formal door rather than the guard's known "script on disk" gap.
  - Thomas approved the implementation under ten policy conditions.
- **What this PR adds:**
  - `scripts/c7_blocked_selftest.py` runs ten PR-D state-transition checks on synthetic lanes:
    - healthy quiet, told once, failed delivery and failed record offered again;
    - cleared once and recurrence told, a vanished ledger not cleared;
    - stale token refused, a lock not blocked, no key in the text.
  - How it stays shut:
    - Each lane gets its own `TemporaryDirectory`.
    - It refuses any argument (exit 2) and reads no environment or stdin.
    - It imports only `cash_flows`, never `store`, whose `state_dir()` defaults to the real state.
    - It prints only `PASS|FAIL`, `SOURCE <sha256> <path>` and `RESULT`.
    - Exit 0 when all pass, 1 otherwise.
  - `tests/test_c7_blocked_selftest.py` (18) pins it:
    - it passes on merged code, with closed output and source hashes equal to the tree;
    - input refused, and a raising check prints a bare FAIL;
    - every lane stays inside and is removed from the temporary root;
    - an unmakeable root, or one under `.runtime_governance_state`, fails closed;
    - three PR-D faults turn their checks red;
    - an AST import allow-list, no env, stdin, `state_dir()` or `STATE_REL`;
    - a fresh interpreter loads no network or delivery module;
    - nothing in `runtime/` imports it.
- **The ledger door test, narrowed and not widened** (Thomas decision 2026-10-12):
  - `test_no_door_module_reads_the_ledger` now judges the `cash_flows` import and the ledger/state filename
    literal apart.
  - Exactly `scripts/c7_blocked_selftest.py` may import `cash_flows` (`IMPORT_ONLY_EXEMPT`). The literal ban
    still applies to it. The three allowed files are unchanged.
  - Parametrized negative cases pin:
    - a literal in the self-test is refused, and another module's import is refused;
    - a look-alike path is refused, and the two sets do not grow.
  - This exempts a synthetic self-test from one import check. It is not a wider door to the ledger.
- **Gate 2 accepted:**
  - Thomas accepted the guard kit's direct synthetic run (2026-10-12), so GATE_2_FULLY_SATISFIED, with
    evidence GATE_2_DIRECT_SYNTHETIC_TEST_PASSED.
  - The run: 3 told DENY and 1 snapshot ALLOW added to kit `test_guard.py`; `ALL PASS`, exit 0; installed
    guard, kit and settings sha unchanged.
  - The new-session told probe was not done (NEW_SESSION_TOLD_PROBE_NOT_PERFORMED). It is recorded apart, not
    as the same evidence. The old label DIRECT_TOLD_FILENAME_PROBE_NOT_PERFORMED is superseded by these two.
- **Docs:**
  - The readiness and validation-design docs record the above.
  - The design's execution steps use `<N>` instead of a hard-coded 1217.
  - C-7 now builds the file list and the hashes in the same directory. Before, `git -C … ls-files |
    xargs sha256sum` hashed relative to the caller's directory.
  - The readiness doc's Gate 2 stop condition carries the fail-closed reading.
- **Still not done:**
  - The door has not run inside a candidate image. D-10 ISOLATION EVIDENCE is not collected, so
    VALIDATION_PATH_UNRESOLVED stays.
  - Candidate tag undecided: candidate-1217 is provisional, and this PR changes the deploy batch.
  - ROLLBACK_NOT_VERIFIED, DEPLOYMENT_HOLD, NOT_GRANTED.
  - No runtime, guard, kit, Docker or CI-config change.
