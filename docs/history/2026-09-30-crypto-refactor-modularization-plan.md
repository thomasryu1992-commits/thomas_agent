# Crypto refactor and modularization plan, mapped onto what PR7 already enforces (proposal)

- **What was read** (read-only, `main` 97b3dd98): the lane's layer map, every exchange-write path in
  `runtime/` and `scripts/`, the halt semantics, state ownership, the id chain and duplicated logic.
- **What was found:**
  - No new stop condition. There are two exchange-write adapters, both env-gated, and one autonomous
    chokepoint; SOFT/HARD halts match the directive.
  - S-1 (decided 2026-09-15): `/kill`·`/pause` drop the scheduler fire, so settlement and protection
    stop too.
  - S-2: the venue-contract refresh builds the mainnet order-capable adapter inside the trading fire
    (only `/order/test` and GETs are called).
  - S-3: the promotion door reads the pool outside its lock and replaces the whole file, so a cycle
    demotion or LIVE disarm in between can be overwritten.
  - Report/outcome modules import `live_route`/`live_execution` for constants. Two streak
    implementations differ. Live outcomes do not reach the lifecycle or the C6 report, although the
    `live_settlement` docstring says they do.
- **What was delivered:** `docs/proposals/CRYPTO_REFACTOR_AND_MODULARIZATION_PLAN_V0.1.md` (DRAFT).
  It sets out a phased plan whose first PRs add tests only (an exchange-write caller roster, a script
  capability roster, no egress imports from analytics), and asks for decisions D-1 to D-5.
- **Deliberately not done:** no code, no module moves. The sub-package tree is not recommended,
  because the layer test already enforces it.
- **Decided the same day** (Thomas, as recommended): no sub-package move (D-1). PR-02 and PR-03
  start (D-2). S-3's CAS is a separate behaviour-change PR (D-3). S-1 stays as decided on
  2026-09-15 (D-4). S-2 is covered by PR-02's roster first, with the read-only adapter protocol
  left for the execution-layer step (D-5).
- **PR-06 measured the same day** (read-only, a copy of the live ledger: 26 readable closes, 20 of
  them probes). The two streak rules agree on the populations each one actually reads (strategy
  lineages: 0 of 6 closes differ). They differ only where probes are in the population (pool-wide:
  15 of 26). Net R and stored R are identical on every live row. Recorded in the proposal's §K-1.
  §E-2 also gained the `tunables → testnet_execution` pair that PR-03's test found.
