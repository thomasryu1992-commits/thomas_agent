# H1-a: scheduler-maint no longer receives a trading-capable Binance credential

- **What this PR changes:**
  - `docker-compose.yml`: drops `BINANCE_ACCOUNT_API_KEY` / `_SECRET` from `scheduler-maint`.
  - `tests/test_deployment_env_passthrough.py`: the pin is inverted. The lane receives nothing named
    `BINANCE_ACCOUNT_*`, `MVP_LIVE_ORDER_*` or `MVP_TESTNET_ORDER_*`, and no value draws one from `.env`
    under another name. The secret-ownership matrix narrows to `scheduler`.
  - A fail-closed test for the wallet read: gate on, no key, no socket, total incomplete.
  - `docs/DEPLOYMENT.md` and appendix C of `MULTI_ASSET_EXPANSION_V0.1.md`.
- **Why:** the H0 review (2026-10-07) compared fingerprints and found the account key on `scheduler-maint`
  equal to the live order key. No value was printed. Option A had put that key on the maintenance lane
  for the Binance spot + Simple Earn read. The read was never switched on, so the lane held an
  order-capable key with no consumer. Read-only code does not make a key read-only.
- **Approval:** Thomas, 2026-10-07 (H0 decision, H1-a approved). This reverses option A from the same day.
- **What did not change:** the risk lane (`scheduler`) keeps its account and order credentials until the
  later credential redesign. `holdings/binance_wallet.py` still reads the account key names; on
  `scheduler-maint` they are now absent, so the feed fails closed with `NO_API_KEY`.
  `MVP_BINANCE_WALLET` stays off.
- **Before the wallet may be switched on:**
  - H1-b: a dedicated venue read-only key, which Thomas issues with "Enable Reading" only and an IP
    restriction, under its own variable names;
  - H2: valuation completeness;
  - H3: the privacy boundary;
  - H4: source coherence.
- **Left stale on purpose:** the module docstring of `holdings/binance_wallet.py` and one comment in
  `tests/test_mvp_runtime_crypto_egress_roster.py` still describe option A. H1-b changes that module's key
  names and corrects both. This PR changes one safety property.
