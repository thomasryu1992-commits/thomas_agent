# I0.5.1 Runtime Promotion Review-Only Boundary v0.1 — Rev3 Readiness Split

I0.5.1 Rev3 keeps every Rev2 verified-evidence hardening control and separates Design Readiness from Activation Readiness.

## Design Readiness

May evaluate Component Attestation, Contract Lock, Release Gate, verified Ubuntu/Windows CI, Review Core presence, disabled Tool/Program state, and non-authoritative Runtime Registry state.

`READY_FOR_THOMAS_DESIGN_DECISION` means only that Thomas may review a future Runtime-authoritative read-only design. Current Core is not required at this stage.

## Activation Readiness

May additionally evaluate the existing Current Core verification chain and committed-at-HEAD provenance.

`READY_FOR_RUNTIME_ACTIVATION_REVIEW` means only that a separate activation review may begin. It does not activate Runtime.

## Prohibited Effects

- create or modify `CURRENT_CORE_RELEASE.yaml`;
- create Core Approval or Activation;
- activate Runtime;
- enable Tool, Program, Executor, Supervisor, Scheduler, or Control Channel;
- consume Approval;
- perform filesystem mutation outside explicit review-artifact output;
- perform external or financial action;
- expand Permission or Authority.

Both readiness tracks remain review-only evidence and every Runtime-effect field remains false.
