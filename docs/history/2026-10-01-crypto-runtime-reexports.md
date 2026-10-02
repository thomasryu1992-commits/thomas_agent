# Runtime and scripts read moved names from where they are defined (crypto refactor plan PR-16, third follow-up)

- **What changed:** runtime modules and operator scripts read 51 names through a module they had
  moved out of. They now read them from the module that defines them:
  - from-imports: only the module changes, and each caller binds the same names as before.
    Patches on the caller (`live_route.count_today`, the probe script's `bracket_breaker_status`)
    still reach.
  - attribute reads (`factory.HOLDOUT_FRACTION`, `paper.advance_holding`,
    `cycle.attach_mining_legs` in the scheduler) now name the defining module. No test patches
    any of these names.
  - Tests that read the same names through the old module moved with them, so the re-export had
    no caller left.
- **Removed re-exports:** 50. All of `live_order`'s, `live_execution`'s and `live_evidence`'s are
  gone, along with `factory`'s replay constants and `known_features`, `paper`'s three from
  `trade_plan`, two from `live_pnl`, and `cycle`'s two from `feed_assembly`. The roster now pins
  65 re-exports in 6 modules.
- **Side effect, pinned:** `judgement_fingerprint`, `forward_confirmation` and `candidate_ranking`
  now import `backtest` instead of `factory`. So `live_route` and `breaker_watch` no longer
  reach `factory`, `template_space` or `generator`, even transitively. They reach only
  `backtest` and `robustness`, and the layer pin shrinks to match.
- **Kept, because a test names them as a contract:**
  - `live_leg`'s eight from `live_leg_results`. PR-13 pins that `live_leg` offers every name of
    that module, and they are in its `__all__`.
  - `live_readiness.readiness_data`. The readiness pin lists it by name.
  - `live_pnl.state_dir` and the two `STATE_REL`. The one-state-root pin reads them.
- **Pins whose sample name was a removed re-export:** the four split pins
  (`order_request`, `feed_assembly`, `backtest`, `live_order_stores`) and the roster's scan check
  now sample a name the module still offers.
