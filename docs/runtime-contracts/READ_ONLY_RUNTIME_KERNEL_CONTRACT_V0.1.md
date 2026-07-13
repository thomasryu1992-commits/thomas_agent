# Read-only Runtime Kernel Contract v0.1

**Kernel ID:** `thomas.read_only_runtime_kernel`
**Kernel Version:** `0.1.0`
**Status:** `Candidate — Non-authoritative Development Replay`
**Owner:** `Thomas`

## 1. Purpose

The I0.5 Read-only Runtime Kernel is the first executable orchestration layer over the frozen I0.4 contract foundation.

It reads an exact Input Bundle, validates lineage and safety boundaries, invokes one deterministic in-process read-only Worker, validates the resulting Agent Output, creates Audit evidence, and returns a final Task snapshot entirely in memory.

It is not Runtime-authoritative because the real Repository Consolidation Gate, new Release lifecycle, and Current Core Runtime activation are separate prerequisites.

## 2. Execution Flow

```text
Read exact Bundle
↓
Verify Bundle hashes and read boundary
↓
Validate Task / Binding / Assignment / Role lineage
↓
Validate Role Registry status
↓
Validate Authority chain
↓
Validate Permission Decision
↓
Confirm zero Tool / Program / model budget
↓
Confirm Tool / Program Registries have no enabled resources
↓
Invoke deterministic in-process Worker
↓
Create Agent Output v0.2
↓
Create automatic Contract Validation Result v0.1
↓
Create append-only Audit Event v0.1 chain
↓
Return the unchanged source Task snapshot and a separate REPLAY_COMPLETED lifecycle
```

## 3. Supported Run Mode

Only:

```text
DEVELOPMENT_REPLAY
```

is supported in v0.1.

The following are blocked:

```text
RUNTIME_READ_ONLY
PRODUCTION
LIVE
EXTERNAL
WRITE
```

## 4. Required Safety Checks

The Kernel must fail closed when any of the following is invalid:

- Bundle schema, fingerprint, reference, or file hash;
- requester authentication flag in the explicit Task snapshot;
- Task lifecycle state;
- Task `no_external_action` constraint;
- Tool or Program Request presence;
- route cardinality;
- Core Binding Task, revision, Trace, Binding ID, or Rule subset;
- Approval or Activation references in the supplied Binding snapshot;
- Assignment lineage, Role identity, Actor identity, or resource scope;
- zero Tool, Program, and model-call Assignment budgets;
- Role status, routability, external-action boundary, or capabilities;
- Authority ordering;
- Permission Decision and Assignment Permission parity;
- Role Registry entry;
- Tool or Program Registry enabled status;
- frozen I0.4 Contract Set state;
- secret-bearing keys.

## 5. Lifecycle

The positive development replay path is fixed:

```text
QUEUED
→ RUNNING
→ VALIDATING
→ COMPLETED
→ MEMORY_REVIEW
→ REPLAY_COMPLETED
```

These are Replay lifecycle states, not Task lifecycle transitions. The supplied Task remains `QUEUED`; Contract Validation does not claim independent domain validation, Task completion, Memory Review, or Task closure.

Each referenced record is read once into an immutable byte snapshot. The Kernel computes the file hash and parses the record from that same byte sequence. The Bundle and every record with an available repository schema are validated at Runtime; records without a repository schema remain subject to explicit fail-closed semantic checks.

All transitions occur on an in-memory Task copy and produce Audit evidence. No source file is changed.

## 6. Prohibited Effects

The Kernel must never:

- call an LLM or model provider;
- execute a Tool or Program;
- call a network endpoint;
- write, rename, delete, or mutate a file;
- dispatch Telegram or any Control Channel command;
- dispatch a Scheduler job;
- call or enable an Executor;
- consume an Approval;
- create Runtime permission;
- expand Authority;
- activate a Core Release;
- mutate Runtime state;
- perform external or financial actions.

## 7. Final Rule

> The I0.5 v0.1 Kernel proves contract-driven orchestration in a non-authoritative, deterministic, read-only development replay. It does not make Thomas Agent live.
