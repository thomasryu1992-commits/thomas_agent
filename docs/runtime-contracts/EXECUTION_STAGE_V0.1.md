# Execution Stage v0.1 — the machine's one authoritative crypto execution stage

**Status:** enforced since PR1b (`execution_stage.STAGE_ENFORCED = True`). The record, the
registration door and the reporting landed in PR1a; the entry guard, the autonomous leg, the
slippage probe and the readiness board read the stage now.
**Owner:** Thomas. **Authority:** this contract describes; `runtime/mvp_runtime/crypto/execution_stage.py`,
`schemas/execution_stage.v0.1.schema.json` and their tests decide.
**Decided:** 2026-09-15, Thomas decisions 1, 2, 4, 5, 8, 9 (record: `/root/thomas_crypto_refactor_2026-09-15/decisions-2026-09-15.md`),
after the execution-authority audit found that no record said what stage the machine was at — the
environment authorized the adapter, eight records could refuse an entry, and the readiness board
re-derived an answer from all of them. Same day, Thomas: no canary rung (canaries ended 2026-07-29) and
no expiry (renewals on the money path were retired 2026-07-28 / 2026-08-10).

## 1. The ladder

`READ_ONLY → SHADOW → PAPER → SIGNED_TESTNET → LIVE_AUTONOMOUS → LIVE_SCALED`

| Stage | New exposure a door may create |
|---|---|
| READ_ONLY, SHADOW, PAPER | none on the venue |
| SIGNED_TESTNET | a signed order on the venue's TESTNET, through `scripts/run_signed_testnet_cycle.py` (PR1d-1) — no real money, its own venue state |
| LIVE_AUTONOMOUS | the autonomous leg, the slippage probe, and arming a strategy LIVE |
| LIVE_SCALED | as LIVE_AUTONOMOUS; its entry rule is a separate decision |

**Closing is never gated by the stage, and no stage expires.** A demotion changes only which *new*
exposure a door may create; a reduceOnly close, a protection re-check, the time exit and
reconciliation do not read it. That is why the stage is an argument of
`live_order.evaluate_live_order_guard` (the entry chokepoint, which cannot be called without it) and
of nothing on the close path — `evaluate_live_close_guard` takes no stage, and a test pins that it
never will.

## 2. The record

One file, `.runtime_governance_state/crypto/execution_stage.json` (never under `bridge/`), self-hashed,
schema `execution_stage.v0.1`, written only by `scripts/register_execution_stage.py` as uid 10001, one
writer at a time (`execution_stage.lock`). Every transition also leaves an
`execution_stage_transition.v0` event on the control ledger.

It reads **READ_ONLY** (decision 9), with the reason, when it is: missing · unreadable (including content
that cannot be canonicalised) · tampered · schema-invalid · for another venue · internally inconsistent
(its transition does not match its stages or its witness fields) · not yet effective · **not witnessed**
(below) · bound to a different policy version · bound to a different **safety semantic fingerprint**
(decision 4: `policy_fingerprint.SAFETY_SECTIONS`, hashed after parsing — comments do not move it, a kill
verb or a disposition does). The read never raises: the live leg reads it before it settles and protects.

**The witness.** The self-hash can be recomputed by anyone who can write the state directory, so every
record above READ_ONLY names an approval (`approval_id`, `action_fingerprint`, `witness_stage_id`) that must
be CONSUMED with `consumption_ref = execution_stage:<witness_stage_id>`, decided by Thomas on the verified
channel, with a snapshot that still fingerprints to the bound value, in scope `RUNTIME_GOVERNANCE`, whose
content hashes to `witness_stage_id`.

- BOOTSTRAP / CLIMB / REBIND: the witness is the record's own approval, and the record must carry exactly
  that content (stage, previous stage, transition, venue, policy identity, who, why, evidence), effective at
  the moment it was consumed.
- DEMOTE above READ_ONLY: the witness is the approval the demoted record stood on. The demotion must sit
  strictly below that approval's stage and keep its policy identity — so a demotion lowers a stage; it
  cannot mint one, rebind one after a policy change, or launder one that was never approved.
- DEMOTE to READ_ONLY: no witness. It grants nothing.

**Limit, on the record:** the approval ledger lives in the same state directory and carries no secret. A
writer able to forge a whole coherent approval lifecycle can still forge a stage, and one who kept a copy of
an earlier witnessed record can restore it and undo a demotion; the witness removes the one-hash forgery.
That writer is the service uid, which could already place orders directly.

