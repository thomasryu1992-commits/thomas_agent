# Crypto refactor and modularization plan closed

- **What changed:** `CRYPTO_REFACTOR_AND_MODULARIZATION_PLAN_V0.1.md` moves from `DECIDED` to `IMPLEMENTED`.
  Every item in §S, §J, §L, §M, §N, §O and §K was built and merged (#1049–#1106). Two follow-ups outside
  the plan cleared re-exports (#1107, #1108). Every PR that changed runtime was deployed as a candidate,
  and its first fire was observed; the last was candidate-1108.
- **Status line:** the running progress log is replaced by a closing summary that lists each item's PR.
  The detail stays in each step's history fragment.
- **Not build items, so they do not keep the plan open:**
  - The one signed testnet cycle for PR-13–15 runs after the SIGNED_TESTNET transition (Thomas).
  - §Q is paused under D3 until 2027-03-22.
- **What is left by design:** 65 re-exports that `test_mvp_runtime_crypto_reexport_roster.py` pins. They are
  contracts that tests name, plus `factory`'s template builders. The `pool` facade also stays, by design.
