# C6b b8 checks cover the capture door: seven refusals (N-16) and one round trip

- **What:** `scripts/ops/c6b_checks.py` now checks the capture door from #1267 as part of b8.
  - **N-16 (refused):** the `claude` account cannot do any of the following:
    - list `req/` (opening the directory);
    - create in `out/`;
    - enter `work/`;
    - append to the root-owned door program `/usr/local/sbin/thomas-capture-door` or either unit file;
    - create in `/usr/local/sbin`.
  - **capture (reached):** one request the door must refuse, `https://localhost/`, comes back within 60 seconds
    as `문지기 거절: non_global_address`.
- **Why a refusal probe, not a real capture:** one round trip proves the request drop (rename into `req/`), the
  path unit, the validation and the read-back of `out/`. It runs no Docker container and makes no outside fetch,
  so b8 stays a read-only check that does not depend on any outside site being up. A real capture was verified
  separately at install: support.claude.com, 18 s, 1200×900 with the `thomas-capture` record.
- **Measured live (2026-10-20, as `claude`, this branch):** `C6B_CHECKS=PASS (49 rows, 0 failed)`. That is the
  earlier 41 rows plus 8 new ones. All seven N-16 rows were `denied` and the probe returned the expected refusal.
- **Skip ceiling:** `tests/test_c6b_checks.py` is Linux-only, so all of it skips on Windows. It gained 6 tests
  (24 → 30 collected), so the win32 ceiling goes from 658 to 664. The number is counted, not guessed: every item
  in this module skips on win32. The next CI run's `Skipped tests on win32` line confirms it.
