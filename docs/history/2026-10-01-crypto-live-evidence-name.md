# `live_promotion` is now `live_evidence` (crypto refactor plan L-2.3)

- **Why:** the module stopped promoting anything on 2026-09-15 (PR1r), when the canary door and the
  promotion gate went. What it does is read evidence. It verifies and renders the frozen canary
  registry, and it shows whether each closed live trade recorded the venue's numbers. The plan
  named this its one naming debt.
- **What changed:** `runtime/mvp_runtime/crypto/live_promotion.py`'s content moved to `live_evidence.py`
  with only its docstring and one comment changed. The old file's history stays at the old path. The two scripts and the tests that
  used it now import `live_evidence`. The current-tense comments and the operator-facing docs (the
  live execution and pipeline contracts, `REMAINING_WORK.md`) give the new command.
  `DIAGNOSTIC_CODE_INDEX.md` now attributes the three `CANARY_HISTORY_*` codes to the new file.
- **The old name still works:** `live_promotion.py` is an alias. Imported, it maps itself in
  `sys.modules` to the same module object, so `live_promotion is live_evidence` under every import
  form, and a patch through either name reaches the same functions. Run as
  `python -m runtime.mvp_runtime.crypto.live_promotion`, it runs the board. The layer map lists
  both names in outcome. The layers test pins the identity.
