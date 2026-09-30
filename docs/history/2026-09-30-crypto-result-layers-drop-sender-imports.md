# The result layers no longer import a module that sends (crypto refactor plan PR-04)

- **What was there:** four imports from outcome/report into sending modules, named as exceptions by
  PR-03:
  - `live_readiness` imported `live_route` for its status and codes, the arming check and the halt
    wording;
  - `route_watch` imported `live_route` for `ROUTE_INCIDENT`;
  - `live_promotion` imported `live_execution` for `RECONCILED`;
  - `tunables` imported `testnet_execution` for the two testnet caps.
- **What moved** (each old module re-exports the same object, pinned by
  `test_the_names_pr04_moved_are_the_same_objects_where_they_were`):
  - the route's status values and two reason codes → `vocabulary`;
  - `verify_live_arm` and its `LIVE_ARM_*` codes → `promotion`, beside `live_arm_problem`. The route
    calls its own binding, so a patch on `live_route.verify_live_arm` still reaches it;
  - `halt_advice` → core `control`, beside `halt_description` and the grants it reads;
  - the testnet caps → `testnet_evidence`, their tunables owner with them;
  - `RECONCILED` now comes from `order_request` directly.
  `EGRESS_EXCEPTIONS` is empty and pinned empty.
- **The pool-facade ratchet** (`tests/test_mvp_runtime_crypto_pool_facade.py`, #944): the moved
  `verify_live_arm` reads `live_arm_unsound` from `live_tier` and the artifact field from
  `strategy_artifact`, their owners, not through `pool`. `live_route`'s two reads through `pool`
  are gone from the baseline, which only shrinks.
- **Docstrings corrected** (they had gone out of date, per the plan's §B-3 and §J-2):
  - `live_execution` was "the only code that sends", but brackets and the testnet adapter send too;
  - `live_route` said the cycle imports nothing else from the live stack, but it also imports
    `live_pnl`;
  - `live_settlement` said the lifecycle and C6 read live rows, but they read the paper ledger only.
- **Behaviour change:** none. The moves are identity-preserving; the affected suites pass with Core
  activated (0 skipped). The change ships with the next candidate; no deploy of its own is needed.
