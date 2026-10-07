# The holdings board reads Toss Securities; the KIS feed is removed whole

- **What this delivers:** the Toss feed that replaces KIS (Thomas 2026-10-07, appendix B of
  `docs/proposals/MULTI_ASSET_EXPANSION_V0.1.md`).
  - `holdings/toss_account.py` is the feed, gated by `MVP_TOSS_ACCOUNT=toss` with provider
    `toss_account`. It reads `TOSS_CLIENT_ID`, `TOSS_CLIENT_SECRET` and an optional `TOSS_ACCOUNT_SEQ`.
  - `holdings/model.py` holds the broker-neutral shapes the board and the store read.
  - `holdings/kis_account.py` and the six KIS compose variables are deleted. KIS was never enabled
    on this machine: it had no `.env` entry and no schedule.
- **What carries over unchanged:** the aggregate boundary, the snapshot store, the
  `holdings_refresh` maintenance kind, the `/holdings` verb, the dormant `holdings_status` read, and
  the key living on `scheduler-maint` only. The stored record moves to `holdings_snapshot.v1`:
  `server` becomes `broker`, `usd_cash_krw` is added, and `partial` now means a part is missing
  rather than "always".
- **Shaped by Toss's docs (read 2026-10-07):**
  - **One valid token per client.** Issuing a token revokes the previous one, so scheduler-maint
    is the one issuer. A `token-revoked` or `expired-token` 401 is one reissue and one retry, and a
    second 401 is a degraded read. The terminal script's default became the stored snapshot (no
    call), and `--full` is the live read and says it revoked the lane's token.
  - **Per-currency subtotals.** Toss's overview amounts are not converted between currencies. The
    overseas KRW figures are the `usd` subtotal times Toss's display mid-rate, computed here. The rate
    never leaves the feed. When it is unreadable, the overseas side is absent, not guessed.
  - **Cash is `cashBuyingPower`,** which is not a deposit balance, and the board says so.
  - **No read-only scope** (`scopes: {}`), so read-only stays enforced in code: the token endpoint
    and four reads, no order method.
- **Unverified until the first live read:** Toss has no demo server. The fixtures are Toss's own
  `openapi.json` examples.