## 3. Transitions

| Transition | Rule | Approval |
|---|---|---|
| BOOTSTRAP | no stage above READ_ONLY binds (no record, a READ_ONLY record, or a record that does not bind for a reason other than a policy change) → SHADOW or PAPER, with an attestation (decision 1: this host starts at **PAPER**, one approval). A record it replaces is named in the ask. | once |
| CLIMB | exactly one rung up, from a record that binds; no skip | once |
| REBIND | the same rung again, only when the record was witnessed and the policy version or safety fingerprint changed | once |
| DEMOTE | any rung down, immediate. To READ_ONLY from any state, even an unreadable file; to another rung only from a record that binds | **none** (decision 8) |

Evidence a climb requires, checked **by target rung** so a REBIND cannot walk around it:
**LIVE_AUTONOMOUS** — one COMPLETE signed testnet cycle, named on the ask with `--testnet-cycle`
(decision 2; `EXECUTION_STAGE_SIGNED_TESTNET_EVIDENCE_REQUIRED` when unnamed,
`TESTNET_EVIDENCE_INCOMPLETE` when the cycle does not prove itself). A cycle is entry reconciled →
protective legs confirmed RESTING at the endpoint each belongs to → withdrawn → exit reduceOnly and
reconciled → the venue's own position view clean (decision 11). The cycle id and the hash of its row
ride in the record's evidence and in the approval's content, so the approval signs one specific
cycle. It does not expire and is not re-earned after a policy change — a REBIND names the same cycle
again. **LIVE_SCALED** — refused (no rule yet).

The registry is read at the ask and again at the spend, never by `resolve_execution_stage`: that
function is what the live leg calls before it settles and protects, and its contract is that it
never raises. What the read path checks instead is structural — a record that ARRIVED at
LIVE_AUTONOMOUS naming no cycle is not one the door wrote (a DEMOTE landing there is exempt: it is a
move down, witnessed by the approval of the higher record it descends from).

The ask is `RUNTIME_GOVERNANCE` with target `execution_stage:<venue>:<stage>` (RED for a LIVE target),
announced on the control channel and never mirrored. It binds the whole transition, the record it was
asked against (`stage_ref`) and the policy identity. The spend (`--confirm`), under the stage lock,
re-plans the transition against the running machine and refuses unless it yields the same content —
writing nothing and keeping the grant APPROVED — and otherwise spends it once (CONSUMED before the record
is written). A write that fails after the spend says the grant is gone and a new ask is needed; a ledger
event that fails after the write is a warning beside WRITTEN. There is no `--without-approval`.

## 4. Operating it

```bash
docker exec -u 10001 thomas-scheduler python -m scripts.register_execution_stage --show
docker exec -u 10001 thomas-scheduler python -m scripts.register_execution_stage --request --to PAPER \
    --registered-by thomas --reason "initial stage (decision 1)" --attest "paper ledger since 2026-07; counterfactual shadow book"
# Thomas: /approve <id>
docker exec -u 10001 thomas-scheduler python -m scripts.register_execution_stage --confirm --approval-id <id>
docker exec -u 10001 thomas-scheduler python -m scripts.register_execution_stage --demote --to READ_ONLY --registered-by thomas --reason "..."
```

After a policy bump (e.g. 1.5.1) the record reads READ_ONLY until a REBIND is approved — by design.

## 5. Where it is read

- **The entry guard** (`live_order.evaluate_live_order_guard`, required argument): the autonomous
  leg through `plan_live_entry`, and the slippage probe. Below the rung the purpose needs, every
  new entry is refused and the refusal names the rung and the registration command.
- **The live leg** resolves it once beside the budget, stamps it on the cycle record
  (`live_execution_stage`) and passes that same answer to the entry door.
- **The readiness board**: the `execution_stage` check (it moves `ready`), `readiness_data.execution_stage`
  (with `admits_entry`), and the guard dry-run, which is fed the same stage.
- `scripts/register_execution_stage.py --show`.

- **The promotion door** (`scripts/promote_strategy_candidates.py --live-tier LIVE`, PR1c): arming a strategy for real money needs the stage to admit a live entry, checked at the ask and re-resolved at the install. It is the one gate on that door's roster with no escape flag. Disarming (`scripts/disarm_live_strategies.py`) reads nothing and needs no approval.
