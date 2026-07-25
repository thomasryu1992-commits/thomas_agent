# LP4 Order Adapter — Design Record v0.1

**Status:** Design record. **No code exists yet; nothing here enables trading.** This is the
proposal to review before LP4 is written.
**Owner:** Thomas
**Authority:** None. `governance/GOVERNANCE_POLICY.yaml` owns every rule; live trading stays
OFF (`financial_transaction_execution_implemented: false`, `ORDER_PATH_IMPLEMENTED = False`).
Sequenced after: verification (`CRYPTO_LIVE_EXECUTION_VERIFICATION_V0.1.md`), the budget
(steps 6/6b), and the P5 role (step 7). Depends decisions: `LIVE_EXECUTION_GOVERNANCE_V0.1.md`.

**Claude does not run this, does not handle real keys, and does not enable live trading.**
Every operational step — the order key, the grant, the confirmation phrase, placing a canary,
activating the role — is Thomas's.

## What LP4 is

The **order adapter**: the first and only code that can send an order to a venue. It takes one
**guard-approved** order intent, submits it to Binance USDT-M Futures, **reconciles** the
result against the venue, and returns the `exchange_order_id` + `reconcile_status` + any
mismatches. That is the whole job.

```
guard-PASSED intent (LP3)
        │
        ▼
   [LP4] re-run the final guard on live facts ── not PASS ─▶ refuse, nothing sent
        │ PASS
        ▼
   signed POST /fapi/v1/order  (MARKET, reduceOnly from intent, newClientOrderId = client_order_id)
        │
        ▼
   reconcile: GET /fapi/v1/order by origClientOrderId ── compare symbol/side/filled qty/status
        │
        ▼
   (exchange_order_id, reconcile_status, mismatches)
        │
        ├─▶ live_promotion.build_canary_order_record(...)   # LP6 evidence (clean iff RECONCILED & no mismatch)
        └─▶ live_pnl.build_live_outcome_record(...)          # LP2 ledger, on the eventual close
```

The surrounding contracts already pin most of LP4's shape:

- **Input** — `live_order.build_live_order_intent` already fixes `order_type_exchange: "MARKET"`,
  the `side` (with the reduceOnly side-flip), the `quantity`, `reduce_only`, and the
  `client_order_id` (idempotency). LP4 invents none of these.
- **Output** — `live_promotion.build_canary_order_record(reconcile_status, exchange_order_id,
  client_order_id, problems, …)` derives `clean = (reconcile_status == RECONCILED and not
  problems)`. **The caller cannot assert cleanliness**; LP4 supplies the reconcile facts and the
  record decides.

## What LP4 is NOT

- It does **not** size an order or decide to trade (the strategy/cycle and the guard do).
- It does **not** manage positions, route cycles, or track open exposure over time (**LP5**).
- It does **not** open a position on the close path — a reduceOnly order can only shrink.
- It holds **no** strategy logic and makes **no** model call.

## The gate — one grant, DryRun by default

LP4's real submitter is constructed **only** behind the `live_trading` safety-flag grant via
`safety_gate.select_gated` (the established chokepoint used by `live_pnl`/`live_order`/
`account`). The default is an inert **`DryRunOrderAdapter`** that computes the request and
returns a synthetic "would-submit" result **without opening a socket**. `MVP_LIVE_TRADING=real`
alone fails closed without a valid local grant. Deleting the grant is a live revocation
(`assert_authorization` re-reads at every egress).

This POST is **the first WRITE network egress in the repository.** Every read so far
(`account`, `market_data`) is GET-only. That is the weight of this module.

## The order key — separate from the account key (decision 2026-07-25)

LP4 uses its **own** order-capable API key, distinct from `account.py`'s read-only key:

- New env: **`MVP_LIVE_ORDER_API_KEY` / `MVP_LIVE_ORDER_API_SECRET`** (names only ever
  reported; values never logged, audited, or recorded — the `account.py` posture).
- Operator provisions it: **Futures enabled, withdrawals and internal transfer DISABLED,
  IP-whitelisted.** Keep it distinct from the read-only account key so the read and the trade
  have separate blast radii and can be revoked independently.
- Own host allowlist (`fapi.binance.com`), enforced at construction like `account.ALLOWED_ACCOUNT_HOSTS`.
- The signed URL carries the signature in its query string, so a transport failure raises a
  **deliberately generic** error — the URL never reaches a message, log, or record.

The `live_trading` **grant** (network + filesystem) authorizes the capability; these env vars
carry the key. Both are required; either alone fails closed.

