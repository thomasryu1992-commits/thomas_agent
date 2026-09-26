# Thomas Agent Deferred Architecture

**Status:** Deferred / review-only — **frozen 2026-09-26** (system review D7, Thomas): no new family and
no new capability design lands here; the five families below are the whole set, and the validator already
refuses a sixth (`family_order` must be exactly these five).
**Runtime authority:** None
**Canonical deferred design index:** [`DEFERRED_ARCHITECTURE.yaml`](DEFERRED_ARCHITECTURE.yaml)

This directory owns the concise description of future capability requirements. It does not activate Runtime Entry, an Executor, operations daemons, Control Channel dispatch, a Sandbox, Approval consumption, external action, or financial action.

## Families

| Family | Boundary | Current state |
|---|---|---|
| Runtime Entry | [`runtime_entry/BOUNDARY.md`](runtime_entry/BOUNDARY.md) | Deferred and disabled |
| Executor | [`executor/BOUNDARY.md`](executor/BOUNDARY.md) | Deferred and disabled |
| Operations | [`operations/BOUNDARY.md`](operations/BOUNDARY.md) | Deferred and disabled |
| Control Channel | [`control_channel/BOUNDARY.md`](control_channel/BOUNDARY.md) | Deferred and disabled |
| Sandbox | [`sandbox/BOUNDARY.md`](sandbox/BOUNDARY.md) | Deferred and disabled |

The phase-specific contracts, schemas, fixtures, examples, and validators remain in their existing paths as subordinate detailed evidence. They are not independent architecture authorities.

## Why this stays here, gated, rather than moving to `historical/`

Frozen is not retired. Moving this directory to `historical/` and retiring the Deferred gate was considered
and declined (2026-09-26), because the gate is the only thing that runs several checks the active tree
still depends on:

- `scripts/validate_i0_5_1_runtime_promotion_readiness.py` guards **active** infrastructure: the runtime
  validation workflow's least-privilege tokens (`contents: read`, `persist-credentials: false`, no
  `secrets.`, no `*: write`), the `.gitattributes` LF rules for `runtime/**` and `.github/workflows/**`,
  and the CI evidence collector's credential ban. Nothing else checks these.
- Required-field parity for the 24 contracts marked `required_field_parity: true` (the active parity
  check delegates them here), instance validation of their examples, and the fail-closed runs of their
  negative fixtures.

The gate costs one non-required job of about 12 seconds. §G2 of `docs/REMAINING_WORK.md` is the precedent
for removing an *implementation* under a deferral; it kept the deferred design and this gate.

## Validation

```bash
python scripts/validate_deferred_architecture.py --structure-only
python scripts/validate_deferred_architecture.py
python scripts/run_architecture_gate.py --scope deferred --check-only
```

Passing validation is evidence only and grants no activation or execution authority.
