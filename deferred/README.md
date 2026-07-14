# Thomas Agent Deferred Architecture

**Status:** Deferred / review-only
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

The phase-specific contracts, schemas, fixtures, examples, and validators remain in their existing paths as subordinate detailed evidence until PR #11 performs generated/historical cleanup. They are not independent architecture authorities.

## Validation

```bash
python scripts/validate_deferred_architecture.py --structure-only
python scripts/validate_deferred_architecture.py
python scripts/run_architecture_gate.py --scope deferred --check-only
```

Passing validation is evidence only and grants no activation or execution authority.
