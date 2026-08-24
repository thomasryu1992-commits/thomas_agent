# LP4 Order Adapter — Design Record v0.1

**Status:** IMPLEMENTED — increments 1, 2a and 2b all merged 2026-07-25. This is the design
that was reviewed *before* the code, kept as the decision trail; it is not a description of
current behaviour. For what the code does now read `CRYPTO_LIVE_EXECUTION_V0.1.md` and
`runtime/mvp_runtime/crypto/live_execution.py`.
**Owner:** Thomas
**Authority:** None. `governance/GOVERNANCE_POLICY.yaml` owns every rule. **An order path now
exists** (`financial_transaction_execution_implemented: true`, `ORDER_PATH_IMPLEMENTED = True`,
both flipped 2026-07-25 in lockstep with increment 2b). That says the *code* exists, not that
this machine may act: `financial_executor_enabled` stays false, and acting still needs the
`MVP_LIVE_TRADING=real` opt-in, the confirmation phrase, a registered budget, both kill
switches, and — for the autonomous path — the canary evidence.
Sequenced after: verification (`CRYPTO_LIVE_EXECUTION_VERIFICATION_V0.1.md`), the budget
(steps 6/6b), and the P5 role (step 7). Depends decisions: `LIVE_EXECUTION_GOVERNANCE_V0.1.md`.

**Claude does not run this, does not handle real keys, and does not enable live trading.**
Every operational step — the order key, the live-trading opt-in, the confirmation phrase,
placing a canary, activating the role — is Thomas's.

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

## The gate — the env opt-in, DryRun by default

**Changed 2026-07-28 (Thomas).** This section read *"one grant, DryRun by default"* and
described the submitter as built behind a per-machine `live_trading` safety-flag grant via
`safety_gate.select_gated`, with `MVP_LIVE_TRADING=real` failing closed without a valid local
grant and grant deletion serving as live revocation. **The grant is gone.** Step 1 of the
approval packet below was annotated at the time; this section was not, and read as current
until then. The reason for the removal is in `CRYPTO_LIVE_EXECUTION_V0.1.md` ("One env var is
the whole switch") and the 2026-07-28 entry of `docs/BUILD_HISTORY.md`: a grant expiring while
a position was open shut the CLOSE path too.

LP4's real submitter is constructed **only** behind the live-trading environment opt-in via
`safety_gate.select_env_gated` (`live_execution.build_order_adapter`), the chokepoint the rest
of the live surface moved onto with it. The default is an inert **`DryRunOrderAdapter`** that
computes the request and returns a synthetic "would-submit" result **without opening a socket**
— unchanged, and still what an unset or unrecognized value selects. `MVP_LIVE_TRADING=real`
alone now builds the capable adapter: no second factor, no expiry, no per-machine record.

**Revocation changed with it, and that is the half an operator has to know.** Deleting a grant
file was immediate. Clearing the variable is not — `assert_authorization` re-reads the env at
every egress, but a running process's environment does not change under it, so unsetting it
stops the *next* process and needs a container restart. It also shuts the CLOSE path, which can
strand an open position. The halt of record is the file-based `console_cli kill`, which lands
on a running scheduler at its next guard and is exempt on that path.

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

The live-trading **environment opt-in** authorizes the capability; these env vars carry the key.
Both are required; either alone fails closed. (This read "the `live_trading` grant (network +
filesystem)" until 2026-07-28 — see the gate section above. The shape of the first requirement
changed; that both are needed did not.)

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

**Scope note added 2026-07-28 (maker take-profit).** The allowlist gained `LIMIT`, for the
take-profit leg only. A `TAKE_PROFIT_MARKET` triggers into a market order and therefore always
pays taker plus adverse slippage to reach a price the market had to come to anyway; a resting
`LIMIT` at the same price earns the maker rate instead. `build_order_request` requires a
positive `price` and an explicit `time_in_force` on a LIMIT — the venue makes both mandatory,
and defaulting a time-in-force would be the adapter deciding how long real money rests at a
price. **The entry stays MARKET**, and the stop stays `STOP_MARKET`: a limit entry would fill
only when price comes back to it, which selects against the momentum strategies in the pool,
and a limit stop cannot fill in a gap. Only the target leg can rest without a risk trade.

**Scope note added 2026-08-02 (what re-opening the entry leg would take).** A limit entry is
**deferred, not rejected**, and the fee prize is real — roughly 6 bps of a ~16 bps round trip,
the single largest arithmetic lever in the cost structure (measured 2026-08-02: taker fee plus
slippage is **94%** of all cost at every timeframe, funding 0.2–4%). It is written down here
because the paragraph above records only the refusal, so a reader arriving later sees a closed
door and no handle.

**Provenance, stated because it changes how much these bind.** Thomas's decision of 2026-07-28
is the paragraph above: ship the maker take-profit leg, entry stays MARKET, revisit later. The
four items below are **engineering preconditions derived from that decision's stated reasoning**
— they are not additional constraints Thomas set, and none of them has been ratified. Read them
as "what would have to be true for the question to be worth re-opening", and correct the list
rather than working around it.

