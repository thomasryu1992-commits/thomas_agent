# Pre-Order Risk Snapshot v0.1 — the one gate before an order that opens exposure

**Status:** enforced since crypto PR2b. `live_execution.submit_and_reconcile` refuses every order
that is not reduce-only unless it carries an approved snapshot that has already been recorded, and,
since PR2c-1, one judged at most 60 seconds before the send.
**Owner:** Thomas. **Authority:** this contract describes the behaviour. The authoritative sources are
`runtime/mvp_runtime/crypto/pre_order_gate.py`, `schemas/pre_order_risk_snapshot.v0.1.schema.json`
and their tests.
**Decided:** 2026-09-16/17 (record: `/root/thomas_crypto_refactor_2026-09-15/decisions-2026-09-15.md`).
- Thomas's directive: every real order passes one gate immediately before submission. The gate
  re-verifies everything, even when earlier gates passed. The order references the gate's
  immutable snapshot by hash.
- Decision 17: the approved profile is a composite of existing records.
- Decision 19: snapshots are stored in a per-venue append-only store.
- Decision 20: the signed testnet entry passes the same gate.
- Decision 24 (PR2c-1): the autonomous entry keeps its bar-close plan and is checked against the
  market's price at the moment of the decision. The caps are judged at the higher of the two prices.

## 1. What passes through it

| Door | Purpose | What the door re-derives before the gate seals |
|---|---|---|
| Autonomous leg (`live_route` → `live_leg.execute_live_entry`) | `autonomous` | `live_entry.plan_live_entry`, re-run on the decision's facts narrowed by the gate's re-read (below). This covers every door by name and the final guard's checks. The order must be the one those facts decide, and the bracket the leg will place must be the one they price (`bracket_matches_intent`). The facts include the freshness doors below. |
| Slippage probe (`scripts/run_slippage_probe.py --fire`) | `probe` | `probe.gate_probe_order`: the plan and its cell, the account (readable and at most 60 seconds old at the gate), the symbol being free, the four breakers (the daily loss, the risk guard, the bracket breaker and, since PR2d-1, the API error breaker), the priced ceiling, and the order rebuilt and judged by the live guard in canary mode, on facts narrowed by the same re-read. |
| Signed testnet cycle, entry only (`scripts/run_signed_testnet_cycle.py`) | `signed_testnet` | `testnet_execution.gate_testnet_order`: the testnet guard re-run, and the order rebuilt from the cycle's inputs. |

The gate never judges reduce-only orders: closes, brackets and cancels. The venue enforces that
they cannot add exposure, and a gate that could refuse them could trap a position.

**The re-read (PR2c-2a).** Between a door's first read and its gate, another writer can halt or
disarm the runtime, re-register the budget or the risk limits, demote the stage or the live tier,
spend the day's orders, or trip a breaker. Both entry doors read those facts again right
before the gate (`live_route.reread_entry_facts`; the probe does not read the pool, which authorizes
none of its orders) and fold them in only to narrow (`live_entry.narrow_guard_facts`,
`narrow_entry_facts`):

