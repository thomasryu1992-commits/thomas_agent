# A readiness note maps C7 PR-D and PR-A onto main's code, and finds one seam between them

- **What this PR adds:** `docs/proposals/HOLDINGS_C7_PR_D_PR_A_READINESS_V0.1.md` (DRAFT). It is a preparation note,
  not a design; the authority stays with `HOLDINGS_LEDGER_CHECKPOINT_V0.1.md`.
- **Baseline it records (main `b1e48637`, 2026-10-11):**
  - C7 has no implementation on main yet. Only PR-0 (#1210) exists.
  - `ledger_state_consistency` checks only the chain, the state parse and the cutover.
  - 596 holdings-related tests pass on synthetic fixtures. The 15 C7 tests pass, five of them pinning defects.
  - The note lists which PR flips each defect test.
- **Code facts behind the change points:**
  - `store.refresh` keeps the exception class name instead of the reason code, in three `except` blocks.
  - On a broken chain, `summary()` raises before `update_readiness` runs. That is why C7-D sends nothing.
  - `scheduler.py` already delivers three alert kinds with "mark only after delivery", so PR-D adds a fourth.
  - Every ledger write goes through `_commit`. The three state-only writers save the state whole, so they keep a
    new key.
  - `readiness()` reads the ledger before the state, which is the order PR-A reverses.
- **The seam (R-2):** PR-A detects only and raises nothing. Yet C7 §10 expects a BLOCKED alert from it for C7-4
  and C7-6, so PR-D's alert needs a non-exception input from the start.
- **Review items:**
  - R-1: PR-D's alert key before checkpoints exist. The note recommends a digest of the frozen state file.
  - R-3: `ledger_state_consistency` before the first checkpoint. The note recommends keeping today's reading.
- **What it deliberately does not do:**
  - It writes no code.
  - It does not change the C7 order or decisions.
  - It touches no real ledger, state file or venue API.
