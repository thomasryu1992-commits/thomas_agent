# Runtime Promotion Readiness Contract v0.1 — Rev3 Readiness Split

**Schema Version:** `runtime_promotion_readiness.v0.1`
**Document Version:** `0.1.2`
**Status:** `I0.5.1 Rev3 Review-Only Readiness Contract`
**Owner:** `Thomas`

## Purpose

Separate readiness for a Thomas design decision from readiness for a future Runtime activation review. This record never activates Runtime, creates Current Core, consumes Approval, or grants execution capability.

## A. Design Readiness

Design Readiness answers only:

> Is the I0.5 read-only Runtime Candidate sufficiently integrated and independently evidenced for Thomas to review the Runtime-authoritative read-only design?

Required checks:

1. Component Registry and implementation identity/version attestation.
2. I0.4 Contract Set Lock presence.
3. Repository Release Gate evidence presence and PASS result.
4. Gate evidence fingerprint match to current Gate-owned source, including `runtime/**` and `.github/workflows/**`.
5. Required Gate Check Set present exactly once and PASS:
   - I0.4 Consolidated Contract Set;
   - I0.5 Read-only Runtime Kernel;
   - I0.5.1 Runtime Promotion Readiness;
   - Contract Schema Parity;
   - Security Hardening;
   - Core Release Reproducibility.
6. Cross-platform GitHub Actions workflow presence.
7. Structured `github_ci_evidence.v0.1` record collected from the live GitHub API.
8. CI evidence bound to local Git origin, local HEAD, workflow SHA-256, successful Ubuntu job, and successful Windows job.
9. Review Core Release presence.
10. No enabled Tools.
11. No enabled Programs.
12. I0.5 Registry remains non-authoritative and disabled for Runtime-authoritative mode.

Design outcomes:

```text
BLOCKED_NOT_READY
READY_FOR_THOMAS_DESIGN_DECISION
```

Current Core is intentionally **not** a Design Readiness prerequisite.

## B. Activation Readiness

Activation Readiness answers only:

> After the design is reviewable, is the verified Current Core lineage present for a separate Runtime activation review?

Activation Readiness requires every Design Readiness check plus:

1. Current Core pointer presence.
2. Existing Core Release Verifier PASS for Current Pointer → Activation → Approval → Manifest → immutable Release snapshots.
3. Current Pointer and every referenced governance/release evidence file committed exactly at local HEAD.
4. No invalid, revoked, deactivated, hash-mismatched, or uncommitted Current Core lineage.

Activation-review outcomes:

```text
BLOCKED_NOT_READY
READY_FOR_RUNTIME_ACTIVATION_REVIEW
```

`READY_FOR_RUNTIME_ACTIVATION_REVIEW` is still review-only. It does not activate Runtime and does not mean `ready_for_runtime_activation=true`.

## Compatibility Fields

For I0.5.1 compatibility:

- top-level `summary.result` mirrors Design Readiness result;
- top-level `summary.blocking_reasons` mirrors Design Readiness blockers;
- `summary.design_readiness` is the canonical Design Readiness object;
- `summary.activation_readiness` is the canonical Activation Readiness object.

## Removed Manual Bypasses

The following inputs remain prohibited:

```text
--github-ci-status PASS
--current-core-verified
```

GitHub CI requires a structured evidence record. Current Core verification is computed by the existing Core Release Verifier and Git provenance checks; it is never accepted from a Boolean flag.

## Fail-Closed Rules

- Missing, malformed, stale, wrong-Repository, wrong-HEAD, wrong-workflow, failed Ubuntu, or failed Windows CI evidence blocks Design Readiness.
- Missing or stale Gate evidence blocks Design Readiness.
- Missing or non-PASS required Gate checks block Design Readiness.
- Missing Current Core does **not** block Design Readiness.
- Missing, invalid, revoked, deactivated, hash-mismatched, or uncommitted Current Core blocks Activation Readiness.
- Activation Readiness can never be ready while Design Readiness is blocked.
- Runtime-effect fields remain false in all outcomes.
- Blockers are derived from requirement fields; a record cannot delete blockers and self-declare readiness.
- Component boundary flags must remain candidate-only and no-effect in addition to matching implementation ID/version constants.