| Fact | Folded in as |
|---|---|
| execution stage | the fresh read, on the same `now` |
| runtime may trade (`trading_allowed`) | the first AND the fresh |
| caps | the stricter of the two reads: each cap the lower, a manual kill engaged on either (`live_entry.stricter_limits`). Both doors reserve the day's slot against them |
| today's realized loss | breached if either read says so. The re-read judges the realized figure the door already read against the fresh limit |
| a valid budget backs the order | the first AND the fresh; a legacy window must hold at both `now` and the decision's `clock`, and both must resolve the same record |
| symbol allowlist | what both reads share |
| live tier (autonomous) | what both reads share; the arming approval only if both reads name the same one, and then verified (§3) |
| orders spent today | the fresh count |
| bracket breaker | the higher streak, tripped if either read says so |
| API error breaker (PR2d-1) | not clear unless both reads say clear, and not clear while the breaker cannot record the pass |
| risk limits | the verdict stands only if the limits in force at both `now` and `clock` are the record it was judged on (`LIVE_ENTRY_RISK_LIMITS_CHANGED`; `LIVE_ENTRY_RISK_LIMITS_UNNAMED` for a verdict that names none, `LIVE_ENTRY_RISK_LIMITS_UNRESOLVED` when nothing resolves, or the resolver's own code) |

- The account, the book, the filters and the market price are not re-read: they are what the order
  was sized on, and a second read would move the size by noise.
- A fact that improved cannot widen what is sent. A cap raised since the first read does not count.
  The autonomous gate re-derives the decision on the narrowed facts, so an order the narrowed caps
  would size differently fails `intent_matches_decision`. The probe's gate re-runs its guard on its
  narrowed facts.
- A re-read that fails refuses the entry (`LIVE_PRE_ORDER_REREAD_FAILED`,
  `PROBE_PRE_ORDER_REREAD_FAILED`) and never halts the fan-out.
- The route records what the re-read found as `live_pre_order_reread`.
- **The probe's plan store is compare-and-set.** Every write names the stored plan it replaces
  (`probe.write_plan(..., expected_sha256=)`), and a store another door rewrote since is refused
  (`PROBE_PLAN_CHANGED`). An `--abandon` beside a `--fire` is therefore not undone by the fire's cell
  claim, and the fire sends nothing.
  - **Before the send**, a refused write refuses the fire. If the order was refused before the venue
    and the cell cannot be returned, the symbol still goes back and the refusal names the cell.
  - **After the send**, the position matters more than the plan. A refused or failed write is
    printed (`PLAN      : NOT recorded`), the fire still supervises what it sent to its end, and it
    exits `BLOCKED` with `PROBE_PLAN_NOT_RECORDED`.
  - **An OPEN cell is not finished while its fire may still be sending.** Until the book says what
    the venue holds, that fire keeps its symbol claimed under the cell's entry id. While that claim
    stands, or while the entry marks cannot be read, neither `--abandon` nor another `--fire`
    resolves the cell (`PROBE_CELL_OPEN`).

**Freshness of an autonomous entry (PR2c-1, decisions 18 and 24).** The plan's size, stop and
target stay the bar close's; a 1d plan can be most of a day old. The decision is judged at `clock`,
the wall clock read after every other fact, not at the fire's start (`now`).

| Door | Refuses when | Code |
|---|---|---|
| `account_fresh` | the account was read more than 60 seconds before `clock`, after it, or not at all | `LIVE_ENTRY_ACCOUNT_STALE` |
| `reference_price_fresh` | the market price (the last closed 1m candle, `market_data.read_reference_quote`) is synthetic, absent, unreadable, or its candle closed more than 300 seconds before `clock` | `LIVE_ENTRY_REFERENCE_PRICE_UNUSABLE` |
| `price_within_divergence` | that price is more than 50 bps from the plan's entry | `LIVE_ENTRY_PRICE_DIVERGED` |
| `price_between_protective_legs` | that price is at or past the rounded stop or target | `LIVE_ENTRY_PRICE_BEYOND_BRACKET` |

- The first three accumulate with the other cheap doors. The fourth runs right after the bracket is
  priced, before the liquidation and economics doors.
- **The caps at the higher price.** Sizing takes the per-order cap at the higher of the entry and the
  market price (`size_live_order(cap_price=...)`). The final guard judges the per-order and
  open-exposure caps on the quantity at that price (`evaluate_live_order_guard(reference_price=...)`).
  The order itself still names its bar close and its own notional.
- The route reads the market price only for a context with a plan. A read that fails in any way
  refuses the entry and never halts the fan-out. The reader's own reason (`REFERENCE_PRICE_*`) is
  recorded on the cycle record beside the decision's refusal.
- A price is unreadable when its candle closes more than one bar after `clock`: the forming candle
  is the latest a venue has.
- **The cost of the read.** A failed read is not memoized. Each context of the symbol with a plan
  asks again, each up to its call timeout, on the sequential fan-out that also carries later
  contexts' settle and protect steps. A rate-limit refusal ends every market read for the fire.
- The limits are indexed in `crypto/tunables.py`: `REFERENCE_PRICE_MAX_AGE_SECONDS`,
  `MAX_ACCOUNT_AGE_SECONDS` and `MAX_REFERENCE_DIVERGENCE_BPS`.

**The optional data of an autonomous entry (PR2d-2, decision 28).** Every optional leg of the cycle
degrades rather than blocks. For money that is wrong in two ways:
- **A failed leg leaves its columns None.** A strategy reading them can neither fire nor veto, so two
  strategies on the context that would have disagreed become one that enters alone.
- **A feed that stopped updating keeps its last reading.** The as-of join carries it forward with no
  age limit.

The cycle judges the context (`cycle.optional_data_health`). The door `optional_data_healthy`
refuses the whole context, whatever the plan reads:

| Refuses when | Code |
|---|---|
| any optional leg degraded this cycle: funding, mark, index, premium index, liquidations, open interest, the higher timeframe, the reference symbol, the cross-section | `LIVE_ENTRY_OPTIONAL_DATA_DEGRADED` |
| a feed's reading at the bar is older than its bound: funding 16 hours, the daily liquidation and open-interest series 48 hours, positioning 3 hours | `LIVE_ENTRY_OPTIONAL_DATA_STALE` |
| the leg was handed no account of it, or one it cannot read | `LIVE_ENTRY_OPTIONAL_DATA_UNKNOWN` |

- **The age is measured from the bar's open**, the instant the as-of join keys on, not from `clock`.
  A 1d bar opens a day before it is decided on.
- **Which readings count.** A feed's reading at the bar is its last event at or before the bar's
  open. Positioning counts only a time its two paired series both carry. A feed that holds events
  but none readable at or before the bar is stale.
- **What is not judged.** A feed the snapshot does not carry (not configured, or nothing
  accumulated yet) is not judged. A feed present and empty failed its fetch, and its degrade code
  says so. The same-grid legs join exactly and need no bound.
- **What the gate seals.** The gate seals the whole assessment in `facts.optional_data`.
- **What the records show.** The route records `live_optional_data`. Every cycle record carries
  `optional_data_stale` and the feeds' ages (`optional_data_ages`), gate open or not.
- **Not gated:** paper, the counterfactual shadow, the probe (it reads candles only) and the testnet
  cycle.
- **The bounds** are indexed in `crypto/tunables.py`: `FUNDING_MAX_AGE_HOURS`,
  `DAILY_SERIES_MAX_AGE_HOURS` and `POSITIONING_MAX_AGE_HOURS`.

## 2. The gate's own checks

The gate adds these to the door's checks:

- `door_checks_present`: a door that re-derived nothing has verified nothing. A door that hands a
  malformed check has not shown what it verified: a check that is not a mapping, names nothing,
  carries an `ok` that is not a boolean, or takes one of the gate's own names. A handed check passes
  only when its `ok` is `true` itself.
- `intent_opens_exposure`: the order opens exposure in its own direction. A reduce-only or
  close-position order is refused here, and so is a side that does not open the direction (`BUY`
  for `LONG`, `SELL` for `SHORT`).
- `intent_identity`: `idempotency_key`, `client_order_id` and `order_intent_id` must follow from the
  intent's own fields. The bar and its timeframe are in that identity (decision 16).
- `lineage_complete`, by purpose:
  - autonomous: strategy, candidate, rule hash, generation, timeframe, bar, intent id;
  - probe: strategy, batch, cell, intent id;
  - testnet: strategy, cycle, intent id.
- `approved_profile_complete`: the profile must be whole and built for this purpose.
- `venue_matches_purpose`: autonomous and probe orders go to `binance_futures`, signed testnet orders
  to `binance_futures_testnet`. A testnet cycle's caps authorize nothing on mainnet.
- `decision_time_recorded` (PR2c-1): the door said when it judged its facts (`decided_at`, the wall
  clock, in exactly the form this runtime writes, `YYYY-MM-DDThh:mm:ssZ`). The gate seals it into `facts.decided_at`, over any value the door
  put there. `created_at` stays the fire's start.

`approved` is true only when every check passed.

**What the snapshot binds of the order** (`INTENT_BOUND_FIELDS`, hashed into `intent_fingerprint`):
the order's identity, every field `live_execution.build_order_request` turns into the venue request,
the prices and the lineage. A structural test keeps the request fields and the bound fields in step.

## 3. The approved profile (decision 17)

The profile names records that already authorize trading. Nothing new is registered.

- **The execution stage record:** it must bind, and it must carry its id, hash and approval.
- **The registered budget** (autonomous and probe): it must be valid, with its id and hash.
- **The risk limits in force** (autonomous and probe): either the code-pinned defaults, or a
  registered set that names its id and hash.
- **The authority for this kind of order:**
  - autonomous: `live_arm`, the approval the strategy was armed LIVE under. The promotion door
    writes it to `live_tier_approval_id` beside the tier, and the disarm door removes it.
    **The route verifies it at the gate (PR2c-2b)** (`live_route.verify_live_arm`).
    - **The entry must be sound on both pool reads** (`pool.live_arm_unsound`; an unsound entry
      names no approval in `pool.live_arm_approvals`):
      - the spec it trades must be the rule its `strategy_rule_hash` label names
        (`LIVE_ARM_SPEC_NOT_ITS_RULE`). The router trades the spec, and the approval is checked
        against the label;
      - it must not carry the disarm door's trace (`live_tier_updated_at`,
        `LIVE_ARM_REARMED_OUTSIDE_THE_DOOR`). The promotion door installs every entry fresh, so a
        LIVE entry with that trace was put back in the tier by hand.
    - **The fresh entry must arm the lineage the plan was made from** (`LIVE_ARM_ENTRY_CHANGED`).
    - **The approval store must hold the record the entry names**, and
      `promotion.live_arm_problem` must accept it:

    | The record must | Otherwise |
    |---|---|
    | exist | `LIVE_ARM_APPROVAL_MISSING` |
    | be APPROVED | `LIVE_ARM_APPROVAL_NOT_APPROVED` |
    | name Thomas, VERIFIED on the private control channel | `LIVE_ARM_APPROVER_UNVERIFIED` |
    | approve a strategy-pool promotion into the live target, at the LIVE tier | `LIVE_ARM_APPROVAL_NOT_AN_ARM` |
    | still fingerprint to its recorded value, under the id that fingerprint derives | `LIVE_ARM_APPROVAL_ALTERED` |
    | list the entry's candidate id and rule hash | `LIVE_ARM_APPROVAL_OTHER_CANDIDATE` |
    | have been answered at or before the entry's `promoted_at`, and expire after it | `LIVE_ARM_INSTALLED_OUTSIDE_APPROVAL` |

    The expiry is the earlier of the approval's own and the one its fingerprinted snapshot names.
    The promotion door refuses a LIVE install outside the same window
    (`APPROVAL_OUTSIDE_ARM_WINDOW`), so it never installs an arm the gate would refuse.

    - **An approved promotion stays APPROVED.** It is verified, never consumed, and only a PENDING
      approval expires. The check does not judge its validity window against the order: the arm
      outlives the ask.
    - **The content hash is not re-derived.** It names the members the promotion returned to
      trading, which is the pool as it stood at install.
    - **What the authority carries:** `approval_fingerprint`, `approval_verified` and
      `approval_problem`. The profile requires `approval_verified` to be `true`, with a fingerprint.
    - **Failures hold the entry and never halt the fan-out.** A store that cannot be read gives
      `LIVE_ARM_APPROVAL_UNREADABLE`, and any other failure while verifying is a failed re-read.
      The problem is added to the cycle record's reason codes after `approved_profile_complete`.
    - **A row sealed before PR2c-2b** names none of the three fields. The verified read still
      accepts it, but a send never does. A row naming any of them must carry a verified arm.
  - probe: `probe_plan`, the batch and its approval.
  - testnet: `testnet_caps`, the caps the path carries in code. No budget backs a venue with no money.

