# Approval Flow v0.1 (R9)

**Status:** Active runtime capability
**Owner:** Thomas
**Authority:** None. This document describes an implementation; the canonical Governance
Policy (`governance/GOVERNANCE_POLICY.yaml`) owns the rules it obeys.

The runtime's first governed **ask**. Everything before it the agent either decided itself
(ALLOW) or did-and-reported (EXECUTE_AND_REPORT, R8). An APPROVAL_REQUIRED action is one it
may not decide: it must ask Thomas, over the one channel that can prove it was Thomas, and
record the answer as tamper-evident evidence.

Implemented in `runtime/mvp_runtime/approval.py` + `approval_store.py` + `approval_cli.py`.

## Where this stops (and where R10 continues it)

```
PENDING --(/approve)--> APPROVED --(consume, R10)--> CONSUMED
        --(/reject )--> REJECTED
        --(ttl)-------> EXPIRED
```

Through R9 the flow stopped at APPROVED: an approved grant authorized nothing on its own.
**R10 adds the last transition** — *consuming* the one-time grant to perform exactly its
bound action — as its own governed, gated step. It is deliberately the narrowest possible
crossing; the full design is in [APPROVAL_CONSUMPTION_V0.1](APPROVAL_CONSUMPTION_V0.1.md).
What changed, and what did **not**:

| Aspect | R9 | R10 |
|---|---|---|
| `approval_lifetime.approval_consumption_implemented` | `false` | **`true`** (a scoped implementation exists) |
| `runtime_effect.approval_consumption_allowed` | `false` | **`false`** — unchanged; consumption is granted per-machine by the `approval_consumption` safety flag, not as a standing runtime effect (the R8 `filesystem_write` precedent; the read-only kernel still requires this block fully REVIEW_ONLY) |
| `CONSUMED` state | absent | added in **`approval.v0.2`** (`consumption_status: … \| CONSUMED`) |
| `approval_scope`, `runtime_effect.mode` | `REVIEW_ONLY` | `REVIEW_ONLY` — unchanged; a CONSUMED record grants no executor/external/financial effect |

So an APPROVED approval **still** authorizes nothing on its own — spending it is a separate
step that fails closed unless the `approval_consumption` safety flag is activated on the
machine. What the ask/answer flow produces is proof that Thomas was asked and what he
answered; consumption produces proof that the one grant was spent, once, on exactly what he
saw.

`CONSUMPTION_PREVIEWED` remains unimplemented: its schema requires an
`execution_request.v0.1`, which belongs to the **deferred** executor family
(`request_can_execute: false`). Implementing it would pull deferred material onto the
active path.

## Zero new governance surface

No new contract, schema, registry, or gate. `APPROVAL_CONTRACT_V0.1` is already an
`ACTIVE_RECORD_CONTRACT`; `approval.v0.1` already models the whole lifecycle and already
*requires* Telegram-verified identity on any decided record; `scripts/create_approval_request_preview.py`
already knew the record shape (its logic is now reused in-process, as `binding.py` did with
`create_core_context_binding.py`); R4's operator identity gate already implements the
verification the schema demands.

## The worked action: memory promotion

The runtime had no APPROVAL_REQUIRED action — everything was ALLOW or EXECUTE_AND_REPORT.
The first one is promoting a working-memory candidate to VALIDATED
(`SENSITIVE_MEMORY_GOVERNANCE` / `memory.validated.promote`, P4), matching the repo's own
worked example (`examples/permission/permission_approval_required_v0.3.yaml`).

It needs Thomas precisely because Prime cannot do it: the charter's conditional P4 excludes
changing Validated Memory (`THOMAS_PRIME_CHARTER` §10), and `MVP_OPERATING_POLICY` §12.10
bars Prime from promoting preference/goal memory. Prime's authority here is the authority to
**prepare the request**; the decision is Thomas's, and the promotion itself remains what R5.3
made it — an explicit operator action (`scripts/promote_memory_candidate.py`).

## Identity is the whole point

