# C7 PR-D deployment blockers: a validation design and operator approval packet (design only)

- **What this PR adds** (Thomas 2026-10-12: design and docs only):
  - `docs/proposals/HOLDINGS_C7_PR_D_DEPLOY_VALIDATION_DESIGN_V0.1.md` designs how each remaining blocker could
    be cleared. Nothing was run.
  - `STATUS.md` is regenerated.
- **Gate 2 (the told-name direct check): option A recommended.**
  - Option A adds three told-name DENY cases (Bash, Read, Monitor on a synthetic `/tmp` path) and one bare
    `holdings_snapshot.json` ALLOW to the guard kit's `test_guard.py`.
  - That test runs the global settings' wrapper against the installed hook, exactly as Claude Code calls it.
  - Option B (the repo's guard tests against the installed copy) needs a test-code change to retarget the
    hook. It also checks the repo settings, not the global wiring, so it adds little once the bytes are
    identical.
  - The kit is not under git, so the kit change is preceded by a copy under a new name.
- **Image validation: a moving of the inline import is not a path.**
  - The packet sets ten criteria (D-1–D-10) under which a committed self-test entry point would be a formal
    door rather than the guard's known "script on disk" gap:
    - no input surface;
    - its own temporary root only, synthetic fixtures only;
    - no network and no delivery;
    - closed PASS/FAIL output;
    - pinned by repo tests;
    - runtime untouched;
    - isolated runs on the candidate only.
  - Precedents are the board's one table and `scripts/self_test_core_release_flow.py`.
  - Whether such a door fits the guard's intent is Thomas's policy call, so the state stays
    VALIDATION_PATH_UNRESOLVED. No guard exception or relaxation is proposed.
- **Candidate and rollback:**
  - The chain to record: approved SHA → deploy tree → candidate id → in-image source hashes → rollback tag id
    = the eight services' pre-deploy image id.
  - A metadata-level rollback check (R-1, R-2) is recommended over a live restore drill. The rollback target
    is the image the eight services run healthy now.
  - The state stays ROLLBACK_NOT_VERIFIED until Thomas accepts that level.
- **Unchanged:** GLOBAL_GUARD_INSTALLATION_VERIFIED, GLOBAL_GUARD_HOOK_WIRING_VERIFIED,
  DIRECT_TOLD_FILENAME_PROBE_NOT_PERFORMED, VALIDATION_PATH_UNRESOLVED, ROLLBACK_NOT_VERIFIED,
  CANDIDATE_TAG_DECISION_REQUIRED (candidate-1217 recommended), DEPLOYMENT_HOLD, NOT_GRANTED.