## 4. The record

- **Location:** one append-only file per venue, `pre_order_risk_snapshots.jsonl`, under that venue's
  state directory (`state.venue_state_dir`).
- **Gating:** writes are behind that venue's switch and provider. The live authorization cannot
  write the testnet store, and the reverse also holds.
- **What is written:** only approved snapshots of orders about to be sent.
- **Validation:** every row must satisfy `pre_order_risk_snapshot.v0.1`, and every row is sealed by
  `risk_snapshot_sha256`.
- **Encoding:** rows are written as ASCII JSON, one per line. Whatever a field holds, the writer and
  every reader split the file the same way.
- **Idempotency:** an append is idempotent on `pre_order_risk_snapshot_id`. Different content under a
  recorded id is refused.
- **Durability:** each row is fsynced before its order is sent. The directory entry is not synced,
  as for the live book.
- **A row with no order is possible; an order with no row is not.** A crash between the write and
  the send leaves a row with no order.
- **An unfinished last line is not a row.** A row and its newline go out in one synced write, so text
  after the last newline is a write that never returned, and no order followed it. Readers ignore it,
  and the next append cuts it off before writing its own row. Only the complete part is decoded, so a
  character cut in half does not make the record unreadable.
- **A damaged row is refused, not skipped.** Every line before the last newline must parse as a
  record. It could be the record of an order that did leave. A damaged line fails the verified read,
  and the writer refuses to append past it. Every entry at that venue is then refused
  (`RISK_SNAPSHOT_STORE_TAMPERED`) until the file is repaired; closes never touch the store. Repair by
  moving the file aside and keeping it: the next append starts a new record, and the moved file
  stays the record of the orders before it.
