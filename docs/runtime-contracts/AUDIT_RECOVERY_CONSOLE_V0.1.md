# Audit / Recovery Console Verbs v0.1 (R4, deferred item)

**Status:** Active runtime capability
**Owner:** Thomas
**Authority:** None. This document describes an implementation; the canonical Governance
Policy (`governance/GOVERNANCE_POLICY.yaml`) owns the rules it obeys.

The last two verbs the Governance Policy allows the local operator console:

```yaml
control_channel.local_operator_console.emergency_controls_allowed:
  - pause
  - stop_task
  - kill
  - status
  - audit       # <- this
  - recovery    # <- and this
```

Both are **read-only** and both keep working while PAUSED or KILLED — which is the point.
`kill_switch.kill_allows: [read_only_status, audit_read]` names them as the only things a
killed runtime may still do, because a killed runtime is exactly when an operator needs to
see what happened.

| Where | Command |
|---|---|
| Local console | `python -m runtime.mvp_runtime.console_cli audit [count]` · `... console_cli recovery` |
| Control channel | `/audit [count]` · `/recovery` (Telegram private 1:1, identity-gated) |

## `audit` — read the trail, and actually check it

The runtime has built a hash-chained append-only ledger since R2.6 and **nothing ever read
it back beyond its tip**. Tamper-evidence you never look at is a description, not a
property — the same shape of gap as architecture-review Finding A ("safety guarantees were
described, not enforced"). `audit` is the looking.

Verification covers the **whole** chain (the count only bounds the excerpt printed). Four
checks, because each catches a different tampering. Reason codes reuse the vocabulary
already established by `runtime/protected_governance_state/recovery.py`:

| Check | Catches |
|---|---|
| `AUDIT_EVENT_HASH_MISMATCH` | an edited fingerprint payload |
| `AUDIT_PAYLOAD_RECORD_MISMATCH` | **an edited visible record** — see below |
| `AUDIT_APPEND_ONLY_BOUNDARY_MISMATCH` | a record that stops declaring itself append-only |
| `AUDIT_PREVIOUS_HASH_MISMATCH` | insertion, deletion, reordering |

**Why the second check exists.** Each event embeds the payload it was hashed from, and that
payload duplicates the record's fields. So a self-hash check alone **misses the easiest
attack entirely**: edit `event.event_summary`, leave `integrity.event_fingerprint_payload`
untouched, and the hash still verifies perfectly. Only comparing the payload against the
record it claims to fingerprint catches it. (The existing verifier named above has this
blind spot. It is deferred code, so this is a note, not a fix.)

**Known limit, stated rather than hidden:** a *prefix* of a valid chain is itself a valid
chain, so link verification cannot by itself detect a truncated tail.

A corrupt ledger is **reported, not raised**. The operator reaching for `audit` is often
already in trouble; failing with the thing they are diagnosing helps nobody.

When the chain is broken, the reply says plainly: do **not** edit or delete the ledger to
"fix" it. That is `audit_concealment` (BLOCK), and corrections are new events, never edits
(`MVP_OPERATING_POLICY` §15).

## `recovery` — diagnose, never repair

**It diagnoses local state and names the safe action. It repairs nothing.** Both reasons
are the point, not a limitation to apologise for:

1. **Repairing the audit ledger would be the thing the governance blocks.** A damaged trail
   is evidence; truncating it so the runtime starts again destroys exactly what it exists
   to preserve.
2. **Rollback/recovery proper is not reachable.** Its only owners are
   `ROLLBACK_RECOVERY_CONTRACT_V0.1` (`REVIEW_ONLY`; `rollback_performed`/
   `recovery_performed` pinned `const false`; **requires the deferred
   `execution_request.v0.1`** — the same blocker that excluded `CONSUMPTION_PREVIEWED` in
   R9) and `RUNTIME_ENTRY_CRASH_RECOVERY_CONTRACT_V0.1` (`SYNTHETIC_TEST_ONLY` over a SQLite
   store the MVP does not use). Both sit in `DEFERRED_DISABLED` families. Governance
   mentions `rollback` **zero** times. Nothing here may claim to perform it.

What it is for: the runtime fails closed on corrupt local state — correct, but it leaves
the operator with a reason code and no idea what to do. `recovery` turns that into a
precise diagnosis:

- the control state, and whether an operator stop or a fail-closed corruption is in effect;
- every ledger store: present / absent / **corrupt**, with counts;
- for each fault, the safe action — including the explicit instruction *not* to "fix" the
  audit ledger.

The one genuinely stuck state the live runtime has is a corrupt control-state file (it
reads as KILLED, fail-closed). Its exit already exists and `recovery` names it: `resume`,
as the authenticated operator, writes a fresh ACTIVE state. Nothing new was needed for that
— and nothing here bypasses the kill switch (`agent_can_disable_or_bypass: false`).

## Zero new governance surface

No new contract, schema, registry, or gate. The verbs are P1 `INTERNAL_READ`-tier reads of
local state the runtime itself wrote. `COMMANDS` gains two entries, so the console CLI
(`choices=sorted(control.COMMANDS)`) and the Telegram parser (`parse_command`) both pick
them up for free — and the Telegram path stays behind R4's identity gate, so only the
registered Thomas can read the trail remotely.

## A read is not an event

Neither verb writes a ledger event, following the precedent `status` already set. Two
reasons: a read that appends to the log it is reading races its own chain tip, and
`audit_event.v0.1` requires a bound task + `core_context_binding_id` that a console read
does not have. `changed: False` on every reply, and the bytes on disk are untouched.
