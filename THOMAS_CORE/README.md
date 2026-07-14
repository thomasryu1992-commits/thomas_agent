# THOMAS_CORE

Status: Thomas Approved Candidate
Core Version: 0.2.1
Owner: Thomas
Primary Runtime Identity: Thomas Prime

## Purpose

`THOMAS_CORE` is the shared foundation inherited by Thomas Prime, departments, Agents, Programs, and policy systems.

It defines:

- Who Thomas is
- What Thomas values
- What Thomas is trying to build
- How Thomas compares options
- How Thomas prefers to communicate and operate

## Files

| File | Purpose |
| --- | --- |
| `THOMAS_CORE_PHILOSOPHY.md` | Canonical human-readable Thomas philosophy |
| `CORE_RUNTIME_POLICY_PROJECTION.yaml` | Compact machine-readable Core-derived Runtime invariants |
| `generated/docs/CORE_PROJECTION_MAP.yaml` | Build-time map for Core projection ownership and consistency validation |
| `CORE_RELEASE_MANIFEST_TEMPLATE.yaml` | Defines the immutable semantic file set for a Core release |
| `releases/<release_id>/manifest.yaml` | Immutable review-ready Release Manifest with exact file hashes |
| `approvals/<approval_id>.yaml` | Separate Thomas approval record for one exact Release |
| `CURRENT_CORE_RELEASE.yaml` | Pointer to the exact approved Release used by new Runtime Tasks |
| `CORE_METADATA.yaml` | Core version, governance, information status, and change policy |
| `THOMAS_IDENTITY.md` | Thomas identity, roles, strengths, limitations, and future identity |
| `THOMAS_VALUES.yaml` | Core values and value conflict policy |
| `THOMAS_GOALS.yaml` | Vision, long-term goals, mid-term goals, current goals, and goal rules |
| `THOMAS_DECISION_MODEL.yaml` | Decision process, scoring criteria, risk penalties, and default patterns |
| `THOMAS_PREFERENCE_PROFILE.yaml` | Communication, reporting, work style, automation, and notification preferences |
| `THOMAS_REVENUE_PREFERENCE_MODEL.yaml` | Revenue preference model for business, project, and investment opportunity evaluation |
| `MVP_CORE_SCOPE.md` | What is required for the first agent organization MVP versus what should remain reference-only |
| `MVP_ACTIVE_CORE.yaml` | The only active Core rules for the first MVP runtime |

Related architecture document:

- `historical/architecture/thomas-autonomous-organization-architecture-v0.1.md`

## Runtime Rule

Thomas Core separates learning from protected authority.

Agents are encouraged to learn from Task results, success, failure, and feedback.

Validated low-risk operational knowledge may be used within an explicit scope with evidence, versioning, monitoring, and rollback.

Learning does not expand permission.

Agents may suggest protected Core changes, but they cannot directly change Identity, Mission, Vision, Core Values, long-term goals, risk boundaries, Permission Policy, Constitution, or authority.

Protected Core changes require explicit Thomas approval and versioned Audit records.

## MVP Use

For the first agent organization MVP, do not load every detailed rule as an active runtime rule.

Use only the thirteen rules in `MVP_ACTIVE_CORE.yaml`.

Existing Rule IDs 001–008 retain compatible meaning.

New Rule IDs 009–013 add learning-positive, feedback-to-knowledge, learning-permission boundary, compounding, and repeated-work programization principles.

Keep detailed scoring, long-term portfolio examples, and full classification models as reference material until real decision cases accumulate.
