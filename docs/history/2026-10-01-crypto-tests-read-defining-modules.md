# Crypto tests read moved names where they are defined (crypto refactor plan PR-16 follow-up)

- **What changed:** after PR-16, 111 re-exports outside `pool` were read only by tests. For 95 of
  them, the tests now read the name from the module that defines it. That is 326 reads and
  from-imports across 35 test files, rewritten by AST position, so no string or comment was
  touched. The re-exports then had no caller and are gone: 96 import names in `factory`,
  `live_order`, `cycle`, `paper`, `live_pnl`, `live_readiness`, `live_execution` and `live_route`.
  The 96th, `paper.STOP_BEYOND_LIQUIDATION`, lost its last caller in PR-16 itself. No code moved.
- **What stays:**
  - the four names a module declares in `__all__` (`cycle.retention_cohort`, `live_leg`'s two
    close reasons and `leg_status_line`);
  - the template builders, which tests look up by a computed name;
  - eight names whose reading test file binds the defining module's name to a local (`cost`,
    `order_request`, `state`);
  - every re-export that runtime or a script reads.
- **Checked:** no test patches a moved name on the re-exporting module. The patches tests make on
  the defining modules were logged for the 37 touched files before and after the rewrite, and the
  two logs are identical. The two such patches, `template_space.NUMERIC_FEATURES` and
  `generator.mutate_params`, are not followed by a read through the facade in the same test.
  `tests/test_mvp_runtime_crypto_reexport_roster.py` now pins 121 re-exports in 9 modules.
