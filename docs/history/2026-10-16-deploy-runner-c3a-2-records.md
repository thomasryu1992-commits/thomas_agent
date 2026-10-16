# Option C deploy runner C3a-2: the actor's records — consumption store, request states, audit chain, latch, startup check

**What changed** (`runtime/mvp_runtime/deploy_runner/`, four schemas, one test file).

The second increment of the Option C deploy runner (Thomas 2026-10-16: C3a-2 implementation and PR
creation; merge, install and operation not approved). C3a-1 decided whether a signed approval counts.
This increment holds the records that make an approval count **once** and make the runner refuse
when it cannot be sure what it did. Nothing is wired into a service or installed.

- **`private_files.py`** — every record lives in one directory the actor owns, under the same rules:
  - the directory and the file are opened without following links (`dir_fd` + `O_NOFOLLOW`);
  - each is checked on its descriptor: owned by the actor, no group or other bits, and for a file,
    regular with one link;
  - a row is one ASCII JSON line written under `flock` on the file's own descriptor, cut back if
    the write is short, and `fsync`-ed;
  - a record is read whole or refused: a torn last line, a blank line or a non-ASCII byte fails it.
- **`consumption.py`** — the actor never creates the store.
  - The install writes it with a `GENESIS` row carrying `store_id`. A missing file or first row is
    `STORE_GENESIS_MISSING`.
  - Rows are hash-chained: sequence, link to the previous row, and a self-hash. Any break, or a
    duplicate `approval_id`/`nonce`, is `STORE_CHAIN_BROKEN`.
  - Spending is read–verify–append under one exclusive lock. A second spend of an id or nonce is
    `APPROVAL_ALREADY_CONSUMED`. A failed write leaves the store as it was and says
    `CONSUMPTION_WRITE_FAILED`.
  - Recovery row types (TYPE A/B) are defined in the schema. C3a refuses a store that holds one
    rather than guess.
- **`request_state.py`** — the record that makes a resend a lookup.
  - Unlike `bridge_idempotency`, **a claim never times out**: it stays `claimed` (resend:
    `REQUEST_IN_FLIGHT`) until it finishes.
  - A finished request replays its recorded reply.
  - `unknown` and `partial` never run again.
- **`audit_chain.py`** — the actor's effect audit, chained by `seq`, `previous_event_sha256` and
  `event_sha256`.
  - Each row is validated against `deploy_audit.v0.1` before it is written.
  - A damaged chain takes no more rows.
- **`latch.py`** — written with `O_EXCL`, never rewritten, never removed by code.
  - A latch that cannot be read with certainty counts as latched (`RUNNER_STARTUP_BLOCKED`).
- **`startup.py`** — the startup check, before the actor would listen:
  - instance lock, consumption store (genesis, chain, `store_id` against the install and the last
    `RUNNER_START`), audit chain;
  - the cross-check that every spent approval has exactly one CONSUMED audit row and the reverse;
  - every consumed request without an outcome, and every leftover claim, becomes `unknown`
    (`RECONCILED_UNKNOWN`) and the runner latches;
  - unresolved effects keep it latched **whether or not the LATCH file exists**;
  - a wall clock behind the last time seen starts no new effect;
  - `RUNNER_START` is written last.

**Why some codes are shared.** `REQUEST_ID_REUSED` and `REQUEST_IN_FLIGHT` keep the meanings
`bridge_idempotency` gives them. `RUNNER_STARTUP_BLOCKED` and `STORE_ID_MISMATCH` mean one thing in
each module that raises them. They are declared in `tests/test_diagnostic_code_index.py` with that
reasoning.

**What this does not protect.** The chains detect partial edits by someone who cannot recompute them.
An actor or root rewriting the consumption store and the audit together is not detected locally; only
an external anchor can show it. Permission separation is still C4 and C6.

**Numbers.**
- **Tests:** `tests/test_mvp_runtime_deploy_runner_records.py`, 71 cases, all synthetic in
  `tmp_path`. They include a concurrent spend of one approval from eight threads: exactly one succeeds.
- **Fault injection** (scratch, outside the suite): each of 34 injected faults made a test fail.
  - Three were found missed on the first pass and closed with dedicated cases:
    - a last row that ends in a stray byte instead of a newline;
    - an outcome audit row lost after the request finished (case E);
    - single-field chain breaks, so each of the sequence, link and hash checks is shown to stand on
      its own.
- **Windows skips:** +71. Every case here needs POSIX private-file opens, so on Windows they all
  skip. A simulated run gives 191 passed and 97 skipped for both runner test files. The win32
  ceiling moves 332 → 403, predicted; this PR's Windows run is the measurement.