- **Write failures are typed:** `RISK_SNAPSHOT_STORE_UNWRITABLE`.
- **Verified reads:** `read_snapshots` refuses any row that fails its seal, the schema, or what the
  gate requires of an approved snapshot (§5, step 6). The readiness board shows the mainnet record as
  the `pre_order_snapshots` check.
- **Rows the PR2b gate sealed stay readable.** They name six gate checks and no decision time
  (`PR2B_GATE_CHECK_IDS`). The verified read accepts them; a send never does. A row that names
  `decision_time_recorded` must carry every current check.

## 5. The binding

`submit_and_reconcile(..., risk_snapshot, snapshot_store)` checks, in order, before the adapter is
called. A failure at any step is a `SubmitRefused` (a `ToolError`), raised only when nothing was sent.

1. The guard approved.
2. The request builds.
3. The snapshot is this gate's version and intact.
4. The snapshot is approved.
5. The snapshot is schema-valid.
6. **What the snapshot records supports its approval** (`RISK_SNAPSHOT_UNSUPPORTED`). The seal is a
   plain hash: it shows that nothing changed after sealing, not that the gate did the sealing. So the
   door re-checks what the gate requires of every snapshot it approves:
   - the venue is the purpose's;
   - the profile is whole, built for this purpose, and matches its hash;
   - each of the gate's own checks is recorded once, beside at least one door check;
   - the lineage is complete and names this order.
