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
- **Review corrections (Thomas 2026-10-12, same PR):**
  - **D-10 isolation needs evidence, not flags.** The Dockerfile declares
    `VOLUME ["/app/.runtime_governance_state"]`, chowned to uid 10001. With no explicit mount, Docker makes an
    anonymous volume there that uid 10001 can write and `--read-only` does not cover.
    - §2.5 defines the D-10 ISOLATION EVIDENCE: 13 items read from `docker inspect` on a created, not yet
      started container:
      - image, source hashes;
      - user, privileged, capabilities;
      - network mode none;
      - exactly two tmpfs mounts, no bind or volume;
      - tmpfs root, environment names only;
      - read-only rootfs, closed output, exit code.
    - It also lists the failure conditions and forbids any fallback to the default state path.
    - VALIDATION_PATH_DESIGN_PROPOSED (a design exists) is kept apart from VALIDATION_PATH_UNRESOLVED (the
      blocker, unchanged). Meeting D-1–D-10 does not approve the door. Policy, implementation and execution
      each need their own approval.
  - **Candidate tag timing:** the tag is decided after the final deploy baseline and its implementation PRs
    are fixed.
    - `candidate-1217` is now a PROVISIONAL_RECOMMENDATION.
    - Six identification fields are recorded apart: approved SHA, build SHA, tag, image id, source hashes,
      rollback id.
    - The "four image files" scope is recomputed if a self-test PR lands.
  - **Rollback has three states:**
    - ROLLBACK_TARGET_VERIFIED: the target is identified.
    - ROLLBACK_PROCEDURE_REVIEWED: the procedure was reviewed, not run.
    - ROLLBACK_FUNCTIONAL_VERIFIED: a separately approved real restore succeeded. It is not recommended.
    - TARGET + PROCEDURE is never recorded as FUNCTIONAL, and the state stays ROLLBACK_NOT_VERIFIED until
      Thomas decides.
  - **Gate 2 fail-closed:** a partly met Gate 2 blocks the deploy (GATE_2_NOT_FULLY_SATISFIED →
    DEPLOYMENT_HOLD). This reads the existing condition fail-closed and grants no new permission.
    - The readiness doc's "PENDING or FAILED" wording is left for a later docs change.
- **Unchanged:** GLOBAL_GUARD_INSTALLATION_VERIFIED, GLOBAL_GUARD_HOOK_WIRING_VERIFIED,
  DIRECT_TOLD_FILENAME_PROBE_NOT_PERFORMED, VALIDATION_PATH_UNRESOLVED, ROLLBACK_NOT_VERIFIED,
  CANDIDATE_TAG_DECISION_REQUIRED (candidate-1217 provisional), DEPLOYMENT_HOLD, NOT_GRANTED.
