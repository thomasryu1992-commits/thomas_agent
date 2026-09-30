# Four comments the refactor reviews found wrong (crypto refactor plan, after PR-04 to PR-13)

- **What this is:** the independent review of the six merged refactor commits (PR-04, PR-07 to
  PR-11) found no behaviour change and four places where the text no longer matched the code. This
  fixes the text. No code changes.
- **The four:**
  - `control.halt_advice`'s docstring had a sentence with its subject missing;
  - `pool.py` said `live_route` reads `pool.ARTIFACT_SHA256_FIELD`. It stopped when `verify_live_arm`
    moved to `promotion` (PR-04). Two tests still read it there, so the re-export stays; the review
    called it dead, which it is not;
  - a test comment in `test_mvp_runtime_crypto_readiness_state.py` named two readers of the arm
    verdict and there are three;
  - `test_every_named_egress_exception_still_exists_name_by_name` has nothing to check while
    `EGRESS_EXCEPTIONS` is empty. It now says so, and says which test holds the line.
- **The plan's status line** names PR-12 and PR-13 by number, records that PR-13 is deployed and its
  first fire observed, and records what happened to the one evidence item still missing: the signed
  testnet cycle was run by the operator on 2026-09-30 and refused by its guard, because the machine's
  execution stage is PAPER and the testnet opt-in is off. That refusal is the design. The cycle is
  owed until the stage moves to SIGNED_TESTNET.
