# Programization Review Policy v0.1

**Status:** Review Ready
**Owner:** Thomas
**Purpose:** Define what counts as a valid repeated-work observation before a Programization Review is triggered.

## 1. Trigger

```text
10 independent valid repetitions
→ Programization Review

not

10 executions
→ Automatic Program conversion
```

Ten is a review trigger, not a sufficient conversion condition.

## 2. Pattern Signature

Observations belong to the same repeated-work pattern only when the relevant signature remains compatible.

```yaml
pattern_signature:
  task_type:
  role_id:
  input_schema_sha256:
  ordered_step_signature_sha256:
  output_schema_sha256:
  environment_version:
```

The signature may allow explicitly documented compatible versions.

Materially different input, process, output, Role, or environment creates a different pattern.

## 3. Valid Repetition

A repetition counts only when all required conditions are true.

```yaml
valid_repetition:
  independent_completed_task: true
  quality_or_validation_pass: true
  duplicate: false
  synthetic: false
  retry_of_same_task_revision: false
  validation_revision_cycle: false
  materially_same_pattern_signature: true
```

## 4. Excluded Observations

Do not count:

- Retry of the same Task revision.
- Validation revision cycle.
- Duplicate replay.
- Synthetic test.
- Fixture.
- Manual smoke test.
- Incomplete or failed Task without a valid output.
- The same input replayed without an independent business event.
- An execution with materially different input or output contract.
- An execution with a materially different ordered step signature.

## 5. Candidate Requirements

After the review trigger, a Program Candidate still requires:

- Stable input contract.
- Deterministic or sufficiently stable processing rules.
- Measurable output.
- Defined exceptions and failure behavior.
- Existing Agent baseline.
- Shadow or limited comparison.
- Measurable improvement.
- Rollback path.

## 6. Conversion Boundary

```text
Repeated and deterministic slice
→ Program Candidate

Novel judgment
→ Agent

Interpretation
→ Agent

Strategy
→ Agent

Material exception
→ Agent or escalation
```

## 7. Activation Boundary

Validated low-risk internal Programs may activate within an explicit scope.

External, financial, permission, security, secret, deletion, publication, trading, or irreversible effects continue to require the existing Permission and Approval controls.

## 8. Final Rule

> Repetition creates a review opportunity.

> Evidence and validation create a Program Candidate.

> Programization never expands Permission.