| # | Precondition | State on 2026-08-02 |
|---|---|---|
| 1 | The PM1 observation window has drained | **5.7 of 14 days** — started 2026-07-27T09:49Z, ends ~2026-08-10 |
| 2 | The backtest has an explicit pessimistic fill model — filled only if the bar trades **through** the limit by ≥1 tick, never on a touch | **Built 2026-08-10** — `crypto/limit_entry.py` (`limit_entry_fill`): fills only on a trade-through of ≥1 tick, a touch is never a fill, the fill price is the limit with no improvement credited, and an unreadable bar refuses rather than reading as no-fill. **Zero callers by design**, pinned by `test_nothing_in_the_runtime_imports_the_limit_entry_model_yet` — wiring it into scoring or a live leg is the act (4) gates |
| 3 | The cancel timeout is **bar-based**, not wall-clock, or backtest and live diverge on how long an entry waits | **Built 2026-08-10** — the same model's `timeout_bars`: the wait is counted in bars of the traded timeframe, a rule-decided cancel (`cancel_timeout`) is distinguished from a replay window that ran out (`bars_exhausted`), and the number itself deliberately has **no default** — it is an undecided decision, not a constant to inherit |
| 4 | Gated **per strategy family**, starting where a limit entry is aligned rather than adverse (`mean_reversion_*`, `funding_fade_*`) | Families exist in `factory.TEMPLATES`; **62 candidates, 0 ROBUST** (31 FRAGILE, 31 PROVISIONAL, and only 4 at the current cost basis). Re-measured 2026-08-10: **140 candidates, 0 CONFIRMED** (holdout 124 INSUFFICIENT / 16 CONTRADICTED) — still closed, and still the door |

**(4) is the one that does not drain with time, and it is the load-bearing one.** The refusal
above rests on the pool being momentum/breakout, where a non-fill correlates with the winner —
the selection bias can exceed the fee saving, and OHLCV cannot score it honestly because a touch
is not a fill. That premise has if anything hardened: every lineage that reached ROBUST or the
promotable board on 2026-08-02 is momentum or breakout (`volatility_expansion_short`,
`breakdown_short`, `breakout+macd_momentum`, `xs_momentum_short`, `session_trend_short`), while
the families a limit entry would suit have never produced a ROBUST candidate at any cost basis.
So the staged rollout has no first stage: this opens when those families earn one, not when a
clock runs out.

A limit **stop** is separately ruled out and is not part of this — a plain limit at the stop
price sits on the wrong side of the book and fills at entry, and a stop-limit can miss in a gap.
Only the target leg can rest without trading risk for the fee.

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

LP4 never trusts a caller's "it's approved." It gathers the live facts itself — the opt-in state,
the runtime kill switch (`ControlStore`), the daily-loss breaker (`live_risk_snapshot`), the
clean-canary count (`live_promotion.promotion_status`), today's submission count (`count_today`),
and the registered budget (`resolve_live_order_limits`) — runs `evaluate_live_order_guard`, and
**refuses to open a socket unless it PASSes**. The close path runs `evaluate_live_close_guard`
(opt-in + phrase + reduceOnly) instead — the three the close path keeps, in
`evaluate_live_close_guard`'s own words.

## The canary placement path

Canaries come before any autonomous order (≥3 clean, currently 0). A deliberate, single-order
operator CLI — proposed `scripts/place_canary_order.py` — places exactly one small canary
through LP4 and records the reconciled result to the canary registry (LP6). Not wired into the
autonomous cycle; the cycle (via LP5) is a separate, later step and only after the canaries pass.

## What still stands between LP4 and an autonomous live order

Merging LP4 (behind the gate, DryRun default) still authorizes nothing. Before a real order:

1. `MVP_LIVE_TRADING=real` set by Thomas + the order key provisioned. (This step read "the
   `live_trading` grant minted, TTL-capped, revocable" until 2026-07-28, when Thomas removed
   the grant — there is no TTL and no per-machine record behind this step any more.)
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

**Increment 2b — the threshold crossing (done 2026-07-25):**

- **`scripts/place_canary_order.py`** — the deliberate single-canary operator path: resolves the
  registered budget, reads every live fact (kill switch, breaker, canary count, daily count, and
  open exposure **from the venue**), runs the guard in canary mode, sends exactly one order,
  reconciles, and records the result. Entry-only; the operator closes the position on the venue.
- **Two design gaps this increment had to close first:**
  1. **The chicken-and-egg.** The guard requires ≥ 3 clean canaries — but a canary is what *earns*
     that evidence, so the first one was unplaceable. The promotion gate now does not apply when
     `canary=True`. Implemented as a mode on the *same* guard rather than a second guard, so a
     check added later cannot land on only one path; it defaults to `False` (fail-closed).
  2. **One phrase per capability was aspirational.** The docs said the canary phrase is distinct
     from the autonomous one, but only one phrase existed. Added
     `CANARY_CONFIRMATION_PHRASE` / `MVP_LIVE_CANARY_CONFIRMATION`, so the autonomous phrase
     cannot authorize a canary and the canary phrase cannot authorize autonomous trading (both
     directions asserted).
- **The exposure fail-open is closed on this path**: the canary refuses with
  `NO_ACCOUNT_VISIBILITY` rather than passing the guard's `0.0` default when the account cannot be
  read, because an unknown exposure cannot honor the exposure cap.
- **Governance flip, in lockstep:** `financial_transaction_execution_implemented: false → true`
  and `ORDER_PATH_IMPLEMENTED = False → True`, with a test asserting the two agree so they cannot
  drift. `financial_executor_enabled` stays **false and byte-for-byte untouched** (a test asserts
  that too), and every `runtime_effect` / `cutover.grants_*` flag stays false — so the frozen
  kernel's preflight is untouched. Both replay bundles regenerated (the policy SHA is pinned in
  four places per bundle plus the bundle fingerprint).
- **What READY now means, said out loud.** The readiness board no longer reports "no order path
  exists"; it states that a path exists, that READY therefore means a real order can be placed,
  and that autonomous trading still needs LP5 — so a row of green ticks cannot read as harmless. This is the
  real-money-adjacent step and its own deliberate decision.
