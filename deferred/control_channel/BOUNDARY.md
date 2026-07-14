# Deferred Control Channel Boundary

**Canonical deferred authority:** `deferred/DEFERRED_ARCHITECTURE.yaml`
**Family:** `control_channel`
**Status:** Deferred and disabled
**No activation authority:** This document cannot verify identity, dispatch commands, mutate Kill Switch state, terminate a process, or resume Runtime.

## Scope

This family consolidates future Thomas identity binding, command envelopes, Kill Switch state, and Kill Switch command review.

## Required before any future activation

The shared Deferred prerequisites apply, plus registered Thomas identity and private-chat binding, exact command/action binding, freshness and anti-replay checks, explicit decision semantics, audit, and separate approval for Runtime control integration.

## Current boundary

Identity records are review-only and unbound to a live transport. Commands are not dispatched. Kill Switch state is not mutated. Process termination and automatic resume do not occur.

## Preserved threats and failure modes

Forged identity, group/forwarded/ambiguous approval, stale command reuse, mismatched fingerprints, self-resume, and Kill Switch bypass remain fail-closed concerns.
