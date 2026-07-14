# Thomas Agent Architecture Slimming Master

**Baseline:** I0.5.5
**Status:** Safe Overlay Implementation
**Runtime impact:** None

## Problem

The architecture became thicker because one condition repeatedly expanded into:

```text
Condition
→ Contract
→ Schema
→ Registry
→ Validator
→ Fixture
→ Gate
```

The target model is:

```text
Condition
→ Governance Rule
→ Existing Policy Evaluator
→ Existing Test
```

## Core Rule

> One Concept = One Authority = One Source of Truth

## Authority Structure

```text
Thomas
↓
Thomas Core
↓
System Constitution
↓
Governance Policy
↓
Thomas Prime
↓
Runtime Kernel
↓
Agent / Program / Tool
↓
Validation
↓
Memory / Audit
```

## This Overlay Adds

- canonical Governance Policy candidate;
- System Constitution;
- Memory Policy;
- slim Registry candidates;
- Program and Tool Definitions;
- non-authoritative compatibility projection;
- decomposed read-only Kernel candidate;
- focused tests and gate.

## This Overlay Does Not Remove

- existing canonical Role Registry;
- existing Program Registry;
- existing Tool Registry;
- existing I0.5 Kernel;
- I0.5.1–I0.5.5 review-only artifacts;
- existing release gate.

## Migration Phases

### Phase A — Parallel Introduction

Apply this package. Keep old and new structures in parallel.

### Phase B — Parity

Compare old Kernel output and slim Kernel output. Update loaders to prefer canonical Definitions.

### Phase C — Active Gate Split

Create Active, Deferred, and Legacy Compatibility gates.

### Phase D — Physical Separation

Move generated, deferred, and historical families.

### Phase E — Retirement

Retire duplicated Registry fields and temporary compatibility projection after one stable release.

## Non-Activation

No model, Tool, Program, Executor, external action, financial action, Scheduler, or Runtime-authoritative path is enabled.
