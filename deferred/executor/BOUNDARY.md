# Deferred Executor Boundary

**Canonical deferred authority:** `deferred/DEFERRED_ARCHITECTURE.yaml`
**Family:** `executor`
**Status:** Deferred and disabled
**No activation authority:** This document cannot register, enable, or call an Executor.

## Scope

This family consolidates Execution Request/Result previews, Executor Registry/Readiness, the disabled Restricted Execution Service, hot-path revalidation, Approval consumption preview, rollback/recovery, and candidate intake.

## Required before any future activation

The shared Deferred prerequisites apply, plus exact Permission/Approval/Action lineage, immediate pre-execution risk and validity checks, idempotency, bounded effects, rollback, reconciliation, monitoring, and separate Thomas approval for the exact Executor capability.

## Current boundary

No Executor is registered or enabled. No execution token is issued. Approval consumption is preview-only. No Tool, Program, external, financial, or Runtime effect occurs. Existing detailed artifacts remain subordinate evidence.

## Preserved threats and failure modes

Stale preflight, Approval reuse, authority expansion, unregistered or disabled Executor use, partial external effects, missing idempotency, and unproven rollback remain fail-closed concerns.
