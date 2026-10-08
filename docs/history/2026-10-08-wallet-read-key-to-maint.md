# Wallet activation, step 2: scheduler-maint receives the read-only pair

- **What this PR changes:**
  - `docker-compose.yml`: `scheduler-maint` receives `BINANCE_READ_API_KEY` / `_SECRET`, the dedicated
    read-only pair from H1-b. It is the only Binance credential that lane holds.
  - `tests/test_deployment_env_passthrough.py`:
    - The ownership matrix names the two lanes.
    - A new test pins that the read pair is the maintenance lane's only venue credential.
    - The H1-a refusal of every trading-capable prefix is unchanged.
  - Docs: DEPLOYMENT secret table, RUNTIME_SAFETY_INVARIANTS exposure row.
- **Why:** this is the wallet activation Thomas approved on 2026-10-08, to run once H2, H3-min and
  H4-min were deployed. The holdings fire runs on scheduler-maint and reads the spot wallet and Simple
  Earn, so the read pair has to be there. Thomas chose this placement over having the scheduler write a
  file for maint to read.
- **Preflight before this PR:** one read-only `docker exec`, with the wallet gate set in that process
  only. It found no unpriced asset, no invalid row, no truncated Earn list, Earn read, and no warnings.
  Only counts and flags were printed.
- **What it does not do:** the gate itself (`MVP_BINANCE_WALLET`) is set in `.env` after this deploy,
  as a separate step. No trading-capable credential reaches the maintenance lane.
