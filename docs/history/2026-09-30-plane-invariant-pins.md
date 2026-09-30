# Two invariants pinned: who can sign a venue write, and how far the acting planes reach into research code

- **What was found:** the crypto improvement gap analysis (`docs/proposals/CRYPTO_SYSTEM_IMPROVEMENT_GAP_ANALYSIS_V0.1.md`
  §1 and §16) found two gaps in the tests.
  - The chokepoint tests pin who may call the order adapter. Nothing pinned which modules can sign a
    write to a venue.
  - The layer test pins that research code cannot import order code. It allows the other direction, and
    nothing said how far that goes.
- **What was delivered:**
  - `test_only_the_two_order_adapters_can_sign_a_write`. Across `runtime/` and `scripts/`, the modules
    that import `hmac` are exactly four, each named with its reason. Only `live_execution` and
    `testnet_execution` pass a write method (POST/PUT/PATCH/DELETE). A mutant signer under
    `crypto/` fails it.
  - `test_the_acting_planes_reach_research_code_only_where_named`. Every risk, execution and
    reconciliation module, and `live_route`, is checked for research modules (factory, robustness,
    proposer, proposer_cli, null_control, data_review) it reaches transitively, function-local imports
    included. Two do today, both through a decision module:
    - `live_route` via `promotion.live_arm_problem`;
    - `breaker_watch` via `pool` → `candidate_ranking`.
    The pin names both and only shrinks.
- **Deliberately not done:** no import moved. Taking `live_arm_problem` off `promotion` is a live-path
  change and belongs to the PR7 discipline.