An approval is only worth the certainty that Thomas gave it. Reaching the decision path
**is** the proof: `handle_operator_message` runs R4's identity gate first, which enforces
exactly the policy's `invalid_approval_sources`. Each failure has its own reason code:

| Attempt | Refused with |
|---|---|
| someone else | `UNREGISTERED_USER` |
| group / channel | `NOT_PRIVATE_CHANNEL` |
| forwarded message | `FORWARDED_MESSAGE` |
| wrong chat | `CHAT_NOT_REGISTERED` |
| bare `/approve` (ambiguous) | `NO_APPROVAL_ID` |
| unknown id | `UNKNOWN_APPROVAL` |
| deciding twice | `NOT_PENDING` |
| after expiry | `APPROVAL_EXPIRED` |
| decision not tied to its action | `PERMISSION_DECISION_MISSING` |

The `approval.v0.1` schema independently requires `approved_by: Thomas` +
`verification_status: VERIFIED` + a verification ref on any decided record, so a decision
without verified identity cannot even be built.

## Binding, TTL, single use

- The approval **snapshots the exact action** (fingerprint + payload), so what Thomas sees is
  what he decides and nothing can be substituted later.
- `approval_id` is **derived from the action fingerprint**, so any material change yields a
  different id — `/approve <id>` always names exactly one action.
- TTL is the **earlier** of the policy maximum for the scope and the PermissionDecision's own
  expiry: an approval never outlives the decision it binds to.
- Single-use: a decided approval is final (`approval_reuse_allowed: false`;
  BLOCK: `APPROVAL_REUSE`).
- The store is **append-only** — a decision appends the decided record rather than editing
  the request, so both survive and a decision cannot be quietly rewritten.

## The permission gate widening

R9 makes an APPROVAL_REQUIRED decision **buildable** — it is the object an Approval Request
binds to — while leaving it **not executable**:

- `_BUILDABLE_DISPOSITIONS` = ALLOW, EXECUTE_AND_REPORT, APPROVAL_REQUIRED
- `_EXECUTABLE_DISPOSITIONS` = ALLOW, EXECUTE_AND_REPORT  *(unchanged)*
- `_APPROVAL_REQUIRED_SCOPES` = `{SENSITIVE_MEMORY_GOVERNANCE}` — only the scope the runtime
  can actually ask about. `PUBLICATION`, `EXTERNAL_COMMUNICATION`, `DESTRUCTIVE_CHANGE`, … stay
  refused: a request for one would be an ask the runtime could never honour.
- BLOCK stays unbuildable.

## Commands

| Where | Command |
|---|---|
| Local console (ask) | `python -m runtime.mvp_runtime.approval_cli request --candidate-id memcand_...` |
| Local console (read) | `... approval_cli list` / `... approval_cli show <approval_id>` |
| Control channel (answer) | `/approve <approval_id>` · `/reject <approval_id>` |
| Local console (spend, R10) | `... approval_cli consume <approval_id>` (gated by the `approval_consumption` safety flag) |

Answering from the local console is deliberately impossible: only the verified Telegram
private channel can prove the answer is Thomas's
(`local_operator_console.new_high_risk_approval_creation_allowed: false`). *Consuming*, by
contrast, is a local operator step — it spends an answer Thomas already gave on the verified
channel, and is itself gated behind the safety flag.

## Audit

Asking, answering, and spending are each their own event (`OTHER` + subtype in
`reason_codes`, per the I0.5.4 precedent), chained onto the durable ledger and classified
`SENSITIVE`. Asking and answering carry `NO_EXECUTION_AUTHORIZED`; the decision event names
the verification method and ref, so the trail shows not just that an approval was granted but
how we know it was Thomas. The **consumption** event (R10) carries `APPROVAL_CONSUMED` +
`CONSUMED` and names the validated-memory id it produced, so the trail also shows that the
grant was spent, once, and exactly what it produced.

Approvals live in `.runtime_governance_state/approvals/` (local, gitignored, per-machine).
