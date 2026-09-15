# Execution Stage v0.1 — the machine's one authoritative crypto execution stage

**Status:** PR1a — the record, the door and the reporting are implemented; **no entry door reads the
stage yet** (`execution_stage.STAGE_ENFORCED = False`). PR1b makes the doors read it.
**Owner:** Thomas. **Authority:** this contract describes; `runtime/mvp_runtime/crypto/execution_stage.py`,
`schemas/execution_stage.v0.1.schema.json` and their tests decide.
**Decided:** 2026-09-15, Thomas decisions 1, 2, 4, 5, 8, 9 (record: `/root/thomas_crypto_refactor_2026-09-15/decisions-2026-09-15.md`),
after the execution-authority audit found that no record said what stage the machine was at — the
environment authorized the adapter, eight records could refuse an entry, and the readiness board
re-derived an answer from all of them.

## 1. The ladder

`READ_ONLY → SHADOW → PAPER → SIGNED_TESTNET → LIVE_CANARY → LIVE_AUTONOMOUS → LIVE_SCALED`

| Stage | New exposure a door may create once enforced (PR1b) |
|---|---|
| READ_ONLY, SHADOW, PAPER | none on the venue |
| SIGNED_TESTNET | none on mainnet (signed testnet evidence only; path in PR1d) |
| LIVE_CANARY | the canary and probe doors |
| LIVE_AUTONOMOUS | the autonomous leg, canary, probe, and arming a strategy LIVE |
| LIVE_SCALED | as LIVE_AUTONOMOUS; its entry rule is a separate decision |

**Closing is never gated by the stage.** A demotion or an expiry changes only which *new* exposure a
door may create; a reduceOnly close, a protection re-check and reconciliation do not read it.

## 2. The record

One file, `.runtime_governance_state/crypto/execution_stage.json` (never under `bridge/`), self-hashed,
schema `execution_stage.v0.1`, written only by `scripts/register_execution_stage.py` as uid 10001. Every
transition also leaves an `execution_stage_transition.v0` event on the control ledger.

It reads **READ_ONLY** (decision 9), with the reason, when it is: missing · unreadable · tampered ·
schema-invalid · for another venue · internally inconsistent (its transition does not match its stages)
· a LIVE stage without an end date · not yet effective · expired · bound to a different policy version ·
bound to a different **safety semantic fingerprint** (decision 4: `policy_fingerprint.SAFETY_SECTIONS`,
hashed after parsing — comments do not move it, a kill verb or a disposition does) · or, for any
non-demotion, not backed by its **CONSUMED approval** with this record's fingerprint and
`consumption_ref = execution_stage:<stage_id>` (the self-hash alone can be recomputed by anyone who can
write the state directory).

## 3. Transitions

| Transition | Rule | Approval |
|---|---|---|
| BOOTSTRAP | no record → SHADOW or PAPER, with an attestation of the evidence (decision 1: this host starts at **PAPER**, one approval) | once |
| CLIMB | exactly one rung up, from a record that binds; no skip | once |
| REBIND | the same rung again — after a policy change, or to renew a LIVE end date | once |
| DEMOTE | any rung down, immediate | **none** (decision 8) |

Evidence a climb requires before it can be asked: **LIVE_CANARY** — a reconciled signed testnet order
(decision 2; refused as `EXECUTION_STAGE_SIGNED_TESTNET_EVIDENCE_REQUIRED` until PR1d). **LIVE_AUTONOMOUS** —
at least 3 clean canary orders, a floor in code. **LIVE_SCALED** — refused (no rule yet). A LIVE stage
carries an end date of at most 30 days.

The ask is `RUNTIME_GOVERNANCE` with target `execution_stage:<venue>:<stage>` (RED for a LIVE target),
announced on the control channel and never mirrored. It binds the whole transition, the record it was
asked against (`stage_ref`) and the policy identity; the spend (`--confirm`) re-checks both against the
running machine, writes nothing and keeps the grant APPROVED if either moved, and otherwise spends it
once (CONSUMED before the record is written). There is no `--without-approval`.

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

PR1a: the readiness board (row `execution_stage`, informational; `readiness_data.execution_stage`), the
live leg's cycle record (`live_execution_stage`), `--show`. PR1b: the entry guard, the canary and probe
doors, and the LIVE-tier promotion door.
