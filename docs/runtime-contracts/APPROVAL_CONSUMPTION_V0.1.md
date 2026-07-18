# Approval Consumption v0.1 (R10)

**Status:** Active runtime capability (gated OFF by default)
**Owner:** Thomas
**Authority:** None. This document describes an implementation; the canonical Governance
Policy (`governance/GOVERNANCE_POLICY.yaml`) owns the rules it obeys.

The runtime's first **spend**. Through R9 an APPROVED approval authorized nothing — it was
proof Thomas had been asked and what he answered, and the bound action (promoting a
working-memory candidate to VALIDATED) stayed a separate operator step. R10 closes the loop
with the narrowest possible crossing: *consuming* the one-time grant performs exactly the one
action it was bound to, once, and nothing else.

Implemented in `runtime/mvp_runtime/consumption.py` (+ `approval.build_consumed_record` for
the CONSUMED evidence record and `audit.build_approval_consumption_audit` for the report).

## Why this was safe to add — and stays narrow

Consumption is scoped, gated, hot-path-revalidated, and single-use. Each layer fails closed.

### Scoped — nothing wider than one memory promotion

Only a `SENSITIVE_MEMORY_GOVERNANCE` promotion is consumable (`_CANDIDATE_TARGET_PREFIX`
guards the target; a non-promotion scope is refused `SCOPE_NOT_CONSUMABLE`). The CONSUMED
record keeps `approval_scope: REVIEW_ONLY` and every `runtime_effect` flag false. Consuming
performs an internal governed memory write — the **R8 precedent**: an EXECUTE_AND_REPORT
effect under REVIEW_ONLY — never an executor handoff, external call, or financial move.

### Gated — a per-machine safety flag, not a standing grant

The capable consumer is constructed only behind the `approval_consumption` safety flag, via
`safety_gate.select_gated`. Without the operator's opt-in (`MVP_APPROVAL_CONSUMPTION=on`)
**and** a local, integrity-checked activation record, the gate returns an inert consumer that
refuses (`CONSUMPTION_DISABLED`), or — opted in without a valid activation — raises
`SafetyGateBlocked`. An env var alone consumes nothing. Activate locally with:

```
python scripts/activate_safety_flag.py --provider-id approval_consumption \
    --flags approval_consumption --authority-level P4 --ttl-minutes 30 \
    --reason "Operator decision: enable one memory-promotion consumption."
```

This is exactly why the canonical policy keeps `runtime_effect.approval_consumption_allowed:
false`: the governance does **not** auto-grant consumption as a standing runtime effect (which
would also break the read-only replay kernel's preflight, which requires that block fully
REVIEW_ONLY). The grant is the per-machine safety-flag activation, mirroring R8
`filesystem_write`. The only governance flag R10 flips is
`approval_lifetime.approval_consumption_implemented: true` — "a consumption implementation
exists" — which the kernel does not read.

### Hot-path revalidated — he can only spend a grant on what he saw

Before acting, consumption re-derives the action fingerprint from the approved snapshot
(`FINGERPRINT_MISMATCH` if it no longer matches) and re-hashes the **current** candidate
content, refusing (`CONTENT_CHANGED`) if it drifted since the approval, or `CANDIDATE_GONE` if
the candidate was promoted, pruned, or expired. The candidate lookup is latest-wins over the
append-only working-memory store, so a candidate re-appended with tampered content is caught,
not a superseded earlier copy.

### Single-use — a spent grant is terminal

A CONSUMED approval is final. A compare-and-set re-read of the stored status immediately
before acting refuses a second consume (`ALREADY_CONSUMED`); the MVP runs one process
sequentially, so this closes the realistic window, and the append-only store then makes the
spend itself durable, tamper-evident evidence. Reuse stays blocked
(`approval_reuse_allowed: false`, `one_time_use_required: true`).

## The lifecycle

```
PENDING --(/approve)--> APPROVED --(consume)--> CONSUMED
```

`approval.v0.2` adds `CONSUMED` to both `status` and `consumption.consumption_status`, plus
`consumed_at` and `consumption_ref` (which names the validated-memory id + audit the spend
produced). A CONSUMED record carries the same verified Thomas approver as the APPROVED record
it came from — a spent grant is still the grant Thomas gave.

## Ordering and fail-closed persistence

`consume_approval` computes the promotion (behind the gate), builds the CONSUMED record, then
**builds the consumption audit event before persisting anything** — a consumption that cannot
be audited fails closed with no half-written state (mirroring the R5 promotion ordering).
Only then are the validated entry, the CONSUMED approval, and the audit event appended.

## Audit

The consumption is its own event: `OTHER` with `reason_codes` `[APPROVAL_CONSUMED,
MEMORY_PROMOTED, EXECUTE_AND_REPORT, ONE_TIME_USE, CONSUMED]`, classified `SENSITIVE`, chained
onto the durable ledger tip, anchored to the originating task via the approval's lineage. It
names the validated-memory id it produced — never the content.

## Commands

| Where | Command |
|---|---|
| Local console (spend) | `python -m runtime.mvp_runtime.approval_cli consume <approval_id>` |

Only an APPROVED, unexpired, single-use grant can be consumed, only when the
`approval_consumption` safety flag is activated on the machine.

## Zero new governance surface beyond the two named changes

No new contract, registry, or gate. One schema bump (`approval.v0.1 → v0.2`, additive:
`CONSUMED` + two consumption fields). One governance flag flip
(`approval_consumption_implemented: true`). The permission/approval gate and the slimming gate
were adjusted to expect the implemented flag and to understand the `CONSUMED` evidence state,
while continuing to pin every runtime-effect grant — including
`approval_consumption_allowed` — false. `permission_decision.v0.3` is unchanged: consumption
is an approval-record lifecycle transition; the permission decision is immutable planning
evidence and never carries a consumed state.

## Deliberately excluded

Executor handoff, external/financial execution, runtime mutation, `CONSUMPTION_PREVIEWED`
(needs the deferred `execution_request.v0.1`), consuming any scope other than the memory
promotion, and any relaxation of `runtime_effect.mode` from `REVIEW_ONLY`.
