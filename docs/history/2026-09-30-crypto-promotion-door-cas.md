# The promotion door no longer writes over a pool that changed while it worked (crypto refactor plan PR-S3)

- **The finding (S-3):** `scripts/promote_strategy_candidates.py` read the active pool without the
  lock, ran its checks, and replaced the whole file. A write that landed in between was lost. The
  one that mattered: the cycle disarms a LIVE entry whose allowance is spent, and a promotion built
  from the earlier read puts the entry back at LIVE. The next cycle disarms it again, so the window
  was one cycle, and with nothing armed LIVE today it had no effect yet. Thomas took it as a
  behaviour change of its own (D-3, 2026-09-30), apart from the refactor PRs.
- **What changed:**
  - `pool_state.load_active_pool_with_digest` reads the file once and returns the pool and the
    digest of that text (`"absent"` when there is no file);
  - `pool_state.install_active_pool` takes `expected_digest`. Under the lock every pool writer
    already takes, it hashes the file again and, if it is not the one named, raises
    `STRATEGY_POOL_CHANGED` and writes nothing;
  - the promotion door reads once, in both its modes, and hands that digest to the install. A
    refusal prints `BLOCKED STRATEGY_POOL_CHANGED: …` like the door's other refusals.
- **What the operator sees:** a promotion that raced a writer is refused instead of installed. Run
  the same command again. The approval is verified and never consumed, and nothing was written or
  ledgered, so the retry is the identical command, judged on the pool as it now is.
- **What did not change:** the lock is not held while the door works, so the cycle's writers never
  wait on an operator script. `import_crypto_history --activate-pool` names no digest: it installs
  the pool another file carries and builds nothing from the one on disk. Every other caller of the
  install (tests) is as before.
- **Tests:**
  - the PR-05 characterization test pinned the old behaviour on purpose. It is flipped and renamed:
    the install built from an earlier read is refused and the disarm stands;
  - the store: an install with the current digest goes through; one built from an earlier read is
    refused and leaves the file byte for byte as the writer in between left it (a disarm, another
    install, a removal); the first install on a machine with no pool goes through; the digest names
    the content, not the write; an install that names no digest replaces as before;
  - the door, in replace mode and keep-active mode: a disarm made at a fixed point between the
    door's read and its install refuses the promotion, leaves the disarm, ledgers nothing; the same
    command then goes through; with nothing in between it goes through as before;
  - a roster of every call of the install in `runtime/` and `scripts/`: it hands over a digest or
    it is the one named wholesale replace.
- **Evidence:** eight mutants of the fix (the comparison dropped or inverted, the digest not handed
  over, and so on) are each killed by a named test. The record comparison over 6,707 lane tests
  differs in one record besides the scheduler's measured durations and a concurrency test's race
  winner: the flipped test's pool file, which ends disarmed where it used to end LIVE. The full
  suite passes.
- **Takes effect:** at the operator's door, once the next candidate is deployed.
