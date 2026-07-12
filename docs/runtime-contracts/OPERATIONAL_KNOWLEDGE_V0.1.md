# Validated Operational Knowledge v0.1

Validated Operational Knowledge is reusable only while its evidence, environment, confidence, and review window remain valid.

Required lifecycle fields:

```yaml
validated_at_utc:

review_due_at_utc:

last_confirmed_at_utc:

environment_signature:

confidence:

status:
  - active
  - review_due
  - stale
  - deprecated
```

Environment, model, Tool, API, data, or policy changes may make previous knowledge stale.

Stale or review-due knowledge must not be treated as current validated operating truth without revalidation.

Operational Knowledge may improve Runtime behavior within scope.

It does not expand Permission or rewrite an approved Core Release.
