# I0.4 Consolidation Checkpoint v0.1

**Status:** `REVIEW_ONLY_CHECKPOINT`
**Owner:** `Thomas`
**Scope:** `I0.4.1 through I0.4.7 cumulative integration`

## 1. Decision

The I0.4 contract foundation is structurally sufficient to stop adding new contract families and move toward an actual Read-only Runtime Kernel.

The next engineering objective is not another approval packet. It is to integrate the cumulative source into the real Repository, pass all focused validators and the full Repository Gate, generate new Gate evidence and a new self-contained Review Release, and then begin I0.5.

## 2. Consolidated Layers

1. Thomas-approved Permission and Approval operating policy.
2. Action fingerprint, Permission Decision, and Approval foundation.
3. Tool and Program Request foundation.
4. Execution Request, Execution Result, Validation Result, and Audit Event foundation.
5. Disabled Executor, Hot-Path revalidation, Approval consumption preview, and rollback/recovery foundation.
6. Offline Monitoring, Alert, Health, Clock, Kill Switch evidence, and Executor Candidate Intake.
7. Metadata-only Control Channel binding, non-dispatched command envelope, disabled Supervisor/Scheduler, Review-draft thresholds, and not-run Sandbox candidate test plan.

## 3. Deduplication Decisions

- `THOMAS_PERMISSION_APPROVAL_OPERATING_POLICY_V0.1` is a narrow action-policy layer under the existing MVP Operating Policy. It does not replace or outrank the MVP Operating Policy.
- `Permission Decision`, `Approval`, and `Approval Consumption Preview` remain separate records. Approval cannot expand Authority, and the preview cannot consume Approval.
- `Tool Request`, `Program Request`, and `Execution Request` remain separate because resource eligibility, invocation intent, and execution handoff are different decisions.
- `Execution Result` and `Disabled Executor Evidence` remain separate because one records an execution outcome contract while the other proves the disabled service performed no execution.
- `Monitoring Snapshot`, `Health Snapshot`, and `Alert Event` remain separate because observation, health assessment, and notification candidate evidence have different semantics.
- `Kill Switch State` and `Kill Switch Command Review` remain separate because state evidence and a command envelope are not the same artifact.
- Phase boundary documents remain as historical safety evidence, but they are not Runtime authority or machine-facing record schemas.
- The Review-only Executor Registry is not merged into Tool or Program Registries. Executor readiness and activation have a separate trust boundary.

## 4. Freeze Result

The I0.4 functional contract set is frozen for I0.5 design.

Allowed I0.4 changes:

- defect correction;
- security hardening;
- compatibility repair;
- missing validator coverage;
- Thomas-approved governance correction.

Not allowed without reopening the checkpoint:

- a new execution authority model;
- a new approval bypass;
- a new Tool, Program, or Executor activation path;
- a new external or financial execution path;
- an unreviewed Runtime side effect.

## 5. Real Repository Entry Gate

Before I0.5 implementation:

1. Apply the cumulative consolidation bundle to `agent/harden-integration-flow` with a clean Working Tree.
2. Run all six focused validators.
3. Run the consolidated contract-set validator.
4. Run Contract/Schema parity and Static Integrity.
5. Run `scripts/run_repository_release_gate.py --full`.
6. Generate a new Repository Source Fingerprint and Gate evidence.
7. Build a new self-contained Review Release.
8. Validate strict reproducibility.
9. Review the new Release ID, bundle SHA-256, and Manifest SHA-256.

The prior Review Release remains immutable historical evidence but cannot represent the modified source.

## 6. I0.5 Scope

I0.5 may implement a Read-only Runtime Kernel that reads approved Core context, validates Task and Binding lineage, routes to active read-only-capable Roles, calculates Authority and Permission, creates Review-only requests, and writes Validation/Audit records.

I0.5 must not enable external writes, Tool writes, Program writes, Executor handoff, Approval consumption, Control Channel dispatch, process control, scheduler dispatch, Sandbox writes, external execution, or financial execution.
