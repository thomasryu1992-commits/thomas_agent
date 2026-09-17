# Pre-Order Risk Snapshot v0.1 — the one gate before an order that opens exposure

**Status:** enforced since crypto PR2b. `live_execution.submit_and_reconcile` refuses every order
that is not reduce-only unless it carries an approved snapshot that has already been recorded.
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

## 1. What passes through it

| Door | Purpose | What the door re-derives before the gate seals |
|---|---|---|
| Autonomous leg (`live_route` → `live_leg.execute_live_entry`) | `autonomous` | `live_entry.plan_live_entry`, re-run on the same facts mapping. This covers every door by name and the final guard's checks. The order must be the one those facts decide, and the bracket the leg will place must be the one they price (`bracket_matches_intent`). |
| Slippage probe (`scripts/run_slippage_probe.py --fire`) | `probe` | `probe.gate_probe_order`: the plan and its cell, the account, the symbol being free, the three breakers, the priced ceiling, and the order rebuilt and judged by the live guard in canary mode. |
| Signed testnet cycle, entry only (`scripts/run_signed_testnet_cycle.py`) | `signed_testnet` | `testnet_execution.gate_testnet_order`: the testnet guard re-run, and the order rebuilt from the cycle's inputs. |

The gate never judges reduce-only orders: closes, brackets and cancels. The venue enforces that
they cannot add exposure, and a gate that could refuse them could trap a position.

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
    writes it to `live_tier_approval_id` beside the tier, and the disarm door removes it. Only the
    id's presence is checked. That it names a real approval to arm this candidate is not re-verified
    at order time (§6).
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
8. There is a store, and it is the snapshot's venue's. An adapter that can reach a venue needs a
   store that actually writes (`RISK_SNAPSHOT_NO_STORE`). The append returns this hash.

**Ordering at the doors.** The gate spends nothing, so a refusal costs no bar and no daily slot.
The autonomous leg then checks two things before it spends anything:
- the snapshot (steps 3–7 above);
- that the bracket it will place carries the sealed intent's stop and target, on the sides that
  close the position (`LIVE_ENTRY_BRACKET_NOT_APPROVED`). The venue door never sees the bracket:
  its legs are reduce-only.

Then:

1. The entry leg claims the bar.
2. It reserves the day's slot.
3. It records the snapshot.
4. It sends, and the venue door re-binds (a no-op append).

**Where the hash goes.** It rides to the submit result, the live position, the outcome row, the
audit event's `evidence_refs` (`risk_snapshot:<sha>`) and the testnet evidence row.

## 6. Not yet in it

- **Freshness bounds (PR2c):**
  - price age at order time;
  - account age;
  - the price basis;
  - a gate-time re-read of the stage and the halts.
- **The checks the directive lists that no door runs yet (PR2d):**
  - the API error breaker;
  - per-order slippage and fee evidence;
  - optional-data health as a gate.
- **A shared per-symbol entry lock between the probe and the autonomous leg:** left from PR2a
  (PR2b-2).
- **Order-time re-reads (PR2c):** the arming approval behind `live_tier_approval_id`, the pool tier,
  and the budget are re-read at the gate only as the leg read them. The autonomous gate re-runs the
  decision on the same facts, so it catches a changed order, not a changed fact.
- **Single use:** a snapshot can be re-bound with no time bound. A second send of the same order is
  prevented by the doors (the bar claim and the slot) and by the venue's duplicate-client-id rule.
- **A refusal after the slot is reserved still spends the slot**, although nothing reached the venue.
  This is the conservative direction and is kept.
- **Protective prices outside the autonomous leg are not sealed as prices.** The probe prices its
  stop on the actual fill, from the approved plan's width, because that price does not exist
  before the fill. The testnet cycle places fixed-distance legs and withdraws them at once.
