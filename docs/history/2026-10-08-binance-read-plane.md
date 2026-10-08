# H1-b: the venue read plane signs with its own read-only key, apart from the write plane

- **What this PR changes:**
  - `crypto/account.py`:
    - two credential planes behind the one `MVP_ACCOUNT_FEED` gate. `PLANE_READ` signs with
      `BINANCE_READ_API_KEY` / `_SECRET`; `PLANE_TRADING` signs with `BINANCE_ACCOUNT_*`. A required
      `plane` argument on `select_account_feed`, `read_account` and the feed, with no default. An unknown
      plane is refused (`ACCOUNT_PLANE_UNKNOWN`).
    - No fallback between planes.
    - A GET path allowlist (`READ_PATHS`): any other path is refused before a socket opens
      (`ACCOUNT_PATH_REFUSED`).
    - Two resting-order reads, `open_orders` and `algo_open_orders`.
  - **Observation callers on `PLANE_READ`:** the account snapshot refresh, the dashboard, the readiness
    board (its "account configured" row now checks the read pair), the fee measurement, the account
    CLI, and `scripts/list_resting_orders.py`. That script no longer uses the live write gate's venue
    reader.
  - **Write-plane callers on `PLANE_TRADING`, unchanged:** the live leg, the emergency close and the
    slippage probe.
  - `holdings/binance_wallet.py` reads the read pair.
  - `docker-compose.yml`: the read pair goes to `scheduler` only.
  - Tests: `tests/test_mvp_runtime_crypto_read_plane.py` (new) plus the rosters, passthrough and
    resting-orders tests. Docs: DEPLOYMENT, RUNTIME_SAFETY_INVARIANTS, appendix C, the diagnostic index.
- **Why:** R1 closed the live write gate, and that blinded `list_resting_orders`. Its reads went through
  the write plane's venue reader, signed with the order key. The account snapshot signed its reads with
  the account key, which is the order key. Observation must not need execution permission, and closing
  execution must not blind monitoring (Thomas, H1-b, 2026-10-08).
- **Why the write plane keeps its own reads:** the emergency close and the live leg read the account
  through the same feed. Moving them to the read key would make an emergency close depend on a key the
  write plane does not own. H1-b changes no write-plane behaviour.
- **Readiness semantics (added before merge, Thomas 2026-10-08):**
  - The board's `account_visibility` row became `venue_read_visibility`, observation only. Its detail
    says that READ visibility PASS does not mean the trading account plane is ready.
  - A new `trading_account_credentials` row shows the write plane's account pair as `<set>`/`<unset>`.
  - In the process that holds the live opt-in, account readiness requires two things:
    - the trading pair configured, else `TRADING_ACCOUNT_NOT_CONFIGURED`;
    - an actual GET with that pair's key succeeding, else `TRADING_ACCOUNT_UNREADABLE`.
  - A healthy read plane, or a key that is merely `<set>`, cannot stand in for that read.
  - With the live gate closed, the board never signs with the trading key.
  - No row grants execution permission.
  - `account.py` keeps the venue's raw algo rows rather than importing the execution layer's
    translation: the layer test forbids market → execution, and the one reader of those rows reads both
    vocabularies.
- **Separation status:**
  - credential namespace separation: COMPLETE
  - fallback removal: COMPLETE
  - read/write interface separation: COMPLETE
  - process/container isolation: DEFERRED. The scheduler owns both the read-only key and the trading
    credentials (temporary architecture); a `portfolio-reader` service is the candidate.
- **First-live probe limits:**
  - read pair only, GET only, no trading-key fallback;
  - `MVP_BINANCE_WALLET` stays OFF;
  - no subscribe, redeem, transfer, order or cancel;
  - per-symbol quantities and values stay terminal-only and are not persisted or sent to Hermes;
  - only per-endpoint success or error codes are recorded;
  - the wallet is not merged into the combined NAV because of the probe (that is H2's).
- **Follow-up debt:** the Hermes operator skill `thomas-ops/SKILL.md` still names `account_visibility`.
  Renaming it is a separate change (MANIFEST, host install, `/new`).
- **Placement:** the read pair reaches `scheduler` only. The snapshot refresh rides a risk-lane fire;
  the resting-orders script runs by `docker exec` there. `scheduler-maint` needs the pair only for the
  wallet read, which stays off until H2–H4. The scheduler therefore holds the read key and the trading
  keys together. The recorded separation candidate is to move the snapshot refresh and the
  resting-orders read to a maintenance kind on `scheduler-maint`, or to a reader service.
- **Secrets:** Thomas issued the key ("Enable Reading" only) and wrote it into `.env`. Claude checked
  only `<set>/<unset>`. No value was read, compared or fingerprinted. Whether the key differs from the
  trading key is Thomas's check, in Binance API Management.
- **Negative checks run:** three changes were made deliberately and each turned the named tests red:
  - a fallback from the read pair to the account pair;
  - `scheduler-maint` receiving the read pair and a trading key;
  - `list_resting_orders` on the trading plane.
- **Rollback:** revert this PR and redeploy `rollback-pre-<N>`. An unused read-only key is still a
  credential: Thomas revokes it in Binance and removes its two lines from `.env`.
- **Not done here:** H1-c (the testnet egress stage guard) and H2 (valuation completeness). The first
  live read-only probe is recorded after the deploy.
- **First live read-only probe (2026-10-08, after deploying `candidate-1178`):**
  - Machine state: execution stage `PAPER`, live gate `CLOSED` (`MVP_LIVE_TRADING` empty; the write plane
    selects `DryRunOrderAdapter`), armed strategies 0.
  - The read plane read the venue with the key Thomas issued ("Enable Reading" only). The table records
    the plane, the method and the outcome only; no symbol, quantity, balance or price was recorded.

    | Endpoint | Key | Method | Result |
    |---|---|---|---|
    | futures account `/fapi/v2/account` (income windows read) | READ_KEY | GET | PASS |
    | positions (same response) | READ_KEY | GET | PASS (open positions 0) |
    | resting orders `/fapi/v1/openOrders` | READ_KEY | GET | PASS (0) |
    | resting algo orders `/fapi/v1/openAlgoOrders` | READ_KEY | GET | PASS (0) |
    | spot account `/api/v3/account` | READ_KEY | GET | PASS |
    | Simple Earn flexible `/sapi/v1/simple-earn/flexible/position` | READ_KEY | GET | PASS |
    | Simple Earn locked `/sapi/v1/simple-earn/locked/position` | READ_KEY | GET | PASS |

  - `list_resting_orders --json` answered `asked_the_venue: true` with the live gate closed.
  - The trading credential plane is configured (`<set>`). Its probe was NOT_ATTEMPTED because the live
    gate is closed: the board signs with the trading key only in a process that holds the live opt-in.
  - The spot and Earn reads ran in one `docker exec -e MVP_BINANCE_WALLET=binance_wallet` process. The
    wallet gate was empty in both services' environments and absent from `.env`, before and after.
  - A successful probe is not an activation. The wallet is not in the combined NAV; that belongs to H2.
    The spot wallet and flexible Earn hold assets the board does not yet count, so H2's completeness
    rules (unpriced assets, truncation) apply to a non-empty wallet.
