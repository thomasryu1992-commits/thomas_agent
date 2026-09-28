# The POSIX file lock waits at most sixty seconds

- **What changed:** `filelock.py` polls a non-blocking lock (`LOCK_EX|LOCK_NB` on POSIX, `LK_NBLCK` on
  Windows) every 50 ms until `ACQUIRE_DEADLINE_SECONDS = 60`, then raises `TimeoutError`, which
  `locked()` already turned into the caller's `PersistenceError`. No caller changed.
- **Why:** on Linux, where the service runs, `flock(LOCK_EX)` blocked forever. The module docstring
  promised "never an unbounded hang", but only the Windows path kept it. One wedged holder could stall
  every write in every container sharing the store (system review A1).
- **Why sixty seconds:** it detects a hang; it does not tune contention. The hot shared stores
  measured about 1.1 s worst wait (review §6). The deadline bounds the waiter only: a holder may keep
  a lock longer (the forward-cohort walk replays its book under one, and those fires run up to
  ~160 s). Only a contender that arrives meanwhile is refused.
- **Live path:** every write that can run with an order already at the venue catches the error and
  reports it (`LIVE_POSITION_PERSIST_FAILED`, `LIVE_OUTCOME_PERSIST_FAILED`,
  `BRACKET_BREAKER_UNRECORDED`, the API breaker's `unrecorded`, `AUDIT_NOT_RECORDED`). Before this
  change each of them could hang forever instead. Pre-venue locks refuse the entry. The PR body has
  the site table for Thomas to review before merge, as the review's sequence 1-1 requires.
- **Deliberately not done:** the holder's PID and label in the sidecar. The sidecar is never read or
  written, and a byte-locked region cannot be read on Windows.