## Rehearsal — straight to mainnet canary, no testnet (decision 2026-07-25)

No signed-testnet boundary is added (consistent with `CRYPTO_LIVE_EXECUTION_V0.1.md` "not
ported"). The mock/paper path already covers pre-live rehearsal of the *decision* logic; LP4's
first live validation is the **canary itself** — a deliberate, small, real mainnet order.

- *Rejected alternative:* a Binance-testnet mode in LP4 (submit against the testnet host with
  a fake-money key) would exercise the sign/submit/reconcile path against a real exchange before
  any real money. It was **not** chosen: a second venue with its own host, key, and counters is
  more surface than it earns, and the canary — one small real order, closed immediately — is a
  more honest first validation than testnet fills that never risk anything. If wanted later it
  is an additive mode, not a rewrite.
- *Consequence, stated plainly:* the very first exercise of LP4's real submit path moves real
  money (a small canary). This raises the bar on the reconcile logic (below) and on canary size
  being genuinely small.

## Submit

Signed `POST /fapi/v1/order` on the order host, HMAC-SHA256 over the query, `X-MBX-APIKEY`
header. Body from the intent: `symbol`, `side`, `type=MARKET`, `quantity`, `reduceOnly` set
**from `intent.reduce_only`** (the structural boundary the close guard relies on — LP4 must set
it faithfully or a "close" could open), `newClientOrderId = intent.client_order_id`. Assumes
one-way position mode on USDT-M futures (documented; a hedge-mode `positionSide` is a later
addition if the account uses it).

**Scope note added 2026-07-25 (LP5 decision 1).** LP5 places a venue-side protective bracket
(SL + TP, reduceOnly) at entry, so **increment 2 must also support conditional order types** —
`STOP_MARKET` / `TAKE_PROFIT_MARKET` with a `stopPrice` — not MARKET alone. `build_order_request`
currently asserts `order_type_exchange == "MARKET"`; that assertion widens to an allowlist of the
supported types. The venue's exact conditional-order semantics are a **must-verify-at-implementation**
item (see `LP5_POSITION_KERNEL_DESIGN_V0.1.md`), not something to implement from memory.

## Reconcile — the real-money safety core

A submit is confirmed by a **read**, never assumed from the POST response:

- After the POST (or after any ambiguous outcome), query `GET /fapi/v1/order` by
  `origClientOrderId` and compare against the intent: **symbol, side, filled quantity, and
  `status == FILLED`**. Any divergence is a mismatch; mismatches make the canary record
  **not clean** (and never silently pass).
- **Reconcile-first, never blind-retry.** If the POST times out (sent, no response), LP4 does
  **not** resubmit — it queries by `client_order_id` first. Because `newClientOrderId` is the
  idempotency key, even a resubmit that slipped through is rejected by the venue as a duplicate,
  so a retry can never open a second position; but the rule is reconcile-first regardless.
- A `reconcile_status` vocabulary: `RECONCILED` (found + matches), `MISMATCH` (found + diverges),
  `NOT_FOUND` (venue has no such order — the submit did not land), `UNRECONCILABLE` (the query
  itself failed — fail closed: treated as not clean, surfaced for the operator).

## Failure modes

| Outcome | LP4 behavior |
|---|---|
| Submit rejected (4xx: margin, filters, key perms) | No position; record the refusal + reason; not clean |
| Submit ambiguous (timeout / transport) | **Reconcile by client_order_id first**; decide from the venue, never resubmit blind |
| Filled + reconciled, no mismatch | `RECONCILED`; feed the canary record (clean) / eventual PnL outcome |
| Filled but a field diverges | `MISMATCH`; not clean; surfaced — a wrong-size/side fill is a stop-everything signal |
| Reconcile query fails | `UNRECONCILABLE`; fail closed; not clean |

## LP4 re-runs the final guard (belt-and-suspenders)

LP4 never trusts a caller's "it's approved." It gathers the live facts itself — the grant state,
the runtime kill switch (`ControlStore`), the daily-loss breaker (`live_risk_snapshot`), the
clean-canary count (`live_promotion.promotion_status`), today's submission count (`count_today`),
and the registered budget (`resolve_live_order_limits`) — runs `evaluate_live_order_guard`, and
**refuses to open a socket unless it PASSes**. The close path runs `evaluate_live_close_guard`
(grant + phrase + reduceOnly) instead.

## The canary placement path

Canaries come before any autonomous order (≥3 clean, currently 0). A deliberate, single-order
operator CLI — proposed `scripts/place_canary_order.py` — places exactly one small canary
through LP4 and records the reconciled result to the canary registry (LP6). Not wired into the
autonomous cycle; the cycle (via LP5) is a separate, later step and only after the canaries pass.

## What still stands between LP4 and an autonomous live order

Merging LP4 (behind the gate, DryRun default) still authorizes nothing. Before a real order:

1. The `live_trading` grant minted (Thomas, TTL-capped, revocable) + the order key provisioned.
2. The confirmation phrase set (distinct per capability).
3. A valid registered trading budget (step 6/6b).
4. The runtime kill switch ACTIVE.
5. The final guard PASSes on live facts.
6. **≥ 3 clean canary orders** (currently 0).
7. The `execution.live_trader` role **activated** (candidate → routable) — a separate `ROLE_GOVERNANCE` approval.
8. **LP5** (position kernel + cycle routing) for anything beyond a hand-placed canary.

## Open engineering decisions (deferred to implementation, not blockers)

- Reconcile **retry count + timeout** (how long to keep querying an ambiguous submit before
  declaring `UNRECONCILABLE`).
- **Partial fill** policy for a MARKET order (rare on liquid symbols at canary size; likely
  treat a partial as a mismatch and surface it).
- Whether the canary CLI closes the position automatically or leaves the close to the operator
  (the checklist says "close each canary on the venue afterwards" — lean operator-closed).

## Implementation shape

**Increment 1 — the skeleton (done 2026-07-25):** `runtime/mvp_runtime/crypto/live_execution.py`
— `OrderAdapter` protocol, `DryRunOrderAdapter` (default, inert), `BinanceFuturesOrderAdapter`
(real, gated — a **stub** that re-asserts the grant then raises `ORDER_PATH_NOT_IMPLEMENTED`),
`select_order_adapter` via `select_gated`; `build_order_request` (reduceOnly from intent, MARKET
only), `reconcile_order` (the comparison + the `reconcile_status` vocabulary), and
`submit_and_reconcile` (guard-approval belt-and-suspenders; reconcile-first, never blind-retry).
Fully tested with zero network. **No governance change:** because the real send is a stub, no
code can send an order, so `ORDER_PATH_IMPLEMENTED` and
`financial_transaction_execution_implemented` stay OFF — honestly.

**Increment 2a — the real transport (done 2026-07-25):** `BinanceFuturesOrderAdapter.submit` /
`fetch_order` implemented — signed `POST /fapi/v1/order` and the reconcile `GET`, mirroring
`account.py`'s credential posture (key read at call time from the separate `MVP_LIVE_ORDER_*` env,
names-only errors, the signed URL never logged), plus the conditional order types the LP5 bracket
needs. **Every venue semantic was verified against the venue's own New Order / Query Order /
error-code references rather than written from memory**, which corrected three assumptions:

| Verified fact | Consequence in the code |
|---|---|
| `closePosition=true` is mutually exclusive with **both** `quantity` and `reduceOnly` | a close-all bracket leg sends neither; combining them is refused before the wire |
| `newClientOrderId` must match `^[\.A-Z\:/a-z0-9_-]{1,36}$` | validated locally (the existing generator already complies) |
| code **-2013** = "Order does not exist."; **-4116** = duplicate client id | -2013 is a truthful `NOT_FOUND`; any *other* query rejection raises so it becomes `UNRECONCILABLE`; -4116 means the original already landed, so reconcile decides |
| conditional auto-cancel on position close is **not documented** | LP5 must explicitly cancel the surviving leg — no reliance on auto-cancel |

The reconcile result now also carries the **actual fill** (`avg_price`, `executed_qty`,
`cum_quote`), reported as `None` rather than `0.0` when unknown, because that is what LP5 must
compute `realized_pnl_usdt` from. **The governance/readiness flags deliberately stay OFF in 2a**,
so the readiness board cannot report READY and no autonomous path routes here.

**Increment 2b — the threshold crossing (pending, its own PR):**
- `scripts/place_canary_order.py`: the deliberate single-canary operator path (gathers live
  facts, runs the final guard, calls `submit_and_reconcile`, records the reconciled canary).
- Governance: flip `financial_transaction_execution_implemented: false → true` (the code now
  exists; the grant stays the per-machine safety flag), leave every `runtime_effect`/`cutover`
  flag false, regenerate both replay bundles, and set `ORDER_PATH_IMPLEMENTED = True` in
  lockstep. All per the `LIVE_EXECUTION_GOVERNANCE_V0.1.md` "mechanics" section. This is the
  real-money-adjacent step and its own deliberate decision.
