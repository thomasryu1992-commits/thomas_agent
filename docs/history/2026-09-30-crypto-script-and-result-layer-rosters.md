# Operator scripts get classes, and the result layers may not import a sender (crypto refactor plan PR-03)

- **What was missing:**
  - The 31 scripts that import the crypto lane sit outside the layer test. Three of them send
    orders, and nothing said which ones or checked it.
  - The layer order lets outcome and report import execution. They did: `live_readiness` and
    `route_watch` import `live_route`, `live_promotion` imports `live_execution`, and `tunables`
    imports `testnet_execution`. Every one of those imports is only for constants or read-only
    checks, but the directive (§22) says analytics must not import exchange-write modules at all.
- **What was delivered:**
  - `tests/test_mvp_runtime_crypto_script_roster.py` gives every lane script one of five classes
    (EXCHANGE_WRITE, ORDER_KEY_READ, STATE_WRITE, RESEARCH_WRITE, READ). Each claim is checked
    against the other rosters rather than trusted:
    - the exchange classes are derived from PR-02's egress roster;
    - no writer guarded by `test_state_guard_covers_state_writing_clis` may be READ;
    - no READ script calls the host-root write guard.
  - `tests/test_mvp_runtime_crypto_layers.py` gains the rule that outcome and report import none of
    `live_execution`, `testnet_execution`, `live_leg` and `live_route`. The four current pairs are
    named exceptions, each pinned to the names it takes and to PR-04, the step that removes it. The
    list only shrinks.
- **Proved by mutation:** relabelling `emergency_close` and a guarded writer, and adding an import of
  `live_route` to `dashboard`, each fail (reverted).
- **Runtime change:** none. Tests only, so there is nothing to deploy.
