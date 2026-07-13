# I0.5.1 Rev2 Verified Evidence Boundary v0.1

## Allowed

- read local Git metadata;
- read the GitHub Actions run and jobs through `gh api`;
- create a non-secret CI evidence YAML;
- verify exact Repository, HEAD, workflow hash, and cross-platform job results;
- invoke the existing Core Release Verifier in-process;
- verify Current Core and referenced evidence are tracked exactly at HEAD;
- validate Gate evidence required-check completeness;
- build review-only readiness evidence;
- fail closed.

## Prohibited

- accepting CI `PASS` from a CLI string;
- accepting Current Core verification from a Boolean flag;
- creating or changing `CURRENT_CORE_RELEASE.yaml`;
- approving or activating Core;
- activating Runtime;
- enabling Tool, Program, or Executor paths;
- consuming Approval;
- reading or storing credential values;
- external or financial execution;
- Permission or Authority expansion.
