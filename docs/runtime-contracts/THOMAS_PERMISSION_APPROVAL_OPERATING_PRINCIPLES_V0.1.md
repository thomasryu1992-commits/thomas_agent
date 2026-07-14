# Thomas Permission & Approval Operating Principles v0.1 — Reference

- **Document Version:** `0.2.0`
- **Status:** `HUMAN_READABLE_REFERENCE`
- **Owner:** `Thomas`
- **Authoritative:** `false`
- **Canonical Policy:** [`governance/GOVERNANCE_POLICY.yaml`](../../governance/GOVERNANCE_POLICY.yaml)

> This document explains the operating model. It does not define Permission, Approval, Authority, effect, TTL, Control Channel, Kill Switch, or Runtime rules.

## 1. Canonical source

All machine-readable Governance rules now have one owner:

```text
Policy ID      → thomas.governance.policy
Policy Version → 1.1.0
Policy Path    → governance/GOVERNANCE_POLICY.yaml
```

The previous `THOMAS_PERMISSION_APPROVAL_OPERATING_POLICY_V0.1.yaml` is a compatibility reference only. It is not a Runtime policy source.

## 2. Operating model

The system continues to use `BOUNDED_MAXIMUM_AUTONOMY`.

```text
Safe internal read, analysis, drafting, simulation, and candidate creation
→ ALLOW

Scoped, reversible, versioned internal changes with rollback and reporting
→ EXECUTE_AND_REPORT

External, financial, production, governance, destructive, or security-sensitive changes
→ APPROVAL_REQUIRED

Insufficient Authority, invalid lineage, unsafe uncertainty, prohibited behavior, or conflicting policy data
→ BLOCK
```

The exact scope-to-disposition map is defined only in the canonical Governance Policy.

## 3. Authority and Permission remain separate

```text
Authority
= structural ceiling available to an actor, Role, Assignment, Tool, or Program

Permission
= whether one exact action may proceed now
```

Approval cannot expand Authority. A sufficient Authority chain is necessary but does not itself grant Permission. A Permission Decision does not activate an Executor.

## 4. Protected boundaries

The canonical policy preserves these invariants:

- Thomas remains the final human authority.
- Roles, Programs, Tools, Validators, Memory, and Runtime cannot expand their own Authority.
- Candidate status, review evidence, clean tests, or passing Gates do not grant execution Permission.
- External, financial, public, destructive, protected, or security-sensitive effects require the policy result defined by the canonical source.
- Approval is action-bound, one-time-use, time-limited, and cannot be reused for a changed Task, target, content, amount, scope, Tool, Program, Binding, or expiration.
- Validation may block progression but does not grant Permission, Approval, Authority, activation, or execution.
- Missing, stale, ambiguous, or inconsistent Governance data fails closed.
- Secrets are forbidden from action fingerprints, public records, logs, examples, and generated evidence.

## 5. Control Channel

The approved future Control Channel remains an authenticated Thomas Telegram private 1:1 channel. Identity, private-chat binding, exact Approval ID or fingerprint code, explicit expression, and expiration must all be verified.

Group messages, channel messages, other users, forwarded messages, emoji-only reactions, ambiguous expressions, stale unmatched messages, and decisions for another action are invalid.

This reference does not implement Telegram authentication or Approval consumption.

## 6. Financial and GitHub boundaries

Autonomous financial spend without a registered Budget remains `0`.

Agent-branch creation, assigned Repository edits, tests, validation, builds, Agent-branch commits, approved Repository pushes, and Draft Pull Requests may be classified as `EXECUTE_AND_REPORT` only under the exact canonical policy conditions.

Protected Branch merge, Release, Production deployment, new financial commitment, Repository visibility changes, branch-protection changes, and destructive operations remain governed by the canonical policy. Protected Branch force push and unapproved history rewrite remain blocked.

## 7. Learning and promotion

Ten independent valid repetitions trigger Programization Review only. Good outcomes may create evidence, reports, candidates, recommendations, or review packets. They do not automatically activate a Role, Program, Tool, Runtime, Executor, or new Authority.

## 8. Runtime effect

Governance policy authority is active. Runtime execution authority is not.

```yaml
runtime_effect:
  mode: REVIEW_ONLY
  grants_runtime_execution: false
  grants_tool_or_program_enablement: false
  grants_external_execution: false
  grants_financial_execution: false
  grants_permission_expansion: false
  executor_handoff_allowed: false
  approval_consumption_allowed: false
  core_activation_allowed: false
```

## 9. Final rule

This document is explanatory. When wording here differs from the canonical Governance Policy, `governance/GOVERNANCE_POLICY.yaml` controls and the stricter rule wins.