7. **The order is the one approved** (`RISK_SNAPSHOT_INTENT_MISMATCH`):
   - the fields the snapshot copies (symbol, side, the three ids) are the order's;
   - `intent_fingerprint` equals the order's;
   - the order opens exposure in its own direction, and its ids follow from its fields;
   - the order names the snapshot back by all three references: `pre_order_risk_snapshot_id`,
     `risk_gate_id` and `risk_snapshot_sha256`.
8. **The decision is fresh** (`RISK_SNAPSHOT_STALE`, PR2c-1): `facts.decided_at` is at most
   `MAX_SNAPSHOT_AGE_SECONDS` (60, the account age bound) before the send, judged at the wall clock.
   A decision time that is missing, unreadable, or after the send is refused like an old one. The
   verified read of the record (`read_snapshots`) does not apply this: age bounds the send, not the
   record.
9. There is a store, and it is the snapshot's venue's. An adapter that can reach a venue needs a
   store that actually writes (`RISK_SNAPSHOT_NO_STORE`). The append returns this hash.

**Ordering at the doors.** The gate spends nothing, so a refusal costs no bar and no daily slot.
The autonomous leg then checks two things before it spends anything:
- the snapshot (steps 3–8 above), so a decision already too old costs no symbol, bar or slot. The
  age is judged again at the bind below, at the wall clock to the second: a decision that turns too
  old between the two is refused after its bar and slot are spent, like every refusal there. That
  needs about 60 seconds between the decision and this check;
- that the bracket it will place carries the sealed intent's stop and target, on the sides that
  close the position (`LIVE_ENTRY_BRACKET_NOT_APPROVED`). The venue door never sees the bracket:
  its legs are reduce-only.

