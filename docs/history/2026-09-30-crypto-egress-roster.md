# One roster for every call that can put a write on an exchange (crypto refactor plan PR-02)

- **What was missing:** the lane's egress pins were per module (the chokepoint's importers, the
  venue contract and resting-orders "places nothing" pins, the order-counter door list), and
  `scripts/` sat outside the layer test. Nothing answered "which code, anywhere in `runtime/` or
  `scripts/`, can send, cancel or validate an order". A new script taking an adapter from
  `select_order_adapter()` and calling `submit` would have passed every existing check.
- **What was delivered:** `tests/test_mvp_runtime_crypto_egress_roster.py`. It reads calls, not
  imports and not file names, and pins them as rosters that only shrink:
  - the adapter's egress methods (`submit`, `cancel_order`, `validate_order`), keyed by receiver;
  - the doors that reach them, resolved through each file's imports, aliases included;
  - the signed requests whose verb is not GET;
  - the files that import `hmac`;
  - the files that name an exchange key's environment variable (§M-2d).
  A synthetic tree proves that the scanner catches each kind of breach. Injecting an `adapter.submit`
  into `venue_contract` and an `adapter.cancel_order` into `list_resting_orders` both fail it.
- **Decisions it records, not makes:** the venue-contract refresh holding the mainnet order-key
  adapter (S-2, Thomas D-5 2026-09-30), and the probe, signed-testnet and emergency-close scripts as
  doors outside `live_route`.
- **Runtime change:** none. Tests only, so there is nothing to deploy.
