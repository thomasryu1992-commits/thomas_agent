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
- **Second review (2026-10-11):**
  - R-1:
    - The frozen-state property depends on the fire order. The state-only writers save without verifying the
      ledger, and only `summary()` failing first keeps them from running. PR-D must keep that order, with a test.
    - A recurrence is caught by the told file's lifecycle, not by the digest.
    - Missing or unreadable state alerts with a marker.
    - The digest stays in the LOCAL_ONLY told file.
    - The key carries a format marker for the switch to PR-A.
  - R-2:
    - The first note overstated the gap: C7 §6 already keys detect-only alerts on the frozen checkpoint. What it
      leaves open is the non-exception input.
    - That input must not use the state digest, which changes every fire while writes continue.
    - Its reason_code is DECISION_REQUIRED.
  - R-3:
    - A never-had checkpoint and a lost one cannot be told apart from the state alone.
    - Option (a) leaves this to PR-B's UNCHECKED. Option (b) adds a `checkpoint_introduced` ledger line in PR-A.
      This is DECISION_REQUIRED.
  - A PR-D start checklist is added (§2.7).
- **What it deliberately does not do:**
  - It writes no code.
  - It does not change the C7 order or decisions.
  - It touches no real ledger, state file or venue API.