Then:

1. The entry takes its symbol (PR2b-2, decisions 21 and 22). Under the entry-marks lock, no other
   entry may be in flight on the symbol and the book may hold no position there. The probe takes it
   at this point too, before its slot.
   - **The global caps are judged again at the claim** (PR2c-3, decision 26,
     `live_order.claim_caps_problem`). Two doors entering two different symbols each judged "the
     book plus my order fits" on facts read before the other's order. Under the same lock, the
     claim reads the whole book again. The other entries in flight are the unexpired claims on
     other symbols that the book does not hold yet.
     - **Positions:** the book, plus those entries, plus this one must stay within
       `MAX_LIVE_CONCURRENT_POSITIONS` (`LIVE_ENTRY_CAPACITY_TAKEN`).
     - **Exposure:** start from the venue open notional the door's guard judged. That figure was
       read beside the door's book, whose position ids the decision records (`exposure_seen`); an
       entry is judged only on a book the venue agrees with. Add each booked position that book did
       not hold, each other entry's notional, and this order's notional as its guard judged it. The
       sum must stay within the exposure cap the gate judged (`LIVE_ENTRY_EXPOSURE_TAKEN`).
       - A position counts as new by its id, not its symbol, so one replaced on its symbol since
         the read is counted at its new size.
     - **What the claim records:** its notional, in a map beside the claims
       (`in_flight_notional`, bound to the claim's order id). The claim itself keeps its three
       fields: a runtime from before PR2c-3 reads a fourth as a damaged file and would refuse every
       entry after a rollback. That runtime ignores the map and drops it on its next write.
     - **What counts as the whole cap:** a claim the map does not name for its own order (written
       before PR2c-3, or by an older runtime), and a booked record whose notional cannot be read.
     - **A claim that outlives its entry now holds room on every symbol.** An unconfirmed entry
       keeps its claim for up to 30 minutes, and until then it counts against the position and
       exposure caps of entries on other symbols as well as its own.
     - **Why the three sources cover every entry:** a door gives its symbol back only after the
       book records what the venue holds. Under the lock, every entry is booked, in flight, or both.
   - **Nothing may rest at the venue on the symbol** (PR2c-3, decision 25,
     `live_leg.resting_orders`). Right after the claim, both doors read the plain and the
     conditional open orders on the symbol. A leg left behind can close or shrink the next
     position, so anything resting refuses the entry (`LIVE_ENTRY_RESTING_ORDERS`,
     `PROBE_RESTING_ORDERS`).
     - **A read that fails refuses too:** `LIVE_ENTRY_RESTING_ORDERS_UNREADABLE` on the autonomous
       leg, `PROBE_RESTING_ORDERS` on the probe. So does an answer that is not a list of orders:
       the adapter raises `ORDER_MALFORMED_RESULT` rather than reading it as empty.
     - **Then the decision's age is judged again.** The two reads can take their timeouts, so the
       door judges the decision's age again before it spends a bar or a slot. A decision that aged
       out is refused (`RISK_SNAPSHOT_STALE`).
     - **What a refusal costs:** the symbol goes back and nothing else is spent. Nothing is
       cancelled: the operator withdraws what rests (`scripts/list_resting_orders.py` lists it).
2. The entry leg claims the bar.
3. It reserves the day's slot.
4. It records the snapshot.
5. It sends, and the venue door re-binds (a no-op append).

The symbol is given back only where the book says what the venue holds:
- a refusal before the venue;
- a submit the venue refused with its own code, followed by an answer that the order does not
  exist;
- a booked position;
- a naked close the venue confirmed, after an entry the venue confirmed FILLED.

Anywhere else the claim stays until it expires after 30 minutes, including:
- an unconfirmed entry, and a partial fill even once its close confirmed (the rest may still fill);
- a close that did not confirm;
- a book that could not be written.

An entry that finds its claim gone after its order left (`LIVE_ENTRY_CLAIM_LOST`) outlived it,
and the fan-out halts. The probe accepts no per-call timeout above 60 seconds, so its entry stays
well inside the claim.

**Where the hash goes.** It rides to the submit result, the live position, the outcome row, the
audit event's `evidence_refs` (`risk_snapshot:<sha>`) and the testnet evidence row.

## 6. Not yet in it

- **What the arm check cannot see (PR2c-2b).**
  - **An approval the lineage once had.** A promotion approval is never consumed. A pool edit that
    names an old LIVE approval, with an install time inside its window, verifies. The same holds
    for a pool file restored from before a disarm, since it carries no disarm trace. A later
    disarm is recorded only in the pool and the ledger. Binding the arm to the ledger's promotion
    and disarm events would close this; that is a decision still to make.
  - **Which hash belongs to which candidate.** The approval lists the ids and the hashes
    separately. An entry can name one approved candidate while trading another approved
    candidate's rule.
  - **`promoted_at` is what the pool says.** The door writes it, and so does anyone who writes the
    pool.
  - **An append in progress.** The store is read without its lock, and a half-written last line
    makes it unreadable. The entry is held for that pass, as the stage witness is.
- **An entry refused for resting orders sends no message (PR2c-3).** The refusal and the order ids
  are on the cycle record, and the refusal repeats every pass until the operator withdraws the
  order. A notice that fires once per symbol and order set needs state the route does not keep yet.
- **The readiness board counts armed entries, not verified ones.** An arm that fails verification
  shows as armed there and is held at every gate, with the reason in the cycle record.
- **Tampering by the state directory's own writer is out of reach.** It can forge an arm as it can
  forge the stage record (`EXECUTION_STAGE_V0.1.md`).
- **The order book's age.** The spread door judges the book the fire read for the symbol, memoized
  for the fire, so it can be as old as the fire (about a minute). No bound checks it.
- **The checks the directive lists that no door runs yet (PR2d):** per-order slippage and fee
  evidence.
- **The daily series' bound sits at the edge of a sound reading (PR2d-2).** The forming day is
  dropped, so a sound daily reading is 24 to 48 hours old at a bar. If the vendor publishes the
  closed day late, the first 15m and 1h bars after midnight read stale until it does. The cycle
  records' `optional_data_ages` show how often.
- **What the API error breaker does not count (PR2d-1, decision 27).**
  - **Calls outside the doors' roster.** It counts the five signed calls both entry doors make
    through the order adapter (`live_order.API_ADAPTER_CALLS`, pinned against the doors' own
    code by a test), the account read, and the fill history a settlement falls back to
    (`API_FEED_CALLS`). Not counted:
    - the account's P&L-history read, the second call inside the account read. Its failure
      narrows the snapshot, and the account read is counted once, as a success;
    - the funds board's refresh (`account_store.refresh_snapshot`), the dashboard's and the
      readiness board's reads, the fee measurement, and the operator's own tools
      (`list_resting_orders`, `diagnose_bracket_leg` and its `validate_order`);
    - public market data and the testnet cycle.
  - **A venue code it does not know.** A refusal that came back 418, 429 or 5xx counts whatever
    its code. Otherwise the venue's own code decides (`API_ERROR_VENUE_CODES`), and without one
    the runtime's (`API_ERROR_REASON_CODES`). Any other refusal is read as a business rejection:
    neither a failure nor a success.
  - **A pass it could not record.** A door judges the breaker not clear while its record cannot be
    written (checked by rewriting it before the decision) or a write of the pass already failed.
    A failure after the last send of a pass that could not be written is missing from the count.
- **Single use:** a snapshot can be re-bound within its 60 seconds (PR2c-1). A second send of the
  same order is prevented by the doors (the bar claim and the slot) and by the venue's
  duplicate-client-id rule.
- **The probe's price is not re-judged at the gate.** The probe prices its order on the market read
  itself, at the fire's start. Only its account's age is checked at the gate.
- **A refusal after the slot is reserved still spends the slot**, although nothing reached the venue.
  This is the conservative direction and is kept.
- **A probe plan left unrecorded after a send is not repaired.** The fire reports it
  (`PROBE_PLAN_NOT_RECORDED`) and the operator reconciles the cell by hand.
- **Protective prices outside the autonomous leg are not sealed as prices.** The probe prices its
  stop on the actual fill, from the approved plan's width, because that price does not exist
  before the fill. The testnet cycle places fixed-distance legs and withdraws them at once.
