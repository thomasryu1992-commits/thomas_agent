# Deferred Sandbox Boundary

**Canonical deferred authority:** `deferred/DEFERRED_ARCHITECTURE.yaml`
**Family:** `sandbox`
**Status:** Deferred and disabled
**No activation authority:** This document cannot create or run a Sandbox.

## Scope

This family consolidates the local reversible Sandbox candidate plan and review evidence used before any broader Runtime or Executor capability.

## Required before any future activation

The shared Deferred prerequisites apply, plus an isolated root, path and symlink confinement, secret prohibition, network prohibition unless separately approved, bounded subprocess policy, rollback, cleanup, and independent review.

## Current boundary

No Sandbox is created. No test starts. No subprocess, network call, secret access, persistent write, rollback, or cleanup is performed by the current review artifacts. Production use is prohibited.

## Preserved threats and failure modes

Path or symlink escape, secret access, network escape, persistent side effects, missing prerequisites, unproven rollback, and incomplete cleanup remain fail-closed concerns.
